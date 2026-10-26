"""Real-parser bounded BFS anchors, independent of protocol presentation."""

import asyncio

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.query.traversal import navigate


@pytest.fixture
def graph(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def A():\n B(); B()\ndef B():\n C()\ndef C():\n A()\n")
    graph = CodeGraph(repo)
    graph.build()
    yield graph
    graph.close()


def run(graph, **options):
    with graph.reader_lease() as reader:
        return asyncio.run(navigate(reader, seed={"name": "A"}, deadline_ms=2000, **options))


def test_parallel_direction_and_canonical_proof(graph):
    result = run(graph, max_depth=1)
    assert result["stop_reason"] == "depth_limit"
    assert len(result["edges"]) == 2
    assert len({e["assertion"]["id"] for e in result["edges"]}) == 2
    for edge in result["edges"]:
        assert edge["assertion"]["evidence_id"] == edge["evidence"]["id"]
        assert edge["evidence"]["occurrence_id"] == edge["occurrence"]["id"]
        assert edge["evidence"]["producer_id"] == edge["producer"]["id"]
        assert edge["occurrence"]["source_sha256"] == edge["evidence"]["source_sha256"]
    incoming = run(graph, direction="incoming", max_depth=1)
    assert len(incoming["edges"]) == 1
    assert incoming["edges"][0]["assertion"]["target_entity_id"] == incoming["nodes"][0]["id"]
    assert incoming["edges"][0]["traversal_direction"] == "incoming"


def test_shortest_path_whole_proof(graph):
    result = run(graph, operation="path", target={"name": "C"}, max_depth=5)
    assert result["outcome"] == "complete"
    assert [n["name"] for n in result["path"]["nodes"]] == ["A", "B", "C"]
    assert len(result["path"]["hops"]) == 2
    assert result["work"]["examined_edges"] == 3


def test_visit_budget_before_discovery(graph):
    result = run(graph, max_visited_nodes=1)
    assert result["stop_reason"] == "visited_node_limit"
    assert result["work"]["visited_nodes"] == 1
    assert len(result["nodes"]) == 1 and result["edges"] == []


def test_depth_zero_never_reads_adjacency(graph):
    result = run(graph, max_depth=0)
    assert result["stop_reason"] == "depth_limit"
    assert result["work"]["edge_queries"] == 0


def test_edge_cap_no_lookahead_and_replay(graph):
    first = run(graph, max_examined_edges=1)
    assert first["stop_reason"] == "examined_edge_limit"
    assert first["work"]["examined_edges"] == first["work"]["edge_queries"] == 1
    assert len(first["edges"]) == 1
    assert first == run(graph, max_examined_edges=1)


def test_whole_path_admission(graph):
    result = run(
        graph,
        operation="path",
        target={"name": "C"},
        max_depth=5,
        admit=lambda payload: payload["path"] is None,
    )
    assert result["outcome"] == "partial" and result["stop_reason"] == "byte_limit"
    assert result["path"] is None


def test_ambiguous_seed_no_fanout(graph):
    (graph.repo / "b.py").write_text("def A():\n pass\n")
    graph.build()
    result = run(graph)
    assert result["outcome"] == "disambiguation"
    assert result["seed"]["state"] == "ambiguous"
    assert len(result["seed"]["candidates"]) == 2
    assert result["seed"]["candidates_complete"] is False
    assert result["work"]["visited_nodes"] == result["work"]["edge_queries"] == 0
