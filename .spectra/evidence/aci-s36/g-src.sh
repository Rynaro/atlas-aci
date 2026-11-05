#!/bin/sh
# ACI-S36 G-SRC: the gated paths are byte-unchanged against the base commit; every pre-existing
# scripts/ file is byte-unchanged; no untracked file under mcp-server/src; only the two new
# scripts are added under scripts/; new test files are only test_s36_* and fixtures/community_experiment.
set -u
BASE=53032fe6f2ddb48ce3ed0c1891a168106d578714
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILED=0
GATED="mcp-server/src mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows atlas/evals mcp-server/tests/fixtures/s18 .spectra/changes/aci-s17"
# shellcheck disable=SC2086
git diff --exit-code "$BASE" -- $GATED > /dev/null && echo "PASS gated paths byte-unchanged vs $BASE" \
    || { echo "FAIL gated paths changed"; git diff --stat "$BASE" -- $GATED; FAILED=1; }
# shellcheck disable=SC2086
U=$(git ls-files --others --exclude-standard -- $GATED | grep -v __pycache__)
[ -z "$U" ] && echo "PASS no untracked file under a gated path (incl. mcp-server/src)" \
    || { echo "FAIL untracked under gated paths:"; echo "$U"; FAILED=1; }
git diff --exit-code "$BASE" -- scripts > /dev/null && echo "PASS every tracked scripts/ file byte-unchanged" \
    || { echo "FAIL a tracked scripts/ file changed"; git diff --stat "$BASE" -- scripts; FAILED=1; }
ADDED=$(git ls-files --others --exclude-standard -- scripts | grep -v __pycache__ | sort)
WANT=$(printf '%s\n%s\n' scripts/experiment-leiden-vs-lpa.py scripts/verify-community-experiment.py)
[ "$ADDED" = "$WANT" ] && echo "PASS only the two new scripts were added under scripts/" \
    || { echo "FAIL scripts/ additions: $ADDED"; FAILED=1; }
TESTS=$(git diff --name-only "$BASE" -- mcp-server/tests)
[ -z "$TESTS" ] && echo "PASS no tracked test file modified" || { echo "FAIL tracked tests modified: $TESTS"; FAILED=1; }
NEWTESTS=$(git ls-files --others --exclude-standard mcp-server/tests | grep -v __pycache__ \
    | grep -v -e '^mcp-server/tests/slices/test_s36_' -e '^mcp-server/tests/fixtures/community_experiment/')
[ -z "$NEWTESTS" ] && echo "PASS new test files are only test_s36_* and fixtures/community_experiment" \
    || { echo "FAIL other new test files: $NEWTESTS"; FAILED=1; }
CH=$(git diff --name-only "$BASE")
for f in $CH; do
    case $f in
        CHANGELOG.md|README.md|.spectra/backlog/execution.json|.spectra/backlog/order.md|.spectra/plans/ramza-calibration.jsonl) ;;
        *) echo "FAIL unexpected tracked change: $f"; FAILED=1 ;;
    esac
done
echo "PASS tracked changes limited to the declared files: $(echo "$CH" | tr '\n' ' ')"
for n in aci-s17-probe-lpa-vs-louvain; do
    C=$(find . -name "$n.*" -not -path './.git/*' | wc -l | tr -d ' ')
    [ "$C" = 2 ] && echo "PASS exactly the two existing $n.* files" || { echo "FAIL $n.* count $C"; FAILED=1; }
done
exit "$FAILED"
