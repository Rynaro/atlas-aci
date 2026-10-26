---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-26T05:00:00Z
slice_id: ACI-S17
issue: https://github.com/Rynaro/atlas-aci/issues/38
---
# ACI-S17 — generation-owned exact and lexical candidates

## Scope

Implement issue #38 at base 73792406d65c81c6a5825ddd3b9cb1e26b195309,
/tmp/atlas-aci-aci-s17, branch aci-s17. Live marker and exact criteria digest
e0fdcf05dfa37bc19b9429887298468fde18473526f0fb29e3edc317b76969b3 verified.
Preflight records actual ancestor COMPLETE receipts and selected historical source/test
digests for S07/S08/S11, plus merged S13 interoperability. No prior S17 state/receipt.
Ledger:14 merged,28 remaining,current38,next39; frozen order is not numerical order.

In: exact canonical entity/qualified-label/path matches before SQLite FTS5 candidates,
one snapshot generation, stable filtering/ranking and truthful retrieval provenance.
Out: embeddings, network search, compiler namespace inference, term-offset/snippet
engine, new source-resource leases, adding retrieval to S13 pagination, changing old
operations, portable evidence format, paid models, deployment or dependency upgrades.
Three engineering days and one Vivi executor. Root owns validated CI/merge/main sync.

