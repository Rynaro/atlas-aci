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
import json
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
# A1 (v2): captures whose pattern-level tag starts with `heritage.` (instead
# of `def.` or `ref.`) feed the materialized edge table's inheritance/mixin/
# construct relations (superclass | include | extend | prepend | construct
# — the last is Ruby's `Foo.new`) — see `_extract`. Each `heritage.<relation>`
# pattern captures exactly one `@target_name` node (the referenced
# class/module, pre-resolution); the *source* endpoint (which class/module
# declares the relation) is resolved afterwards from line ranges against
# `symbols`, not captured here (D1's "caller context from the edge source
# endpoint" — F10). Call patterns (`@ref.call`) additionally capture an
# optional `@receiver` node (the call's receiver/qualifier, if any) so
# `_call_qualification` can decide EXTRACTED vs INFERRED (D4/F18) without a
# second parse pass.
#
# Add languages as needed; the Ruby and Python queries below cover the common
# cases, and the web/markup grammars below cover static-site repos.
QUERIES: dict[str, str] = {
    "ruby": """
        (class name: (constant) @name) @def.class
        (module name: (constant) @name) @def.module
        (method name: (identifier) @name) @def.method
        (singleton_method name: (identifier) @name) @def.method
        (call receiver: (_)? @receiver method: (identifier) @callee) @ref.call
        (class
            superclass: (superclass
                [(constant) (scope_resolution)] @target_name)) @heritage.superclass
        (call
            method: (identifier) @_verb (#eq? @_verb "include")
            arguments: (argument_list
                [(constant) (scope_resolution)] @target_name)) @heritage.include
        (call
            method: (identifier) @_verb (#eq? @_verb "extend")
            arguments: (argument_list
                [(constant) (scope_resolution)] @target_name)) @heritage.extend
        (call
            method: (identifier) @_verb (#eq? @_verb "prepend")
            arguments: (argument_list
                [(constant) (scope_resolution)] @target_name)) @heritage.prepend
        (call
            receiver: [(constant) (scope_resolution)] @target_name
            method: (identifier) @_verb (#eq? @_verb "new")) @heritage.construct
    """,
    "python": """
        (class_definition name: (identifier) @name) @def.class
        (function_definition name: (identifier) @name) @def.function
        (call function: (identifier) @callee) @ref.call
        (call
            function: (attribute
                object: (_) @receiver
                attribute: (identifier) @callee)) @ref.call
        (class_definition
            superclasses: (argument_list (identifier) @target_name)) @heritage.superclass
        (class_definition
            superclasses: (argument_list
                (attribute attribute: (identifier) @target_name))) @heritage.superclass
    """,
    "typescript": """
        (class_declaration name: (type_identifier) @name) @def.class
        (function_declaration name: (identifier) @name) @def.function
        (method_definition name: (property_identifier) @name) @def.method
        (call_expression function: (identifier) @callee) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (property_identifier) @callee)) @ref.call
        (new_expression constructor: (identifier) @callee) @ref.call
        (class_declaration
            (class_heritage (extends_clause value: (identifier) @target_name))) @heritage.superclass
    """,
    "javascript": """
        (class_declaration name: (identifier) @name) @def.class
        (function_declaration name: (identifier) @name) @def.function
        (method_definition name: (property_identifier) @name) @def.method
        (call_expression function: (identifier) @callee) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (property_identifier) @callee)) @ref.call
        (new_expression constructor: (identifier) @callee) @ref.call
        (class_declaration (class_heritage (identifier) @target_name)) @heritage.superclass
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
SCHEMA_EPOCH = 3

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

-- Raw (pre-resolution) name references — call sites AND heritage
-- (superclass/include/extend/prepend/construct) sites alike. `enclosing`
-- (F10 / AC-A1-9) is DROPPED here: it was schema'd but always NULL
-- (codegraph.py history); caller context now comes from the materialized
-- `edges` table's source endpoint instead (AC-A1-10), computed from this
-- table plus `symbols` in `_resolve_edges`. `relation`/`qualified`/
-- `qualifier_name` are new in the v2 epoch (epoch 3 adds `qualifier_name` —
-- checker second-pass finding, see below): `relation` distinguishes a
-- method call from a heritage/construct reference. `qualified` is a
-- *structural grammar fact*, decided at extraction time with no resolution
-- needed — Ruby's grammar already distinguishes a constant/scope_resolution
-- receiver from a plain identifier, so this column is the final answer for
-- Ruby refs. `qualifier_name` (nullable) is the receiver's text (or the
-- bare callee's own text when there is no receiver) for Python/JS/TS calls,
-- whose grammars do NOT make that distinction structurally — qualification
-- for those two languages can only be decided once the *global* symbol
-- table exists (does `qualifier_name` resolve to a class/module symbol?),
-- so it is resolved in `_resolve_edges`, not at extraction time. NULL for
-- Ruby (nothing to look up: `qualified` above already holds the answer).
-- relation: call | superclass | include | extend | prepend | construct
-- qualified: Ruby's final syntactic fact (D4/F18); placeholder for others
-- qualifier_name: Python/JS/TS only, resolve-at-query-time qualifier text
CREATE TABLE IF NOT EXISTS refs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    callee_name    TEXT NOT NULL,
    relation       TEXT NOT NULL DEFAULT 'call',
    qualified      INTEGER NOT NULL DEFAULT 0,
    qualifier_name TEXT,
    path           TEXT NOT NULL,
    line           INTEGER NOT NULL,
    lang           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refs_callee ON refs(callee_name);

-- The materialized call/inheritance edge table (A1/D4/G-B) — the first
-- real edge set atlas-aci has ever had. Fully derived from `refs` +
-- `symbols` by `_resolve_edges` on every build (full or incremental), never
-- hand-authored (G-A). One row per *resolved* reference (candidate_count
-- >= 1); a zero-candidate reference never gets a row (AC-A1-6) and stays
-- only in `refs`. `confidence` is the deterministic 3-value enum (D4):
-- EXTRACTED (1 candidate, qualified), INFERRED (1 candidate, heuristic),
-- AMBIGUOUS (>1 candidates — never dropped, AC-A1-5). `target_*` carries
-- the single resolved endpoint for EXTRACTED/INFERRED; `candidates` is a
-- JSON array (totally ordered by path/line/name, AC-A1-8) of every matching
-- {path,line,name} candidate, populated for AMBIGUOUS edges only. AMBIGUOUS
-- edges are stored and returned by graph_query like any other edge
-- (AC-A1-2/AC-A1-5) — D4a's confident-subgraph exclusion (EXTRACTED union
-- INFERRED) is an analysis-time filter for the not-yet-built A2/A3, never a
-- storage-time drop; `confident_edges()` below is the query primitive A2/A3
-- will filter through. `relation` can be 'construct' (checker finding): a
-- bare `Foo(...)`/`new Foo()`/Ruby `Foo.new` whose name resolves entirely
-- to class/module symbols is a constructor invocation, not a method call —
-- kept semantically distinct rather than silently reused as 'call'.
CREATE TABLE IF NOT EXISTS edges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    relation      TEXT NOT NULL,      -- call | superclass | include | extend | prepend | construct
    source_path   TEXT NOT NULL,
    source_line   INTEGER NOT NULL,
    source_name   TEXT,               -- enclosing symbol name at the reference site
    source_kind   TEXT,               -- enclosing symbol kind at the reference site
    callee_name   TEXT NOT NULL,      -- the referenced name, pre-resolution
    confidence    TEXT NOT NULL,      -- EXTRACTED | INFERRED | AMBIGUOUS
    target_path   TEXT,               -- resolved single target (EXTRACTED/INFERRED only)
    target_line   INTEGER,
    target_name   TEXT,
    candidates    TEXT,               -- JSON array [{path,line,name}, ...]; AMBIGUOUS only
    lang          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_callee ON edges(callee_name);
CREATE INDEX IF NOT EXISTS idx_edges_target_name ON edges(target_name);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
CREATE INDEX IF NOT EXISTS idx_edges_confidence ON edges(confidence);

-- Rationale nodes (A4, phase P2) — comment-derived "why" annotations. This
-- is a *separate* relation from `refs` and the call/inheritance `edges`
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
EXPECTED_DDL_HASH = "ef64d212dfcac61f3d224fc30ec46e6c808450f521973d6475bb3dacfc945d9d"


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

# A1/D4 — edge-resolution candidate kinds, by relation.
#
# checker finding (BLOCKER): a bare `Foo(...)`/`new Foo()`/Ruby `Foo.new`
# constructor call resolves against a *class or module* symbol, never a
# callable one — the original `_CALL_CANDIDATE_KINDS` (callable-only)
# silently excluded every constructor call site from the graph (0 edges for
# `callers_of:CodeGraph` despite 51 real call sites), which is the omission
# of an entire symbol *kind*, not a truncation — worse than "never silently
# incomplete" demands. `_CALL_CANDIDATE_KINDS` is therefore the UNION of
# callable and class/module kinds: a "call"-relation reference now resolves
# against either, and `_resolve_edges` relabels the edge `relation` to
# 'construct' when every matched candidate turns out to be a class/module
# (see `_CLASS_TARGET_RELATIONS` below) — never silently reusing 'call' for
# a semantically distinct constructor edge.
_CALLABLE_KINDS: tuple[str, ...] = ("method", "function", "singleton_method")
_CLASS_KINDS: tuple[str, ...] = ("class", "module")
_CALL_CANDIDATE_KINDS: tuple[str, ...] = _CALLABLE_KINDS + _CLASS_KINDS

# Heritage relations (superclass/include/extend/prepend) plus 'construct'
# (Ruby's `.new`, captured directly as 'construct' at extraction time — see
# QUERIES["ruby"]'s heritage.construct pattern) all resolve exclusively
# against class/module symbols; a call that resolves entirely to callable
# symbols is never one of these.
_HERITAGE_RELATIONS: tuple[str, ...] = ("superclass", "include", "extend", "prepend")
_CLASS_TARGET_RELATIONS: tuple[str, ...] = (*_HERITAGE_RELATIONS, "construct")
_HERITAGE_CANDIDATE_KINDS: tuple[str, ...] = _CLASS_KINDS

# The relation set `callers_of` searches: a queried symbol might turn out to
# be a callable (ordinary 'call' edges) or a class/module (constructor
# 'construct' edges) — the caller doesn't know which in advance, and
# shouldn't have to.
_CALL_RELATIONS: tuple[str, ...] = ("call", "construct")


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
    """A raw (pre-resolution) name reference — a call site or a heritage/
    construct (superclass/include/extend/prepend/construct) site.
    ``enclosing`` is DROPPED (F10 / AC-A1-9): caller context is resolved
    after the fact, from the `edges` table's source endpoint, not carried
    on this dataclass.

    ``qualified`` is a *structural grammar fact* for Ruby — decided at
    extraction time, final, no resolution needed (Ruby's grammar already
    distinguishes a constant/scope_resolution receiver from a plain
    identifier). For Python/JS/TS it is a placeholder (their grammars don't
    make that distinction), superseded by ``qualifier_name``.

    ``qualifier_name`` (Python/JS/TS only, ``None`` for Ruby and for
    heritage/construct references) is the receiver's text — or the bare
    callee's own text when there's no receiver — for `_resolve_edges` to
    look up against ``symbols.kind IN ('class', 'module')`` once the global
    symbol table exists. Checker finding (MAJOR): capitalization is a
    *proxy* for "is this a class-bound name"; resolving against the symbol
    table the graph already has is the fact itself.
    """

    callee_name: str
    path: str
    line: int
    lang: str
    relation: str = "call"
    qualified: bool = False
    qualifier_name: str | None = None


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
                    "INSERT INTO refs(callee_name, relation, qualified, qualifier_name, "
                    "path, line, lang) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        r.callee_name,
                        r.relation,
                        int(r.qualified),
                        r.qualifier_name,
                        r.path,
                        r.line,
                        r.lang,
                    ),
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

        # A1/G-B: the edge table is pure derived data over `refs` + `symbols`
        # (both of which the loop above already keeps correct, incrementally
        # or fully) — so it is always cheaply recomputed from scratch here,
        # on every build call, rather than incrementally patched. This is
        # what lets `--since` stay correct even though a changed file in
        # isolation cannot know its own candidate count (that requires the
        # *global* symbol table — G-B/D4).
        edges_added = self._resolve_edges(conn)

        conn.execute(
            "INSERT OR REPLACE INTO manifest(key, value) VALUES ('epoch', ?)",
            (str(SCHEMA_EPOCH),),
        )
        conn.commit()
        return {
            "files_indexed": files_indexed,
            "symbols": symbols_added,
            "refs": refs_added,
            "edges": edges_added,
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
        from its ``@name``; a match carrying a ``heritage.<relation>`` tag
        yields an inheritance/mixin/construct Reference (A1); a match carrying
        ``@callee`` yields a call Reference, with `_call_qualification` (D4/
        F18) deciding its `qualified`/`qualifier_name` fields from the same
        match's optional ``@receiver`` capture — no second parse pass.
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
            heritage_cap = next((c for c in caps if c.startswith("heritage.")), None)
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
            elif heritage_cap is not None:
                # A1/D4: superclass | include | extend | prepend | construct
                # (the last is Ruby's `Foo.new` — checker finding). The
                # referenced name (`@target_name`) is, by grammar
                # construction, always a constant/class/import-style
                # reference in every shipped language — heritage/construct
                # references are unconditionally type-qualified
                # (`qualified=True`), mirroring F18's "a constant receiver
                # ... = type-qualified" rule applied to the heritage slot
                # itself rather than a call receiver.
                target_nodes = caps.get("target_name")
                if not target_nodes:
                    continue
                relation = heritage_cap.split(".", 1)[1]
                for node in target_nodes:
                    name = self._node_text(source, node).strip()
                    if not name:
                        continue
                    refs.append(
                        Reference(
                            callee_name=name,
                            path=rel_path,
                            line=node.start_point[0] + 1,
                            lang=lang,
                            relation=relation,
                            qualified=True,
                        )
                    )
            elif "callee" in caps:
                qualified, qualifier_name = self._call_qualification(lang, source, caps)
                for node in caps["callee"]:
                    name = self._node_text(source, node).strip()
                    if not name:
                        continue
                    refs.append(
                        Reference(
                            callee_name=name,
                            path=rel_path,
                            line=node.start_point[0] + 1,
                            lang=lang,
                            relation="call",
                            qualified=qualified,
                            qualifier_name=qualifier_name,
                        )
                    )

        return symbols, refs

    @staticmethod
    def _call_qualification(
        lang: str, source: bytes, caps: dict[str, list[Any]]
    ) -> tuple[bool, str | None]:
        """The D4/F18 syntactic type-qualification rule for a call reference.

        Returns ``(qualified, qualifier_name)``.

        Ruby's grammar already distinguishes constant receivers from plain
        identifiers at the *node-type* level (`constant` / `scope_resolution`
        vs `identifier` / `self`) — a real structural fact, not a proxy — so
        Ruby's `qualified` is final here and `qualifier_name` is always
        `None` (nothing left to resolve): F18 pins "a constant receiver or
        `::` scope resolution ... = type-qualified; a local-variable
        receiver ... / a bare method = INFERRED".

        Python and JS/TS grammars do NOT make that distinction structurally
        (a class name and a local variable are both plain `identifier`
        nodes). Checker finding (MAJOR): a prior version of this method used
        capitalization as a *proxy* for "is this a class-bound name" — but
        the graph already stores the fact (`symbols.kind = 'class'/'module'`)
        once the build's global symbol table exists, and capitalization is
        neither necessary (a real lowercase-named class was mis-tiered
        INFERRED) nor sufficient (a capitalized local variable or a
        capitalized *function* was mis-tiered EXTRACTED) for that fact. So
        for these two languages, `qualified` returned here is a placeholder
        (always `False`) and `qualifier_name` carries the receiver's text —
        or the bare callee's own text when there's no receiver — for
        `_resolve_edges` to resolve against the symbol table once it exists
        (a lookup, not a guess; still zero-LLM, AC-NEG-3). `self`/`this`
        never resolve to a class/module symbol (no code defines a class
        literally named `self`), so they correctly stay unqualified without
        any special-casing — preserving F18's explicit `self.bar`/`this.bar`
        = INFERRED worked example.
        """
        receiver_nodes = caps.get("receiver")
        if lang == "ruby":
            if not receiver_nodes:
                return False, None
            return receiver_nodes[0].type in ("constant", "scope_resolution"), None

        if receiver_nodes:
            qualifier_name = CodeGraph._node_text(source, receiver_nodes[0]).strip()
        else:
            callee_nodes = caps.get("callee") or []
            qualifier_name = (
                CodeGraph._node_text(source, callee_nodes[0]).strip() if callee_nodes else ""
            )
        return False, (qualifier_name or None)

    @staticmethod
    def _node_text(source: bytes, node) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    # ---- Edge resolution (A1/D4/G-B) ----

    def _resolve_edges(self, conn: sqlite3.Connection) -> int:
        """Materialize the v2 call/inheritance edge table from the raw
        `refs` + `symbols` relations `_run_build` just brought up to date.

        Always a full recompute (`DELETE FROM edges` then re-derive): the DB
        is pure derived data (G-A), and candidate resolution is inherently
        *global* — a single changed file cannot know its own candidate
        count without seeing every other file's symbols — so incrementally
        patching `edges` per changed file would either be wrong or require
        tracking a full dependency graph. Recomputing this cheap, SQL-only
        pass from the already-correct `refs`/`symbols` tables is both
        simpler and correct by construction, on every build call (full or
        `--since`).

        Partitions strictly on candidate count (D4), never an LLM path
        (AC-NEG-3):
          - 0 candidates -> no edge; the reference stays an unresolved name
            in `refs` (AC-A1-6).
          - 1 candidate  -> EXTRACTED if the reference was type-qualified,
            else INFERRED.
          - >1 candidates -> AMBIGUOUS, with the full ordered `candidates[]`
            attached (AC-A1-5) — never dropped, unlike graphify's silent
            edge-drop guard.
        Candidate rows (and therefore `candidates[]`) are always fetched
        `ORDER BY path, line_start, name` — a fixed total order for
        identical input (AC-A1-8), independent of SQLite rowid/insertion
        order (D6 foundation).

        Qualification (checker finding, MAJOR): for a Ruby ref, `qualified`
        is already the final answer (a structural grammar fact set at
        extraction time — see `_call_qualification`). For a Python/JS/TS
        ref, `qualifier_name` names a symbol to resolve *now*, against the
        symbol table that only fully exists at this point in the build: is
        it a known `class`/`module`? That resolution — not capitalization —
        decides EXTRACTED vs INFERRED for those two languages.

        Constructor calls (checker finding, BLOCKER): a `call`-relation
        reference resolves against callable *and* class/module candidates
        (`_CALL_CANDIDATE_KINDS`) — a bare `Foo(...)`/`new Foo()` naming a
        local class was previously invisible (0 candidates against a
        callable-only kind filter). When every matched candidate is a
        class/module, the edge is relabeled `relation='construct'` — a
        semantically distinct edge, never silently reused as `'call'`.
        """
        conn.execute("DELETE FROM edges")
        edges_added = 0

        ref_rows = conn.execute(
            "SELECT callee_name, relation, qualified, qualifier_name, path, line, lang "
            "FROM refs ORDER BY path, line, callee_name"
        ).fetchall()

        for ref in ref_rows:
            relation = ref["relation"]
            if relation in _CLASS_TARGET_RELATIONS:
                candidate_kinds = _HERITAGE_CANDIDATE_KINDS
            else:
                candidate_kinds = _CALL_CANDIDATE_KINDS
            placeholders = ",".join("?" for _ in candidate_kinds)
            candidates = conn.execute(
                f"SELECT name, path, line_start, kind FROM symbols WHERE name = ? "
                f"AND kind IN ({placeholders}) ORDER BY path, line_start, name",
                (ref["callee_name"], *candidate_kinds),
            ).fetchall()

            if not candidates:
                continue  # AC-A1-6: zero candidates — no edge, ever.

            source_name, source_kind = self._enclosing_symbol(conn, ref["path"], ref["line"])

            effective_relation = relation
            if relation == "call" and all(c["kind"] in _CLASS_KINDS for c in candidates):
                # Every candidate is a class/module: this "call" is really a
                # constructor invocation (Python/JS `Foo(...)`/`new Foo()`;
                # Ruby's `.new` is already tagged 'construct' at extraction
                # and never reaches this branch with relation == "call").
                effective_relation = "construct"

            if effective_relation == "construct":
                # A construct edge's `callee_name` (not its receiver, if any)
                # IS the class/module being instantiated — that is exactly
                # what "every candidate is class-kind" already established.
                # Whether it was reached bare (`Foo()`), via `new Foo()`, or
                # via a module-qualified attribute chain
                # (`some_module.Foo()`) doesn't change WHAT is being
                # constructed, so the receiver's own qualification is the
                # wrong question here — unconditionally qualified, mirroring
                # Ruby's heritage/construct captures (always qualified=True
                # at extraction, same reasoning applied at resolution time).
                qualified = True
            elif ref["qualifier_name"] is not None:
                # Python/JS/TS ordinary method calls: resolve the fact,
                # don't guess it from case.
                qualifier_hit = conn.execute(
                    "SELECT 1 FROM symbols WHERE name = ? AND kind IN ('class', 'module') LIMIT 1",
                    (ref["qualifier_name"],),
                ).fetchone()
                qualified = qualifier_hit is not None
            else:
                qualified = bool(ref["qualified"])

            if len(candidates) == 1:
                confidence = "EXTRACTED" if qualified else "INFERRED"
                target = candidates[0]
                target_path = target["path"]
                target_line = target["line_start"]
                target_name = target["name"]
                candidates_json = None
            else:
                confidence = "AMBIGUOUS"
                target_path = target_line = target_name = None
                candidates_json = json.dumps(
                    [
                        {"path": c["path"], "line": c["line_start"], "name": c["name"]}
                        for c in candidates
                    ]
                )

            conn.execute(
                "INSERT INTO edges(relation, source_path, source_line, source_name, "
                "source_kind, callee_name, confidence, target_path, target_line, "
                "target_name, candidates, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    effective_relation,
                    ref["path"],
                    ref["line"],
                    source_name,
                    source_kind,
                    ref["callee_name"],
                    confidence,
                    target_path,
                    target_line,
                    target_name,
                    candidates_json,
                    ref["lang"],
                ),
            )
            edges_added += 1

        return edges_added

    @staticmethod
    def _enclosing_symbol(
        conn: sqlite3.Connection, path: str, line: int
    ) -> tuple[str | None, str | None]:
        """The innermost symbol in `path` whose ``[line_start, line_end]``
        range contains `line` — the F10 replacement for the always-NULL
        `refs.enclosing`: caller context is derived from the materialized
        edge's source endpoint instead of stored redundantly on every ref
        (AC-A1-9/AC-A1-10). "Innermost" = smallest line span; ties are
        broken by the latest start line, then by name, for a fixed
        deterministic result — in practice two symbols in the same file
        cannot legitimately share an identical span, so ties never bite.
        Returns ``(None, None)`` when the reference sits outside every known
        symbol's range (e.g. a Ruby top-level call) — a real, not-hidden,
        "no enclosing definition" fact, not an error.
        """
        row = conn.execute(
            "SELECT name, kind FROM symbols WHERE path = ? AND line_start <= ? AND line_end >= ? "
            "ORDER BY (line_end - line_start) ASC, line_start DESC, name ASC LIMIT 1",
            (path, line, line),
        ).fetchone()
        if row is None:
            return None, None
        return row["name"], row["kind"]

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
        """A1: queries the materialized `edges` table (relation IN
        ('call', 'construct')), replacing the pre-A1 `refs`-by-name-string
        join. Response shape change (F10/AC-A1-9/AC-A1-10): each edge now
        carries caller context as a `source` object (`{path, line, name,
        kind}`, resolved from the edge's source endpoint) instead of the
        old, always-null `enclosing` string — see `_edge_row_to_dict`.

        Includes `construct` (checker finding, BLOCKER): `symbol` might be
        an ordinary callable (ordinary `call` edges) or a class/module
        constructed via a bare `Foo(...)`/`new Foo()`/Ruby `Foo.new` — the
        caller doesn't know which in advance, and `callers_of:SomeClass`
        must not silently come back empty just because the symbol happens
        to be a class.
        """
        return self._edges_for(symbol, _CALL_RELATIONS)

    def subclasses_of(self, symbol: str) -> list[dict[str, Any]]:
        """A1 (AC-A1-7): resolves real inheritance/mixin edges — Ruby
        `superclass`/`include`/`extend`/`prepend` plus the `superclass`
        equivalent already captured for Python and JS/TS — retiring the
        empty stub-with-warning. Aggregates all heritage relations under
        one verb, matching the DSL's single `subclasses_of:Class` form; a
        Rails engine leaning on `concerns/` mixins (D3a's stated Solidus/
        Spree target) surfaces through `include`/`extend`/`prepend` here
        exactly as it would through `superclass`.
        """
        return self._edges_for(symbol, _HERITAGE_RELATIONS)

    def confident_edges(self) -> list[dict[str, Any]]:
        """The confident subgraph (EXTRACTED union INFERRED — D4a) as a
        cheap, ready-made query: the object A2's degree-centrality god
        nodes, A3's community detection, and the D3a probe are all
        specified to consume (none of those are built by A1 — this is the
        storage+retrieval primitive they will filter through). AMBIGUOUS is
        excluded here — never fanned out to its candidates, never given
        fractional weight (AC-NEG-7) — but this is an *analysis-time*
        filter, not a storage-time drop: AMBIGUOUS edges remain in `edges`
        and are still returned in full by `callers_of`/`subclasses_of`
        (D4a, AC-A1-2/AC-A1-5 preserved).
        """
        rows = self.db.execute(
            "SELECT * FROM edges WHERE confidence IN ('EXTRACTED', 'INFERRED') "
            "ORDER BY source_path, source_line, callee_name"
        ).fetchall()
        return [self._edge_row_to_dict(r) for r in rows]

    def _edges_for(self, callee_name: str, relations: tuple[str, ...]) -> list[dict[str, Any]]:
        """Shared query path for `callers_of`/`subclasses_of`: both filter
        the materialized `edges` table by the pre-resolution `callee_name`
        string (mirroring the pre-A1 `callers_of`'s own name-string filter)
        and a relation set. Returns *every* matching edge regardless of
        confidence, including AMBIGUOUS with its `candidates[]` attached —
        D4a's confident-subgraph exclusion applies to `confident_edges`
        (A2/A3's future analysis input), never to what `graph_query` itself
        returns (AC-A1-5). LIMIT self.query_limit (cap+1, never the bare
        cap) bounds SQL work the same way search_symbol/callers_of already
        did pre-A1 (F-1/F-2) — the central bounds chokepoint (server.py)
        still truncates-and-flags the returned list at the response layer.
        """
        placeholders = ",".join("?" for _ in relations)
        rows = self.db.execute(
            f"SELECT * FROM edges WHERE callee_name = ? AND relation IN ({placeholders}) "
            f"ORDER BY source_path, source_line, callee_name LIMIT ?",
            (callee_name, *relations, self.query_limit),
        ).fetchall()
        return [self._edge_row_to_dict(r) for r in rows]

    def unresolved_ref_count(self, callee_name: str, relations: tuple[str, ...]) -> int:
        """Checker finding (MAJOR): an empty `edges` list is otherwise
        indistinguishable between "genuinely zero callers" and "callers
        exist but none resolved" (e.g. an external/gem method with no local
        definition) — exactly the confusion that hid the `callers_of:
        CodeGraph` bug (a whole symbol *kind* silently excluded, surfacing
        as a plausible-looking empty answer). Counts raw `refs` matching
        `callee_name`/`relations` that produced no `edges` row.

        A given `(callee_name, relation-class)` pair resolves uniformly —
        `_resolve_edges` computes the same candidate set for every ref
        sharing that pair, so it is never the case that *some* matching
        refs resolve and others don't: either every one of them became an
        edge, or none did. `relations` is passed straight through as the
        edges-side filter too (Python/JS/TS constructor calls are stored
        as `refs.relation='call'` but may be relabeled `'construct'` in
        `edges`, so counting edges over the same `relations` tuple the
        caller queried — e.g. `_CALL_RELATIONS` — accounts for both without
        the caller needing to know which raw refs became which edge kind).
        """
        placeholders = ",".join("?" for _ in relations)
        total_refs = self.db.execute(
            f"SELECT COUNT(*) FROM refs WHERE callee_name = ? AND relation IN ({placeholders})",
            (callee_name, *relations),
        ).fetchone()[0]
        total_edges = self.db.execute(
            f"SELECT COUNT(*) FROM edges WHERE callee_name = ? AND relation IN ({placeholders})",
            (callee_name, *relations),
        ).fetchone()[0]
        return max(total_refs - total_edges, 0)

    @staticmethod
    def _edge_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Shapes one `edges` row into the graph_query response contract
        (AC-A1-10). `source` replaces the dropped `refs.enclosing` (F10)
        with real caller context from the materialized edge's own source
        endpoint. `target` is the single resolved endpoint for
        EXTRACTED/INFERRED; `candidates` is the full ordered candidate set
        (R-1/AC-A1-5/AC-A1-8) for AMBIGUOUS — the two are mutually
        exclusive by construction (`_resolve_edges` never populates both).
        """
        edge: dict[str, Any] = {
            "relation": row["relation"],
            "confidence": row["confidence"],
            "source": {
                "path": row["source_path"],
                "line": row["source_line"],
                "name": row["source_name"],
                "kind": row["source_kind"],
            },
        }
        if row["confidence"] == "AMBIGUOUS":
            edge["target"] = None
            edge["candidates"] = json.loads(row["candidates"]) if row["candidates"] else []
        else:
            edge["target"] = {
                "path": row["target_path"],
                "line": row["target_line"],
                "name": row["target_name"],
            }
            edge["candidates"] = None
        return edge

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
            # `unresolved_refs` (checker finding, MAJOR): distinguishes a
            # genuinely-empty answer from an incomplete one — see
            # `unresolved_ref_count`.
            return {
                "edges": self.callers_of(method),
                "unresolved_refs": self.unresolved_ref_count(method, _CALL_RELATIONS),
            }

        if verb == "definitions_of":
            return self.search_symbol(arg)

        if verb == "subclasses_of":
            # A1 (AC-A1-7): real inheritance/mixin edges, replacing the MVP
            # empty-stub-with-warning.
            return {
                "edges": self.subclasses_of(arg),
                "unresolved_refs": self.unresolved_ref_count(arg, _HERITAGE_RELATIONS),
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
