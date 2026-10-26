"""Losslessly package closed raw evidence; never normalize failure/whitespace bytes."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

here = Path(__file__).resolve().parent
root = here.parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--include-probe', action='store_true')
args = parser.parse_args()
manifest = here / 'raw-manifest.json'
records = json.loads(manifest.read_text()) if manifest.exists() else []
by_name = {row['logical_path']: row for row in records}
excluded = {'output-check-tests.log'}
for path in sorted(here.rglob('*')):
    if not path.is_file() or path.name in excluded or path.suffix not in {'.log', '.txt', '.diff'}:
        continue
    if path.name == 'docker-image-id.txt':
        continue
    if not args.include_probe and 'probe' in path.relative_to(here).parts:
        continue
    raw = path.read_bytes()
    packed = gzip.compress(raw, mtime=0)
    target = path.with_name(path.name + '.gz')
    target.write_bytes(packed)
    if gzip.decompress(target.read_bytes()) != raw:
        raise RuntimeError('Lossless packaging failed: ' + str(path))
    name = str(path.relative_to(root))
    row = {'logical_path': name, 'producer_output_path': name, 'compressed_path': str(target.relative_to(root)), 'uncompressed_bytes': len(raw), 'uncompressed_sha256': hashlib.sha256(raw).hexdigest(), 'compressed_bytes': len(packed), 'compressed_sha256': hashlib.sha256(packed).hexdigest(), 'gzip_mtime': 0}
    if name in by_name and row != by_name[name]:
        raise RuntimeError('Previously packaged logical evidence changed: ' + name)
    by_name[name] = row
    path.unlink()
manifest.write_text(json.dumps([by_name[k] for k in sorted(by_name)], indent=2) + '\n')
print(json.dumps({'streams': len(by_name), 'exact_roundtrip': True}))
