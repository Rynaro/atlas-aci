"""Lossless deterministic packaging of completed raw machine evidence only."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'packaging-manifest.json'
entries = json.loads(MANIFEST.read_text())['streams'] if MANIFEST.exists() else []
known = {entry['logical_path'] for entry in entries}
for path in sorted(HERE.rglob('*')):
    if not path.is_file() or not (path.suffix == '.log' or path.name in {'tree.txt', 'manifest.headers.txt'} or (path.suffix == '.json' and path.stat().st_size > 131072)):
        continue
    if path.name == 'output-check-tests.log':
        continue
    logical = str(path.relative_to(ROOT))
    assert logical not in known, logical
    raw = path.read_bytes()
    packed = gzip.compress(raw, mtime=0)
    target = path.with_name(path.name + '.gz')
    assert not target.exists(), target
    target.write_bytes(packed)
    assert gzip.decompress(target.read_bytes()) == raw
    entries.append({'logical_path':logical,'compressed_path':str(target.relative_to(ROOT)),
                    'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),
                    'compressed_bytes':len(packed),'compressed_sha256':hashlib.sha256(packed).hexdigest(),
                    'gzip_mtime':0})
    path.unlink()
MANIFEST.write_text(json.dumps({'format':'lossless-raw-evidence.v1','streams':entries},indent=2)+'\n')
summary={'streams':len(entries),'all_roundtrips_exact':True}
(HERE/'packaging-check.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary))
