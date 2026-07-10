#!/usr/bin/env python3
"""D3a probe-verdict verifier — the AC-A3-1/F7 fix (checker defects 11-13, 15).

THE FIRST FIX (defect 10): `.github/workflows/harden-gate.yml` used to
gate A3 on `grep -qiE "verdict.*:.*pass" "$PROBE_ARTIFACT"` — a check of
the PROSE LABEL, never a single Q value.

THE SECOND FIX (defect 11): the recomputation that replaced it still read
its BAR (`q_struct`/`r`) FROM the sidecar under audit. Fixed by hardcoding
the frozen constants (`FROZEN_*` below) and cross-checking the sidecar's
own declared bar against them — never substituting the sidecar's copy
into the arithmetic.

THE THIRD FIX (defect 12): nothing asserted WHICH repos, or how many,
were being graded, nor which seeds. Fixed by hardcoding the two pinned
SHAs and the ten frozen seeds, and asserting the sidecar's repo/seed sets
match EXACTLY.

THE FOURTH DEFECT (checker, this pass) — and the one this file closes:
every `Q` in the sidecar (`lpa_q`, each seed's Louvain `Q`) was taken ON
FAITH. A maker could under-report the Louvain baseline (e.g. all ten
seeds at `Q=0.35` instead of the real ~0.74) and ship a clusterer scoring
`Q=0.31` — nowhere near the real `Q=0.669` — because the bar
(`0.85 * median`) is only as honest as the numbers used to compute it.
Recomputing the bar from a lie about the baseline is still a lie.

THE FIX: `node_count`/`edge_count` were themselves summaries a verifier
cannot recompute FROM. The actual PRIMITIVES — the confident subgraph's
node list + edge list (canonical total order), the shipped LPA's
partition, and all ten Louvain partitions by seed — are committed
separately as a compressed bundle (`probe-graphs.json.gz`, built by
`scripts/probe-assemble-graph-bundle.py`), whose sha256 is recorded in
the sidecar. This script:

  1. Verifies the bundle file's sha256 matches the sidecar's recorded
     value (a tampered or substituted bundle is loud, not silent).
  2. Recomputes `node_count`/`edge_count`/`lpa_community_count` from the
     raw graph and partition arrays, and rejects any mismatch against the
     sidecar's recorded copies — no stored summary is ever trusted.
  3. Recomputes EVERY modularity `Q` (the shipped LPA's, and all ten
     Louvain runs') in pure Python, straight from the graph + partition
     arrays — `Q = sum_c [ L_c/m - (deg_c/2m)^2 ]`, the standard
     undirected/unweighted formula at resolution (gamma) = 1, which is
     algebraically identical to what `nx.community.modularity` computes.
     This validated against the actual recorded probe data to within
     ~1e-15 (float64 noise) before this script was finalized.
  4. Asserts each recomputed `Q` equals the sidecar's recorded value
     within float tolerance — this does double duty: it both derives the
     numbers this script actually grades with, AND independently
     confirms the recorded networkx run was honest (if the two ever
     disagree, one of them is wrong, and this is loud about which check
     caught it).
  5. Uses the RECOMPUTED values (never the sidecar's) for the pass/fail
     arithmetic from here on — median, and all three frozen-bar clauses,
     are computed from graph-derived numbers only.

THE FIFTH DEFECT — the staleness hole: nothing tied the sidecar to the
indexer that produced it. A4/A5 will modify `codegraph.py`; if the
confident-edge selection/projection logic or schema changes, a stale
sidecar could certify a version of the indexer that no longer exists.
Fixed: an `indexer_fingerprint` recorded in the sidecar at probe time and
recomputed here from the CURRENT tree — a mismatch means the probe is
stale and must be re-run, never silently accepted.

THE SIXTH DEFECT (checker, found IN the fifth defect's own fix, same
pass one level deeper): the first `indexer_fingerprint` implementation
hashed `SCHEMA_EPOCH` + `EXPECTED_DDL_HASH` + ONLY `confident_edges()`'s
body — but the committed graph's edge set is ALSO determined by
`_resolve_source_node()` (can drop an edge whose source doesn't resolve)
and `_enclosing_symbol()` (feeds `_resolve_source_node`'s underlying data
via `_resolve_edges()` — a data-flow dependency, not a direct call, so a
naive call-graph closure would miss it) and `_target_kind()`. See
`compute_indexer_fingerprint`'s own docstring for the fix: rather than
hand-list every graph-determining function ("a hand-maintained list of
function names is the next bug" — the same lesson `_CALL_CANDIDATE_KINDS`
taught), it now hashes `codegraph.py` IN FULL, verbatim, plus the two
probe scripts that decide what gets exported/assembled — closing the
CLASS of "which function did I forget," not just this one instance.

THE DOCUMENTED TERMINUS: after all of the above, the one input this
script still cannot independently verify is that the committed graph
bundle is a FAITHFUL export of the two pinned repo SHAs — that requires
actually cloning and indexing two Rails applications, which CI cannot do
on every PR. That link is attested by INDEPENDENT REPRODUCTION (the
checker re-cloned both repos, re-indexed, re-exported, and re-scored,
reproducing every float to the last digit), not by anything this script
mechanically checks. See `probe-lpa-vs-louvain.md` and
`harden-gate.yml`'s header comment for this bound stated explicitly.

Usage:
  python3 scripts/verify-probe-verdict.py <sidecar_json_path>

Exit 0 iff: the sidecar's declared bar/seeds/repo-set agree with the
frozen constants; the graph bundle's sha256 matches; every recomputed
node/edge/community count and modularity Q matches the sidecar's
recorded copies within tolerance; the indexer fingerprint matches the
current tree; and the recorded verdict equals the mechanical evaluation
of the RECOMPUTED numbers against the frozen bar. Exit 1 on any
provenance/integrity/arithmetic mismatch, with a diagnostic naming
exactly what disagreed. Exit 2 on usage / malformed-input errors.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# FROZEN, EXTERNAL FACTS — D3a pre-registration (FORGE), frozen criteria
# sha256:5c3adddbd075a7c12bdd965ee760484a04e5a9a6a4ce05302cbd7bc4147fc7e7.
#
# Checker defect 11: these must NEVER be read from, or derived from, the
# sidecar under audit — a verifier that takes its bar from the file it is
# grading can be handed a softened bar next to failing numbers and wave
# them through. Do not parameterize any of the five values below from
# JSON input; the sidecar's own copies are cross-checked against these,
# never substituted for them.
# ---------------------------------------------------------------------------
FROZEN_Q_STRUCT: float = 0.30
FROZEN_R: float = 0.85
FROZEN_K: int = 10
FROZEN_SEEDS: tuple[int, ...] = tuple(range(10))  # 0..9, frozen
FROZEN_RESOLUTION: float = 1.0

# Checker defect 12: the pinned repo SET is an external fact too — a
# verifier that never checks WHICH repos, or how many, is trivially
# defeated by dropping the one with the tighter margin. Exactly these two,
# identified by their pinned SHA (the SHA is the pin; a `name` field is
# just a label and proves nothing on its own).
FROZEN_PINNED_SHAS: frozenset[str] = frozenset(
    {
        "4026945d614e81383c007ed1ab1278a0195ce5d9",  # solidusio/solidus
        "6699cde44303ea85ef6e56c5e87c44a738ab73fc",  # spree/spree
    }
)

# Checker defect 13: floating-point tolerance for comparing a recomputed
# modularity Q (pure Python, see `_modularity` below) to the sidecar's
# recorded (networkx-computed) value. Validated against real probe data:
# the two formulations agree to ~1e-15 (float64 noise) on every one of 22
# recorded Q values across both pinned repos — 1e-9 is generous headroom
# above that noise floor while still catching any real divergence.
Q_TOLERANCE: float = 1e-9

# Checker defect 13 (staleness hole) / defect 15 (under-coverage, found in
# the fix for defect 13): the indexer fingerprint is computed straight
# from these three files' own SOURCE TEXT — never by importing
# `atlas_aci` (this script must run without `mcp-server`'s environment,
# and without networkx, AC-NEG-2). See `compute_indexer_fingerprint` for
# why all three (not just `codegraph.py`) are in scope.
CODEGRAPH_RELATIVE_PATH = "mcp-server/src/atlas_aci/codegraph.py"
PROBE_EXPORT_SCRIPT_RELATIVE_PATH = "scripts/probe-export-confident-graph.py"
PROBE_ASSEMBLE_SCRIPT_RELATIVE_PATH = "scripts/probe-assemble-graph-bundle.py"


class ProvenanceError(Exception):
    """The sidecar's declared facts (bar constants, seed set, repo
    identity/count, graph-bundle integrity, recomputed counts/Q values, or
    indexer fingerprint) disagree with either the frozen constants above
    or the graph bundle's own recomputed content. Raised for ANY such
    mismatch — never silently reconciled by preferring either side's
    value; the mismatch itself is the finding."""


# ---------------------------------------------------------------------------
# Provenance gates (declared facts vs. frozen/external constants)
# ---------------------------------------------------------------------------


def _check_declared_bar_matches_frozen(bar: dict[str, Any]) -> None:
    mismatches: list[str] = []
    if bar.get("q_struct") != FROZEN_Q_STRUCT:
        mismatches.append(
            f"q_struct: sidecar declares {bar.get('q_struct')!r}, frozen is {FROZEN_Q_STRUCT!r}"
        )
    if bar.get("r") != FROZEN_R:
        mismatches.append(f"r: sidecar declares {bar.get('r')!r}, frozen is {FROZEN_R!r}")
    if bar.get("k") != FROZEN_K:
        mismatches.append(f"k: sidecar declares {bar.get('k')!r}, frozen is {FROZEN_K!r}")
    if bar.get("resolution") != FROZEN_RESOLUTION:
        declared_res = bar.get("resolution")
        mismatches.append(f"resolution: declares {declared_res!r}, frozen is {FROZEN_RESOLUTION!r}")
    declared_seeds = tuple(sorted(bar.get("seeds", [])))
    if declared_seeds != FROZEN_SEEDS:
        mismatches.append(
            f"bar.seeds: sidecar declares {declared_seeds!r}, frozen is {FROZEN_SEEDS!r}"
        )
    if mismatches:
        raise ProvenanceError(
            "sidecar's declared bar disagrees with the frozen, external D3a "
            "constants -- a mismatch is itself a finding, never silently "
            "reconciled:\n  " + "\n  ".join(mismatches)
        )


def _check_repo_set_matches_frozen(repos: list[dict[str, Any]]) -> None:
    declared_shas = [repo.get("pinned_sha") for repo in repos]
    if len(declared_shas) != len(FROZEN_PINNED_SHAS) or set(declared_shas) != FROZEN_PINNED_SHAS:
        raise ProvenanceError(
            f"sidecar names {len(declared_shas)} repo(s) with pinned_sha "
            f"{sorted(s for s in declared_shas if s)!r}; AC-A3-4 requires PASS on "
            f"EXACTLY the two frozen pinned repos {sorted(FROZEN_PINNED_SHAS)!r} "
            "-- no additions, omissions, or duplicates."
        )


def _check_repo_seed_set_matches_frozen(
    repo_name: str, louvain_q_by_seed: dict[str, float]
) -> None:
    declared_seeds = tuple(sorted(int(s) for s in louvain_q_by_seed))
    if declared_seeds != FROZEN_SEEDS or len(louvain_q_by_seed) != FROZEN_K:
        raise ProvenanceError(
            f"{repo_name}: louvain_q_by_seed carries seeds {declared_seeds!r}; "
            f"the frozen bar requires EXACTLY the {FROZEN_K} seeds {FROZEN_SEEDS!r}."
        )


# ---------------------------------------------------------------------------
# Graph bundle: integrity, recomputed counts, recomputed modularity
# ---------------------------------------------------------------------------


def _load_graph_bundle(sidecar_path: Path, sidecar: dict[str, Any]) -> dict[str, Any]:
    graph_bundle_meta = sidecar.get("graph_bundle", {})
    filename = graph_bundle_meta.get("filename")
    recorded_sha256 = graph_bundle_meta.get("sha256")
    if not filename or not recorded_sha256:
        raise ProvenanceError(
            "sidecar is missing graph_bundle.filename/sha256 -- nothing to verify against"
        )

    bundle_path = sidecar_path.parent / filename
    if not bundle_path.is_file():
        raise ProvenanceError(
            f"graph bundle {bundle_path} referenced by the sidecar does not exist"
        )

    compressed = bundle_path.read_bytes()
    actual_sha256 = hashlib.sha256(compressed).hexdigest()
    if actual_sha256 != recorded_sha256:
        raise ProvenanceError(
            f"graph bundle {bundle_path} has been tampered with (or does not match the "
            f"probe that produced this sidecar): sha256 is {actual_sha256!r}, sidecar "
            f"records {recorded_sha256!r}"
        )

    return json.loads(gzip.decompress(compressed).decode("utf-8"))


def _check_bundle_repo_set_matches_sidecar(
    bundle: dict[str, Any], repos: list[dict[str, Any]]
) -> None:
    declared_names = {repo["name"] for repo in repos}
    bundle_names = set(bundle.keys())
    if declared_names != bundle_names:
        raise ProvenanceError(
            f"graph bundle names repos {sorted(bundle_names)!r}, sidecar's `repos` names "
            f"{sorted(declared_names)!r} -- these must be exactly the same set."
        )


def _modularity(n: int, edges: list[tuple[int, int]], labels: list[int]) -> float:
    """Standard undirected, unweighted modularity at resolution (gamma) =
    1: `Q = sum_c [ L_c/m - (deg_c/2m)^2 ]`, where `m` is the edge count,
    `L_c` is community `c`'s internal edge count, `deg_c` is the sum of
    its members' degrees. Algebraically identical to what
    `nx.community.modularity` computes for an unweighted graph with no
    self-loops (our graph construction drops self-loops) -- validated
    against real recorded probe data to ~1e-15 before this was finalized.
    Zero dependencies beyond the standard library.
    """
    m = len(edges)
    if m == 0:
        raise ProvenanceError("cannot compute modularity: the graph has zero edges")
    degree = [0] * n
    for u, v in edges:
        degree[u] += 1
        degree[v] += 1
    community_degree: dict[int, int] = {}
    for node in range(n):
        label = labels[node]
        community_degree[label] = community_degree.get(label, 0) + degree[node]
    internal_edges: dict[int, int] = {}
    for u, v in edges:
        if labels[u] == labels[v]:
            internal_edges[labels[u]] = internal_edges.get(labels[u], 0) + 1
    q = 0.0
    for community, deg_c in community_degree.items():
        l_c = internal_edges.get(community, 0)
        q += (l_c / m) - (deg_c / (2 * m)) ** 2
    return q


def evaluate_repo(repo: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    _check_repo_seed_set_matches_frozen(repo["name"], repo["louvain_q_by_seed"])

    graph = bundle.get(repo["name"])
    if graph is None:
        raise ProvenanceError(f"{repo['name']}: not present in the graph bundle")

    n = len(graph["nodes"])
    edges = [tuple(e) for e in graph["edges"]]
    lpa_labels = graph["lpa_labels"]
    louvain_partitions = graph["louvain_partitions"]

    # Recompute node/edge/community counts from the raw graph -- no
    # stored summary is ever trusted (checker defect 13).
    recomputed_node_count = n
    recomputed_edge_count = len(edges)
    recomputed_lpa_community_count = len(set(lpa_labels))

    count_mismatches: list[str] = []
    if recomputed_node_count != repo.get("node_count"):
        count_mismatches.append(
            f"node_count: recomputed {recomputed_node_count}, recorded {repo.get('node_count')}"
        )
    if recomputed_edge_count != repo.get("edge_count"):
        count_mismatches.append(
            f"edge_count: recomputed {recomputed_edge_count}, recorded {repo.get('edge_count')}"
        )
    if recomputed_lpa_community_count != repo.get("lpa_community_count"):
        count_mismatches.append(
            f"lpa_community_count: recomputed {recomputed_lpa_community_count}, "
            f"recorded {repo.get('lpa_community_count')}"
        )
    if count_mismatches:
        raise ProvenanceError(f"{repo['name']}: " + "; ".join(count_mismatches))

    # Recompute EVERY modularity Q in pure Python, straight from the
    # graph + partitions -- trust nothing but the graph.
    recomputed_lpa_q = _modularity(n, edges, lpa_labels)
    recorded_lpa_q = repo["lpa_q"]
    if abs(recomputed_lpa_q - recorded_lpa_q) > Q_TOLERANCE:
        raise ProvenanceError(
            f"{repo['name']}: recomputed LPA_Q={recomputed_lpa_q!r} disagrees with the "
            f"sidecar's recorded lpa_q={recorded_lpa_q!r} by more than {Q_TOLERANCE} -- "
            "either the graph bundle or the recorded value has been tampered with, or "
            "the recorded networkx run was not honest."
        )

    recomputed_seed_q: dict[int, float] = {}
    for seed in FROZEN_SEEDS:
        partition = louvain_partitions[seed]
        q = _modularity(n, edges, partition)
        recomputed_seed_q[seed] = q
        recorded_q = repo["louvain_q_by_seed"][str(seed)]
        if abs(q - recorded_q) > Q_TOLERANCE:
            raise ProvenanceError(
                f"{repo['name']} seed {seed}: recomputed Q={q!r} disagrees with the "
                f"sidecar's recorded Q={recorded_q!r} by more than {Q_TOLERANCE} -- either "
                "the graph bundle or the recorded value has been tampered with, or the "
                "recorded networkx run was not honest."
            )

    # From here on, arithmetic uses ONLY the recomputed values -- the
    # recorded ones already served their purpose (an honesty check on the
    # networkx run), never the computation's actual input.
    qs = [recomputed_seed_q[seed] for seed in sorted(recomputed_seed_q)]
    median = statistics.median(qs)
    threshold3 = FROZEN_R * median
    clause1 = median >= FROZEN_Q_STRUCT
    clause2 = recomputed_lpa_q >= FROZEN_Q_STRUCT
    clause3 = recomputed_lpa_q >= threshold3
    return {
        "name": repo["name"],
        "median": median,
        "lpa_q": recomputed_lpa_q,
        "threshold3": threshold3,
        "clause1_median_ge_q_struct": clause1,
        "clause2_lpa_ge_q_struct": clause2,
        "clause3_lpa_ge_r_times_median": clause3,
        "repo_pass": clause1 and clause2 and clause3,
    }


# ---------------------------------------------------------------------------
# Indexer fingerprint: is the sidecar stale relative to the CURRENT tree?
# ---------------------------------------------------------------------------


def compute_indexer_fingerprint(repo_root: Path) -> str:
    """Hashes everything that determines the committed graph bundle.

    Checker defect 15 (found in the fix for defect 13's staleness hole,
    the same pass deeper): an earlier revision hashed `SCHEMA_EPOCH` +
    `EXPECTED_DDL_HASH` + ONLY `confident_edges()`'s own body. But the
    committed graph's edge set is jointly determined by `confident_edges()`
    AND `_resolve_source_node()` (which resolves -- and can DROP -- each
    edge's source endpoint) AND `_enclosing_symbol()` (which feeds
    `_resolve_source_node`'s underlying `source_name`/`source_kind` data by
    way of `_resolve_edges()`, not a direct call — a data-flow dependency a
    naive self-call closure would miss) AND `_target_kind()`. A change to
    any of these alters every `Q` in the bundle without necessarily
    touching `confident_edges()` itself — a stale bundle would certify as
    fresh, and no workflow would ask for a re-run.

    THE FIX closes the CLASS, not the instance (the same lesson
    `_CALL_CANDIDATE_KINDS`/`PRODUCED_KINDS` already taught this codebase):
    rather than hand-list every graph-determining function — "a
    hand-maintained list of function names is the next bug" — this hashes
    `codegraph.py` IN FULL, verbatim, plus the two probe scripts that
    decide what the export pipeline reads and writes
    (`probe-export-confident-graph.py`, `probe-assemble-graph-bundle.py`).
    Hashing the whole file cannot omit a graph-determining function *by
    construction* — there is no list to fall out of date. The tradeoff is
    conservatism, not blindness: an edit ANYWHERE in `codegraph.py` (even
    an unrelated docstring) flips the fingerprint and forces a probe
    re-run, which is the safe failure direction (an unnecessary "please
    re-run" is cheap; a silent false "fresh" on a changed indexer is the
    defect this exists to prevent). `scripts/test-verify-probe-verdict.sh`
    proves this by editing `_resolve_source_node`, `_enclosing_symbol`,
    `_target_kind`, and `confident_edges` in turn (one at a time, in a
    throwaway copy) and confirming EACH edit alone flips the fingerprint —
    the same "prove it has teeth" discipline as every other guard here.

    Dependency-free (no `atlas_aci` import, no `mcp-server` environment,
    no networkx) -- reads three files as plain text, nothing else.
    """
    codegraph_path = repo_root / CODEGRAPH_RELATIVE_PATH
    export_script_path = repo_root / PROBE_EXPORT_SCRIPT_RELATIVE_PATH
    assemble_script_path = repo_root / PROBE_ASSEMBLE_SCRIPT_RELATIVE_PATH
    for path in (codegraph_path, export_script_path, assemble_script_path):
        if not path.is_file():
            raise ProvenanceError(f"cannot compute the indexer fingerprint: {path} does not exist")

    codegraph_source = codegraph_path.read_text()
    export_script_source = export_script_path.read_text()
    assemble_script_source = assemble_script_path.read_text()

    epoch_match = re.search(r"^SCHEMA_EPOCH\s*=\s*(\d+)", codegraph_source, re.MULTILINE)
    if not epoch_match:
        raise ProvenanceError(f"could not find SCHEMA_EPOCH in {codegraph_path}")

    ddl_match = re.search(r'^EXPECTED_DDL_HASH\s*=\s*"([0-9a-f]+)"', codegraph_source, re.MULTILINE)
    if not ddl_match:
        raise ProvenanceError(f"could not find EXPECTED_DDL_HASH in {codegraph_path}")

    fingerprint_input = (
        f"epoch={epoch_match.group(1)}|ddl_hash={ddl_match.group(1)}|"
        f"codegraph_source={codegraph_source}|"
        f"export_script={export_script_source}|"
        f"assemble_script={assemble_script_source}"
    )
    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()


def _check_indexer_fingerprint_matches_tree(sidecar: dict[str, Any], repo_root: Path) -> None:
    recorded = sidecar.get("indexer_fingerprint")
    if not recorded:
        raise ProvenanceError(
            "sidecar is missing indexer_fingerprint -- cannot confirm it is not stale"
        )
    current = compute_indexer_fingerprint(repo_root)
    if current != recorded:
        raise ProvenanceError(
            f"indexer_fingerprint mismatch: sidecar records {recorded!r}, the CURRENT tree "
            f"computes {current!r}. The probe is STALE relative to the current indexer "
            "(codegraph.py and/or the probe export/assembly scripts changed since the probe "
            "ran) and must be re-run -- a stale probe must never certify a changed indexer."
        )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <sidecar_json_path>", file=sys.stderr)
        return 2

    sidecar_path = Path(sys.argv[1]).resolve()
    try:
        with open(sidecar_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: could not read/parse sidecar JSON: {e}", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parent.parent

    try:
        _check_declared_bar_matches_frozen(data.get("bar", {}))
        _check_repo_set_matches_frozen(data.get("repos", []))
        _check_indexer_fingerprint_matches_tree(data, repo_root)
        bundle = _load_graph_bundle(sidecar_path, data)
        _check_bundle_repo_set_matches_sidecar(bundle, data.get("repos", []))
    except ProvenanceError as e:
        print(f"FAIL (AC-A3-1/F7, provenance): {e}", file=sys.stderr)
        return 1

    recorded_verdict = str(data["recorded_verdict"]).strip().upper()

    try:
        evaluations = [evaluate_repo(repo, bundle) for repo in data["repos"]]
    except ProvenanceError as e:
        print(f"FAIL (AC-A3-1/F7, provenance): {e}", file=sys.stderr)
        return 1

    # Deliberately NOT named `e` — an earlier revision reused that name for
    # both `except ... as e` (three times, above) and this per-repo result
    # loop variable. Python's runtime scoping made it harmless (a `for`
    # target rebinds cleanly; generator-expression targets are scoped to
    # the genexpr itself), but mypy's control-flow analysis read the
    # `except` blocks' implicit `del e` as still in effect and flagged
    # every use below as "reading a deleted variable" — a false positive,
    # but the ambiguity itself (one name, two unrelated meanings, in one
    # function) was a real readability defect worth fixing on its own.
    computed_verdict = "PASS" if all(ev["repo_pass"] for ev in evaluations) else "CUT"

    print(f"frozen bar (hardcoded): Q_struct={FROZEN_Q_STRUCT} R={FROZEN_R}")
    print("every count and Q below is RECOMPUTED from the graph bundle, not read from the sidecar")
    for ev in evaluations:
        print(
            f"  {ev['name']}: median={ev['median']!r} lpa_q={ev['lpa_q']!r} "
            f"r*median={ev['threshold3']!r} | "
            f"clause1(median>=Q_struct)={ev['clause1_median_ge_q_struct']} "
            f"clause2(lpa_q>=Q_struct)={ev['clause2_lpa_ge_q_struct']} "
            f"clause3(lpa_q>=R*median)={ev['clause3_lpa_ge_r_times_median']} "
            f"-> repo_pass={ev['repo_pass']}"
        )
    print(f"computed_verdict={computed_verdict} recorded_verdict={recorded_verdict}")

    if computed_verdict != recorded_verdict:
        print(
            f"FAIL (AC-A3-1/F7): recorded verdict '{recorded_verdict}' does not equal "
            f"the mechanical evaluation of the RECOMPUTED numbers against the FROZEN "
            f"bar ('{computed_verdict}'). A label is not evidence, and neither is a "
            "stored Q value; every number is derived from the committed graph.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK (AC-A3-1/F7): recorded verdict '{recorded_verdict}' matches the "
        "recomputed verdict under the frozen, hardcoded bar; every recomputed count "
        "and Q agrees with the sidecar's recorded copies; the indexer fingerprint "
        "matches the current tree."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
