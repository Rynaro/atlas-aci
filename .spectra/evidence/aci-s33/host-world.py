"""Build the main world (for the host-git parity leg); usage: host-world.py DEST."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "mcp-server/tests/slices"))
import test_s33_support as sup  # noqa: E402

world = sup.build_world("main", Path(sys.argv[1]))
print(sup.json.dumps(world.shas, sort_keys=True))
