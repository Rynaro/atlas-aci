# ATLAS Setup — End-to-End

> From "I have a repo and an ATLAS spec" to "ATLAS is running against my code
> via Claude Code / Copilot / Cursor" in roughly 90 minutes of focused work.

This guide builds the **reference MCP server** (`atlas-aci`) and wires it into
the host of your choice. The server implements the bounded ACI from
`atlas/tools/bounded-aci-spec.md`.

---

## 0. Stack at a glance

| Component | Choice | Why |
|-----------|--------|-----|
| MCP SDK | Python `mcp>=1.2` | Mature, async, stdio + SSE transports |
| Project mgmt | `uv` | Fast, lockfile, single-binary distribution |
| Filesystem search | `ripgrep` (binary) | Already canonical, faster than any Python equivalent |
| Universal AST | `tree-sitter` + `tree-sitter-language-pack` | Vendor-neutral, multi-language, low overhead; also covers Ruby — no separate specialist mode ships today |
| Memex MVP | hashed-directory KV | No DB needed; upgrade later |
| Memex production | `sqlite-vec` | When semantic search over excerpts becomes useful |
| Sandbox | DevContainer or Docker | Required for `test_dry_run` |
| Distribution | `uv tool install .` | Single command, no virtualenv juggling |

If you swap any of these, the other choices shift. The Python+uv+ripgrep
combination is what I'd default to in your environment.

---

## 1. Install prerequisites

```bash
# Python toolchain (uv handles Python install too)
curl -LsSf https://astral.sh/uv/install.sh | sh

# ripgrep
sudo apt-get install -y ripgrep        # Debian/Ubuntu
# brew install ripgrep                 # macOS

# Node, only if you want to run the JS host configs locally (Cursor, etc.)
# Already present on most dev machines.

# Verify
uv --version          # >= 0.4
rg --version          # >= 13
```

---

## 2. Clone the reference implementation

The `mcp-server/` directory in this setup bundle is a working ATLAS ACI
server. Copy it into your tooling repo (or a dedicated `atlas-aci` repo):

```bash
cp -r atlas-aci/mcp-server ~/code/atlas-aci
cd ~/code/atlas-aci
uv sync                # creates .venv, installs deps from pyproject.toml
uv run atlas-aci --help
```

If `uv run atlas-aci --help` prints the usage banner, the server is
runnable. It will not do anything useful yet — you haven't pointed it at a
repo or wired it to a host.

---

## 3. Smoke-test the server in isolation

```bash
# Start the server in stdio mode against your repo
uv run atlas-aci serve --repo ~/code/your-repo --memex-root /tmp/memex

# In another terminal, talk to it via the MCP inspector
npx @modelcontextprotocol/inspector uv run atlas-aci serve --repo ~/code/your-repo
```

The inspector opens a browser UI showing the server's tool manifest. You
should see exactly seven tools:

- `view_file`
- `list_dir`
- `search_text`
- `search_symbol`
- `graph_query`
- `test_dry_run`
- `memex_read`

If you see `edit_file`, `write_file`, or anything mutating, the server is
broken — that's a non-conformant ATLAS implementation. File a bug.

Try a tool call from the inspector:

```json
{"name": "view_file", "arguments": {"path": "Gemfile", "start_line": 1, "end_line": 30}}
```

You should get back ≤30 lines. Try `end_line: 500` and verify you get
exactly 100 lines plus a `next_cursor` field. That's the bounds enforcement
working.

---

## 4. Build the code-graph index

The first time the server runs against a repo, it indexes structure. For a
~100k-file Rails repo this takes 30–120 seconds.

```bash
uv run atlas-aci index --repo ~/code/your-repo --langs ruby,javascript,typescript
```

This produces:

```
~/code/your-repo/.atlas/
└── graph.<epoch>.db   # SQLite: symbols, refs (caller→callee adjacency),
                        # a manifest table (schema epoch), and a files
                        # table (per-file incremental-index state)
```

There is no `symbols.db`, `routes.json`, or `manifest.yaml` — everything
above lives in the single epoch-namespaced `graph.<epoch>.db` file. (There
is also no Rails-routes extraction shipped today.)

