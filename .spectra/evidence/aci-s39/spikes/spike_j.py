import sys, json, subprocess
code = '''
import sys, json
std = set(sys.stdlib_module_names)
def third(): return sorted({m.split(".")[0] for m in sys.modules} - std - {"atlas_aci"} - {m for m in sys.modules if m.startswith("_")})
import atlas_aci.__main__
a = third(); flags = {k: any(m == k or m.startswith(k + ".") for m in sys.modules) for k in ("atlas_aci.protocol.http","uvicorn","starlette","sse_starlette","atlas_aci.server","mcp")}
import atlas_aci.server
b = third(); flags2 = {k: any(m == k or m.startswith(k + ".") for m in sys.modules) for k in ("atlas_aci.protocol.http","uvicorn","starlette","sse_starlette","atlas_aci.server","mcp")}
print(json.dumps({"after_main": a, "flags_after_main": flags, "after_server": b, "flags_after_server": flags2}))
'''
print(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout)
