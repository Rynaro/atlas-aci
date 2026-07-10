---
artifact: checker-verdict
phase: A1
change_id: aci-v2-harden-and-augment
maker: vivi
checker: vigil
branch: feat/v2-p1-edges
head: ea0eec32b97c835866611ebcc98927f33bab75b0
base: f56a78e
criteria_sha256: 5c3adddbd075a7c12bdd965ee760484a04e5a9a6a4ce05302cbd7bc4147fc7e7  # CONFIRMED matches frozen
verdict: GO-WITH-CONDITIONS
---

# Checker Verdict — Phase A1 (materialized edges + confidence enum + real subclasses_of)

## VERDICT: GO-WITH-CONDITIONS

Criteria hash confirmed (`5c3addd…4fc7e7`) — criteria not bent. Suite run via
`mcp-server/.venv/bin/pytest`: **122 passed**. All eleven A1 criteria PASS on
their mechanical anchors, independently re-verified against a ground-truth
index I built myself (48 files / 874 symbols / 1535 refs / 567 edges; call 420 /
construct 147; EXTRACTED 149 / INFERRED 324 / AMBIGUOUS 94; targets class 147 /
function 326 — every stated number reproduced). The P0-gate and thesis-negative
criteria in scope have not regressed. No BLOCKER.

The predicted "eighth defect" is real and confirmed by reproduction: a
**local variable whose name collides with a class name is mis-tiered
EXTRACTED** (MAJOR-1). It is bounded — it does not flip any A1 criterion, the
resolved target stays correct, and it does not affect the analysis graph — so
it is a condition, not a blocker. Conditions are listed at the end.

## Per-criterion table

| ID | Verdict | Basis |
|----|---------|-------|
| AC-A1-1  | PASS [VERIFIED] | `edges` table present under epoch 3 with relation/source/target/confidence/candidates cols. `test_edges_table_present_v2`; PRAGMA-confirmed. |
| AC-A1-2  | PASS [VERIFIED] | Confidence is a closed enum **by construction** — `_resolve_edges` assigns only the three literals (codegraph.py:1057,1064); no other producer. Real graph shows exactly {EXTRACTED,INFERRED,AMBIGUOUS}. `test_every_call_inheritance_edge_confidence_in_closed_enum`. |
| AC-A1-3  | PASS [VERIFIED] | Single type-qualified → EXTRACTED. `test_single_type_qualified_is_extracted`. Real EXTRACTED sample verified against source (`CodeGraph._node_text` @941/945; Ruby `Target.act`; 147 constructs). See MAJOR-1 caveat (shadowing). |
| AC-A1-4  | PASS [VERIFIED] | Single heuristic → INFERRED. `test_single_heuristic_is_inferred`. Verified `self.reload`, `obj.reload`, bash `greet` all INFERRED. |
| AC-A1-5  | PASS [VERIFIED] | Multi-candidate → AMBIGUOUS with full ordered candidates[], never dropped. Verified `handle()` → AMBIGUOUS, 2 ordered candidates; AMBIGUOUS stays in table + returned by graph_query. |
| AC-A1-6  | PASS [VERIFIED] | Zero candidates → no edge, ref preserved. Verified `missing_fn`/`load_config`/`ghost`/scss `rounded` produce no edge; stay in `refs`; surfaced via `unresolved_refs`. See MINOR-1 (mixin excluded kind — non-silent). |
| AC-A1-7  | PASS [VERIFIED] | `subclasses_of` returns real inheritance+mixin edges. Verified Ruby superclass+include (User/Admin→Base, →Trackable), Python/TS superclass. No `construct` leak. |
| AC-A1-8  | PASS [VERIFIED] | candidates[] total order (same-machine). ORDER BY path,line_start,name (BINARY collation). Indexed twice → byte-identical candidates. `test_candidates_total_order_stable`. |
| AC-A1-9  | PASS [VERIFIED] | `refs.enclosing` DROPPED (not present in v2 refs cols); caller context via edge source endpoint. `test_refs_enclosing_dropped_no_always_null_column`. |
| AC-A1-10 | PASS [VERIFIED] | Caller context from source endpoint; DSL doc (README.md:118-169) documents the F10 `source:{path,line,name,kind}` shape AND the D4a divergence (graph_query returns AMBIGUOUS; confident_edges/god_nodes/communities exclude it). `test_callers_of_caller_context_from_edge_source`. |
| AC-A1-11 | PASS [VERIFIED] | spec.md D4/F18 pins the per-language rule (Ruby/Python/JS-TS); `test_type_qualified_rule_per_language`. See MAJOR-1 (the Python/JS-TS "class-bound name" rule is implemented as global name resolution). |

