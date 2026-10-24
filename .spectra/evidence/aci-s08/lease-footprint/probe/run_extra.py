"""Remaining serial corpus proof/cost jobs, preserving all prior charges."""
import hashlib,json,os,re,signal,subprocess,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
environment=json.loads((HERE/'environment.json').read_text());sources=environment['sources']
prior=environment['prior_probe_seconds_charged']+sum(json.loads(p.read_text())['elapsed_seconds'] for p in HERE.glob('*.job.json'))
for name in ('solidus','spree'):
 repo='/tmp/aci-s08-refined-probe-inputs/'+name
 for mode in ('old-oracle','graph','source','mcp'):
  assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in sources.items())
  stem=HERE/(name+'-'+mode);assert not stem.with_suffix('.job.json').exists()
  command=[str(ROOT/'mcp-server/.venv/bin/python'),str(HERE/('old_oracle.py' if mode=='old-oracle' else 'measure_readers.py'))]+([repo] if mode=='old-oracle' else [mode,repo])
  start=time.monotonic();timeout=False
  with stem.with_suffix('.stdout.log').open('w') as out,stem.with_suffix('.stderr.log').open('w') as err:
   process=subprocess.Popen(['/usr/bin/time','-l',*command],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'mcp-server/src'),'PYTHONDONTWRITEBYTECODE':'1'},stdout=out,stderr=err,start_new_session=True)
   print(json.dumps({'name':stem.name,'pid':process.pid,'prior_seconds':prior}),flush=True)
   try:code=process.wait(timeout=min(600,2700-prior))
   except subprocess.TimeoutExpired:
    timeout=True;os.killpg(process.pid,signal.SIGTERM)
    try:code=process.wait(timeout=5)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);code=process.wait()
  elapsed=time.monotonic()-start;memory=re.search(r'(\d+)\s+maximum resident set size',stem.with_suffix('.stderr.log').read_text())
  result={'command':command,'exit_code':code,'timed_out':timeout,'elapsed_seconds':elapsed,'prior_seconds_charged':prior,'cumulative_seconds_charged':prior+elapsed,'peak_rss_bytes':int(memory.group(1)) if memory else None,'per_job_cap_seconds':600,'total_cap_seconds':2700}
  stem.with_suffix('.job.json').write_text(json.dumps(result,indent=2)+'\n');prior+=elapsed;print(json.dumps({'name':stem.name,**result}),flush=True)
  if code or timeout:raise SystemExit(code or 1)
(HERE/'extra-jobs-summary.json').write_text(json.dumps({'cumulative_serial_job_charge_seconds':prior,'original_baseline_and_cost_charge_seconds':environment['prior_probe_seconds_charged'],'all_jobs_seconds_this_batch':prior-environment['prior_probe_seconds_charged']},indent=2)+'\n')
