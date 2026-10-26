import sqlite3,tempfile,pathlib,json,hashlib,os
root=pathlib.Path(tempfile.mkdtemp(prefix='aci-s17-fts-integrity-'));results=[]
for kind in ('internal','external'):
 p=root/(kind+'.db');w=sqlite3.connect(p)
 if kind=='internal':w.execute('CREATE VIRTUAL TABLE docs USING fts5(text)');w.execute("INSERT INTO docs(rowid,text) VALUES(1,'alpha phrase')")
 else:
  w.execute('CREATE TABLE source(id INTEGER PRIMARY KEY,text TEXT)');w.execute("CREATE VIRTUAL TABLE docs USING fts5(text,content='source',content_rowid='id')");w.execute("INSERT INTO source VALUES(1,'alpha phrase')");w.execute("INSERT INTO docs(rowid,text) VALUES(1,'alpha phrase')")
 w.commit();w.close()
 def measure(label):
  before=(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns);r=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);r.execute('PRAGMA query_only=ON');rows={}
  for sql in ('PRAGMA integrity_check','PRAGMA integrity_check(docs)','PRAGMA quick_check'):
   try:rows[sql]=r.execute(sql).fetchall()
   except sqlite3.Error as e:rows[sql]={'error':str(e),'code':e.sqlite_errorcode}
  try:r.execute("INSERT INTO docs(docs,rank) VALUES('integrity-check',1)");raise AssertionError('write unexpectedly admitted')
  except sqlite3.Error as e:rows['insert_integrity']={'error':str(e),'code':e.sqlite_errorcode}
  if label=='corrupt':
   mem=sqlite3.connect(':memory:');r.backup(mem)
   try:mem.execute("INSERT INTO docs(docs,rank) VALUES('integrity-check',1)");rows['private_memory_check']='accepted'
   except sqlite3.Error as e:rows['private_memory_check']={'error':str(e),'code':e.sqlite_errorcode}
   mem.close()
  r.close();assert before==(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns);results.append({'kind':kind,'state':label,'checks':rows,'original_unchanged':True})
 measure('healthy');w=sqlite3.connect(p)
 if kind=='internal':w.execute("UPDATE docs_content SET c0='different words' WHERE id=1")
 else:w.execute("UPDATE source SET text='different words' WHERE id=1")
 w.commit();w.close();measure('corrupt')
out={'sqlite_version':sqlite3.sqlite_version,'scratch':str(root),'results':results};(root/'result.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2));print('result_sha256',hashlib.sha256((root/'result.json').read_bytes()).hexdigest())
