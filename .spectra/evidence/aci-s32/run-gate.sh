#!/bin/sh
# usage: run-gate.sh <name> <command...>   (runs inside the dev container via dk32.sh)
# Writes <name>.log.gz and <name>.exit (plus <name>.seconds) next to this script.
dir=$(cd "$(dirname "$0")" && pwd)
name=$1
shift
start=$(date +%s)
"$dir/dk32.sh" "$@" > "$dir/$name.log" 2>&1
code=$?
end=$(date +%s)
gzip -f "$dir/$name.log"
echo "$code" > "$dir/$name.exit"
echo "$((end - start))" > "$dir/$name.seconds"
exit 0
