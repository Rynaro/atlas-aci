#!/bin/sh
# Runs as the unprivileged user inside the python:3.14 container (see py314.sh).
set -e
python -VV
cp -a /src /home/u/w
cd /home/u/w/mcp-server
rm -rf .venv .mypy_cache .ruff_cache
pip -q install --user uv
export PATH=$HOME/.local/bin:$PATH
export UV_PROJECT_ENVIRONMENT=/home/u/venv
uv sync --frozen --extra dev
uv run --frozen --extra dev python -VV
rg --version | head -1
uv run --frozen --extra dev python -c "import importlib.metadata as m; print({d: m.version(d) for d in ('mcp', 'pyjwt', 'cryptography', 'uvicorn', 'starlette', 'sse-starlette', 'anyio')})"
uv run --frozen --extra dev pytest -p no:cacheprovider -q -rs tests/slices/test_s41_*.py tests/slices/test_s40_isolation.py --durations=5
