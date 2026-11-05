#!/bin/sh
# sec-relock py314 gate: the s23 + s38 tests and the s11/s13 stdio tests in a throwaway --rm
# python:3.14 container (Linux, the CI interpreter), resolving the NEW lock with
# `uv run --frozen --extra dev`. Network is needed once to install uv and the locked dev
# environment INSIDE the container; nothing is installed on the host or in any dev image. The
# repository is mounted read-only and copied into the container's /tmp. A host-side watchdog
# (`docker kill`) bounds the container.
# usage: py314.sh   -> py314.log.gz and py314.exit next to this script
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=sec-relock-py314-$$
( sleep 2700; docker kill "$NAME" > /dev/null 2>&1 ) & WD=$!
trap 'kill $WD 2>/dev/null; docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT INT TERM
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
uv --version
UV_PROJECT_ENVIRONMENT=/tmp/venv uv run --frozen --extra dev python -c "import importlib.metadata as m,sys;print(sys.version);print({n:m.version(n) for n in (\"mcp\",\"anyio\",\"pyjwt\",\"cryptography\",\"certifi\")})"
UV_PROJECT_ENVIRONMENT=/tmp/venv uv run --frozen --extra dev pytest -p no:cacheprovider -q -rs tests/slices/test_s23_*.py tests/slices/test_s38_*.py tests/slices/test_s11_stdio.py tests/slices/test_s13_stdio.py --durations=5
'
    CODE=$?
    echo "== py314 exit $CODE duration_seconds $(($(date +%s) - START))"
    echo "$CODE" > "$HERE/py314.exit"
} 2>&1 | gzip -9n > "$HERE/py314.log.gz"
echo "py314 exit $(cat "$HERE/py314.exit") ($(($(date +%s) - START)) s)"
