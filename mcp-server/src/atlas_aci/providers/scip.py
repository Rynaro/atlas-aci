"""Official SCIP (scip-typescript) import: producer-time manifest, symbols, positions, evidence.

Nothing here runs a compiler. The operator runs the pinned producer, then
``atlas-aci scip-manifest`` records producer-time source identity (and the
declared position encoding) in a sidecar, then ``atlas-aci import-scip`` maps
the artifact onto canonical evidence records bound to the exact snapshot bytes.

Honest limits (never inferred): only ``scip-typescript`` at a pinned version
with its pinned position encoding is accepted; ``reference`` occurrences stay
references (never calls, never imports); SCIP relationships, diagnostics,
documentation and external-symbol metadata are not imported.
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from atlas_aci.providers import scip_wire as wire
from atlas_aci.storage import evidence as ev

SCHEMA_TAG = "v0.6.1"
SCHEMA_COMMIT = "9d5796159c97c3b107355c424f6c06fb1a0ea01c"
SCHEMA_SHA256 = "b855eb9abdb3fe7f07600ee92f24a6c40f75fceef37136ccdc6844b300936a31"
MANIFEST_FORMAT = "atlas-scip-manifest.v1"
IMPORT_FORMAT = "atlas-scip-import.v1"
STATUS_FORMAT = "atlas-semantic-import.v1"
# Permitted position encodings are pinned per (tool, version): no released
# scip-typescript sets Document.position_encoding and it counts UTF-16 code units.
PRODUCERS: dict[str, dict[str, frozenset[str]]] = {
    "scip-typescript": {"0.4.0": frozenset({"utf-16"})},
}
ENCODINGS = {"utf-8": 1, "utf-16": 2, "utf-32": 3}
_ENCODING_BY_NUMBER = {value: key for key, value in ENCODINGS.items()}
ROLE_NAMES = {
    1: "Definition",
    2: "Import",
    4: "WriteAccess",
    8: "ReadAccess",
    16: "Generated",
    32: "Test",
    64: "ForwardDefinition",
}
DEFINITION = 1
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_DOCUMENTS = wire.DEFAULT_LIMITS.max_documents
LIMITATIONS = {
    "scip_reference_traversal": "reference_relation_not_traversable",
    "scip_external_symbols": "external_symbol_metadata_not_imported",
    "scip_producers": "only_scip_typescript_0_4_0",
    "scip_relationships": "symbol_relationships_not_imported",
    "scip_diagnostics": "diagnostics_not_imported",
}


class ScipImportError(ValueError):
    """A rejected SCIP submission; ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _fail(code: str, message: str) -> ScipImportError:
    return ScipImportError(code, message)


@lru_cache(maxsize=1)
def importer_sha256() -> str:
    """Digest of every ``providers/scip*.py`` module: the importer's code identity."""
    here = Path(__file__).parent
    return hashlib.sha256(
        ev.canonical(
            {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(here.glob("scip*.py"))
            }
        ).encode()
    ).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- producer-time manifest ------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    producer_name: str
    producer_version: str
    position_encoding: str
    index_sha256: str
    documents: dict[str, str]


@dataclass(frozen=True)
class Submission:
    """A fully validated (but not yet snapshot-bound) SCIP index plus its manifest."""

    index: dict[str, Any]
    index_sha256: str
    manifest: Manifest
    manifest_sha256: str
    documents: dict[str, dict[str, Any]]

    @property
    def tool(self) -> dict[str, str]:
        return {"name": self.manifest.producer_name, "version": self.manifest.producer_version}

    @property
    def encoding(self) -> str:
        return self.manifest.position_encoding


def _read_bounded(path: Path, limit: int, what: str) -> bytes:
    try:
        size = path.stat().st_size
        if size > limit:
            raise _fail(f"{what}_too_large", f"{what} exceeds {limit} bytes")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise _fail(f"{what}_unreadable", f"cannot read {what}: {exc}") from exc
    if len(data) > limit:
        raise _fail(f"{what}_too_large", f"{what} exceeds {limit} bytes")
    return data


def check_producer(name: Any, version: Any, encoding: Any = None) -> None:
    versions = PRODUCERS.get(name) if isinstance(name, str) else None
    if versions is None:
        raise _fail("unknown_producer", f"unsupported SCIP producer {name!r}")
    permitted = versions.get(version) if isinstance(version, str) else None
    if permitted is None:
        raise _fail("unknown_producer_version", f"unsupported {name} version {version!r}")
    if encoding is not None and encoding not in permitted:
        raise _fail(
            "encoding_not_permitted",
            f"{name} {version} counts positions in {sorted(permitted)}, not {encoding!r}",
        )


