# Wiring ATLAS into GitHub Copilot

> Copilot's custom-agent + skill + MCP-server surface is the most natural
> home for ATLAS in your existing setup. The agent profile becomes a
> Copilot custom agent; the ATLAS skills become Copilot skills with
> trigger descriptions; the MCP server provides the tools.

## 1. Install the server

```bash
cd ~/code/atlas-aci
uv sync
uv tool install .
atlas-aci index --repo .   # from inside the target repo
```

## 2. Place the ATLAS files in the repo

```bash
cd ~/code/your-repo

mkdir -p .github/agents .github/skills/atlas .github/templates/atlas
cp ~/code/atlas/agent.md       .github/agents/atlas.agent.md
cp ~/code/atlas/ATLAS.md       .github/agents/atlas.spec.md
cp -r ~/code/atlas/skills/*    .github/skills/atlas/
cp -r ~/code/atlas/templates/* .github/templates/atlas/
```

## 3. Register the MCP server in the agent frontmatter

Edit `.github/agents/atlas.agent.md` so the frontmatter includes:

```yaml
---
name: atlas
version: 1.0
methodology: ATLAS
role: Explorer/Scout — read-only codebase intelligence
handoffs:
  - spectra
  - apivr

tools:
  mcp_servers:
    - name: atlas-aci
      transport: stdio
      command: ["atlas-aci", "serve",
                "--repo", "${workspaceFolder}",
                "--memex-root", "${workspaceFolder}/.atlas/memex"]
---
```

## 4. Skill descriptions matter

Copilot loads skills when the prompt semantically matches the skill
`description`. Each ATLAS skill SKILL.md should start with frontmatter:

```yaml
---
name: atlas-traverse
description: |
  Use during ATLAS Phase T (Traverse). Builds the structural map of a
  repository using Tree-sitter, ripgrep, and graph queries. Triggered when
  the agent has produced a mission.md and needs to enumerate entrypoints,
  modules, and dependency edges before diving into specific questions.
---
```

The description is your only handle on triggering. Be specific about
*when* (which phase) and *what* (which artifact follows).

## 5. Verify the agent loads

In the Copilot chat panel:

```
@atlas /capabilities
```

Should list the seven MCP tools. If you see `0 tools available`, the
MCP command failed to spawn — check the workspace path and that
`atlas-aci` is on PATH.

## 6. Invoke

```
@atlas mission — map all Sidekiq workers in app/workers/** that touch
voter PII. Decision target: worker → PII-field → handling-policy matrix.
```

## Token-budget note

The ATLAS agent file adds roughly 900 BPE tokens to the always-loaded
budget. Skills are progressive-disclosure (≤200 lines each) so they only
weigh in when loaded.

If your `copilot-instructions.md` + `CONTEXT.md` is currently ~900 BPE
tokens, adding the ATLAS profile puts you at ~1800 BPE always-loaded.
Still well inside the 25% rule for a 200k-token window. Comfortable.

## Custom agent handoff buttons

Copilot custom-agents support declarative handoffs in frontmatter:

```yaml
handoffs:
  - target: spectra
    label: "Generate spec from this scout report"
    prompt: "Veronica, draft a SPECTRA spec from the attached ATLAS scout report."
  - target: apivr
    label: "Implement R-1"
    prompt: "Archie, implement recommended action R-1 from the attached scout report."
```

This surfaces buttons in the Copilot UI. Wire these to match the
`→ SPECTRA` / `→ APIVR-Δ` labels ATLAS emits.
