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
- [Container image](#container-image)
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
| `search_symbol` | Index-backed symbol lookup. Returns definitions + references for a name (optionally filtered by `kind`). | Requires `atlas-aci index` first; central element cap + byte ceiling (truncated + flagged) |
| `graph_query` | Tiny DSL over the code graph: `callers_of:Sym`, `definitions_of:Name`, `subclasses_of:Class`. | Rejects unknown verbs; central element cap + byte ceiling (truncated + flagged) |
| `test_dry_run` | Run one test file (optionally filtered by case name) as a subprocess with captured stdout/stderr. | 30s wall-clock, ≤8 KiB stdout+stderr. **Operator must sandbox.** |
| `memex_read` | Byte-exact retrieval of a previously captured excerpt via its `memex://excerpt/<sha256>` ref, minted by `Memex.write()`. No ATLAS tool currently emits one. | Scoped to the hashed-dir backend |

Process-wide: **200 calls/minute** sliding-window rate limit. Every
call is recorded in telemetry (per-tool count, bytes out, overflow
flags, errors) — wire `Enforcement.records` to your observability
sink.

### `graph_query` DSL — edge shape (v2.0.0 / A1)

`callers_of:Sym` and `subclasses_of:Class` both query the materialized
call/inheritance edge table `atlas-aci index` builds (`definitions_of:Name`
is unchanged — it delegates straight to `search_symbol`). Each element of
the returned `edges` list has this shape:

```jsonc
{
  "relation": "call",          // call | construct | superclass | include | extend | prepend
  "confidence": "EXTRACTED",   // EXTRACTED | INFERRED | AMBIGUOUS — never LLM-produced
  "source": {                  // caller context — replaces the old, always-null
    "path": "app/tallier.rb",  // `enclosing` field (v1). None/None when the
    "line": 3,                 // reference sits outside every known symbol's
    "name": "call",            // range (e.g. a Ruby top-level call).
    "kind": "method"
  },
  "target": {                  // populated for EXTRACTED/INFERRED only —
    "path": "app/target.rb",   // the single resolved definition.
    "line": 2,
    "name": "record_vote"
  },
  "candidates": null           // populated (never truncated silently — see
                                // below) for AMBIGUOUS edges only; mutually
                                // exclusive with `target`.
}
```
`callers_of`/`subclasses_of` responses also carry a top-level
`unresolved_refs` count: the number of raw references matching the queried
name that exist in the index but resolved to **no** edge (typically an
external/gem method with no local definition). An empty `edges: []` alone
cannot tell a consumer "nothing calls this" apart from "calls exist but
didn't resolve" — `unresolved_refs` makes that distinction explicit instead
of leaving both cases looking identical.

A **zero-candidate** reference (the callee resolves to no known definition
anywhere in the index) never becomes an edge at all — it stays an
unresolved name, exactly as `refs` recorded it pre-v2 (and is counted in
`unresolved_refs` above). `subclasses_of` aggregates every inheritance/mixin
relation (`superclass`, `include`, `extend`, `prepend`) under the one verb,
since a Rails engine leaning on `concerns/` mixins expresses "subclass-of"
through all four relations, not just `superclass`.

`relation: "construct"` is a bare `Foo(...)` / `new Foo()` (JS/TS) /
`Foo.new` (Ruby) call that resolves entirely to a class/module symbol — a
constructor invocation, not a method call, so it is never silently folded
into `relation: "call"`. `callers_of:SomeClass` returns these: the caller
doesn't know in advance whether a queried name is a callable or a class, so
`callers_of` searches both relations for exactly that reason — a symbol's
*kind* is never a reason a query silently comes back empty.

**EXTRACTED vs INFERRED, and a known, guarded limitation.** Ruby's grammar
distinguishes a constant receiver (`Foo.bar`) from a plain identifier
(`obj.bar`, `self.bar`) structurally — a real syntactic fact, no lookup
needed. Python/JS/TS grammars don't make that distinction, so those two
languages resolve `qualifier_name` (the receiver, or the bare callee itself)
against the symbol table: does it name a known `class`/`module`? A local
variable that happens to share a class's name — `Config = load_config()`
then `Config.reload()` — would otherwise be indistinguishable from the
class itself under a name-only check with no scope analysis. The resolver
guards against exactly this: if `qualifier_name` is ALSO assigned to as a
plain local variable anywhere in the same file, the edge is demoted to
`INFERRED` rather than asserting `EXTRACTED` certainty it doesn't have — a
false `EXTRACTED` is worse than an honest `INFERRED`. This guard is
deliberately narrow (a plain `identifier` assignment target only — tuple
unpacking, attribute assignment, and augmented assignment aren't tracked),
so it can under-claim in rare cases the reverse way, but it never
over-claims.

**The analysis-graph divergence (D4a) — spec'd now, not yet shipped.**
`graph_query` always returns every *matching* edge, AMBIGUOUS included,
with its full ordered `candidates[]` attached — this project's "never
silently incomplete" thesis extends to ambiguity itself: an edge with more
than one candidate is reported, not dropped. A1 also ships
`CodeGraph.confident_edges()`, a query primitive over the **confident
subgraph** (`EXTRACTED` ∪ `INFERRED`) that excludes AMBIGUOUS entirely (no
fan-out to candidates, no fractional weight — ambiguity is not importance).
Degree-centrality god nodes and community detection (A2/A3, **not part of
this release** — tracked separately) are specified to consume exactly that
primitive as their analysis input, never the raw `graph_query` edge set.
When those verbs ship, "what `graph_query` returns" and "what god-nodes/
communities analyze" will deliberately differ, and their responses will
carry `analysis_basis`, `ambiguous_edges_excluded`, and `resolved_edge_count`
fields making that divergence visible rather than implicit.

`candidates[]` (and every edge enumeration) is emitted in a fixed total
order (`path`, `line`, `name`) for identical input — required for the
project's byte-deterministic export goal, and incidentally what makes the
shape safe to diff/test. Like every other bounded field, an over-cap
`candidates[]` is truncated on a whole-element boundary and flagged
(`truncated: true`, `truncated_fields: ["edges.candidates"]`,
`more_available: true`) rather than silently cut — nested sub-fields get
the same "never silently incomplete" treatment the top-level `edges` list
already had.

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
> to your `.gitignore`; re-index cheaply with `--since <marker>` (skips
> files whose `(mtime, size)` are unchanged since the last pass — it does
> **not** diff a git ref; see
> [`INTEGRATION.md` §Keep the index fresh](INTEGRATION.md#step-6--keep-the-index-fresh));
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

## Container image

`ghcr.io/rynaro/atlas-aci` is the canonical distribution channel.
Images are built on every `v*` tag push via the [release workflow](.github/workflows/release.yml)
and support **linux/amd64 and linux/arm64** (Apple Silicon and Linux
servers are both first-class targets).

### Pull commands

```bash
# Latest stable release
docker pull ghcr.io/rynaro/atlas-aci:latest

# Specific version
docker pull ghcr.io/rynaro/atlas-aci:0.2.0

# Immutable digest pin (recommended for production)
docker pull ghcr.io/rynaro/atlas-aci@sha256:<digest>
```

Digest-pinned pulls are the most reliable — a tag can be overwritten,
but a digest never changes. The release notes for each tag include the
exact `ghcr.io/rynaro/atlas-aci@sha256:<digest>` string ready to copy.

### Supply-chain attestations

Every release is signed and attested:

- **Cosign keyless signature** — signed with the GitHub Actions OIDC
  token; no long-lived keys required.
- **SBOM (SPDX)** — attached as a ghcr.io attestation via
  `actions/attest-sbom`.
- **Build provenance** — attached via `actions/attest-build-provenance`.

### Verification

Every published GA image (tagged `v<x>.<y>.<z>` without a pre-release suffix)
has been scanned by Trivy before signing and has no known HIGH or CRITICAL CVEs
at release time — the workflow fails and does not proceed to cosign, SBOM
attestation, or GitHub Release creation if any are found. Pre-release images
(`-rc.*`, `-beta.*`) are exempt from the fail-on-vuln gate to allow
security-bug-fix testing, but the Trivy SARIF report is still uploaded and
visible in the repository's **Security** tab. Findings that are discovered
post-release also surface there; users who need a known-good image can pin an
earlier digest via `--image-digest` while a patch release is being prepared.

**Cosign keyless verify:**

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/Rynaro/atlas-aci/.github/workflows/release.yml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/rynaro/atlas-aci@sha256:<digest>
```

**SBOM and provenance via `gh attestation`:**

```bash
gh attestation verify oci://ghcr.io/rynaro/atlas-aci@sha256:<digest> \
  --owner Rynaro
```

### Consumer integration via Eidolons

If you use the [Eidolons nexus](https://github.com/Rynaro/eidolons),
`eidolons mcp atlas-aci` pulls from `ghcr.io/rynaro/atlas-aci` by
default and wires the digest-pinned image into your project's
`.mcp.json`. For air-gapped environments or when the registry is
unreachable, pass `--build-locally`:

```bash
eidolons mcp atlas-aci pull --build-locally
```

See [`INTEGRATION.md §GHCR distribution`](INTEGRATION.md#ghcr-distribution)
for the full consumer workflow.

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

**Status: the host dispatcher is not implemented yet.** `--host stub` is the
only wired option; `StubDispatcher.dispatch()` raises `NotImplementedError`,
and any other `--host` value raises the same in `main()`. There is no
canary pass-rate to quote until a real dispatcher (Claude Code API, Copilot
Action, Cursor headless) is implemented — see the deferred note in
[`scripts/run-canaries.py`](scripts/run-canaries.py). Wiring one in is
tracked as follow-up work; see [`SETUP.md §7`](SETUP.md#7-run-the-canary-suite).

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
│       ├── test_enforcement.py        ← safety invariants
│       ├── test_codegraph.py          ← indexer + language-table honesty
│       ├── test_server.py             ← central bounds chokepoint (D2)
│       └── test_schema_epoch.py       ← epoch-namespaced DB substrate (D1)
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
   `enforcement.py`, plus a central dispatch-layer chokepoint
   (`server.py`'s `apply_central_bounds`) that element-caps every tool's
   declared list-valued field and enforces an absolute byte ceiling —
   the floor every tool (including `search_symbol` and `graph_query`)
   passes through regardless of its own caps. Tools can only narrow
   bounds, never widen them.
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

Apache-2.0. See the top-level [`LICENSE`](LICENSE) file for the full text;
[`mcp-server/pyproject.toml`](mcp-server/pyproject.toml) declares the same
identifier for the packaged distribution.
