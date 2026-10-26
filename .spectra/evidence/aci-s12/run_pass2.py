"""Protected two-pass verification of the exact frozen S12 anchors."""
import json,os,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
image=json.loads((out/'docker-build.json').read_text())['image_id']
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root/'mcp-server/src'),'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1','ATLAS_ACI_S12_DOCKER_IMAGE':image,'ATLAS_ACI_S12_EVIDENCE_DIR':str(out/'pass2-docker')}
command=['/Users/henrique/.local/bin/eidolons','sandbox','loop','--allow-unsafe-host','--base','69fe066f573f29651fb53ad0dac65ae13836b752','--tests','cd mcp-server && .venv/bin/python -m pytest -q -p no:cacheprovider tests/slices/test_s12_*.py','--lint-hook','cd mcp-server && .venv/bin/ruff check src/ tests/','--protect','mcp-server/tests/slices/test_s12_*.py','--k','2','--max-attempts','1','--out','/tmp/aci-s12-pass2','--json']
start=time.monotonic()
with (out/'vivi-pass2.stdout.log').open('wb') as stdout,(out/'vivi-pass2.stderr.log').open('wb') as stderr:
 result=subprocess.run(command,cwd=root,env=env,stdout=stdout,stderr=stderr)
record={'command':command,'exit_code':result.returncode,'elapsed_seconds':time.monotonic()-start,'source_checkpoint':'.spectra/evidence/aci-s12/source-final.json','base_red_evidence':'.spectra/evidence/aci-s12/baseline-checkpoint.json'}
(out/'vivi-pass2.job.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));raise SystemExit(result.returncode)
