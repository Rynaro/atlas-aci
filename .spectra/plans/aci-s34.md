---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T00:00:00Z
slice_id: ACI-S34
issue: https://github.com/Rynaro/atlas-aci/issues/55
---
# ACI-S34 - explicit ADR mappings and source-change review signals

## Scope

Issue #55 on base `0a70041` (main after PR #99; S01-S33 and S37 merged), branch `aci-s34`.
- The marker `<!-- atlas-aci:slice:ACI-S34 -->` is verified.
- Criteria digest `3b760db2423318e36b574d32c556e9dbd442a0bc169f6777d520522086298a8b` (7 product ACs plus RUN-01/02).
- Risk P2. Timebox 2 engineering days. Rightsize tier `lite` (score 4); an independent checker still runs.

**Dependency S19 (#40) is PRESENT.** Receipt `.spectra/state/aci-s19.execution.json` is COMPLETE. `M/` = `mcp-server/src/atlas_aci/`.
- `M/query/diff.py` provides:
  - `compare(before, after)` (:402);
  - `Side` (:73), `DiffRejected(reason)` (:60), `DiffUsageError` (:68);
  - `CATEGORIES` (:34), `REJECTIONS` (:35-43), `UNKNOWN_REASONS` (:44-56, 11 values).
- `compare` holds its own two leases and releases them on every path (:414-434).
- `compare` skips `kind=="file"`, `scip_*` and `coverage_test` entities (:108).
- Correspondence is by identical `entity_id` only. `candidates` (:343-399) is a separate list.
- `unchanged` is only counted (:533, :552). Absence from `changes` therefore does not show that a target is present.

Observed anchors (re-verified at planning):
- **Lease.** `runtime.open_active(stack, Side)` (`M/overlays/runtime.py:227-242`) maps S19 lease errors to `OverlayRejected` with `index_unavailable`, `source_snapshot_unavailable` or `index_upgrade_required`. `DiffUsageError` passes through.
  - Epoch precedent: `revision_vector.open_consumer` (:347-360) requires `_lease_schema_epoch == max(runtime.SCHEMA_EPOCHS)` and an equal `snapshot_id`.
- **Concurrent re-index precedents.**
  - `counterfactual/analyze.py:695-708` `_verify_base` takes a fresh lease and compares (snapshot_id, generation_id), failing with `base_changed`.
  - `query/impact.py:211-236` composes S19 internals on held leases instead.
- **Evidence tables** are `(id, path, payload)` (`storage/evidence.py:24-28`).
  - The definition occurrence predicate is `kind=="definition"`, `provenance_state=="source"` and `role=="definition:def."+kind` (diff.py:115-124).
  - The file entity id is `ev.file_entity(path, language)["id"]` (evidence.py:1111). Existence is checked with `runtime.ENTITY_SQL` (:79).
  - Observations come from `runtime.OBSERVATIONS_SQL` (:78), records with `type=="observation"`. `_source_lease.read(path, sha)` returns bytes whose sha256 has been checked.
  - A failed path's rows are deleted (S19 checker condition 1). S19 then emits one-sided `unknown` records with `extraction_failed_after` or `_before` (test_s19_semantics.py:143-167).
- **Helpers.**
  - `revision_vector.load_strict(raw, numbers=True)` (:123): no BOM, no duplicate keys, no NaN, integers of at most 20 digits.
  - `runtime.canonical_bytes` (:219), `runtime.resolve_output` (:866-889).
  - `ev.canonical` (:229), `ev.hash_value` (:259), `ev.relative_path` (:263), `ev.RELATIONS` (:45, 8 members including `rationale_for`).
- **CLI helpers.** S19 side grammar at `__main__.py:564-622`. `_read_cross_repo_input` (:1611) is a bounded `O_NONBLOCK` read of regular files only. The atomic-write pattern is `_write_history_atomic` (:1962).
- **Real triggers** already used by S19 tests:
  - raw_text yaml, markdown or scss edit → `unknown/fingerprint_unsupported_kind` (test_s19_languages.py:730-781);
  - `def keep(:` → `extraction_failed_after`;
  - differing `langs` → `configuration_mismatch` (test_s19_semantics.py:207-216);
  - git vs non-git → `identity_kind_mismatch` (test_s19_contract.py:259).
- **Pins.**
  - Tools JSON: 7 tools, `94f9567a…ac56`, 1631472 bytes. Budgets 3436/3448/797/7730.
  - `__main__.py` top-level imports must stay lazy (test_s33_isolation.py:381-390, test_s37:1192).
  - CHANGELOG Unreleased/Added: bullets[0] is S30 (test_s30_isolation.py:563-565). bullets[1] is S33 and `bullets[2]` must exist (test_s33_isolation.py:436-441).
  - Locator titles found by `unreleased.index(...)` (S27, S28, S29) must not appear earlier in the file.
  - README `readme[after_links+1]` must be the history-signals row (test_s33_isolation.py:461-464). README must never contain `canary-missions.md` (test_s28:344).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; stricter option; recorded 2026-11-05)
1. **Architecture: scout design A.**
   - The operator input is `atlas-rationale-mapping.v1`: strict JSON with closed keys.
   - `rationale-map` writes `atlas-rationale-overlay.v1`. `rationale-drift` writes `atlas-rationale-drift.v1`. Each output is standalone, content-addressed and written atomically to `--output` only.
   - New package `rationale/{__init__,adr_mapping,drift}.py`, plus two lazily imported commands in `__main__.py`.
   - No gated-path edit (D11). The `rationale` table and the `rationale_for` relation are not touched and not reused. `ev.RELATIONS` is unchanged. No MCP change.
2. **ADR source.** ADR bytes are read ONLY from the stated snapshot's pack, never from the live checkout.
   - Document digest = sha256 of the snapshot bytes. It is declared in the mapping and checked.
   - The span is a byte range plus a declared `span.sha256`. The decision id uses the operator label grammar.
   - Targets are named by exact `entity_id` plus the descriptors {path, kind, name}, which must equal the stored row.
   - Resolution is by id only: there is never a name search and never a rebind to an S19 candidate. Candidate ids are attached for visibility only.
3. **File targets are supported (D6).** Their state comes from each side's `observed_source_sha256` (the manifest key S19 reads at diff.py:101). S19 emits no file records.
   - Refinement: a changed file reports `source_state: content_changed`, a review trigger equal to `body_changed`.
4. **Drift semantics (D5).**
   - Takes the raw mapping and re-resolves it against the before side, which must be the mapped snapshot.
   - Calls `compare` once. Both sides are leased via `open_active`.
   - Race checks:
     - success: the change-set before/after (snapshot_id, generation_id) must equal the held leases;
     - `DiffRejected` (refinement): a fresh-lease re-check of both sides.
     - Either failure → reject `snapshot_changed`.
   - Review signal: `rationale_review_needed` iff source_state ∈ {signature_changed, body_changed, content_changed} AND the whole-file ADR digest is identical on both sides.
   - No finding when the document changed, for `formatting_only`/`location_only`, or when the target is unchanged. Unchanged also requires the target to be present on both sides.
   - An S19 unknown record or a `partial_paths` hit takes precedence over removal.
   - `DiffRejected` → the drift document is still written: every resolved mapping is `unknown_target_history`, reason `comparison_rejected`, detail = the S19 reason, exit 0. `DiffUsageError` → exit 2.
   - A target removed on the after side → `unresolved_mapping target_removed`. The ADR absent on the after side → `unresolved_mapping document_not_in_snapshot`.
