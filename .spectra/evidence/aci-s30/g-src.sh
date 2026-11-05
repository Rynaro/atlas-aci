#!/bin/sh
# G-SRC: gated paths byte-unchanged vs dc57096, only the four allowed src files changed, no
# tracked test edited. Run from anywhere; writes g-src.log.gz and g-src.exit next to this file.
E=$(cd "$(dirname "$0")" && pwd)
cd "$E/../../.." || exit 99
B=dc57096
A=mcp-server/src/atlas_aci
(
    echo "# gate: g-src (base $B)"
    FAIL=0
    echo "## git diff --exit-code over the gated paths"
    git diff --exit-code "$B" -- \
        $A/server.py $A/enforcement.py $A/config.py $A/codegraph.py $A/protocol $A/query \
        $A/tools $A/storage $A/extractors $A/providers $A/indexing $A/resolvers $A/frameworks \
        $A/architecture $A/counterfactual $A/contracts $A/importers $A/overlays \
        $A/dependencies $A/reports mcp-server/pyproject.toml mcp-server/uv.lock \
        mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows || FAIL=1
    echo "## changed and untracked files under mcp-server/src (allowed: 4)"
    {
        git diff --name-only "$B" -- mcp-server/src
        git ls-files --others --exclude-standard -- mcp-server/src
    } | sort -u >"$E/.changed-src.txt"
    cat "$E/.changed-src.txt"
    printf '%s\n' $A/__main__.py $A/cross_repo/__init__.py $A/cross_repo/package_links.py \
        $A/cross_repo/revision_vector.py | sort >"$E/.allowed-src.txt"
    comm -23 "$E/.changed-src.txt" "$E/.allowed-src.txt" >"$E/.extra-src.txt"
    if [ -s "$E/.extra-src.txt" ]; then echo "UNEXPECTED:"; cat "$E/.extra-src.txt"; FAIL=1; fi
    echo "## tracked test files modified vs $B (must be none)"
    git diff --name-only "$B" -- mcp-server/tests | tee "$E/.tracked-tests.txt"
    [ -s "$E/.tracked-tests.txt" ] && FAIL=1
    echo "## untracked test files (allowed: test_s30_*.py and fixtures/cross_repo/*)"
    git ls-files --others --exclude-standard -- mcp-server/tests | tee "$E/.untracked-tests.txt"
    grep -v -e '^mcp-server/tests/slices/test_s30_[a-z_]*\.py$' \
        -e '^mcp-server/tests/fixtures/cross_repo/[a-z.-]*\.json$' "$E/.untracked-tests.txt" \
        >"$E/.extra-tests.txt"
    if [ -s "$E/.extra-tests.txt" ]; then echo "UNEXPECTED:"; cat "$E/.extra-tests.txt"; FAIL=1; fi
    echo "## other tracked files changed vs $B"
    git diff --name-only "$B" | grep -v -e '^\.spectra/' -e '^mcp-server/src/' || true
    echo "# exit: $FAIL"
    exit "$FAIL"
) >"$E/g-src.log.tmp" 2>&1
CODE=$?
gzip -c "$E/g-src.log.tmp" >"$E/g-src.log.gz"
rm -f "$E"/.changed-src.txt "$E"/.allowed-src.txt "$E"/.extra-src.txt "$E"/.tracked-tests.txt \
    "$E"/.untracked-tests.txt "$E"/.extra-tests.txt "$E/g-src.log.tmp"
echo "$CODE" >"$E/g-src.exit"
echo "g-src exit=$CODE"
