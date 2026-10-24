"""Bounded scratch-only jobs; all compiler/parser outputs stay disposable."""
import hashlib,json,os,signal,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent;TMP=Path('/tmp/aci-s07-scss-qualification');GRAMMAR=TMP/'grammar';START=time.monotonic()
ENV={**os.environ,'PYTHONPATH':str(ROOT/'mcp-server/src'),'PYTHONDONTWRITEBYTECODE':'1'}
SOURCE=hashlib.sha256((ROOT/'mcp-server/src/atlas_aci/codegraph.py').read_bytes()).hexdigest()
assert SOURCE=='a7696a05a6880bd378e1fa94498ffd579504d9feabb1a1c9935a1c8d1a71a0b9'
records=[]
def job(name,args,cwd=ROOT):
    name='resume2-'+name
    assert hashlib.sha256((ROOT/'mcp-server/src/atlas_aci/codegraph.py').read_bytes()).hexdigest()==SOURCE
    remaining=1200-(time.time()-(OUT/'initial-run.stdout.log').stat().st_mtime)
    if remaining<=0:raise RuntimeError('20 minute total qualification cap')
    begun=time.monotonic()
    with (OUT/(name+'.stdout.log')).open('w') as out,(OUT/(name+'.stderr.log')).open('w') as err:
        process=subprocess.Popen(['/usr/bin/time','-l',*map(str,args)],cwd=cwd,env=ENV,stdout=out,stderr=err,start_new_session=True)
        try:code=process.wait(timeout=min(600,remaining))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);code=process.wait();raise RuntimeError('qualification deadline '+name)
    record={'name':name,'command':list(map(str,args)),'cwd':str(cwd),'exit_code':code,'elapsed_seconds':time.monotonic()-begun}
    records.append(record);(OUT/'jobs.json').write_text(json.dumps(records,indent=2)+'\n');print(json.dumps(record),flush=True)
    if code:raise RuntimeError('qualification failed '+name)
    return (OUT/(name+'.stdout.log')).read_text()
assert job('grammar-head',['git','rev-parse','HEAD'],GRAMMAR).strip()=='577506f3e0e7e88a5024698e94a2d32de5478dc8'
assert not job('grammar-clean',['git','status','--porcelain','--untracked-files=no'],GRAMMAR).strip()
inputs={str(p.relative_to(GRAMMAR)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [GRAMMAR/'src/parser.c',GRAMMAR/'src/scanner.c',GRAMMAR/'src/tree_sitter/parser.h',GRAMMAR/'LICENSE',GRAMMAR/'NOTICE']}
(OUT/'resume-source-provenance.json').write_text(json.dumps({'grammar_commit':'577506f3e0e7e88a5024698e94a2d32de5478dc8','source_hashes':inputs,'product_sha256':SOURCE,'job_seconds':600,'total_seconds':1200},indent=2)+'\n')
# Prior successful compiler/load jobs retained; library bytes unchanged.
assert (TMP/'scss.dylib').exists()
python=ROOT/'mcp-server/.venv/bin/python';script=OUT/'qualify.py'
# Actual baseline controls already passed in resume-controls job.
for name,pin in [('solidus','4026945d614e81383c007ed1ab1278a0195ce5d9'),('spree','6699cde44303ea85ef6e56c5e87c44a738ab73fc')]:
    repo=Path('/tmp/aci-s07-probe-inputs')/name
    if not repo.exists():
        job(name+'-clone',['git','clone','--no-checkout','--no-local',Path('/tmp/aci-s02-probe-inputs')/name,repo])
        job(name+'-hooks',['git','config','core.hooksPath','/dev/null'],repo)
        job(name+'-sparse',['git','config','core.sparseCheckout','true'],repo)
        (repo/'.git/info/sparse-checkout').write_text('spree/*\n')
        job(name+'-checkout',['git','-c','core.hooksPath=/dev/null','checkout','--detach',pin],repo)
    assert job(name+'-head',['git','rev-parse','HEAD'],repo).strip()==pin
    assert not job(name+'-clean',['git','status','--porcelain','--untracked-files=no'],repo).strip()
    job(name+'-eligible',[python,script,name])
    assert not job(name+'-clean-after',['git','status','--porcelain','--untracked-files=no'],repo).strip()
print('QUALIFICATION_COMPLETE',flush=True)