def _tool_of(index: Mapping[str, Any]) -> tuple[Any, Any]:
    tool = index.get("metadata", {}).get("tool_info", {})
    return tool.get("name"), tool.get("version")


def decode_index(data: bytes, limits: wire.Limits = wire.DEFAULT_LIMITS) -> dict[str, Any]:
    index = wire.decode_index(data, limits)
    seen: set[str] = set()
    for document in index.get("documents", []):
        path = document.get("relative_path", "")
        if not ev.relative_path(path):
            raise _fail(
                "invalid_document_path", f"document path {path!r} is not repository-relative"
            )
        if path in seen:
            raise _fail("duplicate_document", f"document {path!r} occurs more than once")
        seen.add(path)
    return index


def build_manifest(
    repo: Path, index_path: Path, position_encoding: str, limits: wire.Limits = wire.DEFAULT_LIMITS
) -> bytes:
    """Producer-time sidecar: encoding declaration plus the producer's working-tree hashes."""
    from atlas_aci.storage import snapshots as snap

    if position_encoding not in ENCODINGS:
        raise _fail("invalid_encoding", f"position encoding must be one of {sorted(ENCODINGS)}")
    data = _read_bounded(index_path, limits.max_bytes, "index")
    index = decode_index(data, limits)
    name, version = _tool_of(index)
    check_producer(name, version, position_encoding)
    documents: dict[str, str] = {}
    for document in index.get("documents", []):
        path = document["relative_path"]
        try:
            source, _ = snap.safe_source(repo, path)
        except (OSError, ValueError) as exc:
            raise _fail(
                "document_source_unavailable", f"cannot read {path!r} in the working tree: {exc}"
            ) from exc
        documents[path] = sha256_hex(source)
    manifest = {
        "format": MANIFEST_FORMAT,
        "producer": {"name": name, "version": version},
        "position_encoding": position_encoding,
        "index_sha256": sha256_hex(data),
        "documents": documents,
    }
    return (ev.canonical(manifest) + "\n").encode()


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail("manifest_invalid", f"duplicate manifest key {key!r}")
        result[key] = value
    return result


def parse_manifest(raw: bytes) -> Manifest:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ScipImportError):
            raise
        raise _fail("manifest_invalid", "manifest is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or set(data) != {
        "format",
        "producer",
        "position_encoding",
        "index_sha256",
        "documents",
    }:
        raise _fail("manifest_invalid", "manifest does not have the closed v1 field set")
    producer = data["producer"]
    documents = data["documents"]
    if (
        data["format"] != MANIFEST_FORMAT
        or not isinstance(producer, dict)
        or set(producer) != {"name", "version"}
        or not all(isinstance(producer[k], str) and producer[k] for k in producer)
        or data["position_encoding"] not in ENCODINGS
        or not ev.hash_value(data["index_sha256"])
        or not isinstance(documents, dict)
        or len(documents) > MAX_MANIFEST_DOCUMENTS
        or not all(ev.relative_path(k) and ev.hash_value(v) for k, v in documents.items())
    ):
        raise _fail("manifest_invalid", "manifest field values are invalid")
    return Manifest(
        producer["name"],
        producer["version"],
        data["position_encoding"],
        data["index_sha256"],
        dict(documents),
    )


