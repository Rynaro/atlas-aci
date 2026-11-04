# Changelog

All notable changes to **atlas-aci** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Rails: explicit associations, model tables and migration table references (ACI-S25).** New
  opt-in adapter `rails_models` (`atlas-aci index --frameworks rails_models`, library
  `CodeGraph(repo, frameworks=("rails_models",))`; the closed registry is now `{rails,
  rails_models}`, both need `ruby`) with an optional operator table map
  (`--rails-table-map FILE`, format `atlas-rails-table-map.v1`, strictly parsed by the CLI, exit 2
  and no graph directory on any rejection). It reads only `app/models/**` (not concerns) and
  `db/migrate/*.rb` from the build's captured bytes, never runs Ruby, Rails or a migration and
  never opens a database. Per declaration it records a `rails_model` occurrence, evidence from a
  `rails_models` producer and an assertion: `association` (a literal `class_name:` linked to the
  model class it names, `INFERRED` only when exactly one existing candidate name holds exactly one
  model, `AMBIGUOUS` with candidate ids otherwise, `UNRESOLVED` with `class_not_found` or
  `target_not_recognized_model`, and `unsupported_association` for through, polymorphic, implicit
  or computed forms), `model_table` (a literal `self.table_name` or one unambiguous operator-map
  entry) and `migration_mentions` (literal `create_table`, `add_column`, `remove_column`). The last
  two are target-less `EXTRACTED` assertions carrying the table in the canonical `raw_target`
  ("literal declaration extracted, no graph edge"; tables are not entities). Facts are rebuilt
  globally on every enabled build and purged by a build without the flag. The adapters `rails` and
  `rails_models` are independent and combinable. No MCP operation, enum, traversal, `edges` row,
  entity kind or dependency change; the tools JSON is byte-identical. Editing `codegraph.py` and
  `storage/evidence.py` changes the native producer id again: every evidence, assertion, link,
  generation and snapshot id differs from the previous release for all graphs and existing indexes
  rebuild in full on the first `--since` run (entity and occurrence ids are unchanged). Documented
  in `docs/frameworks/rails.md`.

- **Rails: explicit routes mapped to controller actions (ACI-S24).** New opt-in
  `atlas-aci index --frameworks rails` (library `CodeGraph(repo, frameworks=("rails",))`, closed
  registry `{rails}`; an unknown name or `rails` without `ruby` is a usage error, exit 2). The
  adapter reads only the root `config/routes.rb` from the build's captured bytes (no Ruby, Rails,
  rake or bundler is ever run) and records, per declaration, a `rails_route` occurrence, evidence
  from a `rails` producer and a `routes_to` assertion carrying the literal verb, path, target text
  and the `rails_convention.v1` label. A unique controller action is `INFERRED`, two or more are
  `AMBIGUOUS` with their candidate ids, and everything else is `UNRESOLVED` with a closed reason
  (`action_not_found`, `controller_not_found` meaning "no indexed class",
  `unsupported_route_construct`); `resources`, `mount`, `namespace`, interpolation, engines and any
  runtime-dependent target are diagnostics and are never expanded. Rails facts are rebuilt globally
  by the adapter on every enabled build (the S10 plan contributes per-file `remove_path` and the
  `affected` classification; it does not republish per-file facts), and a build without the flag
  purges any earlier rails records. Documented in `docs/frameworks/rails.md`. No MCP operation,
  enum, traversal, `edges` row or dependency change; the tools JSON is byte-identical.

