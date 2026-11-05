#!/bin/sh
# usage: bounded.sh SECS NAME OUTFILE docker-run-args...
secs=$1; name=$2; out=$3; shift 3
docker run --rm --name "$name" "$@" > "$out" 2>&1 &
pid=$!; n=0
while kill -0 $pid 2>/dev/null && [ $n -lt "$secs" ]; do sleep 1; n=$((n+1)); done
if kill -0 $pid 2>/dev/null; then docker kill "$name" >/dev/null 2>&1 || true; echo "TIMEOUT" >> "$out"; fi
rc=0; wait $pid || rc=$?; echo "rc=$rc" >> "$out"
