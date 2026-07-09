#!/usr/bin/env python3
"""Canary suite runner for ATLAS implementations.

Loads canary missions, dispatches each to a host, captures the resulting
scout report, and scores against ground truth. Emits a JSON results file
suitable for CI gating.

This is a thin orchestrator. The actual host dispatch is pluggable —
implement an adapter for your host (Claude Code via API, Copilot via
GitHub Action, Cursor via headless mode) and select it with `--host`.

DEFERRED IN v2.0.0 (doc-honesty batch, AC-DOC-3): no real dispatcher is
implemented yet. `--host stub` (the default and only wired choice) uses
`StubDispatcher`, whose `dispatch()` deliberately raises
`NotImplementedError` — see below. Any other `--host` value raises the same
in `main()`. There is therefore no canary pass-rate to quote against a real
host today; do not cite one in docs until a real dispatcher lands. The
sketched adapters below (Claude Code API, etc.) are comments, not code.

Usage:
  uv run python scripts/run-canaries.py \\
    --repo ~/code/your-repo \\
    --canaries-dir ~/code/atlas/evals/canaries/ \\
    --host claude-code-api \\
    --output /tmp/canary-results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------


@dataclass
class CanaryMission:
    """A single canary mission and its expected outcomes."""

    id: str
    mission_md: str            # contents of mission.md
    expected_answer_md: str    # contents of expected/answer.md
    expected_findings: list[str]  # finding-content fingerprints
    expected_handoff_recipient: str


@dataclass
class CanaryResult:
    """One mission's outcome."""

    mission_id: str
    status: str  # "pass" | "fail" | "error"
    decision_correct: bool
    findings_recall: float
    handoff_correct: bool
    tokens_total: int
    tool_calls_total: int
    wall_clock_s: int
    fold_ratio: float
    search_efficiency: float
    failure_class: str
    error_message: str | None = None
    raw_report: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------
# Host dispatcher protocol
# ----------------------------------------------------------------------------


class HostDispatcher(Protocol):
    """Anything that can run a mission against a host and return a scout report."""

    def dispatch(self, mission_md: str, repo: Path) -> dict[str, Any]:
        """Return the scout report as a parsed dict (per scout-report.v1.json)."""
        ...


class StubDispatcher:
    """Reference dispatcher that does nothing — replace with a real adapter.

    DEFERRED IN v2.0.0: this is the only wired dispatcher today. There is no
    real host adapter, so there is no canary pass-rate to report yet.
    """

    def dispatch(self, mission_md: str, repo: Path) -> dict[str, Any]:
        raise NotImplementedError(
            "Implement a real dispatcher for your host. See module docstring."
        )


# Real dispatcher examples (sketches — fill in for your environment):
#
# class ClaudeCodeAPIDispatcher:
#     def __init__(self, api_key: str, model: str = "claude-sonnet-4-5"):
#         self.client = Anthropic(api_key=api_key)
#         self.model = model
#
#     def dispatch(self, mission_md: str, repo: Path) -> dict[str, Any]:
#         # Use the Anthropic API with MCP tool support
#         response = self.client.beta.messages.create(
#             model=self.model,
#             max_tokens=8000,
#             system=open("atlas/agent.md").read(),
#             messages=[{"role": "user", "content": mission_md}],
#             mcp_servers=[{
#                 "type": "url",
#                 "url": f"stdio:atlas-aci serve --repo {repo}",
#                 "name": "atlas-aci",
#             }],
#         )
#         # Parse the resulting scout report from response.content
#         ...


# ----------------------------------------------------------------------------
# Loading & scoring
# ----------------------------------------------------------------------------


