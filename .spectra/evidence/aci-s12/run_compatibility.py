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
command = ['.venv/bin/python','-m','pytest','-q','-p','no:cacheprovider', 'tests/slices/test_s07_contract.py::test_migrate_validates_current_path_even_with_cached_legacy_handle', 'tests/slices/test_s08_integration.py::test_coherently_rehashed_foreign_binding_fails_before_exposure', 'tests/test_export.py::test_canonical_jsonl_shape', 'tests/test_export.py::test_header_record_epoch_and_content_hash', 'tests/slices/test_s11_docker.py', 'tests/slices/test_s12_docker.py']
start = time.monotonic()
with (out/'compatibility-focused.log').open('wb') as log:
    result = subprocess.run(command,cwd=root/'mcp-server',env=env,stdout=log,stderr=subprocess.STDOUT)
record = {'command':command,'cwd':'mcp-server','exit_code':result.returncode,
          'elapsed_seconds':time.monotonic()-start,'image_id':image,
          'native_environment':{k:v for k,v in env.items() if k.startswith('ATLAS_ACI_')},
          'source_checkpoint':'.spectra/evidence/aci-s12/source-final.json'}
(out/'compatibility-focused.job.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
raise SystemExit(result.returncode)
