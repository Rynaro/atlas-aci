"""Tests for A4 — rationale nodes (D5): comment-derived "why" annotations,
promoted to first-class nodes linked to the code they explain via a
`rationale_for` edge. Ruby -> Python -> JS/TS order (D5); code languages
only (AC-A4-5) — scss/html/yaml/markdown/bash never get a rationale node,
even when they contain comment-like, prefix-matching text.

Every expected value below is computed independently of the extraction
code under test — a fresh fixture/assertion, not a call into
`_match_rationale_comment`/`rationale()` itself — per the boundary-testing
lesson this campaign keeps re-learning. Each test also considers: what
input has this never been shown? (a rationale-looking string INSIDE a
string/template literal; a comment attached to nothing; an ADR reference
to a file that doesn't exist; a marker in a language with no comment
capture at all.)
"""

from __future__ import annotations

from pathlib import Path

from atlas_aci.codegraph import PRODUCED_KINDS, CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement
from atlas_aci.memex import Memex
from atlas_aci.server import dispatch_tool_call


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _rationale_rows(graph: CodeGraph) -> list[dict]:
    rows = graph.db.execute(
        "SELECT path, line, text, label, target_path, target_line, target_name, lang "
        "FROM rationale ORDER BY path, line, text"
    ).fetchall()
    return [dict(r) for r in rows]


# ---- AC-A4-1 — Ruby rationale node + rationale_for edge ----


