"""Rails explicit models, tables and migrations (ACI-S25).

The adapter only reads the bytes the build already captured and the syntax trees tree-sitter
produced from them. It never runs Ruby, Rails, rake, bundler or any migration, never opens a
database, never inflects a name and never guesses: a declaration is either a supported literal
fact or a closed-vocabulary diagnostic.

Facts are shared-store records, one ``rails_model`` occurrence per declaration (the exact
statement span; the class span for operator-map facts), cited by one evidence record from a
``{"rails_models": ...}`` producer, and one assertion per declaration:

* ``association``: a literal ``belongs_to`` / ``has_one`` / ``has_many`` with a literal
  ``class_name`` linked to the model class entity it names. It is ``INFERRED`` for exactly one
  model, ``AMBIGUOUS`` (with candidate identities) when the name reaches several models, and
  ``UNRESOLVED`` with a closed reason otherwise. It is never ``EXTRACTED``.
* ``model_table``: a literal ``self.table_name`` (or one unambiguous operator-map entry) bound to
  its model. The table lives in the canonical ``raw_target``; tables are not graph entities, so
  the assertion has no target and ``EXTRACTED`` means "literal declaration extracted, no graph
  edge".
* ``migration_mentions``: a literal ``create_table`` / ``add_column`` / ``remove_column`` in a
  ``db/migrate/*.rb`` file, sourced at the migration file entity. It is a source statement, never
  proof that the migration ran and never the live schema.

The facts are a derived global function of (captured model and migration bytes, the Ruby
``symbols`` table, the operator-map bytes, this adapter). ``refresh`` rebuilds them from scratch
on every enabled build.

Portability: no recursion, no generic tree walk, explicit ASCII character classes and
``fullmatch``, sorted outputs. This module imports only the standard library and
``atlas_aci.storage.evidence``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas_aci.storage import evidence as ev

ADAPTER = ev.RAILS_MODELS_ADAPTER
MANIFEST_KEY = "rails_models"
MANIFEST_FORMAT = "atlas-rails-models.v1"
MAP_FORMAT = "atlas-rails-table-map.v1"
ROLE_PREFIX = ev.RAILS_MODEL_ROLE_PREFIX
ASSOCIATION_ROLE = ROLE_PREFIX + "association:"
TABLE_ROLE = ROLE_PREFIX + "table:"
MIGRATION_ROLE = ROLE_PREFIX + "migration:"
CLASS_NAME_LABEL = "rails_class_name.v1"
UNEXPANDED_LABEL = "unexpanded"
LABELS = frozenset({CLASS_NAME_LABEL, UNEXPANDED_LABEL})
TABLE_SOURCES = frozenset({"literal", "operator_map"})

ASSOCIATION_KINDS = frozenset({"belongs_to", "has_one", "has_many"})
ASSOCIATION_METHODS = ASSOCIATION_KINDS | {"has_and_belongs_to_many"}
ASSOCIATION_CONSTRUCTS = frozenset(
    {
        "has_and_belongs_to_many",
        "through",
        "source",
        "source_type",
        "polymorphic",
        "as",
        "block",
        "splat",
        "scope_argument",
        "extra_arguments",
        "non_literal_name",
        "unsupported_option",
        "implicit_class_name",
        "symbol_class_name",
        "computed_class_name",
        "malformed_class_name",
        "declaration_limit",
    }
)
# Checked in this order (D4.2 step 2); each one is also a construct code.
ASSOCIATION_OPTION_CONSTRUCTS = ("through", "source", "source_type", "polymorphic", "as")
# Accepted and not recorded; every other option key is `unsupported_option`.
ALLOWED_ASSOCIATION_OPTIONS = frozenset(
    {
        "class_name",
        "foreign_key",
        "primary_key",
        "inverse_of",
        "dependent",
        "optional",
        "required",
        "touch",
        "autosave",
        "validate",
        "counter_cache",
        "strict_loading",
    }
)
TABLE_CONSTRUCTS = frozenset(
    {
        "symbol_table_name",
        "computed_table_name",
        "malformed_table_name",
        "table_name_method",
        "declaration_limit",
    }
)
MIGRATION_OPS = frozenset({"create_table", "add_column", "remove_column"})
MIGRATION_STATEMENT_CONSTRUCTS = frozenset(
    {
        "change_table",
        "drop_table",
        "rename_table",
        "rename_column",
        "add_reference",
        "remove_reference",
        "add_belongs_to",
        "remove_belongs_to",
        "add_index",
        "remove_index",
        "add_timestamps",
        "remove_timestamps",
        "add_foreign_key",
        "remove_foreign_key",
        "change_column",
        "change_column_default",
        "change_column_null",
        "create_join_table",
        "drop_join_table",
        "execute",
        "reversible",
        "revert",
        "up_only",
        "remove_columns",
    }
)
MIGRATION_OP_CONSTRUCTS = frozenset(
    {
        "block",
        "splat",
        "missing_arguments",
        "extra_arguments",
        "non_literal_table",
        "non_literal_column",
        "interpolated_literal",
        "malformed_literal",
    }
)
MIGRATION_OTHER_CONSTRUCTS = frozenset({"conditional", "other_statement", "declaration_limit"})
MIGRATION_CONSTRUCTS = (
    MIGRATION_STATEMENT_CONSTRUCTS | MIGRATION_OP_CONSTRUCTS | MIGRATION_OTHER_CONSTRUCTS
)
MIGRATION_METHODS = frozenset({"change", "up", "down"})

AMBIGUOUS_CLASS_REASON = "ambiguous_class_name"
NOT_RECOGNIZED_REASON = "target_not_recognized_model"
CLASS_NOT_FOUND_REASON = "class_not_found"
UNSUPPORTED_ASSOCIATION_REASON = "unsupported_association"
CONFLICTING_TABLE_REASON = "conflicting_table_mapping"
AMBIGUOUS_TABLE_REASON = "ambiguous_table_name"
AMBIGUOUS_MODEL_REASON = "ambiguous_model"
UNSUPPORTED_TABLE_REASON = "unsupported_table_name"
UNSUPPORTED_MIGRATION_REASON = "unsupported_migration_op"
REASONS = frozenset(
    {
        AMBIGUOUS_CLASS_REASON,
        NOT_RECOGNIZED_REASON,
        CLASS_NOT_FOUND_REASON,
        UNSUPPORTED_ASSOCIATION_REASON,
        CONFLICTING_TABLE_REASON,
        AMBIGUOUS_TABLE_REASON,
        AMBIGUOUS_MODEL_REASON,
        UNSUPPORTED_TABLE_REASON,
        UNSUPPORTED_MIGRATION_REASON,
    }
)
MAP_REASONS = frozenset(
    {
        "too_large",
        "invalid_json",
        "unsupported_format",
        "schema",
        "too_many_entries",
        "malformed_model",
        "malformed_table",
    }
)

MAX_FILES = 10000
MAX_FILE_BYTES = 1048576
MAX_CAPTURE_BYTES = 67108864
MAX_DECLARATIONS = 20000
MAX_MAP_BYTES = 1048576
MAX_MAP_ENTRIES = 10000
MAX_MAP_NESTING = 4
MAX_NAME_BYTES = 128
MAX_CLASS_NAME_BYTES = 256
MAX_TABLE_BYTES = 64

CLASS_NAME_RE = re.compile(r"(::)?[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*")
MODEL_KEY_RE = re.compile(r"[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*")
NAME_RE = re.compile(r"[a-z_][a-z0-9_]*")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIGRATION_BASE_RE = re.compile(rb"ActiveRecord::Migration(\[[0-9]+\.[0-9]+\])?")
MODEL_BASES = frozenset({b"ApplicationRecord", b"ActiveRecord::Base", b"::ActiveRecord::Base"})
MODELS_PREFIX = "app/models/"
CONCERNS_PREFIX = "app/models/concerns/"
MIGRATE_PREFIX = "db/migrate/"

CONDITIONAL_TYPES = frozenset(
    {"if", "unless", "if_modifier", "unless_modifier", "conditional", "case"}
)
# Not statements: comments, and the heredoc body that follows the statement owning its marker.
SKIPPED_STATEMENTS = frozenset({"comment", "heredoc_body"})
SPLAT_TYPES = frozenset({"splat_argument", "hash_splat_argument", "block_argument"})
SYMBOL_TYPES = frozenset({"simple_symbol", "delimited_symbol"})
MODEL_RELATIONS = ev.RAILS_MODEL_RELATIONS
RESOLUTIONS = {
    ev.RAILS_ASSOCIATION_RELATION: ("AMBIGUOUS", "INFERRED", "UNRESOLVED"),
    ev.RAILS_MIGRATION_RELATION: ("EXTRACTED", "UNRESOLVED"),
    ev.RAILS_TABLE_RELATION: ("EXTRACTED", "UNRESOLVED"),
}
ASSOCIATION_KEYS = frozenset({"class_name", "kind", "mapping", "name"})
ASSOCIATION_DIAGNOSTIC_KEYS = frozenset({"construct", "kind", "mapping"})
DIAGNOSTIC_KEYS = frozenset({"construct", "mapping"})
LITERAL_TABLE_KEYS = frozenset({"source", "table"})
MAPPED_TABLE_KEYS = frozenset({"map_sha256", "source", "table"})
CREATE_KEYS = frozenset({"op", "table"})
COLUMN_KEYS = frozenset({"column", "op", "table"})

_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")


def is_model_path(path: str) -> bool:
    return (
        path.startswith(MODELS_PREFIX)
        and path.endswith(".rb")
        and not path.startswith(CONCERNS_PREFIX)
    )


def is_migration_path(path: str) -> bool:
    return (
        path.startswith(MIGRATE_PREFIX)
        and path.endswith(".rb")
        and "/" not in path[len(MIGRATE_PREFIX) :]
    )


@dataclass(frozen=True)
class Span:
    """A plain source span (all `ev.occurrence` reads); no parse tree is retained."""

    start_byte: int
    end_byte: int
    start_point: tuple[int, int]
    end_point: tuple[int, int]


def _span(node: Any) -> Span:
    return Span(
        node.start_byte,
        node.end_byte,
        (node.start_point[0], node.start_point[1]),
        (node.end_point[0], node.end_point[1]),
    )


@dataclass(frozen=True)
class Declaration:
    """One supported fact or diagnostic. ``role`` and ``raw`` (canonical raw_target) are final."""

    area: str
    span: Span
    role: str
    raw: str
    construct: str | None = None
    class_name: str | None = None
    table: str | None = None

    @property
    def relation(self) -> str:
        if self.area == "association":
            return ev.RAILS_ASSOCIATION_RELATION
        if self.area == "table":
            return ev.RAILS_TABLE_RELATION
        return ev.RAILS_MIGRATION_RELATION


def _association(span: Span, kind: str, name: str, class_name: str) -> Declaration:
    raw = ev.canonical(
        {"class_name": class_name, "kind": kind, "mapping": CLASS_NAME_LABEL, "name": name}
    )
    return Declaration("association", span, ASSOCIATION_ROLE + kind, raw, None, class_name)


def _association_diagnostic(span: Span, method: str, construct: str) -> Declaration:
    raw = ev.canonical({"construct": construct, "kind": method, "mapping": UNEXPANDED_LABEL})
    return Declaration(
        "association", span, ASSOCIATION_ROLE + "unsupported:" + construct, raw, construct
    )


def _table_literal(span: Span, table: str) -> Declaration:
    raw = ev.canonical({"source": "literal", "table": table})
    return Declaration("table", span, TABLE_ROLE + "literal", raw, None, None, table)


def _table_diagnostic(span: Span, construct: str) -> Declaration:
    raw = ev.canonical({"construct": construct, "mapping": UNEXPANDED_LABEL})
    return Declaration("table", span, TABLE_ROLE + "unsupported:" + construct, raw, construct)


def _migration_mention(span: Span, op: str, table: str, column: str | None) -> Declaration:
    body: dict[str, Any] = {"op": op, "table": table}
    if column is not None:
        body["column"] = column
    return Declaration("migration", span, MIGRATION_ROLE + op, ev.canonical(body))


def _migration_diagnostic(span: Span, construct: str) -> Declaration:
    raw = ev.canonical({"construct": construct, "mapping": UNEXPANDED_LABEL})
    return Declaration(
        "migration", span, MIGRATION_ROLE + "unsupported:" + construct, raw, construct
    )


def _text(source: bytes, node: Any) -> bytes:
    return source[node.start_byte : node.end_byte]


def _name(source: bytes, node: Any) -> str:
    return _text(source, node).decode("utf-8", errors="replace")


def literal(source: bytes, node: Any, limit: int) -> tuple[str | None, str | None]:
    """``(value, error)`` for a plain string literal; the value is the source bytes between
    the delimiters, never a concatenation of child nodes (a lone ``#`` may split the content
    into several ``string_content`` children)."""
    if node.type != "string":
        return None, "non_literal"
    raw = _text(source, node)
    if len(raw) < 2 or raw[:1] not in (b'"', b"'") or raw[-1:] != raw[:1]:
        return None, "malformed_literal"
    children = list(node.children)
    inner = children[1:-1]
    if any(child.type == "interpolation" for child in inner):
        return None, "interpolated_literal"
    if len(children) < 2 or any(child.type != "string_content" for child in inner):
        return None, "malformed_literal"
    body = raw[1:-1]
    if not body or len(body) > limit:
        return None, "malformed_literal"
    try:
        return body.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "malformed_literal"


@dataclass
class Budget:
    """The global declaration counter (files in sorted order, classes by position)."""

    used: int = 0
    stopped: bool = False


@dataclass
class ModelClass:
    """A class whose superclass is a model base, with its direct-statement declarations."""

    span: Span
    unbound_shape: bool
    complete: bool = True
    declarations: list[Declaration] = field(default_factory=list)


@dataclass
class MigrationScan:
    declarations: list[Declaration] = field(default_factory=list)
    classes: int = 0


def _classes(tree: Any) -> list[tuple[Any, int]]:
    """Every ``class`` node reachable through ``module``/``class`` bodies, in source order, with
    the number of enclosing class/module nodes. Iterative; methods, blocks and conditionals are
    never entered."""
    found: list[tuple[Any, int]] = []
    stack = [(child, 0) for child in reversed(tree.root_node.named_children)]
    while stack:
        node, enclosing = stack.pop()
        if node.type not in {"class", "module"}:
            continue
        if node.type == "class":
            found.append((node, enclosing))
        body = node.child_by_field_name("body")
        if body is not None:
            stack.extend((child, enclosing + 1) for child in reversed(body.named_children))
    found.sort(key=lambda item: item[0].start_byte)
    return found


def _superclass(source: bytes, node: Any) -> bytes | None:
    holder = node.child_by_field_name("superclass")
    if holder is None or not holder.named_children:
        return None
    return _text(source, holder.named_children[0])


def _statements(node: Any) -> list[Any]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    return [child for child in body.named_children if child.type not in SKIPPED_STATEMENTS]


def _arguments(node: Any) -> list[Any]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def _receiverless_method(source: bytes, node: Any) -> str | None:
    """Method name of a receiverless call, the name of a bare identifier, otherwise ``None``."""
    if node.type == "call" and node.child_by_field_name("receiver") is None:
        method = node.child_by_field_name("method")
        return _name(source, method) if method is not None else None
    if node.type == "identifier":
        return _name(source, node)
    return None


def _symbol_or_literal(
    source: bytes, node: Any, limit: int, pattern: re.Pattern[str]
) -> tuple[str | None, str | None]:
    """``(value, error)`` for a ``:symbol`` or a plain string naming an identifier."""
    if node.type == "simple_symbol":
        symbol = _name(source, node)[1:]
        if symbol and len(symbol.encode("utf-8")) <= limit and pattern.fullmatch(symbol):
            return symbol, None
        return None, "malformed_literal"
    value, error = literal(source, node, limit)
    if value is None:
        return None, error
    if pattern.fullmatch(value) is None:
        return None, "malformed_literal"
    return value, None


def _association_statement(source: bytes, node: Any, method: str) -> Declaration:
    """D4.2: the first failing check wins."""
    span = _span(node)

    def diagnostic(code: str) -> Declaration:
        return _association_diagnostic(span, method, code)

    if method == "has_and_belongs_to_many":
        return diagnostic("has_and_belongs_to_many")
    named = _arguments(node)
    pairs = [child for child in named if child.type == "pair"]
    keys = {
        _name(source, key)
        for key in (pair.child_by_field_name("key") for pair in pairs)
        if key is not None and key.type == "hash_key_symbol"
    }
    for code in ASSOCIATION_OPTION_CONSTRUCTS:
        if code in keys:
            return diagnostic(code)
    if node.child_by_field_name("block") is not None:
        return diagnostic("block")
    if any(child.type in SPLAT_TYPES for child in named):
        return diagnostic("splat")
    positional = [child for child in named if child.type != "pair"]
    if not positional:
        return diagnostic("non_literal_name")
    if len(positional) == 2 and positional[1].type == "lambda":
        return diagnostic("scope_argument")
    if len(positional) != 1 or positional[0].type == "hash":
        return diagnostic("extra_arguments")
    name, _error = _symbol_or_literal(source, positional[0], MAX_NAME_BYTES, NAME_RE)
    if name is None:
        return diagnostic("non_literal_name")
    options: dict[str, Any] = {}
    for pair in pairs:
        key = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        if (
            any(part.type == "=>" for part in pair.children)
            or key is None
            or value is None
            or key.type != "hash_key_symbol"
        ):
            return diagnostic("unsupported_option")
        option = _name(source, key)
        if option in options or option not in ALLOWED_ASSOCIATION_OPTIONS:
            return diagnostic("unsupported_option")
        options[option] = value
    target = options.get("class_name")
    if target is None:
        return diagnostic("implicit_class_name")
    if target.type in SYMBOL_TYPES:
        return diagnostic("symbol_class_name")
    class_name, error = literal(source, target, MAX_CLASS_NAME_BYTES)
    if class_name is None:
        return diagnostic(
            "malformed_class_name" if error == "malformed_literal" else "computed_class_name"
        )
    if CLASS_NAME_RE.fullmatch(class_name) is None:
        return diagnostic("malformed_class_name")
    return _association(span, method, name, class_name)


def _is_self_table_name(source: bytes, node: Any) -> bool:
    receiver = node.child_by_field_name("receiver")
    method = node.child_by_field_name("method")
    return (
        node.type == "call"
        and receiver is not None
        and receiver.type == "self"
        and method is not None
        and _text(source, method) == b"table_name"
    )


def _table_statement(source: bytes, node: Any) -> Declaration | None:
    """D4.4: a direct ``self.table_name`` statement of the class body, else ``None``."""
    span = _span(node)
    if node.type == "singleton_method":
        owner = node.child_by_field_name("object")
        method = node.child_by_field_name("name")
        if (
            owner is not None
            and owner.type == "self"
            and method is not None
            and _text(source, method) == b"table_name"
        ):
            return _table_diagnostic(span, "table_name_method")
        return None
    if node.type not in {"assignment", "operator_assignment"}:
        return None
    left = node.child_by_field_name("left")
    if left is None or not _is_self_table_name(source, left):
        return None
    if node.type == "operator_assignment":
        return _table_diagnostic(span, "computed_table_name")
    right = node.child_by_field_name("right")
    if right is None:
        return _table_diagnostic(span, "computed_table_name")
    if right.type in SYMBOL_TYPES:
        return _table_diagnostic(span, "symbol_table_name")
    value, error = literal(source, right, MAX_TABLE_BYTES)
    if value is None:
        if error == "malformed_literal":
            return _table_diagnostic(span, "malformed_table_name")
        return _table_diagnostic(span, "computed_table_name")
    if IDENT_RE.fullmatch(value) is None:
        return _table_diagnostic(span, "malformed_table_name")
    return _table_literal(span, value)


def _limit_diagnostic(area: str, node: Any, method: str | None) -> Declaration:
    span = _span(node)
    if area == "association":
        return _association_diagnostic(span, str(method), "declaration_limit")
    if area == "table":
        return _table_diagnostic(span, "declaration_limit")
    return _migration_diagnostic(span, "declaration_limit")


def scan_models(tree: Any, source: bytes, budget: Budget) -> list[ModelClass]:
    """Pure extraction: the model-base classes of one file, by position, with the supported
    association/table declarations and diagnostics of their direct body statements.

    Once ``MAX_DECLARATIONS`` is reached the next declaration becomes one ``declaration_limit``
    diagnostic and no further statement is read (classes are still listed so the model index
    stays complete); the cut class is marked incomplete.
    """
    result: list[ModelClass] = []
    for node, enclosing in _classes(tree):
        if _superclass(source, node) not in MODEL_BASES:
            continue
        name = node.child_by_field_name("name")
        compact = name is not None and name.type == "scope_resolution"
        model = ModelClass(_span(node), compact and enclosing > 0)
        result.append(model)
        for statement in _statements(node):
            method = _receiverless_method(source, statement)
            if method in ASSOCIATION_METHODS:
                area = "association"
                declaration = None
            else:
                declaration = _table_statement(source, statement)
                if declaration is None:
                    continue
                area = "table"
            if budget.stopped:
                model.complete = False
                break
            if budget.used >= MAX_DECLARATIONS:
                budget.stopped = True
                model.complete = False
                model.declarations.append(_limit_diagnostic(area, statement, method))
                break
            budget.used += 1
            if declaration is None:
                declaration = _association_statement(source, statement, str(method))
            model.declarations.append(declaration)
    return result


def _migration_op(source: bytes, node: Any, op: str) -> Declaration:
    """D4.3 op checks; the first failure wins."""
    span = _span(node)

    def diagnostic(code: str) -> Declaration:
        return _migration_diagnostic(span, code)

    if op != "create_table" and node.child_by_field_name("block") is not None:
        return diagnostic("block")
    named = _arguments(node)
    if any(child.type in SPLAT_TYPES for child in named):
        return diagnostic("splat")
    positional = [child for child in named if child.type != "pair"]
    low, high = (1, 1) if op == "create_table" else (3, 3) if op == "add_column" else (2, 3)
    if len(positional) < low:
        return diagnostic("missing_arguments")
    if len(positional) > high:
        return diagnostic("extra_arguments")
    table, error = _symbol_or_literal(source, positional[0], MAX_TABLE_BYTES, IDENT_RE)
    if table is None:
        return diagnostic("non_literal_table" if error == "non_literal" else str(error))
    if op == "create_table":
        return _migration_mention(span, op, table, None)
    column, error = _symbol_or_literal(source, positional[1], MAX_TABLE_BYTES, IDENT_RE)
    if column is None:
        return diagnostic("non_literal_column" if error == "non_literal" else str(error))
    return _migration_mention(span, op, table, column)


def _migration_statement(source: bytes, node: Any) -> Declaration:
    span = _span(node)
    method = _receiverless_method(source, node)
    if method in MIGRATION_OPS and node.type == "call":
        return _migration_op(source, node, str(method))
    if method in MIGRATION_STATEMENT_CONSTRUCTS:
        return _migration_diagnostic(span, str(method))
    if node.type in CONDITIONAL_TYPES:
        return _migration_diagnostic(span, "conditional")
    return _migration_diagnostic(span, "other_statement")


def scan_migrations(tree: Any, source: bytes, budget: Budget) -> MigrationScan:
    """Pure extraction: the direct statements of ``def change``/``up``/``down`` in classes whose
    superclass is ``ActiveRecord::Migration`` (optionally ``[d.d]``)."""
    result = MigrationScan()
    for node, _enclosing in _classes(tree):
        base = _superclass(source, node)
        if base is None or MIGRATION_BASE_RE.fullmatch(base) is None:
            continue
        result.classes += 1
        for method in _statements(node):
            if method.type != "method":
                continue
            name = method.child_by_field_name("name")
            if name is None or _name(source, name) not in MIGRATION_METHODS:
                continue
            for statement in _statements(method):
                if budget.stopped:
                    return result
                if budget.used >= MAX_DECLARATIONS:
                    budget.stopped = True
                    result.declarations.append(_limit_diagnostic("migration", statement, None))
                    return result
                budget.used += 1
                result.declarations.append(_migration_statement(source, statement))
    return result


# ---- operator map ----


class TableMapError(ValueError):
    """A rejected operator map. The text is fixed per reason and never echoes document content."""

    def __init__(self, reason: str) -> None:
        super().__init__("Invalid Rails table map: " + reason)
        self.reason = reason


@dataclass(frozen=True)
class TableMap:
    sha256: str
    models: dict[str, str]


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def _bounded_int(text: str) -> int:
    """Explicit digit guard: independent of the interpreter's integer-string limit."""
    if len(text) > 64:
        raise ValueError("integer too long")
    return int(text)


