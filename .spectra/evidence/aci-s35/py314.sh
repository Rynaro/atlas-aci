#!/bin/sh
# ACI-S35 py314 gate: the S35 contract tests under python 3.14 in a throwaway container with the
# dev extras. Network is allowed for this container only (uv/pip install and the grammar cache);
# the repository is mounted read-only and copied inside; nothing runs on the host. The container
# reports its python and expat versions.
set -u
REPO=$(git rev-parse --show-toplevel)
docker run --rm -v "$REPO":/src:ro python:3.14 timeout 1500 sh -c '
    python --version &&
    python -c "import pyexpat, platform; print(\"python\", platform.python_version(), \"pyexpat.EXPAT_VERSION\", pyexpat.EXPAT_VERSION)" &&
    cp -a /src /w && cd /w/mcp-server && rm -rf .venv &&
    pip install -q uv &&
    uv run --frozen --extra dev python --version &&
    uv run --frozen --extra dev python -c "import pyexpat; print(\"uv env pyexpat.EXPAT_VERSION\", pyexpat.EXPAT_VERSION)" &&
    uv run --frozen --extra dev pytest -p no:cacheprovider -q --durations=8 tests/slices/test_s35_*.py \
        tests/slices/test_s18_contract.py tests/test_export.py
'
