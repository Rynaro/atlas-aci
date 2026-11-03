"""ACI-S20 operator CLI: option resolution, canonical bytes, rejection and usage exits."""

from __future__ import annotations

import json

from click.testing import CliRunner
from test_s20_support import (
    V1,
    V2,
    Served,
    build,
    git,
    init_git,
    inventory,
    same_path_pair,
    write_tree,
)

from atlas_aci.__main__ import cli
from atlas_aci.query import impact as change_impact
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev


def run(*args):
    return CliRunner().invoke(cli, ["impact", *args])


def roots(before, after):
    return [
        "--before-graph-root",
        str(before.graph_root),
        "--after-graph-root",
        str(after.graph_root),
    ]


def test_canonical_stdout_equals_the_library_envelope(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    result = run("--repo", str(repo), *roots(before, after))
    assert result.exit_code == 0, result.output
    expected = change_impact.run(before, after)
    assert result.stdout_bytes == change_impact.canonical_bytes(expected)
    assert result.stdout_bytes.endswith(b"\n") and result.stdout_bytes.count(b"\n") == 1
    envelope = json.loads(result.stdout_bytes)
    assert envelope["operation"] == "impact" and envelope["data"]["impacted"]
    assert ev.canonical(envelope).encode() + b"\n" == result.stdout_bytes
    # The same server request returns the same envelope (one library function).
    served = Served(tmp_path, repo, before, after)
    try:
        assert served.call({}).structuredContent == envelope
    finally:
        served.close()


def test_before_and_after_repo_override_under_one_graph_root(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    init_git(primary)
    write_tree(primary, V1)
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "fixture")
    linked = tmp_path / "linked"
    git(primary, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
    try:
        write_tree(linked, V2)
        shared = tmp_path / "shared"
        build(primary, shared)
        build(linked, shared)
        result = run(
            "--graph-root", str(shared), "--before-repo", str(primary), "--after-repo", str(linked)
        )
        assert result.exit_code == 0, result.output
        expected = change_impact.run(Side(primary, shared), Side(linked, shared))
        assert result.stdout_bytes == change_impact.canonical_bytes(expected)
        assert json.loads(result.stdout_bytes)["data"]["impacted"]
        mixed = run(
            "--repo", str(primary), "--after-repo", str(linked), "--graph-root", str(shared)
        )
        assert mixed.exit_code == 0 and mixed.stdout_bytes == result.stdout_bytes
    finally:
        git(primary, "worktree", "remove", "--force", str(linked))


def test_propagation_options_match_the_server_request(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    served = Served(tmp_path, repo, before, after)
    try:
        default = json.loads(run("--repo", str(repo), *roots(before, after)).stdout_bytes)
        narrow = run("--repo", str(repo), *roots(before, after), "--relations", "call")
        assert narrow.exit_code == 0, narrow.output
        narrowed = json.loads(narrow.stdout_bytes)
        assert narrowed["data"]["impact_id"] != default["data"]["impact_id"]
        assert narrowed == served.call({"relations": ["call"]}).structuredContent
        shallow = run("--repo", str(repo), *roots(before, after), "--max-depth", "1")
        assert json.loads(shallow.stdout_bytes) == served.call({"max_depth": 1}).structuredContent
        many = run(
            "--repo",
            str(repo),
            *roots(before, after),
            "--relations",
            "call,import",
            "--confidence",
            "EXTRACTED,INFERRED",
            "--max-seeds",
            "2",
            "--max-visited-nodes",
            "50",
            "--max-examined-edges",
            "100",
            "--max-boundary-probes",
            "10",
            "--max-vm-operations",
            "100000",
            "--deadline-ms",
            "2000",
            "--max-bytes",
            "200000",
        )
        assert many.exit_code == 0, many.output
        data = json.loads(many.stdout_bytes)["data"]
        assert data["effective_limits"]["max_seeds"] == 2 and len(data["seeds"]) == 2
        assert data["coverage"]["reasons"]["seed_limit"] is True
        assert set(data["propagation"]["relations"]) == {"call", "import"}
        # Diff-phase operator limits.
        loose = run("--repo", str(repo), *roots(before, after), "--max-changed-files", "5")
        assert json.loads(loose.stdout_bytes)["data"]["effective_limits"]["max_changed_files"] == 5
    finally:
        served.close()


def test_rejections_exit_one_with_a_fixed_message_and_empty_stdout(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    watched = [repo, before.graph_root, after.graph_root]
    state = inventory(*watched)
    other = tmp_path / "other"
    other.mkdir()
    init_git(other)
    write_tree(other, V1)
    build(other, tmp_path / "other-root")
    cases = {
        "SNAPSHOT_DIFF_REJECTED: index_unavailable": run(
            "--repo",
            str(repo),
            "--before-graph-root",
            str(before.graph_root),
            "--after-graph-root",
            str(tmp_path / "never-built"),
        ),
        "SNAPSHOT_DIFF_REJECTED: repository_identity_mismatch": run(
            "--before-repo",
            str(other),
            "--after-repo",
            str(repo),
            "--before-graph-root",
            str(tmp_path / "other-root"),
            "--after-graph-root",
            str(after.graph_root),
        ),
        "SNAPSHOT_DIFF_REJECTED: same_namespace": run(
            "--repo", str(repo), "--graph-root", str(before.graph_root)
        ),
        "IMPACT_DIFF_TOO_LARGE: changed_file_limit": run(
            "--repo", str(repo), *roots(before, after), "--max-changed-files", "0"
        ),
        "IMPACT_DIFF_TOO_LARGE: diff_entity_limit": run(
            "--repo", str(repo), *roots(before, after), "--max-diff-entities", "1"
        ),
        "IMPACT_DIFF_TOO_LARGE: diff_occurrence_limit": run(
            "--repo", str(repo), *roots(before, after), "--max-diff-occurrences", "1"
        ),
    }
    for message, result in cases.items():
        assert result.exit_code == 1 and result.stdout == "", (message, result.output)
        assert "Impact rejected: " + message in result.output
        assert str(tmp_path) not in result.output
        assert "Traceback" not in result.output
    assert not (tmp_path / "never-built").exists()
    assert inventory(*watched) == state


def test_usage_errors_exit_two_with_empty_stdout(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    watched = [repo, before.graph_root, after.graph_root]
    state = inventory(*watched)
    side = roots(before, after)
    attempts = [
        run(*side),  # no repository at all
        run("--before-repo", str(repo), *side),  # one side without a repository
        run("--repo", str(tmp_path / "does-not-exist"), *side),
        run(
            "--repo",
            str(repo),
            "--before-graph-root",
            str(repo / "inside"),
            "--after-graph-root",
            str(after.graph_root),
        ),
        run("--repo", str(repo), *side, "--max-depth", "0"),
        run("--repo", str(repo), *side, "--relations", "rationale_for"),
        run("--repo", str(repo), *side, "--confidence", "AMBIGUOUS"),
        run("--repo", str(repo), *side, "--max-bytes", "-1"),
        run("--repo", str(repo), *side, "--deadline-ms", "2001"),
    ]
    for result in attempts:
        assert result.exit_code == 2 and result.stdout == "", result.output
        assert "Traceback" not in result.output
    assert inventory(*watched) == state


def test_budget_too_small_is_a_usage_error_not_a_partial_result(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    result = run("--repo", str(repo), *roots(before, after), "--max-bytes", "100")
    assert result.exit_code == 2 and result.stdout == ""
    assert "INVALID_BUDGET" in result.output
