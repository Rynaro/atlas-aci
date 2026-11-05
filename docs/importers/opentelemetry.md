# OpenTelemetry trace import into a runtime overlay (ACI-S27)

One operator command, CLI only. It is **not** an MCP tool: the server still exposes exactly seven
read-only tools and the tools JSON is unchanged.

```sh
atlas-aci import-otel --repo R [--graph-root G] --manifest MANIFEST --output OUT [limits] OTLP_JSON
```

The command reads one local OTLP JSON traces file and one provenance manifest, binds the observed
spans to the **active snapshot** of `--repo`, and writes one `atlas-runtime-overlay.v1` file to
`--output`. It prints a short `atlas-otel-import-summary.v1` on stdout. Nothing is printed on
failure.

## What this is, and what it is not

- **The import does not write the graph.** It opens a read-only lease of the active generation
  (the same lease the S19, S31 and S32 commands use) and writes only `--output`. Because any
  graph publish changes the snapshot id (`graph_digest` covers every record), a graph-backed
  import would invalidate the very snapshot it is bound to. The overlay is therefore a separate,
  standalone file; the graph, the evidence store and the repository are not touched.
- **Observed, not complete.** Every observation, every relation, the overlay `coverage` block, the
  import summary (library and CLI stdout) and `overlays.runtime.runtime_paths(overlay)` plus each
  path in it declare `evidence_coverage: "observed_sample"`. The spans are whatever one run
  happened to emit; completeness is unknown (`completeness: "unknown"`, `sampling:
  "not_declared"`). Absence of a span is not evidence that code did not run.
- **`observed_parent_of` is span nesting, not a call.** A child span whose parent span is in the
  same trace of the same artifact becomes a relation with `semantics: "observed_span_nesting"`.
  It is not a call, a dependency or a control-flow edge; no call is inferred from it. It is an
  overlay relation only and is not a member of the graph relation sets.
- **Not in the static impact output.** `graph_query op=impact` still reports
  `runtime_observation: "not_performed"`. Combining overlays with static impact is a follow-up.
- **One local JSON document.** No collector, network, gRPC, protobuf, JSON Lines, metrics or logs.

## Provenance manifest `atlas-otel-provenance.v1`

At most 65536 bytes (`MAX_MANIFEST_BYTES`); strict JSON; every object has exactly these keys.

```json
{
  "format": "atlas-otel-provenance.v1",
  "profile": "atlas-otel-json-profile.v1",
  "artifact": {"sha256": "<64 lowercase hex>", "bytes": 9947},
  "run": {"id": "run-2026-11-05-001"},
  "producer": {"name": "otel-sdk-fixture", "version": "1.0.0"},
  "build": {"id": "build-abc123", "snapshot_id": "<64 lowercase hex>"},
  "source_roots": [{"from": "/srv/app", "to": ""}, {"from": "", "to": ""}]
}
```

A complete example is `mcp-server/tests/fixtures/otel/manifest-example.json`.

- The artifact digest and size are checked **before** the artifact is parsed.
- **Build identity is manifest-only** (limitation `build_declaration_is_manifest_only`): OTLP
  resource attributes such as `service.version` or `vcs.*` are never read, because none of them
  denotes the build that produced this exact source snapshot. One declared build per artifact.
- `producer` and `build` are operator declarations (`attestation: "operator_declared"`).
- `source_roots` has 0..16 entries with unique `from`. See "Path mapping".
- Character classes: `run.id` `[A-Za-z0-9._:-]{1,128}`; `build.id` `[A-Za-z0-9._:+-]{1,128}`;
  `producer.*` printable ASCII, 1..128; `artifact.bytes` an integer, 1..268435456.
- A manifest whose `profile` is a string other than `atlas-otel-json-profile.v1` is
  `unsupported_profile`, after the rest of the shape has been checked.

### Where to find the snapshot id

`build.snapshot_id` is the id of the source snapshot the traced build was indexed as. Read it from
the `snapshot_id` field of the `index_done` log line of `atlas-aci index` (printed on stderr), or
from the `snapshot.snapshot_id` field of a `check-architecture` report. A re-index, `import-scip`
or `import-coverage` publish after that changes the id. The overlay header keeps `declared_id`
next to `supplied_id` (the leased id) so a mismatch is visible. Do not copy `supplied_id` into the
manifest unless that build truly produced the indexed sources: that would defeat the check.

## OTLP profile `atlas-otel-json-profile.v1`

A single TracesData / ExportTraceServiceRequest JSON object with lowerCamelCase keys.

- `traceId` and `spanId` are case-insensitive hex strings (the OTLP JSON mapping); they are
  lowercased. All-zero ids are invalid. `parentSpanId` is absent or `""` for a root.
- `startTimeUnixNano` and `endTimeUnixNano` are required: a decimal string (`0|[1-9][0-9]{0,19}`,
  ASCII digits only) or a JSON integer, value 1..18446744073709551615, start <= end. Persisted as
  decimal strings.
