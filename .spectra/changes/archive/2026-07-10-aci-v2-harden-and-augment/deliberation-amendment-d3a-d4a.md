# FORGE amendment decision record — D3a, D4a (ESL change `aci-v2-harden-and-augment`)

**Gate:** ESL `in_progress` (tier full). Amends D3/D4 of `deliberation.md` under new evidence
(`v1-reference-repo.md`, the ATLAS D3 probe). **Decision type:** D3a = CONSTRAINT-SATISFACTION +
probe-methodology design; D4a = TRADE-OFF with a hard correctness constraint. **Depth:** Deep,
single-trace (not G2 — reversible design decisions feeding the planner). **requires_checker:** false.
**Evidence reliability:** H — `v1-reference-repo.md` (ATLAS, anchored to `f56a78e`), `deliberation.md`
D3 (0.65)/D4 (0.85), `spec.md` F18/D3/D4, `acceptance-criteria.md` frozen IDs, `checker-critique.md`
F4/F5/F7.

**Settled inputs (not relitigated):** `AC-NEG-2` (no networkx in the resolved dep tree) is absolute;
a failed probe **cuts A3 to v2.1** and ships A2 only (DIR-1). Zero-LLM everywhere. Confidence enum is
`EXTRACTED`/`INFERRED`/`AMBIGUOUS(candidate_count>1, candidates[])`; zero candidates = unresolved ref.
Louvain appears **only** as a probe-time baseline in an ad-hoc scratch venv, never in
`mcp-server/pyproject.toml`/`uv.lock` — this is what keeps AC-NEG-2 intact (ATLAS §2, F4/DIR-1).

**Convergent anchor used by both records (H).** Per `spec.md` F18 and ATLAS's direct file fetches
(`product.rb`, `breadcrumb_concern.rb`), the *structure-bearing* edges in the reference repos —
`include Foo::Bar`, `Foo::bar`, `extend`, class heritage — are **constant-qualified → EXTRACTED**
(confident). Ambiguous bare-name calls (`each`, `map`, `call`) are the `candidate_count>1` set. The
confident subgraph is therefore where the six-engine architecture lives; the ambiguous set is mostly
common-method noise.

---

## D3a — The modularity pass rule: pre-registered, two-part, per-repo, non-flapping

**[CONTEXT]** The frozen rule (`AC-A3-4`, and the constants preamble `acceptance-criteria.md:17-21`)
is `LPA_modularity >= Louvain_modularity - 0.05` on *the* pinned reference repo. F5 made the delta
numeric but left the *shape* absolute. ATLAS refutes the shape: modularity Q is scale- and
resolution-dependent, so a fixed absolute delta is **lax where structure is weak** (the biggest
*relative* miss — e.g. a dense `core` engine at Q≈0.15–0.25, where 0.05 is a 20–33% relative miss)
and **strict where structure is strong** (Q≈0.5–0.7, where 0.05 is a ~7–10% miss). ATLAS recommends
a relative floor `LPA_Q >= R·Louvain_Q`, a "real structure exists" precondition on Louvain's own Q,
and a per-repo (never averaged) bar on ≥2 repos — but **declined to assert R without measurement**.
The parent adds three unresolved hazards ATLAS did not name: (1) **circularity** — if the probe
*measures* R and the criterion *uses* R, the probe grades its own homework; (2) the **structureless-
repo branch** — inconclusive-and-swap vs. cut-regardless; (3) **stochasticity** — Louvain is
stochastic + resolution-dependent, LPA is stochastic + order-dependent, so a single run of each is
not a measurement and "a criterion that flaps between runs is not mechanical."

**[OPTIONS]**
- **(a) Keep absolute delta `−0.05`.** Rejected by ATLAS's core argument; the delta is uncalibrated
  against achievable Q. Reject.
- **(b) Relative ratio only, `LPA_Q >= R·Louvain_Q`.** Better shape, but ATLAS's own flaw stands: it
  degrades near `Louvain_Q≈0`, and R alone doesn't catch the specific **giant-community collapse**
  failure mode of LPA, nor does it fix circularity or flapping. Incomplete.
- **(c) "Measure first, then set R."** Rejected on two grounds: it is the exact **circularity** the
  parent forbids (probe fixes its own constant), *and* A3 cannot begin until this is settled, so it
  imposes the real cost the parent flagged. Reject — and note below it is *not necessary*.
