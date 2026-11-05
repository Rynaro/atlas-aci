# Verified test-outcome feedback and an optional ranking overlay (ACI-S35)

`atlas-aci feedback-import` records a test outcome an operator claims about retrieved results, but
only when a pytest JUnit XML artifact, the exact retrieval record and the active snapshot all bear
the claim out. `atlas-aci feedback-rank` prints the retrieval ordering, and only when the operator
asks for the overlay does it move bundles that verified events support. Both commands are
CLI only, no MCP op: nothing is added to the seven read-only tools and the tool manifest is
byte-identical. Feedback processing does not write the graph or the repository: both commands
only lease the active snapshot read-only, `feedback-import` writes one file (`--output`) and
`feedback-rank` writes none.

The overlay is a separate, optional, deterministic re-ordering of the bundles the unchanged
baseline ranker returned. It never adds, drops or rewrites a bundle. With no `--overlay` the
ordering is the baseline ranker's, exactly: the same `graph_query` context function the MCP
server runs, over a read-only lease of the active snapshot.

## What is verified, and what is not

- A `verified_artifact` event means: the artifact bytes have the claimed digest and size, the
  declared run identity matches them, the retrieval record is a self-consistent saved result of
  the named snapshot, the results and their source digests match the snapshot's indexed
  observations, and the artifact has exactly one testcase for the claimed node id whose outcome is
  the claimed one.
- The artifact digest proves bytes, not that a run occurred. The producer and the run are
  `operator_declared` (every event carries `"attestation": "operator_declared"`); nothing here
  can tell a real run from a hand-written file. The link from a result to a test is declared by
  the operator, it is not causal: a passing test says nothing about whether a result caused it.
- The retrieval record is self-consistent, not replayed: its ids are recomputed but its request
  is never re-run. Scoring therefore never uses the positions or the bundle list a record
  declares; it matches bundle ids against the live baseline.
- The store is self-consistent, not authenticated. It carries no secret, so a store whose digests
  agree is accepted whoever wrote it. Import never relabels the events of a previous store.
- Only pytest's default JUnit layout (`junit_family` xunit2, no `junit_prefix`, no namespaces) is
  read. A testcase whose children are skipped, xfail, `error` (a setup or teardown error did not
  exercise the claim), a mix, a duplicate or a name collision is not objective. Only `passed` and
  `failed` are derived.
- Annotations (`useful`, `dead_end`) are `unverified_annotation`: stored, counted, never scored.

## The envelope

`atlas-feedback-envelope.v1` is strict JSON (no byte order mark, no duplicate key, no float,
integers of at most 20 digits, every string valid UTF-8). Every key is required and every object
is closed.

```
{"format": "atlas-feedback-envelope.v1", "retrieval_record_id": HEX64,
 "snapshot_id": HEX64, "generation_id": HEX64,
 "run": null | {"run_id": HEX64, "producer": {"name": TEXT, "version": TEXT}},
 "artifact": null | {"profile": TEXT, "sha256": HEX64, "bytes": INT},
 "claims": [{"claim_id": LABEL, "selector": null | {"kind": "pytest_nodeid", "value": NODEID},
             "outcome": "passed" | "failed", "results": [HEX64], "source_files": {REL: HEX64}}],
 "annotations": [{"annotation_id": LABEL, "kind": "useful" | "dead_end", "results": [HEX64],
                  "source_files": {REL: HEX64}, "note": null | NOTE}]}
```

