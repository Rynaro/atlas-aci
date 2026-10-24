"""Diagnostic only: preserve all actual owner/projection inconsistencies."""
import hashlib,json
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev
repo=Path('/tmp/aci-s07-approved2-probe-inputs/solidus');out=Path(__file__).resolve().parent
original=CodeGraph._validate_evidence_projections

def inspect(self,conn):
    wrong=[]
    for row in conn.execute('SELECT * FROM edges ORDER BY source_path,source_line,source_col'):
        assertion=ev.get(conn,'assertion',row['assertion_id']);source=ev.get(conn,'entity',assertion['source_entity_id'])
        if source['kind']!='file' and (source['name'],source['kind'])!=(row['source_name'],row['source_kind']):
            proof=ev.get(conn,'evidence',assertion['evidence_id']);occurrence=ev.get(conn,'occurrence',proof['occurrence_id'])
            reference=conn.execute('SELECT binding FROM refs WHERE path=? AND line=? AND col=? AND callee_name=?',(row['source_path'],row['source_line'],row['source_col'],row['callee_name'])).fetchone()
            lines=(repo/row['source_path']).read_text().splitlines();line=row['source_line']
            wrong.append({'edge':dict(row),'source_entity':source,'assertion':assertion,'occurrence':occurrence,'binding':json.loads(reference[0]) if reference else None,'source_excerpt':lines[max(0,line-4):line+3]})
    (out/'owner-counterexamples-all.json').write_text(json.dumps(wrong,indent=2)+'\n')
    print(json.dumps({'owner_mismatch_count':len(wrong),'paths':sorted({r['edge']['source_path'] for r in wrong})}),flush=True)
    return original(self,conn)
CodeGraph._validate_evidence_projections=inspect
assert hashlib.sha256(Path(__import__('atlas_aci.codegraph',fromlist=['']).__file__).read_bytes()).hexdigest()=='d79b99f54bdd48bc3ba999f85719858aa6301d61f5e7d2664380914e7d8d2024'
CodeGraph(repo).build()
