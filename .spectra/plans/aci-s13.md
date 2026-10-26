---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-26T03:24:00Z
slice_id: ACI-S13
issue: https://github.com/Rynaro/atlas-aci/issues/35
---
# ACI-S13 — leased evidence resources and immutable result pages

## Scope

Implement issue35 at merged basebfa0e5935d5aefdd2d3c5c045eca1b2b08ebf56b,
/tmp/atlas-aci-aci-s13, branch aci-s13. Exact live criteria SHA:
327911042d4d02566de953b5dab9f2c8fab043c06c7f1184f1d2795faee945ef.
Preflight verified clean base/marker, no existing S13 state/receipt, actual S08/S11/S12
ancestor COMPLETE output digests547/256/319. Ledger13 merged/29 remaining,current35,
next38 preserves frozen priority. A completed predecessor is not inferred from closure.

In: content-addressed source-span resources, opaque scope-bound continuations for
bounded query results, exact G1 ownership across requests, expiry, bounded reads and
replay. Out: permanent generation retention, live-worktree fallback, resumable global
search beyond the original query work limits, cross-process cursor portability,
multiuser authentication, persistent credentials, subscriptions, new tools/packages,
paid evaluations, deployment or performance claims. Three engineering days, one Vivi
implementation allocation; root owns validated publication/CI/merge/main sync.

