"""One frozen full run with every required real native integration enabled."""
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
image = 'atlas-aci:s13-final'
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
for name in ('S06', 'S08', 'S15', 'S11', 'S12', 'S13'):
    env['ATLAS_ACI_' + name + '_DOCKER_IMAGE'] = image
    env['ATLAS_ACI_' + name + '_EVIDENCE_DIR'] = str(out / 'docker-final' / name.lower())
env['ATLAS_ACI_S13_NATIVE_RECORD'] = str(out / 'native-full.json')
command = ['.venv/bin/python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/']
start = time.monotonic()
with (out / 'full-suite.log').open('wb') as log:
    result = subprocess.run(command, cwd=root / 'mcp-server', env=env, stdout=log, stderr=subprocess.STDOUT)
record = {'argv': command, 'cwd': 'mcp-server', 'exit_code': result.returncode,
          'elapsed_seconds': time.monotonic() - start,
          'native_environment': {k: v for k, v in env.items() if k.startswith('ATLAS_ACI_')}}
(out / 'full-suite.job.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record), flush=True)
raise SystemExit(result.returncode)
