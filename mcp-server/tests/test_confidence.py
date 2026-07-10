"""Tests for the A1 deterministic confidence enum (D4) over the materialized
call/inheritance edge table.

Every test computes its expected candidate/confidence outcome independently
of `_resolve_edges`'s own arithmetic — a fresh count in the test, not a call
into the module under test — per the boundary-testing lesson the P0 pass
learned four times (five separate bugs each passed their own tests because
each test inherited the implementation's own arithmetic).
"""

from __future__ import annotations

import json
from pathlib import Path

from atlas_aci.codegraph import CodeGraph


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _edges(graph: CodeGraph, **where: str) -> list[dict]:
    clauses = " AND ".join(f"{k} = ?" for k in where)
    sql = "SELECT * FROM edges"
    if clauses:
        sql += f" WHERE {clauses}"
    rows = graph.db.execute(sql, tuple(where.values())).fetchall()
    return [dict(r) for r in rows]


# ---- AC-A1-2 — every call/inheritance edge's confidence is in the closed enum ----


def test_every_call_inheritance_edge_confidence_in_closed_enum(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    # One EXTRACTED (qualified, unique), one INFERRED (bare, unique), one
    # AMBIGUOUS (two same-named candidates), one heritage EXTRACTED.
    _write(repo, "app/target.rb", "class Target\n  def bar\n  end\nend\n")
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    Target.bar\n    bar()\n  end\nend\n",
    )
    _write(repo, "app/a.rb", "class A\n  def dup_name\n  end\nend\n")
    _write(repo, "app/b.rb", "class B\n  def dup_name\n  end\nend\n")
    _write(repo, "app/caller.rb", "class Caller\n  def call\n    dup_name()\n  end\nend\n")
    _write(repo, "app/base.rb", "class Base\nend\nclass Child < Base\nend\n")

    graph = CodeGraph(repo=repo)
    graph.build()

    rows = graph.db.execute("SELECT confidence FROM edges").fetchall()
    assert rows, "expected at least one materialized edge"
    confidences = {r["confidence"] for r in rows}
    assert confidences <= {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
    # All three values are actually exercised by this fixture, not just a subset.
    assert confidences == {"EXTRACTED", "INFERRED", "AMBIGUOUS"}


# ---- Coordinator MINOR-3 (condition 4) — edges.relation is a closed set too ----


def test_edge_relation_is_a_closed_set(tmp_path: Path) -> None:
    """`confidence` has a test-enforced closed set (AC-A1-2); `relation` had
    no equivalent guard — a future `@heritage.<newrelation>` capture or a
    new `_resolve_edges` branch could silently introduce a fourth+ value.
    `edges.relation` has no DDL CHECK constraint, matching `confidence`'s
    own test-only pinning (not a new asymmetry, a deliberate consistency
    choice — see `KNOWN_EDGE_RELATIONS`'s docstring for why this doesn't
    bump SCHEMA_EPOCH). Exercises every relation value the resolver can
    currently produce so the assertion is non-vacuous."""
    from atlas_aci.codegraph import KNOWN_EDGE_RELATIONS

    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/models.rb",
        "class ApplicationRepository\nend\n"
        "module Auditable\nend\n"
        "module Sluggable\nend\n"
        "module Timestamped\nend\n"
        "class VoteRepository < ApplicationRepository\n"
        "  include Auditable\n"
        "  extend Sluggable\n"
        "  prepend Timestamped\n"
        "  def call\n"
        "    unique_call_target()\n"
        "    ApplicationRepository.new\n"
        "  end\n"
        "end\n"
        "class UniqueCallTarget\n"
        "  def unique_call_target\n"
        "  end\n"
        "end\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    relations = {r["relation"] for r in graph.db.execute("SELECT DISTINCT relation FROM edges")}
    assert relations, "expected at least one edge"
    assert relations <= KNOWN_EDGE_RELATIONS, (
        f"relation(s) {relations - KNOWN_EDGE_RELATIONS} are produced but not in the "
        f"closed set — a new relation value must be added to KNOWN_EDGE_RELATIONS deliberately"
    )
    # Non-vacuous: every currently-producible relation is actually
    # exercised here, not just a trivial subset of the closed set.
    assert relations == {"call", "construct", "superclass", "include", "extend", "prepend"}


# ---- AC-A1-3 / AC-A1-11 — single type-qualified candidate -> EXTRACTED ----


def test_single_type_qualified_is_extracted(tmp_path: Path) -> None:
    """Ruby `Foo.bar` where `Foo` is a constant receiver and `bar` resolves
    to exactly one definition anywhere in the repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.rb", "class Target\n  def unique_name\n  end\nend\n")
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    Target.unique_name\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = _edges(graph, callee_name="unique_name")
    assert len(edges) == 1
    assert edges[0]["confidence"] == "EXTRACTED"
    assert edges[0]["target_name"] == "unique_name"
    assert edges[0]["target_path"] == "app/target.rb"


# ---- AC-A1-4 — single heuristic (non-qualified) candidate -> INFERRED ----


def test_single_heuristic_is_inferred(tmp_path: Path) -> None:
    """Ruby `self.bar` / bare `bar()` — no constant receiver — resolving to
    exactly one definition."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.rb", "class Target\n  def unique_name\n  end\nend\n")
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    self.unique_name\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = _edges(graph, callee_name="unique_name")
    assert len(edges) == 1
    assert edges[0]["confidence"] == "INFERRED"
    assert edges[0]["target_name"] == "unique_name"


