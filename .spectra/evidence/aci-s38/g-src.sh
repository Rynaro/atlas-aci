#!/bin/sh
# ACI-S38 G-SRC. Works on the working tree (tracked diff + untracked files) so it can run before
# the orchestrator commits; after the commit the same checks hold against HEAD.
#  - every path under mcp-server/src except protocol/{results,revisions}.py and server.py, plus
#    mcp-server/pyproject.toml, mcp-server/uv.lock, mcp-server/Dockerfile, .github/workflows,
#    scripts, atlas and docs, is byte-identical to the base commit
#  - tracked changes under mcp-server/src are exactly {protocol/results.py, server.py}; the only
#    untracked file under mcp-server/src is protocol/revisions.py
#  - changed/new files under mcp-server/tests are test_s38_*.py and fixtures/s38/**
#  - changed/new files under hosts are protocol-compatibility.md and cursor.md
set -u
BASE=2db6dd1a3cba6589bf4efc9ad83620e39bacb289
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILED=0
P=mcp-server/src/atlas_aci
# every tracked source file except the two that may change
FILES=$(git ls-files mcp-server/src | grep -v -e "^$P/server.py$" -e "^$P/protocol/results.py$")
[ -n "$FILES" ] || { echo "FAIL no tracked src files listed"; exit 2; }
# shellcheck disable=SC2086
git diff --exit-code "$BASE" -- $FILES mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile \
    mcp-server/Dockerfile.dev .github/workflows scripts atlas docs > /dev/null \
    && echo "PASS every tracked src file except server.py and protocol/results.py, pyproject, uv.lock, Dockerfile, workflows, scripts, atlas, docs: byte-identical vs $BASE" \
    || { echo "FAIL a gated path changed"; FAILED=1; }
git diff --exit-code "$BASE" -- mcp-server/src ':!mcp-server/src/atlas_aci/protocol' ':!mcp-server/src/atlas_aci/server.py' \
    mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows > /dev/null \
    && echo "PASS git diff --exit-code (src except protocol/* and server.py, pyproject, uv.lock, workflows)" \
    || { echo "FAIL the exact G-SRC diff command"; FAILED=1; }
TRACKED=$(git diff --name-only "$BASE" -- mcp-server/src | sort | tr '\n' ' ')
[ "$TRACKED" = "mcp-server/src/atlas_aci/protocol/results.py mcp-server/src/atlas_aci/server.py " ] \
    && echo "PASS tracked src changes are exactly protocol/results.py and server.py" \
    || { echo "FAIL tracked src changes: $TRACKED"; FAILED=1; }
UNTRACKED=$(git ls-files --others --exclude-standard -- mcp-server/src | grep -v __pycache__ | tr '\n' ' ')
[ "$UNTRACKED" = "mcp-server/src/atlas_aci/protocol/revisions.py " ] \
    && echo "PASS the only untracked file under mcp-server/src is protocol/revisions.py" \
    || { echo "FAIL untracked under src: $UNTRACKED"; FAILED=1; }
BADT=$( { git diff --name-only "$BASE" -- mcp-server/tests; git ls-files --others --exclude-standard mcp-server/tests | grep -v __pycache__; } \
    | grep -v -e '^mcp-server/tests/slices/test_s38_' -e '^mcp-server/tests/fixtures/s38/' || true)
[ -z "$BADT" ] && echo "PASS tests changes are only test_s38_*.py and fixtures/s38/**" || { echo "FAIL other test changes: $BADT"; FAILED=1; }
BADH=$( { git diff --name-only "$BASE" -- hosts; git ls-files --others --exclude-standard hosts; } \
    | grep -v -e '^hosts/protocol-compatibility.md$' -e '^hosts/cursor.md$' || true)
[ -z "$BADH" ] && echo "PASS hosts changes are only protocol-compatibility.md and cursor.md" || { echo "FAIL other hosts changes: $BADH"; FAILED=1; }
[ -f .gitattributes ] && { echo "FAIL .gitattributes present"; FAILED=1; } || echo "PASS no .gitattributes"
CH=$(git diff --name-only "$BASE" | grep -v -e '^mcp-server/src/' -e '^mcp-server/tests/' -e '^hosts/' -e '^\.spectra/' || true)
[ "$(echo "$CH" | tr '\n' ' ')" = "CHANGELOG.md README.md " ] && echo "PASS other tracked changes are exactly CHANGELOG.md and README.md" \
    || { echo "FAIL other tracked changes: $CH"; FAILED=1; }
exit "$FAILED"
