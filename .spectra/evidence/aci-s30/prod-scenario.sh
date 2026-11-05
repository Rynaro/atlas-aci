#!/bin/sh
# ACI-S30 production-image scenario. Usage: prod-scenario.sh [IMAGE]   (default atlas-aci:s30)
#
# Build the image first:  docker build -t atlas-aci:s30 mcp-server
#
# Two tiny repositories (consumer app, provider lib), a sentinel third repository (other) and an
# S28 package snapshot are built under one scratch directory that is always mounted at /w, so the
# snapshot ids (which depend on the absolute path) are the same in every container. Every
# container runs with --network none and --read-only; HOME=/tmp (the grammar cache) is never
# replaced. Exit 0 only when every check passes. The scratch directory is kept (printed at end).
set -u

IMAGE=${1:-atlas-aci:s30}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
W=$(mktemp -d "${TMPDIR:-/tmp}/s30-prod.XXXXXX")
chmod 777 "$W"
mkdir -p "$W/out" "$W/graphs" "$W/inputs"
chmod 777 "$W/out" "$W/graphs" "$W/inputs"
FAILED=0
# Same git config in EVERY container (Docker Desktop shows bind mounts as foreign-owned, which makes
# git refuse them and the snapshot identity fall back from git to git_unavailable).
GIT_ENV="-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=*"
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
# Run the image's python helper (no network, read-only root, scratch mounted where asked).
helper() { docker run --rm --network none $GIT_ENV --read-only --entrypoint python \
    -v "$HERE/make-world.py":/mw.py:ro -v "$REPO":/repo:ro -v "$W":/w "$IMAGE" /mw.py "$@"; }
atlas() { docker run --rm --network none $GIT_ENV --read-only "$@"; }
MOUNTS="-v $W/app:/w/app:ro -v $W/lib:/w/lib:ro -v $W/other:/w/other:ro \
-v $W/graphs:/w/graphs:ro -v $W/inputs:/w/inputs:ro -v $W/out:/w/out"
link() { # extra args...
    atlas $MOUNTS "$IMAGE" cross-repo-link --lock /w/inputs/uv.lock \
        --package-snapshot /w/inputs/pkg.json \
        --consumer-repo /w/app --consumer-graph-root /w/graphs/app \
        --provider-repo /w/lib --provider-graph-root /w/graphs/lib "$@"
}
impact() { # extra args...
    atlas $MOUNTS "$IMAGE" cross-repo-impact \
        --consumer-repo /w/app --consumer-graph-root /w/graphs/app \
        --provider-repo /w/lib --provider-graph-root /w/graphs/lib "$@"
}
inventory() { # mode, size, mtime and sha256 of every path under the repositories and graphs
    docker run --rm --network none $GIT_ENV --read-only --entrypoint sh \
        -v "$W/app":/w/app:ro -v "$W/lib":/w/lib:ro -v "$W/other":/w/other:ro \
        -v "$W/graphs":/w/graphs:ro "$IMAGE" -c \
        'cd /w && find app lib other graphs -print0 | sort -z | xargs -0 stat -c "%n %F %s %a %y" \
         && find app lib other graphs -type f -print0 | sort -z | xargs -0 sha256sum'
}
sha_of() { # a file on the host
    if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
    else shasum -a 256 | cut -d' ' -f1; fi
}
field() { f=$1; shift; helper get "/w/out/$f" "$@"; }

say "tools: cmp=$(command -v cmp || echo missing) sha256sum=$(command -v sha256sum || echo missing)" \
    "shasum=$(command -v shasum || echo missing)"
say "image: $IMAGE  scratch: $W"

# ---- the world --------------------------------------------------------------------------------
helper world /w /repo || { say "FAIL  world"; exit 1; }
chmod -R a+rwX "$W"
SNAP_APP=
SNAP_LIB=
index() { # name
    docker run --rm --network none $GIT_ENV --read-only -v "$W":/w "$IMAGE" index --repo "/w/$1" \
        --graph-root "/w/graphs/$1" --langs python 2>&1 | grep -o 'snapshot_id=[0-9a-f]\{64\}' | tail -1 | cut -d= -f2
}
SNAP_APP=$(index app)
SNAP_LIB=$(index lib)
SNAP_OTHER=$(index other)
chmod -R a+rwX "$W"
expect "indexed app snapshot id length" "${#SNAP_APP}" 64
expect "indexed lib snapshot id length" "${#SNAP_LIB}" 64
expect "indexed other snapshot id length" "${#SNAP_OTHER}" 64
docker run --rm --network none $GIT_ENV --read-only -v "$W":/w "$IMAGE" import-python-dependency \
    --lock /w/inputs/uv.lock --package s30-shapes --wheel /w/inputs/s30_shapes-1.0.0-py3-none-any.whl \
    --output /w/inputs/pkg.json >"$W/out/import.stdout"
