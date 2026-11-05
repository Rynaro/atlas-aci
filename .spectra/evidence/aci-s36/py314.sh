#!/bin/sh
# ACI-S36 py314 gate: the s36 tests (and the community regression tests) in a throwaway --rm
# python:3.14 container. Network is needed once to install uv and the locked dev environment INSIDE
# the container; nothing is installed on the host or in any dev image. The repository is mounted
# read-only and copied into the container's /tmp.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
exec docker run --rm --user 1000:1000 -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uvcache -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$REPO":/src:ro python:3.14 timeout 1700 sh -c '
set -e
python -VV
cp -a /src /tmp/w
cd /tmp/w/mcp-server
rm -rf .venv .mypy_cache .ruff_cache
pip -q install --user uv
export PATH=$HOME/.local/bin:$PATH
UV_PROJECT_ENVIRONMENT=/tmp/venv uv run --frozen --extra dev python -VV
UV_PROJECT_ENVIRONMENT=/tmp/venv uv run --frozen --extra dev pytest -p no:cacheprovider -q -rs tests/slices/test_s36_*.py tests/test_communities.py tests/test_thesis_negatives.py --durations=5
'
