#!/bin/sh
# sec-relock gate runner: gate.sh NAME CMD...   -> NAME.log.gz and NAME.exit under this directory.
# CMD runs inside atlas-aci-dev-relock via dkr.sh (uid 1000, repo at /ws, workdir /ws/mcp-server,
# tools at /opt/venv/bin/{python,ruff,mypy}); the container is --rm and has a host-side watchdog
# (GATE_TIMEOUT seconds, default 2400) that `docker kill`s it.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
NAME=$1
shift
START=$(date +%s)
export DKR_NAME="sec-relock-$NAME-$$"
( sleep "${GATE_TIMEOUT:-2400}"; docker kill "$DKR_NAME" > /dev/null 2>&1 ) & WD=$!
trap 'kill $WD 2>/dev/null; docker rm -f "$DKR_NAME" > /dev/null 2>&1 || true' EXIT INT TERM
{
    "$HERE/dkr.sh" "$@"
    CODE=$?
    echo "== gate $NAME exit $CODE duration_seconds $(($(date +%s) - START))"
    echo "$CODE" > "$HERE/$NAME.exit"
} 2>&1 | gzip -9n > "$HERE/$NAME.log.gz"
echo "$NAME exit $(cat "$HERE/$NAME.exit") ($(($(date +%s) - START)) s)"
