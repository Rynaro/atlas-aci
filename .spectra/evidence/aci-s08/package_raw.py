"""Lossless deterministic packaging of completed task-owned raw log streams."""
import gzip,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
existing=json.loads((HERE/'footprint-baseline/raw-log-manifest.json').read_text())['files'];known={row['logical_path']:row for row in existing};rows=[];plain=[]
for path in sorted(HERE.rglob('*.log')):
 if path.name=='output-check-tests.log':continue
 assert not path.is_symlink();logical=str(path.relative_to(ROOT));raw=path.read_bytes()
 if logical in known:
  target=ROOT/known[logical]['compressed_path'];compressed=target.read_bytes();assert hashlib.sha256(raw).hexdigest()==known[logical]['uncompressed_sha256']
 else:
  target=HERE/'raw-logs'/(str(path.relative_to(HERE))+'.gz');target.parent.mkdir(parents=True,exist_ok=True);compressed=gzip.compress(raw,mtime=0);target.write_bytes(compressed)
 assert gzip.decompress(compressed)==raw and compressed[4:8]==b'\0'*4
 rows.append({'logical_path':logical,'compressed_path':str(target.relative_to(ROOT)),'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_bytes':len(compressed),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),'gzip_mtime':0});plain.append(path)
manifest={'reason':'Completed original/refinement raw logs preserved exactly; latest authoritative logical-to-compressed map. Earlier baseline manifests describe the earlier copy-retained checkpoint. No product, measured fact or frozen artifact rewritten.','files':rows,'plain_copies_removed_after_exact_roundtrip':True}
(HERE/'raw-log-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
for path in plain:path.unlink()
print(json.dumps({'packaged_streams':len(rows),'raw_bytes':sum(row['uncompressed_bytes'] for row in rows),'compressed_bytes':sum(row['compressed_bytes'] for row in rows),'all_roundtrip_exact':True}))
