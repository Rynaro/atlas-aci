# Changelog

All notable changes to **atlas-aci** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
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
