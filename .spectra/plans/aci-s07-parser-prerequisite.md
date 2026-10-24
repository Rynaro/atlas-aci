---
eidolon: ramza
kind: spec
version: 1.1.0
created_at: 2026-10-24T03:10:12Z
supporting_work: aci-s07-parser-prerequisite
parent_slice: ACI-S07
---
# S07 supporting correction — explicit source observations and stale evidence

## Scope

REVIEW DRAFT: no production execution before independent approval and root handoff.
This revises the supporting scratch proposal through actual T -> R cycle 1. It
amends the shipped S04 blanket recovered-tree abort design, not any original
issue acceptance criteria. Original S07 DONE state, frozen criteria, envelope
and historical probe evidence remain immutable. One ACI-S07 execution identity
and receipt remains .spectra/state/aci-s07.execution.json.

Base ad9ab7a6122f653b0e9e7052e6173c6257b4a3b8; current uncommitted codegraph SHA
 a7696a05a6880bd378e1fa94498ffd579504d9feabb1a1c9935a1c8d1a71a0b9.
Preserve Vivi's existing work. Preflight records product/protected-artifact hashes.
Full tier remains required: original actual rightsize6/complexity9 are historical
scratch inputs; this refinement changes public metadata and migration semantics,
removes the proposed native dependency and retains independent maker/checker review.
No phase initialization or original criteria reset. Standard routing, no paid runs.

Exact additional execution paths, beyond already approved S07 scope:
- mcp-server/src/atlas_aci/codegraph.py: observation publication, stale isolation,
  readers/status, incremental reuse, portable adapters.
- mcp-server/src/atlas_aci/storage/evidence.py: closed observation records and
  consistency validation; preserve existing entity/evidence identity domains.
