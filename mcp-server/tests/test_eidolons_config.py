"""Project configuration must preserve client settings and fail before writes."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def load_configurator() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/configure-eidolons.py"
    spec = importlib.util.spec_from_file_location("eidolons_config", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path: Path) -> tuple[ModuleType, Path]:
    module = load_configurator()
    server = {
        "command": "docker",
        "args": [
            "run",
            "--rm",
            "-i",
            module.CRYSTALIUM_IMAGE,
            "python",
            "-m",
            "crystalium",
            "serve",
        ],
        "env": {"CLAUDE_ONLY": "preserved"},
    }
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"crystalium": server, "unrelated": {"command": "other"}}})
    )
    (tmp_path / ".cursor").mkdir()
    server = {**server, "env": {"CURSOR_ONLY": "preserved"}, "timeout": 42}
    (tmp_path / ".cursor/mcp.json").write_text(json.dumps({"mcpServers": {"crystalium": server}}))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text('[agents]\ndefault_subagent_model = "custom"\n')
    (tmp_path / ".claude/agents").mkdir(parents=True)
    (tmp_path / ".claude/agents/idg.md").write_text("---\nname: idg\ndescription: Scribe\n---\n")
    return module, tmp_path


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_preserves_host_settings_and_is_idempotent(
    project: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, root = project
    calls: list[list[str]] = []
    cache = tmp_path / "custom-eidolons-cache"
    monkeypatch.setenv("EIDOLONS_HOME", str(cache))

    def render(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        entry = json.loads(kwargs["input"])
        assert entry["args"][-2:] == ["-c", module.BOOTSTRAP]
        return subprocess.CompletedProcess(
            command, 0, stdout=(root / ".codex/config.toml").read_text()
        )

    monkeypatch.setattr(module.subprocess, "run", render)
    module.configure(root)
    configured = snapshot(root)
    module.configure(root)
    assert snapshot(root) == configured
    assert calls[0][:2] == [sys.executable, str(cache / "nexus/cli/src/mcp_codex_wiring.py")]
    claude = json.loads((root / ".mcp.json").read_text())["mcpServers"]
    cursor = json.loads((root / ".cursor/mcp.json").read_text())["mcpServers"]["crystalium"]
    assert claude["unrelated"] == {"command": "other"}
    assert claude["crystalium"]["env"] == {"CLAUDE_ONLY": "preserved"}
    assert cursor["env"] == {"CURSOR_ONLY": "preserved"}
    assert cursor["timeout"] == 42
    assert cursor["args"] == claude["crystalium"]["args"]
    assert (
        "tools: [Read, Write, Edit, mcp__crystalium__*]"
        in (root / ".claude/agents/idg.md").read_text()
    )


def test_codex_validation_failure_leaves_every_file_unchanged(
    project: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, root = project
    before = snapshot(root)

    def reject(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(module.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        module.configure(root)
    assert snapshot(root) == before


@pytest.mark.parametrize("change", ["custom-arguments", "other-release"])
def test_refuses_unknown_launches_before_writing(
    project: tuple[ModuleType, Path], change: str
) -> None:
    module, root = project
    path = root / ".mcp.json"
    config = json.loads(path.read_text())
    args = config["mcpServers"]["crystalium"]["args"]
    if change == "custom-arguments":
        args.extend(["--config", "/custom/config.yaml"])
    else:
        args[3] = "ghcr.io/rynaro/crystalium@sha256:other-release"
    path.write_text(json.dumps(config))
    before = snapshot(root)
    with pytest.raises(ValueError, match="no files changed"):
        module.configure(root)
    assert snapshot(root) == before
