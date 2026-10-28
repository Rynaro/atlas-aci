"""Actual one-G1 retrieval, multiple seeds and cumulative SQL work."""

import asyncio
import json

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.query.context_work import ContextWork
from atlas_aci.query.indexed import IndexedReader, QueryStopped
from atlas_aci.query.retrieval import retrieve
from atlas_aci.query.traversal import navigate


@pytest.fixture
def graph(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in {
        "a.py": "def A():\n B()\ndef B():\n pass\n",
        "c.py": "def C():\n D()\ndef D():\n pass\n",
    }.items():
        path = repo / "app" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
    graph = CodeGraph(repo)
    graph.build()
    try:
        yield graph
    finally:
        graph.close()


def test_one_reader_multiseed_sql_and_extra_pk_never_renews(graph):
    async def exercise(reader):
        work = ContextWork(max_examined_edges=1)
        trace = []
        reader.db.set_trace_callback(trace.append)
        data = await retrieve(reader, text="app", accounting=work)
        seeds = {
            c["entity"]["name"]: c["entity"]["id"]
            for c in data["candidates"]
            if c["kind"] == "entity"
        }
        assert {"A", "C"} <= seeds.keys()
        first = await navigate(
            reader, seed={"entity_id": seeds["A"]}, max_depth=1, deadline_ms=2000, accounting=work
        )
        after_first = dict(work.counts)
        second = await navigate(
            reader, seed={"entity_id": seeds["C"]}, max_depth=1, deadline_ms=2000, accounting=work
        )
        assert len(first["edges"]) == 1 and first["edges"][0]["producer"]
        assert second["edges"] == [] and second["stop_reason"] == "context_examined_edges_limit"
        assert work.counts["examined_edges"] == 1
        assert work.counts["sql_queries"] > after_first["sql_queries"] > data["work"]["sql_queries"]
        with IndexedReader(
            reader, max_examined_edges=1, deadline_ms=2000, accounting=work
        ) as query:
            assert query.record("entity", seeds["A"])["id"] == seeds["A"]
        reader.db.set_trace_callback(None)
        driver = [sql for sql in trace if not sql.lstrip().startswith("--")]
        assert work.counts["sql_queries"] == len(driver)
        assert (
            work.counts["vm_operations_reserved"]
            == work.counts["sql_queries"] + work.counts["vm_progress_callbacks"]
        )
        assert work.counts["proof_queries"] >= 7
        before = dict(work.counts)
        assert reader.db.execute("SELECT 1").fetchone()[0] == 1
        assert work.counts == before
        print(
            json.dumps(
                {
                    "generation_id": reader.generation_id,
                    "connection_identity": id(reader.db),
                    "aggregate_work": work.counts,
                    "after_first_seed": after_first,
                    "second_stop": second["stop_reason"],
                    "driver_sql": driver,
                }
            )
        )

    with graph.reader_lease() as reader:
        asyncio.run(exercise(reader))


def test_real_vm_interrupt_is_charged_and_owned_hook_clears(graph):
    with graph.reader_lease() as reader:
        work = ContextWork(max_vm_operations=10)
        with (
            pytest.raises(QueryStopped, match="context_vm_operations_reserved_limit"),
            IndexedReader(reader, max_examined_edges=1, deadline_ms=2000, accounting=work) as query,
        ):
            query._one(
                "WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL "
                "SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n",
                (),
                "endpoint",
            )
        assert work.counts["sql_queries"] == 1 and work.counts["vm_operations_reserved"] == 10
        before = dict(work.counts)
        assert reader.db.execute("SELECT 1").fetchone()[0] == 1
        assert work.counts == before


def test_pk_allowance_shared_between_producers(graph):
    with graph.reader_lease() as reader:
        work = ContextWork(max_proof_queries=1)
        first = asyncio.run(
            navigate(reader, seed={"name": "A"}, max_depth=1, deadline_ms=2000, accounting=work)
        )
        assert (
            first["outcome"] == "partial" and first["stop_reason"] == "context_proof_queries_limit"
        )
        assert first["edges"] == [] and work.counts["proof_queries"] == 1
        before = work.counts["sql_queries"]
        with (
            pytest.raises(QueryStopped, match="context_proof_queries_limit"),
            IndexedReader(reader, max_examined_edges=1, deadline_ms=2000, accounting=work) as query,
        ):
            query.name_candidates("C")
        assert work.counts["sql_queries"] == before


def test_absolute_deadline_not_restarted_for_later_seed(graph):
    async def exercise(reader):
        work = ContextWork(deadline_ms=15)
        deadline = work.deadline_ns
        await asyncio.sleep(0.025)
        result = await navigate(
            reader, seed={"name": "A"}, max_depth=1, deadline_ms=2000, accounting=work
        )
        assert result["stop_reason"] == "context_deadline" and work.counts["sql_queries"] == 0
        assert work.deadline_ns == deadline
        assert reader.db.execute("SELECT 1").fetchone()[0] == 1

    with graph.reader_lease() as reader:
        asyncio.run(exercise(reader))


def test_source_and_token_work_cumulative(graph):
    with graph.reader_lease() as reader:
        raw = (graph.repo / "app/a.py").read_bytes()
        work = ContextWork(max_source_reads=1, max_source_bytes=len(raw), max_tokenizer_calls=2)
        work.source_read(len(raw))
        assert reader.read_source("app/a.py") == raw
        with pytest.raises(QueryStopped, match="context_source_reads_limit"):
            work.source_read(len(raw))
        for text in ("café", "☃"):
            work.tokenizer_call()
            assert text.encode("utf-8")
        with pytest.raises(QueryStopped, match="context_tokenizer_calls_limit"):
            work.tokenizer_call()
        assert work.counts["source_reads"] == 1 and work.counts["source_bytes"] == len(raw)
        assert work.counts["tokenizer_calls"] == 2


def test_optional_hook_cancellation_clears_only_owned_progress(graph, monkeypatch):
    from atlas_aci.query import traversal

    async def exercise(reader):
        work = ContextWork()
        entered, release = asyncio.Event(), asyncio.Event()

        async def barrier(_delay):
            entered.set()
            await release.wait()

        monkeypatch.setattr(traversal.asyncio, "sleep", barrier)
        task = asyncio.create_task(navigate(reader, seed={"name": "A"}, accounting=work))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        before = dict(work.counts)
        assert reader.db.execute("SELECT 1").fetchone()[0] == 1
        assert work.counts == before

    with graph.reader_lease() as reader:
        source = reader._source_lease
        asyncio.run(exercise(reader))
    assert source.fd is None
