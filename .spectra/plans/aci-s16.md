---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-02T23:45:00Z
slice_id: ACI-S16
issue: https://github.com/Rynaro/atlas-aci/issues/37
---
# ACI-S16 — official SCIP import from one TypeScript producer

## Scope

Implement issue #37 on merged base `a2e7d37` (main after PR #81), branch `aci-s16`.
Live marker `<!-- atlas-aci:slice:ACI-S16 -->` verified; no prior S16 plan, state or
receipt. Criteria bytes (between markers, leading newline removed) hash to
`b45b747ccfe19d0639ab837fc42541c865660b4d151740fd38093b6030e10e3b` = contract table.
Predecessors verified on the execution base: S07 (#29, `dd88912`), S08 (#30,
`138a6f3`), S09 (#31, `ea9afb9`) are ancestors of HEAD with COMPLETE receipts; the
evidence-record model, pinned source snapshots/leases and cache validation exist.

Observed facts (exploration, `.spectra/evidence/aci-s16/exploration/`):
- Official schema: `scip-code/scip` tag `v0.6.1` (commit `9d57961…`, 2025-10-27),
  `scip.proto` sha256 `b855eb9abdb3fe7f07600ee92f24a6c40f75fceef37136ccdc6844b300936a31`.
- Producer: `@sourcegraph/scip-typescript@0.4.0` (2025-10-02; newer 0.5.x/0.6.0 are
  under the two-week maturity floor). No released version sets
  `Document.position_encoding`; offsets are UTF-16 code units (observed: `saudação`
  after `😀` starts at UTF-16 column 43; code points would give 42, UTF-8 bytes 45).
  `metadata.tool_info` = `scip-typescript`/`0.4.0`; `project_root` is the producer
  host path; occurrences carry Definition (bit 1) or no role; no symbol kinds or
  relationships; external symbols (TypeScript lib) appear only as references.
- `tests/test_thesis_negatives.py::test_no_new_runtime_dependency` pins the core
  runtime dependency set ("must add no new runtime dependency"); `protobuf` is not in
  `uv.lock`.

In:
1. A strict, dependency-free protobuf wire decoder for the SCIP message subset of the
   vendored pinned `scip.proto` (Index, Metadata, ToolInfo, Document, Occurrence,
   SymbolInformation, Relationship). Unknown fields are skipped by wire type; every
   malformed input (truncation, bad wire type/tag, varint overflow, invalid UTF-8,
   bad range arity, negative/oversized values, length beyond buffer) is rejected.
   The official `protobuf` runtime is a **dev-only** extra used by a differential
   test against official generated bindings (generated with pinned protoc, committed
   as a test fixture). No runtime dependency is added.
2. Producer-time sidecar manifest `atlas-scip-manifest.v1` written by a new CLI
   command `atlas-aci scip-manifest` right after the producer runs: producer
   name/version (from the index), declared `position_encoding`, `index_sha256` of
   the exact artifact bytes, and `{relative_path: sha256}` of every indexed document
   as read from the producer's working tree.
3. Operator-side CLI `atlas-aci import-scip INDEX --manifest M --repo R
   [--graph-root G]`. CLI only; nothing added to `READ_ONLY_TOOLS`; no compiler runs
   in the MCP query process.
4. Publication as a new derived generation (kind `scip_import`) from the current
   published `build` generation: base facts copied, SCIP layer added, new snapshot via
   `Builder.publish`, retrieval/coverage regenerated, atomic `os.replace` under the
   writer lock. All validation (decode, manifest, producer, source identity,
   positions) completes before the lock and before any staging file exists.
5. Evidence mapping (per document whose path is observed `indexed` in the base with
   the manifest's hash):
   - SCIP producer record with a new validated fingerprint shape
     `{"scip": {tool {name, version}, schema_sha256, index_sha256, manifest_sha256,
     position_encoding}}`.
   - One entity per SCIP symbol defined in the index: kind `scip_<descriptor kind>`
     (`namespace`, `type`, `term`, `method`, `type_parameter`, `parameter`, `meta`,
     `macro`, `local`), name from the last descriptor, symbols projection with the
     full SCIP symbol string in binding. Prefixed kinds keep SCIP entities disjoint
     from tree-sitter entities and out of tree-sitter candidate kinds.
   - Definition occurrences (role `definition:scip_<kind>`); reference occurrences
     (role `reference:reference`) with `refs` rows.
   - Reference-only occurrences become assertions with relation `reference` — never
     `call` — EXTRACTED to the defining SCIP entity when the symbol is defined in the
     index, otherwise UNRESOLVED `scip_external_symbol`. Source entity = innermost
     SCIP definition whose `enclosing_range` contains the occurrence, else the file.
   - Positions: SCIP `[line, char, endChar]` / `[line, char, endLine, endChar]`
     decoded under the effective encoding (document value if set, which must equal
     the manifest; else the manifest declaration; neither → reject). Converted to
     UTF-8 byte offsets against the snapshot's exact source bytes; display line is
     1-based, column is the byte column (tree-sitter convention).
6. Rejections leave the published graph byte-identical (AC-S16-004/005/007): unknown
   producer (only `scip-typescript`), manifest missing/unparseable/incomplete,
   `index_sha256` mismatch, producer name/version mismatch, document set ≠ manifest
   set, any hash ≠ target snapshot or path not observed `indexed`, base generation not
   `build`, out-of-line positions.
7. Re-import (AC-S16-006): same artifact + manifest onto the same base produces the
   identical generation id and canonical assertion set; re-importing onto a
   `scip_import` generation with the same digests is a no-op reporting the same id; a
   different artifact onto a `scip_import` generation is rejected (rebuild first).
8. Lifecycle: the next `index` (with or without `--since`) from a `scip_import`
   generation performs a full tree-sitter rebuild (the generation kind differs), so
   imported facts are superseded, never orphaned.
9. Status (CLI): `semantic_imports` block for `scip_import` generations: producer,
   version, schema/index/manifest digests, encoding, counts, limitations. Runtime
   capabilities stay `semantic_binding: unsupported` (nothing runs at query time).
10. Docs: `atlas/docs/scip-import.md` (operator workflow, sidecar contract, encoding
    rule, limitations); README/INTEGRATION pointers; CHANGELOG.

Out: running scip-typescript or any compiler in the MCP process; converting
references into calls; producers other than scip-typescript; SCIP relationships,
diagnostics, documentation and external-symbol metadata; mapping SCIP symbols onto
tree-sitter entities; traversal/navigation over `reference` (kept out of
`ev.RELATIONS`, so protocol enums and the documented 3,436/3,448-byte minimum
envelopes are unchanged); a runtime `protobuf` dependency.

Declared implementation paths (issue): `mcp-server/src/atlas_aci/providers/scip*`,
`mcp-server/src/atlas_aci/__main__.py`, `mcp-server/tests/slices/test_s16_*.py`,
`mcp-server/tests/fixtures/scip/*`, `mcp-server/pyproject.toml`, `mcp-server/uv.lock`,
`atlas/docs/*`. Scope amendment (`.spectra/changes/aci-s16/scope-amendment.json`):
`codegraph.py`, `storage/evidence.py`, `storage/snapshots.py` (only if publish needs
it), `protocol/graph.py` (only if a closed schema can observe SCIP records),
`resolvers/dependencies.py` (only if its output validation needs the new relation),
`README.md`, `INTEGRATION.md`, `CHANGELOG.md`, and Ramza/receipt/backlog files.

Rightsize FULL 7 (≥10 files, dev-only dependency, public CLI, untrusted binary input,
novel provider, med stakes).

## Approach

1. Fixture production (`tests/fixtures/scip/`): TypeScript project with non-ASCII text
   (accented identifiers, an astral emoji before identifiers on the same line,
   strings), definitions, references (type, value, property, call-site, import
   specifier, external lib symbol). `generate.sh` runs scip-typescript 0.4.0 in a
   digest-pinned `node:22-slim` with an npm lockfile (integrity hashes) and writes
   `index.scip`, then `atlas-aci scip-manifest` writes the sidecar. `provenance.json`
   records image digest, tool/TypeScript versions, lockfile sha256, scip.proto
   tag/commit/sha256, artifact sha256. Official bindings `scip_pb2.py` generated with
   pinned `grpcio-tools`/protoc are committed for the differential test.
2. `labels.json`: the expected symbol and occurrence set (symbol, role, SCIP range,
   expected UTF-8 byte span and text), produced independently of Atlas code (official
   bindings + Python `str` encoding arithmetic), reviewed by hand for the Unicode rows.
3. `providers/scip_wire.py` (decoder), `providers/scip.proto` (vendored, hash-pinned),
   `providers/scip.py` (manifest, validation, positions, record mapping),
   `codegraph.py` (derived generation publish, `_generation_data` kind `scip_import`,
   status block, `confident_edges` restricted to `KNOWN_EDGE_RELATIONS`, edge relation
   allowance), `storage/evidence.py` (SCIP producer fingerprint; `reference` in
   `REFERENCE_RELATIONS` and allowed on edges but not in `RELATIONS`).
4. Tests `tests/slices/test_s16_contract.py` (8 named) plus supporting
   `test_s16_*.py`: decoder fuzz/malformed corpus, differential decode, CLI, status,
   lifecycle (`index` after import), export/import JSONL round trip of a `scip_import`
   generation, no effect on `callers_of`/god nodes/communities.
5. Gates in Docker: ruff, format, mypy, full pytest (unprivileged, locked image), the
   production image runs `import-scip` read-only on the repo with a writable graph
   root and `serve` answers `search_symbol` with SCIP facts; then re-entry/resume.

### Operator decisions (human_loop: complexity 10/12)

Asked and answered 2026-11-02: (1) `reference` NOT traversable in this slice;
(2) own strict decoder + dev-only official `protobuf` oracle (no runtime dependency);
(3) pin scip-typescript 0.4.0.

### Independent checker conditions (ATLAS, APPROVE_WITH_CONDITIONS — binding)

Recorded in `.spectra/plans/aci-s16.review.json`.

1. `protocol/graph.py` is required: `RECORDS["producer"].fingerprint` becomes
   `anyOf[PRODUCER, SCIP_PRODUCER]`; S17/S18 context/retrieve over SCIP-defined names
   must succeed (retrieval indexes every entity; context bundles validate producers).
   Add `protocol/context.py` if it pins the producer separately.
2. Every kind-switch site accepts `scip_import`: `_generation_data` (kind allow-list,
   observation `require_current`, snapshot pointer, retrieval policy → `available`,
   exact key set = build keys + SCIP block), `_store_generation` (build branch:
   snapshot acquire + retrieval), `published_status`/`status()` coverage reads,
   `_read_coverage` (coverage written after `_store_generation`). Tests: status,
   reader lease, `epoch_ok` succeed on a `scip_import` generation.
3. Entity identity: `owners` encodes the SCIP descriptor chain (or deterministic
   ordinals) so same-name same-kind symbols (two `(p)` parameters) never merge; test
   it. A symbol defined in several ranges/documents gets one entity per
   (symbol, document, definition range) or is rejected — decide and test.
4. `callee_name`/`raw_target` unique per (range, symbol) (use the full SCIP symbol for
   `raw_target`; keep display name searchable via binding/symbols); synthetic
   duplicate-range test against `dep.validate_outputs`.
5. Leakage: exclude `scip_*` entities from name-based navigation seeds
   (`query/indexed.py` NAME_SQL) so existing unique seeds stay unique; make
   `_target_kind` deterministic/non-SCIP for tree-sitter targets; restrict
   `confident_edges` to `KNOWN_EDGE_RELATIONS`; document that status `facts` counts
   include SCIP rows.
6. Encoding is never a free assertion: `scip-manifest --position-encoding` required,
   no default; permitted encodings pinned per (tool, version) — scip-typescript 0.4.0
   → UTF-16 only; definition spans must decode to text equal to the descriptor name
   (mechanical wrong-encoding detector); labeled negative shows UTF-8/UTF-32 give
   45/42 vs 43.
7. TOCTOU: capture the base generation id before the lock and re-verify it under
   `_writer_lock` before any staging file; mirror `_build_atomic`'s
   publish/replace/retain/`builder.published` sequence.
8. Lifecycle: SCIP metadata only in `generation_inputs` (no stray manifest keys
   surviving `backup`); recompute or delete the `binding_dependencies` witness;
   failed-parse retained history must not keep SCIP records (test) or the claim is
   softened.
9. SCIP producer fingerprint branch in `evidence.py` (identity_basis `parsed`), plus
   `importer_sha256` (hash of `providers/scip*.py`); widen edge relation validation
   for `reference`; zero-length `[0,0,0]` span resource-read test.
10. Export/import JSONL round trip of a `scip_import` generation reaches the same
    canonical records (resulting kind `import`); documented.
11. Re-import same-id test uses the same repo path; docs: a different artifact needs a
    full `index` first.
12. Labels script must not import `atlas_aci`; exact set equality and counts; artifact
    hash pinned; hand-checked Unicode rows.
13. Decoder: packed and unpacked `repeated int32` ranges; negative int32 10-byte
    varints; last-wins singulars; field 0; wire types 3–7 rejected; >10-byte varints;
    enum/int overflow; bounds on artifact bytes, documents, occurrences, symbols,
    string lengths and nesting; role bits other than Definition recorded in binding,
    never mapped to `import`; seeded-random differential test vs official parser;
    `scip_pb2.py` excluded from ruff; `protobuf` dev version ≥ gencode; `uv.lock`
    re-locked (CI uses `--frozen --extra dev`).
14. Capability reporting is the CLI status `semantic_imports` block (MCP status
    schema unchanged — accepted); all-or-nothing rejection on any non-indexed document
    documented.

## Stories

As an agent operator with a TypeScript repository, I want to import the official SCIP
index produced by scip-typescript, so that agents receive compiler-grade definition
and reference evidence alongside Atlas's structural graph.

As a reviewer of graph evidence, I want every imported fact bound to the exact
producer version and the exact source bytes it was computed from, so that stale,
foreign or malformed indexes can never become authoritative snapshot evidence.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s16.criteria.md` (sha256 `b45b747c…0e10e3b`).
All tests in `mcp-server/tests/slices/test_s16_contract.py`, real artifact and real
indexer/importer, no mocks of the behavior under test:

| AC | Test | Observable |
| --- | --- | --- |
| AC-S16-001 | test_official_artifact | imported symbols + occurrences == `labels.json` |
| AC-S16-002 | test_producer_identity | every SCIP evidence record's producer = scip-typescript 0.4.0 + digests |
| AC-S16-003 | test_reference_not_call | role-less occurrences → relation `reference`, never `call`; `callers_of` unchanged |
| AC-S16-004 | test_source_mismatch | edited source vs manifest hash → rejected; graph bytes/generation unchanged |
| AC-S16-005 | test_malformed_scip | truncated/corrupt corpus → rejected; graph bytes/generation unchanged; no tmp |
| AC-S16-006 | test_scip_reimport | repeat import → identical generation id and canonical assertion set |
| AC-S16-007 | test_missing_source_manifest | absent/incomplete/foreign manifest → unpublished |
| AC-S16-008 | test_unicode_position_encoding | Unicode rows' byte spans == labels; wrong encoding would differ |
| AC-S16-RUN-01 | gate | declared tracked-file SHA-256 manifest equal across clean re-entry |
| AC-S16-RUN-02 | gate | interrupted/resumed receipt identity equal; one receipt path |

## Confidence

Computed with `ramza-score`. Producer behavior, schema and encoding were observed on a
real artifact; publication reuses the established staged/atomic generation path; the
evidence model is content-addressed so replay identity is structural. Main
uncertainty: coupling of a second producer and a derived generation kind to closed
validators (generation inputs, producer fingerprints, projections, status readers).

## Rejected Alternatives

- Runtime `protobuf` dependency + generated bindings: violates the pinned core
  dependency thesis (AC-NEG-5); kept as a dev-only differential oracle instead.
- Graphify-style simplified JSON skeleton: not the interchange contract.
- Adding `reference` to `ev.RELATIONS` now: changes published protocol enums and
  measured budget floors and leaks into god-node/community analytics; deferred to a
  navigation follow-up with its own envelope measurement.
- Mapping SCIP symbols onto tree-sitter entities by name/line: heuristic; would
  fabricate identity on mismatch.
- Mutating the published generation in place: breaks snapshot binding/graph digest
  validation and the atomic-publication guarantee.
- Guessing UTF-16 for an undeclared encoding: AC-S16-008 requires an explicit
  declaration; the producer-time sidecar carries it.

## Risks

- Closed validators (generation kinds, producer fingerprints, projection coupling,
  status coverage readers) may need more touch points than listed → amend scope with
  reasons before editing.
- Untrusted binary input → strict bounds (max artifact bytes, max documents/
  occurrences) reported as explicit errors; decoder fuzz test.
- Producer-host `project_root` differs from the target repo → only relative paths are
  used; identity is the manifest hashes, never paths alone.
- Snapshot retention is two generations; import targets only the current published
  generation.
- Timebox 5 engineering days; at the limit report completed checks and gaps.
