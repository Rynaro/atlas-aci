"""One locked pure-Python wheel as a standalone, content-addressed declaration snapshot.

The library takes BYTES and zero-argument readers; every filesystem access lives in the CLI.
The wheel is a zip archive read in memory: nothing is extracted, installed, imported or run, no
network is used, and the Python grammar is only ever taken from a local cache (never fetched).
Facts are declaration-only: names, spans, import paths and literal `__all__` exports.
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import metadata
from typing import Any

import tree_sitter_language_pack as pack

from atlas_aci.dependencies import uv_lock as lock
from atlas_aci.dependencies.uv_lock import (
    Artifact,
    DependencyRejected,
    Limits,
    Selection,
)
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-python-package-snapshot.v1"
SUMMARY_FORMAT = "atlas-python-dependency-import-summary.v1"
IMPORTER = {
    "name": "atlas-aci import-python-dependency",
    "version": "atlas-python-dependency-importer.v1",
}
DECLARATION_KINDS = ("class", "function", "async_function", "method", "async_method")
DIAGNOSTIC_CODES = (
    "compiled_extension_not_indexed",
    "conditional_definition_not_indexed",
    "conditional_import_not_indexed",
    "data_directory_not_indexed",
    "dynamic_all_not_indexed",
    "dynamic_module_getattr",
    "nesting_limit_exceeded",
    "non_importable_module_path",
    "non_python_member_not_indexed",
    "non_utf8_source",
    "star_import_not_expanded",
    "stub_file_not_indexed",
    "syntax_error",
)
MEMBER_ROLES = ("python_source", "stub", "extension", "data", "dist_info", "other")
PARSE_STATES = ("parsed", "syntax_error", "non_utf8_source", "not_parsed")
LIMITATIONS = (
    "declaration_only_no_call_binding",
    "dynamic_exports_not_modeled",
    "conditional_definitions_and_imports_not_indexed",
    "local_definitions_and_imports_not_indexed",
    "stubs_extensions_and_data_not_indexed",
    "imports_are_paths_not_resolved_across_packages",
    "lock_bound_by_digest_not_to_a_source_snapshot",
    "single_registry_pure_wheel_only",
    "uv_lock_version_1_revision_3_only",
    "restricted_marker_subset",
    "requires_python_not_evaluated",
    "wheel_record_not_consulted",
    "identifiers_recorded_as_written",
    "not_written_to_the_graph_and_not_exposed_over_mcp",
    "variables_aliases_and_type_aliases_not_indexed",
    "version_strings_compared_exactly_not_normalized",
)
COVERAGE = {
    "evidence_coverage": "declaration_only",
    "completeness": "incomplete",
    "call_binding": "not_claimed",
    "execution": "not_performed",
}
MAX_CLASS_NESTING = 4
MAX_NAME_LENGTH = 1024
RATIO_FLOOR_BYTES = 1024

_ROLE_DIAGNOSTIC = {
    "stub": "stub_file_not_indexed",
    "extension": "compiled_extension_not_indexed",
    "data": "data_directory_not_indexed",
    "other": "non_python_member_not_indexed",
}
_EXTENSIONS = (".so", ".pyd", ".dylib", ".dll")
_CONTROL = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "try_statement",
        "with_statement",
        "match_statement",
    }
)
_DEFINITIONS = frozenset({"class_definition", "function_definition"})
_IMPORTS = frozenset({"import_statement", "import_from_statement", "future_import_statement"})
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_DIST_DIR = r"([A-Za-z0-9_.]+)-([0-9][A-Za-z0-9_.!+]*)\.dist-info"
_SAFE_BITS = 0x080E

# Read once at import: a later call would open the distribution metadata inside a measured run.
try:
    _PACK_VERSION: str | None = metadata.version("tree-sitter-language-pack")
except Exception:
    _PACK_VERSION = None


def canonical_bytes(document: Any) -> bytes:
    """The exact bytes written and printed: canonical JSON plus one newline."""
    return ev.canonical(document).encode("utf-8") + b"\n"


def _digest(value: Any, tag: str) -> str:
    return hashlib.sha256((tag + ":" + ev.canonical(value)).encode("utf-8")).hexdigest()


def _record_id(*parts: Any) -> str:
    return hashlib.sha256(ev.canonical(list(parts)).encode("utf-8")).hexdigest()


def _reject(reason: str) -> DependencyRejected:
    return DependencyRejected(reason)


# ---- grammar --------------------------------------------------------------------------------


def python_parser() -> tuple[Any, dict[str, Any]]:
    """The local python parser and its identity; a grammar that is not cached is never fetched.

    Locality is decided by `downloaded_languages()`, which only lists the cache directory. Any
    failure afterwards is reported as `python_parser_unavailable`.
    """
    try:
        local = "python" in pack.downloaded_languages()
    except Exception:
        local = False
    if not local or _PACK_VERSION is None:
        raise _reject("python_parser_unavailable")
    try:
        parser = pack.get_parser("python")
        language = parser.language
        abi = language.abi_version if language is not None else None
    except Exception:
        raise _reject("python_parser_unavailable") from None
    if type(abi) is not int:
        raise _reject("python_parser_unavailable")
    extractor = {
        "parser": "tree-sitter",
        "language": "python",
        "grammar_version": _PACK_VERSION,
        "abi_version": abi,
    }
    return parser, extractor


# ---- archive --------------------------------------------------------------------------------


@dataclass
class _Member:
    path: str
    sha256: str
    size: int
    data: bytes | None
    role: str = "other"


def module_name(path: str) -> tuple[str | None, bool]:
    """The importable module of a `.py` member path and whether it is a package `__init__`."""
    segments = path[: -len(".py")].split("/")
    is_package = segments[-1] == "__init__"
    if is_package:
        segments = segments[:-1]
    if not segments or any(re.fullmatch(_IDENTIFIER, part) is None for part in segments):
        return None, is_package
    return ".".join(segments), is_package


def _dist_dirs(paths: Sequence[str]) -> list[str]:
    found = {
        path.split("/", 1)[0]
        for path in paths
        if "/" in path and path.split("/", 1)[0].endswith(".dist-info")
    }
    return sorted(found)


def _role(path: str, dist_dir: str | None) -> str:
    first = path.split("/", 1)[0]
    if dist_dir is not None and first == dist_dir and "/" in path:
        return "dist_info"
    if first.endswith(".data") and "/" in path:
        return "data"
    if path.endswith(".pyi"):
        return "stub"
    if path.endswith(_EXTENSIONS):
        return "extension"
    if path.endswith(".py"):
        return "python_source"
    return "other"


def _check_entries(raw: bytes, infos: list[zipfile.ZipInfo]) -> list[tuple[zipfile.ZipInfo, bool]]:
    """Structural and name rules for every entry; returns (info, is_directory) pairs."""
    seen: set[str] = set()
    lowered: set[str] = set()
    files: set[str] = set()
    prefixes: set[str] = set()
    spans: list[tuple[int, int]] = []
    result: list[tuple[zipfile.ZipInfo, bool]] = []
    for info in infos:
        name = info.filename
        if info.orig_filename != name or not name.isascii() or len(name) > MAX_NAME_LENGTH:
            raise _reject("unsafe_archive_member")
        if re.search(r"[\x00-\x1f\x7f\\:]", name) is not None:
            raise _reject("unsafe_archive_member")
        is_directory = name.endswith("/")
        base = name[:-1] if is_directory else name  # exactly one trailing slash
        if not ev.relative_path(base):
            raise _reject("unsafe_archive_member")
        mode = info.external_attr >> 16
        if mode:
            kind = stat.S_IFMT(mode)
            if kind not in (stat.S_IFREG, stat.S_IFDIR) or is_directory != (kind == stat.S_IFDIR):
                raise _reject("unsafe_archive_member")
        if is_directory and info.file_size != 0:
            raise _reject("unsafe_archive_member")
        if base in seen or base.lower() in lowered:
            raise _reject("unsafe_archive_member")
        seen.add(base)
        lowered.add(base.lower())
        parts = base.split("/")
        prefixes.update("/".join(parts[:index]) for index in range(1, len(parts)))
        if not is_directory:
            files.add(base)
        if info.flag_bits & ~_SAFE_BITS or info.compress_type not in (
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
        ):
            raise _reject("unsafe_archive_member")
        offset = info.header_offset
        header = raw[offset : offset + 30]
        if len(header) < 30 or header[:4] != b"PK\x03\x04":
            raise _reject("unsafe_archive_member")
        extent = int.from_bytes(header[26:28], "little") + int.from_bytes(header[28:30], "little")
        end = offset + 30 + extent + info.compress_size
        if end > len(raw):
            raise _reject("unsafe_archive_member")
        spans.append((offset, end))
        result.append((info, is_directory))
    if files & prefixes:
        raise _reject("unsafe_archive_member")
    spans.sort()
    if spans and spans[0][0] != 0:
        raise _reject("unsafe_archive_member")
    for index in range(1, len(spans)):
        if spans[index][0] < spans[index - 1][1]:
            raise _reject("unsafe_archive_member")
    return result


def read_members(raw: bytes, limits: Limits) -> list[_Member]:
    """Every regular member's digest and (for sources and dist-info) bytes, in path order."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:
        raise _reject("invalid_wheel") from None
    try:
        with archive:
            infos = archive.infolist()
            if len(infos) > limits.max_members:
                raise _reject("archive_limit_exceeded")
            if sum(info.file_size for info in infos) > limits.max_total_uncompressed_bytes:
                raise _reject("archive_limit_exceeded")
            entries = _check_entries(raw, infos)
            members: list[_Member] = []
            for info, is_directory in entries:
                if is_directory:
                    continue
                if info.file_size > limits.max_member_bytes:
                    raise _reject("archive_limit_exceeded")
                if info.file_size > RATIO_FLOOR_BYTES and info.file_size > (
                    limits.max_compression_ratio * max(info.compress_size, 1)
                ):
                    raise _reject("archive_limit_exceeded")
                with archive.open(info) as handle:
                    data = handle.read(info.file_size + 1)
                if len(data) != info.file_size or (zlib.crc32(data) & 0xFFFFFFFF) != info.CRC:
                    raise _reject("invalid_wheel")
                keep = info.filename.endswith(".py") or ".dist-info/" in info.filename
                members.append(
                    _Member(
                        info.filename,
                        hashlib.sha256(data).hexdigest(),
                        len(data),
                        data if keep else None,
                    )
                )
    except DependencyRejected:
        raise
    except Exception:
        raise _reject("invalid_wheel") from None
    members.sort(key=lambda item: item.path)
    return members


