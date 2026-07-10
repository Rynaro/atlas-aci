"""Tests for the Tree-sitter code-graph indexer.

These cover the universal language baseline (Ruby) plus the web/markup/config
grammars that let ATLAS index static-site repos (Jekyll & friends): SCSS, HTML,
YAML, Markdown, and shell.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas_aci.codegraph import DEFAULT_LANGS, CodeGraph


def _build(tmp_path: Path, files: dict[str, str]) -> CodeGraph:
    """Full build helper. Returns the live ``CodeGraph`` (its connection is not
    discarded), so callers can mutate `graph.repo` on disk and call
    ``graph.build(since=...)`` again to exercise incremental re-indexing.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    graph = CodeGraph(repo=repo)
    graph.build()
    return graph


def _names(rows: list[dict]) -> set[str]:
    return {r["name"] for r in rows}


def _kind_of(graph: CodeGraph, name: str) -> set[str]:
    return {d["kind"] for d in graph.search_symbol(name)["definitions"]}


# ---- Defaults stay in sync with the query table ----


def test_default_langs_cover_web_stack() -> None:
    for lang in ("ruby", "python", "scss", "html", "yaml", "markdown", "bash"):
        assert lang in DEFAULT_LANGS


# ---- Backward compatibility: the original programming-language baseline ----


def test_ruby_defs_and_refs(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/tally.rb": (
                "class Tallier\n  def call(ballot)\n    record_vote(ballot)\n  end\nend\n"
            ),
        },
    )
    res = graph.search_symbol("Tallier")
    assert _names(res["definitions"]) == {"Tallier"}
    assert _kind_of(graph, "Tallier") == {"class"}
    assert _kind_of(graph, "call") == {"method"}
    # `record_vote(ballot)` is called inside #call → recorded as a reference.
    assert graph.search_symbol("record_vote")["references"]


# ---- Checker MINOR-1 + the PRODUCED_KINDS sweep it prompted ----
#
# `(method name: (identifier) @name)` silently missed every Ruby method
# definition whose name is a `setter` or `operator` grammar node instead of a
# plain `identifier` -- confirmed via a raw tree-sitter parse before fixing:
# `def foo=` wraps its name in a `setter` node, `def +`/`def []`/`def []=`/
# `def ==`/etc. wrap theirs in an `operator` node. Neither was ever captured
# as a symbol, which also meant neither could ever be an enclosing scope for
# `callers_of`/rationale-target attribution (the exact solidus finding:
# `taxon.rb:105`'s `# NOTE:` inside `def child_index=` attributed to the
# class `Taxon` instead, because the setter was invisible to the symbol
# table entirely, not merely coarsely resolved).


def test_ruby_setter_method_is_captured_as_a_symbol(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/taxon.rb": (
                "class Taxon\n  def child_index=(idx)\n    @child_index = idx\n  end\nend\n"
            ),
        },
    )
    assert _kind_of(graph, "child_index=") == {"method"}


def test_ruby_operator_method_is_captured_as_a_symbol(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/money.rb": (
                "class Money\n"
                "  def +(other)\n  end\n"
                "  def [](i)\n  end\n"
                "  def []=(i, v)\n  end\n"
                "  def ==(other)\n  end\n"
                "end\n"
            ),
        },
    )
    for op_name in ("+", "[]", "[]=", "=="):
        assert _kind_of(graph, op_name) == {"method"}, f"operator method {op_name!r} not captured"


def test_ruby_singleton_setter_and_operator_methods_are_captured(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/config.rb": (
                "class Config\n  def self.value=(v)\n  end\n  def self.+(other)\n  end\nend\n"
            ),
        },
    )
    assert _kind_of(graph, "value=") == {"method"}
    assert _kind_of(graph, "+") == {"method"}


def test_ts_private_method_is_captured_as_a_symbol_and_a_call_resolves(tmp_path: Path) -> None:
    """`#privateMethod` uses a `private_property_identifier` name node, NOT
    `property_identifier` -- distinct on both the definition side
    (`method_definition name:`) and the call site
    (`member_expression property:`, e.g. `this.#privateMethod()`)."""
    graph = _build(
        tmp_path,
        {
            "app/widget.ts": (
                "class Widget {\n"
                "  #privateMethod() {\n    return 1;\n  }\n"
                "  run() {\n    return this.#privateMethod();\n  }\n"
                "}\n"
            ),
        },
    )
    assert _kind_of(graph, "#privateMethod") == {"method"}
    edges = graph.callers_of("#privateMethod")
    assert len(edges) == 1
    assert edges[0]["source"]["name"] == "run"


