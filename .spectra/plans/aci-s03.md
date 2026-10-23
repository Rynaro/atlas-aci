---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-23T23:36:43Z
slice_id: ACI-S03
issue_url: https://github.com/Rynaro/atlas-aci/issues/26
---

# ACI-S03 — Request reader leases and atomic generation publication

## Scope

Intent CHANGE: a graph-backed request acquires one explicit generation and keeps
it until release; later requests see the newly published generation. Failed
same-epoch builds preserve the last completed generation.
No source-resource CAS, whole-worktree point-in-time snapshot, retention service,
multi-repository policy, WAL introduction, or new graph semantics.

Base `f9e72ed8e3769cec8b7731ec9feb4e945bb8ccb3`, clean main after PR #67.
Worktree `/tmp/atlas-aci-aci-s03`, branch `aci-s03`.
Live marker/digest verified; no predecessor dependencies or prior S03 state.
Previous slice plans, receipts, and S02 probe measurements remain immutable.

Declared product paths:
- `mcp-server/src/atlas_aci/codegraph.py`
- `mcp-server/src/atlas_aci/server.py`
- `mcp-server/tests/slices/test_s03_*.py`

### Scope amendment S03-PATH-01

Permit only observed compatibility updates in:
- `mcp-server/tests/test_schema_epoch.py`: preserve real read-only/zero-write,
  stale/corrupt-epoch dispatcher, and concurrent-writer coverage while adapting
  reader acquisition and generation metadata fixtures.
- `mcp-server/tests/test_codegraph.py`: preserve incremental skip/update/delete/
  replay semantics while requiring staging and consumed-content hashes.
- `mcp-server/tests/test_export.py` and
  `mcp-server/tests/test_cli_export_import.py`: preserve deterministic portable
  facts, validation-before-publication, and source-free cold import under leases.

Do not change tests merely to suppress failures. No other product, fixture, CLI,
tool module, workflow, verifier, or archived evidence path is preauthorized.
All planning/verification outputs are S03-named under `.spectra/`.
Manifest key/value additions avoid a DDL/epoch change and leave portable graph
facts unchanged. If that proves impossible, amend scope and gates explicitly.

Rightsize: seven files, public lifecycle behavior, persisted protocol upgrade
(rebuild required for legacy missing metadata), high stakes -> full score 5.
The migration flag denotes that protocol upgrade, not a planned DDL migration.
Complexity 9/12 extended (scope2, ambiguity2, dependencies2, risk3).
Standard routing remains in effect; full planning requires independent critique.
DISCOVER/CLARIFY omitted because the contract and required boundary are explicit.

## Approach

### Current failure and affected publishers

ATLAS verified cached lazy `CodeGraph._db` at 791-821 while `epoch_ok`
823-841 opens a different current-path connection. Server 411-457 retains one
graph, and dispatch 370-408 passes it into graph tools. Tool epoch checks can
therefore validate G2 while queries still read cached G1.
Full build 922-938 closes a staging DB then replaces the pathname; incremental
908-920 mutates the live inode through _run_build. Build uses .index.lock;
import stages/replaces without that shared lock. There is no request lease or
finally-close boundary. These are all supported publishers to coordinate.

### Selected lifecycle

1. Add and run the real cached production-reader counterexample before changing
   code: build G1, query through server state/dispatch, publish changed G2 at
   the same epoch, query again through the same server state. Capture old G1
   behavior despite current-path validation and the failing assertion.
2. Introduce an explicit CodeGraph reader-acquisition context with close/release.
   Each graph-backed request opens its own SQLite connection using mode=ro,
   validates epoch and generation metadata on THAT connection, and uses that
   same connection for every query until finally-close. A lease exposes its
   generation ID; it never reopens to a pathname during the request.
   New acquisitions resolve the current DB pathname afresh. Generation identity,
   not file inode/mtime or epoch, describes freshness. Open and validate before
   dispatching query work; corrupt/incomplete metadata fails closed.
3. Wrap search_symbol and graph_query dispatch in exactly one lease context.
   Non-graph tools need no graph connection. Pass the leased graph through the
   existing tool functions so their epoch_ok validates the pinned connection,
   not a second pathname. Preserve query bounds/error shapes and existing
   INDEX_UNAVAILABLE recovery behavior. Include generation_id as bounded
   response metadata for these graph-backed calls, derived from the lease.
   Any addition must go through the existing central response-byte check.
