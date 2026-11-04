---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T02:10:00Z
slice_id: ACI-S23
issue: https://github.com/Rynaro/atlas-aci/issues/44
---
# ACI-S23 — publish a reproducible v3 evidence and compatibility package

## Scope

Implement issue #44 on the merged base: `1cedf60` (main after PR #86 / ACI-S22 merged; tree equals
`aci-s22` @ f67cbfe), branch `aci-s23`. Timebox: 5 days. Criteria bytes `.spectra/plans/aci-s23.criteria.md` hash to
`3a7f63e2…0529`: 6 product criteria + RUN-01/02. The S22 receipt must be COMPLETE on the base.

Observed facts (verified at planning):
- `scripts/eval/` and `docs/evidence/` do not exist. Only `docs/graph-query-dsl.md` is under docs/.
- `atlas/evals/canary-missions.md` does not exist. README.md:833-847, SETUP.md:194-215/300 and
  CLAUDE.md:42-44 cite `--canaries atlas/evals/canary-missions.md`, but the flag is `--canaries-dir`
  (run-canaries.py:286). README.md:840-847 says no dispatcher is implemented, yet `--host stdio` is real
  (run-canaries.py:315-319 → `atlas/evals/stdio_runner.py::run_cli`); other hosts raise (:333-334).
- `stdio_runner`:
  - `run_manifest(server=...)` :322-330 already accepts a server argv, but `run_cli` :428-436 does
    not expose it.
  - `dispatch` :186-209 opens one session per task with `env={"PYTHONPATH": …}` :200.
  - `producer_identity` :159-177 hashes `mcp-server/src`, so `run_id` changes on every commit.
  - `completed_result` :297-319 emits null `model_tokens/model_cost/coding_task_success` plus an
    `unavailable_reason`.
  - It is loaded live by test_s01_contract.py:17 (and run-canaries by :197). test_s17_runner uses a
    frozen copy.
- `measure_context_yield.py` (S18) imports `atlas_aci` at module top (:13-16).
  - `union` :24, `metrics` :34-93 and `body_and_sources` :96-105 are pure.
  - `metrics` raises if a source hash differs from the labels.
  - OUT and FIXTURE are hardcoded. The fixture sha `07553bd3…` is pinned :21.
  - The fixture has 7 files, 4 tasks (payment, request, unicode, phrase) and 742 labelled bytes, with
    `max_inline_source_bytes` 8192 and `retrieval_max_candidates` 32.
  - `.spectra/evidence/aci-s18/local-yield.json` stores 48 context rows and 24 baseline rows with
    `result_base64` and `metrics`.
- Budget semantics:
  - `max_bytes` bounds the whole CallToolResult (atlas/docs/graph-context.md:55); `byte_quarter.v1` =
    ceil(bytes/4) (:74-75).
  - Context budget range: 0..`max_response_bytes` = 1 MiB (context_limits.py:75; config.py:23).
  - `result_bytes` = `model_dump_json(by_alias=True, exclude_none=True)` (protocol/results.py:38-40).
- Legacy tool shapes:
  - `view_file` returns an absolute `path`, `start_line/end_line/lines` (view_file.py:163-168).
  - `search_text` needs `pattern`, `scope` and `regex` (default true) and returns `{path,line,col,preview≤120}`.
  - `search_symbol` returns `definitions` (`symbols` rows: `path,line_start,line_end`) and `references`.
- `CodeGraph.build(since=None)` (codegraph.py:2442) returns :3592-3612 `snapshot_id, files_indexed,
  symbols, refs, edges, rationale, files_skipped, files_removed, extraction_state, extraction_cache
  {disk_hits, misses, miss_reasons, write_failures, predecessor_reused, parser_invocations}`.
  - A full build by a new writer on an unchanged repo parses 0 files (test_s09_contract.py:39-53).
  - `atlas-aci index` logs stats to stderr (`index_done`, __main__.py:168) and prints nothing.
- Pre-v3:
  - `eidolons.mcp.lock:13` pins `ghcr.io/rynaro/atlas-aci` 2.0.0 @ `sha256:e2542ef8…c7cf7`, present
    locally.
  - The HEAD Dockerfile prefetches grammars into `HOME=/tmp` for `--network none` indexing
    (Dockerfile:86-90). Whether v2.0.0 did is unverified.
  - The server is stdio-only (server.py:20,683).
- Hosts:
  - Tested in CI:
    - mcp 1.27.0 (uv.lock:407-408);
    - protocols 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25, plus unknown→negotiated
      (test_s18_stdio.py:32-35);
    - CPython 3.14 only (ci.yml:108);
    - ubuntu, plus macOS for the export-determinism job only (ci.yml:166);
    - a Docker python:3.12-slim smoke job with S06/S08 (ci.yml:426-434).
  - Release builds linux/amd64 and arm64 (release.yml:155).
  - `requires-python >=3.11`.
  - `hosts/{claude-code,copilot,cursor}.md` are documentation only.
- Pins: tools JSON sha `94f9567a…` / 1,631,472 bytes, 7 READ_ONLY_TOOLS (test_s21_isolation.py:42-87).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults)
1. A recorder (impure, run by the executor, not CI) writes immutable raw records as gzipped canonical
   JSON under `docs/evidence/v3/raw/`. A pure stdlib aggregator derives
   `docs/evidence/v3/release-report.{json,md}` (a canonical section plus a separate diagnostic section).
   Contract tests run the aggregator on committed records and on fixture records built in-test.
2. The frozen manifest `atlas/evals/v3/manifest.v1.json` holds:
   - the corpus with sha256s, tasks, variants and arms;
   - budgets: byte_quarter.v1 tokens 2000/8000/16000 = max_bytes 8000/32000/64000;
   - the cost categories, P0 ids and host matrix.
   Every variant cites one task-input digest.
3. Variants:
   - V0 `pre_v3_atlas`: the ghcr v2.0.0 image by digest, legacy tools, via the stdio runner with a
     server-argv hook;
   - V1 `v3_atlas`: HEAD, legacy tools plus op=context;
   - B `search_read`: a deterministic search_text→view_file policy on HEAD.
   Non-applicable cells are `not_applicable`, never zero.
4. The P0 gate runs pytest on the declared ids in a subprocess with the same interpreter, and all must
   pass. Docker-gated ids are excluded from P0. The flaky
   `test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition` is excluded.
5. There is no live budget, so every live engineering outcome is `unmeasured` with reason
   `no_operator_budget`.
6. README corrections are in scope; CLAUDE.md drift is a follow-up only.

