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

For generic-agent, a parallel `generic-agent.mdc` rule with the domain-specific overlay.

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
