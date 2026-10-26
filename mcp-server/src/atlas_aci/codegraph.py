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
import importlib.metadata
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS, path_is_within
from atlas_aci.extractors import rust
from atlas_aci.indexing import extraction_cache as xc
from atlas_aci.label_propagation import label_propagation
from atlas_aci.providers import python_imports
from atlas_aci.resolvers import dependencies as dep
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import snapshots as snap

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
# checker finding (MAJOR-1, condition 5): `@assign.target` (Python/JS/TS
# only — Ruby doesn't need it, see `_resolve_edges`'s shadowing-guard
# comment) captures the LHS identifier of a plain assignment
# (`Foo = ...`) / variable declarator (`let`/`const`/`var Foo = ...`).
# `_extract` collects these into a per-file name set persisted in
# `assignment_targets`, consulted at resolution time to demote a qualifier
# match to INFERRED when the same name is ALSO a local variable in that
# file (`Config = load_config(); Config.reload()` must not assert EXTRACTED
# certainty the resolver doesn't have). Deliberately narrow: only a plain
# identifier target is captured (tuple-unpacking, attribute assignment,
# and augmented assignment are out of scope) — a cheap, best-effort
# shadowing signal, not a full scope analysis.
#
# A4 (v2, P2): `@comment.rationale` captures every `comment`-kind node —
# tree-sitter's own grammar-level node type, verified identical (`comment`)
# across ruby/python/javascript/typescript, distinct from `string`/
# `string_literal` nodes at the grammar level. This is what structurally
# guarantees a `# NOTE:`-looking substring INSIDE a string literal is never
# captured here at all (confirmed empirically: zero matches querying
# `(comment) @c` against fixtures with rationale-shaped text inside string/
# template literals) — not a filter applied after the fact, a parse-tree
# fact. `_extract` decides, per-language, whether a captured comment's OWN
# text is a "recognized rationale-prefixed comment" (D5/AC-A4-1/AC-A4-2);
# a `comment.*` tag is deliberately NOT `def.*` — `PRODUCED_KINDS` (derived
# mechanically from `@def\.(\w+)`) can therefore never contain "rationale",
# which is what keeps a rationale comment permanently out of `symbols` and
# so out of every call/inheritance resolution candidate set (AC-A4-4) —
# the same class of guarantee AC-NEG-7 gives AMBIGUOUS, structural rather
# than a second filter that could silently drift. D5/AC-A4-5: this capture
# is added ONLY to the four code languages below (Ruby, Python, JS, TS, in
# that order) — never to scss/html/yaml/markdown/bash.
#
# Add languages as needed; the Ruby and Python queries below cover the common
# cases, and the web/markup grammars below cover static-site repos.
#
# checker MINOR-1 + the PRODUCED_KINDS sweep it prompted (the same class of
# bug, generalized): `(method name: (identifier) @name)` silently missed
# every Ruby method definition whose NAME is not a plain `identifier` node at
# the grammar level. tree-sitter-ruby wraps a setter method's name
# (`def foo=`) in a `setter` node (`identifier` + `=` token) and an operator
# overload's name (`def +`/`def []`/`def []=`/`def ==`/...) in an `operator`
# node — neither is an `identifier`, so BOTH kinds of definition were
# entirely invisible to `symbols` (not just mis-attributed — absent), which
# also meant `callers_of`/rationale-target resolution could never see them
# as an enclosing scope. Verified via a raw tree-sitter parse of
# `def foo=(x)`/`def +(other)`/`def [](i)`/`def []=(i, v)` before adding
# these — each produces a `setter`/`operator` name node, confirmed empirically
# (this is the same "close the class, not the instance" fix `_CALL_CANDIDATE_
# KINDS`/`PRODUCED_KINDS` already established elsewhere in this file). The
# `setter`/`operator` node's own text (`"foo="`, `"+"`, `"[]="`) is captured
# as `@name` — the correct, conventional Ruby name for these definitions.
QUERIES: dict[str, str] = {
    "rust": rust.QUERY,
    "ruby": """
        (class name: (constant) @name) @def.class
        (class name: (scope_resolution) @name
            (#match? @name "^[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)+$")) @def.class
        (module name: (constant) @name) @def.module
        (module name: (scope_resolution) @name
            (#match? @name "^[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)+$")) @def.module
        (method name: (identifier) @name) @def.method
        (method name: (setter) @name) @def.method
        (method name: (operator) @name) @def.method
        (singleton_method name: (identifier) @name) @def.method
        (singleton_method name: (setter) @name) @def.method
        (singleton_method name: (operator) @name) @def.method
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
        (comment) @comment.rationale
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
        (assignment left: (identifier) @assign.target)
        (comment) @comment.rationale
    """,
    "typescript": """
        (class_declaration name: (type_identifier) @name) @def.class
        (function_declaration name: (identifier) @name) @def.function
        (method_definition name: (property_identifier) @name) @def.method
        (method_definition name: (private_property_identifier) @name) @def.method
        (call_expression function: (identifier) @callee) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (property_identifier) @callee)) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (private_property_identifier) @callee)) @ref.call
        (new_expression constructor: (identifier) @callee) @ref.call
        (class_declaration
            (class_heritage (extends_clause value: (identifier) @target_name))) @heritage.superclass
        (variable_declarator name: (identifier) @assign.target)
        (assignment_expression left: (identifier) @assign.target)
        (comment) @comment.rationale
    """,
    "javascript": """
        (class_declaration name: (identifier) @name) @def.class
        (function_declaration name: (identifier) @name) @def.function
        (method_definition name: (property_identifier) @name) @def.method
        (method_definition name: (private_property_identifier) @name) @def.method
        (call_expression function: (identifier) @callee) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (property_identifier) @callee)) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (private_property_identifier) @callee)) @ref.call
        (new_expression constructor: (identifier) @callee) @ref.call
        (class_declaration (class_heritage (identifier) @target_name)) @heritage.superclass
        (variable_declarator name: (identifier) @assign.target)
        (assignment_expression left: (identifier) @assign.target)
        (comment) @comment.rationale
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
UNSUPPORTED_LANGS: frozenset[str] = frozenset({"tsx", "go", "java"})

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
SCHEMA_EPOCH = 8
# Exchange records carry no native indexes; preserve their established epoch.
PORTABLE_SCHEMA_EPOCH = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- class | module | method | function | ...
    path        TEXT NOT NULL,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    lang        TEXT NOT NULL,
    binding     TEXT NOT NULL DEFAULT '{}'
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
-- col: 0-based column of the reference site (checker MINOR-4, epoch 4) —
--   exists ONLY to give `_edges_for`/`confident_edges` a genuine total-order
--   tiebreak (see `edges.source_col` below); never read for resolution.
CREATE TABLE IF NOT EXISTS refs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    callee_name    TEXT NOT NULL,
    relation       TEXT NOT NULL DEFAULT 'call',
    qualified      INTEGER NOT NULL DEFAULT 0,
    qualifier_name TEXT,
    path           TEXT NOT NULL,
    line           INTEGER NOT NULL,
    col            INTEGER NOT NULL DEFAULT 0,
    lang           TEXT NOT NULL,
    binding        TEXT NOT NULL DEFAULT '{}',
    unresolved_reason TEXT
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
--
-- `source_col` (checker MINOR-4, epoch 4): `_edges_for`/`confident_edges`
-- previously ordered `(source_path, source_line, callee_name)` — two
-- references to the SAME callee on the SAME line (`foo(foo())`) tie, and a
-- tie with no further ORDER BY key falls back to SQLite's rowid/insertion
-- order: same-machine deterministic (a rebuild always inserts in the same
-- ref-processing order), so AC-A1-8 held, but NOT a genuine total order —
-- exactly what D6's byte-deterministic export (AC-A5-7/AC-REL-1) would
-- have discovered on the cross-OS release gate, the worst place to find
-- it. `source_col` (the reference site's own 0-based column — two distinct
-- call sites can share a line but never a column) closes the tie
-- explicitly instead of relying on incidental rowid ordering.
CREATE TABLE IF NOT EXISTS edges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    relation      TEXT NOT NULL,      -- call | superclass | include | extend | prepend | construct
    source_path   TEXT NOT NULL,
    source_line   INTEGER NOT NULL,
    source_col    INTEGER NOT NULL DEFAULT 0,  -- total-order tiebreak only (MINOR-4)
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

-- checker finding (MAJOR-1, epoch 5): every plain-identifier assignment
-- target (`Foo = ...` / `let`|`const`|`var Foo = ...`) captured per file,
-- Python/JS/TS only (see QUERIES' `@assign.target` comment; Ruby's
-- grammar makes a constant receiver lexically un-shadowable by a local
-- variable, so it never needs this). `_resolve_edges` consults this before
-- crediting a `qualifier_name` match against `symbols.kind IN ('class',
-- 'module')` as EXTRACTED: if the SAME name is ALSO assigned to as a local
-- variable in the SAME file, the resolver cannot rule out shadowing
-- (`Config = load_config(); Config.reload()`), so it demotes to INFERRED —
-- an honest "I don't know for certain" rather than a confident, possibly
-- wrong, EXTRACTED. Purely a resolution-time input; never queried by
-- graph_query directly.
CREATE TABLE IF NOT EXISTS assignment_targets (
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'root',
    binding_kind TEXT NOT NULL DEFAULT 'assignment',
    byte_start INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_assignment_targets_path_name ON assignment_targets(path, name);
"""


LEGACY_SCHEMA = SCHEMA
LEGACY_DDL_HASH = "52f933ea7a2a38074fb436d473b2caedb1e8b439584483077896e0e92ea622c3"
SCHEMA += (
    """
CREATE TABLE IF NOT EXISTS import_bindings (
    path TEXT NOT NULL, local_name TEXT NOT NULL, scope TEXT NOT NULL,
    byte_start INTEGER NOT NULL, description TEXT NOT NULL, lang TEXT NOT NULL,
    source_entity_id TEXT, occurrence_id TEXT, evidence_id TEXT, assertion_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_import_binding_scope ON import_bindings(path, scope, local_name);
"""
    + ev.DDL
)
for _table, _columns in ev.LINK_COLUMNS.items():
    if _table == "import_bindings":
        continue
    _pattern = r"(CREATE TABLE IF NOT EXISTS " + _table + r" \(.*?)(\n\);)"
    SCHEMA = re.sub(
        _pattern,
        r"\g<1>,\n" + ",\n".join("    " + column + " TEXT" for column in _columns) + r"\g<2>",
        SCHEMA,
        flags=re.S,
    )


def ddl_hash(ddl: str) -> str:
    """SHA-256 hex digest of a schema DDL string (AC-H-12)."""
    return hashlib.sha256(ddl.encode("utf-8")).hexdigest()


# Hand-maintained — see the SCHEMA_EPOCH docstring above. Do NOT replace this
# with `ddl_hash(SCHEMA)`; that would make the pairing test a tautology.
PREVIOUS_DDL_HASH = "23aee629ec89a6bd420ed3719e2e42de9d0a456b52e1b29aa3760bc05a0eb26a"
EXPECTED_DDL_HASH = "0058d56574c5f1d19feb475f04dd2f4af2b93519b26dcfc78a36233664ca8731"
SQLITE_SCHEMA_HASHES = {
    6: "2a40eed9dbb5f1f4d913a857895b1dfc8ac7daa3edf4e9e033d02084b7de1475",
    7: "9536086b279a9dc5121c3a853f8a1d5c151393db1e6a4f0104f3b8a72b6c3a79",
    8: "87d603375b7d0a4ce26949e720e2d5aaf1e90f1b68bc590b890ebdec5e0b535b",
}


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
KNOWN_QUERY_VERBS: frozenset[str] = frozenset(
    {"callers_of", "definitions_of", "subclasses_of", "god_nodes", "communities", "rationale"}
)

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
#
# checker finding (MINOR-1, condition 2): `mixin` (scss `@include foo` — see
# QUERIES["scss"]'s `@ref.include` pattern, extracted as an ordinary 'call'-
# relation ref) was the *next* excluded kind — same shape as the BLOCKER,
# just non-silent this time (`unresolved_refs` surfaced it instead of hiding
# it). The fix is not "add mixin and move on": every kind `QUERIES` can
# actually produce (`PRODUCED_KINDS`, derived mechanically from the query
# table, same idiom as AC-DOC-6/AC-H-13) must be classified into EXACTLY one
# of the three sets below — callable, class-target, or deliberately
# excluded with a reason — so forgetting a kind is a listed decision, never
# an oversight. test_codegraph.py::test_produced_kinds_are_fully_classified
# fails the build if a kind falls through every bucket, mirroring H4's
# LANG_BY_EXT-vs-QUERIES consistency assert for extensions.
#
# `singleton_method` (never actually a kind value — Ruby's
# `(singleton_method ...) @def.method` pattern stores it as plain "method",
# same as an instance method) has been removed: it could never appear in
# `symbols.kind` and its presence here was dead weight, not a documented
# decision.
_CALLABLE_KINDS: tuple[str, ...] = ("method", "function", "mixin")
_CLASS_KINDS: tuple[str, ...] = ("class", "module")
# Deliberately excluded from call/construct resolution: QUERIES never
# captures a call/construct-relation reference whose callee_name could
# match one of these — they are definition-only lookup targets for
# search_symbol (an HTML anchor id, a YAML mapping key, a Markdown heading,
# an SCSS class/id selector or `$variable`), never something another
# reference "calls". If a future QUERIES change adds a capture that
# references one of these by name, this set — and the classification test
# — must be revisited.
_NON_CALLABLE_KINDS: tuple[str, ...] = (
    "struct",
    "enum",
    "trait",
    "heading",
    "id",
    "key",
    "placeholder",
    "selector",
    "variable",
)
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

# checker finding (MINOR-3, condition 4): `edges.relation` had no closed-set
# guarantee analogous to `confidence`'s (AC-A1-2) — a future
# `@heritage.<newrelation>` capture or a new `effective_relation` branch in
# `_resolve_edges` could silently introduce a fourth+ relation value with
# nothing to catch it. `confidence` itself is pinned by TEST alone (no
# CHECK constraint in SCHEMA — see `test_every_call_inheritance_edge_
# confidence_in_closed_enum`), so `relation` is pinned the same way here,
# for consistency, rather than adding a CHECK constraint (which would be a
# real DDL/SCHEMA_EPOCH change for a guarantee the sibling column doesn't
# have either). `test_codegraph.py::test_edge_relation_is_a_closed_set`
# is the enforcement point; this frozenset is what it checks against.
KNOWN_EDGE_RELATIONS: frozenset[str] = frozenset({"call", *_CLASS_TARGET_RELATIONS})

# A3: a bound on `CodeGraph.communities()`'s label-propagation loop.
# Deterministic asynchronous LPA with fixed tie-breaking is not
# mathematically GUARANTEED to converge (real-world graphs occasionally
# oscillate between two label configurations), so this caps runtime
# without affecting a converging graph — a run that reaches the cap simply
# stops on whatever labels the last pass produced, which is still a
# deterministic function of the input (the loop itself has no randomness).
_LPA_MAX_ITERATIONS = 100


@dataclass
class Symbol:
    name: str
    kind: str
    path: str
    line_start: int
    line_end: int
    lang: str
    binding: str = "{}"
    entity_id: str | None = None
    occurrence_id: str | None = None


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

    ``col`` (checker MINOR-4) is the reference site's 0-based column —
    total-order-tiebreak-only, never read for resolution (see the `edges.
    source_col` DDL comment).
    """

    callee_name: str
    path: str
    line: int
    lang: str
    col: int = 0
    relation: str = "call"
    qualified: bool = False
    qualifier_name: str | None = None
    binding: str = "{}"
    source_entity_id: str | None = None
    occurrence_id: str | None = None
    evidence_id: str | None = None


@dataclass
class RationaleComment:
    """A4 (D5): one recognized rationale comment, pre-resolution — its
    enclosing scope (the ``rationale_for`` edge target) is resolved
    afterwards by line-range containment against ``symbols``, the same
    "resolve from source, don't carry it on the dataclass" discipline
    F10/AC-A1-9 established for call/heritage references. ``label`` is the
    canonicalized ADR/RFC identifier (AC-A4-3, JS/TS only) or ``None``.
    """

    path: str
    line: int
    text: str
    label: str | None
    lang: str
    occurrence_id: str | None = None
    evidence_id: str | None = None
    target_entity_id: str | None = None


# A4/D5 — the rationale-comment marker vocabulary, ported from graphify's
# prefix set (AC-A4-2) for the two comment syntaxes this project's four
# code languages use: `#`-line comments (Ruby, Python) and `//`-line /
# `/* ... */`-block comments (JS, TS). graphify has no Ruby extractor to
# port (the scout report found prefix lists only for Python and JS/TS) —
# Ruby reuses the SAME marker words over the SAME `#` syntax Python uses,
# a disclosed judgment call (D5 orders Ruby first; there is no other
# reference implementation to diverge from). Exact-prefix, case-sensitive
# matching (mirrors graphify's literal `str.startswith` tuples) — not a
# regex-anywhere-in-the-comment search, so "see the WHY: field below" in
# ordinary prose doesn't falsely trigger.
_RATIONALE_MARKERS: tuple[str, ...] = (
    "NOTE",
    "IMPORTANT",
    "HACK",
    "WHY",
    "RATIONALE",
    "TODO",
    "FIXME",
)
_HASH_RATIONALE_PREFIXES: tuple[str, ...] = tuple(f"# {m}:" for m in _RATIONALE_MARKERS)
_SLASH_RATIONALE_PREFIXES: tuple[str, ...] = tuple(f"// {m}:" for m in _RATIONALE_MARKERS)
# JS/TS block-comment continuation lines (`/**\n * NOTE: ...\n */`) carry a
# leading `*` instead of `//` on every line but the first.
_STAR_RATIONALE_PREFIXES: tuple[str, ...] = tuple(f"* {m}:" for m in _RATIONALE_MARKERS)

# AC-A4-3 — JS/TS only (graphify has no Python/other-language equivalent,
# confirmed by the scout report; D5 keeps this project's port scoped the
# same way). `[- ]?` accepts "ADR-11", "ADR 11", "ADR11"; canonicalized
# below to a fixed `ADR-0011` (4-digit, zero-padded) / `RFC-793` (as-is,
# real RFC numbers are already large and never zero-padded in practice) —
# a disclosed judgment call, not specified further by the frozen criterion
# beyond its own two worked examples.
_ADR_RFC_RE = re.compile(r"\b(ADR|RFC)[- ]?(\d{1,5})\b", re.IGNORECASE)


def _canonical_adr_rfc_label(text: str) -> str | None:
    match = _ADR_RFC_RE.search(text)
    if not match:
        return None
    kind, digits = match.group(1).upper(), int(match.group(2))
    return f"ADR-{digits:04d}" if kind == "ADR" else f"RFC-{digits}"


def _match_rationale_comment(lang: str, raw_text: str) -> tuple[int, str, str | None] | None:
    """Decides whether a tree-sitter `comment` node's own exact source text
    is a "recognized rationale-prefixed comment" (AC-A4-1/AC-A4-2) for
    `lang`, and separately (JS/TS only, AC-A4-3) whether it references an
    ADR/RFC identifier. Returns `(line_offset, text, label)` — `line_offset`
    is 0 for a single-line comment or a match on a block comment's own
    first line, otherwise the 0-based offset of the specific inner line
    that matched within a multi-line block comment (so the rationale node
    can point at the actual triggering line, not just the block's start) —
    or `None` if this comment is not a rationale comment at all.
    """
    lines = raw_text.splitlines() or [raw_text]

    if lang in ("ruby", "python"):
        for offset, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(_HASH_RATIONALE_PREFIXES):
                return offset, stripped, None
        return None

    if lang in ("javascript", "typescript"):
        label = _canonical_adr_rfc_label(raw_text)
        for offset, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(_SLASH_RATIONALE_PREFIXES) or stripped.startswith(
                _STAR_RATIONALE_PREFIXES
            ):
                return offset, stripped, label
        if label is not None:
            # An ADR/RFC reference with no NOTE:/HACK:/etc. prefix is STILL
            # a rationale node (AC-A4-3 names no prefix precondition) —
            # anchor at the SPECIFIC line the ADR/RFC identifier actually
            # appears on (not just the block's first line), so `text`
            # carries the substantive content, not e.g. a bare `/**`.
            for offset, line in enumerate(lines):
                if _ADR_RFC_RE.search(line):
                    return offset, line.strip(), label
            return 0, lines[0].strip(), label  # defensive; unreachable given `label` matched
        return None

    return None


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

    _canonical_file_records: list[dict[str, Any]]
    _canonical_imports: list[dict[str, Any]]
    _canonical_bindings: dict[tuple[str, str, str, int], tuple[str, str]]
    _canonical_file_entity: str

    def __init__(
        self,
        repo: Path,
        langs: list[str] | None = None,
        read_only: bool = False,
        query_limit: int | None = None,
        graph_root: Path | None = None,
    ):
        self.repo = repo.resolve()
        self.langs = set(langs) if langs else set(DEFAULT_LANGS)
        self.read_only = read_only
        self.query_limit = (
            query_limit if query_limit is not None else DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1
        )
        self.graph_root = graph_root.resolve() if graph_root is not None else None
        if self.graph_root is None:
            self.atlas_dir = self.repo / ".atlas"
        else:
            if path_is_within(self.repo, self.graph_root):
                raise ValueError(
                    f"External graph root must be outside source checkout: {self.graph_root}"
                )
            for component in (self.graph_root, *self.graph_root.parents):
                if component.exists() and not component.is_dir():
                    raise ValueError(f"Graph root has a non-directory component: {component}")
            identity = hashlib.sha256(str(self.repo).encode("utf-8")).hexdigest()
            selected = (self.graph_root / "repos" / identity).resolve()
            if not path_is_within(self.graph_root, selected) or path_is_within(self.repo, selected):
                raise ValueError(
                    f"Graph namespace escapes the configured external storage: {selected}"
                )
            if selected.exists() and not selected.is_dir():
                raise ValueError(f"Graph namespace is not a directory: {selected}")
            self.atlas_dir = selected
        self._validate_graph_namespace()
        self.db_path = self.atlas_dir / f"graph.{SCHEMA_EPOCH}.db"
        if not read_only:
            self.atlas_dir.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None
        self._closed = False
        self._lease_generation_id: str | None = None
        self._lease_schema_epoch: int | None = None
        self._validated_lease = False
        self._lease_evidence_status = "unknown"
        self._acquisition_observations = None
        self._source_lease: snap.SourceLease | None = None
        self._source_builder: snap.Builder | None = None
        self._source_predecessor: snap.SourceLease | None = None
        self._overlays: dict[str, bytes] = {}

    def _validate_graph_namespace(self) -> None:
        """Reject static namespace redirection; configured root/source aliases are canonicalized."""
        if self.graph_root is not None:
            identity = hashlib.sha256(str(self.repo).encode("utf-8")).hexdigest()
            expected = self.graph_root / "repos" / identity
            if expected.resolve() != expected or self.atlas_dir != expected:
                raise ValueError(
                    f"Graph namespace must not redirect from its canonical path: {expected}"
                )

    def _read_db_path(self) -> Path:
        # Presence is not Path.exists(): dangling links must not expose older
        # evidence, and nonregular objects must be rejected before SQLite open.
        for candidate in (
            self.db_path,
            self.atlas_dir / "graph.7.db",
            self.atlas_dir / "graph.6.db",
        ):
            try:
                mode = candidate.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(f"Native graph must be a regular file: {candidate}")
            return candidate
        return self.db_path

    @property
    def db(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("This graph reader is closed")
        if self._db is None:
            self._validate_graph_namespace()
            selected = self._read_db_path()
            if self.read_only or selected != self.db_path:
                # mode=ro: SQLite never attempts a write, journal, or lock
                # file for this connection — safe on a read-only mount.
                self._db = sqlite3.connect(selected.as_uri() + "?mode=ro", uri=True)
            else:
                self._db = sqlite3.connect(self.db_path)
                self._db.executescript(SCHEMA)
            self._db.row_factory = sqlite3.Row
        return self._db

    def _close_db(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def close(self) -> None:
        """Idempotently release owned handles; a released lease cannot reopen."""
        if self._source_lease is not None:
            self._source_lease.close()
            self._source_lease = None
        self._validated_lease = False
        self._lease_evidence_status = "unknown"
        self._acquisition_observations = None
        self._close_db()
        self._closed = True

    @staticmethod
    def _canonical_generation(inputs: dict[str, Any]) -> str:
        return json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _schema_signature(conn: sqlite3.Connection) -> str:
        rows = [
            (row[0], row[1], " ".join(row[2].split()))
            for row in conn.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]
        return hashlib.sha256(ev.canonical(rows).encode()).hexdigest()

    def _generation_data(
        self, conn: sqlite3.Connection, *, retain_observations=False
    ) -> tuple[dict[str, Any], str] | None:
        """Validate this opened native schema, including exact old6/7 adapters."""
        try:
            values = dict(conn.execute("SELECT key,value FROM manifest"))
            epoch = values.get("epoch")
            if epoch not in {"6", "7", "8"}:
                return None
            legacy = epoch == "6"
            filename = Path(conn.execute("PRAGMA database_list").fetchone()[2]).name
            if legacy and filename != "graph.6.db":
                return None
            inputs = json.loads(values["generation_inputs"])
            identifier = values["generation_id"]
            expected_schema = {
                "epoch": int(epoch),
                "ddl_hash": {"6": LEGACY_DDL_HASH, "7": PREVIOUS_DDL_HASH, "8": EXPECTED_DDL_HASH}[
                    epoch
                ],
            }
            if (
                not isinstance(inputs, dict)
                or inputs.get("format")
                not in (
                    {"atlas-generation.v1"}
                    if legacy
                    else {"atlas-generation.v2", "atlas-generation.v3", "atlas-generation.v4"}
                )
                or inputs.get("kind")
                not in ({"build", "import"} if legacy else {"build", "import", "legacy_conversion"})
                or inputs.get("repository") != str(self.repo)
                or inputs.get("schema") != expected_schema
                or type(inputs["schema"]["epoch"]) is not int
                or not ev.hash_value(identifier)
                or hashlib.sha256(ev.canonical(inputs).encode()).hexdigest() != identifier
                or self._schema_signature(conn) != SQLITE_SCHEMA_HASHES[int(epoch)]
            ):
                return None

            def valid_producer(value):
                record = ev.producer(value, legacy=legacy)
                ev.validate_record(record)
                return legacy == ("modules" not in value)

            def valid_hashes(hashes):
                return isinstance(hashes, dict) and all(
                    ev.relative_path(path) and ev.hash_value(value)
                    for path, value in hashes.items()
                )

            common = {"format", "kind", "repository", "schema"}
            if inputs["format"] in {"atlas-generation.v3", "atlas-generation.v4"}:
                common.add("observation_digest")
                observations = self._observation_records(conn, require_current=True)
                if inputs.get("observation_digest") != self._status_hash(observations):
                    return None
            if inputs["format"] == "atlas-generation.v4":
                common.add("snapshot")
                source = inputs.get("snapshot")
                if not isinstance(source, dict):
                    return None
                if inputs["kind"] == "build":
                    if (
                        set(source) != {"state", "id", "manifest_sha256"}
                        or source["state"] != "available"
                        or not ev.hash_value(source["id"])
                        or source["id"] != source["manifest_sha256"]
                    ):
                        return None
                elif (
                    source != {"state": "unavailable", "reason": "import"}
                    or inputs["kind"] != "import"
                ):
                    return None
            if inputs["kind"] == "build":
                if set(inputs) != common | {"source_hashes", "configuration", "producer"}:
                    return None
                config = inputs["configuration"]
                if (
                    not valid_producer(inputs["producer"])
                    or not valid_hashes(inputs["source_hashes"])
                    or not isinstance(config, dict)
                    or set(config) != {"languages", "skip_patterns"}
                    or any(
                        not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v)
                        for v in config.values()
                    )
                    or config["languages"] != sorted(set(config["languages"]))
                    or set(inputs["source_hashes"])
                    != (
                        {r["path"] for r in observations if r["type"] == "observation"}
                        if inputs["format"] in {"atlas-generation.v3", "atlas-generation.v4"}
                        else {row[0] for row in conn.execute("SELECT path FROM files")}
                    )
                ):
                    return None
            elif inputs["kind"] == "import":
                if set(inputs) != common | {
                    "importer",
                    "artifact",
                    "source_provenance",
                } or not valid_producer(inputs["importer"]):
                    return None
                artifact = inputs["artifact"]
                if (
                    inputs["source_provenance"] != "unavailable"
                    or not isinstance(artifact, dict)
                    or set(artifact) != {"sha256", "content_hash", "record_count"}
                    or not ev.hash_value(artifact["sha256"])
                    or not ev.hash_value(artifact["content_hash"])
                    or not ev.integer(artifact["record_count"])
                ):
                    return None
                if legacy and artifact["record_count"] != sum(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("symbols", "edges", "rationale")
                ):
                    return None
                if not legacy and artifact["record_count"] != sum(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (*ev.TABLES.values(), *ev.COLUMNS)
                ) + len(self._observation_records(conn)):
                    return None
            else:
                if set(inputs) != common | {"converter", "legacy"} or not valid_producer(
                    inputs["converter"]
                ):
                    return None
                origin = inputs["legacy"]
                if (
                    not isinstance(origin, dict)
                    or set(origin) != {"kind", "identity", "source_hashes", "unavailable"}
                    or origin["kind"] not in {"database", "jsonl"}
                    or not ev.hash_value(origin["identity"])
                    or (
                        origin["source_hashes"] is not None
                        and not valid_hashes(origin["source_hashes"])
                    )
                    or origin["unavailable"]
                    != ["exact_spans", "structural_owners", "source_completeness"]
                    + (
                        ["unresolved_references"]
                        if origin["kind"] == "jsonl" or origin["source_hashes"] is None
                        else []
                    )
                ):
                    return None
            if retain_observations:
                self._acquisition_observations = (
                    observations
                    if inputs["format"] in {"atlas-generation.v3", "atlas-generation.v4"}
                    else []
                )
            return inputs, identifier
        except (sqlite3.Error, KeyError, ValueError, TypeError):
            return None

    @property
    def generation_id(self) -> str:
        if self._closed:
            raise RuntimeError("This graph reader is closed")
        if self._lease_generation_id is not None:
            return self._lease_generation_id
        data = self._generation_data(self.db)
        if data is None:
            raise RuntimeError("No complete generation metadata; reindex or reimport")
        return data[1]

    def _snapshot_binding(self, conn, observations=None):
        import heapq

        def ranges():
            streams = (
                (
                    json.loads(row[0])
                    for row in conn.execute("SELECT payload FROM " + table + " ORDER BY id")
                )
                for table in ("occurrences", "evidence")
            )
            yield from heapq.merge(*streams, key=lambda item: (item["id"], item["type"]))

        return snap.closure(
            lambda: ev.records(conn),
            self._observation_records(conn) if observations is None else observations,
            range_records=ranges,
            occurrence=lambda identifier: ev.get(conn, "occurrence", identifier),
        )

    def _snapshot_context(self, inputs):
        return {k: inputs[k] for k in ("repository", "schema", "configuration", "producer")}

    @property
    def snapshot_id(self):
        return self._source_lease.snapshot_id if self._source_lease is not None else None

    def read_source(self, path, source_sha256=None):
        from atlas_aci.enforcement import ToolError

        if self._source_lease is None:
            raise ToolError(
                "SOURCE_UNAVAILABLE",
                "This generation has no authenticated source bytes.",
                "different_tool",
            )
        try:
            return self._source_lease.read(path, source_sha256)
        except (ValueError, OSError) as exc:
            raise ToolError("SOURCE_UNAVAILABLE", str(exc), "different_tool") from exc

    @contextmanager
    def source_lease(self, snapshot_id):
        from atlas_aci.enforcement import ToolError

        if self._closed:
            raise RuntimeError("This graph is closed")
        lease = None
        try:
            lease = snap.acquire(self.atlas_dir, snapshot_id, repository=self.repo)
            yield lease
        except (ValueError, OSError, TypeError, KeyError) as exc:
            raise ToolError(
                "SNAPSHOT_UNAVAILABLE",
                "Requested source snapshot is unavailable: " + str(exc),
                "different_tool",
            ) from exc
        finally:
            if lease is not None:
                lease.close()

    @contextmanager
    def reader_lease(self):
        """Acquire SQLite, immutable manifest and one source-pack FD before exposure."""
        from atlas_aci.enforcement import ToolError

        if self._closed:
            raise RuntimeError("This graph is closed")
        reader = None
        for attempt in range(2):
            reader = CodeGraph(
                self.repo,
                langs=sorted(self.langs),
                read_only=True,
                query_limit=self.query_limit,
                graph_root=self.graph_root,
            )
            try:
                data = reader._generation_data(reader.db, retain_observations=True)
                if data is None:
                    raise ToolError(
                        "INDEX_UNAVAILABLE",
                        f"No complete compatible graph generation under {self.atlas_dir}. "
                        "Run: atlas-aci index --repo <repo>, or reimport a valid graph.",
                        "different_tool",
                    )
                reader._lease_generation_id = data[1]
                reader._lease_schema_epoch = data[0]["schema"]["epoch"]
                inputs = data[0]
                pointer = inputs.get("snapshot", {})
                if pointer.get("state") == "available":
                    binding = reader._snapshot_binding(reader.db, reader._acquisition_observations)
                    expected = {**binding}
                    reader._source_lease = snap.acquire(
                        self.atlas_dir, pointer["id"], repository=self.repo, expected=expected
                    )
                    actual_context = reader._source_lease.manifest["context"]
                    if any(
                        actual_context[k] != value
                        for k, value in reader._snapshot_context(inputs).items()
                    ):
                        raise ValueError("Graph/source provenance contradiction")
                # Reuse only facts already authenticated on THIS opened lease.
                # No traversal SQL/global status materialization is hidden in work counters.
                reader._lease_evidence_status = (
                    "partial"
                    if any(
                        row.get("type") == "observation" and row["state"] == "failed"
                        for row in (reader._acquisition_observations or [])
                    )
                    else "available"
                    if inputs["format"] == "atlas-generation.v4"
                    and reader._source_lease is not None
                    else "unknown"
                )
                reader._validated_lease = True
                reader._acquisition_observations = None
                break
            except sqlite3.Error as exc:
                reader.close()
                raise ToolError(
                    "INDEX_UNAVAILABLE",
                    f"No complete compatible graph generation under {self.atlas_dir}. "
                    "Run: atlas-aci index --repo <repo>, or reimport a valid graph.",
                    "different_tool",
                ) from exc
            except FileNotFoundError as exc:
                reader.close()
                if attempt:
                    raise ToolError(
                        "SNAPSHOT_UNAVAILABLE",
                        "Source snapshot changed during acquisition.",
                        "different_tool",
                    ) from exc
            except (ValueError, OSError, KeyError, TypeError) as exc:
                reader.close()
                raise ToolError(
                    "SNAPSHOT_UNAVAILABLE", "Invalid source snapshot: " + str(exc), "different_tool"
                ) from exc
            except BaseException:
                reader.close()
                raise
        try:
            yield reader
        finally:
            reader.close()

    def epoch_ok(self) -> bool:
        """Validate the pinned handle when present; otherwise inspect RO once."""
        if self._closed:
            return False
        if self._validated_lease:
            return True
        if self._db is not None:
            return self._generation_data(self._db) is not None
        try:
            with self.reader_lease():
                return True
        except (sqlite3.Error, RuntimeError):
            return False
        except Exception as exc:
            from atlas_aci.enforcement import ToolError

            if isinstance(exc, ToolError):
                return False
            raise

    def _producer_inputs(self) -> dict[str, Any]:
        return {
            "codegraph_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "queries_sha256": hashlib.sha256(
                self._canonical_generation(QUERIES).encode()
            ).hexdigest(),
            "modules": {
                name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                for name in (
                    "storage/evidence.py",
                    "providers/python_imports.py",
                    "indexing/extraction_cache.py",
                    "resolvers/dependencies.py",
                    "extractors/rust.py",
                    "storage/snapshots.py",
                )
            },
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("tree-sitter", "tree-sitter-language-pack")
            },
        }

    @staticmethod
    def _status_hash(value: Any) -> str:
        return hashlib.sha256(CodeGraph._canonical_generation(value).encode()).hexdigest()

    def _capabilities(self, enabled: list[str] | None = None) -> dict[str, Any]:
        """Describe query captures as they are persisted by _extract/_resolve_edges."""
        languages = []
        for lang in sorted(set(LANG_BY_EXT.values())):
            query = QUERIES.get(lang, "")
            definitions = (
                list(rust.KINDS)
                if lang == "rust"
                else sorted(set(re.findall(r"@def\.(\w+)", query)))
            )
            # _extract persists any @callee as call, including SCSS @ref.include.
            relations = set(re.findall(r"@heritage\.(\w+)", query))
            if "@callee" in query:
                relations.add("call")
                # _resolve_edges turns a call to a class into construct.
                if "class" in definitions:
                    relations.add("construct")
            if lang == "python":
                relations.add("import")
            if "@comment.rationale" in query:
                relations.add("rationale_for")
            reason = "bounded_structural_queries" if query else "no_extraction_query"
            state = "partial" if query else "unsupported"
            if enabled is not None and lang not in enabled and query:
                state, reason = "unsupported", "language_disabled"
            languages.append(
                {
                    "language": lang,
                    "extensions": sorted(
                        ext for ext, value in LANG_BY_EXT.items() if value == lang
                    ),
                    "extraction": {"state": state, "reason": reason},
                    "definitions": definitions,
                    "relations": sorted(relations),
                    "semantic_binding": {"state": "unsupported", "reason": "no_compiler_provider"},
                }
            )
        return {
            "format": "atlas-capabilities.v3",
            "limitations": {
                "python_imports": "package_local_unaliased_relative_only",
                "control_flow": "unsupported",
                "semantic_binding": "unsupported",
                **rust.LIMITATIONS,
            },
            "producer": self._producer_inputs(),
            "producers": [
                {
                    "name": "tree-sitter",
                    "operation": "structural_extraction",
                    "semantic_binding": {"state": "unsupported", "reason": "no_compiler_provider"},
                }
            ],
            "languages": languages,
        }

    @staticmethod
    def _observation_counts(files: list[dict[str, Any]]) -> dict[str, int]:
        return {
            state: sum(f["state"] == state for f in files)
            for state in ("indexed", "unsupported", "disabled", "failed")
        }

    def _publication_base(self) -> tuple[str | None, str | None]:
        """Read only the completed generation while the caller holds writer lock."""
        try:
            with self.reader_lease() as reader:
                row = reader.db.execute(
                    "SELECT value FROM manifest WHERE key='diagnostic_publication_token'"
                ).fetchone()
                return reader.generation_id, row[0] if row else None
        except Exception:
            return None, None

    @staticmethod
    def _new_publication_token(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO manifest(key,value) VALUES (?,?)",
            ("diagnostic_publication_token", secrets.token_hex(32)),
        )

    def _write_attempt(self, base: tuple[str | None, str | None]) -> None:
        observation = self._build_observation
        snapshot = {
            "format": "atlas-index-attempt.v1",
            "base_generation_id": base[0],
            "base_publication_token": base[1],
            "observation": observation,
            "attempt_id": self._status_hash(observation),
        }
        fd, name = tempfile.mkstemp(prefix=".index-attempt.", dir=self.atlas_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(self._canonical_generation(snapshot) + "\n")
            os.replace(name, self.atlas_dir / "index-attempt.v1.json")
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def _valid_status_provenance(producer: Any, configuration: Any) -> bool:
        if not isinstance(producer, dict):
            return False
        try:
            ev.validate_record(ev.producer(producer, legacy="modules" not in producer))
        except ValueError:
            return False
        if not isinstance(configuration, dict) or set(configuration) != {
            "languages",
            "skip_patterns",
        }:
            return False
        for key in ("languages", "skip_patterns"):
            if not isinstance(configuration[key], list) or any(
                not isinstance(v, str) or not v for v in configuration[key]
            ):
                return False
        return configuration["languages"] == sorted(set(configuration["languages"]))

    @staticmethod
    def _valid_capabilities(
        caps: Any, producer: dict[str, Any], configuration: dict[str, Any]
    ) -> bool:
        if (
            not isinstance(caps, dict)
            or caps.get("format")
            not in {"atlas-capabilities.v1", "atlas-capabilities.v2", "atlas-capabilities.v3"}
            or set(caps)
            != {"format", "producer", "producers", "languages"}
            | (
                {"limitations"}
                if caps.get("format") in {"atlas-capabilities.v2", "atlas-capabilities.v3"}
                else set()
            )
            or (caps.get("format") in {"atlas-capabilities.v2", "atlas-capabilities.v3"})
            != ("modules" in producer)
            or (caps.get("format") == "atlas-capabilities.v3")
            != ("extractors/rust.py" in producer.get("modules", {}))
            or caps["producer"] != producer
        ):
            return False
        if caps["format"] in {"atlas-capabilities.v2", "atlas-capabilities.v3"} and caps[
            "limitations"
        ] != {
            "python_imports": "package_local_unaliased_relative_only",
            "control_flow": "unsupported",
            "semantic_binding": "unsupported",
            **(rust.LIMITATIONS if caps["format"] == "atlas-capabilities.v3" else {}),
        }:
            return False
        unsupported = {"state": "unsupported", "reason": "no_compiler_provider"}
        if caps["producers"] != [
            {
                "name": "tree-sitter",
                "operation": "structural_extraction",
                "semantic_binding": unsupported,
            }
        ]:
            return False
        if not isinstance(caps["languages"], list):
            return False
        names = []
        for item in caps["languages"]:
            if not isinstance(item, dict) or set(item) != {
                "language",
                "extensions",
                "extraction",
                "definitions",
                "relations",
                "semantic_binding",
            }:
                return False
            if item["semantic_binding"] != unsupported or not isinstance(item["language"], str):
                return False
            names.append(item["language"])
            if (
                caps["format"] == "atlas-capabilities.v3"
                and item["language"] == "rust"
                and (
                    item["extensions"] != [".rs"]
                    or item["definitions"] != list(rust.KINDS)
                    or item["relations"] != ["call"]
                )
            ):
                return False
            if (
                not isinstance(item["extraction"], dict)
                or set(item["extraction"]) != {"state", "reason"}
                or item["extraction"]["state"] not in {"partial", "unsupported"}
                or item["extraction"]["reason"]
                not in {"bounded_structural_queries", "no_extraction_query", "language_disabled"}
            ):
                return False
            extraction = item["extraction"]
            enabled = item["language"] in configuration["languages"]
            if extraction["state"] == "partial":
                if not enabled or extraction["reason"] != "bounded_structural_queries":
                    return False
            elif extraction["reason"] == "language_disabled":
                if enabled:
                    return False
            elif extraction["reason"] == "no_extraction_query":
                if item["definitions"] or item["relations"]:
                    return False
            else:
                return False
            for key in ("extensions", "definitions", "relations"):
                values = item[key]
                if (
                    not isinstance(values, list)
                    or any(not isinstance(v, str) or not v for v in values)
                    or values != sorted(set(values))
                ):
                    return False
        return names == sorted(set(LANG_BY_EXT.values()))

    @staticmethod
    def _valid_observed_files(files: Any, *, failed: bool) -> bool:
        if not isinstance(files, list):
            return False
        for item in files:
            if not isinstance(item, dict) or set(item) != {
                "path",
                "language",
                "state",
                "source_sha256",
                "reason",
                "location",
            }:
                return False
            if (
                not isinstance(item["path"], str)
                or not item["path"]
                or Path(item["path"]).is_absolute()
                or ".." in Path(item["path"]).parts
                or item["language"] not in LANG_BY_EXT.values()
                or item["state"] not in {"indexed", "disabled", "unsupported", "failed"}
                or (item["state"] == "failed" and not failed)
            ):
                return False
            source_hash = item["source_sha256"]
            if source_hash is not None and (
                not isinstance(source_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
            ):
                return False
            if item["state"] == "indexed" and source_hash is None:
                return False
            if item["reason"] is not None and not isinstance(item["reason"], str):
                return False
            loc = item["location"]
            if loc is not None and (
                not isinstance(loc, dict)
                or set(loc) != {"line", "column", "end_line", "end_column"}
                or any(type(n) is not int or n < 0 for n in loc.values())
            ):
                return False
        paths = [f["path"] for f in files]
        return paths == sorted(set(paths))

    def _read_attempt(self) -> tuple[dict[str, Any] | None, str | None]:
        try:
            raw = (self.atlas_dir / "index-attempt.v1.json").read_bytes()
        except FileNotFoundError:
            return None, None
        except OSError:
            return None, "diagnostics_unreadable"
        try:
            data = json.loads(raw)
            if not (
                isinstance(data, dict)
                and set(data)
                == {
                    "format",
                    "base_generation_id",
                    "base_publication_token",
                    "observation",
                    "attempt_id",
                }
            ):
                raise ValueError("Invalid status metadata")
            if not (data["format"] == "atlas-index-attempt.v1"):
                raise ValueError("Invalid status metadata")
            for key in ("base_generation_id", "base_publication_token", "attempt_id"):
                value = data[key]
                if not (
                    (value is None and key != "attempt_id")
                    or (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value))
                ):
                    raise ValueError("Invalid status metadata")
            observation = data["observation"]
            if not (
                isinstance(observation, dict)
                and set(observation)
                == {
                    "format",
                    "producer",
                    "configuration",
                    "files",
                    "enumeration",
                    "excluded_count",
                    "failure_reason",
                }
            ):
                raise ValueError("Invalid status metadata")
            if not (
                observation["format"] == "atlas-observation.v1"
                and observation["enumeration"] == "observed_prefix"
            ):
                raise ValueError("Invalid status metadata")
            if not (
                self._valid_status_provenance(observation["producer"], observation["configuration"])
            ):
                raise ValueError("Invalid status metadata")
            if not (
                observation["excluded_count"] is None
                and isinstance(observation["failure_reason"], str)
            ):
                raise ValueError("Invalid status metadata")
            if not (self._valid_observed_files(observation["files"], failed=True)):
                raise ValueError("Invalid status metadata")
            if not (data["attempt_id"] == self._status_hash(observation)):
                raise ValueError("Invalid status metadata")
            return data, None
        except (ValueError, TypeError, KeyError, AssertionError):
            return None, "diagnostics_invalid"

    def _read_coverage(self, reader: CodeGraph, inputs: dict[str, Any]) -> dict[str, Any] | None:
        try:
            row = reader.db.execute(
                "SELECT value FROM manifest WHERE key='coverage_snapshot'"
            ).fetchone()
            if row is None:
                return None
            data = json.loads(row[0])
            if not (
                isinstance(data, dict)
                and set(data)
                == {
                    "format",
                    "generation_id",
                    "configuration",
                    "producer",
                    "capabilities",
                    "files",
                    "counts",
                    "enumeration",
                    "excluded_count",
                }
            ):
                raise ValueError("Invalid status metadata")
            if not (
                data["format"] in {"atlas-coverage.v1", "atlas-coverage.v2"}
                and data["generation_id"] == reader.generation_id
            ):
                raise ValueError("Invalid status metadata")
            if not (data["enumeration"] == "complete" and data["excluded_count"] is None):
                raise ValueError("Invalid status metadata")
            if not (
                data["configuration"] == inputs["configuration"]
                and data["producer"] == inputs["producer"]
            ):
                raise ValueError("Invalid status metadata")
            if not (
                self._valid_observed_files(
                    data["files"], failed=data["format"] == "atlas-coverage.v2"
                )
            ):
                raise ValueError("Invalid status metadata")
            expected_counts = self._observation_counts(data["files"])
            if data["format"] == "atlas-coverage.v1":
                expected_counts.pop("failed")
            if not (data["counts"] == expected_counts):
                raise ValueError("Invalid status metadata")
            if not (
                {
                    f["path"]: f["source_sha256"]
                    for f in data["files"]
                    if f["state"] == "indexed" or data["format"] == "atlas-coverage.v2"
                }
                == inputs["source_hashes"]
            ):
                raise ValueError("Invalid status metadata")
            caps = data["capabilities"]
            if not (self._valid_capabilities(caps, inputs["producer"], inputs["configuration"])):
                raise ValueError("Invalid status metadata")
            return data
        except (ValueError, KeyError, TypeError, AssertionError):
            return None

    def index_is_absent(self) -> bool:
        """Diagnose actual absence only; permissions/broken links are not absence."""
        self._validate_graph_namespace()
        for path in (self.db_path, self.atlas_dir / "graph.7.db", self.atlas_dir / "graph.6.db"):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            return False
        return True

    def published_status(self) -> dict[str, Any]:
        """Project published evidence from this already validated immutable lease.

        No source reads, latest-attempt sidecar, current runtime declarations,
        nested reader acquisition or repeated global graph validation belongs here.
        """
        if self._closed or not self._validated_lease:
            raise RuntimeError("Published status requires an acquired graph reader")
        inputs = json.loads(
            self.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )
        published = self._observation_records(self.db, validate=False)
        coverage = self._read_coverage(self, inputs) if inputs["kind"] == "build" else None
        histories = {r["id"]: r for r in published if r["type"] == "history"}
        observations = {r["path"]: r for r in published if r["type"] == "observation"}
        fact_paths = {
            row[0]
            for row in self.db.execute(
                "SELECT path FROM symbols UNION SELECT path FROM refs "
                "UNION SELECT path FROM rationale"
            )
        }
        historical_pairs: set[tuple[str, str]] = set()
        for history in histories.values():
            historical_pairs.update(
                (row["path"], row["source_sha256"])
                for row in history["records"]
                if row["type"] == "occurrence" and row["provenance_state"] == "source"
            )
        files = []
        for path, item in sorted(observations.items()):
            history = histories.get(item["historical_id"])
            hashes = (
                sorted(
                    {
                        row["source_sha256"]
                        for row in history["records"]
                        if row["type"] == "occurrence"
                        and row["path"] == path
                        and row["provenance_state"] == "source"
                    }
                )
                if history
                else ([item["observed_source_sha256"]] if item["state"] == "indexed" else [])
            )
            retained = bool(
                history
                and any(
                    row["type"] == "projection"
                    and row["row"].get("source_path" if row["table"] == "edges" else "path") == path
                    for row in history["records"]
                )
            )
            files.append(
                {
                    "path": path,
                    "language": item["language"],
                    "state": item["state"],
                    "observation_id": item["id"],
                    "history_id": item["historical_id"],
                    "observed_source_sha256": item["observed_source_sha256"],
                    "fact_source_sha256s": hashes,
                    "retained_facts": retained or path in fact_paths,
                    "stale": retained if history else (False if coverage else None),
                    "reason": item["reason"],
                    "location": item["location"],
                }
            )
        if not published:
            # Explicitly unknown old provenance; a file hash is not a fresh read.
            legacy_hashes = (
                inputs.get("source_hashes", inputs.get("legacy", {}).get("source_hashes")) or {}
            )
            files = [
                {
                    "path": path,
                    "language": None,
                    "state": "unknown",
                    "observation_id": None,
                    "history_id": None,
                    "observed_source_sha256": None,
                    "fact_source_sha256s": [sha],
                    "retained_facts": path in fact_paths,
                    "stale": None,
                    "reason": "published_observations_unavailable",
                    "location": None,
                }
                for path, sha in sorted(legacy_hashes.items())
            ]
        capabilities = None
        if coverage:
            stored = coverage["capabilities"]
            capabilities = {
                "representation": "bounded_projection",
                "stored_format": stored["format"],
                "languages": stored["languages"],
                "producers": stored["producers"],
                # v1 never recorded global limitations; null preserves that
                # absence without substituting current runtime declarations.
                "limitations": (
                    [
                        {"name": key, "reason": value}
                        for key, value in sorted(stored["limitations"].items())
                    ]
                    if "limitations" in stored
                    else None
                ),
            }
        references = []
        if self._source_lease is not None:
            for row in self._source_lease.manifest["current"]:
                item = observations.get(row["path"])
                state = item["state"] if item else "unknown"
                references.append(
                    {
                        "path": row["path"],
                        "source_sha256": row["sha256"],
                        "provenance": "observed_source",
                        "extraction_state": state,
                        "stale": False if state == "indexed" else None,
                        "available": self._source_lease.bindings[(row["path"], row["sha256"])],
                    }
                )
            for path, sha in sorted(historical_pairs):
                references.append(
                    {
                        "path": path,
                        "source_sha256": sha,
                        "provenance": "historical_indexed",
                        "extraction_state": "retained",
                        "stale": True,
                        "available": self._source_lease.bindings[(path, sha)],
                    }
                )
            references.sort(key=lambda row: (row["path"], row["provenance"], row["source_sha256"]))
        state = (
            "partial"
            if coverage and any(r["state"] == "failed" for r in observations.values())
            else "complete"
            if coverage
            else "unknown"
        )
        return {
            "coverage_basis": "published_generation",
            "live_source_verification": "not_performed",
            "latest_attempt_included": False,
            "runtime_capabilities_included": False,
            "indexed": {
                "representation": "bounded_projection",
                "configuration": inputs.get("configuration"),
                "producer": inputs.get("producer", inputs.get("importer", inputs.get("converter"))),
                "capabilities": capabilities,
            },
            "completeness": {
                "state": state,
                "reason": "published_extraction_partial"
                if state == "partial"
                else "published_coverage_complete"
                if coverage
                else "published_coverage_unknown",
                "enumeration": "complete" if coverage else "unknown",
                "counts": {
                    key: coverage["counts"].get(key, 0)
                    for key in ("indexed", "failed", "disabled", "unsupported")
                }
                if coverage
                else None,
                "total_count": len(coverage["files"]) if coverage else None,
                "stale_count": sum(row["stale"] is True for row in files) if published else None,
                "files": files,
            },
            "source": {
                "state": "available" if self.snapshot_id is not None else "unavailable",
                "reason": None if self.snapshot_id is not None else "source_capture_unavailable",
                "total_count": len(references),
                "references": references,
            },
            "facts": {
                "definitions": self.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
                "references": self.db.execute("SELECT COUNT(*) FROM refs").fetchone()[0],
                "edges": self.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
                "rationale": self.db.execute("SELECT COUNT(*) FROM rationale").fetchone()[0],
                "historical_bundles": len(histories),
            },
        }

    def status(self) -> dict[str, Any]:
        """Canonical operator snapshot: one read-only lease and one sidecar read."""
        from atlas_aci.enforcement import ToolError

        runtime = self._capabilities()
        generation = None
        token = None
        inputs: dict[str, Any] = {}
        coverage = None
        fact_paths: set[str] = set()
        published = []
        try:
            with self.reader_lease() as reader:
                generation = reader.generation_id
                inputs = cast("tuple[dict[str, Any], str]", reader._generation_data(reader.db))[0]
                fact_paths = {
                    row[0]
                    for row in reader.db.execute(
                        "SELECT path FROM symbols UNION SELECT path FROM refs "
                        "UNION SELECT path FROM rationale"
                    )
                }
                row = reader.db.execute(
                    "SELECT value FROM manifest WHERE key='diagnostic_publication_token'"
                ).fetchone()
                token = row[0] if row and re.fullmatch(r"[0-9a-f]{64}", row[0]) else None
                published = reader._observation_records(reader.db)
                if inputs["kind"] == "build" and token is not None:
                    coverage = self._read_coverage(reader, inputs)
                attempt, problem = self._read_attempt()
        except ToolError:
            attempt, problem = self._read_attempt()
        if problem:
            applicability = "unavailable"
        elif attempt is None:
            applicability = "none"
        elif generation is None:
            applicability = (
                "unbound_no_generation" if attempt["base_generation_id"] is None else "mismatch"
            )
        elif attempt["base_generation_id"] != generation:
            applicability = "mismatch"
        elif token is None:
            applicability = "uncorrelated_missing_token"
        elif attempt["base_publication_token"] != token:
            applicability = "mismatch"
        else:
            applicability = "matching"
        state = "complete" if coverage else ("unknown" if generation else "unavailable")
        reason = (
            "observed_complete"
            if coverage
            else ("source_coverage_unknown" if generation else "INDEX_UNAVAILABLE")
        )
        fact_hashes = (
            inputs.get("source_hashes", inputs.get("legacy", {}).get("source_hashes")) or {}
        )
        files = [
            {
                "path": path,
                "fact_source_sha256": source_hash,
                "failed_source_sha256": None,
                "observed_source_sha256": None,
                "retained_facts": path in fact_paths,
                "stale": False if coverage else None,
                "refresh_verification": "last_published",
            }
            for path, source_hash in sorted(fact_hashes.items())
        ]
        if published:
            by_id = {r["id"]: r for r in published if r["type"] == "history"}
            files = []
            for item in (r for r in published if r["type"] == "observation"):
                history = by_id.get(item["historical_id"])
                hashes = (
                    sorted(
                        {
                            r["source_sha256"]
                            for r in history["records"]
                            if r["type"] == "evidence"
                            and r["source_sha256"] is not None
                            and any(
                                o["type"] == "occurrence"
                                and o["path"] == item["path"]
                                and o["id"] == r["occurrence_id"]
                                for o in history["records"]
                            )
                        }
                    )
                    if history
                    else ([item["observed_source_sha256"]] if item["state"] == "indexed" else [])
                )
                retained_semantics = history is not None and any(
                    r["type"] == "projection"
                    and r["row"].get("source_path" if r["table"] == "edges" else "path")
                    == item["path"]
                    for r in history["records"]
                )
                files.append(
                    {
                        "path": item["path"],
                        "fact_source_sha256": hashes[0] if len(hashes) == 1 else None,
                        "fact_source_sha256s": hashes,
                        "observed_source_sha256": item["observed_source_sha256"],
                        "failed_source_sha256": item["observed_source_sha256"]
                        if item["state"] == "failed"
                        else None,
                        "retained_facts": retained_semantics or item["path"] in fact_paths,
                        "stale": bool(retained_semantics)
                        if history
                        else (False if coverage else None),
                        "refresh_verification": "observed_failed"
                        if item["state"] == "failed"
                        else "verified_unchanged"
                        if coverage
                        else "artifact_only",
                        "extraction_state": item["state"],
                        "reason": item["reason"],
                        "location": item["location"],
                    }
                )
            files.sort(key=lambda item: item["path"])
            if coverage and any(
                r["type"] == "observation" and r["state"] == "failed" for r in published
            ):
                state, reason = "partial", "observed_complete_extraction_partial"
        observed = {f["path"]: f for f in attempt["observation"]["files"]} if attempt else {}
        if applicability == "matching":
            state, reason = "failed", "refresh_failed_observed_prefix"
            observation = cast("dict[str, Any]", attempt)["observation"]
            compatible = observation["producer"] == inputs.get("producer") and observation[
                "configuration"
            ] == inputs.get("configuration")
            for item in files:
                entry = observed.get(item["path"])
                item["refresh_verification"] = "not_processed"
                item["stale"] = None
                if entry is not None:
                    item["observed_source_sha256"] = entry["source_sha256"]
                    unchanged = entry["source_sha256"] == item["fact_source_sha256"]
                    item["refresh_verification"] = (
                        "verified_unchanged"
                        if entry["state"] == "indexed" and unchanged
                        else "observed_not_published"
                    )
                    item["stale"] = item["retained_facts"] and (
                        entry["state"] == "failed" or not unchanged
                    )
                    if not compatible:
                        item["refresh_verification"] = "incompatible_provenance"
                        item["stale"] = None
                    if entry["state"] == "failed":
                        item["failed_source_sha256"] = entry["source_sha256"]
        elif applicability in {"unavailable", "uncorrelated_missing_token"}:
            state, reason = "unknown", problem or "attempt_correlation_unknown"
            for item in files:
                item["stale"] = None
        payload = {
            "format": "atlas-status.v1",
            "generation_id": generation,
            "runtime": runtime,
            "indexed": {
                "configuration": inputs.get("configuration"),
                "capabilities": coverage["capabilities"] if coverage else None,
                "producer": inputs.get("producer", inputs.get("importer")),
                "coverage_id": self._status_hash(coverage) if coverage else None,
            },
            "completeness": {
                "state": state,
                "reason": reason,
                "files": files,
                "counts": coverage["counts"]
                if coverage and applicability == "none"
                else (
                    self._observation_counts(
                        cast("dict[str, Any]", attempt)["observation"]["files"]
                    )
                    if applicability == "matching"
                    else (coverage["counts"] if coverage and applicability == "mismatch" else None)
                ),
                "excluded_count": None,
                "total_count": len(files)
                if coverage and coverage["format"] == "atlas-coverage.v2"
                else None,
                "stale_count": sum(item["stale"] is True for item in files),
                "enumeration": "observed_prefix"
                if applicability == "matching"
                else ("complete" if coverage else "unknown"),
            },
            "attempt": {
                "observed_failed_count": sum(f["state"] == "failed" for f in observed.values())
                if attempt
                else None,
                "applicability": applicability,
                "reason": problem,
                "attempt_id": attempt["attempt_id"] if attempt else None,
                "observation": attempt["observation"] if attempt else None,
            },
        }
        return {**payload, "status_id": self._status_hash(payload)}

    def _build_inputs(self) -> dict[str, Any]:
        from atlas_aci.config import DEFAULT_SKIP_PATTERNS

        return {
            "format": "atlas-generation.v2",
            "kind": "build",
            "repository": str(self.repo),
            "schema": {"epoch": SCHEMA_EPOCH, "ddl_hash": EXPECTED_DDL_HASH},
            "configuration": {
                "languages": sorted(self.langs),
                "skip_patterns": list(DEFAULT_SKIP_PATTERNS),
            },
            "producer": self._producer_inputs(),
        }

    def _store_generation(self, conn: sqlite3.Connection, inputs: dict[str, Any]) -> str:
        raw = self._canonical_generation(inputs)
        identifier = hashlib.sha256(raw.encode()).hexdigest()
        conn.executemany(
            "INSERT OR REPLACE INTO manifest(key,value) VALUES (?,?)",
            [
                ("epoch", str(SCHEMA_EPOCH)),
                ("generation_inputs", raw),
                ("generation_id", identifier),
            ],
        )
        unknown = (
            inputs["kind"] == "legacy_conversion"
            and "unresolved_references" in inputs["legacy"]["unavailable"]
        )
        if inputs["kind"] == "import":
            unknown = any(
                json.loads(row[0])["identity_basis"] == "legacy_unavailable"
                for row in conn.execute("SELECT payload FROM producers")
            )
            unknown = (
                unknown
                or conn.execute(
                    "SELECT 1 FROM occurrences WHERE payload "
                    'LIKE \'%"role":"legacy_edge:%\' LIMIT 1'
                ).fetchone()
                is not None
            )
        conn.execute(
            "INSERT OR REPLACE INTO manifest(key,value) VALUES ('reference_coverage',?)",
            ("unknown" if unknown else "complete",),
        )
        return identifier

    @contextmanager
    def _writer_lock(self):
        if self.read_only or self._closed:
            raise RuntimeError("Index publication requires an open writable CodeGraph")
        self._validate_graph_namespace()
        self.atlas_dir.mkdir(parents=True, exist_ok=True)
        with (self.atlas_dir / ".index.lock").open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _stage_path(self) -> Path:
        fd, name = tempfile.mkstemp(prefix=f".graph.{SCHEMA_EPOCH}.db.tmp.", dir=self.atlas_dir)
        os.close(fd)
        return Path(name)

    # ---- Build / re-index (index-path only; never called by `serve`) ----

    def build(
        self, since: str | None = None, *, overlays: Mapping[str, bytes] | None = None
    ) -> dict[str, Any]:
        """Index the repo. Returns stats.

        A truthy ``since`` requests incremental reuse. Every eligible source
        buffer is read and hashed once; facts are reused only when its exact
        content hash and schema/configuration/producer compatibility match.
        Changed, new, or removed files update the staged graph accordingly.
        Missing or untrusted build provenance forces a full rebuild.

        Full and incremental builds both stage a complete database under the
        shared publication lock, then atomically replace the published path.
        Readers holding the previous generation keep its immutable inode.
        Per DIR-2, stale-epoch sweeping and rebuilding are index-path only;
        the read-only ``serve`` path never performs them.
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

        from atlas_aci.config import DEFAULT_SKIP_PATTERNS

        if overlays is not None and not isinstance(overlays, Mapping):
            raise ValueError("overlays must be an explicit path-to-bytes mapping")
        supplied_overlays = dict(overlays or {})
        for path, source in supplied_overlays.items():
            if (
                not ev.relative_path(path)
                or not isinstance(source, bytes)
                or Path(path).suffix not in LANG_BY_EXT
                or any(part in DEFAULT_SKIP_PATTERNS for part in Path(path).parts)
            ):
                raise ValueError("Unsafe, skipped or unrecognized overlay")
            for parent in (self.repo / path, *(self.repo / path).parents):
                if parent == self.repo:
                    break
                if parent.is_symlink():
                    raise ValueError("Overlay source symlink")
        with self._writer_lock():
            self._overlays = supplied_overlays
            swept = self._sweep_stale_epoch_files()
            if swept:
                log.info("stale_epoch_files_swept", files=swept)
            base = self._publication_base()
            self._build_observation = {
                "format": "atlas-observation.v1",
                "producer": self._producer_inputs(),
                "configuration": self._build_inputs()["configuration"],
                "files": [],
                "enumeration": "observed_prefix",
                "excluded_count": None,
                "failure_reason": "index_failed",
            }
            try:
                return self._build_atomic(get_parser, since)
            except Exception:
                try:
                    self._write_attempt(base)
                except Exception:
                    log.warning("index_attempt_write_failed", reason="diagnostics_unavailable")
                raise

    def _sweep_stale_epoch_files(self) -> list[str]:
        """Index-only: sweep unknown epochs, preserving old6/7 until publication."""
        if not self.atlas_dir.exists():
            return []
        removed = []
        for f in sorted(self.atlas_dir.glob("graph.*.db")):
            if f.name not in {self.db_path.name, "graph.7.db", "graph.6.db"}:
                f.unlink()
                removed.append(f.name)
        return removed

    def _retire_previous_native(self) -> None:
        """Only after successful atomic8 publication; held7 leases keep their inode."""
        try:
            (self.atlas_dir / "graph.7.db").unlink(missing_ok=True)
        except OSError:
            log.warning("previous_native_retirement_failed")

    def _build_atomic(self, get_parser: Any, since: str | None) -> dict[str, Any]:
        """All index modes mutate only staging, never an inode held by readers."""
        tmp_path = self._stage_path()
        conn = None
        builder = None
        predecessor = None
        predecessor_data = None
        predecessor_outputs_valid = False
        self._untrusted_modern_predecessor = False
        try:
            builder = snap.Builder(self.atlas_dir)
            self._source_builder = builder
            conn = sqlite3.connect(tmp_path)
            conn.row_factory = sqlite3.Row
            inherited = False
            self._proof_predecessor_records = []
            self._proof_predecessor_history = {}
            self._proof_predecessor_observations = {}
            selected = self._read_db_path()
            if selected.exists() and selected.name != "graph.6.db":
                previous = sqlite3.connect(selected.as_uri() + "?mode=ro", uri=True)
                previous.row_factory = sqlite3.Row
                try:
                    predecessor_data = self._generation_data(previous)
                    if predecessor_data is not None:
                        try:
                            self._validate_evidence_projections(previous)
                            dep.validate_outputs(previous)
                            predecessor_outputs_valid = True
                        except (ValueError, KeyError, TypeError):
                            self._untrusted_modern_predecessor = (
                                predecessor_data[0].get("format") == "atlas-generation.v4"
                            )
                        self._proof_predecessor_records = list(
                            ev.records(previous)
                        ) + self._projection_records(previous)
                        self._proof_predecessor_observations = {
                            r["path"]: r
                            for r in self._observation_records(previous)
                            if r["type"] == "observation"
                        }
                        self._proof_predecessor_history = {
                            r["path"]: r
                            for r in self._observation_records(previous)
                            if r["type"] == "history"
                        }
                finally:
                    previous.close()
            if since is not None and selected == self.db_path:
                try:
                    previous = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
                    try:
                        if self._generation_data(previous) is not None:
                            previous.backup(conn)
                            inherited = True
                    finally:
                        previous.close()
                except sqlite3.Error:
                    pass  # missing/legacy metadata means rebuild, never invented provenance
            conn.executescript(SCHEMA)
            if selected.name == "graph.6.db" and selected.exists():
                with sqlite3.connect(
                    (self.atlas_dir / "graph.6.db").as_uri() + "?mode=ro", uri=True
                ) as legacy:
                    legacy.row_factory = sqlite3.Row
                    self._convert_legacy_into(conn, legacy)
                self._proof_predecessor_records = list(ev.records(conn)) + self._projection_records(
                    conn
                )
                inherited = False
            if predecessor_data is not None and predecessor_outputs_valid:
                with self.reader_lease() as old_reader:
                    predecessor = old_reader._source_lease
                    old_reader._source_lease = None
            self._source_predecessor = predecessor
            stats = self._run_build(conn, get_parser, since=since if inherited else None)
            if self._generation_data(conn) is None:
                raise RuntimeError("Staging did not produce a complete generation")
            conn.close()
            conn = None
            self._close_db()
            os.replace(tmp_path, self.db_path)
            builder.published = True
            self._retire_previous_native()
            try:
                snap.retain(self.atlas_dir, stats["snapshot_id"])
            except (OSError, ValueError):
                log.warning("source_snapshot_prune_failed")
            return stats
        finally:
            if predecessor is not None:
                predecessor.close()
            self._source_builder = None
            if builder is not None:
                builder.close()
            if conn is not None:
                conn.close()
            for path in (tmp_path, Path(str(tmp_path) + "-journal")):
                path.unlink(missing_ok=True)

    def _run_build(
        self, conn: sqlite3.Connection, get_parser: Any, since: str | None
    ) -> dict[str, Any]:
        """Index into staging; reuse only exact-buffer hashes and compatible facts."""
        builder = self._source_builder
        if builder is None:
            raise RuntimeError("Source capture requires a staged build")
        base_inputs = self._build_inputs()
        self._capture_producer = ev.producer(base_inputs["producer"])
        previous_witness = dep.read(conn)
        try:
            previous_dependencies = dep.snapshot(conn)
        except (ValueError, TypeError, KeyError):
            previous_dependencies = None
        previous_generation = self._generation_data(conn) if since is not None else None
        previous_inputs = previous_generation[0] if previous_generation is not None else {}
        previous_base = {
            k: v
            for k, v in previous_inputs.items()
            if k not in {"source_hashes", "observation_digest", "snapshot"}
        }
        if previous_base.get("format") in {"atlas-generation.v3", "atlas-generation.v4"}:
            previous_base["format"] = base_inputs["format"]
        incremental = since is not None and previous_base == base_inputs
        invalid_predecessor_outputs = False
        if incremental:
            try:
                # Check complete old source/projection closure before extraction
                # legitimately removes changed facts. A rewritten witness hash
                # cannot make an incomplete predecessor reusable.
                self._validate_evidence_projections(conn)
                dep.validate_outputs(conn)
            except ValueError:
                invalid_predecessor_outputs = True
                incremental = False
        previous_hashes = previous_inputs.get("source_hashes", {}) if incremental else {}
        source_hashes: dict[str, str] = {}
        observed_records = []
        failed_histories = []
        cache = xc.ExtractionCache(self.atlas_dir)
        cache_stats: dict[str, Any] = {
            "disk_hits": 0,
            "misses": 0,
            "miss_reasons": {},
            "write_failures": 0,
            "predecessor_reused": 0,
            "parser_invocations": 0,
        }

        if not incremental:
            for table in (*ev.TABLES.values(), "import_bindings"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM refs")
            conn.execute("DELETE FROM assignment_targets")
            conn.execute("DELETE FROM rationale")
            conn.execute("DELETE FROM files")

        ev.put(conn, self._capture_producer)
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
        rationale_added = 0
        seen_paths: set[str] = set()
        unsupported_skipped: dict[str, int] = {}

        for path in sorted(
            set(self._iter_source_files()) | {self.repo / name for name in self._overlays},
            key=lambda p: str(p.relative_to(self.repo)),
        ):
            ext = path.suffix
            lang = LANG_BY_EXT.get(ext)

            rel = str(path.relative_to(self.repo))
            if lang is not None:
                observation: dict[str, Any] = {
                    "path": rel,
                    "language": lang,
                    "state": "indexed",
                    "source_sha256": None,
                    "reason": None,
                    "location": None,
                }
                self._build_observation["files"].append(observation)
            if lang is None:
                continue
            try:
                if rel in self._overlays:
                    source = self._overlays[rel]
                    mtime_ns, size = 0, len(source)
                else:
                    source, stat = snap.safe_source(self.repo, rel)
                    mtime_ns, size = stat.st_mtime_ns, stat.st_size
                builder.add(source)
            except OSError:
                observation.update(state="failed", reason="read_error")
                self._build_observation["failure_reason"] = "read_error"
                raise
            source_hash = hashlib.sha256(source).hexdigest()
            source_hashes[rel] = source_hash
            observation["source_sha256"] = source_hash
            if lang not in QUERIES:
                observation.update(state="unsupported", reason="no_extraction_query")
                unsupported_skipped[lang] = unsupported_skipped.get(lang, 0) + 1
                observed_records.append(
                    ev.observation(
                        rel, lang, source_hash, "unsupported", "no_extraction_query", None, None
                    )
                )
                continue
            if lang not in self.langs:
                observation.update(state="disabled", reason="language_disabled")
                observed_records.append(
                    ev.observation(
                        rel, lang, source_hash, "disabled", "language_disabled", None, None
                    )
                )
                continue
            if incremental:
                seen_paths.add(rel)
                if (
                    rel in stored_files
                    and previous_hashes.get(rel) == source_hash
                    and self._proof_predecessor_observations.get(rel, {}).get("state") == "indexed"
                ):
                    conn.execute(
                        "UPDATE files SET mtime_ns=?, size=? WHERE path=?", (mtime_ns, size, rel)
                    )
                    files_skipped += 1
                    cache_stats["predecessor_reused"] += 1
                    observed_records.append(
                        ev.observation(rel, lang, source_hash, "indexed", None, None, None)
                    )
                    continue
            cache_inputs = xc.inputs(source, rel, lang, base_inputs)
            packet, cache_reason = cache.lookup(cache_inputs, source)
            if packet is None:
                cache_stats["misses"] += 1
                reasons = cache_stats["miss_reasons"]
                reasons[cache_reason] = reasons.get(cache_reason, 0) + 1
                try:
                    parser = get_parser(cast("SupportedLanguage", lang))
                    cache_stats["parser_invocations"] += 1
                    tree = parser.parse(source)
                except Exception as exc:
                    observation.update(state="failed", reason="parser_exception")
                    self._build_observation["failure_reason"] = "parser_exception"
                    raise RuntimeError(f"Cannot index eligible source {rel}") from exc
                if tree.root_node.has_error:
                    pending = [tree.root_node]
                    error_node = tree.root_node
                    while pending:
                        node = pending.pop()
                        if node.type == "ERROR" or node.is_missing:
                            error_node = node
                            break
                        pending.extend(reversed(node.children))
                    observation.update(
                        state="failed",
                        reason="parse_error",
                        location={
                            "line": error_node.start_point[0] + 1,
                            "column": error_node.start_point[1],
                            "end_line": error_node.end_point[0] + 1,
                            "end_column": error_node.end_point[1],
                        },
                    )
                    history = self._proof_predecessor_history.get(rel) or self._retained_closure(
                        rel, lang, self._proof_predecessor_records
                    )
                    if history:
                        failed_histories.append(history)
                    observed_records.append(
                        ev.observation(
                            rel,
                            lang,
                            source_hash,
                            "failed",
                            "parse_error",
                            observation["location"],
                            history,
                        )
                    )
                    if not cache.write(
                        cache_inputs,
                        None,
                        {"reason": "parse_error", "location": observation["location"]},
                    ):
                        cache_stats["write_failures"] += 1
                    # Failed files do not enter any active lookup/candidate table.
                    if incremental:
                        for table in (
                            "symbols",
                            "refs",
                            "assignment_targets",
                            "rationale",
                            "import_bindings",
                            "files",
                        ):
                            conn.execute(f"DELETE FROM {table} WHERE path=?", (rel,))
                        ev.remove_path(conn, rel)
                    continue
            else:
                cache_stats["disk_hits"] += 1

            if incremental:
                # New or changed file — drop stale rows before re-extracting so
                # a rename/removal-within-file doesn't leave duplicate entries.
                conn.execute("DELETE FROM symbols WHERE path = ?", (rel,))
                conn.execute("DELETE FROM refs WHERE path = ?", (rel,))
                conn.execute("DELETE FROM assignment_targets WHERE path = ?", (rel,))
                conn.execute("DELETE FROM rationale WHERE path = ?", (rel,))

            ev.remove_path(conn, rel)
            conn.execute("DELETE FROM import_bindings WHERE path=?", (rel,))
            if packet is None:
                try:
                    symbols, refs, assignment_targets, rationale_comments = self._extract(
                        tree, source, rel, lang
                    )
                except Exception:
                    observation.update(state="failed", reason="extraction_error")
                    self._build_observation["failure_reason"] = "extraction_error"
                    raise
                packet = xc.ExtractionPacket(
                    {
                        "symbols": [asdict(row) for row in symbols],
                        "refs": [asdict(row) for row in refs],
                        "assignments": [list(fact) for fact in assignment_targets],
                        "rationale": [asdict(row) for row in rationale_comments],
                        "records": list(
                            {row["id"]: row for row in self._canonical_file_records}.values()
                        ),
                        "imports": self._canonical_imports,
                        "bindings": [
                            {"fact": list(fact), "links": list(links)}
                            for fact, links in self._canonical_bindings.items()
                        ],
                        "file_entity": self._canonical_file_entity,
                    }
                )
                packet.validate(cache_inputs, source)
                if not cache.write(cache_inputs, packet):
                    cache_stats["write_failures"] += 1
            # Cold and hit paths restore the same complete raw packet before
            # common insertion; global resolution never enters the cache.
            symbols = [Symbol(**row) for row in packet.payload["symbols"]]
            refs = [Reference(**row) for row in packet.payload["refs"]]
            assignment_targets = [tuple(fact) for fact in packet.payload["assignments"]]
            rationale_comments = [RationaleComment(**row) for row in packet.payload["rationale"]]
            self._canonical_file_records = packet.payload["records"]
            self._canonical_imports = packet.payload["imports"]
            self._canonical_bindings = {
                tuple(row["fact"]): tuple(row["links"]) for row in packet.payload["bindings"]
            }
            self._canonical_file_entity = packet.payload["file_entity"]
            for record in self._canonical_file_records:
                ev.put(conn, record, rel if record["type"] != "producer" else None)
            for binding in self._canonical_imports:
                columns = list(binding)
                conn.execute(
                    "INSERT INTO import_bindings("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    tuple(binding.values()),
                )
            for target_name, scope, binding_kind, byte_start in assignment_targets:
                conn.execute(
                    "INSERT INTO assignment_targets(path, name, scope, binding_kind, "
                    "byte_start, entity_id, occurrence_id, evidence_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rel,
                        target_name,
                        scope,
                        binding_kind,
                        byte_start,
                        self._canonical_file_entity,
                        *self._canonical_bindings[(target_name, scope, binding_kind, byte_start)],
                    ),
                )
            for s in symbols:
                conn.execute(
                    "INSERT INTO symbols(name, kind, path, line_start, line_end, "
                    "lang, binding, entity_id, occurrence_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        s.name,
                        s.kind,
                        s.path,
                        s.line_start,
                        s.line_end,
                        s.lang,
                        s.binding,
                        s.entity_id,
                        s.occurrence_id,
                    ),
                )
                symbols_added += 1
            for r in refs:
                conn.execute(
                    "INSERT INTO refs(callee_name, relation, qualified, qualifier_name, "
                    "path, line, col, lang, binding, source_entity_id, occurrence_id, "
                    "evidence_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r.callee_name,
                        r.relation,
                        int(r.qualified),
                        r.qualifier_name,
                        r.path,
                        r.line,
                        r.col,
                        r.lang,
                        r.binding,
                        r.source_entity_id,
                        r.occurrence_id,
                        r.evidence_id,
                    ),
                )
                refs_added += 1
            for rc in rationale_comments:
                # A4/AC-A4-1: the rationale_for edge target is the TIGHTEST
                # enclosing symbol in THIS SAME FILE's just-extracted
                # `symbols` (a rationale comment's scope is always local to
                # its own file — no cross-file resolution, AC-A4-4 is about
                # never becoming a call/inheritance CANDIDATE, not about
                # finding its owner). None when no symbol contains the
                # comment's line (e.g. a module-level/top-of-file comment)
                # — a real "no enclosing definition" fact, not an error.
                owner_symbol = next(
                    (symbol for symbol in symbols if symbol.entity_id == rc.target_entity_id), None
                )
                target = (
                    (owner_symbol.path, owner_symbol.line_start, owner_symbol.name)
                    if owner_symbol
                    else None
                )
                conn.execute(
                    "INSERT INTO rationale(path, line, text, label, target_path, "
                    "target_line, target_name, lang, occurrence_id, evidence_id, "
                    "target_entity_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rc.path,
                        rc.line,
                        rc.text,
                        rc.label,
                        target[0] if target else None,
                        target[1] if target else None,
                        target[2] if target else None,
                        rc.lang,
                        rc.occurrence_id,
                        rc.evidence_id,
                        rc.target_entity_id,
                    ),
                )
                rationale_added += 1

            conn.execute(
                "INSERT OR REPLACE INTO files(path, mtime_ns, size, lang, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (rel, mtime_ns, size, lang, datetime.now(UTC).isoformat()),
            )
            files_indexed += 1
            observed_records.append(
                ev.observation(rel, lang, source_hash, "indexed", None, None, None)
            )

        if incremental:
            for rel in set(stored_files) - seen_paths:
                ev.remove_path(conn, rel)
                conn.execute("DELETE FROM import_bindings WHERE path=?", (rel,))
                conn.execute("DELETE FROM symbols WHERE path = ?", (rel,))
                conn.execute("DELETE FROM refs WHERE path = ?", (rel,))
                conn.execute("DELETE FROM assignment_targets WHERE path = ?", (rel,))
                conn.execute("DELETE FROM rationale WHERE path = ?", (rel,))
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
        all_observed = sorted(
            [*observed_records, *failed_histories], key=lambda r: (r["type"], r["id"])
        )
        conn.execute(
            "INSERT OR REPLACE INTO manifest(key,value) VALUES ('source_observations',?)",
            (ev.canonical(all_observed),),
        )
        self._observation_records(conn)
        self._validate_history(all_observed)
        self._stale_target_names = {
            (r["row"]["lang"], r["row"]["name"])
            for h in failed_histories
            for r in h["records"]
            if r["type"] == "projection"
            and r["table"] == "symbols"
            and r["row"]["path"] == h["path"]
        }
        try:
            current_dependencies = dep.snapshot(conn)
        except (ValueError, TypeError, KeyError):
            current_dependencies = None
        if current_dependencies is None:
            affected = {row[0] for row in conn.execute("SELECT path FROM files")}
            removed = set(previous_dependencies or {}) - affected
            reused, binding_mode, binding_reason = set(), "full", "unknown_dependency_inputs"
        else:
            affected, removed, reused, binding_mode, binding_reason = dep.plan(
                previous_witness,
                previous_dependencies,
                current_dependencies,
                base_inputs,
                incremental,
            )
        if invalid_predecessor_outputs:
            binding_reason = "invalid_predecessor_outputs"
        selected = affected | removed if binding_mode == "affected" else None
        edges_added = self._resolve_edges(conn, selected)
        self._resolve_rationale_assertions(conn, selected)
        self._validate_evidence_projections(conn)
        if current_dependencies is not None:
            binding_witness = dep.witness(conn, base_inputs)
            conn.execute(
                "INSERT OR REPLACE INTO manifest(key,value) VALUES (?,?)",
                (dep.KEY, ev.canonical(binding_witness)),
            )
        else:
            conn.execute("DELETE FROM manifest WHERE key=?", (dep.KEY,))
        binding_stats = {
            "mode": binding_mode,
            "reason": binding_reason,
            "affected_units": len(affected),
            "reused_units": len(reused),
            "removed_units": len(removed),
            "affected_paths": sorted(affected),
            "reused_paths": sorted(reused),
            "removed_paths": sorted(removed),
        }

        binding = self._snapshot_binding(conn)
        context = {**self._snapshot_context(base_inputs), "identity": snap.identity(self.repo)}
        if self._untrusted_modern_predecessor and any(
            row["sha256"] not in builder.blobs for row in binding["source_bounds"]
        ):
            raise ValueError(
                "Cannot authenticate historical source from an incoherent native predecessor"
            )
        snapshot_id = builder.publish(context, binding, self._source_predecessor)
        generation_id = self._store_generation(
            conn,
            {
                **base_inputs,
                "format": "atlas-generation.v4",
                "snapshot": {
                    "state": "available",
                    "id": snapshot_id,
                    "manifest_sha256": snapshot_id,
                },
                "source_hashes": source_hashes,
                "observation_digest": self._status_hash(all_observed),
            },
        )
        coverage = {
            "format": "atlas-coverage.v2",
            "generation_id": generation_id,
            "configuration": base_inputs["configuration"],
            "producer": base_inputs["producer"],
            "capabilities": self._capabilities(sorted(self.langs)),
            "files": self._build_observation["files"],
            "counts": self._observation_counts(self._build_observation["files"]),
            "enumeration": "complete",
            "excluded_count": None,
        }
        conn.execute(
            "INSERT OR REPLACE INTO manifest(key,value) VALUES ('coverage_snapshot',?)",
            (self._canonical_generation(coverage),),
        )
        self._new_publication_token(conn)
        conn.commit()
        return {
            "snapshot_id": snapshot_id,
            "binding_resolution": binding_stats,
            "extraction_cache": cache_stats,
            "files_indexed": files_indexed,
            "symbols": symbols_added,
            "refs": refs_added,
            "edges": edges_added,
            "rationale": rationale_added,
            "files_skipped": files_skipped,
            "files_removed": files_removed,
            "unsupported_skipped": unsupported_skipped,
            "extraction_state": "partial"
            if failed_histories
            or any(r["type"] == "observation" and r["state"] == "failed" for r in all_observed)
            else "complete",
            "files_failed": sum(
                r["type"] == "observation" and r["state"] == "failed" for r in all_observed
            ),
            "schema_epoch": SCHEMA_EPOCH,
        }

    def _resolve_rationale_assertions(
        self, conn: sqlite3.Connection, selected_paths: set[str] | None = None
    ) -> None:
        for row in conn.execute("SELECT * FROM rationale"):
            if selected_paths is not None and row["path"] not in selected_paths:
                continue
            occurrence = ev.get(conn, "occurrence", row["occurrence_id"])
            target = row["target_entity_id"]
            assertion = ev.assertion(
                occurrence["entity_id"],
                target,
                "rationale_for",
                row["evidence_id"],
                "EXTRACTED" if target else "UNRESOLVED",
                row["text"],
                reason=None if target else "no_enclosing_definition",
            )
            ev.put(conn, assertion, row["path"])
            conn.execute(
                "UPDATE rationale SET assertion_id=? WHERE id=?", (assertion["id"], row["id"])
            )

    def evidence_records(self) -> list[dict[str, Any]]:
        """Read canonical records deterministically through one pinned RO lease."""
        with self.reader_lease() as reader:
            if dict(reader.db.execute("SELECT key,value FROM manifest")).get("epoch") == "6":
                raise ValueError(
                    "Epoch6 canonical evidence is unavailable; use writer-only migrate_legacy()"
                )
            return list(ev.records(reader.db))

    @staticmethod
    def _resolve_rationale_target(
        comment: RationaleComment, symbols: list[Symbol]
    ) -> tuple[str, int, str] | None:
        """The `rationale_for` edge target (AC-A4-1/AC-A4-2): the TIGHTEST
        enclosing symbol (smallest `line_end - line_start` span) among this
        SAME file's just-extracted `symbols` whose range contains the
        comment's line — ties broken by earliest `line_start`, then name,
        for a fixed deterministic result (mirrors `_enclosing_symbol`'s own
        tie-break rule). `None` when no symbol in this file contains the
        comment's line (e.g. a module-level/top-of-file comment) — a real
        "no enclosing definition" fact, not an error."""
        candidates = [s for s in symbols if s.line_start <= comment.line <= s.line_end]
        if not candidates:
            return None
        best = min(candidates, key=lambda s: (s.line_end - s.line_start, s.line_start, s.name))
        return best.path, best.line_start, best.name

    def _iter_source_files(self):
        """Yield source files, respecting the skip list, in a deterministic
        sorted order (AC-A5-8).

        `Path.rglob("*")` yields in raw filesystem/directory-entry order —
        not stable across filesystems, OSes, or even two runs on the same
        machine after an unrelated file-touch reorders directory entries.
        `_run_build` folds every file it indexes straight into `INSERT`
        statements against autoincrement-PK tables, so an unsorted iteration
        order was, until now, silently baked into `symbols`/`edges`/
        `rationale` row IDs — invisible on a single machine (nothing reads
        rowid as meaning), but exactly the kind of hidden non-determinism
        D6's byte-deterministic export (AC-A5-3/AC-REL-1) exists to catch,
        and the reason this project's total-order discipline treats sorted
        iteration as foundational rather than cosmetic. Sorting here, once,
        removes the need for every downstream consumer to defend against it.
        """
        from atlas_aci.config import DEFAULT_SKIP_PATTERNS

        skip = set(DEFAULT_SKIP_PATTERNS)
        candidates = []
        for path in self.repo.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(self.repo).parts
            if any(p in skip for p in rel_parts):
                continue
            candidates.append(path)
        candidates.sort(key=lambda p: str(p.relative_to(self.repo)))
        yield from candidates

    def _extract(
        self, tree, source: bytes, rel_path: str, lang: str
    ) -> tuple[
        list[Symbol], list[Reference], list[tuple[str, str, str, int]], list[RationaleComment]
    ]:
        """Run the language-specific Tree-sitter query and pull out defs +
        refs + assignment targets + rationale comments.

        Iterates *matches* (not raw captures) so each ``@def.<kind>`` node stays
        grouped with the ``@name`` it owns, and ``#eq?``/``#match?`` predicates
        are applied. A match carrying a ``def.*`` capture yields a Symbol named
        from its ``@name``; a match carrying a ``heritage.<relation>`` tag
        yields an inheritance/mixin/construct Reference (A1); a match carrying
        ``@callee`` yields a call Reference, with `_call_qualification` (D4/
        F18) deciding its `qualified`/`qualifier_name` fields from the same
        match's optional ``@receiver`` capture — no second parse pass. A match
        carrying ``@assign.target`` (checker MAJOR-1, condition 5) yields a
        plain assignment-target name, collected for the shadowing guard
        `_resolve_edges` consults — Python/JS/TS only, see QUERIES' comment.
        A match carrying ``@comment.rationale`` (A4/D5) yields a
        RationaleComment IFF `_match_rationale_comment` recognizes its own
        text as a rationale comment for `lang` — most comments captured by
        this tag are NOT rationale comments and are silently skipped here
        (the capture is "every comment", the filter is "is it a NOTE:/HACK:/
        etc.-prefixed one, or does it reference an ADR/RFC").
        """
        from tree_sitter import Query, QueryCursor
        from tree_sitter_language_pack import get_language

        ts_lang = get_language(cast("SupportedLanguage", lang))
        query = Query(ts_lang, QUERIES[lang])
        matches = QueryCursor(query).matches(tree.root_node)
        declarations = {}
        for _, captures in matches:
            tag = next((key for key in captures if key.startswith("def.")), None)
            if tag and captures.get("name"):
                node = captures[tag][0]
                declarations[node.id] = (
                    node,
                    rust.definition_kind(node, tag.split(".", 1)[1])
                    if lang == "rust"
                    else tag.split(".", 1)[1],
                    self._node_text(source, captures["name"][0]).strip(),
                )
        entities = ev.structural_entities(
            tree,
            declarations,
            rel_path,
            lang,
            scope_overrides=rust.scope_overrides(tree, source) if lang == "rust" else None,
        )
        file_entity = ev.file_entity(rel_path, lang)
        producer = getattr(self, "_capture_producer", None) or ev.producer(self._producer_inputs())
        self._canonical_file_records = [file_entity, producer, *entities.values()]
        self._canonical_file_entity = file_entity["id"]
        self._canonical_imports = []
        consumed_hash = hashlib.sha256(source).hexdigest()

        def unsupported_source_owner(node):
            if lang != "ruby":
                return False
            for ancestor in self._ancestors(node.parent):
                name = ancestor.child_by_field_name("name")
                if (
                    ancestor.type in {"class", "module"}
                    and name is not None
                    and name.type == "scope_resolution"
                    and ancestor.id not in entities
                ):
                    return True
            return False

        def reference_context(node):
            context = self._binding_context(lang, source, node, rel_path)
            if unsupported_source_owner(node):
                context["unsupported_source_owner"] = True
            return context

        def owner(node):
            if unsupported_source_owner(node):
                return file_entity["id"]
            parent = node.parent
            while parent is not None:
                if parent.id in entities:
                    return entities[parent.id]["id"]
                parent = parent.parent
            return file_entity["id"]

        def observe(node, kind, role, entity_id):
            occurrence = ev.occurrence(
                node, rel_path, lang, source, entity_id, kind, role, consumed_hash
            )
            proof = ev.provenance(occurrence, producer["id"])
            self._canonical_file_records.extend((occurrence, proof))
            return occurrence["id"], proof["id"]

        observe(tree.root_node, "definition", "file", file_entity["id"])
        symbols: list[Symbol] = []
        refs: list[Reference] = []
        rationale_comments: list[RationaleComment] = []

        # Each match arrives as (pattern_index, {capture_name: [nodes]}).
        for _pattern_index, caps in matches:
            def_cap = next((c for c in caps if c.startswith("def.")), None)
            heritage_cap = next((c for c in caps if c.startswith("heritage.")), None)
            assign_cap = next((c for c in caps if c.startswith("assign.")), None)
            comment_cap = next((c for c in caps if c.startswith("comment.")), None)
            if def_cap is not None:
                name_nodes = caps.get("name")
                if not name_nodes:
                    continue
                name = self._node_text(source, name_nodes[0]).strip()
                if not name:
                    continue
                def_node = caps[def_cap][0]
                definition_kind = declarations[def_node.id][1]
                occurrence_id, _ = observe(
                    def_node,
                    "definition",
                    "definition:def." + definition_kind,
                    entities[def_node.id]["id"],
                )
                symbols.append(
                    Symbol(
                        name=name,
                        kind=definition_kind,
                        path=rel_path,
                        line_start=def_node.start_point[0] + 1,
                        line_end=def_node.end_point[0] + 1,
                        lang=lang,
                        binding=self._symbol_binding(lang, source, def_node, rel_path),
                        entity_id=entities[def_node.id]["id"],
                        occurrence_id=occurrence_id,
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
                    occurrence_id, proof_id = observe(
                        node, "reference", "reference:" + relation, owner(node)
                    )
                    refs.append(
                        Reference(
                            callee_name=name,
                            path=rel_path,
                            line=node.start_point[0] + 1,
                            lang=lang,
                            col=node.start_point[1],
                            relation=relation,
                            source_entity_id=owner(node),
                            occurrence_id=occurrence_id,
                            evidence_id=proof_id,
                            qualified=True,
                            binding=json.dumps(reference_context(node), sort_keys=True),
                        )
                    )
            elif assign_cap is not None:
                continue  # structural blockers below include parameters/imports too
            elif comment_cap is not None:
                # A4/D5: every `comment`-kind node arrives here; most are
                # NOT rationale comments (ordinary prose) and are silently
                # skipped — only `_match_rationale_comment`-recognized ones
                # become a RationaleComment.
                for node in caps[comment_cap]:
                    raw = self._node_text(source, node)
                    matched = _match_rationale_comment(lang, raw)
                    if matched is None:
                        continue
                    line_offset, text, label = matched
                    occurrence_id, proof_id = observe(
                        node, "rationale", "rationale", file_entity["id"]
                    )
                    target_entity = owner(node)
                    rationale_comments.append(
                        RationaleComment(
                            path=rel_path,
                            line=node.start_point[0] + line_offset + 1,
                            text=text,
                            label=label,
                            lang=lang,
                            occurrence_id=occurrence_id,
                            evidence_id=proof_id,
                            target_entity_id=target_entity
                            if target_entity != file_entity["id"]
                            else None,
                        )
                    )
            elif "callee" in caps:
                qualified, qualifier_name = self._call_qualification(lang, source, caps)
                for node in caps["callee"]:
                    if lang == "rust":
                        qualified, qualifier_name = False, rust.qualifier(node, source)
                    name = self._node_text(source, node).strip()
                    if not name:
                        continue
                    occurrence_id, proof_id = observe(
                        node, "reference", "reference:call", owner(node)
                    )
                    refs.append(
                        Reference(
                            callee_name=name,
                            path=rel_path,
                            line=node.start_point[0] + 1,
                            lang=lang,
                            col=node.start_point[1],
                            relation="call",
                            source_entity_id=owner(node),
                            occurrence_id=occurrence_id,
                            evidence_id=proof_id,
                            qualified=qualified,
                            qualifier_name=qualifier_name,
                            binding=json.dumps(
                                {
                                    **reference_context(node),
                                    "receiver": self._node_text(source, caps["receiver"][0]).strip()
                                    if caps.get("receiver")
                                    else None,
                                },
                                sort_keys=True,
                            ),
                        )
                    )

        if lang == "python":
            for import_node, target_node, description in python_imports.extract(tree, source):
                context = self._binding_context(lang, source, import_node, rel_path)
                occurrence_id, proof_id = observe(
                    target_node, "reference", "import:" + description["name"], owner(import_node)
                )
                self._canonical_imports.append(
                    {
                        "path": rel_path,
                        "local_name": description["local_name"],
                        "scope": context["scope"],
                        "byte_start": import_node.start_byte,
                        "description": json.dumps(description, sort_keys=True),
                        "lang": lang,
                        "source_entity_id": owner(import_node),
                        "occurrence_id": occurrence_id,
                        "evidence_id": proof_id,
                        "assertion_id": None,
                    }
                )
        self._canonical_bindings = {}

        def binding_capture(fact, node):
            self._canonical_bindings[fact] = observe(
                node, "reference", "binding:" + fact[2] + ":" + fact[0], file_entity["id"]
            )

        blockers = self._binding_blockers(
            tree.root_node, source, lang, rel_path, capture=binding_capture
        )
        return symbols, refs, blockers, rationale_comments

    @staticmethod
    def _scope_key(node, rel_path: str, lang: str) -> str:
        return f"{lang}:{rel_path}:{node.type}:{node.start_byte}:{node.end_byte}"

    @staticmethod
    def _ancestors(node) -> list[Any]:
        result = []
        while node is not None:
            result.append(node)
            node = node.parent
        return result

    @staticmethod
    def _scope_nodes(node, lang: str) -> list[Any]:
        kinds = {
            "python": {"function_definition", "class_definition", "lambda"},
            "ruby": {
                "method",
                "singleton_method",
                "class",
                "module",
                "singleton_class",
                "block",
                "do_block",
            },
            "javascript": {
                "function_declaration",
                "function_expression",
                "arrow_function",
                "method_definition",
                "class_declaration",
                "statement_block",
                "catch_clause",
                "for_statement",
                "for_in_statement",
            },
            "typescript": {
                "function_declaration",
                "function_expression",
                "arrow_function",
                "method_definition",
                "class_declaration",
                "statement_block",
                "catch_clause",
                "for_statement",
                "for_in_statement",
            },
        }.get(lang, set())
        result = []
        function_seen = False
        for ancestor in CodeGraph._ancestors(node):
            if ancestor.type not in kinds:
                continue
            if lang == "python" and ancestor.type == "class_definition" and function_seen:
                continue  # a method/closure never inherits the class-body namespace
            result.append(ancestor)
            if ancestor.type in {"function_definition", "lambda"}:
                function_seen = True
        return result

    @staticmethod
    def _binding_context(
        lang: str, source: bytes, node, rel_path: str, *, declaration: bool = False
    ) -> dict[str, Any]:
        ancestors = CodeGraph._ancestors(node.parent if declaration else node)
        scopes = CodeGraph._scope_nodes(node.parent if declaration else node, lang)
        classes = [
            n
            for n in ancestors
            if n.type in {"class", "module", "class_definition", "class_declaration"}
        ]
        owner = None
        namespace = "::".join(
            CodeGraph._node_text(source, n.child_by_field_name("name"))
            for n in reversed(classes)
            if n.child_by_field_name("name") is not None
        )
        if classes:
            owner = (
                "ruby:" + namespace
                if lang == "ruby"
                else CodeGraph._scope_key(classes[0], rel_path, lang)
            )
        functions = [
            n
            for n in ancestors
            if n.type
            in {
                "function_definition",
                "function_declaration",
                "function_expression",
                "arrow_function",
                "method_definition",
                "method",
                "singleton_method",
            }
        ]
        method = None
        for fn in functions:
            parents = CodeGraph._ancestors(fn.parent)
            # Python decorated_definition and class-body block are transparent.
            container = next(
                (
                    p
                    for p in parents
                    if p.type
                    in {
                        "class",
                        "module",
                        "class_definition",
                        "class_declaration",
                        "function_definition",
                        "function_declaration",
                        "method",
                        "method_definition",
                        "singleton_method",
                    }
                ),
                None,
            )
            if container is not None and container.type in {
                "class",
                "module",
                "class_definition",
                "class_declaration",
            }:
                method = fn
                break
        category = "static" if lang == "ruby" and owner else "free"
        self_name = self_scope = None
        if method is not None:
            category = "instance"
            if lang == "ruby":
                if method.type == "singleton_method" or any(
                    n.type == "singleton_class"
                    for n in CodeGraph._ancestors(method.parent)
                    if not classes or n.start_byte >= classes[0].start_byte
                ):
                    category = "static"
            elif lang in {"javascript", "typescript"}:
                if any(c.type == "static" for c in method.children):
                    category = "static"
                if any(c.type in {"get", "set"} for c in method.children):
                    category = "unknown"
            elif lang == "python":
                if method.parent is not None and method.parent.type == "decorated_definition":
                    decorators = [
                        CodeGraph._node_text(source, c).lstrip("@").strip()
                        for c in method.parent.named_children
                        if c.type == "decorator"
                    ]
                    category = dict(staticmethod="static", classmethod="class").get(
                        decorators[0] if len(decorators) == 1 else "", "unknown"
                    )
                params = method.child_by_field_name("parameters")
                if (
                    category == "instance"
                    and params is not None
                    and params.named_children
                    and params.named_children[0].type == "identifier"
                ):
                    self_name = CodeGraph._node_text(source, params.named_children[0])
                    self_scope = CodeGraph._scope_key(method, rel_path, lang)
        source_node = next(
            (
                n
                for n in ancestors
                if n.type
                in {
                    "class",
                    "module",
                    "class_definition",
                    "class_declaration",
                    "function_definition",
                    "function_declaration",
                    "method_definition",
                    "method",
                    "singleton_method",
                }
            ),
            None,
        )
        source_name = source_kind = None
        if source_node is not None:
            name = source_node.child_by_field_name("name")
            source_name = CodeGraph._node_text(source, name) if name is not None else None
            source_kind = (
                "class"
                if source_node.type in {"class", "class_definition", "class_declaration"}
                else "module"
                if source_node.type == "module"
                else "method"
                if source_node.type in {"method", "singleton_method", "method_definition"}
                else "function"
            )
        return {
            "scope": CodeGraph._scope_key(scopes[0], rel_path, lang)
            if scopes
            else f"{lang}:{rel_path}:root",
            "scopes": [CodeGraph._scope_key(n, rel_path, lang) for n in scopes]
            + [f"{lang}:{rel_path}:root"],
            "identity": CodeGraph._scope_key(node, rel_path, lang),
            "byte": node.start_byte,
            "owner": owner,
            "namespace": namespace,
            "category": category,
            "self_name": self_name,
            "self_scope": self_scope,
            "this_allowed": method is not None
            and all(fn.type == "arrow_function" for fn in functions[: functions.index(method)]),
            "source_name": source_name,
            "source_kind": source_kind,
            "receiver": CodeGraph._node_text(source, node.parent.child_by_field_name("object"))
            if lang == "python"
            and node.parent is not None
            and node.parent.type == "attribute"
            and node.parent.child_by_field_name("attribute") == node
            else None,
            "unsupported": any(
                n.type
                in {
                    "list_comprehension",
                    "dictionary_comprehension",
                    "set_comprehension",
                    "generator_expression",
                    "lambda",
                    "singleton_class",
                }
                for n in ancestors
            ),
        }

    @staticmethod
    def _symbol_binding(lang: str, source: bytes, node, rel_path: str) -> str:
        result = CodeGraph._binding_context(lang, source, node, rel_path, declaration=True)
        if lang == "python":
            result["import_export_safe"] = python_imports.unconditional(node)
        # The declaration's method category is its own, not its enclosing method.
        own = CodeGraph._binding_context(lang, source, node, rel_path)
        result["category"] = own["category"]
        if node.type in {"class", "module", "class_definition", "class_declaration"}:
            result["category"] = "class"
            result["namespace"] = "::".join(
                filter(
                    None,
                    [
                        result["namespace"],
                        CodeGraph._node_text(source, node.child_by_field_name("name")),
                    ],
                )
            )
        if node.type in {"function_definition", "function_declaration", "function_expression"}:
            container = next(
                (
                    n
                    for n in CodeGraph._ancestors(node.parent)
                    if n.type
                    in {
                        "class_definition",
                        "class_declaration",
                        "function_definition",
                        "function_declaration",
                        "method_definition",
                    }
                ),
                None,
            )
            if container is None or container.type not in {"class_definition", "class_declaration"}:
                result["owner"] = None
                result["category"] = "free"
        if node.type == "singleton_method":
            receiver = node.child_by_field_name("object")
            if receiver is not None and receiver.type == "constant":
                result["owner"] = "ruby:" + CodeGraph._node_text(source, receiver)
            elif receiver is None or receiver.type != "self":
                result["category"] = "unknown"
        if (
            lang == "python"
            and node.parent is not None
            and node.parent.type == "decorated_definition"
            and own["category"] == "free"
        ):
            result["category"] = "unknown"
        return json.dumps(result, sort_keys=True)

    @staticmethod
    def _binding_blockers(
        root, source: bytes, lang: str, rel_path: str, capture=None
    ) -> list[tuple[str, str, str, int]]:
        """Small structural blocker set, never a type or import resolver."""
        if lang not in {"python", "ruby", "javascript", "typescript"}:
            return []
        result = []

        def append(name, scope, kind, node):
            fact = (name, scope, kind, node.start_byte)
            result.append(fact)
            if capture is not None:
                capture(fact, node)

        stack = [root]
        while stack:
            node = stack.pop()
            stack.extend(reversed(node.named_children))
            kind = None
            targets = []
            scope_node = node
            if node.type in {
                "parameters",
                "formal_parameters",
                "method_parameters",
                "lambda_parameters",
            }:
                kind = "parameter"
                targets = list(node.named_children)
            elif node.type in {
                "assignment",
                "assignment_expression",
                "augmented_assignment",
                "named_expression",
            }:
                kind = "assignment"
                left = node.child_by_field_name("left") or node.child_by_field_name("name")
                targets = [left] if left is not None else []
            elif lang == "python" and node.type == "for_statement":
                kind = "assignment"
                target = node.child_by_field_name("left")
                targets = [target] if target is not None else []
            elif lang == "python" and node.type == "as_pattern":
                kind = "assignment"
                target = node.child_by_field_name("alias")
                targets = [target] if target is not None else []
            elif lang == "python" and node.type == "delete_statement":
                kind = "unsupported"
                targets = list(node.named_children)
            elif lang in {"javascript", "typescript"} and node.type == "for_in_statement":
                kind = "assignment"
                target = node.child_by_field_name("left")
                targets = [target] if target is not None else []
                if not any(c.type in {"let", "const"} for c in node.children):
                    scope_node = next(
                        (
                            p
                            for p in CodeGraph._ancestors(node)
                            if p.type
                            in {
                                "function_declaration",
                                "function_expression",
                                "arrow_function",
                                "method_definition",
                            }
                        ),
                        root,
                    )
            elif node.type == "catch_clause":
                kind = "parameter"
                target = node.child_by_field_name("parameter")
                targets = [target] if target is not None else []
            elif node.type == "variable_declarator":
                kind = "assignment"
                target = node.child_by_field_name("name")
                targets = [target] if target is not None else []
                if node.parent is not None and node.parent.type == "variable_declaration":
                    scope_node = next(
                        (
                            p
                            for p in CodeGraph._ancestors(node)
                            if p.type
                            in {
                                "function_declaration",
                                "function_expression",
                                "arrow_function",
                                "method_definition",
                            }
                        ),
                        root,
                    )
            elif node.type in {"global_statement", "nonlocal_statement"}:
                kind = "unsupported"
                targets = list(node.named_children)
            elif node.type in {"import_statement", "import_from_statement"}:
                kind = "import"
                if lang == "python":
                    for child in node.named_children:
                        if child == node.child_by_field_name("module_name"):
                            continue
                        if child.type == "aliased_import":
                            target = child.child_by_field_name("alias")
                            if target is not None:
                                targets.append(target)
                        elif child.type == "dotted_name":
                            targets.extend(child.named_children[:1])
                        elif child.type == "wildcard_import":
                            scopes = CodeGraph._scope_nodes(node, lang)
                            scope = (
                                CodeGraph._scope_key(scopes[0], rel_path, lang)
                                if scopes
                                else f"{lang}:{rel_path}:root"
                            )
                            append("*", scope, "unsupported", node)
                else:
                    clause = next(
                        (c for c in node.named_children if c.type == "import_clause"), None
                    )
                    if clause is not None:
                        pending = list(clause.named_children)
                        while pending:
                            child = pending.pop()
                            if child.type == "import_specifier":
                                target = child.child_by_field_name(
                                    "alias"
                                ) or child.child_by_field_name("name")
                                if target is not None:
                                    targets.append(target)
                            elif child.type == "identifier":
                                targets.append(child)
                            else:
                                pending.extend(child.named_children)
            if kind is None:
                continue
            scopes = CodeGraph._scope_nodes(scope_node, lang)
            scope = (
                CodeGraph._scope_key(scopes[0], rel_path, lang)
                if scopes
                else f"{lang}:{rel_path}:root"
            )
            pending = list(targets)
            while pending:
                target = pending.pop()
                if target.type in {
                    "identifier",
                    "constant",
                    "shorthand_property_identifier_pattern",
                }:
                    append(CodeGraph._node_text(source, target), scope, kind, target)
                elif target.type in {
                    "default_parameter",
                    "typed_parameter",
                    "typed_default_parameter",
                    "optional_parameter",
                    "required_parameter",
                }:
                    name = target.child_by_field_name("name") or target.child_by_field_name(
                        "pattern"
                    )
                    if name is None:
                        name = next(
                            (c for c in target.named_children if c.type == "identifier"), None
                        )
                    if name is not None:
                        pending.append(name)
                elif target.type in {
                    "tuple_pattern",
                    "as_pattern_target",
                    "pattern_list",
                    "list_pattern",
                    "list_splat_pattern",
                    "dictionary_splat_pattern",
                    "rest_pattern",
                    "array_pattern",
                    "object_pattern",
                    "pair_pattern",
                }:
                    pending.extend(target.named_children)
                elif target.type == "assignment_pattern":
                    left = target.child_by_field_name("left")
                    if left is not None:
                        pending.append(left)
                elif target.type not in {
                    "attribute",
                    "member_expression",
                    "subscript",
                    "subscript_expression",
                    "element_reference",
                    "instance_variable",
                    "class_variable",
                    "global_variable",
                }:
                    # Unknown binding patterns invalidate this scope instead of
                    # silently borrowing a convenient outer declaration.
                    append("*", scope, "unsupported", target)
        return sorted(set(result))

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
            return receiver_nodes[0].type in ("constant", "scope_resolution"), CodeGraph._node_text(
                source, receiver_nodes[0]
            ).strip()

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

    def _resolve_edges(
        self, conn: sqlite3.Connection, selected_paths: set[str] | None = None
    ) -> int:
        """Filter structural applicability before assigning confidence. No global
        name fallback is permitted for the scoped programming languages.
        """
        if selected_paths is None:
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM assertions")
            conn.execute("UPDATE refs SET unresolved_reason = NULL")
            conn.execute("UPDATE import_bindings SET assertion_id=NULL")
            conn.execute("UPDATE rationale SET assertion_id=NULL")
        else:
            for path in sorted(selected_paths):
                conn.execute("DELETE FROM edges WHERE source_path=?", (path,))
                conn.execute("DELETE FROM assertions WHERE path=?", (path,))
                conn.execute("UPDATE refs SET unresolved_reason=NULL WHERE path=?", (path,))
                conn.execute("UPDATE import_bindings SET assertion_id=NULL WHERE path=?", (path,))
                conn.execute("UPDATE rationale SET assertion_id=NULL WHERE path=?", (path,))
        symbols: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in conn.execute("SELECT * FROM symbols ORDER BY path, line_start, name, id"):
            item = dict(row)
            item["context"] = json.loads(item["binding"])
            symbols.setdefault((item["lang"], item["name"]), []).append(item)
        blockers: dict[tuple[str, str, str], list[str]] = {}
        for row in conn.execute(
            "SELECT * FROM assignment_targets ORDER BY path, scope, name, byte_start"
        ):
            blockers.setdefault((row["path"], row["scope"], row["name"]), []).append(
                row["binding_kind"]
            )

        import_bindings: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in conn.execute(
            "SELECT * FROM import_bindings ORDER BY path, scope, local_name, byte_start"
        ):
            item = dict(row)
            import_bindings.setdefault(
                (item["path"], item["scope"], item["local_name"]), []
            ).append(item)
        indexed_python = {
            row[0] for row in conn.execute("SELECT path FROM files WHERE lang='python'")
        }
        all_symbols = [item for group in symbols.values() for item in group]

        def import_target(binding):
            description = json.loads(binding["description"])
            hits = blockers.get((binding["path"], binding["scope"], binding["local_name"]), [])
            if any(kind != "import" for kind in hits):
                return [], "shadowed_noncallable"
            if (
                len(
                    import_bindings.get(
                        (binding["path"], binding["scope"], binding["local_name"]), []
                    )
                )
                != 1
            ):
                return [], "relative_import_ambiguous_binding"
            return python_imports.resolve(
                binding["path"], description, indexed_python, all_symbols, blockers
            )

        def visible(ref, context, name):
            for scope in context.get("scopes", ["root"]):
                hits = blockers.get((ref["path"], scope, name), [])
                if blockers.get((ref["path"], scope, "*")):
                    return [], "unsupported_scope"
                if hits:
                    if hits == ["import"]:
                        known = import_bindings.get((ref["path"], scope, name), [])
                        if (
                            len(known) == 1
                            and json.loads(known[0]["description"])["reason"] is None
                        ):
                            if known[0]["byte_start"] >= context.get("byte", -1):
                                return [], "relative_import_before_binding"
                            return import_target(known[0])
                    return (
                        [],
                        "unknown_import_alias"
                        if "import" in hits
                        else "unsupported_scope"
                        if "unsupported" in hits
                        else "shadowed_noncallable",
                    )
                found = [
                    c
                    for c in symbols.get((ref["lang"], name), [])
                    if c["path"] == ref["path"]
                    and c["context"].get("scope") == scope
                    and not (ref["lang"] in {"javascript", "typescript"} and c["kind"] == "method")
                ]
                if found:
                    if any(c["context"].get("category") == "unknown" for c in found):
                        return [], "unsupported_scope"
                    if ref["lang"] in {"javascript", "typescript"} and any(
                        c["kind"] == "class"
                        and c["context"].get("byte", 0) > context.get("byte", 0)
                        for c in found
                    ):
                        return [], "unsupported_scope"
                    return found, None
            return [], "no_visible_target"

        def ruby_classes(ref, context, name):
            for scope in context.get("scopes", ["root"]):
                if blockers.get((ref["path"], scope, name)):
                    return [], "shadowed_noncallable"
            parts = context.get("namespace", "").split("::") if context.get("namespace") else []
            namespaces = ["::".join(parts[:i]) for i in range(len(parts), -1, -1)]
            for namespace in namespaces:
                full = (
                    name.lstrip(":") if "::" in name else "::".join(filter(None, [namespace, name]))
                )
                found = [
                    c
                    for c in symbols.get(("ruby", name.split("::")[-1]), [])
                    if c["kind"] in _CLASS_KINDS and c["context"].get("namespace") == full
                ]
                if found:
                    return found, None
            return [], "no_visible_target"

        def resolve_reference(ref):
            context = json.loads(ref["binding"])
            relation = ref["relation"]
            receiver = context.get("receiver")
            qualified = False
            reason = None
            lang = ref["lang"]
            if lang == "rust":
                candidates, reason = [], "rust_semantic_binding_unavailable"
            elif context.get("unsupported_source_owner"):
                candidates, reason = [], "unsupported_source_owner"
            elif lang not in {"python", "ruby", "javascript", "typescript"}:
                candidates = symbols.get((lang, ref["callee_name"]), [])
            elif context.get("unsupported"):
                candidates, reason = [], "unsupported_scope"
            elif relation in _CLASS_TARGET_RELATIONS:
                if receiver:
                    candidates, reason = [], "unknown_receiver"
                else:
                    candidates, reason = (
                        ruby_classes(ref, context, ref["callee_name"])
                        if lang == "ruby"
                        else visible(ref, context, ref["callee_name"])
                    )
                qualified = True
            elif receiver or (lang == "ruby" and context.get("owner")):
                owners = []
                category = None
                if (
                    (lang == "ruby" and receiver in {None, "self"})
                    or (lang in {"javascript", "typescript"} and receiver == "this")
                    or (lang == "python" and receiver and receiver == context.get("self_name"))
                ):
                    owner = context.get("owner")
                    category = context.get("category")
                    allowed = bool(owner)
                    if lang == "python":
                        allowed = category == "instance" and receiver == context.get("self_name")
                        found_parameter = False
                        for scope in context.get("scopes", []):
                            hits = blockers.get((ref["path"], scope, receiver), [])
                            if hits:
                                found_parameter = scope == context.get("self_scope") and hits == [
                                    "parameter"
                                ]
                                break
                        allowed = allowed and found_parameter
                    elif lang in {"javascript", "typescript"}:
                        allowed = allowed and context.get("this_allowed", False)
                    owners = [owner] if allowed else []
                    reason = "unknown_receiver" if not allowed else None
                else:
                    known, reason = (
                        ruby_classes(ref, context, receiver)
                        if lang == "ruby" and ref["qualified"]
                        else visible(ref, context, receiver)
                    )
                    known = [c for c in known if c["kind"] in _CLASS_KINDS]
                    owners = [
                        "ruby:" + c["context"]["namespace"]
                        if lang == "ruby"
                        else c["context"]["identity"]
                        for c in known
                    ]
                    category = "static" if lang in {"ruby", "javascript", "typescript"} else None
                    qualified = bool(owners)
                    if not owners and reason in {None, "no_visible_target"}:
                        reason = "unknown_receiver"
                candidates = [
                    c
                    for c in symbols.get((lang, ref["callee_name"]), [])
                    if c["context"].get("owner") in owners
                    and (lang == "ruby" or c["path"] == ref["path"])
                    and c["context"].get("category") != "unknown"
                    and (category is None or c["context"].get("category") == category)
                ]
                if owners and not candidates:
                    reason = "owner_has_no_member"
            else:
                candidates, reason = visible(ref, context, ref["callee_name"])
            kinds = (
                _HERITAGE_CANDIDATE_KINDS
                if relation in _CLASS_TARGET_RELATIONS
                else _CALL_CANDIDATE_KINDS
            )
            candidates = [c for c in candidates if c["kind"] in kinds]
            if not candidates:
                unresolved = reason or "no_visible_target"
                if (lang, ref["callee_name"]) in getattr(
                    self, "_stale_target_names", set()
                ) and unresolved in {
                    "no_visible_target",
                    "relative_import_missing_target",
                    "relative_import_missing_module",
                }:
                    unresolved = "stale_target"
                conn.execute(
                    "UPDATE refs SET unresolved_reason = ? WHERE id = ?", (unresolved, ref["id"])
                )
                if ref["source_entity_id"] and ref["evidence_id"]:
                    assertion = ev.assertion(
                        ref["source_entity_id"],
                        None,
                        relation,
                        ref["evidence_id"],
                        "UNRESOLVED",
                        ref["callee_name"],
                        reason=unresolved,
                    )
                    ev.put(conn, assertion, ref["path"])
                return
            effective_relation = relation
            if relation == "call" and all(c["kind"] in _CLASS_KINDS for c in candidates):
                effective_relation = "construct"
                qualified = True
            if len(candidates) == 1:
                confidence = "EXTRACTED" if qualified else "INFERRED"
                target_path, target_line, target_name = (
                    candidates[0]["path"],
                    candidates[0]["line_start"],
                    candidates[0]["name"],
                )
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
            source_name, source_kind = context.get("source_name"), context.get("source_kind")
            if lang not in {"python", "ruby", "javascript", "typescript"}:
                if ref["source_entity_id"]:
                    # The actual captured AST owner is authoritative. Same-line
                    # sibling declarations are not enclosing scopes.
                    source_entity = ev.get(conn, "entity", ref["source_entity_id"])
                    source_name, source_kind = (
                        (None, None)
                        if source_entity["kind"] == "file"
                        else (source_entity["name"], source_entity["kind"])
                    )
                else:
                    source_name, source_kind = self._enclosing_symbol(
                        conn, ref["path"], ref["line"]
                    )
            assertion_id = None
            if ref["source_entity_id"] and ref["evidence_id"]:
                assertion = ev.assertion(
                    ref["source_entity_id"],
                    candidates[0]["entity_id"] if len(candidates) == 1 else None,
                    effective_relation,
                    ref["evidence_id"],
                    confidence,
                    ref["callee_name"],
                    candidates=[candidate["entity_id"] for candidate in candidates]
                    if len(candidates) > 1
                    else [],
                    reason="ambiguous_target" if len(candidates) > 1 else None,
                )
                ev.put(conn, assertion, ref["path"])
                assertion_id = assertion["id"]
            conn.execute(
                "INSERT INTO edges(relation, source_path, source_line, source_col, source_name, "
                "source_kind, callee_name, confidence, target_path, target_line, target_name, "
                "candidates, lang, assertion_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    effective_relation,
                    ref["path"],
                    ref["line"],
                    ref["col"],
                    source_name,
                    source_kind,
                    ref["callee_name"],
                    confidence,
                    target_path,
                    target_line,
                    target_name,
                    candidates_json,
                    lang,
                    assertion_id,
                ),
            )

        def resolve_import(binding):
            found, reason = import_target(binding)
            description = json.loads(binding["description"])
            target = found[0]["entity_id"] if found else None
            assertion = ev.assertion(
                binding["source_entity_id"],
                target,
                "import",
                binding["evidence_id"],
                "INFERRED" if found else "UNRESOLVED",
                description["name"],
                reason=None if found else reason,
            )
            ev.put(conn, assertion, binding["path"])
            conn.execute(
                "UPDATE import_bindings SET assertion_id=? WHERE occurrence_id=?",
                (assertion["id"], binding["occurrence_id"]),
            )

        references_by_path: dict[str, list[sqlite3.Row]] = {}
        for ref in conn.execute("SELECT * FROM refs ORDER BY path,line,col,callee_name"):
            references_by_path.setdefault(ref["path"], []).append(ref)
        imports_by_path: dict[str, list[dict[str, Any]]] = {}
        for group in import_bindings.values():
            for binding in group:
                imports_by_path.setdefault(binding["path"], []).append(binding)
        paths = {
            row[0]
            for row in conn.execute("SELECT path FROM files UNION SELECT path FROM rationale")
        }
        paths |= references_by_path.keys() | imports_by_path.keys()
        if selected_paths is not None:
            paths &= selected_paths
        for path in sorted(paths):
            self._resolve_binding_unit(
                conn,
                path,
                resolve_reference,
                resolve_import,
                references_by_path.get(path, []),
                imports_by_path.get(path, []),
            )
        return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    def _resolve_binding_unit(
        self, conn, path, resolve_reference, resolve_import, references, imports
    ):
        """Execute real binding for one path from the once-read ordered inputs."""
        for ref in references:
            resolve_reference(ref)
        for binding in imports:
            resolve_import(binding)

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

    def _historical_projection(self, table, predicate):
        """History is separately presented; never enters active query/analysis SQL."""
        metadata = self._observation_records(self.db)
        observations = {r["path"]: r for r in metadata if r["type"] == "observation"}
        result = {}
        for history in (r for r in metadata if r["type"] == "history"):
            records = {r["id"]: r for r in history["records"]}
            for projection in history["records"]:
                if (
                    projection["type"] != "projection"
                    or projection["table"] != table
                    or not predicate(projection["row"])
                ):
                    continue
                row, links = projection["row"], projection["links"]
                assertion = records.get(links.get("assertion_id"))
                occurrence_id = links.get("occurrence_id")
                if assertion:
                    proof = records[assertion["evidence_id"]]
                    occurrence_id = proof["occurrence_id"]
                proofs = [
                    r
                    for r in records.values()
                    if r["type"] == "evidence" and r["occurrence_id"] == occurrence_id
                ]
                occurrence = records.get(occurrence_id)
                path = occurrence["path"] if occurrence else row.get("path", history["path"])
                observed = observations.get(path)
                freshness = {
                    "state": "stale",
                    "retained_for_failed_path": history["path"],
                    "fact_source_sha256": occurrence["source_sha256"] if occurrence else None,
                    "observed_source_sha256": observed["observed_source_sha256"]
                    if observed
                    else None,
                    "producer_ids": sorted({r["producer_id"] for r in proofs}),
                    "evidence_ids": sorted(r["id"] for r in proofs),
                    "occurrence_id": occurrence_id,
                }
                shaped = (
                    self._edge_row_to_dict(row)
                    if table == "edges"
                    else {k: v for k, v in row.items() if k != "binding"}
                )
                result[(projection["id"], history["path"])] = dict(shaped, freshness=freshness)
        return [result[key] for key in sorted(result)[: self.query_limit]]

    def _historical_payload(self, table, predicate, field):
        rows = self._historical_projection(table, predicate)
        return {field: rows} if rows else {}

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

        for row in [*defs, *refs]:
            row.pop("binding", None)
        return {
            "definitions": defs,
            "references": refs,
            **self._historical_payload(
                "symbols",
                lambda row: row["name"] == name and (kind in {None, "any"} or row["kind"] == kind),
                "historical_definitions",
            ),
            **self._historical_payload(
                "refs", lambda row: row["callee_name"] == name, "historical_references"
            ),
        }

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
        cheap, ready-made query: the object A2's `god_nodes()`, a future
        A3's community detection, and the D3a probe are all specified to
        consume — this is the storage+retrieval primitive they filter
        through. AMBIGUOUS is excluded here — never fanned out to its
        candidates, never given fractional weight (AC-NEG-7) — but this is
        an *analysis-time* filter, not a storage-time drop: AMBIGUOUS edges
        remain in `edges` and are still returned in full by
        `callers_of`/`subclasses_of` (D4a, AC-A1-2/AC-A1-5 preserved).
        """
        rows = self.db.execute(
            "SELECT * FROM edges WHERE confidence IN ('EXTRACTED', 'INFERRED') "
            "ORDER BY source_path, source_line, source_col, callee_name"
        ).fetchall()
        return [self._edge_row_to_dict(r) for r in rows]

    def god_nodes(self) -> dict[str, Any]:
        """A2 (D3/D4a): degree-centrality ranking over the confident
        subgraph. No clustering, no cluster/community detection of any
        kind (that is a separate, not-yet-built workstream), no
        graph-algorithm runtime dependency (AC-A2-2) — pure Python
        arithmetic over `confident_edges()`'s own output.

        Structural AC-NEG-7 compliance: this method's *entire* analysis
        input is `self.confident_edges()`'s return value — not a second,
        independently-written SQL filter that could silently drift from it.
        AMBIGUOUS cannot leak into degree by construction, not because a
        filter happened to be applied correctly this time
        (`test_god_nodes_input_is_exactly_confident_edges` pins this by
        monkeypatching `confident_edges` and checking the ranking follows).

        Degree semantics — a judgment call the frozen AC-A2-1 leaves open,
        surfaced rather than resolved silently: D4a states plainly that
        "degree centrality (both in- and out-degree) is computed over the
        confident subgraph," and frames a god node as "a symbol many
        references *definitely/probably* reach" — an IN-degree definition
        (things reaching this symbol). Out-degree is computed for the same
        reason D4a computes it: one shared analysis graph, consistent with
        what A3/the D3a probe need. This implementation exposes BOTH
        `in_degree` and `out_degree` on every node, and ranks by their SUM
        (`degree`) as the primary sort key — "degree centrality" read
        literally, unqualified, sums both directions. A consumer who wants
        the "many things call this" reading specifically can re-sort the
        returned list by `in_degree`.

        A node's identity is (path, line, name) — a *specific* symbol
        definition, not a bare name string (two same-named methods in two
        different classes are two different god nodes, never conflated).
        In-degree keys directly off `target` (`_resolve_edges` already
        resolves it to an exact `(path, line_start, name)` triple — no
        ambiguity). Out-degree requires resolving the edge's `source` (the
        call site's enclosing symbol) back to that SAME kind of exact
        identity: `edges.source_line` is the *call site's* line, not the
        enclosing symbol's own `line_start`, so a plain `(source_path,
        source_name, source_kind)` grouping could conflate two identically-
        named-and-kinded definitions in the same file (rare, but real) —
        the lookup below re-derives the exact enclosing definition (name +
        kind + line-range containment against `symbols`) per edge instead.
        """
        confident = self.confident_edges()

        in_degree: dict[tuple[str, int, str], int] = {}
        out_degree: dict[tuple[str, int, str], int] = {}
        node_kind: dict[tuple[str, int, str], str | None] = {}

        for edge in confident:
            # Checker MINOR-1: this guard is a second, redundant barrier,
            # not the only one — `confident_edges()`'s own SQL filter
            # (`WHERE confidence IN ('EXTRACTED', 'INFERRED')`) already
            # means no edge reaching this loop has `target is None` in
            # practice (only an AMBIGUOUS-shaped row can carry that). The
            # docstring above claims AMBIGUOUS "cannot leak into degree by
            # construction, not because a filter happened to be applied
            # correctly" — that claim used to be true only for in_degree
            # (gated on `target is not None`) and false for out_degree
            # (the source/out-degree branch ran unconditionally, on the
            # unstated assumption that `target` is never `None` here).
            # Skipping the WHOLE edge when `target is None` — matching
            # `communities()`'s identical guard below — makes both
            # in_degree and out_degree share the same structural
            # AMBIGUOUS-can't-reach-here guarantee the docstring claims,
            # rather than one path merely happening to be safe today.
            target = edge["target"]
            if target is None:
                continue
            key = (target["path"], target["line"], target["name"])
            in_degree[key] = in_degree.get(key, 0) + 1
            node_kind.setdefault(key, self._target_kind(target))

            source_key, source_kind = self._resolve_source_node(edge["source"])
            if source_key is not None:
                out_degree[source_key] = out_degree.get(source_key, 0) + 1
                node_kind.setdefault(source_key, source_kind)

        nodes: list[dict[str, Any]] = [
            {
                "path": key[0],
                "line": key[1],
                "name": key[2],
                "kind": node_kind.get(key),
                "in_degree": in_degree.get(key, 0),
                "out_degree": out_degree.get(key, 0),
                "degree": in_degree.get(key, 0) + out_degree.get(key, 0),
            }
            for key in (set(in_degree) | set(out_degree))
        ]
        # Total order (D6/checker MINOR-4 lesson): degree ties are common at
        # the tail, so rank is never left to dict/set iteration order —
        # degree DESC primary, then (path, line, name) ASC, a key that is
        # unique per node.
        nodes.sort(key=lambda n: (-cast(int, n["degree"]), n["path"], n["line"], n["name"]))

        return {
            "god_nodes": nodes,
            # AC-A2-3: makes the analysis-graph-versus-returned-edges
            # divergence visible. `analysis_basis` names WHAT was analyzed;
            # `ambiguous_edges_excluded` is the count of edges in the FULL
            # `edges` table that were withheld from that analysis (every
            # AMBIGUOUS row, full stop — not merely ones that happen to
            # touch a ranked node); `resolved_edge_count` is exactly
            # `len(confident_edges())`, i.e. how many edges the ranking
            # WAS computed over. A consumer who then calls `callers_of`/
            # `subclasses_of` and sees AMBIGUOUS edges too is not seeing a
            # contradiction — these three fields say plainly that the
            # ranking never included them.
            "analysis_basis": "confident_edges",
            "ambiguous_edges_excluded": self._ambiguous_edge_count(),
            "resolved_edge_count": len(confident),
        }

    def _ambiguous_edge_count(self) -> int:
        """The count of AMBIGUOUS edges in the FULL `edges` table —
        shared by `god_nodes()`/`communities()`'s `ambiguous_edges_excluded`
        field (AC-A2-3). Independent of `confident_edges()` on purpose: it
        counts what was WITHHELD, not a property of what was analyzed."""
        row = self.db.execute(
            "SELECT COUNT(*) FROM edges WHERE confidence = 'AMBIGUOUS'"
        ).fetchone()
        return cast(int, row[0])

    def _target_kind(self, target: dict[str, Any]) -> str | None:
        """The resolved `kind` of an edge's target endpoint. `target_line`
        (`_resolve_edges`) is already the target symbol's exact
        `line_start` — an exact-match lookup, no range/ambiguity risk."""
        row = self.db.execute(
            "SELECT kind FROM symbols WHERE path = ? AND line_start = ? AND name = ? LIMIT 1",
            (target["path"], target["line"], target["name"]),
        ).fetchone()
        return row["kind"] if row is not None else None

    def _resolve_source_node(
        self, source: dict[str, Any]
    ) -> tuple[tuple[str, int, str] | None, str | None]:
        """Resolves a confident edge's `source` endpoint (the call site's
        enclosing symbol) to the SAME kind of exact `(path, line_start,
        name)` identity `target` already carries. Shared by `god_nodes()`
        (out-degree) and `communities()` (undirected adjacency) so this
        lookup lives in exactly one place.

        `source["line"]` is the *call site's* line, not the enclosing
        symbol's own `line_start` — a plain `(path, name, kind)` grouping
        could conflate two identically-named-and-kinded definitions in the
        same file (rare, but real), so this re-derives the exact enclosing
        definition via name + kind + line-range containment against
        `symbols`, rather than approximating. Returns `(None, None)` when
        the reference has no enclosing symbol at all (e.g. a Ruby
        top-level call) — nothing to attribute source-side contribution to.
        """
        if source["name"] is None:
            return None, None
        row = self.db.execute(
            "SELECT path, line_start, name, kind FROM symbols WHERE path = ? "
            "AND name = ? AND kind = ? AND line_start <= ? AND line_end >= ? "
            "ORDER BY line_start LIMIT 1",
            (source["path"], source["name"], source["kind"], source["line"], source["line"]),
        ).fetchone()
        if row is None:
            return None, None
        return (row["path"], row["line_start"], row["name"]), row["kind"]

    def communities(self) -> dict[str, Any]:
        """A3 (D3/D4a): hand-rolled deterministic label propagation over
        the confident subgraph — zero new runtime dependency (AC-A3-3),
        single run, no seed, total-ordered visitation and tie-breaks
        (AC-A3-2/D6).

        Structural AC-NEG-7 compliance, same guarantee as `god_nodes()`:
        this method's entire analysis input is `self.confident_edges()`'s
        return value. AMBIGUOUS exclusion is additionally *algorithmically
        forced* here, not merely chosen: an AMBIGUOUS edge has no single
        target, so there is no single node to draw an undirected
        connection to in the first place — fan-out or fractional
        connectivity across its candidates was rejected by D4a as the same
        phantom-graph failure D6 rejected for merge drivers.

        Graph construction: an undirected, unweighted projection over the
        SAME node identity `god_nodes()` uses — `(path, line, name)`, a
        specific symbol definition. Multi-edges between the same pair
        collapse to one (D3a); self-loops (a symbol calling itself) are
        dropped — they carry no information about which OTHER community a
        node belongs with.

        Algorithm (deterministic Raghavan-style LPA, adapted): nodes are
        visited in a FIXED total order (sorted `(path, line, name)`) every
        pass, asynchronously (a node sees its neighbors' already-updated
        labels within the same pass) — labels start as each node's own
        index in that sorted order (already a deterministic assignment).
        Each pass, a node adopts the most frequent label among its
        neighbors; ties break toward the SMALLEST label value, a fixed
        rule, never "random" or insertion-order-dependent. Runs until a
        full pass makes no change, or `_LPA_MAX_ITERATIONS` is reached
        (LPA is not guaranteed to converge in general; this bounds runtime
        without affecting the pinned reference repos, which converge well
        under the cap in practice).

        Final community IDs are NOT the raw propagated label values (those
        are arbitrary node-index leftovers) — communities are grouped,
        then renumbered 0..N-1 in ascending order of their smallest member
        node (already a total order), so the ID assignment itself is
        reproducible, not just the grouping.
        """
        confident = self.confident_edges()

        adjacency: dict[tuple[str, int, str], set[tuple[str, int, str]]] = {}
        node_kind: dict[tuple[str, int, str], str | None] = {}

        def _touch(key: tuple[str, int, str], kind: str | None) -> None:
            adjacency.setdefault(key, set())
            node_kind.setdefault(key, kind)

        for edge in confident:
            target = edge["target"]
            if target is None:
                continue
            source_key, source_kind = self._resolve_source_node(edge["source"])
            if source_key is None:
                continue
            target_key = (target["path"], target["line"], target["name"])
            _touch(source_key, source_kind)
            _touch(target_key, self._target_kind(target))
            if source_key == target_key:
                continue  # self-loop: no cross-community information
            adjacency[source_key].add(target_key)
            adjacency[target_key].add(source_key)

        # The actual LPA algorithm is factored into label_propagation.py —
        # a dependency-free module scripts/verify-probe-verdict.py ALSO
        # imports (by file path, never through this package's own
        # __init__) to re-run the identical algorithm against a committed
        # graph bundle's edge list and assert the result matches what was
        # shipped, rather than trusting it as printed. This method's own
        # job is exactly the part that CANNOT be dependency-free: building
        # `adjacency` from the DB-backed confident subgraph.
        community_of = label_propagation(adjacency, _LPA_MAX_ITERATIONS)
        nodes_sorted = sorted(adjacency)
        community_count = len(set(community_of.values()))

        members = [
            {
                "path": node[0],
                "line": node[1],
                "name": node[2],
                "kind": node_kind.get(node),
                "community_id": community_of[node],
            }
            for node in nodes_sorted
        ]

        return {
            "communities": members,
            "community_count": community_count,
            "analysis_basis": "confident_edges",
            "ambiguous_edges_excluded": self._ambiguous_edge_count(),
            "resolved_edge_count": len(confident),
        }

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

        `source_col` is the final ORDER BY key (checker MINOR-4): since
        `callee_name` is invariant across every row this query returns (it
        is the WHERE filter), two references to the SAME callee on the SAME
        source line — `foo(foo())` — would otherwise tie on every column
        selected so far and fall back to SQLite's rowid/insertion order,
        same-machine deterministic but not a genuine total order. The
        reference site's own column can never tie for two distinct call
        sites, so this closes the gap the D6/A5 byte-determinism release
        gate would otherwise have found first.
        """
        placeholders = ",".join("?" for _ in relations)
        rows = self.db.execute(
            f"SELECT * FROM edges WHERE callee_name = ? AND relation IN ({placeholders}) "
            f"ORDER BY source_path, source_line, source_col, callee_name LIMIT ?",
            (callee_name, *relations, self.query_limit),
        ).fetchall()
        return [self._edge_row_to_dict(r) for r in rows]

    def unresolved_ref_count(self, callee_name: str, relations: tuple[str, ...]) -> int | None:
        """Count unresolved source occurrences, including mixed per-name bindings.

        Constructor relabeling must not count a successfully bound raw call as
        unresolved. Imported exports contain edges but no raw refs.
        """
        metadata = dict(
            self.db.execute(
                "SELECT key,value FROM manifest WHERE key IN "
                "('reference_coverage','generation_inputs')"
            )
        )
        if metadata.get("reference_coverage") == "unknown":
            return None
        if "generation_inputs" in metadata:
            inputs = json.loads(metadata["generation_inputs"])
            if inputs.get("format") == "atlas-generation.v1" and inputs.get("kind") == "import":
                return None
        placeholders = ",".join("?" for _ in relations)
        return self.db.execute(
            f"SELECT COUNT(*) FROM refs r WHERE callee_name = ? AND relation IN ({placeholders}) "
            "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_path=r.path "
            "AND e.source_line=r.line AND e.source_col=r.col AND e.lang=r.lang "
            "AND e.callee_name=r.callee_name AND (e.relation=r.relation "
            "OR (r.relation='call' AND e.relation='construct')))",
            (callee_name, *relations),
        ).fetchone()[0]

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

    def rationale(self) -> dict[str, Any]:
        """A4 (D5): every recognized rationale comment in the repo — a
        whole-graph verb like `god_nodes:`/`communities:`, not a single-
        symbol lookup. Independent of `confident_edges()`/the confidence
        enum entirely (AC-A4-6): `rationale_for` edges carry no confidence
        value and live in their own relation, so this method has nothing
        to do with the analysis-graph exclusions AC-NEG-7 governs — a
        rationale comment was never a call/inheritance edge candidate in
        the first place (AC-A4-4, structural via `PRODUCED_KINDS` never
        containing "rationale" — see `_match_rationale_comment`'s callers).

        `target` is the comment's enclosing scope (the `rationale_for`
        edge's destination), or `None` when the comment sits outside every
        known symbol's range (e.g. a module-level comment) — a real "no
        enclosing definition" fact, not an error, mirroring
        `_resolve_source_node`'s `(None, None)` convention for the same
        situation on the call/inheritance side.

        Total order: `(path, line, text)` — a fixed order for identical
        input, matching every other list this project returns (D6).
        """
        rows = self.db.execute(
            "SELECT path, line, text, label, target_path, target_line, target_name, lang "
            "FROM rationale ORDER BY path, line, text"
        ).fetchall()
        items = [
            {
                "path": r["path"],
                "line": r["line"],
                "text": r["text"],
                "label": r["label"],
                "target": (
                    {
                        "path": r["target_path"],
                        "line": r["target_line"],
                        "name": r["target_name"],
                    }
                    if r["target_path"] is not None
                    else None
                ),
                "lang": r["lang"],
            }
            for r in rows
        ]
        return {
            "rationale": items,
            "rationale_count": len(items),
            **self._historical_payload("rationale", lambda _row: True, "historical_rationale"),
        }

    # ---- A5 — Deterministic export / idempotent import (D6) ----
    #
    # The portable artifact is `symbols` + `edges` + `rationale` only —
    # never `refs`/`assignment_targets`/`files` (indexing-internal
    # bookkeeping: `refs` is the pre-resolution intermediate `edges` is
    # already derived from, `assignment_targets` is a resolution-time input,
    # `files` is per-file `--since` state). A subsequent `--since`
    # incremental `index` run against an imported DB has no bookkeeping to
    # compare against, so it treats every file as new and fully
    # re-extracts — converging back to source-of-truth rather than
    # corrupting anything. Documented here rather than left implicit: the
    # exported JSONL is a snapshot of derived, query-facing graph state, not
    # a substitute for re-indexing bookkeeping (a "documented terminus," not
    # an oversight).
    #
    # No merge/union driver ships (AC-A5-5, D6-Q2): on a conflicting
    # concurrent export, the workflow is to discard the conflicted artifact
    # and regenerate it (`atlas-aci index` + a fresh export) — never a
    # semantic graph/union merge (there is no `nx.compose`-style code
    # anywhere in this project, checked by AC-NEG-2 and
    # `test_export.py::test_no_merge_driver_shipped`). A trivial
    # regenerate-on-conflict git merge driver would be permitted but is not
    # shipped; nothing here registers a `git config merge.<x>.driver` entry.
    EXPORT_RECORD_TYPES = ("edge", "rationale", "symbol")

    @staticmethod
    def _canonical_json_line(record: dict[str, Any]) -> str:
        """One canonical JSON line (AC-A5-1): sorted keys, no insignificant
        whitespace (`separators=(",", ":")`), `ensure_ascii=True` (so the
        byte output never depends on a filesystem/terminal encoding or
        locale — `json.dumps` never consults `locale` at all, unlike e.g.
        `str.format` with thousands separators). "Fixed float formatting" is
        enforced as a mechanical negative-space guarantee, not an aspiration
        the schema happens to satisfy today: no column in `SCHEMA` is a
        float, so this asserts that fact on every call rather than silently
        emitting a platform-dependent `repr(float)` if one is ever added
        without updating this function first.
        """
        for key, value in record.items():
            if isinstance(value, float):
                raise TypeError(
                    f"export record field {key!r} is a float ({value!r}) -- no column in "
                    "SCHEMA is a float today; a canonical, explicitly-tested float format "
                    "must be added to _canonical_json_line before one ships (AC-A5-1)."
                )
        return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def _epoch6_projection(item: dict[str, Any]):
        table = {"symbol": "symbols", "edge": "edges", "rationale": "rationale"}[item["type"]]
        row = {key: value for key, value in item.items() if key != "type"}
        if table == "symbols":
            row["binding"] = "{}"
        if table == "edges" and row["candidates"] is not None:
            row["candidates"] = ev.canonical(row["candidates"])
        return table, row

    def _insert_epoch6_artifact(self, conn: sqlite3.Connection, records) -> None:
        for item in records:
            table, row = self._epoch6_projection(item)
            columns = list(row)
            conn.execute(
                "INSERT INTO "
                + table
                + "("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                tuple(row.values()),
            )

    def _legacy_canonicalize(
        self, conn: sqlite3.Connection, source_hashes, original_producer
    ) -> None:
        """Attach only stored semantic fields; never read source or invent spans."""
        for table in ev.TABLES.values():
            conn.execute(f"DELETE FROM {table}")
        producer = ev.producer(original_producer, legacy=True)
        ev.put(conn, producer)
        source_hashes = source_hashes or {}
        file_entities = {}
        symbols = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM symbols ORDER BY path,lang,kind,name,line_start,line_end,binding"
            )
        ]
        by_location: dict[tuple[Any, ...], list[str]] = {}
        by_name: dict[tuple[Any, ...], list[str]] = {}
        ordinals: dict[tuple[Any, ...], int] = {}

        def file(path, lang):
            key = (path, lang)
            if key not in file_entities:
                entity = ev.file_entity(path, lang, legacy=True)
                ev.put(conn, entity, path)
                file_entities[key] = entity["id"]
            return file_entities[key]

        def observe(path, lang, entity, role, line=None, column=None, end_line=None):
            occurrence = ev.record(
                "occurrence",
                entity_id=entity,
                kind="legacy",
                path=path,
                language=lang,
                source_sha256=source_hashes.get(path),
                source_size=None,
                start_byte=None,
                end_byte=None,
                line=line,
                column=column,
                end_line=end_line,
                end_column=None,
                role=role,
                provenance_state="legacy_unavailable",
            )
            proof = ev.provenance(occurrence, producer["id"])
            ev.put(conn, occurrence, path)
            ev.put(conn, proof, path)
            return occurrence["id"], proof["id"]

        for row in conn.execute("SELECT path,lang FROM files ORDER BY path"):
            file(row["path"], row["lang"])
        for row in symbols:
            key: tuple[Any, ...] = (row["path"], row["lang"], row["kind"], row["name"])
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            entity = ev.record(
                "entity",
                path=row["path"],
                language=row["lang"],
                kind=row["kind"],
                name=row["name"],
                owners=[],
                ordinal=ordinal,
                identity_basis="legacy_projection",
            )
            ev.put(conn, entity, row["path"])
            file(row["path"], row["lang"])
            occurrence, _ = observe(
                row["path"],
                row["lang"],
                entity["id"],
                "legacy_definition",
                row["line_start"],
                None,
                row["line_end"],
            )
            conn.execute(
                "UPDATE symbols SET entity_id=?,occurrence_id=? WHERE id=?",
                (entity["id"], occurrence, row["id"]),
            )
            by_location.setdefault(
                (row["path"], row["line_start"], row["name"], row["lang"]), []
            ).append(entity["id"])
            by_name.setdefault((row["path"], row["name"], row["kind"], row["lang"]), []).append(
                entity["id"]
            )

        def owner(path, lang, name, kind):
            known = by_name.get((path, name, kind, lang), [])
            return known[0] if len(known) == 1 else file(path, lang)

        def targets(path, line, name, lang):
            return by_location.get((path, line, name, lang), [])

        reference_by_site: dict[tuple[Any, ...], list[tuple[Any, ...]]] = {}
        references = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM refs ORDER BY path,line,col,callee_name,relation"
            )
        ]
        for row in references:
            context = json.loads(row["binding"])
            source = owner(
                row["path"], row["lang"], context.get("source_name"), context.get("source_kind")
            )
            occurrence, proof = observe(
                row["path"],
                row["lang"],
                source,
                "legacy_reference:" + row["relation"],
                row["line"],
                row["col"],
            )
            conn.execute(
                "UPDATE refs SET source_entity_id=?,occurrence_id=?,evidence_id=? WHERE id=?",
                (source, occurrence, proof, row["id"]),
            )
            key = (row["path"], row["line"], row["col"], row["callee_name"], row["lang"])
            reference_by_site.setdefault(key, []).append((row, source, occurrence, proof))
        represented = set()
        for row in conn.execute(
            "SELECT * FROM edges ORDER BY source_path,source_line,source_col,callee_name,relation"
        ):
            path, lang = row["source_path"], row["lang"]
            key = (path, row["source_line"], row["source_col"], row["callee_name"], lang)
            candidates = reference_by_site.get(key, [])
            if len(candidates) == 1:
                ref, source, occurrence, proof = candidates[0]
                represented.add(ref["id"])
            else:
                source = owner(path, lang, row["source_name"], row["source_kind"])
                occurrence, proof = observe(
                    path,
                    lang,
                    source,
                    "legacy_edge:" + row["relation"],
                    row["source_line"],
                    row["source_col"],
                )
            candidate_ids = []
            target = None
            resolution = row["confidence"]
            reason = None
            if resolution == "AMBIGUOUS":
                for candidate in json.loads(row["candidates"]):
                    found = targets(candidate["path"], candidate["line"], candidate["name"], lang)
                    ev.require(bool(found), "legacy candidate absent from definitions")
                    candidate_ids.extend(found)
                reason = "ambiguous_target"
            else:
                found = targets(row["target_path"], row["target_line"], row["target_name"], lang)
                ev.require(bool(found), "legacy target absent from definitions")
                if len(found) == 1:
                    target = found[0]
                else:
                    resolution = "LEGACY_UNAVAILABLE"
                    reason = "legacy_unavailable"
            assertion = ev.assertion(
                source,
                target,
                row["relation"],
                proof,
                resolution,
                row["callee_name"],
                candidate_ids,
                reason,
            )
            ev.put(conn, assertion, path)
            conn.execute("UPDATE edges SET assertion_id=? WHERE id=?", (assertion["id"], row["id"]))
        for group in reference_by_site.values():
            for row, source, _occurrence, proof in group:
                if row["id"] in represented:
                    continue
                assertion = ev.assertion(
                    source,
                    None,
                    row["relation"],
                    proof,
                    "UNRESOLVED",
                    row["callee_name"],
                    reason=row["unresolved_reason"] or "legacy_unavailable",
                )
                ev.put(conn, assertion, row["path"])
                if row["unresolved_reason"] is None:
                    conn.execute(
                        "UPDATE refs SET unresolved_reason='legacy_unavailable' WHERE id=?",
                        (row["id"],),
                    )
        for row in conn.execute(
            "SELECT rowid AS internal_id,* FROM assignment_targets "
            "ORDER BY path,scope,name,byte_start"
        ):
            lang = next((language for path, language in file_entities if path == row["path"]), None)
            ev.require(lang is not None, "legacy binding without file language")
            entity = file(row["path"], lang)
            occurrence, proof = observe(
                row["path"],
                lang,
                entity,
                "legacy_binding:" + row["binding_kind"] + ":" + row["name"],
            )
            conn.execute(
                "UPDATE assignment_targets SET entity_id=?,occurrence_id=?,evidence_id=? "
                "WHERE rowid=?",
                (entity, occurrence, proof, row["internal_id"]),
            )
        for row in conn.execute("SELECT * FROM rationale ORDER BY path,line,text"):
            path, lang = row["path"], row["lang"]
            source = file(path, lang)
            occurrence, proof = observe(path, lang, source, "legacy_rationale", row["line"])
            known = (
                targets(row["target_path"], row["target_line"], row["target_name"], lang)
                if row["target_path"]
                else []
            )
            target = known[0] if len(known) == 1 else None
            resolution = "INFERRED" if target else "LEGACY_UNAVAILABLE" if known else "UNRESOLVED"
            assertion = ev.assertion(
                source,
                target,
                "rationale_for",
                proof,
                resolution,
                row["text"],
                reason=None if target else "legacy_unavailable",
            )
            ev.put(conn, assertion, path)
            conn.execute(
                "UPDATE rationale SET occurrence_id=?,evidence_id=?,"
                "assertion_id=?,target_entity_id=? "
                "WHERE id=?",
                (occurrence, proof, assertion["id"], target, row["id"]),
            )
        for (path, lang), entity in file_entities.items():
            observe(path, lang, entity, "legacy_file")
        self._validate_evidence_projections(conn)

    def _store_legacy_generation(self, conn, kind, identity, source_hashes):
        unavailable = ["exact_spans", "structural_owners", "source_completeness"]
        if kind == "jsonl" or source_hashes is None:
            unavailable.append("unresolved_references")
        return self._store_generation(
            conn,
            {
                "format": "atlas-generation.v2",
                "kind": "legacy_conversion",
                "repository": str(self.repo),
                "schema": {"epoch": SCHEMA_EPOCH, "ddl_hash": EXPECTED_DDL_HASH},
                "converter": self._producer_inputs(),
                "legacy": {
                    "kind": kind,
                    "identity": identity,
                    "source_hashes": source_hashes,
                    "unavailable": unavailable,
                },
            },
        )

    def _convert_legacy_into(self, conn: sqlite3.Connection, legacy: sqlite3.Connection) -> None:
        metadata = self._generation_data(legacy)
        ev.require(
            metadata is not None and metadata[0]["format"] == "atlas-generation.v1",
            "invalid legacy generation",
        )
        if metadata is None:
            raise ValueError("Invalid evidence: invalid legacy generation")
        inputs, identity = metadata
        for table, columns in ev.COLUMNS.items():
            if table == "import_bindings":
                continue
            for row in legacy.execute("SELECT " + ",".join(columns) + " FROM " + table):
                conn.execute(
                    "INSERT INTO "
                    + table
                    + "("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    tuple(row),
                )
        for row in legacy.execute("SELECT path,mtime_ns,size,lang,indexed_at FROM files"):
            conn.execute(
                "INSERT INTO files(path,mtime_ns,size,lang,indexed_at) VALUES (?,?,?,?,?)",
                tuple(row),
            )
        hashes = inputs.get("source_hashes")
        self._legacy_canonicalize(conn, hashes, inputs.get("producer"))
        self._store_legacy_generation(conn, "database", identity, hashes)

    def migrate_legacy(self) -> dict[str, Any]:
        """Writer-only source-free conversion; keep epoch6 and publish once."""
        with self._writer_lock():
            path = self.atlas_dir / "graph.6.db"
            if self.db_path.exists():
                # Validate the actual current path, independently of a cached
                # epoch6 handle that must remain pinned for existing readers.
                current = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
                try:
                    current.row_factory = sqlite3.Row
                    metadata = self._generation_data(current)
                    ev.require(
                        metadata is not None and metadata[0]["schema"]["epoch"] == SCHEMA_EPOCH,
                        "existing current generation unavailable",
                    )
                finally:
                    current.close()
                return {"schema_epoch": SCHEMA_EPOCH, "already_current": True}
            tmp = self._stage_path()
            conn = legacy = None
            try:
                legacy = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                legacy.row_factory = sqlite3.Row
                conn = sqlite3.connect(tmp)
                conn.row_factory = sqlite3.Row
                conn.executescript(SCHEMA)
                self._convert_legacy_into(conn, legacy)
                self._validate_evidence_projections(conn)
                self._new_publication_token(conn)
                conn.commit()
                ev.require(self._generation_data(conn) is not None, "converted generation metadata")
                conn.close()
                conn = None
                legacy.close()
                legacy = None
                self._close_db()
                os.replace(tmp, self.db_path)
                self._retire_previous_native()
                try:
                    snap.retain(self.atlas_dir, None)
                except (OSError, ValueError):
                    log.warning("source_snapshot_prune_failed")
                return {"schema_epoch": SCHEMA_EPOCH, "already_current": False}
            finally:
                if conn is not None:
                    conn.close()
                if legacy is not None:
                    legacy.close()
                tmp.unlink(missing_ok=True)
                Path(str(tmp) + "-journal").unlink(missing_ok=True)

    def _retained_closure(self, path: str, language: str, records):
        """Only failed-source facts plus their exact predecessor support closure."""
        by_id = {r["id"]: r for r in records}
        symbols = [r for r in records if r["type"] == "projection" and r["table"] == "symbols"]
        proofs = [r for r in records if r["type"] == "evidence"]
        selected = set()

        def include(identifier):
            if identifier in selected:
                return
            item = by_id[identifier]
            selected.add(identifier)

            def links(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key.endswith("_id") and child is not None:
                            include(child)
                        elif key == "candidate_ids":
                            for candidate in child:
                                include(candidate)
                        elif key != "id":
                            links(child)
                elif isinstance(value, list):
                    for child in value:
                        links(child)

            links(item)
            if item["type"] == "entity":
                for symbol in symbols:
                    if symbol["links"]["entity_id"] == identifier:
                        include(symbol["id"])
            if item["type"] == "occurrence":
                for proof in proofs:
                    if proof["occurrence_id"] == identifier:
                        include(proof["id"])
            if item["type"] == "evidence":
                for assertion in records:
                    if assertion["type"] == "assertion" and assertion["evidence_id"] == identifier:
                        include(assertion["id"])

        for item in records:
            if item.get("path") == path:
                include(item["id"])
            elif item["type"] == "projection":
                row = item["row"]
                if row.get("source_path" if item["table"] == "edges" else "path") == path:
                    include(item["id"])
        if not selected:
            return None
        return ev.record(
            "history",
            path=path,
            language=language,
            records=sorted((by_id[key] for key in selected), key=lambda r: (r["type"], r["id"])),
        )

    @staticmethod
    def _insert_evidence_records(conn, records):
        for item in records:
            if item["type"] == "projection":
                data = {**item["row"], **item["links"]}
                columns = list(data)
                conn.execute(
                    "INSERT INTO "
                    + item["table"]
                    + "("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    tuple(data.values()),
                )
            else:
                ev.put(conn, item, item.get("path"))

    def _observation_records(
        self, conn, validate=True, validate_active=True, require_current=False
    ):
        row = conn.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()
        records = json.loads(row[0]) if row else []
        if validate:
            ev.require(isinstance(records, list), "observation records")
            ev.require(
                records == sorted(records, key=lambda r: (r["type"], r["id"])),
                "observation record order",
            )
            histories = {r["id"]: r for r in records if r["type"] == "history"}
            observations = [r for r in records if r["type"] == "observation"]
            ev.require(
                len(observations) == len({r["path"] for r in observations}),
                "conflicting source observations",
            )
            used = set()
            for record in records:
                ev.validate_record(record)
            for item in observations:
                history = histories.get(item["historical_id"])
                ev.require(
                    item["historical_id"] is None or history is not None, "missing retained history"
                )
                if history:
                    used.add(history["id"])
                    ev.require(
                        (history["path"], history["language"]) == (item["path"], item["language"]),
                        "history observation location",
                    )
                expected = ev.observation(
                    item["path"],
                    item["language"],
                    item["observed_source_sha256"],
                    item["state"],
                    item["reason"],
                    item["location"],
                    history,
                )
                ev.require(expected == item, "contradictory retained provenance")
            ev.require(used == set(histories), "unassociated retained history")
            if validate_active and (records or require_current):
                # Historical bundles carry their own original hashes/locations.
                # Only active records may substantiate a current observation.
                by_path = {item["path"]: item for item in observations}
                indexed_paths = set()
                for occurrence_row in conn.execute("SELECT payload FROM occurrences ORDER BY id"):
                    occurrence = json.loads(occurrence_row[0])
                    observation = by_path.get(occurrence["path"])
                    if occurrence["provenance_state"] == "source":
                        ev.require(observation is not None, "missing active source observation")
                    if observation is None:
                        continue  # Explicitly unknown legacy support is not freshness.
                    ev.require(
                        observation["state"] == "indexed", "failed observation has active facts"
                    )
                    ev.require(
                        occurrence["provenance_state"] == "source"
                        and occurrence["language"] == observation["language"]
                        and occurrence["source_sha256"] == observation["observed_source_sha256"],
                        "active occurrence contradicts observed source",
                    )
                    if occurrence["role"] == "file":
                        indexed_paths.add(occurrence["path"])
                for proof_row in conn.execute("SELECT payload FROM evidence ORDER BY id"):
                    proof = json.loads(proof_row[0])
                    occurrence = ev.get(conn, "occurrence", proof["occurrence_id"])
                    observation = by_path.get(occurrence["path"])
                    if proof["provenance_state"] == "source":
                        ev.require(observation is not None, "missing active evidence observation")
                        ev.require(
                            observation["state"] == "indexed"
                            and proof["source_sha256"] == observation["observed_source_sha256"],
                            "active evidence contradicts observed source",
                        )
                ev.require(
                    indexed_paths
                    == {item["path"] for item in observations if item["state"] == "indexed"},
                    "indexed observation lacks active consumed-source evidence",
                )
        return records

    def _validate_history(self, records):
        for history in (r for r in records if r["type"] == "history"):
            # Disposable validation database; never publish or mutate the leased
            # source database, and never resolve a historical assertion again.
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            try:
                conn.executescript(SCHEMA)
                self._insert_evidence_records(conn, history["records"])
                self._validate_evidence_projections(conn)
            finally:
                conn.close()

    def observation_records(self):
        """Pinned recorded observations, including explicitly stale support."""
        with self.reader_lease() as reader:
            return reader._observation_records(reader.db)

    def _projection_records(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        result = {}
        for table, columns in ev.COLUMNS.items():
            links = ev.LINK_COLUMNS[table]
            for row in conn.execute("SELECT " + ",".join((*columns, *links)) + " FROM " + table):
                data = dict(row)
                item = ev.record(
                    "projection",
                    table=table,
                    row={key: data[key] for key in columns},
                    links={key: data[key] for key in links},
                )
                ev.validate_record(item)
                result[item["id"]] = item
        return [result[key] for key in sorted(result)]

    def _validate_evidence_projections(self, conn: sqlite3.Connection) -> None:
        ev.validate_database(conn)
        definitions = {row["entity_id"]: dict(row) for row in conn.execute("SELECT * FROM symbols")}
        by_evidence: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute("SELECT payload FROM assertions"):
            item = json.loads(row[0])
            by_evidence.setdefault(item["evidence_id"], []).append(item)

        def occurrence_links(item, proof=True):
            row, links = item["row"], item["links"]
            occurrence = ev.get(conn, "occurrence", links["occurrence_id"])
            ev.require(
                occurrence["path"] == row["path"] and occurrence["language"] == row["lang"],
                "projection occurrence location",
            )
            if proof:
                evidence = ev.get(conn, "evidence", links["evidence_id"])
                ev.require(
                    evidence["occurrence_id"] == occurrence["id"], "projection evidence link"
                )
            return occurrence

        def target_matches(target, row, prefix):
            definition = definitions.get(target)
            ev.require(definition is not None, "projected target definition")
            ev.require(
                (definition["path"], definition["line_start"], definition["name"])
                == (row[prefix + "path"], row[prefix + "line"], row[prefix + "name"]),
                "projection target contradicts assertion",
            )

        for item in self._projection_records(conn):
            row, links, table = item["row"], item["links"], item["table"]
            if table == "symbols":
                entity = ev.get(conn, "entity", links["entity_id"])
                occurrence = occurrence_links(item, False)
                ev.require(
                    (entity["path"], entity["language"], entity["kind"], entity["name"])
                    == (row["path"], row["lang"], row["kind"], row["name"]),
                    "definition entity contradiction",
                )
                ev.require(
                    occurrence["entity_id"] == entity["id"]
                    and occurrence["line"] == row["line_start"]
                    and occurrence["end_line"] == row["line_end"],
                    "definition occurrence contradiction",
                )
            elif table == "refs":
                occurrence = occurrence_links(item)
                ev.require(
                    occurrence["entity_id"] == links["source_entity_id"]
                    and occurrence["line"] == row["line"]
                    and occurrence["column"] == row["col"],
                    "reference occurrence contradiction",
                )
                assertions = [
                    assertion
                    for assertion in by_evidence.get(links["evidence_id"], [])
                    if assertion["relation"] == row["relation"]
                    or (row["relation"] == "call" and assertion["relation"] == "construct")
                ]
                ev.require(bool(assertions), "unrepresented raw reference")
                for assertion in assertions:
                    ev.require(
                        assertion["source_entity_id"] == links["source_entity_id"]
                        and assertion["raw_target"] == row["callee_name"],
                        "reference assertion contradiction",
                    )
                    ev.require(
                        assertion["relation"] == row["relation"]
                        or (row["relation"] == "call" and assertion["relation"] == "construct"),
                        "reference relation contradiction",
                    )
                    if assertion["resolution"] == "UNRESOLVED":
                        ev.require(
                            assertion["unresolved_reason"] == row["unresolved_reason"],
                            "unresolved reason contradiction",
                        )
            elif table == "edges":
                assertion = ev.get(conn, "assertion", links["assertion_id"])
                proof = ev.get(conn, "evidence", assertion["evidence_id"])
                occurrence = ev.get(conn, "occurrence", proof["occurrence_id"])
                source = ev.get(conn, "entity", assertion["source_entity_id"])
                ev.require(
                    (source["path"], source["language"]) == (row["source_path"], row["lang"]),
                    "edge source contradiction",
                )
                ev.require(
                    occurrence["line"] == row["source_line"]
                    and occurrence["column"] == row["source_col"],
                    "edge evidence location",
                )
                if source["kind"] != "file":
                    ev.require(
                        (source["name"], source["kind"])
                        == (row["source_name"], row["source_kind"]),
                        "edge owner contradiction",
                    )
                ev.require(
                    assertion["relation"] == row["relation"]
                    and assertion["raw_target"] == row["callee_name"],
                    "edge relation/spelling contradiction",
                )
                if assertion["resolution"] == "LEGACY_UNAVAILABLE":
                    known = [
                        definition
                        for definition in definitions.values()
                        if (
                            definition["path"],
                            definition["line_start"],
                            definition["name"],
                            definition["lang"],
                        )
                        == (row["target_path"], row["target_line"], row["target_name"], row["lang"])
                    ]
                    ev.require(
                        proof["provenance_state"] == "legacy_unavailable"
                        and occurrence["kind"] == "legacy"
                        and occurrence["role"].startswith(("legacy_reference:", "legacy_edge:"))
                        and source["identity_basis"] == "legacy_projection"
                        and len(known) > 1
                        and row["confidence"] in {"EXTRACTED", "INFERRED"},
                        "legacy projection discriminant",
                    )
                else:
                    ev.require(
                        assertion["resolution"] == row["confidence"],
                        "edge confidence contradiction",
                    )
                    if assertion["target_entity_id"] is not None:
                        target_matches(assertion["target_entity_id"], row, "target_")
                    else:
                        candidates = json.loads(row["candidates"])
                        actual = sorted(
                            (
                                definitions[key]["path"],
                                definitions[key]["line_start"],
                                definitions[key]["name"],
                            )
                            for key in assertion["candidate_ids"]
                        )
                        ev.require(
                            actual
                            == sorted(
                                (value["path"], value["line"], value["name"])
                                for value in candidates
                            ),
                            "ambiguous candidates contradiction",
                        )
            elif table == "rationale":
                occurrence = occurrence_links(item)
                assertion = ev.get(conn, "assertion", links["assertion_id"])
                proof = ev.get(conn, "evidence", links["evidence_id"])
                ev.require(
                    assertion["relation"] == "rationale_for"
                    and assertion["evidence_id"] == links["evidence_id"]
                    and assertion["raw_target"] == row["text"],
                    "rationale assertion contradiction",
                )
                ev.require(
                    assertion["source_entity_id"] == occurrence["entity_id"]
                    and assertion["target_entity_id"] == links["target_entity_id"],
                    "rationale endpoint contradiction",
                )
                if assertion["target_entity_id"]:
                    target_matches(assertion["target_entity_id"], row, "target_")
                else:
                    known = [
                        definition
                        for definition in definitions.values()
                        if (
                            definition["path"],
                            definition["line_start"],
                            definition["name"],
                            definition["lang"],
                        )
                        == (row["target_path"], row["target_line"], row["target_name"], row["lang"])
                    ]
                    ev.require(
                        all(
                            row[key] is None
                            for key in ("target_path", "target_line", "target_name")
                        )
                        or (
                            proof["provenance_state"] == "legacy_unavailable"
                            and occurrence["kind"] == "legacy"
                            and occurrence["role"] == "legacy_rationale"
                            and assertion["resolution"] == "LEGACY_UNAVAILABLE"
                            and len(known) > 1
                        ),
                        "unknown rationale target",
                    )
            elif table == "assignment_targets":
                entity = ev.get(conn, "entity", links["entity_id"])
                ev.require(
                    entity["kind"] == "file" and entity["path"] == row["path"],
                    "binding file entity contradiction",
                )
                occurrence = ev.get(conn, "occurrence", links["occurrence_id"])
                proof = ev.get(conn, "evidence", links["evidence_id"])
                ev.require(
                    occurrence["entity_id"] == entity["id"]
                    and proof["occurrence_id"] == occurrence["id"],
                    "binding evidence contradiction",
                )
                ev.require(
                    occurrence["role"]
                    in {
                        "binding:" + row["binding_kind"] + ":" + row["name"],
                        "legacy_binding:" + row["binding_kind"] + ":" + row["name"],
                    },
                    "binding role contradiction",
                )
                if occurrence["provenance_state"] == "source":
                    ev.require(
                        occurrence["start_byte"] == row["byte_start"],
                        "binding byte location contradiction",
                    )
            else:
                occurrence = occurrence_links(item)
                description = json.loads(row["description"])
                ev.require(
                    set(description) == {"level", "module", "name", "local_name", "reason"},
                    "import description fields",
                )
                ev.require(
                    ev.integer(description["level"])
                    and isinstance(description["module"], list)
                    and all(
                        isinstance(part, str) and part.isidentifier()
                        for part in description["module"]
                    ),
                    "import module description",
                )
                ev.require(
                    description["local_name"] == row["local_name"]
                    and isinstance(description["name"], str)
                    and bool(description["name"]),
                    "import binding name",
                )
                ev.require(
                    description["reason"] is None or isinstance(description["reason"], str),
                    "import reason",
                )
                if occurrence["provenance_state"] == "source":
                    ev.require(
                        row["byte_start"] <= occurrence["start_byte"]
                        and occurrence["role"] == "import:" + description["name"],
                        "import occurrence role/order",
                    )
                assertion = ev.get(conn, "assertion", links["assertion_id"])
                ev.require(
                    occurrence["entity_id"] == links["source_entity_id"]
                    and assertion["source_entity_id"] == links["source_entity_id"]
                    and assertion["evidence_id"] == links["evidence_id"]
                    and assertion["relation"] == "import"
                    and assertion["raw_target"] == description["name"],
                    "import projection contradiction",
                )

        projections = self._projection_records(conn)
        coverage: dict[str, set[str]] = {table: set() for table in ev.COLUMNS}
        asserted: dict[str, set[str]] = {
            table: set() for table in ("edges", "rationale", "import_bindings")
        }
        defined_entities = set()
        for projection in projections:
            table, links = projection["table"], projection["links"]
            if "occurrence_id" in links:
                coverage[table].add(links["occurrence_id"])
            if "assertion_id" in links:
                asserted[table].add(links["assertion_id"])
            if table == "symbols":
                defined_entities.add(links["entity_id"])
        for record in ev.records(conn):
            if record["type"] == "entity" and record["kind"] != "file":
                ev.require(record["id"] in defined_entities, "missing declaration projection")
            if record["type"] == "occurrence":
                role = record["role"]
                if role in {"file", "legacy_file"}:
                    continue
                if role.startswith(("definition:", "legacy_definition")):
                    table = "symbols"
                elif role.startswith(("binding:", "legacy_binding:")):
                    table = "assignment_targets"
                elif role.startswith(("import:", "legacy_import:")):
                    table = "import_bindings"
                elif role.startswith(("reference:", "legacy_reference:")):
                    table = "refs"
                elif role in {"rationale", "legacy_rationale"}:
                    table = "rationale"
                elif role.startswith("legacy_edge:"):
                    continue  # old JSONL did not carry raw reference projections
                else:
                    raise ValueError("Invalid evidence: unsupported occurrence role")
                ev.require(
                    record["id"] in coverage[table], "missing occurrence projection: " + table
                )
            if record["type"] == "assertion":
                if record["relation"] == "import":
                    table = "import_bindings"
                elif record["relation"] == "rationale_for":
                    table = "rationale"
                elif record["resolution"] != "UNRESOLVED":
                    table = "edges"
                else:
                    proof = ev.get(conn, "evidence", record["evidence_id"])
                    ev.require(
                        proof["occurrence_id"] in coverage["refs"],
                        "missing unresolved reference projection",
                    )
                    continue
                ev.require(
                    record["id"] in asserted[table], "missing assertion projection: " + table
                )

    def export_jsonl(self, out_path: Path) -> dict[str, Any]:
        """Export complete canonical evidence and coherent compatibility projections.

        On conflict or corruption, regenerate from source; no semantic merge or
        graph-union merge driver is registered or used.
        """
        with self.reader_lease() as reader:
            conn = reader.db
            if dict(conn.execute("SELECT key,value FROM manifest")).get("epoch") == "6":
                return reader._export_epoch6_jsonl(out_path)
            self._validate_evidence_projections(conn)
            projections = self._projection_records(conn)

            metadata = self._observation_records(conn)
            generation_format = json.loads(
                conn.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                    0
                ]
            )["format"]
            self._validate_history(metadata)

            def all_records():
                yield from sorted(
                    [*ev.records(conn), *projections, *metadata], key=lambda r: (r["type"], r["id"])
                )

            digest = hashlib.sha256()
            count = size = 0
            for item in all_records():
                data = (ev.canonical(item) + "\n").encode()
                digest.update(data)
                size += len(data)
                count += 1
            header = {
                "type": "header",
                "format": ev.OBSERVATION_FORMAT
                if generation_format in {"atlas-generation.v3", "atlas-generation.v4"}
                else ev.FORMAT,
                "schema_epoch": PORTABLE_SCHEMA_EPOCH,
                "content_hash": digest.hexdigest(),
                "record_count": count,
            }
            head = (ev.canonical(header) + "\n").encode()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("wb") as stream:
                stream.write(head)
                for item in all_records():
                    stream.write((ev.canonical(item) + "\n").encode())
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("symbols", "refs", "edges", "rationale")
            }
            return {
                "records_written": count,
                "content_hash": digest.hexdigest(),
                "schema_epoch": PORTABLE_SCHEMA_EPOCH,
                "bytes_written": size + len(head),
                **counts,
            }

    def _read_exchange(self, in_path: Path):
        artifact = in_path.read_bytes()
        try:
            lines = artifact.splitlines(keepends=True)
            ev.require(
                bool(lines) and all(line.endswith(b"\n") for line in lines), "truncated export"
            )
            try:
                header = json.loads(lines[0])
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError(
                    f"{in_path}: header record is not valid JSON; regenerate from source"
                ) from exc
            ev.require(
                isinstance(header, dict) and header.get("type") == "header",
                "first record is not a header",
            )
            legacy = header.get("schema_epoch") == 6 and "format" not in header
            expected = {"type", "schema_epoch", "content_hash", "record_count"} | (
                set() if legacy else {"format"}
            )
            ev.require(
                set(header) == expected
                and type(header["schema_epoch"]) is int
                and (
                    legacy
                    or (
                        header["schema_epoch"] == PORTABLE_SCHEMA_EPOCH
                        and header["format"] in {ev.FORMAT, ev.OBSERVATION_FORMAT}
                    )
                ),
                "unsupported export format/schema_epoch",
            )
            ev.require(
                ev.integer(header["record_count"]) and ev.hash_value(header["content_hash"]),
                "header values",
            )
            if not legacy:
                ev.require(
                    lines[0] == (ev.canonical(header) + "\n").encode(),
                    "noncanonical header encoding",
                )
            body = b"".join(lines[1:])
            ev.require(
                hashlib.sha256(body).hexdigest() == header["content_hash"], "content_hash mismatch"
            )
            try:
                records = [json.loads(line) for line in lines[1:]]
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError(
                    f"{in_path}: body record is not valid JSON; regenerate from source"
                ) from exc
            ev.require(len(records) == header["record_count"], "record_count mismatch")
            previous = None
            for item, line in zip(records, lines[1:], strict=True):
                ev.require(isinstance(item, dict), "record must be an object")
                if legacy:
                    self._validate_import_record(item, in_path)
                    table = {"symbol": "symbols", "edge": "edges", "rationale": "rationale"}.get(
                        item.get("type")
                    )
                    if table is None:
                        raise ValueError("Invalid evidence: legacy record type")
                    expected_fields = set(ev.COLUMNS[table]) - (
                        {"binding"} if table == "symbols" else set()
                    )
                    ev.require(set(item) == expected_fields | {"type"}, "legacy closed fields")
                    table, row = self._epoch6_projection(item)
                    links = {
                        key: None if key == "target_entity_id" else "0" * 64
                        for key in ev.LINK_COLUMNS[table]
                    }
                    ev.validate_record(ev.record("projection", table=table, row=row, links=links))
                else:
                    # Reuse the established path/required-field diagnostics on
                    # current projections before canonical-ID/link validation.
                    if item.get("type") in {"entity", "occurrence"}:
                        self._validate_relative_repo_path(
                            item.get("path"), in_path, item["type"] + ".path"
                        )
                    elif item.get("type") == "projection" and isinstance(item.get("row"), dict):
                        table = item.get("table")
                        legacy_type = (
                            {"symbols": "symbol", "edges": "edge", "rationale": "rationale"}.get(
                                table
                            )
                            if isinstance(table, str)
                            else None
                        )
                        if legacy_type:
                            view = {"type": legacy_type, **item["row"]}
                            if legacy_type == "edge" and isinstance(view.get("candidates"), str):
                                view["candidates"] = json.loads(view["candidates"])
                            self._validate_import_record(view, in_path)
                        else:
                            for key, value in item["row"].items():
                                if "path" in key and value is not None:
                                    self._validate_relative_repo_path(value, in_path, key)
                    ev.validate_record(item)
                    if item["type"] in {"history", "observation"}:
                        self._validate_relative_repo_path(
                            item["path"], in_path, item["type"] + ".path"
                        )
                    if item["type"] == "history":
                        for retained in item["records"]:
                            if retained["type"] in {"entity", "occurrence"}:
                                self._validate_relative_repo_path(
                                    retained["path"], in_path, "historical path"
                                )
                            elif retained["type"] == "projection":
                                for key, value in retained["row"].items():
                                    if "path" in key and value is not None:
                                        self._validate_relative_repo_path(
                                            value, in_path, "historical " + key
                                        )
                                if (
                                    retained["table"] == "edges"
                                    and retained["row"]["candidates"] is not None
                                ):
                                    for candidate in json.loads(retained["row"]["candidates"]):
                                        self._validate_relative_repo_path(
                                            candidate["path"], in_path, "historical candidate.path"
                                        )
                    ev.require(
                        line == (ev.canonical(item) + "\n").encode(), "noncanonical record encoding"
                    )
                    key = (item["type"], item["id"])
                    ev.require(
                        previous is None or previous < key, "duplicate/noncanonical record order"
                    )
                    previous = key
                # Filesystem containment supplements portable path validation.
                if not legacy and item["type"] in {"entity", "occurrence"}:
                    ev.require(path_is_within(self.repo, Path(item["path"])), "path containment")
                if not legacy and item["type"] == "projection":
                    for key, value in item["row"].items():
                        if "path" in key and value is not None:
                            ev.require(
                                path_is_within(self.repo, Path(value)),
                                "projection path containment",
                            )
            return artifact, header, records, legacy
        except (KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError(f"{in_path}: malformed evidence export") from exc

    def import_jsonl(self, in_path: Path) -> dict[str, Any]:
        """Validate the full exchange and atomically publish original provenance.

        Reject corrupted/contradictory artifacts; regenerate them from source.
        This command never performs a semantic graph merge.
        """
        if self.read_only:
            raise RuntimeError("Import requires a writable graph")
        artifact, header, records, legacy = self._read_exchange(in_path)
        with self._writer_lock():
            tmp = self._stage_path()
            conn = None
            try:
                conn = sqlite3.connect(tmp)
                conn.row_factory = sqlite3.Row
                conn.executescript(SCHEMA)
                if legacy:
                    self._insert_epoch6_artifact(conn, records)
                    self._legacy_canonicalize(conn, None, None)
                    self._store_legacy_generation(
                        conn, "jsonl", hashlib.sha256(artifact).hexdigest(), None
                    )
                else:
                    metadata = [r for r in records if r["type"] in {"history", "observation"}]
                    ev.require(
                        not metadata or header["format"] == ev.OBSERVATION_FORMAT,
                        "observation exchange version",
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO manifest(key,value) "
                        "VALUES ('source_observations',?)",
                        (ev.canonical(metadata),),
                    )
                    self._observation_records(conn, validate_active=False)
                    self._validate_history(metadata)
                    for item in records:
                        if item["type"] in {"history", "observation"}:
                            continue
                        if item["type"] == "projection":
                            table = item["table"]
                            data = {**item["row"], **item["links"]}
                            columns = list(data)
                            conn.execute(
                                "INSERT INTO "
                                + table
                                + "("
                                + ",".join(columns)
                                + ") VALUES ("
                                + ",".join("?" for _ in columns)
                                + ")",
                                tuple(data.values()),
                            )
                        else:
                            path = item.get("path")
                            # Assertions sort before occurrences; their path index
                            # is assigned once links have all been validated.
                            ev.put(conn, item, path)
                    for kind in ("evidence", "assertion"):
                        for stored in conn.execute(f"SELECT id,payload FROM {ev.TABLES[kind]}"):
                            record = json.loads(stored["payload"])
                            proof = (
                                record
                                if kind == "evidence"
                                else ev.get(conn, "evidence", record["evidence_id"])
                            )
                            path = ev.get(conn, "occurrence", proof["occurrence_id"])["path"]
                            conn.execute(
                                f"UPDATE {ev.TABLES[kind]} SET path=? WHERE id=?",
                                (path, stored["id"]),
                            )
                    self._observation_records(
                        conn, require_current=header["format"] == ev.OBSERVATION_FORMAT
                    )
                    self._validate_evidence_projections(conn)
                    # Canonical metadata provenance is separate from imported evidence.
                    self._store_generation(
                        conn,
                        {
                            "format": "atlas-generation.v4"
                            if header["format"] == ev.OBSERVATION_FORMAT
                            else "atlas-generation.v2",
                            **(
                                {
                                    "observation_digest": self._status_hash(metadata),
                                    "snapshot": {"state": "unavailable", "reason": "import"},
                                }
                                if header["format"] == ev.OBSERVATION_FORMAT
                                else {}
                            ),
                            "kind": "import",
                            "repository": str(self.repo),
                            "schema": {"epoch": SCHEMA_EPOCH, "ddl_hash": EXPECTED_DDL_HASH},
                            "importer": self._producer_inputs(),
                            "artifact": {
                                "sha256": hashlib.sha256(artifact).hexdigest(),
                                "content_hash": header["content_hash"],
                                "record_count": len(records),
                            },
                            "source_provenance": "unavailable",
                        },
                    )
                self._validate_evidence_projections(conn)
                self._new_publication_token(conn)
                conn.commit()
                ev.require(self._generation_data(conn) is not None, "import generation metadata")
                counts = {
                    table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("symbols", "edges", "rationale")
                }
                conn.close()
                conn = None
                self._close_db()
                os.replace(tmp, self.db_path)
                self._retire_previous_native()
                try:
                    snap.retain(self.atlas_dir, None)
                except (OSError, ValueError):
                    log.warning("source_snapshot_prune_failed")
                return {
                    "records_imported": len(records),
                    "schema_epoch": SCHEMA_EPOCH,
                    "content_hash": header["content_hash"],
                    **counts,
                }
            finally:
                if conn is not None:
                    conn.close()
                tmp.unlink(missing_ok=True)
                Path(str(tmp) + "-journal").unlink(missing_ok=True)

    def _export_epoch6_jsonl(self, out_path: Path) -> dict[str, Any]:
        """Canonical, byte-deterministic JSONL export (A5/D6).

        One JSON object per line: a header record first
        (`{"type": "header", "schema_epoch", "content_hash", "record_count"}`
        — AC-A5-6), then every symbol/edge/rationale row, each carrying a
        `"type"` discriminator. `content_hash` is the sha256 of the BODY
        (every line after the header, exactly as written) — the importer
        recomputes it and refuses a truncated/corrupted file rather than
        silently importing a partial graph.

        Record-level canonical order (AC-A5-7, F8): records are grouped by
        `type` in a FIXED order (`EXPORT_RECORD_TYPES` — "edge" <
        "rationale" < "symbol", alphabetical), and *within* each type, by an
        explicit `ORDER BY` naming every column that could otherwise tie —
        never relying on SQLite's rowid/insertion order as an incidental
        tiebreak (the exact hazard AC-A1-8/D6 already named once for
        `confident_edges`/`callers_of`, generalized here to the full export).
        This is independent of insertion order BY CONSTRUCTION: the query
        never references `id`/rowid, so shuffling the order rows were
        `INSERT`-ed in cannot change the emitted order.

        Path keys are already repository-relative (AC-A5-2): `_run_build`
        stores `str(path.relative_to(self.repo))` for every path column, so
        no re-anchoring transform is needed on export; the artifact
        re-anchors purely by virtue of being imported against whatever
        `CodeGraph(repo=...)` instance calls `import_jsonl`.

        LF line endings (AC-A5-1): written with `newline="\\n"` explicitly,
        so Python's universal-newline translation on write (a no-op on
        POSIX, but NOT a no-op on Windows, where the default would emit
        `\\r\\n`) never fires — every line ends in a single `\\n` regardless
        of platform. Cross-platform byte-determinism (AC-REL-1) is scoped by
        its own VERIFY text to the macOS/Linux CI OS matrix — both POSIX,
        both already forward-slash path separators — so a Windows
        backslash-separated relative path (`Path.relative_to` on Windows
        would emit `app\\foo.rb`) is a real, separate hazard this export
        does NOT close; naming it here rather than leaving it implicit.

        Two more cross-OS hazards named for the same reason (checker
        MINOR-4 [SUSPECT] — undisclosed, not "closed by coincidence"):
        neither has been observed to fail AC-REL-1's actual macOS/Linux CI
        diff on this project's own source (or the two pinned reference
        repos), but neither is mechanically prevented, so both are named
        rather than left implicit.

        1. **Unicode filename normalization.** macOS's filesystem
           traditionally presents decomposed (NFD) unicode filenames;
           Linux presents whatever bytes were written, typically composed
           (NFC). Two checkouts of the identical commit could theoretically
           report a different byte sequence for the SAME logical filename
           if it contains non-ASCII characters, changing the exported path
           string (and therefore the export's bytes) across OSes. Not
           fixed here: normalizing at export time alone (without also
           normalizing at extraction time, where `_run_build` first stores
           `path.relative_to(self.repo)`) would make an export/re-import
           round-trip silently change a symbol's recorded path relative to
           what a same-machine query would use to look it up — trading one
           hazard for a subtler one under time pressure, so left open and
           named rather than half-fixed. `git`'s `core.precomposeUnicode`
           (default-on for macOS checkouts) avoids this in the common case;
           neither pinned reference repo (solidus, spree) has non-ASCII
           filenames, so it does not currently bite AC-REL-1's CI diff.
        2. **Case-only-colliding filenames.** `app/Foo.rb` and
           `app/foo.rb` are two distinct files on Linux's (typically)
           case-sensitive filesystem but alias to ONE file on a
           case-insensitive macOS volume (the default HFS+/APFS
           configuration). This is not a hazard the exporter can close at
           all — the underlying DATA differs (two files indexed vs. one)
           before export ever runs, a real filesystem-level ambiguity, not
           an export-format bug. Worth naming as a known limitation of
           depending on a real checkout's filesystem for this guarantee,
           not something either `atlas-aci index` or `export_jsonl` can
           detect or correct.
        """
        edge_rows = self.db.execute(
            "SELECT relation, source_path, source_line, source_col, source_name, "
            "source_kind, callee_name, confidence, target_path, target_line, "
            "target_name, candidates, lang FROM edges "
            "ORDER BY source_path, source_line, source_col, callee_name, relation, "
            "confidence, target_path, target_line, target_name, lang"
        ).fetchall()
        rationale_rows = self.db.execute(
            "SELECT path, line, text, label, target_path, target_line, target_name, lang "
            "FROM rationale ORDER BY path, line, text, label, target_path, target_line, "
            "target_name, lang"
        ).fetchall()
        symbol_rows = self.db.execute(
            "SELECT name, kind, path, line_start, line_end, lang FROM symbols "
            "ORDER BY path, line_start, name, kind, line_end, lang"
        ).fetchall()

        records: list[dict[str, Any]] = []
        for r in edge_rows:
            records.append(
                {
                    "type": "edge",
                    "relation": r["relation"],
                    "source_path": r["source_path"],
                    "source_line": r["source_line"],
                    "source_col": r["source_col"],
                    "source_name": r["source_name"],
                    "source_kind": r["source_kind"],
                    "callee_name": r["callee_name"],
                    "confidence": r["confidence"],
                    "target_path": r["target_path"],
                    "target_line": r["target_line"],
                    "target_name": r["target_name"],
                    "candidates": json.loads(r["candidates"]) if r["candidates"] else None,
                    "lang": r["lang"],
                }
            )
        for r in rationale_rows:
            records.append(
                {
                    "type": "rationale",
                    "path": r["path"],
                    "line": r["line"],
                    "text": r["text"],
                    "label": r["label"],
                    "target_path": r["target_path"],
                    "target_line": r["target_line"],
                    "target_name": r["target_name"],
                    "lang": r["lang"],
                }
            )
        for r in symbol_rows:
            records.append(
                {
                    "type": "symbol",
                    "name": r["name"],
                    "kind": r["kind"],
                    "path": r["path"],
                    "line_start": r["line_start"],
                    "line_end": r["line_end"],
                    "lang": r["lang"],
                }
            )

        body_lines = [self._canonical_json_line(rec) for rec in records]
        body_text = "".join(line + "\n" for line in body_lines)
        content_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        header = {
            "type": "header",
            "schema_epoch": 6,
            "content_hash": content_hash,
            "record_count": len(records),
        }
        full_text = self._canonical_json_line(header) + "\n" + body_text

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(full_text)

        return {
            "records_written": len(records),
            "edges": len(edge_rows),
            "rationale": len(rationale_rows),
            "symbols": len(symbol_rows),
            "content_hash": content_hash,
            "schema_epoch": 6,
            "bytes_written": len(full_text.encode("utf-8")),
        }

    _SYMBOL_REQUIRED_FIELDS = ("name", "kind", "path", "line_start", "line_end", "lang")
    _EDGE_REQUIRED_FIELDS = (
        "relation",
        "source_path",
        "source_line",
        "source_col",
        "source_name",
        "source_kind",
        "callee_name",
        "confidence",
        "target_path",
        "target_line",
        "target_name",
        "candidates",
        "lang",
    )
    _RATIONALE_REQUIRED_FIELDS = (
        "path",
        "line",
        "text",
        "label",
        "target_path",
        "target_line",
        "target_name",
        "lang",
    )
    _DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]")

    def _validate_relative_repo_path(self, value: Any, in_path: Path, field_name: str) -> None:
        """Checker MAJOR-1 (the nineteenth defect): `content_hash` proves
        the bytes are what the header claims, and says NOTHING about
        whether those bytes are repository-relative, repository-contained
        path strings. Without this, an imported symbol/edge/rationale
        `path` (or an edge's `target_path`/an AMBIGUOUS candidate's own
        `path`) was inserted VERBATIM — a hand-crafted export with
        `path: "../../etc/passwd"` or an absolute path imported cleanly,
        silently breaking AC-A5-2's "re-anchors on load" guarantee for
        anything but a well-behaved relative path. Rejects: empty/non-
        string values, POSIX-absolute paths (leading `/`), Windows-style
        absolute paths (a leading `\\` or a drive letter, `C:\\...`) even
        though this project runs on POSIX (cheap to close, no reason to
        leave it open), and any `..` traversal component. The final check
        delegates to `config.path_is_within` — the SAME function
        `Config.is_in_repo`/`enforcement.assert_path_in_repo` already use
        to guarantee "paths resolving outside the repo are rejected" for
        every MCP tool call (checker instruction: reuse the invariant
        `README.md` already promises, never reimplement it a second time
        to silently drift from it).
        """
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{in_path}: {field_name} {value!r} is not a non-empty string -- imported "
                "paths must be repository-relative (AC-A5-2)."
            )
        if value.startswith("/") or value.startswith("\\") or self._DRIVE_LETTER_RE.match(value):
            raise ValueError(
                f"{in_path}: {field_name} {value!r} is an absolute path -- imported paths must "
                "be repository-relative (AC-A5-2). Regenerate from source; there is no "
                "semantic merge for this format (AC-A5-5)."
            )
        normalized_parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
        if ".." in normalized_parts:
            raise ValueError(
                f"{in_path}: {field_name} {value!r} contains a '..' traversal component -- "
                "imported paths must stay within the repository (AC-A5-2)."
            )
        if not path_is_within(self.repo, Path(value)):
            raise ValueError(
                f"{in_path}: {field_name} {value!r} resolves outside the repository root -- "
                "rejected (AC-A5-2)."
            )

    def _validate_import_record(self, rec: dict[str, Any], in_path: Path) -> None:
        """Checker MINOR-2 + MAJOR-1, closed together: every required field
        must be PRESENT (a missing key used to raise a raw `KeyError` deep
        inside the insert loop, the same defect class as the truncated-line
        self-catch, one field-access layer deeper -- fixed by validating
        record shape BEFORE any row is inserted, not by wrapping each
        access), and every path-shaped field (including an AMBIGUOUS edge's
        own `candidates[].path`, easy to miss) must pass
        `_validate_relative_repo_path`.
        """
        rtype = rec.get("type")
        required: tuple[str, ...]
        if rtype == "symbol":
            required = self._SYMBOL_REQUIRED_FIELDS
            path_fields = [("path", rec.get("path"))]
        elif rtype == "edge":
            required = self._EDGE_REQUIRED_FIELDS
            path_fields = [
                ("source_path", rec.get("source_path")),
                ("target_path", rec.get("target_path")),
            ]
        elif rtype == "rationale":
            required = self._RATIONALE_REQUIRED_FIELDS
            path_fields = [("path", rec.get("path")), ("target_path", rec.get("target_path"))]
        else:
            raise ValueError(f"{in_path}: unknown record type {rtype!r}")

        missing = [f for f in required if f not in rec]
        if missing:
            raise ValueError(
                f"{in_path}: a {rtype!r} record is missing required field(s) {missing} -- the "
                "export is truncated or corrupted (or was hand-edited). Regenerate it from "
                "source (`atlas-aci index` + a fresh export) -- there is no semantic merge for "
                "this format (AC-A5-5)."
            )

        for field_name, value in path_fields:
            if value is not None:
                self._validate_relative_repo_path(value, in_path, field_name)

        if rtype == "edge" and rec.get("candidates") is not None:
            for i, candidate in enumerate(rec["candidates"]):
                if "path" not in candidate:
                    raise ValueError(
                        f"{in_path}: edge candidates[{i}] is missing required field 'path'"
                    )
                self._validate_relative_repo_path(
                    candidate["path"], in_path, f"candidates[{i}].path"
                )

    def query(self, dsl: str) -> dict[str, Any]:
        """Tiny DSL.

        Forms: 'callers_of:RecordVote#call',
        'subclasses_of:ApplicationRepository',
        'definitions_of:Tallier',
        'god_nodes:' (A2), 'communities:' (A3 — mechanically gated, see
        `communities()`), 'rationale:' (A4) — all three take the same
        trailing-colon-required, argument-ignored form: the analysis spans
        the whole graph, not a single named symbol.
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
                **self._historical_payload(
                    "edges",
                    lambda row: row["callee_name"] == method and row["relation"] in _CALL_RELATIONS,
                    "historical_edges",
                ),
                "unresolved_refs": self.unresolved_ref_count(method, _CALL_RELATIONS),
            }

        if verb == "definitions_of":
            return self.search_symbol(arg)

        if verb == "subclasses_of":
            # A1 (AC-A1-7): real inheritance/mixin edges, replacing the MVP
            # empty-stub-with-warning.
            return {
                "edges": self.subclasses_of(arg),
                **self._historical_payload(
                    "edges",
                    lambda row: (
                        row["callee_name"] == arg and row["relation"] in _HERITAGE_RELATIONS
                    ),
                    "historical_edges",
                ),
                "unresolved_refs": self.unresolved_ref_count(arg, _HERITAGE_RELATIONS),
            }

        if verb == "god_nodes":
            # A2: degree-centrality ranking over the confident subgraph —
            # `arg` is ignored (see the docstring above).
            return self.god_nodes()

        if verb == "communities":
            # A3: deterministic label propagation over the confident
            # subgraph — `arg` is ignored (see the docstring above).
            return self.communities()

        if verb == "rationale":
            # A4: every recognized rationale comment — `arg` is ignored
            # (see the docstring above).
            return self.rationale()

        # Unreachable today (KNOWN_QUERY_VERBS has exactly the six
        # members dispatched above) — NOT dead-code hygiene, a guard
        # (NEW-3, checker second pass). A1/A2/A3/A4 are expected to add
        # verbs to KNOWN_QUERY_VERBS; a verb added there without a corresponding
        # dispatch branch above must fail loudly here, not silently fall
        # through and impersonate subclasses_of's empty-with-warning shape
        # (which is exactly what an unconditional final `return` here
        # would do).
        raise NotImplementedError(
            f"verb {verb!r} is declared in KNOWN_QUERY_VERBS but has no "
            f"dispatch branch in CodeGraph.query() — add one before shipping it."
        )
