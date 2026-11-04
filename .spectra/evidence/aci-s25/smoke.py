import json, shutil, sys, tempfile
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev
fx = Path("/ws/mcp-server/tests/fixtures/rails_models")
work = Path(tempfile.mkdtemp()); repo = work/"repo"
shutil.copytree(fx/"project", repo)
g = CodeGraph(repo, graph_root=work/"g", frameworks=("rails_models",), rails_table_map=(fx/"table-map.json").read_bytes())
g.build()
with g.reader_lease() as l:
    db = l.db
    names = {}
    for (p,) in db.execute("SELECT payload FROM entities"):
        e = json.loads(p); names[e["id"]] = (e["path"], e["name"])
    rows = []
    for (p,) in db.execute("SELECT payload FROM assertions WHERE json_extract(payload,'$.relation') IN ('association','model_table','migration_mentions')"):
        a = json.loads(p)
        pr = ev.get(db,"evidence",a["evidence_id"]); oc = ev.get(db,"occurrence",pr["occurrence_id"])
        rows.append((oc["path"], oc["start_byte"], a["relation"], oc["role"], a["resolution"], a["unresolved_reason"], names.get(a["target_entity_id"]), [names[c] for c in a["candidate_ids"]], a["raw_target"]))
    for r in sorted(rows): print(r)
    print(len(rows))
    print(db.execute("SELECT value FROM manifest WHERE key='rails_models'").fetchone()[0])
