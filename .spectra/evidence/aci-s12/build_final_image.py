"""Build the frozen native-schema checkpoint image for real Linux qualification."""
import json
from pathlib import Path
import subprocess
import time

root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
command=['docker','build','-t','atlas-aci-s12-final','-f','mcp-server/Dockerfile','mcp-server']
start=time.monotonic()
with (out/'docker-build.log').open('wb') as log:
 result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
r={'command':command,'exit_code':result.returncode,'elapsed_seconds':time.monotonic()-start,'source_checkpoint':'.spectra/evidence/aci-s12/source-final.json'}
if result.returncode==0:r['image_id']=subprocess.check_output(['docker','image','inspect','atlas-aci-s12-final','--format','{{.Id}}'],text=True).strip()
(out/'docker-build.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(result.returncode)
