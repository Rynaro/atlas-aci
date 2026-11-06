#!/bin/sh
# ACI-S42 production scenario (G-PROD): run the recorder inside the PRODUCTION image, then aggregate.
#
#   prod-scenario.sh [IMAGE]            positive run (default IMAGE atlas-aci:s42)
#   S42_NEGATIVE=1 prod-scenario.sh     negative control: the main isolation policy is over-granted
#
# Every container is --rm, --network none, named s42-$$-N, run as user atlas with an explicit
# --entrypoint, and bounded by a host watchdog (docker kill after S42_TIMEOUT seconds, FAIL 124).
# Work directories are container-local (/tmp/s42); the output volume is chowned by one root init
# container. Nothing here reads git: the checks hold for an untracked, staged or committed tree.
# Outputs: $S42_OUT (default prod-run, or prod-run-negative for the negative control).
set -eu

IMAGE=${1:-atlas-aci:s42}
NEGATIVE=${S42_NEGATIVE:-0}
TIMEOUT=${S42_TIMEOUT:-300}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
if [ "$NEGATIVE" = "1" ]; then
  OUT=${S42_OUT:-$HERE/prod-run-negative}
  SUMMARY=production-scenario-negative.json
else
  OUT=${S42_OUT:-$HERE/prod-run}
  SUMMARY=production-scenario.json
fi
PY=/usr/local/bin/python
VOL=s42-$$-out
N=0
NAMES=""
FAILS=0
RESULTS=$OUT/results.tsv
EXPECTED_NEGATIVE="isolation.positive_controls,isolation.typed_denials,isolation.unauthorized_zero"

