"""Real issued references reject tampering; unpublished cancellation releases owners."""

import asyncio
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import McpError
from test_s13_contract import close, invoke, page, setup

from atlas_aci.protocol.resources import next_uri, read_resource
from atlas_aci.query.leases import LeaseRegistry


def test_issued_span_tokens_and_post_acquisition_corruption(tmp_path):
    state = setup(tmp_path)

    async def exercise():
        first = await page(state)
        registry = state[-1]._lease_registry
        descriptor = first["data"]["resources"][0]
        token = parse_qs(urlsplit(descriptor["uri"]).query)["token"][0]
        cursor = first["data"]["next_cursor"]
        bad_mac = token[:-1] + ("A" if token[-1] != "A" else "B")
        candidates = [
            next_uri("0" * 64, token, 0, 4000),
            next_uri(descriptor["id"], bad_mac, 0, 4000),
            next_uri(descriptor["id"], cursor, 0, 4000),
            next_uri(
                descriptor["id"], token, descriptor["end_byte"] - descriptor["start_byte"] + 1, 4000
            ),
            descriptor["uri"] + "&offset=0",
        ]
        for uri in candidates:
            with pytest.raises(McpError) as error:
                await read_resource(registry, uri, state[3])
            assert error.value.error.code == -32602
        # A fresh process-local authority cannot recognize the old credential.
        other = LeaseRegistry(state[2], state[-1])
        with pytest.raises(McpError) as error:
            await read_resource(other, descriptor["uri"], state[3])
        assert error.value.error.data["reason"] == "INVALID_REFERENCE"
        await other.aclose()
        # Existing issued G1 remains usable after every rejected request.
        content = (await read_resource(registry, descriptor["uri"], state[3]))[0]
        assert content.content == state[1][descriptor["start_byte"] : descriptor["end_byte"]]
        lease = next(iter(registry._leases.values()))
        source = lease.reader._source_lease
        path = (
            state[0].atlas_dir
            / "source-snapshots"
            / ("pack." + source.manifest["pack"]["sha256"] + ".bin")
        )
        fd = os.open(path, os.O_WRONLY)
        try:
            position = source.index[descriptor["source_sha256"]]["offset"]
            original = os.pread(source.fd, 1, position)
            os.pwrite(fd, bytes([original[0] ^ 1]), position)
        finally:
            os.close(fd)
        with pytest.raises(McpError) as error:
            await read_resource(registry, descriptor["uri"], state[3])
        assert error.value.error.code == -32603
        assert error.value.error.data["reason"] == "SOURCE_CORRUPT"
        await close(state)

    asyncio.run(exercise())


def test_cancel_at_real_install_await_closes_unpublished_reader(tmp_path, monkeypatch):
    state = setup(tmp_path)
    entered = asyncio.Event()
    captured = []
    original = LeaseRegistry.install

    async def barrier(self, owner, reader, *args, **kwargs):
        captured.append((reader.db, reader._source_lease, reader._source_lease.fd))
        entered.set()
        await asyncio.Event().wait()
        return await original(self, owner, reader, *args, **kwargs)

    monkeypatch.setattr(LeaseRegistry, "install", barrier)

    async def exercise():
        task = asyncio.create_task(
            invoke(
                state,
                {
                    "op": "page",
                    "query": {
                        "schema_version": "atlas.graph.v1",
                        "op": "neighborhood",
                        "seed": {"name": "A"},
                        "deadline_ms": 2000,
                    },
                    "page_max_bytes": 18000,
                },
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        assert state[-1]._lease_registry.usage()["pending"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state[-1]._lease_registry.usage()["active"] == 0
        assert state[-1]._lease_registry.usage()["pending"] == 0
        db, source, fd = captured[0]
        with pytest.raises(Exception, match="closed"):
            db.execute("SELECT 1")
        with pytest.raises(OSError):
            os.fstat(fd)
        assert source.fd is None
        await close(state)

    asyncio.run(exercise())


def test_source_free_import_pages_never_authorize_live_source(tmp_path):
    state = setup(tmp_path)
    artifact = tmp_path / "portable.jsonl"
    state[0].export_jsonl(artifact)
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "a.py").write_text("def live_destination():\n pass\n")
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.config import Config
    from atlas_aci.server import build_server_state

    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    config = Config(repo=destination, memex_root=tmp_path / "import-memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    target = imported, state[1], config, enforcement, memex, factory

    async def exercise():
        first = await page(target)
        assert first["evidence_status"] == "unknown"
        data = first["data"]
        assert data["items"] and not data["resources"]
        assert all(not atom["resources"] for atom in data["items"])
        registry = factory._lease_registry
        assert all(not lease.resources for lease in registry._leases.values())
        assert (destination / "a.py").read_text() == "def live_destination():\n pass\n"
        await close(target)
        await close(state)

    asyncio.run(exercise())
