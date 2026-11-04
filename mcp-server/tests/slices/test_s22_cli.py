"""ACI-S22 operator CLI: option resolution, canonical stdout, rejection and usage exits."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from test_s20_support import V1, V2
from test_s22_support import (
    cli_args,
    cli_bytes,
    cov_world,
    inventory,
    plan,
    same_path_pair,
)

from atlas_aci.__main__ import cli
from atlas_aci.query import validation
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev


def invoke(*args):
    return CliRunner().invoke(cli, ["validation-plan", *args])


def side_roots(before, after):
    return [
        "--before-graph-root",
        str(before.graph_root),
        "--after-graph-root",
        str(after.graph_root),
    ]


def test_canonical_stdout_equals_the_library_plan(tmp_path):
    world = cov_world(tmp_path)
    result = invoke("--repo", str(world.repo), *side_roots(world.before, world.after))
    assert result.exit_code == 0, result.output
    expected = plan(world.before, world.after)
    assert result.stdout_bytes == validation.canonical_bytes(expected)
    assert result.stdout_bytes.endswith(b"\n") and result.stdout_bytes.count(b"\n") == 1
    loaded = json.loads(result.stdout_bytes)
    assert ev.canonical(loaded).encode() + b"\n" == result.stdout_bytes
    assert loaded["format"] == "atlas-validation-plan.v1" and loaded["recommendations"]
    assert loaded["status"] == "recommendations_only" and loaded["execution"] == "not_performed"
    validation.validate_plan(loaded)


def test_options_resolve_like_the_library_arguments(tmp_path):
    world = cov_world(tmp_path)
    arguments = {
        "relations": ["call"],
        "confidence": ["EXTRACTED"],
        "max_seeds": 2,
        "max_depth": 2,
        "max_visited_nodes": 50,
        "max_examined_edges": 100,
        "max_vm_operations": 500_000,
        "deadline_ms": 1500,
        "max_spans": 5,
        "max_recommendations": 3,
        "max_gaps": 4,
        "max_evidence_per_recommendation": 2,
        "max_matched_lines": 3,
        "max_observation_rows": 5000,
        "max_reference_probes": 7,
        "max_reference_rows": 900,
        "max_coverage_sources": 2,
    }
    options = {k: (",".join(v) if isinstance(v, list) else v) for k, v in arguments.items()}
    via_cli = cli_bytes(world.before, world.after, **options)
    assert via_cli == validation.canonical_bytes(plan(world.before, world.after, arguments))
    emitted = json.loads(via_cli)
    assert emitted["effective_limits"]["max_matched_lines"] == 3
    assert emitted["effective_limits"]["max_coverage_sources"] == 2
    assert list(emitted["impact"]["relations"]) == ["call"]
    assert len(emitted["recommendations"]) <= 3
    # Diff limits are operator options too.
    assert (
        json.loads(cli_bytes(world.before, world.after, max_changed_files=5))["effective_limits"][
            "max_changed_files"
        ]
        == 5
    )


def test_before_and_after_repo_override_under_one_graph_root(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    repo, before, after = same_path_pair(tmp_path, V1, V2)
    result = invoke(
        "--before-repo", str(repo), "--after-repo", str(repo), *side_roots(before, after)
    )
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == validation.canonical_bytes(plan(before, after))


def test_rejections_exit_one_with_a_fixed_message_and_empty_stdout(tmp_path):
    world = cov_world(tmp_path)
    watched = [world.repo, world.r0, world.r1]
    state = inventory(*watched)
    same_bytes = world.build_root("root-c-a", world.original, runs=("a",))  # == R0's generation
    other_run = world.build_root("root-c-b", world.original, runs=("b",))
    sides = ["--repo", str(world.repo), *side_roots(world.before, world.after)]
    cases = {
        "VALIDATION_SOURCE_REJECTED: index_unavailable": invoke(
            *sides, "--coverage-graph-root", str(tmp_path / "never-built")
        ),
        "VALIDATION_SOURCE_REJECTED: same_namespace": invoke(
            *sides, "--coverage-graph-root", str(world.r0)
        ),
        "VALIDATION_SOURCE_REJECTED: duplicate_source": invoke(
            *sides, "--coverage-graph-root", str(same_bytes)
        ),
        "VALIDATION_SOURCE_REJECTED: source_limit": invoke(
            *sides, "--coverage-graph-root", str(other_run), "--max-coverage-sources", "0"
        ),
        "SNAPSHOT_DIFF_REJECTED: same_namespace": invoke(
            "--repo", str(world.repo), "--graph-root", str(world.r0)
        ),
        "SNAPSHOT_DIFF_REJECTED: index_unavailable": invoke(
            "--repo",
            str(world.repo),
            "--before-graph-root",
            str(world.r0),
            "--after-graph-root",
            str(tmp_path / "never-built-after"),
        ),
        "IMPACT_DIFF_TOO_LARGE: changed_file_limit": invoke(*sides, "--max-changed-files", "0"),
    }
    for message, result in cases.items():
        assert result.exit_code == 1 and result.stdout == "", (message, result.output)
        assert "Validation plan rejected: " + message in result.output
        assert str(tmp_path) not in result.output
        assert "Traceback" not in result.output
    assert not (tmp_path / "never-built").exists()
    assert not (tmp_path / "never-built-after").exists()
    assert inventory(*watched) == state


def test_a_foreign_producer_or_repository_source_is_rejected_by_closed_reason(tmp_path):
    world = cov_world(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    repo, before, _after = same_path_pair(other, V1, V2)
    # A coverage root built for a DIFFERENT repository is not a compatible source.
    result = invoke(
        "--repo",
        str(world.repo),
        *side_roots(world.before, world.after),
        "--coverage-graph-root",
        str(before.graph_root),
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert "Validation plan rejected: VALIDATION_SOURCE_REJECTED: " in result.output
    assert str(tmp_path) not in result.output and repo.exists()


def test_usage_errors_exit_two_with_empty_stdout(tmp_path):
    world = cov_world(tmp_path)
    watched = [world.repo, world.r0, world.r1]
    state = inventory(*watched)
    side = side_roots(world.before, world.after)
    attempts = [
        invoke(*side),  # no repository at all
        invoke("--before-repo", str(world.repo), *side),  # one side without a repository
        invoke("--repo", str(tmp_path / "does-not-exist"), *side),
        invoke(
            "--repo",
            str(world.repo),
            "--before-graph-root",
            str(world.repo / "inside"),
            "--after-graph-root",
            str(world.r1),
        ),
        invoke("--repo", str(world.repo), *side, "--max-depth", "0"),
        invoke("--repo", str(world.repo), *side, "--relations", "rationale_for"),
        invoke("--repo", str(world.repo), *side, "--confidence", "AMBIGUOUS"),
        invoke("--repo", str(world.repo), *side, "--deadline-ms", "2001"),
        invoke("--repo", str(world.repo), *side, "--max-spans", "0"),
        invoke("--repo", str(world.repo), *side, "--max-recommendations", "501"),
        invoke("--repo", str(world.repo), *side, "--max-coverage-sources", "9"),
        invoke("--repo", str(world.repo), *side, "--max-observation-rows", "-1"),
        invoke("--repo", str(world.repo), *side, "--max-bytes", "10"),  # impact-only option
    ]
    for result in attempts:
        assert result.exit_code == 2 and result.stdout == "", result.output
        assert "Traceback" not in result.output
    assert inventory(*watched) == state


@pytest.mark.parametrize("flag", ["--coverage-graph-root"])
def test_the_coverage_root_option_is_repeatable_and_order_free(tmp_path, flag):
    world = cov_world(tmp_path)
    plain = world.build_root("root-plain", world.original)
    c_a = world.build_root("root-c-a", world.original, runs=("a",))
    c_ab = world.build_root("root-c-ab", world.original, runs=("a", "b"))
    before = Side(world.repo, plain)
    one = cli_args(before, world.after, [c_a, c_ab])
    two = cli_args(before, world.after, [c_ab, c_a])
    assert one.count(flag) == 2
    first, second = (CliRunner().invoke(cli, args, catch_exceptions=False) for args in (one, two))
    assert first.exit_code == second.exit_code == 0
    assert first.stdout_bytes == second.stdout_bytes
    emitted = json.loads(first.stdout_bytes)
    assert [s["source"] for s in emitted["coverage_sources"]] == [
        "before",
        "after",
        "external",
        "external",
    ]
