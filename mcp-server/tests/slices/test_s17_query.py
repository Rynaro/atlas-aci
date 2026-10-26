"""Actual SQL precedence/filter/ranking/progress/error controls before API wiring."""

import asyncio

import pytest

from atlas_aci.codegraph import CodeGraph
from atlas_aci.enforcement import ToolError
from atlas_aci.indexing.retrieval import qualified_label
from atlas_aci.query.retrieval import Work, retrieve


def graph(tmp_path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    g = CodeGraph(repo)
    g.build()
    return g


def test_exact_id_qualified_path_name_precedence(tmp_path):
    g = graph(
        tmp_path,
        {
            "a.py": "class Outer:\n def Alpha(self):\n  pass\n",
            "b.py": "def Alpha():\n pass\n# Alpha Alpha Alpha\n",
        },
    )
    try:
        with g.reader_lease() as reader:
            definitions = reader.search_symbol("Alpha")["definitions"]
            assert len(definitions) == 2
            import json

            entity = json.loads(
                reader.db.execute(
                    "SELECT payload FROM entities WHERE json_extract(payload,'$.name')='Alpha' "
                    "ORDER BY id LIMIT 1"
                ).fetchone()[0]
            )
            for text, tier in [
                (entity["id"], "exact_entity_id"),
                (qualified_label(entity), "exact_qualified_label"),
                (entity["path"], "exact_path"),
                ("Alpha", "exact_name"),
            ]:
                data = asyncio.run(retrieve(reader, text=text, max_candidates=1))
                assert len(data["candidates"]) == 1 and data["candidates"][0]["match_tier"] == tier
                if tier in {"exact_entity_id", "exact_qualified_label"}:
                    assert data["candidates"][0]["entity"]["id"] == entity["id"]
    finally:
        g.close()


def test_filter_precedes_rank_and_cap_component_boundary(tmp_path):
    g = graph(
        tmp_path,
        {
            "excluded/a.py": "def Utility():\n pass\n# " + ("task phrase " * 100) + "\n",
            "allowed/a.py": "def Allowed():\n pass\n# task phrase\n",
            "allowedness/a.py": "def Other():\n pass\n# " + ("task phrase " * 20) + "\n",
        },
    )
    try:
        with g.reader_lease() as reader:
            first = asyncio.run(retrieve(reader, text="task phrase", max_candidates=1))
            assert first["candidates"][0]["path"].startswith("excluded/")
            filtered = asyncio.run(
                retrieve(reader, text="task phrase", path_prefixes=["allowed"], max_candidates=1)
            )
            assert [c["path"] for c in filtered["candidates"]] == ["allowed/a.py"]
            mismatch = asyncio.run(
                retrieve(reader, text="Allowed", repository="/another/repository")
            )
            assert (
                mismatch["candidates"] == []
                and mismatch["diagnostic_reason"] == "filtered_repository"
            )
    finally:
        g.close()


def test_lexical_ties_replay_and_literal_operators(tmp_path):
    g = graph(
        tmp_path,
        {
            "a.py": "def One():\n pass\n# same phrase\n",
            "b.py": "def Two():\n pass\n# same phrase\n",
        },
    )
    try:
        with g.reader_lease() as reader:
            one = asyncio.run(retrieve(reader, text="same phrase"))
            two = asyncio.run(retrieve(reader, text="same phrase"))
            assert one["candidates"] == two["candidates"] and len(one["candidates"]) == 2
            assert len({c["lexical_score"] for c in one["candidates"]}) == 1
            assert [c["candidate_id"] for c in one["candidates"]] == sorted(
                c["candidate_id"] for c in one["candidates"]
            )
            literal = asyncio.run(retrieve(reader, text='" OR missing* NEAR('))
            assert literal["candidates"] == []
            empty = asyncio.run(retrieve(reader, text="!!!"))
            assert empty["candidates"] == [] and empty["diagnostic_reason"] == "tokenless"
    finally:
        g.close()


def test_actual_vm_interrupt_and_handler_cleanup(tmp_path):
    g = graph(tmp_path, {"a.py": "def A():\n pass\n"})
    try:
        with g.reader_lease() as reader:
            work = Work(reader, {"max_vm_operations": 300, "deadline_ms": 2000})
            with pytest.raises(ToolError, match="allowance"), work:
                work.rows(
                    "WITH RECURSIVE t(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM t WHERE x<100000) "
                    "SELECT sum(x) FROM t",
                    (),
                    "lexical",
                    1,
                )
            assert work.counts["interrupted_sql_queries"] == 1
            assert work.counts["vm_operations_reserved"] <= 300
            assert reader.db.execute("SELECT 1").fetchone()[0] == 1
            with (
                pytest.raises(ToolError, match="index operation"),
                Work(reader, {"max_vm_operations": 100000, "deadline_ms": 2000}) as failing,
            ):
                failing.rows(
                    "SELECT candidate_id FROM retrieval_fts WHERE retrieval_fts MATCH ?",
                    ('"unterminated',),
                    "lexical",
                    2,
                )
            assert reader.db.execute("SELECT 1").fetchone()[0] == 1
            with pytest.raises(ToolError, match="allowance"):
                asyncio.run(retrieve(reader, text="A", max_vm_operations=0))
            assert asyncio.run(retrieve(reader, text="A"))["candidates"]
    finally:
        g.close()


@pytest.mark.parametrize(
    "change",
    [
        {"text": "\ud800"},
        {"text": "\x00"},
        {"text": "A", "path_prefixes": ["../escape"]},
        {"text": "A", "max_vm_operations": True},
        {"text": "A", "deadline_ms": 1.0},
    ],
)
def test_invalid_query_is_distinct_from_empty(tmp_path, change):
    g = graph(tmp_path, {"a.py": "def A():\n pass\n"})
    try:
        with g.reader_lease() as reader, pytest.raises(ToolError, match=r"Invalid|Query"):
            asyncio.run(retrieve(reader, **change))
    finally:
        g.close()
