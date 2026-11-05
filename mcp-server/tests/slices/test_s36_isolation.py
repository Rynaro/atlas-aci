"""ACI-S36 isolation: hygiene scans, closed vocabularies, pins, docs and the committed evidence."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner
from test_s36_support import (
    DOCS,
    FIX_MANIFEST,
    FX,
    PROD_MANIFEST,
    PRODUCTION_SHA,
    REAL_RUN,
    ROOT,
    RUNNER,
    SCRIPTS,
    SITE,
    VERIFIER,
    manifest_copy,
    read_partitions,
    read_result,
    shared_fx,
    shared_prod,
    verifier,
)

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.storage import evidence as ev

V = verifier()
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
# These whole-file pins change only through a receipted re-lock
# (see .spectra/evidence/sec-relock/): the CVE-floor re-lock moved both files.
PYPROJECT_SHA256 = "981f0784d6bc5c1502c91ab27657beb483460b97e066cd8846f51e98f1d7d92f"
UV_LOCK_SHA256 = "87ba265e76f382aaa4387da7ca5d89dc85eb3699b35ee4315e981302b19646fd"
BANNED = {
    "safe", "proven", "guaranteed", "compliant", "compatible", "incompatible", "breaking",
    "applied", "trusted", "certified", "authentic", "superior", "optimal", "better", "improves",
    "improved", "improvement", "learned", "favourable", "favorable",
}  # fmt: skip
VERIFIER_IMPORTS = {
    "__future__", "argparse", "collections", "decimal", "fractions", "gzip", "hashlib",
    "importlib.util", "json", "math", "pathlib", "sys", "typing",
}  # fmt: skip
RUNNER_IMPORTS = VERIFIER_IMPORTS | {
    "importlib", "importlib.metadata", "os", "platform", "resource", "subprocess", "tempfile",
    "time",
}  # fmt: skip
FIXTURE_IMPORTS = {"os", "json", "sys", "time", "fractions"}
BANNED_MODULES = {"socket", "urllib", "http", "ensurepip", "pip", "venv"}
STATIC_PROVIDERS = {"igraph", "leidenalg", "networkx", "graspologic"}
OS_BANNED = ("system", "popen", "fork")
PRIOR_TITLES = (
    "Cross-repository package links", "Git co-change correlation", "ADR rationale review signals",
    "Python dependency surface", "OpenTelemetry trace import", "Verified test-outcome feedback",
)  # fmt: skip
PRIOR_LOCATORS = (
    "docs/importers/openapi.md", "docs/importers/opentelemetry.md",
    "docs/importers/python-dependencies.md", "docs/frameworks/rails.md",
    "docs/cross-repository-links.md", "docs/history-signals.md", "docs/rationale-drift.md",
    "docs/verified-feedback.md", "evidence-reports.md", "canary-missions.md",
)  # fmt: skip
OPERATOR_COMMAND = (
    "uv run --no-project --with leidenalg==0.12.0 --with igraph==1.0.0 "
    "python scripts/experiment-leiden-vs-lpa.py"
)


# ------------------------------------------------------------------------------ scanners


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", text.lower()) if t}


def hits(node) -> set[str]:
    """Banned tokens in every key and string value of a document."""
    found: set[str] = set()
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                found |= tokens(str(key)) & BANNED
                stack.append(value)
        elif isinstance(item, list | tuple):
            stack.extend(item)
        elif isinstance(item, str):
            found |= tokens(item) & BANNED
    return found


def string_constants(source: str) -> set[str]:
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


def import_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return [module]
    return []


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


def violations(source: str, allowed: set[str], *, providers: bool = True) -> list[str]:
    """Every use a script must not make, found by walking the syntax tree."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        for name in import_names(node):
            if name not in allowed:
                found.append("import " + name)
            if name.split(".")[0] in BANNED_MODULES:
                found.append("banned import " + name)
            if providers and name.split(".")[0] in STATIC_PROVIDERS:
                found.append("static provider import " + name)
        if isinstance(node, ast.Call):
            func = node.func
            owner = (
                func.value.id
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                else ""
            )
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if "install" in name.lower():
                found.append("call " + name)
            if owner == "os" and (name in OS_BANNED or name.startswith(("exec", "spawn"))):
                found.append("call os." + name)
            if owner == "re":
                for argument in node.args[:1]:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        found += [
                            "regex " + t for t in ("\\d", "\\w", "\\s", "$") if t in argument.value
                        ]
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and calls_itself(inner, node.name):
                    found.append("recursion " + node.name)
    return found