5. **Vocabularies (D8).** Closed and documented. No verdict or compliance words: none of the banned set, and not followed, violated or invalidated.
6. **Determinism and read-only.** Canonical bytes, versioned ids, sorted lists, no timestamps and no absolute paths in outputs. Replay holds at the same checkout path. AC-007 inventories cover every command, including rejection paths.
7. **Scope amendment.**
   - Adds `rationale/__init__.py`, `test_s34_*.py` and `fixtures/adr_mapping/**`.
   - Adds a CHANGELOG bullet placed THIRD and a README row placed directly after the `docs/history-signals.md` row.
   - No prior test is edited.
8. **Invariants.** Tools JSON and budgets unchanged; `ev.RELATIONS` unchanged; no dependency and no subprocess; no recursion, no `\d` and no `$` in any regex; no `alter table` and no `nx.compose(`; no interpreter-dependent behaviour (CI 3.14, images 3.12).

### In
1. `rationale/adr_mapping.py`:
   - constants: `MAPPING_FORMAT`, `OVERLAY_FORMAT`, `SUMMARY_FORMAT`, `PRODUCER`, `LINK_KIND`, `REJECTIONS`, `LEASE_REASONS`, `IMPORT_REASONS`, `LIMITS`, `LIMITATIONS`, key tuples;
   - errors: `RationaleRejected(reason)`, `RationaleUsageError(what)`;
   - functions: `parse_mapping`, `open_mapped`, `resolve`, `build_overlay`, `map_rationale`, `validate_overlay`, `summary_document`, `validate_summary`.
2. `rationale/drift.py`:
   - constants: `DRIFT_FORMAT`, `REVIEW_RULE`, `SOURCE_STATES`, `DECISION_STATES`, `OUTCOMES`, `FINDING_KINDS`, `HISTORY_REASONS`, `DRIFT_REASONS`, `STAGES`, `DRIFT_REJECTIONS`;
   - functions: `rationale_drift`, `validate_drift`.
3. `rationale/__init__.py` (docstring only). In `__main__.py`: the `rationale-map` and `rationale-drift` commands, `_RATIONALE_LIMITS` and `_write_rationale_atomic`, all additive.
4. Tests:
   - `test_s34_adr_drift.py` (the 7 named tests);
   - `test_s34_support.py`, `test_s34_states.py`, `test_s34_rejections.py`, `test_s34_isolation.py`.
   - Fixtures (JSON only): `tests/fixtures/adr_mapping/{world.json, mapping.json, mapping-unresolved.json, expected.json}`.
5. `docs/rationale-drift.md`, the CHANGELOG bullet and the README row.

### Out
- LLM or prose-similarity ADR discovery.
- Compliance or "decision followed/invalidated" claims.
- Editing ADRs, source or statuses.
- Graph tables or `ev.RELATIONS` entries; any MCP op.
- An `--adr-root` (live checkout) option.
- Span-level digests on the after side.
- Rename following.
- Git blob ids.
- scip/coverage_test targets.

## Approach

### Decisions

**D1 Mapping input `atlas-rationale-mapping.v1`.** Parsed with `load_strict(raw, numbers=True)`. Every key below is required, and the key sets are closed at every level.
```
{"format":"atlas-rationale-mapping.v1", "snapshot_id":HEX64,
 "mappings":[{"mapping_id":LABEL, "decision_id":LABEL,
   "document":{"path":REL, "sha256":HEX64},
   "span":{"start_byte":INT, "end_byte":INT, "sha256":HEX64},
   "target":{"entity_id":HEX64, "path":REL, "kind":KIND, "name":NAME}}]}
```
- **Grammars** (`re.fullmatch`):
  - LABEL: `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
  - HEX64: `ev.hash_value`.
  - REL: `ev.relative_path` and at most 4096 characters.
  - KIND: `[A-Za-z0-9_.:-]{1,64}`.
  - NAME: str with 1 to 1024 characters.
  - INT: int, not bool, between 0 and 2147483647.
- **Rules.**
  - `mappings` must be non-empty and `mapping_id` unique.
  - A `format` string other than the constant → `unsupported_mapping_version`.
  - Any other violation (bad JSON, BOM, duplicate key, float, extra or missing key, grammar) → `invalid_mapping`.
  - Bytes above `max_mapping_bytes` → `mapping_too_large`. More than `max_mappings` entries → `too_many_mappings`.
  - `mapping_digest` = sha256 of the raw bytes.

**D2 Lease (`open_mapped`).**
- `open_active(stack, Side(repo, graph_root))`. Then `_lease_schema_epoch == max(SCHEMA_EPOCHS)`, else `index_upgrade_required`.
- `reader.snapshot_id == mapping.snapshot_id`, else `mapping_snapshot_mismatch`.
- `OverlayRejected(r)` → `r` ∈ `LEASE_REASONS` (`index_unavailable`, `source_snapshot_unavailable`, `index_upgrade_required`). Anything else → `index_unavailable`.
- `DiffUsageError` → `RationaleUsageError("graph_namespace")`.
- One `ExitStack` and one reader per side, held from resolution to assembly.
- Every SQL block maps `sqlite3.Error` and malformed payload JSON to `index_unavailable`.

**D3 Resolution (`resolve(reader, parsed)`).** Each mapping is checked in this order. The first failure yields one `unresolved_mapping` record with that reason.
- Documents are read once per path and cached.
- SQL literals are exactly `ENTITY_PAYLOAD_SQL = "SELECT payload FROM entities WHERE id=?"` and `OCCURRENCES_BY_PATH_SQL = "SELECT payload FROM occurrences WHERE path=?"`, plus `runtime.OBSERVATIONS_SQL`.

| # | Check (on the mapped snapshot only) | Reason |
| --- | --- | --- |
| R1 | An observation `type=="observation"` at `document.path` with `state=="indexed"` and a hash value | `document_not_in_snapshot` |
| R2 | `observed_source_sha256 == document.sha256` | `document_digest_mismatch` |
| R3 | bytes = `_source_lease.read(path, sha)`; ValueError, OSError, RuntimeError or KeyError → whole-run rejection `source_snapshot_unavailable`; `sha256(bytes) == document.sha256` | (rejection) |
| R4 | `start_byte < end_byte <= len(bytes)` | `span_out_of_range` |
| R5 | `bytes[start:end].decode("utf-8")` in strict mode | `span_not_utf8` |
| R6 | `sha256(bytes[start:end]) == span.sha256` | `span_digest_mismatch` |
| R7 | An entity row with `id==entity_id` exists, AND its path has an indexed observation | `target_not_found` |
| R8 | kind is not `scip_*` and not `ev.COVERAGE_TEST_KIND` | `target_kind_unsupported` |
| R9 | row `path`, `kind` and `name` each `==` the declared value (exact; no name search exists anywhere) | `target_descriptor_mismatch` |
| R10 | Symbol (kind != `file`): exactly one occurrence on the row path with `entity_id==id` and the D-anchor definition predicate. File: `id == ev.file_entity(path, row.language)["id"]` | `target_definition_ambiguous` |

- R10 is pure: `_definition(payloads, entity)`. An isolation test checks it against `symbol_diff._Loaded.definition` on the fixture snapshot.
- `start_line = bytes.count(b"\n", 0, start) + 1` and `end_line = bytes.count(b"\n", 0, end - 1) + 1`.

**D4 Overlay `atlas-rationale-overlay.v1`** (`map_rationale`). Canonical JSON plus LF.
```
{format, id, producer:{name:"atlas-aci rationale-map", version:"atlas-rationale-map.v1"},
 link_kind:"operator_declared_rationale_link", mapping_digest,
 snapshot:{snapshot_id, generation_id}, counts:{mappings, resolved, unresolved},
 resolved:[R], unresolved:[U], limitations:[LIMITATIONS]}
