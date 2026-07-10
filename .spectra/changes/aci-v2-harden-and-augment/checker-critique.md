---
artifact: checker-critique
plan: aci-v2-harden-and-augment
target: atlas-aci v2.0.0
maker: vivi
checker: vigil
role: independent full-tier plan critic (maker != checker; satisfies verify_items V2)
base_repo: <repo>
base_head: f56a78e
reviewed_at: 2026-07-09
verdict: GO-WITH-CONDITIONS
---

# Independent checker critique — aci-v2-harden-and-augment

Adversarial plan review by VIGIL against the actual v0.4.0 source (HEAD `f56a78e`,
clean tree). Every finding carries `file:line` or a criterion ID and is tagged
**[VERIFIED]** (I read the artefact/source and confirmed) or **[SUSPECT]** (reasoned
from behaviour I could not fully execute). Severity: BLOCKER / MAJOR / MINOR.

**Verdict: GO-WITH-CONDITIONS.** The plan's spine is sound: the harden-first gate is
the right shape, D1-D6 are encoded faithfully in prose, G-A/G-B are true, and the
A1->A2->A3->A5 sequencing is correct (G-B verified: no real edges exist today, so god
nodes genuinely need A1). The conditions below are criteria-level and doc-level
defects that would produce false-green gates, late cross-platform rework, or drift-gate
failures if implemented as written. None invalidates the approach; all are fixable
amendments.

---

## C2 / C4 — the harden-gate and decision fidelity

### F1 [VERIFIED] MAJOR — AC-H-9 and AC-H-12 are mutually contradictory (epoch = integer vs epoch = hash)
`acceptance-criteria.md` AC-H-9: "`<epoch>` is the **monotonic schema-epoch integer**".
AC-H-12: "the schema-epoch constant SHALL **equal a hash of the schema DDL**". A hash is
not a monotonic integer; an implementer cannot satisfy both literally. `deliberation.md`
D1 intends a monotonic integer *plus a separate test that the DDL hash recorded for that
epoch still matches* — coherent, but AC-H-12's wording ("epoch SHALL equal a hash")
encodes the wrong thing. Read literally, the DB filename becomes `.atlas/graph.<64-hex>.db`,
contradicting AC-H-9 and `spec.md:107` (`.atlas/graph.<epoch>.db`, integer epoch).
**Fix:** reword AC-H-12 to "a committed `EXPECTED_DDL_HASH` constant paired with the epoch
SHALL equal `hash(current DDL)`; changing the DDL without bumping the epoch (and its
recorded hash) fails CI." Keep AC-H-9's integer.

### F2 [SUSPECT] MAJOR — AC-H-4 (a gate criterion) is trivially satisfiable by a no-op cap
AC-H-4 ("every tool ... SHALL have its response pass through the central cap") is in the P0
`gate_criteria` (`plan-state.json:180-186`). Its VERIFY only "iterates the manifest, asserts
each response is capped." A tool whose response is *below* the cap is "capped" as a no-op,
and a future/existing tool that routes through the middleware but registers **no**
`_bounded_field` passes trivially — which is exactly the "a tool forgot" bug class D2 exists
to make impossible (`deliberation.md:37,131`). The substantive per-tool regressions
(AC-H-7 search_symbol, AC-H-8 graph_query) are **not** in `gate_criteria`. So the gate can be
green while `search_symbol`/`graph_query`/a new `communities` verb is still unbounded.
Note D2's own reversal condition already prescribes the stronger form: "a test asserting
**every registered tool calls a cap helper**" (`deliberation.md:48`).
**Fix:** strengthen AC-H-4 to feed each tool (and each `graph_query` verb) a synthetic
over-cap response and assert it is truncated **and** flagged; OR add a registry-completeness
assertion that every tool/verb exposing a list-valued field has a non-empty registered
`_bounded_field`. Add AC-H-7 and AC-H-8 to `gate_criteria`.

