"""ACI-S39 acceptance: the six product criteria, each against a real server over real sockets."""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sqlite3
import sys
from typing import Any

import pytest
import test_s38_support as s38
import test_s39_support as sup

from atlas_aci.protocol import http

SDK_REVISION = "2025-11-25"
LIVE_PAGE = {
    "schema_version": sup.GRAPH,
    "op": "page",
    "query": {
        "schema_version": sup.GRAPH,
        "op": "neighborhood",
        "seed": {"name": "A"},
        "max_depth": 1,
        "deadline_ms": 2000,
    },
    "page_max_bytes": 18000,
}


@pytest.fixture(scope="module")
def labs(tmp_path_factory: pytest.TempPathFactory) -> Any:
    return sup.build_labs(tmp_path_factory.mktemp("s39c"))


@pytest.fixture(scope="module")
def http_mini(labs: Any):
    child = sup.HttpChild(sup.http_argv(s38.fresh_memex(labs.mini, "http")), env=s38.child_env(0))
    yield child
    child.close()


@pytest.fixture(scope="module")
def http_live(labs: Any):
    child = sup.HttpChild(sup.http_argv(s38.fresh_memex(labs.live, "http")), env=s38.child_env(0))
    yield child
    child.close()


def tool_result(body: dict[str, Any]) -> dict[str, Any]:
    result = body["result"]
    return {
        "text": result["content"][0]["text"],
        "isError": result["isError"],
        "structuredContent": result.get("structuredContent"),
    }


