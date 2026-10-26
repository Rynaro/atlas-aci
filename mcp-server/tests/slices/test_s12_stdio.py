"""Real locked SDK negotiation and stdout frames for admitted graph proofs."""

import asyncio
import json

import pytest
from jsonschema import Draft202012Validator
from test_s11_stdio import session_calls
from test_s12_storage import native7

from atlas_aci.codegraph import CodeGraph
from atlas_aci.protocol.graph import GRAPH_OUTPUT_SCHEMA
from atlas_aci.protocol.results import result_bytes
from atlas_aci.query.admission import MINIMUM_BUDGET


def build(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    (path / "a.py").write_text("def A():\n B(); B()\ndef B():\n C()\ndef C():\n pass\n")
    graph = CodeGraph(path)
    graph.build()
    graph.close()
    return path


@pytest.mark.parametrize(
    "version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01"]
)
def test_native_operations_and_admitted_wire(tmp_path, version):
    path = build(tmp_path)
    common = {"schema_version": "atlas.graph.v1", "seed": {"name": "A"}, "deadline_ms": 2000}
    arguments = [
        {"op": "neighborhood", "max_depth": 1},
        {"op": "path", "target": {"name": "C"}},
        {"op": "neighborhood", "max_examined_edges": 1},
        {"op": "path", "target": {"name": "Absent"}},
        {"op": "path", "target": {"name": "C"}, "max_bytes": MINIMUM_BUDGET},
        {"op": "path", "target": {"name": "C"}, "query": "callers_of:B"},
    ]
    initialized, manifest, results, frames = asyncio.run(
        session_calls(
            tmp_path, path, version, 100000, [("graph_query", {**common, **a}) for a in arguments]
        )
    )
    negotiated = "2025-11-25" if version == "2099-01-01" else version
    assert initialized.protocolVersion == negotiated
    modern = negotiated in {"2025-06-18", "2025-11-25"}
    tool = next(t for t in manifest.tools if t.name == "graph_query")
    assert (tool.outputSchema is not None) == modern
    payloads = [json.loads(r.content[0].text) for r in results]
    assert payloads[0]["data"]["stop_reason"] == "depth_limit"
    assert len(payloads[0]["data"]["edges"]) == 2
    assert [n["name"] for n in payloads[1]["data"]["path"]["nodes"]] == ["A", "B", "C"]
    assert payloads[2]["data"]["work"]["edge_queries"] == 1
    assert payloads[3]["data"]["outcome"] == "no_evidence"
    assert payloads[4]["data"]["path"] is None
    assert payloads[4]["data"]["stop_reason"] == "byte_limit"
    assert payloads[5]["operation"] == "path" and payloads[5]["error"]["code"] == "MIXED_REQUEST"
    assert [r.isError for r in results] == [False] * 5 + [True]
    calls = [line for line in frames if "content" in json.loads(line).get("result", {})]
    assert len(calls) == len(results)
    for index, (payload, result, frame) in enumerate(zip(payloads, results, calls, strict=True)):
        Draft202012Validator(GRAPH_OUTPUT_SCHEMA).validate(payload)
        assert result.structuredContent == (payload if modern else None)
        assert b'"result":' + result_bytes(result) in frame
        budget = MINIMUM_BUDGET if index == 4 else 100000
        assert len(result_bytes(result)) <= budget


def test_native7_actual_stdio_upgrade_and_retained_legacy(tmp_path):
    path, identity = native7(tmp_path)
    _, _, results, _ = asyncio.run(
        session_calls(
            tmp_path,
            path,
            "2025-11-25",
            100000,
            [
                ("graph_query", {"query": "callers_of:B"}),
                ("graph_query", {"schema_version": "atlas.graph.v1", "op": "status"}),
                (
                    "graph_query",
                    {
                        "schema_version": "atlas.graph.v1",
                        "op": "neighborhood",
                        "seed": {"name": "A"},
                    },
                ),
            ],
        )
    )
    assert [r.isError for r in results] == [False, False, True]
    assert results[1].structuredContent["generation_id"] == identity["generation_id"]
    assert results[2].structuredContent["operation"] == "neighborhood"
    assert results[2].structuredContent["error"]["code"] == "INDEX_UPGRADE_REQUIRED"
    assert not (path / ".atlas/graph.8.db").exists()


def test_actual_stdio_missing_index_is_not_empty_success(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _, _, results, _ = asyncio.run(
        session_calls(
            tmp_path,
            path,
            "2025-11-25",
            100000,
            [
                ("graph_query", {"schema_version": "atlas.graph.v1", "op": "status"}),
                (
                    "graph_query",
                    {
                        "schema_version": "atlas.graph.v1",
                        "op": "path",
                        "seed": {"name": "A"},
                        "target": {"name": "B"},
                    },
                ),
            ],
        )
    )
    assert not results[0].isError and results[0].structuredContent["generation_id"] is None
    assert results[1].isError and results[1].structuredContent["operation"] == "path"
    assert results[1].structuredContent["error"]["code"] == "INDEX_UNAVAILABLE"
    assert not (path / ".atlas").exists()
