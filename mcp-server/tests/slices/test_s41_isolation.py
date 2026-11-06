"""ACI-S41 isolation: the fingerprint tripwire, frozen modules, imports, the lazy import,
parameters, vocabularies and the documents."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import test_s39_isolation as iso39
import test_s40_injection as inj40
import test_s40_isolation as iso40
import test_s41_support as s
from test_s41_support import lock_canary  # noqa: F401  (session canary, autouse)

ROOT, SRC = s.ROOT, s.SRC
NEW = (
    "storage/generations.py",
    "storage/leases.py",
    "storage/retention.py",
    "indexing/jobs.py",
    "indexing/job_runner.py",
)
PRODUCER_MODULES = (
    "storage/evidence.py",
    "providers/python_imports.py",
    "indexing/extraction_cache.py",
    "resolvers/dependencies.py",
    "extractors/rust.py",
    "storage/snapshots.py",
    "indexing/retrieval.py",
    "extractors/tsx.py",
)
# sha256 at the base commit (fa16b1c) of the producer-fingerprint inputs and the package inits.
BASE_SHAS = {
    "storage/evidence.py": "88eec05ea6d0f741f3398213fccca893edfff82e7de5a7a91c8ed3b0891a5a97",
    "storage/snapshots.py": "6e800008cbbbda1b345f199aecbda193eb27c68b15aeb1ff941b5a840ab5be82",
    "indexing/extraction_cache.py": "6849d31d6b5b78a8a72a33d4ae0eeb8f5ae3c0d5b2c0da6bbc608d8ef70e21e5",  # noqa: E501
    "indexing/retrieval.py": "d53c185c213e5ed35883652f54b7568e53f95dbed995833eea128c029c4d2ece",
    "storage/__init__.py": "a5c86b099d459153c262be671561811557ad2f764d24f4ba7c56e243d50bf8b6",
    "indexing/__init__.py": "e4b9d192790309018248199725d46db643744ee7be19e51355cab5f4cf93db1f",
    "codegraph.py": "5dbc8433d8a9bb3db80ebbe2d04d1bcfc3f6aa252ea9166e3c94e277e9527026",
    "providers/python_imports.py": "de00688a2826aad7665adfb0c5ca7f6d044250087d645c069cda9452d4938014",  # noqa: E501
    "resolvers/dependencies.py": "e3bd74233b6bac6ddd808f5eb6f473c4211519afa007255ace79b30901e70ccd",
    "extractors/rust.py": "28531780080bfc82e09d328262705a50cbeab05a14e090fe3fe56c5c5406262a",
    "extractors/tsx.py": "cfbdf5d5390bd36816ecaec968f9b5e364d28eab40ac31a5da457fa09ffc81c8",
}
BANNED = iso39.BANNED
DOC = ROOT / "docs" / "operations" / "index-jobs-and-retention.md"
ALLOWED_THIRD_PARTY = {"structlog"}


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


# ------------------------------------------------------------------------ fingerprint tripwire
def test_the_producer_fingerprint_names_exactly_the_eight_modules_and_none_of_the_new_ones(
    tmp_path: Path,
) -> None:
    from atlas_aci.codegraph import CodeGraph

    graph = CodeGraph(tmp_path, read_only=True)
    inputs = graph._producer_inputs()
    assert tuple(inputs["modules"]) == PRODUCER_MODULES
    assert not set(inputs["modules"]) & set(NEW)
    for name in NEW:
        assert (SRC / name).is_file()


def test_every_producer_input_and_frozen_file_is_byte_identical_to_the_base() -> None:
    assert len(BASE_SHAS) == 11 and set(PRODUCER_MODULES) <= set(BASE_SHAS)
    for name, expected in BASE_SHAS.items():
        assert hashlib.sha256((SRC / name).read_bytes()).hexdigest() == expected, name
    for name, expected in iso40.FROZEN.items():
        assert hashlib.sha256((SRC / name).read_bytes()).hexdigest() == expected, name
    for path, expected in (("pyproject.toml", iso39.PYPROJECT_SHA), ("uv.lock", iso39.UV_LOCK_SHA)):
        assert hashlib.sha256((ROOT / "mcp-server" / path).read_bytes()).hexdigest() == expected


PERTURB = """
import sys
from pathlib import Path
from atlas_aci.codegraph import CodeGraph
repo = Path(sys.argv[1]); repo.mkdir(exist_ok=True)
for i in range(3):
    (repo / f"m{i}.py").write_text(f"def f{i}():\\n    return {i}\\n")
