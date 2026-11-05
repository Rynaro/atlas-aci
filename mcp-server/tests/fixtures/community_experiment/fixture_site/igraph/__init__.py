"""ACI-S36 fixture standing in for the optional igraph provider. It is NOT igraph.

It implements only the interface subset the experiment runner calls, so the
runner's detection, isolation, adapter and artifacts can be exercised against a
real partitioner without installing anything. Its version carries the marker
`+atlas.fixture`; results produced with it are tagged `provider_kind: fixture`
and are never Leiden measurements. See `leidenalg/__init__.py`.
"""

import os

__version__ = (
    "9.9.9+atlas.fixture"
    if os.environ.get("ATLAS_S36_FIXTURE_FAULT") == "version_skew"
    else "0.0.0+atlas.fixture"
)


class Graph:
    """Holds the vertex count and the edge list exactly as received."""

    def __init__(self, n=0, edges=None, directed=False):
        self._n = int(n)
        self._edges = [tuple(edge) for edge in (edges or [])]
        if os.environ.get("ATLAS_S36_FIXTURE_FAULT") == "drop_edge":
            self._edges = self._edges[:-1]  # a provider graph that silently lost an edge
        self.directed = bool(directed)

    def vcount(self):
        return self._n

    def ecount(self):
        return len(self._edges)

    def get_edgelist(self):
        return list(self._edges)