def load_submission(
    index_path: Path, manifest_path: Path, limits: wire.Limits = wire.DEFAULT_LIMITS
) -> Submission:
    """Every check that needs neither the lock nor the published graph."""
    manifest_raw = _read_bounded(manifest_path, MAX_MANIFEST_BYTES, "manifest")
    index_raw = _read_bounded(index_path, limits.max_bytes, "index")
    manifest = parse_manifest(manifest_raw)
    check_producer(manifest.producer_name, manifest.producer_version, manifest.position_encoding)
    index = decode_index(index_raw, limits)
    name, version = _tool_of(index)
    check_producer(name, version, manifest.position_encoding)
    if (name, version) != (manifest.producer_name, manifest.producer_version):
        raise _fail(
            "producer_mismatch",
            f"manifest names {manifest.producer_name} {manifest.producer_version} but the index "
            f"was produced by {name} {version}",
        )
    if sha256_hex(index_raw) != manifest.index_sha256:
        raise _fail("index_hash_mismatch", "index bytes differ from the manifest's index_sha256")
    documents = {d["relative_path"]: d for d in index.get("documents", [])}
    if set(documents) != set(manifest.documents):
        missing = sorted(set(manifest.documents) - set(documents))[:5]
        extra = sorted(set(documents) - set(manifest.documents))[:5]
        raise _fail(
            "document_set_mismatch",
            f"index documents differ from manifest documents (missing from index {missing}, "
            f"not in manifest {extra})",
        )
    for path, document in documents.items():
        declared = document.get("position_encoding", 0)
        if declared:
            actual = _ENCODING_BY_NUMBER.get(declared)
            if actual != manifest.position_encoding:
                raise _fail(
                    "encoding_conflict",
                    f"document {path!r} declares encoding {declared} but the manifest declares "
                    f"{manifest.position_encoding!r}",
                )
    return Submission(index, sha256_hex(index_raw), manifest, sha256_hex(manifest_raw), documents)


# ---- symbol grammar --------------------------------------------------------

_SIMPLE = re.compile(r"[A-Za-z0-9_+\-$]+")
_SUFFIX_KIND = {"/": "namespace", "#": "type", ".": "term", ":": "meta", "!": "macro"}


@dataclass(frozen=True)
class Descriptor:
    kind: str
    name: str
    disambiguator: str = ""


@dataclass(frozen=True)
class ParsedSymbol:
    local: bool
    prefix: str
    descriptors: tuple[Descriptor, ...]


def _identifier(text: str, pos: int) -> tuple[str, int]:
    if pos < len(text) and text[pos] == "`":
        out = []
        pos += 1
        while pos < len(text):
            if text[pos] == "`":
                if pos + 1 < len(text) and text[pos + 1] == "`":
                    out.append("`")
                    pos += 2
                    continue
                if not out:
                    break
                return "".join(out), pos + 1
            out.append(text[pos])
            pos += 1
        raise _fail("invalid_symbol", "unterminated or empty escaped identifier")
    match = _SIMPLE.match(text, pos)
    if match is None:
        raise _fail("invalid_symbol", f"expected an identifier at offset {pos}")
    return match.group(), match.end()


@lru_cache(maxsize=65536)
def parse_symbol(text: str) -> ParsedSymbol:
    """Parse the SCIP symbol grammar (scip.proto ``Symbol`` documentation)."""
    if text.startswith("local "):
        identifier = text[6:]
        if not identifier or " " in identifier:
            raise _fail("invalid_symbol", f"malformed local symbol {text!r}")
        return ParsedSymbol(True, identifier, ())
    pos = 0
    for _ in range(4):  # scheme, manager, package name, version
        start = pos
        while pos < len(text):
            if text[pos] == " ":
                if pos + 1 < len(text) and text[pos + 1] == " ":
                    pos += 2
                    continue
                break
            pos += 1
        if pos >= len(text) or pos == start:
            raise _fail("invalid_symbol", f"malformed symbol header in {text!r}")
        pos += 1
    prefix = text[: pos - 1]
    descriptors: list[Descriptor] = []
    while pos < len(text):
        char = text[pos]
        if char in "[(":
            closing = "]" if char == "[" else ")"
            name, pos = _identifier(text, pos + 1)
            if pos >= len(text) or text[pos] != closing:
                raise _fail("invalid_symbol", f"unterminated descriptor in {text!r}")
            pos += 1
            descriptors.append(Descriptor("type_parameter" if char == "[" else "parameter", name))
            continue
        name, pos = _identifier(text, pos)
        if pos >= len(text):
            raise _fail("invalid_symbol", f"descriptor without suffix in {text!r}")
        suffix = text[pos]
        if suffix in _SUFFIX_KIND:
            descriptors.append(Descriptor(_SUFFIX_KIND[suffix], name))
            pos += 1
        elif suffix == "(":
            match = _SIMPLE.match(text, pos + 1)
            disambiguator = match.group() if match else ""
            pos = match.end() if match else pos + 1
            if text[pos : pos + 2] != ").":
                raise _fail("invalid_symbol", f"malformed method descriptor in {text!r}")
            pos += 2
            descriptors.append(Descriptor("method", name, disambiguator))
        else:
            raise _fail("invalid_symbol", f"unknown descriptor suffix {suffix!r} in {text!r}")
    if not descriptors:
        raise _fail("invalid_symbol", f"symbol {text!r} has no descriptors")
    return ParsedSymbol(False, prefix, tuple(descriptors))


