#!/bin/sh
# ACI-S34 production-image scenario. Usage: prod-scenario.sh [IMAGE]   (default atlas-aci:s34)
#
# Build the image first:  docker build -t atlas-aci:s34 mcp-server
#
# The fixture repository (source plus ADR markdown, before and after states) and its snapshots
# are built INSIDE containers as the image user (`atlas`, uid 10001) on a named volume that user
# owns, so git sees an owner that matches the process: NO safe.directory override exists anywhere.
# Every analysis container runs with --network none and --read-only; the volume (repository and
# graph roots) is mounted read-only and only /out is writable. /tmp is never replaced.
# Exit 0 only when every check passes. The volume and the scratch directory are removed at the
# end unless KEEP=1.
set -u

IMAGE=${1:-atlas-aci:s34}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
OUT=$(mktemp -d "${TMPDIR:-/tmp}/s34-prod.XXXXXX")
chmod 777 "$OUT"
VOL="s34-prod-$$"
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
    -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$VOL":/w "$IMAGE" /ph.py "$@"; }
# Read a JSON field of a host file in /out with the same helper (stdlib only).
field() { f=$1; shift; docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$OUT":/out:ro "$IMAGE" \
    /ph.py get "/out/$f" "$@"; }
result() { f=$1; shift; docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$OUT":/out:ro "$IMAGE" \
    /ph.py result "/out/$f" "$@"; }
finding() { f=$1; shift; docker run --rm --network none --read-only --entrypoint python \
    -v "$HERE/prod-helper.py":/ph.py:ro -v "$REPO":/repo:ro -v "$OUT":/out:ro "$IMAGE" \
    /ph.py finding "/out/$f" "$@"; }
# The analysis: repository and graph roots read-only, only /out writable.
analyse() { # command, then its arguments
    docker run --rm --network none --read-only -v "$VOL":/w:ro -v "$OUT":/out "$IMAGE" "$@"
}
inventory() { # mode, size, mtime and sha256 of every path of the volume (repo incl. .git, graphs)
    docker run --rm --network none --read-only --entrypoint sh -v "$VOL":/w:ro "$IMAGE" -c \
        'cd /w && find . -print0 | sort -z | xargs -0 stat -c "%n %F %s %a %y" \
         && find . -type f -print0 | sort -z | xargs -0 sha256sum'
}
index() { # graph name, extra index arguments
    name=$1; shift
    docker run --rm --network none --read-only -v "$VOL":/w "$IMAGE" index --repo /w/repo \
        --graph-root "/w/graphs/$name" "$@" 2>&1 | grep -o 'snapshot_id=[0-9a-f]\{64\}' \
        | tail -1 | cut -d= -f2
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
helper world /w/repo before || { say "FAIL  world before"; exit 1; }
SNAP=$(index before)
expect "before snapshot id length" "${#SNAP}" 64
helper world /w/repo after || { say "FAIL  world after"; exit 1; }
AFTER=$(index after)
expect "after snapshot id length" "${#AFTER}" 64
OTHER=$(index config --langs python,markdown)
expect "config-mismatch snapshot id length" "${#OTHER}" 64
helper fill mapping.json "$SNAP" /w/mapping.json
docker run --rm --network none --read-only --entrypoint cat -v "$VOL":/w:ro "$IMAGE" \
    /w/mapping.json >"$OUT/mapping.json"
helper fill mapping.json "$(printf 'f%.0s' $(seq 1 64))" /w/wrong.json
docker run --rm --network none --read-only --entrypoint cat -v "$VOL":/w:ro "$IMAGE" \
    /w/wrong.json >"$OUT/wrong.json"
# The mapping files live on the volume only to be read here; remove them from the inventory.
docker run --rm --entrypoint rm -v "$VOL":/w "$IMAGE" -f /w/mapping.json /w/wrong.json
docker run --rm --network none --read-only --entrypoint sh -v "$VOL":/w:ro "$IMAGE" -c \
    'git --version; git -C /w/repo rev-parse --show-toplevel'

MAP="--mapping /out/mapping.json --repo /w/repo --graph-root /w/graphs/before"
DRIFT="--mapping /out/mapping.json --repo /w/repo --before-graph-root /w/graphs/before"

# ---- twice, byte-identical, inventory unchanged ------------------------------------------------
inventory >"$OUT/inv.before"
cp "$OUT/mapping.json" "$OUT/mapping.before"
analyse rationale-map $MAP --output /out/map1.json >"$OUT/map1.stdout" 2>"$OUT/map1.err"; EX1=$?
analyse rationale-map --output /out/map2.json --graph-root /w/graphs/before --repo /w/repo \
    --mapping /out/mapping.json >"$OUT/map2.stdout" 2>"$OUT/map2.err"; EX2=$?
expect "rationale-map exit (first)" "$EX1" 0
expect "rationale-map exit (second)" "$EX2" 0
expect "overlay bytes identical across two runs" "$(same "$OUT/map1.json" "$OUT/map2.json")" same
expect "overlay summary stdout identical" "$(same "$OUT/map1.stdout" "$OUT/map2.stdout")" same
expect "overlay summary command" "$(field map1.stdout command)" rationale-map
expect "overlay resolved mappings" "$(field map1.stdout counts resolved)" 22
expect "overlay unresolved mappings" "$(field map1.stdout counts unresolved)" 0

analyse rationale-drift $DRIFT --after-graph-root /w/graphs/after --output /out/drift1.json \
    >"$OUT/drift1.stdout" 2>"$OUT/drift1.err"; EX3=$?
analyse rationale-drift --output /out/drift2.json --after-graph-root /w/graphs/after \
    --before-graph-root /w/graphs/before --repo /w/repo --mapping /out/mapping.json \
    >"$OUT/drift2.stdout" 2>"$OUT/drift2.err"; EX4=$?
expect "rationale-drift exit (first)" "$EX3" 0
expect "rationale-drift exit (second)" "$EX4" 0
expect "drift bytes identical across two runs" "$(same "$OUT/drift1.json" "$OUT/drift2.json")" same
expect "drift summary stdout identical" "$(same "$OUT/drift1.stdout" "$OUT/drift2.stdout")" same
expect "drift overlay id equals the map id" "$(field drift1.json overlay_id)" \
    "$(field map1.json id)"
expect "comparison state" "$(field drift1.json comparison state)" compared
expect "review findings" "$(field drift1.stdout counts rationale_review_needed)" 4
expect "unknown_target_history" "$(field drift1.stdout counts unknown_target_history)" 5
expect "unresolved_mapping" "$(field drift1.stdout counts unresolved_mapping)" 6
expect "no_finding" "$(field drift1.stdout counts no_finding)" 7
expect "body-changed mapping raises rationale_review_needed" \
    "$(result drift1.json charge outcome)" rationale_review_needed
expect "body-changed mapping has a finding" "$(finding drift1.json charge)" yes
expect "unchanged mapping has no finding" "$(finding drift1.json summary)" no
expect "unchanged mapping outcome" "$(result drift1.json summary outcome)" no_finding
expect "edited-document control has no finding" "$(finding drift1.json charge-doc-edited)" no
expect "removed target stays unresolved" "$(result drift1.json retire reason)" target_removed
expect "renumbered sibling is unknown" "$(result drift1.json twin-first reason)" \
    sibling_ordinal_shift

# ---- a pair S19 cannot compare: written, every mapping unknown, exit 0 -------------------------
analyse rationale-drift $DRIFT --after-graph-root /w/graphs/config --output /out/rejected.json \
    >"$OUT/rej.stdout" 2>"$OUT/rej.err"; EXR=$?
expect "rejected comparison exit" "$EXR" 0
expect "rejected comparison state" "$(field rejected.json comparison state)" rejected
expect "rejected comparison reason" "$(field rejected.json comparison reason)" \
    configuration_mismatch
expect "rejected comparison: every mapping is unknown_target_history" \
    "$(field rej.stdout counts unknown_target_history)" 22
expect "rejected comparison result reason" "$(result rejected.json charge reason)" \
    comparison_rejected
expect "rejected comparison result detail" "$(result rejected.json charge detail)" \
    configuration_mismatch

# ---- a mapping for another snapshot: exit 1, empty stdout, nothing written ---------------------
analyse rationale-drift --mapping /out/wrong.json --repo /w/repo \
    --before-graph-root /w/graphs/before --after-graph-root /w/graphs/after \
    --output /out/never.json >"$OUT/m.out" 2>"$OUT/m.err"; EXM=$?
expect "snapshot mismatch exit" "$EXM" 1
expect "snapshot mismatch stdout bytes" "$(wc -c <"$OUT/m.out" | tr -d ' ')" 0
expect "snapshot mismatch text" "$(cat "$OUT/m.err")" \
    "Error: Rationale drift rejected: mapping_snapshot_mismatch"
expect "no document written by a rejection" \
    "$([ -e "$OUT/never.json" ] && echo exists || echo absent)" absent

# ---- usage: exit 2, empty stdout ----------------------------------------------------------------
analyse rationale-drift --mapping /out/mapping.json --output /out/usage.json \
    >"$OUT/u.out" 2>"$OUT/u.err"; EXU=$?
expect "usage exit" "$EXU" 2
expect "usage stdout bytes" "$(wc -c <"$OUT/u.out" | tr -d ' ')" 0
expect "usage text" "$(tail -n 1 "$OUT/u.err")" \
    "Error: Give --repo, or both --before-repo and --after-repo."
analyse rationale-map $MAP --output /w/repo/inside.json >"$OUT/i.out" 2>"$OUT/i.err"; EXI=$?
expect "output inside the repository exit" "$EXI" 2
expect "output inside the repository text" "$(tail -n 1 "$OUT/i.err")" \
    "Error: --output must be outside the repositories and graph roots."

# ---- nothing of the repository, the ADRs or the graphs changed ---------------------------------
inventory >"$OUT/inv.after"
expect "repository (.git included) and graph inventory unchanged" \
    "$(same "$OUT/inv.before" "$OUT/inv.after")" same
expect "the mapping file is unchanged" "$(same "$OUT/mapping.before" "$OUT/mapping.json")" same
expect "no temporary document file remains" \
    "$(find "$OUT" -name '.atlas-rationale-*' | wc -l | tr -d ' ')" 0

# ---- the tool manifest pin ----------------------------------------------------------------------
docker run --rm --network none --read-only -v "$VOL":/w:ro "$IMAGE" tools --repo /w/repo \
    >"$OUT/tools.json" 2>/dev/null
expect "tools JSON sha256" "$(sha_of <"$OUT/tools.json")" "$PINNED_SHA"
expect "tools JSON bytes" "$(wc -c <"$OUT/tools.json" | tr -d ' ')" "$PINNED_BYTES"

if [ "$FAILED" -eq 0 ]; then say "RESULT: all checks passed"; else say "RESULT: FAILED"; fi
exit "$FAILED"
