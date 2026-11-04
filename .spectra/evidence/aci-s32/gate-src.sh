#!/bin/sh
# g-src: gated paths byte-unchanged vs 79ae0b0; only counterfactual/* and __main__.py may change under src.
cd "$(git rev-parse --show-toplevel)" || exit 2
base=79ae0b0
S=mcp-server/src/atlas_aci
git diff --exit-code "$base" -- $S/server.py $S/enforcement.py $S/config.py $S/codegraph.py \
  $S/protocol $S/query $S/tools $S/storage $S/extractors $S/providers $S/architecture \
  $S/indexing $S/resolvers $S/reports mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows
status=$?
echo "gated-diff-exit=$status"
changed=$( { git diff --name-only "$base" -- mcp-server/src; git ls-files --others --exclude-standard mcp-server/src; } | sort -u)
echo "changed-under-src:"
echo "$changed"
bad=$(echo "$changed" | grep -v -e '^mcp-server/src/atlas_aci/counterfactual/' -e '^mcp-server/src/atlas_aci/__main__.py$' -e '^$')
if [ -n "$bad" ]; then echo "UNEXPECTED:"; echo "$bad"; status=1; fi
exit $status
