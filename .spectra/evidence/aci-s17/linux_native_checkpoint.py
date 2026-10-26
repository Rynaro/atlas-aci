"""Actual current native9 derivative on qualified Linux runtime, source and DB RO."""
import hashlib,json,os,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
image='sha256:d788f9e9ace4e4bad602859ee24d2fd1119778e01ead6c44d50aa1252b24d18b'
source=Path('/tmp/aci-s17-linux-native9-source');graphs=Path('/tmp/aci-s17-linux-native9-graphs')
assert not source.exists() and not graphs.exists();source.mkdir();graphs.mkdir();graphs.chmod(0o777)
(source/'a.py').write_text('def Alpha():\n Beta()\n# unique task phrase café\n');(source/'b.py').write_text('def Beta():\n pass\n')
record={'image_id':image,'commands':[],'runtime_mount_read_only':str(root/'mcp-server/src')}
base=['docker','run','--rm','--read-only','--network','none','-e','PYTHONPATH=/s17','-v',str(root/'mcp-server/src')+':/s17:ro','-v',str(source)+':/repo:ro']
def run(script,writable):
 command=[*base,'-v',str(graphs)+':/graphs'+('' if writable else ':ro'),'--entrypoint','python',image,'-c',script]
 p=subprocess.run(command,capture_output=True,text=True,timeout=90);record['commands'].append({'argv':command,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr});assert p.returncode==0,p.stderr;return json.loads(p.stdout)
writer='''import json,shutil,sqlite3,os
from pathlib import Path
from atlas_aci.codegraph import CodeGraph,SCHEMA_EPOCH
assert os.geteuid()==10001 and SCHEMA_EPOCH==9
repo=Path('/repo');g=CodeGraph(repo,graph_root=Path('/graphs/healthy'));g.build();g.close()
with sqlite3.connect(g.db_path) as conn:
 rows=conn.execute('SELECT rowid,candidate_id,text FROM retrieval_fts ORDER BY rowid').fetchall()
 assert len(rows)>2
for variant in ['idbad','contentbad']:
 d=CodeGraph(repo,graph_root=Path('/graphs')/variant)
 shutil.copytree(g.atlas_dir,d.atlas_dir,dirs_exist_ok=True)
 with sqlite3.connect(d.db_path) as conn:
  if variant=='idbad':
   a=rows[0];b=next(r for r in rows[1:] if r[2]!=a[2])
   for current,other in [(a,b),(b,a)]:conn.execute('UPDATE retrieval_fts_content SET c0=? WHERE id=?',(other[1],current[0]))
   assert conn.execute('PRAGMA integrity_check(retrieval_fts)').fetchall()==[('ok',)]
  else:conn.execute('UPDATE retrieval_fts_content SET c1=? WHERE id=?',('changed without index updates',rows[0][0]))
 d.close()
print(json.dumps({'uid':os.geteuid(),'schema_epoch':SCHEMA_EPOCH,'documents':len(rows),'sqlite':sqlite3.sqlite_version}))
'''
reader='''import errno,hashlib,json,os,sqlite3,stat
from pathlib import Path
import atlas_aci
from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import ToolError
assert os.geteuid()==10001
try:Path('/repo/negative-write').write_text('no')
except OSError as e:assert e.errno==errno.EROFS
else:raise AssertionError('source writable')
def inventory():
 values={}
 def visit(p):
  s=p.lstat();assert stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode)
  values[str(p)]={'mode':s.st_mode,'uid':s.st_uid,'gid':s.st_gid,'size':s.st_size,'mtime_ns':s.st_mtime_ns,'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if stat.S_ISREG(s.st_mode) else None}
  if stat.S_ISDIR(s.st_mode):
   with os.scandir(p) as it:
    for child in sorted(it,key=lambda e:e.name):visit(Path(child.path))
 for p in [Path('/repo'),Path('/graphs')]:visit(p)
 return values
before=inventory();repo=Path('/repo');g=CodeGraph(repo,graph_root=Path('/graphs/healthy'),read_only=True)
with g.reader_lease() as lease:
 inputs=json.loads(lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0])
 assert inputs['format']=='atlas-generation.v5'
 assert [tuple(r) for r in lease.db.execute('PRAGMA integrity_check(retrieval_fts)')]==[('ok',)]
 rows=lease.db.execute('SELECT candidate_id FROM retrieval_fts WHERE retrieval_fts MATCH ?',('"unique task phrase"',)).fetchall();assert len(rows)==1
 plans={}
 for key,value,index in [('candidate_id',rows[0][0],'sqlite_autoindex_retrieval_documents_1'),('path','a.py','idx_retrieval_path'),('name','Alpha','idx_retrieval_name')]:
  sql='SELECT candidate_id FROM retrieval_documents WHERE '+key+'=? ORDER BY candidate_id LIMIT 2'
  plan=[r[3] for r in lease.db.execute('EXPLAIN QUERY PLAN '+sql,(value,))]
  assert any('SEARCH' in r and index in r for r in plan) and not any('SCAN' in r or 'TEMP B-TREE' in r for r in plan)
  assert lease.db.execute(sql,(value,)).fetchone();plans[key]=plan
 lexplan=[r[3] for r in lease.db.execute('EXPLAIN QUERY PLAN SELECT candidate_id,bm25(retrieval_fts) FROM retrieval_fts WHERE retrieval_fts MATCH ? ORDER BY bm25(retrieval_fts),candidate_id LIMIT 2',('"unique task phrase"',))]
 good={'generation_id':lease.generation_id,'snapshot_id':lease.snapshot_id,'retrieval':inputs['retrieval'],'exact_plans':plans,'fts_plan':lexplan}
g.close();rejected={}
for variant in ['idbad','contentbad']:
 bad=CodeGraph(repo,graph_root=Path('/graphs')/variant,read_only=True)
 try:
  with bad.reader_lease():raise AssertionError('corruption served')
 except ToolError as e:assert e.code=='INDEX_UNAVAILABLE';rejected[variant]=e.code
 bad.close()
after=inventory();assert before==after
module=Path(atlas_aci.__file__).parent
print(json.dumps({'uid':os.geteuid(),'negative_errno':errno.EROFS,'sqlite':sqlite3.sqlite_version,'actual_import':str(module),'runtime_hashes':{str(p.relative_to(module)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(module.rglob('*.py'))},'healthy':good,'corrupt_rejected':rejected,'before':before,'after':after}))
'''
try:
 record['writer']=run(writer,True);record['reader']=run(reader,False)
 actual={str(p.relative_to(root/'mcp-server/src/atlas_aci')):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'mcp-server/src/atlas_aci').rglob('*.py'))}
 assert record['reader']['runtime_hashes']==actual
 record['exit_code']=0
finally:(out/'linux-native-checkpoint.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':0,'runtime_hash_count':len(actual),'sqlite':record['reader']['sqlite'],'corrupt_rejected':record['reader']['corrupt_rejected']}))
