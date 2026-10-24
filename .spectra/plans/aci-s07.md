---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-24T01:47:59Z
slice_id: ACI-S07
issue_url: https://github.com/Rynaro/atlas-aci/issues/29
---

# ACI-S07 — Lossless directed evidence with portable stable identities

## Scope

Intent CHANGE. Add versioned entity, occurrence, evidence and directed assertion
records behind existing graph queries. Preserve typed parallel assertions,
provenance and old usable generations; keep old JSONL readable.
Base ad9ab7a6122f653b0e9e7052e6173c6257b4a3b8 after merged PR70.
Worktree /tmp/atlas-aci-aci-s07; branch aci-s07. Live ACI-S07 marker and exact
criteria digest verified; no prior S07 state/receipt. Actual S02/S03/S04/S06
interfaces and COMPLETE receipts exist on this base; no predecessor gap found.

Declared issue paths: codegraph.py, storage/*, providers/* under
mcp-server/src/atlas_aci; tests/slices/test_s07_*.py; atlas/docs/*; .spectra metadata.
Use at most small focused storage/evidence.py and providers/python_imports.py
modules (package initializers as needed), not a provider framework or tree rewrite.
Narrow amendments BEFORE implementation:
- mcp-server/tests/fixtures/s07/*: tiny real epoch6 parser-produced DB/JSONL/source fixture
  with generation provenance; avoid tests that fabricate old facts by hand.
- mcp-server/tests/test_schema_epoch.py, test_export.py and
  test_cli_export_import.py: epoch6 compatibility, strict new exchange and
  source-free import assertions replace explicitly obsolete epoch/schema checks.
- .github/workflows/harden-gate.yml: select distinct fresh S07 probe basename.
- scripts/test-verify-probe-verdict.sh: update current-artifact selection only
  where pinned to S02; retain mutation/forgery/strict-verifier controls.
No verifier weakening, old probe edits, blanket expected-count changes or other
test paths preauthorized. Justify any further exact path before editing.

No external graph service, probabilistic confidence, whole-program analysis,
provider abstraction, new language grammar, content-addressed source store, or
navigation UI/MCP surface expansion. Current graph-query shape/caps, S02 binding
guards, S03 leases/staging, S04 unknown/stale coverage and S06 root isolation hold.
User's later standing authorization permits root to validate/merge/sync/continue;
executor does not self-merge. No paid model/evaluation or deployment/credential work.

Actual rightsize full6: >=10 files, public data API, migration, import/storage
integrity boundary, medium stakes. Complexity9 (3,2,2,2), extended; standard
routing, not TRANCE solely because issue risk is P0. Independent checker required.
DISCOVER/CLARIFY omitted: outcome/contracts explicit.

## Approach

### Existing seams and compatibility model

Epoch6 DDL hash is 52f933ea7a2a38074fb436d473b2caedb1e8b439584483077896e0e92ea622c3.
It stores row-ID/line based symbols, refs with S02 bindings/unresolved reasons,
directed edges and rationale. Export omits refs/bindings/source provenance.
Extraction has the exact consumed bytes and AST byte spans; use them there.
Do not turn binding offsets or display line ranges into stable entity identity.

Bump SCHEMA_EPOCH to7 and recompute EXPECTED_DDL_HASH. Add canonical tables while
retaining existing symbols/refs/edges/rationale as compatibility projections.
Existing call/query/community behavior consumes projections; canonical assertions
are never reduced to undirected edges or one row per endpoint pair.
Build projection and canonical records in the SAME staged transaction. Provide
a deterministic CodeGraph evidence-record read API for verification/downstream
use; no new MCP tool required. Communities stays an explicitly filtered,
undirected analysis projection, never the persisted evidence model.

### Canonical record contract (atlas-evidence.v1)

Use canonical sorted-key JSON + UTF-8 + SHA-256 with distinct versioned domain
prefixes. IDs exclude SQLite rowids, absolute source/storage paths, timestamps,
publication tokens, insertion order and implicit hash-map ordering.

| Record | Required meaning and key |
|---|---|
| entity | ID hashes language, normalized repo-relative path, semantic kind, qualified structural owner chain, name and deterministic duplicate discriminator. |
| occurrence | Links entity or raw reference to source location; ID hashes occurrence kind, path/language, consumed source hash, exact half-open byte span and syntactic role. |
| evidence | One provenance record links occurrence, producer identity and source hash/span. ID hashes the complete canonical provenance record. |
| assertion | Directed source entity -> target entity (nullable when unresolved), relation, evidence ID, resolution state/candidate IDs and preserved raw target spelling. ID hashes all assertion semantics; relation/evidence/direction are mandatory key inputs. |

Entity keys for declarations derive structural owner chains from AST ancestry,
including named owner kind/name. Disambiguate same-name/kind siblings using their
ordinal among matching declaration/scope siblings in deterministic AST order,
ignoring whitespace/comments. Anonymous relevant lexical owners use structural
kind and same-kind sibling ordinal; never byte/line offsets or content hashes.
Blank-line-only changes cannot affect these ordinals. Inserting/reordering
otherwise indistinguishable duplicate declarations MAY change their IDs; document
that bounded guarantee rather than claiming arbitrary edit identity tracking.
File/module-level sources have explicit file entities. Same-name owners/languages
and same-line declarations must stay distinct. No smallest-line-range owner guess.

Occurrences/evidence intentionally change when source bytes or spans change,
while the entity ID survives the specified line move. Capture start/end BYTE
offsets against the exact buffer supplied to the parser; line/column are derived
display metadata. Require 0 <= start <= end <= consumed buffer length at build.
Reference evidence uses its actual reference node span, not the target declaration;
definition and rationale evidence use their own AST/comment spans. Producer record
identifies parser/package versions, query hash and code/module fingerprints.
Imported evidence preserves the ORIGINAL producer; local importer provenance
belongs to generation metadata, not rewritten evidence.

Unknown/ambiguous targets retain null concrete target, raw reference/candidates
and stable unresolved reason. Never fabricate an entity resolution from a name.
All current relation types, rationale links and unresolved refs survive canonical
representation/export. Distinct sites or producers yield distinct evidence and
assertions; identical canonical assertions may deduplicate by exact ID only.
A->B and B->A, call and import, two calls on one line, and two evidence records
for one endpoint/relation remain independently addressable.

### Bounded real import producer needed for parallel relations

Add only Python package-local unaliased relative imports, e.g.
pkg/a.py: def A(): from .b import B; B()
pkg/b.py: def B(): ...
with actual package markers and one uniquely identified in-repo module/target.
Extract the import node and invocation with real Tree-sitter nodes; A->B gets
separate import and call assertions with their own spans/evidence.
Validate exact relative level/module path, package membership and unique named
target. No global callee-name matching or cwd/sys.path-dependent absolute imports.
Reject ambiguous file/package collisions, aliases/star/dynamic/external imports
and missing modules as unresolved with a stable reason. Existing S02 shadowing,
lexical visibility, receiver ownership and language filters still run; imports
must not bypass a local assignment/parameter/nearer binding. A known import
binding is available only in its actual lexical scope and declaration order
where the language requires it. No generic Python semantic binding claim.

Update capability declarations to name this bounded import subset and limitations;
all producers remain structural, not compiler semantic providers. Do not imply
other languages gained import support. Existing unsupported syntax stays visible.

### Generation and producer provenance

New epoch7 generations use explicitly versioned atlas-generation.v2 metadata:
preserve S03 source/config/repository identity semantics and include hashes of
each new graph-affecting helper/provider module in producer fingerprints.
Validators retain exact atlas-generation.v1 support ONLY for known epoch6
compatibility; never apply current producer hashes to older evidence.
Imports remain source-free: canonical imported evidence may retain trustworthy
artifact-carried source hashes, while destination source completeness remains
unavailable. No destination source rescan or new claim that those bytes exist.
Legacy-conversion generation kind explicitly records old generation/artifact
identity plus current converter; do not label unavailable old evidence fresh.

### Epoch6 read adapter and staged forward migration

Read-only selection prefers a valid published epoch7 file. If epoch7 is absent,
open the known epoch6 graph read-only, validate ITS epoch6 DDL/generation metadata
and expose its compatible query projections through that pinned connection.
Do not create dirs/locks, run DDL, sweep, migrate or write when reading.
A present malformed epoch7 is INDEX_UNAVAILABLE, not a silent stale fallback.
Unrecognized epochs remain unavailable; compatibility is specific to epoch6.

Writer-only forward conversion runs under the EXISTING namespace .index.lock.
Copy/convert epoch6 into a same-directory epoch7 staging DB; validate all canonical
records and query projections before atomic publication. Keep graph.6.db intact
before, during and after this slice's conversion: no eager epoch sweep of the
last usable legacy generation. Existing pinned G1 handles remain valid; new
acquisition sees G1 until G2 is published, then G2. Failure/interruption deletes
only owned staging files and preserves G1. No WAL or in-place ALTER mutation.

Provide a bounded writer method for source-free legacy conversion, reused by the
index staging path when legacy state is selected; no new migration service/CLI.
Index refresh must not publish an intermediate converted generation and then
fail parsing: conversion plus requested refresh completes in one publication.
Do not inherit legacy rows as newly parsed evidence; normal new-producer indexing
reparses to obtain exact spans. Source-free conversion preserves legacy query
facts while evidence fields absent from epoch6 are null with an explicit
legacy_unavailable reason. Known source hash, when genuinely present in the
old validated build manifest, stays known; missing exact byte span stays unknown.
Never reread current source to invent provenance for old retained facts.

Legacy entity keys are deterministic from available stored semantic fields and
duplicate ordinal, with identity_basis=legacy_projection when owner detail is
unavailable. Do not fabricate lexical owner facts from overlapping line ranges.
A later real parse may replace such provisional identities; document that this
does not assert old-to-new identity continuity absent evidence.
Legacy JSONL lacks refs/binding/source data: preserve what it contains and mark
coverage/evidence unavailable; do not claim missing unresolved refs were zero.

### Versioned exchange and import validation

New default JSONL has explicit format atlas-evidence.v1 and epoch7, canonical
record-type/ID order, body hash and exact record count. Include entity,
occurrence, producer, evidence and assertion records plus the legacy projection
payload needed for existing query compatibility (symbols/refs/edges/rationale/
assignments without rowids). Define strict closed per-record fields and stable
ordering in storage/evidence.py and atlas/docs/evidence-graph.md BEFORE wiring.
Projection rows reference their canonical IDs; validate coherence on import
rather than accepting contradictory parallel truth. Unresolved and legacy-
unavailable data has explicit discriminants; no omissions disguised as empty.

Retain an explicit reader for the existing epoch6 header/body (edge/rationale/
symbol). Validate its original count/hash/path/shape first, then stage conversion
to current canonical records with unavailable provenance. Unsupported future
versions fail clearly. Current-format exports round-trip every assertion and
provenance record unchanged; do not hash the imported artifact into each assertion
or rewrite evidence producer IDs on reimport.

Before publication validate hashes, ID recomputation, enum/schema versions,
all cross-record links/candidate IDs, path containment, numeric spans and
ordering, no booleans masquerading as integer offsets, no duplicate conflicting
IDs, and consistency of each evidence source hash/span/occurrence.
Artifacts without source bytes can establish internal consistency/declared source
size, not independent source-byte authenticity; retain that trust boundary.
Truncated/malformed/evidence-tampered artifacts leave the old graph usable.
Repeated imports rebuild canonical content and compare the FULL provenance set,
not only old edge counts.

### Execution order

Capture actual baseline and real-parser counterexamples before fixes. Generate
tiny epoch6 DB+JSONL fixtures using this pinned base's actual indexer and archive
their source inputs/producer/base/hash provenance. During test fixture copy,
reanchor only the destination repository manifest field and recalculate its
generation ID using the legacy canonical algorithm; preserve source hashes/facts,
and explicitly record this fixture adaptation (not a fresh source verification).

Implement pure record/key validation, then extraction/provenance and bounded
imports, then canonical persistence/projections, epoch6 read/staged conversion,
strict exchange, tests/docs and fresh probe. Preserve root/lease/coverage behavior.
Do not migrate every caller or build a general resolver to satisfy six criteria.

## Stories

As an operator, I need independent typed directed assertions and their source
evidence to survive indexing, migration and exchange without unstable entity IDs.
One strong Vivi executor; five engineering days; P0 risk with standard routing.
At limit report exact residual gaps. Root controls review/green CI/merge/main
sync and next issue under standing authorization; no additional paid agents.

## Acceptance Criteria

Exact issue criteria at .spectra/plans/aci-s07.criteria.md, SHA-256
a5a08415f0dcf276a270cea3cbd1daf29e0b84332c791f7ff394fec0c6b92036.

### AC-S07-001 (event-driven)
GIVEN A imports B and A calls B
WHEN both facts are persisted
THEN the graph SHALL retain both typed relationships.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_parallel_relations

### AC-S07-002 (event-driven)
GIVEN a directed call A to B is exported and imported
WHEN the imported graph is queried
THEN the call direction SHALL remain A to B.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_direction_preserved

### AC-S07-003 (event-driven)
GIVEN only blank lines are inserted before a declaration
WHEN the file is reindexed
THEN the entity identity SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_line_move_identity

### AC-S07-004 (event-driven)
GIVEN a source-backed assertion is stored
WHEN its evidence is read
THEN the evidence SHALL identify the source content hash and span as one provenance record.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_source_evidence

### AC-S07-005 (unwanted-behavior)
GIVEN migration stops before publication
WHEN a legacy query runs
THEN the previous usable generation SHALL remain available.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_migration_interruption

### AC-S07-006 (event-driven)
GIVEN the same canonical assertions are imported twice
WHEN the second import completes
THEN the full canonical assertion set including provenance SHALL equal the first import result.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_assertion_replay

### AC-S07-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S07-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

- test_parallel_relations: actual package parser -> index -> canonical query ->
  export/import; A imports and calls B and both typed assertions survive.
  Include two call sites, same-line calls, reverse direction and multiple
  producer evidence. Unsupported/aliased imports remain unresolved.
- test_direction_preserved: actual B->A negative/reverse control; no endpoint
  sorting, undirected normalization or relation/evidence overwrite.
- test_line_move_identity: record parsed entity IDs, insert only blank lines,
  reindex and prove same IDs plus changed occurrence/hash/span. Include duplicate
  siblings, same-line declarations, same names in owners/languages and nested scopes.
- test_source_evidence: compare record SHA to exact indexed buffer bytes and
  half-open AST span; mutating current source after indexing must not rewrite
  retained evidence. Test refs and rationale as well as declarations.
- test_migration_interruption: actual pinned epoch6 DB, active real RO G1 lease,
  deterministic barrier in conversion before os.replace, injected interruption,
  production query remains G1 and graph.6 bytes unchanged. Successful conversion
  yields valid G2/new readers while old lease stays G1. Repeat at external root.
- test_assertion_replay: import same full artifact twice and compare canonical
  entity/occurrence/producer/evidence/assertion sets and deterministic export
  bytes; include legacy JSONL adapter and source-free destination.

Other required controls: exact current and legacy schema hash validation;
malformed IDs/hash/spans/links/projection contradictions; missing provenance stays
unknown; old incompatible epochs remain unavailable; RO paths create no files;
namespace isolation; S04 status on migration/failure/import; all S02 owner/scope
regressions and S03 lease/failure/producer identity tests. Apply any fixture
amendment narrowly, never weaken source-backed checks into handwritten rows.

Commands from mcp-server using /tmp/aci-s01-tools/bin/uv and
UV_CACHE_DIR=/tmp/aci-s01-uv-cache:
```sh
uv run --frozen pytest tests/slices/test_s07_contract.py -q
uv run --frozen pytest tests/slices/test_s02_contract.py tests/slices/test_s03_contract.py tests/slices/test_s04_contract.py tests/slices/test_s06_contract.py -q
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
Rebuild production Docker image and rerun S06 real :ro suite with
ATLAS_ACI_S06_DOCKER_IMAGE set; no required read-only integration skips. Preserve
S06 Linux UID10001 /graphs:ro snapshot method and 0600 metadata invariants.
CI macOS/Linux canonical export checks must pass with the new format.

### Fresh local CPU probe required by epoch/DDL change

S02 measurements are immutable. New artifacts:
.spectra/changes/aci-s07-probe/aci-s07-probe-lpa-vs-louvain.{md,json}
and probe-graphs.json.gz, with raw evidence under .spectra/evidence/aci-s07/probe/.
Switch only current artifact basenames in harden-gate/self-test. Keep historical
verification pinned to its historical producer; do not change old fingerprints.

Use disposable copies of cached public inputs, preserving pins:
solidus https://github.com/solidusio/solidus.git at
4026945d614e81383c007ed1ab1278a0195ce5d9 (full tree);
spree https://github.com/spree/spree.git at
6699cde44303ea85ef6e56c5e87c44a738ab73fc (same spree/* sparse checkout).
Cache /tmp/aci-s02-probe-inputs is available; use new /tmp/aci-s07-probe-inputs
copies/checkouts so prior inputs/evidence are not repurposed. Record exact HEAD,
clean tracked tree, source/module hashes and environment. No repository tests
or project hooks executed. Serial jobs: 600s/job,2700s total, measured peak RSS;
timeout/failure is a blocker, not PASS. No paid model calls or project dependency
changes. Networkx3.6.1 stays in isolated --no-project ephemeral environment.

Actual existing producer commands for each name:
```sh
# from mcp-server
uv run --frozen python ../scripts/probe-export-confident-graph.py /tmp/aci-s07-probe-inputs/<name> /tmp/aci-s07-<name>-graph.json
# from worktree root, capture stdout to new evidence file
uv run --no-project --with networkx==3.6.1 python scripts/probe-modularity.py /tmp/aci-s07-<name>-graph.json
# combine both fresh graph and modularity JSON outputs
python scripts/probe-assemble-graph-bundle.py --out .spectra/changes/aci-s07-probe/probe-graphs.json.gz --repo solidus:<graph-json>:<modularity-json> --repo spree:<graph-json>:<modularity-json>
python scripts/verify-probe-verdict.py .spectra/changes/aci-s07-probe/aci-s07-probe-lpa-vs-louvain.json
```
Generate sidecar from actual new outputs using existing schema: fixed bar
q_struct=.30,r=.85,k10,seeds0..9,resolution1; two exact pinned repos; measured
node/edge counts,LPA Q/community count,all10 Louvain seed Qs,current computed
indexer fingerprint,bundle filename/SHA. Record genuine PASS/CUT. Required PASS
must follow unchanged verifier's recomputation from fresh graph+partitions;
CUT cannot count as readiness. Store bulky raw JSON losslessly gzipped as S02 did.
Measure the current-format export byte size using the same freshly indexed pinned
Spree input within the existing serial probe timebox; record command, byte count,
artifact hash and actual memory/time under new S07 evidence. Do not commit the
large raw export. The old 84.63% measurement remains historical; do not present
it as a current-format result or alter its historical gate/sidecar. Record the
new size honestly even if the CLI emits its existing large-file warning.
Existing .spectra/evidence/aci-s02/probe/{run_probe,assemble_probe}.py are reference
only: adapt into NEW S07 evidence paths, never execute their old output targets.

## Verification layers and independent checker contract

Coverage decompositions agree on eight seams: parser inputs/identity, resolution,
canonical storage, projection queries, read leases, staged writers/migration,
exchange validation, producer/probe provenance. Command, lifecycle and AC views
all cover these seams; no branch can publish unvalidated evidence.
Constraints: five-day bounded SQLite evolution, no service/framework/dataflow;
legacy missing evidence explicitly unknown. No claims beyond blank-line stability.

At T independent ATLAS reviews actual source and this draft: identity collision/
ordinal limits, consumed-byte evidence, import safety, epoch6 fallback/interruption,
projection/export consistency, predecessor compatibility, source-free legacy
claims, new module fingerprints and fresh probe selection. Actual full lint,
EARS8, independent live criteria SHA/base plus refine cycle1 score required.
Return maker/checker identities, original observation timestamp, score JSON,
source anchors, blockers/prescriptions and command exit codes.
Maker ramza-backlog-order-s07 cannot checker. No A advance before genuine pass.

## Receipt and backlog

Ledger now records S06 merged PR70/ad9ab7a and closure2026-10-24T01:45:12Z;
six merged,36 remaining,current#29/ACI-S07,next#31. Frozen original queue stays
immutable; remaining order filters merged entries. Root live inventory confirms
36open/no new issues. Never mutate past receipts or mark all complete early.

Before code initialize .spectra/state/aci-s07.execution.json IN_PROGRESS with
stable slice/criteria identity,base,paths,output_commit=null,digests,eight ACs.
Capture actual partial receipt and resumed identity (RUN02). After product/CI
checks commit bounded implementation; clean reentry verifies recorded output
commit/digests with git diff --exit-code and no implementation writes (RUN01).
Use later metadata commit to avoid circular hashes. Root may publish/merge/sync
after all validation under user's standing authorization. Missing capability or
timebox failure needs precise evidence; do not falsify a COMPLETE receipt.

## Confidence

Actual Assemble confidence87.5 AUTO_PROCEED at2026-10-24T01:53:54Z:
pattern_match85,requirement_clarity90,decomposition_stability85,constraint_compliance90.
Independent ATLAS APPROVE, checker atlas-s07-plan-checker, refine4.4/min4/pass
observed2026-10-24T01:52:38Z; original and targeted-confirmation evidence is
aci-s07.review.json. State records the same genuine review at01:53:54Z.
Planning scores/lint do not certify product, migration or probe behavior.

## Rejected Alternatives

Actual Explore: canonical records plus compatible projections84.5 selected;
stable IDs on old edges72.5 cannot retain full occurrence/provenance semantics;
rewriting every query around a new graph70.5 exceeds bounded migration scope.

## Risks

| Risk | Mitigation |
|---|---|
| Stable ID accidentally includes offsets/hash | Explicit key version and blank-line/duplicates/owner controls. |
| Import feature revives unsafe global resolution | Exact package-local subset, S02 guards, unsupported states. |
| Migration drops old usable index | Known epoch6 read adapter, stage+lock, no sweep of graph.6 before/after publication. |
| Legacy evidence falsely reconstructed from current source | Preserve known fields, null unavailable span/producer detail; no source rescan. |
| Parallel assertions collapse in projection/export | Independent IDs include direction/relation/evidence; full-set roundtrip. |
| New helper omitted from generation identity | Versioned producer module hashes with import compatibility. |
| Probe stale after epoch bump | Genuine current measurements, new basename, unchanged verifier/bar. |

## Assemble handoff

Canonical helpers/schemas are original checkout .eidolons/ramza/{bin,schemas},
package1.1.0,ECL2.0,plan-state.v1. Complete actual critic/confidence/scope/freeze,
emission plus full JSON schemas/formats and independent digests. Envelope paths
relative to its directory: aci-s07.md and aci-s07.criteria.md. Root mechanical
verify-envelope --block and correlated trace precede one executor.
Prior external memory persistence rejection remains terminal; no retry/bypass.
