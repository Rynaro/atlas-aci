"""Real native G1 ownership and unsupported page baseline before product edits."""
import asyncio,hashlib,json,os,tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.query.traversal import navigate
from atlas_aci.server import build_server_state,call_tool_result

out=Path(__file__).resolve().parent
root=Path(tempfile.mkdtemp(prefix='aci-s13-baseline-')).resolve();repo=root/'repo';repo.mkdir()
source=b'def A():\n B(); B(); B()\ndef B():\n pass\n';(repo/'a.py').write_bytes(source)
writer=CodeGraph(repo);writer.build()
config=Config(repo=repo,memex_root=root/'memex');enforcement,memex,factory=build_server_state(config)
args={'schema_version':'atlas.graph.v1','op':'page','query':{'op':'neighborhood','seed':{'name':'A'}},'page_max_bytes':20000}
response=asyncio.run(call_tool_result('graph_query',args,config,enforcement,memex,factory))
assert response.isError and response.structuredContent['error']['code'] in {'MIXED_REQUEST','INVALID_OPERATION'}
with writer.reader_lease() as held:
 generation=held.generation_id;snapshot=held.snapshot_id;conn=held.db;pack=held._source_lease;fd=pack.fd
 old_manifest=writer.atlas_dir/'source-snapshots'/('manifest.'+snapshot+'.json')
 old_pack=writer.atlas_dir/'source-snapshots'/('pack.'+pack.manifest['pack']['sha256']+'.bin')
 captured=asyncio.run(navigate(held,seed={'name':'A'},max_depth=1,deadline_ms=2000))
 assert len(captured['edges'])==3
 canonical=json.dumps(captured,sort_keys=True,separators=(',',':'))
 for version in (2,3):
  (repo/'a.py').write_text('def new'+str(version)+'():\n pass\n');writer.build()
  assert held.generation_id==generation and held.read_source('a.py')==source
  assert conn.execute("SELECT value FROM manifest WHERE key='generation_id'").fetchone()[0]==generation
 assert not old_manifest.exists() and not old_pack.exists()
 assert json.dumps(captured,sort_keys=True,separators=(',',':'))==canonical
 record={'native_epoch':held._lease_schema_epoch,'generation_id':generation,'snapshot_id':snapshot,'captured_result':captured,'captured_result_sha256':hashlib.sha256(canonical.encode()).hexdigest(),'source_sha256':hashlib.sha256(source).hexdigest(),'source_bytes':len(source),'g1_names_pruned_after_g3':True,'held_sqlite_generation_retained':True,'held_pack_bytes_retained':True,'baseline_page_request':args,'baseline_page_result':response.model_dump(mode='json',exclude_none=True),'product_feature_missing':True}
assert pack.fd is None
try:conn.execute('SELECT 1')
except Exception as e:assert e.__class__.__name__=='ProgrammingError';record['sqlite_closed_after_context']=True
else:raise AssertionError('SQLite leaked')
try:os.fstat(fd)
except OSError:record['pack_fd_closed_after_context']=True
else:raise AssertionError('pack leaked')
factory.close();writer.close()
(out/'baseline-checkpoint.json').write_text(json.dumps(record,indent=2)+'\n')
print('BASELINE_G1_SQLITE_PACK_CAPTURED_RESULT_PRUNE_PASS; PAGE_REAL_RED_UNSUPPORTED_NESTED_QUERY')