```
- **Resolved record R:**
  ```
  {id, type:"rationale_mapping", mapping_id, decision_id, document:{path, sha256},
   span:{start_byte, end_byte, start_line, end_line, sha256},
   target:{entity_id, target_class:"symbol"|"file", path, kind, name, language, owners, ordinal,
           occurrence_id|null, start_byte|null, end_byte|null, source_sha256},
   snapshot_id, generation_id}
  ```
  - `source_sha256` is the path's `observed_source_sha256`.
  - The occurrence fields are null for a file target.
- **Unresolved record U:** `{id, type:"unresolved_mapping", stage:"import", mapping_id, decision_id, reason, document:{path, sha256}, span:{start_byte, end_byte, sha256}, target:{entity_id, path, kind, name}, snapshot_id}`. This echoes the declared input, so nothing is hidden.
- **Ids.**
  - Record id = `sha256("atlas-rationale-record.v1:" + canonical(record minus id))`.
  - `id` = `sha256(OVERLAY_FORMAT + ":" + canonical(rest))`.
  - Neither embeds `mapping_digest`, so a reordered mapping file gives identical record ids.
- Both lists are sorted by `(decision_id, mapping_id)`. `mappings == resolved + unresolved`.
- No key `relation` and no value in `ev.RELATIONS` anywhere.
- Canonical size above `max_output_bytes` → `output_too_large`.

**D5 Drift `atlas-rationale-drift.v1`** (`rationale_drift(raw, before, after, limits)`). Pipeline (the first rejection wins; nothing is written on a rejection):

| # | Stage | Failure |
| --- | --- | --- |
| 1 | D1 parse | `mapping_too_large`, `too_many_mappings`, `invalid_mapping`, `unsupported_mapping_version` |
| 2 | ExitStack: D2 on before (mapped snapshot), `open_active` plus the epoch check on after; `before.atlas_dir == after.atlas_dir` → usage `same_namespace` | `before_`/`after_` + LEASE_REASONS, `mapping_snapshot_mismatch` |
| 3 | D3 resolve on before; `build_overlay` (gives `overlay_id`) | as D3/D4 |
| 4 | `symbol_diff.compare(before_side, after_side)` once. `DiffUsageError` → usage `graph_namespace`. `DiffRejected(r)` → comparison `{state:"rejected", change_set_id:null, reason:r}`. Any other exception → `comparison_failed` | `comparison_failed` |
| 5 | Race check. Compared: change-set `before` and `after` ids equal the held leases. Rejected: a fresh `_open`/`_lease` of each side gives the held (snapshot_id, generation_id) (`_verify_base` pattern). Any exception or mismatch fails | `snapshot_changed` |
| 6 | Evaluate each resolved mapping (D6) using the held after reader and the change set | `after_source_snapshot_unavailable` (doc read) |
| 7 | Assemble; `validate_drift`; size check | `output_too_large`, `invalid_drift` |

Document:
```
{format, id, producer:{name:"atlas-aci rationale-drift", version:"atlas-rationale-drift.v1"},
 mapping_digest, overlay_id, review_rule:"source_signature_body_or_content_changed_and_document_digest_unchanged",
 before:{snapshot_id, generation_id}, after:{snapshot_id, generation_id},
 comparison:{state:"compared"|"rejected", change_set_id|null, reason|null},
 counts:{mappings, resolved, unresolved_at_import, rationale_review_needed,
         unknown_target_history, unresolved_mapping, no_finding},
 results:[RES], findings:[F], limitations:[LIMITATIONS]}
