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
(out/f'{name}-lease-cost.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
