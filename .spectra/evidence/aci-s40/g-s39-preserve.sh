#!/bin/sh
# ACI-S40 G-S39-PRESERVE: the S38 stdio goldens still match on the branch, and the S39 suite and
# test_s38_contract.py are green (run separately by run-gates.sh regression).
# Captures the BASE tree (git archive of f4f9f9f) and the branch tree, seeds 0 and 4242, with the
# same fixture path (/tmp/fx0), then compares:
#   transcripts/*  == committed tests/fixtures/s38/transcripts/* == the base capture   (diff -r)
#   frames.json["frames"], ac004.json, case-sha256.json and inprocess.json (identity excluded)
#   equal to the base capture, for both seeds
# Writes g-s39-preserve.log.gz / .exit. Needs jq on the host.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
SCRATCH=${S40_SCRATCH:-/private/tmp/claude-501/-Users-henrique-workspace-oss-agents-atlas-aci/694bbcf4-4624-4586-978b-245709fb6cab/scratchpad/s40}
PRE=$HERE/preserve
rm -rf "$PRE"
mkdir -p "$PRE/base" "$PRE/after" "$PRE/after-4242"
FAILED=0
{
    docker run --rm --name "s40-preserve-base-$$" --user 1000:1000 -e HOME=/hc -e PYTHONPATH=/base/mcp-server/src \
        -v "$SCRATCH/home":/hc -v "$SCRATCH/base":/base -w /base/mcp-server atlas-aci-dev-relock sh -c "/opt/venv/bin/python tests/slices/test_s38_support.py capture --out /tmp/cap --fixture /tmp/fx0 --label base --seed 0 > /dev/null 2>/tmp/cap.err || { tail -5 /tmp/cap.err >&2; exit 1; }; tar -C /tmp/cap -cf - ." | tar -x -C "$PRE/base" \
        || { echo "FAIL capture base"; FAILED=1; }
    for pair in "after:0" "after-4242:4242"; do
        label=${pair%%:*}; seed=${pair##*:}
        "$HERE/dk40.sh" sh -c "/opt/venv/bin/python tests/slices/test_s38_support.py capture --out /tmp/cap --fixture /tmp/fx0 --label after --seed $seed > /dev/null 2>/tmp/cap.err || { tail -5 /tmp/cap.err >&2; exit 1; }; tar -C /tmp/cap -cf - ." | tar -x -C "$PRE/$label" \
            || { echo "FAIL capture $label"; FAILED=1; }
    done
    for label in base after after-4242; do
        for f in ac004.json case-sha256.json frames.json inprocess.json; do [ -s "$PRE/$label/$f" ] || { echo "FAIL missing $label/$f"; FAILED=1; }; done
    done
    for label in after after-4242; do
        if diff -r "$PRE/base/transcripts" "$PRE/$label/transcripts" > /dev/null \
            && diff -r "$REPO/mcp-server/tests/fixtures/s38/transcripts" "$PRE/$label/transcripts" > /dev/null \
            && [ "$(ls "$PRE/$label/transcripts" | wc -l | tr -d ' ')" = 8 ]; then
            echo "PASS $label: the 8 normalised transcripts equal the base capture and the committed goldens"
        else echo "FAIL $label: transcripts differ"; FAILED=1; fi
        cmp -s "$PRE/base/ac004.json" "$PRE/$label/ac004.json" && echo "PASS $label: ac004.json equals base" || { echo "FAIL $label: ac004.json differs"; FAILED=1; }
        cmp -s "$PRE/base/case-sha256.json" "$PRE/$label/case-sha256.json" && echo "PASS $label: case-sha256.json equals base" || { echo "FAIL $label: case-sha256.json differs"; FAILED=1; }
        if [ "$(jq -S .frames "$PRE/base/frames.json" | shasum -a 256)" = "$(jq -S .frames "$PRE/$label/frames.json" | shasum -a 256)" ] && [ "$(jq '.frames|length' "$PRE/$label/frames.json")" -ge 19 ]; then
            echo "PASS $label: frames.json[frames] equals base ($(jq '.frames|length' "$PRE/$label/frames.json") entries)"
        else echo "FAIL $label: frames.json[frames] differs"; FAILED=1; fi
        if [ "$(jq -S 'del(.identity)' "$PRE/base/inprocess.json" | shasum -a 256)" = "$(jq -S 'del(.identity)' "$PRE/$label/inprocess.json" | shasum -a 256)" ] && [ "$(jq '.manifests|length' "$PRE/$label/inprocess.json")" -ge 7 ]; then
            echo "PASS $label: inprocess.json (identity excluded) equals base"
        else echo "FAIL $label: inprocess.json differs"; FAILED=1; fi
        IB=$(jq -r '.identity.atlas_aci_file' "$PRE/base/frames.json"); IA=$(jq -r '.identity.atlas_aci_file' "$PRE/$label/frames.json")
        if [ "$IB" != "$IA" ] && [ "$(jq -r .identity.label "$PRE/$label/frames.json")" = after ]; then echo "PASS $label: identity shows base tree ($IB) vs branch tree ($IA)"; else echo "FAIL $label: identity not distinct"; FAILED=1; fi
        SB=$(jq -r '.identity.server_py_sha256' "$PRE/base/frames.json"); SA=$(jq -r '.identity.server_py_sha256' "$PRE/$label/frames.json")
        [ "$SB" = "$SA" ] && [ -n "$SB" ] && echo "PASS $label: server.py sha256 is the same in both trees ($SA)" || { echo "FAIL $label: server.py sha differs"; FAILED=1; }
    done
    echo "== g-s39-preserve exit $FAILED"
    echo "$FAILED" > "$HERE/g-s39-preserve.exit"
} 2>&1 | gzip -9n > "$HERE/g-s39-preserve.log.gz"
echo "g-s39-preserve exit $(cat "$HERE/g-s39-preserve.exit")"
