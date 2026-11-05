#!/bin/sh
# ACI-S36 gate runner: gate.sh NAME CMD...   -> NAME.log.gz and NAME.exit under this directory.
# CMD runs inside the dev container (dk36.sh: uid 1000, atlas-aci-dev-s16, repo at /ws,
# workdir /ws/mcp-server), which is --rm and time-bounded by the CMD itself (timeout N).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
NAME=$1
shift
START=$(date +%s)
{
    "$HERE/dk36.sh" "$@"
    CODE=$?
    echo "== gate $NAME exit $CODE duration_seconds $(($(date +%s) - START))"
    echo "$CODE" > "$HERE/$NAME.exit"
} 2>&1 | gzip -9n > "$HERE/$NAME.log.gz"
echo "$NAME exit $(cat "$HERE/$NAME.exit") ($(($(date +%s) - START)) s)"
