"""Capture one bounded traversal and issue all immutable whole-proof pages atomically."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from urllib.parse import urlencode

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.graph import envelope, validate_page
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query.admission import Admission, minimum_budget
from atlas_aci.query.leases import canonical_bytes, digest
from atlas_aci.query.traversal import limits, navigate
from atlas_aci.storage import evidence as ev

COLLECTION_MINIMUM_BUDGET = minimum_budget("retained_collection_limit")


class CollectionAdmission(Admission):
    """Collection allowance is independent of page wire size; no first-page spool."""

    def size(self, data):
        reserved = copy.deepcopy(data)
        reserved["stop_reason"] = "retained_collection_limit"
        return super().size(reserved)

    def __call__(self, data):
        accepted = super().__call__(data)
        if not accepted and self.reason == "byte_limit":
            self.reason = "retained_collection_limit"
        return accepted


def normalize(query, config, page_bytes, scope):
    normalized = {
        "operation": query["op"],
        "seed": query["seed"],
        "direction": query.get("direction", "outgoing"),
        "relations": sorted(query.get("relations", ["call"])),
        "confidence": sorted(query.get("confidence", ["EXTRACTED", "INFERRED"])),
        **limits(query),
        "target": query.get("target"),
        "collection_budget": min(
            query.get("max_bytes", config.max_lease_state_bytes), config.max_lease_state_bytes
        ),
        "element_limit": config.max_bound_field_elements,
        "page_max_bytes": page_bytes,
        "authority": scope,
    }
    return normalized


def resource_descriptor(reader, hop):
    """Only actual returned G1 proof membership, not aggregate min/max summaries."""
    assertion = hop["assertion"]
    evidence = hop["evidence"]
    occurrence = hop["occurrence"]
    for kind, item in [
        ("assertion", assertion),
        ("evidence", evidence),
        ("occurrence", occurrence),
        ("producer", hop["producer"]),
    ]:
        actual = ev.get(reader.db, kind, item["id"])
        if actual != item:
            raise ToolError("INVALID_RESULT", "Captured proof differs from owned graph.")
    if (
        assertion["evidence_id"] != evidence["id"]
        or evidence["occurrence_id"] != occurrence["id"]
        or evidence["producer_id"] != hop["producer"]["id"]
    ):
        raise ToolError("INVALID_RESULT", "Captured proof is disconnected.")
    if reader._source_lease is None or evidence["provenance_state"] != "source":
        return None
    if any(
        evidence[key] != occurrence[key]
        for key in ("source_sha256", "source_size", "start_byte", "end_byte", "provenance_state")
    ):
        raise ToolError("INVALID_RESULT", "Captured source proof is inconsistent.")
    if (
        reader._source_lease.bindings.get((occurrence["path"], evidence["source_sha256"]))
        is not True
    ):
        return None
    descriptor = {
        "repository": digest(str(reader.repo)),
        "generation_id": reader.generation_id,
        "snapshot_id": reader.snapshot_id,
        "path": occurrence["path"],
        "source_sha256": evidence["source_sha256"],
        "source_size": evidence["source_size"],
        "start_byte": evidence["start_byte"],
        "end_byte": evidence["end_byte"],
        "evidence_id": evidence["id"],
        "occurrence_id": occurrence["id"],
        "provenance_state": evidence["provenance_state"],
    }
    descriptor["id"] = digest(descriptor)
    return descriptor


def atoms_from(data, operation):
    if operation == "path":
        return [] if data["path"] is None else [{"kind": "path", "path": data["path"]}]
    nodes = {node["id"]: node for node in data["nodes"]}
    if not data["edges"]:
        return [{"kind": "node", "node": node} for node in data["nodes"]]
    return [
        {
            "kind": "edge",
            "proof": hop,
            "nodes": [
                nodes[hop["assertion"][key]] for key in ("source_entity_id", "target_entity_id")
            ],
        }
        for hop in data["edges"]
    ]


async def issue_pages(registry, factory, arguments):
    config = registry.config
    query = arguments["query"]
    page_budget = arguments["page_max_bytes"]
    if type(page_budget) is not int or not 0 < page_budget <= config.max_response_bytes:
        raise ToolError("INVALID_BUDGET", "Invalid page result budget.")
    requested_collection = query.get("max_bytes", config.max_lease_state_bytes)
    if type(requested_collection) is not int or requested_collection < COLLECTION_MINIMUM_BUDGET:
        raise ToolError("INVALID_BUDGET", "Collection cannot contain bounded metadata.")
    if config.max_lease_state_bytes < COLLECTION_MINIMUM_BUDGET:
        raise ToolError("LEASE_CAPACITY", "No retained collection metadata capacity.")
    async with registry.pending() as reservation:
        with reservation.owner as owner:
            reader = owner.enter_context(
                factory.reader_lease(chunk_catalog_bytes=config.max_lease_state_bytes)
            )
            expected = arguments.get("expected_generation_id", query.get("expected_generation_id"))
            if expected is not None and expected != reader.generation_id:
                raise ToolError("SNAPSHOT_MISMATCH", "Requested generation does not match.")
            start = time.monotonic()
            deadline = start + config.lease_setup_ms / 1000

            def check():
                if time.monotonic() >= deadline:
                    raise ToolError("SETUP_DEADLINE", "Retained result setup deadline reached.")

            normalized = normalize(query, config, page_budget, reservation.scope)
            query_digest = digest(normalized)
            admission = CollectionAdmission(
                operation=query["op"],
                budget=normalized["collection_budget"],
                element_limit=config.max_bound_field_elements,
                generation_id=reader.generation_id,
                snapshot_id=reader.snapshot_id,
                evidence_status=reader._lease_evidence_status,
            )
            await asyncio.sleep(0)
            check()
            captured = await navigate(
                reader,
                operation=query["op"],
                admit=admission,
                **{
                    k: v
                    for k, v in query.items()
                    if k not in {"op", "schema_version", "max_bytes", "expected_generation_id"}
                },
            )
            captured = admission.project(captured)
            check()
            atoms = atoms_from(captured, query["op"])
            resources = {}
            proof_reads = 0
            for atom in atoms:
                hops = (
                    atom["path"]["hops"]
                    if atom["kind"] == "path"
                    else [atom["proof"]]
                    if atom["kind"] == "edge"
                    else []
                )
                atom["resources"] = []
                for hop in hops:
                    check()
                    descriptor = resource_descriptor(reader, hop)
                    proof_reads += 4
                    if descriptor is not None:
                        resources[descriptor["id"]] = descriptor
                        atom["resources"].append(descriptor["id"])
                        if len(resources) > config.max_lease_resources:
                            raise ToolError("LEASE_CAPACITY", "Too many issued source spans.")
            await asyncio.sleep(0)
            check()
            metadata = {
                key: value
                for key, value in captured.items()
                if key not in {"nodes", "edges", "path"}
            }
            metadata["query_operation"] = query["op"]

            def build(lease):
                check()
                pages = []
                offset = 0
                total = len(atoms)

                def page(items, next_index):
                    next_cursor = (
                        registry.token(lease, "cursor", next_index)
                        if next_index is not None
                        else None
                    )
                    resource_views = {}
                    for atom in items:
                        for identifier in atom["resources"]:
                            descriptor = resources[identifier]
                            if "uri" not in descriptor:
                                descriptor["uri"] = (
                                    "atlas-source://"
                                    + identifier
                                    + "?"
                                    + urlencode(
                                        {
                                            "token": registry.token(lease, "resource", identifier),
                                            "offset": 0,
                                            "max_bytes": min(
                                                config.max_bytes_per_call, config.max_response_bytes
                                            ),
                                        }
                                    )
                                )
                            resource_views[identifier] = descriptor
                    data = {
                        "projection": "captured_result_page",
                        "query_digest": query_digest,
                        "lease_id": lease.identifier,
                        "captured": metadata,
                        "items": items,
                        "resources": list(resource_views.values()),
                        "offset": offset,
                        "returned_count": len(items),
                        "total_count": total,
                        "has_more": next_index is not None,
                        "next_cursor": next_cursor,
                        "setup_proof_reads": proof_reads,
                    }
                    result = envelope(
                        data,
                        operation="page" if not pages else "continue",
                        generation_id=reader.generation_id,
                        snapshot_id=reader.snapshot_id,
                        evidence_status=reader._lease_evidence_status,
                    )
                    result["bounds"]["element_limit"] = config.max_bound_field_elements
                    return result

                while offset < total or not pages:
                    check()
                    items = []
                    for atom in atoms[offset:]:
                        check()
                        candidate = [*items, copy.deepcopy(atom)]
                        trial = page(
                            candidate, len(pages) + 1
                        )  # Reserve a complete next cursor first.
                        if (
                            len(result_bytes(sdk_result(trial, error=False, structured=True)))
                            > page_budget
                        ):
                            break
                        items = candidate
                    if offset < total and not items:
                        raise ToolError(
                            "PAGE_TOO_SMALL", "Page cannot contain one whole result atom."
                        )
                    next_offset = offset + len(items)
                    result = page(items, len(pages) + 1 if next_offset < total else None)
                    try:
                        validate_page(result, result["operation"], config.max_bound_field_elements)
                    except ValueError as exc:
                        raise ToolError(
                            "INVALID_RESULT", "Generated page proof is invalid."
                        ) from exc
                    raw = canonical_bytes(result)
                    if (
                        len(result_bytes(sdk_result(result, error=False, structured=True)))
                        > page_budget
                    ):
                        raise ToolError(
                            "PAGE_TOO_SMALL", "Page cannot contain bounded result metadata."
                        )
                    pages.append(raw)
                    if (
                        len(pages) > config.max_lease_pages
                        or sum(map(len, pages)) > config.max_lease_state_bytes
                    ):
                        raise ToolError(
                            "LEASE_CAPACITY", "Page collection exceeds retained capacity."
                        )
                    offset = next_offset
                    if offset >= total:
                        break
                check()
                return pages

            lease = await registry.install(
                owner,
                reader,
                query_digest,
                build,
                resources,
                reservation=reservation,
                check_setup=check,
            )
            return json.loads(lease.pages[0])
