#!/bin/sh
# Regenerates runs/{a,b}/{coverage.json,environment.json,atlas-coverage-manifest.json},
# labels.json and provenance.json.
#
# Needs docker and network (python images, `pip install --require-hashes`). The tests never
# need either: they only read the committed outputs. Run from anywhere:
#
#   ATLAS_ACI_DEV_IMAGE=atlas-aci-dev sh generate.sh
#
# ATLAS_ACI_DEV_IMAGE is any image with this repository's dev environment (`uv sync
# --extra dev`) at /opt/venv. It runs `atlas-aci coverage-probe` and `coverage-manifest`.
# Run A is produced on CPython 3.12 and run B on CPython 3.13; both use the same hash-pinned
# coverage.py / pytest-cov / pytest from requirements.txt over a clean copy of project/.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../../../.." && pwd)
PY312_IMAGE="python@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30"
PY313_IMAGE="python@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1"
DEV_IMAGE="${ATLAS_ACI_DEV_IMAGE:-atlas-aci-dev}"
FIXTURE_REL="mcp-server/tests/fixtures/coverage"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
UIDGID="$(id -u):$(id -g)"

dev() {
  docker run --rm --user "$UIDGID" -e HOME=/tmp -e PYTHONPATH=/ws/mcp-server/src \
    -v "$REPO_ROOT":/ws -w /ws/mcp-server "$DEV_IMAGE" /opt/venv/bin/python -m atlas_aci "$@"
}

# 0. The probe is printed by atlas-aci itself and piped to the TEST interpreter (Atlas never
#    spawns it): `atlas-aci coverage-probe | <venv>/bin/python -I -`.
dev coverage-probe > "$WORK/probe.py"

produce() {
  run=$1
  image=$2
  mkdir -p "$WORK/out-$run" "$HERE/runs/$run"
  docker run --rm --user "$UIDGID" -e HOME=/tmp \
    -v "$HERE":/fx:ro -v "$WORK/probe.py":/probe.py:ro -v "$WORK/out-$run":/out "$image" sh -c '
      set -eu
      python -m venv /tmp/venv
      /tmp/venv/bin/pip install --quiet --disable-pip-version-check --no-cache-dir \
        --require-hashes -r /fx/requirements.txt
      cp -R /fx/project /tmp/proj
      cd /tmp/proj
      /tmp/venv/bin/python -I - < /probe.py > /out/environment.json
      /tmp/venv/bin/python -m pytest --rootdir=/tmp/proj --cov=. --cov-config=.coveragerc \
        --cov-context=test -q
      /tmp/venv/bin/coverage json --show-contexts -o /out/coverage.json
      /tmp/venv/bin/python /fx/generate_labels.py --project /tmp/proj --data /tmp/proj/.coverage \
        --report /out/coverage.json --run '"$run"' --out /out/labels.json'
  cp "$WORK/out-$run/coverage.json" "$HERE/runs/$run/coverage.json"
  cp "$WORK/out-$run/environment.json" "$HERE/runs/$run/environment.json"
  # 1. Producer-time sidecar: working-tree hashes of every measured file and every test file.
  dev coverage-manifest --repo "/ws/$FIXTURE_REL/project" \
    --environment "/ws/$FIXTURE_REL/runs/$run/environment.json" \
    --output "/ws/$FIXTURE_REL/runs/$run/atlas-coverage-manifest.json" \
    "/ws/$FIXTURE_REL/runs/$run/coverage.json"
}

produce a "$PY312_IMAGE"
produce b "$PY313_IMAGE"

# 2. Independent labels (no atlas_aci import), merged per run.
WORK="$WORK" HERE="$HERE" python3 - <<'PY'
import json
import os
from pathlib import Path

work, here = Path(os.environ["WORK"]), Path(os.environ["HERE"])
runs = {r: json.loads((work / f"out-{r}/labels.json").read_text()) for r in ("a", "b")}
(here / "labels.json").write_text(json.dumps({"runs": runs}, indent=1, sort_keys=True) + "\n")
PY

# 3. Provenance.
WORK="$WORK" HERE="$HERE" PY312_IMAGE="$PY312_IMAGE" PY313_IMAGE="$PY313_IMAGE" python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

here = Path(os.environ["HERE"])
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
provenance = {
    "images": {"a": os.environ["PY312_IMAGE"], "b": os.environ["PY313_IMAGE"]},
    "requirements_sha256": sha(here / "requirements.txt"),
    "producer": {"coverage.py": "7.16.2", "pytest-cov": "7.1.0", "pytest": "9.1.1"},
    "context_mode": "pytest-cov:test",
    "report_format": 3,
    "runs": {
        run: {
            "artifact_sha256": sha(here / f"runs/{run}/coverage.json"),
            "environment_sha256": sha(here / f"runs/{run}/environment.json"),
            "manifest_sha256": sha(here / f"runs/{run}/atlas-coverage-manifest.json"),
        }
        for run in ("a", "b")
    },
    "sources": {
        str(p.relative_to(here / "project")): sha(p)
        for p in sorted((here / "project").rglob("*"))
        if p.is_file() and p.suffix == ".py"
    },
}
(here / "provenance.json").write_text(json.dumps(provenance, indent=1, sort_keys=True) + "\n")
PY
echo "regenerated $FIXTURE_REL"
