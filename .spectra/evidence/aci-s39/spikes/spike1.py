import asyncio, json, sys, tempfile, time, os
from pathlib import Path
sys.path.insert(0, "/ws/mcp-server/tests/slices")
import test_s39_support as sup
import test_s38_support as s38

OUT = {}


class MP:
    def __init__(self): self.undo = []
    def setattr(self, obj, name, value):
        self.undo.append((obj, name, getattr(obj, name))); setattr(obj, name, value)
    def close(self):
        for o, n, v in reversed(self.undo): setattr(o, n, v)


async def spike_d():
    import uvicorn
    seen = {}
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        seen["server"] = scope["server"]; seen["client"] = scope["client"]; seen["scheme"] = scope["scheme"]
        body = b"ok"
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
        await send({"type": "http.response.body", "body": body})
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=False, ws="none", interface="asgi3")
    server = uvicorn.Server(cfg)
    server.capture_signals = lambda: __import__("contextlib").nullcontext()
    task = asyncio.ensure_future(server.serve())
    assert await sup.poll(lambda: server.started, 10)
    port = server.servers[0].sockets[0].getsockname()[1]
    r = await sup.RawHttp("127.0.0.1", port).request("POST", "/x", body=b"{}")
    server.should_exit = True
    await asyncio.wait_for(task, 10)
    OUT["d"] = {"status": r.status, "scope_server": list(seen["server"]), "scope_client": list(seen["client"]), "scope_server_port_matches_bind": seen["server"][1] == port, "python": sys.version.split()[0]}


async def spike_efg(root):
    from types import SimpleNamespace
    from atlas_aci.protocol import http
    paths = s38.build_fixture(root / "live", source=s38.LIVE_SOURCE)
    mp = MP()
    hook = sup.TraversalHook().install(mp)
    obs = sup.LeaseObserver().install(mp)
    app = sup.make_app(paths, http_max_inflight=2)
    # (f) scope identity: wrap manager.handle_request and the CallTool handler
    rec = {"wrapper_scope_is_request_scope": None, "ticket_in_scope": None}
    orig_hr = app.manager.handle_request
    wrapper_scopes = []
    async def hr(scope, receive, send):
        wrapper_scopes.append(scope)
        return await orig_hr(scope, receive, send)
    app.manager.handle_request = hr
    from mcp import types
    handler = app.server.request_handlers[types.CallToolRequest]
    order = []
    async def wrapped(req):
        r = app.server.request_context.request
        rec["wrapper_scope_is_request_scope"] = any(r.scope is s for s in wrapper_scopes)
        rec["ticket_in_scope"] = http.TICKET_SCOPE_KEY in r.scope
        try:
            return await handler(req)
        finally:
            order.append(("handler_unwind_done", time.monotonic(), app.gate.active))
    app.server.request_handlers[types.CallToolRequest] = wrapped
    orig_rel = http.Ticket.release
    def rel(self):
        order.append(("ticket.release_called", time.monotonic(), app.gate.active, self._holds))
        orig_rel(self)
    mp.setattr(http.Ticket, "release", rel)
    msg = sup.tool_message("graph_query", sup.NEIGHBORHOOD_A)
    async with sup.serving(app) as (host, port):
        c = sup.RawHttp(host, port)
        s = await c.open_stream(msg)
        assert await sup.poll(lambda: hook.inside == 1, 10), "handler never reached the gate"
        r = obs.readers[0]
        before = {"validated": bool(r._validated_lease), "fd_open": r._source_lease.fd is not None, "open_leases": obs.open_leases, "gate_active": app.gate.active}
        t0 = time.monotonic()
        await s.disconnect()
        ok = await sup.poll(lambda: hook.cancelled == 1, 10, 0.005)
        latency = time.monotonic() - t0
        closed = await sup.poll(lambda: r._closed and obs.open_leases == 0 and app.gate.active == 0, 10, 0.005)
        total = time.monotonic() - t0
        import sqlite3
        try:
            obs.conns[0].execute("SELECT 1"); conn_closed = False
        except sqlite3.ProgrammingError:
            conn_closed = True
        OUT["e"] = {"before": before, "cancelled_seen": ok, "cancel_latency_s": round(latency, 4), "reader_closed": r._closed, "source_fd_none": obs.sources[0].fd is None, "conn_closed": conn_closed, "open_leases_after": obs.open_leases, "gate_active_after": app.gate.active, "all_released_latency_s": round(total, 4), "release_never_set": not hook.release.is_set()}
        OUT["f"] = {**rec, "order": [(o[0], round(o[1] - t0, 4), *o[2:]) for o in order]}
        # control: json_response=True (a fresh app, same state)
    OUT["e_control"] = await control_json(paths, mp)
    mp.close()
    await spike_l(paths)