def load_canary(canary_dir: Path) -> CanaryMission:
    """Load one canary directory."""
    mission_md = (canary_dir / "mission.md").read_text()
    expected_answer = (canary_dir / "expected" / "answer.md").read_text()

    findings_min_path = canary_dir / "expected" / "findings.min"
    expected_findings = []
    if findings_min_path.exists():
        expected_findings = [
            line.strip()
            for line in findings_min_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]

    handoff_yaml = canary_dir / "expected" / "handoff.yaml"
    expected_handoff = "human"
    if handoff_yaml.exists():
        # Tiny YAML parse — avoid pyyaml dep for the runner
        for line in handoff_yaml.read_text().splitlines():
            if line.strip().startswith("primary_recipient:"):
                expected_handoff = line.split(":", 1)[1].strip().strip("\"'")
                break

    return CanaryMission(
        id=canary_dir.name,
        mission_md=mission_md,
        expected_answer_md=expected_answer,
        expected_findings=expected_findings,
        expected_handoff_recipient=expected_handoff,
    )


def score_decision(report: dict[str, Any], expected_answer_md: str) -> bool:
    """Did the report's §3 answer cover every enumerable claim in the expected answer?

    The expected answer is markdown listing claims as bullet points containing
    file paths or symbol names. We extract those tokens and check the report's
    answer prose mentions each.
    """
    import re

    expected_tokens: set[str] = set()
    for line in expected_answer_md.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        # File paths
        for m in re.finditer(r"\b([\w\-./]+\.\w+)\b", line):
            expected_tokens.add(m.group(1))
        # Class/symbol names (CapWords or snake_case identifiers)
        for m in re.finditer(r"\b([A-Z][\w]*(?:#\w+)?)\b", line):
            expected_tokens.add(m.group(1))

    if not expected_tokens:
        return False

    # Flatten the report's answer section to a single string
    answer_blob = " ".join(
        a.get("prose", "") for a in report.get("answer", [])
    )

    found = sum(1 for tok in expected_tokens if tok in answer_blob)
    return found / len(expected_tokens) >= 0.8


def score_findings_recall(report: dict[str, Any], expected_findings: list[str]) -> float:
    """Fraction of expected finding fingerprints that appear in any actual finding's claim."""
    if not expected_findings:
        return 1.0

    actual_claims = []
    for f in report.get("findings", []) or []:
        actual_claims.append(f.get("claim", ""))
    # findings live in findings.md by default; if the report inlines them differently,
    # extend this to look at answer.finding_refs cross-referenced with the findings file.

    if not actual_claims:
        return 0.0

    all_actual = " ".join(actual_claims).lower()
    matched = sum(1 for fp in expected_findings if fp.lower() in all_actual)
    return matched / len(expected_findings)


def classify_failure(
    decision_correct: bool,
    recall: float,
    handoff_correct: bool,
    report: dict[str, Any],
) -> str:
    """Best-effort failure-class assignment for telemetry rollups."""
    if decision_correct and recall >= 0.8 and handoff_correct:
        return "NONE"
    if not handoff_correct:
        return "HANDOFF_MISLABEL"
    telemetry = report.get("telemetry", {})
    if telemetry.get("fold_ratio", 0) > 0.3:
        return "FOLD_DROPPED_CONSTRAINT"
    if telemetry.get("search_efficiency", 1.0) < 0.1:
        return "OVER_EXPLORED"
    if recall < 0.3:
        return "UNDER_SCOPED"
    return "DEAD_END"