### In
1. `atlas/evals/v3/manifest.v1.json`: frozen `atlas-eval-manifest.v1`.
2. `atlas/evals/span_metrics.py`: `union`, `metrics` and `body_and_sources` moved verbatim (behaviour
   unchanged, type hints added). `measure_context_yield.py` imports them from it.
3. `scripts/eval/v3_aggregate.py`: pure stdlib library + CLI (manifest validation, canonical/gzip IO,
   span conversion, budget prefix, aggregation, report JSON/MD).
4. `scripts/eval/v3_record.py`: recorder CLI (probe, retrieval, cost, p0, offline). It imports
   `v3_aggregate` for formats and loads `stdio_runner` via importlib.
5. `scripts/run-canaries.py` `--server-json` and `stdio_runner.run_cli` pass-through. The
   `dispatch` env is changed only if Day-1 (b) requires it.
6. Tests `mcp-server/tests/slices/test_s23_{contract,support,metrics,recorder,isolation}.py`.
7. `docs/evidence/v3/{README.md,release-report.json,release-report.md,raw/*.json.gz}`.
8. README.md: canary section fixed, plus an evidence pointer. CHANGELOG entry and the SETUP.md §7 fix
   via amendment.

### Out
- Any edit under `mcp-server/src`, `mcp-server/pyproject.toml`, `mcp-server/uv.lock`, or
  `mcp-server/tests/fixtures/`. No new MCP op, dependency or CI job.
- Release tag/deploy; invented thresholds or significance claims; live/paid model runs; S15 Rust.
- Re-running `capture_context_baseline.py` (S18 one-shot); regenerating
  `atlas/evals/baseline/result.json` (stale S01 sample, left as is); CLAUDE.md.
- Retrieval evaluation of op=retrieve/impact/diff/validation-plan (no labelled corpus). The S22
  comparison is only an offline proxy.

Declared paths (issue): `atlas/evals/*`, `scripts/run-canaries.py`, `scripts/eval/*`,
`mcp-server/tests/slices/test_s23_*.py`, `docs/evidence/*`, `README.md`. The amendment adds:
- `CHANGELOG.md`;
- `SETUP.md` (optional; if dropped, it becomes a follow-up);
- Ramza/receipt/backlog bookkeeping;
- `.spectra/evidence/aci-s23/*`.
`mcp-server/src` stays untouched.

Rightsize FULL 5: about 14 files, a novel evaluation harness, an external image dependency, med
stakes.

## Approach

### Decisions

D1 Formats. All JSON is canonical: sorted keys, `(",",":")`, `ensure_ascii=False`,
`allow_nan=False`, final LF (the `stdio_runner.canonical` rule).
- gzip uses `mtime=0`, `filename=""`, level 9.
- Identity is always the sha256 of the decompressed canonical bytes, never of the .gz (zlib may
  differ by platform).
- Ratios are `{num, den, value}`, where `value` is a Decimal string quantized to 6 places with
  ROUND_HALF_EVEN. No floats appear in canonical sections.

D2 `atlas-eval-manifest.v1` (closed; validated by `v3_aggregate.load_manifest`):
```
{format, slice:"ACI-S23", scope:{selected[], excluded[]},
 corpus:[{id:"s18-useful-spans", role:"labelled_retrieval", path, sha256:"07553bd3…"},
   {id:"self-source-fa75bec", role:"cost", path, sha256:"dcf0e79f…"},
   {id:"coverage-project", role:"cost", path:"mcp-server/tests/fixtures/coverage/project",
    tree_sha256 (canonical sorted [relpath, sha256] list)}],
 tasks:[{id, corpus, text, terms[]}],                      # verbatim from fixture; terms =
 task_input_sha256 = sha256(canonical(tasks)),             #  [A-Za-z_][A-Za-z0-9_]* in order, dedup
 budgets:{unit:"byte_quarter.v1", tokens:[2000,8000,16000], max_bytes:[8000,32000,64000],
   measure:"result_bytes_repo_normalized.v1", prefix_rule:"atlas-budget-prefix.v1"},
 variants:[{id:"V0", name:"pre_v3_atlas", task_input_sha256, server:{kind:"oci_image",
   image, digest, version:"2.0.0", index_argv[], serve_argv[], network:"none", pull:"never"}},
   {id:"V1", name:"v3_atlas", task_input_sha256, server:{kind:"checkout", ref:"HEAD"}},
   {id:"B", name:"search_read", task_input_sha256, server:{kind:"checkout", ref:"HEAD"}}],
 arms:[V0.legacy, V1.legacy (policy atlas-legacy-symbol-read.v1), V1.context.references,
   V1.context.inline (atlas-context.v1), B.search_read (atlas-search-read.v1)],
 not_applicable:[{arm:"V0.context.references"|"V0.context.inline", reason:"op_unavailable_pre_v3"}],
 policies:{legacy:{max_reads:8}, search_read:{max_reads:8, window_lines:10, regex:false, scope:"."},
   context:{schema_version:"atlas.graph.v1", max_candidates:32, max_inline_bytes:8192}},
 comparisons:[["V1.legacy","V0.legacy"],["V1.context.inline","B.search_read"],
   ["V1.context.references","V1.legacy"]],
 cost:{categories:["index.cold_full","index.warm_full","index.incremental_noop",
   "index.refresh_after_edit","query.retrieval","query.model"], counters[...],
   refresh_edit:{corpus→{path, append_bytes:"\n# atlas-v3-eval refresh\n"}}},
 engineering_outcomes:{live:["coding_task_success","model_tokens","model_cost","agent_turns"],
   live_budget:null, offline:["validation_plan_comparison"]},
 p0:{node_ids:[{id, category:"binding"|"snapshot"}], excluded:[{id, reason}]},
 host_matrix:[{dimension, value, status, evidence}], known_failures:[{id, kind, evidence}]}
```
The V0 argv is frozen at the end of Day 1. Any manifest edit changes `manifest_sha256`, and the
aggregator then rejects every raw record made under the old manifest.

D3 Raw record `atlas-eval-raw.v1`. There is one file per unit, named
`raw/<kind>.<arm|corpus|"all">.json.gz`:
```
{format, kind:"probe"|"retrieval"|"cost"|"p0"|"offline", manifest_sha256, status:"complete"|"not_run",
 not_run_reason?: "image_absent"|"docker_unavailable"|"offline_run_failed",
 # probe: variant, protocol_version, server_info, tools:[names], tools_list_sha256
 # retrieval: arm, variant, task_input_sha256, corpus_commit, repo_root,
 #   observations:[{observation_key, task_id, budget_tokens|null, step, tool, arguments,
 #     outcome, result_text, result_sha256}]
 # cost: corpus, variant, builds:[{category, counters{}}]   # build-return fields only
 # p0: python, pytest, results:[{node_id, testcases, outcome:"passed"|"failed"|"error"|"skipped"|"missing"}]
 # offline: name, result (validation_plan_comparison.comparison output)
 diagnostic:{run_ids[], producer{}, durations_seconds{key:s}, platform, recorder_argv}}
```
- `observation_key = sha256("atlas-eval-observation.v1:" + canonical({arm, task_id,
  task_input_sha256, budget_tokens, step, tool, arguments}))`.
