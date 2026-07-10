#!/usr/bin/env python3
"""D3a probe, phase 2 — networkx-side modularity measurement + partitions.

This script is the ONLY place networkx is ever imported for this probe,
and it MUST be run in an ephemeral environment, never inside
`mcp-server/`'s own project environment:

  uv run --with networkx --no-project python scripts/probe-modularity.py \
      <graph_json_path>

`--no-project` (run from the repo root, where there is no pyproject.toml)
plus `--with networkx` resolves networkx into a throwaway environment for
this one invocation only — it never touches `mcp-server/pyproject.toml`
or `mcp-server/uv.lock` (AC-A3-3/AC-NEG-2). Verify after every probe run:
  grep -ric networkx mcp-server/pyproject.toml mcp-server/uv.lock
must print "0 0".

Reads the JSON phase-1 (`probe-export-confident-graph.py`) produced:
builds ONE undirected/unweighted networkx Graph from its node/edge list
(the same graph the shipped LPA analyzed — D3a's identical-input
requirement), computes:

  - LPA_Q: modularity of the shipped `communities()` partition, via
    networkx's own `nx.community.modularity` — the reference metric, not
    a hand-rolled one, so LPA and Louvain are scored by the same ruler.
  - Louvain_Q x10: `nx.community.louvain_communities` at resolution
    (gamma) 1.0, seeds 0..9 — ten independent runs, never averaged into a
    single number before recording (D3a: median is the frozen bar
    statistic, but best/worst/mean/sd are recorded too for the full
    picture in the probe artefact).

Checker defect 13 (recompute-everything closure): this phase now ALSO
emits each Louvain run's own PARTITION (a community-label array in the
SAME canonical node order phase 1 committed, renumbered 0..k-1 by
ascending smallest-member node — same convention the shipped LPA uses),
not just its `Q`. `scripts/probe-assemble-graph-bundle.py` folds these
partitions together with phase 1's node/edge/LPA data into
`probe-graphs.json.gz`, so `scripts/verify-probe-verdict.py` can
recompute every `Q` in pure Python straight from the graph + partitions —
trusting no summary number this script prints, including the `lpa_q`/
`louvain_*` fields below (which remain useful as the "recorded, honesty-
checked" values, not as the verifier's source of truth).

Prints one JSON object to stdout. Never writes to the repo.
"""

from __future__ import annotations

import json
import statistics
import sys


def _renumber_partition(n: int, groups: list[set[int]]) -> list[int]:
    """A community-label array of length `n` (index = node, value = label),
    renumbered 0..k-1 by ascending smallest-member node — the same
    convention `communities()`'s shipped LPA uses, so Louvain partitions
    are comparable/storable in the identical shape."""
    ordered_groups = sorted(groups, key=min)
    labels = [0] * n
    for label, group in enumerate(ordered_groups):
        for node in group:
            labels[node] = label
    return labels


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <graph_json_path>", file=sys.stderr)
        raise SystemExit(2)

    import networkx as nx  # type: ignore[import-untyped]  # never in mcp-server's own env (AC-NEG-2)

    with open(sys.argv[1]) as f:
        data = json.load(f)
    n = data["node_count"]
    edges = [tuple(e) for e in data["edges"]]

    g = nx.Graph()
    g.add_nodes_from(range(n))
    g.add_edges_from(edges)

    lpa_labels = data["lpa_labels"]
    lpa_groups: dict[int, set[int]] = {}
    for node, label in enumerate(lpa_labels):
        lpa_groups.setdefault(label, set()).add(node)
    lpa_partition = list(lpa_groups.values())
    lpa_q = nx.community.modularity(g, lpa_partition)

    louvain_runs = []
    for seed in range(10):
        comms = nx.community.louvain_communities(g, seed=seed, resolution=1.0)
        q = nx.community.modularity(g, comms)
        partition_labels = _renumber_partition(n, [set(c) for c in comms])
        louvain_runs.append({"seed": seed, "q": q, "partition": partition_labels})

    qs = [run["q"] for run in louvain_runs]

    result = {
        "repo": data["repo"],
        "node_count": n,
        "edge_count": len(edges),
        "lpa_q": lpa_q,
        "lpa_community_count": len(lpa_partition),
        "louvain_runs": louvain_runs,
        "louvain_median": statistics.median(qs),
        "louvain_best": max(qs),
        "louvain_worst": min(qs),
        "louvain_mean": statistics.mean(qs),
        "louvain_sd": statistics.pstdev(qs),
        "networkx_version": nx.__version__,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
