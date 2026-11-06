import importlib.util, json, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("rec", "/s42/draft/s42_record.py")
rec = importlib.util.module_from_spec(spec); sys.modules["rec"] = rec; spec.loader.exec_module(rec)
manifest, sha = rec.load_manifest(Path("/s42/manifest.dev.json"))
for seam in ("mid_publish", "post_replace", "pre_receipt"):
    work = Path(tempfile.mkdtemp(dir="/tmp"))
    case = {"id": seam, "injection": {"kind": "kill", "seam": seam}, "expected_tree": "T2"}
    run = rec.InterruptRun(manifest, case, work)
    run.write("T1"); run.build("base", run.ns, 100); run.write("T2")
    code, out, err = run.submit("k", run.ns, 300, env=rec.fault_env({seam: {"action": "kill"}}))
    job = run.status()["interrupted_jobs"][0]["job_id"]
    print(seam, "kill rc", code, "job doc", {k: v for k, v in run.job_doc(job).items() if k in ("state","attempt","staged_generation_id")})
    c, o, e = run.cli("resume", run.ns.args("jobs", "resume", tail=("--job", job, "--now", "400")))
    env = rec.last_json(o)
    print(" resume keys", sorted(env), "receipt keys", sorted(env["receipt"]))
    print(" ", {k: env["receipt"].get(k) for k in ("resumed_from", "cancel", "outcome", "recovered", "published", "attempt")}, {k: env.get(k) for k in ("reused", "decline_reason", "outcome", "recovered")})
    print(" job doc after", {k: v for k, v in run.job_doc(job).items() if k not in ("receipt",)})
