"""Assemble measured S12 artifacts; the verifier/bar and historical data stay unchanged."""
import gzip,hashlib,importlib.util,json,statistics,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent;OUT=ROOT/'.spectra/changes/aci-s12/probe';OUT.mkdir(parents=True,exist_ok=True)
environment=json.loads((HERE/'environment.json').read_text());assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in environment['sources'].items())
command=[str(ROOT/'mcp-server/.venv/bin/python'),str(ROOT/'scripts/probe-assemble-graph-bundle.py'),'--out',str(OUT/'probe-graphs.json.gz')];repos=[];manifest=[];accounts={};resources={};cache_sizes={}
for name,(_,pin) in environment['pins'].items():
 graph=Path('/tmp/aci-s12-'+name+'-graph.json');mod=HERE/(name+'-modularity.stdout.log');first=json.loads(graph.read_text());values=json.loads(mod.read_text());account=json.loads((HERE/(name+'-accounting.json')).read_text());accounts[name]=account
 assert values['networkx_version']=='3.6.1' and [r['seed'] for r in values['louvain_runs']]==list(range(10))
 assert (first['node_count'],first['edge_count'])==(values['node_count'],values['edge_count'])
 assert (HERE/(name+'-head.stdout.log')).read_text().strip()==pin
 assert not (HERE/(name+'-clean.stdout.log')).read_text().strip() and not (HERE/(name+'-clean-after.stdout.log')).read_text().strip()
 assert account['exact_paths_and_hashes_match'] and account['default_eligible_count']=={'solidus':2519,'spree':2358}[name]
 for step in ('export','account','modularity'):
  job=json.loads((HERE/(name+'-'+step+'.job.json')).read_text());assert job['exit_code']==0 and not job['timed_out'];resources[name+'-'+step]=job
 cache=Path('/tmp/aci-s12-probe-inputs')/name/'.atlas/extraction-cache/v1';entries=list(cache.glob('*/*.json'));cache_sizes[name]={'entries':len(entries),'bytes':sum(p.stat().st_size for p in entries)};cache_sizes[name]['source_snapshot_store']={kind:values for kind,values in account['storage'].items() if kind!='cache'}
 command+=['--repo',name+':'+str(graph)+':'+str(mod)]
 repos.append({'name':name,'pinned_sha':pin,'node_count':values['node_count'],'edge_count':values['edge_count'],'lpa_q':values['lpa_q'],'lpa_community_count':values['lpa_community_count'],'louvain_q_by_seed':{str(r['seed']):r['q'] for r in values['louvain_runs']}})
 raw=graph.read_bytes();packed=gzip.compress(raw,mtime=0);target=HERE/(name+'-graph.json.gz');target.write_bytes(packed);assert gzip.decompress(target.read_bytes())==raw
 manifest.append({'logical_path':str(HERE/(name+'-graph.json')),'producer_output_path':str(graph),'compressed_path':str(target.relative_to(ROOT)),'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_bytes':len(packed),'compressed_sha256':hashlib.sha256(packed).hexdigest(),'gzip_mtime':0})
with (HERE/'assembly.log').open('w') as log:code=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=60).returncode
assert code==0
spec=importlib.util.spec_from_file_location('verifier',ROOT/'scripts/verify-probe-verdict.py');verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier);fingerprint=verifier.compute_indexer_fingerprint(ROOT)
bar={'q_struct':.30,'r':.85,'k':10,'seeds':list(range(10)),'resolution':1.0}
passed=all(statistics.median(r['louvain_q_by_seed'].values())>=.30 and r['lpa_q']>=.30 and r['lpa_q']>=.85*statistics.median(r['louvain_q_by_seed'].values()) for r in repos)
sidecar={'bar':bar,'recorded_verdict':'PASS' if passed else 'CUT','indexer_fingerprint':fingerprint,'graph_bundle':{'filename':'probe-graphs.json.gz','sha256':hashlib.sha256((OUT/'probe-graphs.json.gz').read_bytes()).hexdigest()},'repos':repos};(OUT/'aci-s12-probe-lpa-vs-louvain.json').write_text(json.dumps(sidecar,indent=2)+'\n')
lease_costs={}
for name in environment['pins']:
 lease_costs[name]=json.loads((HERE/(name+'-lease-cost.json')).read_text())
 job=json.loads((HERE/(name+'-lease-cost.job.json')).read_text())
 assert job['exit_code']==0 and not job['timed_out']
 resources[name+'-lease-cost']=job
