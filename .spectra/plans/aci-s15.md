---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T17:08:21Z
slice_id: ACI-S15
issue: https://github.com/Rynaro/atlas-aci/issues/11
---
# ACI-S15 — measured Rust structural extraction

## Scope

Implement existing issue11 on merged base f055e56d2f862ff2dbee827c7b30dabc2a76b094,
branch aci-s15 at /tmp/atlas-aci-aci-s15. Live marker/criteria verified; no prior
S15 state/receipt. Preflight verified S10's261 current receipt digests and historical
S04(5)/S07(332) output digests plus ancestry. Exact criterion digest:
312e461863ca649cff7689ff8d4b1d6538e8648f75c6d35777786df1dede0f9a.

In: free functions, impl methods, struct/enum/trait/module declarations, source call
occurrences, honest macro/generated/semantic limitations and current cache/identity/
reader compatibility. Out: trait dispatch, type inference, compiler/build execution,
macro expansion, crate/use resolution, new dependency versions, semantic providers
or treating PR13 as shipped. User selected this issue through the all-backlog order;
this does not silently add Rust to S23's separately frozen release gate.

Implementation paths:
- mcp-server/src/atlas_aci/codegraph.py
- mcp-server/src/atlas_aci/extractors/__init__.py (inert)
- mcp-server/src/atlas_aci/extractors/rust.py
- mcp-server/tests/slices/test_s15_*.py
- mcp-server/tests/fixtures/rust_workspace/*
- README.md
Narrow amendments, recorded before implementation:
- mcp-server/src/atlas_aci/storage/evidence.py: Rust structural owner overrides and
  exact current producer module set while preserving historical canonical schemas.
- mcp-server/src/atlas_aci/resolvers/dependencies.py: register Rust only after its
  actual raw-input/unresolved-output contract is covered by complete buckets.
- mcp-server/tests/test_codegraph.py: remove obsolete Rust-unsupported expectation,
  retain other unsupported-language negative controls and kind classification.
- mcp-server/tests/slices/test_s04_contract.py: keep real unsupported control using
  Go, plus new Rust runtime-versus-disabled indexed capability controls in S15 tests.
- .spectra/plans/aci-s15.*, .spectra/plans/ramza-calibration.jsonl,
  .spectra/evidence/aci-s15/*, .spectra/state/aci-s15.execution.json,
  .spectra/changes/aci-s15/*, .spectra/backlog/execution.json and order.md.
No Docker/CI/lockfile edit is authorized merely by anticipation. Existing Docker
prefetch already iterates DEFAULT_LANGS; amend only if an observed need remains.

Actual rightsize FULL5:14 estimated files, public new language/capability behavior,
new bounded ownership handling, medium stakes. Complexity8 (2,2,2,2). Standard
routing, independent ATLAS review required; P1 is not a TRANCE activation. No paid
runs or external memory retry/persistence. Known objective/scope needs no user query.

## Approach

### Prior art and actual grammar qualification

ATLAS fetched PR13 closed/unmerged (head0a511c3…): its generic function plus impl-
method patterns duplicate the same AST declarations and do not establish modern
ownership/provenance. Use it only as syntax prior art; do not cherry-pick claims.

Local canonical interpreter confirms tree-sitter-language-pack1.6.0,
has_language('rust') False. No Rust grammar was downloaded during planning. Observed
installed API: download(names), configure(*,cache_dir=None), cache_dir(),
downloaded_languages(); get_parser may lazily download. Official installed package
METADATA documents those APIs and on-demand binaries. No parser availability claim
until a real load and fixture parse succeed.

FIRST executor checkpoint, before product query work: uv sync --frozen; use the
locked1.6.0 official download(['rust']) route, with scoped network/filesystem
escalation if necessary. A task-local cache may be selected using the observed
configure(cache_dir=...) API for qualification; reset to default or explicitly
provision the standard SDK cache for independent normal CLI/test processes. Never
repoint user configuration or add a production cache option merely for this task.
Record exact package/uv-lock hashes, official versioned asset/manifest identity,
platform/architecture, downloaded artifact SHA256s, actual loaded ABI/version
where exposed and command exits. Inspect SDK cache/official release metadata to
establish provenance; do not invent source commits or manifest checksum checks.
Require reproducible recorded artifact identity and query/AST compatibility. If
provisioning or provenance cannot be established, stop with the actual blocker;
no fake parser, skipped required tests, package upgrade or vendored grammar port.

Parse the unchanged Cargo fixture with the actual native Rust grammar, inspect
field/node shapes and compile the proposed query against that same Language.
Use primary grammar reference https://github.com/tree-sitter/tree-sitter-rust,
but actual locked binary tree/query behavior is authoritative for this slice.
No cargo build/test, build.rs execution, proc-macro execution or source rewriting.

### One declaration per AST node and structural owners

Use match-grouped queries for function_item, struct_item, enum_item, trait_item
and mod_item; include trait method signatures supported by the loaded grammar.
Capture each function_item once, then classify its nearest semantic owner as free
function versus impl/trait method. Nested free functions inside a method remain
functions; do not classify via any distant impl ancestor. Emit exactly one symbol/
entity/occurrence per declaration node, including methods with a body. Do not add
a second impl-function pattern that duplicates the generic function capture.

Use kinds function/method/struct/enum/trait/module, with explicit classification in
_CALLABLE_KINDS/_NON_CALLABLE_KINDS as appropriate. Rust struct/enum/trait are
structural lookup kinds; do not label them generic class merely to reuse constructor
heuristics. Public produced-kind/capability declarations must match actual output.

Add a narrow Rust helper for definition classification, called-expression identity
and impl scope descriptors. Feed Rust-only structural scope overrides into existing
ev.structural_entities; keep other languages' owner/ordinal behavior unchanged.
An impl scope is NOT a fabricated public declaration. Its owner descriptor names
the exact inherent/trait impl header (type, trait and generic/where tokens) from
original AST/source, normalized as token sequence excluding whitespace/comments,
plus existing matching-sibling ordinal. No byte offsets, line numbers, source hash
or guessed type identity in the entity key. This is syntactic ownership, not proof
that two type spellings denote the same semantic type.

Nested module/trait/function owners remain in the chain. Distinct impl types and
trait-versus-inherent impls cannot flatten into same-name method ownership. Repeated
same-header impl blocks retain deterministic sibling ordinals per S07's identity
policy. Blank-line/comment changes preserve entity IDs, while source occurrence
hashes/spans change truthfully. No new promise of stability under arbitrary reorder
of indistinguishable duplicate declarations.

### Occurrences without unsupported call resolution

Capture each call_expression once with its source occurrence/proof and enclosing
structural source entity. Preserve unqualified, scoped/associated, field/method and
generic-call syntactic target text; for unsupported callee shapes retain original
text/shape and source occurrence rather than guessing a target. Native fields are
confirmed at qualification, not assumed from old PR. Do not turn macro token-tree
contents into ordinary call/declaration captures or synthesize expanded output.

Explicit Rust branch BEFORE generic flat-name fallback: ALL Rust call references
remain UNRESOLVED with stable reason rust_semantic_binding_unavailable (or a
narrower documented unsupported-shape reason). Candidate IDs and target are empty.
This is deliberately structural support; even an apparent same-file free function
is not advertised as compiler binding. Trait/receiver/generic/associated dispatch
never guesses among same-name functions or methods. Keep source occurrence and
raw qualifier information for future semantic evidence; no new construct/import/
inheritance relations are claimed in this slice.

### Truthful versioned capabilities and default profile

Register QUERIES['rust']; this automatically enables Rust in DEFAULT_LANGS. Remove
Rust from UNSUPPORTED_LANGS only for delivered structural definitions and call
occurrences. Runtime matrix says partial structural extraction, call occurrence
support, semantic binding unsupported; no construct relationship from class-kind
heuristics. Indexed capabilities continue to come from the pinned generation's
producer/configuration, not new runtime defaults.

Use atlas-capabilities.v3 for the explicit new limitations, reading v1/v2 with their
EXACT prior shapes. Preserve existing three limitations and add Rust-specific
macro expansion/derive/proc-macro unavailability, unmaterialized generated-code
unavailability, cfg/build configuration not evaluated, and unresolved dispatch.
Normal .rs files already generated on disk can be structurally parsed as supplied;
Atlas does not generate missing OUT_DIR/include!/build-script output. Conditional
items describe observed syntax, not an active Cargo build configuration. A macro-
only declaration must remain absent from canonical symbols with the gap visible.
Never relabel opaque macro text as validated generated Rust. Malformed real syntax
continues S07 failed observation/quarantine/retry behavior.

Update _valid_capabilities closed format/limitations checks compatibly; v3 has
module-bearing producer provenance like v2, without rejecting historical metadata.
No graph DDL/epoch or portable evidence format change is required. Old indexes may
still report Rust unsupported under their own producer; imported completeness stays
unknown where it already was. Keep disabled Rust distinct from unsupported Rust.

### Cache, dependency and producer closure

Hash extractors/rust.py in _producer_inputs and extend the exact known producer
module allowlists (historical S07 two, S09 three, S10 four, current five). Do not
permit arbitrary names or rewrite old producer hashes. Inert __init__ contains no
extraction logic. Normal package versions plus actual query/helper hashes cover
the canonical producer; platform binary hashes belong in provisioning evidence,
not cross-platform canonical IDs. Qualify the same locked release on each platform.

S09 packet must carry the Rust entities/owners/occurrences/raw refs/qualifiers and
all required sidechannels unchanged; current producer invalidation is expected.
Cold/full/warm/new-writer Rust facts match, complete warm hit skips real parser and
_extract, failed files retry. Path-bound same-name modules across crate fixture
files cannot transplant cached identities.

Register Rust in S10 complete-language buckets because the chosen resolver consumes
only modeled raw source proof/declarations and emits unresolved Rust calls. Verify
local proof links and declaration/display fields are covered; then same-language
unchanged Rust units may be reused. Unknown future Rust semantics still requires
explicit full fallback. Full/incremental/forced-full current facts must match,
including raw unresolved reasons and histories under equal predecessor context.
No inference/dispatch edge enters confident analysis, even with another language's
same-name symbol. Preserve existing RO readers/external-root isolation.

## Stories

As a Rust operator I can navigate real syntax and exact provenance while seeing
where macros, generation and semantic dispatch are unavailable. One Vivi executor;
exact issue timebox THREE engineering days, including grammar qualification and
compatibility verification. Stop/report remaining scope at cap. No performance
threshold or added Rust S23 release condition.

1. Provision/qualify locked native parser; capture before-fix real empty Rust graph
   reproduction and old PR capture hazard in evidence, not shipped assumptions.
2. Implement bounded query/helper/owners/unresolved branch and truthful versioned
   capabilities/default profile; register cache/dependency/producer compatibility.
3. Run real Cargo workspace declarations/ownership/provenance and failure controls,
   host/native plus offline Docker, full gates and receipt/reentry. No Cargo execution.

## Acceptance Criteria

Exact live issue criteria, unchanged:
### AC-S15-001 (event-driven)
GIVEN a Rust fixture contains the declared structural constructs
WHEN the real indexer runs
THEN the canonical declaration set SHALL equal the labeled fixture set.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_rust_declarations

### AC-S15-002 (event-driven)
GIVEN two impl blocks define same-name methods
WHEN facts are extracted
THEN each method occurrence SHALL retain its enclosing impl ownership.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_impl_ownership

### AC-S15-003 (event-driven)
GIVEN a Rust function contains a call expression
WHEN extraction runs
THEN Atlas SHALL record its source call occurrence.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_rust_call_occurrence

### AC-S15-004 (unwanted-behavior)
GIVEN a declaration exists only after unsupported macro expansion
WHEN capabilities are requested
THEN Atlas SHALL report the macro expansion gap.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_macro_gap

### AC-S15-005 (event-driven)
GIVEN a trait-dispatch target is not established
WHEN binding runs
THEN the call SHALL remain unresolved.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_no_trait_guess

### AC-S15-006 (event-driven)
GIVEN the same Cargo workspace is indexed twice
WHEN the second index completes
THEN the canonical Rust fact set SHALL equal the first successful result.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_rust_replay

### AC-S15-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S15-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

Create tests/fixtures/rust_workspace/ with Cargo.toml workspace/members and labeled
.rs files: free/nested functions, inline/external modules, struct/enum/trait,
inherent impl and trait impl, repeated same-name methods across different types,
trait signatures/default methods, generics, actual calls and macro-only output.
Golden declaration labels are independent expected data, not generated by extractor.

Six named tests in tests/slices/test_s15_contract.py; additional controls in
 test_s15_integration.py if needed:
- Exact declaration set and one occurrence per AST declaration: no free-function/
  method duplicate. Nested free fn, same-line siblings, multiple impl headers and
  crate-relative paths have correct distinct owners. Insert blank lines/comments
  before/inside impl header and verify stable entities with new exact proof spans.
- Actual free/scoped/field/generic call expressions preserve raw target and source
  hashes/spans. Trait receiver and associated calls remain unresolved despite unique
  same-name method candidates; confident_edges contains no guessed Rust calls.
- macro_rules!/derive/proc-macro-shaped attribute and include!-style fixtures expose
  capability gaps, no generated-only symbol. No source macro/build execution.
  Malformed Rust produces real parser ERROR -> partial/failed observation, unchanged
  failed retry re-parses, repair recovers; fatal missing/throwing parser preserves G1.
- Runtime/default Rust enabled versus explicit language-disabled generation;
  closed capability v1/v2 compatibility and v3 limitation validation reject malformed
  fields. Existing unsupported-language negative tests remain (Go/Java/TSX), and
  classification test explicitly covers struct/enum/trait without fake construct.
- Full repeat/cold/warm/new-writer/incremental output equality includes all canonical
  raw fields and exact portable JSONL, not just names/counts. Real parser/_extract
  instrumentation proves warm hit. Same-size/same-mtime Rust edits invalidate cache.
  Same-language binding reuse and unknown-profile fallback remain tested under S10.
- Strict current/old producer sets, source-free export/import/replay, legacy readers,
  held G1 across Rust G2, external graph root and readonly source remain correct.

Host provisioning from mcp-server (network/setup action, not product runtime):
PATH=/tmp/aci-s01-tools/bin:$PATH UV_CACHE_DIR=/tmp/aci-s01-uv-cache uv sync --frozen
uv run --frozen python -c "from tree_sitter_language_pack import download; print(download(['rust']))"
Record actual installed version, cache_dir/downloaded_languages, official binary
provenance/hashes and actual Rust query/AST results under .spectra/evidence/aci-s15/.
Use required scoped escalation for network/SDK cache permissions, not a version or
provider substitution. If a task cache is used, document configure/reset and ensure
normal CLI subprocesses have the same qualified grammar available.

Before product changes capture baseline Rust zero-symbol behavior (real graph,
current no-query path) plus existing S04/S07/S10 checks. Then from mcp-server:
uv run --frozen pytest tests/slices/test_s15_contract.py tests/slices/test_s15_integration.py -q
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src
Only name the optional integration test file in commands if it is actually created.
Record command/exit/source hashes; no required tests skipped for unavailable parser.

Build existing mcp-server/Dockerfile with locked dependencies; its DEFAULT_LANGS
prefetch should now include Rust without Docker code changes. Actual Linux image
must load the installed Rust grammar and index the Cargo fixture under --network
none, --read-only, nonroot UID10001, source:/repo:ro and writable external /graphs.
Assert source-write sentinel fails, source digests unchanged, graph/cache external,
then readonly stdio query returns Rust definitions and truthful unresolved calls.
No parser cache mount or runtime compile/download may rescue the offline proof.
Record image/package/asset hashes, mounts, exit codes and native query results.
Required macOS/Linux canonical export matrix and existing Docker jobs remain.

Both pinned Solidus/Spree inventories contain zero .rs (ATLAS checked). Adding
Rust still changes default config/producer, so verify actual current S07 probe,
not merely assume it is unaffected. From root with same uv PATH/cache:
python3 scripts/verify-probe-verdict.py .spectra/changes/aci-s07-probe/aci-s07-probe-lpa-vs-louvain.json
bash scripts/test-verify-probe-verdict.sh
bash scripts/test-verify-export-size.sh
Expected projected behavioral fingerprint0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f
can remain legitimately unchanged because it hashes epoch/DDL/projected graph,
not default config alone. Measure and record. If actual graph changes, diagnose;
no re-signing old metrics. A genuine intended semantic change needs fresh evidence
and explicit amendment. Old size measurement and current S07 resource disclosure
remain unchanged; no new performance threshold or claims based on Rust-free repos.

## Verification layers and checker

Three views: native grammar -> matched declaration/call -> owner/proof;
capability/default profile -> producer/packet/witness -> canonical replay;
provisioning -> real host/native -> installed offline readonly Docker. Shared
invariants are exact original bytes, one AST declaration/occurrence, syntactic
ownership, no dispatch guess and truthful unknowns. Independent ATLAS full review,
actual lint/EARS8/refine score and checker timestamp required. Maker
ramza-backlog-order-s15 cannot self-check. Review approval is not parser availability
or product verification; qualification remains first execution checkpoint.

## Receipt and backlog continuity

Ledger records S10 PR73 merge f055e56, nine merged/33 remaining; current11,next30;
frozen order preserved. Root owns validated publication/CI/merge/main sync under
standing user authorization. Optional S15 selection does not rewrite S23 criteria.

Before implementation initialize only .spectra/state/aci-s15.execution.json
IN_PROGRESS with ACI-S15/live criteria/base/scope/eight AC slots, output_commit=null.
Adapt S10 verify_execution.py/verify_resume.py into NEW S15 evidence paths; preserve
an interrupted receipt snapshot and compare identical execution identity (RUN02).

Declare EXACT metadata-only output_digest_exclusions before initialization:
.spectra/state/aci-s15.execution.json;
.spectra/evidence/aci-s15/final-reentry-before.json;
.spectra/evidence/aci-s15/final-reentry-after.json;
.spectra/evidence/aci-s15/final-reentry-summary.json;
.spectra/evidence/aci-s15/final-resume-identity.json;
.spectra/evidence/aci-s15/output-check-tests.log.
No wildcard exclusions. Product/tests/stable pre-output evidence/plans/criteria and
bookkeeping amendment remain hashed. Exclude these exact paths from output SHA
digests and output-commit comparison, but require wholly clean reentry checkout.
Additional necessary post-output artifact needs a precise amendment before writing.

Commit bounded implementation/stable evidence after gates; record actual output
SHA/digests, then metadata-only receipt/proof commit. Read-only clean reentry verifies
all tracked implementation bytes versus output SHA and returns ALREADY_SATISFIED
without edits (RUN01). Root merges only after complete evidence/green CI. No fake
COMPLETE for unavailable grammar, elapsed timebox or missing native integration.

## Confidence

Not yet computed. Grammar remains unavailable at planning time; Assemble confidence
assesses bounded plan readiness, not a fabricated successful provisioning result.

## Rejected Alternatives

Actual Explore: matched query+bounded owner helper86.5 selected; generic query-only
PR13-style68 rejected for duplicated/flattened ownership and unsafe fallback;
compiler-semantic provider55.5 rejected as outside issue/timebox. No fake traits,
macro expansion simulation, fabricated availability or weaker unsupported tests.

## Risks

| Risk | Boundary |
| --- | --- |
| Locked Rust asset unavailable/incompatible | First real provisioning/query checkpoint; exact blocker, no skip/substitution. |
| Function/method duplicate or flattened impl | One AST-node classification, Rust owner overrides, exact golden declarations. |
| Structural calls advertised as semantics | Explicit unresolved Rust branch before generic fallback; no construct/import claims. |
| New capabilities reject old generations | Closed versioned v3 extension with exact v1/v2 compatibility controls. |
| Default Rust causes runtime lazy download | Build-time DEFAULT_LANGS prefetch and actual offline nonroot readonly Docker. |
| Cache/witness loses owner/proof fields | All-field cold/warm/incremental/portable parity and producer closure. |

## Assemble handoff

Canonical tools/schemas from original checkout .eidolons/ramza/. Finish actual
critic/confidence/scope/freeze/drift/emission/full schema+format+digest gates.
Envelope paths relative to its directory; root mechanical verifier/correlated trace
before Vivi. Stop planning writes at READY; preserve all84 preflight product hashes.
