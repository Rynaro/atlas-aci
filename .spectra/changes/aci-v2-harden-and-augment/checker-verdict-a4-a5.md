---
artifact: checker-verdict
phase: A4 (rationale nodes) + A5 (portable export) + AC-REL-1
change_id: aci-v2-harden-and-augment
checker: vigil
audited_commit: 5dbc8e5
criteria_sha256: 5c3adddbd075a7c12bdd965ee760484a04e5a9a6a4ce05302cbd7bc4147fc7e7  # CONFIRMED at 5dbc8e5
a4_verdict: GO-WITH-CONDITIONS
a5_verdict: GO-WITH-CONDITIONS
---

# Checker Verdict — A4 (rationale nodes) + A5 (portable export)

All reads pinned to committed blob `5dbc8e5` (not the working tree, which Vivi is
editing). Criteria hash confirmed. Suite at `5dbc8e5`: **174 passed** (isolated
`uv run --frozen` worktree). Verdicts:

- **A4: GO-WITH-CONDITIONS**
- **A5: GO-WITH-CONDITIONS**

Both features work and pass every named criterion. The conditions are latent
defects the criteria do not test — the same "criteria are a floor" shape as the
coordinator's defect-18 (A5 has no user-reachable surface). The nineteenth
defect is in A5's **import**: it validates the byte integrity of what it is
handed (content_hash) but not the semantic completeness/safety of it.

## What A4 actually extracts from solidus (real code, not the author's fixtures)

Indexed `solidusio/solidus@4026945d…` with the pinned code: **33 rationale rows**,
all Ruby (this repo writes `# TODO:`/`# NOTE:`/`# FIXME:`, not ADR/RFC): TODO 24,
NOTE 7, FIXME 2; 0 ADR/RFC labels; 27 with a resolved enclosing target, 6 null
(top-of-file). **Judgement: the comments it finds are right** — markers, text,
and line numbers are accurate (spot-checked `order.rb:822 → validate_line_item_availability`,
exact). One target class is **wrong-but-not-crashing**: see MINOR-1.

## Per-criterion table

