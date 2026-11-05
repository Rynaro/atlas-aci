#!/bin/sh
# Regenerate the S38 golden transcripts against the re-locked SDK (mcp 1.30.0) in the new dev
# image, then compare each with the committed (mcp 1.27.0) golden. Usage: regen-goldens.sh SCRATCH
# Step 1 (this script): capture into SCRATCH/out (the repo's fixtures are NOT touched).
# The caller then copies SCRATCH/out/transcripts/* over the fixtures, edits PROVENANCE, runs
# `test_s38_support.py manifest`, and checks the diff with golden-diff.py.
set -eu
SCRATCH=$1
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
DEV_HOME=${DEV_HOME:-$SCRATCH/home}
mkdir -p "$DEV_HOME" "$SCRATCH"
chmod 777 "$DEV_HOME"
rm -rf "$SCRATCH/out" "$SCRATCH/fx"
mkdir -p "$SCRATCH/out" "$SCRATCH/fx"
chmod 777 "$SCRATCH/out" "$SCRATCH/fx"
NAME=sec-relock-regen-$$
( sleep 900; docker kill "$NAME" > /dev/null 2>&1 ) & WD=$!
trap 'kill $WD 2>/dev/null; docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT INT TERM
docker run --rm --name "$NAME" --user 1000:1000 -e HOME=/hc -e PYTHONPATH=/ws/mcp-server/src \
    -v "$DEV_HOME":/hc -v "$REPO":/ws:ro -v "$SCRATCH/out":/out -v "$SCRATCH/fx":/fx \
    -w /ws/mcp-server atlas-aci-dev-relock \
    /opt/venv/bin/python /ws/mcp-server/tests/slices/test_s38_support.py capture \
    --out /out --fixture /fx --label sec-relock --seed 0 > "$SCRATCH/capture.stdout" 2> "$SCRATCH/capture.stderr"
echo "capture ok: $SCRATCH/out"
