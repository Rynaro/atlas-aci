"""Reported TSX gaps and the JSX value-tag rule, on the real parser and indexer."""

import json

import pytest
from test_s14_contract import build, rows, workspace
from tree_sitter_language_pack import get_parser

from atlas_aci.extractors import tsx


def parse(text):
    source = text.encode()
    tree = get_parser("tsx").parse(source)
    assert not tree.root_node.has_error
    return tree, source


def tags(text):
    tree, source = parse(text)
    found = []
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        if node.type in {"jsx_opening_element", "jsx_self_closing_element"}:
            name = node.child_by_field_name("name")
            found.append(None if name is None else tsx.component_tag(name, source))
        elif node.type == "jsx_closing_element":
            pass
    return found


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("<Foo />", ("Foo", None)),
        ("<_Foo />", ("_Foo", None)),
        ("<$x />", ("$x", None)),
        ("<Foo.Bar />", ("Foo.Bar", "Foo")),
        ("<a.b.C />", ("a.b.C", "a.b")),
        ("<this.Panel />", ("this.Panel", "this")),
        ("<foo />", None),
        ("<div />", None),
        ("<my-element />", None),
        ("<svg:rect />", None),
        ("<xlink:href />", None),
    ],
)
def test_jsx_value_tag_rule(tag, expected):
    assert tags("const x = " + tag + ";\n") == [expected]


def test_jsx_fragment_and_closing_tags_are_never_recorded():
    # One element, one opening tag: the closing tag and fragment add nothing.
    assert tags("const x = <><Foo>hi</Foo></>;\n") == [None, ("Foo", None)]


@pytest.mark.parametrize(
    "text,status",
    [
        ("export function F() {}\n", "named"),
        ("export default function F() {}\n", "default"),
        ("export class C {}\n", "named"),
        ("export default class C {}\n", "default"),
        ("export const A = () => 1;\n", "named"),
        ("export let B = function () {};\n", "named"),
        ("function F() {}\n", None),
        ("const A = () => 1;\n", None),
        ("function Outer() { function Inner() {} }\n", None),
    ],
)
def test_export_status(tmp_path, text, status):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.tsx").write_text(text)
    graph, _ = build(repo)
    statuses = [
        json.loads(r["binding"])["export_status"] for r in rows(graph, "SELECT * FROM symbols")
    ]
    assert statuses[0] == status
    assert len(statuses) == len(rows(graph, "SELECT * FROM symbols"))
    graph.close()


GAPS = {
    # Each reported limitation has a fixture that proves the gap is real and explicit.
    "tsx_export_specifiers": (
        "function Local() {}\nexport { Local };\nexport { Remote as Other } from './remote';\n",
        {"Local": None},
        set(),
    ),
    "tsx_default_export_identifier": (
        "function Local() {}\nexport default Local;\n",
        {"Local": None},
        set(),
    ),
    "tsx_anonymous_default_export": (
        "export default function () { return 1; }\nexport const keep = () => 1;\n",
        {"keep": "named"},
        set(),
    ),
    "tsx_intrinsic_jsx": (
        "export function F() { return <div><span /><my-el /><svg:rect /></div>; }\n",
        {"F": "named"},
        set(),
    ),
    "tsx_dynamic_binding": (
        "export async function F(key: string) {\n"
        "  const m = await import('./lazy');\n"
        "  return m[key]();\n"
        "}\n",
        {"F": "named"},
        set(),
    ),
}


@pytest.mark.parametrize("limitation", sorted(GAPS))
def test_reported_gap_is_real_and_not_silently_fabricated(tmp_path, limitation):
    text, symbols, jsx_values = GAPS[limitation]
    assert limitation in tsx.LIMITATIONS
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.tsx").write_text(text)
    graph, stats = build(repo)
    assert stats["extraction_state"] == "complete"
    actual = {
        r["name"]: json.loads(r["binding"])["export_status"]
        for r in rows(graph, "SELECT * FROM symbols")
    }
    assert actual == symbols
    assert not rows(graph, "SELECT 1 FROM import_bindings")  # no import from export/dynamic forms
    assert not rows(graph, "SELECT 1 FROM edges")
    refs = rows(graph, "SELECT relation, callee_name FROM refs")
    assert {r["callee_name"] for r in refs if r["relation"] == "jsx"} == jsx_values
    if limitation == "tsx_dynamic_binding":
        # `import()` and a computed member call leave no call reference at all.
        assert refs == []
    if limitation == "tsx_intrinsic_jsx":
        assert refs == []
    graph.close()


def test_side_effect_and_namespace_only_imports_and_nested_scope(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.tsx").write_text(
        "import './style.css';\n"
        "import * as All from './all';\n"
        "import Def, * as Both from './both';\n"
        "import { type A, B as C, 'string-name' as D } from './named';\n"
    )
    graph, _ = build(repo)
    shapes = {
        r["local_name"]: json.loads(r["description"])
        for r in rows(graph, "SELECT * FROM import_bindings")
    }
    assert {k: (v["name"], v["kind"], v["type_only"]) for k, v in shapes.items()} == {
        "All": ("*", "namespace", False),
        "Def": ("default", "default", False),
        "Both": ("*", "namespace", False),
        "A": ("A", "named", True),
        "C": ("B", "named", False),
        "D": ("string-name", "named", False),
    }
    assert {v["source"] for v in shapes.values()} == {"./all", "./both", "./named"}
    graph.close()


def test_missing_prerequisite_workspace_helper_is_real(tmp_path):
    # Guard: the shared fixture copy is the real repository, not a stub.
    repo = workspace(tmp_path)
    assert sorted(p.name for p in repo.iterdir()) == [
        "anonymous.tsx",
        "components.tsx",
        "default-identifier.tsx",
        "good.tsx",
        "user.tsx",
    ]
