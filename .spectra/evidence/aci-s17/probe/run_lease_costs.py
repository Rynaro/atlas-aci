"""Serial isolated lease/query jobs charged to the same 600/2700 CPU budget."""
import hashlib,json,os,re,signal,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[4];out=Path(__file__).resolve().parent
assert (out/'run.exit').read_text().strip()=='0'
environment=json.loads((out/'environment.json').read_text())
assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==sha for p,sha in environment['sources'].items())
for name in ('solidus','spree'):
 prior=environment['prior_probe_seconds_charged']+sum(json.loads(p.read_text())['elapsed_seconds'] for p in out.glob('*.job.json'))
 assert prior<2700
 command=[str(root/'mcp-server/.venv/bin/python'),str(out/'lease_cost.py'),name,'/tmp/aci-s17-probe-inputs/'+name,str(out)]
 started=time.monotonic();timed_out=False
 with (out/f'{name}-lease-cost.stdout.log').open('w') as stdout,(out/f'{name}-lease-cost.stderr.log').open('w') as stderr:
  child=subprocess.Popen(['/usr/bin/time','-l',*command],cwd=root,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root/'mcp-server/src')},stdout=stdout,stderr=stderr,start_new_session=True)
  print(json.dumps({'started':name,'pid':child.pid,'prior_serial_job_seconds':prior}),flush=True)
  try:code=child.wait(timeout=min(600,2700-prior))
  except subprocess.TimeoutExpired:
   timed_out=True;os.killpg(child.pid,signal.SIGTERM)
   try:code=child.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);code=child.wait()
 memory=re.search(r'(\d+)\s+maximum resident set size',(out/f'{name}-lease-cost.stderr.log').read_text())
 record={'command':command,'exit_code':code,'timed_out':timed_out,'elapsed_seconds':time.monotonic()-started,'peak_rss_bytes':int(memory.group(1)) if memory else None,'prior_serial_job_seconds_charged':prior,'per_job_seconds':600,'total_seconds':2700}
 (out/f'{name}-lease-cost.job.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)
 assert code==0 and not timed_out
