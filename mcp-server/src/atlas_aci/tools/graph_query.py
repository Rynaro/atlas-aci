"""graph_query — code-graph DSL queries."""

from __future__ import annotations

import time
from typing import Any

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError


async def graph_query(
    args: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
    code_graph: CodeGraph,
) -> dict[str, Any]:
    start_t = time.monotonic()
    query = args["query"]

    if not (config.repo / ".atlas" / "graph.db").exists():
        raise ToolError(
            "INDEX_UNAVAILABLE",
            "Code graph not built. Run: atlas-aci index --repo <repo>.",
            "different_tool",
        )

    result = code_graph.query(query)

    enforcement.record(
        tool="graph_query",
        args=args,
        bytes_out=len(str(result)),
        duration_ms=int((time.monotonic() - start_t) * 1000),
    )
    return result
