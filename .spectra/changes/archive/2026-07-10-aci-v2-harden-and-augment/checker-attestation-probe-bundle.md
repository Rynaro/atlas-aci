---
artifact: checker-attestation
scope: D3a probe bundle (independent re-derivation, no re-clone)
change_id: aci-v2-harden-and-augment
checker: vigil
audited_commit: b4e1b02
criteria_sha256: 5c3adddbd075a7c12bdd965ee760484a04e5a9a6a4ce05302cbd7bc4147fc7e7  # CONFIRMED at b4e1b02
bundle_sha256: 1ecc73866eb0a0379df5241a4fd2db871ee5afe235c5eab2af937f5944ef7bae  # CONFIRMED
attestation: CONFIRMED (numbers reproduce; verdict PASS under frozen bar)
verifier_ruling: SOUND-FOR-WHAT-IT-CERTIFIES, with one undisclosed staleness gap (MAJOR, future-facing)
---

# Independent re-attestation — committed D3a probe bundle at b4e1b02

I read the committed blobs at `b4e1b02` (not the working tree), computed
modularity with my **own** implementation (and cross-checked with networkx in a
throwaway env), and did **not** call `scripts/verify-probe-verdict.py` (the
artefact under audit). Frozen constants `Q_struct=0.30`, `R=0.85` taken from
`acceptance-criteria.md` (hash confirmed at b4e1b02), never from the sidecar's
`bar` block.

## 1-2. Recomputed vs recorded numbers (full precision)

My modularity: `Q = Σ_c [ L_c/m − (deg_c/2m)² ]`, undirected/unweighted, γ=1,
straight from the bundle's `edges` + `lpa_labels` + `louvain_partitions`. Cross
-checked against `networkx==3.6.1` `nx.community.modularity` on the same graph.

**solidus** (`4026945d…`) — node_count 2676, edge_count 4217, self-loops 0, dup-edges 0:

| quantity | recorded (sidecar) | mine (hand-rolled) | networkx | Δ |
|---|---|---|---|---|
| LPA_Q | 0.6691476098443865 | 0.6691476098443875 | 0.6691476098443865 | ≤1e-15 |
| Louvain median | (→0.7449783) | 0.7449783049502834 | 0.7449783049502817 | ≤2e-15 |
| LPA community count | 228 | 228 | — | 0 |

All ten seeded Louvain Q reproduce to ≤1.8e-15 (e.g. seed 0: recorded
0.7405822183086415, mine 0.7405822183086431; seed 6 recorded 0.7484086426154646,
mine 0.7484086426154664).

**spree** (`6699cde4…`) — node_count 5391, edge_count 8223, self-loops 0, dup-edges 0:

| quantity | recorded (sidecar) | mine (hand-rolled) | networkx | Δ |
|---|---|---|---|---|
| LPA_Q | 0.7165340320731565 | 0.7165340320731598 | 0.7165340320731565 | ≤3.3e-15 |
| Louvain median | (→0.7846478) | 0.7846477659727411 | 0.7846477659727378 | ≤3.5e-15 |
| LPA community count | 371 | 371 | — | 0 |

All ten seeded Louvain Q reproduce to ≤3.8e-15. The differences are float64
summation-order noise; networkx LPA_Q is **bit-identical** to the recorded value
(Δ = 0.0) on both repos.

The relabeling shift the coordinator flagged is confirmed: solidus median
0.7442625 → **0.7449783**, spree 0.7846472 → **0.7846478**; LPA_Q bit-identical
(label-independent). The shift tightens clause (iii) by ≈0.0006 against margins
of 0.036/0.050 — sixty-plus times larger — so the verdict is untouched.

## 3. Three-clause evaluation (frozen Q_struct=0.30, R=0.85; per repo; never averaged)

