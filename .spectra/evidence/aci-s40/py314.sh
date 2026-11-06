#!/bin/sh
# ACI-S40 py314 gate: the s40 tests (and the s39 isolation test) in a throwaway --rm python:3.14
# container (Linux), with the LOCKED dependencies (`uv sync --frozen --extra dev`). The container
# starts as root only to `apt-get install ripgrep` (the injection tests run the real rg, as CI does
# after its "Install ripgrep" step), then drops to an unprivileged uid 1000 user for everything else.
# Network is needed once to install ripgrep, uv and the locked environment INSIDE the container;
# nothing is installed on the host or in any dev image. The repository is mounted read-only and
# copied into the user's home. usage: py314.sh -> py314.log.gz and py314.exit
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=s40-py314-$$
trap 'docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT INT TERM
START=$(date +%s)
{
    docker run --rm --name "$NAME" -e PYTHONDONTWRITEBYTECODE=1 -e UV_CACHE_DIR=/home/u/uvcache \
        -v "$REPO":/src:ro -v "$HERE/py314-inner.sh":/inner.sh:ro python:3.14 timeout 2400 sh -c '
set -e
apt-get update -qq
apt-get install -y -qq --no-install-recommends ripgrep > /dev/null
useradd -m -u 1000 u
runuser -u u -- env HOME=/home/u UV_CACHE_DIR=/home/u/uvcache PYTHONDONTWRITEBYTECODE=1 sh /inner.sh
'
    CODE=$?
    echo "== py314 exit $CODE duration_seconds $(($(date +%s) - START))"
    echo "$CODE" > "$HERE/py314.exit"
} 2>&1 | gzip -9n > "$HERE/py314.log.gz"
echo "py314 exit $(cat "$HERE/py314.exit") ($(($(date +%s) - START)) s)"