- `result_text` is the exact client-side `CallToolResult.model_dump_json(by_alias=True,
  exclude_none=True)` string. `result_sha256` hashes its bytes, and the aggregator re-verifies it.
- Replay keys are `observation_key` and the content hashes. The stdio_runner `run_id`/producer
  identity is diagnostic only, so commit churn cannot change any canonical value.
- `repo_root` must contain no character that JSON escapes (the recorder refuses one). Then
  `result_bytes_repo_normalized` is `len(result_text.replace(repo_root, "/repo").encode())`. V0
  already serves `/repo`, so normalization removes host-path-length bias between arms.

D4 Policies are deterministic transcripts with no model:
- `atlas-legacy-symbol-read.v1`:
  1. Call `search_symbol(name=t)` for each term.
  2. Collect the definitions (dedup by path, line_start and line_end), ordered by term order, then
     path, then line_start.
  3. Call `view_file(path, start_line=line_start, end_line=line_end)` for the first 8.
- `atlas-search-read.v1`:
  1. Call `search_text(pattern=t, scope=".", regex=false)` for each term.
  2. Collect the matches (dedup by path and line), ordered by term, then path, then line.
  3. Call `view_file(path, max(1,line-10), line+10)` for the first 8.
- Both run as two `run_manifest` phases. The phase-2 manifest is a pure function of the phase-1
  responses and is stored in the raw record.
- `atlas-context.v1`: one `graph_query op=context` call per (task, budget), with
  `max_bytes = 4 × tokens` and the S18 candidate/inline limits.
- `atlas-budget-prefix.v1`:
  - Legacy arms: the admitted calls are the longest prefix of the transcript whose cumulative
    normalized bytes ≤ the budget's max_bytes.
  - Context: the single call is admitted if it is within that bound.
  - Error results count their bytes and deliver no spans.

D5 Useful-span metric (per arm, task and budget, over admitted calls only). Spans are converted to
`span_metrics.metrics()` source dicts against the frozen buffers:
- `view_file` produces an inline span. Its byte range is the lines `[start_line, end_line]` split
  with universal newlines (the view_file rule). It is admitted only if `"".join(lines).encode()` equals
  the frozen bytes; otherwise it fails closed with `content_mismatch`.
- `search_text` matches and `search_symbol` `references` produce reference spans of their whole line.
- `search_symbol` definitions produce reference spans of `line_start..line_end`.
- Context results go through `body_and_sources(raw, False)`.
- Paths: the repo root (V0 `/repo`) is stripped to give a repo-relative path, and the source_sha256
  is the fixture hash. An unknown result shape fails closed.

Reported fields:
- `useful_label_bytes`, `useful_reference_bytes`, `useful_inline_bytes`;
- `unique_*`, `redundant_*`;
- `inline_recall = useful_inline/label` and `reference_recall`;
- `inline_precision = useful_inline/unique_inline`;
- `calls`, `errors`, `result_bytes`, `result_bytes_repo_normalized`,
  `byte_quarter_tokens = ceil(norm/4)`, `bundles` (context).

Per-arm×budget rows are micro-sums over tasks. `paired_differences` (V1−V0 and others, per
`comparisons`) are integer differences only.

D6 Cost (canonical counters come only from `build()` returns). For each cost corpus and for S18,
using V1 in-process with a fresh repo copy and a fresh `.atlas`:
- `index.cold_full`: `CodeGraph(repo).build()`.
- `index.warm_full`: a new `CodeGraph(repo).build()`. Day-1 expects `parser_invocations == 0`.
- `index.incremental_noop`: `build(since="incremental")`.
- `index.refresh_after_edit`: append the declared bytes to the declared file, then
  `build(since="incremental")`.

Counters recorded: files_indexed, files_skipped, files_removed, symbols, refs, edges, rationale,
extraction_state, files_failed, extraction_cache.{disk_hits, misses, miss_reasons,
predecessor_reused, parser_invocations, write_failures}. `snapshot_id` goes to diagnostic.
- `query.retrieval` is derived from D5 rows.
- `query.model` = `{status:"unmeasured", reason:"no_operator_budget"}`.
- V0 index cost: `cold_full` counters parsed from the v2 `index_done` stderr event if present.
  Otherwise the category is `unmeasured` with reason `pre_v3_counters_not_exposed`. Other V0
  categories are `not_applicable`.
- Wall seconds and RSS are diagnostic only. Grammar caches are warm and declared.

D7 V0 offline execution:
- Preflight: `docker image inspect <image>@<digest>`. A missing docker binary records `not_run:
  docker_unavailable`; a missing image records `image_absent`. The recorder never pulls.
- Index: `docker run --rm --pull never --network none --user UID:GID -v <repo_v0>:/repo IMG index
  --repo /repo`.
