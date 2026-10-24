---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T01:14:49Z
slice_id: ACI-S06
issue_url: https://github.com/Rynaro/atlas-aci/issues/12
---

# ACI-S06 — One external graph root across writers and readers

## Scope

Intent CHANGE. Index a genuinely read-only source checkout into an explicitly
writable external graph root; every graph reader/publisher uses the same location.
Base 7b11b275b300c100ab7b123a3ca4bb60977f5166, clean main after PR #69.
Worktree /tmp/atlas-aci-aci-s06; branch aci-s06. Live stable marker, criteria hash,
absence of prior S06 state/receipt and no prerequisites verified before mutation.
No memex semantic change, index migration/deletion, new storage service, DDL/epoch,
portable fact schema, graph extraction/resolution change, or host/container path
alias equivalence. Prior slice receipts, criteria and probe evidence immutable.

Declared paths: mcp-server/src/atlas_aci/{__main__,config,codegraph}.py,
mcp-server/tests/slices/test_s06_*.py, README.md, hosts/* and .spectra metadata.
Narrow scope amendments authorized by this plan BEFORE implementation:
- mcp-server/src/atlas_aci/server.py: pass configured graph root to the production
  CodeGraph constructor; without this, serve cannot satisfy AC-S06-003.
- .github/workflows/ci.yml: enforce real read-only-source indexing and external
  graph stdio queries in the existing container smoke job, whose current writable
  preindex cannot establish AC-S06-001.
No other helper/test amendment is preauthorized; document exact necessity first.

Actual rightsize full score5: ten product files estimated (CLI/config/graph/server,
new test, README, three host guides, CI), public API, filesystem isolation/security
boundary, medium stakes. No dependency, migration or novel architecture flag.
Complexity8/12 extended (3,2,1,2); standard routing. Independent checker required.
DISCOVER/CLARIFY omitted because desired outcome and explicit exclusions are known.

## Approach

### Evidence and reusable pattern

ATLAS found atlas_dir=resolved_repo/.atlas; DB, lock, staging, old-epoch sweep,
S04 attempt sidecar all already derive from it. Change that one storage derivation,
not each filename independently. Carry the configured root into the internal
reader_lease constructor and production server constructor. Existing immutable
publication, real read-only leases and separate memex semantics remain intact.
CLI index/status/export/import construct graphs directly; serve config reaches
server.build_server_state. The probe exporter default constructor remains valid.
Generation provenance already contains the resolved source path: retain it.

Docker is locally available: root's escalated docker info succeeded with server
29.8.0. Default sandbox socket denial is not evidence of unavailable Docker.
Use scoped escalation for real local integration; CI also enforces it. No paid
models/services. Previously rejected external memory persistence remains terminal.

### Concrete interface and storage policy

1. Add optional --graph-root PATH consistently to index, serve, status, export
   and import, and optional graph_root to Config/CodeGraph. Preserve existing
   positional APIs by adding optional fields/parameters without reordering old
   ones. tools does not read graph storage and needs no cosmetic new flag.
   No environment variable is introduced: explicit CLI/config/constructor value
   wins; omitted/None means existing repository-local storage. Document this
   deliberately simple precedence and avoid hidden ambient configuration.
2. Canonical source identity is str(repo.resolve()), encoded UTF-8, SHA-256.
   With external root R, store beneath R.resolve()/repos/<full source SHA-256>.
   Without override use exactly repo.resolve()/.atlas. Keep graph.<epoch>.db,
   .index.lock, same-directory stages, sweep and index-attempt.v1.json beneath
   the selected per-source directory. This is a storage namespace, not a new
   fact-generation identity or Git identity; do not add R to generation_inputs.
3. Distinct canonical source locations, including actual Git worktrees, isolate
   under one operator-owned root. Symlink aliases of the SAME canonical source
   share identity. Index and serve must use the SAME source mount path (e.g.
   /repo in both containers); host /home/project and container /repo intentionally
   differ. Moving a checkout changes namespace; never infer equivalence or copy.
4. Validate explicit roots clearly: a file/non-directory component or inaccessible
   writer root errors; writable graph constructors may create the namespace.
   Read-only constructors/leases/status/export must never create any directory,
   lock, stage, journal or sidecar. Missing explicit root means unavailable,
   NEVER fallback to local .atlas. Existing local and explicit external graphs
   may coexist; explicit choice is authoritative and leaves the other untouched.
   Resolve root symlinks consistently; reject a namespaced path resolving outside
   its canonical configured root before reads/writes. Reject an explicit external
   root located within the source checkout (including symlink aliases), avoiding
   accidental graph self-indexing; omission retains the documented local default.
   This is operator-owned storage, not a hostile concurrent symlink sandbox.
5. Config normalizes graph_root without creating it; memex_root remains separate.
   CodeGraph is the single owner of deriving/validating graph storage. Server
   passes Config.graph_root; reader_lease passes self.graph_root. Preserve query
   caps, generation validation on the opened connection, lease cleanup and S04
   attempt correlation. No source-tree mkdir from external graph indexing.
   Error/help text identifies the actual selected graph directory, replacing
   hardcoded repo/.atlas assumptions where relevant.
6. Export reads the selected graph and writes only its explicit output argument.
   Import stages/locks/publishes in the selected external namespace and retains
   source-free cold-start behavior. Failed indexing writes attempt diagnostics
   only there. Neither path silently discovers/moves/deletes another root's DB.
   Ensure old-epoch cleanup is confined to the selected namespace and cannot
   affect sibling repos or the repository-local graph.
7. README and all three host guides show matching index/serve --graph-root
   values, separate --memex-root, real :ro source mounts with writable /graphs,
   canonical-path/worktree identity, explicit-root precedence and no fallback.
   Preserve defaults in existing examples or explain external opt-in clearly.

### Implementation order and baseline

Initialize the execution receipt before product edits. Capture clean base,
source/criteria digest and actual baseline commands. Add regression tests and
record genuine current behavior: external flag is rejected or current graph
construction attempts local .atlas; a real :ro fixture cannot be indexed locally.
Do not claim a mock or chmod-only fixture demonstrates mount-level read-only.

Implement central optional-root resolution, wire all constructors and leases,
then CLI/config/server, then docs and enforced CI integration. Run named checks
and existing S03/S04 generation/diagnostic tests; fix only introduced regressions.
Do not weaken prior tests or change historical expected behavior to hide failures.

## Stories

As an operator, I want graph state outside a read-only checkout so that indexing
and agent reads share one selected graph without modifying source.
One standard Vivi executor; three engineering days total; P1 risk.
Tasks: central root and constructor plumbing; real filesystem/stdio regressions;
docs/CI plus replay and receipt gates. At cap report remaining precise gaps.
Root orchestrator owns validated PR publication, green CI, merge, main sync and
next issue under standing user authorization. Executor never self-merges.

## Acceptance Criteria

Exact live issue bytes are frozen separately at .spectra/plans/aci-s06.criteria.md.
SHA-256 32a587ee6d402a14cf324de7ab19a8e48204956f0696c74adfb1a4cdcb5772cc.

### AC-S06-001 (event-driven)
GIVEN the source mount is read-only and the configured index root is writable
WHEN indexing runs
THEN the graph generation SHALL be written beneath the configured index root.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_readonly_source_index

### AC-S06-002 (event-driven)
GIVEN the read-only fixture is indexed
WHEN indexing completes
THEN the source-tree content digest SHALL equal its pre-index digest.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_source_unchanged

### AC-S06-003 (event-driven)
GIVEN an external index exists
WHEN serve is launched with the same graph-root configuration
THEN graph queries SHALL read that external index.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_serve_external_root

### AC-S06-004 (state-driven)
GIVEN no graph-root override is configured
THEN Atlas SHALL retain the documented repository-local default location.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_default_compatibility

### AC-S06-005 (event-driven)
GIVEN two repositories use the same external root
WHEN both are indexed
THEN each repository SHALL have a distinct storage identity.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_root_identity_isolation

### AC-S06-006 (event-driven)
GIVEN unchanged source is indexed into the same external root
WHEN the index command is repeated
THEN the canonical graph fact set SHALL equal the first successful result.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_external_replay

### AC-S06-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S06-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

The six named product tests use actual files/SQLite/parser output. Required
read-only integration is part of test_s06_contract.py, not an untracked manual
claim. Tests may skip Docker-only cases in ordinary developer runs without an
explicit test image, but readiness and CI MUST set ATLAS_ACI_S06_DOCKER_IMAGE;
configured integration fails on Docker errors and reports zero skipped required
ACs. No chmod fallback. Use host dev pytest/MCP SDK to control production image.

- test_readonly_source_index: fresh fixture /repo:ro, writable /graphs, container
  --read-only, production UID. First prove a source-write sentinel fails with
  EROFS under identical mounts; prove /graphs is writable; then index succeeds
  and all DB/lock/stage/diagnostics reside in /graphs/repos/<identity>.
- test_source_unchanged: canonical source relative paths and byte digest before
  and after real indexing/query; also compare source metadata and absence of
  .atlas. Snapshot after fixture setup, not before; exclude only atime.
- test_serve_external_root: real MCP initialize/list_tools/call via stdio child
  using same /repo path and root; query a unique fixture symbol and assert real
  returned fact. No local index exists. A wrong explicit root must return
  INDEX_UNAVAILABLE even when a distinguishable local decoy graph exists.
- test_default_compatibility: omission preserves repo/.atlas, old constructors,
  index/status/export/import and serving. Config memex remains independent.
- test_root_identity_isolation: two source repos plus actual Git worktrees share
  an external root but have different namespaces/facts; rebuilding/importing/
  sweeping one cannot affect the others. Canonical source aliases share one.
- test_external_replay: index identical sources twice, including incremental
  mode, and compare canonical exported graph facts; external status replay and
  generation lease/failed-attempt behavior remain consistent.

Additional controls: explicit root is a file, inaccessible writer root, missing
RO root creates nothing, namespace symlink escape rejection, source-contained
external root rejection, both-root conflict selection, export/import roundtrip
including source-free destination, pinned lease through publication, malformed
refresh keeps G1 and writes only external sidecar. RO status/export/serve preserve
graph directory bytes/mtimes. Bounded real subprocess timeouts and cleanup are
required; no arbitrary sleeps as assertions, leaked Docker processes/volumes.

Local reproduction/acceptance commands (worktree root, using scoped Docker
escalation if sandbox blocks its socket):
```sh
docker build -t atlas-aci-s06 -f mcp-server/Dockerfile mcp-server
cd mcp-server
ATLAS_ACI_S06_DOCKER_IMAGE=atlas-aci-s06 UV_CACHE_DIR=/tmp/aci-s01-uv-cache /tmp/aci-s01-tools/bin/uv run --frozen pytest tests/slices/test_s06_contract.py -q
```
The test fixture owns temporary source/graphs/memex mounts, makes only the
operator-owned temporary graph/memex dirs writable for UID10001, records the
actual docker commands, negative sentinel, digests and successful MCP response.
Its production invocations have these exact argument shapes:
```sh
docker run --rm --read-only -v <source>:/repo:ro -v <graphs>:/graphs -v <memex>:/memex atlas-aci-s06 index --repo /repo --graph-root /graphs
docker run --rm -i --read-only -v <source>:/repo:ro -v <graphs>:/graphs:ro -v <memex>:/memex atlas-aci-s06 serve --repo /repo --graph-root /graphs --memex-root /memex
```
Existing CI smoke keeps its default-path regression; add the new Docker-image
test run with uv/dev dependencies and required image environment so external
read-only index/query cannot silently skip. Do not replace legacy smoke with an
external-only path or treat server-alive as graph query proof.

Other commands from mcp-server (same uv/cache):
```sh
uv run --frozen pytest tests/slices/test_s03_contract.py tests/slices/test_s04_contract.py tests/test_codegraph.py tests/test_schema_epoch.py tests/test_export.py tests/test_cli_export_import.py -q
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
uv run --frozen mypy src/
uv run --frozen ruff check ../scripts/ --exclude fingerprint-fixture
uv run --frozen ruff format --check ../scripts/ --exclude fingerprint-fixture
uv run --frozen mypy ../scripts/
bash ../scripts/test-harden-gate-classify.sh
bash ../scripts/test-verify-probe-verdict.sh
bash ../scripts/test-verify-export-size.sh
```
Mechanically compare behavioral compute_indexer_fingerprint before/after and
verify current S02 probe. Expected unchanged 11adbda5...; if changed investigate
actual canonical facts rather than re-signing historical measurements. New
measurement requires a narrow amendment; old probe files remain immutable.

## Verification layers and independent checker contract

Dependency/call-site set: CLI index/status/export/import -> CodeGraph; serve ->
Config -> server constructor; reader_lease -> CodeGraph; storage derivation ->
DB/lock/stage/sweep/attempt; export/import -> same namespace. All are mapped.
Constraint check: three-day bounded optional-location change, no data migration.
Self-consistency decompositions: command view, storage lifecycle view and test
view all cover the same eight seams above; no omitted writer/reader. Conservative
unknowns: no cross-mount alias or hostile-symlink race equivalence guarantee.

At T an independent ATLAS checker must review the concrete boundary/config policy,
all constructors, genuine :ro and stdio evidence design, default/import/lease/S04
compatibility and scope amendments. Run full structural/EARS gates, independently
verify live criteria SHA/base, then actual ramza-score --rubric refine --cycle 1
with clarity/completeness/actionability/efficiency/testability dimensions.
Return distinct checker identity, verdict/blockers, actual score JSON and timestamp,
commands/exit codes and source anchors. No product verification claim at planning.
Maker ramza-backlog-order-s06 cannot be checker. Do not advance to A without
genuine independent pass recorded through ramza-gate critic.

## Receipt and backlog continuity

Authoritative backlog .spectra/backlog/execution.json and concise order.md record
standing authorization, frozen 37-issue queue, five actual merges and current #12.
Future slice IDs/paths remain null until live verification. Preserve this ledger;
never mark queued issues complete or amend S23 release selection silently.
The last pending merge may be carried by the next slice's bookkeeping, but final
state must reconcile actual PR merges, closures and COMPLETE receipts.

Initialize .spectra/state/aci-s06.execution.json IN_PROGRESS before coding with
slice_id/issue_url/criteria_sha256/base_commit/declared_paths/output_commit=null,
output_digests and eight AC results. Capture real partial receipt/resume identity
(RUN-02). After checks commit bounded implementation, record actual output commit
and tracked implementation SHA manifest. Clean reentry verifies exact digest
equality and git diff --exit-code against that output commit for declared paths,
returning ALREADY_SATISFIED without another implementation edit (RUN-01).
Use later metadata commit to avoid circular receipt hashes. Required Docker
evidence may not be skipped in a COMPLETE receipt. Root handles merge/CI/sync;
record exact capability blockers with evidence and continue eligible independent
work only when orchestrator approves scheduling. No paid evaluation, deployment,
credential changes or external memory persistence authorized.

## Confidence

Actual confidence tool: 90, AUTO_PROCEED (2026-10-24T01:18:34Z), dimensions
pattern_match85/requirement_clarity95/decomposition_stability90/constraint_compliance90.
Independent ATLAS APPROVE, checker atlas-s06-plan-checker, actual refine score4.6
(min4) at 2026-10-24T01:17:43Z. State replay records the same genuine review
dimensions at 01:18:34Z, separately from its original observation timestamp.
Full structural/EARS8 and criteria/base/ledger checks pass; no prescriptions.
Planning lint/score is not runtime proof.

## Rejected Alternatives

Actual Explore: explicit root with canonical repo namespace 91 selected.
Direct operator DB directory 72.5 is simple but collisions/worktree isolation
become operator accidents. Git common-dir/worktree catalog 68 adds identity and
catalog dependencies without solving host/container path provenance consistently.

## Risks

| Risk | Mitigation |
|---|---|
| Forgotten internal/server constructor reads local graph | Named lease/server plumbing plus actual stdio decoy-root regression. |
| Shared root cross-repo corruption | Full canonical path hash namespace, real worktrees and confined sweep tests. |
| RO claim based only on chmod | Actual :ro mount and negative EROFS source-write sentinel in local and CI runs. |
| Root silently changes source identity | Keep source provenance unchanged; document same source mount path. |
| S04 sidecar remains local or wrong generation | Derive from selected atlas_dir and exercise failed refresh/status. |
| Historical facts/probe changed accidentally | Exact existing schema and canonical fingerprint checks; immutable probe. |

## Assemble handoff

Helpers/schemas at original checkout .eidolons/ramza/{bin,schemas}; package1.1.0,
ECL2.0, plan-state.v1. Complete genuine critic, confidence, drift declaration,
freeze, emission and full JSON schemas/formats plus independent digests.
Envelope paths relative to its directory: aci-s06.md and aci-s06.criteria.md.
Root mechanical verify-envelope --block and correlated trace precede one Vivi
executor. All planning files under .spectra; no product modifications by RAMZA.
