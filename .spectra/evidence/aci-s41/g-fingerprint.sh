#!/bin/sh
# ACI-S41 G-FINGERPRINT. A fixture repository at one fixed path is built by the BASE commit's code
# (a git archive of fa16b1c, read-only) and by this tree's code, in two separate dev containers.
# The canonical producer-input bytes, the generation_id and the snapshot_id must be equal. Positive
# control: appending one byte to a hashed module (storage/evidence.py) of a scratch copy of the base
# changes the producer inputs and the ids. The S38 goldens are checked by tests/slices/test_s38_*.py
# in the regression gate. Writes g-fingerprint.log.gz, g-fingerprint.exit and g-fingerprint.json.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
BASE=fa16b1c64f6ad4cb7cfde9957e8536fbaeeff318
WORK=$(mktemp -d "${TMPDIR:-/tmp}/s41-fp.XXXXXX")
trap 'rm -rf "$WORK"' EXIT INT TERM
mkdir -p "$WORK/base" "$WORK/perturbed" "$WORK/home" "$WORK/out"
chmod 777 "$WORK/out"
(cd "$REPO" && git archive "$BASE" mcp-server/src) | tar -x -C "$WORK/base"
cp -R "$WORK/base/mcp-server" "$WORK/perturbed/"
printf '\n# perturbed\n' >> "$WORK/perturbed/mcp-server/src/atlas_aci/storage/evidence.py"
cat > "$WORK/fp.py" <<'PY'
import hashlib, json, sys
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
import atlas_aci
repo = Path("/fx/repo"); repo.mkdir(parents=True, exist_ok=True)
for i in range(6):
    (repo / f"m{i}.py").write_text(f"def f{i}(x):\n    return x + {i}\n\nclass C{i}:\n    def m(self):\n        return f{i}(1)\n")
(repo / "a.rb").write_text("class A\n  def go\n    B.new.run\n  end\nend\n")
(repo / "b.js").write_text("export function run() { return helper(); }\nfunction helper() { return 1; }\n")
graph = CodeGraph(repo, read_only=True, graph_root=Path("/fx/g"))
producer = graph._producer_inputs()
writer = CodeGraph(repo, graph_root=Path("/fx/g"))
stats = writer.build()
with writer.reader_lease() as reader:
    gid, sid = reader._lease_generation_id, reader.snapshot_id
print(json.dumps({"src": str(Path(atlas_aci.__file__).parent), "producer_sha256": hashlib.sha256(json.dumps(producer, sort_keys=True).encode()).hexdigest(), "generation_id": gid, "snapshot_id": sid, "files_indexed": stats["files_indexed"]}, sort_keys=True))
PY
run() {  # run LABEL SRC_DIR
    docker run --rm --user 1000:1000 -e HOME=/hc -e PYTHONPATH="$2" -v "$WORK/home":/hc -v "$WORK/fp.py":/fp.py:ro \
        -v "$WORK/base":/base:ro -v "$WORK/perturbed":/perturbed:ro -v "$REPO":/ws:ro \
        --tmpfs /fx:uid=1000,gid=1000 atlas-aci-dev-relock /opt/venv/bin/python /fp.py > "$WORK/out/$1.json" 2> "$WORK/out/$1.err"
}
{
    # the grammar cache must exist once (read-write HOME), outside the compared runs
    docker run --rm --user 1000:1000 -e HOME=/hc -v "$WORK/home":/hc atlas-aci-dev-relock \
        /opt/venv/bin/python -c "from tree_sitter_language_pack import get_parser; [get_parser(l) for l in ('python','ruby','javascript')]" > /dev/null 2>&1 || true
    run base /base/mcp-server/src
    run branch /ws/mcp-server/src
    run perturbed /perturbed/mcp-server/src
    for n in base branch perturbed; do echo "$n: $(cat "$WORK/out/$n.json") $(head -c 200 "$WORK/out/$n.err")"; done
    field() { sed -n 's/.*"'"$2"'": *"\([0-9a-f]*\)".*/\1/p' "$WORK/out/$1.json"; }
    CODE=0
    for k in producer_sha256 generation_id snapshot_id; do
        a=$(field base $k); b=$(field branch $k); c=$(field perturbed $k)
        if [ -n "$a" ] && [ "$a" = "$b" ]; then echo "PASS $k equal at base and branch ($a)"; else echo "FAIL $k base '$a' branch '$b'"; CODE=1; fi
        if [ -n "$c" ] && [ "$c" != "$a" ]; then echo "PASS positive control: the perturbed hashed module changes $k"; else echo "FAIL positive control $k"; CODE=1; fi
    done
    echo "== g-fingerprint exit $CODE"
    echo "$CODE" > "$HERE/g-fingerprint.exit"
    cp "$WORK/out/base.json" "$HERE/g-fingerprint.base.json" 2> /dev/null || true
    cp "$WORK/out/branch.json" "$HERE/g-fingerprint.branch.json" 2> /dev/null || true
} 2>&1 | gzip -9n > "$HERE/g-fingerprint.log.gz"
echo "g-fingerprint exit $(cat "$HERE/g-fingerprint.exit")"
