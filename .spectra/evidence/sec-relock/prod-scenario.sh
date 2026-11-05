#!/bin/sh
# sec-relock SIBLING of .spectra/evidence/aci-s38/prod-scenario.sh (that script is unedited).
# Usage: prod-scenario.sh [IMAGE]      (default atlas-aci:relock)
# Differences from the S38 script, each forced by the re-lock and none weakening a check:
#  - S1 expects mcp 1.30.0 (the re-locked SDK); every other expected line is unchanged;
#  - S7 runs pytest in atlas-aci-dev-relock (the dev image built from the new lock);
#  - S8.lock pins the new pyproject.toml / uv.lock shas (981f0784..., 87ba265e...);
#  - S8.src still requires every gated path byte-identical to the S38 base commit, except that
#    pyproject.toml and uv.lock (pinned by sha in S8.lock instead) and docs/evidence/v3/README.md
#    (the one SDK-row sentence) are excluded from the git comparison.
# ---- original header follows ----
# ACI-S38 production scenario.  Usage: prod-scenario.sh [IMAGE]      (default atlas-aci:s38-wip)
#
# Exercises the server INSIDE the production image over real stdio: a legacy (2024-11-05) and a
# modern (2025-11-25) conversation, the unsupported (2099-01-01 and integer) and the malformed
# (missing version, pre-initialize) cases, replayed by the committed driver
# (mcp-server/tests/slices/test_s38_support.py: production_main) against `atlas-aci serve` and
# compared, after the closed normalisation rules, with the committed golden transcripts.
#
# The production image has entrypoint `atlas-aci`, user `atlas` (uid 10001), /usr/local/bin/python
# 3.12, no `python3`, no `timeout` and no pytest, so every container below passes --entrypoint
# explicitly where it needs python, is `--rm`, `--network none`, named with this shell's pid, and
# is bounded by a host-side watchdog (`docker kill`), never by a `timeout` binary.
#
# Checks (a check that depends on an earlier step FAILs when that step failed; no check accepts an
# empty input, and no digest may equal the empty-input sha256):
#  S0 the image exists (its id and the mcp version are printed)
#  S1 identity: mcp/SUPPORTED/LATEST/REVISIONS/STRUCTURED_VERSIONS/classify_initialize records equal
#     the expected text, and sha256 of server.py, revisions.py and results.py in the image equal the
#     sha256 of the files in this checkout
#  S2 `atlas-aci index` over a fixture repo exits 0 and writes graph files
#  S3 all 8 golden cases replay with match:true, no Traceback, and a digest equal to the golden file
#  S4 two fresh replays of legacy.2024-11-05 and modern.2025-11-25 give the same digests as S3
#  S5 negative control: the legacy conversation compared with the MODERN golden exits 1 with
#     match:false; the raw 2099 initialize response is the 2025-11-25 counter-offer; the raw
#     missing-version response is -32602
#  S6 `atlas-aci tools` stdout equals the tools-JSON pin (sha256 and bytes)
#  S7 the docker regression tests (test_s11_docker, test_s13_docker) pass against this image, and
#     pytest reports no skipped test.  pytest runs in the dev container (atlas-aci-dev-relock) with the
#     docker socket and a Linux docker CLI binary mounted: DOCKER_CLI_BIN is required (FAIL when
#     unset or missing).  PROD_SKIP_S7=1 skips S7 explicitly and says so.
#  S8 host checks: pyproject.toml / uv.lock hashes and no change under the gated source paths
# Environment: BOUND (seconds per container, default 300), PROD_JSON (output path), GOLDEN_DIR
# (default: the committed fixtures; a corrupted copy must make S3 and S4 FAIL),
# DOCKER_CLI_BIN, PROD_SKIP_S7.
set -eu

IMAGE=${1:-atlas-aci:relock}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
BASE=2db6dd1a3cba6589bf4efc9ad83620e39bacb289
BOUND=${BOUND:-300}
PROD_JSON=${PROD_JSON:-$HERE/production-scenario.json}
TOOLS_SHA=94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56
TOOLS_BYTES=1631472
PYPROJECT_SHA=981f0784d6bc5c1502c91ab27657beb483460b97e066cd8846f51e98f1d7d92f
UV_LOCK_SHA=87ba265e76f382aaa4387da7ca5d89dc85eb3699b35ee4315e981302b19646fd
EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
CASES="legacy.2024-11-05 legacy.2025-03-26 modern.2025-06-18 modern.2025-11-25 unsupported.2099-01-01 unsupported.integer malformed.missing-version malformed.pre-initialize"
# GOLDEN_DIR may point at a modified copy of the fixtures (the negative control of the dry runs).
GOLDEN_DIR=${GOLDEN_DIR:-$REPO/mcp-server/tests/fixtures/s38}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/relock-prod.XXXXXX")
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
nonempty() { [ -s "$1" ]; }

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
cname() { SEQ=$((SEQ + 1)); echo "relock-prod-$$-$SEQ"; }

