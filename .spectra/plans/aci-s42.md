---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-06T18:00:00Z
slice_id: ACI-S42
issue: https://github.com/Rynaro/atlas-aci/issues/63
---
# ACI-S42 - validate shared-operation isolation and publish deployment evidence

## Scope

Issue #63, base `4f470d73a80fcf0e797728b1e45bccc77bf3a0a5` (main after PR #107 / S41; S01-S41 merged except S15, plus the security re-lock, mcp 1.30.0). Branch `aci-s42`. This is the last slice.
- Marker `<!-- atlas-aci:slice:ACI-S42 -->` verified. Criteria digest `3930f6ad8488a435e7ea980c700c51b2a899539d37b96858bc984143cb0ebdab`: 6 product ACs in `test_s42_contract.py`, plus RUN-01/02.
- Risk **P1**. Timebox **3 engineering days**. `ramza-rightsize` tier **lite** (score 4), planned with full-tier rigour; an independent checker is run.
- **Dependencies PRESENT on the base:**
  - S23: `scripts/eval/v3_{aggregate,record}.py`, `docs/evidence/v3/`.
  - S38: `protocol/revisions.py` `negotiate` :159, `VERIFIED_SDK` :43, REVISIONS :32.
  - S39: `serve --transport http`, `InflightGate` http.py:201.
  - S40: `serve-shared` `__main__.py`:309, `query/shared_http.py`, `authz.py`.
  - S41: `atlas-aci jobs` `__main__.py`:2852-2975, `job_runner.py` FAULT_ENV :65.

Observed facts (verified at planning, base 4f470d7):
- **Shared config.** The keys are closed (shared_http.py:324-428).
  - `listen.max_inflight` defaults to 8 (1-256). `max_inflight_per_principal` defaults to max(1, n//2) and may not exceed max_inflight.
  - `limits.*` defaults: 200/120 calls; leases 1; graph leases 4, with `2*leases <= graph leases` (:477); total lease state 1 GiB.
  - Port 0 is allowed. Issuer and `resource_base_url` must be https.
  - `test_execution_enabled` is refused.
- **Router order** (shared_http.py:880-942): route, POST, credentials (401 `credentials_missing|invalid` + WWW-Authenticate), policy and repository (403 `repository_forbidden`), then the per-principal slot.
  - A full per-principal slot gives 503 `principal_inflight_limit` + `retry-after: 1` (:925-931). That is before any body read.
  - Then comes the S39 app: the shared `InflightGate` gives 503 `inflight_limit` (http.py:330-333).
- **Stopped line.** `shared_http_stopped admitted= rejected= peak=` is logged on stop (:1110-1116) from the SHARED gate.
  - `rejected` counts gate 503s only; router per-principal 503s are not in it.
  - Logs use structlog ConsoleRenderer to stderr, without colors when stderr is not a tty (`__main__.py`:72-82). `shared_http_listening ... port=` carries the bound port.
- **tool_call lines.** `SharedEnforcement.record` calls `Enforcement.record` (authz.py:824; enforcement.py:186-194), which logs `tool_call tool= args=` on completed tool paths only. Guard and router denials log nothing.
- **Policy.** Grants are `{issuer, subject, repository, tools[, required_scope]}`.
  - `test_dry_run` and `memex_read` are never grantable (authz.py:40).
  - With reload interval 0, every request reloads. Any policy change advances the epoch and invalidates every cursor and resource of every principal (deployment doc §8).
- **Same-path pair.** Two ids with the same path and distinct graph roots, A naming C as `before_repo_id`, is the only served expansion (`impact`). A grant on either id exposes the live tree (doc §7, §10).
- **Jobs.**
  - Fault env `ATLAS_ACI_JOB_FAULTS` is read only by `jobs submit|resume`. `kill` = SIGKILL self, then `os._exit(137)` (job_runner.py:131). `block` prints `ATLAS_JOB_SEAM <name>` and reads stdin.
  - `jobs status` lists `interrupted_jobs[{job_id}]` (jobs.py:791, :825).
  - `jobs gc --wait-seconds 0` gives an immediate `WRITER_BUSY` (`__main__.py`:2947-2953).
  - GC seams are hooks only (retention.py); there is no CLI fault path.
- **Pins and dependencies.**
  - `uv.lock`: mcp 1.30.0, httpx 0.28.1, pyjwt 2.15.1, cryptography 50.0.2, uvicorn 0.44.0. There is no pytest-xdist.
  - CI pytest runs on Python 3.14 from `mcp-server/` (ci.yml:105-117). Scripts are linted with `ruff check ../scripts/`, `ruff format --check ../scripts/` and `mypy ../scripts/` (:128-139). `atlas/` is not linted.
- **Production image** (Dockerfile:55-111): python:3.12-slim, user `atlas` (10001), `HOME=/tmp`, entrypoint `atlas-aci`, STOPSIGNAL SIGINT, ripgrep and git present. No pytest. Grammars are prefetched.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-06)
1. **No product source change.** `mcp-server/src/**`, `pyproject.toml`, `uv.lock`, both Dockerfiles and `.github/workflows` stay byte-identical against 4f470d7.
   - Everything runs on the S38-S41 surfaces: `serve-shared`, `serve --transport http`, stdio `serve` and `atlas-aci jobs`.
   - Retention fault injection is limited to lease-held GC, GC versus a running build (`WRITER_BUSY`) and post-retirement `EXPIRED_REFERENCE`.
   - An optional in-process raise at `gc_post_rename` is labelled `fault_kind: in_process_raise`.
   - Real kills at GC seams are documented as untested.
2. **Placement.**
   - `atlas/evals/shared/manifest.v1.json`, plus an "ACI-S42" section appended to `atlas/evals/README.md`.
   - `scripts/eval/s42_record.py` (impure) and `scripts/eval/s42_aggregate.py` (pure; it loads `v3_aggregate.py` by one importlib spec).
   - `mcp-server/tests/slices/test_s42_{support,contract,isolation}.py`.
   - `docs/evidence/s42/{README.md, raw/*.json.gz, readiness-report.json, readiness-report.md}`. The deployment-model and limits doc is `docs/evidence/s42/README.md`.
   - NO new `docs/operations/*.md` (test_s40_isolation.py:471-474, test_s41_isolation.py:412-415 and :457-459). Script basenames are unique.
3. **Evidence ledger (AC-001).** Black-box, against a real `serve-shared` subprocess. Planted canaries and per-case nonces; a frozen case table with an allow control for every deny class; an over-grant teeth case. The verbatim honesty limits are D9.
4. **Interruption matrix (AC-002).**
   - Cases: `cancel_marker`, `cancel_sigterm`, `kill_checkpoint`, `kill_mid_publish`, `kill_post_replace`, `kill_pre_receipt`, `gc_vs_lease`, `gc_vs_build`, plus the teeth `teeth_replace_by_copy`.
   - Each case runs in its own namespace on container-local tmp, behind the S41 lock canary. The validity rule is D6. Cases run with bounded parallelism (4).
5. **Compatibility (AC-003).** Cells exist only where a record exists. Real hosts are listed `untested` (`no_real_host_run`). The SDK x shared-http cell is conditional on Day-1 (c).
6. **Capacity (AC-004).** `max_inflight` 4, per principal 2, call caps 10000 (above the workload), leases as default. 4 principals x 2 repos, 6 clients, a fixed call count, a saturation phase. One workload record holds `limits` and `observed`.
7. **Verdict (AC-005).** Checks are `pass|fail|missing` and missing counts as fail. The verdict is `blocked` or `ready_for_operator_review`, never an unqualified "ready". `unresolved_limits` is never empty. Malformed input gives a typed error.
8. **Replay (AC-006).** `canonical_sha256` strips every `observed` and `diagnostic` key recursively. The committed raw replays byte-identically. A fresh run is checked by invariants only.
9. **Committed evidence.** Regenerated from a recorder run in the production image `atlas-aci:s42` (committed) and the dev image `atlas-aci-dev-relock` (kept under `.spectra/evidence/aci-s42/dev-run/`). The README records which run was committed. Size cap. `observed` holds image id, python and platform.
10. **Scope amendments.** One CHANGELOG bullet immediately BEFORE the S41 bullet (planning finding F8 shows every pin stays green). One README row after the S41 row (F9). `.spectra/evidence/aci-s42/**`. No existing test is edited.
11. **Invariance.** Unchanged: tools sha `94f9567a...`/1631472, the S38 goldens, the S23 manifest and anchors, `docs/evidence/v3/*`, `tests/fixtures/s38/*`, and every S39/S40/S41 pin. Docs are ASCII and free of the S36 and S39 BANNED words.
12. **Environment.**
    - `test_s42_*` runs in the normal CI pytest job (py3.14, ubuntu-latest) in ≤ ~120 s with ≤4 workers, per-request deadlines of 30 s and startup timeouts of 60 s.
    - G-PROD runs the recorder in `atlas-aci:s42` with `--entrypoint /usr/local/bin/python`, user `atlas`, `--network none`, `--rm`, a host watchdog and trap cleanup. The recorder is stdlib plus installed atlas deps only.

