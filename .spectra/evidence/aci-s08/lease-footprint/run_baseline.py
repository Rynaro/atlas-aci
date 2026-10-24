"""Serial isolated cost jobs, charged to original frozen probe envelope."""
import json, os, re, signal, subprocess, time
from pathlib import Path
HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[3]
prior=486.6451361659856
for old in HERE.glob('*.job.json'):
    prior += json.loads(old.read_text())['elapsed_seconds']
for name in ('solidus','spree'):
    for mode in ('graph','source','mcp'):
        stem=HERE/f'baseline-{name}-{mode}'
        if stem.with_suffix('.job.json').exists():
            if json.loads(stem.with_suffix('.job.json').read_text())['exit_code'] == 0:continue
            for suffix in ('.job.json','.stdout.log','.stderr.log'):
                stem.with_suffix(suffix).rename(HERE/(stem.name+'-wrapper-failure'+suffix))
        command=[str(ROOT/'mcp-server/.venv/bin/python'),str(HERE/'measure_baseline.py'),mode,'/tmp/aci-s08-probe-inputs/'+name]
        start=time.monotonic(); timeout=False
        with stem.with_suffix('.stdout.log').open('w') as out, stem.with_suffix('.stderr.log').open('w') as err:
            p=subprocess.Popen(['/usr/bin/time','-l',*command],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'mcp-server/src'),'PYTHONDONTWRITEBYTECODE':'1'},stdout=out,stderr=err,start_new_session=True)
            print(json.dumps({'job':stem.name,'pid':p.pid,'prior_charge':prior}),flush=True)
            try: code=p.wait(timeout=min(600,2700-prior))
            except subprocess.TimeoutExpired:
                timeout=True;os.killpg(p.pid,signal.SIGTERM)
                try:code=p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);code=p.wait()
        elapsed=time.monotonic()-start
        memory=re.search(r'(\d+)\s+maximum resident set size',stem.with_suffix('.stderr.log').read_text())
        result={'command':command,'exit_code':code,'timed_out':timeout,'elapsed_seconds':elapsed,'prior_charge_seconds':prior,'cumulative_charge_seconds':prior+elapsed,'peak_rss_bytes':int(memory.group(1)) if memory else None,'per_job_cap_seconds':600,'total_cap_seconds':2700}
        stem.with_suffix('.job.json').write_text(json.dumps(result,indent=2)+'\n');prior+=elapsed
        print(json.dumps({'job':stem.name,**result}),flush=True)
        if code or timeout:raise SystemExit(code or 1)
(HERE/'baseline-cost-summary.json').write_text(json.dumps({'cumulative_charge_seconds':prior,'prior_baseline_charge_seconds':486.6451361659856,'new_serial_job_seconds':prior-486.6451361659856},indent=2)+'\n')