4. Use one shared writer-lock discipline for full build, incremental build,
   and import. Under that lock, build a separate staging DB. For incremental,
   use SQLite backup into the staging connection from a valid published base;
   mutate only staging. Never update a published inode in place.
   Commit facts plus complete generation metadata in staging, close writer
   handles, then os.replace the published pathname atomically. Metadata and
   data travel in one DB file, so there is no separately raced pointer file.
5. All error paths close staging connections, remove only this operation's
   staging artifacts, release writer lock, and leave current published DB intact.
   Never unlink/close an active reader's connection to refresh another request.
   Explicitly close owned stale writer/convenience handles before replacement
   rather than assigning _db=None and leaking descriptors.
   Reuse the existing incompatible-epoch policy: this slice does not promise
   fallback to an old schema after _sweep_stale_epoch_files.
6. Import keeps current validation and source-free cold-start semantics. Validate
   the exact artifact consumed, stage it under the same writer coordination, add
   explicit import generation provenance, and replace only after completion.
   No source repository checkout is required to import. A subsequent index of
   imported data lacks source extraction provenance and must do a full rebuild.
7. Add idempotent close and server-finally cleanup for owned handles. SQLite
   connections are created/used/closed in the same owning thread; do not disable
   check_same_thread, share connections across tasks, or add a thread pool/WAL
   to solve this bounded lifecycle.

### Canonical generation identity and inheritance

Store a versioned canonical input object plus its SHA-256 generation_id in the
existing manifest table. Validate their consistency on the opened reader.
Exclude timestamps, mtime, inode, SQLite file bytes, row IDs, PID, staging name,
publication attempt, build mode, output location, and durations.

Build provenance includes:
- resolved repository/worktree location (distinct worktree roots remain distinct);
- schema epoch and expected DDL hash;
- sorted enabled languages and all configuration affecting extracted facts;
- explicit producer fingerprints covering the extraction/resolution code,
  query definitions and parser/language-pack versions;
- a sorted relative-path -> hash map of the exact source byte buffers consumed.

Reading a source file once supplies BOTH its content hash and parser input.
Read/hash every eligible file on each build before deciding to reuse facts.
Incremental reuse requires unchanged content hash AND compatible schema,
language/configuration and producer fingerprints. This catches same-size,
same-mtime content replacement; mtime/size alone cannot justify inheritance.
Unchanged files may skip parsing while updating diagnostic file metadata.
Carry the previous hash only when its existing facts have verified compatible
provenance; remove deleted-file facts/hash together. No later source rescan
may replace the hash of bytes that actually produced the facts.

Missing/invalid provenance in a legacy or imported DB, or changed relevant
configuration/producer, forces a fresh full index instead of inheriting unknown
facts. Eligible-file read/parse/extraction failure aborts this staged publication
rather than marking incomplete data as a completed generation. Unsupported
languages still follow existing explicit skip behavior; syntax trees that the
parser normally accepts retain existing extraction semantics.

For identical canonical build inputs, full and incremental/retry modes reuse the
same generation ID even if a fresh DB file is materialized. Reuse of physical
DB bytes is not required. Source files are read sequentially: the ID describes
the consumed content set, not an atomic filesystem snapshot (S08 is deferred).

Import provenance is distinct: kind=import, destination repository/worktree,
validated portable artifact content identity and schema, and importer fingerprint.
It records source-content provenance as unavailable rather than guessing it.
Identical artifact/destination/importer retries reuse the ID. Import IDs need
not equal original build IDs because their provenance differs. Manifest metadata
stays outside portable export fields, preserving canonical source-free exchange.

Legacy same-epoch DBs without valid generation metadata are not silently assigned
a fabricated build identity. Graph request acquisition returns INDEX_UNAVAILABLE
with reindex/reimport guidance, without writing metadata. Existing valid leases
retain their already validated ID until release.

### Cancellation and concurrency semantics

Graph queries currently execute synchronously inside async handlers. This slice
does not promise preemptive cancellation of SQLite or responsiveness during that
synchronous work. Guarantee finally-close when request cleanup executes, including
CancelledError at an actual await boundary and normal/error exits.
A deterministic test may instrument an async tool wrapper to await a barrier
inside the REAL dispatch lease after a real DB read, then cancel the task and
prove the actual connection is closed. The wrapper controls scheduling only:
no fake database result, fake release counter, or sleep-based timing proof.
Report this as task-cancellation lifecycle evidence; do not call it live MCP
cancellation unless a real MCP cancellation test was separately performed.

