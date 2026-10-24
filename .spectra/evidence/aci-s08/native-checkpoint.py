"""Actual host writer/readonly Docker reader graph+pack lifetime checkpoint."""
import hashlib,json,os,selectors,subprocess,tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph

ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
base=Path(tempfile.mkdtemp(prefix='aci-s08-native-',dir='/tmp'));repo=(base/'repo').resolve();repo.mkdir();graphs=base/'graphs';graphs.mkdir(mode=0o777);graphs.chmod(0o777)
old=b'def old_target():\n return 1\n';(repo/'a.py').write_bytes(old)
graph=CodeGraph(repo,graph_root=graphs);graph.build()
code='''import errno,json,os,sys
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
assert os.geteuid()==10001
try:Path(sys.argv[1]+'/negative-source-write').write_text('forbidden')
except OSError as e:assert e.errno==errno.EROFS;error=e.errno
else:raise AssertionError('source writable')
g=CodeGraph(Path(sys.argv[1]),graph_root=Path('/graphs'),read_only=True)
with g.reader_lease() as reader:
 print(json.dumps({'ready':True,'token':reader.snapshot_id,'definitions':reader.search_symbol('old_target')['definitions'],'source_errno':error,'uid':os.geteuid()}),flush=True)
 assert sys.stdin.readline().strip()=='READ'
 print(json.dumps({'bytes':reader.read_source('a.py').decode(),'definitions':reader.search_symbol('old_target')['definitions']}),flush=True)
g.close()
'''
command=['docker','run','--rm','-i','--read-only','--network','none','-v',str(repo)+':'+str(repo)+':ro','-v',str(graphs)+':/graphs:ro','--entrypoint','python','atlas-aci-s08-checkpoint','-u','-c',code,str(repo)]
record={'command':command,'source':str(repo),'graphs':str(graphs),'source_before':hashlib.sha256((repo/'a.py').read_bytes()).hexdigest(),'image':'atlas-aci-s08-checkpoint'}
proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
 selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
 assert selector.select(30),'container acquisition did not respond'
 line=proc.stdout.readline();record['first_stdout']=line
 assert line,proc.stderr.read()
 record['acquired']=json.loads(line)
 for number in range(3):graph.build(overlays={'a.py':f'def new_{number}(): pass\n'.encode()})
 proc.stdin.write('READ\n');proc.stdin.flush();out,err=proc.communicate(timeout=30);record.update(exit_code=proc.returncode,stdout=out,stderr=err)
 assert proc.returncode==0,err
 answer=json.loads(out);assert answer['bytes']==old.decode() and answer['definitions']
 assert hashlib.sha256((repo/'a.py').read_bytes()).hexdigest()==record['source_before']
 record['passed']=True
finally:
 if proc.poll() is None:proc.kill();proc.wait()
 record['exit_code']=proc.returncode
 (HERE/'native-checkpoint.json').write_text(json.dumps(record,indent=2)+'\n');graph.close()
print(json.dumps(record,indent=2))
