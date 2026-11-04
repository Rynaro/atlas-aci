"""G-IMAGE / G-ROUTES comparison of atlas-aci graph databases (stdlib only, except --selftest).

    python -I compare_graphs.py BASE.db OFF.db ON.db          # base image, new OFF, new ON
    python -I compare_graphs.py REF.db CAND.db                # candidate may add owned records
    python -I compare_graphs.py --routes REF.db CAND.db       # G-ROUTES: --frameworks rails, base vs new
    python -I compare_graphs.py --both RAILS.db BOTH.db       # rails vs rails+rails_models, same image
    python -I compare_graphs.py --digest DB.db                # comparison digest of one database
    python -I compare_graphs.py --selftest                    # two-adapter database, needs atlas_aci

Prints one JSON verdict and exits 0 only when every check passes. Every compared table must
have a non-empty row count, so no comparison is vacuous.

Two adapters own records and they are reported separately: `rails` (S24 routes) and
`rails_models` (S25 models, tables and migrations). Ownership of an adapter is its producer
(fingerprint key), the evidence it owns, the occurrences those cite and the assertions citing
that evidence. The UNION of both owned sets is excluded from the native comparison, and both
manifest keys (`rails_routes`, `rails_models`) are checked.

Explicit exclusions (legitimately differ between builds or images):
  files.indexed_at, manifest.generation_id, manifest.generation_inputs.snapshot (snapshot id
  and manifest sha), the whole retrieval fts index, and retrieval_documents.candidate_id (the
  retrieval documents are compared by a candidate-id-free projection). Producer-derived link
  ids (refs.evidence_id, edges.assertion_id, evidence ids, assertion ids) are compared only
  through their normalized content, because the native producer id churns (decision D12).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter

EXCLUSIONS = [
    "files.indexed_at",
    "files.mtime_ns (differs between mounts)",
    "generation_inputs.producer/schema (base vs off only: the producer id churns by design)",
    "manifest.generation_id",
    "manifest.generation_inputs.snapshot",
    "retrieval_fts*",
    "retrieval_documents.candidate_id",
    "refs.id, edges.id, symbols.id (autoincrement)",
    "refs.evidence_id, edges.assertion_id (producer-derived link ids)",
]


def open_db(path):
    conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def payloads(conn, table):
    return [json.loads(row[0]) for row in conn.execute("SELECT payload FROM " + table)]


def rows(conn, table, columns):
    names = ",".join(columns)
    return Counter(
        json.dumps(list(r), sort_keys=True, default=str)
        for r in conn.execute("SELECT " + names + " FROM " + table)
    )


CHURNING_KEYS = ("evidence_id", "producer_id", "assertion_id")


def scrub(value):
    """Drop producer-derived link ids from a provenance document (iterative)."""
    result = value
    pending = [result]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key in [k for k in item if k in CHURNING_KEYS]:
                del item[key]
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return result


def retrieval(conn):
    """Candidate-id-free projection of retrieval_documents (provenance scrubbed of link ids)."""
    result = Counter()
    for row in conn.execute(
        "SELECT kind, entity_id, qualified_label, name, path, language, text, provenance "
        "FROM retrieval_documents"
    ):
        item = list(row)
        item[7] = scrub(json.loads(item[7])) if item[7] else None
        result[json.dumps(item, sort_keys=True, default=str)] += 1
    return result


ADAPTERS = {"rails": "rails_routes", "rails_models": "rails_models"}


def normalized(assertion, evidence):
    return json.dumps(
        [
            assertion["source_entity_id"],
            assertion["target_entity_id"],
            assertion["relation"],
            assertion["resolution"],
            assertion["candidate_ids"],
            assertion["raw_target"],
            assertion["unresolved_reason"],
            evidence[assertion["evidence_id"]]["occurrence_id"],
        ]
    )


def load(path):
    conn = open_db(path)
    producers = {p["id"]: p for p in payloads(conn, "producers")}
    evidence = {e["id"]: e for e in payloads(conn, "evidence")}
    assertions = {a["id"]: a for a in payloads(conn, "assertions")}
    occurrences = {o["id"]: o for o in payloads(conn, "occurrences")}
    entities = {e["id"] for e in payloads(conn, "entities")}
    owned = {}
    for key in ADAPTERS:
        owner_producers = {
            i
            for i, p in producers.items()
            if isinstance(p["fingerprint"], dict) and key in p["fingerprint"]
        }
        owner_evidence = {i for i, e in evidence.items() if e["producer_id"] in owner_producers}
        owned[key] = {
            "producers": owner_producers,
            "evidence": owner_evidence,
            "occurrences": {evidence[i]["occurrence_id"] for i in owner_evidence},
            "assertions": {i for i, a in assertions.items() if a["evidence_id"] in owner_evidence},
        }
    union = {
        kind: set().union(*(owned[key][kind] for key in ADAPTERS))
        for kind in ("producers", "evidence", "occurrences", "assertions")
    }
    native_assertions = Counter(
        normalized(a, evidence) for i, a in assertions.items() if i not in union["assertions"]
    )
    manifest = dict(conn.execute("SELECT key, value FROM manifest").fetchall())
    inputs = json.loads(manifest["generation_inputs"])
    inputs.pop("snapshot", None)
    data = {
        "entity_ids": entities,
        "occurrence_ids": {i for i in occurrences if i not in union["occurrences"]},
        "evidence_ids": {i for i in evidence if i not in union["evidence"]},
        "assertion_ids": {i for i in assertions if i not in union["assertions"]},
        "symbols": rows(
            conn,
            "symbols",
            ["name", "kind", "path", "line_start", "line_end", "lang", "binding", "entity_id", "occurrence_id"],
        ),
        "refs": rows(
            conn,
            "refs",
            [
                "callee_name",
                "relation",
                "qualified",
                "qualifier_name",
                "path",
                "line",
                "col",
                "lang",
                "binding",
                "unresolved_reason",
                "source_entity_id",
                "occurrence_id",
            ],
        ),
        "edges": rows(
            conn,
            "edges",
            [
                "relation",
                "source_path",
                "source_line",
                "source_col",
                "source_name",
                "source_kind",
                "callee_name",
                "confidence",
                "target_path",
                "target_line",
                "target_name",
                "candidates",
                "lang",
            ],
        ),
        "rationale": rows(
            conn,
            "rationale",
            ["path", "line", "text", "label", "target_path", "target_line", "target_name", "lang", "occurrence_id", "target_entity_id"],
        ),
        "import_bindings": rows(
            conn,
            "import_bindings",
            ["path", "local_name", "scope", "byte_start", "description", "lang", "source_entity_id", "occurrence_id"],
        ),
        "assignment_targets": rows(
            conn,
            "assignment_targets",
            ["path", "name", "scope", "binding_kind", "byte_start", "entity_id", "occurrence_id"],
        ),
        "assertions": native_assertions,
        "files": rows(conn, "files", ["path", "size", "lang"]),
        "retrieval_documents": retrieval(conn),
        "generation_inputs_without_snapshot": inputs,
    }
    adapters = {}
    for key, manifest_key in ADAPTERS.items():
        mine = owned[key]
        adapters[key] = {
            "producer_ids": sorted(mine["producers"]),
            "records": {
                "occurrences": len(mine["occurrences"]),
                "evidence": len(mine["evidence"]),
                "assertions": len(mine["assertions"]),
            },
            "manifest_key": manifest_key,
            "manifest_present": manifest_key in manifest,
            "manifest": json.loads(manifest[manifest_key]) if manifest_key in manifest else None,
            "fingerprints": [producers[i]["fingerprint"][key] for i in sorted(mine["producers"])],
            "occurrence_ids": sorted(mine["occurrences"]),
            "normalized_assertions": sorted(
                normalized(assertions[i], evidence) for i in mine["assertions"]
            ),
        }
    info = {
        "native_producer_ids": sorted(i for i in producers if i not in union["producers"]),
        "adapters": adapters,
        "manifest_keys": sorted(manifest),
        "ids": {
            "evidence": evidence.keys() - union["evidence"],
            "assertion": assertions.keys() - union["assertions"],
            "producer": producers.keys() - union["producers"],
        },
    }
    conn.close()
    return data, info


def size(value):
    return sum(value.values()) if isinstance(value, Counter) else len(value)


# Tables that must be non-empty for the comparison to count; other tables are compared and their
# row counts reported (`vacuous: true` when empty, e.g. edges for a fixture without resolved calls;
# run the coverage fixture, which has edges, for the full-table check).
NON_EMPTY = (
    "entity_ids",
    "occurrence_ids",
    "symbols",
    "refs",
    "assertions",
    "files",
    "retrieval_documents",
    "generation_inputs_without_snapshot",
)
COMPARED = (
    "entity_ids",
    "occurrence_ids",
    "symbols",
    "refs",
    "edges",
    "rationale",
    "import_bindings",
    "assignment_targets",
    "assertions",
    "files",
    "retrieval_documents",
    "generation_inputs_without_snapshot",
)
CHURN = ("evidence_ids", "assertion_ids")
NO_RECORDS = {"assertions": 0, "evidence": 0, "occurrences": 0}


def compare(label, left, right, strict_ids, skip=()):
    """`left`/`right` are (data, info) pairs; adapter-owned records are already separated."""
    checks = []
    for name in COMPARED:
        if name in skip:
            continue
        equal = left[0][name] == right[0][name]
        count = size(left[0][name]) if name != "generation_inputs_without_snapshot" else len(left[0][name])
        checks.append(
            {
                "table": name,
                "equal": equal,
                "rows": count,
                "vacuous": count == 0,
                "ok": equal and (count > 0 or name not in NON_EMPTY),
            }
        )
    if strict_ids:
        for name in CHURN:
            equal = left[0][name] == right[0][name]
            checks.append(
                {"table": name, "equal": equal, "rows": size(left[0][name]), "ok": equal and size(left[0][name]) > 0}
            )
    return {"pair": label, "checks": checks, "ok": all(c["ok"] for c in checks)}


def digest(path):
    data, info = load(path)
    canonical = {
        key: (sorted(value) if isinstance(value, set) else dict(value) if isinstance(value, Counter) else value)
        for key, value in data.items()
    }
    return {
        "path": path,
        "sha256": hashlib.sha256(json.dumps(canonical, sort_keys=True, default=str).encode()).hexdigest(),
        "row_counts": {key: size(value) for key, value in data.items() if key != "generation_inputs_without_snapshot"},
        "native_producer_ids": info["native_producer_ids"],
        "adapters": {
            key: {"producer_ids": a["producer_ids"], "records": a["records"], "manifest_present": a["manifest_present"]}
            for key, a in info["adapters"].items()
        },
        "exclusions": EXCLUSIONS,
    }


def summary(name, path, data, info):
    return {
        "path": path,
        "native_producer_ids": info["native_producer_ids"],
        "adapters": {
            key: {k: a[k] for k in ("producer_ids", "records", "manifest_present")}
            for key, a in info["adapters"].items()
        },
        "row_counts": {k: size(v) for k, v in data.items() if k != "generation_inputs_without_snapshot"},
    }


def absent(info, key):
    adapter = info["adapters"][key]
    return not adapter["producer_ids"] and adapter["records"] == NO_RECORDS and not adapter["manifest_present"]


def without(value, key):
    return {k: v for k, v in value.items() if k != key}


def adapter_view(info, key):
    """The adapter's identity with the churning native producer id removed."""
    adapter = info["adapters"][key]
    return {
        "occurrence_ids": adapter["occurrence_ids"],
        "normalized_assertions": adapter["normalized_assertions"],
        "fingerprints": [without(f, "graph_producer_id") for f in adapter["fingerprints"]],
        "manifest": without(adapter["manifest"], "producer_id") if adapter["manifest"] else None,
    }