graph = CodeGraph(repo, langs=["python"], graph_root=Path(sys.argv[2]))
stats = graph.build()
with graph.reader_lease() as reader:
    print(reader._lease_generation_id, stats["snapshot_id"])
"""


def scratch_ids(tmp_path: Path, name: str, perturb: bool) -> str:
    source = tmp_path / f"src-{name}"
    shutil.copytree(
        SRC.parent, source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), symlinks=True
    )
    if perturb:  # one byte of a hashed module
        with (source / "atlas_aci" / "storage" / "evidence.py").open("a") as stream:
            stream.write("\n# perturbed\n")
    repo = tmp_path / "repo"  # the same repository path for every run: ids are content addressed
    done = s.python_c(
        PERTURB, str(repo), str(tmp_path / f"g-{name}"), env={"PYTHONPATH": str(source)}
    )
    assert done.returncode == 0, done.stderr[-500:]
    return done.stdout.strip().splitlines()[-1]


def test_the_fingerprint_positive_control_a_hashed_byte_changes_the_ids(tmp_path: Path) -> None:
    """Condition 14 control: ids move when a hashed module changes, and only then."""
    base = scratch_ids(tmp_path, "base", perturb=False)
    same = scratch_ids(tmp_path, "same", perturb=False)
    moved = scratch_ids(tmp_path, "moved", perturb=True)
    assert base == same and base != moved
    assert len(base.split()) == 2


FRESH = """
import hashlib, json, sys
import atlas_aci.storage.generations, atlas_aci.storage.leases, atlas_aci.storage.retention
import atlas_aci.indexing.jobs, atlas_aci.indexing.job_runner
from pathlib import Path
from atlas_aci.config import Config
from atlas_aci.server import build_tool_manifest
from atlas_aci.protocol import graph, results
from atlas_aci.query import admission, impact_admission, paging
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.storage import evidence as ev
root = Path(sys.argv[1])
manifest = build_tool_manifest(Config(repo=root, memex_root=root / "memex"))
raw = (json.dumps(manifest, indent=2) + "\\n").encode()
canon = lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
print(json.dumps({
    "tools_sha": hashlib.sha256(raw).hexdigest(), "tools_bytes": len(raw),
    "budgets": [admission.MINIMUM_BUDGET, paging.COLLECTION_MINIMUM_BUDGET,
                results.FIXED_ERROR_ENVELOPE_CAP, impact_admission.IMPACT_MINIMUM_BUDGET],
    "tools": len(READ_ONLY_TOOLS), "relations": sorted(ev.RELATIONS),
    "graph_output": hashlib.sha256(canon(graph.GRAPH_OUTPUT_SCHEMA).encode()).hexdigest(),
}))
"""


def test_the_tools_json_and_budgets_in_a_fresh_interpreter_importing_the_new_modules(
    tmp_path: Path,
) -> None:
    done = s.python_c(FRESH, str(tmp_path))
    assert done.returncode == 0, done.stderr[-600:]
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["tools_sha"] == iso39.TOOLS_SHA and seen["tools_bytes"] == iso39.TOOLS_BYTES
    assert seen["budgets"] == [3436, 3448, 797, 7730] and seen["tools"] == 7
    assert seen["relations"] == [
        "call",
        "construct",
        "extend",
        "import",
        "include",
        "prepend",
        "rationale_for",
        "superclass",
    ]
    assert seen["graph_output"] == iso39.GRAPH_OUTPUT_SHA


# ---------------------------------------------------------------------------------- layout
def test_plain_index_creates_only_the_base_layout(tmp_path: Path) -> None:
    ns = s.Ns(tmp_path)
    s.write_tree(ns.repo, 1)
    done = s.cli(
        ["index", "--repo", str(ns.repo), "--graph-root", str(ns.graphs), "--langs", "python"]
    )
    assert done.returncode == 0, done.stderr[-300:]
    assert {p.name for p in ns.atlas.iterdir()} == {
        "graph.9.db",
        "source-snapshots",
        "extraction-cache",
        ".index.lock",
    }


# ------------------------------------------------------------------------- lazy import and cli
def test_the_main_module_does_not_load_the_s41_modules() -> None:
    code = (
        "import sys\nimport atlas_aci.__main__\n"
        "names = ['atlas_aci.storage.generations','atlas_aci.storage.leases',"
        "'atlas_aci.storage.retention','atlas_aci.indexing.jobs','atlas_aci.indexing.job_runner']\n"
        "print([n for n in names if n in sys.modules])\n"
    )
    done = s.python_c(code)
    assert done.returncode == 0, done.stderr[-300:]
    assert done.stdout.strip() == "[]"


def test_the_jobs_group_parameters_and_the_untouched_commands() -> None:
    from atlas_aci.__main__ import cli

    jobs = cli.commands["jobs"]
    assert set(jobs.commands) == {"submit", "resume", "cancel", "status", "gc", "acquire"}
    common = {"repo", "graph_root"}
    expected = {
        "submit": common | {"langs", "frameworks", "rails_table_map", "now"},
        "resume": common | {"job", "now"},
        "cancel": common | {"job"},
        "status": common,
        "gc": common | {"now", "wait_seconds"},
        "acquire": common | {"generation", "hold"},
    }
    for name, params in expected.items():
        assert {p.name for p in jobs.commands[name].params} == params, name
    assert {p.name for p in cli.commands["serve-shared"].params} == {"shared_config"}
    assert {p.name for p in cli.commands["serve"].params} == (
        iso39.BASE_SERVE_PARAMS | iso39.NEW_SERVE_PARAMS
    )
    assert {p.name for p in cli.commands["index"].params} == {
        "repo",
        "langs",
        "since",
        "frameworks",
        "graph_root",
        "rails_table_map",
    }
    text = (SRC / "__main__.py").read_text()
    assert text.count('@cli.group("jobs")') == 1


def test_the_usage_errors_exit_2_with_empty_stdout_and_create_nothing(tmp_path: Path) -> None:
    ns = s.Ns(tmp_path)
    s.write_tree(ns.repo, 1)
    base = ["--repo", str(ns.repo), "--graph-root", str(ns.graphs)]
    cases = {
        "U1": ["jobs", "submit"],  # a missing option
        "U1b": ["jobs", "gc", *base, "--now", "-1"],
        "U2": ["jobs", "cancel", *base, "--job", "ABC"],
        "U2b": ["jobs", "acquire", *base, "--generation", "xyz"],
        "U3": ["jobs", "submit", *base, "--frameworks", "nope"],
        "U4": [
            "jobs",
            "submit",
            *base,
            "--frameworks",
            "rails_models",
            "--langs",
            "ruby",
            "--rails-table-map",
            str(tmp_path / "missing.json"),
        ],
    }
    wanted = {
        "U2": "--job: must be a 64-character lowercase hex job id",
        "U2b": "--generation: must be a 64-character lowercase hex generation id",
        "U3": "Unknown framework adapter",
        "U4": "Cannot read the Rails table map",
    }
    for name, args in cases.items():
        done = s.cli(args)
        assert done.returncode == 2 and done.stdout == "", (name, done.stderr[-200:])
        assert wanted.get(name, "") in done.stderr, name
    assert not ns.graphs.exists()


def test_every_spawn_site_is_still_one_of_the_audited_four() -> None:
    sites = inj40.spawn_sites()
    assert set(sites) == set(inj40.AUDITED), sites
    for name in NEW:
        assert name not in sites


# -------------------------------------------------------------------------- imports and AST
def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("name", NEW)
def test_the_new_modules_import_only_the_standard_library_and_atlas(name: str) -> None:
    allowed = set(sys.stdlib_module_names) | {"atlas_aci"} | ALLOWED_THIRD_PARTY
    assert imports_of(SRC / name) <= allowed, imports_of(SRC / name) - allowed
    assert "subprocess" not in imports_of(SRC / name)
    tree = ast.parse((SRC / name).read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"print", "eval", "exec", "compile", "__import__"}, name
    text = (SRC / name).read_text()
    assert "alter table" not in text.lower() and "nx.compose(" not in text
    assert "os.system" not in text and "os.popen" not in text and "Popen" not in text
    banned = words(text) & BANNED
    assert not banned, banned


# ------------------------------------------------------------------------------ vocabularies
def test_the_vocabulary_tuples_are_closed_and_unique() -> None:
    from atlas_aci.indexing import job_runner, jobs
    from atlas_aci.protocol import resources
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import leases, retention

    tuples = {
        "STORE_REASONS": (gen.STORE_REASONS, 7),
        "POLICY_REASONS": (gen.POLICY_REASONS, 8),
        "CAPTURE_SEAMS": (gen.CAPTURE_SEAMS, 2),
        "LEASE_SEAMS": (leases.LEASE_SEAMS, 2),
        "GC_SEAMS": (retention.GC_SEAMS, 8),
        "RETAIN_REASONS": (retention.RETAIN_REASONS, 4),
        "RETIRE_REASONS": (retention.RETIRE_REASONS, 4),
        "JOB_STATES": (jobs.JOB_STATES, 8),
        "CANCEL": (jobs.CANCEL, 4),
        "CANCEL_OUTCOMES": (jobs.CANCEL_OUTCOMES, 4),
        "FAILURE_REASONS": (jobs.FAILURE_REASONS, 4),
        "DECLINE_REASONS": (jobs.DECLINE_REASONS, 5),
        "JOB_ERRORS": (jobs.JOB_ERRORS, 11),
        "JOB_SEAMS": (job_runner.JOB_SEAMS, 12),
        "FAULT_ACTIONS": (job_runner.FAULT_ACTIONS, 3),
    }
    for name, (values, size) in tuples.items():
        assert len(values) == len(set(values)) == size, name
        assert all(isinstance(v, str) and v for v in values), name
    assert set(job_runner.JOB_SEAMS) | set(gen.CAPTURE_SEAMS) == set(
        job_runner.JOB_SEAMS + gen.CAPTURE_SEAMS
    )
    assert not set(job_runner.JOB_SEAMS) & set(retention.GC_SEAMS)
    assert resources.error_data("EXPIRED_REFERENCE").code == -32002  # the closed set is unedited


# --------------------------------------------------------------------------------- documents
def test_the_document_is_ascii_names_everything_and_never_overclaims() -> None:
    from atlas_aci.indexing import job_runner, jobs
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import leases, retention

    text = DOC.read_text()
    assert text.isascii() and not words(text) & BANNED
    formats = {
        gen.FORMAT_META,
        gen.FORMAT_POLICY,
        jobs.FORMAT_JOB,
        jobs.FORMAT_IDENTITY,
        jobs.FORMAT_CHECKPOINT,
        jobs.FORMAT_RECEIPT,
        jobs.FORMAT_RESULT,
        jobs.FORMAT_STATUS,
        jobs.FORMAT_ERROR,
        retention.FORMAT_RECEIPT,
        "atlas-aci-job-cancel.v1",
        "atlas-aci-lease.v1",
    }
    vocab = [
        *gen.POLICY_REASONS,
        *jobs.JOB_STATES,
        *jobs.CANCEL,
        *jobs.CANCEL_OUTCOMES,
        *jobs.FAILURE_REASONS,
        *jobs.DECLINE_REASONS,
        *jobs.JOB_ERRORS,
        *retention.RETAIN_REASONS,
        *retention.RETIRE_REASONS,
        *job_runner.JOB_SEAMS,
        *gen.CAPTURE_SEAMS,
        *leases.LEASE_SEAMS,
        *retention.GC_SEAMS,
        *job_runner.FAULT_ACTIONS,
        "EXPIRED_REFERENCE",
        "INVALID_REFERENCE",
        "SNAPSHOT_UNAVAILABLE",
        "INDEX_UNAVAILABLE",
        "retry_current",
        "capture_conflict",
        "ATLAS_ACI_JOB_FAULTS",
    ]
    missing = [t for t in (*formats, *vocab) if t not in text]
    assert not missing, missing
    for token in ("| 0 |", "| 1 |", "| 2 |", "| 3 |", "| 4 |", "| 75 |"):
        assert token in text, token
    for phrase in (
        "reads only the current",  # the server never serves retained generations
        "never touches the live store",
        "hits both names",  # bit-rot on a shared inode
        "returns\n  `WRITER_BUSY` during builds",
        "does not imply that the live",  # reuse exit 0
        "reads every source file once",
        "NFS",
        "Docker Desktop",
        "same filesystem",
        "delete `generations/`",
    ):
        assert phrase.lower().replace("\n", " ").replace("  ", " ") in text.lower().replace(
            "\n", " "
        ).replace("  ", " "), phrase
    assert "serves retained generations" not in text.lower().replace("not serve", "")
    assert "follow-up" in text


def test_the_documents_and_tests_are_the_only_additions() -> None:
    names = sorted(p.name for p in (s.TESTS / "slices").glob("test_s41_*.py"))
    assert names == [
        "test_s41_contract.py",
        "test_s41_gc.py",
        "test_s41_isolation.py",
        "test_s41_jobs.py",
        "test_s41_leases.py",
        "test_s41_support.py",
    ]
    assert {p.name for p in (ROOT / "docs" / "operations").glob("*.md")} == {
        "shared-http-deployment.md",
        "index-jobs-and-retention.md",
    }


def test_the_changelog_bullet_sits_directly_before_the_s40_bullet() -> None:
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines()
    unreleased = lines.index("## [Unreleased]")
    sections = [i for i, line in enumerate(lines) if line.startswith("### ") and i > unreleased]
    first, added, changed = sections[0], sections[1], sections[2]
    assert lines[first] == "### Security" and lines[added] == "### Added"
    bullets = [i for i in range(added, changed) if lines[i].startswith("- **")]
    assert lines[bullets[-1]].startswith("- **Shared multi-principal HTTP mode (ACI-S40).**")
    assert lines[bullets[-2]].startswith(
        "- **Index jobs, retained generations and lease-aware garbage collection (ACI-S41).**"
    )
    body = "\n".join(lines[bullets[-2] : bullets[-1]])
    assert not any(line.startswith("- ") for line in lines[bullets[-2] + 1 : bullets[-1]])
    assert body.isascii() and not words(body) & BANNED
    for needle in ("atlas-aci jobs", "docs/operations/index-jobs-and-retention.md"):
        assert needle in body
    assert sum("(ACI-S41)" in line for line in lines) == 1
    assert "(ACI-S40)" not in body
    security = [line for line in lines[first:added] if line.startswith("- **")]
    assert len(security) == 2  # the Security section is unchanged


def test_the_readme_row_follows_the_s40_row() -> None:
    readme = (ROOT / "README.md").read_text().splitlines()
    index = next(
        i
        for i, line in enumerate(readme)
        if line.startswith("| [`docs/operations/shared-http-deployment")
    )
    row = readme[index + 1]
    assert row.startswith(
        "| [`docs/operations/index-jobs-and-retention.md`]"
        "(docs/operations/index-jobs-and-retention.md) |"
    )
    assert row.count("|") == 3 and row.isascii() and not words(row) & BANNED
    assert "shared-http-deployment.md" not in row
    assert sum("docs/operations/index-jobs-and-retention.md" in line for line in readme) == 1


def test_the_s40_pin_amendment_is_exactly_the_documented_set() -> None:
    text = (s.TESTS / "slices" / "test_s40_isolation.py").read_text()
    assert '"shared-http-deployment.md",\n        "index-jobs-and-retention.md",' in text


def test_no_receipt_or_document_claims_the_server_serves_retained_generations() -> None:
    for path in (DOC, SRC / "storage" / "leases.py", SRC / "storage" / "retention.py"):
        text = path.read_text().lower()
        assert "server serves retained" not in text and "serve retained generations" not in text
