---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T07:00:00Z
slice_id: ACI-S32
issue: https://github.com/Rynaro/atlas-aci/issues/53
---
# ACI-S32 — analyze a proposed patch in an isolated hypothetical snapshot

## Scope

Issue #53 on base `79ae0b0` (main after PR #89 / ACI-S31), branch `aci-s32`. The marker `<!-- atlas-aci:slice:ACI-S32 -->` is verified. There is no prior S32 plan, receipt or execution state. The frozen criteria hash to `1d713204b1c51813227214cd35a204df6339cf13650037a259f3311d7d02cd8e` (7 product ACs plus RUN-01/02), which matches the contract table. Predecessors on base (ancestors with COMPLETE receipts):
- S08 (#30): `CodeGraph.build(overlays=…)`, source packs;
- S19 (#40): `query/diff.py`;
- S31 (#52): `architecture/{rules,checker}.py`.

S20 (`query/impact.py`) is also merged and is reused.

Observed facts (re-verified at planning; `M/` = `mcp-server/src/atlas_aci/`):
- **Overlays.** `build(since, *, overlays)` (`M/codegraph.py:2442`) accepts a path→bytes mapping.
  - Validation (2475-2487): `ev.relative_path`, bytes, `suffix in LANG_BY_EXT`, no `DEFAULT_SKIP_PATTERNS` component, and a live `is_symlink()` check on each parent. That check is the ONLY live-tree access for overlay paths.
  - The walk is `set(self._iter_source_files()) | overlay paths` (3165-3168). `rel in self._overlays` takes the mapping bytes with mtime 0 (3186-3188).
  - `_iter_source_files` (3663-3692) is the only live enumeration (`rglob`, all files; the language filter is at 3170). Its single call site is 3166.
  - `providers/python_imports.py` is pure (`PurePosixPath`), so package markers come from observed paths, not the disk.
- **Identity.**
  - Snapshot context = `{repository, schema, configuration, producer}` + `identity` (1268, 3553). `snapshots.identity` (`storage/snapshots.py:38-64`) holds root and git worktree/git_dir/common_dir (no HEAD).
  - The snapshot id is the sha256 of the canonical manifest (`snapshots.py:909-911`). The graph root appears in neither.
  - `test_s08_contract.py:157-165`: an overlay build's snapshot id equals the later real build of the same bytes, so mtime is not identity.
  - `_producer_inputs` (1440-1463) hashes the codegraph module's own `__file__` plus 8 module files and package versions. A subclass in another module cannot change it.
  - `_build_inputs` (2338-2351) gives configuration = `{languages: sorted(langs), skip_patterns: list(DEFAULT_SKIP_PATTERNS)}`; `SCHEMA_EPOCH = 9` (341); `EXPECTED_DDL_HASH` (548).
- **Source pack.** `SourceLease.read(path, sha)` (`snapshots.py:658-678`) returns checksum-verified bytes. Every observed path is packed, whatever its state (`builder.add` at 3192 precedes the language checks).
- **S19.**
  - `_open`/`_lease` (`M/query/diff.py:192-209`).
  - `_check_compatible` (212-245) requires the same identity kind, the same git `common_dir` (or the same root), equal schema and configuration, equal producer, and running package versions. It does NOT compare producer source hashes with the running code.
  - `compare` (402-434); `canonical_bytes` (587).
- **S20.** `impact.change_set(before_reader, after_reader, limits)` (`M/query/impact.py:211-236`) runs `_check_compatible`, then `_admit_diff` (143-208, count-only: `max_entities`, `max_occurrences`, `max_changed_files`), then `_Loaded`/`_build`.
  - Failures raise `ImpactError` with code `SNAPSHOT_DIFF_REJECTED` or `IMPACT_DIFF_TOO_LARGE` and a reason in {`diff_entity_limit`, `diff_occurrence_limit`, `changed_file_limit`}.
  - `DEFAULT_DIFF_LIMITS` = 50000 / 250000 / 200 (`impact_limits.py:30-34`).
- **S31.**
  - `checker.run(side, rule_bytes, limits)` (`M/architecture/checker.py:825`) runs, in order: `normalize_limits` (137) → `rules.load` → `open_snapshot` (165) → `check(reader, ruleset, limits)` (539) → `validate_report` (749).
  - The per-rule report (511-536) has `outcome`, `reasons`, `counts.violating_pairs` (all pairs, including beyond the emitted cap), `counterexamples[]` (`source_path`, `target_path`), `BUDGET_REASONS` and `UNKNOWN_REASONS`.
- **Tests.**
  - `tests/slices/test_s19_support.py`: `write_tree`, `init_git`, `build`, `inventory` (92-104, which includes `.git/`).
  - S31 surface/AST tests: `test_s31_architecture_rules.py:1131-1220`.
  - `test_thesis_negatives` scans all of src for "alter table" and LLM imports.
- **Pins.** Tools JSON: 7 tools, sha `94f9567a…a56ac56`, 1,631,472 bytes. MINIMUM 3436, COLLECTION 3448, FIXED_ERROR 797, IMPACT 7730.
- **Interpreters.** CI runs Python 3.14; the dev image runs 3.12.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)
1. **Staging = option B, a base-pack view.**
   - `_StagingGraph(CodeGraph)` lives in `counterfactual/analyze.py`. It has the same repo path, `langs` from the base configuration, and `graph_root` = a fresh `TemporaryDirectory` outside the repo.
   - It overrides `_iter_source_files` to yield nothing.
   - `build(overlays=…)` receives every surviving base-observed path's bytes from the base pack (hash-verified), with patched bytes substituted, creates added and deletes omitted.
   - The live tree is never read for content.
   - No edits to codegraph, storage, extractors or providers.
   - A (plain live overlays) and C (physical temp copy) are rejected (see Rejected Alternatives).
2. **Base.**
   - `--base-snapshot` is REQUIRED and must equal the leased active `snapshot_id`.
   - The generation kind must be `build`.
   - Schema, producer and configuration must equal the running ones.
   - Typed early rejection otherwise. The base is re-verified after analysis.
3. **Patch.** A strict pure-Python unified-diff parser and applier over bytes (D2-D4). No `git apply`/`patch` process. Closed reasons; paths and patch text are never echoed.
4. **Diff.** S20 `impact.change_set` with count-only bounds. Admission overflow → `source_diff` incomplete. The full S19 change set goes into the report.
5. **Rules.**
   - `--rules` is optional; without it, `not_requested`.
   - Both sides use identical bytes and limits. The delta is keyed by `(rule_id, source_path, target_path)`.
   - An incomplete side never yields claims of introduction or resolution.
6. **Report.** `atlas-counterfactual-report.v1` with the identity block and `hypothetical_snapshot_id`. The staged real ids are informational. Per-file patch summary, `analyses` status, a content-addressed `report_id`, and canonical bytes + LF. No clock; `--timing` goes to stderr only.
7. **Read-only.** The worktree, `.git`, the active graph root and the scratch root are byte-unchanged, and the temp dir is removed on every path. A scratch root inside the repo or the graph root → usage error.
8. **CLI.** `atlas-aci counterfactual`, with lazy imports. Exit 0 for a report, exit 1 for a typed rejection (fixed text), exit 2 for usage. Never `str(exc)`.
9. **Packaging.** `counterfactual/__init__.py` by amendment. Fixtures under `tests/fixtures/counterfactual/`; trees are built in-test. Extraction-cache seeding is OUT (follow-up).
10. **Invariance and AST bans** as in G-SRC and G-AST below.

### In
1. `counterfactual/__init__.py`: a docstring only.
2. `counterfactual/patch_input.py` (pure):
   - `PATCH_LIMITS`, `PATCH_REJECTIONS`, `PatchRejected`, `PatchUsageError`;
   - `Hunk`, `FilePatch`, `Patch`;
   - `parse(raw, limits)`, `check_scope(...)`, `apply(...)`, `git_blob_ids(data)`.
3. `counterfactual/analyze.py`:
   - `LIMITS` and `normalize_limits`;
   - `CounterfactualRejected`, `CounterfactualFailed`, `CounterfactualUsageError`;
   - `_StagingGraph`;
   - `run(side, base_snapshot_id, patch_bytes, *, rule_bytes=None, limits=None, scratch_root=None)`;
   - `rule_delta`, `report_id`, `hypothetical_snapshot_id`, `validate_report`, `canonical_bytes`, `LIMITATIONS`.
4. `__main__.py`: the `counterfactual` command.
5. `tests/slices/test_s32_counterfactual.py`: the 7 AC tests plus supporting and isolation tests. Fixtures go in `tests/fixtures/counterfactual/*` (flat).
6. `docs/counterfactual-analysis.md`, a CHANGELOG Unreleased entry and a README docs-table row.

### Out
- Applying patches to the worktree, refs, index pointer or remotes; publishing staged generations; MCP exposure.
- Running tests, builds, installers or application code; network.
- Renames and copies (v1); binary, symlink, submodule and mode changes; non-indexable paths (json, toml, …); multi-repo patches.
- Fuzz/offset application; quoted (C-escaped) git paths; patches read from stdin.
- Extraction-cache seeding (follow-up); incremental staging from the base DB.
- Edits to the G-SRC paths.

Declared implementation paths (issue): `counterfactual/patch_input.py`, `counterfactual/analyze.py`, `__main__.py`, `tests/slices/test_s32_counterfactual.py`, `tests/fixtures/counterfactual/`, `docs/counterfactual-analysis.md`.

Scope amendment `.spectra/changes/aci-s32/scope-amendment.json` (recorded before implementation) adds `counterfactual/__init__.py`, `CHANGELOG.md`, `README.md` and the Ramza, receipt and backlog files.

Rightsize: FULL (score 5): files est. 9; public CLI; novel; high stakes. An independent checker and a ramza critic run before Assemble.

## Approach

### Decisions

**D1 Pipeline and stage order.** The first failure wins, and nothing is printed on failure.

| # | Stage | Touches | Failure |
| --- | --- | --- | --- |
| 1 | Limits normalised | — | usage exit 2 |
| 2 | Patch P0/P1 (`parse`) | patch bytes only | `PatchRejected` |
| 3 | `rules.load` (if `--rules`) | rule bytes only | `InvalidRule` |
| 4 | Scratch-root check | path metadata | usage exit 2 |
| 5 | Base lease + checks (D5) | base graph, read-only | `CounterfactualRejected` |
| 6 | Patch P2 (`check_scope` + `apply` against pack bytes) | base pack | `PatchRejected` |
| 7 | Base size bounds + live symlink preflight | pack index, `lstat` | `CounterfactualRejected` |
| 8 | Staging build (D6) | temp dir only | `CounterfactualFailed` |
| 9 | Compatibility + S20 change set (D7) | both leases | `CounterfactualFailed` / incomplete |
| 10 | S31 both sides + delta (D8) | both leases | incomplete |
| 11 | Re-verify base, assemble, `validate_report` | — | `base_changed` |

AC-005's "before indexing" boundary = stages 2-7. No temp directory exists before stage 8.

One `ExitStack`:
1. enter `TemporaryDirectory` first (so it is cleaned last);
2. base lease;
3. staging graph (closed right after `build`);
4. staged lease.

Every exception path unwinds the leases and removes the temp dir.

**D2 Patch grammar** (bytes; lines split on `\n` only; CR is content; ASCII digits only via bytes regexes).

```
patch    := preamble? section+ signature?
preamble := lines before the first line starting "diff --git " or "--- " (commit message;
            ignored except by the P0 global scans)
section  := git_hdr ext* (files hunk+)? | files hunk+
git_hdr  := "diff --git a/X b/X"        X must equal the ---/+++ path; X derived by
            rest="a/X b/X", X=rest[2:(len(rest)-1)//2], require rest==f"a/{X} b/{X}"
ext      := "index H1..H2[ MODE]" | "new file mode MODE" | "deleted file mode MODE"
          | "old mode MODE" | "new mode MODE"
          | "similarity index" | "dissimilarity index" | "rename from|to" | "copy from|to"
            (these last four → unsupported_patch_feature)
            each ext at most once; any other line before ---/@@ → malformed_patch
files    := "--- " (a/P | /dev/null) ["\t" any] LF "+++ " (b/P | /dev/null) ["\t" any] LF
            (-p1 only; a header path without the a/ or b/ prefix → malformed_patch;
             a leading '"' → invalid_path)
hunk     := "@@ -S[,C] +T[,D] @@" [" " any] LF body
            S,T,C,D = [0-9]{1,9}; an omitted count means 1
body     := (" "|"-"|"+") content LF | "\ " any LF
            "\ " marks the preceding body line as having no newline; at most one per side.
            Reading stops when the old/new remaining counts reach 0, plus one optional
            trailing "\ " line.
            Each hunk needs at least one +/- line. An empty line or any other line inside
            a body → malformed_patch (header-count mismatch).
signature:= "-- " LF any*   (git format-patch trailer, only after the last section)
```

Operations:
- **modify**: `a/P` + `b/P`. Mode ext lines are absent, or `index … MODE` with MODE ∈ {100644, 100755}.
- **create**: `/dev/null` + `b/P` + `new file mode M`. Exactly one hunk `-0,0 +1,N` of `+` lines. A git-only section with `new file mode` and no hunks creates an empty file.
- **delete**: `a/P` + `/dev/null` + `deleted file mode M`. Exactly one hunk `-1,N +0,0` of `-` lines. A git-only section with no hunks deletes an empty file.
- **Plain sections** (no git header) infer the op from `/dev/null`, with mode = null.
- A `/dev/null` without the matching git mode line in a git section, or a git-only section without new/deleted file mode → malformed_patch.

Mode rules:
- 120000 anywhere → `symlink_change`;
- 160000 → `submodule_change`;
- other values not in {100644, 100755} → `unsupported_mode`;
- `old mode`/`new mode` between regular modes → `unsupported_patch_feature`.

**D3 Closed rejection vocabulary, in stage order.**
- **P0, global scans in this order** (first hit wins):
  1. `len(raw) > max_patch_bytes` → `limit_exceeded`;
  2. a NUL byte → `binary_patch`;
  3. strict UTF-8 decode fails → `not_utf8`;
  4. any line equal to `GIT binary patch`, or matching `^Binary files .* differ$` → `binary_patch`;
  5. any line longer than `max_line_bytes` → `limit_exceeded`.
- **P1, sequential parse** (first failure in patch order):
  - `malformed_patch`: no section, grammar violation, count mismatch, hunks not strictly ordered or overlapping by old range;
  - `unsupported_patch_feature`, `symlink_change`, `submodule_change`, `unsupported_mode`;
  - `invalid_path`: fails `ev.relative_path`; contains C0/DEL, NUL or a backslash; has a `.git` component; is quoted; or the `a/` and `b/` paths differ;
  - `duplicate_path`;
  - `limit_exceeded`: files > `max_files`, or hunks > `max_hunks`.
- **P2, base-relative and pure over pack bytes**, per file in sorted path order:
  1. `unsupported_path`: the suffix is not in `LANG_BY_EXT`, or a component is in `DEFAULT_SKIP_PATTERNS` (applies to every op);
  2. `path_not_in_base`: a modify/delete target is not an observed base path;
  3. `path_exists_in_base`: a create target is observed;
  4. `base_mismatch`: see D4, plus the old blob-id check;
  5. `limit_exceeded`: a result is larger than `max_result_file_bytes`;
  6. `malformed_patch`: inconsistent new-side positions, an interior line with no LF, or a new blob-id mismatch.
- **Analysis rejections** (stages 5 and 7; `CounterfactualRejected.reason`):
  - `index_unavailable`, `source_snapshot_unavailable`, `index_upgrade_required`;
  - `base_not_buildable`, `base_snapshot_mismatch`;
  - `repository_identity_mismatch`, `configuration_mismatch`, `producer_mismatch`;
  - `base_too_large`, `live_path_is_symlink`.
- **Failures** (`CounterfactualFailed.reason`): `staging_failed`, `staged_incompatible` (carrying the closed S19 reason as `detail`), `base_changed`.

Exception messages are exactly the reason code: no paths, no patch text.

**D4 Applier** (`apply(fp, base: bytes|None, limits) -> bytes|None`; pure; no recursion; O(lines)).
```
L = base.splitlines(keepends=True) on b"\n" only (manual split; b"" → [])
out = []; cur = 0
for h in hunks:                                   # validated ordered in P1
  old = [c + (b"" if nonl else b"\n") for tag,c,nonl in h.lines if tag in b" -"]
  new = [c + (b"" if nonl else b"\n") for tag,c,nonl in h.lines if tag in b" +"]
  anchor = h.S if h.C == 0 else h.S - 1           # C>0 requires S>=1; C==0 allows S>=0
  require cur <= anchor <= len(L)                  else base_mismatch
  out += L[cur:anchor]
  require (h.T if h.D == 0 else h.T - 1) == len(out)       else malformed_patch
  require L[anchor:anchor+len(old)] == old         else base_mismatch   # exact, no fuzz
  out += new; cur = anchor + len(old)
out += L[cur:]; result = b"".join(out)
require every element of out except the last ends with b"\n"   else malformed_patch
delete: require result == b"" else base_mismatch → returns None; create: base = b""
```
Optional `index H1..H2`:
- H1 must be a prefix of the git SHA-1 or SHA-256 blob id of the base bytes (`b"blob %d\0" % len + data`), else `base_mismatch`. All zeros means create.
- H2 must be a prefix of the result's blob id, else `malformed_patch`. All zeros means delete.

**D5 Base checks** (`_open_base`, a local copy of the `open_snapshot` order with the kind check moved earlier):
1. `diff._open`; then `diff._lease` (`DiffRejected` → the same reason).
2. Epoch: `_validated_lease` and `_lease_schema_epoch == SCHEMA_EPOCH`, else `index_upgrade_required`.
3. `generation_inputs.kind == "build"`, else `base_not_buildable`. This covers import, coverage_import, scip_import and legacy generations; imports are checked before their null snapshot.
4. `snapshot_id is not None and == base_snapshot_id`, else `base_snapshot_mismatch`.
5. Let `ctx` = the base manifest context:
   - `ctx.schema == {epoch: SCHEMA_EPOCH, ddl_hash: EXPECTED_DDL_HASH}`, else `index_upgrade_required`;
   - `ctx.repository == str(side.repo.resolve())`, else `repository_identity_mismatch`;
   - `ctx.configuration == {languages: ctx.configuration.languages, skip_patterns: list(DEFAULT_SKIP_PATTERNS)}`, with languages non-empty, else `configuration_mismatch`;
   - `ctx.producer == reader._producer_inputs()` (the running code), else `producer_mismatch`.
6. Observations: `type=="observation"` records from `manifest.source_observations`, keyed by path, with hash `observed_source_sha256`.
7. Size bounds: `len(obs) > max_base_files`, or the sum of pack `index` lengths of observed hashes `> max_base_bytes` → `base_too_large`. Both are checked before any read.

`--base-snapshot` must match `^[0-9a-f]{64}$` (fullmatch), else usage exit 2.

**D6 Staging.**
```python
class _StagingGraph(CodeGraph):
    """Builds only explicit overlay bytes; never enumerates or reads the live checkout."""
    def _iter_source_files(self):
        return iter(())
```
- `staged = {p: lease.read(p, obs[p]["observed_source_sha256"]) for p in obs if p not in deleted}`. Patched bytes then replace their entries, and creates are added.
- Preflight, the same loop as codegraph 2483-2487: any `is_symlink()` on `repo/p` or a parent below the repo, for p in `staged` → `live_path_is_symlink`. This is metadata only.
- `g = _StagingGraph(repo, langs=ctx.configuration.languages, graph_root=tmp)`, then `g.build(overlays=staged)` (full; empty namespace, so no predecessor), then `g.close()`.
- `RuntimeError`, `ValueError`, `OSError` or `sqlite3.Error` → `CounterfactualFailed("staging_failed")`.
- The base bytes are dropped after the build.
- The staging build runs `snap.identity`'s `git rev-parse` with `GIT_OPTIONAL_LOCKS=0`, exactly as `atlas-aci index` does. The analyzer itself launches nothing.

Scratch:
- `scratch_root` defaults to `tempfile.gettempdir()`. It must be an existing directory and must not be within the repo or within the active graph namespace root (`side.graph_root or repo/.atlas`); otherwise `CounterfactualUsageError`.
- `TemporaryDirectory(prefix="atlas-counterfactual-", dir=scratch_root)`.

**D7 Diff.**
- Staged lease via `diff._open`/`diff._lease` on `Side(repo, tmp)`, with the D5 step-2 epoch gate.
- `compat = diff._check_compatible(base, staged)`. `DiffRejected` → `CounterfactualFailed("staged_incompatible", detail=reason)`.
- `change_set, _loaded, diff_work = impact.change_set(base, staged, diff_limits)`:
  - `IMPACT_DIFF_TOO_LARGE` → `change_set = null` and `source_diff` incomplete with `{exc.reason}`;
  - `SNAPSHOT_DIFF_REJECTED` → `staged_incompatible`.
- `source_diff` incomplete reasons, closed: `diff_entity_limit`, `diff_occurrence_limit`, `changed_file_limit`, `partial_paths` (non-empty `partial_paths`), `unknown_changes` (any `unknown` category).

**D8 Architecture delta** (only with `--rules`).
- `ruleset = rules.load(raw)` once (stage 3). Then `before = checker.check(base, ruleset, alim)` and `after = checker.check(staged, ruleset, alim)` on the held leases, and `checker.validate_report` on each. This is exactly `checker.run`'s body without reopening the base. G-S31-PARITY proves byte equality with `checker.run(Side)`.
- Per rule index i (the same rules on both sides): `exhaustive(side) = violating_pairs == len(counterexamples) and reasons == {}`.

```
B = {(s,t) in before witnesses}; A = {(s,t) in after witnesses}
persisting = sorted(B & A)                             # always sound (witnesses are sound)
introduced = sorted(A - B) if exhaustive(before) else null
resolved   = sorted(B - A) if exhaustive(after)  else null
status = complete iff exhaustive(before) and exhaustive(after)
reasons ⊆ {counterexamples_truncated, rule_budget_limited, rule_scope_unknown}  (either side;
          budget = reasons ∩ BUDGET_REASONS, unknown = reasons ∩ UNKNOWN_REASONS)
```
- Entry: `{index, id, before_outcome, after_outcome, status, reasons{}, before_exhaustive, after_exhaustive, counts:{before_violating_pairs, after_violating_pairs}, introduced|null, resolved|null, persisting}` (pairs are `{source_path, target_path}`).
- `architecture.status` = incomplete iff any rule is incomplete; its reasons are the union.

**D9 Identity and report `atlas-counterfactual-report.v1`.** Canonical JSON + LF; repo-relative paths only; no clock, host or temp path.
```
identity = {kind:"hypothetical", published:false, base_snapshot_id, base_generation_id,
            patch_sha256, configuration_sha256, producer_sha256,     # diff._sha of base ctx
            analyzer_version:"atlas-counterfactual.v1", limits:<effective PATCH_LIMITS>}
hypothetical_snapshot_id = sha256("atlas-counterfactual-snapshot.v1:" + canonical(identity))
report = {format, analyzer_version,
 hypothetical:{**identity, hypothetical_snapshot_id},
 base:{snapshot_id, generation_id, generation_kind:"build"},
 staged:{snapshot_id, generation_id, published:false, informational:true},
 compatibility:<S19 block>,
 patch:{sha256, bytes, files_total, hunks_total, lines_added, lines_removed,
        files:[{path, op, mode|null, hunks, added, removed, base_sha256|null,
                patched_sha256|null, base_bytes|null, patched_bytes|null}]},   # sorted by path
 effective_limits:{patch, base, diff, architecture|null},
 analyses:{extraction|source_diff|architecture:{status:"complete"|"incomplete"|"not_requested",
           reasons{}}},
 extraction:{counts:{observed, indexed, failed, unsupported, disabled},
             failed:[{path, patched, reason}] (sorted, ≤ max_listed_failures), failed_total},
 source_diff:{change_set|null, diff_work|null},
 architecture:null|{rules_format, rules_sha256, before_report_id, after_report_id,
                    reports:{before, after}, rules:[delta]},
 limitations:{flag:true…}, report_id}
report_id = sha256("atlas-counterfactual-report.v1:" + canonical(report − report_id))
```
- Extraction status is incomplete iff any staged observation is `failed` (reason `staged_extraction_failed`). The counts come from the staged lease's observations.
- Identity holds only patch limits: they decide whether the hypothetical snapshot exists. Analysis bounds change the report, not the snapshot.
- `validate_report` (the CLI runs it before printing) checks:
  - closed key sets;
  - status ⇔ reasons (complete ⇔ `{}`; `not_requested` ⇔ `architecture is null`);
  - null-list rules (D8);
  - sorted files;
  - `published` is false;
  - recomputing both `hypothetical_snapshot_id` and `report_id`.

**D10 Limits** (CLI flags; out of range → usage exit 2):

| Group | Name | Default | Low | High |
| --- | --- | --- | --- | --- |
| patch | `max_patch_bytes` | 262144 | 1 | 262144 |
| patch | `max_files` | 32 | 1 | 32 |
| patch | `max_hunks` | 256 | 1 | 1024 |
| patch | `max_line_bytes` | 8192 | 1 | 65536 |
| patch | `max_result_file_bytes` | 1048576 | 1 | 8388608 |
| base | `max_base_files` | 20000 | 1 | 200000 |
| base | `max_base_bytes` | 268435456 | 1 | 2147483648 |
| report | `max_listed_failures` | 64 | 0 | 1024 |
| diff | `max_diff_entities` / `max_diff_occurrences` / `max_changed_files` | 50000 / 250000 / 200 | 0 | 10^9 |
| architecture | S31 `LIMITS` (7 flags, S31 names) | S31 | S31 | S31 |

Passing architecture flags without `--rules` is a usage error.

Cost: one full re-index of every observed base file (parse, resolve, pack), plus an S19 `_Loaded.load` of both sides, plus 2× S31. Memory holds all base bytes (≤ `max_base_bytes`). Day-1 (g) measures this, and the docs state it.

**D11 Determinism.**
- Every list is sorted by explicit keys. Counters are counts.
- The temp path never reaches the report: the graph root is not part of snapshot/generation identity (Day-1 f).
- No `str(exc)`.
- 3.12/3.14 safety:
  - no recursion (iterative parser and applier);
  - no hash-order dependence (sorted sets);
  - bytes regexes (ASCII `\d`-free classes `[0-9]`);
  - `re.fullmatch` everywhere (`$` accepts a trailing newline);
  - bounded int parsing (≤ 9 digits);
  - no `TemporaryDirectory(delete=)` (3.12+ only).

**D12 CLI.** `atlas-aci counterfactual --repo R [--graph-root G] --base-snapshot ID --patch P [--rules F] [--scratch-root S] [limit flags] [--timing]`.
- Option types:
  - `--patch` and `--rules` use `click.Path(dir_okay=False, path_type=Path)` without `exists`. Reads take ≤ limit+1 bytes. An OSError gives the fixed text `Counterfactual patch file cannot be read.` / `Architecture rules file cannot be read.` (exit 1).
  - `--scratch-root` uses `click.Path(file_okay=False, path_type=Path)`.
- Exit-1 stderr texts, with stdout empty. The `except` clauses for subclasses come before `ValueError`:
  - `Counterfactual patch rejected: <reason>`;
  - `Architecture rules rejected: invalid_rule: <reason>[ (rule i)]`;
  - `Counterfactual analysis rejected: <reason>`;
  - `Counterfactual analysis failed: <reason>[: <s19 reason>]`;
  - fallback for `OSError`/`ValueError`/`sqlite3.Error`: `Counterfactual analysis failed: <TypeName>`.
- Usage, exit 2:
  - `Invalid counterfactual limit: <name>`;
  - `--base-snapshot must be a 64-character lowercase hex snapshot id.`;
  - `The scratch root must be an existing directory outside the repository and its graph root.`;
  - `The repository cannot be opened as a graph namespace.` (`DiffUsageError`).
- `--timing` measures `time.perf_counter_ns()` in `__main__` only (imported inside the branch) and prints `Counterfactual timing: elapsed_ms=<int>` to stderr after stdout is written.

**D13 Read-only.**
- The base is opened read-only (`mode=ro`), with no writer lock.
- Only the temp namespace is written. The scratch root returns to its exact prior inventory.
- Nothing under the repo, `.git`, or the active graph root (DB, `source-snapshots/` packs, manifests, `catalog.json`, extraction cache) changes.
- `hypothetical_snapshot_id` names no stored object.

### Limitations (static `LIMITATIONS`, emitted as flags and named in the docs)
1. `hypothetical_snapshot_is_never_published`
2. `live_working_tree_content_is_not_read` (only symlink metadata for staged paths)
3. `patch_applies_exactly_without_fuzz_or_offset`
4. `renames_copies_binary_symlink_submodule_and_mode_changes_are_rejected`
5. `only_indexable_source_paths_can_be_patched`
6. `staging_reindexes_every_observed_file`
7. `architecture_delta_inherits_s31_limitations`
8. `incomplete_rule_sides_never_yield_introduced_or_resolved`
9. `a_path_rename_appears_as_removal_plus_addition`
10. `case_insensitive_path_collisions_are_not_modeled`
11. `first_use_grammar_download_outside_the_image`
12. `not_exposed_over_mcp`

### Work plan (timebox 5 engineering days)

**Day 1 AM: qualification, before any product code.** A scratch script writes `.spectra/evidence/aci-s32/qualification.json`. It uses a git repo with the BASE tree (below), an external root R0, and a prototype `_StagingGraph` in the script.
- **(a) Unmodified staging.** Overlays of all R0 pack bytes go into temp T1. Record whether `snapshot_id` and `generation_id` equal R0's. Require:
  - `diff._check_compatible(R0, T1)` passes;
  - `impact.change_set` gives `changes == []` and `partial_paths == []`;
  - the observation vectors (path, state, hash) are equal.
  If the snapshot ids differ, record the differing manifest keys.
- **(b) Patched staging vs a real build.** The DECL edit is computed by hand. A real build of the same tree at the same path goes into R1. Record whether the snapshot and generation ids are equal, and whether `diff.compare(R0,T)` bytes == `diff.compare(R0,R1)` bytes.
- **(c) Producer and configuration.** `_StagingGraph(...)._producer_inputs() == CodeGraph(...)._producer_inputs()`, and the T1 context producer/configuration equal R0's.
- **(d) S31 on both sides.** `checker.check` on the R0 and T leases, and staged report bytes == `checker.run(Side(repo,R1))` bytes.
- **(e) Live-tree independence.** Add untracked `pkg/domain/zz.py`, delete `e.py` live and edit `b.py` live. Staging from R0 bytes gives T2 with the same snapshot id as (a), and `zz.py` is not observed.
- **(f) Determinism.** T1 and T3 in different scratch dirs give the same snapshot and generation ids.
- **(g) Cost.** Index the atlas-aci checkout itself into R_self, then time a staged build + change set + one S31 rule. Record wall ms, file count, total bytes and peak RSS (`resource`; scratch only).
- **Base invariants** go to `base-invariants.json`: tools sha and bytes, 3436/3448/797/7730, and the full pytest result at base.

STOP RULE:
- (a) fails (incompatible, non-empty changes, or different observations) or (c) differs → BLOCKED with the observed facts. Never mock.
- (e) or (f) differs → BLOCKED. Either the live tree leaks or identity is path-bound.
- (b) ids differ → AC-002/003 oracles compare change sets/reports with staged ids normalised. A test-only `_on_staged(side)` callback in `run` exposes the staged root for a direct `compare`. Record both in the evidence.
- (g) > 10 min → document it and keep v1 (seeding is the follow-up).

**Remaining days.**
- Day 1 PM - Day 2: `patch_input.py` (D2-D4) plus the grammar table, applier oracles and AC-005 P0-P2.
- Day 3: `analyze.py` (D5-D9, D13) plus AC-001/002/003.
- Day 4: CLI (D12), AC-004/006/007, isolation and AST tests, docs, CHANGELOG, README.
- Day 5: gates in the lock-faithful dev image, the production-image scenario, RUN-01/02, PR.

Cut order (each cut becomes a listed limitation):
1. README row;
2. the git `index` blob-id cross-check (lines are still parsed);
3. embedded S31 reports → `report_id`s only;
4. plain (non-git) headers;
5. the PYTHONHASHSEED subprocess replay;
6. `--timing`.

Never cut:
- stage order and closed reasons;
- the exact applier;
- live-tree independence;
- the base checks;
- temp cleanup;
- the incomplete-side rule;
- identity and `report_id`;
- the read-only, AST and invariance gates.

### Independent checker conditions
Pending. The orchestrator appends the independent checker and ramza critic findings here; they are binding and override conflicting text above.

## Independent checker conditions

Checker `atlas-s32-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s32.review.json`). Complexity verdict human_loop (11) and confidence VALIDATE (83.25): under the user's autonomous-execution instruction this independent review is the required validation and the substitute human gate; the critic record is author `orchestrator-ramza-plan-s32`, checker `atlas-s32-plan-checker`. Every finding below is a binding plan condition and overrides any conflicting text above.

Orchestrator resolutions of the open options (recorded 2026-11-04): condition 1 — Day-1 (a2) decides; unless (a2) proves snapshot-id AND empty-change-set equality on a base with history records/incremental lineage, the deterministic rule applies: any non-`observation` record in base `source_observations` → `CounterfactualRejected("base_not_buildable")` (closed vocabulary, docs, real-fixture test); condition 5 — `limits` are REMOVED from the identity hash (identity = base snapshot, base generation, patch digest, configuration, producer, analyzer version, kind/published); limits live only in `effective_limits`; condition 6 — split into `partial_paths_touched` (in the patch, or failed on exactly one side) and `partial_paths_preexisting`; only `touched` makes `source_diff` incomplete; both listed; condition 7 — keep the conservative null for `empty_*` selections (no special case) and document it as by design, together with the lower-bound semantics of non-null lists when the other side is non-exhaustive.

1. **MAJOR: base with retained history or an incremental lineage is not reproduced by a from-scratch staged build.**
   - Evidence:
     - `source_observations` also holds `type=="history"` records (codegraph.py:2576-2580, 3266-3270, 3494-3496).
     - Staging runs in an empty namespace, so it has no predecessor and no history.
     - History records feed `observation_digest` and the snapshot ranges. D5 step 6 filters to `type=="observation"` and silently drops them.
     - Day-1 (a) uses only a fresh full base.
     - S08 test_dirty_digest (:147-165) covers a single file only.
   - Effect: unmodified staging can differ from the base, or the retained facts of a failed file diverge between the two sides. A broken file under `--since` is common in real bases.
   - Condition:
     - Add Day-1 (a2): index, break a file, re-index with `--since` so history exists, then restore and also delete a file with `--since`. Run unmodified staging on that base and record the snapshot-id and change-set equality.
     - Add a deterministic D5 rule: if the base `source_observations` contains any non-`observation` record, return `CounterfactualRejected("base_not_buildable")`. Name it in the docs and the closed vocabulary. This is the fallback unless a2 proves equality.
     - Add a test with a real history fixture.

2. **MAJOR: typed-failure coverage has holes, and library `run` can leak raw exceptions.**
   - Evidence:
     - `SourceLease.read` raises ValueError via `require` (snapshots.py:661-677), for example when the binding is not `available`. `reader.read_source` wraps it as `ToolError("SOURCE_UNAVAILABLE")` (codegraph.py:1275-1287).
     - Those reads happen at stages 6/7, before the D6 `try`.
     - `change_set` catches only `sqlite3.Error` and `OSError` (impact.py:233). `_Loaded.load` ValueErrors propagate.
     - Only the CLI has the `TypeName` fallback.
   - Condition:
     - `analyze.run` must wrap every stage. Base reads map to `CounterfactualRejected("source_snapshot_unavailable")`. Diff-phase `ValueError`, `ToolError` and `RuntimeError` map to a closed `CounterfactualFailed` reason, for example `staged_incompatible` with `detail="index_unavailable"`.
     - Never let an untyped exception cross the library boundary.
     - Add a test that forces an unavailable binding through a real fixture.

3. **MAJOR: the hunk-body and `\ No newline` spec is incomplete. A mistake here silently misapplies bytes.**
   - Evidence: the D2 text ("marks the preceding line", "at most one per side", "plus one trailing") does not define three cases:
     - `\ ` after a context line applies to both sides;
     - `\ ` between `-` and `+` lines mid-hunk is legal git output (`-x`, `\ No newline`, `+y`);
     - a `\ ` before any body line, or two for one side, is malformed.
   - Condition: state those rules in D2. Make the grammar-table test cover:
     - `\ ` after a context line, after `-`, after `+`, mid-hunk and trailing;
     - duplicates and a leading `\ ` (malformed);
     - an old-side no-newline line followed by an appended line;
     - a plain-header patch with a timestamp after a tab;
     - a git `---` line with a trailing tab for a path containing spaces.
   - Also require a test that a patch applied to a CRLF base keeps the CR bytes byte-exact.

4. **MINOR: the DECL oracle patches hit git rename detection.**
   - Evidence: DECL deletes `e.py` (`def fe`) and creates `n.py` (`def fn`). Default `diff.renames` would emit `rename from`/`rename to`, which D3 rejects as `unsupported_patch_feature`.
   - Condition:
     - Generate static and in-test oracle patches with `git -c core.quotePath=false -c diff.noprefix=false -c diff.mnemonicPrefix=false diff --no-renames --no-ext-diff --src-prefix=a/ --dst-prefix=b/`.
     - Run git with `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`.
     - Keep static fixtures LF-only (generate CRLF and no-EOL cases in-test).
     - Add a sha256 pin per static fixture, so any normalisation fails loudly. (`tests/fixtures/coverage/.gitattributes` shows the repo has needed line-ending guards before.)

5. **MINOR: effective patch limits sit inside the `hypothetical_snapshot_id` hash.**
   - Evidence: AC-001 says the id is derived from "base snapshot, patch digest and producer configuration". With limits in the hash, the same hypothetical state gets different ids under different `--max-hunks` values, and a verifier recomputing from the three named inputs cannot match.
   - Condition: remove `limits` from `identity`; keep it in `effective_limits`. If kept, document it as an admission-policy input and add a test that two limit settings, both of which admit the patch, give different ids.

6. **MINOR: `partial_paths` makes `source_diff` permanently incomplete if the base has any failed file.**
   - Evidence: `diff._build` adds a path to `partial` when it failed on either side, even when both sides failed and the patch did not touch it (diff.py:458-462).
   - Condition: keep the status conservative, but split the report into `partial_paths_touched` (in the patch, or failed on one side only) and `partial_paths_preexisting`. Treat only the first as the incompleteness trigger, or at least list the two separately.

7. **MINOR: the incompleteness definition in D8 is over-conservative in two cases and under-documented in one.**
   - Over-conservative:
     - Deleting the last source file, or a rule's last target, adds `empty_source_selection`/`empty_target_selection` (checker.py:466-469). `exhaustive(after)` becomes false, so a certain resolution reports `resolved: null`.
     - Pre-existing `unresolved_import` boundaries anywhere in the reach make most real-repo rules incomplete.
   - Under-documented:
     - When a side is non-exhaustive, the non-null list is a sound lower bound, not complete. For example, with `--max-counterexamples-per-rule 1`, `introduced` is a witnessed subset.
   - Condition: document both points and name the `empty_*` handling. Either special-case `empty_*` as exhaustive, or state in the docs that the null is by design.

8. **MINOR: staging edge cases.**
   - Path collisions:
     - A create at `a/b.py/c.py` while `a/b.py` is observed, or the reverse, passes `ev.relative_path`.
     - Condition: P2 rejects file/directory prefix collisions across observed and created paths as `path_exists_in_base`.
   - Suffix semantics:
     - P2's suffix test must use `Path(p).suffix` semantics exactly as build does, so `.py` as a dotfile has an empty suffix.
     - Skip patterns are matched per component, so `vendor/bundle` never matches. Copy the same check; do not "fix" it.
   - Memory:
     - Reading per path duplicates identical blobs, and the `max_base_bytes` sum is per hash.
     - Condition: either sum the per-path lengths or share bytes by hash.

9. **MINOR: stderr and logging.**
   - Evidence:
     - The build emits `structlog` warnings, for example `unsupported_extension_skipped` (codegraph.py:3480).
     - The CLI group configures stderr (`__main__.py:47-57`).
     - A direct library call to `analyze.run` uses structlog's default logger, which writes to stdout.
   - Condition:
     - The `--timing` stderr regex test must use .py-only fixtures and assert the last line (or exact content with documented preconditions).
     - Document that library callers must configure structlog to stderr.
     - Scope the claim "never echo paths" to the CLI's own fixed messages. Build logs may carry paths on stderr.

10. **MINOR: Day-1 gaps.**
    - Record a real fixture trigger for each AC-007 path before relying on it. Case (v) is the one most likely to miss: stdlib `import json` may not yield `unresolved_import`. Fallback: a relative import of a missing module.
    - Add (g2): a synthetic N-file tree (for example 2000 and 5000 files) to get the per-file cost. The atlas-aci self-index alone does not extrapolate to 20000 files.
    - Add (h): the git `rev-parse` identity probe has a 3 s timeout (snapshots.py:46-49). A timeout under CI load surfaces as `identity_kind_mismatch`, so `staged_incompatible` can be flaky. Record it and map it to the typed reason.

11. **MINOR: docs must name these limitations explicitly.**
    - Non-ASCII filenames are rejected, because git quotes them by default.
    - Patches against non-UTF-8 sources are rejected as `not_utf8`.
    - The 20000-file and 256 MiB base cap.
    - Bases built from imports, SCIP or coverage are rejected.
    - A live-tree symlink at a staged path blocks analysis (`live_path_is_symlink`).
    - A cold extraction cache means a full re-parse.

Scope check: scope.txt, the amendment and the criteria are consistent. Fixtures are covered by `fixtures/counterfactual/*`. Existing tests only assert subsets of `cli.commands`, so adding a command is safe. The README edit does not touch the strings that test_s23 pins. The ASCII, AST-ban and RUF001 handling is adequate. 3.12/3.14 hazards are handled. No further file is needed.

## Stories
As an agent operator, I want to see which declarations and architecture rules a proposed patch would change, measured against the exact snapshot I am working from, so I can decide before applying it. The result must be labelled hypothetical and must never touch my checkout or index.

As a reviewer, I want unsafe or non-applying patches rejected with a closed reason before anything is indexed. I also want truncated or unresolved analyses marked incomplete, so a clean-looking delta is never a hidden gap.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s32.criteria.md` (sha256 `1d713204…cd8e`), verbatim:

```
### AC-S32-001 (event-driven)
GIVEN a valid bounded patch applies to the declared base snapshot
WHEN counterfactual analysis completes
THEN the report SHALL identify its hypothetical snapshot by the base snapshot, patch digest and producer configuration.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_identity_is_hypothetical

### AC-S32-002 (event-driven)
GIVEN the patch changes the fixture's supported source declarations
WHEN the proposed snapshot has been indexed
THEN the report SHALL contain the S19 source diff between the declared base and the staged patched snapshot.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_source_diff_matches_proposal

### AC-S32-003 (event-driven)
GIVEN the patch introduces or removes a supported forbidden-import path
WHEN S31 rules are compared across the two snapshots
THEN the report SHALL classify the fixture's architecture findings by their before/after presence.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_architecture_delta

### AC-S32-004 (ubiquitous)
THEN counterfactual analysis SHALL leave the original working tree, git refs and active snapshot state unchanged.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_original_state_unchanged

### AC-S32-005 (unwanted-behavior)
GIVEN the patch escapes the repository scope, changes a symlink or binary, fails its base check or exceeds the declared input limits
WHEN patch validation runs
THEN the analyzer SHALL reject the patch before indexing staged source.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_unsafe_patch_rejected

### AC-S32-006 (event-driven)
GIVEN identical base snapshot, patch bytes, analyzer configuration and rule document
WHEN the proposal is analyzed twice
THEN the canonical counterfactual report SHALL remain byte-identical.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_replay_is_idempotent

### AC-S32-007 (unwanted-behavior)
GIVEN extraction, diff or rule evaluation cannot finish within its supported scope and bounds
WHEN counterfactual result assembly runs
THEN the report SHALL classify the affected analysis as incomplete.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_incomplete_analysis_is_visible

### AC-S32-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S32-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

### Fixtures

**Trees** (in-test dicts; git repo with a commit; external graph root; every package has `__init__.py`; every imported name is a top-level `def`; no absolute imports):
- **BASE:**
  - `pkg/domain/a.py`: `from .b import fb` + `def fa`;
  - `pkg/domain/b.py`: `def fb(): return 1`;
  - `pkg/domain/e.py`: `def fe`;
  - `pkg/infra/c.py`: `from ..domain.b import fb` + `def fc(): return fb()`.
- **VIOL:** BASE with `b.py` prefixed by `from ..infra.c import fc`.

**Patch files** (`tests/fixtures/counterfactual/`, flat). Positive patches are git-style, generated once with `git diff` and committed as static bytes.
- `introduce-import.patch`: BASE→VIOL.
- `remove-import.patch`: VIOL→BASE.
- `introduce-second.patch`: on VIOL, adds `from ..infra.c import fc` to `e.py`.
- `declarations.patch` (DECL), on BASE:
  - `fb()` → `fb(x=0)`;
  - `fc` body → `fb() + 1`;
  - add `def fb2` to `b.py`;
  - create `pkg/domain/n.py` (`def fn`);
  - delete `pkg/domain/e.py`.
- `declarations-plain.patch`: DECL with plain headers.
- `parse-error.patch`: `b.py` → `def fb(:`.
- `rules-domain-infra.json` (two rules, both `to:["pkg/infra/**"]`):
  - `domain-a-not-infra`: `from pkg/domain/a.py`;
  - `domain-not-infra`: `from pkg/domain/**`.
- `unsafe-*.patch`: one file per AC-005 row, plus `unsafe-not-utf8.patch` (raw bytes).
- Oversize, 33-file and long-line patches are generated in-test.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_counterfactual_identity_is_hypothetical | **DECL on BASE, library and CLI bytes equal:**<br>– `hypothetical.kind == "hypothetical"`, `published is False`;<br>– `base_snapshot_id`/`base_generation_id` == the R0 lease;<br>– `patch_sha256 == sha256(file bytes)`;<br>– `configuration_sha256`/`producer_sha256` == the `compatibility` of a direct `diff.compare(R0, R1)`;<br>– `analyzer_version`; `limits` == the effective patch limits;<br>– `hypothetical_snapshot_id` recomputes, and differs from both the base and staged ids.<br>**Sensitivity:**<br>– a 1-byte patch change changes `patch_sha256` and the hypothetical id;<br>– a rebuilt base after an edit changes it;<br>– another scratch root keeps it;<br>– an analysis bound (`--max-changed-files 199`) keeps it but changes `report_id`.<br>**Not published:** `source_lease(hypothetical id)` raises; the R0 lease ids are unchanged; the scratch root is empty. |
| 002 | test_counterfactual_source_diff_matches_proposal | **DECL on BASE:**<br>– after the run, `write_tree` applies the patched tree to the repo and `build` goes into R1;<br>– `ev.canonical(report.source_diff.change_set)` == `diff.canonical_bytes(diff.compare(Side(repo,R0), Side(repo,R1)))` minus the LF (or the normalised form, per Day-1 b);<br>– categories: `fb` signature, `fc` body, `fb2` and `fn` added, `fe` removed;<br>– `report.staged.snapshot_id == change_set.after.snapshot_id`;<br>– `patch.files` has ops modify/modify/create/delete (b, c, n, e) with `base_sha256`/`patched_sha256` == sha256 of the R0 pack bytes / R1 file bytes.<br>**`declarations-plain.patch`:** the same `change_set` bytes.<br>**Applier oracles (supporting):** `git diff` (CRLF lines, no-newline at EOF on either side, add/remove last line, empty-file create/delete, mode 100755 create) and `difflib.unified_diff` (plain, a/ b/ prefixes). `apply(base)` == the target bytes for each. |
| 003 | test_counterfactual_architecture_delta | **BASE + introduce-import:**<br>– `domain-not-infra` is `satisfied_within_scope`→`violated`, `status complete`;<br>– introduced == [(a,c),(b,c)], resolved == [], persisting == [];<br>– `domain-a-not-infra` introduced == [(a,c)].<br>**VIOL + remove-import:** violated→satisfied; resolved == [(a,c),(b,c)]; introduced == [].<br>**VIOL + introduce-second:** introduced == [(e,c)], persisting == [(a,c),(b,c)].<br>**Embedded reports:** `reports.before`/`after` bytes == `checker.run` on R0 and on an R1 build of the patched tree.<br>**Incomplete-side guard:** BASE + introduce-import with `--max-counterexamples-per-rule 1` gives `status incomplete`, `counterexamples_truncated`, `introduced == [(a,c)]` (before is exhaustive), `resolved is None`. |
| 004 | test_counterfactual_original_state_unchanged | **Live tree differs from base:** untracked `pkg/domain/zz.py` (imports infra), live-edited `b.py`, deleted `e.py`.<br>**Snapshots:** `inv = inventory(repo, R0, scratch)` first; then, with `GIT_OPTIONAL_LOCKS=0`, `rev-parse HEAD`, `for-each-ref`, `status --porcelain` and `.git/index` sha256.<br>**Runs:** DECL (success), `remove-import` on BASE (rejected), and a run whose `impact.change_set` is monkeypatched to raise (failure after staging; this verifies cleanup, not diff).<br>**Afterwards:**<br>– all snapshots are equal;<br>– the scratch root is empty;<br>– a fresh R0 lease has the same ids;<br>– no `zz.py` in the staged observations;<br>– the report bytes equal a run made before the live edits (live-tree independence).<br>**Usage:** `--scratch-root` inside the repo or inside R0 → exit 2 with the fixed text. |
| 005 | test_counterfactual_unsafe_patch_rejected | **Parametrised table → `PatchRejected.reason`.** For each case: CLI exit 1 with the exact stderr, empty stdout, the scratch root empty (no namespace created), no path or patch text in stderr.<br>**P0/P1 cases:**<br>– escape: `a/../x.py`, absolute, backslash, `.git/x.py`, control char, quoted path → `invalid_path`;<br>– `new file mode 120000` / `new mode 120000` → `symlink_change`;<br>– 160000 → `submodule_change`;<br>– 100644→100755 mode-only → `unsupported_patch_feature`;<br>– rename, copy → `unsupported_patch_feature`;<br>– `GIT binary patch`, `Binary files … differ`, NUL → `binary_patch`;<br>– non-UTF-8 → `not_utf8`;<br>– duplicate file → `duplicate_path`;<br>– count mismatch, overlapping or unordered hunks, empty → `malformed_patch`;<br>– 262145 bytes, 33 files, line > max, hunks > max → `limit_exceeded`.<br>P0/P1 rejections also fire with a nonexistent `--graph-root` (before the lease).<br>**P2 cases (base check):**<br>– context changed, the right hunk at an offset by one line, wrong `index` old id, delete with residual → `base_mismatch`;<br>– create over `b.py` → `path_exists_in_base`;<br>– modify `pkg/missing.py` → `path_not_in_base`;<br>– `pyproject.toml`, `node_modules/x.js` → `unsupported_path`;<br>– result > `--max-result-file-bytes 16` → `limit_exceeded`;<br>– a patch made against the live-edited `b.py` → `base_mismatch`.<br>**Base rejections, same no-staging assertion:**<br>– wrong `--base-snapshot` → `base_snapshot_mismatch`;<br>– export→import generation → `base_not_buildable`. |
| 006 | test_counterfactual_replay_is_idempotent | **DECL + rules on BASE:**<br>– two library runs, one CLI run, a different scratch root, and `--timing` (stdout identical; stderr matches `^Counterfactual timing: elapsed_ms=[0-9]+$`) all give identical bytes;<br>– two `python -m atlas_aci counterfactual` subprocesses with `PYTHONHASHSEED=1` and `=2` give identical bytes;<br>– a rebuild of the base into a fresh root at the same path gives the same base ids and identical bytes;<br>– `report_id` and the hypothetical id recompute;<br>– `--max-depth 1` changes `report_id` but not the hypothetical id. |
| 007 | test_counterfactual_incomplete_analysis_is_visible | All exit 0 with a validated report:<br>(i) `parse-error.patch`: `extraction` incomplete `{staged_extraction_failed}`, `failed == [{b.py, patched:true, parse_error}]`; `source_diff` incomplete with `partial_paths` (and `unknown_changes` if `extraction_failed_after` records exist).<br>(ii) `--max-changed-files 0`: `source_diff` incomplete `{changed_file_limit}`, `change_set is None`; extraction and architecture still `complete`.<br>(iii) `--max-diff-entities 1` → `diff_entity_limit`.<br>(iv) `--max-depth 1` on a 2-hop introduce → architecture incomplete `{rule_budget_limited}`, `introduced is None` on the stopped side.<br>(v) An absolute `import json` added to `a.py` → `rule_scope_unknown`.<br>(vi) No `--rules` → `architecture.status == "not_requested"`, `architecture is None`.<br>**`validate_report` rejects:** complete-with-reasons, a tampered `report_id`, `published:true`, a non-null `introduced` on a non-exhaustive side. |
| RUN-01 | gate | SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` + `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt is `.spectra/state/aci-s32.execution.json`. |

Every `all(...)` is preceded by a non-empty assertion. Nothing about the behaviour under test is mocked.

### Supporting tests (same file)
- Grammar table: header/ext permutations, `\ ` binding, signature trailer, preamble.
- G-S31-PARITY.
- **G-SURFACE:**
  - tools JSON sha and bytes are the base values; 7 `READ_ONLY_TOOLS`;
  - 3436/3448/797/7730;
  - `counterfactual` is in `cli.commands` and absent from the tools JSON.
- **G-AST:**
  - `patch_input.py` bans imports of `os`, `sys`, `io`, `pathlib` except `PurePosixPath`, `tempfile`, `shutil`, `subprocess`, `multiprocessing`, `threading`, `time`, `datetime`, `random`, `uuid`, `socket`, `urllib`, `http`, `importlib`, `atlas_aci.codegraph`, `atlas_aci.server`, `atlas_aci.tools`; and calls to `open`, `eval`, `exec`, `compile`, `__import__`.
  - `analyze.py` permits `tempfile` (`TemporaryDirectory`/`gettempdir` only), `hashlib`, `json`, `re`, `sqlite3`, `contextlib`, `dataclasses`, `pathlib`, `typing`, `collections.abc`, and `atlas_aci.{codegraph,config,query.diff,query.impact,query.impact_limits,architecture,storage.evidence,counterfactual}`. It bans `shutil`, `os`, `subprocess`, `time`, `datetime`, `random`, `uuid`, `threading`, `multiprocessing`, `socket`, `urllib`, `http`, `importlib`, `atlas_aci.server`, `atlas_aci.tools`, and calls to `eval`/`exec`/`compile`/`__import__`/`IndexedReader`.
  - A planted positive control fails for each module.
  - ASCII only.
- **Vocabulary:** no report key or enum token in {safe, verified, proven, guaranteed, compliant, applied, published-true}.
- **Docs** name every reason, status, limit and limitation.

### Gates beyond the ACs
- **G-SRC:**
  - `git diff --exit-code 79ae0b0 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage,extractors,providers,indexing,resolvers,reports,architecture} mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows`;
  - `git diff --name-only 79ae0b0 -- mcp-server/src` ⊆ {`counterfactual/{__init__,patch_input,analyze}.py`, `__main__.py`}.
- **G-MCP:** tools JSON `94f9567a…` / 1631472.
- **Regression set unchanged:** `test_s08_*`, `test_s19_*`, `test_s20_*`, `test_s31_*`, `test_thesis_negatives`, `test_export`.
- **Production image** (orchestrator):
  - build from the branch; `docker run --network none`, with the fixture repo mounted `:ro` and `/graphs` a volume;
  - `atlas-aci index --repo /src --graph-root /graphs` (snapshot id from the `index_done` log);
  - `counterfactual` twice with DECL + rules → identical stdout;
  - a `/graphs` inventory is identical before and after;
  - `/tmp` holds no `atlas-counterfactual-*`;
  - an unsafe patch → exit 1 with the fixed text.

```sh
cd mcp-server
uv run pytest tests/slices/test_s32_counterfactual.py -q
uv run pytest tests/slices/test_s08_*.py tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/slices/test_s31_*.py tests/test_thesis_negatives.py tests/test_export.py -q
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/
# if an offline 3.14 interpreter is available: uv run --python 3.14 pytest tests/slices/test_s32_counterfactual.py -q
# CI (3.14) green is required before merge either way
```

## Confidence
Computed with `ramza-score`. The design composes merged, tested primitives with no edits to them: S08 overlays, S19 compatibility and the change set, the S20 count-bounded admission, and S31 check/validate. Producers are untouched, so generation ids, envelopes and the tools JSON cannot churn.

`test_s08_contract.py:157-165` already shows that overlay snapshots equal real builds of the same bytes. Main uncertainties, covered by Day-1 with a stop rule:
- equality of unmodified staging with the base;
- live-tree independence through the private override;
- path-independence of the staged ids;
- cost on a real-sized repo.

## Rejected Alternatives
- **A. Plain overlays on the live repo.** `_iter_source_files` would enumerate untracked and edited live files, and overlays cannot delete. The result would depend on the dirty worktree (AC-004/006).
- **C. Physical temp copy.** The snapshot identity is path-bound (root / git `common_dir`), so S19 gives `repository_identity_mismatch` (`test_s19_contract.py:227-304`). Relaxing `_check_compatible` edits S19. A git worktree mutates `.git` (AC-004).
- **A `source_provider` parameter in `CodeGraph.build`.** Edits the fingerprinted codegraph.py → producer churn and re-index of every graph.
- **`git apply` / `patch` subprocess.** Process launch, fuzz/offset defaults, platform variance, and the `.git` index lock.
- **Seeding the base extraction cache or the base DB as predecessor.** Faster, but it couples staging to cache-validation internals and risks stale-fact reuse; it is the follow-up.
- **Keying the rule delta on witness hop ids.** They embed per-snapshot ids; `(rule, source, target)` is stable.
- **Treating non-indexable paths as ignored.** They cannot be base-verified from the snapshot, and a silently partial hypothetical misleads; v1 rejects them as `unsupported_path`.

## Risks (ranked)
1. **The staged build diverges from a real build** (overlay vs walk). Day-1 (a)/(b); the AC-002/003 independent-build oracles.
2. **Private coupling:** `_iter_source_files` override, `diff._open/_lease/_check_compatible`, `impact.change_set`, `reader._producer_inputs`. The module docstring lists them. The behavioural tests (AC-004 untracked file, G-S31-PARITY) fail loudly on drift.
3. **Parser/applier edge cases** (CRLF, no-newline, /dev/null, empty files, count mismatch). Grammar table plus `git diff` and difflib oracles.
4. **Cost and memory:** full re-index plus all base bytes in RAM. Base bounds give a typed rejection; Day-1 (g) numbers are in the docs; seeding is the follow-up.
5. **CI Python 3.14 vs dev 3.12** (the S31 PR failed on exactly this). No recursion, sorted outputs, the PYTHONHASHSEED replay, and CI green before merge.
6. **Concurrent re-index of the base** → re-verify gives `base_changed`, and nothing is printed.
7. **Scratch on tmpfs, or TMPDIR inside the repo** → usage error; cleanup on every path is tested.
8. **First-use grammar download outside Docker** → documented; the image prefetches the grammars.
9. **Rules mostly `unknown` on real repos** (S31 provider limits) → visible as `rule_scope_unknown`, never as a resolved claim.
10. **Timebox of 5 days** → follow the cut order; at the limit, report completed checks and a narrow follow-up.
