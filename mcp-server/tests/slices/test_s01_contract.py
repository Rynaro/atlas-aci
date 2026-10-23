"""ACI-S01 contracts: actual Atlas MCP subprocess and durable transport evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("s01_runner", ROOT / "atlas/evals/stdio_runner.py")
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "example.py").write_text("def baseline_target():\n    return 42\n")
    git(repo, "init", "-q")
    git(repo, "add", "example.py")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "mcp-server/src")}
    subprocess.run(
        [sys.executable, "-m", "atlas_aci", "index", "--repo", str(repo)],
        env=env,
        check=True,
        capture_output=True,
    )
    return repo


def manifest(repo: Path, count: int = 1) -> dict[str, Any]:
    return {
        "schema": "atlas/task-manifest.v1",
        "repository": {"revision": git(repo, "rev-parse", "HEAD")},
        "model": None,
        "tasks": [
            {
                "id": f"search-{i}",
                "tool": "search_symbol",
                "arguments": {"name": "baseline_target"},
                "expect_text": "baseline_target",
            }
            for i in range(count)
        ],
    }


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def test_real_stdio_dispatch(fixture_repo: Path, tmp_path: Path) -> None:
    result = runner.run_manifest(fixture_repo, manifest(fixture_repo), tmp_path / "results")
    receipt = load(tmp_path / "results" / result["run_id"] / "tasks/search-0.json")
    response = load(tmp_path / "results" / result["run_id"] / receipt["response"]["path"])
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["definitions"][0]["name"] == "baseline_target"
    assert receipt["outcome"] == "success"


def test_run_identity(fixture_repo: Path, tmp_path: Path) -> None:
    task = manifest(fixture_repo)
    result = runner.run_manifest(fixture_repo, task, tmp_path / "a")
    inputs = result["inputs"]
    assert inputs["repository"]["revision"] == task["repository"]["revision"]
    assert len(inputs["repository"]["content_sha256"]) == 64
    assert inputs["tasks"][0]["task_id"] == runner.digest(task["tasks"][0])
    assert result["run_id"] == runner.digest(inputs)
    # Changed source at the same revision cannot reuse the completed evidence.
    (fixture_repo / "example.py").write_text("def changed_target():\n    return 43\n")
    changed = runner.run_manifest(fixture_repo, task, tmp_path / "a")
    assert changed["run_id"] != result["run_id"]


def test_same_run_replay(fixture_repo: Path, tmp_path: Path) -> None:
    task = manifest(fixture_repo)
    result = runner.run_manifest(fixture_repo, task, tmp_path / "results")
    before = {
        p.relative_to(tmp_path / "results"): p.read_bytes()
        for p in (tmp_path / "results").rglob("*.json")
    }
    again = runner.run_manifest(fixture_repo, task, tmp_path / "results")
    after = {
        p.relative_to(tmp_path / "results"): p.read_bytes()
        for p in (tmp_path / "results").rglob("*.json")
    }
    assert again == result
    assert before == after
    assert len(list((tmp_path / "results").glob("*/run.json"))) == 1


def test_interrupted_resume(fixture_repo: Path, tmp_path: Path) -> None:
    task = manifest(fixture_repo, count=2)

    def interrupt(receipt: dict[str, Any]) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner.run_manifest(fixture_repo, task, tmp_path / "results", on_task=interrupt)
    first = next((tmp_path / "results").glob("*/tasks/search-0.json"))
    before = first.read_bytes()
    dispatched: list[str] = []
    result = runner.run_manifest(
        fixture_repo,
        task,
        tmp_path / "results",
        on_task=lambda receipt: dispatched.append(receipt["id"]),
    )
    assert result["status"] == "COMPLETE"
    assert dispatched == ["search-1"]
    assert first.read_bytes() == before


def test_timeout_is_failure(fixture_repo: Path, tmp_path: Path) -> None:
    # A real child accepts input and never answers initialization. Closing stdin
    # exits it, so this checks both a real deadline and SDK subprocess cleanup.
    child = tmp_path / "hang.py"
    child.write_text(
        "import os,sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "for line in sys.stdin:\n    pass\n"
    )
    pid_path = tmp_path / "pid"
    started = time.monotonic()
    result = runner.run_manifest(
        fixture_repo,
        manifest(fixture_repo),
        tmp_path / "results",
        server=[sys.executable, str(child), str(pid_path)],
        deadline=0.2,
    )
    assert time.monotonic() - started < 8
    receipt = load(tmp_path / "results" / result["run_id"] / "tasks/search-0.json")
    assert receipt["outcome"] == "timeout"
    assert result["summary"]["failed"] == 1
    assert result["summary"]["passed"] == 0
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_path.read_text()), 0)


def test_unmeasured_metrics(fixture_repo: Path, tmp_path: Path) -> None:
    result = runner.run_manifest(fixture_repo, manifest(fixture_repo), tmp_path / "results")
    assert result["evaluation_kind"] == "transport_replay"
    assert result["metrics"]["model_tokens"] is None
    assert result["metrics"]["model_cost"] is None
    assert result["metrics"]["coding_task_success"] is None
    assert result["metrics"]["unavailable_reason"]


def test_completed_run_no_dispatch(fixture_repo: Path, tmp_path: Path) -> None:
    task = manifest(fixture_repo)
    result = runner.run_manifest(fixture_repo, task, tmp_path / "results")
    # This counter is the actual dispatch function used for subprocess calls.
    assert runner.dispatch_count == 1
    runner.run_manifest(fixture_repo, task, tmp_path / "results")
    assert runner.dispatch_count == 0
    # Payload corruption invalidates the receipt and forces actual dispatch.
    directory = tmp_path / "results" / result["run_id"]
    receipt = load(directory / "tasks/search-0.json")
    (directory / receipt["response"]["path"]).write_text("{}")
    runner.run_manifest(fixture_repo, task, tmp_path / "results")
    assert runner.dispatch_count == 1


def test_cli_from_other_directory(fixture_repo: Path, tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "tasks.json").write_text(json.dumps(manifest(fixture_repo)))
    output = tmp_path / "cli.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run-canaries.py"),
            "--repo",
            str(fixture_repo),
            "--canaries-dir",
            str(tasks),
            "--host",
            "stdio",
            "--output",
            str(output),
            "--ci-gate",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert load(output)["summary"] == {"tasks": 1, "passed": 1, "failed": 0}


def test_call_deadline_after_initialization(fixture_repo: Path, tmp_path: Path) -> None:
    child = tmp_path / "mcp_hang.py"
    child.write_text(
        "import os,sys,anyio\nfrom pathlib import Path\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "mcp=FastMCP('deadline')\n@mcp.tool()\n"
        "async def never() -> str:\n    await anyio.sleep_forever()\n    return ''\n"
        "mcp.run(transport='stdio')\n"
    )
    pid = tmp_path / "pid"
    task = manifest(fixture_repo)
    task["tasks"] = [{"id": "never", "tool": "never", "arguments": {}}]
    result = runner.run_manifest(
        fixture_repo,
        task,
        tmp_path / "results",
        server=[sys.executable, str(child), str(pid)],
        deadline=0.8,
    )
    receipt = load(tmp_path / "results" / result["run_id"] / "tasks/never.json")
    assert receipt["outcome"] == "timeout"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_reindex_diagnostic_dates_do_not_change_identity(
    fixture_repo: Path, tmp_path: Path
) -> None:
    task = manifest(fixture_repo)
    before = runner.run_manifest(fixture_repo, task, tmp_path / "results")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "mcp-server/src")}
    subprocess.run(
        [sys.executable, "-m", "atlas_aci", "index", "--repo", str(fixture_repo)],
        env=env,
        check=True,
        capture_output=True,
    )
    after = runner.run_manifest(fixture_repo, task, tmp_path / "results")
    assert before["run_id"] == after["run_id"]
    assert runner.dispatch_count == 0


def test_invalid_manifest_rejected_before_dispatch(fixture_repo: Path, tmp_path: Path) -> None:
    task = manifest(fixture_repo)
    task["tasks"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        runner.run_manifest(fixture_repo, task, tmp_path / "results")
    assert runner.dispatch_count == 0


def test_outcome_receipt_must_match_response(fixture_repo: Path, tmp_path: Path) -> None:
    task = manifest(fixture_repo)
    result = runner.run_manifest(fixture_repo, task, tmp_path / "results")
    path = tmp_path / "results" / result["run_id"] / "tasks/search-0.json"
    receipt = load(path)
    receipt["outcome"] = "timeout"
    path.write_text(json.dumps(receipt))
    again = runner.run_manifest(fixture_repo, task, tmp_path / "results")
    assert runner.dispatch_count == 1
    assert again["summary"]["passed"] == 1
