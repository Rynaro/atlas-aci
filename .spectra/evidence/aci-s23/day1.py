"""ACI-S23 Day-1 qualification (b)-(f). Run via rec23.sh. Prints one JSON document."""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path("/Users/henrique/workspace/oss/agents/atlas-aci")
SCRATCH = Path(sys.argv[1])
V0 = "ghcr.io/rynaro/atlas-aci@sha256:e2542ef8d569882c560065f0bcaed9ef2a7398e7e61765e25b142a3c93ec7cf7"
CACHE = SCRATCH / "v0cache"
FIX = json.loads((ROOT / "mcp-server/tests/fixtures/s18/useful-spans.v1.json").read_bytes())
out: dict = {}
os.environ.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"})


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


GENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Atlas Eval", "GIT_AUTHOR_EMAIL": "eval@example.invalid",
    "GIT_COMMITTER_NAME": "Atlas Eval", "GIT_COMMITTER_EMAIL": "eval@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "PATH": os.environ["PATH"], "HOME": "/nonexistent",
}


def det_repo(dest: Path, files: dict[str, bytes]) -> str:
    dest.mkdir(parents=True)
    for rel, data in files.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(data)
    cfg = ["-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "core.fileMode=false"]
    for cmd in (["init", "-q", "--template=", "--initial-branch=main"], ["add", "-A"],
                [*cfg, "commit", "-q", "-m", "atlas eval corpus"]):
        subprocess.run(["git", *cmd], cwd=dest, env=GENV, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=dest, env=GENV, text=True).strip()


def s18_files() -> dict[str, bytes]:
    return {p: t.encode() for p, t in FIX["source_files"].items()}


def run_ledger(runner, repo, tasks, server, deadline, led):
    manifest = {"schema": "atlas/task-manifest.v1", "repository": {"revision": subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()}, "model": None, "tasks": tasks}
    res = runner.run_manifest(repo, manifest, led, server=server, deadline=deadline)
    rid = res["run_id"]
    rows = []
    for t in tasks:
        rec = json.loads((led / rid / "tasks" / f"{t['id']}.json").read_bytes())
        resp = json.loads((led / rid / rec["response"]["path"]).read_bytes())
        rows.append((t, rec["outcome"], resp, rec["duration_seconds"]))
    return rows


def main() -> None:
    runner = load("stdio_runner", ROOT / "atlas/evals/stdio_runner.py")
    work = Path(tempfile.mkdtemp(prefix="d1-", dir=SCRATCH))
    # ---- (b) V0 under the SDK dispatch
    v0repo = work / "v0"
    commit = det_repo(v0repo, s18_files())
    r = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "-v", f"{CACHE}:/tmp/.cache:ro",
                        "-v", f"{v0repo}:/repo", V0, "index", "--repo", "/repo"], capture_output=True, text=True)
    done = [ln for ln in r.stderr.splitlines() if "index_done" in ln]
    out["a_index"] = {"exit": r.returncode, "index_done": done[-1].split("index_done")[-1].strip() if done else None,
                      "corpus_commit": commit}
    server = ["docker", "run", "--rm", "-i", "--pull", "never", "--network", "none", "-v", f"{CACHE}:/tmp/.cache:ro",
              "-v", f"{v0repo}:/repo:ro", V0, "serve", "--repo", "/repo", "--memex-root", "/memex"]
    tasks = [{"id": "sym", "tool": "search_symbol", "arguments": {"name": "process_payment"}},
             {"id": "view", "tool": "view_file", "arguments": {"path": "app/payments.py", "start_line": 1, "end_line": 3}}]
    t0 = time.time()
    rows = run_ledger(runner, v0repo, tasks, server, 60.0, work / "led-v0")
    out["b_v0_dispatch"] = {"outcomes": {t["id"]: o for t, o, _, _ in rows}, "seconds": round(time.time() - t0, 2),
                            "sym_has_definition": "definitions" in json.dumps(rows[0][2]) and '"line_start"' in json.dumps(rows[0][2]),
                            "ledger_response_keys": sorted(rows[0][2]),
                            "sample_text_len": len(rows[0][2]["content"][0]["text"])}
    # ---- (c) parity of span_metrics-equivalent functions (S18 module via importlib)
    sys.path.insert(0, str(ROOT / "mcp-server/src"))
    m18 = load("measure_context_yield", ROOT / "atlas/evals/measure_context_yield.py")
    ly = json.loads((ROOT / ".spectra/evidence/aci-s18/local-yield.json").read_bytes())
    cp = json.loads((ROOT / ".spectra/evidence/aci-s18/baseline-checkpoint.json").read_bytes())
    tm = {t["id"]: t for t in FIX["tasks"]}
    ok_ctx = ok_base = 0
    for row in ly["context"]:
        raw = base64.b64decode(row["result_base64"])
        _, srcs, n = m18.body_and_sources(raw, False)
        assert m18.metrics(srcs, tm[row["task"]]["useful_spans"], FIX) == row["metrics"] and n == row["selected_bundles"]
        ok_ctx += 1
    for row in cp["lexical_results"]:
        raw = base64.b64decode(row["result_base64"])
        _, srcs, n = m18.body_and_sources(raw, True)
        lr = next(b for b in ly["baseline_reference_only"] if b["task"] == row["task"] and b["max_bytes"] == row["max_bytes"] and b["protocol_version"] == row["protocol_version"])
        assert m18.metrics(srcs, tm[row["task"]]["useful_spans"], FIX) == lr["metrics"] and n == lr["selected_candidates"]
        ok_base += 1
    out["c_parity_pre_refactor_module"] = {"context_rows": ok_ctx, "baseline_rows": ok_base}
    # ---- (d) HEAD V1: context + both legacy policies; client length == server result_bytes
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.config import Config
    from atlas_aci.protocol.results import result_bytes
    from atlas_aci.server import build_server_state, call_tool_result

    v1repo = work / "v1"
    c1 = det_repo(v1repo, s18_files())
    CodeGraph(v1repo).build()
    ctx_tasks, ctx_args = [], []
    for budget in (8000, 32000, 64000):
        for t in FIX["tasks"]:
            for policy in ("inline_if_complete", "references"):
                a = {"schema_version": "atlas.graph.v1", "op": "context", "text": t["text"], "source_policy": policy,
                     "max_bytes": budget, "max_candidates": 32, "max_inline_bytes": 8192}
                ctx_tasks.append({"id": f"c-{budget}-{t['id']}-{policy}", "tool": "graph_query", "arguments": a})
    v1server = [sys.executable, "-m", "atlas_aci", "serve", "--repo", str(v1repo)]
    rows = run_ledger(runner, v1repo, ctx_tasks, v1server, 60.0, work / "led-v1")
    cfg = Config(repo=v1repo, memex_root=work / "memex", max_calls_per_minute=0)
    enf, memex, factory = build_server_state(cfg)
    outcomes: dict = {}
    mism = 0
    negotiated = None

    async def inproc(args, version):
        res = await call_tool_result("graph_query", args, cfg, enf, memex, factory, protocol_version=version)
        return result_bytes(res)

    async def probe_version():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async with stdio_client(StdioServerParameters(command=v1server[0], args=v1server[1:], cwd=str(v1repo))) as (r_, w_), ClientSession(r_, w_) as s:
            return (await s.initialize()).protocolVersion
    negotiated = asyncio.run(probe_version())
    lens = []
    for t, o, resp, _ in rows:
        outcomes[o] = outcomes.get(o, 0) + 1
        client = json.dumps(resp, separators=(",", ":"), ensure_ascii=False).encode()
        server_len = len(asyncio.run(inproc(t["arguments"], negotiated)))
        lens.append((len(client), server_len))
        mism += len(client) != server_len
    factory.close()
    out["d_context"] = {"calls": len(rows), "outcomes": outcomes, "negotiated_protocol": negotiated,
                        "client_len_ne_server_result_bytes": mism, "sample": lens[:6]}
    # legacy policies
    legacy = []
    for term in ("process_payment", "send_request", "render_text", "retry", "transport", "timeout"):
        legacy.append({"id": f"sym-{term}", "tool": "search_symbol", "arguments": {"name": term}})
        legacy.append({"id": f"txt-{term}", "tool": "search_text", "arguments": {"pattern": term, "scope": ".", "regex": False}})
    rows = run_ledger(runner, v1repo, legacy, v1server, 60.0, work / "led-v1b")
    out["d_legacy_outcomes"] = {t["id"]: (o, len(json.dumps(resp))) for t, o, resp, _ in rows}
    first_def = json.loads(next(r for r in rows if r[0]["id"] == "sym-process_payment")[2]["content"][0]["text"])
    out["d_sample_def_shape"] = first_def["definitions"][:1]
    first_txt = json.loads(next(r for r in rows if r[0]["id"] == "txt-process_payment")[2]["content"][0]["text"])
    out["d_sample_text_shape"] = first_txt["matches"][:1]
    vtask = [{"id": "v", "tool": "view_file", "arguments": {"path": "app/payments.py", "start_line": 1, "end_line": 4}}]
    vrow = run_ledger(runner, v1repo, vtask, v1server, 60.0, work / "led-v1c")[0]
    body = json.loads(vrow[2]["content"][0]["text"])
    out["d_view_matches_frozen"] = "".join(body["lines"]).encode() == FIX["source_files"]["app/payments.py"].encode().splitlines(True)[0:4].__class__ and True
    frozen = FIX["source_files"]["app/payments.py"].splitlines(True)[0:4]
    out["d_view_matches_frozen"] = body["lines"] == frozen
    out["d_view_path"] = body["path"]
    # ---- (f) counters
    cost = {}
    selfsrc = json.loads(gzip.open(ROOT / "mcp-server/tests/fixtures/s08/self-source-fa75bec.json.gz").read())
    corpora = {"s18": s18_files(),
               "self-source": {f["path"]: base64.b64decode(f["base64"]) for f in selfsrc["files"]}}
    for name, files in corpora.items():
        repo = work / f"cost-{name}"
        det_repo(repo, files)
        seq = {}
        g = CodeGraph(repo); seq["cold"] = g.build(); g.close()
        g = CodeGraph(repo); seq["warm"] = g.build(); g.close()
        g = CodeGraph(repo); seq["noop"] = g.build(since="incremental"); g.close()
        target = repo / sorted(files)[0]
        with target.open("ab") as fh:
            fh.write(b"\n# atlas-v3-eval refresh\n")
        g = CodeGraph(repo); seq["refresh"] = g.build(since="incremental"); g.close()
        cost[name] = {k: {"files_indexed": v["files_indexed"], "parser_invocations": v["extraction_cache"]["parser_invocations"],
                          "disk_hits": v["extraction_cache"]["disk_hits"], "misses": v["extraction_cache"]["misses"],
                          "predecessor_reused": v["extraction_cache"]["predecessor_reused"], "files_skipped": v["files_skipped"],
                          "unsupported_skipped": v["unsupported_skipped"], "symbols": v["symbols"]} for k, v in seq.items()}
    out["f_counters"] = cost
    print(json.dumps(out, indent=1, sort_keys=True, default=str))


main()
