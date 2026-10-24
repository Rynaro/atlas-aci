---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T19:48:06Z
slice_id: ACI-S08
supporting_plan: aci-s08-lease-footprint
---
# ACI-S08 supporting refinement — compact source proofs

## Scope

This supports the existing ACI-S08 execution, not a new slice or receipt. Original
plan260694c5, criteria216f05f3, DONE state9a803f14 and enveloped184c0b8 remain immutable.
Exact original hashes and held product hashes are in this supporting preflight.
Baseline merged commit fa75bec971502b72ce85f9e87ab7ec2876274ce9; working product is
intentionally uncommitted S08 implementation, held codegraph7565e53d/snapshotsbd77fa8c.
Do not reset the original state or change the eight original acceptance criteria.

Root authorized bounded internal proof-footprint correction before merge. This is
a measured usability/cost risk, not an invented failed performance criterion.
Scope: storage/snapshots.py, codegraph.py, tests/slices/test_s08_*.py and supporting
metadata only. No portable JSONL/SQL schema/epoch, source inputs/default languages,
query semantics, graph evidence, source byte transformations, thresholds, new
dependencies, server/tool public schemas or cross-request trust cache changes.
Record this supporting amendment before product edits. If an additional path is
actually necessary, stop for a narrow recorded scope decision.

Actual rightsize FULL5 (8 estimated files, migration/security/new internal proof
representation, medium stakes); complexity9(2,2,2,3). Standard role/model routing,
one existing Vivi allocation; maker ramza-backlog-order-s08-footprint and distinct
ATLAS checker. Local patterns suffice; no external memory retry or paid evaluation.

## Approach

### Measured baseline and actual source path

Solidus: pack6,317,973B/2474 blobs, manifest89,981,511B/306720 typed ranges/2519
bindings; whole accounting12.373038s, peak RSS2,212,495,360B. Spree: pack8,566,123B/
2355 blobs, manifest145,713,489B/491458 ranges/2358 bindings; accounting18.758525s,
peak RSS2,793,046,016B. These are WHOLE account.py process figures, including failed
support checks, inventory and one extra digest(manifest), not isolated request
latency or retained memory. All baseline jobs passed; modularity verifier PASS.
Original raw measurements and immutable report remain under evidence/aci-s08/probe
and changes/aci-s08/probe. No baseline resignal or historical-artifact overwrite.

Actual ordinary request path: server400 reader_lease -> _generation_data validates
observations with list(ev.records); _snapshot_binding1119 calls list(ev.records)
plus observation validation again; snapshots.closure63 copies/deduplicates/sorts
all typed records and creates each range dictionary; acquire388 reads/parses/
recanonicalizes the entire manifest and compares the full range vector. The tool's
epoch_ok then invokes generation validation again. Source-only view skips the DB
closure but still parses the huge manifest. This work scales with proof records,
although the source catalog has only a few thousand entries.

First execution checkpoint, BEFORE changes: finish/audit the held-source baseline,
then measure fresh-process isolated reader_lease enter/exit and one ordinary
bounded MCP graph query on each unchanged indexed corpus. Also measure source-only
token acquisition. Record actual source/index IDs, command, time and process peak
RSS, clearly separating these from whole accounting. Record a repeated warm run
without pretending it is an uncached filesystem benchmark. These small readonly
measurements count against the same cumulative probe envelope; no background
indexing or source edits during a measured job.

### Compact closed manifest, not lost evidence

Write internal atlas-source-snapshot.v2. Keep current/context, graph_digest,
observation_digest and pack index semantics. Replace the persisted per-occurrence/
evidence ranges list with a CLOSED range_proof commitment: version/algorithm,
exact total count, per-kind and legacy counts, and SHA256 of the canonical ordered
range sequence. The committed range row retains ALL old fields:
id,kind,path,sha256,size,start,end,provenance. Deterministic order remains (id,kind),
duplicate IDs/conflicting payloads still reject. The stream is domain/version
specified, unambiguous and tested against an independent materialized reference;
do not hash concatenated variable strings or accept unordered multiset hashes.

