# Index jobs, retained generations and garbage collection (S41)

`atlas-aci jobs` runs index builds as durable, resumable, cancellable jobs, keeps older generations
as retained farms, lets a reader hold a retained generation with a cross-process lease, and retires
unleased generations by an explicit policy. It is operator-side tooling around the unchanged index
producer: plain `atlas-aci index`, `status`, `export`, `serve` and `serve-shared`, the tools manifest and the
producer fingerprint do not change, so generation ids and snapshot ids do not change either.

## 1. Summary and what is (not) served

- A job has a stable identity: the same repository, languages, frameworks, table map, producer inputs,
  skip patterns and source tree are the same job. An identical job with a retained output that still
  verifies is reused: no parse, no build, no new generation.
- A build that dies after a durable checkpoint resumes from the last valid checkpoint. A cancel before
  the commit leaves the previously published generation selected.
- A retained generation is a hardlink farm of the published store. A reader takes a shared `flock` on
  the farm (`storage.leases.acquire`, `atlas-aci jobs acquire`). Garbage collection never retires a
  farm that is held, the current generation or a running job's base. A retired reference gives an
  explicit, retryable `EXPIRED_REFERENCE`.
- **What is not served.** The MCP query server (`serve`, `serve-shared`) reads only the current
  generation, `graph.9.db`. It never takes these leases and never reads a farm. Retained generations
  are read through `storage.leases` and `jobs acquire` only; serving a retained generation over MCP is
  a follow-up that needs edits to files the producer fingerprint covers. In-flight server readers are
  protected the way they always were: an open file descriptor survives an unlink. Garbage collection
  never touches the live store (`graph.9.db`, `source-snapshots/`), so a running `serve` is unaffected.
- The job process is the `atlas-aci jobs submit|resume` process itself. The build runs in it; no
  subprocess is spawned, so the audited spawn-site set of the code base is unchanged.

## 2. Layout

Everything lives in the graph namespace `<atlas_dir>` (`<repo>/.atlas`, or
`<graph_root>/repos/<sha256(repo)>`).

```
graph.9.db  source-snapshots/  extraction-cache/  .index.lock    existing; GC never edits the first two
retention.json                                    optional, operator-written (format atlas-aci-retention.v1)
generations/                                      created only by jobs submit, resume and gc
  <generation_id>/graph.9.db                      hardlink of the captured live inode
  <generation_id>/source-snapshots/manifest.<sid>.json  and  pack.<sha>.bin   hardlinks
  <generation_id>/lease.lock                      empty, created before the farm is published
  <generation_id>/meta.json                       atlas-aci-generation-meta.v1
  .stage.<nonce>/   retired/<generation_id>.<nonce>/   gc.lock   gc-receipt.json
jobs/slots/<i>.lock                               i in 0..max_concurrent_jobs-1
jobs/<job_id>/{job.json, checkpoint.log, checkpoint.json, receipt.json, cancel, run.lock, rails-table-map.json}
```

- Directories take the process umask, exactly as the existing store does, and the JSON files and
  `lease.lock` are mode 0644, so a reader running as another user can acquire a lease (a lease needs
  read access only; an acquire works on a read-only mount).
- Plain `index`, `status`, `export`, `import` and `serve` create nothing new and ignore `generations/`,
  `jobs/` and `retention.json`. `jobs status` and `jobs acquire` create nothing.
- A farm shares inodes with the live store: it copies no bytes. Bit-rot or an in-place edit on a shared
  inode hits both names, so the farm is not a backup of a damaged live file. Tests (and operators) change a
  farm file only by copying, modifying and `os.replace`-ing it, which breaks the link first.
- Hardlinked inodes stay on disk until `generations/` is deleted by hand. To roll back, revert the
  change and delete `generations/`, `jobs/` and `retention.json`; every base command ignores them.
- Same filesystem only. A cross-device farm is refused with `CROSS_DEVICE` (and an `EXDEV` from
  `os.link` is mapped to the same code). Copy-based retention on a separate volume is a follow-up.

## 3. Formats

Every JSON document is canonical (sorted keys, no spaces) plus a newline, written to a private temporary
name in the same directory, fsynced, renamed into place, and the directory is fsynced. Reads are strict:
closed key sets, no duplicate keys, no NaN or Infinity, booleans are never accepted as integers, and
size caps (64 KiB for meta and policy, 1 MiB for the rest).