### P0 gate criteria (not regressed)

| ID | Verdict | Basis |
|----|---------|-------|
| AC-H-1  | PASS [VERIFIED] | ci.yml triggers on pull_request; runs pytest/ruff/mypy. |
| AC-H-4  | PASS [VERIFIED] | `test_every_tool_and_verb_truncates_and_flags_over_cap` (covers subclasses_of edges). |
| AC-H-7  | PASS [VERIFIED] | `test_search_symbol_is_bounded` (in 122). |
| AC-H-8  | PASS [VERIFIED] | `test_graph_query_is_bounded` + my own empirical `callers_of` boundary probe. |
| AC-H-9  | PASS [VERIFIED] | db_path = `.atlas/graph.3.db`; SCHEMA_EPOCH=3. |
| AC-H-12 | PASS [VERIFIED] | Recomputed `ddl_hash(SCHEMA)` == literal `ef64d21…945d9d`; literal is hand-pasted (line 373), not derived. |
| AC-H-15 | PASS [VERIFIED] | callers_of/subclasses_of register `("edges",)`; nested candidates registered. `test_every_list_returning_tool_registers_a_bounded_field`. |
| AC-H-18 | PASS (invariant) [VERIFIED] | Invariant holds for the A1-added shapes at cap-1/cap/cap+1/past-end (edges + nested candidates[]); `unresolved_refs` stays true under edge truncation (my probe). **See MINOR-3: the named test does not exist.** |
| AC-NEG-6| PASS [VERIFIED] | harden-gate.yml present; guards A1-A5 paths (classifier, not literal grep). |

### Thesis negatives (not regressed)

| ID | Verdict | Basis |
|----|---------|-------|
| AC-NEG-1 | PASS [VERIFIED] | No anthropic/openai/boto3 import in mcp-server/src. |
| AC-NEG-2 | PASS [VERIFIED] | No networkx in pyproject.toml or uv.lock. |
| AC-NEG-4 | PASS [VERIFIED] | No `ALTER TABLE` in mcp-server/src. |
| AC-NEG-5 | PASS [VERIFIED] | Core `dependencies` unchanged base..HEAD (only the `ruby` optional-extra emptied for the Prism removal). |
| AC-NEG-7 | PASS [VERIFIED] | `confident_edges()` = `WHERE confidence IN ('EXTRACTED','INFERRED')` — AMBIGUOUS never leaks; `test_ambiguous_excluded_from_analysis_graph`. |

## Ranked findings

### MAJOR-1 [VERIFIED] — shadowing/rebinding produces a false EXTRACTED
`mcp-server/src/atlas_aci/codegraph.py:1045-1052`. For Python/JS/TS, EXTRACTED is
decided by resolving the receiver text against the GLOBAL symbol table
(`SELECT 1 FROM symbols WHERE name=? AND kind IN ('class','module')`) with no
scope analysis. A local variable whose name coincides with a class/module symbol
is therefore mis-tiered EXTRACTED, though spec F18 explicitly classifies a
local-variable receiver as INFERRED. Reproduced:
```
Config = load_config()   # local var shadows class Config
Config.reload()          # -> EXTRACTED (target reload, single candidate); should be INFERRED
```
vs. `obj.reload()` and `self.reload()` which correctly stay INFERRED. The same
root cause can mis-tier a construct edge (a class name rebound to a callable and
invoked). This is the campaign's recurring "confident and wrong" pattern and is
**untested** — `test_capitalized_non_class_receiver_is_inferred_not_extracted`
only exercises a receiver whose name is NOT a class, giving false comfort.
**Blast radius is bounded:** the resolved target stays correct (single
candidate), both EXTRACTED and INFERRED are in the confident subgraph so A2/A3
analysis membership is unaffected, no named A1 criterion test flips, it does not
occur in the actual atlas-aci graph (all real EXTRACTED edges verified correct),
and it is defensible under a global-name-match reading of "class-bound name."
It is a residual limitation of the chosen (spec-sanctioned, zero-LLM) name-based
resolver, not a new regression — the old capitalization proxy had the same class
of error. → Condition, not blocker.

