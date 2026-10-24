"""Pair actual before/after jobs; never substitute accounting for acquisition."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];HERE=Path(__file__).resolve().parent;BEFORE=HERE.parent
prior=json.loads((BEFORE/'first-checkpoint.json').read_text());after={};pairs=[]
for name in ('solidus','spree'):
 for mode in ('graph','source','mcp'):
  first=next(row for row in prior['measurements'] if row['repo']==name and row['mode']==mode)
  stem=HERE/(name+'-'+mode);job=json.loads(stem.with_suffix('.job.json').read_text());value=json.loads(stem.with_suffix('.stdout.log').read_text());assert job['exit_code']==0 and not job['timed_out']
  if mode=='mcp':
   server=re.findall(r'MEASURE_SERVER_RSS_BYTES=(\d+)',stem.with_suffix('.stderr.log').read_text());assert len(server)==1;assert int(server[0])==job['peak_rss_bytes']
  pairs.append({'repo':name,'mode':mode,'baseline':first,'refined':{'job':job,'measurement':value},'not_uncached_filesystem':True,'not_per_request_retained_rss':True})
 before=json.loads((ROOT/'.spectra/evidence/aci-s08/probe'/(name+'-accounting.json')).read_text());new=json.loads((HERE/(name+'-accounting.json')).read_text())
 assert before['exact_paths_and_hashes_match'] and new['exact_paths_and_hashes_match']
 assert before['source_snapshot']['pack_sha256']==new['source_snapshot']['pack_sha256'] and before['source_snapshot']['pack_bytes']==new['source_snapshot']['pack_bytes']
 assert before['source_snapshot']['ranges']==new['source_snapshot']['ranges']
 oracle=json.loads((HERE/(name+'-old-oracle.json')).read_text());assert oracle['graph_digest_exact'] and oracle['all_individual_range_fields_committed_exact']
 after[name]={'baseline_account_job':json.loads((ROOT/'.spectra/evidence/aci-s08/probe'/(name+'-account.job.json')).read_text()),'refined_account_job':json.loads((HERE/(name+'-account.job.json')).read_text()),'baseline_storage':before['storage'],'refined_storage':new['storage'],'range_count':new['source_snapshot']['ranges'],'source_bindings':new['source_snapshot']['bindings'],'pack_bytes_and_hash_identical':True,'frozen_old_algorithm_exact_on_new_corpus':oracle}
summary=json.loads((HERE/'extra-jobs-summary.json').read_text());assert summary['cumulative_serial_job_charge_seconds']<2700
report={'paired_isolated_jobs':pairs,'whole_accounting_and_storage_separate':after,'budget':summary,'limits':{'per_job_seconds':600,'total_serial_job_seconds':2700},'notes':['First graph/source intervals exclude factory setup and stdio initialization; outer job retains setup/teardown.','Two calls in one fresh process are first+same-process repeat, not cold filesystem or statistical latency samples.','Peak RSS is process high-water over both acquisitions; MCP server peak is separately emitted and equals job peak. It is not retained per-request memory.','Whole accounting includes extra SQL/stat/vector/digest work; it is not an ordinary request metric.','Both corpora have zero Rust files within the unchanged ten-language profile; native Rust tests establish Rust behavior.','Typed graph digest remains exact frozen old canonical-array bytes for each producer; graph/proof IDs legitimately change with new producer/manifest.','No memory, latency or export-size hard threshold was invented.']}
(HERE/'paired-costs.json').write_text(json.dumps(report,indent=2)+'\n')
lines=['# S08 paired source-lease footprint measurements','','Actual first acquisition / same-process repeat on identical pinned bytes. No uncached-filesystem or per-request retained-memory claim.','Ordinary query is a real bounded stdio MCP search_symbol for a verified absent name, returning no definitions and the actual snapshot token.','','| Corpus | Mode | Before seconds (first/repeat) | After seconds (first/repeat) | Before process peak RSS B | After process peak RSS B |','|---|---|---:|---:|---:|---:|']
for row in pairs:
 old=row['baseline'];new=row['refined'];a=old['measurement']['measurements'];b=new['measurement']['measurements'];lines.append(f"| {row['repo']} | {row['mode']} | {a[0]['seconds']:.6f} / {a[1]['seconds']:.6f} | {b[0]['seconds']:.6f} / {b[1]['seconds']:.6f} | {old['job']['peak_rss_bytes']} | {new['job']['peak_rss_bytes']} |")
lines+=['','MCP memory is the actual server process peak, distinct from the client process peak; both are retained in raw output.','Intervals exclude setup as documented above; each job also records complete process runtime.','','| Corpus | Before manifest B | Refined manifest B | Exact committed ranges | Source bindings | Unchanged pack B |','|---|---:|---:|---:|---:|---:|']
for name,row in after.items():lines.append(f"| {name} | {row['baseline_storage']['manifests']['bytes']} | {row['refined_storage']['manifests']['bytes']} | {row['range_count']} | {row['source_bindings']} | {row['refined_storage']['packs']['bytes']} |")
lines+=['','Whole accounting is separately measured, including its extra work (not ordinary lease latency):']
for name,row in after.items():
 a=row['baseline_account_job'];b=row['refined_account_job'];lines.append(f"- {name}: before {a['elapsed_seconds']:.6f}s / peak{a['peak_rss_bytes']}B; refined {b['elapsed_seconds']:.6f}s / peak{b['peak_rss_bytes']}B.")
lines+=['',f"Cumulative corpus-job charge: {summary['cumulative_serial_job_charge_seconds']:.9f}s, including all prior719.151777501s and the failed-wrapper measurement. This is a sum of actual serial job elapsed_seconds; runner orchestration wall time is separately recorded. Caps remain600s/job and2700s total.",'','The independent archived bd77 materialized algorithm matches every new corpus graph/observation commitment and every individual range field/order. Compact summaries do not replace own-graph range validation.','No portable format, eligible source, profile, verifier, bar or threshold changed. Larger corpora still require whole-graph validation work per ordinary request.']
(HERE/'paired-costs.md').write_text('\n'.join(lines)+'\n');print(json.dumps({'paired_jobs':len(pairs),'budget':summary}),flush=True)
