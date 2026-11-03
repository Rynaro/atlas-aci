"""ACI-S20 closed `impact` schemas and the independent structural validator.

Registered additively into `atlas.graph.v1` (S18 `protocol/context.py` precedent).
The request deliberately carries NO path, repository or graph-root field: the
before snapshot is operator configuration (`serve --before-graph-root`).
"""

from __future__ import annotations

from atlas_aci.protocol import graph as g
from atlas_aci.query import impact_limits as lim
from atlas_aci.storage import evidence as ev

obj, array, nullable = g.obj, g.array, g.nullable
H, S, C, B = g.HASH, g.STRING, g.COUNT, g.BOOL


def flags(names):
    """A closed `{name: true}` set: maps keep the element cap honest (S18 precedent)."""
    return {
        "type": "object",
        "properties": {name: {"const": True} for name in names},
        "additionalProperties": False,
    }


def counts(names):
    return obj({name: C for name in names})


IMPACT_INPUT = obj(
    {
        "schema_version": {"const": g.VERSION},
        "op": {"const": "impact"},
        "expected_generation_id": H,
        "expected_before_generation_id": H,
        "relations": {
            "type": "array",
            "items": {"enum": list(lim.RELATIONS)},
            "minItems": 1,
            "uniqueItems": True,
        },
        "confidence": {
            "type": "array",
            "items": {"enum": ["EXTRACTED", "INFERRED"]},
            "minItems": 1,
            "uniqueItems": True,
        },
        "max_bytes": {"type": "integer", "minimum": 0},
        **{
            key: {"type": "integer", "minimum": low, "maximum": high}
            for key, (_, low, high) in lim.IMPACT_LIMITS.items()
        },
    }
)
IMPACT_INPUT["required"] = ["schema_version", "op"]

