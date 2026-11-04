"""ACI-S22 Day-1 qualification (scratch). Real builds, real leases, no mocks."""
import asyncio, hashlib, json, os, sys, tempfile, shutil, subprocess
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, "/ws/mcp-server/tests/slices")
sys.path.insert(0, "/ws/mcp-server/tests")
import test_s21_support as s21
import test_s19_support as s19
from atlas_aci.codegraph import CodeGraph
from atlas_aci.query import impact, diff as symbol_diff
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev
from atlas_aci.providers import coverage_py

out = {}
tmp = Path(tempfile.mkdtemp(prefix="q22-"))

# ---- COV fixture build (sentinel placed before R0) ----
repo = s21.workspace(tmp)
(repo / "tests/test_sentinel.py").write_text(
    "from pathlib import Path\nPath(__file__).with_name('SENTINEL').write_text('x')\n\n\ndef test_sentinel():\n    assert True\n")
r0 = tmp / "root-r0"
g = CodeGraph(repo, graph_root=r0); g.build(); s21.import_run(g, "a"); g.close()
orig = (repo / "app/calc.py").read_bytes()
src = orig.decode()
src = src.replace("        return low\n", "        return int(low)\n", 1)
src = src.replace("    result = 0\n", "    result = 0 + 0\n", 1)
src = src.replace('return "unreached"', 'return "still"', 1)
assert src.count("\n") == orig.decode().count("\n")
(repo / "app/calc.py").write_text(src)
r1 = tmp / "root-r1"
g = CodeGraph(repo, graph_root=r1); g.build(); g.close()

# C: original bytes in a separate root, built while repo edited? -> must restore bytes temporarily.
# Build C by temporarily restoring original bytes (qualification (e)): repo edited back afterwards.
(repo / "app/calc.py").write_bytes(orig)
rc = tmp / "root-c"
g = CodeGraph(repo, graph_root=rc); g.build(); s21.import_run(g, "a"); gc_gen = None; g.close()
rbuild = tmp / "root-buildonly"
g = CodeGraph(repo, graph_root=rbuild); g.build(); g.close()
(repo / "app/calc.py").write_text(src)

# PKG
pkgrepo_base = tmp / "pkgwork"; pkgrepo_base.mkdir()
PKG1 = {
 "pkg/__init__.py": "",
 "pkg/geometry.py": "def area(w, h):\n    return w * h\n",
 "pkg/shapes.py": "from .geometry import area\n\n\ndef rect_area(w, h):\n    return area(w, h)\n",
 "pkg/test_shapes.py": "from .shapes import rect_area\n\n\ndef test_rect_area():\n    assert rect_area(1, 2) == 2\n",
}
PKG2 = {**PKG1, "pkg/geometry.py": "def area(w, h, scale=1):\n    return w * h * scale\n"}
prepo, pb, pa = s19.same_path_pair(pkgrepo_base, PKG1, PKG2)

# ---- spy installed AFTER all builds ----
launches = []
import subprocess as sp, _posixsubprocess, pty as ptymod
saved = {}
def mk(name):
    def spy(*a, **k):
        launches.append(name); raise RuntimeError("launch blocked: " + name)
    return spy
