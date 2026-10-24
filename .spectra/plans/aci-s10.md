---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T16:01:37Z
slice_id: ACI-S10
issue: https://github.com/Rynaro/atlas-aci/issues/32
---
# ACI-S10 — conservative affected-binding invalidation

## Scope

Implement live issue32 on merged base ea9afb9e2636f581e1c1cffd9b18e50ae9d891c3,
branch aci-s10 at /tmp/atlas-aci-aci-s10. Marker/criteria verified; preflight found
no S10 state/receipt and verified COMPLETE S09's183 current digests plus S07's332
historical output digests at its recorded commit and both output ancestries.
Exact criteria SHA205a19c67aa13eb06b6e2c61b1fa39c12837d9443e5b4da9cfd576ed72501767.

In: persisted resolution dependencies, affected per-file binding work, removal of
superseded bindings and explicit conservative full fallback. Out: new semantics,
resolver options, external package ingestion, general dependency engine, new
services/dependencies, performance thresholds or promise every incremental run
is faster. Preserve S02 ownership/language checks, S03 leases/publication, S07
partial/history/provenance and S09 extraction-only cache behavior.

Implementation paths:
- mcp-server/src/atlas_aci/resolvers/__init__.py (inert)
- mcp-server/src/atlas_aci/resolvers/dependencies.py
- mcp-server/src/atlas_aci/codegraph.py
- mcp-server/tests/slices/test_s10_*.py
Narrow amendments, recorded before execution:
- mcp-server/src/atlas_aci/storage/evidence.py: add EXACT current resolver module
  fingerprint set while preserving exact epoch6/S07/S09 producer allowlists.
