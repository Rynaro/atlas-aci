#!/bin/sh
# Recorder topology: repo + scratch at IDENTICAL host paths, docker socket mounted.
exec docker run --rm --user 1000:1000 --group-add 0 -e HOME=/private/tmp/claude-501/-Users-henrique-workspace-oss-agents-atlas-aci/694bbcf4-4624-4586-978b-245709fb6cab/scratchpad/home -e PYTHONPATH=/Users/henrique/workspace/oss/agents/atlas-aci/mcp-server/src \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /private/tmp/claude-501/-Users-henrique-workspace-oss-agents-atlas-aci/694bbcf4-4624-4586-978b-245709fb6cab/scratchpad:/private/tmp/claude-501/-Users-henrique-workspace-oss-agents-atlas-aci/694bbcf4-4624-4586-978b-245709fb6cab/scratchpad -v /Users/henrique/workspace/oss/agents/atlas-aci:/Users/henrique/workspace/oss/agents/atlas-aci -w /Users/henrique/workspace/oss/agents/atlas-aci/mcp-server atlas-aci-dev-s23-recorder "$@"
