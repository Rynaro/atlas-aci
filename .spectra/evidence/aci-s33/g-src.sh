#!/bin/sh
# G-SRC: gated paths byte-unchanged vs 890e181, only the three allowed src files changed, no
# tracked test edited. Writes g-src.log.gz and g-src.exit next to this file.
E=$(cd "$(dirname "$0")" && pwd)
cd "$E/../../.." || exit 99
B=890e181
A=mcp-server/src/atlas_aci
(
    echo "# gate: g-src (base $B)"
    FAIL=0
    echo "## git diff --exit-code over the gated paths"
    git diff --exit-code "$B" -- \
        $A/server.py $A/enforcement.py $A/config.py $A/codegraph.py $A/protocol $A/query \
        $A/tools $A/storage $A/extractors $A/providers $A/indexing $A/resolvers $A/frameworks \
        $A/architecture $A/counterfactual $A/contracts $A/importers $A/overlays \
        $A/dependencies $A/cross_repo $A/reports mcp-server/pyproject.toml mcp-server/uv.lock \
        mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows || FAIL=1
    echo "## changed and untracked files under mcp-server/src (allowed: 3)"
    {
        git diff --name-only "$B" -- mcp-server/src
        git ls-files --others --exclude-standard -- mcp-server/src
    } | sort -u >"$E/.changed-src.txt"
    cat "$E/.changed-src.txt"
    printf '%s\n' $A/__main__.py $A/history/__init__.py $A/history/cochange.py | sort \
        >"$E/.allowed-src.txt"
    comm -23 "$E/.changed-src.txt" "$E/.allowed-src.txt" >"$E/.extra-src.txt"
    if [ -s "$E/.extra-src.txt" ]; then echo "UNEXPECTED:"; cat "$E/.extra-src.txt"; FAIL=1; fi
    echo "## tracked test files modified vs $B (must be none)"
    git diff --name-only "$B" -- mcp-server/tests | tee "$E/.tracked-tests.txt"
    [ -s "$E/.tracked-tests.txt" ] && FAIL=1
    echo "## untracked test files (allowed: test_s33_*.py and fixtures/cochange/*.json)"
    git ls-files --others --exclude-standard -- mcp-server/tests | tee "$E/.untracked-tests.txt"
    grep -v -e '^mcp-server/tests/slices/test_s33_[a-z_]*\.py$' \
        -e '^mcp-server/tests/fixtures/cochange/[a-z.-]*\.json$' "$E/.untracked-tests.txt" \
        >"$E/.extra-tests.txt"
    if [ -s "$E/.extra-tests.txt" ]; then echo "UNEXPECTED:"; cat "$E/.extra-tests.txt"; FAIL=1; fi
    echo "## other tracked files changed vs $B (allowed: CHANGELOG.md README.md .spectra/*)"
    git diff --name-only "$B" | grep -v -e '^\.spectra/' -e '^mcp-server/src/' || true
    OTHER=$(git diff --name-only "$B" | grep -v -e '^\.spectra/' -e '^mcp-server/src/' -e '^CHANGELOG.md$' -e '^README.md$')
    if [ -n "$OTHER" ]; then echo "UNEXPECTED: $OTHER"; FAIL=1; fi
    echo "## untracked files outside mcp-server and .spectra (allowed: docs/history-signals.md)"
    git ls-files --others --exclude-standard | grep -v -e '^\.spectra/' -e '^mcp-server/' || true
    EXTRA=$(git ls-files --others --exclude-standard | grep -v -e '^\.spectra/' -e '^mcp-server/' -e '^docs/history-signals.md$')
    if [ -n "$EXTRA" ]; then echo "UNEXPECTED: $EXTRA"; FAIL=1; fi
    echo "# exit: $FAIL"
    exit "$FAIL"
) >"$E/g-src.log.tmp" 2>&1
CODE=$?
gzip -c "$E/g-src.log.tmp" >"$E/g-src.log.gz"
rm -f "$E"/.changed-src.txt "$E"/.allowed-src.txt "$E"/.extra-src.txt "$E"/.tracked-tests.txt \
    "$E"/.untracked-tests.txt "$E"/.extra-tests.txt "$E/g-src.log.tmp"
echo "$CODE" >"$E/g-src.exit"
echo "g-src exit=$CODE"
