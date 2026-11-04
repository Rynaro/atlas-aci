"""Day-1 (c): the target-less carve-out on a hand-built database (scratch copy of the build)."""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import tree_sitter_language_pack as pack

from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev

fixture = Path(sys.argv[1])
work = Path(tempfile.mkdtemp())
repo = work / "repo"
shutil.copytree(fixture, repo)
graph = CodeGraph(repo, graph_root=work / "g")
graph.build()
db_copy = work / "copy.db"
shutil.copy(graph.db_path, db_copy)
conn = sqlite3.connect(db_copy)
conn.row_factory = sqlite3.Row
path = "app/models/profile.rb"
source = (repo / path).read_bytes()
tree = pack.get_parser("ruby").parse(source)
statement = tree.root_node.named_children[0].child_by_field_name("body").named_children[0]
cls = conn.execute(
    "SELECT entity_id FROM symbols WHERE path=? AND kind='class'", (path,)
).fetchone()[0]
native = next(
    json.loads(p)["id"]
    for (p,) in conn.execute("SELECT payload FROM producers")
    if "codegraph_sha256" in (json.loads(p)["fingerprint"] or {})
)
producer = ev.producer(
    {
        "rails_models": {
            "adapter": "rails-models.v1",
            "adapter_sha256": "a" * 64,
            "graph_producer_id": native,
            "table_map_sha256": None,
        }
    }
)
occ = ev.occurrence(statement, path, "ruby", source, cls, "rails_model", "rails_model:table:literal")
proof = ev.provenance(occ, producer["id"])
raw = ev.canonical({"source": "literal", "table": "user_profiles"})
result = {}


def make(relation, resolution, target):
    return ev.assertion(cls, target, relation, proof["id"], resolution, raw, [], None)


for name, relation, resolution, target in [
    ("model_table EXTRACTED no target", "model_table", "EXTRACTED", None),
    ("migration_mentions EXTRACTED no target", "migration_mentions", "EXTRACTED", None),
    ("model_table INFERRED no target", "model_table", "INFERRED", None),
    ("model_table EXTRACTED with target", "model_table", "EXTRACTED", cls),
    ("association EXTRACTED", "association", "EXTRACTED", cls),
    ("association INFERRED no target", "association", "INFERRED", None),
    ("routes_to INFERRED no target", "routes_to", "INFERRED", None),
    ("call INFERRED no target", "call", "INFERRED", None),
    ("call EXTRACTED no target", "call", "EXTRACTED", None),
]:
    try:
        ev.validate_record(make(relation, resolution, target))
        result[name] = "accepted"
    except ValueError as exc:
        result[name] = "rejected: " + str(exc)

ev.put(conn, producer)
ev.put(conn, occ, path)
ev.put(conn, proof, path)
ev.put(conn, make("model_table", "EXTRACTED", None), path)
ev.validate_database(conn)
result["validate_database on hand-built db"] = "pass"
shutil.rmtree(work / "g" / "unused", ignore_errors=True)
graph._validate_evidence_projections(conn)
result["_validate_evidence_projections on hand-built db"] = "pass"
print(json.dumps(result, indent=1, sort_keys=True))
