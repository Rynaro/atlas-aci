"""ACI-S34: explicit ADR-span-to-source mappings resolved against one leased snapshot.

An operator names, by hand, which span of which decision document (an ADR) relates to which
indexed source endpoint. `rationale-map` resolves every mapping against the mapped snapshot
only: the document bytes come from that snapshot's source pack (never from the checkout), the
span is a byte range with a declared digest, and the target is an exact entity id whose stored
path, kind and name must equal the declared descriptors. A mapping that cannot be resolved is
reported as an `unresolved_mapping`; it is never repaired, searched for by name or rebound.

The result is the standalone, content-addressed overlay ``atlas-rationale-overlay.v1``. Nothing
here writes the graph, a repository or an ADR, and nothing is reachable from an MCP tool.
Regular expressions use explicit ASCII classes and `re.fullmatch` only; nothing recurses.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from atlas_aci.cross_repo import revision_vector as vector
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import DiffUsageError, Side
from atlas_aci.storage import evidence as ev

MAPPING_FORMAT = "atlas-rationale-mapping.v1"
OVERLAY_FORMAT = "atlas-rationale-overlay.v1"
SUMMARY_FORMAT = "atlas-rationale-summary.v1"
DRIFT_FORMAT = "atlas-rationale-drift.v1"
PRODUCER = {"name": "atlas-aci rationale-map", "version": "atlas-rationale-map.v1"}
LINK_KIND = "operator_declared_rationale_link"
RECORD_TAG = "atlas-rationale-record.v1:"
COMMANDS = ("rationale-map", "rationale-drift")

LEASE_REASONS = ("index_unavailable", "source_snapshot_unavailable", "index_upgrade_required")
REJECTIONS = (
    "mapping_too_large",
    "too_many_mappings",
    "invalid_mapping",
    "unsupported_mapping_version",
    *LEASE_REASONS,
    "mapping_snapshot_mismatch",
    "output_too_large",
    "invalid_overlay",
)
IMPORT_REASONS = (
    "document_not_in_snapshot",
    "document_digest_mismatch",
    "span_out_of_range",
    "span_not_utf8",
    "span_digest_mismatch",
    "target_not_found",
    "target_kind_unsupported",
    "target_descriptor_mismatch",
    "target_definition_ambiguous",
)
USAGE_NAMES = (
    "max_mapping_bytes",
    "max_mappings",
    "max_output_bytes",
    "output_location",
    "output_equals_input",
    "graph_namespace",
    "same_namespace",
    "repository",
)
TARGET_CLASSES = ("symbol", "file")
IMPORT_STAGE = "mapping_import"
LIMITATIONS = (
    "review_signal_is_an_observation_not_a_verdict",
    "whole_document_digest_any_edit_suppresses_the_review_signal",
    "format_and_location_only_changes_raise_no_signal_and_can_mask_comment_pragmas",
    "same_identity_recreation_is_indistinguishable_from_an_edit_and_ordinal_shifts_need_siblings",
    "renamed_or_moved_targets_are_reported_unresolved_and_never_rebound",
    "documents_are_read_from_the_snapshot_never_the_checkout",
    "document_not_in_snapshot_covers_absent_and_present_but_non_indexed_documents",
    "file_targets_compare_whole_file_digests",
    "ids_embed_the_absolute_checkout_path_and_are_not_portable",
    "decision_and_mapping_ids_are_operator_labels",
    "comparison_rejection_reports_every_mapping_as_unknown_target_history",
    "not_written_to_the_graph_and_not_exposed_over_mcp",
)
# name -> (default, minimum, maximum)
LIMITS: dict[str, tuple[int, int, int]] = {
    "max_mapping_bytes": (1048576, 1, 16777216),
    "max_mappings": (1000, 1, 10000),
    "max_output_bytes": (16777216, 1, 67108864),
}

MAPPING_KEYS = ("format", "snapshot_id", "mappings")
ENTRY_KEYS = ("mapping_id", "decision_id", "document", "span", "target")
DOCUMENT_KEYS = ("path", "sha256")
SPAN_KEYS = ("start_byte", "end_byte", "sha256")
TARGET_KEYS = ("entity_id", "path", "kind", "name")
OVERLAY_KEYS = (
    "format",
    "id",
    "producer",
    "link_kind",
    "mapping_digest",
    "snapshot",
    "counts",
    "resolved",
    "unresolved",
    "limitations",
)
PRODUCER_KEYS = ("name", "version")
SNAPSHOT_KEYS = ("snapshot_id", "generation_id")
COUNT_KEYS = ("mappings", "resolved", "unresolved")
RESOLVED_KEYS = (
    "id",
    "type",
    "mapping_id",
    "decision_id",
    "document",
    "span",
    "target",
    "snapshot_id",
    "generation_id",
)
RESOLVED_SPAN_KEYS = ("start_byte", "end_byte", "start_line", "end_line", "sha256")
RESOLVED_TARGET_KEYS = (
    "entity_id",
    "target_class",
    "path",
    "kind",
    "name",
    "language",
    "owners",
    "ordinal",
    "occurrence_id",
    "start_byte",
    "end_byte",
    "source_sha256",
)
UNRESOLVED_KEYS = (
    "id",
    "type",
    "stage",
    "mapping_id",
    "decision_id",
    "reason",
    "document",
    "span",
    "target",
    "snapshot_id",
)
DRIFT_COUNT_KEYS = (
    "mappings",
    "resolved",
    "unresolved_at_import",
    "rationale_review_needed",
    "unknown_target_history",
    "unresolved_mapping",
    "no_finding",
)
SUMMARY_KEYS = ("format", "command", "document_format", "document_id", "mapping_digest", "counts")

ENTITY_PAYLOAD_SQL = "SELECT payload FROM entities WHERE id=?"
ENTITIES_BY_PATH_SQL = "SELECT payload FROM entities WHERE path=?"
OCCURRENCES_BY_PATH_SQL = "SELECT payload FROM occurrences WHERE path=?"
_ENTITY_FIELDS = ("id", "path", "kind", "name", "language", "owners", "ordinal")

_LABEL = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_KIND = r"[A-Za-z0-9_.:-]{1,64}"
_MAX_INT = 2147483647


class RationaleRejected(ValueError):
    """The mapping or the analysis stops; `reason` is a closed code."""

    def __init__(self, reason: str):
        super().__init__("rationale_rejected: " + reason)
        self.reason = reason


class RationaleUsageError(ValueError):
    """The invocation is invalid; `what` is a closed name."""

    def __init__(self, what: str):
        super().__init__("rationale_usage: " + what)
        self.what = what


# ---- limits -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    max_mapping_bytes: int
    max_mappings: int
    max_output_bytes: int


def normalize_limits(chosen: Mapping[str, int | None] | None = None) -> Limits:
    """Defaults for absent values; a bool, a non-int or an out-of-range value is a usage error."""
    chosen = {} if chosen is None else chosen
    unknown = sorted(set(chosen) - set(LIMITS))
    if unknown:
        raise RationaleUsageError(unknown[0])
    values: dict[str, int] = {}
    for name, (default, low, high) in LIMITS.items():
        value = chosen.get(name)
        if value is None:
            values[name] = default
        elif type(value) is not int or not low <= value <= high:
            raise RationaleUsageError(name)
        else:
            values[name] = value
    return Limits(**values)


# ---- identities and bytes -----------------------------------------------------------------------


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_bytes(document: Any) -> bytes:
    """The exact bytes written: canonical JSON plus one newline."""
    return ev.canonical(document).encode("utf-8") + b"\n"


def seal_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """The record with its content-addressed id (the mapping digest is not part of it)."""
    body = {key: value for key, value in record.items() if key != "id"}
    return {"id": sha_text(RECORD_TAG + ev.canonical(body)), **body}


def overlay_identity(overlay: Mapping[str, Any]) -> str:
    rest = {key: value for key, value in overlay.items() if key != "id"}
    return sha_text(OVERLAY_FORMAT + ":" + ev.canonical(rest))


# ---- the mapping --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    mapping_id: str
    decision_id: str
    document_path: str
    document_sha256: str
    start_byte: int
    end_byte: int
    span_sha256: str
    entity_id: str
    target_path: str
    target_kind: str
    target_name: str


@dataclass(frozen=True)
class ParsedMapping:
    snapshot_id: str
    entries: tuple[Entry, ...]
    digest: str


def _invalid() -> RationaleRejected:
    return RationaleRejected("invalid_mapping")


def _strict_strings(document: Any) -> None:
    """Every string (and key) must encode as UTF-8: a lone surrogate is an invalid mapping."""
    pending: list[Any] = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise _invalid() from None
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _closed(value: Any, keys: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise _invalid()
    return value


def _label(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(_LABEL, value) is None:
        raise _invalid()
    return value


def _hash(value: Any) -> str:
    if not ev.hash_value(value):
        raise _invalid()
    return str(value)


def _relative(value: Any) -> str:
    if not ev.relative_path(value) or len(value) > 4096:
        raise _invalid()
    return str(value)


def _integer(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_INT:
        raise _invalid()
    return value


def _entry(item: Any) -> Entry:
    body = _closed(item, ENTRY_KEYS)
    document = _closed(body["document"], DOCUMENT_KEYS)
    span = _closed(body["span"], SPAN_KEYS)
    target = _closed(body["target"], TARGET_KEYS)
    kind, name = target["kind"], target["name"]
    if not isinstance(kind, str) or re.fullmatch(_KIND, kind) is None:
        raise _invalid()
    if not isinstance(name, str) or not 1 <= len(name) <= 1024:
        raise _invalid()
    return Entry(
        mapping_id=_label(body["mapping_id"]),
        decision_id=_label(body["decision_id"]),
        document_path=_relative(document["path"]),
        document_sha256=_hash(document["sha256"]),
        start_byte=_integer(span["start_byte"]),
        end_byte=_integer(span["end_byte"]),
        span_sha256=_hash(span["sha256"]),
        entity_id=_hash(target["entity_id"]),
        target_path=_relative(target["path"]),
        target_kind=kind,
        target_name=name,
    )


def parse_mapping(raw: bytes, limits: Limits | None = None) -> ParsedMapping:
    """One strict mapping document; every violation is a closed rejection."""
    limits = normalize_limits() if limits is None else limits
    if len(raw) > limits.max_mapping_bytes:
        raise RationaleRejected("mapping_too_large")
    try:
        document = vector.load_strict(raw, numbers=True)
    except ValueError:
        raise _invalid() from None
    _strict_strings(document)
    if (
        isinstance(document, dict)
        and isinstance(document.get("format"), str)
        and document["format"] != MAPPING_FORMAT
    ):
        raise RationaleRejected("unsupported_mapping_version")
    top = _closed(document, MAPPING_KEYS)
    snapshot_id = _hash(top["snapshot_id"])
    items = top["mappings"]
    if not isinstance(items, list) or not items:
        raise _invalid()
    if len(items) > limits.max_mappings:
        raise RationaleRejected("too_many_mappings")
    entries = tuple(_entry(item) for item in items)
    if len({entry.mapping_id for entry in entries}) != len(entries):
        raise _invalid()
    return ParsedMapping(snapshot_id, entries, hashlib.sha256(raw).hexdigest())


# ---- the leases ---------------------------------------------------------------------------------


def open_side(stack: ExitStack, side: Side, prefix: str = "") -> Any:
    """Lease one side's active snapshot read-only; the schema epoch must be the current one."""
    try:
        reader = runtime.open_active(stack, side)
    except runtime.OverlayRejected as exc:
        reason = exc.reason if exc.reason in LEASE_REASONS else "index_unavailable"
        raise RationaleRejected(prefix + reason) from None
    except DiffUsageError:
        raise RationaleUsageError("graph_namespace") from None
    if reader._lease_schema_epoch != max(runtime.SCHEMA_EPOCHS):
        raise RationaleRejected(prefix + "index_upgrade_required")
    return reader