Declared issue paths: mcp-server/src/atlas_aci/protocol/*, query/*, storage/*,
server.py and mcp-server/tests/slices/test_s13_*.py. Narrow amendments before edits:
- mcp-server/src/atlas_aci/codegraph.py: explicit ownership transfer/retained reader
  acquisition and optional source-chunk authentication context only; no semantic graph
  change, native epoch/portable format change or ordinary lease validation bypass.
- mcp-server/src/atlas_aci/config.py: validated operator retention/capacity policy;
  no implicit environment precedence, CLI redesign or authentication provider.
- mcp-server/src/atlas_aci/tools/graph_query.py and enforcement.py: typed paging dispatch,
  resource rate/scope checks and unchanged central result limits.
- mcp-server/tests/test_server.py: directly affected operation/resource schema and
  bounds registry tests; mcp-server/tests/fixtures/s13/* for small real fixtures.
- atlas/docs/*: exact pagination, source-byte, lifetime/scope and budget contract.
- .github/workflows/harden-gate.yml and scripts/test-verify-probe-verdict.sh: exact
  fresh S13 artifact selection only after independent genuine evidence approval.
- .spectra/plans/aci-s13.*, .spectra/plans/ramza-calibration.jsonl,
  .spectra/changes/aci-s13/*, .spectra/evidence/aci-s13/*,
  .spectra/state/aci-s13.execution.json, .spectra/backlog/execution.json and order.md.
Record additional observed test needs narrowly before edits. Preserve old criteria,
receipts, plans, portable evidence and probe artifacts. No broad retention framework.

Actual FULL6, complexity9(2,2,2,3), standard routing. Maker ramza-backlog-order-s13;
independent ATLAS full review required. P0 alone does not activate TRANCE. External
memory rejection remains terminal; reuse local source/proofs without retry.

## Approach

### Decision: pagination of one bounded result, not renewed traversal

S12 navigate() owns local queue/visited/parents and discards them after returning;
its frontier hash is not a continuation token. Its byte admission can reject an
already examined proof. S11 dispatch closes the graph/source lease at request end.
S08 writer retention keeps only current+previous source names; G3 can unlink G1,
although an already-open SQLite/pack FD continues to identify the original bytes.
Reopening a pathname or signing the current query cannot satisfy unexpired G1 cursors.

Select process-owned immutable bounded result pages. A NEW opt-in operation runs
ONE real S12 traversal into a separately bounded retained collection, then constructs
all pages before issuing a lease/cursor. It uses the original node/edge/depth/deadline
limits; collection admission measures its total retained bytes/records, NOT the
first page's wire limit. Preserve every admitted record and the exact terminal work
frontier/reason. Page continuation advances through that captured collection only.
There is no cursor after it is exhausted, and no claim to continue BFS beyond a
terminal work/deadline/retained-collection limit. Existing non-opt-in operations and
their canonical result domain remain unchanged.

ATLAS independently reviewed this criterion fit: live issue says continue bounded
results; AC002 requires page2 on G1 and AC006 repeated-cursor canonical pages, not a
resumable search engine or refreshed traversal allowance. Pagination is faithful
only with the collection/terminal/capacity/whole-record rules above and below. This
is an explicit design choice, not an acceptance waiver or first-page-only spool.

### Actual SDK resource seam and first checkpoint

Locked mcp1.27.0 read_resource() accepts Iterable[ReadResourceContents], internally
constructing BlobResourceContents(base64) and ReadResourceResult. Returning a direct
ReadResourceResult is not that decorator's advertised path. Actual independent stdio
proof negotiated2024-11-05 and2025-06-18, preserved full bytes41c3a9005a, split-UTF8+NUL
bytesa900 and empty content. _meta offsets survived; SDK model_dump_json(by_alias=True,
exclude_none=True) result bytes177/170/169 exactly matched wire result bytes. Outer
JSON-RPC frames211/204/203 were separately measured. Both servers/command exited0.
Archived source/raw frames/results/hashes live under evidence/aci-s13/planning/sdk/.

Resource errors are JSON-RPC ErrorData, not CallToolResult/isError. Primitive custom
error codes demonstrated transport only and are not product code choices. Use the
standard errors for the actually supported protocol revisions: resource missing/expired
-32002, invalid URI/scope/budget -32602, internal -32603, with fixed bounded reason data.
Do not adopt a newer protocol lifecycle or reuse elicitation error codes.
Reference: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-06-18/server/resources.mdx

First implementation checkpoint: actual G1 page issuance transfers one authenticated
SQLite+pack lease into owned registry; G2/G3 publication/pruning leaves that cursor and
an issued resource reading G1; repeated page returns identical saved bytes. Also prove
quota rejection before issuance and native bounded resource/error serialization. Stop
if these require a new persistent graph service or violate readonly deployment; no
latest-generation fallback or invented primitive success.

### Typed entry points and immutable pages

Add graph_query op=page with a closed nested query restricted to the existing
neighborhood/path request schema, plus page_max_bytes. Nested status/legacy/page/
continue are rejected; expected_generation_id still guards first acquisition.
Add op=continue with cursor and the original query_digest. Keep seven tool names.
Normalize effective query filters, selectors, direction, confidence, overall work
limits, element/page limits and configured authority before computing query_digest.
The server computes this digest; caller-supplied digests cannot confer authority.

The page operation collects a genuine S12 result under a finite retained-collection
cap, while preserving S12 checks/SQL counts/deadline and source/history isolation.
Adapt admission narrowly; do not duplicate the resolver/traversal or call it first
under the small page byte limit. A distinct retained_collection_limit terminal reason
must be explicit if collection admission stops the search. Captured terminal outcome,
frontier and counters are immutable metadata shared by every page. Disclose mandatory
lease authentication separately from traversal/setup clocks, as S12 already does.

Pagination atoms are operation-specific:
- Neighborhood: whole assertion proof with both referenced endpoint identity
  projections; seed/selection and query metadata remain explicit. Parallel proofs
  retain distinct identities, no dangling node references or skipped admitted edges.
- Path: one whole coherent path atom, including every hop proof. Do not split it
  into a falsely successful path prefix. If the whole atom cannot fit page_max_bytes,
  reject issuance with PAGE_TOO_SMALL under a bounded error, not partial path success.
- No-evidence/disambiguation/empty partial: one bounded truthful result; no fabricated
  continuation when no captured records remain. Preserve bounded candidate semantics.

Pages explicitly describe a projection of the captured result: item offsets/counts,
whether more captured items exist, captured query outcome/stop reason, G1/snapshot,
query_digest and next cursor. Page completeness is distinct from query completeness.
A final page can exhaust the saved collection while still reporting a partial query.
No duration/current timestamp/remaining TTL is recomputed inside a saved canonical
page. Tokens/resources are allocated once during issuance and included in those saved
bytes; replay never generates new references or reruns elapsed-time-sensitive traversal.
The new lease issuance is an operational episode; do not alter default S12 replay
semantics or claim separate newly issued leases have identical opaque credentials.

Construct and validate every page, resource descriptor and next-token before exposing
page1. Charge their actual retained bytes. A record that cannot fit or total state that
cannot be retained rejects the request before any usable cursor/resource is issued;
do not evict another unexpired promise or silently omit already admitted records.
Page requests do not reacquire latest graph or rerun SQL/traversal. Read cached canonical
pages under their original lease, with current authority/expiry checked first.

### Ownership, capacity, expiry and honest authorization scope

Use one small per-server/session registry, not global module state or disk lease files.
Each issued lease owns its actual reader context, SQLite connection, source-pack FD,
immutable pages, issued-span descriptors and necessary authenticated source metadata.
Use explicit ownership transfer so the request ExitStack does not close adopted handles;
failed setup/error/cancel closes the still-unpublished context exactly once. Do not
create or use SQLite connections in another thread. Serialize registry mutations and
same-lease access on the existing event loop; concurrent duplicate cursor requests
return the same saved page rather than advancing mutable state.

Initial operator policy defaults, validated as finite positive integers (bool rejected):
4 pending+active leases;300-second absolute monotonic lifetime;16MiB retained canonical
state per lease/64MiB total;512MiB total referenced pack bytes;256 pages and4096 issued
resource descriptors per lease;2000ms bounded post-acquisition setup. These are explicit
capacity policies, not performance acceptance thresholds. Expose via Config fields,
document them, allow tests/operator construction to choose smaller valid bounds; no
new env/CLI/secret-management surface. Ordinary nonpaged operations stay unaffected.

Reserve a pending lease slot before acquisition; count pending plus active handles.
Before issuance account pages, descriptors, tokens, chunk catalogs and retained reader
metadata (manifest/observations) against canonical-state byte limits, and pack lengths
against referenced-pack capacity. Bound temporary collection/setup as well as committed
registry storage; no unlimited pending spools. Use finite cardinality plus retained
canonical-byte accounting, explicitly not a claim that Python RSS equals serialized
bytes. Measure actual peak RSS/FDs separately. At most one SQLite and one retained pack
FD per active lease; transient validation dirfds are separate and must close.

Reject new work before issuance when capacity is exhausted. No LRU eviction or sliding
expiry of unexpired leases. An issued lease expires at its fixed deadline; reads/replay
do not renew it. A server-owned timer/task must close expired handles even while idle,
and shutdown closes every active/pending context. Cancellation during setup closes the
unpublished lease; cancellation of a read does not consume/invalidate an otherwise
unexpired saved cursor. Final-page consumption is nondestructive: retain its replay
and resources until expiry, not an immediate close that breaks AC006.

Authority is the actual local operator/session/config scope: canonical repository and
graph namespace, relevant source/read policy and effective caps, plus server-instance
identity. There is no authenticated multiuser principal in the current stdio server;
do not invent one or claim remote authorization. Recompute/check relevant current
configuration on every continuation/resource read; changed scope rejects old handles,
including a narrower output cap. Roots/skip/permissions are server-derived, not supplied
inside a trusted cursor. Keep source-free imports/legacy unknowns unavailable for bytes.

Opaque bounded tokens use a closed versioned payload and stdlib HMAC/constant-time
comparison under an ephemeral process secret, binding kind, instance, lease, repository,
generation, query digest and authority digest; cursor also binds page index. No persistent
credential changes. Bound token/URI lengths before decoding, reject duplicate/extra keys,
noncanonical encodings, wrong kind, malformed MAC and scope mismatch. A valid signed
token whose lease was retired yields EXPIRED_REFERENCE. Restart intentionally retires
all process-local references; a foreign/old instance is rejected without claiming its
unknown MAC was authenticated. Do not disclose data for tampered/cross-scope tokens.

### Source-span authorization and bounded authenticated reads

Issued resource identity deterministically commits repository/G1/snapshot, path, full
source hash, half-open byte span and supporting evidence/occurrence identity. Its bytes
are content-addressed through the authenticated source hash plus exact range; no line
number/current-worktree interpretation. URI uses a custom atlas-source scheme, fixed
resource ID and an opaque resource-kind lease token, with relative offset/max_bytes for
bounded chunk reads. Registry lookup supplies actual paths; URI text never opens a path.
Validate URI shape/length, current authority, lease, exact issued descriptor and offset
before reading. Only resources issued from real returned proof membership are available;
compact min/max/count source summaries alone do not authorize arbitrary evidence spans.

Verify assertion→evidence→occurrence→source binding against the owned G1 connection
during issuance. Preserve observed versus historical provenance; a descriptor references
its exact proof bytes, never substitutes current same-path data. No generated resource
for unavailable source/hash/span. Resource read after source edits or G3 pruning uses
the held pack FD; missing/corrupt bytes are errors, never a filesystem fallback.

Add a bounded SourceLease span-read method without changing persisted pack/manifest
formats or existing read(). For resource-owning leases only, derive private64KiB chunk
hashes from the SAME bytes used by the existing full-blob SHA verification at acquisition.
Install a blob's chunk catalog only after its full expected hash matches; bind hashes
to blob and relative chunk offset. Precheck/count catalog capacity and include it in
lease state limits. Ordinary request leases need not retain this extra catalog.

Read only touched aligned chunks from the held FD, verify each private chunk hash and
return slices from THOSE verified buffers, not a second unverified reread. This preserves
post-acquisition in-place tamper detection. Per read source I/O is at most returned raw
bytes plus two64KiB boundary chunks; allocation is bounded chunks plus admitted output,
not the entire file/span. Check exact int offsets/bounds, EOF, empty spans and source
membership. The initial whole-pack/full-blob authentication remains real and separately
accounted; a source-byte budget is not a promise to avoid initial integrity validation.

### MCP resource callbacks and full response budgets

Register the locked SDK's list_resources/list_resource_templates/read_resource seams;
advertise one bounded template, not an unbounded list of issued paths. No subscriptions
or lifecycle replacement. Return one ReadResourceContents containing bytes with
application/octet-stream and versioned _meta describing resource/G1/hash/span, returned
offset/end, completion and next URI. Base64 preserves arbitrary bytes, split UTF-8 and
NUL; do not decode/clamp source text or split code points by accident.

Compute exact ReadResourceResult serialization including URI, base64 expansion, _meta
and next URI before final admission. Choose a bounded raw chunk length that fits the
requested max_bytes and server cap; no whole-file materialization to find a prefix.
Complete bytes refer to the original authorized span, while returned offsets describe
this chunk. An empty valid span succeeds. A budget that can represent metadata but not
one byte must return an explicit minimum-budget error rather than an endless same-offset
next URI. Below minimum uses the documented fixed ErrorData cap. Error data/messages
are fixed/bounded and do not echo arbitrary URI text.

Tool page results use the existing actual SDK CallToolResult measurement including
structured+compatibility text/escaping/flags. Resource results use ReadResourceResult;
resource errors use ErrorData. Never apply tool isError semantics to resource errors.
Publish measured minimums and fixed error caps separately. JSON-RPC framing/request IDs
are measured and disclosed separately, not falsely included in the result-byte claim.
All valid page/resource results fit the caller's effective result cap; raw source bytes
or one JSON representation alone are not sufficient accounting.

Preserve S11 negotiated protocol behavior, intended-operation validation and all legacy/
status/S12 fixtures. New page/continue inputs have explicit enum branches; malformed
tokens cannot fall through legacy validation, and a status-shaped result cannot satisfy
another operation. Resource rate limiting applies mechanically under existing operator
policy. Direct calls and real stdio must enforce the same authority and byte rules.

### Fresh evidence, bounded execution and cleanup

Any producer/source module change requires genuine fresh same-pin/default-profile
evidence, even if the behavioral fingerprint is unchanged. Preserve Solidus
4026945d614e81383c007ed1ab1278a0195ce5d9 and Spree6699cde44303ea85ef6e56c5e87c44a738ab73fc,
all default languages/inventories, verifier/bar/seeds/resolution and historical artifacts.
Use new aci-s13-probe-lpa-vs-louvain.{json,md} plus colocated probe-graphs.json.gz under
.spectra/changes/aci-s13/probe/, raw evidence under evidence/aci-s13/probe/. Record current
producer/runtime/source hashes and actual costs, with600s/job and2700s cumulative including
failures. Switch only the two selector files after independent ATLAS artifact audit/PASS.
Never re-sign old metrics. Preserve compact lossless evidence; remove only explicitly
scoped disposable DB/pack/full-export scratch after audit, not source seeds/SDK/history.

## Stories

Theme: inspectable consistent evidence. Project: v3 navigation. Feature: bounded
leased resources/pages. Executor Vivi, standard tier, single writer.

1. As a client I retain G1 while paging bounded results. Timebox1 day, P0: registry
   ownership, atomic admission, real collection/page construction, scoped tokens,
   fixed expiry and cached replay; first G1→G2/G3/duplicate/capacity checkpoint.
2. As a client I inspect exact proof bytes within a response cap. Timebox1 day, P0:
   issued-span membership, acquisition chunk catalogs, bounded verified pread, native
   resource callbacks/encoding/errors and post-acquisition corruption negatives.
3. As an operator I retain bounded readonly lifecycle and compatibility. Timebox1 day,
   P0: real idle expiry/cancel/shutdown/restart, host↔Docker pruning and offline stdio,
   full/static/genuine fresh-probe gates, docs and receipt/reentry. At three days report
   remaining gaps; no persistent service/resumable global BFS expansion.

## Acceptance Criteria

Exact aci-s13.criteria.md remains the six product plus two RUN contract. Named tests
in tests/slices/test_s13_contract.py must observe actual behavior:
- test_exact_source_resource (AC001): real parser/index query issues proof URI, source
  edits and G2/G3 publication occur, resource returns original bytes/hash/span from G1;
  unavailable/imported provenance never gains a fabricated live source.
- test_continuation_snapshot (AC002): page1 has a real next cursor, publish newer graph
  and prune old names, then page2 uses G1. Require actual retained SQLite/pack and evidence
  identity, not a latest rerun or only a generation label on mocked data.
- test_expired_reference (AC003): real lease retirement closes handles; resource returns
  explicit expiry, with separate missing/corrupt/tampered/scope controls and no fallback.
- test_cursor_scope_mismatch (AC004): different repository/query/filter/operator config,
  kind and token corruption rejected; a forged digest cannot bypass registry/MAC checks.
- test_resource_budget (AC005): span larger than full byte cap; actual SDK result remains
  bounded with exact decoded bytes/offsets/base64/_meta/URI, zero/below-minimum and UTF8
  split/NUL cases. Instrument real pread to prove bounded reads, not whole-file slicing.
- test_cursor_replay (AC006): consume same cursor twice, including concurrent duplicate
  calls and different wall-clock delay; exact cached canonical page/next token/resource
  refs unchanged, no repeated SQL/traversal or mutable work frontier.
- RUN01/02: actual output commit/digests, clean no-op reentry and interrupted identity.

Additional nonvacuous tests: small page budget versus larger retained collection proves
later admitted records are actually delivered without gaps/duplicates; all page concatenation
matches the captured bounded query, terminal partial metadata preserved; no cursor past
collection end; indivisible oversized path rejection; unexpired lease cannot be evicted by
capacity pressure; canceled setup has no issued token/FD leak; actual idle timer expiry;
shutdown/restart cleanup; real source mutation after acquisition fails chunk verification;
cross-chunk/EOF/empty reads; whole-source/hash/range/foreign-graph negatives remain.

Use actual native Docker UID10001/source+graph:ro/network-none and host-macOS→Docker
publication/pruning barriers already supported by S08. Do not assume advisory lock
coherence across those kernels. Prove old pathname disappearance and held-FD original
bytes; measure active FD/state/pack bounds and final closure, without claiming immediate
physical space reclamation. Controlled cancellation is not synchronous preemption.

Commands (record exact commands/exits, no required skips):
```sh
UV_CACHE_DIR=/tmp/aci-s01-uv-cache /tmp/aci-s01-tools/bin/uv sync --frozen --project mcp-server
cd mcp-server
.venv/bin/python -m pytest tests/slices/test_s13_contract.py -q
.venv/bin/python -m pytest tests/test_server.py tests/slices/test_s11_contract.py tests/slices/test_s12_contract.py tests/slices/test_s08_contract.py tests/slices/test_s08_lease_footprint.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/mypy src/
```
Run current CI scripts/static/export/probe self-tests and real native tests with the
qualified image/SDK. No compiler invocation or paid model evaluation is implied.

Full-tier consistency: ownership-flow(acquire→collect→admit→issue→replay→expire),
proof-flow(G1→canonical span→chunk auth→bounded SDK bytes), and failure-flow(scope/
tamper/capacity/deadline/prune/cancel/restart) cover all six criteria and their negative
controls. Independent checker must challenge spool criterion fit, dropped records,
path coherence, exact scope/MAC retirement, all retained-state accounting, source
chunk integrity and result-versus-transport budgets. Primitive success is not product proof.

## Confidence

Compute actual Assemble confidence after independent review. Existing descriptor and
SDK primitives support the approach; integrated registry/capacity/expiry/chunk-proof
behavior remains execution work. Honor the real verdict; do not manufacture human review
or infer confidence solely from the selected Explore score.

## Rejected Alternatives

Writer-retained graph catalog with stateless cursors scored72.5: requires a second
retention/publication contract and readonly coordination that the three-day slice does
not need. Owned-lease resumable BFS scored80: richer future search continuation, but
mutable checkpoints/deadline replay and deferred proof admission add avoidable risk.
Owned-lease immutable bounded pages scored85 and is selected: meets reviewed pagination
ACs using one captured frontier and exact saved pages, with terminal limits explicit.

## Risks

P0 stale ownership/latest fallback: transfer real G1 context and prove G3/prune. P0
capacity promises: admission atomic before issuance, no unexpired eviction, finite
pending/active states. P0 replay drift: cached pages include their original next refs.
P0 span authorization: exact own-graph proof, not compact aggregate membership alone.
P0 bounded read weakens integrity: same-buffer acquisition chunk hashes and verified-buffer
slices, real in-place corruption tests. P1 scope overclaim: local session/operator only,
restart retirement explicit. P1 three-day breadth: no new durable service or BFS engine.

### Receipt and handoff

One .spectra/state/aci-s13.execution.json for ACI-S13 plus frozen digest. Adapt existing
S12 verify_execution.py/verify_resume.py into S13 evidence paths; inspect/resume an
existing partial receipt rather than reset. Record actual output commit, scope, per-AC
commands/exits/evidence and tracked implementation digests before COMPLETE.

Exclude exactly six self-referential/post-output metadata paths from output digests:
.spectra/state/aci-s13.execution.json;
.spectra/evidence/aci-s13/final-reentry-before.json;
.spectra/evidence/aci-s13/final-reentry-after.json;
.spectra/evidence/aci-s13/final-reentry-summary.json;
.spectra/evidence/aci-s13/final-resume-identity.json;
.spectra/evidence/aci-s13/output-check-tests.log.
All product/tests/docs/frozen plan/stable pre-output evidence remain hashed. Concrete
implementation commit precedes metadata-only receipt proof. Clean reentry must return
ALREADY_SATISFIED with identical manifests and git diff --exit-code against output;
real interrupted resume preserves slice_id/criteria_sha256 and one receipt path.

Root mechanical ECL verify-envelope --block and correlated message/thread/spec trace
precede Vivi. Required artifacts include exact criteria/state/plan/scope/review/envelope,
handoff/plan.json/full schema validation and before-edit amendments. Planning handoff product
hashes must match all136 preflight entries. Root owns validated CI/merge/closure.