- **OpenAPI 3.1 contract import and contract diff (ACI-S29).** New operator commands
  `atlas-aci openapi-snapshot DOCUMENT` and `atlas-aci openapi-diff --before F --after F`
  (libraries `atlas_aci.importers.openapi31` and `atlas_aci.contracts.openapi_diff`) import one
  OpenAPI 3.1 JSON document into a canonical `atlas-contract-snapshot.v1` and explain contract
  changes between two such snapshots as `atlas-contract-diff.v1`. Operations are identified by
  uppercase method plus normalised path template (`operationId` is metadata only); every element
  carries the artifact digest, the snapshot id and a JSON pointer. Findings: `operation_removed`,
  `newly_required_parameter`, informational `operation_added`, and `review_required` with a closed
  reason for everything unsupported, unresolved (remote, outside-document, cyclic, over-deep or
  over-budget references) or ambiguous; there is no global verdict. S19 is not invoked: only its
  change-set shape is reused. The `snapshot_id` is a contract identity that binds the artifact
  bytes, the profile and the options; it is a contract snapshot, not a source snapshot, and the
  provenance is artifact digest plus JSON pointer. Binding a contract snapshot to a source snapshot
  is a follow-up. Documented in `docs/importers/openapi.md`. No MCP operation, graph, producer,
  schema or dependency change; the tools JSON is byte-identical.
- **Counterfactual patch analysis (ACI-S32).** New operator command
  `atlas-aci counterfactual --repo R --base-snapshot ID --patch P [--rules F]` (library
  `atlas_aci.counterfactual`) applies a proposed unified diff to the base snapshot's own source
  bytes, indexes the result into a temporary namespace outside the repository (never published,
  removed on every exit path), and prints a canonical `atlas-counterfactual-report.v1`: the S19
  source diff between the declared base and the staged snapshot, and, with a rule document, the
  per-rule before/after S31 classification (introduced, resolved, persisting). The report is
  labelled hypothetical and identified by the base snapshot, patch digest and producer
  configuration. Unsafe or non-applying patches (escaping paths, symlink, submodule, binary,
  rename/copy, mode changes, context mismatch, limits) are rejected with a closed reason before
  anything is indexed; truncated or unresolved analyses are marked `incomplete` and never shown as
  a clean delta. The working tree, git state and active graph are never modified. Documented in
  `docs/counterfactual-analysis.md`, including measured cost (staging re-indexes every observed
  file). No MCP operation, producer, schema or dependency change; the tools JSON is
  byte-identical.

- **Forbidden-import architecture rules (ACI-S31).** New operator command
  `atlas-aci check-architecture --repo R --rules rules.json` (library `atlas_aci.architecture`)
  evaluates a closed `atlas-architecture-rules.v1` document (`import` relation, closed selector
  grammar) against one exact, read-only graph snapshot and prints a canonical
  `atlas-architecture-report.v1`. Each rule is `violated` (with the shortest, deterministic
  import chain and per-hop canonical provenance), `incomplete_due_to_budget` (count-based bounds
  only, no clock), `unknown` (unresolved, dynamic or unsupported-language boundaries, listed) or
  `satisfied_within_scope`. An invalid document is rejected with a structured `invalid_rule`
  error before the graph is opened. The command reports and never enforces (exit 0 is not a
  compliance verdict); `satisfied_within_scope` is not a proof of absence, and real repositories
  mostly yield `unknown` because only package-local relative imports resolve. Documented in
  `docs/architecture-rules.md`. No MCP operation, producer, schema or dependency change; the
  tools JSON is byte-identical.

- **Offline evidence report (ACI-S37).** New operator command
  `atlas-aci evidence-report ENVELOPE --html OUT.html --json OUT.json` (library
  `atlas_aci.reports.evidence`) exports one recorded `graph_query op=context` envelope as a
  self-contained, script-free HTML page and a canonical JSON report
  (`atlas-evidence-report.v1`). The input is validated with the shipped schema and
  `validate_context`; a closed set of typed rejection codes (`envelope_too_large`,
  `invalid_input`, `invalid_envelope`, `unsupported_operation`, `mixed_snapshot_evidence`,
  `report_too_large`) covers every failure. Lease URIs and continuation cursors are omitted and
  listed, truncation and unresolved markers appear in both formats, every source-derived value
  is escaped text, and repeat export is byte-identical. Documented in
  `docs/evidence-reports.md`; frozen envelopes and goldens live in
  `mcp-server/tests/fixtures/evidence_report/`. No MCP operation, dependency, schema or
  runtime source outside `reports/` and the CLI changes; the tools JSON is byte-identical.

