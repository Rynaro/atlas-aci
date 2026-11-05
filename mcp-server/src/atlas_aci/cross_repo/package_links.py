"""Link one Python consumer and one provider by an exact package revision vector (ACI-S30).

The result is the standalone, content-addressed `atlas-cross-repo-overlay.v1`: it is built from
READ-ONLY leases of the two active snapshots, one S28 package snapshot and one explicit mapping,
and it is never part of a graph. A link is an import reference joined to one provider declaration,
under the exact ordered (consumer, provider) revision vector; it is not a call.

Pure given the leases: nothing here opens a file, writes anywhere, imports or runs what it reads.
Regular expressions use explicit ASCII classes and `re.fullmatch` only; every traversal is
iterative.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from atlas_aci.cross_repo import revision_vector as rv
from atlas_aci.cross_repo.revision_vector import CrossRepoRejected, CrossRepoUsageError
from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.dependencies import uv_lock as ul
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-cross-repo-overlay.v1"
IMPACT_FORMAT = "atlas-cross-repo-impact.v1"
SUMMARY_FORMAT = "atlas-cross-repo-link-summary.v1"
PRODUCER = {"name": "atlas-aci cross-repo-link", "version": "atlas-cross-repo-linker.v1"}
REFERENCE_TAG = "atlas-cross-repo-reference.v1"
LINK_TAG = "atlas-cross-repo-link.v1"

RESOLUTIONS = (
    "linked_declaration",
    "unresolved_version_mismatch",
    "unresolved_provider_snapshot_unavailable",
    "unresolved_unsupported_reference_form",
    "unresolved_local_module_shadow",
    "unresolved_reexport_not_followed",
    "unresolved_symbol_not_declared",
    "unresolved_ambiguous_provider_symbol",
    "unresolved_provider_declaration_not_indexed",
)
LOCK_MISMATCH_DETAILS = (
    "coordinate_differs",
    "artifact_digest_differs",
    "lock_digest_differs",
    "environment_differs",
)
MISMATCH_DETAILS = (*LOCK_MISMATCH_DETAILS, "source_member_missing", "source_digest_differs")
PROVIDER_DETAILS = rv.PROVIDER_DETAILS
FORM_DETAILS = (
    "module_import",
    "wildcard_import",
    "nested_scope_import",
    "submodule_import",
    "non_ascii_identifier",
    "non_identifier_name",
)
SYMBOL_DETAILS = ("module_not_in_package", "name_not_declared", "module_incomplete")
AMBIGUOUS_DETAILS = ("package_declarations", "provider_symbols", "provider_name_rebound")
RESOLUTION_DETAILS: dict[str, tuple[str, ...]] = {
    "linked_declaration": (),
    "unresolved_version_mismatch": MISMATCH_DETAILS,
    "unresolved_provider_snapshot_unavailable": PROVIDER_DETAILS,
    "unresolved_unsupported_reference_form": FORM_DETAILS,
    "unresolved_local_module_shadow": (),
    "unresolved_reexport_not_followed": (),
    "unresolved_symbol_not_declared": SYMBOL_DETAILS,
    "unresolved_ambiguous_provider_symbol": AMBIGUOUS_DETAILS,
    "unresolved_provider_declaration_not_indexed": (),
}
REJECTIONS = rv.REJECTIONS
LOOKUP_RESOLUTIONS = (
    "linked_declaration",
    "unresolved_reexport_not_followed",
    "unresolved_symbol_not_declared",
    "unresolved_ambiguous_provider_symbol",
    "unresolved_provider_declaration_not_indexed",
)
FORMS = ("from_import", "module_import", "wildcard_import")
IMPORT_REASONS = (
    "unsupported_absolute_import",
    "unsupported_import_alias",
    "unsupported_import_star",
)
BINDING_STATES = ("bound", "version_mismatch", "provider_snapshot_unavailable")
CHECK_VALUES = {
    "coordinate": ("equal", "differs"),
    "artifact": ("equals_lock_entry", "differs"),
    "lock": ("equal", "differs"),
    "environment": ("equal", "differs"),
    "sources": ("equal", "member_missing", "digest_differs", "not_evaluated"),
}
BOUNDARY_KINDS = ("missing_provider_snapshot", "unresolved_external_reference", "budget_exhausted")
DIRECTIONS = ("from_provider", "to_provider")
ATTESTATION = "operator_declared"
RELATION = "external_reference"
SEMANTICS = "not_claimed"
STATUS = "potential_impact"
BASIS = "static_reference"
RUNTIME_OBSERVATION = "not_performed"
MAX_INVALIDATED_IDS = 10000
TEXT_CAP = 64
NAME_MAX = 1024

LIMITS: dict[str, tuple[int, int, int]] = {
    "max_mapping_bytes": (65536, 1, 1048576),
    "max_lock_bytes": (33554432, 1, 268435456),
    "max_package_snapshot_bytes": (67108864, 1, 268435456),
    "max_overlay_bytes": (67108864, 1, 268435456),
    "max_references": (100000, 1, 1000000),
}
BUDGETS: dict[str, tuple[int, int, int]] = {
    "max_results": (1000, 1, 100000),
    "max_links_examined": (100000, 1, 1000000),
    "max_boundaries": (1000, 1, 100000),
}
LIMITATIONS = (
    "import_reference_only_no_call_binding",
    "module_level_from_imports_only",
    "conditional_absolute_imports_not_distinguished",
    "reexports_not_followed",
    "lock_bound_to_consumer_by_operator_declaration",
    "every_wheel_python_member_must_match_provider_source",
    "provider_snapshot_must_be_the_active_generation",
    "one_consumer_one_provider_one_package",
    "impact_depth_one_without_in_repository_expansion",
    "repository_ids_are_operator_labels",
    "local_shadow_detected_by_observed_paths_only",
    "not_written_to_the_graph_and_not_exposed_over_mcp",
    "decorated_definitions_link_the_undecorated_declaration",
    "repositories_sharing_one_git_common_dir_are_not_distinct",
)
COVERAGE = {
    "evidence_coverage": "import_reference_and_declaration_only",
    "completeness": "incomplete",
    "call_binding": "not_claimed",
    "execution": "not_performed",
    "traversal": "mapped_repositories_only",
}

TOP_KEYS = (
    "format", "overlay_id", "producer", "mapping", "revision_vector", "vector_digest",
    "consumer", "provider", "package", "coverage", "counts", "limitations", "references",
    "links", "supersedes",
)  # fmt: skip
SUPERSEDES_KEYS = (
    "overlay_id", "vector_digest", "links_invalidated", "invalidated_link_ids",
    "invalidated_link_ids_truncated",
)  # fmt: skip
REFERENCE_KEYS = (
    "id", "type", "consumer", "form", "module", "name", "qualified_name", "aliased",
    "resolution", "detail", "link_id",
)  # fmt: skip
REFERENCE_CONSUMER_KEYS = (
    "repository_id", "snapshot_id", "path", "line", "column", "byte_start", "owner_entity_id",
    "occurrence_id", "evidence_id", "assertion_id",
)  # fmt: skip
LINK_KEYS = (
    "id", "type", "relation", "call_semantics", "reference_id", "qualified_name",
    "revision_vector", "vector_digest", "consumer", "provider",
)  # fmt: skip
LINK_CONSUMER_KEYS = (
    "repository_id", "snapshot_id", "path", "line", "owner_entity_id", "assertion_id",
)  # fmt: skip
LINK_PROVIDER_KEYS = (
    "repository_id", "snapshot_id", "entity_id", "path", "name", "kind", "line_start",
    "line_end", "declaration_id", "member", "member_sha256", "decorated",
)  # fmt: skip
BINDING_KEYS = (
    "state", "detail", "checks", "members_compared", "members_equal", "members_missing",
    "members_differing",
)  # fmt: skip
IMPACT_KEYS = (
    "format", "result_id", "overlay_id", "vector_digest", "revision_vector", "direction", "seed",
    "status", "basis", "runtime_observation", "call_semantics", "depth_limit", "provider_state",
    "budgets", "work", "complete", "results", "boundaries",
)  # fmt: skip
RESULT_KEYS = (
    "link_id", "reference_id", "relation", "call_semantics", "depth", "consumer", "provider",
)  # fmt: skip
BOUNDARY_KEYS = (
    "kind", "detail", "at", "reference_id", "link_id", "repository_id", "snapshot_id",
)  # fmt: skip
SUMMARY_KEYS = (
    "format", "overlay_id", "vector_digest", "revision_vector", "binding_state", "provider_state",
    "counts", "links_invalidated",
)  # fmt: skip

KIND_MAP = {"class": "class", "function": "function", "async_function": "function"}

OBSERVATIONS_SQL = "SELECT value FROM manifest WHERE key='source_observations'"
IMPORT_SQL = (
    "SELECT path, local_name, scope, byte_start, description, source_entity_id, occurrence_id, "
    "evidence_id, assertion_id FROM import_bindings WHERE lang='python' "
    "ORDER BY path, byte_start, local_name, occurrence_id"
)
OCCURRENCE_SQL = "SELECT payload FROM occurrences WHERE id=?"
ENTITY_SQL = "SELECT 1 FROM entities WHERE id=?"
SYMBOL_SQL = (
    "SELECT kind, line_start, line_end, binding, entity_id FROM symbols "
    "WHERE path=? AND name=? AND lang='python' AND kind NOT LIKE 'scip\\_%' ESCAPE '\\' "
    "ORDER BY line_start, kind, entity_id"
)
ASSIGNMENT_SQL = (
    "SELECT 1 FROM assignment_targets WHERE path=? AND scope=? AND name IN (?, '*') LIMIT 1"
)
REBINDING_IMPORT_SQL = (
    "SELECT 1 FROM import_bindings WHERE path=? AND scope=? AND local_name IN (?, '*') LIMIT 1"
)

_ASCII_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_IDENTIFIER_SHAPE = r"([A-Za-z_]|[^\x00-\x7f])([A-Za-z0-9_]|[^\x00-\x7f])*"
_COORDINATE = rv.COORDINATE
_PREFIX = rv.SOURCE_PREFIX
_REPOSITORY_ID = r"[a-z0-9][a-z0-9._-]{0,63}"


def canonical_bytes(document: Any) -> bytes:
    """The exact bytes written and printed: canonical JSON plus one newline."""
    return ev.canonical(document).encode("utf-8") + b"\n"


def _sha(parts: Any) -> str:
    return hashlib.sha256(ev.canonical(parts).encode("utf-8")).hexdigest()


# ---- limits and budgets ------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkLimits:
    max_mapping_bytes: int
    max_lock_bytes: int
    max_package_snapshot_bytes: int
    max_overlay_bytes: int
    max_references: int


@dataclass(frozen=True)
class Budgets:
    max_results: int
    max_links_examined: int
    max_boundaries: int


def _normalize(
    table: Mapping[str, tuple[int, int, int]], chosen: Mapping[str, int | None] | None
) -> dict[str, int]:
    chosen = {} if chosen is None else chosen
    unknown = sorted(set(chosen) - set(table))
    if unknown:
        raise CrossRepoUsageError(unknown[0])
    values: dict[str, int] = {}
    for name, (default, low, high) in table.items():
        value = chosen.get(name)
        if value is None:
            values[name] = default
        elif type(value) is not int or not low <= value <= high:
            raise CrossRepoUsageError(name)
        else:
            values[name] = value
    return values


def normalize_limits(chosen: Mapping[str, int | None] | None = None) -> LinkLimits:
    """Defaults for absent values; a bool, a non-int or an out-of-range value is a usage error."""
    return LinkLimits(**_normalize(LIMITS, chosen))


def normalize_budgets(chosen: Mapping[str, int | None] | None = None) -> Budgets:
    return Budgets(**_normalize(BUDGETS, chosen))


# ---- identities --------------------------------------------------------------------------------


def overlay_identity(overlay: Mapping[str, Any]) -> str:
    """The overlay id of `overlay` as it stands (its own `overlay_id` is not part of the input)."""
    rest = {key: value for key, value in overlay.items() if key != "overlay_id"}
    return hashlib.sha256((FORMAT + ":" + ev.canonical(rest)).encode("utf-8")).hexdigest()


def content_identity(overlay: Mapping[str, Any]) -> str:
    """The overlay id with `supersedes` forced null: what a refresh compares."""
    return overlay_identity({**overlay, "supersedes": None})


def reference_id(repository_id: str, snapshot_id: str, occurrence_id: str) -> str:
    return _sha([REFERENCE_TAG, repository_id, snapshot_id, occurrence_id])


def link_id(vector_digest: str, reference: str, entity_id: str, declaration_id: str) -> str:
    return _sha([LINK_TAG, vector_digest, reference, entity_id, declaration_id])


# ---- the package snapshot --------------------------------------------------------------------


@dataclass(frozen=True)
class PackageIndex:
    top_modules: tuple[str, ...]
    modules: dict[str, bool]
    declarations: dict[str, list[dict[str, Any]]]
    from_imports: frozenset[tuple[str, str]]
    sources: tuple[dict[str, Any], ...]
    members: dict[str, dict[str, Any]]


def index_snapshot(snapshot: dict[str, Any]) -> PackageIndex:
    modules: dict[str, bool] = {}
    for item in snapshot["modules"]:
        if item["module"] is not None:
            modules[item["module"]] = item["incomplete"]
    declarations: dict[str, list[dict[str, Any]]] = {}
    for item in snapshot["declarations"]:
        if item["parent_id"] is None and item["kind"] in KIND_MAP:
            declarations.setdefault(item["symbol"], []).append(item)
    imports = frozenset(
        (item["module"], item["alias"] or item["name"])
        for item in snapshot["imports"]
        if item["statement"] == "from"
    )
    members = {item["path"]: item for item in snapshot["members"]}
    sources = tuple(item for item in snapshot["members"] if item["role"] == "python_source")
    return PackageIndex(
        tuple(sorted({name.split(".")[0] for name in modules})),
        modules,
        declarations,
        imports,
        sources,
        members,
    )


def _load_package(raw: bytes, expected_sha256: str, limit: int) -> dict[str, Any]:
    if len(raw) > limit:
        raise CrossRepoRejected("package_snapshot_too_large")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise CrossRepoRejected("package_snapshot_digest_mismatch")
    try:
        snapshot = rv.load_strict(raw, numbers=True)
        pw.validate_snapshot(snapshot)
        if pw.canonical_bytes(snapshot) != raw:
            raise ValueError("not canonical")
    except (ul.DependencyRejected, KeyError, TypeError, ValueError, AttributeError, IndexError):
        raise CrossRepoRejected("invalid_package_snapshot") from None
    except (RecursionError, MemoryError):
        raise CrossRepoRejected("invalid_package_snapshot") from None
    return dict(snapshot)


def _select(lock_raw: bytes, mapping: rv.ParsedMapping, limits: LinkLimits) -> ul.Selection:
    if len(lock_raw) > limits.max_lock_bytes:
        raise CrossRepoRejected("lock_too_large")
    if hashlib.sha256(lock_raw).hexdigest() != mapping.lock_sha256:
        raise CrossRepoRejected("lock_digest_mismatch")
    try:
        chosen = ul.normalize_limits({"max_lock_bytes": limits.max_lock_bytes})
        return ul.select_package(lock_raw, mapping.package_name, mapping.environment, chosen)
    except ul.DependencyRejected as exc:
        raise CrossRepoRejected(
            exc.reason if exc.reason in rv.LOCK_REASONS else "lock_invalid"
        ) from None
    except ul.DependencyUsageError:
        raise CrossRepoRejected("invalid_mapping") from None


# ---- evidence of the leased snapshots ----------------------------------------------------------


def _observations(reader: Any, reason: str) -> dict[str, dict[str, Any]]:
    """The `observation` records of the leased snapshot by path (every state)."""
    try:
        row = reader.db.execute(OBSERVATIONS_SQL).fetchone()
        if row is None:
            raise ValueError("no observations")
        found: dict[str, dict[str, Any]] = {}
        for item in json.loads(row[0]):
            if item.get("type") == "observation":
                found[item["path"]] = item
        return found
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        raise CrossRepoRejected(reason) from None


@dataclass(frozen=True)
class Reference:
    path: str
    line: int
    column: int
    byte_start: int
    owner_entity_id: str
    occurrence_id: str
    evidence_id: str
    assertion_id: str
    scope: str
    level: int
    module: tuple[str, ...]
    name: str
    local_name: str
    reason: str | None


def _evidence_error() -> CrossRepoRejected:
    return CrossRepoRejected("invalid_consumer_evidence")


def _natural(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _description(text: Any) -> tuple[int, tuple[str, ...], str, str, str | None]:
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {
        "level",
        "module",
        "name",
        "local_name",
        "reason",
    }:
        raise ValueError("description")
    module, name, local = data["module"], data["name"], data["local_name"]
    ok = (
        _natural(data["level"])
        and isinstance(module, list)
        and all(isinstance(part, str) and len(part) <= NAME_MAX for part in module)
    )
    ok = ok and isinstance(name, str) and name != "" and len(name) <= NAME_MAX
    ok = (
        ok
        and isinstance(local, str)
        and (data["reason"] is None or isinstance(data["reason"], str))
    )
    if not ok:
        raise ValueError("description")
    return data["level"], tuple(module), name, local, data["reason"]


def _root_of(module: Sequence[str], name: str) -> str:
    return module[0] if module else name.split(".")[0]


def consumer_references(
    reader: Any, top_modules: Sequence[str], max_references: int
) -> tuple[list[Reference], int]:
    """The supported-candidate import references of the consumer and the out-of-scope count."""
    wanted = set(top_modules)
    pending: list[tuple[Any, ...]] = []
    out_of_scope = 0
    try:
        for row in reader.db.execute(IMPORT_SQL):
            path, _local, scope, _start, text, owner, occurrence, evidence, assertion = tuple(row)
            level, module, name, local, reason = _description(text)
            ids = (owner, occurrence, evidence, assertion)
            if not isinstance(path, str) or not isinstance(scope, str):
                raise ValueError("row")
            if not all(ev.hash_value(item) for item in ids):
                raise ValueError("row")
            if level != 0 or _root_of(module, name) not in wanted:
                out_of_scope += 1
                continue
            if reason not in IMPORT_REASONS:
                raise ValueError("reason")
            pending.append((path, scope, level, module, name, local, reason, *ids))
            if len(pending) > max_references:
                raise CrossRepoRejected("reference_limit_exceeded")
        found: list[Reference] = []
        for path, scope, level, module, name, local, reason, owner, occ, evidence, asr in pending:
            row = reader.db.execute(OCCURRENCE_SQL, (occ,)).fetchone()
            if row is None:
                raise ValueError("no occurrence")
            payload = json.loads(row[0])
            if payload["path"] != path or not ev.relative_path(path):
                raise ValueError("occurrence path")
            line, column, start = payload["line"], payload["column"], payload["start_byte"]
            if not (_natural(line, 1) and _natural(column) and _natural(start)):
                raise ValueError("occurrence span")
            found.append(
                Reference(
                    path,
                    line,
                    column,
                    start,
                    owner,
                    occ,
                    evidence,
                    asr,
                    scope,
                    level,
                    module,
                    name,
                    local,
                    reason,
                )
            )
    except CrossRepoRejected:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        raise _evidence_error() from None
    found.sort(key=lambda item: (item.path, item.byte_start, item.occurrence_id))
    if len({item.occurrence_id for item in found}) != len(found):
        raise _evidence_error()
    return found, out_of_scope


@dataclass(frozen=True)
class SymbolRow:
    kind: str
    line_start: int
    line_end: int
    entity_id: str
    unconditional: bool


@dataclass(frozen=True)
class ProviderFacts:
    """The provider's root-scope symbol rows at one (path, name) and whether the name is rebound."""

    rows: tuple[SymbolRow, ...]
    rebound: bool


