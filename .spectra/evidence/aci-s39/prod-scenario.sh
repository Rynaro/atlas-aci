#!/bin/sh
# ACI-S39 production scenario.  Usage: prod-scenario.sh [IMAGE]      (default atlas-aci:s39-wip)
#
# Exercises the loopback Streamable HTTP transport INSIDE the production image: the HTTP server and
# its clients run in the same `--network none` container, on loopback. The image has entrypoint
# `atlas-aci`, user `atlas` (uid 10001), /usr/local/bin/python 3.12, no `python3`, no `timeout`
# and no pytest, so every container below passes --entrypoint explicitly, is `--rm`, `--network
# none`, is named with this shell's pid, and is bounded by a host-side watchdog (`docker kill`),
# never by a `timeout` binary.
#
# Checks (a check that depends on an earlier step FAILs when that step failed; no check accepts an
# empty input, and no digest may equal the empty-input sha256). All of them hold whether the tree
# is untracked, staged or committed (they read the working tree; git is used for the base diff).
#  H0 the image exists
#  H1 identity: mcp/uvicorn/starlette/sse-starlette/anyio versions, and the sha256 of server.py,
#     protocol/http.py, config.py, __main__.py and the two tools files in the image equal this tree
#  H2 `atlas-aci index` over the fixture repo exits 0 and writes graph files; host manifests recorded
#  H3 the driver (test_s39_support.production_main) runs E0-E9 in one container and reports
#     DRIVER: 10 passed, 0 failed:
#     E0 serving ping; E1 stdio vs http equality at 2025-11-25 and 2024-11-05; E2 evil Origin 403
#     before the body then a valid call; E3 two slow bodies hold both slots, a third is 503 with
#     Retry-After 1, finishing frees them; E4 413 (declared and chunked); E5 405/404; E6 421;
#     E7 SIGTERM exit 0 + http_stopped + refused; E8 --host 0.0.0.0 exit 2; E9 /proc/net/tcp has
#     exactly the 127.0.0.1:PORT LISTEN row and tcp6 none
#  H4 host-side: `serve --transport http --host 0.0.0.0` and `--host 192.0.2.1` exit 2 with Usage:
#  H5 the repo and graph-root manifests are unchanged after all runs (repo mounted :ro)
#  H6 the S38 stdio replay (modern.2025-11-25) still matches its golden, and the legacy
#     conversation against the modern golden does not (negative control)
#  H7 `atlas-aci tools` stdout equals the tools-JSON pin (sha256 and bytes)
#  H8 host checks: pyproject.toml / uv.lock pins, server.py and the other gated paths byte-identical
#     to the base commit, the changed set under mcp-server/src is exactly the five allowed files
# Environment: BOUND (seconds per container, default 300), PROD_JSON (output path), GOLDEN_DIR.
set -eu

IMAGE=${1:-atlas-aci:s39-wip}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
BASE=8ab6582e862d8c3bc29141104e58287bbee8f6be
BOUND=${BOUND:-300}
PORT=18765
PROD_JSON=${PROD_JSON:-$HERE/production-scenario.json}
TOOLS_SHA=94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56
TOOLS_BYTES=1631472
PYPROJECT_SHA=981f0784d6bc5c1502c91ab27657beb483460b97e066cd8846f51e98f1d7d92f
UV_LOCK_SHA=87ba265e76f382aaa4387da7ca5d89dc85eb3699b35ee4315e981302b19646fd
EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
GOLDEN_DIR=${GOLDEN_DIR:-$REPO/mcp-server/tests/fixtures/s38}
SRC=$REPO/mcp-server/src/atlas_aci
WORK=$(mktemp -d "${TMPDIR:-/tmp}/s39-prod.XXXXXX")
PASS_N=0
FAIL_N=0
SEQ=0
NAMES=""
RESULTS=""

