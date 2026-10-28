"""Actual owned context pages and typed resource membership across publications."""

import asyncio
import json
import os
import sqlite3
import time

import pytest
from mcp import McpError
from test_s18_atoms import BytesTokenizer
from test_s18_contract import close, decoded, invoke, setup

from atlas_aci.protocol.resources import read_resource
from atlas_aci.protocol.results import result_bytes
from atlas_aci.server import call_tool_result


async def continuation(state, first, *, version="2025-11-25"):
    _, config, enforcement, memex, factory = state
    data = decoded(first)["data"]
    return await call_tool_result(
        "graph_query",
        {
            "schema_version": "atlas.graph.v1",
            "op": "continue",
            "cursor": data["next_cursor"],
            "query_digest": data["query_digest"],
        },
        config,
        enforcement,
        memex,
        factory,
        protocol_version=version,
    )


def test_owned_context_cached_pages_typed_resources_and_g1_retention(tmp_path):
    state = setup(tmp_path, BytesTokenizer())

    async def run():
        first = await invoke(state, continuation=True, max_bytes=40000)
        assert not first.isError, first
        data = decoded(first)["data"]
        assert data["bundles"] and data["resources"] and data["has_more"]
        registry = state[-1]._lease_registry
        g1 = data["generation_id"]
        source = data["resources"][0]
        assert source["kind"] == "definition" and source["membership"]["entity_id"]
        before = await read_resource(registry, source["uri"], state[2])
        path = state[0].repo / "app/payments.py"
        path.write_text(path.read_text().replace("process_payment", "process_payment_g2"))
        state[0].build()
        path.write_text(path.read_text().replace("process_payment_g2", "process_payment_g3"))
        state[0].build()
        latest = await invoke(state, text="process_payment_g3")
        assert not latest.isError and decoded(latest)["generation_id"] != g1
        after = await read_resource(registry, source["uri"], state[2])
        assert before[0].content == after[0].content
        assert b"process_payment" in before[0].content
        second = await continuation(state, first)
        replay = await continuation(state, first)
        assert not second.isError and result_bytes(second) == result_bytes(replay)
        assert decoded(second)["generation_id"] == g1
        assert decoded(second)["data"]["offset"] == len(data["bundles"])
        for result in (first, second):
            assert len(result_bytes(result)) <= 40000
            assert decoded(result)["data"]["token_usage"]["count"] == len(result_bytes(result))
        lease = next(iter(registry._leases.values()))
        connection, source_fd = lease.reader.db, lease.reader._source_lease.fd
        await registry.aclose()
        assert registry.usage()["active"] == 0
        with pytest.raises(OSError):
            os.fstat(source_fd)
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    try:
        asyncio.run(run())
    finally:
        close(state)


def test_context_file_membership_scope_and_negotiated_representation(tmp_path):
    state = setup(tmp_path, BytesTokenizer())

    async def run():
        first = await invoke(
            state, text="retry transport timeout", continuation=True, max_bytes=40000
        )
        assert not first.isError, first
        data = decoded(first)["data"]
        registry = state[-1]._lease_registry
        file = next(r for r in data["resources"] if r["kind"] == "file")
        assert file["start_byte"] == 0 and file["end_byte"] == file["source_size"]
        result = await read_resource(registry, file["uri"], state[2])
        assert result[0].content == (state[0].repo / file["path"]).read_bytes()
        if data["has_more"]:
            wrong = await continuation(state, first, version="2024-11-05")
            assert wrong.isError and decoded(wrong)["error"]["code"] == "SCOPE_MISMATCH"
        # Even an algorithm with the same declaration is a different operator object.
        state[1].context_tokenizer = BytesTokenizer()
        with pytest.raises(McpError) as error:
            await read_resource(registry, file["uri"], state[2])
        assert error.value.error.data["reason"] == "SCOPE_MISMATCH"
        await registry.aclose()

    try:
        asyncio.run(run())
    finally:
        close(state)


def test_real_cancel_after_collection_closes_pending_g1_once(tmp_path, monkeypatch):
    from atlas_aci.query import context_pages

    state = setup(tmp_path)
    original = context_pages.collect
    entered = asyncio.Event()
    captured = []

    async def pause(reader, *args, **kwargs):
        value = await original(reader, *args, **kwargs)
        captured.append((reader.db, reader._source_lease.fd))
        entered.set()
        await asyncio.Event().wait()
        return value

    monkeypatch.setattr(context_pages, "collect", pause)

    async def run():
        task = asyncio.create_task(invoke(state, continuation=True, max_bytes=40000))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        registry = state[-1]._lease_registry
        assert registry.usage()["active"] == registry.usage()["pending"] == 0
        for connection, fd in captured:
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            with pytest.raises(OSError):
                os.fstat(fd)
        await registry.aclose()

    try:
        asyncio.run(run())
    finally:
        close(state)


def test_capacity_and_whole_path_failure_never_evict_or_issue_partial(tmp_path):
    state = setup(tmp_path)
    state[1].max_graph_leases = 1

    async def run():
        first = await invoke(state, continuation=True, max_bytes=40000)
        assert not first.isError
        registry = state[-1]._lease_registry
        lease = next(iter(registry._leases.values()))
        assert lease.pages and lease.reader._source_lease.fd >= 0
        full = await invoke(state, continuation=True, max_bytes=40000)
        assert full.isError and decoded(full)["error"]["code"] == "LEASE_CAPACITY"
        assert next(iter(registry._leases.values())) is lease
        replay = await continuation(state, first)
        assert not replay.isError
        await registry.aclose()

    try:
        asyncio.run(run())
    finally:
        close(state)