def _has_surrogate(document: Any) -> bool:
    pending = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if _SURROGATE.search(item):
                return True
        elif isinstance(item, dict):
            for key, value in item.items():
                pending.append(key)
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _check_nesting(raw: bytes) -> None:
    """Linear scan: ``[``/``{`` depth outside JSON strings must not exceed ``MAX_MAP_NESTING``;
    independent of the interpreter's recursion limit."""
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > MAX_MAP_NESTING:
                raise ValueError("nesting too deep")
        elif byte in (0x5D, 0x7D):
            depth = max(depth - 1, 0)


def _parse_document(raw: bytes) -> Any:
    try:
        _check_nesting(raw)
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("byte order mark")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_refuse_constant,
            parse_int=_bounded_int,
        )
        if _has_surrogate(document):
            raise ValueError("lone surrogate")
    except Exception as exc:  # incl. RecursionError: every parse failure is one closed reason
        raise TableMapError("invalid_json") from exc
    return document


def _check_map(document: Any) -> dict[str, str]:
    if not isinstance(document, dict):
        raise TableMapError("schema")
    if "format" in document and document["format"] != MAP_FORMAT:
        raise TableMapError("unsupported_format")
    if set(document) != {"format", "models"} or not isinstance(document["models"], dict):
        raise TableMapError("schema")
    models = document["models"]
    if len(models) > MAX_MAP_ENTRIES:
        raise TableMapError("too_many_entries")
    checked: dict[str, str] = {}
    for key in sorted(models):
        # ASCII-only fullmatch first: nothing is encoded (or echoed) before it passes.
        if not isinstance(key, str) or len(key) > MAX_CLASS_NAME_BYTES:
            raise TableMapError("malformed_model")
        if MODEL_KEY_RE.fullmatch(key) is None:
            raise TableMapError("malformed_model")
        value = models[key]
        if not isinstance(value, str) or len(value) > MAX_TABLE_BYTES:
            raise TableMapError("malformed_table")
        if IDENT_RE.fullmatch(value) is None:
            raise TableMapError("malformed_table")
        checked[key] = value
    return checked


