"""Direct return codes for frozen static and unaffected guard commands."""
import json,os,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
env={**os.environ,'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1','PATH':'/tmp/aci-s01-tools/bin:'+os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'}
env.pop('PYTHONPATH',None)
checks={
 'ruff':('mcp-server',['.venv/bin/ruff','check','src/','tests/']),
 'format':('mcp-server',['.venv/bin/ruff','format','--check','src/','tests/']),
 'mypy':('mcp-server',['.venv/bin/mypy','src/']),
 'scripts-ruff':('mcp-server',['.venv/bin/ruff','check','../scripts/','--exclude','fingerprint-fixture']),
 'scripts-format':('mcp-server',['.venv/bin/ruff','format','--check','../scripts/','--exclude','fingerprint-fixture']),
 'scripts-mypy':('mcp-server',['.venv/bin/mypy','../scripts/']),
 'lock':('mcp-server',['/tmp/aci-s01-tools/bin/uv','lock','--check']),
 'size-guard':('.',['/bin/bash','scripts/test-verify-export-size.sh']),
 'classify':('.',['/bin/bash','scripts/test-harden-gate-classify.sh'])}
results={}
for name,(cwd,argv) in checks.items():
 start=time.monotonic()
 with (out/(name+'.log')).open('wb') as log:
  p=subprocess.run(argv,cwd=root/cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
 results[name]={'argv':argv,'cwd':cwd,'exit_code':p.returncode,'elapsed_seconds':time.monotonic()-start,'log':name+'.log'}
 (out/'checks-preprobe.json').write_text(json.dumps(results,indent=2)+'\n')
 print(name,p.returncode,flush=True)
raise SystemExit(any(v['exit_code'] for v in results.values()))
