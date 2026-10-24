"""Real locked SDK subprocess qualification before Atlas product edits."""
import asyncio,json,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters,types
from mcp.client.stdio import stdio_client
from mcp.server import Server
from mcp.server.stdio import stdio_server
import jsonschema
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
OUTPUT={'type':'object','required':['ok'],'properties':{'ok':{'type':'boolean'}},'additionalProperties':False}
async def probe_server():
 server=Server('s11-sdk-probe');calls=0
 @server.list_tools()
 async def tools():return [types.Tool(name=name,inputSchema={'type':'object','required':['value'],'properties':{'value':{'type':'string'}}},outputSchema=OUTPUT) for name in ('direct','normal')]
 @server.call_tool()
 async def call(name,arguments):
  nonlocal calls
  calls+=1;data={'wrong':True,'requested_version':server.request_context.session.client_params.protocolVersion,'calls':calls}
  if name=='normal':return data
  return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(data))],structuredContent=data,isError=False)
 async with stdio_server() as streams:await server.run(*streams,server.create_initialization_options())
async def initialized(session,requested):
 response=await session.send_request(types.ClientRequest(types.InitializeRequest(params=types.InitializeRequestParams(protocolVersion=requested,capabilities=types.ClientCapabilities(),clientInfo=types.Implementation(name='s11-preflight',version='1')))),types.InitializeResult)
 await session.send_notification(types.ClientNotification(types.InitializedNotification()));return response
async def raw_call(session,name,arguments):
 return await session.send_request(types.ClientRequest(types.CallToolRequest(params=types.CallToolRequestParams(name=name,arguments=arguments))),types.CallToolResult)
async def capture():
 results=[]
 for requested in ('2024-11-05','2025-03-26','2025-06-18','2025-11-25','2099-01-01'):
  async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(Path(__file__).resolve()),'--server'])) as streams:
   async with ClientSession(*streams) as session:
    init=await initialized(session,requested);tools=await session.list_tools();direct=await raw_call(session,'direct',{'value':'ok'});invalid=await raw_call(session,'direct',{});again=await raw_call(session,'direct',{'value':'ok'});normal=await raw_call(session,'normal',{'value':'ok'})
    assert direct.structuredContent['calls']==1 and again.structuredContent['calls']==2 and invalid.isError and normal.isError
    assert not direct.isError and not jsonschema.Draft7Validator(OUTPUT).is_valid(direct.structuredContent)
    results.append({'requested':requested,'actual_negotiated':init.protocolVersion,'session_requested_observed':direct.structuredContent['requested_version'],'tools':[tool.model_dump(by_alias=True,exclude_none=True) for tool in tools.tools],'direct_invalid_output_accepted':direct.model_dump(by_alias=True,exclude_none=True),'input_invalid_bypasses_application':invalid.model_dump(by_alias=True,exclude_none=True),'normal_invalid_output_rejected':normal.model_dump(by_alias=True,exclude_none=True)})
 provenance=json.loads((ROOT/'mcp-server/tests/fixtures/s11/legacy-provenance.json').read_text());repo=provenance['baseline_import_destination'];base=Path(repo).parent;production=[]
 async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','atlas_aci','serve','--repo',repo,'--memex-root',str(base/'native-memex')])) as streams:
  async with ClientSession(*streams) as session:
   init=await session.initialize();tools=await session.list_tools()
   for arguments in ({'query':'not-a-query'},{'query':'unknown_operation:x'},{'query':'definitions_of:NoMatch'},{'schema_version':'atlas.graph.v1','op':'status'}):
    response=await session.call_tool('graph_query',arguments);production.append({'arguments':arguments,'result':response.model_dump(by_alias=True,exclude_none=True)})
   assert [r['result']['isError'] for r in production]==[False,False,False,True]
   assert len(tools.tools)==7
 result={'sdk_versions':results,'production_initialize':init.model_dump(by_alias=True,exclude_none=True),'production_tools':[tool.model_dump(by_alias=True,exclude_none=True) for tool in tools.tools],'production_results':production,'genuine_red':'Legacy INVALID_QUERY/UNKNOWN_VERB payloads incorrectly have isErrorFalse; structured status is not supported. Direct SDK CallToolResult bypasses outputSchema; input validation occurs before handler.'};(HERE/'sdk-checkpoint.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'sdk_sessions':len(results),'production_calls':len(production),'actual_future_negotiated':results[-1]['actual_negotiated'],'future_session_requested':results[-1]['session_requested_observed'],'baseline_red_confirmed':True}),flush=True)
if __name__=='__main__':asyncio.run(probe_server() if '--server' in sys.argv else capture())