TOKEN = re.compile(r"token=[^\"&\s\\]+")


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        return TOKEN.sub("token=<lease-token>", value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


# ----------------------------------------------------------------------------- AC-001
async def http_conversation(
    client: sup.RawHttp, steps: list[tuple[int, str, str, str, Any]], revision: str
) -> dict[int, dict[str, Any]]:
    bodies: dict[int, dict[str, Any]] = {}
    for step, _name, method, kind, params in steps:
        if kind == "notification":
            response = await client.notify(method, params, version=revision)
            assert response.status == 202 and response.body == b"", step
            continue
        version = None if method == "initialize" else revision
        response = await client.rpc(method, params, request_id=step, version=version)
        assert response.status == 200, (step, response.text[:200])
        assert (response.header("content-type") or "").startswith("text/event-stream")
        bodies[step] = response.rpc(step)
    return bodies


async def live_http(client: sup.RawHttp, revision: str) -> dict[str, Any]:
    await client.initialize(revision)
    assert (await client.notify("notifications/initialized", version=revision)).status == 202
    page = (await client.tool("graph_query", LIVE_PAGE, request_id=3, version=revision)).rpc(3)
    payload = json.loads(page["result"]["content"][0]["text"])
    descriptor = payload["data"]["resources"][0]
    read = await client.rpc(
        "resources/read", {"uri": descriptor["uri"]}, request_id=4, version=revision
    )
    resource = read.rpc(4)["result"]["contents"][0]
    return {
        "page": page["result"],
        "payload": payload,
        "descriptor": descriptor,
        "resource": resource,
    }


@pytest.mark.parametrize("revision", ["2025-11-25", "2024-11-05"])
async def test_http_query_equivalence(
    labs: Any, http_mini: Any, http_live: Any, revision: str
) -> None:
    case = "modern.2025-11-25" if revision == "2025-11-25" else "legacy.2024-11-05"
    steps = s38.steps_for(case)
    stdio_paths = s38.fresh_memex(labs.mini, f"stdio-{revision}")
    stdio_client = s38.RawLineClient(stdio_paths.serve_argv(), env=s38.child_env(0), deadline_s=120)
    try:
        exchanges = s38.run_steps(stdio_client, steps)
    finally:
        stdio_client.close()
    stdio = {x.step: json.loads(x.raw) for x in exchanges if x.raw is not None}
    over_http = await http_conversation(http_mini.client(), steps, revision)
    assert set(stdio) == set(over_http) and len(stdio) == 16

    compared = 0
    # The initialize result: protocolVersion, serverInfo, capabilities.
    for key in ("protocolVersion", "serverInfo", "capabilities"):
        assert over_http[1]["result"][key] == stdio[1]["result"][key], key
        compared += 1
    assert over_http[1]["result"]["protocolVersion"] == revision
    # tools/list digest, the resource lists and ping.
    assert (
        s38.tools_digest(over_http[4]["result"])["tools_digest"]
        == s38.tools_digest(stdio[4]["result"])["tools_digest"]
    )
    assert len(stdio[4]["result"]["tools"]) == 7
    assert over_http[5]["result"] == stdio[5]["result"] == {"resources": []}
    assert over_http[6]["result"] == stdio[6]["result"] and stdio[6]["result"]["resourceTemplates"]
    assert over_http[3]["result"] == stdio[3]["result"] == {}
    compared += 4
    # Tool results, steps 7-16: text bytes, isError and structuredContent.
    for step in s38.TOOL_STEPS:
        mine, theirs = tool_result(over_http[step]), tool_result(stdio[step])
        assert mine["text"].encode() == theirs["text"].encode(), step
        assert mine["isError"] == theirs["isError"] == s38.EXPECTED_IS_ERROR[step], step
        assert mine["structuredContent"] == theirs["structuredContent"], step
        assert theirs["text"], step  # non-empty payloads
        modern = revision in s38.MODERN_REVISIONS
        if step in (7, 8, 11, 12, 13):  # graph_query successes: structured iff modern
            assert (mine["structuredContent"] is not None) == modern, step
        elif step in (9, 10):
            assert mine["structuredContent"] is None, step
        compared += 1
    # Step 17: the same JSON-RPC error object.
    assert over_http[17]["error"] == stdio[17]["error"] and stdio[17]["error"]["code"] == -32601
    compared += 1
    assert compared >= 10

    # The live step: page -> resources/read on a repository large enough to page.
    live_stdio = s38.run_live(s38.fresh_memex(labs.live, f"stdio-live-{revision}"), revision)
    live_over_http = await live_http(http_live.client(), revision)
    blob = live_stdio["resource"]["blob"]
    assert blob and live_over_http["resource"]["blob"] == blob
    assert (
        live_over_http["resource"]["_meta"]["generation_id"]
        == live_stdio["resource"]["_meta"]["generation_id"]
    )
    assert live_stdio["resource"]["_meta"]["generation_id"]
    # The resource URI carries a per-process lease token (authority); it is scrubbed, nothing else.
    mine, theirs = s38.Normaliser(), s38.Normaliser()
    assert mine.result("tools/call", scrub(live_over_http["page"])) == theirs.result(
        "tools/call", scrub(live_stdio["page"])
    )
    assert mine.value(scrub(live_over_http["descriptor"])) == theirs.value(
        scrub(live_stdio["descriptor"])
    )
    assert "token=" in live_stdio["descriptor"]["uri"]  # the scrub removed something real


async def test_absent_header_tool_call_equals_the_2025_03_26_stdio_shaping(
    labs: Any, http_mini: Any
) -> None:
    """A request without `mcp-protocol-version` is shaped as 2025-03-26 (legacy), as in stdio."""
    steps = s38.steps_for("legacy.2025-03-26")
    stdio_client = s38.RawLineClient(
        s38.fresh_memex(labs.mini, "stdio-absent").serve_argv(), env=s38.child_env(0)
    )
    try:
        exchanges = s38.run_steps(stdio_client, steps)
    finally:
        stdio_client.close()
    stdio = {x.step: json.loads(x.raw) for x in exchanges if x.raw is not None}
    client = http_mini.client()
    compared = 0
    rows = {row[0]: row for row in steps}
    for step in (7, 8, 9, 10, 11, 12, 13):
        params = rows[step][4]
        response = await client.rpc("tools/call", params, request_id=step, version=None)
        mine = tool_result(response.rpc(step))
        theirs = tool_result(stdio[step])
        assert mine == theirs, step
        assert mine["structuredContent"] is None and mine["text"]
        compared += 1
    assert compared == 7
    listed = await client.rpc("tools/list", version=None)
    assert (
        s38.tools_digest(listed.rpc(1)["result"])["tools_digest"]
        == s38.tools_digest(stdio[4]["result"])["tools_digest"]
    )


async def test_sdk_host_matches_stdio(labs: Any, http_mini: Any) -> None:
    """The mcp 1.30.0 `streamable_http_client` host sees what the stdio host sees."""
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamable_http_client

    steps = {row[0]: row for row in s38.steps_for("modern.2025-11-25")}
    stdio_client = s38.RawLineClient(
        s38.fresh_memex(labs.mini, "stdio-sdk").serve_argv(), env=s38.child_env(0)
    )
    try:
        stdio = {
            x.step: json.loads(x.raw)
            for x in s38.run_steps(stdio_client, list(steps.values()))
            if x.raw is not None
        }
    finally:
        stdio_client.close()
    seen = 0
    async with asyncio.timeout(90):
        async with streamable_http_client(f"http://{sup.LOOPBACK}:{http_mini.port}/mcp") as (
            read,
            write,
            _sid,
        ):
            async with ClientSession(read, write) as session:
                result = await session.send_request(
                    types.ClientRequest(
                        types.InitializeRequest(
                            params=types.InitializeRequestParams(
                                protocolVersion=SDK_REVISION,
                                capabilities=types.ClientCapabilities(),
                                clientInfo=types.Implementation(name="s39-sdk-host", version="1"),
                            )
                        )
                    ),
                    types.InitializeResult,
                )
                await session.send_notification(
                    types.ClientNotification(types.InitializedNotification())
                )
                assert result.protocolVersion == SDK_REVISION
                for step in range(7, 14):
                    _, name, _, _, params = steps[step]
                    called = await session.call_tool(params["name"], params["arguments"])
                    dump = called.model_dump(by_alias=True, mode="json", exclude_none=True)
                    raw = stdio[step]["result"]
                    assert sup.canon(dump) == sup.canon(raw), (step, name)
                    assert dump["content"][0]["text"]
                    seen += 1
    assert seen == 7


# ----------------------------------------------------------------------------- AC-002
def local_non_loopback_ipv4() -> str | None:
    """The address the OS would route to 192.0.2.1 from (a UDP connect sends no packet)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address


async def test_loopback_default(labs: Any, monkeypatch: Any) -> None:
    config = sup.make_config(labs.mini, http_port=0)
    assert type(config).__dataclass_fields__["http_host"].default == "127.0.0.1"
    assert config.http_host == "127.0.0.1"
    created: list[Any] = []
    original = http.AtlasUvicornServer

    class Recording(original):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(http, "AtlasUvicornServer", Recording)
    seen: list[list[tuple[str, int]]] = []
    task = asyncio.ensure_future(
        http.run_http(config, on_listening=seen.append, install_signals=False)
    )
    assert await sup.poll(lambda: seen or task.done(), 20)
    assert seen, "run_http did not report listening"
    addresses = seen[0]
    assert addresses and all(a == "127.0.0.1" for a, _ in addresses)
    import ipaddress

    assert all(ipaddress.ip_address(a).is_loopback for a, _ in addresses)
    port = addresses[0][1]
    assert port > 0
    response = await sup.RawHttp(sup.LOOPBACK, port).rpc("ping", version=sup.SELECTED)
    assert response.status == 200 and response.rpc(1)["result"] == {}
    if sys.platform == "linux":
        # Exactly one IPv4 loopback LISTEN row, none on tcp6; 127.0.0.2 is refused (not a wildcard).
        assert sup.listen_rows(port, "tcp") == [sup.expected_loopback_row(port)]
        assert sup.listen_rows(port, "tcp6") == []
        assert sup.connection_refused(port, "127.0.0.2")
    other = local_non_loopback_ipv4()
    checked = other is not None
    if checked:
        assert sup.connection_refused(port, other)  # type: ignore[arg-type]
    if os.environ.get("ATLAS_S39_REQUIRE_NONLOOPBACK") == "1":
        assert checked, "the gate expects a non-loopback interface to probe"
    created[0].should_exit = True
    await asyncio.wait_for(task, 20)
    assert sup.connection_refused(port)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "192.0.2.1", "fe80::1%lo"])
def test_cli_refuses_a_non_loopback_host(labs: Any, host: str) -> None:
    argv = [
        sys.executable,
        "-m",
        "atlas_aci",
        "serve",
        "--repo",
        str(labs.mini.repo),
        "--transport",
        "http",
        "--port",
        "0",
        "--host",
        host,
    ]
    done = sup.run_cli(argv, env=s38.child_env(None), timeout=30)
    stderr = done.stderr.decode()
    assert done.returncode == 2, stderr
    assert done.stdout == b""
    assert "Usage:" in stderr and "reason:" in stderr
    assert "http_listening" not in stderr  # nothing was bound


async def test_hostname_resolving_to_a_public_address_never_listens(
    labs: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        http,
        "_getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))],
    )
    called: list[Any] = []
    with pytest.raises(http.LoopbackError) as caught:
        await http.run_http(
            sup.make_config(labs.mini, http_host="public.example", http_port=0),
            on_listening=called.append,
            install_signals=False,
        )
    assert caught.value.reason == "non_loopback_address" and called == []


# ----------------------------------------------------------------------------- AC-003
async def test_disconnect_cleanup(labs: Any, monkeypatch: Any) -> None:
    hook = sup.TraversalHook().install(monkeypatch)
    observer = sup.LeaseObserver().install(monkeypatch)
    app = sup.make_app(labs.live, http_max_inflight=2)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        stream = await client.open_stream(sup.tool_message("graph_query", sup.NEIGHBORHOOD_A))
        assert stream.status == 200
        assert await sup.poll(lambda: hook.inside == 1, 10), "the query never reached the gate"
        reader, conn, source = observer.readers[0], observer.conns[0], observer.sources[0]
        assert reader._validated_lease and source.fd is not None
        assert observer.open_leases == 1 and app.gate.active == 1
        await stream.disconnect()
        assert await sup.poll(
            lambda: (
                reader._closed
                and source.fd is None
                and observer.open_leases == 0
                and app.gate.active == 0
                and hook.cancelled == 1
            ),
            10,
        ), (reader._closed, source.fd, observer.open_leases, app.gate.snapshot(), hook.cancelled)
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        assert not hook.release.is_set()  # the query was cancelled, never completed
        # Control: the same request completes normally once the gate is open.
        hook.release.set()
        again = await client.tool("graph_query", sup.NEIGHBORHOOD_A)
        assert again.status == 200 and again.rpc(1)["result"]["isError"] is False
        assert observer.total == 2


async def test_disconnect_cleanup_page_registry(labs: Any, monkeypatch: Any) -> None:
    hook = sup.TraversalHook().install(monkeypatch)
    observer = sup.LeaseObserver().install(monkeypatch)
    app = sup.make_app(labs.live, http_max_inflight=2)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        stream = await client.open_stream(sup.tool_message("graph_query", LIVE_PAGE))
        assert await sup.poll(lambda: hook.inside == 1, 10)
        assert observer.open_leases >= 1
        await stream.disconnect()
        assert await sup.poll(
            lambda: hook.cancelled == 1 and observer.open_leases == 0 and app.gate.active == 0, 10
        )
        registry = app.state[2]._lease_registry
        assert registry.usage() == {
            "pending": 0,
            "active": 0,
            "state_bytes": 0,
            "referenced_pack_bytes": 0,
            "sqlite_handles": 0,
            "pack_fds": 0,
        }


# ----------------------------------------------------------------------------- AC-004
async def test_concurrency_bound(labs: Any, monkeypatch: Any) -> None:
    limit = 3
    hook = sup.TraversalHook().install(monkeypatch)
    observer = sup.LeaseObserver().install(monkeypatch)
    app = sup.make_app(labs.live, http_max_inflight=limit)
    message = sup.tool_message("graph_query", sup.NEIGHBORHOOD_A)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        admitted = [await client.open_stream(message) for _ in range(limit)]
        assert all(s.status == 200 for s in admitted)
        assert await sup.poll(lambda: observer.open_leases == limit and hook.inside == limit, 10)
        # Overflow: two ordinary posts and two with the body withheld are refused, not queued.
        for index in range(4):
            response = await client.post(
                message, version=sup.SELECTED, withhold_body=index >= 2, timeout=5
            )
            assert response.status == 503, index
            assert response.header("retry-after") == "1"
            assert response.json()["error"]["data"] == {"reason": "inflight_limit"}
            assert observer.open_leases == limit
        assert app.gate.rejected == 4 and app.gate.active == limit
        # The disconnect window: freeing one slot must never allow a 4th concurrent lease.
        await admitted[0].disconnect()
        extra = await client.open_stream(message)
        assert extra.status in (200, 503)
        assert observer.max_open_leases <= limit
        survivors = admitted[1:] + ([extra] if extra.status == 200 else [])
        hook.release.set()
        for stream in survivors:
            finished = await sup.finish_stream(stream)
            assert finished.rpc(1)["result"]["isError"] is False
        for stream in (*admitted, extra):
            await stream.disconnect()
        assert await sup.poll(lambda: app.gate.active == 0, 5)
        snapshot = app.gate.snapshot()
        assert snapshot["peak"] == limit
        assert observer.max_open_leases <= limit and hook.max_inside <= limit
        assert observer.open_leases == 0
        follow = await client.tool("graph_query", sup.NEIGHBORHOOD_A)
        assert follow.status == 200 and follow.rpc(1)["result"]["isError"] is False


# ----------------------------------------------------------------------------- AC-005
async def test_invalid_origin(labs: Any, monkeypatch: Any) -> None:
    allowed = "http://127.0.0.1:5173"
    observer = sup.LeaseObserver().install(monkeypatch)
    results: list[str] = []
    original = http.call_tool_result

    async def spy(name: str, *args: Any, **kwargs: Any) -> Any:
        results.append(name)
        return await original(name, *args, **kwargs)

    monkeypatch.setattr(http, "call_tool_result", spy)
    app = sup.make_app(labs.mini, http_allowed_origins=(allowed,))
    handled: list[Any] = []
    manager_original = app.manager.handle_request

    async def manager_spy(scope: Any, receive: Any, send: Any) -> None:
        handled.append(scope["path"])
        await manager_original(scope, receive, send)

    app.manager.handle_request = manager_spy
    status = {"schema_version": sup.GRAPH, "op": "status"}
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        bad = [
            "http://evil.example",
            "null",
            "http://127.0.0.1:5174",
            "HTTP://127.0.0.1:5173",
        ]
        for origin in bad:
            response = await client.post(
                sup.tool_message("graph_query", status),
                version=sup.SELECTED,
                headers=[("Origin", origin)],
                withhold_body=True,
            )
            assert response.status == 403, origin
            assert response.json()["error"]["data"] == {"reason": "origin_not_allowed"}
        wrong_host = await client.post(
            sup.tool_message("graph_query", status),
            version=sup.SELECTED,
            headers=[("Host", f"evil.example:{port}")],
            withhold_body=True,
        )
        assert wrong_host.status == 421
        assert wrong_host.json()["error"]["data"] == {"reason": "host_not_allowed"}
        # None of the rejected requests reached the SDK, the engine or a reader lease.
        assert handled == [] and results == [] and observer.total == 0
        assert app.gate.snapshot()["admitted"] == 0
        # Controls: the exact allowed Origin runs the query ...
        ok = await client.tool("graph_query", status, headers=[("Origin", allowed)])
        assert ok.status == 200 and ok.rpc(1)["result"]["isError"] is False
        assert handled == ["/mcp"] and results == ["graph_query"] and observer.total == 1
        # ... and so does an absent Origin.
        absent = await client.tool("graph_query", status)
        assert absent.status == 200
        assert len(handled) == 2 and len(results) == 2
    default = sup.make_app(labs.mini)
    async with sup.serving(default) as (host, port):
        client = sup.RawHttp(host, port)
        for origin in (allowed, "http://localhost:3000", "https://127.0.0.1"):
            response = await client.post(
                sup.tool_message("graph_query", status),
                version=sup.SELECTED,
                headers=[("Origin", origin)],
                withhold_body=True,
            )
            assert response.status == 403, origin


# ----------------------------------------------------------------------------- AC-006
async def test_http_replay(labs: Any, monkeypatch: Any) -> None:
    hook = sup.TraversalHook().install(monkeypatch)
    observer = sup.LeaseObserver().install(monkeypatch)
    app = sup.make_app(labs.live, http_max_inflight=2)
    message = sup.tool_message("graph_query", sup.NEIGHBORHOOD_A, request_id=11)
    async with sup.serving(app) as (host, port):
        client = sup.RawHttp(host, port)
        stream = await client.open_stream(message)  # attempt 1: gated, then dropped
        assert await sup.poll(lambda: hook.inside == 1, 10)
        await stream.disconnect()
        assert await sup.poll(
            lambda: hook.cancelled == 1 and observer.open_leases == 0 and app.gate.active == 0, 10
        )
        hook.passthrough = True
        first = await client.post(message, version=sup.SELECTED)
        second = await client.post(message, version=sup.SELECTED)
        assert first.status == second.status == 200
        data_lines = first.sse_data()
        assert data_lines and data_lines == second.sse_data()
        assert first.body == second.body  # the whole SSE body is byte-identical
        results = []
        for response in (first, second):
            result = response.rpc(11)["result"]
            assert result["isError"] is False
            payload = json.loads(result["content"][0]["text"])
            assert payload["data"]["stop_reason"] == "visited_node_limit"
            assert result["structuredContent"] == payload
            results.append(payload)
        commitment = results[0]["data"]["frontier"]["commitment"]
        assert len(commitment) == 64 and int(commitment, 16) >= 0
        assert commitment == results[1]["data"]["frontier"]["commitment"]
        for key in ("generation_id", "snapshot_id"):
            assert results[0][key] and results[0][key] == results[1][key]
        # Non-vacuity: a different deterministic frontier gives a different commitment.
        other = await client.tool(
            "graph_query", {**sup.NEIGHBORHOOD_A, "max_visited_nodes": 2}, request_id=11
        )
        different = json.loads(other.rpc(11)["result"]["content"][0]["text"])
        assert different["data"]["frontier"]["commitment"] != commitment