Bindings become a closed per-(path,source_hash) catalog with available flag,
declared source size, range count and minimum start/maximum end (explicit zero/
empty convention for no typed ranges). Current failed/disabled/unsupported files
still have current consumed hashes and actual blob size even with zero ranges.
Known historical unavailable bytes retain known hash/size; legacy unavailable
ranges retain null hash/size/offset semantics and are counted/committed without
fabricated blob bindings. Every available catalog entry must exactly match pack
length; every range's size agrees with its source group, and every start/end is
validated individually before aggregation. Aggregate max_end is never a substitute
for checking each range. Per-source catalog must equal current plus actual
graph-authorized historical sources, without extra, omitted or foreign bindings.

All typed graph/evidence/projection data remains in SQLite and unchanged portable
JSONL. Full graph_digest keeps the EXACT old six typed-record families plus non-projection
retained records; observation_digest retains its exact existing observation/history
semantics. Projection coherence remains enforced by the existing separate build/
import validators; preserve these checks and their corruption tests. Do not claim
the old graph_digest separately authenticated raw projection rows. Neither digest
is narrowed to just ranges or successful files. The compact
manifest changes internal snapshot/generation IDs legitimately with its format/
producer; identical new construction must still replay deterministically.

A graph reader independently recomputes this complete proof/catalog from its OWN
opened SQLite connection and validated history before exposure, then compares every
closed commitment/catalog field with the manifest. Never trust a stored DB digest,
filename, generation ID or manifest claim alone. Rehashed foreign/missing bindings,
invalid ranges with forged metadata, and current/history contradictions still fail.

A source-only token lease authenticates the manifest ID, closed catalog/pack
structure, per-blob bytes and requested path/hash as before. It cannot authenticate
the meaning of a graph it has not opened; no new authenticity claim is introduced.
The range commitment is validated in conjunction with actual graph records for a
graph-backed lease, not treated as a self-verifying proof. Original source-only
access did not verify graph_digest against an opened graph either.

### Streaming own-graph proof and one-lease validation

ev.records already emits typed records ordered by type/id. Replace whole graph
list/copy/sort/canonical-string constructions with deterministic cursor/iterator
passes. Merge active and retained-history streams with exact same-ID deduplication;
conflicting same IDs, including cross-kind conflicts, must reject. Preserve history
identity/provenance; do not discard old records to simplify a digest. Small identity
bookkeeping or existing history objects may remain; do not claim constant memory
for arbitrary history or hide a whole-record materialization behind a helper.

Compute the full graph digest incrementally over the EXACT previous canonical
JSON-array bytes (brackets, separators, canonical row encoding), checked against
the materialized oracle. Range proof uses its explicitly specified canonical stream
and independently tested ordering. Use ordered occurrence/evidence cursor passes
and actual linked occurrence paths for evidence; bounded joins/lookups must preserve
link/provenance checks and not trust an unchecked projection column. Every record
participates; no truncation, sampling, timing cutoff or only-current shortcut.

Refactor _observation_records active consistency checks to stream relevant typed
rows instead of retaining all six record families. Preserve BOTH directions:
indexed observations require actual file evidence; active occurrences/evidence
require matching indexed observations, exact language/hash and valid history.
Reuse validated observations within the SAME acquisition to avoid doing this twice.
Do not broaden this into S07/S10 resolver/portable-projection redesign.

After all graph/context/manifest/pack validations succeed, mark only that immutable
owned reader as validated. epoch_ok within that live lease can use the successful
validation result. Close/retry/error clears it. A factory, another connection, new
request or newly published generation must validate again. No process-global
generation/path/inode cache and no "trusted last validation" across requests.
The S03 immutable-publisher assumption is unchanged; no claim of defending an
uncooperative in-place writer by polling after every statement.

Pack bytes/hash/header/index and each distinct available blob remain verified
before exposure. Avoid rehashing identical shared content once per path: validate
each unique pack-index blob once, while independently verifying all path bindings.
read_source still authenticates requested bytes/hash. Existing FD lifetime,
safe opens, pruning, bounded retry, controlled cancellation and RO safety remain.

