"""Day-1 (a): per-statement argument shapes for the S25 fixture."""
import sys
from pathlib import Path

import tree_sitter_language_pack as pack

root = Path(sys.argv[1])
parser = pack.get_parser("ruby")


def t(src, n, lim=50):
    s = src[n.start_byte:n.end_byte].decode()
    return s if len(s) <= lim else s[:lim] + "..."


for name in sys.argv[2:]:
    src = (root / name).read_bytes()
    tree = parser.parse(src)
    print("=====", name)
    stack = [(tree.root_node, 0)]
    while stack:
        n, d = stack.pop()
        if n.type in {"call", "assignment", "operator_assignment", "singleton_method", "method", "if_modifier", "identifier"} and d >= 1 and n.parent.type in {"body_statement", "block_body", "program"}:
            print("  " * 0 + "STMT", n.type, repr(t(src, n, 70)))
            for i, c in enumerate(n.children):
                f = n.field_name_for_child(i)
                if c.is_named:
                    print("   ", f, c.type, repr(t(src, c)))
                    if c.type in {"argument_list", "body_statement", "do_block", "block"}:
                        for j, a in enumerate(c.children):
                            if a.is_named:
                                print("       ", c.field_name_for_child(j), a.type, repr(t(src, a)))
                                if a.type in {"pair", "string", "lambda"}:
                                    for k, e in enumerate(a.children):
                                        print("           ", a.field_name_for_child(k), e.type, repr(t(src, e, 30)))
        if n.type in {"program", "module", "class", "body_statement"}:
            for c in reversed(n.named_children):
                if n.type == "program" or n.type == "module" or n.type == "class" or True:
                    stack.append((c, d + 1))
