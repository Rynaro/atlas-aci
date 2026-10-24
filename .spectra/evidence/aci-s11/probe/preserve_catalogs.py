"""Preserve exact closed source catalogs before any authorized scratch cleanup."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

here = Path(__file__).resolve().parent
root = here.parents[3]
started = time.monotonic()
records, candidates = [], []
for name in ('solidus', 'spree'):
    repo = Path('/tmp/aci-s11-probe-inputs') / name
    atlas = repo / '.atlas'
    assert stat.S_ISDIR(atlas.lstat().st_mode) and not atlas.is_symlink()
    assert atlas.resolve().parent == repo.resolve() and (repo/'.git').is_dir()
    account = json.loads((here/(name+'-accounting.json')).read_text())
    store = atlas/'source-snapshots'
    manifests = list(store.glob('manifest.*.json'))
    assert len(manifests) == 1
    for kind, path in [('manifest',manifests[0]), ('catalog',store/'catalog.json')]:
        raw = path.read_bytes()
        compressed = gzip.compress(raw, mtime=0)
        target = here/(name+'-source-'+kind+'.json.gz')
        assert not target.exists()
        target.write_bytes(compressed)
        assert gzip.decompress(target.read_bytes()) == raw
        records.append({'repo':name,'kind':kind,'original_path':str(path),
                        'compressed_path':str(target.relative_to(root)),
                        'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),
                        'compressed_bytes':len(compressed),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),
                        'gzip_mtime':0,'snapshot_id':account['source_snapshot']['snapshot_id'],
                        'pack_sha256':account['source_snapshot']['pack_sha256'],
                        'pack_bytes':account['source_snapshot']['pack_bytes'],
                        'source_closure_evidence':str((here/(name+'-accounting.json')).relative_to(root))})
    candidates.append({'path':str(atlas),'resolved_path':str(atlas.resolve()),
                       'allocated_kib':int(subprocess.check_output(['du','-sk',str(atlas)],text=True).split()[0]),
                       'disposition':'CANDIDATE_ONLY_NOT_REMOVED','source_parent_git_preserved':str(repo/'.git')})
size = json.loads((here/'spree-export-size.json').read_text())
candidates.append({**size,'disposition':'CANDIDATE_ONLY_NOT_REMOVED',
                   'durable_hash_evidence':str((here/'spree-export-size.json').relative_to(root))})
(here/'source-catalog-raw-manifest.json').write_text(json.dumps(records,indent=2)+'\n')
(here/'closed-measurement-cleanup-candidates.json').write_text(json.dumps(candidates,indent=2)+'\n')
(here/'catalog-preservation.json').write_text(json.dumps({'exit_code':0,'elapsed_seconds':time.monotonic()-started,
    'files':len(records),'exact_roundtrip':True,'candidate_only_no_removal':True},indent=2)+'\n')
print(json.dumps({'preserved':len(records),'cleanup_candidates':candidates}))
