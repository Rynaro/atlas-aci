"""ACI-S22 labelled comparison: the script is loaded by importlib spec and asserted on facts
the fixture itself constructs (no superiority claim is tested)."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "atlas/evals/validation_plan_comparison.py"


def load():
    spec = importlib.util.spec_from_file_location("validation_plan_comparison", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_comparison_reports_fixture_constructed_facts(tmp_path):
    result = load().comparison(tmp_path)
    assert result["format"] == "atlas-validation-plan-comparison.v1"
    assert {name: len(ids) for name, ids in result["edits"].items()} == {
        "clamp": 3,
        "total": 1,
        "never_called": 0,
    }
    assert result["run_a_nodeids_total"] == 14
    truth = set(result["truth_functions"])
    assert truth == {
        "tests/test_basic.py::TestTotals::test_total",
        "tests/test_basic.py::test_clamp",
        "tests/test_params.py::test_clamp_param",
    }
    baselines = result["baselines"]
    everything, structural, planned = (
        baselines["B0_run_everything"],
        baselines["B1_structural_only"],
        baselines["B2_validation_plan"],
    )
    assert everything["fn"] == 0 and everything["selected"] > planned["selected"] > 0
    assert everything["gaps"] is None
    # B2 reads the same recorded run as the truth: observed recall is consistent by construction.
    assert planned["fn"] == 0 and planned["tp"] == len(truth) and planned["observed_selected"] == 3
    assert truth <= set(structural["selected_functions"]) | set(planned["selected_functions"])
    assert structural["gaps"] == planned["gaps"] == 1
    assert [g["name"] for g in result["gaps_B2"]] == ["never_called"]
    assert {"no_observation_in_span", "no_structural_test"} <= set(result["gaps_B2"][0]["reasons"])
    assert any("construction" in note for note in result["notes"])
    assert (
        all(isinstance(planned[k], int) for k in ("selected", "tp", "fp", "fn"))
        and 0 < planned["precision"] <= 1
    )


def test_main_writes_the_json_it_prints(tmp_path, capsys):
    target = tmp_path / "out" / "plan-comparison.json"
    target.parent.mkdir()
    assert load().main(["--output", str(target)]) == 0
    printed = capsys.readouterr().out
    assert target.read_text() == printed
    assert json.loads(printed)["baselines"]["B2_validation_plan"]["fn"] == 0


def test_the_eval_script_never_launches_a_process():
    banned = {"subprocess", "multiprocessing", "pty", "os", "importlib"}
    tree = ast.parse(SCRIPT.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & banned