Using **my recomputed** values (not the sidecar's):

- **solidus:** c1 median 0.744978 ≥ 0.30 ✓; c2 LPA_Q 0.669148 ≥ 0.30 ✓;
  c3 LPA_Q 0.669148 ≥ 0.85·median = 0.633232 ✓ (margin **+0.035916**) → **PASS**
- **spree:** c1 median 0.784648 ≥ 0.30 ✓; c2 LPA_Q 0.716534 ≥ 0.30 ✓;
  c3 LPA_Q 0.716534 ≥ 0.85·median = 0.666951 ✓ (margin **+0.049583**) → **PASS**

Both PASS 3/3, independently. `recorded_verdict = PASS` equals my independent
mechanical evaluation. **Attestation: CONFIRMED.**

## 4. Bundle integrity (all green)

- **sha256:** `sha256(probe-graphs.json.gz)` = `1ecc73866…4ef7bae` = the sidecar's
  `graph_bundle.sha256`. ✓
- **Counts recompute from the graph** (not read from stored summaries): 2676/4217/228
  and 5391/8223/371 all recompute from raw `nodes`/`edges`/`lpa_labels`. ✓
- **Exactly the two pinned SHAs**, by SHA not name: `4026945d…` (solidus),
  `6699cde4…` (spree); no additions/omissions/duplicates. ✓
- **Exactly seeds 0..9** in both the sidecar `louvain_q_by_seed` and the bundle's
  10 `louvain_partitions`. ✓
- **Graph well-formed:** all edge endpoints in `[0,n)`; `lpa_labels` and all ten
  Louvain partitions have length exactly n (complete partitions); node identities
  unique; 6 isolated nodes (solidus) / 5 (spree) handled as singleton communities. ✓
- **Indexer fingerprint honest for the committed indexer:** recomputing the
  fingerprint over `b4e1b02:codegraph.py` (epoch=5, DDL d08bb7e7…, `confident_edges()`
  body) gives `24df2c26…0cabcb9` = the sidecar's recorded `indexer_fingerprint`. ✓

## 5. Graph fidelity (stands on the A3 reproduction) — HOLDS

In the A3 verify I re-cloned both pinned repos, re-indexed, re-exported, and
re-scored, producing node/edge counts 2676/4217 (solidus) and 5391/8223 (spree)
and LPA_Q values bit-identical to what is committed here. LPA_Q is a
label-invariant function of graph structure; a bit-identical LPA_Q across the
from-source graph and the committed bundle, together with identical node and
edge counts and real solidus/spree file paths as node identities, is
overwhelming evidence the committed graph is structurally identical to the true
confident subgraph — only the node→integer labeling changed (which is exactly
what moved the seeded-Louvain medians in the 5th–6th decimal). The reasoning
holds. (This link is human-attested by that reproduction, not machine-checked —
see the ruling below.)

## Ruling — is the verifier sound, or is there an input it has not been shown?

**The verifier is sound for what it certifies:** that the recorded counts, every
modularity Q, and the verdict are the correct functions of the committed graph
bundle under the frozen, hardcoded bar — bar/repo-set/seed-set cross-checked
against hardcoded constants (defects 11/12 closed), every Q recomputed in pure
Python and used for the arithmetic instead of the recorded copies (defect 13/F7
closed), bundle sha256 + fingerprint gated (defect 13 staleness partly closed).
Its docstring **honestly discloses** the graph-vs-source terminus (the bundle's
faithfulness to the pinned SHAs is not machine-checkable in CI and is attested by
my independent reproduction) — that disclosure is correct and not a defect.

**But there is one input the fixed verifier still has not been shown, and it is
not the disclosed terminus — it is the fifteenth defect, in the fix for the
fourteenth (the staleness fingerprint):**

### FINDING (MAJOR, future-facing, [VERIFIED]) — the indexer fingerprint under-covers the graph-determining logic
`scripts/verify-probe-verdict.py` `compute_indexer_fingerprint()` hashes
`SCHEMA_EPOCH` + `EXPECTED_DDL_HASH` + **only `confident_edges()`'s body**. But the
committed graph's **edge set** is jointly determined by `confident_edges()` **and
`_resolve_source_node()`** (which resolves every edge's source endpoint via
name+kind+line-range containment and *drops* edges whose source does not resolve),
with `_enclosing_symbol()` feeding the source attribution. I verified these three
helpers are defined in `codegraph.py` and **absent from the fingerprint input**. A
future A4/A5 change to source-endpoint resolution would alter which
(source→target) edges exist — hence every Q — **without flipping the fingerprint**,
so a bundle that no longer reflects what the current indexer produces would
certify as *fresh* and no workflow would prompt a re-run. This is the campaign's
recurring proxy-vs-invariant pattern one level deeper: the fingerprint captures a
**proxy** (edge *selection*) for the **invariant** (the full graph-*determining*
logic, selection **and** projection). The docstring frames the fingerprint as
covering "the confident-edge selection logic … if the confident-edge selection
logic or schema changes," which reads as if that were the complete set of
graph-determining logic — it is not.

- **Impact on THIS attestation: none.** The committed graph is genuine (A3
  reproduction, bit-identical LPA_Q, fingerprint honest), every number
  re-derived, verdict PASS confirmed. The probe is valid and A3 ships on it.
- **Impact going forward: real.** The coordinator noted "A4/A5 will modify
  codegraph.py"; `_resolve_source_node`/`_enclosing_symbol` are exactly the kind
  of helpers a rationale-node (A4) or export (A5) refactor touches. Under such a
  change the staleness guard is silent.
- **Condition (before P3 / any probe re-run):** extend the fingerprint to hash
  every graph-*determining* method the bundle's edges depend on — at minimum
  `_resolve_source_node` and `_enclosing_symbol` (and, defensively, `_target_kind`
  and the probe-assembly script that projects the graph) — not `confident_edges()`
  alone. Because the current graph is genuine, fixing this will not change the
  verdict; it makes the freshness guarantee cover what actually builds the graph.

## Secondary notes (not blocking)
- The verifier trusts that `lpa_labels` is the *shipped* `communities()` output on
  the committed graph (it never re-runs the deterministic LPA to confirm the
  partition). This folds into the disclosed graph-vs-source terminus and is
  confirmed for this bundle by the A3 reproduction (bit-identical LPA_Q). Worth a
  cheap belt: the verifier *could* re-run the deterministic LPA on the committed
  edges and assert the partition matches, without any re-clone.
- `Q_TOLERANCE = 1e-9` is safe: observed hand-rolled-vs-networkx agreement is
  ~1e-15 and the clause margins are 0.036/0.050 — no fudge within tolerance can
  move a clause.
- The sidecar `bar` block declares `q_struct=0.3, r=0.85` (matching the frozen
  criteria), but I evaluated with the criteria's constants regardless, per the
  task.