def _owners(parsed: ParsedSymbol) -> list[dict[str, Any]]:
    if parsed.local:
        return [{"kind": "scip_local", "name": parsed.prefix, "ordinal": 0}]

    def owner(kind: str, name: str) -> dict[str, Any]:
        return {"kind": "scip_" + kind, "name": name, "ordinal": 0}

    chain = [owner("package", parsed.prefix)]
    for index, descriptor in enumerate(parsed.descriptors):
        last = index == len(parsed.descriptors) - 1
        if not last:
            chain.append(owner(descriptor.kind, descriptor.name))
        if descriptor.disambiguator:
            chain.append(owner("disambiguator", descriptor.disambiguator))
    return chain


# ---- positions -------------------------------------------------------------

_BOM = b"\xef\xbb\xbf"
# TypeScript line terminators other than CR/CRLF: LF, LINE SEPARATOR, PARAGRAPH SEPARATOR.
_LINE_BREAKS = frozenset({"\n", chr(0x2028), chr(0x2029)})


def _char_units(encoding: str, char: str) -> int:
    if encoding == "utf-16":
        return 2 if ord(char) > 0xFFFF else 1
    if encoding == "utf-8":
        return len(char.encode("utf-8"))
    return 1


class SourceText:
    """Exact snapshot bytes viewed as the producer saw them (TypeScript line rules)."""

    def __init__(self, raw: bytes, path: str):
        self.path = path
        self.raw = raw
        self.bom = len(_BOM) if raw.startswith(_BOM) else 0
        try:
            text = raw[self.bom :].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _fail("source_not_utf8", f"{path} is not valid UTF-8") from exc
        starts = [0]
        ends: list[int] = []
        index, size = 0, len(text)
        while index < size:
            char = text[index]
            index += 1
            if char == "\r":
                ends.append(index - 1)
                if index < size and text[index] == "\n":
                    index += 1
                starts.append(index)
            elif char in _LINE_BREAKS:
                ends.append(index - 1)
                starts.append(index)
        ends.append(size)
        self.text = text
        self.starts = starts
        self.ends = ends
        offsets = [self.bom]
        for begin, stop in itertools.pairwise(starts):
            offsets.append(offsets[-1] + len(text[begin:stop].encode("utf-8")))
        self.byte_starts = offsets

    def locate(self, line: int, column: int, encoding: str) -> tuple[int, int]:
        """SCIP (line, column) in ``encoding`` to (absolute byte offset, byte column)."""
        if line >= len(self.starts):
            raise _fail("position_out_of_line", f"{self.path}: line {line} is past the document")
        segment = self.text[self.starts[line] : self.ends[line]]
        if segment.isascii():
            if column > len(segment):
                raise _fail(
                    "position_out_of_line", f"{self.path}: column {column} is past line {line}"
                )
            return self.byte_starts[line] + column, column
        units = 0
        byte_column = 0
        for char in segment:
            if units == column:
                break
            if units > column:
                break
            units += _char_units(encoding, char)
            byte_column += len(char.encode("utf-8"))
        else:
            if units < column:
                raise _fail(
                    "position_out_of_line", f"{self.path}: column {column} is past line {line}"
                )
        if units != column:
            raise _fail(
                "position_not_on_boundary",
                f"{self.path}: column {column} on line {line} splits a character in {encoding}",
            )
        return self.byte_starts[line] + byte_column, byte_column

    def slice(self, start: int, end: int) -> str:
        return self.raw[start:end].decode("utf-8", "replace")


@dataclass(frozen=True)
class Span:
    start: tuple[int, int]
    end: tuple[int, int]
    start_byte: int
    end_byte: int
    line: int
    column: int
    end_line: int
    end_column: int


def _normalize_range(values: list[int]) -> tuple[tuple[int, int], tuple[int, int]]:
    if len(values) == 3:
        return (values[0], values[1]), (values[0], values[2])
    return (values[0], values[1]), (values[2], values[3])


def span_of(text: SourceText, values: list[int], encoding: str) -> Span:
    start, end = _normalize_range(values)
    if end < start:
        raise _fail("range_inverted", f"{text.path}: range {values} ends before it starts")
    start_byte, column = text.locate(start[0], start[1], encoding)
    end_byte, end_column = text.locate(end[0], end[1], encoding)
    return Span(start, end, start_byte, end_byte, start[0] + 1, column, end[0] + 1, end_column)


