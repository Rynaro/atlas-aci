#!/usr/bin/env python3
"""D3a probe, phase 1 — export the confident subgraph + the shipped LPA
partition for one reference repo, as plain JSON.

This is the ONLY phase that imports `atlas_aci` (needs the package's own
`uv run --frozen` environment, inside `mcp-server/`). It never imports
networkx or any graph-algorithm library — see `probe-modularity.py` for
phase 2, which runs in a SEPARATE, ephemeral environment
(AC-A3-3/AC-NEG-2: networkx must never enter `mcp-server/`'s dependency
tree, not even transiently).

Graph construction here deliberately reuses `CodeGraph`'s own private
`_resolve_source_node`/`_target_kind` helpers — the SAME resolution
`communities()` itself uses — rather than re-deriving node identity by
hand. The point of the probe is "does the shipped algorithm's own graph
score well against Louvain on the SAME input," not "does an independently
re-derived graph score well" — D3a requires identical input to both
algorithms, and the shipped input IS what `confident_edges()` +
`_resolve_source_node` produce.

Checker defect 13 (recompute-everything closure): output now carries the
FULL confident subgraph — node identities (not just a count) and the
edge list, in the SAME canonical total order `communities()` itself uses
internally (`nodes_sorted = sorted(adjacency)`) — plus the shipped LPA
partition in that exact node order. This is committed downstream (via
`probe-assemble-graph-bundle.py`) as `probe-graphs.json.gz`, so a
verifier can recompute node/edge counts, community counts, and
modularity itself from raw graph data, rather than trusting any of this
script's own summary numbers.

Usage:
  uv run --frozen python ../scripts/probe-export-confident-graph.py \
      <repo_path> <out_json_path>
(run from inside mcp-server/, or with PYTHONPATH=mcp-server/src set)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <repo_path> <out_json_path>", file=sys.stderr)
        raise SystemExit(2)

    repo_path = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2]).resolve()

    from atlas_aci.codegraph import CodeGraph

    graph = CodeGraph(repo=repo_path)
    build_stats = graph.build()
    confident = graph.confident_edges()

    # Scratch (insertion-order) node index -- just a convenience for
    # building the edge set; NOT the canonical order committed below.
    scratch_index: dict[tuple[str, int, str], int] = {}

    def scratch_idx(key: tuple[str, int, str]) -> int:
        if key not in scratch_index:
            scratch_index[key] = len(scratch_index)
        return scratch_index[key]

    scratch_edges: set[tuple[int, int]] = set()
    for edge in confident:
        target = edge["target"]
        if target is None:
            continue
        source_key, _source_kind = graph._resolve_source_node(edge["source"])
        if source_key is None:
            continue
        target_key = (target["path"], target["line"], target["name"])
        if source_key == target_key:
            scratch_idx(source_key)  # self-loop: node exists, no cross-community edge
            continue
        u, v = scratch_idx(source_key), scratch_idx(target_key)
        scratch_edges.add((min(u, v), max(u, v)))

    communities_result = graph.communities()
    # communities()'s own `members` list is already built in ITS canonical
    # node order (`nodes_sorted = sorted(adjacency)`, codegraph.py) -- use
    # that exact order as the ONE canonical order this bundle commits to,
    # rather than re-deriving a second sort that could silently diverge.
    canonical_order: list[tuple[str, int, str]] = [
        (m["path"], m["line"], m["name"]) for m in communities_result["communities"]
    ]
    lpa_labels: list[int] = [m["community_id"] for m in communities_result["communities"]]

    # Sanity: the node set this script derived from confident_edges() must
    # be the SAME node set communities() derived — same source data, same
    # resolution helpers. If this ever mismatches, the probe input is not
    # actually identical to what communities() analyzed, and the Q values
    # below would not mean what the artefact claims they mean.
    if set(canonical_order) != set(scratch_index):
        missing_here = set(canonical_order) - set(scratch_index)
        missing_there = set(scratch_index) - set(canonical_order)
        raise AssertionError(
            "node set mismatch between this script's adjacency and "
            f"communities()'s own node set: {len(missing_here)} in "
            f"communities() only, {len(missing_there)} in this script only"
        )

    canonical_index = {key: i for i, key in enumerate(canonical_order)}
    scratch_to_canonical = {
        scratch_i: canonical_index[key] for key, scratch_i in scratch_index.items()
    }
    canonical_edges = sorted(
        {
            tuple(sorted((scratch_to_canonical[u], scratch_to_canonical[v])))
            for u, v in scratch_edges
        }
    )

    out = {
        "repo": str(repo_path),
        # Node identities, in the canonical order — index i in "edges"/
        # "lpa_labels" below refers to nodes[i].
        "nodes": [[path, line, name] for (path, line, name) in canonical_order],
        "edges": [list(e) for e in canonical_edges],
        "lpa_labels": lpa_labels,
        "node_count": len(canonical_order),
        "edge_count": len(canonical_edges),
        "lpa_community_count": communities_result["community_count"],
        "resolved_edge_count": communities_result["resolved_edge_count"],
        "ambiguous_edges_excluded": communities_result["ambiguous_edges_excluded"],
        "build_stats": build_stats,
    }
    out_path.write_text(json.dumps(out, indent=2))
    print(
        f"exported {out['node_count']} nodes / {out['edge_count']} edges "
        f"({out['lpa_community_count']} LPA communities) -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
