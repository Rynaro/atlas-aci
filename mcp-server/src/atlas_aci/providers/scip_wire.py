"""Strict, dependency-free protobuf wire decoder for the SCIP message subset.

The schema is the vendored, hash-pinned ``scip.proto`` (scip-code/scip tag v0.6.1).
The decoder is the untrusted-input boundary of the SCIP importer: it never
imports ``protobuf``, never executes input-derived code, and rejects every
malformed or out-of-bounds encoding with a :class:`ScipDecodeError` carrying a
stable ``code``. Tests compare it against the official generated bindings.

Strictness decisions (each one is rejected, never silently coerced):

* truncated tag/varint/length-delimited/fixed fields, varints longer than ten
  bytes or whose tenth byte carries more than one bit;
* field number 0 or above 2**29-1, tags above 32 bits, wire types 3, 4, 6 and 7
  (groups and reserved types) even inside unknown fields;
* a known field arriving with the wrong wire type (the official parser would
  keep it as an unknown field; here that is a malformed index);
* invalid UTF-8 in any ``string``; ``int32``/enum values outside the int32 range
  (negative values must use the canonical ten-byte form); ``bool`` other than 0/1;
* a duplicated singular *message* field (the official parser merges; here it is
  rejected rather than guessed). Scalars are last-wins, repeated fields append,
  and repeated numeric fields accept both packed and unpacked encodings;
* ``Occurrence.range`` that is not three or four non-negative integers,
  ``enclosing_range`` that is neither empty nor three/four non-negative
  integers, or a negative ``symbol_roles``;
* configured bounds on artifact bytes, documents, occurrences, symbols, string
  length, repeated-field length and message nesting.

Decoded messages are plain dicts keyed by proto field name. Following proto3,
default-valued scalars and empty repeated fields are omitted; a message-typed
field is present when it was on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["DEFAULT_LIMITS", "SCHEMAS", "Limits", "ScipDecodeError", "decode_index"]

INT32_MAX = 2**31 - 1
MAX_FIELD_NUMBER = 2**29 - 1
VARINT, FIXED64, LENGTH, START_GROUP, END_GROUP, FIXED32 = 0, 1, 2, 3, 4, 5


class ScipDecodeError(ValueError):
    """A malformed or out-of-bounds SCIP artifact; ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 128 * 1024 * 1024
    max_documents: int = 50_000
    max_occurrences: int = 4_000_000
    max_symbols: int = 2_000_000
    max_string_bytes: int = 4 * 1024 * 1024
    max_repeated: int = 100_000
    max_nesting: int = 8


DEFAULT_LIMITS = Limits()

# name, kind, repeated, message type. Kinds: string, int32, enum, bool, message.
Field = tuple[str, str, bool, str | None]
SCHEMAS: dict[str, dict[int, Field]] = {
    "Index": {
        1: ("metadata", "message", False, "Metadata"),
        2: ("documents", "message", True, "Document"),
        3: ("external_symbols", "message", True, "SymbolInformation"),
    },
    "Metadata": {
        1: ("version", "enum", False, None),
        2: ("tool_info", "message", False, "ToolInfo"),
        3: ("project_root", "string", False, None),
        4: ("text_document_encoding", "enum", False, None),
    },
    "ToolInfo": {
        1: ("name", "string", False, None),
        2: ("version", "string", False, None),
        3: ("arguments", "string", True, None),
    },
    "Document": {
        1: ("relative_path", "string", False, None),
        2: ("occurrences", "message", True, "Occurrence"),
        3: ("symbols", "message", True, "SymbolInformation"),
        4: ("language", "string", False, None),
        5: ("text", "string", False, None),
        6: ("position_encoding", "enum", False, None),
    },
    "Occurrence": {
        1: ("range", "int32", True, None),
        2: ("symbol", "string", False, None),
        3: ("symbol_roles", "int32", False, None),
        4: ("override_documentation", "string", True, None),
        5: ("syntax_kind", "enum", False, None),
        6: ("diagnostics", "message", True, "Diagnostic"),
        7: ("enclosing_range", "int32", True, None),
    },
    "Diagnostic": {
        1: ("severity", "enum", False, None),
        2: ("code", "string", False, None),
        3: ("message", "string", False, None),
        4: ("source", "string", False, None),
        5: ("tags", "enum", True, None),
    },
    "SymbolInformation": {
        1: ("symbol", "string", False, None),
        3: ("documentation", "string", True, None),
        4: ("relationships", "message", True, "Relationship"),
        5: ("kind", "enum", False, None),
        6: ("display_name", "string", False, None),
        7: ("signature_documentation", "message", False, "Document"),
        8: ("enclosing_symbol", "string", False, None),
    },
    "Relationship": {
        1: ("symbol", "string", False, None),
        2: ("is_reference", "bool", False, None),
        3: ("is_implementation", "bool", False, None),
        4: ("is_type_definition", "bool", False, None),
        5: ("is_definition", "bool", False, None),
    },
}


