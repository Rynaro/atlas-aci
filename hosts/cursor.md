# Wiring ATLAS into Cursor

## 1. Install the server

```bash
cd ~/code/atlas-aci
uv sync
uv tool install .
atlas-aci index --repo ~/code/your-repo
```

## 2. Add the MCP server in Cursor settings

Cursor → Settings → MCP Servers → Add Custom Server:

| Field | Value |
|-------|-------|
| Name | `atlas-aci` |
| Transport | `stdio` |
| Command | `atlas-aci` |
| Args | `serve --repo /home/you/code/your-repo --memex-root /home/you/.atlas/memex/your-repo` |

Or, equivalently, edit `~/.cursor/mcp.json`:

```jsonc
{
  "mcpServers": {
    "atlas-aci": {
      "command": "atlas-aci",
      "args": [
        "serve",
        "--repo", "/home/you/code/your-repo",
        "--memex-root", "/home/you/.atlas/memex/your-repo"
      ]
    }
  }
}
```

Restart Cursor. The MCP indicator (bottom-right) should show
`atlas-aci ✓`.

## 3. Add the ATLAS agent as a Cursor Rule

Cursor doesn't have first-class custom agents like Copilot, but its
**Rules** system covers the equivalent ground. Place the agent profile as
a project-level rule:

```bash
cd ~/code/your-repo
mkdir -p .cursor/rules
```

`.cursor/rules/atlas.mdc`:

```markdown
---
description: ATLAS scout/explorer methodology — read-only codebase intelligence.
globs: "**/*"
alwaysApply: false
---

# ATLAS — Explorer/Scout

Activate this rule when the user asks for codebase mapping, exploration,
scout reports, or any read-only investigation BEFORE implementation.

[paste contents of atlas/agent.md here, or reference via @-mention to
~/code/atlas/agent.md as needed]

When activated, follow the ATLAS methodology defined in
`atlas/ATLAS.md`. Use the `atlas-aci` MCP tools exclusively for
filesystem and code-graph access. Refuse any write-scoped requests and
hand off per the recipient labels in the scout report.
```

## 4. Invoke

In Cursor's chat or composer:

```
Activate ATLAS. Mission — map all Sidekiq workers in app/workers/** that
touch voter PII. Decision target: worker → PII-field → handling-policy
matrix.
```

Cursor's Composer mode is closer to Plan Mode in spirit, so it pairs
naturally with the ATLAS Phase A → T → L → A → S progression.

## Caveats

- **Cursor's agent loop is more aggressive than Claude Code's.** It will
  try to apply edits during exploration unless you keep it in
  Composer/Plan mode. The ATLAS agent profile's read-only stance helps,
  but the host-level mode toggle is your hard guarantee.
- **Skill files don't have a clean home in Cursor.** The simplest pattern:
  put each ATLAS phase skill (Traverse, Locate, Abstract, Synthesize) as
  a separate `.mdc` rule with `alwaysApply: false` and clear description
  text. Cursor activates rules by description match.
- **MCP tool manifest serializes per session.** ~1500 token overhead
  per chat. Acceptable; don't worry about it.

## Ranking of host fit for ATLAS

This is opinion, not data — calibrate against your own workflow:

| Host | ATLAS fit | Why |
|------|-----------|-----|
| Claude Code | Best | First-class subagents, Plan Mode pairs cleanly with ATLAS phases, MCP first-class. |
| Copilot custom agents | Very good | Native skill+agent+MCP triad, handoff buttons, repo-anchored config. |
| Cursor | Good | MCP works, but rule-based agent activation is less precise than first-class agents. |
| Local LangGraph | Good | Most flexible, most setup. Worth it if you're heavily customizing the harness. |
| Aider | OK | Single-agent design, no native subagents — Operator pattern is hard to express. |

## Optional external graph root with read-only source

The local default remains `<repo>/.atlas`. For external storage, index and serve
with the same explicit root and canonical source path; memex stays separate:

```bash
atlas-aci index --repo /home/you/code/your-repo --graph-root /home/you/.cache/atlas-graphs
atlas-aci serve --repo /home/you/code/your-repo --graph-root /home/you/.cache/atlas-graphs --memex-root /home/you/.atlas/memex/your-repo
```

Add those same `--graph-root` arguments to this host's MCP server configuration.
Each resolved source path (including each Git worktree) has a distinct namespace;
symlink aliases share identity. No environment variable chooses a graph root,
and an explicit missing root never falls back to a local index. Keep it outside
the source checkout; existing indexes are not moved automatically.

For container configurations, prepare graph/memex directories writable by UID10001
and use `/repo` in **both** invocations (a host path is a different identity):

```bash
docker run --rm --read-only --network none -v /home/you/code/your-repo:/repo:ro \
  -v /path/to/graphs:/graphs -v /path/to/memex:/memex \
  atlas-aci index --repo /repo --graph-root /graphs
docker run --rm -i --read-only --network none -v /home/you/code/your-repo:/repo:ro \
  -v /path/to/graphs:/graphs:ro -v /path/to/memex:/memex \
  atlas-aci serve --repo /repo --graph-root /graphs --memex-root /memex
```

The image includes its shipped parser assets; do not mask `/tmp/.cache` with an
empty mount. The source stays kernel-enforced read-only during indexing and MCP
queries; graph writes and failed-attempt diagnostics stay in the selected root.
