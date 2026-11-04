"""ACI-S32 Day-1 qualification (scratch evidence; never imported by the product).

Run inside the dev container:  qualify.py <part>
Parts: main (a, a2, b, c, d, e, f, triggers) | g | g2 <N> | h | merge
Every result is observed behaviour of the real indexer, differ and checker (no mocks).
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import structlog

structlog.configure(
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    wrapper_class=structlog.make_filtering_bound_logger(40),
)

from atlas_aci.architecture import checker, rules  # noqa: E402
from atlas_aci.codegraph import CodeGraph  # noqa: E402
from atlas_aci.query import diff, impact  # noqa: E402
from atlas_aci.query.diff import Side  # noqa: E402
from atlas_aci.query.impact_limits import DEFAULT_DIFF_LIMITS  # noqa: E402

EVID = Path("/ws/.spectra/evidence/aci-s32")
PARTS = EVID / "qualification-parts"

BASE = {
    "pkg/__init__.py": "",
    "pkg/domain/__init__.py": "",
    "pkg/infra/__init__.py": "",
    "pkg/domain/a.py": "from .b import fb\n\n\ndef fa():\n    return fb()\n",
    "pkg/domain/b.py": "def fb():\n    return 1\n",
    "pkg/domain/e.py": "def fe():\n    return 5\n",
    "pkg/infra/c.py": "from ..domain.b import fb\n\n\ndef fc():\n    return fb()\n",
}
VIOL_B = "from ..infra.c import fc\n\n\ndef fb():\n    return 1\n"
DECL = {
    "pkg/domain/b.py": "def fb(x=0):\n    return 1\n\n\ndef fb2():\n    return 2\n",
    "pkg/infra/c.py": "from ..domain.b import fb\n\n\ndef fc():\n    return fb() + 1\n",
    "pkg/domain/n.py": "def fn():\n    return 7\n",
    "pkg/domain/e.py": None,
}
RULES = json.dumps(
    {
        "format": "atlas-architecture-rules.v1",
        "rules": [
            {"id": "domain-a-not-infra", "relation": "import", "from": ["pkg/domain/a.py"],
             "to": ["pkg/infra/**"]},
            {"id": "domain-not-infra", "relation": "import", "from": ["pkg/domain/**"],
             "to": ["pkg/infra/**"]},
        ],
    }
).encode()


class StagingGraph(CodeGraph):
    def _iter_source_files(self):
        return iter(())


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def write_tree(repo: Path, files):
    for name, content in files.items():
        target = repo / name
        if content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)


def mk_repo(parent: Path, files, name="repo") -> Path:
    repo = parent / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "q@example.invalid")
    git(repo, "config", "user.name", "q")
    write_tree(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "q")
    return repo


def build(repo, root, since=None, langs=None):
    g = CodeGraph(repo, graph_root=root, langs=langs)
    try:
        return g.build(since)
    finally:
        g.close()


def observations(reader):
    row = reader.db.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()
    return json.loads(row[0])


def vector(reader):
    return sorted(
        (r["path"], r["state"], r["observed_source_sha256"])
        for r in observations(reader)
        if r["type"] == "observation"
    )


def lease(stack, repo, root):
    graph = diff._open(stack, Side(repo, root))
    return diff._lease(stack, graph)


def pack_bytes(reader):
    obs = {r["path"]: r for r in observations(reader) if r["type"] == "observation"}
    return {p: reader.read_source(p, r["observed_source_sha256"]) for p, r in sorted(obs.items())}


def stage(repo, reader, overlays, tmp):
    langs = reader._source_lease.manifest["context"]["configuration"]["languages"]
    g = StagingGraph(repo, langs=langs, graph_root=tmp)
    try:
        return g.build(overlays=overlays)
    finally:
        g.close()


def manifest_diff(one, two):
    a, b = one._source_lease.manifest, two._source_lease.manifest
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def unmodified(repo, r0_root, scratch, tag):
    """Stage every base pack byte unchanged and compare with the base lease."""
    out = {"tag": tag}
    t = scratch / f"unmod-{tag}"
    t.mkdir()
    with ExitStack() as st:
        r0 = lease(st, repo, r0_root)
        out["base_record_types"] = sorted({r["type"] for r in observations(r0)})
        out["base_states"] = sorted({r["state"] for r in observations(r0) if r["type"] == "observation"})
        try:
            overlays = pack_bytes(r0)
        except Exception as exc:  # recorded as a fact
            out["pack_read_error"] = type(exc).__name__
            return out
        out["n_overlays"] = len(overlays)
        try:
            stage(repo, r0, overlays, t)
        except Exception as exc:
            out["staging_error"] = type(exc).__name__
            return out
        t1 = lease(st, repo, t)
        out["snapshot_id_equal"] = r0.snapshot_id == t1.snapshot_id
        out["generation_id_equal"] = r0.generation_id == t1.generation_id
        out["base_snapshot_id"] = r0.snapshot_id
        out["staged_snapshot_id"] = t1.snapshot_id
        out["manifest_differing_keys"] = manifest_diff(r0, t1)
        out["observation_vectors_equal"] = vector(r0) == vector(t1)
        try:
            compat = diff._check_compatible(r0, t1)
            out["compatible"] = True
            cs, _loaded, work = impact.change_set(r0, t1, dict(DEFAULT_DIFF_LIMITS))
            out["changes"] = len(cs["changes"])
            out["partial_paths"] = cs["partial_paths"]
            out["change_set_id"] = cs["id"]
            out["compat_keys"] = sorted(compat)
        except Exception as exc:
            out["compat_or_change_set_error"] = type(exc).__name__ + ":" + str(getattr(exc, "reason", ""))
    return out


def part_main(work: Path):
    res: dict = {}
    repo = mk_repo(work / "w", BASE)
    r0 = work / "R0"
    build(repo, r0)
    scratch = work / "scratch"
    scratch.mkdir()
    # (a)
    a = unmodified(repo, r0, scratch, "a")
    res["a"] = a
    # (e) live-tree independence
    write_tree(repo, {"pkg/domain/zz.py": "from ..infra.c import fc\n\n\ndef fz():\n    return 1\n"})
    write_tree(repo, {"pkg/domain/e.py": None})
    write_tree(repo, {"pkg/domain/b.py": "def fb():\n    return 999\n"})
    e = unmodified(repo, r0, scratch, "e")
    with ExitStack() as st:
        r0l = lease(st, repo, r0)
        t = scratch / "unmod-e"
        tl = lease(st, repo, t)
        e["zz_observed"] = any(p == "pkg/domain/zz.py" for p, _s, _h in vector(tl))
        e["same_snapshot_as_a"] = tl.snapshot_id == a.get("staged_snapshot_id")
    res["e"] = e
    # restore the live tree
    git(repo, "checkout", "-q", "--", ".")
    (repo / "pkg/domain/zz.py").unlink()
    assert git(repo, "status", "--porcelain") == ""
    # (f) determinism across scratch dirs
    f = unmodified(repo, r0, scratch, "f")
    res["f"] = {
        "t1_snapshot": a.get("staged_snapshot_id"),
        "t3_snapshot": f.get("staged_snapshot_id"),
        "snapshot_equal": a.get("staged_snapshot_id") == f.get("staged_snapshot_id"),
        "generation_equal": None,
    }
    with ExitStack() as st:
        t1 = lease(st, repo, scratch / "unmod-a")
        t3 = lease(st, repo, scratch / "unmod-f")
        res["f"]["generation_equal"] = t1.generation_id == t3.generation_id
        res["f"]["scratch_dirs_differ"] = str(scratch / "unmod-a") != str(scratch / "unmod-f")
        # (c) producer/configuration
        sg = StagingGraph(repo, graph_root=scratch / "x")
        cg = CodeGraph(repo, graph_root=scratch / "y")
        c = {"producer_inputs_equal": sg._producer_inputs() == cg._producer_inputs()}
        r0l = lease(st, repo, r0)
        c0 = r0l._source_lease.manifest["context"]
        c1 = t1._source_lease.manifest["context"]
        c["staged_producer_equals_base"] = c0["producer"] == c1["producer"]
        c["staged_configuration_equals_base"] = c0["configuration"] == c1["configuration"]
        c["identity_equal"] = c0["identity"] == c1["identity"]
        c["context_equal"] = c0 == c1
        res["c"] = c
    # (b) patched staging vs a real build of the same bytes at the same path
    with ExitStack() as st:
        r0l = lease(st, repo, r0)
        overlays = pack_bytes(r0l)
        for p, content in DECL.items():
            if content is None:
                overlays.pop(p)
            else:
                overlays[p] = content.encode()
        tp = scratch / "patched"
        tp.mkdir()
        stage(repo, r0l, overlays, tp)
    write_tree(repo, DECL)
    r1 = work / "R1"
    build(repo, r1)
    with ExitStack() as st:
        r0l = lease(st, repo, r0)
        tl = lease(st, repo, scratch / "patched")
        r1l = lease(st, repo, r1)
        b = {
            "snapshot_id_equal": tl.snapshot_id == r1l.snapshot_id,
            "generation_id_equal": tl.generation_id == r1l.generation_id,
            "manifest_differing_keys": manifest_diff(tl, r1l),
        }
    one = diff.canonical_bytes(diff.compare(Side(repo, r0), Side(repo, scratch / "patched")))
    two = diff.canonical_bytes(diff.compare(Side(repo, r0), Side(repo, r1)))
    b["compare_bytes_equal"] = one == two
    b["compare_bytes_len"] = len(one)
    # (d) S31 on both sides
    rs = rules.load(RULES)
    with ExitStack() as st:
        r0l = checker.open_snapshot(st, Side(repo, r0))
        before = checker.check(r0l, rs)
        tl = checker.open_snapshot(st, Side(repo, scratch / "patched"))
        staged = checker.check(tl, rs)
    real = checker.run(Side(repo, r1), RULES)
    checker.validate_report(before)
    checker.validate_report(staged)
    res["d"] = {
        "staged_report_bytes_equal_real": checker.canonical_bytes(staged) == checker.canonical_bytes(real),
        "report_id_equal": staged["report_id"] == real["report_id"],
        "base_report_equals_run": checker.canonical_bytes(before)
        == checker.canonical_bytes(checker.run(Side(repo, r0), RULES)),
    }
    if not b["snapshot_id_equal"]:
        # normalised comparison (ids differ): strip snapshot block + report_id
        def norm(r):
            r = json.loads(json.dumps(r))
            r.pop("snapshot")
            r.pop("report_id")
            return r

        res["d"]["normalised_equal"] = norm(staged) == norm(real)
    res["b"] = b
    # AC-007 triggers on the staged side (prototype staging; real indexer/checker)
    res["triggers"] = triggers(repo, r0, scratch, rs)
    return res


def staged_checker_report(repo, r0, scratch, name, edits, rs, limits=None):
    with ExitStack() as st:
        r0l = lease(st, repo, r0)
        overlays = pack_bytes(r0l)
        for p, content in edits.items():
            if content is None:
                overlays.pop(p)
            else:
                overlays[p] = content.encode()
        t = scratch / name
        t.mkdir()
        stage(repo, r0l, overlays, t)
        tl = checker.open_snapshot(st, Side(repo, t))
        return checker.check(tl, rs, limits), tl


def triggers(repo, r0, scratch, rs):
    # BASE tree is expected live; restore it to BASE bytes for the staged edits (pack bytes are BASE)
    out = {}
    # (i) parse error -> extraction failed
    with ExitStack() as st:
        r0l = lease(st, repo, r0)
        overlays = pack_bytes(r0l)
        overlays["pkg/domain/b.py"] = b"def fb(:\n"
        t = scratch / "t-parse"
        t.mkdir()
        stage(repo, r0l, overlays, t)
        tl = lease(st, repo, t)
        failed = [r for r in observations(tl) if r["type"] == "observation" and r["state"] != "indexed"]
        out["i_parse_error"] = {
            "failed": [(r["path"], r["state"], r["reason"]) for r in failed],
            "record_types": sorted({r["type"] for r in observations(tl)}),
        }
        cs, _l, _w = impact.change_set(r0l, tl, dict(DEFAULT_DIFF_LIMITS))
        out["i_parse_error"]["partial_paths"] = cs["partial_paths"]
        out["i_parse_error"]["categories"] = sorted({c["category"] for c in cs["changes"]})
        out["i_parse_error"]["unknown_reasons"] = sorted(
            {c["reason"] for c in cs["changes"] if c["category"] == "unknown"}
        )
        # (ii)/(iii) limits
        for name, lim in (
            ("ii_changed_file_limit", {**DEFAULT_DIFF_LIMITS, "max_changed_files": 0}),
            ("iii_entity_limit", {**DEFAULT_DIFF_LIMITS, "max_entities": 1}),
        ):
            try:
                impact.change_set(r0l, tl, lim)
                out[name] = "no_error"
            except impact.ImpactError as exc:
                out[name] = {"code": exc.code, "reason": exc.reason}
    # (iv) --max-depth 1 on a 2-hop introduce: a -> b -> c(infra)
    edits = {"pkg/domain/b.py": VIOL_B}
    rep, _ = staged_checker_report(repo, r0, scratch, "t-depth", edits, rs, {"max_depth": 1})
    out["iv_depth1"] = [
        {"id": r["id"], "outcome": r["outcome"], "reasons": sorted(r["reasons"]),
         "violating_pairs": r["counts"]["violating_pairs"],
         "counterexamples": r["counts"]["counterexamples"]}
        for r in rep["rules"]
    ]
    rep, _ = staged_checker_report(repo, r0, scratch, "t-full", edits, rs)
    out["iv_full"] = [
        {"id": r["id"], "outcome": r["outcome"], "reasons": sorted(r["reasons"]),
         "violating_pairs": r["counts"]["violating_pairs"]}
        for r in rep["rules"]
    ]
    # (v) absolute `import json` in a.py
    edits = {"pkg/domain/a.py": "import json\nfrom .b import fb\n\n\ndef fa():\n    return fb()\n"}
    rep, _ = staged_checker_report(repo, r0, scratch, "t-json", edits, rs)
    out["v_import_json"] = [
        {"id": r["id"], "outcome": r["outcome"], "reasons": sorted(r["reasons"])} for r in rep["rules"]
    ]
    edits = {"pkg/domain/a.py": "from .missing import nothing\nfrom .b import fb\n\n\ndef fa():\n    return fb()\n"}
    rep, _ = staged_checker_report(repo, r0, scratch, "t-missing", edits, rs)
    out["v_relative_missing"] = [
        {"id": r["id"], "outcome": r["outcome"], "reasons": sorted(r["reasons"])} for r in rep["rules"]
    ]
    # empty selection (delete the last source file of rule 0)
    edits = {"pkg/domain/a.py": None}
    rep, _ = staged_checker_report(repo, r0, scratch, "t-empty", edits, rs)
    out["empty_source_selection"] = [
        {"id": r["id"], "outcome": r["outcome"], "reasons": sorted(r["reasons"])} for r in rep["rules"]
    ]
    return out


def part_a2(work: Path):
    """(a2): history records / incremental lineage."""
    res = {}
    repo = mk_repo(work / "w", BASE)
    root = work / "RH"
    scratch = work / "scratch"
    scratch.mkdir()
    build(repo, root)
    res["step0_full"] = unmodified(repo, root, scratch, "h0")
    write_tree(repo, {"pkg/domain/b.py": "def fb(:\n"})
    build(repo, root, since="incremental")
    res["step1_broken_since"] = unmodified(repo, root, scratch, "h1")
    write_tree(repo, {"pkg/domain/b.py": BASE["pkg/domain/b.py"]})
    build(repo, root, since="incremental")
    res["step2_restored_since"] = unmodified(repo, root, scratch, "h2")
    write_tree(repo, {"pkg/domain/e.py": None})
    build(repo, root, since="incremental")
    res["step3_deleted_since"] = unmodified(repo, root, scratch, "h3")
    # a broken file with no predecessor (fresh full index of a broken tree): no history
    repo2 = mk_repo(work / "w2", {**BASE, "pkg/domain/b.py": "def fb(:\n"})
    build(repo2, work / "RB")
    res["step4_fresh_broken_full"] = unmodified(repo2, work / "RB", scratch, "h4")
    proof = {}
    for k, v in res.items():
        proof[k] = bool(
            v.get("snapshot_id_equal")
            and v.get("changes") == 0
            and v.get("partial_paths") == []
        )
    res["equality_proved_per_step"] = proof
    res["has_non_observation_records"] = {
        k: any(t != "observation" for t in v.get("base_record_types", []))
        for k, v in res.items()
        if isinstance(v, dict) and "base_record_types" in v
    }
    return res


def part_g(work: Path):
    repo = work / "self"
    for src_root, dirs, names in os.walk("/ws"):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "node_modules", "__pycache__"}
                   and not os.path.islink(os.path.join(src_root, d))]
        rel = os.path.relpath(src_root, "/ws")
        for name in names:
            src = os.path.join(src_root, name)
            if os.path.islink(src):
                continue
            dst = repo / rel / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    git(repo, "init", "-q")
    root = work / "Rself"
    t0 = time.perf_counter()
    stats = build(repo, root)
    index_ms = int((time.perf_counter() - t0) * 1000)
    out = {"index_ms": index_ms, "index_stats": {k: v for k, v in stats.items() if isinstance(v, int)}}
    rs = rules.load(
        json.dumps(
            {
                "format": "atlas-architecture-rules.v1",
                "rules": [
                    {"id": "q", "relation": "import", "from": ["mcp-server/src/atlas_aci/query/**"],
                     "to": ["mcp-server/src/atlas_aci/server.py"]}
                ],
            }
        ).encode()
    )
    with ExitStack() as st:
        r0 = lease(st, repo, root)
        t0 = time.perf_counter()
        overlays = pack_bytes(r0)
        total = sum(len(v) for v in overlays.values())
        out["files"] = len(overlays)
        out["bytes"] = total
        target = "mcp-server/src/atlas_aci/memex.py"
        overlays[target] = overlays[target] + b"\n\ndef zz_added():\n    return 1\n"
        pack_ms = int((time.perf_counter() - t0) * 1000)
        t1 = time.perf_counter()
        tmp = work / "Tself"
        tmp.mkdir()
        stage(repo, r0, overlays, tmp)
        stage_ms = int((time.perf_counter() - t1) * 1000)
        t2 = time.perf_counter()
        tl = lease(st, repo, tmp)
        diff._check_compatible(r0, tl)
        cs, _l, work_ = impact.change_set(r0, tl, dict(DEFAULT_DIFF_LIMITS))
        cs_ms = int((time.perf_counter() - t2) * 1000)
        t3 = time.perf_counter()
        r0a = checker.open_snapshot(st, Side(repo, root))
        checker.check(r0a, rs)
        tla = checker.open_snapshot(st, Side(repo, tmp))
        checker.check(tla, rs)
        s31_ms = int((time.perf_counter() - t3) * 1000)
    out.update(
        pack_read_ms=pack_ms, staging_build_ms=stage_ms, change_set_ms=cs_ms, s31_two_sides_ms=s31_ms,
        wall_ms=pack_ms + stage_ms + cs_ms + s31_ms, changes=len(cs["changes"]),
        peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
    )
    return out


def synth(repo_files, n):
    files = {}
    per = 50
    for i in range(n):
        pkg = f"p{i // per}"
        files.setdefault(f"{pkg}/__init__.py", "")
        prev = f"from .m{i - 1} import f{i - 1}\n\n" if i % per else ""
        body = "".join(
            f"def g{i}_{k}(x, y=1):\n    '''doc {k}'''\n    total = x + y + {k}\n    return total * 2\n\n\n"
            for k in range(6)
        )
        files[f"{pkg}/m{i}.py"] = prev + f"def f{i}(a):\n    return a + {i}\n\n\n" + body
    return files


def part_g2(work: Path, n: int):
    repo = mk_repo(work / "w", synth(None, n))
    root = work / "Rn"
    t0 = time.perf_counter()
    build(repo, root)
    index_ms = int((time.perf_counter() - t0) * 1000)
    with ExitStack() as st:
        r0 = lease(st, repo, root)
        overlays = pack_bytes(r0)
        total = sum(len(v) for v in overlays.values())
        overlays["p0/m0.py"] = overlays["p0/m0.py"] + b"\ndef extra():\n    return 1\n"
        tmp = work / "Tn"
        tmp.mkdir()
        t1 = time.perf_counter()
        stage(repo, r0, overlays, tmp)
        stage_ms = int((time.perf_counter() - t1) * 1000)
        t2 = time.perf_counter()
        tl = lease(st, repo, tmp)
        cs, _l, _w = impact.change_set(r0, tl, dict(DEFAULT_DIFF_LIMITS))
        cs_ms = int((time.perf_counter() - t2) * 1000)
    return {
        "n_files": len(overlays), "bytes": total, "index_ms": index_ms, "staging_build_ms": stage_ms,
        "change_set_ms": cs_ms, "staging_ms_per_file": round(stage_ms / len(overlays), 3),
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "changes": len(cs["changes"]),
    }


def part_h(work: Path):
    """(h): the 3 s git identity probe timeout under load."""
    repo = mk_repo(work / "w", BASE)
    r0 = work / "R0"
    build(repo, r0)
    shim = work / "shim"
    shim.mkdir()
    (shim / "git").write_text("#!/bin/sh\nsleep 5\n")
    (shim / "git").chmod(0o755)
    out = {}
    with ExitStack() as st:
        r0l = lease(st, repo, r0)
        overlays = pack_bytes(r0l)
        tmp = work / "T"
        tmp.mkdir()
        old = os.environ["PATH"]
        os.environ["PATH"] = str(shim) + os.pathsep + old
        try:
            t0 = time.perf_counter()
            stage(repo, r0l, overlays, tmp)
            out["staging_with_slow_git_ms"] = int((time.perf_counter() - t0) * 1000)
        finally:
            os.environ["PATH"] = old
        tl = lease(st, repo, tmp)
        out["base_identity_kind"] = r0l._source_lease.manifest["context"]["identity"]["kind"]
        out["staged_identity_kind"] = tl._source_lease.manifest["context"]["identity"]["kind"]
        try:
            diff._check_compatible(r0l, tl)
            out["check_compatible"] = "passed"
        except diff.DiffRejected as exc:
            out["check_compatible"] = "DiffRejected:" + exc.reason
        try:
            impact.change_set(r0l, tl, dict(DEFAULT_DIFF_LIMITS))
        except impact.ImpactError as exc:
            out["change_set"] = {"code": exc.code, "reason": exc.reason}
    return out


def main():
    part = sys.argv[1]
    PARTS.mkdir(exist_ok=True)
    if part == "merge":
        merged = {}
        for f in sorted(PARTS.glob("*.json")):
            merged[f.stem] = json.loads(f.read_text())
        (EVID / "qualification.json").write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
        return
    work = Path(tempfile.mkdtemp(prefix="q32-", dir="/tmp"))
    try:
        if part == "main":
            res = part_main(work)
        elif part == "a2":
            res = part_a2(work)
        elif part == "g":
            res = part_g(work)
        elif part == "g2":
            res = part_g2(work, int(sys.argv[2]))
            part = f"g2_{sys.argv[2]}"
        elif part == "h":
            res = part_h(work)
        else:
            raise SystemExit("unknown part")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    (PARTS / f"{part}.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(json.dumps(res, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
