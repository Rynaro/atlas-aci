"""Actual CI-CWD gates on the held S18 runtime; no output masking."""
import json, os, subprocess, time
from pathlib import Path
root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
env={**os.environ,'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1',
     'PATH':'/tmp/aci-s01-tools/bin:'+os.environ['PATH']}
prior=json.loads((root/'.spectra/evidence/aci-s17/checks.json').read_text())
checks={key:(value.get('cwd') or '.',value['argv']) for key,value in prior.items()}
checks['eval-ruff']=('mcp-server',['.venv/bin/ruff','check','../atlas/evals/capture_context_baseline.py','../atlas/evals/measure_context_yield.py'])
checks['eval-format']=('.', ['mcp-server/.venv/bin/ruff','format','--check','--config','mcp-server/pyproject.toml','atlas/evals/capture_context_baseline.py','atlas/evals/measure_context_yield.py'])
results={}
for name,(cwd,argv) in checks.items():
 start=time.monotonic()
 with (out/(name+'.log')).open('wb') as log:r=subprocess.run(argv,cwd=root/cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
 results[name]={'argv':argv,'cwd':cwd,'exit_code':r.returncode,'elapsed_seconds':time.monotonic()-start,'log':name+'.log'}
 (out/'checks.json').write_text(json.dumps(results,indent=2)+'\n')
 print(name,r.returncode,flush=True)
raise SystemExit(any(v['exit_code'] for v in results.values()))
