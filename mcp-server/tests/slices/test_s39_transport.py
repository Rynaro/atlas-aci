"""ACI-S39: the wrapper stage order, SDK-owned statuses, the ticket, parity and the SDK client."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import test_s38_support as s38
import test_s39_support as sup

from atlas_aci.protocol import http

SERVER_PY = Path(http.__file__).resolve().parents[1] / "server.py"
STATUS_ARGS = {"schema_version": sup.GRAPH, "op": "status"}


@pytest.fixture(scope="module")
def labs(tmp_path_factory: pytest.TempPathFactory) -> Any:
    return sup.build_labs(tmp_path_factory.mktemp("s39t"))


def check_rejection(response: sup.HttpResponse, status: int, reason: str) -> dict[str, Any]:
    assert response.status == status, (response.status, response.text)
    body = response.json()
    assert set(body) == {"jsonrpc", "id", "error"}
    assert body["jsonrpc"] == "2.0" and body["id"] == "server-error"
    assert set(body["error"]) == {"code", "message", "data"}
    assert body["error"]["data"] == {"reason": reason}
    assert reason in http.REJECTION_REASONS
    assert body["error"]["code"] == (-32000 if reason == "inflight_limit" else -32600)
    assert (response.header("connection") or "").lower() == "close"
    assert (response.header("content-type") or "").startswith("application/json")
    return body


# ----------------------------------------------------------------------------- stage order
async def test_stage_order_matrix(labs: Any) -> None:
    app = sup.make_app(labs.mini)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        good = f"{host}:{port}"
        evil = [("Origin", "http://evil.example")]
        rpc = client._rpc_headers(None, None)
        cases: list[tuple[str, str, str, list[tuple[str, str]], int, str]] = [
            (
                "host+origin",
                "POST",
                "/mcp",
                [("Host", "evil.example:1"), *evil],
                421,
                "host_not_allowed",
            ),
            ("origin+path", "POST", "/x", [("Host", good), *evil], 403, "origin_not_allowed"),
            ("path+method", "GET", "/x", [("Host", good)], 404, "path_not_found"),
            ("get", "GET", "/mcp", [("Host", good)], 405, "method_not_allowed"),
            ("delete", "DELETE", "/mcp", [("Host", good)], 405, "method_not_allowed"),
            ("options", "OPTIONS", "/mcp", [("Host", good)], 405, "method_not_allowed"),
            ("bare host", "POST", "/mcp", [("Host", host)], 421, "host_not_allowed"),
            (
                "other port",
                "POST",
                "/mcp",
                [("Host", f"{host}:{port + 1}")],
                421,
                "host_not_allowed",
            ),
            ("v6 host", "POST", "/mcp", [("Host", f"[::1]:{port}")], 421, "host_not_allowed"),
            ("trailing path", "POST", "/mcp/", [("Host", good)], 404, "path_not_found"),
            (
                "empty origin",
                "POST",
                "/mcp",
                [("Host", good), ("Origin", "")],
                403,
                "origin_not_allowed",
            ),
            (
                "duplicate origin",
                "POST",
                "/mcp",
                [("Host", good), ("Origin", "http://a.example"), ("Origin", "http://a.example")],
                403,
                "origin_not_allowed",
            ),
            (
                "null origin",
                "POST",
                "/mcp",
                [("Host", good), ("Origin", "null")],
                403,
                "origin_not_allowed",
            ),
        ]
        seen_reasons = set()
        for label, method, path, headers, status, reason in cases:
            if method == "POST":
                response = await client.request(
                    method,
                    path,
                    headers=[*headers, *rpc],
                    declared_length=2,
                    withhold_body=True,
                )
            else:
                response = await client.request(method, path, headers=headers)
            check_rejection(response, status, reason)
            seen_reasons.add(reason)
            if method == "GET":
                assert (response.header("allow") is not None) == (status == 405), label
        assert len(cases) >= 12 and seen_reasons >= {
            "host_not_allowed",
            "origin_not_allowed",
            "path_not_found",
            "method_not_allowed",
        }
        allow = await client.request("GET", "/mcp", headers=[("Host", good)])
        assert allow.header("allow") == "POST"
        # The same wrapper answers a good request on the same server afterwards.
        ok = await client.rpc("ping", version=sup.SELECTED)
        assert ok.status == 200 and ok.rpc(1)["result"] == {}


async def test_duplicate_host_header_is_refused(labs: Any) -> None:
    app = sup.make_app(labs.mini)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        good = f"{host}:{port}"
        response = await client.request(
            "POST",
            "/mcp",
            headers=[("Host", good), ("Host", good), *client._rpc_headers(None, None)],
            declared_length=2,
            withhold_body=True,
        )
        # uvicorn's HTTP parser refuses a repeated Host before any Atlas stage.
        assert response.status == 400 and b"Invalid HTTP request" in response.body
        assert app.gate.snapshot()["admitted"] == 0


async def test_host_case_is_compared_lowercase_by_the_wrapper_but_the_sdk_is_strict(
    labs: Any,
) -> None:
    """`localhost:P` passes; `LOCALHOST:P` passes the wrapper but the SDK is case-sensitive."""
    app = sup.make_app(labs.mini)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        lower = await client.rpc(
            "ping", version=sup.SELECTED, headers=[("Host", f"localhost:{port}")]
        )
        assert lower.status == 200
        upper = await client.rpc(
            "ping", version=sup.SELECTED, headers=[("Host", f"LOCALHOST:{port}")]
        )
        assert upper.status == 421  # recorded: the SDK answers, in plain text, not our JSON
        assert not (upper.header("content-type") or "").startswith("application/json")
        assert app.gate.snapshot()["admitted"] == 2  # both reached stage 6


async def test_peer_and_every_stage_answer_before_any_body_read(labs: Any) -> None:
    app = sup.make_app(labs.mini)
    try:
        host = ("127.0.0.1", 5555)
        good = [("Host", "127.0.0.1:5555")]
        cases = [
            (sup.asgi_scope(client=("192.0.2.1", 1), headers=good), 403, "peer_not_loopback"),
            (sup.asgi_scope(client=None, headers=good), 403, "peer_not_loopback"),
            (sup.asgi_scope(client=("not-an-ip", 1), headers=good), 403, "peer_not_loopback"),
            (sup.asgi_scope(client=("192.0.2.1", 1), headers=[]), 403, "peer_not_loopback"),
            (sup.asgi_scope(headers=[]), 421, "host_not_allowed"),
            (
                sup.asgi_scope(headers=[*good, ("Origin", "http://x.example")]),
                403,
                "origin_not_allowed",
            ),
            (sup.asgi_scope(path="/other", headers=good), 404, "path_not_found"),
            (sup.asgi_scope(method="PUT", headers=good), 405, "method_not_allowed"),
            (sup.asgi_scope(server=None, headers=good), 421, "host_not_allowed"),
            (sup.asgi_scope(headers=[*good, *good]), 421, "host_not_allowed"),
            (
                sup.asgi_scope(headers=[*good, ("Origin", "")]),
                403,
                "origin_not_allowed",
            ),
        ]
        for scope, status, reason in cases:
            got_status, headers, body, receives = await sup.asgi_call(app, scope)
            assert got_status == status, (reason, got_status)
            assert json.loads(body)["error"]["data"]["reason"] == reason
            assert headers["connection"] == "close"
            assert receives == 0  # receive() is never called before stage 6 passes
        # An IPv4-mapped loopback peer is a loopback peer: it proceeds to the next stage.
        mapped = sup.asgi_scope(client=("::ffff:127.0.0.1", 1), headers=[])
        assert (await sup.asgi_call(app, mapped))[0] == 421
        # Full gate: 503 with Retry-After, still before any receive().
        app.gate.active = app.gate.limit
        got_status, headers, body, receives = await sup.asgi_call(app, sup.asgi_scope(headers=good))
        assert (got_status, receives, headers["retry-after"]) == (503, 0, "1")
        assert json.loads(body)["error"] == {
            "code": -32000,
            "message": "Too many requests in flight; retry later.",
            "data": {"reason": "inflight_limit"},
        }
        assert app.gate.rejected == 1
        # A full gate does not outrank an earlier stage.
        got_status, *_ = await sup.asgi_call(app, sup.asgi_scope(method="GET", headers=good))
        assert got_status == 405 and app.gate.rejected == 1
        assert host == ("127.0.0.1", 5555)
        with pytest.raises(RuntimeError):
            await app({"type": "websocket"}, None, None)  # type: ignore[arg-type]
    finally:
        app.gate.active = 0
        app.state[2].close()


async def test_rejection_bodies_never_echo_header_values(labs: Any) -> None:
    app = sup.make_app(labs.mini)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        marker = "ZZ-secret-marker-ZZ"
        response = await client.request(
            "POST",
            "/mcp",
            headers=[("Host", f"{marker}.example:1"), ("Origin", f"http://{marker}.example")],
            declared_length=2,
            withhold_body=True,
        )
        check_rejection(response, 421, "host_not_allowed")
        assert marker not in response.text


# ----------------------------------------------------------------------------- SDK statuses
def padded_call(size: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "view_file", "arguments": {"path": "a.py", "pad": "x" * size}},
    }


async def test_sdk_owned_statuses(labs: Any, monkeypatch: Any) -> None:
    calls: list[str] = []
    original = http.call_tool_result

    async def spy(name: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(name)
        return await original(name, *args, **kwargs)

    monkeypatch.setattr(http, "call_tool_result", spy)
    app = sup.make_app(labs.mini, http_max_body_bytes=1024)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        headers = client._rpc_headers(sup.SELECTED, None)
        declared = await client.post(padded_call(2000), version=sup.SELECTED, tolerate_reset=True)
        assert declared.status == 413 and "too large" in declared.text.lower()
        withheld = await client.post(
            padded_call(2000), version=sup.SELECTED, withhold_body=True, declared_length=1025
        )
        assert withheld.status == 413  # answered with no body ever sent
        chunked = await client.request(
            "POST",
            "/mcp",
            headers=headers,
            chunks=[b"x" * 600, b"y" * 600],
            tolerate_reset=True,
        )
        assert chunked.status == 413
        assert calls == []  # no handler ran for any 413
        batch = await client.post(
            [{"jsonrpc": "2.0", "id": 1, "method": "ping"}], version=sup.SELECTED
        )
        assert batch.status == 400 and batch.json()["error"]["code"] == -32602
        garbage = await client.post(None, raw=b"{nope", version=sup.SELECTED)
        assert garbage.status == 400 and garbage.json()["error"]["code"] == -32700
        future = await client.rpc("tools/list", version="2099-01-01")
        assert future.status == 400 and "Unsupported protocol version" in future.text
        json_only = await client.rpc(
            "tools/list", version=sup.SELECTED, headers=[("Accept", "application/json")]
        )
        # Accept appears twice (ours first): use a dedicated request for a clean 406.
        assert json_only.status in (200, 406)
        no_sse = await client.request(
            "POST",
            "/mcp",
            headers=[("Content-Type", sup.JSON_TYPE), ("Accept", "application/json")],
            body=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
        )
        assert no_sse.status == 406
        plain = await client.request(
            "POST",
            "/mcp",
            headers=[("Content-Type", "text/plain"), ("Accept", sup.ACCEPT)],
            body=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
        )
        assert plain.status in (400, 415)  # recorded: the SDK's transport check answers
        ok = await client.rpc("ping", version=sup.SELECTED)
        assert ok.status == 200
        assert calls == []


# ----------------------------------------------------------------------------- gate and ticket
def test_ticket_refcount_and_single_decrement() -> None:
    gate = http.InflightGate(2)
    assert gate.snapshot() == {"limit": 2, "active": 0, "peak": 0, "admitted": 0, "rejected": 0}
    first, second = gate.try_admit(), gate.try_admit()
    assert first is not None and second is not None
    assert gate.try_admit() is None
    assert gate.snapshot() == {"limit": 2, "active": 2, "peak": 2, "admitted": 2, "rejected": 1}
    first.hold()
    first.hold()  # nested holds
    first.release()
    first.release()
    assert gate.active == 2 and not first.released  # the wrapper's own hold remains
    first.release()
    assert first.released and gate.active == 1
    first.release()  # idempotent: never a double decrement
    first.release()
    assert gate.active == 1
    with pytest.raises(http.RequestGone):
        first.hold()
    assert gate.active == 1  # a refused hold takes nothing
    second.release()
    assert gate.active == 0 and gate.peak == 2
    again = gate.try_admit()
    assert again is not None and gate.snapshot()["admitted"] == 3
    assert set(gate.snapshot()) == {"limit", "active", "peak", "admitted", "rejected"}


def request_context(scope: dict[str, Any] | None, version: str | None = None) -> Any:
    from mcp.server.lowlevel.server import request_ctx

    headers = {} if version is None else {"mcp-protocol-version": version}
    request = SimpleNamespace(scope=scope, headers=headers) if scope is not None else None
    return request_ctx.set(SimpleNamespace(request=request))  # type: ignore[arg-type]


def reset_context(token: Any) -> None:
    from mcp.server.lowlevel.server import request_ctx

    request_ctx.reset(token)


def call_request(arguments: dict[str, Any] | None = None) -> Any:
    from mcp import types

    return types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="graph_query", arguments=arguments or dict(STATUS_ARGS)
        ),
    )


async def test_nested_holds_balance_through_the_real_handlers(labs: Any) -> None:
    from mcp import types

    app = sup.make_app(labs.mini)
    try:
        ticket = app.gate.try_admit()
        assert ticket is not None
        token = request_context({http.TICKET_SCOPE_KEY: ticket}, sup.SELECTED)
        try:
            listed = await app.server.request_handlers[types.ListToolsRequest](
                types.ListToolsRequest(method="tools/list")
            )
            assert len(listed.root.tools) == 7
            result = await app.server.request_handlers[types.CallToolRequest](call_request())
        finally:
            reset_context(token)
        assert result.root.isError is False
        assert ticket._holds == 1 and not ticket.released and app.gate.active == 1
        ticket.release()
        assert ticket.released and app.gate.active == 0
    finally:
        app.state[2].close()


async def test_request_gone_surfaces_as_an_is_error_result_with_no_engine_work(
    labs: Any, monkeypatch: Any
) -> None:
    from mcp import types

    calls: list[str] = []

    async def spy(name: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(name)
        raise AssertionError("engine work must not start without a slot")

    monkeypatch.setattr(http, "call_tool_result", spy)
    app = sup.make_app(labs.mini)
    try:
        ticket = app.gate.try_admit()
        assert ticket is not None
        ticket.release()  # the HTTP exchange ended first
        assert app.gate.active == 0
        token = request_context({http.TICKET_SCOPE_KEY: ticket}, sup.SELECTED)
        try:
            result = await app.server.request_handlers[types.CallToolRequest](call_request())
        finally:
            reset_context(token)
        assert result.root.isError is True  # not a transport error
        assert "ended before the handler started" in result.root.content[0].text
        assert calls == [] and app.gate.active == 0
    finally:
        app.state[2].close()


async def test_cancel_before_the_first_step_takes_no_hold(labs: Any) -> None:
    from mcp import types

    app = sup.make_app(labs.mini)
    try:
        ticket = app.gate.try_admit()
        assert ticket is not None
        token = request_context({http.TICKET_SCOPE_KEY: ticket}, sup.SELECTED)
        try:
            task = asyncio.ensure_future(
                app.server.request_handlers[types.CallToolRequest](call_request())
            )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            reset_context(token)
        assert ticket._holds == 1 and not ticket.released
        ticket.release()
        assert app.gate.active == 0
    finally:
        app.state[2].close()


async def test_cancel_while_holding_releases_the_hold(labs: Any, monkeypatch: Any) -> None:
    from mcp import types

    hook = sup.TraversalHook().install(monkeypatch)
    app = sup.make_app(labs.live)
    try:
        ticket = app.gate.try_admit()
        assert ticket is not None
        token = request_context({http.TICKET_SCOPE_KEY: ticket}, sup.SELECTED)
        try:
            task = asyncio.ensure_future(
                app.server.request_handlers[types.CallToolRequest](call_request(sup.NEIGHBORHOOD_A))
            )
            assert await sup.poll(lambda: hook.inside == 1, 10)
            assert ticket._holds >= 2  # the handler holds while it works
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            reset_context(token)
        assert hook.cancelled == 1 and ticket._holds == 1
        ticket.release()
        assert app.gate.active == 0
    finally:
        app.state[2].close()


async def test_handlers_fail_closed_without_a_ticket(labs: Any) -> None:
    from mcp import types

    app = sup.make_app(labs.mini)
    try:
        for scope in (None, {}, {http.TICKET_SCOPE_KEY: "not-a-ticket"}):
            token = request_context(scope, sup.SELECTED)
            try:
                with pytest.raises(RuntimeError):
                    await app.server.request_handlers[types.ListToolsRequest](
                        types.ListToolsRequest(method="tools/list")
                    )
                result = await app.server.request_handlers[types.CallToolRequest](call_request())
                assert result.root.isError is True
            finally:
                reset_context(token)
        with pytest.raises(RuntimeError):  # no request context at all
            await app.server.request_handlers[types.ListToolsRequest](
                types.ListToolsRequest(method="tools/list")
            )
    finally:
        app.state[2].close()


# ----------------------------------------------------------------------------- parity
def decorator_sequence(path: Path, function: str) -> list[str]:
    tree = ast.parse(path.read_text())
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == function
    )
    return [
        ast.unparse(decorator)
        for inner in ast.walk(node)
        if isinstance(inner, ast.AsyncFunctionDef) and inner is not node
        for decorator in inner.decorator_list
    ]


def test_handler_decorators_match_run_stdio() -> None:
    stdio = decorator_sequence(SERVER_PY, "run_stdio")
    over_http = decorator_sequence(Path(http.__file__), "build_app")
    assert stdio == over_http
    assert len(stdio) == 5 and "server.call_tool(validate_input=False)" in stdio


async def test_runtime_handler_set_equals_run_stdio(labs: Any, monkeypatch: Any) -> None:
    import anyio

    from atlas_aci import server as stdio_server_module

    created: list[Any] = []
    real_server = stdio_server_module.Server

    class Recording(real_server):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    @contextlib.asynccontextmanager
    async def closed_streams():  # type: ignore[no-untyped-def]
        to_server, read_stream = anyio.create_memory_object_stream[Any](0)
        write_stream, from_server = anyio.create_memory_object_stream[Any](0)
        await to_server.aclose()
        try:
            yield read_stream, write_stream
        finally:
            await read_stream.aclose()
            await write_stream.aclose()
            await from_server.aclose()

    monkeypatch.setattr(stdio_server_module, "Server", Recording)
    monkeypatch.setattr(stdio_server_module, "stdio_server", closed_streams)
    config = sup.make_config(labs.mini)
    await asyncio.wait_for(stdio_server_module.run_stdio(config), 30)
    assert len(created) == 1
    app = http.build_app(config)
    try:
        assert len(created[0].request_handlers) >= 5
        assert set(app.server.request_handlers) == set(created[0].request_handlers)
        assert set(app.server.notification_handlers) == set(created[0].notification_handlers)
    finally:
        app.state[2].close()


async def test_session_version_follows_the_header(labs: Any) -> None:
    app = sup.make_app(labs.mini)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        shapes = {}
        for version in (*sup.REVISIONS, None):
            response = await client.rpc("tools/list", version=version)
            assert response.status == 200
            tools = response.rpc(1)["result"]["tools"]
            graph = next(t for t in tools if t["name"] == "graph_query")
            shapes[version] = "outputSchema" in graph
        assert shapes == {
            "2024-11-05": False,
            "2025-03-26": False,
            "2025-06-18": True,
            "2025-11-25": True,
            None: False,  # absent header: shaped as the SDK default, 2025-03-26
        }


# ----------------------------------------------------------------------------- uvicorn
def test_uvicorn_config_is_exactly_the_documented_one(labs: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    app = sup.make_app(labs.mini)
    try:
        config = http.uvicorn_config(app, "127.0.0.1", 0)
        assert config.host == "127.0.0.1" and config.port == 0
        assert config.interface == "asgi3" and config.lifespan == "off"
        assert config.proxy_headers is False and config.server_header is False
        assert config.log_config is None and config.access_log is False
        assert config.ws == "none" and config.http == "h11" and config.loop == "asyncio"
        assert config.workers == 1  # WEB_CONCURRENCY is ignored
        assert (config.timeout_graceful_shutdown, config.timeout_keep_alive) == (5, 5)
        assert config.fd is None and config.uds is None
        assert (http.SHUTDOWN_GRACE_S, http.KEEP_ALIVE_S) == (5, 5)
        assert http.uvicorn_config(app, "::1", 0).host == "::1"
        config.load()
        assert config.loaded_app is app  # the no-argument factory probe did not replace it
    finally:
        app.state[2].close()


def test_asgi_callable_has_exactly_three_required_positional_parameters() -> None:
    parameters = list(inspect.signature(http.AtlasHttpApp.__call__).parameters.values())
    assert [p.name for p in parameters] == ["self", "scope", "receive", "send"]
    assert all(p.default is inspect.Parameter.empty for p in parameters)
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in parameters)


# ----------------------------------------------------------------------------- no sessions
async def test_no_session_header_and_sdk_client_round_trip(labs: Any) -> None:
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamable_http_client

    app = sup.make_app(labs.mini)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        init = await client.rpc(
            "initialize",
            {
                "protocolVersion": sup.SELECTED,
                "capabilities": {},
                "clientInfo": {"name": "s39", "version": "1"},
            },
        )
        assert init.status == 200 and "mcp-session-id" not in init.header_names()
        again = await client.rpc("tools/list", version=sup.SELECTED)
        assert "mcp-session-id" not in again.header_names()
        async with asyncio.timeout(60):
            async with streamable_http_client(f"http://{host}:{port}/mcp") as (read, write, sid):
                async with ClientSession(read, write) as session:
                    result = await session.initialize()
                    assert result.protocolVersion == sup.SELECTED
                    tools = await session.list_tools()
                    assert len(tools.tools) == 7
                    called = await session.call_tool("graph_query", dict(STATUS_ARGS))
                    assert called.isError is False
                    assert isinstance(called, types.CallToolResult)
                    assert sid() is None
        assert await sup.poll(lambda: app.gate.active == 0)


async def test_cancel_notification_is_accepted_and_changes_nothing(
    labs: Any, monkeypatch: Any
) -> None:
    """Stateless `notifications/cancelled` is a no-op: 202, and the in-flight request completes."""
    hook = sup.TraversalHook().install(monkeypatch)
    app = sup.make_app(labs.live, http_max_inflight=2)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        stream = await client.open_stream(sup.tool_message("graph_query", sup.NEIGHBORHOOD_A, 7))
        assert stream.status == 200
        assert await sup.poll(lambda: hook.inside == 1, 10)
        note = await client.notify(
            "notifications/cancelled", {"requestId": 7, "reason": "test"}, version=sup.SELECTED
        )
        assert note.status == 202
        await asyncio.sleep(0.2)
        assert hook.cancelled == 0 and hook.inside == 1  # still running
        hook.release.set()
        finished = await sup.finish_stream(stream)
        result = finished.rpc(7)["result"]
        assert result["isError"] is False
        assert await sup.poll(lambda: app.gate.active == 0)
        await stream.disconnect()


# ----------------------------------------------------------------------------- signals
async def test_a_signal_drains_and_stops_without_being_reraised(labs: Any) -> None:
    """The `capture_signals` override, in the main thread: one signal stops, none is re-raised."""
    import os
    import signal

    import sse_starlette.sse as sse

    assert sse.AppStatus.original_handler is not None  # drain depends on the patched handle_exit
    app = sup.make_app(labs.mini)
    server = http.AtlasUvicornServer(http.uvicorn_config(app, "127.0.0.1", 0), install_signals=True)
    original = signal.getsignal(signal.SIGTERM)
    try:
        with server.capture_signals():
            assert signal.getsignal(signal.SIGTERM) is not original  # a handler is installed
            os.kill(os.getpid(), signal.SIGTERM)
            assert await sup.poll(lambda: server.should_exit, 5)
            assert sse.AppStatus.should_exit is True  # SSE streams are told to drain
            assert signal.getsignal(signal.SIGTERM) == original  # released: a repeat terminates
        assert server._captured_signals == [signal.SIGTERM]
    finally:
        sse.AppStatus.should_exit = False
        app.state[2].close()


def test_signal_handlers_are_never_installed_off_the_main_thread(labs: Any) -> None:
    import signal
    import threading

    app = sup.make_app(labs.mini)
    seen: dict[str, Any] = {}
    original = signal.getsignal(signal.SIGTERM)

    def work() -> None:
        async def go() -> None:
            server = http.AtlasUvicornServer(
                http.uvicorn_config(app, "127.0.0.1", 0), install_signals=True
            )
            with server.capture_signals():
                seen["inside"] = signal.getsignal(signal.SIGTERM)
            seen["ok"] = True

        asyncio.run(go())

    try:
        thread = threading.Thread(target=work)
        thread.start()
        thread.join(15)
        assert seen == {"inside": original, "ok": True}
    finally:
        app.state[2].close()


async def test_sigterm_stops_the_http_child_cleanly(labs: Any) -> None:
    import signal
    import time

    child = sup.HttpChild(
        sup.http_argv(s38.fresh_memex(labs.mini, "sigterm")), env=s38.child_env(0)
    )
    port = child.port
    assert port
    try:
        served = await child.client().rpc("ping", version=sup.SELECTED)
        assert served.status == 200  # it really was serving
        started = time.monotonic()
        child.proc.send_signal(signal.SIGTERM)
        code = child.wait(10)
        elapsed = time.monotonic() - started
    finally:
        child.kill()
    assert code == 0 and elapsed < 10, (code, elapsed)
    stderr = child.stderr_text()
    assert "http_listening" in stderr and "http_stopped" in stderr
    assert "Traceback" not in stderr
    assert sup.connection_refused(port)


STUBBORN_SERVER = """import asyncio, sys, structlog
from pathlib import Path
from types import SimpleNamespace
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
from atlas_aci.config import Config
from atlas_aci.protocol.http import run_http
from atlas_aci.query import traversal