def provider_facts(reader: Any, path: str, name: str) -> ProviderFacts:
    scope = "python:" + path + ":root"
    try:
        rows = reader.db.execute(SYMBOL_SQL, (path, name)).fetchall()
        rebound = (
            reader.db.execute(ASSIGNMENT_SQL, (path, scope, name)).fetchone() is not None
            or reader.db.execute(REBINDING_IMPORT_SQL, (path, scope, name)).fetchone() is not None
        )
        found: list[SymbolRow] = []
        for kind, start, end, binding, entity in (tuple(row) for row in rows):
            info = json.loads(binding)
            if not isinstance(info, dict):
                raise ValueError("binding")
            if info.get("scope") != scope:
                continue
            if not (_natural(start, 1) and _natural(end, start) and ev.hash_value(entity)):
                raise ValueError("symbol row")
            # The one place the indexer's unconditional-definition flag is read; never emitted.
            unconditional = info.get("import_export_safe") is True
            found.append(SymbolRow(str(kind), start, end, entity, unconditional))
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        raise CrossRepoRejected("invalid_provider_evidence") from None
    return ProviderFacts(tuple(found), rebound)


# ---- the bind (D4) ----------------------------------------------------------------------------


def derive_state(
    checks: Mapping[str, str], provider_state: str, provider_detail: str | None
) -> tuple[str, str | None]:
    """The binding state and detail from the five checks and the provider state."""
    for check, detail in (
        ("coordinate", "coordinate_differs"),
        ("artifact", "artifact_digest_differs"),
        ("lock", "lock_digest_differs"),
        ("environment", "environment_differs"),
    ):
        if checks[check] == "differs":
            return "version_mismatch", detail
    if provider_state != "available":
        return "provider_snapshot_unavailable", provider_detail
    if checks["sources"] == "member_missing":
        return "version_mismatch", "source_member_missing"
    if checks["sources"] == "digest_differs":
        return "version_mismatch", "source_digest_differs"
    return "bound", None