# ------------------------------------------------------------------------------ S0 image
IMAGE_ID=$(docker image inspect "$IMAGE" --format '{{.Id}}' 2> /dev/null || true)
if [ -n "$IMAGE_ID" ]; then pass "S0 image $IMAGE exists ($IMAGE_ID)" S0
else fail "S0 image $IMAGE not found" S0; echo "RESULT: failed"; exit 1; fi

mkdir -p "$WORK/repo" "$WORK/graphs" "$WORK/memex" "$WORK/out"
printf 'def target():\n return 1\ndef caller():\n target()\n' > "$WORK/repo/a.py"
chmod 755 "$WORK/repo"
chmod 777 "$WORK/graphs" "$WORK/memex" "$WORK/out"
[ "$(wc -c < "$WORK/repo/a.py" | tr -d ' ')" = 48 ] || { echo "fixture repo is not the 48-byte s38-mini.v1 file"; exit 1; }

# ------------------------------------------------------------------------------ S1 identity
cat > "$WORK/identity.py" <<'PY'
import hashlib
import importlib.metadata as m
import json
from pathlib import Path

import atlas_aci
from mcp import types
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS as S

from atlas_aci.protocol import results
from atlas_aci.protocol import revisions as r

root = Path(atlas_aci.__file__).parent
base = {"capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}
print("mcp", m.version("mcp"))
print("supported", json.dumps(S))
print("latest", types.LATEST_PROTOCOL_VERSION)
print("revisions", json.dumps([[x.id, x.family, x.structured_results, x.status] for x in r.REVISIONS]))
print("structured", json.dumps(sorted(results.STRUCTURED_VERSIONS)))
for label, params in (
    ("2024-11-05", {**base, "protocolVersion": "2024-11-05"}),
    ("2025-11-25", {**base, "protocolVersion": "2025-11-25"}),
    ("2099-01-01", {**base, "protocolVersion": "2099-01-01"}),
    ("20241105", {**base, "protocolVersion": 20241105}),
    ("empty", {}),
):
    print("record", label, r.classify_initialize(params).record_bytes().decode())
for name in ("server.py", "protocol/revisions.py", "protocol/results.py"):
    print("sha", name, hashlib.sha256((root / name).read_bytes()).hexdigest())
PY
EXPECTED_S1=$(cat <<'EOF'
mcp 1.30.0
supported ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"]
latest 2025-11-25
revisions [["2024-11-05", "legacy", false, "retained_legacy"], ["2025-03-26", "legacy", false, "retained_legacy"], ["2025-06-18", "modern", true, "verified_modern"], ["2025-11-25", "modern", true, "selected"]]
structured ["2025-06-18", "2025-11-25"]
EOF
)
rc=0
bounded "$(cname)" "$WORK/s1.out" "$WORK/s1.err" --entrypoint python -v "$WORK/identity.py":/identity.py:ro "$IMAGE" -I /identity.py || rc=$?
if [ "$rc" = 0 ] && nonempty "$WORK/s1.out"; then
    head -5 "$WORK/s1.out" > "$WORK/s1.head"
    printf '%s\n' "$EXPECTED_S1" > "$WORK/s1.expect"
    if cmp -s "$WORK/s1.head" "$WORK/s1.expect"; then pass "S1 mcp, SDK matrix, REVISIONS and STRUCTURED_VERSIONS equal the expected text" S1a
    else fail "S1 identity text differs: $(diff "$WORK/s1.expect" "$WORK/s1.head" | head -5)" S1a; fi
    # the records are compared field by field against the expected outcome and reason
    expect_record() {
        line=$(grep "^record $1 " "$WORK/s1.out" || true)
        case $line in
            *'"outcome":"'"$2"'"'*'"reason":"'"$3"'"'*) pass "S1 classify_initialize($1) -> $2/$3" "S1.record.$1" ;;
            *) fail "S1 classify_initialize($1) is '$line', expected $2/$3" "S1.record.$1" ;;
        esac
    }
    expect_record 2024-11-05 supported listed_revision
    expect_record 2025-11-25 supported listed_revision
    expect_record 2099-01-01 counter_offer unknown_revision
    expect_record 20241105 counter_offer integer_revision
    expect_record empty malformed missing_revision
    S1_SHA_OK=1
    for pair in "server.py:server.py" "protocol/revisions.py:protocol/revisions.py" "protocol/results.py:protocol/results.py"; do
        f=${pair%%:*}
        want=$(sha_of "$REPO/mcp-server/src/atlas_aci/$f")
        have=$(grep "^sha $f " "$WORK/s1.out" | cut -d' ' -f3)
        if [ -n "$want" ] && [ "$want" != "$EMPTY_SHA" ] && [ "$want" = "$have" ]; then :
        else S1_SHA_OK=0; echo "  sha mismatch for $f: host '$want' image '$have'"; fi
    done
    if [ "$S1_SHA_OK" = 1 ]; then pass "S1 server.py, revisions.py and results.py in the image equal this checkout" S1b
    else fail "S1 image sources differ from this checkout" S1b; fi
