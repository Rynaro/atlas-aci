"""Code-graph backend.

Builds a SQLite-backed index over the repository using Tree-sitter for
universal AST parsing. Exposes:

- search_symbol(name, kind?) → defs + refs
- graph_query(query)        → adjacency lookups

This module covers the universal baseline for every shipped language.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS

if TYPE_CHECKING:
    from tree_sitter_language_pack import SupportedLanguage

log = structlog.get_logger()

# Map file extensions → tree-sitter language names.
#
# Beyond the programming languages, the web/markup/config formats below let
# ATLAS index static-site repos (Jekyll, Hugo, plain HTML/SCSS) where the
# "symbols" worth jumping to are SCSS mixins/variables, element ids, YAML
# keys, Markdown headings, and shell functions rather than classes/methods.
#
# Not every extension below has a QUERIES entry — see UNSUPPORTED_LANGS and
# the module-level consistency assertion just below QUERIES. Recognizing an
# extension here without either a QUERIES entry or an UNSUPPORTED_LANGS
# acknowledgment is the "silent dead language" bug (D5-Q2): the indexer would
# skip every such file with zero visible signal.
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

# Kinds the indexer actually produces, derived mechanically from QUERIES so
# this can never drift from reality (AC-DOC-6: the search_symbol `kind` enum
# in server.py's tool manifest must stay a superset of this).
_DEF_KIND_RE = re.compile(r"@def\.(\w+)")
PRODUCED_KINDS: tuple[str, ...] = tuple(
    sorted({m for q in QUERIES.values() for m in _DEF_KIND_RE.findall(q)})
)

# Extensions recognized in LANG_BY_EXT with no QUERIES entry — the indexer
# skips these but reports the skip visibly (AC-H-14) instead of silently
# indexing to nothing (D5-Q2). This is an *honesty* fix, not a coverage
# commitment: promoting one of these to real support means writing a real
# QUERIES entry, at which point it comes out of this set.
UNSUPPORTED_LANGS: frozenset[str] = frozenset({"tsx", "go", "rust", "java"})

assert set(LANG_BY_EXT.values()) <= set(QUERIES) | UNSUPPORTED_LANGS, (
    "LANG_BY_EXT declares a language with neither a QUERIES entry nor an "
    "UNSUPPORTED_LANGS acknowledgment — the silent dead-language bug D5-Q2 "
    "exists specifically to prevent."
)


# ---- Schema-epoch DB substrate (D1/H3) ----
#
# The DB is pure derived data (G-A): fully reconstructable from source, never
# hand-edited. There is therefore no in-place schema-migration ladder — a
# schema change always yields a fresh epoch (`.atlas/graph.<epoch>.db`),
# never an in-place migration of the old file. `SCHEMA_EPOCH` is a monotonic integer,
# bumped in the same commit that changes `SCHEMA` below; `EXPECTED_DDL_HASH`
# is a *hand-maintained* companion constant (deliberately NOT derived from
# `SCHEMA` at import time — see test_schema_epoch.py::test_expected_ddl_hash_
# matches_current_ddl) that must be recomputed and pasted in whenever `SCHEMA`
# changes, so "changed the DDL, forgot to bump the epoch" fails CI (AC-H-12)
# instead of silently reusing a wrong-shaped DB.
#
# Recompute via:
#   python -c "from atlas_aci.codegraph import SCHEMA, ddl_hash; print(ddl_hash(SCHEMA))"
SCHEMA_EPOCH = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- class | module | method | function | ...
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

-- Rationale nodes (A4, phase P2) — comment-derived "why" annotations. This
-- is a *separate* relation from `refs` and the future call/inheritance edge
-- table (F9 / AC-A4-6): `rationale_for` edges carry no confidence-enum
-- value, so they must never collide with "every materialized call/
-- inheritance edge carries a confidence" (AC-A1-2). Schema-only in this
-- epoch — P2's A4 workstream adds the comment-scanning extraction logic;
-- nothing populates this table yet.
CREATE TABLE IF NOT EXISTS rationale (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL,
    line          INTEGER NOT NULL,
    text          TEXT NOT NULL,
    label         TEXT,               -- canonicalized ADR/RFC label, if any
    target_path   TEXT,               -- rationale_for edge target: enclosing scope's file
    target_line   INTEGER,            -- rationale_for edge target: enclosing scope's line
    target_name   TEXT,               -- rationale_for edge target: enclosing scope's symbol name
    lang          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rationale_path ON rationale(path);

CREATE TABLE IF NOT EXISTS manifest (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Per-file state for incremental (`--since`) indexing: lets build() skip
-- files whose (mtime_ns, size) haven't changed since the last pass.
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    mtime_ns    INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    lang        TEXT NOT NULL,
    indexed_at  TEXT
);
"""


