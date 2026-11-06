#!/bin/sh
# ACI-S41 gates in the dev image. usage: run-gates.sh NAME...   (all when none is given)
#   s41-contract regression ruff format mypy full-suite
HERE=$(cd "$(dirname "$0")" && pwd)
PYTEST="/opt/venv/bin/python -m pytest -q -p no:cacheprovider"
DESEL="--deselect tests/slices/test_s01_contract.py::test_call_deadline_after_initialization --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition"
run() {
    case $1 in
    s41-contract)
        "$HERE/gate.sh" s41-contract sh -c "$PYTEST tests/slices/test_s41_contract.py && $PYTEST tests/slices/test_s41_*.py --durations=10" ;;
    regression)
        "$HERE/gate.sh" regression sh -c "$PYTEST $DESEL \
  tests/slices/test_s08_*.py tests/slices/test_s09_*.py tests/slices/test_s12_storage.py \
  tests/slices/test_s13_*.py tests/slices/test_s14_capabilities.py tests/slices/test_s21_lifecycle.py \
  tests/slices/test_s33_isolation.py tests/slices/test_s34_isolation.py tests/slices/test_s35_isolation.py \
  tests/slices/test_s36_isolation.py tests/slices/test_s37_evidence_report.py \
  tests/slices/test_s38_*.py tests/slices/test_s39_isolation.py tests/slices/test_s40_isolation.py \
  tests/slices/test_s40_injection.py tests/test_schema_epoch.py tests/test_thesis_negatives.py \
  tests/test_export.py" ;;
    ruff)
        "$HERE/gate.sh" ruff sh -c '/opt/venv/bin/ruff check src/ tests/ && /opt/venv/bin/ruff check ../scripts/ --exclude fingerprint-fixture' ;;
    format)
        "$HERE/gate.sh" format sh -c '/opt/venv/bin/ruff format --check src/ tests/ && /opt/venv/bin/ruff format --check ../scripts/ --exclude fingerprint-fixture' ;;
    mypy)
        "$HERE/gate.sh" mypy sh -c '/opt/venv/bin/mypy src/ && /opt/venv/bin/mypy ../scripts/' ;;
    full-suite)
        "$HERE/gate.sh" full-suite sh -c "$PYTEST $DESEL" ;;
    *) echo "unknown gate $1"; return 2 ;;
    esac
}
[ $# -gt 0 ] || set -- s41-contract regression ruff format mypy full-suite
for g in "$@"; do run "$g"; done
