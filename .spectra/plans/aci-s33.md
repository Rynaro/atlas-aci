---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T00:00:00Z
slice_id: ACI-S33
issue: https://github.com/Rynaro/atlas-aci/issues/54
---
# ACI-S33 - bounded git co-change correlations with commit evidence

## Scope

This is issue #54 on base `890e181` (main after PR #98; S01-S30, S31, S32 and S37 merged), branch `aci-s33`.
- The marker `<!-- atlas-aci:slice:ACI-S33 -->` is verified.
- The criteria bytes hash to `7cf8c8efa698489f77fef050eabf544f6bfb6ce405a92cda9aeaf7e81159a521` (7 product ACs plus RUN-01/02).
- Risk P2. Timebox 2 engineering days.
- Rightsize computed tier `lite` (score 4). An independent checker still runs.

**Dependency S19 (#40) is present.**
- `M/query/diff.py` (`M/` = `mcp-server/src/atlas_aci/`) has `Side`:73, `_open`:192 and `_lease`:201.
- `runtime.open_active` is at `M/overlays/runtime.py:227-242`.
- Only the S19 lease is reused.

Observed anchors (re-verified at planning):
- **Snapshots record no git commit.**
  - `storage/snapshots.py:38-64` `identity()` returns `{kind:"git", root, worktree_root, git_dir, common_dir}`. Each path is `(repo / rev-parse output).resolve()`. Otherwise it returns `{kind:"git_unavailable"|"non_git", root}`.
  - On any git failure it silently degrades to `git_unavailable` (`:50-53`, `:63-64`).
- **Source observations.**
  - `runtime.OBSERVATIONS_SQL` (`:78`) gives `manifest.source_observations`, a list of records `{type:"observation", path, state, language, observed_source_sha256}`.
  - Observation states are `indexed|disabled|unsupported|failed` (`codegraph.py:1741`).
  - `reader._source_lease.read(path, sha)` (`snapshots.py:658-678`) checks membership and the sha256, and returns the bytes.
- **Lease.** `open_active` accepts epochs `SCHEMA_EPOCHS=(8,9)` (`runtime.py:39`). Its rejections are `index_unavailable`, `source_snapshot_unavailable` and `index_upgrade_required`. `DiffUsageError` passes through.
- **Output.**
  - `runtime.resolve_output(output, protected, inputs)` (`:866-889`) raises `OverlayUsageError` with `output_not_outside_graph` or `output_equals_input`.
  - `runtime.canonical_bytes` (`:219`) is `ev.canonical` plus LF.
  - `write_atomic` (`:892`) uses prefix `.atlas-runtime-overlay-`, so it is NOT reused.
- **Evidence helpers.** `ev.canonical` (`evidence.py:229`, sorted keys, compact, `ensure_ascii=False`, `allow_nan=False`), `ev.hash_value` (`:259`), `ev.relative_path` (`:263`) and `ev.RELATIONS` (`:45`).
- **Repository-id grammar** (S30): `[a-z0-9][a-z0-9._-]{0,63}` (`cross_repo/revision_vector.py:74`).
- **Existing git use.** The only src call site is `snapshots.py:39-54`, run with the inherited env plus `GIT_OPTIONAL_LOCKS=0` and a 3 s timeout. Test precedents:
  - `GIT_ENV` (`test_s32_counterfactual.py:58`);
  - the refuse/slow PATH shim (`:1035-1046`);
  - `git()` and `build()` (`test_s19_support.py:16-47`).
- **Pins.**
  - Tools JSON 7 tools, `94f9567a…ac56` / 1631472 bytes.
  - Budgets 3436/3448/797/7730.
  - Src-wide scans:
    - `alter table` and LLM imports (`test_thesis_negatives.py:20-51`);
    - `nx.compose(` and the `(subprocess|os\.system)…merge\.<x>\.driver` regex, plus no repo-root `.gitattributes` (`test_export.py:354-379`).
  - No test bans subprocess outside S27/S28/S30/S32 packages.
  - The `cli.commands` checks are inclusive (`test_s21:92`, `test_s22:57`, `test_s30:145`).
  - `__main__.py` must stay lazy (`test_s37:1192`).
- **README and CHANGELOG pins.**
  - README rows are located by `docs/importers/{openapi,opentelemetry,python-dependencies}.md`, `docs/frameworks/rails.md`, `docs/cross-repository-links.md` and `evidence-reports.md`. The file must not contain `canary-missions.md` anywhere (`test_s23:117`, `test_s28:344`).
  - S28 ordering (`test_s28_isolation.py:345`): `python-dependencies.md` must come after `docs/importers/opentelemetry.md`.
  - **The S30 bullet must be the first `- **` bullet under Unreleased/Added** (`test_s30_isolation.py:563-565`).
  - The CHANGELOG titles used as locators by S27, S28, S29 and S30 must not appear in the new bullet.
- **Images.** The dev and production images both use `python:3.12-slim` with apt git (`Dockerfile:55-97`, `USER atlas`; `Dockerfile.dev:32-40`). CI runs Python 3.14 on `ubuntu-latest` with the runner's git.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; stricter option; recorded 2026-11-05)
1. **Architecture.**
   - A standalone, content-addressed `atlas-history-overlay.v1`, written atomically to `--output` only.
   - New package `history/{__init__,cochange}.py`, plus a thin `cochange` command in `__main__.py`.
   - No gated-path edit.
   - Imports allowed: `overlays.runtime.{open_active, OverlayRejected, resolve_output, OverlayUsageError, OBSERVATIONS_SQL, SCHEMA_EPOCHS, canonical_bytes}`, `query.diff.{Side, DiffUsageError}`, `storage.evidence as ev`.
   - Nothing is added to `ev.RELATIONS` or to any MCP surface.
2. **AC-007: the selected source snapshot is mandatory.** Operators select it with `--snapshot-id` and the optional `--graph-root`.
   - It is leased read-only and must be the active snapshot with epoch == max(SCHEMA_EPOCHS).
   - Its identity must be `kind=="git"` with `common_dir` AND `worktree_root` equal to the analysed repository's (worktree_root is added, which is stricter).
   - `--source-commit` must exist, and `end` must equal it or be an ancestor of it.
   - PLUS the blob binding: every indexed observation's bytes must have the same git blob id as the source commit's tree entry at that path.
   - `source_snapshot.binding = "indexed_files_match_commit_tree"`. The fallback `operator_declared` applies only if cut #1 is taken.
3. **Git hardening** per D3. No `safe.directory`. Dubious ownership → `history_unavailable`.
4. **Window.**
   - The window is `start..end`: start excluded, end included, and start must be a proper ancestor of end.
   - **Resolution: `--topo-order` truncation is NOT replay-stable by documentation.** git specifies only the topological constraint, not which commits fall inside a `--max-count` prefix. Tie-breaking depends on the walk implementation, generation numbers and commit-graph presence.
   - Stricter option taken: never truncate. When coverage is `incomplete_history` (any reason), the overlay is still written and declares the state and reasons, but `records == []` and `counts == null` (withheld).
   - `--topo-order` is dropped, because no result depends on order.
5. **Filters** per D5: merges excluded; three typed generated lists; bulk counted on eligible paths after generated paths are removed; gitlinks excluded; deletes kept; no renames; unrepresentable paths counted.
6. **Records** per D7: `a < b`, `support`, sorted `supporting_commits`, `evidence_kind`, and literal per-record provenance. `max_pairs` exceeded → `pair_limit_exceeded`.
7. **Scope amendment.**
   - Includes `history/__init__.py`, `test_s33_{support,rejections,isolation}.py`, a README row placed right after the `docs/cross-repository-links.md` row, and a CHANGELOG bullet.
   - **Correction:** the CHANGELOG bullet goes SECOND, directly after the S30 bullet, because `test_s30_isolation.py:563-565` pins S30 first.
   - No prior-test edit is needed.
8. **Invariants.** Tools JSON and budgets unchanged; no new dependency; no recursion; no `\d`, `\w`, `\s` or `$` regex; no `alter table` or `nx.compose(`; no banned tokens.

### In
1. `history/cochange.py`. It contains:
   - constants `FORMAT, SUMMARY_FORMAT, POLICY_FORMAT, PRODUCER, EVIDENCE_KIND, WINDOW_RULE, BINDING, EMPTY_TREE, REJECTIONS, USAGE_NAMES, INCOMPLETE_REASONS, COVERAGE_STATES, COUNT_KEYS, LIMITATIONS, LIMITS, POLICY_LIMITS, DEFAULT_GENERATED, TOP_KEYS, RECORD_KEYS`;
   - errors `HistoryRejected(reason)` and `HistoryUsageError(what)`;
   - functions `normalize_options`, `filter_policy`, `filter_digest`, `cochange`, `validate_overlay`, `overlay_identity`, `overlay_key`, `record_id`, `summary_document`.
2. `history/__init__.py` (docstring only), and the `cochange` command plus `_write_history_atomic` in `__main__.py`.
3. Tests: `test_s33_cochange.py` (the 7 named tests), `test_s33_support.py`, `test_s33_rejections.py`, `test_s33_isolation.py`. Fixtures: `tests/fixtures/cochange/{history.json, expected-records.json}` (JSON only).
4. `docs/history-signals.md`, the CHANGELOG bullet and the README row.

### Out
- Remote fetch.
- Author, committer, date or message data.
- Blame, ownership or productivity.
- Rename reconstruction.
- Causality or prediction.
- Graph, MCP or `ev.RELATIONS` changes.
- Unbounded scans.
- Commits newer than the snapshot.
- sha256 repositories.
- A `safe.directory` override.

## Approach

### Decisions

**D1 Pipeline.** The first rejection wins. On a rejection nothing is written and stdout is empty. A lease is held only during stages 3 and 6.

| # | Stage | Failure |
| --- | --- | --- |
| 1 | `normalize_options`; grammars of hex, label, snapshot id and policy items; `resolve_output(out, protected=[repo, repo/.atlas, graph_root?], inputs=[])` | usage, exit 2 |
| 2 | git probe (D3 call 1) | `history_unavailable`, `unsupported_object_format`, `repository_not_toplevel`, `history_output_invalid` |
| 3 | `ExitStack`: `open_active(Side(repo, graph_root))`; `reader.snapshot_id == --snapshot-id`; `_lease_schema_epoch == max(SCHEMA_EPOCHS)`; `sqlite3.Error` → unavailable; identity `kind=="git"`, `common_dir` == probe common dir, `worktree_root` == probe toplevel | `snapshot_unavailable`, `snapshot_repository_mismatch`; `DiffUsageError` → usage, exit 2 |
| 4 | exists: end, then source, then start | `commit_not_found`, `source_commit_not_found`; start missing: shallow → incomplete `start_commit_missing`, otherwise `commit_not_found` |
| 5 | ancestry: `end ≤ source`, then `start < end` (start==end is rejected) | `outside_snapshot_history`, `start_not_ancestor_of_end`; rc ∉ {0,1} → incomplete `object_missing` |
| 6 | binding (D6), always run, even when stage 4 or 5 recorded incompleteness | `snapshot_commit_mismatch`, `snapshot_source_unavailable`, `source_tree_too_large`, `history_unavailable` |
| 7 | walk (D4), only when no incompleteness yet | incomplete `object_missing`, `commit_budget_exhausted`, `shallow_boundary_in_window`; `history_output_invalid` |
| 8 | filters and pairs (D5, D7) | `pair_limit_exceeded` |
| 9 | assemble; `validate_overlay`; size | `overlay_too_large`, `invalid_overlay` |
| 10 | CLI: `_write_history_atomic(target)` | exit 1 |

**D2 Git runner (`_git(repo, args, cap, timeout) -> (rc, bytes, overflow)`).**
- The call is `subprocess.Popen(argv, stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL, env=_git_env(), close_fds=True)`, with argv a list and no shell.
- A `threading.Timer(timeout, proc.kill)` enforces the timeout. stdout is read with `read(cap+1)`. Above `cap`, the process is killed and `overflow=True`.
- A timer firing or an `OSError` at spawn → `HistoryRejected("history_unavailable")`. A timeout is never a partial result.
- stderr is never read, so it is never echoed.

**D3 Hardening and the exact call sequence.**
- `_git_env()` is built from scratch. It copies only `PATH` (`os.environ.get("PATH", "/usr/bin:/bin")`) and sets:
  - `LC_ALL=C`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_OPTIONAL_LOCKS=0`;
  - `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_NO_LAZY_FETCH=1`, `GIT_ALLOW_PROTOCOL=none`.
- No other variable is passed: no HOME, `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_EXTERNAL_DIFF`, `GIT_CONFIG_PARAMETERS/COUNT`, `GIT_SHALLOW_FILE`, `GIT_REPLACE_REF_BASE` or `GIT_TRACE*`. They are absent by construction.
- Every call starts with `G = ["git","--no-optional-locks","-C",str(repo),"-c","core.fsmonitor=false","-c","core.quotePath=false","-c","diff.external=","-c","diff.renames=false"]`.
- Every revision is `[0-9a-f]{40}` (validated at stage 1) or the constant `EMPTY_TREE = 4b825dc642cb6eb9a060e54bf8d69288fbee4904`. No ref, abbreviation or option-shaped value ever reaches argv.

1. **Probe.** `G+["rev-parse","--show-toplevel","--absolute-git-dir","--git-common-dir","--is-shallow-repository","--is-bare-repository","--show-object-format"]`, cap 65536.
   - rc≠0 → `history_unavailable`. This covers dubious ownership, a non-repository and a bare repository.
   - Parse: strict UTF-8; exactly 6 LF-terminated lines (else `history_output_invalid`); lines 4 and 5 ∈ {`true`,`false`}.
   - Line 6 ≠ `sha1` → `unsupported_object_format`.
   - `Path(line1).resolve() != repo.resolve()` → `repository_not_toplevel`.
   - `common = str((repo / line3).resolve())` and `top = str(Path(line1).resolve())`, the same rule as `identity()`.
2. **Exists.** `G+["cat-file","-e","--end-of-options",sha+"^{commit}"]`, cap 0. rc 0 → present; rc ∈ Day-1 missing set (expected {1,128}) → absent; any other rc → `history_unavailable`.
3. **Ancestry.** `G+["merge-base","--is-ancestor","--end-of-options",a,b]`, cap 0. rc 0 → yes; 1 → no; other → undecidable.
4. **Tree.** `G+["ls-tree","-r","-z","--full-tree","--end-of-options",source]`, cap `max_tree_bytes`.
   - Overflow → `source_tree_too_large`. rc≠0 → `history_unavailable`.
   - Records: `<mode> SP <type> SP <oid> TAB <path> NUL`. The meta bytes must fullmatch `([0-7]{6}) (blob|commit) ([0-9a-f]{40})`. Paths are raw bytes, the output must end with NUL, and a duplicate path → `history_output_invalid`.
5. **List.** `G+["rev-list","--parents","--max-count="+str(N+1),"--end-of-options",end,"^"+start]`, cap `(N+1)*4100`.
   - rc≠0 → incomplete `object_missing`.
   - Each LF-terminated line must fullmatch `[0-9a-f]{40}( [0-9a-f]{40})*`. Shas must be distinct and `end` must be present, else `history_output_invalid`. Overflow is also `history_output_invalid`.
   - More than N lines → incomplete `commit_budget_exhausted`, with `coverage.commit_budget = N`.
6. **Raw parents** (only for listed commits with zero listed parents). `G+["cat-file","commit",sha]`, cap 65536.
   - rc≠0 → `object_missing`.
   - The header is the bytes before the first `\n\n`; if it is not found within the cap → `history_output_invalid`.
   - Any `parent ` header line → `shallow_boundary_in_window`. This covers a shallow graft and an info/grafts cut alike.
7. **Diff.** For each non-merge listed commit in ascending sha order: `G+["diff-tree","-r","-z","--raw","--no-abbrev","--no-renames","--no-ext-diff","--no-textconv","--end-of-options",P,sha]`, where `P` = the single parent, or `EMPTY_TREE` for a genuine root. Cap = policy `diff_output_cap`.
   - Overflow → the commit is `oversized` (excluded; deterministic, because the cap is inside the digest).
   - rc≠0 → incomplete `object_missing`, and processing stops.
   - Parse: split on NUL; the last element is empty; the rest are (meta, path) pairs. Meta fullmatches `:([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}) ([0-9a-f]{40}) ([ADMT])`, else `history_output_invalid`.

**D4 Window and coverage.**
- Shas are 40-hex lowercase only.
- `start == end` or `start` not an ancestor of `end` → `start_not_ancestor_of_end`. `end` not ≤ `source` → `outside_snapshot_history`.
- An undecidable ancestry (stage 5) → incomplete `object_missing`. Records are withheld, and the ancestry stays unclaimed in the overlay (`coverage.state` says why).
- The commit set is exactly the listed shas. Results never depend on listing order: records sort by path and commits sort by sha.
- `coverage = {state: "complete"|"incomplete_history", reasons: sorted ⊆ INCOMPLETE_REASONS, records: "complete"|"withheld", commit_budget: int|null}`.
  - `complete` ⇔ reasons `[]` ⇔ records `complete`.
  - `commit_budget` is non-null iff `commit_budget_exhausted` is a reason.
  - Phase order for reasons: stage 4/5 reasons; then list; then the raw-parents check over ALL zero-parent commits (collect every reason); then diff, stopping at the first failure.

**D5 Filter policy (`atlas-history-filter.v1`).**
- **Merges.** More than one listed parent → excluded and counted. This is a constant of the policy.
- **Generated paths.** Three typed lists, sorted by code point and de-duplicated. There is no glob, no `fnmatch` and no case folding.
  - A path is generated iff any of these holds:
    - `path.startswith(p)` for a prefix `p`, where a prefix ends with `/`;
    - `path.rsplit("/",1)[-1] ∈ names`;
    - `path.endswith(s)` for a suffix `s`.
  - Default `prefixes`: `build/`, `coverage/`, `dist/`, `node_modules/`, `vendor/`.
  - Default `names`: `Cargo.lock`, `Gemfile.lock`, `composer.lock`, `go.sum`, `package-lock.json`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`, `yarn.lock`.
  - Default `suffixes`: `.map`, `.min.css`, `.min.js`, `.pb.go`, `.pyc`, `_pb2.py`, `_pb2_grpc.py`.
  - Grammars (fullmatch):
    - prefix `(?:[A-Za-z0-9_.@+-]{1,128}/){1,16}`, with no segment equal to `.` or `..`;
    - name `[A-Za-z0-9_.@+-]{1,128}`, not `.` or `..`;
    - suffix `[A-Za-z0-9_.@+-]{1,64}`;
    - at most 256 items per list. A violation → usage `generated_prefix|generated_name|generated_suffix`.
  - CLI: repeatable `--generated-prefix/--generated-name/--generated-suffix` ADD to the defaults; `--no-generated-defaults` clears all three default lists first.
- **Per-entry rules, in this order.**
  1. A gitlink (either mode `160000`) is skipped and counted in `gitlink_paths`.
  2. A path that is not strict UTF-8, or that fails `ev.relative_path`, is skipped and counted in `unrepresentable_paths`.
  3. A generated path is skipped and counted in `generated_paths`.
  4. Otherwise the path is eligible (A, D, M and T all count; deletes are kept).
  - Paths are de-duplicated per commit.
  - Counters sum over all non-merge, non-oversized window commits.
- **Commit outcome.** Let E be the number of eligible paths:
  - E > `bulk_threshold` → `bulk_commits`;
  - E == 0 → `empty_commits`;
  - E == 1 → `single_path_commits`;
  - otherwise → `pair_commits`, contributing every 2-combination of the sorted paths.
- **Policy document:**
  `{format:"atlas-history-filter.v1", merge_commits:"excluded", generated:{prefixes,names,suffixes}, bulk:{threshold:T, counts:"eligible_paths_after_generated", rule:"exclude_commit_when_count_exceeds_threshold"}, oversized:{diff_output_cap_bytes:C, rule:"exclude_commit"}, renames:"not_followed", deleted_paths:"included", type_changes:"included", gitlinks:"excluded", unrepresentable_paths:"excluded", path_identity:"git_tree_path_utf8_exact"}`.
- `filter_digest = sha256("atlas-history-filter.v1:" + ev.canonical(policy))`. Limits stay outside the digest.

**D6 Snapshot binding (stage 6).**
- Read `reader.db.execute(runtime.OBSERVATIONS_SQL)` → JSON. Malformed JSON, a missing row or `sqlite3.Error` → `snapshot_unavailable`. Keep the records with `type=="observation"`.
- Tree map: `path(bytes) → (mode, type, oid)` from D3.4.
- For each observation, in path order:
  - The path encoded as UTF-8 must be in the tree map with type `blob` and mode `100644|100755`. Otherwise → `snapshot_commit_mismatch` (this covers untracked, symlinked and submodule paths).
  - For `state=="indexed"`, read `reader._source_lease.read(path, observed_source_sha256)`. A `ValueError`, `RuntimeError` or `OSError` → `snapshot_source_unavailable`.
  - Then require `hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest() == oid`, else `snapshot_commit_mismatch`.
  - Other states (`disabled|unsupported|failed`) get the presence check only.
- Record `source_snapshot = {snapshot_id, commit, binding:"indexed_files_match_commit_tree", containment:"end_commit_is_ancestor_or_equal", files_compared:<count of indexed observations>}`.
- Files the indexer never observes (docs, lock files) are not bound. This is limitation 6.

**D7 Overlay `atlas-history-overlay.v1`.** It has closed keys, canonical JSON plus LF, and no absolute path, clock, host or limit (except `commit_budget`).
```
{format, overlay_id, key,
 producer:{name:"atlas-aci cochange", version:"atlas-history-cochange.v1"},
 repository:{id, kind:"git", object_format:"sha1", identity_rule:"operator_label_bound_to_snapshot_git_common_dir"},
 window:{start_commit, end_commit, rule:"start_exclusive_end_inclusive"},
 source_snapshot:{snapshot_id, commit, binding, containment, files_compared},
 filter:{policy, filter_digest},
 evidence:{kind:"co_change_correlation", semantics:"file_pair_co_occurrence_in_eligible_commits",
           direction:"undirected", dependency_claim:"none", causality_claim:"none"},
 coverage:{state, reasons, records, commit_budget},
 counts: null | {commits_in_window, merge_commits, bulk_commits, oversized_commits, empty_commits,
                 single_path_commits, pair_commits, generated_paths, gitlink_paths,
                 unrepresentable_paths, pairs, pair_observations},
 limitations:[LIMITATIONS], records:[...]}
```
- **Record.** `{id, type:"co_change", evidence_kind:"co_change_correlation", a, b, support, supporting_commits, repository_id, start_commit, end_commit, filter_digest}`.
  - `a < b` by code point; `support == len(supporting_commits) ≥ 1`.
  - Commits are sorted, unique and 40-hex.
  - Records are sorted by `(a, b)`.
- **Ids.**
  - `id = sha256("atlas-history-record.v1:" + canonical([repository_id, start, end, filter_digest, a, b]))`.
  - `key = sha256("atlas-history-key.v1:" + canonical({repository_id, start_commit, end_commit, filter_digest, producer_version}))`. This is the "keyed by repository identity, window and filter digest" requirement.
  - `overlay_id = sha256(FORMAT + ":" + canonical(rest))`.
- **Count invariant.** `commits_in_window = merge + bulk + oversized + empty + single_path + pair_commits`; `pairs = len(records)`; `pair_observations = Σ support`.
- **Pair limits.** More than `max_pairs` distinct pairs, or more than `max_pair_observations` increments → `pair_limit_exceeded`. This is checked during aggregation, which bounds both memory and CPU. Canonical size above `max_overlay_bytes` → `overlay_too_large`.
- **`validate_overlay` → `invalid_overlay`.** It checks:
  - closed key sets at every level and the constants;
  - grammars: hex40, `ev.hash_value`, the label, `ev.relative_path`;
  - the policy shape, the policy grammars and the recomputed `filter_digest`;
  - the recomputed `key`, every record id and `overlay_id`;
  - every record's provenance == the header's (repository.id, the window, filter_digest);
  - sort orders, `a<b`, `support`;
  - the coverage rules (D4) and `incomplete ⇒ records==[] and counts is None`;
  - the count invariants; `limitations == LIMITATIONS`;
  - the absence of any key `relation`, and of any enum value ∈ `ev.RELATIONS`.
- The library calls it before returning.

**D8 Closed vocabularies.**
- `REJECTIONS` (16): `history_unavailable, unsupported_object_format, repository_not_toplevel, history_output_invalid, snapshot_unavailable, snapshot_repository_mismatch, commit_not_found, source_commit_not_found, outside_snapshot_history, start_not_ancestor_of_end, snapshot_commit_mismatch, snapshot_source_unavailable, source_tree_too_large, pair_limit_exceeded, overlay_too_large, invalid_overlay`.
- `INCOMPLETE_REASONS`: `commit_budget_exhausted, object_missing, shallow_boundary_in_window, start_commit_missing`.
- `COVERAGE_STATES`: `complete, incomplete_history`.
- `COUNT_KEYS` are the 12 keys above. The exclusion counters are `merge_commits, bulk_commits, oversized_commits, generated_paths, gitlink_paths, unrepresentable_paths`.
- `LIMITATIONS`:
  1. `correlation_not_dependency_not_causality`
  2. `renames_not_followed_old_and_new_paths_pair`
  3. `merge_commits_excluded_including_merge_resolutions`
  4. `generated_paths_by_declared_prefix_name_suffix_only`
  5. `incomplete_history_withholds_all_records`
  6. `snapshot_binding_covers_indexed_files_only`
  7. `working_tree_conversions_cause_snapshot_mismatch` (autocrlf, eol attributes, smudge filters, LFS)
  8. `repository_owner_must_match_process_user`
  9. `sha1_object_format_only`
  10. `repository_id_is_an_operator_label`
  11. `git_timeouts_reject_never_truncate`
  12. `legacy_grafts_adding_parents_not_detected`
  13. `ancestry_across_a_shallow_boundary_reported_conservatively`
  14. `not_written_to_the_graph_and_not_exposed_over_mcp`
- No constant, key or enum value contains `safe, verified, proven, guaranteed, compliant, compatible, incompatible, breaking, applied`, or equals an `ev.RELATIONS` member.

**D9 Limits** (outside the digest; default/min/max; bool or out of range → usage):
- `max_commits` 1000/1/20000
- `max_pairs` 100000/1/1000000
- `max_pair_observations` 1000000/1/10000000
- `max_overlay_bytes` 67108864/1/268435456
- `max_tree_bytes` 67108864/1/268435456
- `git_timeout` (seconds, per call) 30/1/600

Policy parameters (inside the digest):
- `bulk_threshold` 50/2/1000
- `diff_output_cap` 4194304/4096/67108864

**D10 CLI.**
- `atlas-aci cochange --repo DIR --repository-id LABEL --start HEX40 --end HEX40 --source-commit HEX40 --snapshot-id HEX64 [--graph-root DIR] --output FILE [--bulk-threshold N] [--diff-output-cap N] [--generated-prefix P]... [--generated-name N]... [--generated-suffix S]... [--no-generated-defaults] [--max-commits N] [--max-pairs N] [--max-pair-observations N] [--max-overlay-bytes N] [--max-tree-bytes N] [--git-timeout N]`.
- `--repo` is `click.Path(exists=True, file_okay=False)` and is resolved. Imports are lazy.
- It prints `atlas-history-summary.v1` = `{format, overlay_id, key, repository_id, start_commit, end_commit, filter_digest, coverage_state, reasons, records}` (canonical plus LF).
- `incomplete_history` is exit 0 and the overlay is written.
- `_write_history_atomic` uses mkstemp with prefix `.atlas-history-overlay-`, then fsync, then `os.replace`, and unlinks the temp file on failure.

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| bad hex, label, snapshot id, limit or policy item | 2 | `Invalid co-change option: <name>` |
| output inside repo, repo/.atlas or graph root | 2 | `--output must be outside --repo and the graph root.` |
| namespace unusable | 2 | `The repository cannot be opened as a graph namespace.` |
| rejection | 1 | `History co-change rejected: <reason>` |
| write failure | 1 | `History overlay cannot be written.` |
| other | 1 | `History co-change failed: <TypeName>` (never `str(exc)`) |

### Work plan (timebox 2 engineering days)
**Day 1 AM: qualification.** `qualify.sh` writes `.spectra/evidence/aci-s33/qualification.json`. It runs on the macOS host git, the dev image (`Dockerfile.dev`), the production image (`Dockerfile`, as `atlas`) and `python:3.14`.
- (a) `git version` in each environment.
- (b) Build the main world with the plumbing builder. Record the commit ids → they must be identical everywhere.
- (c) Every D3 argv, run on the world. Record rc and the output sha256 → identical everywhere, with no stderr warning when HOME is absent. Includes `--end-of-options` on cat-file, merge-base, ls-tree, rev-list (with `^start` still negated) and diff-tree; `EMPTY_TREE` without the object present; and `diff.external=` accepted.
- (d) The rc set of `cat-file -e` for a missing commit; `merge-base` with a missing middle commit; `rev-list` and `diff-tree` with a missing commit and a missing tree; a gitlink↔blob change (one entry or D+A).
- (e) Shallow `file://` clones at `--depth 2`: `is-shallow` true; `rev-list --parents` shows the boundary commit with no parents; `cat-file commit` still shows its parents; the missing-start behaviour; `merge-base` across the boundary.
- (f) Blob binding: index the world at source; every indexed observation reads through the lease; sha1 blob ids == ls-tree; the epoch; probe `common_dir` and toplevel == identity (`/private/var` on macOS).
- (g) Dubious ownership: a repo created as root and probed as uid 1000 in the image → rc≠0. Record the `identity()` kind.
- (h) Hostile env and config: `GIT_DIR`, `GIT_EXTERNAL_DIFF` sentinel, `GIT_CONFIG_PARAMETERS`, a replace ref, local `diff.renames=copies` and a `core.fsmonitor` script → identical outputs, no sentinel file.
- (i) Timing: a 1000-commit linear window.
- (j) Base invariants: tools sha and bytes, budgets, the full suite (test_s08 nonregular_pack deselected), and the README/CHANGELOG pins as read above.

**STOP RULE** (return to the orchestrator; never adapt silently):
- any divergence across environments in (b)(c)(d)(e);
- any unsupported flag;
- any binding mismatch on a clean checkout in (f);
- a lease read impossible for an indexed observation;
- a hostile variable or config changing the output, or running the sentinel, in (h).

Recorded adaptations (not stops):
- (g) git does not reject → test with the shim only and record it;
- (i) more than 30 s → default `max_commits` 500, recorded.

**Day 1 PM:** the runner, probe, window and binding (D2-D4, D6); `test_s33_support.py` worlds; AC-007; rejection tests.
**Day 2 AM:** filters, aggregation, overlay, `validate_overlay` and CLI (D5, D7-D10); AC-001..006; `test_s33_isolation.py`.
**Day 2 PM:** docs, CHANGELOG and README; gates; py314; production image; RUN-01/02.

**Cut order** (each cut becomes a listed limitation; no AC is affected):
1. The D6 blob binding. Fallback: `binding:"operator_declared"`, `files_compared: 0`, plus a limitation. The lease, identity and ancestry checks stay.
2. The README row and doc depth.
3. The replace-ref and fsmonitor hostile variants (keep `GIT_DIR`, `GIT_EXTERNAL_DIFF` and `GIT_CONFIG_PARAMETERS`).
4. The `git gc` replay variant.
5. Production-image steps beyond: twice identical, future window, budget, pin, inventory.

**Never cut:**
- the explicit env and the argv-only runner;
- timeouts as rejections; withheld records when incomplete;
- the mandatory snapshot lease, identity and ancestry;
- closed vocabularies; per-record provenance; `validate_overlay`;
- static scans; G-SRC and G-MCP.

### Independent checker conditions
Checker verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s33-plan-checker`, 4 MAJOR + 8 MINOR; `.spectra/plans/aci-s33.review.json`). Conditions 1-12 below are binding and override conflicting text above.

Orchestrator resolutions (stricter option wherever the checker offered a choice):
- C1: the `rev-list` listing runs before any ancestry conclusion; `start < end` is derived from listed parents; `merge-base --is-ancestor` is used only for `end <= source` (rc 0/1 only, anything else rejects); when the listing is truncated the derivation is skipped and the reasons are exactly `{commit_budget_exhausted}`.
- C2: stage 4/5 incompleteness means no listing; truncation means reasons exactly `{commit_budget_exhausted}`; raw-parent and diff phases run only on a full listing; D1/D4 text reconciled; tested with shallow + `--max-commits 1`.
- C3: a repository with `extensions.partialclone` (or any promisor remote) is refused as `history_unavailable` before any object walk, AND the Day-1/test promisor fixture with an `uploadpack` sentinel proves the sentinel never runs.
- C4: the D6 blob binding is NEVER cut; there is no `operator_declared` fallback. If the timebox runs out, STOP and report to the orchestrator.
- C5: overall deadline default 600 s, outside the digest, rejects and never truncates.
- C10: py314 gate uses `--extra dev`; `test_git_contract_probe` added.

Checker findings (verbatim):

1. **MAJOR. `merge-base --is-ancestor` return codes are untrustworthy under missing objects, and AC-005 cases (c) and (d) depend on them.**
   - Evidence: plan D3.3 and stage 5 (`aci-s33.md:119`, `:147`) run ancestry before the listing.
   - In some git versions, possibly including the 2.39 in the images, a parse failure mid-walk yields rc 1, not 128. That gives `start_not_ancestor_of_end` instead of `object_missing`.
   - Day-1 (d) would catch this only as a slice-stopping STOP, and ubuntu-latest git is not in the Day-1 matrix.
   - Binding condition: run the `rev-list` listing before trusting any ancestry result.
   - Derive `start < end` from that listing: start is a proper ancestor of end iff it appears as a parent of a listed commit, and listed parents include excluded ones.
   - Use `merge-base --is-ancestor` only for `end <= source`, accepting rc 0 or 1.
   - Document that a damaged chain between end and source can mislabel as `outside_snapshot_history`.
   - Stricter: when the listing is truncated, skip the derivation and report `commit_budget_exhausted`.

2. **MAJOR. Coverage reasons are order-dependent and contradictory.**
   - Evidence: D1 stage 7 says the walk runs "only when no incompleteness yet". D4 (`:172`) says "list; then raw-parents over ALL zero-parent commits; collect every reason".
   - With a budget-truncated listing, which commits sit in the first N+1 depends on walk order. This is exactly what decision 4 declares unstable.
   - Whether `shallow_boundary_in_window` or `object_missing` is added would therefore vary. That breaks AC-006 and AC-005(e).
   - Binding condition:
     - Stage 4/5 incompleteness means no listing.
     - Truncation means reasons are exactly `{commit_budget_exhausted}`, with no raw-parent check and no diff.
     - Raw-parent and diff phases run only on a full listing.
     - Resolve the D1/D4 text accordingly and test shallow plus `--max-commits 1`.

3. **MAJOR. Partial clones and promisor remotes are an unverified code-execution path.**
   - Evidence: `GIT_NO_LAZY_FETCH` appears in D3's env list but is ignored before git 2.44, and the images may carry 2.39.
   - A missing object in a repo with `extensions.partialClone` triggers a lazy `git fetch`. That can run a repo-local `remote.<n>.uploadpack`, `core.sshCommand` or `core.gitProxy` program.
   - `GIT_ALLOW_PROTOCOL=none` is the only defence, and Day-1 (h) does not test it.
   - Binding condition:
     - Add a Day-1 (h) fixture with a promisor remote, an `uploadpack` sentinel script and a missing object. The sentinel must never run and the result must be `object_missing`.
     - Stricter: add a probe call, `config --local --get extensions.partialclone`, and map a hit to `history_unavailable`.

4. **MAJOR. Cut #1 hollows AC-007 and must not be a silent cut.**
   - Evidence: the cut order (`:333-335`) falls back to `operator_declared`, while the plan claims "no AC is affected".
   - Without the blob binding the operator can declare `--source-commit F3` against a snapshot taken at F2. The test "`--source-commit F3` -> `snapshot_commit_mismatch`" then cannot pass.
   - Binding condition: the D6 blob binding is never cut. If time runs out, STOP and return to the orchestrator.

5. **MINOR. Per-call timeouts only, and a large window can time out instead of reporting budget exhaustion.**
   - Evidence: D2 and D9 bound each call, so the worst case is 20000 commits times 600 s.
   - `rev-list ^start --max-count` still walks the whole start..end range before the count applies. A huge window therefore hits the timeout and yields `history_unavailable`, not `commit_budget_exhausted`.
   - Binding condition: add an overall deadline outside the digest (default 600 s) that rejects and never truncates. Document the large-window behaviour.

6. **MINOR. `diff-tree` lacks `--ignore-submodules=none`.**
   - Evidence: the argv at `:159`.
   - `diff.ignoreSubmodules` in local config, or `ignore=all` in the working-tree `.gitmodules`, can hide gitlink entries. That makes `gitlink_paths` depend on the working tree and breaks AC-006.
   - Binding condition: add the flag and a hostile variant with `.gitmodules` ignore=all.

7. **MINOR. Legacy grafts are only partly detected.**
   - Evidence: limitation 12, and only zero-parent commits get the raw-parent check.
   - Binding condition: set `GIT_GRAFT_FILE=/dev/null` in `_git_env()` and add a `.git/info/grafts` hostile variant in Day-1 (h) and AC-006. The variable should be ignored harmlessly where unsupported.

8. **MINOR. Snapshot binding gaps in stage 3 and stage 6.**
   - The identity accessor is unnamed. Use `reader._source_lease.manifest["context"]["identity"]` (precedent at `revision_vector.py:326-331`).
   - Also require `identity["root"]` to resolve to the probe toplevel. A snapshot of a subdirectory would otherwise surface as a misleading `snapshot_commit_mismatch`.
   - D1 says the lease is "held only during stages 3 and 6". That is a time-of-check/time-of-use gap. Use one `ExitStack` and one reader through stages 3 to 6.

9. **MINOR. AC-004 coverage of the stdout summary.** The `atlas-history-summary.v1` document (`:292`) carries no evidence kind. Add `evidence_kind` to it and validate it.

10. **MINOR. py314 gate and CI git parity.**
    - Evidence: `pyproject.toml:29` makes `dev` an optional extra and `ci.yml:114` syncs `--extra dev`. The gate command at `:518` runs `uv run --frozen pytest` without it, so pytest is missing.
    - Binding condition: use `--extra dev`.
    - The ubuntu-latest git is not in the Day-1 matrix. Add a fast `test_git_contract_probe` that prints `git --version` and asserts the rc sets and flags, so a CI failure is attributable.
    - `python:3.12-slim` may now be trixie, so record git versions rather than assuming 2.39.

11. **MINOR. D2 overflow versus return code.** On overflow the killed process has rc -9. State that the overflow check precedes the rc check. For `cat-file commit`, a header terminator found within the 65536 cap counts as success even when the message overflows.

12. **MINOR. Real-repo behaviour of the blob binding.**
    - The indexer does not honour `.gitignore` (no gitignore handling in `codegraph.py`).
    - Any untracked or ignored indexable file therefore gives `snapshot_commit_mismatch` with no diagnostic.
    - Docs must say to index a clean checkout.
    - Limitation 6 must also say that files added after the snapshot are undetected.
    - Document that `repository_id` is a label and that the start/end shas are the real content binding.
    - Document that AC-006 identity holds per snapshot id (`overlay_id` covers it).

## Stories
As an agent operator, I want file pairs that changed together in an explicit, bounded commit window, each with the commits that support it, so that I can inspect adjacent files without the result being presented as a dependency.

As a reviewer, I want every signal tied to its repository, window, filter policy and the indexed snapshot it was taken from, with incomplete history declared rather than guessed.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s33.criteria.md` (sha256 `7cf8c8ef…9a521`), verbatim:

```
### AC-S33-001 (event-driven)
GIVEN a bounded local history fixture has known eligible pair co-occurrences
WHEN the operator computes co-change signals
THEN the overlay SHALL report the fixture's exact eligible-commit support count for each file pair.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_support_matches_commits

### AC-S33-002 (ubiquitous)
THEN every co-change record SHALL carry its repository identity, start/end commits, filter digest and supporting commit IDs.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_window_provenance

### AC-S33-003 (event-driven)
GIVEN the history contains filtered commits and generated paths
WHEN co-change aggregation runs
THEN the reported support counts SHALL exclude contributions removed by the declared merge/generated/bulk filters.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_excluded_commits_do_not_contribute

### AC-S33-004 (ubiquitous)
THEN every history signal SHALL carry the evidence kind co_change_correlation.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_is_correlation_only

### AC-S33-005 (unwanted-behavior)
GIVEN a required ancestor is missing or the commit budget is exhausted
WHEN the history scan cannot cover the requested window
THEN the result SHALL declare incomplete_history.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_missing_history_is_incomplete

### AC-S33-006 (event-driven)
GIVEN identical repository objects, window, filter policy and producer version
WHEN the same co-change analysis runs twice
THEN the canonical history overlay SHALL remain identical after recomputation.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_replay_is_idempotent

### AC-S33-007 (unwanted-behavior)
GIVEN the requested end commit is not contained in the selected source snapshot's ancestry
WHEN window validation runs
THEN the analyzer SHALL reject the history window as outside_snapshot_history.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_future_window_rejected

### AC-S33-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S33-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

### Fixtures
**Builder** (`test_s33_support.py`).
- Plumbing only: `hash-object -w --stdin`, `update-index -z --index-info` (byte paths; mode 0 removes), `write-tree`, `commit-tree -p … -m <label>`, `update-ref`. There is no working tree while building, and no hooks run.
- Builder env: `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_AUTHOR_NAME/EMAIL` and `GIT_COMMITTER_NAME/EMAIL` = `Atlas Fixture` / `fixture@example.invalid`, `GIT_AUTHOR_DATE` = `GIT_COMMITTER_DATE` = `@<1700000000+i> +0000`. Plus `-c init.defaultBranch=main -c core.hooksPath=/dev/null -c gc.auto=0 -c core.autocrlf=false -c commit.gpgSign=false`.
- Then `reset --hard <source>`, and `build(repo, tmp/graphs/<world>)` (S19 `build`). Graph roots always sit outside the repo.
- Worlds are module-scoped. Mutating variants rebuild their world (same ids) in a fresh directory.
- `tests/fixtures/cochange/history.json` holds the world specs (labels, parents, changes as text, null, `{"gitlink": label}` or `{"path_hex": …}`).
- `expected-records.json` holds the golden records by label. It is pinned to real shas only if Day-1 (b) holds.
- No golden overlay is kept, because `snapshot_id` depends on absolute paths. No `.gitattributes` file is created anywhere in the repository.

**Main world.** Start = C0, the root. C0 holds `src/{a..f}.py`, `README.md`, `uv.lock`, `dist/app.js` and `web/app.min.js`. `bulk_threshold = 5`. End = source = C14.

| Commit | Changes | Outcome |
| --- | --- | --- |
| C1 | a, b | (a,b) |
| C2 | a, b, c | (a,b), (a,c), (b,c) |
| C3 (side branch from C2) | b, c | (b,c) |
| C4 | a, d | (a,d) |
| M5 = merge(C4, C3), also editing a and e | merge | excluded |
| C6 | a, uv.lock, dist/app.js, web/app.min.js | 3 generated paths; single path |
| C7 | a-f | 6 eligible > 5: bulk |
| C8 | a, delete c | (a,c) |
| C9 | a, b, gitlink `vendor/mod` | (a,b); 1 gitlink path |
| C10 | a, `bad\xff.txt`, `docs/naïve.txt` | (docs/naïve.txt, a); 1 unrepresentable path |
| C11 | delete the bad path, b, f | (b,f); 1 unrepresentable path |
| C12 | rename e→e2 (same content), d | (d,e), (d,e2), (e,e2) |
| C13 | empty commit | empty |
| C14 | README only | single path |

Expected:
- 9 pairs. Support: a-b 3, a-c 2, b-c 2; a-d, docs/naïve-a, b-f, d-e, d-e2 and e-e2 each 1. `pair_observations` 13.
- Counts: window 14, merge 1, bulk 1, oversized 0, empty 1, single 2, pair_commits 9, generated 3, gitlink 1, unrepresentable 2.

**Other worlds.**
- **future:** F0 → F1 → F2 → F3 (edits the indexed `src/x.py`); S1 is a side branch from F1; F2r is F2 plus a README edit. The snapshot is at F2.
- **shallow:** `git clone --depth 2 file://<main>`, then indexed.
- **shallow-merge:** X0, X1, Y1 (both children of X0), X2 = merge(X1, Y1); cloned at `--depth 2` from X2.
- **missing:** the main world rebuilt; then delete the loose object of commit C3, or the loose root tree of C8.
- **nested:** directory A indexed inside repo R, then `git init` in A and commits.
- **non_git:** a directory indexed before `git init`.
- **refused:** indexed under the refuse shim.
- **dirty:** F2 with `src/x.py` edited, or an untracked `src/z.py`, indexed into its own root.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_cochange_support_matches_commits | The main-world records equal BOTH:<br>- a pure-Python oracle over the spec that applies D5 to the spec-level changes (no git parsing);<br>- `expected-records.json` mapped label→sha.<br>Exact pair set, `support` and `supporting_commits`; the counts equal the table; the CLI overlay bytes equal the library's. |
| 002 | test_cochange_window_provenance | Every record (non-empty list asserted first) has:<br>- `repository_id == "s33-main"`, `start_commit == C0`, `end_commit == C14`;<br>- `filter_digest` == sha256 recomputed from the policy;<br>- `supporting_commits` ⊆ an independent `git rev-list C14 ^C0`, sorted, 40-hex;<br>- for each supporting commit, a test-side `git diff-tree --name-only` lists both `a` and `b`.<br>`key`, record ids and `overlay_id` recompute. A single-record tamper of each provenance field → `invalid_overlay`. |
| 003 | test_cochange_excluded_commits_do_not_contribute | M5, C7 and C6 never appear in `supporting_commits`. No record names `uv.lock`, `dist/`, `.min.js`, `vendor/mod` or (a, e). C3 (merged, non-merge) DOES contribute.<br>Control run with `--no-generated-defaults --bulk-threshold 1000`: C6 and C7 pairs appear (6+15) and `filter_digest` differs; M5 is still excluded. A test-side `git diff-tree M5^1 M5` shows a and e changed. |
| 004 | test_cochange_is_correlation_only | Every record has `evidence_kind == "co_change_correlation"`, and `evidence.kind` matches.<br>No key `relation`; no string value ∈ `ev.RELATIONS`; `ev.RELATIONS` unchanged.<br>The tools JSON sha and bytes are unchanged and contain no `co_change` or `cochange`; `cochange` ∈ `cli.commands`.<br>The graph root, the repo and `.git` inventory (sha256, size, mode, mtime_ns) are unchanged around the run. |
| 005 | test_cochange_missing_history_is_incomplete | Each case gives `state "incomplete_history"`, `records []`, `counts null`, exit 0, and the overlay validates:<br>(a) shallow clone, start C0 → `start_commit_missing`;<br>(b) shallow-merge, start X1, end X2 → `shallow_boundary_in_window`;<br>(c) missing commit C3 → `object_missing`;<br>(d) missing C8 tree → `object_missing`;<br>(e) `--max-commits 1` and `13` → `commit_budget_exhausted` with `commit_budget` = N.<br>Controls: `--max-commits 14` → complete; shallow C13..C14 → complete. A missing end → `commit_not_found`; a missing start in a non-shallow repo → `commit_not_found`. |
| 006 | test_cochange_replay_is_idempotent | Byte-identical overlays across:<br>- two library runs; CLI runs to two outputs; permuted CLI options;<br>- generated items reordered or duplicated;<br>- `python -m atlas_aci cochange` with `PYTHONHASHSEED=1/2`;<br>- a hostile env (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_EXTERNAL_DIFF` sentinel script, `GIT_CONFIG_PARAMETERS` renames, `HOME` with a `.gitconfig`, a replace ref for C2, local `diff.renames=copies`, a `core.fsmonitor` script); the sentinel is never created;<br>- after `git gc` (packs plus commit-graph).<br>A rebuilt world at another path: everything except `source_snapshot.snapshot_id` and `overlay_id` is equal. The golden records are equal. |
| 007 | test_cochange_future_window_rejected | future world with the snapshot at F2, `source F2`:<br>- end F3 → `outside_snapshot_history`;<br>- end S1 → `outside_snapshot_history`;<br>- end F2 → complete (control: (x,y) support 2).<br>`--source-commit F3` → `snapshot_commit_mismatch`, so declaring a later source cannot evade the check. `F2r` → complete (pins limitation 6).<br>CLI: exit 1 with the exact text, empty stdout, a sentinel `--output` unchanged, no `.atlas-history-overlay-*` left. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the one receipt is `.spectra/state/aci-s33.execution.json`. |

### Supporting tests
- **`test_s33_rejections.py`.** Every D8 reason at library level, plus the CLI exit 1 text, empty stdout, an unchanged sentinel output, no temp files and no echo of a path or of stderr. Cases:
  - `history_unavailable`:
    - refuse shim; slow shim with `--git-timeout 1` (under 3 s); not a git directory; a `git` missing from PATH.
  - `unsupported_object_format`: `init --object-format=sha256`, if Day-1 (c) supports it.
  - `repository_not_toplevel`: a subdirectory.
  - `history_output_invalid`: a proxy shim emitting garbage for `diff-tree` only.
  - `snapshot_unavailable`: no index; wrong id; superseded by a re-index; old epoch via an `open_active` spy.
  - `snapshot_repository_mismatch`: the nested, non_git and refused worlds.
  - `snapshot_commit_mismatch`: dirty (edited and untracked).
  - `snapshot_source_unavailable`: a lease `read` spy, the only fabricated case.
  - `source_tree_too_large`: `--max-tree-bytes 1`.
  - `start_not_ancestor_of_end`: equal, reversed, unrelated.
  - `commit_not_found` / `source_commit_not_found`: unknown shas.
  - `pair_limit_exceeded`: `--max-pairs 1` and `--max-pair-observations 1`.
  - `overlay_too_large`: `--max-overlay-bytes 1`.
- **Usage, exit 2:**
  - hex: short, uppercase, 41 characters, `HEAD`, `--x`;
  - a bad label; a bad snapshot id;
  - each limit as a bool, 0 and max+1;
  - policy items `../x/`, `/abs/`, `x`, `*.js`, empty, 257 items;
  - output inside the repo, repo/.atlas or the graph root (including through a symlinked parent);
  - an unusable namespace.
- **`test_s33_isolation.py`:**
  - `history/*.py == [__init__.py, cochange.py]`; ASCII; lines ≤ 100.
  - AST scan:
    - every `subprocess.*` call is a `Popen` with `env=_git_env()`, an argv Name, and no `shell`;
    - no `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `open(`, `os.replace`, `tempfile` or `sqlite3.connect`;
    - `os.environ` is used only as `.get("PATH", …)`;
    - no function calls itself;
    - no SQL literal (only `runtime.OBSERVATIONS_SQL`).
  - Import allowlist: `__future__, contextlib, collections.abc, dataclasses, hashlib, json, os, pathlib, re, sqlite3, subprocess, threading, typing, atlas_aci.{overlays.runtime, query.diff, storage.evidence}`. No `socket`, `ssl`, `http`, `urllib` or `requests`.
  - Regex scan: no `\d \w \s $`; no `isdigit`, `isidentifier`, `normalize`, `casefold` or `fnmatch`.
  - Vocabulary scan with a planted hit: constants, overlay keys and enum values avoid the banned set ∪ {applied} and `ev.RELATIONS`.
  - `validate_overlay` tamperings, one per check in D7, including `relation:"call"`, an unsorted list, `support` ≠ len, `a ≥ b`, an incomplete overlay with records, a changed policy without a new digest, extra keys, and a limitation drift.
  - The tools JSON pin, the 7 `READ_ONLY_TOOLS`, the four budgets and `ev.RELATIONS`.
  - Docs, CHANGELOG and README:
    - `docs/history-signals.md` is ASCII and names every closed value, limit with its default/min/max, policy default, CLI text and limitation;
    - it contains the phrases "correlation", "not a dependency", "not causality", "start_exclusive_end_inclusive", "records are withheld", "dubious ownership", "safe.directory is never overridden", "sha1", "does not write the graph", "CLI only, no MCP op";
    - the CHANGELOG bullet (located by "Git co-change correlation (ACI-S33)") sits directly after the S30 bullet;
    - the README row (located by `docs/history-signals.md`) contains "correlation", "not a dependency" and "CLI only, no MCP op", and none of the prior locators, prior titles or `canary-missions.md`.

### Gates beyond the ACs
- **G-SRC.**
  - `git diff --exit-code 890e181 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage,extractors,providers,indexing,resolvers,frameworks,architecture,counterfactual,contracts,importers,overlays,dependencies,cross_repo,reports} mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows`.
  - `git diff --name-only 890e181 -- mcp-server/src` ⊆ {`history/__init__.py`, `history/cochange.py`, `__main__.py`}.
  - Test changes are only `test_s33_*.py` and `fixtures/cochange/**`. No prior test is edited.
- **G-MCP.** Tools JSON `94f9567a…` / 1631472; `READ_ONLY_TOOLS` 7.
- **Regression.** `test_s19*`, `test_s27*`, `test_s30*`, `test_s32*`, `test_thesis_negatives`, `test_export` are green and unchanged.
- **CI-exact lint.** `uv run --frozen ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/`.
- **Full suite** with the `test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition` deselect. No new failure or skip compared with Day-1 (j).
- **py314.**
  - `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'git --version; cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen pytest tests/slices/test_s33_*.py tests/test_export.py -q'`.
  - Uses its own git. Log to `.spectra/evidence/aci-s33/py314.log`.
- **Production image** (`prod-image.sh`, written by the executor and run by the orchestrator).
  1. Build `mcp-server/Dockerfile`.
  2. `docker run --rm --network none --entrypoint sh` as `atlas`, with `make-history.sh` mounted `:ro`.
  3. Build the main world in `/tmp/w`, owned by `atlas` so no `safe.directory` is involved. C14 must equal the fixture's golden sha.
  4. `atlas-aci index --repo /tmp/w --graph-root /tmp/g` (the snapshot id comes from `index_done`).
  5. `cochange` twice → `cmp` equal and 9 records.
  6. A future end → exit 1 `outside_snapshot_history`.
  7. `--max-commits 1` → `incomplete_history`.
  8. Inventories (sha256 of `/tmp/w` including `.git`, and `/tmp/g`) are equal before and after.
  9. `atlas-aci tools` matches the pin. Record `git --version`.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s33_cochange.py -q
uv run --frozen pytest tests/slices/test_s33_*.py -q
uv run --frozen pytest tests/slices/test_s19_*.py tests/slices/test_s27_*.py tests/slices/test_s30_*.py tests/slices/test_s32_*.py tests/test_thesis_negatives.py tests/test_export.py -q
uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/ && uv run --frozen mypy src/
```

## Confidence
To be computed with `ramza-score`.
- **Why it is low-churn.**
  - The new package is standalone and only reads: one S19 lease plus read-only git plumbing.
  - No gated or graph change, no MCP change and no new dependency.
  - Every output is canonical and recomputed by `validate_overlay`.
- **Main uncertainties, covered by the Day-1 STOP rule:**
  - parity of git flags, formats and exit codes across four git builds;
  - blob-id agreement of the indexed bytes;
  - shallow and missing-object behaviour;
  - the dubious-ownership exit.

## Rejected Alternatives
- **Live `HEAD` as the snapshot tip** (scout A). The snapshot may be older, or built from a dirty tree.
- **Operator-declared source only** (scout B). An unchecked claim; the binding makes it mechanical.
- **Truncating to the first N in `--topo-order`.** The prefix is not specified across git versions or commit-graph states, so AC-006 would be at risk. Records are withheld instead.
- **Partial records on shallow or missing history.** A partial count looks like an exact one. Withheld.
- **`git log` porcelain in one process.** It reads more config (pretty, decorate, mailmap), and its framing is format-dependent.
- **`diff-tree --stdin` batching.** The header framing is unverified. One process per commit, bounded by `max_commits`.
- **Reading `.git/shallow`.** Replaced by a plumbing comparison of listed and raw parents.
- **A `safe.directory=*` override.** It defeats git's ownership protection. The check is kept and documented.
- **Writing a history table into the graph.** It edits gated paths and changes snapshot ids.
- **`fnmatch` or glob generated patterns.** Platform case-folding and a regex surface. Three typed lists instead.

## Risks (ranked)
1. **Git version divergence** in flags, `-z` formats or exit codes (macOS, bookworm/trixie slim, the 3.14 image, ubuntu CI). Day-1 STOP; every parse failure rejects `history_output_invalid`.
2. **Binding false mismatches on real repositories** (autocrlf, eol attributes, filters, LFS, untracked indexed files, symlinks). A strict rejection plus limitation 7, and the docs tell operators to index a clean checkout. This is cut #1 if time runs short.
3. **Ancestry or shallow exit codes vary** → undecidable → `object_missing` (conservative). Pinned by Day-1 (d)/(e).
4. **Foreign-owner repositories** (Docker `:ro` with a foreign uid) → `history_unavailable`. Documented; an opt-in is a follow-up.
5. **Cost.** One process per commit, and `rev-list ^start` walks. Bounded by `max_commits` and `git_timeout`; a timeout rejects and never truncates.
6. **Snapshot ids depend on the absolute path.** No golden overlay; golden records plus in-test replay.
7. **The CHANGELOG ordering pin** (S30 first). Resolved: the S33 bullet goes second.
8. **Timebox (2 days).** Follow the cut order; at the limit, report and propose a follow-up.
