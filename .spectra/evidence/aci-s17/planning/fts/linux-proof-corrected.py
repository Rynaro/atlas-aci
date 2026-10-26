import sqlite3,pathlib,json,os
out={'sqlite_version':sqlite3.sqlite_version,'uid':os.geteuid(),'results':[]}
for kind in ('internal','external'):
 for state in ('healthy','corrupt'):
  p=pathlib.Path('/proof')/(kind+('-healthy' if state=='healthy' else '')+'.db');before=(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns)
  r=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);r.execute('PRAGMA query_only=ON');result=r.execute('PRAGMA integrity_check(docs)').fetchall()
  mem=sqlite3.connect(':memory:');r.backup(mem)
  try:mem.execute("INSERT INTO docs(docs,rank) VALUES('integrity-check',1)");m='ok'
  except sqlite3.Error as e:m={'code':e.sqlite_errorcode,'error':str(e)}
  r.close();mem.close();assert before==(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns)
  out['results'].append({'kind':kind,'state':state,'readonly_pragma':result,'private_memory_rank1':m,'source_bytes_mode_mtime_unchanged':True})
print(json.dumps(out,indent=2))
assert out['uid']==10001
assert all(x['readonly_pragma']==[('ok',)] for x in out['results'] if x['state']=='healthy')
assert all(x['private_memory_rank1']['code']==267 for x in out['results'] if x['state']=='corrupt')
