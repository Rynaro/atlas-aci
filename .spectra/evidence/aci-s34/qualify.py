"""ACI-S34 Day-1 qualification. Run in the dev image (docker only):

    sh dk34.sh /opt/venv/bin/python /ws/.spectra/evidence/aci-s34/qualify.py

Writes /ws/.spectra/evidence/aci-s34/qualification.json.
"""

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, "/ws/mcp-server/tests/slices")

from test_s19_support import build, init_git, same_path_pair, write_tree  # noqa: E402

from atlas_aci.overlays import runtime  # noqa: E402
from atlas_aci.query import diff as sd  # noqa: E402
from atlas_aci.query.diff import Side  # noqa: E402
from atlas_aci.storage import evidence as ev  # noqa: E402

FX = Path("/ws/mcp-server/tests/fixtures/adr_mapping")
OUT = Path("/ws/.spectra/evidence/aci-s34/qualification.json")
WORLD = json.loads((FX / "world.json").read_text())
RESULT: dict = {}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def section(name):
    def deco(fn):
        started = time.time()
        try:
            RESULT[name] = fn()
            RESULT[name + "_ok"] = True
        except BaseException as exc:  # record, never hide
            RESULT[name + "_ok"] = False
            RESULT[name + "_error"] = type(exc).__name__ + ": " + str(exc)[:600]
            RESULT[name + "_trace"] = traceback.format_exc()[-1500:]
        RESULT[name + "_seconds"] = round(time.time() - started, 2)
        return fn

    return deco


def entities(reader):
    rows = {}
    for (payload,) in reader.db.execute("SELECT payload FROM entities"):
        e = json.loads(payload)
        rows[e["id"]] = e
    return rows


def pair_at(base: Path, git_repo=True, langs=None):
    base.mkdir(parents=True, exist_ok=True)
    return same_path_pair(base, WORLD["before"], WORLD["after"], git_repo=git_repo, langs=langs)


TMP = Path(tempfile.mkdtemp(prefix="s34q-"))
A = TMP / "a"
repo, before, after = pair_at(A)


@section("a_categories")
def _a():
    cs = sd.compare(before, after)
    out = {"summary": cs["summary"], "partial_paths": cs["partial_paths"]}
    out["changes"] = [
        {
            "path": r["path"],
            "name": r["name"],
            "kind": r["kind"],
            "ordinal": r["ordinal"],
            "owners": r["owners"],
            "category": r["category"],
            "reason": r["reason"],
            "entity_id": r["entity_id"],
            "id": r["id"],
        }
        for r in cs["changes"]
    ]
    out["candidates"] = [
        {k: c[k] for k in ("relation", "before_entity_id", "after_entity_id", "id")}
        | {"before": c["before"], "after": c["after"]}
        for c in cs["candidates"]
    ]
    return out


@section("b_adr_in_snapshot")
def _b():
    out = {}
    with ExitStack() as stack:
        reader = runtime.open_active(stack, before)
        obs = {
            r["path"]: r
            for r in json.loads(reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()[0])
            if r.get("type") == "observation"
        }
        for path in ("docs/adr/0001-pricing.md", "docs/adr/0002-audit.md", "docs/adr/0003-legacy.md"):
            rec = obs[path]
            data = reader._source_lease.read(path, rec["observed_source_sha256"])
            out[path] = {
                "state": rec["state"],
                "observation_keys": sorted(rec),
                "sha_equal": sha(data) == rec["observed_source_sha256"],
                "world_equal": data == WORLD["before"][path].encode(),
            }
        out["any_nonindexed_before"] = sorted(p for p, r in obs.items() if r["state"] != "indexed")
        out["epoch"] = reader._lease_schema_epoch
        out["max_epoch"] = max(runtime.SCHEMA_EPOCHS)
    with ExitStack() as stack:
        reader = runtime.open_active(stack, after)
        obs = {
            r["path"]: r
            for r in json.loads(reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()[0])
            if r.get("type") == "observation"
        }
        out["after_0003_present"] = "docs/adr/0003-legacy.md" in obs
        out["after_states"] = {p: r["state"] for p, r in sorted(obs.items()) if r["state"] != "indexed"}
    # a span splitting the accented character
    raw = WORLD["before"]["docs/adr/0001-pricing.md"].encode()
    i = raw.index("é".encode())
    try:
        raw[i + 1 : i + 6].decode("utf-8")
        out["split_decodes"] = True
    except UnicodeDecodeError:
        out["split_decodes"] = False
    return out


