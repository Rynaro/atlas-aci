import importlib.util, inspect, json, sys, tempfile, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("rec", "/s42/draft/s42_record.py")
rec = importlib.util.module_from_spec(spec); sys.modules["rec"] = rec; spec.loader.exec_module(rec)
manifest, sha = rec.load_manifest(Path("/s42/manifest.dev.json"))
what = sys.argv[1]
work = Path(tempfile.mkdtemp(prefix="s42spike-", dir="/tmp"))
if what == "sig":
    from mcp.client.streamable_http import streamable_http_client
    print(inspect.signature(streamable_http_client))
    sys.exit(0)
t = time.monotonic()
if what in ("compat", "capacity"):
    fixtures = rec.build_fixtures(manifest, work / "fixtures")
    keys = rec.Keys(manifest["issuer"])
if what == "compat":
    def emit(unit, body, observed, failure=None):
        print(unit, "FAIL" if failure else "ok", json.dumps(body)[:600] if body else "", observed if failure else "")
    rec.run_compat(manifest, sha, work, fixtures, keys, emit)
elif what == "capacity":
    body, observed = rec.run_capacity(manifest, sha, work, fixtures, keys)
    print(json.dumps({k: body[k] for k in ("limits", "invariants", "saturation")}, indent=1)); print(json.dumps(observed, indent=1))
elif what == "interrupt":
    for cid in sys.argv[2:]:
        case = next(c for c in manifest["interruption"]["cases"] if c["id"] == cid)
        t0 = time.monotonic()
        try:
            body, observed = rec.run_interrupt_case(manifest, sha, case, work)
            print("==", cid, round(time.monotonic() - t0, 1), "s")
            print(json.dumps(body, sort_keys=True)); print(json.dumps(observed, sort_keys=True))
        except Exception as exc:
            import traceback; traceback.print_exc()
            print("== FAIL", cid, type(exc).__name__, exc)
print("total", round(time.monotonic() - t, 1), "s")
