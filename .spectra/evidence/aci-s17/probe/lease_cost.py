"""Separate mandatory generation acquisition from actual exact and lexical queries."""
import asyncio,hashlib,json,os,re,sqlite3,sys,time
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.query.retrieval import retrieve
from atlas_aci.query.retrieval_admission import RetrievalAdmission
from atlas_aci.protocol.results import sdk_result,result_bytes
name=sys.argv[1];repo=Path(sys.argv[2]);out=Path(sys.argv[3])
# Sample selection is outside acquisition/query intervals, from real native rows.
with sqlite3.connect((repo/'.atlas/graph.9.db').as_uri()+'?mode=ro',uri=True) as selection:
 entity_id=selection.execute("SELECT candidate_id FROM retrieval_documents WHERE kind='entity' ORDER BY candidate_id LIMIT 1").fetchone()[0]
 file=selection.execute("SELECT path,text FROM retrieval_documents WHERE kind='source_file' ORDER BY path LIMIT 1").fetchone()
 words=re.findall(r'[A-Za-z][A-Za-z0-9_]*',file[1]);phrase=' '.join(words[:2]);assert phrase
 storage={r[0]:r[1] for r in selection.execute("SELECT name,SUM(pgsize) FROM dbstat GROUP BY name") if r[0].startswith('retrieval_') or r[0].startswith('sqlite_autoindex_retrieval_')}
 counts=dict(selection.execute("SELECT kind,COUNT(*) FROM retrieval_documents GROUP BY kind"))
graph=CodeGraph(repo,read_only=True);started=time.perf_counter();cpu=time.process_time()
with graph.reader_lease() as reader:
 acquired=time.perf_counter()-started;acquired_cpu=time.process_time()-cpu
 conn=reader.db;source=reader._source_lease;fd=source.fd;samples=[]
 for label,text,prefixes in [('exact_id',entity_id,[]),('lexical_phrase',phrase,[file[0]])]:
  trace=[];conn.set_trace_callback(trace.append)
  admission=RetrievalAdmission(reader,budget=1024*1024,element_limit=100)
  begun=time.perf_counter();query_cpu=time.process_time()
  data=asyncio.run(retrieve(reader,text=text,path_prefixes=prefixes,max_candidates=5,admit=admission))
  result=sdk_result(admission.result,error=False,structured=True)
  elapsed=time.perf_counter()-begun;query_cpu=time.process_time()-query_cpu
  conn.set_trace_callback(None)
  assert data['candidates']
  if label=='exact_id':assert data['candidates'][0]['candidate_id']==entity_id and data['candidates'][0]['match_tier']=='exact_entity_id'
  else:assert any(c['kind']=='source_file' and c['path']==file[0] for c in data['candidates'])
  samples.append({'label':label,'text':text,'path_prefixes':prefixes,'seconds':elapsed,'cpu_seconds':query_cpu,'work':data['work'],'candidates':[{'candidate_id':c['candidate_id'],'kind':c['kind'],'path':c['path'],'match_tier':c['match_tier']} for c in data['candidates']],'actual_sql_trace':trace,'result_bytes_dual':len(result_bytes(result)),'diagnostic_reason':data['diagnostic_reason']})
 record={'repo':name,'generation_id':reader.generation_id,'snapshot_id':reader.snapshot_id,'lease_acquisition_seconds':acquired,'lease_acquisition_cpu_seconds':acquired_cpu,'mandatory_validation_outside_query_deadline':True,'queries':samples,'sample_selection':'actual native entityID and first two ASCII words in one available source file, selected outside intervals','query_interval':'core ranking, whole-candidate admission and SDK result construction; final wire dump/size calculation outside interval','resource_scope':'outer job RSS is process lifetime peak, not retained memory per request','cache_state':'new process; OS page-cache state uncontrolled','retrieval_dbstat_bytes':storage,'retrieval_document_counts':counts}
try:os.fstat(fd)
except OSError:record['pack_fd_closed']=True
else:raise RuntimeError('pack FD leaked')
try:conn.execute('SELECT 1')
except sqlite3.ProgrammingError:record['sqlite_closed']=True
else:raise RuntimeError('SQLite leaked')
graph.close();(out/f'{name}-lease-cost.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)
