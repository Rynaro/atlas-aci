---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-03T23:00:00Z
slice_id: ACI-S21
issue: https://github.com/Rynaro/atlas-aci/issues/42
---
# ACI-S21 — import version-matched per-test Python coverage observations

## Scope

Implement issue #42 on merged base `e818a57` (main after PR #84 / ACI-S20), branch `aci-s21`.
Live marker `<!-- atlas-aci:slice:ACI-S21 -->` verified; no prior S21 plan, receipt or execution
state. Criteria bytes hash to `33df7cc632b44c4d55c5e86c664d87ce6db9aeef365ef9d06aedea0b952cc79b`
= contract table (EARS lint ok, 9 criteria). Predecessors verified on the execution base: S07
(#29) and S08 (#30) are ancestors of HEAD with COMPLETE receipts (evidence-record model, pinned
source snapshots/leases, observation records). S16 derived-generation machinery
(`import_scip`/`_publish_scip`) is the design precedent.

Observed facts (re-verified at planning):
- Closed validators: `_validate_evidence_projections` requires a symbols row for every non-file
  entity (codegraph.py:6143), every occurrence role must map to a projection table (6161), every
  resolved assertion needs an edges row (6179-6181); `ev.validate_database` requires the
  assertion's source entity to be in the same path as its evidence occurrence
  (evidence.py:676-680); occurrence kinds are closed to {definition, reference, rationale,
  legacy} (evidence.py:370). Every indexed file has a canonical file entity (`ev.file_entity`,
  codegraph.py:3510-3512).
- Every lease acquisition recomputes the snapshot binding (1326) and retrieval closure (1347)
  over all occurrences/evidence.
- Entity enumerations: retrieval (retrieval.py:98, fingerprinted), S19 diff (diff.py:105-110;
  one-sided entities become "added"), S20 seed paths (impact.py:48-51, must mirror diff), name
  seeds (indexed.py:31-36).
- MCP status facts (`published_status`, 2068-2073) count only symbols/refs/edges/rationale
  tables.
- `coverage` is a default skip pattern (config.py:38); coverage.py/pytest-cov absent from
  pyproject/uv.lock; `test_no_new_runtime_dependency` pins the core dependency set.
- `codegraph.py` uses many locals named `coverage` (`_read_coverage`, `coverage_snapshot`): the
  provider module is `providers/coverage_py.py`, imported as `coverage_py`.
- The coverage.py JSON shape is NOT yet observed: Day 1 qualifies it, with a stop rule.

### Operator decisions (asked and answered by the user, recorded 2026-11-03)

1. Storage: DERIVED GENERATION mirroring S16 — kind `coverage_import` over the current build;
   reference-only relation `test_executes` kept out of `ev.RELATIONS`; per-run provenance = one
   content-addressed producer record; runs accumulate; `index` supersedes. Edits to
   fingerprinted `codegraph.py`/`storage/evidence.py` accepted via scope amendment (generation
   ids change, as S16).
2. Query: CLI STATUS ONLY — `atlas-aci status` gains the block, every item labelled
   `observed_execution`; records reachable via evidence records/export; no MCP/protocol op.

### In

1. `providers/coverage_py.py` (stdlib + `storage.evidence` only): strict parser for the pinned
   coverage.py JSON report; context/path/line normalization; sidecar build/parse; `plan_import`
   against a reader lease; block build/validate; `same_run`; `record_ids`; `LIMITATIONS`;
   `importer_sha256()` (hash of `providers/coverage*.py`).
2. `providers/coverage_probe.py`: stdlib-only script run by the operator's TEST interpreter,
   printing the closed environment JSON (Atlas never spawns a foreign interpreter).
3. Operator CLI (`__main__.py`; nothing added to `READ_ONLY_TOOLS`): `atlas-aci coverage-probe`
   (prints probe source: `atlas-aci coverage-probe | <venv>/bin/python -I - > env.json`);
   `atlas-aci coverage-manifest --repo R --environment E --output M COVERAGE_JSON` →
   `atlas-coverage-manifest.v1` (reads only; writes only M); `atlas-aci import-coverage --repo R
   [--graph-root G] --manifest M COVERAGE_JSON` → JSON stats; `status` gains
   `execution_observations`.
4. Publication: new derived generation `coverage_import` (codegraph.py `import_coverage` +
   `_publish_coverage`, mirroring `_publish_scip` without refactoring it).
5. Evidence mapping (D5): one producer per run; `coverage_test` entities; `execution`
   occurrences at line spans; per-run evidence; `test_executes` assertions; no projection rows.
6. Rejection leaves the published graph byte-identical (AC-003/AC-007); all-or-nothing
   validation completes before the writer lock.
