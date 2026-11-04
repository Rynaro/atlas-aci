"""ACI-S22 semantics: matching, sides, languages, limits, parity with impact, the test rule."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

import pytest
from test_s20_support import RUBY_V1, RUBY_V2
from test_s22_support import (
    CALC,
    PKG_V1,
    PKG_V2,
    cov_world,
    lease,
    plan,
    recommendation,
    same_path_pair,
    span_of,
)

from atlas_aci.protocol.graph import VERSION
from atlas_aci.query import impact, validation
from atlas_aci.query import impact_limits as lim
from atlas_aci.query import validation_coverage as cov
from atlas_aci.query import validation_limits as vlim
from atlas_aci.query.diff import Side
from atlas_aci.query.impact_work import ImpactWork


def synthetic(start: int, end: int, line: int = 1) -> cov.Observation:
    return cov.Observation(
        nodeid="t::x",
        test_path="t.py",
        test_entity_id="0" * 64,
        assertion_id="1" * 64,
        evidence_id="2" * 64,
        occurrence_id="3" * 64,
        producer_id="4" * 64,
        run_id="5" * 64,
        line=line,
        start_byte=start,
        end_byte=end,
        phases=("run",),
        source_sha256="6" * 64,
        test_file_sha256=None,
    )


# ---- matching boundaries (byte overlap) --------------------------------------------


def test_matching_boundaries_are_byte_based_and_inclusive_at_both_ends(tmp_path):
    world = cov_world(tmp_path)
    result = plan(world.before, world.after)
    clamp = span_of(result, "clamp")
    with ExitStack() as stack:
        before = lease(stack, world.before)
        after = lease(stack, world.after)
        counters = cov.Counters()
        geometry = cov.Geometry({"before": before, "after": after}, counters)
        ref = next(
            c for c in _changes(before, after) if c["name"] == "clamp" and c["path"] == CALC
        )["before"]
        geo = geometry.of("before", ref)
    assert counters.source_files_read == 1
    # The span starts at the physical line start (the node itself excludes indentation).
    assert geo["start"] == ref["start_byte"] and geo["end"] == ref["end_byte"]
    last = geo["end"]
    assert cov.overlaps(synthetic(geo["start"], geo["start"] + 3), geo)  # first byte
    assert cov.overlaps(synthetic(last - 3, last), geo)  # ends exactly at the last byte
    assert cov.overlaps(synthetic(last - 1, last + 40), geo)  # straddles the end
    assert cov.overlaps(synthetic(geo["start"] - 9, geo["start"] + 1), geo)  # straddles the start
    assert not cov.overlaps(synthetic(last, last + 5), geo)  # starts at the exclusive end
    assert not cov.overlaps(synthetic(geo["start"] - 9, geo["start"]), geo)  # ends at the start
    assert clamp["sides"]["before"]["line"] == 43 and clamp["sides"]["before"]["end_line"] == 48


def _changes(before, after):
    change, _loaded, _work = impact.change_set(before, after, impact.Settings().diff_limits)
    return change["changes"]


def test_hash_mismatch_excludes_every_row_and_after_side_coverage_is_post_change(tmp_path):
    world = cov_world(tmp_path)
    # before = edited build-only, after = original bytes + run a  (the after side holds coverage)
    result = plan(world.after, world.before)
    clamp = span_of(result, "clamp")
    assert clamp["coverage"]["after"] == "executed" and clamp["coverage"]["before"] == (
        "no_coverage_source"
    )
    assert clamp["state"] == "observed_after"
    rec = recommendation(result, "tests/test_basic.py::test_clamp")
    assert rec["observed_in"] == {"after": True} and rec["post_change_validation"] is True
    assert rec["rank"]["basis_order"] == 0
    assert {e["matched_side"] for e in rec["evidence"]} == {"after"}
    assert {e["source"] for e in rec["evidence"]} == {"after"}
    validation.validate_plan(result)
    # Whole-file mismatch: an unrelated edit to the same file leaves nothing matching.
    mismatched = plan(world.before, world.after)
    after_view = span_of(mismatched, "clamp")["coverage"]["after"]
    assert after_view == "no_coverage_source"  # R0's rows never match the edited after side


# ---- span sides, languages and categories --------------------------------------------


def test_removed_and_added_spans_have_one_side_each(tmp_path):
    first = {"mod.py": "def kept():\n    return 1\n\n\ndef gone():\n    return 2\n"}
    second = {"mod.py": "def kept():\n    return 1\n\n\ndef fresh():\n    return 3\n"}
    _repo, before, after = same_path_pair(tmp_path, first, second)
    result = plan(before, after)
    gone, fresh = span_of(result, "gone"), span_of(result, "fresh")
    assert gone["category"] == "removed" and gone["sides"]["after"] is None
    assert gone["sides"]["before"] is not None
    assert gone["coverage"] == {"before": "no_coverage_source", "after": "not_applicable"}
    assert fresh["category"] == "added" and fresh["sides"]["before"] is None
    assert fresh["coverage"] == {"before": "not_applicable", "after": "no_coverage_source"}
    assert {s["name"] for s in result["spans"]} == {"gone", "fresh"}  # `kept` is no span
    assert {g["name"] for g in result["gaps"]} == {"gone", "fresh"}
    validation.validate_plan(result)


def test_unknown_one_sided_change_is_not_applicable_on_the_missing_side(tmp_path):
    first = {"m.py": "def a():\n    return 1\n"}
    second = {"m.py": "def a(:\n    return 1\n"}  # extraction fails after
    _repo, before, after = same_path_pair(tmp_path, first, second)
    result = plan(before, after)
    span = span_of(result, "a")
    assert span["category"] == "unknown" and span["sides"]["after"] is None
    assert span["coverage"] == {"before": "no_coverage_source", "after": "not_applicable"}
    assert span["state"] == "gap"
    assert result["completeness"]["reasons"] == {"partial_extraction": True}
    validation.validate_plan(result)


def test_format_and_location_changes_are_not_spans(tmp_path):
    first = {"mod.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n"}
    second = {"mod.py": "def a():\n    return 1\n\n\n\n\ndef b():\n    return 2\n"}
    _repo, before, after = same_path_pair(tmp_path, first, second)
    result = plan(before, after)
    assert result["spans"] == [] and result["spans_total"] == 0 and result["gaps"] == []


def test_ruby_change_is_unsupported_language_never_covered(tmp_path):
    _repo, before, after = same_path_pair(tmp_path, RUBY_V1, RUBY_V2)
    result = plan(before, after)
    span = next(s for s in result["spans"] if s["language"] == "ruby")
    assert span["coverage"] == {"before": "unsupported_language", "after": "unsupported_language"}
    assert span["state"] == "gap" and span["structural"] in {"unsupported_language", "incomplete"}
    gap = next(g for g in result["gaps"] if g["change_record_id"] == span["change_record_id"])
    assert "unsupported_language" in gap["reasons"]
    assert not result["recommendations"]


def test_header_end_line_follows_the_def_of_a_decorated_function(tmp_path):
    world = cov_world(tmp_path)
    source = world.original.decode().replace("    return a + b\n", "    return a + b + 0\n")
    assert source != world.original.decode()
    after = world.build_root("root-add", source.encode())
    result = plan(world.before, Side(world.repo, after))
    add = span_of(result, "add")
    side = add["sides"]["before"]
    assert side["line"] in {18, 19} and side["end_line"] == 20
    assert side["header_end_line"] == 19  # the `def` line, not the decorator at 18
    assert [s["name"] for s in result["spans"]] == ["add"]


def test_lone_carriage_return_files_use_physical_lines_for_the_header(tmp_path):
    # A lone CR inside a comment: tree-sitter counts `\n` only, coverage.py's physical lines
    # also split on `\r`, so the two numberings disagree about where `def b` is.
    text = "# one\rtwo\ndef b():\n    return 2\n"
    first, second = {"cr.py": text}, {"cr.py": text.replace("2", "3")}
    _repo, before, after = same_path_pair(tmp_path, first, second)
    result = plan(before, after)
    span = span_of(result, "b")
    side = span["sides"]["before"]
    assert side["line"] == 2  # tree-sitter numbering (display only)
    assert side["header_end_line"] == 3  # physical-line numbering from the same bytes
    validation.validate_plan(result)


# ---- limits and completeness ----------------------------------------------------------


def test_span_limit_and_seed_limit_make_the_plan_incomplete(tmp_path):
    world = cov_world(tmp_path)
    full = plan(world.before, world.after)
    assert full["completeness"] == {"state": "complete", "reasons": {}}
    limited = plan(world.before, world.after, {"max_spans": 1})
    assert limited["spans_total"] == 3 and len(limited["spans"]) == 1
    assert limited["selection"]["spans"] is True
    assert limited["completeness"]["reasons"] == {"span_limit": True}
    seeds = plan(world.before, world.after, {"max_seeds": 1})
    assert seeds["completeness"]["reasons"] == {"seed_limit": True}
    states = {s["name"]: s["structural"] for s in seeds["spans"]}
    assert "incomplete" in states.values()  # an unexpanded seed is never reported as `none`
    assert seeds["impact"]["seeds_expanded"] == 1 and seeds["impact"]["seeds_total"] == 3
    for result in (limited, seeds):
        validation.validate_plan(result)


def test_probe_row_and_observation_limits_are_counter_bounded(tmp_path):
    world = cov_world(tmp_path)
    probes = plan(world.before, world.after, {"max_reference_probes": 1})
    assert probes["completeness"]["reasons"] == {"reference_probe_limit": True}
    assert probes["work"]["reference_probes"] == 1
    assert any(s["structural"] == "incomplete" for s in probes["spans"])
    rows = plan(world.before, world.after, {"max_reference_rows": 1})
    assert rows["completeness"]["reasons"] == {"reference_row_limit": True}
    assert rows["work"]["reference_rows_read"] <= 1
    zero = plan(world.before, world.after, {"max_observation_rows": 0})
    assert zero["completeness"]["reasons"] == {"observation_limit": True}
    assert zero["work"]["observation_rows_read"] == 0
    assert not [r for r in zero["recommendations"] if r["basis"] == "observed_execution"]
    assert {s["coverage"]["before"] for s in zero["spans"]} == {"observation_limit"}
    assert all(s["state"] != "observed_before_only" for s in zero["spans"])
    capped = plan(world.before, world.after, {"max_observation_rows": 40})
    assert capped["completeness"]["reasons"].get("observation_limit") is True
    assert capped["work"]["observation_rows_read"] <= 40
    for result in (probes, rows, zero, capped):
        validation.validate_plan(result)


def test_observed_and_structural_identities_are_not_merged_but_linked(tmp_path):
    world = cov_world(tmp_path)
    result = plan(world.before, world.after)
    structural = [r for r in result["recommendations"] if r["basis"] == "structural"]
    assert structural
    linked = {r["test"]["name"]: r["also_observed"] for r in structural}
    assert linked["test_clamp"] is True and linked["test_clamp_param"] is True
    observed = [r for r in result["recommendations"] if r["basis"] != "structural"]
    assert observed and all(r["also_observed"] is False for r in observed)
    keys = {(r["basis"], r["test"]["entity_id"]) for r in result["recommendations"]}
    assert len(keys) == len(result["recommendations"])
    plain = world.build_root("root-plain", world.original)
    bare = plan(Side(world.repo, plain), world.after)
    assert not any(r["also_observed"] for r in bare["recommendations"])


# ---- parity with impact -----------------------------------------------------------------

PRIVATE = (
    "atlas_aci.query.impact.change_set/select_seeds/propagate/normalize/open_side, "
    "atlas_aci.query.diff._open/_lease/_check_compatible, "
    "lease attributes db/atlas_dir/snapshot_id/generation_id/read_source"
)


def components(before, after):
    """Exactly the composition `validation.collect` performs (impact.compute is never used)."""

    async def go():
        with ExitStack() as stack:
            held = [lease(stack, before), lease(stack, after)]
            change, loaded, _work = impact.change_set(
                held[0], held[1], impact.Settings().diff_limits
            )
            request = impact.normalize({"schema_version": VERSION, "op": "impact"})
            work = ImpactWork(**{n: request[n] for n in lim.IMPACT_LIMITS})
            ids = {r["entity_id"] for r in change["changes"]}
            eligible = impact.select_seeds(change["changes"], loaded[1])
            impacted, rows, _limited = await impact.propagate(
                {"before": held[0], "after": held[1]}, eligible, request, work, ids
            )
            return change["id"], impacted, rows

    return asyncio.run(go())


@pytest.mark.parametrize("name", ["cov", "pkg", "ruby"])
def test_impact_parity_on_every_fixture_pair(tmp_path, name):
    if name == "cov":
        world = cov_world(tmp_path)
        before, after = world.before, world.after
    else:
        files = {"pkg": (PKG_V1, PKG_V2), "ruby": (RUBY_V1, RUBY_V2)}[name]
        _repo, before, after = same_path_pair(tmp_path, *files)
    envelope = impact.run(before, after)["data"]
    result = plan(before, after)
    change_id, impacted, rows = components(before, after)
    hint = "; drift in: " + PRIVATE
    assert result["change_set_id"] == change_id == envelope["change_set_id"], hint
    assert result["impact"]["impacted_total"] == len(impacted) == envelope["impacted_total"], hint
    assert result["impact"]["seeds_total"] == envelope["seeds_total"], hint
    assert [(i["entity"]["id"], i["snapshot_side"], i["distance"]) for i in impacted] == [
        (i["entity"]["id"], i["snapshot_side"], i["distance"]) for i in envelope["impacted"]
    ], hint
    assert [f["commitment"] for f in result["impact"]["frontiers"]] == [
        (s["frontier"] or {}).get("commitment") for s in envelope["seeds"]
    ], hint
    assert result["work"]["examined_edges"] == envelope["work"]["examined_edges"], hint
    assert result["work"]["visited_nodes"] == envelope["work"]["visited_nodes"], hint
    assert len(rows) == len(result["impact"]["frontiers"])


def test_complete_diff_to_impact_to_test_path(tmp_path):
    (tmp_path / "w").mkdir()
    _repo, before, after = same_path_pair(tmp_path / "w", PKG_V1, PKG_V2)
    envelope = impact.run(before, after)["data"]
    item = next(i for i in envelope["impacted"] if i["entity"]["name"] == "test_rect_area")
    result = plan(before, after)
    rec = recommendation(result, "test_rect_area")
    evidence = next(e for e in rec["evidence"] if e["kind"] == "reverse_path")
    assert evidence["distance"] == item["distance"] == 2
    assert [h["assertion_id"] for h in evidence["hops"]] == [
        h["assertion"]["id"] for h in item["path"]["hops"]
    ]
    assert evidence["change_record_id"] == item["change_record_id"]
    seed = next(s for s in result["spans"] if s["name"] == "area")
    assert seed["category"] == "signature" and seed["structural"] == "found"
    assert seed["recommendations"] == [rec["recommendation_id"]]


# ---- the closed test rule and local helpers --------------------------------------------


def entity(name="test_x", kind="function", path="tests/test_a.py", owners=(), language="python"):
    return {"name": name, "kind": kind, "path": path, "owners": list(owners), "language": language}


def klass(name):
    return {"kind": "class", "name": name, "ordinal": 0}


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (entity(), True),
        (entity(kind="method"), True),
        (entity(name="testing"), True),
        (entity(name="helper"), False),
        (entity(name="Test_x"), False),
        (entity(kind="class", name="TestThing"), False),
        (entity(path="tests/helpers.py"), False),
        (entity(path="pkg/a_test.py"), True),
        (entity(path="pkg/atest.py"), False),
        (entity(path="test_a.py"), True),
        (entity(path="tests/test_a.txt"), False),
        (entity(language="ruby"), False),
        (entity(owners=[klass("TestThing")]), True),
        (entity(owners=[klass("Helper")]), False),
        (entity(owners=[klass("TestOuter"), klass("Inner")]), False),
        (entity(owners=[klass("Outer"), klass("TestInner")]), True),
        (entity(owners=[{"kind": "module", "name": None, "ordinal": 0}]), True),
    ],
)
def test_the_pytest_default_convention_rule_table(record, expected):
    assert vlim.is_test_entity(record) is expected


def test_selector_hint_and_parameter_stripping():
    record = entity(owners=[{"kind": "module", "name": None}, klass("TestA"), klass("TestB")])
    assert vlim.selector_hint(record) == "tests/test_a.py::TestA::TestB::test_x"
    assert vlim.strip_parameters("t.py::test_x[a-b]") == "t.py::test_x"
    assert vlim.strip_parameters("t.py::test_x[a[0]]") == "t.py::test_x"
    assert vlim.strip_parameters("t.py::test_x") == "t.py::test_x"


@pytest.mark.parametrize(
    "relations",
    [
        ["call"],
        ["construct"],
        ["call", "construct"],
        ["include", "extend"],
        list(lim.DEFAULT_RELATIONS),
        list(lim.RELATIONS),
    ],
)
def test_local_probe_relations_equal_the_impact_helper(relations):
    assert vlim.probe_relations(relations) == impact._probe_relations(relations), PRIVATE


# ---- external coverage sources (typed, closed rejections) ------------------------------------


def test_a_source_with_the_same_generation_as_a_side_is_a_duplicate(tmp_path):
    world = cov_world(tmp_path)
    same = world.build_root("root-c-a", world.original, runs=("a",))
    with ExitStack() as stack:
        # Generation ids are content hashes: same bytes + same run => the same generation.
        assert lease(stack, Side(world.repo, same)).generation_id == (
            lease(stack, world.before).generation_id
        )
    with pytest.raises(validation.SourceRejected) as caught:
        plan(world.before, world.after, coverage_roots=[same])
    assert caught.value.code == "VALIDATION_SOURCE_REJECTED"
    assert caught.value.reason == "duplicate_source"
    assert str(tmp_path) not in str(caught.value)
    # The same root named twice is the same namespace; two distinct roots are admitted.
    other = world.build_root("root-c-b", world.original, runs=("b",))
    third = world.build_root("root-c-ab", world.original, runs=("a", "b"))
    with pytest.raises(validation.SourceRejected) as twice:
        plan(world.before, world.after, coverage_roots=[other, other])
    assert twice.value.reason == "same_namespace"
    with pytest.raises(validation.SourceRejected) as limit:
        plan(world.before, world.after, {"max_coverage_sources": 1}, coverage_roots=[other, third])
    assert limit.value.reason == "source_limit"
    both = plan(world.before, world.after, coverage_roots=[other, third])
    assert [s["source"] for s in both["coverage_sources"]] == [
        "before",
        "after",
        "external",
        "external",
    ]
    ids = [s["generation_id"] for s in both["coverage_sources"] if s["source"] == "external"]
    assert ids == sorted(ids)  # externals are ordered by generation id, never by flag order
    for rejected in (caught.value.reason, twice.value.reason, limit.value.reason):
        assert rejected in vlim.SOURCE_REJECTIONS