# ---- metadata -------------------------------------------------------------------------------


def _headers(data: bytes) -> dict[str, list[str]]:
    """Header lines up to the first blank line; continuation lines are not headers."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _reject("wheel_metadata_mismatch") from None
    result: dict[str, list[str]] = {}
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line == "":
            break
        if line[0] in " \t" or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result.setdefault(lock.ascii_lower(key), []).append(value.strip(" \t"))
    return result


def check_metadata(members: list[_Member], selection: Selection) -> dict[str, Any]:
    """Cross-check the dist-info against the locked coordinate; returns the `dist_info` block."""
    dirs = _dist_dirs([item.path for item in members])
    if len(dirs) != 1:
        raise _reject("wheel_metadata_mismatch")
    directory = dirs[0]
    match = re.fullmatch(_DIST_DIR, directory)
    if match is None:
        raise _reject("wheel_metadata_mismatch")
    if lock.normalize_name(match.group(1)) != selection.name:
        raise _reject("wheel_metadata_mismatch")
    if match.group(2) != selection.version.replace("-", "_"):
        raise _reject("wheel_metadata_mismatch")
    by_path = {item.path: item for item in members}
    meta, wheel = by_path.get(directory + "/METADATA"), by_path.get(directory + "/WHEEL")
    if meta is None or wheel is None or meta.data is None or wheel.data is None:
        raise _reject("wheel_metadata_mismatch")
    head = _headers(meta.data)
    names, versions = head.get("name", []), head.get("version", [])
    if len(names) != 1 or len(versions) != 1:
        raise _reject("wheel_metadata_mismatch")
    if lock.normalize_name(names[0]) != selection.name or versions[0] != selection.version:
        raise _reject("wheel_metadata_mismatch")
    info = _headers(wheel.data)
    wheel_versions = info.get("wheel-version", [])
    if len(wheel_versions) != 1 or re.fullmatch(r"1\.[0-9]{1,4}", wheel_versions[0]) is None:
        raise _reject("wheel_metadata_mismatch")
    purelib = info.get("root-is-purelib", [])
    if len(purelib) != 1 or lock.ascii_lower(purelib[0]) != "true":
        raise _reject("not_pure_python")
    tags = info.get("tag", [])
    if not tags or any(re.fullmatch(r"[A-Za-z0-9_.]+-none-any", tag) is None for tag in tags):
        raise _reject("not_pure_python")
    return {
        "directory": directory,
        "name": names[0],
        "version": versions[0],
        "wheel_version": wheel_versions[0],
        "root_is_purelib": True,
        "tags": sorted(set(tags)),
    }


# ---- extraction -----------------------------------------------------------------------------


def _span(node: Any) -> dict[str, int]:
    return {
        "start_byte": node.start_byte,
        "end_byte": node.end_byte,
        "start_line": node.start_point.row + 1,
        "start_col": node.start_point.column,
        "end_line": node.end_point.row + 1,
        "end_col": node.end_point.column,
    }


class _Surface:
    """The partial facts of one parsed member, bounded by the remaining record budget."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.declarations: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.exports: list[dict[str, Any]] = []
        self.diagnostics: list[tuple[str, dict[str, int] | None]] = []

    def used(self) -> int:
        return (
            len(self.declarations) + len(self.imports) + len(self.exports) + len(self.diagnostics)
        )

    def check(self) -> None:
        if self.used() > self.budget:
            raise _reject("surface_limit_exceeded")

    def diagnose(self, code: str, span: dict[str, int] | None) -> None:
        self.diagnostics.append((code, span))
        self.check()


