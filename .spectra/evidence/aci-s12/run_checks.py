"""Run unchanged applicable CI checks; current producer probe/27 guards follow fresh approved selectors."""
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[3]
evidence = Path(__file__).resolve().parent
env = {**os.environ, 'PATH': '/tmp/aci-s01-tools/bin:' + os.environ['PATH'],
       'UV_CACHE_DIR': '/tmp/aci-s01-uv-cache', 'UV_OFFLINE': '1',
       'PYTHONDONTWRITEBYTECODE': '1'}
checks = [
 ('ruff', 'mcp-server', ['.venv/bin/ruff','check','src/','tests/']),
 ('format', 'mcp-server', ['.venv/bin/ruff','format','--check','src/','tests/']),
 ('mypy', 'mcp-server', ['.venv/bin/mypy','src/']),
 ('scripts-ruff', 'mcp-server', ['.venv/bin/ruff','check','../scripts/','--exclude','fingerprint-fixture']),
 ('scripts-format', 'mcp-server', ['.venv/bin/ruff','format','--check','../scripts/','--exclude','fingerprint-fixture']),
 ('scripts-mypy', 'mcp-server', ['.venv/bin/mypy','../scripts/']),
 ('lock', 'mcp-server', ['/tmp/aci-s01-tools/bin/uv','lock','--check']),
 ('size-guard', '.', ['/bin/bash','scripts/test-verify-export-size.sh']),
 ('classify', '.', ['/bin/bash','scripts/test-harden-gate-classify.sh']),
]
records = {}
for name,cwd,command in checks:
    start=time.monotonic()
    with (evidence/(name+'.log')).open('wb') as output:
        result=subprocess.run(command,cwd=root/cwd,env=env,stdout=output,stderr=subprocess.STDOUT)
    records[name]={'command':command,'cwd':cwd,'exit_code':result.returncode,'elapsed_seconds':time.monotonic()-start,'log':str((evidence/(name+'.log')).relative_to(root))}
    (evidence/'checks-preprobe.json').write_text(json.dumps(records,indent=2)+'\n')
    print(name,result.returncode,flush=True)
raise SystemExit(any(r['exit_code'] for r in records.values()))