def ddl_hash(ddl: str) -> str:
    """SHA-256 hex digest of a schema DDL string (AC-H-12)."""
    return hashlib.sha256(ddl.encode("utf-8")).hexdigest()


# Hand-maintained — see the SCHEMA_EPOCH docstring above. Do NOT replace this
# with `ddl_hash(SCHEMA)`; that would make the pairing test a tautology.
EXPECTED_DDL_HASH = "2371e04f6df8df80cfa9e6162dd451acf8d8db52e545a494bed6a4e296347cd6"


def parse_query_verb(dsl: str) -> str:
    """Extract the DSL verb ('callers_of', 'definitions_of', 'subclasses_of',
    ...) from a graph_query string without a full parse. Shared by
    `CodeGraph.query` and server.py's central-bounds dispatch (D2) so the two
    never drift on what "verb" means.
    """
    if ":" not in dsl:
        return ""
    verb, _, _ = dsl.partition(":")
    return verb.strip()


# The graph_query DSL's entire verb vocabulary — the single source of truth
# `CodeGraph.query` dispatches against (F-3). A dedicated test
# (test_server.py::test_bounded_field_registry_covers_every_known_query_verb)
# asserts every member here has a non-empty entry in
# `GRAPH_QUERY_VERB_BOUNDED_FIELDS`, discovered from *this* constant rather
# than a hand-maintained duplicate list in the test file. This is what makes
# a brand-new verb's bounds-registration checked mechanically regardless of
# what it's named — the checker demonstrated that a harden-gate.yml content
# grep for literal strings like "label_propagation" is evadable by simply
# choosing different names; a verb landing here (which it must, to be
# dispatchable at all) cannot evade a test that enumerates this set itself.
KNOWN_QUERY_VERBS: frozenset[str] = frozenset({"callers_of", "definitions_of", "subclasses_of"})


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
    """Tree-sitter-backed code graph stored in SQLite under
    ``.atlas/graph.<SCHEMA_EPOCH>.db``.

    ``read_only=True`` is the contract `serve` uses (DIR-2): the constructor
    performs no filesystem writes, and the lazy `db` connection opens in
    SQLite's ``mode=ro`` so no write, journal, or lock file is ever created —
    safe against a ``--read-only`` ``:ro`` bind mount. Sweeping stale-epoch
    files and rebuilding on epoch mismatch happen exclusively in `build()`
    (the `index`/write path); a read-only instance never sweeps or rebuilds.

    ``query_limit`` bounds every internal SQL query in `search_symbol` /
    `callers_of` (F-1/F-2). It is deliberately the *central bounds cap plus
    one* (``max_bound_field_elements + 1``), never equal to the cap itself —
    a query that fetches exactly ``cap`` rows pre-truncates in SQL before the
    central chokepoint ever sees the response, so an exact-cap SQL LIMIT is
    indistinguishable from "nothing more exists" and the overflow flag never
    fires (this collided exactly at the shared default of 200/200). Fetching
    one extra row makes overflow *detectable*: if the (cap+1)-th row comes
    back, the central cap correctly sees ``len > cap`` and truncates-and-
    flags; if it doesn't, nothing was hidden. This is also what bounds the
    *work* (F-2): callers with no LIMIT at all `fetchall()` an unbounded
    result set into memory regardless of what the response-side byte
    ceiling later measures. `server.py`'s `run_stdio` wires this to
    ``config.max_bound_field_elements + 1``; the default here
    (``DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1``) matches Config's own default
    so a bare ``CodeGraph(repo)`` (as most tests construct it) still fetches
    a bounded amount.
    """

    def __init__(
        self,
        repo: Path,
        langs: list[str] | None = None,
        read_only: bool = False,
        query_limit: int | None = None,
    ):
        self.repo = repo.resolve()
        self.langs = set(langs) if langs else set(DEFAULT_LANGS)
        self.read_only = read_only
        self.query_limit = (
            query_limit if query_limit is not None else DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1
        )
        self.atlas_dir = self.repo / ".atlas"
        self.db_path = self.atlas_dir / f"graph.{SCHEMA_EPOCH}.db"
        if not read_only:
            self.atlas_dir.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            if self.read_only:
                # mode=ro: SQLite never attempts a write, journal, or lock
                # file for this connection — safe on a read-only mount.
                self._db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            else:
                self._db = sqlite3.connect(self.db_path)
                self._db.executescript(SCHEMA)
            self._db.row_factory = sqlite3.Row
        return self._db

    def epoch_ok(self) -> bool:
        """True iff the current-epoch DB file exists AND its in-DB manifest
        epoch row agrees with SCHEMA_EPOCH (the F1 belt-and-suspenders
        cross-check). Always opens its own short-lived ``mode=ro`` connection
        — never creates, writes, or reuses `self.db` — so it is safe to call
        from the read-only `serve` path (DIR-2/AC-H-16) as well as from
        `build()` to decide whether an incremental pass is safe.
        """
        if not self.db_path.exists():
            return False
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT value FROM manifest WHERE key = 'epoch'").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return False
        return row is not None and row[0] == str(SCHEMA_EPOCH)

    # ---- Build / re-index (index-path only; never called by `serve`) ----

    def build(self, since: str | None = None) -> dict[str, Any]:
        """Index the repo. Returns stats.

        ``since`` is truthy → incremental mode: a per-file ``(mtime_ns, size)``
        manifest (the ``files`` table) is consulted so files unchanged since the
        last pass are skipped entirely (their existing symbols/refs carry
        forward untouched), changed/new files are re-extracted after purging
        their stale rows, and files that vanished from disk have their rows
        removed. ``since=None`` performs a full rebuild: all ``symbols``,
        ``refs``, and ``files`` rows are wiped and every file is re-indexed,
        which also re-establishes a correct baseline manifest for a later
        incremental pass.

        Per DIR-2, sweeping stale-epoch files and rebuilding on epoch
        mismatch happen *only* here, never on the `serve` (read-only) path.
        Per F17/AC-H-17, a full rebuild writes to a temporary file and
        atomically replaces the target path; both modes run under a
        single-writer file lock so two concurrent `index` invocations (e.g.
        the documented backgrounded post-commit hook) cannot corrupt the DB.
        """
        if self.read_only:
            raise RuntimeError(
                "CodeGraph.build() is index-path only (DIR-2); a read_only "
                "CodeGraph (as `serve` constructs) never writes to .atlas."
            )
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError as e:
            log.error("tree_sitter_unavailable", error=str(e))
            raise

        self.atlas_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.atlas_dir / ".index.lock"
        with open(lock_path, "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                swept = self._sweep_stale_epoch_files()
                if swept:
                    log.info("stale_epoch_files_swept", files=swept)

                if since is not None and self.epoch_ok():
                    return self._build_in_place(get_parser, since)
                if since is not None:
                    log.warning(
                        "schema_epoch_mismatch_forces_full_rebuild",
                        expected_epoch=SCHEMA_EPOCH,
                    )
                return self._build_full_atomic(get_parser)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _sweep_stale_epoch_files(self) -> list[str]:
        """Index-path only (DIR-2): remove ``.atlas/graph.*.db`` files whose
        epoch differs from `SCHEMA_EPOCH`. `serve` never calls this."""
        if not self.atlas_dir.exists():
            return []
        removed = []
        for f in sorted(self.atlas_dir.glob("graph.*.db")):
            if f.name != self.db_path.name:
                f.unlink()
                removed.append(f.name)
        return removed

    def _build_in_place(self, get_parser: Any, since: str) -> dict[str, Any]:
        """Incremental build: modifies the existing current-epoch DB file in
        place (protected by the single-writer lock in `build()`)."""
        self._db = None
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        conn.row_factory = sqlite3.Row
        try:
            stats = self._run_build(conn, get_parser, since=since)
        finally:
            conn.close()
        self._db = None  # next `.db` access reopens fresh against db_path
        return stats

    def _build_full_atomic(self, get_parser: Any) -> dict[str, Any]:
        """Full rebuild: builds into a temp file, then atomically replaces
        `self.db_path` (F17/AC-H-17). A crash mid-build leaves any previous
        DB untouched."""
        tmp_path = self.atlas_dir / f".graph.{SCHEMA_EPOCH}.db.tmp.{os.getpid()}"
        if tmp_path.exists():
            tmp_path.unlink()
        conn = sqlite3.connect(tmp_path)
        conn.executescript(SCHEMA)
        conn.row_factory = sqlite3.Row
        try:
            stats = self._run_build(conn, get_parser, since=None)
        finally:
            conn.close()
        self._db = None  # drop any stale handle before the swap
        os.replace(tmp_path, self.db_path)
        return stats

    def _run_build(
        self, conn: sqlite3.Connection, get_parser: Any, since: str | None
    ) -> dict[str, Any]:
        """The core indexing loop against an already-open connection. Shared
        by the in-place incremental path and the full-rebuild-into-temp-file
        path so the extraction logic itself never duplicates."""
        incremental = since is not None

        if not incremental:
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM refs")
            conn.execute("DELETE FROM files")

        stored_files: dict[str, tuple[int, int]] = {}
        if incremental:
            stored_files = {
                row["path"]: (row["mtime_ns"], row["size"])
                for row in conn.execute("SELECT path, mtime_ns, size FROM files").fetchall()
            }

        files_indexed = 0
        files_skipped = 0
        files_removed = 0
        symbols_added = 0
        refs_added = 0
        seen_paths: set[str] = set()
        unsupported_skipped: dict[str, int] = {}

        for path in self._iter_source_files():
            ext = path.suffix
            lang = LANG_BY_EXT.get(ext)

            if lang is not None and lang not in QUERIES:
                # Recognized extension, no query support (D5-Q2) — report,
                # don't silently no-op.
                unsupported_skipped[lang] = unsupported_skipped.get(lang, 0) + 1
                continue

            if not lang or lang not in self.langs or lang not in QUERIES:
                continue

            rel = str(path.relative_to(self.repo))
            stat = path.stat()
            mtime_ns, size = stat.st_mtime_ns, stat.st_size

            if incremental:
                seen_paths.add(rel)
                if stored_files.get(rel) == (mtime_ns, size):
                    files_skipped += 1
                    continue

            try:
                parser = get_parser(cast("SupportedLanguage", lang))
                source = path.read_bytes()
                tree = parser.parse(source)
            except Exception as exc:
                log.warning("parse_failed", path=str(path), error=str(exc))
                continue

            if incremental:
                # New or changed file — drop stale rows before re-extracting so
                # a rename/removal-within-file doesn't leave duplicate entries.
                conn.execute("DELETE FROM symbols WHERE path = ?", (rel,))
                conn.execute("DELETE FROM refs WHERE path = ?", (rel,))

            symbols, refs = self._extract(tree, source, rel, lang)
            for s in symbols:
                conn.execute(
                    "INSERT INTO symbols(name, kind, path, line_start, line_end, lang) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (s.name, s.kind, s.path, s.line_start, s.line_end, s.lang),
                )
                symbols_added += 1
            for r in refs:
                conn.execute(
                    "INSERT INTO refs(callee_name, path, line, enclosing, lang) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (r.callee_name, r.path, r.line, r.enclosing, r.lang),
                )
                refs_added += 1

            conn.execute(
                "INSERT OR REPLACE INTO files(path, mtime_ns, size, lang, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (rel, mtime_ns, size, lang, datetime.now(UTC).isoformat()),
            )
            files_indexed += 1

        if incremental:
            for rel in set(stored_files) - seen_paths:
                conn.execute("DELETE FROM symbols WHERE path = ?", (rel,))
                conn.execute("DELETE FROM refs WHERE path = ?", (rel,))
                conn.execute("DELETE FROM files WHERE path = ?", (rel,))
                files_removed += 1

        for lang, count in sorted(unsupported_skipped.items()):
            log.warning(
                "unsupported_extension_skipped",
                lang=lang,
                count=count,
                message=f"unsupported extension skipped: {count} files",
            )

        conn.execute(
            "INSERT OR REPLACE INTO manifest(key, value) VALUES ('epoch', ?)",
            (str(SCHEMA_EPOCH),),
        )
        conn.commit()
        return {
            "files_indexed": files_indexed,
            "symbols": symbols_added,
            "refs": refs_added,
            "files_skipped": files_skipped,
            "files_removed": files_removed,
            "unsupported_skipped": unsupported_skipped,
            "schema_epoch": SCHEMA_EPOCH,
        }

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
        # LIMIT self.query_limit (cap+1, never the bare cap — see the class
        # docstring / F-1) on BOTH queries: this bounds the SQL work itself
        # (F-2 — previously `defs` had no LIMIT at all) and makes overflow
        # detectable rather than silently pre-truncated to an amount
        # indistinguishable from "nothing more exists".
        sql = "SELECT * FROM symbols WHERE name = ?"
        params: list[Any] = [name]
        if kind and kind != "any":
            sql += " AND kind = ?"
            params.append(kind)
        sql += " LIMIT ?"
        params.append(self.query_limit)
        defs = [dict(r) for r in self.db.execute(sql, params).fetchall()]

        refs = [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM refs WHERE callee_name = ? LIMIT ?", (name, self.query_limit)
            ).fetchall()
        ]

        return {"definitions": defs, "references": refs}

    def callers_of(self, symbol: str) -> list[dict[str, Any]]:
        # See search_symbol above: LIMIT self.query_limit (cap+1), not
        # unbounded (F-2) and not the bare cap (F-1).
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT path, line, enclosing FROM refs WHERE callee_name = ? LIMIT ?",
                (symbol, self.query_limit),
            ).fetchall()
        ]

    def query(self, dsl: str) -> dict[str, Any]:
        """Tiny DSL.

        Forms: 'callers_of:RecordVote#call',
        'subclasses_of:ApplicationRepository',
        'definitions_of:Tallier'.
        """
        verb = parse_query_verb(dsl)
        if not verb:
            return {"error": "INVALID_QUERY", "message": "Expected 'verb:argument' form."}
        if verb not in KNOWN_QUERY_VERBS:
            return {"error": "UNKNOWN_VERB", "message": f"Unknown verb {verb!r}."}
        _, _, arg = dsl.partition(":")
        arg = arg.strip()

        if verb == "callers_of":
            # Strip Class#method → method
            method = arg.split("#", 1)[-1] if "#" in arg else arg
            return {"edges": self.callers_of(method)}

        if verb == "definitions_of":
            return self.search_symbol(arg)

        if verb == "subclasses_of":
            # Best-effort; without inheritance edges, return classes whose
            # name appears near the parent. A real implementation extends
            # QUERIES with superclass capture.
            return {
                "edges": [],
                "warning": "subclasses_of requires extended index; not implemented in MVP.",
            }

        # Unreachable today (KNOWN_QUERY_VERBS has exactly the three
        # members dispatched above) — NOT dead-code hygiene, a guard
        # (NEW-3, checker second pass). A1 is expected to add verbs to
        # KNOWN_QUERY_VERBS; a verb added there without a corresponding
        # dispatch branch above must fail loudly here, not silently fall
        # through and impersonate subclasses_of's empty-with-warning shape
        # (which is exactly what an unconditional final `return` here
        # would do).
        raise NotImplementedError(
            f"verb {verb!r} is declared in KNOWN_QUERY_VERBS but has no "
            f"dispatch branch in CodeGraph.query() — add one before shipping it."
        )