def _text(data: bytes, node: Any) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8")


def _dotted(data: bytes, node: Any) -> str:
    return ".".join(_text(data, part) for part in node.named_children if part.type == "identifier")


def _contains(root: Any, types: frozenset[str]) -> set[str]:
    found: set[str] = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type in types:
            found.add(node.type)
        pending.extend(node.children)
    return found


def _all_mentions(root: Any, data: bytes) -> list[Any]:
    mentions = []
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type == "identifier" and data[node.start_byte : node.end_byte] == b"__all__":
            mentions.append(node)
        pending.extend(node.children)
    mentions.sort(key=lambda node: node.start_byte)
    return mentions


def _resolve(module: str | None, is_package: bool, level: int, target: str) -> str | None:
    if level == 0:
        return target or None
    if module is None:
        return None
    segments = module.split(".")
    package = segments if is_package else segments[:-1]
    keep = len(package) - (level - 1)
    if keep < 1:
        return None
    return ".".join([*package[:keep], *([target] if target else [])])


def _import_records(
    node: Any, data: bytes, module: str | None, is_package: bool
) -> list[dict[str, Any]]:
    span = _span(node)
    records: list[dict[str, Any]] = []
    level, target = 0, ""
    if node.type == "future_import_statement":
        target = "__future__"
    if node.type == "import_from_statement":
        source = node.child_by_field_name("module_name")
        if source is not None and source.type == "relative_import":
            for part in source.children:
                if part.type == "import_prefix":
                    level = data[part.start_byte : part.end_byte].count(b".")
                elif part.type == "dotted_name":
                    target = _dotted(data, part)
        elif source is not None:
            target = _dotted(data, source)
    for index, child in enumerate(node.children):
        field = node.field_name_for_child(index)
        wildcard = child.type == "wildcard_import"
        if field != "name" and not wildcard:
            continue
        alias = None
        name_node = child
        if child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            alias = _text(data, alias_node) if alias_node is not None else None
        if name_node is None:
            continue
        spelled = "*" if wildcard else _dotted(data, name_node)
        if node.type == "import_statement":
            record = {
                "statement": "import",
                "level": 0,
                "target": spelled,
                "name": None,
                "alias": alias,
                "resolved_module": spelled,
            }
        else:
            record = {
                "statement": "from",
                "level": level,
                "target": target,
                "name": spelled,
                "alias": alias,
                "resolved_module": _resolve(module, is_package, level, target),
            }
        record["span"] = span
        record["wildcard_span"] = _span(child) if wildcard else None
        records.append(record)
    return records


