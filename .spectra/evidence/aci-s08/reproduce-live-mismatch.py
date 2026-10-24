import asyncio,json,tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.server import build_server_state,dispatch_tool_call

with tempfile.TemporaryDirectory(prefix='aci-s08-red-') as temp:
 repo=Path(temp)/'repo';repo.mkdir();path=repo/'a.py';old=b'def old_target():\n    return 1\n';new=b'def new_target():\n    return 2\n';path.write_bytes(old)
 writer=CodeGraph(repo);writer.build();config=Config(repo=repo,memex_root=Path(temp)/'memex');enforcement,memex,factory=build_server_state(config)
 try:
  with factory.reader_lease() as reader:
   definitions=reader.search_symbol('old_target')['definitions'];assert definitions
   path.write_bytes(new);writer.build()
   viewed=asyncio.run(dispatch_tool_call('view_file',{'path':'a.py','start_line':1,'end_line':2},config,enforcement,memex,factory))
   observed=''.join(viewed['lines']);print(json.dumps({'leased_definitions':definitions,'leased_generation':reader.generation_id,'live_bytes':observed,'expected_leased_bytes':old.decode(),'mismatch_observed':observed!=old.decode()}),flush=True)
   assert observed==old.decode(),'Actual leased graph G1 paired with live view_file G2'
 finally:factory.close();writer.close()
