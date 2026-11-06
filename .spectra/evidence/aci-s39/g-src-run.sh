#!/bin/sh
# Runs g-src.sh and stores g-src.log.gz / g-src.exit.
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(sh "$HERE/g-src.sh" 2>&1)
CODE=$?
printf '%s\n== g-src exit %s\n' "$OUT" "$CODE" | gzip -9n > "$HERE/g-src.log.gz"
echo "$CODE" > "$HERE/g-src.exit"
echo "g-src exit $CODE"
