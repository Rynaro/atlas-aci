"""ACI-S20 Day-1 checks (a)-(d) on a real build, plus diff-admission measurement.

Run: dk20.sh /opt/venv/bin/python /ws/.spectra/evidence/aci-s20/day1.py
Read-only against the fixture repos it creates under a temp dir.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/ws/mcp-server/tests/slices")
from test_s19_support import same_path_pair  # noqa: E402

from atlas_aci.query import diff as symbol_diff  # noqa: E402
from atlas_aci.query.traversal import navigate  # noqa: E402

V1 = {
    "pkg/__init__.py": "",
    "pkg/geometry.py": (
        "def area(w, h):\n    return w * h\n\n\n"
        "def perimeter(w, h):\n    return 2 * (w + h)\n\n\n"
        "def helper():\n    return 1\n\n\n"
        "def legacy():\n    return 0\n\n\n"
        "def doubled(w, h):\n    return area(w, h) * 2\n"
    ),
    "pkg/shapes.py": "from .geometry import area\n\n\nclass Rect:\n    def size(self):\n        return area(1, 2)\n",
    "pkg/report.py": "def summary(r):\n    return r.size()\n",
    "pkg/cli.py": "from .geometry import legacy\n\n\ndef main():\n    return legacy()\n",
    "pkg/util.py": "from .geometry import perimeter\n\n\ndef unrelated():\n    return perimeter(1, 2)\n",
}
V2 = {
    **V1,
    "pkg/geometry.py": (
        "def area(w, h, scale=1):\n    return w * h * scale\n\n\n"
        "def perimeter(w, h):\n    return 2 * (w + h)\n\n\n"
        "def helper():\n    return 2\n\n\n"
        "def doubled(w, h):\n    return area(w, h) * 2\n"
    ),
}
RB1 = {
    "app/a.rb": "class Hub\n  def dup_name\n  end\nend\n",
    "app/b.rb": "class Hub\n  def dup_name\n  end\nend\n",
    "app/caller.rb": "class Hub\n  def call\n    dup_name()\n  end\nend\n",
}


def ent(db, name):
    return [json.loads(p) for (p,) in db.execute("SELECT payload FROM entities") if json.loads(p)["name"] == name]


async def main(out):
    result = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "py").mkdir()
        repo, before, after = same_path_pair(tmp / "py", V1, V2)
        from contextlib import ExitStack

        with ExitStack() as stack:
            g_b = symbol_diff._open(stack, before)
            g_a = symbol_diff._open(stack, after)
            lb = symbol_diff._lease(stack, g_b)
            la = symbol_diff._lease(stack, g_a)
            # (a)
            area_id = next(e["id"] for e in ent(la.db, "area"))
            nav = await navigate(la, seed={"entity_id": area_id}, direction="incoming",
                                 relations=["call"], max_depth=3)
            callers = sorted(
                (h["assertion"]["source_entity_id"], h["assertion"]["resolution"]) for h in nav["edges"]
            )
            names = {n["id"]: n["name"] for n in nav["nodes"]}
            result["a_relative_import_cross_file"] = {
                "outcome": nav["outcome"], "stop": nav["stop_reason"],
                "callers": sorted((names[s], r) for s, r in callers),
            }
            # (b)
            rows = la.db.execute(
                "SELECT callee_name, unresolved_reason, path, relation FROM refs WHERE callee_name='size'"
            ).fetchall()
            result["b_size_refs_rows"] = [list(r) for r in rows]
            # (d) removed entity incoming navigate on before lease
            legacy_id = next(e["id"] for e in ent(lb.db, "legacy"))
            nb = await navigate(lb, seed={"entity_id": legacy_id}, direction="incoming", relations=["call"], max_depth=3)
            result["d_before_legacy"] = {
                "outcome": nb["outcome"],
                "callers": sorted(names2 for names2 in (n["name"] for n in nb["nodes"])),
                "hop_resolutions": [h["assertion"]["resolution"] for h in nb["edges"]],
            }
            na = await navigate(la, seed={"entity_id": legacy_id}, direction="incoming", relations=["call"], max_depth=3)
            result["d_after_legacy_navigate"] = {"outcome": na["outcome"], "nodes": len(na["nodes"])}
            main_id = next(e["id"] for e in ent(la.db, "main"))
            rowsm = la.db.execute(
                "SELECT payload FROM assertions WHERE json_extract(payload,'$.source_entity_id')=?", (main_id,)
            ).fetchall()
            result["d_after_main_assertions"] = [
                {k: json.loads(p).get(k) for k in ("relation", "resolution", "target_entity_id", "raw_target")}
                for (p,) in rowsm
            ]
            result["a_after_unresolved_main_refs"] = [
                list(r) for r in la.db.execute(
                    "SELECT callee_name, unresolved_reason FROM refs WHERE callee_name='legacy'"
                )
            ]
            # diff admission measurement
            t = time.perf_counter()
            loaded = symbol_diff._Loaded(la, la.snapshot_id, la.generation_id, la._source_lease.manifest["context"])
            loaded.load()
            result["diff_measure_py_fixture"] = {
                "entities": la.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                "occurrences": la.db.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0],
                "load_seconds": round(time.perf_counter() - t, 4),
            }
        # (c)
        (tmp / "rb").mkdir()
        rrepo, rb, ra = same_path_pair(tmp / "rb", RB1, RB1)
        with ExitStack() as stack:
            g = symbol_diff._open(stack, ra)
            l = symbol_diff._lease(stack, g)
            rows = l.db.execute(
                "SELECT relation, confidence, callee_name, source_name FROM edges WHERE confidence='AMBIGUOUS'"
            ).fetchall()
            result["c_ruby_ambiguous_edges"] = [list(r) for r in rows]
            result["c_ruby_unresolved_ref_count_go"] = l.unresolved_ref_count("dup_name", ("call",))
            result["c_ruby_refs"] = [list(r) for r in l.db.execute("SELECT callee_name, relation, unresolved_reason FROM refs")]
    Path(out).write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps(result, indent=1, sort_keys=True))


asyncio.run(main(sys.argv[1]))
