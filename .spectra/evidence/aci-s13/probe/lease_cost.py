"""Isolate mandatory graph/source acquisition from charged indexed query work."""
import asyncio,json,os,sqlite3,sys,time
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.query.admission import Admission
from atlas_aci.query.traversal import navigate
from atlas_aci.protocol.results import sdk_result,result_bytes

name=sys.argv[1];repo=Path(sys.argv[2]);out=Path(sys.argv[3])
# This explicitly separate fixture-selection step chooses a real active source
# ID. It is NOT navigation work, nor part of the isolated acquisition interval.
with sqlite3.connect((repo/'.atlas/graph.8.db').as_uri()+'?mode=ro',uri=True) as conn:
 row=conn.execute('SELECT payload FROM assertions ORDER BY id LIMIT 1').fetchone()
 assertion=json.loads(row[0]);seed=assertion['source_entity_id']
graph=CodeGraph(repo,read_only=True)
begun=time.perf_counter();cpu=time.process_time()
with graph.reader_lease() as reader:
 acquired=time.perf_counter()-begun;acquired_cpu=time.process_time()-cpu
 conn=reader.db;source=reader._source_lease;fd=source.fd
 trace=[];conn.set_trace_callback(trace.append)
 admission=Admission(operation='neighborhood',budget=1024*1024,element_limit=100,
                     generation_id=reader.generation_id,snapshot_id=reader.snapshot_id,
                     evidence_status=reader._lease_evidence_status)
 start=time.perf_counter();query_cpu=time.process_time()
 data=asyncio.run(navigate(reader,seed={'entity_id':seed},max_visited_nodes=2,
      max_examined_edges=1,max_depth=1,deadline_ms=2000,admit=admission))
 payload=admission.finish(data)
 result=sdk_result(payload,error=False,structured=True)
 query_wall=time.perf_counter()-start;query_cpu=time.process_time()-query_cpu
 assert len(trace)==data['work']['sql_queries']
 assert data['work']['examined_edges']<=1 and data['work']['visited_nodes']<=2
 assert all(' WHERE ' in sql for sql in trace)
 assert len(result_bytes(result))<=1024*1024
 record={'repo':name,'generation_id':reader.generation_id,'snapshot_id':reader.snapshot_id,
  'fixture_selection':'one actual assertion source selected outside isolated intervals; no graph rows fabricated',
  'lease_acquisition_seconds':acquired,'lease_acquisition_cpu_seconds':acquired_cpu,
  'mandatory_validation_outside_traversal_deadline':True,
  'query_including_admission_and_sdk_encoding_seconds':query_wall,
  'query_including_admission_and_sdk_encoding_cpu_seconds':query_cpu,
  'work':data['work'],'outcome':data['outcome'],'stop_reason':data['stop_reason'],
  'actual_sql_trace':trace,'result_bytes_dual':len(result_bytes(result)),
  'resource_scope':'outer job RSS is process lifetime peak, not retained bytes per request',
  'cache_state':'new process; OS page-cache state uncontrolled after preceding index/export/account jobs'}
try:os.fstat(fd)
except OSError:record['pack_fd_closed']=True
else:raise AssertionError('pack FD leaked')
try:conn.execute('SELECT 1')
except sqlite3.ProgrammingError:record['sqlite_closed']=True
else:raise AssertionError('SQLite handle leaked')
graph.close()
# Separate S13 resource-owning acquisition/issuance sample, with a real resolved
# target chosen outside the interval. This is not a corpus throughput claim.
from atlas_aci.config import Config
from atlas_aci.query.leases import LeaseRegistry
from atlas_aci.query.paging import issue_pages
from atlas_aci.protocol.resources import read_resource,wire_bytes
from atlas_aci.enforcement import Enforcement
from mcp import types
import base64
with sqlite3.connect((repo/'.atlas/graph.8.db').as_uri()+'?mode=ro',uri=True) as selection:
 selected=None
 for row in selection.execute('SELECT payload FROM assertions ORDER BY id'):
  candidate=json.loads(row[0])
  if candidate['target_entity_id'] is not None and candidate['resolution'] in ('EXTRACTED','INFERRED'):
   selected=candidate;break
assert selected is not None
factory=CodeGraph(repo,read_only=True)
config=Config(repo=repo,memex_root=Path('/tmp/aci-s13-unused-memex'),max_calls_per_minute=0)
registry=LeaseRegistry(config,factory)
async def retained_sample():
 started=time.perf_counter();cpu=time.process_time()
 page=await issue_pages(registry,factory,{'query':{'op':'neighborhood',
  'seed':{'entity_id':selected['target_entity_id']},'direction':'incoming',
  'relations':[selected['relation']],'max_depth':1,'max_visited_nodes':2,
  'max_examined_edges':1,'deadline_ms':2000},'page_max_bytes':1024*1024})
 issuance=time.perf_counter()-started;issuance_cpu=time.process_time()-cpu
 lease=next(iter(registry._leases.values()));conn=lease.reader.db
 source=lease.reader._source_lease;fd=source.fd
 measured={'issuance_including_mandatory_acquisition_seconds':issuance,
  'issuance_cpu_seconds':issuance_cpu,'usage':registry.usage(),
  'chunk_catalog_canonical_bytes':source.chunk_catalog_bytes,
  'page_count':len(lease.pages),'resource_count':len(lease.resources),
  'captured_work':page['data']['captured']['work'],
  'generation_id':page['generation_id'],'snapshot_id':page['snapshot_id']}
 assert page['data']['resources'] and measured['resource_count']>0
 descriptor=page['data']['resources'][0]
 started=time.perf_counter()
 content=(await read_resource(registry,descriptor['uri'],Enforcement(config)))[0]
 measured['resource_handler_seconds']=time.perf_counter()-started
 result=types.ReadResourceResult(contents=[types.BlobResourceContents(uri=descriptor['uri'],
  mimeType=content.mime_type,blob=base64.b64encode(content.content).decode(),_meta=content.meta)])
 measured['resource_result_bytes']=len(wire_bytes(result))
 measured['raw_source_bytes_returned']=len(content.content)
 assert content.content==source.read(descriptor['path'],descriptor['source_sha256'])[descriptor['start_byte']:descriptor['start_byte']+len(content.content)]
 await registry.aclose()
 assert registry.usage()['active']==registry.usage()['pending']==0
 try:os.fstat(fd)
 except OSError:measured['pack_fd_closed']=True
 else:raise AssertionError('retained pack FD leaked')
 try:conn.execute('SELECT 1')
 except sqlite3.ProgrammingError:measured['sqlite_closed']=True
 else:raise AssertionError('retained SQLite leaked')
 factory.close()
 return measured
record['retained_resource_lease_sample']=asyncio.run(retained_sample())
(out/f'{name}-lease-cost.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
