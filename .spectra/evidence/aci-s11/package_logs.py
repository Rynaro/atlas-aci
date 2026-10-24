"""Losslessly package only completed S11 raw logs after independent audit."""
import gzip
import hashlib
import io
import json
from pathlib import Path

root = Path(__file__).resolve().parents[3]
here = Path(__file__).resolve().parent
manifest = []
for path in sorted(p for p in here.rglob('*') if p.is_file() and p.suffix in {'.log','.txt'}):
    packed_path = path.with_name(path.name + '.gz')
    assert not packed_path.exists(), packed_path
    raw = path.read_bytes()
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, mtime=0) as stream:
        stream.write(raw)
    packed = buffer.getvalue()
    assert gzip.decompress(packed) == raw
    packed_path.write_bytes(packed)
    assert gzip.decompress(packed_path.read_bytes()) == raw
    manifest.append({'logical_path':str(path.relative_to(root)),
                     'compressed_path':str(packed_path.relative_to(root)),
                     'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),
                     'compressed_bytes':len(packed),'compressed_sha256':hashlib.sha256(packed).hexdigest(),
                     'gzip_mtime':0})
    path.unlink()
(here/'raw-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'streams':len(manifest),'roundtrip_verified':True}))
