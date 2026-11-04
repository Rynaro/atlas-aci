"""G-IMAGE comparison of atlas-aci graph databases (stdlib only: sqlite3, json, sys).

    python -I compare_graphs.py BASE.db OFF.db ON.db     # base image, new OFF, new ON
    python -I compare_graphs.py REF.db CAND.db           # candidate may add rails-owned records
    python -I compare_graphs.py --digest DB.db           # comparison digest of one database

Prints one JSON verdict and exits 0 only when every check passes. Every compared table must
have a non-empty row count, so no comparison is vacuous. Rails-owned records are the producer
with a `rails` fingerprint key, the evidence it owns, the occurrences those cite and the
assertions citing that evidence.

Explicit exclusions (legitimately differ between builds or images):
  files.indexed_at, manifest.generation_id, manifest.generation_inputs.snapshot (snapshot id
  and manifest sha), the whole retrieval fts index, and retrieval_documents.candidate_id (the
  retrieval documents are compared by a candidate-id-free projection). Producer-derived link
  ids (refs.evidence_id, edges.assertion_id, evidence ids, assertion ids) are compared only
  through their normalized content, because the native producer id churns (decision D10).
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


def load(path):
    conn = open_db(path)
    producers = {p["id"]: p for p in payloads(conn, "producers")}
    rails_producers = {
        i for i, p in producers.items() if isinstance(p["fingerprint"], dict) and "rails" in p["fingerprint"]
    }
    evidence = {e["id"]: e for e in payloads(conn, "evidence")}
    rails_evidence = {i for i, e in evidence.items() if e["producer_id"] in rails_producers}
    rails_occurrences = {evidence[i]["occurrence_id"] for i in rails_evidence}
    assertions = {a["id"]: a for a in payloads(conn, "assertions")}
    rails_assertions = {i for i, a in assertions.items() if a["evidence_id"] in rails_evidence}
    occurrences = {o["id"]: o for o in payloads(conn, "occurrences")}
    entities = {e["id"] for e in payloads(conn, "entities")}
    native_assertions = Counter(
        json.dumps(
            [
                a["source_entity_id"],
                a["target_entity_id"],
                a["relation"],
                a["resolution"],
                a["candidate_ids"],
                a["raw_target"],
                a["unresolved_reason"],
                evidence[a["evidence_id"]]["occurrence_id"],
            ]
        )
        for i, a in assertions.items()
        if i not in rails_assertions
    )
    manifest = dict(conn.execute("SELECT key, value FROM manifest").fetchall())
    inputs = json.loads(manifest["generation_inputs"])
    inputs.pop("snapshot", None)
    data = {
        "entity_ids": entities,
        "occurrence_ids": {i for i in occurrences if i not in rails_occurrences},
        "rails_occurrence_ids": rails_occurrences,
        "evidence_ids": {i for i in evidence if i not in rails_evidence},
        "assertion_ids": {i for i in assertions if i not in rails_assertions},
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
    info = {
        "native_producer_ids": sorted(i for i in producers if i not in rails_producers),
        "rails_producer_ids": sorted(rails_producers),
        "rails_records": {
            "occurrences": len(rails_occurrences),
            "evidence": len(rails_evidence),
            "assertions": len(rails_assertions),
        },
        "rails_routes_manifest": "rails_routes" in manifest,
        "manifest_keys": sorted(manifest),
        "ids": {
            "evidence": evidence.keys() - rails_evidence,
            "assertion": assertions.keys() - rails_assertions,
            "producer": producers.keys() - rails_producers,
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


def compare(label, left, right, strict_ids, skip=()):
    """`left`/`right` are (data, info) pairs; rails-owned records are already separated."""
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
        "rails_producer_ids": info["rails_producer_ids"],
        "exclusions": EXCLUSIONS,
    }


def main(argv):
    if len(argv) == 3 and argv[1] == "--digest":
        print(json.dumps(digest(argv[2]), indent=1, sort_keys=True))
        return 0
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    loaded = [load(path) for path in argv[1:]]
    verdict = {"exclusions": EXCLUSIONS, "databases": {}, "pairs": [], "extra": []}
    names = ["base", "off", "on"] if len(loaded) == 3 else ["reference", "candidate"]
    for name, path, (data, info) in zip(names, argv[1:], loaded, strict=True):
        verdict["databases"][name] = {
            "path": path,
            "native_producer_ids": info["native_producer_ids"],
            "rails_producer_ids": info["rails_producer_ids"],
            "rails_records": info["rails_records"],
            "rails_routes_manifest": info["rails_routes_manifest"],
            "row_counts": {k: size(v) for k, v in data.items() if k != "generation_inputs_without_snapshot"},
        }
    extra = verdict["extra"]
    if len(loaded) == 3:
        base, off, on = loaded
        # Base vs new image: the native producer id churns by design (D10), so evidence and
        # assertion ids differ; their normalized content must not.
        verdict["pairs"].append(compare(
                "base vs off",
                base,
                off,
                strict_ids=False,
                skip=("generation_inputs_without_snapshot",),
            ))
        verdict["pairs"].append(compare("off vs on (minus rails-owned)", off, on, strict_ids=True))
        checks = {
            "base_has_no_rails": not base[1]["rails_producer_ids"] and not base[1]["rails_routes_manifest"],
            "off_has_no_rails": not off[1]["rails_producer_ids"] and not off[1]["rails_routes_manifest"],
            "on_has_rails_manifest": on[1]["rails_routes_manifest"],
            "on_native_producers_equal_off": (
                on[1]["native_producer_ids"] == off[1]["native_producer_ids"]
            ),
            "base_off_native_producer_ids_differ_expected_churn": (
                base[1]["native_producer_ids"] != off[1]["native_producer_ids"]
            ),
        }
        if on[1]["rails_records"]["occurrences"] > 0:
            # A repository with routes: ON = OFF's producers + exactly one rails producer, and
            # the rails records exist only in ON.
            checks["on_has_exactly_one_rails_producer"] = len(on[1]["rails_producer_ids"]) == 1
            checks["rails_records_only_in_on"] = off[1]["rails_records"]["occurrences"] == 0
        else:
            # No routes (for example a Python repository): ON must equal OFF fully, with no
            # rails producer and no rails record.
            checks["on_has_no_rails_records"] = (
                not on[1]["rails_producer_ids"]
                and on[1]["rails_records"] == {"assertions": 0, "evidence": 0, "occurrences": 0}
            )
    else:
        ref, cand = loaded
        verdict["pairs"].append(compare(
                "reference vs candidate",
                ref,
                cand,
                strict_ids=False,
                skip=("generation_inputs_without_snapshot",),
            ))
        checks = {"reference_has_no_rails": not ref[1]["rails_producer_ids"]}
    extra.append(checks)
    verdict["ok"] = all(p["ok"] for p in verdict["pairs"]) and all(checks.values())
    print(json.dumps(verdict, indent=1, sort_keys=True))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
