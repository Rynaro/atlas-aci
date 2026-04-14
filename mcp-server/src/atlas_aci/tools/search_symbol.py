"""search_symbol — index-backed symbol lookup."""

from __future__ import annotations

import time
from typing import Any

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement, ToolError


async def search_symbol(
    args: dict[str, Any],
    config: Config,
    enforcement: Enforcement,
    code_graph: CodeGraph,
) -> dict[str, Any]:
    start_t = time.monotonic()
    name = args["name"]
    kind = args.get("kind", "any")

    if not (config.repo / ".atlas" / "graph.db").exists():
        raise ToolError(
            "INDEX_UNAVAILABLE",
            "Code graph not built. Run: atlas-aci index --repo <repo>.",
            "different_tool",
        )

    result = code_graph.search_symbol(name=name, kind=kind)

    bytes_out = sum(len(d.get("path", "")) + 32 for d in result["definitions"]) + sum(
        len(r.get("path", "")) + 32 for r in result["references"]
    )
    enforcement.record(
        tool="search_symbol",
        args=args,
        bytes_out=bytes_out,
        duration_ms=int((time.monotonic() - start_t) * 1000),
    )
    return result
