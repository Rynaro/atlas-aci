"""ACI-S30 resolution states, details, evidence guards and impact budgets."""

from __future__ import annotations

import json
import sqlite3

import pytest
from test_s30_support import (
    PACKAGE_MEMBERS,
    file_entity,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    make_package,
    make_world,
    outcome,
)

from atlas_aci.cross_repo import package_links as pl
from atlas_aci.cross_repo import revision_vector as rv
from atlas_aci.overlays import runtime

pytestmark = pytest.mark.usefixtures("grammar")

L_AREA = "from s30_shapes.geometry import area, Circle"
L_MISSING = "from s30_shapes.geometry import missing_name"
FORM = "unresolved_unsupported_reference_form"


def reference(**fields):
    base = {
        "path": "app/main.py",
        "line": 1,
        "column": 0,
        "byte_start": 0,
        "owner_entity_id": "a" * 64,
        "occurrence_id": "b" * 64,
        "evidence_id": "c" * 64,
        "assertion_id": "d" * 64,
        "scope": "python:app/main.py:root",
        "level": 0,
        "module": ("s30_shapes", "geometry"),
        "name": "area",
        "local_name": "area",
        "reason": "unsupported_absolute_import",
    }
    base.update(fields)
    return pl.Reference(**base)


@pytest.fixture(scope="module")
def pure():
    """A package index and a bound binding for the pure resolver."""
    package = pl.index_snapshot(json.loads(make_package().snapshot))
    bound = {"state": "bound", "detail": None}
    return package, bound


def facts(*rows, rebound=False):
    return lambda path, name: pl.ProviderFacts(tuple(rows), rebound)


def row(kind="function", unconditional=True):
    return pl.SymbolRow(kind, 10, 11, "e" * 64, unconditional)


# ---- the pure resolver ---------------------------------------------------------------------


def test_a_declaration_the_provider_did_not_index_is_a_typed_state(pure):
    package, bound = pure
    ref = reference()
    got = pl.resolve_reference(ref, package, bound, "src/", False, facts())
    assert (got.resolution, got.detail) == ("unresolved_provider_declaration_not_indexed", None)
    # The same state when the one indexed symbol has another kind than the declaration.
    mismatched = pl.resolve_reference(ref, package, bound, "src/", False, facts(row("class")))
    assert mismatched.resolution == "unresolved_provider_declaration_not_indexed"
    linked = pl.resolve_reference(ref, package, bound, "src/", False, facts(row("function")))
    assert linked.resolution == "linked_declaration" and linked.path == "src/s30_shapes/geometry.py"
    assert linked.row is not None and linked.declaration is not None
    asked = []

    def lookup(path, name):
        asked.append((path, name))
        return pl.ProviderFacts((row(),), False)

    pl.resolve_reference(ref, package, bound, "vendor/lib/", False, lookup)
    assert asked == [("vendor/lib/s30_shapes/geometry.py", "area")]


def test_provider_rows_that_are_conditional_repeated_or_rebound_are_ambiguous(pure):
    package, bound = pure
    ref = reference()
    for rows, expected in (
        ((row(unconditional=False),), "provider_symbols"),
        ((row(), row()), "provider_symbols"),
        ((row(), row(unconditional=False)), "provider_symbols"),
    ):
        got = pl.resolve_reference(ref, package, bound, "src/", False, facts(*rows))
        assert (got.resolution, got.detail) == (
            "unresolved_ambiguous_provider_symbol",
            expected,
        )
    rebound = pl.resolve_reference(ref, package, bound, "src/", False, facts(row(), rebound=True))
    assert (rebound.resolution, rebound.detail) == (
        "unresolved_ambiguous_provider_symbol",
        "provider_name_rebound",
    )


