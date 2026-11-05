"""ACI-S35: an optional, deterministic ranking overlay over the unchanged baseline ranker.

The baseline is `atlas_aci.query.context.context`, the very function the MCP `graph_query`
context operation runs, called over a read-only lease of the active snapshot with a duck-typed
settings object. The overlay only permutes the bundles the baseline returned: it never adds,
drops or rewrites one. Without `--overlay` the ordering is the baseline's, exactly.

Only active `verified_artifact` events adjust anything: an event is active when every source it
references still matches the queried snapshot's indexed observation AND its snapshot and
generation equal the queried pair. Matching is by bundle id against the live baseline; the
positions and bundle lists a saved record declares are never used for scoring. The sidecar
`atlas-feedback-ranking.v1` lists every adjustment with the ids of the events that support it.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS, DEFAULT_MAX_RESPONSE_BYTES
from atlas_aci.cross_repo import revision_vector as vector
from atlas_aci.enforcement import ToolError
from atlas_aci.feedback import outcomes as fo
from atlas_aci.protocol.graph import request_mode, validate_context
from atlas_aci.query.context import context
from atlas_aci.query.context_limits import LIMITS as CONTEXT_LIMITS
from atlas_aci.query.context_limits import normalize as context_normalize
from atlas_aci.query.diff import Side
from atlas_aci.storage import evidence as ev

RANKING_FORMAT = "atlas-feedback-ranking.v1"
EVALUATION_FORMAT = "atlas-feedback-evaluation.v1"
POLICY_NAME = "atlas-feedback-ranking-policy.v1"
ADJUSTMENT_RULE = "verified_passed_distinct_artifact_selector"
ADJUSTMENT_RULES = (ADJUSTMENT_RULE,)
_FORBIDDEN_WORD = "".join(("ap", "plied"))  # a word the sidecar never uses
PINNED = "baseline_position_0"
MAX_DELTA = 3
PRODUCER = {"name": "atlas-aci feedback-rank", "version": "atlas-feedback-rank.v1"}
POLICY: dict[str, Any] = {
    "format": POLICY_NAME,
    "weights": {"verified_passed": 1, "verified_failed": 0, "unverified_annotation": 0},
    "unit": "distinct_artifact_sha256_and_selector_per_bundle",
    "max_delta_per_bundle": MAX_DELTA,
    "sort_key": [
        "baseline_position_minus_delta",
        "negative_delta",
        "baseline_position",
        "bundle_id",
    ],
    "pinned": PINNED,
}
POLICY_ID = hashlib.sha256(ev.canonical(POLICY).encode("utf-8")).hexdigest()
EXCLUSION_REASONS = ("path_not_indexed", "source_fingerprint_mismatch", "generation_mismatch")
RANK_REJECTIONS = (
    "request_too_large",
    "invalid_request",
    "store_too_large",
    "invalid_store",
    "unsupported_store_version",
    "too_many_events",
    "index_unavailable",
    "source_snapshot_unavailable",
    "index_upgrade_required",
    "baseline_unavailable",
    "invalid_ranking",
    "output_too_large",
)
REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "op",
        "text",
        "repository",
        "path_prefixes",
        "target",
        "direction",
        "relations",
        "confidence",
        "source_policy",
        "max_bytes",
        *CONTEXT_LIMITS,
    }
)
RANKING_KEYS = (
    "format",
    "id",
    "producer",
    "overlay",
    "store_id",
    "snapshot",
    "baseline",
    "ranking",
    "events",
    "annotations",
    "counts",
    "limitations",
)
OVERLAY_KEYS = ("enabled", "policy_name", "policy_id", "policy")
BASELINE_KEYS = ("retrieval_record_id", "ranker", "collection_stop", "bundles")
BASELINE_ENTRY_KEYS = ("position", "bundle_id", "kind")
RANKING_ENTRY_KEYS = ("position", "baseline_position", "bundle_id", "kind", "adjustment")
ADJUSTMENT_KEYS = ("delta", "rule", "supporting_event_ids")
EVENTS_KEYS = ("active", "excluded")
ACTIVE_KEYS = (
    "event_id",
    "outcome",
    "attestation",
    "unit_id",
    "matched_bundle_ids",
    "scored",
)
EXCLUDED_KEYS = ("event_id", "reason")
ANNOTATION_COUNT_KEYS = ("count", "scored")
COUNT_KEYS = ("baseline", "adjusted", "active", "excluded")
EVALUATION_KEYS = (
    "format",
    "id",
    "policy_id",
    "fold",
    "snapshot",
    "store_id",
    "order_basis",
    "counts",
    "held_out",
    "limitations",
)


@dataclass(frozen=True)
class BaselineSettings:
    """The configuration the baseline reads, frozen from the `config.py` constants."""

    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_bound_field_elements: int = DEFAULT_MAX_BOUND_FIELD_ELEMENTS
    context_tokenizer: object | None = None


# ---- the request ---------------------------------------------------------------------------------


def parse_request(raw: bytes, limits: fo.Limits) -> dict[str, Any]:
    """The context request, validated the way the MCP path does; `invalid_request` otherwise."""
    if len(raw) > limits.max_request_bytes:
        raise fo.FeedbackRejected("request_too_large")
    try:
        request = vector.load_strict(raw, numbers=True)
        if not isinstance(request, dict) or not set(request) <= REQUEST_KEYS:
            raise ValueError("request keys")
        if not fo._encodable(request) or "text" not in request:
            raise ValueError("request text")
        if request.get("op") != "context" or request_mode(request) != "structured":
            raise ValueError("request mode")
        context_normalize(request, BaselineSettings())
    except (ToolError, KeyError, TypeError, ValueError):
        raise fo.FeedbackRejected("invalid_request") from None
    return request  # type: ignore[no-any-return]


# ---- the baseline --------------------------------------------------------------------------------


def baseline(reader: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    """The unchanged baseline result (`asyncio.run` inside: never call from a running loop)."""
    try:
        payload = asyncio.run(context(reader, dict(request), BaselineSettings(), structured=True))
        validate_context(payload, "context", DEFAULT_MAX_BOUND_FIELD_ELEMENTS)
        if payload.get("status") != "ok" or payload["data"].get("projection") != "task_context":
            raise ValueError("not a context result")
    except (ToolError, ValueError, KeyError, TypeError):
        raise fo.FeedbackRejected("baseline_unavailable") from None
    return payload  # type: ignore[no-any-return]


# ---- D8: staleness -------------------------------------------------------------------------------


def activity(
    store: Mapping[str, Any], observed: Mapping[str, str], snapshot_id: str, generation_id: str
) -> tuple[list[Mapping[str, Any]], list[dict[str, str]], int]:
    """(active events, excluded events with a reason, active annotation count), in id order."""
    queried = {"snapshot_id": snapshot_id, "generation_id": generation_id}

    def reason(item: Mapping[str, Any]) -> str | None:
        pairs = {(s["path"], s["source_sha256"]) for r in item["results"] for s in r["sources"]}
        if any(path not in observed for path, _ in pairs):
            return "path_not_indexed"
        if any(observed[path] != sha for path, sha in pairs):
            return "source_fingerprint_mismatch"
        if item["snapshot"] != queried:
            return "generation_mismatch"
        return None

    active: list[Mapping[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for event in store["events"]:
        found = reason(event)
        if found is None:
            active.append(event)
        else:
            excluded.append({"event_id": event["id"], "reason": found})
    notes = sum(1 for note in store["annotations"] if reason(note) is None)
    return active, excluded, notes


# ---- D9: scoring and order ----------------------------------------------------------------


def _unit(event: Mapping[str, Any]) -> str:
    return fo.unit_id(event["artifact"]["sha256"], event["claim"]["selector"]["value"])


def adjustments(
    bundle_ids: Sequence[str], active: Sequence[Mapping[str, Any]]
) -> dict[str, tuple[int, list[str]]]:
    """{bundle_id: (delta, supporting event ids)} for the bundles that have a delta."""
    found: dict[str, tuple[int, list[str]]] = {}
    for bundle_id in bundle_ids:
        support = [
            e
            for e in active
            if e["claim"]["outcome"] == "passed"
            and any(r["bundle_id"] == bundle_id for r in e["results"])
        ]
        delta = min(MAX_DELTA, len({_unit(e) for e in support}))
        if delta > 0:
            found[bundle_id] = (delta, sorted(e["id"] for e in support))
    return found


def order(bundle_ids: Sequence[str], deltas: Mapping[str, int]) -> list[str]:
    """Position 0 stays; the rest sort by (position - delta, -delta, position, bundle_id)."""
    rest = sorted(
        list(enumerate(bundle_ids))[1:],
        key=lambda item: (
            item[0] - deltas.get(item[1], 0),
            -deltas.get(item[1], 0),
            item[0],
            item[1],
        ),
    )
    return list(bundle_ids[:1]) + [bundle_id for _, bundle_id in rest]


# ---- the sidecar ---------------------------------------------------------------------------------


def _active_entry(event: Mapping[str, Any], baseline_ids: Sequence[str]) -> dict[str, Any]:
    matched = sorted({r["bundle_id"] for r in event["results"]} & set(baseline_ids))
    outcome = event["claim"]["outcome"]
    return {
        "event_id": event["id"],
        "outcome": outcome,
        "attestation": event["verification"]["attestation"],
        "unit_id": _unit(event),
        "matched_bundle_ids": matched,
        "scored": outcome == "passed" and bool(matched),
    }


def _document(
    *,
    enabled: bool,
    store_id: str | None,
    lease: tuple[str, str],
    payload: Mapping[str, Any],
    active: Sequence[Mapping[str, Any]],
    excluded: Sequence[dict[str, str]],
    notes: int,
) -> dict[str, Any]:
    data = payload["data"]
    bundles = [
        {"position": i, "bundle_id": b["id"], "kind": b["kind"]}
        for i, b in enumerate(data["bundles"])
    ]
    ids = [b["bundle_id"] for b in bundles]
    kinds = {b["bundle_id"]: b["kind"] for b in bundles}
    found = adjustments(ids, active) if enabled else {}
    ranked = order(ids, {k: v[0] for k, v in found.items()})
    ranking = [
        {
            "position": position,
            "baseline_position": ids.index(bundle_id),
            "bundle_id": bundle_id,
            "kind": kinds[bundle_id],
            "adjustment": None
            if bundle_id not in found
            else {
                "delta": found[bundle_id][0],
                "rule": ADJUSTMENT_RULE,
                "supporting_event_ids": found[bundle_id][1],
            },
        }
        for position, bundle_id in enumerate(ranked)
    ]
    rest = {
        "format": RANKING_FORMAT,
        "producer": dict(PRODUCER),
        "overlay": {
            "enabled": enabled,
            "policy_name": POLICY_NAME if enabled else None,
            "policy_id": POLICY_ID if enabled else None,
            "policy": dict(POLICY) if enabled else None,
        },
        "store_id": store_id,
        "snapshot": {"snapshot_id": lease[0], "generation_id": lease[1]},
        "baseline": {
            "retrieval_record_id": data["retrieval_record_id"],
            "ranker": data["ranker"],
            "collection_stop": data["collection_stop"],
            "bundles": bundles,
        },
        "ranking": ranking,
        "events": {
            "active": [_active_entry(e, ids) for e in active] if enabled else [],
            "excluded": list(excluded) if enabled else [],
        },
        "annotations": {"count": notes if enabled else 0, "scored": 0},
        "counts": {
            "baseline": len(bundles),
            "adjusted": len(found),
            "active": len(active) if enabled else 0,
            "excluded": len(excluded) if enabled else 0,
        },
        "limitations": list(fo.LIMITATIONS),
    }
    return {"format": RANKING_FORMAT, "id": fo.digest(RANKING_FORMAT, rest), **rest}


def rank_feedback(
    request_raw: bytes,
    side: Side,
    store_raw: bytes | None,
    enabled: bool,
    limits: fo.Limits,
) -> dict[str, Any]:
    """The `atlas-feedback-ranking.v1` document; without `enabled` the store is never read."""
    if enabled and store_raw is None:
        raise fo.FeedbackUsageError("overlay_without_store")
    if not enabled and store_raw is not None:
        raise fo.FeedbackUsageError("store_without_overlay")
    request = parse_request(request_raw, limits)
    store: dict[str, Any] | None = None
    if enabled and store_raw is not None:
        if len(store_raw) > limits.max_store_bytes:
            raise fo.FeedbackRejected("store_too_large")
        store = fo.load_store(store_raw)
        if len(store["events"]) + len(store["annotations"]) > limits.max_events:
            raise fo.FeedbackRejected("too_many_events")
    with ExitStack() as stack:
        reader = fo.open_snapshot(stack, side)
        observed = fo.indexed_observations(reader)
        lease = (str(reader.snapshot_id), str(reader.generation_id))
        payload = baseline(reader, request)
    active: list[Mapping[str, Any]] = []
    excluded: list[dict[str, str]] = []
    notes = 0
    if store is not None:
        active, excluded, notes = activity(store, observed, *lease)
    document = _document(
        enabled=enabled,
        store_id=store["id"] if store else None,
        lease=lease,
        payload=payload,
        active=active,
        excluded=excluded,
        notes=notes,
    )
    try:
        validate_ranking(document)
    except ValueError:
        raise fo.FeedbackRejected("invalid_ranking") from None
    if len(fo.canonical_bytes(document)) > limits.max_output_bytes:
        raise fo.FeedbackRejected("output_too_large")
    return document


# ---- validation ----------------------------------------------------------------------------------


def validate_ranking(document: Any) -> None:
    """Closed keys, recomputed id, deltas and order; self-consistency of the sidecar only."""
    try:
        _validate_ranking(document)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("invalid feedback ranking") from exc


def _validate_ranking(document: Any) -> None:
    doc = fo._closed(document, RANKING_KEYS)
    fo._check(doc["format"] == RANKING_FORMAT and doc["producer"] == PRODUCER)
    fo._check(doc["limitations"] == list(fo.LIMITATIONS))
    fo._check(doc["id"] == fo.digest(RANKING_FORMAT, {k: v for k, v in doc.items() if k != "id"}))
    overlay = fo._closed(doc["overlay"], OVERLAY_KEYS)
    enabled = overlay["enabled"]
    fo._check(type(enabled) is bool)
    if enabled:
        fo._check(
            overlay["policy_name"] == POLICY_NAME
            and overlay["policy_id"] == POLICY_ID
            and overlay["policy"] == POLICY
        )
        fo._check(ev.hash_value(doc["store_id"]))
    else:
        fo._check(all(overlay[k] is None for k in OVERLAY_KEYS[1:]) and doc["store_id"] is None)
    fo._validate_snapshot(doc["snapshot"])
    base = fo._closed(doc["baseline"], BASELINE_KEYS)
    fo._hex(base["retrieval_record_id"])
    fo._text(base["ranker"], r"[ -~]{1,128}")
    fo._check(base["collection_stop"] is None or isinstance(base["collection_stop"], str))
    entries = base["bundles"]
    fo._check(isinstance(entries, list))
    ids: list[str] = []
    kinds: dict[str, str] = {}
    for position, entry in enumerate(entries):
        row = fo._closed(entry, BASELINE_ENTRY_KEYS)
        fo._check(row["position"] == position and type(row["position"]) is int)
        ids.append(fo._hex(row["bundle_id"]))
        kinds[row["bundle_id"]] = fo._text(row["kind"], r"[ -~]{1,128}")
    fo._check(len(set(ids)) == len(ids))
    events = fo._closed(doc["events"], EVENTS_KEYS)
    active_raw, excluded = events["active"], events["excluded"]
    fo._check(isinstance(active_raw, list) and isinstance(excluded, list))
    active: dict[str, dict[str, Any]] = {}
    for item in active_raw:
        row = fo._closed(item, ACTIVE_KEYS)
        event_id = fo._hex(row["event_id"])
        fo._check(row["outcome"] in fo.OUTCOMES and row["attestation"] == fo.ATTESTATION)
        fo._hex(row["unit_id"])
        matched = row["matched_bundle_ids"]
        fo._check(isinstance(matched, list) and matched == sorted(set(matched)))
        fo._check(all(m in kinds for m in matched))
        fo._check(row["scored"] == (row["outcome"] == "passed" and bool(matched)))
        fo._check(event_id not in active)
        active[event_id] = row
    seen = set(active)
    for item in excluded:
        row = fo._closed(item, EXCLUDED_KEYS)
        fo._check(row["reason"] in EXCLUSION_REASONS)
        fo._check(fo._hex(row["event_id"]) not in seen)
        seen.add(row["event_id"])
    fo._check(enabled or (not active and not excluded))
    notes = fo._closed(doc["annotations"], ANNOTATION_COUNT_KEYS)
    fo._check(type(notes["count"]) is int and notes["count"] >= 0 and notes["scored"] == 0)
    fo._check(enabled or notes["count"] == 0)
    expected: dict[str, tuple[int, list[str]]] = {}
    for bundle_id in ids:
        supporting = [
            e for e in active.values() if e["scored"] and bundle_id in e["matched_bundle_ids"]
        ]
        delta = min(MAX_DELTA, len({e["unit_id"] for e in supporting}))
        if delta > 0:
            expected[bundle_id] = (delta, sorted(e["event_id"] for e in supporting))
    wanted = order(ids, {k: v[0] for k, v in expected.items()})
    ranking = doc["ranking"]
    fo._check(isinstance(ranking, list) and len(ranking) == len(ids))
    adjusted = 0
    for position, entry in enumerate(ranking):
        row = fo._closed(entry, RANKING_ENTRY_KEYS)
        bundle_id = row["bundle_id"]
        fo._check(bundle_id == wanted[position] and row["position"] == position)
        fo._check(
            row["baseline_position"] == ids.index(bundle_id) and row["kind"] == kinds[bundle_id]
        )
        if bundle_id in expected:
            adjustment = fo._closed(row["adjustment"], ADJUSTMENT_KEYS)
            fo._check(
                adjustment["delta"] == expected[bundle_id][0]
                and adjustment["rule"] in ADJUSTMENT_RULES
                and adjustment["supporting_event_ids"] == expected[bundle_id][1]
            )
            adjusted += 1
        else:
            fo._check(row["adjustment"] is None)
    fo._check(enabled or wanted == ids)
    counts = fo._closed(doc["counts"], COUNT_KEYS)
    fo._check(
        counts
        == {
            "baseline": len(ids),
            "adjusted": adjusted,
            "active": len(active),
            "excluded": len(excluded),
        }
    )
    fo._check(_FORBIDDEN_WORD not in ev.canonical(doc))


# ---- D10: held-out evaluation (a report, never a ranking claim) ---------------------------


def evaluate(
    store: Mapping[str, Any],
    observed: Mapping[str, str],
    snapshot_id: str,
    generation_id: str,
    modulus: int = 5,
    residue: int = 0,
) -> dict[str, Any]:
    """Re-order each held-out record's DECLARED bundle order by training events only.

    A record is held out when `int(record_id[:8], 16) % modulus == residue`, so no record feeds
    both sides. The positions are those the record declares (self-consistent, not replayed).
    """
    if type(modulus) is not int or type(residue) is not int or not 0 <= residue < modulus:
        raise ValueError("invalid fold")
    active, excluded, _ = activity(store, observed, snapshot_id, generation_id)

    def held(event: Mapping[str, Any]) -> bool:
        return int(event["retrieval"]["record_id"][:8], 16) % modulus == residue

    training = [e for e in active if not held(e)]
    by_reason = {reason: 0 for reason in EXCLUSION_REASONS}
    for row in excluded:
        by_reason[row["reason"]] += 1
    reports = []
    for event in (e for e in active if held(e)):
        declared = event["retrieval"]["record_bundle_ids"]
        found = adjustments(declared, training)
        ranked = order(declared, {k: v[0] for k, v in found.items()})
        reports.append(
            {
                "event_id": event["id"],
                "record_id": event["retrieval"]["record_id"],
                "results": [
                    {
                        "bundle_id": r["bundle_id"],
                        "baseline_position": declared.index(r["bundle_id"]),
                        "overlay_position": ranked.index(r["bundle_id"]),
                    }
                    for r in event["results"]
                ],
            }
        )
    rest = {
        "format": EVALUATION_FORMAT,
        "policy_id": POLICY_ID,
        "fold": {"keyed_on": "retrieval_record_id", "modulus": modulus, "residue": residue},
        "snapshot": {"snapshot_id": snapshot_id, "generation_id": generation_id},
        "store_id": store["id"],
        "order_basis": "declared_record_order",
        "counts": {
            "events": len(store["events"]),
            "active": len(active),
            "excluded_by_reason": by_reason,
            "held_out": len(reports),
            "training": len(training),
        },
        "held_out": reports,
        "limitations": list(fo.LIMITATIONS),
    }
    return {"format": EVALUATION_FORMAT, "id": fo.digest(EVALUATION_FORMAT, rest), **rest}


def validate_evaluation(
    document: Any,
    store: Mapping[str, Any],
    observed: Mapping[str, str],
    snapshot_id: str,
    generation_id: str,
) -> None:
    """The report equals a fresh recomputation from the store; any other shape is invalid."""
    try:
        fo._closed(document, EVALUATION_KEYS)
        fold = document["fold"]
        fresh = evaluate(
            store, observed, snapshot_id, generation_id, fold["modulus"], fold["residue"]
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("invalid feedback evaluation") from exc
    if fresh != document:
        raise ValueError("invalid feedback evaluation")
