#!/bin/sh
# ACI-S40 G-SRC. Works on the working tree (tracked diff + untracked files) so it holds whether the
# tree is untracked, staged or committed.
#  - everything under mcp-server/src except the allowed files is byte-unchanged vs the base,
#    including server.py, protocol/*, codegraph.py, enforcement.py, config.py and query/leases.py;
#    pyproject.toml, uv.lock, the Dockerfiles, .github/workflows, scripts and atlas are unchanged
#  - changed/new files under mcp-server/src are exactly {A query/authz.py, A query/authn.py,
#    A query/shared_http.py, M __main__.py, M tools/search_text.py, M tools/test_dry_run.py};
#    the only untracked ones are the three new query modules
#  - __main__.py has 0 deleted lines; tests changes are new test_s40_*.py only; hosts changes are
#    http-transport.md only with 0 deleted lines; docs changes are docs/operations/shared-http-
#    deployment.md only; other tracked changes are exactly CHANGELOG.md and README.md with 0 deletions
set -u
BASE=f4f9f9f2b3c41ecf4eb4f5b65a97c9ae31991502
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILED=0
P=mcp-server/src/atlas_aci
ALLOWED="$P/query/authz.py $P/query/authn.py $P/query/shared_http.py $P/__main__.py $P/tools/search_text.py $P/tools/test_dry_run.py"
FILES=$(git ls-files mcp-server/src | grep -v __pycache__ | grep -v -x -e "$P/__main__.py" -e "$P/tools/search_text.py" -e "$P/tools/test_dry_run.py")
[ -n "$FILES" ] || { echo "FAIL no tracked src files listed"; exit 2; }
# shellcheck disable=SC2086
git diff --exit-code "$BASE" -- $FILES mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile \
    mcp-server/Dockerfile.dev .github/workflows scripts atlas > /dev/null \
    && echo "PASS every tracked src file except the three allowed edits, pyproject, uv.lock, Dockerfiles, workflows, scripts, atlas: byte-identical vs $BASE" \
    || { echo "FAIL a gated path changed"; FAILED=1; }
git diff --exit-code "$BASE" -- mcp-server/src ":!$P/__main__.py" ":!$P/tools/search_text.py" ":!$P/tools/test_dry_run.py" \
    mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile .github/workflows scripts atlas > /dev/null \
    && echo "PASS the exact G-SRC git diff --exit-code (src minus the three edited files, pyproject, uv.lock, Dockerfile, workflows, scripts, atlas)" \
    || { echo "FAIL the exact G-SRC diff command"; FAILED=1; }
for f in server.py protocol/http.py codegraph.py enforcement.py config.py query/leases.py; do
    git diff --exit-code "$BASE" -- "$P/$f" > /dev/null \
        && echo "PASS $f byte-identical vs $BASE" || { echo "FAIL $f changed"; FAILED=1; }
done
git diff --exit-code "$BASE" -- "$P/protocol" > /dev/null \
    && echo "PASS protocol/* byte-identical vs $BASE" || { echo "FAIL protocol/* changed"; FAILED=1; }
NAMES=$({ git diff --name-only "$BASE" -- mcp-server/src; git ls-files --others --exclude-standard -- mcp-server/src; } | grep -v __pycache__ | LC_ALL=C sort -u | tr '\n' ' ')
WANT=$(for f in $ALLOWED; do echo "$f"; done | LC_ALL=C sort | tr '\n' ' ')
[ "$NAMES" = "$WANT" ] && echo "PASS changed or new src files are exactly the six allowed ones" \
    || { echo "FAIL changed src files: $NAMES"; FAILED=1; }
UNTRACKED=$(git ls-files --others --exclude-standard -- mcp-server/src | grep -v __pycache__ | LC_ALL=C sort | tr '\n' ' ')
case $UNTRACKED in
    "" | "$P/query/authn.py $P/query/authz.py $P/query/shared_http.py ") echo "PASS the only untracked files under mcp-server/src are (at most) the three new query modules" ;;
    *) echo "FAIL untracked under src: $UNTRACKED"; FAILED=1 ;;
esac
for f in query/authz.py query/authn.py query/shared_http.py; do
    [ -s "$P/$f" ] || { echo "FAIL $f is missing or empty"; FAILED=1; }
done
NUM=$(git diff --numstat "$BASE" -- "$P/__main__.py" | awk '{d += $2; n++} END {print d + 0, n + 0}')
if [ "$NUM" = "0 1" ] || ! git ls-files --error-unmatch "$P/__main__.py" > /dev/null 2>&1; then
    echo "PASS __main__.py numstat: 0 deleted lines ($NUM)"
else echo "FAIL __main__.py numstat '$NUM'"; FAILED=1; fi
BADT=$({ git diff --name-only "$BASE" -- mcp-server/tests; git ls-files --others --exclude-standard mcp-server/tests | grep -v __pycache__; } \
    | grep -v -e '^mcp-server/tests/slices/test_s40_' || true)
[ -z "$BADT" ] && echo "PASS tests changes are only test_s40_*.py (no existing test is edited)" || { echo "FAIL other test changes: $BADT"; FAILED=1; }
EDITED=$(git diff --name-only "$BASE" -- mcp-server/tests || true)
[ -z "$EDITED" ] && echo "PASS no tracked test file differs from the base" || { echo "FAIL tracked test files differ: $EDITED"; FAILED=1; }
BADH=$({ git diff --name-only "$BASE" -- hosts; git ls-files --others --exclude-standard hosts; } | LC_ALL=C sort -u \
    | grep -v -e '^hosts/http-transport.md$' || true)
[ -z "$BADH" ] && echo "PASS hosts changes are only http-transport.md" || { echo "FAIL other hosts changes: $BADH"; FAILED=1; }
HD=$(git diff --numstat "$BASE" -- hosts/http-transport.md | awk '{d += $2; n++} END {print d + 0, n + 0}')
[ "$HD" = "0 1" ] && echo "PASS http-transport.md: 0 deleted lines" || { echo "FAIL http-transport.md numstat '$HD'"; FAILED=1; }
DOCS=$({ git diff --name-only "$BASE" -- docs; git ls-files --others --exclude-standard docs; } | LC_ALL=C sort -u | tr '\n' ' ')
[ "$DOCS" = "docs/operations/shared-http-deployment.md " ] && echo "PASS docs changes are exactly docs/operations/shared-http-deployment.md" || { echo "FAIL docs set: $DOCS"; FAILED=1; }
CH=$({ git diff --name-only "$BASE"; git ls-files --others --exclude-standard; } | grep -v -e '^mcp-server/src/' -e '^mcp-server/tests/' -e '^hosts/' -e '^docs/operations/' -e '^\.spectra/' | LC_ALL=C sort -u || true)
[ "$(echo "$CH" | tr '\n' ' ')" = "CHANGELOG.md README.md " ] && echo "PASS other changes are exactly CHANGELOG.md and README.md" \
    || { echo "FAIL other changes: $CH"; FAILED=1; }
RD=$(git diff --numstat "$BASE" -- CHANGELOG.md README.md | awk '{d += $2; n++} END {print d + 0, n + 0}')
[ "$RD" = "0 2" ] && echo "PASS CHANGELOG.md and README.md: 0 deleted lines" || { echo "FAIL root docs numstat '$RD'"; FAILED=1; }
[ -f .gitattributes ] && { echo "FAIL .gitattributes present"; FAILED=1; } || echo "PASS no .gitattributes"
echo "== g-src exit $FAILED"
exit "$FAILED"
