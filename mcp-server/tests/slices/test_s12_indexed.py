"""Observe real indexed fetches, conservative reservations and owned interruption."""

import sqlite3
import time

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.query import indexed
from atlas_aci.query.indexed import IndexedReader, QueryStopped


def built(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n B(); B()\ndef B():\n A()\n")
    graph = CodeGraph(repo)
    graph.build()
    return graph


def test_exact_fetched_rows_no_hidden_lookahead(tmp_path):
    graph = built(tmp_path)
    try:
        with graph.reader_lease() as reader:
            statements = []
            reader.db.set_trace_callback(statements.append)
            with IndexedReader(reader, max_examined_edges=2, deadline_ms=2000) as query:
                seeds = query.name_candidates("A")
                assert len(seeds) == 1
                after = ""
                records = []
                for _ in range(2):
                    row = query.edge(seeds[0]["id"], "call", "outgoing", after)
                    records.append(row)
                    after = row["id"]
                with pytest.raises(QueryStopped, match="examined_edge_limit"):
                    query.edge(seeds[0]["id"], "call", "outgoing", after)
                assert len({r["id"] for r in records}) == 2
                assert query.counts["examined_edges"] == query.counts["edge_rows"] == 2
                assert query.counts["edge_queries"] == 2
                assert query.counts["endpoint_queries"] == 2
                assert query.counts["endpoint_rows"] == 1
                assert query.counts["sql_queries"] == 4
            selects = [sql for sql in statements if sql.startswith("SELECT")]
            assert len(selects) == 4
            assert all("INDEXED BY" in sql and "LIMIT 1" in sql for sql in selects)
            assert not any("COUNT(" in sql or "SELECT *" in sql for sql in statements)
    finally:
        graph.close()


def test_only_confirmed_eof_refunds_edge_reservation(tmp_path):
    graph = built(tmp_path)
    try:
        with (
            graph.reader_lease() as reader,
            IndexedReader(reader, max_examined_edges=3, deadline_ms=2000) as query,
        ):
            seed = query.name_candidates("A")[0]
            first = query.edge(seed["id"], "call", "outgoing", "")
            second = query.edge(seed["id"], "call", "outgoing", first["id"])
            assert query.edge(seed["id"], "call", "outgoing", second["id"]) is None
            assert query.counts["examined_edges"] == query.counts["edge_rows"] == 2
            assert query.counts["edge_queries"] == 3 and query.counts["confirmed_edge_eof"] == 1
    finally:
        graph.close()


def test_actual_progress_deadline_and_hook_cleanup(tmp_path):
    graph = built(tmp_path)
    try:
        with graph.reader_lease() as reader:
            query = IndexedReader(reader, max_examined_edges=5, deadline_ms=100)
            with pytest.raises(QueryStopped, match="deadline"), query:
                query._one(
                    "WITH RECURSIVE x(n) AS (VALUES(0) UNION ALL "
                    "SELECT n+1 FROM x WHERE n<10000000) SELECT sum(n) FROM x",
                    (),
                    "hydration",
                )
            assert query.deadline_triggered and query.counts["interrupted_sql_queries"] == 1
            assert query.counts["sql_queries"] == 1 and query.counts["hydration_rows"] == 0
            # A stale expired handler would interrupt this actual multi-quantum query.
            assert (
                reader.db.execute(
                    "WITH RECURSIVE x(n) AS (VALUES(0) UNION ALL "
                    "SELECT n+1 FROM x WHERE n<1000) SELECT sum(n) FROM x"
                ).fetchone()[0]
                == 500500
            )
            assert not query._installed
    finally:
        graph.close()


def test_interrupted_indexed_step_stays_charged(tmp_path, monkeypatch):
    graph = built(tmp_path)
    try:
        with graph.reader_lease() as reader:
            seed = reader.db.execute(indexed.NAME_SQL, ("A", "")).fetchone()[0]
            query = IndexedReader(reader, max_examined_edges=1, deadline_ms=20)
            original = query._progress

            def expire_at_real_vm_barrier():
                # Hold a real SQLite VM callback until its actual monotonic deadline.
                while time.monotonic_ns() < query.deadline_ns:
                    pass
                return original()

            monkeypatch.setattr(indexed, "VM_OP_QUANTUM", 1)
            monkeypatch.setattr(query, "_progress", expire_at_real_vm_barrier)
            with pytest.raises(QueryStopped, match="deadline"), query:
                query.edge(seed, "call", "outgoing", "")
            assert query.counts["examined_edges"] == 1
            assert query.counts["edge_rows"] == 0 and query.counts["interrupted_edge_steps"] == 1
            assert reader.db.execute("SELECT 1").fetchone()[0] == 1
    finally:
        graph.close()


def test_unrelated_sqlite_interrupt_is_operational(tmp_path, monkeypatch):
    graph = built(tmp_path)
    try:
        with graph.reader_lease() as reader:
            query = IndexedReader(reader, max_examined_edges=1, deadline_ms=2000)
            monkeypatch.setattr(query, "_progress", lambda: 1)
            with pytest.raises(sqlite3.OperationalError) as error, query:
                query._one(
                    "WITH RECURSIVE x(n) AS (VALUES(0) UNION ALL "
                    "SELECT n+1 FROM x WHERE n<10000) SELECT sum(n) FROM x",
                    (),
                    "hydration",
                )
            assert error.value.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
            assert not query.deadline_triggered
            assert query.counts["interrupted_sql_queries"] == 0
            assert reader.db.execute("SELECT 1").fetchone()[0] == 1
    finally:
        graph.close()


def test_zero_allowance_and_zero_deadline_do_not_fetch(tmp_path):
    graph = built(tmp_path)
    try:
        with graph.reader_lease() as reader:
            seed = reader.db.execute(indexed.NAME_SQL, ("A", "")).fetchone()[0]
            with IndexedReader(reader, max_examined_edges=0, deadline_ms=2000) as query:
                with pytest.raises(QueryStopped, match="examined_edge_limit"):
                    query.edge(seed, "call", "outgoing", "")
                assert query.counts["sql_queries"] == query.counts["examined_edges"] == 0
            with IndexedReader(reader, max_examined_edges=1, deadline_ms=0) as query:
                with pytest.raises(QueryStopped, match="deadline"):
                    query.name_candidates("A")
                assert query.counts["sql_queries"] == 0
    finally:
        graph.close()