- **Reproducible v3 evidence and compatibility package (ACI-S23).** `docs/evidence/v3/`
  publishes a release report derived by a pure, stdlib-only aggregator
  (`scripts/eval/v3_aggregate.py`) from immutable raw records written by a recorder
  (`scripts/eval/v3_record.py`) under the frozen manifest `atlas/evals/v3/manifest.v1.json`.
  It pairs three variants on one frozen task input and corpus commit (pre-v3 atlas-aci 2.0.0
  by image digest, v3 HEAD, and a deterministic search-then-read baseline), reports
  useful-span results per arm at 2000/8000/16000 byte-quarter-token budgets, indexing and
  query cost counters from `CodeGraph.build()` (cold, warm, incremental no-op, refresh), a
  P0 binding/snapshot regression gate (30 explicit pytest ids), an honest host/SDK matrix,
  and marks every live agent outcome `unmeasured` (`no_operator_budget`). Replaying the
  aggregation on the committed records reproduces the canonical section byte for byte.
  `scripts/run-canaries.py --server-json` (and `stdio_runner.run_cli`) run a task manifest
  against another server command. The README and SETUP canary sections are corrected
  (`--canaries-dir`, the real `--host stdio`). `atlas/evals/span_metrics.py` holds the S18
  useful-span functions as a shared, verified copy. No MCP operation, dependency, schema
  or runtime source changes; the tools JSON is byte-identical.

- **Evidence-backed validation plan (ACI-S22).** New operator command
  `atlas-aci validation-plan` (library `atlas_aci.query.validation`) composes the S20
  impact change set and propagation, computed once, with S21 observed coverage and
  returns a ranked, **advisory** list of tests to run. Observed execution matches a
  changed span only through a byte-identical file snapshot of the before or after side
  (`post_change_validation` is true only for the after side); coverage matching neither is
  `stale`, counted and never used; repeatable `--coverage-graph-root` adds external sources.
  Tests found only by static relationships are labelled `structural` (reverse path, or
  same-name reference) under the closed `atlas-pytest-default-conventions.v1` rule and rank
  below observed ones. Every changed span nothing maps to is an explicit gap with closed
  reasons. The record `atlas-validation-plan.v1` is closed, canonical and content-addressed
  (`plan_id`), carries exact assertion/evidence/occurrence/producer/run/generation/snapshot
  ids, and is replay-identical for unchanged inputs. Nothing is run, imported or
  collected; no MCP operation is added (still exactly seven tools, tools JSON
  unchanged) and no earlier module, schema or generation id changes. See
  `atlas/docs/validation-plan.md` and `atlas/evals/validation_plan_comparison.py`.

- **Per-test Python coverage observations (ACI-S21).** New operator commands
  `atlas-aci coverage-probe` (prints the environment probe you pipe to the test
  interpreter), `atlas-aci coverage-manifest` (producer-time sidecar
  `atlas-coverage-manifest.v1`) and `atlas-aci import-coverage REPORT --manifest M`
  record which tests were observed executing which source lines of the exact
  indexed snapshot, as a derived `coverage_import` generation. Only coverage.py
  7.16.2 with pytest-cov 7.1.0 `--cov-context=test` JSON (format 3) is accepted;
  runs accumulate and re-import is idempotent. Observed execution is stored as a
  `coverage_test` entity, an `execution` occurrence over the line's bytes and a
  `test_executes` assertion (outside `RELATIONS`: not traversable, no protocol,
  envelope or MCP change; still exactly seven tools) and is never an assertion
  about behavior or a pass/fail. All validation runs before the writer lock and
  a rejection leaves the graph byte-identical; `atlas-aci index` supersedes it;
  `atlas-aci status` gains `execution_observations`. See
  `atlas/docs/coverage-import.md`. No runtime or dev dependency is added.

