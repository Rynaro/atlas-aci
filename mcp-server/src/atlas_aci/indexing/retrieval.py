"""Native-only whole-source/entity retrieval documents with two-store closure.

No live filesystem reads: available text comes exclusively from an authenticated
source lease built from the consumed buffers. This derivative is not portable.
"""

from __future__ import annotations

import hashlib
import json

from atlas_aci.storage import evidence as ev

FORMAT = "atlas-retrieval.v1"
TOKENIZER = "unicode61 remove_diacritics 2"
POLICY = "entity-metadata+indexed-whole-utf8.v1"
COLUMNS = (
    "candidate_id",
    "kind",
    "entity_id",
    "qualified_label",
    "name",
    "path",
    "language",
    "text",
    "provenance",
)
DDL = """
CREATE TABLE IF NOT EXISTS retrieval_documents (
    candidate_id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT,
    qualified_label TEXT, name TEXT, path TEXT NOT NULL, language TEXT NOT NULL,
    text TEXT NOT NULL, provenance TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retrieval_qualified
ON retrieval_documents(qualified_label,candidate_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_path ON retrieval_documents(path,candidate_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_name ON retrieval_documents(name,candidate_id);
CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(
    candidate_id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2'
);
"""


def digest(value):
    return hashlib.sha256(ev.canonical(value).encode()).hexdigest()


def qualified_label(entity):
    return ev.canonical(
        {
            "format": "atlas-qualified-label.v1",
            **{
                key: entity[key]
                for key in (
                    "language",
                    "path",
                    "owners",
                    "kind",
                    "name",
                    "ordinal",
                    "identity_basis",
                )
            },
        }
    )


def observations(conn):
    row = conn.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()
    return [] if row is None else [r for r in json.loads(row[0]) if r["type"] == "observation"]


def expected(conn, source, source_policy):
    """Derive exact documents from canonical current records and authenticated bytes."""
    ev.require(
        source_policy in {"available", "import", "import_v1", "legacy_source"},
        "retrieval source policy",
    )
    ev.require(
        (source is not None) == (source_policy == "available"), "retrieval source availability"
    )
    observed = observations(conn)
    by_path = {r["path"]: r for r in observed}
    excluded = {}
    source_count = 0
    source_bytes = 0
    docs = []
    # References remain real canonical IDs; no invented assertion for a phrase.
    definitions = {}
    for row in conn.execute("SELECT payload FROM occurrences ORDER BY id"):
        item = json.loads(row[0])
        if item["kind"] == "definition":
            definitions.setdefault(item["entity_id"], item)
    proofs = {}
    for row in conn.execute("SELECT payload FROM evidence ORDER BY id"):
        item = json.loads(row[0])
        proofs.setdefault(item["occurrence_id"], item)
    for row in conn.execute("SELECT payload FROM entities ORDER BY id"):
        entity = json.loads(row[0])
        ev.validate_record(entity)
        label = qualified_label(entity)
        occurrence = definitions.get(entity["id"])
        proof = proofs.get(occurrence["id"]) if occurrence else None
        reference = {
            "state": "unavailable",
            "reason": source_policy if source_policy != "available" else "unknown_span",
        }
        if (
            source is not None
            and occurrence is not None
            and proof is not None
            and occurrence["provenance_state"] == "source"
        ):
            obs = by_path.get(entity["path"])
            if (
                obs
                and obs["state"] == "indexed"
                and obs["observed_source_sha256"] == occurrence["source_sha256"]
                and source.bindings.get((entity["path"], occurrence["source_sha256"])) is True
            ):
                reference = {
                    "state": "available",
                    "path": entity["path"],
                    "source_sha256": occurrence["source_sha256"],
                    "start_byte": occurrence["start_byte"],
                    "end_byte": occurrence["end_byte"],
                    "granularity": "occurrence",
                }
        provenance = {
            "entity": entity,
            "occurrence_id": occurrence["id"] if occurrence else None,
            "evidence_id": proof["id"] if proof else None,
            "source": reference,
        }
        docs.append(
            {
                "candidate_id": entity["id"],
                "kind": "entity",
                "entity_id": entity["id"],
                "qualified_label": label,
                "name": entity["name"],
                "path": entity["path"],
                "language": entity["language"],
                "text": entity["name"] + "\n" + label + "\n" + entity["path"],
                "provenance": ev.canonical(provenance),
            }
        )
    if source is not None:
        ev.require(
            {r["path"]: r["sha256"] for r in source.manifest["current"]}
            == {r["path"]: r["observed_source_sha256"] for r in observed},
            "retrieval observation vector",
        )
        for item in sorted(observed, key=lambda r: r["path"]):
            if item["state"] != "indexed":
                reason = "observation_" + item["state"]
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            raw = source.read(item["path"], item["observed_source_sha256"])
            try:
                text = raw.decode("utf-8", "strict")
            except UnicodeDecodeError:
                excluded["non_utf8"] = excluded.get("non_utf8", 0) + 1
                continue
            ev.require(text.encode("utf-8") == raw, "retrieval consumed UTF8")
            provenance = {
                "kind": "source_file",
                "path": item["path"],
                "language": item["language"],
                "source_sha256": item["observed_source_sha256"],
                "start_byte": 0,
                "end_byte": len(raw),
                "granularity": "file",
                "availability": "available",
            }
            identifier = digest({"format": FORMAT, **provenance})
            docs.append(
                {
                    "candidate_id": identifier,
                    "kind": "source_file",
                    "entity_id": None,
                    "qualified_label": None,
                    "name": None,
                    "path": item["path"],
                    "language": item["language"],
                    "text": text,
                    "provenance": ev.canonical(provenance),
                }
            )
            source_count += 1
            source_bytes += len(raw)
    coverage = {
        "state": "complete"
        if source is not None and not excluded
        else "partial"
        if source is not None
        else "unavailable",
        "source_policy": source_policy,
        "source_files": source_count,
        "source_bytes": source_bytes,
        "excluded": [
            {"reason": reason, "count": count} for reason, count in sorted(excluded.items())
        ],
    }
    docs.sort(key=lambda d: d["candidate_id"])
    ev.require(len({d["candidate_id"] for d in docs}) == len(docs), "duplicate retrieval document")
    return docs, coverage


