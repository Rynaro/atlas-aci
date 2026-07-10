# atlas-aci

Reference MCP server and setup bundle for the **ATLAS bounded
Agent-Computer Interface**.

<!--
  The release badge below is DYNAMIC (shields.io github/v/release) — it
  tracks this repo's latest GitHub release automatically. Don't hardcode
  a version number here; it updates itself the moment a tag is cut.
-->
[![CI](https://github.com/Rynaro/atlas-aci/actions/workflows/ci.yml/badge.svg)](https://github.com/Rynaro/atlas-aci/actions/workflows/ci.yml)
<img src="https://img.shields.io/github/v/release/Rynaro/atlas-aci?sort=semver&label=release&color=blue" alt="release">
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](mcp-server/pyproject.toml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)](mcp-server/pyproject.toml)
[![MCP SDK](https://img.shields.io/badge/mcp-%E2%89%A51.2-6e44ff.svg)](mcp-server/pyproject.toml)

<p>
<a href="#try-it-in-60-seconds">Try it</a> ·
<a href="#the-seven-tools">The tools</a> ·
<a href="#see-it-query">See it query</a> ·
<a href="#how-its-bounded">How it's bounded</a> ·
<a href="#does-it-hold-up">Does it hold up?</a> ·
<a href="#install">Install</a> ·
<a href="#when-atlas-aci-is-the-wrong-tool">When not to use it</a>
</p>

---

Most code-navigation MCP servers hand the agent read *and* write tools —
`edit_file`, `shell_exec`, sometimes `git_commit` — and hope prompt
discipline holds. atlas-aci makes a narrower bet: a code-exploration
agent doesn't need to mutate anything to do its job, so the mutating
tools simply don't exist in the interface. Not by convention, not by
system-prompt instruction — the allowlist is a `frozenset` literal in
source, checked before any tool code runs. **The agent is the untrusted
party**, and as of v2.0.0 every doc claim below is mechanically checked
too, not merely asserted. The numbers — and their honest catches — are
[below](#does-it-hold-up).

## Try it in 60 seconds

Evaluation, not commitment — no host, no API key, nothing written outside
`.atlas/`:

```bash
git clone https://github.com/Rynaro/atlas-aci && cd atlas-aci/mcp-server
uv sync

uv run atlas-aci index --repo /path/to/any/repo
uv run atlas-aci tools --repo /path/to/any/repo | jq 'map(.name)'
# ["view_file","list_dir","search_text","search_symbol",
#  "graph_query","test_dry_run","memex_read"]
```

Any output containing a name not in that list means the server is
non-conformant — file a bug. Full walkthrough (host wiring, canaries,
hardening): [`SETUP.md`](SETUP.md). Onboarding an existing codebase:
[`INTEGRATION.md`](INTEGRATION.md).

## The seven tools

All bounds below are **mechanical** — enforced in the dispatcher before
the tool sees the request, not advisory hints to the model. Defaults
live in
[`mcp-server/src/atlas_aci/config.py`](mcp-server/src/atlas_aci/config.py)
and can be tightened per deployment.

| Tool | Purpose | Hard bound |
|------|---------|------------|
| `view_file` | Read a window of lines from a UTF-8 text file. Rejects binaries by extension and by `UnicodeDecodeError`. | ≤100 lines/call, ≤8 KiB/call, `next_cursor` for paging |
| `list_dir` | List a directory, respecting the skip-list (`node_modules`, `vendor/bundle`, `.git`, `.atlas`, …). | ≤200 entries/call, overflow flag if truncated |
| `search_text` | Ripgrep-backed regex search over a repo-relative scope or glob. Smart-case by default. | ≤50 matches/call, 15s wall-clock timeout |
| `search_symbol` | Index-backed symbol lookup. Returns definitions + references for a name (optionally filtered by `kind`). | Requires `atlas-aci index` first; central element cap + byte ceiling (truncated + flagged) |
| `graph_query` | Tiny DSL over the code graph: `callers_of:Sym`, `definitions_of:Name`, `subclasses_of:Class`, `god_nodes:`, `communities:`, `rationale:`. | Rejects unknown verbs; central element cap + byte ceiling (truncated + flagged) |
| `test_dry_run` | Run one test file (optionally filtered by case name) as a subprocess with captured stdout/stderr. | 30s wall-clock, ≤8 KiB stdout+stderr. **Operator must sandbox.** |
| `memex_read` | Byte-exact retrieval of a previously captured excerpt via its `memex://excerpt/<sha256>` ref, minted by `Memex.write()`. No ATLAS tool currently emits one. | Scoped to the hashed-dir backend |

Process-wide: **200 calls/minute** sliding-window rate limit. Every call
is recorded in telemetry (per-tool count, bytes out, overflow flags,
errors) — wire `Enforcement.records` to your observability sink.

### `graph_query`'s DSL, in six lines

`callers_of`/`subclasses_of` return materialized edges carrying a
`confidence` enum (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`, never
LLM-produced) plus a top-level `unresolved_refs` count. `god_nodes:` and
`communities:` both run over the **confident subgraph** only
(`EXTRACTED` ∪ `INFERRED` — AMBIGUOUS is structurally excluded, not
filtered) and both responses carry `analysis_basis`,
`ambiguous_edges_excluded`, and `resolved_edge_count` so that exclusion
is visible, not implicit. `rationale:` surfaces every recognized
`# NOTE:`/`# TODO:`/`# HACK:`/ADR-RFC-referencing comment as a first-class
node with its own `rationale_for` edge, structurally kept out of the
call/inheritance graph. **Confidence tiers are conservative by
construction:** the `EXTRACTED` shadowing guard is file-scoped, not
scope-scoped, so it can under-claim (never over-claim) — treat
`EXTRACTED` as a floor on what's captured, not an exact count. The full
edge-shape reference, the `god_nodes`/`communities` response shapes, the
D4a analysis-graph-vs-query-graph divergence, and the `rationale:` node
shape live in [`docs/graph-query-dsl.md`](docs/graph-query-dsl.md).

## See it query

`graph_query` is a real local program, not a prompt — ask it something
and it answers in JSON:

```console
$ uv run atlas-aci index --repo .
$ uv run python -c "
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import Enforcement
import asyncio, json
from atlas_aci.tools.graph_query import graph_query
config = Config(repo=Path('.'), memex_root=Path('/tmp/memex'))
cg, enf = CodeGraph(Path('.')), Enforcement(config)
print(json.dumps(asyncio.run(graph_query({'query': 'callers_of:assert_read_only'}, config, enf, cg)), indent=2))
"
{
  "edges": [
    {
      "relation": "call",
      "confidence": "INFERRED",
      "source": {
        "path": "mcp-server/src/atlas_aci/server.py",
        "line": 379,
        "name": "dispatch_tool_call",
        "kind": "function"
      },
      "target": {
        "path": "mcp-server/src/atlas_aci/enforcement.py",
        "line": 81,
        "name": "assert_read_only"
      },
      "candidates": null
    }
    // ... 2 more edges (both from mcp-server/tests/test_enforcement.py)
  ],
  "unresolved_refs": 0
}
```

```console
$ # god_nodes: — degree centrality over the confident subgraph only
{
  "god_nodes": [
    {"path": "mcp-server/src/atlas_aci/codegraph.py", "line": 753,
     "name": "CodeGraph", "kind": "class",
     "in_degree": 128, "out_degree": 0, "degree": 128}
    // ... ranked list continues
  ],
  "analysis_basis": "confident_edges",
  "ambiguous_edges_excluded": 326,
  "resolved_edge_count": 684
}
```

<sup>Output captured against atlas-aci's own index and lightly trimmed
for length; not lightly trimmed for content. Both calls run entirely
local, make **zero LLM calls, no API key**, and reproduce
byte-identically for an identical index — this is deterministic
program output, not a generated sample.</sup>

## How it's bounded

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

These are the properties the enforcement layer guarantees. If any of
them fail under test, the server is non-conformant — every invariant has
a corresponding test in
[`mcp-server/tests/test_enforcement.py`](mcp-server/tests/test_enforcement.py).

1. **Read-only allowlist.** Any tool name not in
   `enforcement.READ_ONLY_TOOLS` is rejected with `FORBIDDEN` before any
   tool code runs. The set is a source-level `frozenset` literal.
2. **Path-traversal guard.** Every file path is resolved (including
   symlinks) and checked against the configured repo root before any
   filesystem access. Paths that resolve outside the repo are rejected
   with `FORBIDDEN`.
3. **Mechanical bounds.** Line / entry / match / byte caps and the
   sliding-window rate limiter are applied per call in `enforcement.py`,
   plus a central dispatch-layer chokepoint (`server.py`'s
   `apply_central_bounds`) that element-caps every tool's declared
   list-valued field and enforces an absolute byte ceiling — the floor
   every tool (including `search_symbol` and `graph_query`) passes
   through regardless of its own caps. Tools can only narrow bounds,
   never widen them.
4. **Structured errors.** All errors are `ToolError(code, message,
   retry_hint)`. The retry hint is one of `none`, `narrower_scope`,
   `different_tool` — intended to be acted on by the model without
   humans in the loop.
5. **Telemetry of every call.** Tool name, arg shape (with the `content`
   field redacted), bytes out, duration, overflow flag, and error code
   are recorded for every call. Wire `Enforcement.records` to your
   observability sink at process start.

**Explicitly absent:** `edit_file`, `write_file`, `shell_exec`,
`git_commit`, `git_checkout`, `migration_apply`, `deploy_run`, and any
other mutating primitive. Attempts to call them return `FORBIDDEN` from
`assert_read_only` before any code runs. If your workflow needs to apply
edits, run migrations, or deploy code, **use a different server** — don't
hollow out this one.

> **Security boundary.** `test_dry_run` spawns a subprocess inside the
> repo. For untrusted models, the operator must sandbox this at the OS
> level — DevContainer, Firecracker microVM, or equivalent. The code
> alone cannot enforce it. See
> [`SETUP.md §8`](SETUP.md#8-production-hardening-checklist).

### `export` / `import` — CLI-only, by design

v2.0.0 adds a deterministic, portable JSONL export/import of the code
graph — the point being "one person builds the graph, everyone else
benefits immediately on `git pull`" instead of everyone re-parsing the
same repo. Both are **CLI commands only** (`atlas-aci export`/`atlas-aci
import`) — neither is registered as an MCP tool, and neither ever will be
without a deliberate, separately-reviewed decision to widen the
read-only thesis above. **`import` is a mutating primitive, full stop**:
it replaces the entire index DB from the bytes of a file on disk. Since
**the agent is the untrusted party**, an agent-callable `import` would
hand it a way to silently replace every future `search_symbol`/
`graph_query` result with attacker-chosen content. `export` is a closer
call — it never writes to the index, only computes content already
exposed by the seven read tools — but keeping it **CLI-only** preserves
a clean, mechanically-checkable invariant (every tool in
`READ_ONLY_TOOLS` performs zero filesystem writes, full stop) over
carving out a single well-reasoned exception.

<details>
<summary>Cold-start workflow, the full import/export threat-model argument, and named cross-OS hazards</summary>

**Cold-start workflow** (the reason this exists):

```bash
atlas-aci export --repo /path/to/your/repo .atlas/export/graph-export.jsonl
git add .atlas/export/graph-export.jsonl && git commit

# ...on a fresh checkout:
atlas-aci import --repo /path/to/checkout .atlas/export/graph-export.jsonl
```

`.atlas/*` is gitignored; `.atlas/export/` is explicitly not — derived
data stays out, the portable artefact goes in. `import` is idempotent
(repeat imports of the same file reproduce the identical DB), rejects a
truncated/hand-edited/wrong-epoch file with a clean error, and validates
every path in the file is repository-relative and repository-contained
before inserting a single row — `content_hash` proves bytes weren't
truncated, never that the paths inside them are safe, so that check is
separate and mandatory.

**Why `import` is never a tool, argued through, not just asserted.** The
export format's `content_hash` check only proves a file wasn't truncated
in transit; it proves nothing about *provenance* — a hand-crafted JSONL
with a self-consistent, freshly-computed hash passes the exact same
check a genuine `atlas-aci export` output does. There is no way to
distinguish "a real export" from "a plausible forgery" from inside
`import_jsonl` itself. That is precisely why this is an operator-invoked,
human-in-the-loop CLI command, never a tool a served agent can reach.

**Why `export` is a closer call.** It opens the index DB read-only and
writes a NEW file at whatever path the caller chooses — nothing it
produces is new information an agent couldn't already reconstruct by
paging through `graph_query` results. The case against a tool wins
anyway: an `export` tool would be the first exception to "every
`READ_ONLY_TOOLS` member performs zero filesystem writes," a property
otherwise enforceable by inspection alone, and would need its own
path-scoping argument forever after. The `write_file`-shaped capability —
"let the model choose a path and put bytes there" — is exactly the
primitive this project's whole thesis excludes, regardless of how
well-scoped the bytes are.

**Known, named cross-OS hazards (not mechanically closed).**
Byte-determinism is verified on the macOS/Linux CI matrix for this
project's own source and the two pinned reference repos, but: (0) a
Windows backslash-separated relative path is outside that CI matrix and
a real, separate hazard this export does not close; (1) unicode filename
normalization — macOS traditionally presents decomposed (NFD) filenames,
Linux presents whatever bytes were written (typically composed, NFC) —
could change an exported path's bytes for a non-ASCII filename across
operating systems (mitigated in practice by git's default
`core.precomposeUnicode` on macOS; neither pinned reference repo has
non-ASCII filenames — this is **documented, not mechanically fixed**);
(2) case-only-colliding filenames (`Foo.rb` / `foo.rb`) are two distinct
files on a case-sensitive filesystem but alias to one on a
case-insensitive macOS volume — a filesystem-level data difference
before export ever runs, not something any export format can paper over.

</details>

**Export size ceiling.** GitHub rejects any single committed file at or
above 100 MB outright. Measured once, for real, on the larger of this
project's two pinned Rails-scale reference repos: Spree @ `6699cde4`
exports to **88,742,743 bytes — 84.63% of that ceiling.** A Rails
application moderately larger than Spree produces an export that cannot
be committed at all. `atlas-aci export` warns to stderr (never fails the
export) past the 50 MB soft threshold and again past the hard limit — see
`_GITHUB_FILE_WARN_BYTES`/`_GITHUB_FILE_HARD_LIMIT_BYTES` in
[`mcp-server/src/atlas_aci/__main__.py`](mcp-server/src/atlas_aci/__main__.py).
If your export crosses the ceiling: compress it out-of-band (`gzip -9`,
decompress before `import`), publish it as a CI artifact instead of
`git add`-ing it, or re-index instead of importing for a one-off —
`atlas-aci index` itself never has this ceiling, only the portable,
committed artefact does.

## Does it hold up?

Verify the cheap part first — no key, no bill. `atlas-aci index` then
any `graph_query` runs entirely local, makes **zero LLM calls**, and
returns a **byte-deterministic** result for an identical index. Two
offline checks prove it, and you can run both yourself:

```bash
grep -rn 'import anthropic\|import openai' mcp-server/src   # zero matches
atlas-aci export --repo . /tmp/a.jsonl && atlas-aci export --repo . /tmp/b.jsonl
diff /tmp/a.jsonl /tmp/b.jsonl                               # byte-identical
```

That's the floor. Above it sit measurements that took real work to
produce, each with its data committed in-repo:

| # | Headline result | The catch |
|---|---|---|
| 1 | **201 tests**, up from 35 at v0.4.0; **zero** new runtime dependencies; `uv.lock`'s `dependencies` block is byte-identical to v0.4.0 throughout. | A green suite proves the invariants hold on this repo's own fixtures — not that the graph is complete on *your* code. |
| 2 | **Communities ship zero-dependency and measured.** A hand-rolled label-propagation implementation vs. networkx Louvain, on two SHA-pinned Rails repos, evaluated independently, never averaged, against a bar frozen **before a line of LPA existed** (`LPA_Q >= 0.85 × Louvain_Q_median`, both scores ≥ 0.30): solidus `LPA_Q 0.6691476` vs. median `0.7449783` (margin `+0.035916`); spree `0.7165340` vs. `0.7846478` (margin `+0.049584`). | At the tighter `R = 0.90` counterfactual, solidus falls short by `0.001333` — smaller than Louvain's own seed-to-seed population sd of `0.0025925`. Spree still *passes* at `R = 0.90` (margin `+0.010351`). The rule is a strict AND across both repos, so solidus's shortfall alone would still have cut the feature — this is not evidence both repos would have failed. |
| 3 | **`rationale:` on real code:** 33 rows found in solidus (24 `TODO`, 7 `NOTE`, 2 `FIXME`); markers, text, and line numbers accurate on spot-check. | **Zero rows in atlas-aci itself** — this codebase doesn't write `# TODO:`/`# NOTE:`/`# FIXME:`-shaped comments, so there's nothing for the same query to find here. |
| 4 | **Cross-platform byte-determinism** for the portable export, green on `ubuntu-latest` *and* `macos-latest`. | The export tops out near Spree scale — **88,742,743 bytes, 84.63%** of GitHub's 100 MB hard limit. A larger Rails app cannot commit its graph (see [How it's bounded](#how-its-bounded)). |
| 5 | **Twenty defects found and fixed**, all one shape: a check validating data whose provenance went unchecked. | Found by this project's own adversarial pass, pre-release; it names a defect *class*, it does not prove zero remain. |

<details>
<summary><strong>1 · The LPA-vs-Louvain probe</strong> — ten Louvain seeds per repo, the three-clause arithmetic, the frozen-before-probe bar</summary>

Ten independent Louvain runs per repo (seeds 0–9, resolution 1.0),
median/best/worst/sd all recomputed in pure Python from a committed,
sha256-verified graph bundle — never read as a cached label. Full
per-seed tables and the exact clause arithmetic:
[`probe-lpa-vs-louvain.md`](https://github.com/Rynaro/atlas-aci/blob/v2.0.0/.spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.md).

</details>

<details>
<summary><strong>2 · `rationale:` on real code, and the honest zero-in-self finding</strong></summary>

Indexed `solidusio/solidus@4026945` with the shipped extraction code: 33
recognized rationale comments, all plain `#`-prefixed Ruby comments (no
ADR/RFC labels in that corpus), 27 with a resolved enclosing symbol, 6
`null` (top-of-file). Spot-checked against the source for accuracy.
Verdict detail:
[`export-size-spree.md`](https://github.com/Rynaro/atlas-aci/blob/v2.0.0/.spectra/changes/aci-v2-harden-and-augment/export-size-spree.md)
(same index run backs both the rationale count and the export-size
measurement below).

</details>

<details>
<summary><strong>3 · Twenty defects, one shape</strong> — the retrospective and the design record</summary>

Every defect this campaign found was a check that validated the data it
was handed while the data's own provenance or completeness went
unchecked — a forged verdict label, a bar read from the artefact it
audited, an unasserted repo/seed set, an under-reported baseline. Full
account:
[`RETRO.md`](https://github.com/Rynaro/atlas-aci/blob/v2.0.0/.spectra/changes/aci-v2-harden-and-augment/RETRO.md)
and
[`ADR-001-checks-vs-proxies.md`](https://github.com/Rynaro/atlas-aci/blob/v2.0.0/.spectra/changes/aci-v2-harden-and-augment/ADR-001-checks-vs-proxies.md).

</details>

<details>
<summary><strong>4 · Cross-OS determinism and the export ceiling</strong></summary>

Measured once, for real, on Spree @ `6699cde4` (2,181 Ruby files):
export size and the three named, disclosed-not-fixed cross-OS hazards
(Windows path separators, NFC/NFD unicode filename normalization,
case-only-colliding filenames) are detailed in
[How it's bounded](#how-its-bounded) above and in
[`export-size-spree.md`](https://github.com/Rynaro/atlas-aci/blob/v2.0.0/.spectra/changes/aci-v2-harden-and-augment/export-size-spree.md).

</details>

## Install

Global, one-time:

```bash
cd mcp-server
uv sync
```

Per repo:

```bash
uv run atlas-aci index --repo /path/to/your/repo \
    --langs ruby,python,javascript,typescript

uv run atlas-aci serve --repo /path/to/your/repo
```

Already have a teammate's index? Skip the parse entirely — see the
cold-start workflow in [How it's bounded](#how-its-bounded). `export`/
`import` are CLI-only, operator-invoked commands, never MCP tools a
served agent can call.

> **TL;DR on staying fresh** — `atlas-aci index` writes to `<repo>/.atlas/`;
> add that to your `.gitignore`. Re-index cheaply with `--since <marker>`
> (skips files whose `(mtime, size)` are unchanged since the last pass —
> it does **not** diff a git ref; see
> [`INTEGRATION.md` § Keep the index fresh](INTEGRATION.md#step-6--keep-the-index-fresh)).
> One server process per repo.

### Supported hosts

| Host | Wiring doc | Notes |
|------|-----------|-------|
| Claude Code | [`hosts/claude-code.md`](hosts/claude-code.md) | Best-ranked host. One JSON edit to `claude_desktop_config.json` per repo. |
| GitHub Copilot custom agents | [`hosts/copilot.md`](hosts/copilot.md) | MCP server goes in agent frontmatter. Skill descriptions must match ATLAS phases (T, L, A, S) for the selector to fire correctly. |
| Cursor | [`hosts/cursor.md`](hosts/cursor.md) | MCP config via Settings UI or `~/.cursor/mcp.json`. Cursor's agent loop is more eager — keep ATLAS invocations in Plan mode unless you want edits attempted alongside exploration. |

### Upgrading between schema epochs

Upgrading to v2.0.0 (or any release that bumps `SCHEMA_EPOCH`)? One
command: `atlas-aci index --repo /path/to/your/repo`. The on-disk index
is pure, disposable derived data — always rebuilt fully from source,
never migrated in place — so a schema change is always a fresh
`.atlas/graph.<SCHEMA_EPOCH>.db` (currently epoch `5`), never an `ALTER
TABLE` ladder against the old one. **`serve` itself always starts** — it
does not check the epoch at startup — but every tool call that touches
the code graph independently asks whether the current-epoch index
exists, and a stale or missing one returns a structured
`INDEX_UNAVAILABLE` `ToolError` naming the exact `index` command, rather
than silently serving stale or wrong results. `serve` performs zero
writes under `.atlas` either way. A downgrade to an older binary finds no
matching epoch file and rebuilds its own on its next `index` run — one
rebuild per direction you move, never a ping-pong.

<details>
<summary>Running in Docker, container image, development, production hardening</summary>

**Running in Docker** — two invocations, documented in detail in the
[`mcp-server/Dockerfile`](mcp-server/Dockerfile) header:

```bash
# Index once (writable repo mount)
docker run --rm -v /path/to/repo:/repo \
    atlas-aci index --repo /repo --langs ruby,python,javascript,typescript

# Serve long-lived (read-only repo mount, persistent memex)
docker run --rm -i --read-only \
    -v /path/to/repo:/repo:ro -v atlas-memex:/memex \
    atlas-aci
```

The `--read-only` flag plus the `:ro` bind mount is an OS-level guarantee
that the server cannot mutate your repo — stronger than any code-level
check. Full index-then-serve pattern:
[`INTEGRATION.md`](INTEGRATION.md#running-in-docker).

**Container image** — `ghcr.io/rynaro/atlas-aci` is the canonical
distribution channel (linux/amd64 + linux/arm64). See
[Verified releases](#verified-releases) for signing/SBOM/provenance, and
[`INTEGRATION.md § GHCR distribution`](INTEGRATION.md#ghcr-distribution)
for the full consumer workflow including
[`eidolons mcp atlas-aci`](https://github.com/Rynaro/eidolons).

**Development** — container-based dev is preferred (a dedicated
[`Dockerfile.dev`](mcp-server/Dockerfile.dev) ships `pytest`, `ruff`,
`mypy`):

```bash
cd mcp-server
docker build -f Dockerfile.dev -t atlas-aci-dev .
docker run --rm -v "$PWD":/work atlas-aci-dev uv run pytest -q
```

Running on the host instead: `uv sync && uv run pytest && uv run ruff
check src/ tests/ && uv run mypy src/`.

**Running the canary suite:**

```bash
uv run python scripts/run-canaries.py \
    --repo /path/to/your/repo \
    --canaries atlas/evals/canary-missions.md \
    --output /tmp/canary-results.json
```

**Status: the host dispatcher is not implemented yet.** `--host stub` is
the only wired option; `StubDispatcher.dispatch()` raises
`NotImplementedError`, and any other `--host` value raises the same in
`main()`. There is no canary pass-rate to quote until a real dispatcher
(Claude Code API, Copilot Action, Cursor headless) is implemented — see
the deferred note in
[`scripts/run-canaries.py`](scripts/run-canaries.py); tracked as
follow-up work in [`SETUP.md §7`](SETUP.md#7-run-the-canary-suite).

**Production hardening** — before letting `atlas-aci` run unattended on
shared infrastructure, work through the full checklist in
[`SETUP.md §8`](SETUP.md#8-production-hardening-checklist). The two items
most commonly skipped: sandboxing `test_dry_run` at the OS level, and
mounting the repo read-only at the OS level (a stronger guarantee than
any code-level check, and one that survives mis-configuration of the
server itself).

</details>

## When atlas-aci is the wrong tool

Honest scoping beats a benchmark. Skip atlas-aci, or reach for something
else alongside it, when:

- **You need to apply edits, run migrations, or deploy code.** This
  server is read-only by construction; use a different server — don't
  hollow this one out to fit.
- **You need agent-callable `export`/`import`.** Both are **CLI-only**,
  deliberately: `import` is a mutating primitive and the agent is the
  untrusted party — see [How it's bounded](#how-its-bounded).
- **Your repo exceeds Spree scale and you need the committed, portable
  graph.** The measured ceiling is **84.63%** of GitHub's 100 MB hard
  limit at Spree scale; re-index instead of importing, or publish the
  export out-of-band (CI artifact, shared cache) instead of committing
  it.
- **You need exact Ruby setter/operator call-site resolution.**
  Definitions (`def foo=`, `def +`, `def []=`) are captured; a *caller*
  of a Ruby setter or operator is still invisible to `callers_of` today
  — see [The seven tools](#the-seven-tools) and
  [`docs/graph-query-dsl.md`](docs/graph-query-dsl.md).
- **You need cross-OS unicode-safe export paths.** NFC/NFD filename
  normalization across macOS/Linux is documented, not mechanically
  fixed — see [How it's bounded](#how-its-bounded).
- **You're running `test_dry_run` against untrusted models with no OS
  sandbox.** The code cannot enforce sandboxing; the operator must put
  the server behind a DevContainer, Firecracker microVM, or equivalent.

## Verified releases

Every published GA image (tagged `v<x>.<y>.<z>` without a pre-release
suffix) is scanned by Trivy before signing and has no known HIGH/CRITICAL
CVEs at release time — the [release workflow](.github/workflows/release.yml)
fails and does not proceed to signing, attestation, or GitHub Release
creation otherwise. Every release is:

- **Cosign-signed, keyless** — GitHub Actions OIDC token, no long-lived keys.
- **SBOM-attested (SPDX)** — via `actions/attest-sbom`.
- **Build-provenance-attested** — via `actions/attest-build-provenance`.

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/Rynaro/atlas-aci/.github/workflows/release.yml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/rynaro/atlas-aci@sha256:<digest>

gh attestation verify oci://ghcr.io/rynaro/atlas-aci@sha256:<digest> --owner Rynaro
```

Digest-pinned pulls are the most reliable — a tag can be overwritten, a
digest never changes. Each release's notes include the exact
`ghcr.io/rynaro/atlas-aci@sha256:<digest>` string ready to copy.

## What's in this repo

| Area | What it contains |
|------|------------------|
| [`mcp-server/`](mcp-server/) | The Python MCP server — CLI, enforcement layer, tools, tree-sitter indexer |
| [`mcp-server/tests/`](mcp-server/tests/) | The test suite: enforcement invariants, indexer honesty, DSL dispatch, epoch substrate — incl. [`test_codegraph.py`](mcp-server/tests/test_codegraph.py) |
| [`hosts/`](hosts/) | Per-editor wiring: Claude Code, Copilot, Cursor |
| [`scripts/`](scripts/) | Guards for the augmentation workstreams (canary orchestrator, probe/export-size verifiers) — not shipped project code |
| [`docs/`](docs/) | Deep reference — the `graph_query` DSL |
| [`SETUP.md`](SETUP.md) | End-to-end playbook: install → host wiring → canaries → hardening |
| [`INTEGRATION.md`](INTEGRATION.md) | Onboarding an existing codebase: indexing, skip tuning, freshness, multi-repo |
| [`CLAUDE.md`](CLAUDE.md) | How Claude Code should navigate this repo when opened in an agent loop |

<details>
<summary>Full repository tree</summary>

```
atlas-aci/
├── README.md                          ← you are here (orientation)
├── SETUP.md                           ← end-to-end playbook
├── INTEGRATION.md                     ← codebase onboarding workflow
├── CLAUDE.md                          ← in-repo Claude Code guidance
├── docs/
│   └── graph-query-dsl.md             ← graph_query DSL deep-dive (A1-A4)
│
├── mcp-server/                        ← the Python MCP server
│   ├── pyproject.toml
│   ├── Dockerfile                     ← production runtime image
│   ├── Dockerfile.dev                 ← dev-loop image (pytest/ruff/mypy)
│   ├── .dockerignore
│   ├── README.md                      ← package-scoped quick start
│   ├── src/atlas_aci/
│   │   ├── __main__.py                ← Click CLI: serve | index | export | import | tools
│   │   ├── server.py                  ← MCP stdio wiring + dispatcher
│   │   ├── enforcement.py             ← read-only guard, bounds, rate limit, telemetry
│   │   ├── config.py                  ← Config dataclass + skip patterns + path_is_within
│   │   ├── codegraph.py               ← tree-sitter indexer + SQLite queries + export/import
│   │   ├── label_propagation.py       ← dependency-free deterministic LPA core
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
│       ├── test_confidence.py         ← confidence enum (EXTRACTED/INFERRED/AMBIGUOUS)
│       ├── test_communities.py        ← label propagation + probe gate
│       ├── test_rationale.py          ← rationale nodes
│       ├── test_export.py             ← export/import (CodeGraph API level)
│       ├── test_cli_export_import.py  ← export/import (CLI reachability level)
│       ├── test_graph_query.py        ← graph_query DSL dispatch
│       ├── test_server.py             ← central bounds chokepoint
│       ├── test_schema_epoch.py       ← epoch-namespaced DB substrate
│       └── test_thesis_negatives.py   ← no LLM, no networkx, ...
│
├── hosts/
│   ├── claude-code.md
│   ├── copilot.md
│   └── cursor.md
│
└── scripts/                           ← guards for the augmentation workstreams,
    │                                     not shipped project code — see each file's own header
    ├── run-canaries.py                ← canary mission orchestrator
    ├── verify-probe-verdict.py        ← probe verdict verifier
    ├── verify-export-size.py          ← export-size-on-Spree verifier
    ├── harden-gate-classify.sh        ← harden-gate.yml's diff classifier
    └── fingerprint-fixture/           ← committed multi-language fixture for the
                                          behavioural indexer fingerprint
```

</details>

## Contributing

Bugs and features for this server belong here. Host-wiring questions
belong in [`hosts/`](hosts/) first — most editor-integration issues are
config, not code. [`scripts/`](scripts/) are augmentation guards for this
repo's own workstreams, not code this project ships to consumers.

## License

Apache-2.0. See the top-level [`LICENSE`](LICENSE) file for the full
text; [`mcp-server/pyproject.toml`](mcp-server/pyproject.toml) declares
the same identifier for the packaged distribution.

---

*If a tool cannot be called, it cannot be misused.*
