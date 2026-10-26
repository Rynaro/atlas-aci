"""Closed context schemas and independent whole-record/source/fingerprint checks."""

from __future__ import annotations

import base64
import copy
import hashlib

from atlas_aci.protocol import graph as g
from atlas_aci.query.context_limits import LIMITS
from atlas_aci.storage import evidence as ev

obj, array, nullable = g.obj, g.array, g.nullable
H, S, C, B = g.HASH, g.STRING, g.COUNT, g.BOOL
CONTEXT_INPUT = obj(
    {
        "schema_version": {"const": g.VERSION},
        "op": {"const": "context"},
        "text": {"type": "string", "minLength": 1, "maxLength": 4096},
        "expected_generation_id": H,
        "repository": {"type": "string", "minLength": 1},
        "path_prefixes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 16,
        },
        "target": g.SELECTOR,
        "direction": {"enum": ["outgoing", "incoming"]},
        "relations": {
            "type": "array",
            "items": {"enum": sorted(ev.RELATIONS)},
            "minItems": 1,
            "uniqueItems": True,
        },
        "confidence": {
            "type": "array",
            "items": {"enum": ["EXTRACTED", "INFERRED"]},
            "minItems": 1,
            "uniqueItems": True,
        },
        "source_policy": {"enum": ["inline_if_complete", "references"]},
        "continuation": B,
        "max_bytes": C,
        **{
            key: {"type": "integer", "minimum": low, "maximum": high}
            for key, (_, low, high) in LIMITS.items()
        },
    }
)
CONTEXT_INPUT["required"] = ["schema_version", "op", "text"]
INLINE = nullable(obj({"encoding": {"enum": ["utf8", "base64"]}, "data": S}))
INLINE_REASON = nullable(
    {"enum": ["source_unavailable", "references_policy", "inline_budget", "source_work_limit"]}
)
FILE_PROVENANCE = obj(
    {
        "kind": {"const": "source_file"},
        "path": S,
        "language": S,
        "source_sha256": H,
        "start_byte": {"const": 0},
        "end_byte": C,
        "granularity": {"const": "file"},
        "availability": {"const": "available"},
    }
)
SOURCE_FULL = {
    "id": H,
    "state": {"enum": ["available", "unavailable"]},
    "reason": nullable(S),
    "path": S,
    **g.SOURCE_SPAN,
    "occurrence_id": H,
    "evidence_id": H,
    "producer_id": H,
    "inline": INLINE,
    "inline_reason": INLINE_REASON,
}
SOURCE_SCHEMA = {
    "oneOf": [
        obj({**SOURCE_FULL, "kind": {"const": "definition"}, "entity_id": H}),
        obj({**SOURCE_FULL, "kind": {"const": "assertion"}, "assertion_id": H}),
        obj(
            {
                "id": H,
                "kind": {"const": "definition"},
                "state": {"const": "unavailable"},
                "reason": {"const": "definition_unavailable"},
                "entity_id": H,
                "inline": {"type": "null"},
                "inline_reason": {"const": "source_unavailable"},
            }
        ),
        obj(
            {
                "id": H,
                "kind": {"const": "file"},
                "state": {"const": "available"},
                "reason": {"type": "null"},
                "document_id": H,
                "document_provenance": FILE_PROVENANCE,
                "path": S,
                "source_sha256": H,
                "source_size": C,
                "start_byte": {"const": 0},
                "end_byte": C,
                "provenance_state": {"const": "source"},
                "inline": INLINE,
                "inline_reason": INLINE_REASON,
            }
        ),
    ]
}
RESOURCE_MEMBERSHIP = copy.deepcopy(SOURCE_SCHEMA)
for variant in RESOURCE_MEMBERSHIP["oneOf"]:
    for field in ("inline", "inline_reason"):
        variant["properties"].pop(field)
        variant["required"].remove(field)
