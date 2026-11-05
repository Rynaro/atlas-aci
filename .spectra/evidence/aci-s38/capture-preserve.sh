#!/bin/sh
# ACI-S38 G-PRESERVE capture. Usage: capture-preserve.sh base|after SCRATCH [SEED]
#   base : a separate `git worktree` of the base commit under SCRATCH, mounted read-only; its own
#          modules (PYTHONPATH=/t/mcp-server/src) serve every conversation; the identity header
#          (atlas_aci.__file__, git HEAD, clean-tree flag, sha256 of server.py and results.py,
#          compared with `git show <base>:<path>`) is verified, and any mismatch is a STOP (exit 3).
#   after: this checkout, mounted read-only.
# The driver is the branch's tests/slices/test_s38_support.py (stdlib + lazy atlas_aci imports).
# Output: preserve/<label>/{frames,inprocess,ac004,case-sha256,identity}.json  plus
#         SCRATCH/transcripts-<label>/ (normalised transcripts, compared with the goldens).
set -eu
LABEL=$1
SCRATCH=$2
SEED=${3:-0}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
BASE=2db6dd1a3cba6589bf4efc9ad83620e39bacb289
DEV=${DEV_IMAGE:-atlas-aci-dev-s16}
# A persistent HOME for the dev container: the first `atlas-aci index` fetches tree-sitter grammars
# into $HOME/.cache; a fresh HOME makes every run download them again.
DEV_HOME=${DEV_HOME:-$SCRATCH/home}
mkdir -p "$DEV_HOME"
chmod 777 "$DEV_HOME"
mkdir -p "$SCRATCH"
case $LABEL in
base)
    TREE=$SCRATCH/base-wt
    [ -d "$TREE" ] || git -C "$REPO" worktree add --detach "$TREE" "$BASE" > /dev/null
    HEAD=$(git -C "$TREE" rev-parse HEAD)
    DIRTY=$(git -C "$TREE" status --porcelain | wc -l | tr -d ' ')
    [ "$HEAD" = "$BASE" ] && [ "$DIRTY" = 0 ] || { echo "STOP: base tree is $HEAD with $DIRTY changes"; exit 3; } ;;
after)
    TREE=$REPO
    HEAD=$(git -C "$REPO" rev-parse HEAD)
    DIRTY=$(git -C "$REPO" status --porcelain -- mcp-server/src | wc -l | tr -d ' ') ;;
*) echo "usage: capture-preserve.sh base|after SCRATCH [SEED]"; exit 2 ;;
esac
OUT=$SCRATCH/out-$LABEL
FX=$SCRATCH/fx-$LABEL
rm -rf "$OUT" "$FX"
mkdir -p "$OUT" "$FX"
chmod 777 "$OUT" "$FX"
NAME=s38-capture-$LABEL-$$
trap 'docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT INT TERM
docker run --rm --name "$NAME" --user 1000:1000 -e HOME=/hc -e PYTHONPATH=/t/mcp-server/src \
    -v "$DEV_HOME":/hc -v "$TREE":/t:ro -v "$REPO/mcp-server/tests/slices":/drv:ro -v "$OUT":/out -v "$FX":/fx \
    -w /t/mcp-server "$DEV" \
    /opt/venv/bin/python /drv/test_s38_support.py capture --out /out --fixture /fx --label "$LABEL" --seed "$SEED" \
    > "$SCRATCH/capture-$LABEL.stdout" 2> "$SCRATCH/capture-$LABEL.stderr"
sha() { if command -v sha256sum > /dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
SRV=mcp-server/src/atlas_aci/server.py
RES=mcp-server/src/atlas_aci/protocol/results.py
if [ "$LABEL" = base ]; then
    for f in $SRV $RES; do
        want=$(git -C "$REPO" show "$BASE:$f" | { if command -v sha256sum > /dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | cut -d' ' -f1)
        have=$(sha "$TREE/$f")
        [ "$want" = "$have" ] || { echo "STOP: $f differs from git show $BASE:$f"; exit 3; }
        grep -q "\"$(basename "$f" .py)_py_sha256\": \"$want\"" "$OUT/frames.json" || { echo "STOP: in-container $f hash is not the base hash"; exit 3; }
    done
    grep -q '"atlas_aci_file": "/t/mcp-server/src/atlas_aci/__init__.py"' "$OUT/frames.json" || { echo "STOP: atlas_aci not imported from the base tree"; exit 3; }
fi
DEST=$HERE/preserve/$LABEL
rm -rf "$DEST"
mkdir -p "$DEST"
cp "$OUT/frames.json" "$OUT/inprocess.json" "$OUT/ac004.json" "$OUT/case-sha256.json" "$DEST/"
rm -rf "$SCRATCH/transcripts-$LABEL"
cp -R "$OUT/transcripts" "$SCRATCH/transcripts-$LABEL"
cat > "$DEST/identity.json" <<JSON
{
 "label": "$LABEL",
 "tree": "$( [ "$LABEL" = base ] && echo "git worktree of $BASE (mounted read-only at /t)" || echo "this checkout (mounted read-only at /t)")",
 "git_head": "$HEAD",
 "dirty_files_under_src_or_tree": $DIRTY,
 "atlas_aci_file_in_container": "/t/mcp-server/src/atlas_aci/__init__.py",
 "host_server_py_sha256": "$(sha "$TREE/$SRV")",
 "host_results_py_sha256": "$(sha "$TREE/$RES")",
 "git_show_base_server_py_sha256": "$(git -C "$REPO" show "$BASE:$SRV" | { if command -v sha256sum > /dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | cut -d' ' -f1)",
 "git_show_base_results_py_sha256": "$(git -C "$REPO" show "$BASE:$RES" | { if command -v sha256sum > /dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | cut -d' ' -f1)",
 "pythonhashseed": $SEED,
 "dev_image": "$DEV",
 "captured_at_utc": "$(date -u +%FT%TZ)"
}
JSON
echo "capture $LABEL ok: $DEST"
