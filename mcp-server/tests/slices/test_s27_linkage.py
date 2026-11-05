"""ACI-S27 linkage: what the leased snapshot says about a declared source location."""

from __future__ import annotations

import base64
import gzip
import json
from contextlib import ExitStack
from pathlib import Path

import pytest
from test_s16_support import imported
from test_s27_support import (
    FILES,
    ROOT,
    SERVICE,
    T1,
    World,
    classes,
    make_world,
    observation,
    otlp,
    run_import,
    sid,
    span_json,
)

from atlas_aci.codegraph import CodeGraph
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import DiffUsageError, Side

ROOTS = [{"from": "/srv/app", "to": ""}, {"from": "", "to": ""}]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s27-link"))


def test_otel_not_indexed_files_are_file_not_indexed(world):
    with ExitStack() as stack:
        reader = runtime.open_active(stack, world.side)
        assert "web/page.js" in reader._source_lease.current
        records = json.loads(reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()[0])
        states = {r["path"]: r["state"] for r in records if r["type"] == "observation"}
    assert states["web/page.js"] == "disabled" and states["app/service.py"] == "indexed"
    artifact = otlp(
        span_json(1, path="/srv/app/web/page.js", line=1),
        span_json(2, path="/srv/app/app/broken.py", line=1),
        span_json(3, path="/srv/app/app/__init__.py"),
    )
    overlay = run_import(world.side, world.snapshot_id, artifact, ROOTS)
    assert classes(overlay) == {
        1: "unlinked_file_not_indexed",
        2: "unlinked_file_not_indexed",
        3: "linked_file",
    }


def test_otel_symbol_is_the_innermost_enclosing_definition(world):
    lines = {1: "Service", 2: "handle", 3: "handle", 6: "helper", 7: "helper"}
    spans = [span_json(n, path="/srv/app/app/service.py", line=n) for n in (1, 2, 3, 4, 5, 6, 7)]
    overlay = run_import(world.side, world.snapshot_id, otlp(*spans), ROOTS)
    for number, name in lines.items():
        symbol = observation(overlay, T1, number)["linkage"]["symbol"]
        assert symbol["name"] == name, number
    assert classes(overlay)[4] == classes(overlay)[5] == "linked_file"  # blank lines
    assert observation(overlay, T1, 1)["linkage"]["symbol"]["kind"] == "class"
    # No line: the file only.
    only = run_import(
        world.side, world.snapshot_id, otlp(span_json(1, path="/srv/app/app/service.py")), ROOTS
    )
    assert only["observations"][0]["linkage"]["classification"] == "linked_file"


def test_otel_line_counts_are_lf_counts():
    assert [runtime.line_count(d) for d in (b"", b"\n", b"a", b"a\n", b"a\nb", b"a\r\nb\r\n")] == [
        0,
        1,
        1,
        1,
        2,
        2,
    ]
    assert runtime.line_count(b"a\rb\rc\r") == 1 and runtime.line_count(b"\n\n") == 2


def test_otel_line_range_uses_lf_rows(tmp_path):
    files = {
        "app/__init__.py": "",
        "app/crlf.py": "x = 1\r\ny = 2\r\n",
        "app/nonl.py": "x = 1\ny = 2",
        "app/cr.py": "# a\r# b\r# c\r",
    }
    world = make_world(tmp_path, files)
    paths = {"crlf": "app/crlf.py", "nonl": "app/nonl.py", "cr": "app/cr.py"}
    cases = [("crlf", 2, "linked"), ("crlf", 3, "out"), ("nonl", 2, "linked"), ("nonl", 3, "out")]
    cases += [("cr", 1, "linked"), ("cr", 2, "out")]
    spans = [
        span_json(n, path="/srv/app/" + paths[name], line=line)
        for n, (name, line, _) in enumerate(cases, start=1)
    ]
    overlay = run_import(world.side, world.snapshot_id, otlp(*spans), ROOTS)
    got = classes(overlay)
    for number, (_name, _line, expected) in enumerate(cases, start=1):
        wanted = "unlinked_line_out_of_range" if expected == "out" else None
        if wanted:
            assert got[number] == wanted, (number, got)
        else:
            assert got[number].startswith("linked_"), (number, got)


