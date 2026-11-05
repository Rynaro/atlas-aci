#!/bin/sh
# Runs g-src.sh and stores g-src.log.gz / g-src.exit.
HERE=$(cd "$(dirname "$0")" && pwd)
sh "$HERE/g-src.sh" 2>&1 | gzip -9n > "$HERE/g-src.log.gz"
CODE=$(sh "$HERE/g-src.sh" > /dev/null 2>&1; echo $?)
echo "$CODE" > "$HERE/g-src.exit"
echo "g-src exit $CODE"
