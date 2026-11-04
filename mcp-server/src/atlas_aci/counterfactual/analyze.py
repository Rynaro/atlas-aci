"""ACI-S32: analyze a proposed patch against a declared base snapshot, in isolation.

A pure library over the existing read-only primitives. The patch is parsed and applied to the
base's hash-verified source pack bytes (never to the working tree); the patched tree is
indexed into a FRESH namespace under a scratch directory (a staged snapshot that is never
published); then the S19 change set (through the S20 count-bounded admission) and, when rules
are given, the S31 architecture check are evaluated on both sides and compared.

Private coupling (documented; the behavioural tests fail loudly on drift):
  - `CodeGraph._iter_source_files` is overridden by `_StagingGraph` so the build enumerates
    nothing: every byte comes from the explicit overlays, so the live checkout (untracked or
    edited files included) can never leak into the hypothetical snapshot;
  - `atlas_aci.query.diff._open/_lease/_check_compatible/_sha` and `reader._producer_inputs`;
  - `atlas_aci.query.impact.change_set`.

Read-only: the base namespace is opened `mode=ro`, nothing under the repository or the active
graph root is written, and the scratch directory is removed on every exit path.

Logging: the build emits `structlog` records. The CLI configures them to stderr; a library
caller must configure structlog itself, otherwise they go to structlog's default (stdout).

Every failure that can cross this boundary is typed: `PatchRejected`, `rules.InvalidRule`,
`CounterfactualUsageError`, `CounterfactualRejected` and `CounterfactualFailed`; the message is
the closed reason only, never a path or patch text.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import threading
from collections.abc import Collection, Iterable, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from atlas_aci.architecture import checker, rules
from atlas_aci.codegraph import EXPECTED_DDL_HASH as DDL_HASH
from atlas_aci.codegraph import LANG_BY_EXT, SCHEMA_EPOCH, CodeGraph
from atlas_aci.config import DEFAULT_SKIP_PATTERNS, path_is_within
from atlas_aci.counterfactual import patch_input as patches
from atlas_aci.counterfactual.patch_input import PatchRejected
from atlas_aci.enforcement import ToolError
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query import impact
from atlas_aci.query.diff import DiffRejected, DiffUsageError, Side
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import snapshots

FORMAT = "atlas-counterfactual-report.v1"
ANALYZER = "atlas-counterfactual.v1"
SNAPSHOT_DOMAIN = "atlas-counterfactual-snapshot.v1:"

BASE_LIMITS: dict[str, tuple[int, int, int]] = {
    "max_base_files": (20000, 1, 200000),
    "max_base_bytes": (268435456, 1, 2147483648),
}
REPORT_LIMITS: dict[str, tuple[int, int, int]] = {"max_listed_failures": (64, 0, 1024)}
DIFF_LIMITS: dict[str, tuple[int, int, int]] = {
    "max_diff_entities": (50000, 0, 10**9),
    "max_diff_occurrences": (250000, 0, 10**9),
    "max_changed_files": (200, 0, 10**9),
}
_DIFF_KEYS = {
    "max_diff_entities": "max_entities",
    "max_diff_occurrences": "max_occurrences",
    "max_changed_files": "max_changed_files",
}
LIMITS: dict[str, tuple[int, int, int]] = {
    **patches.PATCH_LIMITS,
    **BASE_LIMITS,
    **REPORT_LIMITS,
    **DIFF_LIMITS,
    **checker.LIMITS,
}
REJECTIONS = (
    "index_unavailable",
    "source_snapshot_unavailable",
    "index_upgrade_required",
    "base_not_buildable",
    "base_snapshot_mismatch",
    "repository_identity_mismatch",
    "configuration_mismatch",
    "producer_mismatch",
    "base_too_large",
    "live_path_is_symlink",
)
FAILURES = (
    "staging_failed",
    "staged_incompatible",
    "base_changed",
    "architecture_failed",
    "report_invalid",
    "analysis_failed",
)
USAGE_KINDS = ("limit", "base_snapshot", "scratch_root", "namespace")
STATUSES = ("complete", "incomplete", "not_requested")
SOURCE_DIFF_REASONS = (
    "diff_entity_limit",
    "diff_occurrence_limit",
    "changed_file_limit",
    "partial_paths",
    "unknown_changes",
)
_DIFF_LIMIT_REASONS = SOURCE_DIFF_REASONS[:3]
EXTRACTION_REASONS = ("staged_extraction_failed",)
DELTA_REASONS = ("counterexamples_truncated", "rule_budget_limited", "rule_scope_unknown")
LIMITATIONS: dict[str, bool] = {
    name: True
    for name in (
        "hypothetical_snapshot_is_never_published",
        "live_working_tree_content_is_not_read",
        "patch_applies_exactly_without_fuzz_or_offset",
        "renames_copies_binary_symlink_submodule_and_mode_changes_are_rejected",
        "only_indexable_source_paths_can_be_patched",
        "staging_reindexes_every_observed_file",
        "architecture_delta_inherits_s31_limitations",
        "incomplete_rule_sides_never_yield_introduced_or_resolved",
        "empty_selection_sides_are_treated_as_incomplete_by_design",
        "non_null_introduced_or_resolved_lists_are_lower_bounds_when_the_other_side_is_incomplete",
        "a_path_rename_appears_as_removal_plus_addition",
        "case_insensitive_path_collisions_are_not_modeled",
        "bases_with_retained_history_or_imported_provenance_are_rejected",
        "quoted_git_paths_and_non_utf8_patches_are_rejected",
        "first_use_grammar_download_outside_the_image",
        "not_exposed_over_mcp",
    )
}

_BASE_ID = re.compile(r"[0-9a-f]{64}")


class CounterfactualRejected(ValueError):
    """The base cannot host the analysis; `reason` is a closed code and the whole message."""

    def __init__(self, reason: str):
        if reason not in REJECTIONS:
            raise ValueError("Unknown counterfactual rejection")
        super().__init__(reason)
        self.reason = reason


class CounterfactualFailed(ValueError):
    """The analysis could not finish; `reason` is a closed code, `detail` a closed S19 code."""

    def __init__(self, reason: str, detail: str | None = None):
        if reason not in FAILURES:
            raise ValueError("Unknown counterfactual failure")
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class CounterfactualUsageError(ValueError):
    """The invocation is invalid; `kind` is closed and `name` names a limit when relevant."""

    def __init__(self, kind: str, name: str | None = None):
        if kind not in USAGE_KINDS:
            raise ValueError("Unknown counterfactual usage error")
        super().__init__(kind if name is None else kind + ": " + name)
        self.kind = kind
        self.name = name


# ---- limits --------------------------------------------------------------------------------


def normalize_limits(
    values: Mapping[str, Any] | None = None, *, with_rules: bool = False
) -> dict[str, dict[str, int] | None]:
    """Defaults for absent or None entries; unknown or out-of-range entries are usage errors.

    Architecture limits are only meaningful with a rule document; naming one without
    `with_rules` is a usage error.
    """
    given = dict(values or {})
    for name in given:
        if name not in LIMITS:
            raise CounterfactualUsageError("limit", str(name))
    if not with_rules:
        for name in checker.LIMITS:
            if given.get(name) is not None:
                raise CounterfactualUsageError("limit", name)
    groups: dict[str, dict[str, tuple[int, int, int]]] = {
        "patch": patches.PATCH_LIMITS,
        "base": BASE_LIMITS,
        "report": REPORT_LIMITS,
        "diff": DIFF_LIMITS,
        "architecture": checker.LIMITS,
    }
    result: dict[str, dict[str, int] | None] = {}
    for group, table in groups.items():
        if group == "architecture" and not with_rules:
            result[group] = None
            continue
        chosen: dict[str, int] = {}
        for name, (default, low, high) in table.items():
            value = given.get(name)
            value = default if value is None else value
            if type(value) is not int or not low <= value <= high:
                raise CounterfactualUsageError("limit", name)
            chosen[name] = value
        result[group] = chosen
    return result


# ---- staging -------------------------------------------------------------------------------


class _StagingGraph(CodeGraph):
    """Builds only explicit overlay bytes; never enumerates or reads the live checkout."""

    def _iter_source_files(self):  # type: ignore[no-untyped-def]
        return iter(())


_IDENTITY_LOCK = threading.Lock()


@contextmanager
def _recorded_identity(identity: Mapping[str, Any]):  # type: ignore[no-untyped-def]
    """Make the staged build record the BASE's repository identity instead of probing git.

    `CodeGraph` computes the manifest identity with a live `git rev-parse` (3 s timeout, subject
    to ownership checks, so it can differ between two runs on the same checkout: observed with
    bind mounts whose reported owner flips between runs). The identity is part of the snapshot id,
    so a live probe would make the staged id, the compatibility verdict and the report alternate.
    The staged tree models the same repository as the base, so it records the base's identity; no
    git process is started by the analyzer. The swap is serialised and always restored.
    """
    frozen = json.loads(json.dumps(identity))
    with _IDENTITY_LOCK:
        previous = snapshots.identity
        snapshots.identity = lambda _repo: json.loads(json.dumps(frozen))  # type: ignore[assignment]
        try:
            yield
        finally:
            snapshots.identity = previous


@dataclass
class _Base:
    reader: Any
    snapshot_id: str
    generation_id: str
    context: dict[str, Any]
    observations: dict[str, dict[str, Any]]


def _records(reader: Any) -> list[dict[str, Any]]:
    row = reader.db.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()
    records = json.loads(row[0]) if row else []
    return [r for r in records if isinstance(r, dict)]


def _generation_kind(reader: Any) -> Any:
    row = reader.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()
    return json.loads(row[0]).get("kind") if row else None


def _open_namespace(stack: ExitStack, side: Side) -> Any:
    try:
        graph = symbol_diff._open(stack, side)
    except DiffUsageError:
        raise CounterfactualUsageError("namespace") from None
    return graph


def _open_base(stack: ExitStack, side: Side, base_snapshot_id: str) -> _Base:
    """Lease the active snapshot read-only and prove it is a buildable, compatible base."""
    graph = _open_namespace(stack, side)
    try:
        reader = symbol_diff._lease(stack, graph)
    except DiffRejected as exc:
        raise CounterfactualRejected(exc.reason) from None
    if reader._closed or not reader._validated_lease or reader._lease_schema_epoch != SCHEMA_EPOCH:
        raise CounterfactualRejected("index_upgrade_required")
    try:
        if _generation_kind(reader) != "build":
            raise CounterfactualRejected("base_not_buildable")
        if reader.snapshot_id is None or reader._source_lease is None:
            raise CounterfactualRejected("source_snapshot_unavailable")
        if reader.snapshot_id != base_snapshot_id:
            raise CounterfactualRejected("base_snapshot_mismatch")
        context = reader._source_lease.manifest["context"]
        configuration = context["configuration"]
        languages = configuration.get("languages")
        if context["schema"] != {"epoch": SCHEMA_EPOCH, "ddl_hash": DDL_HASH}:
            raise CounterfactualRejected("index_upgrade_required")
        if context["repository"] != str(side.repo.resolve()):
            raise CounterfactualRejected("repository_identity_mismatch")
        if (
            not isinstance(languages, list)
            or not languages
            or not all(isinstance(item, str) for item in languages)
            or configuration
            != {"languages": languages, "skip_patterns": list(DEFAULT_SKIP_PATTERNS)}
        ):
            raise CounterfactualRejected("configuration_mismatch")
        if context["producer"] != reader._producer_inputs():
            raise CounterfactualRejected("producer_mismatch")
        records = _records(reader)
        if any(r.get("type") != "observation" for r in records):
            raise CounterfactualRejected("base_not_buildable")
        observations = {r["path"]: r for r in records}
        generation_id = reader.generation_id
    except CounterfactualRejected:
        raise
    except (sqlite3.Error, ToolError, ValueError, KeyError, TypeError, OSError, RuntimeError):
        raise CounterfactualRejected("source_snapshot_unavailable") from None
    return _Base(reader, reader.snapshot_id, generation_id, context, observations)


def _bounds(base: _Base, limits: Mapping[str, int]) -> None:
    """`base_too_large`, decided from the pack index alone before any source is read."""
    if len(base.observations) > limits["max_base_files"]:
        raise CounterfactualRejected("base_too_large")
    try:
        index = base.reader._source_lease.index
        distinct = {r["observed_source_sha256"] for r in base.observations.values()}
        total = sum(index[sha]["length"] for sha in distinct)
    except (KeyError, TypeError):
        raise CounterfactualRejected("source_snapshot_unavailable") from None
    if total > limits["max_base_bytes"]:
        raise CounterfactualRejected("base_too_large")


def _read(base: _Base, path: str) -> bytes:
    try:
        return bytes(
            base.reader.read_source(path, base.observations[path]["observed_source_sha256"])
        )
    except (ToolError, ValueError, KeyError, TypeError, OSError, RuntimeError):
        raise CounterfactualRejected("source_snapshot_unavailable") from None


def _symlink_preflight(repo: Path, paths: Iterable[str]) -> None:
    """Metadata only, mirroring the build's overlay validation: a symlinked staged path blocks."""
    clean: set[Path] = set()
    try:
        for rel in paths:
            node = repo / rel
            chain = []
            for parent in (node, *node.parents):
                if parent == repo or parent in clean:
                    break
                if parent.is_symlink():
                    raise CounterfactualRejected("live_path_is_symlink")
                chain.append(parent)
            clean.update(chain)
    except OSError:
        raise CounterfactualFailed("staging_failed") from None