Re-run `index` whenever the repo's HEAD shifts significantly. For now, do
it manually; a `post-commit` hook is overkill until you've shipped the
basics. Ship a `git pull` hook later if it's worth automating.

---

## 5. Wire into your host

Pick one. All three configs are in `hosts/`:

- **Claude Code** → `hosts/claude-code.md` (one JSON config edit)
- **GitHub Copilot custom agents** → `hosts/copilot.md` (frontmatter in agent.md)
- **Cursor** → `hosts/cursor.md` (Settings UI)

After wiring, restart the host. The agent should now see the seven ATLAS
tools when it asks `tools/list`.

---

## 6. Drop in the ATLAS agent profile

Copy from the open-source ATLAS bundle:

```bash
# For Claude Code:
cp atlas/agent.md ~/.config/claude/agents/atlas.md
cp atlas/ATLAS.md ~/.config/claude/agents/atlas-spec.md
cp -r atlas/skills ~/.config/claude/skills/atlas/
cp -r atlas/templates ~/.config/claude/templates/atlas/

# For Copilot in a repo:
mkdir -p .github/agents .github/skills/atlas .github/templates/atlas
cp atlas/agent.md .github/agents/atlas.agent.md
cp atlas/ATLAS.md .github/agents/atlas.spec.md
cp -r atlas/skills/* .github/skills/atlas/
cp -r atlas/templates/* .github/templates/atlas/
```

---

## 7. Run the canary suite

```bash
uv run python scripts/run-canaries.py \
  --repo ~/code/your-repo \
  --canaries atlas/evals/canary-missions.md \
  --output /tmp/canary-results.json
```

This is a thin orchestrator that:

1. Loads each canary mission's `mission.md`.
2. Dispatches it to a host adapter you plug in under `--host`.
3. Captures the resulting `scout-report.md`.
4. Compares against the canary's `expected/` answers.
5. Emits pass/fail + telemetry.

**Status: no host dispatcher ships today.** `--host stub` is the only wired
option, and `StubDispatcher.dispatch()` deliberately raises
`NotImplementedError` — see the deferred note in
[`scripts/run-canaries.py`](scripts/run-canaries.py). Real dispatchers
(Claude Code via API, Copilot via Action, Cursor headless) are sketched as
comments in that file but not implemented; there is no pass rate to quote
until one exists. Wire one in before relying on this step.

---

## 8. Production hardening checklist

Before letting ATLAS run unattended on shared infra:

- [ ] **Sandbox the test_dry_run tool.** Use a DevContainer or Firecracker
      microVM. Never let it run in your main shell.
- [ ] **Read-only filesystem mount** for the repo path. The OS-level guarantee
      that the server cannot write is more reliable than code-level checks.
- [ ] **Egress firewall** on the server's network namespace. The ACI does
      not need outbound network; deny by default.
- [ ] **Per-tool rate limits** in the enforcement layer. A misbehaving model
      can issue thousands of probes; cap at e.g. 200 calls / 60s.
- [ ] **Telemetry pipeline.** Wire `enforcement.log_tool_call` to your
      CORTEX JSONL or whatever sink you use.
- [ ] **Secret scanning** on `view_file` output. Pipe through `gitleaks`
      regex pre-checks; redact matches before returning to the agent.
- [ ] **Memex retention policy.** sqlite-vec grows; decide whether
      mission-scoped purges or aggregate retention is right for your audit
      requirements.
- [ ] **Multi-repo isolation.** One server process per repo, or path-prefix
      enforcement in the server. Cross-repo leaks are silent and bad.

---

## 9. Operating notes

### Token cost of the server itself

The MCP tool manifest is loaded into every agent context. The seven ATLAS
tools serialize to ~1500 tokens of JSON Schema. That's your fixed
overhead per conversation. Running additional MCP servers alongside
`atlas-aci` adds their own manifest overhead on top of that.

### Debugging tool calls

Run with `--log-level debug` to get every tool call dumped to stderr:

