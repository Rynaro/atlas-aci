"""ACI-S14: measured TSX structure; JSX tags are never executable calls."""

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from atlas_aci.codegraph import CodeGraph

FIXTURE = Path(__file__).parents[1] / "fixtures/s14/app"
HEALTHY = [
    "anonymous.tsx",
    "components.tsx",
    "default-identifier.tsx",
    "good.tsx",
    "user.tsx",
]


def workspace(tmp_path, *, broken=False, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    for file in HEALTHY + (["broken.tsx"] if broken else []):
        shutil.copy(FIXTURE / file, repo / file)
    return repo


def build(repo, **kwargs):
    graph = CodeGraph(repo)
    stats = graph.build(**kwargs)
    return graph, stats


def rows(graph, sql, *args):
    with graph.reader_lease() as lease:
        return [dict(row) for row in lease.db.execute(sql, args)]


def snippet(repo, occurrence):
    source = (repo / occurrence["path"]).read_bytes()
    assert hashlib.sha256(source).hexdigest() == occurrence["source_sha256"]
    return source[occurrence["start_byte"] : occurrence["end_byte"]].decode()


def by_id(records):
    return {r["id"]: r for r in records}


def occurrence_of(records, assertion):
    index = by_id(records)
    return index[index[assertion["evidence_id"]]["occurrence_id"]]


def test_tsx_definitions(tmp_path):
    repo = workspace(tmp_path)
    graph, stats = build(repo)
    assert stats["extraction_state"] == "complete" and stats["files_indexed"] == 5
    expected = {
        ("Header", "function"): (8, 16, "function Header(props: Props) {"),
        ("Panel", "class"): (18, 25, "class Panel extends React.Component {"),
        ("render", "method"): (19, 21, "render() {"),
        ("helper", "method"): (22, 24, "helper() {"),
        ("Special", "class"): (27, 27, "class Special extends Panel {}"),
        ("Badge", "function"): (29, 37, "Badge = () => {"),
        ("Inner", "function"): (39, 41, "Inner = function () {"),
        ("renderLabel", "function"): (43, 45, "function renderLabel() {"),
        ("makeWidget", "function"): (47, 49, "function makeWidget() {"),
    }
    symbols = {
        (r["name"], r["kind"]): r
        for r in rows(graph, "SELECT * FROM symbols WHERE path='components.tsx'")
    }
    assert set(symbols) == set(expected)
    records = graph.evidence_records()
    source = (repo / "components.tsx").read_bytes()
    lines = source.decode().split("\n")
    offsets = [sum(len(line) + 1 for line in lines[:n]) for n in range(len(lines))]
    for key, (first, last, head) in expected.items():
        symbol = symbols[key]
        assert (symbol["line_start"], symbol["line_end"], symbol["lang"]) == (first, last, "tsx")
        # Exact line spans: the head text starts on the first line, a closing
        # token ends the last line.
        assert head in lines[first - 1]
        definitions = [
            r
            for r in records
            if r["type"] == "occurrence"
            and r["entity_id"] == symbol["entity_id"]
            and r["kind"] == "definition"
        ]
        assert len(definitions) == 1
        occurrence = definitions[0]
        assert occurrence["role"] == "definition:def." + key[1]
        assert (occurrence["line"], occurrence["end_line"]) == (first, last)
        # Exact byte span of the declaration node itself (not `export`/`const`/`;`).
        text = snippet(repo, occurrence)
        assert occurrence["start_byte"] == offsets[first - 1] + lines[first - 1].index(head)
        assert occurrence["end_byte"] == offsets[last - 1] + len(lines[last - 1].rstrip(";"))
        assert text.startswith(head) and text.endswith("}") and text.count("\n") == last - first
        entity = by_id(records)[symbol["entity_id"]]
        assert (entity["path"], entity["language"], entity["kind"]) == (
            "components.tsx",
            "tsx",
            key[1],
        )
    # Method ownership is the class component; arrow/function-expression
    # declarators are plain functions, never methods.
    entities = by_id(records)
    assert [o["name"] for o in entities[symbols[("render", "method")]["entity_id"]]["owners"]] == [
        "Panel"
    ]
    assert entities[symbols[("Badge", "function")]["entity_id"]]["owners"] == []
    # Export status is recorded syntactically on the declaration binding.
    status = {
        key: json.loads(symbol["binding"])["export_status"] for key, symbol in symbols.items()
    }
    assert status[("Header", "function")] == "named"
    assert status[("Panel", "class")] == "named"
    assert status[("Badge", "function")] == "named"
    assert status[("Inner", "function")] is None  # `export { Inner }` is a reported gap
    assert status[("renderLabel", "function")] is None
    app = rows(graph, "SELECT binding FROM symbols WHERE name='App'")
    assert json.loads(app[0]["binding"])["export_status"] == "default"
    tool = graph.search_symbol("Badge")["definitions"]
    assert [(d["path"], d["kind"], d["line_start"], d["line_end"]) for d in tool] == [
        ("components.tsx", "function", 29, 37)
    ]
    graph.close()


def test_tsx_imports(tmp_path):
    repo = workspace(tmp_path)
    graph, _ = build(repo)
    records = graph.evidence_records()
    imports = rows(graph, "SELECT * FROM import_bindings ORDER BY path, byte_start, local_name")
    by_local = {(r["path"], r["local_name"]): r for r in imports}
    # AC-S14-002: the alias is preserved, with imported name and module source.
    alias = by_local[("components.tsx", "useLocalState")]
    description = json.loads(alias["description"])
    assert description == {
        "name": "useState",
        "local_name": "useLocalState",
        "source": "react",
        "kind": "named",
        "type_only": False,
        "reason": "tsx_module_resolution_unavailable",
    }
    index = by_id(records)
    occurrence = index[alias["occurrence_id"]]
    assert occurrence["role"] == "import:useState" and occurrence["kind"] == "reference"
    assert occurrence["line"] == 1 and snippet(repo, occurrence) == "useState"
    assert alias["source_entity_id"] == occurrence["entity_id"]
    assertion = index[alias["assertion_id"]]
    assert (assertion["relation"], assertion["resolution"], assertion["raw_target"]) == (
        "import",
        "UNRESOLVED",
        "useState",
    )
    assert assertion["unresolved_reason"] == "tsx_module_resolution_unavailable"
    assert assertion["target_entity_id"] is None and assertion["candidate_ids"] == []

    def shape(path, local):
        d = json.loads(by_local[(path, local)]["description"])
        return d["name"], d["kind"], d["type_only"], d["source"]

    assert shape("components.tsx", "React") == ("default", "default", False, "react")
    assert shape("components.tsx", "FC") == ("FC", "named", True, "react")
    assert shape("components.tsx", "Plain") == ("Plain", "named", False, "react")
    assert shape("components.tsx", "Icons") == ("*", "namespace", False, "./icons")
    assert shape("components.tsx", "Props") == ("Props", "named", True, "./types")
    assert shape("components.tsx", "TypeDefault") == ("default", "default", True, "./type-default")
    assert shape("user.tsx", "Default") == ("default", "default", False, "./default")
    assert shape("user.tsx", "AppHeader") == ("Header", "named", False, "./components")
    assert shape("user.tsx", "Panel") == ("Panel", "named", False, "./components")
    # Side-effect imports introduce no binding.
    assert len([r for r in imports if r["path"] == "components.tsx"]) == 7
    assert len(imports) == 10
    # Occurrence node: specifier name, or the default/namespace identifier.
    texts = {
        (r["path"], r["local_name"]): snippet(repo, index[r["occurrence_id"]]) for r in imports
    }
    assert texts[("user.tsx", "AppHeader")] == "Header"
    assert texts[("components.tsx", "Icons")] == "Icons"
    assert texts[("components.tsx", "React")] == "React"
    # Every import is UNRESOLVED, never an invented target.
    asserted = [r for r in records if r["type"] == "assertion" and r["relation"] == "import"]
    assert len(asserted) == 10
    assert {(r["resolution"], r["unresolved_reason"]) for r in asserted} == {
        ("UNRESOLVED", "tsx_module_resolution_unavailable")
    }
    # A call through an imported binding is not guessed either.
    call = next(r for r in rows(graph, "SELECT * FROM refs") if r["callee_name"] == "useLocalState")
    assert call["unresolved_reason"] == "tsx_module_resolution_unavailable"
    assert not [
        r for r in rows(graph, "SELECT * FROM edges") if r["callee_name"] == "useLocalState"
    ]
    graph.close()


def test_jsx_not_fabricated_call(tmp_path):
    repo = workspace(tmp_path)
    graph, _ = build(repo)
    records = graph.evidence_records()
    index = by_id(records)
    jsx = [r for r in records if r["type"] == "assertion" and r["relation"] == "jsx"]
    # Value tags only: no intrinsic (`header`, `span`, `div`, `my-el`, `svg:rect`)
    # and no fragment is recorded.
    assert Counter(r["raw_target"] for r in jsx) == Counter(
        {
            "Icons.Logo": 1,
            "Badge": 1,
            "Header": 2,
            "AppHeader": 1,
            "Panel": 1,
            "Default": 1,
            "Missing": 1,
        }
    )
    for assertion in jsx:
        assert assertion["resolution"] == "UNRESOLVED"
        assert assertion["unresolved_reason"] == "jsx_semantic_binding_unavailable"
        assert assertion["target_entity_id"] is None and assertion["candidate_ids"] == []
        occurrence = occurrence_of(records, assertion)
        assert occurrence["role"] == "reference:jsx" and occurrence["kind"] == "reference"
        assert snippet(repo, occurrence) == assertion["raw_target"]
    # No call/construct assertion was ever produced from a JSX occurrence.
    for assertion in records:
        if assertion["type"] == "assertion" and assertion["relation"] != "jsx":
            assert occurrence_of(records, assertion)["role"] != "reference:jsx"
    assert not [
        r
        for r in records
        if r["type"] == "assertion"
        and r["resolution"] in {"EXTRACTED", "INFERRED"}
        and occurrence_of(records, r)["role"] == "reference:jsx"
    ]
    # Same-file (Header, Badge) and imported (AppHeader, Panel, Default) components:
    # zero edges and empty caller queries.
    edges = rows(graph, "SELECT * FROM edges")
    assert {(e["relation"], e["callee_name"]) for e in edges} == {
        ("call", "makeWidget"),
        ("superclass", "Panel"),
        ("call", "renderLabel"),
        ("construct", "Panel"),
    }
    assert not [e for e in edges if e["relation"] == "jsx"]
    assert not [e for e in edges if e["source_path"] == "user.tsx"]
    jsx_positions = {
        (r["path"], r["line"], r["col"])
        for r in rows(graph, "SELECT * FROM refs WHERE relation='jsx'")
    }
    assert len(jsx_positions) == 8
    assert not [
        e for e in edges if (e["source_path"], e["source_line"], e["source_col"]) in jsx_positions
    ]
    for name in ("Header", "Badge", "AppHeader", "Default"):
        result = graph.query("callers_of:" + name)
        assert result["edges"] == [] and result["unresolved_refs"] == 0
    # The raw reference rows carry the explicit relation and reason.
    refs = rows(graph, "SELECT * FROM refs WHERE relation='jsx'")
    assert {r["unresolved_reason"] for r in refs} == {"jsx_semantic_binding_unavailable"}
    assert {r["qualified"] for r in refs} == {0}
    qualifier = {r["callee_name"]: r["qualifier_name"] for r in refs}
    assert qualifier["Icons.Logo"] == "Icons" and qualifier["Badge"] is None
    # A search of a component surfaces JSX uses under their own relation only.
    uses = graph.search_symbol("Header")["references"]
    assert {r["relation"] for r in uses} == {"jsx"}
    assert index  # records are real, not empty
    graph.close()


def test_tsx_diagnostic(tmp_path):
    repo = workspace(tmp_path, broken=True)
    graph, stats = build(repo)
    assert stats["extraction_state"] == "partial" and stats["files_failed"] == 1
    status = graph.status()
    files = {row["path"]: row for row in status["completeness"]["files"]}
    broken = files["broken.tsx"]
    assert (broken["extraction_state"], broken["reason"]) == ("failed", "parse_error")
    # `export function Broken( {` is line 5; the unrecoverable region ends at line 7.
    source = (repo / "broken.tsx").read_text().split("\n")
    assert source[4].startswith("export function Broken(")
    assert broken["location"]["line"] == 5 and broken["location"]["end_line"] == 7
    assert (
        broken["failed_source_sha256"]
        == hashlib.sha256((repo / "broken.tsx").read_bytes()).hexdigest()
    )
    assert broken["retained_facts"] is False
    assert status["completeness"]["state"] == "partial"
    # A sibling good file is still indexed, with its facts.
    assert files["good.tsx"]["extraction_state"] == "indexed"
    assert [d["path"] for d in graph.search_symbol("Sibling")["definitions"]] == ["good.tsx"]
    # Nothing from the failed file leaks into any active table or record.
    assert not rows(graph, "SELECT 1 FROM symbols WHERE path='broken.tsx'")
    assert not rows(graph, "SELECT 1 FROM refs WHERE path='broken.tsx'")
    assert not graph.search_symbol("Healthy")["definitions"]
    assert not graph.search_symbol("Broken")["definitions"]
    assert not [r for r in graph.evidence_records() if r.get("path") == "broken.tsx"]
    graph.close()


def canonical(graph):
    return graph.evidence_records(), graph.observation_records()


def test_tsx_incremental(tmp_path):
    repo = workspace(tmp_path)
    graph, _ = build(repo)
    before = graph.evidence_records()
    components = repo / "components.tsx"
    # Rename a declaration and its same-file uses; user.tsx (a cross-file user
    # importing `Header`) is left unchanged.
    components.write_text(components.read_text().replace("Header", "Heading"))
    stats = graph.build(since="incremental")
    assert stats["files_skipped"] == 4 and stats["files_indexed"] == 1
    assert [d["name"] for d in graph.search_symbol("Heading")["definitions"]] == ["Heading"]
    assert not graph.search_symbol("Header")["definitions"]
    incremental = canonical(graph)
    # The cross-file user's own facts are untouched by the rename.
    assert [r for r in before if r.get("path") == "user.tsx"] == [
        r for r in incremental[0] if r.get("path") == "user.tsx"
    ]
    fresh_repo = workspace(tmp_path, name="fresh")
    (fresh_repo / "components.tsx").write_text(components.read_text())
    fresh, fresh_stats = build(fresh_repo)
    assert fresh_stats["files_indexed"] == 5
    assert incremental == canonical(fresh)
    # The cross-file import still names the old export, unresolved, never rebound.
    imports = rows(graph, "SELECT description FROM import_bindings WHERE local_name='AppHeader'")
    assert json.loads(imports[0]["description"])["name"] == "Header"
    asserts = [
        r
        for r in graph.evidence_records()
        if r["type"] == "assertion" and r["raw_target"] == "Header" and r["relation"] == "import"
    ]
    assert [a["resolution"] for a in asserts] == ["UNRESOLVED"]
    graph.close()
    fresh.close()


def test_tsx_replay(tmp_path):
    repo = workspace(tmp_path)
    cold, cold_stats = build(repo)
    assert cold_stats["extraction_cache"]["disk_hits"] == 0
    assert cold_stats["extraction_cache"]["misses"] == 5
    first = canonical(cold)
    artifact = tmp_path / "first.jsonl"
    cold.export_jsonl(artifact)
    cold.close()
    # Second build of unchanged source: every file is a warm-cache hit.
    warm = CodeGraph(repo)
    stats = warm.build()
    assert stats["extraction_cache"]["disk_hits"] == 5
    assert stats["extraction_cache"]["misses"] == 0
    assert stats["extraction_cache"]["parser_invocations"] == 0
    assert canonical(warm) == first
    replay = tmp_path / "replay.jsonl"
    warm.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    stats = warm.build(since="incremental")
    assert stats["binding_resolution"]["reused_units"] == 5
    assert canonical(warm) == first
    # Wipe the cache: the cold re-extraction equals the warm result exactly.
    for entry in (warm.atlas_dir / "extraction-cache/v1").glob("*/*.json"):
        packet = json.loads(entry.read_text())["packet"]
        assert any(row["lang"] == "tsx" for key in ("symbols", "refs") for row in packet[key])
        entry.unlink()
    stats = warm.build()
    assert stats["extraction_cache"]["disk_hits"] == 0
    assert stats["extraction_cache"]["misses"] == 5
    assert canonical(warm) == first
    warm.export_jsonl(replay)
    assert replay.read_bytes() == artifact.read_bytes()
    warm.close()