RESOURCE = obj(
    {
        "format": {"const": "atlas-context-resource.v1"},
        "id": H,
        "repository": H,
        "generation_id": H,
        "snapshot_id": H,
        "source_reference_id": H,
        "membership": RESOURCE_MEMBERSHIP,
        "kind": {"enum": ["definition", "assertion", "file"]},
        "path": S,
        "source_sha256": H,
        "source_size": C,
        "start_byte": C,
        "end_byte": C,
        "provenance_state": {"const": "source"},
        "uri": {"type": "string", "maxLength": 4096},
    }
)

RANK = obj(
    {
        "tier": {
            "enum": [
                "exact_entity_id",
                "exact_qualified_label",
                "exact_path",
                "exact_name",
                "lexical",
            ]
        },
        "seed_rank": C,
        "distance": C,
        "source_unavailable": B,
        "primary_path": S,
        "owners": g.IDENTITY["properties"]["owners"],
    }
)
RECORD_VARIANTS = {
    "definition": obj(
        {
            "entity": g.RETRIEVAL_ENTITY,
            "occurrence": nullable(g.RECORDS["occurrence"]),
            "evidence": nullable(g.RECORDS["evidence"]),
            "producer": nullable(g.RECORDS["producer"]),
        }
    ),
    "relationship": obj({"nodes": array(g.IDENTITY), "proof": g.HOP}),
    "path": obj({"nodes": array(g.IDENTITY), "hops": array(g.HOP)}),
    "lexical_file": obj(
        {
            "document_id": H,
            "provenance": FILE_PROVENANCE,
            "match_tier": S,
            "score": nullable({"type": "number"}),
        }
    ),
}
BUNDLE = {
    "oneOf": [
        obj(
            {
                "format": {"const": "atlas-context-bundle.v1"},
                "id": H,
                "kind": {"const": kind},
                "records": records,
                "sources": array(SOURCE_SCHEMA),
                "rank": RANK,
            }
        )
        for kind, records in RECORD_VARIANTS.items()
    ]
}
TOKEN_DECL = {
    "mode": {"enum": ["exact", "estimated"]},
    "algorithm": {"type": "string", "minLength": 1, "maxLength": 64},
    "version": {"type": "string", "minLength": 1, "maxLength": 64},
    "domain": {"const": "negotiated_mcp_result_utf8_excluding_jsonrpc"},
}
QUERY = copy.deepcopy(CONTEXT_INPUT)
# A lossless normalized output representation; set fields are explicit membership maps.
QUERY["properties"].pop("schema_version")
QUERY["properties"].pop("op")
QUERY["properties"].pop("expected_generation_id")
QUERY["properties"].update(
    target=nullable(g.SELECTOR),
    repository=nullable(S),
    constraint_representation={"const": "set_membership_maps"},
)
for key in ("path_prefixes", "relations", "confidence"):
    QUERY["properties"][key] = {
        "type": "object",
        "additionalProperties": {"const": True},
        "maxProperties": 16,
    }
