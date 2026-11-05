"""ACI-S30 acceptance: the exact revision vector, versions, boundaries, refresh, replay, bounds."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest
from test_s30_support import (
    MAIN,
    PACKAGE_MEMBERS,
    World,
    by_line,
    cli_impact,
    cli_link,
    cli_run,
    file_entity,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    line_of,
    lock_for,
    make_package,
    make_world,
    outcome,
    world_inventory,
)

from atlas_aci.codegraph import CodeGraph
from atlas_aci.cross_repo import package_links as pl
from atlas_aci.cross_repo import revision_vector as rv
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import Side

pytestmark = pytest.mark.usefixtures("grammar")

L_AREA = "from s30_shapes.geometry import area, Circle"
L_PER = "from s30_shapes.geometry import perimeter as per"
L_FETCH = "from s30_shapes.geometry import fetch_area"
L_REEXPORT = "from s30_shapes import area as reexported_area"
L_MISSING = "from s30_shapes.geometry import missing_name"
L_HIDDEN = "from s30_shapes.compat import hidden"
L_NOTHERE = "from s30_shapes.nothere import x"
L_DUP = "from s30_shapes.dup import twice"
L_SUBMODULE = "from s30_shapes import geometry"
L_STAR = "from s30_shapes.geometry import *"
L_MODULE = "import s30_shapes.geometry"
L_WRAPPED = "from s30_shapes.rebind import wrapped_area"
L_COND = "from s30_shapes.rebind import conditional"
L_IMPORTED = "from s30_shapes.rebind import imported"
L_ACCENT = "from s30_shapes.géometry import area as accented"
L_PAREN = "    Circle as c2,  # note"
L_CONT = "from s30_shapes.geometry import \\"
L_CONT2 = "    perimeter as per2"
L_NESTED = "    from s30_shapes.geometry import area as inner_area"
L_TYPING = "    from s30_shapes.geometry import Circle as tc"
LINKED = "linked_declaration"
FORM = "unresolved_unsupported_reference_form"
AMBIGUOUS = "unresolved_ambiguous_provider_symbol"
NOT_DECLARED = "unresolved_symbol_not_declared"

# The full D7 table for `app/main.py`: (source line, name) -> (resolution, detail).
EXPECTED = {
    (L_AREA, "area"): (LINKED, None),
    (L_AREA, "Circle"): (LINKED, None),
    (L_PER, "perimeter"): (LINKED, None),
    (L_FETCH, "fetch_area"): (LINKED, None),
    (L_REEXPORT, "area"): ("unresolved_reexport_not_followed", None),
    (L_MISSING, "missing_name"): (NOT_DECLARED, "name_not_declared"),
    (L_HIDDEN, "hidden"): (NOT_DECLARED, "module_incomplete"),
    (L_NOTHERE, "x"): (NOT_DECLARED, "module_not_in_package"),
    (L_DUP, "twice"): (AMBIGUOUS, "package_declarations"),
    (L_SUBMODULE, "geometry"): (FORM, "submodule_import"),
    (L_STAR, "*"): (FORM, "wildcard_import"),
    (L_MODULE, "s30_shapes.geometry"): (FORM, "module_import"),
    (L_WRAPPED, "wrapped_area"): (AMBIGUOUS, "provider_name_rebound"),
    (L_COND, "conditional"): (AMBIGUOUS, "provider_symbols"),
    (L_IMPORTED, "imported"): (AMBIGUOUS, "provider_name_rebound"),
    (L_ACCENT, "area"): (FORM, "non_ascii_identifier"),
    (L_PAREN, "Circle"): (LINKED, None),
    (L_PAREN, "# note"): (FORM, "non_identifier_name"),
    (L_CONT, "\\\n"): (FORM, "non_identifier_name"),
    (L_CONT2, "perimeter"): (LINKED, None),
    (L_NESTED, "area"): (FORM, "nested_scope_import"),
    (L_TYPING, "Circle"): (LINKED, None),
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s30"))


@pytest.fixture(scope="module")
def overlay(world):
    return world.link()


def expected_vector(world: World) -> list[dict[str, str]]:
    return [
        {"role": "consumer", "repository_id": "app", "snapshot_id": world.snapshots["app"]},
        {"role": "provider", "repository_id": "lib", "snapshot_id": world.snapshots["lib"]},
    ]


def leased(side: Side):
    stack = ExitStack()
    reader = runtime.open_active(stack, side)
    return stack, reader


# ---- AC-S30-001 ------------------------------------------------------------------------------


def test_cross_repo_link_has_exact_vector(world, overlay, tmp_path):
    vector = expected_vector(world)
    for side, role in ((world.consumer, "app"), (world.provider, "lib")):
        stack, reader = leased(side)
        with stack:
            assert reader.snapshot_id == world.snapshots[role]
    assert overlay["revision_vector"] == vector
    assert overlay["vector_digest"] == rv.vector_digest(vector)
    links = overlay["links"]
    assert len(links) == 7
    stack, provider_reader = leased(world.provider)
    with stack:
        entity_of = {
            name: provider_reader.db.execute(
                "SELECT entity_id FROM symbols WHERE path=? AND name=?",
                ("src/s30_shapes/geometry.py", name),
            ).fetchone()[0]
            for name in ("area", "Circle", "perimeter", "fetch_area")
        }
        other = provider_reader.db.execute(
            "SELECT entity_id FROM symbols WHERE path='src/other_mod.py' AND name='area'"
        ).fetchone()[0]
    stack, consumer_reader = leased(world.consumer)
    with stack:
        rows = {
            (row[0], row[1]): row
            for row in consumer_reader.db.execute(
                "SELECT occurrence_id, assertion_id, path FROM import_bindings"
            )
        }
    assert links and entity_of["area"] != other
    for link in links:
        assert link["revision_vector"] == vector  # list order and exact keys
        assert all(list(entry) == ["role", "repository_id", "snapshot_id"] for entry in vector)
        assert link["vector_digest"] == overlay["vector_digest"]
        assert link["relation"] == "external_reference"
        assert link["call_semantics"] == "not_claimed"
        assert link["id"] == pl.link_id(
            link["vector_digest"],
            link["reference_id"],
            link["provider"]["entity_id"],
            link["provider"]["declaration_id"],
        )
        assert link["provider"]["entity_id"] == entity_of[link["provider"]["name"]]
        assert link["provider"]["entity_id"] != other
        assert link["provider"]["path"].startswith("src/s30_shapes/")
        assert link["consumer"]["path"] == "app/main.py"
        assert set(link["consumer"]) == set(pl.LINK_CONSUMER_KEYS)
    by_ref = {item["id"]: item for item in overlay["references"]}
    for link in links:
        reference = by_ref[link["reference_id"]]
        key = (reference["consumer"]["occurrence_id"], reference["consumer"]["assertion_id"])
        assert key in rows and rows[key][2] == "app/main.py"
        assert link["consumer"]["assertion_id"] == reference["consumer"]["assertion_id"]
    aliased = by_line(overlay)[(line_of(L_PER), "perimeter")]
    unaliased = by_line(overlay)[(line_of(L_CONT2), "perimeter")]
    assert aliased["aliased"] is True and unaliased["aliased"] is True
    first = next(i for i in links if i["reference_id"] == aliased["id"])
    second = next(i for i in links if i["reference_id"] == unaliased["id"])
    assert first["provider"]["entity_id"] == second["provider"]["entity_id"]
    assert first["provider"]["decorated"] is True
    assert overlay["package"]["binding"]["state"] == "bound"
    binding = overlay["package"]["binding"]
    assert binding["members_equal"] == binding["members_compared"] == 6
    assert overlay["coverage"]["call_binding"] == "not_claimed"
    # The CLI writes the library's bytes and prints a summary naming the same vector.
    paths = world.write_inputs()
    out = tmp_path / "overlay.json"
    result = cli_link(world, paths, out)
    assert result.exit_code == 0, result.stderr
    assert out.read_bytes() == pl.canonical_bytes(overlay)
    summary = json.loads(result.stdout)
    assert summary["revision_vector"] == overlay["revision_vector"]
    assert summary == pl.summary_document(overlay)
    assert summary["counts"]["links"] == 7 and summary["links_invalidated"] is None


def test_every_reference_resolves_as_the_table_says(overlay):
    got = {}
    for (line, name), item in by_line(overlay).items():
        got[(MAIN.splitlines()[line - 1], name)] = (item["resolution"], item["detail"])
    assert got == EXPECTED
    counts = overlay["counts"]["by_resolution"]
    assert sum(counts.values()) == len(EXPECTED) == overlay["counts"]["references"]
    assert counts[LINKED] == 7 and set(counts) == set(pl.RESOLUTIONS)
    assert (
        overlay["counts"]["references_out_of_scope"] == 2
    )  # `import json`, `from .local import y`
    names = {item["name"] for item in overlay["references"]}
    assert "json" not in names and "y" not in names
    decorated = {i["provider"]["name"]: i["provider"]["decorated"] for i in overlay["links"]}
    assert decorated == {
        "area": False,
        "Circle": False,
        "perimeter": True,
        "fetch_area": False,
    }
    kinds = {i["provider"]["name"]: i["provider"]["kind"] for i in overlay["links"]}
    assert kinds["fetch_area"] == "function" and kinds["Circle"] == "class"
    assert overlay["package"]["top_level_modules"] == ["s30_shapes"]
    pl.validate_overlay(overlay)


# ---- AC-S30-002 ------------------------------------------------------------------------------


def _wrong_world(tmp_path, kind: str):
    package = make_package()
    wheel_lock = package.lock
    if kind == "V1":
        text = PACKAGE_MEMBERS["s30_shapes/geometry.py"] + "# one byte more\n"
        world = make_world(
            tmp_path, provider_edit=lambda f: f.update({"src/s30_shapes/geometry.py": text})
        )
        return world, {}, wheel_lock, "source_digest_differs"
    if kind == "V5":
        world = make_world(
            tmp_path, provider_edit=lambda f: f.update({"src/s30_shapes/_version.py": None})
        )
        return world, {}, wheel_lock, "source_member_missing"
    world = make_world(tmp_path, package=package)
    if kind in ("V2", "V3"):
        lock = lock_for(package.wheel, package.filename, "1.1.0").replace(
            package.filename.encode(), package.filename.replace("1.0.0", "1.1.0").encode()
        )
        coordinate = "pypi:s30-shapes@1.0.0" if kind == "V2" else "pypi:s30-shapes@1.1.0"
        return world, {"lock": lock, "coordinate": coordinate}, lock, "coordinate_differs"
    lock = lock_for(package.wheel, package.filename, digest="ab" * 32)
    return world, {"lock": lock}, lock, "artifact_digest_differs"


@pytest.mark.parametrize("kind", ["V1", "V2", "V3", "V4", "V5"])
def test_cross_repo_wrong_version_is_unresolved(tmp_path, kind):
    world, overrides, lock, detail = _wrong_world(tmp_path, kind)
    stack, reader = leased(world.provider)
    with stack:
        same_name = reader.db.execute(
            "SELECT count(*) FROM symbols WHERE name='area' AND path='src/s30_shapes/geometry.py'"
        ).fetchone()[0]
    assert same_name == 1  # the provider still has a root `area`: only the version differs
    mapping = world.mapping(**overrides)
    result = world.link(mapping_raw=mapping, lock_raw=lock)
    assert outcome(result, L_AREA, "area") == ("unresolved_version_mismatch", detail)
    assert result["links"] == [] and result["counts"]["links"] == 0
    binding = result["package"]["binding"]
    assert (binding["state"], binding["detail"]) == ("version_mismatch", detail)
    checks = binding["checks"]
    if kind == "V1":
        assert checks["sources"] == "digest_differs" and binding["members_differing"] == 1
        assert binding["members_equal"] == 5 and binding["members_missing"] == 0
    elif kind == "V5":
        assert checks["sources"] == "member_missing" and binding["members_missing"] == 1
        assert binding["members_equal"] == 5
    elif kind == "V4":
        assert checks["artifact"] == "differs" and checks["lock"] == "differs"
    else:
        assert checks["coordinate"] == "differs"
    mismatched = [
        i for i in result["references"] if i["resolution"] == "unresolved_version_mismatch"
    ]
    assert len(mismatched) == len(EXPECTED) - 6  # all but the six unsupported forms
    assert all(i["detail"] == detail and i["link_id"] is None for i in mismatched)
    # A reference state, not a rejection: the CLI succeeds and still writes the overlay.
    paths = world.write_inputs(mapping)
    paths["lock"].write_bytes(lock)
    out = tmp_path / "out.json"
    cli = cli_link(world, paths, out)
    assert cli.exit_code == 0, cli.stderr
    assert json.loads(cli.stdout)["binding_state"] == "version_mismatch"
    assert json.loads(out.read_bytes())["links"] == []


def test_the_unmutated_world_links(overlay):
    assert overlay["links"] and overlay["package"]["binding"]["detail"] is None


# ---- AC-S30-003 ------------------------------------------------------------------------------


class Spy:
    """Record every lease (side and reader) taken through `runtime.open_active`."""

    def __init__(self, monkeypatch):
        self.opened: list[tuple[Side, object]] = []
        self.statements: list[str] = []
        real = runtime.open_active

        def wrapper(stack, side):
            reader = real(stack, side)
            self.opened.append((side, reader))
            if side.repo.name == "lib":
                reader.db.set_trace_callback(self.statements.append)
            return reader

        monkeypatch.setattr(runtime, "open_active", wrapper)


def _entity_of(world: World, name: str):
    stack, reader = leased(world.provider)
    with stack:
        return reader.db.execute(
            "SELECT entity_id FROM symbols WHERE path='src/s30_shapes/geometry.py' AND name=?",
            (name,),
        ).fetchone()[0]


def _assert_vocabulary(document):
    assert document["status"] == "potential_impact"
    assert document["basis"] == "static_reference"
    assert document["runtime_observation"] == "not_performed"
    assert document["call_semantics"] == "not_claimed"


def test_cross_repo_missing_snapshot_is_boundary(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    mapping = world.mapping()
    first = world.link()
    area = _entity_of(world, "area")
    file_seed = file_entity("app/main.py")
    stale = pl.canonical_bytes(first)
    world.edit("lib", "tools/notes.py", "notes = 2\n")  # the provider's active snapshot moves
    spy = Spy(monkeypatch)
    doc = world.impact(stale, file_seed, "to_provider", mapping)
    _assert_vocabulary(doc)
    assert doc["results"] == [] and doc["complete"] is True
    owned = [i for i in first["references"] if i["consumer"]["owner_entity_id"] == file_seed]
    assert len(owned) == len(EXPECTED) - 1  # all but the one inside `def run()`
    assert len(doc["boundaries"]) == len(owned)
    for boundary in doc["boundaries"]:
        assert boundary["kind"] == "missing_provider_snapshot"
        assert boundary["detail"] == "snapshot_not_active" and boundary["at"] == "reference"
        assert boundary["repository_id"] == "lib"
        assert boundary["snapshot_id"] == json.loads(mapping)["repositories"][1]["snapshot_id"]
    assert {b["reference_id"] for b in doc["boundaries"]} == {i["id"] for i in owned}
    assert doc["provider_state"]["matched"] is False
    assert doc["provider_state"]["supplied_snapshot_id"] == world.snapshots["lib"]
    assert [side.repo.name for side, _ in spy.opened] == ["app", "lib"]
    assert spy.statements == []  # no provider SQL after the lease
    # (b) the other direction: one boundary at the seed.
    spy.statements.clear()
    back = world.impact(stale, area, "from_provider", mapping)
    _assert_vocabulary(back)
    assert back["results"] == []
    assert [(b["kind"], b["detail"], b["at"]) for b in back["boundaries"]] == [
        ("missing_provider_snapshot", "snapshot_not_active", "seed")
    ]
    assert spy.statements == []
    # (c) the provider graph root is emptied: another closed cause.
    shutil.rmtree(world.graphs / "lib")
    gone = world.impact(stale, file_seed, "to_provider", mapping)
    assert {b["detail"] for b in gone["boundaries"]} == {"index_unavailable"}
    assert gone["provider_state"] == {
        "declared_snapshot_id": json.loads(mapping)["repositories"][1]["snapshot_id"],
        "supplied_snapshot_id": None,
        "matched": False,
    }
    pl.validate_impact(gone)


def test_an_overlay_built_while_the_provider_was_not_active(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    mapping = world.mapping()
    original = (world.lib / "tools/notes.py").read_text()
    world.edit("lib", "tools/notes.py", "notes = 2\n")
    spy = Spy(monkeypatch)
    built = world.link(mapping_raw=mapping)  # the mapped provider snapshot is not the active one
    assert [side.repo.name for side, _ in spy.opened] == ["app", "lib"]
    assert spy.statements == []  # the link read no provider fact either
    monkeypatch.undo()
    assert built["provider"]["state"] == "unavailable"
    assert built["provider"]["detail"] == "snapshot_not_active" and built["links"] == []
    states = {i["resolution"] for i in built["references"] if i["detail"] != "nested_scope_import"}
    assert "unresolved_provider_snapshot_unavailable" in states
    world.edit("lib", "tools/notes.py", original)  # same bytes: the snapshot id is the mapped one
    assert world.snapshots["lib"] == json.loads(mapping)["repositories"][1]["snapshot_id"]
    doc = world.impact(built, file_entity("app/main.py"), "to_provider", mapping)
    _assert_vocabulary(doc)
    kinds = {(b["kind"], b["detail"]) for b in doc["boundaries"]}
    assert ("missing_provider_snapshot", "snapshot_not_active") in kinds
    assert doc["provider_state"]["matched"] is True and doc["results"] == []
    back = world.impact(built, _entity_of(world, "area"), "from_provider", mapping)
    assert [(b["kind"], b["at"]) for b in back["boundaries"]] == [
        ("missing_provider_snapshot", "seed")
    ]


def test_the_provider_trace_records_statements_when_the_provider_is_used(
    world, overlay, monkeypatch
):
    area = _entity_of(world, "area")
    spy = Spy(monkeypatch)
    doc = world.impact(overlay, area, "from_provider")
    assert doc["results"] and spy.statements  # the trace works: it is silent only when unused
    assert any("entities" in statement for statement in spy.statements)


def test_impact_returns_results_when_the_provider_is_the_mapped_one(world, overlay):
    seed = file_entity("app/main.py")
    doc = world.impact(overlay, seed, "to_provider")
    _assert_vocabulary(doc)
    assert doc["depth_limit"] == 1 and doc["complete"] is True
    assert {r["link_id"] for r in doc["results"]} == {
        i["id"] for i in overlay["links"] if i["consumer"]["owner_entity_id"] == seed
    }
    assert {b["kind"] for b in doc["boundaries"]} == {"unresolved_external_reference"}
    assert all(r["relation"] == "external_reference" and r["depth"] == 1 for r in doc["results"])
    area = _entity_of(world, "area")
    back = world.impact(overlay, area, "from_provider")
    assert [r["provider"]["entity_id"] for r in back["results"]] == [area] * 1
    assert back["boundaries"] == [] and back["work"]["results"] == 1
    pl.validate_impact(doc)
    assert pl.canonical_bytes(doc) == pl.canonical_bytes(world.impact(overlay, seed, "to_provider"))


# ---- AC-S30-004 ------------------------------------------------------------------------------


def test_cross_repo_vector_change_invalidates_links(tmp_path):
    world = make_world(tmp_path)
    mapping1 = world.mapping()
    first = world.link()
    first_raw = pl.canonical_bytes(first)
    # (a) a consumer edit and re-index: a new snapshot, hence a new vector.
    world.edit("app", "app/area_tools.py", "def area():\n    return 2\n")
    mapping2 = world.mapping()
    assert json.loads(mapping2) != json.loads(mapping1)
    second = world.link(mapping_raw=mapping2, previous_raw=first_raw)
    assert second["supersedes"] == {
        "overlay_id": first["overlay_id"],
        "vector_digest": first["vector_digest"],
        "links_invalidated": 7,
        "invalidated_link_ids": sorted(i["id"] for i in first["links"]),
        "invalidated_link_ids_truncated": False,
    }
    assert not {i["id"] for i in first["links"]} & {i["id"] for i in second["links"]}
    assert len(second["links"]) == 7
    assert all(i["revision_vector"] == second["revision_vector"] for i in second["links"])
    assert second["revision_vector"][0]["snapshot_id"] == world.snapshots["app"]
    # (e) the first overlay no longer matches the active consumer snapshot.
    with pytest.raises(rv.CrossRepoRejected) as stale:
        world.impact(first_raw, file_entity("app/main.py"), "to_provider", mapping1)
    assert stale.value.reason == "stale_revision_vector"
    # (b) a provider re-index and a new mapping.
    second_raw = pl.canonical_bytes(second)
    world.edit("lib", "tools/notes.py", "notes = 3\n")
    mapping3 = world.mapping()
    third = world.link(mapping_raw=mapping3, previous_raw=second_raw)
    assert third["supersedes"]["links_invalidated"] == 7
    assert third["supersedes"]["invalidated_link_ids"] == sorted(i["id"] for i in second["links"])
    assert len(third["links"]) == 7
    # (c) the provider moves again and the mapping is stale: unavailable, every link invalidated.
    third_raw = pl.canonical_bytes(third)
    world.edit("lib", "tools/notes.py", "notes = 4\n")
    fourth = world.link(mapping_raw=mapping3, previous_raw=third_raw)
    assert fourth["links"] == [] and fourth["provider"]["detail"] == "snapshot_not_active"
    assert fourth["supersedes"]["links_invalidated"] == 7
    assert fourth["package"]["binding"]["state"] == "provider_snapshot_unavailable"
    states = {i["resolution"] for i in fourth["references"]}
    assert "unresolved_provider_snapshot_unavailable" in states and LINKED not in states
    # (f) a previous overlay of other repository ids is refused.
    other_ids = world.mapping(consumer_id="app2", provider_id="lib2")
    with pytest.raises(rv.CrossRepoRejected) as unrelated:
        world.link(mapping_raw=other_ids, previous_raw=third_raw)
    assert unrelated.value.reason == "previous_overlay_unrelated"


def test_refresh_is_a_fixpoint_once_the_overlay_carries_supersedes(tmp_path):
    world = make_world(tmp_path)
    first = world.link_bytes()
    assert world.link_bytes(previous_raw=first) == first  # equal content: supersedes stays null
    world.edit("app", "app/area_tools.py", "def area():\n    return 3\n")
    fresh = world.link_bytes(mapping_raw=world.mapping())
    second = world.link_bytes(mapping_raw=world.mapping(), previous_raw=first)
    assert json.loads(second)["supersedes"] is not None and second != fresh
    again = world.link_bytes(mapping_raw=world.mapping(), previous_raw=second)
    assert again == fresh and json.loads(again)["supersedes"] is None  # a fresh build
    assert world.link_bytes(mapping_raw=world.mapping(), previous_raw=again) == fresh
    assert world.link_bytes(mapping_raw=world.mapping(), previous_raw=second) == fresh
    # The CLI agrees: refresh with --previous equal to a fresh build is byte-identical.
    paths = world.write_inputs()
    out1, out2 = tmp_path / "o1.json", tmp_path / "o2.json"
    assert cli_link(world, paths, out1).exit_code == 0
    again_cli = cli_link(world, paths, out2, "--previous", out1)
    assert again_cli.exit_code == 0 and out2.read_bytes() == out1.read_bytes() == fresh
    assert json.loads(again_cli.stdout)["links_invalidated"] is None


# ---- AC-S30-005 ------------------------------------------------------------------------------


def _reordered(document, indent):
    def sort_reverse(node):
        if isinstance(node, dict):
            return {key: sort_reverse(node[key]) for key in sorted(node, reverse=True)}
        if isinstance(node, list):
            return [sort_reverse(item) for item in node]
        return node

    return json.dumps(sort_reverse(document), indent=indent).encode()


def test_cross_repo_link_replay_is_idempotent(tmp_path):
    package = make_package(environment={"os_name": "posix", "sys_platform": "linux"})
    world = make_world(tmp_path / "w", package=package)
    base = world.link_bytes()
    assert world.link_bytes() == base
    document = world.document()
    swapped = dict(document, repositories=list(reversed(document["repositories"])))
    assert world.link_bytes(mapping_raw=json.dumps(swapped).encode()) == base
    assert world.link_bytes(mapping_raw=_reordered(document, 4)) == base
    env_flipped = json.loads(json.dumps(document))
    env_flipped["consumer"]["environment"] = {"sys_platform": "linux", "os_name": "posix"}
    assert world.link_bytes(mapping_raw=json.dumps(env_flipped, indent=1).encode()) == base
    overlay = json.loads(base)
    pl.validate_overlay(overlay)
    assert overlay["package"]["binding"]["state"] == "bound" and len(overlay["links"]) == 7
    # The CLI: inputs moved to other directories and the options permuted.
    paths = world.write_inputs()
    moved = tmp_path / "elsewhere" / "nested"
    moved.mkdir(parents=True)
    copies = {name: moved / path.name for name, path in paths.items()}
    for name, path in paths.items():
        shutil.copy(path, copies[name])
    out1, out2, out3 = tmp_path / "o1.json", tmp_path / "o2.json", moved / "o3.json"
    first = cli_link(world, paths, out1)
    other_order = [
        "cross-repo-link",
        "--output", out2,
        "--provider-graph-root", world.graphs / "lib",
        "--provider-repo", world.lib,
        "--consumer-graph-root", world.graphs / "app",
        "--consumer-repo", world.app,
        "--package-snapshot", copies["package"],
        "--lock", copies["lock"],
        "--mapping", copies["mapping"],
    ]  # fmt: skip
    second = cli_run(*other_order)
    assert first.exit_code == second.exit_code == 0
    assert out1.read_bytes() == out2.read_bytes() == base
    assert first.stdout_bytes == second.stdout_bytes
    third = cli_link(world, paths, out3, "--previous", out1)  # fixpoint
    assert third.exit_code == 0 and out3.read_bytes() == base
    assert third.stdout_bytes == first.stdout_bytes
    # Different hash seeds in fresh interpreters.
    outputs = []
    for seed in ("1", "2"):
        out = tmp_path / ("hash" + seed + ".json")
        env = {**os.environ, "PYTHONHASHSEED": seed}
        args = [
            sys.executable, "-m", "atlas_aci", "cross-repo-link",
            "--mapping", str(paths["mapping"]),
            "--lock", str(paths["lock"]),
            "--package-snapshot", str(paths["package"]),
            "--consumer-repo", str(world.app),
            "--consumer-graph-root", str(world.graphs / "app"),
            "--provider-repo", str(world.lib),
            "--provider-graph-root", str(world.graphs / "lib"),
            "--output", str(out),
        ]  # fmt: skip
        done = subprocess.run(args, capture_output=True, env=env, timeout=120)
        assert done.returncode == 0, done.stderr
        outputs.append(out.read_bytes())
    assert outputs == [base, base]
    # Fresh graph roots at the same repository paths, files rewritten in reverse order.
    for repo in (world.app, world.lib):
        for path in sorted(repo.rglob("*.py"), reverse=True):
            data = path.read_bytes()
            path.unlink()
            path.write_bytes(data)
    fresh = tmp_path / "graphs2"
    for name in ("app", "lib"):
        before = world.snapshots[name]
        assert world.reindex(name, fresh) == before
    rebuilt = world.link_bytes(consumer=world.side("app", fresh), provider=world.side("lib", fresh))
    assert rebuilt == base


# ---- AC-S30-006 ------------------------------------------------------------------------------


def test_cross_repo_allowlist_bounds_traversal(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    repos: list[Path] = []
    real_init = CodeGraph.__init__

    def recording(self, repo, *args, **kwargs):
        repos.append(Path(repo).resolve())
        real_init(self, repo, *args, **kwargs)

    monkeypatch.setattr(CodeGraph, "__init__", recording)
    spy = Spy(monkeypatch)
    built = world.link()
    assert {p.name for p in repos} == {"app", "lib"}  # a set: a lease builds a second CodeGraph
    assert {side.repo.name for side, _ in spy.opened} == {"app", "lib"}
    repos.clear()
    spy.opened.clear()
    doc = world.impact(built, file_entity("app/main.py"), "to_provider")
    assert {p.name for p in repos} == {"app", "lib"} and doc["results"]
    assert {side.repo.name for side, _ in spy.opened} == {"app", "lib"}
    # Results carry only ids of the mapping; the provider's own imports are never followed.
    allowed = {"app", "lib"}
    assert {r["consumer"]["repository_id"] for r in doc["results"]} <= allowed
    assert {r["provider"]["repository_id"] for r in doc["results"]} <= allowed
    assert {e["repository_id"] for e in built["revision_vector"]} == allowed
    names = {i["name"] for i in built["references"]} | {
        i["qualified_name"] or "" for i in built["references"]
    }
    assert not {"math", "functools"} & names
    spy.opened.clear()
    repos.clear()
    # A package naming another repository: rejected before any lease or any other input.
    stranger = world.mapping(package_provider="other")
    with pytest.raises(rv.CrossRepoRejected) as named:
        world.link(mapping_raw=stranger)
    assert named.value.reason == "repository_not_allowlisted"
    three = json.loads(world.mapping())
    three["repositories"].append(
        {"repository_id": "other", "role": "provider", "snapshot_id": world.snapshots["other"]}
    )
    with pytest.raises(rv.CrossRepoRejected) as many:
        world.link(mapping_raw=json.dumps(three).encode())
    assert many.value.reason == "invalid_mapping"
    for extra in ("root", "path"):
        keyed = json.loads(world.mapping())
        keyed["repositories"][1][extra] = "/elsewhere"
        with pytest.raises(rv.CrossRepoRejected) as bad:
            world.link(mapping_raw=json.dumps(keyed).encode())
        assert bad.value.reason == "invalid_mapping"
    assert repos == [] and spy.opened == []
    # The CLI reads no lock and no package snapshot for such a mapping.
    paths = world.write_inputs(stranger)
    opened: list[str] = []
    real_open = os.open

    def recorder(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", recorder)
    result = cli_link(world, paths, tmp_path / "o.json")
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Cross-repository link rejected: repository_not_allowlisted\n"
    assert str(paths["mapping"]) in opened
    assert str(paths["lock"]) not in opened and str(paths["package"]) not in opened
    assert repos == [] and spy.opened == []
    # An overlay re-hashed to name another repository: refused before any lease.
    stale_mapping = world.mapping()
    world.edit("lib", "tools/notes.py", "notes = 9\n")  # the mapped provider snapshot is gone
    forged = json.loads(pl.canonical_bytes(world.link(mapping_raw=stale_mapping)))
    assert forged["links"] == [] and forged["provider"]["state"] == "unavailable"
    forged["provider"]["repository_id"] = "other"
    forged["revision_vector"][1]["repository_id"] = "other"
    forged["vector_digest"] = rv.vector_digest(forged["revision_vector"])
    forged["overlay_id"] = pl.overlay_identity(forged)
    pl.validate_overlay(forged)
    repos.clear()
    spy.opened.clear()
    with pytest.raises(rv.CrossRepoRejected) as renamed:
        world.impact(
            pl.canonical_bytes(forged), file_entity("app/main.py"), "to_provider", stale_mapping
        )
    assert renamed.value.reason == "repository_not_allowlisted"
    assert repos == [] and spy.opened == []
    # --help names exactly the two role root pairs and nothing else that takes a location.
    help_text = cli_run("cross-repo-link", "--help").stdout
    roots = {word for word in help_text.split() if word.startswith("--") and "root" in word}
    repo_options = {word for word in help_text.split() if word.startswith("--") and "repo" in word}
    assert roots == {"--consumer-graph-root", "--provider-graph-root"}
    assert repo_options == {"--consumer-repo", "--provider-repo"}


def test_seeds_of_unmapped_repositories_are_unknown(tmp_path):
    world = make_world(tmp_path)
    built = world.link()
    only_other = file_entity("src/only_other.py")
    for direction in ("to_provider", "from_provider"):
        with pytest.raises(rv.CrossRepoRejected) as unknown:
            world.impact(built, only_other, direction)
        assert unknown.value.reason == "unknown_seed"


# ---- AC-S30-007 ------------------------------------------------------------------------------


def test_cross_repo_overlay_preserves_source_graphs(tmp_path):
    world = make_world(tmp_path / "w")
    out = tmp_path / "out"
    out.mkdir()
    paths = world.write_inputs()
    before = world_inventory(world)
    assert any("graphs" in key for key in before) and len(before) > 20
    # Library and CLI: link, refresh, impact in both directions.
    built = world.link()
    raw = pl.canonical_bytes(built)
    world.link(previous_raw=raw)
    seed, area = file_entity("app/main.py"), _entity_of(world, "area")
    world.impact(built, seed, "to_provider")
    world.impact(built, area, "from_provider")
    one, two = out / "one.json", out / "two.json"
    assert cli_link(world, paths, one).exit_code == 0
    assert cli_link(world, paths, two, "--previous", one).exit_code == 0
    for direction, who in (("to_provider", seed), ("from_provider", area)):
        done = cli_impact(world, one, paths["mapping"], who, direction)
        assert done.exit_code == 0, done.stderr
    assert world_inventory(world) == before
    assert sorted(p.name for p in out.iterdir()) == ["one.json", "two.json"]
    # Every rejection path leaves the same inventory.
    wrong_consumer = world.document()
    wrong_consumer["repositories"][0]["snapshot_id"] = "9" * 64
    cases = [
        ("mapping", lambda: world.link(mapping_raw=b"{")),
        ("lock", lambda: world.link(lock_raw=world.lock + b"#")),
        ("consumer", lambda: world.link(mapping_raw=json.dumps(wrong_consumer).encode())),
        ("previous", lambda: world.link(previous_raw=b"[]")),
        ("package", lambda: world.link(package_raw=world.package.snapshot + b" ")),
        ("seed", lambda: world.impact(built, "f" * 64, "to_provider")),
        ("seed2", lambda: world.impact(built, "f" * 64, "from_provider")),
        ("overlay", lambda: world.impact(b"{}", seed, "to_provider")),
    ]
    for name, call in cases:
        with pytest.raises(rv.CrossRepoRejected):
            call()
        assert world_inventory(world) == before, name
    # A provider that is not the mapped one is a state, not a change.
    unmapped = world.link(provider=world.side("other"))
    assert unmapped["links"] == [] and unmapped["provider"]["detail"] == "snapshot_not_active"
    assert world_inventory(world) == before
    # CLI rejection and usage paths.
    bad, bad_lock = out / "bad.json", out / "bad.lock"
    bad.write_bytes(b"SENTINEL")
    bad_lock.write_bytes(b"x")
    inside = cli_link(world, paths, world.app / "inside.json")
    assert inside.exit_code == 2 and not (world.app / "inside.json").exists()
    rejected = cli_link(world, dict(paths, lock=bad_lock), bad)
    assert rejected.exit_code == 1 and bad.read_bytes() == b"SENTINEL"
    stale = cli_impact(world, one, paths["mapping"], "f" * 64, "to_provider")
    assert stale.exit_code == 1 and stale.stdout_bytes == b""
    assert world_inventory(world) == before
    assert sorted(p.name for p in out.iterdir()) == ["bad.json", "bad.lock", "one.json", "two.json"]
    assert not list(world.root.rglob(".atlas-cross-repo-overlay-*"))
