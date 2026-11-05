#!/bin/sh
# sec-relock lock-check gate: `uv lock --check` (the ci.yml step, uv "latest") on the re-locked
# pyproject.toml/uv.lock in a throwaway --rm python:3.14 container. Network is needed once to
# install uv INSIDE the container. The repo is mounted read-only and copied to /tmp.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=sec-relock-lockcheck-$$
( sleep 900; docker kill "$NAME" > /dev/null 2>&1 ) & WD=$!
trap 'kill $WD 2>/dev/null; docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT INT TERM
{
    docker run --rm --name "$NAME" --user 1000:1000 -e HOME=/tmp -v "$REPO":/src:ro python:3.14 sh -c '
set -e
python -VV
pip -q install --user uv
export PATH=$HOME/.local/bin:$PATH
uv --version
cp -a /src/mcp-server /tmp/w
cd /tmp/w
sha256sum pyproject.toml uv.lock
uv lock --check
echo LOCK-CHECK-OK
'
    CODE=$?
    echo "== lock-check exit $CODE"
    echo "$CODE" > "$HERE/lock-check.exit"
} 2>&1 | gzip -9n > "$HERE/lock-check.log.gz"
echo "lock-check exit $(cat "$HERE/lock-check.exit")"
