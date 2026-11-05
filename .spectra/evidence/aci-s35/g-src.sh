#!/bin/sh
# ACI-S35 G-SRC: the gated paths are byte-unchanged against the base commit; only the allowed
# source files changed; __main__.py is additive; no tracked test file changed.
set -u
BASE=bd6da89
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILED=0
GATED="mcp-server/src/atlas_aci/server.py mcp-server/src/atlas_aci/enforcement.py
mcp-server/src/atlas_aci/config.py mcp-server/src/atlas_aci/codegraph.py
mcp-server/src/atlas_aci/protocol mcp-server/src/atlas_aci/query mcp-server/src/atlas_aci/tools
mcp-server/src/atlas_aci/storage mcp-server/src/atlas_aci/extractors
mcp-server/src/atlas_aci/providers mcp-server/src/atlas_aci/indexing
mcp-server/src/atlas_aci/resolvers mcp-server/src/atlas_aci/frameworks
mcp-server/src/atlas_aci/architecture mcp-server/src/atlas_aci/counterfactual
mcp-server/src/atlas_aci/contracts mcp-server/src/atlas_aci/importers
mcp-server/src/atlas_aci/overlays mcp-server/src/atlas_aci/dependencies
mcp-server/src/atlas_aci/cross_repo mcp-server/src/atlas_aci/history
mcp-server/src/atlas_aci/rationale mcp-server/src/atlas_aci/reports
mcp-server/pyproject.toml mcp-server/uv.lock
mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows"
# shellcheck disable=SC2086
git diff --exit-code "$BASE" -- $GATED >/dev/null && echo "PASS gated paths byte-unchanged vs $BASE" \
    || { echo "FAIL gated paths changed"; git diff --stat "$BASE" -- $GATED; FAILED=1; }
UNTRACKED_GATED=$(git ls-files --others --exclude-standard -- $GATED)
[ -z "$UNTRACKED_GATED" ] && echo "PASS no untracked file under a gated path" \
    || { echo "FAIL untracked under gated paths:"; echo "$UNTRACKED_GATED"; FAILED=1; }
CHANGED=$( { git diff --name-only "$BASE" -- mcp-server/src
             git ls-files --others --exclude-standard mcp-server/src; } | grep -v __pycache__ | sort -u)
ALLOWED="mcp-server/src/atlas_aci/__main__.py
mcp-server/src/atlas_aci/feedback/__init__.py
mcp-server/src/atlas_aci/feedback/outcomes.py
mcp-server/src/atlas_aci/feedback/ranking.py"
EXTRA=$(printf '%s\n' "$CHANGED" | grep -vxF "$ALLOWED" | grep -v '^$')
if [ -z "$EXTRA" ]; then echo "PASS only the allowed source files changed:"; printf '%s\n' "$CHANGED"
else echo "FAIL unexpected source changes:"; printf '%s\n' "$EXTRA"; FAILED=1; fi
REMOVED=$(git diff "$BASE" -- mcp-server/src/atlas_aci/__main__.py | grep -c '^-[^-]')
[ "$REMOVED" = 0 ] && echo "PASS __main__.py is additive (0 removed lines)" \
    || { echo "FAIL __main__.py removes $REMOVED lines"; FAILED=1; }
TESTS=$(git diff --name-only "$BASE" -- mcp-server/tests)
[ -z "$TESTS" ] && echo "PASS no tracked test file modified" \
    || { echo "FAIL tracked tests modified:"; echo "$TESTS"; FAILED=1; }
NEWTESTS=$(git ls-files --others --exclude-standard mcp-server/tests | grep -v __pycache__ | grep -v \
    -e '^mcp-server/tests/slices/test_s35_' -e '^mcp-server/tests/fixtures/feedback/')
[ -z "$NEWTESTS" ] && echo "PASS new test files are only test_s35_* and fixtures/feedback" \
    || { echo "FAIL other new test files:"; echo "$NEWTESTS"; FAILED=1; }
for f in 'alter table' 'nx.compose('; do
    git ls-files --others --modified --exclude-standard mcp-server/src docs/verified-feedback.md \
        | xargs grep -il "$f" 2>/dev/null | grep -v __main__ && { echo "FAIL found $f"; FAILED=1; }
done
ASCII=$(git ls-files --others --exclude-standard mcp-server/src docs/verified-feedback.md \
    | grep -v __pycache__ | xargs grep -lP '[^\x00-\x7F]' 2>/dev/null)
[ -z "$ASCII" ] && echo "PASS new source and docs are ASCII" || { echo "FAIL non-ASCII: $ASCII"; FAILED=1; }
exit "$FAILED"