def parse_table_map(raw: bytes) -> TableMap:
    """Strict parse of the operator map bytes (the caller reads the file; this never does).

    Every failure is a :class:`TableMapError` with a closed reason and fixed text.
    """
    try:
        if not isinstance(raw, bytes):
            raise TableMapError("schema")
        if len(raw) > MAX_MAP_BYTES:
            raise TableMapError("too_large")
        models = _check_map(_parse_document(raw))
        return TableMap(hashlib.sha256(raw).hexdigest(), models)
    except TableMapError:
        raise
    except Exception as exc:  # defensive: no other exception type may escape with its text
        raise TableMapError("schema") from exc


# ---- capture ----


@dataclass
class Capture:
    """Bounded model/migration bytes retained by the build (flag-gated by the caller)."""

    files: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    limited: int = 0
    total_bytes: int = 0

    def offer(self, path: str, source: bytes, source_hash: str) -> None:
        if not (is_model_path(path) or is_migration_path(path)):
            return
        if (
            len(self.files) >= MAX_FILES
            or len(source) > MAX_FILE_BYTES
            or self.total_bytes + len(source) > MAX_CAPTURE_BYTES
        ):
            self.limited += 1
            return
        self.files[path] = (source, source_hash)
        self.total_bytes += len(source)


# ---- class-name resolution ----

