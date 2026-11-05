---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T00:00:00Z
slice_id: ACI-S35
issue: https://github.com/Rynaro/atlas-aci/issues/56
---
# ACI-S35 - verified test-outcome feedback and a separate optional ranking overlay

## Scope

Issue #56. Base `bd6da89` (main after PR #100; S01-S34 and S37 merged). Branch `aci-s35`.
- Marker `<!-- atlas-aci:slice:ACI-S35 -->` verified.
- Criteria digest `f4d64ce3859e8ea711b23775a1e276204172ab5cf0b5fd8f5ab259f7484856c7` (7 product ACs plus RUN-01/02).
- Risk P1. Timebox 3 engineering days. Rightsize tier `lite` (score 4); an independent checker still runs.
- `M/` = `mcp-server/src/atlas_aci/`.

**Dependencies S18 (#39) and S22 (#43) are PRESENT; no interface amendment.**
- Receipts: `.spectra/state/aci-s18.execution.json` and `aci-s22.execution.json` are COMPLETE.
- **S18 record.** `graph_query op=context` returns `data.retrieval_record_id = digest(record_domain(...))` (`M/query/context.py:21-45,63-67`).
  - The domain includes `repository` = `str(reader.repo)`, which is resolved (`M/codegraph.py:886`), plus snapshot/generation ids, the ranker `atlas-context-diversity.v1` and the ordered `bundles`.
  - `M/protocol/context.py:326 validate_context(payload, "context", element_limit)` recomputes bundle, source, resource and record ids (`:364`, `:483`, `:599`). It is self-consistency only, never a snapshot check.
  - Records are not persisted by Atlas; the operator saves the tool result JSON.
- **S22 compatibility** means field names, not a consumed artifact: `run_id`, `selector{kind:"pytest_nodeid", value}`, `{snapshot_id, generation_id}`. No test-outcome artifact exists in the repository.
- **Baseline ranker = `M/query/context.py:344 context(reader, args, config, structured=True)`.**
  - This is the very function the MCP path calls (`M/tools/graph_query.py:29-32`) on `code_graph.reader_lease()` (`M/server.py:499-528`), after `validate_context` (`M/server.py:341-348`).
  - The config attributes read are `max_response_bytes`, `max_bound_field_elements` and `context_tokenizer` (`M/query/context_limits.py:72-77`, `M/query/context.py:119-335`).
  - `Config()` must not be constructed: it mkdirs at `M/config.py:191`.
- **Helpers (imported, never edited).**
  - `runtime.open_active` (:227), `OBSERVATIONS_SQL` (:78), `resolve_output` (:866-889), `canonical_bytes` (:219), `ATTESTATION` (:38), `SCHEMA_EPOCHS` (:39).
  - `revision_vector.load_strict(raw, numbers=True)` (:123): it refuses floats.
  - `ev.canonical` (:229), `ev.hash_value` (:259), `ev.relative_path` (:263).
  - `__main__._read_cross_repo_input` (:1611).
- **Pins.**
  - Tools JSON: 7 tools, sha `94f9567a…ac56`, 1631472 bytes. Budgets 3436/3448/797/7730. `ev.RELATIONS` has 8 members.
  - `__main__` top-level imports stay lazy (test_s34_isolation:467-475; test_s37:1192 bans "reports").
  - CHANGELOG Unreleased/Added: bullets[0..2] are S30, S33 and S34. S34 slices `[bullets[2]:bullets[3]]`, so bullets[3] must exist; S35 becomes bullets[3].
  - The S27, S28 and S29 locator titles must not occur in the S35 entry.
  - README: the rationale-drift row (:948) must follow history-signals (:947). Rails (:949) has no neighbour pin. README never contains `canary-missions.md`.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; stricter option; recorded 2026-11-05)
1. **Architecture: scout option A.**
   - New package `feedback/{__init__,outcomes,ranking}.py` and two lazily imported commands, `feedback-import` and `feedback-rank`.
   - No edit to any gated path (D12). No MCP change. No S18/S22 amendment.
   - The overlay only permutes the bundles the baseline returned.
   - The baseline settings are a frozen dataclass (`BaselineSettings`) built from the `config.py` constants.
2. **Outcome profile `atlas-junit-xml-profile.v1`** (pytest `--junitxml`), parsed with the stdlib after a byte pre-scan (D3).
   - Derived outcomes are `passed` or `failed` only.
   - Producer and run are `operator_declared`: the digest proves bytes, not that a run occurred.
3. **Envelope `atlas-feedback-envelope.v1`.** Checks run in the D5 order. Import is ALL-OR-NOTHING: any failure exits 1 with empty stdout and leaves output and store untouched. Annotations are `unverified_annotation` and never scored.
4. **Staleness (stricter).** An event is active only if its source fingerprint matches the queried snapshot's indexed observations AND its (snapshot_id, generation_id) equals the queried pair. Every excluded event is listed with a closed reason.
5. **Policy `atlas-feedback-ranking-policy.v1`.**
   - Verified `passed` = +1 per distinct (artifact, selector); verified `failed` = 0 (recorded, not scored); annotations = 0.
   - Cap 3. Integer-only, with a printed `policy_id`.
   - No anchor pinning: AC-004/007 do not need it, and the cap bounds movement to 3 slots.
   - Disabled → identity and the exact baseline order.
   - "applied" is never emitted; the terms are "active" and "adjustment".
6. **Held-out evaluation.** A library function `evaluate()` plus one test. It is a report only, never a superiority claim. No CLI flag. First cut candidate.
7. **Read-only (AC-003).** Inventories of the repo (including `.git`), every graph root (db, -wal, -shm) and the inputs, before and after every command, including rejection and usage paths. `ev.RELATIONS` and tools JSON unchanged.
8. **Scope amendment.** `feedback/__init__.py`, `test_s35_*.py`, `tests/fixtures/feedback/**`, a CHANGELOG bullet placed FOURTH, a README row directly after rationale-drift, and `docs/verified-feedback.md`. No prior test edited.
9. **Invariants.**
   - Tools JSON and budgets unchanged; no dependency (no defusedxml); no subprocess in the package.
   - No recursion. No `\d`, `\w`, `\s` or `$` in regexes. No "alter table", no `nx.compose(`, no merge-driver text.
   - Outcomes independent of the expat version (CI 3.14, images 3.12).
10. **Planner refinements (stricter than the scout; flagged for the checker).**
    - A JUnit `<error>` is not objective (a setup or teardown error did not exercise the claim).
    - Any `<!` byte pair is rejected, which also rejects comments and CDATA; pytest emits none.
    - The tie-break `-delta` makes delta d move a bundle exactly d slots past unadjusted ones.
    - `feedback-rank` is stdout-only (no write path).
    - `--store` is never read without `--overlay`.

### In
- **`feedback/outcomes.py`.**
  - Constants: formats, `PROFILE`, `SELECTOR_KIND`, `ATTESTATION`, the D9 vocabularies, `LIMITS`, `LIMITATIONS`, key tuples.
  - `FeedbackRejected(reason, detail=None)` and `FeedbackUsageError(what)`.
  - Functions: `normalize_limits`, `parse_envelope`, `load_record`, `prescan_junit`, `parse_junit`, `nodeid_pair`, `derive_outcome`, `run_id`, `import_feedback`, `validate_store`, `summary_document`, `validate_summary`, `canonical_bytes`.
