#!/usr/bin/env bash
# scripts/resolve-probe-artifact.sh
#
# Locates exactly one artefact matching <basename> under .spectra/changes/.
#
# WHY THIS EXISTS: .github/workflows/harden-gate.yml used to hardcode
#   .spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.md
#   .spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.json
# The ESL `archive` verb MOVES (never copies) a change folder to
# .spectra/changes/archive/<date>-<change-id>/ once a change reaches its
# lifecycle terminus (verified, drift_checked) -- so archiving
# `aci-v2-harden-and-augment` would silently break the gate on the very
# next PR that touches an A3 path, with no error, just two `-f` tests
# quietly evaluating false forever.
#
# A HARDCODED PATH IS A PROXY. The actual invariant the gate needs is
# "exactly one valid probe record exists" -- not "the record lives at
# this specific, assumed location." This script enforces that directly:
# it searches the ENTIRE .spectra/changes/ subtree (which nests archive/
# underneath it, so one recursive search covers an active change folder
# AND an archived one without the caller needing to know which applies)
# and treats ambiguity as a hard error, never a first-match fallback.
# Zero matches (artefact missing/not yet recorded) and more than one
# match (e.g. a stale copy left behind by a bad archive, or two change
# folders that both shipped a probe under the same basename) are both
# failures -- silently picking the first `find` result would reintroduce
# exactly the "check validates what it was handed, not what's actually
# true" defect class this campaign's RETRO.md documents twenty-two times
# over.
#
# Usage: scripts/resolve-probe-artifact.sh <basename> [search-root]
#   basename    -- exact filename to locate (e.g. probe-lpa-vs-louvain.json)
#   search-root -- defaults to .spectra/changes (repo-root relative;
#                  callers running from repo root can omit this)
#
# On success: prints the single resolved path to stdout, exits 0.
# On failure: prints a one-line diagnostic to stdout (so a caller that
# captures this script's output via `$(...)` sees the reason even though
# stdout is normally only the resolved path), exits 1.

set -euo pipefail

BASENAME="${1:?usage: resolve-probe-artifact.sh <basename> [search-root]}"
SEARCH_ROOT="${2:-.spectra/changes}"

if [ ! -d "$SEARCH_ROOT" ]; then
    echo "no $BASENAME found -- search root '$SEARCH_ROOT' does not exist"
    exit 1
fi

matches=()
while IFS= read -r -d '' f; do
    matches+=("$f")
done < <(find "$SEARCH_ROOT" -type f -name "$BASENAME" -print0 | sort -z)

if [ "${#matches[@]}" -eq 0 ]; then
    echo "no $BASENAME found under $SEARCH_ROOT (checked active AND archive/ subtrees) -- exactly one is required"
    exit 1
fi

if [ "${#matches[@]}" -gt 1 ]; then
    echo "${#matches[@]} candidates for $BASENAME found under $SEARCH_ROOT -- ambiguous, exactly one is required: ${matches[*]}"
    exit 1
fi

echo "${matches[0]}"
