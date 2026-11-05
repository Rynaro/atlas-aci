#!/bin/sh
# ACI-S34 py314 gate: the S34 contract tests under python 3.14 in a throwaway container with the
# dev extras. Network is allowed for this container only (uv/pip install and the grammar cache);
# the repository is mounted read-only and copied inside; nothing runs on the host.
set -u
REPO=$(git rev-parse --show-toplevel)
docker run --rm -v "$REPO":/src:ro python:3.14 sh -c '
    python --version &&
    cp -a /src /w && cd /w/mcp-server && rm -rf .venv &&
    pip install -q uv &&
    uv run --frozen --extra dev python --version &&
    uv run --frozen --extra dev pytest -p no:cacheprovider -q tests/slices/test_s34_*.py
'
