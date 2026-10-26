"""Production leases, actual navigation SQL and genuine older native readers."""

import asyncio

from test_s12_contract import invoke
from test_s12_contract import state as state
from test_s12_storage import native7

from atlas_aci.config import Config
from atlas_aci.query import traversal
from atlas_aci.query.indexed import IndexedReader
from atlas_aci.server import build_server_state, call_tool_result


def test_every_navigation_statement_counted_and_single_row(state, monkeypatch):
    statements = []
    enter, exit_ = IndexedReader.__enter__, IndexedReader.__exit__

    def entered(query):
        query.conn.set_trace_callback(statements.append)
        return enter(query)

    def exited(query, *args):
        try:
            return exit_(query, *args)
        finally:
            query.conn.set_trace_callback(None)

    monkeypatch.setattr(IndexedReader, "__enter__", entered)
    monkeypatch.setattr(IndexedReader, "__exit__", exited)
    payload, _ = invoke(state, max_depth=8)
    work = payload["data"]["work"]
    assert work["sql_queries"] == len(statements)
    assert work["sql_queries"] == sum(
        work[k] for k in ("edge_queries", "endpoint_queries", "hydration_queries")
    )
    assert work["edge_rows"] == work["examined_edges"] == 4
    assert len(payload["data"]["edges"]) == 4
    assert all(" WHERE " in sql for sql in statements)
    assert not any("COUNT(" in sql or "SELECT *" in sql or "history" in sql for sql in statements)
    assert all(
        "LIMIT 1" in sql if "INDEXED BY" in sql else " WHERE id=" in sql for sql in statements
    )


def test_rejected_unknown_and_confidence_rows_still_charged(state):
    writer = state[-1]
    (writer.repo / "a.py").write_text("def A():\n B(); missing()\ndef B():\n pass\n")
    writer.build()
    payload, _ = invoke(state, max_depth=4)
    data = payload["data"]
    assert len(data["edges"]) == 1
    assert data["work"]["examined_edges"] == data["work"]["edge_rows"] == 2
    assert data["unknown_relations_traversed"] is False
    resolution = data["edges"][0]["assertion"]["resolution"]
    filtered, _ = invoke(
        state, confidence=["EXTRACTED" if resolution == "INFERRED" else "INFERRED"]
    )
    assert filtered["data"]["edges"] == []
    assert filtered["data"]["work"]["examined_edges"] == 2


def test_pinned_generation_survives_actual_replacement(state, monkeypatch):
    writer = state[-1]
    with writer.reader_lease() as reader:
        original_generation = reader.generation_id

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        original_sleep = asyncio.sleep

        async def gate(delay):
            entered.set()
            await release.wait()
            await original_sleep(delay)

        monkeypatch.setattr(traversal.asyncio, "sleep", gate)
        config, enforcement, memex, factory, _ = state
        task = asyncio.create_task(
            call_tool_result(
                "graph_query",
                {
                    "schema_version": "atlas.graph.v1",
                    "op": "path",
                    "seed": {"name": "A"},
                    "target": {"name": "B"},
                    "deadline_ms": 2000,
                    "expected_generation_id": original_generation,
                },
                config,
                enforcement,
                memex,
                factory,
            )
        )
        await entered.wait()
        (writer.repo / "a.py").write_text("def A():\n C()\ndef C():\n pass\n")
        writer.build()
        with writer.reader_lease() as new:
            assert new.generation_id != original_generation
        release.set()
        result = await task
        assert not result.isError
        assert result.structuredContent["generation_id"] == original_generation
        assert [n["name"] for n in result.structuredContent["data"]["path"]["nodes"]] == ["A", "B"]
        monkeypatch.setattr(traversal.asyncio, "sleep", original_sleep)
        current = await call_tool_result(
            "graph_query",
            {
                "schema_version": "atlas.graph.v1",
                "op": "path",
                "seed": {"name": "A"},
                "target": {"name": "C"},
                "deadline_ms": 2000,
            },
            config,
            enforcement,
            memex,
            factory,
        )
        assert (
            not current.isError
            and current.structuredContent["generation_id"] != original_generation
        )
        stale = await call_tool_result(
            "graph_query",
            {
                "schema_version": "atlas.graph.v1",
                "op": "neighborhood",
                "seed": {"name": "A"},
                "expected_generation_id": original_generation,
            },
            config,
            enforcement,
            memex,
            factory,
        )
        assert stale.isError and stale.structuredContent["error"]["code"] == "SNAPSHOT_MISMATCH"

    asyncio.run(exercise())


def test_genuine_native7_navigation_upgrade_not_status_or_legacy(tmp_path):
    repo, identity = native7(tmp_path)
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = build_server_state(config)
    before = (repo / ".atlas/graph.7.db").read_bytes()

    async def exercise():
        results = []
        for args in [
            {"query": "callers_of:B"},
            {"schema_version": "atlas.graph.v1", "op": "status"},
            {"schema_version": "atlas.graph.v1", "op": "neighborhood", "seed": {"name": "A"}},
        ]:
            results.append(
                await call_tool_result("graph_query", args, config, enforcement, memex, factory)
            )
        return results

    try:
        legacy, status, nav = asyncio.run(exercise())
        assert not legacy.isError and not status.isError
        assert status.structuredContent["generation_id"] == identity["generation_id"]
        assert nav.isError and nav.structuredContent["error"]["code"] == "INDEX_UPGRADE_REQUIRED"
        assert (repo / ".atlas/graph.7.db").read_bytes() == before
        assert not (repo / ".atlas/graph.8.db").exists()
    finally:
        factory.close()


def test_import_source_unknown_and_history_not_fanned_out(state, tmp_path):
    writer = state[-1]
    (writer.repo / "history.py").write_text("def H():\n B()\n")
    writer.build()
    (writer.repo / "history.py").write_text("def H(:\n pass\n")
    writer.build()
    payload, _ = invoke(state, seed={"name": "B"}, direction="incoming", max_depth=1)
    assert payload["evidence_status"] == "partial"
    assert all(e["occurrence"]["path"] != "history.py" for e in payload["data"]["edges"])
    artifact = tmp_path / "graph.jsonl"
    writer.export_jsonl(artifact)
    from atlas_aci.codegraph import CodeGraph

    destination = tmp_path / "destination"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    try:
        with imported.reader_lease() as reader:
            assert reader.snapshot_id is None
            result = asyncio.run(
                traversal.navigate(reader, seed={"name": "A"}, max_depth=1, deadline_ms=2000)
            )
            assert result["edges"]
            assert all(h["source_availability"] == "unavailable" for h in result["edges"])
            for hop in result["edges"]:
                assert hop["occurrence"]["source_sha256"] is not None
    finally:
        imported.close()
