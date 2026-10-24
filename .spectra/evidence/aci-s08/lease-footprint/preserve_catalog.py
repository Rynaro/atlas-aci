"""Predelete exact v1/v2 catalog closure, retaining compact actual v2 manifests."""
import gzip,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent;name=sys.argv[1]
def load(prefix):
 repo=Path('/tmp')/prefix/name
 account_path=ROOT/('.spectra/evidence/aci-s08/probe' if prefix=='aci-s08-probe-inputs' else '.spectra/evidence/aci-s08/lease-footprint/probe')/(name+'-accounting.json')
 account=json.loads(account_path.read_text());token=account['source_snapshot']['snapshot_id'];path=repo/'.atlas/source-snapshots'/('manifest.'+token+'.json');raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==token
 value=json.loads(raw);pack=repo/'.atlas/source-snapshots'/('pack.'+value['pack']['sha256']+'.bin')
 with pack.open('rb') as stream:packsha=hashlib.file_digest(stream,'sha256').hexdigest()
 assert packsha==value['pack']['sha256'] and pack.stat().st_size==value['pack']['size']
 return account,value,raw,{'path':str(path),'bytes':len(raw),'sha256':token,'pack_path':str(pack),'pack_bytes':pack.stat().st_size,'pack_sha256':packsha}
old_account,old,old_raw,old_descriptor=load('aci-s08-probe-inputs');new_account,new,new_raw,new_descriptor=load('aci-s08-refined-probe-inputs')
assert old['format']=='atlas-source-snapshot.v1' and new['format']=='atlas-source-snapshot.v2'
lengths={r['sha256']:r['length'] for r in old['pack']['index']};groups={}
for row in old['bindings']:
 pair=(row['path'],row['sha256']);groups[pair]={**row,'size':lengths.get(row['sha256']),'range_count':0,'min_start':0,'max_end':0}
for row in old['ranges']:
 if row['provenance']!='source':continue
 group=groups[(row['path'],row['sha256'])];assert group['size']==row['size'] and 0<=row['start']<=row['end']<=row['size'];group['min_start']=min(group['min_start'],row['start']) if group['range_count'] else row['start'];group['max_end']=max(group['max_end'],row['end']);group['range_count']+=1
catalog=[groups[key] for key in sorted(groups)];assert catalog==new['bindings'];assert old['current']==new['current'];assert old['observation_digest']==new['observation_digest'];assert old['pack']==new['pack'];assert len(old['ranges'])==new['range_proof']['total']
artifacts=[]
for logical,raw in [(name+'-source-manifest-v2.json',new_raw),(name+'-source-catalog-v1.json',(json.dumps(catalog,sort_keys=True,separators=(',',':'))+'\n').encode())]:
 compressed=gzip.compress(raw,mtime=0);target=HERE/'probe'/(logical+'.gz');target.write_bytes(compressed);assert gzip.decompress(compressed)==raw;artifacts.append({'logical_path':str((HERE/'probe'/logical).relative_to(ROOT)),'compressed_path':str(target.relative_to(ROOT)),'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_bytes':len(compressed),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),'gzip_mtime':0})
result={'repo':name,'baseline_v1':old_descriptor,'refined_v2':new_descriptor,'full_per_source_catalog_bounds_size_available_exact':True,'all_v1_individual_source_ranges_checked_before_aggregation':True,'current_vector_exact':True,'observation_digest_exact':True,'pack_index_and_all_bytes_exact':True,'total_ranges':len(old['ranges']),'bindings':len(catalog),'old_graph_digest':old['graph_digest'],'new_graph_digest':new['graph_digest'],'new_graph_and_ranges_exact_old_algorithm':json.loads((HERE/'probe'/(name+'-old-oracle.json')).read_text()),'artifacts':artifacts,'note':'Full graph/range IDs legitimately differ with producer; the frozen old algorithm matched the complete new graph and committed range rows. All original v1 source catalog bounds independently equal v2. No145MB range-array archive required.'}
(HERE/'probe'/(name+'-catalog-preservation.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
