"""S07 real-parser, migration and exchange anchors."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from atlas_aci.codegraph import SCHEMA_EPOCH, CodeGraph

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/s07"


def build(tmp_path: Path, files=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, source in (files or {"a.py": "def A():\n B()\ndef B():\n pass\n"}).items():
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source)
    graph = CodeGraph(repo)
    graph.build()
    return repo, graph


def package(tmp_path: Path):
    return build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "def A():\n from .b import B\n B(); B()\n",
            "pkg/b.py": "def B():\n from .a import A\n A()\n",
        },
    )


def canonical(graph: CodeGraph):
    return graph.evidence_records()


def test_parallel_relations(tmp_path: Path):
    _repo, graph = package(tmp_path)
    records = canonical(graph)
    entities = {r["id"]: r for r in records if r["type"] == "entity"}
    relations = [
        r
        for r in records
        if r["type"] == "assertion"
        and entities[r["source_entity_id"]]["name"] == "A"
        and r["target_entity_id"]
        and entities[r["target_entity_id"]]["name"] == "B"
    ]
    assert sorted(r["relation"] for r in relations) == ["call", "call", "import"]
    assert len({r["evidence_id"] for r in relations}) == 3
    output = tmp_path / "export.jsonl"
    graph.export_jsonl(output)
    target = tmp_path / "target"
    target.mkdir()
    imported = CodeGraph(target)
    imported.import_jsonl(output)
    assert canonical(imported) == records
    imported.close()
    graph.close()


def test_direction_preserved(tmp_path: Path):
    _repo, graph = build(tmp_path)
    output = tmp_path / "export.jsonl"
    graph.export_jsonl(output)
    target = tmp_path / "target"
    target.mkdir()
    imported = CodeGraph(target)
    imported.import_jsonl(output)
    records = canonical(imported)
    entities = {r["id"]: r for r in records if r["type"] == "entity"}
    calls = [
        r
        for r in records
        if r["type"] == "assertion" and r["relation"] == "call" and r["target_entity_id"]
    ]
    assert [
        (entities[r["source_entity_id"]]["name"], entities[r["target_entity_id"]]["name"])
        for r in calls
    ] == [("A", "B")]
    assert imported.query("callers_of:B")["edges"][0]["source"]["name"] == "A"
    assert not imported.query("callers_of:A")["edges"]
    imported.close()
    graph.close()


def test_line_move_identity(tmp_path: Path):
    repo, graph = build(
        tmp_path,
        {
            "a.py": (
                "class X:\n def f(self):\n  pass\n def f(self):\n  pass\n"
                "class Y:\n def f(self):\n  pass\n"
            ),
            "b.js": "class X { f() {} } class Y { f() {} }\n",
        },
    )
    before = canonical(graph)
    for file in (repo / "a.py", repo / "b.js"):
        file.write_bytes(b"\n\n" + file.read_bytes())
    graph.build(since="HEAD")
    after = canonical(graph)
    assert {r["id"] for r in before if r["type"] == "entity"} == {
        r["id"] for r in after if r["type"] == "entity"
    }
    assert {r["id"] for r in before if r["type"] == "occurrence"} != {
        r["id"] for r in after if r["type"] == "occurrence"
    }
    graph.close()


def test_source_evidence(tmp_path: Path):
    repo, graph = build(
        tmp_path, {"a.py": "def A():\n # NOTE: real rationale\n B()\ndef B():\n pass\n"}
    )
    source = (repo / "a.py").read_bytes()
    records = canonical(graph)
    occurrences = {r["id"]: r for r in records if r["type"] == "occurrence"}
    assert {r["kind"] for r in occurrences.values()} >= {"definition", "reference", "rationale"}
    for proof in (r for r in records if r["type"] == "evidence"):
        occurrence = occurrences[proof["occurrence_id"]]
        assert proof["source_sha256"] == hashlib.sha256(source).hexdigest()
        assert 0 <= proof["start_byte"] <= proof["end_byte"] <= len(source)
        assert (proof["start_byte"], proof["end_byte"]) == (
            occurrence["start_byte"],
            occurrence["end_byte"],
        )
    (repo / "a.py").write_bytes(b"changed after indexing")
    assert canonical(graph) == records
    graph.close()


def legacy(tmp_path: Path, external: bool = False):
    repo = tmp_path / "legacy"
    repo.mkdir()
    root = tmp_path / "graphs" if external else None
    graph = CodeGraph(repo, graph_root=root)
    path = graph.atlas_dir / "graph.6.db"
    path.write_bytes(gzip.decompress((FIXTURES / "epoch6.db.gz").read_bytes()))
    # The only fixture adaptation permitted by the approved plan: reanchor
    # repository metadata, preserving all original source hashes/query facts.
    with sqlite3.connect(path) as connection:
        inputs = json.loads(
            connection.execute(
                "SELECT value FROM manifest WHERE key='generation_inputs'"
            ).fetchone()[0]
        )
        inputs["repository"] = str(repo.resolve())
        raw = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        connection.execute("UPDATE manifest SET value=? WHERE key='generation_inputs'", (raw,))
        connection.execute(
            "UPDATE manifest SET value=? WHERE key='generation_id'",
            (hashlib.sha256(raw.encode()).hexdigest(),),
        )
    return repo, root, graph, path


@pytest.mark.parametrize("external", [False, True])
def test_migration_interruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external: bool):
    repo, root, writer, old_path = legacy(tmp_path, external)
    old_bytes = old_path.read_bytes()
    reader = CodeGraph(repo, read_only=True, graph_root=root)
    with reader.reader_lease() as lease:
        original = lease.generation_id
        assert lease.search_symbol("A")["definitions"]
        convert = writer._convert_legacy_into

        def interrupted(*args):
            convert(*args)
            raise RuntimeError("interrupted before publication")

        with monkeypatch.context() as patch:
            patch.setattr(writer, "_convert_legacy_into", interrupted)
            with pytest.raises(RuntimeError, match="interrupted"):
                writer.migrate_legacy()
        assert old_path.read_bytes() == old_bytes
        assert not (writer.atlas_dir / "graph.7.db").exists()
        assert lease.generation_id == original
        writer.migrate_legacy()
        assert lease.generation_id == original
        assert lease.search_symbol("A")["definitions"]
        with reader.reader_lease() as current:
            assert current.generation_id != original
            assert current.search_symbol("A")["definitions"]
    assert old_path.read_bytes() == old_bytes
    assert not list(writer.atlas_dir.glob("*.tmp.*"))
    assert all(
        r["provenance_state"] == "legacy_unavailable"
        for r in canonical(writer)
        if r["type"] == "evidence"
    )
    assert not list(repo.glob("*.py"))
    reader.close()
    writer.close()


def test_assertion_replay(tmp_path: Path):
    _repo, graph = package(tmp_path)
    artifact = tmp_path / "export.jsonl"
    graph.export_jsonl(artifact)
    target = tmp_path / "target"
    target.mkdir()
    imported = CodeGraph(target)
    imported.import_jsonl(artifact)
    first = canonical(imported)
    again = tmp_path / "again.jsonl"
    imported.export_jsonl(again)
    imported.import_jsonl(artifact)
    assert canonical(imported) == first
    imported.export_jsonl(again)
    assert again.read_bytes() == artifact.read_bytes()
    assert not list(target.glob("*.py"))
    imported.close()
    graph.close()


def rewrite_artifact(path: Path, mutate):
    from atlas_aci.storage import evidence as e

    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    records = [json.loads(line) for line in lines[1:]]
    mutate(records)
    replacements = {}

    def replace(value):
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items() if key != "id"}
        return value

    for _ in range(10):
        changed = False
        for i, record in enumerate(records):
            old = record["id"]
            new = replace(record)
            new["id"] = e.identifier(new)
            if new["id"] != old:
                replacements[old] = new["id"]
                changed = True
            records[i] = new
        if not changed:
            break
    records.sort(key=lambda r: (r["type"], r["id"]))
    body = "".join(e.canonical(record) + "\n" for record in records)
    header["content_hash"] = hashlib.sha256(body.encode()).hexdigest()
    header["record_count"] = len(records)
    path.write_text(e.canonical(header) + "\n" + body)


@pytest.mark.parametrize(
    "mutation",
    [
        "bool_span",
        "negative_span",
        "proof_hash",
        "projection_target",
        "projection_source",
        "missing_entity",
        "future",
    ],
)
def test_malformed_exchange_retains_published_graph(tmp_path: Path, mutation: str):
    _repo, graph = build(tmp_path)
    artifact = tmp_path / "real.jsonl"
    graph.export_jsonl(artifact)
    before = canonical(graph)
    generation = graph.generation_id

    def mutate(records):
        if mutation in {"bool_span", "negative_span"}:
            item = next(r for r in records if r["type"] == "occurrence")
            item["start_byte"] = True if mutation == "bool_span" else -1
        elif mutation == "proof_hash":
            next(r for r in records if r["type"] == "evidence")["source_sha256"] = "0" * 64
        elif mutation in {"projection_target", "projection_source"}:
            item = next(r for r in records if r["type"] == "projection" and r["table"] == "edges")
            item["row"]["target_name" if mutation == "projection_target" else "source_name"] = (
                "contradictory"
            )
        elif mutation == "missing_entity":
            item = next(r for r in records if r["type"] == "entity")
            records.remove(item)

    if mutation == "future":
        data = artifact.read_text().splitlines()
        header = json.loads(data[0])
        header["format"] = "atlas-evidence.v99"
        artifact.write_text(json.dumps(header) + "\n" + "\n".join(data[1:]) + "\n")
    else:
        rewrite_artifact(artifact, mutate)
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.generation_id == generation and canonical(graph) == before
    assert graph.search_symbol("A")["definitions"]
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


@pytest.mark.parametrize(
    "table", ["edges", "refs", "symbols", "import_bindings", "rationale", "assignment_targets"]
)
def test_missing_supported_projection_is_rejected(tmp_path: Path, table: str):
    _repo, graph = build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "def A():\n # NOTE: why\n from .b import B\n B()\n",
            "pkg/b.py": "def B():\n pass\n",
        },
    )
    artifact = tmp_path / "real.jsonl"
    graph.export_jsonl(artifact)
    before = canonical(graph)
    generation = graph.generation_id

    def omit(records):
        removed = [r for r in records if r["type"] == "projection" and r["table"] == table]
        assert removed
        records[:] = [r for r in records if r not in removed]

    rewrite_artifact(artifact, omit)
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.generation_id == generation and canonical(graph) == before
    assert graph.query("callers_of:B")["edges"]
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


def test_epoch6_jsonl_adapter_keeps_unknown_reference_coverage(tmp_path: Path):
    repo = tmp_path / "source-free"
    repo.mkdir()
    graph = CodeGraph(repo)
    graph.import_jsonl(FIXTURES / "epoch6.jsonl")
    assert graph.query("callers_of:B")["edges"][0]["source"]["name"] == "A"
    assert graph.query("callers_of:missing")["unresolved_refs"] is None
    first = canonical(graph)
    assert all(
        r["source_sha256"] is None and r["start_byte"] is None
        for r in first
        if r["type"] == "evidence"
    )
    assert graph.status()["completeness"]["state"] == "unknown"
    output = tmp_path / "current.jsonl"
    graph.export_jsonl(output)
    graph.import_jsonl(output)
    assert canonical(graph) == first
    assert graph.query("callers_of:missing")["unresolved_refs"] is None
    assert not list(repo.glob("*.py"))
    graph.close()


def test_failed_refresh_never_publishes_intermediate_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo, _root, writer, old_path = legacy(tmp_path)
    before = old_path.read_bytes()
    (repo / "legacy.py").write_text("def broken(:\n pass\n")
    import tree_sitter_language_pack

    def fatal_parser(_language):
        raise LookupError("controlled provider failure after staged conversion")

    monkeypatch.setattr(tree_sitter_language_pack, "get_parser", fatal_parser)
    with pytest.raises(RuntimeError):
        writer.build(since="HEAD")
    assert old_path.read_bytes() == before and not writer.db_path.exists()
    reader = CodeGraph(repo, read_only=True)
    assert reader.search_symbol("A")["definitions"]
    status = reader.status()
    assert status["attempt"]["observation"]["files"][0]["reason"] == "parser_exception"
    assert status["completeness"]["state"] != "complete"
    reader.close()
    writer.close()


def test_malformed_present_current_never_falls_back(tmp_path: Path):
    from atlas_aci.enforcement import ToolError

    repo, _root, writer, old_path = legacy(tmp_path)
    old_bytes = old_path.read_bytes()
    writer.db_path.write_bytes(b"not SQLite")
    before = {p.name: p.read_bytes() for p in writer.atlas_dir.iterdir() if p.is_file()}
    reader = CodeGraph(repo, read_only=True)
    with pytest.raises(ToolError), reader.reader_lease():
        pass
    assert not reader.epoch_ok()
    assert old_path.read_bytes() == old_bytes
    assert before == {p.name: p.read_bytes() for p in writer.atlas_dir.iterdir() if p.is_file()}
    reader.close()
    writer.close()


def test_conversion_barrier_exposes_only_completed_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import concurrent.futures
    import threading

    repo, root, writer, old_path = legacy(tmp_path, True)
    old_bytes = old_path.read_bytes()
    reader = CodeGraph(repo, read_only=True, graph_root=root)
    entered = threading.Event()
    release = threading.Event()
    convert = writer._convert_legacy_into

    def paused(*args):
        convert(*args)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(writer, "_convert_legacy_into", paused)
    with reader.reader_lease() as held:
        original = held.generation_id
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(writer.migrate_legacy)
            assert entered.wait(10)
            with reader.reader_lease() as concurrent:
                assert concurrent.generation_id == original
                assert concurrent.search_symbol("A")["definitions"]
            assert not writer.db_path.exists()
            release.set()
            task.result(10)
        assert held.generation_id == original and held.search_symbol("A")["definitions"]
        with reader.reader_lease() as current:
            assert current.generation_id != original
    assert old_path.read_bytes() == old_bytes
    reader.close()
    writer.close()


def test_modern_evidence_cannot_be_downgraded_to_legacy_escape(tmp_path: Path):
    __repo, graph = build(tmp_path)
    artifact = tmp_path / "real.jsonl"
    graph.export_jsonl(artifact)
    before = canonical(graph)
    generation = graph.generation_id

    def downgrade(records):
        assertion = next(r for r in records if r["type"] == "assertion" and r["relation"] == "call")
        proof = next(
            r for r in records if r["type"] == "evidence" and r["id"] == assertion["evidence_id"]
        )
        occurrence = next(
            r for r in records if r["type"] == "occurrence" and r["id"] == proof["occurrence_id"]
        )
        assertion.update(
            resolution="LEGACY_UNAVAILABLE",
            target_entity_id=None,
            unresolved_reason="legacy_unavailable",
        )
        for item in (proof, occurrence):
            item.update(
                provenance_state="legacy_unavailable",
                source_sha256=None,
                source_size=None,
                start_byte=None,
                end_byte=None,
            )

    rewrite_artifact(artifact, downgrade)
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.generation_id == generation and canonical(graph) == before
    assert (
        graph.query("callers_of:B")["edges"] and graph.query("callers_of:B")["unresolved_refs"] == 0
    )
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


@pytest.mark.parametrize(
    "bad_path", ["/etc/passwd", "../../escape", r"C:\Windows\System32", "C:/Windows/System32"]
)
def test_current_format_portable_absolute_paths_rejected(tmp_path: Path, bad_path: str):
    __repo, graph = build(tmp_path)
    artifact = tmp_path / "real.jsonl"
    graph.export_jsonl(artifact)
    before = canonical(graph)

    def rewrite(records):
        for record in records:
            if record["type"] in {"entity", "occurrence"}:
                record["path"] = bad_path
                if record["type"] == "entity" and record["kind"] == "file":
                    record["name"] = bad_path
            if record["type"] == "projection":
                for key in record["row"]:
                    if "path" in key and record["row"][key] is not None:
                        record["row"][key] = bad_path

    rewrite_artifact(artifact, rewrite)
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert canonical(graph) == before and graph.query("callers_of:B")["edges"]
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


@pytest.mark.parametrize(
    "bad_path", ["/etc/passwd", "../../escape", r"C:\Windows\System32", "C:/Windows/System32"]
)
def test_epoch6_adapter_retains_portable_path_guards(tmp_path: Path, bad_path: str):
    _repo, graph = build(tmp_path)
    before = canonical(graph)
    generation = graph.generation_id
    db_bytes = graph.db_path.read_bytes()
    lines = (FIXTURES / "epoch6.jsonl").read_bytes().splitlines(keepends=True)
    header = json.loads(lines[0])
    rows = [json.loads(line) for line in lines[1:]]
    symbols = [row for row in rows if row["type"] == "symbol"]
    assert symbols
    symbols[0]["path"] = bad_path
    body = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows
    )
    header.update(content_hash=hashlib.sha256(body).hexdigest(), record_count=len(rows))
    artifact = tmp_path / "bad-legacy.jsonl"
    artifact.write_bytes(
        (json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n").encode() + body
    )
    with pytest.raises(ValueError):
        graph.import_jsonl(artifact)
    assert graph.db_path.read_bytes() == db_bytes and graph.generation_id == generation
    assert canonical(graph) == before and graph.query("callers_of:B")["edges"]
    assert not list(graph.atlas_dir.glob("*.tmp.*"))
    graph.close()


def test_multiple_real_producers_preserve_parallel_assertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from atlas_aci.codegraph import QUERIES
    from atlas_aci.storage import evidence as e

    _repo, graph = package(tmp_path)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    graph.export_jsonl(first)
    # An actual second query compilation/extraction with a different exact
    # producer input, preserving syntax semantics and consumed source bytes.
    with monkeypatch.context() as patch:
        patch.setitem(QUERIES, "python", QUERIES["python"] + "\n")
        graph.build()
        graph.export_jsonl(second)
    original = [json.loads(line) for line in first.read_text().splitlines()[1:]]
    successor = [json.loads(line) for line in second.read_text().splitlines()[1:]]
    first_producers = {r["id"] for r in original if r["type"] == "producer"}
    next_producers = {r["id"] for r in successor if r["type"] == "producer"}
    assert first_producers and next_producers and first_producers.isdisjoint(next_producers)
    combined = {r["id"]: r for r in original + successor}
    records = sorted(combined.values(), key=lambda r: (r["type"], r["id"]))
    body = "".join(e.canonical(r) + "\n" for r in records).encode()
    header = json.loads(first.read_text().splitlines()[0])
    header.update(record_count=len(records), content_hash=hashlib.sha256(body).hexdigest())
    artifact = tmp_path / "combined.jsonl"
    artifact.write_bytes((e.canonical(header) + "\n").encode() + body)
    destination = tmp_path / "source-free"
    destination.mkdir()
    imported = CodeGraph(destination)
    imported.import_jsonl(artifact)
    expected = [
        r
        for r in records
        if r["type"] in {"entity", "occurrence", "producer", "evidence", "assertion"}
    ]
    assert canonical(imported) == expected
    assertions = [r for r in expected if r["type"] == "assertion" and r["relation"] == "call"]
    assert len(assertions) == 6 and len({r["evidence_id"] for r in assertions}) == 6
    assert {
        r["id"] for r in expected if r["type"] == "producer"
    } == first_producers | next_producers
    replay = tmp_path / "replay.jsonl"
    imported.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    imported.import_jsonl(replay)
    assert canonical(imported) == expected
    assert imported.query("callers_of:B")["edges"]
    imported.close()
    graph.close()


def test_legacy_duplicate_rationale_preserves_unavailable_target(tmp_path: Path):
    _repo, graph = build(tmp_path)
    graph.import_jsonl(FIXTURES / "epoch6-duplicate-rationale.jsonl")
    records = canonical(graph)
    assertions = [
        r for r in records if r["type"] == "assertion" and r["relation"] == "rationale_for"
    ]
    assert len(assertions) == 1
    assert assertions[0]["target_entity_id"] is None
    assert assertions[0]["resolution"] == "LEGACY_UNAVAILABLE"
    assert len([r for r in records if r["type"] == "entity" and r["name"] == "A"]) == 2
    output = tmp_path / "current.jsonl"
    graph.export_jsonl(output)
    graph.import_jsonl(output)
    assert canonical(graph) == records
    graph.close()


def test_migrate_validates_current_path_even_with_cached_legacy_handle(tmp_path: Path):
    repo, root, writer, old_path = legacy(tmp_path)
    assert writer.search_symbol("A")["definitions"] and writer.db is not None
    old = old_path.read_bytes()
    with writer.reader_lease() as held:
        generation = held.generation_id
        writer.db_path.write_bytes(b"not SQLite")
        with pytest.raises(ValueError, match="existing current generation unavailable"):
            writer.migrate_legacy()
        assert writer.db_path.read_bytes() == b"not SQLite" and old_path.read_bytes() == old
        assert held.generation_id == generation and held.search_symbol("A")["definitions"]
        writer.db_path.unlink()
        other = CodeGraph(repo, graph_root=root)
        assert other.migrate_legacy()["already_current"] is False
        current = other.db_path.read_bytes()
        assert writer.migrate_legacy() == {"schema_epoch": SCHEMA_EPOCH, "already_current": True}
        assert other.db_path.read_bytes() == current and old_path.read_bytes() == old
        assert held.generation_id == generation and held.search_symbol("A")["definitions"]
        other.close()
    assert not list(writer.atlas_dir.glob("*.tmp.*"))
    writer.close()
