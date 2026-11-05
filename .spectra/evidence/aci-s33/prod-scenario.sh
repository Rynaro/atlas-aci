#!/bin/sh
# ACI-S33 production-image scenario. Usage: prod-scenario.sh [IMAGE]   (default atlas-aci:s33)
#
# Build the image first:  docker build -t atlas-aci:s33 mcp-server
#
# The fixture repository is built INSIDE a container as the image user (`atlas`, uid 10001) on a
# named volume that user owns, so git sees an owner that matches the process: NO safe.directory
# override exists anywhere (S33 must reject dubious ownership, and the last step proves it).
# Every analysis container runs with --network none and --read-only; the volume (repository
# and graph root) is mounted read-only and only /out is writable. /tmp is never replaced.
# Exit 0 only when every check passes. The volume and the scratch directory are removed at the
# end unless KEEP=1.
set -u

IMAGE=${1:-atlas-aci:s33}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
OUT=$(mktemp -d "${TMPDIR:-/tmp}/s33-prod.XXXXXX")
chmod 777 "$OUT"
VOL="s33-prod-$$"
FAILED=0
PINNED_SHA=94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56
PINNED_BYTES=1631472

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
# Run the image's python helper as the image user on the volume (read-write).
helper() { docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/make-history.py":/mh.py:ro -v "$REPO":/repo:ro -v "$VOL":/w "$IMAGE" /mh.py "$@"; }
# Read a JSON field of a host file with the same helper (stdlib only).
field() { f=$1; shift; docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/make-history.py":/mh.py:ro -v "$REPO":/repo:ro -v "$OUT":/out:ro "$IMAGE" \
    /mh.py get "/out/$f" "$@"; }
# The analysis: repository and graph root read-only, only /out writable.
cochange() { # extra docker args are given through EXTRA; remaining args go to the command
    docker run --rm --network none --read-only ${EXTRA:-} -v "$VOL":/w:ro -v "$OUT":/out \
        "$IMAGE" cochange --repo /w/repo --repository-id s33-main --graph-root /w/graphs/main \
        --source-commit "$C14" --snapshot-id "$SNAP" --bulk-threshold 5 "$@"
}
inventory() { # mode, size, mtime and sha256 of every path of the volume (repo incl. .git, graphs)
    docker run --rm --network none --read-only --entrypoint sh -v "$VOL":/w:ro "$IMAGE" -c \
        'cd /w && find . -print0 | sort -z | xargs -0 stat -c "%n %F %s %a %y" \
         && find . -type f -print0 | sort -z | xargs -0 sha256sum'
}
cleanup() {
    if [ "${KEEP:-0}" != 1 ]; then docker volume rm -f "$VOL" >/dev/null 2>&1; rm -rf "$OUT"; fi
}
trap cleanup EXIT

say "image: $IMAGE  volume: $VOL  out: $OUT"
say "git: $(docker run --rm --entrypoint git "$IMAGE" --version)"
say "user: $(docker run --rm --entrypoint id "$IMAGE")"

# ---- the world: owned by the image user, built and indexed inside containers -------------------
docker volume create "$VOL" >/dev/null
UIDGID=$(docker run --rm --entrypoint id "$IMAGE" -u):$(docker run --rm --entrypoint id "$IMAGE" -g)
docker run --rm -u 0:0 --entrypoint chown -v "$VOL":/w "$IMAGE" "$UIDGID" /w
helper world /w/repo >"$OUT/ids.json" || { say "FAIL  world"; exit 1; }
C0=$(field ids.json C0)
C14=$(field ids.json C14)
GOLDEN=$(helper golden)
expect "C14 equals the fixture's golden commit id" "$C14" "$GOLDEN"
SNAP=$(docker run --rm --network none --read-only -v "$VOL":/w "$IMAGE" index --repo /w/repo \
    --graph-root /w/graphs/main --langs python 2>&1 | grep -o 'snapshot_id=[0-9a-f]\{64\}' \
    | tail -1 | cut -d= -f2)
expect "indexed snapshot id length" "${#SNAP}" 64
FUTURE=$(helper future /w/repo "$C14")
expect "a commit newer than the snapshot exists (object only)" "${#FUTURE}" 40
docker run --rm --network none --read-only --entrypoint sh -v "$VOL":/w:ro "$IMAGE" -c \
    'git --version; git -C /w/repo rev-parse --show-toplevel'

# ---- twice, byte-identical, inventory unchanged ------------------------------------------------
inventory >"$OUT/inv.before"
cochange --start "$C0" --end "$C14" --output /out/one.json >"$OUT/one.stdout" 2>"$OUT/one.err"; EX1=$?
cochange --end "$C14" --start "$C0" --output /out/two.json >"$OUT/two.stdout" 2>"$OUT/two.err"; EX2=$?
expect "cochange exit (first)" "$EX1" 0
expect "cochange exit (second)" "$EX2" 0
expect "overlay bytes identical across two runs" "$(same "$OUT/one.json" "$OUT/two.json")" same
expect "summary stdout identical across two runs" "$(same "$OUT/one.stdout" "$OUT/two.stdout")" same
expect "coverage state" "$(field one.json coverage state)" complete
expect "records" "$(field one.stdout records)" 9
expect "pair observations" "$(field one.json counts pair_observations)" 13
expect "summary evidence kind" "$(field one.stdout evidence_kind)" co_change_correlation
expect "summary coverage state" "$(field one.stdout coverage_state)" complete
expect "files compared (indexed blobs bound to the commit tree)" \
    "$(field one.json source_snapshot files_compared)" 5

# ---- a window newer than the snapshot -----------------------------------------------------------
cochange --start "$C0" --end "$FUTURE" --output /out/never.json >"$OUT/f.out" 2>"$OUT/f.err"; EXF=$?
expect "future window exit" "$EXF" 1
expect "future window stdout bytes" "$(wc -c <"$OUT/f.out" | tr -d ' ')" 0
expect "future window text" "$(cat "$OUT/f.err")" \
    "Error: History co-change rejected: outside_snapshot_history"
expect "no overlay written by a rejection" \
    "$([ -e "$OUT/never.json" ] && echo exists || echo absent)" absent

# ---- the commit budget --------------------------------------------------------------------------
cochange --start "$C0" --end "$C14" --max-commits 1 --output /out/budget.json >"$OUT/b.out" \
    2>"$OUT/b.err"; EXB=$?
expect "budget exit" "$EXB" 0
expect "budget coverage state" "$(field budget.json coverage state)" incomplete_history
expect "budget reasons" "$(field budget.json coverage reasons 0)" commit_budget_exhausted
expect "budget records withheld" "$(field budget.json coverage records)" withheld
expect "budget summary records" "$(field b.out records)" 0

# ---- a repository owned by another user: git refuses (dubious ownership) ------------------------
EXTRA="--user 10002:10002" cochange --start "$C0" --end "$C14" --output /out/foreign.json \
    >"$OUT/d.out" 2>"$OUT/d.err"; EXD=$?
expect "foreign-owned repository exit" "$EXD" 1
expect "foreign-owned repository stdout bytes" "$(wc -c <"$OUT/d.out" | tr -d ' ')" 0
expect "foreign-owned repository text" "$(cat "$OUT/d.err")" \
    "Error: History co-change rejected: history_unavailable"

# ---- nothing of the repository or the graph changed ---------------------------------------------
inventory >"$OUT/inv.after"
expect "repository (.git included) and graph inventory unchanged" \
    "$(same "$OUT/inv.before" "$OUT/inv.after")" same
expect "no temporary overlay file remains" \
    "$(find "$OUT" -name '.atlas-history-overlay-*' | wc -l | tr -d ' ')" 0

# ---- the tool manifest pin ----------------------------------------------------------------------
docker run --rm --network none --read-only -v "$VOL":/w:ro "$IMAGE" tools --repo /w/repo \
    >"$OUT/tools.json" 2>/dev/null
expect "tools JSON sha256" "$(sha_of <"$OUT/tools.json")" "$PINNED_SHA"
expect "tools JSON bytes" "$(wc -c <"$OUT/tools.json" | tr -d ' ')" "$PINNED_BYTES"

if [ "$FAILED" -eq 0 ]; then say "RESULT: all checks passed"; else say "RESULT: FAILED"; fi
exit "$FAILED"
