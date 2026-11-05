#!/bin/sh
# ACI-S36 production scenario. Usage: prod-scenario.sh [IMAGE]   (default atlas-aci-dev-s16)
#
# Works with the production image (atlas-aci:s36: entrypoint atlas-aci, user atlas, python at
# /usr/local/bin/python, no timeout binary) and with the dev image (atlas-aci-dev-s16): every
# container is started with `--entrypoint python`, so the image's own entrypoint never matters.
# The image must provide `python` (3.12 or newer) and must NOT provide leidenalg or igraph.
#
# Every container is --rm, --network none, uid 1000 (not root), mounts the repository READ-ONLY at
# /repo and writes only to /out (a scratch directory removed at the end). Nothing is installed.
# No `timeout` binary is needed in the image or on the host: each container is a named foreground
# `docker run` supervised by a host-side watchdog that runs `docker kill` after BOUND seconds.
#
# Checks (all must PASS; a check that depends on an earlier step FAILs when that step failed, and
# no identity check accepts empty input):
#  1. the verifier, with no provider, passes on the committed real run with production_manifest
#     true, and the committed docs name the same outcome (a member of the closed vocabulary);
#  2. two fresh runs of the runner with the FIXTURE provider (PYTHONPATH only) give byte-identical
#     result.json and decompressed partitions, and the verifier passes on each (fixture manifest);
#  3. the runner with `python -S` (no provider) records skipped_missing_provider, exit 0, and the
#     verifier passes on it with production_manifest true; --require-provider is rejected with the
#     exact text and exit 1, and only then is the absence of an output directory checked;
#  4. pyproject.toml, uv.lock and the mcp-server/src tree hash are unchanged across all of it.
# Environment: BOUND (seconds per container, default 600), RESULTS_DIR (default: the committed
# real-run evidence; the orchestrator's negative control points it at a corrupted temp copy).
set -u

