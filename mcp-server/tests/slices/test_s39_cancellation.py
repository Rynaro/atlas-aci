"""ACI-S39 D9: cancelling search_text / test_dry_run (an HTTP disconnect does) kills the child."""

from __future__ import annotations

import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import test_s39_support as sup

from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.tools import search_text as search_module
from atlas_aci.tools import test_dry_run as dry_module

TOOLS = Path(search_module.__file__).resolve().parent


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    pass\n")
    (repo / "test_a.py").write_text("def test_a():\n    pass\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pidfile = tmp_path / "pid"
    for name in ("rg", "pytest"):
        sup.write_fake(bin_dir, name, sup.FAKE_HANG)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("ATLAS_FAKE_PIDFILE", str(pidfile))
    return SimpleNamespace(repo=repo, pidfile=pidfile, bin=bin_dir, root=tmp_path)


def config(lab: SimpleNamespace, **kwargs: Any) -> Config:
    return Config(
        repo=lab.repo,
        memex_root=lab.root / "memex",
        max_calls_per_minute=0,
        **kwargs,
    )


async def child_pid(lab: SimpleNamespace) -> int:
    assert await sup.poll(lambda: lab.pidfile.exists() and lab.pidfile.read_text().strip(), 10), (
        "the fake child never started"
    )
    pid = int(lab.pidfile.read_text())
    assert sup.alive(pid)  # non-vacuous: it is running before the cancel
    return pid


async def test_search_text_cancel_kills_the_child(lab: SimpleNamespace) -> None:
    cfg = config(lab)
    task = asyncio.ensure_future(
        search_module.search_text({"pattern": "x", "scope": "."}, cfg, Enforcement(cfg))
    )
    pid = await child_pid(lab)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await sup.poll(lambda: not sup.alive(pid), 5), "rg child survived the cancel"


async def test_test_dry_run_cancel_kills_the_immediate_child(lab: SimpleNamespace) -> None:
    cfg = config(lab, test_execution_enabled=True)
    task = asyncio.ensure_future(
        dry_module.test_dry_run({"path": "test_a.py"}, cfg, Enforcement(cfg))
    )
    pid = await child_pid(lab)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await sup.poll(lambda: not sup.alive(pid), 5), "test child survived the cancel"


async def test_test_dry_run_timeout_path_is_unchanged(lab: SimpleNamespace) -> None:
    cfg = config(lab, test_execution_enabled=True, test_dry_run_timeout_s=1)
    task = asyncio.ensure_future(
        dry_module.test_dry_run({"path": "test_a.py"}, cfg, Enforcement(cfg))
    )
    pid = await child_pid(lab)
    with pytest.raises(ToolError) as caught:
        await asyncio.wait_for(task, 20)
    assert caught.value.code == "TIMEOUT"
    assert await sup.poll(lambda: not sup.alive(pid), 5)


async def test_search_text_timeout_path_is_unchanged(
    lab: SimpleNamespace, monkeypatch: Any
) -> None:
    real_wait_for = asyncio.wait_for
    patched = SimpleNamespace(
        create_subprocess_exec=asyncio.create_subprocess_exec,
        subprocess=asyncio.subprocess,
        wait_for=lambda aw, timeout: real_wait_for(aw, 0.5),
    )
    monkeypatch.setattr(search_module, "asyncio", patched)
    cfg = config(lab)
    with pytest.raises(ToolError) as caught:
        await search_module.search_text({"pattern": "x", "scope": "."}, cfg, Enforcement(cfg))
    assert caught.value.code == "TIMEOUT"
    pid = int(lab.pidfile.read_text())
    assert await sup.poll(lambda: not sup.alive(pid), 5)


async def test_normal_completion_is_untouched(lab: SimpleNamespace) -> None:
    sup.write_fake(lab.bin, "rg", "exit 0\n")
    cfg = config(lab)
    result = await search_module.search_text({"pattern": "x", "scope": "."}, cfg, Enforcement(cfg))
    assert result == {"matches": []}


@pytest.mark.parametrize("name", ["search_text.py", "test_dry_run.py"])
def test_the_change_is_one_synchronous_kill_and_reraise(name: str) -> None:
    source = (TOOLS / name).read_text()
    tree = ast.parse(source)
    assert "import contextlib" in source
    tries = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Try)
        and [ast.unparse(h.type) if h.type else None for h in n.handlers]
        == ["TimeoutError", "BaseException"]
    ]
    assert len(tries) == 1
    handler = tries[0].handlers[1]
    assert not any(isinstance(n, ast.Await) for n in ast.walk(handler))  # no await on cancel
    assert isinstance(handler.body[-1], ast.Raise) and handler.body[-1].exc is None
    calls = [ast.unparse(n.func) for n in ast.walk(handler) if isinstance(n, ast.Call)]
    assert calls == ["contextlib.suppress", "proc.kill"]
    # The TimeoutError handler is unchanged in shape: it kills and raises the typed error.
    timeout = tries[0].handlers[0]
    assert "proc.kill()" in ast.unparse(timeout) and "TIMEOUT" in ast.unparse(timeout)


# ----------------------------------------------------------------------------- over HTTP
async def disconnect_kills_child(
    lab: SimpleNamespace, tool: str, arguments: Any, **kw: Any
) -> None:
    app = http_app(lab, **kw)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        stream = await client.open_stream(sup.tool_message(tool, arguments))
        assert stream.status == 200
        pid = await child_pid(lab)
        assert app.gate.active == 1
        await stream.disconnect()
        assert await sup.poll(lambda: not sup.alive(pid), 5), "child survived the disconnect"
        assert await sup.poll(lambda: app.gate.active == 0, 5)


def http_app(lab: SimpleNamespace, **kwargs: Any) -> Any:
    from atlas_aci.protocol.http import build_app

    return build_app(config(lab, **kwargs))


async def test_http_disconnect_kills_search_text_child(lab: SimpleNamespace) -> None:
    await disconnect_kills_child(lab, "search_text", {"pattern": "x", "scope": "."})


async def test_http_disconnect_kills_test_dry_run_child(lab: SimpleNamespace) -> None:
    await disconnect_kills_child(
        lab, "test_dry_run", {"path": "test_a.py"}, test_execution_enabled=True
    )