cleanup() {
    for n in $NAMES; do docker rm -f "$n" > /dev/null 2>&1 || true; done
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

pass() { echo "PASS  $1"; PASS_N=$((PASS_N + 1)); RESULTS="$RESULTS\"$2\":\"PASS\","; }
fail() { echo "FAIL  $1"; FAIL_N=$((FAIL_N + 1)); RESULTS="$RESULTS\"$2\":\"FAIL\","; }
sha_of() {
    if command -v sha256sum > /dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
sha_stdin() {
    if command -v sha256sum > /dev/null 2>&1; then sha256sum | cut -d' ' -f1
    else shasum -a 256 | cut -d' ' -f1; fi
}
nonempty() { [ -s "$1" ]; }
# manifest_of DIR: one digest over the sorted (path, sha256) list of every file under DIR.
manifest_of() {
    (cd "$1" && find . -type f | LC_ALL=C sort | while read -r f; do
        printf '%s %s\n' "$f" "$(sha_of "$f")"
    done) | sha_stdin
}
count_files() { find "$1" -type f | wc -l | tr -d ' '; }

# bounded NAME OUT ERR docker-run-args...   -> the container's exit code; 124 when killed.
bounded() {
    name=$1; out=$2; err=$3; shift 3
    NAMES="$NAMES $name"
    docker run --rm --name "$name" --network none "$@" > "$out" 2> "$err" &
    pid=$!
    n=0
    while kill -0 "$pid" 2> /dev/null && [ "$n" -lt "$BOUND" ]; do sleep 1; n=$((n + 1)); done
    if kill -0 "$pid" 2> /dev/null; then
        docker kill "$name" > /dev/null 2>&1 || true   # the container may not exist yet: ignore
        kill "$pid" 2> /dev/null || true
        wait "$pid" 2> /dev/null || true
        return 124
    fi
    rc=0
    wait "$pid" || rc=$?
    return "$rc"
}
cname() { SEQ=$((SEQ + 1)); echo "s39-prod-$$-$SEQ"; }

# ------------------------------------------------------------------------------ H0 image
IMAGE_ID=$(docker image inspect "$IMAGE" --format '{{.Id}}' 2> /dev/null || true)
if [ -n "$IMAGE_ID" ]; then pass "H0 image $IMAGE exists ($IMAGE_ID)" H0
else fail "H0 image $IMAGE not found" H0; echo "RESULT: failed"; exit 1; fi

mkdir -p "$WORK/repo" "$WORK/graphs" "$WORK/memex"
printf 'def target():\n return 1\ndef caller():\n target()\n' > "$WORK/repo/a.py"
TZ=UTC touch -t 202311142213.20 "$WORK/repo/a.py"
chmod 755 "$WORK/repo"
chmod 777 "$WORK/graphs" "$WORK/memex"
[ "$(wc -c < "$WORK/repo/a.py" | tr -d ' ')" = 48 ] || { echo "fixture repo is not the 48-byte s38-mini.v1 file"; exit 1; }

# ------------------------------------------------------------------------------ H1 identity
cat > "$WORK/identity.py" <<'PY'
import hashlib
import importlib.metadata as m
from pathlib import Path

import atlas_aci

root = Path(atlas_aci.__file__).parent
for dist in ("mcp", "uvicorn", "starlette", "sse-starlette", "anyio"):
    print("version", dist, m.version(dist))
for name in (
    "server.py", "protocol/http.py", "config.py", "__main__.py",
    "tools/search_text.py", "tools/test_dry_run.py",
):
    print("sha", name, hashlib.sha256((root / name).read_bytes()).hexdigest())
PY
rc=0
bounded "$(cname)" "$WORK/h1.out" "$WORK/h1.err" --entrypoint /usr/local/bin/python \
    -v "$WORK/identity.py":/identity.py:ro "$IMAGE" -I /identity.py || rc=$?
if [ "$rc" = 0 ] && nonempty "$WORK/h1.out"; then
    H1_OK=1
    for want in "mcp 1.30.0" "uvicorn 0.44.0" "starlette 1.3.1" "sse-starlette 3.3.4" "anyio 4.16.0"; do
        grep -q "^version $want\$" "$WORK/h1.out" || { H1_OK=0; echo "  version line missing: $want"; }
    done
    if [ "$H1_OK" = 1 ]; then pass "H1 locked dependency versions in the image" H1.versions
    else fail "H1 dependency versions differ" H1.versions; fi
    SHA_OK=1
    for f in server.py protocol/http.py config.py __main__.py tools/search_text.py tools/test_dry_run.py; do
        want=$(sha_of "$SRC/$f")
        have=$(grep "^sha $f " "$WORK/h1.out" | cut -d' ' -f3)
        if [ -n "$want" ] && [ "$want" != "$EMPTY_SHA" ] && [ "$want" = "$have" ]; then :
        else SHA_OK=0; echo "  sha mismatch for $f: host '$want' image '$have'"; fi
    done
    if [ "$SHA_OK" = 1 ]; then pass "H1 the six changed or gated source files in the image equal this tree" H1.sha
    else fail "H1 image sources differ from this tree" H1.sha; fi
else
    fail "H1 identity container exit $rc: $(head -c 300 "$WORK/h1.err")" H1.versions
    fail "H1 skipped: identity container failed" H1.sha
fi

# ------------------------------------------------------------------------------ H2 index
rc=0
bounded "$(cname)" "$WORK/h2.out" "$WORK/h2.err" --read-only -v "$WORK/repo":/repo:ro -v "$WORK/graphs":/graphs \
    --entrypoint atlas-aci "$IMAGE" index --repo /repo --graph-root /graphs || rc=$?
GRAPH_FILES=$(count_files "$WORK/graphs")
if [ "$rc" = 0 ] && [ "$GRAPH_FILES" -gt 0 ]; then
    pass "H2 index exit 0, $GRAPH_FILES graph file(s)" H2; H2_OK=1
else fail "H2 index exit $rc, $GRAPH_FILES graph file(s): $(head -c 300 "$WORK/h2.err")" H2; H2_OK=0; fi
REPO_MANIFEST_BEFORE=$(manifest_of "$WORK/repo")
GRAPH_MANIFEST_BEFORE=$(manifest_of "$WORK/graphs")

# ------------------------------------------------------------------------------ H3 driver
DRIVER_OK=0
if [ "$H2_OK" != 1 ]; then
    fail "H3 skipped: H2 failed" H3
else
    rc=0
    bounded "$(cname)" "$WORK/h3.out" "$WORK/h3.err" --read-only -e PYTHONDONTWRITEBYTECODE=1 \
        --entrypoint /usr/local/bin/python \
        -v "$REPO/mcp-server/tests/slices":/s39:ro -v "$WORK/repo":/repo:ro -v "$WORK/graphs":/graphs:ro \
        -v "$WORK/memex":/memex \
        "$IMAGE" -I -c 'import sys;sys.path.insert(0,"/s39");import test_s39_support as s;raise SystemExit(s.production_main(sys.argv[1:]))' \
        --repo /repo --graph-root /graphs --memex-root /memex --port "$PORT" || rc=$?
    sed 's/^/      /' "$WORK/h3.out" || true
    ALL=1
    for e in E0 E1 E2 E3 E4 E5 E6 E7 E8 E9; do
        if grep -q "^PASS $e " "$WORK/h3.out" 2> /dev/null; then pass "H3 $(grep "^PASS $e " "$WORK/h3.out" | cut -c6-120)" "H3.$e"
        else fail "H3 $e did not pass: $(grep "^FAIL $e " "$WORK/h3.out" 2> /dev/null | head -1 | cut -c1-260)" "H3.$e"; ALL=0; fi
    done
    if [ "$rc" = 0 ] && [ "$ALL" = 1 ] && grep -q '^DRIVER: 10 passed, 0 failed$' "$WORK/h3.out"; then
        pass "H3 driver exit 0: DRIVER: 10 passed, 0 failed" H3; DRIVER_OK=1
    else fail "H3 driver rc=$rc: $(tail -2 "$WORK/h3.out" | tr '\n' ' ') $(head -c 300 "$WORK/h3.err")" H3; fi
fi

# ------------------------------------------------------------------------------ H4 non-loopback CLI
for host in 0.0.0.0 192.0.2.1; do
    rc=0
    bounded "$(cname)" "$WORK/h4-$host.out" "$WORK/h4-$host.err" --read-only -v "$WORK/repo":/repo:ro \
        --entrypoint atlas-aci "$IMAGE" serve --transport http --host "$host" --repo /repo || rc=$?
    if [ "$rc" = 2 ] && [ ! -s "$WORK/h4-$host.out" ] && grep -q 'Usage:' "$WORK/h4-$host.err" && grep -q 'reason:' "$WORK/h4-$host.err"; then
        pass "H4 --host $host exits 2 with Usage: and a reason, nothing on stdout" "H4.$host"
    else fail "H4 --host $host rc=$rc stdout=$(wc -c < "$WORK/h4-$host.out" | tr -d ' ') stderr: $(head -c 200 "$WORK/h4-$host.err")" "H4.$host"; fi
done

# ------------------------------------------------------------------------------ H6 S38 stdio preserved
replay() {  # replay CASE OUTFILE [GOLDEN_CASE]
    case_name=$1; out=$2; golden_case=${3:-}
    extra=""
    [ -n "$golden_case" ] && extra="--golden-case $golden_case"
    # shellcheck disable=SC2086
    bounded "$(cname)" "$out" "$out.err" --read-only --entrypoint /usr/local/bin/python \
        -v "$REPO/mcp-server/tests/slices":/s39:ro -v "$GOLDEN_DIR":/golden:ro \
        -v "$WORK/repo":/repo:ro -v "$WORK/graphs":/graphs:ro -v "$WORK/memex":/memex \
        "$IMAGE" -I -c 'import sys;sys.path.insert(0,"/s39");import test_s38_support as s;raise SystemExit(s.production_main(sys.argv[1:]))' \
        --case "$case_name" --golden /golden $extra -- \
        atlas-aci serve --repo /repo --graph-root /graphs --memex-root /memex
}
if [ "$H2_OK" != 1 ]; then
    fail "H6 skipped: H2 failed" H6.match; fail "H6 skipped: H2 failed" H6.control
else
    rc=0
    replay modern.2025-11-25 "$WORK/h6.out" || rc=$?
    d=$(sed -n '1s/.*"sha256": "\([0-9a-f]*\)".*/\1/p' "$WORK/h6.out")
    g=$(sha_of "$GOLDEN_DIR/transcripts/modern.2025-11-25.v1.json")
    line=$(sed -n 1p "$WORK/h6.out")
    case $line in *'"match": true'*) m=1 ;; *) m=0 ;; esac
    if [ "$rc" = 0 ] && [ "$m" = 1 ] && [ -n "$d" ] && [ "$d" != "$EMPTY_SHA" ] && [ "$d" = "$g" ]; then
        pass "H6 stdio modern.2025-11-25 still matches its golden (digest $d)" H6.match
    else fail "H6 stdio replay rc=$rc match=$m digest='$d' golden='$g': $(head -c 300 "$WORK/h6.out.err")" H6.match; fi
    rc=0
    replay legacy.2024-11-05 "$WORK/h6c.out" modern.2025-11-25 || rc=$?
    case $(sed -n 1p "$WORK/h6c.out") in *'"match": false'*) nm=1 ;; *) nm=0 ;; esac
    if [ "$rc" = 1 ] && [ "$nm" = 1 ]; then pass "H6 negative control: the legacy conversation against the modern golden exits 1 with match:false" H6.control
    else fail "H6 negative control rc=$rc match_false=$nm" H6.control; fi
