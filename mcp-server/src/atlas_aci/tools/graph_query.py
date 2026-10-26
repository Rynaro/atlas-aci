"""graph_query — code-graph DSL queries."""

from __future__ import annotations

import time
from typing import Any

from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError
from atlas_aci.protocol.graph import envelope, request_mode
from atlas_aci.query.admission import MINIMUM_BUDGET, Admission
from atlas_aci.query.traversal import navigate


async def graph_query(
    args: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
    code_graph: CodeGraph,
) -> dict[str, Any]:
    start_t = time.monotonic()
    mode = request_mode(args)

    # H3: see search_symbol.py — ask the CodeGraph instance for the
    # epoch-namespaced path rather than hardcoding ".atlas/graph.db".
    if not code_graph.epoch_ok():
        raise ToolError(
            "INDEX_UNAVAILABLE",
            f"Code graph not built for schema epoch {SCHEMA_EPOCH}. "
            f"Run: atlas-aci index --repo <repo>.",
            "different_tool",
        )

    if mode == "structured" and args["op"] != "status":
        budget = args.get("max_bytes", config.max_response_bytes)
        if budget > config.max_response_bytes or budget < MINIMUM_BUDGET:
            raise ToolError("INVALID_BUDGET", "Traversal budget cannot represent bounded metadata.")
        admission = Admission(
            operation=args["op"],
            budget=budget,
            element_limit=config.max_bound_field_elements,
            generation_id=code_graph.generation_id,
            snapshot_id=code_graph.snapshot_id,
            evidence_status=code_graph._lease_evidence_status,
        )
        data = await navigate(
            code_graph,
            operation=args["op"],
            **{
                key: value
                for key, value in args.items()
                if key not in {"op", "schema_version", "expected_generation_id", "max_bytes"}
            },
            admit=admission,
        )
        result = admission.finish(data)
    elif mode == "structured":
        data = code_graph.published_status()
        state = data["completeness"]["state"]
        result = envelope(
            data,
            generation_id=code_graph.generation_id,
            snapshot_id=code_graph.snapshot_id,
            evidence_status="available" if state == "complete" else state,
        )
    else:
        result = code_graph.query(args["query"])

    enforcement.record(
        tool="graph_query",
        args=args,
        bytes_out=len(str(result)),
        duration_ms=int((time.monotonic() - start_t) * 1000),
    )
    return result
