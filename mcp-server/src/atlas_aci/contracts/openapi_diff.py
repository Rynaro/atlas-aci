"""Explain contract changes between two `atlas-contract-snapshot.v1` snapshots.

The change set reuses the shape of the S19 change set (format string, content id, `before` and
`after` references, a summary by kind, limitations, canonical bytes plus one newline, stable
sort). S19 itself is not invoked and no graph, repository or evidence store is read: both inputs
are contract snapshots built from artifact bytes, so the references are artifact digest plus
JSON pointer, not source-snapshot provenance.

There is no verdict. A finding list is a list of concrete changes; whatever the importer or the
differ cannot judge is reported as `review_required` with a closed reason.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from atlas_aci.importers.openapi31 import (
    IGNORED_FIELDS,
    LIMITATIONS,
    PROFILE,
    REGION_NAMES,
    OpenAPIUsageError,
    SnapshotInvalid,
    validate_snapshot,
)
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-contract-diff.v1"
FINDING_FORMAT = "atlas-contract-finding.v1"
DIFFER = "atlas-openapi-diff-rules.v1"
FINDING_KINDS = (
    "operation_removed",
    "newly_required_parameter",
    "review_required",
    "operation_added",
)
REVIEW_REASONS = (
    "incomplete_region",
    "ambiguous_identity",
    "region_changed",
    "parameter_added",
    "parameter_removed",
    "parameter_changed",
    "path_parameter_changed",
    "document_region_changed",
)
CHANGES = ("added_required", "optional_to_required")
FINDING_REGIONS = (*REGION_NAMES, "path_item", "operation", "document")
BLOCKABLE = ("operation_removed", "operation_added")
ACTIONS = ("review", "informational")
REJECTIONS = ("invalid_snapshot", "profile_mismatch")
LIMITS: dict[str, tuple[int, int, int]] = {"max_findings": (1000, 0, 100000)}
_SIDES = ("artifact_sha256", "snapshot_id", "status", "operations", "diagnostics_total")


class ContractDiffRejected(ValueError):
    """The two inputs cannot be compared. Carries one closed reason, never snapshot text."""

    def __init__(self, reason: str) -> None:
        super().__init__("contract_diff_rejected: " + reason)
        self.reason = reason


@dataclass(frozen=True)
class DiffOptions:
    max_findings: int = 1000

    def as_dict(self) -> dict[str, Any]:
        return {"max_findings": self.max_findings, "ignored_fields": list(IGNORED_FIELDS)}


def normalize_diff_options(chosen: Mapping[str, int | None] | None = None) -> DiffOptions:
    chosen = dict(chosen or {})
    for name in chosen:
        if name not in LIMITS:
            raise OpenAPIUsageError(str(name))
    default, low, high = LIMITS["max_findings"]
    value = chosen.get("max_findings")
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise OpenAPIUsageError("max_findings")
    return DiffOptions(value)


def canonical_bytes(change_set: dict[str, Any]) -> bytes:
    """The exact bytes the CLI prints: canonical JSON plus one newline."""
    return ev.canonical(change_set).encode("utf-8") + b"\n"


def _identifier(prefix: str, rest: dict[str, Any]) -> str:
    return hashlib.sha256((prefix + ":" + ev.canonical(rest)).encode("utf-8")).hexdigest()


def _finding(
    kind: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    reason: str | None = None,
    change: str | None = None,
    blocked: str | None = None,
    identity: dict[str, str] | None = None,
    region: str | None = None,
    parameter: dict[str, Any] | None = None,
    reserved: bool | None = None,
) -> dict[str, Any]:
    rest = {
        "type": "contract_finding",
        "kind": kind,
        "action": "informational" if kind == "operation_added" else "review",
        "reason": reason,
        "change": change,
        "blocked_finding": blocked,
        "identity": identity,
        "region": region,
        "parameter": parameter,
        "reserved_header": reserved,
        "before": before,
        "after": after,
    }
    return {**rest, "id": _identifier(FINDING_FORMAT, rest)}


def _ref(snapshot: dict[str, Any], at: str, declared: str | None = None) -> dict[str, Any]:
    return {
        "artifact_sha256": snapshot["artifact"]["sha256"],
        "snapshot_id": snapshot["snapshot_id"],
        "pointer": at,
        "declared_pointer": declared,
    }


class _Side:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for record in snapshot["operations"]:
            identity = record["identity"]
            self.groups.setdefault((identity["path"], identity["method"]), []).append(record)
        self.items = {r["path_template"]: r for r in snapshot["path_items"]}
        # path items are sorted by template, so the first of each identity path is stable
        self.incomplete = [r for r in snapshot["path_items"] if r["status"] == "incomplete"]
        self.by_identity_path: dict[str, dict[str, Any]] = {}
        for record in snapshot["path_items"]:
            if record["identity_path"] is not None:
                self.by_identity_path.setdefault(record["identity_path"], record)
        self.regions = {r["pointer"]: r for r in snapshot["document_regions"]}
        counts: dict[str, int] = {}
        for record in snapshot["operations"]:
            counts[record["path_template"]] = counts.get(record["path_template"], 0) + 1
        self.op_counts = counts

    def op_ref(self, record: dict[str, Any]) -> dict[str, Any]:
        return _ref(self.snapshot, record["provenance"]["pointer"])

    def item_ref(self, record: dict[str, Any]) -> dict[str, Any]:
        return _ref(self.snapshot, record["provenance"]["pointer"], record["declared_pointer"])

    def blocking_item(self, path: str) -> dict[str, Any] | None:
        """An incomplete path item whose identity path is `path` (or unknown) on this side."""
        for record in self.incomplete:
            if record["identity_path"] in (path, None):
                return record
        return None

    def anchor(self, template: str, path: str) -> dict[str, Any]:
        """Where an operation of this identity would live: its path item, else the paths map."""
        if template in self.items:
            return self.item_ref(self.items[template])
        if path in self.by_identity_path:
            return self.item_ref(self.by_identity_path[path])
        return _ref(self.snapshot, "/paths" if self.snapshot["artifact"]["paths_present"] else "")


def _region_ref(side: _Side, record: dict[str, Any], name: str) -> dict[str, Any]:
    pointers = record["regions"][name]["pointers"]
    return _ref(side.snapshot, pointers[0] if pointers else record["provenance"]["pointer"])


def _parameter_ref(side: _Side, record: dict[str, Any], param: dict[str, Any] | None) -> Any:
    if param is None:
        return side.op_ref(record)
    provenance = param["provenance"]
    return _ref(side.snapshot, provenance["pointer"], provenance["declared_pointer"])


def _parameter_findings(
    identity: dict[str, str],
    before: _Side,
    after: _Side,
    one: dict[str, Any],
    two: dict[str, Any],
) -> list[dict[str, Any]]:
    old = {ev.canonical(p["key"]): p for p in one["parameters"]}
    new = {ev.canonical(p["key"]): p for p in two["parameters"]}
    found = []
    for key in sorted(set(old) | set(new)):
        was, now = old.get(key), new.get(key)
        subject = now if now is not None else was
        if subject is None:
            continue
        shared: dict[str, Any] = {
            "identity": identity,
            "parameter": subject["key"],
            "reserved": subject["reserved_header"],
        }
        first = _parameter_ref(before, one, was)
        second = _parameter_ref(after, two, now)
        in_path = subject["in"] == "path"
        if was is None and now is not None:
            if in_path:
                found.append(
                    _finding(
                        "review_required", first, second, reason="path_parameter_changed", **shared
                    )
                )
            elif now["required"] and not now["reserved_header"]:
                found.append(
                    _finding(
                        "newly_required_parameter", first, second, change="added_required", **shared
                    )
                )
            else:
                found.append(
                    _finding("review_required", first, second, reason="parameter_added", **shared)
                )
        elif was is not None and now is None:
            reason = "path_parameter_changed" if in_path else "parameter_removed"
            found.append(_finding("review_required", first, second, reason=reason, **shared))
        elif was is not None and now is not None and was["sha256"] != now["sha256"]:
            if in_path:
                found.append(
                    _finding(
                        "review_required", first, second, reason="path_parameter_changed", **shared
                    )
                )
            elif not was["required"] and now["required"] and not now["reserved_header"]:
                found.append(
                    _finding(
                        "newly_required_parameter",
                        first,
                        second,
                        change="optional_to_required",
                        **shared,
                    )
                )
                if was["sha256_without_required"] != now["sha256_without_required"]:
                    found.append(
                        _finding(
                            "review_required", first, second, reason="parameter_changed", **shared
                        )
                    )
            else:
                found.append(
                    _finding("review_required", first, second, reason="parameter_changed", **shared)
                )
    return found


def _common_findings(
    identity: dict[str, str], before: _Side, after: _Side, one: dict[str, Any], two: dict[str, Any]
) -> list[dict[str, Any]]:
    if not one["regions"] or not two["regions"]:
        return [
            _finding(
                "review_required",
                before.op_ref(one),
                after.op_ref(two),
                reason="incomplete_region",
                identity=identity,
                region="operation",
            )
        ]
    found = []
    for name in REGION_NAMES:
        old, new = one["regions"][name], two["regions"][name]
        if old["status"] == "incomplete" or new["status"] == "incomplete":
            found.append(
                _finding(
                    "review_required",
                    _region_ref(before, one, name),
                    _region_ref(after, two, name),
                    reason="incomplete_region",
                    identity=identity,
                    region=name,
                )
            )
        elif old["sha256"] == new["sha256"]:
            continue
        elif name == "parameters":
            found.extend(_parameter_findings(identity, before, after, one, two))
        else:
            found.append(
                _finding(
                    "review_required",
                    _region_ref(before, one, name),
                    _region_ref(after, two, name),
                    reason="region_changed",
                    identity=identity,
                    region=name,
                )
            )
    return found


def _identity_findings(before: _Side, after: _Side) -> list[dict[str, Any]]:
    found = []
    for key in sorted(set(before.groups) | set(after.groups)):
        identity = {"method": key[1], "path": key[0]}
        old, new = before.groups.get(key, []), after.groups.get(key, [])
        if len(old) > 1 or len(new) > 1:
            for side, records in ((before, old), (after, new)):
                if len(records) < 2:
                    continue
                for record in records:
                    ref = side.op_ref(record)
                    found.append(
                        _finding(
                            "review_required",
                            ref if side is before else None,
                            ref if side is after else None,
                            reason="ambiguous_identity",
                            identity=identity,
                        )
                    )
        elif old and not new:
            record = old[0]
            blocker = after.blocking_item(key[0])
            if blocker is not None:
                found.append(
                    _finding(
                        "review_required",
                        before.op_ref(record),
                        after.item_ref(blocker),
                        reason="incomplete_region",
                        blocked="operation_removed",
                        identity=identity,
                        region="path_item",
                    )
                )
            else:
                found.append(
                    _finding(
                        "operation_removed",
                        before.op_ref(record),
                        after.anchor(record["path_template"], key[0]),
                        identity=identity,
                    )
                )
        elif new and not old:
            record = new[0]
            blocker = before.blocking_item(key[0])
            if blocker is not None:
                found.append(
                    _finding(
                        "review_required",
                        before.item_ref(blocker),
                        after.op_ref(record),
                        reason="incomplete_region",
                        blocked="operation_added",
                        identity=identity,
                        region="path_item",
                    )
                )
            else:
                found.append(
                    _finding(
                        "operation_added",
                        before.anchor(record["path_template"], key[0]),
                        after.op_ref(record),
                        identity=identity,
                    )
                )
        else:
            found.extend(_common_findings(identity, before, after, old[0], new[0]))
    return found


def _path_item_findings(before: _Side, after: _Side) -> list[dict[str, Any]]:
    found = []
    for template in sorted(set(before.items) | set(after.items)):
        old, new = before.items.get(template), after.items.get(template)
        first = before.item_ref(old) if old else None
        second = after.item_ref(new) if new else None
        if any(r is not None and r["status"] == "incomplete" for r in (old, new)):
            found.append(
                _finding(
                    "review_required", first, second, reason="incomplete_region", region="path_item"
                )
            )
        elif (
            before.op_counts.get(template, 0) == 0
            and after.op_counts.get(template, 0) == 0
            and (old and old["sha256"]) != (new and new["sha256"])
        ):
            found.append(
                _finding(
                    "review_required", first, second, reason="region_changed", region="path_item"
                )
            )
    return found


def _document_findings(before: _Side, after: _Side) -> list[dict[str, Any]]:
    found = []
    for at in sorted(set(before.regions) | set(after.regions)):
        old, new = before.regions.get(at), after.regions.get(at)
        first = _ref(before.snapshot, at) if old else None
        second = _ref(after.snapshot, at) if new else None
        if any(r is not None and r["status"] == "incomplete" for r in (old, new)):
            reason = "incomplete_region"
        elif old is None or new is None or old["sha256"] != new["sha256"]:
            reason = "document_region_changed"
        else:
            continue
        found.append(_finding("review_required", first, second, reason=reason, region="document"))
    return found


def _sort_key(finding: dict[str, Any]) -> tuple[Any, ...]:
    identity = finding["identity"]
    return (
        identity is None,
        identity["path"] if identity else "",
        identity["method"] if identity else "",
        finding["kind"],
        finding["reason"] or "",
        finding["region"] or "",
        ev.canonical(finding["parameter"]) if finding["parameter"] else "",
        (finding["before"] or {}).get("pointer", ""),
        (finding["after"] or {}).get("pointer", ""),
        finding["id"],
    )


def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_sha256": snapshot["artifact"]["sha256"],
        "snapshot_id": snapshot["snapshot_id"],
        "status": snapshot["status"],
        "operations": len(snapshot["operations"]),
        "diagnostics_total": snapshot["diagnostics_total"],
    }


def diff(
    before: dict[str, Any], after: dict[str, Any], options: DiffOptions | None = None
) -> dict[str, Any]:
    """Compare two snapshots. Raises `ContractDiffRejected` when they cannot be compared."""
    options = options or DiffOptions()
    if (
        isinstance(before, dict)
        and isinstance(after, dict)
        and before.get("profile") != after.get("profile")
    ):
        raise ContractDiffRejected("profile_mismatch")
    try:
        validate_snapshot(before)
        validate_snapshot(after)
    except SnapshotInvalid:
        raise ContractDiffRejected("invalid_snapshot") from None
    old, new = _Side(before), _Side(after)
    findings = (
        _path_item_findings(old, new) + _identity_findings(old, new) + _document_findings(old, new)
    )
    findings.sort(key=_sort_key)
    kinds = {name: 0 for name in sorted(FINDING_KINDS)}
    reasons = {name: 0 for name in sorted(REVIEW_REASONS)}
    for finding in findings:
        kinds[finding["kind"]] += 1
        if finding["reason"] is not None:
            reasons[finding["reason"]] += 1
    rest = {
        "format": FORMAT,
        "profile": PROFILE,
        "differ": DIFFER,
        "options": options.as_dict(),
        "before": _summary(before),
        "after": _summary(after),
        "findings": findings[: options.max_findings],
        "findings_total": len(findings),
        "selection": {"findings_truncated": len(findings) > options.max_findings},
        "summary": kinds,
        "review_reasons": reasons,
        "limitations": list(LIMITATIONS),
    }
    return {**rest, "id": _identifier(FORMAT, rest)}


# ---- validation -----------------------------------------------------------------------------


class DiffInvalid(ValueError):
    """A change set that is not a well-formed `atlas-contract-diff.v1`."""


def _need(condition: bool, what: str) -> None:
    if not condition:
        raise DiffInvalid(what)


def _check_ref(value: Any) -> None:
    if value is None:
        return
    _need(isinstance(value, dict) and set(value) == {
        "artifact_sha256", "snapshot_id", "pointer", "declared_pointer"
    }, "ref")  # fmt: skip
    _need(isinstance(value["pointer"], str), "ref_pointer")
    _need(value["declared_pointer"] is None or isinstance(value["declared_pointer"], str), "ref")


def _check_finding(finding: Any) -> None:
    keys = {
        "type", "kind", "action", "reason", "change", "blocked_finding", "identity", "region",
        "parameter", "reserved_header", "before", "after", "id",
    }  # fmt: skip
    _need(isinstance(finding, dict) and set(finding) == keys, "finding_keys")
    _need(finding["type"] == "contract_finding", "finding_type")
    _need(
        _identifier(FINDING_FORMAT, {k: v for k, v in finding.items() if k != "id"})
        == finding["id"],
        "finding_id",
    )
    kind, reason, region = finding["kind"], finding["reason"], finding["region"]
    _need(kind in FINDING_KINDS, "kind")
    _need(
        finding["action"] == ("informational" if kind == "operation_added" else "review"), "action"
    )
    _check_ref(finding["before"])
    _check_ref(finding["after"])
    _need(finding["before"] is not None or finding["after"] is not None, "no_reference")
    identity, parameter, blocked = (
        finding["identity"],
        finding["parameter"],
        finding["blocked_finding"],
    )
    _need(
        blocked is None
        or (kind == "review_required" and reason == "incomplete_region" and blocked in BLOCKABLE),
        "blocked",
    )
    _need(region is None or region in FINDING_REGIONS, "region")
    if identity is not None:
        _need(isinstance(identity, dict) and set(identity) == {"method", "path"}, "identity")
    if kind in ("operation_removed", "operation_added"):
        _need(finding["before"] is not None and finding["after"] is not None, "pair")
        _need(
            identity is not None and reason is None and finding["change"] is None,
            "operation_fields",
        )
        _need(
            region is None and parameter is None and finding["reserved_header"] is None,
            "operation_fields",
        )
    elif kind == "newly_required_parameter":
        _need(finding["before"] is not None and finding["after"] is not None, "pair")
        _need(identity is not None and reason is None and region is None, "parameter_fields")
        _need(finding["change"] in CHANGES, "change")
        _need(
            isinstance(parameter, dict) and parameter.get("in") in ("query", "header", "cookie"),
            "parameter_location",
        )
        _need(finding["reserved_header"] is False, "reserved")
    else:
        _need(reason in REVIEW_REASONS and finding["change"] is None, "review_fields")
        if reason in (
            "parameter_added",
            "parameter_removed",
            "parameter_changed",
            "path_parameter_changed",
        ):
            _need(
                identity is not None and isinstance(parameter, dict) and region is None,
                "parameter_review",
            )
            _need(isinstance(finding["reserved_header"], bool), "reserved")
            _need(
                (reason == "path_parameter_changed") == (parameter.get("in") == "path"),
                "path_parameter",
            )
        else:
            _need(parameter is None and finding["reserved_header"] is None, "review_parameter")
        if reason == "ambiguous_identity":
            _need(identity is not None and region is None, "ambiguous")
        elif reason == "document_region_changed":
            _need(identity is None and region == "document", "document")
        elif reason in ("incomplete_region", "region_changed"):
            _need(region is not None, "region_required")
            whole = region in ("path_item", "document") and blocked is None
            _need((identity is None) == whole, "identity_scope")


def validate_diff(change_set: Any) -> None:
    """Raise `DiffInvalid` unless `change_set` is closed, strictly ordered and self-consistent."""
    try:
        _validate_diff(change_set)
    except (KeyError, TypeError, IndexError, AttributeError):
        raise DiffInvalid("shape") from None


def _validate_diff(change_set: Any) -> None:
    keys = {
        "format", "profile", "differ", "options", "before", "after", "findings",
        "findings_total", "selection", "summary", "review_reasons", "limitations", "id",
    }  # fmt: skip
    _need(isinstance(change_set, dict) and set(change_set) == keys, "keys")
    _need(change_set["format"] == FORMAT and change_set["profile"] == PROFILE, "format")
    _need(change_set["differ"] == DIFFER, "differ")
    _need(change_set["limitations"] == list(LIMITATIONS), "limitations")
    options = change_set["options"]
    _need(set(options) == {"max_findings", "ignored_fields"}, "options")
    _need(options["ignored_fields"] == list(IGNORED_FIELDS), "ignored_fields")
    low, high = LIMITS["max_findings"][1:]
    bound = options["max_findings"]
    _need(isinstance(bound, int) and not isinstance(bound, bool) and low <= bound <= high, "bound")
    for side in ("before", "after"):
        _need(set(change_set[side]) == set(_SIDES), "side")
    findings = change_set["findings"]
    for finding in findings:
        _check_finding(finding)
    ordered = [_sort_key(f) for f in findings]
    _need(ordered == sorted(ordered), "order")
    total = change_set["findings_total"]
    _need(len(findings) <= min(total, bound), "findings_total")
    _need(change_set["selection"] == {"findings_truncated": total > bound}, "selection")
    _need(len(findings) == min(total, bound), "findings_count")
    summary, reasons = change_set["summary"], change_set["review_reasons"]
    _need(sorted(summary) == sorted(FINDING_KINDS) and sum(summary.values()) == total, "summary")
    _need(sorted(reasons) == sorted(REVIEW_REASONS), "review_reasons")
    _need(sum(reasons.values()) == summary["review_required"], "review_reasons_total")
    if total == len(findings):
        for name in FINDING_KINDS:
            _need(summary[name] == sum(1 for f in findings if f["kind"] == name), "kind_count")
        for name in REVIEW_REASONS:
            _need(reasons[name] == sum(1 for f in findings if f["reason"] == name), "reason_count")
    rest = {k: v for k, v in change_set.items() if k != "id"}
    _need(_identifier(FORMAT, rest) == change_set["id"], "id")