- **Potential change impact through typed reverse paths (ACI-S20).** New
  `graph_query op=impact` (closed, additive `atlas.graph.v1` operation; still
  exactly seven MCP tools) and operator command `atlas-aci impact` share one
  library function (`atlas_aci.query.impact`). Over two held snapshots it builds
  the S19 change set, propagates each changed entity (removed from the before
  graph; signature, body, added and unknown from the after graph) through bounded
  reverse traversal under one shared work allowance, and returns ranked entities
  that are *potentially affected* with canonical assertion-proof paths, same-name
  unresolved/ambiguous boundaries and honest incomplete-coverage reasons. Static
  reachability only: never a breakage claim. The before snapshot is operator
  configuration (`serve --before-graph-root [--before-repo]`); requests carry no
  paths. New typed errors `IMPACT_UNCONFIGURED`, `SNAPSHOT_DIFF_REJECTED`,
  `IMPACT_DIFF_TOO_LARGE`. The indexer, generation ids and every earlier schema
  member, envelope minimum and traversal bound are unchanged. See
  `atlas/docs/change-impact.md`.

- **Symbol and contract snapshot diff (ACI-S19).** New operator command
  `atlas-aci diff` and read-only library `atlas_aci.query.diff` compare two
  explicit, already-built snapshots (same checkout with two graph roots, or
  worktrees of one repository under one root) and print the canonical
  `atlas-symbol-diff.v1` change set: `added`, `removed`, `signature`, `body`,
  `format` and `location` changes plus `unknown` (closed reasons) and a separate
  list of conservative rename/move candidates. Fingerprints are recomputed from
  hash-verified source-snapshot bytes; every record references both snapshots.
  Unrelated or unverifiable snapshots are rejected with a closed reason (exit 1).
  Nothing is added to the seven MCP tools, schemas or envelopes, and the indexer
  and generation ids are unchanged. See `atlas/docs/symbol-diff.md`.

- **Official SCIP import from one TypeScript producer (ACI-S16).** New operator
  commands `atlas-aci scip-manifest` (producer-time sidecar
  `atlas-scip-manifest.v1`: producer, declared position encoding, index digest,
  per-document source hashes) and `atlas-aci import-scip INDEX --manifest M`
  publish a derived `scip_import` generation from the current `build`
  generation. Only `@sourcegraph/scip-typescript` 0.4.0 (UTF-16 columns) is
  accepted; the index is decoded by a strict dependency-free wire decoder for the
  vendored, hash-pinned `scip.proto` (v0.6.1) with explicit bounds. Definitions
  become `scip_*` entities; reference-only occurrences become the new
  `reference` relation (never `call`; not traversable, so protocol enums and
  analytics are unchanged). Every validation runs before the writer lock and any
  rejection leaves the published graph byte-identical. `atlas-aci status` gains a
  `semantic_imports` block. See `atlas/docs/scip-import.md`. `protobuf` is a
  dev-only test oracle; no runtime dependency is added.

- **Measured TSX structural extraction (ACI-S14).** `.tsx` files are now indexed
  instead of skipped: class and function declarations, arrow/function-expression
  component declarators, methods, import occurrences (default, named, aliased,
  namespace, `import type`, each preserving the local alias, module source and
  kind), and call/construct/superclass relations with the same scoped structural
  binding as TypeScript. JSX value tags are recorded with the new `jsx` raw
  reference relation and are always unresolved
  (`jsx_semantic_binding_unavailable`): a JSX tag is never a call or an edge.
  Imports stay unresolved (`tsx_module_resolution_unavailable`). Export status
  (`named`/`default`) is stored in symbol binding context.
- Capability snapshot `atlas-capabilities.v4` with explicit TSX definitions,
  relations and limitations (`tsx_*`: module resolution, JSX binding, intrinsic
  JSX, export specifiers, default-export identifiers, anonymous default exports,
  dynamic binding). v1, v2 and v3 snapshots remain readable and keep TSX
  unsupported. The producer fingerprint now includes `extractors/tsx.py`, so
  the next `index` run re-extracts every file.

### Changed

- **Producer-id churn (ACI-S24).** The native producer fingerprint covers `codegraph.py` and
  `storage/evidence.py`, which this slice edits, so for every graph (flag on or off) evidence,
  assertion and projection link ids, generation ids, snapshot ids and export bytes change, and
  existing indexes rebuild in full on the first `--since` run. Entity ids, occurrence ids and the
  symbols/refs/edges rows are unchanged.
