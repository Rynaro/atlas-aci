#!/bin/sh
# G-IMAGE / G-ROUTES driver for ACI-S25 (run from the repository root).
#
#   .spectra/evidence/aci-s25/g_image.sh BASE_IMAGE NEW_IMAGE OUT_DIR
#
# BASE_IMAGE is built from f409094 (atlas-aci:s25-base), NEW_IMAGE from the branch. Both run
# `atlas-aci index --langs ruby,python` with the network allowed (parser download); the repos are
# mounted read-only at /repo and each run has its own writable graph root at /graph, so the repo
# path (and therefore the graph identity) is the same in every run. Compares are run with the
# system python3 (stdlib only). Exit 0 only when every comparison and rejection check passes.
set -u
BASE=$1; NEW=$2; OUT=$3
ROOT=$(pwd)
FX=$ROOT/mcp-server/tests/fixtures
CMP="python3 -I $ROOT/.spectra/evidence/aci-s25/compare_graphs.py"
mkdir -p "$OUT"
fail=0

run() { # image name fixture-dir [index flags...]
  image=$1; name=$2; fixture=$3; shift 3
  mkdir -p "$OUT/$name"; chmod 777 "$OUT/$name"
  docker run --rm -v "$fixture:/repo:ro" -v "$FX/rails_models/table-map.json:/map.json:ro" \
    -v "$OUT/$name:/graph" "$image" index --repo /repo --graph-root /graph --langs ruby,python "$@" \
    > "$OUT/$name.log" 2>&1 || { echo "FAILED: $name ($(tail -1 "$OUT/$name.log"))"; fail=1; }
}
db() { ls "$OUT/$1"/repos/*/graph.*.db; }
compare() { # outfile mode-or-empty db...
  out=$1; shift
  $CMP "$@" > "$OUT/$out" || { echo "COMPARE FAILED: $out"; fail=1; }
}

M=$FX/rails_models/project; R=$FX/rails_routes/project; P=$FX/coverage/project
# G-IMAGE, S25 fixture: base vs new OFF vs new ON.
run "$BASE" s25-base "$M"
run "$NEW" s25-off "$M"
run "$NEW" s25-on "$M" --frameworks rails_models --rails-table-map /map.json
# G-IMAGE, a Python repository (no models): ON must equal OFF fully.
run "$BASE" py-base "$P"
run "$NEW" py-off "$P"
run "$NEW" py-on "$P" --frameworks rails_models
# G-ROUTES: the S24 fixture, --frameworks rails, base vs new; then rails vs rails+rails_models.
run "$BASE" routes-base "$R" --frameworks rails
run "$NEW" routes-new "$R" --frameworks rails
run "$NEW" routes-both "$R" --frameworks rails,rails_models
if [ $fail -eq 0 ]; then
  compare image-compare.json "$(db s25-base)" "$(db s25-off)" "$(db s25-on)"
  compare image-compare-python.json "$(db py-base)" "$(db py-off)" "$(db py-on)"
  compare routes-compare.json --routes "$(db routes-base)" "$(db routes-new)"
  compare routes-both-compare.json --both "$(db routes-new)" "$(db routes-both)"
fi
# Rejections (exit 2, nothing created).
docker run --rm -v "$M:/repo:ro" -v "$OUT/rej1:/graph" "$NEW" index --repo /repo --graph-root /graph/x \
  --frameworks django > "$OUT/rej-django.log" 2>&1; [ $? -eq 2 ] || { echo "django not rejected"; fail=1; }
docker run --rm -v "$M:/repo:ro" -v "$FX/rails_models/table-map.json:/map.json:ro" -v "$OUT/rej2:/graph" \
  "$NEW" index --repo /repo --graph-root /graph/x --rails-table-map /map.json \
  > "$OUT/rej-map.log" 2>&1; [ $? -eq 2 ] || { echo "map without adapter not rejected"; fail=1; }
[ -e "$OUT/rej1/x" ] || [ -e "$OUT/rej2/x" ] && { echo "a rejected run created a graph directory"; fail=1; }
echo "G-IMAGE exit $fail"
exit $fail