### Version compatibility and corruption matrix

Read existing internal v1 manifests without rewriting them or changing their IDs.
The v1 adapter validates its original full ranges and normalizes them to the same
closed comparison model; it may retain its old larger parsing cost. New builds
write v2. Never silently reinterpret a v1 digest using v2 framing. Original
generation v1/v2/v3 graph-only handling and v4's CLOSED import-unavailable variant
remain unchanged. A native v4 missing source manifest still fails closed.

Existing range-corruption tests must survive meaningfully: retain v1 mutation
controls; add v2 commitment/count/catalog/bounds corruption plus actual malformed
SQLite occurrence/evidence ranges. Include coherently rehashed manifest and graph
pointer attacks; changing a summary/digest cannot conceal foreign/missing sources
or an invalid underlying range. A later request must detect corruption even after
an earlier request validated the same nominal snapshot. Normal close must not
close another live lease. No deleting hard assertions because a field moved. Explicit negative controls
include coherently rehashed canonical/source bindings AND forged portable projection
rows/links with valid outer export hashes: existing strict build/import validation
must still reject before publication and preserve G1. Preserve every existing
read-path projection/observation check; a compact proof cannot bypass it. Do not
invent a claim that the old source graph_digest alone verified projection semantics.

Storage helper already belongs to the closed producer module set; content hashes
must update normally. No new module allowlist or schema relaxation. Old producer
evidence, source-free import, cache/rebind semantics, capabilities and canonical
portable ordering/content-hash/roundtrip remain unchanged. New producer-dependent
IDs can differ; do not suppress provenance to force cross-producer byte equality.

## Stories

One bounded supporting effort within the ORIGINAL5 engineering days/144000s,
using the smaller of remaining allocation and1 engineering day/28800s. Reported
charged at pickup3707.89s is a checkpoint, not a reset; include subsequent real
work/waits/IO under the existing accounting policy.
- Story1 (0.25d,P0): isolate actual costs, establish materialized proof oracle and
  version/corruption fixtures; output exact baseline and executable red controls.
- Story2 (0.5d,P0): compact manifest + streamed closure/observations and one-lease
  reuse, preserving all fields and v1 adapter; output strict fixture evidence.
- Story3 (0.25d,P0): integrated RO/prune/cancel/history/replay checks, actual paired
  costs and full/static/probe gates; independent ATLAS review and receipt update.
Stop if this requires a generic proof platform, portable/DDL change or weakening
validation. No unobserved speedup claim; if costs do not improve materially,
report the evidence and reconsider before broadening the correction.

## Acceptance Criteria

The six exact supporting EARS blocks in aci-s08-lease-footprint.criteria.md derive
from and supplement the original eight S08 obligations. They do not create a new
execution identity or waive any original check. Test mapping:
FP001: independent materialized vs streaming graph/range commitments across empty,
duplicate-identical, conflicting IDs, active/history overlaps and randomized
insertion order; exact fields/order, not just counts.
FP002: all v1 controls plus v2 compact proof/count/source-size/foreign binding/
current-history/invalid raw range controls, rejected before exposure.
FP003: v1/v2 real pack/graph lifecycle, worktree/overlay/history/source-free import,
same-ID replay, RO host↔Docker prune, multiple leases and finally cleanup.
FP004: instrument real validation calls: one successful validation per live lease,
fresh validation on each new request; corruption between requests is rejected.
FP005: actual isolated before/after metrics on both exact pinned inputs, separately
labeled aggregate accounting; file/range/source counts and manifest/pack bytes.
FP006: original criteria/receipt identity, all required tests/caps and old evidence.

From mcp-server, using existing frozen uv/SDK/cache:
uv run --frozen pytest tests/slices/test_s08_lease_footprint.py tests/slices/test_s08_contract.py tests/slices/test_s08_integration.py -q
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src
Run actual existing S08/S06/Rust offline Docker proof, bounds, old-producer/cache/
binding/portable tests and original unchanged probe/export-size verifiers. All
required gates need actual results; no skips or a prior approved producer reused
as if it validates this changed source.

