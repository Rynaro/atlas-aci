---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-23T20:43:16Z
slice_id: ACI-S02
issue_url: https://github.com/Rynaro/atlas-aci/issues/25
---

# ACI-S02 — Ownership and lexical visibility before call confidence

## Scope

Intent: CHANGE. Stop unrelated same-name declarations becoming concrete call
targets. Use structural facts already available from supported parsers; retain
honest unresolved evidence when ownership or visibility is unavailable.
No type inference, inheritance resolution, framework semantics, new grammars,
general compiler, or cross-repository/module resolver.

Base `feec071f0f1cfbb967fe1849065f23f4e6713120`, clean main after PR #66.
Worktree `/tmp/atlas-aci-aci-s02`, branch `aci-s02`.
Live issue stable marker and criteria digest verified; no predecessor or existing
S02 execution. S01 and S05 artifacts remain immutable.

Product scope:
- `mcp-server/src/atlas_aci/codegraph.py`
- `mcp-server/tests/slices/test_s02_*.py`
- `mcp-server/tests/fixtures/*`
Planning and evidence: S02-named artifacts under `.spectra/`.

### Scope amendment S02-PATH-01

Permit narrowly justified updates in these existing tests, preserving their
original safety/property coverage rather than deleting failures:
- `tests/test_confidence.py`: unsafe receiver/global binding expectations
  (e.g. 142-159,305-359,641-736) must become unresolved assertions or fixtures
  with valid same-owner declarations. Ruby constant/singleton distinctions and
  assignment shadowing are part of the behavior under correction.
- `tests/test_graph_query.py`: affected resolved-target/count fixtures at
  74,158,297 must describe valid binding or explicit unresolved counts.
- `tests/test_schema_epoch.py`: current epoch/DDL and legacy-column fixtures
  (125-127,393-404) must cover rebuild/read-only mismatch behavior for the change.
- `tests/test_export.py`: strict schema and portable round-trip fixtures
  (384-411) may need new persisted fields/defaults while retaining deterministic
  insertion-order and validation guarantees.
All paths above are under `mcp-server/`. Review each actual failure before
editing; explain changed semantics, do not merely replace numeric counts.
This adds test compatibility scope only; frozen issue criteria are unchanged.

Rightsize: nine estimated files, public query evidence, persisted schema change,
high stakes -> full, score 5. Complexity 9/12 extended (2,2,2,3).
Independent maker/checker review required. Intent/scope are explicit, so
DISCOVER/CLARIFY are unnecessary.

## Approach

### Observed cause and usable extraction facts

ATLAS source audit on the actual base establishes:
- 1408-1412: candidates selected globally by name/kind without language, owner,
  or lexical visibility; 1469-1484 independently finds any class/module with
  receiver text, then marks an unrelated unique candidate EXTRACTED.
- 1427-1448: assignment evidence only demotes confidence while retaining an
  unsupported concrete target. Ruby receiver text is dropped at 1329-1332.
- Match-grouped definition/call nodes already exist at 1158-1217,1272-1288;
  ancestor nodes can supply the minimum exact scope/owner facts.
- Symbol/refs persistence is currently name/location/language-centric; assignment
  captures omit parameters, import aliases, and scope. `_enclosing_symbol`
  line ranges are display context, not a lexical binding model.
- `search_symbol` exposes raw refs. Preserve unresolved reason there.
  Callers/edge APIs expose edges. Audit occurrence-level unresolved counts,
  including mixed resolved/unresolved refs of the same name.
- Schema epoch is 5, with a checked DDL digest. Fresh epoch DBs are index-only;
  read-only serving must never migrate/rebuild.
- Portable export currently covers symbols/edges/rationale, not refs or
  assignments. Do not claim unresolved-reason export retention.

### Bounded implementation sequence

1. First add and run a real parser -> CodeGraph.build -> public query
   counterexample at the unchanged product base: Widget lacks close while Other
   declares close. Capture the actual wrong target/confidence and the failing
   required assertion before editing resolver code. No hand-inserted symbol/ref
   rows or mocked extraction may establish this reproduction.
