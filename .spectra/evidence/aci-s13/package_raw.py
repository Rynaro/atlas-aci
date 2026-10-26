"""Task-local lossless raw-log packaging; logical paths remain in the manifest."""
import gzip
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[3]
here=Path(__file__).resolve().parent
records=[]
for path in sorted(here.rglob('*')):
 if not path.is_file() or not (path.suffix in {'.log','.diff'} or path.name.endswith('full-log.txt')):
  continue
 raw=path.read_bytes();packed=gzip.compress(raw,mtime=0);target=path.with_name(path.name+'.gz')
 assert not target.exists(),target
 target.write_bytes(packed)
 assert gzip.decompress(target.read_bytes())==raw
 records.append({'logical_path':str(path.relative_to(root)),'compressed_path':str(target.relative_to(root)),
  'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),
  'compressed_bytes':len(packed),'compressed_sha256':hashlib.sha256(packed).hexdigest(),'gzip_mtime':0})
 path.unlink()
(here/'raw-manifest.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps({'streams':len(records),'exact_roundtrip':True}))