- `.tsx` is no longer reported under `unsupported_skipped`; only `.go` and
  `.java` remain recognized without an extraction query.

## [2.0.0] - 2026-07-10

A code-exploration server that was previously silently over-promising —
three responses had no size cap despite a documented "mechanical bounds"
guarantee, `serve` could crash under its own documented read-only
deployment, and several docs described behavior the code didn't have —
is now mechanically honest, and gains a real code graph: confidence-tagged
call/inheritance edges, degree-centrality "god nodes," deterministic
community detection, comment-derived rationale nodes, and a portable,
git-committable export of the whole graph.

**Read this before upgrading — action required and one behavior change:**

- **You must re-index.** The on-disk DB moved to
  `.atlas/graph.<SCHEMA_EPOCH>.db` (currently epoch `5`) and its schema
  changed materially (new `edges`/`rationale` tables, dropped
  `refs.enclosing`). There is no in-place migration — the DB is pure,
  disposable derived data, always rebuilt from source. Run
  `atlas-aci index --repo <path>` once. `serve` itself always starts
  even against a stale or missing index — it does not check the epoch
  at startup — but any tool call that touches the code graph
  (`search_symbol`, `graph_query`) returns a structured
  `INDEX_UNAVAILABLE` error naming that exact command, rather than
  serving stale or wrong results, and `serve` performs zero writes
  under `.atlas` either way. See `README.md`'s "Migration" section.
- **`callers_of` (and `subclasses_of`)'s response shape changed.** Each
  edge now carries a `source: {path, line, name, kind}` object (the real
  caller context, resolved from the materialized edge table) instead of
  the old field, which was named `enclosing` and was **always `null`** —
  no release ever populated it. If you parsed `enclosing` expecting a
  value, you were always getting `null`; the new `source` object is the
  first version of this data that actually exists.

### Fixed

- **The "mechanical bounds" guarantee is now actually mechanical.** Three
  response surfaces had no cap at all, despite the README's documented
  invariant that every tool response is bounded: `search_symbol`'s
  `definitions` list, `graph_query`'s `callers_of` `edges` list, and — one
  level deeper, found while building the new edge table below — an
  `AMBIGUOUS` edge's own nested `candidates[]` list. All three now route
  through one central dispatch-layer chokepoint (`server.py`'s
  `apply_central_bounds`) that element-caps every tool's declared
  list field(s) and enforces an absolute serialized-byte ceiling,
  truncating and flagging (`truncated`, `returned_count`, `more_available`,
  `retry_hint`) rather than silently returning everything. A
  registry-completeness test fails the build if any current or future
  list-returning tool/verb — top-level or nested — has no cap registered,
  so this can't quietly regress.
- **`serve` no longer crashes under its own documented `--read-only`
  deployment.** `Config`/`Memex` unconditionally created their working
  directories on startup; under the README's own documented
  `--read-only`/`:ro` Docker mount with no separately-mounted writable
  memex volume, that `mkdir` raised `EROFS` and took the whole server down
  at startup. Both are now best-effort: a missing/unwritable memex root
  degrades to `memex_read` returning `NOT_FOUND` (no tool currently emits
  a memex ref anyway), while every other tool is unaffected. Verified with
  a real `docker run --read-only` smoke test, not a permission-bit
  simulation — the earlier check couldn't reproduce this at all, since
  root ignores file-mode bits and a `chmod` can't simulate the kernel-level
  `EROFS` a real read-only mount enforces.
- **`--since` was never documented accurately.** It does not diff a git
  ref — it keys purely on each file's on-disk `(mtime_ns, size)` against
  the last indexed pass, and the marker value you pass (`HEAD~1`, etc.) is
  never read. Every doc claiming otherwise is corrected (`README.md`,
  `INTEGRATION.md`, `SETUP.md`, `CLAUDE.md`, `mcp-server/Dockerfile`); the
  documented `post-commit` hook example still works, but only because it
  happens to enable incremental mode, not because the ref is diffed.