- `LABEL` is `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, `TEXT` is 1 to 128 printable ASCII characters and
  `NOTE` 0 to 512. `results` holds 1 to 128 unique ids and `source_files` at most 1024 entries.
  Claim and annotation labels are unique; at least one claim or annotation is required.
- `NODEID` is a pytest node id: a path of `[A-Za-z0-9_/-]` characters (1 to 512, relative, no
  `.` or `..` segment), one final `.py` and no other dot in the path, then one to eight `::` names
  (`[A-Za-z_][A-Za-z0-9_]{0,127}`) and an optional `[...]` parameter id of printable ASCII. At
  most 1024 characters. A dot in the path is refused because pytest versions treat `.py` inside a
  directory name differently and `a/b.py` and `a.b.py` would give one classname.
- `tests/test_pay.py::TestCards::test_ok[visa]` maps to classname `tests.test_pay.TestCards` and
  name `test_ok[visa]`, the pair pytest writes into the testcase.
- A `format` that is not the constant is `unsupported_envelope_version`; any other violation (an
  outcome such as `"success"`, free text in a claim, a lone surrogate) is `invalid_envelope`.
- The saved retrieval record is the `structuredContent` object of the `graph_query` context tool
  result (or the compact JSON in `content[0].text`), saved by the operator as it was returned.
  Atlas does not persist records. Records with several bundles match by bundle id: an event is
  not scoped to the query that produced its record, so the same bundle seen in another query is
  supported by the same events.

### Run identity

`run_id = sha256("atlas-test-run.v1:" + canonical JSON)` of the artifact profile, digest and size,
the producer name and version and the snapshot and generation ids. It does not depend on the
claims, their labels or their order. This domain is distinct from the S22 `coverage_run_id`;
compatibility with S22 means the field names (`run_id`, a selector of kind `pytest_nodeid`, the
snapshot and generation ids), not a shared run identity.

## Import

`atlas-aci feedback-import --repo D [--graph-root G] --envelope E --retrieval-record R
[--artifact A] [--store S] --output OUT [limits]`. The first failing check rejects the whole
import: exit 1, empty stdout, no output file, the previous store untouched.

| Stage | Check | Rejection |
| --- | --- | --- |
| 1 | input sizes | `envelope_too_large`, `record_too_large`, `artifact_too_large`, `store_too_large` |
| 2 | the envelope | `invalid_envelope`, `unsupported_envelope_version`, `too_many_claims` |
| 3 | the previous store | `invalid_store`, `unsupported_store_version` |
| 4 | the lease and the schema epoch | `index_unavailable`, `source_snapshot_unavailable`, `index_upgrade_required` |
| 5 | indexed observations | `source_snapshot_unavailable` |
| 6-8 | record, record id, snapshot and generation | `record_invalid`, `record_id_mismatch`, `snapshot_mismatch` |
| 9-13 | provenance, profile, size, digest, run identity, artifact syntax | `missing_provenance`, `unsupported_profile`, `artifact_size_mismatch`, `artifact_digest_mismatch`, `run_identity_mismatch`, `artifact_invalid` |
| 14 | results, sources and fingerprints of each claim, then each annotation | `result_not_in_record`, `result_source_unavailable`, `source_not_indexed`, `source_fingerprint_mismatch` |
| 15 | the derived outcome of each claim | `test_not_in_artifact`, `outcome_not_objective`, `claim_disagrees_with_artifact` |
| 16 | merge | `conflicting_duplicate` |
| 17 | assembly | `too_many_events`, `invalid_output`, `output_too_large` |

A claim failure is `unverifiable_outcome: <detail>` and an annotation failure (or a record fault
of an envelope without claims) is `unlinked_annotation: <detail>`.

- `IMPORT_REJECTIONS`: `envelope_too_large`, `record_too_large`, `artifact_too_large`,
  `store_too_large`, `invalid_envelope`, `unsupported_envelope_version`, `too_many_claims`,
  `invalid_store`, `unsupported_store_version`, `index_unavailable`,
  `source_snapshot_unavailable`, `index_upgrade_required`, `unverifiable_outcome`,
  `unlinked_annotation`, `conflicting_duplicate`, `too_many_events`, `invalid_output`,
  `output_too_large`.
- `UNVERIFIABLE_DETAILS`: `record_invalid`, `record_id_mismatch`, `snapshot_mismatch`,
  `missing_provenance`, `unsupported_profile`, `artifact_size_mismatch`,
  `artifact_digest_mismatch`, `run_identity_mismatch`, `artifact_invalid`,
  `result_not_in_record`, `result_source_unavailable`, `source_not_indexed`,
  `source_fingerprint_mismatch`, `test_not_in_artifact`, `outcome_not_objective`,
  `claim_disagrees_with_artifact`.
- `ANNOTATION_DETAILS`: `record_invalid`, `record_id_mismatch`, `snapshot_mismatch`,
  `result_not_in_record`, `result_source_unavailable`, `source_not_indexed`,
  `source_fingerprint_mismatch`.

### The JUnit profile `atlas-junit-xml-profile.v1`

The artifact bytes are checked before any parser exists: they start with exactly
`<?xml version="1.0" encoding="utf-8"?>` (or `UTF-8`), no byte order mark, strict UTF-8, no NUL,
no `<!` anywhere (no DOCTYPE, entity, comment or CDATA) and no `<?` after the declaration. Only
the five predefined entities and character references remain, so the outcome does not depend on
the expat build. Then the standard library reads it with a collector that builds no tree: the
root is `testsuites` or `testsuite`, `testsuite` is a root or a child of `testsuites`, `testcase`
is a direct child of `testsuite` with `classname` and `name` attributes, depth is at most 8, no
namespace is allowed and the testcase count is bounded. A testcase with no direct `failure`,
`error` or `skipped` child is `passed` (`properties`, `system-out` and a `failure` nested in
another element are ignored); exactly one direct `failure` and nothing else is `failed`. A claim
is verified when the derived outcome equals the claimed one.

### Records and the store

`atlas-feedback-events.v1` holds `events` (`verified_artifact`, sorted by id) and `annotations`
(`unverified_annotation`), with no timestamps. Each event carries the claim, the verification
(`attestation` `operator_declared`, the derived outcome and the testcase pair), the retrieval
(record id, ranker, the record's bundle ids), the run, the artifact digest, the snapshot and
generation ids, the results with their sources and a source fingerprint. Ids are domain-prefixed
sha256 digests of canonical JSON that never include a claim label or the envelope order, so the
same inputs give the same bytes. The merge key of an event is (record, artifact digest, selector)
and of an annotation (record, kind, bundle ids). The same id collapses; the same key with another
id is `conflicting_duplicate`: a different result set, a producer version change (it changes the
run id) and a changed annotation note (the key excludes the note, the id includes it) all conflict.
The summary on stdout is `atlas-feedback-import-summary.v1` with the counts of new and duplicate
records. `EVENT_STATUSES` is `verified_artifact`, `ANNOTATION_STATUSES` is
`unverified_annotation`, `ANNOTATION_KINDS` is `useful` and `dead_end`, and `OUTCOMES` is `passed`
and `failed`.

## Ranking

`atlas-aci feedback-rank --repo D [--graph-root G] --request Q [--overlay
atlas-feedback-ranking-policy.v1 --store S] [--max-request-bytes N] [--max-store-bytes N]
[--max-events N] [--max-output-bytes N]` prints one `atlas-feedback-ranking.v1` and writes
nothing. The request is a `graph_query` context request (`schema_version` `atlas.graph.v1`, `op`
`context`, `text` and the optional fields of the tool), validated as the MCP path validates it;
`continuation` and `expected_generation_id` are not accepted.

- Disabled means no `--overlay`, nothing else: the store is never read, `overlay.enabled` is
  false, every `adjustment` is null and `ranking` equals `baseline`. `--store` without
  `--overlay` is a usage error. The overlay enabled with no active event is also the baseline
  order. The baseline is wall-clock bounded by `deadline_ms`; the sidecar reports
  `baseline.collection_stop` so a stopped collection is visible, not hidden.
- Policy `atlas-feedback-ranking-policy.v1`: a verified `passed` outcome counts +1 per distinct
  artifact digest and selector (the unit), a verified `failed` outcome is recorded, not scored,
  an annotation weighs 0; a bundle moves up at most 3 slots, integer arithmetic only. The order
  is the stable sort of the baseline by (position - delta, -delta, position, bundle id), so delta
  d moves a bundle d slots past unadjusted bundles; baseline position 0 (the exact definition) is
  pinned. The policy and its `policy_id` are printed.
- Each moved bundle lists `adjustment`: `delta`, the `rule`
  `verified_passed_distinct_artifact_selector` (`ADJUSTMENT_RULES`) and the sorted
  `supporting_event_ids`. `events.active` lists every active event with its outcome,
  attestation, unit id, matched bundle ids and whether it was scored.
- An event is active only when every source path it references is indexed in the queried
  snapshot, every source digest equals the indexed observation, and its snapshot and generation
  equal the queried pair. Events from another generation are never active: generations are not
  blended. Every other event is listed once in `events.excluded` with the first applicable
  reason of `EXCLUSION_REASONS`: `path_not_indexed`, `source_fingerprint_mismatch`,
  `generation_mismatch`. An unrelated edit changes the generation, so it excludes events too.
  `annotations.count` is the number of active annotations; `annotations.scored` is always 0.
- A held-out evaluation (`atlas-feedback-evaluation.v1`) exists as a library function only. It
  folds by retrieval record (`int(record_id[:8], 16) % 5`), re-orders each held-out record's
  declared bundle order by the training events and reports the positions. It is a report, not a
  ranking claim, and there is no command flag.

`RANK_REJECTIONS`: `request_too_large`, `invalid_request`, `store_too_large`, `invalid_store`,
`unsupported_store_version`, `too_many_events`, `index_unavailable`,
`source_snapshot_unavailable`, `index_upgrade_required`, `baseline_unavailable`,
`invalid_ranking`, `output_too_large`.

## Limits

Defaults, minimum and maximum; a bool or an out-of-range value is a usage error. Also fixed: XML
depth 8, 128 results and 1024 source files per claim or annotation.

| Option | Default | Minimum | Maximum |
| --- | --- | --- | --- |
| `--max-envelope-bytes` | 1048576 | 1 | 16777216 |
| `--max-record-bytes` | 16777216 | 1 | 67108864 |
| `--max-artifact-bytes` | 8388608 | 1 | 67108864 |
| `--max-store-bytes` | 67108864 | 1 | 268435456 |
| `--max-claims` | 1000 | 1 | 10000 |
| `--max-testcases` | 100000 | 1 | 1000000 |
| `--max-events` | 10000 | 1 | 100000 |
| `--max-request-bytes` | 65536 | 1 | 1048576 |
| `--max-output-bytes` | 67108864 | 1 | 268435456 |

## Command line

Options of `feedback-import`: `--repo`, `--graph-root`, `--envelope`, `--retrieval-record`,
`--artifact`, `--store`, `--output` and the limits above except `--max-request-bytes`. Options of
`feedback-rank`: `--repo`, `--graph-root`, `--request`, `--overlay`, `--store`,
`--max-request-bytes`, `--max-store-bytes`, `--max-events`, `--max-output-bytes`. `--artifact` is
required when the envelope has claims. `--output` must be outside the repository, its `.atlas`
directory and the graph root (a symlinked parent included) and must differ from every input.
Inputs are regular files only.

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| a limit is a bool, 0 or above its maximum | 2 | `Invalid feedback option: <name>` |
| output inside a protected root | 2 | `--output must be outside the repository and graph root.` |
| output equals an input | 2 | `--output must differ from every input.` |
| the namespace cannot be opened | 2 | `The repository cannot be opened as a graph namespace.` |
| `--store` without `--overlay` | 2 | `--store requires --overlay.` |
| `--overlay` without `--store` | 2 | `--overlay requires --store.` |
| another policy name | 2 | `Unsupported ranking policy.` |
| an input cannot be read (FIFO, directory, missing) | 1 | `Feedback input cannot be read: <role>` |
| a rejection | 1 | `Feedback import rejected: <reason>[: <detail>]` or `Feedback ranking rejected: <reason>` |
| the output cannot be written | 1 | `Feedback output cannot be written.` |
| anything else | 1 | `Feedback import failed: <TypeName>` or `Feedback ranking failed: <TypeName>` |

`<role>` is `envelope`, `retrieval-record`, `artifact`, `store` or `request`. Nothing is printed
on stdout on a rejection or a usage error. The usage names of `USAGE_NAMES` are the nine limit
names, `output_location`, `output_equals_input`, `graph_namespace`, `store_without_overlay`,
`overlay_without_store` and `unsupported_policy`.

## Limitations

The `limitations` list of every store and ranking is closed:

1. `artifact_digest_proves_bytes_not_that_a_run_occurred`
2. `producer_and_run_are_operator_declared`
3. `result_to_test_linkage_is_operator_declared_not_causal_and_records_are_self_consistent_not_replayed`
4. `only_pytest_junit_xml_passed_and_failed_are_objective`
5. `junit_name_collisions_are_not_objective`
6. `events_from_another_generation_are_never_active`
7. `failed_outcomes_are_recorded_and_not_scored`
8. `annotations_are_unverified_and_never_scored`
9. `ids_embed_the_absolute_checkout_path_and_are_not_portable`
10. `not_written_to_the_graph_and_not_exposed_over_mcp`
11. `evaluation_is_a_report_not_a_ranking_claim`
12. `store_is_self_consistent_not_authenticated`
13. `baseline_is_wall_clock_bounded_by_deadline_ms`

Ids embed the checkout path, so a store is bound to the same checkout path it was made for.
Producing a record through the MCP code path creates the server's memex root; keep it outside the
repository and every graph root.
