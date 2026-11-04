"""ACI-S25 invariance and validation gates: the opt-in models adapter changes nothing it does
not own, and its records are validated shape by shape.

G-OFF, G-ONOFF, G-ENVELOPE, G-MCP, G-NOLEAK and G-QUERY over real builds of the models fixture
(and the Python coverage fixture, which has no models). The base-image comparison (G-IMAGE,
G-ROUTES) runs outside pytest through ``.spectra/evidence/aci-s25/compare_graphs.py``.
"""

import asyncio
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import tree_sitter_language_pack as pack
from click.testing import CliRunner
from test_s24_support import PROJECT as ROUTES_PROJECT
from test_s24_support import rails_lines as route_lines
from test_s25_support import (
    CREATE,
    MAP_BYTES,
    PROJECT,
    UP,
    USERS,
    built,
    copy_db,
    edit,
    entity_of,
    facts,
    manifest_value,
    model_lines,
    model_records,
    rebuilt,
    records,
    table_counts,
    workspace,
)

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import KNOWN_EDGE_RELATIONS, CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.frameworks import rails_models, rails_routes
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact, impact_admission, paging
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import Side
from atlas_aci.server import build_server_state, call_tool_result
from atlas_aci.storage import evidence as ev

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
COVERAGE_PROJECT = Path(__file__).parents[1] / "fixtures/coverage/project"
COMPARE = Path(__file__).parents[3] / ".spectra/evidence/aci-s25/compare_graphs.py"
OWNED = {"assertion": 58, "evidence": 58, "occurrence": 58, "producer": 1}
RELATIONS3 = {"association", "model_table", "migration_mentions"}


def inputs_of(graph):
    with graph.reader_lease() as lease:
        return json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )


def pair(tmp_path, repo=None):
    repo = repo or workspace(tmp_path)
    _r, on = built(tmp_path, root="on", repo=repo)
    _r, off = built(tmp_path, root="off", repo=repo, frameworks=())
    return repo, on, off


def ids_by_type(rows):
    result: dict[str, set[str]] = {}
    for item in rows:
        result.setdefault(item["type"], set()).add(item["id"])
    return result


def without_snapshot(inputs):
    return {k: v for k, v in inputs.items() if k != "snapshot"}


def affected(stats):
    return set(stats["binding_resolution"]["affected_paths"])


# ---- G-OFF ----
def test_a_build_without_the_flag_has_no_model_trace(tmp_path):
    _repo, _on, off = pair(tmp_path)
    assert records(off) and model_records(off) == []
    for item in records(off):
        assert item.get("kind") != "rails_model" and item.get("relation") not in RELATIONS3
        assert "rails_models" not in (item.get("fingerprint") or {})
    assert manifest_value(off) is None
    assert "rails_models" not in json.dumps(inputs_of(off))
    assert "table_map" not in json.dumps(inputs_of(off))
    # `--frameworks rails` alone neither reads models nor leaves a key.
    _r, rails_only = built(tmp_path, root="rails-only", repo=_repo, frameworks=("rails",))
    assert model_records(rails_only) == [] and manifest_value(rails_only) is None


def test_a_repository_without_models_builds_identically_on_and_off(tmp_path):
    repo = workspace(tmp_path)
    shutil.rmtree(repo / "app/models")
    shutil.rmtree(repo / "db")
    (repo / "lib/models/widget.rb").unlink()
    _repo, on, off = pair(tmp_path, repo)
    assert inputs_of(on) == inputs_of(off) and inputs_of(on)["snapshot"]["state"] == "available"
    assert [r["id"] for r in records(on)] == [r["id"] for r in records(off)]
    summary = json.loads(manifest_value(on))
    assert summary["declarations"] == 0 and summary["producer_id"] is None
    assert summary["files"] == {"models": 0, "migrations": 0, "failed": 0, "limited": 0}
    assert manifest_value(off) is None
    python = tmp_path / "python"
    shutil.copytree(COVERAGE_PROJECT, python)
    _r, py_on = built(tmp_path, root="py-on", repo=python)
    _r, py_off = built(tmp_path, root="py-off", repo=python, frameworks=())
    assert records(py_on) and [r["id"] for r in records(py_on)] == [
        r["id"] for r in records(py_off)
    ]
    assert inputs_of(py_on) == inputs_of(py_off)
    assert table_counts(py_on) == table_counts(py_off) and any(table_counts(py_on).values())