def logical(row):
    return {
        **{key: row[key] for key in COLUMNS if key != "text"},
        "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
    }


def descriptor(docs, coverage):
    return {
        "format": FORMAT,
        "tokenizer": TOKENIZER,
        "document_policy": POLICY,
        "document_count": len(docs),
        "entity_count": sum(d["kind"] == "entity" for d in docs),
        "source_count": sum(d["kind"] == "source_file" for d in docs),
        "coverage": coverage,
        "document_digest": digest([logical(d) for d in docs]),
    }


def populate(conn, source, source_policy):
    docs, coverage = expected(conn, source, source_policy)
    conn.execute("DELETE FROM retrieval_fts")
    conn.execute("DELETE FROM retrieval_documents")
    for item in docs:
        conn.execute(
            "INSERT INTO retrieval_documents("
            + ",".join(COLUMNS)
            + ") VALUES ("
            + ",".join("?" for _ in COLUMNS)
            + ")",
            tuple(item[k] for k in COLUMNS),
        )
        conn.execute(
            "INSERT INTO retrieval_fts(candidate_id,text) VALUES (?,?)",
            (item["candidate_id"], item["text"]),
        )
    result = descriptor(docs, coverage)
    validate(conn, result, source, source_policy)
    return result


def shape(value):
    ev.require(
        isinstance(value, dict)
        and set(value)
        == {
            "format",
            "tokenizer",
            "document_policy",
            "document_count",
            "entity_count",
            "source_count",
            "coverage",
            "document_digest",
        },
        "closed retrieval descriptor",
    )
    ev.require(
        value["format"] == FORMAT
        and value["tokenizer"] == TOKENIZER
        and value["document_policy"] == POLICY,
        "retrieval policy",
    )
    ev.require(
        all(ev.integer(value[k]) for k in ("document_count", "entity_count", "source_count"))
        and value["document_count"] == value["entity_count"] + value["source_count"]
        and ev.hash_value(value["document_digest"]),
        "retrieval counts",
    )
    c = value["coverage"]
    ev.require(
        isinstance(c, dict)
        and set(c) == {"state", "source_policy", "source_files", "source_bytes", "excluded"},
        "closed retrieval coverage",
    )
    ev.require(
        c["state"] in {"complete", "partial", "unavailable"}
        and c["source_policy"] in {"available", "import", "import_v1", "legacy_source"}
        and all(ev.integer(c[k]) for k in ("source_files", "source_bytes"))
        and c["source_files"] == value["source_count"],
        "retrieval coverage",
    )
    ev.require(
        isinstance(c["excluded"], list)
        and all(
            isinstance(r, dict)
            and set(r) == {"reason", "count"}
            and isinstance(r["reason"], str)
            and bool(r["reason"])
            and ev.integer(r["count"], 1)
            for r in c["excluded"]
        ),
        "retrieval exclusions",
    )


def stores(conn, value):
    """Read actual INTERNAL FTS text/IDs as well as ordinary keyed rows."""
    shape(value)
    conn.execute("PRAGMA temp_store=MEMORY")
    ev.require(
        [tuple(row) for row in conn.execute("PRAGMA integrity_check(retrieval_fts)")] == [("ok",)],
        "FTS integrity",
    )
    docs = []
    for row in conn.execute(
        "SELECT " + ",".join(COLUMNS) + " FROM retrieval_documents ORDER BY candidate_id"
    ):
        item = dict(zip(COLUMNS, row, strict=True))
        ev.require(
            ev.hash_value(item["candidate_id"])
            and item["kind"] in {"entity", "source_file"}
            and ev.relative_path(item["path"])
            and isinstance(item["text"], str),
            "retrieval row",
        )
        docs.append(item)
    rows = conn.execute("SELECT candidate_id,text FROM retrieval_fts ORDER BY candidate_id")
    for item in docs:
        row = rows.fetchone()
        ev.require(
            row is not None and tuple(row) == (item["candidate_id"], item["text"]),
            "FTS ID/text bijection",
        )
    ev.require(rows.fetchone() is None, "extra FTS document")
    ev.require(descriptor(docs, value["coverage"]) == value, "retrieval descriptor digest")
    return docs


def validate(conn, value, source, source_policy):
    docs = stores(conn, value)
    wanted, coverage = expected(conn, source, source_policy)
    ev.require(
        docs == wanted and value == descriptor(wanted, coverage),
        "retrieval canonical/source document closure",
    )
