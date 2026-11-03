"""ACI-S20 Day-1: measure diff-phase cost (entities/occurrences/changed files) on a synthetic repo.

Run: dk20.sh /opt/venv/bin/python /ws/.spectra/evidence/aci-s20/day1-measure.py <files> <funcs> <changed> <out.json>
"""
from __future__ import annotations
import json, sys, tempfile, time
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, "/ws/mcp-server/tests/slices")
from test_s19_support import same_path_pair  # noqa: E402
from atlas_aci.query import diff as d  # noqa: E402

files, funcs, changed, out = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]


def src(i, extra):
    return "".join(
        f"def f{i}_{j}(a):\n    return a + {j if not (extra and j == 0) else 99}\n\n\n" for j in range(funcs)
    )


v1 = {f"m{i}.py": src(i, False) for i in range(files)}
v2 = {f"m{i}.py": src(i, i < changed) for i in range(files)}
with tempfile.TemporaryDirectory() as tmp:
    t = time.perf_counter()
    repo, before, after = same_path_pair(Path(tmp), v1, v2)
    build_s = time.perf_counter() - t
    with ExitStack() as stack:
        gb, ga = d._open(stack, before), d._open(stack, after)
        lb, la = d._lease(stack, gb), d._lease(stack, ga)
        comp = d._check_compatible(lb, la)
        ents = la.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        occ = la.db.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]
        old = d._Loaded(lb, lb.snapshot_id, lb.generation_id, lb._source_lease.manifest["context"])
        new = d._Loaded(la, la.snapshot_id, la.generation_id, la._source_lease.manifest["context"])
        t = time.perf_counter(); old.load(); new.load(); load_s = time.perf_counter() - t
        t = time.perf_counter(); cs = d._build(old, new, comp, d.FileAnalysis); build_diff_s = time.perf_counter() - t
    res = {
        "files": files, "funcs_per_file": funcs, "changed_files": changed,
        "entities_per_side": ents, "occurrences_per_side": occ,
        "index_both_sides_seconds": round(build_s, 2),
        "load_seconds_both": round(load_s, 3), "build_seconds": round(build_diff_s, 3),
        "changes": len(cs["changes"]), "summary": cs["summary"],
    }
Path(out).write_text(json.dumps(res, indent=1, sort_keys=True))
print(json.dumps(res, indent=1, sort_keys=True))
