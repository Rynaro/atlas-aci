"""Helper run INSIDE the production image by prod-scenario.sh (stdlib only; no atlas_aci import).

  prod-helper.py world DEST STATE        write the fixture state ("before" or "after") into DEST
                                         (git init on the first call; before is the first state)
  prod-helper.py fill NAME SNAPSHOT OUT  write the fixture mapping NAME with @before@ replaced
  prod-helper.py get FILE KEY...         print one JSON field of FILE (ints index lists)
  prod-helper.py result FILE ID KEY      print one field of the result of mapping ID in a drift doc
  prod-helper.py finding FILE ID         print yes or no: does a finding name mapping ID
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FX = Path("/repo/mcp-server/tests/fixtures/adr_mapping")
command = sys.argv[1]
if command == "world":
    dest, state = Path(sys.argv[2]), sys.argv[3]
    texts = json.loads((FX / "world.json").read_text(encoding="ascii"))[state]
    dest.mkdir(parents=True, exist_ok=True)
    if state == "before":
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
    for name, text in texts.items():
        path = dest / name
        if text is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
elif command == "fill":
    text = (FX / sys.argv[2]).read_text(encoding="ascii").replace("@before@", sys.argv[3])
    Path(sys.argv[4]).write_bytes(text.encode("ascii"))
elif command == "get":
    value = json.loads(Path(sys.argv[2]).read_text())
    for key in sys.argv[3:]:
        value = value[int(key)] if isinstance(value, list) else value[key]
    print(value if not isinstance(value, (dict, list)) else json.dumps(value, sort_keys=True))
elif command == "result":
    document = json.loads(Path(sys.argv[2]).read_text())
    found = next(r for r in document["results"] if r["mapping_id"] == sys.argv[3])
    print(found[sys.argv[4]])
elif command == "finding":
    document = json.loads(Path(sys.argv[2]).read_text())
    print("yes" if any(f["mapping_id"] == sys.argv[3] for f in document["findings"]) else "no")
