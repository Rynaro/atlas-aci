"""Compare current original-byte observations to the preserved pinned inventory."""
import gzip,hashlib,json,sqlite3,sys,os
from collections import Counter,defaultdict
from pathlib import Path
from atlas_aci.codegraph import DEFAULT_LANGS

name=sys.argv[1];repo=Path(sys.argv[2]);out=Path(sys.argv[3]);root=Path(__file__).resolve().parents[4]
with sqlite3.connect((repo/'.atlas/graph.8.db').as_uri()+'?mode=ro',uri=True) as conn:
    manifest=dict(conn.execute('SELECT key,value FROM manifest'))
observations=json.loads(manifest['source_observations'])
assert not any(r['type']=='history' for r in observations)
observations=[r for r in observations if r['type']=='observation']
expected=json.loads(gzip.decompress((root/'.spectra/evidence/aci-s07/grammar-qualification'/f'{name}-all-eligible.json.gz').read_bytes()))['files']
actual={r['path']:r['observed_source_sha256'] for r in observations if r['language'] in DEFAULT_LANGS}
assert actual=={r['path']:r['source_sha256'] for r in expected},'Pinned eligible path/hash inventory changed'
counts=defaultdict(Counter)
for r in observations:counts[r['language']][r['state']]+=1
rows=[{key:r[key] for key in ('path','language','observed_source_sha256','state','reason','location')} for r in sorted(observations,key=lambda r:r['path'])]
raw=(json.dumps(rows,sort_keys=True,separators=(',',':'))+'\n').encode();compressed=gzip.compress(raw,mtime=0);target=out/f'{name}-observations.json.gz';target.write_bytes(compressed)
result={'repo':name,'default_eligible_count':len(actual),'recognized_observation_count':len(observations),'expected_count':len(expected),'exact_paths_and_hashes_match':True,'counts_by_language':dict(counts),'counts':dict(Counter(r['state'] for r in observations)),'generation_inputs':json.loads(manifest['generation_inputs']),'observation_artifact':{'path':str(target.relative_to(root)),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'uncompressed_bytes':len(raw),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),'compressed_bytes':len(compressed),'gzip_mtime':0}}
from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import snapshots
failed={r['path'] for r in observations if r['state']=='failed'}
with sqlite3.connect((repo/'.atlas/graph.8.db').as_uri()+'?mode=ro',uri=True) as conn:
    support={}
    for table,column in [('symbols','path'),('refs','path'),('rationale','path'),('assignment_targets','path'),('import_bindings','path')]:
        support[table]=sum(conn.execute('SELECT COUNT(*) FROM '+table+' WHERE '+column+'=?',(path,)).fetchone()[0] for path in failed)
    support['edges']=sum(conn.execute('SELECT COUNT(*) FROM edges WHERE source_path=? OR target_path=?',(path,path)).fetchone()[0] for path in failed)
assert not any(support.values()),support
compact=json.loads(Path('/tmp/aci-s13-'+name+'-graph.json').read_text())
assert not any(row[0] in failed for row in compact['nodes'])
graph=CodeGraph(repo,read_only=True)
with graph.reader_lease() as reader:
    source=reader._source_lease;fd=source.fd;manifest_source=source.manifest
    assert {r['path']:r['sha256'] for r in manifest_source['current']}=={r['path']:r['observed_source_sha256'] for r in observations}
    assert manifest_source['context']['producer']==result['generation_inputs']['producer']
    assert manifest_source['context']['configuration']==result['generation_inputs']['configuration']
    assert snapshots.digest(manifest_source)==reader.snapshot_id==result['generation_inputs']['snapshot']['id']
    summary={'snapshot_id':reader.snapshot_id,'generation_id':reader.generation_id,'own_graph_closure_validated':True,'current_observation_vector_exact':True,'current_count':len(manifest_source['current']),'ranges':manifest_source['range_proof']['total'],'range_proof':manifest_source['range_proof'],'manifest_format':manifest_source['format'],'bindings':len(manifest_source['bindings']),'available_bindings':sum(r['available'] for r in manifest_source['bindings']),'unavailable_bindings':sum(not r['available'] for r in manifest_source['bindings']),'pack_sha256':manifest_source['pack']['sha256'],'pack_bytes':manifest_source['pack']['size'],'unique_blobs':len(manifest_source['pack']['index']),'retained_pack_fds':1}
try:os.fstat(fd)
except OSError:summary['pack_fd_closed_after_lease']=True
else:raise AssertionError('Source pack descriptor leaked')
graph.close()
store=repo/'.atlas/source-snapshots';cache=repo/'.atlas/extraction-cache/v1'
storage={kind:{'files':len(files),'bytes':sum(p.stat().st_size for p in files)} for kind,files in [('packs',list(store.glob('pack.*.bin'))),('manifests',list(store.glob('manifest.*.json'))),('catalog',list(store.glob('catalog.json'))),('cache',list(cache.glob('*/*.json')))]}
result.update(default_languages=list(DEFAULT_LANGS),rust_files_in_tree=len(list(repo.rglob('*.rs'))),failed_active_support=support,failed_confident_nodes=0,source_snapshot=summary,storage=storage)
(out/f'{name}-accounting.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='generation_inputs'}),flush=True)
