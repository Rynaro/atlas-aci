"""Frozen complete regression suite with all required native image gates enabled."""
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
image = json.loads((out/'docker-build.json').read_text())['image_id']
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'}
for slice_id in ('S06','S08','S15','S11','S12'):
    env['ATLAS_ACI_'+slice_id+'_DOCKER_IMAGE'] = image
    env['ATLAS_ACI_'+slice_id+'_EVIDENCE_DIR'] = str(out/'docker-final'/slice_id.lower())
command = ['.venv/bin/python','-m','pytest','-q','-p','no:cacheprovider']
start = time.monotonic()
with (out/'full-suite-corrected.log').open('wb') as log:
    result = subprocess.run(command,cwd=root/'mcp-server',env=env,stdout=log,stderr=subprocess.STDOUT)
record = {'command':command,'cwd':'mcp-server','exit_code':result.returncode,
          'elapsed_seconds':time.monotonic()-start,'image_id':image,
          'native_environment':{k:v for k,v in env.items() if k.startswith('ATLAS_ACI_')},
          'source_checkpoint':'.spectra/evidence/aci-s12/source-final.json'}
(out/'full-suite-corrected.job.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
raise SystemExit(result.returncode)