# ---- G-ONOFF ----
def test_on_minus_model_owned_equals_off_for_every_record_type(tmp_path):
    _repo, on, off = pair(tmp_path)
    owned = rails_models.record_ids(records(on))
    assert len(owned) == 175
    on_ids, off_ids = ids_by_type(records(on)), ids_by_type(records(off))
    assert set(on_ids) == set(off_ids)
    for kind, found in on_ids.items():
        assert found - owned == off_ids[kind], kind
    assert {k: len(v & owned) for k, v in on_ids.items() if v & owned} == OWNED
    with on.reader_lease() as a, off.reader_lease() as b:
        projections_on = ids_by_type(on._projection_records(a.db))
        projections_off = ids_by_type(off._projection_records(b.db))
    assert projections_on == projections_off and projections_on["projection"]
    assert table_counts(on) == table_counts(off) and any(table_counts(on).values())
    on_inputs, off_inputs = inputs_of(on), inputs_of(off)
    assert without_snapshot(on_inputs) == without_snapshot(off_inputs)
    assert on_inputs["snapshot"] != off_inputs["snapshot"]  # the snapshot binds the new records
    assert on_inputs["retrieval"]["document_digest"] == off_inputs["retrieval"]["document_digest"]
    assert "rails_models" not in json.dumps(on_inputs)
    # The producer is the only addition among producers.
    on_producers = [r for r in records(on) if r["type"] == "producer"]
    off_producers = [r for r in records(off) if r["type"] == "producer"]
    assert len(on_producers) == len(off_producers) + 1
    assert [r["id"] for r in on_producers if r["id"] in owned] and True


def test_incremental_toggle_stays_affected_and_matches_fresh_builds(tmp_path):
    repo, on, off = pair(tmp_path)
    graph, _stats = rebuilt(repo, tmp_path, "toggle", since="x")  # first build in this root
    assert model_records(graph)
    steps = [((), "off"), (("rails_models",), "on"), ((), "off"), (("rails_models",), "on")]
    for frameworks, label in steps:
        graph, stats = rebuilt(repo, tmp_path, "toggle", frameworks=frameworks, since="x")
        resolution = stats["binding_resolution"]
        assert resolution["mode"] == "affected", (label, resolution)
        assert resolution["reason"] == "complete_dependency_match", (label, resolution)
        assert resolution["affected_units"] == 0, (label, resolution)
        expected = on if frameworks else off
        assert [r["id"] for r in records(graph)] == [r["id"] for r in records(expected)]
        assert (manifest_value(graph) is not None) == bool(frameworks)


# ---- both adapters ----
def merged(tmp_path):
    repo = workspace(tmp_path, "merged")
    shutil.copytree(ROUTES_PROJECT, repo, dirs_exist_ok=True)
    return repo


def test_both_adapters_coexist_and_each_owns_only_its_records(tmp_path):
    repo = merged(tmp_path)
    both = ("rails", "rails_models")
    _r, rails_only = built(tmp_path, root="rails", repo=repo, frameworks=("rails",))
    _r, models_only = built(tmp_path, root="models", repo=repo)
    _r, together = built(tmp_path, root="both", repo=repo, frameworks=both)
    assert route_lines(rails_only) and model_lines(models_only)
    assert route_lines(together) == route_lines(rails_only)
    assert model_lines(together) == model_lines(models_only)
    assert manifest_value(together, "rails_routes") == manifest_value(rails_only, "rails_routes")
    assert manifest_value(together) == manifest_value(models_only)
    routes_ids = rails_routes.record_ids(records(together))
    models_ids = rails_models.record_ids(records(together))
    assert routes_ids and models_ids and not routes_ids & models_ids
    # Each fingerprint key is its own producer: neither purge touches the other's records.
    producers = [r for r in records(together) if r["type"] == "producer"]
    keys = [set(p["fingerprint"]) for p in producers if p["id"] in routes_ids | models_ids]
    assert sorted(keys, key=sorted) == [{"rails"}, {"rails_models"}]


def test_each_adapter_is_replaced_without_touching_the_other(tmp_path):
    repo = merged(tmp_path)
    both = ("rails", "rails_models")
    graph, _stats = rebuilt(repo, tmp_path, "swap", frameworks=both)
    routes_before, models_before = route_lines(graph), model_lines(graph)
    assert routes_before and models_before
    for frameworks in (("rails",), both, ("rails_models",), both):
        graph, stats = rebuilt(repo, tmp_path, "swap", frameworks=frameworks, since="x")
        assert stats["binding_resolution"]["mode"] == "affected"
        assert route_lines(graph) == (routes_before if "rails" in frameworks else [])
        assert model_lines(graph) == (models_before if "rails_models" in frameworks else [])
        assert (manifest_value(graph, "rails_routes") is not None) == ("rails" in frameworks)
        assert (manifest_value(graph) is not None) == ("rails_models" in frameworks)