### Planning findings (binding with the decisions)
- **F1.** `jobs gc --wait-seconds 0` exists, so `gc_vs_build` gets `WRITER_BUSY` (exit 75) immediately; there is no 30 s wait.
- **F2.** Same-path pair. Repository `c` is registered as the before snapshot of `a` at the same path. Grants never give `c` without `a` (doc §7). Canary domain `C` exists only in `c`'s before graph (a symbol absent from the live tree). The live-tree exposure through `c` is an unresolved limit, not a ledger case.
- **F3.** The stopped-line `peak` is the shared gate peak, a server-side overlap measure. Per-principal 503s are counted by the client.
- **F4.** stderr `tool_call` corroboration exists only on completed tool paths. It is therefore a necessary, not sufficient, signal and is used only for nonce-carrying rows.
- **F5.** The G-PY314 container needs `ripgrep` and network access for the python grammar, otherwise the `search_text` allow controls fail.
- **F6.** In the production image the recorder is PID 1. Servers and jobs are its children, so a job `kill` gives returncode -9 (137 is accepted too).
- **F7.** There is no pytest-xdist; parallelism lives inside the recorder (threads driving subprocesses).
- **F8.** CHANGELOG Added has 25 bullets. Inserting at :420 (after the S14 block, before S41 :421) makes S42 `bullets[-3]`.
  - S40 `[-1]` (test_s40_isolation.py:431-440) and S41 `[-2]` (test_s41_isolation.py:424-435) hold, and `(ACI-S41)` stays unique.
  - `bullets[0..9]` pins (S33-S39) are unaffected.
- **F9.** The README row goes at :957, after the S41 row :956 and before INTEGRATION.md. S40 (SETUP+1) and S41 (shared-http+1) and their once-only path counts hold. The row must not contain `canary-missions.md` (test_s23_isolation.py:130).
- **F10.** `v3_aggregate` loads `span_metrics` lazily (:469-473), so loading it is pure. Its `EvalInputError` asserts its own CODES (:91-98), so S42 defines its own error type and gzip decoder and reuses only `canonical`/`digest`/`gzip_canonical`.
- **F11.** The S41 lock canary is a session autouse fixture in test_s41_support.py:497-511 and applies only where imported. `test_s42_contract` imports it; the recorder runs a stdlib copy and aborts with exit 3 when it fails (fail, never skip).

### In
1. `atlas/evals/shared/manifest.v1.json` (D1), plus an S42 section in `atlas/evals/README.md` (commands, formats, honesty).
2. `scripts/eval/s42_aggregate.py` (D3, D7-D10).
3. `scripts/eval/s42_record.py` (D2, D4-D6, D11).
4. `mcp-server/tests/slices/test_s42_{support,contract,isolation}.py`.
5. `docs/evidence/s42/` (D12), the CHANGELOG bullet and the README row.

### Out
- Any `mcp-server/src` change, a dependency, `ci.yml`, a deployment, credentials, DNS or real-host runs.
- `docs/operations/*` edits. GC-seam kills via src. An src audit hook.
- Re-running the S40 Observer suite as S42 evidence; it is cited as inherited.

## Approach

### Decisions

**D1 Manifest `atlas-shared-readiness-manifest.v1`.** Closed keys; canonical JSON; `manifest_sha256 = sha256(canonical(manifest))`. Frozen after Day-1 (g).
```
{format, slice:"ACI-S42", issuer:"https://idp.example.test", resource_base_url:"https://atlas.example.test/mcp",
 principals:["p1","p2","p3","p4"],
 trees:{A1,A2,B,T1,T2: {relpath: text}},                      # ASCII python sources
 canaries:{A:{name,content}, B:{name,content}, C:{name}, T1:{name}, T2:{name}},  # s42cnry_<d>_<12hex>
 repositories:[{id:"a",tree:"A2"},{id:"b",tree:"B"},{id:"c",tree:"A1",same_path_as:"a",before_of:"a"}],
 grants:{main:[G], revoke:[{subject,repository}], teeth_add:[G]},  G={subject,repository,tools}
 isolation:{listen:{max_inflight:16,max_inflight_per_principal:8}, limits:{max_calls_per_minute:0,
   max_calls_per_principal_per_minute:0}, concurrency:6, classes:[CLASS], cases:[CASE]},
 interruption:{langs:["python"], retention:{keep_last:1}, workers:4, cases:[ICASE]},
 compatibility:{cells:[CELL], hosts:[{id,status:"untested",reason:"no_real_host_run"}]},
 capacity:{listen:{max_inflight:4,max_inflight_per_principal:2,max_body_bytes:1048576},
   limits:{max_calls_per_minute:10000,max_calls_per_principal_per_minute:10000,max_leases_per_principal:1,
   max_graph_leases:4,max_total_lease_state_bytes:1073741824,max_records:1024},
   workload:{principals:4,repositories:2,clients:6,calls_per_client:12,client_principals:["p1","p2","p3","p4","p1","p2"],
   mix:[CALL x12]}, saturation:{principal:"p1",holds:2,other:"p2",poll_ms:100,max_polls:50},
   retry:{honor_retry_after:true,max_attempts:10}},
 deadlines:{request_s:30,startup_s:60,interrupt_case_s:90,recorder_total_s:150},
 deployment_model:{...}, required_checks:[13 names, D8], unresolved_limits:[D9], honesty:[H1..H4]}
CASE = {id, phase:"warm"|"main"|"hold"|"revocation"|"teeth", principal|null,
        token:"valid"|"missing"|"stranger"|"audience:<repo>", route,
        request:{kind:"tool"|"resource_read"|"cursor_continue", tool?, arguments?, uses?:<warm case id>},
        class, expected:"allow"|"deny", expected_outcomes:[OUTCOME], domains:[A|B|C], control:<id>|null,
        nonce_carrier:bool}
ICASE = {id, injection:{kind, seam?, at?}, expected_tree:"T1"|"T2", expect_valid:bool, facts:{...}}
CELL = {id, client:"raw-jsonrpc"|"mcp-sdk", transport:"stdio"|"http"|"shared-http",
        requested:<revision>|"sdk_default", required:bool, shared:bool}
```
- Tree `A2` (the live tree of a and c) carries the A tokens:
  - ≥30 functions `s42cnry_a_<hex>_<n>` that call each other, so every graph page contains `A.name`;
  - a comment carrying `A.content`.
- Tree `A1` additionally defines `s42cnry_c_<hex>` (= `C.name`), which A2 lacks.
- Tree `B` has a file named `s42cnry_b_<hex>.py` (`B.name`) carrying `B.content`.
- Trees `T1`/`T2` are 5 S41-style files plus `def <T1|T2 name>()`.
- Nonce per case = `"s42n" + sha256(case_id)[:16]` (deterministic, canonical).

**D2 Case table** (`grants.main`: p1 {a, b: all 5 shareable tools}; p2 {a: graph_query, search_symbol; c: graph_query}; p3 {b: all 5}; p4 {b: list_dir, view_file}).
- Warm (sequential, allow): `W1 p1.a.page` (graph_query `page` of a neighborhood of an A function, small `page_max_bytes`) yields the cursor and resource URI used below.
- Holds (allow; body withheld until the main gather finishes, then sent): `H1 p2.a.search_symbol`, `H2 p4.b.list_dir`.
- Main (concurrent, semaphore 6):

| id | principal.route.op | class | expected | control |
|---|---|---|---|---|
| M01 | p1.a.search_text (A.name\|nonce) | control | allow {A}, carrier | - |
| M02 | p1.b.search_text (B.content\|nonce) | control | allow {B}, carrier | - |
| M03 | p1.a.view_file (A file) | control | allow {A} | - |
| M04 | p3.b.search_text (B.content\|nonce) | control | allow {B}, carrier | - |
| M05 | p2.a.search_symbol (A.name) | control | allow {A} | - |
| M06 | p4.b.view_file (B file) | control | allow {B} | - |
| M07 | p2.a.impact | control | allow {A,C} | - |
| M08 | p1.a.continue(W1 cursor) | control | allow {A} | - |
| M09 | p1.a.resources/read(W1 uri) | control | allow {A} | - |
| M10 | p3.b.retrieve(repository=b path, text has nonce) | control | allow {B}, carrier | - |
| M11 | p4.b.list_dir(".") | control | allow {B} | - |
| D01 | p3.a.search_text (A.name\|nonce) | ungranted_repo | deny, carrier | M04 |
| D02 | p3.c.search_symbol (C.name) | ungranted_repo | deny | M05 |
| D03 | p2.a.view_file | ungranted_tool | deny | M05 |
| D04 | p4.b.search_text (B.content\|nonce) | ungranted_tool | deny, carrier | M06 |
| D05 | p1.a.test_dry_run | disabled_tool | deny | M03 |
| D06 | p1.b.memex_read | disabled_tool | deny | M02 |
| D07 | p1.a.impact | impact_before_ungranted | deny | M07 |
| D08 | p2.a.continue(W1 cursor) | cross_principal_cursor | deny | M08 |
| D09 | p2.a.resources/read(W1 uri) | cross_principal_resource | deny | M09 |
| D10 | p1.b.resources/read(W1 uri) | cross_repo_resource | deny | M09 |
| D11 | p3.b.retrieve(repository=a path, nonce) | cross_repo_argument | deny, carrier | M10 |
| D12 | p1.a.view_file("../b/<B file>") | path_escape | deny | M03 |
| D13 | p4.b.list_dir("..") | path_escape | deny | M11 |
| D14 | anon.a.search_text (no token) | token_missing | deny | M01 |
| D15 | p1.a.search_text (stranger-signed) | token_invalid | deny | M01 |
| D16 | p1.a.search_text (token for b) | token_wrong_audience | deny | M01 |

