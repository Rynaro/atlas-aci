"""Execute exact repository CI checks and retain direct process exits."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
previous = json.loads((ROOT / '.spectra/evidence/aci-s15/checks.json').read_text())
env = os.environ.copy()
env.update(UV_CACHE_DIR='/tmp/aci-s01-uv-cache', UV_OFFLINE='1',
           PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT / 'mcp-server/src'))
env['PATH'] = '/tmp/aci-s01-tools/bin:' + env['PATH']
results = {}
for name, definition in previous.items():
    command = definition['command']
    cwd = ROOT / definition['cwd']
    selected_env = env.copy()
    if name == 'guard':
        selected_env.pop('PYTHONPATH', None)
    with (OUT / (name + '.log')).open('wb') as output:
        result = subprocess.run(command, cwd=cwd, env=selected_env, stdout=output,
                                stderr=subprocess.STDOUT, timeout=180)
    results[name] = {'command':command,'cwd':definition['cwd'],
                     'exit_code':result.returncode}
    print(name,result.returncode,flush=True)
    (OUT / 'checks.json').write_text(json.dumps(results,indent=2)+'\n')
raise SystemExit(any(item['exit_code'] for item in results.values()))
