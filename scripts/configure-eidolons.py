#!/usr/bin/env python3
"""Apply project compatibility fixes after Eidolons sync or MCP wiring repair.

CRYSTALIUM 2.5.0 logs to stdout before configuring its MCP transport. Route
structlog to stderr before importing the server, and persist its model cache
inside the existing project memory volume. This intentional launch override
is reported as drift by the upstream `eidolons mcp verify` command.
"""

import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

CRYSTALIUM_IMAGE = (
    "ghcr.io/rynaro/crystalium@"
    "sha256:8a6ce6dafc95b036e4cda752e42a3c54d8b0b5ad96cb0d62a391b0aefb100ae0"
)
BOOTSTRAP = """import os, runpy, sys
import structlog
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
cache = os.path.join(os.environ['CRYSTALIUM_DATA_DIR'], '.cache', 'huggingface')
os.environ.setdefault('HF_HOME', cache)
sys.argv = ['crystalium', 'serve']
runpy.run_module('crystalium', run_name='__main__')
"""


def write_if_changed(path: Path, text: str) -> None:
    if path.read_text() != text:
        temporary = path.with_name(path.name + ".eidolons-tmp")
        temporary.write_text(text)
        temporary.replace(path)


def configure(root: Path) -> None:
    config_path = root / ".mcp.json"
    config: dict[str, Any] = json.loads(config_path.read_text())
    entry = config["mcpServers"]["crystalium"]
    args = entry["args"]
    if entry["command"] != "docker" or CRYSTALIUM_IMAGE not in args:
        raise ValueError("Expected the pinned CRYSTALIUM 2.5.0 Docker launch; no files changed")
    image_index = args.index(CRYSTALIUM_IMAGE)
    tail = args[image_index + 1 :]
    if tail not in (
        ["python", "-m", "crystalium", "serve"],
        ["python", "-c", BOOTSTRAP],
    ):
        raise ValueError("Custom CRYSTALIUM arguments require manual review; no files changed")
    entry["args"] = [*args[: image_index + 1], "python", "-c", BOOTSTRAP]

    cursor_path = root / ".cursor/mcp.json"
    cursor: dict[str, Any] = json.loads(cursor_path.read_text())
    cursor_entry = cursor["mcpServers"]["crystalium"]
    cursor_entry.update({"command": entry["command"], "args": copy.deepcopy(entry["args"])})

    # Validate Codex before writing either JSON config, preserving unrelated
    # settings through the installed helper. Honor an alternate cache.
    eidolons_home = Path(os.environ.get("EIDOLONS_HOME", str(Path.home() / ".eidolons")))
    helper = eidolons_home / "nexus/cli/src/mcp_codex_wiring.py"
    codex_path = root / ".codex/config.toml"
    rendered = subprocess.run(
        [sys.executable, str(helper), str(codex_path), "crystalium"],
        input=json.dumps(entry),
        text=True,
        capture_output=True,
        check=True,
    )

    # IDG's upstream adapter inherits every Claude tool without this list.
    idg_path = root / ".claude/agents/idg.md"
    idg = idg_path.read_text()
    if not any(line.startswith("tools:") for line in idg.splitlines()):
        lines = idg.splitlines(keepends=True)
        lines.insert(3, "tools: [Read, Write, Edit, mcp__crystalium__*]\n")
        idg = "".join(lines)

    write_if_changed(config_path, json.dumps(config, indent=2) + "\n")
    write_if_changed(cursor_path, json.dumps(cursor, indent=2) + "\n")
    write_if_changed(codex_path, rendered.stdout)
    write_if_changed(idg_path, idg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    configure(args.project_root)
    print("Project MCP compatibility fixes applied to Claude Code, Codex, and Cursor.")


if __name__ == "__main__":
    main()
