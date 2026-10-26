"""Real Linux native8/old7+6, indexed cursor and owned-hook qualification."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from atlas_aci.codegraph import CodeGraph

root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
image=json.loads((out/'cursor-image-build.json').read_text())['image_id']
fixture=json.loads(gzip.decompress((root/'mcp-server/tests/fixtures/s12/native7.json.gz').read_bytes()))
work=Path(tempfile.mkdtemp(prefix='aci-s12-linux-')).resolve();source=work/'source';source.mkdir();graphs=work/'graphs';graphs.mkdir();graphs.chmod(0o777)
for name,text in fixture['identity']['captured_original_source'].items():(source/name).write_text(text)
oldgraphs=work/'oldgraphs';oldgraphs.mkdir();old=CodeGraph(Path(fixture['repository']),graph_root=oldgraphs)
for name,data in fixture['files'].items():
 path=old.atlas_dir/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(base64.b64decode(data))
old.close()
raw=gzip.decompress((root/'mcp-server/tests/fixtures/s07/epoch6.db.gz').read_bytes())
with sqlite3.connect(':memory:') as c:c.deserialize(raw);old6=json.loads(dict(c.execute('SELECT key,value FROM manifest'))['generation_inputs'])['repository']
sixgraphs=work/'sixgraphs';sixgraphs.mkdir();six=CodeGraph(Path(old6),graph_root=sixgraphs);(six.atlas_dir/'graph.6.db').write_bytes(raw);six.close()
record={'image_id':image,'commands':[]}
base=['docker','run','--rm','--read-only','--network','none','-v',f'{source}:/repo:ro']
def run(args):
 command=[*base,*args];p=subprocess.run(command,capture_output=True,text=True,timeout=90);record['commands'].append({'command':command,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr});assert p.returncode==0,p.stderr;return p.stdout
run(['-v',f'{graphs}:/graphs',image,'index','--repo','/repo','--graph-root','/graphs'])
(source/'history.py').write_text('def broken(:\n pass\n')
run(['-v',f'{graphs}:/graphs',image,'index','--repo','/repo','--graph-root','/graphs','--since','HEAD'])
names=['codegraph.py','storage/evidence.py','storage/snapshots.py','query/indexed.py','query/__init__.py']
script=r'''import errno,hashlib,json,os,sqlite3,sys,time
from pathlib import Path
import atlas_aci
from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import ToolError
from atlas_aci.query.indexed import IndexedReader,QueryStopped,OUTGOING_SQL,INCOMING_SQL,NAME_SQL
assert os.geteuid()==10001
try:Path('/repo/forbidden-write').write_text('forbidden')
except OSError as e:assert e.errno==errno.EROFS
else:raise AssertionError('source writable')
g=CodeGraph(Path('/repo'),graph_root=Path('/graphs'),read_only=True)
before=len(os.listdir('/proc/self/fd'));start=time.monotonic()
with g.reader_lease() as r:
 validation=time.monotonic()-start;assert r._lease_schema_epoch==8
 assert len(os.listdir('/proc/self/fd'))==before+2
 seed=r.db.execute(NAME_SQL,('A','')).fetchone()[0]
 plans={name:[x[3] for x in r.db.execute('EXPLAIN QUERY PLAN '+sql,args)] for name,sql,args in [('out',OUTGOING_SQL,(seed,'call','')),('in',INCOMING_SQL,(seed,'call','')),('name',NAME_SQL,('A',''))]}
 assert all(all('SCAN' not in row and 'TEMP B-TREE' not in row for row in plan) for plan in plans.values())
 trace=[];r.db.set_trace_callback(trace.append)
 with IndexedReader(r,max_examined_edges=2,deadline_ms=2000) as query:
  seed=query.name_candidates('A')[0]['id'];a=query.edge(seed,'call','outgoing','');b=query.edge(seed,'call','outgoing',a['id'])
  try:query.edge(seed,'call','outgoing',b['id']);raise AssertionError('overrun')
  except QueryStopped as e:assert e.reason=='examined_edge_limit'
  counts=dict(query.counts)
 assert counts['edge_rows']==counts['examined_edges']==2 and len(trace)==4
 r.db.set_trace_callback(None)
 try:r.db.execute('CREATE TABLE forbidden(x)');raise AssertionError('SQLite writable')
 except sqlite3.OperationalError as e:assert e.sqlite_errorcode==sqlite3.SQLITE_READONLY
 deadline=IndexedReader(r,max_examined_edges=1,deadline_ms=100)
 try:
  with deadline:deadline._one('WITH RECURSIVE x(n) AS (VALUES(0) UNION ALL SELECT n+1 FROM x WHERE n<10000000) SELECT sum(n) FROM x',(),'hydration')
 except QueryStopped as e:assert e.reason=='deadline' and e.__cause__.sqlite_errorcode==sqlite3.SQLITE_INTERRUPT
 else:raise AssertionError('no real interruption')
 assert r.db.execute('WITH RECURSIVE x(n) AS (VALUES(0) UNION ALL SELECT n+1 FROM x WHERE n<1000) SELECT sum(n) FROM x').fetchone()[0]==500500
 assert not deadline._installed
 result={'uid':os.geteuid(),'source_negative_errno':errno.EROFS,'sqlite_version':sqlite3.sqlite_version,'native_epoch':r._lease_schema_epoch,'plans':plans,'trace':trace,'counts':counts,'deadline_counts':deadline.counts,'hook_cleared':True,'lease_validation_seconds':validation}
 assert r.read_source('history.py')==b'def broken(:\n pass\n'
assert len(os.listdir('/proc/self/fd'))==before
g.close()
old=[]
for repo,storage,epoch in [(sys.argv[1],'/oldgraphs',7),(sys.argv[2],'/sixgraphs',6)]:
 g=CodeGraph(Path(repo),graph_root=Path(storage),read_only=True)
 with g.reader_lease() as r:
  assert r._lease_schema_epoch==epoch and r.query('definitions_of:A')['definitions']
  trace=[];r.db.set_trace_callback(trace.append)
  try:IndexedReader(r,max_examined_edges=1,deadline_ms=100);raise AssertionError('old schema traversed')
  except ToolError as e:assert e.code=='INDEX_UPGRADE_REQUIRED'
  assert not trace
  old.append({'epoch':epoch,'generation_id':r.generation_id,'snapshot_id':r.snapshot_id,'upgrade_required_without_navigation_SQL':True})
 g.close()
result['old']=old
package=Path(atlas_aci.__file__).parent
result['runtime_hashes']={name:hashlib.sha256((package/name).read_bytes()).hexdigest() for name in json.loads(sys.argv[3])}
print(json.dumps(result))
'''
result=json.loads(run(['-v',f'{graphs}:/graphs:ro','-v',f'{oldgraphs}:/oldgraphs:ro','-v',f'{sixgraphs}:/sixgraphs:ro','-v',f"{fixture['repository']}:{fixture['repository']}:ro",'--entrypoint','python',image,'-c',script,fixture['repository'],old6,json.dumps(names)]))
for name,sha in result['runtime_hashes'].items():assert hashlib.sha256((root/'mcp-server/src/atlas_aci'/name).read_bytes()).hexdigest()==sha
assert result['old'][0]['generation_id']==fixture['identity']['generation_id']
record['result']=result;assert not (source/'.atlas').exists();(out/'linux-checkpoint.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(result))
