# Git co-change correlations (ACI-S33)

`atlas-aci cochange` reports which pairs of files changed together in the commits of one explicit
window of a git repository, together with the commits that support each pair. It is a
**correlation**: a pair is "not a dependency" and "not causality". It says that two paths were
touched by the same eligible commit, never that one needs, calls or caused the other. The command
is CLI only, no MCP op: nothing is added to the seven read-only tools and the tool manifest is
byte-identical.

The result is a standalone, content-addressed overlay, `atlas-history-overlay.v1`, written only to
the operator's `--output`. It does not write the graph and never touches the repository: every git
call is a read-only plumbing command and the repository's `.git` directory is left byte-identical.
Nothing is fetched, cloned or discovered, and no network is used.

## What it needs

| Input | Meaning |
| --- | --- |
| `--repo` | The git repository, its top-level directory (a subdirectory is `repository_not_toplevel`) |
| `--repository-id` | An operator label, `[a-z0-9][a-z0-9._-]{0,63}` (the S30 grammar) |
| `--start`, `--end` | Window `start..end`: `start_exclusive_end_inclusive`, both 40 lowercase hex, `start` a proper ancestor of `end` |
| `--source-commit` | The commit the indexed snapshot was taken from (40 hex) |
| `--snapshot-id` | The ACTIVE snapshot of `--repo` (64 hex, printed by `atlas-aci index` as `snapshot_id=`) |
| `--graph-root` | Optional external graph root, as for every other command |
| `--output` | Where the overlay is written; outside the repository and the graph root |

The selected source snapshot is mandatory. It is leased read-only (one lease from the identity
check to the end of the binding) and must be the active snapshot at the newest schema epoch. Its
recorded identity must be a git repository whose common directory and worktree root equal the
repository analysed, and its root must resolve to the repository's top-level directory.

**The snapshot is bound to `--source-commit` mechanically.** The git blob id of the bytes of every
file the snapshot indexed must equal the entry of that commit's tree at the same path, and every
other file the snapshot observed must be a tracked file of that tree. `--end` must be the source
commit or one of its ancestors (`git merge-base --is-ancestor`), otherwise the window is rejected
as `outside_snapshot_history`. Declaring a later `--source-commit` therefore cannot hide a window
that lies beyond the snapshot: the blobs of the snapshot would disagree
(`snapshot_commit_mismatch`).

Operational consequences:

- Index a clean checkout. The indexer does not read `.gitignore`, so any untracked (or ignored)
  indexable file makes the binding fail with `snapshot_commit_mismatch`, with no further detail.
- Working-tree conversions (autocrlf, eol attributes, smudge filters, LFS) change the bytes of a
  file and cause `snapshot_commit_mismatch`.
- The binding covers the files the indexer observed only. A file added after the snapshot, or one
  the indexer never observes (documents, lock files), is not detected.
- `repository_id` is an operator label. The real content binding is the pair of start and end
  commit ids and the source commit bound to the snapshot.
- Replay identity holds per snapshot id: the same window, filter policy and objects in a copy of
  the repository at another path give another snapshot id, so only `source_snapshot.snapshot_id`
  and `overlay_id` differ (the record, key and filter digest bytes are equal).

## The window and the commits

Listing and counting are bounded: at most `--max-commits` commits are listed. The commits of the
window are `git rev-list end ^start`; merge commits are excluded; a root commit is diffed against
the empty tree. The commit set never depends on listing order: records are sorted by path and
supporting commits by id. `start < end` is read from the listing (the start commit must be a
listed parent), never from an ancestry answer of git for the pair; `git merge-base --is-ancestor`
is used only to decide `end <= source` and only a return code of 0 or 1 is accepted. A damaged
history chain between `end` and the source commit may therefore be reported as
`outside_snapshot_history` rather than as incomplete history.

### Eligible commits and the filter policy (`atlas-history-filter.v1`)

A commit contributes only if it is not filtered. Entries are handled in this order:

1. A gitlink (either mode `160000`) is skipped and counted in `gitlink_paths`.
2. A path that is not strict UTF-8, or not a relative tree path, is skipped and counted in
   `unrepresentable_paths`.
3. A generated path is skipped and counted in `generated_paths`.
4. Otherwise the path is eligible (added, deleted, modified and type-changed paths all count;
   renames are not followed, so an old and a new path pair with each other).