# ---- SCSS ----


SCSS = """\
@mixin button($color) {
  color: $color;
}

@function double($n) {
  @return $n * 2;
}

$brand: #ff0066;

%card-base {
  border: 1px solid;
}

.hero-title {
  @include button($brand);
}

#exp-bar {
  width: 100%;
}
"""


def test_scss_symbols(tmp_path: Path) -> None:
    graph = _build(tmp_path, {"_sass/_components.scss": SCSS})

    assert _kind_of(graph, "button") == {"mixin"}
    assert _kind_of(graph, "double") == {"function"}
    assert _kind_of(graph, "card-base") == {"placeholder"}
    # Only `$`-prefixed declarations are variables — not `color:`/`width:`.
    assert _kind_of(graph, "$brand") == {"variable"}
    assert graph.search_symbol("color")["definitions"] == []
    assert graph.search_symbol("width")["definitions"] == []
    # Selectors are indexed so you can jump to where a class/id is styled.
    assert _kind_of(graph, "hero-title") == {"selector"}
    assert _kind_of(graph, "exp-bar") == {"id"}
    # `@include button(...)` is a reference to the mixin.
    assert graph.search_symbol("button")["references"]


# ---- HTML ----


def test_html_indexes_only_ids(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "_layouts/default.html": (
                '<nav id="main-nav" class="navbar">\n'
                '  <a href="/about/">About</a>\n'
                "</nav>\n"
                '<div id="content"></div>\n'
            ),
        },
    )
    assert _kind_of(graph, "main-nav") == {"id"}
    assert _kind_of(graph, "content") == {"id"}
    # class / href values must NOT be indexed as symbols.
    assert graph.search_symbol("navbar")["definitions"] == []
    assert graph.search_symbol("/about/")["definitions"] == []


# ---- YAML ----


def test_yaml_indexes_mapping_keys(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "_data/profile.yml": (
                "name: Rynaro\nskills:\n  - ruby\n  - elixir\nspecial_abilities:\n  focus: high\n"
            ),
        },
    )
    assert _kind_of(graph, "name") == {"key"}
    assert _kind_of(graph, "skills") == {"key"}
    # Nested keys are reachable too.
    assert _kind_of(graph, "focus") == {"key"}


# ---- Markdown ----


def test_markdown_indexes_headings(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "_posts/2026-01-01-hello.md": (
                "# Setup\n\nSome intro text.\n\n## Installation steps\n\nMore text.\n"
            ),
        },
    )
    assert _kind_of(graph, "Setup") == {"heading"}
    assert _kind_of(graph, "Installation steps") == {"heading"}


# ---- Shell ----


def test_bash_functions_and_calls(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "jex.sh": (
                "#!/usr/bin/env bash\nset -e\nensure_dir() {\n  mkdir -p build\n}\nensure_dir\n"
            ),
        },
    )
    assert _kind_of(graph, "ensure_dir") == {"function"}
    # The invocation on the last line is a reference back to the function.
    refs = graph.search_symbol("ensure_dir")["references"]
    assert any(r["path"] == "jex.sh" for r in refs)


# ---- Skip list ----


def test_generated_site_output_is_skipped(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "_sass/_real.scss": "@mixin only_real { x: 1; }\n",
            "_site/assets/_built.scss": "@mixin only_built { x: 1; }\n",
        },
    )
    assert _kind_of(graph, "only_real") == {"mixin"}
    # Anything under _site/ is generated output and must not be indexed.
    assert graph.search_symbol("only_built")["definitions"] == []


@pytest.mark.parametrize(
    "ext", [".scss", ".css", ".html", ".yml", ".yaml", ".md", ".markdown", ".sh"]
)
def test_known_web_extensions_are_recognized(ext: str) -> None:
    from atlas_aci.codegraph import LANG_BY_EXT

    assert ext in LANG_BY_EXT


# ---- Incremental indexing (--since) ----


def _symbol_count(graph: CodeGraph) -> int:
    return int(graph.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])


def _ref_count(graph: CodeGraph) -> int:
    return int(graph.db.execute("SELECT COUNT(*) FROM refs").fetchone()[0])


