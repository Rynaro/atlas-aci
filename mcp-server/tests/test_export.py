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
from pathlib import Path

import pytest

from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _forge_export(real_export_path: Path, forged_path: Path, mutate_body) -> None:
    """Mutate actual legacy-shaped projection views, then rehash current IDs.

    Every caller must change a real field. Current projection IDs/header/order
    stay valid so a path/shape guard, rather than an unrelated checksum, rejects.
    The old epoch6 reader remains covered by its original body shape.
    """
    from atlas_aci.storage import evidence

    lines = real_export_path.read_text().splitlines()
    header = json.loads(lines[0])
    body = [json.loads(line) for line in lines[1:]]
    before = json.dumps(body, sort_keys=True)
    if header.get("format") == evidence.FORMAT:
        views = []
        pairs = []
        names = {"symbols": "symbol", "edges": "edge", "rationale": "rationale"}
        for record in body:
            if record["type"] == "projection" and record["table"] in names:
                view = {"type": names[record["table"]], **record["row"]}
                if view["type"] == "edge" and view["candidates"] is not None:
                    view["candidates"] = json.loads(view["candidates"])
                views.append(view)
                pairs.append((record, view))
        views_before = [evidence.canonical(view) for view in views]
        mutate_body(views)
        mutation_count = sum(
            old != evidence.canonical(view) for old, view in zip(views_before, views, strict=True)
        )
        assert mutation_count > 0, "forgery did not mutate a real projected field"
        for record, view in pairs:
            row = {key: value for key, value in view.items() if key != "type"}
            if record["table"] == "edges" and row["candidates"] is not None:
                row["candidates"] = evidence.canonical(row["candidates"])
            record["row"] = row
            record["id"] = evidence.identifier(record)
        body.sort(key=lambda record: (record["type"], record["id"]))
    else:
        mutate_body(body)
    assert json.dumps(body, sort_keys=True) != before, "forgery did not mutate a real record"
    body_text = "".join(evidence.canonical(record) + "\n" for record in body)
    header["content_hash"] = hashlib.sha256(body_text.encode()).hexdigest()
    header["record_count"] = len(body)
    forged_path.write_text(evidence.canonical(header) + "\n" + body_text)


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
        # Binding JSON is opaque string data; whitespace inside that string
        # is significant, while outer JSON separators remain canonical.
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
    assert header["format"] == "atlas-evidence.v1"
    assert body_types == {"entity", "occurrence", "producer", "evidence", "assertion", "projection"}


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


