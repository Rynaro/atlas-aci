"""ACI-S05: actual executable sentinels and real stdio policy evidence."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.server import build_server_state, build_tool_manifest, dispatch_tool_call
from atlas_aci.tools.test_dry_run import test_dry_run as run_test

ROOT = Path(__file__).resolve().parents[3]
META = "atlas-aci/test_execution_enabled"


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("ATLAS_ACI_ENABLE_TEST_EXECUTION", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_case.py").write_text("# harmless test fixture\n")
    (repo / "sentinel.jsonl").write_text("untouched\n")
    executable = tmp_path / "bin" / "pytest"
    executable.parent.mkdir()
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys,time\nfrom pathlib import Path\n"
        f"with Path({str(repo / 'sentinel.jsonl')!r}).open('a') as stream:\n"
        "    stream.write(json.dumps({'pid':os.getpid(),'argv':sys.argv[1:],"
        "'cwd':os.getcwd()})+'\\n')\n"
        "if os.environ.get('S05_SENTINEL_MODE') == 'timeout':\n    time.sleep(60)\n"
        "sys.stdout.write('o'*9000)\nsys.stderr.write('e'*9000)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(executable.parent) + os.pathsep + os.environ["PATH"])
    return repo


def config(repo: Path, **kwargs: Any) -> Config:
    return Config(repo=repo, memex_root=repo.parent / "memex", **kwargs)


@asynccontextmanager
async def host(repo: Path, enabled: str = ""):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_aci", "serve", "--repo", str(repo)],
        env={
            "PYTHONPATH": str(ROOT / "mcp-server/src"),
            "PATH": os.environ["PATH"],
            "ATLAS_ACI_ENABLE_TEST_EXECUTION": enabled,
        },
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        async with asyncio.timeout(10):
            await session.initialize()
            yield session


def payload(response: Any) -> dict[str, Any]:
    return json.loads(response.content[0].text)


async def test_default_no_spawn(repository: Path) -> None:
    cfg = config(repository)
    enforcement, memex, graph = build_server_state(cfg)
    marker = repository / "sentinel.jsonl"
    before = marker.read_bytes()
    requests = [
        {"path": "test_case.py"},
        {"path": "test_case.py", "enabled": True, "execution_profile": True},
        {},
        {"path": "../../outside.py"},
    ]
    for request in requests:
        with pytest.raises(ToolError) as direct:
            await run_test(request, cfg, enforcement)
        assert direct.value.code == "FORBIDDEN"
        assert "disabled" in direct.value.message
        with pytest.raises(ToolError) as dispatched:
            await dispatch_tool_call("test_dry_run", request, cfg, enforcement, memex, graph)
        assert dispatched.value.code == "FORBIDDEN"
    async with host(repository) as session:
        result = payload(await session.call_tool("test_dry_run", requests[1]))
        assert result["error"] == "FORBIDDEN"
        assert "disabled" in result["message"]
    assert marker.read_bytes() == before


async def test_explicit_enabled_mode(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ACI_ENABLE_TEST_EXECUTION", "1")
    cfg = config(repository, max_bytes_per_call=32)
    assert cfg.test_execution_enabled is True
    result = await run_test(
        {"path": "test_case.py", "case": "specific_case"}, cfg, Enforcement(cfg)
    )
    assert result == {
        "exit_code": 0,
        "stdout": "o" * 32,
        "stderr": "e" * 32,
        "truncated": True,
        "truncated_fields": ["stdout", "stderr"],
    }
    invocation = json.loads((repository / "sentinel.jsonl").read_text().splitlines()[-1])
    assert invocation["argv"] == [str(repository / "test_case.py"), "-k", "specific_case"]
    assert invocation["cwd"] == str(repository)
    with pytest.raises(ToolError) as denied:
        disabled = config(repository, test_execution_enabled=False)
        await run_test({"path": "test_case.py"}, disabled, Enforcement(disabled))
    assert denied.value.code == "FORBIDDEN"
    async with host(repository, "1") as session:
        wire = payload(await session.call_tool("test_dry_run", {"path": "test_case.py"}))
        assert wire["exit_code"] == 0
        assert len(wire["stdout"]) == len(wire["stderr"]) == 8192
        assert wire["truncated_fields"] == ["stdout", "stderr"]


async def test_timeout_preserved(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S05_SENTINEL_MODE", "timeout")
    cfg = config(repository, test_execution_enabled=True, test_dry_run_timeout_s=1)
    with pytest.raises(ToolError) as timeout:
        await run_test({"path": "test_case.py"}, cfg, Enforcement(cfg))
    assert timeout.value.code == "TIMEOUT"
    child = json.loads((repository / "sentinel.jsonl").read_text().splitlines()[-1])
    with pytest.raises(ProcessLookupError):
        os.kill(child["pid"], 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(child["pid"], os.WNOHANG)


async def test_profile_visible(repository: Path) -> None:
    for enabled in (False, True):
        cfg = config(repository, test_execution_enabled=enabled)
        descriptor = next(t for t in build_tool_manifest(cfg) if t["name"] == "test_dry_run")
        assert descriptor["_meta"][META] is enabled
        assert ("enabled" if enabled else "disabled") in descriptor["description"]
        async with host(repository, "1" if enabled else "") as session:
            wire = (await session.list_tools()).model_dump(mode="json", by_alias=True)
            tool = next(t for t in wire["tools"] if t["name"] == "test_dry_run")
            assert tool["_meta"][META] is enabled
            assert tool.get("annotations", {}).get("readOnlyHint") is not True
            view = payload(
                await session.call_tool(
                    "view_file", {"path": "test_case.py", "start_line": 1, "end_line": 1}
                )
            )
            assert "error" not in view
            search = payload(
                await session.call_tool(
                    "search_text", {"pattern": "harmless", "scope": "test_case.py"}
                )
            )
            assert "error" not in search
            assert search["matches"]


async def test_denial_replay(repository: Path) -> None:
    marker = repository / "sentinel.jsonl"
    before = marker.read_bytes()
    async with host(repository) as session:
        results = [
            payload(await session.call_tool("test_dry_run", {"path": "test_case.py"}))
            for _ in range(2)
        ]
    assert results[0] == results[1]
    assert results[0]["error"] == "FORBIDDEN"
    assert marker.read_bytes() == before


@pytest.mark.parametrize("value", [None, "", "0", "false", "true", "yes", " 1", "1 ", "01", "1"])
def test_only_exact_operator_value_enables(
    repository: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv("ATLAS_ACI_ENABLE_TEST_EXECUTION", value)
    assert config(repository).test_execution_enabled is (value == "1")


async def test_environment_is_snapshotted_per_config(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disabled = config(repository)
    monkeypatch.setenv("ATLAS_ACI_ENABLE_TEST_EXECUTION", "1")
    enabled = config(repository)
    assert enabled.test_execution_enabled is True
    assert disabled.test_execution_enabled is False
    with pytest.raises(ToolError) as result:
        await run_test({}, disabled, Enforcement(disabled))
    assert result.value.code == "FORBIDDEN"
