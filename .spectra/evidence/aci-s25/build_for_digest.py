"""Build a fixture with the code on PYTHONPATH (base or candidate) into a graph root; print db path.

    build_for_digest.py FIXTURE_PROJECT OUT_DIR [frameworks] [table_map_file]
"""
import shutil
import sys
from pathlib import Path

from atlas_aci.codegraph import CodeGraph

fixture, out = Path(sys.argv[1]), Path(sys.argv[2])
frameworks = tuple(f for f in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if f)
table_map = Path(sys.argv[4]).read_bytes() if len(sys.argv) > 4 else None
repo = out / "repo"
shutil.copytree(fixture, repo)
options = {"frameworks": frameworks}
if table_map is not None:
    options["rails_table_map"] = table_map
graph = CodeGraph(repo, graph_root=out / "g", **options)
graph.build()
print(graph.db_path)
