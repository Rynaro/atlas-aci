"""ACI-S22 acceptance contract: one test per product criterion, real builds and imports.

Every test is a sync function: `validation.run` owns its event loop (`asyncio.run`).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest
from test_s22_support import (
    CALC,
    LABELS,
    PKG_V1,
    PKG_V2,
    World,
    build,
    cli_bytes,
    cov_world,
    coverage_ids,
    edited,
    inventory,
    lease,
    plan,
    recommendation,
    run_id,
    same_path_pair,
    sha256,
    span_of,
    with_plan_id,
)

from atlas_aci.query import validation
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev


def observed_recs(result):
    return [r for r in result["recommendations"] if r["basis"] == "observed_execution"]


def structural_recs(result):
    return [r for r in result["recommendations"] if r["basis"] == "structural"]


def mapped(result, record_id, *, basis):
    return {
        r["test"]["name"]
        for r in result["recommendations"]
        if r["basis"] == basis and any(e["change_record_id"] == record_id for e in r["evidence"])
    }


# ---- AC-S22-001 ----------------------------------------------------------------


def test_observed_test_suggestion(tmp_path):
    world = cov_world(tmp_path)
    result = plan(world.before, world.after)
    assert cli_bytes(world.before, world.after) == validation.canonical_bytes(result)
    validation.validate_plan(result)

    clamp = span_of(result, "clamp")
    truth = {
        row["nodeid"]
        for row in LABELS["a"]["rows"]
        if row["path"] == CALC and 43 <= row["line"] <= 48
    }
    assert truth == {
        "tests/test_basic.py::test_clamp",
        "tests/test_params.py::test_clamp_param[0]",
        "tests/test_params.py::test_clamp_param[5]",
    }
    assert mapped(result, clamp["change_record_id"], basis="observed_execution") == truth

    rec = recommendation(result, "tests/test_basic.py::test_clamp")
    assert rec["basis"] == "observed_execution" and rec["method"] == "coverage_context"
    assert rec["observed_in"] == {"before": True}
    assert rec["post_change_validation"] is False
    assert rec["test"]["selector"] == {
        "kind": "pytest_nodeid",
        "value": "tests/test_basic.py::test_clamp",
    }
    entries = [e for e in rec["evidence"] if e["change_record_id"] == clamp["change_record_id"]]
    assert len(entries) == 1
    entry = entries[0]
    with ExitStack() as stack:
        r0 = lease(stack, world.before)
        r1 = lease(stack, world.after)
        assert entry["matched_side"] == "before"
        assert entry["matched_snapshot_id"] == r0.snapshot_id != r1.snapshot_id
        assert entry["matched_generation_id"] == r0.generation_id
        assert entry["coverage_generation_id"] == r0.generation_id
        assert entry["coverage_snapshot_id"] == r0.snapshot_id
        assert entry["source"] == "before"
        assert entry["run_id"] == run_id("a")
        assertion = ev.get(r0.db, "assertion", entry["assertion_id"])
        proof = ev.get(r0.db, "evidence", entry["evidence_id"])
        occurrence = ev.get(r0.db, "occurrence", entry["occurrence_id"])
        producer = ev.get(r0.db, "producer", entry["producer_id"])
        assert assertion["relation"] == "test_executes"
        assert assertion["evidence_id"] == proof["id"]
        assert proof["occurrence_id"] == occurrence["id"]
        assert proof["producer_id"] == producer["id"]
        assert producer["fingerprint"]["coverage"]["run_id"] == run_id("a")
        assert ev.get(r0.db, "entity", assertion["source_entity_id"])["name"] == rec["test"]["name"]
        before_ref = clamp["sides"]["before"]
        assert (
            occurrence["source_sha256"]
            == before_ref["source_sha256"]
            == sha256(world.original)
            != clamp["sides"]["after"]["source_sha256"]
        )
        assert before_ref["line"] <= occurrence["line"] <= before_ref["end_line"]
        assert occurrence["path"] == CALC and occurrence["line"] == entry["line"]
    assert entry["scope"] == "body" and entry["matched_lines_total"] >= 1
    assert clamp["state"] == "observed_before_only"
    assert clamp["coverage"] == {"before": "executed", "after": "no_coverage_source"}


# ---- AC-S22-002 ----------------------------------------------------------------


def test_static_test_label(tmp_path):
    # (i) PKG with no coverage at all: a reverse path of length 2 ends at the test.
    (tmp_path / "pkg").mkdir()
    _repo, before, after = same_path_pair(tmp_path / "pkg", PKG_V1, PKG_V2)
    result = plan(before, after)
    rec = recommendation(result, "test_rect_area")
    assert rec["basis"] == "structural" and rec["method"] == "reverse_path"
    assert rec["rank"]["distance"] == 2 and rec["rank"]["basis_order"] == 3
    reverse = [e for e in rec["evidence"] if e["kind"] == "reverse_path"]
    assert reverse and reverse[0]["distance"] == 2 and len(reverse[0]["hops"]) == 2
    assert rec["test"]["selector"] == {
        "kind": "pytest_selector_hint",
        "value": "pkg/test_shapes.py::test_rect_area",
    }
    with ExitStack() as stack:
        held = lease(stack, after)
        for hop in reverse[0]["hops"]:
            assert ev.get(held.db, "assertion", hop["assertion_id"])["relation"] == "call"
            assert ev.get(held.db, "evidence", hop["evidence_id"])["id"] == hop["evidence_id"]
            assert ev.get(held.db, "occurrence", hop["occurrence_id"])["id"]
            assert ev.get(held.db, "producer", hop["producer_id"])["id"]
    assert not [r for r in result["recommendations"] if r["test"]["name"] == "rect_area"]
    assert result["recommendations"] and all(
        r["basis"] == "structural" for r in result["recommendations"]
    )

    # (ii) COV edits on a build-only pair: same-name references from test files.
    world = cov_world(tmp_path / "cov")
    plain = world.build_root("root-plain", world.original)
    result = plan(Side(world.repo, plain), world.after)
    for name in ("test_clamp", "test_clamp_param"):
        rec = recommendation(result, name)
        assert rec["basis"] == "structural" and rec["method"] == "same_name_reference"
        assert rec["rank"]["basis_order"] == 4 and rec["observed_in"] == {}
    assert not observed_recs(result)

    # (iii) coverage present: structural records stay structural, in the serialized bytes.
    raw = cli_bytes(world.before, world.after)
    emitted = json.loads(raw)
    found = [r for r in emitted["recommendations"] if r["basis"] == "structural"]
    assert found and {r["test"]["name"] for r in found} >= {"test_clamp", "test_clamp_param"}
    for rec in found:
        assert rec["observed_in"] == {} and rec["post_change_validation"] is False
        for entry in rec["evidence"]:
            assert entry["kind"] in {"reverse_path", "same_name_reference"}
            assert "run_id" not in entry
            assert not [k for k in entry if k.startswith("coverage_")]
    # Semantic rejection (plan_id recomputed so only the semantic rules can object).
    relabelled = json.loads(raw)
    target = next(r for r in relabelled["recommendations"] if r["basis"] == "structural")
    target["basis"] = "observed_execution"
    with pytest.raises(ValueError, match="Invalid validation plan"):
        validation.validate_plan(with_plan_id(relabelled))
    static = json.loads(raw)
    next(r for r in static["recommendations"] if r["basis"] == "structural")["basis"] = "static"
    with pytest.raises(ValueError, match="Invalid validation plan"):
        validation.validate_plan(with_plan_id(static))
    validation.validate_plan(emitted)


# ---- AC-S22-003 ----------------------------------------------------------------


def test_uncovered_change(tmp_path):
    world = cov_world(tmp_path / "cov")
    result = plan(world.before, world.after)
    never = span_of(result, "never_called")
    gaps = {g["name"]: g for g in result["gaps"]}
    assert set(gaps) == {"never_called"} and result["gaps_total"] == 1
    gap = gaps["never_called"]
    assert gap["change_record_id"] == never["change_record_id"]
    assert gap["basis"] == "unsupported" and gap["action"] == "inspect"
    assert gap["coverage"] == {"before": "no_observation_in_span", "after": "no_coverage_source"}
    assert gap["structural"] == "none"
    assert set(gap["reasons"]) >= {
        "no_observation_in_span",
        "no_coverage_source",
        "no_structural_test",
    }
    assert "clamp" not in gaps and "total" not in gaps
    assert never["recommendations"] == []

    # A build-only pair with an unreferenced added function: a gap on its (after) side.
    first = {"mod.py": "def kept():\n    return 1\n"}
    second = {
        "mod.py": "def kept():\n    return 1\n\n\ndef brand_new():\n    return 3\n\n\n"
        "def brand_newer():\n    return 4\n"
    }
    (tmp_path / "pair").mkdir()
    _repo, before, after = same_path_pair(tmp_path / "pair", first, second)
    result = plan(before, after)
    assert {g["name"] for g in result["gaps"]} == {"brand_new", "brand_newer"}
    for gap in result["gaps"]:
        assert gap["lines"]["before"] is None and gap["lines"]["after"][0] >= 1
        assert gap["coverage"] == {"before": "not_applicable", "after": "no_coverage_source"}
        assert "no_coverage_source" in gap["reasons"]
    capped = plan(before, after, {"max_gaps": 1})
    assert capped["gaps_total"] == result["gaps_total"] == 2
    assert len(capped["gaps"]) == 1 and capped["selection"]["gaps"] is True
    assert result["selection"]["gaps"] is False
    via_cli = json.loads(cli_bytes(before, after, max_gaps=1))
    assert via_cli["gaps_total"] == 2 and via_cli["selection"]["gaps"] is True


# ---- AC-S22-004 ----------------------------------------------------------------


def install_spy(monkeypatch):
    import _posixsubprocess
    import pty

    launches: list[str] = []

    def spy_for(name):
        def spy(*args, **kwargs):
            launches.append(name)
            raise RuntimeError("process launch blocked: " + name)

        return spy

    targets = [
        (subprocess, "Popen"),
        (_posixsubprocess, "fork_exec"),
        (pty, "fork"),
        (asyncio, "create_subprocess_exec"),
        (asyncio, "create_subprocess_shell"),
    ]
    for name in ("fork", "forkpty", "system", "popen", "posix_spawn", "posix_spawnp"):
        targets.append((os, name))
    for name in dir(os):
        if name.startswith(("exec", "spawn")) and callable(getattr(os, name)):
            targets.append((os, name))
    for module, name in targets:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, spy_for(module.__name__ + "." + name))
    if hasattr(sys.modules.get("posix"), "fork"):
        monkeypatch.setattr(sys.modules["posix"], "fork", spy_for("posix.fork"))
    return launches


def test_no_execution(tmp_path, monkeypatch):
    world = cov_world(tmp_path)
    plain = world.build_root("root-plain", world.original)
    external = world.build_root("root-c-ab", world.original, runs=("a", "b"))
    roots = [world.r0, world.r1, plain, external]
    before_state = inventory(world.repo, *roots)
    modules_before = set(sys.modules)
    marker = world.repo / "tests" / "SENTINEL"
    assert (world.repo / "tests/test_sentinel.py").is_file() and not marker.exists()

    # Everything that launches a process (builds, git) ran above; the spy goes in now.
    launches = install_spy(monkeypatch)
    assert subprocess.Popen.__name__ == "spy" and launches == []
    with pytest.raises(RuntimeError, match="launch blocked"):
        subprocess.run(["true"], check=False)
    assert launches == ["subprocess.Popen"]  # liveness control: the spy really sees launches
    launches.clear()

    library = plan(Side(world.repo, plain), world.after, coverage_roots=[external])
    via_cli = cli_bytes(Side(world.repo, plain), world.after, [external])
    assert library["recommendations"] and observed_recs(library)
    assert via_cli == validation.canonical_bytes(library)
    # Own-coverage plan too (no externals), then the spy must still be silent.
    own = plan(world.before, world.after)
    assert own["recommendations"] and cli_bytes(world.before, world.after)
    assert launches == []

    assert not marker.exists()
    assert not [
        m
        for m in set(sys.modules) - modules_before
        if m.split(".")[0] in {"app", "tests", "test_sentinel"}
    ]
    assert inventory(world.repo, *roots) == before_state


# ---- AC-S22-005 ----------------------------------------------------------------


def test_stale_coverage(tmp_path):
    world = cov_world(tmp_path)
    stale = world.build_root("root-c", world.original, runs=("a",))
    version_two = world.build_root("root-v2", edited(world.original, 2))
    before = world.after  # edit v1: matches neither the stale root's bytes nor v2's
    after = Side(world.repo, version_two)
    result = plan(before, after, coverage_roots=[stale])
    validation.validate_plan(result)

    held = [s for s in result["coverage_sources"] if s["source"] == "external"]
    assert len(held) == 1
    source = held[0]
    file = {f["path"]: f for f in source["files"]}[CALC]
    assert file["classification"] == "stale"
    assert result["work"]["stale_observation_rows"] > 0
    assert file["observation_rows"] == result["work"]["stale_observation_rows"]

    # Non-vacuous: the stale root really holds observed rows inside clamp's lines.
    with ExitStack() as stack:
        held_stale = lease(stack, Side(world.repo, stale))
        in_clamp = [
            json.loads(row[0])
            for row in held_stale.db.execute(
                "SELECT payload FROM occurrences WHERE json_extract(payload,'$.kind')='execution' "
                "AND path=?",
                (CALC,),
            )
        ]
    assert [o for o in in_clamp if 44 <= o["line"] <= 47]

    assert not observed_recs(result)
    assert not [
        e
        for r in result["recommendations"]
        for e in r["evidence"]
        if e["kind"] == "observed_execution"
    ]
    ids = coverage_ids(stale, world.repo)
    assert ids
    cited = json.dumps([result["recommendations"], result["spans"], result["gaps"]])
    assert source["generation_id"] not in cited and source["snapshot_id"] not in cited
    assert not [i for i in ids if i in cited]
    clamp = span_of(result, "clamp")
    assert clamp["coverage"] == {"before": "stale_only", "after": "stale_only"}
    # Same-name structural discovery still finds tests for `clamp`, so it is not a gap here...
    assert clamp["state"] == "structural_only" and clamp["structural"] == "found"
    # ...but with structural discovery switched off the stale coverage cannot rescue it.
    bare = plan(before, after, {"max_reference_probes": 0}, coverage_roots=[stale])
    gap = next(g for g in bare["gaps"] if g["name"] == "clamp")
    assert "stale_coverage_only" in gap["reasons"]
    assert gap["coverage"] == {"before": "stale_only", "after": "stale_only"}
    assert not observed_recs(bare)

    # Positive control: the same root, with a before side holding the ORIGINAL bytes.
    original_only = world.build_root("root-original", world.original)
    result = plan(Side(world.repo, original_only), world.after, coverage_roots=[stale])
    validation.validate_plan(result)
    source = next(s for s in result["coverage_sources"] if s["source"] == "external")
    assert {f["path"]: f for f in source["files"]}[CALC]["classification"] == "matches_before"
    external = [
        e
        for r in observed_recs(result)
        for e in r["evidence"]
        if e["source"] == "external" and e["matched_side"] == "before"
    ]
    assert external and result["work"]["stale_observation_rows"] == 0
    assert {e["coverage_generation_id"] for e in external} == {source["generation_id"]}


# ---- AC-S22-006 ----------------------------------------------------------------


def ordered(result):
    return [r["recommendation_id"] for r in result["recommendations"]]


def build_cov_roots(world: World, suffix: str):
    return {
        "before": world.build_root("bo" + suffix, world.original),
        "c_a": world.build_root("ca" + suffix, world.original, runs=("a",)),
        "c_ab": world.build_root("cab" + suffix, world.original, runs=("a", "b")),
        "after": world.build_root("af" + suffix, world.current),
    }


def build_pkg_roots(repo: Path, tmp: Path, suffix: str):
    from test_s22_support import write_tree

    out = {}
    for name, files in (
        ("before", PKG_V1),
        ("ext1", {**PKG_V1, "pkg/extra_one.py": "def one():\n    return 1\n"}),
        ("ext2", {**PKG_V1, "pkg/extra_two.py": "def two():\n    return 2\n"}),
        ("after", PKG_V2),
    ):
        for stale in ("pkg/extra_one.py", "pkg/extra_two.py"):
            (repo / stale).unlink(missing_ok=True)
        write_tree(repo, files)
        build(repo, tmp / (name + suffix))
        out[name] = tmp / (name + suffix)
    return out


def test_validation_replay(tmp_path):
    world = cov_world(tmp_path / "cov")
    roots = build_cov_roots(world, "-1")
    sides = (Side(world.repo, roots["before"]), Side(world.repo, roots["after"]))
    externals = [roots["c_a"], roots["c_ab"]]
    first = plan(*sides, coverage_roots=externals)
    second = plan(*sides, coverage_roots=externals)
    via_cli = cli_bytes(*sides, externals)
    assert first["recommendations"] and observed_recs(first)
    canonical = validation.canonical_bytes(first)
    assert canonical == validation.canonical_bytes(second) == via_cli
    assert ordered(first) == ordered(second) == ordered(json.loads(via_cli))
    assert first["plan_id"] == second["plan_id"] == json.loads(via_cli)["plan_id"]
    frontiers = first["impact"]["frontiers"]
    assert frontiers and frontiers == second["impact"]["frontiers"]
    assert all(f["stop_reason"] is None and f["commitment"] for f in frontiers)
    sources = {s["source"] for s in first["coverage_sources"]}
    assert sources == {"before", "after", "external"}
    assert len([s for s in first["coverage_sources"] if s["source"] == "external"]) == 2
    validation.validate_plan(first)

    # Fresh roots, rebuilt and re-imported from scratch at the same repository path.
    again = build_cov_roots(world, "-2")
    for name in roots:
        with ExitStack() as stack:
            one = lease(stack, Side(world.repo, roots[name]))
            two = lease(stack, Side(world.repo, again[name]))
            assert one.generation_id == two.generation_id and one.snapshot_id == two.snapshot_id
    replay = plan(
        Side(world.repo, again["before"]),
        Side(world.repo, again["after"]),
        coverage_roots=[again["c_a"], again["c_ab"]],
    )
    assert validation.canonical_bytes(replay) == canonical

    # Externals in reversed flag order: identical bytes.
    assert cli_bytes(*sides, list(reversed(externals))) == via_cli
    assert validation.canonical_bytes(plan(*sides, coverage_roots=externals[::-1])) == canonical

    # A cap yields a prefix of the same order; a different propagation limit is another plan.
    capped = plan(*sides, {"max_recommendations": 1}, coverage_roots=externals)
    assert ordered(capped) == ordered(first)[:1]
    assert capped["recommendations_total"] == first["recommendations_total"]
    assert capped["selection"]["recommendations"] is True
    shallow = plan(*sides, {"max_depth": 1}, coverage_roots=externals)
    assert shallow["plan_id"] != first["plan_id"]
    validation.validate_plan(capped)
    validation.validate_plan(shallow)

    # PKG with two external (build-only) sources.
    pkg = tmp_path / "pkgwork"
    pkg.mkdir()
    repo = pkg / "repo"
    repo.mkdir()
    from test_s22_support import init_git_repo

    init_git_repo(repo)
    one = build_pkg_roots(repo, pkg, "-1")
    pkg_sides = (Side(repo, one["before"]), Side(repo, one["after"]))
    pkg_ext = [one["ext1"], one["ext2"]]
    a = plan(*pkg_sides, coverage_roots=pkg_ext)
    b = plan(*pkg_sides, coverage_roots=pkg_ext)
    assert recommendation(a, "test_rect_area")["basis"] == "structural"
    assert validation.canonical_bytes(a) == validation.canonical_bytes(b)
    assert cli_bytes(*pkg_sides, pkg_ext) == validation.canonical_bytes(a)
    assert cli_bytes(*pkg_sides, pkg_ext[::-1]) == validation.canonical_bytes(a)
    two = build_pkg_roots(repo, pkg, "-2")
    c = plan(
        Side(repo, two["before"]),
        Side(repo, two["after"]),
        coverage_roots=[two["ext1"], two["ext2"]],
    )
    assert validation.canonical_bytes(c) == validation.canonical_bytes(a)
    validation.validate_plan(a)
