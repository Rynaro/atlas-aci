"""Verify only authorized exact selectors and the unchanged probe controls."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

root=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent/'selector-final'
out.mkdir(exist_ok=True)
env={**os.environ,'PATH':'/tmp/aci-s01-tools/bin:'+os.environ['PATH'],
     'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1'}
rows={}
for name,command in [
 ('verifier',['mcp-server/.venv/bin/python','scripts/verify-probe-verdict.py','.spectra/changes/aci-s11/probe/aci-s11-probe-lpa-vs-louvain.json']),
 ('guard',['env','-u','PYTHONPATH','/bin/bash','scripts/test-verify-probe-verdict.sh']),
 ('resolve-json',['/bin/bash','scripts/resolve-probe-artifact.sh','aci-s11-probe-lpa-vs-louvain.json']),
 ('resolve-md',['/bin/bash','scripts/resolve-probe-artifact.sh','aci-s11-probe-lpa-vs-louvain.md']),
 ('bash-syntax',['/bin/bash','-n','scripts/test-verify-probe-verdict.sh']),
]:
 started=time.monotonic()
 with (out/(name+'.log')).open('wb') as log:
  process=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
 rows[name]={'command':command,'exit_code':process.returncode,'elapsed_seconds':time.monotonic()-started,'log':str((out/(name+'.log')).relative_to(root))}
 (out/'checks.json').write_text(json.dumps(rows,indent=2)+'\n')
 assert process.returncode==0,(name,process.returncode)
 if name=='guard':
  text=(out/(name+'.log')).read_text();assert '27 passed, 0 failed' in text and len(re.findall(r'^PASS:',text,re.M))==27
proof=[]
for name,count in [('.github/workflows/harden-gate.yml',3),('scripts/test-verify-probe-verdict.sh',1)]:
 before=subprocess.check_output(['git','show','138a6f3cd510e0ecb3be0832221236adfe441ebc:'+name],cwd=root)
 after=(root/name).read_bytes();old=b'aci-s08-refined-probe-lpa-vs-louvain';new=b'aci-s11-probe-lpa-vs-louvain'
 assert after.count(new)==count and after.replace(new,old)==before
 proof.append({'path':name,'exact_replacements':count,'base_sha256':hashlib.sha256(before).hexdigest(),'current_sha256':hashlib.sha256(after).hexdigest(),'reverse_equals_base':True})
(out/'exact-selector-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps({'exit_code':0,'controls_passed':27,'exact_selector_files':2}))