def run_one(
    mission: CanaryMission,
    dispatcher: HostDispatcher,
    repo: Path,
) -> CanaryResult:
    started = time.monotonic()
    try:
        report = dispatcher.dispatch(mission.mission_md, repo)
    except Exception as e:
        return CanaryResult(
            mission_id=mission.id,
            status="error",
            decision_correct=False,
            findings_recall=0.0,
            handoff_correct=False,
            tokens_total=0,
            tool_calls_total=0,
            wall_clock_s=int(time.monotonic() - started),
            fold_ratio=0.0,
            search_efficiency=0.0,
            failure_class="ERROR",
            error_message=str(e),
        )

    decision_correct = score_decision(report, mission.expected_answer_md)
    recall = score_findings_recall(report, mission.expected_findings)
    actual_handoff = report.get("handoff", {}).get("primary_recipient", "")
    handoff_correct = actual_handoff == mission.expected_handoff_recipient

    telemetry = report.get("telemetry", {})
    phases = telemetry.get("phases", {})
    tokens_total = sum(
        (p or {}).get("tokens_in", 0) + (p or {}).get("tokens_out", 0)
        for p in phases.values()
    )
    tool_calls_total = sum((p or {}).get("tool_calls", 0) for p in phases.values())

    passed = decision_correct and recall >= 0.8 and handoff_correct

    return CanaryResult(
        mission_id=mission.id,
        status="pass" if passed else "fail",
        decision_correct=decision_correct,
        findings_recall=recall,
        handoff_correct=handoff_correct,
        tokens_total=tokens_total,
        tool_calls_total=tool_calls_total,
        wall_clock_s=int(time.monotonic() - started),
        fold_ratio=telemetry.get("fold_ratio", 0.0),
        search_efficiency=telemetry.get("search_efficiency", 0.0),
        failure_class=classify_failure(decision_correct, recall, handoff_correct, report),
        raw_report=report,
    )


def aggregate(results: list[CanaryResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"missions": 0}
    passed = sum(1 for r in results if r.status == "pass")
    return {
        "missions": n,
        "passed": passed,
        "pass_rate": passed / n,
        "mean_fold_ratio": sum(r.fold_ratio for r in results) / n,
        "mean_search_efficiency": sum(r.search_efficiency for r in results) / n,
        "mean_tokens": sum(r.tokens_total for r in results) / n,
        "mean_tool_calls": sum(r.tool_calls_total for r in results) / n,
        "failure_classes": _failure_breakdown(results),
    }


def _failure_breakdown(results: list[CanaryResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results:
        out[r.failure_class] = out.get(r.failure_class, 0) + 1
    return out


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--canaries-dir", required=True, type=Path,
                   help="Directory containing one subdirectory per canary mission.")
    p.add_argument("--host", default="stub",
                   choices=["stub", "claude-code-api", "copilot", "cursor"])
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--ci-gate", action="store_true",
                   help="Exit 1 if pass rate < 0.8 or any regression vs baseline.")
    p.add_argument("--baseline", type=Path, default=None,
                   help="Previous canary result file for regression comparison.")
    args = p.parse_args()

    # Load all canary missions
    canary_dirs = sorted([d for d in args.canaries_dir.iterdir() if d.is_dir()])
    if not canary_dirs:
        print(f"No canary subdirectories under {args.canaries_dir}", file=sys.stderr)
        return 2
    missions = [load_canary(d) for d in canary_dirs]

    # Pick a dispatcher
    dispatcher: HostDispatcher
    if args.host == "stub":
        dispatcher = StubDispatcher()
    else:
        # Wire your real dispatcher here
        raise NotImplementedError(f"Dispatcher {args.host!r} not yet implemented.")

    # Run
    print(f"Running {len(missions)} canary missions against repo {args.repo}...", file=sys.stderr)
    results = [run_one(m, dispatcher, args.repo) for m in missions]
    summary = aggregate(results)

    payload = {
        "repo": str(args.repo),
        "host": args.host,
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    args.output.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {args.output}", file=sys.stderr)
    print(json.dumps(summary, indent=2))

    # CI gate
    if args.ci_gate:
        if summary["pass_rate"] < 0.8:
            print("CI gate FAILED: pass rate < 0.8", file=sys.stderr)
            return 1
        if args.baseline and args.baseline.exists():
            baseline = json.loads(args.baseline.read_text())
            baseline_passed = {r["mission_id"] for r in baseline["results"] if r["status"] == "pass"}
            current_passed = {r.mission_id for r in results if r.status == "pass"}
            regressions = baseline_passed - current_passed
            if regressions:
                print(f"CI gate FAILED: regressions in {sorted(regressions)}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
