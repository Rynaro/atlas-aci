from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl
import json
for res in ("https://atlas.example.com/mcp/repo-a","https://atlas.example.com:8443/mcp/repo-a","https://ATLAS.example.com/mcp/repo-a"):
    m = ProtectedResourceMetadata(resource=AnyHttpUrl(res), authorization_servers=[AnyHttpUrl("https://idp.example.com")], scopes_supported=["atlas.read"])
    print(m.model_dump_json(exclude_none=True))
m = ProtectedResourceMetadata(resource=AnyHttpUrl("https://atlas.example.com/mcp/repo-a"), authorization_servers=[AnyHttpUrl("https://idp.example.com/a/b")])
print(m.model_dump_json(exclude_none=True))
print(list(ProtectedResourceMetadata.model_fields))
from mcp.server.auth.routes import build_resource_metadata_url
print(build_resource_metadata_url(AnyHttpUrl("https://atlas.example.com/mcp/repo-a")))