def _varint(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    result = 0
    shift = 0
    for index in range(10):
        if pos >= end:
            raise ScipDecodeError("truncated", "varint runs past the end of its message")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            if index == 9 and byte > 1:
                raise ScipDecodeError("varint_overflow", "varint exceeds 64 bits")
            return result, pos
        shift += 7
    raise ScipDecodeError("varint_too_long", "varint is longer than ten bytes")


def _int32(raw: int) -> int:
    if raw <= INT32_MAX:
        return raw
    if raw >= 2**64 - 2**31:
        return raw - 2**64
    raise ScipDecodeError("int32_overflow", "integer is outside the int32 range")


class _Context:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.documents = 0
        self.occurrences = 0
        self.symbols = 0


def _count(ctx: _Context, name: str) -> None:
    limits = ctx.limits
    if name == "Document":
        ctx.documents += 1
        if ctx.documents > limits.max_documents:
            raise ScipDecodeError("too_many_documents", "document count exceeds the bound")
    elif name == "Occurrence":
        ctx.occurrences += 1
        if ctx.occurrences > limits.max_occurrences:
            raise ScipDecodeError("too_many_occurrences", "occurrence count exceeds the bound")
    elif name == "SymbolInformation":
        ctx.symbols += 1
        if ctx.symbols > limits.max_symbols:
            raise ScipDecodeError("too_many_symbols", "symbol count exceeds the bound")


def _skip(buf: bytes, wire: int, pos: int, end: int) -> int:
    if wire == VARINT:
        return _varint(buf, pos, end)[1]
    if wire == FIXED64:
        pos += 8
    elif wire == FIXED32:
        pos += 4
    elif wire == LENGTH:
        length, pos = _varint(buf, pos, end)
        pos += length
    else:
        raise ScipDecodeError("invalid_wire_type", f"unsupported wire type {wire}")
    if pos > end:
        raise ScipDecodeError("truncated", "field runs past the end of its message")
    return pos


def _range(name: str, values: list[int], *, optional: bool) -> None:
    if optional and not values:
        return
    if len(values) not in (3, 4):
        raise ScipDecodeError("range_arity", f"{name} must have three or four elements")
    if any(value < 0 for value in values):
        raise ScipDecodeError("negative_value", f"{name} contains a negative position")


def _message(
    name: str, buf: bytes, pos: int, end: int, depth: int, ctx: _Context
) -> dict[str, Any]:
    limits = ctx.limits
    if depth > limits.max_nesting:
        raise ScipDecodeError("nesting_too_deep", "message nesting exceeds the bound")
    _count(ctx, name)
    schema = SCHEMAS[name]
    result: dict[str, Any] = {}
    seen_messages: set[int] = set()
    while pos < end:
        tag, pos = _varint(buf, pos, end)
        if tag > 0xFFFFFFFF:
            raise ScipDecodeError("invalid_tag", "tag exceeds 32 bits")
        number, wire = tag >> 3, tag & 7
        if number == 0 or number > MAX_FIELD_NUMBER:
            raise ScipDecodeError("invalid_tag", "invalid field number")
        if wire not in (VARINT, FIXED64, LENGTH, FIXED32):
            raise ScipDecodeError("invalid_wire_type", f"unsupported wire type {wire}")
        spec = schema.get(number)
        if spec is None:
            pos = _skip(buf, wire, pos, end)
            continue
        field, kind, repeated, sub = spec
        packed = repeated and kind in {"int32", "enum"} and wire == LENGTH
        expected = LENGTH if kind in {"string", "message"} else VARINT
        if wire != expected and not packed:
            raise ScipDecodeError("wire_type_mismatch", f"{name}.{field} has wire type {wire}")
        if kind in {"string", "message"} or packed:
            length, pos = _varint(buf, pos, end)
            if length > end - pos:
                raise ScipDecodeError("length_beyond_buffer", f"{name}.{field} overruns its parent")
            stop = pos + length
            if kind == "string":
                if length > limits.max_string_bytes:
                    raise ScipDecodeError("string_too_long", f"{name}.{field} exceeds the bound")
                try:
                    value: Any = buf[pos:stop].decode("utf-8")
                except UnicodeDecodeError:
                    raise ScipDecodeError(
                        "invalid_utf8", f"{name}.{field} is not valid UTF-8"
                    ) from None
            elif kind == "message":
                assert sub is not None
                if not repeated:
                    if number in seen_messages:
                        raise ScipDecodeError(
                            "duplicate_message_field", f"{name}.{field} occurs more than once"
                        )
                    seen_messages.add(number)
                value = _message(sub, buf, pos, stop, depth + 1, ctx)
            else:
                values = result.setdefault(field, [])
                while pos < stop:
                    raw, pos = _varint(buf, pos, stop)
                    values.append(_int32(raw))
                    if len(values) > limits.max_repeated:
                        raise ScipDecodeError("too_many_elements", f"{name}.{field} is too long")
                continue
            pos = stop
        else:
            raw, pos = _varint(buf, pos, end)
            if kind == "bool":
                if raw > 1:
                    raise ScipDecodeError("bool_value", f"{name}.{field} is not a boolean")
                value = bool(raw)
            else:
                value = _int32(raw)
        if repeated:
            values = result.setdefault(field, [])
            values.append(value)
            if len(values) > limits.max_repeated:
                raise ScipDecodeError("too_many_elements", f"{name}.{field} is too long")
        else:
            result[field] = value
    for number, (field, kind, repeated, _sub) in schema.items():
        del number
        empty = (repeated and not result.get(field, [0])) or (
            not repeated and result.get(field) in (0, "", False)
        )
        if kind != "message" and field in result and empty:
            del result[field]
    if name == "Occurrence":
        _range("range", result.get("range", []), optional=False)
        _range("enclosing_range", result.get("enclosing_range", []), optional=True)
        if result.get("symbol_roles", 0) < 0:
            raise ScipDecodeError("negative_value", "symbol_roles is negative")
    return result


def decode_index(data: bytes, limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    """Decode one SCIP ``Index`` message; raises :class:`ScipDecodeError` on any defect."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("SCIP index must be bytes")
    data = bytes(data)
    if len(data) > limits.max_bytes:
        raise ScipDecodeError("artifact_too_large", "index exceeds the byte bound")
    return _message("Index", data, 0, len(data), 0, _Context(limits))