- Revocation phase (sequential, after main):
  - R1 `p1.a.page` (allow, own warm) and R2 `p1.a.continue(R1)` (allow).
  - Swap to `main - revoke[{p1,a}]`: R3 `p1.a.continue(R1)` deny `revoked_grant` (403); R4 `p1.b.search_text` allow (another grant unaffected).
  - Restore main: R5 `p1.a.continue(R1)` deny `revoked_grant` (never revived); R6 `p1.a.page` allow.
- Teeth phase (after revocation): swap to `main + teeth_add[{p3, a, [search_text, search_symbol]}]`, then re-run D01 and D02 with their frozen ids and expectations.
- `expected_outcomes` per deny case are frozen from the Day-1 (g) dry run. A deny case returning `ok`, or a foreign canary hit at Day-1, is a STOP.

**D3 Closed vocabularies.**
- `OUTCOMES`:
  - `ok`;
  - `tool_error:<C>`, `rpc_error:<C>` with C ∈ {FORBIDDEN, SCOPE_MISMATCH, INVALID_REFERENCE, EXPIRED_REFERENCE, OTHER};
  - `http:401:credentials_missing`, `http:401:credentials_invalid`, `http:403:repository_forbidden`, `http:404:route_not_found`, `http:503:inflight_limit`, `http:503:principal_inflight_limit`, `http:other`;
  - `transport_error`.
- `CLASSES`: control, ungranted_repo, ungranted_tool, disabled_tool, impact_before_ungranted, cross_principal_cursor, cross_principal_resource, cross_repo_resource, cross_repo_argument, path_escape, token_missing, token_invalid, token_wrong_audience, revoked_grant.
- `DOMAINS`: A, B, C.
- `PHASES`: warm, hold, main, revocation, teeth.
- `RECORD_KINDS`: environment, isolation, interrupt, compat, capacity.
- `STATUS`: complete, failed.
- `FAILURES`: server_start_timeout, request_deadline, case_deadline, subprocess_error, sdk_unavailable, stopped_line_missing.
- `SOURCES`: proc_status, proc_fd, wait4, stopped_line, unavailable.
- `RUSAGE_UNITS`: kib, bytes.
- `VERDICTS`: blocked, ready_for_operator_review.
- `CHECK_STATES`: pass, fail, missing.
- Aggregator error codes (`ReadinessInputError.code`): manifest_mismatch, unknown_shape, unknown_kind, unknown_unit, duplicate_record, not_canonical, missing_input, vocabulary.
- All tuples are unique, and the aggregator asserts that the manifest's `required_checks`, `classes` and `unresolved_limits` equal its constants.

**D4 Raw record `atlas-shared-raw.v1`.**
- Shape: `{format, kind, unit, manifest_sha256, status, failure|null, <body>, observed:{...}, diagnostic:{...}}`.
- One file `<kind>.<unit>.json.gz` (canonical bytes, gzip level 9, mtime 0). Records hold row-level facts only; every count is derived by the aggregator.
- Input digest = `sha256(canonical(strip(record)))`, where `strip` removes every key named `observed` or `diagnostic` at any depth.
- **Never canonical:** ports, pids, tokens, kids, JWKS bytes, absolute paths, gids (they are path-dependent), timings, 503 counts, interleavings.
- `environment.main`:
  - body: `{mcp_version, atlas_aci_version, lock_canary:{second_fd_blocks, process_blocks, hardlink_same_inode, shared_folder_fs}}`;
  - observed: `{python, platform, runner:"container_image"|"host_process", image|null, fstype, cpu_count}`.
- `isolation.{main,revocation,teeth}`:
  - body: `{policy:"main"|"revoked"|"teeth", policy_sha256, rows:[ROW], holds_spanned:bool, server:{stopped_line_parsed, peak_ge_3}}`;
  - row: `ROW={case_id, phase, principal, route, request_kind, tool|null, class, expected, outcome, http_status, canary_hits:[domain], server_seen:bool|null, observed:{t_start_ms, t_end_ms}}`;
  - record observed: `{server:{admitted, rejected, peak}, max_client_overlap}`.
  - `canary_hits` = domains with a token present in the response bytes AND absent from the row's request bytes.
  - `server_seen` = the row nonce appears in a stderr `tool_call` line (carriers only, else null).
- `interrupt.<case>`:
  - body: `{injection, expected_tree, facts:{...}, validation:{status_current_matches, no_invalid_jobs, server_gid_matches|null, server_expected_canary, server_other_canary_absent, acquire_ok, control_gid_matches}, valid, live_unchanged|null}`;
  - observed: `{gids:{base, current, control}, exit_codes, durations_s}`.
- `compat.<cell>`:
  - body: `{client, transport, requested, sent_revision, expected:{outcome, offered}, negotiated, server_info:{name, version}, mcp_version, tools:{count, sha256}, call:{tool, outcome, canary_hit}, shared}`;
  - observed: `{latency_ms}`.
- `capacity.main`:
  - body: `{limits:{listen, limits}, workload, invariants:{all_calls_completed, client_principal_concurrency_within_cap, server_peak_within_limit, no_untyped_5xx, typed_503_has_retry_after, unauthorized_zero, limits_match_config_document}, saturation:{third_rejected_typed, other_principal_admitted, recovered_after_disconnect}}`;
  - observed: `{latency_ms:{n, p50, p95, max}, wall_s, attempts, rejections:{inflight_limit, principal_inflight_limit}, server:{admitted, rejected, peak, source}, memory:{vm_rss_kib, vm_hwm_kib, samples, source}, fds:{max, source}, rusage:{ru_maxrss, unit, source}, saturation_polls}`.

**D5 Ledger rule (AC-001).** Per row:
- `unauthorized = (expected=="deny" and outcome=="ok") or bool(set(canary_hits) - authorized(row)) or (expected=="deny" and nonce_carrier and server_seen)`.
- `authorized(row)` = the case's `domains` for allow rows and ∅ for deny rows.
- The aggregator recomputes this from rows, and the test recomputes it with an independent implementation.
- "Zero" means zero within the enumerated cases.

**D6 Interruption protocol (AC-002).** Per case, under `work/interrupt/<case>/`:
1. Write T1. `jobs submit` on `graphs` gives the base gid. Twin `twin1` submit gives the control for T1.
2. Write T2. Twin `twin2` submit gives the control for T2.
3. Inject:
   - `cancel_marker`: block at `staged`, then `jobs cancel`, then release. Facts: exit 4, state cancelled, cancel honoured.
   - `cancel_sigterm`: block at `pre_replace`, then SIGTERM. Fact: exit 4.
   - `kill_checkpoint`: kill at `checkpoint`, at=2. Facts: killed, then `jobs resume` with `resumed_from 2`, outcome built.
   - `kill_mid_publish`: kill at `mid_publish`, then resume. Fact: outcome built.
   - `kill_post_replace` / `kill_pre_receipt`: kill, then resume. Fact: outcome recovered.
   - `gc_vs_lease`: a normal T2 job, `retention.json` keep_last 1, `jobs acquire --generation <T1 gid> --hold`, then `jobs gc --now 2000000000`. Facts: (T1 gid, leased) retained; holder SIGKILL; gc retires it; `jobs acquire` gives exit 3 `EXPIRED_REFERENCE`.
   - `gc_vs_build`: block at `staged`, then `jobs gc --wait-seconds 0`. Facts: exit 75 `WRITER_BUSY`; release, job completed.
   - `teeth_replace_by_copy`: a normal T2 job; then copy `generations/<current>/graph.9.db`, flip byte at len//2, `os.replace` onto the farm path. The live `graph.9.db` inode and sha are unchanged (`live_unchanged`).
   - The job id comes from `jobs status.interrupted_jobs`.
4. Validate. `valid` = all of:
   - `jobs status`: current == expected gid, `running == interrupted == failed == 0`;
   - a stdio `serve --repo R --graph-root graphs --memex-root <case>/memex` (raw line client at 2025-11-25): structured `graph_query op=status` `generation_id` == expected (null if Day-1 (f) finds no field);
   - `search_symbol` finds the expected tree canary and not the other tree's;
   - `jobs acquire --generation <current>` exits 0;
   - expected gid == control gid.
