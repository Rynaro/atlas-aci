"""ACI-S36 semantics: hand-computed oracles, the decision table, strict loading and rejections.

Every expected number in the oracle tests is a literal worked out by hand (the worked
arithmetic is in the comments) and written without the library under test. `R` is the shape
of a ratio: the reduced `num/den` and its 6-place ROUND_HALF_EVEN decimal string.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction as F
from pathlib import Path

import pytest
from test_s36_support import (
    FIX_MANIFEST,
    PROD_MANIFEST,
    PRODUCTION_SHA,
    ROOT,
    S18_GRAPH,
    S23_MANIFEST,
    SIDECAR,
    SITE,
    SYN_GRAPH,
    USEFUL,
    bundle_graph,
    canonical,
    copy_run,
    leftover,
    load_path_module,
    lpa_module,
    manifest_copy,
    projected,
    projection,
    read_partitions,
    read_result,
    rejected,
    repo_copy,
    reseal_raw,
    run_driver,
    run_experiment,
    run_script,
    run_verify,
    runner,
    sha,
    shared_fx,
    tamper_partitions,
    tamper_result,
    verifier,
    write_manifest,
)

V = verifier()


def R(num, den, value):
    return {"num": num, "den": den, "value": value}


ZERO = R(0, 1, "0.000000")
ACCENT = "caf" + chr(233)


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return shared_fx(tmp_path_factory)


# --------------------------------------------------------------------------- projection

NODES = [["b.py", 3, "f"], ["a.py", 1, "g"], ["a.py", 1, "f"], ["c.py", 2, "h"]]


def test_projection_table():
    # Reordering: sorted by (path, line, name) -> a.py/1/f, a.py/1/g, b.py/3/f, c.py/2/h.
    nodes, edges = V.project(NODES, [[0, 1], [1, 2], [3, 0]])
    assert nodes == [["a.py", 1, "f"], ["a.py", 1, "g"], ["b.py", 3, "f"], ["c.py", 2, "h"]]
    # old 0 -> 2, old 1 -> 1, old 2 -> 0, old 3 -> 3: pairs (2,1)->(1,2), (1,0)->(0,1), (3,2)->(2,3)
    assert edges == [[0, 1], [1, 2], [2, 3]]
    # Duplicates and reversed duplicates collapse, self-loops vanish, a loop-only node stays.
    nodes2, edges2 = V.project(NODES, [[0, 1], [1, 0], [0, 1], [2, 2], [3, 3]])
    assert edges2 == [[1, 2]] and len(nodes2) == 4
    assert nodes2 == nodes
    assert V.project(NODES, [[0, 1]])[0] == nodes  # nodes 2 and 3 are isolated and retained
    for bad_nodes in (
        [["a.py", True, "f"], ["b.py", 1, "g"]],
        [["a.py", 0, "f"], ["b.py", 1, "g"]],
        [["a.py", 1, "f"], ["a.py", 1, "f"]],
        [["a.py", 1], ["b.py", 1, "g"]],
        [["a.py", 1.5, "f"], ["b.py", 1, "g"]],
        [[1, 1, "f"], ["b.py", 1, "g"]],
    ):
        with pytest.raises(V.Rejection) as info:
            V.project(bad_nodes, [[0, 1]])
        assert info.value.reason == "graph_invalid", bad_nodes
    for bad_edges in ([[0, 9]], [[0, -1]], [[0, True]], [[0]], [[0, 1, 2]], [["0", 1]], []):
        with pytest.raises(V.Rejection) as info:
            V.project(NODES, bad_edges)
        assert info.value.reason == "graph_invalid", bad_edges
    with pytest.raises(V.Rejection) as info:
        V.project(NODES, [[0, 0]])  # zero edges after dropping the loop
    assert info.value.reason == "graph_invalid"
    with pytest.raises(V.Rejection) as info:
        V.project(NODES, [[0, 1]], max_nodes=3)
    assert info.value.reason == "graph_too_large"
    with pytest.raises(V.Rejection) as info:
        V.project(NODES, [[0, 1], [1, 2]], max_edges=1)
    assert info.value.reason == "graph_too_large"


def test_projection_is_the_identity_on_the_frozen_graphs_and_matches_the_helper():
    for graph_id, raw in (
        ("s18-task-graph", json.loads(S18_GRAPH.read_bytes())),
        ("solidus", bundle_graph("solidus")),
        ("spree", bundle_graph("spree")),
    ):
        nodes, edges = V.project(raw["nodes"], raw["edges"])
        assert nodes == raw["nodes"] and edges == raw["edges"], graph_id
        assert (nodes, edges) == projection(raw["nodes"], raw["edges"])
    raw = json.loads(SYN_GRAPH.read_bytes())
    nodes, edges = V.project(raw["nodes"], raw["edges"])
    assert (
        (nodes, edges) == projection(raw["nodes"], raw["edges"]) == projected("synthetic-cliques")
    )
    assert nodes != raw["nodes"] and len(edges) < len(raw["edges"])  # reordered, deduplicated
    degree = [0] * len(nodes)
    for u, v in edges:
        degree[u] += 1
        degree[v] += 1
    assert degree.count(0) == 3 and len(nodes) == 51  # iso_a, iso_b and the loop-only node


# ----------------------------------------------------------------------------- quality

# Graph A: two triangles {0,1,2} and {3,4,5} joined by the bridge (2,3); m = 7.
# Degrees: 2 2 3 3 2 2. Partition [[0,1,2],[3,4,5]]: L_c = 3, D_c = 7 each,
# Q = 2 * (3/7 - (7/14)^2) = 2 * (12/28 - 7/28) = 5/14; coverage 6/7; k = 2 so the median is
# index ceil(2/2)-1 = 0 and p90 index ceil(18/10)-1 = 1, both size 3.
TWO_TRIANGLES = (6, [[0, 1], [0, 2], [1, 2], [3, 4], [3, 5], [4, 5], [2, 3]])


def test_quality_oracles_on_two_triangles_and_a_bridge():
    n, edges = TWO_TRIANGLES
    assert V.quality(n, edges, [[0, 1, 2], [3, 4, 5]]) == {
        "modularity": R(5, 14, "0.357143"),
        "coverage": R(6, 7, "0.857143"),
        "community_count": 2,
        "singleton_count": 0,
        "size_max": 3,
        "size_median": 3,
        "size_p90": 3,
        "disconnected_communities": 0,
        "disconnected_share": ZERO,
    }
    # All singletons: sum D^2 = 4+4+9+9+4+4 = 34, Q = (0 - 34) / (4 * 49) = -17/98; k = 6 so
    # median index (7//2)-1 = 2 and p90 index ceil(54/10)-1 = 5 (all size 1); no internal edge.
    assert V.quality(n, edges, [[i] for i in range(6)]) == {
        "modularity": R(-17, 98, "-0.173469"),
        "coverage": ZERO,
        "community_count": 6,
        "singleton_count": 6,
        "size_max": 1,
        "size_median": 1,
        "size_p90": 1,
        "disconnected_communities": 0,
        "disconnected_share": ZERO,
    }
    # One community: L = 7, D = 14, Q = 7/7 - (14/14)^2 = 0.
    assert V.quality(n, edges, [[0, 1, 2, 3, 4, 5]]) == {
        "modularity": ZERO,
        "coverage": R(1, 1, "1.000000"),
        "community_count": 1,
        "singleton_count": 0,
        "size_max": 6,
        "size_median": 6,
        "size_p90": 6,
        "disconnected_communities": 0,
        "disconnected_share": ZERO,
    }


# Graph H: path 0-1-2, path 3-4-5, node 6 isolated; m = 4; degrees 1 2 1 1 2 1 0.
# Partition [[0,2],[1],[3,4,5],[6]]: {0,2} is two NON-ADJACENT nodes (0-1 and 1-2 cross out), so
# its internal-edge subgraph is disconnected; {1} and {6} are singletons (6 is isolated);
# L = 0,0,2,0 and D = 2,2,4,0, Q = (4*4*2 - (4+4+16+0)) / (4*16) = 8/64 = 1/8; coverage 2/4.
# Sorted sizes [1,1,2,3], k = 4 (even): median index (5//2)-1 = 1 -> 1; p90 index
# ceil(36/10)-1 = 3 -> 3; one disconnected community of four -> 1/4.
HOLES = (7, [[0, 1], [1, 2], [3, 4], [4, 5]])


def test_quality_oracle_with_a_disconnected_community_and_an_isolated_node():
    n, edges = HOLES
    assert V.quality(n, edges, [[0, 2], [1], [3, 4, 5], [6]]) == {
        "modularity": R(1, 8, "0.125000"),
        "coverage": R(1, 2, "0.500000"),
        "community_count": 4,
        "singleton_count": 2,
        "size_max": 3,
        "size_median": 1,
        "size_p90": 3,
        "disconnected_communities": 1,
        "disconnected_share": R(1, 4, "0.250000"),
    }


def path_blocks(k):
    """A path whose contiguous blocks have sizes 1..k (n = k(k+1)/2, m = n-1)."""
    n = k * (k + 1) // 2
    blocks, start = [], 0
    for size in range(1, k + 1):
        blocks.append(list(range(start, start + size)))
        start += size
    return n, [[i, i + 1] for i in range(n - 1)], blocks


def test_size_statistics_at_ten_and_eleven_communities():
    # k = 10 (n = 55, m = 54): sizes 1..10; median index (11//2)-1 = 4 -> 5; p90 index
    # ceil(90/10)-1 = 8 -> 9. sum L = 45 so coverage 45/54 = 5/6. D = 1, 2s (s = 2..9), 19:
    # sum D^2 = 1 + 4*(285-1) + 361 = 1498, Q = (4*54*45 - 1498) / (4*54^2) = 4111/5832.
    n, edges, blocks = path_blocks(10)
    assert V.quality(n, edges, blocks) == {
        "modularity": R(4111, 5832, "0.704904"),
        "coverage": R(5, 6, "0.833333"),
        "community_count": 10,
        "singleton_count": 1,
        "size_max": 10,
        "size_median": 5,
        "size_p90": 9,
        "disconnected_communities": 0,
        "disconnected_share": ZERO,
    }
    # k = 11 (n = 66, m = 65): sizes 1..11; median index (12//2)-1 = 5 -> 6; p90 index
    # ceil(99/10)-1 = 9 -> 10. coverage 55/65 = 11/13. D = 1, 2s (2..10), 21:
    # sum D^2 = 1 + 4*(385-1) + 441 = 1978, Q = (4*65*55 - 1978) / (4*65^2) = 6161/8450.
    n, edges, blocks = path_blocks(11)
    assert V.quality(n, edges, blocks) == {
        "modularity": R(6161, 8450, "0.729112"),
        "coverage": R(11, 13, "0.846154"),
        "community_count": 11,
        "singleton_count": 1,
        "size_max": 11,
        "size_median": 6,
        "size_p90": 10,
        "disconnected_communities": 0,
        "disconnected_share": ZERO,
    }


# -------------------------------------------------------------------------------- ARI

# Unequal blocks on n = 6: P = [[0,1,2],[3,4],[5]] (3,2,1), Q = [[0,1],[2,3,4,5]] (2,4).
# Cells: {0,1}=2, {2}=1, {3,4}=2, {5}=1 -> index = 1+0+1+0 = 2; A = 3+1+0 = 4; B = 1+6 = 7;
# N = 15; E = 4*7/15 = 28/15; M = 11/2; ARI = (2 - 28/15) / (11/2 - 28/15) = (2/15)/(109/30)
# = 4/109.
P1 = [[0, 1, 2], [3, 4], [5]]
P2 = [[0, 1], [2, 3, 4, 5]]
LP = [[0, 1, 2, 3], [4, 5]]
ONE = [[0, 1, 2, 3, 4, 5]]


def test_ari_oracles_are_exact_fractions():
    assert V.ari(P1, P2, 6) == F(4, 109)
    assert V.ari(P2, P1, 6) == F(4, 109)
    assert V.ari(P1, P1, 6) == F(1)
    assert V.ari([[0, 1], [2, 3]], [[0, 1, 2, 3]], 4) == F(0)
    assert V.ari([[0], [1], [2], [3]], [[0], [1], [2], [3]], 4) == F(1)
    assert V.ari([[0], [1], [2], [3]], [[0, 1, 2, 3]], 4) == F(0)
    # LP = [[0,1,2,3],[4,5]] vs P1: cells {0,1,2}=3,{3}=1,{4}=1,{5}=1 -> index 3, A = 7, B = 4,
    # E = 28/15, M = 11/2: (3 - 28/15)/(11/2 - 28/15) = (17/15)/(109/30) = 34/109.
    assert V.ari(LP, P1, 6) == F(34, 109)
    # LP vs P2: cells {0,1}=2,{2,3}=2,{4,5}=2 -> index 3, A = 7, B = 7, E = 49/15, M = 7:
    # (3 - 49/15)/(7 - 49/15) = (-4/15)/(56/15) = -1/14.
    assert V.ari(LP, P2, 6) == F(-1, 14)
    # A single block agrees with nothing: index = B = E.
    assert V.ari(ONE, P1, 6) == F(0) and V.ari(P1, ONE, 6) == F(0)


def test_stability_oracle_covers_every_field():
    # Leiden (seed order) P1, P2, P1; LPA = LP. pairs (0,1),(0,2),(1,2) = 4/109, 1, 4/109;
    # min 4/109; mean (4/109 + 1 + 4/109)/3 = 39/109; one identical pair of three; two distinct
    # partitions; against LPA: 34/109, -1/14, 34/109.
    got = V.stability(LP, [P1, P2, P1], 6)
    assert got == {
        "pairwise_ari": [R(4, 109, "0.036697"), R(1, 1, "1.000000"), R(4, 109, "0.036697")],
        "min_pairwise_ari": R(4, 109, "0.036697"),
        "mean_pairwise_ari": R(39, 109, "0.357798"),
        "identical_pairs": 1,
        "identical_pair_share": R(1, 3, "0.333333"),
        "distinct_leiden_partitions": 2,
        "lpa_vs_leiden_ari": [
            R(34, 109, "0.311927"),
            R(-1, 14, "-0.071429"),
            R(34, 109, "0.311927"),
        ],
        "lpa": "deterministic_no_seed",
    }


def test_ratio_and_canonical_agree_with_the_s23_aggregate_bytes():
    aggregate = load_path_module(ROOT / "scripts/eval/v3_aggregate.py", "s36_v3_aggregate")
    for num, den in ((1, 2), (-1, 14), (0, 1), (5, 14), (2, 3), (-3, 7), (0, 0), (1, 1)):
        assert V.canonical(V.ratio_of(num, den)) == aggregate.canonical(aggregate.ratio(num, den))
    assert V.canonical({"b": [1, {"z": None}], "a": ACCENT}) == aggregate.canonical(
        {"b": [1, {"z": None}], "a": ACCENT}
    )
    assert V.ratio_of(2, 4) == R(1, 2, "0.500000") and V.ratio_of(5, 0) == R(0, 0, None)
    assert V.ratio_of(1, 8)["value"] == "0.125000" and V.ratio_of(1, 16)["value"] == "0.062500"
    # ROUND_HALF_EVEN: 1/2^7 = 0.0078125 -> 0.007812; 3/2^7 = 0.0234375 -> 0.023438
    assert V.ratio_of(1, 128)["value"] == "0.007812"
    assert V.ratio_of(3, 128)["value"] == "0.023438"


def test_the_frozen_quality_matches_the_sidecar_and_the_probe_modularity():
    side = {r["name"]: r["lpa_q"] for r in json.loads(SIDECAR.read_text())["repos"]}
    probe = load_path_module(ROOT / "scripts/verify-probe-verdict.py", "s36_probe_verdict")
    lpa = lpa_module()
    for name in ("solidus", "spree"):
        nodes, edges = projected(name)
        adjacency = {i: set() for i in range(len(nodes))}
        for u, v in edges:
            adjacency[u].add(v)
            adjacency[v].add(u)
        labels = lpa.label_propagation(adjacency, 100)
        assert labels == dict(enumerate(bundle_graph(name)["lpa_labels"]))
        communities = V.canonicalize([labels[i] for i in range(len(nodes))])
        q = V.quality(len(nodes), edges, communities)["modularity"]
        exact = F(q["num"], q["den"])
        assert abs(float(exact) - side[name]) < 1e-9, name
        listed = [labels[i] for i in range(len(nodes))]
        assert (
            abs(float(exact) - probe._modularity(len(nodes), [tuple(e) for e in edges], listed))
            < 1e-12
        )
    nodes, edges = projected("synthetic-cliques")
    labels = [i % 5 for i in range(len(nodes))]
    q = V.quality(len(nodes), edges, V.canonicalize(labels))["modularity"]
    assert abs(float(F(q["num"], q["den"])) - probe._modularity(
        len(nodes), [tuple(e) for e in edges], labels)) < 1e-12  # fmt: skip


# ----------------------------------------------------------------------- usefulness

# Ten nodes, communities C = [[0,1,2,3,4,5],[6,7],[8,9]]; task t1 has seeds [7,2] (exact_name)
# and gold {2,6,9}; t2 has no seeds and gold {1}.
# Expansion: seeds, then 7's community (6), then 2's community (0,1,3,4,5) ->
# [7,2,6,0,1,3,4,5]. b=2 -> [7,2] (hit 2); b=4 -> [7,2,6,0] (hits 2,6); b=8 -> all eight
# (hits 2,6; 9 is never reached).
C = [[0, 1, 2, 3, 4, 5], [6, 7], [8, 9]]
D2 = [[0, 1, 3, 4], [2, 5, 6, 7], [8, 9]]  # L = [7,2,5,6]: shorter than any budget above 4
EVIDENCE = [
    {"task_id": "t1", "seed_tier": "exact_name", "seeds": [7, 2], "gold": [2, 6, 9]},
    {"task_id": "t2", "seed_tier": "none", "seeds": [], "gold": [1]},
]


def test_expansion_order_with_a_community_larger_than_the_budget():
    assert V.expansion_order([7, 2], C) == [7, 2, 6, 0, 1, 3, 4, 5]
    # One seed inside the six-member community: the community alone exceeds budgets 2 and 4.
    assert V.expansion_order([3], C) == [3, 0, 1, 2, 4, 5]
    assert V.expansion_order([0, 1], C) == [0, 1, 2, 3, 4, 5]  # seeds in one community
    assert V.expansion_order([], C) == []
    assert V.expansion_order([7, 2], D2) == [7, 2, 5, 6]


def test_usefulness_rows_and_totals_by_hand():
    use = V.usefulness("m" * 64, "g", EVIDENCE, [("lpa", C), ("leiden-seed-0", D2)])
    assert use["budgets"] == [2, 4, 8] and use["proxy"] == "offline_community_expansion_proxy.v1"
    rows = {(r["run_id"], r["budget"], r["task_id"]): r for r in use["rows"]}
    assert len(rows) == 2 * 3 * 2 == len(use["rows"])

    def row(run, budget, task):
        r = rows[(run, budget, task)]
        return r["selected"], r["hits"], r["selected_count"], r["gold_count"]

    assert row("lpa", 2, "t1") == ([7, 2], 1, 2, 3)
    assert row("lpa", 4, "t1") == ([7, 2, 6, 0], 2, 4, 3)
    assert row("lpa", 8, "t1") == ([7, 2, 6, 0, 1, 3, 4, 5], 2, 8, 3)
    assert row("lpa", 8, "t2") == ([], 0, 0, 1)
    assert row("leiden-seed-0", 2, "t1") == ([7, 2], 1, 2, 3)
    assert row("leiden-seed-0", 8, "t1") == ([7, 2, 5, 6], 2, 4, 3)
    totals = {(t["run_id"], t["budget"]): t for t in use["totals"]}
    assert len(totals) == 6
    # lpa: b=2 hits 1 / selected 2 / gold 4; b=4 hits 2 / 4 / 4; b=8 hits 2 / 8 / 4.
    assert totals[("lpa", 2)]["recall"] == R(1, 4, "0.250000")
    assert totals[("lpa", 2)]["precision"] == R(1, 2, "0.500000")
    assert totals[("lpa", 4)]["recall"] == R(1, 2, "0.500000")
    assert totals[("lpa", 8)]["precision"] == R(1, 4, "0.250000")
    assert totals[("leiden-seed-0", 8)]["precision"] == R(1, 2, "0.500000")
    assert totals[("leiden-seed-0", 8)]["selected_count"] == 4
    assert all(t["manifest_sha256"] == "m" * 64 for t in use["totals"] + use["rows"])
    empty = V.usefulness("m" * 64, "g", [{"task_id": "t", "seed_tier": "none", "seeds": [],
                                          "gold": []}], [("lpa", C)])  # fmt: skip
    assert empty["totals"][0]["recall"] == R(0, 0, None)  # no gold: null, not zero


def test_task_evidence_table_over_the_frozen_s18_inputs():
    nodes, _ = projected("s18-task-graph")
    useful = json.loads(USEFUL.read_bytes())
    items = json.loads(S23_MANIFEST.read_bytes())["tasks"]
    got = {r["task_id"]: r for r in V.task_evidence(nodes, items, useful)}
    # Index: 0 send_request, 1 retry_request, 2 process_payment, 3 validate_payment,
    # 4 write_ledger, 5 render_text, 6 escape_text. 'phrase' has no exact name, so every node of
    # a file that holds the text is a seed (both app/client.py nodes); other/payments.py and
    # noise/task.py have no graph node.
    assert [nodes[i][2] for i in range(7)] == [
        "send_request", "retry_request", "process_payment", "validate_payment", "write_ledger",
        "render_text", "escape_text",
    ]  # fmt: skip
    assert got["payment"] == {"task_id": "payment", "seed_tier": "exact_name", "seeds": [2],
                              "gold": [2, 3, 4]}  # fmt: skip
    assert got["request"] == {"task_id": "request", "seed_tier": "exact_name", "seeds": [0],
                              "gold": [0, 1]}  # fmt: skip
    assert got["unicode"] == {"task_id": "unicode", "seed_tier": "exact_name", "seeds": [5],
                              "gold": [5, 6]}  # fmt: skip
    assert got["phrase"] == {"task_id": "phrase", "seed_tier": "lexical_file", "seeds": [0, 1],
                             "gold": [1]}  # fmt: skip
    assert list(got) == ["payment", "request", "unicode", "phrase"]
    # The unicode file holds multi-byte text, so its line offsets are measured in bytes.
    text = useful["source_files"]["app/render.py"]
    assert len(text.encode()) > len(text)


# ------------------------------------------------------------------------------ decision

LPA = {2: (F(1, 4), F(1, 2)), 4: (F(1, 2), F(1, 2)), 8: (F(1, 2), F(1, 4))}
BETTER = {2: (F(1, 2), F(1, 2)), 4: (F(1, 2), F(1, 2)), 8: (F(1, 2), F(1, 4))}


def decide(**changes):
    """The primitives of a run that qualifies, with one thing changed at a time."""
    args = {
        "provider_available": True,
        "gold_total": 5,
        "seeded_tasks": 3,
        "leiden_differs": [True] + [False] * 9,  # only one seed differs from LPA
        "lpa_scores": dict(LPA),
        "leiden_scores": {seed: dict(BETTER) for seed in range(10)},
        "mean_ari": {"g1": F(9, 10), "g2": F(1)},  # exactly on the floor
        "lpa_q": {"g1": F(1, 2), "g2": F(1, 3)},
        "leiden_q": {"g1": [F(1, 2)] * 10, "g2": [F(1, 3)] * 10},  # equal is not below
    }
    args.update(changes)
    got = V.decide(**args)
    assert set(got) == {"outcome", "reasons", "conditions"}
    return got["outcome"], got["reasons"], got["conditions"]


def test_decision_table_covers_every_outcome_and_reason_order():
    outcome, reasons, conditions = decide()
    assert (outcome, reasons) == ("leiden_candidate_for_review", [])
    assert conditions == {
        "provider_available": True,
        "task_evidence_present": True,
        "task_graph_partitions_differ": True,
        "usefulness_no_worse_all_budgets": True,
        "recall_gain_budgets": 1,
        "stability_floor_met": True,
        "modularity_not_below_lpa": True,
    }
    assert V.OUTCOMES == (
        "retain_lpa", "leiden_candidate_for_review", "inconclusive", "skipped_missing_provider",
    )  # fmt: skip
    # Skipped: every other condition is null and the reason is the missing provider.
    outcome, reasons, conditions = decide(provider_available=False)
    assert (outcome, reasons) == ("skipped_missing_provider", ["provider_missing"])
    assert conditions["provider_available"] is False
    assert [v for k, v in conditions.items() if k != "provider_available"] == [None] * 6
    # Inconclusive: no task evidence wins over identical partitions.
    assert decide(gold_total=0)[:2] == ("inconclusive", ["no_task_evidence"])
    assert decide(seeded_tasks=0)[:2] == ("inconclusive", ["no_task_evidence"])
    assert decide(gold_total=0, leiden_differs=[False] * 10)[:2] == (
        "inconclusive", ["no_task_evidence"],
    )  # fmt: skip
    assert decide(leiden_differs=[False] * 10)[:2] == (
        "inconclusive", ["task_graph_partitions_identical"],
    )  # fmt: skip
    # Retain: each reason alone, then all four in the fixed order.
    worse = {s: dict(BETTER) for s in range(10)}
    worse[3] = {**BETTER, 8: (F(1, 2), F(1, 8))}  # one seed, one budget: precision below LPA
    assert decide(leiden_scores=worse)[:2] == ("retain_lpa", ["usefulness_regression"])
    ties = {s: dict(LPA) for s in range(10)}
    assert decide(leiden_scores=ties)[:2] == ("retain_lpa", ["no_usefulness_gain"])  # tie
    assert decide(mean_ari={"g1": F(9, 10) - F(1, 10**30), "g2": F(1)})[:2] == (
        "retain_lpa", ["stability_below_floor"],
    )  # fmt: skip
    assert decide(leiden_q={"g1": [F(1, 2)] * 9 + [F(1, 2) - F(1, 10**9)], "g2": [F(1, 3)] * 10})[
        :2
    ] == ("retain_lpa", ["modularity_below_lpa"])
    outcome, reasons, conditions = decide(
        leiden_scores=ties,
        mean_ari={"g1": F(1, 2), "g2": F(1)},
        leiden_q={"g1": [F(1, 4)] * 10, "g2": [F(1, 3)] * 10},
    )
    assert (outcome, reasons) == ("retain_lpa", ["no_usefulness_gain", "stability_below_floor",
                                                 "modularity_below_lpa"])  # fmt: skip
    both = {s: {**ties[s], 2: (F(1, 8), F(1, 2))} for s in range(10)}  # worse recall at b=2
    assert decide(leiden_scores=both, mean_ari={"g1": F(0), "g2": F(1)},
                  lpa_q={"g1": F(2, 3), "g2": F(1, 3)})[:2] == (
        "retain_lpa", ["usefulness_regression", "no_usefulness_gain", "stability_below_floor",
                       "modularity_below_lpa"]
    )  # fmt: skip
    assert conditions["recall_gain_budgets"] == 0


def test_decision_boundaries():
    # Mean ARI exactly 9/10 passes (the baseline); just below fails; just above passes.
    assert decide()[2]["stability_floor_met"] is True
    assert decide(mean_ari={"g1": F(8999, 10000), "g2": F(1)})[2]["stability_floor_met"] is False
    assert decide(mean_ari={"g1": F(9001, 10000), "g2": F(1)})[2]["stability_floor_met"] is True
    # Every graph must meet the floor, not the average of graphs.
    assert decide(mean_ari={"g1": F(1), "g2": F(1, 2)})[2]["stability_floor_met"] is False
    # A recall tie is not a gain; a gain needs every seed strictly above LPA at that budget.
    assert decide(leiden_scores={s: dict(LPA) for s in range(10)})[2]["recall_gain_budgets"] == 0
    one_tie = {s: dict(BETTER) for s in range(10)}
    one_tie[9] = {**BETTER, 2: LPA[2]}
    cond = decide(leiden_scores=one_tie)[2]
    assert cond["recall_gain_budgets"] == 0 and cond["usefulness_no_worse_all_budgets"] is True
    assert decide(leiden_scores=one_tie)[:2] == ("retain_lpa", ["no_usefulness_gain"])
    # null versus non-null counts as worse and is never a gain; null equals null is fine.
    nulls = {**LPA, 2: (None, None)}
    assert decide(lpa_scores=nulls)[2]["usefulness_no_worse_all_budgets"] is False
    # (budgets 4 and 8 tie with LPA, and b=2 cannot be a gain against a null baseline)
    assert decide(lpa_scores=nulls)[:2] == (
        "retain_lpa", ["usefulness_regression", "no_usefulness_gain"],
    )  # fmt: skip
    both_null = {s: {**BETTER, 2: (None, None)} for s in range(10)}
    cond = decide(lpa_scores=nulls, leiden_scores=both_null)[2]
    assert cond["usefulness_no_worse_all_budgets"] is True and cond["recall_gain_budgets"] == 0
    half_null = {s: {**BETTER, 2: (None, F(1, 2))} for s in range(10)}
    assert decide(leiden_scores=half_null)[2]["usefulness_no_worse_all_budgets"] is False
    # A single seed that differs from LPA is enough to leave the identical-partition branch.
    assert decide(leiden_differs=[False] * 9 + [True])[0] == "leiden_candidate_for_review"
    assert decide(leiden_differs=[True] + [False] * 9)[0] == "leiden_candidate_for_review"
    assert decide(leiden_differs=[False] * 10)[0] == "inconclusive"
    # Modularity alone can never produce a candidate: every other bar failing still retains LPA.
    outcome, reasons, _ = decide(
        leiden_scores={s: dict(LPA) for s in range(10)},
        lpa_q={"g1": F(1, 100), "g2": F(1, 100)},
    )
    assert (outcome, reasons) == ("retain_lpa", ["no_usefulness_gain"])
    # Equal modularity meets the (zero) margin; any shortfall of one seed misses it.
    assert decide()[2]["modularity_not_below_lpa"] is True
    short = {"g1": [F(1, 2)] * 10, "g2": [F(1, 3)] * 9 + [F(1, 3) - F(1, 10**12)]}
    assert decide(leiden_q=short)[2]["modularity_not_below_lpa"] is False


def test_the_frozen_thresholds_and_constants_are_pinned_by_value():
    assert F(9, 10) == V.FROZEN_STABILITY_FLOOR
    assert V.FROZEN_MIN_RECALL_GAIN_BUDGETS == 1
    assert F(0) == V.FROZEN_MODULARITY_MARGIN
    assert tuple(range(10)) == V.FROZEN_SEEDS and V.FROZEN_BUDGETS == (2, 4, 8)
    assert V.FROZEN_PARTITION_TYPE == "ModularityVertexPartition" and V.FROZEN_N_ITERATIONS == -1
    assert V.FROZEN_LPA_MAX_ITERATIONS == 100
    assert V.FROZEN_BOUNDS == {
        "max_input_bytes": 16777216, "max_nodes": 200000, "max_edges": 1000000,
        "run_timeout_seconds": 600,
    }  # fmt: skip
    assert V.FROZEN_TASK_INPUT_SHA256 == (
        "236c0def27a416dd93a411a4e40d2666510320c4d9f9cfff08f29a1145cc8fe6"
    )
    assert V.FROZEN_USEFUL_SPANS_SHA256 == (
        "07553bd3d51be01a1d522fc59c240bf41ecf312a051c413c609585c60999b507"
    )
    assert V.FROZEN_BUNDLE_SHA256 == (
        "c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe"
    )
    assert V.PRODUCTION_MANIFEST_SHA256 == PRODUCTION_SHA
    assert V.FROZEN_DECISION["stability_min_mean_pairwise_ari"] == {"num": 9, "den": 10}
    assert V.FROZEN_DECISION["min_budgets_with_recall_gain"] == 1
    manifest = json.loads(PROD_MANIFEST.read_bytes())
    assert manifest["decision"] == V.FROZEN_DECISION and manifest["bounds"] == V.FROZEN_BOUNDS
    assert manifest["algorithms"]["leiden"]["provider"] == {
        "kind": "real", "distributions": {"igraph": "1.0.0", "leidenalg": "0.12.0"},
    }  # fmt: skip
    assert [g["id"] for g in manifest["graphs"]] == ["s18-task-graph", "solidus", "spree"]
    assert V.digest_of(manifest["tasks"]["items"]) == V.FROZEN_TASK_INPUT_SHA256
    assert len(V.LIMITATIONS) == 12 and len(set(V.LIMITATIONS)) == 12
    assert len(V.RUNNER_REJECTIONS) == 20 and len(V.VERIFIER_REJECTIONS) == 33
    assert len(set(V.RUNNER_REJECTIONS)) == 20 and len(set(V.VERIFIER_REJECTIONS)) == 33
    assert V.VERIFIER_REJECTIONS[:11] == V.RUNNER_REJECTIONS[:11]
    assert V.PARTITION_REJECTIONS == (
        "partition_invalid_type", "partition_node_unknown", "partition_node_duplicated",
        "partition_node_omitted", "partition_noncanonical", "partition_node_table_mismatch",
    )  # fmt: skip
    assert V.SEED_TIERS == ("exact_name", "lexical_file", "none")
    assert V.STATUSES == ("complete", "skipped_missing_provider")
    assert "recall_gain_budgets" in decide()[2]


# ------------------------------------------------------------- strict loading, partitions


def test_strict_json_rejects_what_the_artifacts_may_never_contain():
    load = V.load_strict
    assert load(b'{"a":[1,2,{"b":null}]}') == {"a": [1, 2, {"b": None}]}
    for raw in (
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b'{"a":1.5}',
        b'{"a":1e3}',
        b"\xef\xbb\xbf{}",
        b'{"a":"\xff"}',
        b"[" * 200000 + b"]" * 200000,
        b'{"a":',
        b"",
    ):
        with pytest.raises(ValueError):
            load(raw)
    # Inputs of other slices may carry floats; duplicates and non-finite numbers still fail.
    assert load(b'{"a":1.5}', allow_floats=True) == {"a": 1.5}
    with pytest.raises(ValueError):
        load(b'{"a":1,"a":2}', allow_floats=True)


def test_validate_partition_directly_for_bool_float_negative_empty_and_repeats():
    check = V.validate_partition
    assert check([[0, 1], [2]], 3) is None
    assert check([[True, 1], [2]], 3) == "partition_invalid_type"
    assert check([[0.0, 1], [2]], 3) == "partition_invalid_type"
    assert check([[0, 1], 2], 3) == "partition_invalid_type"
    assert check({"0": [0]}, 1) == "partition_invalid_type"
    assert check([["0"]], 1) == "partition_invalid_type"
    assert check([[-1, 0], [1]], 2) == "partition_node_unknown"
    assert check([[0, 1], []], 2) == "partition_noncanonical"  # an empty community
    assert check([[1, 1]], 2) == "partition_node_duplicated"  # [1,1] inside one community
    assert check([[0], [1, 1]], 2) == "partition_node_duplicated"
    assert check([[0, 1], [2], [3]], 3) == "partition_node_unknown"
    assert check([], 1) == "partition_node_omitted"
    assert check([[1], [0]], 2) == "partition_noncanonical"  # wrong community order


def edited_partitions(tmp_path, fx, plain_edit):
    copy = copy_run(fx["dir"], tmp_path / "edit")
    plain = __import__("gzip").decompress((copy / "partitions.json.gz").read_bytes())
    reseal_raw(copy, plain_edit(plain))
    return copy


@pytest.mark.parametrize(
    ("name", "edit"),
    [
        ("float", lambda p: p.replace(b'"n":7', b'"n":7.0', 1)),
        ("nan", lambda p: p.replace(b'"n":7', b'"n":NaN', 1)),
        ("duplicate_key", lambda p: p.replace(b'"n":7', b'"n":7,"n":7', 1)),
        ("float_member", lambda p: p.replace(b"[[0,1]", b"[[0.5,1]", 1)),
    ],
)
def test_floats_nan_and_duplicate_keys_reach_the_cli_as_invalid_json(
    fx, tmp_path, capsys, name, edit
):
    copy = edited_partitions(tmp_path, fx, edit)
    assert V.main(
        ["--results", str(copy), "--manifest", str(FIX_MANIFEST), "--manifest-sha256",
         sha(FIX_MANIFEST.read_bytes())]
    ) == 1  # fmt: skip
    assert capsys.readouterr().err == (
        "Community verification rejected: artifact_invalid_json: partitions\n"
    )


def test_spree_scale_fractions_serialise_and_quantise(tmp_path):
    """Ten very different partitions of the spree graph give 55 ARIs with large denominators."""
    nodes, edges = projected("spree")
    n = len(nodes)
    base = V.canonicalize(bundle_graph("spree")["lpa_labels"])
    parts = []
    for seed in range(10):
        labels = [(v * (seed + 3) + (v // (seed + 2))) % (40 + 7 * seed) for v in range(n)]
        parts.append(V.canonicalize(labels))
    assert len({json.dumps(p) for p in parts}) == 10
    got = V.stability(base, parts, n)
    biggest = max(
        [got["mean_pairwise_ari"], got["min_pairwise_ari"], *got["pairwise_ari"]],
        key=lambda r: len(str(r["den"])),
    )
    digits = len(str(biggest["den"]))
    assert 200 < digits < 4300, digits  # large, yet inside the int<->str limit
    raw = V.canonical(got)  # serialising the exact integers works on every interpreter
    assert json.loads(raw) == got
    assert all(len(r["value"]) == 8 or r["value"].startswith("-") for r in got["pairwise_ari"])
    mean = F(got["mean_pairwise_ari"]["num"], got["mean_pairwise_ari"]["den"])
    pairs = [F(r["num"], r["den"]) for r in got["pairwise_ari"]]
    assert mean == sum(pairs, F(0)) / 45
    quantised = V.ratio_of(mean.numerator, mean.denominator)
    assert quantised == got["mean_pairwise_ari"] and quantised["value"].count(".") == 1
    q = V.quality(n, edges, parts[0])
    assert V.canonical(q) and q["modularity"]["den"] > 1


# ------------------------------------------------------------------- usage errors

RUNNER_USAGE = [
    ([], "command"),
    (["bogus"], "command"),
    (["run", "--manifest-sha256", "a" * 64, "--out", "OUT"], "manifest"),
    (["run", "--manifest", "M", "--out", "OUT"], "manifest_sha256"),
    (["run", "--manifest", "M", "--manifest-sha256", "zz", "--out", "OUT"], "manifest_sha256"),
    (["run", "--manifest", "M", "--manifest-sha256", "a" * 64], "out"),
    (["run", "--manifest", "M", "--manifest-sha256", "a" * 64, "--out", "EXISTS"], "out_exists"),
    (["run", "--manifest", "M", "--manifest-sha256", "a" * 64, "--out", "MISSING/x"], "out"),
    (
        ["run", "--manifest", "M", "--manifest-sha256", "a" * 64, "--out", "INSIDE"],
        "out_inside_mcp_server",
    ),
    (
        [
            "run",
            "--manifest",
            "M",
            "--manifest-sha256",
            "a" * 64,
            "--out",
            "OUT",
            "--repo-root",
            "MISSING",
        ],
        "repo_root",
    ),
    (
        ["run", "--manifest", "M", "--manifest-sha256", "a" * 64, "--out", "OUT", "--bogus"],
        "arguments",
    ),
    (
        [
            "run",
            "--manifest",
            "M",
            "--manifest-sha256",
            "a" * 64,
            "--out",
            "OUT",
            "--require-provider=yes",
        ],
        "arguments",
    ),
    (["run", "--manifest"], "manifest"),
    (["run", "--out"], "out"),
]
VERIFIER_USAGE = [
    ([], "results"),
    (["--results", "R", "--manifest"], "manifest"),
    (["--results", "R", "--manifest-sha256", "zz"], "manifest_sha256"),
    (["--results", "R", "--manifest-sha256"], "manifest_sha256"),
    (["--results", "R", "--repo-root", "MISSING"], "repo_root"),
    (["--results", "R", "--max-artifact-bytes", "0"], "max_artifact_bytes"),
    (["--results", "R", "--max-artifact-bytes", "abc"], "max_artifact_bytes"),
    (["--results", "R", "--max-artifact-bytes", "268435457"], "max_artifact_bytes"),
    (["--results", "R", "--max-artifact-bytes", "-5"], "max_artifact_bytes"),
    (["--results", "R", "--bogus"], "arguments"),
    (["stray"], "arguments"),
]


def fill(argv, tmp_path):
    out = tmp_path / "exists"
    out.mkdir(exist_ok=True)
    table = {
        "M": str(FIX_MANIFEST),
        "OUT": str(tmp_path / "fresh"),
        "EXISTS": str(out),
        "MISSING": str(tmp_path / "missing"),
        "MISSING/x": str(tmp_path / "missing" / "x"),
        "INSIDE": str(ROOT / "mcp-server" / "s36-never-created"),
        "R": str(tmp_path),
    }
    return [table.get(a, a) for a in argv]


@pytest.mark.parametrize(("argv", "name"), RUNNER_USAGE)
def test_runner_usage_errors_exit_two_with_the_name(argv, name, tmp_path, capsys):
    with pytest.raises(SystemExit) as info:
        runner().main(fill(argv, tmp_path))
    assert info.value.code == 2
    seen = capsys.readouterr()
    assert seen.out == "" and seen.err == f"Usage error: {name}\n"
    assert leftover(tmp_path) == [] and not (tmp_path / "fresh").exists()


@pytest.mark.parametrize(("argv", "name"), VERIFIER_USAGE)
def test_verifier_usage_errors_exit_two_with_the_name(argv, name, tmp_path, capsys):
    with pytest.raises(SystemExit) as info:
        V.main(fill(argv, tmp_path))
    assert info.value.code == 2
    seen = capsys.readouterr()
    assert seen.out == "" and seen.err == f"Usage error: {name}\n"


def test_every_usage_name_is_reached():
    assert {name for _, name in RUNNER_USAGE} == set(V.RUNNER_USAGE_NAMES)
    assert {name for _, name in VERIFIER_USAGE} == set(V.VERIFIER_USAGE_NAMES)
    assert len(V.RUNNER_USAGE_NAMES) == 8 and len(V.VERIFIER_USAGE_NAMES) == 6
    # The CLI exposes no bound override: a bound name is an unknown argument.
    for flag in ("--max-nodes", "--timeout", "--run-timeout-seconds", "--max-input-bytes"):
        with pytest.raises(SystemExit) as info:
            runner().main(["run", flag, "1"])
        assert info.value.code == 2


# ----------------------------------------------------- runner rejections, exact stderr

SYN_REL = "mcp-server/tests/fixtures/community_experiment/graphs/synthetic-cliques.v1.json"
S18_REL = "mcp-server/tests/fixtures/community_experiment/s18-task-graph.v1.json"


class Case:
    """One way to reach a rejection: `go(tmp)` returns the finished child process."""

    def __init__(self, go, text):
        self.go = go
        self.text = text


def with_manifest(tmp, manifest, **kwargs):
    path = tmp / "m.json"
    written = write_manifest(path, manifest)
    value = kwargs.pop("sha_value", None) or written
    return run_experiment(path, value, tmp / "out", **kwargs)


def edited_manifest(edit):
    manifest = manifest_copy()
    edit(manifest)
    return manifest


def flip_graph(tmp):
    root = repo_copy(tmp / "copy")
    graph = root / S18_REL
    graph.write_bytes(graph.read_bytes().replace(b"send_request", b"send_requesx"))
    return root


def no_edges_case(tmp):
    root = repo_copy(tmp / "copy")
    graph = json.loads((root / SYN_REL).read_bytes())
    graph["edges"] = [[0, 0]]
    raw = canonical(graph)
    (root / SYN_REL).write_bytes(raw)
    manifest = manifest_copy()
    manifest["graphs"][1]["sha256"] = sha(raw)
    return with_manifest(tmp, manifest, repo_root=root)


def missing_graph(tmp):
    root = repo_copy(tmp / "copy")
    (root / S18_REL).unlink()
    return run_experiment(FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), tmp / "out", repo_root=root)


def anchor_case(tmp):
    root = repo_copy(tmp / "copy")
    path = root / "atlas/evals/v3/manifest.v1.json"
    path.write_bytes(path.read_bytes().replace(b'"text":"send_request"', b'"text":"send_reques"'))
    return run_experiment(FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), tmp / "out", repo_root=root)


def site_without_metadata(tmp):
    site = tmp / "site"
    for name in ("igraph", "leidenalg"):
        shutil.copytree(SITE / name, site / name)
    return site


def fixture_run(tmp, **env):
    return run_experiment(FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), tmp / "out", **env)


def driver_run(tmp, overrides, **env):
    args = ["run", "--manifest", str(FIX_MANIFEST), "--manifest-sha256"]
    args += [sha(FIX_MANIFEST.read_bytes()), "--out", str(tmp / "out")]
    return run_driver(overrides, args, **env)


RUNNER_CASES = {
    "manifest_unreadable": Case(
        lambda t: run_experiment(t / "missing.json", "a" * 64, t / "out"), "manifest_unreadable"
    ),
    "manifest_invalid": Case(
        lambda t: with_manifest(t, edited_manifest(lambda m: m.update(unexpected=1))),
        "manifest_invalid",
    ),
    "manifest_digest_mismatch": Case(
        lambda t: with_manifest(t, manifest_copy(), sha_value="0" * 64), "manifest_digest_mismatch"
    ),
    "manifest_constants_mismatch": Case(
        lambda t: with_manifest(t, edited_manifest(lambda m: m["bounds"].update(max_nodes=5))),
        "manifest_constants_mismatch",
    ),
    "provider_pin_invalid": Case(
        lambda t: with_manifest(
            t,
            edited_manifest(lambda m: m["algorithms"]["leiden"]["provider"].update(kind="real")),
        ),
        "provider_pin_invalid",
    ),
    "input_unreadable": Case(missing_graph, "input_unreadable: s18-task-graph"),
    "input_too_large": Case(
        lambda t: driver_run(t, {"max_input_bytes": 10}), "input_too_large: s18-task-graph"
    ),
    "input_hash_mismatch": Case(
        lambda t: run_experiment(
            FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), t / "out", repo_root=flip_graph(t)
        ),
        "input_hash_mismatch: s18-task-graph",
    ),
    "task_anchor_mismatch": Case(anchor_case, "task_anchor_mismatch: s23_manifest"),
    "graph_invalid": Case(no_edges_case, "graph_invalid: synthetic-cliques"),
    "graph_too_large": Case(
        lambda t: driver_run(t, {"max_nodes": 3}), "graph_too_large: s18-task-graph"
    ),
    "provider_required_missing": Case(
        lambda t: run_experiment(
            PROD_MANIFEST,
            PRODUCTION_SHA,
            t / "out",
            site=False,
            flags=("-S",),
            extra=["--require-provider"],
        ),
        "provider_required_missing",
    ),
    "provider_import_failed": Case(
        lambda t: fixture_run(t, ATLAS_S36_FIXTURE_FAULT="import_error"), "provider_import_failed"
    ),
    "provider_version_mismatch": Case(
        lambda t: run_experiment(PROD_MANIFEST, PRODUCTION_SHA, t / "out"),
        "provider_version_mismatch",
    ),
    "provider_changed_during_run": Case(
        lambda t: fixture_run(t, ATLAS_S36_FIXTURE_FAULT="version_skew"),
        "provider_changed_during_run: leiden-seed-0",
    ),
    "measurement_failed": Case(
        lambda t: fixture_run(t, ATLAS_S36_FIXTURE_FAULT="exit1"),
        "measurement_failed: leiden-seed-0",
    ),
    "run_timeout": Case(
        lambda t: driver_run(t, {"run_timeout_seconds": 3}, ATLAS_S36_FIXTURE_FAULT="sleep"),
        "run_timeout: leiden-seed-0",
    ),
    "partition_invalid": Case(
        lambda t: fixture_run(t, ATLAS_S36_FIXTURE_FAULT="omit"), "partition_invalid: leiden-seed-0"
    ),
    "self_check_failed": Case(
        lambda t: driver_run(t, {"hook_self_check": True}), "self_check_failed"
    ),
    "output_write_failed": Case(
        lambda t: driver_run(t, {"hook_make_temp": True}), "output_write_failed"
    ),
}


def test_every_runner_rejection_has_a_case():
    assert tuple(RUNNER_CASES) == V.RUNNER_REJECTIONS


@pytest.mark.parametrize("reason", V.RUNNER_REJECTIONS)
def test_runner_rejection_text_is_exact_and_leaves_nothing_behind(reason, tmp_path):
    case = RUNNER_CASES[reason]
    sandbox = tmp_path / "box"
    sandbox.mkdir()
    done = case.go(sandbox)
    rejected(done, case.text)
    assert case.text.split(":")[0] == reason
    assert not (sandbox / "out").exists() and leftover(sandbox) == []


def test_a_package_without_metadata_is_an_import_failure(tmp_path):
    site = site_without_metadata(tmp_path)
    done = run_experiment(
        FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), tmp_path / "out", site=False,
        PYTHONPATH=str(site),
    )  # fmt: skip
    rejected(done, "provider_import_failed")
    assert not (tmp_path / "out").exists() and leftover(tmp_path) == []


def test_an_unwritable_parent_is_an_output_write_failure(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("permission bits do not bind root")
    parent = tmp_path / "locked"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        done = run_experiment(FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), parent / "out")
    finally:
        parent.chmod(0o700)
    rejected(done, "output_write_failed")
    assert list(parent.iterdir()) == []


def test_projection_temp_directories_do_not_leak(tmp_path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    for fault in ("omit", "exit1"):
        done = run_experiment(
            FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), tmp_path / "out", TMPDIR=str(scratch),
            ATLAS_S36_FIXTURE_FAULT=fault,
        )  # fmt: skip
        assert done.returncode == 1
    done = run_experiment(
        FIX_MANIFEST, sha(FIX_MANIFEST.read_bytes()), tmp_path / "ok", TMPDIR=str(scratch)
    )
    assert done.returncode == 0, done.stderr
    assert list(scratch.iterdir()) == []


# ------------------------------------------------------- verifier rejections, exact text


def vmain(capsys, results, *, manifest=FIX_MANIFEST, sha_value=None, root=None, extra=(),
          overrides=None):  # fmt: skip
    argv = ["--results", str(results), "--manifest", str(manifest), "--manifest-sha256"]
    argv.append(sha_value or sha(Path(manifest).read_bytes()))
    if root is not None:
        argv += ["--repo-root", str(root)]
    code = V.main([*argv, *extra], overrides=overrides)
    seen = capsys.readouterr()
    return code, seen.out, seen.err


def corrupt_json(path, old, new):
    path.write_bytes(path.read_bytes().replace(old, new, 1))


def part_edit(fn):
    def apply(doc):
        fn(doc["graphs"][1]["runs"])

    return apply


def result_edit(fn):
    return lambda base, tmp: tamper_result(base, fn)


def build_verifier_cases(fx, tmp):
    """reason -> (results dir, kwargs for vmain, exact detail text)."""
    cases = {}
    base = fx["dir"]

    def fresh(name):
        return copy_run(base, tmp / name)

    manifest_path = tmp / "m.json"

    def variant(edit, name):
        manifest = manifest_copy()
        edit(manifest)
        path = tmp / (name + ".json")
        return path, write_manifest(path, manifest)

    cases["manifest_unreadable"] = (
        base,
        {"manifest": tmp / "none.json", "sha_value": "a" * 64},
        "",
    )
    path, value = variant(lambda m: m.update(unexpected=1), "extra")
    cases["manifest_invalid"] = (base, {"manifest": path, "sha_value": value}, "")
    cases["manifest_digest_mismatch"] = (base, {"sha_value": "0" * 64}, "")
    path, value = variant(lambda m: m["bounds"].update(max_edges=5), "bounds")
    cases["manifest_constants_mismatch"] = (base, {"manifest": path, "sha_value": value}, "")
    path, value = variant(
        lambda m: m["algorithms"]["leiden"]["provider"].update(kind="real"), "pin"
    )
    cases["provider_pin_invalid"] = (base, {"manifest": path, "sha_value": value}, "")
    gone = repo_copy(tmp / "gone")
    (gone / S18_REL).unlink()
    cases["input_unreadable"] = (base, {"root": gone}, ": s18-task-graph")
    cases["input_too_large"] = (base, {"overrides": {"max_input_bytes": 10}}, ": s18-task-graph")
    cases["input_hash_mismatch"] = (base, {"root": flip_graph(tmp)}, ": s18-task-graph")
    anchored = repo_copy(tmp / "anchor")
    target = anchored / "atlas/evals/v3/manifest.v1.json"
    corrupt_json(target, b'"text":"send_request"', b'"text":"send_reques"')
    cases["task_anchor_mismatch"] = (base, {"root": anchored}, ": s23_manifest")
    broken = repo_copy(tmp / "nograph")
    graph = json.loads((broken / SYN_REL).read_bytes())
    graph["edges"] = []
    raw = canonical(graph)
    (broken / SYN_REL).write_bytes(raw)
    path, value = variant(lambda m: m["graphs"][1].update(sha256=sha(raw)), "nograph")
    cases["graph_invalid"] = (
        base, {"manifest": path, "sha_value": value, "root": broken}, ": synthetic-cliques",
    )  # fmt: skip
    cases["graph_too_large"] = (base, {"overrides": {"max_nodes": 3}}, ": s18-task-graph")
    cases["artifact_unreadable"] = (tmp / "no-such-dir", {}, ": result")
    cases["artifact_too_large"] = (base, {"extra": ["--max-artifact-bytes", "100"]}, ": result")
    dup = fresh("dup")
    corrupt_json(dup / "result.json", b'{"canonical_sha256"', b'{"format":"x","canonical_sha256"')
    cases["artifact_invalid_json"] = (dup, {}, ": result")
    schema = fresh("schema")
    corrupt_json(schema / "result.json", b'{"canonical_sha256"', b'{"extra":1,"canonical_sha256"')
    cases["artifact_schema_invalid"] = (schema, {}, ": result")
    unsealed = fresh("unsealed")
    corrupt_json(unsealed / "result.json", b'"status":"complete"', b'"status":"skipped"')
    cases["canonical_digest_mismatch"] = (unsealed, {}, "")
    stale = fresh("stale")
    plain = __import__("gzip").decompress((stale / "partitions.json.gz").read_bytes())
    (stale / "partitions.json.gz").write_bytes(
        __import__("gzip").compress(plain.replace(b'"n":7', b'"n":8', 1), 9, mtime=0)
    )
    cases["partitions_digest_mismatch"] = (stale, {}, "")
    ref = fresh("ref")
    tamper_result(ref, lambda r: r.update(manifest_sha256="1" * 64))
    cases["manifest_reference_mismatch"] = (ref, {}, ": result")
    proj = fresh("proj")
    tamper_partitions(proj, lambda d: d["graphs"][0].update(projection_sha256="2" * 64))
    cases["projection_mismatch"] = (proj, {}, ": s18-task-graph")
    runs = fresh("runs")
    tamper_partitions(runs, lambda d: d["graphs"][1]["runs"].pop())
    cases["run_set_mismatch"] = (runs, {}, ": synthetic-cliques")
    for reason, (action, wanted) in PARTITION_ACTIONS.items():
        copy = fresh(reason)
        tamper_partitions(copy, part_edit(action))
        cases[wanted] = (copy, {}, ": leiden-seed-0")
    swap = fresh("swap")
    tamper_partitions(swap, part_edit(lambda r: r[0].update(communities=r[1]["communities"])))
    cases["lpa_rerun_mismatch"] = (swap, {}, ": synthetic-cliques")
    metric = fresh("metric")
    tamper_result(metric, lambda r: r["graphs"][0]["runs"][0]["quality"].update(size_max=99))
    cases["metric_mismatch"] = (metric, {}, "")
    useful = fresh("useful")
    tamper_result(useful, lambda r: r["usefulness"]["totals"][0].update(hits=99))
    cases["usefulness_mismatch"] = (useful, {}, "")
    decision = fresh("decision")
    tamper_result(decision, lambda r: r["decision"].update(reasons=["no_usefulness_gain"]))
    cases["decision_mismatch"] = (decision, {}, "")
    skipped = fresh("skipped")
    tamper_result(skipped, lambda r: r.update(status="skipped_missing_provider"))
    cases["skipped_state_inconsistent"] = (skipped, {}, "")
    record = fresh("record")
    tamper_result(record, lambda r: r["provider"].update(kind="real"))
    cases["provider_record_mismatch"] = (record, {}, "")
    tel = fresh("tel")
    telemetry = json.loads((tel / "telemetry.json").read_bytes())
    telemetry["result_canonical_sha256"] = "3" * 64
    (tel / "telemetry.json").write_bytes(canonical(telemetry))
    cases["telemetry_invalid"] = (tel, {}, "")
    assert manifest_path
    return cases


PARTITION_ACTIONS = {
    "omitted": (lambda r: r[1]["communities"][0].pop(), "partition_node_omitted"),
    "duplicated": (
        lambda r: r[1]["communities"][1].append(r[1]["communities"][0][0]),
        "partition_node_duplicated",
    ),
    "unknown": (lambda r: r[1]["communities"][0].append(-1), "partition_node_unknown"),
    "unsorted": (lambda r: r[1]["communities"][0].reverse(), "partition_noncanonical"),
    "bool": (lambda r: r[1]["communities"][0].__setitem__(0, True), "partition_invalid_type"),
    "table": (lambda r: None, "partition_node_table_mismatch"),
}


def test_every_verifier_rejection_has_a_case_and_the_exact_text(fx, tmp_path, capsys):
    # The foreign node table is a different edit; build it here, the rest from the table.
    PARTITION_ACTIONS["table"] = (lambda r: r[1].__setitem__("n", r[1]["n"]), "x")
    del PARTITION_ACTIONS["table"]
    cases = build_verifier_cases(fx, tmp_path)
    table = tmp_path / "table"
    copy_run(fx["dir"], table)
    tamper_partitions(table, lambda d: d["graphs"][1].update(nodes_sha256="4" * 64))
    cases["partition_node_table_mismatch"] = (table, {}, ": synthetic-cliques")
    assert set(cases) == set(V.VERIFIER_REJECTIONS)
    assert len(cases) == 33
    for reason, (results, kwargs, detail) in cases.items():
        code, out, err = vmain(capsys, results, **kwargs)
        assert (code, out) == (1, ""), reason
        assert err == f"Community verification rejected: {reason}{detail}\n", (reason, err)
    # Control: the untouched run still verifies.
    code, out, err = vmain(capsys, fx["dir"])
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"


def test_telemetry_must_be_closed_ints_bound_and_complete(fx, tmp_path, capsys):
    def tamper(edit):
        copy = copy_run(fx["dir"], tmp_path / f"t{len(list(tmp_path.iterdir()))}")
        telemetry = json.loads((copy / "telemetry.json").read_bytes())
        edit(telemetry)
        (copy / "telemetry.json").write_bytes(canonical(telemetry))
        return vmain(capsys, copy)

    edits = (
        lambda t: t.update(extra=1),
        lambda t: t.update(canonical=True),
        lambda t: t.update(manifest_sha256="5" * 64),
        lambda t: t["runs"].pop(),
        lambda t: t["runs"][0].update(elapsed_ns=-1),
        lambda t: t["runs"][0].update(elapsed_ns=1.5),
        lambda t: t["runs"][0].update(rss_source="unknown"),
        lambda t: t["platform"].update(machine=1),
    )
    for edit in edits:
        code, _out, err = tamper(edit)
        assert (code, err) == (1, "Community verification rejected: telemetry_invalid\n")


def test_the_verifier_reads_its_library_and_lpa_from_the_repo_not_the_results(fx, tmp_path, capsys):
    """Planted modules inside the results directory are never loaded."""
    copy = copy_run(fx["dir"], tmp_path / "planted")
    (copy / "verify-community-experiment.py").write_text("raise SystemExit(99)\n")
    (copy / "label_propagation.py").write_text("raise SystemExit(98)\n")
    code, out, err = vmain(capsys, copy)
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"
    # An LPA file whose bytes differ from the manifest pin is refused, not executed.
    root = repo_copy(tmp_path / "lpa")
    (root / "mcp-server/src/atlas_aci/label_propagation.py").write_text("raise SystemExit(97)\n")
    code, out, err = vmain(capsys, copy, root=root)
    assert (code, err) == (1, "Community verification rejected: input_hash_mismatch: lpa_module\n")


# ------------------------------------------------------------ the S18 export and the provider


EXPORTER = ROOT / "scripts/probe-export-confident-graph.py"


def test_s18_task_graph_reproduces(tmp_path):
    """The committed task graph is exactly what the unchanged exporter yields for the S18 repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in json.loads(USEFUL.read_bytes())["source_files"].items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    out = tmp_path / "export.json"
    env = {"PYTHONPATH": str(ROOT / "mcp-server/src"), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/probe-export-confident-graph.py"),
            str(repo),
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=240,
        env={**os.environ, **env},
    )
    assert done.returncode == 0, done.stderr
    exported = json.loads(out.read_text())
    committed = json.loads(S18_GRAPH.read_bytes())
    assert exported["nodes"] == committed["nodes"] and exported["edges"] == committed["edges"]
    assert committed["format"] == "atlas-community-graph.v1"
    assert committed["graph_id"] == "s18-task-graph"
    assert committed["provenance"]["exporter_sha256"] == sha(
        (ROOT / "scripts/probe-export-confident-graph.py").read_bytes()
    )
    assert committed["provenance"]["source_fixture_sha256"] == sha(USEFUL.read_bytes())
    assert S18_GRAPH.read_bytes() == canonical(committed)


