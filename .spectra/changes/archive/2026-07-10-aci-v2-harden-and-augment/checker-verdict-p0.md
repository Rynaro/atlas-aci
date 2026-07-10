---
artifact: checker-verdict
change_id: aci-v2-harden-and-augment
gate: P0 (hardening)
maker: vivi
checker: vigil
criteria_sha256: f89400c426f56871eb30ea15253a9105e3d24c7705dbbfa695fe30368b939994
criteria_hash_confirmed: true
verdict: GO-WITH-CONDITIONS
date: 2026-07-09
---

# P0 Harden-Gate — Checker Verdict (VIGIL, adversarial)

## Verdict: GO-WITH-CONDITIONS

The frozen acceptance-criteria hash matches exactly
(`f89400c…b939994`) — the criteria were **not** bent. Every one of the eight
named P0 gate criteria (AC-H-1/4/7/8/9/12/15, AC-NEG-6) passes by its literal
VERIFY method, verified independently (not accepted on Vivi's word): I ran the
six gate-criteria tests + the negatives suite through `.venv/bin/pytest`
(**10 passed**), recomputed `ddl_hash(SCHEMA)` and confirmed it equals the
hand-pasted `EXPECTED_DDL_HASH`, and re-ran every doc/negative grep. The
thesis negatives (NEG-1/2/4/5) are intact.

It is **not** a clean GO. Adversarial probing found **two MAJOR defects in the
invariants the gate exists to certify** — one of them (silent truncation of
`search_symbol` references at the production cap) I **reproduced empirically**.
Neither fails a *named* gate criterion; both live in the coverage gaps between
the criteria and the D2 thesis. The conditions below must be tracked before any
A1–A5 workstream builds on this substrate.

Why not NO-GO: the named criteria genuinely pass with real, non-tautological
tests; nothing was bent; the P0 work is substantially correct. Why not clean
GO: the "truncate-and-flag is un-ignorable" invariant — the heart of the
hardening thesis — is empirically violated on the exact hub-symbol case this
ACI exists to navigate. A clean GO here would be the agreeable-checker failure.

**Escalation note for the maintainer:** if you read "the un-ignorable flag holds
on *every* path" as a gate-level invariant rather than only the named criteria,
F-1 flips this to NO-GO. I am surfacing that judgment rather than forcing it,
because the eight named criteria pass.

---

## Per-criterion table

### The gate (all must hold)
| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| AC-H-1  CI runs pytest on PR | PASS [VERIFIED] | ci.yml `on: pull_request` → `uv run --frozen pytest -q`; failing test → nonzero exit → failing check. `uv sync --frozen` does **not** block on the lock drift (uses lock as-is; freshness is `--locked`, confirmed uv 0.11.23). Probe-PR clause guaranteed by workflow logic. |
| AC-H-4  every tool/verb truncates+flags over-cap | PASS [VERIFIED] | `test_every_tool_and_verb_truncates_and_flags_over_cap` green; real per-tool/verb over-cap fixtures; non-tautological. |
| AC-H-7  search_symbol bounded | PASS [VERIFIED] | `test_search_symbol_is_bounded` green. Scoped to **definitions** (no SQL LIMIT → central cap catches >cap → flags). Refs sub-field is F-1, out of this criterion's scope. |
| AC-H-8  graph_query callers_of bounded | PASS [VERIFIED] | `test_graph_query_is_bounded` green; edges capped+flagged at the response layer. (Work-bounding is F-2.) |
| AC-H-9  epoch-namespaced DB path | PASS [VERIFIED] | `test_db_path_is_epoch_namespaced` green; `.atlas/graph.1.db`, `SCHEMA_EPOCH` is int. |
| AC-H-12 committed DDL hash == current DDL | PASS [VERIFIED] | Recomputed `ddl_hash(SCHEMA)` = `2371e04f…347cd6` == `EXPECTED_DDL_HASH` (codegraph.py:255, hand-pasted literal, not self-derived). |
| AC-H-15 registry completeness | PASS [VERIFIED] | `test_every_list_returning_tool_registers_a_bounded_field` green; deleting the `search_symbol` entry fails 3 tests (per maker; consistent with source) — non-tautological. |
| AC-NEG-6 harden-gate is the in-tree mechanism | PASS-AS-WRITTEN [VERIFIED] + MAJOR caveat F-3 | harden-gate.yml runs on PR; unconditional negatives + gate-criteria-when-augmented; a marker-path augmentation probe is caught. **Content-marker heuristic for the 4 shared hot files is evadable (F-3, disclosed).** |

### Thesis negatives (must stay intact)
| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| AC-NEG-1 no LLM client importable | PASS [VERIFIED] | grep 0; `test_no_llm_client_importable` green. |
| AC-NEG-2 no networkx | PASS [VERIFIED] | grep networkx in pyproject+uv.lock → 0; test green. |
| AC-NEG-4 no ALTER TABLE ladder | PASS [VERIFIED] | grep "alter table" src → 0; epoch scheme is genuine drop-and-rebuild (DELETE FROM + `os.replace` atomic swap + `unlink` sweep), no in-place migration. |
| AC-NEG-5 no new runtime dep | PASS [VERIFIED] | core deps == baseline {mcp, tree-sitter, tree-sitter-language-pack, click, pydantic, structlog}; test green; sqlite-vec is `[vec]` extra, pytest/ruff/mypy are `[dev]`. |

### Also-assessed
| Criterion | Verdict | Evidence / note |
|-----------|---------|-----------------|
| AC-H-2  ruff+mypy in CI | PASS [VERIFIED] | ci.yml: ruff check + ruff format --check + mypy src/. |
| AC-H-3  element cap before byte ceiling | PASS [VERIFIED] | apply_central_bounds loops the element cap, then checks the byte ceiling; `test_central_bounds_applies_element_cap_then_byte_ceiling`. |
| AC-H-5  overflow contract | PASS [VERIFIED] | `test_overflow_truncate_and_flag_contract` green. **Precondition is central truncation, which F-1 sidesteps for references.** |
| AC-H-6  absolute byte ceiling hard-fails | PASS [VERIFIED] | `test_absolute_byte_ceiling_hard_fails` green. 1 MiB defensible: largest legit list response ≈16–40 KB (element cap binds first in every list path); backstop only. Does **not** guard F-2 (measured post-materialization). |
| AC-H-10 index sweeps, serve does not | PASS [VERIFIED] | `_sweep_stale_epoch_files` reachable only from build(); test green. |
| AC-H-11 index rebuilds / serve fails-fast on epoch mismatch | PASS [VERIFIED] | epoch_ok() cross-checks manifest row; read-only path → ToolError; test green. |
| AC-H-13 LANG_BY_EXT ⊆ QUERIES ∪ unsupported | PASS [VERIFIED] | module-level assert + test green. |
| AC-H-14 unsupported-ext skip reported | PASS [VERIFIED] | `unsupported_skipped` stat + log.warning; test green. |
| AC-H-16 serve on :ro mount, zero .atlas writes | **PARTIAL** [VERIFIED] | pytest half present but via a **chmod proxy (F-4)**; the required CI `:ro`-mount smoke test is **absent**. Connection is `mode=ro` (not `immutable=1`); serve zero-writes-under-.atlas traced from source. **Not in the P0 gate.** |
| AC-H-17 concurrent index atomic rename | PASS [VERIFIED] | `fcntl.flock(LOCK_EX)` + `os.replace`; `test_concurrent_index_atomic_rename_no_corruption` green. |
| AC-DOC-1 bounds invariant true incl. search_symbol/graph_query | PASS [VERIFIED] | README bullet **strengthened** (central chokepoint added), invariant retained verbatim, **no per-tool exception**; line drift 411→421 is additive. |
| AC-DOC-2 no --since git-ref anywhere | PASS [VERIFIED] | criterion grep → 0 across tracked README/INTEGRATION/SETUP/mcp-server; all 5 sites corrected. |
| AC-DOC-3 no false canary pass-rate | PASS [VERIFIED] | no numeric rate; README/SETUP flagged aspirational; run-canaries.py has explicit DEFERRED note + NotImplementedError. |
| AC-DOC-4 no Prism | PASS [VERIFIED] | grep prism over source+docs → 0. |
| AC-DOC-5 LICENSE present | PASS [VERIFIED] | top-level LICENSE (Apache-2.0, 202 lines); README license section repointed. |
| AC-DOC-6 kind enum superset | PASS [VERIFIED] | `enum=["any", *PRODUCED_KINDS]`; test green. |
| AC-DOC-7 no symbols.db in CLAUDE.md | PASS [VERIFIED] | grep → 0. |
| AC-DOC-8 layout lists test_codegraph.py | PASS [VERIFIED] | README:392. |
| AC-DOC-9 memex description honest | PASS [VERIFIED] | README + server.py:142-148 say "no ATLAS tool currently emits one". |
| AC-DOC-10 no unbounded/impl-defined in mcp-server/README | PASS [VERIFIED] | grep → 0. |
| AC-REL-3 uv.lock version == pyproject | **FAIL** [VERIFIED] | uv.lock 0.3.1 vs pyproject 0.4.0 (`uv lock --check` fails). **Deferred to P3, not in P0 gate.** |

---

## Ranked findings

### MAJOR

**F-1  [VERIFIED — reproduced]  Silent truncation of `search_symbol` references at the production cap.**
`codegraph.py:647` fetches references with a fixed `LIMIT 200`; `config.py:54`
sets `max_bound_field_elements = 200`. The two collide exactly: the SQL layer
truncates references to 200, and the central cap (`server.py`
`apply_central_bounds`) then sees `len == 200`, not `> 200`, so it sets **no**
`truncated`/`more_available` flag — and the tool wrapper flags nothing either.
Reproduction (production defaults, a symbol with 250 references):
```
Case A references: true_refs=250  returned=200  truncated=False  more_available=False  any_flag=False
Case B definitions: true_defs=250  returned=200  truncated=True   more_available=True
```
Definitions (no SQL LIMIT) flag correctly; references (SQL LIMIT == central cap)
truncate **silently**. This is the exact "truncate without the flag on a nested
field" failure the gate exists to forbid; on a large repo, `search_symbol` on a
hub method silently drops references and the agent believes it has the complete
set. Fix is one character (`LIMIT 200` → `LIMIT max_bound_field_elements + 1`,
or drop the SQL LIMIT and let the central cap flag). Blocker-adjacent; the *only*
reason it is not marked BLOCKER is that it does not fail a *named* gate criterion
(AC-H-7 is scoped to definitions; AC-H-4/5's precondition is central truncation,
which this path sidesteps).

**F-2  [VERIFIED — from source]  Central cap applied *after* `fetchall()` materializes every row (OOM/DoS vector).**
`codegraph.py:642` (`SELECT * FROM symbols WHERE name = ?`, no LIMIT) and
`codegraph.py:656-658` (`callers_of`, no LIMIT) materialize the *entire* result
set into Python before the central cap truncates the response. The response is
bounded (AC-H-7/8 hold), but the *work* is not: a `search_symbol`/`callers_of`
on a hub name on a Rails-scale repo materializes every matching row first. The
1 MiB byte ceiling does not help — it is measured on the already-materialized,
already-serialized body. Note the inconsistency the harden pass left
unreconciled: references *are* SQL-bounded (LIMIT 200, which causes F-1) while
definitions/callers_of are *not* (which causes F-2). The correct reconciliation
— `LIMIT max_bound_field_elements + 1` on all three — fixes both at once.

**F-3  [VERIFIED — disclosed]  harden-gate augmentation heuristic is evadable → AC-NEG-6 decorative for crafted edits.**
`harden-gate.yml:119` (`AUGMENTATION_CODE_MARKERS`) and `:136` classify an edit
to the four shared hot files as augmentation only if an added line matches a
literal marker. A feature PR can evade all of them: name the edge table
`call_edges` (the marker is the literal `CREATE TABLE IF NOT EXISTS edges`);
import the `{EXTRACTED,INFERRED,AMBIGUOUS}` enum from a helper module so the
added lines in codegraph.py carry no literal; name the new test file
`test_edges.py` (not a marker path). Result: `augmented=false`, the
gate-criteria step (guarded by `if augmented == 'true'`) never runs, and A1–A5
code lands without the harden check. This is **honestly disclosed** in the
KNOWN LIMITATION header (lines 30-39) and does not fail AC-NEG-6's literal
VERIFY (a marker-path probe *is* caught), so the criterion passes as written —
but the mechanism is a best-effort net, not the guarantee the prose implies.

### MINOR

**F-4  [VERIFIED]  AC-H-16 chmod proxy does not model a `--read-only` / `:ro` mount** (`test_schema_epoch.py:129`). `chmod 0o555/0o444` is enforced against non-root only — **root ignores mode bits**, whereas a `:ro` bind mount returns EROFS even for root (containers commonly run as root). chmod also does not exercise SQLite hot-journal rollback or advisory-lock behaviour on a genuinely read-only filesystem. The `mode=ro` connection (correct for the clean case; no `-wal`/`-shm` for a rollback-journal DB) is sound, but `immutable=1` would be strictly stronger against a stray hot `-journal`. The CI `:ro` smoke test AC-H-16 explicitly names is **absent** — Vivi's disclosure is honest; the criterion is genuinely half-done (and not in the P0 gate).

**F-5  [VERIFIED]  ci.yml comment mischaracterizes `uv sync --frozen`** (`ci.yml:15-17`): claims it "refuses to silently re-lock … and fails loudly instead if the lock is genuinely out of sync." False — `--frozen` uses the stale lock *without* a freshness check (that behaviour is `--locked`). Confirmed: the lock **is** stale (`uv lock --check` → "needs to be updated"), yet CI proceeds to pytest. No gate impact, but the stated REL-3-guard rationale is wrong.

**F-6  [VERIFIED]  Dual/triple truncation-flag vocabulary dilutes "un-ignorable".** Central path uses `truncated`/`more_available`/`returned_count`; `search_text` and `list_dir` use `overflow`; `view_file` uses `next_cursor`/`total_lines`. Each path *does* flag, but a naive consumer scanning only for `truncated` misses the others. (list_dir's own `overflow` is what keeps its 200==cap collision from being a second F-1; codegraph's references path has no such self-flag, which is why it is silent.)

**F-7  [OBSERVATION]  `returned_count` sums across all bounded fields** (`server.py:235-237`) — for multi-field tools (search_symbol/definitions_of) it reports defs+refs combined. Defensible ("total returned elements"); AC-H-5 only exercises single-field. Not a defect per the criterion.

**F-8  [OBSERVATION]  `Config.__post_init__` always `mkdir`s `memex_root`** (`config.py:76`). If an operator points `memex_root` under the `:ro` repo mount, serve fails at construction. Not a `.atlas` write (so outside AC-H-16's claim), but relevant to the read-only deployment story the change advertises.

---

## Conditions on the GO
1. **F-1 (mandatory before merge, blocker-adjacent):** eliminate the silent references truncation — `LIMIT max_bound_field_elements + 1` (or drop the SQL LIMIT) so a >cap references result reaches the central cap and is flagged. Add a test at the production boundary (cap == SQL limit), which the current suite never exercises.
2. **F-2 (mandatory before A1–A5):** bound the SQL work on `definitions` and `callers_of` (same `+1` LIMIT), so the central bound is a *work* bound, not only a *response* bound.
3. **F-3 (track):** the harden-gate is a best-effort net; make the required-status-check operator step explicit and tighten the marker list as A1–A5 land, or the AC-NEG-6 guarantee degrades to decorative.
4. **F-4 / AC-H-16 (track for P0-follow):** land the CI `:ro`-mount smoke test the criterion names; consider `immutable=1`.
5. **F-5 (cheap):** correct the ci.yml comment; if REL-3 detection was intended, add `--locked` to one job or a lock-vs-pyproject check.

## Self-flagged items — my ruling
- **AC-H-16 chmod proxy:** honest disclosure, **real partial** (F-4). Not blocking (not in gate).
- **harden-gate heuristic:** honest disclosure, **real limitation** (F-3). Criterion passes as written.
- **AC-A3-1/4/5 format guess:** fails **CLOSED** (missing/malformed probe → `exit 1`). Correct, safe direction. Not a hole.
- **AC-DOC-1 line drift:** invariant **strengthened**, not weakened. Honest.
- **1 MiB ceiling / self-caught bug:** **defensible.** Largest legit list response ≈16–40 KB; element cap binds before the byte ceiling in every list path; the fix (separate `max_response_bytes` from the 8 KiB `max_bytes_per_call`) is correct. Caveat: it is a response-size backstop, not the work bound F-2 needs.

*VIGIL — Verify · Isolate · Graph · Intervene · Learn. Read-only mission; no fixes applied, criteria untouched.*

---

# SECOND PASS — re-verify after the six fix commits (VIGIL)

**Context:** Coordinator ruled the first pass **NO-GO** on F-1 (a green gate over an
empirically-violated "never silently incomplete" invariant launders the defect —
correct). Vivi landed six commits (`7524ffc`, `f7b74fd`, `7cff938`, `3f5b98b`,
`0763ff5`, `76b4427`). Criteria hash still `f89400c…b939994` (re-confirmed, unbent).
Independently re-established (attacked, not assumed): `.venv/bin/pytest -q` → **69 passed**
(ran it); `uv.lock` unchanged vs base; complete changed-file set cross-checked against
declared-scope.md.

## Second-pass verdict: GO-WITH-CONDITIONS

Unlike the first pass, this is **not** laundering a silent defect. The F-1 invariant is
**verifiably restored** — I re-ran my original reproduction against the fix and the silent
path is closed (below). Every named P0 gate criterion still passes (re-run within the 69).
The remaining items are (a) a **fail-safe over-signal** (NEW-1 — view_file now over-reports
truncation; the *opposite* of a silent-incompleteness failure) and (b) two **declared-scope
reconciliations** that block archive at `drift_check` but are not P0-criteria failures.
Neither condition is a "silently incomplete" violation, so neither is a NO-GO trigger.

**Why not clean GO:** archive is genuinely blocked until RAMZA reconciles two scope drifts
(`drift_check` gate), and NEW-1 is a tracked recommendation on the core un-ignorable-flag
mechanism. **Why not NO-GO:** no invariant is empirically violated; F-1/F-2 are fixed and
proven; the thesis holds on every path I could find.

## Disposition of prior findings F-1..F-8

| Finding | Disposition | Mark | Evidence |
|---------|-------------|------|----------|
| **F-1** references silent truncation | **FIXED** | [VERIFIED] | `codegraph.py`: both `defs` and `refs` SQL now `LIMIT ?` = `self.query_limit` = `cap+1`; `run_stdio` wires `query_limit=config.max_bound_field_elements+1`. **Re-ran my repro**: 250 refs at prod defaults → returns cap, `truncated: True`, `truncated_fields: ["references"]`, `more_available: True`. New boundary test parametrizes cap-1/cap/cap+1. |
| **F-2** post-`fetchall()` OOM | **FIXED** | [VERIFIED] | Limit is applied **in SQL** (`LIMIT ?`) on `search_symbol` (defs+refs) and `callers_of`, not after `fetchall()`. No `DISTINCT`/`GROUP BY`/join in any of the three, so no fan-out can hide overflow at the cap boundary. |
| **F-3** harden-gate evadable | **FIXED (verb-reachability)** | [VERIFIED] | `KNOWN_QUERY_VERBS` gates `query()` (unknown verb → error before dispatch); `test_bounded_field_registry_covers_every_known_query_verb` enumerates that same constant and runs **unconditionally in ci.yml**. A new verb must join `KNOWN_QUERY_VERBS` to be reachable, and doing so trips the completeness test regardless of naming. harden-gate comment now honestly documents the grep evasion and correctly retains step 3 for A3 *sequencing* (which no pytest can observe). **Residual (R-1, [SUSPECT], latent):** the registry checks each verb has a *non-empty* bounded_field, not that it covers *every* list field a future verb's response might carry — an unregistered list *sub-field* would still escape. No current verb does this. |
| **F-4** chmod ≠ :ro proxy | **FIXED** | [VERIFIED] | New pytest `test_read_only_connection_is_actually_sqlite_mode_ro` proves the connection rejects an INSERT with `sqlite3.OperationalError` (mode=ro, uid-independent). New CI job `serve-read-only-smoke` builds the real image and runs `docker run --read-only -v repo:/repo:ro` — an actual EROFS-enforced mount, not chmod — asserting liveness + zero `.atlas` writes, incl. a no-`--memex-root` regression variant. |
| **F-5** false `--frozen` claim | **FIXED** | [VERIFIED] | ci.yml comment corrected; now accurately states `--frozen` does not validate lock freshness (that is `--locked`) and explains the REL-3-stale-lock deferral. Matches my own `uv lock --check` result. |
| **F-6** dual truncation vocabulary | **FIXED, but introduced NEW-1** | [VERIFIED] | `apply_central_bounds` promotes tool-level `overflow`/`next_cursor` onto the unified `truncated` contract. Closes the missed-signal gap — but over-fires on view_file (NEW-1). |
| **F-7** summed returned_count | **FIXED** | [VERIFIED] | `returned_count` is now per-field `{field: len}`; `truncated_fields` lists exactly which field(s) lost data; `test_returned_count_is_per_field_not_summed` pins per-field attribution. No list field truncates without appearing in `truncated_fields` (path-1 central overflow + path-2 tool-signal promotion cover all current tools). **Residual ([SUSPECT], pre-existing MINOR):** `test_dry_run`'s byte-level stdout/stderr cap sets its own `truncated: True` with no `truncated_fields`/`more_available` — an inconsistent contract shape, not silent loss. |
| **F-8** memex crash vs silent degradation | **FIXED — acceptable fail-open** | [VERIFIED] | **Reproduced both directions:** post-fix `Config()`/`Memex()` construct without crashing on an unwritable memex_root (fail-open + warning); `memex.read` of a well-formed-but-absent ref raises `ToolError NOT_FOUND` (**fail-closed, distinguishable from success** — not an empty masquerade); pre-fix unconditional `mkdir` raises `NotADirectoryError` (crash confirmed). A caller cannot confuse "degraded" from "nothing to find" — the read-time NOT_FOUND is the visible signal even though the stderr warning isn't. **Not F-1 in another module.** |

## New findings introduced by the fix commits

**NEW-1 [VERIFIED — reproduced] [MINOR, fail-safe]  `view_file` now false-flags every non-EOF window as truncated, with a wrong retry_hint.**
Introduced by `f7b74fd` (F-6) and **tested-in** (`test_view_file_next_cursor_promotes_to_truncated` asserts `retry_hint == "narrower_scope"`). `apply_central_bounds` treats `"next_cursor" in result` as a truncation signal, but `view_file` sets `next_cursor` on *any* read that doesn't reach EOF — including a deliberate, complete small window. **Reproduced end-to-end:** requesting lines 2-4 of a 10-line file (a complete 3-line window, nothing over-cap) returns `lines: 3` (exactly what was asked) yet `truncated: True`, `more_available: True`, `retry_hint: "narrower_scope"`. Two problems: (a) `truncated` becomes near-permanently true for the most-used tool, diluting the "un-ignorable" flag into noise that trains agents to ignore it — which *indirectly* threatens the un-ignorable property globally; (b) `narrower_scope` is the wrong continuation — the correct action is to page forward via `next_cursor`, and narrowing shows *less* of the file. This is the *opposite* of a thesis violation (over-complete, never hides data), so it does **not** block on "never silently incomplete" — but it should be fixed: view_file should set `truncated` only on genuine over-cap/`overflow`, and let `next_cursor` carry ordinary pagination without `retry_hint: narrower_scope`.

**NEW-2 [SUSPECT — reasoned] [MINOR, latent]  `CodeGraph.query_limit` default re-anchors to the module constant, not the config cap.**
`CodeGraph.__init__` defaults `query_limit` to `DEFAULT_MAX_BOUND_FIELD_ELEMENTS + 1` (= 201), **not** to `config.max_bound_field_elements + 1`. `run_stdio` (the only production serve path) wires the correct `config.max_bound_field_elements + 1`, and the two callers that matter (run_stdio + the tests) always pass it — so the fix holds in production for any custom cap. But the cap+1 coupling is a **wiring convention, not a structural guarantee**, and **no test exercises run_stdio's wiring**. The F-1 collision re-opens if a custom-cap `Config` (cap ≥ 201, or cap > 201) is ever paired with a default-constructed `CodeGraph` — no current path does this. Consider deriving `query_limit` from the config at a single choke-point, or asserting `query_limit > cap` at construction.

**NEW-3 [SUSPECT — reasoned] [MINOR, latent]  `query()` fallthrough silently returns the `subclasses_of` stub for any un-dispatched `KNOWN_QUERY_VERBS` member.**
The refactored `query()` ends with an unconditional `return {"edges": [], "warning": "subclasses_of…"}` (no `if verb == "subclasses_of"` guard). A future verb added to `KNOWN_QUERY_VERBS` (required to be reachable) but without its own dispatch branch would silently return the subclasses_of stub rather than an error. Latent; add an explicit final `else: return UNKNOWN_VERB` or a per-verb assertion.

## Scope-drift rulings (RAMZA reconciles before archive; `drift_check` gate)

Complete drift set cross-checked: **exactly two** undeclared files, no others.

**SCOPE-1 [VERIFIED]  `mcp-server/src/atlas_aci/memex.py` — modified though on the exclusion list (`declared-scope.md:123`).**  **Ruling: legitimate discovery, the change was RIGHT; amend the scope doc.** The exclusion's stated rationale is "memex-ref *emission* deferred" — the 4-line best-effort-`mkdir` hardening adds no ref-emission feature, so it does not violate the exclusion's *intent*. It was *necessary*: `Config.__post_init__` **and** `Memex.__init__` both `mkdir` the same root, so fixing only the in-scope `config.py` would leave `Memex.__init__` crashing serve under `:ro` (AC-H-16). Vivi disclosed it. RAMZA should narrow the exclusion to "memex-ref emission features" or add memex.py under P0/AC-H-16.

**SCOPE-2 [VERIFIED]  `mcp-server/tests/test_thesis_negatives.py` — in no scope entry (added pass-1, undisclosed).**  **Ruling: legitimate, in-spirit; declare it.** It is a pytest mirror of AC-NEG-1/2/4/5 (criteria already in scope; harden-gate.yml's negatives step is declared) — it adds no new capability, only a local guard for in-scope criteria. Not scope creep, but a genuine undeclared-file drift Vivi should have listed in "New files declared" (`declared-scope.md:118-121`) at pass-1. RAMZA adds it there.

## Conditions on the GO
1. **NEW-1 (recommended before merge):** stop `view_file` from false-flagging complete windows; fix the `retry_hint` for pagination. Fail-safe, so not blocking, but it erodes the core un-ignorable-flag mechanism on the most-used tool.
2. **SCOPE-1 / SCOPE-2 (mandatory before archive):** RAMZA reconciles `declared-scope.md`; both changes are right, the doc is stale. `drift_check` fails until done.
3. **NEW-2 / NEW-3 / R-1 (track):** latent fragilities; close them when A1–A5 extend the query surface.

*VIGIL second pass — Verify · Isolate · Graph · Intervene · Learn. Read-only; no fixes applied, criteria untouched. F-1/F-2 fixes reproduced against source; the silent-incompleteness invariant is restored.*
