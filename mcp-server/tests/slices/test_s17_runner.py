"""Lossless typed SQLite BLOB row capture without logical identity exclusions."""

import importlib.util
import sqlite3
import subprocess
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "s17_runner", ROOT / "atlas/evals/stdio_runner.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blob_identity_lossless_typed_and_diagnostic_only_invariance(tmp_path, monkeypatch):
    runner = load_runner()
    db = tmp_path / "graph.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE facts(value)")
        conn.execute("CREATE TABLE manifest(key TEXT,value TEXT)")
        conn.execute("INSERT INTO manifest VALUES ('diagnostic_publication_token','one')")
        conn.execute("INSERT INTO manifest VALUES ('coverage_snapshot','keep-me')")
        conn.execute("INSERT INTO facts VALUES (?)", ("original",))
    frozen = types.ModuleType("frozen_runner")
    frozen.__file__ = str(ROOT / "atlas/evals/stdio_runner.py")
    source = subprocess.check_output(
        ["git", "show", "73792406d65c81c6a5825ddd3b9cb1e26b195309:atlas/evals/stdio_runner.py"],
        cwd=ROOT,
    )
    exec(compile(source, frozen.__file__, "exec"), frozen.__dict__)
    assert runner.index_identity(db) == frozen.index_identity(db)
    seen = {}
    original_digest = runner.digest

    def capture(value):
        seen.update(value)
        return original_digest(value)

    monkeypatch.setattr(runner, "digest", capture)
    identities = []
    for value in (
        b"",
        b"abc",
        b"abd",
        b"\x00\xff\x80",
        "",
        "abc",
        '{"sqlite_type":"blob","hex":"616263"}',
    ):
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE facts SET value=?", (value,))
        identity = runner.index_identity(db)
        identities.append(identity)
        cell = seen["facts"]["rows"][0][0]
        if isinstance(value, bytes):
            assert cell == {"sqlite_type": "blob", "hex": value.hex()}
            assert bytes.fromhex(cell["hex"]) == value
        else:
            assert cell == value
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE manifest SET value='two' WHERE key='diagnostic_publication_token'")
        assert runner.index_identity(db) == identity
    assert len(set(identities)) == len(identities)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE manifest SET value='semantic-change' WHERE key='coverage_snapshot'")
    assert runner.index_identity(db) != identities[-1]