QUERY["required"] = list(QUERY["properties"])
COUNTS = obj(
    {
        key: C
        for key in (
            "vm_operations_reserved",
            "examined_edges",
            "proof_queries",
            "source_reads",
            "source_bytes",
            "sql_queries",
            "vm_progress_callbacks",
        )
    }
)
FRONTIER = obj(
    {
        "seed_id": H,
        "deadline_scope": {"const": "configured_shared_context"},
        "outcome": S,
        "stop_reason": nullable(S),
        "frontier": obj({"commitment": H, "queued_nodes": C, "depth": C}),
        "work": g.WORK,
        "effective_limits": obj({key: C for key in g.LIMITS}),
        "selection_commitment": H,
        "target_state": nullable(S),
    }
)
COVERAGE = copy.deepcopy(g.RETRIEVAL_DATA_SCHEMA["properties"]["coverage"])
COVERAGE["properties"].pop("unavailable_count")
COVERAGE["required"].remove("unavailable_count")
CONTEXT_DATA = obj(
    {
        "projection": {"const": "task_context"},
        "format": {"const": "atlas-context-record.v1"},
        "request": QUERY,
        "repository": S,
        "generation_id": H,
        "snapshot_id": nullable(H),
        "ranker": {"const": "atlas-context-diversity.v1"},
        "tokenizer": obj(TOKEN_DECL),
        "token_usage": obj(
            {**TOKEN_DECL, "count": {"type": "integer", "minimum": 0, "maximum": 2**63 - 1}}
        ),
        "bundles": array(BUNDLE),
        "effective_limits": obj({**{key: C for key in LIMITS}, "max_bytes": C}),
        "coverage": COVERAGE,
        "retrieval": obj(
            {
                "query_digest": H,
                "returned_count": C,
                "truncated": B,
                "truncation_reason": nullable(S),
                "diagnostic_reason": S,
            }
        ),
        "frontiers": array(FRONTIER),
        "work": COUNTS,
        "collection_stop": nullable(S),
        "selected_range": obj({"offset": C, "count": C}),
        "selection": obj(
            {
                "total_count": nullable(C),
                "has_more": B,
                "reason": nullable({"enum": ["byte_limit", "element_limit"]}),
            }
        ),
        "source_resource_ids": {
            "type": "object",
            "propertyNames": H,
            "additionalProperties": {"const": True},
        },
        "retrieval_record_id": H,
        "query_digest": H,
        "resources": array(RESOURCE),
        "offset": C,
        "returned_count": C,
        "total_count": nullable(C),
        "has_more": B,
        "next_cursor": nullable({"type": "string", "maxLength": 2048}),
        "selection_truncated": B,
        "selection_reason": nullable({"enum": ["byte_limit", "element_limit"]}),
    }
)