SIDE = {"enum": list(lim.SIDES[::-1])}  # before | after
CATEGORY = {"enum": list(lim.SEED_CATEGORIES)}
SNAPSHOT = obj({"snapshot_id": H, "generation_id": H})
LIMIT_KEYS = (
    *lim.IMPACT_LIMITS,
    "max_bytes",
    "element_limit",
    "max_diff_entities",
    "max_diff_occurrences",
    "max_changed_files",
)
WORK_KEYS = (
    "examined_edges",
    "visited_nodes",
    "sql_queries",
    "vm_operations_reserved",
    "vm_progress_callbacks",
    "boundary_probes",
    "boundary_probes_completed",
)
WORK = obj(
    {
        **{name: C for name in WORK_KEYS},
        "boundary_probe_stop": nullable({"enum": list(lim.PROBE_STOPS)}),
    }
)
DIFF_WORK_KEYS = (
    "before_entities",
    "after_entities",
    "before_occurrences",
    "after_occurrences",
    "changed_files",
    "parsed_files",
)
FRONTIER = nullable(
    obj(
        {
            "outcome": {"enum": ["complete", "partial", "no_evidence", "disambiguation"]},
            "stop_reason": nullable({"enum": list(lim.FRONTIER_STOPS)}),
            "commitment": H,
            "queued_nodes": C,
            "depth": C,
        }
    )
)
SEED = obj(
    {
        "changed_entity_id": H,
        "change_record_id": H,
        "category": CATEGORY,
        "snapshot_side": SIDE,
        "snapshot_id": H,
        "generation_id": H,
        "name": S,
        "kind": S,
        "language": S,
        "path": S,
        "expansion": {"enum": ["expanded", "not_expanded"]},
        "frontier": FRONTIER,
    }
)
RANK = obj({"distance": C, "category_weight": C, "side_order": C})
PATH = obj({"nodes": array(g.IDENTITY), "hops": array(g.HOP)})
IMPACTED = obj(
    {
        "status": {"const": "potentially_affected"},
        "basis": {"const": "static_reachability"},
        "entity": g.IDENTITY,
        "also_changed": B,
        "snapshot_side": SIDE,
        "snapshot_id": H,
        "generation_id": H,
        "changed_entity_id": H,
        "change_category": CATEGORY,
        "change_record_id": H,
        "distance": C,
        "reached_from_seeds": C,
        "rank": RANK,
        "path": PATH,
    }
)
BOUNDARY = obj(
    {
        "entity_id": H,
        "name": S,
        "language": S,
        "snapshot_side": SIDE,
        "unresolved_reference_count": nullable(C),
        "ambiguous_reference_count": C,
        "reference_coverage": {"enum": ["known", "unknown"]},
        "basis": {"const": "same_name_reference"},
        "reasons": flags(
            ("unresolved_reference", "ambiguous_reference", "reference_coverage_unknown")
        ),
    }
)
COVERAGE = obj(
    {
        "state": {"enum": ["complete", "incomplete"]},
        "reasons": flags(lim.COVERAGE_REASONS),
        "boundaries": array(BOUNDARY),
        "boundaries_total": C,
        "boundaries_returned": C,
    }
)
IMPACT_DATA = obj(
    {
        "projection": {"const": "change_impact"},
        "status": {"const": "potential_impact"},
        "basis": {"const": "static_reachability"},
        "runtime_observation": {"const": "not_performed"},
        "before": SNAPSHOT,
        "after": SNAPSHOT,
        "change_set_id": H,
        "change_summary": {"type": "object", "additionalProperties": C},
        "propagation": obj(
            {
                "direction": {"const": "incoming"},
                "relations": flags(lim.RELATIONS),
                "confidence": flags(lim.DEFAULT_CONFIDENCE),
                "seed_categories": counts(lim.SEED_CATEGORIES),
                "ranker": {"const": lim.RANKER_ID},
            }
        ),
        "effective_limits": counts(LIMIT_KEYS),
        "work": WORK,
        "diff_work": counts(DIFF_WORK_KEYS),
        "seeds": array(SEED),
        "seeds_total": C,
        "impacted": array(IMPACTED),
        "impacted_total": C,
        "impacted_returned": C,
        "coverage": COVERAGE,
        "selection": obj(
            {
                "truncated": B,
                "reason": nullable({"enum": ["byte_limit", "element_limit"]}),
            }
        ),
        "limitations": flags(lim.LIMITATIONS),
        "impact_id": H,
    }
)


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def validate_impact(payload, operation, element_limit):
    """Re-check every claim an impact result makes; never trust the producer."""
    if payload.get("operation") != operation or not g.STRUCTURED_VALIDATOR.is_valid(payload):
        raise ValueError("Invalid impact operation/schema")
    if payload["status"] == "error":
        return
    from atlas_aci.query.admission import lists

    data = payload["data"]
    _check(isinstance(data, dict) and data.get("projection") == "change_impact", "Impact shape")
    _check(not any(len(items) > element_limit for items in lists(data)), "Impact element bound")
    _check(
        payload["bounds"]["element_limit"] == element_limit
        and data["effective_limits"]["element_limit"] == element_limit,
        "Impact element limit",
    )
    _check(
        data["after"]["generation_id"] == payload["generation_id"]
        and data["after"]["snapshot_id"] == payload["snapshot_id"],
        "Impact generation mismatch",
    )
    _check(data["impact_id"] == lim.impact_id(data), "Impact identity mismatch")

    limits, work, diff = data["effective_limits"], data["work"], data["diff_work"]
    for counter, limit in (
        ("examined_edges", "max_examined_edges"),
        ("visited_nodes", "max_visited_nodes"),
        ("vm_operations_reserved", "max_vm_operations"),
        ("boundary_probes", "max_boundary_probes"),
    ):
        _check(work[counter] <= limits[limit], "Impact work bound: " + counter)
    _check(work["boundary_probes_completed"] <= work["boundary_probes"], "Impact probe counters")
    for counter in ("before_entities", "after_entities"):
        _check(diff[counter] <= limits["max_diff_entities"], "Impact diff entity bound")
    for counter in ("before_occurrences", "after_occurrences"):
        _check(diff[counter] <= limits["max_diff_occurrences"], "Impact diff occurrence bound")
    _check(diff["changed_files"] <= limits["max_changed_files"], "Impact changed-file bound")
    for name, (_, low, high) in lim.IMPACT_LIMITS.items():
        _check(low <= limits[name] <= high, "Impact limit range: " + name)

    sides = {"before": data["before"], "after": data["after"]}
    seeds = {}
    for seed in data["seeds"]:
        _check(seed["changed_entity_id"] not in seeds, "Duplicate impact seed")
        _check(
            seed["snapshot_id"] == sides[seed["snapshot_side"]]["snapshot_id"]
            and seed["generation_id"] == sides[seed["snapshot_side"]]["generation_id"],
            "Impact seed side mismatch",
        )
        _check((seed["expansion"] == "expanded") == (seed["frontier"] is not None), "Seed frontier")
        seeds[seed["changed_entity_id"]] = seed
    _check(
        len(seeds) <= limits["max_seeds"] and len(seeds) <= data["seeds_total"],
        "Impact seed extent",
    )
    categories = data["propagation"]["seed_categories"]
    _check(sum(categories.values()) == data["seeds_total"], "Impact seed category totals")

    allowed = {
        "relations": sorted(data["propagation"]["relations"]),
        "confidence": sorted(data["propagation"]["confidence"]),
        "direction": "incoming",
    }
    previous = None
    seen = set()
    for item in data["impacted"]:
        entity, path = item["entity"], item["path"]
        _check(entity["id"] not in seen, "Duplicate impacted entity")
        seen.add(entity["id"])
        key = lim.rank_key(item)
        _check(previous is None or previous < key, "Impact rank order")
        previous = key
        seed = seeds.get(item["changed_entity_id"])
        _check(
            seed is not None
            and seed["snapshot_side"] == item["snapshot_side"]
            and seed["category"] == item["change_category"]
            and seed["change_record_id"] == item["change_record_id"]
            and seed["expansion"] == "expanded",
            "Impact item has no matching expanded seed",
        )
        _check(
            item["snapshot_id"] == sides[item["snapshot_side"]]["snapshot_id"]
            and item["generation_id"] == sides[item["snapshot_side"]]["generation_id"],
            "Impact item side mismatch",
        )
        nodes, hops = path["nodes"], path["hops"]
        _check(
            len(nodes) == len(hops) + 1
            and len({n["id"] for n in nodes}) == len(nodes)
            and nodes[0]["id"] == item["changed_entity_id"]
            and nodes[-1]["id"] == entity["id"]
            and nodes[-1] == entity,
            "Impact path endpoints",
        )
        _check(
            item["distance"] == len(hops) and 1 <= item["distance"] <= limits["max_depth"],
            "Impact distance",
        )
        _check(
            item["rank"]
            == {
                "distance": item["distance"],
                "category_weight": lim.CATEGORY_WEIGHT[item["change_category"]],
                "side_order": lim.SIDES.index(item["snapshot_side"]),
            }
            and item["reached_from_seeds"] >= 1,
            "Impact rank record",
        )
        identities = {node["id"]: node for node in nodes}
        for node in nodes:
            ev.validate_record(
                {"type": "entity", **{k: v for k, v in node.items() if k != "projection"}}
            )
        for source, hop, target in zip(nodes[:-1], hops, nodes[1:], strict=True):
            g.validate_hop(hop, identities, allowed)
            assertion = hop["assertion"]
            _check(
                assertion["target_entity_id"] == source["id"]
                and assertion["source_entity_id"] == target["id"],
                "Impact path direction",
            )
    _check(
        data["impacted_returned"] == len(data["impacted"])
        and data["impacted_returned"] <= data["impacted_total"],
        "Impact extent",
    )

    coverage = data["coverage"]
    reasons = coverage["reasons"]
    _check(
        coverage["boundaries_returned"] == len(coverage["boundaries"])
        and coverage["boundaries_returned"] <= coverage["boundaries_total"],
        "Impact boundary extent",
    )
    _check((coverage["state"] == "complete") == (not reasons), "Impact coverage state")
    _check(
        (("seed_limit" in reasons) == (data["seeds_total"] > len(data["seeds"]))),
        "Impact seed_limit coverage",
    )
    if coverage["boundaries_total"] > 0:
        _check(
            any(
                name in reasons
                for name in (
                    "unresolved_reference",
                    "ambiguous_reference",
                    "reference_coverage_unknown",
                )
            ),
            "Impact boundary without reason",
        )
    for boundary in coverage["boundaries"]:
        unresolved = boundary["unresolved_reference_count"]
        expected = {
            name
            for name, present in (
                ("unresolved_reference", unresolved is not None and unresolved > 0),
                ("ambiguous_reference", boundary["ambiguous_reference_count"] > 0),
                ("reference_coverage_unknown", boundary["reference_coverage"] == "unknown"),
            )
            if present
        }
        _check(
            expected and set(boundary["reasons"]) == expected and expected <= set(reasons),
            "Impact boundary reasons",
        )
        _check(
            (unresolved is None) == (boundary["reference_coverage"] == "unknown"),
            "Impact boundary coverage",
        )

    selection = data["selection"]
    truncated = (
        data["impacted_returned"] < data["impacted_total"]
        or coverage["boundaries_returned"] < coverage["boundaries_total"]
    )
    _check(
        selection["truncated"] == truncated and (selection["reason"] is not None) == truncated,
        "Impact selection",
    )
    _check(
        payload["bounds"]["truncated"] == (truncated or "work_limit" in reasons),
        "Impact bounds truncation",
    )
    _check(
        payload["bounds"]["lists"]
        == {
            "impacted": {
                "total_count": data["impacted_total"],
                "returned_count": data["impacted_returned"],
                "truncated": data["impacted_returned"] < data["impacted_total"],
            },
            "boundaries": {
                "total_count": coverage["boundaries_total"],
                "returned_count": coverage["boundaries_returned"],
                "truncated": coverage["boundaries_returned"] < coverage["boundaries_total"],
            },
        },
        "Impact bounds lists",
    )