targets = [(sp, "Popen"), (_posixsubprocess, "fork_exec"), (os, "fork"), (os, "forkpty"), (os, "system"), (os, "popen"), (ptymod, "fork")]
for n in ("posix_spawn", "posix_spawnp", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe"):
    if hasattr(os, n): targets.append((os, n))
for mod, n in targets:
    saved[(mod, n)] = getattr(mod, n); setattr(mod, n, mk(n))
assert sp.Popen is getattr(sp, "Popen")

# ---- (a) ----
def lease(stack, side):
    return impact.open_side(stack, side)
sb = Side(repo, r0); sa = Side(repo, r1)
res = {}
with ExitStack() as stack:
    b = lease(stack, sb); a = lease(stack, sa)
    change, loaded, dwork = impact.change_set(b, a, impact.Settings().diff_limits)
    recs = {r["name"]: r for r in change["changes"] if r["path"] == "app/calc.py"}
    res["a_records"] = {n: {"category": r["category"], "has_before": r["before"] is not None, "has_after": r["after"] is not None} for n, r in recs.items()}
    obs_b = impact._observations(b)["app/calc.py"]["observed_source_sha256"]
    obs_a = impact._observations(a)["app/calc.py"]["observed_source_sha256"]
    res["a_ref_hash_eq_manifest_eq_bytes"] = (
        recs["clamp"]["before"]["source_sha256"] == obs_b == hashlib.sha256(orig).hexdigest()
        and recs["clamp"]["after"]["source_sha256"] == obs_a == hashlib.sha256(src.encode()).hexdigest())
    res["a_body_all"] = all(recs[n]["category"] == "body" for n in ("clamp", "total", "never_called"))
    res["a_clamp_before_span"] = [recs["clamp"]["before"][k] for k in ("line", "end_line", "start_byte", "end_byte")]
    res["a_before_is_coverage_import"] = impact._is_coverage_import(b)
    # ---- (b) ----
    fid = [r[0] for r in b.db.execute("SELECT id FROM entities WHERE path='app/calc.py' AND json_extract(payload,'$.kind')='file'")]
    res["b_file_entities"] = len(fid)
    plan = list(b.db.execute("EXPLAIN QUERY PLAN SELECT id,payload FROM assertions WHERE json_extract(payload,'$.relation')=? AND json_extract(payload,'$.target_entity_id')=? ORDER BY id", ("test_executes", fid[0])))
    res["b_query_plan"] = [tuple(x) for x in plan]
    rows = list(b.db.execute("SELECT id,payload FROM assertions WHERE json_extract(payload,'$.relation')=? AND json_extract(payload,'$.target_entity_id')=? ORDER BY id", ("test_executes", fid[0])))
    res["b_rows"] = len(rows)
    nodeids = set(); hashes = set(); lines = set()
    for aid, payload in rows:
        asr = json.loads(payload)
        src_e = ev.get(b.db, "entity", asr["source_entity_id"])
        evd = ev.get(b.db, "evidence", asr["evidence_id"])
        occ = ev.get(b.db, "occurrence", evd["occurrence_id"])
        prod = ev.get(b.db, "producer", evd["producer_id"])
        nodeids.add(src_e["name"]); hashes.add(occ["source_sha256"])
        if 43 <= occ["line"] <= 48: res.setdefault("b_clamp_nodeids", set()).add(src_e["name"])
        res.setdefault("b_sample", {"asr": asr, "occ": occ, "prod_keys": sorted(prod["fingerprint"]) if prod["fingerprint"] else None, "src": src_e})
    res["b_nodeids_total"] = len(nodeids)
    res["b_clamp_nodeids"] = sorted(res.get("b_clamp_nodeids", []))
    res["b_occ_hash_eq_before_ref_hash"] = hashes == {recs["clamp"]["before"]["source_sha256"]}
    labels = s21.LABELS["a"] if "a" in s21.LABELS else None
    res["b_labels_keys"] = list(labels)[:5] if isinstance(labels, dict) else str(type(labels))
    # ---- (c) ----
    for name in ("clamp", "total"):
        refs = [tuple(x) for x in a.db.execute("SELECT path,line,col FROM refs WHERE callee_name=? ORDER BY path,line,col", (name,))]
        res["c_refs_" + name] = refs
    # same-path assertions
    for path in ("tests/test_basic.py", "tests/test_params.py"):
        for (payload,) in a.db.execute("SELECT payload FROM assertions WHERE path=?", (path,)):
            asr = json.loads(payload)
            if asr.get("raw_target") in ("clamp", "total", "calc.clamp", "calc.total"):
                se = loaded[1].entities.get(path, {}).get(asr["source_entity_id"])
                res.setdefault("c_assertions", []).append({"path": path, "raw_target": asr["raw_target"], "relation": asr["relation"], "src": se and {k: se[k] for k in ("kind", "name", "owners")}, "target": asr.get("target_entity_id")})
    # ---- probe external (e) ----
    with ExitStack() as st2:
        cr = impact.open_side(st2, Side(repo, rc))
        res["e_leased"] = True
        compat = symbol_diff._check_compatible(cr, a)
        res["e_compat_keys"] = sorted(compat)
        res["e_gen_ids"] = {"R0": b.generation_id, "R1": a.generation_id, "C": cr.generation_id}
        res["e_c_equals_r0_generation"] = cr.generation_id == b.generation_id
        res["e_c_atlas_dir_distinct"] = cr.atlas_dir not in (a.atlas_dir, b.atlas_dir)
        res["e_c_matches_before_hash"] = impact._observations(cr)["app/calc.py"]["observed_source_sha256"] == obs_b
        bo = impact.open_side(st2, Side(repo, rbuild))
        res["e_buildonly_generation_distinct_from_r0"] = bo.generation_id != b.generation_id
out["a_to_e_cov"] = res

# ---- (d) PKG ----
async def go():
    with ExitStack() as stack:
        b = impact.open_side(stack, pb); a = impact.open_side(stack, pa)
        change, loaded, dwork = impact.change_set(b, a, impact.Settings().diff_limits)
        req = impact.normalize({"schema_version": __import__("atlas_aci.protocol.graph", fromlist=["VERSION"]).VERSION, "op": "impact"})
        from atlas_aci.query.impact_work import ImpactWork
        from atlas_aci.query import impact_limits as lim
        work = ImpactWork(**{n: req[n] for n in lim.IMPACT_LIMITS})
        changed_ids = {r["entity_id"] for r in change["changes"]}
        eligible = impact.select_seeds(change["changes"], loaded[1])
        impacted, rows, limited = await impact.propagate({"before": b, "after": a}, eligible, req, work, changed_ids)
        return [(i["entity"]["name"], i["entity"]["kind"], i["entity"]["path"], i["distance"]) for i in impacted], [(r["name"], r["category"]) for r in rows]
imp, seeds = asyncio.run(go())
out["d_pkg_impacted"] = imp
out["d_pkg_seeds"] = seeds
out["d_ok"] = ("test_rect_area", "function", "pkg/test_shapes.py", 2) in [tuple(x) for x in imp]

# ---- (f) launch count + liveness control ----
out["f_launches_during_a_to_e"] = list(launches)
launches.clear()
try:
    sp.run(["true"])
    out["f_liveness_raised"] = False
except RuntimeError:
    out["f_liveness_raised"] = True
out["f_liveness_counted"] = list(launches)
launches.clear()
for (mod, n), v in saved.items(): setattr(mod, n, v)
out["f_sentinel_exists"] = (repo / "tests/SENTINEL").exists()
ae = out["a_to_e_cov"]
out["verdict"] = {
    "a": bool(ae["a_body_all"] and ae["a_ref_hash_eq_manifest_eq_bytes"] and ae["a_before_is_coverage_import"]),
    "b": bool(ae["b_rows"] > 0 and ae["b_occ_hash_eq_before_ref_hash"] and len(ae["b_clamp_nodeids"]) == 3 and "idx_assertions_target_relation_id" in json.dumps(ae["b_query_plan"])),
    "c": bool(ae["c_refs_clamp"] and ae["c_refs_total"] and any(x["src"] and x["src"]["name"] == "test_total" for x in ae["c_assertions"])),
    "d": bool(out["d_ok"]),
    "e": bool(ae["e_leased"] and ae["e_c_atlas_dir_distinct"] and ae["e_c_matches_before_hash"]),
    "f": bool(out["f_launches_during_a_to_e"] == [] and out["f_liveness_raised"] and out["f_liveness_counted"] == ["Popen"]),
}
out["verdict"]["stop_rule_triggered"] = not all(out["verdict"][k] for k in "abef")
out["notes"] = [
    "PKG variant: the test calls a free function rect_area (not Rect().rect_area()); instance-method call via Rect() was NOT reverse-resolved in this probe (impacted = [rect_area] only).",
    "External root C built at original bytes + import a has the SAME generation_id as R0 (content-hashed): duplicate_source applies (checker condition 2).",
]
text = json.dumps(out, indent=1, sort_keys=True, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
import re
text = text.replace(str(tmp), "<tmp>")
Path("/ws/.spectra/evidence/aci-s22/qualification.json").write_text(text + "\n")
print(text[:300])
