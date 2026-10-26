"""Actual G1 SQLite+pack ownership, cached whole atoms and finite admission."""

import asyncio
import json
import sqlite3

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import ToolError
from atlas_aci.query.leases import LeaseRegistry, canonical_bytes, digest
from atlas_aci.query.traversal import navigate


def setup(tmp_path, **policy):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = b"def A():\n B(); B(); B()\ndef B():\n pass\n"
    (repo / "a.py").write_bytes(source)
    writer = CodeGraph(repo)
    writer.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex", **policy)
    return writer, source, LeaseRegistry(config, writer)


async def issue(registry, writer):
    async with registry.pending() as reservation:
        with reservation.owner as stack:
            reader = stack.enter_context(writer.reader_lease(chunk_catalog_bytes=1024 * 1024))
            result = await navigate(reader, seed={"name": "A"}, max_depth=1, deadline_ms=2000)
            nodes = {n["id"]: n for n in result["nodes"]}

            def pages(lease):
                pages = []
                for index, proof in enumerate(result["edges"]):
                    assertion = proof["assertion"]
                    atom = {
                        "proof": proof,
                        "nodes": [
                            nodes[assertion[k]] for k in ("source_entity_id", "target_entity_id")
                        ],
                    }
                    pages.append(
                        canonical_bytes(
                            {
                                "generation": reader.generation_id,
                                "atom": atom,
                                "next": registry.token(lease, "cursor", index + 1)
                                if index + 1 < len(result["edges"])
                                else None,
                            }
                        )
                    )
                return pages

            return await registry.install(
                stack, reader, digest({"seed": "A"}), pages, {}, reservation=reservation
            )


def test_actual_g1_owned_across_g3_pruning_and_duplicate_replay(tmp_path):
    writer, source, registry = setup(tmp_path, max_graph_leases=1)

    async def exercise():
        lease = await issue(registry, writer)
        conn = lease.reader.db
        pack = lease.reader._source_lease
        old = (
            writer.atlas_dir
            / "source-snapshots"
            / ("pack." + pack.manifest["pack"]["sha256"] + ".bin")
        )
        first = json.loads(lease.pages[0])
        cursor = first["next"]
        assert cursor
        for version in (2, 3):
            (writer.repo / "a.py").write_text("def new" + str(version) + "():\n pass\n")
            writer.build()
        assert not old.exists()
        assert pack.read("a.py") == source
        assert (
            conn.execute("SELECT value FROM manifest WHERE key='generation_id'").fetchone()[0]
            == first["generation"]
        )
        before = registry.usage()
        assert before["active"] == before["sqlite_handles"] == before["pack_fds"] == 1
        with pytest.raises(ToolError, match="capacity"):
            async with registry.pending():
                pytest.fail("unexpired lease evicted")
        results = await asyncio.gather(
            registry.page(cursor, lease.query), registry.page(cursor, lease.query)
        )
        assert results[0] == results[1] == lease.pages[1]
        assert registry.usage() == before
        await registry.aclose()
        assert pack.fd is None
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    try:
        asyncio.run(exercise())
    finally:
        writer.close()


def test_state_admission_failure_keeps_request_ownership(tmp_path):
    writer, _source, registry = setup(tmp_path, max_lease_state_bytes=1)

    async def exercise():
        with pytest.raises(ToolError, match="capacity"):
            await issue(registry, writer)
        assert registry.usage()["pending"] == registry.usage()["active"] == 0
        await registry.aclose()

    try:
        asyncio.run(exercise())
    finally:
        writer.close()


def test_idle_expiry_real_timer_closes_handles(tmp_path):
    writer, _source, registry = setup(tmp_path, graph_lease_seconds=1)

    async def exercise():
        lease = await issue(registry, writer)
        conn = lease.reader.db
        source = lease.reader._source_lease
        token = json.loads(lease.pages[0])["next"]
        # This wait deliberately tests expiry in an idle RUNNING event loop.
        await asyncio.sleep(1.05)
        assert not registry.usage()["active"] and source.fd is None
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        with pytest.raises(ToolError, match="expired"):
            await registry.page(token, lease.query)
        await registry.aclose()

    try:
        asyncio.run(exercise())
    finally:
        writer.close()


def test_immediate_shutdown_without_timer_scheduling(tmp_path):
    writer, _source, registry = setup(tmp_path)

    async def exercise():
        lease = await issue(registry, writer)
        source = lease.reader._source_lease
        conn = lease.reader.db
        await registry.aclose()
        assert source.fd is None and registry.usage()["active"] == 0
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    try:
        asyncio.run(exercise())
    finally:
        writer.close()


def test_full_retained_lease_identity_metadata_charged_near_cap(tmp_path):
    writer, _source, registry = setup(tmp_path)

    async def exercise():
        first = await issue(registry, writer)
        # Reconstruct the PREVIOUS accounting components, not a hardcoded size.
        source = first.reader._source_lease
        metadata = {
            "manifest": source.manifest,
            "chunks": source.chunk_catalog,
            "current": source.current,
            "bindings": sorted((p, h, v) for (p, h), v in source.bindings.items()),
        }
        old_components = (
            sum(map(len, first.pages)) + len(canonical_bytes({})) + len(canonical_bytes(metadata))
        )
        await asyncio.sleep(0)
        await registry.aclose()
        registry.config.max_lease_state_bytes = old_components
        second = LeaseRegistry(registry.config, writer)
        try:
            with pytest.raises(ToolError, match="capacity"):
                await issue(second, writer)
            assert second.usage()["active"] == 0
        finally:
            await second.aclose()

    try:
        asyncio.run(exercise())
    finally:
        writer.close()
