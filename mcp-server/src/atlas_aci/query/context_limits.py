"""Closed context request limits, separate collection and emitted-result budgets."""

from __future__ import annotations

from atlas_aci.enforcement import ToolError
from atlas_aci.query.retrieval import normalized as retrieval_normalized
from atlas_aci.query.retrieval import utf8_size
from atlas_aci.storage import evidence as ev

LIMITS = {
    "max_candidates": (32, 1, 100),
    "max_seeds": (8, 0, 8),
    "max_bundles": (128, 1, 128),
    "max_collection_bytes": (8 * 1024 * 1024, 0, 8 * 1024 * 1024),
    "max_examined_edges": (256, 0, 256),
    "max_depth": (2, 0, 32),
    "max_vm_operations": (1_000_000, 0, 10_000_000),
    "deadline_ms": (2000, 0, 2000),
    "max_inline_bytes": (8192, 0, 8192),
}


def normalize(arguments, config):
    effective = {}
    for name, (default, low, high) in LIMITS.items():
        value = arguments.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise ToolError("INVALID_INPUT", "Invalid finite context limit.")
        effective[name] = value
    if effective["max_collection_bytes"] == 0:
        raise ToolError("INVALID_BUDGET", "Collection cannot contain context metadata.")
    text = retrieval_normalized(
        arguments["text"], arguments.get("repository"), arguments.get("path_prefixes")
    )
    target = arguments.get("target")
    if target is not None and (
        not isinstance(target, dict)
        or len(target) != 1
        or not (
            (set(target) == {"entity_id"} and ev.hash_value(target["entity_id"]))
            or (
                set(target) == {"name"}
                and isinstance(target["name"], str)
                and 1 <= utf8_size(target["name"]) <= 4096
            )
        )
    ):
        raise ToolError("INVALID_INPUT", "Invalid context target selector.")
    direction = arguments.get("direction", "outgoing")
    relations = arguments.get("relations", ["call"])
    confidence = arguments.get("confidence", ["EXTRACTED", "INFERRED"])
    if (
        direction not in {"outgoing", "incoming"}
        or (
            not isinstance(relations, list)
            or not relations
            or any(not isinstance(v, str) for v in relations)
            or len(set(relations)) != len(relations)
            or not set(relations) <= ev.RELATIONS
        )
        or (
            not isinstance(confidence, list)
            or not confidence
            or any(not isinstance(v, str) for v in confidence)
            or len(set(confidence)) != len(confidence)
            or not set(confidence) <= {"EXTRACTED", "INFERRED"}
        )
    ):
        raise ToolError("INVALID_INPUT", "Invalid context relation constraints.")
    policy = arguments.get("source_policy", "inline_if_complete")
    continuation = arguments.get("continuation", False)
    budget = arguments.get("max_bytes", config.max_response_bytes)
    if policy not in {"references", "inline_if_complete"} or type(continuation) is not bool:
        raise ToolError("INVALID_INPUT", "Invalid context presentation policy.")
    if type(budget) is not int or not 0 <= budget <= config.max_response_bytes:
        raise ToolError("INVALID_BUDGET", "Invalid context result budget.")
    if type(config.max_bound_field_elements) is not int or config.max_bound_field_elements < 0:
        raise ToolError("INVALID_BOUNDS", "Invalid operator element limit.")
    return {
        "text": text["text"],
        "repository": text["repository"],
        "path_prefixes": text["path_prefixes"],
        "target": target,
        "direction": direction,
        "relations": sorted(relations),
        "confidence": sorted(confidence),
        "source_policy": policy,
        "continuation": continuation,
        "max_bytes": budget,
        **effective,
    }


def canonical_request(query):
    """Lossless set-membership maps avoid implying capped request constraints."""
    return {
        **{k: v for k, v in query.items() if k not in {"path_prefixes", "relations", "confidence"}},
        "constraint_representation": "set_membership_maps",
        **{
            key: {value: True for value in query[key]}
            for key in ("path_prefixes", "relations", "confidence")
        },
    }
