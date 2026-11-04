"""Day-1 (b): class node span vs definition occurrence, namespaces, skip pattern (base code)."""
import json
import shutil
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
parser = pack.get_parser("ruby")
result = {"classes": [], "observed_skipped": None}
with graph.reader_lease() as lease:
    db = lease.db
    rows = db.execute(
        "SELECT s.path, s.name, s.kind, s.entity_id, s.binding, s.occurrence_id FROM symbols s "
        "WHERE s.lang='ruby' AND s.kind IN ('class','module') ORDER BY s.path, s.line_start, s.id"
    ).fetchall()
    for path, name, kind, entity_id, binding, occurrence_id in rows:
        occ = ev.get(db, "occurrence", occurrence_id)
        source = (repo / path).read_bytes()
        tree = parser.parse(source)
        match = []
        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            if n.type in {"class", "module"} and (n.start_byte, n.end_byte) == (occ["start_byte"], occ["end_byte"]):
                match.append(n.type)
            stack.extend(n.named_children)
        result["classes"].append(
            {
                "path": path,
                "name": name,
                "kind": kind,
                "namespace": json.loads(binding).get("namespace"),
                "owner": json.loads(binding).get("owner"),
                "span_matches_node": match,
                "occ_kind": occ["kind"],
                "entity_is_occ_entity": occ["entity_id"] == entity_id,
            }
        )
    paths = {r[0] for r in db.execute("SELECT path FROM files")}
    result["observed_skipped"] = "app/models/storage/vault.rb" not in paths
    manifest = dict(db.execute("SELECT key, value FROM manifest").fetchall())
    inputs = json.loads(manifest["generation_inputs"])
    result["generation_inputs_keys"] = sorted(inputs)
    result["generation_inputs_snapshot"] = inputs.get("snapshot")
    result["reader_snapshot_id"] = lease.snapshot_id
print(json.dumps(result, indent=1, sort_keys=True))