Baseline probe22 jobs consumed486.645136166s (all0,max211.063s), reported by Vivi.
Audit final baseline job records and add ALL further size/isolation jobs, then
carry that exact charge into NEW refinement measurement directories. Every job
<=600s and total cumulative probe work<=2700s; use remaining time, never start a
fresh2700 budget or overwrite old logs. Preserve old successful measurements as
baseline, not a failed probe. Held baseline completes before source mutation.

Repeat actual current-producer indexing/accounting and required verifier against
unchanged Solidus4026945d614e81383c007ed1ab1278a0195ce5d9 and
Spree6699cde44303ea85ef6e56c5e87c44a738ab73fc, same recorded profile/inventory.
No input narrowing, stale producer re-signing or inferred current result.
Store bulky evidence losslessly and retain originals until independent audit.
Clean only explicitly inventoried task temporary DB/CAS/exports after audits;
preserve compact evidence, seed checkouts, SDK assets and active leases.

## Verification and independent review

Three views: typed records→canonical stream→closed commitment; current/history
source catalog→pack ranges→authenticated bytes; per-request validation→one live
lease→publication/prune/release. Shared invariants are exact proof participation,
same opened generation, no RO writes and deterministic replay. Structural/EARS,
budget/path coverage and adversarial controls precede distinct ATLAS full critique.
The reviewer must verify that compact commitments do not substitute for validating
actual graph ranges and that the standalone source-token trust boundary is honest.

## Scope amendment and execution handoff

Only:
mcp-server/src/atlas_aci/storage/snapshots.py
mcp-server/src/atlas_aci/codegraph.py
mcp-server/tests/slices/test_s08_*.py
.spectra/plans/aci-s08-lease-footprint.*
.spectra/plans/ramza-calibration.jsonl
.spectra/changes/aci-s08/lease-footprint/*
.spectra/evidence/aci-s08/lease-footprint/*
The executor may append the supporting-plan pointer/results to the ORIGINAL
.spectra/state/aci-s08.execution.json under existing S08 execution authority;
RAMZA does not edit it. Existing exact six receipt/post-output exclusions remain,
and supporting stable evidence is hashed into the same eventual output receipt.
No new slice/issue/branch, no original plan/state/envelope rewrite. Root owns
mechanical ECL/correlated trace, then one Vivi writer, independent verification,
publication/green CI/merge/sync under standing user authorization.

## Confidence

Computed only at Assemble after real independent review. The measured duplication
and chosen representation are concrete; implementation of the streaming merge,
historical dedup and strict v1/v2 adapter remains a risk, not an observed success.
Honor the actual computed verdict, with no inferred approval or target score.

## Rejected Alternatives

Actual explore scores: retain v1+stream graph only78.5/solid (lower risk but leaves
90–146MB manifest parsing on source-only reads); compact closed commitment/catalog
plus streaming87/elite selected; persistent Merkle/query-proof platform66/weak
rejected for architecture/schema scope and timebox. Cross-request memoization,
dropping ranges, sampling records or trusting stored roots alone weaken integrity.
Portable interning/compression is a different contract and is not authorized here.

## Risks

- Stream framing/order/dedup changes identity: independent materialized oracle,
  exact history/current conflicts and canonical replay tests.
- Summary-only validation misses an invalid range: inspect every actual graph range
  before aggregation; corruption controls must fail at acquisition.
- Compact source-only token is overstated: bind requested bytes/path/hash, make no
  independent graph authenticity claim without the opened graph.
- Per-lease reuse leaks across requests: owned-reader-only lifetime and fresh-request
  corruption detection, no global cache.
- Cost shifts elsewhere: isolate real requests, record whole-accounting separately,
  report manifest and pack sizes without invented performance thresholds.
- Refinement expands: one-day supporting cap within original5d and cumulative probe
  caps; preserve baseline and return a precise gap rather than broaden silently.
