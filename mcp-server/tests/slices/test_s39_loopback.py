"""ACI-S39: the loopback rule, the Config bounds and the CLI (no server needed for most cases)."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import test_s39_support as sup
from click.testing import CliRunner

from atlas_aci.__main__ import cli
from atlas_aci.config import Config
from atlas_aci.protocol import http

V4 = socket.AF_INET
V6 = socket.AF_INET6


def entry(address: str, scope: int = 0) -> tuple[Any, ...]:
    if ":" in address:
        return (V6, socket.SOCK_STREAM, 6, "", (address, 0, 0, scope))
    return (V4, socket.SOCK_STREAM, 6, "", (address, 0))


def resolver(*addresses: str):
    def fake(host: str, port: Any, **kwargs: Any):
        return [entry(a) for a in addresses]

    return fake


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    (path / "a.py").write_text("def a():\n    pass\n")
    return path


def config(repo: Path, **kwargs: Any) -> Config:
    return Config(repo=repo, memex_root=repo.parent / "memex", **kwargs)


# ----------------------------------------------------------------------------- resolve_loopback
@pytest.mark.parametrize(
    ("host", "literal"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.2", "127.0.0.2"),
        ("::1", "::1"),
        ("[::1]", "::1"),
        ("::ffff:127.0.0.1", "127.0.0.1"),
    ],
)
def test_resolve_accepts_loopback_literals(host: str, literal: str) -> None:
    assert http.resolve_loopback(host) == literal


def test_resolve_localhost_prefers_ipv4_and_accepts_v6_only(monkeypatch: Any) -> None:
    monkeypatch.setattr(http, "_getaddrinfo", resolver("::1", "127.0.0.1"))
    assert http.resolve_loopback("localhost") == "127.0.0.1"
    monkeypatch.setattr(http, "_getaddrinfo", resolver("::1"))
    assert http.resolve_loopback("localhost") == "::1"


@pytest.mark.parametrize(
    ("host", "reason"),
    [
        ("", "empty_host"),
        ("0.0.0.0", "wildcard_address"),
        ("::", "wildcard_address"),
        ("[::]", "wildcard_address"),
        ("192.0.2.1", "non_loopback_address"),
        ("10.0.0.1", "non_loopback_address"),
        ("::ffff:192.0.2.1", "non_loopback_address"),
        ("fe80::1%lo0", "scope_id"),
        (" 127.0.0.1", "unresolvable"),
        ("a" * 254, "unresolvable"),
        (None, "unresolvable"),
    ],
)
def test_resolve_rejects_with_exact_reason(host: Any, reason: str) -> None:
    with pytest.raises(http.LoopbackError) as caught:
        http.resolve_loopback(host)
    assert caught.value.reason == reason
    assert reason in str(caught.value)
    assert reason in http.LOOPBACK_REASONS


@pytest.mark.parametrize(
    ("addresses", "reason"),
    [
        (("127.0.0.1", "192.0.2.1"), "mixed_resolution"),
        (("192.0.2.1", "::1"), "mixed_resolution"),
        (("0.0.0.0",), "wildcard_address"),
        (("192.0.2.1",), "non_loopback_address"),
        ((), "unresolvable"),
    ],
)
def test_resolve_hostname_results(
    monkeypatch: Any, addresses: tuple[str, ...], reason: str
) -> None:
    monkeypatch.setattr(http, "_getaddrinfo", resolver(*addresses))
    with pytest.raises(http.LoopbackError) as caught:
        http.resolve_loopback("name.example")
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "error", [socket.gaierror(-2, "nope"), OSError("x"), UnicodeError("l"), ValueError("v")]
)
def test_resolve_resolver_errors_are_unresolvable(monkeypatch: Any, error: Exception) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(http, "_getaddrinfo", boom)
    with pytest.raises(http.LoopbackError) as caught:
        http.resolve_loopback("name.example")
    assert caught.value.reason == "unresolvable"


@pytest.mark.parametrize("host", ["a" * 64 + ".invalid", "a\x00b", "bad\u0000.example"])
def test_resolve_real_resolver_errors_are_unresolvable(host: str) -> None:
    """Over-long labels and NUL raise UnicodeError/ValueError, not gaierror, before any lookup."""
    with pytest.raises(http.LoopbackError) as caught:
        http.resolve_loopback(host)
    assert caught.value.reason == "unresolvable"


def test_resolve_scope_id_on_hostname_results(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        http, "_getaddrinfo", lambda *a, **k: [(V6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 3))]
    )
    with pytest.raises(http.LoopbackError) as caught:
        http.resolve_loopback("name.example")
    assert caught.value.reason == "scope_id"
    monkeypatch.setattr(
        http,
        "_getaddrinfo",
        lambda *a, **k: [(V6, socket.SOCK_STREAM, 6, "", ("fe80::1%lo", 0, 0, 0))],
    )
    with pytest.raises(http.LoopbackError) as caught:
        http.resolve_loopback("name.example")
    assert caught.value.reason == "scope_id"


def test_reasons_are_closed_and_unique() -> None:
    assert len(set(http.LOOPBACK_REASONS)) == len(http.LOOPBACK_REASONS) == 7
    assert len(set(http.REJECTION_REASONS)) == len(http.REJECTION_REASONS) == 6
    assert set(http.REJECTION_REASONS) == set(http._REJECTIONS)


def test_uvicorn_config_refuses_non_loopback(repo: Path) -> None:
    app = http.build_app(config(repo))
    try:
        for bad in ("0.0.0.0", "192.0.2.1", "::", "localhost", "127.0.0.1 ", "::ffff:127.0.0.1"):
            with pytest.raises(http.LoopbackError):
                http.uvicorn_config(app, bad, 0)
        assert http.uvicorn_config(app, "127.0.0.1", 0).host == "127.0.0.1"
    finally:
        app.state[2].close()


# ----------------------------------------------------------------------------- run_http race
async def test_dns_change_between_cli_check_and_bind_fails_closed(
    repo: Path, monkeypatch: Any
) -> None:
    answers = [["127.0.0.1"], ["192.0.2.1"]]

    def flip(host: str, port: Any, **kwargs: Any):
        return [entry(a) for a in answers.pop(0)]

    monkeypatch.setattr(http, "_getaddrinfo", flip)
    assert http.resolve_loopback("rebind.example") == "127.0.0.1"  # the CLI-time check
    built: list[Any] = []
    monkeypatch.setattr(http, "AtlasUvicornServer", lambda *a, **k: built.append(a))
    called: list[Any] = []
    with pytest.raises(http.LoopbackError) as caught:
        await http.run_http(
            config(repo, http_host="rebind.example", http_port=0),
            on_listening=called.append,
            install_signals=False,
        )
    assert caught.value.reason == "non_loopback_address"
    assert built == [] and called == []  # no listener was ever created
    assert answers == []  # the resolver really was consulted twice


async def test_literal_host_makes_no_resolver_calls(repo: Path, monkeypatch: Any) -> None:
    calls: list[Any] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("resolver must not be used for a literal")

    monkeypatch.setattr(http, "_getaddrinfo", spy)
    seen: list[Any] = []
    task = asyncio.ensure_future(
        http.run_http(
            config(repo, http_host="127.0.0.1", http_port=0),
            on_listening=seen.append,
            install_signals=False,
        )
    )
    assert await sup.poll(lambda: seen or task.done(), 15)
    assert seen, "run_http did not report listening"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == []
    assert seen[0][0][0] == "127.0.0.1"


async def test_post_bind_check_rejects_a_non_loopback_socket(repo: Path, monkeypatch: Any) -> None:
    real_port: list[int] = []
    original = http._listening_addresses

    def lying(server: Any) -> list[tuple[str, int]]:
        real_port.append(original(server)[0][1])
        return [("192.0.2.1", real_port[0])]

    monkeypatch.setattr(http, "_listening_addresses", lying)
    seen: list[Any] = []
    with pytest.raises(http.LoopbackError) as caught:
        await http.run_http(
            config(repo, http_port=0), on_listening=seen.append, install_signals=False
        )
    assert caught.value.reason == "bound_non_loopback"
    assert seen == []
    assert real_port and sup.connection_refused(real_port[0])  # it was shut down


async def test_fail_closed_when_test_execution_is_enabled(repo: Path) -> None:
    cfg = config(repo, test_execution_enabled=True, http_port=0)
    with pytest.raises(http.RefusedError, match="test execution"):
        await http.run_http(cfg, install_signals=False)


async def test_fail_closed_on_an_unverified_sdk(repo: Path, monkeypatch: Any) -> None:
    import importlib.metadata

    original = importlib.metadata.version

    def patched(name: str) -> str:
        return "1.28.1" if name == "mcp" else original(name)

    monkeypatch.setattr(importlib.metadata, "version", patched)
    with pytest.raises(http.RefusedError, match="verified mcp SDK"):
        await http.run_http(config(repo, http_port=0), install_signals=False)


# ----------------------------------------------------------------------------- Config bounds
def test_config_defaults_and_unchanged_construction(repo: Path) -> None:
    cfg = Config(repo=repo, memex_root=repo.parent / "m")
    assert (cfg.http_host, cfg.http_port, cfg.http_max_inflight) == ("127.0.0.1", 8765, 8)
    assert (cfg.http_max_body_bytes, cfg.http_allowed_origins) == (1048576, ())
    positional = Config(repo, repo.parent / "m2")
    assert positional.repo == repo.resolve()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("http_host", ""),
        ("http_host", "a" * 254),
        ("http_host", "bad host"),
        ("http_host", "fe80::1%lo"),
        ("http_host", "0.0.0.0"),
        ("http_host", "::"),
        ("http_host", "192.0.2.1"),
        ("http_host", "[10.0.0.1]"),
        ("http_host", 5),
        ("http_port", -1),
        ("http_port", 65536),
        ("http_port", True),
        ("http_port", 80),
        ("http_port", 1023),
        ("http_port", "80"),
        ("http_max_inflight", 0),
        ("http_max_inflight", 257),
        ("http_max_inflight", True),
        ("http_max_body_bytes", 1023),
        ("http_max_body_bytes", 16 * 1024 * 1024 + 1),
        ("http_max_body_bytes", 1.5),
        ("http_allowed_origins", ("*",)),
        ("http_allowed_origins", ("null",)),
        ("http_allowed_origins", ("http://a.example", "http://a.example")),
        ("http_allowed_origins", tuple(f"http://h{i}.example" for i in range(33))),
        ("http_allowed_origins", ("http://example.com/path",)),
        ("http_allowed_origins", ("HTTP://example.com",)),
        ("http_allowed_origins", ("http://example.com:99999",)),
        ("http_allowed_origins", ("http://example.com:0",)),
        ("http_allowed_origins", ("ftp://example.com",)),
        ("http_allowed_origins", ("http://",)),
        ("http_allowed_origins", "http://a.example"),
    ],
)
def test_config_rejects_out_of_bounds_http_fields(repo: Path, field: str, value: Any) -> None:
    with pytest.raises(ValueError, match=field):
        config(repo, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("http_host", "localhost"),
        ("http_host", "[::1]"),
        ("http_host", "::ffff:127.0.0.1"),
        ("http_host", "127.0.0.9"),
        ("http_port", 0),
        ("http_port", 1024),
        ("http_port", 65535),
        ("http_max_inflight", 1),
        ("http_max_inflight", 256),
        ("http_max_body_bytes", 1024),
        ("http_allowed_origins", ["http://127.0.0.1:5173", "https://[::1]:8443"]),
        ("http_allowed_origins", ("http://a.example",)),
    ],
)
def test_config_accepts_bounds_and_coerces_lists(repo: Path, field: str, value: Any) -> None:
    cfg = config(repo, **{field: value})
    got = getattr(cfg, field)
    assert got == (tuple(value) if field == "http_allowed_origins" else value)


def test_config_caps_the_buffered_product(repo: Path) -> None:
    config(repo, http_max_inflight=16, http_max_body_bytes=16 * 1024 * 1024)  # exactly 256 MiB
    config(repo, http_max_inflight=256, http_max_body_bytes=1024 * 1024)
    with pytest.raises(ValueError, match="http_max_inflight times http_max_body_bytes"):
        config(repo, http_max_inflight=17, http_max_body_bytes=16 * 1024 * 1024)
    with pytest.raises(ValueError, match="http_max_inflight times http_max_body_bytes"):
        config(repo, http_max_inflight=256, http_max_body_bytes=2 * 1024 * 1024)


# ----------------------------------------------------------------------------- CLI
def invoke(repo: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    runner = CliRunner()
    return runner.invoke(cli, ["serve", "--repo", str(repo), *args], env=env)


@pytest.mark.parametrize(
    "option",
    [
        ("--host", "127.0.0.1"),
        ("--port", "9000"),
        ("--max-inflight", "4"),
        ("--max-body-bytes", "2048"),
        ("--allowed-origins", "http://a.example"),
    ],
)
def test_http_options_require_the_http_transport(repo: Path, option: tuple[str, str]) -> None:
    for extra in ((), ("--transport", "stdio")):
        result = invoke(repo, *extra, *option)
        assert result.exit_code == 2
        assert "require --transport http" in result.output


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("--allowed-origins", "*"), "http_allowed_origins"),
        (("--allowed-origins", "null"), "http_allowed_origins"),
        (("--allowed-origins", ""), "http_allowed_origins"),
        (("--allowed-origins", "http://a.example,http://a.example"), "http_allowed_origins"),
        (("--port", "80"), "http_port"),
        (("--port", "1023"), "http_port"),
        (("--max-inflight", "256", "--max-body-bytes", "2097152"), "http_max_inflight times"),
    ],
)
def test_config_errors_are_usage_errors(repo: Path, args: tuple[str, ...], needle: str) -> None:
    result = invoke(repo, "--transport", "http", *args)
    assert result.exit_code == 2, result.output
    assert needle in result.output


@pytest.mark.parametrize(
    "args",
    [
        ("--port", "70000"),
        ("--max-inflight", "0"),
        ("--max-inflight", "257"),
        ("--max-body-bytes", "10"),
    ],
)
def test_click_ranges_are_usage_errors(repo: Path, args: tuple[str, ...]) -> None:
    result = invoke(repo, "--transport", "http", *args)
    assert result.exit_code == 2


def test_test_execution_fails_closed_before_any_bind(repo: Path) -> None:
    result = invoke(
        repo, "--transport", "http", "--port", "0", env={"ATLAS_ACI_ENABLE_TEST_EXECUTION": "1"}
    )
    assert result.exit_code == 2
    assert "test execution is enabled" in result.output


def test_help_lists_the_new_options(repo: Path) -> None:
    out = invoke(repo, "--help").output
    for option in (
        "--transport",
        "--host",
        "--port",
        "--max-inflight",
        "--max-body-bytes",
        "--allowed-origins",
    ):
        assert option in out
    assert "--frameworks" not in out and "--rails-table-map" not in out


def test_cli_bind_conflict_exits_1_with_fixed_text(repo: Path, tmp_path: Path) -> None:
    from test_s38_support import child_env

    with socket.socket() as holder:
        holder.bind((sup.LOOPBACK, 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        paths = SimpleNamespace(repo=repo, graphs=tmp_path / "graphs", memex=tmp_path / "memex")
        argv = sup.http_argv(paths)
        argv[argv.index("--port") + 1] = str(port)
        done = sup.run_cli(argv, env=child_env(None), timeout=60)
    stderr = done.stderr.decode()
    assert done.returncode == 1, stderr
    assert "could not start" in stderr and "Traceback" not in stderr
    assert done.stdout == b""