- Expected tree is T1 for cancel cases and T2 otherwise. The teeth case must record `valid:false` with `acquire_ok:false`.
- 4 workers. Each case has a 90 s deadline; on expiry it is recorded `status failed` with failure `case_deadline`.

**D7 Report `atlas-shared-readiness-report.v1`.**
```
{format, report:{manifest_sha256, inputs:[{name,sha256}], verdict, checks:{<name>:"pass"|"fail"|"missing"}, failed_checks:[sorted],
  deployment_model, honesty:[H1..H4],
  isolation:{principals, routes, rows_total, allow_rows, deny_rows, unauthorized:{main, revocation, teeth},
    unauthorized_rows:[case_id], by_class:[{class, deny_cases, controls_ok, unauthorized}],
    overlap:{holds_spanned, server_peak_ge_3, observed:{max_client_overlap, server_peak}}},
  interruption:{cases:[{id, injection, expected_tree, expect_valid, valid, validation}]},
  compatibility:{cells:[...one per compat record], untested_cells:[{id, reason}], hosts:[...]},
  capacity:{workload:{limits, workload, invariants, saturation, observed:{...D4 capacity observed...}}},
  environment:{mcp_version, atlas_aci_version, observed:{python, platform, runner, image}},
  unresolved_limits:[...]},
 canonical_sha256: sha256(canonical(strip(report))), diagnostic:{}}
```
- `readiness-report.json` = `json.dumps(sort_keys, indent=1, ensure_ascii=True)` + LF.
- `readiness-report.md` (ASCII, LF, deterministic) sections, in order:
  1. title;
  2. `Verdict: blocked` or `Verdict: ready_for_operator_review (evidence for an operator decision; not a deployment approval)`;
  3. `Failed checks:` (names, or `none`);
  4. checks table;
  5. deployment model;
  6. honesty H1-H4 verbatim;
  7. isolation by class;
  8. interruption table;
  9. compatibility matrix plus untested cells plus hosts;
  10. capacity workload record (limits table, invariants, then an "observed (excluded from canonical_sha256)" table with source labels);
  11. unresolved limits;
  12. inputs;
  13. canonical_sha256.

**D8 Checks and verdict.** Each check reads named units. An absent unit gives `missing`; a unit with status failed gives `fail`.

| check | pass iff |
|---|---|
| records.complete | every manifest unit (environment, 3 isolation, 9 interrupt, required compat cells, capacity) has exactly one `complete` record |
| environment.sdk_pinned | `mcp_version == "1.30.0"` and all lock_canary booleans hold |
| isolation.unauthorized_zero | main + revocation unauthorized == 0 |
| isolation.positive_controls | every non-control class has ≥1 deny case; every deny case's control row is allow, `ok`, hits ⊇ its domains (non-empty); every allow carrier has `server_seen` true; M07 hits C |
| isolation.typed_denials | every deny row outcome ∈ its `expected_outcomes`; no `http:other`/`transport_error` row |
| isolation.concurrency | ≥4 principals and ≥2 routes in main; `holds_spanned`; `server.peak_ge_3` |
| isolation.ledger_teeth | teeth unauthorized ≥ 1 with case ids and expectations equal to main |
| interrupt.all_recovered | every non-teeth case `valid` and facts == manifest facts |
| interrupt.teeth_detected | teeth `valid:false`, `acquire_ok:false`, `live_unchanged:true` |
| retention.lease_respected | gc_vs_lease and gc_vs_build facts == manifest |
| compat.cells_recorded | every required cell recorded; each record: negotiated == expected.offered, mcp 1.30.0, tools.count 7, call ok with canary hit |
| capacity.limits_held | limits == manifest capacity limits and every invariant true |
| capacity.saturation_typed | the three saturation booleans true |

- The verdict is `blocked` if any check is not `pass`, else `ready_for_operator_review`.
- A changed capacity limit is a failed check, never a raise. Malformed or foreign input raises `ReadinessInputError` (D3 codes): no report, CLI exit 2.

**D9 Unresolved limits** (always rendered; equal to the manifest list):
- `black_box_ledger_detects_planted_canaries_and_typed_outcomes_only`
- `zero_is_within_the_enumerated_cases_not_a_no_leakage_claim`
- `stderr_tool_call_corroboration_is_necessary_not_sufficient`
- `timing_side_channels_untested`
- `shared_address_space_untested`
- `tls_reverse_proxy_untested`
- `same_path_pair_exposes_the_live_tree_by_design`
- `real_hosts_untested_claude_code_copilot_cursor`
- `s40_in_process_observer_suite_inherited_not_rerun`
- `gc_seam_kills_untested_only_lease_writer_busy_and_expiry`
- `single_host_loopback_workload_not_a_load_test`
- `resource_metrics_linux_proc_with_source_labels`
- `no_deployment_credentials_or_dns_performed`
- `fresh_runs_compared_by_invariants_not_by_digest`

Honesty statements (verbatim in the md, README and manifest):
- H1 "Black-box evidence: the ledger detects planted canaries and typed outcomes in responses and server log lines, not internal reads."
- H2 "Unauthorized evidence access is zero within the enumerated cases; this is not a claim that no leakage exists."
- H3 "Not tested: timing side channels, the shared address space of the single server process, and a TLS-terminating reverse proxy."
- H4 "Inherited and not re-run: the S40 in-process Observer suite (mcp-server/tests/slices/test_s40_contract.py)."

**D10 Aggregator `s42_aggregate.py`** (pure).
- Imports ⊆ {`__future__`, argparse, gzip, hashlib, importlib, json, re, sys, decimal, pathlib, typing, collections, dataclasses, math}.
- It must not use time, os, subprocess, platform, random, socket, urllib, threading, asyncio, signal, resource, shutil, tempfile, uuid, environ, getenv or atlas_aci.
- Exactly one `spec_from_file_location`, naming `v3_aggregate.py`; it reuses `canonical`/`digest`/`gzip_canonical`. It exports `strip`, `aggregate(manifest, records)`, `render_md`, `read_raw_dir`, `run`, `main`.
- CLI: `python scripts/eval/s42_aggregate.py --manifest M --raw DIR --out DIR [--verify]`.
  - stdout: `<canonical_sha256> <verdict>`.
  - Exit 0 when written, or when `--verify` finds regenerated bytes equal to the files in `--out` (nothing written).
  - Exit 1 on a `--verify` mismatch.
  - Exit 2 on `ReadinessInputError`, with `s42_aggregate: <code>: <msg>` on stderr.
- Replay: `cd mcp-server && uv run --frozen python ../scripts/eval/s42_aggregate.py --manifest ../atlas/evals/shared/manifest.v1.json --raw ../docs/evidence/s42/raw --out ../docs/evidence/s42 --verify`.

**D11 Recorder `s42_record.py`** (impure; top-level stdlib only; atlas_aci, mcp, httpx, jwt and cryptography imported lazily; py3.11+ syntax).
- CLI: `python [-I] scripts/eval/s42_record.py --manifest M --out DIR --work DIR [--image ID] [--phases environment,isolation,compat,capacity,interrupt] [--workers N<=4]`.
- `--out` must be absent or empty (records are immutable, never overwritten); `--work` must be empty.
- Exit codes:
  - 0: every unit is `complete`;
  - 1: ≥1 unit recorded `failed` (the others are still written);
  - 2: usage or manifest invalid (nothing written);
  - 3: abort (lock canary failed, `--out`/`--work` not empty).
- Siblings load by importlib spec (`s42_aggregate` for `gzip_canonical`/`strip`).
- **Processes:** every server is `[sys.executable, "-m", "atlas_aci", ...]` (no shell). stderr is pumped uncapped to `work/<unit>.stderr`. The port is parsed from the listening line. Shutdown is SIGTERM then `os.wait4(pid)`, with a 10 s deadline then SIGKILL.
- **Keys:** RSA-2048 plus a stranger key generated per run with `cryptography`. Tokens are RS256 via `jwt`, `aud = <base>/<repo>`, ttl 3600. The config dir is 0700 and files are 0600, swapped atomically.
- **Clients:**
  - a minimal HTTP/1.1 asyncio-stream client (S39 RawHttp shape: JSON and SSE, withheld or half body, `Connection: close`, per-request deadline);
  - a stdio line client;
  - the SDK `ClientSession` via `stdio_client` and `streamable_http_client`; for shared-http, `http_client=httpx.AsyncClient(headers={"Authorization": ...})` if Day-1 (c) passes.
- **Phase order:**
  - environment (lock canary on `--work`);
  - isolation:
    1. index a@A1 into `gc` (the c graph root);
    2. rewrite to A2 and index into `ga`;
    3. index b into `gb`;
    4. start one `serve-shared` with 3 repos;
    5. warm, holds, main gather, release holds, the compat shared-http cells, revocation, teeth;
    6. stop and parse the stopped line;
  - compat (stdio raw, stdio SDK, `serve --transport http` raw and SDK);
  - capacity (its own `serve-shared`): workload, saturation, `/proc/<pid>/status` VmRSS/VmHWM and `/proc/<pid>/fd` sampled every 200 ms, stop, `wait4` `ru_maxrss` (unit `kib` on Linux, `bytes` on darwin);
  - interrupt (ThreadPoolExecutor, `--workers`).
