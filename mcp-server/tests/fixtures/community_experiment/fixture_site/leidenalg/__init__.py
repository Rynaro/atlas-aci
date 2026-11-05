"""ACI-S36 fixture standing in for the optional leidenalg provider. It is NOT Leiden.

A real, deterministic, seeded greedy-modularity partitioner named
`atlas-fixture-greedy-modularity.v1`: the node visiting order is permuted by a
32-bit LCG seeded with `seed`; local moving uses the exact Fraction modularity
gain; a node moves only for a strictly larger gain than staying, and among
equal gains the smallest community label wins; at most 100 passes; only
sorted iteration. The membership it returns is computed, never canned.

Test hooks (environment variables, read at call time):
  ATLAS_S36_FIXTURE_CAPTURE_DIR  write <pid>.json with the graph exactly as received
  ATLAS_S36_FIXTURE_FAULT        omit | sleep | exit1 | version_skew | import_error | drop_edge
"""

import json
import os
import sys
import time
from fractions import Fraction

if os.environ.get("ATLAS_S36_FIXTURE_FAULT") == "import_error":
    raise ImportError("fixture import fault")

__version__ = (
    "9.9.9+atlas.fixture"
    if os.environ.get("ATLAS_S36_FIXTURE_FAULT") == "version_skew"
    else "0.0.0+atlas.fixture"
)
MAX_PASSES = 100


class ModularityVertexPartition:
    """Marker for the partition type the runner asks for."""


class _Result:
    def __init__(self, membership):
        self.membership = membership


def _order(n, seed):
    state = (int(seed or 0) * 2654435761 + 12345) & 0xFFFFFFFF
    order = list(range(n))
    for i in range(n - 1, 0, -1):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        j = (state >> 8) % (i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def _partition(n, edges, seed):
    m = len(edges)
    neighbours = [[] for _ in range(n)]
    for u, v in edges:
        neighbours[u].append(v)
        neighbours[v].append(u)
    for row in neighbours:
        row.sort()
    degree = [len(row) for row in neighbours]
    label = list(range(n))
    total = list(degree)
    order = _order(n, seed)
    for _ in range(MAX_PASSES):
        moved = False
        for v in order:
            if not neighbours[v]:
                continue
            old = label[v]
            total[old] -= degree[v]
            links = {}
            for w in neighbours[v]:
                links[label[w]] = links.get(label[w], 0) + 1
            best = old
            best_gain = Fraction(links.get(old, 0), m) - Fraction(total[old] * degree[v], 2 * m * m)
            for c in sorted(links):
                gain = Fraction(links[c], m) - Fraction(total[c] * degree[v], 2 * m * m)
                if gain > best_gain:
                    best, best_gain = c, gain
            total[best] += degree[v]
            label[v] = best
            moved = moved or best != old
        if not moved:
            break
    names = {}
    return [names.setdefault(c, len(names)) for c in label]


def find_partition(graph, partition_type, n_iterations=2, seed=None):
    fault = os.environ.get("ATLAS_S36_FIXTURE_FAULT")
    n = graph.vcount()
    edges = graph.get_edgelist()
    capture = os.environ.get("ATLAS_S36_FIXTURE_CAPTURE_DIR")
    if capture:
        record = {"edges": [list(e) for e in edges], "n": n, "seed": seed}
        text = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with open(os.path.join(capture, str(os.getpid()) + ".json"), "w") as handle:
            handle.write(text)
    if fault == "sleep":
        time.sleep(120)
    if fault == "exit1":
        sys.exit(1)
    membership = _partition(n, edges, seed)
    if fault == "omit":
        membership = membership[:-1]
    return _Result(membership)
