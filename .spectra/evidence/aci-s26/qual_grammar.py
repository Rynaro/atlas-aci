"""Day-1 (a): grammar probe for the S26 fixture and inline snippets (scratch evidence; not product
code). Standalone: needs only tree-sitter and tree-sitter-language-pack.

    python qual_grammar.py <fixture-project-dir>      # prints JSON
"""
import json
import sys
from pathlib import Path

import tree_sitter
import tree_sitter_language_pack as pack

root = Path(sys.argv[1])
parser = pack.get_parser("ruby")


def sexp(node, source, depth=0, limit=5):
    """Compact s-expression with field names (named children only)."""
    if depth > limit:
        return "..."
    parts = []
    for i, child in enumerate(node.children):
        if not child.is_named:
            continue
        field = node.field_name_for_child(i)
        text = sexp(child, source, depth + 1, limit)
        parts.append((field + ": " if field else "") + text)
    if not parts:
        return node.type
    return "(" + node.type + " " + " ".join(parts) + ")"


def first(node, kind):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == kind:
            return n
        stack.extend(reversed(n.named_children))
    return None


def parse(text):
    source = text.encode()
    return parser.parse(source), source


out = {"has_error": {}, "snippets": {}, "facts": {}}
for path in sorted(root.rglob("*.rb")):
    tree, _ = parse(path.read_text())
    out["has_error"][str(path.relative_to(root))] = tree.root_node.has_error
out["facts"]["fixture_files"] = len(out["has_error"])
out["facts"]["fixture_all_clean"] = not any(out["has_error"].values())
out["facts"]["descendant_for_byte_range"] = hasattr(tree_sitter.Node, "descendant_for_byte_range")
out["facts"]["versions"] = {
    "python": sys.version,
    "tree_sitter_binding": getattr(tree_sitter, "__version__", None),
}

CALLS = {
    "constant": "Foo.perform_async(1)",
    "scope": "A::B.perform_async(1)",
    "rooted": "::A.perform_async(1)",
    "set_chain": "A.set(wait: 1).perform_later(1)",
    "receiverless": "perform_async(1)",
    "no_parens": "Foo.perform_async 1, 2",
    "bare": "Foo.perform_async",
    "empty_parens": "Foo.perform_async()",
    "splat": "Foo.perform_async(*a)",
    "hash_splat": "Foo.perform_async(**o)",
    "pair_symbol": "Foo.perform_async(a, k: 1)",
    "pair_rocket": "Foo.perform_async(a, :k => 1)",
    "block_argument": "Foo.perform_async(a, &b)",
    "brace_block": "Foo.perform_async(a) { |x| x }",
    "do_block": "Foo.perform_async(a) do |x| x end",
    "forward": "def f(...)\n  Foo.perform_async(...)\nend",
    "heredoc": "Foo.perform_async(<<~X, 2)\n  body\nX\n",
    "braced_hash": "Foo.perform_async({ a: 1 })",
    "delayed": "Foo.perform_in(300, 1)",
    "trailing_comment": "Foo.perform_async(1, # c\n  2)",
    "string_receiver": "\"Foo\".constantize.perform_later(1)",
    "self_class": "self.class.perform_async(1)",
}
for name, text in CALLS.items():
    tree, source = parse(text)
    call = first(tree.root_node, "call")
    fields = {}
    if call is not None:
        for field in ("receiver", "method", "arguments", "block"):
            node = call.child_by_field_name(field)
            fields[field] = None if node is None else node.type
        args = call.child_by_field_name("arguments")
        fields["argument_children"] = (
            None if args is None else [c.type for c in args.named_children]
        )
    out["snippets"]["call:" + name] = {
        "has_error": tree.root_node.has_error,
        "sexp": sexp(tree.root_node, source),
        "call_fields": fields,
    }

DEFS = {
    "mixed": "def perform(a, b = 1, *c, d:, e: 2, **f, &g)\nend",
    "required": "def perform(a, b)\nend",
    "optional": "def perform(a, b = 1)\nend",
    "parenless": "def perform a, b\nend",
    "post_optional": "def perform(a, b = 1, c)\nend",
    "hash_splat_nil": "def perform(a, **nil)\nend",
    "destructured": "def perform((a, b), c)\nend",
    "forward": "def perform(...)\nend",
    "none": "def perform\nend",
    "empty_parens": "def perform()\nend",
    "required_kw": "def perform(a, b:)\nend",
    "optional_kw": "def perform(a, b: 1)\nend",
    "block": "def perform(a, &blk)\nend",
    "splat": "def perform(*args)\nend",
}
for name, text in DEFS.items():
    tree, source = parse(text)
    method = first(tree.root_node, "method")
    params = method.child_by_field_name("parameters") if method else None
    entry = {"has_error": tree.root_node.has_error, "sexp": sexp(tree.root_node, source)}
    if params is not None:
        entry["parameters_type"] = params.type
        entry["children"] = []
        for child in params.named_children:
            item = {"type": child.type}
            if child.type == "optional_parameter":
                nm = child.child_by_field_name("name")
                item["name_field"] = None if nm is None else source[nm.start_byte:nm.end_byte].decode()
                item["value_field"] = child.child_by_field_name("value") is not None
            if child.type == "keyword_parameter":
                item["value_field"] = child.child_by_field_name("value") is not None
            entry["children"].append(item)
    else:
        entry["parameters_type"] = None
    out["snippets"]["def:" + name] = entry

SHAPES = {
    "include_job": "class A\n  include Sidekiq::Job\nend",
    "include_rooted": "class A\n  include ::Sidekiq::Worker\nend",
    "super_app": "class A < ApplicationJob\nend",
    "super_active": "class A < ActiveJob::Base\nend",
    "super_rooted": "class A < ::ActiveJob::Base\nend",
    "compact_name": "class A::B < ApplicationJob\nend",
    "rooted_name": "class ::A\nend",
    "nested_compact": "module Outer\n  class Inner::Thing\n  end\nend",
    "singleton_class": "class A\n  class << self\n    def perform(y)\n    end\n  end\n  def self.perform(x)\n  end\nend",
}
for name, text in SHAPES.items():
    tree, source = parse(text)
    out["snippets"]["shape:" + name] = {
        "has_error": tree.root_node.has_error,
        "sexp": sexp(tree.root_node, source),
    }
tree, source = parse(SHAPES["include_job"])
inc = first(tree.root_node, "call")
arg = inc.child_by_field_name("arguments").named_children[0]
out["facts"]["include_shape"] = {
    "receiver": inc.child_by_field_name("receiver"),
    "method": source[inc.child_by_field_name("method").start_byte:inc.child_by_field_name("method").end_byte].decode(),
    "argument_type": arg.type,
    "argument_text": source[arg.start_byte:arg.end_byte].decode(),
}
out["facts"]["include_shape"]["receiver"] = None if out["facts"]["include_shape"]["receiver"] is None else "present"
# descendant_for_byte_range returns the identifier for the method span.
tree, source = parse("Foo.perform_async(1)")
call = tree.root_node.named_children[0]
method = call.child_by_field_name("method")
found = tree.root_node.descendant_for_byte_range(method.start_byte, method.end_byte)
out["facts"]["descendant_probe"] = {
    "type": found.type,
    "parent": found.parent.type,
    "method_field_matches": (
        found.parent.child_by_field_name("method").start_byte == found.start_byte
        and found.parent.child_by_field_name("method").end_byte == found.end_byte
    ),
}
print(json.dumps(out, indent=1, sort_keys=True))