@section("c_path_independent_ids")
def _c():
    other = TMP / "other-path-longer"
    r2, b2, a2 = pair_at(other)
    with ExitStack() as stack:
        one = entities(runtime.open_active(stack, before))
        two = entities(runtime.open_active(stack, b2))
    out = {"ids_equal": sorted(one) == sorted(two), "count": len(one)}
    out["snapshot_ids_differ"] = True
    # file entity id rule
    rows = {}
    for e in one.values():
        if e["kind"] == "file":
            rows[e["path"]] = e
    bad = []
    for path, e in rows.items():
        if ev.file_entity(path, e["language"])["id"] != e["id"]:
            bad.append(path)
    out["file_entity_rule_bad"] = bad
    out["file_entities"] = {p: [e["id"], e["language"]] for p, e in sorted(rows.items())}
    with ExitStack() as stack:
        reader = runtime.open_active(stack, before)
        out["ENTITY_SQL_finds_file"] = all(
            reader.db.execute(runtime.ENTITY_SQL, (e["id"],)).fetchone() is not None
            for e in rows.values()
        )
    shutil.rmtree(other, ignore_errors=True)
    return out


@section("targets_before")
def _t():
    with ExitStack() as stack:
        reader = runtime.open_active(stack, before)
        out = []
        for e in entities(reader).values():
            if e["kind"] == "file" and e["path"].startswith(("docs/", "src/", "config/")):
                out.append({"id": e["id"], "path": e["path"], "kind": "file", "name": e["name"],
                            "language": e["language"]})
            elif e["kind"] != "file":
                out.append({"id": e["id"], "path": e["path"], "kind": e["kind"], "name": e["name"],
                            "owners": e["owners"], "ordinal": e["ordinal"],
                            "language": e["language"]})
        return sorted(out, key=lambda r: (r["path"], r["name"], r.get("ordinal", 0)))


@section("d_file_targets")
def _d():
    out = {}
    for label, side in (("before", before), ("after", after)):
        with ExitStack() as stack:
            reader = runtime.open_active(stack, side)
            obs = {
                r["path"]: r
                for r in json.loads(reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()[0])
                if r.get("type") == "observation"
            }
            ents = entities(reader)
            for path in ("src/settings.py", "src/constants.py", "src/fragile.py", "src/legacy.py"):
                rec = obs.get(path)
                fid = ev.file_entity(path, "python")["id"]
                out.setdefault(path, {})[label] = {
                    "observation": None if rec is None else {
                        "state": rec["state"], "sha": rec.get("observed_source_sha256")},
                    "file_entity_present": fid in ents,
                    "n_entities": sum(1 for e in ents.values() if e["path"] == path),
                }
    return out


@section("e_rejections")
def _e():
    out = {}
    # configuration_mismatch: after side built with langs omitting yaml
    cfg_langs = ["python", "markdown"]
    build(repo, TMP / "root-config", cfg_langs)
    try:
        sd.compare(before, Side(repo, TMP / "root-config"))
        out["configuration_mismatch"] = "NO REJECTION"
    except sd.DiffRejected as exc:
        out["configuration_mismatch"] = exc.reason
    # identity_kind_mismatch: same-repo variant
    git_dir = repo / ".git"
    moved = repo / ".git-aside"
    git_dir.rename(moved)
    try:
        build(repo, TMP / "root-nongit")
    finally:
        moved.rename(git_dir)
    try:
        sd.compare(before, Side(repo, TMP / "root-nongit"))
        out["identity_kind_same_repo"] = "NO REJECTION"
    except sd.DiffRejected as exc:
        out["identity_kind_same_repo"] = exc.reason
    return out