Then, with E the number of eligible paths of the commit: E greater than `bulk_threshold` is a
`bulk_commits` exclusion; E == 0 is `empty_commits`; E == 1 is `single_path_commits`; otherwise
the commit is a `pair_commits` and contributes every pair of its sorted paths. A commit whose raw
diff exceeds `diff_output_cap` is an `oversized_commits` exclusion. A merge commit (more than one
parent) is counted in `merge_commits` and never contributes, including its conflict resolutions.

Generated paths use three typed lists (no glob, no case folding): a path is generated if it starts
with a prefix, if its last segment is a name, or if it ends with a suffix. Defaults:

| List | Defaults |
| --- | --- |
| prefixes | `build/`, `coverage/`, `dist/`, `node_modules/`, `vendor/` |
| names | `Cargo.lock`, `Gemfile.lock`, `composer.lock`, `go.sum`, `package-lock.json`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`, `yarn.lock` |
| suffixes | `.map`, `.min.css`, `.min.js`, `.pb.go`, `.pyc`, `_pb2.py`, `_pb2_grpc.py` |

`--generated-prefix`, `--generated-name` and `--generated-suffix` (repeatable, at most 256 each)
add to the defaults; `--no-generated-defaults` empties the three lists first. Grammars: a prefix is
one to sixteen segments of `[A-Za-z0-9_.@+-]{1,128}` each ending in `/` (no `.` or `..`), a name
is `[A-Za-z0-9_.@+-]{1,128}` (not `.` or `..`), a suffix is `[A-Za-z0-9_.@+-]{1,64}`.

The policy parameters are part of the policy document, which is hashed:
`filter_digest = sha256("atlas-history-filter.v1:" + canonical(policy))`.

| Policy parameter | Default | Min | Max |
| --- | --- | --- | --- |
| `--bulk-threshold` | 50 | 2 | 1000 |
| `--diff-output-cap` (bytes) | 4194304 | 4096 | 67108864 |

## Limits (outside the filter digest)

| Limit | Default | Min | Max |
| --- | --- | --- | --- |
| `--max-commits` | 1000 | 1 | 20000 |
| `--max-pairs` | 100000 | 1 | 1000000 |
| `--max-pair-observations` | 1000000 | 1 | 10000000 |
| `--max-overlay-bytes` | 67108864 | 1 | 268435456 |
| `--max-tree-bytes` | 67108864 | 1 | 268435456 |
| `--git-timeout` (seconds, per git call) | 30 | 1 | 600 |
| `--git-deadline` (seconds, whole analysis) | 600 | 1 | 86400 |

More distinct pairs than `--max-pairs`, or more increments than `--max-pair-observations`, is
`pair_limit_exceeded`. A canonical overlay larger than `--max-overlay-bytes` is `overlay_too_large`.
A timeout or the overall deadline is a rejection (`history_unavailable`): it never returns a
partial result. A very large window can hit the timeout while git still walks `start..end`
(`rev-list ^start` examines the whole range before the count applies), and is then reported as
`history_unavailable` rather than `commit_budget_exhausted`; narrow the window or raise
`--git-timeout` and `--git-deadline`.

## Git is run by one hardened runner

An argument list (never a shell), a from-scratch environment (only `PATH` is copied; `LC_ALL=C`,
no system or global config, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_GRAFT_FILE=/dev/null`,
`GIT_NO_LAZY_FETCH=1`, `GIT_ALLOW_PROTOCOL=none`, no `HOME`), fixed `-c` overrides
(`core.fsmonitor=false`, `core.quotePath=false`, `diff.external=`, `diff.renames=false`), bounded
output (the overflow check comes before the return code) and a timeout. Every revision is 40
lowercase hex and follows `--end-of-options`; `diff-tree` is run with `--ignore-submodules=none`,
`--no-ext-diff` and `--no-textconv`. The caller's environment, a repository's local diff or
fsmonitor configuration, a `.gitmodules` `ignore = all`, replace refs and `info/grafts` therefore
change nothing. Only the object format `sha1` is supported (`unsupported_object_format`).

A repository whose configuration marks it as a partial clone (`extensions.partialclone` or a
promisor remote) is refused as `history_unavailable` before any object is read, because a missing
object would trigger a lazy fetch and repository-configured programs.

**Ownership.** safe.directory is never overridden. git itself refuses a repository owned by
another user ("dubious ownership"), and such a repository is `history_unavailable`. Run the command
as the owner of the repository, or index and analyse a copy you own. The indexer's snapshot
identity of such a repository degrades to `git_unavailable`, which is also never accepted.

## The overlay (`atlas-history-overlay.v1`)

