"""Day-1 (a): grammar probe for the S25 fixture (scratch evidence; not product code)."""
import json
import sys
from pathlib import Path

import tree_sitter_language_pack as pack

root = Path(sys.argv[1])
parser = pack.get_parser("ruby")
out = {"has_error": {}, "notes": {}}


def dump(node, source, depth=0, limit=6):
    lines = []
    stack = [(node, None, 0)]
    while stack:
        n, field, d = stack.pop()
        if d > limit:
            continue
        text = source[n.start_byte:n.end_byte].decode()
        text = text if len(text) < 40 else text[:37] + "..."
        lines.append("  " * d + (field + ": " if field else "") + n.type + (" " + repr(text) if not n.named_children else ""))
        kids = []
        for i, c in enumerate(n.children):
            kids.append((c, n.field_name_for_child(i), d + 1))
        stack.extend(reversed(kids))
    return lines


files = sorted(p for p in root.rglob("*.rb"))
for p in files:
    src = p.read_bytes()
    tree = parser.parse(src)
    out["has_error"][str(p.relative_to(root))] = tree.root_node.has_error
if "--dump" in sys.argv:
    for name in sys.argv[sys.argv.index("--dump") + 1:]:
        p = root / name
        src = p.read_bytes()
        tree = parser.parse(src)
        print("=====", name)
        print("\n".join(dump(tree.root_node, src)))
else:
    print(json.dumps(out, indent=1, sort_keys=True))
