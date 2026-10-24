"""Real package parser controls for the bounded relative import producer."""

from pathlib import Path

import pytest

from atlas_aci.codegraph import CodeGraph


@pytest.mark.parametrize(
    "body,marker,collision,reason",
    [
        ("from .b import B as C\n C()", True, False, "unknown_import_alias"),
        ("B()\n from .b import B", True, False, "relative_import_before_binding"),
        ("from .missing import B\n B()", True, False, "relative_import_missing_module"),
        ("from .b import B\n B()", False, False, "relative_import_no_package"),
        ("from .b import B\n B()", True, True, "relative_import_ambiguous_module"),
        ("from external import B\n B()", True, False, "unknown_import_alias"),
        ("from .b import *\n B()", True, False, "unsupported_scope"),
        ("from .b import B\n B = 1\n B()", True, False, "unknown_import_alias"),
    ],
)
def test_import_rejections(tmp_path: Path, body: str, marker: bool, collision: bool, reason: str):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    if marker:
        (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/b.py").write_text("def B():\n pass\n")
    (repo / "pkg/a.py").write_text("def A():\n " + body + "\n")
    if collision:
        (repo / "pkg/b").mkdir()
        (repo / "pkg/b/__init__.py").write_text("def B():\n pass\n")
    graph = CodeGraph(repo)
    graph.build()
    records = graph.evidence_records()
    calls = [r for r in records if r["type"] == "assertion" and r["relation"] == "call"]
    assert len(calls) == 1 and calls[0]["target_entity_id"] is None
    assert calls[0]["unresolved_reason"] == reason
    assert any(r["type"] == "assertion" and r["relation"] == "import" for r in records)
    graph.close()


@pytest.mark.parametrize(
    "target,conditional_import",
    [
        ("def B():\n pass\nB = 0\n", False),
        ("def B():\n pass\ndel B\n", False),
        ("def B():\n pass\n", True),
        ("if False:\n def B():\n  pass\n", False),
    ],
)
def test_import_cannot_prove_blocked_or_conditional_target(
    tmp_path: Path, target: str, conditional_import: bool
):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/b.py").write_text(target)
    (repo / "pkg/a.py").write_text(
        "def A():\n if False:\n  from .b import B\n B()\n"
        if conditional_import
        else "def A():\n from .b import B\n B()\n"
    )
    graph = CodeGraph(repo)
    graph.build()
    assert not graph.query("callers_of:B")["edges"]
    assert graph.query("callers_of:B")["unresolved_refs"] == 1
    records = graph.evidence_records()
    assert all(
        r["target_entity_id"] is None and r["unresolved_reason"]
        for r in records
        if r["type"] == "assertion"
    )
    graph.close()