def test_the_resolver_precedence_is_fixed(pure):
    package, _ = pure
    ref = reference()
    mismatch = {"state": "version_mismatch", "detail": "source_digest_differs"}
    gone = {"state": "provider_snapshot_unavailable", "detail": "index_unavailable"}
    never = facts(row())
    assert pl.resolve_reference(ref, package, mismatch, "src/", False, never).detail == (
        "source_digest_differs"
    )
    assert pl.resolve_reference(ref, package, gone, "src/", False, None).resolution == (
        "unresolved_provider_snapshot_unavailable"
    )
    # The shadow guard comes before the binding, the form before the shadow.
    shadowed = pl.resolve_reference(ref, package, mismatch, "src/", True, never)
    assert shadowed.resolution == "unresolved_local_module_shadow"
    nested = reference(scope="python:app/main.py:function_definition:1:9")
    first = pl.resolve_reference(nested, package, mismatch, "src/", True, never)
    assert (first.resolution, first.detail) == (FORM, "nested_scope_import")


def test_form_classification_separates_names_that_are_not_identifiers():
    cases = {
        ("s30_shapes", "geometry"): None,
        ("s30_shapes", "géometry"): "non_ascii_identifier",
        ("s30_shapes", "# note"): "non_identifier_name",
        ("s30_shapes", "\\\n"): "non_identifier_name",
        ("s30_shapes", "1abc"): "non_identifier_name",
        ("s30_shapes", "a b"): "non_identifier_name",
    }
    for (module, name), detail in cases.items():
        got = pl.classify_form(reference(module=(module,), name=name))
        assert got == ("from_import", detail), (name, got)
    assert pl.classify_form(reference(module=(), name="s30_shapes.geometry")) == (
        "module_import",
        "module_import",
    )
    assert pl.classify_form(reference(name="*")) == ("wildcard_import", "wildcard_import")


def test_non_identifier_text_is_capped_and_always_encodable():
    long = "#" + "x" * 500
    assert len(pl._display(long)) == pl.TEXT_CAP
    assert pl._display("s30_shapes") == "s30_shapes"
    lone = "a\ud800b"  # a lone surrogate cannot be written as UTF-8
    assert pl._display(lone).encode("utf-8") and "ud800" in pl._display(lone)
    huge_identifier = "a" * 900
    assert pl._display(huge_identifier) == huge_identifier


# ---- end to end ----------------------------------------------------------------------------


def test_a_local_module_shadow_of_the_package_root_is_detected(tmp_path):
    world = make_world(tmp_path, consumer_edit=lambda f: f.update({"s30_shapes.py": "x = 1\n"}))
    built = world.link()
    shadowed = [
        i for i in built["references"] if i["resolution"] == "unresolved_local_module_shadow"
    ]
    assert len(shadowed) == 16 and all(i["detail"] is None for i in shadowed)
    assert outcome(built, L_AREA, "area")[0] == "unresolved_local_module_shadow"
    assert built["links"] == [] and built["package"]["binding"]["state"] == "bound"
    # Forms that cannot be linked anyway keep their own more specific state.
    assert outcome(built, "from s30_shapes.geometry import *", "*")[0] == FORM


@pytest.mark.parametrize(
    "path",
    ["pkg/s30_shapes/__init__.py", "s30_shapes/__init__.py", "vendor/s30_shapes.py"],
)
def test_the_shadow_covers_a_package_directory_and_nested_paths(tmp_path, path):
    world = make_world(tmp_path, consumer_edit=lambda f: f.update({path: "x = 1\n"}))
    assert outcome(world.link(), L_AREA, "area")[0] == "unresolved_local_module_shadow"


def test_a_shadow_file_that_failed_to_index_still_shadows(tmp_path):
    broken = "def broken(:\n"
    world = make_world(tmp_path, consumer_edit=lambda f: f.update({"s30_shapes.py": broken}))
    assert outcome(world.link(), L_AREA, "area")[0] == "unresolved_local_module_shadow"
    states = _observation_states(world)
    assert states["s30_shapes.py"] != "indexed"


def _observation_states(world):
    from contextlib import ExitStack

    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.consumer)
        value = reader.db.execute(pl.OBSERVATIONS_SQL).fetchone()[0]
    return {i["path"]: i["state"] for i in json.loads(value) if i["type"] == "observation"}


