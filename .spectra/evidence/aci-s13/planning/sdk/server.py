import asyncio
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp import types,McpError
s=Server("resource-proof")
@s.list_resource_templates()
async def templates():
 return [types.ResourceTemplate(name="bytes",uriTemplate="atlas-proof://bytes/{start}-{end}",mimeType="application/octet-stream")]
@s.read_resource()
async def read(uri):
 u=str(uri)
 if "expired" in u:raise McpError(types.ErrorData(code=-32042,message="EXPIRED_REFERENCE",data={"reason":"retired"}))
 if "scope" in u:raise McpError(types.ErrorData(code=-32043,message="SCOPE_MISMATCH",data={"reason":"foreign"}))
 start,end=map(int,u.rsplit("/",1)[1].split("-"));original=b"A\xc3\xa9\x00Z"
 return [ReadResourceContents(original[start:end],"application/octet-stream",meta={"start_byte":start,"end_byte":end,"total_bytes":len(original),"next_offset":end if end<len(original) else None})]
async def main():
 async with stdio_server() as (r,w):await s.run(r,w,s.create_initialization_options())
asyncio.run(main())
