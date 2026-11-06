#!/bin/sh
HERE=$(cd "$(dirname "$0")" && pwd)
START=$(date +%s)
{ sh "$HERE/g-src.sh"; echo $? > "$HERE/g-src.exit"; } 2>&1 | gzip -9n > "$HERE/g-src.log.gz"
echo "g-src exit $(cat "$HERE/g-src.exit") ($(($(date +%s) - START)) s)"
