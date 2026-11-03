#!/bin/sh
exec docker run --rm --user 1000:1000 -e HOME=/hc -e PYTHONPATH=/ws/mcp-server/src \
  -v /private/tmp/claude-501/-Users-henrique-workspace-oss-agents-atlas-aci/694bbcf4-4624-4586-978b-245709fb6cab/scratchpad/home:/hc \
  -v /Users/henrique/workspace/oss/agents/atlas-aci:/ws -w /ws/mcp-server atlas-aci-dev-s16 "$@"
