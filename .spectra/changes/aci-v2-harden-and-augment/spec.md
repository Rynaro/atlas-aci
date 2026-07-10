---
artifact: ramza-spec
plan: aci-v2-harden-and-augment
target: atlas-aci v2.0.0
esl_change: aci-v2-harden-and-augment
esl_tier: full
maker: vivi
checker: vigil
base_repo: <repo>
base_head: f56a78e
base_tag: v0.4.0
created_at: 2026-07-09T18:26:43Z
amended: refine cycle 1 (F1-F18 + DIR-1/2/3) then amendment 2 (FORGE D3a/D4a by-ID list + AC-H-18 truncate-iff-withheld + SCOPE-1/SCOPE-2; 68 -> 71 criteria; V1 resolved to two pinned SHAs)
checker_verdict: GO-WITH-CONDITIONS (vigil)
---

# atlas-aci v2.0.0 — Harden-and-Augment Spec (amended)

> Encodes FORGE decisions D1–D6 and the GATED harden-first constraint verbatim, amended per
> vigil's independent checker critique (`checker-critique.md`) and the three settled orchestrator
> directives. These decisions are **settled**; this spec makes them implementable without
> re-derivation. Every scope claim traces to a scout `file:line` anchor or a `D<n>` decision.

## Problem Statement

atlas-aci's thesis is **zero-LLM, mechanically-bounded, fully-local, agent-is-untrusted**
(the "Mechanical bounds" invariant at `README.md:411-413`; deliberation G-A/G-B). v0.4.0
(HEAD `f56a78e`) violates that thesis in three structural ways and lacks four capabilities the
reference project (graphify) proves are achievable LLM-free:

1. **The bound is a lie for 2 of 7 tools.** `search_symbol` and `graph_query` call **no**
   `enforcement.cap_*` helper (`tools/search_symbol.py:30-41`, `tools/graph_query.py:29-37` call
   only `enforcement.record`); `search_symbol`'s `definitions` (`codegraph.py:408-414`) and
   `callers_of` edges (`codegraph.py:425-431`) are unbounded, contradicting the invariant at
   `README.md:411-413`. The failure mode is "a tool forgot" — the per-tool model failed 3×
   (`test_dry_run` also byte-slices around `enforcement.cap_bytes`, `test_dry_run.py:88-91`).
2. **Nothing runs the tests.** `.github/workflows/` contains only `release.yml`; no
   `pytest`/`ruff`/`mypy` on PRs (scout Del 3 item 2). "Verified" is currently unenforceable.
3. **The DB has no migration story.** `manifest.version` is written (`codegraph.py:321`, always
   `'1'`) but never read; additive `CREATE TABLE IF NOT EXISTS` tolerates new tables but not
   *altering* `refs`/`symbols` (scout Del 3 item 7).

Missing capabilities (all proven LLM-free in graphify — `scout-graphify.md` Deliverable 2):
edge confidence tagging (claim 1), real inheritance edges / `subclasses_of` (stub,
`codegraph.py:454-460`), community + god-node detection (claim 5), rationale nodes (claim 6), a
portable committable graph artifact (claim 8). G-B: atlas-aci has **no materialized edge set yet**
(`refs.callee_name` is a bare string with `enclosing` hardcoded `None`, matched by name at query
time, `codegraph.py:391-397,408-431`) — v2 creates the first one; vigil verified G-B against
source, so the A1→A2→A3→A5 critical path stands.

A release whose thesis is **mechanical honesty** cannot ship doc claims that are untrue. Eight such
claims are in scope (see `## Doc-Honesty Decisions`), each resolved by **building the feature** or
**correcting the doc** — and, per vigil F11-F13, each doc correction is a **repo-wide** grep-zero,
not a single-line fix, so the same untruth cannot survive in a sibling doc.

## Scope

**In scope (v2.0.0):**
- Hardening gate: CI (H0), central bounds chokepoint (H1) + routing + every-tool-bounded test
  (H2), schema-epoch DB substrate (H3), silent-dead-language honesty fix (H4). (D1, D2, D5-Q2,
  cross-cutting gate.)
- Augmentation: materialized call/inheritance edge table + deterministic confidence enum +
  `subclasses_of` (A1, D4 + inheritance); degree-centrality god nodes (A2, D3); hand-rolled
  deterministic LPA communities (A3, D3, **conditional on the D3 probe** — see D3 below); rationale
  nodes Ruby→Python→JS/TS (A4, D5-Q1); deterministic sorted-JSONL export + import (A5, D6).
- Doc-honesty batch: the eight scout-surfaced untrue claims, resolved repo-wide per item.