- `status.code` is absent or 0, 1 or 2. `status.message` is never read.
- `attributes` is absent or a list of objects each with a string `key`.
- Unknown keys are ignored and never walked, except by the lone-surrogate scan.
- Deprecated `instrumentationLibrarySpans` on any resource is rejected, so spans are never
  silently dropped.

**The valid-span boundary.** A span that does not meet the rules above does not become a
"missing location": the whole artifact is rejected (`invalid_span` and the identifier reasons).
That includes an `endTimeUnixNano` of 0 or an unfinished span, JSON `null` values for required
fields, a start after the end, and an all-zero trace, span or parent id. A span that only lacks
source-location attributes is valid and is counted (see below).

### Strict JSON

A byte-order mark, invalid UTF-8, trailing data (including JSON Lines), duplicate keys, `NaN` and
`Infinity`, numbers that overflow to infinity, an integer token over `MAX_INT_DIGITS` (4300)
digits and lone surrogates are `artifact_invalid_json`. Bracket depth outside strings above
`MAX_NESTING_DEPTH` (32) is `nesting_too_deep`; it is a byte scan, so it never depends on the
interpreter's recursion limit. Every walk is iterative.

## Source location and linkage

The allowlisted span attributes are `code.file.path` (value exactly `{"stringValue": s}`,
1..4096 characters without NUL or control characters) and `code.line.number` (value exactly
`{"intValue": n}`, a decimal string or an integer, 1..2147483647). The legacy spellings
`code.filepath` and `code.lineno` are not read; a span carrying one is counted in
`legacy_source_attributes_ignored`.

The location state of a span is `missing` (no `code.file.path`), `malformed` (the path is
invalid or repeated, or the line is present but invalid or repeated) or `present`. The
`summary.source_locations` block counts them independently of linkage.

Closed classification, first match wins:

| Classification | When | Source facts read |
| --- | --- | --- |
| `unlinked_missing_source_location` | the location is missing | none |
| `unlinked_malformed_source_location` | the location is malformed | none |
| `unlinked_build_mismatch` | the declared snapshot id differs from the leased id | none |
| `unlinked_path_outside_root` | no source root maps the declared path | none |
| `unlinked_file_not_in_snapshot` | the mapped path is not a file of the snapshot | the lease's file list |
| `unlinked_file_not_indexed` | no `indexed` observation (or no file entity) for the path | observations, entity |
| `unlinked_line_out_of_range` | the line is past the last line of the snapshot file | the file bytes |
| `linked_symbol` | a line is given and a definition encloses it | symbols |
| `linked_file` | otherwise | none |

- **Missing and malformed locations come first.** They are properties of the span alone, so they
  are counted as `missing_source_location` / `malformed_source_location` whether or not the build
  matches. On a build mismatch every observation with a well-formed location is
  `unlinked_build_mismatch`, and no source fact is read for any observation (no query, no file
  read).
- The overlay `snapshot` block is `{declared_id, supplied_id, matched}`; `snapshot_id` on an
  observation is the leased id only when `matched`, else null.
- **Symbols.** `linked_symbol` carries the innermost definition enclosing the line (smallest
  line range, then the later start), with `name`, `kind`, `line_start`, `line_end` and
  `entity_id`. It is a join by file and line (limitation
  `symbol_is_the_innermost_definition_enclosing_the_line`), not a proof of which code ran.
  Symbols from imported SCIP layers (`scip_*` kinds) are ignored. Lines are counted as LF
  terminated rows plus a final partial row (CRLF and lone CR do not add rows).
- Accepted graphs: the active generation must carry an authenticated source snapshot (a build,
  or a derived `scip_import` / `coverage_import` generation) at schema epoch 8 or 9. Otherwise
  the import is rejected with `index_unavailable`, `source_snapshot_unavailable` or
  `index_upgrade_required`.

### Path mapping

A rule matches when the declared path equals `from` (a directory, so it never links) or starts
with `from + "/"` (whole segments). The longest `from` wins. `{"from": "", "to": ""}` maps relative
declared paths unchanged; `{"from": "/", ...}` matches every absolute path. The result is
`to + "/" + rest` (or `rest`) and must be a clean relative path; `..`, `.`, empty segments and
trailing slashes do not map. There is no implicit identity mapping. Windows paths (a backslash or
a drive letter), case differences and Unicode normalization differences never link: matching is
byte exact on the declared and the mapped path.

## The overlay `atlas-runtime-overlay.v1`

Canonical JSON plus one LF, no timestamps, host or paths of the operator's files. The overlay is a
pure function of the artifact bytes, the manifest bytes, the leased snapshot facts and the
importer constants (`atlas-otel-json-importer.v1`), so a reimport is byte identical. Its
`overlay_id` hashes everything else, including `manifest.sha256`: a reformatted manifest gives the
same observations and relations and a different `overlay_id`. The file is written by temp file and
atomic replace in the output's directory, with mode 0600.

