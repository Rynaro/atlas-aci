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

**Reader check:** every number below was pasted straight out of the two
scripts' JSON output (`scripts/probe-export-confident-graph.py` +
`scripts/probe-modularity.py`), not retyped/rounded by hand except where
explicitly noted (4 significant decimal digits in prose; the full
float64 precision is preserved in the "raw JSON" blocks so the pass/fail
arithmetic is independently re-checkable without trusting this summary).

## Reference repos (pinned, exact SHAs)

| Repo | URL | Pinned commit | Clone method |
|---|---|---|---|
| solidus | `https://github.com/solidusio/solidus` | `4026945d614e81383c007ed1ab1278a0195ce5d9` | `git init && git remote add origin <url> && git fetch --depth 1 origin <sha> && git checkout FETCH_HEAD` (shallow, full tree) |
| spree | `https://github.com/spree/spree` | `6699cde44303ea85ef6e56c5e87c44a738ab73fc` | same, plus `git config core.sparseCheckout true` + `.git/info/sparse-checkout` = `spree/*` (Rails engine split; only the `spree/` subtree is indexed) |

Both `git rev-parse HEAD` after checkout matched the pinned SHA exactly
(reproduced at probe time, not asserted from memory).

## Method (two-phase, networkx isolated to a throwaway environment)

**Phase 1** (`scripts/probe-export-confident-graph.py`, run under
`mcp-server`'s own `uv run --frozen` environment — no networkx anywhere
near this phase): indexes the pinned repo with the shipped `CodeGraph`,
pulls `confident_edges()`, builds the undirected/unweighted node+edge
list using the SAME `_resolve_source_node`/`_target_kind` resolution
`communities()` itself uses (D3a's "identical input to both algorithms"
requirement — this is not a hand-re-derived approximation of the graph,
it is the actual graph the shipped LPA analyzed), and separately calls
the shipped `communities()` to record its partition. A structural
assertion in the script (`node set mismatch`) fails loudly if the node
set this script derived ever diverged from `communities()`'s own node
set — it did not, on either repo. Output: one JSON file per repo.

**Phase 2** (`scripts/probe-modularity.py`, run via
`uv run --with networkx --no-project` from the **repo root**, which has
no `pyproject.toml`/`uv.lock` of its own — an ephemeral, single-invocation
environment, never `uv add networkx` anywhere): reads phase 1's JSON,
builds one `networkx.Graph`, computes:
- `LPA_Q` = `nx.community.modularity(g, <shipped LPA's groups>)` — the
  shipped partition scored by networkx's own reference modularity
  function, so LPA and Louvain are measured with the identical ruler;
- ten independent `nx.community.louvain_communities(g, seed=s,
  resolution=1.0)` runs for `s in 0..9`, each scored the same way.

`networkx==3.6.1` (whatever `uv --with networkx` resolved at probe time;
recorded per-repo in the raw JSON below).

## Solidus — `4026945d614e81383c007ed1ab1278a0195ce5d9`

Confident subgraph (input to BOTH algorithms, identical):
- **2,676 nodes**, **4,217 undirected edges** (deduplicated, self-loops dropped)
- Full index for context: 2,519 files indexed, 21,522 symbols, 117,458 refs,
  37,364 total edges of which **17,346 resolved** (EXTRACTED+INFERRED,
  `confident_edges()`) and **20,018 AMBIGUOUS excluded**
  (17,346 + 20,018 = 37,364 — accounted for)
- Shipped LPA partition: **228 communities**

Louvain — 10 runs, seeds 0..9, resolution (gamma) = 1.0:

| seed | Q |
|---|---|
| 0 | 0.7458086443811871 |
| 1 | 0.7451503223090207 |
| 2 | 0.7418849447359792 |
| 3 | 0.7450801432754456 |
| 4 | 0.741671146109655 |
| 5 | 0.7456979212104264 |
| 6 | 0.7464751202673506 |
| 7 | 0.7392679640753077 |
| 8 | 0.7434448255868258 |
| 9 | 0.743328647795667 |

- **Louvain_Q_median = 0.7442624844311356**
- Louvain_Q_best = 0.7464751202673506, worst = 0.7392679640753077
- Louvain_Q_mean = 0.7437809679746865, sd = 0.002175745102662048

- **LPA_Q = 0.6691476098443865**