- mcp-server/tests/slices/test_s07_parser_prerequisite.py and
  mcp-server/tests/fixtures/s07/parser_prerequisite/*: real corpus-shaped controls.
- mcp-server/tests/slices/test_s04_contract.py: only recovered-syntax publication
  expectations and corresponding status/ABA/retry tests; fatal cases retained.
- mcp-server/tests/slices/test_s07_records.py and test_s07_contract.py: metadata,
  stale evidence closure and portable compatibility assertions.
- mcp-server/tests/test_export.py and test_schema_epoch.py: exact changed closed
  record/DDL/version contracts only; no weakened integrity or historical fixtures.
- README.md: operator-visible partial observation versus fatal build behavior.
- .spectra/plans/aci-s07-parser-prerequisite.*, .spectra/evidence/aci-s07/
  parser-prerequisite/* and .spectra/changes/aci-s07-parser-prerequisite/*.
Existing S07 fresh-probe artifact paths and CI selection amendment remain as
originally approved. No dependency/lockfile/Docker/native-provider changes.
Any other product path needs a concrete scope amendment before editing.

## Approach

### Requirement origin and choice

ATLAS verified live #26/#27 and frozen criteria: S04 AC003 requires failed-file
completeness diagnostics; AC004 requires retained failed-refresh facts be stale.
S03 AC003 protects G1 IF replacement fails. Universal root.has_error -> abort is
S04 plan90-97/17/50-53/311, shipped tests and README, not an explicit issue/probe
criterion. S03's earlier plan preserved parser-accepted recovered-tree extraction.
This is a material, reviewable behavior amendment, not merely a test adjustment.

Choose COMPLETE ATOMIC OBSERVATION with PARTIAL EXTRACTION, using unchanged
shipped parsers and exact bytes. Every eligible file is attempted and represented.
A recovered tree is a failed extraction observation; no subtree from that file
produces new facts. This deliberately trades clean-region navigation for soundness
and reports that loss. It does not claim SCSS/Tailwind or ERB composition is fixed,
validate opaque host text, remove failed inputs, or redefine partial as complete.
Runtime capability declarations remain distinct from actual indexed coverage.

A genuine fatal read/parser exception/extraction/DB/publication failure aborts
staging and preserves G1. Syntax diagnostics alone become file-level failures,
not an incomplete repository enumeration. Exit success means the atomic observation
was published; CLI build output and status MUST say partial plus failed counts.
README documents this change; no strict-success alias or probe-only mode is added.

Rejected grammar route: modern SCSS577506f3 fixed !default but failed39 residual
SCSS/CSS files; seven Ruby/YAML files are real ERB composites. Correcting multiple
grammars plus original-byte host composition/native packaging is not a bounded
prerequisite established by evidence. Preserve those failed qualification logs.
Recovered-clean-subtree extraction is also rejected: valid-looking captures can
have corrupted owner/scope context. Whole-file quarantine has a simpler auditable
safety boundary, applied to malformed controls and real dialect limitations alike.

### Observation and identity contract

Use one closed, deterministic per-file observation representation persisted in
the published SQLite generation and carried by the portable evidence artifact.
Fields must distinguish path/language, observed_source_sha256, extraction state
(indexed/failed/unsupported/disabled), stable reason/error location, and retained
fact provenance. Retained provenance lists exact historical fact/evidence IDs,
original fact source hashes and original producer IDs; never substitutes observed
hash for fact hash. Files with no prior facts have empty retained IDs, not fake
zero-length source evidence. Do not label parser error as proof source is invalid.

Use atlas-generation.v3 for newly indexed generations, read v1/v2 compatibly;
closed inputs include canonical observation digest plus current configuration/
producer and source path identity. Distinguish current observed hashes from
historical indexed hashes. Keep occurrence/evidence identity hashing unchanged.
Version coverage as atlas-coverage.v2: enumeration complete does not imply
extraction complete; any failed file yields partial with exact failed/stale counts.
First-build parse failures produce visible observations even with no graph facts.

A successful new observation generation is atomically coherent: graph, coverage,
observation digest and freshness metadata all come from the leased connection.
No late filesystem scan or latest-path overlay supplies facts for a pinned lease.
Keep fatal-attempt sidecar correlation/ABA protections; a completed publication
suppresses older failed-attempt overlays, including same-input republication.
Status identity covers persisted coverage and matching fatal-attempt diagnostics.
No timestamps, inode, UUID or publication token enters canonical identities.

Unknown legacy source hashes/coverage stay unknown. A source-backed legacy fact
may be retained as historical with its exact available provenance; never invent
an original hash/span/producer. Missing observation metadata cannot imply fresh.
Imported declared observation state is artifact provenance, not verification of
current destination files. Producer/config/language changes invalidate fresh
cache reuse; retained historical evidence still names its ORIGINAL producer.

### Stale isolation and staged build

Under the existing writer lock, stage from a consistent predecessor, even for a
full refresh when historical facts may need retention. Read/hash each eligible
buffer once and parse it as currently routed; do not transform bytes. Keep old
facts only for failed-extraction paths. Successful files replace facts normally;
deleted/disabled files are not silently retained as current source. Preserve the
legacy adapter and atomic publication rules; no intermediate migrated publication.

Separate retained historical records from the fresh binding candidate set before
_resolve_edges and rationale resolution. Preserve old assertion/evidence/occurrence/
entity/producer closure exactly for returned history; do not recompute stale
references against new definitions. Historical target occurrence coordinates must
remain tied to their old evidence, even if the same entity moved or was deleted.
Never force a historical assertion through a validator expecting the CURRENT
symbol's line. Historical closure is distinguishable from active symbol rows;
it cannot create duplicate current definitions or inflate fresh graph counts.

Fresh references cannot resolve against stale definitions, stale import bindings,
or stale assignment/scope facts. When a match is unavailable because its source
failed extraction, leave a stable unresolved reason (stale_target); retain S02
language/owner/shadowing checks. Current assertions require both fresh source
context and fresh target support. Rationale and import relationships follow the
same rule. Retained historical assertions keep original confidence as provenance,
with explicit stale status, never promoted into the current confident projection.

Search/graph-query results that return retained facts expose freshness plus exact
fact versus observed hashes. confident_edges and every community/god-node/coupling
projection exclude stale source or target support. Any query cache must be per
leased generation/observation identity; no reuse based only on canonical entity
IDs or old generation source-hash map. Current mixed resolved/unresolved counts
must exclude historical records from current totals, with history counted separately.

Retry every failed file even if observed bytes, size and mtime are unchanged;
its old fact hash is never a cache hit for those bytes. Recovery clears stale
associations and replaces old facts/closure that no longer has historical users.
Full and incremental builds of identical observed inputs must produce the same
current graph/observation identity. Historical retained content is an explicit
identity input, so differing prior evidence cannot falsely replay the same ID.

### Portable contract

Extend the current unmerged S07 portable artifact with explicit observation and
historical-association records under a NEW exchange format atlas-evidence.v2.
Keep the existing atlas-evidence.v1 identity hash domain for unchanged canonical
entities/occurrences/evidence/assertions; do not change IDs just because the
exchange header changes. Accept valid v1 and epoch6 artifacts through the existing
adapter with observation/freshness unavailable, never presumed verified.

Strictly validate path/hash/state and referenced evidence closure, stale relation
associations, current-versus-historical projections, and conflicting duplicate
observations before publication. Source-free import retains known staleness;
export/import/export and repeated imports are deterministic. Do not copy runtime
absolute root/publication token into portable provenance. Unknown destination
verification is separate from known historical stale state. Adjust unmerged epoch7
DDL/hash only if persistence requires it; fresh probe already required, no new
migration epoch merely for this unmerged correction.

## Stories

As an operator I can inspect a partially understood repository without an unsafe
relationship being presented as current, and distinguish completed source
observation from an interrupted refresh. Same Vivi executor, standard tier.

Timebox: one engineering day maximum WITHIN original S07 five-day cap. Root/coder
records remaining allocation; use the smaller amount, no unrecorded extension.
First 60 minutes: real two-file prototype proves retained historical relationship
closure, stale-target exclusion, first-file failure diagnostics and export/import
staleness. If that needs general temporal graph machinery or cannot satisfy the
closed provenance contract, STOP with exact missing capability; no halfway adoption.
Then finish source observation/lifecycle, compatibility tests, full suite and fresh
probe. Every probe subprocess remains bounded by existing runner timeout/budget;
record real exit/time/RSS. No native builds or grammar downloads in this option.

## Acceptance Criteria

These supporting criteria were unfrozen when the origin audit triggered refine.
They supplement, never replace, original S07 ACs and the S03/S04 acceptance text.
### AC-S07-PARSER-001 (event-driven)
GIVEN both pinned inputs and unchanged default eligibility
WHEN indexing completes source observation
THEN the generation SHALL account for every eligible original path and source hash.
VERIFY: gate: compare 2519 Solidus and 2358 Spree sorted path/hash entries with preserved qualification manifests; no language narrowing, byte transformation or hidden failed path.

### AC-S07-PARSER-002 (event-driven)
GIVEN a real parser returns a recovered tree with syntax errors
WHEN a complete observation generation is published
THEN that file SHALL be reported as failed extraction with explicit partial coverage and no newly extracted facts.
VERIFY: test: real malformed Python/JavaScript/Ruby and pinned SCSS/ERB examples expose observed hashes and error locations; first-build failures remain visible; clean sibling facts remain usable.

### AC-S07-PARSER-003 (event-driven)
GIVEN a previously indexed file now has a recovered syntax error
WHEN its historical facts are retained
THEN every retained fact SHALL preserve its original evidence and be marked stale relative to the new observation.
VERIFY: test: start with actual definitions and a resolved relationship, change bytes to malformed content, assert old fact hash/span/producer and new observed hash differ, query/export/import preserve staleness, and no old fact is rebased onto new bytes.

### AC-S07-PARSER-004 (unwanted-behavior)
GIVEN a graph contains stale or extraction-failed source observations
WHEN resolution or confident graph analysis runs
THEN stale facts SHALL NOT contribute fresh bindings or confident relationships.
VERIFY: test: fresh callers cannot bind a stale target, stale source references are not resolved anew, historical assertions stay identifiable, and confident_edges/community/god-node projections and cached results exclude stale contributions.

### AC-S07-PARSER-005 (unwanted-behavior)
GIVEN source reading, parser execution, extraction, persistence or publication fails fatally
WHEN a replacement build is attempted
THEN the previous completed generation SHALL remain intact and usable.
VERIFY: test: inject real exceptions after partial progress, retain reader G1 across publication failure, compare graph and evidence bytes, prove cleanup and incomplete diagnostics; first fatal build publishes no generation.

### AC-S07-PARSER-006 (event-driven)
GIVEN a failed file is retried or repaired
WHEN full or incremental indexing completes
THEN the generation SHALL recompute its observation and expose coherent deterministic freshness.
VERIFY: test: retry unchanged failed bytes invokes parser, repaired bytes replace stale facts, same-size/same-mtime changes are read, identical observations replay IDs, pinned leases remain coherent, and legacy/import unknowns never become verified source claims.

### AC-S07-PARSER-007 (event-driven)
GIVEN a portable artifact contains historical facts and current observation provenance
WHEN it is imported without source files
THEN known stale associations SHALL survive with destination source verification unavailable.
VERIFY: test: strict closed-record validation rejects contradictory hashes/freshness and dangling history; repeated imports/export are deterministic; legacy artifacts without observations remain explicitly unknown.

### AC-S07-PARSER-008 (state-driven)
GIVEN any product, compatibility or fresh probe gate is failed or unmeasured
THEN ACI-S07 SHALL remain incomplete.
VERIFY: gate: original criteria/identity and historical measurements stay immutable; run original full tests and unchanged fresh same-pin/default producer probe with frozen modularity bar; partial coverage counts are reported beside actual measurements and CUT cannot satisfy readiness.

## Verification

1. Real parser controls: malformed Python/JS/Ruby, pinned !default and residual
   SCSS examples, plus raw ERB Ruby/YAML. Assert errors remain errors, files remain
   visible failed observations, no new captures from their recovered trees. Clean
   sibling fixtures retain exact previous facts/spans. No grammar acceptance claim.
2. Non-vacuous S04 AC003/004: seed a real class/function and resolved caller, make
   its source invalid, then assert the old definition AND historical relationship
   remain traceable with original hash/span/producer and new failed hash. Assert
   stale target cannot satisfy a NEW clean caller, and stale relations do not enter
   confident/community results. Repair and prove retry/recovery replaces history.
3. Amend test_s04_contract.py::test_parse_failure_visible and
   ::test_retained_stale_fact to assert published partial observations, not merely
   remove pytest.raises. Its deterministic status, first-build, no-token legacy,
   successful identical-content republish and unprocessed-file cases must choose
   explicit recovered versus fatal controls. Existing parser/read/diagnostic-write
   exception tests stay fatal; add extraction/DB failure after actual progress.
   Fatal unprocessed retained files remain unknown, never freshly verified.
4. G1 reader barrier across partial G2 publication and fatal G2 failure, full and
   incremental paths, cancellation/finally cleanup, readonly :ro storage controls,
   stale per-generation cache and same-stat edits. Original S03/S06 tests remain.
5. Strict portable negative cases: fresh claim with old hash, missing observation,
   duplicate contradictory state, dangling historical endpoint, changed producer,
   stale artifact imported source-free, v1/epoch6 unknown adapters. Line-moved and
   deleted historical targets must not be rebound or lose their original evidence.
6. All4877 eligible pinned files must be accounted for with exact unchanged hashes.
   Successful build qualification means every file has an explicit outcome and
   no unsafe facts, NOT every grammar accepts every source. Report counts separately
   by language/state/reason and preserve every earlier failed qualification result.
7. Fresh default-producer Solidus/Spree graph exports and real modularity probe;
   same pins/profile,10 seeds0..9,resolution1,Q>=.30,LPA>=.85medianLouvain.
   No stale/historical assertions in measured confident graph. Report partial
   coverage with metrics; no changed bar, invented PASS, old metric re-sign or CUT
   as readiness. If conservative graph misses bar, S07 remains blocked honestly.

Commands from worktree (original S07 commands remain authoritative):
- cd mcp-server && UV_CACHE_DIR=/tmp/aci-s01-uv-cache
  /tmp/aci-s01-tools/bin/uv run --frozen pytest tests/slices/test_s07_parser_prerequisite.py
  tests/slices/test_s04_contract.py tests/slices/test_s03_contract.py
  tests/slices/test_s07_records.py tests/slices/test_s07_contract.py -q
- Same environment: uv run --frozen pytest -q (Docker requires scoped escalation;
  no required skips), uv run --frozen ruff check ., uv run --frozen mypy src.
- Existing scripts/test-verify-probe-verdict.sh plus exact fresh probe export/
  measurement/bundle/verifier commands from original S07 plan; immutable pins
  Solidus4026945d614e81383c007ed1ab1278a0195ce5d9 and
  Spree6699cde44303ea85ef6e56c5e87c44a738ab73fc (historical spree/* checkout).
- Original S07 RUN-01/RUN-02 receipt/reentry protocol, one bounded local commit,
  root publication/green CI/merge, no second receipt or issue closure before gates.

## Verification layers and checker

Three decomposition views agree: source traversal -> observation/provenance ->
atomic publication; facts -> historical isolation -> query/analysis; portable
records -> closed validation -> source-free import. Shared invariants are exact
bytes, no new failed-file facts, immutable old evidence, coherent lease, truthful
coverage. Independent ATLAS evaluates scope/timebox and all three views; maker
ramza-backlog-order-s07-parser-prerequisite cannot self-sign. Actual lint/EARS8,
refine rubric/identity/timestamp precede Assemble. Root mechanically verifies
final envelope before authorizing the same Vivi executor.

## Confidence

Not yet computed. The outcome of the unchanged fresh modularity gate is unknown.
Review readiness is distinct from claiming product correctness or probe success.

## Rejected Alternatives

Prior scratch alternatives/scores remain audit history. Revised options are
scored through actual helpers during refinement: explicit complete observation
with whole-file quarantine selected on original criteria/soundness; blanket abort
retains shipped behavior but does not supply requested partial diagnostics across
all files; multi-grammar composition offers better syntax coverage but currently
has no bounded proven implementation. Unsafe clean-subtree extraction and narrower
probe profiles are not acceptable alternatives. The choice is NOT conditioned on
which option yields a passing modularity number.

## Risks

| Risk | Required boundary |
| --- | --- |
| History schema becomes a temporal graph platform | One predecessor's necessary retained evidence closure only; stop at first checkpoint if general machinery is necessary. |
| Partial publish misrepresented as complete | Separate enumeration/extraction states, CLI counts and README behavior change. |
| Old evidence contaminates new bindings | Explicit candidate exclusion and current-analysis projection tests. |
| Provenance lost by export/import | Closed versioned observation/history records and strict contradiction controls. |
| Conservative quarantine lowers navigation/metrics | Report coverage loss; unchanged probe may fail and remain a genuine blocker. |
| Remaining S07 time is insufficient | Charge to five-day bound, record remaining allocation, stop rather than silently extend. |

## Assemble handoff

A scope-amendment pointer records origin audit and this supporting plan before
product execution. Original S07 artifacts remain immutable. Complete actual
confidence/scope/freeze/emission and full schemas/hash checks; use envelope-relative
paths. Prior external memory write rejection remains terminal. No code written
by RAMZA; inherited product edits verified unchanged against preflight.
