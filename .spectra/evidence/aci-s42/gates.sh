#!/bin/sh
# ACI-S42 gates. Usage: gates.sh GATE   (writes <gate>.log.gz and <gate>.exit beside this script)
# Every docker run is --rm and bounded by a host watchdog (docker kill). No uv, pip or host python.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
SCRATCH=/private/tmp/claude-501/-Users-henrique-workspace-oss-agents-atlas-aci/694bbcf4-4624-4586-978b-245709fb6cab/scratchpad
BASE=4f470d73a80fcf0e797728b1e45bccc77bf3a0a5
GATE=$1

# dock SECONDS NAME [docker args] -- cmd...   (the dev image, uid 1000, repo at /ws)
dock() {
  secs=$1; name=$2; shift 2
  extra=""
  while [ "$1" != "--" ]; do extra="$extra $1"; shift; done
  shift
  ( sleep "$secs"; docker kill "$name" >/dev/null 2>&1 ) &
  wd=$!
  # shellcheck disable=SC2086
  docker run --rm --name "$name" $extra --user 1000:1000 -e HOME=/hc -e PYTHONPATH=/ws/mcp-server/src \
    -v "$SCRATCH/home:/hc" -v "$REPO:/ws" -w /ws/mcp-server atlas-aci-dev-relock "$@"
  rc=$?
  kill "$wd" 2>/dev/null
  return $rc
}

run() {
  case "$GATE" in
    s42-contract)
      dock 900 s42-gate-c -- /opt/venv/bin/python -m pytest tests/slices/test_s42_isolation.py tests/slices/test_s42_contract.py tests/slices/test_s42_support.py -q -p no:cacheprovider --durations=0 ;;
    s42-contract-cpus4)
      dock 900 s42-gate-c4 --cpus 4 -- /opt/venv/bin/python -m pytest tests/slices/test_s42_isolation.py tests/slices/test_s42_contract.py -q -p no:cacheprovider --durations=0 ;;
    regression)
      dock 2400 s42-gate-r -- sh -c "/opt/venv/bin/python -m pytest tests/slices/test_s23*.py tests/slices/test_s36_isolation.py tests/slices/test_s38*.py tests/slices/test_s39*.py tests/slices/test_s40*.py tests/slices/test_s41*.py tests/test_thesis_negatives.py -q -p no:cacheprovider" ;;
    ruff)
      dock 300 s42-gate-l1 -- sh -c '/opt/venv/bin/ruff check src/ tests/ && /opt/venv/bin/ruff check ../scripts/ --exclude fingerprint-fixture' ;;
    format)
      dock 300 s42-gate-l2 -- sh -c '/opt/venv/bin/ruff format --check src/ tests/ && /opt/venv/bin/ruff format --check ../scripts/ --exclude fingerprint-fixture' ;;
    mypy)
      dock 600 s42-gate-l3 -- sh -c '/opt/venv/bin/mypy src/ && /opt/venv/bin/mypy ../scripts/' ;;
    full-suite)
      dock 3000 s42-gate-f -- /opt/venv/bin/python -m pytest -q -p no:cacheprovider --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition ;;
    dev-run)
      rm -rf "$HERE/dev-run"
      DEVID=$(docker image inspect --format "{{.Id}}" atlas-aci-dev-relock)
      dock 600 s42-gate-dev --cpus 4 -- sh -c "cd /tmp && s=\$(date +%s) && /opt/venv/bin/python /ws/scripts/eval/s42_record.py --manifest /ws/atlas/evals/shared/manifest.v1.json --out /ws/.spectra/evidence/aci-s42/dev-run/raw --work /tmp/w --image $DEVID; echo RECORDER_EXIT=\$? WALL=\$(( \$(date +%s) - s ))s; /opt/venv/bin/python /ws/scripts/eval/s42_aggregate.py --manifest /ws/atlas/evals/shared/manifest.v1.json --raw /ws/.spectra/evidence/aci-s42/dev-run/raw --out /ws/.spectra/evidence/aci-s42/dev-run" ;;
    g-mcp)
      dock 120 s42-gate-m -- sh -c 'd=$(mktemp -d) && /opt/venv/bin/python -m atlas_aci tools --repo "$d" > /tmp/tools.json && sha256sum /tmp/tools.json && wc -c < /tmp/tools.json'
      ;;
    g-replay)
      dock 200 s42-gate-p -- sh -c '
        set -e
        M=../atlas/evals/shared/manifest.v1.json; R=../docs/evidence/s42/raw; C=../docs/evidence/s42
        for n in 1 2; do mkdir -p /tmp/rp$n; /opt/venv/bin/python ../scripts/eval/s42_aggregate.py --manifest $M --raw $R --out /tmp/rp$n; done
        for f in readiness-report.json readiness-report.md; do
          cmp /tmp/rp1/$f /tmp/rp2/$f && cmp /tmp/rp1/$f $C/$f && echo "identical: $f"
        done
        /opt/venv/bin/python ../scripts/eval/s42_aggregate.py --manifest $M --raw $R --out $C --verify && echo "verify exit 0"' ;;
    g-src)
      cd "$REPO" || exit 1
      rc=0
      git diff --exit-code "$BASE" -- mcp-server/src mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows hosts docs/operations docs/evidence/v3 mcp-server/tests/fixtures || rc=1
      echo "untracked under src/config/workflows:"; u=$(git ls-files --others --exclude-standard -- mcp-server/src mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows hosts docs/operations docs/evidence/v3 mcp-server/tests/fixtures scripts/eval/v3_aggregate.py scripts/eval/v3_record.py); [ -z "$u" ] && echo none || { echo "$u"; rc=1; }
      echo "== name-status (tracked changes vs base) =="; git diff --name-status "$BASE" -- mcp-server/tests scripts atlas CHANGELOG.md README.md
      echo "== untracked new files in scope =="; git ls-files --others --exclude-standard -- mcp-server/tests scripts atlas docs/evidence/s42
      echo "== deletions in modified files =="; git diff --numstat "$BASE" -- atlas/evals/README.md CHANGELOG.md README.md
      git diff --numstat "$BASE" -- atlas/evals/README.md CHANGELOG.md README.md | awk '$2 != 0 {print "DELETIONS: " $0; bad=1} END {exit bad}' || rc=1
      return $rc ;;
    *) echo "unknown gate $GATE"; return 2 ;;
  esac
}

run > "$HERE/$GATE.log" 2>&1
code=$?
echo "$code" > "$HERE/$GATE.exit"
gzip -f "$HERE/$GATE.log"
echo "$GATE exit $code"
exit "$code"
