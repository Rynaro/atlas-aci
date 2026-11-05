#!/bin/sh
# ACI-S34 gate runner. Usage: run-gate.sh NAME COMMAND...
# Runs COMMAND with `sh -c`, writes NAME.log.gz and NAME.exit (the real numeric exit code) here.
# Header notes: dev image python 3.12 as uid 1000 (CI is python 3.14); known pre-existing
# failures: test_s01_contract::test_call_deadline_after_initialization (uid 1000) and the
# flaky, deselected test_s08_integration::test_nonregular_pack_does_not_block_acquisition.
NAME=$1; shift
DIR=$(cd "$(dirname "$0")" && pwd)
LOG=$(mktemp)
{
    echo "# gate: $NAME"
    echo "# command: $*"
    echo "# started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# base: 0a70041; branch aci-s34; dev image atlas-aci-dev-s16 (python 3.12, uid 1000)"
    echo "# known failures: test_s01_contract::test_call_deadline_after_initialization (uid 1000);"
    echo "#   test_s08_integration::test_nonregular_pack_does_not_block_acquisition (flaky, deselected)"
} >"$LOG"
START=$(date +%s)
sh -c "$*" >>"$LOG" 2>&1
CODE=$?
END=$(date +%s)
echo "# finished: $(date -u +%Y-%m-%dT%H:%M:%SZ) duration_s=$((END - START)) exit=$CODE" >>"$LOG"
gzip -c "$LOG" >"$DIR/$NAME.log.gz"
rm -f "$LOG"
echo "$CODE" >"$DIR/$NAME.exit"
echo "$NAME exit=$CODE duration_s=$((END - START))"
exit 0