- **(d) Pre-registered two-part rule + Louvain precondition + deterministic run discipline.**
  Selected. The constants are fixed by *principle* now, in the criteria, before the probe runs.

**[DECISION] Adopt (d). The complete, mechanical, pre-registered rule:**

**Run discipline (exploits D6 total orders, per the parent's hint).**
- **LPA is run ONCE, with no seed and zero variance.** `AC-A3-2` already forces the shipped LPA to
  be deterministic (identical membership + total-ordered IDs on identical input); its
  node-visitation order and tie-breaks are pinned to the D6 total order. The probe MUST use *that
  exact shipped implementation* over the *same deterministic sorted edge table* — not a reference
  LPA — else it grades a different algorithm than what ships. So LPA's side is a single number.
- **Louvain (baseline) variance is pinned by fixing all of its free parameters.** Run networkx
  `louvain_communities` **K=10** times with **seeds 0..9** (recorded), at **resolution γ=1.0**
  (classic Newman-Girvan modularity, the objective Q is defined at; recorded). networkx Louvain is
  deterministic given a seed, so best/worst/median/mean±sd over the fixed seed set are a
  **deterministic function** of (repo@SHA, atlas-aci version, K, seeds, γ). **Baseline statistic =
  median**; best/worst/mean±sd are also recorded so a near-boundary verdict is inspectable.
- **Both algorithms consume the identical graph** (see D4a): the **confident subgraph**
  (EXTRACTED ∪ INFERRED), taken as an **undirected, unweighted projection** (multi-edges collapsed,
  direction dropped) so LPA's and Louvain's Q are apples-to-apples. Result: the *entire probe is
  deterministic* — same inputs → same verdict. Non-flapping by construction.

**Precondition (per repo, on Louvain).** `Louvain_Q_median >= Q_struct` with **`Q_struct = 0.30`**
— the Newman "meaningful community structure exists" floor. **Transfer to a code dependency graph
(justified, not assumed):** 0.30 is used here *only* to reject a structureless test graph, a
*conservative* use. A well-architected code graph should clear it *more* easily than the social/
biological networks the heuristic came from, because enforced module boundaries (Rails engines,
namespaces) create exactly the dense-within/sparse-between structure modularity measures — ATLAS's
verified six-engine ground truth is precisely such structure. The transfer is *asymmetrically safe*:
a structured graph clears it comfortably; only a genuinely structureless graph fails. (The god-node/
heavy-tail property depresses Q somewhat, but the engine boundaries dominate in the pinned repos.)
It is **not** a quality target for LPA — that is the relative term's job.

**Pass rule (per repo, on LPA — BOTH must hold):**
1. **Absolute floor:** `LPA_Q >= Q_struct (0.30)`. LPA's own partition must independently show
   meaningful structure. This directly catches giant-community collapse (collapse → Q≈0 < 0.30).
2. **Relative retention:** `LPA_Q >= R · Louvain_Q_median` with **`R = 0.85` fixed a priori.**

**Justification of R = 0.85 (not round-because-round).** R is bounded by two *principled* endpoints:
- **Upper bound ~0.95 (modularity-degeneracy plateau, Good–de Montjoye–Clauset 2010):** the top of
  the modularity landscape is *flat* — many structurally-distinct partitions sit within a few percent
  of max-Q. The acceptance band must exceed that plateau, or we would reject LPA for landing on a
  *different-but-equivalent* high-modularity partition. A ~10% band (R≈0.90) clears a few-percent
  plateau with margin; anything stricter than ~0.95 risks rejecting equivalents.
- **Lower bound ~0.80 (advisory floor):** below ~0.80, LPA is leaving >20% of *provable* Louvain
  structure unfound — that stops being "modestly worse" and becomes "materially incomplete" for a
  navigation tool.
- **R = 0.85 is the midpoint of the derived band [0.80, 0.90].** The residual within the band is the
  single *maintainer brand-vs-quality weight* that `deliberation.md` D3 flagged as inferred-not-given
  (the reason D3 was 0.65) — it is not resolvable by more reasoning, only by a maintainer dial. I fix
  it at the midpoint; the reversal condition names the weight that moves it to 0.80 or 0.90.
- **Why the exact value is only weakly load-bearing:** LPA's failure is **bimodal** (H, LPA
  literature — Raghavan 2007 et seq.): it either finds roughly-comparable structure (Q within a
  modest fraction of Louvain) *or* collapses (Q→0). R=0.80/0.85/0.90 changes the verdict *only* in a
  narrow marginal band; the real failure (collapse) fails any R and the absolute floor. So the *rule*
  is high-confidence even though the *constant* carries one bounded residual.

