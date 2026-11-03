"""ACI-S19 declaration syntax: closed per-language map, normalization, fingerprints.

Offline and read-only. Every fact here is recomputed from hash-verified source
bytes with the same grammar the indexer used; nothing is persisted and no
fingerprinted producer module is imported or changed.

Frozen at Day-1 qualification (`.spectra/evidence/aci-s19/grammar/dump.txt`):

* ``COMMENT_TYPES`` omit a node and its whole subtree.
* ``ATOMIC_TYPES`` are serialized as ``[type, exact text]`` (string, template,
  heredoc, regex and character literals keep interior whitespace and
  interpolations verbatim: over-reporting a body change is allowed, a false
  "unchanged" is not).
* ``ROWS`` map (language, grammar node type) to the entity kinds, the body
  location and the closed set of child keys (field name, or node type when the
  grammar gives no field) the row understands. Any other named child makes the
  declaration ``unmapped_declaration_child``.

Whitespace-only differences are invisible to the normalization only where the
gap between two sibling nodes is pure ASCII whitespace; any other uncovered
byte fails closed (``uncovered_source_text``).
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass
from typing import Any

SYNTAX_VERSION = "atlas-symbol-diff-syntax.v1"
# A candidate needs this many non-punctuation tokens (leaves containing a letter,
# digit or underscore) in its normalized body, or in its normalized signature for
# body-less kinds, so `pass`, `...`, `{}`, a lone `return x` or a unit struct can
# never pair two unrelated declarations.
MIN_CANDIDATE_TOKENS = 6

COMMENT_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"comment", "line_continuation"}),
    "javascript": frozenset({"comment"}),
    "typescript": frozenset({"comment"}),
    "tsx": frozenset({"comment"}),
    "ruby": frozenset({"comment"}),
    "rust": frozenset({"line_comment", "block_comment"}),
    "bash": frozenset({"comment"}),
    "scss": frozenset({"comment", "js_comment"}),
}
_JS_ATOMIC = frozenset({"string", "template_string", "regex", "jsx_text", "hash_bang_line"})
ATOMIC_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"string"}),
    "javascript": _JS_ATOMIC,
    "typescript": _JS_ATOMIC,
    "tsx": _JS_ATOMIC,
    "ruby": frozenset(
        {
            "string",
            "heredoc_body",
            "regex",
            "subshell",
            "character",
            "bare_string",
            "bare_symbol",
            "simple_symbol",
            "delimited_symbol",
        }
    ),
    "rust": frozenset({"string_literal", "raw_string_literal", "char_literal"}),
    "bash": frozenset(
        {
            "string",
            "raw_string",
            "ansi_c_string",
            "translated_string",
            "heredoc_redirect",
            "heredoc_body",
        }
    ),
    "scss": frozenset({"string_value"}),
}

_WHITESPACE = b" \t\r\n\x0b\x0c"
_RUBY_VISIBILITY = frozenset(
    {
        "private",
        "protected",
        "public",
        "module_function",
        "private_class_method",
        "public_class_method",
    }
)
_JS_WRAPPERS = frozenset({"export_statement", "ambient_declaration"})

# Closed unknown reasons raised here (diff.py adds the evidence-level ones).
FINGERPRINT_UNSUPPORTED = "fingerprint_unsupported_kind"
NODE_NOT_FOUND = "declaration_node_not_found"
PARSE_ERROR = "parse_error_in_declaration"
UNCOVERED = "uncovered_source_text"
UNMAPPED = "unmapped_declaration_child"
NON_UTF8 = "non_utf8_declaration"


class Unknown(Exception):
    """A declaration cannot be soundly fingerprinted; `reason` is a closed code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Row:
    kinds: frozenset[str]
    allowed: frozenset[str]
    body: str | None = None  # "field" (the `body` field) | "block" (child type) | None
    tail: str | None = None  # "declarator": tsx `const f = () => ...`
    semicolons: bool = False  # Ruby: header/body `;` separators are insignificant