def test_incremental_skips_unchanged_files(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/a.rb": "class A\n  def call\n  end\nend\n",
            "app/b.rb": "class B\n  def call\n  end\nend\n",
        },
    )

    stats = graph.build(since="HEAD")

    assert stats["files_skipped"] >= 2
    assert stats["files_indexed"] == 0
    assert _names(graph.search_symbol("A")["definitions"]) == {"A"}
    assert _names(graph.search_symbol("B")["definitions"]) == {"B"}


def test_incremental_reindexes_changed_file_without_stale_rows(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/a.rb": "class OldName\n  def call\n  end\nend\n",
            "app/b.rb": "class B\n  def call\n  end\nend\n",
        },
    )
    path_a = graph.repo / "app/a.rb"
    path_a.write_text("class NewName\n  def call\n  end\nend\n")
    # Force a distinct, later mtime so the (mtime_ns, size) change key differs
    # even if the rewrite happened to land at the same size within the same
    # filesystem timestamp tick.
    bumped_ns = path_a.stat().st_mtime_ns + 10_000_000_000
    os.utime(path_a, ns=(bumped_ns, bumped_ns))

    stats = graph.build(since="HEAD")

    assert stats["files_indexed"] == 1
    assert graph.search_symbol("OldName")["definitions"] == []
    assert _names(graph.search_symbol("NewName")["definitions"]) == {"NewName"}
    # The untouched sibling file's symbols must survive unchanged.
    assert _names(graph.search_symbol("B")["definitions"]) == {"B"}


def test_incremental_removes_deleted_file(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/a.rb": "class A\n  def call\n  end\nend\n",
            "app/b.rb": "class B\n  def call\n  end\nend\n",
        },
    )
    (graph.repo / "app/b.rb").unlink()

    stats = graph.build(since="HEAD")

    assert stats["files_removed"] >= 1
    assert graph.search_symbol("B")["definitions"] == []
    assert _names(graph.search_symbol("A")["definitions"]) == {"A"}


def test_incremental_is_idempotent_when_nothing_changes(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/a.rb": "class A\n  def call\n    record_vote(1)\n  end\nend\n",
        },
    )

    graph.build(since="HEAD")
    first_symbols, first_refs = _symbol_count(graph), _ref_count(graph)

    graph.build(since="HEAD")
    second_symbols, second_refs = _symbol_count(graph), _ref_count(graph)

    assert first_symbols == second_symbols
    assert first_refs == second_refs


def test_full_build_populates_files_manifest(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/a.rb": "class A\nend\n",
            "app/b.rb": "class B\nend\n",
        },
    )

    count = graph.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert count > 0


# ---- H4: dead-language honesty (D5-Q2) ----


def test_lang_by_ext_consistent_with_queries() -> None:
    """Every LANG_BY_EXT value must be either a real QUERIES entry or an
    explicit UNSUPPORTED_LANGS acknowledgment — never silently neither
    (the bug this guards: .tsx/.go/.rs/.java recognized but query-less)."""
    from atlas_aci.codegraph import LANG_BY_EXT, QUERIES, UNSUPPORTED_LANGS

    assert set(LANG_BY_EXT.values()) <= set(QUERIES) | UNSUPPORTED_LANGS
    # And the acknowledgment set itself must be real: every unsupported lang
    # must actually be unreachable in QUERIES (else it's a stale entry).
    assert UNSUPPORTED_LANGS.isdisjoint(QUERIES)
    # Pin the specific four dead extensions this fix targets so a future
    # edit that silently drops the acknowledgment (rather than adding a real
    # QUERIES entry) is caught.
    for ext, lang in (
        (".tsx", "tsx"),
        (".go", "go"),
        (".rs", "rust"),
        (".java", "java"),
    ):
        assert LANG_BY_EXT[ext] == lang
        assert lang in UNSUPPORTED_LANGS
        assert lang not in QUERIES


def test_unsupported_extension_skip_is_reported(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "app/a.rb": "class A\nend\n",
            "main.go": "package main\n\nfunc main() {}\n",
            "lib.rs": "fn main() {}\n",
            "App.java": "class App {}\n",
        },
    )
    stats = graph.build()  # rebuild so the returned stats reflect this tree
    assert stats["unsupported_skipped"] == {"go": 1, "rust": 1, "java": 1}
    # And it must not have silently produced phantom symbols from files it
    # never actually parsed.
    assert _names(graph.search_symbol("main")["definitions"]) == set()


