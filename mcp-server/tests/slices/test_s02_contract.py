"""Real-parser ownership/scope binding contracts (ACI-S02)."""

from __future__ import annotations

import json
from pathlib import Path

from atlas_aci.codegraph import CodeGraph


def index(tmp_path: Path, files: dict[str, str]) -> CodeGraph:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    graph = CodeGraph(repo=repo)
    graph.build()
    return graph


def test_unrelated_receiver(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "example.py": (
                "class Widget:\n"
                "    pass\n"
                "class Other:\n"
                "    @staticmethod\n"
                "    def close():\n"
                "        pass\n"
                "def request():\n"
                "    Widget.close()\n"
            )
        },
    )
    result = graph.query("callers_of:close")
    print(json.dumps(result, sort_keys=True))
    assert result["edges"] == []
    assert result["unresolved_refs"] == 1
    refs = graph.search_symbol("close")["references"]
    assert refs[0]["unresolved_reason"] == "owner_has_no_member"


def test_owned_receiver(tmp_path: Path) -> None:
    samples = {
        "owned.py": (
            "class Widget:\n"
            "    @staticmethod\n"
            "    def close():\n"
            "        pass\n"
            "class Other:\n"
            "    @staticmethod\n"
            "    def close():\n"
            "        pass\n"
            "def request():\n"
            "    Widget.close()\n"
        ),
        "owned.js": (
            "class Widget { static close() {} }\n"
            "class Other { static close() {} }\n"
            "function request() {Widget.close();}\n"
        ),
        "owned.ts": (
            "class Widget { static close() {} }\n"
            "class Other { static close() {} }\n"
            "function request() {Widget.close();}\n"
        ),
        "owned.rb": (
            "class Widget\n"
            " def self.close; end\n"
            "end\n"
            "class Other\n"
            " def self.close; end\n"
            "end\n"
            "def request; Widget.close; end\n"
        ),
    }
    for i, (name, source) in enumerate(samples.items()):
        directory = tmp_path / str(i)
        directory.mkdir()
        graph = index(directory, {name: source})
        edges = graph.callers_of("close")
        assert len(edges) == 1
        assert edges[0]["confidence"] == "EXTRACTED"
        assert edges[0]["target"]["line"] == (
            3 if name.endswith(".py") else 2 if name.endswith(".rb") else 1
        )
        assert edges[0]["source"]["name"] == "request"


def test_shadowed_name(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "scope.py": (
                "def target():\n"
                "    pass\n"
                "def outer():\n"
                "    def target():\n"
                "        pass\n"
                "    target()\n"
            )
        },
    )
    edges = graph.callers_of("target")
    assert len(edges) == 1
    assert edges[0]["target"]["line"] == 4
    assert edges[0]["source"]["name"] == "outer"


def test_cross_language_collision(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {"caller.py": "def caller():\n    foreign()\n", "other.js": "function foreign() {}\n"},
    )
    assert graph.callers_of("foreign") == []
    assert graph.query("callers_of:foreign")["unresolved_refs"] == 1
    assert (
        graph.search_symbol("foreign")["references"][0]["unresolved_reason"] == "no_visible_target"
    )


def test_unknown_alias(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "caller.py": "from external import target as alias\ndef caller():\n    alias()\n",
            "other.py": "def alias():\n    pass\n",
        },
    )
    assert graph.callers_of("alias") == []
    assert (
        graph.search_symbol("alias")["references"][0]["unresolved_reason"] == "unknown_import_alias"
    )


def canonical(graph: CodeGraph) -> tuple[list[dict], list[tuple]]:
    edges = graph.callers_of("close")
    refs = [
        (r["path"], r["line"], r["col"], r["callee_name"], r["unresolved_reason"])
        for r in graph.search_symbol("close")["references"]
    ]
    return edges, refs


def test_binding_replay(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "replay.py": (
                "class Widget:\n"
                "    @staticmethod\n"
                "    def close():\n"
                "        pass\n"
                "def request(obj):\n"
                "    Widget.close()\n"
                "    obj.close()\n"
            )
        },
    )
    before = canonical(graph)
    graph.build()
    assert canonical(graph) == before
    assert len(before[0]) == 1
    assert graph.query("callers_of:close")["unresolved_refs"] == 1


def test_instance_self_and_static_categories(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "a.py": (
                "class A:\n"
                "    def close(self):\n"
                "        pass\n"
                "    def request(self):\n"
                "        self.close()\n"
            ),
            "a.js": "class A { close() {} request() { this.close(); } }\nA.close();\n",
            "a.rb": "class A\n def close; end\n def request; self.close; end\nend\nA.close\n",
        },
    )
    edges = graph.callers_of("close")
    assert len(edges) == 3
    assert all(e["confidence"] == "INFERRED" for e in edges)
    assert graph.query("callers_of:close")["unresolved_refs"] == 2