### F3 [VERIFIED] MAJOR — the GATE's teeth (required-status-check / branch protection) are not in-tree and not verifiable by the stated checks
AC-H-1, AC-H-2, AC-NEG-6 and AC-REL-1 all hinge on GitHub **branch protection / required
status checks** ("blocks merge", "branch protection lists the three harden-gate checks as
required", "a probe augmentation PR against a red gate cannot merge"). Branch protection is
repo *settings*, not a committed artefact; nothing in the change tree, and none of the VERIFY
methods, can assert it. A feature PR **can** merge while the gate is red if protection is
unset/bypassed. So "GATED, not merely ordered" (`spec.md:92`, `deliberation.md:129`) is only
as real as un-versioned config the acceptance suite cannot see. The in-repo half (H0 workflow
exists + triggers on `pull_request`) is verifiable; the *gating* half is a promise about
settings. **Fix:** either (a) commit branch-protection-as-code (e.g. a checked-in ruleset /
`gh api` bootstrap script) and point AC-NEG-6 at it, or (b) explicitly downgrade AC-NEG-6/AC-H-1/AC-H-2's
"required status check" clauses to "MUST be configured by the operator (out-of-tree)" so the
plan stops claiming a mechanical in-tree gate it cannot verify.

### F4 [VERIFIED] MAJOR — AC-A3-4 (flip to Louvain) directly contradicts AC-NEG-2 and AC-NEG-5
If the D3 probe fails, AC-A3-4 requires "**adopting Louvain as a required base dependency**"
(= networkx). AC-NEG-2: "networkx SHALL NOT appear anywhere in the resolved dependency tree."
AC-NEG-5: "no new runtime dependency." These are stated **unconditionally**, so the flip
branch is un-passable: taking it violates two NEG criteria, and the release cannot be green.
`deliberation.md:64` confirms the reversal genuinely adds networkx. **Fix:** condition
AC-NEG-2/AC-NEG-5 on "unless the D3 probe triggers the documented Louvain flip (scope
amendment)", or state that a flip requires an ESL scope amendment before the NEG criteria can
change. As written, the criteria set is not jointly satisfiable in the flip case.

