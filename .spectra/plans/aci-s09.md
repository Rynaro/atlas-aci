---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T15:21:08Z
slice_id: ACI-S09
issue: https://github.com/Rynaro/atlas-aci/issues/31
---
# ACI-S09 — content-addressed extraction cache

## Scope

Implement issue31 on clean merged base dd88912f73b728a57d7fc424093b88b0238550e8,
branch aci-s09, worktree /tmp/atlas-aci-aci-s09. Live marker/criteria verified;
preflight verified all332 COMPLETE S07 receipt digests and output-commit ancestry.
Actual S03/S04/S06/S07 behavior exists; no missing predecessor. No S09 prior state
or receipt. Exact criteria SHA de38446751926cb40e918c324155eb1020503a678c2ca2aec366547561819c05.

In: local per-file extraction cache, complete/failed entry states, atomic writes,
strict input/payload validation and operational cache diagnostics. Out: S10 semantic
invalidation, remote/shared services, TTL/eviction policy, compiler providers,
export compression, new performance thresholds or source-byte authentication.
The existing S07 partial-extraction/stale-history, generation leases, external graph
root, canonical exchange and query semantics remain controlling invariants.

Declared implementation paths:
- mcp-server/src/atlas_aci/indexing/__init__.py
- mcp-server/src/atlas_aci/indexing/extraction_cache.py
- mcp-server/src/atlas_aci/codegraph.py
- mcp-server/tests/slices/test_s09_*.py
Narrow amendments, recorded before execution:
- mcp-server/src/atlas_aci/storage/evidence.py: its closed producer module allowlist
  must recognize the new extraction-cache implementation hash while retaining exact
  old epoch6/S07 producer shapes; no relaxation to arbitrary module names.
- README.md: cache location, miss/failure behavior, diagnostics and measured limits;
  no speed promise or claim that the existing large full export shrank.
