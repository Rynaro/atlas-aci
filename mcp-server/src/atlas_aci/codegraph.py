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
}


# Per-language Tree-sitter queries for symbol extraction.
# Add languages as needed; the Ruby and Python queries below cover the common cases.
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
}


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
        self.langs = set(langs or ["ruby", "python", "javascript", "typescript"])
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
        """Run the language-specific Tree-sitter query and pull out defs + refs."""
        from tree_sitter import QueryCursor
        from tree_sitter_language_pack import get_language

        ts_lang = get_language(cast("SupportedLanguage", lang))
        query = ts_lang.query(QUERIES[lang])
        captures = QueryCursor(query).captures(tree.root_node)

        symbols: list[Symbol] = []
        refs: list[Reference] = []

        # Tree-sitter captures arrive as {capture_name: [nodes]}
        for cap_name, nodes in captures.items():
            for node in nodes:
                if cap_name == "name":
                    # The parent capture tells us def.<kind>; we look it up via the def.* siblings
                    continue
                if cap_name.startswith("def."):
                    kind = cap_name.split(".", 1)[1]
                    name_node = self._find_name_child(node)
                    if name_node is None:
                        continue
                    name = source[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    symbols.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            path=rel_path,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            lang=lang,
                        )
                    )
                elif cap_name == "callee":
                    name = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
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

    def _find_name_child(self, node):
        """Find the @name capture inside a def.<kind> node.

        Heuristic: first 'identifier'/'constant' child.
        """
        for child in node.children:
            if child.type in ("identifier", "constant", "type_identifier", "property_identifier"):
                return child
            # Recurse one level for nested wrappings
            for grandchild in child.children:
                if grandchild.type in (
                    "identifier",
                    "constant",
                    "type_identifier",
                    "property_identifier",
                ):
                    return grandchild
        return None

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
