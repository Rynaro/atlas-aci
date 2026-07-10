"""Tests for A5 — deterministic sorted-JSONL export + idempotent import
(D6): the portable, committable artifact that lets a `.atlas/graph.<epoch>.db`
travel with the repo without ever shipping a semantic graph/union merge
driver (AC-A5-5, D6-Q2).

Every acceptance criterion below is checked against the export's ACTUAL
bytes/behaviour, not a description of what the exporter is supposed to do —
the same boundary-testing discipline this campaign keeps re-learning: what
input has this never been shown? (records inserted in shuffled order, a
cold-start import into an empty repo, a re-import of the same file, a
deliberately corrupted content_hash.)
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from atlas_aci.codegraph import SCHEMA, SCHEMA_EPOCH, CodeGraph


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _build(tmp_path: Path, name: str, files: dict[str, str]) -> CodeGraph:
    repo = tmp_path / name
    repo.mkdir()
    for rel, content in files.items():
        _write(repo, rel, content)
    graph = CodeGraph(repo=repo)
    graph.build()
    return graph


_FILES = {
    "app/tallier.rb": ("class Tallier\n  def call(ballot)\n    record_vote(ballot)\n  end\nend\n"),
    "app/service.py": (
        "class Service:\n    def run(self):\n        return helper()\n\n\n"
        "def helper():\n    return 1\n"
    ),
    "app/widget.ts": ("class Widget {\n  build() {\n    return new Widget();\n  }\n}\n"),
}


# ---- AC-A5-1: canonical JSONL shape ----


def test_canonical_jsonl_shape(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    stats = graph.export_jsonl(out_path)

    raw = out_path.read_bytes()
    # LF-only: no CR anywhere, and the file ends in exactly one trailing LF.
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")

    lines = text.split("\n")[:-1]  # drop the single trailing empty element
    assert len(lines) == stats["records_written"] + 1  # + header

    for line in lines:
        # No insignificant whitespace: compact separators only.
        assert ": " not in line
        assert ", " not in line
        record = json.loads(line)
        # Sorted keys: re-serializing with sort_keys must reproduce the
        # exact same line — proves the file was already canonical, not
        # merely parseable.
        assert json.dumps(record, sort_keys=True, separators=(",", ":")) == line

    header = json.loads(lines[0])
    assert header["type"] == "header"
    assert header["schema_epoch"] == SCHEMA_EPOCH
    assert isinstance(header["content_hash"], str) and len(header["content_hash"]) == 64

    body_types = {json.loads(line)["type"] for line in lines[1:]}
    assert body_types <= {"edge", "rationale", "symbol"}
    assert "symbol" in body_types
    assert "edge" in body_types


def test_no_float_fields_pass_silently(tmp_path: Path) -> None:
    """AC-A5-1's "fixed float formatting" clause is enforced as a mechanical
    negative-space guarantee: no column in SCHEMA is a float today, and
    `_canonical_json_line` asserts that on every call rather than silently
    emitting a platform-dependent `repr(float)` if one is ever introduced."""
    with pytest.raises(TypeError, match="float"):
        CodeGraph._canonical_json_line({"type": "symbol", "weight": 0.5})


# ---- AC-A5-2: relative path keys re-anchor on load ----


def test_relative_path_keys_reanchor(tmp_path: Path) -> None:
    graph = _build(tmp_path, "source-repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    # Import into a COMPLETELY DIFFERENT repo root -- the artifact must
    # re-anchor purely from its own relative path keys, never an absolute
    # path baked in at export time.
    target_repo = tmp_path / "a-totally-different-checkout-location"
    target_repo.mkdir()
    imported = CodeGraph(repo=target_repo)
    imported.import_jsonl(out_path)

    defs = imported.search_symbol("Tallier")["definitions"]
    assert len(defs) == 1
    assert defs[0]["path"] == "app/tallier.rb"  # still repo-relative, re-anchored on the new root
    assert not defs[0]["path"].startswith("/")
    assert str(tmp_path) not in defs[0]["path"]


# ---- AC-A5-3: byte-identical exports of an identical source tree ----


def test_export_byte_identical_for_identical_input(tmp_path: Path) -> None:
    """Two INDEPENDENT full builds of the identical source tree (not the
    same DB connection re-exported) must produce byte-identical exports --
    proving determinism is a property of the export format + A1/A3's total
    orders, not an accident of reusing one build's row order."""
    graph_a = _build(tmp_path, "repo-a", _FILES)
    graph_b = _build(tmp_path, "repo-b", _FILES)

    out_a = tmp_path / "export-a.jsonl"
    out_b = tmp_path / "export-b.jsonl"
    graph_a.export_jsonl(out_a)
    graph_b.export_jsonl(out_b)

    assert out_a.read_bytes() == out_b.read_bytes()