- **Compat cells** (10):
  - raw x shared-http x {2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25, unlisted "2099-01-01"};
  - raw x http x 2025-11-25 and raw x stdio x 2025-11-25 (`shared:false` baselines);
  - SDK x stdio, SDK x http, SDK x shared-http (`sdk_default` = `mcp.types.LATEST_PROTOCOL_VERSION`; the last is `required:false` with reason `sdk_bearer_path_unavailable` if Day-1 (c) fails).
  - Hosts: claude-code, github-copilot, cursor.
  - `expected` = `negotiate(requested)` (outcome, offered), computed by the recorder.
- **Capacity workload:** clients map to principals p1, p2, p3, p4, p1, p2; each does 12 calls over a and b (search_text, search_symbol, view_file, list_dir, graph_query `status`). A typed 503 is retried after Retry-After, up to 10 attempts.
- **Saturation:** p1 opens 2 half-body requests, then polls a third p1 call every 100 ms (max 50) until 503 `principal_inflight_limit` with `retry-after: 1`. p2 then gets 200. The holders are closed and p1 is polled until 200.

**D12 Docs.**
- `docs/evidence/s42/README.md` (ASCII, no BANNED words):
  - the tested deployment model: one loopback `serve-shared` process, port 0, repos a/b plus c same-path before, 4 principals, RS256 generated keys, offline JWKS, reload 0, no TLS proxy, container-local tmp;
  - which run is committed (image id);
  - the regenerate, replay and production-scenario commands;
  - H1-H4 verbatim, D9 rendered, and the record formats.
- `atlas/evals/README.md` gets an appended "ACI-S42 shared-operation readiness" section.
- **CHANGELOG** (:420), one bullet: `- **Shared-operation readiness evidence (ACI-S42).** ...`. It names `scripts/eval/s42_record.py`, `scripts/eval/s42_aggregate.py` and `docs/evidence/s42/`, ends "not a deployment; no product code changes", has no `(ACI-S4x)` token other than its own, no `- ` continuation, and is ASCII.
- **README** (:957): `| [\`docs/evidence/s42/README.md\`](docs/evidence/s42/README.md) | Shared-operation readiness evidence (ACI-S42): tested deployment model, evidence access ledger, interruption matrix, tested client and transport combinations, one bounded workload record and unresolved limits; not a deployment |`.

### Acceptance-test mapping (`test_s42_contract.py`; real processes, no mock of the behaviour under test; every `all()` follows a non-empty check)
- **Fixture `fresh`** (module, sync): imports `lock_canary` from test_s41_support. It runs `[sys.executable, s42_record.py, --manifest, --out tmp/raw, --work tmp/work]` with cwd `mcp-server` and timeout 170 s, and asserts exit 0. On failure the assertion carries the stderr tail.
- **Fixture `committed`**: the records from `docs/evidence/s42/raw`.

| AC | Test | Observable, with positive control or teeth |
|---|---|---|
| 001 | test_negative_isolation_suite | The fresh isolation main and revocation records: the aggregator's unauthorized count == the test's independent D5 count == 0, over ≥30 rows, 4 principals, 2 routes. Every class has deny cases. Every control is `ok` with its domains hit (M07 hits C: before expansion is real). Allow carriers have `server_seen` true and deny carriers false. Deny outcomes are typed. `holds_spanned` and `peak_ge_3` hold, and H1/H2 intervals contain every main row (observed). Revocation: R2 ok, R3 403, R4 ok, R5 refused, R6 ok. **Teeth:** the teeth record's unauthorized ≥ 1, and aggregating with it renamed to `isolation.main` gives `blocked` with `isolation.unauthorized_zero` in json `failed_checks` and md `Failed checks:`. H1-H4 are in the md. |
| 002 | test_failure_recovery_suite | Fresh: all 8 non-teeth `interrupt.*` records `valid` with all 5 conditions true (server_gid_matches true unless Day-1 (f) recorded null). Case facts as D6 (exit 4, killed -9/137, `resumed_from 2`, outcome recovered, exit 75 `WRITER_BUSY`, `(gid, leased)` then retired then exit 3 `EXPIRED_REFERENCE`). **Teeth:** `teeth_replace_by_copy` has `valid:false`, `acquire_ok:false`, `live_unchanged:true` and the live server condition true. Checks `interrupt.all_recovered`, `interrupt.teeth_detected` and `retention.lease_respected` pass. Environment lock canary booleans are true. |
| 003 | test_compatibility_matrix | Report cell ids == the compat record units exactly. Each cell: negotiated == `negotiate(requested).offered`, recomputed in-test; `mcp_version == VERIFIED_SDK[1] == importlib.metadata.version("mcp")`; tools count 7; call ok with a canary hit. The unlisted cell gives counter_offer → `2025-11-25`. Hosts are untested with `no_real_host_run` and are never cells. **Negative control:** drop `compat.raw.http.2025-11-25` → the cell disappears, `records.complete` fails, verdict `blocked`. |
| 004 | test_capacity_record | Exactly one `report.capacity.workload` holds `limits` (== manifest capacity) and `observed` (latency n == 72 calls and p50 ≤ p95 ≤ max; memory, fds, rusage and server each with a `SOURCES` label, `proc_*`/`wait4`/`stopped_line` on Linux; peak ≥ 1). Every invariant and saturation boolean is true. **Teeth:** the same record with `invariants.server_peak_within_limit=false` → `capacity.limits_held` fails. |
| 005 | test_failed_gate_visible | **Control:** committed records → `ready_for_operator_review`, `failed_checks == []`. Cases (loop): drop `interrupt.kill_mid_publish` (→ `records.complete` fail, `interrupt.all_recovered` missing); flip D01 outcome to `ok` (→ `isolation.unauthorized_zero`); set a compat `negotiated` wrong (→ `compat.cells_recorded`); set saturation false (→ `capacity.saturation_typed`). Each is `blocked`, with the check named in json `failed_checks` and md `Failed checks:` and the md verdict line `Verdict: blocked`. Typed errors: foreign manifest sha → `manifest_mismatch`; unknown kind → `unknown_kind`; duplicate unit → `duplicate_record`; non-canonical gzip → `not_canonical`; unknown outcome → `vocabulary`. The CLI exits 2 and writes no report. |
| 006 | test_shared_report_replay | Committed raw → `aggregate` twice: equal; json and md bytes == the committed files. CLI into two tmp dirs: byte-identical to each other and to the committed files. `--verify` exits 0, and exits 1 against an edited copy. Permuted record order → same `canonical_sha256`. Every `observed` and `diagnostic` value mutated → same sha. A changed capacity limit → different sha and `blocked`. Fresh run: verdict `ready_for_operator_review` by invariants (no equality with the committed digest). Determinism scan of `strip(report)`: no work path, port, pid, `eyJ` (JWT), `kid-`, PEM marker or 64-hex observed gid. |
| RUN-01 | gate | The SHA-256 manifest of declared tracked files is equal before and after a clean-worktree re-entry; `git diff --exit-code <output_commit>` on those paths. |
| RUN-02 | gate | `slice_id` + `criteria_sha256` are equal in the interrupted and resumed receipts; the one receipt is `.spectra/state/aci-s42.execution.json`. |

### Supporting tests (`test_s42_isolation.py`; `test_s42_support.py` holds loaders and helpers and no tests)
- **Aggregator purity:** AST import, name and attribute check per D10; exactly one `spec_from_file_location`, and the text names `v3_aggregate.py`. The recorder's top-level imports are stdlib only.
- **Invariance:**
  - tools sha/bytes via the CLI;
  - pyproject `981f0784...`, uv.lock `87ba265e...`, Dockerfile, Dockerfile.dev, and a digest of the sorted (relpath, sha256) of `mcp-server/src/**/*.py`, all equal to Day-1 (a) constants;
  - `READ_ONLY_TOOLS` == 7.
- **Docs:**
  - `docs/evidence/s42/README.md` and `readiness-report.md` contain H1-H4 and "zero within the enumerated cases", every D9 limit, and "untested" for each host;
  - they never contain the word `ready` outside `ready_for_operator_review`;
  - ASCII; no S36 ∪ S39 BANNED word in the README, md, CHANGELOG bullet, README row or the atlas/evals section.
- **Evidence:**
  - the raw dir holds only `.json.gz`, total < 400000 bytes, each < 150000;
  - every file decodes canonical with the committed manifest sha;
  - no `.so`, PEM, JWK `"d"` member or JWT in `docs/evidence/s42` or `atlas/evals/shared`;
  - the committed json verdict == the md verdict line.
