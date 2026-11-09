"""ACI-S33 isolation: a read-only package, a closed vocabulary, a validated overlay, the docs."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_s33_support import build_indexed, cli_run, provision_grammar, run

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.history import cochange as lib
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.storage import evidence as ev

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "mcp-server/src/atlas_aci/history"
MODULE = PACKAGE / "cochange.py"
DOCS = ROOT / "docs/history-signals.md"
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
BANNED = {
    "safe", "verified", "proven", "guaranteed", "compliant", "compatible", "incompatible",
    "breaking", "applied",
}  # fmt: skip
ALLOWED_IMPORTS = {
    "__future__", "hashlib", "json", "os", "re", "signal", "sqlite3", "subprocess", "threading",
    "time", "collections.abc", "contextlib", "dataclasses", "itertools", "pathlib", "typing",
    "atlas_aci.overlays.runtime", "atlas_aci.query.diff", "atlas_aci.storage.evidence",
}  # fmt: skip
FORBIDDEN_OS = {"system", "popen", "replace", "fork", "kill"}


@pytest.fixture(scope="session")
def grammar():
    return provision_grammar()


@pytest.fixture(scope="module")
def main(grammar, tmp_path_factory):
    return build_indexed("main", tmp_path_factory.mktemp("s33i-main"))


def call_parts(node: ast.Call) -> tuple[str, str]:
    """(owner, name) of a call: `os.replace(...)` is ("os", "replace"), `f(...)` is ("", "f")."""
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


def popen_is_hardened(node: ast.Call) -> bool:
    keywords = {k.arg: k.value for k in node.keywords}
    env = keywords.get("env")
    return (
        call_parts(node)[1] == "Popen"
        and "shell" not in keywords
        and isinstance(env, ast.Call)
        and call_parts(env) == ("", "_git_env")
        and bool(node.args)
        and isinstance(node.args[0], ast.Name)
    )


def violations(source: str) -> list[str]:
    """Every use the package must not make, found by walking the syntax tree."""
    found: list[str] = []
    tree = ast.parse(source)
    allowed_environ: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if (
                node.func.attr == "get"
                and isinstance(receiver, ast.Attribute)
                and receiver.attr == "environ"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "PATH"
            ):
                allowed_environ.add(id(receiver))
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
            if owner == "subprocess" and not popen_is_hardened(node):
                found.append("subprocess " + name)
            if owner == "os" and (name in FORBIDDEN_OS or name.startswith(("exec", "spawn"))):
                found.append("call os." + name)
            if name in {"open", "mkstemp", "mkdtemp", "connect", "compile", "eval", "exec"}:
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
            if isinstance(node.value, ast.Name) and node.value.id == "tempfile":
                found.append("tempfile")
            if node.attr in {"isdigit", "isidentifier", "normalize", "casefold"}:
                found.append("str." + node.attr)
            if node.attr == "environ" and id(node) not in allowed_environ:
                found.append("os.environ")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = r"\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|PRAGMA)\b"
            if re.match(sql, node.value):
                found.append("sql literal")
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and calls_itself(inner, node.name):
                    found.append("recursion " + node.name)
    return found


def test_the_package_is_one_read_only_module():
    assert sorted(p.name for p in PACKAGE.glob("*.py")) == ["__init__.py", "cochange.py"]
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        assert text.isascii(), path
        assert max(len(line) for line in text.splitlines()) <= 100, path
    assert violations(MODULE.read_text()) == []
    assert Path(__file__).read_text().isascii()
    assert "merge." not in MODULE.read_text() or ".driver" not in MODULE.read_text()
    assert "alter table" not in MODULE.read_text().lower()
    assert "nx.compose(" not in MODULE.read_text()


def test_the_scan_finds_what_it_forbids():
    planted = (
        "import socket\n"
        "import subprocess, os, re, tempfile, sqlite3\n"
        "def f(a):\n"
        "    subprocess.run(['git'], shell=True)\n"
        "    subprocess.Popen(a, env={})\n"
        "    os.system('x')\n"
        "    open('x')\n"
        "    os.replace('a', 'b')\n"
        "    tempfile.mkstemp()\n"
        "    sqlite3.connect('x')\n"
        "    re.match(r'\\d+$', 'x')\n"
        "    x = os.environ['HOME']\n"
        "    'x'.isdigit()\n"
        "    'SELECT 1'\n"
        "    return f(a)\n"
    )
    hits = violations(planted)
    for expected in (
        "import socket", "subprocess run", "subprocess Popen", "call os.system", "call open",
        "call os.replace", "tempfile", "call connect", "regex \\d", "regex $", "re.match",
        "os.environ", "str.isdigit", "sql literal", "recursion f",
    ):  # fmt: skip
        assert expected in hits, (expected, hits)
    good = "import subprocess\ndef g(argv):\n    subprocess.Popen(argv, env=_git_env())\n"
    assert violations(good) == []
    assert violations("import os\nx = os.environ.get('PATH', '/bin')\n") == []


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def vocabulary_hits(source: str) -> set[str]:
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


def overlay_hits(node, key=None) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            found |= tokens(str(name)) & BANNED
            found |= overlay_hits(value, name)
    elif isinstance(node, list):
        for value in node:
            found |= overlay_hits(value, key)
    elif isinstance(node, str) and key not in {"a", "b", "generated", "id", "repository_id"}:
        found |= tokens(node) & BANNED
    return found


def test_the_vocabulary_does_not_overclaim(main):
    assert vocabulary_hits(MODULE.read_text()) == set()
    assert vocabulary_hits("X = ('verified', 'ok')\nY = {'a': 'breaking_change'}\n") == {
        "verified",
        "breaking",
    }
    overlay = run(main)
    assert overlay_hits(overlay) == set()
    assert overlay_hits({"kind": "safe_to_remove"}) == {"safe"}
    assert not set(lib.REJECTIONS) & ev.RELATIONS and not set(lib.LIMITATIONS) & ev.RELATIONS
    assert len(lib.REJECTIONS) == 16 and len(set(lib.REJECTIONS)) == 16
    assert lib.INCOMPLETE_REASONS == (
        "commit_budget_exhausted", "object_missing", "shallow_boundary_in_window",
        "start_commit_missing",
    )  # fmt: skip
    assert lib.COVERAGE_STATES == ("complete", "incomplete_history")
    assert len(lib.COUNT_KEYS) == 12 and lib.EVIDENCE_KIND == "co_change_correlation"
    assert "relation" not in lib.TOP_KEYS + lib.RECORD_KEYS


def mutated(overlay, edit):
    copy = json.loads(json.dumps(overlay))
    edit(copy)
    return copy


def test_validate_overlay_rejects_every_drift(main):
    overlay = run(main)
    truncated = run(main, max_commits=1)
    lib.validate_overlay(overlay)
    lib.validate_overlay(truncated)
    records = overlay["records"]
    assert len(records) > 2

    def set_path(*path, value):
        def edit(copy):
            target = copy
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = value

        return edit

    def swap(copy):
        copy["records"][0], copy["records"][1] = copy["records"][1], copy["records"][0]

    def with_record(copy):
        copy["records"] = records

    cases = {
        "extra key": lambda c: c.update(extra=1),
        "missing key": lambda c: c.pop("limitations"),
        "format": set_path("format", value="atlas-history-overlay.v2"),
        "producer": set_path("producer", "version", value="other"),
        "repository kind": set_path("repository", "kind", value="svn"),
        "object format": set_path("repository", "object_format", value="sha256"),
        "label": set_path("repository", "id", value="Bad Label"),
        "window equal": set_path("window", "end_commit", value=overlay["window"]["start_commit"]),
        "window rule": set_path("window", "rule", value="inclusive"),
        "binding": set_path("source_snapshot", "binding", value="operator_declared"),
        "containment": set_path("source_snapshot", "containment", value="equal"),
        "files compared": set_path("source_snapshot", "files_compared", value=-1),
        "policy changed without a digest": set_path(
            "filter", "policy", "bulk", "threshold", value=7
        ),
        "policy grammar": set_path("filter", "policy", "generated", "names", value=["a/b"]),
        "policy unsorted": set_path(
            "filter", "policy", "generated", "suffixes", value=[".x", ".a"]
        ),
        "policy constant": set_path("filter", "policy", "merge_commits", value="included"),
        "digest": set_path("filter", "filter_digest", value="0" * 64),
        "evidence kind": set_path("evidence", "kind", value="call"),
        "dependency claim": set_path("evidence", "dependency_claim", value="declared"),
        "causality claim": set_path("evidence", "causality_claim", value="declared"),
        "coverage state": set_path("coverage", "state", value="incomplete_history"),
        "coverage reasons": set_path("coverage", "reasons", value=["object_missing"]),
        "commit budget": set_path("coverage", "commit_budget", value=5),
        "counts total": set_path("counts", "commits_in_window", value=99),
        "counts pairs": set_path("counts", "pairs", value=1),
        "counts observations": set_path("counts", "pair_observations", value=1),
        "counts key": lambda c: c["counts"].update(extra=1),
        "counts negative": set_path("counts", "merge_commits", value=-1),
        "unsorted records": swap,
        "support": set_path("records", 0, "support", value=99),
        "a not below b": lambda c: c["records"][0].update(
            a=c["records"][0]["b"], b=c["records"][0]["a"]
        ),
        "duplicate commits": lambda c: c["records"][1].update(
            supporting_commits=[c["records"][1]["supporting_commits"][0]] * 2, support=2
        ),
        "commit grammar": set_path("records", 0, "supporting_commits", 0, value="XYZ"),
        "record id": set_path("records", 0, "id", value="0" * 64),
        "record path": set_path("records", 0, "a", value="../x"),
        "record relation key": lambda c: c["records"][0].update(relation="call"),
        "record kind": set_path("records", 0, "type", value="edge"),
        "limitation drift": lambda c: c["limitations"].pop(),
        "key": set_path("key", value="0" * 64),
        "overlay id": set_path("overlay_id", value="0" * 64),
        "top relation key": lambda c: c.update(relation="call"),
        "relation value": set_path("evidence", "semantics", value="call"),
    }
    for name, edit in cases.items():
        with pytest.raises(lib.HistoryRejected) as caught:
            lib.validate_overlay(mutated(overlay, edit))
        assert caught.value.reason == "invalid_overlay", name
    # an incomplete overlay never carries records or counts
    for name, edit in {
        "records with incomplete": with_record,
        "counts with incomplete": set_path("counts", value=overlay["counts"]),
        "state without a reason": set_path("coverage", "reasons", value=[]),
        "two reasons with the budget": set_path(
            "coverage", "reasons", value=["commit_budget_exhausted", "object_missing"]
        ),
        "records state": set_path("coverage", "records", value="complete"),
    }.items():
        with pytest.raises(lib.HistoryRejected) as caught:
            lib.validate_overlay(mutated(truncated, edit))
        assert caught.value.reason == "invalid_overlay", name
    # an operator label may be any label, even one that spells a relation
    named = run(main, repository_id="import")
    assert named["repository"]["id"] == "import"
    summary = lib.summary_document(overlay)
    lib.validate_summary(summary)
    for field, value in (
        ("format", "x"), ("evidence_kind", "call"), ("records", -1), ("reasons", ["nope"]),
        ("coverage_state", "partial"), ("overlay_id", "z"),
    ):  # fmt: skip
        with pytest.raises(lib.HistoryRejected):
            lib.validate_summary({**summary, field: value})
    with pytest.raises(lib.HistoryRejected):
        lib.validate_summary({**summary, "extra": 1})


def test_the_surface_is_unchanged():
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("history" in name or "cochange" in name for name in READ_ONLY_TOOLS)
    out = cli_run("tools", "--repo", ROOT)
    assert out.exit_code == 0
    raw = out.stdout_bytes
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert "co_change" not in out.stdout and "cochange" not in out.stdout
    assert "cochange" in cli.commands
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    assert (
        frozenset(
            {
                "call",
                "construct",
                "import",
                "superclass",
                "include",
                "extend",
                "prepend",
                "rationale_for",
            }
        )
        == ev.RELATIONS
    )


def test_the_command_is_lazy():
    text = (ROOT / "mcp-server/src/atlas_aci/__main__.py").read_text()
    top = [line for line in text.splitlines() if line.startswith(("import ", "from "))]
    assert not any("history" in line for line in top)
    from atlas_aci import __main__ as entry

    assert {n: (lo, hi, d) for n, lo, hi, d in entry._HISTORY_LIMITS} == lib.LIMITS
    probe = "import sys, atlas_aci.__main__; assert 'atlas_aci.history' not in sys.modules"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True)
    assert done.returncode == 0, done.stderr


def test_the_documentation_names_every_closed_value():
    text = DOCS.read_text()
    assert text.isascii()
    for phrase in (
        "correlation", "not a dependency", "not causality", "start_exclusive_end_inclusive",
        "records are withheld", "dubious ownership", "safe.directory is never overridden",
        "sha1", "does not write the graph", "CLI only, no MCP op", "co_change_correlation",
        "outside_snapshot_history", "atlas-history-overlay.v1", "atlas-history-summary.v1",
        "atlas-history-filter.v1", ".gitignore", "clean checkout", "operator label",
        "snapshot id", "partial clone",
    ):  # fmt: skip
        assert phrase in text, phrase
    values = [
        *lib.REJECTIONS, *lib.INCOMPLETE_REASONS, *lib.COVERAGE_STATES, *lib.COUNT_KEYS,
        *lib.LIMITATIONS, *lib.TOP_KEYS, *lib.RECORD_KEYS, *lib.SUMMARY_KEYS,
        *(item for kind in lib.DEFAULT_GENERATED.values() for item in kind),
    ]  # fmt: skip
    for value in values:
        assert value in text, value
    for name, (low, high, default) in {**lib.LIMITS, **lib.POLICY_LIMITS}.items():
        row = next(
            line for line in text.splitlines() if line.startswith("| `--" + name.replace("_", "-"))
        )
        assert all(str(n) in row for n in (low, high, default)), (name, row)
    for cli_text in (
        "Invalid co-change option: <name>",
        "--output must be outside --repo and the graph root.",
        "The repository cannot be opened as a graph namespace.",
        "History co-change rejected: <reason>",
        "History overlay cannot be written.",
        "History co-change failed: <TypeName>",
    ):
        assert cli_text in text, cli_text
    for option in (
        "--repo", "--repository-id", "--start", "--end", "--source-commit", "--snapshot-id",
        "--graph-root", "--output", "--generated-prefix", "--generated-name",
        "--generated-suffix", "--no-generated-defaults",
    ):  # fmt: skip
        assert option in text, option


def test_changelog_and_readme_entries():
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    added = lines.index("### Added", lines.index("## [3.0.0] - 2026-11-06"))
    bullets = [i for i, line in enumerate(lines) if line.startswith("- **") and i > added]
    first, second = bullets[0], bullets[1]
    assert lines[first].startswith("- **Cross-repository package links (ACI-S30).")
    assert lines[second].startswith("- **Git co-change correlation (ACI-S33).")
    entry = "\n".join(lines[second : bullets[2]])
    for title in (
        "Cross-repository package links", "Python dependency surface",
        "OpenTelemetry trace import", "OpenAPI 3.1 contract import",
    ):  # fmt: skip
        assert title not in entry
    for phrase in ("correlation", "not a dependency", "CLI only, no MCP op", "incomplete_history"):
        assert phrase in entry
    readme = (ROOT / "README.md").read_text().splitlines()
    row = next(line for line in readme if "docs/history-signals.md" in line)
    assert row.startswith("|")
    for phrase in ("correlation", "not a dependency", "CLI only, no MCP op"):
        assert phrase in row
    for locator in (
        "docs/importers/openapi.md", "docs/importers/opentelemetry.md",
        "docs/importers/python-dependencies.md", "docs/frameworks/rails.md",
        "docs/cross-repository-links.md", "evidence-reports.md", "canary-missions.md",
    ):  # fmt: skip
        assert locator not in row
    assert "canary-missions.md" not in "\n".join(line for line in readme if "history" in line)
    after_links = next(
        i for i, line in enumerate(readme) if "docs/cross-repository-links.md" in line
    )
    assert "docs/history-signals.md" in readme[after_links + 1]
    assert DOCS.exists() and DOCS.parent == ROOT / "docs"