def _scratch(scratch_root: Path | None, side: Side) -> Path:
    chosen = Path(tempfile.gettempdir()) if scratch_root is None else Path(scratch_root)
    try:
        resolved = chosen.resolve()
        usable = resolved.is_dir()
    except (OSError, RuntimeError, ValueError):
        raise CounterfactualUsageError("scratch_root") from None
    namespace = (side.graph_root if side.graph_root is not None else side.repo / ".atlas").resolve()
    if (
        not usable
        or path_is_within(side.repo.resolve(), resolved)
        or path_is_within(namespace, resolved)
    ):
        raise CounterfactualUsageError("scratch_root")
    return resolved


@dataclass
class _Applied:
    patch: patches.FilePatch
    base_sha256: str | None
    result: bytes | None


def _apply_patch(base: _Base, patch: patches.Patch, limits: Mapping[str, Any]) -> list[_Applied]:
    """P2: scope, presence and exact application per file in sorted path order."""
    scope = patches.Scope(
        base.observations,
        [f.path for f in patch.files if f.op == "create"],
        tuple(LANG_BY_EXT),
        DEFAULT_SKIP_PATTERNS,
    )
    applied: list[_Applied] = []
    for item in sorted(patch.files, key=lambda f: f.path):
        scope.check(item)
        data: bytes | None = None
        sha: str | None = None
        if item.op != "create":
            record = base.observations[item.path]
            sha = record["observed_source_sha256"]
            try:
                length = base.reader._source_lease.index[sha]["length"]
            except (KeyError, TypeError):
                raise CounterfactualRejected("source_snapshot_unavailable") from None
            if length > limits["base"]["max_base_bytes"]:
                raise CounterfactualRejected("base_too_large")
            data = _read(base, item.path)
        result = patches.apply(item, data, limits["patch"])
        applied.append(_Applied(item, sha, result))
    return applied


