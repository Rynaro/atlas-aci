"""ACI-S27 isolation: pure parser, bounded surface, closed vocabulary, docs and fixtures."""

from __future__ import annotations

import ast
import hashlib
import json
import re

from click.testing import CliRunner
from test_s21_support import workspace
from test_s27_support import DOCS, FX, ROOT, SRC, basic, example, make_world, manifest_for, raw_of

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.importers import otel_json as otel
from atlas_aci.overlays import runtime
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
MODULES = [SRC / "importers" / "otel_json.py", SRC / "overlays" / "__init__.py"]
MODULES.append(SRC / "overlays" / "runtime.py")


def test_otel_surface_is_unchanged_and_cli_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("otel" in name or "overlay" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    lowered = out.stdout.lower()
    assert "otel" not in lowered and "runtime-overlay" not in lowered
    assert "observed_parent_of" not in lowered and "observed_sample" not in lowered
    assert "import-otel" in cli.commands
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730


BANNED_IMPORTS = {
    "click", "subprocess", "socket", "urllib", "http", "time", "datetime", "random", "jsonschema",
    "atlas_aci.server", "atlas_aci.tools", "atlas_aci.protocol", "atlas_aci.codegraph",
    "atlas_aci.architecture", "atlas_aci.counterfactual",
}  # fmt: skip
OTEL_ALLOWED = {
    "__future__", "hashlib", "json", "re", "math", "dataclasses", "typing", "collections.abc",
    "atlas_aci.storage",
}  # fmt: skip
WRITE_SQL = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|PRAGMA|ATTACH)\b")


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [
                    (node.module or "") + "." + a.name for a in node.names
                ]
            for name in names:
                if any(name == b or name.startswith(b + ".") for b in BANNED_IMPORTS):
                    found.append("import " + name)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and WRITE_SQL.search(node.value)
        ):
            found.append("sql " + node.value[:30])
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                arg = node.args[0] if node.args else None
                text = arg.value if isinstance(arg, ast.Constant) else None
                if (
                    func.value.id == "re"
                    and isinstance(text, str)
                    and ("\\d" in text or "$" in text)
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
    return found


def test_otel_modules_are_pure_ascii_and_read_only():
    assert sorted(p.name for p in (SRC / "overlays").glob("*.py")) == ["__init__.py", "runtime.py"]
    for module in MODULES:
        text = module.read_text()
        assert violations(text) == [], module.name
        assert text.isascii(), module.name
        assert "alter table" not in text.lower() and "nx.compose(" not in text
        assert all(len(line) <= 100 for line in text.splitlines()), module.name
    planted = (
        "import click\nimport time\nfrom atlas_aci.tools import search_text\n"
        "import re\nre.compile(r'\\d')\nre.fullmatch('a$', 'a')\n"
        "q = 'INSERT INTO t VALUES (1)'\nr = 'delete from x'\ns = 'DROP TABLE x'\n"
        "def loop(n):\n    return loop(n - 1)\n"
    )
    assert {
        "import click",
        "import time",
        "import atlas_aci.tools",
        "regex \\d",
        "regex a$",
        "recursion loop",
    } <= set(violations(planted))
    assert any(v.startswith("sql INSERT") for v in violations(planted))
    # The pure parser imports only what the S29 purity scan allows, and nothing else.
    tree = ast.parse(MODULES[0].read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == OTEL_ALLOWED
    assert not {"os", "sys", "pathlib", "io", "tempfile", "sqlite3"} & imported
    assert "open(" not in MODULES[0].read_text()


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


BANNED_VOCABULARY = {
    "safe", "verified", "proven", "guaranteed", "compliant",
    "compatible", "incompatible", "breaking",
}  # fmt: skip


def _vocabulary_hits(node, key=None) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            found |= _tokens(str(name)) & BANNED_VOCABULARY
            found |= _vocabulary_hits(value, name)
    elif isinstance(node, list):
        for value in node:
            found |= _vocabulary_hits(value, key)
    elif isinstance(node, str) and key in {
        "kind", "classification", "reason", "format", "profile", "type", "status", "relation",
        "semantics", "evidence_coverage", "completeness", "sampling", "attestation", "starts_at",
        "relation_semantics", "name", "version",
    }:  # fmt: skip
        found |= _tokens(node) & BANNED_VOCABULARY
    return found


def test_otel_vocabulary_does_not_overclaim(tmp_path):
    world = make_world(tmp_path)
    overlay = runtime.import_otel(
        world.side, raw_of(manifest_for(basic(), world.snapshot_id)), basic()
    )
    mismatch = runtime.import_otel(world.side, (FX / "manifest-example.json").read_bytes(), basic())
    outputs = [
        overlay,
        mismatch,
        runtime.runtime_paths(overlay),
        runtime.summary_document(overlay),
    ]
    assert all(_vocabulary_hits(item) == set() for item in outputs)
    assert _vocabulary_hits({"verified": 1, "kind": "breaking"}) == {"verified", "breaking"}
    constants = [
        *otel.REJECTIONS, *otel.LIMITS, *otel.LOCATION_STATES, *runtime.LIMITATIONS,
        *runtime.UNLINKED_REASONS, *runtime.CLASSIFICATIONS, *runtime.OVERLAY_REJECTIONS,
        *runtime.USAGE_REASONS, *runtime.STARTS_AT, *runtime.TOP_KEYS, *runtime.OBSERVATION_KEYS,
        *runtime.RELATION_KEYS, *runtime.SUMMARY_KEYS, *runtime.PATHS_KEYS,
        *runtime.LOCATION_COUNTS,
        otel.MANIFEST_FORMAT, otel.PROFILE, otel.IMPORTER_NAME, otel.IMPORTER_VERSION,
        runtime.FORMAT, runtime.PATHS_FORMAT, runtime.SUMMARY_FORMAT, runtime.EVIDENCE_COVERAGE,
        runtime.COMPLETENESS, runtime.SAMPLING, runtime.SEMANTICS, runtime.RELATION,
        runtime.ATTESTATION,
    ]  # fmt: skip
    assert constants and all(not _tokens(token) & BANNED_VOCABULARY for token in constants)
    assert len(runtime.CLASSIFICATIONS) == 9 and len(runtime.LIMITATIONS) == 14
    assert (
        "linked_symbol",
        "linked_file",
        *("unlinked_" + r for r in runtime.UNLINKED_REASONS),
    ) == runtime.CLASSIFICATIONS


def test_otel_docs_name_every_closed_value_and_state_the_interpretation():
    docs = DOCS.read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    readme = (ROOT / "README.md").read_text()
    row = next(line for line in readme.splitlines() if "docs/importers/opentelemetry.md" in line)
    unreleased = changelog.split("\n## [3.0.0]", 1)[1].split("\n## [", 1)[0]
    entry = unreleased[unreleased.index("OpenTelemetry trace import") :].split("\n- **", 1)[0]
    names = [
        *otel.REJECTIONS, *otel.LIMITS, *runtime.OVERLAY_REJECTIONS, *runtime.LIMITATIONS,
        *runtime.CLASSIFICATIONS, *runtime.UNLINKED_REASONS, *runtime.SUMMARY_KEYS,
        *runtime.LOCATION_COUNTS, *runtime.TOP_KEYS, *runtime.OBSERVATION_KEYS,
        *runtime.LINKAGE_KEYS, *runtime.SYMBOL_KEYS,
        otel.MANIFEST_FORMAT, otel.PROFILE, otel.IMPORTER_VERSION, runtime.FORMAT,
        runtime.PATHS_FORMAT, runtime.SUMMARY_FORMAT, "MAX_MANIFEST_BYTES", "MAX_NESTING_DEPTH",
        "MAX_INT_DIGITS", "import-otel", "index_done", "check-architecture",
        "does not write the graph",
        "not a call", "observed_sample", "completeness is unknown", "manifest-only",
        "Invalid OpenTelemetry import limit:",
        "--output must be outside --repo and the graph root.",
        "--output must differ from the manifest and the artifact.",
        "The repository cannot be opened as a graph namespace.",
        "OpenTelemetry manifest cannot be read.", "OpenTelemetry artifact cannot be read.",
        "OpenTelemetry import rejected:", "Runtime overlay cannot be written.",
        "OpenTelemetry import failed:", "observed_parent_of", "runtime_paths", "O(spans)",
        "valid-span boundary", "Windows paths", "case differences", "Unicode normalization",
        "endTimeUnixNano", "all-zero", "max_paths", "max_path_length",
    ]  # fmt: skip
    assert [n for n in names if n not in docs] == []
    for text in (entry, row):
        assert "does not write the graph" in text and "observed_parent_of" in text
    assert "CLI only, no MCP op" in row and "CLI only, no MCP op" in entry
    assert "observed_sample" in row and "observed_sample" in entry
    assert "completeness is unknown" in docs and "not a call" in docs
    assert "runtime_observation" in docs and "not_performed" in docs


def test_otel_fixtures_are_listed_with_their_digests():
    provenance = json.loads((FX / "provenance.json").read_bytes())
    assert provenance["authored"] == "hand-authored for ACI-S27; no external source"
    listed = provenance["file"]
    on_disk = sorted(
        p.name for p in FX.iterdir() if p.name not in {".gitattributes", "provenance.json"}
    )
    assert on_disk == sorted(listed) and listed
    for name, entry in listed.items():
        data = (FX / name).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest(), name
        assert entry["bytes"] == len(data) and entry["purpose"], name
    assert (FX / ".gitattributes").read_text() == "* -text\n"
    manifest = example()
    assert manifest["artifact"] == {
        "sha256": hashlib.sha256(basic()).hexdigest(),
        "bytes": len(basic()),
    }
    assert not (ROOT / ".gitattributes").exists()
    assert Path_is_ascii(__file__)


def Path_is_ascii(path: str) -> bool:
    with open(path, encoding="utf-8") as handle:
        return handle.read().isascii()
