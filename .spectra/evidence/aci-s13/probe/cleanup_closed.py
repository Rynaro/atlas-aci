"""Exact root-authorized closed measurement cleanup after independent preservation."""
import gzip,hashlib,json,os,shutil,sqlite3,stat,subprocess,time
from pathlib import Path
from atlas_aci.storage import snapshots
here=Path(__file__).resolve().parent;root=here.parents[3];started=time.monotonic()
expected=['/tmp/aci-s13-probe-inputs/solidus/.atlas','/tmp/aci-s13-probe-inputs/spree/.atlas','/tmp/aci-s13-spree-export.jsonl']
candidates=json.loads((here/'closed-measurement-cleanup-candidates.json').read_text());assert [r['path'] for r in candidates]==expected
record={'authority':'Root exact three-path conditional approval; ATLAS final raw and explicit four-stream preservation APPROVE. All jobs/readers closed.','expected_paths':expected,'predelete':[],'retained_evidence':['source-catalog-raw-manifest.json','graph-raw-manifest.json','../raw-manifest.json'],'df_before':subprocess.check_output(['df','-k','/tmp'],text=True)}
# Independently recheck every packaged raw log before removing any scratch.
for row in json.loads((here.parent/'raw-manifest.json').read_text()):
 packed=(root/row['compressed_path']).read_bytes();raw=gzip.decompress(packed)
 assert len(packed)==row['compressed_bytes'] and hashlib.sha256(packed).hexdigest()==row['compressed_sha256']
 assert len(raw)==row['uncompressed_bytes'] and hashlib.sha256(raw).hexdigest()==row['uncompressed_sha256']
assert len(json.loads((here.parent/'raw-manifest.json').read_text()))==122
for name in ('solidus','spree'):
 repo=Path('/tmp/aci-s13-probe-inputs')/name;atlas=repo/'.atlas'
 assert stat.S_ISDIR(atlas.lstat().st_mode) and not atlas.is_symlink()
 assert atlas.resolve().parent==repo.resolve() and (repo/'.git').is_dir()
 account=json.loads((here/(name+'-accounting.json')).read_text());files=[];manifest=None
 for row in json.loads((here/'source-catalog-raw-manifest.json').read_text()):
  if row['repo']!=name:continue
  original=Path(row['original_path']);assert stat.S_ISREG(original.lstat().st_mode) and not original.is_symlink()
  packed=(root/row['compressed_path']).read_bytes();raw=gzip.decompress(packed)
  assert original.read_bytes()==raw
  assert len(raw)==row['uncompressed_bytes'] and hashlib.sha256(raw).hexdigest()==row['uncompressed_sha256']
  obj=json.loads(raw)
  if row['kind']=='manifest':manifest=obj
  else:assert obj==[account['source_snapshot']['snapshot_id']]
  files.append({'path':str(original),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'durable':row['compressed_path']})
 assert snapshots.digest(manifest)==account['source_snapshot']['snapshot_id']
 db=atlas/'graph.8.db';assert stat.S_ISREG(db.lstat().st_mode) and not db.is_symlink()
 with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:values=dict(conn.execute('SELECT key,value FROM manifest'))
 inputs=json.loads(values['generation_inputs']);context=manifest['context']
 assert values['generation_id']==account['source_snapshot']['generation_id']
 assert all(inputs[k]==context[k] for k in ('repository','schema','configuration','producer'))
 observations=[r for r in json.loads(values['source_observations']) if r['type']=='observation']
 assert {r['path']:r['sha256'] for r in manifest['current']}=={r['path']:r['observed_source_sha256'] for r in observations}
 pack=atlas/'source-snapshots'/('pack.'+manifest['pack']['sha256']+'.bin')
 assert stat.S_ISREG(pack.lstat().st_mode) and not pack.is_symlink()
 raw=pack.read_bytes();assert len(raw)==manifest['pack']['size'] and hashlib.sha256(raw).hexdigest()==manifest['pack']['sha256']
 for item in manifest['pack']['index']:
  assert 0<=item['offset']<=item['offset']+item['length']<=len(raw)
  assert hashlib.sha256(raw[item['offset']:item['offset']+item['length']]).hexdigest()==item['sha256']
 with db.open('rb') as stream:db_sha=hashlib.file_digest(stream,'sha256').hexdigest()
 record['predelete'].append({'path':str(atlas),'resolved_path':str(atlas.resolve()),'allocated_kib':int(subprocess.check_output(['du','-sk',str(atlas)],text=True).split()[0]),'generation_id':values['generation_id'],'snapshot_id':account['source_snapshot']['snapshot_id'],'database_bytes':db.stat().st_size,'database_sha256':db_sha,'preserved_files':files,'pack_bytes':len(raw),'pack_sha256':manifest['pack']['sha256'],'index_members_verified':len(manifest['pack']['index']),'current_observations_verified':len(observations),'context_exact':True})
artifact=Path(expected[2]);assert stat.S_ISREG(artifact.lstat().st_mode) and not artifact.is_symlink()
assert artifact.stat().st_size==651845336
with artifact.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
assert sha=='241dedc74190f5f3deb90995111294148568ed0f40d131c1391ca9335bc66a8e'
record['predelete'].append({'path':str(artifact),'bytes':artifact.stat().st_size,'sha256':sha})
# All preconditions passed before the first destructive operation.
record['all_preconditions_passed']=True
(here/'cleanup-preflight.json').write_text(json.dumps(record,indent=2)+'\n')
for path in expected:
 p=Path(path)
 if p.name=='.atlas':shutil.rmtree(p)
 else:p.unlink()
 assert not os.path.lexists(p)
for name in ('solidus','spree'):
 assert (Path('/tmp/aci-s13-probe-inputs')/name/'.git').is_dir()
 assert (here/(name+'-graph.json.gz')).is_file()
record['deleted_exact_paths']=expected;record['source_parents_git_preserved']=True
record['df_after']=subprocess.check_output(['df','-k','/tmp'],text=True)
record['elapsed_seconds']=time.monotonic()-started;record['exit_code']=0
(here/'cleanup-closed.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':0,'deleted_exact_paths':expected,'elapsed_seconds':record['elapsed_seconds'],'df_after':record['df_after']}))
