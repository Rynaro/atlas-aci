#!/bin/sh
# ACI-S35 production-image scenario. Usage: prod-scenario.sh [IMAGE]   (default atlas-aci:s35)
#
# Build the image first:  docker build -t atlas-aci:s35 mcp-server
#
# The fixture repository (the world of tests/fixtures/feedback/world.json, before and after
# states) and its snapshots are built INSIDE containers as the image user (`atlas`, uid 10001) on
# a named volume that user owns, so git sees an owner that matches the process: NO safe.directory
# override exists anywhere. A real retrieval record is produced by the image's own MCP code path
# (build_server_state + call_tool_result; helper `record`). Every analysis container runs with
# --network none and --read-only; the volume (repository and graph roots) is mounted read-only
# for the feedback commands and only /out is writable. /tmp is never replaced by a tmpfs.
# Exit 0 only when every check passes. The volume and the scratch directory are removed at the
# end unless KEEP=1.
#
# Expected (all PASS lines): two imports give byte-identical stores and stdout (2 events, 1
# annotation); a forged success claim exits 1 with the exact text, empty stdout and no output;
# feedback-rank without --overlay lists the record's bundle order; with --overlay the passed
# bundle moves up one slot with the supporting event id; after editing a referenced source and
# re-indexing, every event is excluded as source_fingerprint_mismatch and the order is the
# baseline; usage errors exit 2; the repository (.git included) and graph inventories never
# change across the feedback commands; the tools JSON pin holds.
set -u

IMAGE=${1:-atlas-aci:s35}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
OUT=$(mktemp -d "${TMPDIR:-/tmp}/s35-prod.XXXXXX")
chmod 777 "$OUT"
VOL="s35-prod-$$"
FAILED=0
PINNED_SHA=94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56
PINNED_BYTES=1631472
POLICY=atlas-feedback-ranking-policy.v1

