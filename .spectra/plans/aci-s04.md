---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T00:44:02Z
slice_id: ACI-S04
issue_url: https://github.com/Rynaro/atlas-aci/issues/27
---

# ACI-S04 — Truthful operator capabilities and completeness status

## Scope

Intent CHANGE: one versioned operator CLI JSON status payload distinguishing
recognized extensions, enabled structural extraction, actual relations, unsupported
semantic binding, and failed/stale observations. No MCP tool (S11), new grammar,
provider framework, probabilistic percentage, or generalized partial generation.

Base 7793d62d0ad00cd29ba0be5de53491f15ac28802, clean main after PR #68.
Worktree /tmp/atlas-aci-aci-s04; branch aci-s04. Live marker and criteria hash
verified; no predecessors or prior S04 execution. Previous slice/probe artifacts
and frozen criteria are immutable.

Declared product scope: mcp-server/src/atlas_aci/codegraph.py,
mcp-server/src/atlas_aci/__main__.py, mcp-server/tests/slices/test_s04_*.py,
README.md. S04 planning/evidence/receipt remain under .spectra/.
No additional existing test path is preauthorized; document a narrowly justified
scope amendment before changing any existing test. All S03 invariants must pass.
No DDL/epoch, atlas-generation.v1 input-schema, portable-fact, dependency, or
lockfile change is planned.

Actual rightsize: six estimated files, public CLI contract, medium stakes ->
lite score3. No new dependency, protocol migration, or independent checker
requirement. Complexity8/12 extended (2,2,2,2). Standard routing remains.
DISCOVER/CLARIFY omitted: outcome and constraints explicit.

## Approach

### Evidence and central decision

Actual QUERIES/LANG_BY_EXT/default language registry defines availability, but
capture names alone are insufficient: SCSS @ref.include currently persists
relation=call. Derive capability declarations from registered queries AND actual
extraction handling, cross-checked by source fixtures.
All current providers are Tree-sitter structural extraction/bounded lookup;
EXTRACTED/INFERRED confidence never establishes compiler semantic binding.
JSONL import reproduces portable facts without proving upstream source coverage.

S03 stores closed atlas-generation.v1 inputs and immutable facts. Its source_hashes
must continue to identify bytes that produced retained facts. A failed refresh
must not replace those hashes or mutate the published DB. Choose separate,
generation-correlated failed-attempt diagnostics (alternative A), not partial
publication or a provenance-schema rewrite.

### Implementation sequence

1. Add atlas-aci status --repo PATH producing canonical atlas-status.v1 JSON.
   Construct CodeGraph(read_only=True) directly, acquire reader_lease once,
   inspect only the leased generation and one atomic diagnostic snapshot,
   then release. Do not construct Config/Memex, create directories/locks,
   scan/read current source files, rebuild, checkpoint, or write on status.
   Missing/invalid/legacy generation yields a structured unavailable status with
   INDEX_UNAVAILABLE reason and null generation/completeness; CLI status may
   exit0 when it successfully reports that state. Invalid CLI usage/I/O failure
   still errors. A first failed index can report its attempt without an index.
2. Define explicit language/relation/producer entries with state and reason.
   supported means the stated bounded structural operation is implemented;
   partial means its declared syntax/lookup limits apply; unsupported means no
   enabled provider supplies it; failed describes an observed failed attempt,
   not a missing grammar. Use unknown/unavailable where coverage is not measured.
   A registered but generation-disabled language is unsupported for effective
   indexed extraction, while current-runtime availability is reported separately.
3. Produce current-runtime declarations from actual registry and handler rules.
   Preserve a bounded matrix of delivered relations: definitions, call,
   construct, superclass/include/extend/prepend, rationale as actually extracted.
   Ruby/Python/JS/TS differ; SCSS include produces call, HTML IDs/YAML keys/
   Markdown headings produce symbols only, Bash functions/commands are structural.
   Every producer's compiler/semantic_binding capability is unsupported until
   actual semantic evidence exists. Do not advertise unimplemented Rust/TSX/Go/
   Java extraction or infer semantic support from an imported edge.
