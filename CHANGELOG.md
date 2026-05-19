# Changelog

All notable changes to **atlas-aci** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **feat(codegraph): Rust Tree-sitter query (`QUERIES["rust"]`).** Adds symbol extraction for free `fn`, impl methods, `struct`/`enum` (kind `class`), `trait` (new kind `trait`), `mod` (kind `module`), and reference extraction for free calls, scoped calls (`Mod::fn()`), and method calls (`x.method()`). Callers opt in via `langs=["rust"]` or `--langs rust`. Closes #11.

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