IMAGE=${1:-atlas-aci-dev-s16}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
OUT=$(mktemp -d "${TMPDIR:-/tmp}/s36-prod.XXXXXX")
chmod 777 "$OUT"
BOUND=${BOUND:-600}
RESULTS_DIR=${RESULTS_DIR:-.spectra/evidence/aci-s36/real-run}
# An absolute RESULTS_DIR (a host directory, used by negative controls) is mounted read-only.
RESULTS_MOUNT=""
case $RESULTS_DIR in /*) RESULTS_MOUNT="-v $RESULTS_DIR:/results:ro"; RESULTS_ARG=/results;; *) RESULTS_ARG=$RESULTS_DIR;; esac
FAILED=0
FX=mcp-server/tests/fixtures/community_experiment
PROD_SHA=3239ec59d9fce113a115ff4b27c42bfbf06715060e1b2cb7e7de8e2e435a2beb
EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
OUTCOMES="retain_lpa leiden_candidate_for_review inconclusive skipped_missing_provider"
SEQ=0

say() { printf '%s\n' "$*"; }
pass() { say "PASS  $1"; }
fail() { say "FAIL  $1"; FAILED=1; }
sha_of() {
    if command -v sha256sum > /dev/null 2>&1; then sha256sum | cut -d' ' -f1
    else shasum -a 256 | cut -d' ' -f1; fi
}
tree_sha() { (cd "$REPO" && find mcp-server/src -type f -not -path '*/__pycache__/*' | LC_ALL=C sort \
    | while read -r f; do printf '%s  %s\n' "$(sha_of < "$f")" "$f"; done | sha_of); }
lock_sha() { (cd "$REPO" && cat mcp-server/pyproject.toml mcp-server/uv.lock | sha_of); }

# box [-e VAR=VALUE]... -- python-arguments...  (stdout is the container's stdout; returns its exit
# code; 124 when the watchdog had to kill it). The watchdog is a host sh loop, not `timeout`.
box() {
    ENVS=""
    while [ "$1" != "--" ]; do ENVS="$ENVS $1 $2"; shift 2; done
    shift
    SEQ=$((SEQ + 1))
    NAME="s36-prod-$$-$SEQ"
    # shellcheck disable=SC2086
    docker run --rm --name "$NAME" --network none --user 1000:1000 --entrypoint python \
        -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 $ENVS $RESULTS_MOUNT -v "$REPO":/repo:ro -v "$OUT":/out \
        -w /repo "$IMAGE" "$@" &
    RUN=$!
    (
        i=0
        while [ "$i" -lt "$BOUND" ]; do sleep 1; i=$((i + 1)); done
        docker kill "$NAME" > /dev/null 2>&1 && : > "$OUT/$NAME.killed"
    ) > /dev/null 2>&1 &
    DOG=$!
    wait "$RUN"
    CODE=$?
    kill "$DOG" > /dev/null 2>&1
    wait "$DOG" 2> /dev/null
    if [ -e "$OUT/$NAME.killed" ]; then return 124; fi
    return "$CODE"
}
in_list() { case " $2 " in *" $1 "*) return 0;; *) return 1;; esac; }

BEFORE_LOCK=$(lock_sha)
BEFORE_TREE=$(tree_sha)
say "image: $IMAGE  results under test: $RESULTS_DIR"

# --- 1. the committed real run -------------------------------------------------------------
V1=$(box -- scripts/verify-community-experiment.py --results "$RESULTS_ARG" 2> "$OUT/v1.err")
CODE=$?
STEP1=0
if [ "$CODE" = 0 ] && [ -n "$V1" ]; then
    pass "verifier exits 0 on the real-run evidence"
else
    fail "verifier exit $CODE (output '${V1}'): $(cat "$OUT/v1.err")"; STEP1=1
fi
if [ "$STEP1" = 0 ]; then
    case $V1 in *'"production_manifest":true'*) pass "production_manifest is true";; *) fail "production_manifest is not true: $V1";; esac
    case $V1 in *'"status":"pass"'*) pass "status is pass";; *) fail "status is not pass: $V1";; esac
    OUTCOME=$(printf '%s' "$V1" | sed -n 's/.*"outcome":"\([a-z_]*\)".*/\1/p')
    say "committed outcome: '$OUTCOME'"
    if [ -n "$OUTCOME" ] && in_list "$OUTCOME" "$OUTCOMES"; then
        pass "the verifier reports an outcome from the closed vocabulary ($OUTCOME)"
        if grep -q "\`$OUTCOME\`" "$REPO/docs/experiments/community-comparison.md"; then
            pass "the committed docs name the same outcome ($OUTCOME)"
        else fail "the docs do not name the outcome $OUTCOME"; fi
    else fail "outcome '$OUTCOME' is empty or not in the closed vocabulary"; fi
else
    fail "production_manifest/status/outcome checks skipped: step 1 failed"
fi

# --- 2. fixture provider replay ------------------------------------------------------------
FIXSHA=$(sha_of < "$REPO/$FX/manifest.fixture.v1.json")
SITE=/repo/$FX/fixture_site
STEP2=0
for n in a b; do
    SEED=0; [ $n = b ] && SEED=4242
    box -e PYTHONPATH=$SITE -e PYTHONHASHSEED=$SEED -- scripts/experiment-leiden-vs-lpa.py run \
        --manifest $FX/manifest.fixture.v1.json --manifest-sha256 "$FIXSHA" --out /out/fixture-$n \
        > "$OUT/fx-$n.out" 2> "$OUT/fx-$n.err"
    CODE=$?
    if [ "$CODE" = 0 ] && [ -s "$OUT/fx-$n.out" ] && [ -s "$OUT/fixture-$n/result.json" ] \
        && [ -s "$OUT/fixture-$n/partitions.json.gz" ]; then
        pass "fixture run $n exits 0 and wrote its artifacts"
    else fail "fixture run $n exit $CODE: $(cat "$OUT/fx-$n.err")"; STEP2=1; fi
