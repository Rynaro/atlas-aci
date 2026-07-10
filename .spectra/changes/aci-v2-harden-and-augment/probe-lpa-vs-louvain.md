---
artifact: d3a-probe-verdict
phase: A3
change_id: aci-v2-harden-and-augment
maker: vivi
branch: feat/v2-p1-edges
bar_frozen_before_code: true
verdict: PASS
---

# D3a probe — shipped LPA vs. networkx Louvain, on two pinned reference repos

## Anti-circularity discipline

This bar was frozen (constants below) **before a line of `communities()`
was written**: `Q_struct = 0.30`, `R = 0.85`, `K = 10` Louvain runs,
`seeds = 0..9`, `resolution (gamma) = 1.0`, baseline statistic = **median**
Q across the 10 Louvain runs. The pass rule, per repo, independently,
**never averaged across repos**:

```
Louvain_Q_median >= 0.30   AND
LPA_Q            >= 0.30   AND
LPA_Q            >= 0.85 * Louvain_Q_median
```

Either repo failing **any** clause cuts A3 to v2.1 (delete the LPA
implementation and the `communities:` verb, keep this artefact as the
record, ship A2 god-nodes alone) — never a fallback to adopting networkx
(AC-NEG-2 is absolute, DIR-1).

## What is actually committed, and why (the recompute-everything closure)

Three rounds of checker attack progressively closed every gap in how
this bar is graded. Each round is preserved here rather than silently
overwritten, because the gate's honesty depends on knowing exactly what
it does and does not check:

1. **The verdict must be recomputed, not read as a label.** The gate
   originally accepted any artefact whose prose contained the string
   `"verdict: PASS"` — a forged copy with a failing `LPA_Q` and an
   untouched label sailed straight through (checker MAJOR-1/defect 10).
2. **The bar must be hardcoded in the verifier, never read from the
   artefact.** A verifier that recomputes the three clauses using a
   `q_struct`/`r` value taken FROM the sidecar can be handed a softened
   bar next to failing numbers (checker defect 11).
3. **The repo set and seed set must be asserted exactly.** Nothing
   checked WHICH repos, or how many, or which seeds, were being graded —
   dropping the tighter-margin repo (solidus) is the most direct way to
   lie about a two-repo result (checker defect 12).
4. **Every `Q` value is an assertion, not a measurement, until it is
   recomputed from the graph.** A maker could under-report the Louvain
   baseline (e.g. all ten seeds at `Q=0.35` instead of the real ~0.74)
   and ship a clusterer scoring `Q=0.31` against a bar that is only as
   honest as the numbers used to compute it — `0.85 * median` is
   trivially gameable by lying about the baseline (checker defect 13).
5. **The sidecar must be tied to the indexer that produced it.** A4/A5
   modify `codegraph.py`; nothing tied a recorded PASS to the specific
   `SCHEMA_EPOCH`/`EXPECTED_DDL_HASH`/`confident_edges()` selection logic
   that produced it, so a stale sidecar could certify an indexer that no
   longer exists (the staleness hole).

**The fix for (4) and (5)** is what is committed alongside this file
today:

- **`probe-graphs.json.gz`** (built by
  `scripts/probe-assemble-graph-bundle.py`): the confident subgraph
  itself — node identities and the edge list, in the SAME canonical
  total order `communities()`'s own node ordering already produces — the
  shipped LPA's partition, and all ten Louvain partitions by seed, for
  BOTH repos. This is the actual **primitive** data; `node_count`,
  `edge_count`, `lpa_community_count`, and every modularity `Q` are
  **derived** from it, never stored as an independent, trust-me number.