2. Capture structural declaration owner, receiver form/text, callable category,
   and lexical scope/binding facts from match-grouped AST nodes. Scope identity
   uses path/language plus byte spans/parent relationship, not rowid or smallest
   line range. Same-line siblings and nested scopes must remain distinct.
   Record parameters/import aliases/assignments that can block lookup even when
   the extractor cannot establish a callable target.
3. Resolve language and applicable lexical binding first, then receiver owner
   and instance/static category, then candidates/confidence. A concrete edge
   requires a structurally applicable target. Unknown receiver/import/type/
   dynamic binding produces no concrete edge, even at INFERRED confidence.
   Never fall back to a global same-name method after an owner/lexical miss.
   AMBIGUOUS candidates may contain only otherwise applicable declarations.
4. Retain every raw ref with a deterministic unresolved reason when no supported
   binding is established, e.g. unknown_receiver, owner_has_no_member,
   unknown_import_alias, shadowed_noncallable, unsupported_scope, no_visible_target.
   Define the exact reason vocabulary in code/tests; it need not infer unknown
   semantic facts. Clear/recompute stale reasons on rebuild. Preserve ref order.
5. Persist only required facts; bump SCHEMA_EPOCH and recompute EXPECTED_DDL_HASH
   for DDL changes. Keep index-only new-epoch rebuild and read-only serve
   rejection. Audit explicit export/import column/validation lists: preserve
   portable symbols/edges/rationale compatibility for the new epoch, or add
   explicit validated fields if required. Internal optional metadata may use safe
   defaults. No cross-epoch in-place migration.
6. Preserve public query shapes/confidence enums and supported non-call
   relations. Verify caller source ownership and per-occurrence unresolved count
   with one resolved and one unresolved same-name call. Existing count arithmetic
   can remain only if it is proven correct under relation relabeling and mixed
   occurrences; remove the obsolete uniform-resolution assumption.
7. Run the six product tests plus targeted negatives, full regression/CI checks,
   two-build canonical comparison, schema/read-only behavior, and export/import
   round-trip. Capture an implementation commit and both execution re-entry gates.

### Explicit supported structural subset

| Language | Positive bindings to deliver | Conservative unresolved boundary |
|---|---|---|
| Python | Same-module declarations; nearest enclosing function binding; directly known local class member; self member when self is the unshadowed instance parameter of the enclosing method. | Unknown object type, imported/aliased targets, assignment/parameter shadowing with no callable identity, dynamic/decorated binding whose category is unknown. |
| JavaScript/TypeScript | Same-file function declarations and direct class members; known class receiver selects static methods, this selects the enclosing instance/static category; nearest function/block bindings. | Unknown variable receiver, imported alias, reassignment, uncertain dynamic/computed member, unsupported lexical construct. |
| Ruby | Direct class/module ownership; constant receiver selects singleton/class methods; self/bare method call uses the enclosing class/module instance or singleton context; uniquely identified structural constants/namespaces. | Constant receiver must not bind an instance method; unknown object receiver, dynamic methods, inherited member, ambiguous namespace. |
| Bash/SCSS and existing non-call relations | Preserve currently supported extraction and valid same-language relation behavior; add language separation before candidates. | No newly inferred owner/type semantics or grammar additions. |

Python methods do not lexically inherit the class body's local namespace.
Parameters/imports/assignments create binding blockers for the appropriate scope;
Python function-local assignment blocks outer fallback even before the assignment
line. Unsupported global/nonlocal/comprehension cases remain unresolved rather
than borrowing a convenient outer target.
JS/TS let/const/class block scope differs from var/function scope; temporal-dead-
zone or declaration-order uncertainty must not resolve to an outer same-name
binding. Model simple supported cases; mark unsupported ones unresolved.
Ruby singleton and instance contexts are not interchangeable. Do not infer a
receiver's type from capitalization, a variable name, or unrelated class presence.
Cross-file Python/JS/TS linking requires established module/import evidence;
none is added here. A missing import does not make a declaration globally visible.
Preserve safe local structural positives rather than solving arbitrary source
execution order or runtime monkey-patching.

## Stories

As an Atlas user, I want a call's target to belong to its receiver and visible
scope, so navigation does not confidently point at unrelated code.
One slice, three engineering days total, P0 risk; one standard Vivi executor.
At the timebox limit report observed results and narrowly remaining gaps.
No paid model evaluation or additional implementation agent allocation.

