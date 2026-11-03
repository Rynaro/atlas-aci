"""ACI-S19 operator CLI: option resolution, rejection surfaces, canonical bytes."""

from __future__ import annotations

from click.testing import CliRunner
from test_s19_support import build, git, init_git, inventory, same_path_pair, write_tree

from atlas_aci.__main__ import cli
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import Side

V1 = {"a.py": "def f(a):\n    return a\n"}
V2 = {"a.py": "def f(a, b):\n    return a\n"}


def run(*args):
    return CliRunner().invoke(cli, ["diff", *args])


def test_repo_with_two_graph_roots_prints_canonical_bytes(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    expected = symbol_diff.canonical_bytes(symbol_diff.compare(before, after))
    result = run(
        "--repo",
        str(repo),
        "--before-graph-root",
        str(before.graph_root),
        "--after-graph-root",
        str(after.graph_root),
    )
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == expected and expected.endswith(b"\n")
    assert expected.count(b"\n") == 1  # one canonical line, no pretty printing
    assert b'"category":"signature"' in expected


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
            "--graph-root",
            str(shared),
            "--before-repo",
            str(primary),
            "--after-repo",
            str(linked),
        )
        assert result.exit_code == 0, result.output
        expected = symbol_diff.compare(Side(primary, shared), Side(linked, shared))
        assert result.stdout_bytes == symbol_diff.canonical_bytes(expected)
        # --repo supplies the default; a per-side repo overrides only its own side.
        mixed = run(
            "--repo",
            str(primary),
            "--after-repo",
            str(linked),
            "--graph-root",
            str(shared),
        )
        assert mixed.exit_code == 0 and mixed.stdout_bytes == result.stdout_bytes
    finally:
        git(primary, "worktree", "remove", "--force", str(linked))


def test_usage_errors_exit_two_with_empty_stdout(tmp_path):
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    watched = [repo, before.graph_root, after.graph_root]
    state = inventory(*watched)
    roots = [
        "--before-graph-root",
        str(before.graph_root),
        "--after-graph-root",
        str(after.graph_root),
    ]
    same = run("--repo", str(repo), "--graph-root", str(before.graph_root))
    missing_side = run("--before-repo", str(repo), *roots)
    nothing = run(*roots)
    absent = run("--repo", str(tmp_path / "does-not-exist"), *roots)
    inside = run(
        "--repo",
        str(repo),
        "--before-graph-root",
        str(repo / "inside"),
        "--after-graph-root",
        str(after.graph_root),
    )
    for result in (same, missing_side, nothing, absent, inside):
        assert result.exit_code == 2 and result.stdout == "", result.output
    assert "same graph namespace" in same.output
    assert "--repo" in missing_side.output
    assert "inside" in inside.output
    assert inventory(*watched) == state
    assert "Traceback" not in "".join(
        r.output for r in (same, missing_side, nothing, absent, inside)
    )


def test_rejection_exits_one_with_message_and_empty_stdout(tmp_path):
    repo, before, _after = same_path_pair(tmp_path, V1, V2)
    result = run(
        "--repo",
        str(repo),
        "--before-graph-root",
        str(before.graph_root),
        "--after-graph-root",
        str(tmp_path / "never-built"),
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert "Snapshot diff rejected: index_unavailable" in result.output
    assert not (tmp_path / "never-built").exists()
    assert "Traceback" not in result.output