- **The shipped `v0.4.0` tag's lockfile disagreed with itself.**
  `mcp-server/uv.lock` pinned `atlas-aci` at `0.3.1` while
  `mcp-server/pyproject.toml` said `0.4.0` — a released tag whose own
  lockfile named the wrong version of the package it locks. Both are now
  `2.0.0`, `uv.lock` was deliberately re-locked (a 1-line diff — nothing
  else had drifted), and a test asserts the two agree on every `pytest`
  run so this exact regression can't ship silently again.
- **Doc-honesty batch**, corrected repo-wide rather than at a single site:
  removed the vaporware Prism Ruby-specialist-mode references (no such
  mode ships — Ruby is `tree-sitter-language-pack`, same as every other
  language); corrected the canary-suite pass-rate claims (the host
  dispatcher is a `NotImplementedError` stub with an explicit deferred
  note, and there was never a real pass rate behind the quoted numbers);
  `mcp-server/README.md`'s tools table no longer calls `search_symbol`
  "unbounded (cheap)" or `graph_query` "implementation-defined"; `CLAUDE.md`
  no longer references a `.atlas/symbols.db` artifact the code never
  created; `README.md`'s repository-layout listing includes
  `test_codegraph.py`/`test_server.py`/`test_schema_epoch.py`/`test_export.py`;
  `server.py`'s `memex_read` description no longer implies another tool
  emits a memex ref (none does); added the top-level `LICENSE` (Apache-2.0)
  the README already promised.

### Added

- **Materialized call/inheritance edge table with a deterministic
  confidence enum.** Every reference is now resolved once, at index time,
  into `EXTRACTED` (single, syntactically-qualified candidate),
  `INFERRED` (single candidate, name-unique but unqualified), or
  `AMBIGUOUS` (more than one candidate — always returned in full, never
  dropped, never given fractional weight). Deterministic and rule-based;
  no LLM is ever in this path. `subclasses_of` is now real (it was
  previously an empty stub with a warning) and resolves Ruby
  `superclass`/`include`/`extend`/`prepend` and the Python/JS/TS
  superclass-equivalent.
