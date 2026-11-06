---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-06T12:00:00Z
slice_id: ACI-S41
issue: https://github.com/Rynaro/atlas-aci/issues/62
---
# ACI-S41 - durable index jobs, retained generations and lease-aware garbage collection

## Scope

Issue #62. Base `fa16b1c64f6ad4cb7cfde9957e8536fbaeeff318`: main after PR #106 (S40), with S01-S40 merged except S15, plus the security re-lock (mcp 1.30.0). Branch `aci-s41`.
- Marker `<!-- atlas-aci:slice:ACI-S41 -->` verified.
- Criteria digest `ffa352d6b5bdcb773e47845b08431890ce8d3c3721dcfb534b30385ec6a1659f` (9 product ACs in `test_s41_contract.py`, plus RUN-01/02).
- Risk **P0**. Timebox **5 engineering days**. Rightsize tier **full** (score 6): independent checker and critic record.
- **Dependencies PRESENT:**
  - S08: `storage/snapshots.py` Builder/acquire/retain; `CodeGraph.reader_lease` (codegraph.py:1323).
  - S09: `ExtractionCache.lookup/write` (extraction_cache.py:468/:507); `stats["extraction_cache"]["parser_invocations"]` (codegraph.py:3155, :3277).
  - S10: `dep.validate_outputs` (codegraph.py:2571).
  - S13: `query/leases.py` (`EXPIRED_REFERENCE` :254); `protocol/resources.py` REASONS :18-28 (EXPIRED_REFERENCE → -32002).

Observed facts (verified at planning, base fa16b1c):
- **Fingerprint.** `_producer_inputs` (codegraph.py:1453-1476) hashes the `codegraph.py` bytes, QUERIES and the tree-sitter versions, plus an EXPLICIT 8-name tuple (:1461-1470). Of those, `storage/{evidence,snapshots}.py` and `indexing/{extraction_cache,retrieval}.py` lie in this slice's directories. There is no glob, so new modules are not producer inputs.
- **Publication.**
  - `build()` (:2455) takes `_writer_lock` (:2436-2446: `.index.lock`, a new fd per call, blocking EX) and calls `_publication_base()` (:2506) inside the lock.
  - `_build_atomic` (:2544) then: `_stage_path` (:2448); `snap.Builder` (:2554); `_run_build` (:2652, which runs `builder.publish` :3623 and returns stats with `snapshot_id` :3656); `_close_db()` (:2657); `os.replace` (:2658, the COMMIT); `builder.published = True`; `_retire_previous_native()` (:2660); `snap.retain` (:2662).
  - `finally`: `Builder.close` unlinks installed objects that were never published (snapshots.py:942-954), and the tmp file is removed.
  - Any exception → `index-attempt.v1.json` (:2518-2522).
- **Publishers.** Every publisher holds the writer lock (codegraph.py:2501, 2770, 2969, 5909, 6725).
- **Readers.**
  - `reader_lease` (:1323-1431) constructs `CodeGraph(self.repo, …, graph_root=self.graph_root)` literally (:1331), so it cannot read another directory. Its validation steps are :1339-1381.
  - `_generation_data` (:1018) checks that `inputs.repository == str(self.repo)` and the content hash.
- **snapshots.py.**
  - `acquire` (:717) reads only `source-snapshots/manifest.<sid>.json` and `pack.<sha>.bin`, through `directory()` (:278: absolute path, O_NOFOLLOW per component, never creates).
  - `retain` (:957-993) keeps 2 snapshots (1 with None) and unlinks the rest.
  - The docstring (:1-4) states: "no locks… open fds survive unlink".
- **Extraction cache.** Each entry is written with fsync + `os.replace` (:523-531). The key is `digest(expected)`, and `expected` carries `path` and `source_sha256` (:88-99). `_run_build` resolves `xc.ExtractionCache` at call time (:3148) and writes on every miss (:3318, :3377).
- **Source set.**
  - `_iter_source_files` (:3726) yields in sorted order and honours the skip patterns (config.py:33, which include `.atlas`).
  - `source_hashes` covers every LANG_BY_EXT file read (:3227-3228) and is stored in the generation inputs (:3634).
  - test_export.py:490-504 asserts exactly this set.
- **Errors.** `ToolError(code, message, retry_hint="none")` (enforcement.py:42). `retry_hint` is an open string (in use: none, different_tool, narrower_scope).
- **Pins.**
  - test_s40_isolation.py: FROZEN shas :28-41; pyproject/uv.lock :117-124; lazy import :198-214; `serve`/`serve-shared` params :217-222; CHANGELOG `bullets[-1]` is S40 :420-444; README row :447-457; docs listing :471-473.
  - test_s39_isolation.py: tools sha `94f9567a…`/1631472 and graph-output sha (:28-33); CHANGELOG `bullets[6..9]` (:431-446).
  - S08/S21 retain counts: test_s08_integration.py:347 (==1), test_s21_lifecycle.py:180 (==2).
  - test_thesis_negatives.py:47-51: "alter table". test_export.py:372-379: `nx.compose(` and the merge-driver pattern.
- **Pin not in the scout report:** test_s40_injection.py:338-375 asserts that the set of src files containing `subprocess.*`/`os.system`/`os.exec*`/`asyncio.create_subprocess_*` calls equals exactly 4 audited files. See F1.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-06)
1. **Fingerprint safety.**
   - Byte-identical vs fa16b1c: `codegraph.py`; `storage/{evidence,snapshots}.py`; `indexing/{extraction_cache,retrieval}.py`; `storage/__init__.py`; `indexing/__init__.py`.
   - All S41 code is NEW files under `storage/` and `indexing/`.
   - A tripwire pins the producer-input tuple and asserts that no new module is in it.
2. **Live-server reading of AC-004/007/008.**
   - The query server reads only the CURRENT generation (`graph.9.db`). Current is always a GC root, and GC never touches the live store (`graph.9.db`, `source-snapshots/`, which the inline `retain` still owns and the S08/S21 counts pin).
   - In-flight server readers are also protected by open-FD pinning (snapshots.py:3-4).
   - `server.py`, `query/*`, `protocol/*` and `config.py` stay byte-identical.
   - Cross-process flock leases protect retained non-current generations accessed through the new acquire API and CLI.
   - Proved by a test: a live `reader_lease` running concurrently with GC is unaffected.
   - Wiring `serve` to non-current generations is a documented follow-up.
3. **Retained generations.**
   - A hardlink farm `<atlas_dir>/generations/<gid>/` holding `graph.9.db`, the snapshot manifest and pack, `lease.lock` and `meta.json`.
   - Same filesystem only; otherwise a typed refusal.
   - Captured in short `.index.lock` sections, and never held across `build()`. GC captures current at its start.
   - Plain `index` creates nothing new; S41 directories are created only by `jobs` commands.
4. **Leases.**
   - Reader: O_RDONLY plus `flock(LOCK_SH|LOCK_NB)` on a pre-existing `lease.lock`, an inode re-check and a tombstone check, then `snapshots.acquire` on the farm.
   - GC: global `gc.lock`, then `flock(LOCK_EX|LOCK_NB)` per candidate. Held → retain. Otherwise rename to `retired/<gid>.<nonce>` while holding EX, then delete; crash recovery sweeps `retired/`.
   - A losing reader gets `ToolError("EXPIRED_REFERENCE", …, retry_hint="retry_current")`. The closed `REASONS` set is unchanged.
   - No writable lease directory and no server-side writes; a read-only acquire is proved byte- and mtime-neutral.
   - flock runs on container-local tmp. NFS and Docker Desktop virtiofs caveats are documented.
5. **Job identity.**
   - sha256 of canonical JSON over: format; resolved repo; namespace; sorted langs and frameworks; rails map sha; schema epoch plus `EXPECTED_DDL_HASH`; `_producer_inputs()`; skip patterns; a source-tree digest of the sorted (relpath, sha256).
   - Excludes `since` and anything time-based, and never uses the DB sha (it carries a random publication token).
   - "Digest-verified output" means the receipt's `generation_id` + `snapshot_id` pass a full `snapshots.acquire` and the generation check.
6. **Reuse (AC-001/009).**
   - An identical job with a verified retained output returns the existing receipt with no work (0 parser invocations, no new generation).
   - A missing, expired or digest-invalid output declines reuse and re-runs.
   - Re-publishing a retained non-current output as current is OUT of scope.
7. **Checkpoints and cancellation.**
   - The build runs in its own process (`indexing/job_runner.py`; see F1). Checkpoints are the content-addressed cache entries, recorded through a wrapper over `ExtractionCache.write`.
   - Phases: accepted → running → staged → published → captured → completed.
   - Resume re-runs, and cache hits prove continuation (`parser_invocations == files - prefix`).
   - Cancel (marker or SIGTERM) raises before `os.replace`; after it the outcome is "too late". The `index-attempt.v1.json` side effect is documented and not asserted absent.
   - `CodeGraph` subclass seams are allowed.
   - Fault seams are callables plus an env var read only in the runner entry. After each seam: the pointer is valid, `reader_lease` succeeds and a rerun converges.
8. **GC.**
   - Roots: current; flock-held leases; newest `keep_last` (≥1) by capture sequence; running jobs' bases.
   - `max_age`/`max_bytes` come from a closed `retention.json` (not `Config`). Time enters only through an injectable `now`.
   - Orphan staging is swept only under the writer lock. Cache entries not referenced by live job checkpoints are pruned within bounds.
   - The `gc-receipt` carries a canonical retained-set digest; a rerun over the same reachable set removes nothing and gives the same digest.
9. **Concurrency and metrics.**
   - `max_concurrent_jobs` is enforced with N slot lock files (NB; BUSY when full). The writer lock serialises publication.
   - `jobs status` prints canonical JSON metrics.
