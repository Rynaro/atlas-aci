"""atlas.graph.v1: published generation projections, never live diagnostics."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from atlas_aci.enforcement import ToolError
from atlas_aci.query.traversal import LIMITS
from atlas_aci.storage import evidence as ev

VERSION = "atlas.graph.v1"
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
STRING = {"type": "string"}
BOOL = {"type": "boolean"}
COUNT = {"type": "integer", "minimum": 0}
RESERVED = ("schema_version", "op", "expected_generation_id")


def obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def array(schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": schema}


LEGACY_INPUT = {
    "type": "object",
    "required": ["query"],
    "properties": {"query": STRING},
    "not": {"anyOf": [{"required": [key]} for key in RESERVED]},
}
STRUCTURED_INPUT = obj(
    {
        "schema_version": {"const": VERSION},
        "op": {"const": "status"},
        "expected_generation_id": HASH,
    }
)
STRUCTURED_INPUT["required"] = ["schema_version", "op"]
SELECTOR = {"oneOf": [obj({"entity_id": HASH}), obj({"name": {"type": "string", "minLength": 1}})]}
NAV_INPUTS = []
for operation in ("neighborhood", "path"):
    schema = obj(
        {
            "schema_version": {"const": VERSION},
            "op": {"const": operation},
            "expected_generation_id": HASH,
            "seed": SELECTOR,
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
            "max_bytes": {"type": "integer", "minimum": 0},
            **{
                name: {"type": "integer", "minimum": lo, "maximum": hi}
                for name, (_, lo, hi) in LIMITS.items()
            },
            **({"target": SELECTOR} if operation == "path" else {}),
        }
    )
    schema["required"] = ["schema_version", "op", "seed"] + (
        ["target"] if operation == "path" else []
    )
    NAV_INPUTS.append(schema)
GRAPH_INPUT_SCHEMA = {
    "type": "object",
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "oneOf": [LEGACY_INPUT, STRUCTURED_INPUT, *NAV_INPUTS],
}
LOCATION = obj({key: COUNT for key in ("line", "column", "end_line", "end_column")})
STATE_REASON = obj({"state": STRING, "reason": STRING})
LANGUAGE = obj(
    {
        "language": STRING,
        "extensions": array(STRING),
        "definitions": array(STRING),
        "relations": array(STRING),
        "extraction": STATE_REASON,
        "semantic_binding": STATE_REASON,
    }
)
PRODUCER = obj(
    {
        "codegraph_sha256": HASH,
        "queries_sha256": HASH,
        "modules": {
            "type": "object",
            "properties": {
                name: HASH
                for name in (
                    "storage/evidence.py",
                    "providers/python_imports.py",
                    "indexing/extraction_cache.py",
                    "resolvers/dependencies.py",
                    "extractors/rust.py",
                    "storage/snapshots.py",
                )
            },
            "additionalProperties": False,
        },
        "packages": obj({"tree-sitter": STRING, "tree-sitter-language-pack": STRING}),
    }
)
PRODUCER["required"] = ["codegraph_sha256", "queries_sha256", "packages"]
CAPABILITIES = obj(
    {
        "representation": {"const": "bounded_projection"},
        "stored_format": STRING,
        "languages": array(LANGUAGE),
        "producers": array(
            obj({"name": STRING, "operation": STRING, "semantic_binding": STATE_REASON})
        ),
        "limitations": nullable(array(obj({"name": STRING, "reason": STRING}))),
    }
)
FILE = obj(
    {
        "path": STRING,
        "observation_id": nullable(HASH),
        "history_id": nullable(HASH),
        "language": nullable(STRING),
        "state": {"enum": ["indexed", "failed", "disabled", "unsupported", "unknown"]},
        "observed_source_sha256": nullable(HASH),
        "fact_source_sha256s": array(HASH),
        "stale": nullable(BOOL),
        "retained_facts": BOOL,
        "reason": nullable(STRING),
        "location": nullable(LOCATION),
    }
)
REFERENCE = obj(
    {
        "path": STRING,
        "source_sha256": HASH,
        "provenance": {"enum": ["observed_source", "historical_indexed"]},
        "extraction_state": STRING,
        "stale": nullable(BOOL),
        "available": BOOL,
    }
)
STATUS_DATA_SCHEMA = obj(
    {
        "facts": nullable(
            obj(
                {
                    key: COUNT
                    for key in (
                        "definitions",
                        "references",
                        "edges",
                        "rationale",
                        "historical_bundles",
                    )
                }
            )
        ),
        "coverage_basis": {"const": "published_generation"},
        "live_source_verification": {"const": "not_performed"},
        "latest_attempt_included": {"const": False},
        "runtime_capabilities_included": {"const": False},
        "indexed": obj(
            {
                "representation": {"const": "bounded_projection"},
                "configuration": nullable(
                    obj({"languages": array(STRING), "skip_patterns": array(STRING)})
                ),
                "producer": nullable(PRODUCER),
                "capabilities": nullable(CAPABILITIES),
            }
        ),
        "completeness": obj(
            {
                "state": {"enum": ["complete", "partial", "unknown", "unavailable"]},
                "reason": STRING,
                "enumeration": {"enum": ["complete", "unknown"]},
                "counts": nullable(
                    obj({key: COUNT for key in ("indexed", "failed", "disabled", "unsupported")})
                ),
                "total_count": nullable(COUNT),
                "stale_count": nullable(COUNT),
                "files": array(FILE),
            }
        ),
        "source": obj(
            {
                "state": {"enum": ["available", "unavailable"]},
                "reason": nullable(STRING),
                "total_count": COUNT,
                "references": array(REFERENCE),
            }
        ),
    }
)
BOUNDS_SCHEMA = obj(
    {
        "element_limit": COUNT,
        "truncated": BOOL,
        "lists": {
            "type": "object",
            "additionalProperties": obj(
                {"total_count": COUNT, "returned_count": COUNT, "truncated": BOOL}
            ),
        },
    }
)
IDENTITY = obj(
    {
        "projection": {"const": "entity_identity"},
        "id": HASH,
        "name": STRING,
        "kind": STRING,
        "language": STRING,
        "path": STRING,
        "owners": array(obj({"kind": STRING, "name": nullable(STRING), "ordinal": COUNT})),
        "identity_basis": STRING,
        "ordinal": COUNT,
    }
)
SELECTION = obj(
    {
        "selector_kind": {"enum": ["name", "entity_id"]},
        "state": {"enum": ["unresolved", "absent", "unique", "ambiguous"]},
        "candidates": array(IDENTITY),
        "candidates_complete": BOOL,
        "entity_id": nullable(HASH),
    }
)
# Canonical records keep their closed field sets. validate_navigation additionally
# runs the authoritative typed-record validator and checks every proof link.
PROVENANCE = {"enum": ["source", "legacy_unavailable"]}
SOURCE_SPAN = {
    "source_sha256": nullable(HASH),
    "source_size": nullable(COUNT),
    "start_byte": nullable(COUNT),
    "end_byte": nullable(COUNT),
    "provenance_state": PROVENANCE,
}
RECORDS = {
    "assertion": obj(
        {
            "type": {"const": "assertion"},
            "id": HASH,
            "source_entity_id": HASH,
            "target_entity_id": nullable(HASH),
            "relation": {"enum": sorted(ev.RELATIONS)},
            "evidence_id": HASH,
            "resolution": {"enum": ["EXTRACTED", "INFERRED"]},
            "candidate_ids": array(HASH),
            "raw_target": STRING,
            "unresolved_reason": {"type": "null"},
        }
    ),
    "evidence": obj(
        {
            "type": {"const": "evidence"},
            "id": HASH,
            "occurrence_id": HASH,
            "producer_id": HASH,
            **SOURCE_SPAN,
        }
    ),
    "occurrence": obj(
        {
            "type": {"const": "occurrence"},
            "id": HASH,
            "entity_id": nullable(HASH),
            "kind": STRING,
            "path": STRING,
            "language": STRING,
            "role": STRING,
            **SOURCE_SPAN,
            **{key: nullable(COUNT) for key in ("line", "column", "end_line", "end_column")},
        }
    ),
    "producer": obj(
        {
            "type": {"const": "producer"},
            "id": HASH,
            "identity_basis": {"enum": ["parsed", "legacy_metadata", "legacy_unavailable"]},
            "fingerprint": nullable(PRODUCER),
        }
    ),
}
HOP = obj(
    {
        "projection": {"const": "canonical_assertion_proof"},
        "traversal_direction": {"enum": ["outgoing", "incoming"]},
        **RECORDS,
        "source_availability": {"enum": ["available", "unavailable"]},
    }
)
WORK = obj(
    {
        key: COUNT
        for key in (
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
)
NAV_COMMON = {
    "scope": {"const": "current_published_evidence"},
    "outcome": {"enum": ["complete", "partial", "disambiguation", "no_path", "no_evidence"]},
    "stop_reason": nullable(
        {
            "enum": [
                "deadline",
                "depth_limit",
                "visited_node_limit",
                "examined_edge_limit",
                "byte_limit",
                "element_limit",
            ]
        }
    ),
    "effective_limits": obj({key: COUNT for key in LIMITS}),
    "seed": SELECTION,
    "direction": {"enum": ["outgoing", "incoming"]},
    "relations": array({"enum": sorted(ev.RELATIONS)}),
    "confidence": array({"enum": ["EXTRACTED", "INFERRED"]}),
    "work": WORK,
    "frontier": obj({"commitment": HASH, "queued_nodes": COUNT, "depth": COUNT}),
    "unknown_relations_traversed": {"const": False},
}
NAV_DATA = {
    "neighborhood": obj({**NAV_COMMON, "nodes": array(IDENTITY), "edges": array(HOP)}),
    "path": obj(
        {
            **NAV_COMMON,
            "target": nullable(SELECTION),
            "path": nullable(obj({"nodes": array(IDENTITY), "hops": array(HOP)})),
        }
    ),
}
ERROR_SCHEMA = obj({"code": STRING, "message": STRING, "retry_hint": STRING})
ENVELOPE_SCHEMA = obj(
    {
        "schema_version": {"const": VERSION},
        "operation": {"enum": ["status", "neighborhood", "path"]},
        "status": {"enum": ["ok", "error"]},
        "generation_id": nullable(HASH),
        "snapshot_id": nullable(HASH),
        "evidence_status": {"enum": ["available", "partial", "unknown", "unavailable"]},
        "data": {"anyOf": [STATUS_DATA_SCHEMA, *NAV_DATA.values(), {"type": "null"}]},
        "error": nullable(ERROR_SCHEMA),
        "bounds": BOUNDS_SCHEMA,
    }
)
ENVELOPE_SCHEMA["oneOf"] = [
    {
        "properties": {
            "operation": {"const": operation},
            "status": {"const": "ok"},
            "data": schema,
            "error": {"type": "null"},
        }
    }
    for operation, schema in {"status": STATUS_DATA_SCHEMA, **NAV_DATA}.items()
] + [
    {"properties": {"status": {"const": "error"}, "data": {"type": "null"}, "error": ERROR_SCHEMA}}
]
LEGACY_OUTPUT_SCHEMA = {
    "type": "object",
    "not": {"anyOf": [{"required": [key]} for key in (*RESERVED, "operation")]},
}
GRAPH_OUTPUT_SCHEMA = {
    "type": "object",
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "oneOf": [ENVELOPE_SCHEMA, LEGACY_OUTPUT_SCHEMA],
}
INPUT_VALIDATOR = Draft202012Validator(GRAPH_INPUT_SCHEMA)
STRUCTURED_VALIDATOR = Draft202012Validator(ENVELOPE_SCHEMA)
LEGACY_VALIDATOR = Draft202012Validator(LEGACY_OUTPUT_SCHEMA)


def request_mode(arguments: Any) -> str:
    if not isinstance(arguments, dict):
        raise ToolError("INVALID_INPUT", "Tool arguments must match the published schema.")
    structured = any(key in arguments for key in RESERVED)
    if structured and "query" in arguments:
        raise ToolError("MIXED_REQUEST", "Legacy and structured fields cannot be mixed.")
    if structured and arguments.get("schema_version") != VERSION:
        raise ToolError("UNSUPPORTED_SCHEMA_VERSION", "Unsupported graph schema version.")
    if structured and (
        not isinstance(arguments.get("op"), str)
        or arguments.get("op") not in {"status", "neighborhood", "path"}
    ):
        raise ToolError("INVALID_OPERATION", "Unsupported graph operation.")
    if structured and any(
        key in arguments and type(arguments[key]) is not int for key in (*LIMITS, "max_bytes")
    ):
        raise ToolError(
            "INVALID_INPUT", "Traversal limits must be integers, not booleans or floats."
        )
    if not INPUT_VALIDATOR.is_valid(arguments):
        raise ToolError("INVALID_INPUT", "Tool arguments must match the published schema.")
    return "structured" if structured else "legacy"


def envelope(
    data: dict[str, Any] | None,
    *,
    generation_id: str | None = None,
    snapshot_id: str | None = None,
    evidence_status: str = "unavailable",
    error: dict[str, str] | None = None,
    operation: str = "status",
) -> dict[str, Any]:
    return {
        "schema_version": VERSION,
        "operation": operation,
        "status": "error" if error else "ok",
        "generation_id": generation_id,
        "snapshot_id": snapshot_id,
        "evidence_status": evidence_status,
        "data": data,
        "error": error,
        "bounds": {"element_limit": 0, "truncated": False, "lists": {}},
    }


def bound_status(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    """Project JSON paths independently: shared Python references are not wire aliases."""
    if type(limit) is not int or limit < 0:
        raise ToolError("INVALID_BOUNDS", "Configured element limit must be nonnegative.")
    records: dict[str, Any] = {}

    def project(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            return {key: project(child, path + "/" + key) for key, child in value.items()}
        if isinstance(value, list):
            total = len(value)
            prefix = value[:limit]
            records[path] = {
                "total_count": total,
                "returned_count": len(prefix),
                "truncated": total > len(prefix),
            }
            return [project(child, path + "/" + str(index)) for index, child in enumerate(prefix)]
        return value

    data = project(payload["data"], "/data")
    return {
        **payload,
        "data": data,
        "bounds": {
            "element_limit": limit,
            "truncated": any(row["truncated"] for row in records.values()),
            "lists": records,
        },
    }


def no_evidence_data() -> dict[str, Any]:
    """Actual absent index, not an empty but supposedly complete source scan."""
    return {
        "coverage_basis": "published_generation",
        "live_source_verification": "not_performed",
        "latest_attempt_included": False,
        "runtime_capabilities_included": False,
        "indexed": {
            "representation": "bounded_projection",
            "configuration": None,
            "producer": None,
            "capabilities": None,
        },
        "completeness": {
            "state": "unavailable",
            "reason": "index_absent",
            "enumeration": "unknown",
            "counts": None,
            "total_count": None,
            "stale_count": None,
            "files": [],
        },
        "source": {
            "state": "unavailable",
            "reason": "index_absent",
            "total_count": 0,
            "references": [],
        },
        "facts": None,
    }


def validate_navigation(payload, operation, element_limit):
    """Validate admitted whole graph projections; never clip a successful proof."""
    if payload.get("operation") != operation or not STRUCTURED_VALIDATOR.is_valid(payload):
        raise ValueError("Invalid operation-specific navigation result")
    if payload["status"] == "error":
        return
    data = payload["data"]
    if (data["outcome"] == "partial") != (data["stop_reason"] is not None):
        raise ValueError("Invalid traversal frontier status")
    if data["work"]["visited_nodes"] > data["effective_limits"]["max_visited_nodes"]:
        raise ValueError("Visited-node budget exceeded")
    if data["work"]["examined_edges"] > data["effective_limits"]["max_examined_edges"]:
        raise ValueError("Examined-edge budget exceeded")
    nodes = data.get("nodes", [])
    hops = data.get("edges", [])
    if operation == "path" and data["path"] is not None:
        if data["outcome"] != "complete":
            raise ValueError("Partial path must be null")
        nodes, hops = data["path"]["nodes"], data["path"]["hops"]
        if len(nodes) != len(hops) + 1:
            raise ValueError("Incomplete path proof")
    from atlas_aci.query.admission import lists

    if any(len(items) > element_limit for items in lists({"nodes": nodes, "hops": hops})):
        raise ValueError("Whole proof exceeds element cap")
    for name in ("seed", "target"):
        if data.get(name) is not None and any(
            len(items) > element_limit for items in lists(data[name])
        ):
            raise ValueError("Endpoint candidate exceeds element cap")
    identities = {node["id"]: node for node in nodes}
    if len(identities) != len(nodes):
        raise ValueError("Duplicate returned entity identity")
    for selection_name in ("seed", "target"):
        selection = data.get(selection_name)
        if selection is None:
            continue
        unique = selection["state"] == "unique"
        if unique != (selection["entity_id"] is not None):
            raise ValueError("Endpoint uniqueness contradiction")
        if unique and any(c["id"] != selection["entity_id"] for c in selection["candidates"]):
            raise ValueError("Endpoint candidate identity contradiction")
        for candidate in selection["candidates"]:
            ev.validate_record(
                {"type": "entity", **{k: v for k, v in candidate.items() if k != "projection"}}
            )
    for node in nodes:
        ev.validate_record(
            {"type": "entity", **{k: v for k, v in node.items() if k != "projection"}}
        )
    if nodes and nodes[0]["id"] != data["seed"]["entity_id"]:
        raise ValueError("Traversal root differs from selected seed")
    if (
        operation == "path"
        and data["path"] is not None
        and nodes[-1]["id"] != data["target"]["entity_id"]
    ):
        raise ValueError("Path endpoint differs from selected target")
    for hop in hops:
        for kind in ("assertion", "evidence", "occurrence", "producer"):
            ev.validate_record(hop[kind])
        assertion, evidence, occurrence = (
            hop[key] for key in ("assertion", "evidence", "occurrence")
        )
        if (
            assertion["evidence_id"] != evidence["id"]
            or evidence["occurrence_id"] != occurrence["id"]
            or evidence["producer_id"] != hop["producer"]["id"]
            or assertion["resolution"] not in data["confidence"]
            or assertion["relation"] not in data["relations"]
            or hop["traversal_direction"] != data["direction"]
            or not {assertion["source_entity_id"], assertion["target_entity_id"]}
            <= identities.keys()
        ):
            raise ValueError("Disconnected canonical proof")
        source = identities[assertion["source_entity_id"]]
        target = identities[assertion["target_entity_id"]]
        legacy = evidence["provenance_state"] == "legacy_unavailable"
        if (
            source["path"] != occurrence["path"]
            or source["language"] != occurrence["language"]
            or (
                occurrence["entity_id"] != source["id"]
                and not (legacy and occurrence["entity_id"] is None)
            )
            or legacy != (source["identity_basis"] == "legacy_projection")
            or legacy != (target["identity_basis"] == "legacy_projection")
            or (not legacy) != (hop["producer"]["identity_basis"] == "parsed")
        ):
            raise ValueError("Assertion source ownership/provenance contradiction")
        if any(
            evidence[key] != occurrence[key]
            for key in (
                "source_sha256",
                "source_size",
                "start_byte",
                "end_byte",
                "provenance_state",
            )
        ):
            raise ValueError("Inconsistent source proof")
    if operation == "path" and data["path"] is not None:
        for source, hop, target in zip(nodes[:-1], hops, nodes[1:], strict=True):
            assertion = hop["assertion"]
            expected = (assertion["source_entity_id"], assertion["target_entity_id"])
            if data["direction"] == "incoming":
                expected = expected[::-1]
            if (source["id"], target["id"]) != expected:
                raise ValueError("Path direction contradiction")