def test_a_provider_that_is_not_python_importable_member_is_missing(tmp_path):
    world = make_world(tmp_path, provider_edit=lambda f: f.pop("src/s30_shapes/geometry.py"))
    built = world.link()
    assert built["package"]["binding"]["detail"] == "source_member_missing"
    assert built["package"]["binding"]["members_missing"] == 1 and built["links"] == []


@pytest.mark.parametrize("component", ["log", "tmp", "build", "dist", "coverage", "node_modules"])
def test_skip_pattern_directories_make_members_missing(tmp_path, component):
    prefix = "src/" + component + "/"
    world = make_world(tmp_path)
    mapping = world.mapping(prefix=prefix)
    built = world.link(mapping_raw=mapping)
    binding = built["package"]["binding"]
    assert binding["detail"] == "source_member_missing" and binding["members_missing"] == 6
    assert binding["members_compared"] == 6 and built["links"] == []
    assert outcome(built, L_AREA, "area") == (
        "unresolved_version_mismatch",
        "source_member_missing",
    )


def test_a_multi_part_skip_pattern_never_matches(tmp_path):
    world = make_world(
        tmp_path,
        provider_edit=lambda f: f.update(
            {"public/assets/" + k: v for k, v in PACKAGE_MEMBERS.items()}
        ),
    )
    built = world.link(mapping_raw=world.mapping(prefix="public/assets/"))
    assert built["package"]["binding"]["state"] == "bound" and len(built["links"]) == 7


# ---- the evidence guards -------------------------------------------------------------------