- **`feedback/ranking.py`.**
  - Constants: `POLICY`, `POLICY_NAME`, `ADJUSTMENT_RULE`, `EXCLUSION_REASONS`, `RANK_REJECTIONS`, `BaselineSettings`.
  - Functions: `parse_request`, `baseline`, `activity`, `adjustments`, `order`, `rank_feedback`, `validate_ranking`, `evaluate`, `validate_evaluation`.
- **`feedback/__init__.py`**: docstring only, with no "import" token.
- **`__main__.py`** (additive only): `feedback-import`, `feedback-rank`, `_FEEDBACK_LIMITS`, `_write_feedback_atomic` (prefix `.atlas-feedback-`), `_feedback_usage`.
- **Tests:**
  - `test_s35_feedback_overlay.py` (the 7 named tests);
  - `test_s35_support.py`, `test_s35_outcomes.py`, `test_s35_ranking.py`, `test_s35_isolation.py`;
  - fixtures `tests/fixtures/feedback/`.
- **Docs:** `docs/verified-feedback.md`, the CHANGELOG bullet and the README row.

### Out
- Running tests or builds; remote CI; claims that the artifact proves a run happened.
- Model training, reward optimisation, automatic default promotion, operator-supplied weights.
- Writing the graph or evidence tables; any MCP op or schema change; blending generations.
- JUnit dialects other than pytest's default (`junit_prefix`, xunit plugins, namespaces).
- Free-text success claims.
- Rename following of results.
- An `--evaluate` CLI flag.

## Approach

### Decisions

**D1 Envelope `atlas-feedback-envelope.v1`.** Parsed with `load_strict(raw, numbers=True)`. Every key is required, and the key sets are closed at every level.
```
{"format":"atlas-feedback-envelope.v1", "retrieval_record_id":HEX64,
 "snapshot_id":HEX64, "generation_id":HEX64,
 "run": null | {"run_id":HEX64, "producer":{"name":TEXT, "version":TEXT}},
 "artifact": null | {"profile":TEXT, "sha256":HEX64, "bytes":INT},
 "claims":[{"claim_id":LABEL, "selector": null | {"kind":"pytest_nodeid", "value":NODEID},
            "outcome":"passed"|"failed", "results":[HEX64,...], "source_files":{REL:HEX64}}],
 "annotations":[{"annotation_id":LABEL, "kind":"useful"|"dead_end", "results":[HEX64,...],
                 "source_files":{REL:HEX64}, "note": null | NOTE}]}
```
- **Grammars** (all `re.fullmatch`, ASCII only):
  - LABEL `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; TEXT `[ -~]{1,128}`; NOTE `[ -~]{0,512}`.
  - HEX64 `ev.hash_value`; REL `ev.relative_path`, at most 4096 characters; INT an int, not bool, in 0..2^31-1.
  - NODEID (at most 1024 characters): `([A-Za-z0-9_./-]{1,512})((?:::[A-Za-z_][A-Za-z0-9_]{0,127}){1,8})(\[[ -~]{0,256}\])?`. Group 1 must end with `.py` (checked with `endswith`) and satisfy `ev.relative_path`.
- **Rules.**
  - `results` holds 1..128 unique ids. `claim_id`s are unique, and so are `annotation_id`s. claims + annotations is at least 1.
  - Every string must strict-encode to UTF-8; a lone surrogate gives `invalid_envelope`.
  - A `format` other than the constant gives `unsupported_envelope_version`. Any other violation (BOM, duplicate key, float, extra or missing key, grammar, an outcome such as `"success"`, free text in a claim) gives `invalid_envelope`.
  - Above `max_envelope_bytes` → `envelope_too_large`. More than `max_claims` claims + annotations → `too_many_claims`.
  - Free text exists only in an annotation `note`, and an annotation never verifies.

**D2 Run identity.** It is independent of claims and labels:
```
run_id = sha256("atlas-test-run.v1:" + canonical({"artifact":{"profile","sha256","bytes"},
         "producer":{"name","version"}, "snapshot":{"snapshot_id","generation_id"}}))
