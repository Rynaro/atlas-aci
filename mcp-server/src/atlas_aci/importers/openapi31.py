"""Import one OpenAPI 3.1 JSON document into an `atlas-contract-snapshot.v1`.

The snapshot is a pure function of (document bytes, profile, options). It is a *contract
snapshot*: its `snapshot_id` binds the artifact digest, the profile and the options, and it is
not a source snapshot or a graph snapshot. Every element carries the artifact digest, the
snapshot id and an RFC 6901 pointer into the artifact; there is no source-snapshot provenance.

Nothing here reads a file, a clock, the environment or the network, and no traversal recurses:
nesting is bounded by a byte scan, and every walk uses an explicit stack, so the outcome never
depends on the interpreter's recursion limit.
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

FORMAT = "atlas-contract-snapshot.v1"
PROFILE = "atlas-openapi31-profile.v1"
MAX_NESTING_DEPTH = 64
MAX_INT_DIGITS = 4300

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
REGION_NAMES = (
    "parameters",
    "requestBody",
    "responses",
    "callbacks",
    "security",
    "operationId",
    "deprecated",
    "servers",
    "other",
)
REJECTIONS = (
    "too_large",
    "invalid_json",
    "nesting_too_deep",
    "not_an_object",
    "root_reference",
    "unsupported_profile",
    "invalid_document",
    "limit_exceeded",
)
STATUSES = ("complete", "incomplete")
LOCATIONS = ("query", "header", "path", "cookie")
RESERVED_HEADERS = ("accept", "content-type", "authorization")
LEVELS = ("path_item", "operation")
REFERENCE_REASONS = (
    "remote",
    "outside_document",
    "target_missing",
    "cyclic_reference",
    "reference_depth_exceeded",
    "reference_expansion_limit",
)
DIAGNOSTIC_REASONS: dict[str, tuple[str, ...]] = {
    "unresolved_reference": REFERENCE_REASONS,
    "invalid_path_item": ("not_an_object", "reference_with_siblings"),
    "invalid_path_template": ("malformed_template",),
    "invalid_operation": ("not_an_object",),
    "invalid_parameter": (
        "not_an_array",
        "not_an_object",
        "missing_name",
        "invalid_location",
        "invalid_required",
        "path_parameter_not_required",
        "path_parameter_not_in_template",
        "duplicate_parameter",
    ),
    "ambiguous_identity": ("normalized_identity_collision",),
}
IGNORED_FIELDS = (
    "components:x-*",
    "document:externalDocs",
    "document:info",
    "document:openapi",
    "document:tags",
    "document:x-*",
    "operation:description",
    "operation:externalDocs",
    "operation:summary",
    "operation:tags",
    "operation:x-*",
    "parameter:description",
    "parameter:x-*",
    "path_item:description",
    "path_item:summary",
    "path_item:x-*",
    "paths:x-*",
)
LIMITATIONS = (
    "json_documents_only_yaml_not_supported",
    "same_document_json_pointer_references_only",
    "schemas_are_compared_by_digest_not_by_implication",
    "no_global_verdict_is_produced",
    "security_and_authentication_changes_are_review_only",
    "contract_snapshot_is_not_bound_to_a_source_snapshot",
    "operations_are_not_mapped_to_handlers",
    "ignored_fields_are_excluded_from_digests",
    "reference_chains_follow_only_at_path_item_parameter_body_response_positions",
    "document_region_changes_are_not_attributed_to_operations",
    "reserved_header_parameters_are_review_only",
    "ref_like_values_inside_examples_are_classified_conservatively",
    "not_exposed_over_mcp",
    "schema_id_and_anchor_base_uris_are_not_applied",
    "integer_and_float_literals_differ_and_decimals_collapse_to_floats",
)

# name: (default, low, high)
LIMITS: dict[str, tuple[int, int, int]] = {
    "max_document_bytes": (8388608, 1, 33554432),
    "max_operations": (5000, 1, 20000),
    "max_parameters_per_operation": (128, 1, 1024),
    "max_ref_depth": (8, 1, 64),
    "max_ref_expansions": (100000, 0, 1000000),
    "max_diagnostics": (1000, 0, 100000),
}

BOM = b"\xef\xbb\xbf"
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_PROFILE = re.compile(r"3\.1\.[0-9]+")
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")
_POINTER_TOKEN = re.compile(r"(?:[^~]|~[01])*", re.DOTALL)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*")
_HEX = "0123456789abcdefABCDEF"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PATH_ITEM_KNOWN = ("parameters", "servers", "summary", "description")
_OPERATION_KNOWN = (
    "parameters",
    "requestBody",
    "responses",
    "callbacks",
    "security",
    "operationId",
    "deprecated",
    "servers",
    "summary",
    "description",
    "tags",
    "externalDocs",
)
_TOP_KNOWN = (
    "openapi",
    "info",
    "jsonSchemaDialect",
    "servers",
    "paths",
    "webhooks",
    "components",
    "security",
    "tags",
    "externalDocs",
)


class OpenAPIRejected(ValueError):
    """The document is refused as a whole. Carries one closed reason, never document text."""

    def __init__(self, reason: str) -> None:
        super().__init__("openapi_rejected: " + reason)
        self.reason = reason


class OpenAPIUsageError(ValueError):
    """A limit outside its documented range."""

    def __init__(self, limit: str) -> None:
        super().__init__("invalid_limit: " + limit)
        self.limit = limit


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


@dataclass(frozen=True)
class Options:
    max_document_bytes: int = 8388608
    max_operations: int = 5000
    max_parameters_per_operation: int = 128
    max_ref_depth: int = 8
    max_ref_expansions: int = 100000
    max_diagnostics: int = 1000

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in LIMITS}


def normalize_options(chosen: Mapping[str, int | None] | None = None) -> Options:
    """Defaults for unset limits; anything out of range or not an integer is a usage error."""
    chosen = dict(chosen or {})
    for name in chosen:
        if name not in LIMITS:
            raise OpenAPIUsageError(str(name))
    values: dict[str, int] = {}
    for name, (default, low, high) in LIMITS.items():
        value = chosen.get(name)
        if value is None:
            value = default
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise OpenAPIUsageError(name)
        values[name] = value
    return Options(**values)


def digest(value: Any) -> str:
    return hashlib.sha256(ev.canonical(value).encode("utf-8")).hexdigest()


def canonical_bytes(snapshot: dict[str, Any]) -> bytes:
    """The exact bytes the CLI prints: canonical JSON plus one newline."""
    return ev.canonical(snapshot).encode("utf-8") + b"\n"


def snapshot_identity(artifact_sha256: str, options: Options) -> str:
    rest = {"artifact_sha256": artifact_sha256, "profile": PROFILE, "options": options.as_dict()}
    return hashlib.sha256((FORMAT + ":" + ev.canonical(rest)).encode("utf-8")).hexdigest()


# ---- strict JSON ----------------------------------------------------------------------------


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
    """Linear scan: `[`/`{` depth outside JSON strings must not exceed `MAX_NESTING_DEPTH`.

    Independent of the interpreter's recursion limit, so the reason never varies by version.
    """
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
                raise OpenAPIRejected("nesting_too_deep")
        elif byte in (0x5D, 0x7D):
            depth = max(depth - 1, 0)


def parse(raw: bytes, options: Options) -> Any:
    """Strict JSON: closed first-failure order, every failure one closed reason."""
    if len(raw) > options.max_document_bytes:
        raise OpenAPIRejected("too_large")
    if raw.startswith(BOM):
        raise OpenAPIRejected("invalid_json")
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
        raise OpenAPIRejected("invalid_json") from exc
    return document


# ---- RFC 6901 -------------------------------------------------------------------------------


def pointer(*parts: Any) -> str:
    """RFC 6901 pointer for the given parts (`~` first, then `/`)."""
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def resolve_pointer(document: Any, text: str) -> Any:
    """Strict RFC 6901 lookup returning `MISSING` instead of raising; iterative."""
    if text == "":
        return document
    if not text.startswith("/"):
        return MISSING
    node = document
    for token in text.split("/")[1:]:
        if _POINTER_TOKEN.fullmatch(token) is None:
            return MISSING
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if key not in node:
                return MISSING
            node = node[key]
        elif isinstance(node, list):
            if _ARRAY_INDEX.fullmatch(key) is None or len(key) > 18 or int(key) >= len(node):
                return MISSING
            node = node[int(key)]
        else:
            return MISSING
    return node


def percent_decode(fragment: str) -> str | None:
    """`%XX` to bytes, then strict UTF-8; None when an escape or the bytes are invalid."""
    out = bytearray()
    at = 0
    while at < len(fragment):
        char = fragment[at]
        if char == "%":
            pair = fragment[at + 1 : at + 3]
            if len(pair) != 2 or pair[0] not in _HEX or pair[1] not in _HEX:
                return None
            out.append(int(pair, 16))
            at += 3
        else:
            out.extend(char.encode("utf-8"))
            at += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return None


def profile_supported(value: Any) -> bool:
    return isinstance(value, str) and _PROFILE.fullmatch(value) is not None


def classify(ref: str) -> tuple[str, str]:
    """("local", pointer) for `#/...`, else ("bad", reason). Never touches a document."""
    if ref.startswith("#/"):
        decoded = percent_decode(ref[1:])
        if decoded is None:
            return "bad", "target_missing"
        return "local", decoded
    if ref.startswith("#"):
        return "bad", "outside_document"
    scheme, colon, _rest = ref.partition(":")
    if (colon and _SCHEME.fullmatch(scheme) is not None) or ref.startswith("//"):
        return "bad", "remote"
    return "bad", "outside_document"


def path_template(key: str) -> tuple[list[str], str] | None:
    """(variable names, identity path with every `{name}` as `{}`) or None when malformed."""
    if not key.startswith("/"):
        return None
    names: list[str] = []
    out: list[str] = []
    at = 0
    while at < len(key):
        char = key[at]
        if char == "{":
            close = key.find("}", at + 1)
            if close < 0:
                return None
            name = key[at + 1 : close]
            if name == "" or "{" in name or "/" in name or name in names:
                return None
            names.append(name)
            out.append("{}")
            at = close + 1
        elif char == "}":
            return None
        else:
            out.append(char)
            at += 1
    return names, "".join(out)


def _is_extension(key: str) -> bool:
    return key.startswith("x-")


def _is_container(value: Any) -> bool:
    return isinstance(value, dict | list)


# ---- the builder ----------------------------------------------------------------------------

Event = tuple[str, str, str | None, str]  # (kind, reason, ref, member pointer)


class _Builder:
    def __init__(
        self, document: dict[str, Any], options: Options, artifact_sha: str, snapshot_id: str
    ) -> None:
        self.doc = document
        self.options = options
        self.artifact_sha = artifact_sha
        self.snapshot_id = snapshot_id
        self.expansions = 0
        # Template-independent results only: reference resolution, subtree scans, digests.
        self._follow_cache: dict[str, tuple[Any, ...]] = {}
        self._probe_cache: dict[str, tuple[str, str]] = {}
        self._nodes: dict[str, tuple[tuple[Event, ...], tuple[str, ...]]] = {}
        self._memo: dict[str, tuple[Event, ...]] = {}
        self._digest_cache: dict[str, tuple[str, str]] = {}
        self._diagnostics: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    # provenance

    def prov(self, at: str) -> dict[str, str]:
        return {
            "artifact_sha256": self.artifact_sha,
            "snapshot_id": self.snapshot_id,
            "pointer": at,
        }

    # diagnostics

    def diagnose(self, event: Event, region_pointer: str) -> str:
        kind, reason, ref, member = event
        key = (kind, reason, member, region_pointer)
        if key not in self._diagnostics:
            self._diagnostics[key] = {
                "kind": kind,
                "reason": reason,
                "ref": ref,
                "region_pointer": region_pointer,
                "provenance": self.prov(member),
            }
        return kind + "/" + reason

    def report(self, events: list[Event] | tuple[Event, ...], region_pointer: str) -> list[str]:
        return sorted({self.diagnose(event, region_pointer) for event in events})

    # references

    def follow(self, member: str, ref: str) -> tuple[Any, ...]:
        """Follow a Reference Object chain. ("ok", target, declared, chain) or ("fail", event)."""
        cached = self._follow_cache.get(ref)
        if cached is None:
            cached = self._follow_chain(ref)
            self._follow_cache[ref] = cached
        if cached[0] == "ok":
            return cached
        _, reason, failing_ref, hop_member = cached
        return "fail", ("unresolved_reference", reason, failing_ref, hop_member or member)

    def _follow_chain(self, ref: str) -> tuple[Any, ...]:
        chain: list[str] = []
        seen: set[str] = set()
        current = ref
        current_member = ""  # empty: the use site's own `$ref`
        while True:
            if self.expansions >= self.options.max_ref_expansions:
                reason = "reference_expansion_limit"
            else:
                kind, where = classify(current)
                if kind == "bad":
                    reason = where
                elif where in seen:
                    reason = "cyclic_reference"
                elif len(chain) >= self.options.max_ref_depth:
                    reason = "reference_depth_exceeded"
                else:
                    self.expansions += 1
                    target = resolve_pointer(self.doc, where)
                    if target is MISSING:
                        reason = "target_missing"
                    else:
                        chain.append(where)
                        seen.add(where)
                        if isinstance(target, dict) and isinstance(target.get("$ref"), str):
                            current_member = where + "/$ref"
                            current = target["$ref"]
                            continue
                        return "ok", target, where, tuple(chain)
            return "fail", reason, current, current_member

    def probe(self, ref: str) -> tuple[str, str]:
        """One step: ("ok", pointer) or ("bad", reason). Never follows chains."""
        cached = self._probe_cache.get(ref)
        if cached is not None:
            return cached
        result: tuple[str, str]
        if self.expansions >= self.options.max_ref_expansions:
            result = ("bad", "reference_expansion_limit")
        else:
            kind, where = classify(ref)
            if kind == "bad":
                result = ("bad", where)
            else:
                self.expansions += 1
                found = resolve_pointer(self.doc, where)
                result = ("bad", "target_missing") if found is MISSING else ("ok", where)
        self._probe_cache[ref] = result
        return result

    def scan(
        self, value: Any, base: str, skip_root_ref: bool = False
    ) -> tuple[list[Event], list[str]]:
        """Iterative sorted walk: unresolved `$ref` events and edges to resolved targets."""
        events: list[Event] = []
        edges: set[str] = set()
        stack: list[tuple[str, Any, bool]] = [(base, value, skip_root_ref)]
        while stack:
            at, node, skip = stack.pop()
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and not skip:
                    state, detail = self.probe(ref)
                    if state == "ok":
                        edges.add(detail)
                    else:
                        events.append(("unresolved_reference", detail, ref, at + "/$ref"))
                for key in sorted(node, reverse=True):
                    if _is_container(node[key]):
                        stack.append((at + pointer(key), node[key], False))
            elif isinstance(node, list):
                for index in range(len(node) - 1, -1, -1):
                    if _is_container(node[index]):
                        stack.append((at + "/" + str(index), node[index], False))
        return events, sorted(edges)

    def _node(self, where: str) -> tuple[tuple[Event, ...], tuple[str, ...]]:
        info = self._nodes.get(where)
        if info is None:
            events, edges = self.scan(resolve_pointer(self.doc, where), where)
            info = (tuple(sorted(set(events))), tuple(edges))
            self._nodes[where] = info
        return info

    def closure(self, start: str) -> tuple[Event, ...]:
        """Events of `start` and of everything reachable from it by resolved references.

        Iterative Tarjan: a component's result is memoized once, so a recursive schema or a
        shared component is walked once however many regions reach it.
        """
        memo = self._memo
        if start in memo:
            return memo[start]
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, at = work.pop()
            if at == 0 and node not in index:
                index[node] = low[node] = len(index)
                stack.append(node)
                on_stack.add(node)
            edges = self._node(node)[1]
            descended = False
            while at < len(edges):
                target = edges[at]
                at += 1
                if target in memo:
                    continue
                if target not in index:
                    work.append((node, at))
                    work.append((target, 0))
                    descended = True
                    break
                if target in on_stack:
                    low[node] = min(low[node], index[target])
            if descended:
                continue
            if low[node] == index[node]:
                members: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == node:
                        break
                inside = set(members)
                found: set[Event] = set()
                for member in members:
                    own, out = self._node(member)
                    found.update(own)
                    for target in out:
                        if target not in inside:
                            found.update(memo[target])
                frozen = tuple(sorted(found))
                for member in members:
                    memo[member] = frozen
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
        return memo[start]

    def region_events(
        self, value: Any, at: str, skip_root_ref: bool = False, local_only: bool = False
    ) -> list[Event]:
        events, edges = self.scan(value, at, skip_root_ref)
        found = set(events)
        if not local_only:
            for edge in edges:
                found.update(self.closure(edge))
        return sorted(found)

    def position_events(self, value: Any, at: str) -> list[Event]:
        """A position that follows a Reference Object chain (body, response, parameter)."""
        if isinstance(value, dict) and isinstance(value.get("$ref"), str):
            result = self.follow(at + "/$ref", value["$ref"])
            found: set[Event] = set(self.region_events(value, at, skip_root_ref=True))
            if result[0] == "fail":
                found.add(result[1])
            else:
                found.update(self.closure(result[2]))
            return sorted(found)
        return self.region_events(value, at)

    # parameters

    def parameter_digests(
        self, obj: dict[str, Any], declared: str, siblings: dict[str, Any], cache: bool
    ) -> tuple[str, str]:
        if cache and not siblings and declared in self._digest_cache:
            return self._digest_cache[declared]
        core = {k: v for k, v in obj.items() if k != "description" and not _is_extension(k)}
        core.pop("required", None)
        if obj.get("in") == "header" and isinstance(obj.get("name"), str):
            core["name"] = obj["name"].lower()
        full = dict(core, required=obj.get("required") is True)
        if siblings:
            result = (
                digest({"resolved": full, "siblings": siblings}),
                digest({"resolved": core, "siblings": siblings}),
            )
        else:
            result = (digest(full), digest(core))
            if cache:
                self._digest_cache[declared] = result
        return result

    def keyed(
        self, list_ptr: str, value: Any, level: str, names: list[str]
    ) -> tuple[dict[tuple[str, Any], dict[str, Any]], list[Event]]:
        params: dict[tuple[str, Any], dict[str, Any]] = {}
        events: list[Event] = []
        if value is MISSING:
            return params, events
        if not isinstance(value, list):
            return params, [("invalid_parameter", "not_an_array", None, list_ptr)]
        for index, item in enumerate(value):
            use = list_ptr + "/" + str(index)
            siblings: dict[str, Any] = {}
            chain: tuple[str, ...] = ()
            referenced = isinstance(item, dict) and isinstance(item.get("$ref"), str)
            if referenced:
                result = self.follow(use + "/$ref", item["$ref"])
                events.extend(self.region_events(item, use, skip_root_ref=True))
                if result[0] == "fail":
                    events.append(result[1])
                    continue
                obj, declared, chain = result[1], result[2], result[3]
                siblings = {
                    k: v
                    for k, v in item.items()
                    if k not in ("$ref", "summary", "description") and not _is_extension(k)
                }
                events.extend(self.closure(declared))
            else:
                obj, declared = item, use
                events.extend(self.region_events(obj, use))
            if not isinstance(obj, dict):
                events.append(("invalid_parameter", "not_an_object", None, use))
                continue
            name, location = obj.get("name"), obj.get("in")
            if not isinstance(name, str) or name == "":
                events.append(("invalid_parameter", "missing_name", None, use))
                continue
            if not isinstance(location, str) or location not in LOCATIONS:
                events.append(("invalid_parameter", "invalid_location", None, use))
                continue
            if "required" in obj and not isinstance(obj["required"], bool):
                events.append(("invalid_parameter", "invalid_required", None, use))
            if location == "path":
                if obj.get("required") is not True:
                    events.append(("invalid_parameter", "path_parameter_not_required", None, use))
                if name not in names:
                    events.append(
                        ("invalid_parameter", "path_parameter_not_in_template", None, use)
                    )
                    continue
                key: tuple[str, Any] = ("path", names.index(name))
                key_record: dict[str, Any] = {"in": "path", "ordinal": key[1]}
            else:
                shown = name.lower() if location == "header" else name
                key = (location, shown)
                key_record = {"in": location, "name": shown}
            if key in params:
                events.append(("invalid_parameter", "duplicate_parameter", None, use))
                continue
            full, without = self.parameter_digests(obj, declared, siblings, referenced)
            provenance = self.prov(use)
            provenance["declared_pointer"] = declared
            params[key] = {
                "key": key_record,
                "in": location,
                "name": name,
                "required": obj.get("required") is True,
                "level": level,
                "reserved_header": location == "header" and name.lower() in RESERVED_HEADERS,
                "sha256": full,
                "sha256_without_required": without,
                "provenance": {**provenance, "ref_chain": list(chain)},
            }
        return params, events


def _sorted_unique(items: list[str]) -> list[str]:
    return sorted(set(items))


def _status(reasons: list[str]) -> str:
    return "incomplete" if reasons else "complete"


def _operation_regions(
    build: _Builder,
    op: dict[str, Any],
    item: dict[str, Any],
    op_ptr: str,
    item_ptr: str,
    path_level: tuple[dict[tuple[str, Any], dict[str, Any]], list[Event]],
    names: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    regions: dict[str, Any] = {}
    reasons: list[str] = []
    value = op.get("parameters", MISSING)
    op_params, op_events = build.keyed(op_ptr + "/parameters", value, "operation", names)
    effective = dict(path_level[0])
    effective.update(op_params)
    params = sorted(effective.values(), key=lambda p: ev.canonical(p["key"]))
    listed = [
        [[p["key"].get("in"), p["key"].get("name", p["key"].get("ordinal"))], p["sha256"]]
        for p in params
    ]
    where = [
        at
        for at, present in (
            (item_ptr + "/parameters", "parameters" in item),
            (op_ptr + "/parameters", "parameters" in op),
        )
        if present
    ]
    kinds = build.report(list(path_level[1]) + op_events, op_ptr + "/parameters")
    regions["parameters"] = {
        "sha256": digest(listed),
        "status": _status(kinds),
        "pointers": sorted(where),
    }
    reasons.extend(kinds)

    for name in REGION_NAMES[1:]:
        at = op_ptr + pointer(name)
        events: list[Event]
        if name == "requestBody":
            events = build.position_events(op.get(name), at) if name in op else []
            shown: Any = op.get(name)
            pointers = [at] if name in op else []
        elif name == "responses":
            shown = op.get(name)
            pointers = [at] if name in op else []
            events = []
            if isinstance(shown, dict):
                for code in sorted(shown):
                    events.extend(build.position_events(shown[code], at + pointer(code)))
            elif name in op:
                events = build.region_events(shown, at)
        elif name == "deprecated":
            shown = op.get(name, False)
            pointers = [at] if name in op else []
            events = []
        elif name == "servers":
            shown = {"operation": op.get(name), "path_item": item.get(name)}
            pointers = [p for p, o in ((at, op), (item_ptr + "/servers", item)) if "servers" in o]
            events = []
            for p, o in ((at, op), (item_ptr + "/servers", item)):
                if "servers" in o:
                    events.extend(build.region_events(o["servers"], p))
        elif name == "other":
            unknown_op = {
                k: v for k, v in op.items() if k not in _OPERATION_KNOWN and not _is_extension(k)
            }
            unknown_item = {
                k: v
                for k, v in item.items()
                if k not in METHODS and k not in _PATH_ITEM_KNOWN and not _is_extension(k)
            }
            shown = {"operation": unknown_op, "path_item": unknown_item}
            pointers = [op_ptr + pointer(k) for k in sorted(unknown_op)] + [
                item_ptr + pointer(k) for k in sorted(unknown_item)
            ]
            events = []
            for k in sorted(unknown_op):
                events.extend(build.region_events(unknown_op[k], op_ptr + pointer(k)))
            for k in sorted(unknown_item):
                events.extend(build.region_events(unknown_item[k], item_ptr + pointer(k)))
            at = op_ptr
        else:
            shown = op.get(name)
            pointers = [at] if name in op else []
            events = build.region_events(shown, at) if name in op else []
        kinds = build.report(events, at)
        regions[name] = {
            "sha256": digest(shown),
            "status": _status(kinds),
            "pointers": sorted(pointers),
        }
        reasons.extend(kinds)
    return regions, params, _sorted_unique(reasons)


def _import_paths(build: _Builder) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = build.doc.get("paths")
    items: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    if not isinstance(paths, dict):
        return items, operations
    keys = sorted(k for k in paths if not _is_extension(k))
    if len(keys) > build.options.max_operations:
        raise OpenAPIRejected("limit_exceeded")
    for key in keys:
        use = "/paths" + pointer(key)
        template = path_template(key)
        if template is None:
            kinds = build.report([("invalid_path_template", "malformed_template", None, use)], use)
            items.append(
                {
                    "path_template": key,
                    "identity_path": None,
                    "declared_pointer": None,
                    "sha256": None,
                    "status": "incomplete",
                    "reasons": kinds,
                    "provenance": build.prov(use),
                }
            )
            continue
        names, identity_path = template
        value = paths[key]
        events: list[Event] = []
        declared: str | None = use
        item: Any = value
        if isinstance(value, dict) and isinstance(value.get("$ref"), str):
            result = build.follow(use + "/$ref", value["$ref"])
            events.extend(build.region_events(value, use, skip_root_ref=True))
            if result[0] == "fail":
                events.append(result[1])
                item, declared = None, None
            else:
                item, declared = result[1], result[2]
                events.extend(build.closure(declared))
                extra = [
                    k
                    for k in value
                    if k not in ("$ref", "summary", "description") and not _is_extension(k)
                ]
                if extra:
                    events.append(("invalid_path_item", "reference_with_siblings", None, use))
        if declared is not None and not isinstance(item, dict):
            events.append(("invalid_path_item", "not_an_object", None, use))
            item = None
        record: dict[str, Any] = {
            "path_template": key,
            "identity_path": identity_path,
            "declared_pointer": declared if item is not None else None,
            "sha256": None,
            "provenance": build.prov(use),
        }
        if item is None:
            kinds = build.report(events, use)
            record.update(status=_status(kinds), reasons=kinds)
            items.append(record)
            continue
        item_ptr = str(declared)
        path_level = build.keyed(
            item_ptr + "/parameters", item.get("parameters", MISSING), "path_item", names
        )
        unknown_item = {
            k: v
            for k, v in item.items()
            if k not in METHODS and k not in _PATH_ITEM_KNOWN and not _is_extension(k)
        }
        record["sha256"] = digest(
            {
                "parameters": item.get("parameters"),
                "servers": item.get("servers"),
                "other": unknown_item,
            }
        )
        found = [m for m in METHODS if m in item]
        if not found:
            events.extend(path_level[1])
            for k in sorted(unknown_item):
                events.extend(build.region_events(unknown_item[k], item_ptr + pointer(k)))
            if "servers" in item:
                events.extend(build.region_events(item["servers"], item_ptr + "/servers"))
        kinds = build.report(events, use)
        record.update(status=_status(kinds), reasons=kinds)
        items.append(record)
        for method in found:
            op = item[method]
            op_ptr = item_ptr + pointer(method)
            identity = {"method": method.upper(), "path": identity_path}
            base = {
                "identity": identity,
                "path_template": key,
                "operation_id": None,
                "path_item_pointer": use,
                "provenance": build.prov(op_ptr),
                "parameters": [],
                "regions": {},
            }
            if len(operations) >= build.options.max_operations:
                raise OpenAPIRejected("limit_exceeded")
            if not isinstance(op, dict):
                reasons = build.report(
                    [("invalid_operation", "not_an_object", None, op_ptr)], op_ptr
                )
                operations.append({**base, "status": "incomplete", "reasons": reasons})
                continue
            listed = 0
            for holder in (item, op):
                if isinstance(holder.get("parameters"), list):
                    listed += len(holder["parameters"])
            if listed > build.options.max_parameters_per_operation:
                raise OpenAPIRejected("limit_exceeded")
            regions, params, reasons = _operation_regions(
                build, op, item, op_ptr, item_ptr, path_level, names
            )
            operation_id = op.get("operationId")
            operations.append(
                {
                    **base,
                    "operation_id": operation_id if isinstance(operation_id, str) else None,
                    "status": _status(reasons),
                    "reasons": reasons,
                    "parameters": params,
                    "regions": regions,
                }
            )
    return items, operations


def _document_regions(build: _Builder) -> list[dict[str, Any]]:
    doc = build.doc
    targets: list[tuple[str, Any]] = []
    for name in ("jsonSchemaDialect", "servers", "security"):
        if name in doc:
            targets.append(("/" + name, doc[name]))
    webhooks = doc.get("webhooks")
    if isinstance(webhooks, dict):
        for name in sorted(webhooks):
            targets.append(("/webhooks" + pointer(name), webhooks[name]))
    components = doc.get("components")
    if isinstance(components, dict):
        for kind in sorted(k for k in components if not _is_extension(k)):
            entries = components[kind]
            if isinstance(entries, dict):
                for name in sorted(entries):
                    targets.append(("/components" + pointer(kind, name), entries[name]))
            else:
                targets.append(("/components" + pointer(kind), entries))
    for name in sorted(doc):
        if name not in _TOP_KNOWN and not _is_extension(name):
            targets.append((pointer(name), doc[name]))
    regions = []
    for at, value in sorted(targets, key=lambda pair: pair[0]):
        kinds = build.report(build.region_events(value, at, local_only=True), at)
        regions.append(
            {
                "pointer": at,
                "sha256": digest(value),
                "status": _status(kinds),
                "provenance": build.prov(at),
            }
        )
    return regions


def _mark_ambiguous(build: _Builder, operations: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in operations:
        identity = record["identity"]
        groups.setdefault((identity["method"], identity["path"]), []).append(record)
    for records in groups.values():
        if len(records) < 2:
            continue
        for record in records:
            at = record["path_item_pointer"]
            event: Event = ("ambiguous_identity", "normalized_identity_collision", None, at)
            kinds = build.report([event], at)
            record["status"] = "incomplete"
            record["reasons"] = _sorted_unique(record["reasons"] + kinds)


def _check_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise OpenAPIRejected("not_an_object")
    if "$ref" in document:
        raise OpenAPIRejected("root_reference")
    if "swagger" in document or not profile_supported(document.get("openapi")):
        raise OpenAPIRejected("unsupported_profile")
    for name in ("paths", "components", "webhooks"):
        if name in document and not isinstance(document[name], dict):
            raise OpenAPIRejected("invalid_document")
    if "jsonSchemaDialect" in document and not isinstance(document["jsonSchemaDialect"], str):
        raise OpenAPIRejected("invalid_document")
    return document


def import_document(raw: bytes, options: Options | None = None) -> dict[str, Any]:
    """Import one OpenAPI 3.1 JSON document. Raises `OpenAPIRejected` for a refused document."""
    options = options or Options()
    document = _check_document(parse(raw, options))
    artifact_sha = hashlib.sha256(raw).hexdigest()
    snapshot_id = snapshot_identity(artifact_sha, options)
    build = _Builder(document, options, artifact_sha, snapshot_id)
    path_items, operations = _import_paths(build)
    _mark_ambiguous(build, operations)
    regions = _document_regions(build)
    operations.sort(
        key=lambda r: (r["identity"]["path"], r["identity"]["method"], r["path_template"])
    )
    ordered = sorted(
        build._diagnostics.values(),
        key=lambda d: (d["region_pointer"], d["provenance"]["pointer"], d["kind"], d["reason"]),
    )
    emitted = ordered[: options.max_diagnostics]
    unresolved = sum(1 for d in ordered if d["kind"] == "unresolved_reference")
    return {
        "format": FORMAT,
        "profile": PROFILE,
        "options": options.as_dict(),
        "artifact": {
            "sha256": artifact_sha,
            "bytes": len(raw),
            "openapi": document["openapi"],
            "json_schema_dialect": document.get("jsonSchemaDialect"),
            "paths_present": "paths" in document,
        },
        "snapshot_id": snapshot_id,
        "status": "incomplete" if ordered else "complete",
        "path_items": path_items,
        "operations": operations,
        "document_regions": regions,
        "diagnostics": emitted,
        "diagnostics_total": len(ordered),
        "counts": {
            "path_items": len(path_items),
            "operations": len(operations),
            "parameters": sum(len(r["parameters"]) for r in operations),
            "references_followed": build.expansions,
            "references_unresolved": unresolved,
        },
        "limitations": list(LIMITATIONS),
    }


# ---- validation -----------------------------------------------------------------------------


class SnapshotInvalid(ValueError):
    """A snapshot that is not a well-formed `atlas-contract-snapshot.v1`."""


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise SnapshotInvalid(what)


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _check_provenance(value: Any, snapshot: dict[str, Any]) -> None:
    _require(isinstance(value, dict) and set(value) >= {"artifact_sha256", "snapshot_id"}, "prov")
    _require(value["artifact_sha256"] == snapshot["artifact"]["sha256"], "prov_artifact")
    _require(value["snapshot_id"] == snapshot["snapshot_id"], "prov_snapshot")
    _require(isinstance(value.get("pointer"), str), "prov_pointer")


def validate_snapshot(snapshot: Any) -> None:
    """Raise `SnapshotInvalid` unless `snapshot` is a closed, ordered, self-consistent snapshot."""
    try:
        _validate_snapshot(snapshot)
    except (KeyError, TypeError, IndexError, AttributeError):
        raise SnapshotInvalid("shape") from None


def _validate_snapshot(snapshot: Any) -> None:
    _require(isinstance(snapshot, dict), "type")
    keys = {
        "format", "profile", "options", "artifact", "snapshot_id", "status", "path_items",
        "operations", "document_regions", "diagnostics", "diagnostics_total", "counts",
        "limitations",
    }  # fmt: skip
    _require(set(snapshot) == keys, "keys")
    _require(snapshot["format"] == FORMAT and snapshot["profile"] == PROFILE, "format")
    _require(snapshot["limitations"] == list(LIMITATIONS), "limitations")
    opts = snapshot["options"]
    _require(isinstance(opts, dict) and set(opts) == set(LIMITS), "options")
    for name, (_, low, high) in LIMITS.items():
        _require(
            isinstance(opts[name], int)
            and not isinstance(opts[name], bool)
            and low <= opts[name] <= high,
            "option_range",
        )
    artifact = snapshot["artifact"]
    _require(
        isinstance(artifact, dict)
        and set(artifact) == {"sha256", "bytes", "openapi", "json_schema_dialect", "paths_present"}
        and _is_sha(artifact["sha256"])
        and isinstance(artifact["bytes"], int)
        and profile_supported(artifact["openapi"])
        and isinstance(artifact["paths_present"], bool),
        "artifact",
    )
    expected = snapshot_identity(artifact["sha256"], Options(**opts))
    _require(snapshot["snapshot_id"] == expected, "snapshot_id")
    _require(snapshot["status"] in STATUSES, "status")
    valid_reasons = {k + "/" + r for k, rs in DIAGNOSTIC_REASONS.items() for r in rs}
    for record in snapshot["path_items"]:
        _require(
            set(record) == {
                "path_template", "identity_path", "declared_pointer", "sha256", "status",
                "reasons", "provenance",
            },
            "path_item_keys",
        )  # fmt: skip
        _require(record["status"] == _status(record["reasons"]), "path_item_status")
        _require(set(record["reasons"]) <= valid_reasons, "path_item_reasons")
        _check_provenance(record["provenance"], snapshot)
    templates = [r["path_template"] for r in snapshot["path_items"]]
    _require(templates == sorted(templates), "path_item_order")
    order = []
    for record in snapshot["operations"]:
        _require(
            set(record) == {
                "identity", "path_template", "operation_id", "path_item_pointer", "status",
                "reasons", "provenance", "parameters", "regions",
            },
            "operation_keys",
        )  # fmt: skip
        identity = record["identity"]
        _require(identity["method"] in [m.upper() for m in METHODS], "method")
        _require(isinstance(identity["path"], str), "identity_path")
        _require(record["status"] == _status(record["reasons"]), "operation_status")
        _require(set(record["reasons"]) <= valid_reasons, "operation_reasons")
        _check_provenance(record["provenance"], snapshot)
        regions = record["regions"]
        if regions:
            _require(list(regions) == list(REGION_NAMES), "region_names")
            for region in regions.values():
                _require(_is_sha(region["sha256"]) and region["status"] in STATUSES, "region")
                _require(region["pointers"] == sorted(region["pointers"]), "region_pointers")
        else:
            _require(record["reasons"] != [], "empty_regions")
        keys_seen = [ev.canonical(p["key"]) for p in record["parameters"]]
        _require(keys_seen == sorted(keys_seen) and len(set(keys_seen)) == len(keys_seen), "params")
        for param in record["parameters"]:
            _require(param["in"] in LOCATIONS and param["level"] in LEVELS, "param_enum")
            _require(_is_sha(param["sha256"]) and _is_sha(param["sha256_without_required"]), "p")
            _check_provenance(param["provenance"], snapshot)
        order.append((identity["path"], identity["method"], record["path_template"]))
    _require(order == sorted(order), "operation_order")
    pointers = [r["pointer"] for r in snapshot["document_regions"]]
    _require(pointers == sorted(pointers), "document_region_order")
    for region in snapshot["document_regions"]:
        _require(_is_sha(region["sha256"]) and region["status"] in STATUSES, "document_region")
        _check_provenance(region["provenance"], snapshot)
    diagnostics = snapshot["diagnostics"]
    _require(len(diagnostics) <= snapshot["diagnostics_total"], "diagnostics_total")
    _require(len(diagnostics) <= opts["max_diagnostics"], "diagnostics_bound")
    sort_keys = []
    for item in diagnostics:
        _require(item["reason"] in DIAGNOSTIC_REASONS.get(item["kind"], ()), "diagnostic_enum")
        _check_provenance(item["provenance"], snapshot)
        sort_keys.append(
            (item["region_pointer"], item["provenance"]["pointer"], item["kind"], item["reason"])
        )
    _require(sort_keys == sorted(sort_keys), "diagnostic_order")
    _require((snapshot["status"] == "complete") == (snapshot["diagnostics_total"] == 0), "complete")
