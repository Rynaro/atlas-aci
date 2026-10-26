import base64,hashlib,json,pathlib,selectors,subprocess,sys,tempfile,time
from mcp import types
SERVER='''import asyncio\nfrom mcp.server.lowlevel import Server\nfrom mcp.server.lowlevel.helper_types import ReadResourceContents\nfrom mcp.server.stdio import stdio_server\nfrom mcp import types,McpError\ns=Server("resource-proof")\n@s.list_resource_templates()\nasync def templates():\n return [types.ResourceTemplate(name="bytes",uriTemplate="atlas-proof://bytes/{start}-{end}",mimeType="application/octet-stream")]\n@s.read_resource()\nasync def read(uri):\n u=str(uri)\n if "expired" in u:raise McpError(types.ErrorData(code=-32042,message="EXPIRED_REFERENCE",data={"reason":"retired"}))\n if "scope" in u:raise McpError(types.ErrorData(code=-32043,message="SCOPE_MISMATCH",data={"reason":"foreign"}))\n start,end=map(int,u.rsplit("/",1)[1].split("-"));original=b"A\\xc3\\xa9\\x00Z"\n return [ReadResourceContents(original[start:end],"application/octet-stream",meta={"start_byte":start,"end_byte":end,"total_bytes":len(original),"next_offset":end if end<len(original) else None})]\nasync def main():\n async with stdio_server() as (r,w):await s.run(r,w,s.create_initialization_options())\nasyncio.run(main())\n'''
root=pathlib.Path(tempfile.mkdtemp(prefix='aci-s13-resource-sdk-proof-'));server=root/'server.py';server.write_text(SERVER);results=[]
for version in ('2024-11-05','2025-06-18'):
 err=(root/(version+'.stderr.log')).open('wb');p=subprocess.Popen([sys.executable,'-B',str(server)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err);sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ);frames=[]
 def request(n,method,params=None):
  msg={'jsonrpc':'2.0','method':method}
  if n is not None:msg['id']=n
  if params is not None:msg['params']=params
  p.stdin.write((json.dumps(msg,separators=(',',':'))+'\n').encode());p.stdin.flush()
  if n is None:return None
  assert sel.select(10),'stdio response timeout'
  line=p.stdout.readline().rstrip(b'\n');assert line, 'server closed';frames.append(line);obj=json.loads(line);assert obj['id']==n;return obj,line
 try:
  initialized,_=request(1,'initialize',{'protocolVersion':version,'capabilities':{},'clientInfo':{'name':'proof','version':'1'}});assert initialized['result']['protocolVersion']==version;request(None,'notifications/initialized')
  listing,_=request(2,'resources/templates/list');assert listing['result']['resourceTemplates'][0]['uriTemplate']=='atlas-proof://bytes/{start}-{end}'
  reads=[]
  for n,(start,end) in enumerate(((0,5),(2,4),(5,5)),3):
   obj,line=request(n,'resources/read',{'uri':f'atlas-proof://bytes/{start}-{end}'})
   model=types.ReadResourceResult.model_validate(obj['result']);content=model.contents[0];assert isinstance(content,types.BlobResourceContents);assert base64.b64decode(content.blob)==b'A\xc3\xa9\x00Z'[start:end];assert content.meta=={'start_byte':start,'end_byte':end,'total_bytes':5,'next_offset':end if end<5 else None}
   marker=b'"result":';text=line[line.index(marker)+len(marker):].decode();value,stop=json.JSONDecoder().raw_decode(text);raw=text[:stop].encode();dump=model.model_dump_json(by_alias=True,exclude_none=True).encode();assert raw==dump
   reads.append({'span':[start,end],'decoded_hex':base64.b64decode(content.blob).hex(),'model_result_bytes':len(dump),'actual_result_bytes':len(raw),'jsonrpc_frame_bytes':len(line),'meta':content.meta})
  errors=[]
  for n,label in ((6,'expired'),(7,'scope')):
   obj,line=request(n,'resources/read',{'uri':'atlas-proof://'+label+'/0-1'});assert 'result' not in obj and 'isError' not in obj['error'];model=types.ErrorData.model_validate(obj['error']);assert model.code==(-32042 if label=='expired' else -32043);errors.append({'label':label,'error':obj['error'],'frame_bytes':len(line)})
  results.append({'protocol':version,'negotiated':initialized['result']['protocolVersion'],'reads':reads,'errors':errors})
 finally:
  p.stdin.close();p.wait(timeout=10);sel.close();err.close();assert p.returncode==0
 (root/(version+'.frames.jsonl')).write_bytes(b'\n'.join(frames)+b'\n')
record={'interpreter':sys.executable,'temporary_server':str(server),'server_sha256':hashlib.sha256(server.read_bytes()).hexdigest(),'results':results};(root/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({'scratch':str(root),'result_sha256':hashlib.sha256((root/'result.json').read_bytes()).hexdigest(),'record':record},indent=2))