def _row(kinds, allowed, **kwargs) -> Row:
    return Row(frozenset(kinds.split()), frozenset(allowed.split()), **kwargs)


_JS_ROWS = {
    "function_declaration": _row(
        "function", "name parameters return_type type_parameters body", body="field"
    ),
    "method_definition": _row(
        "method",
        "name parameters return_type type_parameters body decorator "
        "accessibility_modifier override_modifier",
        body="field",
    ),
    "class_declaration": _row(
        "class", "name type_parameters class_heritage body decorator", body="field"
    ),
}
_RUST_FUNCTION = "visibility_modifier function_modifiers name type_parameters parameters "
ROWS: dict[str, dict[str, Row]] = {
    "python": {
        "function_definition": _row(
            "function", "name parameters return_type type_parameters body", body="field"
        ),
        "class_definition": _row("class", "name type_parameters superclasses body", body="field"),
    },
    "javascript": _JS_ROWS,
    "typescript": _JS_ROWS,
    "tsx": {
        **_JS_ROWS,
        "variable_declarator": _row("function", "name type value", body="field", tail="declarator"),
    },
    "ruby": {
        "method": _row("method", "name parameters body", body="field", semicolons=True),
        "singleton_method": _row(
            "method", "name object parameters body", body="field", semicolons=True
        ),
        "class": _row("class", "name superclass body", body="field", semicolons=True),
        "module": _row("module", "name body", body="field", semicolons=True),
    },
    "rust": {
        "function_item": _row(
            "function method",
            _RUST_FUNCTION + "return_type where_clause body",
            body="field",
        ),
        "function_signature_item": _row("method", _RUST_FUNCTION + "return_type where_clause"),
        "struct_item": _row("struct", "visibility_modifier name type_parameters where_clause body"),
        "enum_item": _row("enum", "visibility_modifier name type_parameters where_clause body"),
        "trait_item": _row(
            "trait",
            "visibility_modifier name type_parameters bounds where_clause body",
            body="field",
        ),
        "mod_item": _row("module", "visibility_modifier name body", body="field"),
    },
    "bash": {"function_definition": _row("function", "name body", body="field")},
    "scss": {
        "mixin_statement": _row("mixin", "name parameters block", body="block"),
        "function_statement": _row("function", "name parameters block", body="block"),
    },
}
_VALUE_KEYS = frozenset(
    {"parameters", "parameter", "type_parameters", "return_type", "body", "name"}
)
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression"})

# Limitations, static and sorted; surfaced verbatim in every change set.
LIMITATIONS = (
    "ambiguous_duplicate_siblings_reported_as_signature_or_body_changes",
    "comment_only_edits_report_as_format_and_can_mask_behavior_bearing_comments",
    "definition_without_one_source_occurrence_is_unknown",
    "generated_and_macro_expanded_code_is_not_compared",
    "ids_embed_the_absolute_checkout_path_and_are_not_portable",
    "overload_signatures_and_other_non_entity_siblings_are_not_compared",
    "raw_text_kinds_report_location_or_unknown_only",
    "rename_and_move_candidates_are_conservative_and_never_merged_into_identity",
    "ruby_section_style_visibility_is_not_part_of_a_method_signature",
    "scip_entities_are_excluded",
    "semantic_equivalence_is_not_decided",
    "unrelated_repository_recreated_at_the_same_path_is_undetectable",
    "whitespace_only_text_leaves_such_as_jsx_text_report_as_body_changes",
)


def parsed_kinds(language: str) -> frozenset[str]:
    return frozenset(k for row in ROWS.get(language, {}).values() for k in row.kinds)


@dataclass(frozen=True)
class Facts:
    basis: str  # "parsed" | "raw_text"
    declaration_sha256: str
    own_text_sha256: str
    signature_sha256: str | None
    body_sha256: str | None
    signature_tokens: int
    body_tokens: int | None


@dataclass(frozen=True)
class EntityInput:
    entity_id: str
    kind: str
    name: str
    start_byte: int
    end_byte: int


def _sha(domain: str, data: bytes) -> str:
    return hashlib.sha256(domain.encode() + b"\n" + data).hexdigest()