**Anti-circularity (explicit).** `Q_struct=0.30`, `R=0.85`, `K=10`, seeds `0..9`, `γ=1.0` are frozen
in the criteria **before** the probe runs. The probe *measures* `Louvain_Q` and `LPA_Q` and *applies*
the frozen rule; it never derives the constants from its own measurements. The rule is falsifiable —
LPA can fail it — and the amended criteria MUST be re-frozen before the probe executes (fits the
timeline: the probe is A3's precondition; A3 is gated behind this settlement).

**Per-repo, never averaged.** The rule must pass **independently on Solidus AND Spree** (the two
ATLAS-pinned engine-split repos, pinned by **commit SHA** per ATLAS §5:
`solidusio/solidus@4026945d614e81383c007ed1ab1278a0195ce5d9`,
`spree/spree@6699cde44303ea85ef6e56c5e87c44a738ab73fc`). Either repo failing → **CUT A3 to v2.1**.

**Structureless-repo branch (resolves the parent's either/or by scoping it).** Because the reference
repos are chosen for *source-verified* structure (engine splits ATLAS confirmed exist in source), the
Louvain precondition is effectively a check on **atlas-aci's own graph construction**, not on the
repo:
- If a **source-verified-structured** pinned repo yields `Louvain_Q_median < 0.30`, the constructed
  graph failed to surface *known* structure → **probe FAIL → cut A3** (communities over a graph that
  can't even surface engine boundaries are noise), **and flag A1 edge-quality** for review. It is
  *not* "inconclusive, swap the repo."
- Re-pinning a repo is warranted *only* if the source-structure assumption itself was wrong (a repo
  we believed engine-split turns out flat) — a maker error in selection, corrected by swapping, not a
  probe result.

**[CONSEQUENCE]** Forecloses: the absolute-delta rule; any single-run comparison; the "measure R
first" path; and averaging across repos. Commits the probe to run atlas-aci's *shipped* LPA (couples
the probe to `AC-A3-2`/`AC-A3-3`). Makes the probe verdict a **mechanical function of recorded
numbers**, which resolves F7 (no prose judgement at probe time). Because the confident subgraph
(D4a) carries the constant-qualified include/heritage edges that *define* engine structure, the probe
graph should *retain* — arguably *sharpen* — the architecture, so a low `Louvain_Q` would genuinely
indict construction, not the rule. Preserves AC-NEG-2 (Louvain stays probe-time-only). Does **not**
require measurement to settle the rule (only the already-required probe run to get the verdict).

**[REVERSAL CONDITION]** (1) **Maintainer brand-vs-quality weight** — the one input that moves R
within [0.80, 0.90]: weight ship-it/brand-minimalism higher → R=0.80; weight community-quality
higher → R=0.90. Signal: an explicit maintainer weight, or a probe result landing in the marginal
band (recorded ratio between 0.80 and 0.90) where the constant becomes load-bearing. (2) If a
source-verified-structured repo's confident subgraph is too sparse for *Louvain* to clear
`Q_struct` (construction, not algorithm), that is the FAIL/cut signal *and* an A1 edge-recall
reversal for the extraction layer. (3) If the plateau/bimodal-failure assumptions prove wrong on
these specific graphs (signal: LPA lands mid-band on *both* repos with high seed-variance in
Louvain), escalate the R weight to the maintainer rather than trust the midpoint.

**Confidence: 0.78.** Decomposed honestly: the **rule shape** (two-part + precondition + deterministic
single-run LPA vs seeded-median Louvain + per-repo) is ~0.88 — mechanically sound, non-circular,
non-flapping. The **exact R** carries the ~0.65 residual inherited from D3 (the un-given maintainer
weight), bounded to [0.80,0.90] and fixed at 0.85. Net 0.78 (> 0.7). Evidence that would raise it:
the maintainer weight (turns 0.78 → ~0.9), and the probe's actual landing point (a clear pass or
clear collapse makes the constant moot → retroactively ~0.9).

---

## D4a — AMBIGUOUS edges: excluded from the analysis graph, still returned by `graph_query`

**[CONTEXT]** D4 emits `candidate_count>1` references as AMBIGUOUS edges carrying `candidates[]`,
never dropped (`AC-A1-5`), because "never silently incomplete." But nobody decided whether those
edges **participate in degree centrality (A2) and community detection (A3)** — which silently changes
god-node ranking, modularity, *and the D3a probe result itself*. An AMBIGUOUS edge is **one syntactic
call site with a known source and a k-way-uncertain target** — it is *not* k facts, and *not* one
fact to a known node. The acute failure mode: common names (`call`, `run`, `new`, `each`) are exactly
the high-candidate-count references, so any option that credits target-side importance by candidate
membership **ranks ambiguity as importance** — making god nodes *actively misleading*, worse than not
shipping them.

**[OPTIONS]**
- **(1) Exclude AMBIGUOUS from the analysis graph** (A2/A3/probe) while `graph_query` still returns
  them. The confident subgraph (EXTRACTED ∪ INFERRED) is the analysis object.
- **(2) Fractional-weight 1/k to each candidate.** The "expected graph." Dampens but does not
  eliminate ambiguity-as-importance (a frequent candidate still accrues weight); injects *phantom
  fractional connectivity* into A3 that still biases toward merging communities; and leaks float-
  weight determinism concerns into degree/D6.
- **(3) Full edge to every candidate.** Disqualified for A2 by the parent's own argument — inflates
  degree *most* for the common names we rank. For A3 it manufactures connectivity between a source
  and *every* candidate of a common name, merging communities through god-node candidates → drives
  the exact LPA collapse failure mode.
- **(4) Synthetic node per ambiguous ref.** Pollutes A2/A3 with non-source entities (synthetic hubs
  rank as god nodes; artificial communities form). Worst of both — complexity + phantom structure.

**[DECISION] Adopt (1). The analysis graph is the confident subgraph; `graph_query` is unchanged.**

- **The `graph_query` returned edges and the analysis graph DIVERGE — deliberately.** `graph_query`
  (`callers_of`, `subclasses_of`, …) returns **all** matching edges including AMBIGUOUS (with
  `candidates[]`) — the *navigation/honesty* view (D4 unchanged, `AC-A1-5` preserved). The **analysis
  graph** feeding A2 god-nodes, A3 communities, and the D3a probe is the **confident subgraph
  (EXTRACTED ∪ INFERRED)** — AMBIGUOUS excluded, no fan-out, no fractional weight.
- **This divergence is a real design commitment and is made VISIBLE in the schema.** The `god_nodes`
  and `communities` responses MUST carry `analysis_basis: "confident_edges"` and
  `ambiguous_edges_excluded: N` (and `resolved_edge_count`), and the `graph_query` DSL doc
  (`AC-A1-10` pattern) MUST document it — otherwise, per the parent's warning, an agent sees a
  ranking, then queries callers, gets AMBIGUOUS edges too, and reasons about a graph the tool never
  ranked over. The visibility fields close that gap; this is the same "loud, not silent" fix as D2
  truncate-and-flag and D4 tagged-uncertainty.
- **Reckoning with A2 (god nodes).** Degree centrality (both in- and out-degree) is computed over the
  confident subgraph. This makes a god node "a symbol many references *definitely/probably* reach,"
  not "a symbol with a common name." Excluding AMBIGUOUS from *target in-degree* is the essential
  anti-inflation guarantee (the failure the parent named). Excluding it from *source out-degree* too
  (rather than the split rule "count +1 to source, 0 to targets") is chosen for **one clean analysis
  graph** shared by A2/A3/probe: the out-degree undercount is roughly *uniform* across sources
  (ambiguity isn't concentrated on specific callers the way it is on common *target names*), so it
  barely perturbs *ranking*, while the in-degree protection is decisive. Under-ranking is the *safe*
  direction (under-claim, not the misleading over-claim of ambiguity inflation) and A2 is advisory.
- **Reckoning with A3 (communities).** Exclusion is *forced*, not merely chosen: an AMBIGUOUS edge
  has no single target, so it cannot carry a label between two nodes in LPA/Louvain. Fanning out
  (option 3) or fractional-weighting (option 2) would inject connectivity corresponding to *no
  definite source fact* — philosophically the **same phantom-graph sin D6 rejected** for the union
  merge driver. Reject on thesis-consistency.
- **Deterministic, total-ordered, zero-LLM.** The confident/ambiguous partition is candidate-count-
  deterministic (D4); "exclude AMBIGUOUS" is set membership over a total-ordered edge table; no LLM,
  no floats, D6-clean.

**Effect on the D3a probe (the parent's explicit ask).** The probe computes modularity over the
**confident subgraph** (EXTRACTED ∪ INFERRED), undirected unweighted projection — the *same* object
LPA, Louvain, and god-nodes consume. Convergent upside (H, from the F18/ATLAS anchor): the structure-
bearing edges (constant-qualified `include`/`::`/heritage) are EXTRACTED, so they *stay in* the probe
graph, while ambiguous common-method calls (which would blur communities) are removed — exclusion is
likely *quality-improving*, not just safe. Interaction with D3a's precondition: if excluding AMBIGUOUS
leaves the confident subgraph too sparse for Louvain to clear `Q_struct` on a source-verified repo,
that is D3a's construction-FAIL/cut signal and an A1 edge-recall flag — the honest outcome.

**[CONSEQUENCE]** Forecloses: fractional/fan-out/synthetic-node graph constructions; and the
implicit assumption that "the graph the tool returns" equals "the graph it analyzes." Commits every
consumer of `god_nodes`/`communities` to read `analysis_basis`/`ambiguous_edges_excluded`. Does
**not** change storage or `graph_query` (AMBIGUOUS still stored, still returned — `AC-A1-2`/`AC-A1-5`
intact) and does **not** shrink the JSONL export or `AC-REL-2` size (AMBIGUOUS edges are still
serialized; D4a is an *analysis-time* filter, not a drop).

**[REVERSAL CONDITION]** If god-node ranking is shown to *under-surface* genuinely call-heavy sources
because their out-calls are disproportionately ambiguous (signal: a source known to be central ranks
low and manual inspection ties it to excluded AMBIGUOUS out-edges), adopt the **split rule** for A2
only: AMBIGUOUS counts +1 to *source out-degree*, 0 to every *target in-degree* — a clean sub-decision
reversal that leaves A3 and the probe (which are algorithmically forced to exclude) untouched. If real
usage shows agents *need* AMBIGUOUS in clustering (unlikely), revisit — but only via a fan-out that
D6/collapse arguments currently forbid.

**Confidence: 0.85.** Anchored: options 2/3 disqualified by the parent's own inflation argument and
D6's phantom-graph precedent; exclusion is *algorithmically forced* for A3; the F18/ATLAS anchor shows
the confident subgraph *retains* the real structure; visibility fields resolve the divergence cleanly.
Residual (the only sub-0.85 piece): the "also exclude from source out-degree" choice over the split
rule — bounded, named, and cleanly reversible without touching A3/probe.

---

## Exact frozen-criteria changes for RAMZA (amend + re-freeze; nothing invented)

**Preamble constants — `acceptance-criteria.md:17-21`.** Replace "the **modularity PASS rule** is
`LPA_modularity >= Louvain_modularity - 0.05`" with the D3a two-part pre-registered rule and its
params (`Q_struct=0.30`, `R=0.85`, `K=10`, seeds `0..9`, `γ=1.0`, median baseline, undirected
unweighted projection of the confident subgraph). Resolve the `[VERIFY]` reference-repo to the **two
pinned commit SHAs** (Solidus `4026945d…`, Spree `6699cde4…`; SHAs not tags, per ATLAS §5).

### D3a
- **AC-A3-4 (REPLACE the threshold clause).** New pass/cut rule, evaluated **independently on each of
  Solidus AND Spree, never averaged**: probe **PASS** on a repo iff (i) precondition
  `Louvain_Q_median >= 0.30`, AND (ii) `LPA_Q >= 0.30`, AND (iii) `LPA_Q >= 0.85 · Louvain_Q_median`,
  where `Louvain_Q_median` = median of K=10 networkx `louvain_communities` runs at seeds 0..9, γ=1.0,
  and `LPA_Q` = the single deterministic run of the *shipped* LPA — both over the **confident
  subgraph** (D4a), undirected unweighted projection. **Either repo failing any clause → A3 CUT to
  v2.1, ship A2 god-nodes only, never adopt networkx/Louvain** (DIR-1; AC-NEG-2 stays absolute). A
  source-verified-structured repo failing the (i) precondition is a **construction FAIL → cut**, not
  a repo swap. Louvain runs only in a probe-time scratch venv (preserves AC-NEG-2).
- **AC-A3-1 (STRENGTHEN — resolves F7).** Keep the file-presence gate on `probe-lpa-vs-louvain.md`,
  but require the artefact to record, **per repo**: the K=10 Louvain best/worst/median/mean±sd with
  seeds + γ; the LPA single deterministic Q (from the shipped impl); the confident-subgraph
  construction (D4a); the three-clause evaluation; and the PASS/CUT verdict. VERIFY additionally
  asserts the recorded verdict **equals the mechanical evaluation of the recorded numbers** (a maker
  cannot record failing numbers under a "PASS" label).
- **AC-A3-2 (CLARIFY).** State that the probe consumes the shipped deterministic LPA over the
  **confident subgraph**, single run, no seed.
- **AC-A3-3 (CLARIFY).** The hand-rolled LPA operates over the confident subgraph (EXTRACTED ∪
  INFERRED); AMBIGUOUS never fanned out.
- **AC-A3-5 (no mechanism change).** "PASS" now denotes AC-A3-4's three-clause rule holding on **both**
  pinned repos.
- **Adjacent, endorsed (ATLAS §2, not forced by D3a but cheap to fold): AC-REL-2.** Amend to measure
  export size **once** at P3 on the **larger** pinned repo (Spree), recorded into the probe/sibling
  artefact, with CI checking the *recorded* number (matching AC-A3-1's measure-once pattern) rather
  than re-cloning per PR. Note: export size includes AMBIGUOUS edges — D4a does **not** shrink it.
- **spec.md D3 (~line 197) and V1 `[VERIFY]` / `plan-state.json` verify_items[0].** Update the
  numeric rule text and pin the two SHAs; V1 becomes RESOLVED-pending-probe-run.

### D4a
- **AC-A2-1 (AMEND).** Degree-centrality god-node ranking SHALL be computed over the **confident
  subgraph (EXTRACTED ∪ INFERRED)**; AMBIGUOUS edges excluded from degree.
- **AC-A2-3 (NEW).** The `god_nodes` and `communities` responses SHALL carry `analysis_basis:
  "confident_edges"`, `ambiguous_edges_excluded: N`, and `resolved_edge_count`, making the
  analysis-graph-vs-returned-edges divergence visible (test-pinned).
- **AC-A3-2 / AC-A3-3 (as above).** LPA community graph = confident subgraph; no fan-out.
- **AC-A1-10 (EXTEND).** The DSL doc SHALL document the divergence: `graph_query` returns all matching
  edges including AMBIGUOUS; `god_nodes`/`communities` analyze only the confident subgraph.
- **AC-NEG-7 (NEW).** AMBIGUOUS edges SHALL NOT contribute to degree-centrality god-node ranking nor
  to community membership (no ambiguity-as-importance; no fan-out to candidates; no fractional-weight
  injection). Test/grep-pinned.
- **PRESERVED, state explicitly (no change): AC-A1-2** (all call/inheritance edges carry the enum) and
  **AC-A1-5** (AMBIGUOUS never dropped from the edge table / `graph_query`). D4a is an analysis-time
  filter, not a drop.

---

## Handoffs
- **→ RAMZA (planner):** amend the six-plus criteria above and re-freeze (`criteria_sha256`); the
  rule and constants are fully specified — no invention required. This must land **before** the probe
  runs (anti-circularity).
- **→ ATLAS (probe executor):** run the probe under the amended AC-A3-4 (K=10 seeded Louvain @ γ=1.0,
  shipped-LPA single run, confident-subgraph undirected projection, per-repo on both pinned SHAs),
  emit `probe-lpa-vs-louvain.md` with the required content schema.
- **→ human (single named residual):** the maintainer brand-vs-quality weight that fixes R within
  [0.80, 0.90]; midpoint 0.85 holds until given.
- **requires_checker:** false (design decisions feeding the planner; no deploy/destroy/spend/public).

*FORGE — amendment to D3/D4 for ESL change `aci-v2-harden-and-augment` (tier full).*
