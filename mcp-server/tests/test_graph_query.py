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