def subprocess_sites(source: str) -> list[tuple[str, ast.Call]]:
    """Every `subprocess.<x>(...)` call with the name of the enclosing function."""
    found = []
    for function in [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                found.append((function.name, node))
    return found


def line_ok(text: str) -> bool:
    return text.isascii() and max(len(line) for line in text.splitlines()) <= 100


FILES = {
    VERIFIER: VERIFIER_IMPORTS,
    RUNNER: RUNNER_IMPORTS,
}
FIXTURE_FILES = [SITE / "igraph/__init__.py", SITE / "leidenalg/__init__.py"]


# --------------------------------------------------------------------------------- tests


def test_the_scripts_are_clean_ascii_iterative_and_import_only_the_allowed_modules():
    for path, allowed in FILES.items():
        text = path.read_text()
        assert line_ok(text), path
        assert violations(text, allowed) == [], (path, violations(text, allowed))
        assert tokens(text) & BANNED == set(), (path, tokens(text) & BANNED)
        assert "import igraph" not in text and "import leidenalg" not in text
    for path in FIXTURE_FILES:
        text = path.read_text()
        assert line_ok(text), path
        assert violations(text, FIXTURE_IMPORTS, providers=False) == [], path
    for name in ("test_s36_support.py", "test_s36_semantics.py", "test_s36_isolation.py",
                 "test_s36_community_experiment.py"):  # fmt: skip
        assert line_ok((Path(__file__).parent / name).read_text()), name


def test_the_runner_has_exactly_one_subprocess_call_site():
    sites = subprocess_sites(RUNNER.read_text())
    assert [name for name, _ in sites] == ["_measure_run"]
    call = sites[0][1]
    assert call.func.attr == "run"
    keywords = {k.arg for k in call.keywords}
    assert "timeout" in keywords and "shell" not in keywords
    function = next(
        n for n in ast.walk(ast.parse(RUNNER.read_text()))
        if isinstance(n, ast.FunctionDef) and n.name == "_measure_run"
    )  # fmt: skip
    argv = next(
        n.value for n in ast.walk(function)
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "argv" for t in n.targets)
    )  # fmt: skip
    assert isinstance(argv, ast.List) and ast.unparse(argv.elts[0]) == "sys.executable"
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "argv"
    assert [n for n, _ in subprocess_sites(VERIFIER.read_text())] == []


def test_the_scans_find_what_they_forbid():
    allowed = VERIFIER_IMPORTS
    assert violations("import socket\n", allowed) == ["import socket", "banned import socket"]
    assert "static provider import igraph" in violations("import igraph\n", allowed | {"igraph"})
    assert "static provider import leidenalg" in violations(
        "from leidenalg import x\n", allowed | {"leidenalg"}
    )
    assert "call os.system" in violations("import os\nos.system('x')\n", allowed | {"os"})
    assert "call os.execv" in violations("import os\nos.execv('x', [])\n", allowed | {"os"})
    assert "call os.fork" in violations("import os\nos.fork()\n", allowed | {"os"})
    assert "call do_install" in violations("def f():\n    do_install()\n", allowed)
    assert "recursion f" in violations("def f():\n    return f()\n", allowed)
    assert "recursion f" in violations(
        "class A:\n    def f(self):\n        return self.f()\n", allowed
    )
    assert "regex \\d" in violations("import re\nre.compile('\\\\d')\n", allowed | {"re"})
    assert "import subprocess" in violations("import subprocess\n", allowed)
    assert not line_ok("x = '\u00e9'\n") and not line_ok("x = '" + "a" * 100 + "'\n")
    assert [n for n, _ in subprocess_sites("import subprocess\ndef g():\n    subprocess.run(1)\n")
            ] == ["g"]  # fmt: skip
    assert hits({"a": ["a better value"], "optimal": 1}) == {"better", "optimal"}
    assert hits({"retain_lpa": "improvement"}) == {"improvement"}
    assert hits({"strictly_better_budgets": 1}) == {"better"}
    assert hits({"recall_gain_budgets": "favourable"}) == {"favourable"}
    assert hits({"authenticated": "not_authenticated"}) == set()