**Out of scope (explicit non-goals):**
- The `~/.agents/skills/` cross-framework install — belongs to `Rynaro/ATLAS`.
- **Any LLM call, anywhere.** `AMBIGUOUS` is a deterministic value, never an LLM output (D4).
- Making `--since` genuinely git-ref-aware — the honesty fix **corrects the docs repo-wide**.
- A Prism-based Ruby specialist mode — v2 **strips the vaporware references**, does not build it.
- A canary host-dispatcher implementation — v2 **corrects the README/SETUP claims** and adds a
  visible deferred note to `scripts/run-canaries.py`; the dispatcher (`run-canaries.py:334`) stays
  deferred.
- Real Go/Rust/Java/.tsx symbol queries — D5-Q2 is an *honesty* fix, not a coverage commitment.
- Wiring memex refs into graph responses — v2 corrects the false manifest description
  (`server.py:139`); emission is deferred.
- **Any networkx / graspologic / optional-dep-gated algorithm — absolute (DIR-1).** A below-threshold
  D3 probe **cuts A3 to v2.1**; it never adds networkx or Louvain.
- A semantic/union graph **merge driver** (D6-Q2). A *trivial* regenerate-on-conflict git driver
  is permitted (AC-A5-5).

**Deferred (named, with reversal signal):** git-aware `--since`; canary dispatcher; polyglot symbol
coverage; memex-ref emission; A3 LPA communities if the probe fails (→ v2.1); record-level merge
driver (D6 reversal); compression/sharding if Rails-scale JSONL exceeds 100 MB (D6 reversal).

## Approach

### Cross-cutting invariant — GATED harden-first, enforced by a committed workflow (DIR-3 / F3)

**No augmentation code merges until three things are green:**
1. **CI runs the test suite on every PR** (H0). Without it "verified" is fiction.
2. **Central bounds chokepoint (H1) verified + a test that every registered tool/verb with a
   list-valued field declares a non-empty `_bounded_field` and truncates+flags an over-cap
   response** (H2/AC-H-4/AC-H-15 — F2: a no-op cap must not pass).
3. **The schema-epoch substrate landed** (H3).

The gate's **teeth are in-tree**: a committed workflow `.github/workflows/harden-gate.yml` (DIR-3)
**fails the build when any augmentation A1–A5 path is modified while the hardening checks are absent
or failing** (AC-NEG-6). This is the mechanism — reviewable in the diff. GitHub branch protection /
required status checks are **defence-in-depth only** (documented, out-of-tree, un-versioned; the
acceptance suite cannot assert settings — F3). AC-H-1/AC-H-2/AC-REL-1 therefore assert only what is
in-tree-verifiable (the workflow runs the checks on `pull_request` and produces a failing check
run); "blocks merge" is delegated to `harden-gate.yml` + operator config.

### D1 — Schema-epoch-namespaced DB substrate (H3)

- DB path becomes `.atlas/graph.<epoch>.db` where `<epoch>` is a **monotonic integer constant**
  bumped in the same commit that changes schema (keyed on epoch, not marketing version). Replaces
  bare `.atlas/graph.db` (`codegraph.py:207`).
- **F1 (epoch vs DDL-hash reconciled):** the epoch is an **integer**; separately, a committed
  `EXPECTED_DDL_HASH` constant is **paired** with the epoch and must equal `hash(current DDL)`.
  Changing the DDL without bumping the epoch and its recorded hash fails CI (AC-H-12). The filename
  stays `.atlas/graph.<integer>.db` (AC-H-9).
- **No `ALTER TABLE` ladder.** The DB is pure derived data (G-A). Confidence is computed **fresh**
  under v2 semantics, never back-filled (D1 rejects option a).
- **F16 / DIR-2 — sweep and rebuild are `index`-path only.** Auto-sweep of non-current-epoch
  `.atlas/graph.*.db` files and rebuild-on-mismatch happen **exclusively in the `index` (write)
  command**. **`serve` never mutates `.atlas`**: on epoch match it starts read-only; on mismatch it
  **fails fast with a structured `ToolError` naming the required `index` command**, performing zero
  writes/unlinks (AC-H-10/AC-H-11/AC-H-16). This reconciles with the shipped `--read-only`/`:ro`
  serve deployment (`README.md:201-210`, `Dockerfile:12`).
- **Implementation coupling (vigil C3):** H3's rename breaks the hardcoded existence checks
  `(config.repo / ".atlas" / "graph.db").exists()` at `search_symbol.py:23` and `graph_query.py:22`
  — both must move to the epoch path; the mismatch is exactly how `serve` fails fast.
- **F17 — concurrency:** the `index` build writes to a temporary path then **atomically renames**
  under a **single-writer lock**, so the documented background post-commit hook
  (`INTEGRATION.md:207-208`) cannot corrupt the DB via concurrent runs (`codegraph.py:214` uses
  default locking) — AC-H-17.
