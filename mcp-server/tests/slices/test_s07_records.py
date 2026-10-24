"""Record contract anchors independent of database wiring."""

from __future__ import annotations

import copy
import hashlib

import pytest
from tree_sitter_language_pack import get_parser

from atlas_aci.storage import evidence as e


def entities(source: bytes):
    tree = get_parser("python").parse(source)
    pending = [tree.root_node]
    declarations = {}
    while pending:
        node = pending.pop()
        pending.extend(node.named_children)
        if node.type in {"function_definition", "class_definition"}:
            name = node.child_by_field_name("name")
            declarations[node.id] = (
                node,
                "function" if node.type == "function_definition" else "class",
                source[name.start_byte : name.end_byte].decode(),
            )
    return tree, declarations, e.structural_entities(tree, declarations, "pkg/a.py", "python")


def test_record_identity_uses_ast_owners_and_matching_ordinals():
    source = (
        b"class X:\n def f(self):\n  pass\n def f(self):\n  pass\nclass Y:\n def f(self):\n  pass\n"
    )
    _tree, _decl, first = entities(source)
    _tree, _decl, moved = entities(b"\n\n" + source)
    assert {item["id"] for item in first.values()} == {item["id"] for item in moved.values()}
    assert len({item["id"] for item in first.values()}) == 5
    assert {item["ordinal"] for item in first.values() if item["name"] == "f"} == {0, 1}
    assert all("byte" not in e.canonical(item) for item in first.values())


def test_occurrence_uses_real_consumed_buffer_and_ast_span():
    source = "# café\ndef f():\n pass\n".encode()
    _tree, declarations, stored = entities(source)
    node = next(iter(declarations.values()))[0]
    occurrence = e.occurrence(
        node, "pkg/a.py", "python", source, stored[node.id]["id"], "definition", "function"
    )
    e.validate_record(occurrence)
    assert source[occurrence["start_byte"] : occurrence["end_byte"]] == b"def f():\n pass"
    assert occurrence["source_sha256"] == hashlib.sha256(source).hexdigest()
    changed = copy.deepcopy(occurrence)
    changed["start_byte"] = True
    changed["id"] = e.identifier(changed)
    with pytest.raises(ValueError):
        e.validate_record(changed)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(extra=0),
        lambda r: r.update(path="../escape.py"),
        lambda r: r.update(ordinal=True),
        lambda r: r.update(owners=[{"kind": "function", "name": "f", "ordinal": False}]),
    ],
)
def test_closed_entity_contract(mutate):
    item = e.file_entity("a.py", "python")
    mutate(item)
    item["id"] = e.identifier(item)
    with pytest.raises(ValueError):
        e.validate_record(item)


def test_parallel_direction_relation_and_evidence_are_key_inputs():
    a, b, proof = ("a" * 64, "b" * 64, "c" * 64)
    records = [
        e.assertion(a, b, "call", proof, "INFERRED", "B"),
        e.assertion(a, b, "import", proof, "INFERRED", "B"),
        e.assertion(b, a, "call", proof, "INFERRED", "A"),
        e.assertion(a, b, "call", "d" * 64, "INFERRED", "B"),
    ]
    assert len({r["id"] for r in records}) == 4
    for r in records:
        e.validate_record(r)
