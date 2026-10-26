"""Production request ownership, cancellation and genuine older native readers."""

import asyncio
import os
import sqlite3
from contextlib import contextmanager

import pytest
from test_s17_contract import call, close, setup
from test_s17_storage import old8

from atlas_aci.config import Config
from atlas_aci.query import retrieval
from atlas_aci.server import build_server_state, call_tool_result


@pytest.mark.parametrize("outcome", ["normal", "budget", "cancel"])
def test_dispatch_closes_owned_sqlite_pack_and_progress_hook(tmp_path, monkeypatch, outcome):
    state = setup(tmp_path)
    factory = state[-1]
    original_lease = factory.reader_lease
    acquired, cleared = [], []

    @contextmanager
    def tracked():
        with original_lease() as reader:
            acquired.append((reader, reader.db, reader._source_lease, reader._source_lease.fd))
            yield reader

    monkeypatch.setattr(factory, "reader_lease", tracked)
    original_exit = retrieval.Work.__exit__

    def exited(work, *args):
        try:
            return original_exit(work, *args)
        finally:
            assert not work.installed
            assert tuple(work.conn.execute("SELECT 1").fetchone()) == (1,)
            cleared.append(work)

    monkeypatch.setattr(retrieval.Work, "__exit__", exited)

    async def exercise():
        if outcome == "cancel":
            entered = asyncio.Event()
            release = asyncio.Event()

            async def barrier(_delay):
                entered.set()
                await release.wait()

            monkeypatch.setattr(retrieval.asyncio, "sleep", barrier)
            task = asyncio.create_task(call(state, "Alpha"))
            await asyncio.wait_for(entered.wait(), 5)
            assert len(acquired) == 1 and tuple(acquired[0][1].execute("SELECT 1").fetchone()) == (
                1,
            )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await call(
                state, "Alpha", **({"max_vm_operations": 0} if outcome == "budget" else {})
            )
            assert result.isError == (outcome == "budget")
            if result.isError:
                assert result.structuredContent["error"]["code"] == "RETRIEVAL_BUDGET_EXCEEDED"
        assert len(acquired) == len(cleared) == 1
        reader, conn, source, fd = acquired[0]
        assert reader._closed and source.fd is None
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        with pytest.raises(OSError):
            os.fstat(fd)

    try:
        asyncio.run(exercise())
    finally:
        close(state)


def test_genuine_old8_retrieve_upgrade_preserves_status_legacy_and_native_bytes(tmp_path):
    graph, fixture = old8(tmp_path)
    before = {
        p.relative_to(graph.atlas_dir): p.read_bytes()
        for p in graph.atlas_dir.rglob("*")
        if p.is_file()
    }
    config = Config(
        repo=graph.repo,
        graph_root=tmp_path / "graphs",
        memex_root=tmp_path / "memex",
        max_calls_per_minute=0,
    )
    enforcement, memex, factory = build_server_state(config)

    async def exercise():
        results = []
        for args in [
            {"schema_version": "atlas.graph.v1", "op": "retrieve", "text": "A"},
            {"schema_version": "atlas.graph.v1", "op": "status"},
            {"query": "callers_of:B"},
        ]:
            results.append(
                await call_tool_result("graph_query", args, config, enforcement, memex, factory)
            )
        return results

    try:
        retrieve, status, legacy = asyncio.run(exercise())
        assert (
            retrieve.isError
            and retrieve.structuredContent["error"]["code"] == "INDEX_UPGRADE_REQUIRED"
        )
        assert not status.isError and not legacy.isError
        assert status.structuredContent["generation_id"] == fixture["generation_id"]
        assert not graph.db_path.exists()
        assert before == {
            p.relative_to(graph.atlas_dir): p.read_bytes()
            for p in graph.atlas_dir.rglob("*")
            if p.is_file()
        }
    finally:
        graph.close()
        factory.close()