4. On successful index builds, store a separate versioned coverage/capability
   snapshot in manifest (outside closed generation_inputs), committed in staging
   before publication. Include generation producer fingerprint, selected language
   configuration, applied skips, observed per-file outcomes, counts, and actual
   relation declaration snapshot. Preserve known successful/reused source hashes
   from S03 unchanged. Old generations missing this snapshot have unknown indexed
   completeness/capabilities; do not retroactively label them using current code.
   Imported generations report source completeness unavailable and upstream
   producer coverage unknown, even when their files table is empty.
5. Detect BOTH parser exceptions and Tree.root_node.has_error (including ERROR/
   missing nodes). Recovered trees with syntax errors count as failed parsing:
   abort staging and retain G1, following S03 failure semantics. Record stable
   relative path/language/error category and bounded stable error-node location,
   failed buffer SHA-256 if bytes were read, plus prior fact-source hash when
   present. Read errors have failed_hash=null with explicit unreadable reason.
   Do not overwrite fact hashes with failed buffer hashes; do not publish partial
   facts or use the failed hash as an incremental cache hit.
6. Under the existing writer lock, write one same-directory-temp+replace
   .atlas/index-attempt.v1.json after a failed attempt. It contains version,
   base generation ID, base diagnostic publication token, semantic attempt ID,
   attempted producer/configuration, observed outcomes/counts, and observation
   completeness. Preserve the original build exception and G1 even if writing
   diagnostics itself fails; surface a diagnostic-write warning/error honestly.
   Successful facts continue to publish through S03's immutable staging path.
7. Each successful DB publication (build or import) stores an internal diagnostic
   publication token as an extra manifest value; it is NOT part of canonical
   generation_inputs, exported facts, canonical coverage, or status JSON.
   A failed-attempt snapshot is applicable only to the exact base generation AND
   token. This prevents an old attempt attaching to a later publication that
   reused the same canonical generation ID (ABA). Legacy token absence is
   explicit; old indexes have unknown coverage, and a new successful publication
   always gets a token. No read-time token creation.
8. Status computes coverage_id over canonical persisted coverage and attempt_id
   over canonical semantic attempt data, excluding internal correlation tokens.
   status_id hashes generation ID (or null), coverage_id (or null), applicable
   diagnostic observation identity plus applicability state (or null), and
   current-runtime declaration identity. The observation identity includes the
   semantic attempt_id even when an initial/legacy attempt cannot be correlated. Thus changes
   to coverage/diagnostics are visible even when canonical fact generation ID is
   reused. Same generation+coverage+attempt+runtime serializes identically.
   Emit sorted keys and ordered language/relation/producer/path entries; omit
   indexed_at, current time, durations, random tokens, and absolute transient paths.
9. Update README with schema/state semantics, CLI examples, configured-versus-
   available distinction, structural/semantic limits, imported/legacy unknowns,
   recovered-parse failure behavior and snapshot/attempt boundaries.

### Snapshot and race contract

Status pins a DB lease, reads the atomic attempt file once, and applies only an
exact generation/token match. It never combines facts from different handles.
If a newer publication/attempt races and does not match the lease, report
attempt applicability=mismatch/unavailable without overlaying those diagnostics.
A bounded read observes a coherent identified generation plus a matching attempt,
not an atomic snapshot of the entire filesystem or a promise of latest completion.
Corrupt/truncated/foreign diagnostic snapshots are unavailable with reason, never
treated as zero failures or silently trusted. Source hashes come from consumed
buffers; exception text must be normalized to stable categories/locations.

A correlated failed refresh marks retained facts for the failed file stale when
those old facts exist; their original fact_source_sha256 remains distinct from
failed_source_sha256. New failed files have no retained facts. Untouched or
unprocessed retained files are NOT newly verified: report last-published evidence
and refresh_verification=unknown/not_processed. Overall latest-refresh completeness
is failed/partial with observed counts; unknown totals remain null.
Fail-fast traversal counts are explicitly observed-prefix counts, never a claim
that remaining files passed. Unsupported/disabled encountered files are counted
separately; paths excluded before enumeration have unknown excluded count plus
the applied skip policy. Zero is valid only for a measured, complete observation.