Resolution = tuple[str, str | None, list[str], str | None]


def class_candidates(namespace: str, name: str) -> list[str]:
    """The Rails ``compute_type`` candidate names, deepest first (names only, no precedence)."""
    if name.startswith("::"):
        return [name[2:]]
    parts = namespace.split("::")
    names = ["::".join(parts[:count]) + "::" + name for count in range(len(parts), 0, -1)]
    names.append(name)
    return list(dict.fromkeys(names))


class Resolver:
    """Class lookup over the build's Ruby ``symbols`` table (one scan per build)."""

    def __init__(self, conn: sqlite3.Connection, paths: Iterable[str]) -> None:
        wanted = set(paths)
        self.by_span: dict[tuple[str, int, int], list[tuple[str | None, str | None]]] = {}
        self.namespaces: set[str] = set()
        self.models: dict[str, set[str]] = {}
        for row in conn.execute(
            "SELECT s.path, s.kind, s.entity_id, s.binding, "
            "json_extract(o.payload,'$.start_byte'), json_extract(o.payload,'$.end_byte') "
            "FROM symbols s JOIN occurrences o ON o.id=s.occurrence_id "
            "WHERE s.lang='ruby' AND s.kind IN ('class','module') "
            "ORDER BY s.path, s.line_start, s.id"
        ):
            namespace = _binding(row[3]).get("namespace")
            if isinstance(namespace, str):
                self.namespaces.add(namespace)
            if row[1] == "class" and row[0] in wanted:
                key = (row[0], row[4], row[5])
                entry = (row[2], namespace if isinstance(namespace, str) else None)
                self.by_span.setdefault(key, []).append(entry)

    def bind(self, path: str, span: Span) -> tuple[str, str] | None:
        """The (entity id, namespace) of the one class symbol with exactly this span."""
        rows = self.by_span.get((path, span.start_byte, span.end_byte), [])
        if len(rows) != 1 or rows[0][0] is None or rows[0][1] is None:
            return None
        return rows[0][0], rows[0][1]

    def add_model(self, namespace: str, entity_id: str) -> None:
        self.models.setdefault(namespace, set()).add(entity_id)

    def resolve(self, namespace: str, class_name: str) -> Resolution:
        existing = [c for c in class_candidates(namespace, class_name) if c in self.namespaces]
        if not existing:
            return "UNRESOLVED", None, [], CLASS_NOT_FOUND_REASON
        ids = sorted({i for c in existing for i in self.models.get(c, ())})
        # One existing name with one model entity is the only unique answer. Atlas does not
        # evaluate Rails precedence: several existing names never resolve to a single class.
        if len(existing) == 1 and len(ids) == 1:
            return "INFERRED", ids[0], [], None
        if len(ids) >= 2:
            return "AMBIGUOUS", None, ids, AMBIGUOUS_CLASS_REASON
        return "UNRESOLVED", None, [], NOT_RECOGNIZED_REASON


