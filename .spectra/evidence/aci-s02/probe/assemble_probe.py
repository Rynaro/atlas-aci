"""Assemble ACI-S02 evidence entirely from the fresh authorized probe outputs."""
import hashlib
import importlib.util
import json
import shutil
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
OUT = ROOT/'.spectra/changes/aci-s02-probe'
AMENDMENT = json.loads((ROOT/'.spectra/plans/aci-s02.probe-scope-amendment.json').read_text())
bar = AMENDMENT['fixed_bar']
repos = []
command = ['mcp-server/.venv/bin/python','scripts/probe-assemble-graph-bundle.py','--out',str(OUT/'probe-graphs.json.gz')]
measurements = {}
for name, pin in AMENDMENT['pins'].items():
    graph = Path('/tmp/aci-s02-probe-'+name+'-graph.json')
    modularity = HERE/(name+'-modularity.stdout.log')
    phase1 = json.loads(graph.read_text())
    phase2 = json.loads(modularity.read_text())
    assert phase2['networkx_version']=='3.6.1'
    assert phase1['node_count']==phase2['node_count'] and phase1['edge_count']==phase2['edge_count']
    assert [run['seed'] for run in phase2['louvain_runs']]==list(range(10))
    assert (HERE/(name+'-head.stdout.log')).read_text().strip()==pin
    assert not (HERE/(name+'-status.stdout.log')).read_text().strip()
    for step in ['export','modularity']:
        job=json.loads((HERE/(name+'-'+step+'.job.json')).read_text())
        assert job['exit_code']==0 and not job['timed_out']
        measurements[name+'-'+step]=job
    shutil.copyfile(graph,HERE/(name+'-graph.json'))
    command+=['--repo',name+':'+str(graph)+':'+str(modularity)]
    repos.append({'name':name,'pinned_sha':pin,'node_count':phase2['node_count'],'edge_count':phase2['edge_count'],'lpa_q':phase2['lpa_q'],'lpa_community_count':phase2['lpa_community_count'],'louvain_q_by_seed':{str(run['seed']):run['q'] for run in phase2['louvain_runs']}})
with (HERE/'assembly.log').open('w') as stream:
    subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=60)
spec=importlib.util.spec_from_file_location('s02_probe_verifier',ROOT/'scripts/verify-probe-verdict.py')
verifier=importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
fingerprint=verifier.compute_indexer_fingerprint(ROOT)
passed=all(statistics.median(r['louvain_q_by_seed'].values())>=bar['q_struct'] and r['lpa_q']>=bar['q_struct'] and r['lpa_q']>=bar['r']*statistics.median(r['louvain_q_by_seed'].values()) for r in repos)
sidecar={'bar':bar,'recorded_verdict':'PASS' if passed else 'CUT','indexer_fingerprint':fingerprint,'graph_bundle':{'filename':'probe-graphs.json.gz','sha256':hashlib.sha256((OUT/'probe-graphs.json.gz').read_bytes()).hexdigest()},'repos':repos}
(OUT/'aci-s02-probe-lpa-vs-louvain.json').write_text(json.dumps(sidecar,indent=2)+'\n')
lines=['# ACI-S02 — current producer LPA/Louvain evidence','','This is a fresh local CPU measurement of the constrained resolver, separate from','the unchanged archived probe. It is not a model/agent evaluation and makes no','token-savings or coding-success claim. The graph input changed; higher modularity','does not establish an improvement over the historical measurement.','','Current verdict: **'+sidecar['recorded_verdict']+'**. Fixed bar: median Louvain Q >= 0.30, LPA Q >= 0.30 and','LPA Q >= 0.85 × median Louvain Q; resolution 1.0, seeds 0..9, ten runs per repo.','','| Repository | Nodes | Edges | LPA Q | Louvain median Q | Ratio |','|---|---:|---:|---:|---:|---:|']
for r in repos:
    median=statistics.median(r['louvain_q_by_seed'].values());lines.append(f"| {r['name']} | {r['node_count']} | {r['edge_count']} | {r['lpa_q']:.12f} | {median:.12f} | {r['lpa_q']/median:.12f} |")
lines+=['','Both disposable inputs were fetched at exact commits and checked for a clean','tracked source tree before indexing. Solidus is the complete shallow tree; Spree','uses the historical runbook\'s `spree/*` sparse checkout. No repository tests ran.','']
for r in repos:lines.append(f"- {r['name']}: `{r['pinned_sha']}`")
lines+=['','Source SHA-256: `'+hashlib.sha256((ROOT/'mcp-server/src/atlas_aci/codegraph.py').read_bytes()).hexdigest()+'`.','Behavioral indexer fingerprint: `'+fingerprint+'`.','Bundle SHA-256: `'+sidecar['graph_bundle']['sha256']+'`.','','All jobs were serial, bounded to 600 seconds each and 2700 seconds total.','No deadline fired. Peak memory below is actual macOS `/usr/bin/time -l` RSS.','','| Job | Seconds | Peak RSS bytes |','|---|---:|---:|']
for name,job in measurements.items():lines.append(f"| {name} | {job['duration_seconds']:.3f} | {job['peak_memory_bytes']} |")
lines+=['','Raw graphs, all twenty seeded partitions, commands, stdout/stderr, runtime','environment and assembly provenance are under','.spectra/evidence/aci-s02/probe/. The shipped LPA and independent networkx','3.6.1 Louvain runs used identical graph primitives; the unchanged verifier','recomputes every Q/count from the committed bundle. Networkx was isolated with','`uv run --no-project --with networkx==3.6.1`; project dependencies are unchanged.','']
(OUT/'aci-s02-probe-lpa-vs-louvain.md').write_text('\n'.join(lines))
(HERE/'assembly-provenance.json').write_text(json.dumps({'command':command,'exit_code':0,'graph_sha256':{name:hashlib.sha256((HERE/(name+'-graph.json')).read_bytes()).hexdigest() for name in AMENDMENT['pins']},'modularity_stdout_sha256':{name:hashlib.sha256((HERE/(name+'-modularity.stdout.log')).read_bytes()).hexdigest() for name in AMENDMENT['pins']},'indexer_fingerprint':fingerprint,'recorded_verdict':sidecar['recorded_verdict']},indent=2)+'\n')
print(json.dumps({'recorded_verdict':sidecar['recorded_verdict'],'indexer_fingerprint':fingerprint,'bundle_sha256':sidecar['graph_bundle']['sha256']}))
