#!/bin/sh
# ACI-S39 G-SRC. Works on the working tree (tracked diff + untracked files) so it holds whether the
# tree is untracked, staged or committed.
#  - everything under mcp-server/src except the five allowed files is byte-unchanged vs the base,
#    including server.py; pyproject.toml, uv.lock, the Dockerfiles, .github/workflows, scripts,
#    atlas and docs are unchanged
#  - changed/new files under mcp-server/src are exactly {protocol/http.py (A), __main__.py,
#    config.py, tools/search_text.py, tools/test_dry_run.py}; the only untracked one is http.py
#  - the two tools files have 0 deleted lines
#  - tests changes are test_s39_*.py only; hosts changes are http-transport.md (A) and
#    protocol-compatibility.md (M); other tracked changes are CHANGELOG.md and README.md
set -u
BASE=8ab6582e862d8c3bc29141104e58287bbee8f6be
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILED=0
P=mcp-server/src/atlas_aci
FILES=$(git ls-files mcp-server/src | grep -v -e "^$P/protocol/http.py$" -e "^$P/__main__.py$" -e "^$P/config.py$" -e "^$P/tools/search_text.py$" -e "^$P/tools/test_dry_run.py$" -e __pycache__)
[ -n "$FILES" ] || { echo "FAIL no tracked src files listed"; exit 2; }
# shellcheck disable=SC2086
git diff --exit-code "$BASE" -- $FILES mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile \
    mcp-server/Dockerfile.dev .github/workflows scripts atlas docs > /dev/null \
    && echo "PASS every tracked src file except the five allowed ones, pyproject, uv.lock, Dockerfiles, workflows, scripts, atlas, docs: byte-identical vs $BASE" \
    || { echo "FAIL a gated path changed"; FAILED=1; }
git diff --exit-code "$BASE" -- mcp-server/src ":!$P/protocol/http.py" ":!$P/__main__.py" ":!$P/config.py" \
    ":!$P/tools/search_text.py" ":!$P/tools/test_dry_run.py" mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows > /dev/null \
    && echo "PASS the exact G-SRC git diff --exit-code (src minus the five files, pyproject, uv.lock, workflows)" \
    || { echo "FAIL the exact G-SRC diff command"; FAILED=1; }
git diff --exit-code "$BASE" -- "$P/server.py" > /dev/null \
    && echo "PASS server.py byte-identical vs $BASE" || { echo "FAIL server.py changed"; FAILED=1; }
NAMES=$({ git diff --name-only "$BASE" -- mcp-server/src; git ls-files --others --exclude-standard -- mcp-server/src; } | grep -v __pycache__ | LC_ALL=C sort -u | tr '\n' ' ')
WANT="$P/__main__.py $P/config.py $P/protocol/http.py $P/tools/search_text.py $P/tools/test_dry_run.py "
[ "$NAMES" = "$WANT" ] && echo "PASS changed or new src files are exactly the five allowed ones" \
    || { echo "FAIL changed src files: $NAMES"; FAILED=1; }
UNTRACKED=$(git ls-files --others --exclude-standard -- mcp-server/src | grep -v __pycache__ | tr '\n' ' ')
case $UNTRACKED in
    "" | "$P/protocol/http.py ") echo "PASS the only untracked file under mcp-server/src is (at most) protocol/http.py" ;;
    *) echo "FAIL untracked under src: $UNTRACKED"; FAILED=1 ;;
esac
STATUS=$(git diff --name-status "$BASE" -- mcp-server/src | LC_ALL=C sort | tr '\t\n' '  ')
case $STATUS in
    "M $P/__main__.py M $P/config.py M $P/tools/search_text.py M $P/tools/test_dry_run.py " | "A $P/protocol/http.py M $P/__main__.py M $P/config.py M $P/tools/search_text.py M $P/tools/test_dry_run.py ")
        echo "PASS name-status: M __main__.py config.py tools/search_text.py tools/test_dry_run.py (+ A protocol/http.py when tracked)" ;;
    *) echo "FAIL name-status: $STATUS"; FAILED=1 ;;
esac
NUM=$(git diff --numstat "$BASE" -- "$P/tools/search_text.py" "$P/tools/test_dry_run.py" | awk '{d += $2; n++} END {print d + 0, n + 0}')
[ "$NUM" = "0 2" ] && echo "PASS numstat: 0 deleted lines in the two tools files" || { echo "FAIL tools numstat '$NUM'"; FAILED=1; }
BADT=$({ git diff --name-only "$BASE" -- mcp-server/tests; git ls-files --others --exclude-standard mcp-server/tests | grep -v __pycache__; } \
    | grep -v -e '^mcp-server/tests/slices/test_s39_' || true)
[ -z "$BADT" ] && echo "PASS tests changes are only test_s39_*.py" || { echo "FAIL other test changes: $BADT"; FAILED=1; }
BADH=$({ git diff --name-only "$BASE" -- hosts; git ls-files --others --exclude-standard hosts; } | LC_ALL=C sort -u \
    | grep -v -e '^hosts/http-transport.md$' -e '^hosts/protocol-compatibility.md$' || true)
[ -z "$BADH" ] && echo "PASS hosts changes are only http-transport.md and protocol-compatibility.md" || { echo "FAIL other hosts changes: $BADH"; FAILED=1; }
HOSTS=$({ git diff --name-only "$BASE" -- hosts; git ls-files --others --exclude-standard hosts; } | LC_ALL=C sort -u | tr '\n' ' ')
[ "$HOSTS" = "hosts/http-transport.md hosts/protocol-compatibility.md " ] && echo "PASS both hosts files changed (one new, one edited)" || { echo "FAIL hosts set: $HOSTS"; FAILED=1; }
[ -f .gitattributes ] && { echo "FAIL .gitattributes present"; FAILED=1; } || echo "PASS no .gitattributes"
CH=$(git diff --name-only "$BASE" | grep -v -e '^mcp-server/src/' -e '^mcp-server/tests/' -e '^hosts/' -e '^\.spectra/' || true)
[ "$(echo "$CH" | tr '\n' ' ')" = "CHANGELOG.md README.md " ] && echo "PASS other tracked changes are exactly CHANGELOG.md and README.md" \
    || { echo "FAIL other tracked changes: $CH"; FAILED=1; }
exit "$FAILED"
