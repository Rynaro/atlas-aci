"""Code-graph backend.

Builds a SQLite-backed index over the repository using Tree-sitter for
universal AST parsing. Exposes:

- search_symbol(name, kind?) → defs + refs
- graph_query(query)        → adjacency lookups

For Ruby-heavy repos, the recommended
production deployment also runs `prism-codegraph` as a separate MCP server
with deeper Ruby semantics. This module covers the universal baseline.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from tree_sitter_language_pack import SupportedLanguage

log = structlog.get_logger()

# Map file extensions → tree-sitter language names.
#
# Beyond the programming languages, the web/markup/config formats below let
# ATLAS index static-site repos (Jekyll, Hugo, plain HTML/SCSS) where the
# "symbols" worth jumping to are SCSS mixins/variables, element ids, YAML
# keys, Markdown headings, and shell functions rather than classes/methods.
LANG_BY_EXT: dict[str, str] = {
    ".rb": "ruby",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    # Stylesheets — SCSS is a strict superset of CSS, so the scss grammar
    # parses plain `.css` too.
    ".scss": "scss",
    ".css": "scss",
    # Markup / templates (Jekyll layouts, includes, pages).
    ".html": "html",
    ".htm": "html",
    # Data / config / front matter.
    ".yml": "yaml",
    ".yaml": "yaml",
    # Prose / posts.
    ".md": "markdown",
    ".markdown": "markdown",
    # Shell tooling (build scripts, `jex.sh`-style helpers).
    ".sh": "bash",
    ".bash": "bash",
}


# Per-language Tree-sitter queries for symbol extraction.
#
# Every `@def.<kind>` capture must be accompanied by a `@name` capture in the
# same pattern — `_extract` reads the name straight from `@name`. A `@callee`
# capture (optionally paired with `@ref.*`) records a reference / call edge.
# Captures whose names start with `_` (e.g. `@_an`) are query-local helpers
# used only by `#eq?`/`#match?` predicates and are ignored by `_extract`.
#
# Add languages as needed; the Ruby and Python queries below cover the common
# cases, and the web/markup grammars below cover static-site repos.
QUERIES: dict[str, str] = {
    "ruby": """
        (class name: (constant) @name) @def.class
        (module name: (constant) @name) @def.module
        (method name: (identifier) @name) @def.method
        (singleton_method name: (identifier) @name) @def.method
        (call method: (identifier) @callee) @ref.call
    """,
    "python": """
        (class_definition name: (identifier) @name) @def.class
        (function_definition name: (identifier) @name) @def.function
        (call function: (identifier) @callee) @ref.call
        (call function: (attribute attribute: (identifier) @callee)) @ref.call
    """,
    "typescript": """
        (class_declaration name: (type_identifier) @name) @def.class
        (function_declaration name: (identifier) @name) @def.function
        (method_definition name: (property_identifier) @name) @def.method
        (call_expression function: (identifier) @callee) @ref.call
    """,
    "javascript": """
        (class_declaration name: (identifier) @name) @def.class
        (function_declaration name: (identifier) @name) @def.function
        (method_definition name: (property_identifier) @name) @def.method
        (call_expression function: (identifier) @callee) @ref.call
    """,
    # Stylesheets: jump to where a mixin/function/placeholder/`$variable` is
    # defined or a class/id selector is styled; `@include` sites become refs.
    # The `#match?` keeps only `$`-prefixed declarations (real SCSS variables),
    # not every `color:`/`margin:` property declaration.
    "scss": r"""
        (mixin_statement (identifier) @name) @def.mixin
        (function_statement (identifier) @name) @def.function
        (placeholder (identifier) @name) @def.placeholder
        (declaration (property_name) @name (#match? @name "^\\$")) @def.variable
        (class_selector (class_name) @name) @def.selector
        (id_selector (id_name) @name) @def.id
        (include_statement (identifier) @callee) @ref.include
    """,
    # Markup: index elements carrying an `id` (anchor targets / JS hooks).
    "html": """
        (attribute
            (attribute_name) @_an
            (quoted_attribute_value (attribute_value) @name)
            (#eq? @_an "id")) @def.id
    """,
    # Data / config / front matter: every mapping key is a lookup target.
    "yaml": """
        (block_mapping_pair key: (flow_node) @name) @def.key
    """,
    # Prose: headings are the navigable structure of a document.
    "markdown": """
        (atx_heading (inline) @name) @def.heading
        (setext_heading (paragraph (inline) @name)) @def.heading
    """,
    # Shell: function definitions are defs; every command invocation is a ref,
    # so `callers_of:<fn>` resolves call sites.
    "bash": """
        (function_definition (word) @name) @def.function
        (command (command_name (word) @callee)) @ref.call
    """,
}

# Languages indexed by default — kept in sync with the query table so adding a
# grammar above automatically extends the default index.
DEFAULT_LANGS: tuple[str, ...] = tuple(QUERIES)


SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- class | module | method | function
    path        TEXT NOT NULL,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    lang        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);

CREATE TABLE IF NOT EXISTS refs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    callee_name TEXT NOT NULL,
    path        TEXT NOT NULL,
    line        INTEGER NOT NULL,
    enclosing   TEXT,                -- enclosing def name, if known
    lang        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refs_callee ON refs(callee_name);

CREATE TABLE IF NOT EXISTS manifest (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Symbol:
    name: str
    kind: str
    path: str
    line_start: int
    line_end: int
    lang: str


@dataclass
class Reference:
    callee_name: str
    path: str
    line: int
    enclosing: str | None
    lang: str


class CodeGraph:
    """Tree-sitter-backed code graph stored in SQLite under .atlas/graph.db."""

    def __init__(self, repo: Path, langs: list[str] | None = None):
        self.repo = repo.resolve()
        self.langs = set(langs) if langs else set(DEFAULT_LANGS)
        self.db_path = self.repo / ".atlas" / "graph.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(self.db_path)
            self._db.executescript(SCHEMA)
            self._db.row_factory = sqlite3.Row
        return self._db

    # ---- Build / re-index ----

    def build(self, since: str | None = None) -> dict[str, Any]:
        """Index the repo. Returns stats."""
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError as e:
            log.error("tree_sitter_unavailable", error=str(e))
            raise

        # Wipe existing data — incremental indexing is a future optimization
        if since is None:
            self.db.execute("DELETE FROM symbols")
            self.db.execute("DELETE FROM refs")

        files_seen = 0
        symbols_added = 0
        refs_added = 0

        for path in self._iter_source_files():
            ext = path.suffix
            lang = LANG_BY_EXT.get(ext)
            if not lang or lang not in self.langs or lang not in QUERIES:
                continue

            try:
                parser = get_parser(cast("SupportedLanguage", lang))
                source = path.read_bytes()
                tree = parser.parse(source)
            except Exception as exc:
                log.warning("parse_failed", path=str(path), error=str(exc))
                continue

            files_seen += 1
            rel = str(path.relative_to(self.repo))

            symbols, refs = self._extract(tree, source, rel, lang)
            for s in symbols:
                self.db.execute(
                    "INSERT INTO symbols(name, kind, path, line_start, line_end, lang) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (s.name, s.kind, s.path, s.line_start, s.line_end, s.lang),
                )
                symbols_added += 1
            for r in refs:
                self.db.execute(
                    "INSERT INTO refs(callee_name, path, line, enclosing, lang) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (r.callee_name, r.path, r.line, r.enclosing, r.lang),
                )
                refs_added += 1

        self.db.execute("INSERT OR REPLACE INTO manifest(key, value) VALUES ('version', '1')")
        self.db.commit()
        return {"files_indexed": files_seen, "symbols": symbols_added, "refs": refs_added}

    def _iter_source_files(self):
        """Yield source files, respecting the skip list."""
        from atlas_aci.config import DEFAULT_SKIP_PATTERNS

        skip = set(DEFAULT_SKIP_PATTERNS)
        for path in self.repo.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(self.repo).parts
            if any(p in skip for p in rel_parts):
                continue
            yield path

    def _extract(
        self, tree, source: bytes, rel_path: str, lang: str
    ) -> tuple[list[Symbol], list[Reference]]:
        """Run the language-specific Tree-sitter query and pull out defs + refs.

        Iterates *matches* (not raw captures) so each ``@def.<kind>`` node stays
        grouped with the ``@name`` it owns, and ``#eq?``/``#match?`` predicates
        are applied. A match carrying a ``def.*`` capture yields a Symbol named
        from its ``@name``; a match carrying ``@callee`` yields a Reference.
        """
        from tree_sitter import Query, QueryCursor
        from tree_sitter_language_pack import get_language

        ts_lang = get_language(cast("SupportedLanguage", lang))
        query = Query(ts_lang, QUERIES[lang])
        matches = QueryCursor(query).matches(tree.root_node)

        symbols: list[Symbol] = []
        refs: list[Reference] = []

        # Each match arrives as (pattern_index, {capture_name: [nodes]}).
        for _pattern_index, caps in matches:
            def_cap = next((c for c in caps if c.startswith("def.")), None)
            if def_cap is not None:
                name_nodes = caps.get("name")
                if not name_nodes:
                    continue
                name = self._node_text(source, name_nodes[0]).strip()
                if not name:
                    continue
                def_node = caps[def_cap][0]
                symbols.append(
                    Symbol(
                        name=name,
                        kind=def_cap.split(".", 1)[1],
                        path=rel_path,
                        line_start=def_node.start_point[0] + 1,
                        line_end=def_node.end_point[0] + 1,
                        lang=lang,
                    )
                )
            elif "callee" in caps:
                for node in caps["callee"]:
                    name = self._node_text(source, node).strip()
                    if not name:
                        continue
                    refs.append(
                        Reference(
                            callee_name=name,
                            path=rel_path,
                            line=node.start_point[0] + 1,
                            enclosing=None,
                            lang=lang,
                        )
                    )

        return symbols, refs

    @staticmethod
    def _node_text(source: bytes, node) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    # ---- Queries ----

    def search_symbol(self, name: str, kind: str | None = None) -> dict[str, Any]:
        sql = "SELECT * FROM symbols WHERE name = ?"
        params: list[Any] = [name]
        if kind and kind != "any":
            sql += " AND kind = ?"
            params.append(kind)
        defs = [dict(r) for r in self.db.execute(sql, params).fetchall()]

        refs = [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM refs WHERE callee_name = ? LIMIT 200", (name,)
            ).fetchall()
        ]

        return {"definitions": defs, "references": refs}

    def callers_of(self, symbol: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT path, line, enclosing FROM refs WHERE callee_name = ?", (symbol,)
            ).fetchall()
        ]

    def query(self, dsl: str) -> dict[str, Any]:
        """Tiny DSL.

        Forms: 'callers_of:RecordVote#call',
        'subclasses_of:ApplicationRepository',
        'definitions_of:Tallier'.
        """
        if ":" not in dsl:
            return {"error": "INVALID_QUERY", "message": "Expected 'verb:argument' form."}
        verb, _, arg = dsl.partition(":")
        verb = verb.strip()
        arg = arg.strip()

        if verb == "callers_of":
            # Strip Class#method → method
            method = arg.split("#", 1)[-1] if "#" in arg else arg
            return {"edges": self.callers_of(method)}

        if verb == "definitions_of":
            return self.search_symbol(arg)

        if verb == "subclasses_of":
            # Best-effort; without inheritance edges, return classes whose name appears
            # near the parent. A real implementation extends QUERIES with superclass capture.
            return {
                "edges": [],
                "warning": "subclasses_of requires extended index; not implemented in MVP.",
            }

        return {"error": "UNKNOWN_VERB", "message": f"Unknown verb {verb!r}."}
