"""Content cache acceptance: real parser/extraction and published graph outputs."""

from pathlib import Path

import tree_sitter_language_pack as pack

from atlas_aci.codegraph import CodeGraph


def counted(monkeypatch):
    counts = {"parser": 0, "extract": 0}
    get_parser, extract = pack.get_parser, CodeGraph._extract

    class Parser:
        def __init__(self, parser):
            self.parser = parser

        def parse(self, source):
            counts["parser"] += 1
            return self.parser.parse(source)

    monkeypatch.setattr(pack, "get_parser", lambda lang: Parser(get_parser(lang)))

    def real_extract(self, *args):
        counts["extract"] += 1
        return extract(self, *args)

    monkeypatch.setattr(CodeGraph, "_extract", real_extract)
    return counts


def fixture(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def B():\n pass\ndef A():\n # NOTE: calls B\n B()\n")
    return repo


def test_unchanged_no_parse(tmp_path, monkeypatch):
    counts = counted(monkeypatch)
    repo = fixture(tmp_path)
    graph = CodeGraph(repo)
    graph.build()
    assert counts == {"parser": 1, "extract": 1}
    first = graph.evidence_records()
    counts.update(parser=0, extract=0)
    graph.close()
    writer = CodeGraph(repo)
    writer.build()  # full build, new writer: must use the persistent extraction packet
    assert counts == {"parser": 0, "extract": 0}
    assert writer.evidence_records() == first
    assert writer.query("callers_of:B")["edges"]
    writer.close()


def test_same_stat_changed_content(tmp_path, monkeypatch):
    import os

    counts = counted(monkeypatch)
    repo = fixture(tmp_path)
    graph = CodeGraph(repo)
    graph.build()
    path = repo / "a.py"
    before = path.stat()
    path.write_bytes(path.read_bytes().replace(b"def B", b"def C"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    counts.update(parser=0, extract=0)
    stats = graph.build(since="HEAD")
    assert counts == {"parser": 1, "extract": 1}
    assert stats["extraction_cache"]["misses"] == 1
    assert graph.search_symbol("C")["definitions"]
    assert not graph.search_symbol("B")["definitions"]
    graph.close()


def test_producer_change(tmp_path, monkeypatch):
    from atlas_aci.codegraph import QUERIES

    counts = counted(monkeypatch)
    graph = CodeGraph(fixture(tmp_path))
    graph.build()
    monkeypatch.setitem(QUERIES, "python", QUERIES["python"] + "\n; changed query producer\n")
    counts.update(parser=0, extract=0)
    graph.build(since="HEAD")
    assert counts == {"parser": 1, "extract": 1}
    assert graph.query("callers_of:B")["edges"]
    graph.close()


def test_config_change(tmp_path, monkeypatch):
    counts = counted(monkeypatch)
    repo = fixture(tmp_path)
    graph = CodeGraph(repo, langs=["python"])
    graph.build()
    graph.close()
    counts.update(parser=0, extract=0)
    writer = CodeGraph(repo, langs=["python", "javascript"])
    writer.build(since="HEAD")
    assert counts == {"parser": 1, "extract": 1}
    writer.close()


def test_partial_cache_rejected(tmp_path, monkeypatch):
    counts = counted(monkeypatch)
    graph = CodeGraph(fixture(tmp_path))
    graph.build()
    entries = list((graph.atlas_dir / "extraction-cache/v1").glob("*/*.json"))
    assert len(entries) == 1
    entries[0].write_text('{"format":')
    (entries[0].parent / ".entry-interrupted").write_text("partial")
    counts.update(parser=0, extract=0)
    stats = graph.build()
    assert counts == {"parser": 1, "extract": 1}
    assert stats["extraction_cache"]["miss_reasons"] == {"invalid": 1}
    counts.update(parser=0, extract=0)
    assert graph.build()["extraction_cache"]["disk_hits"] == 1
    assert counts == {"parser": 0, "extract": 0}
    graph.close()


def test_warm_replay(tmp_path, monkeypatch):
    import copy
    from dataclasses import asdict

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/b.py").write_text("def B(): pass\n")
    (repo / "pkg/a.py").write_text(
        "from .b import B\nfrom .b import B as alias\n"
        "def A(value):\n # NOTE: structural rationale\n B(); B()\n alias()\n"
        "def shadow(B):\n B()\n"
    )
    counts = counted(monkeypatch)
    captured = {}
    real = CodeGraph._extract

    def capture(self, tree, source, rel, lang):
        outputs = real(self, tree, source, rel, lang)
        captured[rel] = copy.deepcopy(
            {
                "symbols": [asdict(x) for x in outputs[0]],
                "refs": [asdict(x) for x in outputs[1]],
                "assignments": outputs[2],
                "rationale": [asdict(x) for x in outputs[3]],
                "records": self._canonical_file_records,
                "imports": self._canonical_imports,
                "bindings": self._canonical_bindings,
                "file_entity": self._canonical_file_entity,
            }
        )
        return outputs

    monkeypatch.setattr(CodeGraph, "_extract", capture)
    graph = CodeGraph(repo)
    graph.build()
    first = graph.evidence_records(), graph.observation_records(), graph.query("callers_of:B")
    artifact = tmp_path / "cold.jsonl"
    graph.export_jsonl(artifact)
    counts.update(parser=0, extract=0)
    stats = graph.build()
    assert stats["extraction_cache"]["disk_hits"] == 3
    assert counts == {"parser": 0, "extract": 0}
    assert (
        graph.evidence_records(),
        graph.observation_records(),
        graph.query("callers_of:B"),
    ) == first
    warm = tmp_path / "warm.jsonl"
    graph.export_jsonl(warm)
    assert warm.read_bytes() == artifact.read_bytes()
    # The packet stores every raw field, not just the public query projection.
    import json

    for entry in (graph.atlas_dir / "extraction-cache/v1").glob("*/*.json"):
        packet = json.loads(entry.read_text())["packet"]
        path = next(r["path"] for r in packet["records"] if r["type"] == "entity")
        raw = captured[path]
        for key in ("symbols", "refs", "rationale", "imports", "file_entity"):
            assert packet[key] == raw[key]
        assert packet["assignments"] == [list(x) for x in raw["assignments"]]
        assert packet["records"] == list({x["id"]: x for x in raw["records"]}.values())
        assert {tuple(x["fact"]): tuple(x["links"]) for x in packet["bindings"]} == raw["bindings"]
    # A forced cache miss produces identical bytes and all actual parsers run.
    for entry in (graph.atlas_dir / "extraction-cache/v1").glob("*/*.json"):
        entry.unlink()
    counts.update(parser=0, extract=0)
    graph.build()
    graph.export_jsonl(warm)
    assert counts == {"parser": 3, "extract": 3}
    assert warm.read_bytes() == artifact.read_bytes()
    graph.close()
