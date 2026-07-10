"""Deterministic label propagation (D3/D4a/A3) — the pure algorithmic
core of `CodeGraph.communities()`, factored out into its own
dependency-free module.

Checker instruction (D3a probe closure): the shipped LPA's own partition
was, until now, ASSERTED into the committed graph bundle and never
re-derived by `scripts/verify-probe-verdict.py` — "the last assertion in
the chain." Factoring the algorithm out here, with ZERO imports beyond
the standard library (no `sqlite3`, no `tree_sitter`, no other
`atlas_aci` internals), lets the verifier import this exact module (by
file path, bypassing `atlas_aci`'s package `__init__` entirely — see
`verify-probe-verdict.py`'s own loader) and RE-RUN the identical
algorithm against the committed bundle's edge list, asserting the result
equals `lpa_labels` rather than trusting that field as printed.

Operates on any hashable, orderable node type: `CodeGraph.communities()`
passes `(path, line, name)` tuples (a specific symbol definition);
`verify-probe-verdict.py`, re-running this against a committed graph
bundle, passes plain integer node indices (the bundle's own node
numbering). The algorithm only needs a FIXED TOTAL ORDER over whatever
the nodes are — never what they represent.
"""

from __future__ import annotations

from typing import Any

# `Node` is deliberately `Any`, not a strictly-bound TypeVar: this
# function is generic over any hashable, orderable key (tuples for
# codegraph.py, plain ints for the verifier's bundle re-run), and Python
# has no single builtin protocol that captures "hashable AND supports
# `<`" precisely enough for mypy to accept `sorted()` on it. Runtime
# behavior is unaffected either way; this is a typing-precision
# trade-off, not a correctness one.
Node = Any

# A safety-only bound on the propagation loop (deterministic asynchronous
# LPA with fixed tie-breaking is not mathematically GUARANTEED to
# converge; this caps runtime without affecting a converging graph — a
# run that reaches the cap simply stops on whatever labels the last pass
# produced, which is still a deterministic function of the input, since
# the loop itself has no randomness).
DEFAULT_MAX_ITERATIONS = 100


def label_propagation(
    adjacency: dict[Node, set[Node]], max_iterations: int = DEFAULT_MAX_ITERATIONS
) -> dict[Node, int]:
    """Deterministic asynchronous label propagation (Raghavan-style,
    adapted): nodes are visited in a FIXED total order (`sorted(adjacency)`)
    every pass, asynchronously (a node sees its neighbors' already-updated
    labels within the same pass) — labels start as each node's own index
    in that sorted order (already a deterministic assignment). Each pass,
    a node adopts the most frequent label among its neighbors; ties break
    toward the SMALLEST label value, a fixed rule, never "random" or
    insertion/hash-order-dependent. Runs until a full pass makes no
    change, or `max_iterations` is reached.

    Final community IDs are NOT the raw propagated label values (those
    are arbitrary node-index leftovers) — communities are grouped, then
    renumbered 0..N-1 in ascending order of their smallest member node
    (already a total order), so the ID assignment itself is reproducible,
    not just the grouping.

    Returns `{node: community_id}` for every node key in `adjacency`
    (isolated nodes — an empty neighbor set — get their own singleton
    community, same as every other node: they simply never change their
    initial sorted-index label since they have no neighbors to agree
    with, and singleton labels still get grouped/renumbered like any
    other).
    """
    nodes_sorted = sorted(adjacency)
    labels: dict[Node, int] = {node: i for i, node in enumerate(nodes_sorted)}

    for _pass_number in range(max_iterations):
        changed = False
        for node in nodes_sorted:
            neighbors = adjacency[node]
            if not neighbors:
                continue
            counts: dict[int, int] = {}
            for neighbor in neighbors:
                label = labels[neighbor]
                counts[label] = counts.get(label, 0) + 1
            max_count = max(counts.values())
            # Deterministic tie-break: the smallest label among those
            # tied for most-frequent — never "random", never
            # insertion/hash-order-dependent.
            new_label = min(label for label, count in counts.items() if count == max_count)
            if new_label != labels[node]:
                labels[node] = new_label
                changed = True
        if not changed:
            break

    groups: dict[int, list[Node]] = {}
    for node in nodes_sorted:
        groups.setdefault(labels[node], []).append(node)
    # Renumber 0..N-1, ordered by each group's smallest member — the
    # propagated label VALUES are arbitrary leftover node-indices; the
    # total order lives in the node identities, not the labels.
    ordered_groups = sorted(groups.values(), key=min)
    return {
        node: community_id for community_id, group in enumerate(ordered_groups) for node in group
    }