def _literal_all(root: Any, data: bytes, mention: Any) -> list[tuple[str, dict[str, int]]] | None:
    """The exports of the one unconditional literal `__all__`, or None for any other form."""
    assignment = mention.parent
    if assignment is None or assignment.type != "assignment":
        return None
    if assignment.child_by_field_name("left") != mention:
        return None
    if assignment.child_by_field_name("type") is not None:
        return None
    holder = assignment.parent
    if holder is not None and holder.type == "expression_statement":
        holder = holder.parent
    if holder is None or holder != root:
        return None
    value = assignment.child_by_field_name("right")
    if value is None or value.type not in ("list", "tuple"):
        return None
    exports: list[tuple[str, dict[str, int]]] = []
    for element in value.named_children:
        if element.type == "comment":
            continue
        if element.type != "string":
            return None
        if [part.type for part in element.children] != [
            "string_start",
            "string_content",
            "string_end",
        ]:
            return None
        start, content = element.children[0], element.children[1]
        if data[start.start_byte : start.end_byte] not in (b"'", b'"'):
            return None
        name = _text(data, content)
        if re.fullmatch(_IDENTIFIER, name) is None:
            return None
        exports.append((name, _span(element)))
    return exports


def extract_member(
    parser: Any, data: bytes, module: str | None, is_package: bool, budget: int
) -> tuple[str, _Surface]:
    """`(parse_state, facts)` of one `.py` member; offsets are relative to `data`."""
    surface = _Surface(budget)
    if module is None:
        surface.diagnose("non_importable_module_path", None)
        return "not_parsed", surface
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        surface.diagnose("non_utf8_source", None)
        return "non_utf8_source", surface
    root = parser.parse(data).root_node
    if root.has_error:
        surface.diagnose("syntax_error", None)
        return "syntax_error", surface
    tasks: list[tuple[list[Any], int | None, int, str]] = [(root.named_children, None, 0, "")]
    while tasks:
        children, parent_ref, depth, prefix = tasks.pop()
        for statement in children:
            node, decorated = statement, False
            if node.type == "decorated_definition":
                inner = node.child_by_field_name("definition")
                if inner is None:
                    continue
                node, decorated = inner, True
            kind = node.type
            if kind == "class_definition":
                if depth + 1 > MAX_CLASS_NESTING:
                    surface.diagnose("nesting_limit_exceeded", _span(node))
                    continue
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = _text(data, name_node)
                surface.declarations.append(
                    {
                        "kind": "class",
                        "name": name,
                        "prefix": prefix,
                        "parent_ref": parent_ref,
                        "decorated": decorated,
                        "span": _span(node),
                    }
                )
                surface.check()
                body = node.child_by_field_name("body")
                if body is not None:
                    tasks.append(
                        (
                            body.named_children,
                            len(surface.declarations) - 1,
                            depth + 1,
                            prefix + name + ".",
                        )
                    )
            elif kind == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = _text(data, name_node)
                is_async = any(part.type == "async" for part in node.children)
                if parent_ref is None:
                    label = "async_function" if is_async else "function"
                else:
                    label = "async_method" if is_async else "method"
                surface.declarations.append(
                    {
                        "kind": label,
                        "name": name,
                        "prefix": prefix,
                        "parent_ref": parent_ref,
                        "decorated": decorated,
                        "span": _span(node),
                    }
                )
                surface.check()
                if parent_ref is None and name == "__getattr__":
                    surface.diagnose("dynamic_module_getattr", _span(node))
            elif kind in _CONTROL:
                found = _contains(node, _DEFINITIONS | _IMPORTS)
                if found & _DEFINITIONS:
                    surface.diagnose("conditional_definition_not_indexed", _span(node))
                if found & _IMPORTS:
                    surface.diagnose("conditional_import_not_indexed", _span(node))
            elif kind in _IMPORTS and parent_ref is None:
                for record in _import_records(node, data, module, is_package):
                    wildcard = record.pop("wildcard_span")
                    surface.imports.append(record)
                    surface.check()
                    if wildcard is not None:
                        surface.diagnose("star_import_not_expanded", wildcard)
    mentions = _all_mentions(root, data)
    if mentions:
        exports = _literal_all(root, data, mentions[0]) if len(mentions) == 1 else None
        if exports is None:
            surface.diagnose("dynamic_all_not_indexed", _span(mentions[0]))
        else:
            for name, span in exports:
                surface.exports.append({"name": name, "span": span})
                surface.check()
    return "parsed", surface


# ---- assembly -------------------------------------------------------------------------------


def _basis(
    extractor: dict[str, Any],
    lock_block: dict[str, Any],
    environment: dict[str, str],
    package: dict[str, Any],
) -> dict[str, Any]:
    return {
        "importer": IMPORTER,
        "extractor": extractor,
        "lock": lock_block,
        "environment": environment,
        "package": package,
    }


