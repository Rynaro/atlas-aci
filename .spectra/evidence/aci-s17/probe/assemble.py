"""Assemble measured S12 artifacts; the verifier/bar and historical data stay unchanged."""
import gzip,hashlib,importlib.util,json,statistics,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent;OUT=ROOT/'.spectra/changes/aci-s17/probe';OUT.mkdir(parents=True,exist_ok=True)
environment=json.loads((HERE/'environment.json').read_text());assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in environment['sources'].items())
command=[str(ROOT/'mcp-server/.venv/bin/python'),str(ROOT/'scripts/probe-assemble-graph-bundle.py'),'--out',str(OUT/'probe-graphs.json.gz')];repos=[];manifest=[];accounts={};resources={};cache_sizes={}
for name,(_,pin) in environment['pins'].items():
 graph=Path('/tmp/aci-s17-'+name+'-graph.json');mod=HERE/(name+'-modularity.stdout.log');first=json.loads(graph.read_text());values=json.loads(mod.read_text());account=json.loads((HERE/(name+'-accounting.json')).read_text());accounts[name]=account
 assert values['networkx_version']=='3.6.1' and [r['seed'] for r in values['louvain_runs']]==list(range(10))
 assert (first['node_count'],first['edge_count'])==(values['node_count'],values['edge_count'])
 assert (HERE/(name+'-head.stdout.log')).read_text().strip()==pin
 assert not (HERE/(name+'-clean.stdout.log')).read_text().strip() and not (HERE/(name+'-clean-after.stdout.log')).read_text().strip()
 assert account['exact_paths_and_hashes_match'] and account['default_eligible_count']=={'solidus':2519,'spree':2358}[name]
 for step in ('export','account','modularity'):
  job=json.loads((HERE/(name+'-'+step+'.job.json')).read_text());assert job['exit_code']==0 and not job['timed_out'];resources[name+'-'+step]=job
 cache=Path('/tmp/aci-s17-probe-inputs')/name/'.atlas/extraction-cache/v1';entries=list(cache.glob('*/*.json'));cache_sizes[name]={'entries':len(entries),'bytes':sum(p.stat().st_size for p in entries)};cache_sizes[name]['source_snapshot_store']={kind:values for kind,values in account['storage'].items() if kind!='cache'}
 command+=['--repo',name+':'+str(graph)+':'+str(mod)]
 repos.append({'name':name,'pinned_sha':pin,'node_count':values['node_count'],'edge_count':values['edge_count'],'lpa_q':values['lpa_q'],'lpa_community_count':values['lpa_community_count'],'louvain_q_by_seed':{str(r['seed']):r['q'] for r in values['louvain_runs']}})
 raw=graph.read_bytes();packed=gzip.compress(raw,mtime=0);target=HERE/(name+'-graph.json.gz');target.write_bytes(packed);assert gzip.decompress(target.read_bytes())==raw
 manifest.append({'logical_path':str(HERE/(name+'-graph.json')),'producer_output_path':str(graph),'compressed_path':str(target.relative_to(ROOT)),'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_bytes':len(packed),'compressed_sha256':hashlib.sha256(packed).hexdigest(),'gzip_mtime':0})
with (HERE/'assembly.log').open('w') as log:code=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=60).returncode
assert code==0
spec=importlib.util.spec_from_file_location('verifier',ROOT/'scripts/verify-probe-verdict.py');verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier);fingerprint=verifier.compute_indexer_fingerprint(ROOT)
bar={'q_struct':.30,'r':.85,'k':10,'seeds':list(range(10)),'resolution':1.0}
passed=all(statistics.median(r['louvain_q_by_seed'].values())>=.30 and r['lpa_q']>=.30 and r['lpa_q']>=.85*statistics.median(r['louvain_q_by_seed'].values()) for r in repos)
sidecar={'bar':bar,'recorded_verdict':'PASS' if passed else 'CUT','indexer_fingerprint':fingerprint,'graph_bundle':{'filename':'probe-graphs.json.gz','sha256':hashlib.sha256((OUT/'probe-graphs.json.gz').read_bytes()).hexdigest()},'repos':repos};(OUT/'aci-s17-probe-lpa-vs-louvain.json').write_text(json.dumps(sidecar,indent=2)+'\n')
lease_costs={name:json.loads((HERE/(name+'-lease-cost.json')).read_text()) for name in environment['pins']}
size=json.loads((HERE/'spree-export-size.json').read_text())
jobs={p.stem:json.loads(p.read_text()) for p in HERE.glob('*.job.json')}
assert all(j['exit_code']==0 and not j['timed_out'] for j in jobs.values())
charge=environment['prior_probe_seconds_charged']+sum(j['elapsed_seconds'] for j in jobs.values());assert charge<2700
lines=['# ACI-S17 fresh producer measurement','','Actual same-pin/default10-profile CPU measurements. Historical artifacts, bar, seeds, resolver and verifier are unchanged. Both corpora contain zero Rust files; Rust quality is established by native workspace tests. No source transformations, input narrowing, paid calls or dependency changes.','','Recorded verdict: **'+sidecar['recorded_verdict']+'**. Only unchanged verifier actual PASS establishes the gate.','','| Repo | Nodes | Edges | LPA Q | Median Louvain Q | Indexed | Failed |','|---|---:|---:|---:|---:|---:|---:|']
for r in repos:
 a=accounts[r['name']];lines.append(f"| {r['name']} | {r['node_count']} | {r['edge_count']} | {r['lpa_q']:.12f} | {statistics.median(r['louvain_q_by_seed'].values()):.12f} | {a['counts'].get('indexed',0)} | {a['counts'].get('failed',0)} |")
