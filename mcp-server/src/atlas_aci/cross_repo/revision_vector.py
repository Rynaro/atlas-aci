"""The explicit mapping, the revision vector and the two read-only leases (ACI-S30).

Pure helpers plus the lease wiring. The mapping is bytes only: it names two repositories by
operator label and exact snapshot id, one locked package and where that package's sources live
in the provider; it never carries a path or a root. Regular expressions use explicit ASCII
classes and `re.fullmatch` only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from atlas_aci.dependencies import uv_lock as ul
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import DiffUsageError, Side
from atlas_aci.storage import evidence as ev

MAPPING_FORMAT = "atlas-cross-repo-mapping.v1"
VECTOR_FORMAT = "atlas-cross-repo-vector.v1"
ROLES = ("consumer", "provider")
PROVIDER_DETAILS = (
    "snapshot_not_active",
    "index_unavailable",
    "source_snapshot_unavailable",
    "index_upgrade_required",
)
LEASE_REASONS = ("index_unavailable", "source_snapshot_unavailable", "index_upgrade_required")
# The reasons `select_package` can raise; they pass through unchanged.
LOCK_REASONS = (
    "lock_invalid",
    "unsupported_lock_profile",
    "package_not_in_lock",
    "unresolved_lock_variant",
    "unsupported_source_kind",
    "no_locked_pure_wheel",
)
REJECTIONS = (
    "mapping_too_large",
    "invalid_mapping",
    "unsupported_mapping_version",
    "repository_not_allowlisted",
    "same_repository",
    "lock_too_large",
    "lock_digest_mismatch",
    *LOCK_REASONS,
    "package_snapshot_too_large",
    "package_snapshot_digest_mismatch",
    "invalid_package_snapshot",
    "previous_overlay_too_large",
    "invalid_previous_overlay",
    "previous_overlay_unrelated",
    "consumer_index_unavailable",
    "consumer_source_snapshot_unavailable",
    "consumer_index_upgrade_required",
    "consumer_snapshot_mismatch",
    "invalid_consumer_evidence",
    "invalid_provider_evidence",
    "reference_limit_exceeded",
    "overlay_too_large",
    "invalid_overlay",
    "overlay_mapping_mismatch",
    "stale_revision_vector",
    "unknown_seed",
)
USAGE_REASONS = ("graph_namespace", "repository_overlap")
MAX_INT_DIGITS = 20

_REPOSITORY_ID = r"[a-z0-9][a-z0-9._-]{0,63}"
COORDINATE = r"pypi:([a-z0-9][a-z0-9-]{0,198})@([0-9][0-9A-Za-z!+._-]{0,63})"
SOURCE_PREFIX = r"([A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}/){0,8}"


class CrossRepoRejected(ValueError):
    """The link or the impact stopped; `reason` is a closed code."""

    def __init__(self, reason: str) -> None:
        if reason not in REJECTIONS:
            raise ValueError("unknown rejection")
        super().__init__("cross_repo_rejected: " + reason)
        self.reason = reason


class CrossRepoUsageError(ValueError):
    """The invocation is invalid; `what` names a limit, a budget, the seed or a usage reason."""

    def __init__(self, what: str) -> None:
        super().__init__("cross_repo_usage: " + what)
        self.what = what


def sha(value: Any, tag: str) -> str:
    return hashlib.sha256((tag + ":" + ev.canonical(value)).encode("utf-8")).hexdigest()


# ---- the one strict JSON loader ---------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def _refuse(_: str) -> Any:
    raise ValueError("number or constant not allowed")


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_INT_DIGITS:
        raise ValueError("integer too long")
    return int(text)


def load_strict(raw: bytes, numbers: bool) -> Any:
    """One strict JSON document: UTF-8 without BOM, no duplicate key, no NaN or Infinity.

    With `numbers` false any number is refused (the mapping has none); otherwise integers of at
    most 20 digits are read and every float is refused. Every failure is a `ValueError`.
    """
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("byte order mark")
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_int=_bounded_int if numbers else _refuse,
            parse_float=_refuse,
            parse_constant=_refuse,
        )
    except (RecursionError, MemoryError) as exc:
        raise ValueError("document too deep or too large") from exc


# ---- the mapping ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedMapping:
    consumer_id: str
    consumer_snapshot: str
    provider_id: str
    provider_snapshot: str
    lock_sha256: str
    environment: dict[str, str]
    coordinate: str
    package_name: str
    package_version: str
    package_snapshot_sha256: str
    source_prefix: str
    digest: str


def _closed(value: Any, keys: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("not a closed object")
    return value


def _text(value: Any, pattern: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError("malformed text")
    return value


def _hash(value: Any) -> str:
    if not ev.hash_value(value):
        raise ValueError("malformed digest")
    return str(value)


def parse_mapping(raw: bytes, max_bytes: int) -> ParsedMapping:
    """The checked mapping; every malformed shape is `invalid_mapping`."""
    if len(raw) > max_bytes:
        raise CrossRepoRejected("mapping_too_large")
    try:
        document = load_strict(raw, numbers=False)
        named = isinstance(document, dict) and isinstance(document.get("format"), str)
        if named and document["format"] != MAPPING_FORMAT:
            raise CrossRepoRejected("unsupported_mapping_version")
        return _from_document(document)
    except CrossRepoRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, ul.DependencyUsageError):
        raise CrossRepoRejected("invalid_mapping") from None


def _from_document(document: Any) -> ParsedMapping:
    top = _closed(document, ("format", "repositories", "consumer", "packages"))
    repositories = top["repositories"]
    if not isinstance(repositories, list) or len(repositories) != 2:
        raise ValueError("two repositories")
    by_role: dict[str, dict[str, Any]] = {}
    for item in repositories:
        entry = _closed(item, ("repository_id", "role", "snapshot_id"))
        _text(entry["repository_id"], _REPOSITORY_ID)
        _hash(entry["snapshot_id"])
        if entry["role"] not in ROLES or entry["role"] in by_role:
            raise ValueError("roles")
        by_role[entry["role"]] = entry
    consumer, provider = by_role["consumer"], by_role["provider"]
    block = _closed(top["consumer"], ("lock_sha256", "environment"))
    _hash(block["lock_sha256"])
    env = block["environment"]
    if not isinstance(env, dict) or ul.canonical_environment(env) != env:
        raise ValueError("environment")
    packages = top["packages"]
    if not isinstance(packages, list) or len(packages) != 1:
        raise ValueError("one package")
    package = _closed(
        packages[0],
        ("provider_repository_id", "coordinate", "package_snapshot_sha256", "source_prefix"),
    )
    _text(package["provider_repository_id"], _REPOSITORY_ID)
    found = (
        re.fullmatch(COORDINATE, package["coordinate"])
        if isinstance(package["coordinate"], str)
        else None
    )
    if found is None or ul.normalize_name(found.group(1)) != found.group(1):
        raise ValueError("coordinate")
    _hash(package["package_snapshot_sha256"])
    _text(package["source_prefix"], SOURCE_PREFIX)
    if package["provider_repository_id"] != provider["repository_id"]:
        raise CrossRepoRejected("repository_not_allowlisted")
    if consumer["repository_id"] == provider["repository_id"] or (
        consumer["snapshot_id"] == provider["snapshot_id"]
    ):
        raise CrossRepoRejected("same_repository")
    normalized = {
        "format": MAPPING_FORMAT,
        "repositories": [
            {key: consumer[key] for key in ("repository_id", "role", "snapshot_id")},
            {key: provider[key] for key in ("repository_id", "role", "snapshot_id")},
        ],
        "consumer": {"lock_sha256": block["lock_sha256"], "environment": dict(env)},
        "packages": [{key: package[key] for key in sorted(package)}],
    }
    return ParsedMapping(
        consumer_id=consumer["repository_id"],
        consumer_snapshot=consumer["snapshot_id"],
        provider_id=provider["repository_id"],
        provider_snapshot=provider["snapshot_id"],
        lock_sha256=block["lock_sha256"],
        environment=dict(env),
        coordinate=package["coordinate"],
        package_name=found.group(1),
        package_version=found.group(2),
        package_snapshot_sha256=package["package_snapshot_sha256"],
        source_prefix=package["source_prefix"],
        digest=sha(normalized, MAPPING_FORMAT),
    )


# ---- the revision vector ------------------------------------------------------------------------


def revision_vector(mapping: ParsedMapping) -> list[dict[str, str]]:
    """The exact ordered vector: the consumer entry, then the provider entry."""
    return [
        {
            "role": "consumer",
            "repository_id": mapping.consumer_id,
            "snapshot_id": mapping.consumer_snapshot,
        },
        {
            "role": "provider",
            "repository_id": mapping.provider_id,
            "snapshot_id": mapping.provider_snapshot,
        },
    ]


def vector_digest(vector: Sequence[dict[str, str]]) -> str:
    return sha(list(vector), VECTOR_FORMAT)


def vector_matches(vector: Any, mapping: ParsedMapping) -> bool:
    return bool(vector == revision_vector(mapping))


# ---- roots and distinctness ----------------------------------------------------------------


def _effective_root(side: Side) -> Any:
    return (side.graph_root if side.graph_root is not None else side.repo / ".atlas").resolve()


def check_roots(consumer: Side, provider: Side) -> None:
    """Usage error when one repository lies inside the other or a graph root inside the other repo.

    A root shared by both sides, or beside both repositories, is fine: namespaces are keyed by the
    repository path, so two sides never share a database.
    """
    try:
        first, second = consumer.repo.resolve(), provider.repo.resolve()
        roots = ((_effective_root(consumer), second), (_effective_root(provider), first))
    except (OSError, RuntimeError):
        raise CrossRepoUsageError("graph_namespace") from None
    if first == second:
        return  # one repository twice: `same_repository` is the answer, not a usage error
    if first.is_relative_to(second) or second.is_relative_to(first):
        raise CrossRepoUsageError("repository_overlap")
    if any(root.is_relative_to(other) for root, other in roots):
        raise CrossRepoUsageError("repository_overlap")


def check_distinct(consumer: Side, provider: Side) -> None:
    """Before any open: the two resolved repository paths must differ."""
    try:
        same = consumer.repo.resolve() == provider.repo.resolve()
    except (OSError, RuntimeError):
        raise CrossRepoUsageError("graph_namespace") from None
    if same:
        raise CrossRepoRejected("same_repository")


def identity_key(reader: Any) -> tuple[str, str]:
    """The repository identity of a leased snapshot: git common dir, else the root."""
    identity = reader._source_lease.manifest["context"]["identity"]
    if identity["kind"] == "git":
        return ("git", str(identity["common_dir"]))
    return (str(identity["kind"]), str(identity["root"]))


def check_distinct_leases(consumer_reader: Any, provider_reader: Any) -> None:
    """After the leases: repositories sharing one git common dir are one repository."""
    if identity_key(consumer_reader) == identity_key(provider_reader):
        raise CrossRepoRejected("same_repository")


# ---- the leases ----------------------------------------------------------------------------


def _current_epoch(reader: Any) -> bool:
    return bool(reader._lease_schema_epoch == max(runtime.SCHEMA_EPOCHS))


def open_consumer(stack: ExitStack, side: Side, snapshot_id: str) -> Any:
    """Lease the consumer's active snapshot; it must be the mapped one."""
    try:
        reader = runtime.open_active(stack, side)
    except runtime.OverlayRejected as exc:
        reason = exc.reason if exc.reason in LEASE_REASONS else "index_unavailable"
        raise CrossRepoRejected("consumer_" + reason) from None
    except DiffUsageError:
        raise CrossRepoUsageError("graph_namespace") from None
    if not _current_epoch(reader):
        raise CrossRepoRejected("consumer_index_upgrade_required")
    if reader.snapshot_id != snapshot_id:
        raise CrossRepoRejected("consumer_snapshot_mismatch")
    return reader


