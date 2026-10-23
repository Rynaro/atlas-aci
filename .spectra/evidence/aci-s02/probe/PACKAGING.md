# Raw probe evidence packaging

The four large raw graph/partition outputs are stored as deterministic gzip
(`mtime=0`, no embedded filename). `raw-packaging.json` maps each original
logical path to its compressed path, exact byte sizes, SHA-256 hashes and original
Git blob at implementation commit `d7aa74ca9b388f477dea44d04551dbb49f1700d6`.
Decompression was checked byte-for-byte against those committed blobs. The
measured bundle, sidecar, report and producer command records are unchanged.

To restore the original logical paths and the two temporary graph inputs needed
by the existing assembly helper, run from the checkout root:

```sh
python3 - <<'PY'
import gzip, hashlib, json
from pathlib import Path
manifest = json.loads(Path('.spectra/evidence/aci-s02/probe/raw-packaging.json').read_text())
for item in manifest['files']:
    raw = gzip.decompress(Path(item['compressed_path']).read_bytes())
    assert len(raw) == item['uncompressed_bytes']
    assert hashlib.sha256(raw).hexdigest() == item['uncompressed_sha256']
    paths = [Path(item['logical_path'])]
    if paths[0].name.endswith('-graph.json'):
        paths.append(Path('/tmp/aci-s02-probe-' + paths[0].name))
    for path in paths:
        if path.exists():
            assert path.read_bytes() == raw, f'Refusing to replace different bytes: {path}'
        else:
            path.write_bytes(raw)
PY
PATH=/tmp/aci-s01-tools/bin:$PATH UV_CACHE_DIR=/tmp/aci-s01-uv-cache UV_OFFLINE=1 \
  mcp-server/.venv/bin/python .spectra/evidence/aci-s02/probe/assemble_probe.py
```

The helper also consumes the preserved pin/status/job records. Its environment
must contain the locked project dependencies and `uv`. Restoring the four plain
logical paths creates untracked files; remove those restored copies after replay
to return to the packaged checkout. Original producer logs deliberately retain
the paths used during the actual measurement.
