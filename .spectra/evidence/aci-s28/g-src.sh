#!/bin/sh
# G-SRC: gated paths byte-unchanged vs the base, src changes limited to the allowed four files,
# no tracked test file modified.  Run from anywhere; exit 0 means every check passed.
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)" || exit 2
BASE=8d7dd73
A=mcp-server/src/atlas_aci
echo "== 1. gated paths byte-unchanged vs $BASE"
git diff --exit-code $BASE -- $A/server.py $A/enforcement.py $A/config.py $A/codegraph.py \
  $A/protocol $A/query $A/tools $A/storage $A/extractors $A/providers $A/indexing $A/resolvers \
  $A/frameworks $A/architecture $A/counterfactual $A/contracts $A/importers $A/overlays \
  $A/reports mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile \
  mcp-server/Dockerfile.dev .github/workflows || { echo "FAIL gated path changed"; exit 1; }
echo "ok"
echo "== 2. changed and untracked files under mcp-server/src"
{ git diff --name-only $BASE -- mcp-server/src; \
  git ls-files --others --exclude-standard -- mcp-server/src; } | sort -u > /tmp/s28-gsrc.$$
cat /tmp/s28-gsrc.$$
printf '%s\n' $A/__main__.py $A/dependencies/__init__.py $A/dependencies/python_wheel.py \
  $A/dependencies/uv_lock.py | sort > /tmp/s28-allowed.$$
extra=$(comm -23 /tmp/s28-gsrc.$$ /tmp/s28-allowed.$$)
rm -f /tmp/s28-gsrc.$$ /tmp/s28-allowed.$$
[ -z "$extra" ] || { echo "FAIL files outside the allowed four:"; echo "$extra"; exit 1; }
echo "ok"
echo "== 3. no tracked test file modified (only new test_s28_*.py and fixtures)"
changed=$(git diff --name-only $BASE -- mcp-server/tests)
[ -z "$changed" ] || { echo "FAIL tracked tests changed:"; echo "$changed"; exit 1; }
untracked=$(git ls-files --others --exclude-standard -- mcp-server/tests \
  | grep -v -e '^mcp-server/tests/slices/test_s28_[a-z_]*\.py$' \
            -e '^mcp-server/tests/fixtures/python_dependencies/')
[ -z "$untracked" ] || { echo "FAIL unexpected new test files:"; echo "$untracked"; exit 1; }
echo "ok"
echo "G-SRC PASS"
