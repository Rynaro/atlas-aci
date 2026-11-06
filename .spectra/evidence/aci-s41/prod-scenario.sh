#!/bin/sh
# ACI-S41 production scenario.  Usage: prod-scenario.sh [IMAGE]      (default atlas-aci:s41-wip)
#
# Exercises durable index jobs, retained generations, reader leases and lease-aware garbage
# collection INSIDE the production image, across containers that share a NAMED Docker volume (no
# macOS bind mount for locks). The image has entrypoint `atlas-aci`, user `atlas` (uid 10001),
# /usr/local/bin/python 3.12, no `python3`, no `timeout` and no pytest, so every container passes
# --entrypoint explicitly, is `--rm`, `--network none`, is named s41-<pid>-<n> and is bounded by a
# host-side watchdog (`docker kill`), never by a `timeout` binary. Volumes are removed on exit.
#
# Every check is non-vacuous (a positive control precedes each negative claim) and holds whether the
# tree is untracked, staged or committed (checks read the working tree; git is used for the base
# diff only).
#  H0  the image exists
#  P0  identity: the sha256 of the 6 changed or new and the frozen source files in the image equal
#      this tree
#  P1  python -I writes tree v1 into the repo volume
#  P2  `jobs submit` completes (G1, 5 parser invocations); the same submit again is reused
#  P3  v2 (every file changed): a kill at checkpoint 2 exits 137 (the PID-1 fallback works),
#      `jobs status` shows interrupted == 1 and the live generation is still G1; `jobs resume` gives
#      resumed_from 2 and 3 parser invocations (G2). v3: a submit blocked at `staged` is SIGTERMed,
#      then released: exit 4 JOB_CANCELLED, the live generation is still G2; the same job without a
#      cancel publishes G3
#  P4  two containers: A holds G1 (`jobs acquire --hold`, graphs mounted :ro); B writes
#      retention.json (keep_last 1) and runs `jobs gc`: G1 is retained as leased, G3 as current and
#      >= 1 generation is retired; closing A's stdin ends A with exit 0; the next `jobs gc` retires
#      G1; `jobs acquire G1` is EXPIRED_REFERENCE (exit 3); a further `jobs gc` retires nothing and
#      has an equal retained_digest
#  P5  `jobs acquire` of the current generation exits 0 after GC; an acquire in a :ro container
#      leaves the graphs manifest (bytes and mtimes, computed in the container) unchanged
#  P6  a read-only `atlas-aci serve` (:ro mounts, --read-only) returns identical search_symbol
#      results before, during (a concurrent GC container retires a generation) and after GC, with
#      no error (the live server is unchanged across garbage collection)
#  P7  `atlas-aci tools` stdout equals the tools-JSON pin (sha256 and bytes)
#  H8  host checks: pyproject.toml / uv.lock pins and the frozen paths byte-identical to the base
#      commit, the changed set under mcp-server/src is exactly the six allowed files and
#      __main__.py has 0 deleted lines
# (An empty named volume is re-initialised from the image's /repo mount point on every start, which
# would undo the chown, so the init container also leaves a marker file in each volume.)
# Environment: BOUND (seconds per container, default 300), PROD_JSON (output path), S41_NEGATIVE=1
# (the first submit expects reuse: the run must FAIL).
set -eu

IMAGE=${1:-atlas-aci:s41-wip}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
BASE=fa16b1c64f6ad4cb7cfde9957e8536fbaeeff318
BOUND=${BOUND:-300}
PROD_JSON=${PROD_JSON:-$HERE/production-scenario.json}
TOOLS_SHA=94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56
TOOLS_BYTES=1631472
PYPROJECT_SHA=981f0784d6bc5c1502c91ab27657beb483460b97e066cd8846f51e98f1d7d92f
UV_LOCK_SHA=87ba265e76f382aaa4387da7ca5d89dc85eb3699b35ee4315e981302b19646fd
EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
SRC=$REPO/mcp-server/src/atlas_aci
WORK=$(mktemp -d "${TMPDIR:-/tmp}/s41-prod.XXXXXX")
VOL_REPO=s41-$$-repo
VOL_GRAPHS=s41-$$-graphs
PASS_N=0
FAIL_N=0
SEQ=0
NAMES=""
RESULTS=""
NEGATIVE=${S41_NEGATIVE:-0}

