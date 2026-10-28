"""One-reader collection, complete atoms, final negotiated SDK accounting."""

from __future__ import annotations

import asyncio

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.graph import envelope
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query import context_bundles as bundles
from atlas_aci.query.context_admission import RANKER_ID, prefix, ranked, reserved_payload
from atlas_aci.query.context_limits import canonical_request, normalize
from atlas_aci.query.context_tokens import account, declaration
from atlas_aci.query.context_work import ContextWork
from atlas_aci.query.indexed import QueryStopped
from atlas_aci.query.leases import canonical_bytes, digest
from atlas_aci.query.retrieval import retrieve
from atlas_aci.query.traversal import navigate


def record_domain(reader, query, selected, captured, offset, total, more, reason):
    return {
        "format": "atlas-context-record.v1",
        "request": canonical_request(query),
        "repository": str(reader.repo),
        "generation_id": reader.generation_id,
        "snapshot_id": reader.snapshot_id,
        "ranker": RANKER_ID,
        "tokenizer": captured["tokenizer"],
        "bundles": selected,
        "effective_limits": captured["effective_limits"],
        "coverage": captured["coverage"],
        "retrieval": captured["retrieval"],
        "frontiers": captured["frontiers"],
        "work": captured["work"],
        "collection_stop": captured["collection_stop"],
        "selected_range": {"offset": offset, "count": len(selected)},
        "selection": {"total_count": total, "has_more": more, "reason": reason},
        "source_resource_ids": {
            descriptor["id"]: True
            for atom in selected
            for source in atom["sources"]
            if (descriptor := bundles.resource_descriptor(reader, source)) is not None
        },
    }


def presentation(
    reader,
    query,
    selected,
    captured,
    *,
    offset=0,
    total=None,
    more=False,
    cursor=None,
    resources=None,
    operation="context",
    element_limit=200,
    reason=None,
):
    domain = record_domain(reader, query, selected, captured, offset, total, more, reason)
    data = {
        "projection": "task_context",
        **domain,
        "retrieval_record_id": digest(domain),
        "query_digest": captured["query_digest"],
        "resources": resources or [],
        "offset": offset,
        "returned_count": len(selected),
        "total_count": total,
        "has_more": more,
        "next_cursor": cursor,
        "selection_truncated": reason is not None,
        "selection_reason": reason,
    }
    result = envelope(
        data,
        operation=operation,
        generation_id=reader.generation_id,
        snapshot_id=reader.snapshot_id,
        evidence_status=reader._lease_evidence_status,
    )
    result["bounds"]["element_limit"] = element_limit
    return result


def frontier(data, seed_id, shared_deadline_ms):
    return {
        "seed_id": seed_id,
        "deadline_scope": "configured_shared_context",
        "outcome": data["outcome"],
        "stop_reason": data["stop_reason"],
        "frontier": data["frontier"],
        "work": data["work"],
        # Remaining wall milliseconds are an operational duration, not a new
        # per-seed policy. The absolute ContextWork deadline remains authoritative.
        "effective_limits": {**data["effective_limits"], "deadline_ms": shared_deadline_ms},
        "selection_commitment": digest({"seed": data["seed"], "target": data.get("target")}),
        "target_state": data.get("target", {}).get("state") if data.get("target") else None,
    }


