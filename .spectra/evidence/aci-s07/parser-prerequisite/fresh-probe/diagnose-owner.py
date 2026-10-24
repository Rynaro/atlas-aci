"""Diagnostic only: retain the actual first mismatched parser/projection row."""
import hashlib,json
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev
repo=Path('/tmp/aci-s07-approved-probe-inputs/solidus')
original=CodeGraph._validate_evidence_projections
out=Path(__file__).resolve().parent

def inspect(self,conn):
    for row in conn.execute('SELECT * FROM edges ORDER BY source_path,source_line,source_col'):
        assertion=ev.get(conn,'assertion',row['assertion_id'])
        source=ev.get(conn,'entity',assertion['source_entity_id'])
        if source['kind']!='file' and (source['name'],source['kind'])!=(row['source_name'],row['source_kind']):
            proof=ev.get(conn,'evidence',assertion['evidence_id'])
            occurrence=ev.get(conn,'occurrence',proof['occurrence_id'])
            data={'edge':dict(row),'source_entity':source,'assertion':assertion,'occurrence':occurrence,'proof':proof}
            (out/'owner-counterexample.json').write_text(json.dumps(data,indent=2)+'\n')
            print(json.dumps(data),flush=True)
            break
    return original(self,conn)
CodeGraph._validate_evidence_projections=inspect
assert hashlib.sha256(Path(__import__('atlas_aci.codegraph',fromlist=['']).__file__).read_bytes()).hexdigest()=='59c2c347d5bfdcbe72a99998b0cbe641db6f2167a1eee51a17026805175e163f'
CodeGraph(repo).build()
