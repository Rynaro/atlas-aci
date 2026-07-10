# Changelog

All notable changes to **atlas-aci** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (v2.0.0 P0 — hardening gate)
- **CI that actually runs.** `.github/workflows/ci.yml` runs `pytest`,
  `ruff check`, `ruff format --check`, and `mypy` on every pull request
  against `main`. Previously `.github/workflows/` held only the tag-triggered
  `release.yml`; the 35+ existing tests never ran on a PR.
- **Central bounds chokepoint (`server.py`'s `apply_central_bounds` /
  `dispatch_tool_call`).** Every tool response — and every `graph_query`
  verb — now passes through one dispatch-layer gate that element-caps the
  tool's declared `_bounded_field`(s) and enforces an absolute serialized-byte
  ceiling, truncating and flagging (`truncated`, `returned_count`,
  `more_available`, `retry_hint`) rather than hard-failing except at the
  byte-ceiling backstop. This closes the two tools that previously called
  `enforcement.record()` with **no** cap at all — `search_symbol`'s
  `definitions` list and `graph_query`'s `callers_of` edges were genuinely
  unbounded, contradicting the README's "mechanical bounds" invariant. A
  registry-completeness test (`test_every_list_returning_tool_registers_a_
  bounded_field`) fails the build if any current or future list-returning
  tool/verb has no non-empty `_bounded_field` registered — a no-op
  registration cannot pass silently.
- **Schema-epoch DB substrate.** `.atlas/graph.db` is now
  `.atlas/graph.<SCHEMA_EPOCH>.db`. The DB is pure derived data (fully
  reconstructable from source), so there is no in-place schema-migration
  ladder — a schema change bumps `SCHEMA_EPOCH` and its paired
  `EXPECTED_DDL_HASH` constant instead. Sweeping stale-epoch files and
  rebuilding on an epoch mismatch happen *only* on the `index` (write) path;
  `serve` never mutates `.atlas` — on a mismatch it fails fast with a
  structured error naming the required `index` command, matching the
  documented `--read-only`/`:ro` deployment. Full rebuilds write to a
  temporary file and atomically replace the target path under a
  single-writer lock, so two concurrent `index` runs (e.g. the documented
  backgrounded post-commit hook) cannot corrupt the DB. A `rationale`
  relation (schema only; no confidence-enum column) is folded into this
  epoch ahead of the rationale-extraction work that will populate it.
- **Dead-language honesty.** `.tsx`/`.go`/`.rs`/`.java` are recognized
  extensions with no Tree-sitter query support; the indexer now reports
  "unsupported extension skipped: N files" instead of silently indexing
  them to nothing. Not a coverage commitment — no new grammars were added.
- **`search_symbol`'s `kind` enum** is now derived mechanically from the
  kinds the indexer actually produces across every shipped language
  (previously stale: missing `mixin`, `selector`, `heading`, etc.).
- Added a top-level `LICENSE` (Apache-2.0), matching the identifier already
  declared in `pyproject.toml`.

### Fixed (doc-honesty batch)
- Corrected every repo-wide claim that `--since` diffs a git ref — it keys
  only on each file's on-disk `(mtime_ns, size)` and never reads the marker
  value (`README.md`, `INTEGRATION.md`, `SETUP.md`, `CLAUDE.md`,
  `mcp-server/Dockerfile`).
- Removed the vaporware Prism Ruby-specialist-mode references (`SETUP.md`,
  `INTEGRATION.md`, `codegraph.py`'s module docstring,
  `mcp-server/pyproject.toml`'s empty `ruby` extra) — no such mode ships.
  `.tsx` is also no longer claimed to be "handled by the TS grammar".
  Ruby, like every other language, is covered by `tree-sitter-language-pack`
  and nothing else.
- Corrected the canary-suite pass-rate claims (`README.md`, `SETUP.md`) —
  the host dispatcher is a `NotImplementedError` stub
  (`scripts/run-canaries.py`, which now carries an explicit deferred note);
  there was never a real pass rate behind the quoted 50-60%/≥80% numbers.
- `mcp-server/README.md`'s tools table no longer describes `search_symbol`
  as "unbounded (cheap)" or `graph_query` as "implementation-defined" now
  that both route through the central bounds chokepoint.
- `CLAUDE.md` no longer references a `.atlas/symbols.db` artifact the code
  never created; `INTEGRATION.md`/`SETUP.md` correct the same fabricated
  `symbols.db`/`routes.json`/`manifest.yaml` file list.
- `README.md`'s repository-layout listing now includes
  `test_codegraph.py`, `test_server.py`, and `test_schema_epoch.py`
  (previously only `test_enforcement.py` was listed).
- `server.py`'s `memex_read` tool description no longer claims other tools
  return `memex://` refs — none currently do.

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
