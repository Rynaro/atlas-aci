#!/bin/sh
# ACI-S38 gate runner: gate.sh NAME CMD...   -> NAME.log.gz and NAME.exit under this directory.
# CMD runs inside the dev container (dk38.sh: uid 1000, atlas-aci-dev-s16, repo at /ws,
# workdir /ws/mcp-server, tools at /opt/venv/bin/{python,ruff,mypy}); the container is --rm.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
NAME=$1
shift
START=$(date +%s)
{
    "$HERE/dk38.sh" "$@"
    CODE=$?
    echo "== gate $NAME exit $CODE duration_seconds $(($(date +%s) - START))"
    echo "$CODE" > "$HERE/$NAME.exit"
} 2>&1 | gzip -9n > "$HERE/$NAME.log.gz"
echo "$NAME exit $(cat "$HERE/$NAME.exit") ($(($(date +%s) - START)) s)"