- **Sets:** `docs/operations/*.md` == {shared-http-deployment.md, index-jobs-and-retention.md}; `test_s42_*.py` == {support, contract, isolation}.
- **CHANGELOG/README:**
  - `bullets[-3]` starts with the S42 title, `(ACI-S42)` appears once, `[-2]` is S41 and `[-1]` is S40;
  - the README row directly follows the S41 row and contains no `docs/operations/` path.
- **Vocabulary:** closed tuples are unique, and the manifest lists equal the aggregator constants.

### Gates (dev image `atlas-aci-dev-relock`, `uv run --frozen`, logs and exit files in `.spectra/evidence/aci-s42/`)
- **G-S42:** `pytest tests/slices/test_s42_contract.py -q`, then `pytest tests/slices/test_s42_*.py -q --durations=0`. The total must be ≤ 120 s under `--cpus 4`.
- **G-REG:** `pytest tests/slices/test_s23*.py tests/slices/test_s36_isolation.py tests/slices/test_s38*.py tests/slices/test_s39*.py tests/slices/test_s40*.py tests/slices/test_s41*.py tests/test_thesis_negatives.py -q`.
- **G-LINT, exactly as CI:** `ruff check src/ tests/`; `ruff format --check src/ tests/`; `mypy src/`; `ruff check ../scripts/ --exclude fingerprint-fixture`; `ruff format --check ../scripts/ --exclude fingerprint-fixture`; `mypy ../scripts/`.
- **G-FULL:** `pytest -q`; no new failure or skip vs `base-invariants.json`.
- **G-SRC:**
  - `git diff --exit-code 4f470d7 -- mcp-server/src mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows hosts docs/operations docs/evidence/v3 mcp-server/tests/fixtures`;
  - `git diff --name-status 4f470d7 -- mcp-server/tests` == A `test_s42_{support,contract,isolation}.py`;
  - `scripts` == A `eval/s42_{record,aggregate}.py`;
  - `atlas` == A `evals/shared/manifest.v1.json`, M `evals/README.md` (0 deletions);
  - root == M `CHANGELOG.md`, M `README.md` (0 deletions).
- **G-MCP:** `atlas-aci tools` sha `94f9567a...` / 1631472 bytes.
- **G-PY314:** `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'apt-get update -qq && apt-get install -y -qq ripgrep && cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s42_*.py -q'`. Container-local tmp; no skips.
- **G-REPLAY:** aggregate the committed raw into two tmp dirs; both json and md are `cmp`-identical to each other and to the committed files; `--verify` exits 0.
- **G-PROD** (`.spectra/evidence/aci-s42/production-scenario.sh IMG`, POSIX sh, `set -eu`):
  - **Containers.** Each is `--rm`, `--network none`, named `s42-$$-N`, user `atlas`, with an explicit `--entrypoint`. Named volume `s42-$$-out`, chowned by one root init container. `trap` removes containers and volumes on EXIT/INT/TERM. A host watchdog (`sleep 300; docker kill`) gives FAIL 124.
  - **P0:** image id; `python -I -c` inside the image prints the mcp, pyjwt, cryptography and atlas-aci versions; the sha256 of the installed `atlas_aci/**/*.py` equals the host `mcp-server/src`.
  - **P1:** `docker run ... --entrypoint /usr/local/bin/python -v <repo>:/src:ro -v s42-$$-out:/out IMG -I /src/scripts/eval/s42_record.py --manifest /src/atlas/evals/shared/manifest.v1.json --out /out/raw --work /tmp/s42 --image <id>` must exit 0.
  - **P2:** a copy-out container (`--entrypoint /bin/cp`); host aggregation gives `ready_for_operator_review`. Non-vacuous: isolation rows ≥ 30; compat cells ≥ 9; 9 interrupt records; capacity n == 72; the teeth checks pass.
  - **P3:** aggregating with `capacity.main.json.gz` removed gives `blocked`.
  - **Output:** `production-scenario.json`; exit 0 only if all checks pass. The committed `docs/evidence/s42` is regenerated from P1's records.
- **G-HARDEN:** `bash scripts/harden-gate-classify.sh 4f470d7 HEAD` prints `augmented=false`.
- **G-DRIFT:** `ramza-drift` with the declared scope.

### Work plan (timebox 3 engineering days)
**Day 1 AM: qualification**, before any slice file. Outputs `qualification.json` and `base-invariants.json`; spikes live in scratch.
- **(a)** `git diff --quiet 4f470d7 -- mcp-server/src`. Record the pyproject, uv.lock, Dockerfile(s) and src tree digests and the tools sha. Run the base full suite in the background. Run the S41 lock canary in the dev image tmp.
- **(b)** A `serve-shared` subprocess on container-local tmp with generated keys and a/b/c (same-path pair passes `validate_deployment`):
  - the listening port is parsed;
  - a nonce-bearing `search_text` gives a `tool_call` stderr line containing the nonce;
  - SIGTERM gives exit 0 and a parsed `shared_http_stopped admitted= rejected= peak=` line;
  - `os.wait4` returns rusage.
- **(c)** `inspect.signature(streamable_http_client)` has `http_client`, and an SDK session with a bearer httpx client initializes, lists 7 tools and calls `search_symbol` on `/mcp/a`.
- **(d)** S41 faults from a recorder-style subprocess:
  - kill at checkpoint at=2 → rc -9, `interrupted_jobs` id, resume `resumed_from 2`;
  - block at `staged` → seam line; `jobs gc --wait-seconds 0` → exit 75;
  - `jobs acquire --hold` lease line; acquire of a corrupted farm copy → non-zero.
- **(e)** `/proc/<pid>/status` and `/proc/<pid>/fd` readable for a child in the dev image, and in the production image as `atlas`.
- **(f)** Over stdio at 2025-11-25, the structured `graph_query op=status` carries `generation_id` == `jobs status` current.
- **(g)** A dry run of the full case table: record every outcome and freeze `expected_outcomes`. Also confirm: W1 gives `next_cursor` and a resource URI; the M08 page and the M09 blob carry `A.name`; M07 carries `C.name`.
- **(h)** Recorder wall time in the dev image with `--cpus 4`.
- **(i)** In the production image: `python -I s42_record.py --help` as `atlas` with `--network none`, and the python grammar available offline.

**STOP RULE** (return to the orchestrator; never adapt silently):
- (a) src differs at base, or base red.
- (b) the server cannot start or stop in a subprocess, or the stopped line is absent.
- (d) the fault env or seams do not work from a subprocess.
- (g) ANY deny case returns `ok` or a foreign canary hit. This is a P1 product finding: report it as BLOCKED with the evidence; never edit expectations to pass.
- (h) > 150 s after cuts 1-4.

**Recorded adaptations, not stops:**
- (c) fails → SDK x shared-http becomes `required:false`, reason `sdk_bearer_path_unavailable`, listed untested.
- (e) unavailable → `unavailable` source labels.
- (f) no field → `server_gid_matches:null`; validity rests on the other 4 conditions.
- (h) 120-150 s → apply cuts 1-4.

**Day 1 PM:** manifest v1 (frozen after (g)), `s42_aggregate.py`, `test_s42_support.py`, the purity and vocabulary tests, and synthetic-record unit checks of D5/D8.
**Day 2:** `s42_record.py` isolation, revocation, teeth, compat and capacity; AC-001/003/004. **Checkpoint with a clock reading:** if a never-cut item is red, return to the orchestrator.
**Day 3 AM:** the interruption matrix and AC-002; AC-005/006; regenerate evidence (dev image, then G-PROD in the production image); docs, CHANGELOG, README.
**Day 3 PM:** G-REG, G-LINT, G-FULL, G-SRC, G-MCP, G-PY314, G-REPLAY, G-PROD, G-HARDEN, G-DRIFT; RUN-01/02; the receipt.

**Cut order** (each cut becomes a listed limitation; the manifest is cut before freezing):
1. The README row, then the CHANGELOG bullet.
2. The optional `gc_post_rename` in_process_raise case (not planned unless time remains).
3. The compat `raw.stdio` and `raw.http` baselines.
4. The unlisted-revision cell.
5. `/proc` fd sampling (VmRSS/VmHWM and `ru_maxrss` stay).
6. `kill_mid_publish` and `kill_pre_receipt` (one pre-commit and one post-commit kill stay).
7. G-PROD P3.

**Never cut:** all 6 ACs; the isolation over-grant and interruption replace-by-copy teeth; every positive control; H1-H4 and D9; replay determinism and the strip rule; the lock canary; G-SRC and G-MCP.