def test_otel_declared_path_variants_never_link(world):
    declared = [
        "C:\\srv\\app\\app\\service.py",
        "C:/srv/app/app/service.py",
        "\\srv\\app\\app\\service.py",
        "/SRV/APP/app/service.py",
        "/srv/application/app/service.py",
        "/srv/app/app/../app/service.py",
        "/srv/app/app//service.py",
        "/srv/app",
    ]
    spans = [span_json(n, path=p, line=3) for n, p in enumerate(declared, start=1)]
    spans += [
        span_json(20, path="/srv/app/APP/service.py", line=3),
        span_json(21, path="/srv/app/app/serv\u0131ce.py", line=3),
        span_json(22, path="/srv/app/app/service.py", line=3),
    ]
    overlay = run_import(world.side, world.snapshot_id, otlp(*spans), ROOTS)
    got = classes(overlay)
    assert {got[n] for n in range(1, 9)} == {"unlinked_path_outside_root"}
    assert got[20] == got[21] == "unlinked_file_not_in_snapshot"
    assert got[22] == "linked_symbol"


def test_otel_snapshot_after_reindex_no_longer_matches(tmp_path):
    world = make_world(tmp_path, {"app/__init__.py": "", "app/service.py": SERVICE})
    artifact = otlp(span_json(1, path="/srv/app/app/service.py", line=3))
    before = run_import(world.side, world.snapshot_id, artifact, ROOTS)
    assert classes(before) == {1: "linked_symbol"}
    (world.repo / "app/service.py").write_text(SERVICE + "\n\ndef extra():\n    return 1\n")
    from test_s19_support import build

    changed = build(world.repo, world.root, ["python"])["snapshot_id"]
    assert changed != world.snapshot_id
    after = run_import(world.side, world.snapshot_id, artifact, ROOTS)
    assert classes(after) == {1: "unlinked_build_mismatch"}
    assert after["snapshot"]["supplied_id"] == changed and after["snapshot"]["matched"] is False
    assert run_import(world.side, changed, artifact, ROOTS)["snapshot"]["matched"] is True


def stage_native(base: Path, fixture: str, repository_key: str, files_key: str, decode):
    data = json.loads(gzip.decompress((ROOT / "mcp-server/tests/fixtures" / fixture).read_bytes()))
    repo = Path(data[repository_key])
    graph = CodeGraph(repo, graph_root=base / "old-graph")
    try:
        for name, item in data[files_key].items():
            target = graph.atlas_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(decode(item)))
    finally:
        graph.close()
    return Side(repo, base / "old-graph")


def test_otel_epoch_eight_links_and_history_records_are_ignored(tmp_path):
    side = stage_native(
        tmp_path / "eight",
        "s17/native8.json.gz",
        "original_repository",
        "native_files",
        lambda item: item["base64"],
    )
    with ExitStack() as stack:
        reader = runtime.open_active(stack, side)
        assert reader._lease_schema_epoch == 8
        snapshot = reader.snapshot_id
        raw = reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()[0]
    kinds = {r["type"] for r in json.loads(raw)}
    assert kinds == {"observation", "history"}  # a history record has no `state`
    artifact = otlp(
        span_json(1, path="/srv/n/a.py", line=1),
        span_json(2, path="/srv/n/history.py", line=1),
        span_json(3, path="/srv/n/b.py"),
    )
    overlay = run_import(side, snapshot, artifact, [{"from": "/srv/n", "to": ""}])
    assert classes(overlay) == {
        1: "linked_symbol",
        2: "unlinked_file_not_indexed",
        3: "linked_file",
    }
    symbol = observation(overlay, T1, 1)["linkage"]["symbol"]
    assert symbol["name"] == "A" and symbol["entity_id"] is not None
    assert sid(1) == observation(overlay, T1, 1)["span_id"]