# ---- A1 checker MINOR-1 (condition 2): call-candidate-kind completeness ----


def test_produced_kinds_are_fully_classified_for_call_resolution() -> None:
    """Every kind PRODUCED_KINDS can actually produce (derived mechanically
    from QUERIES, same source AC-DOC-6 uses for the search_symbol kind enum)
    must fall into exactly one of: `_CALLABLE_KINDS` (a "call"-relation ref
    can resolve to it), `_CLASS_KINDS` (a heritage/construct ref resolves to
    it), or `_NON_CALLABLE_KINDS` (deliberately excluded, with a reason in
    the source comment). This is the guard the BLOCKER's post-mortem should
    have left behind: excluding a kind is now a listed decision, not an
    oversight that surfaces only when someone happens to query it (`mixin`
    was found this way — non-silent thanks to `unresolved_refs`, but the
    same shape as the class-resolution bug)."""
    from atlas_aci.codegraph import (
        _CALLABLE_KINDS,
        _CLASS_KINDS,
        _NON_CALLABLE_KINDS,
        PRODUCED_KINDS,
    )

    classified = set(_CALLABLE_KINDS) | set(_CLASS_KINDS) | set(_NON_CALLABLE_KINDS)
    assert set(PRODUCED_KINDS) <= classified, (
        f"kind(s) {set(PRODUCED_KINDS) - classified} are produced by QUERIES but "
        f"classified nowhere — a future callers_of on that kind would silently "
        f"resolve to zero candidates"
    )
    # The three buckets must be pairwise disjoint — a kind classified twice
    # (e.g. both callable and excluded) is as much a bug as unclassified.
    assert set(_CALLABLE_KINDS).isdisjoint(_CLASS_KINDS)
    assert set(_CALLABLE_KINDS).isdisjoint(_NON_CALLABLE_KINDS)
    assert set(_CLASS_KINDS).isdisjoint(_NON_CALLABLE_KINDS)
    # Pin the specific kinds this fix targets, so a future edit that quietly
    # drops `mixin` back out (or re-adds the dead `singleton_method`) is
    # caught rather than silently reopening the same bug shape.
    assert "mixin" in _CALLABLE_KINDS
    assert "singleton_method" not in _CALLABLE_KINDS


def test_scss_mixin_include_resolves_to_a_real_edge(tmp_path: Path) -> None:
    """End-to-end regression for the exact MINOR-1 reproduction:
    `callers_of:rounded` on an scss `@include rounded;` site must resolve,
    not return an unresolved-but-silent-looking empty edge list."""
    graph = _build(
        tmp_path,
        {
            "_sass/_mixins.scss": "@mixin rounded {\n  border-radius: 4px;\n}\n",
            "_sass/_card.scss": ".card {\n  @include rounded;\n}\n",
        },
    )
    edges = graph.callers_of("rounded")
    assert len(edges) == 1
    assert edges[0]["relation"] == "call"
    # `@include <name>;` has no receiver/qualifier syntax at all (unlike
    # Ruby/Python/JS-TS calls) — F18 defines no qualification rule for it,
    # so a bare, unqualified mixin name is INFERRED (name-uniqueness),
    # mirroring F18's own "a bare method with a unique name = INFERRED"
    # rule for the languages it does cover.
    assert edges[0]["confidence"] == "INFERRED"


# ---- AC-A5-8: source-file iteration is deterministically sorted ----


def test_source_file_iteration_sorted(tmp_path: Path) -> None:
    """`_iter_source_files` used to be bare `self.repo.rglob("*")` —
    filesystem/directory-entry order, not a content-derived total order.
    Files are deliberately created in REVERSE-of-sorted order (many
    filesystems, ext4/tmpfs included, tend to preserve insertion order for
    small directories absent htree indexing), so a regression back to raw
    `rglob` has a real chance of being caught here rather than only in a
    cross-machine CI OS-matrix run — the same "same-machine teeth before
    the cross-OS gate" property D6/F8 established for record-level order."""
    repo = tmp_path / "repo"
    repo.mkdir()
    rels = ["zeta.rb", "mu.rb", "beta/gamma.rb", "alpha.rb"]  # reverse-of-sorted creation order
    for rel in rels:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("class Foo\nend\n")

    graph = CodeGraph(repo=repo)
    seen = [str(p.relative_to(repo)) for p in graph._iter_source_files()]

    assert seen == sorted(rels)