### Independent checker conditions
Verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s42-plan-checker`, independent read-only review; 0 BLOCKER, 7 MAJOR, 9 MINOR). Every finding is accepted as a binding plan condition with the same number; where a condition conflicts with plan text above, the condition wins. The orchestrator decision stands: zero changes under `mcp-server/src`, `pyproject.toml`, `uv.lock`, the Dockerfile and the workflows.

Orchestrator resolutions (stricter option chosen each time):
- Condition 4: `max_leases_per_principal` 3 and `max_graph_leases` 8 in the isolation phase, with separate warm leases W1 (cursor) and W2 (resource); recorded in the manifest.
- Condition 7: Day-1 (h) measures the full recorder under `docker run --cpus 4`; deadlines are at least 2x the measurement (fixture >= 300 s, per case >= 120 s); the independent isolation/compat/capacity phases overlap with the interrupt pool without oversubscribing 4 vCPU; any cut is applied and recorded before the manifest is frozen. The 120 s figure is a dev-image budget gate, not a test assertion.
- Condition 10: include the deterministic gate-503 phase (p1 and p2 hold two each, p3 gets `http:503:inflight_limit`, stopped-line `rejected >= 1`); label `peak <= max_inflight` as a tautological invariant; declare per-principal capacity grants so every workload call is allowed.
- Condition 9: AC-003 and AC-005 negative controls use never-cut units (`compat.raw.shared-http.2025-11-25`, `interrupt.kill_checkpoint`).
- Never-cut (unchanged from the plan, plus): conditions 1-6, the teeth and positive-control cases, the honesty statements, replay determinism, zero src change. Stop and return to the orchestrator at the Day-2 checkpoint if any never-cut item is red.

**Findings**

1. **MAJOR. Canary hits can be self-cancelling.**
   - D4 defines `canary_hits` as tokens in the response and not in the request. `search_text` returns `{path,line,col,preview}`, where `preview` is the matched line only (`tools/search_text.py:105-126`).
   - M01, M02, M04, D01 and D04 put `A.name` or `B.content` in the pattern, so the match line's token is excluded. The control then hits only if the witness sits on the same line or path.
   - The M09/D09/D10 resource `blob` is base64 (`protocol/resources.py:109-115`). A raw-byte scan never sees a canary there.
   - **Condition:** each domain gets disjoint token classes: a pattern token and a witness token. The witness is on the same source line and in the file path. No request ever contains a witness. The scan runs on raw bytes plus base64-decoded `blob` values. Add synthetic-row unit tests for both clauses of D5: an allow row hitting an unauthorized domain, and a deny row with an error outcome that still carries hits. The D01 teeth case alone fires only through `expected deny and outcome ok`, not the hit clause.

2. **MAJOR. The control-to-deny pairing does not satisfy "control hits ⊇ deny domains".**
   - D01 (domain A) is controlled by M04, which hits B.
   - D02 (C) is controlled by M05, which hits A.
   - D11 and D12 target A and B respectively but are controlled by M10 and M03.
   - `domains` is not defined for deny rows.
   - **Condition:** define `target_domain` for every deny case. Each deny case needs a control that (a) uses the same tool class and (b) hits the same target witness through an allowed principal. Concretely: D01 uses M01; D02 uses M07; D11 and D12 get new controls (p1.a.retrieve; p1.b.view_file of the B file).
   - The aggregator check is then `control.hits ⊇ {target_domain}`.

3. **MAJOR. The nonce collides between main and teeth.**
   - The nonce is `sha256(case_id)[:16]`, and the teeth phase re-runs D01 with the same id on the same server.
   - If `server_seen` is a grep of the cumulative stderr file, the teeth hit makes the main D01 row `server_seen: true`. That makes main unauthorized, a deterministic false fail.
   - **Condition:** the nonce is derived from `(phase, case_id)`.
   - Spawn servers with `stderr=<file object>` rather than a pump thread. `Enforcement.record` logs synchronously with `flush=True`, so file reads are race-free. Compute `server_seen` after the server stops, or by a recorded byte offset.

4. **MAJOR. Lease quota breaks R1 and the M08/M09 race.**
   - Isolation `limits` leave `max_leases_per_principal` at 1 (`shared_http.py:415-417`).
   - `ScopedLeaseRegistry.pending()` raises `LEASE_CAPACITY` when `held >= quota` (`authz.py:743`). `paging.py:134` reserves per page.
   - p1's warm W1 lease still exists when R1 `p1.a.page` runs in the same epoch.
   - M08 (continue) and M09 (resources/read) consume the same W1 lease concurrently. Order dependence is possible, and so is a continue reserving a new lease while the old one is held.
   - **Condition:** set `max_leases_per_principal` to 3 and `max_graph_leases` to 8 or more (`2*quota <= graph leases`, `:477`). Use separate warm leases, W1 for the cursor and W2 for the resource. State this in the manifest.

5. **MAJOR. Typed-denial classification is under-specified, and `OTHER` makes it vacuous.**
   - Guard denials are HTTP 200 with `isError:true` and a `fixed_result` FORBIDDEN (`shared_http.py:625-664`). Resource denials are JSON-RPC errors (`:665-666`). Router denials are HTTP 4xx/5xx with `data.reason`.
   - The plan lets Day-1 freeze `expected_outcomes` that may include `*:OTHER`. A malformed request or a `LEASE_CAPACITY` error would then count as a "typed denial".
   - **Condition:** the classifier order is: HTTP status and reason; then SSE/JSON parse; then an `error` member gives `rpc_error`; then `isError` gives `tool_error:<code>`, read from `structuredContent.error.code` or legacy `error`. `ok` is only a result with no isError, no top-level `error` and no `status:"error"`. A malformed body gives `transport_error`.
   - Frozen `expected_outcomes` for deny rows may not contain `OTHER`. An `OTHER` at Day-1 is investigated, not frozen. Each deny request must have the same shape as its control, differing only in authorization.

6. **MAJOR. AC-002 teeth cover only the `acquire` condition.**
   - `teeth_replace_by_copy` corrupts a retained farm. The status gid, canary and gid-equality conditions have no teeth. They could be always-true and nothing would notice.
   - **Condition:** add `teeth_wrong_expectation`. Run the validator on a healthy namespace against the other tree. It must record `status_current_matches`, `server_expected_canary` and `control_gid_matches` false, with `valid:false`. Run it on a copy namespace: same repo path, `shutil.copytree` graph root, replace-by-copy via `os.replace` (never an in-place write).
   - Keep the live-inode and sha unchanged assertions on the original.

7. **MAJOR. The timing budget is unproven and inconsistent.**
   - The recorder phases are sequential. My estimate:
     - interrupt matrix: about 22 s per case (5 builds plus one resume, status, acquire, stdio server), 9 cases over 4 workers, about 66-90 s;
     - isolation: about 25 s;
     - compat servers: about 16 s;
     - capacity: about 20 s.
     - Total is about 130-150 s, against a `recorder_total_s` of 150, a fixture timeout of 170 and a gate of 120.
   - On 4 vCPU the four workers contend with each other. A hard deadline then turns slowness into a red CI test.
   - **Condition:** Day-1 (h) must measure under `--cpus 4` on the full D6 case flow. Deadlines are at least 2x the measurement (fixture at least 300 s; per-case at least 120 s). The 120 s figure is a dev-image budget gate, not a test assertion. Cuts are applied before freezing, not after.
   - Consider overlapping the independent isolation, compat and capacity phases with the interrupt pool, sized so the four vCPU are not oversubscribed.

8. **MINOR. Interruption mechanics.**
   - The job id for cancel and `gc_vs_build` (a live blocked job) comes from `jobs status.running_jobs` (`jobs.py:788-791`). Only kill cases use `interrupted_jobs`. D6 says `interrupted_jobs` for all.
   - `cancel_sigterm` must release the seam after SIGTERM, because the handler only sets a flag while the hook blocks in `readline`. S41 does `sleep(0.3)` then `release` (`test_s41_contract.py:229-233`), and uses `staged`, not `pre_replace`.
   - `block` cases require `stdin=PIPE`, held open.
   - Child env must drop all `ATLAS_ACI_*` except the targeted fault spec, as `s41.child_env` does (`test_s41_support.py:134-139`).
   - Day-1 (d) must replay the D6 sequence (T1 base, then T2 over a warm cache) for all eight injection kinds, not S41's cold flow. Otherwise `checkpoint at=2` and `resumed_from 2` are unproven.
   - Facts record normalized `killed:true`, never the raw -9 or 137.

9. **MINOR. Compatibility.**
   - `negotiated` must be read from the initialize response (`result.protocolVersion` / `InitializeResult`). Subsequent requests must send `mcp-protocol-version: <negotiated>`, never the unlisted `2099-01-01`, which the SDK transport rejects with 400.
   - The AC-003 test checks listed revisions and the counter-offer against literals, not only `negotiate()`. `negotiate()` is the same atlas code that produced the expectation.
   - SDK shared-http (`streamable_http_client(url, http_client=...)`, mcp 1.30.0 unverified here): Day-1 (c) records `inspect.signature` plus the full initialize, list and call round trip. The 405 GET and DELETE probes are tolerated. On failure the plan's `required:false` path applies.
   - `shared` is derived in the aggregator from `transport == "shared-http"`, never read from the record. Stdio and http baselines never render "shared". Hosts stay `untested` and never become cells.
   - Cut order versus tests: cuts 3, 4 and 6 delete units that the AC-003 and AC-005 negative controls use (`compat.raw.http.2025-11-25`, the unlisted cell, `interrupt.kill_mid_publish`). Use never-cut units for the negative controls, for example `compat.raw.shared-http.2025-11-25` and `interrupt.kill_checkpoint`.

10. **MINOR. Capacity and saturation.**
    - The per-principal check at router stage 8 is before any body read (`shared_http.py:925-931`). The shared gate's `try_admit` is before `handle_request` reads the body (`http.py:330-336`). Withheld-body holds therefore do occupy both slots, so the saturation is deterministic with polling.
    - `peak <= max_inflight` is tautological because the gate rejects at the limit. Label it as such. Optionally add a deterministic gate phase: p1 holds 2 and p2 holds 2, then p3 gets `http:503:inflight_limit`, with the stopped-line `rejected >= 1`.
    - The retry loop has a 1 s `Retry-After` per 503. A 6-client mix against a gate of 4 makes wall time variable. Bound `all_calls_completed` explicitly (attempts at most 10) and keep rejection counts in `observed`.
    - The plan declares no capacity grants. p2, p3 and p4 each lack grants on some of the mix's tools or repositories. The mix needs per-principal allowed calls, with unauthorized count zero.
    - The stopped line is emitted in `run_shared`'s `finally` on SIGTERM and is parseable (`:1110-1116`). Colors are off unless stderr is a tty (`__main__.py:79`).

11. **MINOR. Concurrency claim wording.**
    - Gate `peak >= 3` can come from the six main requests alone, so it does not prove the two holds were admitted.
    - **Condition:** add a settle of at least 300 ms after the hold headers go out. The report says "client intervals overlapped and the server gate peak was at least 3 (the gate counts requests, not principals)". `holds_spanned` is a recorder boolean the aggregator cannot recompute; label it as recorder-attested.

12. **MINOR. Aggregator robustness.**
    - Map `gzip.BadGzipFile`, `JSONDecodeError`, `UnicodeDecodeError`, non-dict JSON and unexpected keys to typed `ReadinessInputError` codes (`not_canonical` or `unknown_shape`). Raw exceptions are not acceptable.
    - A `status:"failed"` record may carry no body. The checks must read `status` first and yield `fail` or `missing`, never `KeyError`.
    - `inputs[].sha256` is `digest(strip(record))`, never the sha of the gz bytes.
    - Compare canonical plain bytes, not recompressed gz bytes. Deflate output varies with zlib version, and 3.12 versus 3.14 may differ.
    - `strip` removes `observed` and `diagnostic` at every depth, including inside rows.
    - Reusing `v3_aggregate` via one importlib spec is safe: `ensure_ascii=False`, `allow_nan=False`, `mtime=0` and no filename give byte-stable canonical output. It is untouched and frozen by scope. Load it under a unique module name and `assert spec.loader`.

13. **MINOR. Orphans.**
    - `subprocess.run(timeout=170)` SIGKILLs only the recorder. A `serve-shared` child would survive.
    - **Condition:** the fixture uses `Popen(start_new_session=True)` and `os.killpg` on timeout. The recorder installs a SIGTERM/atexit reaper and tracks every child PID. Every subprocess in a test has a timeout, since there is no pytest-timeout and no job timeout.
    - In the production image the recorder is PID 1 and reaps its direct children only.

14. **MINOR. Wording.**
    - The "never contains `ready` outside `ready_for_operator_review`" test uses `\bready\b` and must pass for "already", "readiness" and the `ready_for_operator_review` token.
    - Keep the S36 BANNED words (`compatible`, `safe`, `trusted`) and the S39 words out of every document.
    - State in the md that `isolation` means the enumerated cases (H2), and that no real host (Claude Code, Copilot, Cursor) was tested.

15. **MINOR. Lint scope.**
    - `ci.yml:119-139` lints `src/`, `tests/` and `../scripts/`. The format config in `mcp-server/pyproject.toml` (line-length 100, rules E,F,W,I,B,UP,SIM,RUF) applies via the cwd, as the existing `v3_aggregate.py` lines over 88 columns show.
    - `atlas/evals` holds only JSON and Markdown here, so it is acceptable that it is not linted.
    - mypy: unique basenames and importlib-only sibling loads are fine. `os.wait4` and `resource` are available on ubuntu.
    - The production recorder with `/usr/local/bin/python -I` works. The script directory is not on `sys.path` under `-I`, so sibling loads by path are right. `atlas_aci`, `jwt`, `cryptography` and `httpx` come from the image's installed deps. Check the versions in P0.

16. **Pins confirmed, no action.**
    - CHANGELOG: the Added bullets are S30, S33, S34, S35, S36, S28, S38, S27, S39, S26, ..., S14, S41 (`:421`), S40 (`:433`). Inserting before `:421` leaves the `bullets[0..9]` pins in S33-S39 untouched. It makes S41 `[-2]` and S40 `[-1]` still hold (`test_s41_isolation.py:424-435`, `test_s40_isolation.py:431-440`). Each `(ACI-S4x)` stays unique.
    - README: row after `:956`, before `INTEGRATION.md`; the S40 and S41 adjacency pins hold.
    - The docs/operations set pin, the `docs/evidence/v3` pins (`test_s23_isolation.py`), the `tests/fixtures/s38` rglob, the tools sha and the S23 manifest sha are all unaffected. `harden-gate-classify.sh` flags only the six named paths and four hot src files.
    - The plan's file set triggers none of them.

Time and risk: three engineering days for a roughly 1.5k-line recorder, an aggregator, three tests, two images and G-PROD is tight. The existing cut list is workable once the negative controls are moved onto never-cut units (finding 9). Rollback is a single revert.

## Stories
As an operator considering shared HTTP serving, I want reproducible, immutable evidence of what was tested: who could reach which repository evidence, how the store recovers from interrupted jobs, which client and transport combinations were exercised, and how the server behaved under a bounded workload. A single failed check must make the verdict `blocked`.

As a reviewer, I want the report to say exactly what is not covered, so a zero is never read as "no leakage".

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s42.criteria.md` (sha256 `3930f6ad...ebdab`). AC-S42-001..006 and RUN-01/02 are not restated; the mapping is above. Interpretations recorded for the checker:
- AC-001 "declared multi-principal fixture" = the D2 case table (main + revocation phases).
- AC-002 "valid completed generation" = D6 validity.
- AC-003 "host/protocol combination" = a (client, transport, requested to negotiated revision) cell backed by a record.
- AC-004 "one workload record" = `report.capacity.workload`.
- AC-006 "canonical metrics" = `strip(report)`.

