"""ACI-S28 extraction: declaration forms, imports, exports and the diagnostics of one member."""

from __future__ import annotations

import pytest
from test_s28_support import (
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    imp,
    lock_for,
    minimal_entries,
    rejected,
)

from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.dependencies import uv_lock as ul

pytestmark = pytest.mark.usefixtures("grammar")


def snap(source: bytes, path: str = "m/x.py", limits=None) -> dict:
    from test_s28_support import GEN

    wheel = GEN.build_zip(minimal_entries(source, path))
    return imp(lock_for(wheel), wheel=wheel, limits=limits or ul.normalize_limits())


def codes(snapshot: dict, member: str = "m/x.py") -> list[str]:
    return sorted(d["code"] for d in snapshot["diagnostics"] if d["member"] == member)


def exported(snapshot: dict) -> list[str]:
    return [e["name"] for e in snapshot["exports"]]


def declared(snapshot: dict) -> list[tuple[str, str]]:
    return [(d["kind"], d["qualified_name"]) for d in snapshot["declarations"]]


LITERAL = {
    "list": (b'__all__ = ["a", "b"]\n', ["a", "b"]),
    "tuple": (b"__all__ = ('a', 'b')\n", ["a", "b"]),
    "trailing comma and comment": (b'__all__ = [\n    "a",  # first\n    "b",\n]\n', ["a", "b"]),
    "empty": (b"__all__ = []\n", []),
    "after code": (b"import os\n\n\ndef f():\n    pass\n\n__all__ = ['f']\n", ["f"]),
}
DYNAMIC = {
    "augmented": b'__all__ = ["a"]\n__all__ += ["b"]\n',
    "extend": b'__all__ = ["a"]\n__all__.extend(["b"])\n',
    "annotated": b'__all__: list = ["a"]\n',
    "tuple unpack": b'a, __all__ = 1, ["a"]\n',
    "in if": b'if X:\n    __all__ = ["a"]\n',
    "in try": b'try:\n    __all__ = ["a"]\nexcept E:\n    pass\n',
    "in with": b'with x:\n    __all__ = ["a"]\n',
    "two assignments": b'__all__ = ["a"]\n__all__ = ["b"]\n',
    "concatenated list": b'__all__ = ["a"] + ["b"]\n',
    "name element": b'__all__ = ["a", b]\n',
    "fstring element": b'__all__ = [f"a"]\n',
    "prefixed element": b'__all__ = [r"a"]\n',
    "bytes element": b'__all__ = [b"a"]\n',
    "escape": b'__all__ = ["a\\n"]\n',
    "implicit concatenation": b'__all__ = ["a" "b"]\n',
    "triple quoted": b'__all__ = ["""a"""]\n',
    "not an identifier": b'__all__ = ["a-b"]\n',
    "empty string": b'__all__ = [""]\n',
    "a variable": b"__all__ = names\n",
    "a call": b'__all__ = list(["a"])\n',
    "a set": b'__all__ = {"a"}\n',
    "parenthesised string": b'__all__ = ("a")\n',
    "chained": b'__all__ = x = ["a"]\n',
    "deleted": b"del __all__\n",
    "read in a function": b'__all__ = ["a"]\n\n\ndef f():\n    return __all__\n',
    "attribute read": b"names = mod.__all__\n",
    "global in a function": b"def f():\n    global __all__\n    __all__ = []\n",
    "starred": b'__all__ = [*other, "a"]\n',
    "comprehension": b'__all__ = [n for n in ("a",)]\n',
}


def test_surface_literal_all_is_exported_exactly():
    for label, (source, names) in LITERAL.items():
        result = snap(source)
        assert exported(result) == names, label
        assert "dynamic_all_not_indexed" not in codes(result), label
        for export in result["exports"]:
            data = source[export["span"]["start_byte"] : export["span"]["end_byte"]]
            assert data[1:-1] == export["name"].encode() and data[:1] in (b'"', b"'"), label


def test_surface_every_other_all_is_dynamic_with_no_exports():
    for label, source in DYNAMIC.items():
        result = snap(source)
        assert result["exports"] == [], label
        assert codes(result).count("dynamic_all_not_indexed") == 1, label
        incomplete = {m["member"]: m["incomplete"] for m in result["modules"]}
        assert incomplete["m/x.py"] is True, label
    assert "dynamic_exports_not_modeled" in pw.LIMITATIONS


def test_surface_duplicate_export_names_have_distinct_records():
    result = snap(b'__all__ = ["a", "a"]\n')
    first, second = result["exports"]
    assert first["name"] == second["name"] == "a"
    assert first["span"]["start_byte"] != second["span"]["start_byte"]
    assert first["id"] != second["id"]
    pw.validate_snapshot(result)


def test_surface_variables_aliases_and_type_aliases_are_not_indexed():
    source = (
        b"X = f()\nY: int = 3\ntype Z = int\nW = lambda: 1\n\n\n"
        b"class C:\n    attr = 1\n    other: int\n\n\nvalue = C\n"
    )
    result = snap(source)
    assert declared(result) == [("class", "C")]
    assert result["exports"] == []
    assert "variables_aliases_and_type_aliases_not_indexed" in pw.LIMITATIONS