async def stubborn(delay):
    while True:  # swallows cancellation: a teardown that never finishes by itself
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

traversal.asyncio = SimpleNamespace(sleep=stubborn)
repo, memex, graphs = map(Path, sys.argv[1:4])
asyncio.run(run_http(Config(repo=repo, memex_root=memex, graph_root=graphs, http_port=0)))
"""


async def test_a_second_signal_terminates_a_stuck_shutdown(labs: Any) -> None:
    import signal

    paths = s38.fresh_memex(labs.live, "stuck")
    argv = [
        sys.executable,
        "-c",
        STUBBORN_SERVER,
        str(paths.repo),
        str(paths.memex),
        str(paths.graphs),
    ]
    child = sup.HttpChild(argv, env=s38.child_env(0))
    assert child.port
    stream = None
    try:
        client = child.client()
        stream = await client.open_stream(sup.tool_message("graph_query", sup.NEIGHBORHOOD_A))
        assert stream.status == 200  # admitted; the handler now ignores cancellation
        await asyncio.sleep(0.6)
        child.proc.send_signal(signal.SIGTERM)
        await asyncio.sleep(1.5)
        assert child.proc.poll() is None  # the graceful stop is waiting on the stuck handler
        child.proc.send_signal(signal.SIGTERM)
        code = child.wait(5)
        assert code == -signal.SIGTERM, code  # the default disposition took over
    finally:
        if stream is not None:
            await stream.disconnect()
        child.kill()


# ----------------------------------------------------------------------------- the test client
def test_the_sse_parser_handles_crlf_comments_and_fields() -> None:
    text = (
        ": ping - 2026-01-01 00:00:00\r\n\r\n"
        'event: message\r\nid: 7\r\ndata: {"a":1}\r\n\r\n'
        ": another comment\n"
        "data: first\ndata: second\n\n"
        'retry: 100\r\ndata: {"b":2}\r\r'
        "data:tail-without-blank-line"
    )
    events = sup.parse_sse(text)
    assert [(e.event, e.id, e.data) for e in events] == [
        ("message", "7", '{"a":1}'),
        (None, None, "first\nsecond"),
        (None, None, '{"b":2}'),
        (None, None, "tail-without-blank-line"),
    ]
    assert events[2].retry == "100"
    assert sup.parse_sse(": only a comment\n\n") == []
