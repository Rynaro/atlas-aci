# Counterfactual analysis: what would this patch change?

`atlas-aci counterfactual` takes a proposed unified diff and an indexed **base snapshot** and
answers one question: which declarations and which architecture rules would the patch change, if
it were applied? It applies the patch to the base snapshot's own source bytes, indexes the result
into a temporary namespace outside your repository, compares the two snapshots, and discards the
temporary namespace. The answer is a canonical report labelled **hypothetical**.

It is an operator command only. There is no MCP operation and no new tool: the tools JSON is
byte-identical. It never applies the patch to your working tree, never touches git (refs, index,
worktree) and never publishes a snapshot.

```sh
atlas-aci index --repo /path/to/repo --graph-root /path/to/graphs
atlas-aci counterfactual --repo /path/to/repo --graph-root /path/to/graphs \
  --base-snapshot <64-hex snapshot id> --patch change.patch [--rules rules.json] \
  [--scratch-root DIR] [--timing] [--max-...]
```

The snapshot id is the one `atlas-aci index` logs (`index_done`) and the one every snapshot
manifest carries. It **must be the active snapshot** of the namespace: a patch is only meaningful
against the exact base it was written for.

## Read the result correctly

* **The report is hypothetical.** `hypothetical.kind` is `hypothetical` and `published` is
  `false`. `hypothetical_snapshot_id` names a state that is stored nowhere: asking the index for it
  fails. The `staged` block carries the id of the temporary build; it is informational and also
  names nothing that survives the run.
* **Exit 0 means the analysis ran**, not that the patch is harmless. Read the three `analyses`
  statuses: `complete`, `incomplete` (with closed `reasons`) or `not_requested` (architecture
  without `--rules`). Exit 1 is a typed rejection, exit 2 is usage. Nothing is printed on exit 1 or 2.
* **A clean-looking delta is never a hidden gap.** Anything the analysis could not finish inside
  its scope or bounds is `incomplete`, and the reasons say why.
* The analyzer reports. It does not enforce, approve or certify the patch.

## What happens, in order

The first failure wins; nothing is printed on failure, and no temporary directory exists before
step 8.

1. Limits are normalised (usage, exit 2).
2. The patch is parsed (`Counterfactual patch rejected: <reason>`): global byte scans, then the
   sequential grammar.
3. `--rules` is validated, if given (`Architecture rules rejected: invalid_rule: ...`).
4. The scratch root is checked (usage, exit 2).
5. The base is leased read-only (`mode=ro`, no writer lock) and checked.
6. The patch is applied to the base's hash-verified source pack bytes, file by file in path order.
7. Base size bounds and a metadata-only live-symlink preflight run.
8. The patched tree is indexed into a fresh namespace under the scratch root (the **staged
   snapshot**). The indexer is told to enumerate nothing: every byte comes from the base pack plus
   the patch. Untracked or edited files in your working tree are never read, so the report does
   not depend on a dirty checkout.
9. The two snapshots are checked for compatibility and the S19 change set is computed through the
   S20 count-only admission bounds.
10. The S31 rule check runs on both sides and the results are compared.
11. The base is re-verified, the report is assembled and validated.

Stages 2 to 7 happen before any staged source is indexed (AC-005).

## Patch grammar

A strict, pure-Python parser over bytes; there is no `git apply` or `patch` process. Application is
exact: there is no fuzz and no offset search. Line endings are LF; a CR is content (a CRLF base file
keeps its CR bytes exactly).

* Headers may be git-style (`diff --git a/X b/X`, `index`, `new file mode`, `deleted file mode`) or
  plain (`--- a/P` / `+++ b/P`, with an optional tab and timestamp). Only `-p1` paths are accepted:
  `a/` and `b/` prefixes are mandatory; `/dev/null` marks a creation or deletion.
* A creation has one hunk `-0,0 +1,N` of `+` lines; a deletion has one hunk `-1,N +0,0` of `-`
  lines. A git section with `new file mode` / `deleted file mode` and no hunk creates or deletes an
  empty file.
* `\ No newline at end of file` marks the preceding body line as lacking its final newline. After a
  context line it applies to both sides; after `-` or `+` to that side; it may sit between a `-` run
  and a `+` run. A leading marker, a second marker for one line, or any later line on a closed side
  is `malformed_patch`.
* A commit-message preamble and a `-- ` signature trailer are ignored.
* The optional git `index H1..H2` line is checked: H1 must be a prefix of the git blob id of the
  base bytes (SHA-1 or SHA-256 object format) and H2 of the result's.

### Rejection reasons

Patch rejections (`Counterfactual patch rejected: <reason>`), in the order they are checked:

