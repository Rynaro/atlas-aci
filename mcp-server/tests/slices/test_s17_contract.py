"""Six frozen retrieval criteria through real production dispatch/parser/SQLite."""

import asyncio
import hashlib
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.protocol.results import result_bytes
from atlas_aci.query.retrieval import retrieve
from atlas_aci.server import build_server_state, call_tool_result


def setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    files = {
        "a.py": "class Outer:\n def Alpha(self):\n  pass\n",
        "b.py": "def Alpha():\n pass\n",
        "excluded/utility.py": "def Utility():\n pass\n# " + ("task phrase " * 100) + "\n",
        "allowed/phrase.py": "def Allowed():\n pass\n# nightingale\n# task phrase café\n",
        "allowedness/other.py": "def Other():\n pass\n# " + ("task phrase " * 20) + "\n",
    }
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    writer = CodeGraph(repo)
    writer.build()
    config = Config(repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)
    return writer, config, enforcement, memex, factory


async def call(state, text, **options):
    _writer, config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", "op": "retrieve", "text": text, **options},
        config,
        enforcement,
        memex,
        factory,
    )


def close(state):
    state[0].close()
    state[-1].close()


def test_exact_identity_first(tmp_path):
    state = setup(tmp_path)

    async def check():
        result = await call(state, "Alpha")
        assert not result.isError, result
        choices = [
            c
            for c in result.structuredContent["data"]["candidates"]
            if c["match_tier"] == "exact_name"
        ]
        assert len(choices) == 2
        chosen = choices[1]
        qualified = await call(state, chosen["qualified_label"], max_candidates=1)
        assert not qualified.isError, qualified
        first = qualified.structuredContent["data"]["candidates"][0]
        assert (
            first["match_tier"] == "exact_qualified_label"
            and first["candidate_id"] == chosen["candidate_id"]
        )
        exact = await call(state, chosen["candidate_id"], max_candidates=1)
        assert (
            not exact.isError
            and exact.structuredContent["data"]["candidates"][0]["candidate_id"]
            == chosen["candidate_id"]
        )
        assert exact.structuredContent["data"]["candidates"][0]["match_tier"] == "exact_entity_id"

    try:
        asyncio.run(check())
    finally:
        close(state)


def test_lexical_candidate(tmp_path):
    state = setup(tmp_path)
    raw = (state[0].repo / "allowed/phrase.py").read_bytes()

    async def check():
        result = await call(state, "nightingale task")
        assert not result.isError, result
        candidates = result.structuredContent["data"]["candidates"]
        assert len(candidates) == 1
        candidate = candidates[0]
        source = candidate["source"]
        assert candidate["kind"] == "source_file" and candidate["path"] == "allowed/phrase.py"
        assert candidate["entity"] is candidate["occurrence"] is candidate["evidence"] is None
        assert source["granularity"] == "file" and source["start_byte"] == 0
        assert (
            source["end_byte"] == source["source_size"] == len(raw)
            and source["source_sha256"] == hashlib.sha256(raw).hexdigest()
        )
        assert not any("uri" in key for key in candidate)
        (state[0].repo / "allowed/phrase.py").write_text("not the indexed text")
        with state[-1].reader_lease() as reader:
            assert reader.read_source(candidate["path"], source["source_sha256"]) == raw

    try:
        asyncio.run(check())
    finally:
        close(state)


def test_search_generation(tmp_path):
    state = setup(tmp_path)

    async def check():
        with state[-1].reader_lease() as held:
            first = await retrieve(held, text="nightingale task")
            old_id = held.generation_id
            (state[0].repo / "allowed/phrase.py").write_text(
                "def Fresh():\n pass\n# new unique generation phrase\n"
            )
            (state[0].repo / "excluded/utility.py").unlink()
            state[0].build()
            again = await retrieve(held, text="nightingale task")
            assert again["candidates"] == first["candidates"]
            new = await call(state, "new unique generation phrase")
            assert not new.isError, new
            assert new.structuredContent["generation_id"] != old_id
            assert all(
                c["generation_id"] == new.structuredContent["generation_id"]
                for c in new.structuredContent["data"]["candidates"]
            )
            absent = await call(state, "nightingale task")
            assert not absent.isError and absent.structuredContent["data"]["candidates"] == []
            mismatch = await call(state, "Fresh", expected_generation_id=old_id)
            assert (
                mismatch.isError
                and mismatch.structuredContent["error"]["code"] == "SNAPSHOT_MISMATCH"
            )

    try:
        asyncio.run(check())
    finally:
        close(state)


def test_no_match(tmp_path):
    state = setup(tmp_path)

    async def check():
        absent = await call(state, "qzxv_absolutely_absent_phrase")
        assert not absent.isError, absent
        assert (
            absent.structuredContent["data"]["candidates"] == []
            and absent.structuredContent["data"]["diagnostic_reason"] == "no_match"
        )
        malformed = await call(state, "Alpha", max_vm_operations=True)
        assert malformed.isError and malformed.structuredContent["error"]["code"] == "INVALID_INPUT"
        budget = await call(state, "Alpha", max_vm_operations=0)
        assert (
            budget.isError
            and budget.structuredContent["error"]["code"] == "RETRIEVAL_BUDGET_EXCEEDED"
        )
        normal = await call(state, "Alpha")
        assert not normal.isError and normal.structuredContent["data"]["candidates"]

    try:
        asyncio.run(check())
    finally:
        close(state)


def test_search_filter(tmp_path):
    state = setup(tmp_path)

    async def check():
        all_matches = await call(state, "task phrase", max_candidates=1)
        assert not all_matches.isError, all_matches
        assert all_matches.structuredContent["data"]["candidates"][0]["path"].startswith(
            "excluded/"
        )
        selected = await call(state, "task phrase", max_candidates=1, path_prefixes=["allowed"])
        assert not selected.isError, selected
        assert [c["path"] for c in selected.structuredContent["data"]["candidates"]] == [
            "allowed/phrase.py"
        ]
        wrong = await call(state, "Alpha", repository=str(Path("/different/repository")))
        assert not wrong.isError and not wrong.structuredContent["data"]["candidates"]

    try:
        asyncio.run(check())
    finally:
        close(state)


def test_retrieval_replay(tmp_path):
    state = setup(tmp_path)

    async def check():
        results = [await call(state, "Alpha") for _ in range(3)]
        assert all(not r.isError for r in results), results
        candidates = [r.structuredContent["data"]["candidates"] for r in results]
        assert candidates[0] == candidates[1] == candidates[2]
        assert len({r.structuredContent["generation_id"] for r in results}) == 1
        assert all(len(result_bytes(r)) <= state[1].max_response_bytes for r in results)

    try:
        asyncio.run(check())
    finally:
        close(state)
