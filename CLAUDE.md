# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`atlas-aci` is the reference implementation of the **ATLAS ACI** (Agent-Computer Interface) — a read-only MCP server that exposes bounded code-exploration tools to AI agents. The server is intentionally **read-only by construction**: it exposes exactly 7 tools (`view_file`, `list_dir`, `search_text`, `search_symbol`, `graph_query`, `test_dry_run`, `memex_read`) and rejects any write/mutate operations at the enforcement layer.

## Common commands

All commands run from `mcp-server/`.

```bash
# Install dependencies
uv sync

# Run the MCP server (stdio transport)
uv run atlas-aci serve --repo /path/to/repo

# Build the code-graph index for a repo
uv run atlas-aci index --repo /path/to/repo --langs ruby,python,javascript,typescript

# Incremental re-index
uv run atlas-aci index --repo /path/to/repo --since HEAD~10

# Print tool manifest as JSON
uv run atlas-aci tools --repo /path/to/repo

# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Type check
uv run mypy src/
```

Run canary suite from repo root:
```bash
uv run python scripts/run-canaries.py --repo /path/to/repo --canaries atlas/evals/canary-missions.md --output /tmp/canary-results.json
```

## Architecture

The MCP server (`mcp-server/src/atlas_aci/`) has a layered design:

1. **`__main__.py`** — Click CLI with three subcommands: `serve`, `index`, `tools`
2. **`server.py`** — MCP protocol wiring. Registers `tools/list` and `tools/call` handlers. Every tool call passes through enforcement before reaching the tool implementation.
3. **`enforcement.py`** — The security/reliability core. Enforces: read-only tool allowlist, path-traversal rejection, rate limiting (sliding window), per-tool output bounds (line caps, match caps, byte caps), and telemetry recording. All bounds are mechanical, not advisory.
4. **`config.py`** — Dataclass holding repo path, memex root, all bound limits, and skip patterns. One instance per server process.
5. **`tools/`** — Individual tool implementations. Each receives `(arguments, config, enforcement)` and returns a dict. `search_text` wraps ripgrep, `view_file` reads lines with pagination via `next_cursor`, `test_dry_run` runs a subprocess with timeout.
6. **`codegraph.py`** — Tree-sitter-based symbol indexer. Produces `.atlas/symbols.db` and `.atlas/graph.db` (SQLite).
7. **`memex.py`** — Hashed-directory key-value store for byte-exact excerpt retrieval.

**Key invariant**: If a tool name is not in `enforcement.READ_ONLY_TOOLS`, the call is rejected with `FORBIDDEN` before any code runs.

## Key conventions

- Python ≥3.11, managed with `uv`
- Ruff for linting/formatting (line-length 100, target py311)
- pytest with `asyncio_mode = "auto"` — test functions can be plain `async def`
- structlog for all logging (always to stderr)
- `hosts/` contains per-editor wiring instructions (Claude Code, Copilot, Cursor) — these are documentation, not code