## Acceptance Criteria

Canonical criteria extraction retains complete content lines between marker
lines and final LF, excluding the opening marker's terminating LF.
Authoritative file `.spectra/plans/aci-s02.criteria.md`.
SHA-256 `cdbaf9c448dc62b55c50a4c1326e30e458cc115e1fe7149df903299e1d3a9cb5`.

### AC-S02-001 (event-driven)
GIVEN Widget lacks close while unrelated Other defines close
WHEN the fixture is indexed through the real parser
THEN Atlas SHALL retain Widget.close as unresolved.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_unrelated_receiver

### AC-S02-002 (event-driven)
GIVEN a directly declared method belongs to the referenced receiver
WHEN call binding runs
THEN Atlas SHALL bind the call to that receiver-owned declaration.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_owned_receiver

### AC-S02-003 (event-driven)
GIVEN a lexical name shadows a same-name outer declaration
WHEN binding runs
THEN Atlas SHALL select the declaration in the applicable lexical scope.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_shadowed_name

### AC-S02-004 (unwanted-behavior)
GIVEN only another language defines the same callee name
WHEN binding runs
THEN Atlas SHALL leave the reference unresolved.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_cross_language_collision

### AC-S02-005 (unwanted-behavior)
GIVEN an import alias cannot be resolved from available facts
WHEN binding runs
THEN Atlas SHALL preserve an unresolved reason for the alias.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_unknown_alias

### AC-S02-006 (event-driven)
GIVEN unchanged source and resolver configuration
WHEN the same fixture is rebuilt twice
THEN the canonical resolved-edge set SHALL be identical across builds.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_binding_replay

### AC-S02-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S02-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

Each named product test uses actual source fixtures, parser, indexer, and public
query response. SQL may corroborate facts but may not manufacture the binding
being verified.

- test_unrelated_receiver: capture old Widget.close -> Other.close result before
  fix, then assert no unrelated concrete edge and a retained unresolved ref.
- test_owned_receiver: two distinct same-name owners, only the structurally
  applicable declaration selected. Cover the delivered Python/Ruby/JS/TS forms,
  including Ruby singleton vs instance and JS static vs instance negatives.
- test_shadowed_name: nearest nested lexical callable wins; separately prove
  parameter/assignment blockers do not fall back outward. Include same-line
  sibling declarations, Python method/class-body scope distinction, JS block vs
  function scope, and a local-callable positive.
- test_cross_language_collision: source call has only an other-language
  same-name declaration; no edge, retained source ref.
- test_unknown_alias: actual import-alias syntax creates retained unresolved
  reason even if an unrelated local/global same-name candidate exists.
- test_binding_replay: build twice unchanged and compare canonical resolved
  edges/target identities sorted by semantic fields, excluding durations and
  surrogate row IDs. Also check stable unresolved reasons/order.
- Additional regression: same name with one genuinely resolved occurrence and
  one unrelated/unknown receiver; query unresolved count must be one. Confirm
  source ownership for same-line references is not assigned via line span.
- Retain supported constructors, heritage/reference relations, Bash/SCSS cases,
  response bounds, schema-epoch rebuild/read-only behavior, and deterministic
  export/import round-trip. Export does not promise raw unresolved ref storage.

Three coverage views: runtime pipeline (parse/facts/bind/query/replay), file
ownership (extract/schema/resolve/query/tests), and failure scenarios
(receiver/scope/language/alias/staleness). All cover the six product obligations;
receipt gates separately cover both RUN criteria. Independent checker must
challenge the conservative subset and three-day scope, not assume broad language
semantics from the table.