def test_surface_declaration_forms():
    source = (
        b"def a():\n    pass\n\n\nasync def b():\n    pass\n\n\n@d\nclass C:\n"
        b"    def m(self):\n        pass\n\n    async def n(self):\n        pass\n\n"
        b"    @staticmethod\n    def s():\n        pass\n\n    class D:\n"
        b"        @a.b(1)\n        async def p(self):\n            pass\n\n\n"
        b"def outer():\n    def inner():\n        pass\n\n    class Local:\n        pass\n"
    )
    result = snap(source)
    assert declared(result) == [
        ("function", "a"),
        ("async_function", "b"),
        ("class", "C"),
        ("method", "C.m"),
        ("async_method", "C.n"),
        ("method", "C.s"),
        ("class", "C.D"),
        ("async_method", "C.D.p"),
        ("function", "outer"),
    ]
    decorated = {d["qualified_name"]: d["decorated"] for d in result["declarations"]}
    assert decorated["C"] and decorated["C.s"] and decorated["C.D.p"]
    assert not decorated["a"] and not decorated["C.m"]
    ids = {d["qualified_name"]: d["id"] for d in result["declarations"]}
    parents = {d["qualified_name"]: d["parent_id"] for d in result["declarations"]}
    assert parents["C.D.p"] == ids["C.D"] and parents["C.D"] == ids["C"] and parents["C"] is None
    assert codes(result) == []


def test_surface_class_nesting_limit():
    four = b"class A:\n class B:\n  class C:\n   class D:\n    def m(self):\n     pass\n"
    result = snap(four)
    assert [q for _, q in declared(result)] == ["A", "A.B", "A.B.C", "A.B.C.D", "A.B.C.D.m"]
    assert codes(result) == []
    five = four.replace(b"    def m", b"    class E:\n     def m")
    result = snap(five)
    assert [q for _, q in declared(result)] == ["A", "A.B", "A.B.C", "A.B.C.D"]
    assert codes(result) == ["nesting_limit_exceeded"]
    assert result["declarations"][-1]["span"]["start_line"] == 4
    skipped = result["diagnostics"][0]
    assert skipped["span"]["start_line"] == 5  # the span is the class that was not entered


def test_surface_control_blocks_are_diagnosed_never_indexed():
    source = (
        b"if X:\n    def f():\n        pass\n    import os\n"
        b"for i in y:\n    class C:\n        pass\n"
        b"while 1:\n    import a\n"
        b"try:\n    import b\nexcept E:\n    def g():\n        pass\n"
        b"with c:\n    def h():\n        pass\n"
        b"match v:\n    case 1:\n        import z\n"
        b"if Y:\n    pass\n"
    )
    result = snap(source)
    assert declared(result) == [] and result["imports"] == []
    assert sorted(codes(result)) == sorted(
        ["conditional_definition_not_indexed"] * 4 + ["conditional_import_not_indexed"] * 4
    )
    class_body = b"class K:\n    if X:\n        def f(self):\n            pass\n"
    result = snap(class_body)
    assert declared(result) == [("class", "K")]
    assert codes(result) == ["conditional_definition_not_indexed"]


def test_surface_import_forms():
    source = (
        b"from __future__ import annotations\n"
        b"import a.b as c, d\n"
        b"from x.y import (\n    p,\n    q as r,\n)\n"
        b"from . import s\n"
        b"from .. import t as u\n"
        b"from .m import *\n"
        b"\n\ndef f():\n    import inner\n\n\nclass K:\n    import in_class\n"
    )
    result = snap(source, "m/n/x.py")
    rows = [
        (i["statement"], i["level"], i["target"], i["name"], i["alias"], i["resolved_module"])
        for i in result["imports"]
    ]
    assert rows == [
        ("from", 0, "__future__", "annotations", None, "__future__"),
        ("import", 0, "a.b", None, "c", "a.b"),
        ("import", 0, "d", None, None, "d"),
        ("from", 0, "x.y", "p", None, "x.y"),
        ("from", 0, "x.y", "q", "r", "x.y"),
        ("from", 1, "", "s", None, "m.n"),
        ("from", 2, "", "t", "u", "m"),
        ("from", 1, "m", "*", None, "m.n.m"),
    ]
    assert codes(result, "m/n/x.py") == ["star_import_not_expanded"]
    spans = {(i["span"]["start_byte"]) for i in result["imports"]}
    assert len(spans) == 6  # one span per statement; two statements name two imports each
    ordinals = [i["id"] for i in result["imports"]]
    assert len(set(ordinals)) == len(ordinals)
    pw.validate_snapshot(result)


def test_surface_relative_imports_resolve_against_the_package_only():
    top = b"from . import x\nfrom .a import b\nfrom .. import y\nimport plain\n"
    result = snap(top, "six.py")  # a top-level module has an empty base package
    assert [i["resolved_module"] for i in result["imports"]] == [None, None, None, "plain"]
    package = snap(top, "pkg/__init__.py")
    assert [i["resolved_module"] for i in package["imports"]] == ["pkg", "pkg.a", None, "plain"]
    deep = snap(b"from ... import z\nfrom ..q import w\n", "pkg/sub/mod.py")
    assert [i["resolved_module"] for i in deep["imports"]] == [None, "pkg.q"]
    flat = snap(b"from . import z\nfrom .q import w\n", "pkg/mod.py")
    assert [i["resolved_module"] for i in flat["imports"]] == ["pkg", "pkg.q"]


