---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T18:19:39Z
slice_id: ACI-S08
issue: https://github.com/Rynaro/atlas-aci/issues/30
---
# ACI-S08 — coherent graph and source snapshots

## Scope

Implement issue30 at base fa75bec971502b72ce85f9e87ab7ec2876274ce9 in
/tmp/atlas-aci-aci-s08, branch aci-s08. Exact live criteria digest:
216f05f349bf35630f299ba814be697b8150e3b0da0bd2fef0512a21b34d3dba.
Preflight verified actual S03/S06/S07 interfaces and COMPLETE ancestor receipts
(7/11/332 output digests), plus S15's314 historical output digests. No S08 state or
receipt existed. Ledger records PR74 merged,10 merged/32 remaining,current30,next33.

In: immutable source blobs, canonical snapshot manifests, graph/source reader
leases, actual worktree identity, explicit byte overlays, bounded inactive-history
retention and readonly readers. Out: editor integration, cross-repository graphs,
filesystem-wide transactional capture, unlimited history, remote CAS, new services,
dependency versions, Windows/network-filesystem guarantees, release/deployment or
paid evaluation. POSIX local filesystems are the existing fcntl-backed host envelope.
No new performance threshold; report measured bytes/FDs/time as costs.

Declared product paths:
- mcp-server/src/atlas_aci/codegraph.py
- mcp-server/src/atlas_aci/storage/snapshots.py
- mcp-server/src/atlas_aci/storage/evidence.py
- mcp-server/tests/slices/test_s08_*.py
Narrow amendments BEFORE implementation:
- mcp-server/src/atlas_aci/server.py: graph response snapshot token, optional
  snapshot-backed view_file schema/dispatch, preserving centralized bounds.
- mcp-server/src/atlas_aci/tools/view_file.py: read authenticated snapshot bytes
  through the lease while preserving existing live mode and line/byte limits.
- README.md: source mode/token, overlays API, identity/retention/platform limits.
- mcp-server/tests/test_server.py: only directly affected optional schema and
  view_file response metadata expectations; preserve all bounds/pagination tests.