lines+=['','All4877 preserved eligible path/hash observations and exact pins are accounted for. Fresh inputs have no history; failed files supply no current extracted facts or confident nodes. Full source observation locations, native source-proof context, logical retrieval descriptor, producer modules, cache and snapshot storage are retained in accounting records.','','## Measured current costs','','| Job | Seconds | Process peak RSS bytes |','|---|---:|---:|']
for name,j in jobs.items():lines.append(f"| {name} | {j['elapsed_seconds']:.6f} | {j.get('peak_rss_bytes')} |")
lines+=['','| Repo | Mandatory lease wall seconds | Lease CPU seconds |','|---|---:|---:|']
for name,c in lease_costs.items():lines.append(f"| {name} | {c['lease_acquisition_seconds']:.6f} | {c['lease_acquisition_cpu_seconds']:.6f} |")
lines+=['','Each cost job runs in a new process after prior build/export/accounting jobs; OS page-cache state is uncontrolled. Peak RSS includes fixture selection, acquisition and both queries, not retained per-request bytes. Actual native entityID and two source-file words are selected outside isolated intervals. Query intervals include ranking, whole-candidate admission and SDK result construction, before final wire dump/size assertion. Neither interval is full MCP transport latency. Mandatory whole-generation canonical/source/BOTH-store FTS validation precedes query VM/deadline counters. SQLite FTS may scan/sort internally; explicit driver SQL counters do not claim to count internal virtual-table work. No new latency, size, memory or quality threshold is invented.','']
for name,c in lease_costs.items():
 for query in c['queries']:lines.append('- '+name+' '+query['label']+': `'+json.dumps(query,sort_keys=True)+'`')
 lines.append('- '+name+' native retrieval dbstat page bytes (separate from file/cache/source-pack accounting): `'+json.dumps(c['retrieval_dbstat_bytes'],sort_keys=True)+'`')
lines+=['',f"Current full Spree portable JSONL: **{size['bytes']:,} bytes**, SHA256 `{size['sha256']}`. Portable epoch7 remains unchanged and contains no retrieval tables/text. No historical size ratio is claimed.",'', 'Cache and snapshot physical bytes/entries: `'+json.dumps(cache_sizes,sort_keys=True)+'`. Cache is a sibling store, not an additional source-pack component. Native FTS/index page bytes are separately measured; no space-reclamation policy is promised.','',f"Cumulative SUM of serial job elapsed_seconds: {charge:.9f}s, including prior charge {environment['prior_probe_seconds_charged']:.9f}s. Original per-run elapsed_total fields include orchestration overhead; this sum is not exact wall runtime.600s/job and2700s cumulative caps were unchanged.",'',f"Behavioral fingerprint: `{fingerprint}`.",f"Bundle SHA256: `{sidecar['graph_bundle']['sha256']}`."]
(OUT/'aci-s17-probe-lpa-vs-louvain.md').write_text('\n'.join(lines)+'\n')
(HERE/'graph-raw-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(HERE/'assembly-provenance.json').write_text(json.dumps({'command':command,'exit_code':code,'indexer_fingerprint':fingerprint,'verdict':sidecar['recorded_verdict'],'sources':environment['sources'],'cache_sizes':cache_sizes,'isolated_lease_costs':lease_costs,'prior_failed_probe_seconds_charged':environment['prior_probe_seconds_charged'],'cumulative_probe_seconds_charged':charge,'bundle_sha256':sidecar['graph_bundle']['sha256']},indent=2)+'\n')
print(json.dumps(sidecar),flush=True)
