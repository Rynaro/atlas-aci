#!/bin/sh
# ACI-S28 production-image scenario.  Usage: prod-scenario.sh [IMAGE]   (default atlas-aci:s28)
#
# Build the image first (from the repository root):  docker build -t atlas-aci:s28 mcp-server
#
# Every run is `--network none --read-only` with the fixtures mounted read-only.  The output
# directory is a plain bind mount (never a tmpfs over /tmp: the production HOME=/tmp holds the
# prefetched python grammar, and a tmpfs would hide it).
#
# Expected (each step prints PASS or the script stops with exit 1):
#   1. import twice -> exit 0 both times, byte-identical snapshots and stdout, the snapshot
#      equals the committed golden and its extractor block equals the golden's
#   2. AC-003 byte-flipped wheel   -> exit 1, empty stdout, stderr
#        Error: Python dependency rejected: artifact_digest_mismatch pypi:s28-demo-pkg@1.2.0
#      and a pre-existing --output is byte-unchanged, no temp file left
#   3. AC-004 forked lock, no env  -> exit 1, empty stdout, stderr
#        Error: Python dependency rejected: unresolved_lock_variant
#   4. AC-006 real lock, no wheel  -> exit 1, empty stdout, stderr
#        Error: Python dependency rejected: missing_dependency_source pypi:packaging@26.0
#   5. usage error (--env bogus)   -> exit 2, empty stdout
#   6. tools JSON sha256 94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56, 1631472 bytes
set -u
IMAGE="${1:-atlas-aci:s28}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
FX="$ROOT/mcp-server/tests/fixtures/python_dependencies"
LOCK="$ROOT/mcp-server/uv.lock"
PURE=s28_demo_pkg-1.2.0-py3-none-any.whl
GOLDEN=s28_demo_pkg-1.2.0.snapshot.json
PIN_SHA=94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56
PIN_BYTES=1631472
WORK="$(mktemp -d "${TMPDIR:-/tmp}/s28-prod.XXXXXX")"
OUT="$WORK/out"
mkdir -p "$OUT" && chmod 777 "$OUT"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

if command -v sha256sum >/dev/null 2>&1; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
echo "tools: cmp=$(command -v cmp || echo missing) sha=$SHA image=$IMAGE"
command -v cmp >/dev/null 2>&1 || fail "cmp is not available"
sum() { $SHA "$1" | cut -d' ' -f1; }

# run_cli <stdout-file> <stderr-file> <args...>: the container's exit code is returned.
run_cli() {
  stdout="$1"; stderr="$2"; shift 2
  docker run --rm --network none --read-only --user "$(id -u):$(id -g)" \
    -v "$FX:/fx:ro" -v "$LOCK:/lk/uv.lock:ro" -v "$OUT:/out" \
    "$IMAGE" "$@" >"$stdout" 2>"$stderr"
}

# ---- 1. success twice -----------------------------------------------------------------------
ARGS="import-python-dependency --lock /fx/single.uv.lock --package s28-demo-pkg --wheel /fx/$PURE"
run_cli "$WORK/s1" "$WORK/e1" $ARGS --output /out/a.json || { cat "$WORK/e1"; fail "first import"; }
run_cli "$WORK/s2" "$WORK/e2" $ARGS --output /out/b.json || { cat "$WORK/e2"; fail "second import"; }
[ -s "$WORK/s1" ] || fail "empty summary on stdout"
cmp "$OUT/a.json" "$OUT/b.json" || fail "the two snapshots differ"
cmp "$WORK/s1" "$WORK/s2" || fail "the two summaries differ"
cmp "$OUT/a.json" "$FX/$GOLDEN" || fail "the snapshot differs from the committed golden"
[ -s "$WORK/e1" ] && { cat "$WORK/e1"; fail "unexpected stderr on success"; }
docker run --rm --network none --read-only --entrypoint python -v "$FX:/fx:ro" -v "$OUT:/out:ro" \
  "$IMAGE" -c '
import json
got = json.load(open("/out/a.json"))["extractor"]
want = json.load(open("/fx/'"$GOLDEN"'"))["extractor"]
assert got == want, (got, want)
print("extractor", got)' || fail "extractor block differs from the golden"
pass "import twice: identical snapshot and stdout, equal to the golden ($(sum "$OUT/a.json"))"

