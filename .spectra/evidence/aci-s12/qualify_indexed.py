"""Actual production cursor SQL/work/interrupt transcript, after authenticated lease."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from atlas_aci.codegraph import CodeGraph
from atlas_aci.query.indexed import IndexedReader, QueryStopped, OUTGOING_SQL, INCOMING_SQL, NAME_SQL

out=Path(__file__).resolve().parent
repo=Path(tempfile.mkdtemp(prefix='aci-s12-cursor-')).resolve()
(repo/'a.py').write_text('def A():\n B(); B()\ndef B():\n A()\n')
g=CodeGraph(repo);g.build();start=time.monotonic()
with g.reader_lease() as r:
 validation=time.monotonic()-start
 fd=r._source_lease.fd
 seed=r.db.execute(NAME_SQL,('A','')).fetchone()[0]
 plans={name:[row[3] for row in r.db.execute('EXPLAIN QUERY PLAN '+sql,args)] for name,sql,args in [('out',OUTGOING_SQL,(seed,'call','')),('in',INCOMING_SQL,(seed,'call','')),('name',NAME_SQL,('A',''))]}
 assert all(all('SCAN' not in row and 'TEMP B-TREE' not in row for row in plan) for plan in plans.values())
 trace=[];r.db.set_trace_callback(trace.append)
 with IndexedReader(r,max_examined_edges=2,deadline_ms=2000) as query:
  actual_seed=query.name_candidates('A')[0];first=query.edge(actual_seed['id'],'call','outgoing','');second=query.edge(actual_seed['id'],'call','outgoing',first['id'])
  try:query.edge(actual_seed['id'],'call','outgoing',second['id']);raise AssertionError('edge cap overrun')
  except QueryStopped as exc:assert exc.reason=='examined_edge_limit'
  counts=dict(query.counts)
 r.db.set_trace_callback(None)
 assert len(trace)==4 and counts['edge_rows']==counts['examined_edges']==2
 deadline=IndexedReader(r,max_examined_edges=1,deadline_ms=100)
 try:
  with deadline:deadline._one('WITH RECURSIVE x(n) AS (VALUES(0) UNION ALL SELECT n+1 FROM x WHERE n<10000000) SELECT sum(n) FROM x',(),'hydration')
 except QueryStopped as exc:
  assert exc.reason=='deadline' and isinstance(exc.__cause__,sqlite3.OperationalError)
  actual_code=exc.__cause__.sqlite_errorcode;assert actual_code==sqlite3.SQLITE_INTERRUPT
 else:raise AssertionError('real deadline not interrupted')
 assert r.db.execute('WITH RECURSIVE x(n) AS (VALUES(0) UNION ALL SELECT n+1 FROM x WHERE n<1000) SELECT sum(n) FROM x').fetchone()[0]==500500
 record={'native_epoch':r._lease_schema_epoch,'sqlite_runtime':sqlite3.sqlite_version,'lease_validation_seconds':validation,'validation_cost_outside_traversal_deadline':True,'plans':plans,'actual_navigation_trace':trace,'counts':counts,'distinct_assertion_ids':[first['id'],second['id']],'deadline_counts':deadline.counts,'actual_sqlite_interrupt_code':actual_code,'owned_hook_cleared_connection_usable':not deadline._installed}
try:os.fstat(fd)
except OSError:record['actual_pack_fd_closed']=True
else:raise AssertionError('pack FD leak')
g.close();(out/'indexed-checkpoint.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
