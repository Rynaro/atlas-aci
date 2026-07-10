"""Tests for A3 — deterministic hand-rolled label propagation over the
confident subgraph (D3/D4a). No clustering library, no graph-algorithm
runtime dependency (AC-A3-3) — pure Python arithmetic over
`confident_edges()`'s own output, the same structural AC-NEG-7 guarantee
`god_nodes()` already has.

Every expected value below is computed independently of `communities()`'s
own arithmetic — a fresh fixture/assertion, not a call into the code under
test — per the boundary-testing lesson this campaign keeps re-learning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement
from atlas_aci.memex import Memex
from atlas_aci.server import dispatch_tool_call


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ---- AC-A3-3 — no new runtime dependency ----


def test_no_new_dependency() -> None:
    import re

    src_path = Path(__file__).resolve().parent.parent / "src" / "atlas_aci" / "codegraph.py"
    source = src_path.read_text()
    assert not re.search(r"\bimport networkx\b|\bfrom networkx\b", source)
    assert not re.search(r"\bimport igraph\b|\bfrom igraph\b", source)
    assert not re.search(r"\bimport graspologic\b|\bfrom graspologic\b", source)


def test_communities_input_is_exactly_confident_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same structural AC-NEG-7 guarantee as `god_nodes()`: monkeypatch
    `confident_edges` to return `[]` and confirm `communities()` follows to
    empty — there is no OTHER route into `edges` for community membership."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/a.rb", "class A\n  def a1\n    a2()\n  end\n  def a2\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    assert graph.communities()["communities"], "fixture must produce output normally"

    monkeypatch.setattr(graph, "confident_edges", lambda: [])
    patched = graph.communities()
    assert patched["communities"] == []
    assert patched["community_count"] == 0
    assert patched["resolved_edge_count"] == 0


# ---- AC-NEG-7 — AMBIGUOUS is algorithmically forced out, not merely filtered ----


def test_ambiguous_edges_never_join_a_community(tmp_path: Path) -> None:
    """An AMBIGUOUS edge has no single target — there is no node to draw an
    undirected connection to in the first place (D4a: algorithmically
    forced, not a choice). The two ambiguous candidates must not be
    fused into a community through the ambiguous reference either."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/dup_a.rb", "class DupA\n  def dup_name\n  end\nend\n")
    _write(repo, "app/dup_b.rb", "class DupB\n  def dup_name\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    dup_name()\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    result = graph.communities()
    names = {m["name"] for m in result["communities"]}
    assert "dup_name" not in names
    assert not names & {"DupA", "DupB"}
    assert result["communities"] == [], "the only edge in this fixture is AMBIGUOUS"


# ---- Genuine separation: two disconnected clusters must NOT be fused ----


def test_two_disconnected_clusters_get_distinct_community_ids(tmp_path: Path) -> None:
    """A meaningful (non-vacuous) proof that LPA actually separates
    structure, not merely that it runs: two internally-tight, mutually
    disconnected clusters must land in two different communities."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/cluster_a.rb",
        "class ClusterA\n"
        "  def a1\n    a2()\n    a3()\n  end\n"
        "  def a2\n    a3()\n  end\n"
        "  def a3\n    a1()\n  end\n"
        "end\n",
    )
    _write(
        repo,
        "app/cluster_b.rb",
        "class ClusterB\n"
        "  def b1\n    b2()\n    b3()\n  end\n"
        "  def b2\n    b3()\n  end\n"
        "  def b3\n    b1()\n  end\n"
        "end\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    result = graph.communities()
    assert result["community_count"] == 2
    ids = {m["name"]: m["community_id"] for m in result["communities"]}
    assert ids["a1"] == ids["a2"] == ids["a3"]
    assert ids["b1"] == ids["b2"] == ids["b3"]
    assert ids["a1"] != ids["b1"]


# ---- AC-A3-2 — deterministic, total-ordered community IDs ----


def test_lpa_deterministic_total_order(tmp_path: Path) -> None:
    """Indexes the identical source from scratch twice — including
    `PYTHONHASHSEED`-sensitive scenarios are covered separately by manual
    cross-process verification; this test pins same-machine, same-process
    determinism the way AC-A1-8/AC-A2-1's siblings do — and diffs the FULL
    community membership list (every field, in order), not just the
    community count."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/cluster_a.rb",
        "class ClusterA\n"
        "  def a1\n    a2()\n    a3()\n  end\n"
        "  def a2\n    a3()\n  end\n"
        "  def a3\n    a1()\n  end\n"
        "end\n",
    )
    _write(
        repo,
        "app/cluster_b.rb",
        "class ClusterB\n"
        "  def b1\n    b2()\n    b3()\n  end\n"
        "  def b2\n    b3()\n  end\n"
        "  def b3\n    b1()\n  end\n"
        "end\n",
    )
    _write(repo, "app/bridge.rb", "class Bridge\n  def call\n    a1()\n    b1()\n  end\nend\n")

    graph1 = CodeGraph(repo=repo)
    graph1.build()
    result1 = graph1.communities()

    graph2 = CodeGraph(repo=repo)
    graph2.build()
    result2 = graph2.communities()

    assert result1["communities"] == result2["communities"], (
        "the full community membership list must be byte-identical across "
        "independent rebuilds of identical source"
    )
    assert result1["community_count"] == result2["community_count"]


# ---- AC-A2-3 (communities half) ----


async def test_analysis_basis_fields_present_for_communities(tmp_path: Path) -> None:
    """A fixture where resolved/ambiguous/total all differ meaningfully —
    a fixture where they coincide proves nothing (checker instruction),
    same discipline as god_nodes' equivalent test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(
        repo,
        "app/cluster_a.rb",
        "class ClusterA\n"
        "  def a1\n    a2()\n    a3()\n  end\n"
        "  def a2\n    a3()\n  end\n"
        "  def a3\n  end\nend\n",
    )
    _write(repo, "app/dup_a.rb", "class DupA\n  def dup_name\n  end\nend\n")
    _write(repo, "app/dup_b.rb", "class DupB\n  def dup_name\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    dup_name()\n  end\nend\n")
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    expected_resolved = code_graph.db.execute(
        "SELECT COUNT(*) FROM edges WHERE confidence IN ('EXTRACTED', 'INFERRED')"
    ).fetchone()[0]
    expected_ambiguous = code_graph.db.execute(
        "SELECT COUNT(*) FROM edges WHERE confidence = 'AMBIGUOUS'"
    ).fetchone()[0]
    assert expected_resolved == 3
    assert expected_ambiguous == 1
    assert expected_resolved != expected_ambiguous

    result = await dispatch_tool_call(
        "graph_query", {"query": "communities:"}, config, enforcement, memex, code_graph
    )
    assert result["analysis_basis"] == "confident_edges"
    assert result["resolved_edge_count"] == expected_resolved == 3
    assert result["ambiguous_edges_excluded"] == expected_ambiguous == 1
    assert not any(m["name"] == "dup_name" for m in result["communities"])


async def test_communities_verb_end_to_end(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(
        repo,
        "app/cluster_a.rb",
        "class ClusterA\n  def a1\n    a2()\n  end\n  def a2\n  end\nend\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query", {"query": "communities:"}, config, enforcement, memex, code_graph
    )
    assert result["community_count"] == 1
    names = {m["name"] for m in result["communities"]}
    assert names == {"a1", "a2"}