- **`probe-lpa-vs-louvain.json`** (the sidecar) now also carries
  `graph_bundle.sha256` (the bundle's integrity hash) and
  `indexer_fingerprint` (a hash over `SCHEMA_EPOCH`, `EXPECTED_DDL_HASH`,
  and `confident_edges()`'s own source text, computed at probe time).
- **`scripts/verify-probe-verdict.py`**, given only these two files and
  the current tree (no networkx, no `mcp-server` environment, standard
  library only), now:
  1. Verifies the bundle's sha256 against the sidecar's recorded value
     (a tampered or substituted bundle is loud, not silent).
  2. Recomputes `node_count`/`edge_count`/`lpa_community_count` straight
     from the graph and rejects any mismatch against the sidecar's
     recorded copies.
  3. Recomputes EVERY modularity `Q` — the shipped LPA's, and each of the
     ten Louvain runs' — in **pure Python**
     (`Q = sum_c [ L_c/m - (deg_c/2m)^2 ]`, the standard undirected/
     unweighted formula at resolution `gamma = 1`), and asserts each
     recomputed value equals the sidecar's recorded (networkx-computed)
     value within float tolerance. This does double duty: it independently
     confirms the recorded networkx run was honest, AND cross-validates
     this pure-Python formula against `nx.community.modularity` itself —
     the two agree to within float64 noise (~1e-15) on all 22 values
     across both repos (see the reproduction table below).
  4. Uses the **recomputed** values, never the sidecar's, for the actual
     pass/fail arithmetic from here on.
  5. Recomputes the `indexer_fingerprint` from the CURRENT tree's
     `codegraph.py` and fails loudly — "the probe is stale and must be
     re-run" — on any mismatch, rather than silently certifying a changed
     indexer.

`scripts/test-verify-probe-verdict.sh` forges fourteen artefacts (every
round above, plus the graph-bundle/fingerprint attacks) and asserts each
is rejected while the real, currently-recorded sidecar + bundle are
accepted — each guard's necessity was proven in isolation by disabling it
and confirming precisely (and only) the matching scenario flips.

## The one input this cannot mechanically verify — stated, not implied

After all of the above, there is exactly one remaining trusted input:
**that the committed graph bundle is a faithful export of the two pinned
repo SHAs.** Verifying that mechanically would require cloning and
indexing two full Rails applications on every PR — CI cannot do that,
and this project does not pretend otherwise. That link is attested by
**independent reproduction**, not by a mechanical check: the checker
re-cloned both repos at the exact pinned SHAs, re-indexed them with the
shipped `CodeGraph`, re-exported the confident subgraph, and re-scored it
with an ephemeral networkx run — and every load-bearing float reproduced
to the last digit (see the per-repo reproduction table below). An
honest, documented bound is a guarantee; an undocumented one is the same
defect this campaign has now found (and closed) thirteen times. This
same statement is repeated in `.github/workflows/harden-gate.yml`'s
header comment, next to the mechanical checks it actually runs.

**Reader check:** every number below was pasted straight out of the
probe scripts' JSON output, not retyped/rounded by hand except where
explicitly noted (4-6 significant decimal digits in prose; the sidecar
and graph bundle preserve full float64/exact-integer precision, so the
pass/fail arithmetic — and the modularity formula itself — is
independently re-checkable without trusting this summary).

## Reference repos (pinned, exact SHAs)

| Repo | URL | Pinned commit | Clone method |
|---|---|---|---|
| solidus | `https://github.com/solidusio/solidus` | `4026945d614e81383c007ed1ab1278a0195ce5d9` | `git init && git remote add origin <url> && git fetch --depth 1 origin <sha> && git checkout FETCH_HEAD` (shallow, full tree) |
| spree | `https://github.com/spree/spree` | `6699cde44303ea85ef6e56c5e87c44a738ab73fc` | same, plus `git config core.sparseCheckout true` + `.git/info/sparse-checkout` = `spree/*` (Rails engine split; only the `spree/` subtree is indexed) |

Both `git rev-parse HEAD` after checkout matched the pinned SHA exactly
(reproduced at probe time, not asserted from memory).

## Method (three-phase, networkx isolated to a throwaway environment)

**Phase 1** (`scripts/probe-export-confident-graph.py`, run under
`mcp-server`'s own `uv run --frozen` environment — no networkx anywhere
near this phase): indexes the pinned repo with the shipped `CodeGraph`,
pulls `confident_edges()`, builds the undirected/unweighted node+edge
list using the SAME `_resolve_source_node`/`_target_kind` resolution
`communities()` itself uses (D3a's "identical input to both algorithms"
requirement), and calls the shipped `communities()` to record its
partition. Node identities are committed in `communities()`'s own
canonical total order. A structural assertion in the script (`node set
mismatch`) fails loudly if the node set this script derived ever
diverged from `communities()`'s own node set — it did not, on either
repo.

**Phase 2** (`scripts/probe-modularity.py`, run via
`uv run --with networkx --no-project` from the **repo root**, which has
no `pyproject.toml`/`uv.lock` of its own — an ephemeral, single-invocation
environment, never `uv add networkx` anywhere): reads phase 1's JSON,
builds one `networkx.Graph`, computes `LPA_Q` and ten Louvain runs'
`Q` (seeds 0..9, resolution 1.0) via `nx.community.modularity`/
`nx.community.louvain_communities`, and ALSO emits each run's own
partition (not just its `Q`) for the bundle below.

**Phase 3** (`scripts/probe-assemble-graph-bundle.py`, dependency-free —
`json`/`gzip`/`hashlib` only, no networkx, no `mcp-server` environment):
merges phase 1's graph + phase 2's partitions into the single committed
`probe-graphs.json.gz`, and prints its sha256 for the sidecar.

`networkx==3.6.1` (whatever `uv --with networkx` resolved at probe time).

## Solidus — `4026945d614e81383c007ed1ab1278a0195ce5d9`

Confident subgraph (input to BOTH algorithms, identical):
- **2,676 nodes**, **4,217 undirected edges** (deduplicated, self-loops dropped)
- Full index for context: 2,519 files indexed, 21,522 symbols, 117,458 refs,
  37,364 total edges of which **17,346 resolved** (EXTRACTED+INFERRED,
  `confident_edges()`) and **20,018 AMBIGUOUS excluded**
  (17,346 + 20,018 = 37,364 — accounted for)
- Shipped LPA partition: **228 communities**

Louvain — 10 runs, seeds 0..9, resolution (gamma) = 1.0:

| seed | Q (networkx) | Q (pure-Python recompute) |
|---|---|---|
| 0 | 0.7405822183086415 | agrees to ~1e-15 |
| 1 | 0.7451940436220051 | agrees to ~1e-15 |
| 2 | 0.7470429912373492 | agrees to ~1e-15 |
| 3 | 0.7447625662785583 | agrees to ~1e-15 |
| 4 | 0.7444007618698866 | agrees to ~1e-15 |
| 5 | 0.7456120531140569 | agrees to ~1e-15 |
| 6 | 0.7484086426154646 | agrees to ~1e-15 |
| 7 | 0.745414168483327  | agrees to ~1e-15 |
| 8 | 0.7441155470795878 | agrees to ~1e-15 |
| 9 | 0.7392709725515066 | agrees to ~1e-15 |

- **Louvain_Q_median = 0.7449783049502817** (recomputed independently from
  the raw per-seed values by `verify-probe-verdict.py`, never read as a
  cached field)
- Louvain_Q_best = 0.7484086426154646, worst = 0.7392709725515066
- Louvain_Q_mean = 0.7444803965160384, sd = 0.0025925440401239684 (population sd)

- **LPA_Q = 0.6691476098443865** (networkx) — pure-Python recompute agrees
  to ~1e-15

Clause evaluation (solidus, independently, using the recomputed values):
```
Louvain_Q_median >= 0.30   ->  0.744978 >= 0.30           -> PASS
LPA_Q            >= 0.30   ->  0.669148 >= 0.30           -> PASS
LPA_Q  >= 0.85 * Louvain_Q_median
      ->  0.85 * 0.7449783049502817 = 0.6332315592077394
      ->  0.6691476098443865 >= 0.6332315592077394         -> PASS (margin +0.035916)
```
**Solidus verdict: PASS (3/3 clauses).**

## Spree — `6699cde44303ea85ef6e56c5e87c44a738ab73fc`

Confident subgraph (input to BOTH algorithms, identical):
- **5,391 nodes**, **8,223 undirected edges** (deduplicated, self-loops dropped)
- Full index for context (`spree/` subtree only): 2,358 files indexed,
  18,683 symbols, 193,189 refs, 110,678 total edges of which
  **33,024 resolved** and **77,654 AMBIGUOUS excluded**
  (33,024 + 77,654 = 110,678 — accounted for)
- Shipped LPA partition: **371 communities**

Louvain — 10 runs, seeds 0..9, resolution (gamma) = 1.0:

| seed | Q (networkx) | Q (pure-Python recompute) |
|---|---|---|
| 0 | 0.7842462736363122 | agrees to ~1e-15 |
| 1 | 0.7871970308260426 | agrees to ~1e-15 |
| 2 | 0.7866764351106793 | agrees to ~1e-15 |
| 3 | 0.7846281261531277 | agrees to ~1e-15 |
| 4 | 0.7844339389156355 | agrees to ~1e-15 |
| 5 | 0.7823176522240195 | agrees to ~1e-15 |
| 6 | 0.7878835593546776 | agrees to ~1e-15 |
| 7 | 0.7846674057923477 | agrees to ~1e-15 |
| 8 | 0.7879746079611754 | agrees to ~1e-15 |
| 9 | 0.7833310698737013 | agrees to ~1e-15 |

- **Louvain_Q_median = 0.7846477659727378** (recomputed independently)
- Louvain_Q_best = 0.7879746079611754, worst = 0.7823176522240195
- Louvain_Q_mean = 0.7853356099847719, sd = 0.0018652613916560594 (population sd)

- **LPA_Q = 0.7165340320731565** (networkx) — pure-Python recompute agrees
  to ~1e-15

Clause evaluation (spree, independently, using the recomputed values):
```
Louvain_Q_median >= 0.30   ->  0.784648 >= 0.30           -> PASS
LPA_Q            >= 0.30   ->  0.716534 >= 0.30           -> PASS
LPA_Q  >= 0.85 * Louvain_Q_median
      ->  0.85 * 0.7846477659727378 = 0.6669506010768271
      ->  0.7165340320731565 >= 0.6669506010768271         -> PASS (margin +0.049584)
```
**Spree verdict: PASS (3/3 clauses).**

## Note on the recomputed numbers vs. an earlier snapshot of this artefact

An earlier revision of this file recorded slightly different Louvain
seed values (e.g. solidus median `0.744262` vs. `0.744978` above). The
underlying confident subgraph is IDENTICAL (same 2,676/4,217 and
5,391/8,223 node/edge counts, same `LPA_Q` to the last digit, since the
shipped LPA is a deterministic function of node identity, not of an
arbitrary integer labeling). The difference is that this revision
canonicalizes the graph's node-to-integer mapping to `communities()`'s
own sorted order (needed so the committed bundle has one, reproducible,
documented node order) — Louvain's result can depend on that integer
labeling (a known property of the algorithm, not a bug in this probe),
so re-deriving the bundle under the canonical order produced a
(negligibly) different set of ten seed values. Both snapshots pass all
three clauses on both repos with comfortable margin; this is a
methodological refinement (a documented, reproducible node order,
required by the recompute-everything closure above), not a re-roll of
the measurement to chase a better number.

## networkx isolation, confirmed

```
$ grep -ric networkx mcp-server/pyproject.toml mcp-server/uv.lock
mcp-server/pyproject.toml:0
mcp-server/uv.lock:0
```
networkx never entered `mcp-server/`'s dependency tree — it ran only via
`uv run --with networkx --no-project` from the repo root (no
`pyproject.toml` there), a fully ephemeral resolution for the single
`probe-modularity.py` invocation per repo. `scripts/verify-probe-verdict.py`
(the gate's actual check) never imports networkx at all.

## Overall verdict: PASS — A3 ships

Both repos, independently, pass all three frozen clauses, using values
recomputed from the committed graph bundle in pure Python. Per the D3a
protocol this is a strict AND across repos (either one failing any
clause would cut A3) — both passed all three, so **A3 (deterministic LPA
communities) ships**, mechanically justified by the numbers above, not by
argument.

## Reproduction

```bash
# Solidus (shallow, full tree, exact SHA)
mkdir solidus && cd solidus && git init -q
git remote add origin https://github.com/solidusio/solidus.git
git fetch --depth 1 origin 4026945d614e81383c007ed1ab1278a0195ce5d9
git checkout -q FETCH_HEAD

# Spree (shallow, sparse-checked to spree/, exact SHA)
mkdir spree && cd spree && git init -q
git remote add origin https://github.com/spree/spree.git
git config core.sparseCheckout true
mkdir -p .git/info && printf 'spree/*\n' > .git/info/sparse-checkout
git fetch --depth 1 origin 6699cde44303ea85ef6e56c5e87c44a738ab73fc
git checkout -q FETCH_HEAD

# Phase 1 (inside mcp-server's own frozen env; repeat per repo)
cd mcp-server
uv run --frozen python ../scripts/probe-export-confident-graph.py \
    <repo_path> /tmp/<repo>-graph.json

# Phase 2 (ephemeral networkx, from the atlas-aci repo root; repeat per repo)
cd ..
uv run --with networkx --no-project python scripts/probe-modularity.py \
    /tmp/<repo>-graph.json > /tmp/<repo>-modularity.json

# Phase 3 (assemble the committed bundle; dependency-free)
python3 scripts/probe-assemble-graph-bundle.py \
    --out .spectra/changes/aci-v2-harden-and-augment/probe-graphs.json.gz \
    --repo "solidus:/tmp/solidus-graph.json:/tmp/solidus-modularity.json" \
    --repo "spree:/tmp/spree-graph.json:/tmp/spree-modularity.json"

# Verify (standard library only; no networkx, no mcp-server env)
python3 scripts/verify-probe-verdict.py \
    .spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.json
```