| Format | Where | Content |
| --- | --- | --- |
| `atlas-aci-generation-meta.v1` | `meta.json` | generation_id, snapshot_id, kind, repository, namespace, seq, captured_at, db_sha256, db_size, pack_sha256, pack_size. `db_sha256` is an integrity check of the captured file, never an identity |
| `atlas-aci-retention.v1` | `retention.json` | keep_last 1..64 (default 2), max_age_seconds 0..31536000 or null, max_bytes or null, max_cache_bytes or null, max_concurrent_jobs 1..16 (default 1), checkpoint_summary_interval 1..4096 (default 64). Only `format` is required |
| `atlas-aci-index-job-identity.v1` | inside `job.json` | repository, namespace, languages, frameworks, rails_table_map_sha256, schema, producer, skip_patterns, source_tree_sha256, source_files |
| `atlas-aci-index-job.v1` | `job.json` | job_id, identity, graph_root, state, attempt, base_generation_id, staged_generation_id, published_generation_id, snapshot_id, failure_reason, cancel, resumed_from, last_decline. No timestamps and no pid |
| `atlas-aci-job-checkpoint.v1` | `checkpoint.json` | a constant-size summary: job_id, attempt, count, chain |
| `atlas-aci-index-job-receipt.v1` | `receipt.json` | job_id, attempt, outcome (`built` or `recovered`), generation_id, snapshot_id, base_generation_id, resumed_from, extraction_cache, files_indexed, cancel (`none` or `too_late`). No time fields |
| `atlas-aci-job-result.v1` | stdout of submit and resume | job_id, reused, decline_reason, receipt, current_generation_id, receipt_is_current |
| `atlas-aci-job-cancel.v1` | stdout of cancel | job_id, outcome |
| `atlas-aci-lease.v1` | stdout of acquire | generation_id, snapshot_id, origin (`retained` or `live`) |
| `atlas-aci-gc-receipt.v1` | `generations/gc-receipt.json` and stdout of gc | now, policy, current_generation_id, retained, retired, recovered, swept, cache, retained_set, retained_digest |
| `atlas-aci-jobs-status.v1` | stdout of status | see section 9 |
| `atlas-aci-job-error.v1` | stdout of a failure | code, retry_hint, and optionally reason, job_id, generation_id |

`checkpoint.log` holds one fsynced canonical line per durable cache write:
`{"n","path","source_sha256","key","state","chain"}` with `state` of `complete` or `failed` and
`chain = sha256(previous_chain || canonical(line without chain))`, starting from
`sha256("atlas-aci-job-checkpoint.v1\0" + job_id)`. A torn tail is ignored.

## 4. Job lifecycle, states and identity

- **Identity.** `job_id = sha256(canonical(identity))` over the object above. It has no `since` marker and
  no time, and never the database file hash (that file carries a random publication token). The tree
  digest covers every recognised source file in sorted order. Computing it reads every source file once,
  also for a reuse check: that is the cost of a content-addressed identity.
- **States.** `accepted`, `running`, `staged`, `published`, `captured` (non-terminal) and `completed`,
  `cancelled`, `failed` (terminal). A non-terminal job whose `run.lock` is held is running; with a free
  `run.lock` it is interrupted (the runner died). `failure_reason` is one of `build_error`,
  `source_changed`, `capture_failed` and `injected_fault`.
- **Submit and resume on an existing job.**

| Existing job | `jobs submit` | `jobs resume --job` |
| --- | --- | --- |
| completed, output verifies | reuse (`reused: true`) | `JOB_NOT_RESUMABLE` |
| completed, output declined | new attempt, `attempt += 1` | `JOB_NOT_RESUMABLE` |
| failed or cancelled | new attempt, `attempt += 1`, fresh checkpoint log | `JOB_NOT_RESUMABLE` |
| non-terminal, `run.lock` held | `JOB_RUNNING` (exit 75) | `JOB_RUNNING` |
| non-terminal, `run.lock` free (interrupted) | continue like a resume, `resumed_from` set | continue, `resumed_from` set |
| unknown id | new job | `JOB_UNKNOWN` |

  `JOB_INTERRUPTED` is the code `jobs status` attaches to every job whose runner died (`interrupted_jobs`);
  a resume or a submit of such a job continues it. A killed process cannot print an error itself.