def test_noncallable_blockers_and_class_namespace_skip(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "scope.py": (
                "def target():\n"
                "    pass\n"
                "class A:\n"
                "    def target(self):\n"
                "        pass\n"
                "    def request(self):\n"
                "        target()\n"
                "def parameter(target):\n"
                "    target()\n"
                "def assigned():\n"
                "    target()\n"
                "    target = 1\n"
            )
        },
    )
    edges = graph.callers_of("target")
    assert len(edges) == 1
    assert edges[0]["source"]["name"] == "request"
    assert edges[0]["target"]["line"] == 1
    assert graph.query("callers_of:target")["unresolved_refs"] == 2


def test_javascript_block_and_function_scope(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "scope.js": (
                "function target() {}\n"
                "function outer() {\n"
                " { function target() {} target(); }\n"
                " { let target = 1; }\n"
                " target();\n"
                "}\n"
                "function blocked() {{var target = 1;} target();}\n"
                "function tdz() {{target(); let target = 1;}}\n"
            )
        },
    )
    edges = graph.callers_of("target")
    assert [e["target"]["line"] for e in edges] == [3, 1]
    assert graph.query("callers_of:target")["unresolved_refs"] == 2


def test_same_line_source_ownership(tmp_path: Path) -> None:
    graph = index(
        tmp_path, {"same.js": "function alpha(){hit();} function beta(){hit();} function hit(){}\n"}
    )
    edges = graph.callers_of("hit")
    assert [e["source"]["name"] for e in edges] == ["alpha", "beta"]


def test_no_cross_file_module_guess(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {"caller.py": "def request():\n    target()\n", "target.py": "def target():\n    pass\n"},
    )
    assert graph.callers_of("target") == []
    assert graph.query("callers_of:target")["unresolved_refs"] == 1


def test_cross_file_owner_spans_do_not_alias(tmp_path: Path) -> None:
    graph = index(
        tmp_path,
        {
            "a.py": (
                "class Widget:\n    @staticmethod\n    def empty():\n        pass\nWidget.close()\n"
            ),
            "b.py": "class OtherX:\n    @staticmethod\n    def close():\n        pass\n",
        },
    )
    assert graph.callers_of("close") == []
    assert graph.query("callers_of:close")["unresolved_refs"] == 1


def test_control_flow_and_import_blockers(tmp_path: Path) -> None:
    samples = [
        (
            "loop.py",
            (
                "def target():\n"
                "    pass\n"
                "def request(values):\n"
                "    for target in values:\n"
                "        target()\n"
            ),
        ),
        (
            "catch.js",
            (
                "function target() {}\n"
                "function request() { try { throw 1; } catch (target) { "
                "target(); } }"
            ),
        ),
        (
            "wildcard.py",
            "def target():\n    pass\nfrom external import *\ndef request():\n    target()\n",
        ),
    ]
    for i, (name, source) in enumerate(samples):
        directory = tmp_path / str(i)
        directory.mkdir()
        graph = index(directory, {name: source})
        assert graph.callers_of("target") == []
        assert graph.search_symbol("target")["references"][0]["unresolved_reason"]


def test_receiver_keywords_are_language_specific(tmp_path: Path) -> None:
    samples = [
        ("js_self.js", "class A { close() {} request(self) { self.close(); } }"),
        ("ruby_this.rb", "class A\n def close; end\n def request(this); this.close; end\nend"),
        (
            "nested_this.js",
            (
                "class A { close() {} request() { function nested() { co"
                "nst f = () => this.close(); } } }"
            ),
        ),
    ]
    for i, (name, source) in enumerate(samples):
        directory = tmp_path / str(i)
        directory.mkdir()
        graph = index(directory, {name: source})
        assert graph.callers_of("close") == []
        assert graph.search_symbol("close")["references"][0]["unresolved_reason"]


def test_additional_binding_constructs_fail_closed(tmp_path: Path) -> None:
    py_prefix = "def target():\n    pass\n"
    js_prefix = "function target() {}\n"
    samples = [
        (
            "with.py",
            py_prefix + "def request():\n    with manager() as target:\n        target()\n",
        ),
        (
            "except.py",
            py_prefix
            + (
                "def request():\n"
                "    try:\n"
                "        risky()\n"
                "    except Exception as target:\n"
                "        target()\n"
            ),
        ),
        ("delete.py", py_prefix + "def request():\n    del target\n    target()\n"),
        ("parameter.js", js_prefix + "function request({target}) { target(); }"),
        ("parameter.ts", js_prefix + "function request({target}: any) { target(); }"),
        ("assignment.js", js_prefix + "function request(obj) { const {target} = obj; target(); }"),
        ("catch.js", js_prefix + "function request() { try {} catch ({target}) { target(); } }"),
        (
            "loop.js",
            js_prefix + "function request(values) { for (const target of values) { target(); } }",
        ),
    ]
    for i, (name, source) in enumerate(samples):
        directory = tmp_path / str(i)
        directory.mkdir()
        graph = index(directory, {name: source})
        assert graph.callers_of("target") == []
        assert graph.query("callers_of:target")["unresolved_refs"] == 1
        assert graph.search_symbol("target")["references"][0]["unresolved_reason"]