- Serve argv: `docker run --rm -i --pull never --network none -v <repo>:/repo:ro IMG serve --repo
  /repo` (memex on the image's /memex volume). Day-1 fixes the exact v2 flags.
- No `--tmpfs /tmp`, because it would hide grammar caches under HOME=/tmp.
- V0, V1 and B use separate copies of one deterministic corpus git commit (fixed author, committer
  and date; the record_baseline precedent). Equal `corpus_commit` is a pairing proof.
- **This release run must include V0** (decision 3). `not_run` exists for the recorder contract and
  tests only. A committed V0 `not_run` fails the Day-4 gate.

D8 P0 gate. `v3_record.run_p0(ids, cwd=mcp-server)` runs
`[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", tmp, *ids]`.
- The env has every `ATLAS_ACI_*_DOCKER_IMAGE` removed.
- It parses the junit XML (stdlib `xml.etree`) and maps testcases back to ids by module and function
  (params stripped).
- Each id needs ≥1 testcase and all of them `passed`. A `missing`, skipped or failed id fails the gate.

P0 ids (categories):
- binding:
  - `test_s02_contract.py::{test_unrelated_receiver, test_owned_receiver, test_shadowed_name,
    test_cross_language_collision, test_unknown_alias, test_binding_replay,
    test_no_cross_file_module_guess, test_additional_binding_constructs_fail_closed}`;
  - `test_s10_contract.py::{test_call_only_reuses_nonempty_same_language_unit,
    test_unknown_dependency_fallback, test_incremental_replay}`;
  - `test_s10_dependencies.py::{test_incoherent_outputs_force_full_repair,
    test_untrusted_witness_full_fallback, test_fatal_selected_binding_preserves_pinned_g1}`.
- snapshot:
  - `test_s03_contract.py::{test_active_request_pinned, test_failed_publish,
    test_concurrent_readers_keep_their_own_generation, test_generation_replay,
    test_consumed_buffer_hash_and_failure}`;
  - `test_s08_contract.py::{test_snapshot_source_coherence, test_active_lease_retained,
    test_snapshot_replay}`;
  - `test_s08_integration.py::{test_coherently_rehashed_foreign_binding_fails_before_exposure,
    test_prune_during_unexposed_acquisition_retries_latest}`;
  - `test_s08_lease_footprint.py::test_rehashed_compact_or_raw_range_contradiction`;
  - `test_s09_contract.py::{test_same_stat_changed_content, test_producer_change}`;
  - `test_s12_storage.py::{test_staged_upgrade_preserves_history_and_held_source,
    test_genuine_epoch6_reader_and_writer_conversion}`;
  - `test_s17_storage.py::test_genuine_held8_upgrade9_overlay_and_replay`.
- Excluded:
  - the flaky nonregular-pack test;
  - every `test_s*_docker.py` (env-gated);
  - `test_s01_contract.py::test_call_deadline_after_initialization` (known failure under uid 1000;
    not P0).
- CI cost: these 33 ids run a second time in the subprocess, measured on Day 1. The plan assumes
  ≤120 s extra on the CI pytest job. Above 300 s, the executor stops and asks the orchestrator.

D9 `atlas-release-report.v1`:
```
{format, canonical:{manifest_sha256, task_input_sha256, inputs:[{name, sha256}] (sorted),
   pairing:{task_input_sha256, corpus_commit, arms:[{arm, tasks, status}], paired:true},
   retrieval:{budget:{unit,tokens,max_bytes,measure,prefix_rule},
     span_matrix:[{arm, budget_tokens, max_bytes, status:"measured"|"not_applicable"|"not_run",
       reason?, metrics{}}], per_task:[...], paired_differences:[...]},
   cost:{"index.*":{corpus:{variant:{status, counters?}}}, "query.retrieval":[...],
     "query.model":{status:"unmeasured", reason:"no_operator_budget"}},
   engineering_outcomes:{live:{<name>:{status:"unmeasured", reason:"no_operator_budget",
     value:null}}, offline:{validation_plan_comparison:{label:"offline_fixture_proxy", ...}}},
   release_integrity:{status:"pass"|"fail", total, passed, results:[{node_id, category, outcome}]},
   host_matrix:[...], known_failures:[...], limitations:[...],
   gates:{paired_inputs, release_integrity, span_matrix, cost, live_agent_evaluation:"incomplete"}},
 canonical_sha256: sha256(canonical(canonical)),
 diagnostic:{durations_seconds, run_ids, producers, platforms}}
```
- `release-report.md` is rendered from `canonical` only (byte-replayable).
- The aggregator fails closed with typed `EvalInputError(code)` on any of:
  - `manifest_mismatch`, `hash_mismatch`, `duplicate_input`;
  - `unpaired_inputs` (a task_input_sha256 or corpus_commit mismatch, or a missing/extra task per arm
    or budget);
  - `incomplete_matrix` (a measured arm lacks a budget row);
  - `missing_cost_category`;
  - `live_without_budget` (any live value or `kind:"live"` record while `live_budget` is null);
  - `content_mismatch`, `unknown_shape`.

D10 Purity and gates. `v3_aggregate.py` imports only: `__future__, argparse, base64, decimal, gzip,
hashlib, importlib.util, json, math, pathlib, re, sys, typing, collections, dataclasses`.
- `importlib.util` is used only to load `atlas/evals/span_metrics.py`.
- No `time`, `datetime`, `random`, `os.environ`, `subprocess`, `socket`, `platform` and no
  `atlas_aci`. An AST test enforces this.
- Both scripts are ruff/format/mypy clean under the existing CI steps (`../scripts/`).
- Module names `v3_aggregate` and `v3_record` are unique for mypy, with no `__init__.py`. Tests load
  them by importlib spec (test_s22_eval precedent).

### Limitations (static list in the report)
1. `no_live_agent_evaluation_no_operator_budget`
2. `four_labelled_tasks_742_bytes_no_generalisation`
3. `no_thresholds_or_significance_claimed`
4. `policies_are_deterministic_transcripts_not_agents`
5. `byte_quarter_is_not_model_tokens`
6. `result_bytes_client_measured_repo_root_normalized`
7. `protocol_version_negotiated_per_server_not_controlled`
8. `v0_limited_to_legacy_tools`
9. `offline_validation_comparison_is_fixture_proxy`
10. `grammar_cache_warm_in_cold_builds`
11. `durations_diagnostic_only`
12. `retrieve_impact_diff_ops_not_span_evaluated`

### Work plan (timebox 5 engineering days)

Day 1 — qualification before product code. Scratch outputs go to
`.spectra/evidence/aci-s23/qualification.json`; base invariants go to `base-invariants.json` (tools
sha/bytes, a sha manifest of `mcp-server/src`, pyproject and uv.lock, and the full pytest result).
- (a) V0: inspect the image by digest. `--network none --pull never`: `--help`, `index --help`,
  `serve --help`. Index the S18 corpus offline, then over SDK stdio: initialize + tools/list. Record
  the negotiated protocol and the legacy arg schemas.
- (b) `run_manifest(server=V0 argv)` runs one search_symbol and one view_file task to `success`
  (proves env/PATH for docker under the SDK).
- (c) `span_metrics.metrics`/`body_and_sources` over every committed S18 `local-yield.json` row
  (48 + 24) reproduce the committed `metrics` exactly.
- (d) HEAD V1 context at 8000/32000/64000 and both legacy policies run on S18. Errors are allowed
  and recorded. view_file content equals the frozen bytes.
- (e) All 33 P0 ids are collected and pass. Record runtime, python and uid.
- (f) Counter semantics on S18 + self-source: cold `parser_invocations > 0`; `warm_full`,
  `incremental_noop` `== 0`; `refresh_after_edit == 1`.
- Freeze `manifest.v1.json` (V0 argv from (a)).

STOP RULE:
- (a) image absent → BLOCKED (this release run needs V0).
- (a) offline index fails, e.g. a grammar fetch → stop and escalate. The options are the
  orchestrator's call (network for the V0 index step only, recorded; or V0 `not_run` with the
  release goal unmet). Never substitute another build.
