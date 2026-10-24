"""ACI-S07's authorized serial local CPU experiment; 600s/job, 2700s total."""
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
INPUTS = Path('/tmp/aci-s07-probe-inputs')
UV = '/tmp/aci-s01-tools/bin/uv'
PINS = {'solidus': ('https://github.com/solidusio/solidus.git', '4026945d614e81383c007ed1ab1278a0195ce5d9'), 'spree': ('https://github.com/spree/spree.git', '6699cde44303ea85ef6e56c5e87c44a738ab73fc')}
START = time.monotonic()
ENV = {**os.environ, 'UV_CACHE_DIR': '/tmp/aci-s01-uv-cache'}
ENV['UV_OFFLINE']='1'
ENV['PYTHONDONTWRITEBYTECODE']='1'
ENV['PYTHONPATH']=str(ROOT/'mcp-server/src')
MODULES={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'mcp-server/src/atlas_aci/storage/evidence.py',ROOT/'mcp-server/src/atlas_aci/providers/python_imports.py']}
SOURCE = hashlib.sha256((ROOT/'mcp-server/src/atlas_aci/codegraph.py').read_bytes()).hexdigest()
(EVIDENCE/'environment.json').write_text(json.dumps({'python':sys.version, 'source_sha256':SOURCE, 'module_sha256s':MODULES, 'base_commit':'ad9ab7a6122f653b0e9e7052e6173c6257b4a3b8', 'uv':UV, 'cache':ENV['UV_CACHE_DIR'], 'pins':PINS, 'networkx':'3.6.1', 'seconds_per_job':600, 'seconds_total':2700}, indent=2)+'\n')


def job(name, command, cwd):
    assert hashlib.sha256((ROOT/'mcp-server/src/atlas_aci/codegraph.py').read_bytes()).hexdigest() == SOURCE
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in MODULES.items())
    remaining = 2700-(time.monotonic()-START)
    if remaining <= 0:
        raise RuntimeError('Authorized total probe timebox exhausted')
    started = time.monotonic()
    timed_out = False
    stdout = EVIDENCE/(name+'.stdout.log')
    stderr = EVIDENCE/(name+'.stderr.log')
    with stdout.open('w') as out, stderr.open('w') as err:
        proc = subprocess.Popen(['/usr/bin/time','-l',*command],cwd=cwd,env=ENV,stdout=out,stderr=err,start_new_session=True)
        try:
            code = proc.wait(timeout=min(600,remaining))
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid,signal.SIGTERM)
            try:
                code=proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL)
                code=proc.wait()
    memory = re.search(r'(\d+)\s+maximum resident set size',stderr.read_text())
    result={'name':name,'command':command,'cwd':str(cwd),'exit_code':code,'timed_out':timed_out,'duration_seconds':time.monotonic()-started,'peak_memory_bytes':int(memory.group(1)) if memory else None,'stdout':str(stdout.relative_to(ROOT)),'stderr':str(stderr.relative_to(ROOT)),'source_sha256':SOURCE}
    (EVIDENCE/(name+'.job.json')).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
    if code or timed_out:
        raise RuntimeError('Probe job failed: '+name)
    return stdout


INPUTS.mkdir(exist_ok=True)
for name,(url,pin) in PINS.items():
    repo=INPUTS/name
    if repo.exists() and (repo/'.atlas').exists():
        raise RuntimeError('Refusing to reuse measured input '+str(repo))
    # Disposable checkout from the unchanged cached public pinned object store.
    # No old source/index/evidence directory is reused as the measured checkout.
    cache=Path('/tmp/aci-s02-probe-inputs')/name
    if not repo.exists():
        job(name+'-clone',['git','clone','--no-checkout','--no-local',str(cache),str(repo)],INPUTS)
    job(name+'-hooks',['git','config','core.hooksPath','/dev/null'],repo)
    job(name+'-remote',['git','remote','set-url','origin',url],repo)
    if name=='spree':
        job(name+'-sparse',['git','config','core.sparseCheckout','true'],repo)
        (repo/'.git/info/sparse-checkout').write_text('spree/*\n')
    job(name+'-checkout',['git','checkout','--detach',pin],repo)
    head=job(name+'-head',['git','rev-parse','HEAD'],repo).read_text().strip()
    status=job(name+'-status',['git','status','--porcelain','--untracked-files=no'],repo).read_text()
    assert head==pin and not status
    graph=Path('/tmp/aci-s07-'+name+'-graph.json')
    job(name+'-export',[UV,'run','--frozen','python','../scripts/probe-export-confident-graph.py',str(repo),str(graph)],ROOT/'mcp-server')
    if name=='spree':
        output=Path('/tmp/aci-s07-spree-export.jsonl')
        job(name+'-jsonl',[UV,'run','--frozen','python','-m','atlas_aci','export','--repo',str(repo),str(output)],ROOT/'mcp-server')
        digest=hashlib.file_digest(output.open('rb'),'sha256').hexdigest()
        (EVIDENCE/'spree-current-format-size.json').write_text(json.dumps({'path':str(output),'bytes':output.stat().st_size,'sha256':digest},indent=2)+'\n')
    phase2=job(name+'-modularity',[UV,'run','--no-project','--with','networkx==3.6.1','python','scripts/probe-modularity.py',str(graph)],ROOT)
    values=json.loads(phase2.read_text())
    passed=values['lpa_q']>=0.30 and values['lpa_q']>=0.85*values['louvain_median']
    print(json.dumps({'repo':name,'nodes':values['node_count'],'edges':values['edge_count'],'lpa_q':values['lpa_q'],'louvain_median':values['louvain_median'],'ratio':values['lpa_q']/values['louvain_median'],'verdict':'PASS' if passed else 'CUT'}),flush=True)
print('MEASUREMENTS_COMPLETE',flush=True)
