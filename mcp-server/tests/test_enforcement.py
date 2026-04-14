"""Tests for the enforcement layer.

These are the tests that prove the safety properties hold. If any of these
fail, the server is non-conformant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS, Enforcement, ToolError


@pytest.fixture
def config(tmp_path: Path) -> Config:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Gemfile").write_text("gem 'rails'\n")
    return Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=10)


@pytest.fixture
def enforcement(config: Config) -> Enforcement:
    return Enforcement(config)


# ---- Read-only invariant ----


def test_read_only_tools_are_exhaustive() -> None:
    """The read-only set must contain exactly the seven ATLAS primitives."""
    assert (
        frozenset(
            {
                "view_file",
                "list_dir",
                "search_text",
                "search_symbol",
                "graph_query",
                "test_dry_run",
                "memex_read",
            }
        )
        == READ_ONLY_TOOLS
    )


def test_write_tools_are_forbidden(enforcement: Enforcement) -> None:
    """Any tool name not in the read-only set must be rejected."""
    for forbidden in [
        "edit_file",
        "write_file",
        "shell_exec",
        "git_commit",
        "deploy_run",
        "migration_apply",
    ]:
        with pytest.raises(ToolError) as exc:
            enforcement.assert_read_only(forbidden)
        assert exc.value.code == "FORBIDDEN"


def test_read_only_tools_pass(enforcement: Enforcement) -> None:
    for allowed in READ_ONLY_TOOLS:
        enforcement.assert_read_only(allowed)  # no exception


# ---- Path traversal ----


def test_path_in_repo_passes(enforcement: Enforcement, config: Config) -> None:
    enforcement.assert_path_in_repo(config.repo / "Gemfile")


def test_path_outside_repo_rejected(enforcement: Enforcement) -> None:
    with pytest.raises(ToolError) as exc:
        enforcement.assert_path_in_repo(Path("/etc/passwd"))
    assert exc.value.code == "FORBIDDEN"


def test_path_traversal_via_dotdot_rejected(enforcement: Enforcement, config: Config) -> None:
    # Construct a path that resolves outside the repo
    sneaky = config.repo / ".." / ".." / "etc" / "passwd"
    with pytest.raises(ToolError) as exc:
        enforcement.assert_path_in_repo(sneaky)
    assert exc.value.code == "FORBIDDEN"


# ---- Bounds ----


def test_cap_lines_under_limit(enforcement: Enforcement) -> None:
    start, end, overflow = enforcement.cap_lines(1, 50)
    assert (start, end, overflow) == (1, 50, False)


def test_cap_lines_at_limit(enforcement: Enforcement) -> None:
    start, end, overflow = enforcement.cap_lines(1, 100)
    assert (start, end, overflow) == (1, 100, False)


def test_cap_lines_over_limit(enforcement: Enforcement) -> None:
    start, end, overflow = enforcement.cap_lines(1, 500)
    assert (start, end, overflow) == (1, 100, True)


def test_cap_matches_respects_max(enforcement: Enforcement) -> None:
    assert enforcement.cap_matches(100) == 50  # MAX_MATCHES default
    assert enforcement.cap_matches(20) == 20
    assert enforcement.cap_matches(0) == 50


def test_cap_entries_respects_max(enforcement: Enforcement) -> None:
    assert enforcement.cap_entries(500) == 200
    assert enforcement.cap_entries(100) == 100
    assert enforcement.cap_entries(None) == 200


# ---- Rate limit ----


def test_rate_limit_allows_under_threshold(enforcement: Enforcement) -> None:
    for _ in range(10):
        enforcement.assert_rate_limit()  # max_calls_per_minute=10 from fixture


def test_rate_limit_rejects_over_threshold(enforcement: Enforcement) -> None:
    for _ in range(10):
        enforcement.assert_rate_limit()
    with pytest.raises(ToolError) as exc:
        enforcement.assert_rate_limit()
    assert exc.value.code == "TIMEOUT"


# ---- Telemetry ----


def test_telemetry_records_calls(enforcement: Enforcement) -> None:
    enforcement.record(tool="view_file", args={"path": "Gemfile"}, bytes_out=42, duration_ms=8)
    enforcement.record(
        tool="search_text", args={"pattern": "foo"}, bytes_out=100, duration_ms=15, overflow=True
    )
    summary = enforcement.telemetry_summary()
    assert summary["total_calls"] == 2
    assert summary["total_bytes_out"] == 142
    assert summary["per_tool"] == {"view_file": 1, "search_text": 1}
    assert summary["overflows"] == 1