def _binding(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


# ---- identity, purge, manifest ----


def adapter_sha256() -> str:
    """Identity of this adapter's own bytes (the only file the adapter reads)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _producer_clause() -> str:
    return (
        "SELECT id FROM producers WHERE json_extract(payload,'$.fingerprint.rails_models') "
        "IS NOT NULL"
    )


def has_records(conn: sqlite3.Connection) -> bool:
    """Cheap probe: rails_models records cannot exist without their producer or manifest key."""
    if conn.execute("SELECT 1 FROM manifest WHERE key=?", (MANIFEST_KEY,)).fetchone():
        return True
    return conn.execute(_producer_clause() + " LIMIT 1").fetchone() is not None


def purge(conn: sqlite3.Connection) -> None:
    """Remove every rails_models-owned record and the manifest key (whatever the flag)."""
    for relation in sorted(MODEL_RELATIONS):
        conn.execute(
            "DELETE FROM assertions WHERE json_extract(payload,'$.relation')=?", (relation,)
        )
    conn.execute(
        "DELETE FROM evidence WHERE json_extract(payload,'$.producer_id') IN ("
        + _producer_clause()
        + ")"
    )
    conn.execute(
        "DELETE FROM occurrences WHERE json_extract(payload,'$.kind')=?", (ev.RAILS_MODEL_KIND,)
    )
    conn.execute(
        "DELETE FROM producers WHERE json_extract(payload,'$.fingerprint.rails_models') IS NOT NULL"
    )
    conn.execute("DELETE FROM manifest WHERE key=?", (MANIFEST_KEY,))


# ---- refresh ----


@dataclass(frozen=True)
class Fact:
    path: str
    source: bytes
    source_hash: str
    entity_id: str
    declaration: Declaration
    resolution: Resolution


@dataclass
class _Entry:
    path: str
    source: bytes
    source_hash: str
    entity_id: str
    namespace: str
    model: ModelClass


def _unresolved(reason: str) -> Resolution:
    return "UNRESOLVED", None, [], reason


def _map_declaration(span: Span, table: str, map_sha256: str) -> Declaration:
    raw = ev.canonical({"map_sha256": map_sha256, "source": "operator_map", "table": table})
    return Declaration("table", span, TABLE_ROLE + "operator_map", raw, None, None, table)


def _table_facts(
    entry: _Entry, mapped: str | None, map_sha256: str, counts: dict[str, int]
) -> list[Fact]:
    """D6 for one fully scanned bound model class; ``mapped`` is its sole map entry, if any."""

    def fact(declaration: Declaration, resolution: Resolution) -> Fact:
        return Fact(
            entry.path, entry.source, entry.source_hash, entry.entity_id, declaration, resolution
        )

    declarations = [d for d in entry.model.declarations if d.area == "table"]
    unsupported = [d for d in declarations if d.construct is not None]
    literals = [d for d in declarations if d.table is not None]
    if unsupported or not entry.model.complete:
        # An unsupported statement, or a cut scan (no table decision and no map record),
        # leaves only the diagnostics; the map entry is suppressed.
        counts["suppressed"] += mapped is not None
        return [fact(d, _unresolved(UNSUPPORTED_TABLE_REASON)) for d in unsupported]
    if len(literals) >= 2:
        counts["suppressed"] += mapped is not None
        return [fact(d, _unresolved(AMBIGUOUS_TABLE_REASON)) for d in literals]
    if len(literals) == 1:
        only = literals[0]
        if mapped is not None and mapped != only.table:
            counts["conflicting"] += 1
            conflict = _unresolved(CONFLICTING_TABLE_REASON)
            twin = _map_declaration(entry.model.span, mapped, map_sha256)
            return [fact(only, conflict), fact(twin, conflict)]
        counts["confirmed"] += mapped is not None
        return [fact(only, ("EXTRACTED", None, [], None))]
    if mapped is not None:
        counts["bound"] += 1
        twin = _map_declaration(entry.model.span, mapped, map_sha256)
        return [fact(twin, ("EXTRACTED", None, [], None))]
    return []


def _summary(
    stats: dict[str, int],
    facts: list[Fact],
    table_map: TableMap | None,
    counts: dict[str, int],
    truncated: bool,
    producer_id: str | None,
) -> dict[str, Any]:
    tally: dict[str, dict[str, int]] = {
        relation: {name: 0 for name in names} for relation, names in RESOLUTIONS.items()
    }
    for item in facts:
        tally[item.declaration.relation][item.resolution[0]] += 1
    mapping: dict[str, int] | None = None
    if table_map is not None:
        mapping = {"entries": len(table_map.models), **counts}
    return {
        "format": MANIFEST_FORMAT,
        "adapter": ADAPTER,
        "adapter_sha256": adapter_sha256(),
        "table_map_sha256": table_map.sha256 if table_map is not None else None,
        "files": {
            "models": stats["files_models"],
            "migrations": stats["files_migrations"],
            "failed": stats["files_failed"],
            "limited": stats["files_limited"],
        },
        "classes": {
            "models": stats["classes_models"],
            "migrations": stats["classes_migrations"],
            "unbound": stats["classes_unbound"],
        },
        "declarations": stats["declarations"],
        "facts": tally,
        "map": mapping,
        "truncated": truncated,
        "producer_id": producer_id,
    }


def _admit(facts: list[Fact], state: dict[str, int]) -> list[Fact]:
    """Cap the emitted operator-map records at ``MAX_MAP_ENTRIES`` (extra ones set ``capped``)."""
    kept: list[Fact] = []
    for item in facts:
        if item.declaration.role == TABLE_ROLE + "operator_map":
            if state["records"] >= MAX_MAP_ENTRIES:
                state["capped"] = 1
                continue
            state["records"] += 1
        kept.append(item)
    return kept


def refresh(
    conn: sqlite3.Connection,
    *,
    enabled: bool,
    capture: Capture,
    table_map: TableMap | None,
    observations: Iterable[dict[str, Any]],
    get_parser: Callable[[str], Any],
    graph_producer_id: str,
) -> dict[str, Any] | None:
    """Purge every rails_models-owned record, then (enabled only) rebuild them from scratch.

    Runs inside the build's staging transaction after edge resolution. ``get_parser`` is only
    called when the adapter is enabled and at least one captured file is an indexed
    observation with a matching hash.
    """
    if has_records(conn):
        purge(conn)
    if not enabled:
        return None
    # `observations` also holds `history` records; only the observation is current state.
    current = {item["path"]: item for item in observations if item["type"] == "observation"}
    stats = {
        "files_models": 0,
        "files_migrations": 0,
        "files_failed": 0,
        "files_limited": capture.limited,
        "classes_models": 0,
        "classes_migrations": 0,
        "classes_unbound": 0,
        "declarations": 0,
    }
    budget = Budget()
    parser: Any = None
    models: list[tuple[str, bytes, str, list[ModelClass]]] = []
    migrations: list[tuple[str, bytes, str, str, MigrationScan]] = []
    for path in sorted(capture.files):
        source, source_hash = capture.files[path]
        observed = current.get(path)
        file_id = ev.file_entity(path, "ruby")["id"]
        if (
            observed is None
            or observed["state"] != "indexed"
            or observed["observed_source_sha256"] != source_hash
            or conn.execute("SELECT 1 FROM entities WHERE id=?", (file_id,)).fetchone() is None
        ):
            stats["files_failed"] += 1
            continue
        if parser is None:
            parser = get_parser("ruby")
        tree = parser.parse(source)
        if tree.root_node.has_error:
            stats["files_failed"] += 1
            continue
        # Scans return plain spans; the tree is released before the next file is parsed.
        if is_model_path(path):
            stats["files_models"] += 1
            models.append((path, source, source_hash, scan_models(tree, source, budget)))
        else:
            stats["files_migrations"] += 1
            scan = scan_migrations(tree, source, budget)
            stats["classes_migrations"] += scan.classes
            migrations.append((path, source, source_hash, file_id, scan))
        del tree
    stats["declarations"] = budget.used + int(budget.stopped)
    facts: list[Fact] = []
    entries: list[_Entry] = []
    resolver = Resolver(conn, [item[0] for item in models]) if models else None
    for path, source, source_hash, classes in models:
        for model in classes:
            bound = resolver.bind(path, model.span) if resolver is not None else None
            if bound is None or MODEL_KEY_RE.fullmatch(bound[1]) is None or model.unbound_shape:
                stats["classes_unbound"] += 1
                continue
            stats["classes_models"] += 1
            if resolver is not None:
                resolver.add_model(bound[1], bound[0])
            entries.append(_Entry(path, source, source_hash, bound[0], bound[1], model))
    entries.sort(key=lambda e: (e.namespace, e.path, e.model.span.start_byte))
    groups: dict[str, list[_Entry]] = {}
    for entry in entries:
        groups.setdefault(entry.namespace, []).append(entry)
    counts = {
        "bound": 0,
        "confirmed": 0,
        "conflicting": 0,
        "ambiguous": 0,
        "suppressed": 0,
        "unmatched": 0,
    }
    state = {"records": 0, "capped": 0}
    map_sha = table_map.sha256 if table_map is not None else ""
    entries_of_map = table_map.models if table_map is not None else {}
    for entry in entries:
        for declaration in entry.model.declarations:
            if declaration.area != "association":
                continue
            if declaration.construct is not None:
                resolution = _unresolved(UNSUPPORTED_ASSOCIATION_REASON)
            elif resolver is not None:
                resolution = resolver.resolve(entry.namespace, str(declaration.class_name))
            else:
                resolution = _unresolved(CLASS_NOT_FOUND_REASON)
            facts.append(
                Fact(
                    entry.path,
                    entry.source,
                    entry.source_hash,
                    entry.entity_id,
                    declaration,
                    resolution,
                )
            )
        mapped = None
        if len(groups[entry.namespace]) == 1:
            mapped = entries_of_map.get(entry.namespace)
        facts.extend(_admit(_table_facts(entry, mapped, map_sha, counts), state))
    for key in sorted(entries_of_map):
        group = groups.get(key, [])
        if not group:
            counts["unmatched"] += 1
        elif len(group) >= 2:
            counts["ambiguous"] += 1
            for entry in group:
                twin = _map_declaration(entry.model.span, entries_of_map[key], map_sha)
                item = Fact(
                    entry.path,
                    entry.source,
                    entry.source_hash,
                    entry.entity_id,
                    twin,
                    _unresolved(AMBIGUOUS_MODEL_REASON),
                )
                facts.extend(_admit([item], state))
    for path, source, source_hash, file_id, scan in migrations:
        for declaration in scan.declarations:
            if declaration.construct is not None:
                resolution = _unresolved(UNSUPPORTED_MIGRATION_REASON)
            else:
                resolution = ("EXTRACTED", None, [], None)
            facts.append(Fact(path, source, source_hash, file_id, declaration, resolution))
    facts.sort(key=lambda f: (f.path, f.declaration.span.start_byte, f.declaration.role))
    truncated = budget.stopped or capture.limited > 0 or bool(state["capped"])
    producer_id: str | None = None
    if facts:
        producer = ev.producer(
            {
                "rails_models": {
                    "adapter": ADAPTER,
                    "adapter_sha256": adapter_sha256(),
                    "graph_producer_id": graph_producer_id,
                    "table_map_sha256": table_map.sha256 if table_map is not None else None,
                }
            }
        )
        producer_id = producer["id"]
        ev.put(conn, producer)
    for item in facts:
        declaration = item.declaration
        occurrence = ev.occurrence(
            declaration.span,
            item.path,
            "ruby",
            item.source,
            item.entity_id,
            ev.RAILS_MODEL_KIND,
            declaration.role,
            item.source_hash,
        )
        proof = ev.provenance(occurrence, str(producer_id))
        resolution = item.resolution
        assertion = ev.assertion(
            item.entity_id,
            resolution[1],
            declaration.relation,
            proof["id"],
            resolution[0],
            declaration.raw,
            resolution[2],
            resolution[3],
        )
        ev.put(conn, occurrence, item.path)
        ev.put(conn, proof, item.path)
        ev.put(conn, assertion, item.path)
    summary = _summary(stats, facts, table_map, counts, truncated, producer_id)
    conn.execute(
        "INSERT OR REPLACE INTO manifest(key,value) VALUES (?,?)",
        (MANIFEST_KEY, ev.canonical(summary)),
    )
    return summary


def record_ids(records: list[dict[str, Any]]) -> set[str]:
    """IDs of every canonical record contributed by a rails_models producer."""
    producers = {
        r["id"]
        for r in records
        if r["type"] == "producer"
        and isinstance(r["fingerprint"], dict)
        and "rails_models" in r["fingerprint"]
    }
    proofs = {r["id"] for r in records if r["type"] == "evidence" and r["producer_id"] in producers}
    occurrences = {
        r["occurrence_id"] for r in records if r["type"] == "evidence" and r["id"] in proofs
    }
    assertions = {
        r["id"] for r in records if r["type"] == "assertion" and r["evidence_id"] in proofs
    }
    return producers | proofs | occurrences | assertions


# ---- validation ----


def _decode(raw_target: Any) -> dict[str, Any]:
    ev.require(isinstance(raw_target, str), "rails_models raw_target type")
    try:
        value = json.loads(raw_target)
    except ValueError as exc:
        raise ValueError("Invalid evidence: rails_models raw_target is not JSON") from exc
    ev.require(isinstance(value, dict), "rails_models raw_target is not an object")
    ev.require(ev.canonical(value) == raw_target, "rails_models raw_target is not canonical")
    return value


def _is_ascii_match(value: Any, pattern: re.Pattern[str], limit: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and value.isascii()
        and pattern.fullmatch(value) is not None
    )


def _class_entity(conn: sqlite3.Connection, identifier: Any) -> None:
    ev.require(ev.hash_value(identifier), "rails_models target identity")
    entity = ev.get(conn, "entity", identifier)
    ev.require(
        entity["kind"] == "class" and entity["language"] == "ruby",
        "rails_models target is not a ruby class entity",
    )


def _check_resolution(
    item: dict[str, Any],
    resolution: str,
    reasons: frozenset[str] | set[str],
    *,
    target: bool = False,
) -> None:
    """The (resolution, reason, target, candidates) quadruple, without the entity kinds."""
    reason = item["unresolved_reason"]
    if resolution == "EXTRACTED":
        ev.require(
            reason is None and item["target_entity_id"] is None and not item["candidate_ids"],
            "rails_models extracted fact",
        )
    elif resolution == "INFERRED":
        ev.require(
            target and reason is None and not item["candidate_ids"], "rails_models inferred fact"
        )
    elif resolution == "AMBIGUOUS":
        ev.require(
            target
            and reason == AMBIGUOUS_CLASS_REASON
            and item["target_entity_id"] is None
            and len(item["candidate_ids"]) >= 2,
            "rails_models ambiguous fact",
        )
    else:
        ev.require(
            resolution == "UNRESOLVED"
            and isinstance(reason, str)
            and reason in reasons
            and item["target_entity_id"] is None
            and not item["candidate_ids"],
            "rails_models unresolved fact",
        )


def _check_association(
    conn: sqlite3.Connection, item: dict[str, Any], rest: str, value: dict[str, Any]
) -> None:
    resolution = item["resolution"]
    if rest.startswith("unsupported:"):
        code = rest[len("unsupported:") :]
        ev.require(
            set(value) == ASSOCIATION_DIAGNOSTIC_KEYS
            and value["mapping"] == UNEXPANDED_LABEL
            and isinstance(value["kind"], str)
            and value["kind"] in ASSOCIATION_METHODS
            and value["construct"] == code
            and code in ASSOCIATION_CONSTRUCTS,
            "rails_models association diagnostic",
        )
        ev.require(resolution == "UNRESOLVED", "rails_models association diagnostic resolution")
        _check_resolution(item, resolution, {UNSUPPORTED_ASSOCIATION_REASON})
        return
    ev.require(
        set(value) == ASSOCIATION_KEYS
        and value["mapping"] == CLASS_NAME_LABEL
        and rest in ASSOCIATION_KINDS
        and value["kind"] == rest
        and _is_ascii_match(value["name"], NAME_RE, MAX_NAME_BYTES)
        and _is_ascii_match(value["class_name"], CLASS_NAME_RE, MAX_CLASS_NAME_BYTES),
        "rails_models association",
    )
    ev.require(resolution in RESOLUTIONS[ev.RAILS_ASSOCIATION_RELATION], "association resolution")
    _check_resolution(
        item,
        resolution,
        {NOT_RECOGNIZED_REASON, CLASS_NOT_FOUND_REASON},
        target=item["target_entity_id"] is not None or bool(item["candidate_ids"]),
    )
    if resolution == "INFERRED":
        _class_entity(conn, item["target_entity_id"])
    elif resolution == "AMBIGUOUS":
        for identifier in item["candidate_ids"]:
            _class_entity(conn, identifier)


def _check_table(
    item: dict[str, Any], rest: str, value: dict[str, Any], map_sha256: str | None
) -> None:
    resolution = item["resolution"]
    ev.require(resolution in RESOLUTIONS[ev.RAILS_TABLE_RELATION], "model_table resolution")
    if rest.startswith("unsupported:"):
        code = rest[len("unsupported:") :]
        ev.require(
            set(value) == DIAGNOSTIC_KEYS
            and value["mapping"] == UNEXPANDED_LABEL
            and value["construct"] == code
            and code in TABLE_CONSTRUCTS,
            "rails_models table diagnostic",
        )
        ev.require(resolution == "UNRESOLVED", "rails_models table diagnostic resolution")
        _check_resolution(item, resolution, {UNSUPPORTED_TABLE_REASON})
        return
    ev.require(rest in TABLE_SOURCES and value.get("source") == rest, "rails_models table source")
    if rest == "literal":
        ev.require(set(value) == LITERAL_TABLE_KEYS, "rails_models literal table shape")
        reasons = {CONFLICTING_TABLE_REASON, AMBIGUOUS_TABLE_REASON}
    else:
        ev.require(
            set(value) == MAPPED_TABLE_KEYS
            and map_sha256 is not None
            and value["map_sha256"] == map_sha256,
            "rails_models map table shape",
        )
        reasons = {CONFLICTING_TABLE_REASON, AMBIGUOUS_MODEL_REASON}
    ev.require(_is_ascii_match(value["table"], IDENT_RE, MAX_TABLE_BYTES), "rails_models table")
    _check_resolution(item, resolution, reasons)


def _check_migration(item: dict[str, Any], rest: str, value: dict[str, Any]) -> None:
    resolution = item["resolution"]
    ev.require(resolution in RESOLUTIONS[ev.RAILS_MIGRATION_RELATION], "migration resolution")
    if rest.startswith("unsupported:"):
        code = rest[len("unsupported:") :]
        ev.require(
            set(value) == DIAGNOSTIC_KEYS
            and value["mapping"] == UNEXPANDED_LABEL
            and value["construct"] == code
            and code in MIGRATION_CONSTRUCTS,
            "rails_models migration diagnostic",
        )
        ev.require(resolution == "UNRESOLVED", "rails_models migration diagnostic resolution")
        _check_resolution(item, resolution, {UNSUPPORTED_MIGRATION_REASON})
        return
    ev.require(rest in MIGRATION_OPS and value.get("op") == rest, "rails_models migration op")
    ev.require(
        set(value) == (CREATE_KEYS if rest == "create_table" else COLUMN_KEYS)
        and _is_ascii_match(value["table"], IDENT_RE, MAX_TABLE_BYTES)
        and (rest == "create_table" or _is_ascii_match(value["column"], IDENT_RE, MAX_TABLE_BYTES)),
        "rails_models migration shape",
    )
    ev.require(resolution == "EXTRACTED", "rails_models migration resolution")
    _check_resolution(item, resolution, set())


AREAS = {
    "association:": ev.RAILS_ASSOCIATION_RELATION,
    "table:": ev.RAILS_TABLE_RELATION,
    "migration:": ev.RAILS_MIGRATION_RELATION,
}


def _check_assertion(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    occurrence: dict[str, Any],
    map_sha256: str | None,
) -> None:
    role = occurrence["role"]
    ev.require(
        isinstance(role, str) and role.startswith(ROLE_PREFIX), "rails_models occurrence role"
    )
    area = role[len(ROLE_PREFIX) :]
    relation = item["relation"]
    matched = [prefix for prefix, name in AREAS.items() if area.startswith(prefix)]
    ev.require(
        len(matched) == 1 and AREAS[matched[0]] == relation, "rails_models role/relation area"
    )
    rest = area[len(matched[0]) :]
    ev.require(item["source_entity_id"] == occurrence["entity_id"], "rails_models source")
    source = ev.get(conn, "entity", item["source_entity_id"])
    value = _decode(item["raw_target"])
    if relation == ev.RAILS_MIGRATION_RELATION:
        ev.require(
            source["kind"] == "file"
            and source["language"] == "ruby"
            and is_migration_path(occurrence["path"])
            and source["path"] == occurrence["path"],
            "rails_models migration source",
        )
        _check_migration(item, rest, value)
        return
    ev.require(
        source["kind"] == "class"
        and source["language"] == "ruby"
        and is_model_path(occurrence["path"])
        and source["path"] == occurrence["path"],
        "rails_models class source",
    )
    if relation == ev.RAILS_ASSOCIATION_RELATION:
        _check_association(conn, item, rest, value)
    else:
        _check_table(item, rest, value, map_sha256)


def validate_records(conn: sqlite3.Connection) -> None:
    """Referential and shape completeness of the rails_models records (build, predecessor,
    import, export). Never compares the adapter or map hash to the running values; every
    failure is ``ValueError("Invalid evidence: rails_models ...")``."""
    try:
        _validate_records(conn)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("Invalid evidence: rails_models malformed record") from exc


def _validate_records(conn: sqlite3.Connection) -> None:
    producers = {
        row[0]: json.loads(row[1])
        for row in conn.execute(
            "SELECT id, payload FROM producers "
            "WHERE json_extract(payload,'$.fingerprint.rails_models') IS NOT NULL"
        )
    }
    ev.require(len(producers) <= 1, "rails_models: more than one producer")
    map_sha256: str | None = None
    for producer in producers.values():
        fingerprint = producer["fingerprint"]["rails_models"]
        map_sha256 = fingerprint["table_map_sha256"]
        ev.require(
            conn.execute(
                "SELECT 1 FROM producers WHERE id=?", (fingerprint["graph_producer_id"],)
            ).fetchone()
            is not None,
            "rails_models: graph producer is missing",
        )
    occurrences = {
        row[0]: json.loads(row[1])
        for row in conn.execute(
            "SELECT id, payload FROM occurrences WHERE json_extract(payload,'$.kind')=?",
            (ev.RAILS_MODEL_KIND,),
        )
    }
    proofs: dict[str, dict[str, Any]] = {}
    if producers:
        for row in conn.execute(
            "SELECT id, payload FROM evidence "
            "WHERE json_extract(payload,'$.producer_id') IN (" + _producer_clause() + ")"
        ):
            proofs[row[0]] = json.loads(row[1])
    assertions: dict[str, dict[str, Any]] = {}
    for relation in sorted(MODEL_RELATIONS):
        for row in conn.execute(
            "SELECT id, payload FROM assertions WHERE json_extract(payload,'$.relation')=?",
            (relation,),
        ):
            assertions[row[0]] = json.loads(row[1])
    ev.require(
        len(assertions) <= MAX_DECLARATIONS + 1 + MAX_MAP_ENTRIES,
        "rails_models: too many declarations",
    )
    ev.require(
        all(proof["occurrence_id"] in occurrences for proof in proofs.values()),
        "rails_models: evidence cites a non-model occurrence",
    )
    ev.require(
        sorted(proof["occurrence_id"] for proof in proofs.values()) == sorted(occurrences),
        "rails_models: model occurrence without exactly one evidence",
    )
    ev.require(
        sorted(item["evidence_id"] for item in assertions.values()) == sorted(proofs),
        "rails_models: evidence without exactly one assertion",
    )
    for item in assertions.values():
        proof = proofs[item["evidence_id"]]
        _check_assertion(conn, item, occurrences[proof["occurrence_id"]], map_sha256)
    for identifier in producers:
        ev.require(
            any(proof["producer_id"] == identifier for proof in proofs.values()),
            "rails_models: producer is cited by no evidence",
        )
