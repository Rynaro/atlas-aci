"""Complete same-G1 canonical atoms and deterministic whole-span inline policy."""

from __future__ import annotations

import base64
import copy
import json

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.graph import validate_hop
from atlas_aci.query.indexed import IndexedReader, QueryStopped
from atlas_aci.query.leases import digest
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-context-bundle.v1"
KINDS = ("definition", "path", "relationship", "lexical_file")
TIERS = ("exact_entity_id", "exact_qualified_label", "exact_path", "exact_name", "lexical")


def record(reader, work, kind, identifier):
    with IndexedReader(reader, max_examined_edges=0, deadline_ms=2000, accounting=work) as query:
        value = query.record(kind, identifier)
    ev.require(value is not None, "context canonical record missing")
    return value


def own(reader, work, value):
    ev.validate_record(value)
    ev.require(record(reader, work, value["type"], value["id"]) == value, "context foreign record")


def reference(reader, kind, occurrence, evidence, producer, membership):
    if occurrence is None or evidence is None:
        row = {
            "kind": kind,
            "state": "unavailable",
            "reason": "definition_unavailable",
            **membership,
        }
    else:
        ev.require(evidence["occurrence_id"] == occurrence["id"], "context occurrence link")
        ev.require(
            producer is not None and evidence["producer_id"] == producer["id"],
            "context producer link",
        )
        ev.require(
            all(
                evidence[k] == occurrence[k]
                for k in (
                    "source_sha256",
                    "source_size",
                    "start_byte",
                    "end_byte",
                    "provenance_state",
                )
            ),
            "context source link",
        )
        available = (
            reader._source_lease is not None
            and evidence["provenance_state"] == "source"
            and reader._source_lease.bindings.get((occurrence["path"], evidence["source_sha256"]))
            is True
        )
        row = {
            "kind": kind,
            "state": "available" if available else "unavailable",
            "reason": None if available else "source_unavailable",
            "path": occurrence["path"],
            **{
                k: evidence[k]
                for k in (
                    "source_sha256",
                    "source_size",
                    "start_byte",
                    "end_byte",
                    "provenance_state",
                )
            },
            "occurrence_id": occurrence["id"],
            "evidence_id": evidence["id"],
            "producer_id": producer["id"],
            **membership,
        }
    row["id"] = digest(row)
    row.update(inline=None, inline_reason="not_selected")
    return row


def bundle(kind, records, sources, *, tier, seed_rank, distance, path, owners):
    body = {"format": FORMAT, "kind": kind, "records": records, "sources": sources}
    return {
        "id": digest(body),
        **body,
        "rank": {
            "tier": tier,
            "seed_rank": seed_rank,
            "distance": distance,
            "source_unavailable": not any(s["state"] == "available" for s in sources),
            "primary_path": path,
            "owners": owners,
        },
    }


def definition(reader, candidate, work, seed_rank):
    entity = candidate["entity"]
    own(reader, work, entity)
    occurrence, evidence = candidate["occurrence"], candidate["evidence"]
    producer = None
    ev.require((occurrence is None) == (evidence is None), "context partial definition proof")
    if occurrence is not None:
        ev.require(
            occurrence is not None
            and occurrence["entity_id"] == entity["id"]
            and occurrence["kind"] == "definition",
            "context defining occurrence",
        )
        own(reader, work, occurrence)
    if evidence is not None:
        own(reader, work, evidence)
        producer = record(reader, work, "producer", evidence["producer_id"])
    source = reference(
        reader, "definition", occurrence, evidence, producer, {"entity_id": entity["id"]}
    )
    return bundle(
        "definition",
        {"entity": entity, "occurrence": occurrence, "evidence": evidence, "producer": producer},
        [source],
        tier=candidate["match_tier"],
        seed_rank=seed_rank,
        distance=0,
        path=entity["path"],
        owners=entity["owners"],
    )


def lexical_file(reader, candidate, work, seed_rank):
    with IndexedReader(reader, max_examined_edges=0, deadline_ms=2000, accounting=work) as query:
        row = query._one(
            "SELECT candidate_id,kind,provenance FROM retrieval_documents WHERE candidate_id=?",
            (candidate["candidate_id"],),
            "hydration",
        )
    ev.require(
        row is not None and row[0] == candidate["candidate_id"] and row[1] == "source_file",
        "context native document",
    )
    provenance = json.loads(row[2])
    ev.require(
        digest({"format": "atlas-retrieval.v1", **provenance}) == candidate["candidate_id"],
        "context document identity",
    )
    ev.require(
        reader._source_lease is not None
        and reader._source_lease.current.get(provenance["path"]) == provenance["source_sha256"],
        "context current file binding",
    )
    source = {
        "kind": "file",
        "state": "available",
        "reason": None,
        "document_id": candidate["candidate_id"],
        "document_provenance": provenance,
        "path": provenance["path"],
        "source_sha256": provenance["source_sha256"],
        "source_size": provenance["end_byte"],
        "start_byte": 0,
        "end_byte": provenance["end_byte"],
        "provenance_state": "source",
    }
    source["id"] = digest(source)
    source.update(inline=None, inline_reason="not_selected")
    return bundle(
        "lexical_file",
        {
            "document_id": candidate["candidate_id"],
            "provenance": provenance,
            "match_tier": candidate["match_tier"],
            "score": candidate["lexical_score"],
        },
        [source],
        tier=candidate["match_tier"],
        seed_rank=seed_rank,
        distance=0,
        path=candidate["path"],
        owners=[],
    )


