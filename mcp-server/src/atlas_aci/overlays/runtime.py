"""Bind runtime observations from one OTLP JSON artifact to the active snapshot (ACI-S27).

The result is the standalone, content-addressed `atlas-runtime-overlay.v1`: it is built from a
READ-ONLY lease of the active graph generation and written only to the operator's output path.
It is never part of the graph, so importing it cannot change the snapshot id it is bound to.

Coverage is `observed_sample` everywhere: the spans are whatever one run happened to emit.
`observed_parent_of` records span nesting as observed; it is not a call relation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, suppress
from itertools import pairwise
from pathlib import Path
from typing import Any

from atlas_aci.importers import otel_json as otel
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import DiffRejected, Side
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-runtime-overlay.v1"
PATHS_FORMAT = "atlas-runtime-paths.v1"
SUMMARY_FORMAT = "atlas-otel-import-summary.v1"
EVIDENCE_COVERAGE = "observed_sample"
COMPLETENESS = "unknown"
SAMPLING = "not_declared"
SEMANTICS = "observed_span_nesting"
RELATION = "observed_parent_of"
ATTESTATION = "operator_declared"
SCHEMA_EPOCHS = (8, 9)

LIMITATIONS = (
    "span_nesting_is_observed_not_a_call_relation",
    "observations_are_a_sample_and_completeness_is_unknown",
    "one_declared_build_per_artifact",
    "build_declaration_is_manifest_only",
    "producer_and_build_fields_are_operator_declared",
    "legacy_code_attribute_spellings_are_not_read",
    "single_otlp_json_traces_document_only",
    "metrics_and_logs_are_not_imported",
    "linkage_requires_the_active_graph_snapshot",
    "symbol_is_the_innermost_definition_enclosing_the_line",
    "overlay_is_written_outside_the_graph",
    "static_impact_output_is_unchanged",
    "span_timing_is_copied_not_interpreted",
    "not_exposed_over_mcp",
)
UNLINKED_REASONS = (
    "build_mismatch",
    "missing_source_location",
    "malformed_source_location",
    "path_outside_root",
    "file_not_in_snapshot",
    "file_not_indexed",
    "line_out_of_range",
)
CLASSIFICATIONS = ("linked_symbol", "linked_file", *("unlinked_" + r for r in UNLINKED_REASONS))
LOCATION_COUNTS = ("present", "missing_source_location", "malformed_source_location")
OVERLAY_REJECTIONS = (
    "index_unavailable",
    "source_snapshot_unavailable",
    "index_upgrade_required",
    "invalid_overlay",
)
USAGE_REASONS = ("output_not_outside_graph", "output_equals_input", "invalid_runtime_path_limit")
STARTS_AT = ("trace_root", "parent_not_in_artifact")
PATH_LIMITS = {"max_paths": (1, 100000, 1000), "max_path_length": (1, 4096, 256)}

OBSERVATIONS_SQL = "SELECT value FROM manifest WHERE key='source_observations'"
ENTITY_SQL = "SELECT 1 FROM entities WHERE id=?"
SYMBOL_SQL = (
    "SELECT name, kind, line_start, line_end, entity_id FROM symbols "
    "WHERE path=? AND line_start<=? AND line_end>=? AND kind NOT LIKE 'scip\\_%' ESCAPE '\\' "
    "ORDER BY (line_end-line_start) ASC, line_start DESC, name, kind, entity_id LIMIT 1"
)
TEMP_PREFIX = ".atlas-runtime-overlay-"

_RUN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_BUILD = re.compile(r"[A-Za-z0-9._:+-]{1,128}")
_TEXT = re.compile(r"[ -~]{1,128}")
_TRACE = re.compile(r"[0-9a-f]{32}")
_SPAN = re.compile(r"[0-9a-f]{16}")
_DECIMAL = re.compile(r"0|[1-9][0-9]{0,19}")

TOP_KEYS = (
    "format",
    "overlay_id",
    "importer",
    "run",
    "artifact",
    "manifest",
    "producer",
    "build",
    "source_roots",
    "snapshot",
    "coverage",
    "summary",
    "limitations",
    "observations",
    "relations",
)
OBSERVATION_KEYS = (
    "id",
    "type",
    "run_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "artifact_sha256",
    "snapshot_id",
    "start_time_unix_nano",
    "end_time_unix_nano",
    "status_code",
    "source",
    "linkage",
    "evidence_coverage",
)
RELATION_KEYS = (
    "id",
    "type",
    "relation",
    "trace_id",
    "parent_id",
    "child_id",
    "parent_span_id",
    "child_span_id",
    "semantics",
    "evidence_coverage",
)
LINKAGE_KEYS = ("classification", "path", "file_entity_id", "symbol")
SYMBOL_KEYS = ("name", "kind", "line_start", "line_end", "entity_id")
SUMMARY_KEYS = (
    "evidence_coverage",
    "traces_total",
    "observations_total",
    "linked_symbol",
    "linked_file",
    "unlinked_total",
    "unlinked_by_reason",
    "source_locations",
    "relations_total",
    "root_spans",
    "parent_not_in_artifact",
    "legacy_source_attributes_ignored",
)
PATHS_KEYS = (
    "format",
    "overlay_id",
    "run_id",
    "snapshot_id",
    "evidence_coverage",
    "completeness",
    "semantics",
    "limits",
    "paths",
    "paths_total",
    "paths_truncated",
    "limitations",
)
PATH_ENTRY_KEYS = (
    "trace_id",
    "starts_at",
    "observation_ids",
    "span_ids",
    "truncated",
    "evidence_coverage",
)
SUMMARY_DOCUMENT_KEYS = (
    "format",
    "overlay_id",
    "run_id",
    "artifact_sha256",
    "snapshot",
    "evidence_coverage",
    "summary",
)
IMPORTER = {
    "name": otel.IMPORTER_NAME,
    "version": otel.IMPORTER_VERSION,
    "profile": otel.PROFILE,
}
COVERAGE = {
    "evidence_coverage": EVIDENCE_COVERAGE,
    "completeness": COMPLETENESS,
    "sampling": SAMPLING,
    "relation_semantics": SEMANTICS,
}


class OverlayRejected(ValueError):
    """The overlay cannot be built or is invalid; `reason` is a closed code."""

    def __init__(self, reason: str):
        super().__init__("overlay_rejected: " + reason)
        self.reason = reason


class OverlayUsageError(ValueError):
    """The invocation is invalid (output location or a path limit); `reason` is a closed code."""

    def __init__(self, reason: str):
        super().__init__("overlay_usage: " + reason)
        self.reason = reason


def _sha(value: Any) -> str:
    return hashlib.sha256(ev.canonical(value).encode("utf-8")).hexdigest()


def canonical_bytes(document: Any) -> bytes:
    """The exact bytes written and printed: canonical JSON plus one newline."""
    return ev.canonical(document).encode("utf-8") + b"\n"


# ---- lease ----------------------------------------------------------------------------------


def open_active(stack: ExitStack, side: Side) -> Any:
    """Lease the active generation read-only (the order of `checker.open_snapshot`)."""
    graph = symbol_diff._open(stack, side)  # DiffUsageError passes through
    try:
        reader = symbol_diff._lease(stack, graph)
    except DiffRejected as exc:
        raise OverlayRejected(exc.reason) from exc
    if (
        reader._closed
        or not reader._validated_lease
        or reader._lease_schema_epoch not in SCHEMA_EPOCHS
    ):
        raise OverlayRejected("index_upgrade_required")
    if reader.snapshot_id is None or reader._source_lease is None:
        raise OverlayRejected("source_snapshot_unavailable")
    return reader


# ---- linkage --------------------------------------------------------------------------------


def line_count(data: bytes) -> int:
    """Number of source lines as the indexer counts them: LF terminated, plus a final partial."""
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


class _Linker:
    """Source facts of the leased snapshot, each fetched at most once per path or per line."""

    def __init__(self, reader: Any):
        self.reader = reader
        self._records: dict[str, dict[str, Any]] | None = None
        self._entities: dict[str, str | None] = {}
        self._counts: dict[str, int] = {}
        self._symbols: dict[tuple[str, int], dict[str, Any] | None] = {}

    def record(self, path: str) -> dict[str, Any] | None:
        if self._records is None:
            row = self.reader.db.execute(OBSERVATIONS_SQL).fetchone()
            if row is None:
                raise OverlayRejected("source_snapshot_unavailable")
            found: dict[str, dict[str, Any]] = {}
            try:
                for item in json.loads(row[0]):
                    # Only `observation` records: `history` records carry no state.
                    if item.get("type") == "observation":
                        found[item["path"]] = item
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise OverlayRejected("source_snapshot_unavailable") from exc
            self._records = found
        return self._records.get(path)

    def entity(self, path: str, language: str) -> str | None:
        if path not in self._entities:
            identifier = ev.file_entity(path, language)["id"]
            present = self.reader.db.execute(ENTITY_SQL, (identifier,)).fetchone()
            self._entities[path] = identifier if present is not None else None
        return self._entities[path]

    def lines(self, path: str, source_sha256: Any) -> int:
        if path not in self._counts:
            try:
                data = self.reader._source_lease.read(path, source_sha256)
            except (ValueError, OSError, RuntimeError, KeyError) as exc:
                raise OverlayRejected("source_snapshot_unavailable") from exc
            self._counts[path] = line_count(data)
        return self._counts[path]

    def symbol(self, path: str, line: int) -> dict[str, Any] | None:
        key = (path, line)
        if key not in self._symbols:
            try:
                row = self.reader.db.execute(SYMBOL_SQL, (path, line, line)).fetchone()
            except sqlite3.OperationalError as exc:
                raise OverlayRejected("index_upgrade_required") from exc
            self._symbols[key] = (
                None
                if row is None
                else {
                    "name": row[0],
                    "kind": row[1],
                    "line_start": row[2],
                    "line_end": row[3],
                    "entity_id": row[4],
                }
            )
        return self._symbols[key]

    def link(self, span: otel.Span, roots: Sequence[Mapping[str, str]]) -> dict[str, Any]:
        path = otel.map_source_path(span.declared_path, roots)
        if path is None:
            return _unlinked("path_outside_root")
        if path not in self.reader._source_lease.current:
            return _unlinked("file_not_in_snapshot")
        record = self.record(path)
        entity = None
        if record is not None and record.get("state") == "indexed":
            entity = self.entity(path, record["language"])
        if record is None or entity is None:
            return _unlinked("file_not_indexed")
        symbol = None
        if span.line is not None:
            if span.line > self.lines(path, record["observed_source_sha256"]):
                return _unlinked("line_out_of_range")
            symbol = self.symbol(path, span.line)
        return {
            "classification": "linked_file" if symbol is None else "linked_symbol",
            "path": path,
            "file_entity_id": entity,
            "symbol": symbol,
        }


def _unlinked(reason: str) -> dict[str, Any]:
    return {
        "classification": "unlinked_" + reason,
        "path": None,
        "file_entity_id": None,
        "symbol": None,
    }


# ---- overlay --------------------------------------------------------------------------------


def _summary(
    observations: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    spans: Sequence[otel.Span],
) -> dict[str, Any]:
    by_reason = {reason: 0 for reason in UNLINKED_REASONS}
    linked_symbol = linked_file = 0
    for item in observations:
        name = item["linkage"]["classification"]
        if name == "linked_symbol":
            linked_symbol += 1
        elif name == "linked_file":
            linked_file += 1
        else:
            by_reason[name[len("unlinked_") :]] += 1
    states = [span.location_state for span in spans]
    keys = {(span.trace_id, span.span_id) for span in spans}
    return {
        "evidence_coverage": EVIDENCE_COVERAGE,
        "traces_total": len({span.trace_id for span in spans}),
        "observations_total": len(observations),
        "linked_symbol": linked_symbol,
        "linked_file": linked_file,
        "unlinked_total": sum(by_reason.values()),
        "unlinked_by_reason": by_reason,
        "source_locations": {
            "present": states.count("present"),
            "missing_source_location": states.count("missing"),
            "malformed_source_location": states.count("malformed"),
        },
        "relations_total": len(relations),
        "root_spans": sum(1 for span in spans if span.parent_span_id is None),
        "parent_not_in_artifact": sum(
            1
            for span in spans
            if span.parent_span_id is not None and (span.trace_id, span.parent_span_id) not in keys
        ),
        "legacy_source_attributes_ignored": sum(1 for span in spans if span.legacy),
    }


def build_overlay(
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    artifact_meta: Mapping[str, Any],
    spans: Sequence[otel.Span],
    reader: Any,
) -> dict[str, Any]:
    """Classify every span, materialize nesting, and return the validated overlay.

    The build match reads only `reader.snapshot_id`. When it fails, no source fact is read for
    any observation: not a SQL statement and not a source byte.
    """
    run_id = manifest["run"]["id"]
    declared = manifest["build"]["snapshot_id"]
    supplied = reader.snapshot_id
    matched = declared == supplied
    roots = manifest["source_roots"]
    linker = _Linker(reader) if matched else None
    ordered = sorted(spans, key=lambda s: (s.trace_id, s.span_id))
    ids = {
        (s.trace_id, s.span_id): otel.observation_id(run_id, s.trace_id, s.span_id) for s in ordered
    }
    observations: list[dict[str, Any]] = []
    for span in ordered:
        if span.location_state == "missing":
            linkage = _unlinked("missing_source_location")
        elif span.location_state == "malformed":
            linkage = _unlinked("malformed_source_location")
        elif linker is None:
            linkage = _unlinked("build_mismatch")
        else:
            linkage = linker.link(span, roots)
        observations.append(
            {
                "id": ids[(span.trace_id, span.span_id)],
                "type": "runtime_observation",
                "run_id": run_id,
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "artifact_sha256": artifact_meta["sha256"],
                "snapshot_id": supplied if matched else None,
                "start_time_unix_nano": span.start,
                "end_time_unix_nano": span.end,
                "status_code": span.status_code,
                "source": {"declared_path": span.declared_path, "line": span.line},
                "linkage": linkage,
                "evidence_coverage": EVIDENCE_COVERAGE,
            }
        )
    relations = []
    for span in ordered:
        if span.parent_span_id is None:
            continue
        parent = ids.get((span.trace_id, span.parent_span_id))
        if parent is None:
            continue
        child = ids[(span.trace_id, span.span_id)]
        relations.append(
            {
                "id": otel.relation_id(parent, child),
                "type": "runtime_relation",
                "relation": RELATION,
                "trace_id": span.trace_id,
                "parent_id": parent,
                "child_id": child,
                "parent_span_id": span.parent_span_id,
                "child_span_id": span.span_id,
                "semantics": SEMANTICS,
                "evidence_coverage": EVIDENCE_COVERAGE,
            }
        )
    relations.sort(key=lambda r: (r["trace_id"], r["parent_span_id"], r["child_span_id"]))
    rest = {
        "format": FORMAT,
        "importer": dict(IMPORTER),
        "run": {"id": run_id},
        "artifact": {"sha256": artifact_meta["sha256"], "bytes": artifact_meta["bytes"]},
        "manifest": {"format": manifest["format"], "sha256": manifest_sha256},
        "producer": {
            "name": manifest["producer"]["name"],
            "version": manifest["producer"]["version"],
            "attestation": ATTESTATION,
        },
        "build": {"id": manifest["build"]["id"]},
        "source_roots": sorted(
            ({"from": r["from"], "to": r["to"]} for r in roots),
            key=lambda r: (r["from"], r["to"]),
        ),
        "snapshot": {"declared_id": declared, "supplied_id": supplied, "matched": matched},
        "coverage": dict(COVERAGE),
        "summary": _summary(observations, relations, spans),
        "limitations": list(LIMITATIONS),
        "observations": observations,
        "relations": relations,
    }
    overlay = {"overlay_id": _identity(rest), **rest}
    validate_overlay(overlay)
    return overlay


def _identity(rest: Mapping[str, Any]) -> str:
    return hashlib.sha256((FORMAT + ":" + ev.canonical(rest)).encode("utf-8")).hexdigest()


def import_otel(
    side: Side, manifest_raw: bytes, artifact_raw: bytes, limits: otel.Limits | None = None
) -> dict[str, Any]:
    """The full pipeline: manifest, artifact, then the leased snapshot; nothing is written."""
    manifest = otel.parse_manifest(manifest_raw)
    spans = otel.parse_artifact(artifact_raw, manifest, limits or otel.normalize_limits())
    meta = {"sha256": otel.sha256_hex(artifact_raw), "bytes": len(artifact_raw)}
    with ExitStack() as stack:
        reader = open_active(stack, side)
        return build_overlay(manifest, otel.sha256_hex(manifest_raw), meta, spans, reader)


def summary_document(overlay: Mapping[str, Any]) -> dict[str, Any]:
    """The import summary printed by the CLI; it carries the coverage declaration."""
    return {
        "format": SUMMARY_FORMAT,
        "overlay_id": overlay["overlay_id"],
        "run_id": overlay["run"]["id"],
        "artifact_sha256": overlay["artifact"]["sha256"],
        "snapshot": dict(overlay["snapshot"]),
        "evidence_coverage": EVIDENCE_COVERAGE,
        "summary": overlay["summary"],
    }


# ---- validation -----------------------------------------------------------------------------


def _fail() -> Any:
    raise OverlayRejected("invalid_overlay")


def _check(condition: Any) -> None:
    if not condition:
        _fail()


def _exact(value: Any, names: Sequence[str]) -> dict[str, Any]:
    _check(isinstance(value, dict) and set(value) == set(names))
    return value


def _is_int(value: Any, low: int = 0) -> bool:
    return type(value) is int and value >= low


def _is_text(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _check_header(overlay: dict[str, Any]) -> None:
    _check(overlay["format"] == FORMAT)
    _check(ev.hash_value(overlay["overlay_id"]))
    _check(_exact(overlay["importer"], tuple(IMPORTER)) == IMPORTER)
    _check(_is_text(_RUN, _exact(overlay["run"], ("id",))["id"]))
    artifact = _exact(overlay["artifact"], ("sha256", "bytes"))
    _check(ev.hash_value(artifact["sha256"]) and _is_int(artifact["bytes"]))
    manifest = _exact(overlay["manifest"], ("format", "sha256"))
    _check(manifest["format"] == otel.MANIFEST_FORMAT and ev.hash_value(manifest["sha256"]))
    producer = _exact(overlay["producer"], ("name", "version", "attestation"))
    _check(_is_text(_TEXT, producer["name"]) and _is_text(_TEXT, producer["version"]))
    _check(producer["attestation"] == ATTESTATION)
    _check(_is_text(_BUILD, _exact(overlay["build"], ("id",))["id"]))
    roots = overlay["source_roots"]
    _check(isinstance(roots, list) and len(roots) <= otel.MAX_SOURCE_ROOTS)
    for rule in roots:
        _exact(rule, ("from", "to"))
        _check(otel.valid_root_from(rule["from"]) and otel.valid_root_to(rule["to"]))
    _check(roots == sorted(roots, key=lambda r: (r["from"], r["to"])))
    _check(len({r["from"] for r in roots}) == len(roots))
    snapshot = _exact(overlay["snapshot"], ("declared_id", "supplied_id", "matched"))
    _check(ev.hash_value(snapshot["declared_id"]) and ev.hash_value(snapshot["supplied_id"]))
    _check(type(snapshot["matched"]) is bool)
    _check(snapshot["matched"] == (snapshot["declared_id"] == snapshot["supplied_id"]))
    _check(_exact(overlay["coverage"], tuple(COVERAGE)) == COVERAGE)
    _check(overlay["limitations"] == list(LIMITATIONS))


def _check_observation(item: Any, overlay: dict[str, Any]) -> None:
    _exact(item, OBSERVATION_KEYS)
    snapshot = overlay["snapshot"]
    _check(item["type"] == "runtime_observation" and item["evidence_coverage"] == EVIDENCE_COVERAGE)
    _check(item["run_id"] == overlay["run"]["id"])
    _check(_is_text(_TRACE, item["trace_id"]) and _is_text(_SPAN, item["span_id"]))
    _check(item["parent_span_id"] is None or _is_text(_SPAN, item["parent_span_id"]))
    _check(item["artifact_sha256"] == overlay["artifact"]["sha256"])
    _check(item["id"] == otel.observation_id(item["run_id"], item["trace_id"], item["span_id"]))
    _check(_is_text(_DECIMAL, item["start_time_unix_nano"]))
    _check(_is_text(_DECIMAL, item["end_time_unix_nano"]))
    _check(1 <= int(item["start_time_unix_nano"]) <= int(item["end_time_unix_nano"]))
    _check(int(item["end_time_unix_nano"]) <= otel.MAX_UNIX_NANO)
    _check(type(item["status_code"]) is int and item["status_code"] in (0, 1, 2))
    source = _exact(item["source"], ("declared_path", "line"))
    _check(source["declared_path"] is None or isinstance(source["declared_path"], str))
    _check(
        source["line"] is None or (_is_int(source["line"], 1) and source["line"] <= otel.MAX_LINE)
    )
    linkage = _exact(item["linkage"], LINKAGE_KEYS)
    name = linkage["classification"]
    _check(name in CLASSIFICATIONS)
    _check(item["snapshot_id"] == (snapshot["supplied_id"] if snapshot["matched"] else None))
    located = name in ("linked_symbol", "linked_file")
    if name == "unlinked_missing_source_location":
        _check(source["declared_path"] is None)
    elif name != "unlinked_malformed_source_location":
        _check(source["declared_path"] is not None)
    if name == "unlinked_build_mismatch":
        _check(not snapshot["matched"])
    if not snapshot["matched"]:
        _check(
            name
            in (
                "unlinked_build_mismatch",
                "unlinked_missing_source_location",
                "unlinked_malformed_source_location",
            )
        )
    if located:
        _check(snapshot["matched"] and isinstance(linkage["path"], str))
        _check(ev.relative_path(linkage["path"]) and ev.hash_value(linkage["file_entity_id"]))
    else:
        _check(linkage["path"] is None and linkage["file_entity_id"] is None)
        _check(linkage["symbol"] is None)
    if name == "linked_symbol":
        symbol = _exact(linkage["symbol"], SYMBOL_KEYS)
        _check(isinstance(symbol["name"], str) and isinstance(symbol["kind"], str))
        _check(_is_int(symbol["line_start"], 1) and _is_int(symbol["line_end"], 1))
        _check(
            symbol["line_start"] <= source["line"] <= symbol["line_end"]
            if source["line"]
            else False
        )
        _check(symbol["entity_id"] is None or ev.hash_value(symbol["entity_id"]))
    elif name == "linked_file":
        _check(linkage["symbol"] is None)


def _check_relations(overlay: dict[str, Any], keys: dict[tuple[str, str], str]) -> None:
    relations = overlay["relations"]
    _check(isinstance(relations, list))
    expected = {
        (o["trace_id"], o["parent_span_id"], o["span_id"])
        for o in overlay["observations"]
        if o["parent_span_id"] is not None and (o["trace_id"], o["parent_span_id"]) in keys
    }
    found = set()
    previous = None
    for item in relations:
        _exact(item, RELATION_KEYS)
        _check(item["type"] == "runtime_relation" and item["relation"] == RELATION)
        _check(item["semantics"] == SEMANTICS and item["evidence_coverage"] == EVIDENCE_COVERAGE)
        order = (item["trace_id"], item["parent_span_id"], item["child_span_id"])
        _check(previous is None or previous < order)
        previous = order
        _check(keys.get((order[0], order[1])) == item["parent_id"])
        _check(keys.get((order[0], order[2])) == item["child_id"])
        _check(item["id"] == otel.relation_id(item["parent_id"], item["child_id"]))
        found.add(order)
    _check(found == expected and len(found) == len(relations))


def _check_summary(overlay: dict[str, Any]) -> None:
    observations = overlay["observations"]
    summary = _exact(overlay["summary"], SUMMARY_KEYS)
    _check(summary["evidence_coverage"] == EVIDENCE_COVERAGE)
    reasons = _exact(summary["unlinked_by_reason"], UNLINKED_REASONS)
    locations = _exact(summary["source_locations"], LOCATION_COUNTS)
    counts = {name: 0 for name in CLASSIFICATIONS}
    for item in observations:
        counts[item["linkage"]["classification"]] += 1
    total = len(observations)
    _check(summary["observations_total"] == total)
    _check(summary["linked_symbol"] == counts["linked_symbol"])
    _check(summary["linked_file"] == counts["linked_file"])
    _check(all(reasons[r] == counts["unlinked_" + r] for r in UNLINKED_REASONS))
    _check(summary["unlinked_total"] == sum(reasons.values()))
    _check(summary["linked_symbol"] + summary["linked_file"] + summary["unlinked_total"] == total)
    _check(locations["missing_source_location"] == reasons["missing_source_location"])
    _check(locations["malformed_source_location"] == reasons["malformed_source_location"])
    _check(sum(locations.values()) == total)
    _check(all(_is_int(v) for v in (*locations.values(), *reasons.values())))
    _check(summary["traces_total"] == len({o["trace_id"] for o in observations}))
    _check(summary["relations_total"] == len(overlay["relations"]))
    keys = {(o["trace_id"], o["span_id"]) for o in observations}
    _check(summary["root_spans"] == sum(1 for o in observations if o["parent_span_id"] is None))
    _check(
        summary["parent_not_in_artifact"]
        == sum(
            1
            for o in observations
            if o["parent_span_id"] is not None and (o["trace_id"], o["parent_span_id"]) not in keys
        )
    )
    legacy = summary["legacy_source_attributes_ignored"]
    _check(_is_int(legacy) and legacy <= total)
    _check(all(_is_int(summary[k]) for k in SUMMARY_KEYS[1:6] if k != "unlinked_by_reason"))


def _check_acyclic(observations: list[dict[str, Any]]) -> None:
    parents = {(o["trace_id"], o["span_id"]): o["parent_span_id"] for o in observations}
    state: dict[tuple[str, str], int] = {}
    for start in parents:
        if start in state:
            continue
        walked = []
        current: tuple[str, str] | None = start
        while current is not None and current in parents and current not in state:
            state[current] = 1
            walked.append(current)
            up = parents[current]
            current = None if up is None else (current[0], up)
        _check(current is None or state.get(current) != 1)
        for node in walked:
            state[node] = 2


def validate_overlay(overlay: Any) -> None:
    """Recompute everything checkable from the overlay itself; any failure is `invalid_overlay`."""
    try:
        _exact(overlay, TOP_KEYS)
        _check_header(overlay)
        observations = overlay["observations"]
        _check(isinstance(observations, list))
        for item in observations:
            _check_observation(item, overlay)
        order = [(o["trace_id"], o["span_id"]) for o in observations]
        _check(all(a < b for a, b in pairwise(order)))
        _check_summary(overlay)
        _check_acyclic(observations)
        _check_relations(overlay, {(o["trace_id"], o["span_id"]): o["id"] for o in observations})
        rest = {k: v for k, v in overlay.items() if k != "overlay_id"}
        _check(overlay["overlay_id"] == _identity(rest))
    except OverlayRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        raise OverlayRejected("invalid_overlay") from exc


# ---- runtime paths --------------------------------------------------------------------------


def _path_limit(name: str, value: Any) -> int:
    low, high, _default = PATH_LIMITS[name]
    if type(value) is not int or not low <= value <= high:
        raise OverlayUsageError("invalid_runtime_path_limit")
    return value


def runtime_paths(
    overlay: Mapping[str, Any],
    max_paths: int = PATH_LIMITS["max_paths"][2],
    max_path_length: int = PATH_LIMITS["max_path_length"][2],
) -> dict[str, Any]:
    """Observed span-nesting chains, one per leaf, root to leaf; never a call path.

    `max_path_length` counts nodes (spans) per path. `paths_total` is the number of leaves of
    the in-artifact forest regardless of either limit; `paths_truncated` is true when fewer paths
    were emitted than that, or when any emitted path was cut at `max_path_length`.
    """
    limit_paths = _path_limit("max_paths", max_paths)
    limit_length = _path_limit("max_path_length", max_path_length)
    validate_overlay(overlay)
    by_id = {o["id"]: o for o in overlay["observations"]}
    children: dict[str, list[dict[str, Any]]] = {}
    has_parent = set()
    for relation in overlay["relations"]:
        children.setdefault(relation["parent_id"], []).append(by_id[relation["child_id"]])
        has_parent.add(relation["child_id"])
    for group in children.values():
        group.sort(key=lambda o: o["span_id"])
    starts = [o for o in overlay["observations"] if o["id"] not in has_parent]
    total = sum(1 for o in overlay["observations"] if o["id"] not in children)
    paths: list[dict[str, Any]] = []
    for start in starts:
        if len(paths) >= limit_paths:
            break
        origin = "trace_root" if start["parent_span_id"] is None else "parent_not_in_artifact"
        walk = [start]
        frames = [iter(children.get(start["id"], ()))]
        leaf = not children.get(start["id"])
        cut = len(walk) >= limit_length and not leaf
        if leaf or cut:
            paths.append(_entry(walk, origin, cut))
            continue
        while frames and len(paths) < limit_paths:
            child = next(frames[-1], None)
            if child is None:
                frames.pop()
                walk.pop()
                continue
            walk.append(child)
            kids = children.get(child["id"], ())
            if not kids or len(walk) >= limit_length:
                paths.append(_entry(walk, origin, bool(kids)))
                walk.pop()
            else:
                frames.append(iter(kids))
    result = {
        "format": PATHS_FORMAT,
        "overlay_id": overlay["overlay_id"],
        "run_id": overlay["run"]["id"],
        "snapshot_id": overlay["snapshot"]["supplied_id"]
        if overlay["snapshot"]["matched"]
        else None,
        "evidence_coverage": EVIDENCE_COVERAGE,
        "completeness": COMPLETENESS,
        "semantics": SEMANTICS,
        "limits": {"max_paths": limit_paths, "max_path_length": limit_length},
        "paths": paths,
        "paths_total": total,
        "paths_truncated": len(paths) < total or any(p["truncated"] for p in paths),
        "limitations": list(LIMITATIONS),
    }
    validate_runtime_paths(result)
    return result


def _entry(walk: list[dict[str, Any]], origin: str, truncated: bool) -> dict[str, Any]:
    return {
        "trace_id": walk[0]["trace_id"],
        "starts_at": origin,
        "observation_ids": [o["id"] for o in walk],
        "span_ids": [o["span_id"] for o in walk],
        "truncated": truncated,
        "evidence_coverage": EVIDENCE_COVERAGE,
    }


def validate_runtime_paths(result: Any) -> None:
    """Closed keys, closed enums and the coverage declaration, at the top level and per path."""
    try:
        _exact(result, PATHS_KEYS)
        _check(result["format"] == PATHS_FORMAT and ev.hash_value(result["overlay_id"]))
        _check(_is_text(_RUN, result["run_id"]))
        _check(result["snapshot_id"] is None or ev.hash_value(result["snapshot_id"]))
        _check(result["evidence_coverage"] == EVIDENCE_COVERAGE)
        _check(result["completeness"] == COMPLETENESS and result["semantics"] == SEMANTICS)
        limits = _exact(result["limits"], tuple(PATH_LIMITS))
        _check(all(_is_int(v, 1) for v in limits.values()))
        _check(_is_int(result["paths_total"]) and type(result["paths_truncated"]) is bool)
        _check(result["limitations"] == list(LIMITATIONS))
        _check(isinstance(result["paths"], list) and len(result["paths"]) <= limits["max_paths"])
        for entry in result["paths"]:
            _exact(entry, PATH_ENTRY_KEYS)
            _check(entry["evidence_coverage"] == EVIDENCE_COVERAGE)
            _check(entry["starts_at"] in STARTS_AT and type(entry["truncated"]) is bool)
            _check(_is_text(_TRACE, entry["trace_id"]))
            nodes, spans = entry["observation_ids"], entry["span_ids"]
            _check(isinstance(nodes, list) and isinstance(spans, list))
            _check(1 <= len(nodes) == len(spans) <= limits["max_path_length"])
            _check(all(ev.hash_value(n) for n in nodes) and all(_is_text(_SPAN, s) for s in spans))
    except OverlayRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise OverlayRejected("invalid_overlay") from exc


# ---- output ---------------------------------------------------------------------------------


def _identity_of(path: Path | str, follow: bool = True) -> tuple[int, int] | None:
    try:
        info = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def resolve_output(output: Path, protected: Sequence[Path], inputs: Sequence[Path]) -> Path:
    """The write target: its parent resolved once, checked against the roots it must avoid.

    "Inside" means the root itself or any descendant, decided by walking the target's ancestors
    and comparing (st_dev, st_ino) with each protected root, so a case variant or a symlinked
    parent cannot slip past a path-prefix comparison. Nothing is created.
    """
    parent = Path(os.path.realpath(output.parent))
    target = parent / output.name
    roots = {i for i in (_identity_of(p) for p in protected) if i is not None}
    chain: list[tuple[int, int] | None] = [_identity_of(target, follow=False)]
    walker = parent
    while True:
        chain.append(_identity_of(walker))
        if walker.parent == walker:
            break
        walker = walker.parent
    if any(item in roots for item in chain if item is not None):
        raise OverlayUsageError("output_not_outside_graph")
    own = _identity_of(target)
    named = {os.path.realpath(p) for p in inputs}
    if str(target) in named or (own is not None and any(own == _identity_of(p) for p in inputs)):
        raise OverlayUsageError("output_equals_input")
    return target


def write_atomic(target: Path, data: bytes) -> None:
    """Write beside `target` (same directory) and `os.replace` it; no partial output remains."""
    descriptor, name = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise
