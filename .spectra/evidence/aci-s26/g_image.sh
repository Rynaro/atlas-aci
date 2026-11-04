#!/bin/sh
# G-IMAGE / G-ROUTES / G-MODELS driver for ACI-S26 (run from the repository root).
#
#   .spectra/evidence/aci-s26/g_image.sh BASE_IMAGE NEW_IMAGE OUT_DIR
#
# BASE_IMAGE is built from 0194758 (atlas-aci:s26-base), NEW_IMAGE from the branch
# (docker build -t atlas-aci:s26 mcp-server). Every run is `atlas-aci index --langs ruby,python`
# with `--network none`, the fixture mounted read-only at /repo and its own writable graph root
# at /graph, so the repo path (and therefore the graph identity) is the same in every run.
# Compares run with the system python3 (stdlib only). Exit 0 only when every comparison and
# every rejection check passes.
#
#   S26 fixture:  base / new OFF / new ON (--frameworks rails_jobs)    -> image-compare.json
#   Python repo:  base / new OFF / new ON                               -> image-compare-python.json
#   S26 fixture:  rails_jobs alone vs rails,rails_jobs,rails_models     -> jobs-all-compare.json
#   merged repo:  rails_jobs alone vs all three, and each other adapter alone vs the all-three
#                 build (G-ROUTES / G-MODELS stay equal when jobs is also on)
#   G-ROUTES:     S24 fixture --frameworks rails, base vs new (+ rails vs rails,rails_jobs)
#   G-MODELS:     S25 fixture --frameworks rails_models --rails-table-map, base vs new
#   Rejections:   base rejects --frameworks rails_jobs (exit 2); new rejects django (exit 2) and
#                 a table map without rails_models (exit 2); none creates a graph directory.
set -u
BASE=$1; NEW=$2; OUT=$3
ROOT=$(pwd)
FX=$ROOT/mcp-server/tests/fixtures
CMP=${CMP:-"python3 -I $ROOT/.spectra/evidence/aci-s26/compare_graphs.py"}
mkdir -p "$OUT"
fail=0

run() { # image name fixture-dir [index flags...]
  image=$1; name=$2; fixture=$3; shift 3
  mkdir -p "$OUT/$name"; chmod 777 "$OUT/$name"
  docker run --rm --network none -v "$fixture:/repo:ro" -v "$FX/rails_models/table-map.json:/map.json:ro" \
    -v "$OUT/$name:/graph" "$image" index --repo /repo --graph-root /graph --langs ruby,python "$@" \
    > "$OUT/$name.log" 2>&1 || { echo "FAILED: $name ($(tail -1 "$OUT/$name.log"))"; fail=1; }
}
db() { ls "$OUT/$1"/repos/*/graph.*.db; }
compare() { # outfile mode-or-empty db...
  out=$1; shift
  $CMP "$@" > "$OUT/$out" || { echo "COMPARE FAILED: $out"; fail=1; }
}

J=$FX/rails_jobs/project; M=$FX/rails_models/project; R=$FX/rails_routes/project; P=$FX/coverage/project
MERGED=$OUT/merged-repo
rm -rf "$MERGED"; mkdir -p "$MERGED"
cp -R "$R/." "$MERGED/"; cp -R "$M/." "$MERGED/"; cp -R "$J/." "$MERGED/"
EVERY=rails,rails_jobs,rails_models

# G-IMAGE, S26 fixture: base vs new OFF vs new ON.
run "$BASE" s26-base "$J"
run "$NEW" s26-off "$J"
run "$NEW" s26-on "$J" --frameworks rails_jobs
# G-IMAGE, a Python repository (no Ruby jobs): ON must equal OFF fully.
run "$BASE" py-base "$P"
run "$NEW" py-off "$P"
run "$NEW" py-on "$P" --frameworks rails_jobs
# jobs alone vs all three adapters on the S26 fixture (the others add no records there).
run "$NEW" s26-all "$J" --frameworks $EVERY --rails-table-map /map.json
# Merged repository: each adapter alone, and all three together.
run "$NEW" merged-all "$MERGED" --frameworks $EVERY --rails-table-map /map.json
run "$NEW" merged-jobs "$MERGED" --frameworks rails_jobs
run "$NEW" merged-routes "$MERGED" --frameworks rails
run "$NEW" merged-models "$MERGED" --frameworks rails_models --rails-table-map /map.json
# G-ROUTES: the S24 fixture, --frameworks rails, base vs new; then rails vs rails+rails_jobs.
run "$BASE" routes-base "$R" --frameworks rails
run "$NEW" routes-new "$R" --frameworks rails
run "$NEW" routes-jobs "$R" --frameworks rails,rails_jobs
# G-MODELS: the S25 fixture, --frameworks rails_models --rails-table-map, base vs new.
run "$BASE" models-base "$M" --frameworks rails_models --rails-table-map /map.json
run "$NEW" models-new "$M" --frameworks rails_models --rails-table-map /map.json
if [ $fail -eq 0 ]; then
  compare image-compare.json "$(db s26-base)" "$(db s26-off)" "$(db s26-on)"
  compare image-compare-python.json "$(db py-base)" "$(db py-off)" "$(db py-on)"
  compare jobs-all-compare.json --jobs "$(db s26-on)" "$(db s26-all)"
  compare merged-jobs-compare.json --jobs "$(db merged-jobs)" "$(db merged-all)"
  compare routes-compare.json --routes "$(db routes-base)" "$(db routes-new)"
  compare routes-jobs-compare.json --with-jobs "$(db routes-new)" "$(db routes-jobs)"
  compare models-compare.json --models "$(db models-base)" "$(db models-new)"
fi
# Rejections (exit 2, nothing created).
docker run --rm --network none -v "$J:/repo:ro" -v "$OUT/rej0:/graph" "$BASE" index --repo /repo \
  --graph-root /graph/x --frameworks rails_jobs > "$OUT/rej-base-jobs.log" 2>&1
[ $? -eq 2 ] || { echo "the base image did not reject rails_jobs"; fail=1; }
docker run --rm --network none -v "$J:/repo:ro" -v "$OUT/rej1:/graph" "$NEW" index --repo /repo \
  --graph-root /graph/x --frameworks django > "$OUT/rej-django.log" 2>&1
[ $? -eq 2 ] || { echo "django not rejected"; fail=1; }
docker run --rm --network none -v "$J:/repo:ro" -v "$FX/rails_models/table-map.json:/map.json:ro" \
  -v "$OUT/rej2:/graph" "$NEW" index --repo /repo --graph-root /graph/x --frameworks rails_jobs \
  --rails-table-map /map.json > "$OUT/rej-map.log" 2>&1
[ $? -eq 2 ] || { echo "map without rails_models not rejected"; fail=1; }
for d in rej0 rej1 rej2; do
  [ -e "$OUT/$d/x" ] && { echo "a rejected run created a graph directory ($d)"; fail=1; }
done
echo "G-IMAGE exit $fail"
exit $fail
