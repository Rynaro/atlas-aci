"""Whole-candidate result budgets and generated canonical/source proof boundary."""

import asyncio
import copy

import pytest
from test_s17_contract import call, close, setup

from atlas_aci.protocol.graph import validate_retrieval
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP, result_bytes
from atlas_aci.query.admission import lists


def test_entity_cannot_switch_to_wholefile_to_bypass_source_proof(tmp_path):
    state = setup(tmp_path)
    try:
        result = asyncio.run(call(state, "Alpha"))
        assert not result.isError
        forged = copy.deepcopy(result.structuredContent)
        candidate = next(
            c
            for c in forged["data"]["candidates"]
            if c["kind"] == "entity" and c["source"]["state"] == "available"
        )
        source = candidate["source"]
        source.update(
            granularity="file", start_byte=0, end_byte=source["source_size"], source_sha256="0" * 64
        )
        with pytest.raises(ValueError):
            validate_retrieval(forged, state[1].max_bound_field_elements)
    finally:
        close(state)


def test_actual_sdk_byte_prefix_and_recursive_element_cap(tmp_path):
    state = setup(tmp_path)

    async def check():
        for budget in (0, 1, 100, 4000, 8000, 18000):
            result = await call(state, "Alpha", max_bytes=budget)
            if result.isError:
                assert result.structuredContent["error"]["code"] in {
                    "INVALID_BUDGET",
                    "RESPONSE_TOO_LARGE",
                }
                assert len(result_bytes(result)) <= max(budget, FIXED_ERROR_ENVELOPE_CAP)
            else:
                assert len(result_bytes(result)) <= budget
                validate_retrieval(result.structuredContent, state[1].max_bound_field_elements)
        state[1].max_bound_field_elements = 1
        result = await call(state, "Alpha")
        assert not result.isError, result
        assert all(len(items) <= 1 for items in lists(result.structuredContent["data"]))
        literal = await call(state, "task phrase", max_candidates=1)
        assert not literal.isError and len(literal.structuredContent["data"]["candidates"]) == 1

    try:
        asyncio.run(check())
    finally:
        close(state)


def test_sourcefree_metadata_reference_remains_unknown(tmp_path):
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.config import Config
    from atlas_aci.server import build_server_state

    state = setup(tmp_path)
    artifact = tmp_path / "portable.jsonl"
    state[0].export_jsonl(artifact)
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "a.py").write_text("different live bytes")
    graph = CodeGraph(destination)
    graph.import_jsonl(artifact)
    config = Config(repo=destination, memex_root=tmp_path / "import-memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    imported = graph, config, enforcement, memex, factory
    try:
        result = asyncio.run(call(imported, "Alpha"))
        assert not result.isError, result
        data = result.structuredContent["data"]
        assert data["candidates"]
        assert (
            data["coverage"]["state"] == "unavailable"
            and data["coverage"]["unavailable_count"] is None
        )
        assert all(
            c["kind"] == "entity" and c["source"]["state"] == "unavailable"
            for c in data["candidates"]
        )
        validate_retrieval(result.structuredContent, config.max_bound_field_elements)
    finally:
        close(imported)
        close(state)