# ---- 2. AC-003 digest mismatch --------------------------------------------------------------
mkdir -p "$OUT/bad" && cp "$FX/$PURE" "$OUT/bad/$PURE"
printf '\377' | dd of="$OUT/bad/$PURE" bs=1 seek=100 conv=notrunc 2>/dev/null
cmp -s "$OUT/bad/$PURE" "$FX/$PURE" && fail "the flipped copy equals the original"
printf 'SENTINEL-BYTES' > "$OUT/keep.json"
run_cli "$WORK/s3" "$WORK/e3" import-python-dependency --lock /fx/single.uv.lock \
  --package s28-demo-pkg --wheel "/out/bad/$PURE" --output /out/keep.json
code=$?
[ "$code" = 1 ] || fail "digest mismatch exit $code (want 1)"
[ ! -s "$WORK/s3" ] || fail "stdout is not empty on rejection"
[ "$(cat "$WORK/e3")" = "Error: Python dependency rejected: artifact_digest_mismatch pypi:s28-demo-pkg@1.2.0" ] \
  || { cat "$WORK/e3"; fail "digest mismatch text"; }
[ "$(cat "$OUT/keep.json")" = "SENTINEL-BYTES" ] || fail "the pre-existing output changed"
ls -A "$OUT" | grep -q '^\.atlas-python-dependency-' && fail "a temp file was left behind"
pass "AC-003 artifact_digest_mismatch (exit 1, empty stdout, output untouched)"

# ---- 3. AC-004 unresolved variant -----------------------------------------------------------
printf 'SENTINEL-BYTES' > "$OUT/keep.json"
run_cli "$WORK/s4" "$WORK/e4" import-python-dependency --lock /fx/forked.uv.lock \
  --package s28-demo-pkg --wheel "/fx/$PURE" --output /out/keep.json
code=$?
[ "$code" = 1 ] || fail "unresolved variant exit $code (want 1)"
[ ! -s "$WORK/s4" ] || fail "stdout is not empty on rejection"
[ "$(cat "$WORK/e4")" = "Error: Python dependency rejected: unresolved_lock_variant" ] \
  || { cat "$WORK/e4"; fail "unresolved variant text"; }
[ "$(cat "$OUT/keep.json")" = "SENTINEL-BYTES" ] || fail "the pre-existing output changed"
pass "AC-004 unresolved_lock_variant"

# ---- 4. AC-006 missing source ---------------------------------------------------------------
printf 'SENTINEL-BYTES' > "$OUT/keep.json"
run_cli "$WORK/s5" "$WORK/e5" import-python-dependency --lock /lk/uv.lock --package packaging \
  --output /out/keep.json
code=$?
[ "$code" = 1 ] || fail "missing source exit $code (want 1)"
[ ! -s "$WORK/s5" ] || fail "stdout is not empty on rejection"
[ "$(cat "$WORK/e5")" = "Error: Python dependency rejected: missing_dependency_source pypi:packaging@26.0" ] \
  || { cat "$WORK/e5"; fail "missing source text"; }
[ "$(cat "$OUT/keep.json")" = "SENTINEL-BYTES" ] || fail "the pre-existing output changed"
pass "AC-006 missing_dependency_source"

# ---- 5. usage error -------------------------------------------------------------------------
run_cli "$WORK/s6" "$WORK/e6" $ARGS --env bogus --output /out/never.json
code=$?
[ "$code" = 2 ] || fail "usage error exit $code (want 2)"
[ ! -s "$WORK/s6" ] || fail "stdout is not empty on a usage error"
[ ! -e "$OUT/never.json" ] || fail "an output was created on a usage error"
grep -q 'Invalid --env value.' "$WORK/e6" || fail "usage text"
pass "usage error exit 2"

# ---- 6. the tools JSON is unchanged ---------------------------------------------------------
run_cli "$WORK/tools.json" "$WORK/e7" tools --repo /fx || fail "tools"
[ "$(sum "$WORK/tools.json")" = "$PIN_SHA" ] || fail "tools JSON sha256 $(sum "$WORK/tools.json")"
[ "$(wc -c < "$WORK/tools.json" | tr -d ' ')" = "$PIN_BYTES" ] || fail "tools JSON byte count"
pass "tools JSON pin ($PIN_SHA, $PIN_BYTES bytes)"
echo "ALL PASS"