First-build failure with no completed generation reports generation=null,
completeness unavailable, and the failed file/hash/error in a separate observed
attempt section with applicability=unbound_no_generation. It does not manufacture
a successful generation or retained facts. A refresh over a valid S03 generation
without S04 coverage/publication-token metadata similarly exposes the failed file
and observed failed hash as applicability=uncorrelated_missing_token. The leased
old fact hash, if available, is labeled separately as last-published evidence;
no trustworthy linkage is invented between that old fact and the attempt, so
stale correlation is unknown rather than falsely asserted or silently omitted.
Both cases remain visible diagnostics and contribute to status identity. A later
publication with a token never overlays either unbound attempt; classify the old
observation as mismatched/superseded rather than a current failed refresh.

On successful recovery, new publication metadata invalidates old attempt overlay
without relying on deleting the sidecar. A token-only publication change does not
change canonical status when coverage and semantic observations are identical.
Repeated failed attempts with identical observed input use the same semantic
attempt/status IDs; diagnostic tokens serve correlation only.
Parsing failures must be retried until repaired; do not inherit failed observations
as successful file facts. Source-free import remains source-free.

## Stories

As an Atlas operator, I want status to tell me what the indexed producer supports
and which files failed or remain stale, so I do not mistake recognition or old
facts for complete semantic evidence.
One standard Vivi executor, two engineering days total, P1. No paid model calls.
At the timebox report observed checks and narrow gaps; avoid new provider or
partial-generation architecture.

## Acceptance Criteria

Exact complete lines between issue markers, final LF preserved, are materialized
at .spectra/plans/aci-s04.criteria.md.
SHA-256 947bf7c6701f496e5c88b979195582ad9fa19513a229f55f6e2848ddf7b4ba71.

### AC-S04-001 (event-driven)
GIVEN a recognized fixture language has no enabled extraction provider
WHEN status is requested
THEN that language SHALL be reported as unsupported for extraction.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_unsupported_extension

### AC-S04-002 (event-driven)
GIVEN a producer supplies definitions but no semantic binding
WHEN capabilities are serialized
THEN semantic binding SHALL be reported as unsupported for that producer.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_producer_specific_capability

### AC-S04-003 (unwanted-behavior)
GIVEN a fixture parser fails
WHEN status is requested
THEN the failed file SHALL appear in completeness diagnostics.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_parse_failure_visible

### AC-S04-004 (state-driven)
GIVEN facts from a failed refresh were retained
THEN the retained facts SHALL be marked stale.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_retained_stale_fact

### AC-S04-005 (event-driven)
GIVEN the same generation is queried without changes
WHEN status is requested twice
THEN the canonical capability payload SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_status_replay

### AC-S04-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S04-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

Use actual CodeGraph builds, real malformed source, and the CLI JSON serializer:
- test_unsupported_extension: recognized .rs/.go fixture has no extractor; also
  configured-disabled registered language remains effectively unsupported while
  runtime availability is separately true.
- test_producer_specific_capability: definitions-only provider fixture and actual
  query handling cannot claim semantic binding; cover SCSS include->call mapping
  and source-free imported facts with unknown upstream coverage.
- test_parse_failure_visible: real recovered errors (e.g. Python
  "def broken(:\n    pass\n", JS "function broken( {\n", Ruby
  "class Broken\n def broken(\n") produce failed file diagnostics.
  Cover first-build failure with no generation and refresh failure over a valid
  S03 generation without S04 token/coverage: failures remain visible with explicit
  unbound/uncorrelated applicability and unknown completeness/stale correlation.
  Also exercise a throwing parser/read-error path without substituting mocks for
  all parser behavior. First failed build with no generation is visible safely.
- test_retained_stale_fact: build valid G1, corrupt its source, fail refresh, then
  CLI status shows old fact hash + distinct failed hash + stale retained facts.
  G1 facts/ID stay unchanged; another unprocessed file is not labeled fresh.
  Retry still fails, repair succeeds, and stale overlay disappears even if
  canonical G1 content/ID is republished. Assert real publication-token mismatch
  protects against the old sidecar in this ABA case.
- test_status_replay: two actual CLI outputs are byte-identical under unchanged
  generation/coverage/attempt/runtime. New failed attempt or coverage changes
  status identity despite a retained canonical generation. Internal tokens and
  wall-clock metadata never leak into canonical payload.