cleanup() {
  for name in $NAMES; do docker kill "$name" >/dev/null 2>&1 || true; done
  docker volume rm -f "$VOL" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

next_name() {
  N=$((N + 1))
  NAME=s42-$$-$N
  NAMES="$NAMES $NAME"
}

# run_c SECONDS docker-run-args...   (the name is $NAME; stdout/stderr go to the caller's redirects)
run_c() {
  secs=$1
  shift
  docker run --rm --name "$NAME" "$@" &
  pid=$!
  (sleep "$secs"; docker kill "$NAME" >/dev/null 2>&1 && echo "FAIL 124 watchdog killed $NAME" >&2) &
  wd=$!
  rc=0
  wait "$pid" || rc=$?
  kill "$wd" 2>/dev/null || true
  wait "$wd" 2>/dev/null || true
  return "$rc"
}

# in_image NAME-LESS: run a python -I snippet in the image with the repo and extra mounts
py_image() { # py_image "docker args" -c code args...
  next_name
  extra=$1
  shift
  # shellcheck disable=SC2086
  run_c "$TIMEOUT" --network none --user atlas --entrypoint $PY $extra "$IMAGE" -I "$@"
}

record() { # record NAME STATUS DETAIL
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$RESULTS"
  printf '%s %s: %s\n' "$2" "$1" "$3"
  if [ "$2" != "PASS" ]; then FAILS=$((FAILS + 1)); fi
}

rm -rf "$OUT"
mkdir -p "$OUT"
chmod 777 "$OUT"
: >"$RESULTS"

MANIFEST=/src/atlas/evals/shared/manifest.v1.json
MANIFEST_MOUNT=""
if [ "$NEGATIVE" = "1" ]; then
  # The over-granted policy: p4 gains search_text on b, so deny case D04 (ungranted_tool) succeeds.
  next_name
  run_c "$TIMEOUT" --network none --user atlas --entrypoint $PY -v "$REPO:/src:ro" "$IMAGE" -I -c '
import json
m = json.load(open("/src/atlas/evals/shared/manifest.v1.json"))
for g in m["grants"]["main"]:
    if g["subject"] == "p4" and g["repository"] == "b":
        g["tools"] = sorted(set(g["tools"]) | {"search_text"})
print(json.dumps(m, indent=1, sort_keys=True, ensure_ascii=True))
' >"$OUT/negative-manifest.json"
  MANIFEST=/m/manifest.json
  MANIFEST_MOUNT="-v $OUT/negative-manifest.json:/m/manifest.json:ro"
  echo "NEGATIVE CONTROL: main policy over-granted (p4 gains search_text on repository b)"
fi

# ---------------------------------------------------------------------------- P0 identity
IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$IMAGE")
echo "IMAGE: $IMAGE id=$IMAGE_ID created=$(docker image inspect --format '{{.Created}}' "$IMAGE")"
py_image "-v $REPO:/src:ro" -c '
import hashlib, importlib.metadata as m, json, pathlib, sys
import atlas_aci
root = pathlib.Path(atlas_aci.__file__).parent
files = sorted(root.rglob("*.py"))
digest = hashlib.sha256(json.dumps([[str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()] for p in files]).encode()).hexdigest()
src = pathlib.Path("/src/mcp-server/src/atlas_aci")
sfiles = sorted(src.rglob("*.py"))
sdigest = hashlib.sha256(json.dumps([[str(p.relative_to(src)), hashlib.sha256(p.read_bytes()).hexdigest()] for p in sfiles]).encode()).hexdigest()
print("python", sys.version.split()[0])
for name in ("mcp", "pyjwt", "cryptography", "httpx", "atlas-aci"):
    print("version", name, m.version(name))
print("installed_files", len(files), digest)
print("source_files", len(sfiles), sdigest)
print("same_tree", digest == sdigest and len(files) == len(sfiles) > 0)
' >"$OUT/p0.txt"
cat "$OUT/p0.txt"
if grep -q '^version mcp 1.30.0$' "$OUT/p0.txt"; then
  record P0-sdk-version PASS "mcp 1.30.0 in the production image"
else
  record P0-sdk-version FAIL "mcp version differs"
fi
if grep -q '^same_tree True$' "$OUT/p0.txt"; then
  record P0-installed-equals-source PASS "installed atlas_aci/**/*.py equals mcp-server/src"
else
  record P0-installed-equals-source FAIL "the installed package differs from mcp-server/src"
fi

# ------------------------------------------------------------------------------- P1 record
docker volume create "$VOL" >/dev/null
next_name
run_c 60 --user root --entrypoint /bin/chown -v "$VOL:/out" "$IMAGE" atlas:atlas /out
next_name
P1_RC=0
# shellcheck disable=SC2086
run_c "$TIMEOUT" --network none --user atlas --entrypoint $PY -v "$REPO:/src:ro" $MANIFEST_MOUNT \
  -v "$VOL:/out" -w /tmp "$IMAGE" -I /src/scripts/eval/s42_record.py --manifest "$MANIFEST" \
  --out /out/raw --work /tmp/s42 --image "$IMAGE_ID" >"$OUT/recorder.stdout" 2>"$OUT/recorder.stderr" || P1_RC=$?
tail -3 "$OUT/recorder.stderr" || true
if [ "$P1_RC" = "0" ]; then
  record P1-recorder-in-production-image PASS "recorder exit 0 as user atlas, --network none, python -I"
else
  record P1-recorder-in-production-image FAIL "recorder exit $P1_RC"
fi

# ----------------------------------------------------------------------------- P2 aggregate
mkdir -p "$OUT/raw"
next_name
run_c 120 --user atlas --entrypoint /bin/tar -v "$VOL:/out:ro" "$IMAGE" -C /out/raw -cf - . | tar -C "$OUT/raw" -xf -
RAW_COUNT=$(find "$OUT/raw" -name '*.json.gz' | wc -l | tr -d ' ')
mkdir -p "$OUT/report"
chmod 777 "$OUT/report"
AGG_RC=0
# shellcheck disable=SC2086
py_image "-v $REPO:/src:ro $MANIFEST_MOUNT -v $OUT/raw:/raw:ro -v $OUT/report:/report" \
  /src/scripts/eval/s42_aggregate.py --manifest "$MANIFEST" --raw /raw --out /report \
  >"$OUT/aggregate.stdout" 2>"$OUT/aggregate.stderr" || AGG_RC=$?
cat "$OUT/aggregate.stdout" "$OUT/aggregate.stderr" || true
FACTS_RC=0
py_image "-v $OUT/report:/r:ro" -c '
import json
d = json.load(open("/r/readiness-report.json"))
r = d["report"]
iso, comp = r["isolation"], r["compatibility"]
w = r["capacity"]["workload"]
print("verdict", r["verdict"])
print("failed", ",".join(r["failed_checks"]) or "none")
print("iso_rows", iso["rows_total"])
print("compat_cells", len(comp["cells"]))
print("interrupt_records", len([c for c in r["interruption"]["cases"] if c["valid"] is not None]))
print("capacity_n", w["observed"]["latency_ms"]["n"] if w else 0)
print("teeth", r["checks"]["isolation.ledger_teeth"], r["checks"]["interrupt.teeth_detected"])
print("unauthorized", iso["unauthorized"]["main"], iso["unauthorized"]["revocation"], iso["unauthorized"]["teeth"])
print("sha", d["canonical_sha256"])
print("image", r["environment"]["observed"].get("image"))
print("env", r["environment"]["observed"].get("python"), r["environment"]["observed"].get("platform"))
' >"$OUT/facts.txt" 2>"$OUT/facts.stderr" || FACTS_RC=$?
cat "$OUT/facts.txt" || true
fact() { sed -n "s/^$1 //p" "$OUT/facts.txt" | head -1; }

if [ "$NEGATIVE" = "1" ]; then
  VERDICT=$(fact verdict)
  FAILED=$(fact failed)
  if [ "$VERDICT" = "blocked" ]; then
    record N1-verdict-blocked PASS "the over-granted policy gives verdict blocked"
  else
    record N1-verdict-blocked FAIL "verdict is '$VERDICT', expected blocked"
  fi
  if [ "$FAILED" = "$EXPECTED_NEGATIVE" ]; then
    record N2-failed-checks-exact PASS "failed checks are exactly: $FAILED"
  else
    record N2-failed-checks-exact FAIL "failed checks '$FAILED', expected '$EXPECTED_NEGATIVE'"
  fi
  case "$(fact unauthorized)" in
    "0 0 "*) record N3-unauthorized-counted FAIL "the main ledger shows no unauthorized access" ;;
    *) record N3-unauthorized-counted PASS "unauthorized (main revocation teeth): $(fact unauthorized)" ;;
  esac
  case ",$FAILED," in
    *,capacity.*|*,compat.*|*,interrupt.*|*,environment.*|*,records.*)
      record N4-other-checks-intact FAIL "an unrelated check failed: $FAILED" ;;
    *) record N4-other-checks-intact PASS "environment, interruption, compat and capacity checks still pass" ;;
  esac