def provider_block(
    repository_id: str,
    declared: str,
    supplied: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    matched = detail is None and supplied == declared
    return {
        "repository_id": repository_id,
        "declared_snapshot_id": declared,
        "supplied_snapshot_id": supplied,
        "matched": matched,
        "state": "available" if matched else "unavailable",
        "detail": detail,
    }


def open_provider(
    stack: ExitStack, side: Side, repository_id: str, declared: str
) -> tuple[Any | None, dict[str, Any]]:
    """Lease the provider; the reader is returned only when its active snapshot is the mapped one.

    An unavailable provider is a state, never a rejection, and no SQL is issued on it.
    """
    try:
        reader = runtime.open_active(stack, side)
    except runtime.OverlayRejected as exc:
        reason = exc.reason if exc.reason in LEASE_REASONS else "index_unavailable"
        return None, provider_block(repository_id, declared, None, reason)
    except DiffUsageError:
        raise CrossRepoUsageError("graph_namespace") from None
    if not _current_epoch(reader):
        return None, provider_block(repository_id, declared, None, "index_upgrade_required")
    if reader.snapshot_id != declared:
        return None, provider_block(
            repository_id, declared, reader.snapshot_id, "snapshot_not_active"
        )
    return reader, provider_block(repository_id, declared, declared, None)


# ---- refresh ----------------------------------------------------------------------------------


def supersedes(
    overlay_id: str,
    previous_vector_digest: str,
    old_ids: Sequence[str],
    new_ids: Sequence[str],
    cap: int,
) -> dict[str, Any]:
    """What a refresh invalidated: the previous links that the fresh build does not contain."""
    gone = sorted(set(old_ids) - set(new_ids))
    return {
        "overlay_id": overlay_id,
        "vector_digest": previous_vector_digest,
        "links_invalidated": len(gone),
        "invalidated_link_ids": gone[:cap],
        "invalidated_link_ids_truncated": len(gone) > cap,
    }
