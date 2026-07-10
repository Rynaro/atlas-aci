# Changelog

All notable changes to **atlas-aci** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
