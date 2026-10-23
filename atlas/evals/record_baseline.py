"""Create/index a pinned tiny Git fixture and record one real MCP transport task."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="atlas-s01-") as temporary:
        fixture = Path(temporary) / "repo"
        fixture.mkdir()
        (fixture / "example.py").write_text("def baseline_target():\n    return 42\n")
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
            "PYTHONPATH": str(ROOT / "mcp-server/src"),
        }
        for command in (
            ["git", "init", "-q", "--initial-branch=main"],
            ["git", "add", "example.py"],
            [
                "git",
                "-c",
                "user.name=Atlas Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Pinned transport fixture",
            ],
            [sys.executable, "-m", "atlas_aci", "index", "--repo", str(fixture)],
        ):
            subprocess.run(command, cwd=fixture, env=env, check=True)
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=fixture, text=True
        ).strip()
        task = {
            "schema": "atlas/task-manifest.v1",
            "repository": {"revision": revision},
            "model": None,
            "tasks": [
                {
                    "id": "search-baseline",
                    "tool": "search_symbol",
                    "arguments": {"name": "baseline_target"},
                    "expect_text": "baseline_target",
                }
            ],
        }
        tasks = Path(temporary) / "tasks"
        tasks.mkdir()
        (tasks / "tasks.json").write_text(json.dumps(task, indent=2) + "\n")
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run-canaries.py"),
                "--repo",
                str(fixture),
                "--canaries-dir",
                str(tasks),
                "--host",
                "stdio",
                "--output",
                str(args.output.resolve()),
                "--ci-gate",
            ],
            env=env,
            check=True,
        )
        # Preserve the actual pinned input alongside the durable output ledger.
        args.output.with_suffix(".tasks.json").write_text(json.dumps(task, indent=2) + "\n")


if __name__ == "__main__":
    main()
