import sys, asyncio, json, tempfile, contextvars, hashlib
from pathlib import Path
sys.path.insert(0, "/ws/mcp-server/tests/slices")
import test_s38_support as s38
import test_s39_support as s39
from mcp import types
from mcp.server.lowlevel.server import _ping_handler
from atlas_aci.protocol import http
from atlas_aci.query.leases import LeaseRegistry, digest
from atlas_aci.enforcement import ToolError
from atlas_aci.server import build_server_state
from atlas_aci.config import Config
from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import Enforcement
from atlas_aci.memex import Memex

AUTH = contextvars.ContextVar("AUTH", default=None)
scope_calls = []
timer_scope_calls = []
class Scoped(LeaseRegistry):
    def scope(self):
        ctx = AUTH.get()
        scope_calls.append(ctx)
        if ctx is None:
            import inspect
            if any(f.function in ("_idle_expiry","_expire","close") for f in inspect.stack()):
                timer_scope_calls.append(1)
            raise ToolError("SCOPE_MISMATCH", "Reference scope does not match.")
        return digest({"base": super().scope(), "authz": ctx})

async def main(root):
    r = Path(root)
    a = s38.build_fixture(r/"a", source=s38.LIVE_SOURCE)
    b = s38.build_fixture(r/"b", source=s38.LIVE_SOURCE)
    out = {}
    apps = {}
    shared_gate = http.InflightGate(8)
    for ident, p in (("a", a), ("b", b)):
        cfg = s39.make_config(p, graph_lease_seconds=1)
        enf, mem, cg = build_server_state(cfg)
        cg._lease_registry = Scoped(cfg, cg)
        app = http.build_app(cfg, (enf, mem, cg))
        app.gate = shared_gate
        server = app.server
        out.setdefault("handlers", sorted(t.__name__ for t in server.request_handlers))
        out["notification_handlers"] = sorted(server.notification_handlers)
        out["ping_is_sdk"] = server.request_handlers[types.PingRequest] is _ping_handler
        # guard
        for typ, h in list(server.request_handlers.items()):
            if typ is types.PingRequest: continue
            def mk(orig, typ):
                async def guarded(req):
                    r_ = None
                    try:
                        r_ = server.request_context.request
                    except LookupError:
                        pass
                    ident_ = None
                    if r_ is not None:
                        ident_ = r_.scope.get("spike.principal")
                    out.setdefault("scope_is_child", []).append(r_ is not None and r_.scope.get("spike.child") is True)
                    if req is None:
                        out.setdefault("list_none", []).append(AUTH.get())
                        if AUTH.get() is None: raise RuntimeError("no")
                        return await orig(req)
                    if ident_ is None:
                        raise RuntimeError("nope")
                    tok = AUTH.set(ident_)
                    try:
                        return await orig(req)
                    finally:
                        AUTH.reset(tok)
                return guarded
            server.request_handlers[typ] = mk(h, typ)
        apps[ident] = (app, cfg, cg)
    class Router:
        gate = shared_gate
        def allow_host_name(self, n):
            for x,_,_ in apps.values(): x.allow_host_name(n)
        @__import__("contextlib").asynccontextmanager
        async def running(self):
            async with __import__("contextlib").AsyncExitStack() as st:
                for x,_,_ in apps.values(): await st.enter_async_context(x.running())
                yield
        async def aclose_state(self):
            for x,_,_ in apps.values(): await x.aclose_state()
        async def __call__(self, scope, receive, send):
            path = scope["path"]; ident = path.rsplit("/",1)[1]
            hdr = dict(scope["headers"])
            who = hdr.get(b"x-who", b"").decode()
            child = dict(scope); child["path"]="/mcp"; child["raw_path"]=b"/mcp"
            child["spike.principal"] = who; child["spike.child"]=True
            await apps[ident][0](child, receive, send)
    router = Router()
    async with s39.serving(router) as (host, port):
        for ident in "ab":
            c = s39.RawHttp(host, port)
            async def post(path, who, message, version=s39.SELECTED):
                body = json.dumps(message).encode()
                return await c.request("POST", path, headers=c._rpc_headers(version, [("X-Who", who)]), body=body)
            lt = await post("/mcp/"+ident, "p1", {"jsonrpc":"2.0","id":1,"method":"tools/list"})
            out[f"tools_list_{ident}"] = (lt.status, len(lt.rpc(1)["result"]["tools"]))
            page_args = {"schema_version": s38.GRAPH, "op":"page","query":{"schema_version": s38.GRAPH,"op":"neighborhood","seed":{"name":"A"},"max_depth":1,"deadline_ms":2000},"page_max_bytes":18000}
            r1 = await post("/mcp/"+ident, "p1", s39.tool_message("graph_query", page_args))
            res = r1.rpc(1)["result"]; payload = json.loads(res["content"][0]["text"])
            out[f"page_{ident}"] = (res["isError"], payload.get("status"), bool(payload["data"].get("next_cursor")))
            cont = {"schema_version": s38.GRAPH,"op":"continue","cursor":payload["data"]["next_cursor"],"query_digest":payload["data"]["query_digest"]}
            r2 = await post("/mcp/"+ident, "p2", s39.tool_message("graph_query", cont))
            res2 = r2.rpc(1)["result"]; p2 = json.loads(res2["content"][0]["text"])
            out[f"p2_cont_{ident}"] = (res2["isError"], (p2.get("error") or {}).get("code"))
            r3 = await post("/mcp/"+ident, "p1", s39.tool_message("graph_query", cont))
            res3 = r3.rpc(1)["result"]; p3 = json.loads(res3["content"][0]["text"])
            out[f"p1_cont_{ident}"] = (res3["isError"], p3.get("status"))
            # resource read: p1 reads, p2 reads
            uri = payload["data"]["resources"][0]["uri"]
            rr1 = await post("/mcp/"+ident, "p1", {"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":uri}})
            out[f"res_p1_{ident}"] = list(rr1.rpc(1))
            rr2 = await post("/mcp/"+ident, "p2", {"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":uri}})
            out[f"res_p2_{ident}"] = rr2.rpc(1).get("error")
            # cross repo cursor
            other = "b" if ident=="a" else "a"
            r4 = await post("/mcp/"+other, "p1", s39.tool_message("graph_query", cont))
            res4 = r4.rpc(1)["result"]; p4 = json.loads(res4["content"][0]["text"])
            out[f"p1_cont_on_{other}"] = (res4["isError"], (p4.get("error") or {}).get("code"))
            break
        # timer: wait for graph_lease_seconds=1 expiry
        n_before = len(timer_scope_calls)
        await asyncio.sleep(2.5)
        out["usage_after_expiry"] = apps["a"][2]._lease_registry.usage()
        out["timer_scope_calls"] = len(timer_scope_calls)
        out["scope_calls_none_ctx"] = sum(1 for x in scope_calls if x is None)
        out["list_none"] = out.get("list_none")
        out["gate"] = shared_gate.snapshot()
    print(json.dumps(out, indent=1, default=str))
    # distinct ContextVar after
    print("AUTH outside:", AUTH.get())

with tempfile.TemporaryDirectory() as d:
    asyncio.run(main(d))