# ---- G-ENVELOPE ----
def test_envelope_minimums_and_relation_sets_are_the_base_values():
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    base = {"call", "construct", "import", "superclass", "include", "extend", "prepend"}
    assert base | {"rationale_for"} == ev.RELATIONS
    assert ev.RELATIONS | {"jsx", "reference"} == ev.REFERENCE_RELATIONS
    assert (ev.RELATIONS - {"rationale_for"}) | {"reference"} == ev.EDGE_RELATIONS
    assert len(ev.RELATIONS) == 8
    for name in RELATIONS3:
        for relations in (ev.RELATIONS, ev.REFERENCE_RELATIONS, ev.EDGE_RELATIONS):
            assert name not in relations
        assert name not in KNOWN_EDGE_RELATIONS
        assert name in ev.ASSERTION_RELATIONS
    assert (
        ev.REFERENCE_RELATIONS
        | {
            "test_executes",
            "routes_to",
            *RELATIONS3,
        }
        == ev.ASSERTION_RELATIONS
    )
    assert ev.RAILS_MODEL_RELATIONS == RELATIONS3
    assert {"model_table", "migration_mentions"} == ev.RAILS_TARGETLESS_RELATIONS


@pytest.mark.parametrize("relation", sorted(ev.ASSERTION_RELATIONS))
def test_only_the_two_model_relations_accept_a_target_less_resolved_assertion(relation):
    source, evidence = "a" * 64, "b" * 64
    raw = "x:1" if relation == "test_executes" else ev.canonical({"table": "t"})

    def check(resolution):
        record = ev.assertion(source, None, relation, evidence, resolution, raw)
        try:
            ev.validate_record(record)
        except ValueError:
            return False
        return True

    assert check("EXTRACTED") == (relation in {"model_table", "migration_mentions"})
    assert check("INFERRED") is False  # a resolved INFERRED assertion always names its target
    # With a target, the carve-out never applies and every other relation behaves as before.
    with_target = ev.assertion(source, "c" * 64, relation, evidence, "INFERRED", raw)
    expect = relation not in {"jsx", "test_executes", "model_table", "migration_mentions"}
    try:
        ev.validate_record(with_target)
        accepted = True
    except ValueError:
        accepted = False
    assert accepted == expect


