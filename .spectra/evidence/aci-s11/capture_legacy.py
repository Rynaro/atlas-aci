"""Before-product real parser fixture and identical source-free portable oracle."""
import asyncio,hashlib,importlib.metadata,json,tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.server import build_server_state,dispatch_tool_call
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'mcp-server/tests/fixtures/s11';HERE=Path(__file__).resolve().parent
sources={'app/target.rb':'class Target\n def self.bar; end\nend\n','app/hub.rb':'class Hub\n def bar; end\n def call\n  # NOTE: keep the explicit target\n  Target.bar\n  bar()\n end\nend\n','app/a.rb':'class Hub\n def dup_name; end\nend\n','app/b.rb':'class Hub\n def dup_name; end\nend\n','app/caller.rb':'class Hub\n def call_duplicate\n  dup_name()\n end\nend\n','app/base.rb':'class Base; end\nclass Child < Base; end\n','history.py':'def history_target():\n return 1\ndef history_caller():\n # NOTE: retain original evidence\n history_target()\n'}
base=Path(tempfile.mkdtemp(prefix='aci-s11-baseline-',dir='/tmp')).resolve();repo=base/'source';repo.mkdir()
for name,content in sources.items():path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
g=CodeGraph(repo);g.build();(repo/'history.py').write_bytes(b'def broken(:\n pass\n');g.build()
assert {row[0] for row in g.db.execute('SELECT DISTINCT confidence FROM edges')}=={'EXTRACTED','INFERRED','AMBIGUOUS'}
artifact=OUT/'legacy-evidence.jsonl';g.export_jsonl(artifact);g.close();raw=artifact.read_bytes();destination=base/'imported';destination.mkdir();importer=CodeGraph(destination);importer.import_jsonl(artifact);importer.close()
queries=['definitions_of:Target','callers_of:bar','subclasses_of:Base','god_nodes:','communities:','rationale:','definitions_of:NoMatch','callers_of:dup_name','definitions_of:history_target','callers_of:history_target','not-a-query','unknown_operation:x']
async def capture():
 config=Config(repo=destination,memex_root=base/'memex');enforcement,memex,factory=build_server_state(config);results=[]
 try:
  for query in queries:
   value=await dispatch_tool_call('graph_query',{'query':query},config,enforcement,memex,factory);results.append({'arguments':{'query':query},'decoded':value})
 finally:factory.close()
 return results
results=asyncio.run(capture());assert results[7]['decoded']['edges'][0]['confidence']=='AMBIGUOUS';assert results[8]['decoded']['historical_definitions'];assert results[9]['decoded']['historical_edges'];assert results[6]['decoded']['definitions']==[];assert results[10]['decoded']['error']=='INVALID_QUERY';assert results[11]['decoded']['error']=='UNKNOWN_VERB'
(OUT/'legacy-baseline.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n');(OUT/'legacy-source.json').write_text(json.dumps({'initial_sources':sources,'published_overlay_on_disk':{'history.py':'def broken(:\n pass\n'}},indent=2)+'\n');(OUT/'legacy-normalization.json').write_text(json.dumps({'allowed_paths':['$.generation_id'],'reason':'The same frozen artifact imported under a different destination/importer legitimately changes only generation anchoring; snapshot_id remainsnull/source_availablefalse. No evidence IDs/links/rowIDs/confidence/reasons/order/counts/errors may be normalized.','require_actual_difference_provenance':True},indent=2)+'\n')
provenance={'base_commit':'138a6f3cd510e0ecb3be0832221236adfe441ebc','artifact_path':str(artifact.relative_to(ROOT)),'artifact_sha256':hashlib.sha256(raw).hexdigest(),'artifact_bytes':len(raw),'header':json.loads(raw.splitlines()[0]),'source':str(repo),'baseline_import_destination':str(destination),'baseline_captured_before_product_edits':True,'query_count':len(results),'native_source_snapshot':'RealG1 then parser-recovered syntax failure G2 retaining historical facts; frozen artifact imported source-free for legacy parity.','versions':{name:importlib.metadata.version(name) for name in ['mcp','pydantic','jsonschema','tree-sitter','tree-sitter-language-pack']},'baseline_product_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'mcp-server/src/atlas_aci/codegraph.py',ROOT/'mcp-server/src/atlas_aci/server.py',ROOT/'mcp-server/src/atlas_aci/tools/graph_query.py']}}
(OUT/'legacy-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n');(HERE/'baseline-fixture-context.json').write_text(json.dumps(provenance,indent=2)+'\n');print(json.dumps(provenance),flush=True)
