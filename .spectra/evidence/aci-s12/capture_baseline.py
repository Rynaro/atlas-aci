"""Capture actual pre-S12 producer, native7 source/history and portable evidence."""
import asyncio
import base64
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from atlas_aci.codegraph import CodeGraph, SCHEMA_EPOCH
from atlas_aci.config import Config
from atlas_aci.server import build_server_state, call_tool_result

root=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent
fixtures=root/'mcp-server/tests/fixtures/s12';fixtures.mkdir(exist_ok=True)
assert SCHEMA_EPOCH==7
sources={}
for name in subprocess.check_output(['git','ls-files','--','mcp-server/src/atlas_aci'],cwd=root,text=True).splitlines():
 if name.endswith('.py'):
  raw=(root/name).read_bytes();sources[name.removeprefix('mcp-server/src/')]={'base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}

def save(name,value):
 raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode();packed=gzip.compress(raw,mtime=0);path=fixtures/name;assert not path.exists();path.write_bytes(packed)
 return {'path':str(path.relative_to(root)),'uncompressed_bytes':len(raw),'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_bytes':len(packed),'compressed_sha256':hashlib.sha256(packed).hexdigest(),'gzip_mtime':0}
source_artifact=save('baseline-source.json.gz',{'base_commit':'69fe066f573f29651fb53ad0dac65ae13836b752','files':sources})
repo=Path(tempfile.mkdtemp(prefix='aci-s12-native7-')).resolve()
files={'a.py':'def A():\n B(); B()\ndef B():\n C()\ndef C():\n A()\n','history.py':'def retired():\n pass\ndef stale_caller():\n retired()\n','types.rb':'class Base\nend\nclass Child < Base\nend\n'}
for name,text in files.items():(repo/name).write_text(text)
graph=CodeGraph(repo);graph.build();(repo/'history.py').write_text('def broken(:\n pass\n');graph.build()
with graph.reader_lease() as reader:
 assert reader.published_status()['completeness']['state']=='partial'
 assert any(r['type']=='history' for r in reader.observation_records())
 identity={'generation_id':reader.generation_id,'snapshot_id':reader.snapshot_id,'source_bytes':{p.name:base64.b64encode(p.read_bytes()).decode() for p in repo.iterdir() if p.is_file()},'captured_original_source':files}
 queries={q:reader.query(q) for q in ['definitions_of:A','callers_of:B','subclasses_of:Base','callers_of:retired']}
 graph_meta=dict(reader.db.execute('SELECT key,value FROM manifest'))
 signature=reader._schema_signature(reader.db)
 plans=reader.db.execute("EXPLAIN QUERY PLAN SELECT payload FROM assertions WHERE json_extract(payload,'$.source_entity_id')=? AND json_extract(payload,'$.relation')=? ORDER BY id LIMIT 1",('x','call')).fetchall()
 files_native={str(p.relative_to(graph.atlas_dir)):base64.b64encode(p.read_bytes()).decode() for p in [graph.db_path,*sorted((graph.atlas_dir/'source-snapshots').rglob('*'))] if p.is_file()}
fixture=save('native7.json.gz',{'repository':str(repo),'native_epoch':7,'files':files_native,'identity':identity,'queries':queries,'generation_inputs':json.loads(graph_meta['generation_inputs'])})
portable=fixtures/'baseline-evidence.jsonl';graph.export_jsonl(portable)
config=Config(repo=repo,memex_root=repo.parent/(repo.name+'-memex'))
enforcement,memex,factory=build_server_state(config)
result=asyncio.run(call_tool_result('graph_query',{'schema_version':'atlas.graph.v1','op':'neighborhood','seed':{'name':'A'}},config,enforcement,memex,factory));assert result.isError and result.structuredContent['error']['code']=='INVALID_OPERATION'
record={'base_commit':'69fe066f573f29651fb53ad0dac65ae13836b752','actual_source':str(__import__('atlas_aci.codegraph',fromlist=['']).__file__),'source_files':len(sources),'source_artifact':source_artifact,'native_fixture':fixture,'original_repository':str(repo),'identity':identity,'sqlite_version':sqlite3.sqlite_version,'native_signature':signature,'baseline_query_plan':[list(row) for row in plans],'baseline_typed_result':result.model_dump(mode='json',exclude_none=True),'portable':{'path':str(portable.relative_to(root)),'bytes':portable.stat().st_size,'sha256':hashlib.sha256(portable.read_bytes()).hexdigest()},'packages':{name:importlib.metadata.version(name) for name in ['mcp','tree-sitter','tree-sitter-language-pack']}}
(fixtures/'baseline-provenance.json').write_text(json.dumps(record,indent=2)+'\n');(out/'baseline-checkpoint.json').write_text(json.dumps(record,indent=2)+'\n')
factory.close();graph.close();print(json.dumps({'native_epoch':7,'source_files':len(sources),'genuine_history':True,'typed_baseline_error':'INVALID_OPERATION','portable_bytes':portable.stat().st_size,'fixture':fixture}))
