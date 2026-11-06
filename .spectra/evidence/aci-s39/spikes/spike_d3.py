import asyncio, json, sys, contextlib
sys.path.insert(0, "/w/mcp-server/tests/slices")
import test_s39_support as sup
import uvicorn, importlib.metadata as m
async def main():
    seen = {}
    async def app(scope, receive, send):
        if scope["type"] != "http": return
        seen.update(server=scope["server"], client=scope["client"])
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
        await send({"type": "http.response.body", "body": b"ok"})
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=False, ws="none", interface="asgi3")
    server = uvicorn.Server(cfg)
    server.capture_signals = lambda: contextlib.nullcontext()
    task = asyncio.ensure_future(server.serve())
    assert await sup.poll(lambda: server.started, 10)
    port = server.servers[0].sockets[0].getsockname()[1]
    r = await sup.RawHttp("127.0.0.1", port).request("POST", "/x", body=b"{}")
    server.should_exit = True
    await asyncio.wait_for(task, 10)
    print(json.dumps({"d_py314": {"python": sys.version.split()[0], "status": r.status, "server": seen["server"], "client": seen["client"], "uvicorn": m.version("uvicorn"), "mcp": m.version("mcp"), "starlette": m.version("starlette"), "sse_starlette": m.version("sse-starlette"), "anyio": m.version("anyio")}}))
asyncio.run(main())
