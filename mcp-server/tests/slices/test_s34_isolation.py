"""ACI-S34 isolation: a read-only package, closed vocabularies, validated documents, the docs."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_s34_support import (
    BASE_TOOLS_BYTES,
    BASE_TOOLS_SHA256,
    DOCS,
    FX,
    ROOT,
    SRC,
    build_world,
    cli_run,
)

from atlas_aci import __main__ as entry
from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.query import diff as symbol_diff
from atlas_aci.rationale import adr_mapping as am
from atlas_aci.rationale import drift
from atlas_aci.storage import evidence as ev

PACKAGE = SRC / "rationale"
FILES = [PACKAGE / name for name in ("__init__.py", "adr_mapping.py", "drift.py")]
BANNED = {
    "safe", "verified", "proven", "guaranteed", "compliant", "compatible", "incompatible",
    "breaking", "applied", "followed", "violated", "invalidated",
}  # fmt: skip
ALLOWED_IMPORTS = {
    "__future__", "hashlib", "json", "re", "sqlite3", "collections.abc", "contextlib",
    "dataclasses", "pathlib", "typing", "atlas_aci.overlays.runtime", "atlas_aci.query.diff",
    "atlas_aci.storage.evidence", "atlas_aci.cross_repo.revision_vector",
    "atlas_aci.rationale.adr_mapping",
}  # fmt: skip
SQL_LITERALS = {
    "SELECT payload FROM entities WHERE id=?",
    "SELECT payload FROM entities WHERE path=?",
    "SELECT payload FROM occurrences WHERE path=?",
}
FORBIDDEN_CALLS = {"open", "mkstemp", "mkdtemp", "connect", "compile", "eval", "exec", "system"}
FORBIDDEN_MODULES = {"os", "subprocess", "tempfile", "socket", "urllib"}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build_world(tmp_path_factory.mktemp("s34i"))


@pytest.fixture(scope="module")
def overlay(world):
    return am.map_rationale(world.raw(), world.before)


@pytest.fixture(scope="module")
def document(world):
    return drift.rationale_drift(world.mixed(), world.before, world.after)


def call_parts(node: ast.Call) -> tuple[str, str]:
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else ""
        return owner, func.attr
    return "", getattr(func, "id", "")


def calls_itself(node: ast.Call, name: str) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == name
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
        and func.attr == name
    )


def violations(source: str) -> list[str]:
    """Every use the package must not make, found by walking the syntax tree."""
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += ["import " + a.name for a in node.names if a.name not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module + "." + a.name for a in node.names]
            if module not in ALLOWED_IMPORTS and not all(n in ALLOWED_IMPORTS for n in names):
                found.append("from " + module)
        elif isinstance(node, ast.Call):
            owner, name = call_parts(node)
            if owner in FORBIDDEN_MODULES:
                found.append("call " + owner + "." + name)
            if name in FORBIDDEN_CALLS:
                found.append("call " + name)
            if owner == "re":
                if name != "fullmatch":
                    found.append("re." + name)
                for argument in node.args[:1]:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        found += [
                            "regex " + t for t in ("\\d", "\\w", "\\s", "$") if t in argument.value
                        ]
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in FORBIDDEN_MODULES:
                found.append("module " + node.value.id)
            if node.attr in {"isdigit", "isidentifier", "normalize", "casefold", "environ"}:
                found.append("str." + node.attr)
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and calls_itself(inner, node.name):
                    found.append("recursion " + node.name)
    return found


def sql_literals(source: str) -> set[str]:
    pattern = r"\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|PRAGMA|ALTER)\b"
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.match(pattern, node.value)
    }


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def constant_hits(source: str) -> set[str]:
    """Banned words in module-level constants (names are not scanned, values are)."""
    found: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.isupper() for t in node.targets
        ):
            for inner in ast.walk(node.value):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    found |= tokens(inner.value) & BANNED
    return found


def document_hits(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            found |= tokens(str(name)) & BANNED
            found |= document_hits(value)
    elif isinstance(node, list):
        for value in node:
            found |= document_hits(value)
    elif isinstance(node, str):
        found |= tokens(node) & BANNED
    return found


def test_the_package_is_three_read_only_modules():
    assert sorted(p.name for p in PACKAGE.glob("*.py")) == [
        "__init__.py",
        "adr_mapping.py",
        "drift.py",
    ]
    for path in FILES:
        text = path.read_text()
        assert text.isascii(), path
        assert max(len(line) for line in text.splitlines()) <= 100, path
        assert violations(text) == [], path
        assert "alter table" not in text.lower() and "nx.compose(" not in text
        assert tokens(text) & BANNED == set(), (path, tokens(text) & BANNED)
    for path in sorted(Path(__file__).parent.glob("test_s34_*.py")):
        assert path.read_text().isascii(), path
    assert sql_literals(FILES[1].read_text()) == SQL_LITERALS
    assert sql_literals(FILES[2].read_text()) == set()
    assert tuple(sorted(SQL_LITERALS, key=lambda s: ("path" in s, "occurrences" in s))) == (
        am.ENTITY_PAYLOAD_SQL,
        am.ENTITIES_BY_PATH_SQL,
        am.OCCURRENCES_BY_PATH_SQL,
    )


def test_the_scan_finds_what_it_forbids():
    planted = (
        "import socket\n"
        "import os, re, tempfile, subprocess\n"
        "from urllib import request\n"
        "def f(a):\n"
        "    os.replace('a', 'b')\n"
        "    open('x')\n"
        "    tempfile.mkstemp()\n"
        "    subprocess.run(['x'])\n"
        "    re.match(r'\\d+$', 'x')\n"
        "    x = os.environ['HOME']\n"
        "    'x'.isdigit()\n"
        "    'SELECT 1'\n"
        "    return f(a)\n"
    )
    hits = violations(planted)
    for expected in (
        "import socket", "import os", "from urllib", "call os.replace", "call open",
        "call tempfile.mkstemp", "call subprocess.run", "regex \\d", "regex $", "re.match",
        "module os", "str.isdigit", "recursion f",
    ):  # fmt: skip
        assert expected in hits, (expected, hits)
    assert sql_literals(planted) == {"SELECT 1"}
    assert violations("import re\nre.fullmatch(r'[0-9]+', 'x')\n") == []


def test_the_vocabulary_does_not_overclaim(overlay, document):
    for path in FILES:
        assert constant_hits(path.read_text()) == set()
    assert constant_hits("X = ('verified', 'ok')\nY = {'a': 'breaking_change'}\n") == {
        "verified",
        "breaking",
    }
    assert constant_hits("X = ('followed_by', 'invalidated')\n") == {"followed", "invalidated"}
    assert document_hits(overlay) == set() and document_hits(document) == set()
    assert document_hits({"kind": "safe_to_remove", "x": ["violated"]}) == {"safe", "violated"}
    everything = {
        *am.REJECTIONS, *am.IMPORT_REASONS, *am.USAGE_NAMES, *am.LIMITATIONS,
        *drift.DRIFT_REJECTIONS, *drift.HISTORY_REASONS, *drift.DRIFT_REASONS, *drift.OUTCOMES,
        *drift.SOURCE_STATES, *drift.DECISION_STATES, *drift.STAGES, *am.TARGET_CLASSES,
        am.LINK_KIND, drift.REVIEW_RULE,
    }  # fmt: skip
    assert not everything & ev.RELATIONS
    for key in (*am.OVERLAY_KEYS, *am.RESOLVED_TARGET_KEYS, *drift.DRIFT_KEYS, *drift.RESULT_KEYS):
        assert "relation" not in key
    assert len(am.REJECTIONS) == 10 and len(drift.DRIFT_REJECTIONS) == 16
    assert len(set(drift.DRIFT_REJECTIONS)) == 16 and len(am.LIMITATIONS) == 12
    assert set(drift.HISTORY_REASONS) >= set(symbol_diff.UNKNOWN_REASONS)
    assert len(am.IMPORT_REASONS) == 9 and len(drift.HISTORY_REASONS) == 15
    assert drift.OUTCOMES[:3] == drift.FINDING_KINDS


def mutated(value, edit):
    copy = json.loads(json.dumps(value))
    edit(copy)
    return copy


def reseal(doc):
    """Recompute everything derived, so only the edited legality check can still fail."""
    doc["findings"] = [drift.finding(r) for r in doc["results"] if r["outcome"] != "no_finding"]
    doc["counts"] = drift._counts(doc["results"])
    doc["id"] = drift.drift_identity(doc)
    return doc


def test_validate_overlay_rejects_every_drift(overlay, world):
    am.validate_overlay(overlay)
    unresolved = am.map_rationale(world.raw("mapping-unresolved.json"), world.before)
    am.validate_overlay(unresolved)

    def top(key, value):
        return lambda c: c.__setitem__(key, value)

    def first(key, value, group="resolved"):
        return lambda c: c[group][0].__setitem__(key, value)

    def nested(group, outer, key, value):
        return lambda c: c[group][0][outer].__setitem__(key, value)

    def swap(c):
        c["resolved"][0], c["resolved"][1] = c["resolved"][1], c["resolved"][0]

    def twin(c):
        c["resolved"][1] = c["resolved"][0]

    def file_occurrence(c):
        record = next(r for r in c["resolved"] if r["target"]["target_class"] == "file")
        record["target"]["occurrence_id"] = "a" * 64

    cases = [
        top("extra", 1), top("format", "x"), top("producer", {"name": "x", "version": "y"}),
        top("link_kind", "rationale_for"), top("link_kind", "call"), top("mapping_digest", "z"),
        top("snapshot", {"snapshot_id": "a" * 64}), top("counts", {"mappings": 1}),
        top("limitations", []), top("id", "0" * 64), top("resolved", {}),
        first("relation", "call"), first("type", "x"), first("mapping_id", "-bad"),
        first("snapshot_id", "0" * 64), first("generation_id", "0" * 64), first("id", "0" * 64),
        nested("resolved", "target", "target_class", "other"),
        nested("resolved", "target", "relation", "import"),
        nested("resolved", "span", "start_line", 0),
        nested("resolved", "span", "end_byte", 0),
        nested("resolved", "document", "sha256", "q"),
        swap, twin, file_occurrence,
    ]  # fmt: skip
    for edit in cases:
        with pytest.raises(am.RationaleRejected) as info:
            am.validate_overlay(mutated(overlay, edit))
        assert info.value.reason == "invalid_overlay"
    bad_unresolved = [
        first("reason", "nope", "unresolved"), first("stage", "comparison", "unresolved"),
        first("snapshot_id", "0" * 64, "unresolved"), first("type", "x", "unresolved"),
        nested("unresolved", "target", "extra", 1), nested("unresolved", "span", "sha256", "x"),
    ]  # fmt: skip
    for edit in bad_unresolved:
        with pytest.raises(am.RationaleRejected):
            am.validate_overlay(mutated(unresolved, edit))
    for value in (None, [], "x", 3):
        with pytest.raises(am.RationaleRejected):
            am.validate_overlay(value)


def test_validate_drift_rejects_every_drift(document, world):
    drift.validate_drift(document)
    results = {r["mapping_id"]: r for r in document["results"]}
    same_digest = results["settings-file"]["target_source_sha256_before"]
    # The edited-document control is legal.
    assert results["summary-doc-edited"]["outcome"] == "no_finding"
    rejected = drift.rationale_drift(world.mixed(), world.before, world.config)
    drift.validate_drift(rejected)

    def at(name, **fields):
        def edit(doc):
            for item in doc["results"]:
                if item["mapping_id"] == name:
                    item.update(fields)
            reseal(doc)

        return edit

    def top(key, value):
        return lambda c: c.__setitem__(key, value)

    def reorder(doc):
        doc["results"].reverse()
        reseal(doc)

    def duplicate(doc):
        doc["results"][1] = dict(doc["results"][0])
        reseal(doc)

    def bogus_finding(doc):
        doc["findings"].append(dict(doc["findings"][0], id="0" * 64))

    def finding_not_in_results(doc):
        doc["findings"][0]["mapping_id"] = "other"

    def stale_finding(doc):
        doc["findings"][0]["id"] = "0" * 64

    def wrong_count(doc):
        doc["counts"]["no_finding"] += 1

    def relation_key(doc):
        doc["results"][0]["relation"] = "call"

    legal_but_wrong = [
        at("charge", decision_state="document_changed"),
        at("charge", source_state="unchanged"),
        at("charge", reason="x"),
        at("charge", document_sha256_after="e" * 64),
        at("charge-doc-edited", decision_state="document_unchanged"),
        at("charge-doc-edited", document_sha256_after=None),
        at("audit", source_state="body_changed"),
        at("fragile", reason="not_a_reason"),
        at("fragile", decision_state="not_compared"),
        at("fragile", detail="x"),
        at("retire", source_state="unknown"),
        at("retire", reason="document_not_in_snapshot"),
        at("charge-legacy-doc", decision_state="document_unchanged"),
        at("settings-file", target_source_sha256_after=None),
        at("settings-file", target_source_sha256_after=same_digest),
        at("no-doc", source_state="unchanged"),
        at("no-doc", reason="target_removed"),
        at("no-doc", target_class="symbol"),
        at("charge", outcome="nope"),
        at("charge", stage="nope"),
        at("charge", candidate_ids=["b" * 64, "a" * 64]),
        at("charge", change_record_id="x"),
        at("charge", target_entity_id="x"),
        at("charge", mapping_id="-"),
        at("charge", extra=1),
        reorder, duplicate,
    ]  # fmt: skip
    for edit in legal_but_wrong:
        with pytest.raises(am.RationaleRejected) as info:
            drift.validate_drift(mutated(document, edit))
        assert info.value.reason == "invalid_drift"
    others = [
        top("extra", 1), top("format", "x"), top("producer", {}), top("review_rule", "x"),
        top("mapping_digest", "x"), top("overlay_id", "x"), top("limitations", ["x"]),
        top("id", "0" * 64), top("before", {"snapshot_id": "a" * 64}),
        top("comparison", {"state": "compared", "change_set_id": None, "reason": None}),
        top("comparison", {"state": "rejected", "change_set_id": "a" * 64, "reason": "x"}),
        top("comparison", {"state": "rejected", "change_set_id": None, "reason": "nope"}),
        top("comparison", {"state": "x", "change_set_id": None, "reason": None}),
        top("results", {}), bogus_finding, finding_not_in_results, stale_finding,
        wrong_count, relation_key,
    ]  # fmt: skip
    for edit in others:
        with pytest.raises(am.RationaleRejected):
            drift.validate_drift(mutated(document, edit))
    # A rejected comparison must be reported by every compared result.
    with pytest.raises(am.RationaleRejected):
        drift.validate_drift(mutated(rejected, at("charge", reason="extraction_failed_after")))
    with pytest.raises(am.RationaleRejected):
        drift.validate_drift(mutated(rejected, at("charge", detail="schema_mismatch")))
    # A compared document may not carry a comparison_rejected result.
    with pytest.raises(am.RationaleRejected):
        drift.validate_drift(
            mutated(document, at("fragile", reason="comparison_rejected", detail="x"))
        )
    for value in (None, [], "x"):
        with pytest.raises(am.RationaleRejected):
            drift.validate_drift(value)


def test_validate_summary_rejects_every_drift(overlay, document):
    summary = am.summary_document(overlay)
    am.validate_summary(summary)
    am.validate_summary(am.summary_document(document))
    assert am.summary_document(document)["command"] == "rationale-drift"
    edits = [
        ("format", "x"), ("command", "x"), ("document_format", "atlas-rationale-drift.v1"),
        ("document_id", "z"), ("mapping_digest", "z"), ("counts", {"mappings": 1}),
        ("counts", {**summary["counts"], "resolved": -1}), ("extra", 1),
    ]  # fmt: skip
    for key, value in edits:
        with pytest.raises(am.RationaleRejected):
            am.validate_summary({**summary, key: value})
    with pytest.raises(am.RationaleRejected):
        am.validate_summary(None)


def test_the_surface_is_unchanged():
    out = cli_run("tools", "--repo", ROOT)
    assert out.exit_code == 0
    raw = out.stdout_bytes
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    for text in (
        "rationale-map", "rationale-drift", "rationale_review_needed", "unresolved_mapping",
        "atlas-rationale-",
    ):  # fmt: skip
        assert text not in out.stdout, text
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("rationale" in name for name in READ_ONLY_TOOLS)
    assert {"rationale-map", "rationale-drift"} <= set(cli.commands)
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    assert (
        frozenset(
            {
                "call", "construct", "import", "superclass", "include", "extend", "prepend",
                "rationale_for",
            }
        )
        == ev.RELATIONS
    )  # fmt: skip


def test_the_commands_are_lazy():
    text = (SRC / "__main__.py").read_text()
    top = [line for line in text.splitlines() if line.startswith(("import ", "from "))]
    assert not any("rationale" in line for line in top)
    assert {n: (d, lo, hi) for n, lo, hi, d in entry._RATIONALE_LIMITS} == am.LIMITS
    probe = "import sys, atlas_aci.__main__; assert 'atlas_aci.rationale' not in sys.modules"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True)
    assert done.returncode == 0, done.stderr
    assert (SRC / "rationale/__init__.py").read_text().count("import") == 0


def test_the_documentation_names_every_closed_value():
    text = DOCS.read_text()
    assert text.isascii()
    assert tokens(text) & BANNED == set()
    for phrase in (
        "observation, not a verdict", "never rebound", "read from the snapshot",
        "whole-document digest", "does not write the graph", "CLI only, no MCP op",
        "same checkout path", "ordinal", "recreation", "present but non-indexed document",
        "operator_declared_rationale_link", "atlas-rationale-mapping.v1",
        "atlas-rationale-overlay.v1", "atlas-rationale-drift.v1", "atlas-rationale-summary.v1",
        "rationale_review_needed", "sibling_ordinal_shift",
    ):  # fmt: skip
        assert phrase in text, phrase
    values = [
        *am.REJECTIONS, *am.IMPORT_REASONS, *am.LIMITATIONS, *drift.DRIFT_REJECTIONS,
        *drift.HISTORY_REASONS, *drift.DRIFT_REASONS, *drift.OUTCOMES, *drift.SOURCE_STATES,
        *drift.DECISION_STATES, *drift.STAGES, *drift.COMPARISON_STATES, *am.TARGET_CLASSES,
        am.LINK_KIND, drift.REVIEW_RULE, *symbol_diff.UNKNOWN_REASONS,
    ]  # fmt: skip
    for value in values:
        assert value in text, value
    block = text.split("exactly these sixteen:", 1)[1].split("```")[1]
    assert tuple(t.strip() for t in block.replace("\n", " ").split(",") if t.strip()) == (
        drift.DRIFT_REJECTIONS
    )
    for name, (default, low, high) in am.LIMITS.items():
        row = next(
            line for line in text.splitlines() if line.startswith("| `--" + name.replace("_", "-"))
        )
        assert all(str(n) in row for n in (default, low, high)), (name, row)
    for cli_text in (
        "Invalid rationale option: <name>",
        "Give --repo, or both --before-repo and --after-repo.",
        "--output must be outside the repositories and graph roots.",
        "--output must differ from the mapping.",
        "The repository cannot be opened as a graph namespace.",
        "Both sides resolve to the same graph namespace.",
        "Rationale mapping cannot be read.",
        "Rationale mapping rejected: <reason>",
        "Rationale drift rejected: <reason>",
        "Rationale output cannot be written.",
        "Rationale mapping failed: <TypeName>",
        "Rationale drift failed: <TypeName>",
    ):
        assert cli_text in text, cli_text
    for option in (
        "--mapping", "--repo", "--before-repo", "--after-repo", "--graph-root",
        "--before-graph-root", "--after-graph-root", "--output", "--max-mapping-bytes",
        "--max-mappings", "--max-output-bytes",
    ):  # fmt: skip
        assert option in text, option
    assert (FX / "world.json").read_text().isascii()
    assert sorted(p.suffix for p in FX.iterdir()) == [".json"] * 4


def test_changelog_and_readme_entries():
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    added = lines.index("### Added", lines.index("## [3.0.0] - 2026-11-06"))
    bullets = [i for i, line in enumerate(lines) if line.startswith("- **") and i > added]
    assert lines[bullets[0]].startswith("- **Cross-repository package links (ACI-S30).")
    assert lines[bullets[1]].startswith("- **Git co-change correlation (ACI-S33).")
    assert lines[bullets[2]].startswith("- **ADR rationale review signals (ACI-S34).")
    entry_text = "\n".join(lines[bullets[2] : bullets[3]])
    for title in (
        "Cross-repository package links", "Git co-change correlation", "Python dependency surface",
        "OpenTelemetry trace import", "OpenAPI 3.1 contract import",
    ):  # fmt: skip
        assert title not in entry_text
    for phrase in ("observation, not a verdict", "CLI only, no MCP op", "never rebound"):
        assert phrase in entry_text
    assert tokens(entry_text) & BANNED == set()
    readme = (ROOT / "README.md").read_text().splitlines()
    row = next(line for line in readme if "docs/rationale-drift.md" in line)
    assert row.startswith("|")
    for phrase in ("observation, not a verdict", "CLI only, no MCP op"):
        assert phrase in row
    for locator in (
        "docs/importers/openapi.md", "docs/importers/opentelemetry.md",
        "docs/importers/python-dependencies.md", "docs/frameworks/rails.md",
        "docs/cross-repository-links.md", "docs/history-signals.md", "evidence-reports.md",
        "canary-missions.md",
    ):  # fmt: skip
        assert locator not in row
    assert tokens(row) & BANNED == set()
    index = next(i for i, line in enumerate(readme) if "docs/history-signals.md" in line)
    assert "docs/rationale-drift.md" in readme[index + 1]
    assert DOCS.exists() and DOCS.parent == ROOT / "docs"