def definition_text_matches(kind: str, name: str, text: str, span: Span) -> bool:
    """Mechanical wrong-encoding detector: a definition's span must spell its name.

    Producer conventions accepted explicitly: the zero-width file-module span
    (namespace at 0:0), quoted property names, and ``<constructor>`` spelled
    ``constructor``. Local symbols carry no name in the symbol string.
    """
    if kind == "local":
        return bool(text)
    if text == name:
        return True
    if kind == "namespace" and span.start == span.end == (0, 0):
        return True
    if name == "<constructor>" and text == "constructor":
        return True
    return len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'" and text[1:-1] == name


# ---- evidence mapping ------------------------------------------------------

Range = tuple[tuple[int, int], tuple[int, int]]


@dataclass(frozen=True)
class Plan:
    producer: dict[str, Any]
    records: list[tuple[dict[str, Any], str | None]]
    symbols: list[dict[str, Any]]
    refs: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    counts: dict[str, int]


@dataclass
class _Definition:
    symbol: str
    parsed: ParsedSymbol
    kind: str
    name: str
    span: Span
    enclosing: Range | None
    roles: int
    ordinal: int = 0
    entity: dict[str, Any] | None = None

    @property
    def record(self) -> dict[str, Any]:
        assert self.entity is not None
        return self.entity


@dataclass
class _Reference:
    symbol: str
    parsed: ParsedSymbol
    span: Span
    enclosing: Range | None
    roles: int


@dataclass
class _Document:
    path: str
    sha: str
    size: int
    language: str
    definitions: list[_Definition]
    references: list[_Reference]
    information: dict[str, Any]
    skipped: int


def role_names(roles: int) -> list[str]:
    names = [name for bit, name in ROLE_NAMES.items() if roles & bit]
    unknown = roles & ~sum(ROLE_NAMES)
    return names + ([f"Unknown:{unknown}"] if unknown else [])


def _contains(outer: Range, span: Span) -> bool:
    return outer[0] <= span.start and span.end <= outer[1]


def _flat(value: Range | None) -> list[int] | None:
    return None if value is None else [*value[0], *value[1]]


