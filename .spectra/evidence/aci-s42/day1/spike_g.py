"""Day-1 (b)+(g) spike: full isolation case table against a real serve-shared process."""
import base64, asyncio, importlib.util, json, sys, tempfile, time
from pathlib import Path

spec = importlib.util.spec_from_file_location("rec", "/s42/draft/s42_record.py")
rec = importlib.util.module_from_spec(spec); sys.modules["rec"] = rec; spec.loader.exec_module(rec)

manifest, sha = rec.load_manifest(Path("/s42/manifest.dev.json"))
work = Path(tempfile.mkdtemp(prefix="s42spike-", dir="/tmp"))
t = time.monotonic()
fixtures = rec.build_fixtures(manifest, work / "fx")
print("fixtures", round(time.monotonic() - t, 1), "s", flush=True)
keys = rec.Keys(manifest["issuer"])

# log full responses
orig_row = rec.IsolationState.row
LOG = {}
def row(self, case, phase, resp, body, t0, t1):
    r = orig_row(self, case, phase, resp, body, t0, t1)
    LOG[(phase, case["id"])] = (resp.status if resp else None, (resp.body[:700] if resp else b""), body[:300])
    if case["id"] in ("M09", "M12", "M10", "M07", "R5") and resp:
        print("FULL", case["id"], resp.body[:1500].decode("utf8","replace") if case["id"] != "M09" else [base64.b64decode(c["blob"]) for m in rec.parse_messages(resp) for c in m["result"]["contents"]])
    return r
rec.IsolationState.row = row

t = time.monotonic()
try:
    records = rec.run_isolation(manifest, sha, work, fixtures, keys)
except Exception as exc:
    print("EXC", type(exc), exc)
    for key in sorted(LOG):
        st, b, q = LOG[key]
        print("--", key, st, "\nREQ", q, "\nRESP", b)
    raise
print("isolation", round(time.monotonic() - t, 1), "s", flush=True)
for unit, body in records.items():
    print("==", unit, {k: v for k, v in body.items() if k != "rows"})
    for r in body["rows"]:
        print(f'{r["phase"]:10} {r["case_id"]:4} {str(r["principal"]):5} {r["route"]} {r["outcome"]:42} hits={r["canary_hits"]} seen={r["server_seen"]} witreq={r["witness_in_request"]} http={r["http_status"]}')
if len(sys.argv) > 1:
    for key in sorted(LOG):
        if key[1] in sys.argv[1:]:
            st, b, q = LOG[key]
            print("--", key, st, "\nREQ", q, "\nRESP", b)
print("workdir", work)
import base64
for key in sorted(LOG):
    if key[1] in ("M09",):
        pass