def _snapshot_id(basis: dict[str, Any]) -> str:
    return _digest(basis, FORMAT)


def _span_key(item: dict[str, Any]) -> int:
    return item["span"]["start_byte"] if item["span"] is not None else -1


def build_snapshot(
    selection: Selection, artifact: Artifact, wheel_raw: bytes, limits: Limits
) -> dict[str, Any]:
    """The validated snapshot of `wheel_raw`; nothing is parsed before the digest matches."""
    try:
        return _build(selection, artifact, wheel_raw, limits)
    except DependencyRejected as exc:
        if exc.coordinate is None:
            exc.coordinate = selection.coordinate()
        raise


def _build(selection: Selection, artifact: Artifact, raw: bytes, limits: Limits) -> dict[str, Any]:
    if len(raw) > limits.max_wheel_bytes:
        raise _reject("wheel_too_large")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != artifact.sha256 or (artifact.size is not None and artifact.size != len(raw)):
        raise _reject("artifact_digest_mismatch")
    members = read_members(raw, limits)
    dist_info = check_metadata(members, selection)
    parser, extractor = python_parser()
    for member in members:
        member.role = _role(member.path, dist_info["directory"])
    states: dict[str, tuple[str | None, bool, str, _Surface]] = {}
    used = 0
    for member in members:
        if member.role != "python_source":
            continue
        assert member.data is not None
        name, is_package = module_name(member.path)
        state, surface = extract_member(
            parser, member.data, name, is_package, limits.max_records - used
        )
        used += surface.used()
        states[member.path] = (name, is_package, state, surface)
    used += sum(1 for item in members if item.role in _ROLE_DIAGNOSTIC)
    if used > limits.max_records:
        raise _reject("surface_limit_exceeded")
    package = {
        "ecosystem": lock.ECOSYSTEM,
        "name": selection.name,
        "version": selection.version,
        "coordinate": selection.coordinate_text,
        "source": {"kind": "registry", "registry": selection.registry},
        "artifact": {
            "filename": artifact.filename,
            "sha256": digest,
            "bytes": len(raw),
            "url": artifact.url,
            "digest_check": "equals_lock_entry",
        },
    }
    lock_block = {
        "profile": lock.LOCK_PROFILE,
        "version": lock.LOCK_VERSION,
        "revision": lock.LOCK_REVISION,
        "sha256": selection.lock_sha256,
    }
    environment = dict(selection.environment)
    snapshot_id = _snapshot_id(_basis(extractor, lock_block, environment, package))
    snapshot = _records(
        snapshot_id, members, states, dist_info, extractor, lock_block, environment, package
    )
    validate_snapshot(snapshot)
    return snapshot


def _records(
    sid: str,
    members: list[_Member],
    states: dict[str, tuple[str | None, bool, str, _Surface]],
    dist_info: dict[str, Any],
    extractor: dict[str, Any],
    lock_block: dict[str, Any],
    environment: dict[str, str],
    package: dict[str, Any],
) -> dict[str, Any]:
    digests = {item.path: item.sha256 for item in members}

    def scope(path: str) -> dict[str, Any]:
        return {"package_snapshot_id": sid, "member": path, "member_sha256": digests[path]}

    diagnostics: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    declarations: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []

    def diagnostic(path: str, code: str, span: dict[str, int] | None) -> None:
        start = span["start_byte"] if span is not None else -1
        diagnostics.append(
            {
                "id": _record_id("atlas-pydep-diagnostic.v1", sid, path, code, start),
                "type": "diagnostic",
                **scope(path),
                "code": code,
                "span": span,
            }
        )

    for member in members:
        code = _ROLE_DIAGNOSTIC.get(member.role)
        if code is not None:
            diagnostic(member.path, code, None)
    for path in sorted(states):
        name, is_package, state, surface = states[path]
        for code, span in surface.diagnostics:
            diagnostic(path, code, span)
        refs: list[str] = []
        for item in surface.declarations:
            qualified = item["prefix"] + item["name"]
            start = item["span"]["start_byte"]
            identifier = _record_id(
                "atlas-pydep-declaration.v1", sid, path, item["kind"], qualified, start
            )
            refs.append(identifier)
            parent = item["parent_ref"]
            assert name is not None
            declarations.append(
                {
                    "id": identifier,
                    "type": "declaration",
                    **scope(path),
                    "module": name,
                    "kind": item["kind"],
                    "name": item["name"],
                    "qualified_name": qualified,
                    "symbol": name + "." + qualified,
                    "parent_id": refs[parent] if parent is not None else None,
                    "decorated": item["decorated"],
                    "span": item["span"],
                }
            )
        ordinals: dict[int, int] = {}
        for item in surface.imports:
            start = item["span"]["start_byte"]
            ordinal = ordinals.get(start, 0)
            ordinals[start] = ordinal + 1
            imports.append(
                {
                    "id": _record_id("atlas-pydep-import.v1", sid, path, start, ordinal),
                    "type": "import_path",
                    **scope(path),
                    "module": name,
                    **item,
                }
            )
        for item in surface.exports:
            start = item["span"]["start_byte"]
            exports.append(
                {
                    "id": _record_id("atlas-pydep-export.v1", sid, path, item["name"], start),
                    "type": "export",
                    **scope(path),
                    "module": name,
                    "name": item["name"],
                    "span": item["span"],
                }
            )
        modules.append(
            {
                "id": _record_id("atlas-pydep-module.v1", sid, path),
                "type": "module",
                **scope(path),
                "module": name,
                "is_package": is_package,
                "parse_state": state,
                "incomplete": False,
            }
        )
    flagged = {item["member"] for item in diagnostics}
    for item in modules:
        item["incomplete"] = item["member"] in flagged
    declarations.sort(
        key=lambda r: (r["member"], r["span"]["start_byte"], r["kind"], r["qualified_name"])
    )
    exports.sort(key=lambda r: (r["member"], r["span"]["start_byte"], r["name"]))
    diagnostics.sort(key=lambda r: (r["member"], r["code"], _span_key(r)))
    return {
        "format": FORMAT,
        "snapshot_id": sid,
        "importer": dict(IMPORTER),
        "extractor": extractor,
        "lock": lock_block,
        "environment": environment,
        "package": package,
        "dist_info": dist_info,
        "coverage": dict(COVERAGE),
        "counts": _counts(members, modules, declarations, imports, exports, diagnostics),
        "limitations": list(LIMITATIONS),
        "members": [
            {"path": m.path, "role": m.role, "sha256": m.sha256, "bytes": m.size} for m in members
        ],
        "modules": modules,
        "declarations": declarations,
        "imports": imports,
        "exports": exports,
        "diagnostics": diagnostics,
    }


