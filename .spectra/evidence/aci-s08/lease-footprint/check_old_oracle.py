"""Materialized reference loaded from authenticated pre-refinement source bytes."""
import gzip,hashlib,importlib.util,json,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent
fixture=json.loads(gzip.decompress((HERE/'v1-fixture.json.gz').read_bytes()))
raw=gzip.decompress((ROOT/'.spectra/evidence/aci-s08/footprint-baseline/runtime-source/storage/snapshots.py.txt.gz').read_bytes())
assert hashlib.sha256(raw).hexdigest()=='bd77fa8ce6114f744a733fb2644f1b406cfee5b053b8d1e8655dae7040dfb479'
with tempfile.TemporaryDirectory() as scratch:
    path=Path(scratch)/'oracle.py';path.write_bytes(raw)
    spec=importlib.util.spec_from_file_location('old_snapshot_oracle',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    expected=fixture['expected'];reference=old.closure(expected['records'],expected['observations'])
    assert reference==expected['closure']
    # Explicit order/dedup controls, including retained non-projection history.
    assert old.closure(list(reversed(expected['records'])),expected['observations'])==reference
    assert old.closure(expected['records']+expected['records'],expected['observations'])==reference
    assert any(row['type']=='history' for row in expected['observations'])
    conflict=[dict(expected['records'][0])];conflict[0]['unexpected_conflict']=True
    try:old.closure(expected['records']+conflict,expected['observations'])
    except ValueError:pass
    else:raise AssertionError('Frozen oracle missed conflicting same ID')
    graphrows={r['id']:r for r in expected['records']}
    for history in expected['observations']:
        if history['type']=='history':graphrows.update({r['id']:r for r in history['records'] if r['type']!='projection'})
    graphbytes=old.ev.canonical(sorted(graphrows.values(),key=lambda r:(r['type'],r['id']))).encode()
    assert hashlib.sha256(graphbytes).hexdigest()==reference['graph_digest']
    result={'exit_code':0,'oracle_source_sha256':hashlib.sha256(raw).hexdigest(),'graph_canonical_array_bytes':len(graphbytes),'graph_digest':reference['graph_digest'],'range_count':len(reference['ranges']),'observation_digest':reference['observation_digest'],'controls':['actual current+history','reverse input order','exact duplicate dedup','conflicting ID rejected'],'no_product_edits':True}
(HERE/'old-oracle-check.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
