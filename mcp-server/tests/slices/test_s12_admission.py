"""SDK framing, adversarial payloads and actual request lifetime controls."""

import asyncio
import json
import sqlite3
from contextlib import contextmanager

import pytest
from test_s12_contract import invoke
from test_s12_contract import state as state

from atlas_aci.protocol.graph import envelope
from atlas_aci.protocol.results import bounded_result, result_bytes
from atlas_aci.query import traversal
from atlas_aci.query.admission import MINIMUM_BUDGET
from atlas_aci.server import call_tool_result


def direct(state, arguments, **kwargs):
    config, enforcement, memex, factory, _ = state
    return asyncio.run(
        call_tool_result("graph_query", arguments, config, enforcement, memex, factory, **kwargs)
    )


@pytest.mark.parametrize("op", ["neighborhood", "path"])
@pytest.mark.parametrize("budget", [0, 100, MINIMUM_BUDGET - 1])
def test_below_measured_common_minimum(state, op, budget):
    args = {
        "schema_version": "atlas.graph.v1",
        "op": op,
        "seed": {"name": "A"},
        "max_bytes": budget,
        **({"target": {"name": "B"}} if op == "path" else {}),
    }
    wire = direct(state, args)
    assert wire.isError and wire.structuredContent["operation"] == op
    assert wire.structuredContent["error"]["code"] == "INVALID_BUDGET"


@pytest.mark.parametrize("operation", ["neighborhood", "path"])
def test_status_payload_cannot_satisfy_other_operation(operation):
    wire = bounded_result(
        envelope(None, error={"code": "INTERNAL", "message": "bad", "retry_hint": "none"}),
        error=True,
        mode="structured",
        structured=True,
        operation=operation,
        budget=10000,
    )
    assert wire.isError and wire.structuredContent["operation"] == operation
    assert wire.structuredContent["error"]["code"] == "INVALID_RESULT"


@pytest.mark.parametrize("value", [True, 1.0, -1, 10001])
def test_strict_limits_not_internal_errors(state, value):
    args = {
        "schema_version": "atlas.graph.v1",
        "op": "neighborhood",
        "seed": {"name": "A"},
        "max_examined_edges": value,
    }
    wire = direct(state, args)
    assert wire.isError and wire.structuredContent["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize("value", [[], {}, None, 1])
def test_malformed_operation_safe_before_discriminator(state, value):
    wire = direct(state, {"schema_version": "atlas.graph.v1", "op": value})
    assert wire.isError and wire.structuredContent["operation"] == "status"
    assert wire.structuredContent["error"]["code"] == "INVALID_OPERATION"


def test_long_identity_whole_record_and_ambiguous_prefix(state):
    writer = state[-1]
    name = "é" * 3000
    (writer.repo / "a.py").write_text("def " + name + "():\n pass\n")
    (writer.repo / "b.py").write_text("def " + name + "():\n pass\n")
    writer.build()
    payload, wire = invoke(state, seed={"name": name}, max_bytes=MINIMUM_BUDGET)
    assert payload["data"]["outcome"] == "disambiguation"
    assert payload["data"]["seed"]["state"] == "ambiguous"
    assert payload["data"]["seed"]["candidates"] == []
    assert payload["data"]["seed"]["candidates_complete"] is False
    assert len(result_bytes(wire)) <= MINIMUM_BUDGET
    (writer.repo / "b.py").unlink()
    writer.build()
    payload, wire = invoke(state, seed={"name": name}, max_bytes=MINIMUM_BUDGET)
    assert payload["data"]["outcome"] == "partial"
    assert payload["data"]["nodes"] == [] and payload["data"]["edges"] == []
    assert len(result_bytes(wire)) <= MINIMUM_BUDGET


def test_zero_element_cap_returns_no_partial_proof(state):
    state[0].max_bound_field_elements = 0
    payload, _ = invoke(state)
    assert payload["data"]["outcome"] == "partial"
    assert payload["data"]["stop_reason"] == "element_limit"
    assert payload["data"]["nodes"] == payload["data"]["edges"] == []


@pytest.mark.parametrize("protocol", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"])
def test_actual_representation_stays_under_same_budget(state, protocol):
    args = {
        "schema_version": "atlas.graph.v1",
        "op": "path",
        "seed": {"name": "A"},
        "target": {"name": "C"},
        "max_bytes": MINIMUM_BUDGET,
        "deadline_ms": 2000,
    }
    wire = direct(state, args, protocol_version=protocol)
    payload = json.loads(wire.content[0].text)
    assert not wire.isError and payload["data"]["path"] is None
    assert len(result_bytes(wire)) <= MINIMUM_BUDGET
    assert (wire.structuredContent is not None) == (protocol in {"2025-06-18", "2025-11-25"})


def test_actual_cancel_at_owned_hook_closes_handle_once(state, monkeypatch):
    factory = state[-2]
    original = factory.reader_lease
    held = {}

    @contextmanager
    def lease():
        with original() as reader:
            held["reader"] = reader
            held["conn"] = reader.db
            held["source"] = reader._source_lease
            yield reader

    monkeypatch.setattr(factory, "reader_lease", lease)

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def gate(_delay):
            entered.set()
            await release.wait()

        monkeypatch.setattr(traversal.asyncio, "sleep", gate)
        config, enforcement, memex, _, _ = state
        task = asyncio.create_task(
            call_tool_result(
                "graph_query",
                {"schema_version": "atlas.graph.v1", "op": "neighborhood", "seed": {"name": "A"}},
                config,
                enforcement,
                memex,
                factory,
            )
        )
        await entered.wait()
        assert held["reader"]._validated_lease and held["source"].fd is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert held["reader"]._closed and held["source"].fd is None
    with pytest.raises(sqlite3.ProgrammingError):
        held["conn"].execute("SELECT 1")


def test_real_elapsed_deadline_at_controlled_await(state, monkeypatch):
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
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
                    "target": {"name": "C"},
                    "deadline_ms": 10,
                },
                config,
                enforcement,
                memex,
                factory,
            )
        )
        await entered.wait()
        asyncio.get_running_loop().call_later(0.025, release.set)
        result = await task
        assert not result.isError
        assert result.structuredContent["data"]["stop_reason"] == "deadline"
        assert result.structuredContent["data"]["path"] is None
        assert result.structuredContent["data"]["work"]["sql_queries"] == 0

    asyncio.run(exercise())


