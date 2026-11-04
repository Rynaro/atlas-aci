"""Offline evidence report: one validated context envelope to canonical JSON and static HTML.

A pure transformation. It takes the bytes of one `atlas.graph.v1` envelope that a server
already produced (`operation == "context"`, `status == "ok"`, `projection == "task_context"`),
validates it with the shipped schema and the shipped semantic validator, removes the live
capability credentials (lease URIs and continuation cursors), and renders two artifacts:

- a canonical JSON report that keeps every identity, marker and the validated envelope, and
- a self-contained HTML page whose every dynamic value is escaped text.

No retrieval call is made: the module reads no repository, opens no reader and follows no
cursor. It has no clock, locale, environment, path or random input, so equal input and equal
rendering version give byte-identical output.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import html
import json
from importlib import resources
from string import Template
from typing import Any

from atlas_aci import config
from atlas_aci.protocol import graph
from atlas_aci.storage import evidence as ev

REPORT_FORMAT = "atlas-evidence-report.v1"
RENDERING_VERSION = "atlas-evidence-render.1"
OPTIONS = {
    "control_codepoints": "visible_placeholder",
    "credential_fields": "omitted",
    "html_excerpts": "escaped_text",
}
CODES = (
    "envelope_too_large",
    "invalid_input",
    "invalid_envelope",
    "unsupported_operation",
    "mixed_snapshot_evidence",
    "report_too_large",
)
MAX_INPUT_FILE_BYTES = 4 * 1024 * 1024
MAX_ENVELOPE_BYTES = config.DEFAULT_MAX_RESPONSE_BYTES
ELEMENT_LIMIT = config.DEFAULT_MAX_BOUND_FIELD_ELEMENTS
MAX_JSON_REPORT_BYTES = 3 * 1024 * 1024
MAX_HTML_REPORT_BYTES = 8 * 1024 * 1024
TEMPLATE_IDENTIFIERS = ["identity", "markers", "omitted", "bundles", "sources"]
IDENTITY_POINTERS = (
    ("schema_version", "/schema_version"),
    ("operation", "/operation"),
    ("status", "/status"),
    ("evidence_status", "/evidence_status"),
    ("generation_id", "/generation_id"),
    ("snapshot_id", "/snapshot_id"),
    ("repository", "/data/repository"),
    ("retrieval_record_id", "/data/retrieval_record_id"),
    ("query_digest", "/data/query_digest"),
    ("ranker", "/data/ranker"),
    ("request_text", "/data/request/text"),
    ("source_policy", "/data/request/source_policy"),
)
OMITTED_REASON = "capability_credential"


class ReportRejected(Exception):
    """A rejected input or output. `str()` is the closed code; nothing else is carried."""

    def __init__(self, code: str) -> None:
        if code not in CODES:
            raise ValueError("unknown rejection code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code


@dataclasses.dataclass(frozen=True)
class Report:
    html: bytes
    json: bytes


# ---- input pipeline -------------------------------------------------------------------------


def _no_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def load_envelope(raw: bytes) -> dict[str, Any]:
    """Validate raw bytes into an accepted context envelope, or raise `ReportRejected`.

    Every failure of every step maps to that step's closed code: no other exception type
    (recursion, overflow, unicode, schema or semantic errors) can leave this function.
    """
    if not isinstance(raw, bytes | bytearray):
        raise ReportRejected("invalid_input")
    if len(raw) > MAX_INPUT_FILE_BYTES:
        raise ReportRejected("envelope_too_large")
    try:
        envelope = json.loads(
            bytes(raw).decode("utf-8"),
            parse_constant=_no_constant,
            object_pairs_hook=_no_duplicates,
        )
        size = len(ev.canonical(envelope).encode("utf-8"))
    except Exception:
        raise ReportRejected("invalid_input") from None
    if size > MAX_ENVELOPE_BYTES:
        raise ReportRejected("envelope_too_large")
    try:
        valid = isinstance(envelope, dict) and graph.STRUCTURED_VALIDATOR.is_valid(envelope)
    except Exception:
        valid = False
    if not valid:
        raise ReportRejected("invalid_envelope")
    data = envelope.get("data")
    if (
        envelope.get("operation") != "context"
        or envelope.get("status") != "ok"
        or not isinstance(data, dict)
        or data.get("projection") != "task_context"
    ):
        raise ReportRejected("unsupported_operation")
    try:
        mixed = _mixed_snapshot(envelope)
    except Exception:
        raise ReportRejected("invalid_envelope") from None
    if mixed:
        raise ReportRejected("mixed_snapshot_evidence")
    try:
        limit = envelope["bounds"]["element_limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit > ELEMENT_LIMIT:
            raise ValueError("element limit above the default")
        graph.validate_context(envelope, "context", min(ELEMENT_LIMIT, limit))
    except Exception:
        raise ReportRejected("invalid_envelope") from None
    return envelope


def _mixed_snapshot(envelope: dict[str, Any]) -> bool:
    """True when any per-item generation/snapshot pair differs from the declared pair.

    Null compares as a value. A context envelope has exactly two places that carry a
    per-item pair: `data` and each `data.resources[]` descriptor.
    """
    declared = (envelope["generation_id"], envelope["snapshot_id"])
    data = envelope["data"]
    pairs = [(data["generation_id"], data["snapshot_id"])]
    pairs.extend((item["generation_id"], item["snapshot_id"]) for item in data["resources"])
    return any(pair != declared for pair in pairs)


# ---- credentials ----------------------------------------------------------------------------


def _strip(envelope: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    stripped = copy.deepcopy(envelope)
    data = stripped["data"]
    cursor = data.pop("next_cursor")
    omitted = [
        {
            "pointer": "/data/next_cursor",
            "reason": OMITTED_REASON,
            "value": "null" if cursor is None else "redacted",
        }
    ]
    for index, resource in enumerate(data["resources"]):
        del resource["uri"]
        omitted.append(
            {
                "pointer": f"/data/resources/{index}/uri",
                "reason": OMITTED_REASON,
                "value": "redacted",
            }
        )
    return stripped, omitted


# ---- JSON report ----------------------------------------------------------------------------


def _escape_part(part: Any) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def pointer(*parts: Any) -> str:
    """RFC 6901 pointer for the given path parts."""
    return "".join("/" + _escape_part(part) for part in parts)


def resolve_pointer(document: Any, pointer_text: str) -> Any:
    """Resolve an RFC 6901 pointer produced by `pointer`; raises LookupError when absent."""
    node = document
    for token in pointer_text.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(key)]
        elif isinstance(node, dict):
            node = node[key]
        else:
            raise LookupError(pointer_text)
    return node


def _unique_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for bundle in data["bundles"]:
        for source in bundle["sources"]:
            seen.setdefault(source["id"], source)
    return list(seen.values())


def _hops(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    records = bundle["records"]
    if bundle["kind"] == "relationship":
        return [records["proof"]]
    if bundle["kind"] == "path":
        return list(records["hops"])
    return []


def _hop_parts(bundle_index: int, bundle: dict[str, Any]) -> list[tuple[Any, ...]]:
    base = ("data", "bundles", bundle_index, "records")
    if bundle["kind"] == "relationship":
        return [(*base, "proof")]
    if bundle["kind"] == "path":
        return [(*base, "hops", k) for k in range(len(bundle["records"]["hops"]))]
    return []


def _marker_parts(envelope: dict[str, Any]) -> list[tuple[Any, ...]]:
    data = envelope["data"]
    parts: list[tuple[Any, ...]] = [
        ("evidence_status",),
        ("bounds", "element_limit"),
        ("bounds", "truncated"),
        ("bounds", "lists"),
        ("data", "selection", "total_count"),
        ("data", "selection", "has_more"),
        ("data", "selection", "reason"),
        ("data", "selection_truncated"),
        ("data", "selection_reason"),
        ("data", "has_more"),
        ("data", "total_count"),
        ("data", "retrieval", "truncated"),
        ("data", "retrieval", "truncation_reason"),
        ("data", "retrieval", "diagnostic_reason"),
        ("data", "collection_stop"),
        ("data", "coverage", "state"),
        ("data", "coverage", "source_policy"),
        ("data", "coverage", "excluded_counts"),
        ("data", "effective_limits"),
        ("data", "work"),
    ]
    for index in range(len(data["frontiers"])):
        for key in ("outcome", "stop_reason", "target_state"):
            parts.append(("data", "frontiers", index, key))
    for index, bundle in enumerate(data["bundles"]):
        parts.append(("data", "bundles", index, "rank", "source_unavailable"))
        for j in range(len(bundle["sources"])):
            for key in ("state", "reason", "inline_reason"):
                parts.append(("data", "bundles", index, "sources", j, key))
        for hop in _hop_parts(index, bundle):
            for key in ("resolution", "unresolved_reason", "candidate_ids"):
                parts.append((*hop, "assertion", key))
            parts.append((*hop, "source_availability"))
    return parts


def _markers(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    markers = []
    for parts in _marker_parts(envelope):
        ptr = pointer(*parts)
        try:
            value = resolve_pointer(envelope, ptr)
        except LookupError:
            continue
        markers.append({"name": ptr[1:].replace("/", "."), "pointer": ptr, "value": value})
    return markers


def _hop_view(index: int, hop: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    assertion = hop.get("assertion") or {}
    occurrence = hop.get("occurrence") or {}
    evidence = hop.get("evidence") or {}
    return {
        "index": index,
        "assertion_id": assertion.get("id"),
        "relation": assertion.get("relation"),
        "traversal_direction": hop.get("traversal_direction"),
        "resolution": assertion.get("resolution"),
        "unresolved_reason": assertion.get("unresolved_reason"),
        "raw_target": assertion.get("raw_target"),
        "source_entity_id": assertion.get("source_entity_id"),
        "target_entity_id": assertion.get("target_entity_id"),
        "evidence_id": evidence.get("id"),
        "occurrence_id": occurrence.get("id"),
        "producer_id": evidence.get("producer_id"),
        "path": occurrence.get("path"),
        "line": occurrence.get("line"),
        "column": occurrence.get("column"),
        "end_line": occurrence.get("end_line"),
        "end_column": occurrence.get("end_column"),
        "start_byte": evidence.get("start_byte"),
        "end_byte": evidence.get("end_byte"),
        "source_sha256": evidence.get("source_sha256"),
        "source_id": sources[index]["id"] if index < len(sources) else None,
        "source_availability": hop.get("source_availability"),
    }


def _bundle_view(index: int, bundle: dict[str, Any]) -> dict[str, Any]:
    records = bundle["records"]
    occurrence = records.get("occurrence") if bundle["kind"] == "definition" else None
    location = None
    if occurrence is not None:
        location = {
            key: occurrence.get(key) for key in ("path", "line", "column", "end_line", "end_column")
        }
    return {
        "index": index,
        "id": bundle["id"],
        "kind": bundle["kind"],
        "rank": bundle["rank"],
        "source_ids": [source["id"] for source in bundle["sources"]],
        "location": location,
        "hops": [_hop_view(k, hop, bundle["sources"]) for k, hop in enumerate(_hops(bundle))],
    }


def _source_view(source: dict[str, Any], resources_: list[dict[str, Any]]) -> dict[str, Any]:
    inline = source.get("inline")
    return {
        "id": source["id"],
        "kind": source.get("kind"),
        "state": source.get("state"),
        "reason": source.get("reason"),
        "path": source.get("path"),
        "start_byte": source.get("start_byte"),
        "end_byte": source.get("end_byte"),
        "source_sha256": source.get("source_sha256"),
        "source_size": source.get("source_size"),
        "provenance_state": source.get("provenance_state"),
        "inline_encoding": None if inline is None else inline.get("encoding"),
        "inline_reason": source.get("inline_reason"),
        "resource_ids": [
            item["id"] for item in resources_ if item.get("source_reference_id") == source["id"]
        ],
    }


def build_report(envelope: dict[str, Any]) -> dict[str, Any]:
    """Report dict for an envelope accepted by `load_envelope`. The input is not mutated."""
    stripped, omitted = _strip(envelope)
    data = stripped["data"]
    identity = {name: resolve_pointer(stripped, ptr) for name, ptr in IDENTITY_POINTERS}
    return {
        "report_format": REPORT_FORMAT,
        "rendering_version": RENDERING_VERSION,
        "options": dict(OPTIONS),
        "envelope_sha256": hashlib.sha256(ev.canonical(stripped).encode("utf-8")).hexdigest(),
        "omitted_fields": omitted,
        "envelope": stripped,
        "view": {
            "identity": identity,
            "markers": _markers(stripped),
            "bundles": [_bundle_view(i, b) for i, b in enumerate(data["bundles"])],
            "sources": [_source_view(s, data["resources"]) for s in _unique_sources(data)],
        },
    }


def render_json(report: dict[str, Any]) -> bytes:
    return ev.canonical(report).encode("utf-8") + b"\n"


# ---- HTML report ----------------------------------------------------------------------------

# Fixed codepoint table, so output never depends on the Python Unicode database version.
_PLACEHOLDER_RANGES = (
    (0x0000, 0x0008),
    (0x000B, 0x001F),
    (0x007F, 0x009F),
    (0x00AD, 0x00AD),
    (0x061C, 0x061C),
    (0x180E, 0x180E),
    (0x200B, 0x200F),
    (0x2028, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x206F),
    (0xFEFF, 0xFEFF),
    (0xFFF9, 0xFFFB),
    (0xE0001, 0xE0001),
    (0xE0020, 0xE007F),
)
PLACEHOLDERS = {
    codepoint: f'<span class="cp">U+{codepoint:04X}</span>'
    for low, high in _PLACEHOLDER_RANGES
    for codepoint in range(low, high + 1)
}
_HEX = frozenset("0123456789abcdef")


def text(value: Any) -> str:
    """The single escaping function: every dynamic value passes through here."""
    if value is None:
        return "none"
    raw = value if isinstance(value, str) else ev.canonical(value)
    return html.escape(raw, quote=True).translate(PLACEHOLDERS)


def _raw(value: Any) -> str:
    if value is None:
        return "none"
    return value if isinstance(value, str) else ev.canonical(value)


def _hex_id(identifier: Any) -> str:
    if not (isinstance(identifier, str) and len(identifier) == 64 and set(identifier) <= _HEX):
        raise ReportRejected("invalid_envelope")
    return identifier


def _location(item: dict[str, Any]) -> str:
    return text(
        f"{_raw(item.get('path'))}:{_raw(item.get('line'))}:{_raw(item.get('column'))}"
        f"-{_raw(item.get('end_line'))}:{_raw(item.get('end_column'))}"
    )


def _byte_range(item: dict[str, Any]) -> str:
    return text(f"bytes [{_raw(item.get('start_byte'))}, {_raw(item.get('end_byte'))})")


def _dl(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<dt>{text(label)}</dt><dd>{value}</dd>\n" for label, value in rows)
    return f"<dl>\n{body}</dl>"


def _table(head: list[str], rows: list[tuple[str, list[str]]]) -> str:
    cells = "".join(f"<th>{text(label)}</th>" for label in head)
    lines = [f"<table>\n<thead><tr>{cells}</tr></thead>\n<tbody>"]
    for attribute, values in rows:
        tds = "".join(f"<td>{value}</td>" for value in values)
        lines.append(f"<tr{attribute}>{tds}</tr>")
    lines.append("</tbody>\n</table>")
    return "\n".join(lines)


def _identity_html(report: dict[str, Any], json_bytes: bytes) -> str:
    rows = [
        ("report_format", text(report["report_format"])),
        ("rendering_version", text(report["rendering_version"])),
        ("options", text(report["options"])),
        ("envelope_sha256", text(report["envelope_sha256"])),
        ("json_report_sha256", text(hashlib.sha256(json_bytes).hexdigest())),
    ]
    rows.extend((name, text(value)) for name, value in report["view"]["identity"].items())
    data = report["envelope"]["data"]
    rows.append(("tokenizer (diagnostic)", text(data["tokenizer"])))
    rows.append(("token_usage (diagnostic)", text(data["token_usage"])))
    return (
        '<header id="section-identity">\n<h1>ATLAS ACI evidence report</h1>\n'
        + _dl(rows)
        + "\n</header>"
    )


def _markers_html(report: dict[str, Any]) -> str:
    rows = [
        (
            f' id="marker-{n}"',
            [text(n), text(marker["name"]), text(marker["pointer"]), text(marker["value"])],
        )
        for n, marker in enumerate(report["view"]["markers"])
    ]
    return (
        '<section id="section-markers">\n<h2>Markers</h2>\n'
        "<p>Values are copied from the input envelope. A value of none means the input value "
        "is null; an absent key is not listed.</p>\n"
        + _table(["#", "name", "pointer", "value"], rows)
        + "\n</section>"
    )


def _omitted_html(report: dict[str, Any]) -> str:
    items = "".join(
        f"<li>{text(item['pointer'])} - {text(item['reason'])} - {text(item['value'])}</li>\n"
        for item in report["omitted_fields"]
    )
    body = f"<ul>\n{items}</ul>" if items else "<p>none</p>"
    return (
        '<section id="section-omitted">\n<h2>Omitted fields</h2>\n'
        "<p>Live capability credentials are removed from both artifacts. A value of null means "
        "the input carried no cursor; redacted means a credential was present and removed.</p>\n"
        + body
        + "\n</section>"
    )


def _node_line(node: dict[str, Any]) -> str:
    return " | ".join(text(node.get(key)) for key in ("id", "name", "kind", "path"))


def _hop_row(bundle_index: int, hop: dict[str, Any]) -> tuple[str, list[str]]:
    source_id = hop["source_id"]
    link = "none"
    if source_id is not None:
        link = f'<a href="#source-{_hex_id(source_id)}">source</a>'
    return (
        f' id="bundle-{bundle_index}-hop-{hop["index"]}"',
        [
            text(hop["relation"]),
            text(hop["traversal_direction"]),
            text(hop["resolution"]),
            text(hop["unresolved_reason"]),
            text(hop["raw_target"]),
            text(hop["source_entity_id"]),
            text(hop["target_entity_id"]),
            _location(hop),
            _byte_range(hop),
            text(hop["source_sha256"]),
            text(hop["source_availability"]),
            link,
        ],
    )


HOP_HEAD = [
    "relation",
    "direction",
    "resolution",
    "unresolved_reason",
    "raw_target",
    "source_entity",
    "target_entity",
    "location",
    "bytes",
    "source_sha256",
    "source_availability",
    "source",
]


def _bundle_html(index: int, view: dict[str, Any], bundle: dict[str, Any]) -> str:
    records = bundle["records"]
    rows = [
        ("kind", text(view["kind"])),
        ("id", text(view["id"])),
        ("rank", text(view["rank"])),
    ]
    nodes: list[dict[str, Any]] = []
    if view["kind"] == "definition":
        nodes = [records["entity"]]
        location = view["location"]
        rows.append(("location", "none" if location is None else _location(location)))
    elif view["kind"] in {"relationship", "path"}:
        nodes = list(records["nodes"])
    else:
        provenance = records.get("provenance") or {}
        rows.append(("path", text(provenance.get("path"))))
        rows.append(("match_tier", text(records.get("match_tier"))))
        rows.append(("score", text(records.get("score"))))
    if nodes:
        items = "".join(f"<li>{_node_line(node)}</li>\n" for node in nodes)
        rows.append(("nodes (id | name | kind | path)", f"<ol>\n{items}</ol>"))
    links = "".join(
        f'<li><a href="#source-{_hex_id(source_id)}">{text(source_id)}</a></li>\n'
        for source_id in view["source_ids"]
    )
    rows.append(("sources", f"<ul>\n{links}</ul>" if links else "none"))
    hops = view["hops"]
    hop_table = (
        _table(HOP_HEAD, [_hop_row(index, hop) for hop in hops]) if hops else "<p>hops: none</p>"
    )
    return (
        f'<article id="bundle-{index}">\n<h3>Bundle {index}: {text(view["kind"])}</h3>\n'
        + _dl(rows)
        + "\n"
        + hop_table
        + "\n</article>"
    )


def _bundles_html(report: dict[str, Any]) -> str:
    bundles = report["envelope"]["data"]["bundles"]
    body = "\n".join(
        _bundle_html(i, view, bundles[i]) for i, view in enumerate(report["view"]["bundles"])
    )
    return (
        '<section id="section-bundles">\n<h2>Evidence bundles</h2>\n'
        + (body if body else "<p>none</p>")
        + "\n</section>"
    )


def _excerpt_html(source: dict[str, Any]) -> str:
    inline = source.get("inline")
    if inline is None:
        reason = source.get("inline_reason")
        shown = reason if reason is not None else source.get("reason")
        return f"<p>excerpt not included: {text(shown)}</p>"
    # Browsers drop the first LF after the opening pre tag, so one extra LF is emitted.
    if inline["encoding"] == "utf8":
        return f'<pre class="excerpt">\n{text(inline["data"])}</pre>'
    return f'<p>base64, not decoded</p>\n<pre class="b64">\n{text(inline["data"])}</pre>'


def _sources_html(report: dict[str, Any]) -> str:
    data = report["envelope"]["data"]
    parts = []
    for source, view in zip(_unique_sources(data), report["view"]["sources"], strict=True):
        rows = [
            ("id", text(view["id"])),
            ("kind", text(view["kind"])),
            ("state", text(view["state"])),
            ("reason", text(view["reason"])),
            ("path", text(view["path"])),
            ("byte range", _byte_range(view)),
            ("source_sha256", text(view["source_sha256"])),
            ("source_size", text(view["source_size"])),
            ("provenance_state", text(view["provenance_state"])),
            ("inline_reason", text(view["inline_reason"])),
            ("resource_ids", text(view["resource_ids"])),
        ]
        parts.append(
            f'<article id="source-{_hex_id(view["id"])}">\n<h3>Source {text(view["id"])}</h3>\n'
            + _dl(rows)
            + "\n"
            + _excerpt_html(source)
            + "\n</article>"
        )
    return (
        '<section id="section-sources">\n<h2>Sources</h2>\n'
        + ("\n".join(parts) if parts else "<p>none</p>")
        + "\n</section>"
    )


def _template() -> Template:
    raw = resources.files("atlas_aci.reports").joinpath("evidence_template.html").read_bytes()
    template = Template(raw.decode("utf-8").replace("\r\n", "\n"))
    if template.get_identifiers() != TEMPLATE_IDENTIFIERS:
        raise RuntimeError("evidence template placeholders changed")
    return template


def render_html(report: dict[str, Any], json_bytes: bytes) -> bytes:
    page = _template().substitute(
        identity=_identity_html(report, json_bytes),
        markers=_markers_html(report),
        omitted=_omitted_html(report),
        bundles=_bundles_html(report),
        sources=_sources_html(report),
    )
    return page.encode("utf-8")


# ---- export ---------------------------------------------------------------------------------


def export(
    raw: bytes,
    *,
    max_json_bytes: int = MAX_JSON_REPORT_BYTES,
    max_html_bytes: int = MAX_HTML_REPORT_BYTES,
) -> Report:
    """Validate and render both artifacts in memory; nothing is written."""
    report = build_report(load_envelope(raw))
    json_bytes = render_json(report)
    if len(json_bytes) > max_json_bytes:
        raise ReportRejected("report_too_large")
    html_bytes = render_html(report, json_bytes)
    if len(html_bytes) > max_html_bytes:
        raise ReportRejected("report_too_large")
    return Report(html=html_bytes, json=json_bytes)
