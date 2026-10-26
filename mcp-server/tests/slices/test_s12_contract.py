"""Acceptance anchors through production dispatch and actual SDK result framing."""

import asyncio
import json

import pytest
from jsonschema import Draft202012Validator

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA
from atlas_aci.protocol.results import result_bytes
from atlas_aci.query.admission import MINIMUM_BUDGET
from atlas_aci.server import build_server_state, call_tool_result


@pytest.fixture
def state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n B(); B()\ndef B():\n C()\ndef C():\n A()\n")
    writer = CodeGraph(repo)
    writer.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement, memex, factory = build_server_state(config)
    yield config, enforcement, memex, factory, writer
    factory.close()
    writer.close()


def invoke(state, **arguments):
    config, enforcement, memex, factory, _ = state
    args = {
        "schema_version": "atlas.graph.v1",
        "op": "neighborhood",
        "seed": {"name": "A"},
        "deadline_ms": 2000,
        **arguments,
    }
    wire = asyncio.run(call_tool_result("graph_query", args, config, enforcement, memex, factory))
    payload = json.loads(wire.content[0].text)
    Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(payload)
    assert wire.structuredContent == payload
    assert not wire.isError, payload
    return payload, wire


def test_direction_filter(state):
    out, _ = invoke(state, max_depth=1)
    inc, _ = invoke(state, max_depth=1, direction="incoming")
    assert len(out["data"]["edges"]) == 2
    assert len(inc["data"]["edges"]) == 1
    assert all(e["traversal_direction"] == "incoming" for e in inc["data"]["edges"])
    filtered, _ = invoke(state, relations=["superclass"], max_depth=3)
    assert filtered["data"]["edges"] == [] and filtered["data"]["outcome"] == "complete"


def test_path_evidence(state):
    payload, _ = invoke(state, op="path", target={"name": "C"}, max_depth=3)
    path = payload["data"]["path"]
    assert [n["name"] for n in path["nodes"]] == ["A", "B", "C"]
    with state[-1].reader_lease() as reader:
        for hop in path["hops"]:
            for kind, table in [
                ("assertion", "assertions"),
                ("evidence", "evidence"),
                ("occurrence", "occurrences"),
                ("producer", "producers"),
            ]:
                row = reader.db.execute(
                    "SELECT payload FROM " + table + " WHERE id=?", (hop[kind]["id"],)
                ).fetchone()
                assert json.loads(row[0]) == hop[kind]


def test_visit_budget(state):
    payload, _ = invoke(state, max_visited_nodes=1)
    assert payload["data"]["stop_reason"] == "visited_node_limit"
    assert payload["data"]["work"]["visited_nodes"] == 1
    assert payload["data"]["edges"] == []


def test_deadline(state):
    payload, _ = invoke(state, deadline_ms=0)
    assert payload["data"]["outcome"] == "partial"
    assert payload["data"]["stop_reason"] == "deadline"
    assert payload["data"]["work"]["sql_queries"] == 0


def test_ambiguous_seed(state):
    writer = state[-1]
    (writer.repo / "b.py").write_text("def A():\n pass\n")
    writer.build()
    payload, _ = invoke(state)
    assert payload["data"]["outcome"] == "disambiguation"
    assert payload["data"]["seed"]["state"] == "ambiguous"
    assert payload["data"]["seed"]["candidates_complete"] is False
    assert payload["data"]["work"]["visited_nodes"] == 0


def test_traversal_replay(state):
    first, wire1 = invoke(state, max_examined_edges=1)
    second, wire2 = invoke(state, max_examined_edges=1)
    assert first == second and result_bytes(wire1) == result_bytes(wire2)


def test_examined_edge_budget(state):
    payload, _ = invoke(state, max_examined_edges=1)
    data = payload["data"]
    assert data["stop_reason"] == "examined_edge_limit"
    assert data["work"]["examined_edges"] == data["work"]["edge_queries"] == 1
    assert len(data["edges"]) == 1


def test_serialized_byte_budget(state):
    payload, wire = invoke(state, op="path", target={"name": "C"}, max_bytes=MINIMUM_BUDGET)
    assert len(result_bytes(wire)) <= MINIMUM_BUDGET
    assert payload["operation"] == "path" and payload["data"]["path"] is None
    assert payload["data"]["stop_reason"] == "byte_limit"


def test_depth_budget(state):
    payload, _ = invoke(state, max_depth=0)
    assert payload["data"]["stop_reason"] == "depth_limit"
    assert payload["data"]["work"]["edge_queries"] == 0
    assert len(payload["data"]["nodes"]) == 1


def test_all_filters_fit_metadata_minimum(state):
    from atlas_aci.storage import evidence as ev

    payload, wire = invoke(state, max_bytes=MINIMUM_BUDGET, relations=sorted(ev.RELATIONS))
    assert len(result_bytes(wire)) <= MINIMUM_BUDGET
    assert payload["data"]["outcome"] == "partial"


def test_element_cap_preserves_path_atomicity(state):
    state[0].max_bound_field_elements = 1
    payload, _ = invoke(state, op="path", target={"name": "C"}, max_depth=5)
    assert payload["data"]["outcome"] == "partial"
    assert payload["data"]["path"] is None
    assert payload["data"]["stop_reason"] == "element_limit"


def test_known_operation_errors_preserved(state):
    config, enforcement, memex, factory, _ = state
    args = {"schema_version": "atlas.graph.v1", "op": "path", "seed": {"name": "A"}}
    wire = asyncio.run(call_tool_result("graph_query", args, config, enforcement, memex, factory))
    assert wire.isError and wire.structuredContent["operation"] == "path"
    assert wire.structuredContent["error"]["code"] == "INVALID_INPUT"


def test_path_all_filters_fit_metadata_minimum(state):
    from atlas_aci.storage import evidence as ev

    payload, wire = invoke(
        state,
        op="path",
        target={"name": "C"},
        max_bytes=MINIMUM_BUDGET,
        relations=sorted(ev.RELATIONS),
    )
    assert len(result_bytes(wire)) <= MINIMUM_BUDGET
    assert payload["data"]["path"] is None