- README.md: documented coarse invalidation granularity and fallback diagnostics.
- .spectra/plans/aci-s10.*, .spectra/plans/ramza-calibration.jsonl,
  .spectra/evidence/aci-s10/*, .spectra/state/aci-s10.execution.json,
  .spectra/changes/aci-s10/*, .spectra/backlog/execution.json and order.md.
No indexing-module change is needed merely because the issue permits that path;
any additional observed product/test path needs a narrow amendment first.

Actual rightsize FULL5 (eight estimated files; public build diagnostics; persisted
reuse decisions crossing fact-integrity boundary; new dependency witnesses; medium
stakes). Complexity9: scope2/ambiguity2/dependencies3/risk2. Standard routing;
P0 alone does not activate TRANCE. Independent ATLAS plan review required.
DISCOVER/CLARIFY unnecessary for known scope. No paid model runs or external
memory retry/persistence; prior blocked destination remains terminal.

## Approach

### Existing oracle and narrow seam

Current _resolve_edges3238–3244 deletes ALL active edges/assertions and clears
ALL reference reasons;3245–3268 constructs global symbol/blocker/import/module
inputs;3350+ binds every ref and3536+ every import. _run_build2315–2317 always
runs rationale resolution and strict projection validation. This safe full rebind
is the oracle; keep it available for full indexing and explicit fallback.

Do not cache resolved outputs in S09 packets. Extract/current raw facts still
come from actual source or validated extraction cache, and failed-source history
remains separate. Refactor only enough to allow resolving a selected set of source
paths while preserving existing full behavior and global lookup context.

### Conservative dependency representation

Binding unit is one active source path containing refs/import bindings/rationale;
track relevant zero-work indexed files too for module/package presence and cleanup.
Store a versioned atlas-binding-dependencies.v1 witness in the published SQLite
manifest, not another external cache/database. Per-unit record contains language,
local input digest, dependency bucket IDs/digests and prior output digest. Header
contains exact resolver producer/schema/configuration profile and complete unit
inventory. Canonical encoding, sorted lists and explicit closed keys/types.

The chosen granularity is COMPLETE language-level resolver-input buckets, not a
claim of exact per-reference minimal impact. For each language fingerprint all
resolver-visible current declaration fields: name/kind/path/line bounds/language,
parsed binding context and entity identity. Exclude SQLite IDs and occurrence/
evidence IDs NOT read by target selection; include every actually consumed field.
Fingerprint assignment/scope/blocker facts and import-binding descriptions/order
for that language, including wildcard blockers. Python additionally depends on
complete indexed Python path/package/module membership, including empty __init__.py
and simultaneous module.py/package/__init__.py alternatives. Include current stale
(language,name) universe used by stale_target reasoning. Local input digest covers
all raw refs/imports/rationale and source entity/proof links consumed for outputs;
exclude derived unresolved reasons/assertion IDs from INPUT digests.

These complete buckets cover POSITIVE and NEGATIVE queries: a previously absent
name/module/export, new ambiguity, wildcard/blocker addition/removal and failed-
source disappearance cannot hide behind old resolved edges. Every unit depends
on its full language bucket even if its current result is unresolved or empty.
Python target-export blockers are cross-file inputs, not merely caller-local.
Global relationship classification constants/lookup code are covered by producer
fingerprints. If any consumed dependency cannot be represented, mark impact
unknown and perform full rebind; do not approximate by known target edges alone.

Current resolvers read raw facts, not other units' resolved assertions. Thus the
closure is changed raw-input buckets -> all dependent file units, plus changed/new/
deleted units; no transitive fixed-point engine is required. If future behavior
starts consuming bound outputs, this profile must fall back until extended.

### Affected work and preservation

Capture predecessor witness/outputs from the staged predecessor BEFORE extraction
mutates tables. After extraction/history quarantine, compute current input digests.
Reuse requires incremental mode, compatible extraction/resolver producer/config,
valid complete predecessor witness, unchanged local and bucket digests, and exact
still-present output digest/closure. A missing output cannot be labeled reused.
The original full-build path always performs full binding, even with warm extraction.

Affected set: new/changed units, all units whose bucket changed, removed/failed/
disabled units needing cleanup, and units whose stored output integrity fails.
Unknown/incompatible witness conditions invoke FULL fallback, not a guessed subset.
Remove affected units' edges/assertions by SOURCE path; clear/recompute only their
reference reasons and import/rationale assertion links. Do not delete all assertions
then pretend unaffected work was reused. Preserve untouched valid outputs exactly.
Remove outputs of vanished paths even when extraction already removed their raw
rows. Rebind affected refs/imports/rationale against current GLOBAL raw-fact indexes;
never retain an old target merely because the caller source bytes stayed constant.

Rationale belongs to the same source-path work unit; preserve/recompute it coherently.
Existing build result counts (such as edges) remain TOTAL current counts, not only
recomputed counts. Add separate operational diagnostics for mode full/affected,
reason, affected/reused/removed unit counts (and deterministic path sets if useful).
No timing promise or TTL. Actual witness persistence and all output validation
happen in the same staging transaction before publication. Any fatal error keeps
G1 usable; existing reader leases and shared writer lock remain authoritative.

Metadata absence (old/imported index), malformed/partial/unknown profile, producer/
configuration mismatch, unresolved dependency representation or incomplete unit
coverage yields an explicit stable fallback reason and full binding. Do not make
readonly queries repair/create metadata; an index remains queryable without it.
The witness is operational, excluded from canonical facts, generation/status
identity and portable export. Import preserves source provenance but has no trusted
local witness; next indexing re-establishes it through full binding.

### Honest selectivity and granularity

Declaration/scope/blocker/module-universe changes may rebind every unit of that
language; this is deliberate conservative over-invalidation. Still prove genuine
WITHIN-LANGUAGE reuse: on a real call-only edit that leaves target-selection
buckets unchanged, recompute the edited path and preserve another live binding
unit in the SAME language. Use two Python files with stable declarations and
same-width call-name edit, or a declaration-free caller fixture if needed; inspect
actual bucket inputs, do not weaken the equality predicate to force the test.

Instrument real per-unit binding execution with a wrapping spy/counter while
executing actual resolver logic; assert the edited unit ran and the unrelated
same-language unit did not. Retained unit must contain a real assertion/reference,
not an empty file. An unrelated-language-only example is insufficient. Separate
negative tests prove missing/ambiguous/stale candidates invalidate on additions.
A no-op retry should reuse all eligible units; full fallback still recomputes all.

### Producer and integrity boundary

Include resolvers/dependencies.py bytes in _producer_inputs module hashes; __init__
is inert. storage/evidence.py accepts only exact historical two-module S07 set,
three-module S09 set and four-module current set, plus existing epoch6 legacy shape.
No arbitrary module-name allowance or historical producer rewriting. Current reuse
requires the current exact full fingerprint. S09 packet invalidation after this
producer change is expected once; cache remains extraction-only afterward.

No schema/DDL/epoch change is needed for a manifest witness. Validate closed fields,
exact bool/int distinctions, normalized paths, unique unit/dependency keys, hash
formats and complete inventory. Recompute current dependency/output hashes; a
syntactically valid hash string or rehashed outer container alone is insufficient.
This is coherence of owner-local derived state, not cryptographic authenticity
against a writer able to forge all facts and witnesses. Invalid witness falls back;
it never weakens S07 canonical evidence/import validation.

## Stories

As an operator I need incremental changes to update all affected bindings,
including formerly missing targets, while retaining demonstrably unaffected work.
One Vivi executor; exact issue timebox FIVE engineering days. Stop and report
remaining scope at cap; no silent dependency engine or performance project.

1. Baseline actual edit sequences against current full-binding oracle; add a real
   failing selectivity/persisted-dependency test before code (equivalence may already
   pass because baseline always rebinds). Record both facts honestly.
2. Implement pure witness/bucket planning and selected per-path binding/cleanup;
   keep full oracle/fallback. Add exact current producer set and diagnostics.
3. Exercise positive/negative/fallback/lifecycle controls, full static/test/CI gates,
   then receipt/reentry and root-owned publication. No mock facts or DB results.

## Acceptance Criteria

Exact live issue criteria, unmodified:
### AC-S10-001 (event-driven)
GIVEN an imported declaration changes
WHEN incremental indexing completes
THEN its canonical graph SHALL equal a clean rebuild of the new source.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_export_change_equivalence

### AC-S10-002 (event-driven)
GIVEN a referenced file is deleted
WHEN incremental indexing completes
THEN its canonical graph SHALL equal the deletion fixture clean build.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_delete_equivalence

### AC-S10-003 (event-driven)
GIVEN a file is renamed without changing its declarations
WHEN incremental indexing completes
THEN its canonical graph SHALL equal the rename fixture clean build.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_rename_equivalence

### AC-S10-004 (event-driven)
GIVEN resolver configuration changes
WHEN incremental indexing completes
THEN its canonical graph SHALL equal a clean rebuild with that configuration.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_resolver_config_equivalence

### AC-S10-005 (unwanted-behavior)
GIVEN the dependency closure cannot be determined
WHEN incremental binding is requested
THEN Atlas SHALL use the documented conservative rebuild path.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_unknown_dependency_fallback

### AC-S10-006 (event-driven)
GIVEN an incremental update completed successfully
WHEN the same update is retried
THEN the canonical graph SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_incremental_replay

### AC-S10-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S10-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

Six named product tests in tests/slices/test_s10_contract.py; additional dependency
controls in test_s10_dependencies.py. Fixtures created in tmp_path unless a justified
fixture path amendment is recorded first.

- Real Python relative import declaration edit; delete; rename; same-name owner/
  language collisions; calls and imports remain distinct; independent expected
  targets/reasons plus incremental-vs-full canonical equality.
- Add/remove ambiguous target; absent module appears; empty package marker appears;
  module.py plus module/__init__.py ambiguity; export becomes conditional; alias/
  scope/wildcard blocker addition/removal; class/owner/member changes; stale failed
  target and recovery. Previously unresolved refs must become resolved or change
  reasons exactly like full build. No resolved-edge-only dependency graph.
- Actual configuration change uses existing selected languages/skip patterns or
  producer fingerprint, NOT a new public resolver option. Config/producer mismatch
  produces explicit full fallback and matches full build under changed inputs.
- Missing/corrupt/truncated witness, duplicate/missing unit, unknown profile, stale
  output hash and unresolved dependency capability invoke full fallback with real
  binding execution. Valid same-language unaffected-unit reuse control prevents
  “always full” from satisfying the feature. No-op retry retains graph identity.
- Compare ALL current canonical entity/occurrence/evidence/assertion records,
  raw/projection references including unresolved reasons, imports, assignments,
  rationale and public query results; normalize only operational row IDs/counters.
  Current source hashes/spans/candidates/direction may never be normalized away.
- Preserve S07 history semantics: for all-valid edit sequences cold clean rebuild
  equality includes full portable bytes. For failed/stale sequences compare against
  FULL rebind from a clone of the SAME predecessor in a separate graph root for
  the SAME source namespace, then compare history too. A fresh source-only build
  lacks predecessor evidence; compare current graph separately and assert historical
  closure preservation rather than erasing history to manufacture equality.
- Reused outputs reference current valid source/proof records; changed source/
  target paths leave no superseded assertions or dangling links. Partial publisher
  and real injected binding failure retain old G1; concurrent actual writers and
  held reader leases remain coherent. Source mount :ro + external graph root has
  no source writes; serving/status/export/import must not create witness metadata.
- Strict old/new producer shape controls, S09 warm extraction parser-count tests,
  S07 current/history portability and legacy query compatibility remain valid.

From mcp-server with UV_CACHE_DIR=/tmp/aci-s01-uv-cache and
PATH=/tmp/aci-s01-tools/bin:$PATH:
uv sync --frozen
uv run --frozen pytest tests/slices/test_s09_contract.py tests/slices/test_s07_parser_prerequisite.py -q
Capture baseline and genuine new selective-reuse red test before product changes.
Then:
uv run --frozen pytest tests/slices/test_s10_contract.py tests/slices/test_s10_dependencies.py -q
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src
Required real Docker/readonly and canonical macOS/Linux CI jobs remain; root can
supply scoped Docker escalation. Record actual counts/no required skips; do not
repeat broad gates without changed code/failure or unresolved concern.

From root, same uv PATH/cache:
python3 scripts/verify-probe-verdict.py .spectra/changes/aci-s07-probe/aci-s07-probe-lpa-vs-louvain.json
bash scripts/test-verify-probe-verdict.sh
bash scripts/test-verify-export-size.sh
Expected behavioral fingerprint0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f
must be measured unchanged; diagnostic witnesses are not graph semantics. If it
changes, diagnose actual graph drift; never re-sign old S07 metrics. No new export
size/performance criterion; S07 resource measurements remain honest known costs.

## Verification layers and independent checker

Three views: raw fact delta -> complete dependency buckets -> affected units;
source-path outputs -> selective cleanup/recompute -> strict canonical validation;
predecessor witness -> staged atomic publication -> coherent next reader. Shared
invariants: all actual consumed inputs modeled, negative lookups included, history
separate, unknown falls back, same-language real reuse and full oracle equivalence.
Adversarial constraints are listed above. Independent ATLAS full lint/EARS8 and
actual refine score/identity/timestamp precede Assemble. Maker ramza-backlog-order-s10
cannot self-check. Plan approval is not execution success or a fabricated benchmark.

## Receipt and backlog continuity

Ledger records actual S09 PR72 merge ea9afb9, eight merged/34 remaining; current32,
next11 (ACI-S15 Rust). Frozen queue unchanged. Root owns validated PR/CI/merge/main
sync under standing user authorization. Coder never self-merges; no paid runs,
release or deployment implied. Prior criteria, plans, receipts and probes immutable.

Before code create the one .spectra/state/aci-s10.execution.json IN_PROGRESS receipt
with live slice/criteria identity/base/declared scope/eight AC result slots and
output_commit=null. Preserve interrupted snapshot, compare resumed identity RUN02.
Adapt S09 verify_execution.py/verify_resume.py into NEW S10 evidence paths; no new
runner framework and no edits to prior scripts/receipts.

Declare these EXACT metadata-only output_digest_exclusions in bookkeeping-amendment
before receipt initialization: authoritative .spectra/state/aci-s10.execution.json;
.spectra/evidence/aci-s10/final-reentry-before.json; final-reentry-after.json;
final-reentry-summary.json; final-resume-identity.json; output-check-tests.log
(all five latter basenames under the SAME .spectra/evidence/aci-s10/ directory).
They necessarily follow output commit; exclude them from output_digests AND output-
commit diff comparison. All product/tests, stable pre-output evidence, plan/frozen
criteria and the bookkeeping amendment remain hashed. No broad wildcard exclusions.
Reentry itself requires a clean checkout including metadata and exact frozen
criteria. Any additional post-output artifact needs precise amendment before writing.

After all product gates, commit bounded implementation and stable evidence; record
its SHA/digests in receipt, then a metadata-only commit. Read-only clean reentry
checks exact tracked implementation hashes and git diff versus output SHA using
those exact exclusions, returning ALREADY_SATISFIED without product edits (RUN01).
Root publishes only after verified receipt and green CI. Timebox failure reports
actual gaps, never COMPLETE by planning or issue closure alone.

## Confidence

Not yet computed; actual independent review and bounded decomposition determine
Assemble score. Product selectivity/equivalence remain unverified until execution.

## Rejected Alternatives

Actual Explore: complete coarse buckets/per-file units86.5 selected; fine-grained
per-query engine75 has more invalidation surfaces and exceeds necessary granularity;
full rebind with diagnostics77 preserves correctness but does not implement actual
affected-work reuse. Positive-only target edges, dropping failed history and cached
resolved S09 packets violate the required invariants and are not alternatives.

## Risks

| Risk | Required boundary |
| --- | --- |
| Missing negative/cross-file input | Complete language/module/blocker/stale buckets; addition controls; unknown full fallback. |
| Witness claims reuse after outputs were deleted | Verify output closure/digest; selected cleanup only; real retained assertion control. |
| Over-invalidation mistaken for precise minimal impact | Document coarse bucket granularity; no speed promise; prove same-language local reuse. |
| History differs from fresh source-only build | Equal predecessor oracle or separate current/history assertions; never erase evidence. |
| Selective cleanup corrupts canonical links | Per-source unit outputs, strict projection validation, fatal rollback/G1 tests. |
| Receipt self-hashing | Exact metadata exclusions approved before initialization, all implementation still bound. |

## Assemble handoff

Canonical tools/schemas from original checkout .eidolons/ramza/. Complete actual
critic/confidence/scope/freeze/drift/emission/full schema+format+digest gates.
Envelope paths relative to envelope directory. Root mechanical verify-envelope
and correlated trace before Vivi. Stop planning writes at READY. Planning preserves
all80 preflight product hashes; one implementation writer afterward.
