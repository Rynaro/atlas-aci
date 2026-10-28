"""Protected real-argv two-pass execution, with final native image enabled."""
import json,os,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
image=(out/'docker-image-id.txt').read_text().strip()
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root/'mcp-server/src'),
 'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1','ATLAS_ACI_S18_DOCKER_IMAGE':image,
 'ATLAS_ACI_S18_NATIVE_RECORD':str(out/'native-pass2.json')}
command=['/Users/henrique/.local/bin/eidolons','sandbox','loop','--allow-unsafe-host','--base','ae307760ec928572da406e174bfe922848faabaf','--tests','mcp-server/.venv/bin/python -m pytest -q -p no:cacheprovider mcp-server/tests/slices/test_s17_*.py','--lint-hook','cd mcp-server && .venv/bin/ruff check src/ tests/','--protect','mcp-server/tests/slices/test_s17_*.py','--k','2','--max-attempts','1','--out','/tmp/aci-s18-pass2-final','--json']
start=time.monotonic()
with (out/'vivi-pass2-final.stdout.log').open('wb') as stdout,(out/'vivi-pass2-final.stderr.log').open('wb') as stderr:
 result=subprocess.run(command,cwd=root,env=env,stdout=stdout,stderr=stderr)
record={'argv':command,'exit_code':result.returncode,'elapsed_seconds':time.monotonic()-start,'image_id':image,'base_red_evidence':'baseline-checkpoint.json'}
(out/'vivi-pass2-final.job.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
raise SystemExit(result.returncode)
