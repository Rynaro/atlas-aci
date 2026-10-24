
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
