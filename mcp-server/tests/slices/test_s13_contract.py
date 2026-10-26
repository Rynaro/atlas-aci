"""Production page/continuation and exact source-resource acceptance anchors."""

import asyncio
import base64
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import McpError, types

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.protocol.resources import next_uri, read_resource, wire_bytes
from atlas_aci.protocol.results import result_bytes
from atlas_aci.server import build_server_state, call_tool_result


def setup(tmp_path, **policy):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = b"def A():\n " + b"B(); " * 20 + b"\ndef B():\n pass\n"
    (repo / "a.py").write_bytes(source)
    writer = CodeGraph(repo)
    writer.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0, **policy)
    enforcement, memex, factory = build_server_state(config)
    return writer, source, config, enforcement, memex, factory


async def invoke(state, args):
    _w, _s, config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", **args},
        config,
        enforcement,
        memex,
        factory,
    )


async def page(state):
    result = await invoke(
        state,
        {
            "op": "page",
            "query": {
                "schema_version": "atlas.graph.v1",
                "op": "neighborhood",
                "seed": {"name": "A"},
                "max_depth": 1,
                "deadline_ms": 2000,
            },
            "page_max_bytes": 18000,
        },
    )
    assert not result.isError, result
    assert len(result_bytes(result)) <= 18000
    return result.structuredContent


async def close(state):
    registry = getattr(state[-1], "_lease_registry", None)
    if registry is not None:
        await registry.aclose()
    state[-1].close()
    state[0].close()


def test_exact_source_resource(tmp_path):
    state = setup(tmp_path)

    async def exercise():
        first = await page(state)
        descriptor = first["data"]["resources"][0]
        for version in (2, 3):
            (state[0].repo / "a.py").write_text("def new" + str(version) + "():\n pass\n")
            state[0].build()
        content = (await read_resource(state[-1]._lease_registry, descriptor["uri"], state[3]))[0]
        assert content.content == state[1][descriptor["start_byte"] : descriptor["end_byte"]]
        assert content.meta["generation_id"] == first["generation_id"]
        await close(state)

    asyncio.run(exercise())


def test_continuation_snapshot(tmp_path):
    state = setup(tmp_path)

    async def exercise():
        first = await page(state)
        data = first["data"]
        assert data["next_cursor"]
        registry = state[-1]._lease_registry
        lease = next(iter(registry._leases.values()))
        held = lease.reader.db
        source = lease.reader._source_lease
        old = (
            state[0].atlas_dir
            / "source-snapshots"
            / ("pack." + source.manifest["pack"]["sha256"] + ".bin")
        )
        for version in (2, 3):
            (state[0].repo / "a.py").write_text("def new" + str(version) + "():\n pass\n")
            state[0].build()
        assert not old.exists()
        result = await invoke(
            state,
            {"op": "continue", "cursor": data["next_cursor"], "query_digest": data["query_digest"]},
        )
        assert (
            not result.isError
            and result.structuredContent["generation_id"] == first["generation_id"]
        )
        assert (
            held.execute("SELECT value FROM manifest WHERE key='generation_id'").fetchone()[0]
            == first["generation_id"]
        )
        assert source.read("a.py") == state[1]
        await close(state)

    asyncio.run(exercise())


def test_expired_reference(tmp_path):
    state = setup(tmp_path, graph_lease_seconds=1)

    async def exercise():
        first = await page(state)
        uri = first["data"]["resources"][0]["uri"]
        registry = state[-1]._lease_registry
        source = next(iter(registry._leases.values())).reader._source_lease
        await asyncio.sleep(1.05)
        assert source.fd is None and registry.usage()["active"] == 0
        with pytest.raises(McpError) as error:
            await read_resource(registry, uri, state[3])
        assert (
            error.value.error.code == -32002
            and error.value.error.data["reason"] == "EXPIRED_REFERENCE"
        )
        await close(state)

    asyncio.run(exercise())


def test_cursor_scope_mismatch(tmp_path):
    state = setup(tmp_path)

    async def exercise():
        first = await page(state)
        data = first["data"]
        wrong = await invoke(
            state, {"op": "continue", "cursor": data["next_cursor"], "query_digest": "0" * 64}
        )
        assert wrong.isError and wrong.structuredContent["error"]["code"] == "SCOPE_MISMATCH"
        state[2].max_response_bytes -= 1
        wrong = await invoke(
            state,
            {"op": "continue", "cursor": data["next_cursor"], "query_digest": data["query_digest"]},
        )
        assert wrong.isError and wrong.structuredContent["error"]["code"] == "SCOPE_MISMATCH"
        await close(state)

    asyncio.run(exercise())


