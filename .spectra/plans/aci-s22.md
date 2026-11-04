---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T01:00:00Z
slice_id: ACI-S22
issue: https://github.com/Rynaro/atlas-aci/issues/43
---
# ACI-S22 — turn impact and coverage into an evidence-backed validation plan

## Scope

Implement issue #43 on merged base `4843ddc` (main after PR #85 / ACI-S21), branch `aci-s22`.
Live marker `<!-- atlas-aci:slice:ACI-S22 -->` verified; no prior S22 plan, receipt or execution
state. Criteria bytes hash to `bf2e698f1ae1fc7f418674eccda6a44a57fb2609d7af4e45e969fead4c383cab`
= contract table (EARS lint ok, 8 criteria). Predecessors verified on the execution base: S20
(#41, `query/impact*`) and S21 (#42, `coverage_import` generations) are ancestors of HEAD with
COMPLETE receipts.

Observed facts (re-verified at planning):
- `impact.py` composable blocks: `change_set(before, after, limits)` :211 → `(result, loaded,
  diff_work)` (diff admission before any parse); `select_seeds(changes, loaded[1])` :258;
  `async propagate(readers, seeds, request, work, changed_ids)` :304 → `(impacted, seed_rows,
  limited)`, impacted sorted by `impact_limits.rank_key` :106, each with `path{nodes,hops}`;
  `normalize` :91 (published input schema + IMPACT_LIMITS); `Settings` :68. `compute` :492 returns
  an envelope already prefix-admitted by `max_bytes`/element cap — not a complete collection.
- Change records (`diff._record` :288) carry `before`/`after` refs (`diff._ref` :252):
  `snapshot_id, generation_id, occurrence_id, evidence_id, path, source_sha256` (the definition
  occurrence's FILE hash, as used by `read_source(path, sha)`), `start/end_byte, line, end_line`.
  `_Loaded` (diff.py:85) holds `entities[path][id]` (code entities only) per side.
- S21 records: `test_executes` assertion (source `coverage_test`, name = nodeid, path = test file;
  target = measured file's file entity; `raw_target "path:line"`) → evidence → `execution`
  occurrence (`line == end_line`, `source_sha256` = that generation's snapshot hash, role
  `observed_execution:<phases>`) → producer `{"coverage": {run_id, …}}`. Served by
  `idx_assertions_target_relation_id` (evidence.py:35); `ev.get` :737; runs in
  `generation_inputs.coverage.runs`. No reader API (`IndexedReader.record` hides `coverage_test`).
- Native call assertions: `raw_target = callee_name`, source = enclosing entity
  (codegraph.py:4768-4827); `refs` indexed by `callee_name`; assertions indexed by `path`.
- Fixture `tests/fixtures/coverage/project`: `app/calc.py` `clamp` 43-48, body lines 44-47
  executed by `tests/test_basic.py::test_clamp`, `tests/test_params.py::test_clamp_param[0]`/`[5]`
  (labels.json run a); `never_called` 51-52 has no `test_executes`. Tests use absolute
  `from app import calc` → no resolved edges.
- Process launches in src: only `snapshots.identity` (build-time publish, codegraph.py:3553),
  `tools/search_text.py`, `tools/test_dry_run.py`. Lease/read paths spawn nothing.
- Pins: tools JSON sha `94f9567a…`, 1,631,472 bytes (test_s21_isolation.py:42-43); MINIMUM 3436,
  COLLECTION 3448, FIXED_ERROR 797, IMPACT_MINIMUM 7730 (test_s20_mcp.py:435-460); CLI commands are
  pinned only by subset (test_s21_isolation.py:92).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)

1. Surface: library `query/validation*.py` + CLI `atlas-aci validation-plan` (impact's Side
   flags). No MCP op; `tools/graph_query.py` untouched; `__main__.py` via scope amendment.
2. Snapshot matching: an observation matches span side S iff occurrence path == ref.path,
   occurrence `source_sha256` == S's `ref.source_sha256`, and `ref.line ≤ line ≤ ref.end_line`.
   Own-generation coverage is used for that side. Repeatable `--coverage-graph-root` adds external
   sources. Each source is classified per file hash as before, after or neither; neither = stale,
   never evidence, reported in `coverage_sources`. No request paths.
3. Structural discovery: A (test-like reverse-path impacted entities, hop proof) + B (same-name
   references from test files, lower rank). C omitted.
4. Ranker `atlas-validation-rank.v1`, content-addressed `plan_id`. Every recommendation carries
   `basis`, `observed_in`, `post_change_validation` (true only when observed in after), exact
   assertion/evidence/occurrence/producer/run/generation/snapshot ids, and `present_in_after`.
   Gaps carry closed reasons.

### In
1. `query/validation_limits.py`: formats, closed enums, VALIDATION_LIMITS, test rule, `rank_key`,
   `recommendation_id`, `plan_id`, LIMITATIONS (pure data/functions).
2. `query/validation_coverage.py`: source admission, per-file classification, bounded observation
   reads, header/body scope.
3. `query/validation.py`: `async collect(...)`, spans, structural A/B, recommendations, gaps,
   completeness, selection, `plan_id`; `run(before: Side, after: Side, arguments, settings,
   coverage_roots)`; `canonical_bytes`.
4. `query/validation_schema.py`: closed JSON Schema + semantic `validate_plan(plan)` (CLI runs it
   before printing, impact precedent).
5. CLI `atlas-aci validation-plan` (`__main__.py`).
6. `atlas/evals/validation_plan_comparison.py` (+ README section): labelled comparison.
7. Docs `atlas/docs/validation-plan.md`, README/INTEGRATION pointers, CHANGELOG.

### Out
- Running, importing or collecting tests; automatic skipping; CI changes.
- MCP exposure (op, schemas, tools JSON, admission minimum); impact boundary probes; pagination;
  byte-budget admission.
- Merging nodeids with tree-sitter entities (S21 rejected it); option C path heuristics; reading
  pytest configuration.
- Edits to `query/{impact*,diff*,indexed,traversal,admission,paging}.py`, fingerprinted producers,
  `providers/coverage*`, `protocol/*`, `server.py`, `config.py`, `enforcement.py`, `tools/*`,
  `pyproject.toml`, `uv.lock`.

Declared implementation paths (issue): `mcp-server/src/atlas_aci/query/validation*`,
`mcp-server/src/atlas_aci/tools/graph_query.py` (declared but stays byte-unchanged),
`mcp-server/tests/slices/test_s22_*.py`, `atlas/evals/*`.

Scope amendment (`.spectra/changes/aci-s22/scope-amendment.json`, recorded before implementation):
`__main__.py` (CLI command), `atlas/docs/validation-plan.md`, `README.md`, `INTEGRATION.md`,
`CHANGELOG.md`, and Ramza/receipt/backlog files. Fixtures are reused read-only (tmp copies), and
inline sources come via `test_s19_support.same_path_pair`.

Rightsize FULL 5 (files est. 10; public CLI; novel matching/ranking model; med stakes).

## Approach

### Decisions

D1 Composition, single computation. `collect` opens/holds the before and after leases plus each
external source (`impact.open_side` mapping) in one `ExitStack`.
- `request = impact.normalize({"schema_version": VERSION, "op": "impact", **impact_args})` →
  `impact.change_set(before, after, settings.diff_limits)` once →
  `eligible = impact.select_seeds(changes, loaded[1])` →
  `seeds = eligible[:min(max_seeds, element_limit)]` →
  `work = ImpactWork(**{n: request[n] for n in IMPACT_LIMITS})` →
  `await impact.propagate(readers, seeds, request, work, changed_ids)` once.
- This mirrors `compute` :502-513 without editing impact.py. `compute` is never called in
  production, so there is no double change-set or parse cost.
- G-IMPACT-PARITY (test only) asserts:
  - `change_set_id` == `compute(...)["data"]["change_set_id"]`;
  - the impacted (entity id, snapshot_side, distance) sequence equals compute's untruncated
    `impacted` on every fixture pair.
- Private coupling stays `diff` internals via impact only (documented).

D2 Spans. One span per eligible change record (categories removed/signature/unknown/body/added;
format/location are not spans, as in impact), ordered by `(category_weight, path, first line,
change_record_id)`.
- The first `max_spans` are evaluated; any beyond → completeness `span_limit`.
- Sides: removed → before; added → after; others → each non-null ref; null ref →
  `not_applicable`.
- `header_end_line` (Python): the first line L in `[ref.line, ref.end_line]` whose bytes
  (`coverage_py.line_spans` over `reader.read_source(path, sha)`, cached per side+path) start,
  after leading whitespace, with `def `, `async def ` or `class `; otherwise `ref.line`.
  Non-Python: `ref.line`. Body lines = `(header_end_line, end_line]`.

D3 Coverage sources. `before` and `after` (own generations; contribute only if kind
`coverage_import`) plus `external` roots (`--coverage-graph-root`, opened at the after repo path).
Each external:
- must lease (`impact.open_side`);
- must pass `diff._check_compatible(source, after)`;
- must have a distinct `atlas_dir` and `generation_id` from both sides and from other sources;
- must stay within `max_coverage_sources`.

Any failure → typed `VALIDATION_SOURCE_REJECTED` with a closed reason (exit 1, empty stdout).
Sources are sorted `before, after, external…` (externals by generation_id), so flag order is
irrelevant.

Per (source, changed path) classification:
- `matches_before` / `matches_after` / `matches_both`: the source's snapshot observation hash
  (`source_observations.observed_source_sha256`) equals that side's file hash;
- `stale`: the source has observation rows for the path but matches neither;
- `not_observed`: no rows.

Only matching sources feed evidence. Stale rows are counted (`stale_observation_rows`), never
hydrated into evidence, and surface in span coverage states.

D4 Observation reads. For each matching-or-stale (source, changed path), in sorted order:
1. the file entity id (`entities` by path, kind `file`);
2. one target-indexed query: `relation='test_executes'` AND `target_entity_id=?` ORDER BY id;
3. bulk path loads of evidence and `execution` occurrences;
4. `ev.get` for `coverage_test` entities and producers (cached).

Rows are charged to `work.observation_rows_read`. Hitting `max_observation_rows` stops reading;
spans of unread or partially read paths get state `observation_limit` plus completeness
`observation_limit`. The D-matching rule (decision 2) is then applied per span side. A matched
line is `body` if `> header_end_line`, else `declaration` (import-time header lines, S21
semantics).

D5 Structural discovery (basis `structural`). Test rule `atlas-pytest-default-conventions.v1`, a
closed predicate:
- language `python`;
- kind ∈ {function, method};
- name starts with `test`;
- basename matches `test_*.py` or `*_test.py`;
- if any class owner exists, the nearest class owner's name starts with `Test`.

Methods:
- A `reverse_path`: every `propagate` impacted item whose entity passes the rule. Evidence keeps
  `distance`, side ids and per-hop `{assertion_id, evidence_id, occurrence_id, producer_id,
  relation, resolution}` taken from the impact hops.
- B `same_name_reference`: probe names in order. First the evaluated span entities (seed side),
  then the impacted entities in impact rank order, deduped by (side, name). Stop at
  `max_reference_probes`. For each probe:
  - `SELECT path FROM refs WHERE callee_name=? ORDER BY path, line, col`, charged against
    `max_reference_rows`;
  - keep test-rule paths;
  - load that path's assertions once (cached), keeping those with `raw_target == name`,
    `relation ∈ impact._probe_relations(relations)` and a source entity (from `loaded[side]`)
    that passes the rule;
  - `ev.get` evidence → occurrence/producer ids;
  - `distance` = via distance + 1 (seed = 0 → 1).

Per span, `structural` is:
- `found`: some recommendation maps to it;
- `incomplete`: the seed was not expanded, its frontier stopped on a limit, or one of its probes
  went unprobed;
- `unsupported_language`: the span is not Python;
- `none`: otherwise.

D6 Recommendations. One per test identity:
- observed: `("coverage_test", path, nodeid)` across all sources/sides;
- structural: `("code_entity", entity_id)` across both sides.

`recommendation_id = sha256("atlas-validation-recommendation.v1:" + canonical(key))`.
Observed and structural records stay separate. A structural record's `also_observed` is true iff
its selector hint equals an observed nodeid with its `[param]` suffix stripped.

Fields:
- `post_change_validation` ⇔ `observed_in.after`.
- `present_in_after` ⇔ the test file is indexed in the after snapshot.
- Per evidence entry, `test_file_after` ∈ {same_bytes, changed_bytes, absent}, comparing the
  evidence's snapshot hash of the test file with after's.
- Evidence entries are sorted by `(matched/snapshot side after=0 before=1, scope body=0,
  change_record_id, source order, generation_id, run_id, line)`. The first
  `max_evidence_per_recommendation` are emitted, with `evidence_total`.

D7 Span state and gaps.
- Per span side, coverage state = first applicable of: `not_applicable`, `unsupported_language`,
  `observation_limit`, `executed` (body match in a matching source), `declaration_only`,
  `no_observation_in_span` (a matching coverage source has rows for this file),
  `no_observation_in_file` (a matching coverage-bearing source has none for this file),
  `stale_only`, `no_coverage_source`.
- Span `state`:
  - `observed_after` if after is `executed`;
  - else `observed_before_only` if before is `executed`;
  - else `structural_only` if structural is `found`;
  - else `gap`.
- Every `gap` span appears in `gaps[]` with `action: "inspect"`, `basis: "unsupported"` and a
  non-empty closed `reasons` map. Each side state maps to a reason:
  - `no_observation_in_span`, `no_observation_in_file`, `no_coverage_source`,
    `observation_limit` and `unsupported_language` → reasons of the same name;
  - `stale_only` → `stale_coverage_only`;
  - `declaration_only` → `declaration_only_execution`.

  The structural state maps as: `none` → `no_structural_test`; `incomplete` →
  `structural_discovery_incomplete`; `unsupported_language` → `unsupported_language`.
- Gaps are computed from the full evaluated collection, never from the emitted prefix.

D8 Ranking (`atlas-validation-rank.v1`, ascending tuple):
`(basis_order, -spans, -body_lines, category_weight, distance, selector.value,
recommendation_id)`.
- `basis_order`: 0 observed with an after body match; 1 observed with a before body match only;
  2 observed declaration-only; 3 structural with reverse_path; 4 structural same_name only.
- `spans` = distinct mapped change_record_ids.
- `body_lines` = distinct matched body lines.
- `category_weight` = min over mapped spans (impact weights).
- `distance` = min structural distance (0 for observed).

D9 Record `atlas-validation-plan.v1` (closed; canonical JSON + "\n"; repo-relative paths only):
```
{format, status:"recommendations_only", execution:"not_performed", ci_policy:"advisory_only",
 ranker, test_rule, before|after:{snapshot_id, generation_id, generation_kind}, change_set_id,
 diff_work, impact:{seeds_total, seeds_expanded, impacted_total, relations{}, confidence{},
   frontiers:[{changed_entity_id, snapshot_side, expansion, outcome, stop_reason, commitment}]},
 effective_limits{}, work:{examined_edges, visited_nodes, observation_rows_read,
   stale_observation_rows, reference_probes, reference_rows_read, assertion_paths_loaded,
   source_files_read},
 coverage_sources:[{source:"before"|"after"|"external", generation_id, snapshot_id,
   generation_kind, runs:[{run_id, producer_id, tool, report_timestamp}],
   files:[{path, classification, observation_rows}]}],
 spans:[{change_record_id, entity_id, name, kind, language, path, category,
   sides:{before|after: null|{snapshot_id, generation_id, source_sha256, line, end_line,
   header_end_line}}, coverage:{before, after}, structural, recommendations, state}], spans_total,
 recommendations:[{recommendation_id, action:"run_test", basis:"observed_execution"|"structural",
   method:"coverage_context"|"reverse_path"|"same_name_reference",
   test:{identity_kind:"coverage_test"|"code_entity", entity_id, path, name,
     selector:{kind:"pytest_nodeid"|"pytest_selector_hint", value}},
   observed_in:{before?:true, after?:true}, post_change_validation, present_in_after,
   also_observed, rank:{basis_order, spans, body_lines, category_weight, distance},
   spans_total, evidence_total, evidence:[E]}], recommendations_total,
 gaps:[{change_record_id, entity_id, name, kind, language, path, category, action:"inspect",
   basis:"unsupported", lines:{before|after: null|[line, end_line]}, coverage{}, structural,
   reasons{}}], gaps_total,
 completeness:{state:"complete"|"incomplete", reasons{}},
 selection:{spans, recommendations, gaps: <truncated bool>}, limitations{}, plan_id}
```
E (observed):
```
{kind:"observed_execution", change_record_id, matched_side,
 matched_snapshot_id, matched_generation_id, source, coverage_generation_id,
 coverage_snapshot_id, run_id, producer_id, assertion_id, evidence_id, occurrence_id, line,
 phases, scope:"body"|"declaration", matched_lines[≤max_matched_lines], matched_lines_total,
 body_lines_total, test_file_after}
```
`line` and the ids belong to the representative observation: the lowest body line, else the
lowest line.

E (structural):
```
{kind:"reverse_path"|"same_name_reference", change_record_id, snapshot_side, snapshot_id,
 generation_id, distance, hops[] | {via_entity_id, reference:{assertion_id, evidence_id,
 occurrence_id, producer_id, relation, resolution, raw_target, line}}, test_file_after}
```
Structural records have `observed_in == {}`, `post_change_validation == false` and no
coverage/run field.

Closed enums: completeness reasons `span_limit, seed_limit, impact_work_limit,
partial_extraction, observation_limit, reference_probe_limit, reference_row_limit`.
Source rejection reasons are `diff.REJECTIONS` plus `same_namespace, duplicate_source,
source_limit`.

`plan_id = sha256("atlas-validation-plan.v1:" + canonical(plan − plan_id))`.

`validate_plan` enforces:
- the JSON Schema;
- the basis⇔evidence-kind rules above;
- `post_change_validation ⇔ observed_in.after`;
- every gap span has state `gap` with non-empty reasons;
- strict `rank_key` order;
- totals ≥ emitted;
- `completeness.state == "complete"` ⇔ empty reasons;
- the `plan_id` recompute.

D10 Limits and cost. VALIDATION_LIMITS (default, low, high):

| Limit | Default | Low | High |
| --- | --- | --- | --- |
| `max_spans` | 256 | 1 | 2048 |
| `max_recommendations` | 50 | 1 | 500 |
| `max_gaps` | 256 | 1 | 2048 |
| `max_evidence_per_recommendation` | 8 | 1 | 64 |
| `max_matched_lines` | 32 | 1 | 256 |
| `max_observation_rows` | 100000 | 0 | 1000000 |
| `max_reference_probes` | 64 | 0 | 512 |
| `max_reference_rows` | 2000 | 0 | 20000 |
| `max_coverage_sources` | 4 | 0 | 8 |

Impact propagation limits use impact's ranges; diff limits are operator-only (impact defaults).
Each list is a ranked prefix up to its cap, with totals and `selection` flags. There is no byte
budget (CLI only); worst-case output at maxima is documented.

Cost compared with `atlas-aci impact`:
- the same single change set and propagation (impact boundary probes are skipped);
- plus one lease acquisition per external source — the dominant fixed cost, at most 8;
- plus O(changed paths × sources) indexed queries / path loads;
- plus ≤ `max_reference_probes` indexed `refs` lookups and ≤ 1 assertions load per distinct test
  path.

Only the parity test computes twice.

D11 Determinism (AC-006). Every collection is sorted by explicit keys (SQL `ORDER BY id`/`path`,
then Python sorts). Plan counters are pure counts, and there are no timestamps, durations,
absolute paths or host data.
- Generation/snapshot/record ids are content-addressed, so fresh roots at the same repo path
  replay.
- Propagation deadline caveat (issue step 7): replay-identical only while frontier commitments
  match. Tests assert this guard.

D12 Errors and CLI. `atlas-aci validation-plan`:
- options: `--repo/--before-repo/--after-repo/--graph-root/--before-graph-root/
  --after-graph-root` (as in impact), `--relations`, `--confidence`, `--max-seeds`,
  `--max-depth`, `--max-visited-nodes`, `--max-examined-edges`, `--max-vm-operations`,
  `--deadline-ms`, `--max-diff-*`, the D10 limits, and repeatable `--coverage-graph-root`.
- stdout: `canonical_bytes(plan)` after `validate_plan`.
- typed errors (`SNAPSHOT_DIFF_REJECTED`, `IMPACT_DIFF_TOO_LARGE`, `VALIDATION_SOURCE_REJECTED`)
  → exit 1 with `Validation plan rejected: CODE[: reason]` and empty stdout; messages never carry
  `str(exc)` or paths.
- `INVALID_INPUT`/usage → exit 2.

D13 No execution (AC-004). `query/validation*.py` import nothing from `subprocess`,
`multiprocessing`, `pty`, `runpy`, `importlib`, `ctypes`, `atlas_aci.tools`, `atlas_aci.server`.
They do not call `eval/exec/compile/__import__` or any `os.exec*/spawn*/fork*/system/popen/
posix_spawn*`; an AST deny-list test enforces this. Sources are read as snapshot bytes only.

### Limitations (static `validation_limits.LIMITATIONS`)
1. `recommendations_are_advisory_and_do_not_change_ci`
2. `tests_are_not_executed_by_planning`
3. `observed_execution_is_not_a_pass_or_assertion`
4. `before_snapshot_observation_is_not_post_change_validation`
5. `absence_of_observation_is_not_evidence_of_non_execution`
6. `coverage_matches_only_byte_identical_file_snapshots`
7. `declaration_lines_are_weighted_below_body_lines`
8. `structural_tests_follow_pytest_default_conventions_only`
9. `same_name_references_over_approximate`
10. `absolute_test_imports_do_not_yield_reverse_paths`
11. `observed_and_structural_identities_are_not_merged`
12. `format_and_location_changes_are_not_spans`
13. `impact_boundaries_not_reported_use_atlas_aci_impact`
14. `results_are_not_paginated`
15. `not_exposed_over_mcp`

### Work plan (timebox 3 engineering days)

Day 1 AM — qualification, before any product code (scratch script, outputs in
`.spectra/evidence/aci-s22/qualification.json`):
- (a) A `coverage_import` before (fixture + run a) vs a build after (calc.py edited: `clamp`,
  `total`, `never_called` bodies) passes `impact.change_set`. Records are `body` with both refs,
  and `ref.source_sha256` == manifest `observed_source_sha256` == sha256(bytes).
- (b) The target-indexed `test_executes` read on the before lease returns the label rows for
  calc.py, and the occurrence hash equals the before ref hash.
- (c) The `refs` rows for `clamp`/`total` lie in the test files, and same-path assertions with
  `raw_target` equal to the name have test-entity sources (record the kind/owners for
  `TestTotals.test_total`).
- (d) The inline package `pkg/{__init__,geometry,shapes,test_shapes}.py` (relative imports) gives
  `test_rect_area` at reverse distance 2 from `area`.
- (e) An external root built at the original bytes leases while the repo is edited and passes
  `_check_compatible` against after.
- (f) The launch spy (AC-004 design) counts 0 across (a)-(e).
- Base invariants go to `base-invariants.json`: the tools sha/bytes, 3436/3448/797/7730, and the
  full pytest result at base.

STOP RULE:
- (a), (b), (e) or (f) fails → BLOCKED with the observed facts. Never substitute mocks.
- (c) fails → B ships for seed names only, or is dropped and listed as a limitation; AC-002 rests
  on (d).
- Both (c) and (d) fail → BLOCKED for AC-002.

Day 1 PM — `validation_limits.py`, `validation_coverage.py`, `test_s22_support.py`.

Day 2 — `validation.py`, `validation_schema.py`, CLI, `test_s22_contract.py`.

Day 3 — `test_s22_semantics/cli/isolation/eval.py`, the eval script + evidence, docs, gates in
the lock-faithful dev image (rebuild from the branch), then RUN-01/02.

Cut order (each cut becomes a listed limitation):
1. doc depth / README polish;
2. `also_observed`;
3. eval breadth (keep B0/B1/B2 on one edit);
4. header detection → `body` iff `line > ref.line`;
5. B probes of impacted (non-seed) entities;
6. externals capped at 1.

Never cut: the matching rule, stale exclusion, `validate_plan`, the no-exec gates,
G-IMPACT-PARITY, `plan_id` determinism, the invariance gates.

### Independent checker conditions (binding; override conflicting plan text)

Checker `atlas-s22-plan-checker` (read-only review of this plan against base 4843ddc): APPROVE_WITH_CONDITIONS, no BLOCKER. Review record: `.spectra/plans/aci-s22.review.json`. Conditions 1-10 below are binding.

1. MAJOR. The AC-005 oracle "no evidence cites any assertion/evidence/occurrence id present in C's DB" is unsatisfiable.
   - Native test call assertions, evidence and occurrences are content-addressed (codegraph.py:4768-4829). `tests/*.py` bytes are unchanged, so C holds the same ids that structural B evidence cites from the after DB.
   - Condition: restrict the oracle to coverage-record ids (`test_executes` assertions, `execution` occurrences, coverage producers, as `coverage_py.record_ids` or `test_s21_support.coverage_records` select) plus C's `generation_id`/`snapshot_id`. Also assert zero `observed_execution` evidence.

2. MAJOR. The `duplicate_source` rule collides with the plan's own fixtures.
   - Generation ids are content-hashed (codegraph.py:2821-2839). Build+import run a at the original bytes in a second root gives the same `generation_id` as R0. This is the same equivalence test_s21_isolation.py:320 relies on.
   - So the AC-005 positive control "R0-at-original as before, R1, C" is rejected if R0 carries import a. Likewise, AC-006's two externals must be distinct generations.
   - Condition: the positive control's before side is a build-only root at the original bytes. AC-006 externals are C_a (run a) and C_ab (runs a+b), neither equal to R0's generation. Add a test that same-id plus same-bytes yields `VALIDATION_SOURCE_REJECTED: duplicate_source`.

3. MAJOR. The `refs`, assertion and execution reads are bounded only by row caps, and some are over-broad.
   - D4 step 3 bulk-loads all `evidence` and `execution` occurrences by path. That includes every native reference proof in the file, uncharged.
   - D5 `ORDER BY path,line,col` on a hot name sorts all matches before any cap.
   - Condition: fetch evidence, occurrence and producer by primary key from each charged assertion row (`ev.get`). Charge every row loaded. Use `LIMIT remaining+1` on the `refs` query. The `test_executes` query must use the literal expression `json_extract(payload,'$.target_entity_id')` so it hits `idx_assertions_target_relation_id` (evidence.py:35-38). A test asserts that with `EXPLAIN QUERY PLAN`.
   - Condition: post-propagation phases must not call `work.check()`, `remaining_ms()` or any wall clock. They are bounded by counters only. Keep separate counters; `ImpactWork.counts` is closed and has no `observation_rows_read`.

4. MINOR. Line-number matching between two numbering schemes.
   - Tree-sitter counts `\n`; `coverage_py.line_spans` splits on `\n`, `\r\n` and `\r` (coverage_py.py:409-423). They disagree on files with lone CR.
   - Condition: match by byte overlap of the occurrence span `[start_byte,end_byte)` with the ref span, and expand the ref start to the physical line start. A node's start byte excludes indentation, so plain containment would drop an indented `def` header. Derive `header_end_line` from the same `line_spans` of the same bytes. Keep lines for display only. Boundary tests become byte-based.

5. MINOR. The plan claims "private coupling stays diff internals via impact only", but it also uses `impact._probe_relations` (D5) and `diff._check_compatible` (D3).
   - Condition: reimplement `_probe_relations` locally (3 lines), with a unit test against `impact._probe_relations` for several relation sets. List every private symbol used in the module docstring. G-IMPACT-PARITY fails loudly with a message naming them.

6. MINOR. CLI and error-mapping divergence from `impact`.
   - `impact.run` opens sides with `symbol_diff._open` (`DiffUsageError` gives exit 2; __main__.py:666). `impact.open_side` maps the same error to `index_unavailable` (exit 1).
   - Condition: sides use the `impact.run` mapping. Only externals use `open_side`.
   - Flag names mirror impact: `--max-changed-files`, `--max-diff-entities`, `--max-diff-occurrences`.
   - Never copy impact's `except (ValueError, OSError): ClickException(str(exc))` (__main__.py:675), which leaks paths.
   - `--coverage-graph-root` uses `click.Path(file_okay=False)` without `exists=True`, so a missing root maps to `index_unavailable`.
   - `validation.run` uses `asyncio.run` like `impact.run`, so S22 contract tests must be sync functions.

7. MINOR. G-VOCAB (D13 and the tests table) is underspecified.
   - `diff.UNKNOWN_REASONS` contains `uncovered_source_text`, a substring collision with `covered`. Plan keys such as `coverage` and `coverage_sources` are legal.
   - Condition: tokenize on non-alphanumerics and ban whole tokens only. Scan keys, schema `enum` entries and values of closed-enum fields. Exclude free text (`name`, `path`, selector values). Add a positive control that a planted `covered` enum value fails.

8. MINOR. D6 leaves `method` undefined when one structural recommendation has both A and B evidence.
   - Condition: `method` is `reverse_path` if any A evidence exists, else `same_name_reference`. Both evidence kinds may coexist. `validate_plan` enforces this consistently with `basis_order` 3 or 4.
   - D11 says "no timestamps", but D9 emits `report_timestamp`. Reword: input-derived and deterministic.

9. MINOR. The eval precedent is wrong.
   - `atlas/evals` has no S20 comparison; S20's lives in tests/slices/test_s20_baseline.py. The only script-loading precedent is importlib spec loading in test_s17_runner.py:16.
   - Condition: `test_s22_eval.py` loads the script by importlib spec. ruff covers `tests/`, so the script must be ruff-clean if imported. State which gates (ruff, mypy) cover `atlas/evals`.

10. MINOR. AC-004 spy hygiene.
    - The liveness control `subprocess.run(["true"])` raises under the spy, so catch it, then reset. First assert `subprocess.Popen is spy`.
    - Install the spy only after all builds, imports and `git init` (`same_path_pair` defaults to `git_repo=True`). Add `posix.fork`, `os.spawn*`, `os.exec*` and `asyncio.create_subprocess_*`.
    - Place `tests/test_sentinel.py` in the workspace before the R0 build, so R0 and R1 are identical aside from the edits. It must not call `clamp` or `total`.

## Stories

As an agent operator reviewing a change, I want a ranked list of tests to run, each with exact
execution or structural evidence tied to the changed span and snapshot. Then I can validate
efficiently without mistaking pre-change coverage for post-change validation.

As a reviewer, I want uncovered changed spans exposed as explicit gaps, with closed reasons, and
stale coverage reported but never used. Then a short recommendation list is never read as
permission to skip CI.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s22.criteria.md` (sha256 `bf2e698f…c383cab`), verbatim:

```
### AC-S22-001 (event-driven)
GIVEN test T executed the changed span in the exact before snapshot of the requested diff
WHEN validation planning runs
THEN T SHALL be recommended with the before-snapshot execution evidence.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_observed_test_suggestion

### AC-S22-002 (event-driven)
GIVEN a test is suggested only by static relationships
WHEN the plan is serialized
THEN its basis SHALL be labeled structural.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_static_test_label

### AC-S22-003 (event-driven)
GIVEN a changed span has no mapped test evidence
WHEN planning runs
THEN the plan SHALL expose a validation coverage gap for that span.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_uncovered_change

### AC-S22-004 (event-driven)
GIVEN a validation plan is requested
WHEN planning completes
THEN the repository subprocess launch count SHALL be zero.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_no_execution

### AC-S22-005 (unwanted-behavior)
GIVEN available coverage matches neither the declared before snapshot nor the after snapshot
WHEN planning runs
THEN those observations SHALL not be treated as matching-build coverage.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_stale_coverage

### AC-S22-006 (event-driven)
GIVEN impact, evidence and ranker configuration are unchanged
WHEN planning is repeated
THEN the canonical recommendation order SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_validation_replay

### AC-S22-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S22-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

Test setup:
- All tests live in `mcp-server/tests/slices/test_s22_contract.py`, using real builds via
  `CodeGraph.build()` and real imports via `test_s21_support.import_run`, with every graph root
  external and all roots at the same tmp repo path.
- Fixture COV: copy `tests/fixtures/coverage/project` → build R0 + import run a (original bytes)
  → edit `calc.py` (`clamp` body, `total` body, `never_called` body; same line count) → build R1.
- Fixture PKG: the inline package of Day-1 (d) via `same_path_pair`.
- No mocks of the behavior under test. Every `all(...)` is preceded by a non-empty assertion.

| AC | Test | Observable |
| --- | --- | --- |
| AC-S22-001 | test_observed_test_suggestion | COV, plan(before=R0, after=R1) (library and CLI bytes equal). The observed nodeid set mapped to `clamp`'s change_record_id == the labels.json run-a nodeids with path calc.py and line in 43..48 = {test_clamp, test_clamp_param[0], [5]}. The `test_clamp` recommendation has `basis "observed_execution"`, `observed_in == {"before": true}` and `post_change_validation == false`. Its evidence has `matched_side "before"`, `matched_snapshot_id == R0 lease snapshot_id != R1's`, `coverage_generation_id == R0 generation_id` and `run_id == test_s21_support.run_id("a")`. Its assertion/evidence/occurrence/producer resolve via `ev.get` in the R0 DB: relation `test_executes`, source name == nodeid, occurrence `source_sha256 ==` the before ref hash `== sha256(original calc.py)`, and line within the span. Span state is `observed_before_only`. |
| AC-S22-002 | test_static_test_label | (i) PKG (no coverage): `test_rect_area` has `basis "structural"`, `method "reverse_path"`, `distance 2`, and every hop id exists in the after DB. The non-test `rect_area` is never recommended. (ii) COV edits on a build-only pair: `test_clamp`/`test_clamp_param` are `structural` with `same_name_reference`. (iii) In the AC-001 plan, structural records stay `structural`. For every structural record in the serialized CLI bytes: `observed_in == {}`, `post_change_validation false`, no `run_id`/`coverage_*` key. `validate_plan` rejects a copy relabelled `observed_execution` and a copy with basis `"static"`. |
| AC-S22-003 | test_uncovered_change | COV plan: `gaps` has the `never_called` change_record_id, `basis "unsupported"`, `action "inspect"`, `coverage.before == "no_observation_in_span"`, `coverage.after == "no_coverage_source"`, `structural == "none"`, reasons ⊇ {no_observation_in_span, no_coverage_source, no_structural_test}. `clamp`/`total` are absent from gaps. Build-only pair with an unreferenced added function → gap with `no_coverage_source` on its side. With `--max-gaps 1`, `gaps_total` is unchanged and `selection.gaps` is true. |
| AC-S22-004 | test_no_execution | The spy records then raises on `subprocess.Popen`, `_posixsubprocess.fork_exec`, `os.{fork, forkpty, system, popen, posix_spawn, posix_spawnp, exec*, spawn*}` and `pty.fork`. Liveness control: `subprocess.run(["true"])` counts 1 and is reset. Then library + CLI plans run on COV with an external root → success, non-empty recommendations, launches `== []`. The repo copy holds `tests/test_sentinel.py`, which writes a marker on import: no marker, and no new `app*`/`tests*` entries in `sys.modules`. G-RO inventory of the repo and all roots is unchanged. |
| AC-S22-005 | test_stale_coverage | Build C at the original bytes + import a; edit v1 → build B, edit v2 → build A; plan(B, A, `--coverage-graph-root C`). C's `files[app/calc.py].classification == "stale"` and `stale_observation_rows > 0` (non-vacuous: C really holds rows in `clamp`'s lines). There are no `observed_execution` records. No evidence cites C's generation_id, or any assertion/evidence/occurrence id present in C's DB. `clamp` span coverage is `stale_only` on both sides, with gap reason `stale_coverage_only`. Positive control: plan(R0-at-original as before, R1, C) → C classified `matches_before`, used with `source "external"`. |
| AC-S22-006 | test_validation_replay | COV + PKG plans with two externals. Two in-process runs and a CLI run give identical bytes, recommendation-id sequences and `plan_id`. Frontier commitments are asserted equal (deadline guard). Rebuilding and re-importing everything into fresh roots gives equal generation ids and identical bytes. Externals passed in reversed flag order give identical bytes. `--max-recommendations 1` gives a prefix of the same order. `--max-depth 1` changes `plan_id`. `validate_plan` (strict rank order) passes. |
| AC-S22-RUN-01 | gate | SHA-256 manifest of declared tracked implementation files equal before/after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths |
| AC-S22-RUN-02 | gate | `slice_id` + `criteria_sha256` equal in interrupted and resumed receipts; one authoritative receipt path `.spectra/state/aci-s22.execution.json` |

Supporting tests:
- `test_s22_semantics.py`:
  - matching boundaries (line == ref.line and == end_line included; outside excluded; hash ≠ →
    excluded);
  - after-side coverage (before = edited, after = original + run a) → `post_change_validation
    true`, state `observed_after`, `basis_order 0`;
  - removed seed → before-only span, added seed → after-only;
  - unknown one-sided → `not_applicable`;
  - Ruby change → `unsupported_language`;
  - the test-rule table;
  - `header_end_line` for a decorated `add` (lines 18/19);
  - `span_limit`, `seed_limit` → structural `incomplete`;
  - `reference_probe_limit`, `observation_limit` (limit 0);
  - `also_observed`;
  - G-IMPACT-PARITY;
  - the complete diff → impact → test path (issue step 4).
- `test_s22_cli.py`: option resolution, canonical stdout, exit 1 (unleasable root, same-namespace
  or duplicate source) with empty stdout and no tmp path in stderr, exit 2 usage/limits.
- `test_s22_isolation.py`:
  - G-MCP: tools JSON sha/bytes == 94f9567a…/1631472; `READ_ONLY_TOOLS` has 7 entries;
    `validation-plan` is in `cli.commands`.
  - G-ENVELOPE: 3436/3448/797/7730.
  - The AST deny-list (D13).
  - G-VOCAB: no key/value/enum token in the plan or schema among {covered, verified, passed,
    proven, safe, skip, skipped, guaranteed}.
- `test_s22_eval.py`: runs the eval and asserts only fixture-constructed facts.

Eval (`atlas/evals/validation_plan_comparison.py`, minimal labelled comparison, S20 precedent):
- Truth: labels.json run-a nodeids with a body line in each edited span (`clamp` → 3, `total` → 1,
  `never_called` → 0).
- Baselines:
  - B0 run-all (every run-a nodeid);
  - B1 structural-only (build-only pair);
  - B2 plan (R0 coverage + structural).
- Reported per baseline: |selected|, TP/FP/FN, precision and recall at function granularity
  (`[param]` stripped), plus gap count.
- Honesty notes:
  - B2's observed recall is consistent by construction, because it uses the same run as the
    truth;
  - B1 against the truth is the informative number;
  - there is no superiority threshold.
- Writes `.spectra/evidence/aci-s22/plan-comparison.json` when that directory exists.

Gates beyond the ACs:
- G-PRODUCER: `git diff --exit-code 4843ddc -- mcp-server/src/atlas_aci/{codegraph.py,storage/,
  extractors/,providers/,indexing/,resolvers/}`; generation-id pin tests pass unchanged.
- G-ENGINE: `git diff --exit-code 4843ddc -- mcp-server/src/atlas_aci/query/{impact.py,
  impact_limits.py,impact_work.py,impact_admission.py,diff.py,diff_syntax.py,indexed.py,
  traversal.py,admission.py,paging.py}`.
- G-SURFACE: `git diff --exit-code 4843ddc -- mcp-server/src/atlas_aci/{protocol,tools,server.py,
  enforcement.py,config.py} mcp-server/pyproject.toml mcp-server/uv.lock`.
- All `test_s19_*`, `test_s20_*`, `test_s21_*` pass unchanged.

Commands (record exits; no required skips):
```sh
cd mcp-server
uv run pytest tests/slices/test_s22_contract.py -q
uv run pytest tests/slices/test_s22_*.py tests/slices/test_s20_*.py tests/slices/test_s21_*.py tests/slices/test_s19_*.py -q
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/
```

## Confidence

Computed with `ramza-score`. Composition reuses tested S19/S20/S21 machinery with no edits to
it, and producers are untouched, so generation ids, envelopes and the tools JSON cannot churn.
Matching is a pure equality on file bytes that S21 import already binds.

Main uncertainties, all covered by Day-1 checks with a stop rule:
- how Python attribute-call refs/assertions are spelled for option B;
- compatibility of external roots;
- usefulness of pytest-default test detection on real repositories;
- the header heuristic.

## Rejected Alternatives

- MCP `graph_query op=validation_plan`: it breaks the tools-JSON and op-count pins, needs
  protocol/results/server edits and a new admission minimum outside the declared paths, and
  reverses S21's no-MCP decision for coverage.
- Calling `impact.compute` and post-processing its envelope: the result is truncated by
  `max_bytes`/element cap (not a complete collection), and reusing it would still need the loaded
  sides, which doubles the change-set and parse cost.
- Adding an exported `collect` to impact.py: it churns an S20 module (G-ENGINE) for no
  capability gain; composition + G-IMPACT-PARITY suffices.
- Matching by generation id instead of file bytes: this rejects valid external coverage of
  unchanged files. Matching by path/line only admits stale coverage (violates AC-005).
- Merging nodeids into tree-sitter test entities: ambiguous for parametrized/dynamic tests, and
  S21 rejected it.
- Option C path heuristics: no evidence, only noise.
- Verifying recommendations with `test_dry_run`: executes repository code (AC-004; out of scope).

## Risks

- Option B spelling differs (qualified raw_target, module-level source) → Day-1 (c). Fall back to
  seed-only probing or reverse-path only, listed; never fabricate.
- External roots built by a different producer get rejected (`producer_mismatch`) → fail-closed,
  documented ("rebuild the coverage root with this Atlas").
- Same-name over-approximation on common names (`run`, `get`) → lower rank, `method` label,
  counted against `max_reference_rows`, documented.
- Header heuristic misses (multi-line decorators, nested defs) → it only affects ranking and the
  `declaration_only` state; documented; cut item 4.
- The propagation deadline can truncate on large repos → `impact_work_limit` makes the plan
  `incomplete`; replay holds only with equal frontiers.
- The private `diff` coupling via impact → parity test.
- Timebox 3 days → report completed checks and gaps with a narrow follow-up; no silent growth.

