"""search_symbol — index-backed symbol lookup."""

from __future__ import annotations

import time
from typing import Any

from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph
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

    # H3: ask the CodeGraph instance itself (single source of truth for the
    # epoch-namespaced path) rather than hardcoding ".atlas/graph.db" here —
    # that hardcoding is what broke silently under the H3 rename (vigil C3).
    # `epoch_ok()` also fails fast on a stale/mismatched epoch without ever
    # writing (DIR-2), which is exactly what `serve` needs on a read-only
    # mount.
    if not code_graph.epoch_ok():
        raise ToolError(
            "INDEX_UNAVAILABLE",
            f"Code graph not built for schema epoch {SCHEMA_EPOCH}. "
            f"Run: atlas-aci index --repo <repo>.",
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
