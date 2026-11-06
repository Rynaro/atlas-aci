#!/bin/sh
# ACI-S39 gates in the dev image. usage: run-gates.sh NAME...   (all when none is given)
#   s39-contract regression ruff format mypy full-suite
HERE=$(cd "$(dirname "$0")" && pwd)
DESEL="--deselect tests/slices/test_s01_contract.py::test_call_deadline_after_initialization --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition"
run() {
    case $1 in
    s39-contract)
        "$HERE/gate.sh" s39-contract sh -c 'ATLAS_S39_REQUIRE_NONLOOPBACK=1 /opt/venv/bin/python -m pytest -q -p no:cacheprovider tests/slices/test_s39_contract.py && ATLAS_S39_REQUIRE_NONLOOPBACK=1 /opt/venv/bin/python -m pytest -q -p no:cacheprovider tests/slices/test_s39_*.py --durations=10' ;;
    regression)
        "$HERE/gate.sh" regression sh -c "/opt/venv/bin/python -m pytest -q -p no:cacheprovider $DESEL \
  tests/slices/test_s11_*.py tests/slices/test_s12_admission.py tests/slices/test_s13_*.py \
  tests/slices/test_s18_stdio*.py tests/slices/test_s23_*.py \
  tests/slices/test_s24_isolation.py tests/slices/test_s25_isolation.py tests/slices/test_s26_isolation.py \
  tests/slices/test_s27_isolation.py tests/slices/test_s28_isolation.py tests/slices/test_s30_isolation.py \
  tests/slices/test_s33_isolation.py tests/slices/test_s34_isolation.py tests/slices/test_s35_isolation.py \
  tests/slices/test_s36_isolation.py tests/slices/test_s37_evidence_report.py tests/slices/test_s38_*.py \
  tests/slices/test_s05_contract.py tests/slices/test_s20_mcp.py tests/slices/test_s22_isolation.py \
  tests/slices/test_s29_openapi_diff.py tests/slices/test_s31_architecture_rules.py \
  tests/test_server*.py tests/test_enforcement.py tests/test_thesis_negatives.py" ;;
    ruff)
        "$HERE/gate.sh" ruff sh -c '/opt/venv/bin/ruff check src/ tests/ && /opt/venv/bin/ruff check ../scripts/ --exclude fingerprint-fixture' ;;
    format)
        "$HERE/gate.sh" format sh -c '/opt/venv/bin/ruff format --check src/ tests/ && /opt/venv/bin/ruff format --check ../scripts/ --exclude fingerprint-fixture' ;;
    mypy)
        "$HERE/gate.sh" mypy sh -c '/opt/venv/bin/mypy src/ && /opt/venv/bin/mypy ../scripts/' ;;
    full-suite)
        "$HERE/gate.sh" full-suite /opt/venv/bin/python -m pytest -q -p no:cacheprovider $DESEL ;;
    *) echo "unknown gate $1"; return 2 ;;
    esac
}
[ $# -gt 0 ] || set -- s39-contract regression ruff format mypy full-suite
for g in "$@"; do run "$g"; done
