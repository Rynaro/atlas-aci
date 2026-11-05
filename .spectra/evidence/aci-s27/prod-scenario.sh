#!/bin/bash
# ACI-S27 production-image scenario (orchestrator). Usage: prod-scenario.sh [IMAGE]
# IMAGE defaults to atlas-aci:s27 (built from mcp-server/Dockerfile on branch aci-s27).
# Dev-image dry run: IMAGE=atlas-aci-dev-s16 AT="/opt/venv/bin/python -m atlas_aci" \
#   EXTRA="-e PYTHONPATH=/ws/mcp-server/src -v <repo>:/ws:ro" prod-scenario.sh
# Needs docker, git, shasum, wc, sed. No python on the host.
set -u
IMAGE=${1:-${IMAGE:-atlas-aci:s27}}
AT=${AT:-atlas-aci}
EXTRA=${EXTRA:-}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
FX=$REPO/mcp-server/tests/fixtures/otel
G=${G:-$(mktemp -d)}; rm -rf "$G"; mkdir -p "$G/src/app" "$G/src/web" "$G/graphs" "$G/in"
fail=0; ok() { echo "OK   $*"; }; bad() { echo "FAIL $*"; fail=1; }
cd "$G/src"
touch app/__init__.py
printf 'class Service:\n    def handle(self, x):\n        return helper(x)\n\n\ndef helper(x):\n    return x\n' > app/service.py
printf 'def (:\n' > app/broken.py
printf 'function f() { return 1 }\n' > web/page.js
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -qm fixture
chmod -R a+rwX "$G"
cp "$FX/trace-basic.json" "$G/in/trace.json"
inv() { ( cd "$G" && find src graphs -type f | sort | xargs shasum -a 256 ); }
RUND() { docker run --rm --user 1000:1000 --network none $EXTRA "$@"; }
# 3. index (snapshot id from the index_done line)
RUND -v "$G/src:/src" -v "$G/graphs:/graphs" $( [ "$AT" = atlas-aci ] || echo "--entrypoint /opt/venv/bin/python" ) \
  "$IMAGE" $( [ "$AT" = atlas-aci ] || echo "-m atlas_aci" ) index --repo /src --graph-root /graphs --langs python \
  2> "$G/index.err" >/dev/null; echo "index exit=$?"
SNAP=$(grep -oE 'snapshot_id[^0-9a-f]*[0-9a-f]{64}' "$G/index.err" | head -1 | grep -oE '[0-9a-f]{64}')
[ -n "$SNAP" ] && ok "snapshot id from index_done: $SNAP" || { bad "no snapshot id"; exit 1; }
# 4. manifests: matched, mismatched (fixed id), digest-mismatch
SHA=$(shasum -a 256 "$G/in/trace.json" | cut -d' ' -f1); BYTES=$(wc -c < "$G/in/trace.json" | tr -d ' ')
mk() { printf '{"format":"atlas-otel-provenance.v1","profile":"atlas-otel-json-profile.v1","artifact":{"sha256":"%s","bytes":%s},"run":{"id":"run-prod-001"},"producer":{"name":"otel-sdk-fixture","version":"1.0.0"},"build":{"id":"build-abc123","snapshot_id":"%s"},"source_roots":[{"from":"/srv/app","to":""},{"from":"","to":""}]}\n' "$1" "$2" "$3"; }
mk "$SHA" "$BYTES" "$SNAP" > "$G/in/matched.json"
mk "$SHA" "$BYTES" "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" > "$G/in/mismatch.json"
mk "$(printf '0%.0s' $(seq 64))" "$BYTES" "$SNAP" > "$G/in/digest.json"
chmod -R a+rwX "$G/in"
inv > "$G/pre.inv"
ENTRY=(); [ "$AT" = atlas-aci ] || ENTRY=(--entrypoint /opt/venv/bin/python)
CMD=(); [ "$AT" = atlas-aci ] || CMD=(-m atlas_aci)
MOUNTS=(-v "$G/src:/src:ro" -v "$G/graphs:/graphs" -v "$G/in:/in:ro" -v "$FX:/fixtures:ro")
# 5. two imports into a tmpfs /out, byte-identical files and stdout
docker run --rm --user 1000:1000 --network none --read-only --tmpfs /out:uid=1000,gid=1000 $EXTRA "${MOUNTS[@]}" \
  --entrypoint sh "$IMAGE" -c "
    AT='$AT'
    \$AT import-otel --repo /src --graph-root /graphs --manifest /in/matched.json --output /out/a.json /in/trace.json > /out/a.out 2>/out/a.err; echo a=\$?
    \$AT import-otel --repo /src --graph-root /graphs --manifest /in/matched.json --output /out/b.json /in/trace.json > /out/b.out 2>/out/b.err; echo b=\$?
    cmp /out/a.json /out/b.json && echo OVERLAY-IDENTICAL
    cmp /out/a.out /out/b.out && echo STDOUT-IDENTICAL
    sha256sum /out/a.json; wc -c /out/a.json
    cat /out/a.out
    \$AT import-otel --repo /src --graph-root /graphs --manifest /in/mismatch.json --output /out/m.json /in/trace.json 2>&1 | head -c 2000 | sed 's/^/mismatch-stdout: /'
    # 7. digest mismatch: exit 1, fixed text, empty stdout
    \$AT import-otel --repo /src --graph-root /graphs --manifest /in/digest.json --output /out/d.json /in/trace.json > /out/d.out 2>/out/d.err; echo digest-exit=\$?; cat /out/d.err; echo digest-stdout-bytes=\$(wc -c < /out/d.out)
    # 8. output inside --repo
    \$AT import-otel --repo /src --graph-root /graphs --manifest /in/matched.json --output /src/x.json /in/trace.json > /out/r.out 2>/out/r.err; echo inside-repo-exit=\$?; tail -1 /out/r.err
  " 2>&1 | tee "$G/scenario.out"
# 6. graph inventory unchanged
inv | diff -q - "$G/pre.inv" > /dev/null && ok "graphs and source inventory unchanged" || bad "inventory changed"
# 9. informational: graphs mounted read-only
docker run --rm --user 1000:1000 --network none --read-only --tmpfs /out:uid=1000,gid=1000 $EXTRA \
  -v "$G/src:/src:ro" -v "$G/graphs:/graphs:ro" -v "$G/in:/in:ro" --entrypoint sh "$IMAGE" -c \
  "$AT import-otel --repo /src --graph-root /graphs --manifest /in/matched.json --output /out/ro.json /in/trace.json >/dev/null 2>/out/ro.err; echo informational-readonly-graphs-exit=\$?; head -c 300 /out/ro.err" 2>&1 | tee -a "$G/scenario.out"
# 10. tools JSON pin
docker run --rm --user 1000:1000 --network none $EXTRA -v "$G/src:/src:ro" "${ENTRY[@]}" "$IMAGE" "${CMD[@]}" tools --repo /src 2>/dev/null > "$G/tools.json"
T=$(shasum -a 256 "$G/tools.json" | cut -d' ' -f1); B=$(wc -c < "$G/tools.json" | tr -d ' ')
[ "$T" = 94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56 ] && [ "$B" = 1631472 ] && ok "tools JSON pin" || bad "tools JSON $T $B"
echo "scratch=$G fail=$fail"; exit $fail
