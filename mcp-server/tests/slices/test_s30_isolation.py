"""ACI-S30 isolation: surface, purity, vocabulary, validators, docs, changelog and README."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_s21_support import workspace
from test_s30_support import (
    BASE_TOOLS_BYTES,
    BASE_TOOLS_SHA256,
    DOCS,
    FX,
    ROOT,
    SRC,
    file_entity,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    make_world,
)

from atlas_aci.__main__ import cli
from atlas_aci.cross_repo import package_links as pl
from atlas_aci.cross_repo import revision_vector as rv
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.storage import evidence as ev

pytestmark = pytest.mark.usefixtures("grammar")

PACKAGE_FILES = sorted((SRC / "cross_repo").glob("*.py"))
BANNED_VOCABULARY = {
    "safe", "verified", "proven", "guaranteed", "compliant",
    "compatible", "incompatible", "breaking", "applied",
}  # fmt: skip
BANNED_IMPORTS = {
    "click", "subprocess", "socket", "urllib", "http", "time", "datetime", "random", "os", "sys",
    "tempfile", "shutil", "io", "atlas_aci.server", "atlas_aci.tools", "atlas_aci.protocol",
    "atlas_aci.codegraph", "atlas_aci.architecture", "atlas_aci.counterfactual",
    "atlas_aci.query.impact", "atlas_aci.storage.snapshots",
}  # fmt: skip
ALLOWED_IMPORTS = {
    "__future__", "hashlib", "json", "re", "sqlite3", "contextlib", "collections.abc",
    "dataclasses", "typing", "atlas_aci.overlays.runtime", "atlas_aci.query.diff",
    "atlas_aci.dependencies.uv_lock", "atlas_aci.dependencies.python_wheel",
    "atlas_aci.storage.evidence", "atlas_aci.cross_repo.revision_vector",
    "atlas_aci.cross_repo.package_links",
}  # fmt: skip
WRITE_SQL = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|PRAGMA|ATTACH)\b")
BANNED_CALLS = {"open", "isdigit", "isalpha", "isalnum", "isnumeric", "isspace", "casefold"}
BANNED_CALLS |= {"isdecimal", "isidentifier", "normalize", "setrecursionlimit"}
BANNED_TEXT = ("os.replace", "tempfile", "sqlite3.connect", "re.IGNORECASE", "re.UNICODE")


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def violations(source: str) -> list[str]:
    """Everything the S30 purity scan forbids in one module's source text."""
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                names = [base] + [base + "." + a.name for a in node.names]
            for name in names:
                if any(name == b or name.startswith(b + ".") for b in BANNED_IMPORTS):
                    found.append("import " + name)
        is_text = isinstance(node, ast.Constant) and isinstance(node.value, str)
        if is_text and WRITE_SQL.search(node.value):  # type: ignore[attr-defined]
            found.append("sql " + node.value[:30])  # type: ignore[attr-defined]
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in BANNED_CALLS:
                found.append("call " + name)
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                arg = node.args[0] if node.args else None
                text = arg.value if isinstance(arg, ast.Constant) else None
                banned = ("\\d", "\\w", "\\s", "$")
                if (
                    func.value.id == "re"
                    and isinstance(text, str)
                    and any(b in text for b in banned)
                ):
                    found.append("regex " + text)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    func = inner.func
                    if isinstance(func, ast.Name) and func.id == node.name:
                        found.append("recursion " + node.name)
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == node.name
                        and isinstance(func.value, ast.Name)
                        and func.value.id in ("self", "cls")
                    ):
                        found.append("recursion " + node.name)
    for text in BANNED_TEXT:
        if text in source:
            found.append("text " + text)
    return found