async def control_json(paths, mp):
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    hook = sup.TraversalHook().install(mp)
    app = sup.make_app(paths, http_max_inflight=2)
    app.manager = StreamableHTTPSessionManager(app.server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*", "localhost:*"]))
    # json_response needs only Accept json
    msg = sup.tool_message("graph_query", sup.NEIGHBORHOOD_A)
    result = {}
    async with sup.serving(app) as (host, port):
        reader, writer = await asyncio.open_connection(host, port)
        body = json.dumps(msg).encode()
        head = sup._headers_block("POST", "/mcp", f"{host}:{port}", [("Content-Type", "application/json"), ("Accept", "application/json, text/event-stream"), ("mcp-protocol-version", "2025-11-25")], len(body), False)
        writer.write(head + body); await writer.drain()
        assert await sup.poll(lambda: hook.inside == 1, 10)
        await sup.close_writer(writer)
        await asyncio.sleep(2.0)
        result = {"cancelled_after_2s": hook.cancelled, "still_inside": hook.inside}
        hook.release.set()
        await sup.poll(lambda: app.gate.active == 0, 10)
    return result


async def spike_l(paths):
    from types import SimpleNamespace
    app = sup.make_app(paths, http_max_inflight=2)
    msg = sup.tool_message("graph_query", sup.NEIGHBORHOOD_A)
    msg2 = sup.tool_message("graph_query", {**sup.NEIGHBORHOOD_A, "max_visited_nodes": 2})
    async with sup.serving(app) as (host, port):
        c = sup.RawHttp(host, port)
        a = await c.post(msg, version="2025-11-25")
        b = await c.post(msg, version="2025-11-25")
        d = await c.post(msg2, version="2025-11-25")
        def payload(r):
            res = r.rpc(1)["result"]
            return json.loads(res["content"][0]["text"]), res
        pa, ra = payload(a); pb, rb = payload(b); pd, rd = payload(d)
        OUT["l"] = {"status": a.status, "isError": ra.get("isError"), "stop_reason": pa["data"].get("stop_reason"), "commit_equal": pa["data"]["frontier"]["commitment"] == pb["data"]["frontier"]["commitment"], "commitment": pa["data"]["frontier"]["commitment"], "sse_body_byte_equal": a.body == b.body, "sse_body_sha256": sup.sha256_hex(a.body), "sse_body_len": len(a.body), "stop_reason_2": pd["data"].get("stop_reason"), "commit_differs_at_2": pd["data"]["frontier"]["commitment"] != pa["data"]["frontier"]["commitment"], "structured_eq_text": ra.get("structuredContent") == pa}
        # (g) tools/list header R vs stdio digest
        g = {}
        for rev in sup.REVISIONS:
            r = await c.rpc("tools/list", version=rev)
            g[rev] = s38.tools_digest(r.rpc(1)["result"])["tools_digest"]
        r = await c.rpc("tools/list", version=None)
        g["absent"] = s38.tools_digest(r.rpc(1)["result"])["tools_digest"]
        OUT["g_http"] = g
    # stdio digests
    fx = s38.build_fixture(Path(tempfile.mkdtemp()) / "x")
    OUT["g_stdio"] = {}
    for rev in sup.REVISIONS:
        client = s38.RawLineClient(paths.serve_argv(), env=s38.child_env(0))
        try:
            client.request("initialize", s38.initialize_params(rev)); client.notify("notifications/initialized")
            body = json.loads(client.request("tools/list"))
            OUT["g_stdio"][rev] = s38.tools_digest(body["result"])["tools_digest"]
        finally:
            client.close()
    from atlas_aci.config import Config
    from atlas_aci.server import build_tool_manifest
    cfg = sup.make_config(paths)
    OUT["g_manifest"] = {rev: s38.sha256_hex(s38.canon(build_tool_manifest(cfg, rev))) for rev in sup.REVISIONS}
    OUT["g_equal"] = {rev: OUT["g_http"][rev] == OUT["g_stdio"][rev] for rev in sup.REVISIONS}
    OUT["g_absent_equals_2025_03_26"] = OUT["g_http"]["absent"] == OUT["g_stdio"]["2025-03-26"]


async def main():
    root = Path(tempfile.mkdtemp())
    await spike_d()
    await spike_efg(root)
    print(json.dumps(OUT, indent=1, default=str))

asyncio.run(main())
