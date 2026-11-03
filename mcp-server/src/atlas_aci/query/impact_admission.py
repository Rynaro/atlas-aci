"""ACI-S20 whole-atom prefix admission measured on the real negotiated SDK framing.

Collection finishes before presentation. `impacted` atoms then `boundaries` atoms
form ONE ranked sequence; exactly one strict prefix is admitted. The first atom
that does not fit stops all later admission (no skipping, no reordering). Every
candidate is sized with worst-case reserved selection/counter encodings so the
size is monotone in the prefix length and the final bytes can only be smaller.
"""

from __future__ import annotations

import copy
from typing import Any

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.graph import envelope
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query import impact_limits as lim
from atlas_aci.query.admission import lists

BIG = 999_999_999
HEX = "f" * 64


def evidence_rank(*statuses: str) -> str:
    order = ("available", "partial", "unknown", "unavailable")
    return max(statuses, key=order.index)


def assemble(base: dict[str, Any], impacted, boundaries, reason) -> dict[str, Any]:
    """Final `data` for one prefix. `base` holds everything that is selection-independent."""
    data = {
        **{k: base[k] for k in base if k not in {"coverage"}},
        "impacted": list(impacted),
        "impacted_returned": len(impacted),
        "coverage": {
            **base["coverage"],
            "boundaries": list(boundaries),
            "boundaries_returned": len(boundaries),
        },
        "selection": {"truncated": reason is not None, "reason": reason},
    }
    data["impact_id"] = lim.impact_id(data)
    return data


def reserved(data: dict[str, Any]) -> dict[str, Any]:
    """Worst-case encodings of every selection-dependent field (never smaller than final)."""
    result = copy.deepcopy(data)
    result["impacted_returned"] = BIG
    result["coverage"]["boundaries_returned"] = BIG
    # `false` is the longest boolean encoding and "element_limit" the longest reason.
    result["selection"] = {"truncated": False, "reason": "element_limit"}
    result["impact_id"] = HEX
    return result


def frame(data, *, element_limit, generation_id, snapshot_id, evidence_status, final=True):
    payload = envelope(
        data,
        operation="impact",
        generation_id=generation_id,
        snapshot_id=snapshot_id,
        evidence_status=evidence_status,
    )
    returned = (data["impacted_returned"], data["coverage"]["boundaries_returned"])
    totals = (data["impacted_total"], data["coverage"]["boundaries_total"])
    work_limited = "work_limit" in data["coverage"]["reasons"]
    payload["bounds"] = {
        "element_limit": element_limit,
        "truncated": (data["selection"]["truncated"] or work_limited) if final else False,
        "lists": {
            name: {
                "total_count": total,
                "returned_count": count,
                "truncated": count < total if final else False,
            }
            for name, total, count in zip(("impacted", "boundaries"), totals, returned, strict=True)
        },
    }
    return payload


def size(data, *, element_limit, generation_id, snapshot_id) -> int:
    """Dual text+structured framing with the longest evidence_status: a common ceiling."""
    payload = frame(
        reserved(data),
        element_limit=element_limit,
        generation_id=generation_id,
        snapshot_id=snapshot_id,
        evidence_status="unavailable",
        final=False,
    )
    return len(result_bytes(sdk_result(payload, error=False, structured=True)))


def prefix(base, atoms, *, budget, element_limit, generation_id, snapshot_id):
    """Return (impacted, boundaries, reason) for the longest admissible strict prefix."""

    def build(count):
        head = atoms[:count]
        return assemble(
            base,
            [a for kind, a in head if kind == "impacted"],
            [a for kind, a in head if kind == "boundaries"],
            None,
        )

    def fits(count):
        return (
            size(
                build(count),
                element_limit=element_limit,
                generation_id=generation_id,
                snapshot_id=snapshot_id,
            )
            <= budget
        )

    if not fits(0):
        raise ToolError("INVALID_BUDGET", "Impact metadata cannot fit the result budget.")
    # Element limits first: a monotone upper bound on the admissible prefix.
    limit, seen = 0, {"impacted": 0, "boundaries": 0}
    for kind, atom in atoms:
        if seen[kind] + 1 > element_limit or any(
            len(items) > element_limit for items in lists(atom)
        ):
            break
        seen[kind] += 1
        limit += 1
    low, high = 0, limit  # invariant: fits(low); answer in [low, high]
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    reason = None
    if low < len(atoms):
        reason = "element_limit" if low == limit else "byte_limit"
    chosen = atoms[:low]
    return (
        [a for kind, a in chosen if kind == "impacted"],
        [a for kind, a in chosen if kind == "boundaries"],
        reason,
    )


def worst_case_base() -> dict[str, Any]:
    """Zero seeds/atoms, maximal limits, every reason and limitation key, 9-digit counters."""
    from atlas_aci.protocol import impact as schema

    limits = {name: high for name, (_, _, high) in lim.IMPACT_LIMITS.items()}
    side = {"snapshot_id": HEX, "generation_id": HEX}
    return {
        "projection": "change_impact",
        "status": "potential_impact",
        "basis": "static_reachability",
        "runtime_observation": "not_performed",
        "before": dict(side),
        "after": dict(side),
        "change_set_id": HEX,
        "change_summary": {
            name: BIG
            for name in (*lim_categories(), "unchanged", "candidates")  # S19 summary keys
        },
        "propagation": {
            "direction": "incoming",
            "relations": {name: True for name in lim.RELATIONS},
            "confidence": {name: True for name in lim.DEFAULT_CONFIDENCE},
            "seed_categories": {name: BIG for name in lim.SEED_CATEGORIES},
            "ranker": lim.RANKER_ID,
        },
        "effective_limits": {
            **limits,
            "max_bytes": BIG,
            "element_limit": 10000,
            "max_diff_entities": BIG,
            "max_diff_occurrences": BIG,
            "max_changed_files": BIG,
        },
        "work": {
            **{name: BIG for name in schema.WORK_KEYS},
            "boundary_probe_stop": max(lim.PROBE_STOPS, key=len),
        },
        "diff_work": {name: BIG for name in schema.DIFF_WORK_KEYS},
        "seeds": [],
        "seeds_total": BIG,
        "impacted_total": BIG,
        "coverage": {
            "state": "incomplete",
            "reasons": {name: True for name in lim.COVERAGE_REASONS},
            "boundaries_total": BIG,
        },
        "limitations": {name: True for name in lim.LIMITATIONS},
    }


def lim_categories():
    from atlas_aci.query.diff import CATEGORIES

    return CATEGORIES


def minimum_budget() -> int:
    data = assemble(worst_case_base(), [], [], None)
    return size(data, element_limit=10000, generation_id=HEX, snapshot_id=HEX)


IMPACT_MINIMUM_BUDGET = minimum_budget()