def test_ruby_rationale_node_and_edge(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/foo.rb",
        "# NOTE: module-level, no enclosing scope\n"
        "class Foo\n"
        "  # HACK: this method special-cases nil for legacy reasons\n"
        "  def bar\n"
        "  end\n"
        "end\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    rows = _rationale_rows(graph)
    assert len(rows) == 2, "exactly two recognized rationale comments in this fixture"

    module_level = next(r for r in rows if r["line"] == 1)
    assert module_level["text"] == "# NOTE: module-level, no enclosing scope"
    assert module_level["lang"] == "ruby"
    assert module_level["target_path"] is None, "no symbol in this file contains line 1"

    hack = next(r for r in rows if r["line"] == 3)
    assert hack["text"] == "# HACK: this method special-cases nil for legacy reasons"
    # The HACK comment (line 3) sits inside `class Foo` (lines 2-6) but
    # BEFORE `def bar` (lines 4-5) -- its tightest enclosing scope is the
    # class itself, not the method, computed independently by hand here.
    assert hack["target_path"] == "app/foo.rb"
    assert hack["target_line"] == 2
    assert hack["target_name"] == "Foo"


# ---- AC-A4-2 — Python + JS/TS, ported prefix set ----


def test_python_and_jsts_rationale_nodes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/foo.py",
        "# WHY: module-level python rationale\n"
        "def foo():\n"
        "    # RATIONALE: inner comment explaining a choice\n"
        "    return 1\n",
    )
    _write(
        repo,
        "app/foo.js",
        "function bar() {\n  // IMPORTANT: js rationale\n  return 1;\n}\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    rows = _rationale_rows(graph)
    py_rows = [r for r in rows if r["lang"] == "python"]
    js_rows = [r for r in rows if r["lang"] == "javascript"]
    assert len(py_rows) == 2
    assert len(js_rows) == 1

    py_module = next(r for r in py_rows if r["line"] == 1)
    assert py_module["text"] == "# WHY: module-level python rationale"
    assert py_module["target_path"] is None

    py_inner = next(r for r in py_rows if r["line"] == 3)
    assert py_inner["text"] == "# RATIONALE: inner comment explaining a choice"
    assert py_inner["target_name"] == "foo"
    assert py_inner["target_line"] == 2

    js_row = js_rows[0]
    assert js_row["text"] == "// IMPORTANT: js rationale"
    assert js_row["target_name"] == "bar"
    assert js_row["label"] is None


# ---- AC-A4-3 — JS/TS ADR/RFC promotion (JS/TS only, no equivalent elsewhere) ----


def test_jsts_adr_rfc_promotion(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/foo.ts",
        "function foo(): number {\n"
        "  // see ADR-11 for context, no NOTE:/HACK: prefix here\n"
        "  return 1;\n"
        "}\n"
        "function bar(): number {\n"
        "  /**\n"
        "   * background reading: RFC 793\n"
        "   */\n"
        "  return 2;\n"
        "}\n"
        "function baz(): number {\n"
        "  // a completely ordinary comment, no marker, no ADR/RFC\n"
        "  return 3;\n"
        "}\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    rows = _rationale_rows(graph)
    assert len(rows) == 2, "only the ADR- and RFC-referencing comments become rationale nodes"

    adr = next(r for r in rows if r["label"] == "ADR-0011")
    assert adr["target_name"] == "foo"
    assert adr["text"] == "// see ADR-11 for context, no NOTE:/HACK: prefix here"

    rfc = next(r for r in rows if r["label"] == "RFC-793")
    assert rfc["target_name"] == "bar"
    assert rfc["line"] == 7, "anchored at the actual matching inner line of the block comment"

    # AC-A4-3 makes no existence claim about the identifier: an ADR/RFC
    # reference to a document that does not exist anywhere in this repo
    # (there is no ADR-0011.md/RFC-793.md fixture file at all) still gets
    # a valid canonicalized label — the system never silently asserts the
    # reference resolves to anything.
    repo_files = {p.name for p in repo.rglob("*")}
    assert "ADR-0011.md" not in repo_files
    assert "RFC-793.md" not in repo_files


def test_adr_rfc_label_canonicalization_forms(tmp_path: Path) -> None:
    """Independently computed canonical forms for a spread of source
    spellings — zero-padded 4-digit ADR, as-is RFC (a disclosed judgment
    call, not further specified by the frozen criterion)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/spellings.js",
        "function a() { // ADR-11\n  return 1; }\n"
        "function b() { // ADR 42\n  return 1; }\n"
        "function c() { // adr123\n  return 1; }\n"
        "function d() { // RFC-2119\n  return 1; }\n"
        "function e() { // rfc 8259\n  return 1; }\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()
    labels_by_target = {r["target_name"]: r["label"] for r in _rationale_rows(graph)}
    assert labels_by_target == {
        "a": "ADR-0011",
        "b": "ADR-0042",
        "c": "ADR-0123",
        "d": "RFC-2119",
        "e": "RFC-8259",
    }


# ---- AC-A4-4 — rationale nodes never enter cross-file symbol resolution ----


def test_rationale_excluded_from_resolution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/foo.rb",
        "# NOTE: this text mentions bar() but is not a real call site\n"
        "class Foo\n"
        "  def bar\n"
        "  end\n"
        "\n"
        "  def caller_method\n"
        "    bar()\n"
        "  end\n"
        "end\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()

    # Structural guard (checker instruction): "rationale" must never be a
    # producible symbol kind at all -- the SAME completeness guard that
    # caught the `mixin` omission bug (MINOR-1, A1). If a future refactor
    # ever tagged the comment capture `@def.rationale` instead of
    # `@comment.rationale`, this fails immediately.
    assert "rationale" not in PRODUCED_KINDS

    # Exactly one edge for `bar` -- the real call site. The comment's own
    # text (which literally contains "bar()") must not add a phantom
    # second edge or inflate unresolved_refs.
    edge_count = graph.db.execute(
        "SELECT COUNT(*) FROM edges WHERE callee_name = 'bar'"
    ).fetchone()[0]
    assert edge_count == 1
    result = graph.query("callers_of:bar")
    assert len(result["edges"]) == 1
    assert result["edges"][0]["source"]["name"] == "caller_method"

    # The comment never becomes a definable/searchable symbol either.
    search = graph.search_symbol("bar")
    assert len(search["definitions"]) == 1, "only the real `def bar`, nothing from the comment"


def test_rationale_prefix_inside_string_literal_is_never_captured(tmp_path: Path) -> None:
    """What has this never been shown? A `# NOTE:`/`// HACK:`-shaped
    string INSIDE a string or template literal -- tree-sitter's grammar
    distinguishes `comment` nodes from `string`/`template_string` nodes at
    the parse-tree level, so this must never reach the `rationale` table
    at all (not merely be filtered out after the fact)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/foo.py",
        'x = "# NOTE: not a real comment, just a string"\n'
        'y = """\\n# HACK: also just a string body\\n"""\n'
        "def foo():\n    return x\n",
    )
    _write(
        repo,
        "app/foo.js",
        'const x = "// NOTE: not a real comment";\n'
        "const y = `// HACK: template literal body`;\n"
        "function foo() { return x; }\n",
    )
    graph = CodeGraph(repo=repo)
    graph.build()
    assert _rationale_rows(graph) == [], (
        "rationale-shaped text inside string/template literals must never produce a rationale node"
    )


def test_rationale_comment_attached_to_nothing_has_no_target(tmp_path: Path) -> None:
    """A rationale comment with no enclosing symbol anywhere in its file
    (e.g. a lone top-of-file comment in a file with no defs at all) must
    carry target_path/target_line/target_name all None -- a real "no
    enclosing definition" fact, not a resolution failure."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/orphan.rb", "# TODO: nothing else lives in this file\n")
    graph = CodeGraph(repo=repo)
    graph.build()
    rows = _rationale_rows(graph)
    assert len(rows) == 1
    assert rows[0]["target_path"] is None
    assert rows[0]["target_line"] is None
    assert rows[0]["target_name"] is None


# ---- AC-A4-5 — never for markup/config languages ----


def test_no_rationale_for_markup_config_langs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/style.scss",
        "// NOTE: scss comment shaped like rationale\n.foo { color: red; }\n",
    )
    _write(repo, "app/page.html", '<!-- NOTE: html comment -->\n<div id="foo"></div>\n')
    _write(repo, "app/data.yml", "# NOTE: yaml comment\nfoo: bar\n")
    _write(repo, "app/post.md", "<!-- NOTE: markdown comment -->\n# Heading\n")
    _write(
        repo,
        "app/script.sh",
        "# NOTE: bash comment, has a real comment capture, still not rationale\n"
        "foo() { echo hi; }\n",
    )

    graph = CodeGraph(repo=repo)
    stats = graph.build()
    assert stats["files_indexed"] == 5

    assert _rationale_rows(graph) == [], (
        "no rationale node for any markup/config language, even with a "
        "recognized-looking marker prefix present"
    )
    langs_present = {
        row[0] for row in graph.db.execute("SELECT DISTINCT lang FROM rationale").fetchall()
    }
    assert langs_present == set()


# ---- End-to-end: the `rationale:` graph_query verb ----


async def test_rationale_verb_end_to_end(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo=repo, memex_root=tmp_path / "memex")
    enforcement = Enforcement(config)
    memex = Memex(config.memex_root)

    _write(
        repo,
        "app/foo.rb",
        "class Foo\n  # WHY: explains the choice\n  def bar\n  end\nend\n",
    )
    code_graph = CodeGraph(repo=repo)
    code_graph.build()

    result = await dispatch_tool_call(
        "graph_query", {"query": "rationale:"}, config, enforcement, memex, code_graph
    )
    assert result["rationale_count"] == 1
    item = result["rationale"][0]
    assert item["text"] == "# WHY: explains the choice"
    assert item["target"] == {"path": "app/foo.rb", "line": 1, "name": "Foo"}
    assert item["label"] is None
    assert item["lang"] == "ruby"


def test_rationale_deterministic_total_order(tmp_path: Path) -> None:
    """Indexes identical source from scratch twice; the full rationale
    list must be byte-identical, same discipline as A2/A3's determinism
    pins (D6)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "app/foo.rb",
        "# NOTE: a\nclass Foo\n  # HACK: b\n  def bar\n  end\nend\n",
    )
    _write(repo, "app/bar.py", "# WHY: c\ndef baz():\n    pass\n")

    graph1 = CodeGraph(repo=repo)
    graph1.build()
    result1 = graph1.rationale()

    graph2 = CodeGraph(repo=repo)
    graph2.build()
    result2 = graph2.rationale()

    assert result1 == result2
    assert result1["rationale_count"] == 3
