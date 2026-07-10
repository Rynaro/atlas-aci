# FORGE decision record — atlas-aci v2.0.0 (ESL change `aci-v2-harden-and-augment`)

**Gate:** ESL `deliberated` state, required before `in_progress`. Tier `full`.
**Decision type:** CONSTRAINT-SATISFACTION / TRADE-OFF hybrid, 6 coupled sub-decisions.
**Depth:** Deep (single-trace FORGE, 2 passes). **requires_checker:** false (design decisions → planner; no deploy/destroy/spend/public action).
**Evidence:** `scout-atlas-aci.md` (v0.4.0, HEAD f56a78e), `scout-graphify.md` (HEAD 9c27a52). Reliability **H** — both evidence-anchored to file:line. No CRYSTALIUM prior verdicts on atlas-aci.

## Two ground-truth facts that recur across decisions
- **G-A — the `.atlas/graph.db` is pure derived data.** Gitignored (`.gitignore:14-15`), fully reconstructable from source, no user-authored content. It is a *cache*, not a database of record. (scout claim 8, Del 3 item 7)
- **G-B — atlas-aci has no real edge objects yet.** `refs.callee_name` is a bare string with no FK to `symbols.id`; "edges" are matched dynamically by name at query time (`codegraph.py:408-431`). v2's confidence/inheritance work *creates the first materialized edge set*. (scout claim 3, Del 3)

These are the thesis anchors: **zero-LLM / mechanically-bounded / fully-local, agent-is-untrusted** (`README.md:107-111`). Every decision below is scored against it. A recurring anti-pattern across the whole design is **silence about incompleteness** (silent truncation, silent edge-drop, silent dead-language no-op) — the brand is "never silently incomplete," and that principle resolves D2, D4, and D5-Q2 the same way.

---

## D1 — Schema migration for `.atlas/graph.db` (decided first; gates every feature)

**[CONTEXT]** v2 adds a `confidence` column to `refs` plus new tables (edges, rationale, communities). Today: only additive `CREATE TABLE IF NOT EXISTS`; the `manifest` `version` row is written (`codegraph.py:321`, always `'1'`) but **never read** — no migration mechanism exists (Del 3 item 7). Additive-only tolerates new tables but not *altering* `refs`/`symbols`. `--since` incremental indexing exists (Del claim 3) but a full reindex is not free on Rails-scale.

**[OPTIONS]**
- (a) `schema_version` row + explicit `ALTER TABLE` migration ladder. **Fatal flaw:** the `refs` data change isn't just structural — v1 rows were extracted with *no confidence computation*. `ALTER ADD COLUMN confidence` back-fills a default/NULL that is *semantically wrong*; correct confidence requires re-extraction. So the ladder preserves rows you must recompute anyway → buys nothing, adds N→N+1 test burden + partial-schema hazard. **Reject.**
- (b) detect-mismatch → drop + full reindex. Correct-by-construction (fresh v2 semantics), no ladder. Cost: reindex window where index is absent/partial; binary downgrade re-triggers a drop (ping-pong).
- (c) version-namespaced DB path + auto-sweep (graphify `cache.py:16-35`). Old DB survives until new one is built; downgrade "just works"; sweep is trivial `glob`. graphify-validated; scout flagged it "structurally right" (graphify Del 3 item 1) precisely for the "bugfix ships, old cache serves wrong output" risk — which *is* the v2 confidence-recompute risk.