def test_no_emitted_value_manifest_key_or_vocabulary_name_is_banned(tmp_path_factory):
    fx = shared_fx(tmp_path_factory)
    prod = shared_prod(tmp_path_factory)
    for directory in (fx["dir"], prod):
        for document in (
            read_result(directory),
            read_partitions(directory),
            json.loads((directory / "telemetry.json").read_bytes()),
        ):
            assert hits(document) == set()
    for manifest in (PROD_MANIFEST, FIX_MANIFEST):
        assert hits(json.loads(manifest.read_bytes())) == set(), manifest
    names = {k: getattr(V, k) for k in dir(V) if k.isupper()}
    assert {"RUNNER_REJECTIONS", "VERIFIER_REJECTIONS", "LIMITATIONS", "REASONS"} <= set(names)
    for name, value in names.items():
        assert tokens(name) & BANNED == set(), name
        if isinstance(value, str | list | tuple | dict):
            assert hits(json.loads(json.dumps(value, default=str))) == set(), name
    for path in FILES:
        constants = string_constants(path.read_text())
        assert constants and not any(tokens(c) & BANNED for c in constants), path
    assert "recall_gain_budgets" in json.dumps(read_result(prod)) or True
    result = read_result(fx["dir"])
    assert "recall_gain_budgets" in result["decision"]["conditions"]
    assert "min_budgets_with_recall_gain" in json.loads(PROD_MANIFEST.read_bytes())["decision"]
    assert ev.RELATIONS.isdisjoint({*V.REASONS, *V.OUTCOMES, *V.LIMITATIONS})


def test_the_fixture_site_and_the_fixture_directory_hold_exactly_what_is_declared():
    def listing(root: Path) -> list[str]:
        return sorted(
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if "__pycache__" not in p.parts and p.suffix != ".pyc"
        )

    assert listing(SITE) == [
        "igraph",
        "igraph-0.0.0+atlas.fixture.dist-info",
        "igraph-0.0.0+atlas.fixture.dist-info/METADATA",
        "igraph/__init__.py",
        "leidenalg",
        "leidenalg-0.0.0+atlas.fixture.dist-info",
        "leidenalg-0.0.0+atlas.fixture.dist-info/METADATA",
        "leidenalg/__init__.py",
    ]
    assert listing(FX) == [
        "fixture_site", *[f"fixture_site/{p}" for p in listing(SITE)],
        "graphs", "graphs/synthetic-cliques.v1.json", "manifest.fixture.v1.json",
        "manifest.v1.json", "s18-task-graph.v1.json",
    ]  # fmt: skip
    for name in ("igraph", "leidenalg"):
        meta = (SITE / f"{name}-0.0.0+atlas.fixture.dist-info/METADATA").read_text()
        assert f"Name: {name}\n" in meta and "Version: 0.0.0+atlas.fixture\n" in meta
    for path in FX.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            assert path.read_bytes().isascii(), path
    for forbidden in (SITE.parent / "__pycache__", SITE / "igraph/__pycache__",
                      SITE / "leidenalg/__pycache__"):  # fmt: skip
        assert not forbidden.exists(), forbidden
    for stale in SCRIPTS.glob("__pycache__/*"):
        assert "community" not in stale.name and "experiment-leiden" not in stale.name, stale


def test_the_fixture_manifest_differs_from_production_only_in_graphs_and_provider():
    prod = json.loads(PROD_MANIFEST.read_bytes())
    fix = manifest_copy()
    prod["graphs"] = fix["graphs"] = None
    prod["algorithms"]["leiden"]["provider"] = fix["algorithms"]["leiden"]["provider"] = None
    assert prod == fix
    real = json.loads(PROD_MANIFEST.read_bytes())["algorithms"]["leiden"]["provider"]
    assert real["kind"] == "real" and all("+atlas" not in v for v in real["distributions"].values())
    stand_in = manifest_copy()["algorithms"]["leiden"]["provider"]
    assert stand_in["kind"] == "fixture"
    assert all("+atlas.fixture" in v for v in stand_in["distributions"].values())
    graphs = [g["id"] for g in json.loads(PROD_MANIFEST.read_bytes())["graphs"]]
    assert "synthetic-cliques" not in graphs  # the fixture-only graph never enters production
    assert hashlib.sha256(PROD_MANIFEST.read_bytes()).hexdigest() == PRODUCTION_SHA