| Stage | Reason | Meaning |
| --- | --- | --- |
| global | `limit_exceeded` | patch larger than `max_patch_bytes`, a line longer than `max_line_bytes`, more than `max_files` files, more than `max_hunks` hunks, or a result larger than `max_result_file_bytes` |
| global | `binary_patch` | a NUL byte, `GIT binary patch`, or `Binary files ... differ` |
| global | `not_utf8` | the patch is not valid UTF-8 (this includes patches against non-UTF-8 sources) |
| parse | `malformed_patch` | grammar violation, count mismatch, unordered or overlapping hunks, inconsistent positions |
| parse | `unsupported_patch_feature` | renames, copies, similarity headers, mode-only changes |
| parse | `symlink_change` | any mode `120000` |
| parse | `submodule_change` | any mode `160000` |
| parse | `unsupported_mode` | a mode other than `100644` or `100755` |
| parse | `invalid_path` | not repository-relative, `..`, absolute, backslash, control character, a `.git` component, a quoted path, or different `a/` and `b/` paths |
| parse | `duplicate_path` | one path in two sections |
| base | `unsupported_path` | the suffix is not an indexable source suffix, or a path component is a skipped directory (`node_modules`, `tmp`, ...) |
| base | `path_not_in_base` | a modification or deletion of a path the base did not observe |
| base | `path_exists_in_base` | a creation of an observed path, or a file/directory prefix collision |
| base | `base_mismatch` | the context does not match the base exactly, the old `index` id is wrong, or a deletion leaves content |

Non-ASCII file names are accepted only when git did not quote them: git quotes them by default
(`core.quotePath`), and a quoted header is `invalid_path`. Generate patches with
`git -c core.quotePath=false diff --no-renames --no-ext-diff --src-prefix=a/ --dst-prefix=b/`.
Renames are rejected, so use `--no-renames`; a renamed path shows as a removal plus an addition.

### Base rejections

`Counterfactual analysis rejected: <reason>`:

* `index_unavailable`, `source_snapshot_unavailable` (including a corrupt or missing source pack),
  `index_upgrade_required`;
* `base_snapshot_mismatch`: `--base-snapshot` is not the active snapshot;
* `base_not_buildable`: the active generation is not a plain `build` generation, **or** its
  observations carry retained **history** records (see below);
* `repository_identity_mismatch`, `configuration_mismatch`, `producer_mismatch`: the base was
  produced for another repository path, configuration or running code;
* `base_too_large`: see the limits;
* `live_path_is_symlink`: a path that would be staged is a symlink in the live checkout (or sits
  below one). Only symlink metadata is read.

Bases built by import (`atlas-aci import`), SCIP import or coverage import are rejected as
`base_not_buildable`: they have no authenticated source bytes to stage. A base whose history was
retained by an incremental re-index (`index --since`) after a file failed to parse is also rejected
as `base_not_buildable`: the retained facts of that file cannot be reproduced by a from-scratch
staged build, so the two sides would not be comparable. Re-index the base from a clean state, or
fix the file and re-index; once the file indexes again the history is gone.

## The report

Format `atlas-counterfactual-report.v1` (`analyzer_version` `atlas-counterfactual.v1`), canonical JSON plus a newline, repo-relative paths only,
no clock, host or temporary path. `report_id` is the content address of the report.

```text
hypothetical   kind, published=false, base_snapshot_id, base_generation_id, patch_sha256,
               configuration_sha256, producer_sha256, analyzer_version, hypothetical_snapshot_id
base / staged  snapshot and generation ids (staged: published=false, informational=true)
compatibility  the S19 compatibility block
patch          sha256, bytes, totals, files[]: path, op, mode, hunks, added, removed,
               base_sha256, patched_sha256, base_bytes, patched_bytes (sorted by path)
effective_limits   patch, base, diff, report, architecture
analyses       extraction | source_diff | architecture: status + reasons
extraction     counts, failed[] (path, patched, reason), failed_total
source_diff    change_set (the S19 change set), diff_work,
               partial_paths_touched, partial_paths_preexisting
architecture   null, or rules_format, rules_sha256, before/after report ids and embedded S31
               reports, and one delta per rule
limitations    every flag below
```

### Identity

`hypothetical_snapshot_id` is derived from exactly: the base snapshot id, the base generation id,
the patch digest, the producer configuration (configuration and producer hashes), the analyzer
version, `kind` and `published`. **Limits are not part of it.** Different `--max-*` values that both
admit the patch give the same hypothetical id but a different `report_id`; the limits are recorded
in `effective_limits`. A one-byte change to the patch, or a rebuilt base, changes the id.