- (b) fails → patch only the `stdio_runner.dispatch` env merge, re-qualify; a second failure →
  BLOCKED.
- (c) mismatch → fix the refactor until exact. Never edit expected values.
- (e) any failure → BLOCKED: a real regression, so AC-002 cannot pass honestly. Runtime above 300 s
  → ask the orchestrator.
- (f) differs → record the actual semantics and rename/redefine the category. No stop unless
  `warm_full` parses on every corpus (then report it as observed; never claim reuse).

Day 2:
- `span_metrics.py` refactor;
- `v3_aggregate.py`;
- `test_s23_support.py` (builders for in-test raw records over real S18 buffers);
- `test_s23_metrics.py`;
- AC-001/003/004/005/006 on fixture records.

Day 3:
- `v3_record.py` (probe, retrieval phases, cost, p0, offline);
- `--server-json` in run-canaries plus `run_cli` pass-through;
- `test_s23_recorder.py`;
- AC-002.

Day 4:
- release recording: all arms including V0, the cost corpora, P0 and offline;
- aggregate; commit raw + report;
- `docs/evidence/v3/README.md`, README.md, CHANGELOG, SETUP.md §7;
- committed-record assertions in the contract tests.

Day 5:
- gates: full pytest; ruff/format on `tests/` and `../scripts/`; mypy `src/` and `../scripts/`;
- G-SRC/G-MCP;
- RUN-01/02; buffer.

Cut order (each cut becomes a listed limitation):
1. md/README polish;
2. V0 index counters (→ `unmeasured`);
3. per-task tables in md (kept in JSON);
4. the coverage-project cost corpus;
5. the offline S22 proxy (→ `available_not_collected`);
6. probe detail beyond the negotiated version and tool names.

Never cut:
- manifest pairing, all three budgets, V0 in the release run;
- every declared cost category, the P0 gate;
- the unmeasured live gate, replay;
- the invariance gates.

### Independent checker conditions (binding; override conflicting plan text)

Checker `atlas-s23-plan-checker` (read-only review against base 1cedf60): APPROVE_WITH_CONDITIONS, no BLOCKER. Review record: `.spectra/plans/aci-s23.review.json`. Conditions 1-16 below are binding.

Orchestrator resolution of condition 1 (recorded 2026-11-04, observed on the executor host): the host has Docker Desktop (socket `/var/run/docker.sock` -> `~/.docker/run/docker.sock`) but the host docker CLI is a macOS binary and the dev image `atlas-aci-dev-s16` has no docker CLI. Topology: the recorder runs in a derived image `atlas-aci-dev-s23-recorder` = `FROM atlas-aci-dev-s16` plus a pinned static linux docker CLI (download.docker.com static tarball, version and sha256 pinned in `.spectra/evidence/aci-s23/Dockerfile.recorder`), started with `-v /var/run/docker.sock:/var/run/docker.sock` and the repository and scratch roots mounted at IDENTICAL host paths (`-v <abs>:<abs>`) so nested `docker run -v` paths resolve on the daemon. Day-1 step 0 proves: `import mcp, atlas_aci`; `docker image inspect` of the V0 digest; a nested bind-mount round trip; socket permission under the chosen uid. Failure → BLOCKED before product code. Contract tests never need docker.

Orchestrator decision on the Day-1(a) escalation (recorded 2026-11-04; evidence `.spectra/evidence/aci-s23/qualification.json`): the v2.0.0 image does not prefetch tree-sitter grammars (tree-sitter-language-pack 1.6.0, HOME=/tmp empty), so an offline V0 index exits 0 with `files_indexed=0` — a silent zero. Resolution, in order: (A, chosen) fetch the pinned grammar set ONCE with network in a separate, recorded step into a cache directory whose per-file sha256 manifest is committed as a declared input (`.spectra/evidence/aci-s23/v0-grammar-cache.json`); mount that cache at the image's HOME cache path; V0 `index` and `serve` stay `--network none --pull never`. (B, fallback only if A cannot be made to work) network for the V0 `index` step only, serve offline, recorded as a deviation in the manifest and report. Under either, V0 retrieval records are admissible only if the V0 index event shows `files_indexed` = corpus file count and `symbols > 0`; otherwise the recorder aborts (never a silent-empty V0). V0 `not_run` remains unacceptable for this release run. HEAD is unaffected (its image prefetches; V1/B run from the checkout).

1. **MAJOR — executor topology.**
   - Evidence:
     - S22 ran its tests via `docker run --user 1000:1000 -e HOME=/hc … atlas-aci-dev-s16 /opt/venv/bin/python -m pytest` (`.spectra/state/aci-s22.execution.json:119`). There is no `mcp-server/.venv` on the host.
     - Plan lines 546-551 assume one process that has `uv`, mcp and `atlas_aci`, plus a docker CLI and daemon, and bind-mounts a host path (`-v <repo_v0>:/repo`).
     - A dev container normally has no docker socket, and its bind-mount paths would not be host paths.
   - Condition: add Day-1 step 0, a topology preflight. It records where the recorder runs and checks that `import mcp, atlas_aci`, `docker image inspect <digest>` and a bind-mount round trip all work in that one process. If the recorder runs in a container, the repo must be mounted at an identical host path. Otherwise the result is BLOCKED before any product code.
   - The tests added in this slice (`test_s23_*.py`) must not need a docker CLI.

2. **MAJOR — `result_text` fidelity.**
   - Evidence: `dispatch` returns a `model_dump(mode="json", …)` dict (`stdio_runner.py:209`). `run_manifest` stores it with `canonical()`, which uses `sort_keys` (`:29-39`, `:399-407`). Plan D3 (:192) claims the exact `model_dump_json` string.
   - Condition: define `result_text` as the deterministic re-serialization of the ledger dict (compact separators, `ensure_ascii=False`). Alternatively add an optional capture hook, which is a stdio_runner edit.
   - Day-1(d) must prove, on the S18 context calls, that the client-side length equals the server `result_bytes(...)` (`protocol/results.py:38-40`) for the negotiated protocol. State in the manifest which one is used.

3. **MAJOR — V0/V1 transport failures must not look like zero yield.**
   - Evidence: the default deadline is 10 s (`stdio_runner.py:329`) and covers `initialize` plus the call (`:206-209`). A cold `docker run` plus Python import can exceed it. `run_manifest` converts that into a recorded `timeout` or `transport_error` outcome (`:404-406`).
   - Condition: the recorder uses a V0 deadline of at least 60 s.
   - The recorder aborts the release run if any V0, V1 or B call outcome is `timeout` or `transport_error`. Only tool-level errors (`tool_error`, `application_error`) may be recorded as errors.
   - The probe also records the image id, `RepoDigests` (matching the manifest digest) and `serverInfo`.
   - SDK env: I recall that `stdio_client` merges `env` over a default environment (HOME, LOGNAME, PATH, SHELL, TERM, USER), so PATH should survive. `DOCKER_HOST`, `DOCKER_CONFIG` and `DOCKER_CONTEXT` do not. Day-1(b) must prove docker works under `dispatch`. The fallback patch must pass the `DOCKER_*` variables through.