Clause evaluation (solidus, independently):
```
Louvain_Q_median >= 0.30   ->  0.744262 >= 0.30           -> PASS
LPA_Q            >= 0.30   ->  0.669148 >= 0.30           -> PASS
LPA_Q  >= 0.85 * Louvain_Q_median
      ->  0.85 * 0.7442624844311356 = 0.6326231117664652
      ->  0.6691476098443865 >= 0.6326231117664652         -> PASS (margin +0.036524)
```
**Solidus verdict: PASS (3/3 clauses).**

<details><summary>raw phase-2 JSON (solidus)</summary>

```json
{
  "repo": "/tmp/atlas-aci-probe/solidus",
  "node_count": 2676,
  "edge_count": 4217,
  "lpa_q": 0.6691476098443865,
  "lpa_community_count": 228,
  "louvain_median": 0.7442624844311356,
  "louvain_best": 0.7464751202673506,
  "louvain_worst": 0.7392679640753077,
  "louvain_mean": 0.7437809679746865,
  "louvain_sd": 0.002175745102662048,
  "networkx_version": "3.6.1"
}
```

</details>

## Spree — `6699cde44303ea85ef6e56c5e87c44a738ab73fc`

Confident subgraph (input to BOTH algorithms, identical):
- **5,391 nodes**, **8,223 undirected edges** (deduplicated, self-loops dropped)
- Full index for context (`spree/` subtree only): 2,358 files indexed,
  18,683 symbols, 193,189 refs, 110,678 total edges of which
  **33,024 resolved** and **77,654 AMBIGUOUS excluded**
  (33,024 + 77,654 = 110,678 — accounted for)
- Shipped LPA partition: **371 communities**

Louvain — 10 runs, seeds 0..9, resolution (gamma) = 1.0:

| seed | Q |
|---|---|
| 0 | 0.7823565917157614 |
| 1 | 0.7819666569990837 |
| 2 | 0.7831034076876495 |
| 3 | 0.786152866802137 |
| 4 | 0.7834588913803953 |
| 5 | 0.7871688503469261 |
| 6 | 0.7842090200929405 |
| 7 | 0.785905335566653 |
| 8 | 0.7850854322540173 |
| 9 | 0.7868501469488868 |

- **Louvain_Q_median = 0.7846472261734789**
- Louvain_Q_best = 0.7871688503469261, worst = 0.7819666569990837
- Louvain_Q_mean = 0.7846257199794451, sd = 0.0017795684238646426

- **LPA_Q = 0.7165340320731565**

Clause evaluation (spree, independently):
```
Louvain_Q_median >= 0.30   ->  0.784647 >= 0.30           -> PASS
LPA_Q            >= 0.30   ->  0.716534 >= 0.30           -> PASS
LPA_Q  >= 0.85 * Louvain_Q_median
      ->  0.85 * 0.7846472261734789 = 0.6669501422474571
      ->  0.7165340320731565 >= 0.6669501422474571         -> PASS (margin +0.049584)
```
**Spree verdict: PASS (3/3 clauses).**

<details><summary>raw phase-2 JSON (spree)</summary>

```json
{
  "repo": "/tmp/atlas-aci-probe/spree/spree",
  "node_count": 5391,
  "edge_count": 8223,
  "lpa_q": 0.7165340320731565,
  "lpa_community_count": 371,
  "louvain_median": 0.7846472261734789,
  "louvain_best": 0.7871688503469261,
  "louvain_worst": 0.7819666569990837,
  "louvain_mean": 0.7846257199794451,
  "louvain_sd": 0.0017795684238646426,
  "networkx_version": "3.6.1"
}
```

</details>

## Overall verdict: PASS — A3 ships

Both repos, independently, pass all three frozen clauses. Per the D3a
protocol this is a strict AND across repos (either one failing any
clause would cut A3) — both passed all three, so **A3 (deterministic LPA
communities) ships**, mechanically justified by the numbers above, not by
argument.

## networkx isolation, confirmed

```
$ grep -ric networkx mcp-server/pyproject.toml mcp-server/uv.lock
mcp-server/pyproject.toml:0
mcp-server/uv.lock:0
```
networkx never entered `mcp-server/`'s dependency tree — it ran only via
`uv run --with networkx --no-project` from the repo root (no
`pyproject.toml` there), a fully ephemeral resolution for the single
`probe-modularity.py` invocation per repo.

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
```