def test_a_lease_of_an_old_schema_epoch_is_refused_on_both_sides(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    real = runtime.open_active
    epochs = {"app": 9, "lib": 9}

    def old(stack, side):
        reader = real(stack, side)
        reader._lease_schema_epoch = epochs[side.repo.name]
        return reader

    monkeypatch.setattr(runtime, "open_active", old)
    assert world.link()["links"]
    epochs["lib"] = 8
    gone = world.link()
    assert gone["provider"]["detail"] == "index_upgrade_required" and gone["links"] == []
    assert gone["provider"]["supplied_snapshot_id"] is None
    epochs["app"] = 8
    with pytest.raises(rv.CrossRepoRejected) as consumer:
        world.link()
    assert consumer.value.reason == "consumer_index_upgrade_required"
    epochs.update({"app": 9, "lib": 9})
    built = world.link()
    epochs["app"] = 8
    with pytest.raises(rv.CrossRepoRejected) as stale:
        world.impact(built, file_entity("app/main.py"), "to_provider")
    assert stale.value.reason == "consumer_index_upgrade_required"
    epochs.update({"app": 9, "lib": 8})
    doc = world.impact(built, file_entity("app/main.py"), "to_provider")
    assert {b["detail"] for b in doc["boundaries"]} == {"index_upgrade_required"} and not doc[
        "results"
    ]


@pytest.mark.parametrize("side,table,reason", [
    ("app", "import_bindings", "invalid_consumer_evidence"),
    ("lib", "symbols", "invalid_provider_evidence"),
    ("lib", "assignment_targets", "invalid_provider_evidence"),
    ("app", "manifest", "invalid_consumer_evidence"),
    ("lib", "manifest", "invalid_provider_evidence"),
])  # fmt: skip
def test_a_database_error_is_invalid_evidence_never_an_uncaught_error(
    tmp_path, monkeypatch, side, table, reason
):
    world = make_world(tmp_path)
    real = runtime.open_active

    def denying(stack, where):
        reader = real(stack, where)
        if where.repo.name == side:

            def authorizer(action, first, second, database, source):
                if action == sqlite3.SQLITE_READ and first == table:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            reader.db.set_authorizer(authorizer)
        return reader

    monkeypatch.setattr(runtime, "open_active", denying)
    with pytest.raises(rv.CrossRepoRejected) as caught:
        world.link()
    assert caught.value.reason == reason


def test_the_reference_count_is_bounded(tmp_path):
    world = make_world(tmp_path)
    total = world.link()["counts"]["references"]
    assert total == 22
    exact = world.link(limits=pl.normalize_limits({"max_references": total}))
    assert exact["counts"]["references"] == total
    with pytest.raises(rv.CrossRepoRejected) as caught:
        world.link(limits=pl.normalize_limits({"max_references": total - 1}))
    assert caught.value.reason == "reference_limit_exceeded"


# ---- budgets -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_world(tmp_path_factory):
    world = make_world(tmp_path_factory.mktemp("budgets"))
    return world, world.link()


def test_budget_defaults_and_ranges_are_pinned():
    assert pl.BUDGETS == {
        "max_results": (1000, 1, 100000),
        "max_links_examined": (100000, 1, 1000000),
        "max_boundaries": (1000, 1, 100000),
    }
    assert pl.normalize_budgets() == pl.Budgets(1000, 100000, 1000)
    for name, (_, low, high) in pl.BUDGETS.items():
        assert getattr(pl.normalize_budgets({name: low}), name) == low
        assert getattr(pl.normalize_budgets({name: high}), name) == high
        for bad in (0, high + 1, True, "1", 1.5, -1):
            with pytest.raises(rv.CrossRepoUsageError) as caught:
                pl.normalize_budgets({name: bad})
            assert caught.value.what == name
    with pytest.raises(rv.CrossRepoUsageError):
        pl.normalize_budgets({"max_depth": 2})


@pytest.mark.parametrize("name", ["max_results", "max_links_examined", "max_boundaries"])
def test_each_exhausted_budget_is_a_boundary_and_the_result_incomplete(built_world, name):
    world, built = built_world
    seed = file_entity("app/main.py")
    budgets = pl.normalize_budgets({name: 1})
    doc = world.impact(built, seed, "to_provider", budgets=budgets)
    pl.validate_impact(doc)
    assert doc["complete"] is False
    exhausted = [b for b in doc["boundaries"] if b["kind"] == "budget_exhausted"]
    assert [(b["detail"], b["at"]) for b in exhausted] == [(name, "seed")]
    assert doc["budgets"][name] == 1
    if name == "max_results":
        assert len(doc["results"]) == 1
    if name == "max_links_examined":
        assert doc["work"]["records_examined"] == 1
    if name == "max_boundaries":
        assert len([b for b in doc["boundaries"] if b["kind"] != "budget_exhausted"]) == 1
    again = world.impact(built, seed, "to_provider", budgets=budgets)
    assert pl.canonical_bytes(again) == pl.canonical_bytes(doc)  # deterministic truncation by id


def test_the_first_examined_references_are_the_lowest_ids(built_world):
    world, built = built_world
    seed = file_entity("app/main.py")
    doc = world.impact(
        built, seed, "to_provider", budgets=pl.normalize_budgets({"max_links_examined": 3})
    )
    owned = [i for i in built["references"] if i["consumer"]["owner_entity_id"] == seed]
    first_three = sorted(owned, key=lambda item: item["id"])[:3]
    seen = {r["reference_id"] for r in doc["results"]} | {
        b["reference_id"] for b in doc["boundaries"] if b["reference_id"]
    }
    assert seen == {i["id"] for i in first_three} and doc["work"]["records_examined"] == 3


def test_a_provider_entity_with_no_links_is_an_empty_complete_result(built_world):
    world, built = built_world
    hidden = _entity(world, "_hidden_helper")
    doc = world.impact(built, hidden, "from_provider")
    assert doc["results"] == [] and doc["boundaries"] == [] and doc["complete"] is True


def _entity(world, name):
    from contextlib import ExitStack

    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.provider)
        return reader.db.execute(
            "SELECT entity_id FROM symbols WHERE path='src/s30_shapes/geometry.py' AND name=?",
            (name,),
        ).fetchone()[0]


def test_an_impact_request_with_a_bad_direction_or_seed_is_usage(built_world):
    world, built = built_world
    seed = file_entity("app/main.py")
    for direction, bad_seed, what in (
        ("sideways", seed, "direction"),
        ("to_provider", "x", "seed"),
    ):
        with pytest.raises(rv.CrossRepoUsageError) as caught:
            world.impact(built, bad_seed, direction)
        assert caught.value.what == what