def test_otel_older_epochs_need_an_upgrade(tmp_path):
    side = stage_native(tmp_path / "seven", "s12/native7.json.gz", "repository", "files", str)
    with pytest.raises(runtime.OverlayRejected) as info, ExitStack() as stack:
        runtime.open_active(stack, side)
    assert info.value.reason == "index_upgrade_required"
    assert str(info.value) == "overlay_rejected: index_upgrade_required"


def exported_world(world: World, base: Path) -> World:
    artifact = base / "export.jsonl"
    source = CodeGraph(world.repo, graph_root=world.root)
    try:
        source.export_jsonl(artifact)
    finally:
        source.close()
    target = World(world.repo, base / "imported-graph", "")
    graph = CodeGraph(target.repo, graph_root=target.root)
    try:
        graph.import_jsonl(artifact)
    finally:
        graph.close()
    return target


def test_otel_unavailable_graphs_are_typed(world, tmp_path):
    artifact = otlp(span_json(1, path="/srv/app/app/service.py", line=3))
    with pytest.raises(runtime.OverlayRejected) as info:
        run_import(Side(world.repo, tmp_path / "nowhere"), world.snapshot_id, artifact, ROOTS)
    assert info.value.reason == "index_unavailable"
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory")
    with pytest.raises(DiffUsageError):
        run_import(Side(world.repo, occupied), world.snapshot_id, artifact, ROOTS)
    portable = exported_world(world, tmp_path)
    with pytest.raises(runtime.OverlayRejected) as info:
        run_import(portable.side, world.snapshot_id, artifact, ROOTS)
    assert info.value.reason == "source_snapshot_unavailable"


def test_otel_derived_generations_never_link_to_foreign_symbols(tmp_path):
    repo, graph, result = imported(tmp_path)
    side = Side(repo)
    with ExitStack() as stack:
        reader = runtime.open_active(stack, side)
        assert reader.snapshot_id == result["snapshot_id"]
        covered = reader.db.execute(
            "SELECT count(*) FROM symbols WHERE path='src/greeting.ts' AND line_start<=2 "
            "AND line_end>=2 AND kind LIKE 'scip\\_%' ESCAPE '\\'"
        ).fetchone()[0]
        real = reader.db.execute(
            "SELECT count(*) FROM symbols WHERE path='src/greeting.ts' AND line_start<=2 "
            "AND line_end>=2 AND kind NOT LIKE 'scip\\_%' ESCAPE '\\'"
        ).fetchone()[0]
    assert covered > 0 and real == 0  # only a SCIP row spans line 2
    artifact = otlp(
        span_json(1, path="/srv/ts/src/greeting.ts", line=2),
        span_json(2, path="/srv/ts/src/greeting.ts", line=3),
    )
    overlay = run_import(side, result["snapshot_id"], artifact, [{"from": "/srv/ts", "to": ""}])
    assert classes(overlay) == {1: "linked_file", 2: "linked_symbol"}
    symbol = observation(overlay, T1, 2)["linkage"]["symbol"]
    assert not symbol["kind"].startswith("scip_") and symbol["kind"] == "function"
    assert FILES and graph is not None


def test_otel_history_records_never_replace_observations():
    """A history record shares its path with the observation and carries no state."""
    import sqlite3
    from types import SimpleNamespace

    history = {"type": "history", "path": "a.py", "language": "python", "records": []}
    observation_ = {
        "type": "observation",
        "path": "a.py",
        "language": "python",
        "state": "indexed",
        "observed_source_sha256": "ab" * 32,
    }
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT)")
    for label, records in (("after", [observation_, history]), ("before", [history, observation_])):
        db.execute("DELETE FROM manifest")
        db.execute("INSERT INTO manifest VALUES ('source_observations', ?)", (json.dumps(records),))
        linker = runtime._Linker(SimpleNamespace(db=db))
        assert linker.record("a.py") == observation_, label
        assert linker.record("b.py") is None