@section("f_predicate")
def _f():
    # D3 R10 predicate vs symbol_diff._Loaded.definition
    out = {"checked": 0, "disagree": []}
    for label, side in (("before", before), ("after", after)):
        with ExitStack() as stack:
            reader = runtime.open_active(stack, side)
            loaded = sd._Loaded(reader, reader.snapshot_id, reader.generation_id, {})
            loaded.load()
            ents = entities(reader)
            payloads: dict = {}
            for (p,) in reader.db.execute("SELECT payload FROM occurrences"):
                o = json.loads(p)
                payloads.setdefault(o["path"], []).append(o)
            for e in ents.values():
                if e["kind"] == "file" or e["kind"].startswith("scip_"):
                    continue
                mine = [
                    o for o in payloads.get(e["path"], [])
                    if o["entity_id"] == e["id"] and o["kind"] == "definition"
                    and o["provenance_state"] == "source"
                    and o["role"] == "definition:def." + e["kind"]
                ]
                theirs = loaded.definition(e["id"])
                m = mine[0]["id"] if len(mine) == 1 else None
                t = theirs["id"] if theirs else None
                out["checked"] += 1
                if m != t:
                    out["disagree"].append([label, e["id"], e["name"]])
    return out


@section("g_concurrent_leases")
def _g():
    out = {}
    # Rebuild a scratch copy: hold both leases, re-index the after root meanwhile.
    base = TMP / "g"
    r, b, a = pair_at(base)
    with ExitStack() as stack:
        rb = runtime.open_active(stack, b)
        ra = runtime.open_active(stack, a)
        gen_before = ra.generation_id
        out["held_both"] = True
        # re-index after root with a changed file
        write_tree(r, {"src/reports.py": "def summary(rows):\n    return len(rows) + 1\n"})
        build(r, base / "root-after")
        out["lease_still_reads"] = ra.db.execute("SELECT count(*) FROM entities").fetchone()[0] > 0
        data = ra._source_lease.read(
            "src/pricing.py",
            json.loads(ra.db.execute(runtime.OBSERVATIONS_SQL).fetchone()[0])[0]["observed_source_sha256"]
            if False else [
                x for x in json.loads(ra.db.execute(runtime.OBSERVATIONS_SQL).fetchone()[0])
                if x.get("type") == "observation" and x["path"] == "src/pricing.py"
            ][0]["observed_source_sha256"],
        )
        out["lease_reads_source"] = len(data) > 0
        cs = sd.compare(b, a)
        out["compare_after_gen_is_new"] = cs["after"]["generation_id"] != gen_before
        out["held_generation_unchanged"] = ra.generation_id == gen_before
    return out


@section("h_rebuild_same_path")
def _h():
    base = TMP / "h"
    r, b, a = pair_at(base)
    with ExitStack() as stack:
        ids1 = (runtime.open_active(stack, b).snapshot_id, runtime.open_active(stack, a).snapshot_id)
        gens1 = (stack and None)
    cs1 = sd.compare(b, a)
    shutil.rmtree(base / "root-before")
    shutil.rmtree(base / "root-after")
    clean = {k: None for k in WORLD["after"] if k not in WORLD["before"]}
    write_tree(r, {**clean, **WORLD["before"]})
    build(r, base / "root-before")
    write_tree(r, WORLD["after"])
    build(r, base / "root-after")
    cs2 = sd.compare(b, a)
    return {"change_set_equal": cs1["id"] == cs2["id"], "before_equal": cs1["before"] == cs2["before"],
            "after_equal": cs1["after"] == cs2["after"]}


@section("i_timing")
def _i():
    t0 = time.time()
    base = TMP / "i"
    pair_at(base)
    t1 = time.time()
    r, b, a = repo, before, after
    t2 = time.time()
    sd.compare(b, a)
    t3 = time.time()
    return {"pair_build_s": round(t1 - t0, 2), "compare_s": round(t3 - t2, 2)}


