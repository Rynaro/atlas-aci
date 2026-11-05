#!/bin/sh
# ACI-S38 G-HARDEN: scripts/harden-gate-classify.sh BASE HEAD prints augmented=false and
# a3_touched=false. The project history is not touched: the changed files (tracked changes plus
# new untracked files, outside .spectra) are copied into a throwaway LOCAL CLONE under SCRATCH and
# committed THERE, and the classifier runs inside that clone.
# usage: g-harden.sh SCRATCH   -> g-harden.log.gz / g-harden.exit next to this script
set -u
SCRATCH=$1
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
BASE=2db6dd1a3cba6589bf4efc9ad83620e39bacb289
CL=$SCRATCH/harden-clone
{
    rm -rf "$CL"
    git clone -q --no-hardlinks "$REPO" "$CL" && git -C "$CL" checkout -q --detach "$BASE" || { echo "clone failed"; echo 1 > "$HERE/g-harden.exit"; exit 0; }
    { git -C "$REPO" diff --name-only "$BASE"; git -C "$REPO" ls-files --others --exclude-standard; } | grep -v -e '^\.spectra/' -e __pycache__ | sort -u | while read -r f; do
        mkdir -p "$CL/$(dirname "$f")"
        cp "$REPO/$f" "$CL/$f"
    done
    git -C "$CL" add -A
    git -C "$CL" -c user.name=s38 -c user.email=s38@example.invalid commit -q -m "s38 working tree (throwaway clone)"
    echo "changed files in the clone:"
    git -C "$CL" diff --name-only "$BASE" HEAD
    OUT=$(cd "$CL" && bash scripts/harden-gate-classify.sh "$BASE" HEAD)
    echo "$OUT"
    if [ "$OUT" = "augmented=false
a3_touched=false" ]; then echo 0 > "$HERE/g-harden.exit"; echo "g-harden MATCH"; else echo 1 > "$HERE/g-harden.exit"; echo "g-harden MISMATCH"; fi
} 2>&1 | gzip -9n > "$HERE/g-harden.log.gz"
echo "g-harden exit $(cat "$HERE/g-harden.exit")"
rm -rf "$CL"
