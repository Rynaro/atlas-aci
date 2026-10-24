"""Compare current original-byte observations to the preserved pinned inventory."""
import gzip,hashlib,json,sqlite3,sys
from collections import Counter,defaultdict
from pathlib import Path
from atlas_aci.codegraph import DEFAULT_LANGS

name=sys.argv[1];repo=Path(sys.argv[2]);out=Path(sys.argv[3]);root=Path(__file__).resolve().parents[5]
with sqlite3.connect((repo/'.atlas/graph.7.db').as_uri()+'?mode=ro',uri=True) as conn:
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
(out/f'{name}-accounting.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='generation_inputs'}),flush=True)
