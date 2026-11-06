import asyncio, os, sys, tempfile, json
from pathlib import Path
sys.path.insert(0, "/ws/mcp-server/tests/slices")
import test_s39_support as sup
async def main():
    from atlas_aci.config import Config
    from atlas_aci.enforcement import Enforcement
    from atlas_aci.tools.search_text import search_text
    d = Path(tempfile.mkdtemp()); (d/"bin").mkdir(); (d/"repo").mkdir(); (d/"repo"/"a.py").write_text("x\n")
    sup.write_fake(d/"bin", "rg", sup.FAKE_HANG)
    pidfile = d/"pid"
    os.environ["PATH"] = f"{d/'bin'}:{os.environ['PATH']}"; os.environ["ATLAS_FAKE_PIDFILE"] = str(pidfile)
    cfg = Config(repo=d/"repo", memex_root=d/"memex", max_calls_per_minute=0)
    task = asyncio.ensure_future(search_text({"pattern":"x","scope":"."}, cfg, Enforcement(cfg)))
    assert await sup.poll(lambda: pidfile.exists() and pidfile.read_text().strip(), 10)
    pid = int(pidfile.read_text()); await asyncio.sleep(0.2)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    await asyncio.sleep(2.0)
    print(json.dumps({"h": {"pid": pid, "child_alive_2s_after_cancel": sup.alive(pid), "src": os.environ.get("PYTHONPATH")}}))
    if sup.alive(pid): os.kill(pid, 9)
asyncio.run(main())
