---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T21:30:00Z
slice_id: ACI-S11
issue: https://github.com/Rynaro/atlas-aci/issues/33
---
# ACI-S11 — versioned graph results with legacy compatibility

## Scope

Implement issue33 at base138a6f3cd510e0ecb3be0832221236adfe441ebc in
/tmp/atlas-aci-aci-s11, branch aci-s11. Live marker ACI-S11 and exact frozen-input
criteria SHA6ac4f0b0574b1c9d150d35d580f33b521f89a4c9e13e9044cf9e7eff4c294599
were verified before state initialization. S03/S04/S07 ancestor receipts and actual
interfaces exist; S08 interoperability is also verified. Preflight records hashes.
Ledger records PR75 merged,11 merged/31 remaining,current33,next34.

In: explicit graph_query input discriminator, status as the first structured
operation, published schemas, truthful MCP errors, compatible legacy JSON text,
central nested bounds, deterministic snapshot response, and real SDK integration.
Out: new tool names, all future graph operations, raw SQL, protocol lifecycle
replacement, graph semantics/storage formats, new packages/versions, services,
paid evaluation, deployment, automatic coder merge, or performance thresholds.
Three engineering days total; root owns validated publication/merge/sync.

Declared issue paths: mcp-server/src/atlas_aci/server.py,
mcp-server/src/atlas_aci/tools/graph_query.py,
mcp-server/src/atlas_aci/protocol/*,
mcp-server/tests/slices/test_s11_*.py, atlas/docs/*.
Narrow amendments recorded before implementation:
- mcp-server/src/atlas_aci/codegraph.py: published-status assembly from an already
  validated lease; retain CLI operational status and all graph/source validation.
- mcp-server/tests/test_server.py: directly affected schemas, bounds, SDK adapter
  and truthful result expectations; preserve existing navigation/execution guards.
- mcp-server/tests/fixtures/s11/*: small real source and frozen portable evidence,
  baseline legacy outputs and provenance, not a large production corpus.
- .spectra/plans/aci-s11.*, .spectra/plans/ramza-calibration.jsonl,
  .spectra/changes/aci-s11/*, .spectra/evidence/aci-s11/*,
  .spectra/state/aci-s11.execution.json, .spectra/backlog/execution.json and order.md.
No config/enforcement/dependency-file edits are assumed. If an observed dependency
requires another path, record a narrow amendment before editing; do not weaken an
old test, criteria, receipt, probe, or portable integrity rule to fit this plan.

Actual FULL6, complexity9(2,2,2,3), standard role routing. Maker is
ramza-backlog-order-s11; independent ATLAS checker required. Known scope needs no
new clarification. Prior external memory rejection is terminal; local source and
prior plans are the reusable pattern, with no retry or external persistence.

## Approach

### Grounded seams and first checkpoint

Current graph_query accepts only query:string. Server dispatch acquires one real
graph/source lease; run_stdio returns text and catches exceptions into JSON text,
leaving MCP isError false. Central bounds currently measure one payload, not both
text and structured copies. CodeGraph.status() opens another lease and reads the
latest failed-attempt sidecar; wrapping it under a pinned reader can mix G1 and G2.
S04 deliberately changes operational status_id after a failed refresh. Therefore
fixed snapshot replay needs an explicit published-state domain, not ignored fields.

The actual locked SDK is mcp1.27.0, with Pydantic2.13.0/jsonschema4.26.0 already
resolved. Direct CallToolResult returns bypass SDK outputSchema validation; normal
SDK input validation returns errors before the application handler. Use the public
call_tool(validate_input=False) decorator option and explicitly validate every
published input inside the bounded adapter. This disables only duplicated SDK
validation, not validation itself. Validate our output before returning a direct
CallToolResult, allowing isError to remain truthful. No SDK lifecycle patch.

First checkpoint before broad edits: preserve actual baseline legacy outputs on a
small real indexed/imported fixture, and exercise actual locked SDK initialization,
tool discovery and CallToolResult behavior in a subprocess. Capture schema/input
error bypass as baseline evidence. Stop for a concrete compatibility amendment if
the public adapter cannot implement the stated contract; no mocked SDK assurance.

Official protocol references for schema/structured text/error semantics:
https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-06-18/server/tools.mdx
https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-06-18/schema.mdx
The implementation authority remains the verified locked SDK plus native tests.

### Explicit request modes and narrow protocol modules

Keep all seven current names exactly: view_file,list_dir,search_text,search_symbol,
graph_query,test_dry_run,memex_read. Extend only graph_query's operation contract.
Legacy mode retains query:string and its decoded result shape; preserve historically
accepted unrelated arguments unless they are reserved structured discriminator keys.
Structured mode is {schema_version:"atlas.graph.v1",op:"status"}, optionally
expected_generation_id as a 64-character lowercase hex current-generation guard.
It is not historical graph retrieval. Reject mixed query+structured tags, missing
discriminators, unknown versions/operations, invalid types and structured extra
properties before graph acquisition. A mismatched expected ID returns an explicit
SNAPSHOT_MISMATCH error; never silently serve another generation.

Use a small protocol package (graph schema/envelope and SDK result/budget adapter),
not a registry framework. Existing locked jsonschema validates schemas and instances;
existing SDK/Pydantic constructs wire models. No package, lockfile or version change.
Publish closed structured status/error schemas, with an explicit legacy-object
alternative excluding reserved structured tags. Validate the expected request mode
separately, so malformed structured output cannot fall through a permissive legacy
branch by omitting its schema tag. Invalid generated output becomes a fixed bounded
internal-result error, not unvalidated JSON or recursive schema failure.

For negotiated SDK-supported2025-06-18/2025-11-25 sessions, advertise graph outputSchema
and return structuredContent plus a JSON TextContent of the same payload. For older
supported2024-11-05/2025-03-26 sessions, omit outputSchema/structuredContent and retain
the same JSON text representation. Determine version from the actual public request
context/session initialization semantics, including unsupported-version negotiation;
test both branches through stdio. Never infer support from arbitrary client names.

### Canonical published status and source provenance

Structured status is the last published generation's evidence, explicitly not live
source freshness or the latest failed refresh. Assemble it from the single acquired
reader and its validated generation/source context; do not invoke reader.status(),
open another graph, reread the latest pathname, or repeat global validation inside
the lease. Keep CLI status() behavior unchanged, including latest-attempt/runtime
diagnostics. Share a small pure assembly helper only if it preserves both domains.

Freeze a versioned envelope with schema_version,operation,status,generation_id,
snapshot_id,evidence_status,data,error,bounds. status is ok or error; exactly one
of data/error is populated. Successful status data declares:
- coverage_basis=published_generation; live_source_verification=not_performed;
  latest_attempt_included=false and runtime_capabilities_included=false.
- Stored indexed configuration, producer, capabilities and coverage/completeness,
  including failed observations, stale retained facts and honest unknown reasons.
- Source availability and bounded source references authorized by the pinned source
  manifest: path and source_sha256 under the envelope snapshot, with observed versus
  historical indexed provenance/stale flags distinguished. Do not relabel old bytes.
- Full published counts remain full counts; bounded lists separately report returned
  counts/truncation. Missing old/import metadata is unknown/unavailable, never zero
  completeness or declarations inferred from the current runtime.

Use the existing generation/snapshot and stable entity/evidence identifiers where
present; never synthesize row IDs or live source references. No current time, random
request identifier, duration, latest-attempt sidecar or current-runtime declarations
belongs in canonical payload. An unchanged request on G1 must serialize identically
after live source edits, failed refresh diagnostics or runtime-only changes. A real
barrier test holds G1 while G2 publishes, verifies all status/source references remain
G1, then verifies a new acquisition sees G2. A guard targeting G1 rejects G2.

No-index status is a valid unavailable answer only when absence is actually diagnosed
without side effects. Native corrupt metadata, missing required source pack, permission
or operational read failures remain errors. Do not classify by matching error prose,
or hide corrupt indexes as no evidence. Source-free valid imports and supported legacy
generations are successful unknown-source states, not corrupt native exceptions.
All status/no-index paths remain read-only: no directories, locks, migrations or writes.

### Truthful errors and complete result bounds

Supported-operation exceptions produce isError:true. Keep known legacy INVALID_QUERY
and UNKNOWN_VERB payloads compatible while marking their MCP result erroneous. Valid
empty DSL queries remain isError:false; successful status unknown/unavailable is also
false. Do not flag arbitrary strings or a successful test_dry_run process result merely
because its exit code is nonzero. Unexpected exceptions use a controlled bounded public
message; internal diagnostics stay outside the result. Unknown/mixed/schema-invalid
inputs have fixed explicit error codes without echoing unbounded supplied values.

Preserve central guards, list caps, pagination and legacy count/overflow behavior.
Add explicit schema-aware bounds for every variable status list, including nested
files, source references, capability languages/relations/kinds and configuration arrays.
Truncate whole elements deterministically; do not fabricate completeness from the
remaining prefix. No unbounded nested status arrays may evade the registry.

The final byte gate measures actual SDK-serialized CallToolResult bytes, including
structuredContent, escaped compatibility text, isError and emitted model fields.
Use the SDK serialization settings actually sent on stdio; UTF-8 bytes, not Python
character counts or the size of only one payload. Compact JSON text is compatible
with normalized legacy payloads. JSON-RPC transport framing/request IDs are separately
recorded in native tests and explicitly outside the tool-result budget; never claim
that the configured tool-result cap bounds arbitrary transport identifiers.

All input/dispatch/output-validation errors use the same final budget path. Establish
the minimum representable valid error envelope from actual fixed variants. A configured
budget below that minimum returns a fixed INVALID_BUDGET result under a documented
fixed error-envelope cap, as the issue permits; no recursion or silent cap increase.
Measure both tool-result and full stdout frame in tests. Exercise non-ASCII, quotes,
backslashes, oversized exceptions, duplicated representations and smallest caps.

### Compatibility oracle and immutable producer evidence

Before edits, construct a small actual parser/index fixture covering all six DSL
verbs, empty, ambiguous, malformed, unknown, current and historical evidence. Export
its portable evidence and record its exact SHA/provenance. Import/query that SAME
frozen artifact under baseline and new producer, preserving its real stable IDs,
evidence links, spans, directions, confidence and reasons. Compare normalized decoded
payloads with an explicit small allowlist limited to volatile environment/generation
anchoring fields proved to differ. Never normalize away canonical evidence IDs,
relationships, errors, counts or ordering. Keep raw before/after outputs and the
normalization manifest. Fresh real builds separately verify native source status;
the source-free portable oracle cannot claim snapshot source availability.

Changing codegraph.py changes the existing whole-file producer fingerprint, even
when graph facts do not change. Preserve exact historical/current producer validators,
S09 packets, S10 witnesses, S15 capv3 and S08 strict source adapters. Mechanically
check behavioral fingerprint0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f.
No DDL, portable record or fact-semantic change is intended. If fingerprint changes,
diagnose it and follow existing genuine fresh-probe procedure; never resign old
measurements or treat a producer hash change as behavioral equivalence proof.

## Stories

Theme: reliable ACI navigation. Project: v3 graph contract. Feature: structured
status beside the unchanged DSL. Executor Vivi, standard tier, one writer.

1. As an existing client I retain graph answers while protocol failures become
   truthful. Timebox1 engineering day, P0: capture baseline/portable fixture and
   native SDK behavior; implement discriminator, schemas, adapter and errors; prove
   all legacy fixtures and invalid-input-before-execution controls. Output: exact
   fixtures, code and raw native logs, no claimed product pass before tests.
2. As a client I can inspect the same published graph/source snapshot repeatedly.
   Timebox1 day, P0: implement one-lease published status and bounded source references;
   preserve CLI diagnostics, imported/legacy unknowns and genuine corruption errors;
   test real G1/G2 barriers and replay after mutable diagnostics/source changes.
3. As an operator I receive bounded interoperable responses and resumable evidence.
   Timebox1 day, P0: complete nested/serialized budgets, old/new stdio versions,
   readonly Docker/lifetime controls, docs, full regressions and RUN gates. Record
   actual costs and any remaining gap at the three-day cap; do not expand operations.

## Acceptance Criteria

The adjacent aci-s11.criteria.md is the exact eight-criterion source; do not amend it.
Implementation mappings:
- AC001 test_legacy_compatibility: all six real DSL verbs plus error/empty/ambiguous/
  current/history branches against one pinned portable evidence artifact; strict
  decoded parity, separately asserted intentional MCP isError differences.
- AC002 test_structured_status: real parsed/indexed graph, published JSON Schema,
  both representations equal, stable IDs/source refs and partial/unknown states.
- AC003 test_invalid_operation: unknown op/version, mixed tags and malformed types,
  explicit bounded failure and observed zero graph acquisitions/executions.
- AC004 test_tool_error_flag: real execution failure and native missing/corrupt source
  state; malformed generated output and actual SDK error transport controls.
- AC005 test_no_evidence_success: valid no-match DSL successful empty, no fabricated
  fact; no-index/valid imported unknown status distinguished from corrupt index.
- AC006 test_structured_replay: same request/G1 byte-identical canonical payload after
  failed-attempt/runtime/live-source changes; real G1/G2 barrier and expected-ID guard.
- RUN01/RUN02: authoritative receipt and exact output-commit digest/re-entry below.

Additional nonvacuous tests cover every advertised schema, old/new SDK sessions,
all seven tool names, source-only/legacy interoperability, nested bounds, outputSchema
validation bypass defense, and invalid-output failure rather than permissive union
acceptance. Count one SQLite+source lease for each graph request and close on normal,
exception and controlled CancelledError at an actual await. Do not claim synchronous
operations are preemptible or equate controlled cancellation with live MCP cancellation.
Preserve existing S08 descriptor/RO/prune tests and S04 operational status tests.

Verification commands (record exact environment, exit, elapsed time and scope):
```sh
UV_CACHE_DIR=/tmp/aci-s01-uv-cache /tmp/aci-s01-tools/bin/uv sync --frozen --project mcp-server
cd mcp-server
.venv/bin/python -m pytest tests/slices/test_s11_contract.py -q
.venv/bin/python -m pytest tests/test_server.py tests/slices/test_s04_contract.py tests/slices/test_s08_contract.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/mypy src
```
Use actual existing filenames/CI commands if a representative path above differs,
recording the exact substitution; no required skips. Run scripts/test-verify-probe-verdict.sh
and current CI export/probe guards from repository root with the qualified interpreter.
Native integration uses locked mcp client/server subprocesses, genuine initialized
stdio, real source/index fixtures and existing offline Docker image/UID/mount envelope.
Inspect .github/workflows/{ci,harden-gate}.yml for exact current commands before final
verification; do not invent an equivalent green claim. No paid model run is required.

Full-tier consistency review: request-flow decomposition (classify→lease→assemble→
bound→validate→serialize), data-flow decomposition (legacy frozen facts / pinned
published status / error envelope), and failure/lifecycle decomposition all cover
the same eight criteria and the same adapter/status/bounds seams. No hidden extra
operation or independent writer is introduced. Independent checker must challenge
SDK bypasses, status mixed-generation risk, error/no-evidence distinction, permissive
schema fallthrough, wire escaping, legacy oracle normalization and lease release.

## Confidence

Compute actual confidence in Assemble after independent review; no verdict is
assumed from selected Explore score. Known public SDK semantics and existing lease
contracts support bounded execution; native adapter/schema/budget behavior remains
an implementation obligation. Preserve original dimensions and honor the actual
AUTO_PROCEED/VALIDATE/COLLABORATE/ESCALATE result, with no fabricated human review.

## Rejected Alternatives

SDK dict normalization plus latest CLI status scored72.5: simple, but cannot express
truthful SDK isError through that path and can mix generations/replay diagnostics.
Explicit validated adapter plus published status scored85 and is selected: bounded
public SDK seam, one snapshot domain, explicit compatibility/budget responsibilities.
Canonical plus live diagnostics in every structured response scored70.5: useful
operational context but creates replay ambiguity and expands schema/bounds beyond
status-first needs. CLI status already preserves those diagnostics.

## Risks

P0 SDK input and direct-result output bypasses: explicitly validate both inside the
bounded adapter and test actual invalid cases. P0 latest status contamination: pinned
published helper only, no nested lease, real barrier/replay. P0 duplicate wire size:
actual SDK bytes and escaping, fixed undersized-budget handling. P1 producer-derived
identity churn can invalidate a weak golden: query the exact portable oracle and
keep precise normalization evidence. P1 three-day breadth: prioritize six product
criteria and existing invariants, no future operation/framework. Measured costs are
reported without invented limits; avoid reintroducing S08 repeated global proof work.

### Execution identity, receipt and handoff gate

One authoritative .spectra/state/aci-s11.execution.json for ACI-S11 plus frozen
criteria digest. Adapt the existing .spectra/evidence/aci-s08/verify_execution.py
receipt verifier and receipt schema; inspect its actual CLI before invocation.
Resume an existing partial receipt, never overwrite or create a second framework.
All product criteria need actual evidence before COMPLETE. Make a concrete bounded
local output commit; record its full SHA, tracked implementation digest and commands.
No root merge/issue closure until independent product/evidence checks and real CI.

Exclude exactly these self-referential/post-output metadata files from RUN01 digest:
.spectra/state/aci-s11.execution.json;
.spectra/evidence/aci-s11/final-reentry-before.json;
.spectra/evidence/aci-s11/final-reentry-after.json;
.spectra/evidence/aci-s11/final-reentry-summary.json;
.spectra/evidence/aci-s11/final-resume-identity.json;
.spectra/evidence/aci-s11/output-check-tests.log.
Hash all declared tracked product/tests/docs, frozen plan and stable pre-output
evidence. Clean-worktree reentry must return ALREADY_SATISFIED, exact before/after
manifest equality and git diff --exit-code for implementation paths. Record identical
slice_id/criteria_sha256 across real partial receipt resume. No circular receipt hash.

Root verifies the full-schema ECL envelope mechanically with --block and correlates
verify_pass to message/thread/spec digest before Vivi starts. Final plan/criteria
paths are envelope-relative. Required artifacts: plan,state,criteria,scope,review,
envelope,handoff,plan.json,validation and scope/bookkeeping amendments. All planning
writes are .spectra-only; product source hashes must remain preflight-identical.