def _stage(
    stack: ExitStack, base: _Base, applied: list[_Applied], scratch: Path, repo: Path
) -> Path:
    """Index base bytes plus the patch into a fresh namespace under `scratch`; never published."""
    namespace = Path(
        stack.enter_context(
            tempfile.TemporaryDirectory(prefix="atlas-counterfactual-", dir=scratch)
        )
    )
    blobs: dict[str, bytes] = {}
    overlays: dict[str, bytes] = {}
    gone = {a.patch.path for a in applied if a.result is None}
    for path in sorted(base.observations):
        if path in gone:
            continue
        sha = base.observations[path]["observed_source_sha256"]
        if sha not in blobs:
            blobs[sha] = _read(base, path)
        overlays[path] = blobs[sha]
    for item in applied:
        if item.result is not None:
            overlays[item.patch.path] = item.result
    del blobs
    _symlink_preflight(repo, overlays)
    graph = _StagingGraph(
        repo, langs=list(base.context["configuration"]["languages"]), graph_root=namespace
    )
    try:
        with _recorded_identity(base.context["identity"]):
            graph.build(overlays=overlays)
    except Exception:
        raise CounterfactualFailed("staging_failed") from None
    finally:
        graph.close()
        del overlays
    return namespace


# ---- analyses ------------------------------------------------------------------------------


