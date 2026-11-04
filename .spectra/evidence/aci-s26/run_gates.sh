#!/bin/sh
# ACI-S26 gates. Usage (repo root): .spectra/evidence/aci-s26/run_gates.sh GATE...
# Gates: s26-contract regression ruff format mypy g-src g-mcp full-suite
# Each writes <gate>.log.gz and <gate>.exit (the real numeric exit code) beside this script.
EV=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$EV/../../.." && pwd)
DK="$EV/dk26.sh"
BASE=01947584046036ee4328eda03d23787ec7471a30
TMPD=${TMPDIR:-/tmp}/s26-gates.$$
mkdir -p "$TMPD"

finish() { # name logfile code
  gzip -c "$2" > "$EV/$1.log.gz"
  echo "$3" > "$EV/$1.exit"
  echo "$1 exit $3"
}

for gate in "$@"; do
  log="$TMPD/$gate.log"
  case $gate in
  s26-contract)
    start=$(date +%s)
    "$DK" sh -c '/opt/venv/bin/python -m pytest -p no:cacheprovider --durations=8 tests/slices/test_s26_*.py' > "$log" 2>&1
    code=$?
    echo "duration_seconds: $(( $(date +%s) - start )) (wall clock, including container start)" >> "$log"
    finish "$gate" "$log" $code ;;
  regression)
    "$DK" sh -c 'files=""; for f in tests/slices/test_s07_*.py tests/slices/test_s09_*.py tests/slices/test_s10_*.py tests/slices/test_s12_storage.py tests/slices/test_s14_*.py tests/slices/test_s16_*.py tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/slices/test_s21_*.py tests/slices/test_s22_*.py tests/slices/test_s23_*.py tests/slices/test_s24_*.py tests/slices/test_s25_*.py tests/slices/test_s29_*.py tests/slices/test_s31_*.py tests/slices/test_s32_*.py tests/slices/test_s37_*.py tests/test_schema_epoch.py tests/test_thesis_negatives.py tests/test_export.py; do [ -e "$f" ] && files="$files $f"; done; echo "files:$files"; /opt/venv/bin/python -m pytest -q -p no:cacheprovider $files' > "$log" 2>&1
    finish "$gate" "$log" $? ;;
  ruff)
    "$DK" /opt/venv/bin/ruff check src/ tests/ > "$log" 2>&1
    finish "$gate" "$log" $? ;;
  format)
    "$DK" /opt/venv/bin/ruff format --check src/ tests/ > "$log" 2>&1
    finish "$gate" "$log" $? ;;
  mypy)
    "$DK" /opt/venv/bin/mypy src/ > "$log" 2>&1
    finish "$gate" "$log" $? ;;
  g-src)
    (
      cd "$ROOT" || exit 9
      code=0
      S=mcp-server/src/atlas_aci
      echo "== git diff --exit-code $BASE over the gated paths"
      git diff --exit-code "$BASE" -- \
        $S/server.py $S/enforcement.py $S/config.py $S/__main__.py $S/protocol $S/query $S/tools \
        $S/extractors $S/providers $S/architecture $S/counterfactual $S/contracts $S/importers \
        $S/indexing $S/resolvers $S/reports $S/frameworks/rails_routes.py \
        $S/frameworks/rails_models.py mcp-server/pyproject.toml mcp-server/uv.lock \
        .github/workflows || code=1
      echo "== storage/ except evidence.py"
      git diff --exit-code "$BASE" -- $S/storage ":(exclude)$S/storage/evidence.py" || code=1
      echo "== changed or untracked files under mcp-server/src"
      { git diff --name-only "$BASE" -- mcp-server/src; git ls-files --others --exclude-standard mcp-server/src; } | sort -u > "$TMPD/changed.txt"
      cat "$TMPD/changed.txt"
      printf '%s\n' $S/codegraph.py $S/frameworks/__init__.py $S/frameworks/rails_jobs.py $S/storage/evidence.py | sort > "$TMPD/allowed.txt"
      if diff "$TMPD/changed.txt" "$TMPD/allowed.txt"; then echo "changed set == the four allowed files"; else echo "changed set differs from the allowed four"; code=1; fi
      echo "== prior-slice test edits (only test_s24_rails_routes, test_s25_rails_models, test_s25_isolation)"
      git diff --name-only "$BASE" -- mcp-server/tests | sort > "$TMPD/tests.txt"
      cat "$TMPD/tests.txt"
      printf '%s\n' mcp-server/tests/slices/test_s24_rails_routes.py mcp-server/tests/slices/test_s25_isolation.py mcp-server/tests/slices/test_s25_rails_models.py | sort > "$TMPD/tests-allowed.txt"
      if diff "$TMPD/tests.txt" "$TMPD/tests-allowed.txt"; then echo "tracked test edits == the three allowed files"; else echo "tracked test edits differ"; code=1; fi
      git diff -U0 "$BASE" -- mcp-server/tests/slices/test_s24_rails_routes.py mcp-server/tests/slices/test_s25_rails_models.py mcp-server/tests/slices/test_s25_isolation.py | grep -E '^[+-][^+-]'
      echo "== relation sets"
      "$DK" /opt/venv/bin/python -P -c "
from atlas_aci.storage import evidence as ev
from atlas_aci.frameworks import REGISTRY
assert len(ev.RELATIONS)==8 and ev.REFERENCE_RELATIONS==ev.RELATIONS|{'jsx','reference'}
assert ev.EDGE_RELATIONS==(ev.RELATIONS-{'rationale_for'})|{'reference'}
assert not ev.RAILS_JOB_RELATIONS & (ev.RELATIONS|ev.REFERENCE_RELATIONS|ev.EDGE_RELATIONS)
assert ev.RAILS_JOB_RELATIONS <= ev.ASSERTION_RELATIONS and REGISTRY=={'rails','rails_jobs','rails_models'}
print('relation sets unchanged; registry', sorted(REGISTRY))" || code=1
      exit $code
    ) > "$log" 2>&1
    finish "$gate" "$log" $? ;;
  g-mcp)
    "$DK" /opt/venv/bin/python -P -m atlas_aci tools --repo /ws > "$TMPD/tools.json" 2> "$TMPD/tools.err"
    code=$?
    sha=$(shasum -a 256 "$TMPD/tools.json" | cut -d' ' -f1)
    bytes=$(wc -c < "$TMPD/tools.json" | tr -d ' ')
    { echo "tools json sha256: $sha"; echo "tools json bytes: $bytes"; echo "pinned sha256:    94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"; echo "pinned bytes:     1631472"; cat "$TMPD/tools.err"; } > "$log"
    if [ $code -eq 0 ] && [ "$sha" = 94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56 ] && [ "$bytes" = 1631472 ]; then echo "MATCH" >> "$log"; else echo "MISMATCH" >> "$log"; code=1; fi
    finish "$gate" "$log" $code ;;
  full-suite)
    echo "# full suite, pytest -q; deselected: tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition (known flaky, has hung for hours in the dev image). Known pre-existing failure under uid 1000: tests/slices/test_s01_contract.py::test_call_deadline_after_initialization" > "$log"
    "$DK" /opt/venv/bin/python -m pytest -q -p no:cacheprovider --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition >> "$log" 2>&1
    finish "$gate" "$log" $? ;;
  *) echo "unknown gate $gate"; exit 2 ;;
  esac
done
