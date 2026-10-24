"""Remove only root-authorized, independently audited closed S11 scratch outputs."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import time

here=Path(__file__).resolve().parent
started=time.monotonic()
before=os.statvfs('/tmp').f_bavail*os.statvfs('/tmp').f_frsize
artifacts=json.loads((here/'source-catalog-raw-manifest.json').read_text())
root=here.parents[3]
pre=[]
for name in ('solidus','spree'):
 repo=Path('/tmp/aci-s11-probe-inputs')/name;atlas=repo/'.atlas'
 assert stat.S_ISDIR(atlas.lstat().st_mode) and not atlas.is_symlink()
 assert atlas.resolve().parent==repo.resolve() and (repo/'.git').is_dir()
 expected_pin=json.loads((here/'environment.json').read_text())['pins'][name][1]
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()==expected_pin
 assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=repo,text=True).strip()
 assert not any(p.is_symlink() for p in atlas.rglob('*'))
 account=json.loads((here/(name+'-accounting.json')).read_text())
 with sqlite3.connect((atlas/'graph.7.db').as_uri()+'?mode=ro',uri=True) as conn:
  metadata=dict(conn.execute('SELECT key,value FROM manifest'))
 assert metadata['generation_id']==account['source_snapshot']['generation_id']
 assert json.loads(metadata['generation_inputs'])==account['generation_inputs']
 for item in [a for a in artifacts if a['repo']==name]:
  packed=(root/item['compressed_path']).read_bytes();raw=gzip.decompress(packed)
  assert hashlib.sha256(packed).hexdigest()==item['compressed_sha256']
  assert hashlib.sha256(raw).hexdigest()==item['uncompressed_sha256']
  assert Path(item['original_path']).read_bytes()==raw
 pack=atlas/'source-snapshots'/('pack.'+account['source_snapshot']['pack_sha256']+'.bin')
 assert stat.S_ISREG(pack.lstat().st_mode) and not pack.is_symlink()
 with pack.open('rb') as stream:pack_sha=hashlib.file_digest(stream,'sha256').hexdigest()
 assert pack_sha==account['source_snapshot']['pack_sha256'] and pack.stat().st_size==account['source_snapshot']['pack_bytes']
 pre.append({'path':str(atlas),'resolved_path':str(atlas.resolve()),'allocated_kib':int(subprocess.check_output(['du','-sk',str(atlas)],text=True).split()[0]),'generation_id':metadata['generation_id'],'snapshot_id':account['source_snapshot']['snapshot_id'],'pack_sha256':pack_sha,'pack_bytes':pack.stat().st_size,'source_parent_git':str(repo/'.git')})
raw=Path('/tmp/aci-s11-spree-export.jsonl');assert stat.S_ISREG(raw.lstat().st_mode) and not raw.is_symlink()
expected=json.loads((here/'spree-export-size.json').read_text())
with raw.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
assert raw.stat().st_size==expected['bytes']==651845336 and sha==expected['sha256']=='fdd923467e5a8a0e572f66f48efe269f775d5334dadfd7a060db23708c6a88d6'
pre.append({'path':str(raw),'bytes':raw.stat().st_size,'sha256':sha})
(here/'closed-measurement-cleanup-preflight.json').write_text(json.dumps({'authorized_exact_paths':pre,'available_bytes':before,'independent_raw_and_preservation_audit':'ATLAS PASS before removal'},indent=2)+'\n')
for item in pre[:2]:shutil.rmtree(item['path'])
raw.unlink()
assert all(not Path(item['path']).exists() for item in pre)
assert all(Path(item['source_parent_git']).is_dir() for item in pre[:2])
after=os.statvfs('/tmp').f_bavail*os.statvfs('/tmp').f_frsize
result={'exit_code':0,'removed_exact_paths':[item['path'] for item in pre],'available_bytes_before':before,'available_bytes_after':after,'observed_available_bytes_gain':after-before,'elapsed_seconds':time.monotonic()-started,'source_parents_git_retained':True,'all_compact_and_preserved_evidence_retained':True,'df_after':subprocess.check_output(['df','-k','/tmp'],text=True)}
(here/'closed-measurement-cleanup.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
