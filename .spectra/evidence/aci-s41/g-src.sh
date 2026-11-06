#!/bin/sh
# ACI-S41 G-SRC. Works on the working tree (tracked diff + untracked files) so it holds whether the
# tree is untracked, staged or committed.
#  - tracked src files except __main__.py and the five new modules are byte-unchanged vs the base,
#    including codegraph.py, storage/{__init__,evidence,snapshots}.py, indexing/{__init__,
#    extraction_cache,retrieval}.py, server.py, protocol/*, query/*, config.py, enforcement.py;
#    pyproject.toml, uv.lock, the Dockerfiles, .github/workflows, scripts, atlas and hosts too
#  - changed/new files under mcp-server/src are exactly {A storage/generations.py, A storage/leases.py,
#    A storage/retention.py, A indexing/jobs.py, A indexing/job_runner.py, M __main__.py}
#  - __main__.py has 0 deleted lines
#  - tests: new test_s41_*.py plus test_s40_isolation.py (numstat <= 4 added, 3 deleted)
#  - docs: only docs/operations/index-jobs-and-retention.md; root: CHANGELOG.md and README.md, 0 deletions
set -u
BASE=fa16b1c64f6ad4cb7cfde9957e8536fbaeeff318
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILED=0
P=mcp-server/src/atlas_aci
NEWF="$P/storage/generations.py $P/storage/leases.py $P/storage/retention.py $P/indexing/jobs.py $P/indexing/job_runner.py"
FILES=$(git ls-files mcp-server/src | grep -v __pycache__ | grep -v -x -e "$P/__main__.py" -e "$P/storage/generations.py" -e "$P/storage/leases.py" -e "$P/storage/retention.py" -e "$P/indexing/jobs.py" -e "$P/indexing/job_runner.py")
[ -n "$FILES" ] || { echo "FAIL no tracked src files listed"; exit 2; }
# shellcheck disable=SC2086
git diff --exit-code "$BASE" -- $FILES mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile \
    mcp-server/Dockerfile.dev .github/workflows scripts atlas hosts > /dev/null \
    && echo "PASS every tracked src file except __main__.py and the five new modules, pyproject, uv.lock, Dockerfiles, workflows, scripts, atlas, hosts: byte-identical vs $BASE" \
    || { echo "FAIL a gated path changed"; FAILED=1; }
for f in codegraph.py storage/__init__.py storage/evidence.py storage/snapshots.py indexing/__init__.py indexing/extraction_cache.py indexing/retrieval.py server.py config.py enforcement.py; do
    git diff --exit-code "$BASE" -- "$P/$f" > /dev/null \
        && echo "PASS $f byte-identical vs $BASE" || { echo "FAIL $f changed"; FAILED=1; }
done
for d in protocol query tools providers resolvers extractors; do
    git diff --exit-code "$BASE" -- "$P/$d" > /dev/null \
        && echo "PASS $d/ byte-identical vs $BASE" || { echo "FAIL $d/ changed"; FAILED=1; }
done
NAMES=$({ git diff --name-only "$BASE" -- mcp-server/src; git ls-files --others --exclude-standard -- mcp-server/src; } | grep -v __pycache__ | LC_ALL=C sort -u | tr '\n' ' ')
WANT=$(for f in $NEWF "$P/__main__.py"; do echo "$f"; done | LC_ALL=C sort | tr '\n' ' ')
[ "$NAMES" = "$WANT" ] && echo "PASS changed or new src files are exactly the six allowed ones" \
    || { echo "FAIL changed src files: $NAMES"; FAILED=1; }
for f in $NEWF; do [ -s "$f" ] || { echo "FAIL $f is missing or empty"; FAILED=1; }; done
NUM=$(git diff --numstat "$BASE" -- "$P/__main__.py" | awk '{d += $2; n++} END {print d + 0, n + 0}')
if [ "$NUM" = "0 1" ]; then echo "PASS __main__.py numstat: 0 deleted lines ($NUM)"; else echo "FAIL __main__.py numstat '$NUM'"; FAILED=1; fi
BADT=$({ git diff --name-only "$BASE" -- mcp-server/tests; git ls-files --others --exclude-standard mcp-server/tests | grep -v __pycache__; } \
    | grep -v -e '^mcp-server/tests/slices/test_s41_' -e '^mcp-server/tests/slices/test_s40_isolation.py$' || true)
[ -z "$BADT" ] && echo "PASS tests changes are only test_s41_*.py and test_s40_isolation.py" || { echo "FAIL other test changes: $BADT"; FAILED=1; }
EDITED=$(git diff --name-only "$BASE" -- mcp-server/tests | tr '\n' ' ')
[ "$EDITED" = "mcp-server/tests/slices/test_s40_isolation.py " ] || [ -z "$EDITED" ] && echo "PASS the only edited existing test is test_s40_isolation.py ($EDITED)" || { echo "FAIL tracked test files differ: $EDITED"; FAILED=1; }
S40N=$(git diff --numstat "$BASE" -- mcp-server/tests/slices/test_s40_isolation.py | awk '{print $1 "/" $2}')
case $S40N in "") echo "PASS test_s40_isolation.py not (yet) a tracked diff" ;; 4/3|3/3|1/1|2/2|3/2|4/2|2/3) echo "PASS test_s40_isolation.py numstat $S40N (the docs-set pin line only)" ;; *) echo "FAIL test_s40_isolation.py numstat $S40N"; FAILED=1 ;; esac
DOCS=$({ git diff --name-only "$BASE" -- docs; git ls-files --others --exclude-standard docs; } | LC_ALL=C sort -u | tr '\n' ' ')
[ "$DOCS" = "docs/operations/index-jobs-and-retention.md " ] && echo "PASS docs changes are exactly docs/operations/index-jobs-and-retention.md" || { echo "FAIL docs set: $DOCS"; FAILED=1; }
CH=$({ git diff --name-only "$BASE"; git ls-files --others --exclude-standard; } | grep -v -e '^mcp-server/src/' -e '^mcp-server/tests/' -e '^docs/operations/' -e '^\.spectra/' | LC_ALL=C sort -u || true)
[ "$(echo "$CH" | tr '\n' ' ')" = "CHANGELOG.md README.md " ] && echo "PASS other changes are exactly CHANGELOG.md and README.md" \
    || { echo "FAIL other changes: $CH"; FAILED=1; }
RD=$(git diff --numstat "$BASE" -- CHANGELOG.md README.md | awk '{d += $2; n++} END {print d + 0, n + 0}')
[ "$RD" = "0 2" ] && echo "PASS CHANGELOG.md and README.md: 0 deleted lines" || { echo "FAIL root docs numstat '$RD'"; FAILED=1; }
[ -f .gitattributes ] && { echo "FAIL .gitattributes present"; FAILED=1; } || echo "PASS no .gitattributes"
echo "== g-src exit $FAILED"
echo "$FAILED" > "$HERE/g-src.exit"
exit "$FAILED"
