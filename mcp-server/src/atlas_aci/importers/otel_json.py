"""Parse one local OTLP JSON trace artifact and its provenance manifest (ACI-S27).

Pure: no file, clock, environment, network or graph access. The artifact is one TracesData /
ExportTraceServiceRequest JSON object (lowerCamelCase keys). Only a small allowlist of span
fields is ever read: the identities, the parent identity, the two timestamps, `status.code`,
`code.file.path` and `code.line.number`. Everything else in the artifact is never walked (other
than the surrogate scan) and never reaches an output.

Nothing here recurses: nesting is bounded by a byte scan and every walk uses an explicit stack
or loop, so the outcome never depends on the interpreter's recursion limit or its integer
string conversion limit.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from atlas_aci.storage import evidence as ev

MANIFEST_FORMAT = "atlas-otel-provenance.v1"
PROFILE = "atlas-otel-json-profile.v1"
IMPORTER_NAME = "atlas-aci import-otel"
IMPORTER_VERSION = "atlas-otel-json-importer.v1"

MAX_MANIFEST_BYTES = 65536
MAX_NESTING_DEPTH = 32
MAX_INT_DIGITS = 4300
MAX_PATH_CHARS = 4096
MAX_ROOT_CHARS = 1024
MAX_SOURCE_ROOTS = 16
MAX_UNIX_NANO = 18446744073709551615
MAX_LINE = 2147483647
MAX_DECLARED_ARTIFACT_BYTES = 268435456

LIMITS = ("max_artifact_bytes", "max_spans", "max_attributes_per_span")
DEFAULT_LIMITS = {
    "max_artifact_bytes": 33554432,
    "max_spans": 100000,
    "max_attributes_per_span": 512,
}
LIMIT_RANGES = {
    "max_artifact_bytes": (1, 268435456),
    "max_spans": (1, 1000000),
    "max_attributes_per_span": (1, 4096),
}

REJECTIONS = (
    "manifest_too_large",
    "manifest_invalid",
    "unsupported_profile",
    "artifact_too_large",
    "artifact_digest_mismatch",
    "artifact_invalid_json",
    "nesting_too_deep",
    "artifact_not_otlp_traces",
    "span_limit_exceeded",
    "attribute_limit_exceeded",
    "invalid_trace_id",
    "invalid_span_id",
    "invalid_parent_span_id",
    "invalid_span",
    "duplicate_span",
    "parent_cycle",
)
LOCATION_STATES = ("present", "missing", "malformed")

PATH_KEY = "code.file.path"
LINE_KEY = "code.line.number"
LEGACY_KEYS = ("code.filepath", "code.lineno")

BOM = b"\xef\xbb\xbf"
_SURROGATE = re.compile("[\ud800-\udfff]")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_RUN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_BUILD = re.compile(r"[A-Za-z0-9._:+-]{1,128}")
_TEXT = re.compile(r"[ -~]{1,128}")
_ROOT_TEXT = re.compile(r"[^\x00-\x1f\x7f\\]{1,1024}")
_DECLARED_PATH = re.compile(r"[^\x00-\x1f]{1,4096}")
_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
_TRACE_HEX = re.compile(r"[0-9a-fA-F]{32}")
_SPAN_HEX = re.compile(r"[0-9a-fA-F]{16}")
_DECIMAL = re.compile(r"0|[1-9][0-9]{0,19}")


class OtelRejected(ValueError):
    """The manifest or the artifact is refused; `reason` is a closed code, never input text."""

    def __init__(self, reason: str):
        super().__init__("otel_rejected: " + reason)
        self.reason = reason


class OtelUsageError(ValueError):
    """An import limit is out of range or not an integer; `limit` is its closed name."""

    def __init__(self, limit: str):
        super().__init__("otel_usage: " + limit)
        self.limit = limit


@dataclass(frozen=True)
class Limits:
    max_artifact_bytes: int = DEFAULT_LIMITS["max_artifact_bytes"]
    max_spans: int = DEFAULT_LIMITS["max_spans"]
    max_attributes_per_span: int = DEFAULT_LIMITS["max_attributes_per_span"]


@dataclass(frozen=True)
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start: str
    end: str
    status_code: int
    location_state: str
    declared_path: str | None
    line: int | None
    legacy: bool


def normalize_limits(chosen: Mapping[str, Any] | None = None) -> Limits:
    """Fill defaults; any unknown name, bool, non-int or out-of-range value is a usage error."""
    chosen = dict(chosen or {})
    for name in chosen:
        if name not in LIMITS:
            raise OtelUsageError(str(name))
    values: dict[str, int] = {}
    for name in LIMITS:
        value = chosen.get(name)
        if value is None:
            value = DEFAULT_LIMITS[name]
        low, high = LIMIT_RANGES[name]
        if type(value) is not int or not low <= value <= high:
            raise OtelUsageError(name)
        values[name] = value
    return Limits(**values)


def _sha(value: Any) -> str:
    return hashlib.sha256(ev.canonical(value).encode("utf-8")).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def observation_id(run_id: str, trace_id: str, span_id: str) -> str:
    return _sha(["atlas-otel-observation.v1", run_id, trace_id, span_id])


def relation_id(parent_id: str, child_id: str) -> str:
    return _sha(["atlas-otel-relation.v1", parent_id, child_id])


# ---- strict JSON ----------------------------------------------------------------------------


class _JsonNesting(ValueError):
    pass


class _JsonInvalid(ValueError):
    pass


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _bounded_int(token: str) -> int:
    if len(token.lstrip("-")) > MAX_INT_DIGITS:
        raise ValueError("integer too long")
    return int(token)


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
    """Linear scan: `[`/`{` depth outside JSON strings must not exceed `MAX_NESTING_DEPTH`."""
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
            if depth > MAX_NESTING_DEPTH:
                raise _JsonNesting("nesting")
        elif byte in (0x5D, 0x7D):
            depth = max(depth - 1, 0)


def _loads(raw: bytes) -> Any:
    """Strict JSON: BOM, nesting, UTF-8, duplicates, constants, long ints, lone surrogates."""
    if raw.startswith(BOM):
        raise _JsonInvalid("bom")
    _check_nesting(raw)
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_refuse_constant,
            parse_float=_finite_float,
            parse_int=_bounded_int,
        )
        if _has_surrogate(document):
            raise ValueError("lone surrogate")
    except Exception as exc:  # incl. RecursionError: every parse failure is one closed reason
        raise _JsonInvalid("invalid") from exc
    return document


# ---- manifest -------------------------------------------------------------------------------


def _text_ok(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _keys(value: Any, names: tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(names)


def valid_root_from(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value in ("", "/"):
        return True
    if _ROOT_TEXT.fullmatch(value) is None or value.endswith("/"):
        return False
    body = value[1:] if value.startswith("/") else value
    return all(part not in ("", ".", "..") for part in body.split("/"))


def valid_root_to(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value == "":
        return True
    return _ROOT_TEXT.fullmatch(value) is not None and ev.relative_path(value)


def _valid_roots(roots: Any) -> bool:
    if not isinstance(roots, list) or len(roots) > MAX_SOURCE_ROOTS:
        return False
    seen: set[str] = set()
    for rule in roots:
        if not _keys(rule, ("from", "to")):
            return False
        if not valid_root_from(rule["from"]) or not valid_root_to(rule["to"]):
            return False
        if rule["from"] in seen:
            return False
        seen.add(rule["from"])
    return True


def _manifest_shape_ok(document: Any) -> bool:
    names = ("format", "profile", "artifact", "run", "producer", "build", "source_roots")
    if not _keys(document, names):
        return False
    if document["format"] != MANIFEST_FORMAT or not isinstance(document["profile"], str):
        return False
    artifact = document["artifact"]
    if not _keys(artifact, ("sha256", "bytes")):
        return False
    size = artifact["bytes"]
    if not _text_ok(_HEX64, artifact["sha256"]) or type(size) is not int:
        return False
    if not 1 <= size <= MAX_DECLARED_ARTIFACT_BYTES:
        return False
    run, producer, build = document["run"], document["producer"], document["build"]
    if not _keys(run, ("id",)) or not _text_ok(_RUN, run["id"]):
        return False
    if not _keys(producer, ("name", "version")):
        return False
    if not _text_ok(_TEXT, producer["name"]) or not _text_ok(_TEXT, producer["version"]):
        return False
    if not _keys(build, ("id", "snapshot_id")):
        return False
    if not _text_ok(_BUILD, build["id"]) or not _text_ok(_HEX64, build["snapshot_id"]):
        return False
    return _valid_roots(document["source_roots"])


def parse_manifest(raw: bytes) -> dict[str, Any]:
    """Size, then strict JSON, then the closed shape, then the profile; first failure wins."""
    if len(raw) > MAX_MANIFEST_BYTES:
        raise OtelRejected("manifest_too_large")
    try:
        document = _loads(raw)
    except ValueError:
        raise OtelRejected("manifest_invalid") from None
    if not _manifest_shape_ok(document):
        raise OtelRejected("manifest_invalid")
    if document["profile"] != PROFILE:
        raise OtelRejected("unsupported_profile")
    return {
        "format": document["format"],
        "profile": document["profile"],
        "artifact": {
            "sha256": document["artifact"]["sha256"],
            "bytes": document["artifact"]["bytes"],
        },
        "run": {"id": document["run"]["id"]},
        "producer": {
            "name": document["producer"]["name"],
            "version": document["producer"]["version"],
        },
        "build": {
            "id": document["build"]["id"],
            "snapshot_id": document["build"]["snapshot_id"],
        },
        "source_roots": [
            {"from": rule["from"], "to": rule["to"]} for rule in document["source_roots"]
        ],
    }


# ---- source path mapping --------------------------------------------------------------------


def map_source_path(declared: Any, source_roots: Any) -> str | None:
    """Rewrite a declared span path into a snapshot-relative path, or None.

    The longest matching `from` wins; matching is on whole segments. There is no implicit
    identity mapping, and a Windows-style path never matches.
    """
    if not isinstance(declared, str) or not declared:
        return None
    if "\\" in declared or _DRIVE.match(declared) is not None:
        return None
    best: tuple[int, str, str] | None = None
    for rule in source_roots:
        origin = rule["from"]
        if origin == "":
            if declared.startswith("/"):
                continue
            rest = declared
        elif origin == "/":
            if not declared.startswith("/"):
                continue
            rest = declared[1:]
        elif declared == origin:
            rest = ""
        elif declared.startswith(origin + "/"):
            rest = declared[len(origin) + 1 :]
        else:
            continue
        if best is None or len(origin) > best[0]:
            best = (len(origin), rule["to"], rest)
    if best is None or best[2] == "":
        return None
    target, rest = best[1], best[2]
    result = rest if target == "" else target + "/" + rest
    return result if ev.relative_path(result) else None


# ---- artifact -------------------------------------------------------------------------------


def _unix_nano(value: Any) -> int | None:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and _DECIMAL.fullmatch(value) is not None:
        number = int(value)
    else:
        return None
    return number if 1 <= number <= MAX_UNIX_NANO else None


def _hex_id(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return None
    lowered = value.lower()
    return None if set(lowered) == {"0"} else lowered


def _path_value(value: Any) -> str | None:
    if not _keys(value, ("stringValue",)):
        return None
    text = value["stringValue"]
    return text if isinstance(text, str) and _DECLARED_PATH.fullmatch(text) else None


def _line_value(value: Any) -> int | None:
    if not _keys(value, ("intValue",)):
        return None
    raw = value["intValue"]
    if type(raw) is int:
        number = raw
    elif isinstance(raw, str) and _DECIMAL.fullmatch(raw) is not None:
        number = int(raw)
    else:
        return None
    return number if 1 <= number <= MAX_LINE else None


def _location(attributes: list[Any]) -> tuple[str, str | None, int | None, bool]:
    paths: list[Any] = []
    lines: list[Any] = []
    legacy = False
    for item in attributes:
        key = item["key"]
        if key == PATH_KEY:
            paths.append(item.get("value"))
        elif key == LINE_KEY:
            lines.append(item.get("value"))
        elif key in LEGACY_KEYS:
            legacy = True
    declared = _path_value(paths[0]) if len(paths) == 1 else None
    line = _line_value(lines[0]) if len(lines) == 1 else None
    if not paths:
        state = "missing"
    elif len(paths) > 1 or declared is None or (lines and line is None) or len(lines) > 1:
        state = "malformed"
    else:
        state = "present"
    return state, declared, line, legacy


def _span(item: dict[str, Any], limits: Limits) -> Span:
    trace_id = _hex_id(item.get("traceId"), _TRACE_HEX)
    if trace_id is None:
        raise OtelRejected("invalid_trace_id")
    span_id = _hex_id(item.get("spanId"), _SPAN_HEX)
    if span_id is None:
        raise OtelRejected("invalid_span_id")
    parent: str | None = None
    if item.get("parentSpanId", "") != "":
        parent = _hex_id(item["parentSpanId"], _SPAN_HEX)
        if parent is None:
            raise OtelRejected("invalid_parent_span_id")
    start = _unix_nano(item.get("startTimeUnixNano"))
    end = _unix_nano(item.get("endTimeUnixNano"))
    if start is None or end is None or start > end:
        raise OtelRejected("invalid_span")
    code = 0
    if "status" in item:
        status = item["status"]
        if not isinstance(status, dict):
            raise OtelRejected("invalid_span")
        if "code" in status:
            code = status["code"]
            if type(code) is not int or code not in (0, 1, 2):
                raise OtelRejected("invalid_span")
    attributes = item.get("attributes", [])
    if not isinstance(attributes, list):
        raise OtelRejected("invalid_span")
    if len(attributes) > limits.max_attributes_per_span:
        raise OtelRejected("attribute_limit_exceeded")
    for entry in attributes:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            raise OtelRejected("invalid_span")
    state, declared, line, legacy = _location(attributes)
    return Span(
        trace_id, span_id, parent, str(start), str(end), code, state, declared, line, legacy
    )


def _collect(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        raise OtelRejected("artifact_not_otlp_traces")
    found: list[dict[str, Any]] = []
    for resource in document["resourceSpans"]:
        if not isinstance(resource, dict) or "instrumentationLibrarySpans" in resource:
            raise OtelRejected("artifact_not_otlp_traces")
        scopes = resource.get("scopeSpans", [])
        if not isinstance(scopes, list):
            raise OtelRejected("artifact_not_otlp_traces")
        for scope in scopes:
            if not isinstance(scope, dict):
                raise OtelRejected("artifact_not_otlp_traces")
            items = scope.get("spans", [])
            if not isinstance(items, list):
                raise OtelRejected("artifact_not_otlp_traces")
            for item in items:
                if not isinstance(item, dict):
                    raise OtelRejected("artifact_not_otlp_traces")
                found.append(item)
    return found


def _check_cycles(spans: list[Span]) -> None:
    """Iterative three-colour walk over the in-artifact (trace, parent) links."""
    parents = {(s.trace_id, s.span_id): s.parent_span_id for s in spans}
    colour: dict[tuple[str, str], int] = {}
    for start in parents:
        if start in colour:
            continue
        walked: list[tuple[str, str]] = []
        current: tuple[str, str] | None = start
        while current is not None and current in parents and current not in colour:
            colour[current] = 1
            walked.append(current)
            up = parents[current]
            current = None if up is None else (current[0], up)
        if current is not None and colour.get(current) == 1:
            raise OtelRejected("parent_cycle")
        for node in walked:
            colour[node] = 2


def parse_artifact(raw: bytes, manifest: Mapping[str, Any], limits: Limits) -> list[Span]:
    """Size, digest (before any parse), strict JSON, OTLP shape, count, spans, ids, cycles."""
    if len(raw) > limits.max_artifact_bytes:
        raise OtelRejected("artifact_too_large")
    declared = manifest["artifact"]
    if len(raw) != declared["bytes"] or sha256_hex(raw) != declared["sha256"]:
        raise OtelRejected("artifact_digest_mismatch")
    try:
        document = _loads(raw)
    except _JsonNesting:
        raise OtelRejected("nesting_too_deep") from None
    except ValueError:
        raise OtelRejected("artifact_invalid_json") from None
    items = _collect(document)
    if len(items) > limits.max_spans:
        raise OtelRejected("span_limit_exceeded")
    spans: list[Span] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        span = _span(item, limits)
        key = (span.trace_id, span.span_id)
        if key in seen:
            raise OtelRejected("duplicate_span")
        seen.add(key)
        spans.append(span)
    _check_cycles(spans)
    return spans