else
    fail "S1 identity container exit $rc: $(head -c 300 "$WORK/s1.err")" S1a
fi

# ------------------------------------------------------------------------------ S2 index
rc=0
bounded "$(cname)" "$WORK/s2.out" "$WORK/s2.err" --read-only -v "$WORK/repo":/repo:ro -v "$WORK/graphs":/graphs "$IMAGE" index --repo /repo --graph-root /graphs || rc=$?
GRAPH_FILES=$(find "$WORK/graphs" -type f | wc -l | tr -d ' ')
if [ "$rc" = 0 ] && [ "$GRAPH_FILES" -gt 0 ]; then pass "S2 index exit 0, $GRAPH_FILES graph file(s)" S2; S2_OK=1
else fail "S2 index exit $rc, $GRAPH_FILES graph file(s): $(head -c 300 "$WORK/s2.err")" S2; S2_OK=0; fi

# ------------------------------------------------------------------------------ S3 replay
replay() {  # replay CASE OUTFILE [GOLDEN_CASE]  -> exit code of the driver
    case_name=$1; out=$2; golden_case=${3:-}
    extra=""
    [ -n "$golden_case" ] && extra="--golden-case $golden_case"
    # shellcheck disable=SC2086
    bounded "$(cname)" "$out" "$out.err" --read-only --entrypoint python \
        -v "$REPO/mcp-server/tests/slices":/s38:ro -v "$GOLDEN_DIR":/golden:ro \
        -v "$WORK/repo":/repo:ro -v "$WORK/graphs":/graphs -v "$WORK/memex":/memex \
        "$IMAGE" -I -c 'import sys;sys.path.insert(0,"/s38");import test_s38_support as s;raise SystemExit(s.production_main(sys.argv[1:]))' \
        --case "$case_name" --golden /golden $extra -- \
        atlas-aci serve --repo /repo --graph-root /graphs --memex-root /memex
}
digest_of() { sed -n '1s/.*"sha256": "\([0-9a-f]*\)".*/\1/p' "$1"; }
FIRST_LINE_FIELD() { sed -n 1p "$1"; }
for c in $CASES; do
    if [ "$S2_OK" != 1 ]; then fail "S3 $c skipped: S2 failed" "S3.$c"; continue; fi
    rc=0
    replay "$c" "$WORK/s3-$c.out" || rc=$?
    d=$(digest_of "$WORK/s3-$c.out")
    g=$(sha_of "$GOLDEN_DIR/transcripts/$c.v1.json")
    line=$(FIRST_LINE_FIELD "$WORK/s3-$c.out")
    case $line in *'"match": true'*) m=1 ;; *) m=0 ;; esac
    case $line in *'"traceback": false'*) tb=0 ;; *) tb=1 ;; esac
    if [ "$rc" = 0 ] && [ "$m" = 1 ] && [ "$tb" = 0 ] && [ -n "$d" ] && [ "$d" != "$EMPTY_SHA" ] && [ "$d" = "$g" ]; then
        pass "S3 $c match:true digest ${d%????????????????????????????????????????????????????}..." "S3.$c"
    else fail "S3 $c rc=$rc match=$m traceback=$tb digest='$d' golden='$g': $(head -c 300 "$WORK/s3-$c.out.err")" "S3.$c"; fi
done

