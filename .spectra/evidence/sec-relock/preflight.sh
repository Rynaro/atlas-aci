#!/bin/sh
# Preflight gate body (run inside the dev container via gate.sh): the values record, then the
# UNCHANGED s11/s13/s18 stdio tests, the UNCHANGED s38 suite (version pins are expected to trip
# until the follow-up re-pins) and mypy src/. Every step runs; the exit is the worst of them.
P=/opt/venv/bin/python
worst=0
run() { echo "== $*"; "$@"; c=$?; echo "-- exit $c"; [ $c -gt $worst ] && worst=$c; }
run $P /ws/.spectra/evidence/sec-relock/preflight.py /ws/.spectra/evidence/sec-relock/preflight.json
run $P -m pytest -p no:cacheprovider -q -rf tests/slices/test_s11_stdio.py tests/slices/test_s13_stdio.py tests/slices/test_s18_stdio.py
run $P -m pytest -p no:cacheprovider -q -rf tests/slices/test_s38_*.py
run /opt/venv/bin/mypy src/
exit $worst
