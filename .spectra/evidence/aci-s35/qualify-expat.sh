#!/bin/sh
# Day-1 (d): run qualify_expat.py under the dev image (3.12), python:3.12-slim and python:3.14.
# No network; the repository is mounted read-only; output goes to a scratch dir. Parity is the
# equality of the `results` objects.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
OUT=${1:-$HERE/expat}
mkdir -p "$OUT"
chmod 777 "$OUT"
run() { # label image
    docker run --rm --network none --read-only -v "$REPO":/ws:ro -v "$OUT":/out "$2" \
        timeout 120 python -I /ws/.spectra/evidence/aci-s35/qualify_expat.py "/out/$1.json"
}
run dev atlas-aci-dev-s16
run py312slim python:3.12-slim
run py314 python:3.14
