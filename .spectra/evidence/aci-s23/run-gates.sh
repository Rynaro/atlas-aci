#!/bin/bash
# ACI-S23 gates: each gate writes <name>.log.gz and <name>.exit under .spectra/evidence/aci-s23/.
# Usage: run-gates.sh <gate>...   (all Python runs go through dk23.sh, i.e. Docker as uid 1000)
cd "$(dirname "$0")/../../../mcp-server" || exit 99
E=../.spectra/evidence/aci-s23
D=$E/dk23.sh
BASE=1cedf60
emit() { # name, then the command lines on stdin are run in a subshell
  local name=$1
  local rc
  ( set -o pipefail; bash -s ) 2>&1 | gzip -n > "$E/$name.log.gz"
  rc=${PIPESTATUS[0]}
  echo "$rc" > "$E/$name.exit"
  echo "$name exit=$rc"
}
gate() {
  case "$1" in
    s23-contract) emit "$1" <<X
cd $PWD; $D /opt/venv/bin/python -m pytest -q -p no:cacheprovider tests/slices/test_s23_contract.py
X
;;
    s23-supporting) emit "$1" <<X
cd $PWD; $D /opt/venv/bin/python -m pytest -q -p no:cacheprovider tests/slices/test_s23_metrics.py tests/slices/test_s23_recorder.py tests/slices/test_s23_isolation.py tests/slices/test_s23_support.py
X
;;
    regression) emit "$1" <<X
cd $PWD; $D /opt/venv/bin/python -m pytest -q -p no:cacheprovider tests/slices/test_s01_contract.py tests/slices/test_s18_*.py tests/slices/test_s22_*.py
X
;;
    ruff) emit "$1" <<X
cd $PWD; rc=0
$D /opt/venv/bin/ruff check src/ tests/ || rc=1
$D /opt/venv/bin/ruff check ../scripts/ --exclude fingerprint-fixture || rc=1
$D /opt/venv/bin/ruff check ../atlas/evals/span_metrics.py || rc=1
exit \$rc
X
;;
    format) emit "$1" <<X
cd $PWD; rc=0
$D /opt/venv/bin/ruff format --check src/ tests/ || rc=1
$D /opt/venv/bin/ruff format --check ../scripts/ --exclude fingerprint-fixture || rc=1
$D /opt/venv/bin/ruff format --check ../atlas/evals/span_metrics.py || rc=1
exit \$rc
X
;;
    mypy) emit "$1" <<X
cd $PWD; rc=0
$D /opt/venv/bin/mypy src/ || rc=1
$D /opt/venv/bin/mypy ../scripts/ || rc=1
$D /opt/venv/bin/mypy ../atlas/evals/span_metrics.py || rc=1
exit \$rc
X
;;
    full-suite) emit "$1" <<X
cd $PWD; $D /opt/venv/bin/python -m pytest -q -p no:cacheprovider
X
;;
    g-src) emit "$1" <<X
cd $PWD/..; git diff --exit-code $BASE -- mcp-server/src mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/tests/fixtures .github/workflows atlas/evals/capture_context_baseline.py atlas/evals/measure_context_yield.py && echo "no diff against $BASE"
X
;;
    g-mcp) emit "$1" <<X
cd $PWD; $D /opt/venv/bin/python /hc/gmcp.py
X
;;
    *) echo "unknown gate $1";;
  esac
}
for g in "$@"; do gate "$g"; done
