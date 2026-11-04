"""ACI-S31: the closed `atlas-architecture-rules.v1` document, its selectors and its errors.

Every rejection is a structured `InvalidRule` with a closed reason, the rule index and the
rule id (the id only when it matches the id pattern). Selector text, descriptions and paths are
never echoed in an error. Matching is exact by Unicode code point: no normalization, no case
folding, no regex or fnmatch evaluation, only string operations on the sorted path universe.

Validation order (the first failure wins):
  1. more than `MAX_DOCUMENT_BYTES` bytes                       -> too_large
  2. strict parse (UTF-8, no BOM, no duplicate key, no NaN/Infinity, no lone surrogate)
                                                                -> invalid_json
  3. an object whose `format` is present and not `FORMAT`       -> unsupported_format
  4. the closed JSON Schema, then a full-match recheck of ids   -> schema
  5. more than `MAX_RULES` rules                                -> too_large
  6. per rule in document order: more than `MAX_SELECTORS` selectors on a side -> too_large;
     a relation other than `import` -> unsupported_relation; each `from` then `to` selector
     -> malformed_selector; an id already seen -> duplicate_rule_id
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from atlas_aci.storage import evidence as ev

FORMAT = "atlas-architecture-rules.v1"
RELATIONS = ("import",)
MAX_DOCUMENT_BYTES = 262144
MAX_RULES = 64
MAX_SELECTORS = 32
MAX_SELECTOR_CHARS = 512
MAX_DESCRIPTION_CHARS = 512
ID_PATTERN = "^[a-z0-9][a-z0-9._-]{0,63}$"
INVALID_RULE_REASONS = (
    "too_large",
    "invalid_json",
    "unsupported_format",
    "schema",
    "unsupported_relation",
    "malformed_selector",
    "duplicate_rule_id",
)
SELECTOR_KINDS = ("all", "tree", "children", "literal")

_ID = re.compile(ID_PATTERN)
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_FORBIDDEN_CHARS = frozenset("*?[]{}!\\")

_SELECTORS = {
    "type": "array",
    "minItems": 1,
    "uniqueItems": True,
    "items": {"type": "string", "minLength": 1, "maxLength": MAX_SELECTOR_CHARS},
}
_RULE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "relation", "from", "to"],
    "properties": {
        "id": {"type": "string", "pattern": ID_PATTERN},
        "description": {"type": "string", "maxLength": MAX_DESCRIPTION_CHARS},
        "relation": {"type": "string"},
        "from": _SELECTORS,
        "to": _SELECTORS,
    },
}
SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["format", "rules"],
    "properties": {
        "format": {"const": FORMAT},
        "rules": {"type": "array", "minItems": 1, "items": _RULE},
    },
}
VALIDATOR = Draft202012Validator(SCHEMA)


class InvalidRule(ValueError):
    """A rule document is rejected. `reason` is closed; no document text is carried."""

    code = "invalid_rule"

    def __init__(self, reason: str, rule_index: int | None = None, rule_id: str | None = None):
        if reason not in INVALID_RULE_REASONS:
            raise ValueError("Unknown invalid_rule reason")
        super().__init__(self.code + ": " + reason)
        self.reason = reason
        self.rule_index = rule_index
        self.rule_id = rule_id if isinstance(rule_id, str) and _ID.fullmatch(rule_id) else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "reason": self.reason,
            "rule_index": self.rule_index,
            "rule_id": self.rule_id,
        }


@dataclass(frozen=True)
class Selector:
    kind: str
    base: str
    text: str


@dataclass(frozen=True)
class Rule:
    index: int
    id: str
    description: str | None
    relation: str
    sources: tuple[Selector, ...]
    targets: tuple[Selector, ...]


@dataclass(frozen=True)
class RuleSet:
    sha256: str
    rules: tuple[Rule, ...]


def _segment_ok(segment: str) -> bool:
    return (
        bool(segment)
        and segment not in {".", ".."}
        and not any(c in _FORBIDDEN_CHARS or ord(c) < 32 or ord(c) == 127 for c in segment)
    )


def parse_selector(text: str) -> Selector:
    """Parse one selector or raise ValueError (the caller maps it to `malformed_selector`)."""
    if not isinstance(text, str) or not text or len(text) > MAX_SELECTOR_CHARS:
        raise ValueError("malformed selector")
    if text == "**":
        return Selector("all", "", text)
    segments = text.split("/")
    head, last = segments[:-1], segments[-1]
    if not all(_segment_ok(s) for s in head):
        raise ValueError("malformed selector")
    if last == "**":
        kind = "tree"
    elif last == "*":
        kind = "children"
    elif _segment_ok(last):
        kind = "literal"
    else:
        raise ValueError("malformed selector")
    if kind != "literal" and not head:
        raise ValueError("malformed selector")
    base = text if kind == "literal" else "/".join(head)
    if not ev.relative_path(base):
        raise ValueError("malformed selector")
    return Selector(kind, base, text)


def select(universe: Sequence[str], selector: Selector) -> list[str]:
    """Paths of the sorted `universe` the selector matches, in ascending order.

    Prefix ranges use bisect: O(log n) to locate plus the size of the matched subtree.
    A literal naming a directory matches nothing (use `dir/**`).
    """
    if selector.kind == "all":
        return list(universe)
    if selector.kind == "literal":
        at = bisect_left(universe, selector.base)
        return [selector.base] if at < len(universe) and universe[at] == selector.base else []
    prefix = selector.base + "/"
    found = []
    for at in range(bisect_left(universe, prefix), len(universe)):
        path = universe[at]
        if not path.startswith(prefix):
            break
        if selector.kind == "tree" or "/" not in path[len(prefix) :]:
            found.append(path)
    return found


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


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


def _parse(raw: bytes) -> Any:
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("byte order mark")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_refuse_constant,
        )
        if _has_surrogate(document):
            raise ValueError("lone surrogate")
    except Exception as exc:  # incl. RecursionError: every parse failure is one closed reason
        raise InvalidRule("invalid_json") from exc
    return document


def _path_key(error: Any) -> tuple[tuple[int, Any], ...]:
    return tuple((0, p) if isinstance(p, int) else (1, str(p)) for p in error.absolute_path)


def _schema_failure(document: Any) -> InvalidRule | None:
    errors = sorted(VALIDATOR.iter_errors(document), key=_path_key)
    if not errors:
        return None
    path = list(errors[0].absolute_path)
    index = path[1] if len(path) >= 2 and path[0] == "rules" and isinstance(path[1], int) else None
    rule_id = None
    if index is not None:
        rule = document["rules"][index]
        rule_id = rule.get("id") if isinstance(rule, dict) else None
    return InvalidRule("schema", index, rule_id)


def load(raw: bytes) -> RuleSet:
    """Validate `raw` and return the rule set, or raise `InvalidRule`."""
    if not isinstance(raw, bytes | bytearray) or len(raw) > MAX_DOCUMENT_BYTES:
        raise InvalidRule("too_large")
    raw = bytes(raw)
    document = _parse(raw)
    if isinstance(document, dict) and "format" in document and document["format"] != FORMAT:
        raise InvalidRule("unsupported_format")
    failure = _schema_failure(document)
    if failure is not None:
        raise failure
    for index, entry in enumerate(document["rules"]):
        # jsonschema uses re.search, so `$` accepts a trailing newline: recheck as a full match.
        if not _ID.fullmatch(entry["id"]):
            raise InvalidRule("schema", index)
    if len(document["rules"]) > MAX_RULES:
        raise InvalidRule("too_large")
    seen: set[str] = set()
    rules = []
    for index, entry in enumerate(document["rules"]):
        rule_id = entry["id"]
        if len(entry["from"]) > MAX_SELECTORS or len(entry["to"]) > MAX_SELECTORS:
            raise InvalidRule("too_large", index, rule_id)
        if entry["relation"] not in RELATIONS:
            raise InvalidRule("unsupported_relation", index, rule_id)
        parsed: dict[str, tuple[Selector, ...]] = {}
        for side in ("from", "to"):
            try:
                parsed[side] = tuple(parse_selector(text) for text in entry[side])
            except ValueError as exc:
                raise InvalidRule("malformed_selector", index, rule_id) from exc
        if rule_id in seen:
            raise InvalidRule("duplicate_rule_id", index, rule_id)
        seen.add(rule_id)
        rules.append(
            Rule(
                index,
                rule_id,
                entry.get("description"),
                entry["relation"],
                parsed["from"],
                parsed["to"],
            )
        )
    return RuleSet(hashlib.sha256(raw).hexdigest(), tuple(rules))
