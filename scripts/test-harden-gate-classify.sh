#!/usr/bin/env bash
# scripts/test-harden-gate-classify.sh
#
# Self-test for scripts/harden-gate-classify.sh — calls the EXACT SAME
# file .github/workflows/harden-gate.yml calls, never a copy-pasted
# re-implementation, so a pass here says something about what CI actually
# runs. Builds a disposable git repo with controlled diffs and asserts the
# classification each one produces.
#
# Covers both failure classes the checker found across two passes:
#   - false negative (evasion by renaming) — not testable here by design;
#     it is an accepted, documented limitation, not a regression to guard.
#   - false positive (prose about code trips the same grep as real code) —
#     scenarios 4 and 5 below are exactly this: a whole-line comment and an
#     inline trailing SQL comment, each mentioning a marker word, must not
#     trip either check. Scenario 6 proves the comment-stripping fix for
#     that didn't also swallow real code matches.
#
# Usage: scripts/test-harden-gate-classify.sh
# Exit 0 if every scenario matches its expected classification, 1 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLASSIFY="$SCRIPT_DIR/harden-gate-classify.sh"

TMP_REPO="$(mktemp -d)"
trap 'rm -rf "$TMP_REPO"' EXIT

cd "$TMP_REPO"
git init -q -b main
git config user.email "test@example.com"
git config user.name "harden-gate-classify self-test"

mkdir -p mcp-server/src/atlas_aci/tools mcp-server/tests

cat > mcp-server/src/atlas_aci/codegraph.py << 'PYEOF'
"""Code-graph backend (fixture)."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY);
"""


def noop():
    pass
PYEOF

echo '"""graph_query tool (fixture)."""' > mcp-server/src/atlas_aci/tools/graph_query.py
echo '"""search_symbol tool (fixture)."""' > mcp-server/src/atlas_aci/tools/search_symbol.py
echo '"""server (fixture)."""' > mcp-server/src/atlas_aci/server.py
# Tracked placeholder so mcp-server/tests/ survives every scenario
# checkout below (git does not track empty directories).
: > mcp-server/tests/.gitkeep

git add -A
git commit -q -m "base"
BASE_SHA="$(git rev-parse HEAD)"

pass_count=0
fail_count=0

_assert_classification() {
    local scenario="$1" expected_augmented="$2" expected_a3_touched="$3" head_sha="$4"
    local output augmented a3_touched
    output="$("$CLASSIFY" "$BASE_SHA" "$head_sha")"
    augmented="$(printf '%s\n' "$output" | sed -n 's/^augmented=//p')"
    a3_touched="$(printf '%s\n' "$output" | sed -n 's/^a3_touched=//p')"
    if [ "$augmented" = "$expected_augmented" ] && [ "$a3_touched" = "$expected_a3_touched" ]; then
        echo "PASS: $scenario (augmented=$augmented, a3_touched=$a3_touched)"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL: $scenario -- expected augmented=$expected_augmented a3_touched=$expected_a3_touched, got augmented=$augmented a3_touched=$a3_touched"
        fail_count=$((fail_count + 1))
    fi
}

# ---- Scenario 1: clean hardening-style diff — no augmentation, no A3 ----
git checkout -q -b scenario-clean "$BASE_SHA"
cat >> mcp-server/src/atlas_aci/codegraph.py << 'PYEOF'


def another_helper():
    """A perfectly ordinary hardening-style addition."""
    return 1
PYEOF
git commit -aqm "clean hardening change"
_assert_classification "clean hardening diff" false false "$(git rev-parse HEAD)"
git checkout -q "$BASE_SHA"
git branch -qD scenario-clean

# ---- Scenario 2: augmentation marker PATH added (test_confidence.py) ----
git checkout -q -b scenario-marker-path "$BASE_SHA"
echo '"""placeholder"""' > mcp-server/tests/test_confidence.py
git add -A
git commit -qm "add test_confidence.py"
_assert_classification "augmentation marker path (test_confidence.py)" true false "$(git rev-parse HEAD)"
git checkout -q "$BASE_SHA"
git branch -qD scenario-marker-path

# ---- Scenario 3: A3-exclusive path added, no probe artefact ----
# (test_communities.py is ALSO an augmentation marker path — both fire.)
git checkout -q -b scenario-a3-path "$BASE_SHA"
echo '"""placeholder"""' > mcp-server/tests/test_communities.py
git add -A
git commit -qm "add test_communities.py"
_assert_classification "A3-exclusive path (test_communities.py)" true true "$(git rev-parse HEAD)"
git checkout -q "$BASE_SHA"
git branch -qD scenario-a3-path

# ---- Scenario 4: whole-line comment mentioning marker words — must NOT trip either check ----
git checkout -q -b scenario-comment-only "$BASE_SHA"
cat >> mcp-server/src/atlas_aci/codegraph.py << 'PYEOF'

# NOTE: a comment explaining that "label_propagation" and "rationale_for"
# are marker strings must not itself trip the marker grep.
PYEOF
git commit -aqm "comment mentioning marker words"
_assert_classification "whole-line comment mentioning markers (Python #)" false false "$(git rev-parse HEAD)"
git checkout -q "$BASE_SHA"
git branch -qD scenario-comment-only

# ---- Scenario 5: inline SQL trailing comment mentioning a marker — must NOT trip either check ----
git checkout -q -b scenario-inline-sql-comment "$BASE_SHA"
cat >> mcp-server/src/atlas_aci/codegraph.py << 'PYEOF'

OTHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS rationale (
    target_path TEXT  -- rationale_for edge target
);
"""
PYEOF
git commit -aqm "DDL column with inline rationale_for comment"
_assert_classification "inline SQL trailing-comment marker mention" false false "$(git rev-parse HEAD)"
git checkout -q "$BASE_SHA"
git branch -qD scenario-inline-sql-comment

# ---- Scenario 6: a REAL code occurrence of a marker — must still fire ----
# (proves the comment-stripping fix did not also swallow genuine matches)
git checkout -q -b scenario-real-code-marker "$BASE_SHA"
cat >> mcp-server/src/atlas_aci/codegraph.py << 'PYEOF'

CONFIDENCE_EXTRACTED = "EXTRACTED"
PYEOF
git commit -aqm "real code marker occurrence"
_assert_classification "real code marker (EXTRACTED, no comment)" true false "$(git rev-parse HEAD)"
git checkout -q "$BASE_SHA"
git branch -qD scenario-real-code-marker

echo ""
echo "$pass_count passed, $fail_count failed"
if [ "$fail_count" -ne 0 ]; then
    exit 1
fi
