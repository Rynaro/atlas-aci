"""Real native8/source/history fixture frozen before S17 product edits."""
import asyncio,base64,gzip,hashlib,json,os
from pathlib import Path
from atlas_aci.codegraph import CodeGraph,SCHEMA_EPOCH
from atlas_aci.config import Config
from atlas_aci.server import build_server_state,call_tool_result

root=Path(__file__).resolve().parents[3];out=Path(__file__).resolve().parent
repo=Path('/tmp/aci-s17-native8-source-corrected').resolve();assert not repo.exists();repo.mkdir()
assert SCHEMA_EPOCH==8
sources={'a.py':b'def A():\n B()\n','b.py':b'def B():\n pass\n','history.py':b'def previous():\n pass\n'}
for name,data in sources.items():(repo/name).write_bytes(data)
graph=CodeGraph(repo);g1=graph.build()
with graph.reader_lease() as held:
 old_gen=held.generation_id;old_snapshot=held.snapshot_id;old_fd=held._source_lease.fd
 (repo/'history.py').write_bytes(b'def broken(:\n pass\n')
 g2=graph.build()
 assert held.generation_id==old_gen and held.read_source('history.py')==sources['history.py']
 assert held.db.execute("SELECT value FROM manifest WHERE key='generation_id'").fetchone()[0]==old_gen
os_closed=False
try:os.fstat(old_fd)
except OSError:os_closed=True
assert os_closed
with graph.reader_lease() as reader:
 status=reader.published_status();generation=reader.generation_id;snapshot=reader.snapshot_id
 observations=json.loads(reader.db.execute("SELECT value FROM manifest WHERE key='source_observations'").fetchone()[0]);histories=[r for r in observations if r['type']=='history'];assert histories
 assert reader.read_source('history.py')==b'def broken(:\n pass\n'
 assert reader.read_source('history.py',hashlib.sha256(sources['history.py']).hexdigest())==sources['history.py']
 native_files={}
 for p in sorted(graph.atlas_dir.rglob('*')):
  if p.is_file() and (p==graph.db_path or p.parent.name=='source-snapshots'):
   raw=p.read_bytes();native_files[str(p.relative_to(graph.atlas_dir))]={'base64':base64.b64encode(raw).decode(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
config=Config(repo=repo,memex_root=Path('/tmp/aci-s17-baseline-memex'),max_calls_per_minute=0)
enforcement,memex,factory=build_server_state(config)
result=asyncio.run(call_tool_result('graph_query',{'schema_version':'atlas.graph.v1','op':'retrieve','text':'unique task phrase'},config,enforcement,memex,factory));assert result.isError
factory.close();artifact=out/'native8-portable.jsonl';graph.export_jsonl(artifact);graph.close()
fixture=root/'mcp-server/tests/fixtures/s17/native8.json.gz';fixture.parent.mkdir(parents=True,exist_ok=True)
obj={'format':'aci-s17-native8-fixture.v1','original_repository':str(repo),'schema_epoch':8,'generation_id':generation,'snapshot_id':snapshot,'native_files':native_files,'source_files':{p.name:base64.b64encode(p.read_bytes()).decode() for p in sorted(repo.glob('*.py'))},'historical_source':{name:base64.b64encode(data).decode() for name,data in sources.items()}}
raw=(json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode();packed=gzip.compress(raw,mtime=0);fixture.write_bytes(packed);assert gzip.decompress(fixture.read_bytes())==raw
modules={str(p.relative_to(root/'mcp-server/src/atlas_aci')):base64.b64encode(p.read_bytes()).decode() for p in sorted((root/'mcp-server/src/atlas_aci').rglob('*.py'))};archive=gzip.compress((json.dumps(modules,sort_keys=True,separators=(',',':'))+'\n').encode(),mtime=0);(out/'pre-edit-source.json.gz').write_bytes(archive)
record={'schema_epoch':8,'repository':str(repo),'g1':g1,'g2':g2,'generation_id':generation,'snapshot_id':snapshot,'held_old_generation':old_gen,'held_old_snapshot':old_snapshot,'held_source_bytes_after_publication':True,'held_pack_closed':True,'history_bundles':len(histories),'status':status,'unsupported_retrieve':result.model_dump(mode='json',by_alias=True,exclude_none=True),'fixture':{'path':str(fixture.relative_to(root)),'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_bytes':len(packed),'compressed_sha256':hashlib.sha256(packed).hexdigest(),'gzip_mtime':0},'source_archive_count':len(modules),'source_archive_sha256':hashlib.sha256(archive).hexdigest(),'portable':{'path':str(artifact.relative_to(root)),'bytes':artifact.stat().st_size,'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()},'actual_import':__import__('atlas_aci').__file__}
(out/'baseline-checkpoint.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({'generation_id':generation,'snapshot_id':snapshot,'history_bundles':len(histories),'native_files':len(native_files),'source_modules':len(modules),'red':record['unsupported_retrieve']}))
