---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T23:06:00Z
slice_id: ACI-S12
issue: https://github.com/Rynaro/atlas-aci/issues/34
---
# ACI-S12 — bounded directional evidence navigation

## Scope

Implement issue34 at merged base69fe066f573f29651fb53ad0dac65ae13836b752,
/tmp/atlas-aci-aci-s12, branch aci-s12. Exact live criteria digest:
210baed6d4e4969c7e2223559580ec14a0c4ed4e277b062e891b8edc62159395.
Preflight verified marker, clean HEAD, absent S12 artifacts and actual ancestor
S07/S11 COMPLETE receipt bytes (332/256 digests), not just issue closure. Ledger
records PR76 merged,12 merged/30 remaining,current34,next35.

In: typed incoming/outgoing neighborhoods and shortest evidence paths, exact entity
identity/disambiguation, explicit work/depth/deadline/result limits and deterministic
partial results. Out: raw SQL/Cypher, compiler/type inference, dataflow, transitive
semantic guesses, historical graph merging, extra tools, remote stores, new packages,
paid evaluations, deployment and performance claims. Five engineering days, one Vivi
implementation allocation; root owns validated PR/CI/merge/sync.

Issue-declared paths: mcp-server/src/atlas_aci/query/*,
mcp-server/src/atlas_aci/tools/graph_query.py,
mcp-server/src/atlas_aci/enforcement.py, mcp-server/tests/slices/test_s12_*.py.
Necessary narrow amendments recorded before implementation:
- storage/evidence.py and codegraph.py under mcp-server/src/atlas_aci: canonical
  expression indexes, native epoch8 lifecycle and exact old6/7 read compatibility;
  no canonical fact/portable record redesign or extraction/resolution changes.
- protocol/graph.py, protocol/results.py and server.py: S11 operation branches,
  operation-specific output/error validation, central limits and one-lease dispatch.
- mcp-server/tests/test_schema_epoch.py, test_export.py, test_cli_export_import.py,
  test_server.py: directly affected schema/native-versus-portable epoch, old-reader,
  import/export and operation/bounds expectations; preserve their integrity checks.
- mcp-server/tests/fixtures/s12/*: small real parser-produced old/native/portable
  fixtures and golden provenance; atlas/docs/*: request, limits and compatibility.
- .github/workflows/harden-gate.yml and scripts/test-verify-probe-verdict.sh:
  exact fresh S12 artifact selection only, after actual independent probe approval.
- .spectra/plans/aci-s12.*, .spectra/plans/ramza-calibration.jsonl,
  .spectra/changes/aci-s12/*, .spectra/evidence/aci-s12/*,
  .spectra/state/aci-s12.execution.json, .spectra/backlog/execution.json and order.md.
No dependency/config/CLI/other prior-slice-test edits assumed. Amend further observed
needs narrowly before editing; never change frozen old criteria, receipts or probes.

Actual FULL7 (public API/migration/security/novel,15 estimated files,medium stakes),
complexity9(2,2,2,3), standard role routing. P1 does not activate TRANCE. Maker
ramza-backlog-order-s12; independent ATLAS full review required. Prior external memory
rejection is terminal; reuse local verified source and planning artifacts without retry.

## Approach

### Grounded storage and qualified primitive

Canonical assertion records contain directed source/target entity IDs, relation,
resolution and evidence ID; parallel assertions retain distinct evidence identity.
Existing storage/evidence.py DDL has only ID/path indexes with JSON payloads. ATLAS
actually observed SCAN for endpoint/relation/name queries despite LIMIT; exact ID
uses the primary key. confident_edges() fetchall and historical helpers materialize
whole graphs/history. They are not traversal adjacency primitives.

Select native SQLite expression indexes over unchanged canonical payloads:
outgoing(source_entity_id,relation,id), incoming(target_entity_id,relation,id),
entity-name(name,id), with exact json_extract expressions. Writer builds/imports
create them; a readonly request never creates indexes or auxiliary files.
Query one exact endpoint and one relation with ID keyset/order and bounded LIMIT.
Process requested relations in sorted order, then assertion IDs, avoiding an IN/OR
query or merge lookahead that hides sorting or uncharged prefetched edges.

ATLAS's disposable real-parser fixture qualification used actual Python-linked
SQLite3.53.1,73 unchanged canonical records from S11 portable artifact
SHA75253c5c6d4d8c88b626a12ab9373f3f865c9d4eb6f4d16134c0ec61a6fa86e7.
All three exact keyset queries became SEARCH using their expression index, without
SCAN/temp B-tree; LIMIT1 pages matched full ordered rows (out2,in1,name2). A second
mode=ro connection retained SEARCH and rejected writes with SQLITE_READONLY8.
Actual progress-handler interruption returned SQLITE_INTERRUPT9; clearing the hook
restored normal queries. This qualifies a primitive, not product counters/migration.
SQLite is runtime-linked, not pinned by uv; actual Linux/offline Docker compatibility
remains required. Initial missing-new-worktree-venv exit127 is not a product result.

The first implementation checkpoint must prove actual production query plans on
real parsed/indexed data in native and Linux runtimes, indexed keyset iteration,
charged parallel assertions and cleanup after interruption. Missing JSON/index
capability is an explicit stop, not a silent scan fallback. Keep canonical typed
validation: json_extract alone does not validate IDs, bool/int types or record shape.

### Native epoch and portable compatibility

Indexes change sqlite_master signatures and therefore are a real native schema
change. Bump native SCHEMA_EPOCH7→8 with explicit hand-maintained EXPECTED_DDL_HASH
and actual SQLite signature. Preserve exact old7 DDL/signature constants alongside
the existing exact epoch6 adapter; do not validate old7 with new8 hashes.
Read selection prefers8, then validated7, then known6. Existing DSL/status/export
remain readable on valid old generations with their original generation/provenance.
New traversal on6/7 returns INDEX_UPGRADE_REQUIRED with index/import guidance; never
autocreate/migrate on read and never silently run unbounded adjacency fallback.

Writer full/incremental rebuild and source-free import publish a complete indexed8
stage under the existing writer lock. Do not sweep old7 before successful publication.
Failed/partial upgrade preserves the last usable7 generation and its source pack;
an already acquired7 SQLite/pack lease remains coherent through8 publication.
For rebuild, inspect the selected validated predecessor even when its filename is7:
retain S07 historical closure and S08 source provenance, including failed-refresh
history, rather than treating old7 as an absent predecessor. Schema mismatch forces
fresh extraction/rebinding where required; it never disguises old facts as current.
Retire obsolete native files only under the existing safe writer publication policy.

Portable atlas-evidence.v1/v2 records do not contain these indexes. Keep the existing
portable schema_epoch7 and exact record semantics/order/hash; explicitly separate
that exchange constant from native epoch8. Old portable7 and epoch6 inputs remain
strictly validated and import into native8; source-free imports remain source-unavailable.
No source lookup fallback, fabricated producer, changed stable ID or relaxed projection/
observation/history validation. Fresh source builds can have new producer/generation
identities; imported exact evidence retains original identities. Capture a genuine
baseline-produced old7 fixture before editing; do not fabricate old compatibility
solely with the new8 builder. Test normal/failing upgrade and held old source handles.

### Typed operations and identity policy

Extend atlas.graph.v1 with op=neighborhood and op=path, preserving status and all
seven tool names/legacy DSL. Inputs are closed schemas with expected_generation_id
guard retained. A seed is exactly one of {entity_id:64hex} or {name:nonempty_string};
path has a target with the same selector contract. direction is outgoing or incoming;
relations is a nonempty unique subset of the existing canonical relation vocabulary,
default [call]. No arbitrary predicate or raw SQL. Confidence selection permits only
EXTRACTED and INFERRED, default both; expose each assertion's actual resolution.

Exact IDs use primary-key lookup. Name selectors use indexed name order and at most
two candidate records to establish absent/unique/ambiguous; report these lookup counts
separately from graph visited_nodes. More than one returns successful disambiguation,
with candidate IDs/identity projections and candidates_complete=false when enumeration
was intentionally capped. Never pick the first same-name owner/language. Endpoint
ambiguity/missing names cannot trigger traversal. Byte limits may shorten the displayed
candidate prefix, but cannot convert ambiguity into unique identity.

Only active resolved assertions with real target IDs are traversable. AMBIGUOUS,
UNRESOLVED and LEGACY_UNAVAILABLE assertions never fan out candidate IDs. Every fetched
assertion is charged before confidence/target filtering. Historical closed records
remain isolated and are not added to active adjacency; the response declares current
published-evidence scope and does not claim program-level absence of unknown calls.
Source-free valid imports can traverse their validated resolved evidence, while source
availability/spans/provenance remain honestly unavailable where the artifact lacks them.

Use breadth-first search for unweighted shortest paths, with deterministic relation→
assertion-ID adjacency order and first-discovery predecessor. Nodes are unique by exact
entity ID; cycles do not re-enqueue visited nodes. Parallel assertions remain distinct
examined/returned evidence, even when they point to an already visited endpoint.
Outgoing expands source→target; incoming follows target→source but retains each hop's
original assertion source/target and a separate traversal direction, never reverses
the stored relation. Equal-length paths use the stable first-discovery rule; do not
claim a weighted/global shortest path outside the requested relation/confidence domain.

### Explicit work accounting and deadline boundary

Provide documented defaults/hard maxima (policy, not performance acceptance thresholds):
visited_nodes100/1000; examined_edges1000/10000; depth3/32; deadline_ms100/2000.
Node limit is at least1; edge/depth/deadline can be0 for bounded immediate results.
Reject booleans/non-integers/negative/above-max values rather than silently widening.
Request max_bytes defaults to the existing server result cap and cannot exceed it;
existing central element caps still apply. Publish effective limits in the result.

Charge seed before visiting; reserve node allowance before discovering/enqueuing a new
target. Check depth before expansion: depth0 visits only the verified seed and examines
no outgoing edge. Reserve edge allowance before each single-row indexed step. Confirmed
EOF refunds the reservation; an interrupted in-flight step retains its conservative
charge, explicitly documented as charged examined work. Normal counts equal actual
fetched assertions, including rejected-confidence, null-target and parallel evidence.
No extra row is fetched to discover that a cap was exceeded. Exhausting allowance
without proving EOF returns partial, not a false complete/no-path answer.

Each admitted assertion requires only a fixed number of primary-key endpoint/evidence/
occurrence/producer lookups. Track actual lookup/SQL-step diagnostics and check the
deadline before hydration; no full graph/history scan, fetchall, unbounded sorting,
COUNT(*) for result totals or hidden preloaded adjacency. Query SQL uses the exact
qualified index/keyset shape and fails closed if the required index is absent. Tests
observe actual statements/plans and charged fetched rows; counters alone are not proof.
Name resolution is at most two candidates per endpoint plus fixed primary-key checks,
explicitly outside visited_nodes but bounded and reported. No unbounded candidate list.

Start monotonic traversal deadline after the mandatory authenticated graph/source
lease is acquired, before seed lookup. Existing global integrity/source-pack acquisition
is not bounded by traversal counters or deadline; disclose and measure that cost
separately. Do not weaken S08 proof checks or promise a hard total MCP latency bound.
Check the deadline before each lookup/expansion/hydration/response admission, and use
a query-owned SQLite progress handler with a small fixed documented VM-op quantum
to interrupt a running query. Own and clear that hook in finally on the dedicated
lease connection; Python has no public get_progress_handler, so do not claim restoration
of unknown external handlers. Recognize only the actual deadline-triggered SQLITE_INTERRUPT
as partial; corruption/other SQLite failures remain operational errors.

Keep a last fully committed traversal checkpoint. Interruption does not emit a half
hop/path or dangling evidence. Canonical frontier includes stop reason, charged counters,
depth and compact deterministic cursor/queue commitment, not wall-clock duration or
remaining time. Same request/snapshot and same work frontier yields identical order/
payload; different wall-clock frontiers may differ. No resumable remote cursor service.
Controlled cancellation closes SQLite/pack/progress hook; synchronous work is not
claimed preemptible merely because a test injects CancelledError at an await.

### Evidence output, bytes and S11 adapter integration

Return operation-specific closed data schemas inside the existing versioned envelope.
Successful outcomes distinguish complete, partial, disambiguation and no_path. A
no_path result requires exhaustion within all work limits; a depth-limited search is
partial/depth_limit, not proof of no path beyond that depth. Empty/missing exact seed
is successful no evidence. Missing index/native corrupt graph is an operational error,
not the S11 status-only no-index successful status payload.

Nodes are explicitly identity projections. Each returned edge/hop contains assertion
ID, original source/target IDs, relation/resolution and a supporting evidence reference
with evidence/occurrence/producer IDs, original source hash/span/provenance state and
snapshot source availability. Resolve links from the same leased SQLite; no invented
proofs or live source fallback. Do not pretend projected objects hash to canonical IDs.
For a path, return either a whole coherent path with all hop proofs or a partial result
without a falsely successful truncated path. Neighborhood records are admitted whole.

Account the complete actual S11 CallToolResult including duplicated structured/text
representations, escaping, flags and envelope. JSON-RPC framing remains separately
measured/disclosed, as in S11. Derive/document the minimum envelope for every outcome
and supported client representation; below-minimum requests return INVALID_BUDGET
under the measured fixed error cap. Reserve enough space for final counters/partial
metadata before admitting a record; admission uses actual SDK serialization, not a
sum of unescaped object lengths. If one record/path cannot fit, return byte_limit
partial with truthful work counters. No clipped strings, split proof records or
silent RESPONSE_TOO_LARGE replacing a promised bounded partial traversal.

Central status list projection must not independently truncate a traversal path or
discard hop proof links. Register an operation-specific bounder/validator; central
guard verifies the already admitted coherent result and server absolute cap. Preserve
existing status bounds/legacy payloads. Add intended-operation validation to the
S11 adapter: a status-shaped success/error cannot satisfy a path/neighborhood request.
Known valid operations keep their operation in errors; pre-discriminator unknown-op
errors preserve compatible S11 behavior. Legacy error validation stays strict and
cannot accept structured envelopes by falling through a permissive union. Preserve
actual negotiated-version logic, old text-only clients and truthful MCP isError.

### Fresh producer evidence and first execution gates

Any producer change requires genuine new same-pin/default-profile probe evidence,
even if projected facts are unchanged. Native epoch8 necessarily changes the verifier
fingerprint; do not reuse0605 or resign old measurements. Preserve exact Solidus
4026945d614e81383c007ed1ab1278a0195ce5d9 and Spree6699cde44303ea85ef6e56c5e87c44a738ab73fc,
all default languages/full inventories, frozen bar/seeds/resolution and unchanged
verifier/resolver. Record final source/runtime/producer hashes, failed attempts and
actual current export/cache/source-store/lease costs without a new threshold.

Use .spectra/changes/aci-s12/probe/aci-s12-probe-lpa-vs-louvain.{json,md} and colocated
probe-graphs.json.gz; raw measurements under .spectra/evidence/aci-s12/probe/. The
existing600-second per-job/2700-second cumulative budget includes failed attempts;
track actual charges, no reset on retry. Only after independent ATLAS artifact audit
and unchanged verifier PASS may the two exact selector files point to the new basename.
No classifier/CI algorithm or mutation-fixture weakening. Preserve compact lossless
raw archives; clean only explicitly scoped disposable DB/pack/full-export copies
after independent measurement audit, never source seeds/qualified SDK/history.

## Stories

Theme: useful bounded ACI navigation. Project: v3 evidence retrieval. Feature:
typed neighborhoods and shortest paths. Executor Vivi, standard tier, one writer.

1. As an operator I gain indexed traversal without losing old readable graphs.
   Timebox1.5 days, P0: capture genuine baseline7 fixture; implement exact indexes,
   epoch8/old6+7 adapters and writer/import publication; prove query plans, failed
   upgrade preservation and old held graph/source coherence. Output: strict schema/
   portable tests and real primitive traces before traversal breadth.
2. As a client I navigate exact directed evidence within explicit work limits.
   Timebox1.5 days, P0: bounded seed lookup, typed BFS, budgets/progress hook/frontier,
   whole proof output and byte admission. Output: all nine named product criteria
   on real parser/SQLite fixtures, with observed work counters and negative controls.
3. As a client/operator I retain validated protocol and reproducible execution.
   Timebox2 days, P0: S11 adapter/central-bound integration, old/new SDK stdio, RO
   Linux/sourcepack lifetime, full/static/portable gates, fresh probes, docs and
   exact receipt/reentry. Report remaining gaps at five days; do not add operations.

## Acceptance Criteria

Exact adjacent aci-s12.criteria.md contains nine product plus two RUN criteria;
preserve its bytes. Named tests in tests/slices/test_s12_contract.py:
- test_direction_filter (AC001): real A→B,C→A plus inverse relation/direction controls;
  outgoing call target onlyB, incoming onlyC, distinct relation filters not conflated.
- test_path_evidence (AC002): shortest/equal-length/cyclic real paths with every hop's
  exact canonical assertion/evidence/occurrence/producer/hash/span links; reverse
  traversal retains original assertion direction. Parallel evidence is not collapsed.
- test_visit_budget (AC003): high-degree real-parser hub, counters/SQL trace prove
  reserve-before-discovery; no preloaded whole graph or posthoc clipped counters.
- test_deadline (AC004): actual SQLite plus real monotonic expiration at a controlled
  expansion barrier and real SQL progress interruption; no further expansion, coherent
  partial result, cleanup/usable connection. Injected clocks may supplement deterministic
  frontier tests, but cannot be the sole deadline integration evidence.
- test_ambiguous_seed (AC005): same name in distinct owners/languages, name disambiguates
  with bounded real indexed lookup; exact ID selects the correct entity; target too.
- test_traversal_replay (AC006): repeated complete and fixed-work-frontier results
  byte-identical on same snapshot; separate wall-clock frontier differences disclosed.
- test_examined_edge_budget (AC007): dense actual distinct call occurrences between
  the same entities; every fetched/rejected/parallel assertion charged, no cap overrun.
- test_serialized_byte_budget (AC008): actual SDK result UTF-8 bytes, old/new clients,
  escaping/non-ASCII, minimum and below-minimum errors, whole paths and metadata;
  valid budget never exceeded, no split record or false successful path prefix.
- test_depth_budget (AC009): deeper real graph, depth0 and exact boundary, no expansion
  beyond limit; partial depth stop distinct from proven no-path.
- RUN01/02: exact authoritative receipt/output digest and interrupted identity below.

Additional required controls: no path/absent seed; confidence exclusions/null-target
no fanout; historical failed-refresh closure not traversed as current; source-free
imports and exact old6/7 compatibility; same-source full/incremental canonical facts;
invalid schema/op and operation-mismatched generated output; actual S11 legacy fixture
parity; same lease through G2 publication; error/controlled cancellation cleanup;
noindex/corrupt native distinctions; readonly Docker/source namespace and no writes.
Freeze baseline source/portable fixture provenance before edits. Full raw proof fields
remain independently checked; no names-only or hand-fabricated graph substitute for
the parser→index→query acceptance fixtures. SQL plans/counters need actual traces.

Verification uses the existing locked environment (no new dependencies):
```sh
UV_CACHE_DIR=/tmp/aci-s01-uv-cache /tmp/aci-s01-tools/bin/uv sync --frozen --project mcp-server
cd mcp-server
.venv/bin/python -m pytest tests/slices/test_s12_contract.py -q
.venv/bin/python -m pytest tests/test_schema_epoch.py tests/test_export.py tests/test_cli_export_import.py tests/test_server.py tests/slices/test_s11_contract.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/mypy src/
```
Also run exact current CI scripts lint/type checks and probe/export guard self-tests;
native Docker uses existing UID10001/private graph-root/source:ro/network-none pattern.
All required integration executes without skips. Record commands/exits/runtime/versions.
No compiler execution or paid model evaluation is necessary for structural traversal.

Full-tier consistency: request-flow (validate→lease→seed→indexed expansion→proof→
admission→SDK) maps to all nine criteria; storage-flow (old/new generation→indexes→
active assertions→linked evidence→portable compatibility) covers the same seams;
failure-flow (ambiguity/limits/deadline/upgrade/corruption/cancel) covers their negative
controls. Independent reviewer must challenge hidden SQL scans, duplicate lookahead,
counter definitions, shortest-path/depth claims, partial proof coherence, source/history
upgrade retention, native/portable epoch separation and S11 operation/error fallthrough.

## Confidence

Compute actual Assemble confidence after independent review. SQL primitive qualification
does not certify integrated migration, counters, deadline or SDK bytes. Honor the real
verdict; never infer AUTO_PROCEED from Explore score or fabricate product/human approval.

## Rejected Alternatives

Budgeted ordered global scanning scored70: can conservatively charge every row but
wastes the caller's budget on unrelated graph data and does not provide useful indexed
neighborhoods. External generation-bound adjacency store scored72: fast lookup but
adds a second publication/lease/validation lifecycle with avoidable five-day risk.
Canonical expression indexes plus exact epoch adapter scored85.5 and is selected:
uses SQLite atomic publication and unchanged fact records; actual qualified indexed
queries support bounded expansion, at the explicit cost of native compatibility work.

## Risks

P0 hidden SQL scans/sorts: exact qualified index expressions, real EXPLAIN and row-step
traces, no whole-graph helper. P0 epoch upgrade destroys old history/source: preserve
validated predecessor and held leases, stage/publish once, fail before exposure.
P0 bytes destroy path semantics: whole proof admission and actual SDK bytes. P1 runtime
SQLite JSON/index differences: real Linux/readonly tests, no silent fallback. P1 deadline
scope misunderstood: acquisition is separate; fixed VM quantum is not hard realtime.
P1 five-day breadth: no new semantic provider, general query language or cursor service.

### Execution identity and handoff

One .spectra/state/aci-s12.execution.json for ACI-S12 plus frozen criteria; adapt the
existing S11 verify_execution.py/verify_resume.py protocol into new S12 evidence paths.
Initialize only if absent, otherwise verify identity and resume missing work. Receipt
records actual base/output commit, declared paths, per-AC commands/exits/evidence and
tracked implementation digests. No COMPLETE without all11 actual acceptance results.
Root performs independent product/evidence and green CI checks before merge/closure.

Exclude exactly these self-referential/post-output metadata paths from output digests:
.spectra/state/aci-s12.execution.json;
.spectra/evidence/aci-s12/final-reentry-before.json;
.spectra/evidence/aci-s12/final-reentry-after.json;
.spectra/evidence/aci-s12/final-reentry-summary.json;
.spectra/evidence/aci-s12/final-resume-identity.json;
.spectra/evidence/aci-s12/output-check-tests.log.
All tracked product/tests/docs/frozen plan/stable pre-output evidence stay hashed.
Record a concrete local implementation commit, then metadata-only receipt/proof commit.
Clean reentry returns ALREADY_SATISFIED, exact before/after digest equality and git diff
--exit-code versus the recorded output for implementation paths. Real interrupted
resume preserves slice_id/criteria_sha256 and the single authoritative receipt.

Root mechanical ECL verify-envelope --block plus correlated message/thread/spec
verify_pass precedes Vivi. Required planning artifacts: exact criteria, state, plan,
scope/review, envelope-relative paths, handoff/plan.json/full validation and amendments.
All planning writes remain .spectra-only; compare all119 preflight product hashes before
READY. Supporting primitive evidence retains limits/failures rather than claiming
production readiness. No deployment or paid spend is part of the handoff.
