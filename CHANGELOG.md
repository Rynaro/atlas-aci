# Changelog

All notable changes to **atlas-aci** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