else
  if [ "$AGG_RC" = "0" ] && [ "$(fact verdict)" = "ready_for_operator_review" ] && [ "$(fact failed)" = "none" ]; then
    record P2-verdict PASS "ready_for_operator_review, failed checks none"
  else
    record P2-verdict FAIL "aggregation exit $AGG_RC verdict '$(fact verdict)' failed '$(fact failed)'"
  fi
  [ "${RAW_COUNT:-0}" -ge 25 ] && record P2-records-non-vacuous PASS "$RAW_COUNT raw records" || record P2-records-non-vacuous FAIL "only $RAW_COUNT raw records"
  [ "$(fact iso_rows)" -ge 30 ] 2>/dev/null && record P2-isolation-rows PASS "$(fact iso_rows) ledger rows" || record P2-isolation-rows FAIL "ledger rows '$(fact iso_rows)'"
  [ "$(fact compat_cells)" -ge 9 ] 2>/dev/null && record P2-compat-cells PASS "$(fact compat_cells) recorded cells" || record P2-compat-cells FAIL "cells '$(fact compat_cells)'"
  [ "$(fact interrupt_records)" = "10" ] && record P2-interrupt-records PASS "10 interruption records (8 recoveries, 2 teeth)" || record P2-interrupt-records FAIL "interruption records '$(fact interrupt_records)'"
  [ "$(fact capacity_n)" = "72" ] && record P2-capacity-calls PASS "72 workload calls in one record" || record P2-capacity-calls FAIL "capacity n '$(fact capacity_n)'"
  [ "$(fact teeth)" = "pass pass" ] && record P2-teeth-checks PASS "ledger and recovery teeth checks pass" || record P2-teeth-checks FAIL "teeth '$(fact teeth)'"
  case "$(fact unauthorized)" in
    "0 0 "[1-9]*) record P2-ledger-counts PASS "unauthorized (main revocation teeth) = $(fact unauthorized): zero within the enumerated cases, and the over-granted teeth run fires" ;;
    *) record P2-ledger-counts FAIL "unauthorized counts '$(fact unauthorized)'" ;;
  esac
  [ "$(fact image)" = "$IMAGE_ID" ] && record P2-environment-recorded PASS "report names the producing image, python $(fact env)" || record P2-environment-recorded FAIL "image '$(fact image)'"

  # ------------------------------------------------------------------------- P3 blocked
  P3_RC=0
  mkdir -p "$OUT/report-p3"
  chmod 777 "$OUT/report-p3"
  py_image "-v $REPO:/src:ro -v $OUT/raw:/raw:ro -v $OUT/report-p3:/o" -c '