```
- **Result RES** (one per mapping, including import-unresolved ones):
  ```
  {mapping_id, decision_id, stage:"import"|"comparison", target_entity_id, target_class|null,
   document_path, source_state, decision_state, outcome, reason|null, detail|null,
   change_record_id|null, candidate_ids:[...], target_source_sha256_before|null,
   target_source_sha256_after|null, document_sha256_before|null, document_sha256_after|null}
  ```
- **Finding F** = the projection of every RES whose `outcome != "no_finding"`: `{id, kind:outcome, mapping_id, decision_id, target_entity_id, document_path, reason, detail, change_record_id, candidate_ids}`, with `id = sha256("atlas-rationale-finding.v1:"+canonical(rest))`.
- Results and findings are sorted by `(decision_id, mapping_id)`.
- `id = sha256(DRIFT_FORMAT+":"+canonical(rest))`.
- **Count invariants:**
  - `mappings = resolved + unresolved_at_import`;
  - `mappings = review + unknown + unresolved_mapping + no_finding`;
  - `unresolved_mapping >= unresolved_at_import`;
  - `len(findings) = mappings - no_finding`.
- `candidate_ids` = sorted ids of S19 candidates with `before_entity_id == target`. They are never used to look anything up.

**D6 Per-mapping evaluation.** The first matching row wins. A symbol target's S19 record is the unique `changes` record with `entity_id == target` (`change_record_id` = its `id`).

| # | Condition | outcome / reason (detail) | source_state |
| --- | --- | --- | --- |
| E0 | Import-unresolved | `unresolved_mapping` / D3 reason; stage `import`; decision_state `not_compared` | `not_compared` |
| E1 | comparison `rejected` | `unknown_target_history` / `comparison_rejected` (S19 reason); decision_state `not_compared` | `unknown` |
| E2 | Symbol: record category `unknown`. File: after observation `state=="failed"` | `unknown_target_history` / record `reason`, or `extraction_failed_after` for a file | `unknown` |
| E3 | Target path ∈ `partial_paths` | `unknown_target_history` / `path_partial` | `unknown` |
| E4 | Symbol: record `removed`. File: no after observation AND no after file entity | `unresolved_mapping` / `target_removed` | `removed` |
| E5 | Symbol: record `added`; or no record and the entity is absent on after or lacks exactly one definition. File: exactly one of observation/entity present on after, or a non-indexed non-failed state | `unknown_target_history` / `correspondence_not_confirmed` | `unknown` |
| E6 | Symbol: record `signature` / `body` / `format` / `location` → `signature_changed` / `body_changed` / `formatting_only` / `location_only`; no record and present with one definition → `unchanged`. File: both indexed and the entity present; equal sha → `unchanged`, else `content_changed` | (go to E7) | as stated |
| E7 | After document observation absent or not indexed | `unresolved_mapping` / `document_not_in_snapshot` | E6 value |
| E8 | source_state ∈ REVIEW_STATES and decision_state `document_unchanged` | `rationale_review_needed` | E6 value |
| E9 | otherwise | `no_finding` | E6 value |

- **decision_state** (from E2 on): read the after observation at `document_path`.
  - Absent, or not `indexed` → `document_missing`.
  - Otherwise read the bytes through the after lease and compute `sha256(bytes)`. Equal to the mapped digest → `document_unchanged`, else `document_changed`.
- `stage` is `comparison` for E1-E9.
- Before/after sha fields come from the S19 refs, or for an unchanged symbol or a file target from the lease observations.

**D7 Legality table (`validate_drift`).**

| outcome | source_state | decision_state | reason / detail |
| --- | --- | --- | --- |
| `rationale_review_needed` | {signature_changed, body_changed, content_changed} | document_unchanged | null / null |
| `no_finding` | {unchanged, location_only, formatting_only}, or review states | document_unchanged (non-review states only) or document_changed | null / null |
| `unknown_target_history` | unknown | not_compared iff `comparison_rejected` (then detail ∈ `symbol_diff.REJECTIONS`); else document_* | ∈ HISTORY_REASONS |
| `unresolved_mapping` (import) | not_compared | not_compared | ∈ IMPORT_REASONS / null |
| `unresolved_mapping` (comparison) | removed for `target_removed`; else a compared state for `document_not_in_snapshot` (then decision_state document_missing) | any compared value | ∈ DRIFT_REASONS / null |

`validate_overlay`, `validate_drift` and `validate_summary` check:
- closed keys at every level;
- constants, grammars and vocabulary membership;
- recomputed record, finding and document ids;
- sort orders and count invariants;
- that findings equal the projection of results;
- that `overlay_id` and `comparison.change_set_id` are hash values or null as legal;
- `limitations == LIMITATIONS`;
- no `relation` key and no `ev.RELATIONS` value.

The library calls them before returning (`invalid_overlay` / `invalid_drift`).

**D8 Closed vocabularies.**
- `REJECTIONS` (map, 10): `mapping_too_large, too_many_mappings, invalid_mapping, unsupported_mapping_version, index_unavailable, source_snapshot_unavailable, index_upgrade_required, mapping_snapshot_mismatch, output_too_large, invalid_overlay`.
- `DRIFT_REJECTIONS` (17): `mapping_too_large, too_many_mappings, invalid_mapping, unsupported_mapping_version, before_index_unavailable, before_source_snapshot_unavailable, before_index_upgrade_required, after_index_unavailable, after_source_snapshot_unavailable, after_index_upgrade_required, mapping_snapshot_mismatch, snapshot_changed, comparison_failed, output_too_large, invalid_overlay, invalid_drift, source_snapshot_unavailable`.
  - The last value is listed only for completeness. The before-side reads in D3 map to `before_source_snapshot_unavailable` in drift. The final tuple drops any duplicate; the docs test pins the exact tuple.
- `IMPORT_REASONS` (9): `document_not_in_snapshot, document_digest_mismatch, span_out_of_range, span_not_utf8, span_digest_mismatch, target_not_found, target_kind_unsupported, target_descriptor_mismatch, target_definition_ambiguous`.
- `DRIFT_REASONS`: `target_removed, document_not_in_snapshot`.
- `HISTORY_REASONS` (14): the 11 S19 `UNKNOWN_REASONS` written as literals (a test asserts they are a superset of S19's), plus `path_partial, comparison_rejected, correspondence_not_confirmed`.
- `SOURCE_STATES`: `unchanged, location_only, formatting_only, signature_changed, body_changed, content_changed, removed, unknown, not_compared`.
- `REVIEW_STATES`: `signature_changed, body_changed, content_changed`.
- `DECISION_STATES`: `document_unchanged, document_changed, document_missing, not_compared`.
- `OUTCOMES`: `rationale_review_needed, unknown_target_history, unresolved_mapping, no_finding`.
- `FINDING_KINDS`: the first three outcomes.
- `STAGES`: `import, comparison`. `TARGET_CLASSES`: `symbol, file`. `COMPARISON_STATES`: `compared, rejected`.
- `USAGE_NAMES`: `max_mapping_bytes, max_mappings, max_output_bytes, output_location, output_equals_input, graph_namespace, same_namespace, repository`.
- `LIMITATIONS` (12):
  1. `review_signal_is_an_observation_not_a_verdict`
  2. `whole_document_digest_any_edit_suppresses_the_review_signal`
  3. `format_and_location_only_changes_raise_no_signal_and_can_mask_comment_pragmas`
  4. `same_identity_recreation_is_indistinguishable_from_an_edit`
  5. `renamed_or_moved_targets_are_reported_unresolved_and_never_rebound`
  6. `documents_are_read_from_the_snapshot_never_the_checkout`
  7. `documents_must_be_observed_and_indexed_in_the_snapshot`
  8. `file_targets_compare_whole_file_digests`
  9. `ids_embed_the_absolute_checkout_path_and_are_not_portable`
  10. `decision_and_mapping_ids_are_operator_labels`
  11. `comparison_rejection_reports_every_mapping_as_unknown_target_history`
  12. `not_written_to_the_graph_and_not_exposed_over_mcp`
- No constant, key or value contains a banned token (`safe, verified, proven, guaranteed, compliant, compatible, incompatible, breaking, applied`), nor `followed`, `violated` or `invalidated`. None equals an `ev.RELATIONS` member.

**D9 Limits.** Outside every id; never change content, only reject. Format default/min/max; a bool or an out-of-range value → usage.
- `max_mapping_bytes` 1048576/1/16777216
- `max_mappings` 1000/1/10000
- `max_output_bytes` 16777216/1/67108864

**D10 CLI.** Both commands are lazy imports inside the function body; no top-level import line mentions `rationale`. `--repo` and the side repos are `click.Path(exists=True, file_okay=False)` and are resolved.
- **`atlas-aci rationale-map`**: `--mapping FILE --repo DIR [--graph-root DIR] --output FILE [--max-mapping-bytes N] [--max-mappings N] [--max-output-bytes N]`.
  - Protected outputs: repo, repo/.atlas, graph root. Input: the mapping.
- **`atlas-aci rationale-drift`**: `--mapping FILE [--repo DIR] [--before-repo DIR] [--after-repo DIR] [--graph-root DIR] [--before-graph-root DIR] [--after-graph-root DIR] --output FILE [limits]` (S19 grammar).
  - Protected outputs: both repos, both `repo/.atlas`, all given graph roots.
- **Mapping read.** `_read_cross_repo_input(path, max_mapping_bytes, "Rationale mapping cannot be read.")`, reused unchanged. Reading `limit+1` bytes detects `mapping_too_large`.
- **Output.**
  - `runtime.resolve_output` before any lease.
  - `_write_rationale_atomic`: mkstemp prefix `.atlas-rationale-`, fsync, `os.replace`, unlink on failure.
  - stdout is `atlas-rationale-summary.v1` = `{format, command, document_format, document_id, mapping_digest, counts}`, canonical plus LF.
  - Unresolved, unknown and comparison-rejected outcomes all exit 0.

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| A limit is a bool, 0 or above max | 2 | `Invalid rationale option: <name>` |
| No repo for a side | 2 | `Give --repo, or both --before-repo and --after-repo.` |
| `--output` inside a protected root | 2 | `--output must be outside the repositories and graph roots.` |
| `--output` equals the mapping | 2 | `--output must differ from the mapping.` |
| Namespace unusable / both sides the same | 2 | `The repository cannot be opened as a graph namespace.` / `Both sides resolve to the same graph namespace.` |
| Mapping unreadable (FIFO, directory, missing) | 1 | `Rationale mapping cannot be read.` |
| Rejection | 1 | `Rationale mapping rejected: <reason>` / `Rationale drift rejected: <reason>` |
| Write failure | 1 | `Rationale output cannot be written.` |
| Other | 1 | `Rationale mapping failed: <TypeName>` / `Rationale drift failed: <TypeName>` (never `str(exc)`) |

**D11 Gated paths** (import only; no edit): `server.py, enforcement.py, config.py, codegraph.py, protocol/, query/, tools/, storage/, extractors/, providers/, indexing/, resolvers/, frameworks/, architecture/, counterfactual/, contracts/, importers/, overlays/, dependencies/, cross_repo/, history/, reports/`, `pyproject.toml`, `uv.lock`, `Dockerfile*`, `.github/workflows/`.
- Package import allowlist: `__future__, hashlib, json, re, sqlite3, collections.abc, contextlib, dataclasses, pathlib, typing, atlas_aci.overlays.runtime, atlas_aci.query.diff, atlas_aci.storage.evidence, atlas_aci.cross_repo.revision_vector, atlas_aci.rationale.adr_mapping`.
- No `os`, `subprocess`, `tempfile`, `open(` or network import.
- `rationale/` is outside `_producer_inputs`, so generation ids cannot churn.

### Work plan (timebox 2 engineering days)
**Day 1 AM: qualification.** `.spectra/evidence/aci-s34/qualify.py` (run with `uv run --frozen python`) writes `qualification.json`. It builds the `world.json` pair with `same_path_pair`.
- (a) **Categories.** The S19 `compare` category and reason for each fixture target:
  - body (`charge`), signature (`refund`), format (comment inside `audit`), location (comment line above `post`);
  - removed (`retire`, with `src/legacy.py` deleted), and a rename candidate (`compute_total` → `calculate_total`, body of at least `MIN_CANDIDATE_TOKENS` tokens);
  - raw_text yaml `max_charge` → `unknown/fingerprint_unsupported_kind`;
  - `def keep(:` → `extraction_failed_after` with `partial_paths`;
  - `summary` absent from `changes` and counted unchanged.
- (b) **ADR in the snapshot.** The three markdown ADRs are observed `indexed` under default langs. `_source_lease.read` returns bytes whose sha256 equals the observation sha and equals the `world.json` bytes. Spans are recorded with their digests, including a span that splits the multi-byte `é`. Deleted ADR-0003 is absent from the after observations.
- (c) **Path-independent ids.** Build the before world at two different tmp paths. Every target `entity_id` is equal; record them as literals. The file entity id equals `ev.file_entity(path, language)["id"]` and `ENTITY_SQL` finds it.
- (d) **File targets.** The `observed_source_sha256` of `src/settings.py` (changed), `src/constants.py` (unchanged), `src/fragile.py` (`failed` after) and `src/legacy.py` (absent after) on each side, plus file-entity presence.
- (e) **DiffRejected triggers.** After side built with `langs` without yaml → `configuration_mismatch`. After side built with `.git` moved aside → `identity_kind_mismatch`.
- (f) The D3 R10 predicate equals `_Loaded.definition` for every symbol target.
- (g) **Concurrent leases.** `open_active` held on both sides plus `compare` succeeds. A re-index of the after root while the lease is held: held-lease reads still succeed and `compare` reports the new after generation id.
- (h) Wiping and rebuilding both roots at the same paths gives equal snapshot and generation ids.
- (i) Timing: one pair build, one `compare`, and the projected s34 suite time.
- (j) Base invariants: tools sha and bytes, budgets, `ev.RELATIONS`, the full suite (test_s08 nonregular_pack deselected), and the README/CHANGELOG pins as read above.

**STOP RULE** (return to the orchestrator; never adapt silently):
- (b) ADR not indexed under default langs, or a digest disagreement;
- (c) entity ids differ across checkout paths, or the file-entity id rule does not hold;
- (e) no real `DiffRejected` trigger;
- (g) concurrent leases fail, or a re-index breaks a held lease;
- (f) the predicate disagrees with `_Loaded.definition`.

Recorded adaptations (not stops):
- (a) a category differs from expectation → change only that fixture edit to one that gives the category, and record it;
- (h) ids differ on rebuild → drop the rebuild replay variant and record it;
- (i) a pair build over 10 s → share module-scoped worlds more aggressively.

**Day 1 PM:** D1-D4, `rationale-map`, the support worlds, AC-001, AC-004 (import part), the map rejections.
**Day 2 AM:** D5-D7, `rationale-drift`, AC-002, 003, 005, 006 and 007, `test_s34_states.py`, `test_s34_isolation.py`.
**Day 2 PM:** docs, CHANGELOG and README; gates; py314; production image; RUN-01/02.

**Cut order** (each cut is listed in the receipt; no AC is affected):
1. The production-image steps beyond: twice identical, review finding, rejected comparison, rejection exit, inventory, tools pin.
2. The PYTHONHASHSEED subprocess replay variant.
3. The rebuild-at-same-path replay variant.
4. Doc and README depth beyond the scanned phrases.
5. `validate_*` tampering breadth beyond one case per D7 check.

**Never cut:**
- exact-id resolution with descriptor equality; no rebinding;
- snapshot-only ADR bytes; read-only behaviour with inventories;
- the two race checks; closed vocabularies; the validators;
- file targets (cutting them needs an orchestrator decision: STOP);
- the 7 AC tests; G-SRC and G-MCP.

### Independent checker conditions
Checker verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s34-plan-checker`, 2 MAJOR + 11 MINOR; `.spectra/plans/aci-s34.review.json`). Conditions 1-13 below are binding and override conflicting text above.

Orchestrator resolutions (stricter option wherever the checker offered a choice):
- C1: keep the tools JSON sha/bytes pin; assert no READ_ONLY_TOOLS name contains "rationale" and none of `rationale-map`, `rationale-drift`, `rationale_review_needed`, `unresolved_mapping`, `atlas-rationale-` appears in the tools JSON.
- C2 (stricter than documentation alone): ordinal renumbering is DETECTED, not only documented. When the mapped symbol has same-name siblings (same path, kind, name and owners) and the sibling count or the set of sibling entity ids differs between the before and after snapshots, the mapping is `unknown_target_history` with the new closed reason `sibling_ordinal_shift` (taking precedence over signature/body/removed), so a survivor is never compared under a renumbered id. Recreation under an identical id with no sibling change stays a documented limitation (the docs contain "ordinal" and "recreation"). Day-1 records the two-same-name-defs, delete-the-first case and a test pins `sibling_ordinal_shift`. Container (class/module) targets raising a review on any member edit is documented.
- C4: after-side decision_state uses the observation hash in any state; `document_missing` only when no observation exists.
- C6: lone surrogates and any non-strict-UTF-8 string -> `invalid_mapping`.
- C9: python, markdown and yaml grammar provisioning fails, never skips.
- C10: if the same-repo identity_kind_mismatch trigger fails on Day 1, use the S19 two-repo form (not a STOP).
- C11: assert no replacement entity id appears anywhere in the outputs.
- C13: the cut order is applied at Day-2 midday if needed (never cut: exact-id resolution, no rebinding, the sibling_ordinal_shift guard, read-only, AC tests) and recorded in the receipt.

Checker findings (verbatim):

1. **MAJOR. The tools-JSON assertion "no `rationale` string" cannot pass.**
   - Evidence: the pinned 1631472-byte JSON already contains the word. See server.py:146, server.py:238 and protocol/graph.py:238. S33's precedent only asserts the absence of "cochange" (test_s33_isolation.py:353, :358).
   - Condition: replace the assertion. Keep sha and bytes pinned. Assert that no `READ_ONLY_TOOLS` name contains "rationale". Assert that none of `rationale-map`, `rationale-drift`, `rationale_review_needed`, `unresolved_mapping`, `atlas-rationale-` appears in the tools JSON.

2. **MAJOR. Same-id replacement is wider than limitation 4 says, and it is the issue's central "never rebind" concern.**
   - Evidence: the entity id is (path, language, kind, name, owners, ordinal) with ordinals counted per same-name sibling (evidence.py:1171-1184, :1111-1121). Deleting an earlier same-name sibling renumbers the survivor. A mapping to ordinal 0 is then compared with the former ordinal 1 and yields a false `body_changed` review. Recreation under the same id behaves the same way.
   - Condition: limitation 4's literal text must name ordinal renumbering as well as recreation. The docs must contain "ordinal". Add a Day-1 qualify case (two same-name defs in one file, delete the first) and record the observed outcome in a test or docs note.
   - Also document that class and module targets raise a review on any member edit, because their body includes the members.

3. **MINOR. The AC-004 observable contradicts the fixture.**
   - Evidence: "each reason appears exactly once" conflicts with two descriptor-mismatch rows (name and path). That gives 8 mappings over 7 reachable reasons.
   - Condition: assert per `mapping_id` (each appears once in `unresolved`, with the exact expected reason) and that the reason set equals the 7 reachable IMPORT_REASONS. The pure-helper tests cover the other two.

4. **MINOR. The document-state conflation is mislabelled.**
   - Evidence: every non-history observation carries a hash in any state (evidence.py:1322; snapshots.py:864-870 stage failed and disabled blobs). The plan's after-side `document_missing` for a present but non-indexed ADR, and its E7 `unresolved_mapping/document_not_in_snapshot`, mislabel an ADR that was edited into an unparsable state.
   - Condition: compute after-side decision_state from the observation's `observed_source_sha256` in any state. Use `document_missing` only when no observation record exists. Keep R1 strict (before side requires indexed). Docs and limitation 7 must say `document_not_in_snapshot` also covers a present but non-indexed document.

5. **MINOR. The E3/E5 precedence for file targets has a dead branch.**
   - Evidence: `_failed` is `state != "indexed"` (diff.py:437-439) and feeds `partial_paths` (:456, :461). A file whose after state is unsupported or disabled therefore hits E3 `path_partial` before E5. For symbols S19 emits the fixed reason `extraction_failed_after` for any non-indexed state (:464-468).
   - Condition: state the real precedence in the D6 table. Remove or justify E5's "non-indexed non-failed" file branch, and do not claim a test for an unreachable branch.

6. **MINOR. Lone surrogates bypass `load_strict`.**
   - Evidence: JSON `"\ud800"` parses (revision_vector.py:123-141 has no surrogate check). NAME and REL are free text. The later `.encode("utf-8")` in `ev.canonical` callers raises, giving exit 1 `failed: UnicodeEncodeError` instead of `invalid_mapping`.
   - Condition: parse must strict-encode every string. Failure gives `invalid_mapping`. Add a rejection-test case.

7. **MINOR. The D8 DRIFT_REJECTIONS wording is self-contradictory.** It lists a bare `source_snapshot_unavailable` "for completeness" and then says "drops any duplicate".
   - Condition: the exact tuple has 16 values with no bare value (before-side reads map to `before_source_snapshot_unavailable`). Every value is reachable in a test, and the docs test pins the tuple.

8. **MINOR. The D7 `no_finding` row is ambiguous, and the validator path for (non-review source_state + document_changed) is unexercised.**
   - Condition: restate D7 as an explicit matrix. Add a mapping `summary-doc-edited` (ADR-0002 to `summary`: unchanged source, document_changed, `no_finding`) to the fixtures and to `expected.json`. `validate_drift` must accept it.

9. **MINOR. Grammar provisioning.**
   - Evidence: `provision_grammar` checks only python (test_s33_support.py:403-418). The S34 world needs python, markdown and yaml.
   - Condition: the S34 support fixture must `pytest.fail`, never skip, on any of the three. Do not add scss.

10. **MINOR. The `identity_kind_mismatch` trigger differs from the S19-proven form.**
    - Evidence: the proven form uses two distinct repos (test_s19_contract.py:253-259). The plan's "move `.git` aside then restore" on one repo is plausible, since acquire binds only the repository string (snapshots.py:726), but it is unproven.
    - Condition: if the same-repo variant fails Day-1 (e) but the two-repo variant (`--before-repo`/`--after-repo`) works, adapt and record it. It is not a STOP. `configuration_mismatch` stays the main trigger.

11. **MINOR. The `retire` fixture can contradict `candidate_ids []`.**
    - Evidence: S19 pairs removed and added declarations with identical signature and body when the body has at least `MIN_CANDIDATE_TOKENS` (6) tokens, including path moves (diff.py:343-399). `legacy_v2.retire` would then yield a `move` candidate.
    - Condition: make the replacement body differ or stay under 6 tokens. Otherwise pin whatever Day-1 shows. Either way assert that no replacement entity id appears anywhere.

12. **MINOR. Early regression coverage for the doc-edit pins is missing.**
    - Evidence: the CHANGELOG and README pins live in S26-S29, S23, S14 and S37. The regression command lists only S19, S30, S32, S33, thesis and export.
    - Condition: add `test_s23_isolation`, `test_s26_isolation`, `test_s27_isolation`, `test_s28_isolation`, `test_s29_openapi_diff`, `test_s14_capabilities` and `test_s37_evidence_report` to the targeted run immediately after the CHANGELOG and README edits.

13. **MINOR. Timebox risk.** Validators, tamper tests and the 5 test files are heavy for Day 2 AM.
    - Condition: apply the cut order at Day-2 midday, not at the end. Record any cut in the receipt.

## Stories
As an agent operator, I want an ADR's exact decision text linked to explicitly chosen code, so that a code change with untouched rationale is flagged for review.

As a reviewer, I want every link pinned to its snapshot, document digest and exact target id. A link that can no longer be resolved should stay visibly unresolved, and an uncomparable target should stay unknown. Neither is rebound or turned into a verdict.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s34.criteria.md` (sha256 `3b760db2…298a8b`). AC-S34-001..007 and RUN-01/02 are reproduced verbatim from that file and are not re-stated here. The executor reads the frozen file.

### Fixtures
**`world.json`.** `{"before": {path: text}, "after": {path: text|null}}`, ASCII except the one `é` in ADR-0001. Repos are written with `write_tree` and indexed with `same_path_pair(git_repo=True)`. Graph roots sit outside the repo.

| Path | Before → after | Expected |
| --- | --- | --- |
| `docs/adr/0001-pricing.md` | unchanged | document_unchanged |
| `docs/adr/0002-audit.md` | appended `## Notes` after the span | document_changed |
| `docs/adr/0003-legacy.md` | deleted | document_missing |
| `src/pricing.py` `charge` | `amount * 2` → `amount * 3` | body |
| `src/refunds.py` `refund` | parameter added | signature |
| `src/audit.py` `audit` | comment inside the body | format |
| `src/ledger.py` `post` | comment line inserted above | location |
| `src/reports.py` `summary` | unchanged | unchanged |
| `src/legacy.py` `retire` | file deleted; `src/legacy_v2.py` adds a same-name `retire` | removed |
| `src/rename.py` `compute_total` | renamed `calculate_total`, same body | removed plus candidate |
| `src/fragile.py` `keep` | → `def keep(:` | unknown extraction_failed_after |
| `config/limits.yml` `max_charge` | `10` → `20` | unknown fingerprint_unsupported_kind |
| `src/settings.py` (file) | edited | content_changed |
| `src/constants.py` (file) | unchanged | unchanged |

**`mapping.json`.** Literal Day-1 entity ids and digests, and `"snapshot_id": "@before@"`, which the test replaces with the before snapshot id. Mappings and expected outcomes (`expected.json`):

| mapping_id | Decision → target | Expected outcome |
| --- | --- | --- |
| `charge` | ADR-0001 → charge | review / body_changed |
| `refund` | ADR-0001 → refund | review / signature_changed |
| `charge-doc-edited` | ADR-0002 → charge | no_finding / body_changed / document_changed |
| `audit` | ADR-0001 → audit | no_finding formatting_only |
| `post` | ADR-0001 → post | no_finding location_only |
| `summary` | ADR-0001 → summary | no_finding unchanged |
| `summary-legacy-doc` | ADR-0003 → summary | unresolved document_not_in_snapshot |
| `retire` | ADR-0001 → retire | unresolved target_removed; candidate_ids [] |
| `rename` | ADR-0001 → compute_total | unresolved target_removed; candidate_ids [c] |
| `fragile` | ADR-0001 → keep | unknown extraction_failed_after |
| `limits` | ADR-0001 → `max_charge` | unknown fingerprint_unsupported_kind |
| `settings-file` | ADR-0001 → `src/settings.py` file | review content_changed |
| `constants-file` | ADR-0001 → `src/constants.py` file | no_finding unchanged |
| `fragile-file` | ADR-0001 → `src/fragile.py` file | unknown extraction_failed_after |
| `legacy-file` | ADR-0001 → `src/legacy.py` file | unresolved target_removed |

**`mapping-unresolved.json`.** One mapping per IMPORT_REASON reachable for real:
- span end past EOF;
- a span splitting `é`;
- a wrong span digest;
- a wrong document digest;
- `docs/adr/9999.md`;
- an absent hex id;
- the `charge` id with name `charge2`, and with path `src/other.py`.

`target_kind_unsupported` and `target_definition_ambiguous` are tested through the pure `_target_class(row)` and `_definition(payloads, entity)` helpers with fabricated rows (S30 C4 precedent). Both are declared.

The worlds are module-scoped. Extra roots:
- `root-config`: the after state built with `langs` that omit yaml;
- `root-nongit`: the after state built with `.git` moved aside and then restored.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_adr_mapping_has_exact_evidence | `rationale-map` against root-before while the live checkout holds the after state. All 15 mappings are resolved, including ADR-0003, which is deleted from the checkout: this shows the bytes come from the snapshot. Each record has `decision_id`, span (bytes, lines, sha256 == the test's own sha256 of the `world.json` slice), `document.sha256 ==` sha256 of the `world.json` bytes, `target.entity_id ==` the literal, descriptors, `snapshot_id`/`generation_id ==` the lease. Record ids, `id` and `link_kind` recompute. The overlay passes `validate_overlay`. The CLI bytes equal the library's. |
| 002 | test_adr_source_change_requests_review | `rationale-drift` before→after. The `charge`, `refund` and `settings-file` findings are `rationale_review_needed` with document_unchanged and `document_sha256_before == _after`. `change_record_id` equals the id of S19's own `compare` record (body/signature). Control: `charge-doc-edited` has no finding and `document_changed`. |
| 003 | test_adr_unchanged_source_has_no_drift_signal | The `summary` and `constants-file` results are `unchanged`/`no_finding`, and no finding names their mapping_id. `summary` is confirmed on both sides: the after lease has the entity with exactly one definition, and the before/after source sha are equal. |
| 004 | test_adr_missing_target_is_unresolved | Every `mapping-unresolved.json` reason appears exactly once in `unresolved` with type `unresolved_mapping`, the input echoed, and exit 0. Drift: `retire`, `legacy-file` and `rename` → `target_removed`; `rename.candidate_ids` == S19's candidate id; no result or finding contains the `calculate_total` or `legacy_v2.retire` entity ids; `summary-legacy-doc` → `document_not_in_snapshot`. The descriptor-mismatch row stays unresolved even though a same-name entity exists. |
| 005 | test_adr_reimport_is_idempotent | Byte-identical overlays and drift documents across: two library runs; two CLI runs to different outputs; permuted CLI options; `python -m atlas_aci` with `PYTHONHASHSEED` 1 and 2; and a rebuild of both roots at the same paths (if Day-1 (h) holds). A reordered mapping file gives equal `resolved`, `unresolved`, `results` and `findings` and equal record ids; only `mapping_digest` and the document ids differ. `overlay_id` in the drift document equals the map's `id`. |
| 006 | test_adr_incomparable_snapshot_is_unknown | Main pair: `fragile` and `fragile-file` → `extraction_failed_after` (not removed); `limits` → `fingerprint_unsupported_kind`. `root-config` and `root-nongit`: exit 0, the file is written, `comparison.state == "rejected"` with reason `configuration_mismatch` / `identity_kind_mismatch`, every stage-`comparison` result is `unknown_target_history` / `comparison_rejected` with detail = that reason and decision_state `not_compared`, and the import-unresolved results are unchanged. |
| 007 | test_adr_analysis_never_edits_decisions | A `stat_inventory` (sha256, size, mode, mtime_ns) of the repo including `.git`, every graph root, the `.atlas` dirs and the mapping file is equal before and after each of: map ok; map `mapping_snapshot_mismatch`; drift ok; drift comparison-rejected; drift `mapping_snapshot_mismatch` (before = root-after); usage with output inside the repo. The ADR bytes in the checkout equal `world.json`. Nothing appears except `--output`, and no `.atlas-rationale-*` temp file remains. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the one receipt is `.spectra/state/aci-s34.execution.json`. |

### Supporting tests
- **`test_s34_states.py`.**
  - The fixture literals equal an independent sqlite read of root-before.
  - Every `expected.json` row; the `audit` formatting_only and `post` location_only controls.
  - D6 precedence: `fragile` is unknown and not removed; `retire` + ADR-0003 combination: target_removed wins over document_missing.
  - Race checks with real re-indexes: a spy around `symbol_diff.compare` re-indexes root-after with an edit before delegating → `snapshot_changed`; the same with `langs` changed (`DiffRejected` path) → `snapshot_changed`.
- **`test_s34_rejections.py`.**
  - Every D8 rejection at library level, plus the CLI exit 1 text, empty stdout, an unchanged sentinel output and no temp file.
  - Cases: BOM, duplicate key, float, extra key, bad label/hex/path, empty `mappings`, duplicate `mapping_id`, `format` v2 → `unsupported_mapping_version`; `--max-mapping-bytes 1`; `--max-mappings 1`; `--max-output-bytes 1`; no index; `index_upgrade_required` through an `open_active` spy; `mapping_snapshot_mismatch`; a lease `read` spy (`source_snapshot_unavailable`, the only fabricated error); `comparison_failed` through a `compare` spy raising `RuntimeError`.
  - Usage (exit 2): every limit as a bool, 0 and max+1; output inside the repo, repo/.atlas or a graph root (including through a symlinked parent); output == mapping; same namespace; no repo; mapping as a FIFO or a directory → exit 1 `cannot be read`.
- **`test_s34_isolation.py`.**
  - `rationale/*.py == [__init__, adr_mapping, drift]`; ASCII; lines of at most 100 characters.
  - AST scan:
    - the import allowlist (D11);
    - no `os`, `subprocess`, `open(`, `tempfile`, `socket` or `urllib`;
    - no function calls itself;
    - the SQL literals are exactly the two D3 constants;
    - no regex with `\d`, `\w`, `\s` or `$`; no `isdigit` or `casefold`.
  - Vocabulary scan with a planted hit: module constants, output keys and output values avoid the banned set ∪ {followed, violated, invalidated} and `ev.RELATIONS`.
  - `HISTORY_REASONS ⊇ symbol_diff.UNKNOWN_REASONS`.
  - `validate_overlay`, `validate_drift` and `validate_summary` tamperings, one per D7 check, including `relation:"call"`, `rationale_for` as a value, review with document_changed, unsorted lists, a wrong count, a finding not in results, a stale id, extra keys and a limitations drift.
  - The tools JSON pin with no `rationale` string; `READ_ONLY_TOOLS` 7; the budgets; `ev.RELATIONS`; `rationale-map` and `rationale-drift` ∈ `cli.commands`.
  - Lazy imports: no top-level `rationale` import line, and the subprocess probe `'atlas_aci.rationale' not in sys.modules`. `_RATIONALE_LIMITS == LIMITS`.
  - Docs:
    - `docs/rationale-drift.md` is ASCII and names every D8 value, format, limit with its default/min/max, CLI text and option;
    - it contains the phrases "observation, not a verdict", "never rebound", "read from the snapshot", "whole-document digest", "does not write the graph", "CLI only, no MCP op", "same checkout path";
    - the CHANGELOG bullet (located by "ADR rationale review signals (ACI-S34)") is bullets[2], and bullets[0]/[1] are still S30/S33;
    - the README row (located by `docs/rationale-drift.md`) is directly after the history-signals row, contains "observation, not a verdict" and "CLI only, no MCP op", and contains none of the prior locators, `docs/history-signals.md` or `canary-missions.md`.

### Gates beyond the ACs
- **G-SRC.**
  - `git diff --exit-code 0a70041 -- <D11 paths>`.
  - `git diff --name-only 0a70041 -- mcp-server/src` ⊆ {`rationale/__init__.py`, `rationale/adr_mapping.py`, `rationale/drift.py`, `__main__.py`}.
  - `git diff 0a70041 -- mcp-server/src/atlas_aci/__main__.py | grep -c '^-[^-]'` == 0 (additive only).
  - Test changes are only `test_s34_*.py` and `fixtures/adr_mapping/**`. No prior test is edited.
- **G-MCP.** Tools JSON `94f9567a…` / 1631472; `READ_ONLY_TOOLS` 7.
- **Regression.** `test_s19*`, `test_s30*`, `test_s32*`, `test_s33*`, `test_thesis_negatives`, `test_export` are green and unchanged.
- **CI-exact lint.** `uv run --frozen ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/`.
- **Full suite** with the `test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition` deselect. No new failure or skip compared with Day-1 (j).
- **py314.**
  - `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s34_*.py tests/slices/test_s19_semantics.py tests/test_export.py -q'`.
  - Log to `.spectra/evidence/aci-s34/py314.log`.
- **Production image** (`prod-image.sh`, run by the orchestrator).
  1. Build `mcp-server/Dockerfile`.
  2. `--network none --entrypoint sh` as `atlas`, with `world.json`, `mapping.json` and `make-world.py` mounted `:ro`.
  3. Write the before state to `/tmp/w`, `git init`, then `atlas-aci index --repo /tmp/w --graph-root /tmp/gb`.
  4. Apply the after state; index `/tmp/ga`, and `/tmp/gx` with `--langs` omitting yaml.
  5. Fill the snapshot id from `atlas-aci status`.
  6. `rationale-map` twice and `rationale-drift` twice → `cmp` equal; the `charge` finding is `rationale_review_needed`.
  7. Drift against `/tmp/gx` → exit 0 with every comparison result `comparison_rejected`.
  8. A mapping with a wrong snapshot id → exit 1 with the exact text.
  9. The inventory of `/tmp/w` (including `.git`), `/tmp/gb`, `/tmp/ga`, `/tmp/gx` and the mapping is unchanged.
  10. `atlas-aci tools` matches the pin.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s34_adr_drift.py -q
uv run --frozen pytest tests/slices/test_s34_*.py -q
uv run --frozen pytest tests/slices/test_s19_*.py tests/slices/test_s30_*.py tests/slices/test_s32_*.py tests/slices/test_s33_*.py tests/test_thesis_negatives.py tests/test_export.py -q
uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/ && uv run --frozen mypy src/
```

## Confidence
To be computed with `ramza-score`.
- **Why it is low-churn.**
  - The new package only reads: two S19 leases and one public `compare`.
  - No gated, graph or MCP change, and no dependency or subprocess.
  - Every output is canonical and recomputed by a validator.
- **Main uncertainties, covered by the Day-1 STOP rule:**
  - fixture edits producing exactly the format and location categories;
  - path independence of entity ids (needed for the literal pins);
  - held leases surviving a concurrent re-index.

## Rejected Alternatives
- **Rationale records in the graph or evidence store, or reusing `rationale`/`rationale_for`.** Needs gated edits, churns snapshot ids, and mixes operator links with extracted facts.
- **Resolution by name or path+line (S27 style).** A same-name replacement would be rebound silently.
- **Following S19 rename/move candidates.** They are conservative hints, not identity.
- **Reading ADRs from the live checkout.** TOCTOU, and the document would not be the one the snapshot recorded.
- **Span-level digest equality on the after side.** Spans cannot be relocated soundly. The whole-document digest is literal to AC-002, and the false-negative trade-off is documented.
- **S20-style composition of `_Loaded`/`_build` on held leases.** Couples to more private S19 internals. `compare` plus two race checks keeps the public contract.
- **Exit 1 on `DiffRejected`.** AC-006 requires a returned `unknown_target_history` per mapping.
- **Symbol-only v1 (file targets `unsupported`).** The issue includes file identities.

## Risks (ranked)
1. **False negatives by design.** Any ADR edit suppresses the signal; format/location-only changes are silent (comment pragmas); a same-id re-creation reads as an edit. Mitigated by limitations 2-4 and by per-mapping states that are always visible.
2. **Concurrent re-index between the leases and `compare`.** Covered by the two race checks, tested with real re-indexes. Day-1 (g) is a STOP gate.
3. **Private coupling** (`_source_lease`, `_lease_schema_epoch`, `_open`/`_lease`, the definition predicate). Accepted as in S27-S33; pinned by the R10 cross-check and the regression set.
4. **Ids embed absolute paths** (snapshot/generation). Replay holds only at the same path; no golden output documents; documented.
5. **An ADR that is not indexed** (`--langs` without markdown, skip patterns) makes every mapping `document_not_in_snapshot`. Documented with a remedy.
6. **CHANGELOG/README ordering pins.** Bullet third, row after history-signals, no locator text.
7. **Timebox.** Follow the cut order; at the limit, report and propose a follow-up.