| ID | Verdict | Basis |
|----|---------|-------|
| AC-A4-1 | PASS [VERIFIED] | Rationale node + `rationale_for` edge to enclosing scope; verified on real solidus (order.rb:822→`validate_line_item_availability`). Caveat MINOR-1 (setter coarsening). |
| AC-A4-2 | PASS [VERIFIED] | Python/JS/TS markers ported from graphify prefix set; Ruby reuses `#` markers (disclosed). |
| AC-A4-3 | PASS [VERIFIED] | JS/TS ADR/RFC canonicalized (`// see ADR-7` → `ADR-0007`), prefix-free ADR still a node. An ADR pointing at a nonexistent file is fine — the label is a string, no file resolution. |
| AC-A4-4 | PASS [VERIFIED] | Rationale kept out of resolution: it is a separate `RationaleComment`/`rationale`-table path, never a `symbol`/`ref`/`edge`; `PRODUCED_KINDS` (from `@def.`) cannot contain it (`@comment.rationale` ≠ `@def.`). A `# NOTE:` **inside a string literal** is not captured (tree-sitter distinguishes comment nodes from strings) — verified Ruby+Python. |
| AC-A4-5 | PASS [VERIFIED] | No `@comment.rationale` capture for scss/html/yaml/markdown/bash; a bash `# HACK:` produces no rationale node — verified. |
| AC-A4-6 | PASS [VERIFIED] | **Structural table separation** (stronger than a filter): `rationale_for` lives in the `rationale` table with no `confidence` column; `confident_edges()` reads `FROM edges` only. There is no filter to bypass — I injected a rationale and confirmed 0 `rationale_for` rows ever enter `edges`, so god_nodes/communities/the probe can never see one. |
| AC-A5-1 | PASS [VERIFIED] | Canonical JSONL: `sort_keys`, `separators=(",",":")`, `ensure_ascii=True`, explicit `newline="\n"`; float-guard raises `TypeError` (verified) since no column is a float. |
| AC-A5-2 | PASS [VERIFIED] (export) | Export path keys are repo-relative and re-anchor on load. **Condition MAJOR-1: import does not enforce this** — a `../`/absolute path imports verbatim. |
| AC-A5-3 | PASS [VERIFIED] | Byte-identical; **robust to hostile env** — identical sha256 under `LC_ALL=tr_TR.UTF-8` (Turkish dotless-i), `C.UTF-8`, `PYTHONHASHSEED` 0/1/999/random, with unicode identifiers, a unicode filename, and CRLF source. SQLite `ORDER BY` (BINARY) and `json.dumps sort_keys` (codepoint) are both locale-independent; no `.lower()` in the sort path. |
| AC-A5-4 | PASS [VERIFIED] | Idempotent roundtrip (export→import→export byte-identical; second import same counts); rejects corrupted hash, wrong epoch, truncated header/body with diagnosable `ValueError`. **Condition MINOR-2** (missing-field → raw KeyError, an untested corruption path). |
| AC-A5-5 | PASS [VERIFIED] | No graph/union merge driver; regenerate-from-source documented (`test_no_merge_driver_shipped`). |
| AC-A5-6 | PASS [VERIFIED] | Header carries `schema_epoch` + `content_hash` (sha256 of body). **Condition MINOR-3** (`record_count` never validated on import). |
| AC-A5-7 | PASS [VERIFIED] | Explicit `ORDER BY` over every tie-breaking column, never rowid; independent of insertion order by construction. |
| AC-A5-8 | PASS [VERIFIED] | `_iter_source_files` sorts by relative-path string (codepoint), not `rglob` order. |
| AC-REL-1 | PASS [VERIFIED via CI + my determinism attacks] | Cross-OS diff green in PR #19; my locale/hashseed/unicode/CRLF attacks all reproduced byte-identically. **SUSPECT hazards MINOR-4** (unicode NFC/NFD, case-only collisions) undisclosed. |

## Ranked findings

### MAJOR-1 [VERIFIED] — the nineteenth defect: import validates bytes, not path semantics
`import_jsonl` (codegraph.py:2161) gates on `content_hash` (byte integrity) and
`schema_epoch`, then inserts every record's `path` **verbatim**. I imported a
crafted-but-valid-hash export whose symbol `path` was `"../../etc/passwd"` — it
imported OK, no rejection. AC-A5-2 promises the artefact "re-anchors on load,"
which is true only for **relative** paths; an absolute path (`/etc/passwd`) would
not re-anchor at all, and a `../` escape is stored as-is. The content_hash proves
the bytes are what the header claims — it says nothing about whether those bytes
are repo-relative, repo-contained records. This is the campaign's recurring shape
(validate the data handed, not its provenance/completeness), now on the import
path. **Reachability is low** (atlas-aci's own `export_jsonl` always writes
`str(path.relative_to(repo))`, so only a hand-crafted/foreign export hits it) and
**downstream `view_file` sandboxing** should contain any later dereference — so
it is a defense-in-depth gap, not an active exploit, but AC-A5-2's guarantee is
unenforced where the untrusted data actually enters. Condition: validate imported
paths are relative and repo-contained (reject `..`/absolute/drive-letter) before
insert.

### MINOR-1 [VERIFIED] — A4 coarsens the target of a rationale inside a Ruby setter
`_resolve_rationale_target` (codegraph.py:1079) picks the tightest **captured**
symbol containing the comment. Ruby setter methods (`def foo=(x)`) are not
captured — the `(method name: (identifier) @name)` query misses the setter name
node — so a rationale comment inside a setter attributes to the enclosing
**class** instead. Verified on solidus: `taxon.rb:105`'s `# NOTE:` is inside
`def child_index=(idx)` (line 100) but is attributed to class `Taxon`; solidus has
~61 uncaptured `def foo=(` setters (0 captured). The comment, marker, text, and
line are correct; only the `rationale_for` target is broader than the true
enclosing scope. Root cause is upstream (symbol extraction omits setters), but it
surfaces in A4's headline output and also affects `callers_of`/god-node
attribution for any setter method. AC-A4-1 is still satisfied (the class *is* an
enclosing scope); this is a target-quality condition, not a criterion failure.

