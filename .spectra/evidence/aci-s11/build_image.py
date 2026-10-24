"""Build only this task's production image and record the actual outcome."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
command = ['docker', 'build', '-t', 'atlas-aci-s11', '-f', 'mcp-server/Dockerfile', 'mcp-server']
started = time.monotonic()
with (out/'docker-build.log').open('wb') as log:
    result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
record = {'command':command,'cwd':'.','exit_code':result.returncode,'elapsed_seconds':time.monotonic()-started,
          'source_checkpoint':'.spectra/evidence/aci-s11/source-v1-corrected-checkpoint.json',
          'dockerfile_sha256':hashlib.sha256((root/'mcp-server/Dockerfile').read_bytes()).hexdigest()}
if result.returncode == 0:
    record['image_id'] = subprocess.check_output(['docker','image','inspect','atlas-aci-s11','--format','{{.Id}}'],text=True).strip()
(out/'docker-build.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
raise SystemExit(result.returncode)
