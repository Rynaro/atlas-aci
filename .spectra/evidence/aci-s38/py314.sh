#!/bin/sh
# ACI-S38 py314 gate: the s38 tests (and test_s11_protocol / test_s11_stdio) in a throwaway --rm
# python:3.14 container (Linux). Network is needed once to install uv and the locked dev
# environment INSIDE the container; nothing is installed on the host or in any dev image. The
# repository is mounted read-only and copied into the container's /tmp. The golden transcripts
# must also pass here (the CLI index/serve path on another Python and other grammar builds).
# usage: py314.sh   -> py314.log.gz and py314.exit next to this script
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=s38-py314-$$
trap 'docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT INT TERM
START=$(date +%s)
{
    docker run --rm --name "$NAME" --user 1000:1000 -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uvcache \
        -e PYTHONDONTWRITEBYTECODE=1 -v "$REPO":/src:ro python:3.14 timeout 2400 sh -c '
set -e
python -VV
cp -a /src /tmp/w
cd /tmp/w/mcp-server
rm -rf .venv .mypy_cache .ruff_cache
pip -q install --user uv
export PATH=$HOME/.local/bin:$PATH
UV_PROJECT_ENVIRONMENT=/tmp/venv uv run --frozen --extra dev python -VV
UV_PROJECT_ENVIRONMENT=/tmp/venv uv run --frozen --extra dev pytest -p no:cacheprovider -q -rs tests/slices/test_s38_*.py tests/slices/test_s11_protocol.py tests/slices/test_s11_stdio.py --durations=5
'
    CODE=$?
    echo "== py314 exit $CODE duration_seconds $(($(date +%s) - START))"
    echo "$CODE" > "$HERE/py314.exit"
} 2>&1 | gzip -9n > "$HERE/py314.log.gz"
echo "py314 exit $(cat "$HERE/py314.exit") ($(($(date +%s) - START)) s)"