def _state_counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {"observed": 0, "indexed": 0, "failed": 0, "unsupported": 0, "disabled": 0}
    for record in records:
        counts["observed"] += 1
        counts[record["state"]] += 1
    return counts


def _pairs(rule: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {(e["source_path"], e["target_path"]) for e in rule["counterexamples"]}


def _exhaustive(rule: Mapping[str, Any]) -> bool:
    return rule["counts"]["violating_pairs"] == len(rule["counterexamples"]) and not rule["reasons"]


def _listed(pairs: Collection[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"source_path": s, "target_path": t} for s, t in sorted(pairs)]


def rule_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per-rule before/after classification keyed by `(rule, source_path, target_path)`.

    `persisting` is always sound (witnesses are sound). `introduced` is only listed when the
    BEFORE side is exhaustive and `resolved` only when the AFTER side is, otherwise null; a
    non-null list is then a lower bound when the other side is incomplete.
    """
    result = []
    for one, two in zip(before["rules"], after["rules"], strict=True):
        was, now = _pairs(one), _pairs(two)
        before_ok, after_ok = _exhaustive(one), _exhaustive(two)
        reasons: set[str] = set()
        for rule in (one, two):
            if rule["counts"]["violating_pairs"] != len(rule["counterexamples"]):
                reasons.add("counterexamples_truncated")
            if set(rule["reasons"]) & set(checker.BUDGET_REASONS):
                reasons.add("rule_budget_limited")
            if set(rule["reasons"]) & set(checker.UNKNOWN_REASONS):
                reasons.add("rule_scope_unknown")
        result.append(
            {
                "index": one["index"],
                "id": one["id"],
                "before_outcome": one["outcome"],
                "after_outcome": two["outcome"],
                "status": "complete" if before_ok and after_ok else "incomplete",
                "reasons": {name: True for name in sorted(reasons)},
                "before_exhaustive": before_ok,
                "after_exhaustive": after_ok,
                "counts": {
                    "before_violating_pairs": one["counts"]["violating_pairs"],
                    "after_violating_pairs": two["counts"]["violating_pairs"],
                },
                "introduced": _listed(now - was) if before_ok else None,
                "resolved": _listed(was - now) if after_ok else None,
                "persisting": _listed(was & now),
            }
        )
    return result


def _flags(names: Iterable[str]) -> dict[str, bool]:
    return {name: True for name in sorted(set(names))}


def _status(reasons: Mapping[str, bool]) -> dict[str, Any]:
    return {"status": "incomplete" if reasons else "complete", "reasons": dict(reasons)}


# ---- identity ------------------------------------------------------------------------------


def hypothetical_snapshot_id(identity: Mapping[str, Any]) -> str:
    """Names the hypothetical state; it identifies no stored object."""
    return hashlib.sha256((SNAPSHOT_DOMAIN + ev.canonical(identity)).encode("utf-8")).hexdigest()


def report_id(report: Mapping[str, Any]) -> str:
    body = {key: value for key, value in report.items() if key != "report_id"}
    return hashlib.sha256((FORMAT + ":" + ev.canonical(body)).encode("utf-8")).hexdigest()


def canonical_bytes(report: Mapping[str, Any]) -> bytes:
    """The exact bytes the CLI prints: canonical JSON plus one newline."""
    return ev.canonical(report).encode("utf-8") + b"\n"


def _identity(
    base: _Base, patch: patches.Patch, compatibility: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "kind": "hypothetical",
        "published": False,
        "base_snapshot_id": base.snapshot_id,
        "base_generation_id": base.generation_id,
        "patch_sha256": patch.sha256,
        "configuration_sha256": compatibility["configuration_sha256"],
        "producer_sha256": compatibility["producer_sha256"],
        "analyzer_version": ANALYZER,
    }


# ---- run -----------------------------------------------------------------------------------


def run(
    side: Side,
    base_snapshot_id: str,
    patch_bytes: bytes,
    *,
    rule_bytes: bytes | None = None,
    limits: Mapping[str, Any] | None = None,
    scratch_root: Path | None = None,
) -> dict[str, Any]:
    """Analyze `patch_bytes` against the active snapshot `base_snapshot_id` of `side`.

    Stage order, first failure wins and nothing is returned on failure: limits, patch parse,
    rule load, scratch root, base lease and checks, patch application against the base pack,
    base size bounds and live-symlink preflight, staging, compatibility and change set, rule
    check on both sides, base re-verification, validation.
    """
    try:
        return _run(side, base_snapshot_id, patch_bytes, rule_bytes, limits, scratch_root)
    except (
        PatchRejected,
        rules.InvalidRule,
        CounterfactualUsageError,
        CounterfactualRejected,
        CounterfactualFailed,
    ):
        raise
    except Exception:
        raise CounterfactualFailed("analysis_failed") from None


def _run(
    side: Side,
    base_snapshot_id: str,
    patch_bytes: bytes,
    rule_bytes: bytes | None,
    limits: Mapping[str, Any] | None,
    scratch_root: Path | None,
) -> dict[str, Any]:
    effective = normalize_limits(limits, with_rules=rule_bytes is not None)
    if not isinstance(base_snapshot_id, str) or _BASE_ID.fullmatch(base_snapshot_id) is None:
        raise CounterfactualUsageError("base_snapshot")
    patch = patches.parse(patch_bytes, effective["patch"])
    ruleset = None if rule_bytes is None else rules.load(rule_bytes)
    scratch = _scratch(scratch_root, side)
    repo = side.repo.resolve()
    with ExitStack() as stack:
        base = _open_base(stack, side, base_snapshot_id)
        _bounds(base, effective["base"] or {})
        applied = _apply_patch(base, patch, effective)
        namespace = _stage(stack, base, applied, scratch, repo)
        staged = _open_staged(stack, repo, namespace)
        compatibility = _compatible(base, staged)
        change_set, diff_work, limit_reasons = _source_diff(base, staged, effective)
        architecture = _architecture(base, staged, ruleset, effective)
        report = _assemble(
            base,
            staged,
            patch,
            applied,
            compatibility,
            change_set,
            diff_work,
            limit_reasons,
            architecture,
            effective,
        )
    _verify_base(side, base)
    try:
        validate_report(report)
    except ValueError:
        raise CounterfactualFailed("report_invalid") from None
    return report


def _open_staged(stack: ExitStack, repo: Path, namespace: Path) -> Any:
    try:
        graph = symbol_diff._open(stack, Side(repo, namespace))
    except DiffUsageError:
        raise CounterfactualFailed("staging_failed") from None
    try:
        reader = symbol_diff._lease(stack, graph)
    except DiffRejected as exc:
        raise CounterfactualFailed("staged_incompatible", exc.reason) from None
    if reader._closed or not reader._validated_lease or reader._lease_schema_epoch != SCHEMA_EPOCH:
        raise CounterfactualFailed("staged_incompatible", "schema_mismatch")
    return reader


def _compatible(base: _Base, staged: Any) -> dict[str, Any]:
    try:
        return dict(symbol_diff._check_compatible(base.reader, staged))
    except DiffRejected as exc:
        raise CounterfactualFailed("staged_incompatible", exc.reason) from None
    except (ToolError, ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error):
        raise CounterfactualFailed("staged_incompatible", "index_unavailable") from None


def _source_diff(
    base: _Base, staged: Any, effective: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, int] | None, list[str]]:
    diff_limits = {_DIFF_KEYS[name]: value for name, value in effective["diff"].items()}
    try:
        change_set, _loaded, diff_work = impact.change_set(base.reader, staged, diff_limits)
    except impact.ImpactError as exc:
        if exc.code == "IMPACT_DIFF_TOO_LARGE" and exc.reason in _DIFF_LIMIT_REASONS:
            return None, None, [str(exc.reason)]
        detail = exc.reason if exc.reason in symbol_diff.REJECTIONS else "index_unavailable"
        raise CounterfactualFailed("staged_incompatible", detail) from None
    except (ToolError, ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error):
        raise CounterfactualFailed("staged_incompatible", "index_unavailable") from None
    return change_set, diff_work, []


def _architecture(
    base: _Base, staged: Any, ruleset: Any, effective: Mapping[str, Any]
) -> dict[str, Any] | None:
    if ruleset is None:
        return None
    try:
        before = checker.check(base.reader, ruleset, effective["architecture"])
        after = checker.check(staged, ruleset, effective["architecture"])
        checker.validate_report(before)
        checker.validate_report(after)
        deltas = rule_delta(before, after)
    except Exception:
        raise CounterfactualFailed("architecture_failed") from None
    return {
        "rules_format": rules.FORMAT,
        "rules_sha256": ruleset.sha256,
        "before_report_id": before["report_id"],
        "after_report_id": after["report_id"],
        "reports": {"before": before, "after": after},
        "rules": deltas,
    }


def _verify_base(side: Side, base: _Base) -> None:
    """A concurrent re-index must not slip a different base under an already-staged analysis."""
    try:
        with ExitStack() as stack:
            graph = symbol_diff._open(stack, side)
            reader = symbol_diff._lease(stack, graph)
            same = (
                reader.snapshot_id == base.snapshot_id
                and reader.generation_id == base.generation_id
            )
    except Exception:
        raise CounterfactualFailed("base_changed") from None
    if not same:
        raise CounterfactualFailed("base_changed")


def _assemble(
    base: _Base,
    staged: Any,
    patch: patches.Patch,
    applied: list[_Applied],
    compatibility: dict[str, Any],
    change_set: dict[str, Any] | None,
    diff_work: dict[str, int] | None,
    limit_reasons: list[str],
    architecture: dict[str, Any] | None,
    effective: Mapping[str, Any],
) -> dict[str, Any]:
    # S19's notion of a partial path: not `indexed` on either side (failed, unsupported, disabled).
    base_failed = {p for p, r in base.observations.items() if r["state"] != "indexed"}
    staged_records = {r["path"]: r for r in _records(staged) if r.get("type") == "observation"}
    staged_failed = {p for p, r in staged_records.items() if r["state"] != "indexed"}
    in_patch = {a.patch.path for a in applied}
    touched = sorted(
        p
        for p in base_failed | staged_failed
        if p in in_patch or ((p in base_failed) != (p in staged_failed))
    )
    preexisting = sorted((base_failed | staged_failed) - set(touched))

    reasons: dict[str, bool] = {}
    for name in limit_reasons:
        reasons[name] = True
    if touched:
        reasons["partial_paths"] = True
    if change_set is not None and any(c["category"] == "unknown" for c in change_set["changes"]):
        reasons["unknown_changes"] = True
    counts = _state_counts(staged_records.values())
    listed = sorted(
        (
            {"path": p, "patched": p in in_patch, "reason": str(r["reason"])}
            for p, r in staged_records.items()
            if r["state"] == "failed"
        ),
        key=lambda item: item["path"],
    )
    cap = effective["report"]["max_listed_failures"]
    extraction_reasons = {"staged_extraction_failed": True} if counts["failed"] else {}
    if architecture is None:
        arch_state: dict[str, Any] = {"status": "not_requested", "reasons": {}}
    else:
        arch_state = _status(
            _flags(name for entry in architecture["rules"] for name in entry["reasons"])
        )
    files = []
    for item in applied:
        data = item.result
        files.append(
            {
                "path": item.patch.path,
                "op": item.patch.op,
                "mode": item.patch.mode,
                "hunks": len(item.patch.hunks),
                "added": item.patch.added,
                "removed": item.patch.removed,
                "base_sha256": item.base_sha256,
                "patched_sha256": None if data is None else hashlib.sha256(data).hexdigest(),
                "base_bytes": None
                if item.base_sha256 is None
                else base.reader._source_lease.index[item.base_sha256]["length"],
                "patched_bytes": None if data is None else len(data),
            }
        )
    identity = _identity(base, patch, compatibility)
    report: dict[str, Any] = {
        "format": FORMAT,
        "analyzer_version": ANALYZER,
        "hypothetical": {
            **identity,
            "hypothetical_snapshot_id": hypothetical_snapshot_id(identity),
        },
        "base": {
            "snapshot_id": base.snapshot_id,
            "generation_id": base.generation_id,
            "generation_kind": "build",
        },
        "staged": {
            "snapshot_id": staged.snapshot_id,
            "generation_id": staged.generation_id,
            "published": False,
            "informational": True,
        },
        "compatibility": compatibility,
        "patch": {
            "sha256": patch.sha256,
            "bytes": patch.size,
            "files_total": patch.files_total,
            "hunks_total": patch.hunks_total,
            "lines_added": sum(f["added"] for f in files),
            "lines_removed": sum(f["removed"] for f in files),
            "files": files,
        },
        "effective_limits": {
            "patch": effective["patch"],
            "base": effective["base"],
            "diff": effective["diff"],
            "report": effective["report"],
            "architecture": effective["architecture"],
        },
        "analyses": {
            "extraction": _status(extraction_reasons),
            "source_diff": _status(reasons),
            "architecture": arch_state,
        },
        "extraction": {
            "counts": counts,
            "failed": listed[:cap],
            "failed_total": len(listed),
        },
        "source_diff": {
            "change_set": change_set,
            "diff_work": diff_work,
            "partial_paths_touched": touched,
            "partial_paths_preexisting": preexisting,
        },
        "architecture": architecture,
        "limitations": dict(LIMITATIONS),
    }
    report["report_id"] = report_id(report)
    return report


# ---- validation ----------------------------------------------------------------------------

_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_COUNT = {"type": "integer", "minimum": 0}
_TEXT = {"type": "string"}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(properties),
        "properties": properties,
    }


def _enum(values: Iterable[str]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _true_flags(values: Iterable[str]) -> dict[str, Any]:
    names = list(values)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {name: {"const": True} for name in names},
    }


def _limits(table: Mapping[str, tuple[int, int, int]]) -> dict[str, Any]:
    return _obj({name: _COUNT for name in table})


_ANALYSIS = {
    "extraction": EXTRACTION_REASONS,
    "source_diff": SOURCE_DIFF_REASONS,
    "architecture": DELTA_REASONS,
}
_PAIR = _obj({"source_path": _TEXT, "target_path": _TEXT})
_PAIRS = {"type": "array", "items": _PAIR}
_REPORT_SCHEMA: dict[str, Any] = _obj(
    {
        "format": {"const": FORMAT},
        "analyzer_version": {"const": ANALYZER},
        "hypothetical": _obj(
            {
                "kind": {"const": "hypothetical"},
                "published": {"const": False},
                "base_snapshot_id": _HASH,
                "base_generation_id": _HASH,
                "patch_sha256": _HASH,
                "configuration_sha256": _HASH,
                "producer_sha256": _HASH,
                "analyzer_version": {"const": ANALYZER},
                "hypothetical_snapshot_id": _HASH,
            }
        ),
        "base": _obj(
            {
                "snapshot_id": _HASH,
                "generation_id": _HASH,
                "generation_kind": {"const": "build"},
            }
        ),
        "staged": _obj(
            {
                "snapshot_id": _HASH,
                "generation_id": _HASH,
                "published": {"const": False},
                "informational": {"const": True},
            }
        ),
        "compatibility": _obj(
            {
                "identity_kind": _TEXT,
                "identity_rule": _TEXT,
                "schema": _obj({"epoch": _COUNT, "ddl_hash": _TEXT}),
                "configuration_sha256": _HASH,
                "producer_sha256": _HASH,
            }
        ),
        "patch": _obj(
            {
                "sha256": _HASH,
                "bytes": _COUNT,
                "files_total": _COUNT,
                "hunks_total": _COUNT,
                "lines_added": _COUNT,
                "lines_removed": _COUNT,
                "files": {
                    "type": "array",
                    "items": _obj(
                        {
                            "path": _TEXT,
                            "op": _enum(patches.OPERATIONS),
                            "mode": _nullable(_enum(patches.REGULAR_MODES)),
                            "hunks": _COUNT,
                            "added": _COUNT,
                            "removed": _COUNT,
                            "base_sha256": _nullable(_HASH),
                            "patched_sha256": _nullable(_HASH),
                            "base_bytes": _nullable(_COUNT),
                            "patched_bytes": _nullable(_COUNT),
                        }
                    ),
                },
            }
        ),
        "effective_limits": _obj(
            {
                "patch": _limits(patches.PATCH_LIMITS),
                "base": _limits(BASE_LIMITS),
                "diff": _limits(DIFF_LIMITS),
                "report": _limits(REPORT_LIMITS),
                "architecture": _nullable(_limits(checker.LIMITS)),
            }
        ),
        "analyses": _obj(
            {
                name: _obj(
                    {"status": _enum(STATUSES), "reasons": _true_flags(reasons)},
                )
                for name, reasons in _ANALYSIS.items()
            }
        ),
        "extraction": _obj(
            {
                "counts": _obj(
                    {
                        name: _COUNT
                        for name in ("observed", "indexed", "failed", "unsupported", "disabled")
                    }
                ),
                "failed": {
                    "type": "array",
                    "items": _obj({"path": _TEXT, "patched": {"type": "boolean"}, "reason": _TEXT}),
                },
                "failed_total": _COUNT,
            }
        ),
        "source_diff": _obj(
            {
                "change_set": _nullable({"type": "object"}),
                "diff_work": _nullable({"type": "object"}),
                "partial_paths_touched": {"type": "array", "items": _TEXT},
                "partial_paths_preexisting": {"type": "array", "items": _TEXT},
            }
        ),
        "architecture": _nullable(
            _obj(
                {
                    "rules_format": _TEXT,
                    "rules_sha256": _HASH,
                    "before_report_id": _HASH,
                    "after_report_id": _HASH,
                    "reports": _obj({"before": {"type": "object"}, "after": {"type": "object"}}),
                    "rules": {
                        "type": "array",
                        "items": _obj(
                            {
                                "index": _COUNT,
                                "id": _TEXT,
                                "before_outcome": _enum(checker.OUTCOMES),
                                "after_outcome": _enum(checker.OUTCOMES),
                                "status": _enum(("complete", "incomplete")),
                                "reasons": _true_flags(DELTA_REASONS),
                                "before_exhaustive": {"type": "boolean"},
                                "after_exhaustive": {"type": "boolean"},
                                "counts": _obj(
                                    {
                                        "before_violating_pairs": _COUNT,
                                        "after_violating_pairs": _COUNT,
                                    }
                                ),
                                "introduced": _nullable(_PAIRS),
                                "resolved": _nullable(_PAIRS),
                                "persisting": _PAIRS,
                            }
                        ),
                    },
                }
            )
        ),
        "limitations": {"const": LIMITATIONS},
        "report_id": _HASH,
    }
)
_VALIDATOR = Draft202012Validator(_REPORT_SCHEMA)


def _invalid(message: str) -> ValueError:
    return ValueError("Invalid counterfactual report: " + message)


def _check_analyses(report: Mapping[str, Any]) -> None:
    analyses = report["analyses"]
    for name, state in analyses.items():
        complete = state["status"] == "complete"
        if state["status"] == "not_requested":
            if name != "architecture" or state["reasons"] or report["architecture"] is not None:
                raise _invalid("not_requested")
        elif complete != (not state["reasons"]):
            raise _invalid("status")
    if (analyses["architecture"]["status"] == "not_requested") != (report["architecture"] is None):
        raise _invalid("architecture request")
    extraction = report["extraction"]
    counts = extraction["counts"]
    if (
        counts["observed"]
        != sum(counts[k] for k in ("indexed", "failed", "unsupported", "disabled"))
        or extraction["failed_total"] != counts["failed"]
        or len(extraction["failed"]) > counts["failed"]
        or len(extraction["failed"]) > report["effective_limits"]["report"]["max_listed_failures"]
        or [f["path"] for f in extraction["failed"]]
        != sorted(f["path"] for f in extraction["failed"])
        or bool(counts["failed"]) != bool(analyses["extraction"]["reasons"])
    ):
        raise _invalid("extraction")
    diff = report["source_diff"]
    reasons = set(analyses["source_diff"]["reasons"])
    limit_reasons = reasons & set(_DIFF_LIMIT_REASONS)
    change_set = diff["change_set"]
    if (
        len(limit_reasons) > 1
        or (change_set is None) != bool(limit_reasons)
        or (diff["diff_work"] is None) != (change_set is None)
        or ("partial_paths" in reasons) != bool(diff["partial_paths_touched"])
        or diff["partial_paths_touched"] != sorted(set(diff["partial_paths_touched"]))
        or diff["partial_paths_preexisting"] != sorted(set(diff["partial_paths_preexisting"]))
        or set(diff["partial_paths_touched"]) & set(diff["partial_paths_preexisting"])
    ):
        raise _invalid("source diff")
    if change_set is not None:
        if sorted(
            diff["partial_paths_touched"] + diff["partial_paths_preexisting"]
        ) != change_set.get("partial_paths"):
            raise _invalid("partial paths")
        body = {k: v for k, v in change_set.items() if k != "id"}
        unknown = any(c.get("category") == "unknown" for c in change_set.get("changes", []))
        digest = hashlib.sha256(
            (symbol_diff.FORMAT + ":" + ev.canonical(body)).encode("utf-8")
        ).hexdigest()
        if (
            change_set.get("format") != symbol_diff.FORMAT
            or change_set.get("id") != digest
            or change_set["before"]["snapshot_id"] != report["base"]["snapshot_id"]
            or change_set["after"]["snapshot_id"] != report["staged"]["snapshot_id"]
            or unknown != ("unknown_changes" in reasons)
        ):
            raise _invalid("change set")
    elif "unknown_changes" in reasons:
        raise _invalid("change set")


def _check_architecture(report: Mapping[str, Any]) -> None:
    section = report["architecture"]
    if section is None:
        return
    before, after = section["reports"]["before"], section["reports"]["after"]
    checker.validate_report(before)
    checker.validate_report(after)
    if (
        section["before_report_id"] != before["report_id"]
        or section["after_report_id"] != after["report_id"]
        or section["rules_sha256"] != before["rules_sha256"]
        or section["rules_sha256"] != after["rules_sha256"]
        or before["effective_limits"] != after["effective_limits"]
        or before["effective_limits"] != report["effective_limits"]["architecture"]
        or before["snapshot"]["snapshot_id"] != report["base"]["snapshot_id"]
        or after["snapshot"]["snapshot_id"] != report["staged"]["snapshot_id"]
        or section["rules"] != rule_delta(before, after)
    ):
        raise _invalid("architecture")
    entries = section["rules"]
    union: set[str] = set()
    for entry in entries:
        ready = entry["before_exhaustive"] and entry["after_exhaustive"]
        if (
            (entry["introduced"] is None) != (not entry["before_exhaustive"])
            or (entry["resolved"] is None) != (not entry["after_exhaustive"])
            or (entry["status"] == "complete") != ready
            or bool(entry["reasons"]) == ready
        ):
            raise _invalid("delta")
        union.update(entry["reasons"])
    state = report["analyses"]["architecture"]
    incomplete = any(e["status"] == "incomplete" for e in entries)
    if (state["status"] == "incomplete") != incomplete or set(state["reasons"]) != union:
        raise _invalid("architecture status")


def validate_report(report: Mapping[str, Any]) -> None:
    """The closed schema, then the semantic rules a schema cannot express."""
    if next(_VALIDATOR.iter_errors(report), None) is not None:
        raise _invalid("schema")
    files = report["patch"]["files"]
    patch = report["patch"]
    if (
        [f["path"] for f in files] != sorted(f["path"] for f in files)
        or len({f["path"] for f in files}) != len(files)
        or patch["files_total"] != len(files)
        or patch["hunks_total"] != sum(f["hunks"] for f in files)
        or patch["lines_added"] != sum(f["added"] for f in files)
        or patch["lines_removed"] != sum(f["removed"] for f in files)
    ):
        raise _invalid("patch")
    for group, table in (
        ("patch", patches.PATCH_LIMITS),
        ("base", BASE_LIMITS),
        ("diff", DIFF_LIMITS),
        ("report", REPORT_LIMITS),
    ):
        for name, (_default, low, high) in table.items():
            if not low <= report["effective_limits"][group][name] <= high:
                raise _invalid("effective limit")
    identity = {
        key: report["hypothetical"][key]
        for key in report["hypothetical"]
        if key != "hypothetical_snapshot_id"
    }
    if (
        report["hypothetical"]["base_snapshot_id"] != report["base"]["snapshot_id"]
        or report["hypothetical"]["base_generation_id"] != report["base"]["generation_id"]
        or report["hypothetical"]["patch_sha256"] != patch["sha256"]
        or report["hypothetical"]["configuration_sha256"]
        != report["compatibility"]["configuration_sha256"]
        or report["hypothetical"]["producer_sha256"] != report["compatibility"]["producer_sha256"]
        or report["hypothetical"]["hypothetical_snapshot_id"] != hypothetical_snapshot_id(identity)
    ):
        raise _invalid("identity")
    _check_analyses(report)
    _check_architecture(report)
    if report["report_id"] != report_id(report):
        raise _invalid("report_id")


__all__ = [
    "ANALYZER",
    "FAILURES",
    "FORMAT",
    "LIMITATIONS",
    "LIMITS",
    "REJECTIONS",
    "CounterfactualFailed",
    "CounterfactualRejected",
    "CounterfactualUsageError",
    "canonical_bytes",
    "hypothetical_snapshot_id",
    "normalize_limits",
    "report_id",
    "rule_delta",
    "run",
    "validate_report",
]