async def collect(reader, query, config, *, structured):
    work = ContextWork(
        max_vm_operations=query["max_vm_operations"],
        max_examined_edges=query["max_examined_edges"],
        deadline_ms=query["deadline_ms"],
        max_proof_queries=query["max_bundles"] * 16,
        max_source_reads=query["max_bundles"],
        max_source_bytes=query["max_collection_bytes"],
        max_tokenizer_calls=16 * (query["max_bundles"] + 1),
    )
    budget = query["max_collection_bytes"]
    metadata = {
        "request": canonical_request(query),
        "ranker": RANKER_ID,
        "tokenizer": declaration(config.context_tokenizer),
    }
    if len(canonical_bytes(metadata)) >= budget:
        raise ToolError("INVALID_BUDGET", "Collection cannot contain context metadata.")
    atoms, frontiers = [], []
    stop = None

    class RetrievalCollection:
        reason = "collection_limit"

        def __call__(self, data):
            return len(canonical_bytes(data)) + len(canonical_bytes(metadata)) <= budget

    data = await retrieve(
        reader,
        text=query["text"],
        repository=query["repository"],
        path_prefixes=query["path_prefixes"],
        max_candidates=query["max_candidates"],
        max_vm_operations=query["max_vm_operations"],
        deadline_ms=query["deadline_ms"],
        accounting=work,
        admit=RetrievalCollection(),
    )

    def append(atom):
        nonlocal stop
        if len(atoms) >= query["max_bundles"]:
            stop = "bundle_limit"
            return False
        if (
            len(canonical_bytes({**metadata, "atoms": [*atoms, atom], "frontiers": frontiers}))
            > budget
        ):
            stop = "collection_limit"
            return False
        atoms.append(atom)
        return True

    seeds = []
    try:
        for position, candidate in enumerate(data["candidates"]):
            work.check()
            atom = (
                bundles.definition(reader, candidate, work, position)
                if candidate["kind"] == "entity"
                else bundles.lexical_file(reader, candidate, work, position)
            )
            if not append(atom):
                break
            if candidate["kind"] == "entity" and len(seeds) < min(
                query["max_seeds"], config.max_bound_field_elements
            ):
                seeds.append((position, candidate))
        for position, candidate in seeds if stop is None else []:
            work.check()
            remaining = budget - len(
                canonical_bytes({**metadata, "atoms": atoms, "frontiers": frontiers})
            )

            class NavigationCollection:
                reason = "context_collection_limit"

                def __init__(self, budget):
                    self.budget = budget

                def __call__(self, value):
                    return len(canonical_bytes(value)) <= self.budget

            nav = await navigate(
                reader,
                seed={"entity_id": candidate["entity"]["id"]},
                operation="path" if query["target"] else "neighborhood",
                target=query["target"],
                direction=query["direction"],
                relations=query["relations"],
                confidence=query["confidence"],
                max_examined_edges=work.remaining("examined_edges"),
                max_depth=query["max_depth"],
                max_visited_nodes=min(1000, query["max_examined_edges"] + 1),
                deadline_ms=work.remaining_ms(),
                accounting=work,
                admit=NavigationCollection(remaining),
            )
            frontiers.append(frontier(nav, candidate["entity"]["id"], query["deadline_ms"]))
            if query["target"]:
                if nav["path"] is not None:
                    append(
                        bundles.whole_path(
                            reader,
                            nav["path"],
                            work,
                            tier=candidate["match_tier"],
                            seed_rank=position,
                        )
                    )
            else:
                nodes = {n["id"]: n for n in nav["nodes"]}
                distances = {candidate["entity"]["id"]: 0}
                for hop in nav["edges"]:
                    source, target = (
                        hop["assertion"][key] for key in ("source_entity_id", "target_entity_id")
                    )
                    start, end = (
                        (source, target)
                        if hop["traversal_direction"] == "outgoing"
                        else (target, source)
                    )
                    distance = distances.get(start, query["max_depth"]) + 1
                    distances[end] = min(distances.get(end, distance), distance)
                    atom = bundles.relationship(
                        reader,
                        hop,
                        [nodes[source], nodes[target]],
                        work,
                        tier=candidate["match_tier"],
                        seed_rank=position,
                        distance=distance,
                    )
                    if not append(atom):
                        break
            if stop is not None:
                break
            if work.stop_reason is not None:
                stop = work.stop_reason
                break
            await asyncio.sleep(0)
    except QueryStopped as exc:
        stop = exc.reason
    work.check()
    ordered = ranked(atoms)
    ordered = bundles.inline_sources(
        ordered,
        reader,
        work,
        policy=query["source_policy"],
        max_inline_bytes=query["max_inline_bytes"],
    )
    work.check()
    captured = {
        "tokenizer": declaration(config.context_tokenizer),
        "effective_limits": {
            k: query[k] for k in query if k.startswith("max_") or k == "deadline_ms"
        },
        "coverage": data["coverage"],
        "retrieval": {
            k: data[k]
            for k in (
                "query_digest",
                "returned_count",
                "truncated",
                "truncation_reason",
                "diagnostic_reason",
            )
        },
        "frontiers": frontiers,
        "work": {k: v for k, v in work.counts.items() if k != "tokenizer_calls"},
        "collection_stop": stop,
    }
    captured["query_digest"] = digest(
        {
            "request": canonical_request(query),
            "generation_id": reader.generation_id,
            "snapshot_id": reader.snapshot_id,
            "ranker": RANKER_ID,
            "tokenizer": captured["tokenizer"],
            "structured": structured,
        }
    )
    if len(canonical_bytes({**metadata, "atoms": ordered, "captured": captured})) > budget:
        raise ToolError("COLLECTION_LIMIT", "Complete captured context exceeds collection budget.")
    return ordered, captured, work


async def _context(reader, arguments, config, *, structured):
    query = normalize(arguments, config)
    if query["continuation"]:
        raise ToolError("INVALID_INPUT", "Retained context requires the owned context dispatch.")
    atoms, captured, work = await collect(reader, query, config, structured=structured)

    def build(selected, more):
        return presentation(
            reader,
            query,
            selected,
            captured,
            total=len(atoms),
            more=False,
            element_limit=config.max_bound_field_elements,
            reason="byte_limit" if more else None,
        )

    empty = reserved_payload(build([], bool(atoms)), config.context_tokenizer)
    if (
        len(result_bytes(sdk_result(empty, error=False, structured=structured)))
        > query["max_bytes"]
    ):
        raise ToolError("INVALID_BUDGET", "Context metadata cannot fit the result budget.")
    selected, reason = prefix(
        atoms,
        build,
        budget=query["max_bytes"],
        structured=structured,
        element_limit=config.max_bound_field_elements,
        tokenizer=config.context_tokenizer,
    )
    payload = presentation(
        reader,
        query,
        selected,
        captured,
        total=len(atoms),
        element_limit=config.max_bound_field_elements,
        reason=reason,
    )
    work.check()
    result = account(payload, tokenizer=config.context_tokenizer, structured=structured, work=work)
    if (
        len(result_bytes(sdk_result(result, error=False, structured=structured)))
        > query["max_bytes"]
    ):
        raise ToolError("INVALID_BUDGET", "Final context exceeds result budget.")
    return result


async def context(reader, arguments, config, *, structured):
    try:
        return await _context(reader, arguments, config, structured=structured)
    except QueryStopped as exc:
        raise ToolError("CONTEXT_BUDGET_EXCEEDED", "Shared context work limit reached.") from exc