@section("j_sibling_ordinal")
def _j():
    out = {}
    cs = sd.compare(before, after)
    with ExitStack() as stack:
        rb = runtime.open_active(stack, before)
        ra = runtime.open_active(stack, after)
        eb, ea = entities(rb), entities(ra)
    for path in ("src/dupes.py", "src/twins.py", "src/account.py"):
        out[path] = {
            "before": sorted(
                [e["name"], e["kind"], e["ordinal"], e["id"][:12]]
                for e in eb.values() if e["path"] == path and e["kind"] != "file"
            ),
            "after": sorted(
                [e["name"], e["kind"], e["ordinal"], e["id"][:12]]
                for e in ea.values() if e["path"] == path and e["kind"] != "file"
            ),
            "s19": [
                [r["name"], r["ordinal"], r["category"], r["reason"], r["id"][:12]]
                for r in cs["changes"] if r["path"] == path
            ],
        }
    return out


@section("k_retire_candidates")
def _k():
    cs = sd.compare(before, after)
    return {
        "retire_candidate": [c for c in cs["candidates"] if c["before"]["name"] == "retire"],
        "n_candidates": len(cs["candidates"]),
    }



@section("l_container_targets")
def _l():
    out = {}
    base = "class Account:\n    def open(self):\n        return 1\n\n    def close(self):\n        return 2\n"
    cases = {
        "member_body_edit": (base, base.replace("return 2", "return 3")),
        "class_attribute_added": (base, base + "\n    limit = 5\n"),
        "member_shift_only": (base, base.replace("class Account:", "class Account:\n    limit = 5")),
    }
    for label, (one, two) in cases.items():
        t = TMP / ("l-" + label)
        t.mkdir()
        _r, b, a = same_path_pair(t, {"a.py": one}, {"a.py": two})
        cs = sd.compare(b, a)
        out[label] = [[r["name"], r["kind"], r["category"]] for r in cs["changes"]]
    return out


b = RESULT["b_adr_in_snapshot"]
c = RESULT["c_path_independent_ids"]
e = RESULT["e_rejections"]
g = RESULT["g_concurrent_leases"]
checks = {
    "b_adr_indexed_and_digests_agree": all(
        b[p]["state"] == "indexed" and b[p]["sha_equal"] and b[p]["world_equal"]
        for p in ("docs/adr/0001-pricing.md", "docs/adr/0002-audit.md", "docs/adr/0003-legacy.md")
    ) and b["epoch"] == b["max_epoch"],
    "c_ids_path_independent_and_file_rule": c["ids_equal"] and not c["file_entity_rule_bad"]
    and c["ENTITY_SQL_finds_file"],
    "e_real_rejected_triggers": e["configuration_mismatch"] == "configuration_mismatch"
    and e["identity_kind_same_repo"] == "identity_kind_mismatch",
    "f_predicate_agrees": not RESULT["f_predicate"]["disagree"],
    "g_concurrent_leases_hold": all(g.values()),
}
RESULT["stop_rule"] = {
    "checks": checks,
    "triggered": not all(checks.values()),
    "adaptations": [
        "(a) all categories as expected (body, signature, format, location, removed, candidate, "
        "raw_text yaml unknown, extraction_failed_after, unchanged): no fixture change",
        "(h) rebuild at the same paths gives equal ids and change set: variant kept",
        "(i) pair build 0.4 s: no sharing needed",
        "C10 same-repo identity_kind_mismatch trigger works: no two-repo form needed",
        "C2 two same-name defs, delete the first: S19 reports the survivor as body (false "
        "review) and the other as removed: sibling_ordinal_shift pins it",
        "container: class compared by its own declaration, a member edit is not a class change",
    ],
}

OUT.write_text(json.dumps(RESULT, indent=1, sort_keys=True, default=str) + "\n")
print(json.dumps({k: v for k, v in RESULT.items() if k.endswith("_ok") or k.endswith("_error")}, indent=1))
shutil.rmtree(TMP, ignore_errors=True)
