#!/bin/sh
# Throwaway python:3.14 run of the S30 contract tests (repo mounted read-only, network only here).
cd "$(dirname "$0")/../../.." || exit 99
docker run --rm -v "$PWD":/src:ro python:3.14 sh -c '
set -e
cp -a /src /w
cd /w/mcp-server
rm -rf .venv
python --version
pip -q install uv
uv sync --frozen --extra dev -q
uv run --frozen pytest -p no:cacheprovider tests/slices/test_s30_support.py tests/slices/test_s30_cross_repo.py tests/slices/test_s30_resolution.py tests/slices/test_s30_rejections.py tests/slices/test_s30_isolation.py -q --durations=5
'