import pathlib, shutil, subprocess, sys, tempfile
work = pathlib.Path(tempfile.mkdtemp())
for p in pathlib.Path("/raw").iterdir():
    if p.name != "capacity.main.json.gz":
        shutil.copy(p, work / p.name)
done = subprocess.run([sys.executable, "-I", "/src/scripts/eval/s42_aggregate.py", "--manifest", "/src/atlas/evals/shared/manifest.v1.json", "--raw", str(work), "--out", "/o"], capture_output=True, text=True)
print(done.stdout.strip() or done.stderr.strip())
import json
r = json.load(open("/o/readiness-report.json"))["report"]
print("p3_failed", ",".join(r["failed_checks"]))
' >"$OUT/p3.txt" 2>"$OUT/p3.stderr" || P3_RC=$?
  cat "$OUT/p3.txt" || true
  if grep -q ' blocked$' "$OUT/p3.txt" && grep -q 'capacity.limits_held' "$OUT/p3.txt"; then
    record P3-missing-record-blocks PASS "without capacity.main the verdict is blocked and names capacity.limits_held"
  else
    record P3-missing-record-blocks FAIL "removing a record did not block (rc $P3_RC)"
  fi
fi

# ----------------------------------------------------------------------------- the summary
SUMMARY_RC=0
next_name
run_c 60 --network none --user atlas --entrypoint $PY -v "$RESULTS:/results.tsv:ro" \
  -v "$OUT/facts.txt:/facts.txt:ro" -e S42_IMAGE="$IMAGE_ID" -e S42_NEG="$NEGATIVE" "$IMAGE" -I -c '
import json, os
rows = [line.rstrip("\n").split("\t") for line in open("/results.tsv")]
facts = {}
for line in open("/facts.txt"):
    key, _, value = line.rstrip("\n").partition(" ")
    facts[key] = value
print(json.dumps({"image": os.environ["S42_IMAGE"], "negative_control": os.environ["S42_NEG"] == "1", "facts": facts, "checks": [{"name": n, "status": s, "detail": d} for n, s, d in rows]}, indent=1, sort_keys=True))
' >"$OUT/$SUMMARY" || SUMMARY_RC=$?
echo "wrote $OUT/$SUMMARY"
if [ "$SUMMARY_RC" != "0" ]; then FAILS=$((FAILS + 1)); fi

if [ "$FAILS" = "0" ]; then
  if [ "$NEGATIVE" = "1" ]; then
    echo "RESULT: negative control blocked as intended"
  else
    echo "RESULT: all checks passed"
  fi
  exit 0
fi
echo "RESULT: $FAILS check(s) failed"
exit 1
