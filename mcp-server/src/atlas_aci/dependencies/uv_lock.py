"""uv.lock reading: profile, package selection for one environment, locked pure-wheel set.

Pure and bounded: it takes BYTES and returns values, never touches the filesystem and never
evaluates or imports anything it reads. Every outcome that depends on the interpreter's TOML
or integer parsing is pinned by a byte pre-scan that runs first. Regular expressions use explicit
ASCII classes and `re.fullmatch` only.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

LOCK_PROFILE = "atlas-uv-lock-profile.v1"
LOCK_VERSION = 1
LOCK_REVISION = 3
ECOSYSTEM = "pypi"
ENV_KEYS = (
    "implementation_name",
    "os_name",
    "platform_machine",
    "platform_python_implementation",
    "platform_system",
    "python_full_version",
    "sys_platform",
)
REJECTIONS = (
    "lock_too_large",
    "lock_invalid",
    "unsupported_lock_profile",
    "package_not_in_lock",
    "unresolved_lock_variant",
    "unsupported_source_kind",
    "no_locked_pure_wheel",
    "missing_dependency_source",
    "ambiguous_dependency_source",
    "wheel_too_large",
    "artifact_digest_mismatch",
    "invalid_wheel",
    "unsafe_archive_member",
    "archive_limit_exceeded",
    "wheel_metadata_mismatch",
    "not_pure_python",
    "python_parser_unavailable",
    "surface_limit_exceeded",
    "invalid_snapshot",
)
VARIANT_DETAILS = (
    "environment_incomplete",
    "marker_limit_exceeded",
    "multiple_matching_variants",
    "no_matching_variant",
    "unmarked_variant",
    "unsupported_marker",
)

# name -> (default, minimum, maximum); a value outside the range or not an int is a usage error.
LIMITS: dict[str, tuple[int, int, int]] = {
    "max_lock_bytes": (33554432, 1, 268435456),
    "max_wheel_bytes": (67108864, 1, 268435456),
    "max_members": (10000, 1, 100000),
    "max_total_uncompressed_bytes": (268435456, 1, 1073741824),
    "max_member_bytes": (16777216, 1, 268435456),
    "max_records": (200000, 1, 1000000),
    "max_compression_ratio": (200, 1, 10000),
}

MAX_TOML_DEPTH = 16
MAX_DIGIT_RUN = 128
MAX_MARKERS = 64
MARKER_MAX_CHARS = 1024
MARKER_MAX_PAREN_DEPTH = 8
MARKER_MAX_TOKENS = 256
MARKER_MAX_LITERAL = 256

_NAME_ARGUMENT = r"[A-Za-z0-9]([A-Za-z0-9._-]{0,198}[A-Za-z0-9])?"
_VERSION = r"[0-9][0-9A-Za-z!+._-]{0,63}"
_URL = r"https?://[A-Za-z0-9._~:/?#@!&'()*+,;=%-]{1,2048}"
_URL_NO_QUERY = r"https?://[A-Za-z0-9._~:/@!&'()*+,;=%-]{1,2048}"
_PY_FULL = r"[0-9]{1,4}(\.[0-9]{1,4}){1,2}"
_ENV_OTHER = r"[A-Za-z0-9._+-]{1,64}"
_MARKER_VERSION = r"[0-9]{1,4}(\.[0-9]{1,4}){0,3}"
_MARKER_PREFIX = r"[0-9]{1,4}(\.[0-9]{1,4}){0,3}\.\*"
_WHEEL = (
    r"([A-Za-z0-9_.]+)-([0-9][A-Za-z0-9_.!+]*)(-[0-9][A-Za-z0-9_.]*)?"
    r"-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)\.whl"
)
_UPPER_TO_LOWER = {code: code + 32 for code in range(65, 91)}

STRING_VARIABLES = frozenset(ENV_KEYS) - {"python_full_version"}
VERSION_VARIABLES = frozenset({"python_full_version", "python_version"})
OPERATORS = ("==", "!=", "<=", ">=", "<", ">")


class DependencyRejected(ValueError):
    """The import stopped; `reason` is a closed code and `coordinate` the selected coordinate."""

    def __init__(
        self,
        reason: str,
        detail: str | None = None,
        coordinate: dict[str, Any] | None = None,
    ) -> None:
        if reason not in REJECTIONS or (detail is not None and detail not in VARIANT_DETAILS):
            raise ValueError("unknown rejection")
        super().__init__("dependency_rejected: " + reason)
        self.reason = reason
        self.detail = detail
        self.coordinate = coordinate


class DependencyUsageError(ValueError):
    """The invocation is invalid; `what` names the argument (a limit name, env, package)."""

    def __init__(self, what: str) -> None:
        super().__init__("dependency_usage: " + what)
        self.what = what


class MarkerError(ValueError):
    """A marker cannot be used; `detail` is a member of VARIANT_DETAILS."""

    def __init__(self, detail: str) -> None:
        super().__init__("marker_error: " + detail)
        self.detail = detail


@dataclass(frozen=True)
class Limits:
    max_lock_bytes: int
    max_wheel_bytes: int
    max_members: int
    max_total_uncompressed_bytes: int
    max_member_bytes: int
    max_records: int
    max_compression_ratio: int


def normalize_limits(chosen: Mapping[str, int | None] | None = None) -> Limits:
    """Defaults for absent values; a bool, a non-int or an out-of-range value is a usage error."""
    chosen = {} if chosen is None else chosen
    unknown = sorted(set(chosen) - set(LIMITS))
    if unknown:
        raise DependencyUsageError(unknown[0])
    values: dict[str, int] = {}
    for name, (default, low, high) in LIMITS.items():
        value = chosen.get(name)
        if value is None:
            values[name] = default
        elif type(value) is not int or not low <= value <= high:
            raise DependencyUsageError(name)
        else:
            values[name] = value
    return Limits(**values)


def ascii_lower(text: str) -> str:
    return text.translate(_UPPER_TO_LOWER)


def normalize_name(name: str) -> str:
    """PEP 503 normalisation over ASCII only (no Unicode-database dependence)."""
    return ascii_lower(re.sub(r"[-_.]+", "-", name))


def parse_package_argument(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(_NAME_ARGUMENT, value) is None:
        raise DependencyUsageError("package")
    return normalize_name(value)


def _canonical_full_version(value: str) -> str:
    return ".".join(str(int(part)) for part in value.split("."))


def canonical_environment(items: Mapping[str, Any]) -> dict[str, str]:
    """Validated environment; python_full_version components lose leading zeros."""
    result: dict[str, str] = {}
    for key, value in items.items():
        if key not in ENV_KEYS or not isinstance(value, str):
            raise DependencyUsageError("env")
        if key == "python_full_version":
            if re.fullmatch(_PY_FULL, value) is None:
                raise DependencyUsageError("env")
            value = _canonical_full_version(value)
        elif re.fullmatch(_ENV_OTHER, value) is None:
            raise DependencyUsageError("env")
        result[key] = value
    return dict(sorted(result.items()))


def parse_environment(items: Sequence[str]) -> dict[str, str]:
    """`KEY=VALUE` items to a canonical mapping; unknown, duplicate or malformed is usage."""
    pairs: dict[str, str] = {}
    for item in items:
        if not isinstance(item, str) or "=" not in item:
            raise DependencyUsageError("env")
        key, value = item.split("=", 1)
        if key in pairs:
            raise DependencyUsageError("env")
        pairs[key] = value
    return canonical_environment(pairs)


# ---- markers --------------------------------------------------------------------------------


def _version_tuple(text: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in text.split(".")]
    first, second, third, fourth = [*parts, 0, 0, 0, 0][:4]
    return (first, second, third, fourth)


def _scan_marker(text: str) -> list[tuple[str, str]]:
    """Tokens of one marker; every unsupported construct raises `unsupported_marker`."""
    if len(text) > MARKER_MAX_CHARS:
        raise MarkerError("marker_limit_exceeded")
    tokens: list[tuple[str, str]] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char in " \t":
            index += 1
            continue
        if len(tokens) >= MARKER_MAX_TOKENS:
            raise MarkerError("marker_limit_exceeded")
        if char in "()":
            tokens.append((char, char))
            index += 1
        elif char in "'\"":
            end = text.find(char, index + 1)
            if end < 0:
                raise MarkerError("unsupported_marker")
            literal = text[index + 1 : end]
            if len(literal) > MARKER_MAX_LITERAL or re.fullmatch(r"[ -\[\]-~]*", literal) is None:
                raise MarkerError("unsupported_marker")
            tokens.append(("literal", literal))
            index = end + 1
        elif char in "=!<>~":
            run = re.match(r"[=!<>~]+", text[index:])
            assert run is not None
            operator = run.group(0)
            if operator not in OPERATORS:
                raise MarkerError("unsupported_marker")
            tokens.append(("op", operator))
            index += len(operator)
        elif re.fullmatch(r"[A-Za-z_]", char) is not None:
            word = re.match(r"[A-Za-z_][A-Za-z0-9_.]*", text[index:])
            assert word is not None
            value = word.group(0)
            tokens.append(("connective" if value in ("and", "or") else "word", value))
            index += len(value)
        else:
            raise MarkerError("unsupported_marker")
    return tokens


def parse_marker(text: str) -> tuple[tuple[Any, ...], ...]:
    """Compile one marker to postfix; `expr := term (or term)*`, `term := atom (and atom)*`."""
    tokens = _scan_marker(text)
    if not tokens:
        raise MarkerError("unsupported_marker")
    output: list[tuple[Any, ...]] = []
    stack: list[str] = []
    expect_operand = True
    depth = 0
    index = 0
    while index < len(tokens):
        kind, value = tokens[index]
        if expect_operand:
            if kind == "(":
                stack.append("(")
                depth += 1
                if depth > MARKER_MAX_PAREN_DEPTH:
                    raise MarkerError("marker_limit_exceeded")
                index += 1
                continue
            if kind != "word" or index + 2 >= len(tokens):
                raise MarkerError("unsupported_marker")
            (operator_kind, operator), (literal_kind, literal) = tokens[index + 1 : index + 3]
            if operator_kind != "op" or literal_kind != "literal":
                raise MarkerError("unsupported_marker")
            if value in VERSION_VARIABLES:
                plain = re.fullmatch(_MARKER_VERSION, literal) is not None
                prefix = re.fullmatch(_MARKER_PREFIX, literal) is not None
                if not plain and not (prefix and operator in ("==", "!=")):
                    raise MarkerError("unsupported_marker")
            elif value in STRING_VARIABLES:
                if operator not in ("==", "!="):
                    raise MarkerError("unsupported_marker")
            else:
                raise MarkerError("unsupported_marker")
            output.append(("cmp", value, operator, literal))
            expect_operand = False
            index += 3
            continue
        if kind == ")":
            while stack and stack[-1] != "(":
                output.append((stack.pop(),))
            if not stack:
                raise MarkerError("unsupported_marker")
            stack.pop()
            depth -= 1
        elif kind == "connective":
            rank = 2 if value == "and" else 1
            while stack and stack[-1] != "(" and (2 if stack[-1] == "and" else 1) >= rank:
                output.append((stack.pop(),))
            stack.append(value)
            expect_operand = True
        else:
            raise MarkerError("unsupported_marker")
        index += 1
    if expect_operand or "(" in stack:
        raise MarkerError("unsupported_marker")
    while stack:
        output.append((stack.pop(),))
    return tuple(output)


def _prefix_match(left: tuple[int, int, int, int], literal: str) -> bool:
    """`== '3.10.*'`: the leading components of the version equal the stated prefix."""
    prefix = tuple(int(part) for part in literal[:-2].split("."))
    return left[: len(prefix)] == prefix


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "==":
        return bool(left == right)
    if operator == "!=":
        return bool(left != right)
    if operator == "<":
        return bool(left < right)
    if operator == "<=":
        return bool(left <= right)
    if operator == ">":
        return bool(left > right)
    return bool(left >= right)


def marker_variables(compiled: Sequence[tuple[Any, ...]]) -> set[str]:
    return {item[1] for item in compiled if item[0] == "cmp"}


def evaluate_marker(compiled: Sequence[tuple[Any, ...]], environment: Mapping[str, str]) -> bool:
    values: list[bool] = []
    for item in compiled:
        if item[0] == "cmp":
            _, variable, operator, literal = item
            if variable == "python_version":
                full = environment.get("python_full_version")
                if full is None:
                    raise MarkerError("environment_incomplete")
                left: Any = _version_tuple(".".join(full.split(".")[:2]))
                right: Any = literal
            elif variable == "python_full_version":
                full = environment.get("python_full_version")
                if full is None:
                    raise MarkerError("environment_incomplete")
                left, right = _version_tuple(full), literal
            else:
                left = environment.get(variable)
                if left is None:
                    raise MarkerError("environment_incomplete")
                right = literal
            if variable in VERSION_VARIABLES:
                if literal.endswith(".*"):
                    hit = _prefix_match(left, literal)
                    values.append(hit if operator == "==" else not hit)
                    continue
                right = _version_tuple(literal)
            values.append(_compare(left, operator, right))
        else:
            second, first = values.pop(), values.pop()
            values.append(first and second if item[0] == "and" else first or second)
    return values[0]


# ---- filenames ------------------------------------------------------------------------------


def parse_wheel_filename(name: str) -> dict[str, Any] | None:
    """PEP 427 filename parts, or None when `name` is not a wheel filename."""
    if len(name) > 255:
        return None
    match = re.fullmatch(_WHEEL, name)
    if match is None:
        return None
    dist, version, build, python, abi, platform = match.groups()
    return {
        "dist": dist,
        "version": version,
        "build": build,
        "python": python.split("."),
        "abi": abi.split("."),
        "platform": platform.split("."),
    }


# ---- the lock -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Artifact:
    filename: str
    sha256: str
    size: int | None
    url: str


@dataclass(frozen=True)
class Selection:
    name: str
    version: str
    registry: str
    lock_sha256: str
    environment: tuple[tuple[str, str], ...]
    artifacts: tuple[Artifact, ...]

    @property
    def coordinate_text(self) -> str:
        return ECOSYSTEM + ":" + self.name + "@" + self.version

    def coordinate(self) -> dict[str, Any]:
        return {
            "ecosystem": ECOSYSTEM,
            "name": self.name,
            "version": self.version,
            "coordinate": self.coordinate_text,
            "source": {"kind": "registry", "registry": self.registry},
            "expected_artifacts": [
                {"filename": item.filename, "sha256": item.sha256}
                for item in sorted(self.artifacts, key=lambda a: a.filename)
            ],
        }


def _invalid() -> DependencyRejected:
    return DependencyRejected("lock_invalid")


def prescan(raw: bytes, limits: Limits) -> str:
    """Byte-level checks that make TOML parsing interpreter-independent; returns the text."""
    if len(raw) > limits.max_lock_bytes:
        raise DependencyRejected("lock_too_large")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise _invalid()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _invalid() from None
    depth = braces = 0
    for match in re.finditer(rb"[\[\]{}\n]", raw):
        char = match.group(0)
        if char == b"[":
            depth += 1
        elif char == b"{":
            depth += 1
            braces += 1
        elif char == b"]":
            depth = max(0, depth - 1)
        elif char == b"}":
            depth = max(0, depth - 1)
            braces = max(0, braces - 1)
        elif braces > 0:
            raise _invalid()
        if depth > MAX_TOML_DEPTH:
            raise _invalid()
    for run in re.finditer(rb"(\\+)[ex]", raw):
        if len(run.group(1)) % 2 == 1:
            raise _invalid()
    if re.search(rb"[0-9_]{" + str(MAX_DIGIT_RUN + 1).encode() + rb"}", raw) is not None:
        raise _invalid()
    return text


def _refuse_float(_: str) -> float:
    raise ValueError("float")


def _int(value: Any) -> bool:
    return type(value) is int


def load_lock(raw: bytes, limits: Limits) -> list[dict[str, Any]]:
    """The profile-checked `package` table list of one lock."""
    text = prescan(raw, limits)
    try:
        document = tomllib.loads(text, parse_float=_refuse_float)
    except Exception:
        raise _invalid() from None
    if not isinstance(document, dict) or not _int(document.get("version")):
        raise _invalid()
    if "revision" not in document:
        raise DependencyRejected("unsupported_lock_profile")
    if not _int(document["revision"]):
        raise _invalid()
    if (document["version"], document["revision"]) != (LOCK_VERSION, LOCK_REVISION):
        raise DependencyRejected("unsupported_lock_profile")
    packages = document.get("package")
    if not isinstance(packages, list) or not all(
        isinstance(item, dict) and isinstance(item.get("name"), str) for item in packages
    ):
        raise _invalid()
    return packages


def _wheel_entries(entry: dict[str, Any], name: str, version: str) -> list[Artifact]:
    wheels = entry.get("wheels", [])
    if not isinstance(wheels, list) or not all(isinstance(item, dict) for item in wheels):
        raise _invalid()
    kept: list[Artifact] = []
    for wheel in wheels:
        url, digest, size = wheel.get("url"), wheel.get("hash"), wheel.get("size")
        if not isinstance(url, str) or re.fullmatch(_URL_NO_QUERY, url) is None:
            continue
        filename = url.rsplit("/", 1)[1]
        parts = parse_wheel_filename(filename)
        if parts is None or normalize_name(parts["dist"]) != name:
            continue
        if parts["version"] != version.replace("-", "_"):
            continue
        if parts["abi"] != ["none"] or parts["platform"] != ["any"]:
            continue
        if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            continue
        if size is not None and (not _int(size) or size < 0):
            continue
        kept.append(Artifact(filename, digest[7:], size, url))
    if len({item.filename for item in kept}) != len(kept):
        raise _invalid()
    return kept


def select_package(
    lock_raw: bytes, package: str, environment: Mapping[str, str], limits: Limits
) -> Selection:
    """Exactly one locked variant for `environment`, before any artifact is read."""
    name = parse_package_argument(package)
    env = canonical_environment(environment)
    packages = load_lock(lock_raw, limits)
    candidates = [item for item in packages if normalize_name(item["name"]) == name]
    if not candidates:
        raise DependencyRejected("package_not_in_lock")
    compiled: list[list[tuple[tuple[Any, ...], ...]]] = []
    for entry in candidates:
        version = entry.get("version")
        if not isinstance(version, str) or re.fullmatch(_VERSION, version) is None:
            raise _invalid()
        markers = entry.get("resolution-markers")
        if markers is None:
            compiled.append([])
            continue
        if (
            not isinstance(markers, list)
            or not 1 <= len(markers) <= MAX_MARKERS
            or not all(isinstance(item, str) for item in markers)
        ):
            raise _invalid()
        try:
            compiled.append([parse_marker(item) for item in markers])
        except MarkerError as exc:
            raise DependencyRejected("unresolved_lock_variant", exc.detail) from None
    truth: list[bool | None] = []
    try:
        for markers_of in compiled:
            truth.append(any([evaluate_marker(item, env) for item in markers_of]) or None)
    except MarkerError as exc:
        raise DependencyRejected("unresolved_lock_variant", exc.detail) from None
    if len(candidates) == 1:
        if compiled[0] and truth[0] is None:
            raise DependencyRejected("unresolved_lock_variant", "no_matching_variant")
        chosen = candidates[0]
    else:
        if any(not markers_of for markers_of in compiled):
            raise DependencyRejected("unresolved_lock_variant", "unmarked_variant")
        matching = [item for item, value in zip(candidates, truth, strict=True) if value]
        if not matching:
            raise DependencyRejected("unresolved_lock_variant", "no_matching_variant")
        if len(matching) > 1:
            raise DependencyRejected("unresolved_lock_variant", "multiple_matching_variants")
        chosen = matching[0]
    version = chosen["version"]
    base = {
        "ecosystem": ECOSYSTEM,
        "name": name,
        "version": version,
        "coordinate": ECOSYSTEM + ":" + name + "@" + version,
        "source": None,
        "expected_artifacts": [],
    }
    source = chosen.get("source")
    registry = source.get("registry") if isinstance(source, dict) and len(source) == 1 else None
    if not isinstance(registry, str) or re.fullmatch(_URL, registry) is None:
        raise DependencyRejected("unsupported_source_kind", coordinate=base)
    artifacts = _wheel_entries(chosen, name, version)
    selection = Selection(
        name,
        version,
        registry,
        hashlib.sha256(lock_raw).hexdigest(),
        tuple(env.items()),
        tuple(sorted(artifacts, key=lambda a: a.filename)),
    )
    if not artifacts:
        raise DependencyRejected("no_locked_pure_wheel", coordinate=selection.coordinate())
    return selection


def match_supplied(selection: Selection, basenames: Sequence[str]) -> tuple[int, Artifact]:
    """The one supplied basename that EQUALS a locked pure-wheel filename (nothing is opened)."""
    by_name = {item.filename: item for item in selection.artifacts}
    hits = [index for index, base in enumerate(basenames) if base in by_name]
    if not hits:
        raise DependencyRejected("missing_dependency_source", coordinate=selection.coordinate())
    if len(hits) > 1:
        raise DependencyRejected("ambiguous_dependency_source", coordinate=selection.coordinate())
    return hits[0], by_name[basenames[hits[0]]]