Additional controls: matching/mismatched/corrupt sidecar; producer mismatch;
missing/legacy/import completeness unknown, not zero/complete; default vs selected
language provenance; atomic replacement race with deterministic barriers; status
read-only directory/file bytes and mtimes unchanged; original errors propagate;
failed diagnostics cannot corrupt publication; all S03 lease/hash/cleanup tests.

Run from worktree mcp-server with /tmp/aci-s01-tools/bin/uv and
UV_CACHE_DIR=/tmp/aci-s01-uv-cache:
- uv run --frozen pytest tests/slices/test_s04_contract.py -q
- uv run --frozen pytest tests/slices/test_s03_contract.py tests/test_codegraph.py -q
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

Capture current behavioral compute_indexer_fingerprint before and after; require
equality and verify current .spectra/changes/aci-s02-probe/
aci-s02-probe-lpa-vs-louvain.json. Extra manifest/diagnostic state must not alter
canonical facts, epoch or DDL. If facts change, investigate; any necessary new
probe requires a scope amendment and fresh genuine measurement, never re-signing
S02 data. Prior probe evidence remains immutable; do not preauthorize its refresh.
No huge raw logs: retain necessary evidence losslessly compressed.

## Receipt and re-entry

Initialize only .spectra/state/aci-s04.execution.json IN_PROGRESS before coding,
recording stable slice_id/criteria_sha256, issue_url, base_commit, declared_paths,
output_commit initially null, output_digests, and seven per-AC results.
Preserve a genuine partial receipt and verify the same identity on resume
(RUN-02); no plan reset or duplicate authoritative receipt.

After product checks, record a bounded implementation commit and SHA-256 manifest
of declared tracked implementation files. Clean-checkout re-entry verifies receipt
and evidence, returns ALREADY_SATISFIED without implementation writes, and proves
exact before/after digest equality plus git diff --exit-code against output_commit
for declared paths (RUN-01). Use a later receipt metadata commit to avoid circular
hashes. Reuse existing verification approach; do not alter previous slice artifacts.
COMPLETE requires all seven ACs and applicable checks, actual output digests and
reviewable changes. No merge/release/deployment included.

## Confidence

Actual ramza-score confidence: 90, AUTO_PROCEED, recorded 2026-10-24T00:47:11Z.
Dimensions: pattern_match 85, requirement_clarity 95, decomposition_stability 90,
constraint_compliance 90. Computed lite tier requires no independent critic.
Planning gates do not establish product behavior.

## Rejected Alternatives

Actual Explore scores: generation plus correlated-attempt 89.5, selected;
partial-generation provenance extension 71, exceeds bounded scope and risks
mislabeling retained facts; current capability table alone 63, cannot explain
older indexed producers, failures, or stale evidence.

## Risks

| Risk | Mitigation |
|---|---|
| Runtime capabilities falsely applied to older facts | Persist indexed declarations and producer identity; unknown when missing. |
| Failed parse replaces retained fact hash | Abort stage; separate failed buffer hash and immutable old provenance. |
| Same-ID republish revives stale attempt | Internal publication token correlation; token excluded from canonical identity. |
| Aborted scan reported complete | Observed-prefix basis, null unknown totals, unprocessed files unverified. |
| Error recovery silently treated as success | Real root.has_error/missing-node fixtures and stage abort. |
| Status writes under read-only repo | CodeGraph RO lease only, no Config/Memex/source scan or lock creation. |
| Two-day cap overwhelmed | Single snapshot/sidecar, no provider framework or partial generation. |

## Assemble handoff

Helper/schema root /Users/henrique/workspace/oss/agents/atlas-aci/.eidolons/ramza/.
Actual lifecycle must complete RS/S/P/E/C/T/A, computed confidence, declared
scope, initial criteria freeze, full envelope/state schema validation and independent
hash verification. ECL paths relative to envelope directory: aci-s04.md and
aci-s04.criteria.md. Root mechanical verify-envelope --block trace precedes Vivi.
Prior external memory persistence rejection is terminal; no retry or redirected
repository payload. Standard tier, one executor, no additional agent fan-out.