# ------------------------------------------------------------------------------ S4 replay twice
for c in legacy.2024-11-05 modern.2025-11-25; do
    if [ "$S2_OK" != 1 ] || [ ! -s "$WORK/s3-$c.out" ]; then fail "S4 $c skipped: S2/S3 failed" "S4.$c"; continue; fi
    rc=0
    replay "$c" "$WORK/s4-$c.out" || rc=$?
    d1=$(digest_of "$WORK/s3-$c.out")
    d2=$(digest_of "$WORK/s4-$c.out")
    if [ "$rc" = 0 ] && [ -n "$d1" ] && [ "$d1" != "$EMPTY_SHA" ] && [ "$d1" = "$d2" ]; then pass "S4 $c second fresh replay has the same digest" "S4.$c"
    else fail "S4 $c rc=$rc digests '$d1' vs '$d2'" "S4.$c"; fi
done
L=$(digest_of "$WORK/s3-legacy.2024-11-05.out")
M=$(digest_of "$WORK/s3-modern.2025-11-25.out")
if [ -n "$L" ] && [ -n "$M" ] && [ "$L" != "$M" ]; then pass "S4 the legacy and modern transcripts differ" S4.differ
else fail "S4 legacy and modern digests are empty or equal ('$L' '$M')" S4.differ; fi

# ------------------------------------------------------------------------------ S5 negative control
if [ "$S2_OK" = 1 ]; then
    rc=0
    replay legacy.2024-11-05 "$WORK/s5.out" modern.2025-11-25 || rc=$?
    case $(sed -n 1p "$WORK/s5.out") in *'"match": false'*) nm=1 ;; *) nm=0 ;; esac
    if [ "$rc" = 1 ] && [ "$nm" = 1 ]; then pass "S5 legacy conversation against the modern golden exits 1 with match:false" S5.control
    else fail "S5 negative control rc=$rc match_false=$nm" S5.control; fi
else fail "S5 negative control skipped: S2 failed" S5.control; fi
if grep -q '"protocolVersion":"2025-11-25"' "$WORK/s3-unsupported.2099-01-01.out" 2> /dev/null \
    && grep -q '"serverInfo"' "$WORK/s3-unsupported.2099-01-01.out"; then
    pass "S5 the raw 2099-01-01 initialize response is the 2025-11-25 counter-offer" S5.counter
else fail "S5 the 2099-01-01 response is not the counter-offer" S5.counter; fi
if grep -q '"code":-32602' "$WORK/s3-malformed.missing-version.out" 2> /dev/null; then
    pass "S5 the raw missing-version response is -32602" S5.malformed
else fail "S5 the missing-version response is not -32602" S5.malformed; fi

# ------------------------------------------------------------------------------ S6 tools pin
rc=0
bounded "$(cname)" "$WORK/tools.json" "$WORK/tools.err" --read-only -v "$WORK/repo":/repo:ro "$IMAGE" tools --repo /repo || rc=$?
TS=$(sha_of "$WORK/tools.json")
TB=$(wc -c < "$WORK/tools.json" | tr -d ' ')
if [ "$rc" = 0 ] && [ "$TS" = "$TOOLS_SHA" ] && [ "$TB" = "$TOOLS_BYTES" ]; then pass "S6 atlas-aci tools sha256 $TS, $TB bytes" S6
else fail "S6 tools rc=$rc sha '$TS' bytes '$TB' (want $TOOLS_SHA, $TOOLS_BYTES)" S6; fi

# ------------------------------------------------------------------------------ S7 docker tests
if [ "${PROD_SKIP_S7:-0}" = 1 ]; then
    echo "SKIP  S7 explicitly skipped (PROD_SKIP_S7=1)"
    RESULTS="$RESULTS\"S7\":\"SKIPPED_BY_REQUEST\","
elif [ -z "${DOCKER_CLI_BIN:-}" ] || [ ! -f "$DOCKER_CLI_BIN" ]; then
    fail "S7 DOCKER_CLI_BIN must point at a Linux docker CLI binary (got '${DOCKER_CLI_BIN:-}'); or set PROD_SKIP_S7=1" S7