- **God nodes** (`graph_query`'s `god_nodes:` verb) — degree-centrality ranking
  over the confident (`EXTRACTED` ∪ `INFERRED`) subgraph. Pure Python
  arithmetic; no graph-algorithm dependency.
- **Communities** (`graph_query`'s `communities:` verb) — a hand-rolled,
  deterministic label-propagation implementation, shipped only after
  passing a pre-registered probe against a `networkx` Louvain baseline on
  two real Rails-scale reference repos (the probe itself runs `networkx`
  in a throwaway, ephemeral environment that never touches this project's
  own dependency tree — `networkx` appears nowhere in
  `pyproject.toml`/`uv.lock`, absolutely and unconditionally).
- **Rationale nodes** (`graph_query`'s `rationale:` verb) — comment-derived "why"
  annotations (`NOTE:`/`HACK:`/`TODO:`/ADR-and-RFC references, in
  Ruby → Python → JS/TS order) promoted to first-class nodes linked to the
  code they explain via a `rationale_for` edge. A structurally separate
  relation from call/inheritance edges — a rationale node can never enter
  `god_nodes`/`communities`/any confidence-based resolution.
- **Portable, deterministic export/import** (`atlas-aci export <path>` /
  `atlas-aci import <path>` — CLI-only; see "Why read-only" in
  `README.md` for why neither is an MCP tool). The export is canonical,
  byte-deterministic JSONL — sorted keys, explicit record-level ordering
  independent of insertion order, LF-only line endings, verified
  byte-identical across a macOS/Linux CI matrix — so one person can build
  the graph and commit it, and everyone else reproduces the identical DB
  on `import` without re-parsing a single source file. `import` validates
  every path in the file is repository-relative and repository-contained
  before inserting a row (an absolute or `../`-escaping path is rejected,
  never inserted verbatim) and rejects a truncated, hand-edited, or
  wrong-schema-epoch file with a clean error. **Known bound, not silently
  hit:** GitHub rejects a committed file at or above 100 MB; the larger of
  this project's two pinned reference repos (Spree) exports to ~84.6 MB
  (84.63% of that ceiling) — `atlas-aci export` warns as its output
  approaches and crosses this bound; see README.md's AC-REL-2 section for
  what to do beyond it.
- **CI that actually runs.** `.github/workflows/ci.yml` runs `pytest`,
  `ruff check`, `ruff format --check`, and `mypy` on every pull request
  against `main` (previously only a tag-triggered release workflow
  existed — the test suite never ran on a PR); a cross-platform
  (macOS + Linux) job verifies the export's byte-determinism; a
  `--read-only`/`:ro` Docker smoke test verifies `serve` stays alive and
  writes nothing under `.atlas`; and a separate `harden-gate.yml` workflow
  blocks any augmentation-path PR while these checks are absent or
  failing, regardless of branch-protection configuration.
- **Schema-epoch DB substrate.** `.atlas/graph.db` is now
  `.atlas/graph.<SCHEMA_EPOCH>.db` (currently epoch `5`). The DB is pure
  derived data, so there is no in-place schema-migration ladder — a schema
  change bumps `SCHEMA_EPOCH` (and its paired `EXPECTED_DDL_HASH`
  constant) instead. Sweeping stale-epoch files and rebuilding happen
  *only* on the `index` (write) path; `serve` never mutates `.atlas`.
  Full rebuilds write to a temporary file and atomically replace the
  target under a single-writer lock, so two concurrent `index` runs (e.g.
  the documented post-commit hook) cannot corrupt the DB.
- **Dead-language honesty.** `.tsx`/`.go`/`.rs`/`.java` are recognized
  extensions with no Tree-sitter query support; the indexer reports
  "unsupported extension skipped: N files" instead of silently indexing
  them to nothing. Not a coverage commitment — no new grammars were added.
- `search_symbol`'s `kind` enum is now derived mechanically from the kinds
  the indexer actually produces across every shipped language (previously
  stale: missing `mixin`, `selector`, `heading`, etc.).

## [0.4.0] - 2026-07-07

### Added
- **feat(codegraph): incremental indexing via `--since` so large repos skip unchanged files.** `CodeGraph.build()` was fully destructive — every run did `DELETE FROM symbols`/`refs` then re-indexed the whole tree, and passing `--since` silently re-inserted every file (producing duplicate rows). Full re-indexes after each commit are impractical on Rails-scale repos. A new `files` manifest table (`path`, `mtime_ns`, `size`, `lang`, `indexed_at`, created with `CREATE TABLE IF NOT EXISTS` so existing `.atlas/graph.db` files upgrade transparently) now tracks per-file state:
  - **Full mode (`since=None`)** wipes `symbols`/`refs`/`files`, re-indexes everything, and records each file's `(mtime_ns, size)` — establishing a baseline for a later incremental pass.
  - **Incremental mode (`--since`)** consults the manifest: files unchanged since the last pass are skipped (their symbols/refs carry forward untouched), changed/new files are re-extracted after purging their stale rows, and files removed from disk have all their rows deleted.
  - `build()` stats gain `files_skipped` and `files_removed`.
  - Chose `(mtime_ns, size)` over git-diff because the indexer targets arbitrary directories not guaranteed to be git repos; changed files always get fresh mtimes after checkout/pull/commit, serving the post-commit-hook use case. The `--since` CLI help text now describes the real behavior.

## [0.3.1] - 2026-06-20

### Security
- **Bump transitive dependencies to clear newly-disclosed HIGH advisories so the release Trivy gate passes.** The `v0.3.0` tag built and pushed an image but its release pipeline failed the `HIGH/CRITICAL` Trivy gate on six fixable advisories disclosed since `0.2.3` (all in `mcp`'s transitive tree), so no GitHub Release was published. This patch floors the affected packages via `[tool.uv] constraint-dependencies` (constraints, not new direct deps — they bind only what's already resolved and stop a future re-lock from regressing below the fix), verified locally against the built image with `trivy 0.69.3` (`--severity HIGH,CRITICAL --ignore-unfixed`):
  - `cryptography` 46.0.7 → 49.0.0 — GHSA-537c-gmf6-5ccf (vulnerable OpenSSL bundled in wheels; fixed ≥48.0.1)
  - `pyjwt` 2.12.1 → 2.13.0 — CVE-2026-48526 (authentication bypass via forged tokens)
  - `python-multipart` 0.0.26 → 0.0.32 — CVE-2026-42561, CVE-2026-53539 (parser DoS)
  - `starlette` 1.0.0 → 1.3.1 — CVE-2026-48818 (SSRF / NTLM credential theft), CVE-2026-54283
- Supersedes the incomplete `v0.3.0` tag; this is the first published release carrying the static-site indexing feature.

## [0.3.0] - 2026-06-20

### Added
- **feat(codegraph): index static-site file types so ATLAS works on Jekyll/Hugo repos.** The code graph previously only carried symbol queries for `ruby`, `python`, `javascript`, and `typescript`, so a Jekyll site (SCSS, HTML, YAML, Markdown, shell) indexed to almost nothing. Added Tree-sitter symbol queries and extension mappings for five more grammars:
  - `scss` (`.scss`, `.css`) — `@mixin`/`@function`/placeholder/`$variable` defs, class & id selectors, and `@include` refs. A `#match?` predicate keeps only `$`-prefixed declarations as variables (not every `color:` property).
  - `html` (`.html`, `.htm`) — elements carrying an `id` (anchor / JS-hook targets); an `#eq?` predicate excludes `class`/`href`/other attributes.
  - `yaml` (`.yml`, `.yaml`) — every mapping key (`_config.yml`, `_data/*`, front matter).
  - `markdown` (`.md`, `.markdown`) — ATX and setext headings as the document outline.
  - `bash` (`.sh`, `.bash`) — function defs plus command invocations as refs, so `callers_of:<fn>` resolves call sites.
- `codegraph.DEFAULT_LANGS` — single source of truth for the default language set (derived from the query table). The `index --langs` flag and `CodeGraph(...)` default now both flow from it, so adding a grammar extends the default index automatically.

### Changed
- **`_extract` now iterates Tree-sitter *matches* instead of raw captures.** Each `@def.<kind>` node stays grouped with the `@name` it owns and `#eq?`/`#match?` predicates are applied, replacing the brittle `_find_name_child` first-identifier-child heuristic (which could not name SCSS placeholders, Markdown headings, YAML keys, or shell functions). Backward compatible with the existing Ruby/Python/JS/TS queries.
- `DEFAULT_SKIP_PATTERNS` now excludes `_site`, `.jekyll-cache`, and `.sass-cache` so generated static-site output stays out of the index.

## [0.2.3] - 2026-05-06

### Fixed
- **fix(image): set `HOME=/tmp` in production image so non-default UID overrides can read `$HOME` (tree-sitter `$HOME`-relative I/O EACCES).** The baked `USER atlas:10001` has `/home/atlas` at mode `0700`. When eidolons CLI overrides `-u` to match host UID, the process cannot read `$HOME` → EACCES → every source file `parse_failed`. Setting `HOME=/tmp` in the `ENV` block directs all `$HOME`-relative I/O to a tmpfs path that is always readable and writable, regardless of `-u`.
- **Container index `ModuleNotFoundError: tree_sitter_language_pack`.** The
  production `mcp-server/Dockerfile` rebuilt transitive deps fresh from PyPI
  via `pip install /tmp/*.whl`, ignoring `uv.lock`. When upstream
  `tree-sitter-language-pack==1.6.3` shipped a restructured wheel that no
  longer exposes a top-level `tree_sitter_language_pack` module, every fresh
  build silently produced an image that failed at `atlas-aci index` time.
  - `mcp-server/pyproject.toml` — tighten `tree-sitter-language-pack` to
    `>=0.3,<1.6.3` so even bare `pip install` resolution avoids the broken
    release.
  - `mcp-server/Dockerfile` — install transitive deps from a
    `uv export --frozen --no-dev`-derived `requirements.txt`, then install
    the project wheel with `--no-deps`. The lockfile is now load-bearing
    in the production image, matching `Dockerfile.dev`.
