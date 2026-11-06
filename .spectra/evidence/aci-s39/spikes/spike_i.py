import asyncio, os, signal, subprocess, sys, tempfile, json, time
from pathlib import Path
sys.path.insert(0, "/ws/mcp-server/tests/slices")
import test_s39_support as sup, test_s38_support as s38
BASE = '''
import asyncio, uvicorn, sys
async def app(scope, receive, send):
    pass
cfg = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=False, ws="none", interface="asgi3")
server = uvicorn.Server(cfg)
async def main():
    t = asyncio.ensure_future(server.serve())
    while not server.started: await asyncio.sleep(0.01)
    print("LISTENING", flush=True)
    await t
    print("serve_returned", flush=True)
asyncio.run(main())
'''
OVER = '''
import asyncio, sys, structlog
from pathlib import Path
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
from atlas_aci.config import Config
from atlas_aci.protocol.http import run_http
repo, memex, graphs = map(Path, sys.argv[1:4])
asyncio.run(run_http(Config(repo=repo, memex_root=memex, graph_root=graphs, http_port=0)))
'''
def run(code, args=()):
    p = subprocess.Popen([sys.executable, "-c", code, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p
def wait_listening(p, marker):
    buf = ""
    end = time.time() + 30
    while time.time() < end:
        line = p.stdout.readline() if marker == "LISTENING" else p.stderr.readline()
        buf += line
        if marker in line: return True
    return False
out = {}
p = run(BASE); assert wait_listening(p, "LISTENING"); time.sleep(0.3); p.send_signal(signal.SIGTERM)
code = p.wait(15); out["base_uvicorn_default"] = {"exit": code, "stdout": p.stdout.read(), "stderr_tail": p.stderr.read()[-300:]}
root = Path(tempfile.mkdtemp()); paths = s38.build_fixture(root/"fx")
p = run(OVER, [str(paths.repo), str(paths.memex), str(paths.graphs)])
assert wait_listening(p, "http_listening"); time.sleep(0.3)
t = time.time(); p.send_signal(signal.SIGTERM); code = p.wait(15)
err = p.stderr.read()
out["override_run_http"] = {"exit": code, "latency_s": round(time.time()-t, 3), "http_stopped": "http_stopped" in err, "traceback": "Traceback" in err, "stderr_tail": err[-400:]}
print(json.dumps(out, indent=1))
