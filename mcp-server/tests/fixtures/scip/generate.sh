#!/bin/sh
# Regenerates index.scip, atlas-scip-manifest.json, labels.json and provenance.json.
#
# Needs docker and network (node image, `npm ci`). The tests never need either:
# they only read the committed outputs. Run from anywhere:
#
#   ATLAS_ACI_DEV_IMAGE=atlas-aci-dev sh generate.sh
#
# ATLAS_ACI_DEV_IMAGE is any image with this repository's dev environment
# (`uv sync --extra dev`, i.e. Python + official protobuf) at /opt/venv.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../../../.." && pwd)
NODE_IMAGE="node@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da"
DEV_IMAGE="${ATLAS_ACI_DEV_IMAGE:-atlas-aci-dev}"
FIXTURE_REL="mcp-server/tests/fixtures/scip"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# 1. Produce the artifact with the pinned producer over a clean copy of project/.
cp -R "$HERE/project" "$WORK/proj"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$WORK/proj":/proj -w /proj "$NODE_IMAGE" sh -c '
    npm ci --ignore-scripts --no-audit --no-fund
    node_modules/.bin/scip-typescript index --cwd /proj --output /proj/index.scip'
cp "$WORK/proj/index.scip" "$HERE/index.scip"

# 2. Producer-time sidecar: declared encoding + working-tree hashes of every document.
#    scip-typescript 0.4.0 counts UTF-16 code units and never sets Document.position_encoding.
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONPATH=/ws/mcp-server/src \
  -v "$REPO_ROOT":/ws -w /ws/mcp-server "$DEV_IMAGE" /opt/venv/bin/python -m atlas_aci \
  scip-manifest --repo "/ws/$FIXTURE_REL/project" --position-encoding utf-16 \
  --output "/ws/$FIXTURE_REL/atlas-scip-manifest.json" "/ws/$FIXTURE_REL/index.scip"

# 3. Independent labels (no atlas_aci import): official bindings + Python str arithmetic.
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$REPO_ROOT":/ws -w /ws/mcp-server "$DEV_IMAGE" /opt/venv/bin/python \
  "$FIXTURE_REL/generate_labels.py" "$FIXTURE_REL/index.scip" "$FIXTURE_REL/project" \
  "$FIXTURE_REL/labels.json"

# 4. Provenance.
HERE="$HERE" NODE_IMAGE="$NODE_IMAGE" python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

here = Path(os.environ["HERE"])
lock = json.loads((here / "project/package-lock.json").read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
provenance = {
    "artifact_sha256": sha(here / "index.scip"),
    "manifest_sha256": sha(here / "atlas-scip-manifest.json"),
    "node_image": os.environ["NODE_IMAGE"],
    "producer": {
        "name": "@sourcegraph/scip-typescript",
        "version": lock["packages"]["node_modules/@sourcegraph/scip-typescript"]["version"],
        "typescript": lock["packages"]["node_modules/typescript"]["version"],
        "package_lock_sha256": sha(here / "project/package-lock.json"),
        "package_json_sha256": sha(here / "project/package.json"),
    },
    "schema": {
        "repository": "https://github.com/scip-code/scip",
        "tag": "v0.6.1",
        "commit": "9d5796159c97c3b107355c424f6c06fb1a0ea01c",
        "scip_proto_sha256": sha(here / "../../../src/atlas_aci/providers/scip.proto"),
    },
    "bindings": {
        "scip_pb2_sha256": sha(here / "scip_pb2.py"),
        "generator": "grpcio-tools==1.85.1 (protoc gencode 7.35.1)",
    },
    "sources": {
        str(p.relative_to(here / "project")): sha(p)
        for p in sorted((here / "project/src").glob("*.ts"))
    },
}
(here / "provenance.json").write_text(json.dumps(provenance, indent=1, sort_keys=True) + "\n")
PY
echo "regenerated $FIXTURE_REL"