def imports_of(source: str) -> set[str]:
    """The modules a source imports; `from pkg import name` counts as `pkg.name` for a module."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for alias in node.names:
                path = SRC.parent / (base + "." + alias.name).replace(".", "/")
                is_module = path.with_suffix(".py").exists() or path.is_dir()
                found.add(base + "." + alias.name if is_module else base)
    return found


# ---- the surface ------------------------------------------------------------------------------


def test_the_cross_repository_surface_is_cli_only_and_the_tools_are_unchanged(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("cross" in name or "overlay" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    lowered = out.stdout.lower()
    for word in ("cross-repo", "cross_repo", "revision_vector", "external_reference"):
        assert word not in lowered
    assert {"cross-repo-link", "cross-repo-impact"} <= set(cli.commands)
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    assert set(ev.RELATIONS) == {
        "call", "construct", "extend", "import", "include", "prepend", "rationale_for",
        "superclass",
    }  # fmt: skip
    assert pl.RELATION not in ev.RELATIONS  # a link is not an existing graph relation


def test_the_cross_repo_package_is_exactly_three_pure_ascii_modules():
    assert [f.name for f in PACKAGE_FILES] == [
        "__init__.py",
        "package_links.py",
        "revision_vector.py",
    ]
    for file in PACKAGE_FILES:
        text = file.read_text()
        assert text.isascii(), file.name
        assert all(len(line) <= 100 for line in text.splitlines()), file.name
        assert "alter table" not in text.lower() and "nx.compose(" not in text
        assert violations(text) == [], (file.name, violations(text))
    assert Path(__file__).read_text().isascii()


def test_the_purity_scan_catches_what_it_forbids():
    planted = (
        "import click\nimport os\nfrom atlas_aci.codegraph import CodeGraph\nimport re\n"
        "re.compile(r'\\d')\nre.fullmatch('a$', 'a')\nq = 'INSERT INTO t VALUES (1)'\n"
        "open('x')\nname.isidentifier()\nos.replace('a', 'b')\nsqlite3.connect('x')\n"
        "def loop(n):\n    return loop(n - 1)\n"
    )
    found = set(violations(planted))
    assert {
        "import click", "import os", "import atlas_aci.codegraph", "regex \\d", "regex a$",
        "call open", "call isidentifier", "recursion loop", "text os.replace",
        "text sqlite3.connect",
    } <= found  # fmt: skip
    assert any(v.startswith("sql INSERT") for v in found)


def test_the_import_allowlist_is_exact():
    for file in PACKAGE_FILES:
        found = imports_of(file.read_text())
        assert found <= ALLOWED_IMPORTS, (file.name, sorted(found - ALLOWED_IMPORTS))
    all_found = set().union(*(imports_of(f.read_text()) for f in PACKAGE_FILES))
    assert "atlas_aci.overlays.runtime" in all_found and "atlas_aci.query.diff" in all_found
    assert not any("codegraph" in name for name in all_found)


def test_only_select_statements_are_issued():
    statements = [
        value
        for name, value in vars(pl).items()
        if name.endswith("_SQL") and isinstance(value, str)
    ]
    assert len(statements) >= 6
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert all(WRITE_SQL.search(s) is None for s in statements)
    assert WRITE_SQL.search("PRAGMA journal_mode") is not None  # the guard fires on a plant


# ---- vocabulary -------------------------------------------------------------------------------


def hits(node) -> set[str]:
    """Banned tokens in every key and every string value of an output document."""
    found: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            found |= tokens(str(name)) & BANNED_VOCABULARY
            found |= hits(value)
    elif isinstance(node, list):
        for value in node:
            found |= hits(value)
    elif isinstance(node, str):
        found |= tokens(node) & BANNED_VOCABULARY
    return found


def module_constants(source: str) -> list[str]:
    """Every string in a module-level assignment (the S30 scan scope, with output keys/values)."""
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            found += [
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
    return found


def test_the_vocabulary_does_not_overclaim(tmp_path):
    """Scope: module-level string constants of both modules plus every key and string value that
    the overlay, impact and summary documents emit (SQL text is not vocabulary)."""
    world = make_world(tmp_path)
    built = world.link()
    seed = file_entity("app/main.py")
    outputs = [
        built,
        pl.summary_document(built),
        world.impact(built, seed, "to_provider"),
        world.impact(built, seed, "to_provider", budgets=pl.normalize_budgets({"max_results": 1})),
    ]
    assert all(hits(item) == set() for item in outputs)
    assert hits({"verified": 1, "kind": "breaking", "x": ["applied"]}) == {
        "verified", "breaking", "applied",
    }  # fmt: skip
    constants = [text for f in PACKAGE_FILES for text in module_constants(f.read_text())]
    sql = {v for name, v in vars(pl).items() if name.endswith("_SQL")}
    constants = [c for c in constants if c not in sql]
    assert len(constants) > 150
    assert all(not tokens(c) & BANNED_VOCABULARY for c in constants), [
        c for c in constants if tokens(c) & BANNED_VOCABULARY
    ]
    assert tokens("provider_name_rebound") & BANNED_VOCABULARY == set()
    assert module_constants("A = 'import_export_safe'") == ["import_export_safe"]


def test_the_indexer_flag_is_read_inline_and_never_emitted(tmp_path):
    literal = "import_export_safe"
    for file in PACKAGE_FILES:
        tree = ast.parse(file.read_text())
        assert literal not in module_constants(file.read_text())
        inside = [
            n
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef)
            for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and n.value == literal
        ]
        everywhere = [
            n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == literal
        ]
        assert len(inside) == len(everywhere) == (1 if file.name == "package_links.py" else 0)
    built = make_world(tmp_path).link()
    assert literal not in json.dumps(built) and "safe" not in json.dumps(built)


# ---- the validators ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("iso"))


@pytest.fixture(scope="module")
def good(world):
    return world.link()


def linked_reference(doc):
    return next(i for i in doc["references"] if i["resolution"] == "linked_declaration")


def tamperings():
    def reorder_vector(o):
        o["revision_vector"].reverse()

    def other_vector(o):
        o["links"][0]["revision_vector"][1]["snapshot_id"] = "7" * 64

    def link_id(o):
        o["links"][0]["id"] = "6" * 64

    def unknown_resolution(o):
        o["references"][0]["resolution"] = "linked_somewhere"

    def illegal_detail(o):
        linked_reference(o)["detail"] = "name_not_declared"

    def illegal_mismatch(o):
        ref = next(
            i for i in o["references"] if i["resolution"] == "unresolved_reexport_not_followed"
        )
        ref["detail"] = "coordinate_differs"

    def unlinked(o):
        linked_reference(o)["link_id"] = None

    def orphan(o):
        o["links"].pop()

    def counts(o):
        o["counts"]["links"] += 1

    def by_resolution(o):
        o["counts"]["by_resolution"]["linked_declaration"] -= 1

    def coverage(o):
        o["coverage"]["call_binding"] = "claimed"

    def limitations(o):
        o["limitations"].pop()

    def unsorted_references(o):
        o["references"].reverse()

    def unsorted_links(o):
        o["links"].reverse()

    def superseded_in_links(o):
        o["supersedes"] = {
            "overlay_id": "5" * 64, "vector_digest": "4" * 64, "links_invalidated": 1,
            "invalidated_link_ids": [o["links"][0]["id"]], "invalidated_link_ids_truncated": False,
        }  # fmt: skip

    def unsorted_supersedes(o):
        o["supersedes"] = {
            "overlay_id": "5" * 64, "vector_digest": "4" * 64, "links_invalidated": 2,
            "invalidated_link_ids": ["9" * 64, "8" * 64], "invalidated_link_ids_truncated": False,
        }  # fmt: skip

    def miscounted_supersedes(o):
        o["supersedes"] = {
            "overlay_id": "5" * 64, "vector_digest": "4" * 64, "links_invalidated": 5,
            "invalidated_link_ids": ["8" * 64], "invalidated_link_ids_truncated": False,
        }  # fmt: skip

    def extra_top(o):
        o["extra"] = 1

    def extra_reference(o):
        o["references"][0]["extra"] = 1

    def extra_link(o):
        o["links"][0]["extra"] = 1

    def relation(o):
        o["links"][0]["relation"] = "call"

    def semantics(o):
        o["links"][0]["call_semantics"] = "claimed"

    def provider_state(o):
        o["provider"]["state"] = "unavailable"

    def provider_matched(o):
        o["provider"]["matched"] = False

    def binding_state(o):
        o["package"]["binding"]["state"] = "version_mismatch"

    def binding_counts(o):
        o["package"]["binding"]["members_equal"] += 1

    def source_prefix(o):
        o["links"][0]["provider"]["path"] = "other/" + o["links"][0]["provider"]["member"]

    def attestation(o):
        o["consumer"]["lock"]["attestation"] = "verified"

    def producer(o):
        o["producer"]["version"] = "atlas-cross-repo-linker.v2"

    def qualified(o):
        linked_reference(o)["qualified_name"] = "s30_shapes.other.name"

    def type_name(o):
        o["references"][0]["type"] = "call"

    def mapping_digest(o):
        o["mapping"]["digest"] = "xyz"

    def booleans(o):
        o["counts"]["links"] = True

    return {k: v for k, v in locals().items() if callable(v) and not k.startswith("_")}


@pytest.mark.parametrize("name", sorted(tamperings()))
def test_validate_overlay_rejects_every_tampering(good, name):
    pl.validate_overlay(good)
    broken = copy.deepcopy(good)
    tamperings()[name](broken)
    broken["overlay_id"] = pl.overlay_identity(broken)  # sealed: only a deeper check can refuse
    with pytest.raises(rv.CrossRepoRejected) as caught:
        pl.validate_overlay(broken)
    assert caught.value.reason == "invalid_overlay"


def test_the_overlay_and_vector_identities_must_recompute(good):
    for key in ("overlay_id", "vector_digest"):
        broken = copy.deepcopy(good)
        broken[key] = "0" * 64
        with pytest.raises(rv.CrossRepoRejected):
            pl.validate_overlay(broken)
    assert pl.overlay_identity(good) == good["overlay_id"]
    assert pl.content_identity(good) == good["overlay_id"]  # supersedes is already null
    assert rv.vector_digest(good["revision_vector"]) == good["vector_digest"]


def test_validate_impact_rejects_tamperings(world, good):
    seed = file_entity("app/main.py")
    doc = world.impact(good, seed, "to_provider")
    pl.validate_impact(doc)

    def sealed(change):
        broken = copy.deepcopy(doc)
        change(broken)
        broken["result_id"] = pl._impact_identity(broken)
        with pytest.raises(rv.CrossRepoRejected):
            pl.validate_impact(broken)

    unsealed = copy.deepcopy(doc)
    unsealed["status"] = "applied"
    with pytest.raises(rv.CrossRepoRejected):
        pl.validate_impact(unsealed)
    assert len(doc["results"]) > 1 and len(doc["boundaries"]) > 1
    sealed(lambda d: d.update(status="confirmed"))
    sealed(lambda d: d.update(basis="runtime"))
    sealed(lambda d: d.update(runtime_observation="performed"))
    sealed(lambda d: d.update(call_semantics="claimed"))
    sealed(lambda d: d["results"].reverse())
    sealed(lambda d: d["boundaries"].reverse())
    sealed(lambda d: d["boundaries"][0].update(kind="elsewhere"))
    sealed(lambda d: d["boundaries"][0].update(detail="made_up"))
    sealed(lambda d: d.update(complete=False))
    sealed(lambda d: d["work"].update(results=0))
    sealed(lambda d: d["results"][0].update(relation="call"))
    sealed(lambda d: d.update(extra=1))
    sealed(lambda d: d["budgets"].update(max_results=0))
    sealed(lambda d: d.update(direction="sideways"))
    assert pl.BOUNDARY_KINDS == (
        "missing_provider_snapshot", "unresolved_external_reference", "budget_exhausted",
    )  # fmt: skip


# ---- the strict loader ------------------------------------------------------------------------


def test_the_one_strict_loader_refuses_what_it_must():
    load = rv.load_strict
    assert load(b'{"a": [1, 2, {"b": 12345678901234567890}]}', numbers=True)["a"][2]["b"] > 0
    for raw in (
        b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}',
        b'{"a": 1.5}', b'{"a": 1e3}', b'{"a": ' + b"9" * 21 + b"}", b'{"a": -' + b"9" * 21 + b"}",
        b"\xef\xbb\xbf{}", b"\xff", b"[" * 100000, b'{"a": ' * 5000, b"",
    ):  # fmt: skip
        with pytest.raises(ValueError):
            load(raw, numbers=True)
    with pytest.raises(ValueError):
        load(b'{"a": 1}', numbers=False)  # the mapping has no numbers
    assert load(b'{"a": "b"}', numbers=False) == {"a": "b"}
    assert load(b'{"a": ' + b"9" * 20 + b"}", numbers=True)["a"] == int("9" * 20)


# ---- documents --------------------------------------------------------------------------------


def docs() -> str:
    return DOCS.read_text()


def test_the_docs_are_ascii_and_name_every_closed_value():
    text = docs()
    assert text.isascii() and "\t" not in text
    closed = [
        pl.FORMAT, pl.IMPACT_FORMAT, pl.SUMMARY_FORMAT, rv.MAPPING_FORMAT, rv.VECTOR_FORMAT,
        pl.PRODUCER["name"], pl.PRODUCER["version"], pl.ATTESTATION, pl.RELATION, pl.SEMANTICS,
        pl.STATUS, pl.BASIS, pl.RUNTIME_OBSERVATION,
        *pl.RESOLUTIONS, *pl.MISMATCH_DETAILS, *pl.PROVIDER_DETAILS, *pl.FORM_DETAILS,
        *pl.SYMBOL_DETAILS, *pl.AMBIGUOUS_DETAILS, *pl.FORMS, *pl.BINDING_STATES,
        *pl.BOUNDARY_KINDS, *pl.DIRECTIONS, *pl.LIMITATIONS, *pl.REJECTIONS, *pl.LIMITS,
        *pl.BUDGETS, *pl.COVERAGE.values(),
        *(v for values in pl.CHECK_VALUES.values() for v in values),
    ]  # fmt: skip
    missing = [item for item in closed if item not in text]
    assert not missing, missing
    for name, (default, _low, high) in {**pl.LIMITS, **pl.BUDGETS}.items():
        assert str(default) in text and str(high) in text, name
    for phrase in (
        "not a call", "call_semantics", "does not write the graph", "CLI only, no MCP op",
        "operator_declared", "re-exports are not followed", "conditional", "active generation",
        "source_member_missing", "never discovers, clones or fetches", "index_done",
        "file-level", "log", "tmp", "build", "dist", "coverage", "node_modules",
        "common dir", "worktree", "must not overlap", "decorated",
    ):  # fmt: skip
        assert phrase in text, phrase
    example = (FX / "mapping-example.json").read_text()
    assert example.strip() in text
    for message in (
        "Invalid cross-repository limit:", "Invalid --seed value.",
        "--output must be outside both repositories and both graph roots.",
        "--output must differ from every input file.",
        "A repository cannot be opened as a graph namespace.",
        "The repositories and graph roots must not overlap.",
        "Cross-repository mapping cannot be read.", "uv lock file cannot be read.",
        "Package snapshot cannot be read.", "Previous overlay cannot be read.",
        "Cross-repository overlay cannot be read.", "Cross-repository link rejected:",
        "Cross-repository impact rejected:", "Cross-repository overlay cannot be written.",
        "Cross-repository link failed:", "Cross-repository impact failed:",
    ):  # fmt: skip
        assert message in text, message


def test_the_changelog_and_readme_record_the_surface():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    lines = changelog.splitlines()
    start = next(i for i, line in enumerate(lines) if "Cross-repository package links" in line)
    bullet = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith("- **") or line.startswith("#"):
            break
        bullet.append(line)
    entry = "\n".join(bullet)
    assert entry.startswith("- **Cross-repository package links")
    assert "not a call" in entry and "CLI only, no MCP op" in entry
    for title in (
        "Python dependency surface",
        "OpenTelemetry trace import",
        "OpenAPI 3.1 contract import",
    ):
        assert title not in entry
    added = lines.index("### Added", lines.index("## [3.0.0] - 2026-11-06"))
    first_bullet = next(i for i, line in enumerate(lines) if line.startswith("- **") and i > added)
    assert first_bullet == start  # the new entry sits at the top of 3.0.0/Added
    readme = (ROOT / "README.md").read_text().splitlines()
    row = next(line for line in readme if "docs/cross-repository-links.md" in line)
    assert row.startswith("|") and "not a call" in row and "CLI only, no MCP op" in row
    for locator in (
        "docs/importers/openapi.md", "docs/importers/opentelemetry.md",
        "docs/importers/python-dependencies.md", "docs/frameworks/rails.md", "canary-missions.md",
    ):  # fmt: skip
        assert locator not in row
    assert DOCS.exists() and DOCS.parent == ROOT / "docs"