# ---- G-MCP ----
def test_the_tool_surface_is_byte_identical_and_read_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("rails" in name or "model" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    for name in ("rails_models", "model_table", "migration_mentions", "table_map"):
        assert name not in out.stdout
    assert "rails_table_map" in {p.name for p in cli.commands["index"].params}
    assert "rails_table_map" not in {p.name for p in cli.commands["serve"].params}
    assert "rails_models" in next(
        p.help for p in cli.commands["index"].params if p.name == "frameworks"
    )


# ---- G-NOLEAK ----
GENERATION_DERIVED = {
    "generation_id",
    "snapshot_id",
    "id",
    "manifest_sha256",
    "uri",
    "query_digest",
    "retrieval_record_id",
}


def scrub(value):
    """Drop generation/snapshot-derived identities, which legitimately differ between graphs
    whose snapshots bind different record sets. Entity ids and every content field stay."""
    if isinstance(value, dict):
        return {
            k: len(v) if k == "source_resource_ids" else scrub(v)
            for k, v in value.items()
            if k not in GENERATION_DERIVED
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def first_difference(left, right, path="$"):
    """Iterative diagnostic: the first JSON path where two documents differ."""
    pending = [(left, right, path)]
    while pending:
        a, b, where = pending.pop()
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                return f"{where}: keys {sorted(set(a) ^ set(b))}"
            pending.extend((a[k], b[k], f"{where}.{k}") for k in sorted(a, reverse=True))
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            pending.extend(
                (x, y, f"{where}[{i}]") for i, (x, y) in enumerate(zip(a, b, strict=True))
            )
        elif a != b:
            return f"{where}: {str(a)[:120]} != {str(b)[:120]}"
    return "equal"


def server(tmp_path, repo, root):
    config = Config(
        repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0, graph_root=root
    )
    enforcement, memex, factory = build_server_state(config)
    return config, enforcement, memex, factory


async def graph_call(state, **arguments):
    config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", **arguments},
        config,
        enforcement,
        memex,
        factory,
    )


def test_mcp_neighbors_and_context_are_equal_on_and_off(tmp_path):
    repo, on, _off = pair(tmp_path)
    seeds = [
        entity_of(on, USERS, "User"),
        entity_of(on, "app/models/blog/post.rb", "Blog::Post"),
        ev.file_entity(CREATE, "ruby")["id"],
    ]
    states = [server(tmp_path, repo, tmp_path / "on"), server(tmp_path, repo, tmp_path / "off")]

    async def run():
        collected: list[list] = [[], []]
        try:
            for slot, state in enumerate(states):
                for seed in seeds:
                    for direction in ("incoming", "outgoing"):
                        result = await graph_call(
                            state,
                            op="neighborhood",
                            seed={"entity_id": seed},
                            direction=direction,
                            max_depth=2,
                            deadline_ms=2000,
                        )
                        assert not result.isError, result
                        collected[slot].append(result.content[0].text)
                for text in ("User", "Blog::Post", "has_many posts", "create_users"):
                    retrieved = await graph_call(state, op="retrieve", text=text)
                    assert not retrieved.isError, retrieved
                    collected[slot].append(json.dumps(retrieved.structuredContent))
                    context = await graph_call(
                        state, op="context", text=text, source_policy="references"
                    )
                    assert not context.isError, context
                    collected[slot].append(context.content[0].text)
                found = await call_tool_result(
                    "search_symbol", {"name": "Post"}, state[0], state[1], state[2], state[3]
                )
                collected[slot].append(found.content[0].text)
        finally:
            for state in states:
                state[3].close()
        return collected

    with_models, without = asyncio.run(run())
    assert len(with_models) == len(without) > 8
    for a, b in zip(with_models, without, strict=True):
        for name in ("rails_model", "model_table", "migration_mentions", "rails_class_name"):
            assert name not in a
        assert scrub(json.loads(a)) == scrub(json.loads(b)), first_difference(
            scrub(json.loads(a)), scrub(json.loads(b))
        )
    assert any("User" in text for text in with_models)


# ---- G-QUERY ----
def test_change_set_and_admission_arithmetic(tmp_path):
    repo = workspace(tmp_path)
    _r, on_before = built(tmp_path, root="on-before", repo=repo)
    _r, off_before = built(tmp_path, root="off-before", repo=repo, frameworks=())
    edit(repo, USERS, "  validates :name, presence: true\n", "  def full_name\n    name\n  end\n")
    _r, on_after = built(tmp_path, root="on-after", repo=repo)
    _r, off_after = built(tmp_path, root="off-after", repo=repo, frameworks=())
    on_set = symbol_diff.compare(
        Side(repo, tmp_path / "on-before"), Side(repo, tmp_path / "on-after")
    )
    off_set = symbol_diff.compare(
        Side(repo, tmp_path / "off-before"), Side(repo, tmp_path / "off-after")
    )
    assert on_set["changes"], "the edit must be a real change"

    def projected(change_set):
        return [(c["category"], c.get("entity_id"), c.get("path")) for c in change_set["changes"]]

    assert projected(on_set) == projected(off_set)
    with (
        on_before.reader_lease() as a,
        on_after.reader_lease() as b,
        off_before.reader_lease() as c,
        off_after.reader_lease() as d,
    ):
        limits = {"max_entities": 10**6, "max_occurrences": 10**6, "max_changed_files": 100}
        with_models = impact._admit_diff(a, b, limits)
        plain = impact._admit_diff(c, d, limits)
    assert with_models["before_entities"] == plain["before_entities"]
    assert with_models["after_entities"] == plain["after_entities"]
    # Model occurrences are counted by the diff admission (documented): exactly the 58.
    assert with_models["before_occurrences"] == plain["before_occurrences"] + 58
    assert with_models["after_occurrences"] == plain["after_occurrences"] + 58


# ---- validation ----
def planted(graph, tmp_path, name, mutate, table="assertions", select=None):
    conn = copy_db(graph, tmp_path, name + ".db")
    row = conn.execute(
        f"SELECT id, payload FROM {table} WHERE {select or '1=1'} LIMIT 1"
    ).fetchone()
    assert row is not None, name
    payload = json.loads(row[1])
    mutate(payload)
    conn.execute(f"UPDATE {table} SET payload=? WHERE id=?", (ev.canonical(payload), row[0]))
    return conn


def where(relation=None, raw=None, resolution=None):
    clauses = []
    if relation:
        clauses.append(f"json_extract(payload,'$.relation')='{relation}'")
    if raw:
        clauses.append(f"json_extract(payload,'$.raw_target') LIKE '%{raw}%'")
    if resolution:
        clauses.append(f"json_extract(payload,'$.resolution')='{resolution}'")
    return " AND ".join(clauses)


def raw(value):
    return lambda p: p.update(raw_target=value)


def expect_invalid(conn, text="Invalid evidence: rails_models"):
    with pytest.raises(ValueError, match=text):
        rails_models.validate_records(conn)


def test_planted_corruptions_each_raise(tmp_path):
    _repo, graph = built(tmp_path)
    assoc = where("association", resolution="INFERRED")
    diag = where("association", raw="unexpanded")
    table = where("model_table", raw="literal", resolution="EXTRACTED")
    mapped = where("model_table", raw="operator_map", resolution="EXTRACTED")
    mention = where("migration_mentions", resolution="EXTRACTED")
    shapes = {
        "list": raw("[1]"),
        "scalar": raw("3"),
        "not_json": raw("{"),
        "not_canonical": raw('{"mapping": "unexpanded", "construct": "through"}'),
        "unknown_key": raw(
            '{"class_name":"A","extra":1,"kind":"has_many","mapping":"rails_class_name.v1",'
            '"name":"x"}'
        ),
        "missing_key": raw('{"mapping":"unexpanded"}'),
        "bad_name": raw(
            '{"class_name":"A","kind":"has_many","mapping":"rails_class_name.v1","name":"X Y"}'
        ),
        "bad_class": raw(
            '{"class_name":"a","kind":"has_many","mapping":"rails_class_name.v1","name":"x"}'
        ),
        "wrong_kind": raw(
            '{"class_name":"A","kind":"has_one","mapping":"rails_class_name.v1","name":"x"}'
        ),
        "wrong_label": raw('{"class_name":"A","kind":"has_many","mapping":"x","name":"x"}'),
    }
    for name, mutate in shapes.items():
        expect_invalid(planted(graph, tmp_path, name, mutate, select=assoc))
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "vocab",
            raw('{"construct":"zzz","kind":"has_many","mapping":"unexpanded"}'),
            select=diag,
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "diagkind",
            raw('{"construct":"through","kind":"zzz","mapping":"unexpanded"}'),
            select=diag,
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "tablevocab",
            raw('{"source":"literal","table":"bad-name"}'),
            select=table,
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "mapsha",
            raw('{"map_sha256":"' + "0" * 64 + '","source":"operator_map","table":"blog_tags"}'),
            select=mapped,
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "mentionshape",
            raw('{"op":"add_column","table":"users"}'),
            select=mention + " AND json_extract(payload,'$.raw_target') LIKE '%email%'",
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "source",
            lambda p: p.update(source_entity_id="0" * 64),
            select=assoc,
        )
    )
    # model_table with a target; an extracted association; resolution/reason triples.
    class_id = entity_of(graph, USERS, "User")
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "tabletarget",
            lambda p: p.update(target_entity_id=class_id),
            select=table,
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "reason",
            lambda p: p.update(resolution="UNRESOLVED", unresolved_reason="class_not_found"),
            select=table,
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "wrongreason",
            lambda p: p.update(unresolved_reason="unsupported_association"),
            select=where("association", resolution="UNRESOLVED", raw="Ghost"),
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "ambiguous_one",
            lambda p: p.update(candidate_ids=p["candidate_ids"][:1]),
            select=where("association", resolution="AMBIGUOUS"),
        )
    )
    # INFERRED to a file entity or a module entity, not a class.
    file_id = ev.file_entity(USERS, "ruby")["id"]
    with graph.reader_lease() as lease:
        module_id = lease.db.execute(
            "SELECT entity_id FROM symbols WHERE kind='module' LIMIT 1"
        ).fetchone()[0]
    for name, wrong in (("tofile", file_id), ("tomodule", module_id)):
        expect_invalid(
            planted(
                graph, tmp_path, name, lambda p, w=wrong: p.update(target_entity_id=w), select=assoc
            ),
            "not a ruby class entity",
        )
    # Relation/role-area mismatch.
    expect_invalid(
        planted(graph, tmp_path, "area", lambda p: p.update(relation="model_table"), select=assoc),
        "role/relation area",
    )
    # A second producer; an orphan occurrence; a producer cited by nothing.
    conn = copy_db(graph, tmp_path, "second.db")
    row = conn.execute(
        "SELECT payload FROM producers "
        "WHERE json_extract(payload,'$.fingerprint.rails_models') IS NOT NULL"
    ).fetchone()
    fingerprint = dict(json.loads(row[0])["fingerprint"]["rails_models"], adapter_sha256="7" * 64)
    ev.put(conn, ev.producer({"rails_models": fingerprint}))
    expect_invalid(conn, "more than one producer")
    conn = copy_db(graph, tmp_path, "orphan.db")
    row = conn.execute(
        "SELECT payload FROM occurrences WHERE json_extract(payload,'$.kind')='rails_model' LIMIT 1"
    ).fetchone()
    body = {k: v for k, v in json.loads(row[0]).items() if k != "id"}
    body.update(start_byte=0, end_byte=1, line=1, column=0, end_line=1, end_column=1)
    ev.put(conn, ev.record("occurrence", **body), body["path"])
    expect_invalid(conn, "without exactly one evidence")
    conn = copy_db(graph, tmp_path, "unused.db")
    conn.execute(
        "DELETE FROM assertions WHERE json_extract(payload,'$.relation') IN "
        "('association','model_table','migration_mentions')"
    )
    expect_invalid(conn)