```
The declared `run.run_id` must be equal, else `run_identity_mismatch`.

**D3 JUnit profile `atlas-junit-xml-profile.v1`.**
- **Byte pre-scan**, before any parser exists. Any failure gives `artifact_invalid`.
  - The bytes start with exactly `<?xml version="1.0" encoding="utf-8"?>` or the same with `UTF-8`. No BOM.
  - Strict UTF-8 decode.
  - No `\x00`.
  - No `<!` anywhere (DOCTYPE, ENTITY, ATTLIST, comments, CDATA).
  - No `<?` after the declaration (no processing instructions).
  - So no DTD can exist and no entity can be defined. Only the five predefined entities and character references remain, and expat treats those identically across versions, so the outcome is independent of expat.
- **Parse.** `xml.etree.ElementTree.XMLParser(target=_Collector())` with one `feed(raw)` and then `close()`. No tree is built, and no pull or reparse deferral is involved.
  - `_Collector.start` tracks depth iteratively. Depth > 8 → invalid.
  - A tag containing `{` (a namespace) → invalid.
  - The root must be `testsuites` or `testsuite`. `testsuite` is allowed only as the root or a child of `testsuites`.
  - `testcase` is allowed only as a direct child of `testsuite`. It needs string `classname` and `name` attributes.
  - Direct children `failure`, `error` and `skipped` are recorded per testcase. Every other element and all text is ignored.
  - More than `max_testcases` testcases → invalid. `ET.ParseError` or a collector error → invalid.
- **nodeid → (classname, name)**, the same as pytest's `mangle_test_address` with no `junit_prefix`. With `head, bracket, params = value.partition("[")` and `parts = head.split("::")`:
  - `classname = ".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]])`;
  - `name = parts[-1] + bracket + params`.
  - Example: `tests/test_pay.py::TestCards::test_ok[visa]` maps to `tests.test_pay.TestCards` and `test_ok[visa]`.
- **Derived outcome for a pair.**
  - 0 testcases → `test_not_in_artifact`.
  - More than 1 testcase → `outcome_not_objective` (duplicates and collisions are never resolved).
  - No children → `passed`.
  - Exactly one `failure` and no `error` or `skipped` → `failed`.
  - Anything else (skipped, xfail, error, mixed) → `outcome_not_objective`.
- Verified iff derived == claimed, else `claim_disagrees_with_artifact`.

**D4 Records.**
- `load_record(raw)`: `json.loads` with a duplicate-key hook and `parse_constant` refusal. Floats are allowed because lexical bundles carry `score`.
- Then: a dict, `operation == "context"`, `status == "ok"`, `data.projection == "task_context"`, and `validate_context(payload, "context", DEFAULT_MAX_BOUND_FIELD_ELEMENTS)`.
- Any ValueError, TypeError, KeyError or AttributeError → `record_invalid`.
- A result's **sources** are the sorted unique `(path, source_sha256)` pairs of its bundle `sources` with `state=="available"`, a str `path` and a hex `source_sha256`. Its **position** is the bundle's index in `data.bundles`.

**D5 Import pipeline** (`import_feedback(envelope_raw, record_raw, artifact_raw|None, store_raw|None, side, limits)`). The first failure wins and nothing is written.

| # | Stage | Failure (reason[: detail]) |
| --- | --- | --- |
| 1 | Sizes | `envelope_too_large`, `record_too_large`, `artifact_too_large`, `store_too_large` |
| 2 | D1 parse | `invalid_envelope`, `unsupported_envelope_version`, `too_many_claims` |
| 3 | Previous store: `load_strict` + `validate_store` | `invalid_store`, `unsupported_store_version` |
| 4 | `open_active` + `_lease_schema_epoch == max(SCHEMA_EPOCHS)`; `DiffUsageError` → usage `graph_namespace` | `index_unavailable`, `source_snapshot_unavailable`, `index_upgrade_required` |
| 5 | Indexed observations (`OBSERVATIONS_SQL`, `type=="observation"`, `state=="indexed"`, hex `observed_source_sha256`); sqlite or JSON error | `source_snapshot_unavailable` |
| 6 | D4 record | X`: record_invalid` |
| 7 | envelope id == `data.retrieval_record_id` | X`: record_id_mismatch` |
| 8 | envelope (snapshot, generation) == record's == lease's | X`: snapshot_mismatch` |
| 9 | Claims present: `run`, `artifact` and `--artifact` bytes non-null, and every selector non-null | `unverifiable_outcome: missing_provenance` |
| 10 | `artifact.profile == PROFILE` | `unverifiable_outcome: unsupported_profile` |
| 11 | `len(bytes) == artifact.bytes`, then sha256 | `…: artifact_size_mismatch`, `…: artifact_digest_mismatch` |
| 12 | D2 | `…: run_identity_mismatch` |
| 13 | D3 pre-scan and parse | `…: artifact_invalid` |
| 14 | Each claim in `claim_id` order, then each annotation in `annotation_id` order: every result id is in the record; it has at least one source; each source path is indexed; `source_files ==` the derived pairs AND each sha == the observed sha | Y`: result_not_in_record`, `result_source_unavailable`, `source_not_indexed`, `source_fingerprint_mismatch` |
| 15 | Each claim: D3 derived outcome | `…: test_not_in_artifact`, `outcome_not_objective`, `claim_disagrees_with_artifact` |
| 16 | Build records (D6). The same id collapses; the same `dedup_key` with a different id gives a rejection, within the envelope and against the previous store | `conflicting_duplicate` |
| 17 | events + annotations ≤ `max_events`; assemble; `validate_store`; size | `too_many_events`, `invalid_output`, `output_too_large` |

- X = `unverifiable_outcome` when the envelope has at least one claim, else `unlinked_annotation`.
- Y = `unverifiable_outcome` for a claim and `unlinked_annotation` for an annotation.
- Stage 9 also covers the CLI case of claims given without `--artifact`. This is the AC-002 "lacks its required provenance" path.

**D6 Records and store.**
- **Event:**
  ```
  {id, type:"feedback_event", status:"verified_artifact",
   claim:{selector:{kind,value}, outcome},
   verification:{profile, attestation:"operator_declared", derived_outcome, testcase:{classname,name}},
   retrieval:{record_id, ranker, record_bundle_ids:[...record order]},
   run:{run_id, producer:{name,version}}, artifact:{sha256,bytes},
   snapshot:{snapshot_id,generation_id},
   results:[{bundle_id, position, sources:[{path,source_sha256}]}] (sorted by bundle_id),
   source_fingerprint, dedup_key}
  ```
- **Annotation:**
  ```
  {id, type:"feedback_annotation", status:"unverified_annotation", verification:"none",
   kind, note, retrieval:{record_id, ranker, record_bundle_ids}, snapshot, results,
   source_fingerprint, dedup_key}
  ```
- **Hashes** (domain-prefixed sha256 of canonical JSON):
  - `source_fingerprint = H("atlas-feedback-fingerprint.v1", sorted unique [path, sha] over results)`;
  - event `dedup_key = H("atlas-feedback-dedup.v1", {record_id, artifact_sha256, selector})`; run_id is excluded, so a relabelled producer conflicts instead of double counting;
  - annotation `dedup_key = H("atlas-feedback-annotation-key.v1", {record_id, kind, sorted bundle ids})`;
  - `id = H("atlas-feedback-event.v1" | "atlas-feedback-annotation.v1", record minus id)`.
  - `claim_id`, `annotation_id` and envelope order never enter any id.
- **Store `atlas-feedback-events.v1`:**
  ```
  {format, id, producer:{name:"atlas-aci feedback-import", version:"atlas-feedback-import.v1"},
   counts:{events, annotations}, events:[...by id], annotations:[...by id], limitations:[LIMITATIONS]}
  ```
  - `id = H(STORE_FORMAT, rest)`. No timestamps.
  - Merge = the union with the previous store, sorted by id.
- **Summary on stdout `atlas-feedback-import-summary.v1`:**
  ```
  {format, command:"feedback-import", store_format, store_id, previous_store_id|null,
   counts:{events, annotations, new_events, new_annotations, duplicate_events, duplicate_annotations}}
  ```
- **`validate_store`** checks:
  - closed keys, constants, grammars and vocabularies;
  - recomputed ids, fingerprints and dedup keys;
  - sorting, unique ids and unique dedup keys;
  - `derived_outcome == claim.outcome`;
  - `positions == index in record_bundle_ids`;
  - counts and `limitations`.

**D7 Ranking sidecar `atlas-feedback-ranking.v1`** (`rank_feedback(request_raw, side, store_raw|None, enabled, limits)`).
- **Pipeline:**
  1. `request_too_large`.
  2. `parse_request`: `load_strict`; closed keys {schema_version="atlas.graph.v1", op="context", text, repository, path_prefixes, target, direction, relations, confidence, source_policy, max_bytes, *context_limits.LIMITS}; no `continuation` or `expected_generation_id`; `normalize(args, BaselineSettings())`. A ToolError gives `invalid_request`.
  3. When enabled only: store size, `invalid_store`/`unsupported_store_version`, `too_many_events`.
  4. Lease (D5 stage 4) plus observations.
  5. `baseline`: `asyncio.run(context(reader, args, BaselineSettings(), structured=True))`, then `validate_context` on the result. A ToolError or ValueError gives `baseline_unavailable`.
  6. Activity (D8) and scoring (D9).
  7. Assemble, `validate_ranking`, size check → `invalid_ranking` / `output_too_large`.
- **Document:**
  ```
  {format, id, producer:{name:"atlas-aci feedback-rank", version:"atlas-feedback-rank.v1"},
   overlay:{enabled, policy_name|null, policy_id|null, policy|null}, store_id|null,
   snapshot:{snapshot_id, generation_id},
   baseline:{retrieval_record_id, ranker, collection_stop, bundles:[{position, bundle_id, kind}]},
   ranking:[{position, baseline_position, bundle_id, kind,
             adjustment: null | {delta, rule, supporting_event_ids:[sorted]}}],
   events:{active:[{event_id, outcome, matched_bundle_ids:[sorted], scored}],
           excluded:[{event_id, reason}]},
   annotations:{count, scored:0}, counts:{baseline, adjusted, active, excluded},
   limitations:[LIMITATIONS]}
  ```
  `id = H(RANKING_FORMAT, rest)`.
- **Disabled** (no `--overlay`): the store is never read; `overlay` holds `enabled:false` and null policy fields; `store_id` null; `events` and `annotations` empty or zero; every `adjustment` null; `ranking[i].bundle_id == baseline.bundles[i].bundle_id`.

**D8 Staleness (`activity(store, observations, snapshot_id, generation_id)`).** For each event and annotation in id order, the first applicable reason wins:
1. `path_not_indexed`: some referenced path has no indexed observation.
2. `source_fingerprint_mismatch`: some (path, sha) differs from the observed sha.
3. `generation_mismatch`: (snapshot_id, generation_id) differs from the queried pair (no cross-generation blending).
4. Otherwise active.

Excluded verified events are listed in `events.excluded`. Annotations are only counted.

**D9 Policy and order.**
- `POLICY`:
  ```
  {"format":"atlas-feedback-ranking-policy.v1",
   "weights":{"verified_passed":1,"verified_failed":0,"unverified_annotation":0},
   "unit":"distinct_artifact_sha256_and_selector_per_bundle", "max_delta_per_bundle":3,
   "sort_key":["baseline_position_minus_delta","negative_delta","baseline_position","bundle_id"],
   "pinned":"none"}
  ```
  `policy_id = sha256(canonical(POLICY))`. `POLICY_NAME = "atlas-feedback-ranking-policy.v1"`; the operator passes it to `--overlay`.
- **Scoring.** Per baseline bundle B: `supporting_event_ids` = sorted ids of active `passed` events with B in `results`; `delta = min(3, |{(artifact.sha256, selector.value)}|)` over those events.
  - `adjustment` is non-null iff delta > 0, with rule `ADJUSTMENT_RULE = "verified_passed_distinct_artifact_selector"`.
  - Active `failed` events have `scored:false` and never change anything.
- **Order.** `sorted(baseline, key=(pos - delta, -delta, pos, bundle_id))`. Delta d moves a bundle exactly min(d, pos) slots past unadjusted bundles.
- **`validate_ranking`** checks:
  - a permutation of the baseline with positions 0..n-1 and `baseline_position` consistent;
  - recomputed deltas and the D9 order;
  - supporting ids ⊆ active ids with outcome passed; `scored` iff outcome passed and matched;
  - excluded reasons ∈ EXCLUSION_REASONS; the disabled-mode invariants; closed keys; ids; limitations;
  - no `applied` token.

**D10 Held-out evaluation `atlas-feedback-evaluation.v1`** (`evaluate(store, observations, snapshot_id, generation_id, modulus=5, residue=0)`). Library only.
- Held out iff `int(event_id[:8], 16) % modulus == residue`.
- Training = active verified events that are not held out, scored by D9.
- For each held-out active event, its `record_bundle_ids` order is permuted by the training deltas. The report gives per result `{bundle_id, baseline_position, overlay_position}`.
- `counts{events, active, excluded_by_reason{...}, held_out, training}`.
- No aggregate metric or superiority claim. `validate_evaluation` recomputes everything.

**D11 Vocabularies (closed and documented).**
- `IMPORT_REJECTIONS` (18): `envelope_too_large, record_too_large, artifact_too_large, store_too_large, invalid_envelope, unsupported_envelope_version, too_many_claims, invalid_store, unsupported_store_version, index_unavailable, source_snapshot_unavailable, index_upgrade_required, unverifiable_outcome, unlinked_annotation, conflicting_duplicate, too_many_events, invalid_output, output_too_large`.
- `UNVERIFIABLE_DETAILS` (16): `record_invalid, record_id_mismatch, snapshot_mismatch, missing_provenance, unsupported_profile, artifact_size_mismatch, artifact_digest_mismatch, run_identity_mismatch, artifact_invalid, result_not_in_record, result_source_unavailable, source_not_indexed, source_fingerprint_mismatch, test_not_in_artifact, outcome_not_objective, claim_disagrees_with_artifact`.
- `ANNOTATION_DETAILS` (7): `record_invalid, record_id_mismatch, snapshot_mismatch, result_not_in_record, result_source_unavailable, source_not_indexed, source_fingerprint_mismatch`.
- `RANK_REJECTIONS` (12): `request_too_large, invalid_request, store_too_large, invalid_store, unsupported_store_version, too_many_events, index_unavailable, source_snapshot_unavailable, index_upgrade_required, baseline_unavailable, invalid_ranking, output_too_large`.
- `EVENT_STATUSES` (`verified_artifact`); `ANNOTATION_STATUSES` (`unverified_annotation`); `ANNOTATION_KINDS` (`useful, dead_end`); `OUTCOMES` (`passed, failed`); `EXCLUSION_REASONS` (`path_not_indexed, source_fingerprint_mismatch, generation_mismatch`); `ADJUSTMENT_RULES` (1).
- `USAGE_NAMES`: the 9 limit names, plus `output_location, output_equals_input, graph_namespace, store_without_overlay, overlay_without_store, unsupported_policy`.
- `LIMITATIONS` (11):
  1. `artifact_digest_proves_bytes_not_that_a_run_occurred`
  2. `producer_and_run_are_operator_declared`
  3. `result_to_test_linkage_is_operator_declared_not_causal`
  4. `only_pytest_junit_xml_passed_and_failed_are_objective`
  5. `junit_name_collisions_are_not_objective`
  6. `events_from_another_generation_are_never_active`
  7. `failed_outcomes_are_recorded_and_not_scored`
  8. `annotations_are_unverified_and_never_scored`
  9. `ids_embed_the_absolute_checkout_path_and_are_not_portable`
  10. `not_written_to_the_graph_and_not_exposed_over_mcp`
  11. `evaluation_is_a_report_not_a_ranking_claim`
- **S35 BANNED tokens** (package, docs, CHANGELOG entry, README row, emitted keys and values): `safe, proven, guaranteed, compliant, compatible, incompatible, breaking, applied, trusted, certified, authentic, superior, optimal, better, improves, improved, improvement, learned`. No value equals an `ev.RELATIONS` member.

**D12 Limits, CLI and gated paths.**
- **Limits** (default/min/max; a bool or out-of-range value → usage `Invalid feedback option: <name>`):
  - `max_envelope_bytes` 1048576/1/16777216
  - `max_record_bytes` 16777216/1/67108864
  - `max_artifact_bytes` 8388608/1/67108864
  - `max_store_bytes` 67108864/1/268435456
  - `max_claims` 1000/1/10000
  - `max_testcases` 100000/1/1000000
  - `max_events` 10000/1/100000
  - `max_request_bytes` 65536/1/1048576
  - `max_output_bytes` 67108864/1/268435456
  - Fixed values: XML depth 8, 128 results per item.
- **`atlas-aci feedback-import`**: `--repo DIR [--graph-root DIR] --envelope F --retrieval-record F [--artifact F] [--store F] --output F [limits]`.
  - Protected outputs: repo, repo/.atlas, graph root. Inputs: all given files; `--output` must differ from `--store`.
  - `resolve_output` runs before any lease; `_write_feedback_atomic` then prints the summary.
- **`atlas-aci feedback-rank`**: `--repo DIR [--graph-root DIR] --request F [--overlay atlas-feedback-ranking-policy.v1 --store F] [--max-request-bytes N] [--max-store-bytes N] [--max-events N] [--max-output-bytes N]`. It prints the sidecar (canonical + LF) and writes nothing.
- Reads go through `_read_cross_repo_input` (regular files only, `O_NONBLOCK`, limit+1).

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| Limit bool, 0 or above max | 2 | `Invalid feedback option: <name>` |
| Output inside a protected root / equals an input | 2 | `--output must be outside the repository and graph root.` / `--output must differ from every input.` |
| Namespace unusable | 2 | `The repository cannot be opened as a graph namespace.` |
| `--store` without `--overlay` / reverse / other policy | 2 | `--store requires --overlay.` / `--overlay requires --store.` / `Unsupported ranking policy.` |
| Input unreadable (FIFO, directory, missing) | 1 | `Feedback input cannot be read: <role>` (role ∈ envelope, retrieval-record, artifact, store, request) |
| Rejection | 1 | `Feedback import rejected: <reason>[: <detail>]` / `Feedback ranking rejected: <reason>` |
| Write failure | 1 | `Feedback output cannot be written.` |
| Other | 1 | `Feedback import failed: <TypeName>` / `Feedback ranking failed: <TypeName>` (never `str(exc)`) |

- **Gated paths** (import only, never edited):
  - `server.py, enforcement.py, config.py, codegraph.py`;
  - `protocol/, query/, tools/, storage/, extractors/, providers/, indexing/, resolvers/, frameworks/, architecture/, counterfactual/, contracts/, importers/, overlays/, dependencies/, cross_repo/, history/, rationale/, reports/`;
  - `pyproject.toml, uv.lock, Dockerfile*, .github/workflows/`.
- **Package import allowlist:** `__future__, asyncio, hashlib, json, re, sqlite3, collections.abc, contextlib, dataclasses, pathlib, typing, xml.etree.ElementTree, atlas_aci.config, atlas_aci.enforcement, atlas_aci.overlays.runtime, atlas_aci.query.diff, atlas_aci.query.context, atlas_aci.query.context_limits, atlas_aci.protocol.context, atlas_aci.storage.evidence, atlas_aci.cross_repo.revision_vector, atlas_aci.feedback.outcomes`.
  - No `os`, `subprocess`, `tempfile`, `socket`, `urllib` or `open(`. No `Config(` call.
  - `feedback/` is outside the generation inputs, so generation ids cannot churn.

### Work plan (timebox 3 engineering days)
**Day 1 AM: qualification.** `.spectra/evidence/aci-s35/qualify.py` (run with `uv run --frozen python`) writes `qualification.json`, using the `world.json` roots (see Fixtures).
- (a) **Library vs MCP.** Query "process_payment" on root-before. The `call_tool_result` (protocol 2025-11-25) decoded payload equals `context()` over `open_active` with `BaselineSettings`. Record the bundle id order, `retrieval_record_id` and full `data` equality, plus 2 replays.
- (b) **Saved record.** `validate_context` accepts the record after a file round trip. One tampered byte in a bundle fails it.
- (c) **Real pytest JUnit.** Run pytest `--junitxml` once on a scratch suite: passed, failed, skipped, xfail, setup error, a class method, and a parametrised id with `-` and `.`.
  - Record the exact declaration bytes, the (classname, name) per nodeid (must equal D3), and the child elements.
  - Generalise the outputs into the fixture XMLs.
- (d) **Expat independence.** `prescan_junit`/`parse_junit` (a throwaway copy in the evidence dir) over every fixture variant gives identical results and details on local Python, `python:3.12-slim` and `python:3.14`. Record `pyexpat.EXPAT_VERSION`.
- (e) **Generation stability.** An unchanged rebuild at the same path gives equal (snapshot_id, generation_id). An unrelated-file edit changes both while the referenced bundle ids stay equal. A referenced-file edit changes the bundle ids.
- (f) **Observations.** Fields `type`, `state` and `observed_source_sha256`; every available bundle source has a str path and hex sha equal to the observation.
- (g) **Timing.** Five builds, one MCP call and one library call; the projected s35 suite time.
- (h) **Base invariants.** Tools sha and bytes, budgets, `ev.RELATIONS`, the full suite (test_s08 nonregular_pack deselected), and the CHANGELOG/README pins as read above.

**STOP RULE** (return to the orchestrator; never adapt silently):
- (a) the bundle order differs between library and MCP, or library `context()` fails over `open_active`;
- (b) `validate_context` rejects a genuine saved record;
- (c) pytest's (classname, name) differs from the D3 mapping for a supported nodeid shape;
- (d) any variant differs between interpreters or expat versions;
- (e) an unchanged rebuild changes generation_id.

Recorded adaptations (not stops):
- (a) record ids differ only through `repository` → assert order equality only and document it;
- (c) a declaration variant → add it to the allowed pair;
- (g) a build over 10 s → module-scoped worlds.

**Day 1 PM:** `outcomes.py` D1-D6, `feedback-import`, the support world, AC-001, AC-002, `test_s35_outcomes.py`.
**Day 2:** `ranking.py` D7-D10, `feedback-rank`, AC-003..007, `test_s35_ranking.py`. Apply the cut order at Day-2 end if needed.
**Day 3:** `test_s35_isolation.py`, docs, CHANGELOG, README, gates, py314, production image, RUN-01/02.

**Cut order** (each cut is recorded in the receipt; no AC is affected):
1. D10 evaluation (library and test). The follow-up is "held-out overlay evaluation".
2. Production-image steps beyond: import twice equal, forged exit 1, disabled == baseline, stale exclusion, inventory, tools pin.
3. The PYTHONHASHSEED subprocess replay.
4. `validate_*` tamper breadth beyond one case per check.
5. Docs and README depth beyond the scanned phrases.

**Never cut:**
- the D5 verification order, the pre-scan, all-or-nothing import;
- D8 staleness and generation exclusion;
- disabled == baseline, including the MCP comparison;
- read-only behaviour with inventories;
- closed vocabularies; the 7 AC tests; G-SRC and G-MCP.

### Independent checker conditions
Checker verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s35-plan-checker`, 3 MAJOR + 9 MINOR; `.spectra/plans/aci-s35.review.json`). Conditions 1-12 below are binding and override conflicting text above.

Orchestrator resolutions (stricter option wherever the checker offered a choice):
- C1: NODEID path grammar restricted to `[A-Za-z0-9_/-]{1,512}` plus a single trailing `.py` (no other dot); negative rows for `.py`-in-directory and `a.b.py`; Day-1 (c) case and STOP rule.
- C2: LIMITATION `store_is_self_consistent_not_authenticated` (closed list + docs scan); `attestation:"operator_declared"` echoed on each active event; a test pins that a hand-built self-consistent store passes `validate_store`; import never relabels PREV events.
- C3: truncated baselines are not rejected; every AC-004..007 and qualification request asserts `collection_stop is None` and no `selection_reason`; 20-replay qualification; LIMITATION `baseline_is_wall_clock_bounded_by_deadline_ms`; "disabled" means no `--overlay` only.
- C6: baseline position 0 is PINNED (`"pinned":"baseline_position_0"`), recomputed by `validate_ranking`.
- C10: scoring uses only bundle_id equality with the live baseline, never event positions or record bundle lists; record replay not required.
- C11: the held-out fold is by `retrieval.record_id`; if D10 is cut the receipt records "approach step 4 not delivered" and a follow-up.

Checker findings (verbatim):

1. MAJOR: the nodeid to (classname, name) mapping differs from pytest's, and it is not injective.
   - Evidence: plan D3 lines 146-149 use `parts[0][:-3].replace("/", ".")`. As I recall, pytest's `mangle_test_address` runs `x.replace(".py", "")` on every name element, not only the suffix.
   - A directory or stem containing `.py` (for example `tests/my.pytools/test_a.py`) gets a different classname, so it falls to `test_not_in_artifact`.
   - `a/b.py` and `a.b.py` both map to classname `a.b`, so a claim on one path can be verified by the other's testcase.
   - Condition (stricter): restrict NODEID group 1 to `[A-Za-z0-9_/-]{1,512}` followed by the final `.py`, with no other dot. Add the `.py`-in-directory and `a.b.py` shapes as negative rows in the nodeid table. Add a `.py`-in-directory case to Day-1 (c) and its STOP rule.

2. MAJOR: the store is not authenticated, and `verified_artifact` is not forgery-proof through the store.
   - Evidence: D5 stage 3 validates `--store PREV` with `validate_store`, and D7 step 3 does the same for `feedback-rank`. D6 `validate_store` recomputes ids but cannot see artifact bytes.
   - A hand-authored store with consistent ids is accepted. It is merged by import and scored by rank. This contradicts the story that a forged claim never changes ranking.
   - Conditions:
     - Add LIMITATION 12, `store_is_self_consistent_not_authenticated`, to the closed list and to the docs scan.
     - Echo `attestation:"operator_declared"` on each `events.active[]` entry.
     - Add a test that a hand-built self-consistent store passes `validate_store`, so the limit is pinned and not silent.
     - Import must never relabel PREV events.

3. MAJOR: the baseline depends on wall-clock time, which can make library, MCP and run-to-run outputs differ.
   - Evidence: `context_work.py:52-54` and `context.py:246-248` set `collection_stop="context_deadline"` at 2000 ms. After collection, `work.check()` at `context.py:248` raises, which becomes `baseline_unavailable`. The issue itself says wall-clock-truncated work is replay-identical only if its frontier is identical.
   - Rejecting a truncated baseline would make AC-007 unsatisfiable under load, so I do not bind that.
   - Conditions:
     - Every AC-004 to AC-007 and qualification request asserts `baseline.collection_stop is None` and no `selection_reason`.
     - Qualification (a)/(g) records 20 replays with zero stops.
     - Add a LIMITATION, `baseline_is_wall_clock_bounded_by_deadline_ms`, and a docs phrase.
     - The sidecar already carries `collection_stop`; keep it.
     - Say explicitly that "disabled" means "no `--overlay`" only. Enabled with zero active events is separately pinned by the AC-005 root-after case, where order equals baseline.

4. MINOR: request validation does not match the MCP path.
   - Evidence: `server.py:639-641` runs `request_mode` and the published-schema validator before dispatch. D7 `parse_request` calls only `normalize`. `context_limits.py:33` does `arguments["text"]`, so a missing `text` is an uncaught KeyError and gives "Feedback ranking failed: KeyError".
   - Conditions: require `text` before `normalize`. Catch ToolError, KeyError, TypeError and ValueError as `invalid_request`. Call `request_mode(args)` and add `atlas_aci.protocol.graph` to the import allowlist. The AC-007 test asserts `call_tool_result` accepts the same request.

5. MINOR: reader and loop equivalence need explicit assertions.
   - Evidence: MCP builds `CodeGraph(read_only=True, query_limit=cap+1)` at `server.py:591-596`. `open_active` goes through `diff.py:194`, which uses the default `query_limit`. Both are 201 today.
   - Conditions: assert `reader.query_limit` and the `repo` string equality in qualification (a). `asyncio.run` inside `baseline()` fails inside a running loop, so the S35 tests that call the CLI or `rank_feedback` must be sync functions.

6. MINOR: the overlay can demote the baseline's exact-definition anchor.
   - Evidence: `context_admission.py:37-41` pins the first exact definition at position 0. D9 key `(pos-delta, -delta, ...)` puts a position-1 bundle with delta 1 ahead of the anchor, because (0,-1) is less than (0,0).
   - Condition (stricter): pin baseline position 0 with `"pinned":"baseline_position_0"`, and make `validate_ranking` recompute with it. Delta then moves a bundle min(d, pos-1) slots, and the AC-004 position>=2 example still holds. If the executor refuses to pin, a documented limitation is the only alternative and must say the anchor can be displaced.

7. MINOR: the D3 "passed" rule is ambiguous.
   - Evidence: D3 line 153 says "No children → passed", but line 144 says other elements are ignored. pytest emits `<properties>`, `<system-out>` and `<system-err>` on passed cases.
   - Conditions: define passed as no direct `failure`, `error` or `skipped` child. A `failure` nested under another element is not a direct child and is ignored. Add fixtures for a passed case with `properties` and `system-out`, and for a nested `<failure>`. Wrap both `feed()` and `close()` in the collector-exception catch. The expat-independence reasoning is sound: no `<!`, no PI, no DTD, no namespace.

8. MINOR: `load_record` can leak a RecursionError.
   - Evidence: the D4 catch list is ValueError, TypeError, KeyError and AttributeError. `revision_vector.py:139` catches RecursionError and MemoryError for the same hazard. A deeply nested record up to 16 MiB gives "failed: RecursionError" instead of `record_invalid`.
   - Condition: also catch RecursionError and MemoryError, and add a depth test.

9. MINOR: the AC-004 R3 scenario may be unsatisfiable on the fixture.
   - Evidence: bundle A is the last bundle of the "process_payment" query. R3 ("validate_card") must contain the same bundle id. A default outgoing call-edge relationship bundle likely will not appear there.
   - Conditions: Day-1 (a) must pick A by id so it is present in both records and at position 2 or later in the rank request, or change the fixture or request. Document that events match by bundle id across queries and are not query-scoped.

10. MINOR: forged-record limits are undocumented.
    - Evidence: `validate_context` (`protocol/context.py:326-600`) is self-consistency only.
    - A re-hashed record can alter record id, `position` and `record_bundle_ids`. Bundle ids are content-addressed, so ranking is safe only because scoring matches bundle_id against the live baseline.
    - Conditions: scoring must never use event position or `record_bundle_ids`. Reword LIMITATION 3 to "records are self-consistent, not replayed". Mark D10 outputs as declared-order based. Replay of the record's request against the lease is the stricter option and is not required.

11. MINOR: D10 folds by event id, but the issue says held-out records.
    - Condition (stricter): fold on `retrieval.record_id`, so no record feeds both training and held-out. The test uses `int(record_id[:8],16) % 5`. If D10 is cut, the receipt must record "approach step 4 not delivered" with a follow-up issue. No AC is affected.

12. MINOR: documentation and edge cases.
    - D2 `atlas-test-run.v1` is a different domain from S22 `coverage_run_id` (`evidence.py:786`). Docs and the scope amendment must say compatibility means field names and the `pytest_nodeid` selector kind (`validation_limits.py:54`), not S22 coverage run identity.
    - An annotation whose note changes between imports gives `conflicting_duplicate`, because the dedup key excludes the note and the id includes it. Document this.
    - Document that a junit `<error>` is not objective (limitation 4).
    - In AC-003, MCP record production uses `Config(...)`, which mkdirs the memex root (`config.py:191`). That root must sit outside every inventoried tree and be created before the "before" inventory.

Checked and sound:
- Baseline: `graph_query.py:29-32` calls the same `context()`. Central bounds for context add only the byte ceiling (`server.py:250-252`, no bounded field). `record_domain` excludes `query_digest` and `structured`. The library path rejects `continuation`, as the plan intends.
- S34 `stat_inventory` precedent (`test_s34_adr_drift.py:413-416`).
- CHANGELOG and README pins at `test_s30_isolation.py:563-565`, `test_s33_isolation.py:436-441`, `test_s34_isolation.py:533-563` and S26-S29. The bullet at index 3 and the row after rationale-drift are correct. The S35 README row must not contain the string `docs/rationale-drift.md`.
- `test_s37:1192` (no "reports" in top-level imports) and the harden-gate "alter table" grep are respected by the plan.
- No `xml` use exists elsewhere in src, and no dependency is added.
- Production-image realism: `build_server_state` and `call_tool_result` exist (`server.py:567`, `server.py:600`). No MCP-free CLI path produces a full record, and the plan's `make-record.py` is acceptable. Docs must say the saved record is the tool result's `structuredContent` object, or the compact JSON in `content[0].text`.

## Stories
As an agent operator, I want a test outcome I claim about retrieved results to be counted only when a checked JUnit artifact, the exact retrieval record and the active snapshot agree, so that a forged or stale claim never changes ranking.

As a reviewer, I want the overlay to be opt-in, explained per adjustment by event ids, and to expire when source premises change. With the overlay off, ordering must be identical to the baseline ranker.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s35.criteria.md` (sha256 `f4d64ce3…6c7`). AC-S35-001..007 and RUN-01/02 are not re-stated here; the executor reads the frozen file.

### Fixtures
**`tests/fixtures/feedback/world.json`** (ASCII): `{"before":{path:text}, "unrelated":{...}, "after":{...}, "removed":{...}}`.
- `before`: `app/payments.py` (`process_payment` calls `validate_card` and `record_charge`), `app/cards.py`, `app/ledger.py`, `app/unrelated.py`, `tests/test_payments.py` (text only; never run).
- Roots, all at the same checkout path and outside the repo, built with `test_s19_support.write_tree`/`build`, git repo:
  - `root-before` (G1);
  - `root-rebuild` (unchanged rebuild);
  - `root-unrelated` (`app/unrelated.py` edited);
  - `root-removed` (`app/payments.py` deleted);
  - `root-after` (G2, `app/payments.py` body edited).
- The checkout ends in the `after` state.
- Records are produced in-test through `call_tool_result` with `Config(repo, graph_root=..., memex_root=tmp/memex, max_calls_per_minute=0)` and saved to files. There are no golden ids, because ids embed tmp paths.

**JUnit XMLs** (ASCII, pytest layout from Day-1 (c)):
- `junit-outcomes.xml`: A passed, B failed, C skipped, D error, E duplicated.
- `junit-second.xml`: A passed, different bytes.
- `junit-doctype.xml`: billion laughs.
- `junit-pi.xml`, `junit-comment.xml`, `junit-namespace.xml`, `junit-depth.xml` (depth 9), `junit-nodecl.xml`, `junit-misplaced.xml` (testcase under the root).
- Generated in-test: NUL, invalid UTF-8, oversized, more than `max_testcases`, tampered bytes.

Envelopes are built in-test by `test_s35_support.envelope(record, claims, ...)`, which uses an independent run_id implementation with `hashlib` and `json`.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_feedback_verified_event_requires_artifact | **Setup.** Record R1 ("process_payment", root-before); claims A=passed on the last bundle (position ≥ 2; assert ≥ 3 bundles) and B=failed on bundle 1; artifact `junit-outcomes.xml`. **Import.** `feedback-import` exits 0 and the store has 2 `verified_artifact` events. **Each event:** `retrieval.record_id` == R1's id; `run.run_id` == the independent formula; `snapshot` == the lease; `artifact.sha256` == the sha of the fixture bytes; `derived_outcome == claim.outcome`; the testcase pair == the D3 mapping; results' sources == R1 bundle sources == an independent sqlite read of the observations; `positions` and `record_bundle_ids` == R1. **Validation.** `validate_store` passes; CLI bytes == library bytes. **Without the artifact.** The same envelope without `--artifact` → exit 1 `Feedback import rejected: unverifiable_outcome: missing_provenance`, empty stdout, no output file. An annotation-only envelope yields only `unverified_annotation` and no event. |
| 002 | test_feedback_forged_success_is_rejected | **Each variant:** exit 1 with the exact detail text; empty stdout; output absent; the previous store sentinel byte-identical. **Variants:** B claimed passed → `claim_disagrees_with_artifact`; C, D, E → `outcome_not_objective`; absent selector → `test_not_in_artifact`; a byte flipped → `artifact_digest_mismatch`; `bytes` off by one → `artifact_size_mismatch`; wrong run_id or a producer edit → `run_identity_mismatch`; run/artifact/selector null → `missing_provenance`; profile `x` → `unsupported_profile`; every invalid XML → `artifact_invalid`; edited R1 → `record_invalid`; a different id → `record_id_mismatch`; an R2 record from root-after imported on root-before → `snapshot_mismatch`; an unknown bundle → `result_not_in_record`; wrong `source_files` → `source_fingerprint_mismatch`. **All-or-nothing.** One good claim plus one forged claim → rejected, nothing written. Outcome `"success"` → `invalid_envelope`. |
| 003 | test_feedback_overlay_preserves_canonical_facts | **Inventory.** `stat_inventory` (from `test_s34_support`) of the repo with `.git`, every root (db, -wal, -shm) and the inputs dir is equal before and after each of: import ok; import rejected; import usage (output inside the graph root); rank disabled; rank enabled; rank `invalid_store`. **Also:** each graph db sha256 and its per-table `count(*)` over a `mode=ro` sqlite read are unchanged; no `.atlas-feedback-*` temp file remains; only `--output` appears; `ev.RELATIONS` and the tools JSON pin hold; the MCP `graph_query op=context` payload before == after the import. |
| 004 | test_feedback_opt_in_ranking_is_explained | **Command.** `feedback-rank --overlay atlas-feedback-ranking-policy.v1 --store S` on root-before. **Overlay fields.** `overlay.enabled`; `policy_id` == the test's sha of canonical(POLICY). **Bundle A.** Its entry has `adjustment{delta:1, rule, supporting_event_ids:[eA]}` and moves up exactly 1. Adding the `junit-second.xml` event → delta 2, 2 sorted ids. The same artifact+selector through record R3 ("validate_card") → 2 ids, delta still counted once. **Bundle B.** `failed` is active with `scored:false` and null adjustment. **Checks.** The annotation is counted, never scored; the test recomputes the order independently from D9; `validate_ranking` passes; no `applied` token. |
| 005 | test_feedback_stale_source_is_excluded | **Store from root-before.** Rank on root-after → every event is `source_fingerprint_mismatch` and the order == baseline; root-unrelated → `generation_mismatch`; root-removed → `path_not_indexed`; root-rebuild → active and adjusted. **Mixed G1+G2 store** (an R2 event imported on root-after): on root-after only the G2 ids support adjustments, and G1 is excluded. Every excluded event is listed once with its reason. |
| 006 | test_feedback_reimport_is_idempotent | **Equal bytes:** two fresh imports; an import with `--store s1` gives s2 == s1 with summary `duplicate_events == 2, new_events == 0`; permuted claims, keys and options; PYTHONHASHSEED 1/2 `python -m atlas_aci`. **Ranking:** the sidecars from s1 and s2 are byte-equal. **Conflicts:** the same (record, artifact, selector) with different results, or a producer-version change → `conflicting_duplicate`, nothing written. |
| 007 | test_feedback_disabled_preserves_baseline | **Disabled rank.** No `--overlay`, with the active store existing. `ranking` ids == `baseline` ids == the MCP `call_tool_result` bundle ids for the same request == library `context()` ids; positions are the identity; all adjustments null; the policy is null. **Usage.** `--store` without `--overlay` → exit 2. **Records.** The sidecar `baseline.retrieval_record_id` == the library record id. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the one receipt is `.spectra/state/aci-s35.execution.json`. |

### Supporting tests
- **`test_s35_outcomes.py`.**
  - The pre-scan rule table.
  - Depth, namespace and misplaced cases.
  - The nodeid → pair table, including the Day-1 shapes.
  - The derived-outcome table.
  - Strict envelope rejections: BOM, duplicate key, float, extra key, lone surrogate, every grammar, duplicate `claim_id`, empty `results`, v2 format.
  - Every IMPORT_REJECTION and detail reachable, with the exact CLI text.
  - Limits as a bool, 0 and max+1.
  - Output inside the repo, repo/.atlas or the graph root (including through a symlinked parent); output == store.
  - FIFO and directory inputs; an unindexed root; `index_upgrade_required` through an `open_active` spy.
- **`test_s35_ranking.py`.**
  - D9 order properties (delta d moves d slots, cap 3, ties).
  - The D8 reason precedence.
  - Every RANK_REJECTION.
  - `baseline_unavailable` through a 1-byte `max_bytes`.
  - D10 with `residue = int(first_id[:8],16) % 5`.
  - `validate_store`, `validate_ranking` and `validate_evaluation` tamperings, one per check.
- **`test_s35_isolation.py`.**
  - `feedback/*.py == [__init__, outcomes, ranking]`; ASCII; lines of at most 100 characters.
  - AST: the import allowlist; no function calls itself; no `Config(`; regex scans.
  - BANNED and `ev.RELATIONS` value scans with a planted hit.
  - Tools JSON pin, and none of `feedback-import`, `feedback-rank`, `verified_artifact`, `unverifiable_outcome`, `atlas-feedback-` in it. `READ_ONLY_TOOLS` has 7 and no name contains "feedback". Budgets. The commands are in `cli.commands`.
  - Lazy imports: no top-level line mentions `feedback`, and the probe `'atlas_aci.feedback' not in sys.modules`. `_FEEDBACK_LIMITS == LIMITS`. `BaselineSettings` equals the config constants.
  - **Docs:** ASCII; every D11 value, format, limit row, CLI text and option is named; the phrases "proves bytes, not that a run occurred", "operator_declared", "not causal", "never active", "recorded, not scored", "CLI only, no MCP op", "does not write the graph", "same checkout path".
  - **CHANGELOG:** the bullet (located by "Verified test-outcome feedback (ACI-S35)") is `bullets[3]`, bullets[0..2] are unchanged, and the entry has no prior locator titles.
  - **README:** the row sits directly after the rationale-drift row, contains no prior locators and no `canary-missions.md`.

### Gates beyond the ACs
- **G-SRC.**
  - `git diff --exit-code bd6da89 -- <D12 gated paths>`.
  - `git diff --name-only bd6da89 -- mcp-server/src` ⊆ {`feedback/__init__.py`, `feedback/outcomes.py`, `feedback/ranking.py`, `__main__.py`}.
  - `git diff bd6da89 -- mcp-server/src/atlas_aci/__main__.py | grep -c '^-[^-]'` == 0 (additive only).
  - Test changes are only `test_s35_*.py` and `fixtures/feedback/**`.
- **G-MCP.** Tools JSON `94f9567a…` / 1631472; `READ_ONLY_TOOLS` 7.
- **Regression** (green and unchanged): `test_s18*`, `test_s22*`, `test_s14_capabilities`, `test_s23_isolation`, `test_s26_isolation`, `test_s27_isolation`, `test_s28_isolation`, `test_s29_openapi_diff`, `test_s30_isolation`, `test_s33_isolation`, `test_s34_*`, `test_s37_evidence_report`, `test_thesis_negatives`, `test_export`. Run this set immediately after the CHANGELOG and README edits.
- **CI-exact lint.** `uv run --frozen ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/`.
- **Full suite** with the `test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition` deselect; no new failure or skip compared with Day-1 (h).
- **py314.**
  - `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s35_*.py tests/slices/test_s18_contract.py tests/test_export.py -q'`.
  - Log to `.spectra/evidence/aci-s35/py314.log`.
- **Production image** (`prod-image.sh`, orchestrator-run).
  1. Build `mcp-server/Dockerfile`.
  2. Run with `--network none --entrypoint sh` as the container user, with `world.json`, the XMLs and `make-record.py` mounted `:ro`.
  3. Write the before state to `/tmp/w`, `git init`, then `atlas-aci index --repo /tmp/w --graph-root /tmp/g1`.
  4. `make-record.py` calls `build_server_state` + `call_tool_result` in the image and writes `/tmp/r1.json`; a script builds the envelope.
  5. `feedback-import` twice → `cmp` equal.
  6. A forged claim → exit 1 with the exact text.
  7. `feedback-rank` disabled == the record's bundle order. Enabled shows adjustment event ids.
  8. Edit `app/payments.py` and re-index `/tmp/g1` → the event is `source_fingerprint_mismatch`.
  9. The inventory of `/tmp/w` and `/tmp/g1` is unchanged across the feedback commands; `atlas-aci tools` matches the pin.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s35_feedback_overlay.py -q
uv run --frozen pytest tests/slices/test_s35_*.py -q
uv run --frozen pytest tests/slices/test_s18_*.py tests/slices/test_s22_*.py tests/slices/test_s34_*.py tests/slices/test_s33_isolation.py tests/slices/test_s30_isolation.py tests/slices/test_s27_isolation.py tests/test_thesis_negatives.py tests/test_export.py -q
uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/ && uv run --frozen mypy src/
```

## Confidence
To be computed with `ramza-score`.
- **Why it is low-churn.** The new package only reads: one lease per command and the unchanged public `context()`/`validate_context`. No gated, graph or MCP edit, and no dependency.
- **Main uncertainties, covered by the Day-1 STOP rule:**
  - library-vs-MCP baseline identity;
  - the pytest name mapping;
  - expat independence;
  - generation stability.

## Rejected Alternatives
- **An MCP op or context argument for the overlay.** It changes the tools JSON and gated server/protocol/query code.
- **Feedback rows in the graph or evidence tables.** This would mutate the canonical store (AC-003) and churn generations.
- **A custom JSON test report.** Nothing produces it; JUnit XML is pytest's native output.
- **defusedxml.** A new dependency; the pre-scan removes the DTD surface instead.
- **Negative weight for failed tests.** A failing test is not evidence that the retrieved result was a dead end.
- **Partial import** (accept good claims, skip bad ones). It hides forgeries; all-or-nothing is stricter.
- **Fingerprint-only staleness.** It would blend generations, which the issue excludes.
- **Definition-anchor pinning.** No AC needs it, and the cap already bounds movement.

## Risks (ranked)
1. **Overclaiming trust.** The artifact and the linkage are operator-declared. Mitigated by limitations 1-3, the `operator_declared` attestation, the vocabulary bans and the docs phrases.
2. **Baseline drift between library and MCP** (structured flag, repo resolution, `deadline_ms` wall-clock truncation). Mitigated by the Day-1 STOP rule, the MCP comparison in AC-007, and `collection_stop` exposed in the sidecar.
3. **XML parser surface or expat drift.** Mitigated by the pre-scan before parsing, the target parser without a tree, and the Day-1 3.12/3.14 check.
4. **The strict generation guard makes events inert after any re-index with changes.** Intended; documented.
5. **Store growth** (`record_bundle_ids`). Bounded by `max_events`/`max_output_bytes`, which reject.
6. **CHANGELOG/README ordering pins.** Bullet fourth; row after rationale-drift.
7. **Timebox.** Apply the cut order at Day-2 end.