- **Slots.** `max_concurrent_jobs` slot files are taken with a non-blocking `flock`; when every slot is held
  the answer is `BUSY` (exit 75) and nothing is written. One runner per job is enforced by `run.lock`.
  The writer lock (`.index.lock`) serialises publication.
- **Reuse.** The receipt's generation must pass a full lease acquisition on its farm, then the farm's
  source-hash digest, producer, repository and languages must equal the identity. A decline is recorded
  in `last_decline` with one of `no_receipt`, `output_retired`, `output_raced`, `output_digest_invalid`
  or `identity_mismatch`, and the job re-runs. For `output_digest_invalid` the farm is first retired
  (a leased damaged farm fails the job with `capture_failed`). Reuse exit 0 does not imply that the live
  index equals the tree: read `receipt_is_current` and `current_generation_id` in the result, which are
  computed read-only while the receipt bytes stay equal to the stored `receipt.json`. Re-publishing a
  retained non-current output as the current generation is out of scope.
- **Reconcile.** If an earlier attempt died after the commit (its staged generation is the live one), the
  next attempt takes the writer lock, repeats the catalog update (`snap.retain`) and the capture, and
  completes with outcome `recovered`.
- **Failure typing.** An unreadable or linked source gives `JOB_FAILED` with reason `build_error` and
  creates nothing. Any `OSError` (a full disk, a permission error, a read-only mount) gives
  `STORE_INVALID` with fixed text.

## 5. Checkpoints and resume

Checkpoints are the content-addressed extraction-cache entries; the log records which were written. A
resume re-runs the build and the cache hits prove the continuation: `parser_invocations` equals the
files minus the valid prefix for a cold cache. The valid prefix is the longest prefix of log lines that
parse, chain, name a source whose hash matches, carry the key of the current build inputs and whose
cache entry passes the same predicate `ExtractionCache.lookup` applies. The log is truncated to it. A
resume costs one pass over the checkpoint log plus one source read per valid line.

## 6. Cancellation