def test_surface_module_names_and_parse_states():
    cases = {
        "pkg/mod.py": ("pkg.mod", False, "parsed"),
        "pkg/__init__.py": ("pkg", True, "parsed"),
        "a/b/c.py": ("a.b.c", False, "parsed"),
        "__init__.py": (None, True, "not_parsed"),
        "pkg/mod-x.py": (None, False, "not_parsed"),
        "pkg/1abc.py": (None, False, "not_parsed"),
        "pkg/a.b.py": (None, False, "not_parsed"),
        "x y.py": (None, False, "not_parsed"),
    }
    for path, (module, is_package, state) in cases.items():
        assert pw.module_name(path) == (module, is_package), path
        member = [m for m in snap(b"def f():\n    pass\n", path)["modules"] if m["member"] == path]
        assert [(m["module"], m["parse_state"]) for m in member] == [(module, state)], path
    result = snap(b"def f():\n    pass\n", "pkg/mod-x.py")
    assert result["declarations"] == []  # an unimportable path has no declarations
    assert codes(result, "pkg/mod-x.py") == ["non_importable_module_path"]


def test_surface_syntax_error_drops_the_whole_member():
    result = snap(b"class Phantom:\n    def ok(self):\n        pass\n\n\ndef broken(:\n")
    assert declared(result) == [] and result["imports"] == [] and result["exports"] == []
    assert codes(result) == ["syntax_error"]
    assert [m["parse_state"] for m in result["modules"] if m["member"] == "m/x.py"] == [
        "syntax_error"
    ]
    # One construct the grammar cannot parse (here an unclosed bracket) drops the member too.
    for source in (b'__all__ = ["a"]\nx = (\n', b"def f(:\n", b"class\n"):
        assert codes(snap(source)) == ["syntax_error"], source
    latin = snap(b"# caf\xe9\ndef f():\n    pass\n")
    assert declared(latin) == [] and codes(latin) == ["non_utf8_source"]


def test_surface_bom_prefixed_source_keeps_offsets_relative_to_the_member_bytes():
    source = b"\xef\xbb\xbfclass A:\n    pass\n"
    result = snap(source)
    assert declared(result) == [("class", "A")]  # the grammar skips the BOM: parsed, no error
    span = result["declarations"][0]["span"]
    assert (span["start_byte"], span["start_col"], span["start_line"]) == (3, 3, 1)
    assert source[span["start_byte"] : span["end_byte"]].startswith(b"class A")
    assert result["modules"][-1]["parse_state"] == "parsed"


def test_surface_spans_are_byte_offsets():
    source = "# caf\u00e9\nclass \u00c9t\u00e9:\n    def f(self):\n        pass\n".encode()
    result = snap(source)
    cls = result["declarations"][0]
    assert cls["name"] == "\u00c9t\u00e9" and cls["symbol"] == "m.x.\u00c9t\u00e9"
    span = cls["span"]
    assert span["start_line"] == 2 and span["start_col"] == 0
    assert span["start_byte"] == len("# caf\u00e9\n".encode())
    method = result["declarations"][1]["span"]
    assert (method["start_line"], method["start_col"]) == (3, 4)
    assert source[method["start_byte"] : method["end_byte"]].startswith(b"def f")


def test_surface_getattr_and_record_budget():
    hook = b"def __getattr__(name):\n    pass\n\n\n"
    result = snap(hook + b"class C:\n    def __getattr__(self, n):\n        pass\n")
    assert codes(result) == ["dynamic_module_getattr"]  # only the module-level hook
    assert ("function", "__getattr__") in declared(result)
    source = b"def a():\n    pass\n\n\ndef b():\n    pass\n"
    total = len(snap(source)["declarations"]) + sum(
        len(snap(source)[k]) for k in ("imports", "exports", "diagnostics")
    )
    assert total == 2
    assert snap(source, limits=ul.normalize_limits({"max_records": total}))["declarations"]
    exc = rejected(lambda: snap(source, limits=ul.normalize_limits({"max_records": total - 1})))
    assert exc.reason == "surface_limit_exceeded" and exc.coordinate is not None
    # Role diagnostics count against the same budget.
    from test_s28_support import GEN

    stub = [*minimal_entries(), GEN.Entry(b"m/x.pyi", b"x: int\n")]
    wheel = GEN.build_zip(stub)
    base = imp(lock_for(wheel), wheel=wheel)
    used = sum(len(base[k]) for k in ("declarations", "imports", "exports", "diagnostics"))
    tight = ul.normalize_limits({"max_records": used - 1})
    assert rejected(lambda: imp(lock_for(wheel), wheel=wheel, limits=tight)).reason == (
        "surface_limit_exceeded"
    )
