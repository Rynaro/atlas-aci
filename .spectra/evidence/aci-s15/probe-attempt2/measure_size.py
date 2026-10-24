"""One actual current portable export, after serial fresh graph measurements."""
import hashlib,json,os,re,signal,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent
assert (HERE/'run.exit').read_text().strip()=='0'
env=json.loads((HERE/'environment.json').read_text()); assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in env['sources'].items())
prior=env['prior_probe_seconds_charged']+sum(json.loads(p.read_text())['elapsed_seconds'] for p in HERE.glob('*.job.json'))
assert prior<2700
artifact=Path('/tmp/aci-s15-retry-spree-export.jsonl'); command=['/tmp/aci-s01-tools/bin/uv','run','--frozen','python','-m','atlas_aci','export','--repo','/tmp/aci-s15-retry-probe-inputs/spree',str(artifact)]
started=time.monotonic();timed_out=False
with (HERE/'spree-jsonl.stdout.log').open('w') as out,(HERE/'spree-jsonl.stderr.log').open('w') as err:
 process=subprocess.Popen(['/usr/bin/time','-l',*command],cwd=ROOT/'mcp-server',env={**os.environ,'UV_CACHE_DIR':'/tmp/aci-s01-uv-cache','UV_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT/'mcp-server/src')},stdout=out,stderr=err,start_new_session=True)
 print(json.dumps({'name':'spree-jsonl','pid':process.pid,'prior_seconds':prior}),flush=True)
 try:code=process.wait(timeout=min(600,2700-prior))
 except subprocess.TimeoutExpired:
  timed_out=True;os.killpg(process.pid,signal.SIGTERM)
  try:code=process.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);code=process.wait()
memory=re.search(r'(\d+)\s+maximum resident set size',(HERE/'spree-jsonl.stderr.log').read_text())
result={'command':command,'cwd':str(ROOT/'mcp-server'),'exit_code':code,'timed_out':timed_out,'elapsed_seconds':time.monotonic()-started,'peak_rss_bytes':int(memory.group(1)) if memory else None,'prior_seconds_charged':prior,'per_job_seconds':600,'total_seconds':2700}
(HERE/'spree-jsonl.job.json').write_text(json.dumps(result,indent=2)+'\n');assert code==0 and not timed_out
with artifact.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
size={'path':str(artifact),'bytes':artifact.stat().st_size,'sha256':sha}
(HERE/'spree-export-size.json').write_text(json.dumps(size,indent=2)+'\n');print(json.dumps({**result,**size}),flush=True)