def _line(*parts: Any) -> bytes:
    return (
        json.dumps(
            list(parts), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        + b"\n"
    )


class _Stream:
    """Flat canonical event stream: nesting is part of the hash, recursion is not."""

    def __init__(self, domain: str):
        self.domain = domain
        self.data = bytearray()
        self.tokens = 0

    def emit(self, *parts: Any) -> None:
        self.data += _line(*parts)

    def digest(self) -> str:
        return _sha(self.domain, bytes(self.data))


@dataclass
class _Placement:
    entity: EntityInput
    node: Any  # the declaration node (None for raw kinds with no exact node)
    row: Row | None
    chain: list[tuple[Any, Any]]  # (wrapper, node it wraps), innermost first
    siblings: list[Any]  # context siblings absorbed with the declaration
    placeholder: Any  # node replaced by a `<decl>` marker inside a parent
    own_start: int
    own_end: int


class FileAnalysis:
    """All declaration facts for one (path, source bytes) under one language."""

    def __init__(self, language: str, source: bytes, entities: list[EntityInput]):
        from tree_sitter_language_pack import get_parser

        self.language = language
        self.source = source
        self.comments = COMMENT_TYPES.get(language, frozenset())
        self.atomic = ATOMIC_TYPES.get(language, frozenset())
        self.rows = ROWS.get(language, {})
        self.kinds = parsed_kinds(language)
        self.root = get_parser(language).parse(source).root_node  # type: ignore[arg-type]
        self.placements: dict[str, _Placement | Unknown] = {}
        for entity in entities:
            try:
                self.placements[entity.entity_id] = self._place(entity)
            except Unknown as failure:
                self.placements[entity.entity_id] = failure
        self.subst: dict[int, tuple[Any, ...]] = {}
        self.skip: set[int] = set()
        spans = []
        for placement in self.placements.values():
            if isinstance(placement, Unknown):
                continue
            marker = ("<decl>", placement.entity.kind, placement.entity.name)
            if placement.placeholder is not None:
                self.subst[placement.placeholder.id] = marker
            self.skip.update(node.id for node in placement.siblings)
            spans.append((placement.own_start, placement.own_end, placement.entity))
        self.spans = sorted(spans, key=lambda s: (s[0], -s[1], s[2].entity_id))
        self.starts = [s[0] for s in self.spans]

    # ---- declaration lookup -------------------------------------------------
    def _chain(self, entity: EntityInput) -> list[Any]:
        node = self.root.descendant_for_byte_range(entity.start_byte, entity.end_byte)
        found = []
        while node is not None and (node.start_byte, node.end_byte) == (
            entity.start_byte,
            entity.end_byte,
        ):
            found.append(node)
            node = node.parent
        return found

    def _text(self, node: Any) -> bytes:
        return self.source[node.start_byte : node.end_byte]

    def _place(self, entity: EntityInput) -> _Placement:
        chain = self._chain(entity)
        if entity.kind not in self.kinds:
            outer = chain[-1] if chain else None
            return _Placement(
                entity, outer, None, [], [], outer, entity.start_byte, entity.end_byte
            )
        matches = [
            n for n in chain if n.type in self.rows and entity.kind in self.rows[n.type].kinds
        ]
        if len(matches) != 1:
            raise Unknown(NODE_NOT_FOUND)
        node = matches[0]
        row = self.rows[node.type]
        name = node.child_by_field_name("name")
        if name is None or self._text(name).decode("utf-8", "replace") != entity.name:
            raise Unknown(NODE_NOT_FOUND)
        if row.tail == "declarator":
            value = node.child_by_field_name("value")
            if value is None or value.type not in _FUNCTION_VALUES:
                raise Unknown(NODE_NOT_FOUND)
        wrappers, siblings = self._context(node, entity.kind)
        placeholder = wrappers[-1][0] if wrappers else node
        start = min([placeholder.start_byte, *(s.start_byte for s in siblings)])
        return _Placement(
            entity, node, row, wrappers, siblings, placeholder, start, placeholder.end_byte
        )

    def _context(self, node: Any, kind: str) -> tuple[list[tuple[Any, Any]], list[Any]]:
        """Syntax the grammar places outside the declaration node but is contract."""
        language = self.language
        wrappers: list[tuple[Any, Any]] = []
        siblings: list[Any] = []
        if language == "python":
            parent = node.parent
            if (
                parent is not None
                and parent.type == "decorated_definition"
                and _same(parent.child_by_field_name("definition"), node)
            ):
                wrappers.append((parent, node))
        elif language in {"javascript", "typescript", "tsx"}:
            child = node
            if node.type == "variable_declarator":
                # Only the declaration keyword (and the export chain above it) is
                # context, read in `_context_events`; nothing wraps a declarator
                # when it is nested in a parent declaration.
                parent = node.parent
                if parent is None or parent.type not in {
                    "lexical_declaration",
                    "variable_declaration",
                }:
                    raise Unknown(NODE_NOT_FOUND)
                return [], []
            while True:
                parent = child.parent
                if (
                    parent is None
                    or parent.type not in _JS_WRAPPERS
                    or not _same(parent.child_by_field_name("declaration"), child)
                ):
                    break
                wrappers.append((parent, child))
                child = parent
            if node.type == "method_definition":
                prior = node.prev_sibling
                while prior is not None:
                    if prior.type in self.comments:
                        prior = prior.prev_sibling
                    elif prior.type == "decorator":
                        siblings.append(prior)
                        prior = prior.prev_sibling
                    else:
                        break
        elif language == "ruby":
            arguments = node.parent
            call = arguments.parent if arguments is not None else None
            if (
                arguments is not None
                and arguments.type == "argument_list"
                and call is not None
                and call.type == "call"
                and call.child_by_field_name("receiver") is None
                and _same(call.child_by_field_name("arguments"), arguments)
            ):
                verb = call.child_by_field_name("method")
                members = [
                    c for c in arguments.children if c.is_named and c.type not in self.comments
                ]
                if (
                    verb is not None
                    and self._text(verb).decode("utf-8", "replace") in _RUBY_VISIBILITY
                    and len(members) == 1
                    and _same(members[0], node)
                ):
                    wrappers.append((call, node))
        elif language == "rust":
            prior = node.prev_sibling
            while prior is not None:
                if prior.type in self.comments:
                    prior = prior.prev_sibling
                elif prior.type == "attribute_item":
                    siblings.append(prior)
                    prior = prior.prev_sibling
                else:
                    break
        return wrappers, siblings

    # ---- serialization ------------------------------------------------------
    def _enter(self, node: Any, stream: _Stream, subst, root: bool) -> list[Any] | None:
        if not root:
            marker = subst.get(node.id)
            if marker is not None:
                stream.emit("p", *marker)
                return None
            if node.type in self.comments or node.id in self.skip:
                return None
        if node.type == "ERROR" or node.is_missing or node.has_error:
            raise Unknown(PARSE_ERROR)
        if node.child_count == 0 or node.type in self.atomic:
            try:
                text = self._text(node).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise Unknown(NON_UTF8) from exc
            stream.emit("l", node.type, text)
            if any(ch.isalnum() or ch == "_" for ch in text):
                stream.tokens += 1
            return None
        children = node.children
        cursor = node.start_byte
        for child in children:
            if child.start_byte < cursor or self._text_gap(cursor, child.start_byte):
                raise Unknown(UNCOVERED)
            cursor = child.end_byte
        if self._text_gap(cursor, node.end_byte):
            raise Unknown(UNCOVERED)
        stream.emit("o", node.type)
        return children

    def _text_gap(self, start: int, end: int) -> bool:
        return end < start or bool(self.source[start:end].strip(_WHITESPACE))

    def _serialize(self, root: Any, stream: _Stream, subst, semicolons: bool = False) -> None:
        children = self._enter(root, stream, subst, True)
        if children is None:
            return
        stack = [iter(children)]
        while stack:
            node = next(stack[-1], None)
            if node is None:
                stream.emit("c")
                stack.pop()
                continue
            if semicolons and len(stack) == 1 and node.type == ";" and not node.is_named:
                continue
            nested = self._enter(node, stream, subst, False)
            if nested is not None:
                stack.append(iter(nested))

    # ---- facts --------------------------------------------------------------
    def facts(self, entity_id: str) -> Facts:
        placement = self.placements[entity_id]
        if isinstance(placement, Unknown):
            raise placement
        entity = placement.entity
        declaration = self.source[entity.start_byte : entity.end_byte]
        own = self._own_text(placement)
        if placement.row is None:
            return Facts(
                "raw_text", hashlib.sha256(declaration).hexdigest(), own, None, None, 0, None
            )
        node, row = placement.node, placement.row
        self._check_children(node, row.allowed)
        name = node.child_by_field_name("name")
        body = None
        base_subst: dict[int, tuple[Any, ...]] = {**self.subst}
        base_subst.pop(node.id, None)
        if row.tail == "declarator":
            value = node.child_by_field_name("value")
            self._check_children(value, _VALUE_KEYS)
            body = value.child_by_field_name("body")
        elif row.body == "field":
            body = node.child_by_field_name("body")
        elif row.body == "block":
            body = next((c for c in node.children if c.type == "block"), None)
        local = dict(base_subst)
        local[name.id] = ("<name>",)
        if body is not None:
            local[body.id] = ("<body>",)
        signature = _Stream("signature")
        self._context_events(placement, signature)
        signature.emit("D")
        self._serialize(node, signature, local, semicolons=row.semicolons)
        body_hash = None
        body_tokens = None
        if body is not None:
            stream = _Stream("body")
            self._serialize(body, stream, base_subst)
            body_hash, body_tokens = stream.digest(), stream.tokens
        return Facts(
            "parsed",
            hashlib.sha256(declaration).hexdigest(),
            own,
            signature.digest(),
            body_hash,
            signature.tokens,
            body_tokens,
        )

    def _check_children(self, node: Any, allowed: frozenset[str]) -> None:
        for index, child in enumerate(node.children):
            if not child.is_named or child.type in self.comments:
                continue
            key = node.field_name_for_child(index) or child.type
            if key not in allowed and child.type not in allowed:
                raise Unknown(UNMAPPED)

    def _context_events(self, placement: _Placement, stream: _Stream) -> None:
        node = placement.node
        stream.emit("C")
        if node.type == "variable_declarator":
            parent = node.parent
            kind = parent.child_by_field_name("kind") or parent.children[0]
            stream.emit("k", kind.type)
            child = parent
            while True:
                above = child.parent
                if (
                    above is None
                    or above.type not in _JS_WRAPPERS
                    or not _same(above.child_by_field_name("declaration"), child)
                ):
                    break
                self._serialize(above, stream, {child.id: ("<decl>",)})
                child = above
            return
        for wrapper, wrapped in placement.chain:
            self._serialize(wrapper, stream, {wrapped.id: ("<decl>",)})
        for sibling in reversed(placement.siblings):
            self._serialize(sibling, stream, {})

    def _own_text(self, placement: _Placement) -> str:
        start, end = placement.own_start, placement.own_end
        index = bisect.bisect_left(self.starts, start)
        events: list[Any] = []
        cursor = start
        while index < len(self.spans) and self.starts[index] < end:
            s, e, other = self.spans[index]
            index += 1
            if other.entity_id == placement.entity.entity_id or e > end or s < cursor:
                continue
            if (s, e) == (start, end):
                continue
            events.append(["t", hashlib.sha256(self.source[cursor:s]).hexdigest()])
            events.append(["d", other.kind, other.name])
            cursor = e
        events.append(["t", hashlib.sha256(self.source[cursor:end]).hexdigest()])
        return _sha("own_text", json.dumps(events, separators=(",", ":")).encode())


def _same(left: Any, right: Any) -> bool:
    return left is not None and right is not None and left.id == right.id