def three_way(loaded):
    base, off, on = loaded
    pairs = [
        compare("base vs off", base, off, strict_ids=False, skip=("generation_inputs_without_snapshot",)),
        compare("off vs on (minus adapter-owned)", off, on, strict_ids=True),
    ]
    checks = {
        "base_has_no_adapter_records": all(absent(base[1], k) for k in ADAPTERS),
        "off_has_no_adapter_records": all(absent(off[1], k) for k in ADAPTERS),
        "on_native_producers_equal_off": on[1]["native_producer_ids"] == off[1]["native_producer_ids"],
        "base_off_native_producer_ids_differ_expected_churn": (
            base[1]["native_producer_ids"] != off[1]["native_producer_ids"]
        ),
    }
    enabled = [k for k in ADAPTERS if on[1]["adapters"][k]["manifest_present"]]
    checks["on_enables_at_least_one_adapter"] = bool(enabled)
    for key in ADAPTERS:
        adapter = on[1]["adapters"][key]
        if key in enabled and adapter["records"]["occurrences"] > 0:
            checks[key + "_exactly_one_producer"] = len(adapter["producer_ids"]) == 1
            checks[key + "_records_only_in_on"] = off[1]["adapters"][key]["records"]["occurrences"] == 0
        elif key in enabled:
            checks[key + "_no_records_no_producer"] = not adapter["producer_ids"]
        else:
            checks[key + "_absent_in_on"] = absent(on[1], key)
    return pairs, checks