- .spectra/plans/aci-s09.*, .spectra/plans/ramza-calibration.jsonl,
  .spectra/evidence/aci-s09/*, .spectra/state/aci-s09.execution.json,
  .spectra/changes/aci-s09/*, .spectra/backlog/execution.json and order.md.
Any further existing-test/product change requires a specific observed assumption
and scope amendment first. Prior plans/criteria/receipts/probe artifacts immutable.

Actual rightsize FULL5: eight estimated files, public build diagnostics, parser-
bypass persistence trust boundary, new extraction-packet representation, medium
stakes. Actual complexity8 (2,2,2,2), extended; standard routing, independent
ATLAS checker before Assemble. P1 does not activate TRANCE. DISCOVER/CLARIFY not
needed: objective, scope and failure policy are defined. No paid evaluation or
external memory persistence; prior rejection is terminal, no retry.

## Approach

### Reuse the narrow extraction seam

ATLAS scout/source verified: _run_build1910–1926 establishes producer/config
compatibility;1974–1984 reads and hashes exact source once;2002–2016 reuses only
compatible indexed predecessor facts; full builds always parse. _extract2372+
returns symbols/references/assignment tuples/rationale plus mutable sidechannels
_canonical_file_records/_canonical_imports/_canonical_bindings/_canonical_file_entity.
Insertion2084–2199 consumes them. Global resolution and rationale run2247–2248.

Package ALL these extraction outputs into one typed serializable ExtractionPacket
at that existing seam. Cold extraction and cache restoration use the SAME packet
insertion path. Restore/replace every sidechannel or remove hidden sidechannels
at this seam; no broad extractor/resolver rewrite. Cache entities, occurrences,
evidence, producer and raw lexical/import/assignment/rationale inputs. NEVER cache
resolved graph assertions/edges, global candidates, generation/coverage/observation
metadata, current/stale history, SQLite row IDs, timestamps or parser tree objects.
Global _resolve_edges and rationale resolution still run for every completed build.
A dependency edit can therefore change bindings despite a source-file cache hit.

### Exact cache key and storage

Use atlas-extraction-cache.v1 envelope with a canonical SHA256 key over:
format/packet version; source bytes SHA256 and byte length; normalized relative
path; selected parser language; current schema epoch/DDL hash; full extraction
producer fingerprint; extraction-relevant configuration. Use existing _build_inputs
configuration (selected languages and skip patterns) conservatively as a whole;
unrelated option changes may cause extra misses, never false hits. No mtime/size,
inode or ambient time establishes content equivalence. Read/hash the actual buffer
once before lookup; parse the identical buffer on miss. Disabled/unsupported files
retain existing observations but are not extraction cache candidates.

Producer includes actual codegraph/query hashes, parser package versions and the
new indexing/extraction_cache.py hash alongside storage/evidence.py and
providers/python_imports.py. Metadata validation accepts the EXACT original module
set or EXACT extended set, preserving legacy producer interpretation. Current
cache keys/packets require the current complete extended fingerprint; an old
historical producer is never silently upgraded to current. The indexing __init__
module is inert; any later executable extraction logic there needs fingerprint
closure before adoption. Avoid schema epoch/DDL change: cache is separate storage.

Store under self.atlas_dir/extraction-cache/v1/<key-prefix>/<key>.json using the
existing S06 per-repository/worktree graph namespace. No source-root or memex
fallback. Path remains part of key because entity/scope identities are path-bound;
identical content at two paths must not transplant cached IDs. Renames get a new
packet for the new path. Symlink/source discovery behavior remains unchanged.

Only index/writer flow accesses or creates cache paths, under existing graph writer
lock. Serve/status/export/import/reader leases neither create nor mutate cache.
Temp files in the cache entry directory, private file permissions like existing
index artifacts, canonical JSON, flush/close then atomic replace. Interrupted temp
files are never entries; readers require exact final basename/key and complete
state. Concurrent index writers remain serialized by the existing graph lock.
No runtime download/compile, new dependency, daemon or separate lock hierarchy.

### Validation, failed entries and cache failure

Closed envelope fields: format, key, canonical inputs, state, packet or failure,
payload checksum. complete requires the entire packet; failed stores deterministic
reason/location without facts and is ALWAYS a miss on future indexing. Never
reuse failed/recovered syntax, retained historical facts or partial extraction as
a hit. Retrying unchanged failed bytes invokes the real parser again; recovery
replaces failed diagnostics and facts through the existing S07 path.

Lookup validates format/state/closed fields, recalculated key, exact current inputs,
payload checksum, packet schema and complete cross-record consistency BEFORE any
row insertion. Validate canonical IDs, source hash/size/span bounds, file path and
language, producer identity, entity/occurrence/evidence closure and raw projection
links. Binding tuples/import descriptions/rationale ownership must have correct
shape and reference only the packet's allowed records. Reject unknown fields,
missing lists/sidechannels, duplicate contradictory IDs, wrong path/source/producer,
resolved assertions masquerading as extraction facts and invalid reference links.
Do not treat recomputed outer checksum as proof of coherent payload.

This is integrity validation for an owner-local derived cache, NOT authenticity
against an adversary able to replace all cache contents and valid checksums with
fabricated yet coherent semantics. Such proof requires re-extraction/trusted signing
and is out of scope. No pickle/eval/import of cached data. Cache entry paths must
be derived from validated hash keys, not content-provided output paths.

Malformed/truncated/failed/incompatible/inconsistent/unreadable entries are misses
and reparse actual source. Cache write failures are operational diagnostics and
must not discard valid freshly extracted facts or fail an otherwise valid build.
Write a failed entry only when a deterministic file-level parse failure was
observed; never convert fatal read/parser/extraction/DB failure into successful
coverage. Fatal failure still aborts staged publication and keeps G1. A complete
per-file packet written before a later fatal build may remain reusable because its
extraction inputs were fully consumed/validated; it does not certify publication.

### Interaction with incremental reuse and observations

Preserve current compatible indexed-predecessor fast path. Report it separately
as predecessor_reused, not a disk-cache hit. If no compatible predecessor facts
exist (including full builds/new writer instance), lookup the persistent packet;
complete hit skips BOTH parser.parse and extraction Query execution. All actual
source hashes and indexed observations still come from this build's read buffer.
Failed predecessor paths cannot use predecessor skip or a failed cache state.
Complete historical packet for the exact now-current valid source may be reused
only if its current full producer/config/path key matches; then it represents
freshly reobserved bytes, not a stale history promotion.

Operational counters/reason counts in build return/logs: disk hit, miss, failed/
invalid/incompatible miss, write failure, predecessor reuse, parser invocation.
Specify unambiguous counters in tests; no elapsed-time performance assertion.
Do not persist counters/cache path/hit state into generation inputs, status IDs,
coverage records, evidence, canonical JSONL or portable artifacts. Cold/warm/forced
miss runs under the SAME producer/config/source namespace yield identical canonical
extraction facts, current graph, observation identity and portable JSONL bytes.
Known stale history remains predecessor-dependent as S07 already specifies.

## Stories

As an operator, I want repeated indexing to reuse valid extraction without letting
misleading timestamps, corrupt packets or changed producers alter graph truth.
One Vivi executor; at most three engineering days, stop/report remaining scope at
cap. No new speed threshold, future release claim or optimization of S07's measured
621.65MiB export/~1.98GB export RSS. Cost disclosure carries forward to S23.

1. Capture baseline and real red parser-count test on full repeated builds. Package
   extraction outputs and prove uncached packet insertion equals current extraction.
2. Implement canonical keys, closed complete/failed packets and atomic local cache;
   integrate under writer lock with strict miss/fatal distinctions.
3. Add corpus-shaped regression controls and current-producer compatibility; run
   focused/full/static/Docker/gate checks and receipt/reentry protocol. Tests may
   instrument real parser methods but never substitute mock trees/facts/results.

## Acceptance Criteria

Exact live issue bytes below; no additional invented performance threshold.
### AC-S09-001 (event-driven)
GIVEN source bytes change while size and mtime are preserved
WHEN incremental extraction runs
THEN Atlas SHALL extract the changed content.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_same_stat_changed_content

### AC-S09-002 (event-driven)
GIVEN the extraction query fingerprint changes
WHEN incremental extraction runs
THEN the old cached facts SHALL be invalidated.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_producer_change

### AC-S09-003 (event-driven)
GIVEN an extraction-relevant option changes
WHEN incremental extraction runs
THEN the affected file cache entry SHALL be invalidated.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_config_change

### AC-S09-004 (unwanted-behavior)
GIVEN a cache write was interrupted
WHEN the cache entry is read
THEN Atlas SHALL treat that entry as a cache miss.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_partial_cache_rejected

### AC-S09-005 (event-driven)
GIVEN all canonical extraction inputs are unchanged
WHEN extraction is repeated
THEN the canonical extracted facts SHALL equal the first run.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_warm_replay

### AC-S09-006 (event-driven)
GIVEN a complete valid cache entry exists
WHEN the same file is indexed again
THEN the parser invocation count for that file SHALL be zero.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_unchanged_no_parse

### AC-S09-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S09-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

All six issue-named tests live in tests/slices/test_s09_contract.py. Additional
cache validation cases may live in test_s09_cache_validation.py. Fixtures created
in tmp_path avoid changing unrelated shared fixtures.

- Same-size/same-mtime edit: actual parser runs on changed bytes and produces the
  new named fact; old cached result not reused. Query mutation and current producer/
  parser version fingerprint controls invalidate old entries. Selected-language/
  skip configuration change uses actual _build_inputs, no invented option.
- Cold/full/warm/new-writer instances in same source namespace: count underlying
  real parser.parse and actual _extract calls per file. Complete hits yield zero;
  canonical packets, graph/query results, observations and JSONL bytes equal.
  Force misses by removing only test-owned cache artifacts; no new public bypass.
- Rich fixtures cover parallel imports/calls, multiple occurrences, assignment
  shadowing, owners, same-line declarations, rationale and unsupported imports.
  Exact-content duplicate files and rename do not share path-bound identities.
- Partial temp write, truncated final JSON, failed state, wrong key/input hash,
  unsupported version, packet missing sidechannel, coherently rehashed wrong
  path/source/producer/link and resolved-output injection all miss/reparse. A valid
  control hits so “always miss” cannot pass. Validate fresh-source evidence before
  accepting restored packet, including ranges/closure and producer module hashes.
- Actual recovered syntax stays failed and retries parser on unchanged bytes;
  existing retained evidence remains stale until valid current extraction/reuse.
  Parser/read/extraction failures preserve G1; independent cache I/O failures
  degrade to fresh extraction, not partial publication. Warm/cold global binding
  after target edits/deletion still changes current results correctly.
- G1 held reader while warm G2 publishes, two actual index processes serialized,
  atomic interrupted write observed as miss. Use actual subprocess/filesystem
  barriers for atomicity claims; avoid testing only a hand-crafted boolean flag.
- External graph root + readonly source: all cache paths external, source digests
  unchanged, no writes from read-only query/status/export/import. Reuse existing
  real Docker :ro S06 evidence fixture behavior; if adding an existing test-path
  edit becomes necessary, amend explicitly before editing. No required skips.
- Producer extension admits old S07/epoch6 exact shapes, rejects arbitrary module
  names/missing current cache fingerprint, and source-free portable roundtrip
  remains valid. Keep integrity and strict import mutation tests unchanged.

Before product changes from mcp-server:
UV_CACHE_DIR=/tmp/aci-s01-uv-cache /tmp/aci-s01-tools/bin/uv sync --frozen
UV_CACHE_DIR=/tmp/aci-s01-uv-cache /tmp/aci-s01-tools/bin/uv run --frozen pytest tests/slices/test_s07_contract.py tests/slices/test_s07_parser_prerequisite.py -q
Record source/command/exit and new S09 red test before fix under .spectra/evidence/aci-s09/.
Then, from mcp-server with same uv/cache:
- uv run --frozen pytest tests/slices/test_s09_contract.py tests/slices/test_s09_cache_validation.py -q
- uv run --frozen pytest -q
- uv run --frozen ruff check .
- uv run --frozen mypy src
Run relevant Docker/readonly and macOS/Linux canonical exchange CI gates; root
supplies scoped Docker escalation when needed. Do not repeat broad checks absent
new changes or failures. Capture actual totals, not anticipated pass counts.

From repo root, verify existing current S07 behavioral probe:
PATH=/tmp/aci-s01-tools/bin:$PATH UV_CACHE_DIR=/tmp/aci-s01-uv-cache python3 scripts/verify-probe-verdict.py .spectra/changes/aci-s07-probe/aci-s07-probe-lpa-vs-louvain.json
bash scripts/test-verify-probe-verdict.sh
bash scripts/test-verify-export-size.sh
Expected behavioral fingerprint0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f
should remain unchanged: verifier hashes epoch/DDL/projected confident facts,
not operational stats or producer metadata. Measure it, do not assume. If changed,
stop and diagnose graph drift; never re-sign old probe evidence. Genuine intended
semantic change needs explicit replan/fresh measurements, not a cache shortcut.
Current raw export resource limitation is disclosed; historical size gate unchanged.

## Verification layers and independent checker

Call-site chain: read once -> key -> validated packet OR real parse/extract ->
shared insertion -> global binding -> observations -> atomic generation publication.
Three consistent decompositions: input equivalence/cache lifetime; full packet/
fact closure; read/write isolation/publication. All share exact buffer, path-bound
producer identity, no cached global binding and unchanged canonical outputs.
Adversarial controls cover every skip/hit/miss/fatal boundary, not only timing.
ATLAS independently reviews this full plan and runs real lint/EARS8/refine rubric;
maker ramza-backlog-order-s09 cannot self-check. Required critic identity and exact
scores/timestamp recorded before Assemble. No fake human review or AUTO verdict.

## Receipt and backlog continuity

Ledger now records actual PR71 merge dd88912, seven merged/35 remaining; current31,
next32; frozen queue unchanged. All later slice identities remain unverified until
pickup. User standing authorization covers validated PR/merge/main sync/continuation;
root alone owns publication. No paid evaluation, release or deployment implied.

Before code initialize .spectra/state/aci-s09.execution.json IN_PROGRESS with
ACI-S09/issue31/criteria digest/base/declared paths, output_commit null and eight
AC result slots. Preserve interrupted snapshot and compare resumed identity RUN02.
Adapt existing .spectra/evidence/aci-s07/verify_execution.py and verify_resume.py
into NEW S09 evidence paths with exact six product/eight total ACs; remove S07-only
supporting-plan clauses rather than create another runner framework. Do not alter
prior scripts/receipts. After checks, create bounded local implementation commit;
manifest tracked implementation inputs, output SHA and per-AC commands/results.
Clean-checkout reentry must verify output_commit/digests/git diff/no new writes
(RUN01). Use later metadata commit to avoid circular receipt hashing. Output must
be reviewable; incomplete/timebox failure stays IN_PROGRESS/BLOCKED with evidence.

## Confidence

Not yet computed. Actual independent review and remaining assumptions determine
Assemble score. Full lint/EARS validates plan shape, not implementation.

## Rejected Alternatives

Actual Explore scores: atomic per-file JSON packet87.5 selected; dedicated SQLite
cache82 adds another DB lifecycle/transaction surface; staged-generation-table
reuse74 couples cache completeness to graph mutation/history and does not provide
an independent persistent extraction packet. No remote cache, AST serialization,
mtime shortcut, resolved-edge cache or parser-error-as-success shortcut.

## Risks

| Risk | Required control |
| --- | --- |
| Mutable sidechannel omitted on hit | One complete typed packet and rich cold/warm exact equivalence. |
| Locally corrupt facts bypass parser | Closed packet/input/ID/span/producer validation; all invalid states miss. |
| Cache key omits extraction input | Full actual producer/module/config/schema/path/content key; change controls. |
| Failed/stale cache poisons current graph | Failed entries never hit; global binding always recomputed; S07 invariants. |
| Cache I/O damages usable generation | Optional cache failures fall back; fatal extraction/publication still atomic. |
| Disk/RSS grows despite faster parser reuse | No undocumented eviction/performance promise; record diagnostics and measured cost. |

## Assemble handoff

Canonical helpers/schemas: original checkout .eidolons/ramza/bin and schemas.
Finish actual critic/confidence/scope/freeze/drift/emission/full schemas+hash checks.
Envelope artifact/criteria paths relative to envelope directory. Root mechanical
verify-envelope trace precedes Vivi. Preserve 76 preflight product hashes during
planning. Stop planning writes at READY; one product writer thereafter.