4. **MAJOR — the "33 P0 ids" claim is wrong.**
   - Evidence: I grepped every id and all exist, but they total **30** (binding 14, snapshot 16). Plan lines 273-307 say 33.
   - Condition: correct the count in the plan and manifest. `test_release_integrity_gate` asserts `len(ids)` equals the manifest's count and the junit testcase count is at least that many.

5. **MAJOR — Day-1(c) premise is false for baseline rows.**
   - Evidence: `local-yield.json` has `result_base64` only on the 48 context rows (grep count 48). The 24 `baseline_reference_only` rows carry no bytes (`measure_context_yield.py:125-144`). Their bytes are in `baseline-checkpoint.json` `lexical_results`.
   - Condition: parity reads the baseline rows from `baseline-checkpoint.json` using `body_and_sources(raw, True)` and `tasks[row["task"]]["useful_spans"]`. It asserts 48+24 non-vacuously and checks `selected_candidates`.

6. **MAJOR — the `span_metrics` refactor is untested.**
   - Evidence: nothing under `mcp-server` imports `measure_context_yield`, so no test covers it after the edit. The file sha `c3b6fddf…` is pinned in the S18 and S22 receipts (`aci-s22.execution.json:95`).
   - Condition: do not edit it. Ship `span_metrics.py` and verify parity against the S18 module loaded via importlib. If you do edit it, add a test that loads it and asserts `module.metrics is span_metrics.metrics`, and that the script still resolves its sibling import when run directly.
   - CI ruff and mypy do **not** cover `atlas/evals/` (`ci.yml:120-139`). Run ruff and mypy on `span_metrics.py` explicitly.

7. **MINOR — path claim in D3/D5 is wrong.**
   - Evidence: `view_file` echoes the argument, so `path` is `str(Path(arg))` (`view_file.py:52,165`), and it is relative if called relatively. `search_text` returns paths relative to `config.repo` (`search_text.py:92`). `search_symbol` rows are repo-relative.
   - Condition: policies call `view_file` with repo-relative paths. The span converter accepts relative and `/repo`-prefixed paths. Add a normalization non-vacuity test: record the replacement count and assert no host tmp prefix remains in the normalized text. Handle the macOS `/var` vs `/private/var` difference.

8. **MINOR — `search_symbol` is an exact-name match** (`codegraph.py:4990`). The `phrase` task has terms retry, transport and timeout, so V0 and V1 legacy return no definitions. State this in the report as a property of the policy, not a defect.

9. **MINOR — V0 argv can be fixed at plan time.**
   - Evidence: tag `v2.0.0` exists (`packed-refs`, afd3491) and CHANGELOG `[2.0.0]` (:110) says epoch 5, `graph.5.db`. `git show v2.0.0:mcp-server/src/atlas_aci/__main__.py` is read-only.
   - Condition: read the v2 CLI flags and the `view_file` and `search_symbol` signatures from that tag before Day 1.
   - The HEAD image uses `USER atlas` uid 10001 with `HOME=/tmp` and prefetched grammars (`Dockerfile:76,89,97`). `--user UID:GID` may break that. Mirror the CI invocation (`ci.yml:277`, no `--user`) first.
   - The V0 index step runs offline; Day-1(a)'s stop rule already covers failure.

10. **MAJOR — docker classification and tests.**
    - Evidence: D7 distinguishes only a missing binary from a missing image. A down daemon, a dev container without docker, and an absent image all look similar.
    - Condition: classify by exit code and stderr. A daemon-down result maps to `docker_unavailable`.
    - `test_s23_recorder` tests the `image_absent` and `docker_unavailable` paths with a fake `docker` shim script on PATH, not the real daemon.
    - The B-arm smoke test needs `rg`. CI installs it (`ci.yml:98`). Skip with a reason if it is absent. Keep the smoke to at most 6 sessions.

11. **MINOR — P0 subprocess hygiene.**
    - Add `--basetemp <tmp>` and a subprocess timeout (about 900 s).
    - Strip `PYTEST_*`, `COVERAGE_*` and `ATLAS_ACI_*_DOCKER_IMAGE` from the environment.
    - No conftest side effects exist (conftests are only under `tests/fixtures/coverage`).
    - There is no recursion risk, because no `test_s23` id is in P0.
    - My estimate is 60-150 s for 30 ids; Day 1 measures it. Pre-decide the response to more than 300 s: BLOCKED with a recorded decision request, never silently weakening AC-002.

12. **MINOR — cost semantics (verified).**
    - `build()` returns `snapshot_id, binding_resolution, extraction_cache{disk_hits, misses, miss_reasons, write_failures, predecessor_reused, parser_invocations}, files_indexed, symbols, refs, edges, rationale, files_skipped, files_removed, unsupported_skipped, extraction_state, files_failed, schema_epoch` (`codegraph.py:3592-3612`).
    - `build(since=…)` exists and requires `previous_base == base_inputs` (:3113).
    - A warm full build by a new writer has `parser_invocations == 0` (`test_s09_contract.py:39-53`). Parse failures are cached too (:3282), so S18's `incomplete.py` should not re-parse.
    - Condition: add `unsupported_skipped` and `binding_resolution` to the counters or drop them explicitly.
    - V0 `index_done` stderr counters are unlikely to exist, so expect `unmeasured: pre_v3_counters_not_exposed`.
    - The self-source corpus is a `.json.gz` fixture (`fixtures/s08/self-source-fa75bec.json.gz`, 25 files). The manifest must state the materialization rule and verify per-file sha256 against its provenance `files[]`.

13. **MINOR — lint and mypy.**
    - `scripts/` is covered by ruff and mypy in CI (`ci.yml:128-139`). `mcp-server/pyproject.toml` has no mypy config (non-strict), and ruff uses line-length 100 with rules E, F, W, I, B, UP, SIM, RUF.
    - There is no ruff config at the repo root, so which config applies to `../scripts` is unclear.
    - Condition: run the exact CI commands from `mcp-server/` on Day 2 and keep the output they require. Check that the `import v3_aggregate` sibling import resolves under mypy and ruff-isort.
    - Handle `spec is None` and `loader is None` in `importlib` code. Tests are not mypy-checked.

