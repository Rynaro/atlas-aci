
import os,sys,json,hashlib,subprocess,selectors,pathlib,time,uuid
ROOT=pathlib.Path(sys.argv[1]);IMAGE='sha256:d01e6c42b3a84b3047e14c270566defcd5c9361e32e2de6a5168a4a36c714162'
OLD=b'G1-PACK\\n'+bytes(range(256))*16384+b'G1-END';NEW=b'G2-PACK\\n'+b'different-content'*8192+b'G2-END';H=lambda b:hashlib.sha256(b).hexdigest()
reader=r"""
import os,sys,json,hashlib,fcntl,pathlib
p=pathlib.Path(sys.argv[1]);expected=sys.argv[2];samples=json.loads(sys.argv[3]);mode=sys.argv[4]
fdroot='/proc/self/fd' if pathlib.Path('/proc/self/fd').exists() else '/dev/fd';base=len(os.listdir(fdroot));fd=None;result={'uid':os.geteuid(),'mode':mode}
def say(x):print(json.dumps(x),flush=True)
try:
 if mode.startswith('race'):
  say({'stage':'before_open','uid':os.geteuid()});sys.stdin.readline()
 try:fd=os.open(p,os.O_RDONLY)
 except FileNotFoundError:
  result['status']='missing_retry';say(result);sys.exit(0)
 old=os.fstat(fd);result['data_fd_delta']=len(os.listdir(fdroot))-base;result['access_mode']=fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE
 try:os.write(fd,b'x')
 except OSError as e:result['fd_write_errno']=e.errno
 if not mode.startswith('race'):
  say({'stage':'opened','uid':os.geteuid(),'size':old.st_size,'data_fd_delta':result['data_fd_delta']});sys.stdin.readline()
 if mode=='exit':os._exit(0)
 result['path_exists_after_publication']=p.exists();result['samples_ok']=all(hashlib.sha256(os.pread(fd,length,offset)).hexdigest()==want for offset,length,want in samples)
 h=hashlib.sha256();offset=0
 while True:
  b=os.pread(fd,262144,offset)
  if not b:break
  h.update(b);offset+=len(b)
 result['full_sha256']=h.hexdigest();result['bytes_read']=offset;result['eof_ok']=os.pread(fd,1,old.st_size)==b'';result['stat_unchanged']=old==os.fstat(fd);result['status']='ok' if h.hexdigest()==expected and result['samples_ok'] and result['eof_ok'] else 'hash_mismatch_retry'
 if mode=='error':raise RuntimeError('controlled consumer error')
except RuntimeError as e:result['consumer_error']=str(e)
finally:
 if fd is not None:
  os.close(fd)
  try:os.fstat(fd)
  except OSError as e:result['closed_errno']=e.errno
  result['fd_count_restored']=len(os.listdir(fdroot))==base
if 'status' in result:say(result)
"""
writer=r"""
import pathlib,os,sys,json
r=pathlib.Path(sys.argv[1]);replacement=sys.argv[2]=='replace';data=b'G2-PACK\\n'+b'different-content'*8192+b'G2-END'
(r/'pack-g2').write_bytes(data);os.chmod(r/'pack-g2',0o644);(r/'current.tmp').write_text('pack-g2');os.replace(r/'current.tmp',r/'current');(r/'pack-g1').unlink()
if replacement:
 (r/'replacement.tmp').write_bytes(data);os.chmod(r/'replacement.tmp',0o644);os.replace(r/'replacement.tmp',r/'pack-g1')
print(json.dumps({'published':True,'old_path_exists':(r/'pack-g1').exists()}))
"""
(ROOT/'reader.py').write_text(reader);(ROOT/'writer.py').write_text(writer)
results=[]
for label,where,actor,replace,mode in [('mac_native','host','host',False,'normal'),('linux_native','docker','docker',False,'normal'),('host_docker_replaced','docker','host',True,'normal'),('host_docker_error','docker','host',False,'error'),('mac_exit','host','host',False,'exit'),('linux_exit','docker','host',False,'exit'),('mac_race_missing','host','host',False,'race_missing'),('linux_race_missing','docker','host',False,'race_missing'),('linux_race_replaced','docker','host',True,'race_replaced')]:
 case=ROOT/label;case.mkdir();case.chmod(0o777);pack=case/'pack-g1';pack.write_bytes(OLD);pack.chmod(0o644);(case/'current').write_text('pack-g1');samples=[[offset,length,H(OLD[offset:offset+length])] for offset,length in [(0,31),(4093,91),(len(OLD)//2,127),(len(OLD)-37,37),(len(OLD)+7,16)]]
 args=[str(pack),H(OLD),json.dumps(samples),mode];name='aci-s08-pack-'+uuid.uuid4().hex[:10]
 if where=='host':argv=[sys.executable,'-u',str(ROOT/'reader.py'),*args]
 else:argv=['docker','run','--rm','-i','--name',name,'--read-only','--network','none','-v',f'{ROOT}:{ROOT}:ro','--entrypoint','python',IMAGE,'-u',str(ROOT/'reader.py'),*args]
 p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  with selectors.DefaultSelector() as s:
   s.register(p.stdout,selectors.EVENT_READ);assert s.select(15),label+' reader deadline'
  first=json.loads(p.stdout.readline());assert first['stage']==('before_open' if mode.startswith('race') else 'opened')
  if actor=='host':wargv=[sys.executable,str(ROOT/'writer.py'),str(case),'replace' if replace else 'absent']
  else:wargv=['docker','run','--rm','--read-only','--network','none','-v',f'{ROOT}:{ROOT}','--entrypoint','python',IMAGE,str(ROOT/'writer.py'),str(case),'replace' if replace else 'absent']
  pub=subprocess.run(wargv,capture_output=True,text=True,timeout=15);assert pub.returncode==0,pub.stderr
  out,err=p.communicate('continue\\n',timeout=15);assert p.returncode==0,(label,err)
  answer=json.loads(out.splitlines()[-1]) if out.strip() else {'status':'process_exit_closed_by_os'}
  assert answer['status']==({'race_missing':'missing_retry','race_replaced':'hash_mismatch_retry','exit':'process_exit_closed_by_os'}.get(mode,'ok')),(label,answer)
  if mode not in ('exit','race_missing'):assert answer['data_fd_delta']==1 and answer['closed_errno']==9 and answer['fd_count_restored'],answer
  result={'case':label,'reader_argv':argv,'writer_argv':wargv,'ready':first,'result':answer,'reader_exit':p.returncode,'writer_exit':pub.returncode,'stderr':err,'original_sha256':H(OLD),'replacement_sha256':H(NEW),'original_bytes':len(OLD)};results.append(result);print(json.dumps({'case':label,'result':answer}),flush=True)
 finally:
  if p.poll() is None:
   if where=='docker':subprocess.run(['docker','stop',name],capture_output=True,timeout=15)
   else:p.kill();p.communicate(timeout=10)
(ROOT/'results.json').write_text(json.dumps({'image':IMAGE,'root':str(ROOT),'cases':results,'scripts':{n:H((ROOT/n).read_bytes()) for n in ['proof.py','reader.py','writer.py']}},indent=2)+'\\n')
print('RESULTS '+str(ROOT/'results.json'),flush=True)
