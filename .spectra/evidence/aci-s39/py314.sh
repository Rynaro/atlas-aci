#!/bin/sh
# ACI-S39 py314 gate: the s39 tests (and the s38 isolation test) in a throwaway --rm python:3.14
# container (Linux), with the LOCKED dependencies (`uv sync --frozen --extra dev`). Network is needed
# once to install uv and the locked environment INSIDE the container; nothing is installed on the
# host or in any dev image. The repository is mounted read-only and copied into /tmp. The fake `rg`
# in the tests makes ripgrep unnecessary. usage: py314.sh -> py314.log.gz and py314.exit
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=s39-py314-$$
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
export UV_PROJECT_ENVIRONMENT=/tmp/venv
uv sync --frozen --extra dev
uv run --frozen --extra dev python -VV
uv run --frozen --extra dev python -c "import importlib.metadata as m; print({d: m.version(d) for d in (\"mcp\", \"uvicorn\", \"starlette\", \"sse-starlette\", \"anyio\")})"
uv run --frozen --extra dev pytest -p no:cacheprovider -q -rs tests/slices/test_s39_*.py tests/slices/test_s38_isolation.py --durations=5
'
    CODE=$?
    echo "== py314 exit $CODE duration_seconds $(($(date +%s) - START))"
    echo "$CODE" > "$HERE/py314.exit"
} 2>&1 | gzip -9n > "$HERE/py314.log.gz"
echo "py314 exit $(cat "$HERE/py314.exit") ($(($(date +%s) - START)) s)"