Declared paths: query/*, indexing/*, storage/* under mcp-server/src/atlas_aci and
mcp-server/tests/slices/test_s17_*.py. Before-edit narrow amendments:
- codegraph.py: native9 DDL/lifecycle/closed generation metadata and producer hashing,
  derivative population/validation, old6/7/8 adapters and exact staged publication.
- protocol/graph.py, protocol/results.py, server.py, tools/graph_query.py: one typed
  retrieval operation, intended-output validation and existing actual SDK byte caps.
- tests/test_schema_epoch.py, tests/test_export.py, tests/test_cli_export_import.py,
  tests/test_codegraph.py, tests/test_server.py: only affected epoch/producer/import/
  protocol assumptions; preserve all substantive negatives and portable7 assertions.
- tests/slices/test_s07_contract.py, test_s08_contract.py, test_s08_integration.py,
  test_s12_contract.py, test_s13_contract.py: only observed current-epoch/selected-old-DB
  fixture assumptions or direct compatibility controls; do not weaken historical proofs.
- mcp-server/tests/fixtures/s17/* and atlas/docs/* for real fixtures/explicit contract.
- .github/workflows/harden-gate.yml and scripts/test-verify-probe-verdict.sh: only fresh
  artifact selection after independent evidence approval.
- .spectra/plans/aci-s17.*, calibration log, changes/aci-s17/*, evidence/aci-s17/*,
  state/aci-s17.execution.json and the existing two backlog files.
Storage includes snapshots.py supported-epoch predicate/closed source context; query
includes IndexedReader native8/9 acceptance. No Config/enforcement/CLI changes assumed.
Record any further observed need before edits. Prior criteria/receipts/probes immutable.

Actual FULL6, complexity9, standard routing. Maker ramza-backlog-order-s17; independent
ATLAS full critique required. Prior rejected external memory destination remains terminal.

## Approach

### Native derivative, not a second search service

Current native8 canonical tables provide exact entity ID/name indexes, but no FTS or
qualified/path candidate layer. Live ripgrep search is not snapshot retrieval. Adding
FTS virtual and shadow tables changes the closed sqlite_master signature: use native9,
hand-maintained DDL/signature guards, and exact native6/7/8 read adapters. Portable
atlas-evidence.v2/native portable epoch7 records remain unchanged and omit FTS data.

Select ordinary INTERNAL-CONTENT FTS5. Do not use external-content/contentless FTS:
their content/posting integrity behavior differs. Keep deterministic candidate metadata
in ordinary keyed tables with lookup indexes; the FTS table indexes a text column and
stores candidate ID as unindexed metadata. Rowids are private build coordinates, never
candidate identities or output tie-breaks. Rebuild this small source-text derivative on
each staged full/incremental/import build, using sorted stable IDs for insertion. S09
extraction cache and S10 binding reuse remain unchanged; no separate FTS invalidation
engine is needed for this three-day slice.

All writers/importers/migrations finish documents, lookup indexes, FTS integrity and
generation metadata in the staged DB before atomic publication. Any indexing/validation
failure leaves previous G1 DB/source bytes usable and no half-published search. Keep
writer lock and existing source-pack publication/retirement ordering. Reader acquisition
opens one actual DB/source generation; never join a latest FTS sidecar to a held G1.

First checkpoint BEFORE broad API work: qualify actual host AND supported Linux image
FTS5 create/query/readonly semantic-integrity controls; build native9, preserve genuine
native8 G1 through successful/failed migration, import portable7 source-free, and prove
G1-held/G2-new searches use their own document sets. A missing FTS capability or integrity
primitive is a concrete blocker, not permission to drop Linux or silently upgrade SQLite.

### Exact identities and explicitly coarse source spans

Entity candidates preserve canonical entity IDs, language/path/owners/kind/name/ordinal.
Define qualified_label as a versioned, lossless canonical serialization of that literal
owner projection (document exact escaping and include path/language/ordinal). It is an
index key/display label, not a compiler-qualified name, inferred import alias or type.
Return the label so clients can repeat exact lookup. Tests cover same names/owners,
Ruby names containing ::, Rust impl token owners, nested scopes and duplicate ordinals.

Source lexical documents are WHOLE current successfully indexed UTF-8 files, span
[0, original byte length), not guessed term positions. This preserves all available
file text including comments and multi-line phrases. Return granularity=file explicitly;
the candidate identifies the matching source document, not a pinpoint occurrence.
No clipping or partial-document indexing merely to satisfy a byte budget. Non-UTF8,
failed, stale retained and unavailable imported source documents are not fresh lexical
documents: report distinct excluded/unavailable counts/reasons and partial coverage.
Current failed-file hashes must not be used to relabel retained old facts as fresh.

Build file text from the EXACT consumed buffer/explicit overlay used for source hashing
and source-pack creation, including cache-hit builds. Never reread live files later.
Strict UTF-8 decode must round-trip to those exact bytes; NUL and multi-byte characters
need real controls. Source candidate ID commits path/source hash/span/kind; returned
provenance also binds repository/G1/snapshot. Entity metadata documents carry names,
literal qualified labels and paths without fabricating source text.

Candidates contain real entity/occurrence/evidence IDs when available and exact source
references with availability state. A file-span candidate is justified by authenticated
generation source membership; do not invent an assertion ID for a phrase in a comment.
No atlas-source resource URI is minted: S13 resource issuance requires retained ownership
and assertion membership, which this slice does not change. Source-free imports can
retrieve exact/name/path entity metadata, but source lexical coverage is unavailable.
Legacy unknown owners/spans stay unknown; never scan destination working-tree bytes.

### Closed provenance and integrity

Add closed atlas-generation.v5 for native9, preserving the complete existing v4 fields
and their validation, plus a retrieval descriptor: format/version, tokenizer/document
policy, exact document count/coverage and canonical logical-document digest. The digest
is over sorted typed document metadata/text hashes, independent of FTS rowids or segment
layout. Existing graph/source commitments do NOT implicitly cover new FTS tables.
The descriptor is included in generation identity, so same complete inputs replay;
source-free import/legacy variants explicitly state which text is unavailable.

Native9 may not claim v4-with-missing-search metadata. Preserve exact old v1–v4 variants
for old6/7/8 only. Native9 build requires available authenticated source snapshot; import
requires explicit source-unavailable/import. Legacy conversion permits only its existing
honest provenance state: available bytes must come from an authenticated predecessor
pack, otherwise use a closed unavailable/legacy-source variant and metadata-only search.
No foreign/missing source exception for a modern native build. Native8 migration must
not read today's worktree to reconstruct yesterday's source or erase historical evidence.

On opened own-DB validation recompute document-set closure against canonical current
entity records and observations; no duplicate IDs, foreign repo/path, stale documents,
missing eligible source rows or extra source hashes. Compare source text bytes/hash/size
against authenticated snapshot bindings and current indexed observations; metadata-only
documents must exactly match their canonical entity/qualified projection. Missing source
coverage must agree with provenance, not a caller flag. Preserve all existing canonical,
projection, history and source-range validations independently.

Validate FTS content/posting consistency with a genuinely read-only supported primitive,
not just a rehashed stored descriptor. Independent actual host SQLite3.53.1 and Linux3.46.1 internal-content
PRAGMA integrity_check(docs) controls detect stale shadow content; external-content
counterexamples do not. Require EXACT [("ok",)] diagnostic rows, not SQL success alone.
The Linux image d788f9e9ace4e4bad602859ee24d2fd1119778e01ead6c44d50aa1252b24d18b
ran UID10001, --read-only, --network none, proof mount:ro; original DB inventories were
unchanged. Archived scripts/results/positive and corrupt fixtures are under
evidence/aci-s17/planning/fts/. First Linux harness failed only a tuple/list assertion;
that failure and the corrected successful run are both retained. Product native-table
controls must still run; primitive success is not product validation. Use the qualified explicit
integrity operation per acquired retrieval context; never trust persisted success or a
cross-request cache. If runtime support is insufficient, stop at checkpoint with exact
evidence for reviewed remediation; no hidden write to readonly DB or unchecked fallback.
Integrity may authenticate whole derivative/source closure before query and is separately
measured; do not advertise total request work as only bounded ranking work.

Hash every new executable indexing/storage producer module in _producer_inputs. Extend
storage/evidence.py with exactly the new allowed module set, preserving all historical
closed producer sets; no arbitrary module allowance. Policy/tokenizer/derivative version
changes invalidate incompatible reuse. Current record identities/portable semantics and
source-pack ranges remain valid; query modules do not invent new graph relationships.

### Retrieval protocol, ordering and work limits

Add one atlas.graph.v1 op=retrieve to existing graph_query. Closed input contains query
text (1..4096 UTF-8 bytes), optional expected_generation_id, repository equality filter,
bounded normalized relative path-prefix list (at most16), max_candidates (1..100),
max_bytes and finite deterministic SQL-work/deadline bounds. Reject bool/invalid bounds,
traversal paths, mixed legacy/unknown operations before execution. Keep seven tools and
all old status/navigation/page/continue semantics; no pagination nesting for retrieve.

Ordering tiers: exact canonical entity ID; exact qualified_label; exact normalized path;
exact literal entity name; remaining lexical candidates. Exact tiers always precede
FTS score. Within exact ties use stable candidate ID; within lexical ties use ascending
bm25 then stable candidate ID. Deduplicate candidate IDs across tiers without collapsing
different entities or file-span evidence. No insertion order, SQLite rowid, graph degree,
timestamps or timing in ordering. Return match tier and honest lexical score separately
from semantic confidence (FTS relevance is not EXTRACTED/INFERRED confidence).

Use one documented literal-phrase syntax for FTS: escape query text into a bound FTS
phrase, with fixed unicode61 tokenizer policy recorded in the derivative. Quotes,
operators, punctuation and SQL fragments remain data, not user-authored MATCH syntax.
Define case/diacritic behavior explicitly and test it. Tokenless input is a valid no-match
with diagnostic reason, not corruption. SQL/FTS operational errors produce typed errors,
never an empty success. Do not expose raw SQL or silently fall back to live regex search.

Apply repo/path filters inside every exact and lexical candidate query BEFORE ranking
and cap. Prefix comparison must be normalized path-component aware, not an accidental
LIKE wildcard. Excluded high-rank candidates must not starve allowed lower-rank matches.
Repository filter mismatch yields an empty filtered result within the current repo;
it never selects another graph. Stable global FTS corpus statistics are generation-bound;
filtering need not rebuild corpus scores. Diagnostics state this scope.

LIMIT does not bound FTS posting traversal or score sorting. Use the dedicated lease's
owned SQLite progress hook with a finite VM-operation allowance and cooperative deadline,
restore/clear it in finally, and reserve/count row hydration before work. Initial defaults:
1000000 VM operations,2000ms ranking deadline,100 candidate cap; explicit finite upper
bounds prevent callers removing limits. Count exact lookups, FTS calls, hydrated rows,
and interrupted operations honestly. Only select small keys/scores for ranking; hydrate
bounded admitted results afterwards. Set connection-local temporary storage policy to
avoid unexpected temp-file writes and qualify actual read-only Docker behavior.

An interrupted SQL/ranking operation returns a bounded RETRIEVAL_BUDGET_EXCEEDED error,
not a timing-dependent partial candidate set or false empty/no-match. Thus successful
same-query/snapshot results have identical ordered candidates; timing diagnostics remain
outside canonical identity. Output-byte admission takes a deterministic prefix of whole
candidate/provenance atoms, reports truncation and effective limits; never clips hashes,
spans or proof links. Preserve exact-first ordering even at a tiny valid result budget.

Validate intended-operation output explicitly; no retrieve result may pass a status or
legacy schema branch. Existing full SDK CallToolResult measurement includes structured
plus escaped compatibility text/flags; final negotiated result must fit max_bytes.
Fixed below-minimum/errors use measured existing error-cap policy. JSON-RPC framing
remains separately measured. Direct and native stdio must agree on errors and candidates.

### Compatibility and actual evidence

Native9 keeps old6/7/8 exact readers without writes. Old navigation retains native8
support and gains9 only when all required indexes exist. Old lexical retrieval reports
INDEX_UPGRADE_REQUIRED; it does not auto-migrate on serve. Preserve S13 held G1 leases
and source-resource cursors through new publication/retirement. snapshots.py admits9
only with exact context/schema ownership; bool, unknown epochs and coherent foreign
context attacks still reject. Portable7 export/import remains strict and source-free.

Any producer change requires fresh exact-pin DEFAULT_LANGS evidence, even if graph
facts appear unchanged. Native epoch9 changes the behavioral fingerprint. Preserve
Solidus4026945d614e81383c007ed1ab1278a0195ce5d9 and
Spree6699cde44303ea85ef6e56c5e87c44a738ab73fc, all input manifests, verifier/bar/seeds.
Use .spectra/changes/aci-s17/probe/aci-s17-probe-lpa-vs-louvain.{json,md} and colocated
probe-graphs.json.gz, with raw evidence under evidence/aci-s17/probe/. Charge every
attempt:600s per job,2700s cumulative. Independently audit before two selector updates;
never re-sign old measurements. Measure new FTS disk/build/acquisition/query costs,
do not invent performance/quality thresholds. Preserve compact lossless evidence and
remove only approved disposable DB/pack/full-export scratch after audit.

## Stories

Theme: deterministic task context. Project: v3 retrieval. Feature: exact and lexical
candidates. Vivi standard executor, one writer, total three engineering days.

1. As an operator I publish authenticated searchable generations. Timebox1 day,P0:
   native FTS/runtime corruption checkpoint, document/source closure and native9/old8
   staged migration/import adapters; keep G1 on actual injected failure.
2. As an agent I retrieve relevant exact/source candidates. Timebox1 day,P1: labeled
   fixture first, exact indexes/qualified labels, literal phrase FTS, filter-before-rank,
   deterministic diagnostics/work interruption and typed bounded SDK operation.
3. As an operator I retain compatible truthful evidence. Timebox1 day,P0: native readonly
   host/Linux, G1/G2 barriers, cache/full/incremental parity, corruption/export/sourcefree
   and old-operation regressions, full/static/fresh probe, receipt/reentry. Stop at limit
   with completed checks/gaps; no new backend, runtime upgrade or unbounded follow-up.

## Acceptance Criteria

Exact aci-s17.criteria.md is the six product plus two RUN contract. Add real parser/
SQLite fixtures with ambiguous names, qualified owners, same-path utilities, multi-line
unique task phrase, comments and irrelevant high-degree utilities. No mocked retrieval.
- test_exact_identity_first: exact canonical/qualified entity beats frequent lexical
  utility; path/name tiers and duplicate-owner ties preserve stable identities.
- test_lexical_candidate: unique source phrase yields the exact authenticated whole-file
  byte span/hash with granularity=file; test multi-line/UTF8/comments and no invented term
  or assertion coordinates. Source bytes must equal the consumed/indexed buffer.
- test_search_generation: real G1 held/G2 newly acquired searches return only their
  generation, with actual changed text, deletion and writer publication barriers.
- test_no_match: genuine absent phrase returns empty success; malformed request,
  corrupted FTS and interrupted query remain distinct nonempty error envelopes.
- test_search_filter: excluded exact and FTS matches absent; lower allowed match remains
  despite excluded top ranks; normalized path boundaries and repo isolation proved.
- test_retrieval_replay: repeated fixed-snapshot successful searches preserve all ordered
  IDs/tiers/scores/provenance; cold/full/incremental/cache builds agree on canonical
  documents and ranking, independent of insertion order and temporary rowids.
- RUN01/02: concrete implementation commit, unchanged manifest on clean reentry and one
  authoritative interrupted-resume identity/receipt.

Additional required negatives: FTS posting/content disagreement; descriptor rehash with
foreign/missing/stale docs; text inconsistent with source hash/span; duplicate IDs;
legacy/sourcefree imported text falsely claimed available; failed parser observation
never reused as fresh source; actual read-once edit; no partial FTS publication; genuine
native8 archived fixture and held source lease; unsupported9-like schema failclosed.
Real progress interruption followed by normal query proves cleanup. EXPLAIN/actual row
counts characterize indexed exact queries and FTS virtual-table work/sorting, without
pretending LIMIT alone bounds work. Native Linux corruption test is mandatory, no skips.

Commands, exact exits recorded:
```sh
UV_CACHE_DIR=/tmp/aci-s01-uv-cache /tmp/aci-s01-tools/bin/uv sync --frozen --project mcp-server
cd mcp-server
.venv/bin/python -m pytest tests/slices/test_s17_contract.py -q
.venv/bin/python -m pytest tests/test_schema_epoch.py tests/test_export.py tests/test_cli_export_import.py tests/test_server.py tests/slices/test_s12_contract.py tests/slices/test_s13_contract.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/mypy src/
```
Run actual current CI/static/probe self-tests, real SDK protocol versions and UID10001
source+graph:ro/network-none Docker with Linux runtime. No compiler or paid model claim.
Measure complete result bytes and failed/error framing, real lease cleanup on errors/
controlled cancellation, and unchanged old resources/cursors. Never claim sync preemption.

Full-tier consistency checks: publication-flow(buffer→document→FTS→integrity→G1/G2),
query-flow(exact/filter→FTS/rank→bounded proof→SDK), and failure-flow(old/import/missing/
corrupt/deadline/readonly) cover all six criteria. Independent checker challenges source
granularity, derivative closure, closed schema adapters, bounded work and three-day fit.

## Confidence

Compute actual Assemble confidence after independent review and available native
primitive evidence. Integrated native9/FTS/source closure is not
proven by document lint or the qualified host/Linux primitive. Honor the computed verdict.

## Rejected Alternatives

Separate generation FTS sidecar scored72.5: adds another publication/retention race and
readonly catalog. Native occurrence-only FTS scored78.5: narrower stored evidence misses
source phrases in comments/outside extracted spans. Native current-file FTS plus exact
entity indexes scored85: preserves available text and literal identities with explicit
coarse spans and one atomic DB, without a snippet/semantic provider platform.

## Risks

P0 native virtual-table compatibility/integrity: real Linux and old8 checkpoint before
adoption. P0 source misattribution: consumed bytes and own-generation closure, explicit
unavailable/stale states. P1 file granularity: useful lexical baseline, not pinpoint term
locations; disclose it. P1 expensive FTS sorting: progress allowance and errors, no false
latency claim. P1 three-day breadth: full derivative rebuild avoids incremental platform.

### Receipt and handoff

One .spectra/state/aci-s17.execution.json, exact frozen identity. Adapt S13 existing
verify_execution.py/verify_resume.py; inspect partial receipt before init. Concrete output
commit and all per-AC commands/exits/digests precede COMPLETE. Exclude exactly six
self-referential/post-output metadata paths from output digests:
.spectra/state/aci-s17.execution.json;
.spectra/evidence/aci-s17/final-reentry-before.json;
.spectra/evidence/aci-s17/final-reentry-after.json;
.spectra/evidence/aci-s17/final-reentry-summary.json;
.spectra/evidence/aci-s17/final-resume-identity.json;
.spectra/evidence/aci-s17/output-check-tests.log.
All product/tests/docs/frozen plan/stable evidence stay hashed. Reentry returns
ALREADY_SATISFIED with identical tracked manifests and git diff --exit-code; interrupted
resume preserves slice_id/criteria_sha256 and one receipt. Root mechanical ECL block plus
message/thread/spec-correlated verify_pass precedes single Vivi writer. Planning handoff
must preserve all96 preflight product hashes. Root owns green-CI merge/closure, not coder.
