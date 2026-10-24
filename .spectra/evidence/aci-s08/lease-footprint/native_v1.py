"""Genuine archived v1 native graph+source read on final offline RO Linux image."""
import base64,gzip,hashlib,json,os,subprocess,tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent
fixture=json.loads(gzip.decompress((HERE/'v1-fixture.json.gz').read_bytes()));base=Path(tempfile.mkdtemp(prefix='aci-s08-v1-native-',dir='/tmp')).resolve();graphs=base/'graphs';source=base/'empty-source';source.mkdir()
g=CodeGraph(Path(fixture['repository']),graph_root=graphs,read_only=True)
for item in fixture['files']:
 if not item['path'].startswith('.atlas/'):continue
 raw=base64.b64decode(item['base64']);assert hashlib.sha256(raw).hexdigest()==item['sha256'];target=g.atlas_dir/item['path'].removeprefix('.atlas/');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
def inventory():return {str(p.relative_to(graphs)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size,'mode':p.stat().st_mode,'mtime_ns':p.stat().st_mtime_ns} for p in sorted(graphs.rglob('*')) if p.is_file()}
before=inventory();history=next(r for r in fixture['expected']['observations'] if r['type']=='history');old_hash=next(r['source_sha256'] for r in history['records'] if r['type']=='occurrence')
code='''import errno,json,os,sys,sqlite3
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
assert os.geteuid()==10001
repo=Path(sys.argv[1]);assert list(repo.iterdir())==[]
try:(repo/'sentinel').write_bytes(b'forbidden')
except OSError as exc:assert exc.errno==errno.EROFS;error=exc.errno
else:raise AssertionError('source writable')
g=CodeGraph(repo,graph_root=Path('/graphs'),read_only=True);base=len(os.listdir('/proc/self/fd'))
with g.reader_lease() as reader:
 assert len(os.listdir('/proc/self/fd'))==base+2
 assert reader.snapshot_id==sys.argv[2]
 assert reader._source_lease.manifest['format']=='atlas-source-snapshot.v1'
 assert reader.read_source('a.py')==b'def broken(:\\n pass\\n'
 assert b'def target' in reader.read_source('a.py',sys.argv[3])
 fd=reader._source_lease.fd;db=reader.db
 assert reader.epoch_ok()
assert len(os.listdir('/proc/self/fd'))==base
try:os.fstat(fd)
except OSError:pass
else:raise AssertionError('pack fd leaked')
try:db.execute('SELECT 1')
except sqlite3.ProgrammingError:pass
else:raise AssertionError('SQLite leaked')
g.close();assert list(repo.iterdir())==[]
print(json.dumps({'uid':os.geteuid(),'source_write_errno':error,'version':'v1','original_snapshot_id':sys.argv[2],'current_and_historical_bytes_exact':True,'source_empty_no_live_fallback':True,'fd_and_sqlite_closed':True}))
'''
command=['docker','run','--rm','--read-only','--network','none','-v',str(source)+':'+fixture['repository']+':ro','-v',str(graphs)+':/graphs:ro','--entrypoint','python','atlas-aci-s08-footprint','-c',code,fixture['repository'],fixture['expected']['snapshot_id'],old_hash]
result=subprocess.run(command,capture_output=True,text=True,timeout=30);(HERE/'native-v1.stderr.log').write_text(result.stderr);after=inventory()
record={'command':command,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'image_id':'sha256:da089d0b288e9a77ea14434dfffdc86ee1278916ac502cd35a56a19b64290858','before':before,'after':after,'graph_store_bytes_metadata_unchanged':before==after,'source_empty_after':not list(source.iterdir())}
(HERE/'native-v1.json').write_text(json.dumps(record,indent=2)+'\n');assert result.returncode==0,result.stderr;assert before==after and not list(source.iterdir());print(result.stdout,flush=True)
