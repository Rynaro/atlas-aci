"""ACI-S22 labelled comparison of three test-selection baselines on one edit.

The coverage fixture project (`mcp-server/tests/fixtures/coverage`) is built twice into
external graph roots at ONE repository path: the original bytes with the real recorded
coverage run `a` imported (R0), then the same tree with three function bodies edited
(`clamp`, `total`, `never_called`; R1). Truth is derived from the frozen run-`a` labels:
the run-`a` test ids with an observed body line inside each edited span.

Baselines, all reported at function granularity (a `[param]` suffix is stripped):

* B0  run everything the recorded run executed;
* B1  structural-only (a build-only pair: no coverage anywhere);
* B2  the validation plan (R0 coverage + structural).

Honesty notes (also in the output): B2's observed recall is consistent by construction
because it reads the SAME run the truth is taken from; B1 against the truth is the
informative number; no superiority threshold is claimed; one edit on one small fixture
is a demonstration of the mechanism, not a benchmark.

Run from the checkout root inside the Python environment of `mcp-server`:

    python atlas/evals/validation_plan_comparison.py [--output PATH]

The plan is read-only planning: no test is ever run by this script.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from atlas_aci.codegraph import CodeGraph
from atlas_aci.query import validation
from atlas_aci.query.diff import Side
from atlas_aci.query.validation_limits import strip_parameters

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "mcp-server" / "tests" / "fixtures" / "coverage"
EVIDENCE = ROOT / ".spectra" / "evidence" / "aci-s22"
CALC = "app/calc.py"
FORMAT = "atlas-validation-plan-comparison.v1"
# (old, new) replacements: same line count, three changed bodies.
EDITS = (
    ("clamp", "        return low\n", "        return int(low)\n"),
    ("total", "    result = 0\n", "    result = 0 + 0\n"),
    ("never_called", '    return "unreached"\n', '    return "still"\n'),
)
# Body line ranges of each edited function in the ORIGINAL file (def line excluded).
SPANS = {"clamp": (44, 48), "total": (24, 30), "never_called": (52, 52)}


def _build(repo: Path, root: Path, *, coverage_run: str | None = None) -> Path:
    graph = CodeGraph(repo, graph_root=root)
    try:
        graph.build()
        if coverage_run:
            run = FIXTURE / "runs" / coverage_run
            graph.import_coverage(run / "coverage.json", run / "atlas-coverage-manifest.json")
    finally:
        graph.close()
    return root


def _functions(nodeids: set[str]) -> set[str]:
    return {strip_parameters(n) for n in nodeids}


def _score(selected: set[str], truth: set[str]) -> dict[str, Any]:
    tp, fp, fn = len(selected & truth), len(selected - truth), len(truth - selected)
    return {
        "selected": len(selected),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": None if not selected else round(tp / len(selected), 4),
        "recall": None if not truth else round(tp / len(truth), 4),
        "selected_functions": sorted(selected),
    }


def comparison(workdir: Path) -> dict[str, Any]:
    labels = json.loads((FIXTURE / "labels.json").read_text())["runs"]["a"]["rows"]
    repo = workdir / "repo"
    shutil.copytree(FIXTURE / "project", repo)
    original = (repo / CALC).read_bytes()
    r0 = _build(repo, workdir / "root-original-with-run-a", coverage_run="a")
    plain = _build(repo, workdir / "root-original-build-only")
    text = original.decode()
    for _name, old, new in EDITS:
        assert text.count(old) == 1
        text = text.replace(old, new)
    (repo / CALC).write_text(text)
    r1 = _build(repo, workdir / "root-edited")

    everything = {row["nodeid"] for row in labels}
    per_edit = {
        name: {
            row["nodeid"] for row in labels if row["path"] == CALC and low <= row["line"] <= high
        }
        for name, (low, high) in SPANS.items()
    }
    truth_ids = set().union(*per_edit.values())
    truth = _functions(truth_ids)

    with_coverage = validation.run(Side(repo, r0), Side(repo, r1))
    structural = validation.run(Side(repo, plain), Side(repo, r1))

    def selected(plan: dict[str, Any]) -> set[str]:
        names = set()
        for rec in plan["recommendations"]:
            names.add(strip_parameters(rec["test"]["selector"]["value"]))
        return names

    baselines = {
        "B0_run_everything": {**_score(_functions(everything), truth), "gaps": None},
        "B1_structural_only": {
            **_score(selected(structural), truth),
            "gaps": structural["gaps_total"],
        },
        "B2_validation_plan": {
            **_score(selected(with_coverage), truth),
            "gaps": with_coverage["gaps_total"],
            "observed_selected": len(
                _functions(
                    {
                        r["test"]["selector"]["value"]
                        for r in with_coverage["recommendations"]
                        if r["basis"] == "observed_execution"
                    }
                )
            ),
        },
    }
    return {
        "format": FORMAT,
        "fixture": "mcp-server/tests/fixtures/coverage/project (run a)",
        "granularity": "function (a [param] suffix is stripped)",
        "edits": {name: sorted(ids) for name, ids in per_edit.items()},
        "truth_nodeids": sorted(truth_ids),
        "truth_functions": sorted(truth),
        "run_a_nodeids_total": len(everything),
        "baselines": baselines,
        "plan_ids": {"B1": structural["plan_id"], "B2": with_coverage["plan_id"]},
        "gaps_B2": [
            {"name": g["name"], "reasons": sorted(g["reasons"])} for g in with_coverage["gaps"]
        ],
        "notes": [
            "B2 observed recall is consistent by construction: plan and truth read the same run.",
            "B1 against the truth is the informative number (no coverage was used).",
            "B1 is perfect here because the fixture tests call the edited functions by name;"
            " that is a property of this fixture, not a general claim.",
            "No superiority threshold is claimed; one edit on one small fixture.",
            "Recommendations are advisory; nothing was executed to produce them.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output", type=Path, default=None)
    options = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="atlas-s22-eval-") as tmp:
        result = comparison(Path(tmp))
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    target = options.output or (EVIDENCE / "plan-comparison.json" if EVIDENCE.is_dir() else None)
    if target is not None:
        target.write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