def relationship(reader, proof, nodes, work, *, tier, seed_rank, distance=1):
    identities = {n["id"]: n for n in nodes}
    ev.require(
        len(nodes) == 2
        and set(identities)
        == {proof["assertion"]["source_entity_id"], proof["assertion"]["target_entity_id"]},
        "context exact endpoints",
    )
    validate_hop(
        proof,
        identities,
        {
            "direction": proof["traversal_direction"],
            "relations": [proof["assertion"]["relation"]],
            "confidence": [proof["assertion"]["resolution"]],
        },
    )
    for key in ("assertion", "evidence", "occurrence", "producer"):
        own(reader, work, proof[key])
    for node in nodes:
        own(
            reader, work, {"type": "entity", **{k: v for k, v in node.items() if k != "projection"}}
        )
    source = reference(
        reader,
        "assertion",
        proof["occurrence"],
        proof["evidence"],
        proof["producer"],
        {"assertion_id": proof["assertion"]["id"]},
    )
    return bundle(
        "relationship",
        {"nodes": nodes, "proof": proof},
        [source],
        tier=tier,
        seed_rank=seed_rank,
        distance=distance,
        path=proof["occurrence"]["path"],
        owners=identities[proof["assertion"]["source_entity_id"]]["owners"],
    )


def whole_path(reader, path, work, *, tier, seed_rank):
    ev.require(len(path["nodes"]) == len(path["hops"]) + 1, "context path extent")
    sources = []
    for index, hop in enumerate(path["hops"]):
        pair = path["nodes"][index : index + 2]
        outgoing = hop["traversal_direction"] == "outgoing"
        expected = [hop["assertion"]["source_entity_id"], hop["assertion"]["target_entity_id"]]
        if not outgoing:
            expected.reverse()
        ev.require([n["id"] for n in pair] == expected, "context connected directed path")
        atom = relationship(
            reader, hop, pair, work, tier=tier, seed_rank=seed_rank, distance=index + 1
        )
        sources.extend(atom["sources"])
    # A zero-hop exact path still carries its real endpoint and no invented proof.
    if not path["hops"]:
        for node in path["nodes"]:
            own(
                reader,
                work,
                {"type": "entity", **{k: v for k, v in node.items() if k != "projection"}},
            )
    first = path["nodes"][0]
    return bundle(
        "path",
        path,
        sources,
        tier=tier,
        seed_rank=seed_rank,
        distance=len(path["hops"]),
        path=first["path"],
        owners=first["owners"],
    )


def inline_sources(atoms, reader, work, *, policy, max_inline_bytes):
    ev.require(policy in {"references", "inline_if_complete"}, "context inline policy")
    remaining = max_inline_bytes
    cache = {}
    result = copy.deepcopy(atoms)
    for atom in result:
        for source in atom["sources"]:
            if source["state"] != "available":
                source["inline_reason"] = "source_unavailable"
            elif policy == "references":
                source["inline_reason"] = "references_policy"
            elif source["end_byte"] - source["start_byte"] > remaining:
                source["inline_reason"] = "inline_budget"
            else:
                key = (source["path"], source["source_sha256"])
                try:
                    if key not in cache:
                        work.source_read(source["source_size"])
                        cache[key] = reader.read_source(*key)
                except QueryStopped as exc:
                    if exc.reason == "context_deadline":
                        raise
                    source["inline_reason"] = "source_work_limit"
                    continue
                raw = cache[key][source["start_byte"] : source["end_byte"]]
                ev.require(
                    len(raw) == source["end_byte"] - source["start_byte"],
                    "context complete inline span",
                )
                try:
                    value = {"encoding": "utf8", "data": raw.decode("utf-8", "strict")}
                except UnicodeError:
                    value = {"encoding": "base64", "data": base64.b64encode(raw).decode()}
                source["inline"] = value
                source["inline_reason"] = None
                remaining -= len(raw)
        atom["id"] = digest({k: v for k, v in atom.items() if k not in {"id", "rank"}})
    return result


def resource_descriptor(reader, source):
    """Only exact already-authenticated atom membership, never an aggregate range."""
    if source["state"] != "available":
        return None
    lease = reader._source_lease
    if lease is None or lease.bindings.get((source["path"], source["source_sha256"])) is not True:
        raise ToolError("INVALID_RESULT", "Context source binding is not owned.")
    descriptor = {
        "format": "atlas-context-resource.v1",
        "repository": digest(str(reader.repo)),
        "generation_id": reader.generation_id,
        "snapshot_id": reader.snapshot_id,
        "source_reference_id": source["id"],
        "membership": {k: v for k, v in source.items() if k not in {"inline", "inline_reason"}},
        **{
            key: source[key]
            for key in (
                "kind",
                "path",
                "source_sha256",
                "source_size",
                "start_byte",
                "end_byte",
                "provenance_state",
            )
        },
    }
    descriptor["id"] = digest(descriptor)
    return descriptor
