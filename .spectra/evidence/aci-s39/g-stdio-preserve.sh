#!/bin/sh
# ACI-S39 G-STDIO-PRESERVE: the S38 golden transcripts still match on the branch.
# Captures the branch tree twice (seeds 0 and 4242, the same fixture path as the base capture, since
# ids in frames.json and inprocess.json derive from the fixture path), then compares with
# preserve/base (captured from `git archive 8ab6582`, see qualification.json b_c_m):
#   transcripts/*  == committed tests/fixtures/s38/transcripts/* == preserve/base/transcripts/*  (cmp)
#   frames.json["frames"], ac004.json and inprocess.json (identity excluded) equal to base, for both seeds
# Writes g-stdio-preserve.log.gz / .exit. Needs jq on the host.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PRE=$HERE/preserve
mkdir -p "$PRE/after" "$PRE/after-4242"
FAILED=0
{
    for pair in "after:0" "after-4242:4242"; do
        label=${pair%%:*}; seed=${pair##*:}
        "$HERE/dk39.sh" sh -c "/opt/venv/bin/python tests/slices/test_s38_support.py capture --out /tmp/cap --fixture /tmp/fx0 --label after --seed $seed > /dev/null 2>/tmp/cap.err || { tail -5 /tmp/cap.err >&2; exit 1; }; tar -C /tmp/cap -cf - ." | tar -x -C "$PRE/$label" \
            || { echo "FAIL capture $label"; FAILED=1; }
        for f in ac004.json case-sha256.json frames.json inprocess.json; do [ -s "$PRE/$label/$f" ] || { echo "FAIL missing $label/$f"; FAILED=1; }; done
    done
    for label in after after-4242; do
        if diff -r "$PRE/base/transcripts" "$PRE/$label/transcripts" > /dev/null \
            && diff -r "$REPO/mcp-server/tests/fixtures/s38/transcripts" "$PRE/$label/transcripts" > /dev/null \
            && [ "$(ls "$PRE/$label/transcripts" | wc -l | tr -d ' ')" = 8 ]; then
            echo "PASS $label: the 8 normalised transcripts equal preserve/base and the committed goldens"
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
    echo "== g-stdio-preserve exit $FAILED"
    echo "$FAILED" > "$HERE/g-stdio-preserve.exit"
} 2>&1 | gzip -9n > "$HERE/g-stdio-preserve.log.gz"
echo "g-stdio-preserve exit $(cat "$HERE/g-stdio-preserve.exit")"