Canonical JSON plus a newline, closed keys at every level, no absolute path, clock or host name.
Top level: `format`, `overlay_id`, `key`, `producer`, `repository`, `window`, `source_snapshot`,
`filter`, `evidence`, `coverage`, `counts`, `limitations`, `records`.

Every record has `id`, `type` (`co_change`), `evidence_kind`, `a`, `b` (`a < b` by code point),
`support` (the number of eligible commits holding both paths, equal to the length of
`supporting_commits`), `supporting_commits` (sorted, unique, 40 hex), `repository_id`,
`start_commit`, `end_commit` and `filter_digest`. Every history signal carries the evidence kind
`co_change_correlation`; the overlay header says `dependency_claim: none` and
`causality_claim: none`. The record id and the overlay key are keyed by repository identity,
window and filter digest. `validate_overlay` recomputes every identifier, digest, sort order and
count and rejects drift as `invalid_overlay`.

`counts` holds `commits_in_window`, `merge_commits`, `bulk_commits`, `oversized_commits`,
`empty_commits`, `single_path_commits`, `pair_commits`, `generated_paths`, `gitlink_paths`,
`unrepresentable_paths`, `pairs` and `pair_observations`;
`commits_in_window` is the sum of the six commit outcomes.

### Incomplete history

When the history cannot cover the requested window the result declares
`coverage.state: incomplete_history`; the overlay is still written, the exit code is 0, the
records are withheld (`records: []`, `counts: null`) and the reasons say why. Coverage states:
`complete`, `incomplete_history`. Reasons:

| Reason | Meaning |
| --- | --- |
| `start_commit_missing` | A shallow repository does not hold the start commit |
| `shallow_boundary_in_window` | A listed commit has no parent in the listing but its raw commit names one (a shallow cut) |
| `object_missing` | A commit or tree object of the window cannot be read |
| `commit_budget_exhausted` | More than `--max-commits` commits are in the window (`coverage.commit_budget` says the budget); no other reason is reported with it |

A missing end commit, or a missing start commit in a repository that is not shallow, is
`commit_not_found`. A partial count looks exact, so no partial record is ever emitted.

## Rejections (exit 1, nothing written, empty stdout)

The closed set: `history_unavailable`, `unsupported_object_format`, `repository_not_toplevel`,
`history_output_invalid`, `snapshot_unavailable`, `snapshot_repository_mismatch`,
`commit_not_found`, `source_commit_not_found`, `outside_snapshot_history`,
`start_not_ancestor_of_end`, `snapshot_commit_mismatch`, `snapshot_source_unavailable`,
`source_tree_too_large`, `pair_limit_exceeded`, `overlay_too_large`, `invalid_overlay`.

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| bad hex, label, snapshot id, limit or policy item | 2 | `Invalid co-change option: <name>` |
| output inside the repository, its `.atlas` or the graph root | 2 | `--output must be outside --repo and the graph root.` |
| namespace unusable | 2 | `The repository cannot be opened as a graph namespace.` |
| rejection | 1 | `History co-change rejected: <reason>` |
| write failure | 1 | `History overlay cannot be written.` |
| other | 1 | `History co-change failed: <TypeName>` (never the exception text) |

On success stdout is the canonical `atlas-history-summary.v1`: `format`, `overlay_id`, `key`,
`repository_id`, `start_commit`, `end_commit`, `filter_digest`, `evidence_kind`, `coverage_state`,
`reasons` and `records` (the number of records).

## Limitations

The overlay carries these closed strings: `correlation_not_dependency_not_causality`,
`renames_not_followed_old_and_new_paths_pair`,
`merge_commits_excluded_including_merge_resolutions`,
`generated_paths_by_declared_prefix_name_suffix_only`,
`incomplete_history_withholds_all_records`,
`snapshot_binding_covers_indexed_files_only_files_added_later_are_not_detected`,
`working_tree_conversions_cause_snapshot_mismatch`, `repository_owner_must_match_process_user`,
`sha1_object_format_only`, `repository_id_is_an_operator_label`,
`git_timeouts_reject_never_truncate`, `legacy_grafts_adding_parents_not_detected`,
`ancestry_across_a_shallow_boundary_reported_conservatively`,
`not_written_to_the_graph_and_not_exposed_over_mcp`,
`damaged_chain_between_end_and_source_may_report_outside_snapshot_history` and
`indexer_ignores_gitignore_so_untracked_indexable_files_reject_the_binding`.

Out of scope: remote fetch, author, committer, date or message data, blame or ownership,
rename reconstruction, prediction, any graph, MCP or `ev.RELATIONS` change, commits newer than the
snapshot, and sha256 repositories.
