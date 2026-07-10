---
artifact: checker-verdict
phase: A2+A3 (god nodes, LPA communities, D3a probe)
change_id: aci-v2-harden-and-augment
maker: vivi
checker: vigil
branch: feat/v2-p1-edges
head: 21014c92d502605cb62fc1ef51d89a5a77f1972f
criteria_sha256: 5c3adddbd075a7c12bdd965ee760484a04e5a9a6a4ce05302cbd7bc4147fc7e7  # CONFIRMED matches frozen
schema_epoch_at_head: 5
a2_a3_verdict: GO-WITH-CONDITIONS
probe_validity: SOUND
---

# Checker Verdict — A2 (god nodes) + A3 (LPA communities) + the D3a probe

## VERDICTS

- **A2 + A3: GO-WITH-CONDITIONS**
- **Probe validity: SOUND** (independently reproduced bit-for-bit; not rigged)

Criteria hash confirmed. Suite: **148 passed** (matches the coordinator's count,
not the maker's reported 155). The D3a probe is genuine — I re-cloned both
pinned repos, re-indexed, re-exported, and re-scored with ephemeral
networkx==3.6.1, and every load-bearing number reproduced **exactly**. The A3
ship decision is validly justified by the measurement.

The single condition is the **tenth defect** (MAJOR-1): the harden-gate does
**not** perform AC-A3-1's required mechanical evaluation — it greps for a
`verdict: PASS` label instead of recomputing the three-clause rule from the
artefact's own numbers. The probe's numbers happen to be correct, so the ship
decision is right — but the guardrail meant to *guarantee* that is defeatable.
AC-A3-1 is therefore **FAIL**.

## Probe reproduction (the decisive evidence) — SOUND

Re-cloned `solidusio/solidus@4026945d…` (HEAD matched) and
`spree/spree@6699cde4…` (sparse `spree/`, HEAD matched), re-ran both phases:

| quantity | solidus artefact | solidus reproduced | spree artefact | spree reproduced |
|---|---|---|---|---|
| files / symbols / refs / edges | 2519 / 21522 / 117458 / 37364 | **exact** | 2358 / 18683 / 193189 / 110678 | **exact** |
| nodes / undirected edges | 2676 / 4217 | **exact** | 5391 / 8223 | **exact** |
| resolved / AMBIGUOUS excluded | 17346 / 20018 | **exact** | 33024 / 77654 | **exact** |
| LPA community count | 228 | **exact** | 371 | **exact** |
| **LPA_Q** | 0.6691476098443865 | **0.6691476098443865** | 0.7165340320731565 | **0.7165340320731565** |
| **Louvain median** | 0.7442624844311356 | **0.7442624844311356** | 0.7846472261734789 | **0.7846472261734789** |
| networkx | 3.6.1 | 3.6.1 | 3.6.1 | 3.6.1 |

Reconciliation: 17346+20018=37364 and 33024+77654=110678 (both accounted). The
mechanical three-clause evaluation, recomputed independently from the artefact's
own per-seed Q values (median = median of the 10 recorded floats, verified to
1e-12), gives **PASS 3/3 on both** — matching the recorded verdict:

- solidus: median 0.744262 ≥ 0.30 ✓; LPA_Q 0.669148 ≥ 0.30 ✓; 0.669148 ≥ 0.85·median (0.632623) ✓ (margin +0.036524)
- spree:   median 0.784647 ≥ 0.30 ✓; LPA_Q 0.716534 ≥ 0.30 ✓; 0.716534 ≥ 0.85·median (0.666950) ✓ (margin +0.049584)

Why SOUND, point by point against the attack list:
- **Identical graph to both algorithms.** `communities()` and
  `probe-export-confident-graph.py` build the undirected/dedup/self-loop-dropped
  graph from `confident_edges()` with the *same* `_resolve_source_node`; the
  construction is line-for-line identical, the export script asserts the node
  set equals `communities()`'s own node set, and LPA_Q reproducing to the last
  digit *proves* the edge set is identical (a different graph would move Q).
- **Genuinely the confident subgraph.** Input is `confident_edges()`
  (EXTRACTED∪INFERRED); AMBIGUOUS absent (20018/77654 excluded, counted).
- **Same ruler.** `probe-modularity.py:62,67` scores LPA and every Louvain run
  with the same `nx.community.modularity(g, …)` on one graph object.
- **Cross-process determinism.** `communities()` produced an identical partition
  under PYTHONHASHSEED ∈ {0,1,42,12345}; the LPA reductions (counts→max→min→sorted)
  are order-invariant by construction; and the 228/371 partitions reproduced on
  a fresh clone.
- **Not tuned to the repos.** No `solidus`/`spree` reference in the source;
  `_LPA_MAX_ITERATIONS = 100` is a generic runtime bound.
- **networkx isolation intact.** `grep -ric networkx mcp-server/pyproject.toml
  mcp-server/uv.lock` → `0 0` after the ephemeral runs (AC-NEG-2).

## Per-criterion table

| ID | Verdict | Basis |
|----|---------|-------|
| AC-A2-1 | PASS [VERIFIED] | Degree centrality, deterministic total-order sort `(-degree, path, line, name)`. `test_god_nodes_degree_centrality_deterministic`. |
| AC-A2-2 | PASS [VERIFIED] | Pure-Python dict arithmetic over `confident_edges()`; no networkx/igraph/graspologic import. `test_god_nodes_uses_only_edge_counts`. |
| AC-A2-3 | PASS [VERIFIED] | `analysis_basis`/`ambiguous_edges_excluded`/`resolved_edge_count` on BOTH god_nodes and communities; test computes independent counts (resolved 3 ≠ ambiguous 1) and asserts the response equals them; reconciles to total (17346+20018=37364 on solidus). |
| AC-A3-1 | **FAIL [VERIFIED]** | Half met: harden-gate fails when an A3 path changes without the artefact (lines 181-184). Half UNMET: the required "assert the recorded verdict equals the **mechanical evaluation** of the recorded numbers (resolving F7)" is not implemented — the gate only greps `verdict.*:.*pass` (lines 186-190). A rigged artefact with failing numbers under a retained PASS label passes the grep (demonstrated). See MAJOR-1. |
| AC-A3-2 | PASS [VERIFIED] | `test_lpa_deterministic_total_order` (same-process, full membership diff); I verified cross-process (4 PYTHONHASHSEEDs) + exact 228/371 reproduction on fresh clones. Total-ordered visitation, initial labels, tie-break to smallest, community renumber by smallest member. |
| AC-A3-3 | PASS [VERIFIED] | Hand-rolled LPA, no new runtime dependency. `test_no_new_dependency`; AC-NEG-2 intact. |
| AC-A3-4 | PASS [VERIFIED] | Three-clause rule per repo, never averaged; both PASS 3/3, reproduced bit-for-bit; CUT branch present; networkx never added. |
| AC-A3-5 | PASS [VERIFIED] | harden-gate fires when A3 code exists (test_communities.py present) and the artefact is absent or lacks a PASS label; cut-branch semantics exist. NOTE: inherits the same label-grep weakness as AC-A3-1 (folded into MAJOR-1) — it does not recompute the three-clause rule, it trusts the "verdict: PASS" string. |
| AC-NEG-7 | PASS [VERIFIED] | `confident_edges()` excludes AMBIGUOUS; `communities()` additionally skips `target is None` edges (double barrier); `god_nodes()` in-degree guards on target. Both correct in production. `test_ambiguous_edges_never_join_a_community`, `test_ambiguous_edges_never_contribute_to_god_node_degree`, `test_*_input_is_exactly_confident_edges`. See MINOR-1 (god_nodes out-degree is single-barrier). |

## Ranked findings

### MAJOR-1 [VERIFIED] — the tenth defect: AC-A3-1's mechanical-evaluation guardrail is a label grep
`.github/workflows/harden-gate.yml:186-190`. AC-A3-1 requires: "The check SHALL
also assert the recorded verdict equals the mechanical evaluation of the
recorded numbers (a maker cannot record failing numbers under a PASS label,
resolving F7)." The implemented check is:
```
if [ ! -f "$PROBE_ARTIFACT" ] || ! grep -qiE "verdict.*:.*pass" "$PROBE_ARTIFACT"; then FAIL
```
It never parses the per-seed Q values / LPA_Q / node counts and never recomputes
`c1 ∧ c2 ∧ c3`. No pytest test, no other CI step does it either (grepped
`.github/`, `scripts/`, `tests/`). **Demonstrated:** an artefact edited to
`LPA_Q = 0.11111 (below floor)` while retaining `verdict: PASS` still matches the
grep — the gate cannot tell it from an honest one. This is exactly the "measures
what the artefact *says* (a PASS string) rather than what the invariant *demands*
(the arithmetic closes to PASS)" defect class this campaign keeps producing, and
the exact F7 failure AC-A3-1 was written to close.
**Scope:** the *actual* artefact's numbers are correct (reproduced bit-for-bit),
so A3's ship decision is validly justified and the feature works — this is a
defeatable *guardrail*, not a wrong measurement. → **Condition (must-fix before
release):** make the gate (or a pytest fixture) parse the artefact's recorded
numbers and recompute the three-clause verdict, failing on any label/arithmetic
mismatch. Because the numbers already yield PASS, fixing this will not change the
outcome — it will make the guarantee real.

### MINOR-1 [VERIFIED] — god_nodes' AMBIGUOUS-exclusion is single-barrier, contra its docstring
`codegraph.py:1362-1421`. The docstring claims AMBIGUOUS "cannot leak into degree
BY CONSTRUCTION, not because a filter happened to be applied correctly." True for
in-degree (guarded by `if target is not None`, line 1413) and for `communities()`
(skips `target is None`, line 1561). But the **out-degree** half (lines 1418-1420)
resolves and counts the source of *every* edge with no target guard. I injected an
AMBIGUOUS-shaped edge (`target=None`, resolvable source) directly into
`confident_edges()` output (bypassing the SQL filter) and watched the source
symbol's `out_degree` increment. In production this never happens —
`confident_edges()` excludes AMBIGUOUS and that filter is the guarantee, which is
tested — so AC-NEG-7 holds. But god_nodes rests on **one** barrier (the filter),
not the "by construction" double barrier the docstring claims and that
`communities()` actually has. Robustness/claim-accuracy note; not a criterion
failure. Suggest a `target is not None` guard on the out-degree branch to match
the docstring, or soften the docstring.

### MINOR-2 [VERIFIED] — the pre-registered R sits inside the baseline's own noise
Confirming the coordinator's own recorded finding. At the frozen R=0.85 both
repos clear clause (iii) comfortably (solidus ratio 0.8991, spree 0.9132). But a
counterfactual R=0.90 would cut solidus on a Q shortfall of **0.000688** —
smaller than Louvain's own seed-to-seed sd (0.002176, reported as population sd
via `statistics.pstdev`). FORGE's prediction that R would be "weakly load-bearing
because LPA's failure is bimodal" was **wrong**: the failure landed mid-band, not
bimodal. **Do NOT move R now** — revising a pre-registered bar after seeing the
data is the circularity pre-registration exists to prevent. Recorded for a future
release: require the clause-(iii) margin to exceed the baseline's own noise, or
the bar is finer than the instrument. (Also: the artefact reports `louvain_sd`
as population sd, not sample sd — descriptive only, not part of the pass rule.)

### MINOR-3 [VERIFIED] — the graph-identity assertion is a node-set proxy
`probe-export-confident-graph.py:81-88` asserts the *node* set matches
`communities()`'s node set, not the *edge* set. The edge sets are in fact
identical (line-for-line construction; and LPA_Q reproducing to the last digit
proves it — a different edge set moves Q). Belt-and-suspenders note: the stronger
edge-set-identity property holds but is only checked via a node-set proxy plus
construction discipline. Not a defect.

## Notes for the record
- All A1 conditions confirmed addressed in commits 3e92167→b5c2d64 (the AC-H-18
  named anchor now exists and enumerates god_nodes + communities at cap-1/cap/cap+1;
  shadowed qualifiers demoted to INFERRED; relation vocabulary and edge ordering closed).
- SCHEMA_EPOCH is now 5 (was 3 at A1); the condition/A-track DDL changes bumped it;
  `test_expected_ddl_hash_matches_current_ddl` is green in the 148.
- AC-H-18 for the new verbs: `test_truncation_signal_iff_content_withheld`
  parametrizes god_nodes and communities at the boundary with independently-computed
  expectations — PASS.
