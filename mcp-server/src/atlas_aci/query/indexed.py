"""Single-row indexed reads and query-owned SQLite deadline accounting.

The caller first acquires the authenticated graph/source lease. Its global
validation is deliberately outside these traversal counters and deadline.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from atlas_aci.enforcement import ToolError
from atlas_aci.storage import evidence as ev

VM_OP_QUANTUM = 100
OUTGOING_SQL = (
    "SELECT id,payload FROM assertions INDEXED BY idx_assertions_source_relation_id "
    "WHERE json_extract(payload,'$.source_entity_id')=? "
    "AND json_extract(payload,'$.relation')=? AND id>? ORDER BY id LIMIT 1"
)
INCOMING_SQL = (
    "SELECT id,payload FROM assertions INDEXED BY idx_assertions_target_relation_id "
    "WHERE json_extract(payload,'$.target_entity_id')=? "
    "AND json_extract(payload,'$.relation')=? AND id>? ORDER BY id LIMIT 1"
)
NAME_SQL = (
    "SELECT id,payload FROM entities INDEXED BY idx_entities_name_id "
    "WHERE json_extract(payload,'$.name')=? AND id>? ORDER BY id LIMIT 1"
)


class QueryStopped(Exception):
    """A bounded work frontier, distinct from operational SQLite failures."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class IndexedReader:
    """Own the progress hook on one dedicated validated lease connection.

    No unknown previous hook can be restored: sqlite3 has no public getter.
    The small VM quantum is cooperative interruption, not a hard latency bound.
    """

    def __init__(self, reader: Any, *, max_examined_edges: int, deadline_ms: int, accounting=None):
        if reader._closed or not reader._validated_lease:
            raise RuntimeError("Navigation requires an acquired graph lease")
        if reader._lease_schema_epoch not in {8, 9}:
            raise ToolError(
                "INDEX_UPGRADE_REQUIRED",
                "Navigation needs native epoch8 or9 indexes. Run atlas-aci index --repo <repo>, "
                "or export the old graph and import it with this version.",
                "different_tool",
            )
        if type(max_examined_edges) is not int or not 0 <= max_examined_edges <= 10000:
            raise ValueError("Invalid examined-edge allowance")
        if type(deadline_ms) is not int or not 0 <= deadline_ms <= 2000:
            raise ValueError("Invalid traversal deadline")
        self.accounting = accounting
        self.shared_stop_reason = None
        self.reader = reader
        self.conn = reader.db
        self.max_examined_edges = max_examined_edges
        self.deadline_ns = time.monotonic_ns() + deadline_ms * 1_000_000
        self.deadline_triggered = False
        self._installed = False
        self.counts = {
            "examined_edges": 0,
            "edge_queries": 0,
            "edge_rows": 0,
            "confirmed_edge_eof": 0,
            "interrupted_edge_steps": 0,
            "endpoint_queries": 0,
            "endpoint_rows": 0,
            "hydration_queries": 0,
            "hydration_rows": 0,
            "sql_queries": 0,
            "interrupted_sql_queries": 0,
        }

    def __enter__(self):
        self.conn.set_progress_handler(
            self._progress, self.accounting.vm_quantum if self.accounting else VM_OP_QUANTUM
        )
        self._installed = True
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        if self._installed:
            try:
                self.conn.set_progress_handler(None, 0)
            finally:
                self._installed = False

    def _progress(self):
        if self.accounting is not None:
            try:
                self.accounting.progress(self.accounting.vm_quantum)
            except QueryStopped as exc:
                self.shared_stop_reason = exc.reason
                return 1
        if time.monotonic_ns() >= self.deadline_ns:
            self.deadline_triggered = True
            return 1
        return 0

    def check_deadline(self):
        if self.accounting is not None:
            self.accounting.check()
        if time.monotonic_ns() >= self.deadline_ns:
            raise QueryStopped("deadline")

    def _one(self, sql, parameters, family):
        """Private fixed-SQL callers only; execute/fetch at most one actual row."""
        self.check_deadline()
        if self.accounting is not None:
            self.accounting.before_sql(family)
        self.counts[family + "_queries"] += 1
        self.counts["sql_queries"] += 1
        try:
            row = self.conn.execute(sql, parameters).fetchone()
        except sqlite3.Error as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT and (
                self.deadline_triggered or self.shared_stop_reason is not None
            ):
                self.counts["interrupted_sql_queries"] += 1
                raise QueryStopped(self.shared_stop_reason or "deadline") from exc
            raise
        if row is not None:
            self.counts[family + "_rows"] += 1
        return row

    @staticmethod
    def _record(row, kind):
        if row is None:
            return None
        record = json.loads(row[1])
        ev.validate_record(record)
        ev.require(record["type"] == kind and record["id"] == row[0], "indexed record identity")
        return record

    def record(self, kind, identifier, *, endpoint=False):
        if kind not in ev.TABLES or not ev.hash_value(identifier):
            raise ValueError("Invalid canonical lookup")
        row = self._one(
            "SELECT id,payload FROM " + ev.TABLES[kind] + " WHERE id=?",
            (identifier,),
            "endpoint" if endpoint else "hydration",
        )
        return self._record(row, kind)

    def name_candidates(self, name):
        """At most two records establish absent/unique/ambiguous, no third lookahead."""
        candidates = []
        after = ""
        for _ in range(2):
            row = self._one(NAME_SQL, (name, after), "endpoint")
            if row is None:
                break
            record = self._record(row, "entity")
            candidates.append(record)
            after = record["id"]
        return candidates

    def edge(self, endpoint, relation, direction, after):
        self.check_deadline()
        if self.counts["examined_edges"] >= self.max_examined_edges:
            raise QueryStopped("examined_edge_limit")
        if direction not in {"outgoing", "incoming"} or relation not in ev.RELATIONS:
            raise ValueError("Invalid adjacency selector")
        if not ev.hash_value(endpoint) or (after != "" and not ev.hash_value(after)):
            raise ValueError("Invalid adjacency key")
        if self.accounting is not None:
            self.accounting.reserve_edge()
        self.counts["examined_edges"] += 1  # Reserve BEFORE the actual indexed step.
        before = self.counts["sql_queries"]
        try:
            row = self._one(
                OUTGOING_SQL if direction == "outgoing" else INCOMING_SQL,
                (endpoint, relation, after),
                "edge",
            )
        except QueryStopped:
            if self.counts["sql_queries"] == before:
                self.counts["examined_edges"] -= 1  # No in-flight SQL was started.
                if self.accounting is not None:
                    self.accounting.refund_edge()
            else:
                self.counts["interrupted_edge_steps"] += 1
            raise
        if row is None:
            self.counts["examined_edges"] -= 1  # Only confirmed EOF refunds.
            if self.accounting is not None:
                self.accounting.refund_edge()
            self.counts["confirmed_edge_eof"] += 1
        return self._record(row, "assertion")