def test_resource_budget(tmp_path):
    state = setup(tmp_path)
    source = b"def A():\n # NOTE: " + b"x" * 150000 + b"\xc3\xa9Z\n pass\n"
    (state[0].repo / "a.py").write_bytes(source)
    state[0].build()

    async def exercise():
        reply = await invoke(
            state,
            {
                "op": "page",
                "query": {
                    "schema_version": "atlas.graph.v1",
                    "op": "neighborhood",
                    "seed": {"name": "A"},
                    "relations": ["rationale_for"],
                    "direction": "incoming",
                    "deadline_ms": 2000,
                },
                "page_max_bytes": 1024 * 1024,
            },
        )
        assert not reply.isError, reply
        descriptor = reply.structuredContent["data"]["resources"][0]
        assert descriptor["end_byte"] - descriptor["start_byte"] > 4000
        token = parse_qs(urlsplit(descriptor["uri"]).query)["token"][0]
        uri = next_uri(descriptor["id"], token, 0, 4000)
        content = (await read_resource(state[-1]._lease_registry, uri, state[3]))[0]
        result = types.ReadResourceResult(
            contents=[
                types.BlobResourceContents(
                    uri=uri,
                    mimeType=content.mime_type,
                    blob=base64.b64encode(content.content).decode(),
                    _meta=content.meta,
                )
            ]
        )
        assert len(wire_bytes(result)) <= 4000
        assert (
            content.content
            == source[descriptor["start_byte"] : descriptor["start_byte"] + len(content.content)]
        )
        assert content.meta["next_uri"] and not content.meta["complete"]
        # A legal byte offset may start inside a UTF8 sequence; no text decoding.
        split = next_uri(
            descriptor["id"], token, descriptor["end_byte"] - descriptor["start_byte"] - 2, 4000
        )
        tail = (await read_resource(state[-1]._lease_registry, split, state[3]))[0]
        assert tail.content == b"\xa9Z" and tail.meta["complete"]
        await close(state)

    asyncio.run(exercise())


def test_cursor_replay(tmp_path):
    state = setup(tmp_path)

    async def exercise():
        first = await page(state)
        data = first["data"]
        args = {
            "op": "continue",
            "cursor": data["next_cursor"],
            "query_digest": data["query_digest"],
        }
        left, right = await asyncio.gather(invoke(state, args), invoke(state, args))
        assert not left.isError and result_bytes(left) == result_bytes(right)
        await asyncio.sleep(0)
        again = await invoke(state, args)
        assert result_bytes(again) == result_bytes(left)
        await close(state)

    asyncio.run(exercise())


def test_deadline_in_final_state_accounting_rejects_before_transfer(tmp_path, monkeypatch):
    import threading

    import atlas_aci.query.leases as leases

    state = setup(tmp_path, lease_setup_ms=300)
    original = leases.canonical_bytes
    blocked = []

    def delayed(value):
        if isinstance(value, dict) and set(value) == {"manifest", "chunks", "current", "bindings"}:
            blocked.append(True)
            threading.Event().wait(0.4)
        return original(value)

    monkeypatch.setattr(leases, "canonical_bytes", delayed)

    async def exercise():
        result = await invoke(
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
        assert blocked
        assert result.isError and result.structuredContent["error"]["code"] == "SETUP_DEADLINE"
        assert (
            state[-1]._lease_registry.usage()["active"]
            == state[-1]._lease_registry.usage()["pending"]
            == 0
        )
        await close(state)

    asyncio.run(exercise())


@pytest.mark.parametrize("budget", [0, 1])
def test_nested_collection_budget_cannot_understate_saved_metadata(tmp_path, budget):
    state = setup(tmp_path)

    async def exercise():
        result = await invoke(
            state,
            {
                "op": "page",
                "query": {
                    "schema_version": "atlas.graph.v1",
                    "op": "neighborhood",
                    "seed": {"name": "A"},
                    "max_bytes": budget,
                },
                "page_max_bytes": 18000,
            },
        )
        assert result.isError and result.structuredContent["error"]["code"] == "INVALID_BUDGET"
        assert (
            not getattr(state[-1], "_lease_registry", None)
            or state[-1]._lease_registry.usage()["active"] == 0
        )
        await close(state)

    asyncio.run(exercise())


@pytest.mark.parametrize("mutation", ["work", "seed"])
def test_generated_captured_metadata_preserves_s12_invariants(tmp_path, mutation):
    import copy

    from atlas_aci.protocol.graph import validate_page

    state = setup(tmp_path)

    async def exercise():
        first = await page(state)
        forged = copy.deepcopy(first)
        data = forged["data"]["captured"]
        if mutation == "work":
            data["work"]["examined_edges"] = data["effective_limits"]["max_examined_edges"] + 1
        else:
            data["seed"]["entity_id"] = "0" * 64
        with pytest.raises(ValueError):
            validate_page(forged, "page", state[2].max_bound_field_elements)
        await close(state)

    asyncio.run(exercise())
