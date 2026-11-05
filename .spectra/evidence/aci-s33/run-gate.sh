#!/bin/sh
# usage: run-gate.sh NAME 'command run inside the dev container (cwd /ws/mcp-server)' [HEADER]
# Writes NAME.log.gz (header, output, exit) and NAME.exit (the real numeric exit code).
E=$(cd "$(dirname "$0")" && pwd)
NAME=$1
CMD=$2
HEADER=${3:-}
START=$(date +%s)
sh "$E/dk33.sh" sh -c "$CMD" >"$E/$NAME.log.tmp" 2>&1
CODE=$?
END=$(date +%s)
{
    echo "# gate: $NAME"
    echo "# command: $CMD"
    echo "# duration_seconds: $((END - START))"
    [ -n "$HEADER" ] && echo "# $HEADER"
    cat "$E/$NAME.log.tmp"
    echo "# exit: $CODE"
} | gzip -c >"$E/$NAME.log.gz"
rm -f "$E/$NAME.log.tmp"
echo "$CODE" >"$E/$NAME.exit"
echo "$NAME exit=$CODE duration=$((END - START))s"
