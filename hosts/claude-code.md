# Wiring ATLAS into Claude Code

## 1. Install the server

```bash
cd ~/code/atlas-aci
uv sync
uv tool install .          # installs `atlas-aci` on PATH
```

## 2. Build the index for your repo

```bash
atlas-aci index --repo ~/code/your-repo
```

Re-run after major refactors. ~60s for a 100k-file Rails repo.

## 3. Register as an MCP server

Edit `~/.config/claude/claude_desktop_config.json` (Linux) or
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

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

For multiple repos, register one MCP server per repo with distinct names:

```jsonc
{
  "mcpServers": {
    "atlas-my-repo": {
      "command": "atlas-aci",
      "args": ["serve", "--repo", "/home/you/code/your-repo", ...]
    },
    "atlas-rails-app-2": {
      "command": "atlas-aci",
      "args": ["serve", "--repo", "/home/you/code/other-app", ...]
    }
  }
}
```

## 4. Drop in the ATLAS agent profile

Claude Code reads agent definitions from `~/.config/claude/agents/`:

```bash
mkdir -p ~/.config/claude/agents ~/.config/claude/skills/atlas ~/.config/claude/templates/atlas
cp atlas/agent.md ~/.config/claude/agents/atlas.md
cp atlas/ATLAS.md ~/.config/claude/agents/atlas-spec.md
cp -r atlas/skills/* ~/.config/claude/skills/atlas/
cp -r atlas/templates/* ~/.config/claude/templates/atlas/
```

For a generic-agent overlay:

```bash
cp generic-agent/generic-agent.agent.md ~/.config/claude/agents/generic-agent.md
mkdir -p ~/.config/claude/skills/generic-agent
cp -r generic-agent/skills/* ~/.config/claude/skills/generic-agent/
```

## 5. Restart Claude Code

The MCP servers connect on app startup. After restart, in a chat:

```
> /tools list
```

Should show seven `atlas-aci` tools. If you see `mcp_error`, check
`~/Library/Logs/Claude/mcp*.log` for the failure.

## 6. Invoke ATLAS

```
@atlas mission — list all writers to sensitive_records, decision target:
(DataObject, write_op, authorization_method) triples.
```

(Or `@generic-agent ...` if you have a generic-agent overlay configured.)

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Tools don't appear | MCP server failed to start | Check `~/Library/Logs/Claude/mcp-server-atlas-aci.log` |
| `INDEX_UNAVAILABLE` errors | `atlas-aci index` not yet run | Run it once |
| Path errors | `--repo` doesn't match where the user is asking about | One server per repo |
| Slow tool calls | Tree-sitter parse on every search_symbol | Verify `.atlas/graph.db` exists; rebuild if 0 bytes |
| Server crashes silently | Python exception not caught | Run with `--log-level debug` outside Claude Code to see traces |

## Notes

- Claude Code does NOT respect file-pattern-matched instruction loading (no
  `applyTo` analog). Skills load when their description text semantically
  matches the prompt; phrase your `description:` field accordingly.
- The agent profile (`agent.md`) is auto-loaded when you `@atlas`. The
  spec file (`ATLAS.md`) is loaded on first phase transition via
  description match — this is fine, but means the very first prompt sees
  only the profile.