# ---- AC-A1-5 — multi-candidate -> AMBIGUOUS with full ordered candidates[] ----


def test_multi_candidate_is_ambiguous_with_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/a.rb", "class A\n  def dup_name\n  end\nend\n")
    _write(repo, "app/b.rb", "class B\n  def dup_name\n  end\nend\n")
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    dup_name()\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = _edges(graph, callee_name="dup_name")
    assert len(edges) == 1
    edge = edges[0]
    assert edge["confidence"] == "AMBIGUOUS"
    assert edge["target_path"] is None
    assert edge["target_line"] is None
    assert edge["target_name"] is None
    candidates = json.loads(edge["candidates"])
    # Computed independently: exactly the two `dup_name` definitions, never
    # dropped (unlike graphify's silent guard).
    assert candidates == [
        {"path": "app/a.rb", "line": 2, "name": "dup_name"},
        {"path": "app/b.rb", "line": 2, "name": "dup_name"},
    ]


# ---- AC-A1-6 — zero candidates -> no edge, ever ----


def test_zero_candidates_emits_no_edge(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    totally_undefined_anywhere()\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    assert _edges(graph, callee_name="totally_undefined_anywhere") == []
    # The reference itself is preserved, unresolved — G-B's name-string model.
    ref = graph.db.execute(
        "SELECT callee_name FROM refs WHERE callee_name = ?",
        ("totally_undefined_anywhere",),
    ).fetchone()
    assert ref is not None


# ---- AC-A1-8 — candidates[] total order is fixed for identical input ----


def test_candidates_total_order_stable(tmp_path: Path) -> None:
    """Files are written in an order that does NOT match the expected sort
    order (z_last first on disk, a_first last) — a rowid/insertion-order bug
    would surface immediately. Expected order computed independently here:
    plain Python `sorted()` over (path, line, name), matching D6's `ORDER BY
    path, line, name` convention."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/z_last.rb", "class ZLast\n  def bar\n  end\nend\n")
    _write(repo, "app/a_first.rb", "class AFirst\n  def bar\n  end\nend\n")
    _write(repo, "app/m_mid.rb", "class MMid\n  def bar\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    bar()\n  end\nend\n")

    expected = sorted(
        [
            {"path": "app/z_last.rb", "line": 2, "name": "bar"},
            {"path": "app/a_first.rb", "line": 2, "name": "bar"},
            {"path": "app/m_mid.rb", "line": 2, "name": "bar"},
        ],
        key=lambda c: (c["path"], c["line"], c["name"]),
    )

    graph = CodeGraph(repo=repo)
    graph.build()
    edge = _edges(graph, callee_name="bar")[0]
    assert json.loads(edge["candidates"]) == expected

    # Rebuild from scratch (fresh DB, same source) — identical order again.
    graph2 = CodeGraph(repo=repo)
    graph2.build()
    edge2 = _edges(graph2, callee_name="bar")[0]
    assert json.loads(edge2["candidates"]) == expected


# ---- Coordinator MINOR-4 — the edge ENUMERATION is a total order too ----
#
# AC-A1-8 only pins candidates[]'s order; the *edge list itself* (returned
# by callers_of/subclasses_of/confident_edges) is a separate enumeration
# with its own tie hazard: two references to the SAME callee on the SAME
# source line (`foo(foo())`) previously tied on every ORDER BY column and
# fell back to SQLite's rowid/insertion order — same-machine deterministic,
# so this never failed a test, but not a genuine total order (exactly what
# D6's cross-OS byte-determinism gate would have discovered last).


def test_edge_enumeration_is_a_total_order_not_just_candidates(tmp_path: Path) -> None:
    """`foo(foo())`: two edges sharing callee_name, source_path, AND
    source_line — content-identical in every field except the reference
    site's own column. Indexes the SAME source from scratch twice and diffs
    the FULL edge enumeration (every field of every edge, in return order),
    not just `candidates[]` (which AC-A1-8 already covers)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.rb", "class Target\n  def foo(x)\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    foo(foo(1))\n  end\nend\n")

    graph = CodeGraph(repo=repo)
    graph.build()
    edges = graph.callers_of("foo")
    assert len(edges) == 2, "both the outer and inner foo(...) call sites must resolve"

    # Independently computed expectation: the reference site with the
    # SMALLER column comes first — a fact about the source text itself
    # (`foo(foo(1))`: outer call's callee token starts before the inner
    # one's), not a call into `_edges_for`'s own ordering logic.
    raw_cols = sorted(
        r["col"] for r in graph.db.execute("SELECT col FROM refs WHERE callee_name = 'foo'")
    )
    assert len(raw_cols) == 2 and raw_cols[0] < raw_cols[1], "fixture must genuinely tie/differ"

    # Same source, indexed from scratch again (fresh DB): the full edge
    # enumeration — every field, in order — must be byte-for-byte identical,
    # not merely "the same set in some order".
    graph2 = CodeGraph(repo=repo)
    graph2.build()
    edges2 = graph2.callers_of("foo")
    assert edges2 == edges, (
        "the full edge enumeration (not just candidates[]) must be a total "
        "order stable across independent rebuilds of identical source"
    )


# ---- AC-A1-11 — per-language type-qualified rule (F18) ----


def test_type_qualified_rule_per_language(tmp_path: Path) -> None:
    # Ruby: `Foo.bar` (constant receiver) = EXTRACTED; `obj.bar` (local/
    # identifier receiver) = INFERRED.
    ruby_repo = tmp_path / "ruby_repo"
    ruby_repo.mkdir()
    _write(ruby_repo, "app/target.rb", "class Target\n  def act\n  end\nend\n")
    _write(
        ruby_repo,
        "app/hub.rb",
        "class Hub\n  def call(obj)\n    Target.act\n  end\nend\n"
        "class Hub2\n  def call(obj)\n    obj.act\n  end\nend\n",
    )
    ruby_graph = CodeGraph(repo=ruby_repo)
    ruby_graph.build()
    # Both call sites live in the same file (hub.rb) at different lines —
    # disambiguate by source_line rather than path.
    ruby_by_line = {
        e["source_line"]: e["confidence"] for e in _edges(ruby_graph, callee_name="act")
    }
    assert ruby_by_line[3] == "EXTRACTED"  # Target.act
    assert ruby_by_line[8] == "INFERRED"  # obj.act

    # Python: `Foo.bar()` / `Foo()` where Foo is class-bound (capitalized) =
    # EXTRACTED; `self.bar()` / `obj.bar()` = INFERRED.
    py_repo = tmp_path / "py_repo"
    py_repo.mkdir()
    _write(py_repo, "app/target.py", "class Target:\n    def act(self):\n        pass\n")
    _write(
        py_repo,
        "app/hub.py",
        "class Hub:\n    def call(self, obj):\n        Target.act()\n"
        "class Hub2:\n    def call(self, obj):\n        self.act()\n",
    )
    py_graph = CodeGraph(repo=py_repo)
    py_graph.build()
    py_by_line = {e["source_line"]: e["confidence"] for e in _edges(py_graph, callee_name="act")}
    assert py_by_line[3] == "EXTRACTED"  # Target.act()
    assert py_by_line[6] == "INFERRED"  # self.act()

    # JS/TS: `Foo.bar()` / `new Foo()` where Foo is class-bound (capitalized)
    # = EXTRACTED; `this.bar()` / `obj.bar()` = INFERRED.
    js_repo = tmp_path / "js_repo"
    js_repo.mkdir()
    _write(js_repo, "app/target.js", "class Target {\n  act() {}\n}\n")
    _write(
        js_repo,
        "app/hub.js",
        "class Hub {\n  call() {\n    Target.act();\n  }\n}\n"
        "class Hub2 {\n  call() {\n    this.act();\n  }\n}\n",
    )
    js_graph = CodeGraph(repo=js_repo)
    js_graph.build()
    js_by_line = {e["source_line"]: e["confidence"] for e in _edges(js_graph, callee_name="act")}
    assert js_by_line[3] == "EXTRACTED"  # Target.act()
    assert js_by_line[8] == "INFERRED"  # this.act()


# ---- AC-A1-7 — real inheritance edges via QUERIES heritage capture ----


def test_python_and_jsts_heritage_captured(tmp_path: Path) -> None:
    """Confirms A1's QUERIES extension actually captures superclass/extends
    for Python and JS/TS too, not only Ruby (AC-A1-7 names all three)."""
    py_repo = tmp_path / "py_repo"
    py_repo.mkdir()
    _write(py_repo, "app/base.py", "class Base:\n    pass\n")
    _write(py_repo, "app/child.py", "class Child(Base):\n    pass\n")
    py_graph = CodeGraph(repo=py_repo)
    py_graph.build()
    py_super = _edges(py_graph, callee_name="Base", relation="superclass")
    assert len(py_super) == 1
    assert py_super[0]["confidence"] == "EXTRACTED"
    assert py_super[0]["source_name"] == "Child"

    ts_repo = tmp_path / "ts_repo"
    ts_repo.mkdir()
    _write(ts_repo, "app/base.ts", "class Base {}\n")
    _write(ts_repo, "app/child.ts", "class Child extends Base {}\n")
    ts_graph = CodeGraph(repo=ts_repo)
    ts_graph.build()
    ts_super = _edges(ts_graph, callee_name="Base", relation="superclass")
    assert len(ts_super) == 1
    assert ts_super[0]["confidence"] == "EXTRACTED"
    assert ts_super[0]["source_name"] == "Child"


# ---- AC-NEG-3 — AMBIGUOUS is produced only by the deterministic rule ----


def test_no_llm_import_in_codegraph_module() -> None:
    import re

    src_path = Path(__file__).resolve().parent.parent / "src" / "atlas_aci" / "codegraph.py"
    source = src_path.read_text()
    assert not re.search(r"\bimport anthropic\b|\bfrom anthropic\b", source)
    assert not re.search(r"\bimport openai\b|\bfrom openai\b", source)
    assert not re.search(r"\bimport boto3\b|\bfrom boto3\b", source)


def test_ambiguous_producer_is_deterministic_only(tmp_path: Path) -> None:
    """Two independent builds of the identical source tree must assign the
    identical confidence to the identical reference — a deterministic
    candidate-count rule, never a run-dependent (or LLM-dependent) output."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/a.rb", "class A\n  def dup_name\n  end\nend\n")
    _write(repo, "app/b.rb", "class B\n  def dup_name\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    dup_name()\n  end\nend\n")

    first = CodeGraph(repo=repo)
    first.build()
    first_confidence = _edges(first, callee_name="dup_name")[0]["confidence"]

    second = CodeGraph(repo=repo)
    second.build()
    second_confidence = _edges(second, callee_name="dup_name")[0]["confidence"]

    assert first_confidence == second_confidence == "AMBIGUOUS"


# ---- AC-NEG-7 — AMBIGUOUS excluded from the confident (analysis) subgraph ----


def test_ambiguous_excluded_from_analysis_graph(tmp_path: Path) -> None:
    """A1 owns the storage + retrieval primitive A2/A3 (not built yet) will
    filter their degree-centrality/community input through:
    `CodeGraph.confident_edges()`. AMBIGUOUS must never appear there — no
    fan-out to candidates, no fractional weight — while still being fully
    queryable (never dropped) via `callers_of`/`subclasses_of` (D4a)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.rb", "class Target\n  def unique_name\n  end\nend\n")
    _write(repo, "app/a.rb", "class A\n  def dup_name\n  end\nend\n")
    _write(repo, "app/b.rb", "class B\n  def dup_name\n  end\nend\n")
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    Target.unique_name\n    dup_name()\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    confident = graph.confident_edges()
    assert confident, "expected at least one confident edge"
    assert all(e["confidence"] in ("EXTRACTED", "INFERRED") for e in confident)
    assert not any(e["confidence"] == "AMBIGUOUS" for e in confident)
    # `dup_name` never resolves EXTRACTED/INFERRED in this fixture (it is
    # the two-candidate AMBIGUOUS case) — confirm it never leaks into the
    # confident subgraph's targets under any name.
    assert not any(e["target"] and e["target"]["name"] == "dup_name" for e in confident)

    # Still fully queryable via graph_query's own verbs — never dropped.
    ambiguous_via_callers_of = graph.callers_of("dup_name")
    assert len(ambiguous_via_callers_of) == 1
    assert ambiguous_via_callers_of[0]["confidence"] == "AMBIGUOUS"
    assert len(ambiguous_via_callers_of[0]["candidates"]) == 2


# ---- Coordinator findings (post-review): constructor resolution, ----
# ---- qualification-by-resolution, self/super, unresolved_refs.      ----
#
# BLOCKER: a bare `Foo(...)`/`new Foo()`/Ruby `Foo.new` constructor call
# whose name matches a local class previously resolved against zero
# candidates (candidate kinds were callable-only) — the entire symbol
# *kind* was silently excluded from the graph, not merely truncated.
# `callers_of:CodeGraph` returning `[]` while 51 real call sites exist is
# indistinguishable, from the response alone, from "CodeGraph is never
# constructed" — exactly what "never silently incomplete" forbids.
#
# Each test below is written from the invariant the coordinator named, not
# from `_resolve_edges`'s own arithmetic, and was confirmed to fail against
# the pre-fix `HEAD` (verified manually via `git stash`; not re-asserted
# here as an automated regression harness would require reverting the
# fixture code itself).


def test_bare_constructor_call_to_local_class_resolves(tmp_path: Path) -> None:
    """The exact shape of the coordinator's BLOCKER: a local class with
    call sites via a bare `Foo(...)` constructor must produce real,
    EXTRACTED `construct` edges — not silence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.py", "class Target:\n    def __init__(self):\n        pass\n")
    _write(
        repo,
        "app/hub.py",
        "class Hub:\n    def call(self):\n        Target()\n        Target()\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("Target")
    assert len(edges) == 2, "both constructor call sites must resolve, not zero"
    assert all(e["relation"] == "construct" for e in edges)
    assert all(e["confidence"] == "EXTRACTED" for e in edges)
    assert all(e["target"]["name"] == "Target" for e in edges)


def test_new_expression_constructor_call_resolves(tmp_path: Path) -> None:
    """JS/TS `new Foo()` — a distinct AST node type from a bare call —
    must resolve exactly like a bare `Foo()` constructor call."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.ts", "class Target {}\n")
    _write(repo, "app/hub.ts", "class Hub {\n  call(): void {\n    new Target();\n  }\n}\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("Target")
    assert len(edges) == 1
    assert edges[0]["relation"] == "construct"
    assert edges[0]["confidence"] == "EXTRACTED"


def test_ruby_dot_new_constructor_call_resolves(tmp_path: Path) -> None:
    """Ruby's constructor idiom is syntactically `Foo.new`, not `Foo(...)`
    — the callee node text is literally "new", not the class name, so this
    needs its own capture (QUERIES["ruby"]'s heritage.construct pattern),
    not just the candidate-kind widening that fixes Python/JS/TS."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.rb", "class Target\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    Target.new\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("Target")
    assert len(edges) == 1
    assert edges[0]["relation"] == "construct"
    assert edges[0]["confidence"] == "EXTRACTED"

    # A `.new` call on a NON-constant (local variable) receiver must NOT be
    # swept into this — Ruby's own grammar already tells constant apart
    # from identifier, and a `.new` call from a variable receiver isn't
    # syntactically qualified at all (no heritage.construct match for it).
    _write(
        repo,
        "app/hub2.rb",
        "class Hub2\n  def call(klass)\n    klass.new\n  end\nend\n",
    )
    graph2 = CodeGraph(repo=repo)
    graph2.build()
    # `klass.new` produces a *plain* call ref (callee_name="new"), which has
    # zero method/function/class/module candidates named "new" anywhere in
    # this fixture — no edge, not a false EXTRACTED/INFERRED.
    assert graph2.callers_of("new") == []


def test_module_qualified_constructor_call_resolves_extracted(tmp_path: Path) -> None:
    """`some_module.Target()` — a constructor reached through a
    module-qualified attribute chain, not a bare name. The callee itself
    (`Target`) is what is being constructed; the qualification question is
    about the CLASS identity, which is unambiguous once every candidate is
    class-kind — not about whether the receiver text ("some_module") is
    itself a known class. Regression guard for a real edge this fix's first
    pass got wrong (self-indexing atlas-aci surfaced it): reused the
    receiver-qualification lookup for construct edges too, mis-tiering this
    exact shape INFERRED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.py", "class Target:\n    pass\n")
    _write(
        repo,
        "app/hub.py",
        "import app.target as some_module\n"
        "class Hub:\n    def call(self):\n        some_module.Target()\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("Target")
    assert len(edges) == 1
    assert edges[0]["relation"] == "construct"
    assert edges[0]["confidence"] == "EXTRACTED"


# ---- MAJOR: qualification-by-resolution replaces the capitalization proxy ----


def test_lowercase_named_real_class_is_extracted_not_inferred(tmp_path: Path) -> None:
    """A class literally named with a lowercase identifier (legal Python/
    JS/Ruby syntax, just unconventional) called directly by that name must
    be EXTRACTED — capitalization is not the fact, symbol-table membership
    is. Under the old capitalization proxy this was silently mis-tiered
    INFERRED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/point.py",
        "class point:\n    @staticmethod\n    def act():\n        pass\n",
    )
    _write(repo, "app/hub.py", "class Hub:\n    def call(self):\n        point.act()\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("act")
    assert len(edges) == 1
    assert edges[0]["relation"] == "call"
    assert edges[0]["confidence"] == "EXTRACTED"


def test_capitalized_non_class_receiver_is_inferred_not_extracted(tmp_path: Path) -> None:
    """A capitalized identifier that is NOT a known class (e.g. a
    parameter following an unconventional capitalized naming style) must
    be INFERRED — capitalization alone must never grant EXTRACTED. Under
    the old capitalization proxy this was silently mis-tiered EXTRACTED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.py", "class Elsewhere:\n    def act(self):\n        pass\n")
    _write(
        repo,
        "app/hub.py",
        "class Hub:\n    def call(self, Thing):\n        Thing.act()\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("act")
    assert len(edges) == 1
    assert edges[0]["relation"] == "call"
    assert edges[0]["confidence"] == "INFERRED"


# ---- Coordinator MAJOR-1 (condition 5) — shadowing demotes to INFERRED ----
#
# checker (vigil) reproduction: `Config = load_config()` then
# `Config.reload()` was mis-tiered EXTRACTED, because the resolver
# name-matches `qualifier_name` against the GLOBAL symbol table with no
# scope analysis — a local variable happening to share a class's name is
# indistinguishable from the class itself under that check alone. Fix: a
# cheap, deterministic guard — if `qualifier_name` is ALSO an assignment
# target in the same file, demote to INFERRED (cannot rule out shadowing).
# A false EXTRACTED is worse than an honest INFERRED.


def test_shadowed_class_name_demotes_to_inferred_not_extracted(tmp_path: Path) -> None:
    """The coordinator's exact reproduction: `Config` is a real class
    elsewhere, but re-bound to a local variable in THIS file before being
    used as a receiver — the resolver cannot tell the two apart, so it must
    not claim EXTRACTED certainty it doesn't have."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/config.py",
        "class Config:\n    def reload(self):\n        pass\n"
        "def load_config():\n    return object()\n",
    )
    _write(
        repo,
        "app/hub.py",
        "class Hub:\n"
        "    def do_it(self):\n"
        "        Config = load_config()\n"  # local var shadows class Config
        "        Config.reload()\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("reload")
    assert len(edges) == 1
    assert edges[0]["confidence"] == "INFERRED", (
        "a qualifier_name that is ALSO a local assignment target in this file "
        "must never be credited as EXTRACTED"
    )
    # The resolved TARGET stays correct — only the *confidence* about how
    # certain that resolution is changes (checker's "bounded" finding).
    assert edges[0]["target"]["name"] == "reload"


def test_shadowed_construct_target_demotes_to_inferred(tmp_path: Path) -> None:
    """The same root cause, on the construct path: a class name rebound to
    a callable and invoked. `_resolve_edges` still (correctly) classifies
    this as `relation='construct'` — every candidate matching the name is
    class-kind — but must not claim EXTRACTED given the shadowing risk."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.py", "class Target:\n    def __init__(self):\n        pass\n")
    _write(
        repo,
        "app/hub.py",
        "class Hub:\n"
        "    def do_it(self):\n"
        "        Target = get_factory()\n"  # rebinds the class name locally
        "        Target()\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("Target")
    assert len(edges) == 1
    assert edges[0]["relation"] == "construct"
    assert edges[0]["confidence"] == "INFERRED"


def test_unshadowed_class_reference_still_extracted(tmp_path: Path) -> None:
    """Regression guard: a file with NO local reassignment of the class
    name must be unaffected by the shadowing guard — still EXTRACTED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.py", "class Target:\n    def act(self):\n        pass\n")
    _write(repo, "app/hub.py", "class Hub:\n    def call(self):\n        Target.act(self)\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("act")
    assert len(edges) == 1
    assert edges[0]["confidence"] == "EXTRACTED"


def test_js_shadowed_class_name_demotes_to_inferred(tmp_path: Path) -> None:
    """Same guard, JS/TS: `let Config = loadConfig();` shadows class
    `Config` in this file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/config.js",
        "class Config {\n  reload() {}\n}\nfunction loadConfig() {\n  return {};\n}\n",
    )
    _write(
        repo,
        "app/hub.js",
        "class Hub {\n  doIt() {\n    let Config = loadConfig();\n    Config.reload();\n  }\n}\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("reload")
    assert len(edges) == 1
    assert edges[0]["confidence"] == "INFERRED"


def test_ruby_qualifier_name_is_always_none_immune_to_this_guard(tmp_path: Path) -> None:
    """Ruby needs no shadowing guard at all: its grammar makes a constant
    receiver lexically un-shadowable by a local variable (a capitalized
    bare word is always parsed as a `constant` node, never as an
    `identifier`, regardless of any local assignment) — confirmed directly
    against `refs.qualifier_name`, which must be NULL for every Ruby ref."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.rb", "class Target\n  def act\n  end\nend\n")
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    Target = 5\n    Target.act\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    rows = graph.db.execute("SELECT qualifier_name FROM refs WHERE lang = 'ruby'").fetchall()
    assert rows
    assert all(r["qualifier_name"] is None for r in rows)


# ---- self/super stay INFERRED — spec.md D4/F18's explicit worked example ----


def test_self_receiver_stays_inferred_per_frozen_spec(tmp_path: Path) -> None:
    """spec.md D4/F18 explicitly worked-examples `self.bar`/`this.bar` as
    INFERRED for Python/JS/Ruby, by name, in all three per-language rules.
    Even though the edge's source endpoint now makes the enclosing class
    known, this implementation does NOT reinterpret that as qualification:
    doing so would contradict the frozen worked example, and is a spec.md
    amendment decision, not an implementation one (see the coordinator
    report — flagged, not silently resolved either way)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/hub.py",
        "class Hub:\n    def act(self):\n        pass\n    def call(self):\n        self.act()\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("act")
    assert len(edges) == 1
    assert edges[0]["confidence"] == "INFERRED"


def test_ruby_self_receiver_stays_inferred_per_frozen_spec(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def act\n  end\n\n  def call\n    self.act\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("act")
    assert len(edges) == 1
    assert edges[0]["confidence"] == "INFERRED"


# ---- MAJOR: unresolved_refs distinguishes "empty" from "incomplete" ----


def test_unresolved_refs_nonzero_when_refs_exist_but_dont_resolve(tmp_path: Path) -> None:
    """A callee referenced only via external/gem-style calls (no local
    definition anywhere) must surface a nonzero `unresolved_refs` count —
    an empty `edges` list alone cannot tell a consumer whether nothing
    calls this symbol or whether calls exist but couldn't be resolved."""
    from atlas_aci.codegraph import _CALL_RELATIONS

    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/hub.rb",
        "class Hub\n  def call\n    totally_external_gem_method()\n"
        "    totally_external_gem_method()\n  end\nend\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    assert graph.callers_of("totally_external_gem_method") == []
    unresolved = graph.unresolved_ref_count("totally_external_gem_method", _CALL_RELATIONS)
    assert unresolved == 2


def test_unresolved_refs_zero_when_genuinely_no_callers(tmp_path: Path) -> None:
    """A defined-but-never-called method: `edges == []` AND
    `unresolved_refs == 0` — the genuinely-empty case, distinguishable from
    the incomplete one above only by the count, never by the edges list
    alone."""
    from atlas_aci.codegraph import _CALL_RELATIONS

    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/a.rb", "class A\n  def never_called\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    assert graph.callers_of("never_called") == []
    assert graph.unresolved_ref_count("never_called", _CALL_RELATIONS) == 0


def test_unresolved_refs_zero_when_fully_resolved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.rb", "class Target\n  def bar\n  end\nend\n")
    _write(repo, "app/hub.rb", "class Hub\n  def call\n    Target.bar\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()

    edges = graph.callers_of("bar")
    assert len(edges) == 1
    assert graph.unresolved_ref_count("bar", ("call", "construct")) == 0


# ---- Confidence distribution sanity (no longer degenerate) ----


def test_extracted_tier_is_not_degenerate_once_constructors_and_direct_class_refs_count(
    tmp_path: Path,
) -> None:
    """Regression guard for the coordinator's finding that EXTRACTED held
    0.8% of edges on a real repo — a symptom of the constructor-resolution
    bug, not an inherent property of real code. A small but representative
    fixture (constructor calls, qualified attribute calls, unqualified
    calls) must show EXTRACTED as a substantial fraction, not a token
    handful."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/target.py", "class Target:\n    def act(self):\n        pass\n")
    _write(
        repo,
        "app/hub.py",
        "class Hub:\n"
        "    def call(self):\n"
        "        Target()\n"  # construct -> EXTRACTED
        "        Target()\n"  # construct -> EXTRACTED
        "        Target.act(self)\n"  # call, qualified -> EXTRACTED
        "        self.helper()\n"  # call, self -> INFERRED
        "    def helper(self):\n"
        "        pass\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    rows = graph.db.execute("SELECT confidence FROM edges").fetchall()
    confidences = [r["confidence"] for r in rows]
    extracted = confidences.count("EXTRACTED")
    total = len(confidences)
    assert total == 4
    assert extracted == 3, "2 constructs + 1 qualified attribute call"
    assert confidences.count("INFERRED") == 1
