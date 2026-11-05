---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T00:00:00Z
slice_id: ACI-S27
issue: https://github.com/Rynaro/atlas-aci/issues/48
---
# ACI-S27 - import one local OTLP JSON trace artifact into a separate runtime overlay bound to the active snapshot

## Scope

Issue #48 on base `92e8597` (main after PR #94 / ACI-S26), branch `aci-s27`.
- The marker `<!-- atlas-aci:slice:ACI-S27 -->` is verified.
- There is no prior S27 plan, receipt or execution state.
- The criteria bytes hash to `0ca0c0d79db9163d3c411358d6b88a2c9c1a58f97e7cf197be82197dace18cde`, which matches the contract table (7 product ACs plus RUN-01/02).
- Rightsize: computed tier `lite` (score 4). An independent checker still runs.

Dependencies (ancestors of HEAD, with COMPLETE receipts):
- **S08 (#30):** `SourceLease` (`M/storage/snapshots.py:642`), with `.current` path→sha (:650), `.read(path, sha)` (:658) and `acquire` (:717).
- **S20 (#41):** `query/impact.py`. Its `runtime_observation: "not_performed"` (`M/query/impact.py:545`, `M/protocol/impact.py:168`) stays untouched.

Observed facts (re-verified at planning; `M/` = `mcp-server/src/atlas_aci/`):
- **Leasing.**
  - `query/diff.py`: `_open` (:192, which raises `DiffUsageError(str(exc))`; never echo it), `_lease` (:201), `DiffRejected` (:60), `Side` (:73).
  - `checker.open_snapshot` (`M/architecture/checker.py:165-180`) checks, in order: `_closed`/`_validated_lease`/epoch ∈ {8,9}, then `snapshot_id is None`, with `REJECTIONS` at :45.
  - `checker.py` imports jsonschema (:33), so the gate order is copied, not imported. This follows S32's `_open_base` precedent (`M/counterfactual/analyze.py:275-290`).
  - `reader_lease` (`M/codegraph.py:1323-1396`) sets `_lease_schema_epoch` (:1348), `_source_lease` (:1354) and `_validated_lease` (:1396).
  - `snapshot_id` (:1285) comes from the source lease.
- **Snapshot ids.**
  - `graph_digest` covers every record (`snapshots.py:216`), so any `index`, `import-scip` or `import-coverage` publish changes the snapshot id.
  - The id is printed by the `index_done` log line (`codegraph.py:3656` → `__main__.py:231`) and by `check-architecture` reports.
- **Source facts.**
  - Observation records come from `manifest.source_observations` (`checker.py:103`), with state ∈ {indexed, failed, unsupported, disabled} (`M/storage/evidence.py:1324`). Every observed path is packed whatever its state.
  - `symbols(name, kind, path, line_start, line_end, lang, binding, entity_id, occurrence_id)`: DDL at `codegraph.py:351`, link columns at `evidence.py:219` / `codegraph.py:527`, `line_start = start_point[0]+1` (:3884), insert at :3420, `idx_symbols_path`.
  - `ev.file_entity(path, lang)` is at :1111.
- **Strict JSON precedent.** `M/importers/openapi31.py:236-323` (`_strict_pairs`, `_refuse_constant`, `_finite_float`, `_bounded_int`, `_has_surrogate`, `_check_nesting`). Its `_check_nesting` raises `OpenAPIRejected` (:299).
- **Test pins.**
  - `tests/slices/test_s29_openapi_diff.py:1361-1368` pins the exact `importers/` + `contracts/` file list, and the purity scan (:1298-1358) applies to every file listed.
  - `test_s29` `BANNED_VOCABULARY` (:1400-1403).
  - The S29 CHANGELOG slice is `split("\n- **",1)` (:1446-1448), and its README row lookup is the substring `docs/importers/openapi.md`.
  - `test_s14` parses only the `### TSX structural navigation` section (`README.md:428`).
  - `test_export.py:367` bans a root `.gitattributes`.
  - `test_thesis_negatives.py:47-51` bans "alter table" in src.
  - Helpers: `tests/slices/test_s19_support.py` (`init_git`, `write_tree`, `build`, `inventory`).
- **Pins.**
  - Tools JSON: 7 tools, sha `94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56` / 1631472 bytes.
  - MINIMUM 3436, COLLECTION 3448, FIXED_ERROR 797, IMPACT 7730.
  - Nothing in src mentions otel, `observed_sample` or `observed_parent_of`.
- **Interpreters.** CI runs Python 3.14; the dev and production images run 3.12.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-05)
1. **Architecture = scout option B.**
   - What it is: a standalone, content-addressed `atlas-runtime-overlay.v1` written only to the operator's `--output`, which must be outside `--repo` and the graph root. The write is atomic. The overlay is built from a READ-ONLY lease of the active generation.
   - What it never does: write the graph, or edit `codegraph.py`, `storage/*`, `query/*`, `protocol/*`, `tools/*` or `server.py`.
   - `observed_parent_of` is an overlay relation, never an `ev.RELATIONS` member.
   - Rationale: any graph publish changes the snapshot id, so a graph-backed import would invalidate its own matched snapshot. The issue also requires a separate overlay.
   - S20's `runtime_observation: "not_performed"` is unchanged.
2. **Modules.**
   - Pure parser `importers/otel_json.py`, under the S29 purity scan.
   - I/O, leasing and linkage in `overlays/runtime.py` (+ `overlays/__init__.py`).
   - CLI `atlas-aci import-otel` in `__main__.py`. It writes only `--output`; the docs say it does not write the graph.
3. **Manifest `atlas-otel-provenance.v1`.**
   - Closed keys (D2). The artifact digest is checked BEFORE the artifact is parsed. One declared build per artifact.
   - Resource attributes are not used for build identity (planner resolution of the stricter option). No stable OpenTelemetry semantic-convention resource attribute denotes "the build that produced this exact source snapshot": `service.version` is a release label, not a content identity, and `vcs.*` attributes describe VCS/CI telemetry, not running services.
   - Reading resource attributes would also widen the AC-006 allowlist. The build declaration is therefore manifest-only (limitation `build_declaration_is_manifest_only`); a resource cross-check is a profile-v2 follow-up.
4. **Identity.**
   - The observation key is (run_id, trace_id, span_id); the id is a sha256 over a versioned tuple.
   - The importer version is the constant `atlas-otel-json-importer.v1` (no file hash).
   - Canonical bytes, sorted, with no import timestamps.
5. **Allowlist (AC-006).**
   - Read: ids, parent id, start/end unix nano (as decimal strings), `status.code`, `code.file.path`, `code.line.number`.
   - Legacy `code.filepath`/`code.lineno` are not read (→ missing; counted by `legacy_source_attributes_ignored`).
   - Never persisted: everything else.
6. **Linkage** uses the closed classification set (D6), with symbol enrichment by the innermost enclosing symbol at the leased snapshot. `unlinked_line_out_of_range` is the first cut.
7. **AC-007 "runtime-path result"** means every `observed_parent_of` relation, every observation, the overlay `coverage` block, the import summary (library and CLI stdout), and the return value of `overlays.runtime.runtime_paths(overlay)` plus each path in it. Each declares `evidence_coverage: "observed_sample"`. There is no new CLI query command.
8. **Strict parsing and bounds** (D3/D4).
   - Helpers are COPIED into `otel_json.py`, not imported. S29's helpers raise `OpenAPIRejected` with depth 64, so importing them would leak S29 reason codes and couple S27 to S29 private names. S29 stays byte-unchanged.
   - Hex ids: the OTLP JSON encoding states that trace and span ids are "case-insensitive hex-encoded strings", so uppercase is accepted and normalised to lowercase. Day-1 (f) re-reads the spec text. If the wording cannot be confirmed, the STRICTER fallback is to reject uppercase as `invalid_trace_id`/`invalid_span_id`.
9. **Scope amendment:**
   - `overlays/__init__.py`, `overlays/runtime.py`;
   - the one-statement `test_s29` file-list pin edit (and NO other prior-test edit);
   - a separate CHANGELOG `- **` bullet, a README docs-table row (outside the TSX section), `test_s27_*.py`, `tests/fixtures/otel/**`.
   - No S29 `BANNED_VOCABULARY` token appears in any new constant or text.
10. **Invariants:**
    - tools JSON sha and bytes unchanged; 3436/3448/797/7730;
    - gated paths byte-unchanged vs 92e8597 (G-SRC);
    - no interpreter-dependent behaviour.

**Planner refinement P1 (flagged for the checker; resolves a conflict between decisions 3/6 and AC-004).**
- **The conflict.** Decision 3 says "every observation `unlinked_build_mismatch`" on a mismatch. AC-004, however, is unconditional: a valid span lacking source-location attributes SHALL be counted under `missing_source_location`. AC-003 applies "WHEN source linkage is attempted".
- **The resolution.** The span-intrinsic location checks (missing, then malformed) run first. Neither reads any source fact. Every observation WITH a well-formed location is `unlinked_build_mismatch` on a mismatch, and no source fact is read for any observation (tested by the D10 SQL trace and read counter). This satisfies AC-003 and AC-004 literally.
- **Fallback, if the orchestrator rejects P1.** Keep the decision-3 precedence, and add `summary.source_locations{present,missing_source_location,malformed_source_location}`, counted independently of classification. The AC-004 test then asserts that block.

### In
1. `importers/otel_json.py` (pure):
   - constants and closed vocabularies; `Limits`, `normalize_limits`, `OtelRejected`, `OtelUsageError`;
   - strict JSON; `parse_manifest(raw) -> dict`; `parse_artifact(raw, manifest, limits) -> list[Span]`;
   - `map_source_path(declared, source_roots) -> str | None`; `observation_id`, `relation_id`.
2. `overlays/__init__.py` (docstring only) and `overlays/runtime.py`:
   - `FORMAT`, `LIMITATIONS`, `CLASSIFICATIONS`, `UNLINKED_REASONS`, `OverlayRejected`;
   - `open_active(stack, side)`, `build_overlay(manifest, manifest_sha256, artifact_meta, spans, reader)`;
   - `import_otel(side, manifest_raw, artifact_raw, limits)`, `validate_overlay`, `canonical_bytes`, `summary_document`;
   - `runtime_paths(overlay, max_paths=1000, max_path_length=256)`, `write_atomic(path, data)`.
3. `__main__.py`: the `import-otel` command.
4. `tests/slices/test_s27_otel_import.py` (+ optional `test_s27_support.py`) and `tests/fixtures/otel/*`.
5. `docs/importers/opentelemetry.md`, a CHANGELOG bullet and a README row.

### Out
- Collectors, network or OTLP/gRPC ingestion, protobuf encoding, JSON Lines files, metrics and logs.
- Continuous monitoring and deployment access.
- Inferring calls from span nesting; claiming coverage completeness.
- Legacy attribute profiles.
- Per-resource builds (follow-up).
- Linking against a retained (non-active) snapshot.
- MCP exposure, graph or evidence writes, edits to any G-SRC path, a `runtime-paths` CLI.

## Approach

### Decisions

**D1 Pipeline.** The first failure wins, nothing is printed on failure, and no graph is opened before stage 8.

| # | Stage | Touches | Failure |
| --- | --- | --- | --- |
| 1 | limits normalised | — | usage exit 2 |
| 2 | output-path checks (CLI) | path metadata | usage exit 2 |
| 3 | manifest: size → strict JSON → closed shape → profile | manifest bytes | `OtelRejected` |
| 4 | artifact size, then `bytes` + sha256 equality | artifact bytes (no parse) | `artifact_too_large`, `artifact_digest_mismatch` |
| 5 | artifact strict JSON + OTLP shape + span count | artifact | `OtelRejected` |
| 6 | per-span validation (document order) + duplicates | spans | `OtelRejected` |
| 7 | parent-cycle check | span map | `parent_cycle` |
| 8 | `open_active` (D5) | graph, read-only | `OverlayRejected` / usage exit 2 |
| 9 | build match (`reader.snapshot_id` only, no SQL) | — | classification |
| 10 | linkage (matched only), relations, summary, `validate_overlay` | lease (read) | — |
| 11 | `write_atomic(output)` | output path only | exit 1 fixed text |

**D2 Manifest `atlas-otel-provenance.v1`.**
- Limits: at most 65536 bytes (`MAX_MANIFEST_BYTES`); strict JSON (D3) with `MAX_NESTING_DEPTH`.
- Exact key sets at every level: no extra keys, none missing.
```
{"format":"atlas-otel-provenance.v1", "profile":"atlas-otel-json-profile.v1",
 "artifact":{"sha256":HEX64, "bytes":INT 1..268435456},
 "run":{"id":RUN}, "producer":{"name":TEXT, "version":TEXT},
 "build":{"id":BUILD, "snapshot_id":HEX64},
 "source_roots":[{"from":FROM, "to":TO}, ...]}          # 0..16 entries, `from` unique
```
- Field grammars (all checked with `re.fullmatch`, ASCII classes only, no `\d`, no `$`):

| Field | Grammar |
| --- | --- |
| `HEX64` | `[0-9a-f]{64}` |
| `RUN` | `[A-Za-z0-9._:-]{1,128}` |
| `BUILD` | `[A-Za-z0-9._:+-]{1,128}` |
| `TEXT` | `[ -~]{1,128}` |
| `FROM` | `""`, `"/"`, or 1..1024 chars with no NUL, C0, DEL or backslash, no trailing `/`, and no `.`/`..`/empty segment except the single leading empty segment of an absolute path |
| `TO` | `""` or `ev.relative_path` |

- Types: `bytes` is `int` and not `bool`.
- Reasons:
  - size exceeded → `manifest_too_large`;
  - any JSON, nesting, shape, format or type failure → `manifest_invalid`;
  - a profile that is a string other than PROFILE → `unsupported_profile`, checked after shape.
- `manifest_sha256` = sha256 of the raw manifest bytes (exact provenance).

**D3 Strict JSON** (copied helpers; the same for manifest and artifact; every walk iterative):
1. BOM `EF BB BF` → invalid.
2. `_check_nesting(raw)` byte scan with `MAX_NESTING_DEPTH = 32` → `nesting_too_deep` (manifest: `manifest_invalid`).
3. Strict UTF-8 decode, then `json.loads` with:
   - `object_pairs_hook=_strict_pairs` (duplicate keys);
   - `parse_constant=_refuse` (NaN, ±Infinity);
   - `parse_float=_finite_float`;
   - `parse_int=_bounded_int` (`MAX_INT_DIGITS = 4300`, independent of `PYTHONINTMAXSTRDIGITS`).

   Any `Exception`, including RecursionError, trailing data and JSON Lines, → `artifact_invalid_json`.
4. `_has_surrogate` → invalid.

**D4 OTLP profile `atlas-otel-json-profile.v1`.** A single TracesData / ExportTraceServiceRequest JSON object with lowerCamelCase keys. Unknown keys are ignored (as OTLP/JSON requires) and are never walked except by the surrogate scan.

Shape (`artifact_not_otlp_traces`):
- the root is an object with key `resourceSpans` holding a list;
- each item is an object with `scopeSpans`, absent or a list;
- each scope item is an object with `spans`, absent or a list;
- each span is an object;
- the deprecated `instrumentationLibrarySpans` key on any resourceSpans item → reject. Otherwise its spans would be silently dropped.

Span count > `max_spans` → `span_limit_exceeded` (counted before per-span work).

Per span, in document order (resourceSpans, scopeSpans, spans by index):

| Field | Rule | Rejection |
| --- | --- | --- |
| `traceId` | string, fullmatch `[0-9a-fA-F]{32}`, not all zeros; lowercased | `invalid_trace_id` |
| `spanId` | fullmatch `[0-9a-fA-F]{16}`, not all zeros; lowercased | `invalid_span_id` |
| `parentSpanId` | absent or `""` → root; else as `spanId` | `invalid_parent_span_id` |
| `startTimeUnixNano`, `endTimeUnixNano` | required; a string with fullmatch `0\|[1-9][0-9]{0,19}`, or a JSON `int` (not bool); value in 1..18446744073709551615; start ≤ end; persisted as a canonical decimal string | `invalid_span` |
| `status` | absent → code 0; else an object whose `code` is absent (→ 0) or an `int` (not bool) in {0,1,2}; `message` is never read | `invalid_span` |
| `attributes` | absent → []; else a list of ≤ `max_attributes_per_span` objects, each with a string `key`; `value` is read only for the two allowlisted keys | `invalid_span` / `attribute_limit_exceeded` |

- After normalisation, (trace_id, span_id) seen twice → `duplicate_span` (e.g. `AB..` and `ab..`).
- Parent cycles: an iterative three-colour walk over the in-artifact (trace_id, parent) map. A self-parent or any loop → `parent_cycle`.

Source location (per span, never a rejection):
- `code.file.path` value must be exactly `{"stringValue": s}`, with 1 ≤ len(s) ≤ 4096 and no NUL or C0.
- `code.line.number` value must be exactly `{"intValue": v}`, with `v` a decimal string (same regex) or an `int`, in 1..2147483647.
- States:
  - path key absent → `missing`;
  - path present but its value is invalid, the key appears more than once, or the line key is present but invalid or duplicated → `malformed`;
  - otherwise → `present` (line optional).
- `legacy` = any `code.filepath`/`code.lineno` key present. Their values are never read.

`Span` (frozen dataclass): `trace_id, span_id, parent_span_id|None, start, end (str), status_code, location_state, declared_path|None, line|None, legacy: bool`.
- `declared_path` and `line` are kept only when individually valid.
- `line` is kept when `missing`, so it remains visible in `source`.

**D5 Lease** (`open_active`, local copy of the `checker.open_snapshot` order):
1. `symbol_diff._open`, where `DiffUsageError` → usage exit 2 with fixed text.
2. `_lease`, where `DiffRejected` → `OverlayRejected` with the same reason (`index_unavailable` / `source_snapshot_unavailable`).
3. `reader._closed`, not `_validated_lease`, or epoch ∉ {8,9} → `index_upgrade_required`.
4. `snapshot_id is None` or `_source_lease is None` → `source_snapshot_unavailable`.

Any generation kind carrying a snapshot is accepted. The lease is held in one `ExitStack` and released before the write.

**D6 Linkage and closed vocabularies.** `matched = (manifest.build.snapshot_id == reader.snapshot_id)`. Per observation, first match wins:

| # | Condition | Classification | Reads |
| --- | --- | --- | --- |
| 1 | location `missing` | `unlinked_missing_source_location` | none |
| 2 | location `malformed` | `unlinked_malformed_source_location` | none |
| 3 | not `matched` | `unlinked_build_mismatch` | none |
| 4 | `map_source_path` → None | `unlinked_path_outside_root` | none |
| 5 | path ∉ `_source_lease.current` | `unlinked_file_not_in_snapshot` | lease dict |
| 6 | no observation record with state `indexed`, or no file entity row | `unlinked_file_not_indexed` | `OBSERVATIONS_SQL` once; `SELECT 1 FROM entities WHERE id=?` |
| 7 | line > line count of `lease.read(path, sha)` (cached per path) | `unlinked_line_out_of_range` (CUT 2) | pack read |
| 8 | line given and an enclosing symbol exists | `linked_symbol` | symbols query |
| 9 | otherwise | `linked_file` | — |

`map_source_path` (pure):
- `from == ""` matches only relative declared paths; `"/"` matches every absolute path.
- Otherwise a rule matches when `declared == from` (→ None: it names a directory) or when `declared` starts with `from + "/"` (segment boundary).
- The longest `from` wins (unique by D2).
- `result = rest if to == "" else to + "/" + rest`. The result must pass `ev.relative_path`, else None.
- No matching rule → None. There is no implicit identity mapping: relative paths need an explicit `{"from":"","to":""}`. Windows paths never match.

Symbol query:
```
SELECT name, kind, line_start, line_end, entity_id FROM symbols
WHERE path=? AND line_start<=? AND line_end>=?
ORDER BY (line_end-line_start) ASC, line_start DESC, name, kind, entity_id LIMIT 1
```
- Rows are 1-based and inclusive; Day-1 confirms.
- `file_entity_id = ev.file_entity(path, language)["id"]`, confirmed present.
- A source read failure → `OverlayRejected("source_snapshot_unavailable")`, failing closed.

Closed sets:
- `UNLINKED_REASONS = (build_mismatch, missing_source_location, malformed_source_location, path_outside_root, file_not_in_snapshot, file_not_indexed, line_out_of_range)`.
- `CLASSIFICATIONS = (linked_symbol, linked_file, *("unlinked_" + r))`.
- `REJECTIONS` (otel_json, in stage order): `manifest_too_large, manifest_invalid, unsupported_profile, artifact_too_large, artifact_digest_mismatch, artifact_invalid_json, nesting_too_deep, artifact_not_otlp_traces, span_limit_exceeded, attribute_limit_exceeded, invalid_trace_id, invalid_span_id, invalid_parent_span_id, invalid_span, duplicate_span, parent_cycle`.
- `OVERLAY_REJECTIONS` (runtime): `index_unavailable, source_snapshot_unavailable, index_upgrade_required, invalid_overlay`.
- Exception `str()` is `"otel_rejected: <reason>"` / `"overlay_rejected: <reason>"`, never input text or paths.

**D7 Overlay `atlas-runtime-overlay.v1`** (closed; canonical JSON + LF; no paths of the operator's files, clock, host or limits):
```
{format, overlay_id,
 importer:{name:"atlas-aci import-otel", version:"atlas-otel-json-importer.v1", profile},
 run:{id}, artifact:{sha256, bytes}, manifest:{format, sha256},
 producer:{name, version, attestation:"operator_declared"}, build:{id},
 source_roots:[{from,to}] sorted by (from,to),
 snapshot:{declared_id, supplied_id, matched},
 coverage:{evidence_coverage:"observed_sample", completeness:"unknown", sampling:"not_declared",
           relation_semantics:"observed_span_nesting"},
 summary:S, limitations:[LIMITATIONS], observations:[O], relations:[R]}
O = {id, type:"runtime_observation", run_id, trace_id, span_id, parent_span_id|null, artifact_sha256,
     snapshot_id|null, start_time_unix_nano, end_time_unix_nano, status_code,
     source:{declared_path|null, line|null},
     linkage:{classification, path|null, file_entity_id|null,
              symbol:null|{name, kind, line_start, line_end, entity_id|null}},
     evidence_coverage:"observed_sample"}
R = {id, type:"runtime_relation", relation:"observed_parent_of", trace_id, parent_id, child_id,
     parent_span_id, child_span_id, semantics:"observed_span_nesting", evidence_coverage:"observed_sample"}
S = {evidence_coverage:"observed_sample", traces_total, observations_total, linked_symbol, linked_file,
     unlinked_total, unlinked_by_reason:{<every UNLINKED_REASONS key, zeros included>},
     relations_total, root_spans, parent_not_in_artifact, legacy_source_attributes_ignored}
```
- `O.snapshot_id` = the leased id iff `matched`, else null. `path`, `file_entity_id` and `symbol` are null unless classes 8/9 apply.
- `R` exists only when the parent (trace_id, parent_span_id) is in the artifact. Otherwise `parent_not_in_artifact += 1` and no edge is created.
- Relations are materialised whether or not the build matches (they are span-intrinsic).
- Ids, each `sha256(ev.canonical(<value>).encode("utf-8")).hexdigest()`:

| Id | Value hashed |
| --- | --- |
| `O.id` | `["atlas-otel-observation.v1", run_id, trace_id, span_id]` |
| `R.id` | `["atlas-otel-relation.v1", parent_id, child_id]` |
| `overlay_id` | `sha256(("atlas-runtime-overlay.v1:" + canonical(rest)).encode())` |

- Order: observations by (trace_id, span_id); relations by (trace_id, parent_span_id, child_span_id); sets never reach output.
- `validate_overlay` checks:
  - closed keys at every level and closed enums;
  - every `evidence_coverage == "observed_sample"`;
  - sort orders;
  - id and `overlay_id` recompute;
  - summary sums (`linked + unlinked_total == observations_total`; reasons sum);
  - relation endpoints exist.

  Any failure → `invalid_overlay`.

**D8 `runtime_paths(overlay, max_paths=1000 (1..100000), max_path_length=256 (1..4096))`** (library only):
- Steps:
  1. `validate_overlay` (`invalid_overlay`).
  2. Starts = observations with no in-artifact parent, sorted (trace_id, span_id), with `starts_at` ∈ {`trace_root`, `parent_not_in_artifact`}.
  3. Iterative DFS, children sorted by span_id. One path per leaf. A node at `max_path_length` with children ends a path with `truncated: true`.
  4. Emission stops at `max_paths`; `paths_total` counts every path an unbounded walk would emit.
- Result:
```
{format:"atlas-runtime-paths.v1", overlay_id, run_id, snapshot_id|null, evidence_coverage:"observed_sample",
 completeness:"unknown", semantics:"observed_span_nesting", limits:{max_paths, max_path_length},
 paths:[{trace_id, starts_at, observation_ids[], span_ids[], truncated, evidence_coverage:"observed_sample"}],
 paths_total, paths_truncated, limitations:[LIMITATIONS]}
```
- Cost: O(observations + emitted path nodes).

**D9 Bounds** (out of range or not `int` → usage exit 2, `Invalid OpenTelemetry import limit: <name>`). They accept or reject inputs and never change overlay content, so they are not in the overlay.

| Limit | Default | Low | High |
| --- | --- | --- | --- |
| `max_artifact_bytes` | 33554432 | 1 | 268435456 |
| `max_spans` | 100000 | 1 | 1000000 |
| `max_attributes_per_span` | 512 | 1 | 4096 |

Constants: `MAX_MANIFEST_BYTES = 65536`, `MAX_NESTING_DEPTH = 32`, `MAX_INT_DIGITS = 4300`, path ≤ 4096, ≤ 16 source roots.

**D10 Read-only.**
- No SQL other than `SELECT` against the leased connection, and no write.
- On a mismatch, zero SQL statements run after the lease, and `SourceLease.read` is not called.
- `write_atomic`:
  1. `tempfile.mkstemp(prefix=".atlas-runtime-overlay-", dir=output.parent)`;
  2. write all bytes, `fsync`, `os.replace`;
  3. on any failure, unlink the temp file and re-raise `OSError`.
- No `mkdir`.

**D11 CLI** `atlas-aci import-otel --repo R [--graph-root G] --manifest M --output OUT [limits] OTLP_JSON`:
- `--repo` uses `click.Path(exists=True, file_okay=False)`. The other paths use `click.Path(path_type=Path)` with no `exists`.
- Lazy imports; reads are bounded: `open("rb").read(limit + 1)`.
- `except` order: `OtelUsageError`, then `OtelRejected` and `OverlayRejected`, then `DiffUsageError`, then `(OSError, ValueError, sqlite3.Error)`.
- On success, stdout gets binary canonical JSON + LF:
  `{format:"atlas-otel-import-summary.v1", overlay_id, run_id, artifact_sha256, snapshot, evidence_coverage:"observed_sample", summary:S}`.
- On failure, stdout is empty. Click adds the `Error: ` prefix. `str(exc)`, paths and input text are never printed.

| Situation | Exit | Text |
| --- | --- | --- |
| bad limit | 2 | `Invalid OpenTelemetry import limit: <name>` |
| output resolves inside `--repo` or the graph root (`G` or `R/.atlas`) | 2 | `--output must be outside --repo and the graph root.` |
| output resolves equal to the manifest or the artifact | 2 | `--output must differ from the manifest and the artifact.` |
| namespace cannot be opened | 2 | `The repository cannot be opened as a graph namespace.` |
| manifest unreadable | 1 | `OpenTelemetry manifest cannot be read.` |
| artifact unreadable | 1 | `OpenTelemetry artifact cannot be read.` |
| any typed rejection | 1 | `OpenTelemetry import rejected: <reason>` |
| write failure | 1 | `Runtime overlay cannot be written.` |
| other error | 1 | `OpenTelemetry import failed: <TypeName>` |

**D12 Determinism (AC-005).**
- The overlay is a pure function of (artifact bytes, manifest bytes, leased snapshot facts, importer constants).
- No clock, randomness, environment, host or path data.
- Integers are kept as ints, and times as decimal strings (no floats).
- A reimport into another path, or over the same path, gives identical bytes.

### Limitations (static `LIMITATIONS`, emitted in the overlay and runtime_paths, each named in the docs)
1. `span_nesting_is_observed_not_a_call_relation`
2. `observations_are_a_sample_and_completeness_is_unknown`
3. `one_declared_build_per_artifact`
4. `build_declaration_is_manifest_only`
5. `producer_and_build_fields_are_operator_declared`
6. `legacy_code_attribute_spellings_are_not_read`
7. `single_otlp_json_traces_document_only`
8. `metrics_and_logs_are_not_imported`
9. `linkage_requires_the_active_graph_snapshot`
10. `symbol_is_the_innermost_definition_enclosing_the_line`
11. `overlay_is_written_outside_the_graph`
12. `static_impact_output_is_unchanged`
13. `span_timing_is_copied_not_interpreted`
14. `not_exposed_over_mcp`

### Work plan (timebox 3 engineering days)

**Day 1 AM - qualification, before product code.** A scratch script writes `.spectra/evidence/aci-s27/qualification.json`.
- (a) **Strict parser** (3.12 dev image; also `uv run --python 3.14` if available offline):
  - depth 32 parses; 33 → `nesting_too_deep`; 100000 → no RecursionError;
  - brackets inside strings are not counted;
  - a 4301-digit int is rejected;
  - the decimal regex rejects `"+1"`, `"01"`, `"1_0"`, `" 1"`, `"1\n"` and `"\u0663"`;
  - 2^64-1 accepts and 2^64 rejects;
  - uppercase hex normalises; all zeros rejects;
  - BOM, NaN, `1e999`, a duplicate key, `"\ud800"`, non-UTF-8 and two-line JSON Lines are each rejected.
- (b) **Lease and resolve** on an in-test python fixture repo (D-fixtures):
  - `open_active` succeeds read-only;
  - `reader.snapshot_id == build()["snapshot_id"]`;
  - `app/service.py` is in `_source_lease.current` with state `indexed`;
  - the file entity id exists;
  - the symbols query returns the innermost method for a line inside it, with 1-based inclusive ranges and a non-null `entity_id` on epoch 9;
  - record which fixture file yields `file_not_indexed` (`app/broken.py` parse failure, or a `.js` file with `langs=["python"]`);
  - `reader.db.set_trace_callback` works;
  - `inventory(repo, root)` is unchanged.
- (c) **Digest gate first:** non-JSON artifact bytes with a mismatching digest → `artifact_digest_mismatch` (not `artifact_invalid_json`).
- (d) **Canonical bytes:** two `import_otel` runs and two processes with `PYTHONHASHSEED=1/2` → identical overlay bytes.
- (e) **Base invariants** go to `base-invariants.json`: tools sha and bytes, 3436/3448/797/7730, and the full `pytest -q` at base with test_s08 nonregular_pack deselected.
- (f) **Spec wording:** record the OTLP JSON hex-case sentence and the semconv `code.file.path` / `code.line.number` names, if reachable. Otherwise record "not reachable offline".

STOP RULE:
- (a) A valid depth-32 document fails on either interpreter → lower `MAX_NESTING_DEPTH` to the observed safe value (≥ 16) and record it. Below 16 → return to the orchestrator.
- (b) The active snapshot cannot be leased read-only, or the symbols rows are absent/not 1-based inclusive, or the file-entity lookup fails → stop. If the fix needs any G-SRC edit → return to the orchestrator as BLOCKED. A null `entity_id` alone → apply CUT 5 and continue.
- (c) The digest check is not before the parse → fix before Day 1 PM.
- (d) Non-identical bytes → find the nondeterminism before Day 2.
- (f) The hex-case wording contradicts decision 8 → apply the stricter fallback.

**Day 1 PM:** `otel_json.py` (D2-D4, D9), the rejection table and the strict-JSON tests.

**Day 2:** `overlays/runtime.py` (D5-D8, D10), AC-001/002/003/004/005/006/007 at library level.

**Day 3:** CLI (D11), the output-path, isolation, purity, vocabulary and docs tests, docs, CHANGELOG, README, gates, production image, RUN-01/02.

Cut order (each cut becomes a listed limitation; no AC is affected):
1. README row and doc depth;
2. `unlinked_line_out_of_range`, removed from the closed set;
3. the `legacy_source_attributes_ignored` counter;
4. the `PYTHONHASHSEED` subprocess replay;
5. symbol `entity_id`;
6. symbol enrichment, removing `linked_symbol` (`linked_file` only).

Never cut:
- the D1 order with the digest check before the parse;
- the strict parser and bounds;
- closed rejections and classifications;
- P1 precedence with zero source reads on a mismatch;
- the allowlist;
- `observed_sample` on every D7/D8 element;
- `runtime_paths`;
- ids and canonical bytes;
- output-path checks and atomic write;
- CLI fixed texts;
- G-SRC/G-MCP.

### Independent checker conditions
Checker `atlas-s27-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s27.review.json`); this is the validation required by the confidence verdict VALIDATE (84). Every condition below is binding and overrides any conflicting plan text.

Orchestrator resolutions (recorded 2026-11-05 under the user's autonomous-execution instruction): Planner refinement P1 is ACCEPTED (the checker confirms it is the only reading satisfying AC-003 and AC-004 together); D6 governs, and the contrary 'every observation unlinked_build_mismatch' wording in decision 3 and the P1 note is superseded: on a mismatch every observation WITH a well-formed source location is unlinked_build_mismatch and no source fact is read for any observation; the independent `summary.source_locations` block is REQUIRED (condition 4); condition 1 - the symbols query filters `kind NOT LIKE 'scip\_%' ESCAPE '\'` with a scip_import-generation test; if that test cannot be built by end of Day 1, the stricter fallback applies (reject scip_import and coverage_import generations in open_active); condition 14 - if `symbols.entity_id` is absent at epoch 8, only epoch 9 is accepted (`index_upgrade_required` otherwise).

**P1 review.** P1 does not misread AC-003. "WHEN source linkage is attempted" cannot apply to a span with no usable location. AC-004 is unconditional. Only P1 satisfies both ACs on a mismatched import, and D6 rows 1-2 before 3 already encode it. Decision 3 and the P1 note still contain the contrary "every observation unlinked_build_mismatch" wording. D6 governs.

**Confirmed safe, no finding**
- **Read-only lease.** `CodeGraph(read_only=True)` does no mkdir (`codegraph.py:923`) and opens SQLite `mode=ro` (`:977`). `snap.acquire` uses `directory()` without create (`snapshots.py:719`). `LeaseRegistry` is created only in `server.py:481`. I found no WAL pragma or lock file on the reader path.
- **Absent and old graphs.**
  - No graph: `sqlite3.Error` becomes `INDEX_UNAVAILABLE`, then `DiffRejected("index_unavailable")` (`diff.py:201-209`).
  - Epoch 6 or 7 triggers the plan's `index_upgrade_required` gate.
- **Leased generation.** Publishing replaces the graph file (`codegraph.py:2449`), and the open connection and pack fd pin the old inode. `symbols` is therefore the leased generation.
- **Hex case.** The OTLP JSON wording is "case-insensitive hex-encoded", so decision 8 is correct.
- **Prior-test pins.**
  - Only `test_s29` pins the file list, and it reads only S29 constants for vocabulary (`:1427-1439`).
  - `cli.commands` assertions are all subset checks. `import-otel` does not trip `test_s29:1291`.
  - S26 and S29 CHANGELOG slicing is safe.
  - README row 943 is far from the TSX section (428-489).
  - No test pins `__main__.py` text.
- **Other pins and CI.** The src-wide "alter table" and "nx.compose(" scans are covered. mypy is non-strict. No 3.12/3.14 hazard beyond what the plan already handles.

**Findings**

1. **MAJOR.** The symbols query can select SCIP rows.
   - Evidence: `providers/scip.py:794-801` inserts `symbols` rows with `kind="scip_..."` and an identifier-span range. `codegraph.py:2106` filters on `scip\_%`. `query/diff.py:11-12` calls them out of scope. D5 accepts every generation kind, and the D6 query has no kind filter.
   - Effect: the "narrowest range" rule would pick a SCIP row over the real enclosing method, giving a silently wrong `linked_symbol`.
   - Condition: add `AND kind NOT LIKE 'scip\_%' ESCAPE '\'` to the query. Add a scip_import-generation test using the S16 fixtures. If it cannot be built by end of Day 1, take the stricter alternative: reject `scip_import` and `coverage_import` generations in `open_active`.

2. **MAJOR.** The AC-006 test is self-contradictory.
   - Evidence: the plan forbids `name`/`kind` keys "anywhere". D7 itself has `linkage.symbol.{name,kind}`, `producer.name` and `importer.name`.
   - Condition: run the forbidden-key walk (`name`, `kind`, `attributes`, `events`, `links`, `resource`, `scope`) over each observation excluding `linkage.symbol`, and over relations. Assert exact key sets for `symbol`, `producer`, `importer` and the top level.

3. **MAJOR.** `source_observations` holds `history` records with no `state` (the S31 review's finding 1; `checker.py:252-253` filters them). D6 row 6 does not say so, so a `KeyError` would surface as "failed: KeyError".
   - Condition: filter `type == "observation"`, key by path, and use `language` and `observed_source_sha256`. Add a history-record test, or record it as unconstructible.

4. **MAJOR.** Adopt the orchestrator's `summary.source_locations{present, missing_source_location, malformed_source_location}` block.
   - Condition: count it from `span.location_state`, not from classification. Add it to the D7 closed `S` keys, the docs name list, `summary_document` and the AC-004/006 tests.
   - `validate_overlay` must require `missing` and `malformed` to equal the matching `unlinked_by_reason` counts, and `present + missing + malformed == observations_total`.

5. **MINOR.** The `app/broken.py` syntax-error fixture does not give `file_not_indexed`.
   - Evidence: `codegraph.py:3207-3252` shows only an OSError makes a file `failed`, and a syntax-error file stays `indexed`. A file in a language outside `langs` becomes `disabled` (`:3245-3252`) and is still packed.
   - Condition: make the primary s8 fixture a `.js` file built with `langs=["python"]`. Day 1 must confirm it is in `_source_lease.current` with state `disabled`, otherwise s8 would classify as `file_not_in_snapshot`.

6. **MINOR.** The line count for `line_out_of_range` is undefined.
   - Condition: lines = `data.count(b"\n")` plus 1 if the data is non-empty and does not end in LF. Count LF only (tree-sitter rows, `codegraph.py:3884`), never `splitlines()`. Add a test with CRLF, a lone CR and no trailing newline.

7. **MINOR.** Pin edit and purity scan.
   - The `test_s29` list comes from sorted globs, so `importers/otel_json.py` goes between `importers/openapi31.py` and `contracts/__init__.py`, not at the end. The scope amendment's word "append" is wrong.
   - `otel_json.py` may import only `__future__`, `hashlib`, `json`, `re`, `math`, `dataclasses`, `typing`, `collections.abc` and `from atlas_aci.storage import evidence as ev` (`test_s29:1304-1307`; that import form passes via the `parts` rule).
   - It must not import `os`, `sys` or `pathlib`.
   - `overlays/runtime.py` is not under the S29 scan. Only S27's own purity test governs it.

8. **MINOR.** Output-path containment.
   - Condition: define "inside" as the root itself or any descendant. Test it by walking the output's ancestors comparing `os.stat` `(st_dev, st_ino)` against the repo, the graph root and `R/.atlas`. Path-prefix checks miss case variants on case-insensitive filesystems such as macOS.
   - Resolve the output parent once, and put `mkstemp` and `os.replace` in that resolved directory (TOCTOU).

9. **MINOR.** D11's `open("rb").read(limit+1)` blocks on a FIFO.
   - Condition: copy the `__main__.py:38-54` discipline for manifest and artifact: `O_NONBLOCK`, `S_ISREG`, bounded read, fixed texts.

10. **MINOR.** The `TO` grammar is `ev.relative_path` only (`evidence.py:263-272`). It admits NUL, newline and any length, yet `TO` is persisted and joined into lookups.
    - Condition: apply the `FROM` character class (no NUL/C0/DEL) and the 1024-character cap to `TO`.

11. **MINOR.** `runtime_paths` semantics.
    - Condition: define `paths_total` as the number of leaves of the in-artifact forest, independent of `max_path_length`. Define `paths_truncated` as emitted < `paths_total` or any path has `truncated`. State what "max_path_length" counts (nodes).
    - Test an empty artifact (`resourceSpans: []`, `[{}]`) through `validate_overlay` and `runtime_paths`.
    - Validate the `runtime_paths` output with the same closed-key checks.

12. **MINOR.** `validate_overlay` should recompute, not trust.
    - It should check: `matched == (declared_id == supplied_id)`; `snapshot_id` is null iff not matched; every `unlinked_build_mismatch` is consistent with `matched`; summary counts are recomputed from the observations.

13. **MINOR.** The docs must state the "valid span" boundary: whole-artifact rejection for `endTimeUnixNano` 0 or unfinished spans, null values, and all-zero parent ids. They must also state that Windows paths and case or Unicode path differences never link.

14. **MINOR.** Day 1 (b) should show `symbols` has `entity_id` at epoch 8. If it does not, restrict to epoch 9 (`index_upgrade_required` otherwise) rather than let `OperationalError` surface or degrade to `linked_file`. Cache symbol and entity lookups per `(path, line)` and per path. Note that overlay size is O(spans) with no output cap.

## Stories
As an agent operator, I want local OTLP JSON spans bound to the exact indexed source snapshot, with build mismatches and missing locations made visible, so that runtime evidence never silently attaches to the wrong code.

As a reviewer, I want observed parent spans labelled `observed_parent_of` with `observed_sample` coverage, so that nesting is never read as a call graph or as complete execution coverage.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s27.criteria.md` (sha256 `0ca0c0d7...de18cde`), verbatim:

```
### AC-S27-001 (event-driven)
GIVEN a supported OTLP artifact has valid provenance for an indexed snapshot
WHEN the operator imports the artifact
THEN the importer SHALL emit a runtime observation carrying the run, trace, span, artifact and matched snapshot identities.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_observation_has_snapshot_provenance

### AC-S27-002 (event-driven)
GIVEN a child span identifies a parent span present in the same trace
WHEN the importer materializes their relationship
THEN the runtime overlay SHALL represent the relationship as observed_parent_of.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_parent_relation_preserves_observed_semantics

### AC-S27-003 (unwanted-behavior)
GIVEN the artifact's declared build cannot be matched to the supplied source snapshot
WHEN source linkage is attempted
THEN the importer SHALL classify the observation as unlinked_build_mismatch.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_build_mismatch_is_unlinked

### AC-S27-004 (unwanted-behavior)
GIVEN a valid span lacks the supported source-location attributes
WHEN the importer processes that span
THEN the import summary SHALL count the observation under missing_source_location.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_missing_source_is_counted

### AC-S27-005 (event-driven)
GIVEN the artifact digest, run identity, importer version and snapshot mapping are unchanged
WHEN the same trace artifact is imported twice
THEN the runtime observation set SHALL remain identical after reimport.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_reimport_is_idempotent

### AC-S27-006 (ubiquitous)
THEN the persisted runtime overlay SHALL exclude attributes outside the declared import allowlist.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_attributes_are_allowlisted

### AC-S27-007 (ubiquitous)
THEN every runtime-path result SHALL declare its evidence coverage as observed_sample.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_sampling_limits_are_explicit

### AC-S27-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S27-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

### Fixtures
**In-test repo** (`make_world` via `test_s19_support`, `langs=["python"]`):
- `app/service.py`: `class Service:` (l1), `def handle(self, x):` (l2-3), `def helper(x):` (l6-7);
- `app/broken.py` (a syntax error, or the Day-1 (b) substitute);
- the snapshot id comes from `build()` and is cross-checked against `open_active`.

**Files in `tests/fixtures/otel/`** (hand-authored, 2-space JSON, `.gitattributes` `* -text`):
- `trace-basic.json`, trace T1 (uppercase ids on s1):

| Span | Parent | Location | Expected |
| --- | --- | --- | --- |
| s1 | root | `/srv/app/app/service.py` l3 | `linked_symbol` `handle` |
| s2 | s1 | l7 | `linked_symbol` `helper` |
| s3 | s2 | path only | `linked_file` |
| s4 | s1 | none | missing |
| s5 | s1 | legacy attrs only | missing; legacy counter 1 |
| s7 | s1 | `/srv/app/app/absent.py` | `file_not_in_snapshot` |
| s8 | s1 | `/srv/app/app/broken.py` l1 | `file_not_indexed` |
| s9 | s1 | l999 | `line_out_of_range` |
| s11 | s1 | `code.line.number` as stringValue | malformed |
| s12 | s1 | relative `app/service.py` l6 | `linked_symbol` `helper` |

- `trace-basic.json`, trace T2:

| Span | Parent | Location | Expected |
| --- | --- | --- | --- |
| s6 | root | `/opt/elsewhere/x.py` | `path_outside_root` |
| s10 | absent span | — | `parent_not_in_artifact` |
| s13 | s1's span id (exists only in T1) | — | no edge |

- `trace-sentinels.json`: one span whose span name, kind, `status.message`, events, links, `traceState`, flags, resource attributes, scope name/version/attributes, span attributes (`http.url`, `db.statement`, `code.function.name`, `code.filepath`, `code.lineno`) and an unknown top-level key each carry a unique `S27SENTINEL<nn>`. `code.file.path = /srv/app/S27ALLOWED/x.py` is the positive control.
- `manifest-example.json`: valid for `trace-basic.json`, with `source_roots [{"from":"/srv/app","to":""},{"from":"","to":""}]` and `build.snapshot_id` a fixed hex that no test graph produces. It drives AC-003 and the docs example.
- `provenance.json`: `{file: {sha256, bytes, purpose}}` plus `authored: "hand-authored for ACI-S27; no external source"`.

Matched manifests are generated in-test from `manifest-example.json` by replacing `build.snapshot_id` (and `artifact`). Rejection inputs are built in-test.

**Assertion rules.** Every `all(...)` is preceded by a non-empty assertion. Nothing about the behaviour under test is mocked. The only instrumentation is a delegating counter on `SourceLease.read` and a sqlite trace callback.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_otel_observation_has_snapshot_provenance | **Matched import of trace-basic (library and CLI):**<br>- for s1, the observation has `run_id` = manifest run, lowercase `trace_id`/`span_id` from the uppercase input, `artifact_sha256` = sha256(file), `snapshot_id` = leased id = `build()` id, and `id` = recompute;<br>- every observation has those five identities, with `snapshot_id` equal to the leased id;<br>- the header has `snapshot.matched true`, `declared_id == supplied_id`, `importer.version`, and `manifest.sha256` = sha256(manifest bytes);<br>- s1 `linkage` is `linked_symbol` with the `handle` symbol, `path app/service.py` and `file_entity_id` = `ev.file_entity` id; s3 is `linked_file`;<br>- the exact classification per span equals the fixture table. |
| 002 | test_otel_parent_relation_preserves_observed_semantics | - The relation (parent, child) span set equals exactly {(s1,s2),(s2,s3),(s1,s4),(s1,s5),(s1,s7),(s1,s8),(s1,s9),(s1,s11),(s1,s12)}.<br>- Each relation has `relation == "observed_parent_of"`, `semantics == "observed_span_nesting"`, and `parent_id`/`child_id` equal to the observation ids, all in the same `trace_id`.<br>- s10 and s13 have no relation; `parent_not_in_artifact == 2`; `root_spans == 2`.<br>- `"observed_parent_of" not in ev.RELATIONS | ev.REFERENCE_RELATIONS`, and no relation value contains `call`.<br>- The relation set is identical in the mismatch overlay. |
| 003 | test_otel_build_mismatch_is_unlinked | **manifest-example (mismatch):**<br>- every observation with a well-formed location is `unlinked_build_mismatch`;<br>- s4/s5/s11 stay missing/malformed (P1);<br>- every `snapshot_id`, `path`, `file_entity_id` and `symbol` is null;<br>- `snapshot {declared_id: fixture hex, supplied_id: leased id, matched: false}`;<br>- `unlinked_by_reason.build_mismatch` equals the exact count.<br>**Zero source facts read:** with a trace callback on `reader.db` after `open_active`, and a delegating counter on `SourceLease.read`, `build_overlay` executes 0 statements and 0 reads.<br>**Control:** the matched build executes > 0 `SELECT` statements only. |
| 004 | test_otel_missing_source_is_counted | - `summary.unlinked_by_reason.missing_source_location == 2` (s4, s5), in the matched run, in the mismatched run, and in the CLI stdout summary.<br>- `legacy_source_attributes_ignored == 1`.<br>- `malformed_source_location == 1`.<br>- s5's overlay bytes contain no legacy value.<br>- Sum checks: linked + unlinked = total.<br>- An in-test variant moves s1's `code.file.path` to `code.filepath` and increments the count. |
| 005 | test_otel_reimport_is_idempotent | **Byte-identical:**<br>- two library imports;<br>- CLI into `out1` and `out2`;<br>- a CLI rerun over `out1`;<br>- artifact and manifest copied to other paths;<br>- `python -m atlas_aci import-otel` with `PYTHONHASHSEED=1/2` (CUT 4).<br>**Also:**<br>- stdout is identical;<br>- `observations` equal the library output;<br>- `overlay_id` and every id recompute.<br>**Decided:**<br>- a reformatted manifest gives identical observations and relations, a different `manifest.sha256` and a different `overlay_id`;<br>- a changed `run.id` changes every observation id but not the (trace, span) keys;<br>- changed `source_roots` change `overlay_id`. |
| 006 | test_otel_attributes_are_allowlisted | **trace-sentinels:**<br>- no `S27SENTINEL` substring in the overlay bytes, the CLI stdout or `canonical(runtime_paths(...))`;<br>- `S27ALLOWED` IS present (positive control);<br>- the observation key set equals the D7 `O` keys exactly;<br>- `source` keys are `{declared_path, line}`;<br>- the overlay top-level keys equal the D7 set;<br>- no `name`, `kind`, `attributes`, `events`, `links`, `resource` or `scope` key anywhere (iterative key walk). |
| 007 | test_otel_sampling_limits_are_explicit | **Matched and mismatched overlays:**<br>- `coverage == {observed_sample, unknown, not_declared, observed_span_nesting}`;<br>- every observation and relation has `evidence_coverage == "observed_sample"`;<br>- `summary.evidence_coverage` (library and CLI stdout) and `runtime_paths` at top level and per path also carry it;<br>- `completeness == "unknown"`;<br>- `LIMITATIONS` holds limitations 1, 2 and 3 in both outputs.<br>**runtime_paths:**<br>- exact chains for T1 (`s1>s2>s3`, `s1>s4`, …) and `starts_at` values;<br>- `max_paths=1` → `paths_truncated true` and `paths_total` unchanged;<br>- `max_path_length=1` → `truncated` paths.<br>**Static impact:** `query/impact.py` and `protocol/impact.py` still contain `runtime_observation` `not_performed`. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt is `.spectra/state/aci-s27.execution.json`. |

### Supporting tests (same file)
- **Rejection table.** Library `.reason`, plus CLI exit 1 with the exact stderr, empty stdout, and no tmp path, sentinel or input text in stderr. One or more cases per code:
  - `manifest_too_large`;
  - `manifest_invalid`: an extra key, a missing key, a duplicate key, the wrong `format`, run id `"r\n"`, run id `"r\u0663"`, an uppercase snapshot id, 17 roots, a duplicate `from`, `to` with `..`, `from` with a trailing `/`, `bytes: true`;
  - `unsupported_profile`;
  - `artifact_too_large` (`--max-artifact-bytes 10`);
  - `artifact_digest_mismatch`: a byte flip, a `bytes` mismatch, and non-JSON bytes with a wrong digest;
  - `artifact_invalid_json`: BOM, NaN, `1e999`, a duplicate key, a surrogate, non-UTF-8, JSON Lines, a 4301-digit int;
  - `nesting_too_deep`: 33 and 100000;
  - `artifact_not_otlp_traces`: an array root, no `resourceSpans`, `resource_spans`, `scopeSpans: {}`, `instrumentationLibrarySpans`;
  - `span_limit_exceeded`;
  - `attribute_limit_exceeded`;
  - `invalid_trace_id`: 31 chars, all zeros, a base64-looking value, missing;
  - `invalid_span_id`;
  - `invalid_parent_span_id`;
  - `invalid_span`: missing start, end < start, `"+1"`, `"1_0"`, `"\u0663"`, 2^64, `1.5`, `true`, `status.code` 3 or `"STATUS_CODE_OK"`, `attributes {}`, an attribute with no key;
  - `duplicate_span`: same ids, different case;
  - `parent_cycle`: A→B→A, and a self-parent;
  - `index_unavailable`: no graph;
  - `source_snapshot_unavailable`: a portable-import generation, if constructible as in S31.
- **Limits.** Exit 2 for each out-of-range or bool limit, with the fixed text.
- **Output paths.**
  - Exit 2 for output inside `--repo`, inside `--graph-root`, inside `R/.atlas` by default, through a symlinked parent into the repo, equal to the artifact, or equal to the manifest.
  - Exit 1 for a missing parent and for a directory output, with no `.atlas-runtime-overlay-*` left behind.
- **Read-only.**
  - `inventory(repo, graph_root)` is identical before and after a successful import, a rejected import and a mismatched import.
  - `(snapshot_id, _lease_generation_id)` is unchanged.
  - The trace callback shows only `SELECT`.
  - The only new file in the output directory is OUT.
- **Source-root table** (pure): `/srv/application/x` is not matched by `/srv/app`; the longest `from` wins; `""` matches only relative paths; `"/"`; exact-root → None; a `..` after rewrite → None; `C:\x` → None.
- **validate_overlay.** Passes on every output. Rejects a tampered `overlay_id`, unsorted observations, a missing `evidence_coverage`, an unknown classification and a dangling relation.
- **Isolation.**
  - Tools JSON sha and bytes; `READ_ONLY_TOOLS == 7`; 3436/3448/797/7730.
  - `import-otel` is in `cli.commands`.
  - `otel` and `runtime-overlay` do not appear in the tools JSON.
- **Purity.**
  - The amended `test_s29` pin now scans `importers/otel_json.py` under the S29 rules.
  - S27 adds:
    - `overlays/*.py` == [`__init__.py`, `runtime.py`];
    - ASCII; lines ≤ 100;
    - no `\d`/`$` regex; no self-recursion;
    - no `click`, `subprocess`, `socket`, `urllib`, `http`, `time`, `datetime`, `random`, `jsonschema` or `atlas_aci.{server,tools,protocol,codegraph,architecture,counterfactual}` imports;
    - no `INSERT`/`UPDATE`/`DELETE`/`CREATE`/`DROP` SQL literal;
    - no "alter table";
    - a planted positive control.
- **Vocabulary.** All constants, keys and enum values are tokenized as in S29 and must avoid `BANNED_VOCABULARY`; a planted hit proves the scan.
- **Docs.** `docs/importers/opentelemetry.md` names:
  - every rejection, classification, summary key, limitation, limit and constant;
  - the format strings (`atlas-otel-provenance.v1`, `atlas-otel-json-profile.v1`, `atlas-otel-json-importer.v1`, `atlas-runtime-overlay.v1`, `atlas-runtime-paths.v1`, `atlas-otel-import-summary.v1`);
  - every CLI fixed text;
  - `index_done` and `check-architecture` as the snapshot-id sources;
  - the phrases "does not write the graph", "not a call", "observed_sample", "completeness is unknown" and "manifest-only".

  The CHANGELOG entry (sliced by its own title up to `\n- **`) and the README row contain "does not write the graph", "observed_parent_of" and "CLI only, no MCP op". The S29 docs test still passes.
- **Fixture provenance.** Every file is listed, and its sha256 and bytes match.

### Gates beyond the ACs
- **G-SRC.**
  - `git diff --exit-code 92e8597 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage,extractors,providers,indexing,resolvers,frameworks,architecture,counterfactual,contracts,reports,importers/openapi31.py,importers/__init__.py} mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows`
  - `git diff --name-only 92e8597 -- mcp-server/src` ⊆ {`importers/otel_json.py`, `overlays/__init__.py`, `overlays/runtime.py`, `__main__.py`}.
  - `git diff 92e8597 -- mcp-server/tests` touches only `test_s27_*.py`, `fixtures/otel/**`, and `test_s29_openapi_diff.py` with exactly one added line, `"importers/otel_json.py",`.
- **G-MCP.** Tools JSON `94f9567a...` / 1631472; `READ_ONLY_TOOLS` 7.
- **Regression.** `test_s08*`, `test_s14*`, `test_s20*`, `test_s21*`, `test_s29*`, `test_s31*`, `test_s32*`, `test_thesis_negatives`, `test_export` are unchanged and green.
- **CI-exact lint.** `uv run --frozen ruff check src/ tests/`, `uv run --frozen ruff format --check src/ tests/`, `uv run --frozen mypy src/`.
- **Full suite.** `uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition`. No new failure or skip vs the base classification.
- **Production image** (orchestrator):
  1. Build `mcp-server/Dockerfile` from the branch.
  2. `docker run --network none` with the fixture repo mounted `:ro` at `/src`, `/graphs` as a volume, `/out` as a tmpfs and `tests/fixtures/otel` mounted `:ro`.
  3. `atlas-aci index --repo /src --graph-root /graphs` (snapshot id from `index_done`).
  4. Write a matched manifest.
  5. `import-otel` twice into `/out/a.json` and `/out/b.json` → identical bytes and stdout; the sha equals the host run of the same snapshot facts (informational: the repository path differs).
  6. The `/graphs` inventory is identical before and after.
  7. A digest-mismatch manifest → exit 1 with the fixed text and empty stdout.
  8. `--output /src/x.json` → exit 2.
  9. Informational: a rerun with `/graphs` mounted `:ro`, outcome recorded.
  10. `atlas-aci tools` matches the pin.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s27_otel_import.py -q
uv run --frozen pytest tests/slices/test_s08_*.py tests/slices/test_s14_*.py tests/slices/test_s20_*.py tests/slices/test_s21_*.py tests/slices/test_s29_*.py tests/slices/test_s31_*.py tests/slices/test_s32_*.py tests/test_thesis_negatives.py tests/test_export.py -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/ && uv run --frozen mypy src/
```

## Confidence
To be computed with `ramza-score`.
- **Why it is low-churn.** The slice is a pure parser plus a read-only lease, using two established precedents (S29 strict import, S31/S32 leasing). There is no graph, producer, protocol or query change, so generation ids, envelopes and the tools JSON cannot churn.
- **Main uncertainties**, covered by Day 1 with a stop rule:
  - the symbols table's line semantics and `entity_id` population on epoch 9;
  - picking the not-indexed fixture;
  - the nesting bound across interpreters;
  - the OTLP hex-case wording.
- **Semantic risk.** Mis-linkage would attach a span to the wrong symbol. It is bounded by the exact-snapshot gate and the explicit source roots.

## Rejected Alternatives
- **(A) Derived graph generation, as in S21.** It edits `codegraph.py`/`storage`, and publishing changes the snapshot id, so the import invalidates its own matched snapshot. It also puts runtime facts in the graph, contrary to "separate overlay".
- **(C) Evidence-store records.** Same flaws as (A), plus new record kinds in the fingerprinted `storage/evidence.py`.
- **Linking against any retained snapshot through `source_lease(id)`.** That lease has no symbols table, so file-only linkage would hide the mismatch semantics. Deferred.
- **Reading `service.version` or `vcs.*` as build identity.** These are not content identities, and reading them widens the allowlist.
- **Implicit identity mapping for relative paths.** The issue requires explicit remapping.
- **Rejecting the whole artifact on a bad source location.** AC-004 requires counting.
- **Truncating spans or attributes at a limit.** That would fake missing locations; the artifact is rejected instead.
- **Importing the S29 helpers.** That couples S27 to S29 private names and leaks `OpenAPIRejected`.

## Risks (ranked)
1. **Snapshot-id discoverability.**
   - Problem: operators can declare a stale id, making everything `unlinked_build_mismatch`. A re-index or coverage import between the trace build and the import has the same effect.
   - Mitigation: the header shows `declared_id` vs `supplied_id`; the docs name the `index_done` and `check-architecture` sources and warn not to copy `supplied_id` unless that build truly produced it.
2. **Name/line-based symbol join.** Innermost-by-line can pick a nested def or lambda. The rule is documented (limitation 10) and deterministic, and `entity_id` is carried when present.
3. **Private reader attributes and helpers** (`_open`, `_lease`, `_source_lease`, `_validated_lease`). Established reuse (S31/S32); the regression set covers them.
4. **Single declared build** over-attributes multi-service traces (limitation 3). Per-resource builds are a follow-up.
5. **Interpreter differences** (3.12/3.14) in recursion and int parsing. Addressed by the byte scan, the `parse_int` guard, iterative walks and the AST no-recursion check.
6. **Out-of-slice test edit.** The one-line `test_s29` pin edit is recorded in the scope amendment before implementation; G-SRC checks it is exactly one line.
7. **Timebox (3 days).** Follow the cut order. At the limit, report completed checks and a narrow follow-up.