- A redundant `epoch` row in `manifest` is the belt-and-suspenders cross-check.
- The **rationale relation** DDL is part of this substrate (F9 — see D5), so A4 depends only on H3.

### D2 — Central bounds chokepoint + overflow semantics (H1/H2)

- **Locus (mandatory floor):** central dispatch-middleware in `_call_tool` (where
  `assert_read_only`/`assert_rate_limit` already chokepoint at `server.py:170-171`). After the tool
  returns and before serialization: apply a central **entry-cap to the declared list-valued field**
  (clean element-boundary truncation, never raw byte-slicing) + a universal **serialized-byte
  ceiling**. Tools expose their truncatable collection via a `_bounded_field` convention.
- **F2 — the gate must not be no-op-satisfiable.** AC-H-4 feeds each tool/verb a synthetic over-cap
  response and asserts truncate+flag; AC-H-15 asserts **every list-returning tool/verb registers a
  non-empty `_bounded_field`** (D2's own reversal prescription, `deliberation.md:48`). AC-H-7
  (search_symbol) and AC-H-8 (graph_query) are promoted into the P0 `gate_criteria`, so the gate
  cannot be green while either is unbounded.
- **Overflow (truncate-and-flag):** un-ignorable top-level `truncated:true` + returned count +
  `more_available:true` + `retry_hint:narrower_scope`. **Hard-fail reserved for the absolute
  byte-ceiling backstop** (AC-H-6).

### D4 — Deterministic 3-value confidence enum + real edges (A1)

Materialize the first real call/inheritance edge set (G-B). Partition confidence on a **single
deterministic quantity — candidate count** — with **no LLM producer for any value**:
- **EXTRACTED** — exactly one candidate AND syntactically **type-qualified** at the site.
- **INFERRED** — exactly one candidate via heuristic (name-uniqueness / local assignment table),
  not type-qualified.
- **AMBIGUOUS** — candidate_count > 1; emit the edge with the full **ordered** `candidates[]`,
  never dropped, never silent (replaces graphify's silent drop).
- **Zero candidates → not in the enum**: unresolved ref / no edge (preserves G-B's name-string
  model).

**F18 — per-language "type-qualified" rule pinned (atlas-aci's languages are dynamically typed;
graphify's `type_qualified` came from typed languages):**
- **Ruby:** a constant receiver or `::` scope resolution (`Foo::bar`, `Foo.new`, `Foo.bar` where
  `Foo` is a constant) = type-qualified → EXTRACTED; a local-variable receiver resolved via the
  local assignment table (`obj.bar`, `self.bar`) = INFERRED; a bare method with a unique name =
  INFERRED via name-uniqueness.
- **Python:** `Foo.bar` / `Foo()` where `Foo` is a class-bound name = qualified → EXTRACTED;
  `self.bar` / `obj.bar` via local inference = INFERRED.
- **JS/TS:** `Foo.bar` / `new Foo()` where `Foo` is a class or imported binding = qualified →
  EXTRACTED; `this.bar` / `obj.bar` via local inference = INFERRED.
Per-language fixtures decide EXTRACTED vs INFERRED accordingly (AC-A1-3/4/11).

- `candidates[]` is **totally ordered**, same-machine testable (AC-A1-8) — D6 foundation.
- `subclasses_of` retires its stub (`codegraph.py:454-460`): v2 adds superclass/heritage capture to
  the Ruby/Python/JS/TS `QUERIES` and answers from real inheritance edges (AC-A1-7).
- **F10 — `refs.enclosing` is DROPPED.** The legacy always-NULL column (`codegraph.py:391-397`) is
  omitted from the fresh v2 CREATE TABLE (no ALTER needed). Caller context is carried by the
  materialized **edge source endpoint**; `callers_of`'s response shape changes accordingly and the
  DSL doc is updated (AC-A1-9/AC-A1-10).

### D3 - God nodes then LPA communities; networkx absolutely rejected (A2, A3) - DIR-1 / D3a / D4a

- **A2 - god nodes** via degree centrality over the **confident subgraph** (EXTRACTED union INFERRED;
  AMBIGUOUS excluded from degree - D4a) of the v2 edge table (mirrors graphify `analyze.py:100-121`).
  Trivially correct on a young edge set, zero new dependency, ships first.
- **A3 - communities** via **hand-rolled deterministic label propagation** (~150 LOC, zero new
  runtime dependency) over the same **confident subgraph** (AMBIGUOUS never fanned out - D4a), update
  order + tie-breaking pinned to a **total order** (D6 forces this).
- **D4a - AMBIGUOUS excluded from the analysis graph, still returned by `graph_query`.** The analysis
  graph feeding A2/A3/the probe is the confident subgraph; `graph_query` (`callers_of`,
  `subclasses_of`, ...) still returns **all** matching edges including AMBIGUOUS with `candidates[]`
  (AC-A1-5 intact). The divergence is made visible: `god_nodes`/`communities` responses carry
  `analysis_basis:"confident_edges"`, `ambiguous_edges_excluded:N`, `resolved_edge_count` (AC-A2-3),
  the DSL doc documents it (AC-A1-10), and AC-NEG-7 forbids ambiguity-as-importance (no fan-out, no
  fractional weight). AC-A1-2/AC-A1-5 are preserved - an analysis-time filter, not a drop.
- **HARD PRECONDITION on A3 (D3 flagged 0.65):** before A3 code merges, run the **evidence probe** -
  LPA-vs-Louvain **modularity comparison** on **both** pinned reference repos - and write the verdict
  to `probe-lpa-vs-louvain.md`. The ordering is **mechanical**: `harden-gate.yml` fails if an A3 path
  changes in a commit lacking the probe artefact (AC-A3-1/F7).
- **D3a - the pre-registered pass rule (resolves [VERIFY] V1, F5), frozen BEFORE the probe runs
  (anti-circularity - the probe never derives the bar it is graded against).** Constants:
  `Q_struct = 0.30` (Newman structure floor, only to reject a non-discriminating graph),
  `R = 0.85` (maintainer-confirmed midpoint of the principled band [0.80, 0.90]; 0.95 upper =
  modularity-degeneracy plateau, Good-de Montjoye-Clauset 2010; 0.80 lower = LPA leaving >20% of
  provable structure unfound). Louvain baseline = **median** of `K=10` networkx `louvain_communities`
  runs at **seeds 0..9**, **gamma=1.0**; LPA = the single deterministic run of the *shipped* impl
  (no seed); both over the confident subgraph as an **undirected unweighted projection**. Pass per
  repo iff `Louvain_Q_median >= 0.30` AND `LPA_Q >= 0.30` AND `LPA_Q >= 0.85*Louvain_Q_median`,
  evaluated **independently on Solidus (`4026945d...`) and Spree (`6699cde4...`), never averaged**.
- **DIR-1 - the decision branch (networkx flip DELETED):** `AC-NEG-2` (no networkx) is **absolute and
  unconditional**. If **either** pinned repo fails **any** clause, **A3 is cut from v2.0.0 and
  deferred to v2.1; v2.0.0 ships A2 god nodes only** - it does **not** adopt Louvain/networkx.
  Mechanical cut-branch: the release fails if any A3 code exists while the probe verdict is not PASS
  on both repos (AC-A3-4/AC-A3-5). **The probe runs networkx only in an ephemeral env (uvx/throwaway
  venv), never in `pyproject.toml`/`uv.lock` - that is what keeps AC-NEG-2 intact (AC-A3-1 note).**

### D5 — Rationale scope + dead-language honesty (A4, H4)

- **A4 — rationale nodes for CODE languages only, Ruby → Python → JS/TS** (D5-Q1). Ruby fresh;
  Python + JS/TS ported from graphify (`extract.py:914,938-1164`) incl. JS/TS **ADR/RFC promotion**
  (`extract.py:1087`). Exclude scss/html/yaml/markdown/bash.
- **F9 — rationale edge store (C6 softened):** A4 is independent of edge **resolution** (comment
  scan), but its `rationale_for` edges need an edge **store**. That store is a **separate rationale
  relation** whose DDL lives in the **H3 epoch substrate**, **not** the A1 call/inheritance edge
  table. `rationale_for` edges carry **no** confidence enum value; `AC-A1-2` therefore scopes to
  call/inheritance edges only (AC-A4-6). So A4 depends only on H3 (not A1), preserving the
  parallel-after-gate sequencing.
- **H4 — silent-dead-language honesty fix** (D5-Q2): `.tsx/.go/.rs/.java` in `LANG_BY_EXT`
  (`codegraph.py:41-44`) with no `QUERIES` entry silently index to nothing (`codegraph.py:264`).
  Fix: `set(LANG_BY_EXT.values()) ⊆ set(QUERIES) ∪ explicit_unsupported` + a **visible** skip
  report (AC-H-13/AC-H-14). Not a coverage commitment.

### D6 — Portable export + import, no merge driver (A5)

- **Format: normalized, sorted, canonical JSONL** — one record per line, **canonical per-line JSON**
  (sorted keys, no insignificant whitespace, fixed float formatting, LF), **relative-path keys**
  re-anchored on load, plus a **header record** carrying schema-epoch + content hash. Byte-
  deterministic by construction.
- **F8 — record-level total order has same-machine teeth.** Beyond in-record key sorting (AC-A5-1),
  the exporter emits **records in an explicit canonical content order** (`ORDER BY type, path, line,
  name, …`) independent of rowid/insertion order (AC-A5-7), and **`_iter_source_files` is sorted**,
  not FS-order `rglob` (AC-A5-8, fixing `codegraph.py:336`). A developer who never read D6 fails a
  **same-machine** check long before the cross-OS gate — the cross-OS check (AC-REL-1) is not the
  first line of defence.
- **No semantic/union merge driver** (D6-Q2). Ship deterministic export + **idempotent import** +
  a documented "on conflict, re-run `atlas index`" workflow. A *trivial* regenerate-on-conflict git
  driver is permitted (AC-A5-5 narrowed to forbid only a **graph/union** driver).

## Migration and Upgrade

Existing users hold a v1 `.atlas/graph.db` (`codegraph.py:207`). Upgrade is **automatic and
lossless-by-regeneration** because the DB is pure derived data (G-A), and — per DIR-2 — **all
mutation happens on the `index` (writable) path, never on `serve`:**
1. Running `atlas-aci index` under a **writable** mount builds `.atlas/graph.<v2-epoch>.db` fresh
   under v2 semantics (confidence computed, edges materialized), sweeps non-current-epoch files,
   and rebuilds on epoch mismatch — building to a temp path + atomic rename under a single-writer
   lock (F17).
2. `atlas-aci serve` under the documented **`--read-only` `:ro`** mount (`README.md:201-210`)
   performs **zero writes** under `.atlas`: on epoch match it starts; on mismatch it **fails fast
   with a `ToolError` naming the required `index` command** ("index required"). It never sweeps,
   rebuilds, or creates.
3. A binary downgrade to v1 finds no `.atlas/graph.db` and rebuilds its own via `index` — one-time
   reindex per direction, no ping-pong.
4. The one-time full reindex per epoch bump cannot be `--since`-cheap (D1). Reversal signal:
   full-reindex time on the largest supported repo exceeds an operationally tolerable ceiling.
5. The committable JSONL export (A5) is the portable artifact; the DB stays local/ephemeral. A prior
   committed artifact is re-imported through the idempotent importer, which reproduces a valid
   current-epoch DB or fails the integrity check loudly.

**SCOPE-1 (F-8) - `memex.py` is in P0 scope:** the read-only serve path also needs `Memex.__init__`'s
`mkdir` to fail open. `Config.__post_init__` and `Memex.__init__` both `mkdir` the same root, so
fixing only the in-scope `config.py` leaves `Memex` crashing serve under `:ro` (`OSError: Errno 30`,
EROFS) - the real Docker `--read-only`/`:ro` smoke test for AC-H-16 caught it. memex-ref *emission*
stays deferred; only the best-effort `mkdir` is hardened.

**Epoch churn is invisible to users (A1):** `<epoch>` is a monotonic integer bumped once per schema
change; A1 alone advanced it 1 -> 5 (committed `SCHEMA_EPOCH = 5`, `EXPECTED_DDL_HASH` verified against
the HEAD blob, not the working tree). Only the **final** shipped epoch matters to the user-facing
"reindex" story - the P3 migration doc MUST reference the current committed epoch/`EXPECTED_DDL_HASH`
at release, never an intermediate value like epoch 2.

If non-derived data is ever stored in the DB, the cache premise breaks and a real ALTER ladder
becomes necessary (D1 reversal).

## Stories (Phase Spine)

Each phase has a **mechanical exit criterion**; per-phase blocking preconditions are in
`plan-state.json`.

- **P0 — Hardening gate (GATED).** H0 (CI: pytest+ruff+mypy on PR), H1 (central bounds chokepoint),
  H2 (route `search_symbol`+`graph_query` + over-cap/registry tests), H3 (epoch DB substrate +
  sweep/rebuild on `index` only + serve fail-fast + DDL-hash test + rationale-store DDL), H4
  (dead-language honesty), DOC batch, plus the committed `harden-gate.yml`. **Exit gate (all three
  green): CI-runs-tests-on-PR ∧ over-cap+registry+truncation-invariant test (AC-H-4/AC-H-15/AC-H-7/AC-H-8/AC-H-18) ∧
  epoch-substrate+DDL-hash-test (AC-H-9/AC-H-12).** No augmentation merges until green; `harden-gate.yml`
  enforces it in-tree.
- **P1 — Edges + god nodes.** A1 (edge table + confidence enum + candidates + `subclasses_of` +
  `refs.enclosing` DROP + caller-context-from-source), A2 (degree-centrality god nodes). **Blocked
  on:** P0 gate (all three) + H3. A2 blocked on A1 (degree = edge count; G-B). **Exit:** edge/
  confidence tests green (AC-A1-*); god-node determinism test green (AC-A2-1).
- **P2 — Communities + rationale.** A3 (LPA communities, total-ordered — **or cut to v2.1 if the
  probe fails**), A4 (rationale Ruby→Python→JS/TS). **A3 blocked on:** A1 (trustworthy edges) + A2
  (ordered first) + **the D3 probe (hard, mechanical, AC-A3-1)**. **A4 blocked on:** P0 gate + H3
  only (rationale-store DDL folded into H3; independent of A1 edge resolution — F9). **Exit:** LPA
  determinism + total-order test (AC-A3-2) or the mechanical cut-branch (AC-A3-4/5); rationale tests
  green (AC-A4-*).
- **P3 — Export + release.** A5 (deterministic sorted-JSONL export + idempotent import; no
  graph/union merge driver), release-prep (version bump 2.0.0, CHANGELOG, migration doc,
  canary-honesty). **A5 blocked on:** A1, A3 (or its cut), A4 frozen. **Release blocker:** D6
  **cross-platform byte-determinism** verified (AC-REL-1). **Exit:** byte-determinism green on CI
  OS matrix; round-trip import green (AC-A5-4).

Critical path: `H0 → H1 → H2 → [GATE] → H3 → A1 → A3 → A5`, with A2 off A1 early, A4 parallel after
H3+gate, H4 parallel within hardening. (A1→A2→A3→A5 verified sound by vigil against G-A/G-B.)

## Doc-Honesty Decisions

Each scout-surfaced untrue claim, decided **build** vs **correct-the-doc**, with **repo-wide**
resolution per vigil F11-F13:

| # | Item (anchor) | Decision | Phase |
|---|---|---|---|
| 1 | Bounds invariant `README.md:411-413` untrue for search_symbol/graph_query (anchor fixed from scout's wrong 107-111, F6) | **BUILD** — H2 makes it true; also correct `mcp-server/README.md:34-35` `unbounded`/`implementation-defined` rows (F11) | P0 (H2, AC-DOC-1, AC-DOC-10) |
| 2 | `--since` documented as git-ref diffing; impl keys on `(mtime_ns,size)` (`codegraph.py:240`) | **CORRECT DOCS repo-wide** — INTEGRATION.md:201, README.md:173, INTEGRATION.md:208, Dockerfile:64, SETUP.md:238 (F12) | P0 (AC-DOC-2) |
| 3 | Canary pass-rate over a `NotImplementedError` dispatcher (`README.md:348`, `SETUP.md:182-183,282`) | **CORRECT DOCS repo-wide** + visible deferred note in `run-canaries.py` (F13/F15) | P0 (AC-DOC-3) |
| 4 | Prism Ruby specialist mode referenced (5 sites incl `SETUP.md:120`), zero code | **CORRECT DOCS** — `grep -rni prism` zero-match | P0 (AC-DOC-4) |
| 5 | No `LICENSE` despite `README.md:462-463` promise | **BUILD** — add Apache-2.0 `LICENSE` | P0 (AC-DOC-5) |
| 6 | `refs.enclosing` always NULL (`codegraph.py:391-397`) | **BUILD** — DROP in v2 schema; caller-context from edge source (F10) | P1 (A1, AC-A1-9/10) |
| 7 | `search_symbol` `kind` enum stale (`server.py:97-101`) | **CORRECT SCHEMA** — enum ⊇ produced kinds + test | P0 (AC-DOC-6) |
| 8 | `CLAUDE.md:54` cites nonexistent `.atlas/symbols.db` | **CORRECT DOC** | P0 (AC-DOC-7) |

Also folded: README repo-layout omits `test_codegraph.py` (`README.md:384`) → CORRECT (AC-DOC-8);
`server.py:139` memex "refs returned by other tools" false → CORRECT (AC-DOC-9); `mcp-server/README.md`
bounds rows → CORRECT (AC-DOC-10). All P0.

## Acceptance Criteria

Frozen in `acceptance-criteria.md` (EARS form, `ramza-ears-lint`-clean, SHA-256 in
`plan-state.json`). **71 criteria** across tracks H (18), DOC (10), A1 (11), A2 (3), A3 (5), A4 (6),
A5 (8), REL (3), NEG (7). Every criterion names a mechanical check. The harden-gate, each of A1–A5,
the doc-honesty items, and the D6 cross-platform byte-determinism release blocker are all covered;
negative criteria protect the thesis (no LLM client importable, no networkx unconditionally,
AMBIGUOUS never LLM-produced, no ALTER ladder, no new runtime dep, no augmentation before the gate,
no ambiguity-as-importance in god-nodes/communities per AC-NEG-7). AC-H-18 pins the
truncate-signal-iff-content-withheld invariant (the F-1/NEW-1 defect class); AC-A2-3 makes the D4a
analysis-graph-vs-returned-edges divergence visible.

## Confidence

Computed by `ramza-score --rubric confidence` (see `plan-state.json` gates[]). The plan cleared
vigil's independent check with **GO-WITH-CONDITIONS**; all conditions F1-F18 + DIR-1/2/3 are applied.
The reference-repo `[VERIFY]` (F5/V1) is now RESOLVED to two pinned SHAs with the D3a pass rule
frozen before the probe runs; the residual opens are the D3a probe RUN (ATLAS), the D6
cross-platform byte-determinism blocker (V3), and the Spree export-size measurement (V4) - all
mechanically bounded (three-clause per-repo rule + cut-to-v2.1 branch; CI OS matrix; recorded-number
assert).

## Rejected Alternatives

**Plan-structure hypotheses** (`ramza-score --rubric explore`): **H-A — encode FORGE's spine.
SELECTED (elite 87.5).** H-B collapse-hardening REJECTED (weak 41.5). H-C full-parallel REJECTED
(weak 40.5).

**Design alternatives** (settled by FORGE / directives):
- D1(a) ALTER ladder — rejected (back-fills wrong confidence).
- D2 per-tool caps as sole defense — rejected (failed 3×); F2 further forbids a no-op-satisfiable gate.
- **D3 networkx / Louvain flip — DELETED (DIR-1).** A failing probe cuts A3 to v2.1; it never adds a dep.
- D3 optional-dep gating — rejected (silent-cliff footgun).
- D4(a) graphify's silent edge-drop — rejected (undetectable false-negative).
- D6 SQL dump / single JSON blob — rejected (not byte-deterministic / 512 MiB cliff).
- D6 union merge driver — rejected (phantom graph). Trivial regenerate-on-conflict driver permitted.
- F3 branch-protection-as-mechanism — rejected: committed `harden-gate.yml` is the mechanism;
  protection is defence-in-depth.
- **D3a absolute-delta threshold (-0.05) - rejected** (uncalibrated against achievable Q, ATLAS section 3): replaced by the two-part pre-registered rule (Q_struct floor + R=0.85 relative retention), per repo on two pinned SHAs.
- **D3a measure-R-first - rejected** (circularity: the probe would grade its own bar); R fixed a priori at 0.85.
- **D4a fractional-weight / fan-out / synthetic-node analysis graphs - rejected** (ambiguity-as-importance + phantom connectivity = the LPA-collapse and phantom-graph failure modes); AMBIGUOUS excluded from the analysis graph, still returned by graph_query.

## Risks

| ID | Risk | Severity | Mitigation / owner |
|---|---|---|---|
| R1 | **D6 cross-platform byte-determinism unmet** | **RELEASE BLOCKER** | AC-REL-1 diffs export across CI OS matrix; AC-A5-7/A5-8 catch it same-machine first. Owner: vivi + CI. |
| R2 | **D3 LPA quality below threshold** (FORGE 0.65) | High | AC-A3-1 mechanical probe precondition (D3a three-clause rule per repo on both pinned SHAs, verdict == mechanical eval); **DIR-1 cut-branch: A3 -> v2.1, ship A2 only, never networkx** (AC-A3-4/5, AC-NEG-2 absolute). Owner: ATLAS probe -> vivi. |
| R3 | Forgetting to bump `<epoch>` on a schema change | Med | EXPECTED_DDL_HASH test (AC-H-12) + manifest epoch cross-check (AC-H-11). Owner: vivi. |
| R4 | AMBIGUOUS edges balloon on hub symbols | Med | Central cap + truncate-and-flag (AC-H-5) applies. Owner: vivi. |
| R5 | A4 effort budget forces cut after Ruby+Python | Low | Per-language ACs independent (AC-A4-1..3). Owner: vivi. |
| R6 | Rails-scale JSONL exceeds 100 MB | Low | AC-REL-2 measures export size ONCE at P3 on Spree (recorded, CI-asserted) vs 104857600 bytes; D6 reversal adds compression/sharding. Owner: vivi. |
| R7 | An augmentation PR lands before the gate is green | Med | AC-NEG-6 via committed `harden-gate.yml` (in-tree); branch protection defence-in-depth. Owner: CI. |
| R8 | **Concurrent `index` runs race the sweep/rebuild** (F17) | Med | AC-H-17: temp-path + atomic rename under single-writer lock; sweep is index-path only (DIR-2). Owner: vivi. |
| R9 | serve on `:ro` mount hits sweep/rebuild write (F16) | High (was unwritten) | DIR-2: sweep/rebuild are index-only; serve fails fast read-only (AC-H-16). Owner: vivi. |
| R10 | **`uv.lock` pins `atlas-aci` 0.3.1 vs `pyproject.toml` 0.4.0 at HEAD** (F14, verified via `git show HEAD:mcp-server/uv.lock:37`) | Med (live defect shipped in v0.4.0) | Regenerate the lock on the 2.0.0 bump; AC-REL-3 asserts `uv.lock`==`pyproject.toml`. Owner: vivi. |
| R11 | **NEW-2 / NEW-3** (query_limit re-anchors to a module const; query() fallthrough returns the subclasses_of stub) | Low (latent, guarded) | Now guarded by tests; **track for A1, not a P0 gate** - close when A1 extends the query surface. Owner: vivi. |
| R12 | **R-1** latent list sub-field escape in the verb-reachability gate | Low (latent) | Registry checks each verb has a non-empty `_bounded_field`, not that it covers every list sub-field a future verb might carry; **track for A1**. Owner: vivi. |
| R13 | `test_dry_run` sets `truncated:true` without populating `truncated_fields` | Low (contract-shape inconsistency, not silent loss) | **Track for A1**; align to the per-field `truncated_fields` contract. Owner: vivi. |
| R14 | **Verification-anchor hazard: a `VERIFY:` naming a test that does not exist** | Med (process) | A1 found `AC-H-18`'s `VERIFY:` named `test_server.py::test_truncation_signal_iff_content_withheld`, which existed in no commit and was not run by `harden-gate.yml` - the exact documented-but-not-delivered class this release exists to kill, inside a gate criterion. Resolved by **conforming the code to the frozen criterion** (the test now exists under that exact name, wired into the gate, 6 -> 7 tests); no criteria change, hash unchanged. Standing check (must be **phase-scoped**, against the **committed blob** not the working tree): at each phase exit assert every `VERIFY:` naming a test names a test that exists **for that phase's criteria** (a flat all-71 sweep mid-campaign false-positives on not-yet-built phases - e.g. A2/A3/A4/A5 share test files with earlier phases); a full 71-criteria sweep runs at **archive** (P3 end) when every referenced artefact must exist. Verified at A1: all P0+A1 test-anchored criteria (incl. AC-H-18) exist at HEAD; the only gaps are the A2/A3/A4/A5 workstreams not yet built. Owner: ramza (per-phase + archive) / vivi. |
| R15 | **File-scoped shadowing guard under-reports `EXTRACTED`** | Low (under-claims, safe direction) | One local assignment shadowing a class name anywhere in a file demotes every reference to that class in that file from EXTRACTED to INFERRED. Under-claims, never over-claims (correct direction), but disclosed only in code comments, not the user-facing `README.md` DSL section. **Track for the P3 doc pass** (not a new criterion). Owner: vivi (P3 doc). |

## [VERIFY] items

- **[VERIFY] Reference Rails-scale repos (F5) - RESOLVED (pending probe run).** Pinned to two
  SHA-pinned public BSD-3 repos (ATLAS `v1-reference-repo.md`): `solidusio/solidus@4026945d...` and
  `spree/spree@6699cde4...` (SHAs, not tags). The D3a pass rule and constants (Q_struct=0.30, R=0.85,
  K=10 seeds 0..9 gamma=1.0 median Louvain baseline, shipped single-run LPA, confident-subgraph
  undirected unweighted projection) are frozen in the criteria before the probe runs. Probe RUN is
  still pending; executor: **ATLAS**.
- **[VERIFY] D3 evidence probe** - LPA-vs-Louvain modularity per repo on both pinned SHAs. Resolver:
  **ATLAS**. Hard, mechanical precondition on P2/A3 (AC-A3-1: verdict == mechanical eval of recorded
  numbers); feeds the DIR-1 proceed-or-cut branch. networkx is used only in a probe-time ephemeral
  env, never in pyproject/uv.lock (AC-NEG-2).
- **[VERIFY] Cross-platform byte-determinism** — macOS vs Linux; unverifiable single-OS. Resolver:
  **vivi + CI OS matrix** at P3 (AC-REL-1). Release blocker.
- **[VERIFY] Rails-scale export size** — unmeasured (D6 residual). Resolver: **vivi** at P3 (AC-REL-2).
- **[VERIFY] ESL change.json** — `acceptance_checks: []` + the amended criteria SHA-256 must be
  threaded into the ESL manifest at handoff. Resolver: **orchestrator** (stated: "I will thread the
  manifest"); `change.json` left ESL-owned, untouched.

*Note: verify_items V2 (independent plan critic, full-tier maker≠checker) is now RESOLVED — vigil
critiqued this plan (`checker-critique.md`, maker=vivi, checker=vigil); recorded via `ramza-gate
critic --author ramza --checker vigil`.*

---

*RAMZA plan for atlas-aci v2.0.0 — ESL change `aci-v2-harden-and-augment`, tier full. Amended after
vigil GO-WITH-CONDITIONS.*