- .spectra/plans/aci-s08.*, .spectra/plans/ramza-calibration.jsonl,
  .spectra/changes/aci-s08/*, .spectra/evidence/aci-s08/*,
  .spectra/state/aci-s08.execution.json, .spectra/backlog/execution.json and order.md.
No CLI/config/portable-format/DDL rewrite is planned. Amend observed additional
test needs narrowly before editing, never rewrite prior criteria/receipts/probes.

Actual FULL6:12 estimated files, public API/security/new lifecycle, medium stakes.
Complexity9(2,2,2,3), extended reasoning within standard role routing; issue P0 does
not activate TRANCE. Maker ramza-backlog-order-s08; independent ATLAS required.
Known contract needs no clarification. External memory rejection is terminal;
local source/prior plans provide patterns, no retry or external persistence.

## Approach

### Existing seams and first proof

S03 reader_lease1092–1121 pins only SQLite. _run_build2044–2052 reads one source
buffer; generation.v3 stores hashes/observations but no bytes. Server397–408 leases
graph calls while view_file independently opens live paths. S06 routes all graph
storage through its canonical-path namespace. S07 history can contain old B bytes
alongside current B; imports deliberately have no source checkout. S09 cache stores
raw facts, not bytes; S10 rebind witnesses and S15 Rust ownership remain untouched.

Adapt S03 staged publication + S06 namespace, adding one storage helper. The
initial per-snapshot flock design FAILED actual host/Docker qualification:
macOS EX succeeded while Linux held SH on the same readonly bind-mounted file.
Native macOS and same-kernel Linux exclusion both worked. This observed failure,
not a hypothetical concern, triggered genuine T→R cycle1; original scores remain
in state and planning/flock-domain-failure.json preserves evidence.

Revised first checkpoint: one coalesced immutable source-pack FD per lease.
Qualify host macOS unlink/replacement while a Docker UID10001 reader holds the
original O_RDONLY FD through a directory :ro mount; require original offset/hash
reads after pathname removal. Same native macOS/Linux controls and before-open
race controls are required. ATLAS's primitive proof is distinct from later actual
SQLite+pack acquisition/publication/pruning tests. Stop if the descriptor does
not retain original bytes on a required deployment; do not silently drop existing
host-indexed→readonly-Docker support or substitute unlimited retention.

Actual independent ATLAS primitive result: all9 cases pass on native macOS,
Linux and macOS-host→Docker-Desktop readonly directory mounts. The reader opened
one O_RDONLY pack FD without prereading; after unlink/replacement, five pread
ranges/EOF and the full4,194,319-byte hash matched the original. Normal/error
closure restores FD count; before-open missing/wrong-content controls return
retry without source exposure. Scripts, raw logs/results, normalized JSON and
SHA256 provenance are under .spectra/evidence/aci-s08/planning/pack-fd-proof/.
Original pack SHA8246b3b30d88a5e3cfc50c75bbd7e440fa72bb40c945389c19af3d04e18b5db1.
Path/stat/nlink visibility can be cached or change: bytes/hash, not metadata tuple
identity, are the invariant. Process-exit cases establish exit/OS-close semantics,
not measured physical block reclamation. This qualifies the storage primitive;
actual graph/manifest race closure and integrated lifecycle remain unverified.

### Canonical content and worktree boundary

A versioned closed snapshot manifest contains canonical resolved source root,
repository/worktree identity, schema, sorted relevant configuration, complete
producer fingerprints, sorted current observed path→source SHA256 vector, and
the graph-authorized historical source bindings with explicit availability, and
a validated content-addressed pack index (blob hash→offset/length, pack hash/size).
Current observations include indexed, failed-syntax, disabled and recognized
unsupported files actually read, not only successful fact producers. Existing
skip/unrecognized-file policy remains visible; snapshot does not claim whole-repo
byte capture. Canonical source context includes retained historical evidence
inputs when present: different S07 predecessor histories are not identical inputs.

Use actual Git common-dir/worktree git-dir/root identity for real linked worktrees,
read through readonly git metadata commands with GIT_OPTIONAL_LOCKS=0. Distinct
resolved worktree paths remain distinct. Non-Git roots use explicit path identity.
If a .git marker exists but metadata is unavailable (e.g. outside container mount),
record Git identity unavailable rather than claim non-Git or cross-mount equivalence.
Branch/HEAD are operational diagnostics, excluded from canonical IDs: branch moves
with unchanged captured bytes and identity must replay. No git hooks/builds/source
execution. Existing S06 same canonical source-mount requirement remains.

Hash canonical manifest bytes to snapshot_id; no clocks, mtimes, inodes, random
tokens, cache counters or SQLite row IDs. Generation format atlas-generation.v4
binds snapshot_id and canonical manifest digest, while validating exact v1/v2/v3
old shapes. Audit every generation-format/coverage branch so v4 retains exact
observation and S04 capability semantics rather than falling into legacy unknown. Keep SQL schema/epoch and portable evidence format unchanged. Avoid
a circular generation↔snapshot digest: snapshot carries canonical graph source/
observation bindings, not the generation ID derived from the snapshot reference.
Reader validates snapshot identity and its correspondence to its OWN opened graph,
including current observation hashes and historical evidence authorization.

One source buffer per enumerated path is consumed by CAS, hashing and extraction/
cache lookup. Same-size/same-mtime edits are detected by bytes as today. A captured
sorted per-file vector can span concurrent edits; it is NOT an atomic filesystem
instant. Returned graph facts and source bytes correspond to that vector. A read
error, unsafe path, required-blob failure or fatal extraction error aborts staged
publication and preserves G1. S07 recovered syntax remains a complete observation
with partial extraction, not a falsely complete source analysis.

### CAS, immutable publication and historical truth

Place immutable packs/manifests entirely under the configured graph namespace,
separate from source and memex. Blob SHA256 is the logical content address;
coalesced packs are the physical backing for multiple blobs. This is packed CAS,
not one open FD per source file. Each pack contains unique blob contents in sorted
hash order, with a fixed versioned header and exact offset/length entries in the
canonical manifest. Pack SHA256 addresses the physical object. Repeated identical
packs reuse bytes; no claim of incremental cross-pack deduplication. Internal
packs are not a new portable source export format.

One helper owns normalized relative paths, lowercase SHA256 grammar, canonical
manifests and descriptor-relative safe opens. Reject absolute/../noncanonical
aliases and foreign hashes; authorize (path,hash) against the leased manifest
before reading its range. Secure component traversal with dir_fd, O_NOFOLLOW/
O_DIRECTORY and regular-file fstat. A source symlink/race that cannot be safely
bound inside the source root is a fatal read error, not permission to follow an
outside target. Document and test real symlink replacement/foreign-source controls.

Under the existing writer index lock, stage exact consumed buffers in graph-root
temporary storage, keyed by blob hash; assemble the deterministic pack in bounded
chunks without rereading live source or holding every source buffer in RAM.
Historical available bytes come from a validated predecessor pack FD, not current
paths. Validate each blob hash, pack hash/header/size, unique entries, exact sorted
order, integer (not bool) nonnegative offsets/lengths, nonoverlap, bounds and exact
payload coverage; reject duplicate, trailing, truncated or inconsistent records.
The current/historical graph binding closure must match the manifest, not merely
a self-consistent unrelated pack. Existing hash names require content verification.

Build/validate staged graph and closed manifest, requiring every available current/
historical blob before publication. Flush durable files/directories within the
tested host envelope; no untested power-loss promise. Install complete pack and
manifest atomically before replacing the graph pointer. Failure before replacement
preserves G1; orphan staging is collected later. Never modify bytes of a published
pack or manifest in place. Real missing/corrupt required content rejects publication.
Historical bindings preserve exact old path/hash/provenance; never rebase old facts
onto current buffers. Previously source-backed historical evidence remains required
and retained, including when a failed refresh keeps old facts quarantined. Pre-S08
history lacking bytes is explicitly unavailable_legacy, with its known hash;
unknown hashes stay unknown. Current consumed bytes may authenticate a matching
historical hash, but arbitrary leftover CAS cache presence cannot alter canonical
availability/replay. No live-source fallback for missing historical content.

Source-free import stays graph-readable with explicit unavailable source provenance.
It must not synthesize a snapshot from live destination files or stale local CAS.
New builds over imported/legacy state capture actual current buffers, retaining
honest unavailable historical bindings. Portable JSONL remains facts/evidence;
it does not promise source-blob transport. Export/import strictness and all old
producer profiles remain valid. Add snapshots.py to actual producer hashing and
the exact new allowed module set; retain all previous exact sets, no open-ended
module acceptance or rewriting old producer metadata.

### Readonly leases and bounded pruning

No cross-kernel advisory lock is used for source retention. A reader opens its
readonly SQLite connection, validates that connection's generation, reads and
validates its immutable manifest, then opens the referenced pack O_RDONLY through
a safe descriptor-relative path. Validate actual pack bytes/hash/index against the
manifest before exposing the graph/source handle. Hold one pack FD plus SQLite
for the lease; retain the validated manifest in memory. Source reads use pread on
that FD and authenticate the requested blob hash. Never reopen by pathname after
acquisition. Multiple logical blob reads do not create per-blob retained FDs.

Publication may replace the graph and prune paths during acquisition. If manifest
or pack disappears before its FD is acquired, discard the unexposed SQLite handle
and retry once from the current generation; a second race returns a bounded
SNAPSHOT_UNAVAILABLE error, not mixed bytes or an infinite loop. Explicit old-token
source acquisition never substitutes the latest token: missing means unavailable.
Once the pack FD is open, unlink/replacement cannot change its object under the
qualified filesystem semantics. Validate before exposure and release everything
on every retry, normal/error/controlled-cancellation path. Old v1/v2/v3 generations
without a source manifest remain graph-readable/source-unavailable; v4 missing or
contradictory source data fails closed. Readonly paths never mkdir, create lease
files, migrate, checkpoint, repair or write source/graph storage.

Writer maintenance under the existing writer lock retains directory entries for
current plus one previous completed source snapshot, and deletes older manifests
and packs not referenced by those retained manifests. Publication order/catalog
metadata is operational and excluded from IDs. Same-ID replay does not replace
an immutable existing pack; sharing a pack between retained manifests keeps its
entry. Source-free import/legacy publication updates this retention bookkeeping
without inventing source availability. No compatible G1 sweep before publication.

An active old lease may hold an unlinked pack: the OS retains its bytes until the
final descriptor closes, including cross-process/qualified Docker readers. Thus
idle namespace entries are bounded, retained FDs are O(live leases), and disk
consumption includes active unlinked packs until release. There is NO global hard
byte bound across arbitrary independent readonly processes and no invented TTL.
No process registry, writable lease files, PID reaper or background service.
A new request for a pruned token fails; an already acquired lease stays readable.
After normal/error/process-exit release, OS resources are reclaimed. Prune failure
cannot invalidate the newly published/current retained pack. Concurrent writers
retain the existing index-lock support envelope; this slice establishes host
writer↔readonly-container readers, not distributed cross-kernel multiwriter locks.

### Public source access and explicit overlays

reader_lease yields a graph reader with snapshot_id and read_source(path,
source_sha256=None) sharing its source lease. A historical source request supplies
its proof hash; omitted hash selects the CURRENT observed path binding. Missing
or unauthorized binding fails explicitly. Graph search/query responses include
snapshot_id and source availability without unbounded manifest data.

view_file adds optional snapshot_id and source_sha256. With a token it acquires
that retained source snapshot, authenticates the path/hash, decodes original bytes
and applies existing line/byte/overflow/cursor rules. It NEVER falls back to live
files. Without a token preserve existing live view behavior, explicitly labeled
source_mode=live; snapshot mode is labeled snapshot with ID/hash. Documentation
instructs graph-evidence callers to pass the returned token (and historical hash
when needed). Explicit source-unavailable errors cover old/imported generations;
no token is invented. A source-only token can outlive the graph request within
retention; this does not promise historical graph-query access after G2.

Expose only a bounded Python build API overlays: Mapping[str,bytes] | None, copied
at invocation. Overlay bytes override the disk buffer; new unsaved normalized
relative paths join deterministic enumeration. Validate eligible recognized paths,
reject skipped/unrecognized/unsafe overlay entries explicitly; no tombstone/editor
protocol/automatic editor reads. Do not write overlay bytes into the source tree.
Overlay content, not its editor origin, determines canonical hashes. Same bytes
on disk versus explicit overlay replay identically. New source code must not
execute or require Cargo/compiler/template evaluation.

## Stories

Theme: reliable navigation; project: immutable source evidence; feature: snapshot
lease; executor: one Vivi standard allocation with independent ATLAS verification.
Total hard cap5 engineering days; elapsed active work tracked, no silent extension.
- Story1 (1d,P0): as a reader, acquire coherent source/graph bytes. Establish real
  pack-FD primitive + red old-graph/new-source reproduction; implement safe CAS and
  canonical manifest/publication closure. Output actual native proof and tests.
- Story2 (1.5d,P0): as an operator, retain active snapshots across builds/prune.
  Wire all publishers and readonly leases, legacy/import unknowns, closed
  generation/producer validation and failure cleanup. Output barrier tests.
- Story3 (1d,P1): as an agent, request graph-linked source and explicit overlays.
  Wire worktree identity/build overlays and optional view_file token with bounds,
  preserving live compatibility. Output real Git/stdio/overlay controls.
- Story4 (1.5d,P0): as maintainer, verify all compatibility/reentry claims. Run
  full/static/Docker/probe gates, independent review and receipt. At cap report
  remaining checks; no narrower inputs, skipped required integration or new spend.

## Acceptance Criteria

The unchanged eight EARS blocks in aci-s08.criteria.md are normative. Mapping:
AC001→real cached production graph lease, edit/publish/prune, authenticated old bytes;
AC002→actual git linked worktrees plus distinct non-Git roots/external namespaces;
AC003→dirty same-stat edits and new untracked/overlay bytes change snapshot digest;
AC004→remove/corrupt required blob just before publication, preserve G1 byte/hash;
AC005→independent RO-process lease surviving G2/G3/prune, then collectible on release;
AC006→same construction cold/warm/full/incremental replay exact manifest/ID.
RUN01/02→the single receipt and exact tracked-content/no-op protocol below.

Additional nonvacuous controls:
- G1 query returns actual proof span/hash; read original bytes after live edit,
  including old historical B and new current B under one path. No fresh/stale
  relationship leakage; S07 partial coverage remains explicit.
- Actual processes synchronized by pipes/events, no timing-sleep proof. Acquire
  across publish/prune interleavings; active G1 does not prevent G2 completion.
  Normal, exception and controlled CancelledError release verified by closed
  SQLite/FD assertions and absence of leaked open deleted-pack handles. Synchronous dispatch cannot
  be preempted mid-sync call; no claim of live MCP cancellation without evidence.
  Two concurrent leases survive closing either one; one lease does not release
  another lease's open-pack lifetime. Prune before pack-open retries/fails cleanly;
  prune after pack-open preserves exact bytes through the acquired descriptor.
- First build failure, existing G1 failure, orphan staged blob, corrupt manifest,
  foreign path/hash, real symlink swaps and missing legacy source fail honestly.
  Malformed pack cases include bool/negative/overflow offsets, overlap, duplicate
  hashes, truncated/trailing bytes and a coherently rehashed foreign graph binding.
  Retry repairs safely. Readonly acquisition/prune attempts leave graph/source
  inventories unchanged. No source fallback even if live file happens to match.
- Same bytes/identity/config/schema/producer/history replay across new writer,
  full/warm/incremental; different root/worktree/config/producer changes identity.
  Branch/HEAD-only diagnostic changes do not. Exact raw facts/portable JSONL match
  prior semantics; no operational metadata contaminates facts/status identity.
- New unsaved overlay, overlay overriding dirty file, malformed/unsafe overlay
  entries, unsupported/disabled observed sources, history with unknown bytes,
  source-free import without source directory and pre-S08 readers all covered.
- FD growth bounded by live leases (one pack FD each) plus transient staging, independent of file count;
  after release resources return to baseline. Retention tests prove actual cleanup
  of unheld snapshots/blobs, not merely "active files still exist forever."

Before code capture baseline actual graph/source mismatch and existing S03/S06/
S07/S09/S10/S15 checks. Commands from mcp-server with PATH=/tmp/aci-s01-tools/bin:$PATH
and UV_CACHE_DIR=/tmp/aci-s01-uv-cache:
uv sync --frozen
uv run --frozen pytest tests/slices/test_s08_contract.py tests/slices/test_s08_integration.py -q
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src
Only include integration.py if created. Record actual commands, exits and source
hashes; use qualified locked Rust asset, no version/provider substitutions.

Use existing Dockerfile, --network none, nonroot UID10001, source:/repo:ro and
external /graphs. Build once with /graphs writable, serve/query/view snapshot with
/graphs:ro; a separate writer mount publishes/prunes while that RO reader holds
G1. Prove source write sentinel EROFS/permission failure, unchanged source hashes,
no RO writes and genuine stdio graph→snapshot view. Native macOS+Linux proof is
required, not future CI only. Preserve existing CI matrix and bounds.

From root run actual unchanged behavioral gates:
python3 scripts/verify-probe-verdict.py .spectra/changes/aci-s07-probe/aci-s07-probe-lpa-vs-louvain.json
bash scripts/test-verify-probe-verdict.sh
bash scripts/test-verify-export-size.sh
Expected epoch/DDL/projected graph fingerprint
0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f must be measured.
If it changes, diagnose and obtain genuinely fresh evidence for any intended
change; never re-sign old measurements. Preserve known current export-size/RSS
disclosures and all original pinned inputs/default languages/bar.

After independent measurement audit, clean only explicitly inventoried task-local
temporary DB/CAS/full exports under S08 scratch roots. Preserve compact tracked
evidence, source/seed checkouts, qualified SDK assets and active lease resources.
Record exact cleanup roots and before/after bytes; no broad /tmp or repo deletion.

## Verification layers and checker

Three decompositions: consumed buffer→CAS→manifest→graph pointer; readonly
acquisition→active token→source read→release/prune; worktree/overlay/history→canonical
inputs→cache/binding/export compatibility. Shared invariants are authenticated
original bytes, own-generation binding, explicit unavailable states, no RO writes,
and atomic successful publication. Structural/EARS, dependency coverage, five-day
constraint and adversarial concurrency review precede independent ATLAS critique.
Checker must inspect actual pack-FD proof and rejected lock proof and distinguish it from integrated product
evidence. No maker self-approval or invented human review.

## Receipt and backlog continuity

Initialize only .spectra/state/aci-s08.execution.json IN_PROGRESS with ACI-S08,
criteria/base/scope/eight AC slots and output_commit=null. Adapt existing S15
verify_execution.py/verify_resume.py into NEW S08 evidence paths; no new framework.
Preserve interrupted receipt identity for RUN02. Root owns publication/green CI/
merge/main sync under standing user authorization, executor never self-merges.

Exactly six metadata-only output_digest_exclusions, declared before receipt init:
.spectra/state/aci-s08.execution.json;
.spectra/evidence/aci-s08/final-reentry-before.json;
.spectra/evidence/aci-s08/final-reentry-after.json;
.spectra/evidence/aci-s08/final-reentry-summary.json;
.spectra/evidence/aci-s08/final-resume-identity.json;
.spectra/evidence/aci-s08/output-check-tests.log.
Exclude only these from output digests/output-commit comparisons to avoid circular
self-hashes. All product/tests/stable preoutput evidence/frozen plan/criteria/scope
amendments remain hashed; whole checkout must still be clean for reentry.

Record a concrete local implementation output commit; later receipt bookkeeping
commit contains COMPLETE only after actual eight AC results, command/exit/evidence,
declared paths and verified digests. RUN01 clean checkout invokes verifier against
that output, compares before/after tracked manifests and git diff --exit-code for
declared implementation paths. Matching completion returns ALREADY_SATISFIED with
no product diff. Changed criteria/receipt/output blocks until reconciled. Root
mechanical ECL verify_pass correlated to message/thread/spec is required before
Vivi starts; independent product verification precedes publication.

## Confidence

Not yet computed: Assemble uses the actual helper after independent review.
Remaining assumptions are cross-boundary open-FD retention, graph/pack acquisition
race closure and five-day integration breadth. Native primitive evidence can resolve
the first; integrated tests remain execution obligations, never planning claims.
Honor the actual verdict, including human review if VALIDATE applies.

## Rejected Alternatives

Actual explore scores: global shared lock through reader68/weak rejected because
it blocks G2; per-snapshot locks84.5/solid initially selected then REJECTED by the
actual cross-kernel proof (macOS EX succeeds during Docker SH); preopen every blob
FD73/solid rejected for file-count resource pressure. The revised coalesced
content-addressed source pack scores84/solid: one FD per lease, more pack-copy I/O
and disk duplication, no shared lock-domain assumption. The old84.5 score is not
retroactively changed or treated as evidence of feasibility. Native proof and
independent critique must qualify the new design before handoff.

Process-only registries cannot protect RO readers; writable lease files violate
RO mounts. Live-source hash checking cannot return old bytes. Unlimited idle CAS,
mtime identity, implicit editor reads and legacy-byte guesses violate boundaries.

## Risks

- Open-file lifetime differs across filesystem boundary: actual macOS/Linux and
  host→RO-Docker unlink/read proof is required; no dropped supported deployment.
- Acquisition/prune race: validate own SQLite/manifest/pack before exposure,
  bounded fresh acquisition retry, never reopen active source by pathname.
- Old evidence paired with fresh bytes: exact (path,hash) authorization and closed
  source availability; no fallback or old-producer relabeling.
- Security TOCTOU: descriptor-relative opens, strict pack offsets/hashes and real
  symlink/race controls; malformed local metadata is rejected without repair.
- Cost: coalescing may duplicate unchanged bytes across retained packs, and active
  unlinked packs consume disk until close. Measure/disclose; no global-byte claim.
- Timebox breadth: prioritize six ACs and compatibility, stop honestly at5d;
  no general storage/provider platform or portable source-bundle expansion.
