"""Assemble only completed fresh measurements; never re-sign older evidence."""
import gzip,hashlib,importlib.util,json,statistics,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];HERE=Path(__file__).resolve().parent;OUT=ROOT/'.spectra/changes/aci-s07-probe';OUT.mkdir(exist_ok=True)
assert hashlib.sha256((ROOT/'mcp-server/src/atlas_aci/codegraph.py').read_bytes()).hexdigest()=='9af60175b29b30ad47b10c6eb3d592bb19a3e6c09a840375e18415c52da872c0'
PINS={'solidus':'4026945d614e81383c007ed1ab1278a0195ce5d9','spree':'6699cde44303ea85ef6e56c5e87c44a738ab73fc'}
BAR={'q_struct':.30,'r':.85,'k':10,'seeds':list(range(10)),'resolution':1.0}
command=[str(ROOT/'mcp-server/.venv/bin/python'),str(ROOT/'scripts/probe-assemble-graph-bundle.py'),'--out',str(OUT/'probe-graphs.json.gz')]
repos=[];jobs={};accounts={};raw_manifest=[]
for name,pin in PINS.items():
    graph=Path('/tmp/aci-s07-approved3-'+name+'-graph.json');modularity=HERE/(name+'-modularity.stdout.log')
    first=json.loads(graph.read_text());second=json.loads(modularity.read_text());account=json.loads((HERE/(name+'-accounting.json')).read_text());accounts[name]=account
    assert second['networkx_version']=='3.6.1'
    assert first['node_count']==second['node_count'] and first['edge_count']==second['edge_count']
    assert [r['seed'] for r in second['louvain_runs']]==list(range(10))
    assert (HERE/(name+'-head.stdout.log')).read_text().strip()==pin
    assert not (HERE/(name+'-clean.stdout.log')).read_text().strip()
    assert not (HERE/(name+'-clean-after.stdout.log')).read_text().strip()
    assert account['exact_paths_and_hashes_match'] and account['default_eligible_count']=={'solidus':2519,'spree':2358}[name]
    for step in ('export','account','modularity'):
        job=json.loads((HERE/(name+'-'+step+'.job.json')).read_text());assert job['exit_code']==0 and not job['timed_out'];jobs[name+'-'+step]=job
    command+=['--repo',name+':'+str(graph)+':'+str(modularity)]
    repos.append({'name':name,'pinned_sha':pin,'node_count':second['node_count'],'edge_count':second['edge_count'],'lpa_q':second['lpa_q'],'lpa_community_count':second['lpa_community_count'],'louvain_q_by_seed':{str(r['seed']):r['q'] for r in second['louvain_runs']}})
    raw=graph.read_bytes();compressed=gzip.compress(raw,mtime=0);target=HERE/(name+'-graph.json.gz');target.write_bytes(compressed)
    assert gzip.decompress(target.read_bytes())==raw
    raw_manifest.append({'logical_path':str(HERE/(name+'-graph.json')),'original_producer_path':str(graph),'compressed_path':str(target.relative_to(ROOT)),'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_bytes':len(compressed),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),'gzip_mtime':0})
with (HERE/'assembly.log').open('w') as log:
    code=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=60).returncode