### F5 [VERIFIED] MAJOR — undefined "usefulness threshold" and unnamed "reference Rails-scale repo" make AC-A3-1/AC-A3-4/AC-REL-2 non-mechanical
AC-A3-4 pass/fail depends on "LPA modularity **below the usefulness threshold**" — no numeric
threshold is defined anywhere (spec/deliberation say only "small delta"/"usefulness
threshold"). AC-A3-1, AC-A3-4, AC-REL-2 all reference "a **reference Rails-scale repo**" that
is never pinned to a concrete repository/URL/commit. A probe decision and an export-size
assertion that depend on an unnamed repo and an undefined threshold require human judgement —
they are not the "mechanical check" the criteria header (`acceptance-criteria.md:11`) claims.
**Fix:** name the exact reference repo (+ pinned commit) and a numeric modularity delta and a
numeric byte ceiling (D6/R6 say "~100 MB" — drop the tilde) in the criteria before A3/REL.

---

## C1 — mechanically-verifiable criteria (additional)

### F6 [VERIFIED] MINOR — AC-DOC-1 VERIFY greps the wrong lines
`spec.md` (Problem Statement, thesis) and AC-DOC-1 anchor the "mechanical bounds ... applied
per call" invariant to `README.md:107-111`. The actual invariant text is `README.md:411-413`
("**Mechanical bounds.** Line / entry / match / byte caps ... applied per call in
`enforcement.py`. Tools can only narrow bounds, never widen them."). `README.md:107-111` is
the unrelated "## Why read-only" section. AC-DOC-1's VERIFY ("grep: README.md:107-111 retains
the bounds invariant") therefore checks the wrong location; the scout propagated this
wrong anchor and it is now in the spec's thesis citation too. **Fix:** repoint every
`README.md:107-111` reference in `spec.md` and AC-DOC-1 to `README.md:411-413`.

### F7 [VERIFIED] MINOR — AC-A3-1 does not enforce its own ordering; probe "decision" needs prose judgement
AC-A3-1 says "the merge SHALL be blocked until the probe artifact ... present ... **before any
A3 file is touched**". The VERIFY only checks the artefact exists (`test -f`); nothing
enforces the "before A3 is touched" ordering, and "a recorded proceed/flip decision" requires
reading prose. This is honor-system, not mechanical. **Fix:** either accept it as an advisory
process gate (say so) or add a CI check that fails if any A3 source path changed in a commit
that does not also contain `probe-lpa-vs-louvain.md`.

---

## C5 — the tightest coupling (D6 total order)

### F8 [VERIFIED] MAJOR — record-level canonical total order is required by D6 but is not a testable A5 criterion; only AC-REL-1 catches it, and late
D6 (`spec.md:208-216`, `deliberation.md:118`) requires a **total-order sort** of export
*records*. The A5 criteria pin key ordering *within* a record (AC-A5-1 "sorted keys") and
determinism-of-IDs upstream (AC-A1-8 candidates, AC-A3-2 community IDs), but **no criterion
requires the export lines themselves to be emitted in a canonical content order**. The source
today builds rows from `self.repo.rglob("*")` (`codegraph.py:336`, unsorted, FS-dependent)
and `SELECT ... refs/symbols` with **no `ORDER BY`** (`codegraph.py:409-431`). An A5
implementer who serialises in rowid/insertion order passes AC-A5-3 on a single machine (stable
same-FS iteration) and only fails at **AC-REL-1** — a cross-OS release-gate check that fires
last, forcing rework at P3. This is precisely the "developer implemented A1/A3 without reading
D6" gap the checker mandate asks about: AC-A5-1..6 would **not** catch a rowid-order export.
**Fix:** add an A5 criterion asserting the exporter emits records in an explicit canonical
order (e.g. `ORDER BY (type, path, line, name, ...)`) independent of rglob/rowid, and require
sorted iteration in `_iter_source_files`. Do not rely on AC-REL-1 as the first line of defence.

---

## C4 / C6 — decision fidelity and sequencing

### F9 [VERIFIED] MAJOR — rationale_for edges collide with AC-A1-2 ("every materialized edge carries a closed-enum confidence")
AC-A1-2: "**every** materialized edge SHALL carry a confidence value drawn from exactly
{EXTRACTED, INFERRED, AMBIGUOUS}." AC-A4-1: a rationale node "with a **rationale_for edge** to
its enclosing scope." A `rationale_for` edge has no meaningful value in that call/inheritance
enum. If rationale edges share the materialized edge table, AC-A1-2 is violated; if they do
not, the plan never says where they live (a separate table changes the DDL and thus the epoch,
per D1). This also undercuts the C6 claim that "A4 is independent of the edge graph"
(`spec.md:196`, `deliberation.md:172`): A4 is independent of edge *resolution* (comment scan),
but its `rationale_for` edges still need an edge *store*, which is an A1/substrate artefact —
so A4 is not as free-standing as the parallel-after-H3 sequencing asserts. **Fix:** state
explicitly that rationale/`rationale_for` live in a separate relation (or that AC-A1-2 scopes
to call/inheritance edges only), and fold the rationale-store DDL into H3 so A4 truly has no
A1 dependency.

### F10 [VERIFIED] MINOR — `refs.enclosing` "drop" branch has an unspecified downstream shape change
AC-A1-9 lets `refs.enclosing` (always NULL at `codegraph.py:396`) be "populated or absent."
If dropped, `callers_of` (`codegraph.py:425-431`, `SELECT path, line, enclosing`) loses the
`enclosing` field and its response shape changes — the very caller-context the column was
reserved for. No criterion pins which branch is taken or covers the response-shape consequence
of dropping. Populating it is real new extraction work (walk to the enclosing def during
`_extract`), not a checkbox. **Fix:** decide populate-vs-drop in the spec and, if dropping,
add a criterion for the new `callers_of` shape + the DSL doc update.

### C6 confirmations [VERIFIED]
- **G-B is true.** `refs.callee_name` is a bare string, no FK to `symbols.id`; `enclosing`
  is hardcoded `None` (`codegraph.py:391-397`); matching is by name at query time
  (`codegraph.py:408-431`). There are no real edges today, so A2 (degree centrality) genuinely
  needs A1's materialized edge table. **A1->A2 sequencing is sound; the critical path is not
  wrong on this axis.**
- **A4 comment-scan independence** from call/inheritance resolution is correct; the only
  coupling is the edge-store one in F9.

---

## C7 — doc-honesty completeness (the systematic gap)

The 8 doc-honesty decisions are individually reachable, but four of them fix **one** location
while the **same** untrue claim survives in sibling docs that no criterion greps. Only AC-DOC-4
(prism) uses a comprehensive `grep -rni` zero-match — the right pattern; the others are
location-scoped.

### F11 [VERIFIED] MAJOR — `mcp-server/README.md` still advertises the tools as unbounded after H2, and no criterion corrects it
`mcp-server/README.md` "Tools exposed" table: `search_symbol | Index lookup | **unbounded
(cheap)**` and `graph_query | Code-graph queries | **implementation-defined**`. Once H2 makes
these bounded (the whole point of item 1 / AC-DOC-1), this table becomes a **new** untrue claim
that directly contradicts the restored thesis. `mcp-server/README.md` is in declared scope but
**no AC-DOC-*** targets these rows. **Fix:** add a doc-honesty criterion to correct the
`mcp-server/README.md` bounds column (BUILD item 1's "correct-the-doc" tail).

### F12 [VERIFIED] MAJOR — `--since` untruth survives in three uncovered locations after AC-DOC-2
AC-DOC-2 only fixes `INTEGRATION.md:201`. The same git-ref implication persists at:
`README.md:172-174` (TL;DR "re-index incrementally with `--since HEAD~10`"),
`INTEGRATION.md:207-208` (the `--since HEAD~1` post-commit example — still implies HEAD~1 is
diffed), and `mcp-server/Dockerfile:64` ("`atlas-aci index --since <ref>` for incremental
runs"). README is in scope but AC-DOC-2 greps only INTEGRATION.md; **Dockerfile is explicitly
out of scope** (`declared-scope.md:104`), so its false claim cannot even be corrected without
drift. **Fix:** broaden AC-DOC-2 to a repo-wide grep-zero for the git-ref phrasing (as
AC-DOC-4 does for prism), correct the README TL;DR and the `--since HEAD~1` example, and either
add Dockerfile to scope or accept a documented residual.

### F13 [VERIFIED] MINOR — canary pass-rate untruth survives in SETUP.md after AC-DOC-3
AC-DOC-3 targets `README.md:348`. The identical claim is at `SETUP.md:182-183` ("First-run
pass rate of 50-60% is normal ... until >=80%") plus `SETUP.md:281-282`. SETUP.md is in scope
but AC-DOC-3 does not grep it; the chosen resolution is "remove/flag the claim" (dispatcher
deferred), not "command exits 0", so SETUP.md is not covered. **Fix:** extend AC-DOC-3 to grep
SETUP.md too.

### C7 confirmations [VERIFIED]
- README "mechanical bounds, always" IS untrue for `search_symbol`/`graph_query` today
  (`search_symbol.py:30-41`, `graph_query.py:29-37` call only `enforcement.record`, no cap) —
  item 1 BUILD is correctly reachable via AC-H-7/AC-H-8 (modulo F2).
- `INTEGRATION.md:201` git-ref claim is false (`__main__.py:66-73` help text is honest;
  `codegraph.py:240` keys on `since is not None`) — reachable.
- Canary pass-rate over a `NotImplementedError` dispatcher: confirmed
  (`scripts/run-canaries.py:84,334`) — reachable.
- Prism vaporware: confirmed at `codegraph.py:9-11`, `SETUP.md:20,120,218,264`,
  `INTEGRATION.md:112`, `pyproject.toml:22-23` (note `SETUP.md:120` "parsed via Prism" is a
  fifth site the spec's item-4 anchor list omits, but AC-DOC-4's `grep -rni prism` zero-match
  catches it — good).
- CLAUDE.md `.atlas/symbols.db` (line 54), README repo-layout omits `test_codegraph.py`
  (lists only `test_enforcement.py` at `README.md:384`), memex manifest string
  (`server.py:139`): all confirmed and criterion-covered (AC-DOC-7/8/9).

---

## C3 — scope completeness

### F14 [VERIFIED, impact SUSPECT] MAJOR — `mcp-server/uv.lock` is out of declared scope but a 2.0.0 release must touch it
Release-prep bumps `pyproject.toml` version 0.4.0 -> 2.0.0. `uv.lock` records the workspace
package version (`uv.lock:36-37` currently `name = "atlas-aci"` / `version = "0.3.1"` — already
one release stale). Regenerating the lock on a **major** release is normal and would change
`uv.lock`, which is **not** in `plan-state.json declared_scope` (lines 110-132) — a
`ramza-drift` failure. The declared-scope note "uv.lock beyond the version bump"
(`declared-scope.md:104`) acknowledges the version bump *should* touch it, yet the mechanical
list omits it entirely. (Impact is SUSPECT because the stale `0.3.1` lock shows the project may
not regenerate on bump; either way the plan is ambiguous.) **Fix:** add `mcp-server/uv.lock` to
`declared_scope`, or state in scope that the lock is intentionally left stale.

### F15 [VERIFIED] MINOR — `scripts/run-canaries.py` is declared but no phase touches it
The canary decision is "correct the README, **dispatcher deferred**"
(`spec.md:76`, AC-DOC-3). So `scripts/run-canaries.py` (declared at `declared-scope.md:46`,
`plan-state.json:129`) is not actually edited unless an inline "not implemented" note is added.
Harmless for drift (over-declaration), but flagged per the checker mandate. **Fix:** drop it
from scope or add the honesty note as a covered edit.

### C3 confirmations [VERIFIED]
- `search_symbol.py:23` and `graph_query.py:22` hardcode `(config.repo / ".atlas" / "graph.db").exists()`.
  H3's rename to `.atlas/graph.<epoch>.db` **breaks both existence checks** (they will report
  INDEX_UNAVAILABLE against the renamed DB). Both files ARE in declared scope (H2/A1), so this
  is not drift — but the plan never flags that H3's rename forces edits to these two
  existence checks; treat as an implementation coupling for vivi, not a scope gap.
- Everything else criteria imply editing is present in `declared_scope` (the flat `mcp-server/tests/*.py`
  glob generously covers all seven net-new test files).

---

## C8 — missing risks (unwritten)

### F16 [VERIFIED mechanism / SUSPECT impact] MAJOR — auto-sweep + rebuild-at-startup collides with the documented `--read-only` serve container
`README.md:204-208` documents serve as `docker run --rm -i --read-only -v /repo:/repo:ro ...`,
i.e. the repo (and thus `<repo>/.atlas`) is a **read-only** mount; `Dockerfile:12` confirms only
`index` needs a writable `/repo`. AC-H-10 sweeps ("**the non-current-epoch DB files SHALL be
swept**") and AC-H-11 rebuilds ("treated as stale and **rebuilt**") **at server/indexer
startup** — both are writes/unlinks under `.atlas`. On the `:ro` serve mount these fail
(read-only filesystem). The Migration section (`spec.md:229-238`) assumes a writable `.atlas`
("full reindex builds it fresh", "old ... auto-swept at startup") and never reconciles this
with the shipped read-only serve deployment. **Fix:** scope the sweep/rebuild to the *index*
command (writable mount) only, or make the serve path tolerate a read-only `.atlas` (skip
sweep, fail-soft to "index required"), and add a criterion for the read-only-mount serve path.

### F17 [SUSPECT] MINOR->MAJOR — concurrent index runs race the auto-sweep
`sqlite3.connect` is used with default locking, no WAL (`codegraph.py:214`); the documented
post-commit hook fires `atlas-aci index ... &` in the **background** (`INTEGRATION.md:207-208`),
so two quick commits launch two concurrent index processes. Full builds `DELETE FROM
symbols/refs/files` then re-insert (`codegraph.py:243-245`); H3 adds a startup **sweep** that
unlinks `.atlas/graph.*.db`. A second process can sweep/rebuild the DB the first is mid-write
on. Pre-existing hazard, **aggravated** by the sweep; no criterion addresses concurrency.
**Fix:** at minimum document single-writer expectation; ideally a lockfile or `.tmp` +
atomic-rename build.

### F18 [SUSPECT] MINOR — confidence-enum semantics ("syntactically type-qualified") are inherited from graphify's statically-typed resolvers and underspecified for Ruby/Python/JS
AC-A1-3/4 hinge on "**syntactically type-qualified** at the call site (`Foo::bar`, explicit
receiver type)". graphify's `type_qualified` (`extract.py:2374-2375`) comes from typed
languages (Swift/Kotlin/C++). atlas-aci's languages are dynamically typed; what counts as
"type-qualified" for `self.foo()` / `obj.bar()` in Ruby/Python is not pinned. The criteria are
test-fixture-decidable, but the *semantic* meaning of EXTRACTED vs INFERRED for these languages
is defined only by whatever fixture the implementer writes. **Fix:** pin the syntactic rule per
shipped language in the spec (e.g. constant/`::`/`.new` receiver = qualified).

---

## Confirmed-correct (no action)
- D1 no-ALTER is faithful: AC-NEG-4 grep-zero for "alter table"; the fresh-epoch schema means
  "dropping" `refs.enclosing` needs no ALTER (just omit from the v2 CREATE TABLE). Consistent.
- D2 central-locus, D4 deterministic 3-value enum + candidate-count partition + zero-candidate
  no-edge, D5 markup/config rationale exclusion + dead-language honesty, D6 no union merge
  driver: all encoded faithfully (modulo F4/F8/F9 above).
- No LLM anywhere: AC-NEG-1/NEG-3 are decidable; the `[dev]` extras already ship
  pytest/ruff/mypy (`pyproject.toml`), so H0 adds no dependency and AC-NEG-5's baseline is clean.
- One residual criterion nit [VERIFIED MINOR]: AC-A5-5 forbids "`git config merge.*driver`
  ... wiring", but D6/`spec.md:220-222` explicitly *permit* a trivial regenerate-on-conflict
  driver — which would register `git config merge.<x>.driver` and trip the grep. Narrow
  AC-A5-5 to forbid a **graph/union** merge driver specifically.

---

## Ordered conditions (address before / during `in_progress`)
1. F1  — fix AC-H-12 wording (epoch integer + recorded DDL-hash constant); resolve vs AC-H-9.
2. F3  — resolve the branch-protection gap: commit protection-as-code or downgrade AC-NEG-6/AC-H-1/AC-H-2's "required check" claim.
3. F2  — strengthen AC-H-4 to a real per-tool/per-verb cap assertion; add AC-H-7/AC-H-8 to gate_criteria.
4. F4  — condition AC-NEG-2/AC-NEG-5 on the D3-flip, or require a scope amendment for the flip.
5. F5  — name the reference Rails repo (+commit) and numeric thresholds for AC-A3-1/AC-A3-4/AC-REL-2.
6. F8  — add an A5 criterion mandating canonical record-level ordering + sorted `_iter_source_files`.
7. F9  — specify where `rationale_for` edges live; reconcile AC-A1-2 vs AC-A4-1; fold rationale-store DDL into H3.
8. F16 — reconcile auto-sweep/rebuild-at-startup with the `--read-only` serve mount; add a criterion.
9. F11 — add a criterion correcting `mcp-server/README.md`'s "unbounded"/"implementation-defined" rows.
10. F12 — broaden AC-DOC-2 to grep-zero the `--since` git-ref phrasing repo-wide (README TL;DR, HEAD~1 example, Dockerfile).
11. F14 — add `mcp-server/uv.lock` to declared_scope (or declare it intentionally stale).
12. F13, F6, F10, F17, F18, AC-A5-5 nit, F7, F15 — minor amendments per findings above.

*VIGIL — independent checker record for ESL change `aci-v2-harden-and-augment` (maker=vivi, checker=vigil).*
