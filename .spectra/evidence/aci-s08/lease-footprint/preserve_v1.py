"""Actual old producer build/history, archived without format or identity rewriting."""
import base64,gzip,hashlib,importlib.util,json,tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent
sources=json.loads((ROOT/'.spectra/plans/aci-s08-lease-footprint.handoff.yaml').read_text())['held_baseline_sources']
assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in sources.items())
repo=(Path(tempfile.mkdtemp(prefix='aci-s08-real-v1-'))/'repo').resolve();repo.mkdir()
(repo/'a.py').write_bytes(b'def target():\n return 1\ndef caller():\n target()\n')
(repo/'b.py').write_bytes(b'def separate():\n return 2\n')
g=CodeGraph(repo);g.build()
(repo/'a.py').write_bytes(b'def broken(:\n pass\n');g.build()
with g.reader_lease() as reader:
    observations=reader._observation_records(reader.db)
    records=list(ev.records(reader.db))
    assert any(r['type']=='history' for r in observations)
    token=reader.snapshot_id
    assert reader._source_lease.manifest['format']=='atlas-source-snapshot.v1'
    old_path=ROOT/'.spectra/evidence/aci-s08/footprint-baseline/runtime-source/storage/snapshots.py.txt.gz'
    frozen=gzip.decompress(old_path.read_bytes());assert hashlib.sha256(frozen).hexdigest()==sources['mcp-server/src/atlas_aci/storage/snapshots.py']
    module_path=repo.parent/'frozen_snapshot_oracle.py';module_path.write_bytes(frozen)
    spec=importlib.util.spec_from_file_location('frozen_s08_snapshot_oracle',module_path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    oracle=old.closure(records,observations)
    assert all(reader._source_lease.manifest[key]==value for key,value in oracle.items())
    expected={'closure':oracle,'records':records,'observations':observations,'snapshot_id':token,'generation_id':reader.generation_id}
g.close()
files=[]
for path in sorted([g.db_path,*list((g.atlas_dir/'source-snapshots').iterdir()),*repo.glob('*.py')]):
    raw=path.read_bytes();files.append({'path':str(path.relative_to(repo)),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'base64':base64.b64encode(raw).decode()})
value={'format':'actual-held-v1-fixture.v1','repository':str(repo),'sources':sources,'files':files,'expected':expected,'origin':'Built by held 7565/bd77 real parser; valid G1 then failed syntax G2 with actual retained history. No metadata/version rewrites.'}
raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode();compressed=gzip.compress(raw,mtime=0)
target=HERE/'v1-fixture.json.gz';target.write_bytes(compressed)
provenance={'path':str(target.relative_to(ROOT)),'raw_sha256':hashlib.sha256(raw).hexdigest(),'raw_bytes':len(raw),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),'compressed_bytes':len(compressed),'gzip_mtime':0,'repository':str(repo),'sources':sources,'snapshot_id':token,'file_count':len(files),'history_count':sum(r['type']=='history' for r in observations),'independent_oracle_source':str(old_path.relative_to(ROOT)),'independent_oracle_sha256':hashlib.sha256(frozen).hexdigest()}
(HERE/'v1-fixture-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n');print(json.dumps(provenance),flush=True)
