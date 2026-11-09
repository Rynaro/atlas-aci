"""ACI-S35 isolation: a read-only package, closed vocabularies, documents, docs and entries."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_s35_support import (
    BASE_TOOLS_BYTES,
    BASE_TOOLS_SHA256,
    DOCS,
    FX,
    NODE_A,
    NODE_B,
    ROOT,
    SRC,
    annotation,
    build_world,
    bundle_ids,
    claim,
    cli_run,
    envelope,
    fixture,
    import_ok,
    produce_record,
    run_rank,
    store_of,
)

from atlas_aci import __main__ as entry
from atlas_aci.__main__ import cli
from atlas_aci.config import DEFAULT_MAX_BOUND_FIELD_ELEMENTS, DEFAULT_MAX_RESPONSE_BYTES
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.feedback import outcomes as fo
from atlas_aci.feedback import ranking
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.storage import evidence as ev

PACKAGE = SRC / "feedback"
FILES = [PACKAGE / name for name in ("__init__.py", "outcomes.py", "ranking.py")]
BANNED = {
    "safe", "proven", "guaranteed", "compliant", "compatible", "incompatible", "breaking",
    "applied", "trusted", "certified", "authentic", "superior", "optimal", "better", "improves",
    "improved", "improvement", "learned",
}  # fmt: skip
ALLOWED_IMPORTS = {
    "__future__", "asyncio", "hashlib", "json", "re", "sqlite3", "collections.abc", "contextlib",
    "dataclasses", "pathlib", "typing", "xml.etree.ElementTree", "atlas_aci.config",
    "atlas_aci.enforcement", "atlas_aci.overlays.runtime", "atlas_aci.query.diff",
    "atlas_aci.query.context", "atlas_aci.query.context_limits", "atlas_aci.protocol.context",
    "atlas_aci.protocol.graph", "atlas_aci.storage.evidence",
    "atlas_aci.cross_repo.revision_vector", "atlas_aci.feedback.outcomes",
}  # fmt: skip
FORBIDDEN_CALLS = {
    "open", "mkstemp", "mkdtemp", "connect", "compile", "eval", "exec", "system", "Config",
}  # fmt: skip
FORBIDDEN_MODULES = {"os", "subprocess", "tempfile", "socket", "urllib"}
PRIOR_TITLES = (
    "Cross-repository package links", "Git co-change correlation", "ADR rationale review signals",
    "Python dependency surface", "OpenTelemetry trace import", "OpenAPI 3.1 contract import",
)  # fmt: skip
PRIOR_LOCATORS = (
    "docs/importers/openapi.md", "docs/importers/opentelemetry.md",
    "docs/importers/python-dependencies.md", "docs/frameworks/rails.md",
    "docs/cross-repository-links.md", "docs/history-signals.md", "docs/rationale-drift.md",
    "evidence-reports.md", "canary-missions.md",
)  # fmt: skip


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build_world(tmp_path_factory.mktemp("s35i"))


@pytest.fixture(scope="module")
def emitted(world):
    """The four kinds of document the package emits, from real runs."""
    raw, record = produce_record(world, "before")
    last = len(bundle_ids(record)) - 1
    document = envelope(
        record,
        [claim(record, "a", NODE_A, "passed", [last]), claim(record, "b", NODE_B, "failed", [1])],
        [annotation(record, "n", "useful", [2], "fine")],
        artifact=fixture("junit-outcomes.xml"),
    )
    store_path = import_ok(world, "before", document, raw, fixture("junit-outcomes.xml"))
    store = store_of(store_path)
    ranked = json.loads(
        run_rank(world, "before", {"schema_version": "atlas.graph.v1", "op": "context",
                                   "text": "process_payment"}, store=store_path, overlay=True,
                 ).stdout_bytes
    )  # fmt: skip
    _, summary = fo.import_feedback(
        json.dumps(document).encode(), raw, fixture("junit-outcomes.xml"), None,
        world.side("before"), fo.normalize_limits(),
    )  # fmt: skip
    pair = (world.snapshot("before"), world.generation("before"))
    evaluation = ranking.evaluate(store, {}, *pair)
    return {"store": store, "ranking": ranked, "summary": summary, "evaluation": evaluation}


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
    for node in ast.walk(ast.parse(source)):
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
            if node.attr in {"isdigit", "isidentifier", "casefold", "environ"}:
                found.append("str." + node.attr)
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and calls_itself(inner, node.name):
                    found.append("recursion " + node.name)
    return found


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def document_hits(node, banned=BANNED) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            found |= tokens(str(name)) & banned
            found |= document_hits(value, banned)
    elif isinstance(node, list):
        for value in node:
            found |= document_hits(value, banned)
    elif isinstance(node, str):
        found |= tokens(node) & banned
    return found


def string_values(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            found |= string_values(value)
    elif isinstance(node, list):
        for value in node:
            found |= string_values(value)
    elif isinstance(node, str):
        found.add(node)
    return found


def module_string_constants(source: str) -> set[str]:
    """Every string literal of the module outside docstrings and comments."""
    tree = ast.parse(source)
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Module | ast.FunctionDef | ast.ClassDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    }


def test_the_package_is_three_read_only_modules():
    assert sorted(p.name for p in PACKAGE.glob("*.py")) == [
        "__init__.py", "outcomes.py", "ranking.py",
    ]  # fmt: skip
    for path in FILES:
        text = path.read_text()
        assert text.isascii(), path
        assert max(len(line) for line in text.splitlines()) <= 100, path
        assert violations(text) == [], (path, violations(text))
        assert "alter table" not in text.lower() and "nx.compose(" not in text
        assert "Config(" not in text and "subprocess" not in text and "merge-driver" not in text
        assert tokens(text) & BANNED == set(), (path, tokens(text) & BANNED)
    assert (PACKAGE / "__init__.py").read_text().count("import") == 0
    for path in sorted(Path(__file__).parent.glob("test_s35_*.py")):
        text = path.read_text()
        assert text.isascii(), path
        assert max(len(line) for line in text.splitlines()) <= 100, path
    for name in ("_LABEL", "_TEXT", "_NOTE", "_NODEID"):
        for banned in ("\\d", "\\w", "\\s", "$"):
            assert banned not in getattr(fo, name), (name, banned)
    assert {p.name for p in FX.iterdir()} == {
        "world.json", *(f"junit-{n}.xml" for n in (
            "outcomes", "second", "doctype", "pi", "comment", "namespace", "depth", "nodecl",
            "misplaced",
        )),
    }  # fmt: skip
    for path in FX.iterdir():
        assert path.read_bytes().isascii(), path


def test_the_scans_find_what_they_forbid():
    assert violations("import os\n") == ["import os"]
    assert "call Config" in violations("x = Config(repo=1)\n")
    assert "recursion f" in violations("def f():\n    return f()\n")
    assert "regex \\d" in violations("import re\nre.fullmatch(r'\\d+', 'x')\n")
    assert "re.match" in violations("import re\nre.match('a', 'x')\n")
    assert "call open" in violations("open('x')\n")
    assert "module os" in violations("def f():\n    return os.sep\n")
    assert document_hits({"a": ["a safe value"], "optimal": 1}) == {"safe", "optimal"}
    assert document_hits({"authenticated": "not_authenticated"}) == set()
    assert string_values({"a": ["call", {"b": "import"}]}) == {"call", "import"}


def test_no_emitted_value_or_constant_is_banned_or_a_relation(emitted):
    for name, document in emitted.items():
        assert document_hits(document) == set(), name
        assert string_values(document) & ev.RELATIONS == set(), name
    sources = {p.name: p.read_text() for p in FILES}
    for name, text in sources.items():
        constants = module_string_constants(text)
        assert not any(tokens(c) & BANNED for c in constants), name
        assert constants & ev.RELATIONS == set(), name
    planted = json.loads(json.dumps(emitted["ranking"]))
    planted["events"]["active"][0]["outcome"] = "call"
    assert string_values(planted) & ev.RELATIONS == {"call"}
    planted["baseline"]["ranker"] = "an improved ranker"
    assert document_hits(planted) == {"improved"}
    values = [
        *fo.IMPORT_REJECTIONS, *fo.UNVERIFIABLE_DETAILS, *fo.ANNOTATION_DETAILS, *fo.LIMITATIONS,
        *fo.USAGE_NAMES, *ranking.RANK_REJECTIONS, *ranking.EXCLUSION_REASONS,
        *fo.ANNOTATION_KINDS, *fo.OUTCOMES, *fo.EVENT_STATUSES, *fo.ANNOTATION_STATUSES,
        ranking.ADJUSTMENT_RULE,
    ]  # fmt: skip
    assert not any(tokens(v) & BANNED for v in values)
    assert not set(values) & ev.RELATIONS


def test_the_vocabularies_are_closed_and_sized():
    assert len(fo.IMPORT_REJECTIONS) == 18 and len(set(fo.IMPORT_REJECTIONS)) == 18
    assert len(fo.UNVERIFIABLE_DETAILS) == 16 and len(fo.ANNOTATION_DETAILS) == 7
    assert len(ranking.RANK_REJECTIONS) == 12 and len(ranking.EXCLUSION_REASONS) == 3
    assert len(fo.LIMITATIONS) == 13 and len(set(fo.LIMITATIONS)) == 13
    assert len(fo.USAGE_NAMES) == 9 + 6 and len(fo.LIMIT_NAMES) == 9
    assert set(fo.ANNOTATION_DETAILS) <= set(fo.UNVERIFIABLE_DETAILS)
    assert fo.ANNOTATION_KINDS == ("useful", "dead_end") and fo.OUTCOMES == ("passed", "failed")
    assert fo.EVENT_STATUSES == ("verified_artifact",)
    assert fo.ANNOTATION_STATUSES == ("unverified_annotation",)
    assert ranking.ADJUSTMENT_RULES == ("verified_passed_distinct_artifact_selector",)
    assert "store_is_self_consistent_not_authenticated" in fo.LIMITATIONS
    assert "baseline_is_wall_clock_bounded_by_deadline_ms" in fo.LIMITATIONS
    assert fo.ATTESTATION == "operator_declared"
    assert hashlib.sha256(ev.canonical(ranking.POLICY).encode()).hexdigest() == ranking.POLICY_ID
    assert ranking.POLICY["pinned"] == "baseline_position_0"
    assert ranking.POLICY["weights"] == {
        "verified_passed": 1, "verified_failed": 0, "unverified_annotation": 0,
    }  # fmt: skip


def test_the_tools_json_and_the_seven_tools_are_unchanged():
    out = cli_run("tools", "--repo", ROOT)
    assert out.exit_code == 0
    raw = out.stdout_bytes
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    for text in (
        "feedback-import", "feedback-rank", "verified_artifact", "unverifiable_outcome",
        "atlas-feedback-",
    ):  # fmt: skip
        assert text not in out.stdout, text
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("feedback" in name for name in READ_ONLY_TOOLS)
    assert {"feedback-import", "feedback-rank"} <= set(cli.commands)
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


def test_the_commands_are_lazy_and_the_tables_agree():
    text = (SRC / "__main__.py").read_text()
    top = [line for line in text.splitlines() if line.startswith(("import ", "from "))]
    assert not any("feedback" in line for line in top)
    assert {n: (d, lo, hi) for n, lo, hi, d in entry._FEEDBACK_LIMITS} == fo.LIMITS
    assert tuple(fo.LIMITS) == fo.LIMIT_NAMES
    probe = "import sys, atlas_aci.__main__; assert 'atlas_aci.feedback' not in sys.modules"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True)
    assert done.returncode == 0, done.stderr
    settings = ranking.BaselineSettings()
    assert settings.max_response_bytes == DEFAULT_MAX_RESPONSE_BYTES
    assert settings.max_bound_field_elements == DEFAULT_MAX_BOUND_FIELD_ELEMENTS
    assert settings.context_tokenizer is None
    assert fo.normalize_limits() == fo.Limits(*(v[0] for v in fo.LIMITS.values()))


def test_the_documentation_names_every_closed_value():
    text = DOCS.read_text()
    assert text.isascii()
    assert tokens(text) & BANNED == set()
    for phrase in (
        "proves bytes, not that a run occurred", "operator_declared", "not causal", "never active",
        "recorded, not scored", "CLI only, no MCP op", "does not write the graph",
        "same checkout path", "self-consistent, not authenticated", "not replayed",
        "structuredContent", "content[0].text", "coverage_run_id", "pytest_nodeid",
        "match by bundle id", "memex root", "`error`", "changed annotation note",
        "wall-clock bounded", "baseline position 0", "Disabled means no `--overlay`",
    ):  # fmt: skip
        assert phrase in text, phrase
    values = [
        *fo.IMPORT_REJECTIONS, *fo.UNVERIFIABLE_DETAILS, *fo.ANNOTATION_DETAILS, *fo.LIMITATIONS,
        *ranking.RANK_REJECTIONS, *ranking.EXCLUSION_REASONS, *fo.ANNOTATION_KINDS, *fo.OUTCOMES,
        *fo.EVENT_STATUSES, *fo.ANNOTATION_STATUSES, *ranking.ADJUSTMENT_RULES,
        fo.ENVELOPE_FORMAT, fo.STORE_FORMAT, fo.SUMMARY_FORMAT, fo.PROFILE, fo.SELECTOR_KIND,
        ranking.RANKING_FORMAT, ranking.EVALUATION_FORMAT, ranking.POLICY_NAME,
        *(n for n in fo.USAGE_NAMES if n not in fo.LIMIT_NAMES),
    ]  # fmt: skip
    for value in values:
        assert value in text, value
    for name, (default, low, high) in fo.LIMITS.items():
        row = next(
            line for line in text.splitlines() if line.startswith("| `--" + name.replace("_", "-"))
        )
        assert all(str(n) in row for n in (default, low, high)), (name, row)
    for cli_text in (
        "Invalid feedback option: <name>",
        "--output must be outside the repository and graph root.",
        "--output must differ from every input.",
        "The repository cannot be opened as a graph namespace.",
        "--store requires --overlay.",
        "--overlay requires --store.",
        "Unsupported ranking policy.",
        "Feedback input cannot be read: <role>",
        "Feedback import rejected: <reason>[: <detail>]",
        "Feedback ranking rejected: <reason>",
        "Feedback output cannot be written.",
        "Feedback import failed: <TypeName>",
        "Feedback ranking failed: <TypeName>",
    ):  # fmt: skip
        assert cli_text in text, cli_text
    for option in (
        "--repo", "--graph-root", "--envelope", "--retrieval-record", "--artifact", "--store",
        "--output", "--request", "--overlay", *("--" + n.replace("_", "-") for n in fo.LIMIT_NAMES),
    ):  # fmt: skip
        assert option in text, option
    for role in ("envelope", "retrieval-record", "artifact", "store", "request"):
        assert role in text


def test_changelog_and_readme_entries():
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    added = lines.index("### Added", lines.index("## [3.0.0] - 2026-11-06"))
    bullets = [i for i, line in enumerate(lines) if line.startswith("- **") and i > added]
    assert lines[bullets[0]].startswith("- **Cross-repository package links (ACI-S30).")
    assert lines[bullets[1]].startswith("- **Git co-change correlation (ACI-S33).")
    assert lines[bullets[2]].startswith("- **ADR rationale review signals (ACI-S34).")
    located = next(
        i for i, line in enumerate(lines) if "Verified test-outcome feedback (ACI-S35)" in line
    )
    assert located == bullets[3]
    entry_text = "\n".join(lines[bullets[3] : bullets[4]])
    for title in PRIOR_TITLES:
        assert title not in entry_text
    for phrase in (
        "CLI only, no MCP op", "proves bytes, not that a run occurred", "operator_declared",
        "never active", "recorded, not scored", "does not write the graph",
        "docs/verified-feedback.md",
    ):  # fmt: skip
        assert phrase in entry_text, phrase
    assert tokens(entry_text) & BANNED == set()
    assert not any(line.startswith("- **") for line in lines[bullets[3] + 1 : bullets[4]])
    readme = (ROOT / "README.md").read_text().splitlines()
    row = next(line for line in readme if "docs/verified-feedback.md" in line)
    assert row.startswith("|") and row.count("|") == 3
    for phrase in ("CLI only, no MCP op", "proves bytes, not that a run occurred"):
        assert phrase in row
    for locator in PRIOR_LOCATORS:
        assert locator not in row
    assert tokens(row) & BANNED == set()
    index = next(i for i, line in enumerate(readme) if "docs/rationale-drift.md" in line)
    assert "docs/verified-feedback.md" in readme[index + 1]
    history = next(i for i, line in enumerate(readme) if "docs/history-signals.md" in line)
    assert index == history + 1
    assert DOCS.exists() and DOCS.parent == ROOT / "docs"