def test_whole_path_too_small_and_tokenizer_failure_leave_no_tokens(tmp_path):
    state = setup(tmp_path)

    class Broken(BytesTokenizer):
        def encode(self, text):
            raise RuntimeError("controlled trusted operator failure")

    async def run():
        small = await invoke(
            state, continuation=True, target={"name": "validate_payment"}, max_bytes=24000
        )
        assert small.isError and decoded(small)["error"]["code"] == "PAGE_TOO_SMALL"
        registry = state[-1]._lease_registry
        assert registry.usage()["active"] == registry.usage()["pending"] == 0
        state[1].context_tokenizer = Broken()
        failed = await invoke(state, continuation=True, max_bytes=50000)
        assert failed.isError and decoded(failed)["error"]["code"] == "TOKENIZER_FAILED"
        assert registry.usage()["active"] == registry.usage()["pending"] == 0
        await registry.aclose()

    try:
        asyncio.run(run())
    finally:
        close(state)


def test_sourcefree_import_retains_proofs_without_live_byte_authority(tmp_path):
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.config import Config
    from atlas_aci.server import build_server_state

    state = setup(tmp_path)
    artifact = tmp_path / "portable.jsonl"
    state[0].export_jsonl(artifact)
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "app").mkdir()
    live = destination / "app/payments.py"
    live.write_text("def DestinationOnly():\n pass\n")
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    config = Config(repo=destination, memex_root=tmp_path / "import-memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    target = imported, config, enforcement, memex, factory

    async def run():
        result = await invoke(target, continuation=True, max_bytes=40000)
        assert not result.isError, result
        body = decoded(result)
        # Failed observations survive import; source-byte authority is separately unavailable.
        assert body["evidence_status"] == "partial"
        assert body["data"]["bundles"] and not body["data"]["resources"]
        sources = [s for a in body["data"]["bundles"] for s in a["sources"]]
        assert sources and all(s["state"] == "unavailable" and s["inline"] is None for s in sources)
        registry = factory._lease_registry
        assert all(
            not lease.resources and lease.pack_bytes == 0 and lease.reader._source_lease is None
            for lease in registry._leases.values()
        )
        assert live.read_text() == "def DestinationOnly():\n pass\n"
        await registry.aclose()

    try:
        asyncio.run(run())
    finally:
        close(target)
        close(state)


def test_actual_expiry_retires_context_connection_pack_and_tokens(tmp_path):
    state = setup(tmp_path)
    state[1].graph_lease_seconds = 1

    async def run():
        first = await invoke(state, continuation=True, max_bytes=40000)
        assert not first.isError
        registry = state[-1]._lease_registry
        lease = next(iter(registry._leases.values()))
        fd, connection = lease.reader._source_lease.fd, lease.reader.db
        resource = decoded(first)["data"]["resources"][0]
        await asyncio.sleep(1.1)
        assert registry.usage()["active"] == 0
        with pytest.raises(OSError):
            os.fstat(fd)
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with pytest.raises(McpError) as exc:
            await read_resource(registry, resource["uri"], state[2])
        assert exc.value.error.data["reason"] == "EXPIRED_REFERENCE"
        expired = await continuation(state, first)
        assert expired.isError and decoded(expired)["error"]["code"] == "EXPIRED_REFERENCE"
        await registry.aclose()

    try:
        asyncio.run(run())
    finally:
        close(state)


def test_independent_credentials_and_real_setup_delay_do_not_change_canonical_package(
    tmp_path, monkeypatch
):
    from atlas_aci.query import context as core

    state = setup(tmp_path)
    original = core.retrieve

    expired_after_real_retrieval = []

    async def delayed(*args, **kwargs):
        data = await original(*args, **kwargs)
        if kwargs["deadline_ms"] == 1500:
            deadline = kwargs["accounting"].deadline_ns
            await asyncio.sleep(max(0, (deadline - time.monotonic_ns()) / 1e9) + 0.005)
            expired_after_real_retrieval.append(True)
        else:
            await asyncio.sleep(0.025)
        return data

    async def run():
        first = await invoke(state, continuation=True, max_bytes=40000)
        assert not first.isError, first
        monkeypatch.setattr(core, "retrieve", delayed)
        second = await invoke(state, continuation=True, max_bytes=40000)
        assert not second.isError, second
        left, right = decoded(first)["data"], decoded(second)["data"]
        assert left["next_cursor"] != right["next_cursor"]
        assert left["bundles"] == right["bundles"]
        assert left["work"] == right["work"]
        print(
            json.dumps(
                {"left_frontiers": left["frontiers"], "right_frontiers": right["frontiers"]},
                sort_keys=True,
            )
        )
        assert left["retrieval_record_id"] == right["retrieval_record_id"]
        assert all(
            f["deadline_scope"] == "configured_shared_context"
            and f["effective_limits"]["deadline_ms"] == 2000
            for f in left["frontiers"]
        )
        registry = state[-1]._lease_registry
        assert registry.usage()["active"] == 2
        expired = await invoke(state, continuation=True, max_bytes=40000, deadline_ms=1500)
        assert expired_after_real_retrieval == [True]
        assert expired.isError and decoded(expired)["error"]["code"] == "CONTEXT_BUDGET_EXCEEDED"
        assert registry.usage()["pending"] == 0 and registry.usage()["active"] == 2
        await registry.aclose()

    try:
        asyncio.run(run())
    finally:
        close(state)