14. **MINOR — determinism.**
    - gzip is fine because identity is the decompressed sha.
    - Define `value` for a zero denominator, for example `null` with status `undefined`. Wrap the Decimal quantize in a local context.
    - `corpus_commit` is deterministic only if you fix author, committer and dates, `commit.gpgsign=false`, `core.autocrlf=false`, an empty template dir, and `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` to `/dev/null`. The `record_baseline.py:24-47` precedent sets only the dates and a couple of `-c` options.

15. **MINOR — host matrix honesty.**
    - "CPython 3.12 tested (smoke)" is really the server runtime in the Docker image only, driven by a 3.14 host client (`ci.yml:426-434`). Say so.
    - The macOS row ("release run host if macOS") conflicts with a deterministic canonical section, since platform is diagnostic. Make it static: "CI export-determinism job only".
    - The protocol row covers `op=context` only (`test_s18_stdio.py:32-53`). Say so.
    - Add a test that each `file:line` evidence anchor still contains its expected token (line numbers rot).
    - `release.yml:155` linux/arm64 as "built, not run" is correct.

16. **MINOR — scope and wiring.**
    - `scope.txt` covers all of: `atlas/evals/README.md` (by `atlas/evals/*`), the `run-canaries.py` and `stdio_runner.py` edits, `CHANGELOG.md` and `SETUP.md`.
    - Make `run_cli` read `getattr(args, "server_json", None)`.
    - Do not touch `mcp-server/src`.
    - Assert a size cap on the committed raw directory in `test_s23_isolation`.

Counters carry no uid assumptions. The contract tests need no docker once item 10 is applied.

## Stories

As a maintainer publishing v3, I want paired, budgeted, replayable evidence, so that every claim in
the release report can be regenerated from committed raw records and frozen inputs.

As an adopter, I want an honest host/SDK matrix and explicit `unmeasured` live outcomes, so that I
never mistake fixture accounting for agent success.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s23.criteria.md` (sha256 `3a7f63e2…0529`). They are quoted
verbatim below; if a byte differs, the criteria file governs.

```
### AC-S23-001 (event-driven)
GIVEN a comparison experiment runs
WHEN its manifest is inspected
THEN all variants SHALL use the same frozen task inputs.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_paired_inputs

### AC-S23-002 (event-driven)
GIVEN release evidence is assembled
WHEN known binding and snapshot regressions are evaluated
THEN all declared P0 regression cases SHALL pass.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_release_integrity_gate

### AC-S23-003 (event-driven)
GIVEN retrieval evaluation completes
WHEN the report is generated
THEN the report SHALL include useful-span results at each declared budget.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_span_budget_matrix

### AC-S23-004 (event-driven)
GIVEN cost measurements are aggregated
WHEN the report is generated
THEN the report SHALL include the declared indexing and query cost categories.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_cost_accounting

### AC-S23-005 (unwanted-behavior)
GIVEN no live agent evaluation was executed
WHEN the report is generated
THEN live engineering outcomes SHALL be marked unmeasured.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_incomplete_live_gate

### AC-S23-006 (event-driven)
GIVEN the same immutable run records are aggregated twice
WHEN both reports are generated
THEN their canonical metrics SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_report_replay

### AC-S23-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S23-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

Test setup:
- The committed records live in `docs/evidence/v3/raw/`, with the manifest in `atlas/evals/v3/`.
- Fixture records are built in-test by `test_s23_support.make_records(...)` from the real S18 buffers
  (hand-checkable spans).
- Cost records come from real `CodeGraph.build()` on tmp repos.
- No mocks of behaviour under test. Every `all(...)` is preceded by a non-empty assertion.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_paired_inputs | **Manifest:** `task_input_sha256 == sha256(canonical(tasks))`; every variant cites it; tasks equal the S18 fixture (sha `07553bd3…`); every corpus sha matches its file on disk. **Committed records:** each retrieval record has that digest and the same `corpus_commit`; per arm (context: per budget), the task-id set equals the manifest's; report `pairing.paired` is true. **Negative controls** (fixture records): an arm with one altered task text, a missing task, or another `corpus_commit` → `EvalInputError("unpaired_inputs")`. |
| 002 | test_release_integrity_gate | **Manifest:** P0 ids are non-empty and contain both categories; no excluded id appears; no id is in a `*_docker.py` file. **Run:** `run_p0(ids)` in a subprocess with the same interpreter → each id ≥1 testcase, all `passed`, none `missing/skipped`. **Committed report:** `release_integrity.status == "pass"` with `results` ids == the manifest ids. **Fixture:** a p0 record containing one `failed` → report `status "fail"` and gate `fail`. A misspelt id → `missing` (non-vacuous id mapping). |
| 003 | test_span_budget_matrix | **Fixture records:** a V1.legacy transcript (`view_file payments.py 1..k`) gives a hand-computed `useful_inline_bytes` per budget (prefix rule visible: the smallest budget admits fewer calls). **Report:** `span_matrix` has exactly one row per (arm, budget) for tokens {2000, 8000, 16000}, with all metric keys; V0.context.* rows are `not_applicable` with `op_unavailable_pre_v3`, never 0. **Committed report:** all 5 measured arms × 3 budgets are `measured`, and md has a table row per budget. Removing one budget's records → `incomplete_matrix`. |
| 004 | test_cost_accounting | **Real cost:** `v3_record.measure_index_costs` on a 2-file tmp repo → aggregate → categories == manifest list; cold `parser_invocations == 2`; warm_full and incremental_noop `== 0`; refresh_after_edit `== 1`; `query.retrieval` has `calls/result_bytes/byte_quarter_tokens` per arm and budget; `query.model` is unmeasured; no duration key in `canonical`. **Committed report:** every index category per cost corpus for V1 (V0 per D6). Dropping a category → `missing_cost_category`. |
| 005 | test_incomplete_live_gate | **Fixture and committed reports:** every manifest live outcome is `{status "unmeasured", reason "no_operator_budget", value null}`; `gates.live_agent_evaluation == "incomplete"`; a walk of `canonical` finds no non-null `model_tokens/model_cost/coding_task_success/agent_turns`; md says "unmeasured (no_operator_budget)". The offline proxy is labelled `offline_fixture_proxy`. A planted live record or value → `live_without_budget`. |
| 006 | test_report_replay | **Replay:** aggregating the committed raw twice in-process, and twice via CLI into two tmp dirs → byte-identical `canonical` and md, equal `canonical_sha256`, and equal to the committed report's `canonical`/md. **Diagnostic-only perturbations** of a copy (durations ×2, new run_id/producer, gzip level 1, files renamed and reverse-ordered) → identical canonical. **Non-vacuity:** one `result_text` byte changed (sha recomputed) → different `canonical_sha256`; a stale sha → `hash_mismatch`. |
| RUN-01 | gate | SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` + `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt is `.spectra/state/aci-s23.execution.json`. |