10. **CLI.** A new lazy `jobs` group; `index` and `serve` are untouched.
11. **Scope amendments.**
    - `test_s40_isolation.py:471-473`: the docs/operations pin becomes the exact set `{"shared-http-deployment.md", "index-jobs-and-retention.md"}` (S39 relaxed an S38 pin the same way). No other existing test is edited.
    - One CHANGELOG bullet immediately BEFORE the S40 bullet; one README row after the S40 row; `.spectra/evidence/aci-s41/**`.
12. **Invariance.**
    - The tools sha, the budgets 3436/3448/797/7730, `ev.RELATIONS` and the graph-output sha are unchanged, as are the S38 goldens, the S39/S40 frozen shas and pyproject/uv.lock.
    - stdlib `fcntl` only; the query process stays read-only.
    - None of the banned source phrases; ASCII docs without the S39 BANNED words.
13. **Environment.** Dev image `atlas-aci-dev-relock`; G-PROD runs in the production image.

### Planning findings (binding with the decisions; F1 refines the mechanism of decision 7, not its intent)
- **F1. The job process is the `atlas-aci jobs submit|resume` command process.**
  - `job_runner.run_from_cli` runs the build in that process; the fault env var is read only there.
  - Library tests call `job_runner.run_job(..., hooks=)` inline, or in a `python -c` subprocess for kills.
  - Reason: a `subprocess.Popen` in `indexing/` breaks test_s40_injection.py:371-375 (an exact spawn-site set), and re-exporting `Popen` would evade a security audit. Process isolation, SIGKILL and SIGTERM semantics are unchanged.
  - Alternative for the checker: amend `AUDITED` plus the S40 argv audit (a second prior-test edit). Rejected.
- **F2. Checkpoint log.** Per-entry records go to an append-only `checkpoint.log` (one fsynced canonical line each, chained digest; a torn tail is ignored). `checkpoint.json` (fsync+rename) is a constant-size summary rewritten at every phase change and every `checkpoint_summary_interval` entries. Rewriting a full list per file is O(N²) I/O.
- **F3. Farm verification.** It repeats `reader_lease` steps :1339-1381 on a farm sqlite `mode=ro` connection, using a repo-bound read-only checker (`CodeGraph(repo, read_only=True)`, which creates nothing).
  - Private coupling (listed in the module docstring): `_generation_data`, `_snapshot_binding`, `_snapshot_context`, `_acquisition_observations`, `_acquisition_retrieval_documents`, `ret.expected`, `ret.descriptor`.
  - Guarded by the parity test L-PARITY.
- **F4. Capture inside the build's own writer-lock section.** `_JobGraph._publication_base` captures the base, and `_JobGraph._retire_previous_native` captures the new current right after the commit. There is no re-flock (no same-process deadlock) and no window for another publisher.
- **F5. GC renames under the writer lock (O(1) each) and deletes outside it.** Roots are therefore computed against the true current.

### In
1. `storage/generations.py`: layout, `RetentionPolicy`/`load_policy`, `writer_lock`, `current_generation`, `capture_current`, `verify_generation`, `list_generations`, `StoreError`.
2. `storage/leases.py`: `acquire`, `GenerationLease`, `probe_leased`, hooks.
3. `storage/retention.py`: `gc`, orphan sweep, cache prune, retained-set digest, `retire_invalid`.
4. `indexing/jobs.py`: identity, `JobStore`, `submit`/`resume`/`cancel`/`status`, slots, receipts, reuse verification.
5. `indexing/job_runner.py`: `run_job`, `run_from_cli`, `_JobGraph(CodeGraph)`, `CheckpointingCache(xc.ExtractionCache)`, seams, SIGTERM handling.
6. `__main__.py`: an additive lazy `jobs` group.
7. Tests `test_s41_{support,contract,jobs,gc,leases,isolation}.py`. Docs `docs/operations/index-jobs-and-retention.md`. The CHANGELOG bullet and README row. The one S40 pin line. Evidence under `.spectra/evidence/aci-s41/`.

### Out
- Edits to `codegraph.py`, the hashed modules, `storage/__init__.py`, `indexing/__init__.py`, `server.py`, `query/*`, `protocol/*`, `config.py`, `enforcement.py`, `tools/*`, pyproject, uv.lock, the Dockerfile and workflows.
- Serving non-current generations over MCP; re-publishing a retained output as current; remote stores; a background daemon; cross-device retention (copying); unlimited retention; GC of the live store.

## Approach

### Decisions

**D1 APIs** (all types `from __future__ import annotations`; mypy clean).
```python
# storage/generations.py
FORMAT_META = "atlas-aci-generation-meta.v1"; FORMAT_POLICY = "atlas-aci-retention.v1"
GID = re.compile(r"[0-9a-f]{64}")
STORE_REASONS = ("cross_device", "writer_busy", "index_unavailable", "meta_invalid",
                 "capture_conflict", "leased_invalid")
class StoreError(Exception): reason: str            # str(exc) == f"store error ({reason})"
@dataclass(frozen=True) class RetentionPolicy:
    keep_last: int = 2; max_age_seconds: int | None = None; max_bytes: int | None = None
    max_cache_bytes: int | None = None; max_concurrent_jobs: int = 1
    checkpoint_summary_interval: int = 64
class PolicyError(ValueError): reason: str; field: str   # "invalid retention policy (<reason> at <field>)"
POLICY_REASONS = ("unreadable", "too_large", "not_json", "duplicate_key", "unknown_field",
                  "wrong_type", "out_of_range", "bad_format")
@dataclass(frozen=True) class Namespace: repo: Path; graph_root: Path | None; atlas_dir: Path
def namespace(repo: Path, graph_root: Path | None) -> Namespace     # via CodeGraph(read_only=True); creates nothing
def load_policy(ns: Namespace) -> RetentionPolicy                    # <atlas_dir>/retention.json; absent -> defaults
@contextmanager def writer_lock(ns, *, wait_seconds: float = 30.0)    # flock .index.lock EX|NB poll 50 ms; StoreError writer_busy
def current_generation(ns) -> tuple[str, str | None] | None           # (gid, sid) via reader_lease
def capture_current(ns, *, now: int, locked: bool, hooks=None) -> Meta | None
def verify_generation(ns, farm: Path, meta: Meta) -> Verified          # F3; returns an open SourceLease | None
def list_generations(ns) -> list[Meta]                                 # 64-hex dirs; invalid meta -> Meta(valid=False)
# storage/leases.py
LEASE_RETRY_HINT = "retry_current"; ACQUIRE_ATTEMPTS = 3; ACQUIRE_BACKOFF_SECONDS = 0.02
LEASE_SEAMS = ("pre_lock", "post_lock")
class GenerationLease:
    generation_id: str; snapshot_id: str | None; origin: Literal["retained", "live"]
    db: sqlite3.Connection                       # mode=ro
    def read_source(self, path: str, source_sha256: str | None = None) -> bytes
    def close(self) -> None                      # idempotent; drops flock and every fd
def acquire(ns, generation_id: str, *, hooks: Mapping[str, Callable[[], None]] | None = None) -> GenerationLease
def probe_leased(farm: Path) -> bool             # EX|NB then immediate LOCK_UN; read-only
# storage/retention.py
GC_SEAMS = ("gc_post_recover", "gc_post_capture", "gc_post_sweep", "gc_post_reachability",
            "gc_post_lock_ex", "gc_post_rename", "gc_mid_delete", "gc_pre_receipt")
def gc(ns, *, policy: RetentionPolicy, now: int, wait_seconds: float = 30.0, hooks=None) -> dict
def retained_digest(ns) -> str
def retire_invalid(ns, generation_id: str) -> bool
# indexing/jobs.py
@dataclass(frozen=True) class JobSpec: ns: Namespace; langs: tuple[str, ...]; frameworks: tuple[str, ...]; rails_table_map: bytes | None
def identity(spec: JobSpec) -> dict        # canonical identity object (D4)
def job_id(ident: dict) -> str             # sha256(canonical(ident))
def submit(spec, *, now: int, hooks=None, install_signals: bool = False) -> dict    # result envelope
def resume(ns, job: str, *, now: int, hooks=None, install_signals: bool = False) -> dict
def cancel(ns, job: str) -> dict
def status(ns) -> dict
class JobError(Exception): code: str; retry_hint: str = "none"
# indexing/job_runner.py
JOB_SEAMS = ("runner_start", "predecessor_captured", "checkpoint", "mid_publish", "staged",
             "pre_replace", "post_replace", "post_retire", "captured", "post_retain",
             "pre_receipt", "pre_complete")
FAULT_ENV = "ATLAS_ACI_JOB_FAULTS"; FAULT_ACTIONS = ("kill", "block", "raise")
def run_job(store, job: str, *, now: int, hooks=None) -> dict
def run_from_cli(store, job: str, *, now: int) -> dict   # the only reader of FAULT_ENV; installs SIGTERM
```

**D2 On-disk layout and formats.**
```
<atlas_dir>/  (<repo>/.atlas or <graph_root>/repos/<sha256(repo)>)
  graph.9.db  source-snapshots/  extraction-cache/  .index.lock       existing; GC never touches the first two
  retention.json                                                      optional, operator-written
  generations/                                                        created only by jobs submit|resume|gc
    <gid>/graph.9.db                       hardlink of the captured live inode
    <gid>/source-snapshots/manifest.<sid>.json, pack.<psha>.bin   hardlinks (absent if no snapshot)
    <gid>/lease.lock                       empty, 0644, created BEFORE the dir is published
    <gid>/meta.json
    .stage.<nonce>/   retired/<gid>.<nonce>/   gc.lock   gc-receipt.json
  jobs/slots/<i>.lock                      i in 0..max_concurrent_jobs-1
  jobs/<job_id>/{job.json, checkpoint.log, checkpoint.json, receipt.json, cancel, run.lock}
```
- **Writes.** Every JSON is canonical (`ev.canonical`) + "\n", written to `.tmp.<nonce>` (O_EXCL, 0600) in the same directory, fsynced, `os.replace`d, then the directory is fsynced.
- **Reads.** Closed key sets; duplicate keys, NaN and Infinity are rejected; booleans are never accepted as ints. Size caps: meta 64 KiB; job, receipt and gc-receipt 1 MiB.
- **`meta.json`** (`atlas-aci-generation-meta.v1`):
  `{format, generation_id, snapshot_id|null, kind, repository, namespace, seq, captured_at, db_sha256, db_size, pack_sha256|null, pack_size|null}`.
  - `kind` comes from the generation inputs. `seq` = 1 + the maximum existing seq (under the writer lock). `captured_at` = the injected `now` (int seconds).
  - `db_sha256` is an integrity check of this captured file, never an identity.