cleanup() {
    { exec 4>&-; } 2> /dev/null || true
    { exec 5>&-; } 2> /dev/null || true
    for n in $NAMES; do docker rm -f "$n" > /dev/null 2>&1 || true; done
    docker volume rm -f "$VOL_REPO" "$VOL_GRAPHS" > /dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

pass() { echo "PASS  $1"; PASS_N=$((PASS_N + 1)); RESULTS="$RESULTS\"$2\":\"PASS\","; }
fail() { echo "FAIL  $1"; FAIL_N=$((FAIL_N + 1)); RESULTS="$RESULTS\"$2\":\"FAIL\","; }
sha_of() {
    if command -v sha256sum > /dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
nonempty() { [ -s "$1" ]; }
cname() { n=$(cat "$WORK/seq" 2> /dev/null || echo 0); n=$((n + 1)); echo "$n" > "$WORK/seq"; echo "s41-$$-$n"; }  # a file: $(...) is a subshell
# hexf FILE KEY: the first "KEY":"<64 hex>" value; numf FILE KEY: the first "KEY":<digits> value.
hexf() { grep -o "\"$2\":\"[0-9a-f]\{64\}\"" "$1" | head -1 | cut -d'"' -f4 || true; }
numf() { grep -o "\"$2\":[0-9][0-9]*" "$1" | head -1 | cut -d: -f2 || true; }
has() { grep -q -- "$2" "$1" 2> /dev/null; }

# bounded NAME OUT ERR docker-run-args...   -> the container's exit code; 124 when killed.
bounded() {
    name=$1; out=$2; err=$3; shift 3
    NAMES="$NAMES $name"
    docker run --rm --name "$name" --network none "$@" > "$out" 2> "$err" 4>&- 5>&- &
    pid=$!
    n=0
    while kill -0 "$pid" 2> /dev/null && [ "$n" -lt "$BOUND" ]; do sleep 1; n=$((n + 1)); done
    if kill -0 "$pid" 2> /dev/null; then
        docker kill "$name" > /dev/null 2>&1 || true
        kill "$pid" 2> /dev/null || true
        wait "$pid" 2> /dev/null || true
        return 124
    fi
    rc=0
    wait "$pid" || rc=$?
    return "$rc"
}
# start NAME OUT ERR STDIN docker-run-args...  -> BG_PID; an interactive container fed from a fifo.
start() {
    name=$1; out=$2; err=$3; fifo=$4; shift 4
    NAMES="$NAMES $name"
    # fds 4 and 5 (the fifo write ends) are closed in the child: an inherited copy would keep the
    # fifo open and the container would never see EOF.
    docker run --rm -i --name "$name" --network none "$@" < "$fifo" > "$out" 2> "$err" 4>&- 5>&- &
    BG_PID=$!
}
# wait_for FILE PATTERN SECONDS: poll until the pattern appears in the file.
wait_for() {
    n=0
    while [ "$n" -lt "$3" ]; do
        if grep -q -- "$2" "$1" 2> /dev/null; then return 0; fi
        sleep 1; n=$((n + 1))
    done
    return 1
}
# wait_pid PID NAME SECONDS -> the exit code, 124 after a kill
wait_pid() {
    n=0
    while kill -0 "$1" 2> /dev/null && [ "$n" -lt "$3" ]; do sleep 1; n=$((n + 1)); done
    if kill -0 "$1" 2> /dev/null; then docker kill "$2" > /dev/null 2>&1 || true; wait "$1" 2> /dev/null || true; return 124; fi
    rc=0
    wait "$1" || rc=$?
    return "$rc"
}

ATLAS="--entrypoint atlas-aci"
RW="--read-only -v $VOL_REPO:/repo -v $VOL_GRAPHS:/graphs"
RO="--read-only -v $VOL_REPO:/repo:ro -v $VOL_GRAPHS:/graphs:ro"
JOBARGS="--repo /repo --graph-root /graphs"
# atlas_rw NAME OUT ERR ENV-ARGS -- atlas-aci-args...  (rw volumes)
atlas_run() {  # atlas_run MODE NAME OUT ERR [docker-opts] -- args
    mode=$1; name=$2; out=$3; err=$4; shift 4
    opts=""
    while [ "$1" != "--" ]; do opts="$opts $1"; shift; done
    shift
    if [ "$mode" = ro ]; then vols=$RO; else vols=$RW; fi
    # shellcheck disable=SC2086
    bounded "$name" "$out" "$err" $vols $opts $ATLAS "$IMAGE" "$@"
}

# ------------------------------------------------------------------------------ H0 image
IMAGE_ID=$(docker image inspect "$IMAGE" --format '{{.Id}}' 2> /dev/null || true)
if [ -n "$IMAGE_ID" ]; then pass "H0 image $IMAGE exists ($IMAGE_ID)" H0
else fail "H0 image $IMAGE not found" H0; echo "RESULT: failed"; exit 1; fi

docker volume create "$VOL_REPO" > /dev/null
docker volume create "$VOL_GRAPHS" > /dev/null
rc=0
bounded "$(cname)" "$WORK/init.out" "$WORK/init.err" --user 0 -v "$VOL_REPO":/repo -v "$VOL_GRAPHS":/graphs \
    --entrypoint sh "$IMAGE" -c 'touch /repo/.s41 /graphs/.s41 && chown 10001:10001 /repo /graphs /repo/.s41 /graphs/.s41 && chmod 755 /repo /graphs' || rc=$?
[ "$rc" = 0 ] || { echo "volume init failed ($rc): $(head -c 300 "$WORK/init.err")"; echo "RESULT: failed"; exit 1; }

# ------------------------------------------------------------------------------ P0 identity
cat > "$WORK/identity.py" <<'PY'
import hashlib
import importlib.metadata as m
from pathlib import Path

import atlas_aci

root = Path(atlas_aci.__file__).parent
print("version mcp", m.version("mcp"))
for name in (
    "storage/generations.py", "storage/leases.py", "storage/retention.py",
    "indexing/jobs.py", "indexing/job_runner.py", "__main__.py",
    "codegraph.py", "storage/evidence.py", "storage/snapshots.py", "indexing/extraction_cache.py",
    "indexing/retrieval.py", "storage/__init__.py", "indexing/__init__.py", "server.py",
    "config.py", "enforcement.py", "query/leases.py",
):
    print("sha", name, hashlib.sha256((root / name).read_bytes()).hexdigest())
PY
FILES="storage/generations.py storage/leases.py storage/retention.py indexing/jobs.py indexing/job_runner.py __main__.py codegraph.py storage/evidence.py storage/snapshots.py indexing/extraction_cache.py indexing/retrieval.py storage/__init__.py indexing/__init__.py server.py config.py enforcement.py query/leases.py"
rc=0
bounded "$(cname)" "$WORK/p0.out" "$WORK/p0.err" --entrypoint /usr/local/bin/python \
    -v "$WORK/identity.py":/identity.py:ro "$IMAGE" -I /identity.py || rc=$?
if [ "$rc" = 0 ] && nonempty "$WORK/p0.out" && has "$WORK/p0.out" '^version mcp 1.30.0$'; then
    SHA_OK=1
    for f in $FILES; do
        want=$(sha_of "$SRC/$f")
        have=$(grep "^sha $f " "$WORK/p0.out" | cut -d' ' -f3)
        if [ -n "$want" ] && [ "$want" != "$EMPTY_SHA" ] && [ "$want" = "$have" ]; then :
        else SHA_OK=0; echo "  sha mismatch for $f: host '$want' image '$have'"; fi
    done
    if [ "$SHA_OK" = 1 ]; then pass "P0 mcp 1.30.0; the six changed or new and eleven frozen source files in the image equal this tree" P0
    else fail "P0 image sources differ from this tree" P0; fi
else fail "P0 identity container exit $rc: $(head -c 300 "$WORK/p0.err")" P0; fi

# ------------------------------------------------------------------------------ P1 tree v1
cat > "$WORK/tree.py" <<'PY'
import sys
from pathlib import Path

version = int(sys.argv[1])
shift = {1: 0, 2: 100, 3: 200, 4: 300}[version]
for i in range(5):
    body = f"def f{i}(x):\n    return x + {i + shift}\n\nclass C{i}:\n    def m(self):\n        return f{i}(1)\n"
    Path(f"/repo/m{i}.py").write_text(body)
print("wrote", version, len(list(Path("/repo").glob("m*.py"))))
PY
write_tree() {  # write_tree VERSION
    bounded "$(cname)" "$WORK/tree$1.out" "$WORK/tree$1.err" --read-only -v "$VOL_REPO":/repo \
        -v "$WORK/tree.py":/tree.py:ro --entrypoint /usr/local/bin/python "$IMAGE" -I /tree.py "$1"
}
rc=0; write_tree 1 || rc=$?
if [ "$rc" = 0 ] && has "$WORK/tree1.out" '^wrote 1 5$'; then pass "P1 python -I wrote tree v1 (5 files) into the repo volume" P1
else fail "P1 tree v1 rc=$rc: $(head -c 200 "$WORK/tree1.err")" P1; fi

# ------------------------------------------------------------------------------ P2 submit and reuse
rc=0
atlas_run rw "$(cname)" "$WORK/p2a.out" "$WORK/p2a.err" -- jobs submit $JOBARGS --langs python --now 100 || rc=$?
G1=$(hexf "$WORK/p2a.out" generation_id)
JOB1=$(hexf "$WORK/p2a.out" job_id)
PARSE1=$(numf "$WORK/p2a.out" parser_invocations)
EXPECT_REUSED=false
[ "$NEGATIVE" = 1 ] && EXPECT_REUSED=true
if [ "$rc" = 0 ] && [ -n "$G1" ] && [ -n "$JOB1" ] && [ "$PARSE1" = 5 ] && has "$WORK/p2a.out" "\"reused\":$EXPECT_REUSED" \
    && has "$WORK/p2a.out" '"receipt_is_current":true'; then
    pass "P2 jobs submit completed: G1 $G1, 5 parser invocations (the positive control), receipt_is_current" P2.build
else fail "P2 first submit rc=$rc reused-expect=$EXPECT_REUSED parse='$PARSE1' g1='$G1': $(head -c 300 "$WORK/p2a.err")" P2.build; fi
rc=0
atlas_run rw "$(cname)" "$WORK/p2b.out" "$WORK/p2b.err" -- jobs submit $JOBARGS --langs python --now 101 || rc=$?
if [ "$rc" = 0 ] && [ -n "$G1" ] && has "$WORK/p2b.out" '"reused":true' && [ "$(hexf "$WORK/p2b.out" generation_id)" = "$G1" ] \
    && [ "$(sed 's/"reused":[a-z]*/"reused":X/' "$WORK/p2a.out")" = "$(sed 's/"reused":[a-z]*/"reused":X/' "$WORK/p2b.out")" ]; then
    pass "P2 the identical submit is reused: the same G1 and a result equal to the first one except reused (the stored receipt, attempt 1: no new work)" P2.reuse
else fail "P2 reuse rc=$rc: $(head -c 300 "$WORK/p2b.out")" P2.reuse; fi

# ------------------------------------------------------------------------------ P3 kill, resume, cancel
rc=0; write_tree 2 || rc=$?
[ "$rc" = 0 ] && has "$WORK/tree2.out" '^wrote 2 5$' || { fail "P3 tree v2 rc=$rc" P3.tree; }
rc=0
atlas_run rw "$(cname)" "$WORK/p3k.out" "$WORK/p3k.err" \
    -e 'ATLAS_ACI_JOB_FAULTS={"checkpoint":{"action":"kill","at":2}}' -- jobs submit $JOBARGS --langs python --now 200 || rc=$?
if [ "$rc" = 137 ] && ! has "$WORK/p3k.out" '"format"'; then
    pass "P3 the kill seam at checkpoint 2 ended the container with exit 137 (PID 1 included)" P3.kill
else fail "P3 kill exit $rc (want 137): $(head -c 200 "$WORK/p3k.out")" P3.kill; fi
rc=0
atlas_run ro "$(cname)" "$WORK/p3s.out" "$WORK/p3s.err" -- jobs status $JOBARGS || rc=$?
JOB2=$(hexf "$WORK/p3s.out" job_id)
if [ "$rc" = 0 ] && has "$WORK/p3s.out" '"interrupted":1' && has "$WORK/p3s.out" '"code":"JOB_INTERRUPTED"' && [ -n "$JOB2" ] \
    && [ "$(hexf "$WORK/p3s.out" current_generation_id)" = "$G1" ]; then
    pass "P3 jobs status: interrupted == 1 (JOB_INTERRUPTED), the live generation is still G1" P3.status
else fail "P3 status rc=$rc: $(head -c 400 "$WORK/p3s.out")" P3.status; fi
rc=0
atlas_run rw "$(cname)" "$WORK/p3r.out" "$WORK/p3r.err" -- jobs resume $JOBARGS --job "${JOB2:-0}" --now 300 || rc=$?
G2=$(hexf "$WORK/p3r.out" generation_id)
if [ "$rc" = 0 ] && [ "$(numf "$WORK/p3r.out" resumed_from)" = 2 ] && [ "$(numf "$WORK/p3r.out" parser_invocations)" = 3 ] \
    && [ "$(numf "$WORK/p3r.out" disk_hits)" = 2 ] && [ -n "$G2" ] && [ "$G2" != "$G1" ]; then
    pass "P3 jobs resume: resumed_from 2, parser_invocations 3, disk_hits 2 (G2 $G2)" P3.resume
else fail "P3 resume rc=$rc: $(head -c 500 "$WORK/p3r.out")" P3.resume; fi

rc=0; write_tree 3 || rc=$?
mkfifo "$WORK/fifo-b"
exec 5<> "$WORK/fifo-b"
start "$(cname)" "$WORK/p3b.out" "$WORK/p3b.err" "$WORK/fifo-b" $RW -e 'ATLAS_ACI_JOB_FAULTS={"staged":{"action":"block"}}' \
    $ATLAS "$IMAGE" jobs submit $JOBARGS --langs python --now 400
B_PID=$BG_PID; B_NAME=$(echo "$NAMES" | awk '{print $NF}')
if wait_for "$WORK/p3b.out" 'ATLAS_JOB_SEAM staged' 120; then
    docker kill --signal TERM "$B_NAME" > /dev/null 2>&1 || true
    sleep 1
    printf '\n' >&5
    RC_B=0; wait_pid "$B_PID" "$B_NAME" 60 || RC_B=$?   # not rc: bounded() overwrites it
    exec 5>&-
    rc2=0
    atlas_run ro "$(cname)" "$WORK/p3c.out" "$WORK/p3c.err" -- jobs status $JOBARGS || rc2=$?
    if [ "$RC_B" = 4 ] && has "$WORK/p3b.out" '"code":"JOB_CANCELLED"' && [ "$rc2" = 0 ] \
        && [ "$(hexf "$WORK/p3c.out" current_generation_id)" = "$G2" ] && [ "$G2" != "" ]; then
        pass "P3 SIGTERM at a blocked submit: exit 4 JOB_CANCELLED; jobs status current is still G2" P3.cancel
    else fail "P3 cancel rc=$RC_B status rc=$rc2 current='$(hexf "$WORK/p3c.out" current_generation_id)': $(head -c 300 "$WORK/p3b.out")" P3.cancel; fi
else
    exec 5>&-
    docker kill "$B_NAME" > /dev/null 2>&1 || true
    fail "P3 the blocked submit never reached the staged seam: $(head -c 300 "$WORK/p3b.err")" P3.cancel
fi
rc=0
atlas_run rw "$(cname)" "$WORK/p3d.out" "$WORK/p3d.err" -- jobs submit $JOBARGS --langs python --now 500 || rc=$?
G3=$(hexf "$WORK/p3d.out" generation_id)
if [ "$rc" = 0 ] && [ -n "$G3" ] && [ "$G3" != "$G2" ] && has "$WORK/p3d.out" '"receipt_is_current":true'; then
    pass "P3 control: the same job without a cancel publishes G3 $G3" P3.control
else fail "P3 control rc=$rc: $(head -c 300 "$WORK/p3d.out")" P3.control; fi

# ------------------------------------------------------------------------------ P4 lease and gc across containers
NS=$(grep -o '"namespace":"[0-9a-f]\{64\}"' "$WORK/p3s.out" | head -1 | cut -d'"' -f4 || true)
cat > "$WORK/policy.py" <<'PY'
import json
import sys
from pathlib import Path

target = Path("/graphs/repos") / sys.argv[1] / "retention.json"
target.write_text(json.dumps({"format": "atlas-aci-retention.v1", "keep_last": 1}))
print("policy written", target.name)
PY
rc=0
bounded "$(cname)" "$WORK/pol.out" "$WORK/pol.err" --read-only -v "$VOL_GRAPHS":/graphs -v "$WORK/policy.py":/policy.py:ro \
    --entrypoint /usr/local/bin/python "$IMAGE" -I /policy.py "${NS:-missing}" || rc=$?
[ "$rc" = 0 ] && has "$WORK/pol.out" '^policy written' || echo "  policy write rc=$rc: $(head -c 200 "$WORK/pol.err")"

mkfifo "$WORK/fifo-a"
exec 4<> "$WORK/fifo-a"
start "$(cname)" "$WORK/p4a.out" "$WORK/p4a.err" "$WORK/fifo-a" $RO $ATLAS "$IMAGE" jobs acquire $JOBARGS --generation "${G1:-0}" --hold
A_PID=$BG_PID; A_NAME=$(echo "$NAMES" | awk '{print $NF}')
if wait_for "$WORK/p4a.out" '"origin":"retained"' 120 && has "$WORK/p4a.out" "\"generation_id\":\"$G1\""; then
    pass "P4 container A (graphs :ro) holds a lease on G1 (origin retained)" P4.hold
    rc=0
    atlas_run rw "$(cname)" "$WORK/p4g1.out" "$WORK/p4g1.err" -- jobs gc $JOBARGS --now 1000 || rc=$?
    if [ "$rc" = 0 ] && has "$WORK/p4g1.out" "{\"generation_id\":\"$G1\",\"reason\":\"leased\"}" \
        && has "$WORK/p4g1.out" "{\"generation_id\":\"$G3\",\"reason\":\"current\"}" \
        && has "$WORK/p4g1.out" "\"retired\":\[{\"generation_id\":\"$G2\",\"reason\":\"outside_window\"}\]"; then
        pass "P4 GC in container B: G1 retained (leased), G3 retained (current), G2 retired (non-vacuous)" P4.gc_leased
    else fail "P4 gc rc=$rc: $(head -c 700 "$WORK/p4g1.out")" P4.gc_leased; fi
    exec 4>&-
    RC_A=0; wait_pid "$A_PID" "$A_NAME" 60 || RC_A=$?
    if [ "$RC_A" = 0 ]; then pass "P4 closing A's stdin ended the holder with exit 0" P4.release
    else fail "P4 holder exit $RC_A" P4.release; fi
else
    exec 4>&-
    docker kill "$A_NAME" > /dev/null 2>&1 || true
    fail "P4 holder never printed its lease: $(head -c 300 "$WORK/p4a.err")" P4.hold
    fail "P4 skipped: no holder" P4.gc_leased
    fail "P4 skipped: no holder" P4.release
fi
rc=0
atlas_run rw "$(cname)" "$WORK/p4g2.out" "$WORK/p4g2.err" -- jobs gc $JOBARGS --now 1000 || rc=$?
if [ "$rc" = 0 ] && has "$WORK/p4g2.out" "\"retired\":\[{\"generation_id\":\"$G1\",\"reason\":\"outside_window\"}\]"; then
    pass "P4 after the lease ended, GC retired G1" P4.gc_retired
else fail "P4 second gc rc=$rc: $(head -c 600 "$WORK/p4g2.out")" P4.gc_retired; fi
rc=0
atlas_run ro "$(cname)" "$WORK/p4e.out" "$WORK/p4e.err" -- jobs acquire $JOBARGS --generation "${G1:-0}" || rc=$?
if [ "$rc" = 3 ] && has "$WORK/p4e.out" '"code":"EXPIRED_REFERENCE"' && has "$WORK/p4e.out" '"retry_hint":"retry_current"'; then
    pass "P4 acquiring the retired G1: exit 3 EXPIRED_REFERENCE (retry_current)" P4.expired
else fail "P4 expired acquire rc=$rc: $(head -c 300 "$WORK/p4e.out")" P4.expired; fi
rc=0
atlas_run rw "$(cname)" "$WORK/p4g3.out" "$WORK/p4g3.err" -- jobs gc $JOBARGS --now 1000 || rc=$?
D2=$(hexf "$WORK/p4g2.out" retained_digest); D3=$(hexf "$WORK/p4g3.out" retained_digest)
if [ "$rc" = 0 ] && has "$WORK/p4g3.out" '"retired":\[\]' && [ -n "$D2" ] && [ "$D2" = "$D3" ]; then
    pass "P4 a further GC retires nothing and has an equal retained_digest ($D3)" P4.replay
else fail "P4 replay rc=$rc digests '$D2' '$D3'" P4.replay; fi

# ------------------------------------------------------------------------------ P5 current and read-only acquire
rc=0
atlas_run ro "$(cname)" "$WORK/p5a.out" "$WORK/p5a.err" -- jobs acquire $JOBARGS --generation "${G3:-0}" || rc=$?
if [ "$rc" = 0 ] && has "$WORK/p5a.out" "\"generation_id\":\"$G3\""; then
    pass "P5 the current generation is acquirable after GC (exit 0)" P5.current
else fail "P5 acquire of the current generation rc=$rc: $(head -c 300 "$WORK/p5a.out")" P5.current; fi
cat > "$WORK/manifest.py" <<'PY'
import hashlib
import os
from pathlib import Path

rows = []
for path in sorted(Path("/graphs").rglob("*")):
    if path.is_file() and not path.is_symlink():
        info = path.stat()
        rows.append((str(path), info.st_size, info.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()))
print("files", len(rows))
print("digest", hashlib.sha256(repr(rows).encode()).hexdigest())
PY
manifest() {  # manifest NAME OUT
    bounded "$(cname)" "$2" "$2.err" --read-only -v "$VOL_GRAPHS":/graphs:ro -v "$WORK/manifest.py":/manifest.py:ro \
        --entrypoint /usr/local/bin/python "$IMAGE" -I /manifest.py
}
rc=0; manifest m1 "$WORK/m1.out" || rc=$?
rc2=0
atlas_run ro "$(cname)" "$WORK/p5b.out" "$WORK/p5b.err" -- jobs acquire $JOBARGS --generation "${G3:-0}" || rc2=$?
rc3=0; manifest m2 "$WORK/m2.out" || rc3=$?
M1=$(grep '^digest ' "$WORK/m1.out" | cut -d' ' -f2); M2=$(grep '^digest ' "$WORK/m2.out" | cut -d' ' -f2)
FILES_N=$(grep '^files ' "$WORK/m1.out" | cut -d' ' -f2)
if [ "$rc" = 0 ] && [ "$rc2" = 0 ] && [ "$rc3" = 0 ] && [ -n "$M1" ] && [ "$M1" != "$EMPTY_SHA" ] && [ "${FILES_N:-0}" -gt 0 ] && [ "$M1" = "$M2" ]; then
    pass "P5 a :ro acquire left the graphs manifest (bytes and mtimes of $FILES_N files) unchanged" P5.read_only
else fail "P5 read-only manifest rc=$rc/$rc2/$rc3 '$M1' '$M2' files='${FILES_N:-}'" P5.read_only; fi

# ------------------------------------------------------------------------------ P6 live serve unchanged across GC
rc=0; write_tree 4 || rc=$?
rc=0
atlas_run rw "$(cname)" "$WORK/p6j.out" "$WORK/p6j.err" -- jobs submit $JOBARGS --langs python --now 2000 || rc=$?
G4=$(hexf "$WORK/p6j.out" generation_id)
if [ "$rc" != 0 ] || [ -z "$G4" ]; then echo "  P6 setup job rc=$rc: $(head -c 200 "$WORK/p6j.err")"; fi
serve_driver() {  # serve_driver NAME OUT ROUNDS INTERVAL
    bounded "$1" "$2" "$2.err" --read-only --tmpfs /memex:uid=10001,gid=10001 -v "$REPO/mcp-server/tests/slices":/s41:ro -v "$VOL_REPO":/repo:ro -v "$VOL_GRAPHS":/graphs:ro \
        --entrypoint /usr/local/bin/python "$IMAGE" -I -c \
        'import sys;sys.path.insert(0,"/s41");import test_s41_support as s;raise SystemExit(s.production_main(sys.argv[1:]))' \
        --repo /repo --graph-root /graphs --memex-root /memex --rounds "$3" --interval "$4" --name f0
}
rc=0; serve_driver "$(cname)" "$WORK/p6a.out" 2 0 || rc=$?
DA=$(grep '^ROUND 0 ' "$WORK/p6a.out" | sed 's/.*sha256=//' || true)
DURING_NAME=$(cname)
NAMES="$NAMES $DURING_NAME"
serve_driver "$DURING_NAME" "$WORK/p6b.out" 8 1 &
DRV_PID=$!
sleep 3
rcg=0
atlas_run rw "$(cname)" "$WORK/p6g.out" "$WORK/p6g.err" -- jobs gc $JOBARGS --now 3000 || rcg=$?
rcb=0; wait "$DRV_PID" || rcb=$?
rc=0; serve_driver "$(cname)" "$WORK/p6c.out" 2 0 || rc=$?
DC=$(grep '^ROUND 0 ' "$WORK/p6c.out" | sed 's/.*sha256=//' || true)
OKB=$(grep -c '^ROUND [0-9]* ok=1 ' "$WORK/p6b.out" || true)
DIFFB=$(grep '^ROUND ' "$WORK/p6b.out" | sed 's/.*sha256=//' | sort -u | wc -l | tr -d ' ')
if [ "$rcg" = 0 ] && has "$WORK/p6g.out" "\"generation_id\":\"$G3\",\"reason\":\"outside_window\"" && [ -n "$DA" ] && [ "$DA" != "$EMPTY_SHA" ] \
    && [ "$rcb" = 0 ] && [ "$OKB" = 8 ] && [ "$DIFFB" = 1 ] && [ "$(grep '^ROUND ' "$WORK/p6b.out" | head -1 | sed 's/.*sha256=//')" = "$DA" ] \
    && [ "$DC" = "$DA" ] && has "$WORK/p6a.out" '^DRIVER: 2 ok, 0 failed$' && has "$WORK/p6c.out" '^DRIVER: 2 ok, 0 failed$'; then
    pass "P6 the read-only server answered search_symbol identically before, during (a GC retired G3) and after garbage collection ($DA)" P6
else fail "P6 serve invariance gc=$rcg during=$rcb ok=$OKB distinct=$DIFFB before='$DA' after='$DC': $(head -c 300 "$WORK/p6b.out.err")" P6; fi

# ------------------------------------------------------------------------------ P7 tools pin
rc=0
bounded "$(cname)" "$WORK/tools.json" "$WORK/tools.err" --read-only -v "$VOL_REPO":/repo:ro $ATLAS "$IMAGE" tools --repo /repo || rc=$?
TS=$(sha_of "$WORK/tools.json"); TB=$(wc -c < "$WORK/tools.json" | tr -d ' ')
if [ "$rc" = 0 ] && [ "$TS" = "$TOOLS_SHA" ] && [ "$TB" = "$TOOLS_BYTES" ]; then pass "P7 atlas-aci tools sha256 $TS, $TB bytes" P7
else fail "P7 tools rc=$rc sha '$TS' bytes '$TB' (want $TOOLS_SHA, $TOOLS_BYTES)" P7; fi

# ------------------------------------------------------------------------------ H8 host checks
cd "$REPO"
P=mcp-server/src/atlas_aci
H8_OK=1
[ "$(sha_of mcp-server/pyproject.toml)" = "$PYPROJECT_SHA" ] && [ "$(sha_of mcp-server/uv.lock)" = "$UV_LOCK_SHA" ] || H8_OK=0
FROZEN_LIST=$(git ls-files mcp-server/src | grep -v __pycache__ | grep -v -x -e "$P/__main__.py" -e "$P/storage/generations.py" -e "$P/storage/leases.py" -e "$P/storage/retention.py" -e "$P/indexing/jobs.py" -e "$P/indexing/job_runner.py" || true)
[ -n "$FROZEN_LIST" ] || H8_OK=0
# shellcheck disable=SC2086
git diff --exit-code "$BASE" -- $FROZEN_LIST mcp-server/Dockerfile .github/workflows scripts atlas > /dev/null || H8_OK=0
NAMES_CHANGED=$({ git diff --name-only "$BASE" -- mcp-server/src; git ls-files --others --exclude-standard -- mcp-server/src; } | grep -v __pycache__ | LC_ALL=C sort -u | tr '\n' ' ')
WANT="$P/__main__.py $P/indexing/job_runner.py $P/indexing/jobs.py $P/storage/generations.py $P/storage/leases.py $P/storage/retention.py "
[ "$NAMES_CHANGED" = "$WANT" ] || { H8_OK=0; echo "  changed src files: $NAMES_CHANGED"; }
NUM=$(git diff --numstat "$BASE" -- "$P/__main__.py" | awk '{d += $2; n++} END {print d + 0, n + 0}')
[ "$NUM" = "0 1" ] || H8_OK=0
if [ "$H8_OK" = 1 ]; then pass "H8 pyproject and uv.lock pins, frozen paths byte-identical to the base, exactly the six allowed src files, __main__.py has 0 deleted lines" H8
else fail "H8 host checks failed" H8; fi

echo "== passed $PASS_N, failed $FAIL_N"
printf '{"image":"%s","image_id":"%s","passed":%s,"failed":%s,"results":{%s}}\n' "$IMAGE" "$IMAGE_ID" "$PASS_N" "$FAIL_N" "${RESULTS%,}" > "$PROD_JSON"
if [ "$FAIL_N" = 0 ] && [ "$PASS_N" -gt 0 ]; then echo "RESULT: all checks passed"; exit 0; fi
echo "RESULT: failed"
exit 1
