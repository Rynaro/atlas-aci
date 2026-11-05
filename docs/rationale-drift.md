# ADR mappings and source-change review signals (ACI-S34)

`atlas-aci rationale-map` links the exact text of an architecture decision record (ADR) to source
code the operator names by hand. `atlas-aci rationale-drift` then compares two indexed snapshots
and flags a link whose source changed while the decision document did not. The flag is an
**observation, not a verdict**: it says "look at this decision again", never that the decision
was broken, still holds or is out of date. Both commands are CLI only, no MCP op: nothing is added
to the seven read-only tools and the tool manifest is byte-identical.

Each command writes one standalone, content-addressed document to the operator's `--output`
(`atlas-rationale-overlay.v1`, `atlas-rationale-drift.v1`) and prints a short
`atlas-rationale-summary.v1`. The analysis does not write the graph, a repository or an ADR: the
documents are read from the snapshot, the graphs are leased read-only, and nothing is fetched or
discovered. The link is an operator declaration (`operator_declared_rationale_link`); it is not
the `rationale` table of the graph and not the `rationale_for` relation, and there is no
similarity search over prose.

## The mapping

The operator supplies `atlas-rationale-mapping.v1`, strict JSON (no byte order mark, no duplicate
key, no float, integers of at most 20 digits, every string valid UTF-8 so a lone surrogate is an
`invalid_mapping`). Every key is required and every object is closed.

```
{"format": "atlas-rationale-mapping.v1", "snapshot_id": HEX64,
 "mappings": [{"mapping_id": LABEL, "decision_id": LABEL,
   "document": {"path": REL, "sha256": HEX64},
   "span": {"start_byte": INT, "end_byte": INT, "sha256": HEX64},
   "target": {"entity_id": HEX64, "path": REL, "kind": KIND, "name": NAME}}]}
```

- `LABEL` is `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` (an operator label: `decision_id` and `mapping_id` are
  not checked against anything), `HEX64` is 64 lowercase hex digits, `REL` a relative path of at
  most 4096 characters, `KIND` is `[A-Za-z0-9_.:-]{1,64}`, `NAME` 1 to 1024 characters and `INT`
  an integer from 0 to 2147483647. `mappings` is not empty and `mapping_id` is unique.
- The span is a byte range `[start_byte, end_byte)` of the document with a declared digest. The
  target is the exact entity id plus the descriptors path, kind and name, which must equal the
  stored entity row. A file is a target too: its kind is `file` and its name its path.
- `snapshot_id` is the snapshot the document and the target are read from. It must be the ACTIVE
  snapshot of the repository.

## Resolution (`rationale-map`)

Every mapping is resolved against the mapped snapshot only. The ADR bytes are read from the
snapshot's source pack, never from the checkout, so a document deleted or edited in the working
tree afterwards does not change the result; the span, the document digest and the entity are
checked in this order and the first failure becomes one `unresolved_mapping` record (stage
`mapping_import`):

| Reason | The mapping is unresolved when |
| --- | --- |
| `document_not_in_snapshot` | the document has no `indexed` observation (`document_not_in_snapshot` also covers a present but non-indexed document) |
| `document_digest_mismatch` | the indexed document's digest is not the declared one |
| `span_out_of_range` | not `start_byte < end_byte <= document length` |
| `span_not_utf8` | the span bytes are not strict UTF-8 (a span may not split a character) |
| `span_digest_mismatch` | the span digest is not the declared one |
| `target_not_found` | no entity has that id, or its path is not indexed |
| `target_kind_unsupported` | the kind is a `scip_*` kind or the coverage test kind |
| `target_descriptor_mismatch` | the stored path, kind or name differs from the declared one |
| `target_definition_ambiguous` | a symbol lacks exactly one source definition, or a file id is not its path's file id |

Resolution is by exact entity id. A name is never searched for and a mapping is **never rebound**,
not even to a rename or move candidate that the snapshot diff suggests. An unresolved record
echoes the declared input so that nothing is hidden. The overlay records, for each resolved
mapping, the decision id, the span (bytes, lines, digest), the document digest, the full target
identity (class `symbol` or `file`, path, kind, name, language, owners, ordinal, definition
occurrence, source digest) and the snapshot and generation ids.

## Drift (`rationale-drift`)

`rationale-drift` re-resolves the mapping on the before side (which must be the mapped snapshot)
and compares it with the after side through the ACI-S19 snapshot diff, once. Both sides are
leased with the same read-only lease as every other operator command; the repository, the ADRs
and the graphs are not written.

For each mapping the result has a `source_state`, a `decision_state` and one `outcome`:

| source_state | Meaning |
| --- | --- |
| `unchanged` | no S19 record and the target is on the after side with one definition (a file: equal digests) |
| `location_only`, `formatting_only` | S19 `location` or `format`: raises no signal and can mask a comment pragma |
| `signature_changed`, `body_changed` | S19 `signature` or `body` |
| `content_changed` | a file target whose whole-file digest differs |
| `removed` | S19 `removed` (a file: neither observation nor file entity on the after side) |
| `unknown` | the correspondence cannot be established |
| `not_compared` | the mapping did not resolve, or the comparison was rejected |

| decision_state | Meaning |
| --- | --- |
| `document_unchanged` | the after snapshot observes the document with the mapped digest |
| `document_changed` | it observes the document with another digest (in any observation state) |
| `document_missing` | the after snapshot has no observation of the document |
| `not_compared` | no comparison was made |

The review rule is `source_signature_body_or_content_changed_and_document_digest_unchanged`: a
`rationale_review_needed` finding is raised if and only if the source state is `signature_changed`,
`body_changed` or `content_changed` and the **whole-document digest** is the same on both sides.
An edited document records `document_changed` and no finding.

The first matching row wins:

| Row | Condition | Outcome / reason |
| --- | --- | --- |
| E0 | import-unresolved | `unresolved_mapping` / the import reason (stage `mapping_import`) |
| E1 | the comparison was rejected | `unknown_target_history` / `comparison_rejected`, detail the S19 reason |
| E2 | S19 `unknown` record (a file: after state `failed`) | `unknown_target_history` / the S19 reason, `extraction_failed_after` for a file |
| E3 | the target path is in S19 `partial_paths` (for a file this is every after state that is not `indexed`) | `unknown_target_history` / `path_partial` |
| E3b | same-name siblings changed (below) | `unknown_target_history` / `sibling_ordinal_shift` |
| E4 | removed | `unresolved_mapping` / `target_removed` |
| E5 | added, absent without a record, or a file with exactly one of observation and file entity | `unknown_target_history` / `correspondence_not_confirmed` |
| E6 | the source state is established | (continues) |
| E7 | no observation of the document in the after snapshot | `unresolved_mapping` / `document_not_in_snapshot` |
| E8 | a review state and `document_unchanged` | `rationale_review_needed` |
| E9 | otherwise | `no_finding` |

For symbols E3 is shadowed by E2 (S19 emits a record for every surviving entity of a failed
path), and E5 cannot arise from a real snapshot pair; both are kept for the closed contract and
tested with fabricated facts. A target removed together with its document stays `target_removed`.

**Ordinal renumbering.** An entity id contains the ordinal among same-name siblings, so deleting
an earlier same-name definition renumbers the survivor and S19 would compare it under the old id.
When the siblings of the mapped symbol (same path, kind, name and owner names) are more than one
on either side and their id sets differ between the two snapshots, the mapping is
`unknown_target_history` / `sibling_ordinal_shift`, ahead of signature, body and removal. A
recreation under an identical id with no sibling change cannot be told from an edit.

**Containers.** A class is compared by its own declaration with nested definitions excluded: a
member edit is a change of the member's entity, not of the class; an edit of the class's own
statements is a `body_changed`.

If the comparison is rejected (`configuration_mismatch`, `identity_kind_mismatch`, ...) the
document is still written and every resolved mapping is `unknown_target_history` with reason
`comparison_rejected`; the exit status is 0. The file targets use whole-file digests from the
snapshot observations, so any edit of the file raises the signal.

### Races

Both sides are leased before the comparison. If the change set's before or after snapshot and
generation ids are not the leased ones (a re-index in between), the run is rejected as
`snapshot_changed`; a rejected comparison is re-checked with a fresh lease of each side and a
changed generation is `snapshot_changed` too.

## Closed vocabularies

Every value below is a closed code; the documents are checked by `validate_overlay`,
`validate_drift` and `validate_summary` before they are returned.

Formats: `atlas-rationale-mapping.v1`, `atlas-rationale-overlay.v1`, `atlas-rationale-drift.v1`,
`atlas-rationale-summary.v1`. Link kind: `operator_declared_rationale_link`. Outcomes:
`rationale_review_needed`, `unknown_target_history`, `unresolved_mapping`, `no_finding`.
Stages: `mapping_import`, `comparison`. Comparison states: `compared`, `rejected`. Target classes:
`symbol`, `file`.

`rationale-map` rejections (exit 1): `mapping_too_large`, `too_many_mappings`, `invalid_mapping`,
`unsupported_mapping_version`, `index_unavailable`, `source_snapshot_unavailable`,
`index_upgrade_required`, `mapping_snapshot_mismatch`, `output_too_large`, `invalid_overlay`.

`rationale-drift` rejections (exit 1), exactly these sixteen:

```
mapping_too_large, too_many_mappings, invalid_mapping, unsupported_mapping_version,
before_index_unavailable, before_source_snapshot_unavailable, before_index_upgrade_required,
after_index_unavailable, after_source_snapshot_unavailable, after_index_upgrade_required,
mapping_snapshot_mismatch, snapshot_changed, comparison_failed, output_too_large,
invalid_overlay, invalid_drift
```

History reasons of `unknown_target_history`: the S19 unknown reasons
`fingerprint_unsupported_kind`, `extraction_failed_before`, `extraction_failed_after`,
`definition_occurrence_ambiguous`, `source_unavailable`, `declaration_node_not_found`,
`parse_error_in_declaration`, `uncovered_source_text`, `unmapped_declaration_child`,
`non_utf8_declaration`, `inconsistent_evidence`, and `path_partial`, `comparison_rejected`,
`correspondence_not_confirmed`, `sibling_ordinal_shift`. Drift reasons of `unresolved_mapping`:
`target_removed`, `document_not_in_snapshot`.

Limitations carried by every document:

- `review_signal_is_an_observation_not_a_verdict`
- `whole_document_digest_any_edit_suppresses_the_review_signal`
- `format_and_location_only_changes_raise_no_signal_and_can_mask_comment_pragmas`
- `same_identity_recreation_is_indistinguishable_from_an_edit_and_ordinal_shifts_need_siblings`
- `renamed_or_moved_targets_are_reported_unresolved_and_never_rebound`
- `documents_are_read_from_the_snapshot_never_the_checkout`
- `document_not_in_snapshot_covers_absent_and_present_but_non_indexed_documents`
- `file_targets_compare_whole_file_digests`
- `ids_embed_the_absolute_checkout_path_and_are_not_portable`
- `decision_and_mapping_ids_are_operator_labels`
- `comparison_rejection_reports_every_mapping_as_unknown_target_history`
- `not_written_to_the_graph_and_not_exposed_over_mcp`

## Commands

`atlas-aci rationale-map --mapping FILE --repo DIR [--graph-root DIR] --output FILE [limits]`

`atlas-aci rationale-drift --mapping FILE [--repo DIR] [--before-repo DIR] [--after-repo DIR]
[--graph-root DIR] [--before-graph-root DIR] [--after-graph-root DIR] --output FILE [limits]`

`--repo`, `--before-repo`, `--after-repo`, `--graph-root`, `--before-graph-root`,
`--after-graph-root`, `--mapping` and `--output` are the options; the S19 side grammar applies to
the drift command (`--repo`, or both `--before-repo` and `--after-repo`; a side's graph root falls
back to `--graph-root`, then to `<repo>/.atlas`). The before side must be the mapped snapshot.

| Limit | Minimum | Maximum | Default |
| --- | --- | --- | --- |
| `--max-mapping-bytes` | 1 | 16777216 | 1048576 |
| `--max-mappings` | 1 | 10000 | 1000 |
| `--max-output-bytes` | 1 | 67108864 | 16777216 |

Limits never change a result, they only reject. Unresolved mappings, unknown targets and a
rejected comparison are results: exit 0.

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| A limit is not an integer in range | 2 | `Invalid rationale option: <name>` |
| No repository for a side | 2 | `Give --repo, or both --before-repo and --after-repo.` |
| `--output` inside a repository, its `.atlas` or a graph root | 2 | `--output must be outside the repositories and graph roots.` |
| `--output` is the mapping | 2 | `--output must differ from the mapping.` |
| A repository is not a usable namespace | 2 | `The repository cannot be opened as a graph namespace.` |
| Both sides are one namespace | 2 | `Both sides resolve to the same graph namespace.` |
| The mapping cannot be read (missing, a FIFO, a directory) | 1 | `Rationale mapping cannot be read.` |
| A rejection | 1 | `Rationale mapping rejected: <reason>` or `Rationale drift rejected: <reason>` |
| The output cannot be written | 1 | `Rationale output cannot be written.` |
| Anything else | 1 | `Rationale mapping failed: <TypeName>` or `Rationale drift failed: <TypeName>` |

On exit 1 and 2 nothing is printed on stdout and no output file is created or replaced. The
output is written atomically (a temporary `.atlas-rationale-*` file, then a rename).

## What to know before relying on it

- It flags a possible review; it does not judge a decision. A comment pragma, a pure formatting
  edit or a location-only move raises no signal, and any edit to the ADR, however small, silences
  it for that mapping (the whole-document digest).
- Index the ADRs: the default languages include markdown; with `--langs` that omit it every
  mapping is `document_not_in_snapshot`. The remedy is to index with markdown enabled.
- Snapshot and generation ids embed the absolute checkout path: replays are byte-identical at the
  same checkout path only, and the documents are not portable between machines.
- The result is not stored in the graph and not exposed to agents; read the files you wrote.
