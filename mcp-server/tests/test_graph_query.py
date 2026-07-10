"""Tests for the A1 graph_query DSL surface: real `subclasses_of` edges
(AC-A1-7) and the F10 response-shape change — caller context from the
materialized edge's source endpoint, replacing `refs.enclosing` (AC-A1-10).

Also covers two coordinator-review findings, end to end through
`dispatch_tool_call` (not just the `CodeGraph` method level covered in
test_confidence.py): `callers_of` on a local class resolving `construct`
edges (BLOCKER — a whole symbol *kind* was previously excluded from
resolution), and the `unresolved_refs` field distinguishing a genuinely
empty answer from an incomplete one (MAJOR).
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


# ---- AC-A1-7 — subclasses_of returns real edges, not the empty stub ----


async def test_subclasses_of_returns_real_edges(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    # Ruby's `module A::B` compact namespaced form parses its name as a
    # `scope_resolution` node, which the (pre-A1, unchanged) `def.module`
    # query only captures for a plain `constant` — a real but separate
    # symbol-extraction gap, not an A1 edge-resolution concern, so the
    # mixin here uses a plain (non-namespaced) module name.
    _write(
        repo,
        "app/models.rb",
        "class ApplicationRepository\nend\n"
        "module Auditable\nend\n"
        "class VoteRepository < ApplicationRepository\n"
        "  include Auditable\n"
        "end\n"
        "class BallotRepository < ApplicationRepository\nend\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "subclasses_of:ApplicationRepository"},
        config,
        enforcement,
        memex,
        code_graph,
    )

    assert "warning" not in result, "the MVP empty-stub-with-warning must be retired"
    edges = result["edges"]
    subclass_names = {e["source"]["name"] for e in edges}
    assert subclass_names == {"VoteRepository", "BallotRepository"}
    assert all(e["relation"] == "superclass" for e in edges)
    assert all(e["confidence"] == "EXTRACTED" for e in edges)

    # The mixin relation is queried through the same verb (D3a: Rails
    # engines lean on concerns/ mixins as heavily as superclass chains).
    mixin_result = await dispatch_tool_call(
        "graph_query",
        {"query": "subclasses_of:Auditable"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    mixin_edges = mixin_result["edges"]
    assert len(mixin_edges) == 1
    assert mixin_edges[0]["relation"] == "include"
    assert mixin_edges[0]["source"]["name"] == "VoteRepository"


async def test_subclasses_of_unknown_class_returns_empty_edges(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)
    _write(repo, "app/a.rb", "class A\nend\n")
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "subclasses_of:NoSuchClass"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    assert result["edges"] == []


# ---- AC-A1-10 — callers_of carries caller context from the edge source ----


async def test_callers_of_caller_context_from_edge_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(repo, "app/target.rb", "class Target\n  def record_vote\n  end\nend\n")
    _write(
        repo,
        "app/tallier.rb",
        "class Tallier\n  def call(ballot)\n    Target.record_vote\n  end\nend\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:record_vote"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    edges = result["edges"]
    assert len(edges) == 1
    edge = edges[0]
    # The new response shape (F10): caller context lives under `source`,
    # resolved from the edge's own materialized source endpoint — never the
    # old, always-null `enclosing` string.
    assert "enclosing" not in edge
    assert edge["source"] == {
        "path": "app/tallier.rb",
        "line": 3,
        "name": "call",
        "kind": "method",
    }
    assert edge["target"] == {
        "path": "app/target.rb",
        "line": 2,
        "name": "record_vote",
    }
    assert edge["confidence"] == "EXTRACTED"
    assert edge["candidates"] is None


async def test_callers_of_source_is_none_outside_any_known_symbol(tmp_path: Path) -> None:
    """A top-level call with no enclosing definition is a real fact — the
    source name/kind are None, not a crash and not a silently wrong guess."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(repo, "app/target.rb", "class Target\n  def act\n  end\nend\n")
    _write(repo, "app/script.rb", "Target.act\n")
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:act"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    edges = result["edges"]
    assert len(edges) == 1
    assert edges[0]["source"]["name"] is None
    assert edges[0]["source"]["kind"] is None
    assert edges[0]["source"]["path"] == "app/script.rb"


# ---- AC-A1-5 / R-1 — AMBIGUOUS edges are returned through graph_query too ----


async def test_callers_of_returns_ambiguous_edges_with_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(repo, "app/a.rb", "class A\n  def dup_name\n  end\nend\n")
    _write(repo, "app/b.rb", "class B\n  def dup_name\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    dup_name()\n  end\nend\n")
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:dup_name"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    edges = result["edges"]
    assert len(edges) == 1
    assert edges[0]["confidence"] == "AMBIGUOUS"
    assert edges[0]["target"] is None
    assert edges[0]["candidates"] == [
        {"path": "app/a.rb", "line": 2, "name": "dup_name"},
        {"path": "app/b.rb", "line": 2, "name": "dup_name"},
    ]


