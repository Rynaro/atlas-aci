"""atlas.graph.v1: published generation projections, never live diagnostics."""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from atlas_aci.enforcement import ToolError
from atlas_aci.query.retrieval import LIMITS as RETRIEVAL_LIMITS
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
PAGE_INPUT = obj(
    {
        "schema_version": {"const": VERSION},
        "op": {"const": "page"},
        "query": {"oneOf": NAV_INPUTS},
        "page_max_bytes": {"type": "integer", "minimum": 1},
        "expected_generation_id": HASH,
    }
)
PAGE_INPUT["required"] = ["schema_version", "op", "query", "page_max_bytes"]
CONTINUE_INPUT = obj(
    {
        "schema_version": {"const": VERSION},
        "op": {"const": "continue"},
        "cursor": {"type": "string", "minLength": 1, "maxLength": 2048},
        "query_digest": HASH,
    }
)
RETRIEVE_INPUT = obj(
    {
        "schema_version": {"const": VERSION},
        "op": {"const": "retrieve"},
        "text": {"type": "string", "minLength": 1, "maxLength": 4096},
        "expected_generation_id": HASH,
        "repository": {"type": "string", "minLength": 1},
        "path_prefixes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 16,
        },
        "max_bytes": {"type": "integer", "minimum": 0},
        **{
            key: {"type": "integer", "minimum": low, "maximum": high}
            for key, (_, low, high) in RETRIEVAL_LIMITS.items()
        },
    }
)
RETRIEVE_INPUT["required"] = ["schema_version", "op", "text"]
GRAPH_INPUT_SCHEMA = {
    "type": "object",
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "oneOf": [
        LEGACY_INPUT,
        STRUCTURED_INPUT,
        *NAV_INPUTS,
        PAGE_INPUT,
        CONTINUE_INPUT,
        RETRIEVE_INPUT,
    ],
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
                    "indexing/retrieval.py",
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
CAPTURED_COMMON = copy.deepcopy(NAV_COMMON)
CAPTURED_COMMON["stop_reason"] = nullable(
    {
        "enum": [
            "deadline",
            "depth_limit",
            "visited_node_limit",
            "examined_edge_limit",
            "byte_limit",
            "element_limit",
            "retained_collection_limit",
        ]
    }
)
CAPTURED_SCHEMA = {
    "oneOf": [
        obj({**CAPTURED_COMMON, "query_operation": {"const": "neighborhood"}}),
        obj(
            {**CAPTURED_COMMON, "query_operation": {"const": "path"}, "target": nullable(SELECTION)}
        ),
    ]
}
RESOURCE_DESCRIPTOR = obj(
    {
        "id": HASH,
        "repository": HASH,
        "generation_id": HASH,
        "snapshot_id": HASH,
        "path": STRING,
        "source_sha256": HASH,
        "source_size": COUNT,
        "start_byte": COUNT,
        "end_byte": COUNT,
        "evidence_id": HASH,
        "occurrence_id": HASH,
        "provenance_state": {"const": "source"},
        "uri": {"type": "string", "maxLength": 4096},
    }
)
ATOM_SCHEMA = {
    "oneOf": [
        obj({"kind": {"const": "node"}, "node": IDENTITY, "resources": array(HASH)}),
        obj(
            {
                "kind": {"const": "edge"},
                "proof": HOP,
                "nodes": array(IDENTITY),
                "resources": array(HASH),
            }
        ),
        obj(
            {
                "kind": {"const": "path"},
                "path": obj({"nodes": array(IDENTITY), "hops": array(HOP)}),
                "resources": array(HASH),
            }
        ),
    ]
}
PAGE_DATA_SCHEMA = obj(
    {
        "projection": {"const": "captured_result_page"},
        "query_digest": HASH,
        "lease_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "captured": CAPTURED_SCHEMA,
        "items": array(ATOM_SCHEMA),
        "resources": array(RESOURCE_DESCRIPTOR),
        "offset": COUNT,
        "returned_count": COUNT,
        "total_count": COUNT,
        "has_more": BOOL,
        "next_cursor": nullable({"type": "string", "minLength": 1, "maxLength": 2048}),
        "setup_proof_reads": COUNT,
    }
)
RETRIEVAL_ENTITY = obj(
    {
        "type": {"const": "entity"},
        **{k: v for k, v in IDENTITY["properties"].items() if k != "projection"},
    }
)
RETRIEVAL_SOURCE = {
    "oneOf": [
        obj(
            {
                "state": {"const": "available"},
                "path": STRING,
                "source_sha256": HASH,
                "start_byte": COUNT,
                "end_byte": COUNT,
                "source_size": COUNT,
                "granularity": {"enum": ["file", "occurrence"]},
            }
        ),
        obj({"state": {"const": "unavailable"}, "reason": STRING}),
    ]
}
RETRIEVAL_CANDIDATE = obj(
    {
        "candidate_id": HASH,
        "kind": {"enum": ["entity", "source_file"]},
        "match_tier": {
            "enum": [
                "exact_entity_id",
                "exact_qualified_label",
                "exact_path",
                "exact_name",
                "lexical",
            ]
        },
        "lexical_score": nullable({"type": "number"}),
        "path": STRING,
        "language": STRING,
        "qualified_label": nullable(STRING),
        "entity": nullable(RETRIEVAL_ENTITY),
        "occurrence": nullable(RECORDS["occurrence"]),
        "evidence": nullable(RECORDS["evidence"]),
        "source": RETRIEVAL_SOURCE,
        "repository_id": HASH,
        "generation_id": HASH,
        "snapshot_id": nullable(HASH),
    }
)
RETRIEVAL_WORK_KEYS = (
    "exact_queries",
    "lexical_queries",
    "metadata_queries",
    "hydration_queries",
    "sql_queries",
    "key_rows",
    "hydrated_rows",
    "vm_operations_reserved",
    "vm_progress_callbacks",
    "interrupted_sql_queries",
)
RETRIEVAL_DATA_SCHEMA = obj(
    {
        "projection": {"const": "retrieval_candidates"},
        "query_digest": HASH,
        "query_syntax": {"const": "literal_phrase"},
        "repository": STRING,
        "repository_filter_matched": BOOL,
        "path_prefix_count": {"type": "integer", "minimum": 0, "maximum": 16},
        "effective_limits": obj(
            {
                key: {"type": "integer", "minimum": low, "maximum": high}
                for key, (_, low, high) in RETRIEVAL_LIMITS.items()
            }
        ),
        "vm_operation_quantum": {"const": 1},
        "work": obj({key: COUNT for key in RETRIEVAL_WORK_KEYS}),
        "ranking_statistics_scope": {"const": "generation_global"},
        "sql_count_scope": {"const": "explicit_driver_statements"},
        "coverage": obj(
            {
                "state": {"enum": ["complete", "partial", "unavailable"]},
                "source_policy": {"enum": ["available", "import", "import_v1", "legacy_source"]},
                "source_files": COUNT,
                "source_bytes": COUNT,
                "unavailable_count": nullable(COUNT),
                "excluded_counts": {
                    "type": "object",
                    "maxProperties": 4,
                    "additionalProperties": {"type": "integer", "minimum": 1},
                },
            }
        ),
        "candidates": array(RETRIEVAL_CANDIDATE),
        "returned_count": COUNT,
        "truncated": BOOL,
        "truncation_reason": nullable({"enum": ["candidate_limit", "byte_limit", "element_limit"]}),
        "diagnostic_reason": {"enum": ["matched", "no_match", "tokenless", "filtered_repository"]},
    }
)
ERROR_SCHEMA = obj({"code": STRING, "message": STRING, "retry_hint": STRING})
ENVELOPE_SCHEMA = obj(
    {
        "schema_version": {"const": VERSION},
        "operation": {"enum": ["status", "neighborhood", "path", "page", "continue", "retrieve"]},
        "status": {"enum": ["ok", "error"]},
        "generation_id": nullable(HASH),
        "snapshot_id": nullable(HASH),
        "evidence_status": {"enum": ["available", "partial", "unknown", "unavailable"]},
        "data": {
            "anyOf": [
                STATUS_DATA_SCHEMA,
                *NAV_DATA.values(),
                PAGE_DATA_SCHEMA,
                RETRIEVAL_DATA_SCHEMA,
                {"type": "null"},
            ]
        },
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
    for operation, schema in {
        "status": STATUS_DATA_SCHEMA,
        **NAV_DATA,
        "page": PAGE_DATA_SCHEMA,
        "continue": PAGE_DATA_SCHEMA,
        "retrieve": RETRIEVAL_DATA_SCHEMA,
    }.items()
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
    if (
        structured
        and "query" in arguments
        and not (arguments.get("op") == "page" and isinstance(arguments["query"], dict))
    ):
        raise ToolError("MIXED_REQUEST", "Legacy and structured fields cannot be mixed.")
    if structured and arguments.get("schema_version") != VERSION:
        raise ToolError("UNSUPPORTED_SCHEMA_VERSION", "Unsupported graph schema version.")
    if structured and (
        not isinstance(arguments.get("op"), str)
        or arguments.get("op")
        not in {"status", "neighborhood", "path", "page", "continue", "retrieve", "context"}
    ):
        raise ToolError("INVALID_OPERATION", "Unsupported graph operation.")
    from atlas_aci.query.context_limits import LIMITS as CONTEXT_LIMITS

    if structured and any(
        key in arguments and type(arguments[key]) is not int
        for key in (*LIMITS, *RETRIEVAL_LIMITS, *CONTEXT_LIMITS, "max_bytes", "page_max_bytes")
    ):
        raise ToolError(
            "INVALID_INPUT", "Traversal limits must be integers, not booleans or floats."
        )
    if not INPUT_VALIDATOR.is_valid(arguments):
        raise ToolError("INVALID_INPUT", "Tool arguments must match the published schema.")
    if structured and arguments["op"] in {"retrieve", "context"}:
        from atlas_aci.query.retrieval import normalized

        normalized(
            arguments["text"],
            arguments.get("repository"),
            arguments.get("path_prefixes"),
            **{key: arguments[key] for key in RETRIEVAL_LIMITS if key in arguments},
        )
    if structured and arguments["op"] == "page":
        if (
            request_mode(arguments["query"]) != "structured"
            or arguments["query"]["op"] not in NAV_DATA
        ):
            raise ToolError("INVALID_INPUT", "Only bounded traversal queries can be paged.")
        expected = arguments.get("expected_generation_id")
        nested = arguments["query"].get("expected_generation_id")
        if expected is not None and nested is not None and expected != nested:
            raise ToolError("INVALID_INPUT", "Generation guards must agree.")
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
    validate_captured(data, element_limit)
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
    identities = {node["id"]: node for node in nodes}
    if len(identities) != len(nodes):
        raise ValueError("Duplicate returned entity identity")
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
        validate_hop(hop, identities, data)
    if operation == "path" and data["path"] is not None:
        for source, hop, target in zip(nodes[:-1], hops, nodes[1:], strict=True):
            assertion = hop["assertion"]
            expected = (assertion["source_entity_id"], assertion["target_entity_id"])
            if data["direction"] == "incoming":
                expected = expected[::-1]
            if (source["id"], target["id"]) != expected:
                raise ValueError("Path direction contradiction")


def validate_hop(hop, identities, data):
    for kind in ("assertion", "evidence", "occurrence", "producer"):
        ev.validate_record(hop[kind])
    assertion, evidence, occurrence = (hop[key] for key in ("assertion", "evidence", "occurrence"))
    if (
        assertion["evidence_id"] != evidence["id"]
        or evidence["occurrence_id"] != occurrence["id"]
        or evidence["producer_id"] != hop["producer"]["id"]
        or assertion["resolution"] not in data["confidence"]
        or assertion["relation"] not in data["relations"]
        or hop["traversal_direction"] != data["direction"]
        or not {assertion["source_entity_id"], assertion["target_entity_id"]} <= identities.keys()
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


def validate_page(payload, operation, element_limit):
    """Validate a labelled projection of captured results without re-clipping atoms."""
    if payload.get("operation") != operation or not STRUCTURED_VALIDATOR.is_valid(payload):
        raise ValueError("Invalid page result schema/operation")
    if payload["status"] == "error":
        return
    data = payload["data"]
    captured = data["captured"]
    items = data["items"]
    validate_captured(captured, element_limit)
    if (
        data["returned_count"] != len(items)
        or data["offset"] + len(items) > data["total_count"]
        or data["has_more"] != (data["offset"] + len(items) < data["total_count"])
        or data["has_more"] != (data["next_cursor"] is not None)
        or (data["has_more"] and not items)
    ):
        raise ValueError("Invalid captured page extent")
    if (captured["outcome"] == "partial") != (captured["stop_reason"] is not None):
        raise ValueError("Invalid captured query outcome")
    from atlas_aci.protocol.resources import parse_uri
    from atlas_aci.query.admission import lists
    from atlas_aci.query.leases import digest

    if any(
        len(value) > element_limit
        for value in lists({"items": items, "resources": data["resources"]})
    ):
        raise ValueError("Page atom exceeds element cap")
    descriptors = {d["id"]: d for d in data["resources"]}
    if len(descriptors) != len(data["resources"]):
        raise ValueError("Duplicate resource descriptor")
    referenced = set()
    for atom in items:
        if atom["kind"] == "path":
            if captured["query_operation"] != "path" or captured["outcome"] != "complete":
                raise ValueError("Invalid whole path atom")
            nodes = atom["path"]["nodes"]
            hops = atom["path"]["hops"]
            nav = {k: v for k, v in captured.items() if k != "query_operation"}
            nav["path"] = atom["path"]
            projected = envelope(
                nav,
                operation="path",
                generation_id=payload["generation_id"],
                snapshot_id=payload["snapshot_id"],
                evidence_status=payload["evidence_status"],
            )
            projected["bounds"]["element_limit"] = element_limit
            validate_navigation(projected, "path", element_limit)
        elif atom["kind"] == "edge":
            if captured["query_operation"] != "neighborhood":
                raise ValueError("Wrong edge atom operation")
            nodes = atom["nodes"]
            hops = [atom["proof"]]
            if len(nodes) != 2:
                raise ValueError("Edge must carry both endpoint projections")
        else:
            if captured["query_operation"] != "neighborhood":
                raise ValueError("Wrong node atom operation")
            nodes = [atom["node"]]
            hops = []
        identities = {n["id"]: n for n in nodes}
        for node in nodes:
            ev.validate_record(
                {"type": "entity", **{k: v for k, v in node.items() if k != "projection"}}
            )
        for hop in hops:
            validate_hop(hop, identities, captured)
        known = {hop["evidence"]["id"]: hop for hop in hops}
        for identifier in atom["resources"]:
            if identifier not in descriptors:
                raise ValueError("Missing issued resource descriptor")
            descriptor = descriptors[identifier]
            hop = known.get(descriptor["evidence_id"])
            if hop is None:
                raise ValueError("Resource not in returned proof membership")
            proof = hop["evidence"]
            occurrence = hop["occurrence"]
            if (
                descriptor["generation_id"] != payload["generation_id"]
                or descriptor["snapshot_id"] != payload["snapshot_id"]
                or descriptor["path"] != occurrence["path"]
                or descriptor["occurrence_id"] != occurrence["id"]
                or any(
                    descriptor[k] != proof[k]
                    for k in (
                        "source_sha256",
                        "source_size",
                        "start_byte",
                        "end_byte",
                        "provenance_state",
                    )
                )
                or descriptor["id"]
                != digest({k: v for k, v in descriptor.items() if k not in {"id", "uri"}})
                or parse_uri(descriptor["uri"])[0] != identifier
            ):
                raise ValueError("Issued resource proof contradiction")
            referenced.add(identifier)
    if referenced != descriptors.keys():
        raise ValueError("Unreferenced resource descriptor")


def validate_captured(data, element_limit):
    """Shared S12 metadata invariants; no assumption that a later page contains its root."""
    from atlas_aci.query.admission import lists

    if (data["outcome"] == "partial") != (data["stop_reason"] is not None):
        raise ValueError("Invalid traversal frontier status")
    if data["work"]["visited_nodes"] > data["effective_limits"]["max_visited_nodes"]:
        raise ValueError("Visited-node budget exceeded")
    if data["work"]["examined_edges"] > data["effective_limits"]["max_examined_edges"]:
        raise ValueError("Examined-edge budget exceeded")
    for name in ("seed", "target"):
        if data.get(name) is not None and any(
            len(items) > element_limit for items in lists(data[name])
        ):
            raise ValueError("Endpoint candidate exceeds element cap")
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


def validate_retrieval(payload, element_limit):
    """Closed whole candidates with canonical IDs, source and intended operation."""
    import math

    from atlas_aci.indexing import retrieval as ret
    from atlas_aci.query.admission import lists
    from atlas_aci.query.retrieval import TIERS

    if payload.get("operation") != "retrieve" or not STRUCTURED_VALIDATOR.is_valid(payload):
        raise ValueError("Invalid intended retrieval result")
    if payload["status"] == "error":
        return
    data = payload["data"]
    candidates = data["candidates"]
    limits = data["effective_limits"]
    work = data["work"]
    ev.require(
        data["returned_count"] == len(candidates) <= min(limits["max_candidates"], element_limit),
        "retrieval candidate count",
    )
    ev.require(
        all(len(items) <= element_limit for items in lists(data)), "whole candidate element cap"
    )
    ev.require(data["truncated"] == (data["truncation_reason"] is not None), "retrieval truncation")
    ev.require(
        work["vm_operations_reserved"] <= limits["max_vm_operations"]
        and work["vm_progress_callbacks"] <= work["vm_operations_reserved"],
        "retrieval VM allowance",
    )
    ev.require(
        work["sql_queries"]
        == sum(
            work[k]
            for k in ("exact_queries", "lexical_queries", "metadata_queries", "hydration_queries")
        )
        and work["exact_queries"] <= 4
        and work["lexical_queries"] <= 1
        and work["metadata_queries"] == 1,
        "retrieval driver SQL counts",
    )
    ev.require(
        work["key_rows"] <= limits["max_candidates"] + 1
        and work["hydrated_rows"] <= work["hydration_queries"] <= 3 * limits["max_candidates"]
        and work["interrupted_sql_queries"] == 0,
        "retrieval hydration bounds",
    )
    if not data["repository_filter_matched"]:
        ev.require(
            not candidates and data["diagnostic_reason"] == "filtered_repository",
            "retrieval repository isolation",
        )
    seen = set()
    previous = None
    for candidate in candidates:
        identifier = candidate["candidate_id"]
        ev.require(identifier not in seen, "duplicate candidate")
        seen.add(identifier)
        ev.require(
            candidate["generation_id"] == payload["generation_id"]
            and candidate["snapshot_id"] == payload["snapshot_id"]
            and candidate["repository_id"] == ret.digest(data["repository"]),
            "retrieval generation/scope",
        )
        ev.require(ev.relative_path(candidate["path"]), "retrieval path")
        tier = candidate["match_tier"]
        score = candidate["lexical_score"]
        ev.require(
            (score is None) == (tier != "lexical")
            and (score is None or (type(score) in {float, int} and math.isfinite(score))),
            "retrieval relevance",
        )
        order = (TIERS.index(tier), score if score is not None else 0, identifier)
        ev.require(previous is None or previous < order, "retrieval stable ordering")
        previous = order
        entity = candidate["entity"]
        occurrence = candidate["occurrence"]
        evidence = candidate["evidence"]
        source = candidate["source"]
        if candidate["kind"] == "entity":
            ev.require(
                source["state"] != "available" or source["granularity"] == "occurrence",
                "entity source granularity",
            )
            ev.require(
                entity is not None
                and identifier == entity["id"]
                and candidate["qualified_label"] == ret.qualified_label(entity),
                "retrieval entity identity",
            )
            ev.validate_record(entity)
            ev.require(
                entity["path"] == candidate["path"] and entity["language"] == candidate["language"],
                "retrieval entity location",
            )
            if occurrence is not None:
                ev.validate_record(occurrence)
                ev.require(
                    occurrence["entity_id"] == identifier
                    and occurrence["path"] == candidate["path"]
                    and occurrence["language"] == candidate["language"],
                    "retrieval occurrence ownership",
                )
            if evidence is not None:
                ev.validate_record(evidence)
                ev.require(
                    occurrence is not None and evidence["occurrence_id"] == occurrence["id"],
                    "retrieval evidence link",
                )
                ev.require(
                    all(evidence[k] == occurrence[k] for k in SOURCE_SPAN),
                    "retrieval evidence span",
                )
        else:
            ev.require(
                entity is None
                and occurrence is None
                and evidence is None
                and candidate["qualified_label"] is None,
                "file candidate has no invented proof",
            )
            ev.require(
                source["state"] == "available"
                and source["granularity"] == "file"
                and source["start_byte"] == 0,
                "whole-file lexical span",
            )
            key = {
                "format": ret.FORMAT,
                "kind": "source_file",
                "path": candidate["path"],
                "language": candidate["language"],
                "source_sha256": source["source_sha256"],
                "start_byte": 0,
                "end_byte": source["end_byte"],
                "granularity": "file",
                "availability": "available",
            }
            ev.require(ret.digest(key) == identifier, "source-file candidate identity")
        if source["state"] == "available":
            ev.require(
                payload["snapshot_id"] is not None
                and source["path"] == candidate["path"]
                and 0 <= source["start_byte"] <= source["end_byte"] <= source["source_size"],
                "retrieval source bounds",
            )
            if source["granularity"] == "file":
                ev.require(source["end_byte"] == source["source_size"], "whole-file size")
            else:
                ev.require(
                    occurrence is not None
                    and evidence is not None
                    and occurrence["provenance_state"] == "source",
                    "available occurrence source",
                )
                ev.require(
                    all(
                        source[k] == occurrence[k]
                        for k in ("source_sha256", "start_byte", "end_byte", "source_size")
                    ),
                    "source occurrence reference",
                )


def validate_context(payload, operation, element_limit):
    from atlas_aci.protocol.context import validate_context as validate

    return validate(payload, operation, element_limit)


def _register_context_schemas():
    # Register after all shared canonical schemas exist; semantic imports stay lazy.
    from atlas_aci.protocol.context import CONTEXT_DATA, CONTEXT_INPUT

    GRAPH_INPUT_SCHEMA["oneOf"].append(CONTEXT_INPUT)
    ENVELOPE_SCHEMA["properties"]["operation"]["enum"].append("context")
    ENVELOPE_SCHEMA["properties"]["data"]["anyOf"].append(CONTEXT_DATA)
    for operation in ("context", "continue"):
        ENVELOPE_SCHEMA["oneOf"].append(
            {
                "properties": {
                    "operation": {"const": operation},
                    "status": {"const": "ok"},
                    "data": CONTEXT_DATA,
                    "error": {"type": "null"},
                }
            }
        )
    return Draft202012Validator(GRAPH_INPUT_SCHEMA), Draft202012Validator(ENVELOPE_SCHEMA)


INPUT_VALIDATOR, STRUCTURED_VALIDATOR = _register_context_schemas()
