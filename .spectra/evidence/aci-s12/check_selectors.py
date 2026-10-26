"""Exact selector wiring and current-producer validation, no guard rewrites."""
import json,os,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
base='69fe066f573f29651fb53ad0dac65ae13836b752'
env={**os.environ,'PATH':'/tmp/aci-s01-tools/bin:'+os.environ['PATH'],'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1'}
commands={
 'shell-syntax':['/bin/bash','-n','scripts/test-verify-probe-verdict.sh'],
 'resolve-md':['scripts/resolve-probe-artifact.sh','aci-s12-probe-lpa-vs-louvain.md'],
 'resolve-json':['scripts/resolve-probe-artifact.sh','aci-s12-probe-lpa-vs-louvain.json'],
 'immutable-inputs':['git','diff','--exit-code',base,'--','scripts/verify-probe-verdict.py','scripts/resolve-probe-artifact.sh','scripts/probe-export-confident-graph.py','scripts/probe-modularity.py','scripts/probe-assemble-graph-bundle.py','.spectra/changes/aci-s11','.spectra/changes/aci-s08','.spectra/changes/archive'],
 'current-probe':['mcp-server/.venv/bin/python','scripts/verify-probe-verdict.py','.spectra/changes/aci-s12/probe/aci-s12-probe-lpa-vs-louvain.json'],
}
results={}
for name,command in commands.items():
 start=time.monotonic();p=subprocess.run(command,cwd=root,env=env,capture_output=True,timeout=300)
 (out/('selector-'+name+'.log')).write_bytes(p.stdout+p.stderr)
 results[name]={'command':command,'exit_code':p.returncode,'elapsed_seconds':time.monotonic()-start,'log':'.spectra/evidence/aci-s12/selector-'+name+'.log'}
 assert p.returncode==0,(name,p.stderr)
 if name.startswith('resolve-'):
  ext='md' if name.endswith('md') else 'json'
  assert p.stdout.decode().strip()=='.spectra/changes/aci-s12/probe/aci-s12-probe-lpa-vs-louvain.'+ext
 if name=='current-probe':assert b'computed_verdict=PASS recorded_verdict=PASS' in p.stdout
for name,count in [('.github/workflows/harden-gate.yml',3),('scripts/test-verify-probe-verdict.sh',1)]:
 current=(root/name).read_bytes();baseline=subprocess.check_output(['git','show',base+':'+name],cwd=root)
 assert current.count(b'aci-s12-probe-lpa-vs-louvain')==count
 assert current.replace(b'aci-s12-probe-lpa-vs-louvain',b'aci-s11-probe-lpa-vs-louvain')==baseline
results['reverse-exact-baseline']={'exit_code':0,'workflow_replacements':3,'selftest_replacements':1,'whole_files_equal_baseline_after_reversal':True}
assert (out/'guard-final.log').read_text().count('\nPASS:')+int((out/'guard-final.log').read_text().startswith('PASS:'))==27
assert '27 passed, 0 failed' in (out/'guard-final.log').read_text()
(out/'selector-checks.json').write_text(json.dumps(results,indent=2)+'\n')
checks=json.loads((out/'checks-preprobe.json').read_text());checks['probe']=results['current-probe'];checks['guard']={'command':['env','-u','PYTHONPATH','/bin/bash','scripts/test-verify-probe-verdict.sh'],'exit_code':0,'log':'.spectra/evidence/aci-s12/guard-final.log','actual_controls_passed':27,'final_marker':'27 passed, 0 failed'}
assert len(checks)==11 and all(r['exit_code']==0 for r in checks.values())
(out/'checks.json').write_text(json.dumps(checks,indent=2)+'\n')
print('SELECTOR_CHECKS_ALL_PASS; ACTUAL27_CONTROLS_PASS; ALL11_CHECKS_ZERO')
