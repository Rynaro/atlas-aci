"""ACI-S35: verified test-outcome events from one pytest JUnit artifact and one retrieval record.

An operator claims that a retrieved result was exercised by a test that passed or failed. The
claim becomes a `verified_artifact` event only when the JUnit artifact (digest, size, declared run
identity), the exact S18 retrieval record and the leased snapshot all agree with it. Producer and
run are operator-declared: the digest proves bytes, not that a run occurred. Anything else is
rejected as a whole (all or nothing) with a closed reason; nothing is written by this module.

The artifact is read by the standard library only, after a byte pre-scan that removes every
construct (DTD, entity definition, comment, CDATA, processing instruction) that could make the
outcome depend on the parser build. Regular expressions use explicit ASCII classes and
`re.fullmatch` only; nothing recurses.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS
from atlas_aci.cross_repo import revision_vector as vector
from atlas_aci.overlays import runtime
from atlas_aci.protocol.graph import validate_context
from atlas_aci.query.diff import DiffUsageError, Side
from atlas_aci.storage import evidence as ev

ENVELOPE_FORMAT = "atlas-feedback-envelope.v1"
STORE_FORMAT = "atlas-feedback-events.v1"
SUMMARY_FORMAT = "atlas-feedback-import-summary.v1"
PROFILE = "atlas-junit-xml-profile.v1"
SELECTOR_KIND = "pytest_nodeid"
ATTESTATION = runtime.ATTESTATION
PRODUCER = {"name": "atlas-aci feedback-import", "version": "atlas-feedback-import.v1"}
COMMAND = "feedback-import"

EVENT_STATUSES = ("verified_artifact",)
ANNOTATION_STATUSES = ("unverified_annotation",)
ANNOTATION_KINDS = ("useful", "dead_end")
OUTCOMES = ("passed", "failed")
IMPORT_REJECTIONS = (
    "envelope_too_large",
    "record_too_large",
    "artifact_too_large",
    "store_too_large",
    "invalid_envelope",
    "unsupported_envelope_version",
    "too_many_claims",
    "invalid_store",
    "unsupported_store_version",
    "index_unavailable",
    "source_snapshot_unavailable",
    "index_upgrade_required",
    "unverifiable_outcome",
    "unlinked_annotation",
    "conflicting_duplicate",
    "too_many_events",
    "invalid_output",
    "output_too_large",
)
UNVERIFIABLE_DETAILS = (
    "record_invalid",
    "record_id_mismatch",
    "snapshot_mismatch",
    "missing_provenance",
    "unsupported_profile",
    "artifact_size_mismatch",
    "artifact_digest_mismatch",
    "run_identity_mismatch",
    "artifact_invalid",
    "result_not_in_record",
    "result_source_unavailable",
    "source_not_indexed",
    "source_fingerprint_mismatch",
    "test_not_in_artifact",
    "outcome_not_objective",
    "claim_disagrees_with_artifact",
)
ANNOTATION_DETAILS = (
    "record_invalid",
    "record_id_mismatch",
    "snapshot_mismatch",
    "result_not_in_record",
    "result_source_unavailable",
    "source_not_indexed",
    "source_fingerprint_mismatch",
)
LIMIT_NAMES = (
    "max_envelope_bytes",
    "max_record_bytes",
    "max_artifact_bytes",
    "max_store_bytes",
    "max_claims",
    "max_testcases",
    "max_events",
    "max_request_bytes",
    "max_output_bytes",
)
USAGE_NAMES = (
    *LIMIT_NAMES,
    "output_location",
    "output_equals_input",
    "graph_namespace",
    "store_without_overlay",
    "overlay_without_store",
    "unsupported_policy",
)
LIMITATIONS = (
    "artifact_digest_proves_bytes_not_that_a_run_occurred",
    "producer_and_run_are_operator_declared",
    "result_to_test_linkage_is_operator_declared_not_causal_and_records_are_self_consistent"
    "_not_replayed",
    "only_pytest_junit_xml_passed_and_failed_are_objective",
    "junit_name_collisions_are_not_objective",
    "events_from_another_generation_are_never_active",
    "failed_outcomes_are_recorded_and_not_scored",
    "annotations_are_unverified_and_never_scored",
    "ids_embed_the_absolute_checkout_path_and_are_not_portable",
    "not_written_to_the_graph_and_not_exposed_over_mcp",
    "evaluation_is_a_report_not_a_ranking_claim",
    "store_is_self_consistent_not_authenticated",
    "baseline_is_wall_clock_bounded_by_deadline_ms",
)
# name -> (default, minimum, maximum)
LIMITS: dict[str, tuple[int, int, int]] = {
    "max_envelope_bytes": (1048576, 1, 16777216),
    "max_record_bytes": (16777216, 1, 67108864),
    "max_artifact_bytes": (8388608, 1, 67108864),
    "max_store_bytes": (67108864, 1, 268435456),
    "max_claims": (1000, 1, 10000),
    "max_testcases": (100000, 1, 1000000),
    "max_events": (10000, 1, 100000),
    "max_request_bytes": (65536, 1, 1048576),
    "max_output_bytes": (67108864, 1, 268435456),
}
MAX_XML_DEPTH = 8
MAX_RESULTS = 128
MAX_SOURCE_FILES = 1024
MAX_PATH_CHARS = 4096
MAX_NODEID_CHARS = 1024

DECLARATIONS = (
    b'<?xml version="1.0" encoding="utf-8"?>',
    b'<?xml version="1.0" encoding="UTF-8"?>',
)
_LABEL = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_TEXT = r"[ -~]{1,128}"
_NOTE = r"[ -~]{0,512}"
_NODEID = r"([A-Za-z0-9_/-]{1,512})\.py((?:::[A-Za-z_][A-Za-z0-9_]{0,127}){1,8})(\[[ -~]{0,256}\])?"

ENVELOPE_KEYS = (
    "format",
    "retrieval_record_id",
    "snapshot_id",
    "generation_id",
    "run",
    "artifact",
    "claims",
    "annotations",
)
RUN_KEYS = ("run_id", "producer")
PRODUCER_KEYS = ("name", "version")
ARTIFACT_KEYS = ("profile", "sha256", "bytes")
CLAIM_KEYS = ("claim_id", "selector", "outcome", "results", "source_files")
SELECTOR_KEYS = ("kind", "value")
ANNOTATION_KEYS = ("annotation_id", "kind", "results", "source_files", "note")
STORE_KEYS = ("format", "id", "producer", "counts", "events", "annotations", "limitations")
STORE_COUNT_KEYS = ("events", "annotations")
EVENT_KEYS = (
    "id",
    "type",
    "status",
    "claim",
    "verification",
    "retrieval",
    "run",
    "artifact",
    "snapshot",
    "results",
    "source_fingerprint",
    "dedup_key",
)
CLAIM_RECORD_KEYS = ("selector", "outcome")
VERIFICATION_KEYS = ("profile", "attestation", "derived_outcome", "testcase")
TESTCASE_KEYS = ("classname", "name")
RETRIEVAL_KEYS = ("record_id", "ranker", "record_bundle_ids")
RUN_RECORD_KEYS = ("run_id", "producer")
ARTIFACT_RECORD_KEYS = ("sha256", "bytes")
SNAPSHOT_KEYS = ("snapshot_id", "generation_id")
RESULT_KEYS = ("bundle_id", "position", "sources")
SOURCE_KEYS = ("path", "source_sha256")
ANNOTATION_RECORD_KEYS = (
    "id",
    "type",
    "status",
    "verification",
    "kind",
    "note",
    "retrieval",
    "snapshot",
    "results",
    "source_fingerprint",
    "dedup_key",
)
SUMMARY_KEYS = ("format", "command", "store_format", "store_id", "previous_store_id", "counts")
SUMMARY_COUNT_KEYS = (
    "events",
    "annotations",
    "new_events",
    "new_annotations",
    "duplicate_events",
    "duplicate_annotations",
)


class FeedbackRejected(Exception):
    """The input cannot be imported; `reason` (and `detail`) are closed codes."""

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(reason if detail is None else reason + ": " + detail)
        self.reason = reason
        self.detail = detail


class FeedbackUsageError(ValueError):
    """The invocation is invalid; `what` is a closed name of `USAGE_NAMES`."""

    def __init__(self, what: str):
        super().__init__("feedback_usage: " + what)
        self.what = what


class ArtifactInvalid(ValueError):
    """The artifact is not a document of `atlas-junit-xml-profile.v1`."""


class RecordInvalid(ValueError):
    """The retrieval record is not a valid saved S18 context result."""


# ---- hashing and canonical bytes --------------------------------------------------------------


def digest(domain: str, value: Any) -> str:
    return hashlib.sha256((domain + ":" + ev.canonical(value)).encode("utf-8")).hexdigest()


def canonical_bytes(document: Any) -> bytes:
    """The exact bytes written and printed: canonical JSON plus one newline."""
    return runtime.canonical_bytes(document)


def run_id(
    profile: str, artifact_sha256: str, artifact_bytes: int, producer: Mapping[str, str],
    snapshot_id: str, generation_id: str,
) -> str:  # fmt: skip
    """The declared-run identity: independent of claims, labels and envelope order."""
    return digest(
        "atlas-test-run.v1",
        {
            "artifact": {"profile": profile, "sha256": artifact_sha256, "bytes": artifact_bytes},
            "producer": {"name": producer["name"], "version": producer["version"]},
            "snapshot": {"snapshot_id": snapshot_id, "generation_id": generation_id},
        },
    )


def unit_id(artifact_sha256: str, selector_value: str) -> str:
    """One scoring unit: a distinct artifact and selector."""
    return digest(
        "atlas-feedback-unit.v1", {"artifact_sha256": artifact_sha256, "selector": selector_value}
    )


# ---- limits -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    max_envelope_bytes: int
    max_record_bytes: int
    max_artifact_bytes: int
    max_store_bytes: int
    max_claims: int
    max_testcases: int
    max_events: int
    max_request_bytes: int
    max_output_bytes: int


def normalize_limits(chosen: Mapping[str, Any] | None = None) -> Limits:
    """Defaults for what is not chosen; a bool or an out-of-range value is a usage error."""
    chosen = chosen or {}
    values: dict[str, int] = {}
    for name, (default, low, high) in LIMITS.items():
        value = chosen.get(name)
        if value is None:
            value = default
        if type(value) is not int or not low <= value <= high:
            raise FeedbackUsageError(name)
        values[name] = value
    return Limits(**values)


# ---- small validators --------------------------------------------------------------------------


def _check(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid")


def _closed(value: Any, keys: Sequence[str]) -> dict[str, Any]:
    _check(isinstance(value, dict) and set(value) == set(keys))
    return value  # type: ignore[no-any-return]


def _text(value: Any, pattern: str) -> str:
    _check(isinstance(value, str) and re.fullmatch(pattern, value) is not None)
    return value  # type: ignore[no-any-return]


def _hex(value: Any) -> str:
    _check(ev.hash_value(value))
    return value  # type: ignore[no-any-return]


def _int(value: Any) -> int:
    _check(type(value) is int and 0 <= value <= 2**31 - 1)
    return value  # type: ignore[no-any-return]


def _rel(value: Any) -> str:
    _check(ev.relative_path(value) and len(value) <= MAX_PATH_CHARS)
    return value  # type: ignore[no-any-return]


def valid_nodeid(value: Any) -> bool:
    """A pytest node id of the supported shape: a plain path, `.py`, 1..8 names, optional ids."""
    if not isinstance(value, str) or len(value) > MAX_NODEID_CHARS:
        return False
    match = re.fullmatch(_NODEID, value)
    return match is not None and ev.relative_path(match.group(1) + ".py")


def nodeid_pair(value: str) -> tuple[str, str]:
    """The (classname, name) pytest writes into the JUnit testcase for a supported node id."""
    _check(valid_nodeid(value))
    head, bracket, params = value.partition("[")
    parts = head.split("::")
    classname = ".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]])
    return classname, parts[-1] + bracket + params


def _encodable(document: Any) -> bool:
    """Every string (keys included) strict-encodes to UTF-8; iterative, so no recursion."""
    stack = [document]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                return False
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return True


def _results(value: Any) -> list[str]:
    _check(isinstance(value, list) and 1 <= len(value) <= MAX_RESULTS)
    _check(all(ev.hash_value(v) for v in value) and len(set(value)) == len(value))
    return list(value)


def _source_files(value: Any) -> dict[str, str]:
    _check(isinstance(value, dict) and len(value) <= MAX_SOURCE_FILES)
    for key, sha in value.items():
        _rel(key)
        _hex(sha)
    return dict(value)


def _producer(value: Any) -> dict[str, str]:
    producer = _closed(value, PRODUCER_KEYS)
    return {"name": _text(producer["name"], _TEXT), "version": _text(producer["version"], _TEXT)}


# ---- D1: the envelope --------------------------------------------------------------------------


def parse_envelope(raw: bytes, limits: Limits) -> dict[str, Any]:
    """The strict `atlas-feedback-envelope.v1`; claims and annotations are sorted by label."""
    if len(raw) > limits.max_envelope_bytes:
        raise FeedbackRejected("envelope_too_large")
    try:
        document = vector.load_strict(raw, numbers=True)
    except ValueError:
        raise FeedbackRejected("invalid_envelope") from None
    if not isinstance(document, dict) or not isinstance(document.get("format"), str):
        raise FeedbackRejected("invalid_envelope")
    if document["format"] != ENVELOPE_FORMAT:
        raise FeedbackRejected("unsupported_envelope_version")
    try:
        _check(_encodable(document))
        _closed(document, ENVELOPE_KEYS)
        claims, annotations = document["claims"], document["annotations"]
        _check(isinstance(claims, list) and isinstance(annotations, list))
        _check(len(claims) + len(annotations) >= 1)
    except ValueError:
        raise FeedbackRejected("invalid_envelope") from None
    if len(claims) + len(annotations) > limits.max_claims:
        raise FeedbackRejected("too_many_claims")
    try:
        parsed: dict[str, Any] = {
            "format": ENVELOPE_FORMAT,
            "retrieval_record_id": _hex(document["retrieval_record_id"]),
            "snapshot_id": _hex(document["snapshot_id"]),
            "generation_id": _hex(document["generation_id"]),
            "run": None,
            "artifact": None,
        }
        if document["run"] is not None:
            run = _closed(document["run"], RUN_KEYS)
            parsed["run"] = {"run_id": _hex(run["run_id"]), "producer": _producer(run["producer"])}
        if document["artifact"] is not None:
            artifact = _closed(document["artifact"], ARTIFACT_KEYS)
            parsed["artifact"] = {
                "profile": _text(artifact["profile"], _TEXT),
                "sha256": _hex(artifact["sha256"]),
                "bytes": _int(artifact["bytes"]),
            }
        parsed["claims"] = sorted(
            (_claim(item) for item in claims), key=lambda item: item["claim_id"]
        )
        parsed["annotations"] = sorted(
            (_annotation(item) for item in annotations), key=lambda item: item["annotation_id"]
        )
        _check(len({c["claim_id"] for c in parsed["claims"]}) == len(parsed["claims"]))
        _check(
            len({a["annotation_id"] for a in parsed["annotations"]}) == len(parsed["annotations"])
        )
    except (ValueError, KeyError, TypeError):
        raise FeedbackRejected("invalid_envelope") from None
    return parsed


def _claim(value: Any) -> dict[str, Any]:
    item = _closed(value, CLAIM_KEYS)
    selector = None
    if item["selector"] is not None:
        chosen = _closed(item["selector"], SELECTOR_KEYS)
        _check(chosen["kind"] == SELECTOR_KIND and valid_nodeid(chosen["value"]))
        selector = {"kind": SELECTOR_KIND, "value": chosen["value"]}
    _check(item["outcome"] in OUTCOMES)
    return {
        "claim_id": _text(item["claim_id"], _LABEL),
        "selector": selector,
        "outcome": item["outcome"],
        "results": _results(item["results"]),
        "source_files": _source_files(item["source_files"]),
    }


def _annotation(value: Any) -> dict[str, Any]:
    item = _closed(value, ANNOTATION_KEYS)
    _check(item["kind"] in ANNOTATION_KINDS)
    note = item["note"]
    _check(note is None or (isinstance(note, str) and re.fullmatch(_NOTE, note) is not None))
    return {
        "annotation_id": _text(item["annotation_id"], _LABEL),
        "kind": item["kind"],
        "results": _results(item["results"]),
        "source_files": _source_files(item["source_files"]),
        "note": note,
    }


# ---- D4: the retrieval record -------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def _refuse(_: str) -> Any:
    raise ValueError("constant not allowed")


@dataclass(frozen=True)
class Bundle:
    bundle_id: str
    position: int
    kind: str
    sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Record:
    record_id: str
    snapshot_id: str
    generation_id: str
    ranker: str
    bundles: tuple[Bundle, ...]

    def bundle(self, bundle_id: str) -> Bundle | None:
        return next((b for b in self.bundles if b.bundle_id == bundle_id), None)


_RECORD_ERRORS = (
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
    RecursionError,
    MemoryError,
)


def load_record(raw: bytes) -> Record:
    """A saved S18 context result, valid on its own terms (never replayed against a snapshot)."""
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("byte order mark")
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_refuse
        )
        _check(isinstance(payload, dict))
        _check(payload.get("operation") == "context" and payload.get("status") == "ok")
        data = payload["data"]
        _check(isinstance(data, dict) and data.get("projection") == "task_context")
        validate_context(payload, "context", DEFAULT_MAX_BOUND_FIELD_ELEMENTS)
        bundles = []
        for position, bundle in enumerate(data["bundles"]):
            pairs = {
                (s["path"], s["source_sha256"])
                for s in bundle["sources"]
                if s.get("state") == "available"
                and isinstance(s.get("path"), str)
                and ev.hash_value(s.get("source_sha256"))
            }
            bundles.append(
                Bundle(_hex(bundle["id"]), position, str(bundle["kind"]), tuple(sorted(pairs)))
            )
        _check(len({b.bundle_id for b in bundles}) == len(bundles))
        return Record(
            _hex(data["retrieval_record_id"]),
            _hex(data["snapshot_id"]),
            _hex(data["generation_id"]),
            str(data["ranker"]),
            tuple(bundles),
        )
    except _RECORD_ERRORS:
        raise RecordInvalid("record_invalid") from None


# ---- D3: the JUnit profile ----------------------------------------------------------------------


def prescan_junit(raw: bytes) -> None:
    """Byte checks before any parser exists; any failure is `ArtifactInvalid`."""
    if not raw.startswith(DECLARATIONS):
        raise ArtifactInvalid("declaration")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ArtifactInvalid("encoding") from None
    if "\x00" in text:
        raise ArtifactInvalid("nul")
    if "<!" in text:
        raise ArtifactInvalid("markup declaration")
    if "<?" in text[len(DECLARATIONS[0]) :]:
        raise ArtifactInvalid("processing instruction")


@dataclass(frozen=True)
class Testcase:
    classname: str
    name: str
    children: tuple[str, ...]


class _Collector:
    """Builds no tree: the testcases and their direct failure, error and skipped children."""

    def __init__(self, limit: int):
        self.limit = limit
        self.stack: list[str] = []
        self.cases: list[tuple[str, str, list[str]]] = []

    def start(self, tag: str, attrib: Mapping[str, str]) -> None:
        if "{" in tag or len(self.stack) >= MAX_XML_DEPTH:
            raise ArtifactInvalid("tag or depth")
        parent = self.stack[-1] if self.stack else None
        if parent is None:
            if tag not in ("testsuites", "testsuite"):
                raise ArtifactInvalid("root")
        elif tag == "testsuite" and parent != "testsuites":
            raise ArtifactInvalid("suite")
        elif tag == "testcase":
            if parent != "testsuite":
                raise ArtifactInvalid("case")
            classname, name = attrib.get("classname"), attrib.get("name")
            if not isinstance(classname, str) or not isinstance(name, str):
                raise ArtifactInvalid("attributes")
            if len(self.cases) >= self.limit:
                raise ArtifactInvalid("too many testcases")
            self.cases.append((classname, name, []))
        elif parent == "testcase" and tag in ("failure", "error", "skipped"):
            self.cases[-1][2].append(tag)
        self.stack.append(tag)

    def end(self, tag: str) -> None:
        self.stack.pop()

    def data(self, data: str) -> None:
        return None

    def close(self) -> list[tuple[str, str, list[str]]]:
        return self.cases


def parse_junit(raw: bytes, max_testcases: int) -> list[Testcase]:
    """The testcases of a pre-scanned artifact; a parser or collector error is `ArtifactInvalid`."""
    collector = _Collector(max_testcases)
    parser = ET.XMLParser(target=collector)
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # ET.ParseError and every collector error alike
        raise ArtifactInvalid("parse") from None
    return [Testcase(c[0], c[1], tuple(c[2])) for c in collector.cases]


def derive_outcome(cases: Sequence[Testcase], nodeid: str) -> str:
    """`passed`, `failed` or a closed unverifiable detail for one claimed node id."""
    wanted = nodeid_pair(nodeid)
    found = [c for c in cases if (c.classname, c.name) == wanted]
    if not found:
        return "test_not_in_artifact"
    if len(found) > 1:
        return "outcome_not_objective"
    children = found[0].children
    if not children:
        return "passed"
    return "failed" if children == ("failure",) else "outcome_not_objective"


# ---- the lease ----------------------------------------------------------------------------------


def open_snapshot(stack: ExitStack, side: Side) -> Any:
    """The active generation leased read-only at the current schema epoch."""
    try:
        reader = runtime.open_active(stack, side)
    except DiffUsageError:
        raise FeedbackUsageError("graph_namespace") from None
    except runtime.OverlayRejected as exc:
        raise FeedbackRejected(exc.reason) from None
    if reader._lease_schema_epoch != max(runtime.SCHEMA_EPOCHS):
        raise FeedbackRejected("index_upgrade_required")
    return reader


def indexed_observations(reader: Any) -> dict[str, str]:
    """{path: observed source sha256} of the indexed observations of the leased snapshot."""
    try:
        row = reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()
        if row is None:
            raise ValueError("no observations")
        found: dict[str, str] = {}
        for item in json.loads(row[0]):
            if (
                item.get("type") == "observation"
                and item.get("state") == "indexed"
                and isinstance(item.get("path"), str)
                and ev.hash_value(item.get("observed_source_sha256"))
            ):
                found[item["path"]] = item["observed_source_sha256"]
        return found
    except (sqlite3.Error, ValueError, TypeError, AttributeError, KeyError):
        raise FeedbackRejected("source_snapshot_unavailable") from None


# ---- D6: records and the store ------------------------------------------------------------------


def _results_record(record: Record, ids: Sequence[str]) -> list[dict[str, Any]]:
    found = []
    for bundle_id in sorted(ids):
        bundle = record.bundle(bundle_id)
        assert bundle is not None
        found.append(
            {
                "bundle_id": bundle_id,
                "position": bundle.position,
                "sources": [{"path": p, "source_sha256": s} for p, s in bundle.sources],
            }
        )
    return found


def fingerprint(results: Sequence[Mapping[str, Any]]) -> str:
    pairs = sorted(
        {(s["path"], s["source_sha256"]) for r in results for s in r["sources"]},
    )
    return digest("atlas-feedback-fingerprint.v1", [list(p) for p in pairs])


def _retrieval(record: Record) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "ranker": record.ranker,
        "record_bundle_ids": [b.bundle_id for b in record.bundles],
    }


def event_dedup_key(record_id: str, artifact_sha256: str, selector: Mapping[str, str]) -> str:
    return digest(
        "atlas-feedback-dedup.v1",
        {
            "record_id": record_id,
            "artifact_sha256": artifact_sha256,
            "selector": {"kind": selector["kind"], "value": selector["value"]},
        },
    )


def annotation_dedup_key(record_id: str, kind: str, bundle_ids: Sequence[str]) -> str:
    return digest(
        "atlas-feedback-annotation-key.v1",
        {"record_id": record_id, "kind": kind, "bundle_ids": sorted(bundle_ids)},
    )


def build_event(
    envelope: Mapping[str, Any], claim: Mapping[str, Any], record: Record,
    pair: tuple[str, str], outcome: str,
) -> dict[str, Any]:  # fmt: skip
    results = _results_record(record, claim["results"])
    body = {
        "type": "feedback_event",
        "status": "verified_artifact",
        "claim": {"selector": dict(claim["selector"]), "outcome": claim["outcome"]},
        "verification": {
            "profile": envelope["artifact"]["profile"],
            "attestation": ATTESTATION,
            "derived_outcome": outcome,
            "testcase": {"classname": pair[0], "name": pair[1]},
        },
        "retrieval": _retrieval(record),
        "run": {"run_id": envelope["run"]["run_id"], "producer": dict(envelope["run"]["producer"])},
        "artifact": {
            "sha256": envelope["artifact"]["sha256"],
            "bytes": envelope["artifact"]["bytes"],
        },
        "snapshot": {
            "snapshot_id": envelope["snapshot_id"],
            "generation_id": envelope["generation_id"],
        },
        "results": results,
        "source_fingerprint": fingerprint(results),
        "dedup_key": event_dedup_key(
            record.record_id, envelope["artifact"]["sha256"], claim["selector"]
        ),
    }
    return {"id": digest("atlas-feedback-event.v1", body), **body}


def build_annotation(
    envelope: Mapping[str, Any], item: Mapping[str, Any], record: Record
) -> dict[str, Any]:
    results = _results_record(record, item["results"])
    body = {
        "type": "feedback_annotation",
        "status": "unverified_annotation",
        "verification": "none",
        "kind": item["kind"],
        "note": item["note"],
        "retrieval": _retrieval(record),
        "snapshot": {
            "snapshot_id": envelope["snapshot_id"],
            "generation_id": envelope["generation_id"],
        },
        "results": results,
        "source_fingerprint": fingerprint(results),
        "dedup_key": annotation_dedup_key(record.record_id, item["kind"], item["results"]),
    }
    return {"id": digest("atlas-feedback-annotation.v1", body), **body}


def assemble_store(
    events: Sequence[Mapping[str, Any]], annotations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    rest = {
        "format": STORE_FORMAT,
        "producer": dict(PRODUCER),
        "counts": {"events": len(events), "annotations": len(annotations)},
        "events": sorted(events, key=lambda item: item["id"]),
        "annotations": sorted(annotations, key=lambda item: item["id"]),
        "limitations": list(LIMITATIONS),
    }
    return {**rest, "id": digest(STORE_FORMAT, rest)}


def _bundle_ids(value: Any) -> list[str]:
    _check(isinstance(value, list) and 1 <= len(value) <= MAX_RESULTS)
    _check(all(ev.hash_value(v) for v in value) and len(set(value)) == len(value))
    return list(value)


def _validate_results(results: Any, retrieval: Mapping[str, Any]) -> None:
    ids = retrieval["record_bundle_ids"]
    _check(isinstance(results, list) and 1 <= len(results) <= MAX_RESULTS)
    last = ""
    for item in results:
        result = _closed(item, RESULT_KEYS)
        bundle_id = _hex(result["bundle_id"])
        _check(bundle_id > last and bundle_id in ids)
        last = bundle_id
        _check(result["position"] == ids.index(bundle_id) and type(result["position"]) is int)
        sources = result["sources"]
        _check(isinstance(sources, list) and 1 <= len(sources) <= MAX_SOURCE_FILES)
        previous: tuple[str, str] | None = None
        for source in sources:
            row = _closed(source, SOURCE_KEYS)
            pair = (_rel(row["path"]), _hex(row["source_sha256"]))
            _check(previous is None or pair > previous)
            previous = pair


def _validate_retrieval(value: Any) -> dict[str, Any]:
    retrieval = _closed(value, RETRIEVAL_KEYS)
    _hex(retrieval["record_id"])
    _text(retrieval["ranker"], _TEXT)
    _bundle_ids(retrieval["record_bundle_ids"])
    return retrieval


def _validate_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _closed(value, SNAPSHOT_KEYS)
    _hex(snapshot["snapshot_id"])
    _hex(snapshot["generation_id"])
    return snapshot


def _validate_event(item: Any) -> dict[str, Any]:
    event = _closed(item, EVENT_KEYS)
    _check(event["type"] == "feedback_event" and event["status"] in EVENT_STATUSES)
    claim = _closed(event["claim"], CLAIM_RECORD_KEYS)
    selector = _closed(claim["selector"], SELECTOR_KEYS)
    _check(selector["kind"] == SELECTOR_KIND and valid_nodeid(selector["value"]))
    _check(claim["outcome"] in OUTCOMES)
    verification = _closed(event["verification"], VERIFICATION_KEYS)
    _text(verification["profile"], _TEXT)
    _check(verification["attestation"] == ATTESTATION)
    _check(verification["derived_outcome"] == claim["outcome"])
    testcase = _closed(verification["testcase"], TESTCASE_KEYS)
    _check((testcase["classname"], testcase["name"]) == nodeid_pair(selector["value"]))
    retrieval = _validate_retrieval(event["retrieval"])
    run = _closed(event["run"], RUN_RECORD_KEYS)
    producer = _producer(run["producer"])
    artifact = _closed(event["artifact"], ARTIFACT_RECORD_KEYS)
    _hex(artifact["sha256"])
    _int(artifact["bytes"])
    snapshot = _validate_snapshot(event["snapshot"])
    _check(
        _hex(run["run_id"])
        == run_id(
            verification["profile"], artifact["sha256"], artifact["bytes"], producer,
            snapshot["snapshot_id"], snapshot["generation_id"],
        )
    )  # fmt: skip
    _validate_results(event["results"], retrieval)
    _check(event["source_fingerprint"] == fingerprint(event["results"]))
    _check(
        event["dedup_key"] == event_dedup_key(retrieval["record_id"], artifact["sha256"], selector)
    )
    body = {k: v for k, v in event.items() if k != "id"}
    _check(event["id"] == digest("atlas-feedback-event.v1", body))
    return event


def _validate_annotation(item: Any) -> dict[str, Any]:
    note = _closed(item, ANNOTATION_RECORD_KEYS)
    _check(note["type"] == "feedback_annotation" and note["status"] in ANNOTATION_STATUSES)
    _check(note["verification"] == "none" and note["kind"] in ANNOTATION_KINDS)
    text = note["note"]
    _check(text is None or (isinstance(text, str) and re.fullmatch(_NOTE, text) is not None))
    retrieval = _validate_retrieval(note["retrieval"])
    _validate_snapshot(note["snapshot"])
    _validate_results(note["results"], retrieval)
    _check(note["source_fingerprint"] == fingerprint(note["results"]))
    ids = [r["bundle_id"] for r in note["results"]]
    _check(note["dedup_key"] == annotation_dedup_key(retrieval["record_id"], note["kind"], ids))
    body = {k: v for k, v in note.items() if k != "id"}
    _check(note["id"] == digest("atlas-feedback-annotation.v1", body))
    return note


def validate_store(store: Any) -> None:
    """Self-consistency only: recomputed ids, keys and fingerprints; never authentication.

    A hand-built store whose digests agree passes. The store carries no secret, so it cannot
    say who produced an event or whether its artifact existed (a documented limitation).
    """
    try:
        document = _closed(store, STORE_KEYS)
        _check(document["format"] == STORE_FORMAT and document["producer"] == PRODUCER)
        _check(document["limitations"] == list(LIMITATIONS))
        events, annotations = document["events"], document["annotations"]
        _check(isinstance(events, list) and isinstance(annotations, list))
        counts = _closed(document["counts"], STORE_COUNT_KEYS)
        _check(counts == {"events": len(events), "annotations": len(annotations)})
        checked_events = [_validate_event(e) for e in events]
        checked_notes = [_validate_annotation(a) for a in annotations]
        for items in (checked_events, checked_notes):
            ids = [i["id"] for i in items]
            _check(ids == sorted(set(ids)))
            keys = [i["dedup_key"] for i in items]
            _check(len(set(keys)) == len(keys))
        rest = {k: v for k, v in document.items() if k != "id"}
        _check(document["id"] == digest(STORE_FORMAT, rest))
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("invalid feedback store") from exc


def load_store(raw: bytes) -> dict[str, Any]:
    """A previous store, strictly parsed and validated; `invalid_store` otherwise."""
    try:
        document = vector.load_strict(raw, numbers=True)
    except ValueError:
        raise FeedbackRejected("invalid_store") from None
    if (
        isinstance(document, dict)
        and isinstance(document.get("format"), str)
        and document["format"] != STORE_FORMAT
    ):
        raise FeedbackRejected("unsupported_store_version")
    try:
        _check(_encodable(document))
        validate_store(document)
    except ValueError:
        raise FeedbackRejected("invalid_store") from None
    return document  # type: ignore[no-any-return]


# ---- the summary --------------------------------------------------------------------------------


def summary_document(
    store: Mapping[str, Any], previous_id: str | None, counts: Mapping[str, int]
) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "command": COMMAND,
        "store_format": STORE_FORMAT,
        "store_id": store["id"],
        "previous_store_id": previous_id,
        "counts": {
            "events": len(store["events"]),
            "annotations": len(store["annotations"]),
            "new_events": counts["new_events"],
            "new_annotations": counts["new_annotations"],
            "duplicate_events": counts["duplicate_events"],
            "duplicate_annotations": counts["duplicate_annotations"],
        },
    }


def validate_summary(summary: Any) -> None:
    try:
        document = _closed(summary, SUMMARY_KEYS)
        _check(document["format"] == SUMMARY_FORMAT and document["command"] == COMMAND)
        _check(document["store_format"] == STORE_FORMAT and ev.hash_value(document["store_id"]))
        _check(
            document["previous_store_id"] is None or ev.hash_value(document["previous_store_id"])
        )
        counts = _closed(document["counts"], SUMMARY_COUNT_KEYS)
        _check(all(type(v) is int and v >= 0 for v in counts.values()))
        _check(counts["new_events"] + counts["duplicate_events"] <= counts["events"])
        _check(counts["new_annotations"] + counts["duplicate_annotations"] <= counts["annotations"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("invalid feedback summary") from exc


# ---- D5: the import pipeline ---------------------------------------------------------------------


def _item_failure(kind: str, detail: str) -> FeedbackRejected:
    return FeedbackRejected(kind, detail)


def _check_item(
    kind: str, item: Mapping[str, Any], record: Record, observed: Mapping[str, str]
) -> None:
    """Stage 14 for one claim or annotation; order-independent (phase by phase)."""
    ids = item["results"]
    if any(record.bundle(i) is None for i in ids):
        raise _item_failure(kind, "result_not_in_record")
    bundles = [record.bundle(i) for i in ids]
    if any(b is None or not b.sources for b in bundles):
        raise _item_failure(kind, "result_source_unavailable")
    derived: dict[str, set[str]] = {}
    for bundle in bundles:
        assert bundle is not None
        for path, sha in bundle.sources:
            derived.setdefault(path, set()).add(sha)
    if any(path not in observed for path in derived):
        raise _item_failure(kind, "source_not_indexed")
    exact = all(len(shas) == 1 for shas in derived.values()) and item["source_files"] == {
        path: next(iter(shas)) for path, shas in derived.items()
    }
    if not exact or any(observed[path] != next(iter(shas)) for path, shas in derived.items()):
        raise _item_failure(kind, "source_fingerprint_mismatch")


def _merge(
    built: Sequence[dict[str, Any]], previous: Sequence[Mapping[str, Any]]
) -> tuple[list[Mapping[str, Any]], int, int]:
    """(previous plus new records, new count, duplicate count); a key reused by another id fails."""
    fresh: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    for item in built:
        if item["id"] in fresh:
            continue
        if keys.setdefault(item["dedup_key"], item["id"]) != item["id"]:
            raise FeedbackRejected("conflicting_duplicate")
        fresh[item["id"]] = item
    known = {p["id"]: p for p in previous}
    known_keys = {p["dedup_key"]: p["id"] for p in previous}
    added = [i for i in fresh.values() if i["id"] not in known]
    if any(known_keys.get(i["dedup_key"], i["id"]) != i["id"] for i in added):
        raise FeedbackRejected("conflicting_duplicate")
    merged = [*known.values(), *added]
    return merged, len(added), len(fresh) - len(added)


def import_feedback(
    envelope_raw: bytes,
    record_raw: bytes,
    artifact_raw: bytes | None,
    store_raw: bytes | None,
    side: Side,
    limits: Limits,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The store and the summary, or `FeedbackRejected`; the first failure wins; no write."""
    # 1 sizes
    for raw, cap, reason in (
        (envelope_raw, limits.max_envelope_bytes, "envelope_too_large"),
        (record_raw, limits.max_record_bytes, "record_too_large"),
        (artifact_raw, limits.max_artifact_bytes, "artifact_too_large"),
        (store_raw, limits.max_store_bytes, "store_too_large"),
    ):
        if raw is not None and len(raw) > cap:
            raise FeedbackRejected(reason)
    # 2 envelope, 3 previous store
    envelope = parse_envelope(envelope_raw, limits)
    previous = load_store(store_raw) if store_raw is not None else None
    claims, annotations = envelope["claims"], envelope["annotations"]
    kind = "unverifiable_outcome" if claims else "unlinked_annotation"
    with ExitStack() as stack:
        # 4 lease, 5 observations
        reader = open_snapshot(stack, side)
        observed = indexed_observations(reader)
        lease = (str(reader.snapshot_id), str(reader.generation_id))
    # 6 record, 7 record id, 8 snapshot
    try:
        record = load_record(record_raw)
    except RecordInvalid:
        raise FeedbackRejected(kind, "record_invalid") from None
    if envelope["retrieval_record_id"] != record.record_id:
        raise FeedbackRejected(kind, "record_id_mismatch")
    claimed = (envelope["snapshot_id"], envelope["generation_id"])
    if claimed != (record.snapshot_id, record.generation_id) or claimed != lease:
        raise FeedbackRejected(kind, "snapshot_mismatch")
    # 9..13 provenance, only when there are claims
    artifact = envelope["artifact"]
    cases: list[Testcase] = []
    if claims:
        if (
            envelope["run"] is None
            or artifact is None
            or artifact_raw is None
            or any(c["selector"] is None for c in claims)
        ):
            raise FeedbackRejected("unverifiable_outcome", "missing_provenance")
        if artifact["profile"] != PROFILE:
            raise FeedbackRejected("unverifiable_outcome", "unsupported_profile")
        if len(artifact_raw) != artifact["bytes"]:
            raise FeedbackRejected("unverifiable_outcome", "artifact_size_mismatch")
        if hashlib.sha256(artifact_raw).hexdigest() != artifact["sha256"]:
            raise FeedbackRejected("unverifiable_outcome", "artifact_digest_mismatch")
        expected = run_id(
            artifact["profile"], artifact["sha256"], artifact["bytes"],
            envelope["run"]["producer"], *claimed,
        )  # fmt: skip
        if envelope["run"]["run_id"] != expected:
            raise FeedbackRejected("unverifiable_outcome", "run_identity_mismatch")
        try:
            prescan_junit(artifact_raw)
            cases = parse_junit(artifact_raw, limits.max_testcases)
        except ArtifactInvalid:
            raise FeedbackRejected("unverifiable_outcome", "artifact_invalid") from None
    # 14 each claim, then each annotation
    for claim in claims:
        _check_item("unverifiable_outcome", claim, record, observed)
    for note in annotations:
        _check_item("unlinked_annotation", note, record, observed)
    # 15 derived outcomes
    events = []
    for claim in claims:
        outcome = derive_outcome(cases, claim["selector"]["value"])
        if outcome not in OUTCOMES:
            raise FeedbackRejected("unverifiable_outcome", outcome)
        if outcome != claim["outcome"]:
            raise FeedbackRejected("unverifiable_outcome", "claim_disagrees_with_artifact")
        pair = nodeid_pair(claim["selector"]["value"])
        events.append(build_event(envelope, claim, record, pair, outcome))
    notes = [build_annotation(envelope, n, record) for n in annotations]
    # 16 merge, 17 assemble
    prior_events = previous["events"] if previous else []
    prior_notes = previous["annotations"] if previous else []
    merged_events, new_events, duplicate_events = _merge(events, prior_events)
    merged_notes, new_notes, duplicate_notes = _merge(notes, prior_notes)
    if len(merged_events) + len(merged_notes) > limits.max_events:
        raise FeedbackRejected("too_many_events")
    store = assemble_store(merged_events, merged_notes)
    try:
        validate_store(store)
    except ValueError:
        raise FeedbackRejected("invalid_output") from None
    summary = summary_document(
        store,
        previous["id"] if previous else None,
        {
            "new_events": new_events,
            "new_annotations": new_notes,
            "duplicate_events": duplicate_events,
            "duplicate_annotations": duplicate_notes,
        },
    )
    validate_summary(summary)
    if len(canonical_bytes(store)) > limits.max_output_bytes:
        raise FeedbackRejected("output_too_large")
    return store, summary