A leased G1 request can perform reads, pause at a barrier, allow another process
to publish G2, and then read G1 again. A new request after the publication barrier
must see G2. Writer tests use real SQLite and independent processes/threads with
connections owned by their own execution thread; synchronization controls ordering.

## Stories

As an Atlas operator, I want requests to see one coherent published generation,
so rebuilding the index does not serve stale/new data inconsistently.
Three engineering days total, P0 risk, one standard Vivi executor. Prioritize
all six product criteria and real failure/concurrency evidence; no optional
retention architecture, paid model runs, or additional implementation agents.

## Acceptance Criteria

Canonical content lines between marker lines, including final LF but excluding
the opening marker terminator, are in `.spectra/plans/aci-s03.criteria.md`.
SHA-256 `19aa50b06e7bd201094eed9132c7e4e391459a35f917bd89734a142e6888cbbc`.

### AC-S03-001 (event-driven)
GIVEN a completed replacement is published
WHEN a new request acquires a reader
THEN the request SHALL read the published generation.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_new_request_new_generation

### AC-S03-002 (state-driven)
GIVEN a request holds generation G1 while G2 is published
THEN all database reads in that request SHALL use G1.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_active_request_pinned

### AC-S03-003 (unwanted-behavior)
GIVEN a build fails before publication
WHEN a request acquires a reader
THEN the request SHALL acquire the last completed generation.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_failed_publish

### AC-S03-004 (event-driven)
GIVEN two generations have the same schema epoch
WHEN the second generation is published
THEN new-reader freshness SHALL be determined by generation identity.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_same_epoch_replacement

### AC-S03-005 (unwanted-behavior)
GIVEN a request exits through cancellation
WHEN request cleanup runs
THEN the generation reader lease SHALL be released.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_reader_release

### AC-S03-006 (event-driven)
GIVEN unchanged repository, worktree, source-content, schema, relevant configuration and producer fingerprints
WHEN publication of the same canonical generation is retried
THEN the publisher SHALL reuse the existing generation identity.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_generation_replay

### AC-S03-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S03-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

Every product AC uses real SQLite, normal indexing/import, and production
reader/dispatch boundaries. Barriers/events replace timing sleeps.

- test_new_request_new_generation: actual cached-server red reproduction before
  fix; after full replacement the same server's next request sees new facts/ID.
- test_active_request_pinned: G1 lease reads before/after G2 publication while a
  second real request acquires G2; cover full, incremental and import publishers.
  Assert both results and IDs, not just connection object identity.
- test_failed_publish: inject a real build exception after staging has changed
  but before publication; new readers still acquire/query prior G1. Include
  eligible-source failure, staging cleanup, lock release, and retry success.
- test_same_epoch_replacement: changed facts yield a distinct ID with the same
  SCHEMA_EPOCH; same-path/stat tricks cannot substitute for explicit identity.
  Cover a source edit retaining size and mtime.
- test_reader_release: real task cancellation at the controlled await in the
  production lease, then actual sqlite3 closed-connection failure on attempted
  use. Also verify normal/error cleanup and no leaked acquisition on failure.
- test_generation_replay: same root/worktree, consumed bytes, schema/config/
  producer -> same ID across repeated full/incremental builds. Changed source,
  language selection/config, producer input, or worktree/root -> distinct ID.
  Tamper/missing provenance forces safe rebuild or read-only rejection, never
  fabricated source verification. Repeat source-free import -> same import ID.

Additional regression checks: build/import writer-lock exclusion; publication
barriers with concurrent reader acquisitions; actual extraction-buffer hashes
despite a source edit after read; legacy/import inheritance fallback; deleted
files; connection-associated epoch mismatch; zero read-path source/.atlas writes;
unchanged bounds and error shapes; canonical portable export/import stability.

Three independent coverage views converge: request lifecycle acquire/query/release,
publisher lifecycle stage/commit/swap/failure, and provenance lifecycle
consume/hash/inherit/retry. They cover the six product ACs plus separate RUN
receipt invariants; independent critique must challenge their interaction.

From worktree mcp-server, use /tmp/aci-s01-tools/bin/uv and
UV_CACHE_DIR=/tmp/aci-s01-uv-cache:
- uv run --frozen pytest tests/slices/test_s03_contract.py -q
- uv run --frozen pytest tests/test_schema_epoch.py tests/test_codegraph.py tests/test_export.py tests/test_cli_export_import.py -q
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

