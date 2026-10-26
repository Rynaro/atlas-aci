"""Actual typed relation and exact-ID selection proof on a mixed-language fixture."""
import asyncio,json,tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.server import build_server_state,call_tool_result

out=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='aci-s12-typed-') as temp:
 root=Path(temp);repo=root/'repo';repo.mkdir()
 (repo/'a.py').write_text('def A():\n B(); B()\ndef B():\n pass\n')
 (repo/'types.rb').write_text('class A\nend\nclass Child < A\nend\n')
 writer=CodeGraph(repo);writer.build()
 with writer.reader_lease() as reader:
  # Fixture setup chooses actual canonical IDs. This is not traversal work.
  entities=[json.loads(row[0]) for row in reader.db.execute('SELECT payload FROM entities')]
 ids={(e['language'],e['name']):e['id'] for e in entities}
 config=Config(repo=repo,memex_root=root/'memex');enforcement,memex,factory=build_server_state(config)
 results=[]
 async def call(args):
  args={'schema_version':'atlas.graph.v1','deadline_ms':2000,**args}
  result=await call_tool_result('graph_query',args,config,enforcement,memex,factory)
  assert not result.isError,result
  results.append({'arguments':args,'result':result.model_dump(mode='json',exclude_none=True)})
  return result.structuredContent['data']
 async def exercise():
  ambiguous=await call({'op':'neighborhood','seed':{'name':'A'}})
  assert ambiguous['outcome']=='disambiguation' and ambiguous['work']['visited_nodes']==0
  assert {c['language'] for c in ambiguous['seed']['candidates']}=={'python','ruby'}
  selected=await call({'op':'neighborhood','seed':{'entity_id':ids['python','A']},'max_depth':1})
  assert selected['seed']['entity_id']==ids['python','A'] and len(selected['edges'])==2
  typed=await call({'op':'path','seed':{'entity_id':ids['ruby','Child']},
                    'target':{'entity_id':ids['ruby','A']},'relations':['superclass'],
                    'confidence':['EXTRACTED']})
  assert typed['outcome']=='complete' and len(typed['path']['hops'])==1
  assert typed['path']['hops'][0]['assertion']['relation']=='superclass'
  assert typed['path']['hops'][0]['assertion']['resolution']=='EXTRACTED'
  reverse=await call({'op':'path','seed':{'entity_id':ids['ruby','A']},
                      'target':{'entity_id':ids['ruby','Child']},'relations':['superclass'],
                      'direction':'incoming'})
  assert reverse['path']['hops'][0]['assertion']==typed['path']['hops'][0]['assertion']
 try:asyncio.run(exercise())
 finally:factory.close();writer.close()
(out/'typed-operations.json').write_text(json.dumps({'source_checkpoint':'.spectra/evidence/aci-s12/source-final.json','results':results,'all_controls_passed':True},indent=2)+'\n')
print('TYPED_ID_AND_AMBIGUITY_CONTROLS_PASSED')
