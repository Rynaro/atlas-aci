#!/usr/bin/env bash
# scripts/harden-gate-classify.sh
#
# Shared diff-classification logic for .github/workflows/harden-gate.yml
# AND its self-test (scripts/test-harden-gate-classify.sh). The workflow
# calls this script rather than re-implementing the logic inline, so the
# self-test proves something about what CI actually runs, not about a copy
# that could silently drift from it.
#
# Usage (run from inside the git repo being classified):
#   scripts/harden-gate-classify.sh <base-sha> <head-sha>
#
# Prints exactly two lines to stdout:
#   augmented=true|false
#   a3_touched=true|false
#
# KNOWN LIMITATION, VERIFIED (F-3, checker second pass) — evasion by
# renaming: the content-marker heuristic below is dodgeable by choosing
# different identifiers (import the confidence enum from a differently
# named module, call a table `call_edges`, name a test `test_edges.py`).
# Do not try to out-guess this with more markers ("marker whack-a-mole");
# it is not a fixable arms race. The structural defences that actually
# hold the line live in the pytest suite, not here: KNOWN_QUERY_VERBS
# reachability (mcp-server/src/atlas_aci/codegraph.py) and ci.yml's
# unconditional full-suite run on every PR — see harden-gate.yml's header
# comment for the full argument. This script's job is narrower and
# strictly weaker by design: it is a review trigger for "augmentation may
# have landed" plus the one thing ci.yml cannot check at all (A3's
# probe-before-code *sequencing*), not a soundness proof.
#
# SECOND KNOWN LIMITATION, FIXED HERE (checker third pass) — false
# positives from prose about code: a naive grep over added diff lines
# matches comments *documenting* a marker string as readily as the marker
# used in real code — including this very file's own limitation note
# above, and inline SQL comments on unrelated DDL columns (an unrelated
# rationale-table column comment containing the word "rationale_for"
# tripped the augmentation check; a comment explaining that
# "label_propagation" is a marker tripped the A3 check). Added lines are
# therefore reduced to their non-comment content before either regex is
# applied — a whole-line Python `#`/SQL `--` comment collapses to empty,
# and a trailing comment on a real code line is stripped down to just the
# code. This does NOT weaken the markers: a real occurrence in actual code
# or a string literal is completely untouched by this stripping; only
# comment text is ever removed.

set -euo pipefail

BASE_SHA="${1:?usage: harden-gate-classify.sh <base-sha> <head-sha>}"
HEAD_SHA="${2:?usage: harden-gate-classify.sh <base-sha> <head-sha>}"

# Files that can ONLY exist because an A1-A5 workstream landed — an exact,
# not heuristic, signal.
AUGMENTATION_MARKER_PATHS="mcp-server/src/atlas_aci/export.py
mcp-server/tests/test_confidence.py
mcp-server/tests/test_communities.py
mcp-server/tests/test_rationale.py
mcp-server/tests/test_export.py
mcp-server/tests/test_graph_query.py"

# Shared "hot files" touched by both hardening and every augmentation
# workstream (declared-scope.md) — a pure path match can't disambiguate,
# so these are only flagged via the content heuristic below, never by
# path alone.
HOT_FILES="mcp-server/src/atlas_aci/codegraph.py
mcp-server/src/atlas_aci/tools/graph_query.py
mcp-server/src/atlas_aci/tools/search_symbol.py
mcp-server/src/atlas_aci/server.py"

AUGMENTATION_CODE_MARKERS='EXTRACTED|INFERRED|AMBIGUOUS|god_node|label_propagation|rationale_for|CREATE TABLE IF NOT EXISTS edges'

# test_communities.py can ONLY exist because A3 landed — an exact signal,
# checked by path alone. codegraph.py / graph_query.py are shared hot
# files (H3/H4/A1/A2/A3 all touch them), so those two are only flagged via
# an A3-specific content marker actually added in the diff, never by path
# alone (that would false-positive on any PR that legitimately edits them
# for H3/H4 — this very P0 branch, for instance).
A3_EXCLUSIVE_PATH="mcp-server/tests/test_communities.py"
A3_HOT_FILES="mcp-server/src/atlas_aci/codegraph.py
mcp-server/src/atlas_aci/tools/graph_query.py"
A3_CODE_MARKERS='label_propagation|community_id|LPA|lpa_communities'

# Added (`+`) lines from one file's diff, with the `+++ b/...` header
# dropped, the leading `+` and its following whitespace stripped, and any
# Python `#` / SQL `--` comment — whole-line or trailing — removed. A line
# that is nothing but a comment collapses to the empty string and can
# never match a marker; a code line's own content is untouched.
_added_code_only() {
    local base="$1" head="$2" path="$3"
    git diff "$base" "$head" -- "$path" \
        | grep -E '^\+' \
        | grep -vE '^\+\+\+' \
        | sed -E 's/^\+[[:space:]]*//' \
        | sed -E 's/[[:space:]]*(#|--).*$//'
}

changed_files="$(git diff --name-only "$BASE_SHA" "$HEAD_SHA")"

augmented=false
while IFS= read -r f; do
    [ -z "$f" ] && continue
    if grep -qxF "$f" <<< "$AUGMENTATION_MARKER_PATHS"; then
        augmented=true
    elif grep -qxF "$f" <<< "$HOT_FILES"; then
        if _added_code_only "$BASE_SHA" "$HEAD_SHA" "$f" | grep -qE "$AUGMENTATION_CODE_MARKERS"; then
            augmented=true
        fi
    fi
done <<< "$changed_files"

a3_touched=false
while IFS= read -r f; do
    [ -z "$f" ] && continue
    if [ "$f" = "$A3_EXCLUSIVE_PATH" ]; then
        a3_touched=true
    elif grep -qxF "$f" <<< "$A3_HOT_FILES"; then
        if _added_code_only "$BASE_SHA" "$HEAD_SHA" "$f" | grep -qE "$A3_CODE_MARKERS"; then
            a3_touched=true
        fi
    fi
done <<< "$changed_files"

echo "augmented=$augmented"
echo "a3_touched=$a3_touched"