assert code==0
spec=importlib.util.spec_from_file_location('verifier',ROOT/'scripts/verify-probe-verdict.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
fingerprint=module.compute_indexer_fingerprint(ROOT)
passed=all(statistics.median(r['louvain_q_by_seed'].values())>=.30 and r['lpa_q']>=.30 and r['lpa_q']>=.85*statistics.median(r['louvain_q_by_seed'].values()) for r in repos)
sidecar={'bar':BAR,'recorded_verdict':'PASS' if passed else 'CUT','indexer_fingerprint':fingerprint,'graph_bundle':{'filename':'probe-graphs.json.gz','sha256':hashlib.sha256((OUT/'probe-graphs.json.gz').read_bytes()).hexdigest()},'repos':repos}
(OUT/'aci-s07-probe-lpa-vs-louvain.json').write_text(json.dumps(sidecar,indent=2)+'\n')
size=json.loads((HERE/'spree-export-size.json').read_text());size_job=json.loads((HERE/'spree-jsonl.job.json').read_text());assert size_job['exit_code']==0 and not size_job['timed_out']
lines=['# ACI-S07 current producer measurement','','Fresh default-profile CPU measurements on unchanged public pins. The earlier S02','metrics and all failed S07 attempts remain historical evidence. No coding-agent,','token-savings or compiler-completeness claim is made.','','Recorded verdict: **'+sidecar['recorded_verdict']+'**. The unchanged verifier must independently','recompute this verdict; exit0 alone does not establish PASS. Bar: median Louvain','Q >=0.30, shipped LPA Q >=0.30, and LPA Q >=0.85 × median; resolution 1.0, seeds 0..9.','','| Repo | Nodes | Edges | LPA Q | Louvain median | Ratio |','|---|---:|---:|---:|---:|---:|']
for r in repos:
    median=statistics.median(r['louvain_q_by_seed'].values());lines.append(f"| {r['name']} | {r['node_count']} | {r['edge_count']} | {r['lpa_q']:.12f} | {median:.12f} | {r['lpa_q']/median:.12f} |")
lines+=['','## Source observation coverage','','Every original eligible path/hash matches the preserved inventory:2519 Solidus','and2358 Spree,4877 total. Failed extraction quarantines the whole file; failed','files remain explicit observations and contribute no newly extracted facts.','No historical facts were present in the fresh measured inputs.','','| Repo | Indexed | Failed | Original eligible observations |','|---|---:|---:|---:|']
for name,account in accounts.items():lines.append(f"| {name} | {account['counts'].get('indexed',0)} | {account['counts'].get('failed',0)} | {account['default_eligible_count']} |")
lines+=['','Per-language counts, exact original hashes and diagnostics are preserved in','the raw observation manifests under the attempt3 evidence directory. Partial','coverage is not a claim that the parser accepts every dialect or input.','']
for name,pin in PINS.items():lines.append(f'- {name}: `{pin}`; clean tracked source verified before and after indexing.')
lines+=['','Solidus uses the full pinned tree; Spree retains historical `spree/*` checkout.','No source transformation, dependency change, grammar replacement or repository','hooks/tests were used. Networkx 3.6.1 ran only in isolated `uv --no-project` jobs.','','## Resources and current export size','','Serial subprocess deadlines:600 s/job and2700 s total, including393.127 s charged','to preserved failed/diagnostic attempts before this retry. All command exits,','actual timings and peak RSS are retained; no deadline was relaxed.','','| Job | Seconds | Peak RSS bytes |','|---|---:|---:|']
for name,job in {**jobs,'spree-jsonl':size_job}.items():lines.append(f"| {name} | {job['elapsed_seconds']:.3f} | {job['peak_rss_bytes']} |")
lines+=['',f"Current full Spree v2 JSONL export: **{size['bytes']:,} bytes**, SHA256`{size['sha256']}`.",'This new measurement does not replace the historical export-size saving or its','frozen gate. The large raw export remains temporary; only its exact size/hash','and actual producer resource evidence are retained.','',f'Behavioral fingerprint: `{fingerprint}`.',f"Bundle SHA256: `{sidecar['graph_bundle']['sha256']}`.",'','Raw graphs and all 20 seeded Louvain partitions are losslessly packaged with','original hashes. See `.spectra/evidence/aci-s07/parser-prerequisite/fresh-probe-attempt3/`.','']
(OUT/'aci-s07-probe-lpa-vs-louvain.md').write_text('\n'.join(lines))
(HERE/'graph-raw-manifest.json').write_text(json.dumps(raw_manifest,indent=2)+'\n')
(HERE/'assembly-provenance.json').write_text(json.dumps({'command':command,'exit_code':code,'indexer_fingerprint':fingerprint,'recorded_verdict':sidecar['recorded_verdict'],'bundle_sha256':sidecar['graph_bundle']['sha256'],'source_sha256':hashlib.sha256((ROOT/'mcp-server/src/atlas_aci/codegraph.py').read_bytes()).hexdigest()},indent=2)+'\n')
print(json.dumps({'recorded_verdict':sidecar['recorded_verdict'],'indexer_fingerprint':fingerprint,'bundle_sha256':sidecar['graph_bundle']['sha256']}),flush=True)