```
[14:32:01] tool=view_file path=app/flows/foo.rb start=1 end=100 bytes_out=4231 ms=8
[14:32:02] tool=search_symbol name=RecordVote kind=any defs=1 refs=4 ms=12
[14:32:02] FORBIDDEN tool=edit_file (rejected at enforcement layer)
```

That last line is what you want to see if a model misbehaves — the
enforcement layer caught it.

### Updating the index incrementally

`--since <marker>` skips files whose `(mtime_ns, size)` are unchanged since
the last pass — it does not diff a git ref, so any truthy value works as
the marker:

```bash
uv run atlas-aci index --repo ... --since incremental
```

Tree-sitter parsing is fast enough that incremental indexing matters less
than you'd expect. Full re-index of a 100k-file Rails repo on a modern
laptop is ~60s. Worry about this later.

---

## 10. What to build next (after MVP works)

Roughly in priority order:

1. **`ar_query_graph` adapter** — the Rails-aware writer/reader-to-table
   resolver. Enables high-confidence anchoring of write paths for
   missions targeting data integrity.
2. **sqlite-vec Memex backend.** Enables `memex.search(query)` so the
   Locate phase can find prior excerpts semantically. Big quality lift on
   recurring missions over the same area.
3. **Per-mission audit pack.** A `--audit-tarball MISSION-ID` flag that
   bundles the scout report + Memex + tool-call log into one tar for
   review. Especially useful for HARD GATE missions where you'll want a
   permanent trail.
4. **Telemetry → CORTEX bridge.** Right now telemetry is per-mission. A
   pipeline that ingests every mission's telemetry into your existing
   CORTEX JSONL gives you the cross-session reflection signal.
5. **A canary host dispatcher.** `scripts/run-canaries.py` ships only a
   `StubDispatcher`; a real Claude Code / Copilot / Cursor adapter is
   needed before the canary suite produces a meaningful pass rate.

---

## Anti-patterns I'd avoid

- **Building the MCP server inside the agent's repo.** It should be its
  own deliverable so it can serve multiple repos.
- **Letting the MCP server run as the agent process's user.** Run it as a
  dedicated user with read-only repo mount. The privilege boundary is the
  whole point.
- **Caching tool responses across missions.** Each mission's context is
  ephemeral; cached `view_file` results outliving a mission lead to silent
  staleness when the repo changes.
- **Adding "convenience" tools** like `summarize_file` or `explain_module`.
  Those are LLM-side concerns. Keep the ACI primitive and dumb.
- **Declaring victory without an end-to-end check.** The canary suite is
  the intended mechanism for this, but it needs a real host dispatcher
  wired in first (§10 item 5) — it ships with only a stub today.

---

## File map for this setup bundle

```
atlas-aci/
├── SETUP.md                          # this file
│
├── mcp-server/                       # runnable Python MCP server
│   ├── pyproject.toml
│   ├── README.md
│   ├── Dockerfile
│   ├── src/atlas_aci/
│   │   ├── __init__.py
│   │   ├── __main__.py               # CLI entry: serve | index | --help
│   │   ├── server.py                 # MCP server wiring
│   │   ├── enforcement.py            # bounds, read-only guard, logging
│   │   ├── memex.py                  # hashed-dir Memex backend
│   │   ├── codegraph.py              # tree-sitter wrapper + symbol index
│   │   ├── config.py                 # repo, ignore lists, limits
│   │   └── tools/
│   │       ├── __init__.py
│   │       ├── view_file.py
│   │       ├── list_dir.py
│   │       ├── search_text.py        # ripgrep wrapper
│   │       ├── search_symbol.py
│   │       ├── graph_query.py
│   │       └── test_dry_run.py
│   └── tests/
│       ├── test_enforcement.py
│       ├── test_codegraph.py
│       ├── test_server.py
│       └── test_schema_epoch.py
│
├── hosts/                            # per-host wiring snippets
│   ├── claude-code.md
│   ├── copilot.md
│   └── cursor.md
│
└── scripts/
    └── run-canaries.py               # canary suite orchestrator
```