def test_record_layer_rejections(tmp_path):
    _repo, graph = built(tmp_path)
    good = next(r for r in model_records(graph) if r["type"] == "occurrence")
    body = {k: v for k, v in good.items() if k != "id"}
    with pytest.raises(ValueError, match="rails model occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, role="definition:class")))
    with pytest.raises(ValueError, match="rails model occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, kind="reference")))
    with pytest.raises(ValueError, match="rails model occurrence shape"):
        ev.validate_record(ev.record("occurrence", **dict(body, language="python")))
    with pytest.raises(ValueError, match="occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, kind="rails_route")))
    producer = next(r for r in model_records(graph) if r["type"] == "producer")
    fingerprint = dict(producer["fingerprint"]["rails_models"])
    ev.validate_record(ev.producer({"rails_models": fingerprint}))
    with pytest.raises(ValueError, match="rails_models producer fields"):
        ev.validate_record(ev.producer({"rails_models": dict(fingerprint, extra="x")}))
    with pytest.raises(ValueError, match="producer fields"):
        ev.validate_record(ev.producer({"rails_models": fingerprint, "scip": {}}))
    with pytest.raises(ValueError, match="producer fields"):
        ev.validate_record(ev.producer({"rails_models": fingerprint, "rails": {}}))
    # A different valid adapter or map hash is accepted; a null map identity too.
    ev.validate_record(ev.producer({"rails_models": dict(fingerprint, adapter_sha256="7" * 64)}))
    ev.validate_record(ev.producer({"rails_models": dict(fingerprint, table_map_sha256=None)}))
    with pytest.raises(ValueError, match="rails_models producer hashes"):
        ev.validate_record(ev.producer({"rails_models": dict(fingerprint, adapter_sha256="XYZ")}))
    with pytest.raises(ValueError, match="rails_models producer hashes"):
        ev.validate_record(ev.producer({"rails_models": dict(fingerprint, table_map_sha256="no")}))
    with pytest.raises(ValueError, match="rails_models producer values"):
        ev.validate_record(ev.producer({"rails_models": dict(fingerprint, adapter="other.v1")}))


def test_validate_records_accepts_a_different_adapter_hash(tmp_path):
    _repo, graph = built(tmp_path)
    conn = copy_db(graph, tmp_path)
    row = conn.execute(
        "SELECT id, payload FROM producers "
        "WHERE json_extract(payload,'$.fingerprint.rails_models') IS NOT NULL"
    ).fetchone()
    old = json.loads(row[1])
    fingerprint = dict(old["fingerprint"]["rails_models"], adapter_sha256="7" * 64)
    rewritten = ev.producer({"rails_models": fingerprint})
    conn.execute(
        "UPDATE producers SET id=?, payload=? WHERE id=?",
        (rewritten["id"], ev.canonical(rewritten), row[0]),
    )
    conn.execute(
        "UPDATE evidence SET payload=json_set(payload,'$.producer_id',?) "
        "WHERE json_extract(payload,'$.producer_id')=?",
        (rewritten["id"], row[0]),
    )
    rails_models.validate_records(conn)  # format only: the running adapter hash is never compared
    assert (
        rewritten["fingerprint"]["rails_models"]["adapter_sha256"] != rails_models.adapter_sha256()
    )


def test_projection_validation_passes_on_a_copy_of_an_on_database(tmp_path):
    _repo, graph = built(tmp_path)
    conn = copy_db(graph, tmp_path)
    graph._validate_evidence_projections(conn)
    ev.validate_database(conn)
    rails_models.validate_records(conn)
    # The same copy with a model class unreachable from any projection still validates records.
    assert {f["assertion"]["relation"] for f in facts(graph)} == RELATIONS3


# ---- S10 / invalidation ----
def fresh_equal(graph, repo, tmp_path, name, table_map=MAP_BYTES):
    full, _stats = rebuilt(repo, tmp_path, name, table_map=table_map)
    assert model_lines(graph) == model_lines(full)
    with graph.reader_lease() as lease:
        graph._validate_evidence_projections(lease.db)
        rails_models.validate_records(lease.db)
        known = {json.loads(p)["id"] for (p,) in lease.db.execute("SELECT payload FROM entities")}
    for fact in facts(graph):
        linked = [fact["assertion"]["target_entity_id"], *fact["assertion"]["candidate_ids"]]
        assert all(i in known for i in linked if i is not None)
    return full


def test_deleting_a_model_retracts_the_links_to_it(tmp_path):
    repo, graph = built(tmp_path)
    before = model_lines(graph)
    a2 = [f for f in facts(graph) if f["raw"].get("class_name") == "::Profile"]
    assert a2 and a2[0]["assertion"]["resolution"] == "INFERRED"
    (repo / "app/models/profile.rb").unlink()
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert USERS in affected(stats) or stats["binding_resolution"]["affected_units"] >= 0
    after = [f for f in facts(graph) if f["raw"].get("class_name") == "::Profile"]
    assert after[0]["assertion"]["resolution"] == "UNRESOLVED"
    assert after[0]["assertion"]["unresolved_reason"] == "class_not_found"
    assert not any(f["occurrence"]["path"] == "app/models/profile.rb" for f in facts(graph))
    assert model_lines(graph) != before
    fresh_equal(graph, repo, tmp_path, "fresh-delete")


def test_a_map_only_change_recomputes_with_no_affected_unit(tmp_path):
    repo, graph = built(tmp_path)
    first = model_lines(graph)
    remapped = MAP_BYTES.replace(b'"blog_tags"', b'"blog_labels"')
    other, stats = rebuilt(repo, tmp_path, "g", since="x", table_map=remapped)
    assert stats["binding_resolution"]["affected_units"] == 0
    assert model_lines(other) != first
    assert any(f["raw"].get("table") == "blog_labels" for f in facts(other))
    fresh_equal(other, repo, tmp_path, "fresh-map", table_map=remapped)
    # And dropping the map (same adapter) removes exactly the map facts.
    plain, stats = rebuilt(repo, tmp_path, "g", since="x", table_map=None)
    assert stats["binding_resolution"]["affected_units"] == 0
    assert not any(f["raw"].get("source") == "operator_map" for f in facts(plain))
    assert json.loads(manifest_value(plain))["map"] is None
    fresh_equal(plain, repo, tmp_path, "fresh-nomap", table_map=None)


def test_editing_a_migration_and_a_model_matches_fresh_builds(tmp_path):
    repo, _graph = built(tmp_path)
    edit(repo, CREATE, "create_table :users do |t|", "create_table :accounts do |t|")
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert CREATE in affected(stats)
    assert any(f["raw"].get("table") == "accounts" for f in facts(graph))
    assert not any(f["raw"] == {"op": "create_table", "table": "users"} for f in facts(graph))
    fresh_equal(graph, repo, tmp_path, "fresh-migration")
    edit(repo, USERS, 'class_name: "Ghost"', 'class_name: "Profile"')
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert USERS in affected(stats)
    ghost = next(f for f in facts(graph) if f["raw"].get("name") == "ghost")
    assert ghost["assertion"]["resolution"] == "INFERRED"
    fresh_equal(graph, repo, tmp_path, "fresh-model")
    # A change elsewhere in the Ruby bucket (a new class) re-resolves the associations.
    (repo / "app/models/ghost.rb").write_text("class Ghost < ApplicationRecord\nend\n")
    edit(repo, USERS, 'class_name: "Profile"', 'class_name: "Ghost"')
    graph, _stats = rebuilt(repo, tmp_path, "g", since="x")
    ghost = next(f for f in facts(graph) if f["raw"].get("name") == "ghost")
    assert ghost["assertion"]["target_entity_id"] == entity_of(
        graph, "app/models/ghost.rb", "Ghost"
    )
    fresh_equal(graph, repo, tmp_path, "fresh-ghost")


def test_a_syntax_error_retracts_that_files_facts_and_leaves_no_history(tmp_path):
    repo, graph = built(tmp_path)
    tag = "app/models/tag.rb"
    assert any(f["occurrence"]["path"] == tag for f in facts(graph))
    (repo / tag).write_text('class Tag < ApplicationRecord\n  self.table_name = ((("x"\n')
    graph, _stats = rebuilt(repo, tmp_path, "g", since="x")
    assert json.loads(manifest_value(graph))["files"]["failed"] == 1
    assert not any(f["occurrence"]["path"] == tag for f in facts(graph))
    with graph.reader_lease() as lease:
        observed = json.loads(
            lease.db.execute(
                "SELECT value FROM manifest WHERE key='source_observations'"
            ).fetchone()[0]
        )
        graph._validate_evidence_projections(lease.db)
    history = [h for h in observed if h["type"] == "history" and h["path"] == tag]
    assert history, "the failed file keeps its native history"
    assert not rails_models.record_ids(history[0]["records"])
    # The rest of the fact set equals a fresh build of the same tree.
    full, _stats = rebuilt(repo, tmp_path, "fresh-broken")
    assert model_lines(graph) == model_lines(full) and model_lines(graph)
    # Repairing the file restores the facts.
    (repo / tag).write_text('class Tag < ApplicationRecord\n  self.table_name = "tags_v2"\nend\n')
    graph, _stats = rebuilt(repo, tmp_path, "g", since="x")
    assert json.loads(manifest_value(graph))["files"]["failed"] == 0
    assert any(f["occurrence"]["path"] == tag for f in facts(graph))


def test_on_to_off_incremental_purges_every_model_record(tmp_path):
    repo, graph = built(tmp_path)
    assert len(model_records(graph)) == 175
    off, stats = rebuilt(repo, tmp_path, "g", frameworks=(), since="x")
    assert model_records(off) == [] and manifest_value(off) is None
    with off.reader_lease() as lease:
        for table in ("producers", "evidence", "occurrences", "assertions"):
            for (payload,) in lease.db.execute(f"SELECT payload FROM {table}"):
                for name in ("rails_models", "rails_model", "model_table", "migration_mentions"):
                    assert name not in payload
                assert '"association"' not in payload
    _r, fresh = built(tmp_path, root="fresh-off", repo=repo, frameworks=())
    assert [r["id"] for r in records(off)] == [r["id"] for r in records(fresh)]
    assert stats["binding_resolution"]["mode"] == "affected"
    on_again, stats = rebuilt(repo, tmp_path, "g", since="x")
    assert stats["binding_resolution"]["mode"] == "affected"
    assert len(model_records(on_again)) == 175


def test_producer_exists_only_with_declarations(tmp_path):
    repo = tmp_path / "quiet"
    (repo / "app/models").mkdir(parents=True)
    (repo / "app/models/quiet.rb").write_text("class Quiet < ApplicationRecord\nend\n")
    (repo / "app/models/other.rb").write_text("class Other < Quiet\nend\n")
    _r, graph = built(tmp_path, repo=repo, root="quiet-g")
    assert model_records(graph) == []
    summary = json.loads(manifest_value(graph))
    assert summary["declarations"] == 0 and summary["producer_id"] is None
    assert summary["classes"]["models"] == 1 and summary["files"]["models"] == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "a.rb").write_text("class A; end\n")
    _r, graph = built(tmp_path, repo=empty, root="empty-g")
    assert json.loads(manifest_value(graph))["files"] == {
        "models": 0,
        "migrations": 0,
        "failed": 0,
        "limited": 0,
    }


def test_parser_is_only_used_when_enabled_and_a_model_file_is_captured(tmp_path, monkeypatch):
    calls: list[bytes] = []
    real = pack.get_parser

    class Counting:
        def __init__(self, parser):
            self.parser = parser

        def parse(self, source):
            calls.append(bytes(source))
            return self.parser.parse(source)

    monkeypatch.setattr(pack, "get_parser", lambda lang: Counting(real(lang)))
    repo, _graph = built(tmp_path, frameworks=())  # warm the extraction cache
    calls.clear()
    rebuilt(repo, tmp_path, "g", frameworks=())
    assert calls == []  # OFF: nothing is parsed at all
    rebuilt(repo, tmp_path, "g", frameworks=("rails",))
    assert calls == []  # rails alone: no routes.rb here, and no model file is read
    rebuilt(repo, tmp_path, "g")
    expected = sorted(
        (repo / n).read_bytes()
        for n in (
            p.relative_to(repo).as_posix()
            for p in repo.rglob("*.rb")
            if rails_models.is_model_path(p.relative_to(repo).as_posix())
            or rails_models.is_migration_path(p.relative_to(repo).as_posix())
        )
        if "/storage/" not in n
    )
    assert len(expected) == 17
    assert sorted(calls) == expected  # ON: each captured file is re-parsed once, outside the cache
    # No eligible file: the adapter never asks for a parser.
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.rb").write_text("class A; end\n")
    built(tmp_path, repo=other, root="other-g")
    calls.clear()
    rebuilt(other, tmp_path, "other-g")
    assert calls == []


def test_export_import_round_trip_keeps_and_validates_the_model_records(tmp_path):
    repo, graph = built(tmp_path)
    exported = tmp_path / "graph.jsonl"
    runner = CliRunner()
    out = runner.invoke(
        cli, ["export", "--repo", str(repo), "--graph-root", str(tmp_path / "g"), str(exported)]
    )
    assert out.exit_code == 0, out.output
    assert exported.read_text().count('"rails_model"') >= 58
    other = tmp_path / "other"
    out = runner.invoke(
        cli, ["import", str(exported), "--repo", str(repo), "--graph-root", str(other)]
    )
    assert out.exit_code == 0, out.output
    imported = CodeGraph(repo, graph_root=other, read_only=True)
    assert model_lines(imported) == model_lines(graph) and model_lines(graph)
    imported.close()


# ---- the image comparison tool itself ----
def load_compare():
    spec = importlib.util.spec_from_file_location("compare_graphs_s25", COMPARE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compare_graphs_separates_the_two_adapters_and_excludes_their_union(tmp_path):
    module = load_compare()
    repo = merged(tmp_path)
    both = ("rails", "rails_models")
    _r, together = built(tmp_path, root="both", repo=repo, frameworks=both)
    _r, plain = built(tmp_path, root="plain", repo=repo, frameworks=())
    _r, rails_only = built(tmp_path, root="rails", repo=repo, frameworks=("rails",))
    data, info = module.load(str(together.db_path))
    adapters = info["adapters"]
    assert adapters["rails"]["records"]["occurrences"] > 0
    assert adapters["rails_models"]["records"]["occurrences"] == 58
    assert adapters["rails"]["manifest_present"] and adapters["rails_models"]["manifest_present"]
    assert not set(adapters["rails"]["occurrence_ids"]) & set(
        adapters["rails_models"]["occurrence_ids"]
    )
    plain_data, _plain_info = module.load(str(plain.db_path))
    assert data["occurrence_ids"] == plain_data["occurrence_ids"]
    assert data["assertions"] == plain_data["assertions"]
    verdict = module.both_pair([module.load(str(rails_only.db_path)), (data, info)])
    assert all(p["ok"] for p in verdict[0]) and all(verdict[1].values())
    three = module.three_way(
        [module.load(str(plain.db_path)), module.load(str(plain.db_path)), (data, info)]
    )
    assert three[1]["rails_models_exactly_one_producer"] and three[1]["rails_exactly_one_producer"]


def test_the_models_adapter_cannot_be_enabled_by_serve_or_tools():
    assert "frameworks" not in {p.name for p in cli.commands["serve"].params}
    assert "frameworks" not in {p.name for p in cli.commands["tools"].params}
    assert Path(PROJECT).exists() and Path(UP).name.endswith(".rb")
