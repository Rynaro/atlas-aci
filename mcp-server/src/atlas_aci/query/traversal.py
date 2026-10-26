"""Deterministic active-assertion traversal on one authenticated request lease.

The acquisition proof precedes this engine and is outside its charged work.
Every adjacency row, including rejected confidence and unknown targets, spends
an edge reservation. Returned proofs are admitted whole, never prefix-clipped.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
from collections import deque
from collections.abc import Callable
from typing import Any

from atlas_aci.query.indexed import IndexedReader, QueryStopped
from atlas_aci.storage import evidence as ev

LIMITS = {
    "max_visited_nodes": (100, 1, 1000),
    "max_examined_edges": (1000, 0, 10000),
    "max_depth": (3, 0, 32),
    "deadline_ms": (100, 0, 2000),
}


def limits(values):
    result = {}
    for name, (default, lower, upper) in LIMITS.items():
        value = values.get(name, default)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError("Invalid " + name)
        result[name] = value
    return result


def identity(entity):
    return {
        "projection": "entity_identity",
        **{
            k: entity[k]
            for k in (
                "id",
                "name",
                "kind",
                "language",
                "path",
                "owners",
                "identity_basis",
                "ordinal",
            )
        },
    }


def resolve(query, selector):
    if not isinstance(selector, dict) or len(selector) != 1:
        raise ValueError("Exactly one endpoint selector is required")
    if set(selector) == {"entity_id"} and ev.hash_value(selector["entity_id"]):
        row = query.record("entity", selector["entity_id"], endpoint=True)
        candidates = [] if row is None else [row]
    elif set(selector) == {"name"} and isinstance(selector["name"], str) and selector["name"]:
        candidates = query.name_candidates(selector["name"])
    else:
        raise ValueError("Invalid endpoint selector")
    state = "absent" if not candidates else "unique" if len(candidates) == 1 else "ambiguous"
    return {
        "selector_kind": next(iter(selector)),
        "state": state,
        "candidates": [identity(c) for c in candidates],
        "candidates_complete": len(candidates) < 2,
        "entity_id": candidates[0]["id"] if len(candidates) == 1 else None,
    }, candidates


def hydrate(query, assertion, direction):
    evidence = query.record("evidence", assertion["evidence_id"])
    ev.require(evidence is not None, "navigation evidence")
    occurrence = query.record("occurrence", evidence["occurrence_id"])
    producer = query.record("producer", evidence["producer_id"])
    ev.require(occurrence is not None and producer is not None, "navigation proof closure")
    ev.require(
        all(
            evidence[k] == occurrence[k]
            for k in ("source_sha256", "source_size", "start_byte", "end_byte", "provenance_state")
        ),
        "navigation source provenance",
    )
    return {
        "projection": "canonical_assertion_proof",
        "traversal_direction": direction,
        "assertion": assertion,
        "evidence": evidence,
        "occurrence": occurrence,
        "producer": producer,
        "source_availability": "available"
        if query.reader.snapshot_id is not None and evidence["source_sha256"] is not None
        else "unavailable",
    }


async def navigate(
    reader: Any,
    *,
    seed,
    operation="neighborhood",
    target=None,
    direction="outgoing",
    relations=None,
    confidence=None,
    admit: Callable[[dict], bool] | None = None,
    **options,
):
    """Traverse with fixed indexed steps; cancellation releases the owned hook.

    ``admit`` receives a complete tentative presentation. The protocol adapter
    measures its worst negotiated SDK framing with reserved final metadata.
    """
    if operation not in {"neighborhood", "path"} or direction not in {"outgoing", "incoming"}:
        raise ValueError("Invalid traversal operation/direction")
    relations = ["call"] if relations is None else relations
    confidence = ["EXTRACTED", "INFERRED"] if confidence is None else confidence
    if (
        not isinstance(relations, list)
        or not relations
        or len(set(relations)) != len(relations)
        or not set(relations) <= ev.RELATIONS
    ):
        raise ValueError("Invalid relation selection")
    if (
        not isinstance(confidence, list)
        or not confidence
        or len(set(confidence)) != len(confidence)
        or not set(confidence) <= {"EXTRACTED", "INFERRED"}
    ):
        raise ValueError("Invalid confidence selection")
    if (operation == "path") != (target is not None):
        raise ValueError("Path requires a target")
    effective = limits(options)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    queue: deque[tuple[str, int]] = deque()
    visited: dict[str, tuple[dict[str, Any], int]] = {}
    parents = {}
    position = None
    stop = None
    outcome = "complete"
    path = None
    seed_info: dict[str, Any] = {
        "selector_kind": next(iter(seed), "unknown"),
        "state": "unresolved",
        "candidates": [],
        "candidates_complete": False,
        "entity_id": None,
    }
    target_info = None

    with IndexedReader(
        reader,
        max_examined_edges=effective["max_examined_edges"],
        deadline_ms=effective["deadline_ms"],
    ) as query:

        def payload():
            frontier = {
                "queue": list(queue),
                "position": position,
                "visited": list(visited),
                "stop_reason": stop,
                "work": query.counts,
                "depth": max((v[1] for v in visited.values()), default=0),
            }
            data = {
                "scope": "current_published_evidence",
                "outcome": outcome,
                "stop_reason": stop,
                "effective_limits": effective,
                "seed": seed_info,
                "direction": direction,
                "relations": sorted(relations),
                "confidence": sorted(confidence),
                "work": {**query.counts, "visited_nodes": len(visited)},
                "frontier": {
                    "commitment": hashlib.sha256(ev.canonical(frontier).encode()).hexdigest(),
                    "queued_nodes": len(queue),
                    "depth": frontier["depth"],
                },
                "unknown_relations_traversed": False,
            }
            if operation == "path":
                data.update(target=target_info, path=path)
            else:
                data.update(nodes=list(nodes), edges=list(edges))
            return data

        def allowed():
            query.check_deadline()
            result = admit is None or admit(copy.deepcopy(payload()))
            query.check_deadline()
            return result

        def finish():
            query.check_deadline()
            result = payload()
            query.check_deadline()
            return result

        try:
            await asyncio.sleep(0)  # Controlled cancellation on the real acquired lease/hook.
            seed_info, sources = resolve(query, seed)
            destinations = []
            if target is not None:
                target_info, destinations = resolve(query, target)
            selections = [seed_info] + ([] if target_info is None else [target_info])
            if any(s["state"] == "ambiguous" for s in selections):
                outcome = "disambiguation"
                return finish()
            if any(s["state"] == "absent" for s in selections):
                outcome = "no_evidence"
                return finish()
            start = sources[0]
            goal = destinations[0]["id"] if destinations else None
            visited[start["id"]] = (start, 0)
            nodes.append(identity(start))
            queue.append((start["id"], 0))
            if not allowed():
                nodes.clear()
                raise QueryStopped(getattr(admit, "reason", "byte_limit"))
            if start["id"] == goal:
                path = {"nodes": [identity(start)], "hops": []}
                if not allowed():
                    path = None
                    raise QueryStopped(getattr(admit, "reason", "byte_limit"))
                return finish()
            while queue:
                query.check_deadline()
                current, depth = queue[0]
                if depth >= effective["max_depth"]:
                    raise QueryStopped("depth_limit")
                for relation in sorted(relations):
                    after = ""
                    position = [current, depth, relation, after]
                    while True:
                        await asyncio.sleep(0)
                        assertion = query.edge(current, relation, direction, after)
                        if assertion is None:
                            break
                        # Row work is committed even when the fact is not traversable.
                        after = assertion["id"]
                        position = [current, depth, relation, after]
                        if (
                            assertion["resolution"] not in confidence
                            or assertion["target_entity_id"] is None
                        ):
                            continue
                        other = (
                            assertion["target_entity_id"]
                            if direction == "outgoing"
                            else assertion["source_entity_id"]
                        )
                        if other is None:
                            continue
                        fresh = other not in visited
                        if fresh and len(visited) >= effective["max_visited_nodes"]:
                            raise QueryStopped("visited_node_limit")
                        entity = query.record("entity", other) if fresh else visited[other][0]
                        ev.require(entity is not None, "navigation target entity")
                        proof = hydrate(query, assertion, direction)
                        if operation == "neighborhood":
                            if fresh:
                                nodes.append(identity(entity))
                            edges.append(proof)
                            try:
                                accepted = allowed()
                            except BaseException:
                                edges.pop()
                                if fresh:
                                    nodes.pop()
                                raise
                            if not accepted:
                                edges.pop()
                                if fresh:
                                    nodes.pop()
                                raise QueryStopped(getattr(admit, "reason", "byte_limit"))
                        if fresh:
                            visited[other] = (entity, depth + 1)
                            parents[other] = (current, proof)
                            queue.append((other, depth + 1))
                            if other == goal:
                                chain_nodes, chain_hops = [identity(entity)], []
                                cursor = other
                                while cursor != start["id"]:
                                    previous, hop = parents[cursor]
                                    chain_hops.append(hop)
                                    chain_nodes.append(identity(visited[previous][0]))
                                    cursor = previous
                                path = {
                                    "nodes": list(reversed(chain_nodes)),
                                    "hops": list(reversed(chain_hops)),
                                }
                                if not allowed():
                                    path = None
                                    raise QueryStopped(getattr(admit, "reason", "byte_limit"))
                                return finish()
                queue.popleft()
            position = None
            if operation == "path":
                outcome = "no_path"
            return finish()
        except QueryStopped as exc:
            stop = exc.reason
            outcome = "partial"
            path = None
        return payload()
