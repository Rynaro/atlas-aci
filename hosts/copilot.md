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

For a generic-agent overlay — overlaid on the same repo:

```bash
cp ~/code/generic-agent/generic-agent.agent.md   .github/agents/generic-agent.agent.md
cp -r ~/code/generic-agent/skills/*        .github/skills/    # NOT under atlas/, they're generic-agent-specific
```

## 3. Register the MCP server in the agent frontmatter

Edit `.github/agents/atlas.agent.md` (and `generic-agent.agent.md`) so the
frontmatter includes:

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

For generic-agent, point at the same server but include the domain-specific
extensions (Prism subprocess MCP if/when you split it out):

```yaml
---
name: generic-agent
version: 2.0
methodology: ATLAS
methodology_version: 1.0
replaces: SAGE
tools:
  mcp_servers:
    - name: atlas-aci
      transport: stdio
      command: ["atlas-aci", "serve",
                "--repo", "${workspaceFolder}",
                "--memex-root", "${workspaceFolder}/.atlas/memex"]
    # When prism-codegraph is split out as its own server, add it here:
    # - name: prism-codegraph
    #   transport: stdio
    #   command: ["prism-codegraph", "serve", "--repo", "${workspaceFolder}"]
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

Or for generic-agent:

```
@generic-agent mission — enumerate DataObjects writing to sensitive_records and
prove each is INSERT-only with auth. Decision target: (DataObject,
write_op, auth_method) triples + integrity checklist.
```

## Token-budget note (specific to your setup)

Per your existing `copilot-instructions.md` consolidation work: the
ATLAS+generic-agent agent files together add roughly 1700 BPE tokens to the
always-loaded budget when both agents are present. Skills are
progressive-disclosure (≤200 lines each) so they only weigh in when
loaded.

If your `copilot-instructions.md` + `CONTEXT.md` is currently ~900 BPE
tokens (your stated post-consolidation number), and Archie + generic-agent
profiles add ~1700 BPE, you're at ~2600 BPE always-loaded. Still well
inside the 25% rule for a 200k-token window. Comfortable.

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