async def test_candidates_subfield_bounded_end_to_end(tmp_path: Path) -> None:
    """R-1, end to end: a common name resolving to more candidates than the
    (small, test-configured) central element cap must still come back
    truncated-and-flagged on the `edges[].candidates` sub-field, exactly
    like any other bounded field — never silently whole."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cap = 3
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_bound_field_elements=cap)
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    for i in range(cap + 2):  # cap + 2 same-named definitions -> AMBIGUOUS
        _write(repo, f"app/dup_{i}.rb", f"class Dup{i}\n  def shared_name\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    shared_name()\n  end\nend\n")
    code_graph = CodeGraph(repo=repo, query_limit=config.max_bound_field_elements + 1)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:shared_name"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    edges = result["edges"]
    assert len(edges) == 1
    assert len(edges[0]["candidates"]) == cap
    assert result["truncated"] is True
    assert "edges.candidates" in result["truncated_fields"]
    assert result["more_available"] is True
    assert result["retry_hint"] == "narrower_scope"


# ---- Coordinator finding, BLOCKER — callers_of on a local class ----


async def test_callers_of_on_local_class_resolves_construct_edges_end_to_end(
    tmp_path: Path,
) -> None:
    """The exact bug: `callers_of:CodeGraph`-shaped query on a repo with a
    local class and real constructor call sites came back an empty,
    unflagged `edges: []` — indistinguishable from "never constructed".
    Verified end to end through `dispatch_tool_call`, not just the
    `CodeGraph` method (test_confidence.py already covers that level)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(repo, "app/target.py", "class Target:\n    def __init__(self):\n        pass\n")
    _write(
        repo,
        "app/hub.py",
        "class Hub:\n    def call(self):\n        Target()\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:Target"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    edges = result["edges"]
    assert len(edges) == 1, "the constructor call site must not be silently absent"
    assert edges[0]["relation"] == "construct"
    assert edges[0]["confidence"] == "EXTRACTED"
    assert result["unresolved_refs"] == 0


# ---- Coordinator finding, MAJOR — unresolved_refs distinguishes empty vs incomplete ----


async def test_unresolved_refs_field_present_end_to_end(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    external_gem_method()\n    external_gem_method()\n  end\nend\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    # Genuinely nothing to find at all: zero refs, zero edges.
    nothing = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:absolutely_nothing_like_this_anywhere"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    assert nothing["edges"] == []
    assert nothing["unresolved_refs"] == 0

    # Refs exist, but the callee has no local definition: an incomplete
    # answer, not an empty one — the count is what tells them apart.
    incomplete = await dispatch_tool_call(
        "graph_query",
        {"query": "callers_of:external_gem_method"},
        config,
        enforcement,
        memex,
        code_graph,
    )
    assert incomplete["edges"] == []
    assert incomplete["unresolved_refs"] == 2


# ---- A2 — degree-centrality god nodes ----
#
# No clustering, no cluster/community detection of any kind — pure
# arithmetic over confident_edges()'s own output. Every fixture below is built so the
# *independently expected* degree counts are computed by hand in the test
# (never by calling into `god_nodes()`'s own arithmetic), per the lesson
# that AC-A1-6 once passed vacuously because the candidate count was
# computed over a symbol set that excluded the answer.


def _rb(repo: Path, rel: str, content: str) -> None:
    _write(repo, rel, content)


async def test_god_nodes_response_shape_end_to_end(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _rb(repo, "app/enforcement.rb", "class Enforcement\n  def enforce\n  end\nend\n")
    _rb(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    Enforcement.enforce\n    Enforcement.enforce\n  end\nend\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query", {"query": "god_nodes:"}, config, enforcement, memex, code_graph
    )
    assert result["analysis_basis"] == "confident_edges"
    assert result["resolved_edge_count"] == 2
    assert result["ambiguous_edges_excluded"] == 0
    nodes = result["god_nodes"]
    assert len(nodes) == 2
    enforce_node = next(n for n in nodes if n["name"] == "enforce")
    assert enforce_node["in_degree"] == 2
    assert enforce_node["out_degree"] == 0
    assert enforce_node["degree"] == 2
    assert enforce_node["kind"] == "method"
    call_node = next(n for n in nodes if n["name"] == "call")
    assert call_node["out_degree"] == 2
    assert call_node["in_degree"] == 0
    # Ranked by degree DESC — both nodes tie at degree 2 here, broken by
    # (path, line, name); enforcement.rb sorts before hub.rb.
    assert nodes[0]["path"] == "app/enforcement.rb"


# ---- AC-A2-1 — deterministic total order, INCLUDING at the tail (ties) ----


def test_god_nodes_degree_centrality_deterministic(tmp_path: Path) -> None:
    """Ties at the same degree are the norm, not the exception (checker
    finding) — this fixture deliberately creates three same-degree nodes,
    written to disk in an order that does NOT match the expected tiebreak
    (mirroring AC-A1-8's z_last/a_first trick), and indexes the identical
    source from scratch twice, diffing the FULL ranking (not just the top
    N) for byte-for-byte identity."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Three distinct methods, each called exactly once -> degree 1 each,
    # a genuine three-way tie. Files written in an order that would
    # surface a rowid/insertion-order bug immediately.
    _rb(repo, "app/z_target.rb", "class ZTarget\n  def z_method\n  end\nend\n")
    _rb(repo, "app/a_target.rb", "class ATarget\n  def a_method\n  end\nend\n")
    _rb(repo, "app/m_target.rb", "class MTarget\n  def m_method\n  end\nend\n")
    _rb(
        repo,
        "app/hub.rb",
        "class Hub\n"
        "  def call\n"
        "    ZTarget.z_method\n"
        "    ATarget.a_method\n"
        "    MTarget.m_method\n"
        "  end\n"
        "end\n",
    )

    graph1 = CodeGraph(repo=repo)
    graph1.build()
    result1 = graph1.god_nodes()

    graph2 = CodeGraph(repo=repo)
    graph2.build()
    result2 = graph2.god_nodes()

    assert result1["god_nodes"] == result2["god_nodes"], (
        "the full ranking (not just the top entry) must be byte-identical "
        "across independent rebuilds of identical source"
    )
    # Independently computed expectation: the three degree-1 targets sort
    # by (path, line, name) ASC among themselves — a_target before
    # m_target before z_target — regardless of disk-write order.
    target_names = [n["name"] for n in result1["god_nodes"] if n["name"] != "call"]
    assert target_names == ["a_method", "m_method", "z_method"]


# ---- AC-A2-2 — no graph-algorithm runtime dependency ----


def test_god_nodes_uses_only_edge_counts() -> None:
    import re

    src_path = Path(__file__).resolve().parent.parent / "src" / "atlas_aci" / "codegraph.py"
    source = src_path.read_text()
    assert not re.search(r"\bimport networkx\b|\bfrom networkx\b", source)
    assert not re.search(r"\bimport igraph\b|\bfrom igraph\b", source)
    assert not re.search(r"\bimport graspologic\b|\bfrom graspologic\b", source)


async def test_god_nodes_uses_only_edge_counts_end_to_end(tmp_path: Path) -> None:
    """Not just a grep — confirms the actual computation is arithmetic over
    `edges` counts by cross-checking against independently-computed totals
    (a direct `SELECT COUNT(*)` per node), not a call into `god_nodes()`'s
    own logic."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _rb(repo, "app/target.rb", "class Target\n  def act\n  end\nend\n")
    _rb(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    Target.act\n    Target.act\n    Target.act\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    expected_in_degree = graph.db.execute(
        "SELECT COUNT(*) FROM edges WHERE target_name = 'act' "
        "AND confidence IN ('EXTRACTED', 'INFERRED')"
    ).fetchone()[0]

    result = graph.god_nodes()
    act_node = next(n for n in result["god_nodes"] if n["name"] == "act")
    assert act_node["in_degree"] == expected_in_degree == 3


# ---- AC-A2-3 — analysis_basis / ambiguous_edges_excluded / resolved_edge_count ----


async def test_analysis_basis_fields_present(tmp_path: Path) -> None:
    """A fixture where all three numbers DIFFER from each other and from
    the trivial 0-or-all cases — a fixture where they coincide proves
    nothing (checker instruction). Total edges in this fixture: 3 resolved
    (confident) + 2 ambiguous = 5; `ambiguous_edges_excluded` (2) and
    `resolved_edge_count` (3) must each be their own distinct number, not a
    stand-in for "all" or "none"."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    # Two confident (unique) targets, called 3 times combined.
    _rb(repo, "app/target.rb", "class Target\n  def act\n  end\nend\n")
    _rb(repo, "app/other.rb", "class Other\n  def go\n  end\nend\n")
    # One ambiguous target (two candidates), called twice.
    _rb(repo, "app/dup_a.rb", "class DupA\n  def dup_name\n  end\nend\n")
    _rb(repo, "app/dup_b.rb", "class DupB\n  def dup_name\n  end\nend\n")
    _rb(
        repo,
        "app/hub.rb",
        "class Hub\n"
        "  def call\n"
        "    Target.act\n"
        "    Target.act\n"
        "    Other.go\n"
        "    dup_name()\n"
        "    dup_name()\n"
        "  end\n"
        "end\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    # Independently computed expectations, straight from the edges table.
    expected_resolved = code_graph.db.execute(
        "SELECT COUNT(*) FROM edges WHERE confidence IN ('EXTRACTED', 'INFERRED')"
    ).fetchone()[0]
    expected_ambiguous = code_graph.db.execute(
        "SELECT COUNT(*) FROM edges WHERE confidence = 'AMBIGUOUS'"
    ).fetchone()[0]
    assert expected_resolved == 3
    assert expected_ambiguous == 2
    assert expected_resolved != expected_ambiguous  # non-degenerate fixture

    result = await dispatch_tool_call(
        "graph_query", {"query": "god_nodes:"}, config, enforcement, memex, code_graph
    )
    assert result["analysis_basis"] == "confident_edges"
    assert result["resolved_edge_count"] == expected_resolved == 3
    assert result["ambiguous_edges_excluded"] == expected_ambiguous == 2
    # The ambiguous target must not appear anywhere in the ranking.
    assert not any(n["name"] == "dup_name" for n in result["god_nodes"])


# ---- AC-NEG-7 — structurally impossible for AMBIGUOUS to leak into degree ----


async def test_god_nodes_input_is_exactly_confident_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strongest form of AC-NEG-7 for god_nodes: not merely "the
    numbers came out right this time" but "there is no OTHER path into the
    ranking." Monkeypatches `confident_edges` to return an empty list and
    asserts the ranking is empty too, proving `god_nodes()` has no
    independent route to `edges` for degree purposes — only through
    `confident_edges()`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _rb(repo, "app/target.rb", "class Target\n  def act\n  end\nend\n")
    _rb(repo, "app/hub.rb", "class Hub\n  def call\n    Target.act\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    # Sanity: without the patch, there IS a ranking.
    assert graph.god_nodes()["god_nodes"], "fixture must produce a non-empty ranking normally"

    monkeypatch.setattr(graph, "confident_edges", lambda: [])
    patched = graph.god_nodes()
    assert patched["god_nodes"] == []
    assert patched["resolved_edge_count"] == 0
    # ambiguous_edges_excluded is independent of confident_edges() (it
    # counts the full `edges` table directly) and must be unaffected by
    # the patch — this fixture has zero AMBIGUOUS edges either way.
    assert patched["ambiguous_edges_excluded"] == 0


def test_ambiguous_edges_never_contribute_to_god_node_degree(tmp_path: Path) -> None:
    """AC-NEG-7, direct form: a name with 2+ candidates (AMBIGUOUS, no
    fan-out) must contribute ZERO degree anywhere — not to the ambiguous
    name's own (nonexistent) node, and not fractionally split across its
    candidates."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _rb(repo, "app/dup_a.rb", "class DupA\n  def dup_name\n  end\nend\n")
    _rb(repo, "app/dup_b.rb", "class DupB\n  def dup_name\n  end\nend\n")
    _rb(repo, "app/hub.rb", "class Hub\n  def call\n    dup_name()\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    result = graph.god_nodes()
    # No node for the ambiguous callee itself, and neither DupA nor DupB
    # picked up any fractional/fanned-out in_degree from it.
    names = {n["name"] for n in result["god_nodes"]}
    assert "dup_name" not in names
    assert not names & {"DupA", "DupB"}
    assert result["god_nodes"] == [], "the only edge in this fixture is AMBIGUOUS"


def test_god_nodes_out_degree_double_guarded_against_target_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checker MINOR-1: `god_nodes()`'s out-degree branch used to run
    unconditionally, even for an edge shaped like an AMBIGUOUS row
    (`target is None`) — safe in production only because
    `confident_edges()`'s own SQL filter never actually produces such a
    row, not because `god_nodes()` itself refused one. This monkeypatches
    `confident_edges()` to return exactly one AMBIGUOUS-shaped edge (a
    resolvable source, `target=None`) and asserts BOTH `in_degree` and
    `out_degree` stay at zero everywhere — the same double-barrier
    `communities()` already has (`if target is None: continue`, guarding
    the whole edge, not just the in-degree half)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _rb(repo, "app/hub.rb", "class Hub\n  def call\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    # Sanity: this fixture has zero real confident edges (nothing calls
    # `call`) — any degree seen below can only come from the monkeypatch.
    assert graph.confident_edges() == []

    fake_source = {"path": "app/hub.rb", "line": 2, "name": "call", "kind": "method"}
    monkeypatch.setattr(graph, "confident_edges", lambda: [{"source": fake_source, "target": None}])
    result = graph.god_nodes()
    assert result["god_nodes"] == [], (
        "an edge with target=None must contribute zero degree anywhere — "
        "not even out_degree via a resolvable source"
    )