def _counts(
    members: Sequence[Any],
    modules: Sequence[dict[str, Any]],
    declarations: Sequence[Any],
    imports: Sequence[Any],
    exports: Sequence[Any],
    diagnostics: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    roles = [m.role if isinstance(m, _Member) else m["role"] for m in members]
    return {
        "members": len(members),
        "python_sources": roles.count("python_source"),
        "parsed_modules": sum(1 for item in modules if item["parse_state"] == "parsed"),
        "declarations": len(declarations),
        "imports": len(imports),
        "exports": len(exports),
        "diagnostics": len(diagnostics),
        "diagnostics_by_code": {
            code: sum(1 for item in diagnostics if item["code"] == code)
            for code in DIAGNOSTIC_CODES
        },
    }


# ---- validation -----------------------------------------------------------------------------


def _check(condition: Any) -> None:
    if not condition:
        raise DependencyRejected("invalid_snapshot")


def _exact(value: Any, keys: set[str]) -> None:
    _check(isinstance(value, dict) and set(value) == keys)


def _natural(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _span_ok(span: Any, size: int) -> bool:
    if not isinstance(span, dict) or set(span) != {
        "start_byte",
        "end_byte",
        "start_line",
        "start_col",
        "end_line",
        "end_col",
    }:
        return False
    return (
        _natural(span["start_byte"])
        and _natural(span["end_byte"])
        and span["start_byte"] < span["end_byte"] <= size
        and _natural(span["start_line"], 1)
        and _natural(span["end_line"], 1)
        and span["end_line"] >= span["start_line"]
        and _natural(span["start_col"])
        and _natural(span["end_col"])
    )


SCOPE_KEYS = {"id", "type", "package_snapshot_id", "member", "member_sha256"}
TOP_KEYS = {
    "format", "snapshot_id", "importer", "extractor", "lock", "environment", "package",
    "dist_info", "coverage", "counts", "limitations", "members", "modules", "declarations",
    "imports", "exports", "diagnostics",
}  # fmt: skip


def validate_snapshot(snapshot: Any) -> None:
    """Raise `invalid_snapshot` unless `snapshot` is closed, ordered and self-consistent."""
    try:
        _validate(snapshot)
    except DependencyRejected:
        raise
    except (KeyError, TypeError, IndexError, AttributeError, ValueError):
        raise _reject("invalid_snapshot") from None


def _validate(snapshot: Any) -> None:
    _exact(snapshot, TOP_KEYS)
    _check(snapshot["format"] == FORMAT and snapshot["importer"] == IMPORTER)
    sid = snapshot["snapshot_id"]
    _check(ev.hash_value(sid))
    extractor = snapshot["extractor"]
    _exact(extractor, {"parser", "language", "grammar_version", "abi_version"})
    _check(extractor["parser"] == "tree-sitter" and extractor["language"] == "python")
    _check(
        isinstance(extractor["grammar_version"], str)
        and re.fullmatch(r"[0-9A-Za-z.+!_-]{1,64}", extractor["grammar_version"]) is not None
        and _natural(extractor["abi_version"], 1)
    )
    lock_block = snapshot["lock"]
    _exact(lock_block, {"profile", "version", "revision", "sha256"})
    _check(
        lock_block["profile"] == lock.LOCK_PROFILE
        and lock_block["version"] == lock.LOCK_VERSION
        and lock_block["revision"] == lock.LOCK_REVISION
        and ev.hash_value(lock_block["sha256"])
    )
    environment = snapshot["environment"]
    _check(isinstance(environment, dict))
    _check(lock.canonical_environment(environment) == environment)
    package = snapshot["package"]
    _exact(package, {"ecosystem", "name", "version", "coordinate", "source", "artifact"})
    _check(package["ecosystem"] == lock.ECOSYSTEM)
    _check(
        isinstance(package["name"], str)
        and lock.normalize_name(package["name"]) == package["name"]
        and package["name"] != ""
    )
    _check(isinstance(package["version"], str) and package["version"] != "")
    _check(package["coordinate"] == "pypi:" + package["name"] + "@" + package["version"])
    _exact(package["source"], {"kind", "registry"})
    _check(
        package["source"]["kind"] == "registry" and isinstance(package["source"]["registry"], str)
    )
    artifact = package["artifact"]
    _exact(artifact, {"filename", "sha256", "bytes", "url", "digest_check"})
    _check(
        isinstance(artifact["filename"], str)
        and ev.hash_value(artifact["sha256"])
        and _natural(artifact["bytes"])
        and isinstance(artifact["url"], str)
        and artifact["digest_check"] == "equals_lock_entry"
    )
    _check(_snapshot_id(_basis(extractor, lock_block, environment, package)) == sid)
    dist = snapshot["dist_info"]
    _exact(dist, {"directory", "name", "version", "wheel_version", "root_is_purelib", "tags"})
    _check(
        all(isinstance(dist[key], str) for key in ("directory", "name", "version", "wheel_version"))
        and dist["root_is_purelib"] is True
        and isinstance(dist["tags"], list)
        and dist["tags"] != []
        and dist["tags"] == sorted(set(dist["tags"]))
        and all(isinstance(tag, str) for tag in dist["tags"])
    )
    _check(snapshot["coverage"] == COVERAGE and snapshot["limitations"] == list(LIMITATIONS))
    _validate_records(snapshot, sid)


def _validate_records(snapshot: dict[str, Any], sid: str) -> None:
    members = snapshot["members"]
    _check(isinstance(members, list))
    by_path: dict[str, dict[str, Any]] = {}
    for item in members:
        _exact(item, {"path", "role", "sha256", "bytes"})
        _check(isinstance(item["path"], str) and ev.relative_path(item["path"]))
        _check(item["role"] in MEMBER_ROLES and ev.hash_value(item["sha256"]))
        _check(_natural(item["bytes"]) and item["path"] not in by_path)
        by_path[item["path"]] = item
    _check([m["path"] for m in members] == sorted(by_path))
    sources = [m for m in members if m["role"] == "python_source"]
    modules = snapshot["modules"]
    _check(
        isinstance(modules, list) and [m["member"] for m in modules] == [s["path"] for s in sources]
    )
    module_of: dict[str, dict[str, Any]] = {}
    for item in modules:
        _exact(item, SCOPE_KEYS | {"module", "is_package", "parse_state", "incomplete"})
        _scope(item, "module", sid, by_path)
        _check(item["id"] == _record_id("atlas-pydep-module.v1", sid, item["member"]))
        name, is_package = module_name(item["member"])
        _check(item["module"] == name and item["is_package"] is is_package)
        _check(item["parse_state"] in PARSE_STATES and type(item["incomplete"]) is bool)
        _check((item["parse_state"] == "not_parsed") == (name is None))
        module_of[item["member"]] = item
    diagnostics = snapshot["diagnostics"]
    _check(isinstance(diagnostics, list))
    per_member: dict[str, list[str]] = {}
    for item in diagnostics:
        _exact(item, SCOPE_KEYS | {"code", "span"})
        _scope(item, "diagnostic", sid, by_path)
        _check(item["code"] in DIAGNOSTIC_CODES)
        size = by_path[item["member"]]["bytes"]
        _check(item["span"] is None or _span_ok(item["span"], size))
        _check(
            item["id"]
            == _record_id(
                "atlas-pydep-diagnostic.v1", sid, item["member"], item["code"], _span_key(item)
            )
        )
        per_member.setdefault(item["member"], []).append(item["code"])
    _check(
        [(d["member"], d["code"], _span_key(d)) for d in diagnostics]
        == sorted((d["member"], d["code"], _span_key(d)) for d in diagnostics)
    )
    for member in members:
        wanted = _ROLE_DIAGNOSTIC.get(member["role"])
        codes = per_member.get(member["path"], [])
        if member["role"] == "dist_info":
            _check(codes == [])
        elif wanted is not None:
            _check(codes == [wanted])
    for path, item in module_of.items():
        codes = per_member.get(path, [])
        _check(item["incomplete"] is (codes != []))
        for state, code in (
            ("syntax_error", "syntax_error"),
            ("non_utf8_source", "non_utf8_source"),
            ("not_parsed", "non_importable_module_path"),
        ):
            _check((item["parse_state"] == state) == (code in codes))
        if item["parse_state"] != "parsed":
            _check(len(codes) == 1)
    declarations = snapshot["declarations"]
    _check(isinstance(declarations, list))
    seen: dict[str, dict[str, Any]] = {}
    for item in declarations:
        _exact(
            item,
            SCOPE_KEYS
            | {
                "module",
                "kind",
                "name",
                "qualified_name",
                "symbol",
                "parent_id",
                "decorated",
                "span",
            },
        )
        _scope(item, "declaration", sid, by_path)
        _check(item["member"] in module_of and module_of[item["member"]]["parse_state"] == "parsed")
        _check(item["module"] == module_of[item["member"]]["module"])
        _check(item["kind"] in DECLARATION_KINDS and type(item["decorated"]) is bool)
        _check(isinstance(item["name"], str) and item["name"] != "")
        _check(_span_ok(item["span"], by_path[item["member"]]["bytes"]))
        _check(
            item["id"]
            == _record_id(
                "atlas-pydep-declaration.v1",
                sid,
                item["member"],
                item["kind"],
                item["qualified_name"],
                item["span"]["start_byte"],
            )
        )
        _check(item["id"] not in seen)
        seen[item["id"]] = item
    for item in declarations:
        parent_id = item["parent_id"]
        prefix = ""
        if parent_id is not None:
            parent = seen[parent_id]
            _check(parent["kind"] == "class" and parent["member"] == item["member"])
            prefix = parent["qualified_name"] + "."
        is_method = item["kind"] in ("method", "async_method")
        if item["kind"] != "class":
            _check(is_method == (parent_id is not None))
        _check(item["qualified_name"] == prefix + item["name"])
        _check(item["symbol"] == item["module"] + "." + item["qualified_name"])
    _check(
        [
            (d["member"], d["span"]["start_byte"], d["kind"], d["qualified_name"])
            for d in declarations
        ]
        == sorted(
            (d["member"], d["span"]["start_byte"], d["kind"], d["qualified_name"])
            for d in declarations
        )
    )
    _validate_imports(snapshot, sid, by_path, module_of)
    _validate_exports(snapshot, sid, by_path, module_of)
    _check(
        snapshot["counts"]
        == _counts(
            members, modules, declarations, snapshot["imports"], snapshot["exports"], diagnostics
        )
    )
    ids = [
        x["id"]
        for key in ("modules", "declarations", "imports", "exports", "diagnostics")
        for x in snapshot[key]
    ]
    _check(len(ids) == len(set(ids)))


def _scope(item: dict[str, Any], kind: str, sid: str, by_path: dict[str, Any]) -> None:
    _check(
        item["type"] == kind and item["package_snapshot_id"] == sid and ev.hash_value(item["id"])
    )
    _check(item["member"] in by_path and item["member_sha256"] == by_path[item["member"]]["sha256"])


def _validate_imports(
    snapshot: dict[str, Any], sid: str, by_path: dict[str, Any], module_of: dict[str, Any]
) -> None:
    imports = snapshot["imports"]
    _check(isinstance(imports, list))
    ordinals: dict[tuple[str, int], int] = {}
    for item in imports:
        _exact(
            item,
            SCOPE_KEYS
            | {
                "module",
                "statement",
                "level",
                "target",
                "name",
                "alias",
                "resolved_module",
                "span",
            },
        )
        _scope(item, "import_path", sid, by_path)
        owner = module_of[item["member"]]
        _check(owner["parse_state"] == "parsed" and item["module"] == owner["module"])
        _check(item["statement"] in ("import", "from") and _natural(item["level"]))
        _check(isinstance(item["target"], str))
        _check(item["name"] is None or isinstance(item["name"], str))
        _check(item["alias"] is None or isinstance(item["alias"], str))
        _check(item["resolved_module"] is None or isinstance(item["resolved_module"], str))
        _check(_span_ok(item["span"], by_path[item["member"]]["bytes"]))
        if item["statement"] == "import":
            _check(item["level"] == 0 and item["name"] is None)
            _check(item["resolved_module"] == item["target"])
        else:
            _check(item["name"] is not None)
            _check(
                item["resolved_module"]
                == _resolve(item["module"], owner["is_package"], item["level"], item["target"])
            )
        key = (item["member"], item["span"]["start_byte"])
        ordinal = ordinals.get(key, 0)
        ordinals[key] = ordinal + 1
        _check(
            item["id"] == _record_id("atlas-pydep-import.v1", sid, item["member"], key[1], ordinal)
        )
    keys = [(i["member"], i["span"]["start_byte"]) for i in imports]
    _check(keys == sorted(keys))


def _validate_exports(
    snapshot: dict[str, Any], sid: str, by_path: dict[str, Any], module_of: dict[str, Any]
) -> None:
    exports = snapshot["exports"]
    _check(isinstance(exports, list))
    for item in exports:
        _exact(item, SCOPE_KEYS | {"module", "name", "span"})
        _scope(item, "export", sid, by_path)
        owner = module_of[item["member"]]
        _check(owner["parse_state"] == "parsed" and item["module"] == owner["module"])
        _check(
            isinstance(item["name"], str) and re.fullmatch(_IDENTIFIER, item["name"]) is not None
        )
        _check(_span_ok(item["span"], by_path[item["member"]]["bytes"]))
        _check(
            item["id"]
            == _record_id(
                "atlas-pydep-export.v1",
                sid,
                item["member"],
                item["name"],
                item["span"]["start_byte"],
            )
        )
    keys = [(e["member"], e["span"]["start_byte"], e["name"]) for e in exports]
    _check(keys == sorted(keys))


# ---- entry points ---------------------------------------------------------------------------


def import_dependency(
    lock_raw: bytes,
    package: str,
    environment: dict[str, str],
    supplied: Sequence[tuple[str, Callable[[], bytes]]],
    limits: Limits,
) -> dict[str, Any]:
    """Select the locked variant, match the supplied wheel by name, then build the snapshot."""
    selection = lock.select_package(lock_raw, package, environment, limits)
    index, artifact = lock.match_supplied(selection, [name for name, _ in supplied])
    raw = supplied[index][1]()
    return build_snapshot(selection, artifact, raw, limits)


def summary_document(snapshot: dict[str, Any], wheels_ignored: int = 0) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "snapshot_id": snapshot["snapshot_id"],
        "coordinate": snapshot["package"]["coordinate"],
        "artifact_sha256": snapshot["package"]["artifact"]["sha256"],
        "coverage": snapshot["coverage"],
        "counts": snapshot["counts"],
        "wheels_ignored": wheels_ignored,
    }