expect "package snapshot written" "$([ -s "$W/inputs/pkg.json" ] && echo yes || echo no)" yes
helper mapping /w /w/inputs/mapping.json "$SNAP_APP" "$SNAP_LIB"

# ---- link twice, byte-identical, inventories unchanged ------------------------------------------
inventory >"$W/inv.before"
link --mapping /w/inputs/mapping.json --output /w/out/one.json >"$W/out/one.stdout"; EX1=$?
link --mapping /w/inputs/mapping.json --output /w/out/two.json >"$W/out/two.stdout"; EX2=$?
expect "link exit (first)" "$EX1" 0
expect "link exit (second)" "$EX2" 0
if command -v cmp >/dev/null 2>&1; then
    cmp -s "$W/out/one.json" "$W/out/two.json" && SAME=yes || SAME=no
    cmp -s "$W/out/one.stdout" "$W/out/two.stdout" && SAMEOUT=yes || SAMEOUT=no
else
    [ "$(sha_of <"$W/out/one.json")" = "$(sha_of <"$W/out/two.json")" ] && SAME=yes || SAME=no
    [ "$(sha_of <"$W/out/one.stdout")" = "$(sha_of <"$W/out/two.stdout")" ] && SAMEOUT=yes || SAMEOUT=no
fi
expect "overlay bytes identical across two runs" "$SAME" yes
expect "summary stdout identical across two runs" "$SAMEOUT" yes
expect "links" "$(field one.json counts links)" 7
expect "binding state" "$(field one.json package binding state)" bound
expect "provider state" "$(field one.json provider state)" available
expect "mapped vector order" "$(field one.json revision_vector 0 role),$(field one.json revision_vector 1 role)" consumer,provider
expect "summary links" "$(helper get /w/out/one.stdout counts links)" 7
inventory >"$W/inv.after-link"
expect "repo and graph inventory unchanged by two links" "$(cmp -s "$W/inv.before" "$W/inv.after-link" && echo same || echo changed)" same

# ---- impact: results while the provider is the mapped one ----------------------------------------
SEED=$(helper entity app/main.py)
AREA=$(helper link-entity /w/out/one.json area)
impact --overlay /w/out/one.json --mapping /w/inputs/mapping.json --seed "$SEED" \
    --direction to_provider >"$W/out/impact-live.json"; EXI=$?
expect "impact (live provider) exit" "$EXI" 0
expect "impact (live provider) results" "$(field impact-live.json work results)" 7
impact --overlay /w/out/one.json --mapping /w/inputs/mapping.json --seed "$AREA" \
    --direction from_provider >"$W/out/impact-from.json"
expect "impact from_provider results" "$(field impact-from.json work results)" 1
inventory >"$W/inv.after-live-impact"
expect "inventory unchanged by impact" "$(cmp -s "$W/inv.before" "$W/inv.after-live-impact" && echo same || echo changed)" same

# ---- the provider moves: refresh and a missing_provider_snapshot boundary ------------------------
docker run --rm --network none $GIT_ENV --read-only --entrypoint sh -v "$W":/w "$IMAGE" -c 'echo "notes = 2" > /w/lib/tools/notes.py'
SNAP_LIB2=$(index lib)
chmod -R a+rwX "$W"
expect "provider snapshot changed" "$([ "$SNAP_LIB" != "$SNAP_LIB2" ] && echo yes || echo no)" yes
inventory >"$W/inv.moved"
# (a) the unchanged mapping now names a snapshot that is no longer the active one
link --mapping /w/inputs/mapping.json --previous /w/out/one.json --output /w/out/stale.json \
    >"$W/out/stale.stdout"; EXS=$?
expect "refresh against the moved provider exit" "$EXS" 0
expect "provider unavailable detail" "$(field stale.json provider detail)" snapshot_not_active
expect "no links while unavailable" "$(field stale.json counts links)" 0
expect "links invalidated (unavailable)" "$(field stale.json supersedes links_invalidated)" 7
# (b) a mapping naming the new provider snapshot
helper mapping /w /w/inputs/mapping2.json "$SNAP_APP" "$SNAP_LIB2"
link --mapping /w/inputs/mapping2.json --previous /w/out/one.json --output /w/out/refreshed.json \
    >"$W/out/refreshed.stdout"; EXR=$?
expect "refresh exit" "$EXR" 0
expect "refreshed links" "$(field refreshed.json counts links)" 7
expect "links invalidated (new vector)" "$(field refreshed.json supersedes links_invalidated)" 7
expect "summary names the invalidation" "$(helper get /w/out/refreshed.stdout links_invalidated)" 7
link --mapping /w/inputs/mapping2.json --previous /w/out/refreshed.json --output /w/out/again.json \
    >/dev/null; EXA=$?
