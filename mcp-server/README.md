# atlas-aci

Reference MCP server implementing the ATLAS bounded Agent-Computer Interface.

Read-only by construction. The agent talking to this server cannot mutate
your repository, period.

## Install

```bash
uv sync
```

## Run

```bash
# stdio transport (for Claude Code, Copilot, Cursor)
uv run atlas-aci serve --repo /path/to/your/repo

# Index the code graph (one-time, ~30-120s for large repos)
uv run atlas-aci index --repo /path/to/your/repo

# Inspect tools
uv run atlas-aci tools
```

## Tools exposed

| Tool | Purpose | Bound |
|------|---------|-------|
| `view_file` | Read a window of a file | ≤100 lines/call |
| `list_dir` | List directory entries | ≤200 entries/call |
| `search_text` | Ripgrep-backed regex | ≤50 matches/call |
| `search_symbol` | Index lookup | central element cap + byte ceiling (truncated + flagged) |
| `graph_query` | Code-graph queries | central element cap + byte ceiling (truncated + flagged) |
| `test_dry_run` | Run a test in sandbox | wall-clock ≤30s, stdout ≤8KiB |
| `memex_read` | Byte-exact excerpt fetch | bounded by Memex backend |

Every tool response also passes through a central dispatch-layer chokepoint
(`server.py`'s `_call_tool`) that element-caps any declared list-valued field
and enforces an absolute serialized-byte ceiling, so no current or future
tool can ship unbounded by forgetting its own cap. See
[`../README.md` §Mechanical bounds](../README.md#security-invariants).

## Explicitly absent

`edit_file`, `write_file`, `shell_exec`, `git_*`, `migration_apply`,
`deploy_run`. These belong to other agents (APIVR-Δ, infra tooling).

If you need them, you don't need this server — you need a different one.

## Architecture

```
┌──────────────────────────────────────┐
│ MCP server (this package)            │
│  ┌────────────────────────────────┐  │
│  │ enforcement.py                  │  │  ← bounds, read-only guard, logging
│  └─────────────┬──────────────────┘  │
│                │                       │
│  ┌─────────────▼──────────────────┐  │
│  │ tools/{view_file, list_dir, ...}│  │
│  └────┬────────────┬───────────────┘  │
│       │            │                   │
│  ┌────▼────┐  ┌────▼─────┐            │
│  │ memex.py│  │codegraph │            │
│  │(hashed  │  │(tree-    │            │
│  │ dir)    │  │ sitter)  │            │
│  └─────────┘  └──────────┘            │
└──────────────────────────────────────┘
```

## Testing

```bash
uv run pytest
```

## See also

- `../SETUP.md` for end-to-end setup including host wiring
- `../../atlas/tools/bounded-aci-spec.md` for the contract this implements