7. Accumulation and re-import (AC-005/AC-006), order-independent.
8. Lifecycle: `index` supersedes; failed-parse retained history never keeps coverage records;
   JSONL export/import round-trip keeps records, drops the run block (S16 parity).
9. Docs `atlas/docs/coverage-import.md`, README/INTEGRATION pointers, CHANGELOG.

### Out

Running tests or any interpreter during import; pass/fail outcomes; interpreting execution as an
assertion about behavior; branch arcs, `missing_lines`/`excluded_lines`, coverage.py
`functions`/`classes` regions; the `.coverage` SQLite data file as input; coverage's own
`dynamic_context = test_function` and static contexts; producers other than the qualified
coverage.py/pytest-cov pair; mapping nodeids onto tree-sitter test functions; traversal or MCP
exposure of `test_executes` (protocol enums, 3,436/3,448 envelopes, MCP tools unchanged);
chaining with `scip_import` in either direction; any runtime/dev dependency change (pyproject and
uv.lock untouched).

Declared implementation paths (issue): `mcp-server/src/atlas_aci/providers/coverage*`,
`mcp-server/src/atlas_aci/__main__.py`, `mcp-server/tests/slices/test_s21_*.py`,
`mcp-server/tests/fixtures/coverage/*`, `atlas/docs/*`.

Scope amendment (`.spectra/changes/aci-s21/scope-amendment.json`, recorded before
implementation): `codegraph.py`, `storage/evidence.py`, `indexing/retrieval.py`,
`query/indexed.py`, `query/diff.py`, `query/impact.py`, `mcp-server/tests/fixtures/coverage/**`
(nested fixture project), `README.md`, `INTEGRATION.md`, `CHANGELOG.md`, and Ramza/receipt/backlog
files. Explicitly untouched (gated): `protocol/*`, `server.py`, `tools/*`, `enforcement.py`,
`config.py`, `providers/scip*`, `storage/snapshots.py`, `resolvers/dependencies.py`,
`query/diff_syntax.py`, `query/traversal.py`, `pyproject.toml`, `uv.lock`.

Rightsize FULL 6 (16 files est., public CLI + status, untrusted artifact input, novel provider,
med stakes).

## Approach

### Decisions

D1 Accepted format (pinned; Day 1 confirms or stops). coverage.py JSON report from `coverage json
--show-contexts`: `meta.format == 3`, `meta.show_contexts == true`, `meta.version` in the
qualified allow-list; contexts from pytest-cov `--cov-context=test` labelled `"<nodeid>|<phase>"`,
phase ∈ {setup, run, teardown}. Candidate pins (final after the Day-1 maturity rule: newest
release ≥ 14 days old): coverage 7.10.7, pytest-cov 7.0.0, pytest 8.4.2, transitive deps pinned
by hash. Qualified values go into `provenance.json` and `coverage_py.PRODUCERS = {"coverage.py":
{<version>: {"report_format": 3, "context_mode": "pytest-cov:test", "pytest-cov":
{<version>}}}}`; widening requires a newly qualified fixture. No dev extra (lock unchanged): the
independent label oracle runs inside the producer container via coverage's own
`CoverageData.contexts_by_lineno` API.

D2 Strict parse. Bounded read (default 64 MiB); `json.loads` with duplicate-key rejection
(`object_pairs_hook`) and NaN/Infinity rejection; top-level ⊇ {meta, files} ⊆ {meta, files,
totals}; per file require `executed_lines` (sorted unique ints ≥ 1) and `contexts` (keys
`^[1-9][0-9]*$`, values lists of str); other per-file keys ignored (listed limitation); every
context line ∈ `executed_lines` (else `inconsistent_artifact`). Bounds: files ≤ 20,000; distinct
tests ≤ 100,000; nodeid ≤ 4,096 UTF-8 bytes; `test_executes` per run ≤ 500,000, per generation ≤
1,000,000; runs ≤ 16 (final values set by the Day-3 measurement, D11). Each violation has a
stable code.

D3 Normalization.
- Paths: every `files` key must satisfy `ev.relative_path` (POSIX, no `..`, no backslash, not
  absolute, already normalized) else `absolute_path`/`path_not_normalized`; operator sets `[run]
  relative_files = true` and runs from repo root. Each path must be observed `indexed` in the base
  with language `python` and `observed_source_sha256` == sidecar hash == snapshot `current[path]`
  == `sha256(lease.read(path))` (reuse S16 checks, scip.py:729-746).
- Contexts: `""` (default/static) and lines with no context entry are counted
  `unattributed_lines` and never create `test_executes`. Otherwise split at the LAST `|`; suffix
  must be a phase; nodeid = prefix whose path part (before the first `::`) must be a relative
  path ending `.py` with no `|` (so a static-context prefix like `ci|tests/...` fails
  `unsupported_context`); the remainder (class chain, function, `[param]` id) is kept verbatim —
  each parametrized variant is a distinct test. The test file must be in sidecar `test_files`,
  indexed `python` in the base, hash-equal to the snapshot (else
  `test_source_mismatch`/`manifest_incomplete`).
