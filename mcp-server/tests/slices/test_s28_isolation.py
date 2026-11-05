"""ACI-S28 isolation: unchanged surface, pure source, closed vocabulary, fixtures and docs."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_s21_support import workspace
from test_s28_support import (
    DOCS,
    FX,
    GEN,
    PURE,
    ROOT,
    SRC,
    WHEEL,
    cli,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    imp,
    lock_for,
    main_snapshot,
    minimal_entries,
    reason,
)

from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.dependencies import uv_lock as ul
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging

pytestmark = pytest.mark.usefixtures("grammar")

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
PACKAGE_FILES = sorted((SRC / "dependencies").glob("*.py"))
# The S29 vocabulary guard (tests/slices/test_s29_openapi_diff.py BANNED_VOCABULARY).
BANNED_VOCABULARY = {
    "safe", "verified", "proven", "guaranteed", "compliant",
    "compatible", "incompatible", "breaking",
}  # fmt: skip


def test_python_dependency_surface_is_unchanged_and_cli_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("python" in name or "dependency" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    lowered = out.stdout.lower()
    for word in ("python-package", "pydep", "python_dependency", "uv.lock", "import-python"):
        assert word not in lowered
    assert "import-python-dependency" in cli.commands
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730


def functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def test_python_dependency_sources_are_pure_ascii_and_bounded():
    assert [f.name for f in PACKAGE_FILES] == ["__init__.py", "python_wheel.py", "uv_lock.py"]
    banned_regex = ("\\d", "\\w", "\\s", "$")
    banned_methods = {"isdigit", "isalpha", "isalnum", "isnumeric", "isspace", "casefold"}
    banned_methods |= {"isdecimal", "isidentifier", "normalize"}
    for file in PACKAGE_FILES:
        text = file.read_text()
        assert text.isascii(), file.name
        assert all(len(line) <= 100 for line in text.splitlines()), file.name
        assert "alter table" not in text.lower() and "nx.compose(" not in text
        tree = ast.parse(text)
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in banned_methods:
                raise AssertionError(file.name + " calls " + func.attr)
            is_re = isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            if is_re and func.value.id == "re" and call.args:  # type: ignore[union-attr]
                pattern = call.args[0]
                if isinstance(pattern, ast.Constant) and isinstance(pattern.value, str):
                    assert not any(b in pattern.value for b in banned_regex), pattern.value
        for fn in functions(tree):  # no self-recursion anywhere
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                func = call.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                assert name != fn.name or isinstance(func, ast.Attribute), (file.name, fn.name)
        assert "setrecursionlimit" not in text and "re.IGNORECASE" not in text
        assert "re.I)" not in text and "re.UNICODE" not in text
    assert Path(__file__).read_text().isascii()


def string_constants(tree: ast.AST) -> list[str]:
    found = []
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            found += [
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
    return found


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def hits(node, key=None) -> set[str]:
    found: set[str] = set()
    checked = {"kind", "code", "role", "parse_state", "statement", "type", "format", "profile"}
    checked |= {"digest_check", "evidence_coverage", "completeness", "call_binding", "execution"}
    checked |= {"parser", "language", "ecosystem", "name", "version"}
    if isinstance(node, dict):
        for name, value in node.items():
            found |= tokens(str(name)) & BANNED_VOCABULARY
            found |= hits(value, name)
    elif isinstance(node, list):
        for value in node:
            found |= hits(value, key)
    elif isinstance(node, str) and key in checked - {"name", "version"}:
        found |= tokens(node) & BANNED_VOCABULARY
    return found


def test_python_dependency_vocabulary_does_not_overclaim():
    wheel, lock = GEN.build_zip(minimal_entries()), None
    lock = lock_for(wheel)
    outputs = [main_snapshot(), imp(lock, wheel=wheel), pw.summary_document(main_snapshot())]
    assert all(hits(item) == set() for item in outputs)
    assert hits({"safe": 1, "kind": "breaking"}) == {"safe", "breaking"}  # the guard is live
    for file in PACKAGE_FILES:
        for constant in string_constants(ast.parse(file.read_text())):
            assert not tokens(constant) & BANNED_VOCABULARY, (file.name, constant)
    assert main_snapshot()["package"]["artifact"]["digest_check"] == "equals_lock_entry"


def tampered(mutate):
    snapshot = main_snapshot()
    mutate(snapshot)
    return reason(lambda: pw.validate_snapshot(snapshot))


def first(snapshot, kind="declarations"):
    return snapshot[kind][0]


def swap(snapshot, kind):
    snapshot[kind][0], snapshot[kind][1] = snapshot[kind][1], snapshot[kind][0]


def test_python_dependency_validate_snapshot_rejects_every_tampering():
    pw.validate_snapshot(main_snapshot())  # control: the untouched output validates
    mutations = {
        "snapshot id": lambda s: s.update(snapshot_id="0" * 64),
        "record id": lambda s: first(s).update(id="0" * 64),
        "module id": lambda s: first(s, "modules").update(id="1" * 64),
        "import id": lambda s: first(s, "imports").update(id="1" * 64),
        "export id": lambda s: first(s, "exports").update(id="1" * 64),
        "diagnostic id": lambda s: first(s, "diagnostics").update(id="1" * 64),
        "package snapshot id": lambda s: first(s).update(package_snapshot_id="2" * 64),
        "member sha": lambda s: first(s).update(member_sha256="3" * 64),
        "unsorted declarations": lambda s: swap(s, "declarations"),
        "unsorted diagnostics": lambda s: swap(s, "diagnostics"),
        "unsorted members": lambda s: swap(s, "members"),
        "unsorted exports": lambda s: swap(s, "exports"),
        "unknown top key": lambda s: s.update(extra=1),
        "missing top key": lambda s: s.pop("counts"),
        "unknown record key": lambda s: first(s).update(extra=1),
        "unknown kind": lambda s: first(s).update(kind="lambda"),
        "unknown code": lambda s: first(s, "diagnostics").update(code="made_up"),
        "unknown role": lambda s: first(s, "members").update(role="script"),
        "unknown parse state": lambda s: first(s, "modules").update(parse_state="half"),
        "record type": lambda s: first(s).update(type="symbol"),
        "dangling parent": lambda s: s["declarations"][1].update(parent_id="4" * 64),
        "parent is a function": lambda s: first(s).update(parent_id=s["declarations"][1]["id"]),
        "method without parent": lambda s: _method(s).update(parent_id=None),
        "qualified name": lambda s: first(s).update(qualified_name="Other"),
        "symbol": lambda s: first(s).update(symbol="x.y"),
        "counts": lambda s: s["counts"].update(declarations=999),
        "counts by code": lambda s: s["counts"]["diagnostics_by_code"].update(syntax_error=9),
        "coverage": lambda s: s["coverage"].update(call_binding="claimed"),
        "completeness": lambda s: s["coverage"].update(completeness="complete"),
        "incomplete flag": lambda s: first(s, "modules").update(incomplete=True),
        "limitations": lambda s: s["limitations"].pop(),
        "importer": lambda s: s["importer"].update(version="other"),
        "extractor": lambda s: s["extractor"].update(abi_version=3),
        "lock sha": lambda s: s["lock"].update(sha256="5" * 64),
        "lock revision": lambda s: s["lock"].update(revision=4),
        "environment": lambda s: s["environment"].update(os_name="nt"),
        "coordinate": lambda s: s["package"].update(coordinate="pypi:x@1"),
        "artifact sha": lambda s: s["package"]["artifact"].update(sha256="6" * 64),
        "digest check": lambda s: s["package"]["artifact"].update(digest_check="verified"),
        "span range": lambda s: first(s)["span"].update(end_byte=10**9),
        "span order": lambda s: first(s)["span"].update(start_byte=10**6),
        "span line": lambda s: first(s)["span"].update(start_line=0),
        "bool as int": lambda s: first(s)["span"].update(start_col=True),
        "dist info": lambda s: s["dist_info"].update(tags=[]),
        "duplicate id": lambda s: s["declarations"][1].update(id=first(s)["id"]),
        "member path": lambda s: first(s, "members").update(path="../x"),
        "declaration of an unparsed module": lambda s: first(s).update(member="setup.py"),
        "import resolved": lambda s: first(s, "imports").update(resolved_module="elsewhere"),
        "not a dict": lambda s: s.clear(),
    }
    for label, mutate in mutations.items():
        snapshot = main_snapshot()
        mutate(snapshot)
        assert reason(lambda s=snapshot: pw.validate_snapshot(s)) == "invalid_snapshot", label
    assert reason(lambda: pw.validate_snapshot([])) == "invalid_snapshot"
    assert reason(lambda: pw.validate_snapshot(None)) == "invalid_snapshot"


def _method(snapshot):
    return next(d for d in snapshot["declarations"] if d["kind"] == "method")


def test_python_dependency_every_output_validates():
    from test_s28_surface import snap

    for source in (b"", b"def f():\n    pass\n", b'__all__ = ["a"]\n', b"class C:\n    pass\n"):
        pw.validate_snapshot(snap(source))
    original = copy.deepcopy(main_snapshot())
    pw.validate_snapshot(original)
    assert json.loads(json.dumps(original)) == original


def test_python_dependency_fixtures_are_reproducible_and_pinned(tmp_path):
    provenance = json.loads((FX / "provenance.json").read_text())
    assert provenance["authored"]
    files = provenance["files"]
    assert {"forked.uv.lock", "single.uv.lock", PURE, "s28_demo_pkg-1.2.0.snapshot.json"} <= set(
        files
    )
    for name, entry in files.items():
        data = (FX / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], name
        assert len(data) == entry["bytes"] and entry["purpose"], name
    assert "hand-authored" in files["forked.uv.lock"]["purpose"]
    out = tmp_path / "gen"
    out.mkdir()
    produced = GEN.generate(out)
    assert set(produced) == {PURE, "single.uv.lock", "forked.uv.lock"}
    for name, data in produced.items():
        assert (out / name).read_bytes() == (FX / name).read_bytes() == data, name
    # The golden is produced by the importer, never by the generator.
    assert not (out / "s28_demo_pkg-1.2.0.snapshot.json").exists()
    assert (FX / ".gitattributes").read_text() == "* -text\n"
    assert sorted(p.name for p in FX.glob("**/*.py")) == ["generate.py"]  # no wheel source on disk
    assert (FX / "generate.py").read_text().isascii()
    assert b"\r" not in WHEEL[:4] and WHEEL[:2] == b"PK"
    again = subprocess.run(
        [sys.executable, str(FX / "generate.py"), str(out)], capture_output=True, timeout=60
    )
    assert again.returncode == 0
    assert (out / PURE).read_bytes() == WHEEL


def all_closed_values():
    values = [*ul.REJECTIONS, *ul.VARIANT_DETAILS, *ul.ENV_KEYS, *ul.LIMITS]
    values += [*pw.DECLARATION_KINDS, *pw.DIAGNOSTIC_CODES, *pw.MEMBER_ROLES, *pw.PARSE_STATES]
    values += [*pw.LIMITATIONS, pw.FORMAT, pw.SUMMARY_FORMAT, ul.LOCK_PROFILE]
    values += [pw.IMPORTER["name"], pw.IMPORTER["version"], "python_version"]
    values += ["MAX_TOML_DEPTH", "MAX_DIGIT_RUN", "MAX_MARKERS", "MAX_CLASS_NESTING"]
    return values


def test_python_dependency_docs_state_the_interpretation_and_name_every_closed_value():
    raw_docs = DOCS.read_text()
    assert raw_docs.isascii()
    docs = " ".join(raw_docs.replace("**", "").split())  # markup and line wraps do not matter
    values = all_closed_values()
    assert len(values) > 80
    missing = [v for v in values if v not in docs]
    assert missing == []
    for phrase in (
        "does not install",
        "does not download",
        "never executes",
        "declaration_only",
        "not_claimed",
        "not written to the graph",
        "CLI only, no MCP op",
        "not bound to a source snapshot",
        "python_parser_unavailable",
        "hand-authored",
        "equals_lock_entry",
        "digest_check",
        "directory entries are validated and excluded from `members`",
        "wheel_record_not_consulted",
        "no_locked_pure_wheel",
        "download(['python'])",
        "get_parser('python')",
        "--network none --read-only",
        "outside `/tmp`",
        "byte-order mark",
        "missing_dependency_source",
    ):
        assert phrase.lower() in docs.lower(), phrase
    for text in (
        "Invalid Python dependency limit: <name>",
        "Invalid --env value.",
        "Invalid --package name.",
        "--output must differ from the lock and every wheel.",
        "uv lock file cannot be read.",
        "Wheel file cannot be read.",
        "Python dependency rejected: <reason>",
        "Python dependency rejected: <reason> pypi:",
        "Python dependency snapshot cannot be written.",
        "Python dependency import failed: <TypeName>",
    ):
        assert text in docs, text
    for option in ("max-lock-bytes", "max-wheel-bytes", "max-members", "max-records"):
        assert "--" + option in docs
    assert "--max-compression-ratio" in docs and "200" in docs
    changelog = (ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.split("## [", 2)[1]
    entry = next(e for e in unreleased.split("\n- **") if "Python dependency surface" in e)
    readme = (ROOT / "README.md").read_text()
    row = next(
        line for line in readme.splitlines() if "docs/importers/python-dependencies.md" in line
    )
    for text in (entry, row):
        for phrase in ("declaration_only", "does not install", "CLI only, no MCP op"):
            assert phrase in text, phrase
    for forbidden in (
        "docs/importers/openapi.md",
        "docs/importers/opentelemetry.md",
        "docs/frameworks/rails.md",
        "canary-missions.md",
    ):
        assert forbidden not in row
    assert "canary-missions.md" not in readme
    assert readme.index("python-dependencies.md") > readme.index("docs/importers/opentelemetry.md")