fi

# ------------------------------------------------------------------------------ H7 tools pin
rc=0
bounded "$(cname)" "$WORK/tools.json" "$WORK/tools.err" --read-only -v "$WORK/repo":/repo:ro \
    --entrypoint atlas-aci "$IMAGE" tools --repo /repo || rc=$?
TS=$(sha_of "$WORK/tools.json")
TB=$(wc -c < "$WORK/tools.json" | tr -d ' ')
if [ "$rc" = 0 ] && [ "$TS" = "$TOOLS_SHA" ] && [ "$TB" = "$TOOLS_BYTES" ]; then pass "H7 atlas-aci tools sha256 $TS, $TB bytes" H7
else fail "H7 tools rc=$rc sha '$TS' bytes '$TB' (want $TOOLS_SHA, $TOOLS_BYTES)" H7; fi

# ------------------------------------------------------------------------------ H5 manifests unchanged
REPO_MANIFEST_AFTER=$(manifest_of "$WORK/repo")
GRAPH_MANIFEST_AFTER=$(manifest_of "$WORK/graphs")
if [ "$H2_OK" = 1 ] && [ "$REPO_MANIFEST_BEFORE" = "$REPO_MANIFEST_AFTER" ] && [ "$GRAPH_MANIFEST_BEFORE" = "$GRAPH_MANIFEST_AFTER" ] \
    && [ "$GRAPH_MANIFEST_AFTER" != "$EMPTY_SHA" ] && [ "$REPO_MANIFEST_AFTER" != "$EMPTY_SHA" ]; then
    pass "H5 repo ($REPO_MANIFEST_AFTER) and graph root ($GRAPH_MANIFEST_AFTER) manifests are unchanged by every run" H5