**[DECISION]** **(c), refined: a schema-*epoch*-namespaced DB path** — `.atlas/graph.<epoch>.db`, where `<epoch>` is a monotonic integer constant bumped in the same commit that changes schema (key on epoch, **not** marketing/major version, so patch releases sharing a schema don't force needless reindex). Startup auto-sweeps non-current epoch files. Write a redundant `epoch` row in `manifest` as a belt-and-suspenders cross-check (filename epoch ≠ in-DB epoch → treat stale, rebuild). **No ALTER ladder.** Treat the DB as a disposable cache (G-A): all confidence/edge data is computed *fresh* under v2 semantics, never back-filled onto v1 rows. Add a test asserting `<epoch>` matches a hash of the schema DDL (guards "changed schema, forgot to bump epoch").

**[CONSEQUENCE]** Every epoch bump costs a one-time full reindex (can't be `--since`-cheap). New failure mode: forgetting to bump the epoch → old DB silently reused with wrong shape (mitigated by manifest cross-check + DDL-hash test). Cleanly separates the *ephemeral local index* (this decision) from the *portable committed artifact* (D6).

**[REVERSAL CONDITION]** If atlas-aci ever stores **non-derived** data in the DB (user annotations, manually-corrected edges — anything not a pure function of the source tree), the cache premise breaks and a real ALTER ladder becomes necessary. Signal: any write to the DB that isn't reconstructable from source. Also revisit if full-reindex time on the largest supported repo exceeds an operationally tolerable ceiling such that epoch bumps become disruptive.

**Confidence: 0.85** (H). Decided by G-A + the "ALTER back-fills semantically-wrong confidence" insight.

---

## D2 — Bounds enforcement: locus + overflow semantics

**[CONTEXT]** `search_symbol` and `graph_query` call **no** `enforcement.cap_*` helper — unbounded, contradicting the README invariant (Del 3 item 1). `test_dry_run` also byte-slices *around* `enforcement.cap_bytes` (Del 2). So **3/7 tools have cap defects under the current per-tool model** — the failure mode is "a tool forgot." Threat model: the agent is untrusted and may act on silently-truncated results.

**[OPTIONS — (i) locus]** per-tool wrappers (status quo, already failed 3×) vs central dispatch-middleware in `_call_tool` (where `assert_read_only`/`assert_rate_limit` already chokepoint at `server.py:170-171`, and have *not* had this bug because they're central + test-pinned).
**[OPTIONS — (ii) overflow]** silent-truncate (worst — enables the false "complete" conclusion) vs truncate-and-flag (existing pattern for view_file/list_dir/search_text: `overflow`/`next_cursor`/`retry_hint`) vs hard-fail (graphify "fail loud", but graphify's hard caps are on *build ingestion*, not *agent query responses*).

**[DECISION]**
- **(i) Central dispatch-middleware is the mandatory floor.** In `_call_tool`, after the tool returns and before serialization: apply a central **entry-cap to the declared list-valued field** (clean element-boundary truncation, not raw byte-slicing) + a universal **serialized-byte ceiling** as the final validation. Tools expose their truncatable collection via a tiny convention (result-type registry or a `_bounded_field` key) so the gate works without per-verb logic — this also handles `graph_query`'s verb-varying shapes uniformly. Per-tool semantic caps remain *permitted* as an earlier/finer pass but are **demoted from sole line of defense**. This makes "forgot to enforce" structurally impossible for current and future tools.
- **(ii) Truncate-and-flag for normal overflow** — required, un-ignorable top-level `truncated: true` + returned-count + `more_available: true` + `retry_hint: narrower_scope`. Silence is the danger, not truncation; a loud count neutralizes "acts on partial data." **Hard-fail (structured error) reserved for the absolute byte-ceiling backstop** — a single degenerate response that can't be satisfied even truncated (matches graphify's fail-loud, but only for the abusive/degenerate case).

**[CONSEQUENCE]** Central gate must introspect result shape enough to find the truncatable list (light coupling via the convention). Forecloses per-tool bespoke overflow semantics — all tools share one overflow contract (the point). Forecloses the guarantee "any returned set is complete" — every consumer must honor the flag. AMBIGUOUS edges (D4) and hub queries count against these caps.

**[REVERSAL CONDITION]** If a consumer emerges that corrupts on flagged partial results (e.g. computes a set-complement), switch *that path* to hard-fail — signal: a correctness bug traced to a consumer ignoring `truncated`. If the central gate can't bound a response shape it can't introspect, fall back to *mandating* per-tool caps **plus a test asserting every registered tool calls a cap helper** (compile-time "can't forget" instead of runtime).

**Confidence: 0.85** (H). The locus is directly evidence-driven ("forgot" is the literal bug); overflow follows from the untrusted-agent threat model.

---

## D3 — Community detection dependency cost

**[CONTEXT]** graphify: Leiden via `graspologic` (optional, gated `python_version < '3.13'`, **silent** Louvain fallback — graphify Del 3 item 3) / Louvain via networkx base dep. atlas-aci ships `--read-only --cap-drop ALL` minimal container; differentiator is minimal trust surface. **Nobody proposes Leiden.** Note G-B: communities need a *materialized edge set* that v2 is creating for the first time — degree-centrality god nodes need only edge *counts* (trivially correct on a young edge set); clustering needs *trustworthy* edges.

**[OPTIONS]** (a) networkx Louvain (proven, seeded-deterministic, but a dep contrary to the minimal-trust brand — though modest against the already-shipped compiled tree-sitter-language-pack); (b) hand-rolled deterministic label-propagation (~150 LOC, zero dep, we own correctness; lower community quality than Louvain but *advisory* output tolerates it); (c) god-nodes-only, defer communities (conflicts with the maintainer's "communities in scope for v2.0.0" — so treated as *ordering* insight, not a cut).

**[DECISION]** **God nodes via degree centrality (trivial, zero-dep) ship first** as the always-correct structural signal over the v2 edge table; **communities via hand-rolled deterministic label propagation (option b), zero new runtime dependency, update order + tie-breaking pinned to a total order.** **Reject (a) networkx** and **reject any optional-dep gating** (graphify's silent-cliff footgun, the scout's explicit avoid-lesson). Rationale: communities are the *lowest-stakes, most-advisory* of the four capability groups — the worst place to spend the minimal-trust-surface budget; LPA's quality deficit is acceptable because a wrong community label misleads a hint, it never breaches a bound or the sandbox; and zero-dep sidesteps the entire optional-dep failure class the scout warned against. Within the release: god nodes land with the edge table; communities land **after** the edge table is validated (sequenced, not cut).

**[CONSEQUENCE]** We own community correctness forever; LPA gives lower-quality/less-stable clusters than Louvain (tolerable — advisory). The determinism burden is ours: LPA **must** be totally-ordered or D6's byte-identical export breaks (D6 constrains D3). Forecloses "just use the library everyone trusts."

**[REVERSAL CONDITION]** If LPA community quality is insufficient for navigation (signal: agents/users report clusters that don't match real subsystems, or modularity on the reference Rails repo falls below a usefulness threshold), adopt networkx-Louvain **as a required base dep** (never optional-gated). Also flip if LPA correctness-maintenance cost exceeds the dependency cost, or if atlas-aci takes networkx for another reason anyway (then Louvain is free and (b)'s advantage evaporates).

**Confidence: 0.65 — BELOW 0.7, FLAGGED.** Softest decision. Raise it with: (1) a measured LPA-vs-Louvain modularity comparison on the reference Rails repo — within a small delta → confidence in (b) rises sharply; far worse → flip to (a); (2) the actual image-size delta of adding networkx to the current container (if negligible vs tree-sitter, the footprint argument weakens); (3) an explicit maintainer weight on brand-minimalism vs feature-quality — the call hinges on a weight I inferred from the differentiator emphasis.

---

## D4 — Deterministic `AMBIGUOUS` + full confidence enum

**[CONTEXT]** graphify's `AMBIGUOUS` is LLM-only (zero deterministic producer; `llm.py:416`). Its *code* resolvers use a god-node guard: resolve only when **exactly one** candidate, else **drop the edge** (multi-candidate refs silently lost — graphify claim 1/2). Maintainer already rejected AMBIGUOUS-as-LLM. Question: deterministic AMBIGUOUS-with-candidates vs silent drop — help or invite hallucination? Give the full enum + each value's sole deterministic producer.

**[OPTIONS]** (a) port graphify's silent drop (higher precision, but a *false negative* the agent can't detect — "nothing here" is a confident wrong answer); (b) deterministic AMBIGUOUS with candidate set attached (strictly more info than dropping; fits zero-LLM; same "mark incompleteness loudly" fix as D2's truncate-and-flag); (c) AMBIGUOUS as an opt-in-only tier.

**[DECISION]** **Deterministic 3-value enum, partitioned solely on candidate count + syntactic qualification, no LLM producer for any value:**
- **EXTRACTED** ← sole producer: **exactly one** candidate AND the reference is **syntactically type-qualified** at the site (`Foo::bar`, explicit receiver type). Highest trust. (mirrors graphify's `type_qualified` true)
- **INFERRED** ← sole producer: **exactly one** candidate via **heuristic** — name-uniqueness or receiver-type inferred from a local assignment table — *not* type-qualified. (graphify's `type_qualified` false)
- **AMBIGUOUS** ← sole producer: **candidate_count > 1** (the god-node guard *fires*). Emit the edge tagged AMBIGUOUS **with the full `candidates[]` attached**, never dropped, never silent.

The partition key is a single deterministic quantity: candidate count (0 → **not in the enum**: stays an unresolved ref / no edge, preserving G-B's name-string model; 1 → EXTRACTED/INFERRED by qualification; >1 → AMBIGUOUS). The tag is a **required, un-ignorable field**; AMBIGUOUS edges carry `candidates[]` and are ordered/segregated so a consumer cannot mistake one for a resolved single target. The parent's hallucination worry is answered by *how* you surface (attached candidates + required tag + de-emphasis), not by *whether* — hallucination comes from *un-tagged* uncertainty; AMBIGUOUS is loudly-tagged uncertainty, the opposite. Replaces graphify's silent drop: false-negatives (silent) are worse than flagged uncertainty for an untrusted-agent navigation tool.

**[CONSEQUENCE]** More edges than graphify's drop (incl. uncertain ones) → interacts with D2 (an AMBIGUOUS query on a hub like `save` can be large → truncate-and-flag applies). Schema must store candidate sets (ordered, for D6 determinism). Forecloses the "clean high-precision graph" aesthetic — atlas-aci trades precision-by-omission for honesty-by-annotation. Commits consumers to always honoring the confidence field.

**[REVERSAL CONDITION]** If real usage shows AMBIGUOUS edges are either *ignored into noise* (agents filter them wholesale — they cost bytes for no use) → revert toward the silent guard or an opt-in tier; or *over-trusted into hallucination despite the tag* → add a hard barrier (explicit opt-in flag required to receive AMBIGUOUS edges). Signal: telemetry on AMBIGUOUS returned-vs-acted-upon, or a hallucination incident traced to an AMBIGUOUS edge.

**Confidence: 0.85** (H). Clean deterministic partition anchored to graphify's `type_qualified`/god-node-guard; parent pre-endorsed the direction.

---

## D5 — Rationale-node language scope + the silent-dead-language bug

**[CONTEXT]** graphify does rationale for **Python + JS/TS only** (despite 46 languages — graphify claim 6), signalling per-language cost + incremental shipping. atlas-aci has 9 tree-sitter query languages + 4 (`.tsx/.go/.rs/.java`) recognized in `LANG_BY_EXT` but with **no `QUERIES`** → silently index to nothing, zero test coverage (Del 2, Del 3 item 12). Rails is the stated target. Two separable questions.

**[OPTIONS — Q1 rationale scope]** (a) all 9 (more than graphify attempted; 4-5 are markup/config where rationale-comments are marginal); (b) Ruby + Python first (Rails = Ruby is must-have; Python + JS/TS directly portable from graphify incl. JS/TS's ADR/RFC regex `extract.py:1087`); (c) block rationale on first fixing the 4 dead languages.
**[OPTIONS — Q2 dead-language bug]** in scope vs not; fix-by-adding-queries vs fix-by-honesty.

**[DECISION]**
- **Q1: refined (b) — rationale for the CODE languages only, ordered Ruby → Python → JS/TS** by target-value + portability. Ruby (target, must-have, written fresh — graphify has no Ruby rationale to port). Python + JS/TS (directly portable from graphify; JS/TS brings the high-value ADR/RFC promotion). **Exclude scss/html/yaml/markdown/bash** (rationale is a code-decision concept; marginal on markup/config). **Reject (c)'s blocking framing** — rationale nodes come from comment scanning, independent of the call/inheritance edge graph (graphify even *excludes* rationale from cross-file resolution), so no dependency on the dead-language bug. The cut line may fall after Ruby+Python if effort budget is tight; JS/TS is the stretch. ADR/RFC in Ruby/Python is fresh work, lower priority than the JS/TS port.
- **Q2: the silent-dead-language bug IS in scope for v2.0.0 — as a *hardening honesty fix*, not a language-coverage commitment.** Align `LANG_BY_EXT` with `QUERIES` (drop or document the 4 unbacked extensions) + a consistency test (`set(LANG_BY_EXT.values()) ⊆ set(QUERIES) ∪ explicit_unsupported`) + a **visible** "unsupported extension skipped: N files" report. Never silently no-op on a recognized extension. This is the *same anti-pattern* as D2 silent-truncate and D4 silent-drop → belongs in the hardening phase. It does **not** commit v2.0.0 to real Go/Rust/Java/.tsx symbol queries (separate coverage work, out of scope unless separately budgeted).

**[CONSEQUENCE]** Rationale coverage is deliberately partial (code langs) — Rails YAML config comments get no rationale nodes. The honesty fix leaves `.go/.rs/.java` *un-indexed but visibly so*. Forecloses the implicit claim "atlas-aci indexes everything `LANG_BY_EXT` lists."

**[REVERSAL CONDITION]** If the user base shifts polyglot/Go/Rust-heavy (signal: telemetry or user reports of `.go/.rs` repos), upgrade the honesty fix to real query coverage and extend rationale to those langs. If ADR/RFC-in-Ruby/Python proves high-value (signal: Rails shops heavily using ADR comment refs), promote Ruby/Python ADR extraction to must-have.

**Confidence: 0.80** (Q2 high — clear bug, cheap thesis-aligned fix; Q1 moderate — logic sound but the exact cut line is effort-budget-dependent, which I can't measure).

---

## D6 — Portable export + merge

**[CONTEXT]** Design the committable artifact. graphify: `graph.json` (NetworkX node-link, relative-path keys re-anchored on load `README.md:423`, hard-capped 512 MiB). graphify's advertised *auto-configured merge driver* **does not exist in its code** (scout claim 7) — we'd build, not port; and its *actual* merge is a naive `nx.compose` **union** (base loaded, not diffed). Requirements: byte-identical output for identical input (clean git diff), merge under concurrent updates, cold-start cost, Rails-scale size. G-A: the artifact is derived (regeneration is always a valid resolver).

**[OPTIONS — Q1 format]** SQL dump (NOT byte-deterministic — rowids/page layout vary; binary-ish; poor diff → **reject**); single JSON blob (deterministic *if* fully sorted, but whole-file peak memory + coarse diff + the 512 MiB cliff → **reject**); normalized sorted JSONL.
**[OPTIONS — Q2 merge]** ship a merge driver vs deterministic export only.

**[DECISION]**
- **Q1: normalized, sorted, canonical JSONL** — one record per line (symbols / edges / rationale / communities, via a `type` discriminator or separate streams), **total-order sort**, **canonical per-line JSON** (sorted keys, no insignificant whitespace, fixed float formatting, LF), **relative-path keys** (graphify's re-anchor insight — mandatory for portability), plus a header record with schema-epoch (D1) + content hash. Byte-deterministic **by construction**. Wins on all four axes: a changed edge → one changed line (clean diff + git auto-merge); streams line-by-line (low peak memory, no 512 MiB cliff); text (git-friendly, not binary); compresses well. **Byte-determinism is a hard requirement that constrains all upstream ID/order assignment** — communities (D3 LPA), edges, and AMBIGUOUS `candidates[]` (D4) must all be totally-ordered.
- **Q2: deterministic export + import ONLY — NO semantic/union merge driver in v2.0.0.** The JSONL choice makes git's *default* line merge handle non-conflicting concurrent edits for free. For a *real* same-record conflict, the correct resolution is **regenerate-from-source** (G-A: the artifact is a pure function of source). **Explicitly reject graphify's union merge** — it can synthesize a phantom graph corresponding to *no actual source state* (edges from branch A ∪ branch B), which an untrusted agent then navigates: a thesis violation. Ship instead: deterministic export + idempotent import (with a cold-start integrity check that the import reproduces a valid DB) + a documented "on conflict, re-run `atlas index`" workflow. Optionally a *trivial* regenerate-on-conflict git driver (~5 lines: ignore both sides, re-run indexer — deterministic, always-correct), but NOT a graph merge.

**[CONSEQUENCE]** The committed artifact is text (larger than a binary DB, but git-friendly). Forces total-order pinning across D3/D4/edges (a nondeterministic community algorithm would break export byte-identity — the tightest cross-decision coupling). Forecloses shipping the DB itself as the artifact (DB stays local/ephemeral per D1). Forecloses a "smart" 3-way merge — same-record conflicts require regeneration (fine for derived data; and we argued the magic merge is dangerous anyway).

**[REVERSAL CONDITION]** If regeneration is too slow to be an acceptable conflict resolver on the largest repos (signal: reindex time makes "re-run on conflict" operationally painful), add a *record-level* (not union) driver that re-derives only the conflicting records' source files. If Rails-scale JSONL exceeds a git-practical ceiling (signal: export > ~100 MB degrading git ops), add compression/sharding. If cross-platform byte-determinism can't be met (signal: same source → different bytes on macOS vs Linux via float/path formatting), that's a **release blocker to fix before shipping export**, not a reversal.

**Confidence: 0.80** (H). JSONL-for-determinism-and-diff is well-established; merge-driver-out-of-scope is strongly supported (graphify's is vaporware + union is phantom-graph-dangerous + derived-data-regeneration is always correct). Residual: exact JSONL schema + cross-platform canonicalization need spec-level pinning; Rails-scale JSONL assumed git-practical but unmeasured.

---

## Cross-cutting: "harden first, then augment" — GATED, not merely ordered

**[DECISION] GATED, and the gate is mechanical.** The threat model decides it: hardening (D2 central bounds, D1 epoch substrate, D5-Q2 honesty fixes) closes *existing* holes; augmentation *adds new surface* (new tools, response shapes, tables). If features merge onto an *unhardened* base, they reproduce the exact bug class we're fixing — a new `communities`/`subclasses_of` verb under the current per-tool-forgetful model would, like `search_symbol`/`graph_query`, likely *also* forget to cap. But if the **central** bounds gate (D2-i) lands and is verified *first*, every later feature is *automatically* bounded — hardening becomes the substrate features are built on, and "forgetting" becomes structurally impossible.

**What mechanically enforces the gate — three things must be green before any augmentation merges:**
1. **CI that actually runs the tests on every PR.** The scout's linchpin finding (Del 3 item 2): `.github/workflows/` has *only* `release.yml` (tag-triggered build/sign/publish) — **no pytest/ruff/mypy on PRs**. Without CI-runs-tests, "verified" is unenforceable and the harden-gate is fiction. **Establishing CI is therefore the first hardening deliverable** — it is *what makes "harden first" mechanically real rather than aspirational.**
2. **Central bounds enforcement (D2-i) verified**, plus a test asserting *every registered tool's response passes through the central cap* — so a feature PR adding an unbounded tool fails CI.
3. **The schema-epoch substrate (D1) landed** — every feature defines new tables/columns *relative to the v2 epoch*, so the substrate is a compile-time dependency of the feature schemas.

Merely ordering (features after hardening, same release, no gate) fails because nothing stops a feature PR from landing early and regressing the bound; the CI gate is what converts "ordered" into "gated."

**Confidence: 0.85** (H). The "no CI" finding is the linchpin and is directly evidence-anchored.

---

## Sequencing constraint graph

Workstreams:
- **H0** — Establish CI (pytest/ruff/mypy on every PR). *(currently absent — Del 3 item 2)*
- **H1** — Central bounds chokepoint in `_call_tool` (element-cap on declared list field + byte-ceiling + truncate-and-flag contract). *(D2-i/ii)*
- **H2** — Route `search_symbol` + `graph_query` through H1 + test "every tool is bounded." *(D2 fix)*
- **H3** — Schema-epoch-namespaced DB path + auto-sweep + manifest cross-check + DDL-hash test. *(D1)*
- **H4** — Silent-dead-language honesty fix (LANG_BY_EXT↔QUERIES consistency + test + visible skip-report). *(D5-Q2)*
- **A1** — Materialize real edge table + deterministic confidence enum {EXTRACTED/INFERRED/AMBIGUOUS} + candidate sets; implement `subclasses_of` via real inheritance edges (retire the stub). *(D4 + inheritance)*
- **A2** — Degree-centrality god nodes over the edge table (trivial, zero-dep). *(D3)*
- **A3** — Hand-rolled deterministic LPA communities (pinned total order). *(D3)*
- **A4** — Rationale nodes, Ruby → Python → JS/TS. *(D5-Q1)*
- **A5** — Deterministic sorted-JSONL export + import (no merge driver). *(D6)*

Edges (X → Y = X must land before Y; reason):

```
H0 ──▶ (everything)      CI must exist or "verified" is fiction; enforces the harden-gate
H1 ──▶ H2                the chokepoint must exist before the two unbounded tools route through it
{H0,H1,H2} ══▶ A1..A5    THE HARDEN-GATE: no feature merges until central bound + its CI test are green
H3 ──▶ A1,A2,A3,A4,A5    epoch substrate must exist before any feature defines v2 tables/columns (D1 gates all)
A1 ──▶ A2                god nodes need the materialized edge table (G-B: no real edges today) — degree = edge count
A1 ──▶ A3                LPA needs a trustworthy edge set
A2 ──▶ A3 (ordered)      god nodes ship first (trivially correct on a young edge set); communities after edges validated
A1 ──▶ A5                export must serialize the final edge/confidence shape
A3 ──▶ A5 (constraint)   D6 byte-determinism FORCES A3's community IDs to a total order — tightest coupling
A4 ──▶ A5                export must serialize the final rationale shape
H4  ∥  (augmentation)    independent correctness fix; NOT a blocker for A4 (rejecting D5-c's blocking framing)
A4  ∥  (A1/A2/A3)        rationale = comment scan, independent of the edge graph — runs parallel after H3+gate
```

**Critical path:** `H0 → H1 → H2 → [GATE] → H3 → A1 → A3 → A5`, with **A2** hanging off A1 early, **A4** parallel after H3+gate, **H4** parallel within hardening.
**Freeze order for the artifact contract:** A5 (export) comes last among features — it freezes the on-disk contract only after A1 (edges/confidence), A3 (communities, total-ordered), A4 (rationale) stop moving.

---

## Confidence summary & flags

| Decision | Confidence | Flag |
|---|---|---|
| D1 schema-epoch cache | 0.85 | — |
| D2 central bounds + truncate-and-flag | 0.85 | — |
| **D3 zero-dep LPA + degree god-nodes** | **0.65** | **< 0.7 — see below** |
| D4 deterministic AMBIGUOUS enum | 0.85 | — |
| D5 rationale code-langs + honesty fix | 0.80 | Q1 cut line is effort-budget-dependent |
| D6 sorted-JSONL, no merge driver | 0.80 | cross-platform determinism must be spec-pinned (release blocker if unmet) |
| Cross-cutting: GATED via CI | 0.85 | — |

**D3 is the one sub-0.7 decision.** Evidence that would raise it: (1) measured LPA-vs-Louvain modularity on the reference Rails repo; (2) actual image-size delta of adding networkx to the current `--cap-drop ALL` container; (3) an explicit maintainer weight on brand-minimalism vs community quality — the call hinges on a weight I inferred, not one I was given.

## Handoffs
- **→ Planner (RAMZA, default per roster):** the sequencing graph is a ready spine for a phased v2.0.0 plan (Phase 0 = H0..H4 harden-gate; Phase 1 = A1+A2; Phase 2 = A3+A4; Phase 3 = A5). D1 and the harden-gate are the two hard sequencing invariants to encode as gate criteria.
- **→ ATLAS (evidence gap for D3):** an LPA-vs-Louvain modularity probe + a networkx image-size delta on the reference Rails repo would lift the only sub-0.7 decision above threshold before A3 begins.
- **requires_checker:** false (design decisions feeding planning; no irreversible/deploy/destroy/spend/public action).
```
