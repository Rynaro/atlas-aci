"""ACI-S33 acceptance tests: bounded git co-change correlations with commit evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_s33_support import (
    FX,
    World,
    build_indexed,
    build_world,
    cli_args,
    cli_run,
    delete_loose_object,
    expected_records,
    git,
    git_text,
    index,
    inventory,
    normalized,
    oracle,
    overlay_bytes,
    provision_grammar,
    records_of,
    run,
    sentinel_output,
    shallow_clone,
    tree_sha,
)

from atlas_aci.__main__ import cli
from atlas_aci.history import cochange as lib
from atlas_aci.storage import evidence as ev

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
GOLDEN = json.loads((FX / "expected-records.json").read_text())
HEX = "0123456789abcdef"


@pytest.fixture(scope="session")
def grammar():
    return provision_grammar()


pytestmark = pytest.mark.usefixtures("grammar")


@pytest.fixture(scope="module")
def main(tmp_path_factory) -> World:
    return build_indexed("main", tmp_path_factory.mktemp("s33-main"))


@pytest.fixture(scope="module")
def future(tmp_path_factory) -> World:
    return build_indexed("future", tmp_path_factory.mktemp("s33-future"))


def golden_pairs(world: World) -> dict[tuple[str, str], tuple[int, list[str]]]:
    return {
        (p["a"], p["b"]): (p["support"], sorted(world.sha(c) for c in p["commits"]))
        for p in GOLDEN["pairs"]
    }


def tools_json() -> bytes:
    result = cli_run("tools", "--repo", Path(__file__).resolve().parents[3])
    assert result.exit_code == 0
    return result.stdout_bytes


def test_cochange_support_matches_commits(main, tmp_path):
    assert main.shas == GOLDEN["commit_ids"]  # the plumbing builder is deterministic
    overlay = run(main)
    assert overlay["coverage"] == {
        "state": "complete",
        "reasons": [],
        "records": "complete",
        "commit_budget": None,
    }
    assert records_of(overlay) == expected_records(main)  # a spec-level oracle, no git parsing
    assert records_of(overlay) == golden_pairs(main)  # hand-derived golden records
    assert len(overlay["records"]) == 9 and overlay["counts"]["pair_observations"] == 13
    assert overlay["counts"] == GOLDEN["counts"]
    assert overlay["counts"] == {**oracle(main)["counts"]}
    assert [r["a"] for r in overlay["records"]] == sorted(r["a"] for r in overlay["records"])
    out = tmp_path / "overlay.json"
    result = cli_run(*cli_args(main, out))
    assert result.exit_code == 0, result.stderr
    assert out.read_bytes() == overlay_bytes(overlay)
    summary = json.loads(result.stdout)
    lib.validate_summary(summary)
    assert summary["records"] == 9 and summary["overlay_id"] == overlay["overlay_id"]


def test_cochange_window_provenance(main):
    overlay = run(main)
    records = overlay["records"]
    assert records, "a provenance test over no records would prove nothing"
    policy = overlay["filter"]["policy"]
    digest = hashlib.sha256(
        ("atlas-history-filter.v1:" + json.dumps(policy, sort_keys=True, separators=(",", ":")))
        .encode("utf-8")
    ).hexdigest()  # fmt: skip
    window = set(git_text(main.repo, "rev-list", main.end, "^" + main.start).split())
    assert len(window) == 14
    for record in records:
        assert record["repository_id"] == "s33-main"
        assert record["start_commit"] == main.sha("C0") and record["end_commit"] == main.sha("C14")
        assert record["filter_digest"] == digest == overlay["filter"]["filter_digest"]
        commits = record["supporting_commits"]
        assert commits == sorted(set(commits)) and len(commits) == record["support"]
        assert set(commits) <= window and all(len(c) == 40 and set(c) <= set(HEX) for c in commits)
        for sha in commits:
            raw = git(main.repo, "-c", "core.quotePath=false", "diff-tree", "-r", "-z",
                      "--name-only", "--no-renames", sha + "^", sha)  # fmt: skip
            names = {item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item}
            assert record["a"] in names and record["b"] in names
    assert overlay["key"] == lib.overlay_key("s33-main", main.start, main.end, digest)
    for record in records:
        expected_id = lib.record_id(
            "s33-main", main.start, main.end, digest, record["a"], record["b"]
        )
        assert record["id"] == expected_id
    assert overlay["overlay_id"] == lib.overlay_identity(overlay)
    lib.validate_overlay(overlay)
    tampers = {
        "repository_id": "other",
        "start_commit": main.sha("C1"),
        "end_commit": main.sha("C13"),
        "filter_digest": "0" * 64,
        "supporting_commits": list(reversed(records[1]["supporting_commits"])),
        "support": 99,
        "id": "0" * 64,
        "evidence_kind": "call",
    }
    for field, value in tampers.items():
        for position in (0, 1):
            copy = json.loads(json.dumps(overlay))
            if field == "supporting_commits" and len(copy["records"][position][field]) < 2:
                continue
            copy["records"][position][field] = value
            with pytest.raises(lib.HistoryRejected) as caught:
                lib.validate_overlay(copy)
            assert caught.value.reason == "invalid_overlay"
    missing = json.loads(json.dumps(overlay))
    del missing["records"][0]["start_commit"]
    with pytest.raises(lib.HistoryRejected):
        lib.validate_overlay(missing)


def test_cochange_excluded_commits_do_not_contribute(main):
    overlay = run(main)
    used = {sha for r in overlay["records"] for sha in r["supporting_commits"]}
    for label in ("M5", "C6", "C7"):
        assert main.sha(label) not in used
    assert main.sha("C3") in used  # merged, but itself a non-merge commit
    named = {path for r in overlay["records"] for path in (r["a"], r["b"])}
    assert not {"uv.lock", "dist/app.js", "web/app.min.js", "vendor/mod"} & named
    assert ("src/a.py", "src/e.py") not in records_of(overlay)
    assert not any(p.startswith(("dist/", "vendor/")) or p.endswith(".min.js") for p in named)
    changed = {
        item.decode()
        for item in git(main.repo, "diff-tree", "-r", "--name-only", "-z", "--no-renames",
                        main.sha("M5") + "^1", main.sha("M5")).split(b"\0")
        if item
    }  # fmt: skip
    assert {"src/a.py", "src/e.py"} <= changed  # the merge DID change a and e
    control = run(main, no_generated_defaults=True, bulk_threshold=1000)
    pairs = records_of(control)
    assert pairs == expected_records(main, threshold=1000, prefixes=(), names=(), suffixes=())
    c6, c7 = main.sha("C6"), main.sha("C7")
    assert sum(c6 in commits for _s, commits in pairs.values()) == 6
    assert sum(c7 in commits for _s, commits in pairs.values()) == 15
    assert main.sha("M5") not in {s for _n, commits in pairs.values() for s in commits}
    assert control["filter"]["filter_digest"] != overlay["filter"]["filter_digest"]
    assert control["counts"]["bulk_commits"] == 0 and control["counts"]["generated_paths"] == 0
    assert control["counts"]["merge_commits"] == 1


def strings(node, skip=("a", "b", "generated", "supporting_commits")):
    pending = [node]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            yield from (k for k in item if isinstance(k, str))
            pending.extend(v for k, v in item.items() if k not in skip)
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            yield item


def test_cochange_is_correlation_only(main, tmp_path):
    before = inventory(main.graphs, main.repo / ".git", main.repo)
    overlay = run(main)
    out = tmp_path / "o.json"
    result = cli_run(*cli_args(main, out))
    assert result.exit_code == 0
    assert inventory(main.graphs, main.repo / ".git", main.repo) == before
    assert overlay["records"]
    assert {r["evidence_kind"] for r in overlay["records"]} == {"co_change_correlation"}
    assert overlay["evidence"]["kind"] == "co_change_correlation"
    assert (
        overlay["evidence"]["dependency_claim"] == overlay["evidence"]["causality_claim"] == "none"
    )
    assert json.loads(result.stdout)["evidence_kind"] == "co_change_correlation"
    words = set(strings(overlay))
    assert "relation" not in words and not words & ev.RELATIONS
    assert (
        frozenset(
            {
                "call",
                "construct",
                "import",
                "superclass",
                "include",
                "extend",
                "prepend",
                "rationale_for",
            }
        )
        == ev.RELATIONS
    )
    raw = tools_json()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert b"co_change" not in raw and b"cochange" not in raw
    assert "cochange" in cli.commands
    bad = json.loads(overlay_bytes(overlay))
    bad["relation"] = "call"
    with pytest.raises(lib.HistoryRejected):
        lib.validate_overlay(bad)
    summary = lib.summary_document(overlay)
    summary["evidence_kind"] = "call"
    with pytest.raises(lib.HistoryRejected):
        lib.validate_summary(summary)
    assert not (main.graphs / "history").exists()


@pytest.fixture(scope="module")
def shallow_world(main, tmp_path_factory) -> World:
    base = tmp_path_factory.mktemp("s33-shallow")
    world = shallow_clone(main, base / "repo", 2)
    index(world, base / "graphs")
    return world


@pytest.fixture(scope="module")
def merge_world(tmp_path_factory) -> World:
    base = tmp_path_factory.mktemp("s33-merge")
    full = build_world("shallow_merge", base / "full")
    world = shallow_clone(full, base / "repo", 2)
    index(world, base / "graphs")
    return world


@pytest.fixture(scope="module")
def damaged(tmp_path_factory) -> tuple[World, World]:
    base = tmp_path_factory.mktemp("s33-damaged")
    first = build_world("main", base / "commit")
    index(first, base / "graphs-commit")
    delete_loose_object(first, first.sha("C3"))
    second = build_world("main", base / "tree")
    index(second, base / "graphs-tree")
    delete_loose_object(second, tree_sha(second, "C8"))
    return first, second


def assert_incomplete(overlay, *reasons, budget=None):
    assert overlay["coverage"] == {
        "state": "incomplete_history",
        "reasons": sorted(reasons),
        "records": "withheld",
        "commit_budget": budget,
    }
    assert overlay["records"] == [] and overlay["counts"] is None
    lib.validate_overlay(overlay)


def test_cochange_missing_history_is_incomplete(
    main, shallow_world, merge_world, damaged, tmp_path
):
    # (a) a shallow clone that does not hold the start commit
    assert_incomplete(run(shallow_world), "start_commit_missing")
    # (b) a shallow boundary inside the window
    assert_incomplete(
        run(merge_world, bulk_threshold=5), "shallow_boundary_in_window"
    )  # X1..X2 lists Y1, whose raw commit still names a parent
    # (c) a missing commit object, (d) a missing tree object
    missing_commit, missing_tree = damaged
    assert_incomplete(run(missing_commit), "object_missing")
    assert_incomplete(run(missing_tree), "object_missing")
    # (e) the commit budget; a truncated listing says nothing else
    for budget in (1, 13):
        assert_incomplete(run(main, max_commits=budget), "commit_budget_exhausted", budget=budget)
    assert_incomplete(
        run(merge_world, max_commits=1), "commit_budget_exhausted", budget=1
    )  # not shallow_boundary_in_window: a truncated listing is never walked further
    # controls
    full = run(main, max_commits=14)
    assert full["coverage"]["state"] == "complete" and len(full["records"]) == 9
    boundary = run(shallow_world, start=main.sha("C13"))
    assert boundary["coverage"]["state"] == "complete" and boundary["records"] == []
    assert (
        boundary["counts"]["commits_in_window"] == 1
        and boundary["counts"]["single_path_commits"] == 1
    )
    unknown = "1" * 40
    with pytest.raises(lib.HistoryRejected) as caught:
        run(main, end=unknown)
    assert caught.value.reason == "commit_not_found"
    with pytest.raises(lib.HistoryRejected) as caught:
        run(main, start=unknown)
    assert caught.value.reason == "commit_not_found"
    # the CLI exits 0, writes the overlay and the summary says why
    out = tmp_path / "incomplete.json"
    result = cli_run(*cli_args(shallow_world, out))
    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["coverage_state"] == "incomplete_history" and summary["records"] == 0
    assert summary["reasons"] == ["start_commit_missing"]
    lib.validate_overlay(json.loads(out.read_bytes()))


def test_cochange_replay_is_idempotent(main, tmp_path_factory, tmp_path, monkeypatch):
    first = overlay_bytes(run(main))
    assert overlay_bytes(run(main)) == first
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    assert cli_run(*cli_args(main, one)).exit_code == 0
    shuffled = [
        "cochange", "--output", str(two), "--snapshot-id", main.snapshot_id,
        "--bulk-threshold", "5", "--source-commit", main.source, "--end", main.end,
        "--repository-id", "s33-main", "--graph-root", str(main.graphs), "--start", main.start,
        "--repo", str(main.repo),
    ]  # fmt: skip
    assert cli_run(*shuffled).exit_code == 0
    assert one.read_bytes() == two.read_bytes() == first
    reordered = tmp_path / "reordered.json"
    extra = []
    for name in ("vendor/", "dist/", "build/", "dist/"):  # defaults, reordered and duplicated
        extra += ["--generated-prefix", name]
    for name in ("uv.lock", "go.sum", "uv.lock"):
        extra += ["--generated-name", name]
    for suffix in (".pyc", ".min.js", ".map", ".min.js"):
        extra += ["--generated-suffix", suffix]
    assert cli_run(*cli_args(main, reordered, *extra)).exit_code == 0
    assert reordered.read_bytes() == first
    # separate interpreters with different hash seeds
    for seed in ("1", "2"):
        target = tmp_path / ("seed" + seed + ".json")
        argv = [sys.executable, "-m", "atlas_aci", *cli_args(main, target)]
        done = subprocess.run(argv, capture_output=True, env={**os.environ, "PYTHONHASHSEED": seed})
        assert done.returncode == 0, done.stderr
        assert target.read_bytes() == first
    # a rebuilt world at another path: only the path-dependent fields differ
    other = build_indexed("main", tmp_path_factory.mktemp("s33-rebuilt"))
    assert other.shas == main.shas
    again = run(other)
    assert normalized(again) == normalized(json.loads(first))
    assert (
        again["source_snapshot"]["snapshot_id"]
        != json.loads(first)["source_snapshot"]["snapshot_id"]
    )
    assert records_of(again) == golden_pairs(other)
    # a hostile environment and a hostile repository
    hostile = build_indexed("main", tmp_path_factory.mktemp("s33-hostile"))
    sentinel = tmp_path / "SENTINEL"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n")
    script.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[diff]\n\trenames = copies\n[core]\n\tfsmonitor = true\n")
    for key, value in (
        ("diff.renames", "copies"), ("core.fsmonitor", str(script)),
        ("diff.external", str(script)), ("diff.ignoreSubmodules", "all"),
    ):  # fmt: skip
        git(hostile.repo, "config", key, value)
    (hostile.repo / ".gitmodules").write_text(
        '[submodule "mod"]\n\tpath = vendor/mod\n\turl = ./x\n\tignore = all\n'
    )
    git(hostile.repo, "replace", hostile.sha("C2"), hostile.sha("C1"))
    (hostile.repo / ".git" / "info").mkdir(exist_ok=True)
    (hostile.repo / ".git" / "info" / "grafts").write_text(hostile.sha("C4") + "\n")
    for key, value in {
        "GIT_DIR": "/nonexistent", "GIT_WORK_TREE": "/nonexistent",
        "GIT_INDEX_FILE": "/nonexistent", "GIT_EXTERNAL_DIFF": str(script),
        "GIT_CONFIG_PARAMETERS": "'diff.renames=copies'", "HOME": str(home),
        "GIT_OBJECT_DIRECTORY": "/nonexistent", "GIT_SHALLOW_FILE": "/nonexistent",
        "GIT_GRAFT_FILE": "/nonexistent", "GIT_NO_REPLACE_OBJECTS": "0",
    }.items():  # fmt: skip
        monkeypatch.setenv(key, value)
    assert normalized(run(hostile)) == normalized(json.loads(first))
    assert not sentinel.exists()
    monkeypatch.undo()
    # after git gc (packs and a commit graph)
    packed = build_indexed("main", tmp_path_factory.mktemp("s33-packed"))
    git(packed.repo, "gc", "-q", "--aggressive")
    git(packed.repo, "commit-graph", "write", "--reachable")
    assert normalized(run(packed)) == normalized(json.loads(first))


def test_cochange_future_window_rejected(future, tmp_path):
    for label in ("F3", "S1"):
        with pytest.raises(lib.HistoryRejected) as caught:
            run(future, end=future.sha(label))
        assert caught.value.reason == "outside_snapshot_history", label
    control = run(future, end=future.sha("F2"))
    assert control["coverage"]["state"] == "complete"
    assert records_of(control) == {
        ("src/x.py", "src/y.py"): (2, sorted([future.sha("F1"), future.sha("F2")]))
    }
    # declaring a later source cannot evade the check: the blobs of the snapshot disagree
    with pytest.raises(lib.HistoryRejected) as caught:
        run(future, source_commit=future.sha("F3"), end=future.sha("F3"))
    assert caught.value.reason == "snapshot_commit_mismatch"
    with pytest.raises(lib.HistoryRejected) as caught:
        run(future, source_commit=future.sha("F3"))
    assert caught.value.reason == "snapshot_commit_mismatch"
    # a later commit that only edits a file the indexer never observes cannot be told apart
    later = run(future, source_commit=future.sha("F2r"))
    assert later["coverage"]["state"] == "complete"
    assert (
        "snapshot_binding_covers_indexed_files_only_files_added_later_are_not_detected"
        in (later["limitations"])
    )
    out, sentinel = sentinel_output(tmp_path)
    result = cli_run(*cli_args(future, out, end=future.sha("F3")))
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: History co-change rejected: outside_snapshot_history\n"
    assert out.read_bytes() == sentinel
    assert [
        p.name for p in tmp_path.iterdir() if p.name.startswith(".atlas-history-overlay-")
    ] == []
