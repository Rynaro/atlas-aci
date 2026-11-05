#!/bin/sh
# ACI-S36 real measured run, executed INSIDE a throwaway python:3.12-slim container (--rm,
# --network none). The repository is mounted read-only at /repo, the pre-downloaded wheels at
# /wheels (read-only), the output directory at /out. The provider is installed into /tmp/site
# of this container only; nothing is installed anywhere else.
set -u
MANIFEST=/repo/mcp-server/tests/fixtures/community_experiment/manifest.v1.json
SHA=3239ec59d9fce113a115ff4b27c42bfbf06715060e1b2cb7e7de8e2e435a2beb
{
  echo "== uname -m: $(uname -m)"
  echo "== python: $(python -V 2>&1)"
  echo "== pip install --no-index --find-links /wheels --target /tmp/site leidenalg==0.12.0 igraph==1.0.0"
  pip install --no-index --find-links /wheels --target /tmp/site leidenalg==0.12.0 igraph==1.0.0
  echo "== pip exit: $?"
} > /out/install.log 2>&1
export PYTHONPATH=/tmp/site PYTHONDONTWRITEBYTECODE=1
python - > /out/facts.json <<'PY'
import importlib.metadata as m, json, platform
print(json.dumps({
    "igraph": m.version("igraph"), "leidenalg": m.version("leidenalg"), "texttable": m.version("texttable"),
    "machine": platform.machine(), "python": platform.python_version(), "system": platform.system(),
}, sort_keys=True))
PY
for n in 1 2; do
  python /repo/scripts/experiment-leiden-vs-lpa.py run --repo-root /repo --manifest "$MANIFEST" \
    --manifest-sha256 "$SHA" --out /out/run$n > /out/run$n.stdout 2> /out/run$n.stderr
  echo $? > /out/run$n.exit
done