else fail "H5 manifests changed or empty (repo $REPO_MANIFEST_BEFORE -> $REPO_MANIFEST_AFTER, graphs $GRAPH_MANIFEST_BEFORE -> $GRAPH_MANIFEST_AFTER)" H5; fi

# ------------------------------------------------------------------------------ H8 host checks
P=$(sha_of "$REPO/mcp-server/pyproject.toml")
U=$(sha_of "$REPO/mcp-server/uv.lock")
if [ "$P" = "$PYPROJECT_SHA" ] && [ "$U" = "$UV_LOCK_SHA" ]; then pass "H8 pyproject.toml and uv.lock are byte-identical to the pins" H8.lock
else fail "H8 pyproject.toml sha $P / uv.lock sha $U" H8.lock; fi
if (cd "$REPO" && git diff --exit-code "$BASE" -- mcp-server/src/atlas_aci/server.py mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile .github/workflows scripts atlas docs > /dev/null 2>&1); then
    pass "H8 server.py, pyproject, uv.lock, Dockerfile, workflows, scripts, atlas and docs are byte-identical to the base commit" H8.src
else fail "H8 a gated path differs from the base commit" H8.src; fi
# independent of staging and commit state: tracked diff vs the base plus untracked files
CHANGED=$(cd "$REPO" && { git diff --name-only "$BASE" -- mcp-server/src; git ls-files --others --exclude-standard -- mcp-server/src; } | grep -v __pycache__ | LC_ALL=C sort -u | tr '\n' ' ')
case $CHANGED in
    "mcp-server/src/atlas_aci/__main__.py mcp-server/src/atlas_aci/config.py mcp-server/src/atlas_aci/protocol/http.py mcp-server/src/atlas_aci/tools/search_text.py mcp-server/src/atlas_aci/tools/test_dry_run.py ") pass "H8 changed source files are exactly the five allowed ones" H8.names ;;
    *) fail "H8 changed source files: '$CHANGED'" H8.names ;;
esac
DELETED=$(cd "$REPO" && git diff --numstat "$BASE" -- mcp-server/src/atlas_aci/tools/search_text.py mcp-server/src/atlas_aci/tools/test_dry_run.py | awk '{d += $2; n++} END {print d + 0, n + 0}')
if [ "$DELETED" = "0 2" ]; then pass "H8 the two tools files have 0 deleted lines versus the base (additions only)" H8.additions
else fail "H8 tools numstat deletions/files: '$DELETED'" H8.additions; fi

# ------------------------------------------------------------------------------ result
echo "image: $IMAGE ($IMAGE_ID)"
echo "checks: $PASS_N passed, $FAIL_N failed"
{
    printf '{"image":"%s","image_id":"%s","passed":%s,"failed":%s,"driver_ok":%s,"checks":{%s}}\n' \
        "$IMAGE" "$IMAGE_ID" "$PASS_N" "$FAIL_N" "$DRIVER_OK" "${RESULTS%,}"
} > "$PROD_JSON"
if [ "$FAIL_N" = 0 ] && [ "$PASS_N" -gt 0 ]; then
    echo "RESULT: all checks passed"
    exit 0
fi
echo "RESULT: $FAIL_N check(s) failed"
exit 1