size=json.loads((HERE/'spree-export-size.json').read_text());size_job=json.loads((HERE/'spree-jsonl.job.json').read_text());assert size_job['exit_code']==0 and not size_job['timed_out'];resources['spree-jsonl']=size_job
lines=['# ACI-S12 fresh producer measurement','','Fresh same-pin/default-language CPU measurements; historical S07 and P3 evidence','is unchanged. No source transformations, profile narrowing, dependencies or bar','changes. Solidus full tree4026945 and Spree sparse spree/*6699cde; exact full pins','and clean tracked-tree checks are in job provenance.','','Recorded verdict: **'+sidecar['recorded_verdict']+'**. Only actual unchanged verifier PASS establishes readiness.','Bar: median Louvain Q>=0.30, LPA Q>=0.30 and>=0.85×median; seeds0..9/resolution1.0.','','| Repo | Nodes | Edges | LPA Q | Median | Indexed | Failed |','|---|---:|---:|---:|---:|---:|---:|']
for record in repos:
 a=accounts[record['name']];lines.append(f"| {record['name']} | {record['node_count']} | {record['edge_count']} | {record['lpa_q']:.12f} | {statistics.median(record['louvain_q_by_seed'].values()):.12f} | {a['counts'].get('indexed',0)} | {a['counts'].get('failed',0)} |")
lines+=['','All4877 original eligible path/hash observations match the preserved inventory.','Failed files contribute no current extracted facts; fresh inputs have no history.','Full source hashes, language counts and diagnostic locations are losslessly retained.','','| Job | Seconds | Peak RSS bytes |','|---|---:|---:|']
for name,job in resources.items():lines.append(f"| {name} | {job['elapsed_seconds']:.3f} | {job['peak_rss_bytes']} |")
lines += ['', '## Separately measured mandatory acquisition and bounded query', '',
          'These intervals use new processes after preceding index/export jobs; OS page-cache state is uncontrolled. Setup selects one actual active assertion source outside both intervals. Process peak RSS includes setup/acquisition/query and is not retained memory per request.', '',
          '| Repo | Lease acquisition seconds | Lease CPU seconds | Query incl admission/SDK seconds | Query CPU seconds |',
          '|---|---:|---:|---:|---:|']
for name,cost in lease_costs.items():
 lines.append(f"| {name} | {cost['lease_acquisition_seconds']:.6f} | {cost['lease_acquisition_cpu_seconds']:.6f} | {cost['query_including_admission_and_sdk_encoding_seconds']:.6f} | {cost['query_including_admission_and_sdk_encoding_cpu_seconds']:.6f} |")
lines += ['', 'Sample query work (not a positive-path throughput benchmark):']
for name,cost in lease_costs.items():
 lines.append(f"- {name}: `{json.dumps(cost['work'],sort_keys=True)}`, outcome `{cost['outcome']}`, stop `{cost['stop_reason']}`. The selected first raw adjacency row was unresolved, so no hop proof was hydrated.")
lines += ['', 'Mandatory graph/source integrity validation is outside traversal counters/deadline. The bounded query uses at most two visited nodes and one examined row; actual SQL traces, counters, complete SDK byte counts and SQLite/pack closure are recorded. No hard total request latency or new performance threshold is claimed.']
lines+=['',f"Current Spree JSONL: **{size['bytes']:,}bytes**, SHA256`{size['sha256']}`.",'No new size threshold or historical ratio is claimed. This remains a material','storage/memory cost; raw full export stays temporary, exact measurement retained.','',f'Extraction cache and source snapshot store byte/entry counts: `{json.dumps(cache_sizes,sort_keys=True)}`.','No eviction policy or speed threshold is claimed.','',f'Behavioral fingerprint:`{fingerprint}`.',f"Graph bundle SHA256:`{sidecar['graph_bundle']['sha256']}`.",'','All source/command/raw partition/hash/resource records are under','.spectra/evidence/aci-s12/probe/. Serial600s/job/2700s total caps were unchanged.']
lines += ['', f"Prior failed-attempt charge: {environment['prior_probe_seconds_charged']:.6f}s; it remains inside the unchanged2700s budget.", f"Cumulative serial job charge including size and isolated lease/query jobs: {size_job['prior_seconds_charged'] + size_job['elapsed_seconds']:.6f}s."]
lines += ['', 'Both corpora contain zero .rs files; the measured generation profile contains10 languages including Rust. Rust quality is established by qualified native workspace and offline Docker tests.', 'Prior and cumulative probe charges are sums of serial job elapsed_seconds, not total wall runtime; original elapsed_total fields retain orchestration overhead.']
(OUT/'aci-s12-probe-lpa-vs-louvain.md').write_text('\n'.join(lines)+'\n');(HERE/'graph-raw-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');(HERE/'assembly-provenance.json').write_text(json.dumps({'command':command,'exit_code':code,'indexer_fingerprint':fingerprint,'verdict':sidecar['recorded_verdict'],'sources':environment['sources'],'cache_sizes':cache_sizes,'isolated_lease_costs':lease_costs,'prior_failed_probe_seconds_charged':environment['prior_probe_seconds_charged'],'cumulative_probe_seconds_charged':size_job['prior_seconds_charged']+size_job['elapsed_seconds'],'bundle_sha256':sidecar['graph_bundle']['sha256']},indent=2)+'\n');print(json.dumps(sidecar),flush=True)
