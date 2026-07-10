"""Thesis-protecting negatives (Track NEG).

These mirror the same checks `.github/workflows/harden-gate.yml` runs, so a
local `pytest` run catches a regression before it ever reaches CI. Kept
deliberately dumb (plain grep-equivalents) — the whole point of a NEG
criterion is that it needs no judgement call.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
UV_LOCK = Path(__file__).resolve().parent.parent / "uv.lock"


def _all_py_source() -> str:
    return "\n".join(p.read_text() for p in SRC_ROOT.rglob("*.py"))


# ---- AC-NEG-1 ----


def test_no_llm_client_importable_from_server_package() -> None:
    source = _all_py_source()
    assert not re.search(r"\bimport anthropic\b|\bfrom anthropic\b", source)
    assert not re.search(r"\bimport openai\b|\bfrom openai\b", source)
    assert not re.search(r"\bimport boto3\b|\bfrom boto3\b", source)


# ---- AC-NEG-2 ----


def test_networkx_absent_from_dependency_tree() -> None:
    """Absolute and unconditional (DIR-1): a below-threshold D3 probe cuts
    A3 to v2.1, it never adds networkx."""
    assert "networkx" not in PYPROJECT.read_text().lower()
    assert "networkx" not in UV_LOCK.read_text().lower()


# ---- AC-NEG-4 ----


def test_no_alter_table_migration_ladder() -> None:
    """D1: the DB is pure derived data; a schema change bumps the epoch,
    never an in-place ALTER."""
    source = _all_py_source()
    assert "alter table" not in source.lower()


# ---- AC-NEG-5 ----


def test_no_new_runtime_dependency(tmp_path: Path) -> None:
    """The [dev]/[vec]/[ruby] extras aren't the core dependency set; this
    pins the core list itself so a future PR can't quietly grow it."""
    text = PYPROJECT.read_text()
    match = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match, "pyproject.toml must declare a top-level dependencies list"
    core_deps = {
        line.split("#", 1)[0].strip().strip('",').split(">=")[0].split("<")[0].strip('"')
        for line in match.group(1).splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    core_deps = {d for d in core_deps if d}
    expected = {"mcp", "tree-sitter", "tree-sitter-language-pack", "click", "pydantic", "structlog"}
    assert core_deps == expected, (
        f"core [project.dependencies] changed: {core_deps} != baseline {expected} "
        f"(v0.4.0 core set) — v2.0.0 P0 must add no new runtime dependency"
    )
