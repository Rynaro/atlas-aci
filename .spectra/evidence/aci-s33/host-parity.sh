#!/bin/sh
# Day-1 host-git leg: usage host-parity.sh WORLD_DIR DEV_QUALIFY_JSON
# WORLD_DIR is the main world built in a container and copied out with tar (so the host owns it).
# Re-creates every commit with the host git (ids must match) and compares the D3 outputs.
cd "$1" || exit 99
DEV=$2
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 LC_ALL=C
export GIT_AUTHOR_NAME="Atlas Fixture" GIT_AUTHOR_EMAIL=fixture@example.invalid
export GIT_COMMITTER_NAME="Atlas Fixture" GIT_COMMITTER_EMAIL=fixture@example.invalid
echo "host: $(git --version)"
ok=0; bad=0
for c in $(git rev-list --all); do
    tree=$(git rev-parse "$c^{tree}"); at=$(git log -1 --format=%at "$c"); msg=$(git log -1 --format=%s "$c")
    set --
    for p in $(git rev-list --parents -n1 "$c" | awk '{for(i=2;i<=NF;i++)print $i}'); do set -- "$@" -p "$p"; done
    new=$(GIT_AUTHOR_DATE="@$at +0000" GIT_COMMITTER_DATE="@$at +0000" git commit-tree "$tree" "$@" -m "$msg")
    if [ "$new" = "$c" ]; then ok=$((ok+1)); else bad=$((bad+1)); fi
done
echo "host commit-tree reproduces the commit ids: ok=$ok bad=$bad"
export GIT_OPTIONAL_LOCKS=0 GIT_NO_REPLACE_OBJECTS=1 GIT_NO_LAZY_FETCH=1 GIT_ALLOW_PROTOCOL=none GIT_GRAFT_FILE=/dev/null GIT_PAGER=cat
g() { git --no-optional-locks -c core.fsmonitor=false -c core.quotePath=false -c diff.external= -c diff.renames=false "$@"; }
h() { "$@" 2>/dev/null | shasum -a 256 | cut -d' ' -f1; }
END=$(git rev-parse HEAD); START=$(git rev-list --max-parents=0 HEAD)
echo "rev_list      $(h g rev-list --parents --max-count=15 --end-of-options "$END" "^$START")  dev=$(jq -r .calls.rev_list.sha256 "$DEV")"
echo "ls_tree       $(h g ls-tree -r -z --full-tree --end-of-options "$END")  dev=$(jq -r .calls.ls_tree.sha256 "$DEV")"
echo "rev_list_max1 $(h g rev-list --parents --max-count=2 --end-of-options "$END" "^$START")  dev=$(jq -r .calls.rev_list_max1.sha256 "$DEV")"
g cat-file -e --end-of-options "1111111111111111111111111111111111111111^{commit}" 2>/dev/null; echo "exists_missing_rc=$? (dev $(jq .calls.exists_missing.rc "$DEV"))"
g merge-base --is-ancestor --end-of-options "$END" "$START" 2>/dev/null; echo "ancestor_no_rc=$? (dev $(jq .calls.ancestor_no.rc "$DEV"))"
g merge-base --is-ancestor --end-of-options 1111111111111111111111111111111111111111 "$END" 2>/dev/null; echo "ancestor_missing_rc=$? (dev $(jq .calls.ancestor_missing.rc "$DEV"))"
g config --local --get-regexp '^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$' >/dev/null 2>&1; echo "promisor_rc=$? (dev $(jq .calls.promisor_config.rc "$DEV"))"
echo "probe: $(g rev-parse --show-toplevel --absolute-git-dir --git-common-dir --is-shallow-repository --is-bare-repository --show-object-format 2>&1 | tr '\n' ' ')"
echo "diff_tree sha256 per commit (host, label order of dev JSON):"
for c in $(git rev-list --all); do
    p=$(git rev-list --parents -n1 "$c" | awk '{print $2}'); [ -z "$p" ] && p=4b825dc642cb6eb9a060e54bf8d69288fbee4904
    echo "$c $(h g diff-tree -r -z --raw --no-abbrev --no-renames --no-ext-diff --no-textconv --ignore-submodules=none --end-of-options "$p" "$c")"
done | sort > /tmp/host-diff.$$
jq -r '.commit_ids as $ids | .calls.diff_tree | to_entries[] | "\($ids[.key]) \(.value.sha256)"' "$DEV" | sort > /tmp/dev-diff.$$
cmp -s /tmp/host-diff.$$ /tmp/dev-diff.$$ && echo "diff_tree outputs: identical for all commits" || echo "diff_tree outputs: DIFFER"
rm -f /tmp/host-diff.$$ /tmp/dev-diff.$$