Supporting tests:
- `test_s23_metrics.py`:
  - Day-1 (c) as a test;
  - line→byte conversion with universal newlines, and `content_mismatch`;
  - repo-root normalization, and refusal of escapable roots;
  - prefix rule edge cases (exact fit, first call over budget → 0 calls);
  - Decimal rounding.
- `test_s23_recorder.py`:
  - HEAD stdio smoke on one task with both legacy policies and one context budget → valid raw record;
  - V0 `not_run` via a nonexistent docker path (`docker_unavailable`) and an absent image tag
    (`image_absent`, never pulled);
  - `--server-json` through run-canaries (S01 behaviour unchanged).
- `test_s23_isolation.py`:
  - G-MCP: tools JSON sha/bytes `94f9567a…`/1631472 and 7 tools;
  - the D10 AST purity check;
  - README contains no `--canaries atlas/evals/canary-missions.md` and documents `--canaries-dir` and
    `--host stdio`;
  - the raw dir holds only `*.json.gz` and every file decodes to `atlas-eval-raw.v1`.

Gates beyond the ACs:
- G-SRC: `git diff --exit-code <base> -- mcp-server/src mcp-server/pyproject.toml mcp-server/uv.lock
  mcp-server/tests/fixtures`.
- G-MCP (above).
- All existing tests pass. The only allowed local failure is the known uid-1000 S01 case, recorded in
  `known_failures`.

Commands (record exits):
```sh
cd mcp-server
uv run pytest tests/slices/test_s23_contract.py -q
uv run pytest tests/slices/test_s23_*.py tests/slices/test_s01_contract.py tests/slices/test_s18_*.py -q
uv run pytest -q
uv run ruff check src/ tests/ ../scripts/ --exclude fingerprint-fixture
uv run ruff format --check src/ tests/ ../scripts/ --exclude fingerprint-fixture
uv run mypy src/ && uv run mypy ../scripts/
# release run (executor host with docker + local V0 image):
uv run python ../scripts/eval/v3_record.py --manifest ../atlas/evals/v3/manifest.v1.json \
  --out ../docs/evidence/v3/raw
uv run python ../scripts/eval/v3_aggregate.py --manifest ../atlas/evals/v3/manifest.v1.json \
  --raw ../docs/evidence/v3/raw --out ../docs/evidence/v3
```

Host matrix (frozen in the manifest, rendered in the report; the probe adds the negotiated row):

| Dimension | Value | Status | Evidence |
| --- | --- | --- | --- |
| MCP client SDK | mcp 1.27.0 (uv.lock) | tested | release run probe + arms; test_s18_stdio |
| Protocol | 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25; unknown→negotiated | tested | test_s18_stdio.py:32-35 (CI) |
| Transport | stdio | tested | server.py:683; all arms |
| Transport | HTTP/SSE | unsupported | not implemented |
| CPython | 3.14 | tested | ci.yml:108 |
| CPython | 3.12 | tested (smoke) | Docker python:3.12-slim, CI S06/S08 job |
| CPython | 3.11, 3.13 | unverified | requires-python >=3.11 only |
| OS | Linux x86_64 | tested | CI ubuntu |
| OS | macOS | tested (partial) | CI export-determinism job; release run host if macOS |
| OS | Windows | unverified | no CI or evidence |
| Image | linux/amd64 | tested | CI docker job |
| Image | linux/arm64 | documented | built by release.yml:155, not run in CI |
| Hosts | Claude Code, Copilot, Cursor | documented | hosts/*.md, no automated run |
| Canary adapters | claude-code-api, copilot, cursor | unsupported | run-canaries.py:333 |
| Pre-v3 server | atlas-aci 2.0.0 @ e2542ef8… | tested (this eval) | V0 records |

Docs (`docs/evidence/v3/`):
- `README.md`: purpose, prerequisites (uv sync, docker, the local V0 image by digest, no network),
  exact commands, units, record formats (D2/D3/D9), replay instructions, limitations, follow-ups;
- `release-report.{json,md}`;
- `raw/`.
README.md: the canary section rewritten and a "v3 evidence" pointer. CHANGELOG: an Unreleased
ACI-S23 entry.

## Confidence

Computed with `ramza-score`. The aggregator is pure and its replay is mechanical. The measured
functions and S18 metrics are reused by refactor, with an exact parity check. No product code
changes, so the invariants cannot churn.

Main uncertainties, all covered by Day-1 checks with a stop rule:
- whether V0 runs offline, and its v2 CLI flags;
- the SDK env needed to launch docker;
- P0 runtime in CI;
- warm/incremental counter semantics.

## Rejected Alternatives

- Live agent runs: there is no operator budget (excluded by the issue).
- Extending `measure_context_yield.py` to write the report directly: there is no raw/aggregate
  split, so AC-006 replay would depend on re-execution and timings.
- Recording inside contract tests (CI): it would need docker plus the V0 image in CI and makes CI
  the producer of evidence.
- Using `run_id` as the replay key: it churns per commit via `producer_identity`.
- Recomputing result bytes with pydantic in the aggregator: it breaks stdlib purity. The exact
  `result_text` string is stored instead.
- Budgets in raw bytes 2048/8192/16384: 2048 is all INVALID_BUDGET and 4000 was empty or erroring in
  S18. Byte_quarter tokens are the declared unit.
- Building a HEAD Docker image for V1: it needs network, and the host checkout is HEAD exactly.
- pytest markers for P0: they would edit many out-of-scope test files. Explicit node ids are
  verifiable.
- Reusing S18 numbers: the runtime changed after S18, so the measurement is re-run (S18 is used only
  for parity).

## Risks

- v2.0.0 cannot index offline (grammar fetch) → Day-1 (a) stop/escalate; never fake V0.
- v2 legacy argument or result shapes differ → common subset frozen on Day 1; unknown shape fails
  closed.
- Absolute repo paths inflate bytes → repo-root normalization (D3) plus the declared measure.
- Different negotiated protocols (dual output) bias bytes → probe records them; limitation 7.
- Tiny labelled corpus → no generalisation claims and no thresholds; paired differences only.
- P0 subprocess doubles CI time → measured on Day 1, 300 s escalation.
- mypy module resolution for `scripts/eval` → unique names; Day-2 `mypy ../scripts/` early.
- gzip bytes differ by platform → identity is decompressed canonical bytes.
- Committed report drift after aggregator edits → AC-006 compares it with a fresh aggregation, and
  RUN-01 holds.
- Timebox 5 days → apply the cut order; report the unfinished items as named follow-ups.