def bind(
    selection: ul.Selection,
    snapshot: dict[str, Any],
    mapping: rv.ParsedMapping,
    provider: Mapping[str, Any],
    index: PackageIndex,
    observations: Mapping[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Compare the mapping, the lock, the package snapshot and the provider's indexed sources."""
    locked = selection.coordinate_text
    package = snapshot["package"]
    artifacts = {item.sha256 for item in selection.artifacts}
    checks = {
        "coordinate": "equal"
        if mapping.coordinate == locked and package["coordinate"] == locked
        else "differs",
        "artifact": "equals_lock_entry"
        if package["artifact"]["sha256"] in artifacts
        else "differs",
        "lock": "equal" if snapshot["lock"]["sha256"] == mapping.lock_sha256 else "differs",
        "environment": "equal" if snapshot["environment"] == mapping.environment else "differs",
        "sources": "not_evaluated",
    }
    equal = missing = differing = 0
    if observations is not None:
        for member in index.sources:
            seen = observations.get(mapping.source_prefix + member["path"])
            if seen is None or seen.get("state") != "indexed":
                missing += 1
            elif seen.get("observed_source_sha256") == member["sha256"]:
                equal += 1
            else:
                differing += 1
        checks["sources"] = (
            "member_missing" if missing else "digest_differs" if differing else "equal"
        )
    state, detail = derive_state(checks, provider["state"], provider["detail"])
    return {
        "state": state,
        "detail": detail,
        "checks": checks,
        "members_compared": equal + missing + differing,
        "members_equal": equal,
        "members_missing": missing,
        "members_differing": differing,
    }


# ---- resolution (D7) ---------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    resolution: str
    detail: str | None = None
    declaration: dict[str, Any] | None = None
    row: SymbolRow | None = None
    path: str | None = None


def classify_form(ref: Reference) -> tuple[str, str | None]:
    """The reference form and, when it is not a supported one, the closed detail."""
    if not ref.module:
        form = "module_import"
    elif ref.name == "*":
        form = "wildcard_import"
    else:
        form = "from_import"
    if ref.scope != "python:" + ref.path + ":root":
        return form, "nested_scope_import"
    if form != "from_import":
        return form, form
    for part in (*ref.module, ref.name):
        if re.fullmatch(_ASCII_IDENTIFIER, part) is None:
            shaped = re.fullmatch(_IDENTIFIER_SHAPE, part) is not None
            return form, "non_ascii_identifier" if shaped else "non_identifier_name"
    return form, None


def resolve_reference(
    ref: Reference,
    index: PackageIndex,
    binding: Mapping[str, Any],
    source_prefix: str,
    shadowed: bool,
    lookup: Callable[[str, str], ProviderFacts] | None,
) -> Resolution:
    """The first matching outcome, in the fixed precedence. Pure given `lookup`."""
    _, form_detail = classify_form(ref)
    if form_detail is not None:
        return Resolution("unresolved_unsupported_reference_form", form_detail)
    if shadowed:
        return Resolution("unresolved_local_module_shadow")
    if binding["state"] == "version_mismatch":
        return Resolution("unresolved_version_mismatch", binding["detail"])
    if binding["state"] == "provider_snapshot_unavailable":
        return Resolution("unresolved_provider_snapshot_unavailable", binding["detail"])
    module = ".".join(ref.module)
    symbol = module + "." + ref.name
    found = index.declarations.get(symbol, [])
    is_module = symbol in index.modules
    if len(found) >= 2 or (found and is_module):
        return Resolution("unresolved_ambiguous_provider_symbol", "package_declarations")
    if not found:
        if is_module:
            return Resolution("unresolved_unsupported_reference_form", "submodule_import")
        if module not in index.modules:
            return Resolution("unresolved_symbol_not_declared", "module_not_in_package")
        if (module, ref.name) in index.from_imports:
            return Resolution("unresolved_reexport_not_followed")
        if index.modules[module]:
            return Resolution("unresolved_symbol_not_declared", "module_incomplete")
        return Resolution("unresolved_symbol_not_declared", "name_not_declared")
    declaration = found[0]
    if lookup is None:
        raise CrossRepoRejected("invalid_provider_evidence")
    path = source_prefix + declaration["member"]
    facts = lookup(path, declaration["name"])
    if not facts.rows:
        return Resolution("unresolved_provider_declaration_not_indexed")
    if len(facts.rows) >= 2 or not all(item.unconditional for item in facts.rows):
        return Resolution("unresolved_ambiguous_provider_symbol", "provider_symbols")
    row = facts.rows[0]
    if KIND_MAP[declaration["kind"]] != row.kind:
        return Resolution("unresolved_provider_declaration_not_indexed")
    if facts.rebound:
        return Resolution("unresolved_ambiguous_provider_symbol", "provider_name_rebound")
    return Resolution("linked_declaration", None, declaration, row, path)


def shadow_roots(observations: Mapping[str, Any], roots: Sequence[str]) -> set[str]:
    """Roots that a consumer file (observed in any state) could shadow as a local module."""
    shadowed: set[str] = set()
    for root in roots:
        for suffix in (root + ".py", root + "/__init__.py"):
            if any(path == suffix or path.endswith("/" + suffix) for path in observations):
                shadowed.add(root)
    return shadowed


# ---- records ---------------------------------------------------------------------------------


def _display(text: str) -> str:
    """A recorded name: whole for an ASCII identifier, otherwise capped and encodable."""
    if re.fullmatch(_ASCII_IDENTIFIER, text) is not None:
        return text
    text = text[:TEXT_CAP] if len(text) > TEXT_CAP else text
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "backslashreplace").decode("ascii")
    return text


def _qualified(ref: Reference, form: str) -> str | None:
    parts = (*ref.module, ref.name)
    if form != "from_import" or any(re.fullmatch(_ASCII_IDENTIFIER, p) is None for p in parts):
        return None
    return ".".join(parts)


def _reference_record(
    ref: Reference, repository_id: str, snapshot_id: str, outcome: Resolution, link: str | None
) -> dict[str, Any]:
    form, _ = classify_form(ref)
    return {
        "id": reference_id(repository_id, snapshot_id, ref.occurrence_id),
        "type": "external_reference",
        "consumer": {
            "repository_id": repository_id,
            "snapshot_id": snapshot_id,
            "path": ref.path,
            "line": ref.line,
            "column": ref.column,
            "byte_start": ref.byte_start,
            "owner_entity_id": ref.owner_entity_id,
            "occurrence_id": ref.occurrence_id,
            "evidence_id": ref.evidence_id,
            "assertion_id": ref.assertion_id,
        },
        "form": form,
        "module": [_display(part) for part in ref.module],
        "name": _display(ref.name),
        "qualified_name": _qualified(ref, form),
        "aliased": form == "from_import" and ref.local_name != ref.name,
        "resolution": outcome.resolution,
        "detail": outcome.detail,
        "link_id": link,
    }


def _link_record(
    record: dict[str, Any],
    outcome: Resolution,
    vector: list[dict[str, str]],
    digest: str,
    provider_id: str,
    provider_snapshot: str,
    index: PackageIndex,
) -> dict[str, Any]:
    declaration, row = outcome.declaration, outcome.row
    assert declaration is not None and row is not None and outcome.path is not None
    consumer = record["consumer"]
    return {
        "id": link_id(digest, record["id"], row.entity_id, declaration["id"]),
        "type": "cross_repository_link",
        "relation": RELATION,
        "call_semantics": SEMANTICS,
        "reference_id": record["id"],
        "qualified_name": record["qualified_name"],
        "revision_vector": [dict(entry) for entry in vector],
        "vector_digest": digest,
        "consumer": {key: consumer[key] for key in LINK_CONSUMER_KEYS},
        "provider": {
            "repository_id": provider_id,
            "snapshot_id": provider_snapshot,
            "entity_id": row.entity_id,
            "path": outcome.path,
            "name": declaration["name"],
            "kind": row.kind,
            "line_start": row.line_start,
            "line_end": row.line_end,
            "declaration_id": declaration["id"],
            "member": declaration["member"],
            "member_sha256": index.members[declaration["member"]]["sha256"],
            "decorated": declaration["decorated"],
        },
    }


def _counts(
    references: list[dict[str, Any]], links: list[Any], out_of_scope: int
) -> dict[str, Any]:
    by_resolution = {name: 0 for name in RESOLUTIONS}
    for item in references:
        by_resolution[item["resolution"]] += 1
    return {
        "references": len(references),
        "references_out_of_scope": out_of_scope,
        "links": len(links),
        "by_resolution": by_resolution,
    }


def _seal(body: dict[str, Any]) -> dict[str, Any]:
    body["overlay_id"] = overlay_identity(body)
    return body


def _build(
    mapping: rv.ParsedMapping,
    selection: ul.Selection,
    snapshot: dict[str, Any],
    consumer_reader: Any,
    provider_reader: Any | None,
    provider: dict[str, Any],
    limits: LinkLimits,
) -> dict[str, Any]:
    vector = rv.revision_vector(mapping)
    digest = rv.vector_digest(vector)
    index = index_snapshot(snapshot)
    consumer_files = _observations(consumer_reader, "invalid_consumer_evidence")
    provider_files = (
        None
        if provider_reader is None
        else _observations(provider_reader, "invalid_provider_evidence")
    )
    binding = bind(selection, snapshot, mapping, provider, index, provider_files)
    found, out_of_scope = consumer_references(
        consumer_reader, index.top_modules, limits.max_references
    )
    shadowed = shadow_roots(consumer_files, sorted({_root_of(r.module, r.name) for r in found}))
    cache: dict[tuple[str, str], ProviderFacts] = {}

    def lookup(path: str, name: str) -> ProviderFacts:
        if (path, name) not in cache:
            assert provider_reader is not None
            cache[(path, name)] = provider_facts(provider_reader, path, name)
        return cache[(path, name)]

    references: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for ref in found:
        outcome = resolve_reference(
            ref,
            index,
            binding,
            mapping.source_prefix,
            _root_of(ref.module, ref.name) in shadowed,
            lookup if provider_reader is not None else None,
        )
        record = _reference_record(
            ref, mapping.consumer_id, mapping.consumer_snapshot, outcome, None
        )
        if outcome.resolution == "linked_declaration":
            link = _link_record(
                record,
                outcome,
                vector,
                digest,
                mapping.provider_id,
                mapping.provider_snapshot,
                index,
            )
            record["link_id"] = link["id"]
            links.append(link)
        references.append(record)
    links.sort(key=lambda item: item["id"])
    package = snapshot["package"]
    return {
        "format": FORMAT,
        "overlay_id": "",
        "producer": dict(PRODUCER),
        "mapping": {"format": rv.MAPPING_FORMAT, "digest": mapping.digest},
        "revision_vector": vector,
        "vector_digest": digest,
        "consumer": {
            "repository_id": mapping.consumer_id,
            "snapshot_id": mapping.consumer_snapshot,
            "lock": {"sha256": mapping.lock_sha256, "attestation": ATTESTATION},
        },
        "provider": dict(provider),
        "package": {
            "coordinate": selection.coordinate_text,
            "mapping_coordinate": mapping.coordinate,
            "package_snapshot_id": snapshot["snapshot_id"],
            "package_snapshot_sha256": mapping.package_snapshot_sha256,
            "artifact_sha256": package["artifact"]["sha256"],
            "source_prefix": mapping.source_prefix,
            "top_level_modules": list(index.top_modules),
            "binding": binding,
        },
        "coverage": dict(COVERAGE),
        "counts": _counts(references, links, out_of_scope),
        "limitations": list(LIMITATIONS),
        "references": references,
        "links": links,
        "supersedes": None,
    }


def _load_previous(raw: bytes, mapping: rv.ParsedMapping, limit: int) -> dict[str, Any]:
    if len(raw) > limit:
        raise CrossRepoRejected("previous_overlay_too_large")
    try:
        previous = rv.load_strict(raw, numbers=True)
        validate_overlay(previous)
        if canonical_bytes(previous) != raw:
            raise ValueError("not canonical")
    except (CrossRepoRejected, ValueError, TypeError, KeyError):
        raise CrossRepoRejected("invalid_previous_overlay") from None
    pairs = [(item["role"], item["repository_id"]) for item in previous["revision_vector"]]
    wanted = [(item["role"], item["repository_id"]) for item in rv.revision_vector(mapping)]
    if pairs != wanted:
        raise CrossRepoRejected("previous_overlay_unrelated")
    return dict(previous)


def link(
    mapping_raw: bytes,
    lock_raw: bytes,
    package_raw: bytes,
    consumer: Side,
    provider: Side,
    previous_raw: bytes | None = None,
    limits: LinkLimits | None = None,
) -> dict[str, Any]:
    """The cross-repository overlay of one consumer and one provider; nothing is written.

    The mapping, the lock and the package snapshot are checked first and the provider is never
    opened before them. Both leases live in one `ExitStack`, consumer first.
    """
    limits = normalize_limits() if limits is None else limits
    rv.check_roots(consumer, provider)
    mapping = rv.parse_mapping(mapping_raw, limits.max_mapping_bytes)
    rv.check_distinct(consumer, provider)
    selection = _select(lock_raw, mapping, limits)
    snapshot = _load_package(
        package_raw, mapping.package_snapshot_sha256, limits.max_package_snapshot_bytes
    )
    previous = (
        None
        if previous_raw is None
        else _load_previous(previous_raw, mapping, limits.max_overlay_bytes)
    )
    with ExitStack() as stack:
        consumer_reader = rv.open_consumer(stack, consumer, mapping.consumer_snapshot)
        provider_reader, state = rv.open_provider(
            stack, provider, mapping.provider_id, mapping.provider_snapshot
        )
        if provider_reader is not None:
            rv.check_distinct_leases(consumer_reader, provider_reader)
        overlay = _seal(
            _build(mapping, selection, snapshot, consumer_reader, provider_reader, state, limits)
        )
    if previous is not None and content_identity(previous) != overlay["overlay_id"]:
        overlay["supersedes"] = rv.supersedes(
            previous["overlay_id"],
            previous["vector_digest"],
            [item["id"] for item in previous["links"]],
            [item["id"] for item in overlay["links"]],
            MAX_INVALIDATED_IDS,
        )
        _seal(overlay)
    if len(canonical_bytes(overlay)) > limits.max_overlay_bytes:
        raise CrossRepoRejected("overlay_too_large")
    validate_overlay(overlay)
    return overlay


def summary_document(overlay: Mapping[str, Any]) -> dict[str, Any]:
    superseded = overlay["supersedes"]
    return {
        "format": SUMMARY_FORMAT,
        "overlay_id": overlay["overlay_id"],
        "vector_digest": overlay["vector_digest"],
        "revision_vector": overlay["revision_vector"],
        "binding_state": overlay["package"]["binding"]["state"],
        "provider_state": overlay["provider"]["state"],
        "counts": overlay["counts"],
        "links_invalidated": None if superseded is None else superseded["links_invalidated"],
    }


# ---- validation ----------------------------------------------------------------------------------


def _check(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid document")


def _exact(value: Any, keys: Sequence[str]) -> dict[str, Any]:
    _check(isinstance(value, dict) and set(value) == set(keys))
    return dict(value)


def _is_text(value: Any, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def validate_overlay(overlay: Any) -> None:
    """Raise `invalid_overlay` unless `overlay` is closed, ordered and self-consistent."""
    try:
        _validate_overlay(overlay)
    except CrossRepoRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError):
        raise CrossRepoRejected("invalid_overlay") from None
    except MemoryError:
        raise CrossRepoRejected("invalid_overlay") from None


def _validate_vector(vector: Any) -> list[dict[str, str]]:
    _check(isinstance(vector, list) and len(vector) == 2)
    for role, entry in zip(rv.ROLES, vector, strict=True):
        _exact(entry, ("role", "repository_id", "snapshot_id"))
        _check(entry["role"] == role and _is_text(entry["repository_id"], _REPOSITORY_ID))
        _check(ev.hash_value(entry["snapshot_id"]))
    _check(vector[0]["repository_id"] != vector[1]["repository_id"])
    _check(vector[0]["snapshot_id"] != vector[1]["snapshot_id"])
    return list(vector)


def _validate_overlay(overlay: Any) -> None:
    top = _exact(overlay, TOP_KEYS)
    _check(top["format"] == FORMAT and top["producer"] == PRODUCER)
    _check(ev.hash_value(top["overlay_id"]) and top["overlay_id"] == overlay_identity(top))
    _exact(top["mapping"], ("format", "digest"))
    _check(
        top["mapping"]["format"] == rv.MAPPING_FORMAT and ev.hash_value(top["mapping"]["digest"])
    )
    vector = _validate_vector(top["revision_vector"])
    _check(top["vector_digest"] == rv.vector_digest(vector))
    _validate_sides(top, vector)
    binding = _validate_package(top)
    _check(top["coverage"] == COVERAGE and top["limitations"] == list(LIMITATIONS))
    references, links = top["references"], top["links"]
    _check(isinstance(references, list) and isinstance(links, list))
    _validate_references(top, references, binding)
    _validate_links(top, links, references, binding)
    counts = _exact(
        top["counts"], ("references", "references_out_of_scope", "links", "by_resolution")
    )
    expected = _counts(references, links, 0)
    _check(_natural(counts["references_out_of_scope"]))
    expected["references_out_of_scope"] = counts["references_out_of_scope"]
    _check(counts == expected)
    _validate_supersedes(top)


def _validate_sides(top: dict[str, Any], vector: list[dict[str, str]]) -> None:
    consumer = _exact(top["consumer"], ("repository_id", "snapshot_id", "lock"))
    _check(consumer["repository_id"] == vector[0]["repository_id"])
    _check(consumer["snapshot_id"] == vector[0]["snapshot_id"])
    lock = _exact(consumer["lock"], ("sha256", "attestation"))
    _check(ev.hash_value(lock["sha256"]) and lock["attestation"] == ATTESTATION)
    provider = _exact(
        top["provider"],
        (
            "repository_id",
            "declared_snapshot_id",
            "supplied_snapshot_id",
            "matched",
            "state",
            "detail",
        ),
    )
    _check(provider["repository_id"] == vector[1]["repository_id"])
    _check(provider["declared_snapshot_id"] == vector[1]["snapshot_id"])
    supplied, detail = provider["supplied_snapshot_id"], provider["detail"]
    _check(type(provider["matched"]) is bool and provider["state"] in ("available", "unavailable"))
    if provider["matched"]:
        _check(provider["state"] == "available" and detail is None)
        _check(supplied == provider["declared_snapshot_id"])
    else:
        _check(provider["state"] == "unavailable" and detail in PROVIDER_DETAILS)
        if detail == "snapshot_not_active":
            _check(ev.hash_value(supplied) and supplied != provider["declared_snapshot_id"])
        else:
            _check(supplied is None)


def _validate_package(top: dict[str, Any]) -> dict[str, Any]:
    package = _exact(
        top["package"],
        (
            "coordinate", "mapping_coordinate", "package_snapshot_id", "package_snapshot_sha256",
            "artifact_sha256", "source_prefix", "top_level_modules", "binding",
        ),
    )  # fmt: skip
    _check(_is_text(package["coordinate"], _COORDINATE))
    _check(_is_text(package["mapping_coordinate"], _COORDINATE))
    for key in ("package_snapshot_id", "package_snapshot_sha256", "artifact_sha256"):
        _check(ev.hash_value(package[key]))
    _check(_is_text(package["source_prefix"], _PREFIX))
    modules = package["top_level_modules"]
    _check(isinstance(modules, list) and modules == sorted(set(modules)))
    _check(all(_is_text(item, _ASCII_IDENTIFIER) for item in modules))
    binding = _exact(package["binding"], BINDING_KEYS)
    checks = _exact(binding["checks"], tuple(CHECK_VALUES))
    for name, allowed in CHECK_VALUES.items():
        _check(checks[name] in allowed)
    counts = [binding[k] for k in BINDING_KEYS[3:]]
    _check(all(_natural(item) for item in counts))
    compared, equal, missing, differing = counts
    _check(compared == equal + missing + differing)
    provider = top["provider"]
    if provider["state"] != "available":
        _check(checks["sources"] == "not_evaluated" and compared == 0)
    else:
        _check(checks["sources"] != "not_evaluated")
        expected = "member_missing" if missing else "digest_differs" if differing else "equal"
        _check(checks["sources"] == expected)
    state, detail = derive_state(checks, provider["state"], provider["detail"])
    _check(binding["state"] == state and binding["detail"] == detail)
    _check(state in BINDING_STATES)
    return binding


def _validate_references(
    top: dict[str, Any], references: list[Any], binding: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    keys = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in references:
        record = _exact(item, REFERENCE_KEYS)
        consumer = _exact(record["consumer"], REFERENCE_CONSUMER_KEYS)
        _check(record["type"] == "external_reference")
        _check(consumer["repository_id"] == top["consumer"]["repository_id"])
        _check(consumer["snapshot_id"] == top["consumer"]["snapshot_id"])
        _check(ev.relative_path(consumer["path"]) and _natural(consumer["line"], 1))
        _check(_natural(consumer["column"]) and _natural(consumer["byte_start"]))
        for key in ("owner_entity_id", "occurrence_id", "evidence_id", "assertion_id"):
            _check(ev.hash_value(consumer[key]))
        _check(
            record["id"]
            == reference_id(
                consumer["repository_id"], consumer["snapshot_id"], consumer["occurrence_id"]
            )
        )
        _check(record["form"] in FORMS and type(record["aliased"]) is bool)
        module, name = record["module"], record["name"]
        _check(
            isinstance(module, list)
            and all(isinstance(p, str) and len(p) <= NAME_MAX for p in module)
        )
        _check(isinstance(name, str) and 0 < len(name) <= NAME_MAX)
        _check((record["form"] == "module_import") == (module == []))
        _check(record["form"] != "wildcard_import" or name == "*")
        _check(not record["aliased"] or record["form"] == "from_import")
        parts = [*module, name]
        clean = record["form"] == "from_import" and all(
            _is_text(p, _ASCII_IDENTIFIER) for p in parts
        )
        _check(record["qualified_name"] == (".".join(parts) if clean else None))
        _validate_resolution(record, binding)
        keys.append((consumer["path"], consumer["byte_start"], consumer["occurrence_id"]))
        _check(record["id"] not in by_id)
        by_id[record["id"]] = record
    _check(keys == sorted(keys) and len(set(keys)) == len(keys))
    return by_id


def _validate_resolution(record: dict[str, Any], binding: dict[str, Any]) -> None:
    resolution, detail = record["resolution"], record["detail"]
    _check(resolution in RESOLUTIONS)
    allowed = RESOLUTION_DETAILS[resolution]
    _check(detail in allowed if allowed else detail is None)
    _check((record["link_id"] is not None) == (resolution == "linked_declaration"))
    _check(record["link_id"] is None or ev.hash_value(record["link_id"]))
    state = binding["state"]
    if resolution == "unresolved_version_mismatch":
        _check(state == "version_mismatch" and detail == binding["detail"])
    elif resolution == "unresolved_provider_snapshot_unavailable":
        _check(state == "provider_snapshot_unavailable" and detail == binding["detail"])
    elif resolution in LOOKUP_RESOLUTIONS or (
        resolution == "unresolved_unsupported_reference_form" and detail == "submodule_import"
    ):
        _check(state == "bound")


def _validate_links(
    top: dict[str, Any], links: list[Any], references: list[Any], binding: dict[str, Any]
) -> None:
    by_reference = {item["id"]: item for item in references}
    seen: set[str] = set()
    for item in links:
        record = _exact(item, LINK_KEYS)
        _check(record["type"] == "cross_repository_link" and record["relation"] == RELATION)
        _check(record["call_semantics"] == SEMANTICS)
        _check(record["revision_vector"] == top["revision_vector"])
        _check(record["vector_digest"] == top["vector_digest"])
        reference = by_reference[record["reference_id"]]
        _check(
            reference["resolution"] == "linked_declaration" and reference["link_id"] == record["id"]
        )
        _check(record["qualified_name"] == reference["qualified_name"])
        consumer = _exact(record["consumer"], LINK_CONSUMER_KEYS)
        _check(consumer == {key: reference["consumer"][key] for key in LINK_CONSUMER_KEYS})
        provider = _exact(record["provider"], LINK_PROVIDER_KEYS)
        _check(provider["repository_id"] == top["provider"]["repository_id"])
        _check(provider["snapshot_id"] == top["provider"]["declared_snapshot_id"])
        for key in ("entity_id", "declaration_id", "member_sha256"):
            _check(ev.hash_value(provider[key]))
        _check(provider["kind"] in ("class", "function") and type(provider["decorated"]) is bool)
        _check(ev.relative_path(provider["path"]) and ev.relative_path(provider["member"]))
        _check(provider["path"] == top["package"]["source_prefix"] + provider["member"])
        _check(isinstance(provider["name"], str) and provider["name"] != "")
        _check(
            _natural(provider["line_start"], 1)
            and _natural(provider["line_end"], provider["line_start"])
        )
        _check(
            record["id"]
            == link_id(
                top["vector_digest"],
                reference["id"],
                provider["entity_id"],
                provider["declaration_id"],
            )
        )
        _check(record["id"] not in seen)
        seen.add(record["id"])
    _check([item["id"] for item in links] == sorted(seen))
    _check(seen == {item["link_id"] for item in references if item["link_id"] is not None})
    _check(not links or binding["state"] == "bound")


def _validate_supersedes(top: dict[str, Any]) -> None:
    item = top["supersedes"]
    if item is None:
        return
    record = _exact(item, SUPERSEDES_KEYS)
    ids = record["invalidated_link_ids"]
    _check(ev.hash_value(record["overlay_id"]) and record["overlay_id"] != top["overlay_id"])
    _check(ev.hash_value(record["vector_digest"]) and _natural(record["links_invalidated"]))
    _check(isinstance(ids, list) and all(ev.hash_value(i) for i in ids))
    _check(ids == sorted(set(ids)) and len(ids) <= MAX_INVALIDATED_IDS)
    _check(type(record["invalidated_link_ids_truncated"]) is bool)
    if record["invalidated_link_ids_truncated"]:
        _check(len(ids) == MAX_INVALIDATED_IDS and record["links_invalidated"] > len(ids))
    else:
        _check(record["links_invalidated"] == len(ids))
    _check(not set(ids) & {item["id"] for item in top["links"]})


# ---- impact (D10) ----------------------------------------------------------------------------


def _boundary(
    kind: str,
    detail: str | None,
    at: str,
    reference: str | None,
    link: str | None,
    repository_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "detail": detail,
        "at": at,
        "reference_id": reference,
        "link_id": link,
        "repository_id": repository_id,
        "snapshot_id": snapshot_id,
    }


def _result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "link_id": record["id"],
        "reference_id": record["reference_id"],
        "relation": RELATION,
        "call_semantics": SEMANTICS,
        "depth": 1,
        "consumer": dict(record["consumer"]),
        "provider": dict(record["provider"]),
    }


def _load_overlay(raw: bytes, limit: int) -> dict[str, Any]:
    if len(raw) > limit:
        raise CrossRepoRejected("overlay_too_large")
    try:
        overlay = rv.load_strict(raw, numbers=True)
        validate_overlay(overlay)
        if canonical_bytes(overlay) != raw:
            raise ValueError("not canonical")
    except (CrossRepoRejected, ValueError, TypeError, KeyError):
        raise CrossRepoRejected("invalid_overlay") from None
    return dict(overlay)


def _has_entity(reader: Any, entity_id: str, reason: str) -> bool:
    try:
        return bool(reader.db.execute(ENTITY_SQL, (entity_id,)).fetchone() is not None)
    except sqlite3.Error:
        raise CrossRepoRejected(reason) from None


class _Collector:
    """Results and boundaries under the three budgets; the first exceeded budget ends the walk."""

    def __init__(self, budgets: Budgets, repository_id: str, snapshot_id: str) -> None:
        self.budgets = budgets
        self.where = (repository_id, snapshot_id)
        self.results: list[dict[str, Any]] = []
        self.boundaries: list[dict[str, Any]] = []
        self.examined = 0
        self.exhausted: str | None = None

    def examine(self) -> bool:
        if self.examined >= self.budgets.max_links_examined:
            return self._stop("max_links_examined")
        self.examined += 1
        return True

    def add_result(self, record: dict[str, Any]) -> bool:
        if len(self.results) >= self.budgets.max_results:
            return self._stop("max_results")
        self.results.append(record)
        return True

    def add_boundary(self, record: dict[str, Any]) -> bool:
        used = sum(1 for item in self.boundaries if item["kind"] != "budget_exhausted")
        if used >= self.budgets.max_boundaries:
            return self._stop("max_boundaries")
        self.boundaries.append(record)
        return True

    def _stop(self, name: str) -> bool:
        self.exhausted = name
        self.boundaries.append(_boundary("budget_exhausted", name, "seed", None, None, *self.where))
        return False


def impact(
    overlay_raw: bytes,
    mapping_raw: bytes,
    consumer: Side,
    provider: Side,
    seed: str,
    direction: str,
    budgets: Budgets | None = None,
    limits: LinkLimits | None = None,
) -> dict[str, Any]:
    """Depth-one potential impact across the revision vector; read-only, nothing is written."""
    budgets = normalize_budgets() if budgets is None else budgets
    limits = normalize_limits() if limits is None else limits
    if direction not in DIRECTIONS:
        raise CrossRepoUsageError("direction")
    if not ev.hash_value(seed):
        raise CrossRepoUsageError("seed")
    rv.check_roots(consumer, provider)
    overlay = _load_overlay(overlay_raw, limits.max_overlay_bytes)
    mapping = rv.parse_mapping(mapping_raw, limits.max_mapping_bytes)
    if mapping.digest != overlay["mapping"]["digest"]:
        raise CrossRepoRejected("overlay_mapping_mismatch")
    if not rv.vector_matches(overlay["revision_vector"], mapping):
        raise CrossRepoRejected("repository_not_allowlisted")
    rv.check_distinct(consumer, provider)
    with ExitStack() as stack:
        try:
            consumer_reader = rv.open_consumer(stack, consumer, mapping.consumer_snapshot)
        except CrossRepoRejected as exc:
            if exc.reason == "consumer_snapshot_mismatch":
                raise CrossRepoRejected("stale_revision_vector") from None
            raise
        provider_reader, state = rv.open_provider(
            stack, provider, mapping.provider_id, mapping.provider_snapshot
        )
        if provider_reader is not None:
            rv.check_distinct_leases(consumer_reader, provider_reader)
        collected = _traverse(
            overlay, mapping, consumer_reader, provider_reader, state, seed, direction, budgets
        )
    document = {
        "format": IMPACT_FORMAT,
        "result_id": "",
        "overlay_id": overlay["overlay_id"],
        "vector_digest": overlay["vector_digest"],
        "revision_vector": overlay["revision_vector"],
        "direction": direction,
        "seed": seed,
        "status": STATUS,
        "basis": BASIS,
        "runtime_observation": RUNTIME_OBSERVATION,
        "call_semantics": SEMANTICS,
        "depth_limit": 1,
        "provider_state": {
            "declared_snapshot_id": state["declared_snapshot_id"],
            "supplied_snapshot_id": state["supplied_snapshot_id"],
            "matched": state["matched"],
        },
        "budgets": {name: getattr(budgets, name) for name in BUDGETS},
        "work": {
            "records_examined": collected.examined,
            "results": len(collected.results),
            "boundaries": len(collected.boundaries),
        },
        "complete": collected.exhausted is None,
        "results": sorted(collected.results, key=lambda item: item["link_id"]),
        "boundaries": sorted(
            collected.boundaries,
            key=lambda item: (item["kind"], item["reference_id"] or "", item["detail"] or ""),
        ),
    }
    document["result_id"] = _impact_identity(document)
    validate_impact(document)
    return document


def _impact_identity(document: Mapping[str, Any]) -> str:
    rest = {key: value for key, value in document.items() if key != "result_id"}
    return hashlib.sha256((IMPACT_FORMAT + ":" + ev.canonical(rest)).encode("utf-8")).hexdigest()


def _traverse(
    overlay: dict[str, Any],
    mapping: rv.ParsedMapping,
    consumer_reader: Any,
    provider_reader: Any | None,
    state: dict[str, Any],
    seed: str,
    direction: str,
    budgets: Budgets,
) -> _Collector:
    stale = overlay["provider"]["state"] != "available"
    detail = state["detail"] if provider_reader is None else overlay["provider"]["detail"]
    missing = provider_reader is None or stale
    if direction == "to_provider":
        collector = _Collector(budgets, mapping.consumer_id, mapping.consumer_snapshot)
        if not _has_entity(consumer_reader, seed, "invalid_consumer_evidence"):
            raise CrossRepoRejected("unknown_seed")
        owned = sorted(
            (item for item in overlay["references"] if item["consumer"]["owner_entity_id"] == seed),
            key=lambda item: item["id"],
        )
        by_id = {item["id"]: item for item in overlay["links"]}
        for ref in owned:
            if not collector.examine():
                break
            if missing or ref["resolution"] == "unresolved_provider_snapshot_unavailable":
                cause = detail if missing else ref["detail"]
                kept = collector.add_boundary(
                    _boundary(
                        "missing_provider_snapshot",
                        cause,
                        "reference",
                        ref["id"],
                        ref["link_id"],
                        mapping.provider_id,
                        mapping.provider_snapshot,
                    )
                )
            elif ref["resolution"] == "linked_declaration":
                kept = collector.add_result(_result(by_id[ref["link_id"]]))
            else:
                kept = collector.add_boundary(
                    _boundary(
                        "unresolved_external_reference",
                        ref["resolution"],
                        "reference",
                        ref["id"],
                        None,
                        mapping.consumer_id,
                        mapping.consumer_snapshot,
                    )
                )
            if not kept:
                break
        return collector
    collector = _Collector(budgets, mapping.provider_id, mapping.provider_snapshot)
    if missing:
        collector.add_boundary(
            _boundary(
                "missing_provider_snapshot",
                detail,
                "seed",
                None,
                None,
                mapping.provider_id,
                mapping.provider_snapshot,
            )
        )
        return collector
    if not _has_entity(provider_reader, seed, "invalid_provider_evidence"):
        raise CrossRepoRejected("unknown_seed")
    for record in overlay["links"]:
        if record["provider"]["entity_id"] != seed:
            continue
        if not collector.examine() or not collector.add_result(_result(record)):
            break
    return collector


def validate_impact(document: Any) -> None:
    """Raise `invalid_overlay` unless `document` is a closed, ordered, self-consistent result."""
    try:
        _validate_impact(document)
    except CrossRepoRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError):
        raise CrossRepoRejected("invalid_overlay") from None


def _validate_impact(document: Any) -> None:
    top = _exact(document, IMPACT_KEYS)
    _check(top["format"] == IMPACT_FORMAT and top["result_id"] == _impact_identity(top))
    _check(ev.hash_value(top["overlay_id"]) and ev.hash_value(top["seed"]))
    vector = _validate_vector(top["revision_vector"])
    _check(top["vector_digest"] == rv.vector_digest(vector))
    _check(top["direction"] in DIRECTIONS and top["status"] == STATUS and top["basis"] == BASIS)
    _check(top["runtime_observation"] == RUNTIME_OBSERVATION and top["call_semantics"] == SEMANTICS)
    _check(top["depth_limit"] == 1 and type(top["complete"]) is bool)
    side = _exact(
        top["provider_state"], ("declared_snapshot_id", "supplied_snapshot_id", "matched")
    )
    _check(
        side["declared_snapshot_id"] == vector[1]["snapshot_id"] and type(side["matched"]) is bool
    )
    _check(side["supplied_snapshot_id"] is None or ev.hash_value(side["supplied_snapshot_id"]))
    budgets = _exact(top["budgets"], tuple(BUDGETS))
    for name, (_, low, high) in BUDGETS.items():
        _check(type(budgets[name]) is int and low <= budgets[name] <= high)
    results, boundaries = top["results"], top["boundaries"]
    _check(isinstance(results, list) and isinstance(boundaries, list))
    work = _exact(top["work"], ("records_examined", "results", "boundaries"))
    _check(work["results"] == len(results) and work["boundaries"] == len(boundaries))
    _check(_natural(work["records_examined"]))
    for item in results:
        record = _exact(item, RESULT_KEYS)
        _check(ev.hash_value(record["link_id"]) and ev.hash_value(record["reference_id"]))
        _check(record["relation"] == RELATION and record["call_semantics"] == SEMANTICS)
        _check(record["depth"] == 1)
        _exact(record["consumer"], LINK_CONSUMER_KEYS)
        _exact(record["provider"], LINK_PROVIDER_KEYS)
    _check([i["link_id"] for i in results] == sorted({i["link_id"] for i in results}))
    exhausted = 0
    for item in boundaries:
        record = _exact(item, BOUNDARY_KEYS)
        _check(record["kind"] in BOUNDARY_KINDS and record["at"] in ("seed", "reference"))
        _check(_is_text(record["repository_id"], _REPOSITORY_ID))
        _check(ev.hash_value(record["snapshot_id"]))
        _check(record["reference_id"] is None or ev.hash_value(record["reference_id"]))
        if record["kind"] == "budget_exhausted":
            exhausted += 1
            _check(record["detail"] in BUDGETS and record["at"] == "seed")
        elif record["kind"] == "missing_provider_snapshot":
            _check(record["detail"] in PROVIDER_DETAILS)
        else:
            _check(record["detail"] in RESOLUTIONS[1:] and record["at"] == "reference")
    keys = [(i["kind"], i["reference_id"] or "", i["detail"] or "") for i in boundaries]
    _check(keys == sorted(keys))
    _check(top["complete"] is (exhausted == 0) and exhausted <= 1)
    _check(len(boundaries) - exhausted <= budgets["max_boundaries"])
    _check(len(results) <= budgets["max_results"])
