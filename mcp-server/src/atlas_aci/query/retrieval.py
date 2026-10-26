"""Generation-owned exact-first/FTS candidates with charged, owned SQLite work."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
import unicodedata

from atlas_aci.enforcement import ToolError
from atlas_aci.indexing import retrieval as docs
from atlas_aci.storage import evidence as ev

LIMITS = {
    "max_candidates": (100, 1, 100),
    "max_vm_operations": (1000000, 0, 10000000),
    "deadline_ms": (2000, 0, 2000),
}
VM_QUANTUM = 1
TIERS = ("exact_entity_id", "exact_qualified_label", "exact_path", "exact_name", "lexical")
METADATA_COLUMNS = tuple(k for k in docs.COLUMNS if k != "text")


def utf8_size(value):
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        return -1


def normalized(text, repository, path_prefixes, **values):
    if not isinstance(text, str) or not 1 <= utf8_size(text) <= 4096 or "\x00" in text:
        raise ToolError("INVALID_INPUT", "Query must contain 1..4096 UTF-8 bytes without NUL.")
    if repository is not None and (
        not isinstance(repository, str) or not 1 <= utf8_size(repository) <= 4096
    ):
        raise ToolError("INVALID_INPUT", "Invalid repository filter.")
    prefixes = [] if path_prefixes is None else path_prefixes
    if (
        not isinstance(prefixes, list)
        or len(prefixes) > 16
        or any(
            not ev.relative_path(p) or not 1 <= utf8_size(p) <= 4096 or "\x00" in p
            for p in prefixes
        )
    ):
        raise ToolError("INVALID_INPUT", "Invalid normalized path-prefix filter.")
    if set(values) - LIMITS.keys():
        raise ToolError("INVALID_INPUT", "Unknown retrieval allowance.")
    effective = {}
    for key, (default, low, high) in LIMITS.items():
        value = values.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ToolError("INVALID_INPUT", "Invalid retrieval allowance.")
        effective[key] = value
    return {
        "text": text,
        "repository": repository,
        "path_prefixes": sorted(set(prefixes)),
        **effective,
    }


class Work:
    """Reserve one VM quantum per statement, then each observed quantum.

    Prepaying the statement tail makes this a conservative allowance, not a
    false exact VM-instruction count. SQLite virtual-table C work/deadlines
    remain cooperative; this is not synchronous preemption or a latency bound.
    """

    def __init__(self, reader, effective):
        if reader._closed or not reader._validated_lease:
            raise RuntimeError("Retrieval needs an acquired lease")
        if reader._lease_schema_epoch != 9:
            raise ToolError(
                "INDEX_UPGRADE_REQUIRED",
                "Retrieval needs native epoch9. Run atlas-aci index --repo <repo>.",
                "different_tool",
            )
        self.conn = reader.db
        self.limit = effective["max_vm_operations"]
        self.deadline = time.monotonic_ns() + effective["deadline_ms"] * 1000000
        self.installed = False
        self.interrupted = False
        self.counts = {
            k: 0
            for k in (
                "exact_queries",
                "lexical_queries",
                "metadata_queries",
                "hydration_queries",
                "sql_queries",
                "key_rows",
                "hydrated_rows",
                "vm_operations_reserved",
                "vm_progress_callbacks",
                "interrupted_sql_queries",
            )
        }

    def __enter__(self):
        self.conn.set_progress_handler(self.progress, VM_QUANTUM)
        self.installed = True
        return self

    def __exit__(self, *_):
        if self.installed:
            try:
                self.conn.set_progress_handler(None, 0)
            finally:
                self.installed = False

    def stop(self):
        raise ToolError(
            "RETRIEVAL_BUDGET_EXCEEDED",
            "Retrieval work allowance or deadline reached.",
            "refine_request",
        )

    def check(self):
        if time.monotonic_ns() >= self.deadline:
            self.stop()

    def reserve(self):
        self.check()
        if self.counts["vm_operations_reserved"] + VM_QUANTUM > self.limit:
            self.stop()
        self.counts["vm_operations_reserved"] += VM_QUANTUM

    def progress(self):
        self.counts["vm_progress_callbacks"] += 1
        if (
            time.monotonic_ns() >= self.deadline
            or self.counts["vm_operations_reserved"] + VM_QUANTUM > self.limit
        ):
            self.interrupted = True
            return 1
        self.counts["vm_operations_reserved"] += VM_QUANTUM
        return 0

    def rows(self, sql, parameters, family, limit):
        self.reserve()
        self.counts[family + "_queries"] += 1
        self.counts["sql_queries"] += 1
        try:
            cursor = self.conn.execute(sql, parameters)
            rows = cursor.fetchmany(limit + 1)
            if len(rows) > limit:
                raise ValueError("Retrieval fixed SQL row bound")
        except sqlite3.Error as exc:
            if (
                getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT
                and self.interrupted
            ):
                self.counts["interrupted_sql_queries"] += 1
                self.stop()
            raise ToolError(
                "RETRIEVAL_FAILED", "Retrieval index operation failed.", "different_tool"
            ) from exc
        self.check()
        if family in {"exact", "lexical"}:
            self.counts["key_rows"] += len(rows)
        elif family == "hydration":
            self.counts["hydrated_rows"] += len(rows)
        return rows

    def one(self, sql, parameters, family):
        rows = self.rows(sql, parameters, family, 1)
        if not rows:
            raise ToolError("INDEX_UNAVAILABLE", "Retrieval proof is incomplete.", "different_tool")
        return rows[0]


def path_filter(prefixes):
    if not prefixes:
        return "", []
    clauses = []
    parameters = []
    for prefix in prefixes:
        clauses.append("(d.path=? OR substr(d.path,1,?)=?)")
        parameters.extend((prefix, len(prefix) + 1, prefix + "/"))
    return " AND (" + " OR ".join(clauses) + ")", parameters


def ranked(work, query):
    selected = []
    seen = set()
    overflow = False
    filter_sql, filter_args = path_filter(query["path_prefixes"])
    for tier, column in zip(
        TIERS[:4], ("candidate_id", "qualified_label", "path", "name"), strict=True
    ):
        remaining = query["max_candidates"] - len(selected)
        exclude = (
            (" AND d.candidate_id NOT IN (" + ",".join("?" for _ in seen) + ")") if seen else ""
        )
        sql = (
            "SELECT d.candidate_id FROM retrieval_documents AS d WHERE d."
            + column
            + "=?"
            + (" AND d.kind='entity'" if tier == "exact_entity_id" else "")
            + filter_sql
            + exclude
            + " ORDER BY d.candidate_id LIMIT ?"
        )
        parameters = [query["text"], *filter_args, *sorted(seen), remaining + 1]
        rows = work.rows(sql, parameters, "exact", remaining + 1)
        for row in rows:
            if len(selected) == query["max_candidates"]:
                overflow = True
                break
            seen.add(row[0])
            selected.append((row[0], tier, None))
        if overflow:
            return selected, True
    remaining = query["max_candidates"] - len(selected)
    exclude = (" AND d.candidate_id NOT IN (" + ",".join("?" for _ in seen) + ")") if seen else ""
    sql = (
        "SELECT d.candidate_id,bm25(retrieval_fts) FROM retrieval_fts "
        "JOIN retrieval_documents AS d ON d.candidate_id=retrieval_fts.candidate_id "
        "WHERE retrieval_fts MATCH ?"
        + filter_sql
        + exclude
        + " ORDER BY bm25(retrieval_fts),d.candidate_id LIMIT ?"
    )
    phrase = '"' + query["text"].replace('"', '""') + '"'
    rows = work.rows(
        sql, [phrase, *filter_args, *sorted(seen), remaining + 1], "lexical", remaining + 1
    )
    for row in rows:
        if len(selected) == query["max_candidates"]:
            overflow = True
            break
        if not isinstance(row[1], (int, float)) or not math.isfinite(row[1]):
            raise ValueError("Invalid FTS relevance")
        selected.append((row[0], "lexical", float(row[1])))
    return selected, overflow


def hydrate(work, key, tier, score, reader):
    row = work.one(
        "SELECT " + ",".join(METADATA_COLUMNS) + " FROM retrieval_documents WHERE candidate_id=?",
        (key,),
        "hydration",
    )
    document = dict(zip(METADATA_COLUMNS, row, strict=True))
    provenance = json.loads(document["provenance"])
    occurrence = evidence = None
    if document["kind"] == "entity":
        entity = provenance["entity"]
        source = provenance["source"]
        if provenance["occurrence_id"] is not None:
            occurrence = json.loads(
                work.one(
                    "SELECT payload FROM occurrences WHERE id=?",
                    (provenance["occurrence_id"],),
                    "hydration",
                )[0]
            )
            ev.validate_record(occurrence)
        if provenance["evidence_id"] is not None:
            evidence = json.loads(
                work.one(
                    "SELECT payload FROM evidence WHERE id=?",
                    (provenance["evidence_id"],),
                    "hydration",
                )[0]
            )
            ev.validate_record(evidence)
    else:
        entity = None
        source = {
            "state": "available",
            **{
                k: provenance[k]
                for k in ("path", "source_sha256", "start_byte", "end_byte", "granularity")
            },
        }
    return {
        "candidate_id": key,
        "kind": document["kind"],
        "match_tier": tier,
        "lexical_score": score,
        "path": document["path"],
        "language": document["language"],
        "qualified_label": document["qualified_label"],
        "entity": entity,
        "occurrence": occurrence,
        "evidence": evidence,
        "source": source,
        "repository_id": docs.digest(str(reader.repo)),
        "generation_id": reader.generation_id,
        "snapshot_id": reader.snapshot_id,
    }


async def retrieve(reader, *, text, repository=None, path_prefixes=None, admit=None, **limits):
    query = normalized(text, repository, path_prefixes, **limits)
    with Work(reader, query) as work:
        descriptor = json.loads(
            work.one(
                "SELECT json_extract(value,'$.retrieval') FROM manifest "
                "WHERE key='generation_inputs'",
                (),
                "metadata",
            )[0]
        )
        docs.shape(descriptor)
        coverage = descriptor["coverage"]
        data = {
            "projection": "retrieval_candidates",
            "query_digest": docs.digest(query),
            "query_syntax": "literal_phrase",
            "repository": str(reader.repo),
            "repository_filter_matched": repository is None or repository == str(reader.repo),
            "path_prefix_count": len(query["path_prefixes"]),
            "effective_limits": {k: query[k] for k in LIMITS},
            "vm_operation_quantum": VM_QUANTUM,
            "work": work.counts,
            "coverage": {
                **{k: v for k, v in coverage.items() if k != "excluded"},
                "excluded_counts": {r["reason"]: r["count"] for r in coverage["excluded"]},
            },
            "candidates": [],
            "returned_count": 0,
            "truncated": False,
            "truncation_reason": None,
            "diagnostic_reason": "no_match",
        }
        if admit is not None and not admit(data):
            raise ToolError("INVALID_BUDGET", "Retrieval metadata cannot fit the result budget.")
        if data["repository_filter_matched"]:
            keys, overflow = ranked(work, query)
        else:
            keys, overflow = [], False
            data["diagnostic_reason"] = "filtered_repository"
        # Cancellation here owns only this request's handler/reader, not saved S13 references.
        await asyncio.sleep(0)
        work.check()
        if keys:
            data["diagnostic_reason"] = "matched"
        elif data["repository_filter_matched"] and not any(
            unicodedata.category(c)[0] in {"L", "N"} or unicodedata.category(c) == "Co"
            for c in text
        ):
            data["diagnostic_reason"] = "tokenless"
        for key, tier, score in keys:
            work.check()
            candidate = hydrate(work, key, tier, score, reader)
            data["candidates"].append(candidate)
            data["returned_count"] = len(data["candidates"])
            if admit is not None and not admit(data):
                data["candidates"].pop()
                data["returned_count"] = len(data["candidates"])
                data["truncated"] = True
                data["truncation_reason"] = getattr(admit, "reason", "byte_limit")
                break
            await asyncio.sleep(0)
            work.check()
        else:
            data["truncated"] = overflow
            data["truncation_reason"] = "candidate_limit" if overflow else None
        if admit is not None and hasattr(admit, "finish"):
            admit.finish(data)
        work.check()
        return data
