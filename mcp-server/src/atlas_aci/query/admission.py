"""Whole-record admission against actual worst-case negotiated MCP framing."""

from __future__ import annotations

import copy

from atlas_aci.protocol.graph import envelope
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query.traversal import LIMITS
from atlas_aci.storage import evidence as ev


def lists(value):
    if isinstance(value, list):
        yield value
        for child in value:
            yield from lists(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from lists(child)


def presentation(data, element_limit):
    """Candidate enumeration is a labelled prefix, never a uniqueness oracle."""
    data = copy.deepcopy(data)
    for key in ("seed", "target"):
        selection = data.get(key)
        if selection is not None:
            original = selection["candidates"]
            kept = []
            for candidate in original:
                if len(kept) >= element_limit or any(
                    len(items) > element_limit for items in lists(candidate)
                ):
                    break  # Prefix only; never clip an identity's owner chain.
                kept.append(candidate)
            selection["candidates"] = kept
            if len(kept) != len(original):
                selection["candidates_complete"] = False
    return data


def framed(data, operation, generation_id, snapshot_id, element_limit, evidence_status):
    payload = envelope(
        data,
        operation=operation,
        generation_id=generation_id,
        snapshot_id=snapshot_id,
        evidence_status=evidence_status,
    )
    payload["bounds"]["element_limit"] = element_limit
    # These fields describe admission, not an unknown whole-graph total.
    payload["bounds"]["truncated"] = data["outcome"] == "partial"
    return payload


def reserve(data):
    """Reserve final bounded counters and stop/frontier fields BEFORE proof admission."""
    result = copy.deepcopy(data)
    # Upper bounds from fixed one-row dispatch: two endpoints*two queries plus
    # at most four PK hydrations per examined edge, and relation EOF per node.
    # Reserve digits conservatively without charging fictitious query work.
    result["work"] = {name: 999999999 for name in result["work"]}
    result["frontier"] = {"commitment": "f" * 64, "queued_nodes": 1000, "depth": 32}
    result["outcome"] = "disambiguation"
    result["stop_reason"] = "examined_edge_limit"
    return result


class Admission:
    def __init__(
        self,
        *,
        operation,
        budget,
        element_limit,
        generation_id,
        snapshot_id,
        evidence_status="unknown",
    ):
        self.operation = operation
        self.budget = budget
        self.element_limit = element_limit
        self.generation_id = generation_id
        self.snapshot_id = snapshot_id
        self.evidence_status = evidence_status
        self.reason = "byte_limit"

    def encode(self, data):
        return framed(
            data,
            self.operation,
            self.generation_id,
            self.snapshot_id,
            self.element_limit,
            self.evidence_status,
        )

    def size(self, data):
        # Dual text+structured is the conservative common ceiling for old text
        # sessions too. This is actual SDK escaping/flags, not len(str(payload)).
        payload = self.encode(data)
        payload["evidence_status"] = "unavailable"
        return len(result_bytes(sdk_result(payload, error=False, structured=True)))

    def project(self, data):
        result = presentation(data, self.element_limit)
        while self.size(reserve(result)) > self.budget:
            selections = [result.get(key) for key in ("target", "seed")]
            selected = next((s for s in selections if s and s["candidates"]), None)
            if selected is None:
                break
            selected["candidates"].pop()
            selected["candidates_complete"] = False
        return result

    def __call__(self, data):
        projected = self.project(data)
        records = {key: projected[key] for key in ("nodes", "edges", "path") if key in projected}
        if any(len(items) > self.element_limit for items in lists(records)):
            self.reason = "element_limit"
            return False
        self.reason = "byte_limit"
        return self.size(reserve(projected)) <= self.budget

    def finish(self, data):
        result = self.project(data)
        if self.size(result) > self.budget:
            raise ValueError("Traversal metadata admission invariant")
        return self.encode(result)


def minimum_budget():
    """Measured common dual-framing metadata minimum, independent of user names."""
    selection = {
        "selector_kind": "entity_id",
        "state": "unresolved",
        "candidates": [],
        "candidates_complete": False,
        "entity_id": "f" * 64,
    }
    counts = {
        name: 0
        for name in (
            "examined_edges",
            "edge_queries",
            "edge_rows",
            "confirmed_edge_eof",
            "interrupted_edge_steps",
            "endpoint_queries",
            "endpoint_rows",
            "hydration_queries",
            "hydration_rows",
            "sql_queries",
            "interrupted_sql_queries",
            "visited_nodes",
        )
    }
    common = {
        "scope": "current_published_evidence",
        "outcome": "partial",
        "stop_reason": "byte_limit",
        "effective_limits": {name: upper for name, (_, _, upper) in LIMITS.items()},
        "seed": selection,
        "direction": "outgoing",
        "relations": sorted(ev.RELATIONS),
        "confidence": ["EXTRACTED", "INFERRED"],
        "work": counts,
        "frontier": {"commitment": "f" * 64, "queued_nodes": 0, "depth": 0},
        "unknown_relations_traversed": False,
    }
    sizes = []
    for operation in ("neighborhood", "path"):
        data = {
            **common,
            **(
                {"nodes": [], "edges": []}
                if operation == "neighborhood"
                else {"target": selection, "path": None}
            ),
        }
        admission = Admission(
            operation=operation,
            budget=0,
            element_limit=10000,
            generation_id="f" * 64,
            snapshot_id="f" * 64,
        )
        sizes.append(admission.size(reserve(data)))
    return max(sizes)


MINIMUM_BUDGET = minimum_budget()