Before changes capture the current compute_indexer_fingerprint and after changes
verify it remains identical; run scripts/verify-probe-verdict.py against
.spectra/changes/aci-s02-probe/aci-s02-probe-lpa-vs-louvain.json from repo root.
Manifest-only lifecycle must not alter canonical fact/epoch/DDL fingerprint.
If it changes, do not re-fingerprint old evidence: investigate accidental graph
drift; a genuinely necessary fact change needs explicit scope amendment and new
measured probe evidence. Current S02 archive remains untouched. Cached pinned
Solidus/Spree inputs exist under /tmp/aci-s02-probe-inputs if later authorized.
Keep raw measurement logs compressed/lossless rather than huge tracked prose.

Use exact logs/exit codes and current worktree source. Platform-only Docker/OS
checks remain CI evidence, not claims from chmod alone. No dependency/lock change.

## Receipt and re-entry

Initialize only .spectra/state/aci-s03.execution.json IN_PROGRESS before coding:
stable slice_id/criteria_sha256, issue_url, base_commit, declared_paths,
output_commit initially null, output_digests, and eight per-AC result records.
Preserve a genuine intermediate receipt for RUN-02; resumed identity and frozen
hash must match the same authoritative path, with no planning state reset.

After product checks, make a bounded implementation commit and digest declared
tracked implementation files. Clean-checkout re-entry verifies existing receipt/
evidence and returns ALREADY_SATISFIED without implementation changes; require
identical before/after digest manifests and git diff --exit-code against the
output commit for declared paths (RUN-01). A later receipt metadata commit avoids
self-hashing. Reuse prior receipt verification approach without editing old
slice evidence. COMPLETE requires all eight ACs and applicable checks; no
automatic merge, release, deployment, or unverified milestone claim.

## Confidence

Independent ATLAS review approved with no prescriptions: actual refine score
4.6/5, minimum 4, pass. Actual Assemble confidence is 91.25 AUTO_PROCEED
(pattern_match 90, requirement_clarity 95, decomposition_stability 90,
constraint_compliance 90). Staging/hash overhead remains a practical risk;
no performance improvement or product correctness is implied by planning gates.

## Rejected Alternatives

Actual Explore scores:
- Request leases plus staging for all publishers: 84, selected; one explicit
  connection per request and immutable published inode retain existing SQLite
  journal/read-only semantics at the cost of incremental backup work.
- Cached handle with stat refresh: 59.5; races pathname validation with cached
  reads and cannot keep a live-mutated incremental generation fixed.
- WAL/read transactions on a live DB: 70; introduces checkpoint/sidecar/lifetime
  behavior and read-only deployment questions beyond this bounded slice.

## Risks

| Risk | Mitigation |
|---|---|
| Incremental or import bypasses publication discipline | One shared writer lock and staged file for every supported publisher. |
| Generation ID describes bytes other than indexed facts | Hash exact read buffer; content-based inheritance with producer/config compatibility. |
| Source-free import falsely claims source verification | Distinct validated-artifact provenance; no source facts inherited into later index. |
| Cached connection leaks or cross-thread use | Explicit ownership/finally-close and real closed-handle/thread-correct tests. |
| Legacy graph served with invented identity | Read-only INDEX_UNAVAILABLE; index performs full provenance rebuild. |
| Concurrency tests pass through timing luck | Deterministic barriers plus actual DB/query observations. |
| Generation metadata alters graph probe/export | Existing manifest only; exclude lifecycle fields from portable facts; verify fingerprint. |
| Three-day budget exceeded | Keep single-repo pathname pointer; no WAL, retention, CAS or whole-tree snapshot design. |

## Checker and handoff

Full-tier review at T is required before Assemble. Reviewer receives plan,
criteria and state; run full structural/EARS checks and actual refine cycle1
score, then return verdict, per-dimension findings and prescriptions.
Author identity ramza-backlog-order-s03; checker must differ. Read-only checker
may omit --state and return exact score/provenance for orchestrator recording.
Do not create independent approval or freeze early.

After review: record real critic; refine if needed; enter A; compute confidence;
declare scope including S03-PATH-01; freeze criteria once; validate full schemas
and spec/criteria digests; emit envelope; DONE. Helpers/schemas are under
/Users/henrique/workspace/oss/agents/atlas-aci/.eidolons/ramza/.
Envelope-relative payload paths: aci-s03.md and aci-s03.criteria.md.
Orchestrator verify-envelope --block trace must precede Vivi handoff.
Prior CRYSTALIUM persistence rejection is terminal; no retry or redirected payload.