else
    # pytest runs in the dev container (no host Python). The docker socket and a Linux docker CLI
    # are mounted so the tests start sibling containers; $BT is mounted at the identical path so
    # their bind mounts resolve on the daemon.
    BT=$(cd "$WORK" && pwd -P)/s7
    mkdir -p "$BT/hc"
    chmod 777 "$BT" "$BT/hc"
    rc=0
    bounded "relock-s7-$$" "$WORK/s7.out" "$WORK/s7.err" -e HOME=/hc -e PYTHONPATH=/ws/mcp-server/src \
        -e ATLAS_ACI_S11_DOCKER_IMAGE="$IMAGE" -e ATLAS_ACI_S13_DOCKER_IMAGE="$IMAGE" \
        -v /var/run/docker.sock:/var/run/docker.sock -v "$DOCKER_CLI_BIN":/usr/local/bin/docker:ro \
        -v "$BT/hc":/hc -v "$BT":"$BT" -v "$REPO":/ws -w /ws/mcp-server atlas-aci-dev-relock \
        /opt/venv/bin/python -m pytest -p no:cacheprovider -q -rs --basetemp="$BT/t" \
        tests/slices/test_s11_docker.py tests/slices/test_s13_docker.py || rc=$?
    if [ "$rc" = 0 ] && grep -q ' passed' "$WORK/s7.out" && ! grep -qi 'skipped' "$WORK/s7.out"; then
        pass "S7 $(tail -1 "$WORK/s7.out")" S7
    else fail "S7 pytest rc=$rc: $(tail -3 "$WORK/s7.out" | tr '\n' ' ') $(head -c 200 "$WORK/s7.err")" S7; fi
fi

# ------------------------------------------------------------------------------ S8 host checks
P=$(sha_of "$REPO/mcp-server/pyproject.toml")
U=$(sha_of "$REPO/mcp-server/uv.lock")
if [ "$P" = "$PYPROJECT_SHA" ]; then
    if [ "$U" = "$UV_LOCK_SHA" ]; then pass "S8 pyproject.toml and uv.lock are byte-identical to the pins" S8.lock
    else fail "S8 uv.lock sha $U" S8.lock; fi
else fail "S8 pyproject.toml sha $P" S8.lock; fi
GATED="mcp-server/Dockerfile .github/workflows scripts atlas docs mcp-server/src/atlas_aci/__main__.py mcp-server/src/atlas_aci/codegraph.py mcp-server/src/atlas_aci/config.py mcp-server/src/atlas_aci/enforcement.py mcp-server/src/atlas_aci/query mcp-server/src/atlas_aci/tools mcp-server/src/atlas_aci/storage mcp-server/src/atlas_aci/protocol/__init__.py mcp-server/src/atlas_aci/protocol/graph.py mcp-server/src/atlas_aci/protocol/resources.py mcp-server/src/atlas_aci/protocol/context.py mcp-server/src/atlas_aci/protocol/impact.py"
# shellcheck disable=SC2086
if (cd "$REPO" && git diff --exit-code "$BASE" -- $GATED ':(exclude)docs/evidence/v3/README.md' > /dev/null 2>&1); then pass "S8 gated source paths are byte-identical to the base commit" S8.src
else fail "S8 a gated path differs from the base commit" S8.src; fi
# independent of staging and commit state: tracked diff vs the base plus untracked files
NAMES_CHANGED=$(cd "$REPO" && { git diff --name-only "$BASE" -- mcp-server/src; git ls-files --others --exclude-standard -- mcp-server/src; } | grep -v __pycache__ | LC_ALL=C sort -u | tr '\n' ' ')
case $NAMES_CHANGED in
    "mcp-server/src/atlas_aci/protocol/results.py mcp-server/src/atlas_aci/protocol/revisions.py mcp-server/src/atlas_aci/server.py ") pass "S8 changed source files are exactly protocol/results.py, protocol/revisions.py and server.py" S8.names ;;
    *) fail "S8 changed source files: '$NAMES_CHANGED'" S8.names ;;
esac

# ------------------------------------------------------------------------------ result
echo "image: $IMAGE ($IMAGE_ID)  mcp: $(sed -n 1p "$WORK/s1.out" 2> /dev/null | cut -d' ' -f2)"
echo "checks: $PASS_N passed, $FAIL_N failed"
{
    printf '{"image":"%s","image_id":"%s","passed":%s,"failed":%s,"checks":{%s}}\n' \
        "$IMAGE" "$IMAGE_ID" "$PASS_N" "$FAIL_N" "${RESULTS%,}"
} > "$PROD_JSON"
if [ "$FAIL_N" = 0 ] && [ "$PASS_N" -gt 0 ]; then
    echo "RESULT: all checks passed"
    exit 0
fi
echo "RESULT: $FAIL_N check(s) failed"
exit 1