### MINOR-2 [VERIFIED] — another unguarded parse path: missing field → raw KeyError
`import_jsonl` wraps `json.loads` (the truncation case Vivi self-caught at
`5dbc8e5`) but then accesses `rec["name"]`/`rec["line_end"]`/… directly (only
`candidates` uses `.get`). A valid-JSON body record missing a required field,
with a correctly-recomputed `content_hash` (so it passes the integrity gate),
raises a raw `KeyError: 'line_end'` traceback instead of the diagnosable
`ValueError` every other corruption path returns — verified. Same defect class as
the self-caught truncated header, one field-access layer deeper. Low reachability
(own exports are complete; the hash gate catches byte corruption), so it is a
diagnosability gap, not data loss. Condition: wrap the field extraction (or
validate record shape) with the same "regenerate from source" `ValueError`.

### MINOR-3 [VERIFIED] — header `record_count` is never validated
Importing a header claiming `record_count: 999` against a real 2-record body
(valid hash) imports 2 records and silently ignores the count — the importer
never compares `len(records)` to `header["record_count"]`. Not load-bearing
(`content_hash` is the real integrity anchor and would catch a dropped body
line), but it is a stored summary field the verifier of every OTHER number in
this campaign would have recomputed. Condition: assert `len(records) ==
record_count`, or drop the decorative field.

### MINOR-4 [SUSPECT] — undisclosed cross-OS hazards for AC-REL-1
The exporter is deterministic given identical input, but two inputs can differ
across OSes for the same source tree: (a) **unicode filename normalization**
(macOS presents NFD, Linux NFC) and (b) **case-only-colliding filenames**
(`a.rb`/`A.rb`: two files on Linux, one on a case-insensitive macOS volume) both
change the exported path strings/set. The export docstring names the Windows
backslash-separator cousin as out-of-scope but not these two. Likely mitigated in
practice (git `core.precomposeUnicode` is default-on for macOS checkouts; the
pinned reference repos have no such filenames) and AC-REL-1's CI diff passes on
the current source — hence SUSPECT, not VERIFIED. Worth naming alongside the
Windows hazard the docstring already discloses.

## Confirmed sound (not defects)

- **Behavioural fingerprint (my earlier defect-15/17 fix) is sound.** It now runs
  the real D3a export path (`probe-export-confident-graph.py`) via `uv run
  --frozen` on a committed fixture and hashes the output — so it *behaviourally*
  covers `_resolve_source_node` (the gap I flagged). `_target_kind()` deliberately
  does **not** flip it, and that reasoning is **correct**: the D3a export's node
  identity is `(path, line, name)` with no `kind`, so `_target_kind` (which only
  sets `kind` metadata) is invisible to the graph the fingerprint measures and
  cannot change any Q. Cross-machine stability rests on `uv run --frozen` (a hard
  uv dependency; a box without uv cannot compute it) + deterministic tree-sitter;
  the A3 bit-for-bit reproduction across two machines is strong evidence it does
  not flip on a different box within the supported matrix.
- **AC-A4-4 / AC-A5-1 negative-space guards** (rationale never in `PRODUCED_KINDS`;
  float raises) are real mechanical guarantees, not aspirations.

## Known, already-found (not re-counted as my finding)

- **Defect-18 (coordinator): A5 has no user-reachable surface.** Confirmed at
  `5dbc8e5`: `grep -c export` in `__main__.py` and `server.py` = 0; no CLI verb,
  no MCP tool. Every AC-A5-* passes via pytest calling `export_jsonl()`/`import_jsonl()`
  directly. Vivi is adding the surface in the working tree (which is why reads are
  pinned to the blob). Flagged for closure; not scored against A5's criteria.