def test_export_byte_identical_across_two_fresh_indexes_of_the_same_repo(tmp_path: Path) -> None:
    """The same guarantee, phrased the other way: re-indexing the SAME repo
    from scratch into a fresh epoch DB and re-exporting reproduces identical
    bytes -- a full rebuild is not merely idempotent in content, it is
    byte-idempotent in its exported artifact."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, content in _FILES.items():
        _write(repo, rel, content)

    graph = CodeGraph(repo=repo)
    graph.build()
    first = tmp_path / "first.jsonl"
    graph.export_jsonl(first)

    graph.build()  # full rebuild again, same source tree, same epoch DB file
    second = tmp_path / "second.jsonl"
    graph.export_jsonl(second)

    assert first.read_bytes() == second.read_bytes()


# ---- AC-A5-4: idempotent import reproduces a valid current-epoch DB ----


def test_import_roundtrip_idempotent(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    target_repo = tmp_path / "cold-start-repo"
    target_repo.mkdir()
    imported = CodeGraph(repo=target_repo)

    stats_first = imported.import_jsonl(out_path)
    assert imported.epoch_ok() is True
    assert imported.search_symbol("Tallier")["definitions"]
    # `helper()` (app/service.py) resolves to a real edge, unlike
    # `record_vote` (app/tallier.rb), which has no local definition
    # anywhere in `_FILES` and therefore never gets an `edges` row at all
    # (AC-A1-6: zero candidates -> no edge, ever).
    assert imported.callers_of("helper")

    # Re-export the freshly-imported DB and diff against the original --
    # proves the import reconstructs the SAME graph content, not just "a"
    # graph.
    reexported = tmp_path / "reexported.jsonl"
    imported.export_jsonl(reexported)
    assert reexported.read_bytes() == out_path.read_bytes()

    # Import AGAIN (same file) -- must be idempotent: same counts, same
    # resulting DB content, no duplication.
    stats_second = imported.import_jsonl(out_path)
    assert stats_second == stats_first
    reexported_again = tmp_path / "reexported-again.jsonl"
    imported.export_jsonl(reexported_again)
    assert reexported_again.read_bytes() == out_path.read_bytes()


def test_import_rejects_corrupted_content_hash(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    lines = out_path.read_text().splitlines()
    header = json.loads(lines[0])
    header["content_hash"] = "0" * 64
    lines[0] = json.dumps(header, sort_keys=True, separators=(",", ":"))
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text("\n".join(lines) + "\n")

    target_repo = tmp_path / "target"
    target_repo.mkdir()
    imported = CodeGraph(repo=target_repo)
    with pytest.raises(ValueError, match="content_hash mismatch"):
        imported.import_jsonl(tampered)


def test_import_rejects_wrong_schema_epoch(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    lines = out_path.read_text().splitlines()
    header = json.loads(lines[0])
    header["schema_epoch"] = SCHEMA_EPOCH + 1
    # content_hash must still match the (unchanged) body for this to
    # isolate the epoch check specifically, not double up with the
    # content-hash check above.
    lines[0] = json.dumps(header, sort_keys=True, separators=(",", ":"))
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text("\n".join(lines) + "\n")

    target_repo = tmp_path / "target"
    target_repo.mkdir()
    imported = CodeGraph(repo=target_repo)
    with pytest.raises(ValueError, match="schema_epoch"):
        imported.import_jsonl(tampered)


# ---- AC-A5-5: no graph/union merge driver ships ----


def test_no_merge_driver_shipped() -> None:
    """No `nx.compose`-style graph/union merge, and nothing REGISTERS a
    git merge driver (an actual `subprocess`/`os.system` invocation, or a
    shipped `.gitattributes` `merge=` directive) anywhere in this project. A
    trivial regenerate-on-conflict driver would be PERMITTED but is not
    shipped -- the documented workflow (see `import_jsonl`'s own docstring)
    is to discard the conflicted export and regenerate from source.

    Checks actual invocation forms, not the bare substring "merge" or
    "nx.compose" -- both appear legitimately in this project's own
    docstrings, explaining what is deliberately NOT shipped.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    assert not list(repo_root.glob(".gitattributes")), (
        "a .gitattributes file appeared -- check it for a merge= driver directive"
    )

    src_root = Path(__file__).resolve().parent.parent / "src"
    for path in src_root.rglob("*.py"):
        text = path.read_text()
        assert "nx.compose(" not in text, f"{path}: graph/union merge code found"
        driver_pattern = r"(subprocess|os\.system)[^\n]*merge\.[\w<>-]+\.driver"
        driver_registration = re.search(driver_pattern, text)
        assert driver_registration is None, (
            f"{path}: appears to invoke `git config merge.<x>.driver` registration"
        )

    import_doc = CodeGraph.import_jsonl.__doc__ or ""
    export_doc = CodeGraph.export_jsonl.__doc__ or ""
    assert "regenerate" in (import_doc + export_doc).lower()


# ---- AC-A5-6: header record carries schema-epoch + content hash ----


def test_header_record_epoch_and_content_hash(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    text = out_path.read_text()
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    header = json.loads(lines[0])
    assert header["schema_epoch"] == SCHEMA_EPOCH

    body_text = "".join(line + "\n" for line in lines[1:])
    assert header["content_hash"] == hashlib.sha256(body_text.encode("utf-8")).hexdigest()


# ---- AC-A5-7: record-level canonical order, independent of rowid ----


def test_record_level_canonical_order_independent_of_rowid(tmp_path: Path) -> None:
    """Builds a raw DB by hand -- bypassing `_run_build`/`_resolve_edges`
    entirely -- and inserts symbols/edges/rationale rows in a DELIBERATELY
    SHUFFLED order (not the order the exporter's canonical ORDER BY would
    produce). The export must still come out in the fixed canonical order:
    the exporter's SQL never references `id`/rowid, so insertion order
    cannot leak through."""
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".atlas" / f"graph.{SCHEMA_EPOCH}.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Insert three symbols in reverse-of-canonical order.
    for name, kind, path, line_start, line_end in [
        ("Zeta", "class", "z.rb", 10, 20),
        ("Mu", "class", "m.rb", 5, 15),
        ("Alpha", "class", "a.rb", 1, 9),
    ]:
        conn.execute(
            "INSERT INTO symbols(name, kind, path, line_start, line_end, lang) "
            "VALUES (?, ?, ?, ?, ?, 'ruby')",
            (name, kind, path, line_start, line_end),
        )

    # Insert two edges in reverse-of-canonical order.
    for source_path, source_line, callee_name in [("z.rb", 12, "helper"), ("a.rb", 3, "helper")]:
        conn.execute(
            "INSERT INTO edges(relation, source_path, source_line, source_col, source_name, "
            "source_kind, callee_name, confidence, target_path, target_line, target_name, "
            "candidates, lang) VALUES ('call', ?, ?, 0, NULL, NULL, ?, 'INFERRED', 'h.rb', 1, "
            "'helper', NULL, 'ruby')",
            (source_path, source_line, callee_name),
        )

    conn.execute("INSERT INTO manifest(key, value) VALUES ('epoch', ?)", (str(SCHEMA_EPOCH),))
    conn.commit()
    conn.close()

    graph = CodeGraph(repo=repo)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    lines = out_path.read_text().splitlines()
    body = [json.loads(line) for line in lines[1:]]
    symbol_paths = [r["path"] for r in body if r["type"] == "symbol"]
    edge_paths = [r["source_path"] for r in body if r["type"] == "edge"]

    # Canonical order is path-ascending, independent of insertion order.
    assert symbol_paths == sorted(symbol_paths) == ["a.rb", "m.rb", "z.rb"]
    assert edge_paths == sorted(edge_paths) == ["a.rb", "z.rb"]

    # Type grouping is fixed: every "edge" record precedes every "symbol"
    # record, matching EXPORT_RECORD_TYPES's declared order.
    types_seen = [r["type"] for r in body]
    assert types_seen.index("edge") < types_seen.index("symbol")


# ---- Export size on this repo (informational, feeds AC-REL-2's pattern) ----


def test_export_size_recorded_for_this_repo(tmp_path: Path) -> None:
    """Not itself an acceptance criterion (AC-REL-2 is scoped to the larger
    pinned Spree reference repo, measured once at release-prep) -- this
    pins a sane, well-below-ceiling size on THIS project's own mcp-server/
    tree as a same-machine sanity check that the format doesn't balloon.
    Copies `src/` into a throwaway tmp dir first -- `CodeGraph` always
    writes its own `.atlas/` under `self.repo`, and this must never touch
    the real working tree."""
    import shutil

    real_src = Path(__file__).resolve().parent.parent / "src"
    repo = tmp_path / "self-repo"
    shutil.copytree(real_src, repo)

    graph = CodeGraph(repo=repo)
    graph.build()
    out_path = tmp_path / "self-export.jsonl"
    stats = graph.export_jsonl(out_path)
    assert stats["bytes_written"] < 10 * 1024 * 1024  # sane ceiling for this project's own size
    assert stats["bytes_written"] == out_path.stat().st_size