### MINOR-1 [VERIFIED] — `mixin` is an excluded call-target kind (the next excluded kind)
`codegraph.py:415-417` `_CALL_CANDIDATE_KINDS` omits `mixin`. scss `@include foo`
is stored as a `call`-relation ref (codegraph.py:879-895) but a scss mixin is
kind `mixin`, so every scss mixin include resolves to zero candidates → no edge.
Reproduced: `callers_of:rounded` → `edges:[], unresolved_refs:2`. Cross-check of
PRODUCED_KINDS vs `_CALL_CANDIDATE_KINDS`: the only produced kind that is a
legitimate call target and is excluded is `mixin` (heading/id/key/placeholder/
selector/variable are not call targets). **Non-silent** (unresolved_refs
surfaces it) and outside A1's declared Ruby/Python/JS-TS scope, so AC-A1-6 still
holds — but there is no test asserting `_CALL_CANDIDATE_KINDS` covers every
callable-producing PRODUCED_KIND, which is exactly the guard the original
BLOCKER's post-mortem should have left behind. (Aside: `singleton_method` in
`_CALLABLE_KINDS` is dead — Ruby singleton methods are stored as kind `method`
per the QUERIES map — harmless.)

### MINOR-2 [VERIFIED] — AC-H-18's named test does not exist
The frozen criterion names `mcp-server/tests/test_server.py::test_truncation_signal_iff_content_withheld`.
That test never existed in git history (`git log -S` returns nothing). The
invariant is fully covered by a constellation of other tests
(`test_view_file_overflow_and_next_cursor_match_the_invariant`,
`test_candidates_subfield_*`, `test_callers_of_at_sql_limit_boundary_*`,
`test_every_tool_and_verb_truncates_and_flags_over_cap`) plus my empirical probe,
and the invariant holds for the A1-added shapes — but the criterion's mechanical
anchor is unsatisfiable as literally written. Pre-existing P0 state, not a P1
regression. Flagged because the criteria file promises every criterion "names a
test... that decides pass/fail," and this one names a phantom.

### MINOR-3 [VERIFIED] — `edges.relation` is not a closed set
`codegraph.py:309-323` — no CHECK constraint on `edges.relation`, and no test
pins the relation vocabulary, unlike `confidence` (AC-A1-2). A future
`@heritage.<newrelation>` capture or `effective_relation` branch can silently
introduce a 4th+ relation value. No current criterion requires relation closure
(AC-A1-1 only requires it be carried), so not a criterion failure — but it is an
asymmetry with the confidence enum's closed-set guarantee and a latent
silent-widening path. A consumer CAN distinguish "constructed here" from "called
here" today (the `relation` field is on every edge, and subclasses_of never
returns `construct`), so the current behavior is correct; this is about future
drift.

### MINOR-4 [SUSPECT] — edges enumeration order is not a total order
`_edges_for` / `confident_edges` order by `(source_path, source_line, callee_name)`
(codegraph.py:1189-1211). Two references to the same callee on the same source
line (e.g. `foo(foo())`) tie, and their relative order falls back to SQLite
rowid/insertion order. Same-machine deterministic (rebuild is deterministic), so
AC-A1-8 (candidates[], same-machine) is unaffected. Flagged forward for the
D6/A5 byte-determinism gate (AC-A5-7 / AC-REL-1), where a non-total edge order
across machines could break byte-identical export — the release gate is the last
place to discover it.

## Reproduction / regression notes
- 122 tests pass; the three A1 files (test_confidence/test_graph_query/test_schema_epoch) = 42 pass.
- No pre-existing (base f56a78e) test lost an assertion: test_codegraph.py +7 asserts / -0; test_enforcement.py unchanged.
- The H3 guard flip is **honest**: P0's `test_edges_table_not_yet_present` (asserted absence, "H3 must not implement A1 early") is replaced by `test_edges_table_present_v2` (asserts presence) — the opposite assertion for the same fact once A1 lands the table, not a loosened assertion.
- self/super ruling: Vivi kept `self.foo()`/`this.foo()` INFERRED. This is BOTH spec-compliant (F18 worked example) AND substantively correct — the resolver name-matches the callee GLOBALLY with no class-scoping, so a `self.foo()` with one global `foo` is genuinely only heuristically resolved; EXTRACTED would assert certainty the code lacks. Confirmed correct.
- unresolved_refs: counts only zero-candidate refs (AMBIGUOUS produces an edge and is NOT swept in); computed from full COUNT(*), so it stays true when the edges list is truncated; `edges:0, unresolved_refs:0` ⟺ genuinely no callers (verified).
