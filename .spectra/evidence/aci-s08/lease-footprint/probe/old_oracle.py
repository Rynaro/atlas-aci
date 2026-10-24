"""Corpus-sized independent frozen materialized algorithm, not a lease benchmark."""
import gzip, hashlib, importlib.util, json, sqlite3, sys, tempfile
from collections import Counter
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev
from atlas_aci.storage import snapshots as snap
ROOT=Path(__file__).resolve().parents[5];HERE=Path(__file__).resolve().parent
repo=Path(sys.argv[1]).resolve();name=repo.name
env=json.loads((HERE/'environment.json').read_text());assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in env['sources'].items())
raw=gzip.decompress((ROOT/'.spectra/evidence/aci-s08/footprint-baseline/runtime-source/storage/snapshots.py.txt.gz').read_bytes());assert hashlib.sha256(raw).hexdigest()=='bd77fa8ce6114f744a733fb2644f1b406cfee5b053b8d1e8655dae7040dfb479'
g=CodeGraph(repo,read_only=True)
with tempfile.TemporaryDirectory() as directory:
 path=Path(directory)/'old.py';path.write_bytes(raw);spec=importlib.util.spec_from_file_location('old_corpus_source_oracle',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
 with sqlite3.connect(g.db_path.as_uri()+'?mode=ro',uri=True) as conn:
  conn.row_factory=sqlite3.Row;observations=g._observation_records(conn);records=list(ev.records(conn));inputs=json.loads(conn.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0]);token=inputs['snapshot']['id']
 reference=old.closure(records,observations)
 manifest=snap.validate_manifest(json.loads((g.atlas_dir/'source-snapshots'/('manifest.'+token+'.json')).read_bytes()))
 assert manifest['format']=='atlas-source-snapshot.v2'
 assert manifest['graph_digest']==reference['graph_digest'] and manifest['observation_digest']==reference['observation_digest'] and manifest['current']==reference['current']
 ranges=reference['ranges'];counts=Counter(row['kind'] for row in ranges)
 proof={'version':1,'algorithm':'sha256-canonical-json-array-v1','total':len(ranges),'occurrence':counts['occurrence'],'evidence':counts['evidence'],'legacy':sum(row['provenance']=='legacy_unavailable' for row in ranges),'sha256':hashlib.sha256(b'atlas-source-ranges.v2\x00'+ev.canonical(ranges).encode()).hexdigest()}
 assert proof==manifest['range_proof']
 groups={}
 for row in ranges:
  if row['provenance']=='source':groups.setdefault((row['path'],row['sha256']),[]).append(row)
 current={(row['path'],row['sha256']) for row in reference['current']}
 assert set(groups)|current=={(row['path'],row['sha256']) for row in manifest['bindings']}
 for row in manifest['bindings']:
  grouped=groups.get((row['path'],row['sha256']),[])
  assert row['range_count']==len(grouped)
  assert row['min_start']==min((r['start'] for r in grouped),default=0)
  assert row['max_end']==max((r['end'] for r in grouped),default=0)
  assert all(r['size']==row['size'] and 0<=r['start']<=r['end']<=row['size'] for r in grouped)
 result={'repo':name,'oracle_source_sha256':hashlib.sha256(raw).hexdigest(),'actual_original_materialized_algorithm':True,'graph_digest_exact':True,'observation_digest_exact':True,'all_individual_range_fields_committed_exact':True,'range_proof':proof,'source_catalog_exact':True,'source_bindings':len(manifest['bindings']),'snapshot_id':token,'not_ordinary_lease_metric':True}
 (HERE/(name+'-old-oracle.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
g.close()