say() { printf '%s\n' "$*"; }
expect() { # label actual expected
    if [ "$2" = "$3" ]; then
        say "PASS  $1 = $2"
    else
        say "FAIL  $1: got '$2', expected '$3'"
        FAILED=1
    fi
}
sha_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
    else shasum -a 256 | cut -d' ' -f1; fi
}
same() { # two files byte-identical?
    if command -v cmp >/dev/null 2>&1; then cmp -s "$1" "$2" && echo same || echo differ
    else [ "$(sha_of <"$1")" = "$(sha_of <"$2")" ] && echo same || echo differ; fi
}
size_of() { wc -c <"$1" | tr -d ' '; }
# The image's python helper as the image user: on the volume (read-write) ...
helper() { docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$VOL":/w "$IMAGE" /ph.py "$@"; }
# ... or on the host files of /out (read-only) ...
pick() { docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$OUT":/out:ro "$IMAGE" \
    /ph.py "$@"; }
# ... or reading the volume read-only and writing /out (the record through the MCP code path).
make_record() { docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$VOL":/w:ro -v "$OUT":/out \
    "$IMAGE" /ph.py record /w/repo "$1" /out/record.json; }
make_inputs() { # extra arguments to the envelope helper
    docker run --rm --network none --read-only --entrypoint python \
        -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$OUT":/out "$IMAGE" \
        /ph.py "$@"; }
# The feedback commands: repository and graph roots read-only, only /out writable.
analyse() {
    docker run --rm --network none --read-only -v "$VOL":/w:ro -v "$OUT":/out "$IMAGE" "$@"
}
inventory() { # mode, size, mtime and sha256 of every path of the volume (repo incl. .git, graphs)
    docker run --rm --network none --read-only --entrypoint sh -v "$VOL":/w:ro "$IMAGE" -c \
        'cd /w && find . -print0 | sort -z | xargs -0 stat -c "%n %F %s %a %y" \
         && find . -type f -print0 | sort -z | xargs -0 sha256sum'
}
index() { # graph name
    docker run --rm --network none --read-only -v "$VOL":/w "$IMAGE" index --repo /w/repo \
        --graph-root "/w/graphs/$1" >/dev/null 2>&1
}
cleanup() {
    if [ "${KEEP:-0}" != 1 ]; then docker volume rm -f "$VOL" >/dev/null 2>&1; rm -rf "$OUT"; fi
}
trap cleanup EXIT

say "image: $IMAGE  volume: $VOL  out: $OUT"
say "user: $(docker run --rm --entrypoint id "$IMAGE")"

# ---- the world and one real record, built inside containers as the image user ------------------
docker volume create "$VOL" >/dev/null
UIDGID=$(docker run --rm --entrypoint id "$IMAGE" -u):$(docker run --rm --entrypoint id "$IMAGE" -g)
docker run --rm -u 0:0 --entrypoint chown -v "$VOL":/w "$IMAGE" "$UIDGID" /w
helper world /w/repo before || { say "FAIL  world before"; exit 1; }
index before; expect "index before exit" "$?" 0
make_record /w/graphs/before; expect "record through the MCP code path exit" "$?" 0
cp "$REPO/mcp-server/tests/fixtures/feedback/junit-outcomes.xml" "$OUT/artifact.xml"
make_inputs request /out/request.json
make_inputs envelope /out/record.json /out/artifact.xml /out/envelope.json
make_inputs envelope /out/record.json /out/artifact.xml /out/envelope-forged.json forged
expect "record bundle count" "$(pick order /out/record.json record | tr ',' '\n' | wc -l | tr -d ' ')" 6
expect "record collection_stop" "$(pick get /out/record.json data collection_stop)" None

GRAPH="--repo /w/repo --graph-root /w/graphs/before"
IMPORT="feedback-import $GRAPH --envelope /out/envelope.json --retrieval-record /out/record.json --artifact /out/artifact.xml"
RANK="feedback-rank $GRAPH --request /out/request.json"

# ---- import twice: byte-identical store and stdout ---------------------------------------------
inventory >"$OUT/inv.before"
analyse $IMPORT --output /out/store1.json >"$OUT/import1.stdout" 2>"$OUT/import1.err"; EX1=$?
analyse $IMPORT --output /out/store2.json >"$OUT/import2.stdout" 2>"$OUT/import2.err"; EX2=$?
expect "feedback-import exit (first)" "$EX1" 0
expect "feedback-import exit (second)" "$EX2" 0
expect "store bytes identical across two imports" "$(same "$OUT/store1.json" "$OUT/store2.json")" same
expect "summary stdout identical" "$(same "$OUT/import1.stdout" "$OUT/import2.stdout")" same
expect "summary command" "$(pick get /out/import1.stdout command)" feedback-import
expect "summary events" "$(pick get /out/import1.stdout counts events)" 2
expect "summary annotations" "$(pick get /out/import1.stdout counts annotations)" 1
expect "summary new events" "$(pick get /out/import1.stdout counts new_events)" 2
expect "store format" "$(pick get /out/store1.json format)" atlas-feedback-events.v1
analyse $IMPORT --store /out/store1.json --output /out/store3.json >"$OUT/import3.stdout" \
    2>"$OUT/import3.err"; EX3=$?
expect "re-import over the store exit" "$EX3" 0
expect "re-import store is byte-identical" "$(same "$OUT/store1.json" "$OUT/store3.json")" same
expect "re-import duplicate events" "$(pick get /out/import3.stdout counts duplicate_events)" 2
expect "re-import new events" "$(pick get /out/import3.stdout counts new_events)" 0

# ---- a forged success claim: exit 1, empty stdout, nothing written -----------------------------
analyse feedback-import $GRAPH --envelope /out/envelope-forged.json \
    --retrieval-record /out/record.json --artifact /out/artifact.xml --output /out/never.json \
    >"$OUT/forged.stdout" 2>"$OUT/forged.err"; EXF=$?
expect "forged claim exit" "$EXF" 1
expect "forged claim stdout bytes" "$(size_of "$OUT/forged.stdout")" 0
expect "forged claim text" "$(cat "$OUT/forged.err")" \
    "Error: Feedback import rejected: unverifiable_outcome: claim_disagrees_with_artifact"
expect "no output written by a rejection" \
    "$([ -e "$OUT/never.json" ] && echo exists || echo absent)" absent
analyse feedback-import $GRAPH --envelope /out/envelope.json --retrieval-record /out/record.json \
    --output /out/never2.json >"$OUT/noart.stdout" 2>"$OUT/noart.err"; EXN=$?
expect "no artifact exit" "$EXN" 1
expect "no artifact text" "$(cat "$OUT/noart.err")" \
    "Error: Feedback import rejected: unverifiable_outcome: missing_provenance"

# ---- ranking: without --overlay it is the baseline; with it, explained adjustments -------------
analyse $RANK >"$OUT/rank-off.json" 2>"$OUT/rank-off.err"; EXR=$?
expect "feedback-rank (no overlay) exit" "$EXR" 0
RECORD_ORDER=$(pick order /out/record.json record)
expect "disabled ranking equals the record's bundle order" \
    "$(pick order /out/rank-off.json ranking)" "$RECORD_ORDER"
expect "disabled baseline equals the record's bundle order" \
    "$(pick order /out/rank-off.json baseline)" "$RECORD_ORDER"
expect "disabled overlay flag" "$(pick get /out/rank-off.json overlay enabled)" False
expect "disabled adjustments" "$(pick adjustments /out/rank-off.json | wc -l | tr -d ' ')" 0
expect "disabled baseline record id equals the record's" \
    "$(pick get /out/rank-off.json baseline retrieval_record_id)" \
    "$(pick get /out/record.json data retrieval_record_id)"
analyse $RANK --overlay $POLICY --store /out/store1.json >"$OUT/rank-on.json" \
    2>"$OUT/rank-on.err"; EXO=$?
expect "feedback-rank (overlay) exit" "$EXO" 0
PASSED_ID=$(pick events /out/store1.json | tr ' ' '\n' | sed -n 's/^passed://p')
expect "overlay policy" "$(pick get /out/rank-on.json overlay policy_name)" "$POLICY"
expect "adjustment lists the supporting event" "$(pick adjustments /out/rank-on.json)" \
    "5->4:1:$PASSED_ID"
expect "failed event is active and not scored" \
    "$(pick get /out/rank-on.json counts active)/$(pick get /out/rank-on.json counts adjusted)" 2/1
expect "overlay annotations are counted, never scored" \
    "$(pick get /out/rank-on.json annotations count)/$(pick get /out/rank-on.json annotations scored)" 1/0

# ---- usage: exit 2, empty stdout --------------------------------------------------------------
analyse $RANK --store /out/store1.json >"$OUT/u1.out" 2>"$OUT/u1.err"; EXU=$?
expect "usage exit" "$EXU" 2
expect "usage stdout bytes" "$(size_of "$OUT/u1.out")" 0
expect "usage text" "$(tail -n 1 "$OUT/u1.err")" "Error: --store requires --overlay."
analyse $IMPORT --output /w/repo/inside.json >"$OUT/u2.out" 2>"$OUT/u2.err"; EXI=$?
expect "output inside the repository exit" "$EXI" 2
expect "output inside the repository text" "$(tail -n 1 "$OUT/u2.err")" \
    "Error: --output must be outside the repository and graph root."
analyse $RANK --max-events 0 >"$OUT/u3.out" 2>"$OUT/u3.err"; EXL=$?
expect "limit usage exit" "$EXL" 2
expect "limit usage text" "$(tail -n 1 "$OUT/u3.err")" "Error: Invalid feedback option: max_events"

# ---- nothing of the repository or the graphs changed across the feedback commands --------------
inventory >"$OUT/inv.after"
expect "repository (.git included) and graph inventory unchanged" \
    "$(same "$OUT/inv.before" "$OUT/inv.after")" same
expect "no temporary store file remains" \
    "$(find "$OUT" -name '.atlas-feedback-*' | wc -l | tr -d ' ')" 0

# ---- edit a referenced source and re-index: the events are stale -------------------------------
helper world /w/repo after || { say "FAIL  world after"; exit 1; }
index after; expect "index after exit" "$?" 0
inventory >"$OUT/inv.stale.before"
analyse feedback-rank --repo /w/repo --graph-root /w/graphs/after --request /out/request.json \
    --overlay $POLICY --store /out/store1.json >"$OUT/rank-stale.json" 2>"$OUT/rank-stale.err"
expect "stale ranking exit" "$?" 0
expect "stale events are excluded as" "$(pick reasons /out/rank-stale.json)" \
    source_fingerprint_mismatch
expect "stale excluded count" "$(pick get /out/rank-stale.json counts excluded)" 2
expect "stale active count" "$(pick get /out/rank-stale.json counts active)" 0
expect "stale adjustments" "$(pick adjustments /out/rank-stale.json | wc -l | tr -d ' ')" 0
expect "stale order equals the baseline" "$(pick order /out/rank-stale.json ranking)" \
    "$(pick order /out/rank-stale.json baseline)"
analyse feedback-rank --repo /w/repo --graph-root /w/graphs/before --request /out/request.json \
    --overlay $POLICY --store /out/store1.json >"$OUT/rank-old.json" 2>"$OUT/rank-old.err"
expect "the earlier graph root still has active events" \
    "$(pick get /out/rank-old.json counts active)" 2
inventory >"$OUT/inv.stale.after"
expect "inventory unchanged across the stale ranking" \
    "$(same "$OUT/inv.stale.before" "$OUT/inv.stale.after")" same

# ---- the tool manifest pin ----------------------------------------------------------------------
docker run --rm --network none --read-only -v "$VOL":/w:ro "$IMAGE" tools --repo /w/repo \
    >"$OUT/tools.json" 2>/dev/null
expect "tools JSON sha256" "$(sha_of <"$OUT/tools.json")" "$PINNED_SHA"
expect "tools JSON bytes" "$(size_of "$OUT/tools.json")" "$PINNED_BYTES"

if [ "$FAILED" -eq 0 ]; then say "RESULT: all checks passed"; else say "RESULT: FAILED"; fi
exit "$FAILED"