### Analyses and their statuses

Statuses: `complete`, `incomplete`, `not_requested`.

* `extraction`: `incomplete` with `staged_extraction_failed` when any staged file failed to parse.
  `failed[].patched` says whether the patch touched it.
* `source_diff`: `incomplete` with any of `diff_entity_limit`, `diff_occurrence_limit`,
  `changed_file_limit` (the S20 count-only admission bounds; `change_set` is then `null`),
  `partial_paths` (a path in `partial_paths_touched`) or `unknown_changes` (an S19 `unknown`
  category). `partial_paths_touched` are paths that are not `indexed` (failed, unsupported or disabled) on
  exactly one side, or that the patch touches and that are not `indexed` on either; `partial_paths_preexisting` are paths not indexed on both sides that the patch does
  not touch. Only the first list makes the diff incomplete; both are listed.
* `architecture`: `not_requested` without `--rules`, otherwise `complete` or `incomplete`.

### Architecture delta

With `--rules`, both snapshots are evaluated with identical rule bytes and bounds, and each rule
gets a delta keyed by `(rule id, source_path, target_path)` (hop ids embed per-snapshot ids and are
not used). `persisting` is always sound. `introduced` is listed only when the **before** side is
exhaustive and `resolved` only when the **after** side is; otherwise they are `null`. A side is
exhaustive when every violating pair has an emitted witness and the rule has no reasons.

A rule delta is `incomplete` with reasons from `counterexamples_truncated`, `rule_budget_limited`
and `rule_scope_unknown`. Two properties are by design and worth knowing:

* **Empty selections are treated as incomplete.** A rule whose selection becomes empty on a side
  (`empty_source_selection` or `empty_target_selection`; for example the patch deletes the last
  file of the rule's `from` group) is not exhaustive, so `resolved` is `null` even though the
  resolution is certain. This conservative null is deliberate and is not special-cased.
* **A non-null list is a lower bound when the other side is incomplete.** For example with
  `--max-counterexamples-per-rule 1` a non-null `introduced` is a witnessed subset of the introduced
  pairs, not the full set. The `status` of that rule says so.

The delta inherits the S31 limitations: real repositories mostly yield `rule_scope_unknown`
because only package-local relative imports resolve (see `docs/architecture-rules.md`).

## Limits

All limits are command options; out of range is a usage error (`Invalid counterfactual limit:
<name>`). Passing a rule limit without `--rules` is also a usage error.

| Group | Option | Default | Range |
| --- | --- | --- | --- |
| patch | `max_patch_bytes` | 262144 | 1..262144 |
| patch | `max_files` | 32 | 1..32 |
| patch | `max_hunks` | 256 | 1..1024 |
| patch | `max_line_bytes` | 8192 | 1..65536 |
| patch | `max_result_file_bytes` | 1048576 | 1..8388608 |
| base | `max_base_files` | 20000 | 1..200000 |
| base | `max_base_bytes` | 268435456 (256 MiB) | 1..2147483648 |
| report | `max_listed_failures` | 64 | 0..1024 |
| diff | `max_diff_entities` | 50000 | 0..10^9 |
| diff | `max_diff_occurrences` | 250000 | 0..10^9 |
| diff | `max_changed_files` | 200 | 0..10^9 |
| rules | `max_depth`, `max_visited_nodes`, `max_examined_edges`, `max_total_examined_edges`, `max_sources_per_rule`, `max_counterexamples_per_rule`, `max_boundaries_per_rule` | S31 | S31 |

Options are spelled with dashes (`--max-hunks`). `max_base_bytes` counts each distinct source blob
once, because identical blobs are shared in memory. A base of more than 20000 observed files or
256 MiB of distinct source is `base_too_large` unless you raise the bounds; see the cost below
before you do.

## Cost

Staging **re-indexes every observed base file** with a cold extraction cache (parse, resolve and
pack), and all distinct base bytes are held in memory. Seeding the cache from the base is a
follow-up. Measured in the dev container (Python 3.12, arm64, single process):

| Base | Files | Bytes | Staged build | S19 change set | Peak RSS |
| --- | --- | --- | --- | --- | --- |
| this repository (one added declaration, one rule, two S31 sides: 17 s) | 623 | 5.4 MB | 70 s | 9 s | 305 MB |
| synthetic python tree | 2040 | 1.1 MB | 62 s (30 ms/file) | 8 s | 235 MB |
| synthetic python tree | 5100 | 2.8 MB | 198 s (39 ms/file) | 25 s | 484 MB |

The cost is roughly linear in the number of files, about 30 to 40 ms per file here. The default
`max_base_files` of 20000 therefore implies on the order of 12 to 15 minutes of staging. Lower the
bounds to fail fast on big bases.

## Failures

`Counterfactual analysis failed: <reason>[: <detail>]`:

* `staging_failed`: the temporary build failed (including a parser or grammar failure);
* `staged_incompatible`, with a closed S19 compatibility reason as detail, or `index_unavailable`
  for an unexpected failure while diffing;
* `base_changed`: the active snapshot changed while the analysis ran;
* `architecture_failed`, `report_invalid`, `analysis_failed`: the rule check, the report validation or
  an otherwise unclassified stage failed.

The staged build does **not** probe git. Its manifest records the repository identity of the
**base** (taken from the base manifest), so the staged snapshot id never depends on live git state:
a `git` that refuses the checkout (ownership checks on a bind mount whose reported owner changes
between runs), is slow, or is missing cannot change the result. The analyzer starts no process.
`staged_incompatible` therefore carries only a closed S19 compatibility reason such as
`identity_kind_mismatch` if the two manifests ever disagree. Any other `OSError`, `ValueError` or
`sqlite3.Error` reaching the CLI becomes `Counterfactual analysis failed: <TypeName>`.

The CLI's own messages are fixed text and never echo a path or patch content. Build logs written
to stderr by the indexer may contain paths. The exit-1 texts, with an empty stdout:

```text
Error: Counterfactual patch rejected: base_mismatch
Error: Architecture rules rejected: invalid_rule: <reason>[ (rule N)]
Error: Counterfactual analysis rejected: base_not_buildable
Error: Counterfactual analysis failed: staged_incompatible: identity_kind_mismatch
Error: Counterfactual patch file cannot be read.
Error: Architecture rules file cannot be read.
```

Exit 2 (usage): `Invalid counterfactual limit: <name>`; `--base-snapshot must be a 64-character
lowercase hex snapshot id.`; `The scratch root must be an existing directory outside the repository
and its graph root.`; `The repository cannot be opened as a graph namespace.`

## Library and logging

`atlas_aci.counterfactual.analyze.run(Side(repo, graph_root), base_snapshot_id, patch_bytes, ...)`
is the same function the CLI calls. Only typed exceptions cross its boundary:
`PatchRejected`, `rules.InvalidRule`, `CounterfactualUsageError`, `CounterfactualRejected`,
`CounterfactualFailed`. The staged build emits **structlog** records; the CLI configures them to
stderr, a library caller must configure structlog itself or they go to structlog's default
(stdout).

`--timing` prints `Counterfactual timing: elapsed_ms=<int>` to stderr after the report has been
written to stdout; it never reaches the report. Build log lines may precede it on stderr.

`--scratch-root` is the directory under which the temporary namespace is created (default: the
system temporary directory). It must exist and must not be inside the repository or the active graph
root. The temporary namespace is removed on every exit path, and the scratch root returns to its
previous contents.

## Guarantees and non-guarantees

Read-only: the base is opened `mode=ro`; nothing under the repository, `.git`, the active graph root
(database, source packs, manifests, extraction cache) or the scratch root outlives a run. The
report is deterministic: the same base snapshot, patch bytes, limits and rule document give
byte-identical output, independent of the scratch path and the interpreter.

### Limitations

Each is a flag in the report's `limitations` object.

* `hypothetical_snapshot_is_never_published`: the hypothetical id names no stored object.
* `live_working_tree_content_is_not_read`: only symlink metadata of staged paths is read.
* `patch_applies_exactly_without_fuzz_or_offset`.
* `renames_copies_binary_symlink_submodule_and_mode_changes_are_rejected`.
* `only_indexable_source_paths_can_be_patched`: for example `pyproject.toml` or `package.json`
  cannot be patched, because they cannot be verified from the snapshot.
* `staging_reindexes_every_observed_file`: see Cost.
* `architecture_delta_inherits_s31_limitations`.
* `incomplete_rule_sides_never_yield_introduced_or_resolved`.
* `empty_selection_sides_are_treated_as_incomplete_by_design`.
* `non_null_introduced_or_resolved_lists_are_lower_bounds_when_the_other_side_is_incomplete`.
* `a_path_rename_appears_as_removal_plus_addition`.
* `case_insensitive_path_collisions_are_not_modeled`.
* `bases_with_retained_history_or_imported_provenance_are_rejected`: history from `--since`, import,
  SCIP and coverage bases.
* `quoted_git_paths_and_non_utf8_patches_are_rejected`.
* `first_use_grammar_download_outside_the_image`: the tree-sitter grammars may be downloaded on first
  use; the container image prefetches them, so run offline analyses there.
* `not_exposed_over_mcp`.