def test_operational_hydration_failure_closes_real_handles(state, monkeypatch):
    factory = state[-2]
    original = factory.reader_lease
    held = {}

    @contextmanager
    def lease():
        with original() as reader:
            held["conn"] = reader.db
            held["source"] = reader._source_lease

            def authorize(action, arg1, _arg2, _db, _trigger):
                if action == sqlite3.SQLITE_READ and arg1 == "evidence":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            reader.db.set_authorizer(authorize)
            yield reader

    monkeypatch.setattr(factory, "reader_lease", lease)
    result = direct(
        state,
        {
            "schema_version": "atlas.graph.v1",
            "op": "neighborhood",
            "seed": {"name": "A"},
            "deadline_ms": 2000,
        },
    )
    assert result.isError and result.structuredContent["error"]["code"] == "INTERNAL"
    assert held["source"].fd is None
    with pytest.raises(sqlite3.ProgrammingError):
        held["conn"].execute("SELECT 1")
    # The published generation remains usable after this request's real SQL failure.
    with original() as reader:
        assert reader.db.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.parametrize("selector", ["seed", "target"])
def test_nested_candidate_identity_is_whole_and_capped(state, selector):
    writer = state[-1]
    for name in ("nested1.py", "nested2.py"):
        (writer.repo / name).write_text("class Outer:\n class Inner:\n  def leaf(self):\n   pass\n")
    writer.build()
    state[0].max_bound_field_elements = 1
    arguments = (
        {"seed": {"name": "leaf"}}
        if selector == "seed"
        else {"op": "path", "target": {"name": "leaf"}}
    )
    payload, _ = invoke(state, **arguments)
    selection = payload["data"][selector]
    assert selection["state"] == "ambiguous"
    assert selection["candidates_complete"] is False
    assert selection["candidates"] == []


def test_generated_path_endpoint_cannot_contradict_selection(state):
    import copy

    from atlas_aci.protocol.graph import validate_navigation

    payload, _ = invoke(state, op="path", target={"name": "C"})
    for endpoint, other in [("seed", "target"), ("target", "seed")]:
        forged = copy.deepcopy(payload)
        forged["data"][endpoint]["candidates"] = forged["data"][other]["candidates"]
        with pytest.raises(ValueError):
            validate_navigation(forged, "path", state[0].max_bound_field_elements)


def test_rehashed_generated_occurrence_owner_cannot_contradict_assertion(state):
    import copy

    from atlas_aci.protocol.graph import validate_navigation
    from atlas_aci.storage import evidence as ev

    payload, _ = invoke(state, op="path", target={"name": "C"})
    forged = copy.deepcopy(payload)
    hop = forged["data"]["path"]["hops"][0]
    hop["occurrence"]["entity_id"] = hop["assertion"]["target_entity_id"]
    hop["occurrence"]["id"] = ev.identifier(hop["occurrence"])
    hop["evidence"]["occurrence_id"] = hop["occurrence"]["id"]
    hop["evidence"]["id"] = ev.identifier(hop["evidence"])
    hop["assertion"]["evidence_id"] = hop["evidence"]["id"]
    hop["assertion"]["id"] = ev.identifier(hop["assertion"])
    for kind in ("occurrence", "evidence", "assertion"):
        ev.validate_record(hop[kind])
    with pytest.raises(ValueError):
        validate_navigation(forged, "path", state[0].max_bound_field_elements)