def open_mapped(stack: ExitStack, side: Side, snapshot_id: str, prefix: str = "") -> Any:
    """Lease the side; its active snapshot must be the one the mapping names."""
    reader = open_side(stack, side, prefix)
    if reader.snapshot_id != snapshot_id:
        raise RationaleRejected("mapping_snapshot_mismatch")
    return reader


# ---- the facts of one leased snapshot -----------------------------------------------------------


def _row(payload: Any) -> dict[str, Any]:
    row = json.loads(payload)
    if not isinstance(row, dict) or any(key not in row for key in _ENTITY_FIELDS):
        raise ValueError("entity row")
    texts = (row["id"], row["path"], row["kind"], row["language"])
    if not all(isinstance(text, str) for text in texts):
        raise ValueError("entity row text")
    if not isinstance(row["owners"], list) or type(row["ordinal"]) is not int:
        raise ValueError("entity row shape")
    return row


def _target_class(row: Mapping[str, Any]) -> str | None:
    """`symbol` or `file`; None for the kinds this link does not support."""
    kind = row["kind"]
    if kind == "file":
        return "file"
    if kind.startswith("scip_") or kind == ev.COVERAGE_TEST_KIND:
        return None
    return "symbol"


def _definition(
    payloads: Sequence[Mapping[str, Any]], entity: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """The one source definition occurrence of `entity` among its path's occurrences."""
    role = "definition:def." + entity["kind"]
    found = [
        item
        for item in payloads
        if item.get("entity_id") == entity["id"]
        and item.get("kind") == "definition"
        and item.get("provenance_state") == "source"
        and item.get("role") == role
    ]
    return found[0] if len(found) == 1 else None


def _sibling_key(row: Mapping[str, Any]) -> tuple[str, str, Any, tuple[Any, ...]]:
    owners = tuple(
        (item.get("kind"), item.get("name")) if isinstance(item, dict) else None
        for item in row["owners"]
    )
    return (row["path"], row["kind"], row["name"], owners)


class Snapshot:
    """Read-only facts of one leased snapshot, each fetched at most once."""

    def __init__(self, reader: Any, prefix: str = ""):
        self.reader = reader
        self.prefix = prefix
        self._observations: dict[str, dict[str, Any]] | None = None
        self._rows: dict[str, dict[str, Any] | None] = {}
        self._occurrences: dict[str, list[dict[str, Any]]] = {}
        self._at_path: dict[str, list[dict[str, Any]]] = {}
        self._documents: dict[tuple[str, str], bytes] = {}

    def observations(self) -> dict[str, dict[str, Any]]:
        if self._observations is None:
            try:
                row = self.reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()
                if row is None:
                    raise ValueError("no observations")
                found: dict[str, dict[str, Any]] = {}
                for item in json.loads(row[0]):
                    if item.get("type") == "observation":
                        found[item["path"]] = item
            except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
                raise RationaleRejected(self.prefix + "index_unavailable") from None
            self._observations = found
        return self._observations

    def indexed_hash(self, path: str) -> str | None:
        """The observed digest of `path` when it is indexed in this snapshot, else None."""
        record = self.observations().get(path)
        if record is None or record.get("state") != "indexed":
            return None
        value = record.get("observed_source_sha256")
        return str(value) if ev.hash_value(value) else None

    def entity(self, entity_id: str) -> dict[str, Any] | None:
        if entity_id not in self._rows:
            try:
                found = self.reader.db.execute(ENTITY_PAYLOAD_SQL, (entity_id,)).fetchone()
                self._rows[entity_id] = None if found is None else _row(found[0])
            except (sqlite3.Error, ValueError, TypeError):
                raise RationaleRejected(self.prefix + "index_unavailable") from None
        return self._rows[entity_id]

    def entities_at(self, path: str) -> list[dict[str, Any]]:
        if path not in self._at_path:
            try:
                rows = self.reader.db.execute(ENTITIES_BY_PATH_SQL, (path,)).fetchall()
                self._at_path[path] = [_row(payload) for (payload,) in rows]
            except (sqlite3.Error, ValueError, TypeError):
                raise RationaleRejected(self.prefix + "index_unavailable") from None
        return self._at_path[path]

    def occurrences(self, path: str) -> list[dict[str, Any]]:
        if path not in self._occurrences:
            try:
                rows = self.reader.db.execute(OCCURRENCES_BY_PATH_SQL, (path,)).fetchall()
                found = [json.loads(payload) for (payload,) in rows]
                if not all(isinstance(item, dict) for item in found):
                    raise ValueError("occurrence row")
            except (sqlite3.Error, ValueError, TypeError):
                raise RationaleRejected(self.prefix + "index_unavailable") from None
            self._occurrences[path] = found
        return self._occurrences[path]

    def definition(self, row: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return _definition(self.occurrences(row["path"]), row)

    def siblings(self, row: Mapping[str, Any]) -> list[str]:
        """Sorted ids of the entities at the path sharing kind, name and owner names."""
        key = _sibling_key(row)
        return sorted(
            item["id"] for item in self.entities_at(row["path"]) if _sibling_key(item) == key
        )

    def document(self, path: str, digest: str) -> bytes:
        """The document bytes from the snapshot pack (never from the checkout)."""
        key = (path, digest)
        if key not in self._documents:
            try:
                data = self.reader._source_lease.read(path, digest)
            except (ValueError, OSError, RuntimeError, KeyError):
                raise RationaleRejected(self.prefix + "source_snapshot_unavailable") from None
            if hashlib.sha256(data).hexdigest() != digest:
                raise RationaleRejected(self.prefix + "source_snapshot_unavailable")
            self._documents[key] = data
        return self._documents[key]


# ---- resolution ---------------------------------------------------------------------------------


def _unresolved(entry: Entry, reason: str, snapshot_id: str) -> dict[str, Any]:
    return seal_record(
        {
            "type": "unresolved_mapping",
            "stage": IMPORT_STAGE,
            "mapping_id": entry.mapping_id,
            "decision_id": entry.decision_id,
            "reason": reason,
            "document": {"path": entry.document_path, "sha256": entry.document_sha256},
            "span": {
                "start_byte": entry.start_byte,
                "end_byte": entry.end_byte,
                "sha256": entry.span_sha256,
            },
            "target": {
                "entity_id": entry.entity_id,
                "path": entry.target_path,
                "kind": entry.target_kind,
                "name": entry.target_name,
            },
            "snapshot_id": snapshot_id,
        }
    )


def _span(facts: Snapshot, entry: Entry) -> tuple[bytes, str | None]:
    """The span bytes, or the import reason that stops the mapping at the document or span."""
    digest = facts.indexed_hash(entry.document_path)
    if digest is None:
        return b"", "document_not_in_snapshot"
    if digest != entry.document_sha256:
        return b"", "document_digest_mismatch"
    data = facts.document(entry.document_path, digest)
    if not entry.start_byte < entry.end_byte <= len(data):
        return b"", "span_out_of_range"
    chunk = data[entry.start_byte : entry.end_byte]
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return b"", "span_not_utf8"
    if hashlib.sha256(chunk).hexdigest() != entry.span_sha256:
        return b"", "span_digest_mismatch"
    return data, None


def _target(facts: Snapshot, entry: Entry) -> tuple[dict[str, Any] | None, str | None]:
    """The stored entity row and its class, or the import reason that stops the mapping."""
    row = facts.entity(entry.entity_id)
    if row is None or facts.indexed_hash(row["path"]) is None:
        return None, "target_not_found"
    if _target_class(row) is None:
        return None, "target_kind_unsupported"
    declared = (entry.target_path, entry.target_kind, entry.target_name)
    if (row["path"], row["kind"], row["name"]) != declared:
        return None, "target_descriptor_mismatch"
    return row, None


def _resolve_one(facts: Snapshot, snapshot_id: str, generation_id: str, entry: Entry) -> dict:
    data, reason = _span(facts, entry)
    if reason is not None:
        return _unresolved(entry, reason, snapshot_id)
    row, reason = _target(facts, entry)
    if row is None:
        return _unresolved(entry, str(reason), snapshot_id)
    target_class = str(_target_class(row))
    occurrence: Mapping[str, Any] | None = None
    if target_class == "file":
        if row["id"] != ev.file_entity(row["path"], row["language"])["id"]:
            return _unresolved(entry, "target_definition_ambiguous", snapshot_id)
    else:
        occurrence = facts.definition(row)
        if occurrence is None:
            return _unresolved(entry, "target_definition_ambiguous", snapshot_id)
    start, end = entry.start_byte, entry.end_byte
    return seal_record(
        {
            "type": "rationale_mapping",
            "mapping_id": entry.mapping_id,
            "decision_id": entry.decision_id,
            "document": {"path": entry.document_path, "sha256": entry.document_sha256},
            "span": {
                "start_byte": start,
                "end_byte": end,
                "start_line": data.count(b"\n", 0, start) + 1,
                "end_line": data.count(b"\n", 0, end - 1) + 1,
                "sha256": entry.span_sha256,
            },
            "target": {
                "entity_id": row["id"],
                "target_class": target_class,
                "path": row["path"],
                "kind": row["kind"],
                "name": row["name"],
                "language": row["language"],
                "owners": row["owners"],
                "ordinal": row["ordinal"],
                "occurrence_id": None if occurrence is None else occurrence.get("id"),
                "start_byte": None if occurrence is None else occurrence.get("start_byte"),
                "end_byte": None if occurrence is None else occurrence.get("end_byte"),
                "source_sha256": facts.indexed_hash(row["path"]),
            },
            "snapshot_id": snapshot_id,
            "generation_id": generation_id,
        }
    )


def _order(record: Mapping[str, Any]) -> tuple[str, str]:
    return (record["decision_id"], record["mapping_id"])


def resolve(
    reader: Any, parsed: ParsedMapping, facts: Snapshot | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(resolved, unresolved) records of every mapping on the mapped snapshot, sorted."""
    facts = Snapshot(reader) if facts is None else facts
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for entry in parsed.entries:
        record = _resolve_one(facts, reader.snapshot_id, reader.generation_id, entry)
        (resolved if record["type"] == "rationale_mapping" else unresolved).append(record)
    return sorted(resolved, key=_order), sorted(unresolved, key=_order)


def build_overlay(
    reader: Any,
    mapping_digest: str,
    resolved: Sequence[dict[str, Any]],
    unresolved: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    rest = {
        "format": OVERLAY_FORMAT,
        "producer": dict(PRODUCER),
        "link_kind": LINK_KIND,
        "mapping_digest": mapping_digest,
        "snapshot": {"snapshot_id": reader.snapshot_id, "generation_id": reader.generation_id},
        "counts": {
            "mappings": len(resolved) + len(unresolved),
            "resolved": len(resolved),
            "unresolved": len(unresolved),
        },
        "resolved": list(resolved),
        "unresolved": list(unresolved),
        "limitations": list(LIMITATIONS),
    }
    return {"id": sha_text(OVERLAY_FORMAT + ":" + ev.canonical(rest)), **rest}


def sealed_overlay(
    reader: Any,
    parsed: ParsedMapping,
    resolved: Sequence[dict[str, Any]],
    unresolved: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """The overlay, checked by `validate_overlay` before it leaves the library."""
    overlay = build_overlay(reader, parsed.digest, resolved, unresolved)
    validate_overlay(overlay)
    return overlay


def map_rationale(
    raw: bytes, side: Side, limits: Limits | None = None, parsed: ParsedMapping | None = None
) -> dict[str, Any]:
    """Resolve the mapping on the mapped snapshot of `side` and return the overlay."""
    limits = normalize_limits() if limits is None else limits
    parsed = parse_mapping(raw, limits) if parsed is None else parsed
    with ExitStack() as stack:
        reader = open_mapped(stack, side, parsed.snapshot_id)
        resolved, unresolved = resolve(reader, parsed)
        overlay = sealed_overlay(reader, parsed, resolved, unresolved)
    if len(canonical_bytes(overlay)) > limits.max_output_bytes:
        raise RationaleRejected("output_too_large")
    return overlay


# ---- validation ---------------------------------------------------------------------------------


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RationaleRejected(reason)


def keys_closed(value: Any, keys: Sequence[str], reason: str) -> dict[str, Any]:
    require(isinstance(value, dict) and set(value) == set(keys), reason)
    return dict(value)


def is_label(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(_LABEL, value) is not None


def is_count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _MAX_INT


def no_relation_keys(value: Any, reason: str) -> None:
    """No `relation` key anywhere: this document is not a graph relation."""
    pending: list[Any] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            require("relation" not in item, reason)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _check_resolved(item: Any, snapshot: Mapping[str, Any]) -> None:
    bad = "invalid_overlay"
    record = keys_closed(item, RESOLVED_KEYS, bad)
    require(record["type"] == "rationale_mapping", bad)
    require(is_label(record["mapping_id"]) and is_label(record["decision_id"]), bad)
    document = keys_closed(record["document"], DOCUMENT_KEYS, bad)
    require(ev.relative_path(document["path"]) and ev.hash_value(document["sha256"]), bad)
    span = keys_closed(record["span"], RESOLVED_SPAN_KEYS, bad)
    require(all(is_count(span[key]) for key in RESOLVED_SPAN_KEYS[:4]), bad)
    require(span["start_byte"] < span["end_byte"], bad)
    require(1 <= span["start_line"] <= span["end_line"], bad)
    require(ev.hash_value(span["sha256"]), bad)
    target = keys_closed(record["target"], RESOLVED_TARGET_KEYS, bad)
    require(ev.hash_value(target["entity_id"]), bad)
    require(target["target_class"] in TARGET_CLASSES, bad)
    require(ev.relative_path(target["path"]), bad)
    require(isinstance(target["kind"], str) and isinstance(target["language"], str), bad)
    require(isinstance(target["owners"], list) and is_count(target["ordinal"]), bad)
    require(ev.hash_value(target["source_sha256"]), bad)
    fields = (target["occurrence_id"], target["start_byte"], target["end_byte"])
    if target["target_class"] == "file":
        require(all(value is None for value in fields), bad)
    else:
        require(ev.hash_value(fields[0]) and is_count(fields[1]) and is_count(fields[2]), bad)
    require(
        record["snapshot_id"] == snapshot["snapshot_id"]
        and record["generation_id"] == snapshot["generation_id"],
        bad,
    )
    require(seal_record(record)["id"] == record["id"], bad)


def _check_unresolved(item: Any, snapshot_id: str) -> None:
    bad = "invalid_overlay"
    record = keys_closed(item, UNRESOLVED_KEYS, bad)
    require(record["type"] == "unresolved_mapping" and record["stage"] == IMPORT_STAGE, bad)
    require(is_label(record["mapping_id"]) and is_label(record["decision_id"]), bad)
    require(record["reason"] in IMPORT_REASONS, bad)
    document = keys_closed(record["document"], DOCUMENT_KEYS, bad)
    require(ev.relative_path(document["path"]) and ev.hash_value(document["sha256"]), bad)
    span = keys_closed(record["span"], SPAN_KEYS, bad)
    require(is_count(span["start_byte"]) and is_count(span["end_byte"]), bad)
    require(ev.hash_value(span["sha256"]), bad)
    target = keys_closed(record["target"], TARGET_KEYS, bad)
    require(ev.hash_value(target["entity_id"]) and ev.relative_path(target["path"]), bad)
    require(isinstance(target["kind"], str) and isinstance(target["name"], str), bad)
    require(record["snapshot_id"] == snapshot_id, bad)
    require(seal_record(record)["id"] == record["id"], bad)


def validate_overlay(overlay: Any) -> None:
    """Raise `RationaleRejected("invalid_overlay")` unless `overlay` is exactly what we build."""
    bad = "invalid_overlay"
    top = keys_closed(overlay, OVERLAY_KEYS, bad)
    no_relation_keys(top, bad)
    require(top["format"] == OVERLAY_FORMAT, bad)
    require(top["producer"] == PRODUCER and top["link_kind"] == LINK_KIND, bad)
    require(ev.hash_value(top["mapping_digest"]), bad)
    snapshot = keys_closed(top["snapshot"], SNAPSHOT_KEYS, bad)
    require(all(ev.hash_value(value) for value in snapshot.values()), bad)
    counts = keys_closed(top["counts"], COUNT_KEYS, bad)
    resolved, unresolved = top["resolved"], top["unresolved"]
    require(isinstance(resolved, list) and isinstance(unresolved, list), bad)
    require(
        counts
        == {
            "mappings": len(resolved) + len(unresolved),
            "resolved": len(resolved),
            "unresolved": len(unresolved),
        },
        bad,
    )
    for item in resolved:
        _check_resolved(item, snapshot)
    for item in unresolved:
        _check_unresolved(item, snapshot["snapshot_id"])
    for group in (resolved, unresolved):
        require([_order(item) for item in group] == sorted(_order(item) for item in group), bad)
    labels = [item["mapping_id"] for item in (*resolved, *unresolved)]
    require(len(set(labels)) == len(labels), bad)
    require(top["limitations"] == list(LIMITATIONS), bad)
    require(overlay_identity(top) == top["id"], bad)


# ---- the summary --------------------------------------------------------------------------------


def summary_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """What stdout carries: the document's id, format and counts (never its records)."""
    command = "rationale-map" if document["format"] == OVERLAY_FORMAT else "rationale-drift"
    return {
        "format": SUMMARY_FORMAT,
        "command": command,
        "document_format": document["format"],
        "document_id": document["id"],
        "mapping_digest": document["mapping_digest"],
        "counts": dict(document["counts"]),
    }


def validate_summary(summary: Any) -> None:
    bad = "invalid_overlay"
    body = keys_closed(summary, SUMMARY_KEYS, bad)
    require(body["format"] == SUMMARY_FORMAT and body["command"] in COMMANDS, bad)
    expected = OVERLAY_FORMAT if body["command"] == "rationale-map" else DRIFT_FORMAT
    require(body["document_format"] == expected, bad)
    require(ev.hash_value(body["document_id"]) and ev.hash_value(body["mapping_digest"]), bad)
    keys = COUNT_KEYS if body["command"] == "rationale-map" else DRIFT_COUNT_KEYS
    counts = keys_closed(body["counts"], keys, bad)
    require(all(is_count(value) for value in counts.values()), bad)