- **`retention.json`** (`atlas-aci-retention.v1`):
  `{format, keep_last 1..64, max_age_seconds null|0..31536000, max_bytes null|0..2^53, max_cache_bytes null|0..2^53, max_concurrent_jobs 1..16, checkpoint_summary_interval 1..4096}`.
  - All fields optional except `format`; ≤ 64 KiB; errors use `PolicyError` with a JSON-pointer `field`.
- **`job.json`** (`atlas-aci-index-job.v1`):
  `{format, job_id, identity, graph_root|null, state, attempt≥1, base_generation_id|null, staged_generation_id|null, published_generation_id|null, snapshot_id|null, failure_reason|null, cancel, resumed_from|null, last_decline|null}`. No timestamps and no pid.
- **`checkpoint.log`.** One line per durable cache write:
  `{"n":i,"path":rel,"source_sha256":sha,"key":k,"state":"complete"|"failed","chain":c}`
  - `c_i = sha256(c_{i-1} || canonical(line without chain))`; `c_0 = sha256("atlas-aci-job-checkpoint.v1\0" + job_id)`.
  - `checkpoint.json` = `{format:"atlas-aci-job-checkpoint.v1", job_id, attempt, count, chain}`.
- **`receipt.json`** (`atlas-aci-index-job-receipt.v1`):
  `{format, job_id, attempt, outcome:"built"|"recovered", generation_id, snapshot_id|null, base_generation_id|null, resumed_from|null, extraction_cache:{parser_invocations, disk_hits, misses, write_failures}|null, files_indexed|null, cancel:"none"|"too_late"}`. No time fields.
- **Result envelope** (stdout of submit/resume): `{format:"atlas-aci-job-result.v1", job_id, reused, decline_reason|null, receipt}`. When reused, the `receipt` value equals the stored receipt exactly.
- **`gc-receipt.json`** (`atlas-aci-gc-receipt.v1`):
  `{format, now, policy, current_generation_id|null, retained:[{generation_id, reason}], retired:[{generation_id, reason}], recovered, swept:{graph_stage, sources_stage, attempt_temp, cache_temp, capture_stage, job_temp}, cache:{entries, bytes, pruned}, retained_set:{generations:[[gid, sid|null, db_sha256, pack_sha256|null]], cache_keys_sha256}, retained_digest}`.
  - `retained_digest = sha256(canonical(retained_set))`; lists are sorted by gid.
- **Status** (`atlas-aci-jobs-status.v1`, read-only, creates nothing):
  `{format, namespace, current_generation_id|null, jobs:{running, interrupted, completed, cancelled, failed}, phases:{accepted, running, staged, published, captured}, running_jobs[≤64], slots:{capacity, busy}, retained:{generations, bytes, leased}, generations:[{generation_id, seq, snapshot_id, bytes, leased, current}][≤256], generations_truncated, last_gc:{retained_digest, retired, now}|null, policy}`.
  - `bytes` = the sum of `st_size` over distinct `(st_dev, st_ino)`.
- **Error envelope** (stdout on non-zero exits other than 2): `{format:"atlas-aci-job-error.v1", code, retry_hint, generation_id?|job_id?}`.

**D3 Closed vocabularies.**
- `JOB_STATES = ("accepted","running","staged","published","captured","completed","cancelled","failed")`. Terminal: completed, cancelled, failed.
- Derived: a non-terminal state with `run.lock` held is `running`; with `run.lock` free it is `interrupted`.
- `CANCEL = ("none","requested","honoured","too_late")`.
- `CANCEL_OUTCOMES = ("requested","cancelled","too_late","not_running")`.
- `FAILURE_REASONS = ("build_error","source_changed","capture_failed","injected_fault")`.
- `DECLINE_REASONS = ("no_receipt","output_retired","output_raced","output_digest_invalid","identity_mismatch")`.
- `RETAIN_REASONS = ("current","leased","job_base","window")`.
- `RETIRE_REASONS = ("outside_window","max_age","max_bytes","invalid_meta")`.
- `JOB_ERRORS = ("BUSY","JOB_RUNNING","JOB_UNKNOWN","JOB_NOT_RESUMABLE","JOB_FAILED","JOB_INTERRUPTED","JOB_CANCELLED","GC_BUSY","WRITER_BUSY","CROSS_DEVICE","STORE_INVALID")`.
- ToolError codes reused unchanged: `EXPIRED_REFERENCE` (retry_hint `retry_current`), `INVALID_REFERENCE`, `SNAPSHOT_UNAVAILABLE`, `INDEX_UNAVAILABLE`.
- All tuples are unique, and `protocol/resources.REASONS` is unedited.

**D4 Identity and reuse.**
- `identity` = `{format:"atlas-aci-index-job-identity.v1", repository, namespace, languages:sorted, frameworks:sorted, rails_table_map_sha256|null, schema:{epoch, ddl_hash}, producer:_producer_inputs(), skip_patterns:list(DEFAULT_SKIP_PATTERNS), source_tree_sha256, source_files}`.
- The tree is `[[rel, sha256]]` sorted over `_iter_source_files()` filtered by LANG_BY_EXT suffix, with bytes read via `snap.safe_source`. This is exactly the `source_hashes` set the build records.
- `job_id = sha256(canonical(identity))`. There are no time fields and no `since`. Jobs always build with `since=None` and reuse comes from the cache.
- **Reuse** (submit on an existing completed job):
  1. Read the receipt; absent → `no_receipt`.
  2. `leases.acquire(gid)` on the farm: dir absent → `output_retired`; EXPIRED during acquire → `output_raced`; any verify failure → `output_digest_invalid`.
  3. Inside the lease, check that the farm's `generation_inputs.source_hashes` digest == `source_tree_sha256` and that `producer`/`repository` equal the identity; else `identity_mismatch`.
  4. All pass → envelope `reused:true` with the stored receipt; nothing is written (job.json bytes unchanged).
- **Decline** → record `last_decline`, `attempt += 1`, run D5. `output_digest_invalid` first calls `retire_invalid(gid)` (EX|NB; a leased corrupt farm → `JOB_FAILED capture_failed`).

**D5 Runner, checkpoints, cancellation, seams.**
- **Submit/resume order:**
  1. Resolve the namespace and policy.
  2. Compute the identity (submit) or load job.json (resume, which re-derives langs, frameworks and graph_root from it).
  3. Reuse check (no lock needed).
  4. Slot: flock EX|NB on `jobs/slots/<i>.lock` for i in range(N); none free → `BUSY` (exit 75), nothing written.
  5. `run.lock` EX|NB; held → `JOB_RUNNING`.
  6. Write job.json `accepted` (or a new attempt), remove a stale `cancel`.
  7. **Reconcile:** if `staged_generation_id` == the live current gid (an earlier attempt died after the commit) → capture under `writer_lock`, receipt outcome `recovered`, `completed`.
  8. Otherwise `run_job`.
- **`run_job`:**
  - Validates the checkpoint log: the longest prefix whose lines parse, chain-verify, and whose cache envelope at `xc.ExtractionCache(atlas_dir).path(inputs)` has a valid structure (format, key, checksum). The log is truncated to that prefix, and `resumed_from` is set to its length.
  - Then, inside `patched()` (a context manager that restores on exit and is process-local in the CLI process), it rebinds `xc.ExtractionCache` to `CheckpointingCache`, `snap.Builder._install` to a wrapper that fires `mid_publish` after the first install, and `snap.retain` to a wrapper that fires `post_retain` after the original returns.
  - Then `_JobGraph(repo, langs, graph_root, frameworks, rails_table_map).build(since=None)`.
- **`CheckpointingCache.write`:** `ok = super().write(...)`; when ok, append and fsync the log line; every interval, write the summary; fire `checkpoint` with n; then cancel check.
- **`_JobGraph` overrides** (no `codegraph.py` edit):
  - `_publication_base`: `super()`; `capture_current(locked=True)` of the base; persist `base_generation_id`; `predecessor_captured`.
  - `_run_build`: `stats = super()`; read `generation_id` and `generation_inputs.source_hashes` from the staged `conn`. A tree digest ≠ identity → raise `SourceChanged` (failed, `source_changed`; nothing published). Persist `staged` + `staged_generation_id`; `staged`; cancel check; set `_s41_staged`.
  - `_close_db`: when `_s41_staged`, fire `pre_replace` once, then the final cancel check. This is the last point before `os.replace` :2658.
  - `_retire_previous_native`: persist `published`, fire `post_replace`, `super()`, fire `post_retire`, `capture_current(locked=True)` of the new current, persist `captured`, fire `captured`. A capture exception is logged and retried once by `run_job` after `build()` returns, under `writer_lock`.
- **After `build()`:** `pre_receipt` → receipt → `pre_complete` → `completed`.
- **Cancellation.**
  - The check raises `JobCancelled(Exception)` when the `cancel` marker exists or the SIGTERM flag is set (the handler only sets the flag; it is installed by `run_from_cli`, which also restores it).
  - The exception unwinds through `build()`: the writer lock is released, `Builder.close` unlinks installed objects, and the stage is removed. `index-attempt.v1.json` is written (documented).
  - Before the commit → `cancelled`, `cancel:"honoured"`, exit 4. After the commit no check raises → `completed`, `cancel:"too_late"`.
