# atlas-aci

Reference MCP server and setup bundle for the **ATLAS bounded
Agent-Computer Interface**. Read-only by construction — the agent
talking to this server cannot mutate your repository. Not by prompt,
not by convention. Mechanically.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](mcp-server/pyproject.toml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)](mcp-server/pyproject.toml)
[![Status](https://img.shields.io/badge/status-reference%20impl-orange.svg)](#status)
[![MCP SDK](https://img.shields.io/badge/mcp-%E2%89%A51.2-6e44ff.svg)](mcp-server/pyproject.toml)

```
┌─────────────────────────────────────────────┐
│            your MCP host                    │
│  (Claude Code / Copilot / Cursor / …)       │
└──────────────────┬──────────────────────────┘
                   │ stdio  (tools/list, tools/call)
                   ▼
┌─────────────────────────────────────────────┐
│   atlas-aci  (this server)                  │
│   ┌──────────────────────────────────────┐  │
│   │ enforcement.py                       │  │  ← read-only allowlist,
│   │  • READ_ONLY_TOOLS frozenset         │  │    path-traversal guard,
│   │  • assert_path_in_repo               │  │    sliding-window rate
│   │  • assert_rate_limit                 │  │    limiter, bound helpers,
│   │  • cap_lines / cap_matches / …       │  │    telemetry sink
│   └───────────────┬──────────────────────┘  │
│                   │ every call funnels here │
│   ┌───────────────▼──────────────────────┐  │
│   │ 7 tools  (view_file, list_dir,       │  │
│   │  search_text, search_symbol,         │  │
│   │  graph_query, test_dry_run,          │  │
│   │  memex_read)                         │  │
│   └───┬─────────────────────────────┬────┘  │
│       │                             │       │
│   ┌───▼──────┐               ┌──────▼────┐  │
│   │ memex.py │               │codegraph  │  │
│   │ hashed-  │               │tree-sitter│  │
│   │ dir KV   │               │+ SQLite   │  │
│   └──────────┘               └───────────┘  │
└─────────────────────────────────────────────┘
```

---

## Contents

- [Status](#status)
- [The seven tools](#the-seven-tools)
- [Why read-only](#why-read-only)
- [Quick start](#quick-start)
- [Integrate with your codebase](#integrate-with-your-codebase)
- [Supported hosts](#supported-hosts)
- [Running in Docker](#running-in-docker)
- [Development](#development)
- [Repository layout](#repository-layout)
- [Security invariants](#security-invariants)
- [Production hardening](#production-hardening)
- [Further reading](#further-reading)
- [License](#license)

---

## Status

Reference implementation / MVP. Apache-2.0. Python ≥3.11, managed with
[`uv`](https://docs.astral.sh/uv/). Runs under the official `mcp`
Python SDK over stdio. Canary suite and host wiring docs target
Claude Code, GitHub Copilot custom agents, and Cursor.

> **Security boundary.** `test_dry_run` spawns a subprocess inside the
> repo. For untrusted models, the operator must sandbox this at the
> OS level — DevContainer, Firecracker microVM, or equivalent. The
> code alone cannot enforce it. See
> [`SETUP.md §8`](SETUP.md#8-production-hardening-checklist) for the
> full hardening checklist.

---

## The seven tools

All bounds below are **mechanical** — enforced in the dispatcher
before the tool sees the request, not as advisory hints to the model.
Defaults live in
[`mcp-server/src/atlas_aci/config.py`](mcp-server/src/atlas_aci/config.py)
and can be tightened per deployment.

| Tool | Purpose | Hard bound |
|------|---------|------------|
| `view_file` | Read a window of lines from a UTF-8 text file. Rejects binaries by extension and by `UnicodeDecodeError`. | ≤100 lines/call, ≤8 KiB/call, `next_cursor` for paging |
| `list_dir` | List a directory, respecting the skip-list (`node_modules`, `vendor/bundle`, `.git`, `.atlas`, …). | ≤200 entries/call, overflow flag if truncated |
| `search_text` | Ripgrep-backed regex search over a repo-relative scope or glob. Smart-case by default. | ≤50 matches/call, 15s wall-clock timeout |
| `search_symbol` | Index-backed symbol lookup. Returns definitions + references for a name (optionally filtered by `kind`). | Requires `atlas-aci index` first; refs capped at 200 |
| `graph_query` | Tiny DSL over the code graph: `callers_of:Sym`, `definitions_of:Name`, `subclasses_of:Class`. | Returns structured rows; rejects unknown verbs |
| `test_dry_run` | Run one test file (optionally filtered by case name) as a subprocess with captured stdout/stderr. | 30s wall-clock, ≤8 KiB stdout+stderr. **Operator must sandbox.** |
| `memex_read` | Byte-exact retrieval of a previously captured excerpt. Refs are returned by other tools when they cite source content. | Scoped to the hashed-dir backend |

Process-wide: **200 calls/minute** sliding-window rate limit. Every
call is recorded in telemetry (per-tool count, bytes out, overflow
flags, errors) — wire `Enforcement.records` to your observability
sink.

---

## Why read-only

`atlas-aci` implements the bounded ACI contract from the ATLAS spec.
The thesis is narrow: a code-exploration agent should not need
`edit_file`, `write_file`, `shell_exec`, or any mutating primitive to
do its job. Stripping those tools out of the interface is cheaper and
more reliable than relying on prompt discipline. If a tool cannot be
called, it cannot be misused.

Every call funnels through `enforcement.assert_read_only` before any
tool code runs. The allowlist is a `frozenset` literal in source — not
a config value — so "add a write tool" requires a code change, a
review, and a release. See
[`mcp-server/src/atlas_aci/enforcement.py`](mcp-server/src/atlas_aci/enforcement.py).

**Explicitly absent:** `edit_file`, `write_file`, `shell_exec`,
`git_commit`, `git_checkout`, `migration_apply`, `deploy_run`, and any
other mutating primitive. Attempts to call them return `FORBIDDEN`
from `assert_read_only` before any code runs. If your workflow needs
to apply edits, run migrations, or deploy code, use a different
server — don't hollow out this one.

---

## Quick start

The 30-second path. For the full walkthrough, read
[`SETUP.md`](SETUP.md) (install → smoke → canaries → hardening) and
[`INTEGRATION.md`](INTEGRATION.md) (pointing the server at your own
repo).

```bash
cd mcp-server
uv sync

# Build the code-graph index for your repo (one-time, ~30–120s for
# large Rails-scale codebases).
uv run atlas-aci index --repo /path/to/your/repo \
    --langs ruby,python,javascript,typescript

# Start the stdio server (this is what a host launches under the hood).
uv run atlas-aci serve --repo /path/to/your/repo
```

Inspect the tool manifest without booting a host:

```bash
uv run atlas-aci tools --repo /path/to/your/repo | jq 'map(.name)'
# ["view_file","list_dir","search_text","search_symbol",
#  "graph_query","test_dry_run","memex_read"]
```

Any output containing a name not in that list means the server is
non-conformant — file a bug.

---

## Integrate with your codebase

Once the server runs, [`INTEGRATION.md`](INTEGRATION.md) is the
onboarding doc for pointing it at a real project: indexing strategy,
language selection, skip-list tuning, keeping the index fresh
(post-commit hooks, CI), multi-repo setups, and the common pitfalls
that catch first-time operators.

> **TL;DR** — `atlas-aci index` writes to `<repo>/.atlas/`; add that
> to your `.gitignore`; re-index incrementally with `--since HEAD~10`;
> one server process per repo.

---

## Supported hosts

| Host | Wiring doc | Notes |
|------|-----------|-------|
| Claude Code | [`hosts/claude-code.md`](hosts/claude-code.md) | Best-ranked host. One JSON edit to `claude_desktop_config.json` per repo. |
| GitHub Copilot custom agents | [`hosts/copilot.md`](hosts/copilot.md) | MCP server goes in agent frontmatter. Skill descriptions must match ATLAS phases (T, L, A, S) for the selector to fire correctly. |
| Cursor | [`hosts/cursor.md`](hosts/cursor.md) | MCP config via Settings UI or `~/.cursor/mcp.json`. Cursor's agent loop is more eager — keep ATLAS invocations in Plan mode unless you want edits attempted alongside exploration. |

---

## Running in Docker

Two invocations, documented in detail in the
[`mcp-server/Dockerfile`](mcp-server/Dockerfile) header.

**Index once** (writable repo mount):

```bash
docker run --rm \
    -v /path/to/repo:/repo \
    atlas-aci index --repo /repo --langs ruby,python,javascript,typescript
```

**Serve long-lived** (read-only repo mount, persistent memex):

```bash
docker run --rm -i --read-only \
    -v /path/to/repo:/repo:ro \
    -v atlas-memex:/memex \
    atlas-aci
```

The `--read-only` flag plus the `:ro` bind mount is the OS-level
guarantee that the server cannot mutate your repo — stronger than any
code-level check. The runtime image runs as an unprivileged
`atlas:10001` user with `ripgrep` and `git` preinstalled. See
[`INTEGRATION.md`](INTEGRATION.md#running-in-docker) for the full
index-then-serve pattern.

---

## Development

Container-based dev is preferred: it keeps the host clean and matches
how the server is deployed. A dedicated image
([`mcp-server/Dockerfile.dev`](mcp-server/Dockerfile.dev)) ships
`pytest`, `ruff`, and `mypy` pre-installed:

```bash
cd mcp-server
docker build -f Dockerfile.dev -t atlas-aci-dev .

# Interactive dev shell
docker run --rm -it -v "$PWD":/work atlas-aci-dev

# Or run one-shot commands without entering the shell
docker run --rm -v "$PWD":/work atlas-aci-dev uv run pytest -q
docker run --rm -v "$PWD":/work atlas-aci-dev uv run ruff check src/ tests/
docker run --rm -v "$PWD":/work atlas-aci-dev uv run mypy src/
```

The bind-mounted source round-trips edits without rebuilds; the
`.venv` lives outside `/work` so it survives across runs.

If you need to run on the host instead:

```bash
cd mcp-server
uv sync                                    # install deps
uv run pytest                              # test suite
uv run ruff check src/ tests/              # lint
uv run ruff format --check src/ tests/     # format check
uv run mypy src/                           # type check
uv run atlas-aci --help                    # CLI smoke
```

Pytest runs with `asyncio_mode = "auto"`
(`mcp-server/pyproject.toml`), so test functions can be plain
`async def` without per-test markers.

### Running the canary suite

From the repo root:

```bash
uv run python scripts/run-canaries.py \
    --repo /path/to/your/repo \
    --canaries atlas/evals/canary-missions.md \
    --output /tmp/canary-results.json
```

First-run pass rate of 50–60% is normal; iterate on your skill files
until ≥80% before promoting to production. See
[`SETUP.md §7`](SETUP.md#7-run-the-canary-suite).

---

## Repository layout

```
atlas-aci/
├── README.md                          ← you are here (orientation)
├── SETUP.md                           ← end-to-end playbook
├── INTEGRATION.md                     ← codebase onboarding workflow
├── CLAUDE.md                          ← in-repo Claude Code guidance
│
├── mcp-server/                        ← the Python MCP server
│   ├── pyproject.toml
│   ├── Dockerfile                     ← production runtime image
│   ├── Dockerfile.dev                 ← dev-loop image (pytest/ruff/mypy)
│   ├── .dockerignore
│   ├── README.md                      ← package-scoped quick start
│   ├── src/atlas_aci/
│   │   ├── __main__.py                ← Click CLI: serve | index | tools
│   │   ├── server.py                  ← MCP stdio wiring + dispatcher
│   │   ├── enforcement.py             ← read-only guard, bounds, rate limit, telemetry
│   │   ├── config.py                  ← Config dataclass + skip patterns
│   │   ├── codegraph.py               ← tree-sitter indexer + SQLite queries
│   │   ├── memex.py                   ← hashed-dir excerpt store
│   │   └── tools/
│   │       ├── view_file.py
│   │       ├── list_dir.py
│   │       ├── search_text.py         ← ripgrep wrapper
│   │       ├── search_symbol.py
│   │       ├── graph_query.py
│   │       └── test_dry_run.py
│   └── tests/
│       └── test_enforcement.py        ← safety invariants
│
├── hosts/
│   ├── claude-code.md
│   ├── copilot.md
│   └── cursor.md
│
└── scripts/
    └── run-canaries.py                ← canary mission orchestrator
```

---

## Security invariants

These are the properties the enforcement layer guarantees. If any of
them fail under test, the server is non-conformant — every invariant
has a corresponding test in
[`mcp-server/tests/test_enforcement.py`](mcp-server/tests/test_enforcement.py).

1. **Read-only allowlist.** Any tool name not in
   `enforcement.READ_ONLY_TOOLS` is rejected with `FORBIDDEN` before
   any tool code runs. The set is a source-level `frozenset` literal.
2. **Path-traversal guard.** Every file path is resolved (including
   symlinks) and checked against the configured repo root before any
   filesystem access. Paths that resolve outside the repo are rejected
   with `FORBIDDEN`.
3. **Mechanical bounds.** Line / entry / match / byte caps and the
   sliding-window rate limiter are applied per call in
   `enforcement.py`. Tools can only narrow bounds, never widen them.
4. **Structured errors.** All errors are `ToolError(code, message,
   retry_hint)`. The retry hint is one of `none`, `narrower_scope`,
   `different_tool` — intended to be acted on by the model without
   humans in the loop.
5. **Telemetry of every call.** Tool name, arg shape (with the
   `content` field redacted), bytes out, duration, overflow flag, and
   error code are recorded for every call. Wire
   `Enforcement.records` to your observability sink at process start.

---

## Production hardening

Before letting `atlas-aci` run unattended on shared infrastructure,
work through the full checklist in
[`SETUP.md §8`](SETUP.md#8-production-hardening-checklist). The two
items most commonly skipped:

- **Sandbox `test_dry_run`.** The code cannot enforce this; the
  operator must put the server behind a DevContainer, Firecracker
  microVM, or equivalent before enabling the tool for untrusted
  models.
- **Mount the repo read-only at the OS level.** A read-only bind
  mount is a stronger guarantee than any code-level check and
  survives mis-configuration of the server itself.

---

## Further reading

- [`SETUP.md`](SETUP.md) — end-to-end playbook: install → host
  wiring → canaries → hardening → operating notes → anti-patterns.
- [`INTEGRATION.md`](INTEGRATION.md) — onboarding an existing
  codebase: indexing, skip tuning, freshness, multi-repo.
- [`mcp-server/README.md`](mcp-server/README.md) — package-scoped
  quick start, closer to the code.
- [`CLAUDE.md`](CLAUDE.md) — how Claude Code should navigate this
  repo when you open it in an agent loop.
- [`hosts/claude-code.md`](hosts/claude-code.md),
  [`hosts/copilot.md`](hosts/copilot.md),
  [`hosts/cursor.md`](hosts/cursor.md) — per-editor wiring.

---

## License

Apache-2.0. See [`mcp-server/pyproject.toml`](mcp-server/pyproject.toml)
for the declaration. A top-level `LICENSE` file will land with the
first published release.
