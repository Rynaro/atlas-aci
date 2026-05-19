"""Integration tests for the Rust Tree-sitter query.

Builds a CodeGraph over a fixture .rs file in tmp_path and asserts the
extracted symbols and refs match the constructs in the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_aci.codegraph import QUERIES, CodeGraph

RUST_FIXTURE = """\
pub mod utils {
    pub fn helper() -> i32 { 42 }
}

pub struct Counter {
    value: i32,
}

pub enum Status {
    Active,
    Inactive,
}

pub trait Greet {
    fn greet(&self) -> String;
}

impl Counter {
    pub fn new() -> Self {
        Counter { value: 0 }
    }

    pub fn increment(&mut self) {
        self.value += 1;
    }
}

impl Greet for Counter {
    fn greet(&self) -> String {
        String::from("hi")
    }
}

pub fn run() {
    let mut c = Counter::new();
    c.increment();
    let _ = utils::helper();
}
"""


@pytest.fixture
def rust_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text(RUST_FIXTURE)
    return tmp_path


def test_rust_query_compiles() -> None:
    """VG-5: the literal query string must parse against the Rust grammar."""
    from tree_sitter_language_pack import get_language

    get_language("rust").query(QUERIES["rust"])  # must not raise


def test_rust_build_indexes_symbols_and_refs(rust_repo: Path) -> None:
    graph = CodeGraph(repo=rust_repo, langs=["rust"])
    stats = graph.build()

    assert stats["files_indexed"] >= 1
    assert stats["symbols"] > 0
    assert stats["refs"] > 0

    rows = graph.db.execute("SELECT name, kind FROM symbols ORDER BY name, kind").fetchall()
    pairs = {(r["name"], r["kind"]) for r in rows}

    # Defs from the fixture
    assert ("utils", "module") in pairs
    assert ("helper", "function") in pairs
    assert ("Counter", "class") in pairs
    assert ("Status", "class") in pairs
    assert ("Greet", "trait") in pairs
    assert ("run", "function") in pairs
    # Impl methods
    assert ("new", "method") in pairs
    assert ("increment", "method") in pairs
    assert ("greet", "method") in pairs

    callees = {
        r["callee_name"]
        for r in graph.db.execute("SELECT callee_name FROM refs").fetchall()
    }
    # Free / qualified / method callees from `run`
    assert "new" in callees           # Counter::new() — scoped_identifier tail
    assert "increment" in callees     # c.increment()  — field_identifier
    assert "helper" in callees        # utils::helper() — scoped_identifier tail
    assert "from" in callees          # String::from("hi") — scoped_identifier tail


def test_rust_lang_not_in_default_set(tmp_path: Path) -> None:
    """Sanity: CodeGraph default langs do not include rust (callers opt in)."""
    graph = CodeGraph(repo=tmp_path)
    assert "rust" not in graph.langs