- **Seam actions.**
  - `hooks[name]()`: tests' callables.
  - `ATLAS_ACI_JOB_FAULTS` (≤ 1 KiB canonical JSON `{seam: {"action": A, "at": n?}}`) is read only by `run_from_cli`. An unknown seam or action → exit 2 `invalid fault specification`.
  - `kill` = `os.kill(getpid(), SIGKILL)`; `raise` = `InjectedFault` (failed, `injected_fault`); `block` = print `ATLAS_JOB_SEAM <name>` to stdout, flush, then read one stdin line.
  - The CLI prints nothing else to stdout before its final JSON.

**D6 Capture** (`capture_current(ns, now, locked, hooks)`; precondition: the writer lock is held).
1. `current_generation(ns)` via `CodeGraph(repo, read_only=True, graph_root).reader_lease()`. None → return None.
2. `generations/<gid>` exists:
   - its `graph.9.db` inode == the live inode → return its meta (idempotent);
   - its meta is valid and `db_sha256` matches → return it;
   - otherwise `retire_invalid(gid)` (EX|NB; leased → `StoreError capture_conflict`).
3. Same filesystem: `stat(generations).st_dev == stat(atlas_dir).st_dev`, else `StoreError cross_device`. EXDEV from `os.link` → `cross_device`.
4. `mkdir generations/.stage.<nonce>` (0700). `os.link(graph.9.db)`, `os.link(manifest)`, `os.link(pack)` (`follow_symlinks=False`, via dir fds). Assert the linked inode == the live inode. `capture_post_link`.
5. Create `lease.lock` (O_CREAT|O_EXCL, 0644). Stream the db sha. Write `meta.json`. fsync files and dirs. `capture_pre_rename`.
6. `rename(.stage.<nonce>, generations/<gid>)`; fsync `generations/`.

The farm is immutable after step 6; `lease.lock` is never written.

