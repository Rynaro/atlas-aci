#!/bin/sh
# ACI-S38 G-REG file set, run through gate.sh (inside the dev container, cwd /ws/mcp-server).
exec "$(dirname "$0")/gate.sh" regression sh -c '/opt/venv/bin/python -m pytest -q -p no:cacheprovider \
  --deselect tests/slices/test_s01_contract.py::test_call_deadline_after_initialization \
  --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition \
  tests/slices/test_s11_*.py tests/slices/test_s12_*.py tests/slices/test_s13_*.py \
  tests/slices/test_s14_*.py tests/slices/test_s16_*.py tests/slices/test_s17_*.py \
  tests/slices/test_s18_*.py tests/slices/test_s20_*.py tests/slices/test_s21_*.py \
  tests/slices/test_s23_*.py tests/slices/test_s24_isolation.py tests/slices/test_s25_isolation.py \
  tests/slices/test_s26_isolation.py tests/slices/test_s27_isolation.py \
  tests/slices/test_s28_isolation.py tests/slices/test_s3[0-6]_isolation.py \
  tests/slices/test_s05_contract.py tests/slices/test_s35_*.py tests/slices/test_s37_*.py \
  tests/test_server*.py tests/test_schema_epoch.py tests/test_thesis_negatives.py \
  tests/slices/test_s01_contract.py'
