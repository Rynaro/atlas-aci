"""Exact existing smoke steps, task-private paths/image names only substituted."""
import hashlib,json,os,subprocess,tempfile,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent
workflow=(ROOT/'.github/workflows/ci.yml').read_text();lines=workflow.splitlines();steps={}
for i,line in enumerate(lines):
 if not line.startswith('      - name: '):continue
 name=line.split('- name: ',1)[1];j=i+1
 while j<len(lines) and not lines[j].startswith('      - name: '):j+=1
 for k in range(i+1,j):
  if lines[k]=='        run: |':
   body=[]
   for value in lines[k+1:j]:
    if value.startswith('          '):body.append(value[10:])
    elif not value.strip():body.append('')
    else:break
   steps[name]='\n'.join(body)+'\n';break
names=['Build a tiny fixture repo and index it (writable mount)','Snapshot .atlas before serve','serve under --read-only :ro must start and stay alive (AC-H-16)','Confirm zero writes under .atlas (AC-H-16)','serve WITHOUT --memex-root override must still stay alive (regression guard)']
base=Path(tempfile.mkdtemp(prefix='aci-s08-ci-smoke-',dir='/tmp')).resolve();token=uuid.uuid4().hex[:12]
replacements={'atlas-aci-ci-smoketest':'atlas-aci-s08-footprint','/tmp/smoke-repo':str(base/'repo'),'/tmp/atlas-inventory.py':str(base/'inventory.py'),'/tmp/atlas-before.json':str(base/'before.json'),'/tmp/atlas-after.json':str(base/'after.json'),'/tmp/mcp-in':str(base/'mcp-in'),'/tmp/serve-nomemex.log':str(base/'serve-nomemex.log'),'/tmp/serve.log':str(base/'serve.log'),'atlas-memex-ci':'aci-s08-ci-memex-'+token,'atlas-serve-smoketest-nomemex':'aci-s08-ci-nomemex-'+token,'atlas-serve-smoketest':'aci-s08-ci-explicit-'+token}
receipt=json.loads((ROOT/'.spectra/state/aci-s08.execution.json').read_text());sources=receipt['final_validation']['runtime_sources'];assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in sources.items())
result={'workflow_sha256':hashlib.sha256((ROOT/'.github/workflows/ci.yml').read_bytes()).hexdigest(),'replacements':replacements,'fixture_root':str(base),'runtime_sources':sources,'steps':[],'image':'atlas-aci-s08-footprint'}
def save(): (HERE/'basic-smoke-mirror.json').write_text(json.dumps(result,indent=2)+'\n')
try:
 for index,name in enumerate(names):
  script=steps[name]
  for old,new in replacements.items():script=script.replace(old,new)
  target=HERE/f'step-{index+1}.sh';target.write_text(script)
  start=time.monotonic();process=subprocess.run(['/bin/bash',str(target)],cwd=ROOT,capture_output=True,text=True,timeout=180)
  row={'name':name,'command':['/bin/bash',str(target)],'script_sha256':hashlib.sha256(script.encode()).hexdigest(),'exit_code':process.returncode,'elapsed_seconds':time.monotonic()-start,'stdout':process.stdout,'stderr':process.stderr};result['steps'].append(row);save();print(name,process.returncode,flush=True)
  if process.returncode:raise RuntimeError('Actual smoke step failed: '+name)
 before=json.loads((base/'before.json').read_text());after=json.loads((base/'after.json').read_text());assert before==after
 private=[r for r in before if r['path']=='source-snapshots'];assert len(private)==1 and private[0]['mode']&0o777==0o700 and private[0]['uid']==10001
 manifests=[r for r in before if r['path'].startswith('source-snapshots/manifest.')];assert manifests and all(r['mode']&0o777==0o600 and r['uid']==10001 for r in manifests)
 result.update(before_inventory=before,after_inventory=after,complete_inventory_equal=True,private_snapshot_modes_unchanged=True,passed=True);save()
finally:
 result['cleanup']=[]
 for name in (replacements['atlas-serve-smoketest'],replacements['atlas-serve-smoketest-nomemex']):
  process=subprocess.run(['docker','rm','-f',name],capture_output=True,text=True,timeout=30);result['cleanup'].append({'container':name,'exit_code':process.returncode,'stdout':process.stdout,'stderr':process.stderr})
 process=subprocess.run(['docker','volume','rm',replacements['atlas-memex-ci']],capture_output=True,text=True,timeout=30);result['cleanup'].append({'volume':replacements['atlas-memex-ci'],'exit_code':process.returncode,'stdout':process.stdout,'stderr':process.stderr});save()