done
if [ "$STEP2" = 0 ]; then
    if cmp -s "$OUT/fixture-a/result.json" "$OUT/fixture-b/result.json"; then
        pass "fixture result.json is byte-identical across two runs (different PYTHONHASHSEED)"
    else fail "fixture result.json differs between runs"; fi
    PA=$(gzip -dc "$OUT/fixture-a/partitions.json.gz" | sha_of)
    PB=$(gzip -dc "$OUT/fixture-b/partitions.json.gz" | sha_of)
    if [ -n "$PA" ] && [ "$PA" != "$EMPTY_SHA" ] && [ "$PA" = "$PB" ]; then
        pass "decompressed partitions are non-empty and byte-identical ($PA)"
    else fail "partitions empty or different ($PA vs $PB)"; fi
    if cmp -s "$OUT/fx-a.out" "$OUT/fx-b.out"; then pass "run summaries are non-empty and identical"
    else fail "run summaries differ"; fi
    for n in a b; do
        VF=$(box -- scripts/verify-community-experiment.py --results /out/fixture-$n \
            --manifest $FX/manifest.fixture.v1.json --manifest-sha256 "$FIXSHA" 2> "$OUT/vf-$n.err")
        CODE=$?
        if [ "$CODE" = 0 ] && [ -n "$VF" ]; then
            case $VF in *'"production_manifest":false'*) pass "verifier passes on fixture run $n (production_manifest false)";;
                *) fail "fixture verification did not report production_manifest false: $VF";; esac
        else fail "verifier exit $CODE on fixture run $n: $(cat "$OUT/vf-$n.err")"; fi
    done
else
    fail "replay comparisons skipped: a fixture run failed"
fi

# --- 3. no provider: skipped_missing_provider ----------------------------------------------
S=$(box -- -S scripts/experiment-leiden-vs-lpa.py run --manifest $FX/manifest.v1.json \
    --manifest-sha256 "$PROD_SHA" --out /out/skipped 2> "$OUT/skipped.err")
CODE=$?
STEP3=0
if [ "$CODE" = 0 ] && [ -n "$S" ] && [ -s "$OUT/skipped/result.json" ]; then
    pass "runner without a provider exits 0 and wrote its artifacts"
else fail "runner exit $CODE: $(cat "$OUT/skipped.err")"; STEP3=1; fi
if [ "$STEP3" = 0 ]; then
    case $S in *'"outcome":"skipped_missing_provider"'*'"status":"skipped_missing_provider"'*) pass "outcome and status are skipped_missing_provider";;
        *) fail "unexpected summary: $S";; esac
    VS=$(box -- -S scripts/verify-community-experiment.py --results /out/skipped 2> "$OUT/vs.err")
    CODE=$?
    if [ "$CODE" = 0 ] && [ -n "$VS" ]; then
        pass "verifier passes on the skipped run"
        case $VS in *'"production_manifest":true'*) pass "skipped run carries production_manifest true";; *) fail "skipped run: $VS";; esac
    else fail "verifier exit $CODE on the skipped run: $(cat "$OUT/vs.err")"; fi
else
    fail "skipped-run verification skipped: the runner step failed"
fi
box -- -S scripts/experiment-leiden-vs-lpa.py run --manifest $FX/manifest.v1.json \
    --manifest-sha256 "$PROD_SHA" --out /out/required --require-provider \
    > "$OUT/required.out" 2> "$OUT/required.err"
CODE=$?
if [ "$CODE" = 1 ] && [ ! -s "$OUT/required.out" ] \
    && [ "$(cat "$OUT/required.err")" = "Community experiment rejected: provider_required_missing" ]; then
    pass "--require-provider exits 1 with exactly 'Community experiment rejected: provider_required_missing'"
    if [ ! -e "$OUT/required" ]; then pass "the rejected run left no output directory"
    else fail "output directory left behind"; fi
else
    fail "--require-provider: exit $CODE, stderr '$(cat "$OUT/required.err")'"
    fail "rejected-run output check skipped: the rejection itself was not observed"
fi

# --- 4. the product is untouched -----------------------------------------------------------
[ "$(lock_sha)" = "$BEFORE_LOCK" ] && pass "pyproject.toml and uv.lock unchanged" || fail "lock files changed"
[ "$(tree_sha)" = "$BEFORE_TREE" ] && pass "mcp-server/src tree hash unchanged ($BEFORE_TREE)" || fail "mcp-server/src changed"

rm -rf "${OUT:?}"
if [ "$FAILED" = 0 ]; then say "RESULT: all checks passed"; else say "RESULT: FAILED"; fi
exit "$FAILED"
