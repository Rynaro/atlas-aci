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