A cancel is a `cancel` marker (`atlas-aci jobs cancel`) or SIGTERM to the job process. It is checked while
waiting for the writer lock (a poll with a cancel check each tick), after every durable checkpoint, after
staging and immediately before the commit (`os.replace`). Before the commit the job ends `cancelled`
(`cancel: honoured`, exit 4 `JOB_CANCELLED`), the staged files and installed snapshot objects are
removed and the previous generation stays selected. After the commit no check raises: the job completes
with `cancel: too_late`. Cancel outcomes: `requested`, `cancelled` (the runner was dead),
`too_late`, `not_running`. A cancelled build writes `index-attempt.v1.json` like any failed build; that
diagnostic is not asserted absent.
SIGINT is not a cancel: it interrupts the process like any other death, and the job stays resumable
(the production image's stop signal is SIGINT; send SIGTERM or use `jobs cancel` to cancel).

## 7. The lease protocol

A reader (`acquire`) opens the farm directory and `lease.lock` read-only without following links, takes
`flock(LOCK_SH|LOCK_NB)` (3 attempts, 20 ms apart, which absorbs a status probe), then checks that the
farm name still resolves to the directory it opened and that `lease.lock` is still the opened file, then
verifies the farm (repeating the validation of `CodeGraph.reader_lease` on the farm) and returns a lease
holding the lock. Nothing is created, linked, renamed, unlinked or touched. A process that dies drops its
lease with its descriptors.

Garbage collection takes `flock(LOCK_EX|LOCK_NB)` on the same inode before it renames a farm. Argument:
the only path to retirement is "hold EX on the inode of `<id>/lease.lock`, rename, delete, release", and
a reader is valid only if SH was granted on that inode and the name checks pass after the grant. SH and EX
on one inode exclude each other across processes and across descriptors. (a) If SH is granted first, GC's
non-blocking EX fails and the farm is retained. (b) If GC holds EX, the reader's attempts fail and it gets
`EXPIRED_REFERENCE`. (c) If GC renamed and released before the grant, the name check after the grant sees a
missing or different directory. (d) A later re-capture of the same id creates a new directory and a new
lock file, so a stale descriptor fails the inode check. A lost race is an explicit, retryable expiry:
`EXPIRED_REFERENCE` with `retry_hint: retry_current` and the fixed text "Generation reference expired or
retired."; a malformed id is `INVALID_REFERENCE`; a held farm that fails verification is
`SNAPSHOT_UNAVAILABLE`. When no farm exists, `acquire` serves the live generation if it is the requested
id (`origin: live`, pinned by descriptors like the server), else expires.

Limits. The filesystem must enforce `flock`: local Linux filesystems, overlayfs, named Docker volumes and
read-only bind mounts qualify. NFS (flock emulation varies) and Docker Desktop host bind mounts (virtiofs,
osxfs) are not supported; run flock and hardlink tests and operations on container-local storage. The
test suite starts with a canary on the exact directory it uses and fails, never skips, when that
directory does not behave.

## 8. Garbage collection

`jobs gc` (`storage.retention.gc`) is one pass:

1. Take `gc.lock` (non-blocking; `GC_BUSY`). Delete every `retired/*` left by a crashed pass (a vanished
   entry is tolerated).
2. Under the writer lock (`WRITER_BUSY` after `--wait-seconds`, default 30): capture the live generation
   if it has no farm (this covers plain `index` publishers), sweep orphan staging, compute the roots and
   candidates, take `LOCK_EX|LOCK_NB` on each candidate and rename it to `retired/` while holding it, then
   prune the cache. A running build holds the writer lock for its whole duration, so GC returns
   `WRITER_BUSY` during builds: the capture, the sweep and the cache enumeration all happen inside the
   writer lock.
3. Release the writer lock, delete the retired farms, write `gc-receipt.json`.

- **Roots** (never retired): `current`; farms held by a reader (`leased`); the base of a job whose
  `run.lock` is held (`job_base`); the newest `keep_last` farms by capture sequence (`window`), minus
  farms older than `max_age_seconds` (strictly) and trimmed oldest-first while the bytes of roots plus
  window (distinct inodes) exceed `max_bytes`. Current and job bases are never trimmed. Reasons for
  retirement: `outside_window`, `max_age`, `max_bytes`, `invalid_meta`. Retain reasons: `current`,
  `leased`, `job_base`, `window`.
- **Time** enters only through the injected `now` (`--now`, default the clock); the same farms, leases,
  jobs, `now` and policy give the same decisions.
- **Sweep** (writer lock only): `.graph.9.db.tmp.*` and its journal, `.sources-stage.*`,
  `.index-attempt.*`, `extraction-cache/v1/??/.entry-*`, `generations/.stage.*` and `jobs/<id>/.tmp.*`
  of jobs whose `run.lock` is free (`swept`: graph_stage, sources_stage, attempt_temp, cache_temp,
  capture_stage, job_temp). Never `graph.*.db`, `source-snapshots/**`, `catalog.json` or
  `index-attempt.v1.json`. The `.catalog.*` temporary and the objects installed by a killed build but
  never published are cleaned by the next publication's `retain`, not by GC.
- **Cache prune** (`max_cache_bytes` set): unprotected `extraction-cache/v1/??/<key>.json` entries go in
  `(mtime, key)` order until the total fits. Protected are the keys in the checkpoint log of every
  non-terminal job, except that an interrupted job whose `job.json` is older than `max_age_seconds`
  (against the injected `now`) no longer protects its keys.
- **Idempotence.** A second pass over the same reachable set retires nothing, sweeps nothing, prunes
  nothing and writes an equal `retained_digest` (the sha256 of the canonical retained set: farms with
  their snapshot, db and pack digests, and the cache keys digest).
- **Crash seams.** A crash at any step leaves either an intact farm (a lock dies with its process) or a
  renamed one under `retired/`, which the next pass removes. A damaged farm that a capture finds with a
  different inode than the live database for the same generation id (equal content can be rebuilt into
  a new inode) is retired and recaptured, or `capture_conflict` when a reader holds it.

## 9. Status

`jobs status` is read-only and prints `atlas-aci-jobs-status.v1`: `namespace`, `current_generation_id`,
`jobs` (running, interrupted, completed, cancelled, failed), `phases` (accepted, running, staged,
published, captured), `running_jobs` and `interrupted_jobs` (at most 64 each; the latter carry
`JOB_INTERRUPTED`), `slots` (capacity, busy), `retained` (generations, bytes over distinct inodes,
leased), `generations` (at most 256 rows: generation_id, seq, snapshot_id, bytes, leased, current) with
`generations_truncated`, `last_gc` (retained_digest, retired, now) and the effective `policy`. The leased
probe takes a brief exclusive lock, so a concurrent acquisition may retry once or twice.

## 10. CLI and exit codes

```
atlas-aci jobs submit  --repo R [--graph-root G] [--langs L] [--frameworks F] [--rails-table-map P] [--now N]
atlas-aci jobs resume  --repo R [--graph-root G] --job ID [--now N]
atlas-aci jobs cancel  --repo R [--graph-root G] --job ID
atlas-aci jobs status  --repo R [--graph-root G]
atlas-aci jobs gc      --repo R [--graph-root G] [--now N] [--wait-seconds S]
atlas-aci jobs acquire --repo R [--graph-root G] --generation ID [--hold]
```

`acquire --hold` prints the lease, then holds it until stdin reaches EOF or SIGTERM (it installs a SIGTERM
handler, so it also stops as PID 1 of a container). Stdout carries exactly one canonical JSON line (the
result, status, receipt, outcome, lease or error envelope); stderr carries the fixed text
`jobs: <CODE>`. Neither contains an exception text, a path or file content.

| Exit | Meaning |
| --- | --- |
| 0 | completed, reused, status, gc, acquire or a cancel outcome |
| 1 | `JOB_UNKNOWN`, `JOB_NOT_RESUMABLE`, `JOB_FAILED`, `JOB_INTERRUPTED`, `CROSS_DEVICE`, `STORE_INVALID`, `INDEX_UNAVAILABLE`, `SNAPSHOT_UNAVAILABLE` |
| 2 | usage: click errors, `--job: must be a 64-character lowercase hex job id`, `--generation: must be a 64-character lowercase hex generation id`, invalid frameworks or table map (the `index` texts), `retention.json: invalid retention policy (<reason> at <field>)`, `invalid fault specification`. Nothing is created |
| 3 | `EXPIRED_REFERENCE`, `INVALID_REFERENCE` |
| 4 | `JOB_CANCELLED` |
| 75 | `BUSY`, `JOB_RUNNING`, `GC_BUSY`, `WRITER_BUSY` |

Policy reasons: `unreadable`, `too_large`, `not_json`, `duplicate_key`, `unknown_field`, `wrong_type`,
`out_of_range`, `bad_format`, each with a JSON-pointer field.

**Fault seams** (tests and drills). `ATLAS_ACI_JOB_FAULTS` is a JSON object
`{seam: {"action": "kill"|"block"|"raise", "at": n}}`, read only by the `jobs submit|resume` entry. Job
seams: `runner_start`, `predecessor_captured`, `checkpoint`, `mid_publish`, `staged`, `pre_replace`,
`post_replace`, `post_retire`, `captured`, `post_retain`, `pre_receipt`, `pre_complete`; capture seams
`capture_post_link`, `capture_pre_rename`; lease seams `pre_lock`, `post_lock`; GC seams
`gc_post_recover`, `gc_post_capture`, `gc_post_sweep`, `gc_post_reachability`, `gc_post_lock_ex`,
`gc_post_rename`, `gc_mid_delete`, `gc_pre_receipt`. `kill` sends SIGKILL to the process and then exits
137 (so it also works as PID 1), `block` prints `ATLAS_JOB_SEAM <name>` and waits for a line on stdin,
`raise` fails the job with `injected_fault`.

## 11. Limitations

- No MCP serving of retained generations, and no re-publication of a retained output as current.
- Same device only; hardlinks pin inodes (leases can exceed `max_bytes`) until `generations/` is deleted.
- A farm shares inodes with the live store, so corruption of a shared inode is visible through both names.
- GC and a build exclude each other through the writer lock (`WRITER_BUSY`).
- flock must be enforced by the filesystem (no NFS, no Docker Desktop host bind mounts).
- The `.catalog.*` temporary and unpublished snapshot objects of a killed build are cleaned by the next
  publication, not by GC; a cancelled or failed build writes `index-attempt.v1.json`.
- SIGTERM and the `cancel` marker are checked at the points above; a long single-file parse delays them.
- Resume cost and the identity computation read source files (section 4 and section 5).
