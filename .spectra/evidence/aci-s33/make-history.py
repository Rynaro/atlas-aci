"""Helper run INSIDE the production image by prod-scenario.sh (stdlib only; no atlas_aci import).

  make-history.py world DEST        build the main world (plumbing) in DEST, print label->sha JSON
  make-history.py future DEST SHA   write one commit OBJECT on top of SHA (no ref, no checkout)
  make-history.py get FILE KEY...   print one JSON field of FILE (ints index lists)
  make-history.py golden            print the golden C14 sha
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("/repo")
sys.path.insert(0, str(REPO / "mcp-server/tests/slices"))
import test_s33_support as sup  # noqa: E402

command = sys.argv[1]
if command == "world":
    world = sup.build_world("main", Path(sys.argv[2]))
    golden = json.loads((sup.FX / "expected-records.json").read_text())["commit_ids"]
    assert world.shas == golden, "the builder drifted from the golden commit ids"
    print(json.dumps(world.shas, sort_keys=True))
elif command == "future":
    dest, base = Path(sys.argv[2]), sys.argv[3]
    tree = sup.git_text(dest, "rev-parse", base + "^{tree}")
    env = sup.clean_env(
        GIT_AUTHOR_DATE="@1800000000 +0000", GIT_COMMITTER_DATE="@1800000000 +0000"
    )
    print(sup.git_text(dest, "commit-tree", tree, "-p", base, "-m", "future", env=env))
elif command == "get":
    value = json.loads(Path(sys.argv[2]).read_text())
    for key in sys.argv[3:]:
        value = value[int(key)] if isinstance(value, list) else value[key]
    print(value if not isinstance(value, (dict, list)) else json.dumps(value, sort_keys=True))
elif command == "golden":
    print(json.loads((sup.FX / "expected-records.json").read_text())["commit_ids"]["C14"])