def _binding(payload: dict[str, Any]) -> str:
    return json.dumps({"scip": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _map_document(
    path: str, document: Mapping[str, Any], raw: bytes, sha: str, language: str, encoding: str
) -> _Document:
    text = SourceText(raw, path)
    merged: dict[tuple[str, tuple[int, int], tuple[int, int], bool], list[Any]] = {}
    skipped = 0
    for occurrence in document.get("occurrences", []):
        symbol = occurrence.get("symbol", "")
        if not symbol:
            skipped += 1
            continue
        parsed = parse_symbol(symbol)
        span = span_of(text, occurrence["range"], encoding)
        enclosing: Range | None = None
        if occurrence.get("enclosing_range"):
            enclosing = _normalize_range(occurrence["enclosing_range"])
            if enclosing[1] < enclosing[0]:
                raise _fail("range_inverted", f"{path}: enclosing range ends before it starts")
        roles = occurrence.get("symbol_roles", 0)
        key = (symbol, span.start, span.end, bool(roles & DEFINITION))
        if key in merged:
            merged[key][0] |= roles
            merged[key][2] = merged[key][2] or enclosing
        else:
            merged[key] = [roles, span, enclosing, parsed]
    definitions: list[_Definition] = []
    references: list[_Reference] = []
    for (symbol, _start, _end, is_definition), (roles, span, enclosing, parsed) in sorted(
        merged.items(), key=lambda item: (item[0][1], item[0][2], item[0][0], not item[0][3])
    ):
        if not is_definition:
            references.append(_Reference(symbol, parsed, span, enclosing, roles))
            continue
        spelled = text.slice(span.start_byte, span.end_byte)
        if parsed.local:
            kind, name = "local", spelled
        else:
            kind, name = parsed.descriptors[-1].kind, parsed.descriptors[-1].name
        if not definition_text_matches(kind, name, spelled, span):
            raise _fail(
                "definition_text_mismatch",
                f"{path}: definition of {symbol!r} at {span.start}-{span.end} spells "
                f"{spelled!r}, not {name!r}; the declared {encoding} position encoding does "
                "not match the producer's offsets",
            )
        definitions.append(_Definition(symbol, parsed, kind, name, span, enclosing, roles))
    ordinals: dict[str, int] = {}
    for definition in definitions:
        definition.ordinal = ordinals.get(definition.symbol, 0)
        ordinals[definition.symbol] = definition.ordinal + 1
    information = {
        item["symbol"]: item
        for item in document.get("symbols", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    return _Document(path, sha, len(raw), language, definitions, references, information, skipped)


def _owner_of(
    enclosers: list[_Definition],
    starts: list[tuple[int, int]],
    ends_max: list[tuple[int, int]],
    span: Span,
) -> _Definition | None:
    """Innermost definition whose enclosing range contains the span (greatest start, least end)."""
    owner: _Definition | None = None
    for index in range(bisect.bisect_right(starts, span.start) - 1, -1, -1):
        if ends_max[index] < span.end:
            break  # no earlier enclosing range reaches this occurrence
        candidate = enclosers[index]
        enclosing = candidate.enclosing
        assert enclosing is not None
        if owner is not None:
            assert owner.enclosing is not None
            if enclosing[0] < owner.enclosing[0]:
                break
        if _contains(enclosing, span) and (
            owner is None
            or owner.enclosing is None
            or enclosing[0] > owner.enclosing[0]
            or enclosing[1] < owner.enclosing[1]
        ):
            owner = candidate
    return owner


def _occurrence(
    path: str, language: str, sha: str, size: int, entity_id: str, kind: str, role: str, span: Span
) -> dict[str, Any]:
    return ev.record(
        "occurrence",
        entity_id=entity_id,
        kind=kind,
        path=path,
        language=language,
        source_sha256=sha,
        source_size=size,
        start_byte=span.start_byte,
        end_byte=span.end_byte,
        line=span.line,
        column=span.column,
        end_line=span.end_line,
        end_column=span.end_column,
        role=role,
        provenance_state="source",
    )


def plan_import(
    submission: Submission,
    *,
    current: Mapping[str, str],
    observations: Mapping[str, Mapping[str, Any]],
    read_source: Callable[[str, str], bytes],
) -> Plan:
    """Validate against the pinned snapshot and map every document, or raise."""
    producer = ev.producer(block_fingerprint(import_block(submission, "0" * 64)))
    ev.validate_record(producer)
    documents: list[_Document] = []
    for path in sorted(submission.documents):
        sha = submission.manifest.documents[path]
        if current.get(path) != sha:
            raise _fail(
                "source_mismatch",
                f"{path}: manifest hash {sha[:12]} differs from the published snapshot "
                f"({str(current.get(path))[:12]})",
            )
        observed = observations.get(path)
        if (
            observed is None
            or observed.get("state") != "indexed"
            or observed.get("observed_source_sha256") != sha
        ):
            state = observed.get("state") if observed else "unobserved"
            raise _fail("document_not_indexed", f"{path}: observation state is {state!r}")
        raw = read_source(path, sha)
        if sha256_hex(raw) != sha:
            raise _fail("source_mismatch", f"{path}: snapshot bytes do not match their hash")
        document = submission.documents[path]
        encoding = _ENCODING_BY_NUMBER.get(document.get("position_encoding", 0)) or (
            submission.encoding
        )
        documents.append(_map_document(path, document, raw, sha, observed["language"], encoding))
        del raw

    records: list[tuple[dict[str, Any], str | None]] = [(producer, None)]
    symbols: list[dict[str, Any]] = []
    by_symbol: dict[tuple[str | None, str], list[_Definition]] = {}
    identities: dict[str, str] = {}
    for doc in documents:
        for definition in doc.definitions:
            parsed, span = definition.parsed, definition.span
            entity = ev.record(
                "entity",
                path=doc.path,
                language=doc.language,
                kind="scip_" + definition.kind,
                name=definition.name,
                owners=_owners(parsed),
                ordinal=definition.ordinal,
                identity_basis="parsed",
            )
            if identities.setdefault(entity["id"], definition.symbol) != definition.symbol:
                raise _fail(
                    "entity_identity_collision",
                    f"{doc.path}: symbols {identities[entity['id']]!r} and "
                    f"{definition.symbol!r} share one entity identity",
                )
            definition.entity = entity
            occurrence = _occurrence(
                doc.path,
                doc.language,
                doc.sha,
                doc.size,
                entity["id"],
                "definition",
                "definition:scip_" + definition.kind,
                span,
            )
            records += [
                (entity, doc.path),
                (occurrence, doc.path),
                (ev.provenance(occurrence, producer["id"]), doc.path),
            ]
            info = doc.information.get(definition.symbol, {})
            symbols.append(
                {
                    "name": definition.name,
                    "kind": entity["kind"],
                    "path": doc.path,
                    "line_start": span.line,
                    "line_end": span.end_line,
                    "lang": doc.language,
                    "binding": _binding(
                        {
                            "symbol": definition.symbol,
                            "descriptor_kind": definition.kind,
                            "disambiguator": parsed.descriptors[-1].disambiguator
                            if parsed.descriptors
                            else "",
                            "roles": definition.roles,
                            "role_names": role_names(definition.roles),
                            "range": [*span.start, *span.end],
                            "enclosing_range": _flat(definition.enclosing),
                            "position_encoding": submission.encoding,
                            "information": {
                                key: info[key]
                                for key in ("kind", "display_name")
                                if info.get(key) not in (None, 0, "")
                            },
                        }
                    ),
                    "entity_id": entity["id"],
                    "occurrence_id": occurrence["id"],
                }
            )
            by_symbol.setdefault(
                (doc.path if parsed.local else None, definition.symbol), []
            ).append(definition)

    refs: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    counts = {
        "documents": len(documents),
        "entities": len(symbols),
        "definitions": len(symbols),
        "references": 0,
        "resolved_references": 0,
        "ambiguous_references": 0,
        "unresolved_references": 0,
        "skipped_empty_symbol": sum(doc.skipped for doc in documents),
    }
    for doc in documents:
        file_entity = ev.file_entity(doc.path, doc.language)
        enclosers = sorted(
            (d for d in doc.definitions if d.enclosing is not None),
            key=lambda d: (d.enclosing, d.span.start, d.symbol, d.ordinal),
        )
        starts = [d.enclosing[0] for d in enclosers if d.enclosing]
        ends_max: list[tuple[int, int]] = []
        for definition in enclosers:
            assert definition.enclosing is not None
            ends_max.append(
                max(ends_max[-1], definition.enclosing[1]) if ends_max else definition.enclosing[1]
            )
        for reference in doc.references:
            span = reference.span
            owner = _owner_of(enclosers, starts, ends_max, span)
            source = owner.record if owner is not None else file_entity
            occurrence = _occurrence(
                doc.path,
                doc.language,
                doc.sha,
                doc.size,
                source["id"],
                "reference",
                "reference:reference",
                span,
            )
            proof = ev.provenance(occurrence, producer["id"])
            targets = by_symbol.get(
                (doc.path if reference.parsed.local else None, reference.symbol), []
            )
            if not targets:
                resolution, reason = "UNRESOLVED", "scip_external_symbol"
                counts["unresolved_references"] += 1
            elif len(targets) == 1:
                resolution, reason = "EXTRACTED", None
                counts["resolved_references"] += 1
            else:
                resolution, reason = "AMBIGUOUS", "scip_multiple_definitions"
                counts["ambiguous_references"] += 1
            counts["references"] += 1
            ordered = sorted(
                targets, key=lambda d: (d.record["path"], d.span.line, d.name, d.span.start)
            )
            assertion = ev.assertion(
                source["id"],
                ordered[0].record["id"] if resolution == "EXTRACTED" else None,
                "reference",
                proof["id"],
                resolution,
                reference.symbol,
                candidates=[d.record["id"] for d in ordered] if resolution == "AMBIGUOUS" else (),
                reason=reason,
            )
            records += [(occurrence, doc.path), (proof, doc.path), (assertion, doc.path)]
            display = (
                reference.parsed.descriptors[-1].name if reference.parsed.descriptors else None
            )
            refs.append(
                {
                    "callee_name": reference.symbol,
                    "relation": "reference",
                    "qualified": 0,
                    "qualifier_name": None,
                    "path": doc.path,
                    "line": span.line,
                    "col": span.column,
                    "lang": doc.language,
                    "binding": _binding(
                        {
                            "symbol": reference.symbol,
                            "display_name": display,
                            "roles": reference.roles,
                            "role_names": role_names(reference.roles),
                            "range": [*span.start, *span.end],
                            "enclosing_range": _flat(reference.enclosing),
                            "position_encoding": submission.encoding,
                            "resolution": resolution,
                        }
                    ),
                    "unresolved_reason": reason if resolution == "UNRESOLVED" else None,
                    "source_entity_id": source["id"],
                    "occurrence_id": occurrence["id"],
                    "evidence_id": proof["id"],
                }
            )
            if resolution == "UNRESOLVED":
                continue
            single = ordered[0] if resolution == "EXTRACTED" else None
            edges.append(
                {
                    "relation": "reference",
                    "source_path": doc.path,
                    "source_line": span.line,
                    "source_col": span.column,
                    "source_name": None if source["kind"] == "file" else source["name"],
                    "source_kind": None if source["kind"] == "file" else source["kind"],
                    "callee_name": reference.symbol,
                    "confidence": resolution,
                    "target_path": single.record["path"] if single else None,
                    "target_line": single.span.line if single else None,
                    "target_name": single.name if single else None,
                    "candidates": None
                    if single
                    else json.dumps(
                        [
                            {"path": d.record["path"], "line": d.span.line, "name": d.name}
                            for d in ordered
                        ]
                    ),
                    "lang": doc.language,
                    "assertion_id": assertion["id"],
                }
            )
    unique: dict[str, tuple[dict[str, Any], str | None]] = {}
    for item in records:
        unique.setdefault(item[0]["id"], item)
    ordered_records = sorted(unique.values(), key=lambda item: (item[0]["type"], item[0]["id"]))
    return Plan(producer, ordered_records, symbols, refs, edges, counts)


# ---- generation inputs -----------------------------------------------------


def import_block(submission: Submission, base_generation_id: str) -> dict[str, Any]:
    return {
        "format": IMPORT_FORMAT,
        "base_generation_id": base_generation_id,
        "tool": dict(submission.tool),
        "schema_sha256": SCHEMA_SHA256,
        "index_sha256": submission.index_sha256,
        "manifest_sha256": submission.manifest_sha256,
        "position_encoding": submission.encoding,
        "importer_sha256": importer_sha256(),
    }


def block_fingerprint(block: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scip": {
            key: block[key]
            for key in (
                "tool",
                "schema_sha256",
                "index_sha256",
                "manifest_sha256",
                "position_encoding",
                "importer_sha256",
            )
        }
    }


def validate_block(block: Any) -> dict[str, Any]:
    """Closed shape and allow-lists of the ``scip`` generation-input block."""
    ev.require(
        isinstance(block, dict)
        and set(block)
        == {
            "format",
            "base_generation_id",
            "tool",
            "schema_sha256",
            "index_sha256",
            "manifest_sha256",
            "position_encoding",
            "importer_sha256",
        }
        and block["format"] == IMPORT_FORMAT
        and isinstance(block["tool"], dict)
        and set(block["tool"]) == {"name", "version"}
        and all(
            ev.hash_value(block[key])
            for key in (
                "base_generation_id",
                "schema_sha256",
                "index_sha256",
                "manifest_sha256",
                "importer_sha256",
            )
        )
        and block["schema_sha256"] == SCHEMA_SHA256,
        "scip import block",
    )
    try:
        check_producer(block["tool"]["name"], block["tool"]["version"], block["position_encoding"])
    except ScipImportError as exc:
        raise ValueError("Invalid evidence: scip import block producer") from exc
    return block


def same_import(block: Mapping[str, Any], submission: Submission) -> bool:
    """True when ``block`` was produced from exactly this artifact, manifest and importer."""
    return dict(block) == import_block(submission, block["base_generation_id"])


def scip_record_ids(records: list[dict[str, Any]]) -> set[str]:
    """IDs of every canonical and projection record contributed by a SCIP producer."""
    producers = {
        r["id"]
        for r in records
        if r["type"] == "producer"
        and isinstance(r["fingerprint"], dict)
        and "scip" in r["fingerprint"]
    }
    proofs = {r["id"] for r in records if r["type"] == "evidence" and r["producer_id"] in producers}
    occurrences = {
        r["occurrence_id"] for r in records if r["type"] == "evidence" and r["id"] in proofs
    }
    entities = {r["id"] for r in records if r["type"] == "entity" and r["kind"].startswith("scip_")}
    occurrences |= {
        r["id"] for r in records if r["type"] == "occurrence" and r["entity_id"] in entities
    }
    assertions = {
        r["id"] for r in records if r["type"] == "assertion" and r["evidence_id"] in proofs
    }
    selected = producers | proofs | occurrences | entities | assertions
    for record in records:
        if record["type"] != "projection":
            continue
        if any(value in selected for value in record["links"].values() if value is not None):
            selected.add(record["id"])
    return selected