- Lines: L → half-open byte span of line L in the snapshot bytes excluding its terminator; lines
  split ONLY on `\n`, `\r\n`, `\r` (Python tokenizer semantics; never `str.splitlines`); `line =
  end_line = L`, `column = 0`, `end_column = span length`; L ≤ line count else
  `line_out_of_range`; a BOM belongs to line 1.

D4 Sidecar `atlas-coverage-manifest.v1` (canonical JSON + newline): `format`, `artifact_sha256`,
`tool {name: "coverage.py", version: meta.version}`, `report_format: 3`, `context_mode:
"pytest-cov:test"`, `environment` (closed probe output: `{format:
"atlas-coverage-environment.v1", python {version, implementation, cache_tag}, platform {system,
machine}, packages {coverage, pytest, pytest-cov}}`), `commit` (`{state: "recorded", head: <40
hex>, dirty: bool}` | `{state: "unavailable"}`; producer-attested, informational, never
identity), `measured_files {path: sha256}` (must exactly equal the artifact's `files` set),
`test_files {path: sha256}` (every test file named by a context). `coverage-manifest` hashes
regular files under `--repo` (symlinks/outside paths rejected). Cross-check:
`environment.packages.coverage == meta.version` (else `environment_tool_mismatch`), pytest-cov
present and allow-listed. Environment is attested, not verified (documented limitation); source
identity is verified by hashes.

D5 Records (content-addressed; `identity_basis = parsed`).
- `run_id = sha256(canonical({format: "atlas-coverage-run.v1", artifact_sha256,
  manifest_sha256}))` — content-derived, never operator-supplied.
- Producer (= AC-002's "one observation record"): `ev.producer({"coverage": {run_id, tool,
  report_format, context_mode, report_timestamp (meta.timestamp verbatim), artifact_sha256,
  manifest_sha256, environment, commit, importer_sha256}})`. "Observation record" in AC-002 means
  this run producer record, not the `observation`-type per-file extraction-state record
  (untouched).
- Test entity: `record("entity", path=<test file>, language="python", kind="coverage_test",
  name=<nodeid>, owners=[], ordinal=0)`.
- Occurrence: `kind="execution"`, `entity_id = file entity of X` (must exist in base), D3 span,
  snapshot `source_sha256`/`size`, `role = "observed_execution:" + "+".join(sorted(phases))`; one
  per (X, sha, L, phase-set), shared across tests/runs.
- Evidence: `ev.provenance(occurrence, run_producer_id)`, one per (run, occurrence).
- Assertion: `ev.assertion(test_entity, file_entity(X), "test_executes", evidence, "EXTRACTED",
  raw_target=f"{X}:{L}")`.
- Dedup: exactly one assertion per (run, nodeid, X, L) — phases merged into the role; repeated
  execution counts once. The same test/span in another run → a second assertion distinct by
  evidence/producer (AC-006).
- No symbols/refs/edges rows: `search_symbol`, `callers_of`, edges analytics and MCP status facts
  unaffected.

D6 Validator changes (narrow, coverage-only).
- `storage/evidence.py`: `COVERAGE_RELATION = "test_executes"` accepted only by the
  assertion-record check via `ASSERTION_RELATIONS = REFERENCE_RELATIONS | {COVERAGE_RELATION}` at
  408 — NOT in `RELATIONS`/`REFERENCE_RELATIONS`/`EDGE_RELATIONS` (refs/edges rows can never carry
  it); a `test_executes` assertion must be EXTRACTED with `raw_target` matching
  `^.+:[1-9][0-9]*$`; occurrence kind `execution` valid iff role
  `observed_execution:<canonical phase-set>`, state `source`, `line == end_line`, `column == 0`;
  entity kind `coverage_test` requires `owners == []`, `ordinal == 0`, name starting
  `path + "::"`; `_validate_coverage_fingerprint` (closed shape, hashes, basis parsed) dispatched
  on `set(fingerprint) == {"coverage"}` like the SCIP branch at 255. In `validate_database`, the
  "assertion source location" check is replaced ONLY for `relation == test_executes` by a stricter
  shape check (source kind `coverage_test`, occurrence kind `execution`, evidence producer
  fingerprint `{"coverage"}`, target a file entity == `occurrence.entity_id` with same path).
  Conversely, an execution occurrence may be cited only by coverage-producer evidence and
  `test_executes` assertions.
- `codegraph.py` `_validate_evidence_projections`: skip `coverage_test` at 6143; role branch
  `observed_execution:` → no projection (`continue`); assertion branch `test_executes` → no edges
  projection; plus `coverage_py.validate_records(conn)` referential completeness (every
  `coverage_test` is the source of ≥ 1 assertion; every execution occurrence has ≥ 1 coverage
  evidence; for `coverage_import`, coverage producers == block `producer_id`s).

D7 Generation `coverage_import` (codegraph.py). Block `inputs["coverage"] = {format:
"atlas-coverage-import.v1", base_generation_id: <the BUILD id, carried forward>, runs: [{run_id,
producer_id, tool, report_format, context_mode, report_timestamp, artifact_sha256,
manifest_sha256, importer_sha256, environment, commit, counts {measured_files, tests,
observed_lines, test_executes, unattributed_lines}} sorted by run_id]}`; `coverage_py.
validate_block` checks closed shape/allow-list, unique sorted runs, `producer_id ==
ev.producer(fingerprint(run))["id"]`, bounds. Kind-switch sites: 1041 (epoch-9 allow-list), 1074
(`require_current`), 1085 (snapshot pointer), 1108 (retrieval policy `available`), 1124-1141
(exact key set = build keys ∪ {"coverage"}; validate block; every run's producer row exists),
1901 (`published_status` coverage read), 2146-2148 (status block + `_read_coverage`), 2342
(`_store_generation` build branch). 2551-2555: predecessor filter becomes
`scip.scip_record_ids(full) | coverage_py.record_ids(full)`. `import_scip` unchanged; its existing
kind check (2684) rejects a `coverage_import` base.

D8 `import_coverage(artifact, manifest)`. All parsing, sidecar checks, producer allow-list,
environment cross-check, source/test identity and line mapping complete inside a
`reader_lease()` before the writer lock and before any staging file exists; rejection raises
`CoverageImportError(code)`. Base kind `build` → runs = [new]; `coverage_import` → if `run_id`
present with identical entry → `{status: "unchanged", generation_id: base}` (no write); present
with a different entry (e.g. changed importer) → reject `run_conflict` ("rebuild first");
otherwise runs = sorted union and block `base_generation_id` kept; any other kind (incl.
`scip_import`, `import`, `legacy_conversion`) → reject `base_kind_unsupported`. Capture
`(base_id, token)` pre-lock and re-verify `_publication_base()` under `_writer_lock` (TOCTOU, S16
condition 7). `_publish_coverage` mirrors `_publish_scip`: backup base; `ev.put` records (path =
X for occurrence/evidence/assertion, test file for entities, None for producer);
`_validate_evidence_projections` + `dep.validate_outputs`; recompute/delete dependency witness;
`Builder.publish` (context = base context + predecessor identity);
`_store_generation(kind="coverage_import", …, coverage=block)`; rewrite `coverage_snapshot`; new
token; `os.replace`; `builder.published = True`; retain. Order independence: records are a union
of content-addressed records, runs sorted, base id = build's, snapshot manifest does not include
the predecessor id (snapshots.py:902-908) ⇒ build→A→B == build→B→A (proven by test).

D9 Status (CLI only). `status()` gains top-level `execution_observations: []`, or one block for
`coverage_import` (name avoids the existing "coverage" snapshot/completeness vocabulary):
`format: "atlas-execution-observations.v1"`, `semantics: "observed_execution"`,
`assertion_inference: "not_performed"`, `test_outcome: "not_recorded"`, `base_generation_id`,
`runs[]` (block run entries + `semantics: "observed_execution"`), `counts` (from published rows:
`runs`, `tests`, `observed_lines`, `test_executes`), `limitations [{name, reason}]` sorted. MCP
`published_status` gains no field. Vocabulary never uses covers/covered/verified/asserts/passed/
tested_by/proves.

D10 No-MCP-surface exclusions (single predicate `kind <> 'coverage_test'` each):
`indexing/retrieval.py:98` (skip; retrieval descriptor and document digest equal the base
build's); `query/indexed.py` NAME_SQL; `query/diff.py:108` and `query/impact.py:49` together
(S19/S20 path parity). `query/diff_syntax.LIMITATIONS` unchanged (adding an entry would change
every S19/S20 change-set id); coverage entities are observations, not code entities (documented).
S20 entity-count admission (impact.py:141) keeps counting all entities — over-protects only;
documented.

D11 Cost. Coverage rows enter every lease's binding and retrieval closure. Day-3 measurement:
dogfood run of atlas-aci's own pytest suite with pinned coverage/pytest-cov in a throwaway
container, imported into atlas-aci's own graph — record `test_executes`, import time,
lease-acquisition time build vs `coverage_import`, DB size in `.spectra/evidence/aci-s21/
cost.json`; set the D2 per-generation bound so lease time at the bound ≤ 2× build on that repo;
document.

D12 AC-003 policy: reject the whole artifact, no quarantine. A run is one execution of one code
state; mixing verified and mismatched files would publish observations of code that differs from
the snapshot; quarantine adds a second trust class every reader must discriminate; matches S16
(condition 14). Operators scope measurement with coverage `[run] source/omit`.

### Limitations (static `coverage_py.LIMITATIONS`)

`test_outcome: pass_fail_not_recorded`; `semantics: execution_is_not_an_assertion_about_behavior`;
`absence: unexecuted_or_unmeasured_lines_carry_no_evidence`; `unattributed:
lines_without_test_context_not_attributed`; `import_time:
import_time_lines_attributed_to_active_context`; `granularity: statement_start_lines_only`;
`branches: branch_arcs_not_imported`; `producer:
only_qualified_coverage_py_json_format_3_pytest_cov_test_contexts`; `environment:
producer_attested_not_verified`; `exposure: not_traversable_not_exposed_over_mcp`; `chaining:
does_not_combine_with_scip_import`; `portability: jsonl_round_trip_keeps_records_drops_run_block`.

### Work plan (timebox 3 engineering days)

Day 1 AM — qualification (BEFORE any importer code): resolve pins with hashes under the maturity
rule; digest-pin `python:3.12-slim` and `python:3.13-slim`; run the fixture project, capture
`.coverage` + `coverage json --show-contexts`; record in
`.spectra/evidence/aci-s21/qualification.json` the meta/per-file key sets and values, the `""`
representation, phase suffixes, parametrized nodeid spelling, relative path spelling,
context-lines ⊆ `executed_lines`, `[run] context=` static interplay, 3.12 and 3.13 both recording
contexts (check coverage core, `COVERAGE_CORE`), and line numbering for CRLF, form-feed,
U+2028-in-string and non-ASCII files agreeing with the `bytes.splitlines` oracle. Record base
invariants in `.spectra/evidence/aci-s21/base-invariants.json` (MINIMUM_BUDGET,
COLLECTION_MINIMUM_BUDGET, FIXED_ERROR_ENVELOPE_CAP, `ev.RELATIONS`/`REFERENCE_RELATIONS`/
`EDGE_RELATIONS`, `atlas-aci tools` sha256, full pytest at base, every test pinning a literal
generation/producer id). STOP RULE: if the pinned tools do not emit per-line `nodeid|phase`
contexts in the JSON report, or line numbering disagrees with the byte oracle on any fixture
file, stop and report BLOCKED with the observed shape; never silently switch to `.coverage`
SQLite or `test_function` contexts.

Day 1 PM — fixture (`generate.sh`, `generate_labels.py` not importing `atlas_aci`,
`provenance.json`, two runs A on 3.12 and B on 3.13 with env + sidecar files, `labels.json`,
`.gitattributes` `project/** -text`, `conftest.py` `collect_ignore = ["project"]`);
`providers/coverage_py.py` parser/sidecar/probe; `evidence.py` validators;
`test_s21_artifact.py`.

Day 2 — `codegraph.py` import/publish/kind sites/projection exemptions/predecessor filter/status;
the four exclusions; CLI; `test_s21_contract.py`.

Day 3 — `test_s21_lifecycle.py`, `test_s21_isolation.py`, `test_s21_cli.py`; cost evidence (D11)
and bounds; docs; gates in the lock-faithful Docker dev image (rebuild from branch first; s11/s12
runtime-hash lists include `codegraph.py`); RUN-01/02.

Cut order (only if overrunning; each cut listed as a limitation): (1) doc depth/README polish;
(2) `commit` capture (always `{state: "unavailable"}`); (3) row-derived per-run status counts
(keep block counts); (4) run B on 3.13 (second 3.12 run instead; different timestamp → distinct
run); (5) `coverage-probe` command (document `python -I <site>/atlas_aci/providers/
coverage_probe.py`); (6) dogfood cost run → synthetic 100k-assertion fixture. Never cut:
all-or-nothing rejection before the lock, TOCTOU re-check, D6 narrowness, D10 exclusions,
lifecycle filter, order-independence test, G-RO, G-MCP, G-NOLEAK.

### Independent checker conditions

Independent ATLAS review: APPROVE_WITH_CONDITIONS, recorded in
`.spectra/plans/aci-s21.review.json`. Every condition below is binding on implementation and
overrides conflicting text above or below.

1. Entity-id seed leak. `traversal` resolves `{"entity_id": X}` through
   `IndexedReader.record("entity", id)` without a kind filter (traversal.py:60-62;
   indexed.py:154-162).
   - `IndexedReader.record` must return None for an entity of kind `coverage_test`.
   - G-NOLEAK adds neighborhood and path calls with a real `coverage_test` entity_id
     (`state == "absent"`), plus a `file(X)` seed with an incoming neighborhood that contains no
     `test_executes`.
2. Cost bounds.
   - (a) `retrieval.expected` keeps `proofs` only for occurrences of kind `definition`.
   - (b) `_validate_evidence_projections` `by_evidence` skips `relation == "test_executes"`.
   - (c) D11 records lease wall time and peak memory (tracemalloc or RSS) at the proposed bound,
     not only a time ratio.
   - (d) Shipped defaults are measured values. If the Day-3 measurement is cut, the defaults are
     at most 100,000 `test_executes` per generation and at most 50,000 per run.
   - (e) Explicitly bound the total of coverage occurrences plus evidence, not only assertions.
3. Context-line source. The Day-1 fixture includes multi-line call, dict, decorator,
   docstring, comprehension and `async` cases, on both interpreters. Qualification records
   whether context lines are a subset of `executed_lines`. If not:
   - the line source is the context keys (the raw recorded line);
   - `executed_lines` is not an identity or rejection criterion and is used only for counts;
   - no import is rejected on that basis.
4. Rootdir. Pytest nodeids are relative to rootdir.
   - D3 and the docs require pytest rootdir == repo root.
   - The dogfood run uses `--rootdir=<repo>`.
   - A violation yields `manifest_incomplete` with a rootdir hint, and a test covers it.
5. Commit capture. Mandatory: `commit` is always `{state: "unavailable"}` (capture is cut), so
   regenerating the sidecar cannot create a different run id for the same artifact.
6. Check placement. The check "coverage producers == block producer_ids" lives in
   `_generation_data` (the 1124-1141 branch), not in `_validate_evidence_projections`, which
   runs against the staged old manifest during accumulation. `coverage_py.validate_records`
   keeps only block-independent referential checks.
7. Exact validate_database rule. For every assertion:
   `relation == test_executes` ⇔ producer fingerprint keys == {"coverage"} ⇔ occurrence kind
   == `execution`. The evidence branch (evidence.py:656-671) independently enforces
   coverage producer ⇔ execution occurrence. For `test_executes`:
   - source kind `coverage_test`; target kind `file`;
   - `target.id == occurrence.entity_id`, `target.path == occurrence.path`, language python;
   - `line == end_line`;
   - `raw_target == f"{occurrence.path}:{occurrence.line}"` exactly;
   - no candidates; resolution EXTRACTED.

   An `execution` occurrence has a non-null `entity_id` of kind `file`. Add negative crafted-JSONL
   tests for each clause, including a `call` assertion citing coverage evidence.
8. S20 admission. `impact.py:141-146` counts occurrences as well as entities. Either document
   that a large coverage import can trip `diff_occurrence_limit`, or exclude
   `kind='execution'`/`coverage_test` from those counts. A test pins the chosen behavior.
9. Ruff on fixtures. `pyproject.toml` is gated. On Day 1, verify that fixture files with CRLF,
   form feed, U+2028 and non-ASCII content pass `ruff check` and `ruff format --check`
   unmodified:
   - no mixed line endings within a file;
   - per-file `# ruff: noqa` / `# fmt: off` if needed.

   If they still fail, amend scope explicitly; never edit pyproject silently.
10. No execution paths. The AST scan also denies `subprocess`, `os.system`, `os.popen`,
    `os.fork`, `ctypes`, `runpy`, `importlib.import_module`, `eval`, `exec` and `compile` in
    `providers/coverage*.py`, except the probe's documented stdlib set.
    `coverage-manifest --output` is rejected when it resolves inside `--repo`.
11. Line-number oracle. Day-1 qualification also checks line numbering against Python
    `tokenize`/`compile` line numbers on the fixture files. AC-003(d) (synthetic artifact plus a
    sidecar re-made by the real `coverage-manifest`) is accepted.

Cut order (replaces the one above):
1. doc depth / README polish;
2. (already mandatory) commit capture;
3. row-derived per-run status counts;
4. the `coverage-probe` command;
5. run B on 3.13 (use a second 3.12 run);
6. the dogfood run → a synthetic 100k fixture that still measures RSS and lease time.

Never cut conditions 1, 2(a-b), 6 and 7, nor the items listed as never-cut above.

## Stories

As an agent operator with a Python test suite, I want to import one coverage.py run with per-test
contexts, so that the graph records which tests were observed executing which source lines of
the exact indexed snapshot.

As a reviewer of graph evidence, I want each observation bound to its run, environment and
verified source bytes and labelled only as observed execution, so that coverage is never mistaken
for an assertion, a pass, or evidence about code that was not imported.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s21.criteria.md` (sha256 `33df7cc6…cc79b`). All in
`mcp-server/tests/slices/test_s21_contract.py`; each repo is a tmp copy of
`tests/fixtures/coverage/project` built by the real indexer with an external graph root; imports
use the committed real artifacts/sidecars; no mocks of behavior under test; every `all(...)`
preceded by a non-empty assertion.

| AC | Test | Observable |
| --- | --- | --- |
| AC-S21-001 | test_test_execution_mapping | for every run-A label row (nodeid, path, line, phases, start/end byte, text): exactly one `test_executes` EXTRACTED assertion, source = `coverage_test` entity (name == nodeid, path == nodeid file), target = `file_entity(path)`; evidence occurrence kind `execution`, `line == end_line == L`, labelled bytes, `snapshot[start:end] == text`, role `observed_execution:<phases>`; imported (nodeid, path, line) set == label set; `""`-only lines have no assertion; setup+run duplicate line → one assertion with role `…:run+setup`; CRLF/form-feed/U+2028/non-ASCII rows hand-checked |
| AC-S21-002 | test_run_provenance | every coverage assertion → evidence → exactly one producer whose fingerprint `coverage.run_id` == run id recomputed in-test from provenance digests, with tool/report_format/context_mode, environment == `environment-A.json`, artifact/manifest sha == provenance, `importer_sha256 == coverage_py.importer_sha256()`; all run-A assertions share that one producer id; status `runs[0].producer_id` and block `producer_id` equal it |
| AC-S21-003 | test_coverage_source_mismatch | (a) measured file edited before build → `source_mismatch`; (b) test file edited → `test_source_mismatch`; (c) measured path with `failed` observation → `document_not_indexed`; (d) line beyond EOF (synthetic artifact + matching re-made sidecar) → `line_out_of_range`; each: `published_bytes` unchanged, generation id unchanged, no staging leftovers, no `{"coverage"}` producer in DB, status `execution_observations == []` |
| AC-S21-004 | test_no_assertion_inference | status block `semantics == "observed_execution"`, `assertion_inference == "not_performed"`, `test_outcome == "not_recorded"`, every run semantics `observed_execution`; every record cited by a coverage producer has relation `test_executes`, occurrence kind `execution`, role prefix `observed_execution:`; recursive key/value scan of the block finds none of covers/covered/verified/verifies/asserts/asserted/passed/passes/tested_by/proves; a `missing_lines` line has no record and no status claim; MCP `search_symbol(<nodeid>)` returns none |
| AC-S21-005 | test_coverage_reimport | import A → `imported` G1; import A again → `unchanged` G1, identical `published_bytes`, equal canonical set (sorted canonical coverage records + `inputs["coverage"]` + status block); fresh graph root at same repo path, build + import A → G1 and equal set; A→B→A again → `unchanged` |
| AC-S21-006 | test_distinct_run_preserved | A then B → G_AB ≠ G_A; `runs` has 2 entries sorted by run_id with distinct producer ids/environments; for a label row in both runs exactly two assertions with evidence producers {A, B}; both producer records exist; status lists both runs; JSONL export contains both producers; B then A in another root → G_AB with equal canonical set |
| AC-S21-007 | test_missing_coverage_identity | parametrized, each → stable code: nonexistent manifest; manifest missing a measured file; extra file; missing a test file; `artifact_sha256` ≠ artifact; manifest without/malformed environment; artifact `show_contexts` false or `contexts` absent; absolute path; `meta.format != 3`; unknown coverage version; env coverage version ≠ `meta.version`; unsupported context (static prefix); base generation `scip_import`. Each: bytes/generation unchanged, no staging, no coverage producer. CLI `import-coverage` without `--manifest` → exit 2 |
| AC-S21-RUN-01 | gate | SHA-256 manifest of declared tracked implementation files equal before/after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths |
| AC-S21-RUN-02 | gate | `slice_id` + `criteria_sha256` equal in interrupted and resumed receipts; one authoritative receipt path `.spectra/state/aci-s21.execution.json` |

Supporting tests:
- `test_s21_artifact.py`: parser strictness (duplicate keys, NaN, bool-as-int, unsorted/duplicate
  lines, bad line keys, context lines ∉ `executed_lines`, nodeid bounds, last-`|` split with `|`
  inside a param id), bounds via Limits parameter, D3 line splitter vs oracle (incl. lone CR),
  probe/sidecar closed shapes, AST scan that `providers/coverage*.py` import only stdlib +
  `atlas_aci.storage.evidence`.
- `test_s21_cli.py`: `coverage-probe` output runs under `python -I -` and yields a valid
  environment; `coverage-manifest` byte-determinism and rejection of symlink/outside paths;
  `import-coverage` stdout JSON, exit 1 on rejection with stdout empty, usage exit 2; `status`
  block.
- `test_s21_lifecycle.py`: after coverage, `index` and `index --since` → kind `build`, no
  coverage records, status block `[]`; forced parse failure keeps no coverage records in retained
  history; export/import JSONL round-trip → equal canonical coverage record set, kind `import`,
  no block; concurrent publication between lease and lock aborts import, graph unchanged; lease,
  `epoch_ok`, `published_status` succeed on `coverage_import`.
- `test_s21_isolation.py`: gates below (G-NOLEAK, G-S16, G-DIFF-NEUTRAL, G-NOEXEC, G-RO,
  G-ENVELOPE pins).

Invariance gates:
- G-ENVELOPE: MINIMUM_BUDGET == 3436, COLLECTION_MINIMUM_BUDGET == 3448, FIXED_ERROR_ENVELOPE_CAP
  == base, `ev.RELATIONS` == literal 8-element set, `REFERENCE_RELATIONS`/`EDGE_RELATIONS` ==
  base.
- G-MCP: exactly 7 tools; `READ_ONLY_TOOLS` unchanged; `atlas-aci tools` JSON byte-identical to
  base; `git diff --exit-code e818a57 -- mcp-server/src/atlas_aci/{protocol,tools,server.py,
  enforcement.py,config.py}`.
- G-NOLEAK (on a `coverage_import` generation vs its build): MCP `published_status` (minus
  generation/snapshot ids) equal; retrieval descriptor and document digest equal;
  `search_symbol`, `graph_query` retrieve/context for the nodeid/test name return no
  `coverage_test`; incoming neighborhood on `file(X)` over all RELATIONS has no `test_executes`.
- G-S16: `git diff --exit-code e818a57 -- providers/scip* tests/slices/test_s16_*
  tests/fixtures/scip`; all `test_s16_*` pass unchanged; `import-scip` onto `coverage_import` →
  rejected, bytes unchanged; `import-coverage` onto `scip_import` → `base_kind_unsupported`.
- G-DIFF-NEUTRAL: S19 `diff.compare(build, coverage_import)` of the same source → empty change
  set; all `test_s19_*`/`test_s20_*` pass unchanged.
- G-RO: SHA-256 inventory of the source repo (graph root external) identical before/after
  `coverage-manifest` and `import-coverage`, success and every rejection.
- G-NOEXEC: during `import_coverage`, `subprocess.Popen` and `os.exec*`/`os.posix_spawn`
  monkeypatched to raise → import succeeds (no test/interpreter execution).
- G-DEPS: `pyproject.toml` and `uv.lock` byte-unchanged; `test_no_new_runtime_dependency` passes.

Commands (record exits; no required skips):
```sh
cd mcp-server
uv run pytest tests/slices/test_s21_contract.py -q
uv run pytest tests/slices/test_s21_*.py tests/slices/test_s16_*.py tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/test_thesis_negatives.py -q
uv run pytest -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```

## Confidence

Computed with `ramza-score`. Publication reuses the proven S16 staged/atomic derived-generation
path; records are content-addressed, so replay identity and order independence are structural.
Main uncertainties: real JSON/context shape (Day-1 stop rule); the narrow `validate_database`
exemption (first relaxation of the source-location invariant); per-lease cost growth from
coverage rows (D11); literal generation-id pins in existing tests (inventoried Day 1).

## Rejected Alternatives

- Test-file entity as source with nodeid in `raw_target`: no new entities/exclusions, but misuses
  `raw_target` semantics and T is not an entity.
- Mapping nodeids onto tree-sitter test functions: couples observations to extractor identity;
  redefinitions/dynamic tests ambiguous; the issue says store observations separately from static
  bindings.
- Importing `.coverage` SQLite data: native contexts, but its internal schema is not a documented
  export format; numbits decoding adds risk.
- Quarantining mismatched files (partial publish): second trust class (D12).
- Projection rows (symbols/edges) for coverage: leak into `search_symbol`, `callers_of`, MCP
  status facts and analytics.
- `test_executes` in `ev.RELATIONS`: changes protocol enums and measured envelopes, exposes
  coverage over MCP.
- Runtime/dev coverage dependency: unnecessary; labels come from the producer container's own
  coverage API.
- Atlas spawning the test interpreter (`--python`): executes foreign code from Atlas; the
  operator pipes the probe instead.

## Risks

- JSON shape/context spelling differs from D1, or 3.13 contexts unsupported under the default
  core → Day-1 stop rule; never guess.
- Closed validators need more touch points than listed → amend scope with reasons before
  editing.
- Lease cost grows with coverage rows → D11 measurement + bounds, documented.
- Fixture `.py` files linted by ruff / collected by pytest → keep ruff-clean (`noqa: RUF001` on
  the U+2028 line) + `collect_ignore`; `.gitattributes` protects CRLF bytes.
- Existing tests pinning generation/producer ids churn with `codegraph.py`/`evidence.py`/
  `retrieval.py` edits → inventoried Day 1; updating a pin only where the test's purpose is not
  the id itself, recorded.
- Timebox 3 engineering days; at the limit report completed checks and gaps with a narrow
  follow-up.