def test_import_rejects_truncated_header_with_a_clean_diagnostic(tmp_path: Path) -> None:
    """A crash mid-write (a partial `scp`/`git checkout`) can truncate the
    export file's FIRST line -- the header -- before `json.loads` ever
    sees a complete object. This must raise the same kind of diagnosable
    `ValueError` every other corruption path raises, never a raw
    `json.JSONDecodeError` traceback with no "regenerate from source"
    guidance attached (a real gap this test caught and
    `import_jsonl` was fixed to close)."""
    graph = _build(tmp_path, "repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    raw = out_path.read_bytes()
    lines = raw.split(b"\n")
    truncated_header = lines[0][:-5]  # cut mid-JSON-object, before the closing brace
    tampered = tmp_path / "truncated-header.jsonl"
    tampered.write_bytes(truncated_header + b"\n" + b"\n".join(lines[1:]))

    target_repo = tmp_path / "target"
    target_repo.mkdir()
    imported = CodeGraph(repo=target_repo)
    with pytest.raises(ValueError, match="header record is not valid JSON"):
        imported.import_jsonl(tampered)


def test_import_rejects_truncated_body_line_with_a_clean_diagnostic(tmp_path: Path) -> None:
    """The body-line counterpart: a body record cut mid-JSON must also
    raise a diagnosable `ValueError`, not a raw traceback -- even though
    the content_hash check above already catches this in the overwhelming
    common case (any body tampering changes the recomputed hash), this
    isolates the second guard specifically by re-signing the header's
    content_hash to match the ALREADY-truncated body, so the hash check
    passes and the json.loads guard is what actually fires."""
    graph = _build(tmp_path, "repo", _FILES)
    out_path = tmp_path / "export.jsonl"
    graph.export_jsonl(out_path)

    lines = out_path.read_text().splitlines()
    body_lines = lines[1:]
    body_lines[-1] = body_lines[-1][:-5]  # truncate the last body record mid-JSON
    body_text = "".join(line + "\n" for line in body_lines)
    new_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

    header = json.loads(lines[0])
    header["content_hash"] = new_hash
    new_lines = [json.dumps(header, sort_keys=True, separators=(",", ":")), *body_lines]
    tampered = tmp_path / "truncated-body.jsonl"
    tampered.write_text("\n".join(new_lines) + "\n")

    target_repo = tmp_path / "target"
    target_repo.mkdir()
    imported = CodeGraph(repo=target_repo)
    with pytest.raises(ValueError, match="body record is not valid JSON"):
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
    """Reorder real parser projections and canonical storage, preserving links."""
    files = {
        "z.rb": "class Zeta\n def call\n  helper()\n end\n def helper\n end\nend\n",
        "m.rb": "class Mu\nend\n",
        "a.rb": (
            "class Alpha\n def call\n  # NOTE: actual rationale\n"
            "  helper()\n end\n def helper\n end\nend\n"
        ),
    }
    graph = _build(tmp_path, "repo", files)
    first = tmp_path / "first.jsonl"
    graph.export_jsonl(first)
    before_query = graph.query("callers_of:helper")
    before_entities = graph.evidence_records()
    from atlas_aci.storage import evidence

    connection = graph.db
    for table in (*evidence.COLUMNS, *evidence.TABLES.values()):
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        # Canonical string IDs are semantic; integer projection IDs are not.
        selected = [name for name in columns if name != "id" or table in evidence.TABLES.values()]
        rows = list(
            connection.execute(
                "SELECT " + ",".join(selected) + " FROM " + table + " ORDER BY rowid DESC"
            )
        )
        connection.execute("DELETE FROM " + table)
        for row in rows:
            connection.execute(
                "INSERT INTO "
                + table
                + "("
                + ",".join(selected)
                + ") VALUES ("
                + ",".join("?" for _ in selected)
                + ")",
                tuple(row),
            )
    connection.commit()
    second = tmp_path / "second.jsonl"
    graph.export_jsonl(second)
    assert first.read_bytes() == second.read_bytes()
    assert graph.query("callers_of:helper") == before_query
    assert graph.evidence_records() == before_entities
    records = [json.loads(line) for line in second.read_text().splitlines()[1:]]
    keys = [(record["type"], record["id"]) for record in records]
    assert keys == sorted(keys)
    classes = [
        record["row"]["path"]
        for record in records
        if record["type"] == "projection"
        and record["table"] == "symbols"
        and record["row"]["kind"] == "class"
    ]
    callers = [
        record["row"]["source_path"]
        for record in records
        if record["type"] == "projection" and record["table"] == "edges"
    ]
    assert sorted(classes) == ["a.rb", "m.rb", "z.rb"]
    assert sorted(callers) == ["a.rb", "z.rb"]


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


# ---- Checker MAJOR-1 (the nineteenth defect): import path-traversal matrix ----
#
# content_hash proves byte integrity; it proves NOTHING about path
# semantics. Every scenario below forges a VALID-HASH export (via
# _forge_export, which re-signs content_hash/record_count to match the
# mutated body) so each test isolates the path-validation guard
# specifically, not the integrity checks already covered above.


def _import_target(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.mkdir()
    return target


def test_import_rejects_absolute_path(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    forged = tmp_path / "forged-absolute.jsonl"
    _forge_export(
        real_export,
        forged,
        lambda body: [r.update(path="/etc/passwd") for r in body if r["type"] == "symbol"],
    )

    target = _import_target(tmp_path, "target-absolute")
    with pytest.raises(ValueError, match="is an absolute path"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_rejects_dotdot_traversal(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    forged = tmp_path / "forged-dotdot.jsonl"
    _forge_export(
        real_export,
        forged,
        lambda body: [r.update(path="../../etc/passwd") for r in body if r["type"] == "symbol"],
    )

    target = _import_target(tmp_path, "target-dotdot")
    with pytest.raises(ValueError, match="traversal component"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_rejects_windows_style_separator_traversal(tmp_path: Path) -> None:
    """This project runs POSIX-only, but a hostile export can still
    contain a Windows-style `..\\..\\` string -- cheap to close, no
    reason to leave it open."""
    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    forged = tmp_path / "forged-windows-sep.jsonl"
    _forge_export(
        real_export,
        forged,
        lambda body: [r.update(path="..\\..\\secret") for r in body if r["type"] == "symbol"],
    )

    target = _import_target(tmp_path, "target-windows-sep")
    with pytest.raises(ValueError, match="traversal component"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_rejects_windows_drive_letter(tmp_path: Path) -> None:
    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    forged = tmp_path / "forged-drive-letter.jsonl"
    _forge_export(
        real_export,
        forged,
        lambda body: [
            r.update(path="C:\\Windows\\System32\\config") for r in body if r["type"] == "symbol"
        ],
    )

    target = _import_target(tmp_path, "target-drive-letter")
    with pytest.raises(ValueError, match="is an absolute path"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_rejects_path_that_escapes_only_via_a_symlink(tmp_path: Path) -> None:
    """The case the checker explicitly asked for: "a path that
    normalises out of the repo only after resolve()". Lexically,
    `app/escape_link/secret.txt` contains no `..` and is not absolute --
    the manual string check alone would PASS it. Only `resolve()`
    following a REAL on-disk symlink (`config.path_is_within`, shared
    with `enforcement.assert_path_in_repo`) catches this, proving the
    belt-and-suspenders second check earns its place rather than being
    decorative."""
    outside = tmp_path / "outside-the-repo"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret\n")

    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    forged = tmp_path / "forged-symlink.jsonl"
    _forge_export(
        real_export,
        forged,
        lambda body: [
            r.update(path="app/escape_link/secret.txt") for r in body if r["type"] == "symbol"
        ],
    )

    target = _import_target(tmp_path, "target-symlink")
    (target / "app").mkdir()
    (target / "app" / "escape_link").symlink_to(outside)

    with pytest.raises(ValueError, match="resolves outside the repository root"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_rejects_ambiguous_edge_candidate_path_escape(tmp_path: Path) -> None:
    """MAJOR-1 also covers an AMBIGUOUS edge's OWN `candidates[].path` --
    easy to miss since it is nested inside the edge record, not a
    top-level field."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app/a.rb", "class Hub\n def dup_name(x)\n end\nend\n")
    _write(repo, "app/b.rb", "class Hub\n def dup_name(x)\n end\nend\n")
    _write(repo, "app/caller.rb", "class Hub\n  def call\n    dup_name(1)\n  end\nend\n")
    graph = CodeGraph(repo=repo)
    graph.build()
    assert len(graph.callers_of("dup_name")[0]["candidates"]) == 2
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    forged = tmp_path / "forged-candidate.jsonl"

    def mutate(body):
        for r in body:
            if r["type"] == "edge" and r.get("candidates"):
                r["candidates"][0]["path"] = "../../etc/passwd"

    _forge_export(real_export, forged, mutate)

    target = _import_target(tmp_path, "target-candidate")
    with pytest.raises(ValueError, match="candidates\\[0\\]\\.path"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_rejects_missing_required_field(tmp_path: Path) -> None:
    """Checker MINOR-2: a valid-hash body record missing a required
    field used to raise a raw KeyError deep in the insert loop."""
    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    forged = tmp_path / "forged-missing-field.jsonl"
    _forge_export(
        real_export,
        forged,
        lambda body: [r.pop("line_end", None) for r in body if r["type"] == "symbol"],
    )

    target = _import_target(tmp_path, "target-missing-field")
    with pytest.raises(ValueError, match="missing required field"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_rejects_wrong_record_count(tmp_path: Path) -> None:
    """Checker MINOR-3: header record_count is recomputed and asserted,
    not merely a decorative stored field. Forges record_count AFTER
    _forge_export has already re-signed it correctly, isolating this
    guard from the content_hash guard."""
    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    lines = real_export.read_text().splitlines()
    header = json.loads(lines[0])
    body_lines = lines[1:]
    body_text = "".join(line + "\n" for line in body_lines)
    # content_hash left CORRECT (matches the real, untouched body) --
    # only record_count is wrong, isolating this guard specifically.
    header["content_hash"] = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    header["record_count"] = 999
    new_lines = [json.dumps(header, sort_keys=True, separators=(",", ":")), *body_lines]
    forged = tmp_path / "forged-record-count.jsonl"
    forged.write_text("\n".join(new_lines) + "\n")

    target = _import_target(tmp_path, "target-record-count")
    with pytest.raises(ValueError, match="record_count"):
        CodeGraph(repo=target).import_jsonl(forged)


def test_import_accepts_a_genuinely_valid_export_after_all_these_guards(tmp_path: Path) -> None:
    """Control case: every guard above must reject a FORGED file while
    still accepting the real, unmutated export -- a validation pass
    strict enough to reject every attack but broken enough to also
    reject genuine input would be just as much a defect."""
    graph = _build(tmp_path, "repo", _FILES)
    real_export = tmp_path / "export.jsonl"
    graph.export_jsonl(real_export)

    target = _import_target(tmp_path, "target-genuine")
    stats = CodeGraph(repo=target).import_jsonl(real_export)
    assert stats["symbols"] > 0