def source_membership(kind, membership, occurrence, evidence, producer):
    if any(
        evidence[key] != occurrence[key]
        for key in ("source_sha256", "source_size", "start_byte", "end_byte", "provenance_state")
    ):
        raise ValueError("Context source evidence mismatch")
    return {
        "kind": kind,
        **membership,
        "path": occurrence["path"],
        **{
            key: evidence[key]
            for key in (
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
    }


def validate_context(payload, operation, element_limit):
    if payload.get("operation") != operation or not g.STRUCTURED_VALIDATOR.is_valid(payload):
        raise ValueError("Invalid context operation/schema")
    if payload["status"] == "error":
        return
    from atlas_aci.query.admission import lists
    from atlas_aci.query.leases import digest

    if any(len(items) > element_limit for items in lists(payload["data"])):
        raise ValueError("Context exceeds recursive element bound")
    data = payload["data"]
    if (
        data["generation_id"] != payload["generation_id"]
        or data["snapshot_id"] != payload["snapshot_id"]
    ):
        raise ValueError("Context generation mismatch")
    if data["returned_count"] != len(data["bundles"]) or data["selected_range"] != {
        "offset": data["offset"],
        "count": len(data["bundles"]),
    }:
        raise ValueError("Context extent mismatch")
    if data["tokenizer"] != {k: data["token_usage"][k] for k in TOKEN_DECL}:
        raise ValueError("Context token declaration mismatch")
    limits = data["effective_limits"]
    for counter, limit in (
        ("examined_edges", "max_examined_edges"),
        ("vm_operations_reserved", "max_vm_operations"),
    ):
        if data["work"][counter] > limits[limit]:
            raise ValueError("Context shared work bound")
    if len(data["frontiers"]) > limits["max_seeds"] or len(data["bundles"]) > limits["max_bundles"]:
        raise ValueError("Context collection extent")
    if any(
        frontier["effective_limits"]["deadline_ms"] != limits["deadline_ms"]
        for frontier in data["frontiers"]
    ):
        raise ValueError("Context configured shared deadline mismatch")
    for atom in data["bundles"]:
        if atom["id"] != digest({k: v for k, v in atom.items() if k not in {"id", "rank"}}):
            raise ValueError("Context bundle identity mismatch")
        records = atom["records"]
        expected_sources = []
        if atom["kind"] == "definition":
            ev.validate_record(records["entity"])
            if (records["evidence"] is None) != (records["occurrence"] is None) or (
                records["evidence"] is None
            ) != (records["producer"] is None):
                raise ValueError("Partial definition proof")
            if records["evidence"] is None:
                expected_sources.append(
                    {
                        "kind": "definition",
                        "entity_id": records["entity"]["id"],
                        "reason": "definition_unavailable",
                        "state": "unavailable",
                    }
                )
            if records["evidence"] is not None:
                for key in ("occurrence", "evidence", "producer"):
                    ev.validate_record(records[key])
                evidence, occurrence = records["evidence"], records["occurrence"]
                if (
                    evidence["occurrence_id"] != occurrence["id"]
                    or occurrence["entity_id"] != records["entity"]["id"]
                    or evidence["producer_id"] != records["producer"]["id"]
                ):
                    raise ValueError("Disconnected context definition")
                if (
                    occurrence["kind"] != "definition"
                    or occurrence["path"] != records["entity"]["path"]
                ):
                    raise ValueError("Not a defining occurrence")
                expected_sources.append(
                    source_membership(
                        "definition",
                        {"entity_id": records["entity"]["id"]},
                        occurrence,
                        evidence,
                        records["producer"],
                    )
                )
        elif atom["kind"] in {"relationship", "path"}:
            nodes = records["nodes"]
            hops = [records["proof"]] if atom["kind"] == "relationship" else records["hops"]
            identities = {n["id"]: n for n in nodes}
            for node in nodes:
                ev.validate_record(
                    {"type": "entity", **{k: v for k, v in node.items() if k != "projection"}}
                )
            if atom["kind"] == "path" and len(nodes) != len(hops) + 1:
                raise ValueError("Disconnected context path extent")
            if atom["kind"] == "relationship" and [n["id"] for n in nodes] != [
                records["proof"]["assertion"][key]
                for key in ("source_entity_id", "target_entity_id")
            ]:
                raise ValueError("Disconnected context endpoints")
            for index, hop in enumerate(hops):
                if atom["kind"] == "path":
                    pair = [
                        hop["assertion"][key] for key in ("source_entity_id", "target_entity_id")
                    ]
                    if hop["traversal_direction"] == "incoming":
                        pair.reverse()
                    if [node["id"] for node in nodes[index : index + 2]] != pair:
                        raise ValueError("Disconnected directed context path")
                expected_sources.append(
                    source_membership(
                        "assertion",
                        {"assertion_id": hop["assertion"]["id"]},
                        hop["occurrence"],
                        hop["evidence"],
                        hop["producer"],
                    )
                )
                g.validate_hop(
                    hop,
                    identities,
                    {
                        "direction": hop["traversal_direction"],
                        "relations": [hop["assertion"]["relation"]],
                        "confidence": [hop["assertion"]["resolution"]],
                    },
                )
        else:
            if records["document_id"] != digest(
                {"format": "atlas-retrieval.v1", **records["provenance"]}
            ):
                raise ValueError("Context file identity mismatch")
            provenance = records["provenance"]
            expected_sources.append(
                {
                    "kind": "file",
                    "state": "available",
                    "reason": None,
                    "document_id": records["document_id"],
                    "document_provenance": provenance,
                    "path": provenance["path"],
                    "source_sha256": provenance["source_sha256"],
                    "source_size": provenance["end_byte"],
                    "start_byte": 0,
                    "end_byte": provenance["end_byte"],
                    "provenance_state": "source",
                }
            )
        if len(atom["sources"]) != len(expected_sources):
            raise ValueError("Context source membership extent")
        for source, expected in zip(atom["sources"], expected_sources, strict=True):
            if any(source.get(key) != value for key, value in expected.items()):
                raise ValueError("Context source membership mismatch")
            if (
                source["kind"] != "file"
                and "path" in expected
                and source["state"] == "available"
                and source["provenance_state"] != "source"
            ):
                raise ValueError("Context unavailable provenance")
        for source in atom["sources"]:
            stable = {k: v for k, v in source.items() if k not in {"id", "inline", "inline_reason"}}
            if source["id"] != digest(stable):
                raise ValueError("Context source identity mismatch")
            if source["inline"] is not None:
                value = source["inline"]
                raw = (
                    value["data"].encode("utf-8")
                    if value["encoding"] == "utf8"
                    else base64.b64decode(value["data"], validate=True)
                )
                if (
                    source["state"] != "available"
                    or len(raw) != source["end_byte"] - source["start_byte"]
                ):
                    raise ValueError("Incomplete inline source")
                if (
                    source["start_byte"] == 0
                    and source["end_byte"] == source["source_size"]
                    and hashlib.sha256(raw).hexdigest() != source["source_sha256"]
                ):
                    raise ValueError("Context inline source hash mismatch")
    from atlas_aci.protocol.resources import parse_uri

    references = {source["id"]: source for atom in data["bundles"] for source in atom["sources"]}
    if data["selection"] != {
        "total_count": data["total_count"],
        "has_more": data["has_more"],
        "reason": data["selection_reason"],
    }:
        raise ValueError("Context selection domain mismatch")
    expected_resource_ids = {}
    for source in references.values():
        if source["state"] != "available":
            continue
        descriptor = {
            "format": "atlas-context-resource.v1",
            "repository": digest(data["repository"]),
            "generation_id": data["generation_id"],
            "snapshot_id": data["snapshot_id"],
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
        expected_resource_ids[digest(descriptor)] = True
    if expected_resource_ids != data["source_resource_ids"]:
        raise ValueError("Context stable resource domain mismatch")
    descriptor_ids = set()
    for descriptor in data["resources"]:
        identifier = descriptor["id"]
        if identifier in descriptor_ids or identifier != digest(
            {k: v for k, v in descriptor.items() if k not in {"id", "uri"}}
        ):
            raise ValueError("Context resource identity mismatch")
        descriptor_ids.add(identifier)
        source = references.get(descriptor["source_reference_id"])
        if (
            source is None
            or source["state"] != "available"
            or descriptor["membership"]
            != {k: v for k, v in source.items() if k not in {"inline", "inline_reason"}}
        ):
            raise ValueError("Context resource membership mismatch")
        if (
            descriptor["repository"] != digest(data["repository"])
            or descriptor["generation_id"] != data["generation_id"]
            or descriptor["snapshot_id"] != data["snapshot_id"]
            or any(
                descriptor[key] != source[key]
                for key in (
                    "kind",
                    "path",
                    "source_sha256",
                    "source_size",
                    "start_byte",
                    "end_byte",
                    "provenance_state",
                )
            )
        ):
            raise ValueError("Context foreign resource span")
        if parse_uri(descriptor["uri"])[0] != identifier:
            raise ValueError("Context resource URI mismatch")
    if data["has_more"] != (data["next_cursor"] is not None):
        raise ValueError("Context continuation mismatch")
    # Reconstruct the exact explicit canonical domain, never drop arbitrary fields.
    keys = (
        "format",
        "request",
        "repository",
        "generation_id",
        "snapshot_id",
        "ranker",
        "tokenizer",
        "bundles",
        "effective_limits",
        "coverage",
        "retrieval",
        "frontiers",
        "work",
        "collection_stop",
        "selected_range",
        "selection",
        "source_resource_ids",
    )
    domain = {key: data[key] for key in keys}
    if digest(domain) != data["retrieval_record_id"]:
        raise ValueError("Context retrieval record identity mismatch")
