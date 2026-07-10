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
from atlas_aci.label_propagation import label_propagation

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
        (call_expression function: (identifier) @callee) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (property_identifier) @callee)) @ref.call
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
        (call_expression function: (identifier) @callee) @ref.call
        (call_expression
            function: (member_expression
                object: (_) @receiver
                property: (property_identifier) @callee)) @ref.call
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
SCHEMA_EPOCH = 5

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
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignment_targets_path_name ON assignment_targets(path, name);
"""


def ddl_hash(ddl: str) -> str:
    """SHA-256 hex digest of a schema DDL string (AC-H-12)."""
    return hashlib.sha256(ddl.encode("utf-8")).hexdigest()


# Hand-maintained — see the SCHEMA_EPOCH docstring above. Do NOT replace this
# with `ddl_hash(SCHEMA)`; that would make the pairing test a tautology.
EXPECTED_DDL_HASH = "d08bb7e7d889ed81088b38043f4e883d20f4d4f8d5e1aa3c268698d08c349a50"


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
            conn.execute("DELETE FROM assignment_targets")
            conn.execute("DELETE FROM rationale")
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
        rationale_added = 0
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
                conn.execute("DELETE FROM assignment_targets WHERE path = ?", (rel,))
                conn.execute("DELETE FROM rationale WHERE path = ?", (rel,))

            symbols, refs, assignment_targets, rationale_comments = self._extract(
                tree, source, rel, lang
            )
            for target_name in assignment_targets:
                conn.execute(
                    "INSERT INTO assignment_targets(path, name) VALUES (?, ?)",
                    (rel, target_name),
                )
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
                    "path, line, col, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r.callee_name,
                        r.relation,
                        int(r.qualified),
                        r.qualifier_name,
                        r.path,
                        r.line,
                        r.col,
                        r.lang,
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
                target = self._resolve_rationale_target(rc, symbols)
                conn.execute(
                    "INSERT INTO rationale(path, line, text, label, target_path, "
                    "target_line, target_name, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rc.path,
                        rc.line,
                        rc.text,
                        rc.label,
                        target[0] if target else None,
                        target[1] if target else None,
                        target[2] if target else None,
                        rc.lang,
                    ),
                )
                rationale_added += 1

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
            "rationale": rationale_added,
            "files_skipped": files_skipped,
            "files_removed": files_removed,
            "unsupported_skipped": unsupported_skipped,
            "schema_epoch": SCHEMA_EPOCH,
        }

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
    ) -> tuple[list[Symbol], list[Reference], list[str], list[RationaleComment]]:
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

        symbols: list[Symbol] = []
        refs: list[Reference] = []
        assignment_targets: list[str] = []
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
                            col=node.start_point[1],
                            relation=relation,
                            qualified=True,
                        )
                    )
            elif assign_cap is not None:
                for node in caps[assign_cap]:
                    name = self._node_text(source, node).strip()
                    if name:
                        assignment_targets.append(name)
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
                    rationale_comments.append(
                        RationaleComment(
                            path=rel_path,
                            line=node.start_point[0] + line_offset + 1,
                            text=text,
                            label=label,
                            lang=lang,
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
                            col=node.start_point[1],
                            relation="call",
                            qualified=qualified,
                            qualifier_name=qualifier_name,
                        )
                    )

        return symbols, refs, assignment_targets, rationale_comments

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
            "SELECT callee_name, relation, qualified, qualifier_name, path, line, col, lang "
            "FROM refs ORDER BY path, line, col, callee_name"
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

            # checker finding (MAJOR-1, condition 5): a Python/JS/TS
            # qualifier_name that globally matches a class/module symbol
            # might ALSO be a local variable in THIS file shadowing that
            # class (`Config = load_config(); Config.reload()` — or the
            # construct-relation equivalent, a class name rebound to a
            # callable and invoked). The resolver has no scope analysis, so
            # it cannot rule out shadowing when the SAME name is ALSO an
            # assignment target in the same file — the cheap, deterministic,
            # zero-LLM signal is "don't claim certainty you don't have":
            # demote to unqualified (INFERRED) rather than assert EXTRACTED.
            # A false EXTRACTED is worse than an honest INFERRED; this is
            # local (per-file), erring toward under-claiming, never
            # over-claiming. Ruby is unaffected — `qualifier_name` is always
            # None there (its grammar makes a constant lexically
            # un-shadowable by a local variable in the first place).
            qualifier_shadowed = False
            if ref["qualifier_name"] is not None:
                shadow_hit = conn.execute(
                    "SELECT 1 FROM assignment_targets WHERE path = ? AND name = ? LIMIT 1",
                    (ref["path"], ref["qualifier_name"]),
                ).fetchone()
                qualifier_shadowed = shadow_hit is not None

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
                # at extraction, same reasoning applied at resolution time) —
                # UNLESS the shadowing guard above fired.
                qualified = not qualifier_shadowed
            elif ref["qualifier_name"] is not None:
                # Python/JS/TS ordinary method calls: resolve the fact,
                # don't guess it from case.
                if qualifier_shadowed:
                    qualified = False
                else:
                    qualifier_hit = conn.execute(
                        "SELECT 1 FROM symbols WHERE name = ? AND kind IN ('class', 'module') "
                        "LIMIT 1",
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
                "INSERT INTO edges(relation, source_path, source_line, source_col, "
                "source_name, source_kind, callee_name, confidence, target_path, "
                "target_line, target_name, candidates, lang) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        return {"rationale": items, "rationale_count": len(items)}

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

    def export_jsonl(self, out_path: Path) -> dict[str, Any]:
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
            "schema_epoch": SCHEMA_EPOCH,
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
            "schema_epoch": SCHEMA_EPOCH,
            "bytes_written": len(full_text.encode("utf-8")),
        }

    def import_jsonl(self, in_path: Path) -> dict[str, Any]:
        """Idempotent import of a canonical export (AC-A5-4): rebuilds
        `symbols`/`edges`/`rationale` from scratch into a FRESH temp DB file,
        then atomically replaces `self.db_path` (mirrors `_build_full_atomic`
        — F17's crash-safety property applies here too: a crash mid-import
        leaves any previous DB untouched). Always a full rebuild-from-import,
        never an incremental patch, so repeat imports of the same file are
        trivially idempotent by construction — there is no accumulated state
        for a second import to duplicate or diverge from.

        Integrity check (AC-A5-4/AC-A5-6): the header's `content_hash` is
        recomputed from the body actually read and compared before a single
        row is inserted; a mismatch raises loudly, naming "regenerate from
        source" as the recovery path (AC-A5-5) rather than attempting any
        kind of partial or best-effort import. `schema_epoch` must match
        this build's `SCHEMA_EPOCH` exactly — no cross-epoch migration is
        attempted (D1: a schema change is always a fresh epoch, never an
        in-place migration, and that applies to imported data too).
        """
        if self.read_only:
            raise RuntimeError(
                "CodeGraph.import_jsonl() is index-path only (DIR-2); a "
                "read_only CodeGraph (as `serve` constructs) never writes to .atlas."
            )

        text = in_path.read_text(encoding="utf-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            raise ValueError(f"{in_path}: empty export file, no header record")

        # Every `json.loads` below is wrapped: a truncated/corrupted line
        # (a crash mid-write, a partial `scp`/`git checkout`) is exactly the
        # scenario this method exists to reject loudly -- a raw
        # `json.JSONDecodeError` traceback is not a diagnosable, actionable
        # error message, and this is index-path-only, write-adjacent code
        # where "the file is bad" must read as clearly as every other
        # integrity failure this method already raises.
        try:
            header = json.loads(lines[0])
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{in_path}: header record is not valid JSON ({e}) -- the export is truncated "
                "or corrupted. Regenerate it from source (`atlas-aci index` + a fresh export) "
                "-- there is no semantic merge for this format (AC-A5-5)."
            ) from e
        if header.get("type") != "header":
            raise ValueError(
                f"{in_path}: first record is not a header (type={header.get('type')!r})"
            )

        body_text = "".join(line + "\n" for line in lines[1:])
        recomputed_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        if recomputed_hash != header.get("content_hash"):
            raise ValueError(
                f"{in_path}: content_hash mismatch -- recorded "
                f"{header.get('content_hash')!r}, recomputed {recomputed_hash!r}. The export "
                "is truncated or corrupted (or was hand-edited/merged). Regenerate it from "
                "source (`atlas-aci index` + a fresh export) -- there is no semantic merge "
                "for this format (AC-A5-5)."
            )
        if header.get("schema_epoch") != SCHEMA_EPOCH:
            raise ValueError(
                f"{in_path}: schema_epoch {header.get('schema_epoch')!r} does not match this "
                f"build's SCHEMA_EPOCH {SCHEMA_EPOCH} -- re-export from a matching atlas-aci "
                "version; cross-epoch import is never attempted (D1)."
            )

        # The content_hash check above already gates against a corrupted BODY
        # line in the overwhelmingly common case (any tampering changes the
        # recomputed hash) -- this second try/except exists for the
        # remaining, harder-to-reach edge: a body line whose corruption
        # happens to co-occur with a header whose OWN content_hash was
        # separately (mis)computed to still match. Same principle either
        # way: never let a raw JSONDecodeError traceback stand in for a
        # diagnosable message.
        try:
            records = [json.loads(line) for line in lines[1:]]
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{in_path}: a body record is not valid JSON ({e}) -- the export is truncated "
                "or corrupted. Regenerate it from source (`atlas-aci index` + a fresh export) "
                "-- there is no semantic merge for this format (AC-A5-5)."
            ) from e

        self.atlas_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.atlas_dir / f".graph.{SCHEMA_EPOCH}.db.import-tmp.{os.getpid()}"
        if tmp_path.exists():
            tmp_path.unlink()
        conn = sqlite3.connect(tmp_path)
        conn.executescript(SCHEMA)
        conn.row_factory = sqlite3.Row
        symbols_added = edges_added = rationale_added = 0
        try:
            for rec in records:
                rtype = rec.get("type")
                if rtype == "symbol":
                    conn.execute(
                        "INSERT INTO symbols(name, kind, path, line_start, line_end, lang) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            rec["name"],
                            rec["kind"],
                            rec["path"],
                            rec["line_start"],
                            rec["line_end"],
                            rec["lang"],
                        ),
                    )
                    symbols_added += 1
                elif rtype == "edge":
                    candidates_json = (
                        json.dumps(rec["candidates"]) if rec.get("candidates") is not None else None
                    )
                    conn.execute(
                        "INSERT INTO edges(relation, source_path, source_line, source_col, "
                        "source_name, source_kind, callee_name, confidence, target_path, "
                        "target_line, target_name, candidates, lang) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            rec["relation"],
                            rec["source_path"],
                            rec["source_line"],
                            rec["source_col"],
                            rec["source_name"],
                            rec["source_kind"],
                            rec["callee_name"],
                            rec["confidence"],
                            rec["target_path"],
                            rec["target_line"],
                            rec["target_name"],
                            candidates_json,
                            rec["lang"],
                        ),
                    )
                    edges_added += 1
                elif rtype == "rationale":
                    conn.execute(
                        "INSERT INTO rationale(path, line, text, label, target_path, "
                        "target_line, target_name, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            rec["path"],
                            rec["line"],
                            rec["text"],
                            rec["label"],
                            rec["target_path"],
                            rec["target_line"],
                            rec["target_name"],
                            rec["lang"],
                        ),
                    )
                    rationale_added += 1
                else:
                    raise ValueError(f"{in_path}: unknown record type {rtype!r}")

            conn.execute(
                "INSERT OR REPLACE INTO manifest(key, value) VALUES ('epoch', ?)",
                (str(SCHEMA_EPOCH),),
            )
            conn.commit()
        finally:
            conn.close()

        self._db = None  # drop any stale handle before the swap (mirrors _build_full_atomic)
        os.replace(tmp_path, self.db_path)

        if not self.epoch_ok():
            raise ValueError(
                f"{in_path}: import completed but the resulting DB failed the epoch "
                "integrity check -- this should be unreachable; treat it as a bug, not a "
                "data problem."
            )

        return {
            "symbols": symbols_added,
            "edges": edges_added,
            "rationale": rationale_added,
            "schema_epoch": SCHEMA_EPOCH,
        }

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