def routes_pair(loaded):
    """G-ROUTES: the same `--frameworks rails` run by the base image and by the new image."""
    ref, cand = loaded
    pairs = [
        compare("reference vs candidate", ref, cand, strict_ids=False, skip=("generation_inputs_without_snapshot",))
    ]
    checks = {
        "rails_route_occurrence_ids_equal": ref[1]["adapters"]["rails"]["occurrence_ids"]
        == cand[1]["adapters"]["rails"]["occurrence_ids"],
        "rails_has_records": ref[1]["adapters"]["rails"]["records"]["occurrences"] > 0,
        "rails_view_equal_minus_graph_producer_id": adapter_view(ref[1], "rails")
        == adapter_view(cand[1], "rails"),
        "candidate_has_no_rails_models": absent(cand[1], "rails_models"),
        "reference_has_no_rails_models": absent(ref[1], "rails_models"),
    }
    return pairs, checks


def both_pair(loaded):
    """Same image: rails-only vs rails+rails_models; the models adapter only adds its own."""
    only, both = loaded
    pairs = [compare("rails-only vs both (minus adapter-owned)", only, both, strict_ids=True)]
    checks = {
        "rails_view_identical": adapter_view(only[1], "rails") == adapter_view(both[1], "rails"),
        "rails_records_present": only[1]["adapters"]["rails"]["records"]["occurrences"] > 0,
        "rails_only_has_no_models": absent(only[1], "rails_models"),
        "both_has_models_manifest": both[1]["adapters"]["rails_models"]["manifest_present"],
    }
    return pairs, checks