expect "refresh with unchanged inputs exit" "$EXA" 0
link --mapping /w/inputs/mapping2.json --output /w/out/fresh.json >/dev/null
expect "refresh is a fixpoint (equals a fresh build)" "$(cmp -s "$W/out/again.json" "$W/out/fresh.json" && echo same || echo differs)" same
# (c) impact with the old overlay and the old mapping
impact --overlay /w/out/one.json --mapping /w/inputs/mapping.json --seed "$SEED" \
    --direction to_provider >"$W/out/impact-missing.json"; EXM=$?
expect "impact (provider moved) exit" "$EXM" 0
expect "impact boundary kinds" "$(helper kinds /w/out/impact-missing.json)" missing_provider_snapshot
expect "impact results" "$(field impact-missing.json work results)" 0
expect "impact status" "$(field impact-missing.json status)" potential_impact
expect "boundary detail" "$(field impact-missing.json boundaries 0 detail)" snapshot_not_active
expect "boundary at" "$(field impact-missing.json boundaries 0 at)" reference
impact --overlay /w/out/one.json --mapping /w/inputs/mapping.json --seed "$AREA" \
    --direction from_provider >"$W/out/impact-missing-from.json"
expect "from_provider boundary" "$(helper kinds /w/out/impact-missing-from.json)" missing_provider_snapshot
expect "overlay of another mapping refused (exit)" "$(impact --overlay /w/out/one.json --mapping /w/inputs/mapping2.json --seed "$SEED" --direction to_provider >/dev/null 2>&1; echo $?)" 1

# ---- rejections (exit 1, empty stdout) and usage (exit 2) ----------------------------------------
helper mapping /w /w/inputs/mapping-other.json "$SNAP_APP" "$SNAP_LIB2" other
link --mapping /w/inputs/mapping-other.json --output /w/out/never.json >"$W/rej1.out" 2>"$W/rej1.err"; EXJ=$?
expect "allowlist rejection exit" "$EXJ" 1
expect "allowlist rejection stdout bytes" "$(wc -c <"$W/rej1.out" | tr -d ' ')" 0
expect "allowlist rejection text" "$(cat "$W/rej1.err")" "Error: Cross-repository link rejected: repository_not_allowlisted"
cp "$W/inputs/uv.lock" "$W/inputs/uv.flipped.lock"
printf '# flipped\n' >>"$W/inputs/uv.flipped.lock"
atlas $MOUNTS "$IMAGE" cross-repo-link --mapping /w/inputs/mapping2.json --lock /w/inputs/uv.flipped.lock \
    --package-snapshot /w/inputs/pkg.json --consumer-repo /w/app --consumer-graph-root /w/graphs/app \
    --provider-repo /w/lib --provider-graph-root /w/graphs/lib --output /w/out/never.json \
    >"$W/rej2.out" 2>"$W/rej2.err"; EXK=$?
expect "lock rejection exit" "$EXK" 1
expect "lock rejection stdout bytes" "$(wc -c <"$W/rej2.out" | tr -d ' ')" 0
expect "lock rejection text" "$(cat "$W/rej2.err")" "Error: Cross-repository link rejected: lock_digest_mismatch"
link --mapping /w/inputs/mapping2.json --max-references 0 --output /w/out/never.json >"$W/use.out" 2>"$W/use.err"; EXU=$?
expect "usage exit" "$EXU" 2
expect "usage stdout bytes" "$(wc -c <"$W/use.out" | tr -d ' ')" 0
expect "usage text" "$(tail -1 "$W/use.err")" "Error: Invalid cross-repository limit: max_references"
link --mapping /w/inputs/mapping2.json --output /w/app/inside.json >/dev/null 2>"$W/use2.err"; EXV=$?
expect "output inside a repository exit" "$EXV" 2
expect "no overlay written by a rejection" "$([ -e "$W/out/never.json" ] && echo exists || echo absent)" absent
inventory >"$W/inv.final"
expect "inventory unchanged by impact and rejections (after the provider moved)" "$(cmp -s "$W/inv.moved" "$W/inv.final" && echo same || echo changed)" same
expect "no temporary overlay file remains" "$(find "$W" -name '.atlas-cross-repo-overlay-*' | wc -l | tr -d ' ')" 0

# ---- the tool manifest pin -----------------------------------------------------------------------
atlas -v "$W/app":/w/app:ro "$IMAGE" tools --repo /w/app >"$W/tools.json"
expect "tools JSON sha256" "$(sha_of <"$W/tools.json")" "$PINNED_SHA"
expect "tools JSON bytes" "$(wc -c <"$W/tools.json" | tr -d ' ')" "$PINNED_BYTES"

if [ "$FAILED" -eq 0 ]; then say "RESULT: all checks passed (scratch kept at $W)"; else say "RESULT: FAILED (scratch kept at $W)"; fi
exit "$FAILED"