## Confidence
To be computed with `ramza-score`.
- No product code changes, so pins and goldens cannot churn.
- Every surface used is already tested by S38-S41. The format and replay design reuses S23 precedents.
- Main uncertainties, all Day-1 items with a STOP rule: the SDK bearer path, the subprocess seams, and the exact typed outcomes per deny case.

## Rejected Alternatives
- **In-process S40 Observer reuse as the ledger** (World + gather). It sees internal reads, but it is not a deployed process and S40 already ran it. It is cited as inherited evidence (H4).
- **An src audit hook** (`sys.addaudithook` or an `--audit-ledger` flag). It is an src change and breaks the frozen shas and the read-only surface.
- **GC-seam kills via `jobs gc` reading the fault env.** That is an src change; it is listed untested.
- **A new `docs/operations/shared-readiness.md`.** It breaks 3 pin assertions in 2 files outside scope.
- **pytest-xdist for parallelism.** It is a new dependency and a uv.lock change.
- **Equality between fresh and committed digests.** It is nondeterministic across environments; invariants are used instead.
- **Real host runs.** They need an operator budget and are out of scope.

## Risks (ranked)
1. **Day-1 (g) finds a real leak (P1).** No src change is allowed in this slice, so the outcome is BLOCKED with the finding.
2. **Real-server flakiness in CI** (startup, ports, 4 vCPU). Mitigations: port 0, uncapped stderr files, 30/60/90 s deadlines, no retries outside typed 503s, failed units recorded with a stderr tail.
3. **Nondeterminism reaching the canonical section.** Mitigations: the strip rule, the determinism scan, the D4 "never canonical" list.
4. **Black-box blind spots.** Mitigations: H1-H4, D9, teeth, positive controls.
5. **Over-claiming.** Mitigations: the verdict vocabulary and the BANNED/"ready" tests.
6. **Pin collisions.** Mitigations: F8/F9, G-REG.
7. **CI runtime.** Mitigations: Day-1 (h), cuts.
8. **Production-image differences** (py3.12, `HOME=/tmp`, PID 1, no network). Mitigations: F6, Day-1 (i).
9. **The SDK bearer path.** Recorded as untested.
10. **Platform metrics.** Source labels.
11. **Evidence staleness.** The environment record and the README regenerate command.

## Rollback
One bounded PR with additive files only. `git revert <output_commit>` removes the manifest, the two scripts, three tests, `docs/evidence/s42`, the README section and the two root lines. There is no runtime, schema, dependency or product change.
