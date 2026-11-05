"""ACI-S34: source-change review signals for explicit ADR mappings, through the S19 snapshot diff.

`rationale_drift` re-resolves the mapping on the before snapshot (which must be the mapped one),
runs `symbol_diff.compare` once and reports, for every mapping, one closed outcome:

* `rationale_review_needed` when the mapped source changed in a way that can matter (signature,
  body, or file content) while the whole decision document is byte-identical on both sides;
* `unknown_target_history` when the diff cannot establish a correspondence;
* `unresolved_mapping` when the endpoint or the document is gone (never rebound);
* `no_finding` otherwise.

The signal is an observation, not a verdict. The document is standalone and content-addressed;
nothing is written to a graph, a repository or an ADR.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import DiffUsageError, Side
from atlas_aci.rationale import adr_mapping as am
from atlas_aci.rationale.adr_mapping import (
    DRIFT_COUNT_KEYS,
    DRIFT_FORMAT,
    IMPORT_STAGE,
    RationaleRejected,
    RationaleUsageError,
)
from atlas_aci.storage import evidence as ev

DRIFT_PRODUCER = {"name": "atlas-aci rationale-drift", "version": "atlas-rationale-drift.v1"}
REVIEW_RULE = "source_signature_body_or_content_changed_and_document_digest_unchanged"
FINDING_TAG = "atlas-rationale-finding.v1:"
SOURCE_STATES = (
    "unchanged",
    "location_only",
    "formatting_only",
    "signature_changed",
    "body_changed",
    "content_changed",
    "removed",
    "unknown",
    "not_compared",
)
REVIEW_STATES = ("signature_changed", "body_changed", "content_changed")
QUIET_STATES = ("unchanged", "location_only", "formatting_only")
DECISION_STATES = ("document_unchanged", "document_changed", "document_missing", "not_compared")
COMPARED_DECISIONS = DECISION_STATES[:3]
OUTCOMES = ("rationale_review_needed", "unknown_target_history", "unresolved_mapping", "no_finding")
FINDING_KINDS = OUTCOMES[:3]
HISTORY_REASONS = (
    "fingerprint_unsupported_kind",
    "extraction_failed_before",
    "extraction_failed_after",
    "definition_occurrence_ambiguous",
    "source_unavailable",
    "declaration_node_not_found",
    "parse_error_in_declaration",
    "uncovered_source_text",
    "unmapped_declaration_child",
    "non_utf8_declaration",
    "inconsistent_evidence",
    "path_partial",
    "comparison_rejected",
    "correspondence_not_confirmed",
    "sibling_ordinal_shift",
)
DRIFT_REASONS = ("target_removed", "document_not_in_snapshot")
STAGES = (IMPORT_STAGE, "comparison")
COMPARISON_STATES = ("compared", "rejected")
DRIFT_REJECTIONS = (
    "mapping_too_large",
    "too_many_mappings",
    "invalid_mapping",
    "unsupported_mapping_version",
    "before_index_unavailable",
    "before_source_snapshot_unavailable",
    "before_index_upgrade_required",
    "after_index_unavailable",
    "after_source_snapshot_unavailable",
    "after_index_upgrade_required",
    "mapping_snapshot_mismatch",
    "snapshot_changed",
    "comparison_failed",
    "output_too_large",
    "invalid_overlay",
    "invalid_drift",
)
CATEGORY_STATES = {
    "signature": "signature_changed",
    "body": "body_changed",
    "format": "formatting_only",
    "location": "location_only",
}

DRIFT_KEYS = (
    "format",
    "id",
    "producer",
    "mapping_digest",
    "overlay_id",
    "review_rule",
    "before",
    "after",
    "comparison",
    "counts",
    "results",
    "findings",
    "limitations",
)
COMPARISON_KEYS = ("state", "change_set_id", "reason")
RESULT_KEYS = (
    "mapping_id",
    "decision_id",
    "stage",
    "target_entity_id",
    "target_class",
    "document_path",
    "source_state",
    "decision_state",
    "outcome",
    "reason",
    "detail",
    "change_record_id",
    "candidate_ids",
    "target_source_sha256_before",
    "target_source_sha256_after",
    "document_sha256_before",
    "document_sha256_after",
)
FINDING_KEYS = (
    "id",
    "kind",
    "mapping_id",
    "decision_id",
    "target_entity_id",
    "document_path",
    "reason",
    "detail",
    "change_record_id",
    "candidate_ids",
)
_SHA_KEYS = (
    "target_source_sha256_before",
    "target_source_sha256_after",
    "document_sha256_before",
    "document_sha256_after",
)


# ---- the evaluation ------------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """What the comparison established, plus the two held snapshots."""

    rejection: str | None
    records: Mapping[str, Mapping[str, Any]]
    candidates: Mapping[str, Sequence[str]]
    partial: frozenset[str]
    before: am.Snapshot
    after: am.Snapshot

    @property
    def compared(self) -> bool:
        return self.rejection is None


def _observed(facts: am.Snapshot, path: str) -> tuple[str | None, str | None]:
    """(state, digest) of the observation at `path` in any state; (None, None) when absent."""
    record = facts.observations().get(path)
    if record is None:
        return None, None
    digest = record.get("observed_source_sha256")
    if not ev.hash_value(digest):
        raise RationaleRejected(facts.prefix + "index_unavailable")
    return str(record.get("state")), str(digest)


def _decision_state(declared: str, observed: str | None) -> str:
    if observed is None:
        return "document_missing"
    return "document_unchanged" if observed == declared else "document_changed"


def _result(record: Mapping[str, Any], **fields: Any) -> dict[str, Any]:
    target = record["target"]
    result: dict[str, Any] = {
        "mapping_id": record["mapping_id"],
        "decision_id": record["decision_id"],
        "stage": "comparison",
        "target_entity_id": target["entity_id"],
        "target_class": target["target_class"],
        "document_path": record["document"]["path"],
        "source_state": "not_compared",
        "decision_state": "not_compared",
        "outcome": "no_finding",
        "reason": None,
        "detail": None,
        "change_record_id": None,
        "candidate_ids": [],
        "target_source_sha256_before": None,
        "target_source_sha256_after": None,
        "document_sha256_before": None,
        "document_sha256_after": None,
    }
    result.update(fields)
    return result


def import_result(record: Mapping[str, Any]) -> dict[str, Any]:
    """E0: a mapping the import could not resolve (it is never compared)."""
    target = record["target"]
    return {
        "mapping_id": record["mapping_id"],
        "decision_id": record["decision_id"],
        "stage": IMPORT_STAGE,
        "target_entity_id": target["entity_id"],
        "target_class": None,
        "document_path": record["document"]["path"],
        "source_state": "not_compared",
        "decision_state": "not_compared",
        "outcome": "unresolved_mapping",
        "reason": record["reason"],
        "detail": None,
        "change_record_id": None,
        "candidate_ids": [],
        "target_source_sha256_before": None,
        "target_source_sha256_after": None,
        "document_sha256_before": None,
        "document_sha256_after": None,
    }


def _file_state(observation: tuple[str | None, str | None], entity_present: bool) -> str:
    """How a file target stands on the after side: removed, failed, partial, unconfirmed, ok."""
    state, _ = observation
    if state is None and not entity_present:
        return "removed"
    if state == "failed":
        return "failed"
    if state is not None and state != "indexed":
        return "partial"
    if (state is None) != (not entity_present):
        return "unconfirmed"
    return "present"


def _symbol_state(view: View, entity_id: str) -> str:
    """Without an S19 record: is the entity on the after side with exactly one definition?"""
    row = view.after.entity(entity_id)
    if row is None or view.after.definition(row) is None:
        return "unconfirmed"
    return "present"


def evaluate(view: View, record: Mapping[str, Any]) -> dict[str, Any]:
    """One resolved mapping to its result (the first matching row of the precedence table wins)."""
    target = record["target"]
    entity_id, path = target["entity_id"], target["path"]
    is_file = target["target_class"] == "file"
    if not view.compared:
        return _result(
            record,
            source_state="unknown",
            outcome="unknown_target_history",
            reason="comparison_rejected",
            detail=view.rejection,
        )
    change = None if is_file else view.records.get(entity_id)
    declared = record["document"]["sha256"]
    _, document_after = _observed(view.after, record["document"]["path"])
    decision = _decision_state(declared, document_after)
    observation = _observed(view.after, path)
    target_after = observation[1]
    common: dict[str, Any] = {
        "decision_state": decision,
        "change_record_id": None if change is None else change["id"],
        "candidate_ids": [] if is_file else sorted(view.candidates.get(entity_id, [])),
        "target_source_sha256_before": target["source_sha256"],
        "target_source_sha256_after": target_after,
        "document_sha256_before": declared,
        "document_sha256_after": document_after,
    }

    def unknown(reason: str) -> dict[str, Any]:
        return _result(
            record, source_state="unknown", outcome="unknown_target_history", reason=reason,
            **common,
        )  # fmt: skip

    file_state = ""
    if is_file:
        file_state = _file_state(observation, view.after.entity(entity_id) is not None)
        if file_state == "failed":
            return unknown("extraction_failed_after")
    elif change is not None and change["category"] == "unknown":
        if change["reason"] not in HISTORY_REASONS:
            raise RationaleRejected("comparison_failed")
        return unknown(str(change["reason"]))
    if path in view.partial:
        return unknown("path_partial")
    if not is_file:
        before_row = view.before.entity(entity_id)
        if before_row is not None:
            before_ids = view.before.siblings(before_row)
            after_ids = view.after.siblings(before_row)
            if max(len(before_ids), len(after_ids)) > 1 and before_ids != after_ids:
                return unknown("sibling_ordinal_shift")
    removed = file_state == "removed" if is_file else (change or {}).get("category") == "removed"
    if removed:
        return _result(
            record, source_state="removed", outcome="unresolved_mapping", reason="target_removed",
            **common,
        )  # fmt: skip
    if is_file:
        if file_state != "present":
            return unknown("correspondence_not_confirmed")
        source = "unchanged" if observation[1] == target["source_sha256"] else "content_changed"
    elif change is None:
        if _symbol_state(view, entity_id) != "present":
            return unknown("correspondence_not_confirmed")
        source = "unchanged"
    else:
        if change["category"] not in CATEGORY_STATES:
            return unknown("correspondence_not_confirmed")
        source = CATEGORY_STATES[change["category"]]
    if decision == "document_missing":
        return _result(
            record, source_state=source, outcome="unresolved_mapping",
            reason="document_not_in_snapshot", **common,
        )  # fmt: skip
    outcome = (
        "rationale_review_needed"
        if source in REVIEW_STATES and decision == "document_unchanged"
        else "no_finding"
    )
    return _result(record, source_state=source, outcome=outcome, **common)


# ---- the document --------------------------------------------------------------------------------


def _order(item: Mapping[str, Any]) -> tuple[str, str]:
    return (item["decision_id"], item["mapping_id"])


def finding(result: Mapping[str, Any]) -> dict[str, Any]:
    """The finding projection of one result (every outcome other than `no_finding`)."""
    body = {
        "kind": result["outcome"],
        "mapping_id": result["mapping_id"],
        "decision_id": result["decision_id"],
        "target_entity_id": result["target_entity_id"],
        "document_path": result["document_path"],
        "reason": result["reason"],
        "detail": result["detail"],
        "change_record_id": result["change_record_id"],
        "candidate_ids": list(result["candidate_ids"]),
    }
    return {"id": am.sha_text(FINDING_TAG + ev.canonical(body)), **body}


def _counts(results: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    def outcome(name: str) -> int:
        return sum(1 for item in results if item["outcome"] == name)

    imported = sum(1 for item in results if item["stage"] == IMPORT_STAGE)
    return {
        "mappings": len(results),
        "resolved": len(results) - imported,
        "unresolved_at_import": imported,
        "rationale_review_needed": outcome("rationale_review_needed"),
        "unknown_target_history": outcome("unknown_target_history"),
        "unresolved_mapping": outcome("unresolved_mapping"),
        "no_finding": outcome("no_finding"),
    }


def drift_identity(document: Mapping[str, Any]) -> str:
    rest = {key: value for key, value in document.items() if key != "id"}
    return am.sha_text(DRIFT_FORMAT + ":" + ev.canonical(rest))


def _assemble(
    parsed: am.ParsedMapping,
    overlay_id: str,
    old: Any,
    new: Any,
    comparison: Mapping[str, Any],
    results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(results, key=_order)
    rest = {
        "format": DRIFT_FORMAT,
        "producer": dict(DRIFT_PRODUCER),
        "mapping_digest": parsed.digest,
        "overlay_id": overlay_id,
        "review_rule": REVIEW_RULE,
        "before": {"snapshot_id": old.snapshot_id, "generation_id": old.generation_id},
        "after": {"snapshot_id": new.snapshot_id, "generation_id": new.generation_id},
        "comparison": dict(comparison),
        "counts": _counts(ordered),
        "results": ordered,
        "findings": [finding(item) for item in ordered if item["outcome"] != "no_finding"],
        "limitations": list(am.LIMITATIONS),
    }
    return {"id": am.sha_text(DRIFT_FORMAT + ":" + ev.canonical(rest)), **rest}


# ---- the comparison and its race checks ---------------------------------------------------------


def _held(reader: Any) -> dict[str, str]:
    return {"snapshot_id": reader.snapshot_id, "generation_id": reader.generation_id}


def _still_held(before: Side, after: Side, old: Any, new: Any) -> bool:
    """A fresh lease of each side shows the generation this run holds."""
    try:
        with ExitStack() as fresh:
            for side, reader in ((before, old), (after, new)):
                leased = symbol_diff._lease(fresh, symbol_diff._open(fresh, side))
                if _held(leased) != _held(reader):
                    return False
    except Exception:
        return False
    return True


def _compare(before: Side, after: Side, old: Any, new: Any) -> tuple[dict[str, Any] | None, str]:
    """(change set, "") or (None, the S19 rejection); a re-index meanwhile is `snapshot_changed`."""
    try:
        change_set = symbol_diff.compare(before, after)
    except symbol_diff.DiffRejected as exc:
        if not _still_held(before, after, old, new):
            raise RationaleRejected("snapshot_changed") from None
        return None, exc.reason
    except DiffUsageError:
        raise RationaleUsageError("graph_namespace") from None
    except Exception:
        raise RationaleRejected("comparison_failed") from None
    if change_set["before"] != _held(old) or change_set["after"] != _held(new):
        raise RationaleRejected("snapshot_changed")
    return change_set, ""


def _distinct(before: Side, after: Side) -> None:
    """Both sides must be distinct graph namespaces (checked before any lease is taken)."""
    with ExitStack() as probe:
        try:
            first = symbol_diff._open(probe, before)
            second = symbol_diff._open(probe, after)
        except DiffUsageError:
            raise RationaleUsageError("graph_namespace") from None
        if first.atlas_dir == second.atlas_dir:
            raise RationaleUsageError("same_namespace")


def rationale_drift(
    raw: bytes, before: Side, after: Side, limits: am.Limits | None = None
) -> dict[str, Any]:
    """The drift document of one mapping between the mapped (before) snapshot and `after`."""
    limits = am.normalize_limits() if limits is None else limits
    parsed = am.parse_mapping(raw, limits)
    _distinct(before, after)
    with ExitStack() as stack:
        old = am.open_mapped(stack, before, parsed.snapshot_id, "before_")
        new = am.open_side(stack, after, "after_")
        old_facts, new_facts = am.Snapshot(old, "before_"), am.Snapshot(new, "after_")
        resolved, unresolved = am.resolve(old, parsed, old_facts)
        overlay = am.sealed_overlay(old, parsed, resolved, unresolved)
        change_set, reason = _compare(before, after, old, new)
        comparison = {
            "state": "rejected" if change_set is None else "compared",
            "change_set_id": None if change_set is None else change_set["id"],
            "reason": reason or None,
        }
        view = View(
            rejection=reason or None,
            records={}
            if change_set is None
            else {r["entity_id"]: r for r in change_set["changes"]},
            candidates=_candidate_ids(change_set),
            partial=frozenset([] if change_set is None else change_set["partial_paths"]),
            before=old_facts,
            after=new_facts,
        )
        results = [import_result(item) for item in unresolved]
        results += [evaluate(view, item) for item in resolved]
        document = _assemble(parsed, overlay["id"], old, new, comparison, results)
    validate_drift(document)
    if len(am.canonical_bytes(document)) > limits.max_output_bytes:
        raise RationaleRejected("output_too_large")
    return document


def _records(change_set: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    return {} if change_set is None else {r["entity_id"]: r for r in change_set["changes"]}


def _candidate_ids(change_set: Mapping[str, Any] | None) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for item in [] if change_set is None else change_set["candidates"]:
        found.setdefault(item["before_entity_id"], []).append(item["id"])
    return found


# ---- validation ----------------------------------------------------------------------------------


def _legal(result: Mapping[str, Any], comparison: Mapping[str, Any]) -> bool:
    """The explicit legality matrix of one result (outcome x source x decision x reason)."""
    outcome, source, decision = result["outcome"], result["source_state"], result["decision_state"]
    reason, detail = result["reason"], result["detail"]
    if result["stage"] == IMPORT_STAGE:
        return (
            outcome == "unresolved_mapping"
            and source == "not_compared"
            and decision == "not_compared"
            and reason in am.IMPORT_REASONS
            and detail is None
            and result["target_class"] is None
        )
    if result["target_class"] not in am.TARGET_CLASSES:
        return False
    if comparison["state"] == "rejected":
        return (
            outcome == "unknown_target_history"
            and source == "unknown"
            and decision == "not_compared"
            and reason == "comparison_rejected"
            and detail == comparison["reason"]
        )
    if outcome == "rationale_review_needed":
        return (
            source in REVIEW_STATES
            and decision == "document_unchanged"
            and reason is None
            and detail is None
        )
    if outcome == "no_finding":
        quiet = source in QUIET_STATES and decision in ("document_unchanged", "document_changed")
        edited = source in REVIEW_STATES and decision == "document_changed"
        return (quiet or edited) and reason is None and detail is None
    if outcome == "unknown_target_history":
        return (
            source == "unknown"
            and reason in HISTORY_REASONS
            and reason != "comparison_rejected"
            and detail is None
            and decision in COMPARED_DECISIONS
        )
    if outcome == "unresolved_mapping" and detail is None:
        if reason == "target_removed":
            return source == "removed" and decision in COMPARED_DECISIONS
        if reason == "document_not_in_snapshot":
            return source in (*QUIET_STATES, *REVIEW_STATES) and decision == "document_missing"
    return False


def _check_shas(result: Mapping[str, Any]) -> bool:
    values = [result[key] for key in _SHA_KEYS]
    if not all(value is None or ev.hash_value(value) for value in values):
        return False
    before_target, after_target, before_doc, after_doc = values
    if result["decision_state"] == "not_compared":
        return all(value is None for value in values)
    if before_target is None or before_doc is None:
        return False
    decision = result["decision_state"]
    if decision == "document_missing" and after_doc is not None:
        return False
    if decision == "document_unchanged" and after_doc != before_doc:
        return False
    if decision == "document_changed" and (after_doc is None or after_doc == before_doc):
        return False
    if result["source_state"] == "content_changed":
        return after_target is not None and after_target != before_target
    return True


def _check_result(item: Any, comparison: Mapping[str, Any]) -> None:
    bad = "invalid_drift"
    result = am.keys_closed(item, RESULT_KEYS, bad)
    am.require(am.is_label(result["mapping_id"]) and am.is_label(result["decision_id"]), bad)
    am.require(result["stage"] in STAGES, bad)
    am.require(ev.hash_value(result["target_entity_id"]), bad)
    am.require(result["target_class"] is None or result["target_class"] in am.TARGET_CLASSES, bad)
    am.require(ev.relative_path(result["document_path"]), bad)
    am.require(result["source_state"] in SOURCE_STATES, bad)
    am.require(result["decision_state"] in DECISION_STATES, bad)
    am.require(result["outcome"] in OUTCOMES, bad)
    am.require(result["reason"] is None or isinstance(result["reason"], str), bad)
    am.require(result["detail"] is None or isinstance(result["detail"], str), bad)
    change_id = result["change_record_id"]
    am.require(change_id is None or ev.hash_value(change_id), bad)
    ids = result["candidate_ids"]
    am.require(isinstance(ids, list) and all(ev.hash_value(value) for value in ids), bad)
    am.require(ids == sorted(set(ids)), bad)
    am.require(_legal(result, comparison), bad)
    am.require(_check_shas(result), bad)


def validate_drift(document: Any) -> None:
    """Raise `RationaleRejected("invalid_drift")` unless `document` is exactly what we build."""
    bad = "invalid_drift"
    top = am.keys_closed(document, DRIFT_KEYS, bad)
    am.no_relation_keys(top, bad)
    am.require(top["format"] == DRIFT_FORMAT and top["producer"] == DRIFT_PRODUCER, bad)
    am.require(top["review_rule"] == REVIEW_RULE, bad)
    am.require(ev.hash_value(top["mapping_digest"]) and ev.hash_value(top["overlay_id"]), bad)
    for key in ("before", "after"):
        block = am.keys_closed(top[key], am.SNAPSHOT_KEYS, bad)
        am.require(all(ev.hash_value(value) for value in block.values()), bad)
    comparison = am.keys_closed(top["comparison"], COMPARISON_KEYS, bad)
    if comparison["state"] == "compared":
        am.require(ev.hash_value(comparison["change_set_id"]) and comparison["reason"] is None, bad)
    else:
        am.require(comparison["state"] == "rejected", bad)
        am.require(comparison["change_set_id"] is None, bad)
        am.require(comparison["reason"] in symbol_diff.REJECTIONS, bad)
    results, findings = top["results"], top["findings"]
    am.require(isinstance(results, list) and isinstance(findings, list), bad)
    for item in results:
        _check_result(item, comparison)
    am.require([_order(i) for i in results] == sorted(_order(i) for i in results), bad)
    labels = [item["mapping_id"] for item in results]
    am.require(len(set(labels)) == len(labels), bad)
    am.require(
        top["findings"] == [finding(i) for i in results if i["outcome"] != "no_finding"], bad
    )
    counts = am.keys_closed(top["counts"], DRIFT_COUNT_KEYS, bad)
    am.require(counts == _counts(results), bad)
    am.require(all(item["kind"] in FINDING_KINDS for item in findings), bad)
    am.require(top["limitations"] == list(am.LIMITATIONS), bad)
    am.require(drift_identity(top) == top["id"], bad)


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return am.canonical_bytes(document)