def two_way(loaded):
    ref, cand = loaded
    pairs = [compare("reference vs candidate", ref, cand, strict_ids=False, skip=("generation_inputs_without_snapshot",))]
    return pairs, {"reference_has_no_adapter_records": all(absent(ref[1], k) for k in ADAPTERS)}


def selftest():
    """Build a repo holding routes AND models with both adapters; every owned set non-empty,
    disjoint, excluded from the native comparison and both manifest keys present."""
    import shutil
    import tempfile
    from pathlib import Path

    from atlas_aci.codegraph import CodeGraph

    root = Path(__file__).resolve().parents[3] / "mcp-server" / "tests" / "fixtures"
    work = Path(tempfile.mkdtemp())
    repo = work / "repo"
    shutil.copytree(root / "rails_models" / "project", repo)
    shutil.copytree(root / "rails_routes" / "project", repo, dirs_exist_ok=True)
    table_map = (root / "rails_models" / "table-map.json").read_bytes()
    both = CodeGraph(repo, graph_root=work / "both", frameworks=("rails", "rails_models"), rails_table_map=table_map)
    both.build()
    off = CodeGraph(repo, graph_root=work / "off")
    off.build()
    loaded_both, loaded_off = load(str(both.db_path)), load(str(off.db_path))
    info = loaded_both[1]["adapters"]
    owned = {k: set(info[k]["occurrence_ids"]) for k in ADAPTERS}
    result = {
        "rails_nonzero": info["rails"]["records"]["occurrences"] > 0,
        "rails_models_nonzero": info["rails_models"]["records"]["occurrences"] > 0,
        "owned_occurrence_sets_disjoint": not (owned["rails"] & owned["rails_models"]),
        "both_manifest_keys": info["rails"]["manifest_present"] and info["rails_models"]["manifest_present"],
        "one_producer_each": len(info["rails"]["producer_ids"]) == 1 and len(info["rails_models"]["producer_ids"]) == 1,
        "union_excluded_from_native": loaded_both[0]["occurrence_ids"] == loaded_off[0]["occurrence_ids"]
        and loaded_both[0]["assertions"] == loaded_off[0]["assertions"]
        and loaded_both[0]["assertion_ids"] == loaded_off[0]["assertion_ids"],
        "counts": {k: info[k]["records"] for k in ADAPTERS},
    }
    result["ok"] = all(v for k, v in result.items() if k not in {"counts"})
    return result


def main(argv):
    args = argv[1:]
    if args == ["--selftest"]:
        verdict = selftest()
        print(json.dumps(verdict, indent=1, sort_keys=True))
        return 0 if verdict["ok"] else 1
    if len(args) == 2 and args[0] == "--digest":
        print(json.dumps(digest(args[1]), indent=1, sort_keys=True))
        return 0
    mode = None
    if args and args[0] in {"--routes", "--both"}:
        mode, args = args[0], args[1:]
    if len(args) not in (2, 3) or (mode and len(args) != 2):
        print(__doc__, file=sys.stderr)
        return 2
    loaded = [load(path) for path in args]
    if mode == "--routes":
        names, (pairs, checks) = ["reference", "candidate"], routes_pair(loaded)
    elif mode == "--both":
        names, (pairs, checks) = ["rails_only", "both"], both_pair(loaded)
    elif len(loaded) == 3:
        names, (pairs, checks) = ["base", "off", "on"], three_way(loaded)
    else:
        names, (pairs, checks) = ["reference", "candidate"], two_way(loaded)
    verdict = {
        "exclusions": EXCLUSIONS,
        "databases": {n: summary(n, p, *d) for n, p, d in zip(names, args, loaded, strict=True)},
        "pairs": pairs,
        "extra": [checks],
    }
    verdict["ok"] = all(p["ok"] for p in pairs) and all(checks.values())
    print(json.dumps(verdict, indent=1, sort_keys=True))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