From worktree mcp-server, use /tmp/aci-s01-tools/bin/uv with
UV_CACHE_DIR=/tmp/aci-s01-uv-cache:
- uv run --frozen pytest tests/slices/test_s02_contract.py -q
- uv run --frozen pytest tests/test_confidence.py tests/test_graph_query.py tests/test_schema_epoch.py tests/test_export.py -q
- uv run --frozen pytest -q
- uv run --frozen ruff check src/ tests/
- uv run --frozen ruff format --check src/ tests/
- uv run --frozen mypy src/
- uv run --frozen ruff check ../scripts/ --exclude fingerprint-fixture
- uv run --frozen ruff format --check ../scripts/ --exclude fingerprint-fixture
- uv run --frozen mypy ../scripts/
- bash ../scripts/test-harden-gate-classify.sh
- bash ../scripts/test-verify-probe-verdict.sh
- bash ../scripts/test-verify-export-size.sh

Use worktree source/environment, frozen dependencies, exact logs/exit codes.
Report required remote/platform CI honestly. Persist red-before-fix evidence
under .spectra/evidence/aci-s02/ with actual baseline revision and command.

## Receipt and re-entry

Before coding initialize only `.spectra/state/aci-s02.execution.json` with
IN_PROGRESS, stable slice/criteria identity, issue URL, base commit, declared
paths, output commit (initially null), output digests, and eight per-AC results.
Preserve a real partial receipt and compare identity on resumed execution for
RUN-02. Do not reset planning state or alter prior slice artifacts.

After all product checks, make a bounded implementation commit and digest
declared tracked implementation files. Re-enter in a clean checkout of that
commit using the verified receipt and existing evidence; require identical
before/after content manifests and git diff --exit-code against output_commit
for declared paths. This is RUN-01. A later receipt-only metadata commit avoids
self-referential commit hashes. Reuse the established receipt verification
approach; do not invent a second generic framework.
COMPLETE requires all eight ACs and applicable gates, actual output digests, and
reviewable bounded changes. No automatic merge/release/deployment.

## Confidence

Independent ATLAS full-tier critique approved with no blockers or prescriptions:
refine score 4.2/5, minimum 3, pass. Actual Assemble confidence is 88.75
AUTO_PROCEED (pattern_match 85, requirement_clarity 95, decomposition_stability
90, constraint_compliance 85). Efficiency remains the principal concern: honor
the three-day limit and prioritize the six product ACs with conservative
unknown states. These are planning readiness results, not product verification.

## Rejected Alternatives

Actual Explore results:
- AST owner/scope filtering 85.5, selected: uses available structural nodes,
  preserves honest unresolved cases, and directly fixes candidate applicability.
- Confidence demotion alone 58.5: keeps wrong concrete targets and repeats the bug.
- Compiler semantic resolver 67: broader correctness potential but exceeds the
  existing-grammar, no-type-inference scope and three-day timebox.

## Risks

| Risk | Mitigation |
|---|---|
| Different language scopes conflated | Small per-language structural rules; explicit unsupported cases and negative fixtures. |
| Same-line declarations confused | Byte-span/ancestor identities and exact call coordinates; no line-only lookup. |
| Imports/parameters/assignments bypass filter | Record binding blockers before selecting outer candidates. |
| Reduced unsafe edges mistaken for regression | Preserve positive owned fixtures and change old assertions with explicit rationale. |
| Persistence breaks serving or portability | Epoch/hash update, index-only rebuild, strict export/import and read-only tests. |
| Broad language ambition exceeds timebox | Conservative subset above, no inferred runtime behavior, report gaps at limit. |

## Checker and handoff

Full-tier phase T review is required before Assemble. Reviewer receives only
plan, criteria and state, runs structural/EARS gates first, then actual
ramza-score refine cycle1 for clarity/completeness/actionability/efficiency/
testability. Return tool verdict, per-dimension findings and prescriptions.
Author identity ramza-backlog-order-s02; checker must differ. A read-only checker
can omit --state and return exact score for orchestrator recording.

After accepted critique: record genuine critic identity/evidence, refine if
needed, advance A, compute confidence, declare scope (including amendment),
freeze criteria once, validate full schemas/digests/envelope, then DONE.
Helpers/schemas: /Users/henrique/workspace/oss/agents/atlas-aci/.eidolons/ramza/.
ECL artifact.path=aci-s02.md and criteria path=aci-s02.criteria.md, relative to
the envelope directory. Orchestrator mechanical verify-envelope --block trace
is required before Vivi receives an emitted envelope.
CRYSTALIUM persistence was previously rejected; do not retry or redirect payloads.