Top-level keys: `format`, `overlay_id`, `importer`, `run`, `artifact`, `manifest`, `producer`,
`build`, `source_roots`, `snapshot`, `coverage`, `summary`, `limitations`, `observations`,
`relations`. Each observation holds only: `id`, `type`, `run_id`, `trace_id`, `span_id`,
`parent_span_id`, `artifact_sha256`, `snapshot_id`, `start_time_unix_nano`,
`end_time_unix_nano`, `status_code`, `source` (`declared_path`, `line`), `linkage`
(`classification`, `path`, `file_entity_id`, `symbol`) and `evidence_coverage`. **No other span
data is persisted**: not names, kinds, attributes, events, links, resource or scope data.

Size is O(spans): there is no output cap beyond the input bounds below.

`summary` keys: `evidence_coverage`, `traces_total`, `observations_total`, `linked_symbol`,
`linked_file`, `unlinked_total`, `unlinked_by_reason` (one key per reason, zeros included:
`build_mismatch`, `missing_source_location`, `malformed_source_location`, `path_outside_root`,
`file_not_in_snapshot`, `file_not_indexed`, `line_out_of_range`), `source_locations` (`present`,
`missing_source_location`, `malformed_source_location`), `relations_total`, `root_spans`,
`parent_not_in_artifact`, `legacy_source_attributes_ignored`.

`overlays.runtime.validate_overlay` recomputes every id, count and consistency rule; any failure
is `invalid_overlay`.

### Runtime paths (library only)

`overlays.runtime.runtime_paths(overlay, max_paths=1000, max_path_length=256)` returns
`atlas-runtime-paths.v1`: one chain per leaf of the in-artifact forest, root to leaf, with
`starts_at` `trace_root` or `parent_not_in_artifact`. `max_path_length` counts nodes (spans);
`paths_total` is the number of leaves regardless of both limits; `paths_truncated` is true when
fewer paths than that were emitted or any path was cut. There is no CLI query command.

## Limits and constants

| Limit | Default | Range |
| --- | --- | --- |
| `max_artifact_bytes` | 33554432 | 1..268435456 |
| `max_spans` | 100000 | 1..1000000 |
| `max_attributes_per_span` | 512 | 1..4096 |

An artifact over a limit is rejected (`artifact_too_large`, `span_limit_exceeded`,
`attribute_limit_exceeded`); nothing is truncated. Constants: `MAX_MANIFEST_BYTES` (65536),
`MAX_NESTING_DEPTH` (32), `MAX_INT_DIGITS` (4300), at most 16 source roots.

## Rejections

Library exceptions carry a closed `reason`; the CLI prints it and nothing else.

`OtelRejected` reasons, in stage order: `manifest_too_large`, `manifest_invalid`,
`unsupported_profile`, `artifact_too_large`, `artifact_digest_mismatch`, `artifact_invalid_json`,
`nesting_too_deep`, `artifact_not_otlp_traces`, `span_limit_exceeded`,
`attribute_limit_exceeded`, `invalid_trace_id`, `invalid_span_id`, `invalid_parent_span_id`,
`invalid_span`, `duplicate_span`, `parent_cycle`. `OverlayRejected` reasons:
`index_unavailable`, `source_snapshot_unavailable`, `index_upgrade_required`, `invalid_overlay`.

## CLI

| Situation | Exit | Text |
| --- | --- | --- |
| bad limit | 2 | `Invalid OpenTelemetry import limit: <name>` |
| output inside `--repo` or the graph root | 2 | `--output must be outside --repo and the graph root.` |
| output is the manifest or the artifact | 2 | `--output must differ from the manifest and the artifact.` |
| graph namespace cannot be opened | 2 | `The repository cannot be opened as a graph namespace.` |
| manifest unreadable | 1 | `OpenTelemetry manifest cannot be read.` |
| artifact unreadable | 1 | `OpenTelemetry artifact cannot be read.` |
| typed rejection | 1 | `OpenTelemetry import rejected: <reason>` |
| write failure | 1 | `Runtime overlay cannot be written.` |
| other error | 1 | `OpenTelemetry import failed: <TypeName>` |

- "Inside" means the root itself or any descendant of `--repo`, `--graph-root` or
  `<repo>/.atlas`, decided by comparing device and inode of the output's ancestors, so symlinked
  parents and case variants are caught. Nothing is created: a missing output directory is exit 1.
- The manifest and the artifact are opened without blocking and must be regular files; a FIFO or
  a directory is `cannot be read`.
- Paths, input text and exception messages are never printed.

## Limitations (also emitted in the overlay and in `runtime_paths`)

`span_nesting_is_observed_not_a_call_relation`,
`observations_are_a_sample_and_completeness_is_unknown`, `one_declared_build_per_artifact`,
`build_declaration_is_manifest_only`, `producer_and_build_fields_are_operator_declared`,
`legacy_code_attribute_spellings_are_not_read`, `single_otlp_json_traces_document_only`,
`metrics_and_logs_are_not_imported`, `linkage_requires_the_active_graph_snapshot`,
`symbol_is_the_innermost_definition_enclosing_the_line`, `overlay_is_written_outside_the_graph`,
`static_impact_output_is_unchanged`, `span_timing_is_copied_not_interpreted`,
`not_exposed_over_mcp`.

Follow-ups, not done here: per-resource builds, linking against a retained (non-active)
snapshot, combining overlays with static impact, a resource-attribute cross-check.
