#!/bin/sh
# Throwaway python:3.14 run of the S33 contract tests (repo mounted read-only, network only here).
# Usage: py314.sh  -> writes py314.log.gz and py314.exit next to this file.
E=$(cd "$(dirname "$0")" && pwd)
cd "$E/../../.." || exit 99
START=$(date +%s)
docker run --rm -v "$PWD":/src:ro python:3.14 sh -c '
set -e
echo "git: $(git --version)"
cp -a /src /w
cd /w/mcp-server
rm -rf .venv
python --version
pip -q install uv
uv sync --frozen --extra dev -q
uv run --frozen --extra dev pytest -p no:cacheprovider tests/slices/test_s33_*.py tests/test_export.py -q --durations=5 -s
' >"$E/py314.log.tmp" 2>&1
CODE=$?
END=$(date +%s)
{
    echo "# gate: py314 (python:3.14, uv sync --frozen --extra dev, its own git)"
    echo "# duration_seconds: $((END - START))"
    cat "$E/py314.log.tmp"
    echo "# exit: $CODE"
} | gzip -c >"$E/py314.log.gz"
rm -f "$E/py314.log.tmp"
echo "$CODE" >"$E/py314.exit"
echo "py314 exit=$CODE duration=$((END - START))s"