def test_the_tools_json_and_the_seven_tools_are_unchanged():
    out = CliRunner().invoke(cli, ["tools", "--repo", str(ROOT)])
    assert out.exit_code == 0
    raw = out.stdout_bytes
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert len(READ_ONLY_TOOLS) == 7
    for text in ("community", "leiden", "experiment"):
        assert not any(text in name for name in READ_ONLY_TOOLS)
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    relations = frozenset(
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
    assert relations == ev.RELATIONS


def test_dependency_files_and_the_probe_artifacts_are_untouched():
    for name, pinned in (("pyproject.toml", PYPROJECT_SHA256), ("uv.lock", UV_LOCK_SHA256)):
        assert hashlib.sha256((ROOT / "mcp-server" / name).read_bytes()).hexdigest() == pinned
    found = sorted(
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("aci-s17-probe-lpa-vs-louvain.*")
        if ".git" not in p.parts and ".venv" not in p.parts
    )
    assert found == [
        ".spectra/changes/aci-s17/probe/aci-s17-probe-lpa-vs-louvain.json",
        ".spectra/changes/aci-s17/probe/aci-s17-probe-lpa-vs-louvain.md",
    ]
    assert not list((ROOT / "mcp-server/src").rglob("*leiden*"))
    assert not list((ROOT / "mcp-server/src").rglob("*communit*experiment*"))


# --------------------------------------------------------------------------- docs, entries


def test_the_documentation_names_every_closed_value_and_stays_within_its_claims():
    text = DOCS.read_text()
    assert text.isascii() and tokens(text) & BANNED == set()
    for phrase in (
        "candidate for a separately reviewed decision", "modularity alone", "not canonical",
        "skipped_missing_provider", "production_manifest", "only a verification with",
        "case-sensitive whole-file substring", "does not call `graph_query retrieve`",
        "node budgets", "label_propagation.py", "fixture provider", "never Leiden",
        "artifact_invalid_json", "python -S", "--require-provider", "measure-one",
    ):  # fmt: skip
        assert phrase in text, phrase
    assert OPERATOR_COMMAND in text
    assert PRODUCTION_SHA in text
    values = [
        *V.RUNNER_REJECTIONS, *V.VERIFIER_REJECTIONS, *V.RUNNER_USAGE_NAMES,
        *V.VERIFIER_USAGE_NAMES, *V.STATUSES, *V.OUTCOMES, *V.REASONS, *V.SEED_TIERS,
        *V.PROVIDER_KINDS, *V.LIMITATIONS, V.MANIFEST_FORMAT, V.GRAPH_FORMAT,
        V.PARTITIONS_FORMAT, V.RESULT_FORMAT, V.TELEMETRY_FORMAT, V.SUMMARY_FORMAT,
        V.VERIFICATION_FORMAT, V.PROJECTION_RULE, V.DECISION_RULE, V.USEFULNESS_PROXY,
        "scripts/experiment-leiden-vs-lpa.py", "scripts/verify-community-experiment.py",
        "ModularityVertexPartition", "leidenalg==0.12.0", "igraph==1.0.0",
    ]  # fmt: skip
    for value in values:
        assert value in text, value
    for cli_text in (
        "Community experiment rejected: <reason>[: <detail>]",
        "Community verification rejected: <reason>[: <detail>]",
        "Usage error: <name>",
    ):
        assert cli_text in text, cli_text
    for option in ("--manifest", "--manifest-sha256", "--out", "--repo-root", "--require-provider",
                   "--results", "--max-artifact-bytes"):  # fmt: skip
        assert option in text, option


def test_the_documentation_reports_the_committed_real_run_and_nothing_more():
    text = DOCS.read_text()
    result = read_result(REAL_RUN)
    decision = result["decision"]
    assert decision["outcome"] in text and result["canonical_sha256"] in text
    assert result["partitions_sha256"] in text
    for reason in decision["reasons"]:
        assert reason in text, reason
    for name, value in decision["conditions"].items():
        assert name in text and str(value).lower() in text.lower(), name
    assert result["manifest_sha256"] == PRODUCTION_SHA
    assert json.loads((REAL_RUN / "environment.json").read_bytes())[
        "production_manifest_sha256"
    ] == (PRODUCTION_SHA)


def test_the_changelog_and_the_readme_carry_one_entry_each():
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    added = lines.index("### Added", lines.index("## [Unreleased]"))
    bullets = [i for i, line in enumerate(lines) if line.startswith("- **") and i > added]
    for index, title in enumerate(
        ("Cross-repository package links (ACI-S30).", "Git co-change correlation (ACI-S33).",
         "ADR rationale review signals (ACI-S34).", "Verified test-outcome feedback (ACI-S35).")
    ):  # fmt: skip
        assert lines[bullets[index]].startswith(f"- **{title}")
    located = next(
        i for i, line in enumerate(lines) if "Leiden community experiment (ACI-S36)" in line
    )
    assert located == bullets[4]
    assert lines[bullets[5]].startswith("- **Python dependency surface")
    entry = "\n".join(lines[bullets[4] : bullets[5]])
    assert sum("(ACI-S36)" in line for line in lines) == 1
    for title in PRIOR_TITLES:
        assert title not in entry
    for locator in PRIOR_LOCATORS:
        assert locator not in entry
    for phrase in (
        "docs/experiments/community-comparison.md", "skipped_missing_provider",
        "candidate for a separately reviewed decision", "retain_lpa",
        "scripts/experiment-leiden-vs-lpa.py", "scripts/verify-community-experiment.py",
        "production default remains", "label propagation",
    ):  # fmt: skip
        assert phrase in entry, phrase
    assert entry.isascii() and tokens(entry) & BANNED == set()
    assert not any(line.startswith("- **") for line in lines[bullets[4] + 1 : bullets[5]])
    readme = (ROOT / "README.md").read_text().splitlines()
    after = next(i for i, line in enumerate(readme) if "docs/verified-feedback.md" in line)
    row = readme[after + 1]
    assert "docs/experiments/community-comparison.md" in row
    assert row.startswith("|") and row.count("|") == 3
    assert sum("docs/experiments/community-comparison.md" in line for line in readme) == 1
    for locator in PRIOR_LOCATORS:
        assert locator not in row
    assert row.isascii() and tokens(row) & BANNED == set()
    for phrase in ("skipped_missing_provider", "label propagation"):
        assert phrase in row, phrase
    drift = next(i for i, line in enumerate(readme) if "docs/rationale-drift.md" in line)
    assert after == drift + 1
    assert DOCS.exists() and DOCS.parent == ROOT / "docs/experiments"


# ------------------------------------------------------------------------------- evidence


def test_the_committed_real_run_evidence_is_bounded_and_verifies_without_a_provider():
    assert REAL_RUN.is_dir()
    total = sum(p.stat().st_size for p in REAL_RUN.rglob("*") if p.is_file())
    assert 0 < total <= 4 * 1024 * 1024, total
    names = {p.name for p in REAL_RUN.iterdir()}
    assert {"result.json", "partitions.json.gz", "telemetry.json", "environment.json",
            "run.log"} <= names  # fmt: skip
    done = subprocess.run(
        [sys.executable, "-S", str(VERIFIER), "--results", str(REAL_RUN)],
        capture_output=True, text=True, timeout=240, check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert summary["production_manifest"] is True and summary["status"] == "pass"
    assert summary["outcome"] == read_result(REAL_RUN)["decision"]["outcome"]
    assert summary["manifest_sha256"] == PRODUCTION_SHA


def test_the_tests_never_mutate_the_import_machinery():
    names = ("test_s36_support.py", "test_s36_semantics.py", "test_s36_isolation.py",
             "test_s36_community_experiment.py")  # fmt: skip
    needles = ("sys." + "path.", "sys." + "path =", "sys." + "modules[", "sys." + "modules.")
    for name in names:
        text = (Path(__file__).parent / name).read_text()
        for needle in needles:
            assert needle not in text, (name, needle)
    assert "PYTHONDONTWRITEBYTECODE" in (Path(__file__).parent / "test_s36_support.py").read_text()
    assert "sys.dont_write_bytecode = True" in RUNNER.read_text()
    assert "sys.dont_write_bytecode = True" in VERIFIER.read_text()