def test_real_provider_replay(tmp_path):
    """Runs only where leidenalg and igraph are installed: a real run replays byte for byte."""
    import importlib.metadata as metadata

    pytest.importorskip("leidenalg")
    pytest.importorskip("igraph")
    versions = {n: metadata.version(n) for n in ("igraph", "leidenalg")}
    if any("+atlas" in v for v in versions.values()):
        pytest.skip("the installed provider is the fixture")
    manifest = manifest_copy()
    manifest["algorithms"]["leiden"]["provider"] = {"kind": "real", "distributions": versions}
    path = tmp_path / "real.json"
    manifest_sha = write_manifest(path, manifest)
    env = {"PYTHONPATH": os.environ["PYTHONPATH"]} if "PYTHONPATH" in os.environ else {}
    first = run_experiment(path, manifest_sha, tmp_path / "a", site=False, **env)
    second = run_experiment(path, manifest_sha, tmp_path / "b", site=False, **env)
    assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
    assert (tmp_path / "a/result.json").read_bytes() == (tmp_path / "b/result.json").read_bytes()
    document = read_partitions(tmp_path / "a")
    kinds = {r["provider_kind"] for g in document["graphs"] for r in g["runs"][1:]}
    assert kinds == {"real"} and read_result(tmp_path / "a")["provider"]["kind"] == "real"
    assert run_verify(tmp_path / "a", path, manifest_sha).returncode == 0
    assert run_script(ROOT / "scripts/verify-community-experiment.py", ["--results", "/x"]
                      ).returncode in (1, 2)  # fmt: skip


def test_a_provider_graph_that_lost_an_edge_is_refused_by_the_adapter(tmp_path):
    done = run_experiment(
        FIX_MANIFEST,
        sha(FIX_MANIFEST.read_bytes()),
        tmp_path / "out",
        ATLAS_S36_FIXTURE_FAULT="drop_edge",
    )
    rejected(done, "measurement_failed: leiden-seed-0")
    assert not (tmp_path / "out").exists() and leftover(tmp_path) == []