**D7 Lease protocol** (`acquire(ns, gid)`; every open is O_RDONLY|O_NOFOLLOW|O_CLOEXEC, and nothing is created, linked, renamed, unlinked or utime'd).
1. `GID.fullmatch` fails → `INVALID_REFERENCE`.
2. `D = open(generations/<gid>, O_DIRECTORY)`. ENOENT → **live path:** `reader_lease()` on the namespace; if `reader.generation_id == gid` → `origin="live"` (FD-pinned, as the server is); else `EXPIRED_REFERENCE`.
3. `L = openat(D, "lease.lock")`; ENOENT → `EXPIRED_REFERENCE`. `hooks.pre_lock`.
4. `flock(L, LOCK_SH|LOCK_NB)`. On EWOULDBLOCK, retry up to `ACQUIRE_ATTEMPTS` with backoff (this absorbs a status probe); still EWOULDBLOCK → close, `EXPIRED_REFERENCE`. `hooks.post_lock`.
5. **Tombstone check:** `lstat(generations/<gid>)` by name has the same `(st_dev, st_ino)` as `fstat(D)`. **Inode check:** `fstatat(D, "lease.lock")` has the same `(st_dev, st_ino)` as `fstat(L)`. Either fails → unlock, close, `EXPIRED_REFERENCE`.
6. `verify_generation` (F3) on `D`'s path: meta, db sha, `_generation_data` gid == gid, `snap.acquire(farm, sid, repository=repo, expected=binding)` (manifest, pack and blob checksums), the context check and the v5 retrieval closure. Any failure → `SNAPSHOT_UNAVAILABLE` (fixed text).
7. Return the lease holding `L` (SH), the sqlite `mode=ro` connection and the SourceLease. `close()` drops all of them; process death drops the flock (kernel).

The message for every `EXPIRED_REFERENCE` is the fixed text "Generation reference expired or retired." with `retry_hint="retry_current"`.

**Proof sketch** (atomicity against retirement; local POSIX filesystem, one kernel). The only path to retirement is: hold EX on the inode `I` of `<gid>/lease.lock`, then `rename(<gid>, retired/…)`, delete, release. Reader validity = (SH granted on `I`) ∧ (the step-5 name checks pass after the grant). flock SH and EX on the same inode are mutually exclusive (this holds across processes and across separate open file descriptions within one process). Four cases:
- (a) The reader's SH is granted before GC tries EX → GC's NB EX fails → retained (AC-004). The rename never happens while SH is held, so the reader's later reads see an intact farm.
- (b) GC holds EX when the reader tries → NB fails after retries → explicit retryable expiry.
- (c) GC renamed and released before the reader's SH is granted. The rename precedes the release, and the grant follows the release, so the step-5 `lstat` happens after the rename and observes a missing or different directory → expiry. The flock follows `I` through the rename, so a reader holding a pre-rename fd still contends on the same `I`.
- (d) A later re-capture of the same gid creates a new directory and a new `lease.lock` inode → a stale `L` fails the inode check → expiry; a retry finds the new farm.

No interleaving yields a lease on a directory that GC has renamed or will rename. Hence AC-008: readable, or `EXPIRED_REFERENCE` with `retry_current`.

Limits:
- NFS (flock emulation varies) and Docker Desktop virtiofs/osxfs bind mounts are unsupported and documented.
- Linux local filesystems, overlayfs, named volumes and `:ro` bind mounts are supported; Day-1 (b) verifies this.

**D8 GC** (`gc(ns, policy, now)`).
1. Ensure `generations/` exists. `gc.lock` EX|NB, else `GC_BUSY`.
2. **Recovery:** delete every `retired/*` left by a crashed run (`shutil.rmtree`, which is fd-based and does not follow symlinks). Count them as `recovered`. `gc_post_recover`.
3. **Under `writer_lock(wait_seconds)`:**
   - (a) `capture_current(locked=True)`, which covers unmanaged `index`/`import` publishers. `gc_post_capture`.
   - (b) **Sweep** (no build can be live while the lock is held):
     - `<atlas_dir>/.graph.9.db.tmp.*` (and `-journal`), `.sources-stage.*`, `.index-attempt.*`;
     - `extraction-cache/v1/??/.entry-*`; `generations/.stage.*`;
     - `jobs/<id>/.tmp.*` only when that job's `run.lock` is free.
     - Never `graph.*.db`, `source-snapshots/**`, `catalog.json` or `index-attempt.v1.json`.
     - `gc_post_sweep`.
   - (c) **Roots:**
     - `current` = the live gid;
     - `job_base` = the `base_generation_id` of every job whose `run.lock` is held;
     - `window` = the first `keep_last` farms by `(seq desc, gid)`, minus farms with `now - captured_at > max_age_seconds`, then trimmed oldest-first while `bytes(roots ∪ window) > max_bytes` (hard roots are never trimmed).
   - (d) Candidates = valid farms not in roots ∪ window, plus invalid-meta farms, sorted by `(seq, gid)`. `gc_post_reachability`.
   - (e) For each candidate: open its dir and lock (missing `lease.lock` → no reader can hold it); `flock EX|NB`. EWOULDBLOCK → retained `leased`. Otherwise `gc_post_lock_ex`, then `rename(<gid>, retired/<gid>.<nonce>)` while holding EX, `gc_post_rename`, and keep the fd.
   - (f) **Cache prune** only when `max_cache_bytes` is set:
     - protected = keys in the `checkpoint.log` of every non-terminal job;
     - delete unprotected `extraction-cache/v1/??/<64hex>.json` by `(st_mtime_ns, key)` until the total ≤ the bound.
4. Release the writer lock.
5. Delete each retired directory (`gc_mid_delete` after its first unlink), then close its EX fd.
6. Compute `retained_set`/`retained_digest`, `gc_pre_receipt`, write `gc-receipt.json`, release `gc.lock`.

**Idempotence (AC-006).** Run 1 converges to retained = roots ∪ window ∪ leased. With the same farms, leases, jobs, `now` and policy, run 2 computes the same roots and window and has no candidates. The capture is idempotent (inode equality), sweeps and recovery find nothing, and the prune is already under its bound. So no file is created, renamed or removed, and the digest is equal.

Crash at any GC seam: the next GC recovers `retired/` and reaches the same set. A farm locked but not renamed is intact (the lock dies with the process).

**D9 Concurrency.** N slot files (`max_concurrent_jobs`) plus a per-job `run.lock` (one runner per identity). The writer lock serialises every publication and capture, and GC's capture, sweep, roots and renames. `status` probes are NB and non-blocking.

**D10 CLI** (`__main__.py`, additions only; each body lazily imports `atlas_aci.indexing.jobs` or `atlas_aci.storage.*`).
```python
@cli.group("jobs")
def jobs_group() -> None:
    """Operator index jobs, retained generations, reader leases and garbage collection."""
# every subcommand: --repo (exists, dir) and --graph-root (file_okay=False)
jobs submit  [--langs] [--frameworks] [--rails-table-map PATH]   # index-identical validation
jobs resume  --job ID
jobs cancel  --job ID
jobs status
jobs gc      [--now INT>=0]                                      # default int(time.time())
jobs acquire --generation GID [--hold]                           # --hold: print lease JSON, wait for stdin EOF/SIGTERM
```
- **Exit codes:**
  - 0: completed, reused, status, gc, acquire or a cancel outcome;
  - 1: `JOB_UNKNOWN`/`JOB_NOT_RESUMABLE`/`JOB_FAILED`/`JOB_INTERRUPTED`/`CROSS_DEVICE`/`STORE_INVALID`/`INDEX_UNAVAILABLE`/`SNAPSHOT_UNAVAILABLE`;
  - 2: usage; 3: `EXPIRED_REFERENCE`/`INVALID_REFERENCE`; 4: `JOB_CANCELLED`;
  - 75: `BUSY`/`JOB_RUNNING`/`GC_BUSY`/`WRITER_BUSY`.
- **Output.** stdout carries exactly one canonical JSON line (result, status, gc-receipt, cancel or lease, or the error envelope). stderr carries the fixed text `jobs: <CODE>`. Neither ever contains `str(exc)`, a path, or file content.
- **Usage errors** (exit 2, empty stdout, nothing created):
  - U1: click (a missing option, `--now` < 0);
  - U2: `--job: must be a 64-character lowercase hex job id` and `--generation: must be a 64-character lowercase hex generation id`;
  - U3: frameworks invalid (index's text);
  - U4: rails table map unreadable or invalid (index's texts, via `_read_table_map`);
  - U5: `retention.json: invalid retention policy (<reason> at <field>)`;
  - U6: an invalid fault specification.
- **Side effects.** `status`/`acquire` are read-only. `submit`/`resume`/`gc` create the S41 directories. Plain `index`/`serve` are untouched.

**D11 Docs.**
- **`docs/operations/index-jobs-and-retention.md`** (ASCII, no BANNED words). Sections:
  1. Summary and the decision-2 reading (the server reads current only; FD pinning; follow-up).
  2. Layout.
  3. Every D2 format.
  4. Job lifecycle, states and identity.
  5. Checkpoints and resume (cost, torn tail).
  6. Cancellation (commit point, too late, the `index-attempt.v1.json` side effect).
  7. The lease protocol and its proof summary; NFS, Docker Desktop and read-only mount notes.
  8. GC roots, policy, `now` determinism, sweep list, cache prune.
  9. Status fields.
  10. CLI and exit codes U1-U6.
  11. Limitations: no MCP serving of retained generations; no re-publication; same-device only; the `.catalog.*` temp and objects installed but unpublished by a killed build are cleaned by the next publication's `retain`, not by GC; hardlinks pin inodes; the status probe causes a backoff retry.
- **CHANGELOG.** One bullet inserted between the ACI-S14 bullet and the S40 bullet (`CHANGELOG.md:421`), starting `- **Index jobs, retained generations and lease-aware garbage collection (ACI-S41).**`. It names `atlas-aci jobs` and `docs/operations/index-jobs-and-retention.md`. It has no `(ACI-S40)` or other `(ACI-S` token, no continuation line starting `- `, and is ASCII.
- **README.** One row after the S40 row (`README.md:955`): `| [\`docs/operations/index-jobs-and-retention.md\`](docs/operations/index-jobs-and-retention.md) | <one cell> |`. It has 3 pipes and does not contain `shared-http-deployment.md`.

**D12 Test harness** (`test_s41_support.py`; stdlib only at the top level; no test functions).
- `write_tree(root, version)`: 5 Python files, v1/v2/v3 differing in 2 files. `Ns(tmp)`: repo plus an external graph root, all `.resolve()`d.
- `cli(args, env=, stdin=PIPE, timeout=60)`: `[sys.executable, "-m", "atlas_aci", ...]`; `seam_wait(proc, name, 15)` uses selectors.
- `faults({...})` builds the env JSON. `listing(atlas_dir, exclude)` returns `{rel: (sha256, size, st_mtime_ns, st_ino)}`.
- `FlockSpy`: wraps `leases.fcntl.flock`/`retention.fcntl.flock`, records (op, outcome), and has optional Event barriers.
- `OpSpy`: records `os.{open (flags), link, rename, replace, unlink, rmdir, mkdir, utime}` and `sqlite3.connect` URIs.
- `twin(ns)`: a second graph root for the same repo path, giving the same gids by content address.
- Every wait is bounded. `production_main(argv)` drives G-PROD.

### Acceptance-test mapping (`test_s41_contract.py`; real builds, no mock of the behaviour under test; every `all()` is preceded by a non-empty check)
| AC | Test | Steps, barriers and observers |
| --- | --- | --- |
| 001 | test_job_dedup | **Setup.** v1. CLI `jobs submit` → `reused:false`, `parser_invocations == 5` (positive control), farm G1 present. **Repeat** the same submit, via the CLI and in-process: `reused:true`, `decline_reason null`, receipt bytes == the stored `receipt.json`, and `job.json`, `generations/` and `extraction-cache/` listings unchanged. In-process: `run_job` calls 0, `_JobGraph.build` calls 0, `xc.ExtractionCache.write` calls 0; the live gid is unchanged. **Variant.** A changed `--langs` gives a different `job_id` and does not reuse. |
| 002 | test_job_resume | **Setup.** v1. Submit with `faults({"checkpoint":{"at":2,"action":"kill"}})`. The process dies by SIGKILL (returncode -9); job.json state `running`; `status.jobs.interrupted == 1`; `checkpoint.log` has 2 valid lines; the live gid is unchanged (no commit). **Resume** → completed: `resumed_from == 2`, `parser_invocations == 3`, `disk_hits == 2`; receipt gid == a twin uninterrupted run's gid (whose `parser_invocations == 5` is the control). **Variant.** Corrupting cache entry 2 → `resumed_from == 1`, `parser_invocations == 4`. A torn tail line is ignored. |
| 003 | test_cancel_no_publish | **Setup.** v1 published (G1); edit to v2; submit with `block` at `staged`; READY. **(a)** `jobs cancel` → `requested`; release → exit 4, state `cancelled`, `cancel:"honoured"`. **(b)** Same flow with SIGTERM to the submit process. For both: the live `reader_lease` gid == G1 with the same `snapshot_id`; the `source-snapshots/` listing equals the pre-job listing (installed objects removed); no farm for the v2 gid; no `.graph.9.db.tmp.*`. **Controls.** The same job without a cancel publishes G2 ≠ G1. A cancel at a `post_replace` block → completed, `cancel:"too_late"`, live == G2. |
| 004 | test_leased_generation_retained | **Setup.** Jobs on v1, v2, v3 → farms G1, G2, G3 (current), policy `keep_last=1`. **Reader** (in-process, and a second variant as a separate process via `jobs acquire --hold`) acquires G1: `origin "retained"`, `flock SH granted`. **GC:** `retained ∋ (G1, leased)`, `(G3, current)`; `retired ∋ (G2, outside_window)` (positive control). The held lease then `read_source` returns bytes whose sha == the manifest sha, and the db gid == G1. **Release** → a second GC retires G1. |
| 005 | test_expired_reference_visible | After AC-004's release-and-GC (G1 unleased and retired): `acquire(G1)` raises a ToolError with `code == "EXPIRED_REFERENCE"`, `retry_hint == "retry_current"` and the fixed message. CLI `jobs acquire --generation G1` → exit 3 with that error envelope. `protocol.resources.error_data("EXPIRED_REFERENCE").code == -32002` (existing mapping). **Control.** Before GC, `acquire(G1)` succeeds. A malformed id → `INVALID_REFERENCE`. |
| 006 | test_gc_replay | **Setup.** 4 farms, one leased, one interrupted job holding a checkpointed cache key, `max_cache_bytes` set, `now=N`. GC1 retires ≥ 1 farm and prunes ≥ 1 cache entry (non-vacuous). GC2 at the same `now`: `retired == []`, `pruned == 0`, all `swept` 0, `recovered == 0`, `retained_digest` equal; `listing(atlas_dir)` minus {`gc-receipt.json`, `gc.lock`, `.index.lock`} byte-, mtime- and inode-identical; OpSpy records 0 link/rename/unlink/rmdir/mkdir. **Crash variant.** A `python -c` GC killed at `gc_post_rename` then rerun → digest == an uninterrupted twin's digest, and `retired/` is empty. **Control.** Releasing the lease changes the digest. |
| 007 | test_current_generation_retained | **Setup.** G2 is current with no lease and no job; policy `keep_last=1, max_age_seconds=0, max_bytes=0`. GC → `(G2, current)` retained; `acquire(G2)` gives `origin "retained"`, readable, gid == G2; the live `reader_lease` gid == G2. **Variant.** G3 published by plain `atlas-aci index` (never captured) → GC captures it and retains it as current; it is acquirable. **Control.** The older G1 under the same policy is retired. |
| 008 | test_lease_gc_race[order] | Hooks plus FlockSpy Events (10 s bounds), on G1 outside the window. **(a) reader_first:** the reader blocks at `post_lock` → GC → `(G1, leased)` → release → readable. **(b) gc_first:** GC blocks at `gc_post_lock_ex` holding EX → the reader sees 3 recorded EWOULDBLOCKs → `EXPIRED_REFERENCE`/`retry_current` → release GC → retired → retry with the current gid is readable. **(c) open_then_retire:** the reader blocks at `pre_lock` holding `L` → GC fully retires → resume → FlockSpy records SH **granted** and then the tombstone check fails → EXPIRED (the post-lock check is load-bearing). **(d) recapture:** a stale `L` after re-capture → inode mismatch → EXPIRED; retry readable. Every outcome ∈ {readable, EXPIRED_REFERENCE with `retry_current`}; no OSError or ValueError escapes. Both completion orders (reader closes first, or GC finishes first) are covered. |
| 009 | test_missing_job_output[case] | **Setup.** Completed job on v1 (receipt G1). Cases: (i) G1 retired by GC → `output_retired`; (ii) a byte flipped in the farm pack → `output_digest_invalid`; (iii) a byte flipped in the farm db → `output_digest_invalid`; (iv) farm manifest deleted → `output_digest_invalid`; (v) farm meta `db_sha256` rewritten → `output_digest_invalid`. Each: `reused:false` with that reason, `attempt == 2`, `run_job` called once, the new receipt's gid == G1 (content address), and the new farm acquires and verifies. **Control.** Untampered → `reused:true`. |
| RUN-01 | gate | SHA-256 manifest of the declared tracked files is equal before and after a clean-worktree re-entry; `git diff --exit-code <output_commit>` on those paths. |
| RUN-02 | gate | `slice_id` + `criteria_sha256` equal in the interrupted and resumed receipts; one receipt `.spectra/state/aci-s41.execution.json`. |

### Supporting tests
- **`test_s41_jobs.py`:**
  - Identity: canonical and repeatable; `since` absent; a changed file, lang, framework, rails map or root each change the id; equal across PYTHONHASHSEED 0/4242.
  - The source tree equals the build's `source_hashes`.
  - `source_changed` (a file edited at a `staged` block → failed, nothing published).
  - BUSY: N=1, job A blocked at `staged`, job B → exit 75, nothing written; B succeeds after A. A same-identity submit → `JOB_RUNNING`.
  - The cancel outcome table; resume of completed or cancelled → `JOB_NOT_RESUMABLE`; `recovered` reconciliation.
  - **Crash matrix:** every `JOB_SEAMS` × `kill` on v1→v2. After each: (1) the live `reader_lease` succeeds with gid ∈ {G1, G2}; (2) `jobs resume` → completed, gid == G2 (twin), receipt verifies; (3) `jobs gc` → no sweep-pattern leftovers, and the `source-snapshots` manifests == `catalog.json` ∪ their packs; (4) every farm verifies.
  - `raise` and `block` actions; an invalid fault spec → exit 2.
- **`test_s41_gc.py`:**
  - The policy schema table (every `POLICY_REASONS` value with its field); window, age and bytes trimming with injected `now`; hard roots never trimmed; `job_base` root while `run.lock` is held.
  - The sweep list (each pattern planted and swept only under the lock; never `source-snapshots`); invalid-meta farm retired; cache prune protected keys.
  - The GC seam crash matrix (`python -c` with hooks killing); `GC_BUSY`; `WRITER_BUSY` (lock held by a blocked build).
  - **Live-store invariance:** a held live `reader_lease` during GC still reads; the sha and inode of `graph.9.db` and the `source-snapshots` listing are unchanged; OpSpy shows no unlink, rename or replace under the live store, with the positive control that it does show them under `generations/retired`.
  - The S08/S21 retain counts after jobs plus GC still hold.
- **`test_s41_leases.py`:**
  - Cross-process: a child process holds SH via `jobs acquire --hold` (READY line) → GC in the test process retains; EOF → exit → GC retires; SIGKILL of the holder → the lock drops.
  - **Read-only acquire:** OpSpy records 0 write-flag opens and 0 link/rename/unlink/mkdir/utime; every sqlite URI contains `mode=ro`; the listing (bytes, mtime, ctime, inode) is unchanged. Control: OpSpy sees writes during capture.
  - Live-path fallback.
  - **L-PARITY:** `verify_generation` on the current farm == `reader_lease` (gid, sid). Corrupting the db or pack fails both. A farm still verifies after the live `retain` unlinked its manifest and pack.
  - `probe_leased`; the status count; a reader backoff absorbing a probe.
  - `CROSS_DEVICE` via `os.link` raising EXDEV, plus the real tmpfs case when Day-1 (c) found it possible.
- **`test_s41_isolation.py`:**
  - **Fingerprint tripwire:**
    - `CodeGraph(tmp, read_only=True)._producer_inputs()["modules"]` keys == the exact 8-name tuple, and none of the 5 new paths is in it;
    - the shas of `codegraph.py` (== S40 FROZEN), the 4 hashed modules and the 2 `__init__` files == Day-1 base constants;
    - every S40 FROZEN sha; pyproject and uv.lock.
  - G-MCP in a fresh interpreter importing the 5 new modules first: tools sha/bytes, budgets, relations, graph-output sha.
  - **Layout pins:** plain `index` creates only {graph.9.db, source-snapshots, extraction-cache, .index.lock}; `jobs status`/`acquire` create nothing.
  - Lazy import: `import atlas_aci.__main__` loads none of the 5 modules.
  - CLI: the `jobs` subcommand set and params; `serve`/`serve-shared`/`index` params unchanged; `__main__.py` text has `@cli.group("jobs")` once; U1-U6.
  - The spawn-site set is unchanged (via `test_s40_injection.spawn_sites()`).
  - AST: the new modules import only stdlib plus `atlas_aci.*`; no "alter table", `nx.compose(`, print, eval or exec.
  - Vocabulary tuples are closed and unique.
  - **Docs:** ASCII, no BANNED words; names every format, state, reason, exit code, seam and limitation. The docs/operations set; the CHANGELOG (`bullets[-1]` still S40, S41 directly before, `(ACI-S41)` once); the README row after the S40 row; `test_s41_*.py` set equality.

### Gates (dev image `atlas-aci-dev-relock`, `uv run --frozen`; logs and exit files under `.spectra/evidence/aci-s41/`)
- **G-S41:** `pytest tests/slices/test_s41_contract.py -q`, then `pytest tests/slices/test_s41_*.py -q`.
- **G-REG** (after the docs edits): `tests/slices/test_s08_*.py tests/slices/test_s09_*.py tests/slices/test_s12_storage.py tests/slices/test_s13_*.py tests/slices/test_s14_capabilities.py tests/slices/test_s21_lifecycle.py tests/slices/test_s3[3-9]_isolation.py tests/slices/test_s40_isolation.py tests/slices/test_s40_injection.py tests/slices/test_s38_*.py tests/test_schema_epoch.py tests/test_thesis_negatives.py tests/test_export.py`.
- **G-LINT exactly as CI** (ci.yml:117-139): `ruff check src/ tests/`; `ruff format --check src/ tests/`; `mypy src/`; `ruff check ../scripts/ --exclude fingerprint-fixture`; `ruff format --check ../scripts/ --exclude fingerprint-fixture`; `mypy ../scripts/`.
- **G-FULL:** `pytest -q` with S40's recorded deselects; no new failure or skip vs `base-invariants.json`.
- **G-SRC:**
  - `git diff --name-status fa16b1c -- mcp-server/src` == {A storage/generations.py, A storage/leases.py, A storage/retention.py, A indexing/jobs.py, A indexing/job_runner.py, M __main__.py}; `__main__.py` numstat deletions == 0.
  - `git diff --exit-code fa16b1c --` on `codegraph.py`, `storage/{__init__,evidence,snapshots}.py`, `indexing/{__init__,extraction_cache,retrieval}.py`, every other fingerprinted module, `server.py`, `config.py`, `enforcement.py`, `protocol/`, `query/`, `tools/`, `mcp-server/pyproject.toml`, `mcp-server/uv.lock`, `mcp-server/Dockerfile`, `.github/workflows`, `scripts`, `atlas`, `hosts`.
  - Tests == A `test_s41_*.py` plus M `test_s40_isolation.py` (only :471-473 changed; numstat ≤ 4/3).
  - Docs == A `docs/operations/index-jobs-and-retention.md`. Root == M CHANGELOG.md, M README.md with 0 deletions.
- **G-FINGERPRINT:** in a `git worktree` at fa16b1c and on the branch, the same fixture repo gives `_producer_inputs()` canonical bytes equal; a build gives an equal `generation_id` and `snapshot_id`; `test_s38_contract.py` is green (goldens unchanged).
- **G-MCP:** `atlas-aci tools` sha `94f9567a…` / 1631472 bytes.
- **G-HARDEN:** `bash scripts/harden-gate-classify.sh fa16b1c HEAD` prints `augmented=false a3_touched=false`.
- **G-PY314:** `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s41_*.py tests/slices/test_s40_isolation.py -q'`. Container-local flock; passed counts; no skips.
- **G-DRIFT:** `ramza-drift` with the declared scope.
- **G-PROD** (`.spectra/evidence/aci-s41/production-scenario.sh IMG`, POSIX sh, `set -eu`):
  - **Containers.** Every container is `--rm`, `--network none`, named `s41-$$-N`, with an explicit `--entrypoint`, as user `atlas`. Named volumes `s41-$$-{repo,graphs}` (no macOS bind mount for locks). `trap` removes containers and volumes on EXIT/INT/TERM. A host watchdog `bounded SECS NAME …` does `docker kill` → FAIL 124. Init: one root container chowns the volumes.
  - **P0:** image id; inside the image, the sha256 of the 6 changed and every frozen src file equals the host values.
  - **P1:** `/usr/local/bin/python -I -c` writes v1 into `/repo`.
  - **P2:** `atlas-aci jobs submit --repo /repo --graph-root /graphs` → completed (G1); repeat → `reused:true`.
  - **P3:** v2; `ATLAS_ACI_JOB_FAULTS` kill at checkpoint 2 → exit 1 `JOB_INTERRUPTED`; `jobs resume` → `resumed_from 2`, `parser_invocations 3` (G2). A second submit blocked at `staged`: SIGTERM → exit 4; `jobs status` current == G2.
  - **P4 (two containers):**
    - Container A: `jobs acquire --generation G1 --hold`, with `:ro` graphs and stdin from a host fifo; poll for the READY line.
    - Container B: write `retention.json` (`keep_last 1`); `jobs gc --now T` → `(G1, leased)` retained, and ≥ 1 retired (non-vacuous).
    - Close the fifo → A exits 0 → `jobs gc --now T` → G1 retired; `jobs acquire --generation G1` → exit 3 `EXPIRED_REFERENCE`.
    - `jobs gc --now T` again → `retired []` and an equal `retained_digest`.
  - **P5:** `jobs acquire --generation <current>` → exit 0 after GC. A `:ro` container acquire leaves the graphs manifest (bytes and mtimes, computed in-container) unchanged.
  - **P6:** a read-only `atlas-aci serve` (`:ro` mounts, `--read-only`) driven by S38 `RawLineClient` from `production_main`: identical `search_symbol` results before, during (concurrent GC container) and after GC; no errors.
  - **P7:** `IMG tools` sha and bytes.
  - **Output.** `production-scenario.json`; exit 0 only if every check passed, each with a positive control.

### Work plan (timebox 5 engineering days)
**Day 1 AM: qualification**, before any src edit. Outputs `qualification.json` and `base-invariants.json`; spikes live in scratch.
- **(a)** `git diff --quiet fa16b1c -- mcp-server/src`. Record:
  - the shas of the 4 hashed modules, the 2 `__init__` files, `codegraph.py` and the S40 FROZEN files;
  - the tools sha and budgets;
  - the base full suite (run in the background).
- **(b)** flock, in the dev image and on python:3.14 (container-local tmp):
  - SH+SH across 2 processes; SH vs EX NB EWOULDBLOCK in both directions;
  - two threads with separate `open()`s conflict;
  - flock on an O_RDONLY fd; the lock survives a rename of its parent directory;
  - SIGKILL drops it;
  - the same across 2 containers on a named volume, one mounted `:ro`.
- **(c)** `os.link` of `graph.9.db`, the manifest and the pack on the same filesystem; an EXDEV probe with tmpfs at `generations/`.
- **(d)** In a `python -c` process: rebinding `xc.ExtractionCache` counts writes == `parser_invocations` on a cold 5-file build; a warm build has 0 writes; killing after write 2 → rerun `parser_invocations == 3`.
- **(e)** `_JobGraph` overrides fire in the order `_publication_base` → `_run_build` → `_close_db` → `_retire_previous_native`, inside the writer lock. A raise in `_close_db` leaves live unchanged and the installed objects removed. `capture_current(locked=True)` inside `_retire_previous_native` does not deadlock.
- **(f)** `snap.acquire(farm, …)` works; L-PARITY holds; a farm survives the live `retain`; the identity tree digest == `generation_inputs.source_hashes`.
- **(g)** With scratch files added under `storage/` and `indexing/`, `_producer_inputs()` is unchanged.
- **(h)** Green at base: test_s08_integration, test_s21_lifecycle, test_s12_storage, test_schema_epoch, test_s09_cache_validation, test_s40_injection.

**STOP RULE** (return to the orchestrator; never adapt silently):
- (b) any SH/EX semantic differs, threads do not conflict, or flock fails on a `:ro` volume in Linux;
- (c) a same-filesystem link fails;
- (d) the wrapper does not intercept (that would need a `codegraph.py` edit);
- (e) the order differs, or a deadlock;
- (f) farm acquire or parity fails;
- (g) the fingerprint changes;
- (h) anything red at base.

**Recorded adaptations, not stops:**
- an EXDEV real case cannot be built → an `os.link` EXDEV injection plus the doc;
- macOS host bind-mount flock unreliable → documented, and tests run only in containers.

**Day 1 PM:** `generations.py` (policy, namespace, writer_lock, capture, verify), `leases.py`, `test_s41_support.py`, and L-PARITY.
**Day 2:** `retention.py`; `test_s41_leases.py`, `test_s41_gc.py`; AC-004/005/006/007/008.
**Day 3:** `jobs.py`, `job_runner.py`, the CLI; AC-001/002/003/009.
**Day 4:** the crash matrices, `test_s41_jobs.py`, `test_s41_isolation.py`, the docs, CHANGELOG, README and the S40 pin line. **Checkpoint with a clock reading:** if any never-cut item is red, return to the orchestrator; do not extend.
**Day 5:** G-REG, G-LINT, G-FULL, G-SRC, G-FINGERPRINT, G-MCP, G-HARDEN, G-PY314, G-PROD, G-DRIFT; RUN-01/02; the receipt.

**Cut order** (each cut becomes a documented limitation):
1. The README row.
2. The status `generations[]` detail (the counts stay).
3. Cache pruning (`max_cache_bytes`; the cache is then documented as not bounded by GC).
4. `max_bytes` (keep_last and max_age stay).
5. SIGTERM cancellation (the marker stays).
6. `--now` on the CLI (the API keeps `now`).
7. G-PROD P4 across containers → two processes in one container.
8. The GC crash matrix reduced to `gc_post_lock_ex`/`gc_post_rename`/`gc_mid_delete`.

**Never cut:**
- the 9 AC tests with positive controls;
- the D7 lease atomicity (the 4 interleavings, tombstone and inode checks, cross-process);
- current as a GC root and live-store invariance;
- fingerprint safety (tripwire, G-SRC, G-FINGERPRINT);
- crash recovery at the publication seams `mid_publish`, `staged`, `pre_replace`, `post_replace`, `captured`, `post_retain`;
- the read-only acquire proof; G-MCP; the frozen shas.

### Independent checker conditions
Verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s41-plan-checker`, independent read-only review; 1 BLOCKER, 9 MAJOR, 8 MINOR). Ramza's complexity verdict was `human_loop`; under the user's autonomous-execution authorisation this review is the substitute human gate. Every finding is accepted as a binding plan condition with the same number; where a condition conflicts with plan text above, the condition wins. Orchestrator confirmation of planning finding F1 stands: the `atlas-aci jobs submit|resume` process is the isolated job process and no spawn site is added (the S40 spawn audit pin holds).

Orchestrator resolutions (stricter option chosen each time):
- Condition 2: run the Day-1 probe; if gids can collide, implement retire-and-recapture on inode mismatch (typed `capture_conflict` when leased). Either way the probe outcome is recorded.
- Condition 5: adopt the recommended state table (interrupted -> continue as resume with `resumed_from`; failed/cancelled -> new attempt with `attempt += 1`; accepted/running -> `JOB_RUNNING` when `run.lock` is held, else treated as interrupted). `JOB_INTERRUPTED` is produced only by `jobs status`/`resume` for a job whose runner died; a test per row.
- Condition 8: `retire_invalid` takes `gc.lock` NB and returns typed `GC_BUSY` on contention (single arbiter, no unlocked deletion); recovery also tolerates ENOENT.
- Condition 13: implement the `max_age` rule for interrupted job directories via the injected `now` (not just document it); identity failures and any `OSError` map to typed errors with nothing created.
- Condition 18: never-cut = every AC, lease atomicity, current-as-root, fingerprint safety, crash recovery at publication seams, conditions 1, 3, 4, 9 and 10, the docs and the S40 pin amendment. Stop and return to the orchestrator at the Day-4 checkpoint if any is red.

**Findings**

1. **BLOCKER. AC-009 tamper cases (ii)/(iii) corrupt the live store.**
   - Farm `graph.9.db`, manifest and pack share inodes with `graph.9.db` and `source-snapshots/` (D2, D6). A byte flip in the farm file, done in place, also flips the live file.
   - The rebuild then fails at `snap.acquire(predecessor)` (codegraph.py:2602), so "new receipt gid == G1" is unsatisfiable.
   - Condition: tamper only by copy, modify, then `os.replace` onto the farm path (break the link first). Each case asserts that the live `reader_lease` and the inode of `graph.9.db` are unchanged after tampering (non-vacuity). The docs must state that bit-rot on a shared inode hits both names.
   - AC-009 case (i) needs an explicit script: G1 non-current via a v2 job and a `keep_last=1` GC, then restore the tree to v1 (identity includes the tree digest) before resubmitting.

2. **MAJOR. `frameworks` and `rails_table_map` are not in the generation inputs.**
   - `_build_inputs` (:2351-2364) carries only languages and skip patterns.
   - Two jobs that differ only in frameworks or the table map may yield the same `generation_id`. D6 step 2 then returns the existing farm ("first wins"), D5 step 7 treats the same gid as "recovered", and D4 reuse binds to the wrong DB content.
   - Condition: Day-1 probe (add as (i), with STOP rule) on a rails_models fixture, with and without a table map, recording whether gid and sid differ. If they collide, `capture_current` must, when `generations/<gid>` exists with an inode different from the live one, retire and recapture under EX|NB (a leased farm gives typed `capture_conflict`), so the farm always equals the build just published. AC-006 idempotence still holds because inode equality returns early. If they never collide, record the probe as evidence.

3. **MAJOR. The crash-matrix assertion (3) is unsatisfiable for kills before `snap.retain`.**
   - `retain` runs after `_retire_previous_native` (:2660-2662). A kill at `post_replace`, `post_retire` or `captured` leaves the new manifest installed but absent from `catalog.json`.
   - Reconcile (D5 step 7) runs no build and GC never touches `source-snapshots/`, so "manifests == catalog ∪ packs" fails.
   - Condition: reconcile calls the public `snap.retain(atlas_dir, staged_sid)` under the writer lock, reproducing the uncrashed on-disk state. State assertion (3) per seam: catalog ids ⊆ manifests, and the live sid's manifest and pack are present.

4. **MAJOR. The kill seam is a no-op when the process is PID 1, and P3's expected result is wrong.**
   - In the production image the entrypoint python is PID 1 unless `--init` is used. A same-namespace `os.kill(getpid(), SIGKILL)` is ignored for init, so G-PROD P3 would complete the job (vacuous).
   - P3 also says "kill → exit 1 `JOB_INTERRUPTED`", but a killed CLI cannot print that.
   - Condition: the `kill` action is `os.kill(getpid(), SIGKILL)` followed immediately by `os._exit(137)`. P3 asserts container exit 137, then `jobs status` shows `interrupted == 1`. Fix the P3 text.
   - A SIGTERM handler on PID 1 is already installed by `run_from_cli`; `jobs acquire --hold` must install one too.

5. **MAJOR. Behaviour of submit on an existing job in each state is unspecified, and `JOB_INTERRUPTED` has no producer.**
   - Cases: interrupted with free `run.lock`, failed, cancelled, accepted.
   - Condition: add a table to D4/D5 with a test per row. Recommended: interrupted → continue as resume (`resumed_from` set); failed or cancelled → new attempt, with `attempt += 1`; and either remove `JOB_INTERRUPTED` or give it a producer (for example resume of a job whose runner is alive).

6. **MAJOR. Reuse of a retained non-current output reports success while the live index stays stale.**
   - Submit with a tree identical to a retained job whose gid is not current returns `reused:true`, exit 0, and `graph.9.db` is not updated.
   - Condition: add `current_generation_id` and `receipt_is_current` to the result envelope. They are computed read-only, so the receipt bytes stay equal to the stored `receipt.json`. Document that exit 0 on reuse does not imply "live index == tree". AC-001 gets a variant with a non-current retained output.

7. **MAJOR. Cancel and SIGTERM are ignored while the job waits for the writer lock.**
   - `build()` blocks on `flock(LOCK_EX)` (:2442), PEP 475 retries after EINTR, and the handler only sets a flag. The job can wait indefinitely and ignores `docker stop` until SIGKILL.
   - Condition: `_JobGraph` overrides `_writer_lock` with the same body but an NB poll plus a cancel check each tick. AC-003 gets a variant: cancel or SIGTERM while another process holds `.index.lock` → exit 4, nothing written.

8. **MAJOR. `retire_invalid` races GC recovery.**
   - D8 step 2 does `rmtree` of `retired/*` under `gc.lock`. `retire_invalid` (D4, D6) renames to `retired/` and deletes without `gc.lock`, so recovery can rmtree a farm being deleted, and `recovered` stops being 0.
   - Condition: `retire_invalid` takes `gc.lock` NB (`GC_BUSY` on contention, typed), or recovery takes EX on each retired farm's `lease.lock` and tolerates ENOENT. A test covers it.

9. **MAJOR. flock and hardlink tests can be vacuous on Docker Desktop bind mounts.**
   - virtiofs may keep locks guest-local or not enforce them, and may give unstable `st_ino`.
   - Condition:
     - `test_s41_support` has a session-scoped fail-not-skip canary on the exact base directory used. It checks that EX|NB fails against a held SH on a second fd, that `os.link` gives `st_nlink == 2` with equal `(st_dev, st_ino)`, and that `st_dev` differs from the repo bind mount.
     - FlockSpy patches `leases.fcntl` and `retention.fcntl` with a proxy, not the global `fcntl.flock`, so barriers apply only to the intended call sites.

10. **MAJOR. The decision-2 reading is accepted only as stated.** The server never takes S41 leases, so GC's only readers are S41 CLI and API users.
    - Condition: the docs, the PR body and the receipt must not claim the server serves retained generations. AC-004/005/008 test names and docstrings say "retained generation via `storage.leases`". G-PROD P6 (live `serve` unchanged across GC) is never-cut.

11. **MINOR. AC-006 observers contradict the write path.**
    - D2 writes every JSON by tmp plus `os.replace`, so GC2 rewrites `gc-receipt.json` and changes the mtime of `generations/`. `Path.mkdir(exist_ok=True)` also calls `os.mkdir`.
    - Condition: OpSpy counts successful ops only and excludes the `generations/gc-receipt.json` tmp/replace. The listing compares files only and excludes `generations/` directory mtimes. Implementation checks existence before `mkdir`.

12. **MINOR. `job_base` and GC liveness.**
    - A running build holds the writer lock for its whole duration, so GC returns `WRITER_BUSY` and `job_base` is unreachable for real builds.
    - Condition: the `job_base` test holds `run.lock` manually. The docs state that GC cannot run during a build, and that capture and cache enumeration happen inside the writer lock.

13. **MINOR. Operability gaps.**
    - Interrupted jobs protect cache keys forever. Add a `max_age` rule on the job dir mtime via the injected `now`, or document it.
    - Identity computation failing on a symlink or unreadable source must produce a typed `JOB_FAILED build_error` with nothing created.
    - Submit reads every source file once, including for reuse; document the cost.
    - Any `OSError` in jobs commands (ENOSPC, EACCES, a read-only mount) maps to `STORE_INVALID` with fixed text. Mid-GC failures leave `retired/` for recovery.

14. **MINOR. Missing direct id-equivalence test.**
    - Add: an `index` of a tree and a job of the same tree on a twin namespace (same repo path) give equal `generation_id` and `snapshot_id`.
    - G-FINGERPRINT needs a positive control: perturbing a hashed-module byte in a scratch copy changes the id.

15. **MINOR. Checkpoint prefix validity.**
    - It must use the same predicate as `ExtractionCache.lookup` (inputs digest == key, checksum). The "corrupt entry 2" variant must corrupt something that predicate sees.
    - `resumed_from == files - parser_invocations` holds only for a cold cache, so assert primarily on `parser_invocations`.
    - Counters are reliable: `cache_stats` (:3149-3158) is returned at :3658, and `parser_invocations` increments before parsing, including failures.
    - Checkpoint cache writes are not inside any try/except, so `JobCancelled` and `InjectedFault` propagate (:3318, :3377).

16. **MINOR. In-process `patched()` is module-global.** Tests must not run a second build or a twin build in the same process while it is active. Twins run in a `python -c` subprocess.

17. **MINOR. Day-1 additions.**
    - (j) `get_parser` works offline in the production image with `--network none`.
    - (k) Rollback smoke: plain `index`, `status`, `export` and `serve` on a store containing `generations/`, `jobs/` and `retention.json`.
    - Extend L-PARITY with a provenance-context mismatch, a retrieval-closure mutation and a foreign repository.
    - Add status-probe backoff to GC EX, slot and `run.lock` acquisition (same 3×20 ms), so a concurrent `jobs status` does not cause a spurious `leased`, `BUSY` or `JOB_RUNNING`.

18. **MINOR. Timebox and cuts.**
    - Never-cut additions: the tamper and decline path (finding 1), the PID-1 fallback (4), the docs and the S40 pin amendment, the finding-3 reconcile `retain`, and the finding-9 canary.
    - Cuts 3 and 4 (cache prune, `max_bytes`) must pre-declare their test deltas (AC-006 non-vacuity and the policy table) so a cut does not edit a never-cut test.
    - The Day-4 checkpoint stands.
    - Rollback is safe: base commands ignore `generations/`, `jobs/` and `retention.json`. `_sweep_stale_epoch_files` globs only top-level `graph.*.db`, and `.atlas` is in the skip patterns. Hardlinked inodes persist until an operator deletes `generations/`; document that.

## Stories
As an operator sharing one index store, I want index runs to be durable jobs with a stable identity: resumable after a crash, cancellable before publication, and deduplicated when an identical verified result already exists. Then rebuilding never wastes work or corrupts what readers see.

As an operator bounding disk use, I want old generations retired by an explicit policy that can never remove the current generation or one a reader holds, with explicit expiry for retired references. Then storage stays bounded without breaking active readers.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s41.criteria.md` (sha256 `ffa352d6…6a1659f`). AC-S41-001..009 and RUN-01/02 are not restated; the mapping is above.

Reading recorded for the checker (decision 2):
- "Active reader lease" (AC-004/008) means a flock lease taken through `storage.leases.acquire` or `jobs acquire`, or the server's own FD-pinned read of the current generation (current is always a root and its store is never touched by GC).
- "Old resource" (AC-005) means a retained generation id acquired through the S41 API or CLI.

## Confidence
To be computed with `ramza-score`.
- Everything is additive operator-side code around an unchanged producer, so generation ids, goldens and the MCP surface cannot churn.
- flock is the single kernel arbiter for lease-versus-retirement, with a case-complete proof and deterministic barrier tests.
- Main uncertainties, all covered by Day-1:
  - flock behaviour on `:ro` volumes and across containers (b);
  - the cache-wrapper and subclass seams of a frozen `codegraph.py` (d)(e);
  - farm verification parity through private methods (f).

## Rejected Alternatives
- **A writable lease directory with TTL/pid records.** The server would write, which breaks read-only by construction; it needs stale-record cleanup and races on pid reuse.
- **SQLite or fcntl (POSIX) record locks.** They are process-scoped and dropped when ANY fd of the file closes in that process, so the existing code's reopen patterns would silently drop leases.
- **A `/proc/*/fd` scan.** Linux-only, blind across PID namespaces, and racy.
- **Server-side leases now** (edit `server.py`, `query/leases.py` or `codegraph.reader_lease`). It breaks the S39/S40 frozen shas and, for `codegraph.py`, every producer id; this is the follow-up.
- **Reusing `.index.lock` as a shared reader lock.** Readers would starve the indexer.
- **Checkpoint hooks inside `codegraph.py`.** Fingerprint churn across S11-S40 goldens.
- **Copying instead of hardlinking.** Doubles bytes and time; the cross-device follow-up.
- **The DB file sha as identity.** It embeds a random publication token.
- **A spawned child via `subprocess.Popen`.** It breaks the S40 spawn-site audit (F1).
- **Re-publishing a retained output as current.** A new publication path; out of scope.
- **A background GC daemon.** Excluded ("background repository mutation").

## Risks (ranked)
1. **Lease atomicity bug (P0).** One arbiter (flock on the farm inode), name and inode re-checks, the D7 proof, 4 barrier interleavings, cross-process and cross-container tests, Day-1 (b) STOP.
2. **Fingerprint churn.** No edits to hashed files, the tripwire, G-SRC, G-FINGERPRINT, the S38 goldens.
3. **Private coupling to `CodeGraph`** (overrides, module rebinding, F3 methods). `codegraph.py` is frozen in this slice; Day-1 (d)(e)(f), L-PARITY, and the coupling list in the docstrings.
4. **The decision-2 reading not accepted by the checker.** Stated in the plan and docs, proven by a live-reader test and G-PROD P6, with the follow-up named.
5. **Crash windows leaving live-store debris** (installed but unpublished objects, `.catalog.*`). Readers are unaffected; the next publication's `retain` cleans them; the crash matrix asserts convergence.
6. **flock on NFS or Docker Desktop.** Documented as unsupported; tests run container-local.
7. **Hardlinks pin large inodes.** keep_last, max_age and max_bytes bound them, plus status bytes; leases can exceed the bound (documented).
8. **Pin collisions** (CHANGELOG, README, docs set, spawn audit). G-REG after the docs; the only prior-test edit is the S40 docs line.
9. **Timebox.** Cut order and the Day-4 checkpoint.

## Rollback
- One bounded PR with no schema, dependency, lock, producer or `serve` change.
- `git revert <output_commit>` removes 5 modules, the `jobs` group, the tests, the doc and the 2 lines.
- On-disk S41 artefacts (`generations/`, `jobs/`, `retention.json`) are ignored by every base command and can be deleted by hand. The live store is never modified by S41 beyond the base `build()` behaviour.
