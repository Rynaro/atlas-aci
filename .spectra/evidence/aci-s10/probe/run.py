"""Fresh same-pin/default producer S10 probe; serial600s jobs/2700s total."""
import hashlib,json,os,re,signal,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent
INPUTS=Path('/tmp/aci-s10-fresh-probe-inputs');UV='/tmp/aci-s01-tools/bin/uv';START=time.monotonic()
ENV={**os.environ,'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT/'mcp-server/src')}
SOURCES={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'mcp-server/src/atlas_aci/codegraph.py',ROOT/'mcp-server/src/atlas_aci/storage/evidence.py',ROOT/'mcp-server/src/atlas_aci/providers/python_imports.py',ROOT/'mcp-server/src/atlas_aci/indexing/extraction_cache.py',ROOT/'mcp-server/src/atlas_aci/resolvers/dependencies.py']}
assert SOURCES['mcp-server/src/atlas_aci/codegraph.py']=='669289a204050dea520397497cf740a6b238628044e3d499b0cf90cf14592ef9'
PINS={'solidus':('https://github.com/solidusio/solidus.git','4026945d614e81383c007ed1ab1278a0195ce5d9'),'spree':('https://github.com/spree/spree.git','6699cde44303ea85ef6e56c5e87c44a738ab73fc')}
(OUT/'environment.json').write_text(json.dumps({'sources':SOURCES,'pins':PINS,'default_profile':True,'source_transformations':False,'networkx':'3.6.1','per_job_seconds':600,'total_seconds':2700,'prior_probe_seconds_charged':0.0,'base_commit':'ea9afb9e2636f581e1c1cffd9b18e50ae9d891c3'},indent=2)+'\n')

def job(name,command,cwd=ROOT):
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in SOURCES.items())
    remaining=2700-0.0-(time.monotonic()-START)
    if remaining<=0:raise RuntimeError('Total authorized probe time exhausted')
    begun=time.monotonic();timed_out=False
    stdout=OUT/(name+'.stdout.log');stderr=OUT/(name+'.stderr.log')
    with stdout.open('w') as out,stderr.open('w') as err:
        proc=subprocess.Popen(['/usr/bin/time','-l',*map(str,command)],cwd=cwd,env=ENV,stdout=out,stderr=err,start_new_session=True)
        print(json.dumps({'event':'started','name':name,'pid':proc.pid,'elapsed_total':time.monotonic()-START}),flush=True)
        try:code=proc.wait(timeout=min(600,remaining))
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(proc.pid,signal.SIGTERM)
            try:code=proc.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);code=proc.wait()
    memory=re.search(r'(\d+)\s+maximum resident set size',stderr.read_text())
    result={'name':name,'command':list(map(str,command)),'cwd':str(cwd),'exit_code':code,'timed_out':timed_out,'elapsed_seconds':time.monotonic()-begun,'elapsed_total':time.monotonic()-START,'peak_rss_bytes':int(memory.group(1)) if memory else None,'stdout':str(stdout.relative_to(ROOT)),'stderr':str(stderr.relative_to(ROOT))}
    (OUT/(name+'.job.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
    if code or timed_out:raise RuntimeError('Actual probe job failed: '+name)
    return stdout

INPUTS.mkdir(exist_ok=True)
for name,(url,pin) in PINS.items():
    repo=INPUTS/name
    if repo.exists():raise RuntimeError('Refuse to overwrite existing measured input '+str(repo))
    job(name+'-clone',['git','clone','--no-checkout','--no-local',Path('/tmp/aci-s02-probe-inputs')/name,repo])
    job(name+'-hooks',['git','config','core.hooksPath','/dev/null'],repo)
    job(name+'-origin',['git','remote','set-url','origin',url],repo)
    if name=='spree':
        job(name+'-sparse',['git','config','core.sparseCheckout','true'],repo)
        (repo/'.git/info/sparse-checkout').write_text('spree/*\n')
    job(name+'-checkout',['git','-c','core.hooksPath=/dev/null','checkout','--detach',pin],repo)
    assert job(name+'-head',['git','rev-parse','HEAD'],repo).read_text().strip()==pin
    assert not job(name+'-clean',['git','status','--porcelain','--untracked-files=no'],repo).read_text().strip()
    graph=Path('/tmp/aci-s10-fresh-'+name+'-graph.json')
    job(name+'-export',[UV,'run','--frozen','python','../scripts/probe-export-confident-graph.py',repo,graph],ROOT/'mcp-server')
    assert not job(name+'-clean-after',['git','status','--porcelain','--untracked-files=no'],repo).read_text().strip()
    job(name+'-account',[ROOT/'mcp-server/.venv/bin/python',OUT/'account.py',name,repo,OUT])
    phase2=job(name+'-modularity',[UV,'run','--no-project','--with','networkx==3.6.1','python','scripts/probe-modularity.py',graph])
    values=json.loads(phase2.read_text());passed=values['lpa_q']>=.30 and values['lpa_q']>=.85*values['louvain_median']
    print(json.dumps({'repo':name,'nodes':values['node_count'],'edges':values['edge_count'],'lpa_q':values['lpa_q'],'louvain_median':values['louvain_median'],'verdict':'PASS' if passed else 'CUT'}),flush=True)
print('FRESH_MEASUREMENTS_COMPLETE',flush=True)
