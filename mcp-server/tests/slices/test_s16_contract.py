"""ACI-S16: official scip-typescript 0.4.0 import, real artifact and real importer."""

import dataclasses
import hashlib
import json

import pytest
from test_s16_support import (
    ARTIFACT_SHA256,
    INDEX,
    LABELS,
    MANIFEST,
    SCHEMA_SHA256,
    built,
    craft,
    current_id,
    imported,
    manifest_for,
    occurrence_index,
    official,
    published_bytes,
    records,
    rows,
    scip_records,
    scip_view,
    snippet,
    stage_leftovers,
    workspace,
)

from atlas_aci.providers import scip
from atlas_aci.storage import evidence as ev


def untouched(graph, before, generation):
    assert published_bytes(graph) == before
    assert current_id(graph) == generation
    assert stage_leftovers(graph) == []


def snapshot_of(graph):
    return published_bytes(graph), current_id(graph)


def test_official_artifact(tmp_path):
    assert hashlib.sha256(INDEX.read_bytes()).hexdigest() == ARTIFACT_SHA256
    assert LABELS["artifact_sha256"] == ARTIFACT_SHA256
    repo, graph, result = imported(tmp_path)
    assert result["status"] == "imported"
    defs, refs = scip_view(graph)
    occurrences = occurrence_index(graph)

    def key(path, symbol, occurrence_id):
        item = occurrences[occurrence_id]
        return (path, symbol, item["start_byte"], item["end_byte"], snippet(repo, item))

    expected_defs = {
        (r["path"], r["symbol"], r["start_byte"], r["end_byte"], r["text"])
        for r in LABELS["definitions"]
    }
    expected_refs = {
        (r["path"], r["symbol"], r["start_byte"], r["end_byte"], r["text"])
        for r in LABELS["references"]
    }
    got_defs = [key(p, s, o) for p, s, o, _ in defs]
    got_refs = [key(p, s, o) for p, s, o, _ in refs]
    assert len(got_defs) == len(set(got_defs)) == LABELS["counts"]["definitions"] == 17
    assert len(got_refs) == len(set(got_refs)) == LABELS["counts"]["references"] == 24
    assert set(got_defs) == expected_defs
    assert set(got_refs) == expected_refs
    # Entity identity: kind and name per label, one entity per definition.
    by_symbol = {(p, s): row for p, s, _o, row in defs}
    for label in LABELS["definitions"]:
        row = by_symbol[(label["path"], label["symbol"])]
        assert (row["kind"], row["name"]) == ("scip_" + label["kind"], label["name"])
    # External symbols are unresolved; every other reference resolves to a defined entity.
    external = {r["symbol"] for r in LABELS["references"] if r["resolution"] == "external"}
    unresolved = {row["callee_name"] for _p, _s, _o, row in refs if row["unresolved_reason"]}
    assert unresolved == external and len(external) == 2
    assert all(
        row["unresolved_reason"] == "scip_external_symbol"
        for _p, _s, _o, row in refs
        if row["callee_name"] in external
    )
    assert (
        result["resolved_references"],
        result["unresolved_references"],
        result["ambiguous_references"],
    ) == (22, 2, 0)
    assert result["documents"] == LABELS["counts"]["documents"] == 3


def test_producer_identity(tmp_path):
    _repo, graph, result = imported(tmp_path)
    everything = records(graph)
    by_id = {r["id"]: r for r in everything}
    scip_only = scip_records(graph)
    proofs = [r for r in scip_only if r["type"] == "evidence"]
    assert len(proofs) == 41  # 17 definition + 24 reference occurrences, nothing else
    producers = {by_id[p["producer_id"]]["id"] for p in proofs}
    assert len(producers) == 1
    producer = by_id[next(iter(producers))]
    assert producer["identity_basis"] == "parsed"
    fingerprint = producer["fingerprint"]["scip"]
    assert fingerprint["tool"] == {"name": "scip-typescript", "version": "0.4.0"}
    assert fingerprint["schema_sha256"] == SCHEMA_SHA256
    assert fingerprint["index_sha256"] == ARTIFACT_SHA256
    assert fingerprint["manifest_sha256"] == hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert fingerprint["position_encoding"] == "utf-16"
    assert fingerprint["importer_sha256"] == scip.importer_sha256()
    # The generation names the same artifact; tree-sitter evidence keeps its own producer.
    with graph.reader_lease() as lease:
        inputs = json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )
    assert inputs["kind"] == "scip_import" and inputs["scip"]["index_sha256"] == ARTIFACT_SHA256
    assert inputs["scip"]["base_generation_id"] == result["base_generation_id"]
    structural = {
        p["producer_id"]
        for p in everything
        if p["type"] == "evidence" and p["id"] not in {x["id"] for x in proofs}
    }
    assert structural and all("scip" not in by_id[i]["fingerprint"] for i in structural)


def test_reference_not_call(tmp_path):
    _repo, graph = built(tmp_path)
    before_callers = graph.callers_of("saudação")
    before_god = graph.god_nodes()
    before_communities = graph.communities()
    before_edges = rows(graph, "SELECT * FROM edges ORDER BY id")
    graph.import_scip(INDEX, MANIFEST)
    # Official parse: occurrences with no role bits are the reference-only ones.
    parsed = official().Index()
    parsed.ParseFromString(INDEX.read_bytes())
    role_less = sum(1 for d in parsed.documents for o in d.occurrences if o.symbol_roles == 0)
    assert role_less == 24
    assertions = [r for r in scip_records(graph) if r["type"] == "assertion"]
    assert len(assertions) == role_less
    assert {a["relation"] for a in assertions} == {"reference"}
    assert {a["resolution"] for a in assertions} == {"EXTRACTED", "UNRESOLVED"}
    # The call site `saudação(visitor)` is preserved as a reference, not a call.
    call_site = [
        r
        for r in LABELS["references"]
        if r["path"] == "src/main.ts" and r["line"] == 4 and r["text"] == "saudação"
    ]
    assert len(call_site) == 1
    reference_rows = rows(
        graph, "SELECT * FROM refs WHERE relation='reference' AND path='src/main.ts'"
    )
    assert any(r["line"] == 4 for r in reference_rows)
    assert (
        rows(
            graph,
            "SELECT COUNT(*) AS n FROM refs WHERE relation IN ('call','construct') "
            "AND callee_name LIKE 'scip-typescript%'",
        )[0]["n"]
        == 0
    )
    # Structural facts and analytics are exactly what they were.
    assert graph.callers_of("saudação") == before_callers
    assert graph.god_nodes() == before_god
    assert graph.communities() == before_communities
    after_edges = rows(graph, "SELECT * FROM edges WHERE relation != 'reference' ORDER BY id")
    assert [{k: v for k, v in e.items() if k != "id"} for e in after_edges] == [
        {k: v for k, v in e.items() if k != "id"} for e in before_edges
    ]
    relations = {e["relation"] for e in rows(graph, "SELECT relation FROM edges")}
    assert "reference" in relations and "reference" not in ev.RELATIONS
    assert relations - {"reference"} == {e["relation"] for e in before_edges}


def test_source_mismatch(tmp_path):
    repo, graph = built(tmp_path)
    before, generation = snapshot_of(graph)
    # The producer-time manifest was made over the original bytes; the target changed.
    (repo / "src/greeting.ts").write_text(
        (repo / "src/greeting.ts").read_text(encoding="utf-8")
        + "// edited after the producer ran\n",
        encoding="utf-8",
    )
    graph.build()
    before, generation = snapshot_of(graph)
    with pytest.raises(ValueError, match="source_mismatch"):
        graph.import_scip(INDEX, MANIFEST)
    untouched(graph, before, generation)
    assert rows(graph, "SELECT COUNT(*) AS n FROM refs WHERE relation='reference'")[0]["n"] == 0
    # A manifest whose recorded hash disagrees with the snapshot is rejected the same way.
    _repo2, graph2 = built(tmp_path, "second")
    before2, generation2 = snapshot_of(graph2)
    forged = json.loads(MANIFEST.read_text())
    forged["documents"]["src/main.ts"] = "0" * 64
    path = tmp_path / "forged.json"
    path.write_text(ev.canonical(forged) + "\n")
    with pytest.raises(ValueError, match="source_mismatch"):
        graph2.import_scip(INDEX, path)
    untouched(graph2, before2, generation2)


MALFORMED = {
    "empty": (b"", None),
    "truncated_half": (
        INDEX.read_bytes()[: len(INDEX.read_bytes()) // 2],
        "truncated|length_beyond",
    ),
    "truncated_one_byte": (INDEX.read_bytes()[:-1], "truncated|length_beyond"),
    "garbage": (b"\xff" * 64, "invalid_tag|varint|wire_type"),
    "group_wire_type": (INDEX.read_bytes() + b"\x0b", "invalid_wire_type"),
    "field_zero": (INDEX.read_bytes() + b"\x00\x00", "invalid_tag"),
    "invalid_utf8_symbol": (b"\x12\x0a\x12\x08\x12\x02\xc3\x28\x1a\x02\x08\x01", None),
}


def test_malformed_scip(tmp_path):
    _repo, graph = built(tmp_path)
    before, generation = snapshot_of(graph)
    corpus = dict(MALFORMED)
    # Structurally decodable but impossible: a range with two elements; a column past its line.
    corpus["range_arity"] = (
        craft(
            tmp_path, lambda i: i.documents[0].occurrences[0].range.__delitem__(slice(1, None))
        ).read_bytes(),
        "range_arity",
    )

    def past_the_line(index):
        index.documents[0].occurrences[1].range[1:] = [5000, 5006]

    def past_the_document(index):
        index.documents[0].occurrences[1].range[:] = [99, 0, 99, 3]

    corpus["out_of_line"] = (
        craft(tmp_path, past_the_line, "ool.scip").read_bytes(),
        "position_out_of_line",
    )
    corpus["out_of_document"] = (
        craft(tmp_path, past_the_document, "ood.scip").read_bytes(),
        "position_out_of_line",
    )
    corpus["negative_range"] = (
        craft(
            tmp_path,
            lambda i: i.documents[0].occurrences[1].range.__setitem__(1, -1),
            "neg.scip",
        ).read_bytes(),
        "negative_value",
    )
    for name, (data, pattern) in corpus.items():
        artifact = tmp_path / f"{name}.scip"
        artifact.write_bytes(data)
        manifest = manifest_for(tmp_path, data, name=f"{name}.manifest.json")
        with pytest.raises(ValueError, match=pattern or ".") as excinfo:
            graph.import_scip(artifact, manifest)
        assert excinfo.value.args, name
        untouched(graph, before, generation)
    # Rejection is by decoder/mapper, not by the manifest: the valid artifact still imports.
    assert graph.import_scip(INDEX, MANIFEST)["status"] == "imported"
    assert stage_leftovers(graph) == []


def test_scip_reimport(tmp_path):
    repo, graph, first = imported(tmp_path)
    canonical = sorted(r["id"] for r in scip_records(graph))
    assertions = sorted(r["id"] for r in scip_records(graph) if r["type"] == "assertion")
    assert len(assertions) == 24
    # Same artifact onto the scip_import generation: a no-op reporting the same id.
    before, generation = snapshot_of(graph)
    again = graph.import_scip(INDEX, MANIFEST)
    assert again["status"] == "unchanged" and again["generation_id"] == first["generation_id"]
    untouched(graph, before, generation)
    # A different artifact is rejected until the graph is rebuilt from source.
    changed = craft(tmp_path, lambda i: i.documents[1].occurrences.pop(), "changed.scip")
    other = manifest_for(tmp_path, changed.read_bytes(), name="changed.manifest.json")
    with pytest.raises(ValueError, match="different SCIP import"):
        graph.import_scip(changed, other)
    untouched(graph, before, generation)
    # Rebuild from source, import again: the identical generation and assertion set.
    graph.build()
    assert current_id(graph) == first["base_generation_id"]
    second = graph.import_scip(INDEX, MANIFEST)
    assert second["status"] == "imported"
    assert second["generation_id"] == first["generation_id"]
    assert sorted(r["id"] for r in scip_records(graph)) == canonical
    assert sorted(r["id"] for r in scip_records(graph) if r["type"] == "assertion") == assertions
    # The same repository path in an independent graph root reaches the same identity.
    from atlas_aci.codegraph import CodeGraph

    root = tmp_path / "graphs"
    other_graph = CodeGraph(repo, graph_root=root)
    other_graph.build()
    third = other_graph.import_scip(INDEX, MANIFEST)
    assert third["generation_id"] == first["generation_id"]
    assert sorted(r["id"] for r in scip_records(other_graph)) == canonical


def test_missing_source_manifest(tmp_path):
    _repo, graph = built(tmp_path)
    before, generation = snapshot_of(graph)
    good = json.loads(MANIFEST.read_text())

    def write(name, text):
        target = tmp_path / name
        target.write_text(text) if isinstance(text, str) else target.write_bytes(text)
        return target

    incomplete = {
        **good,
        "documents": {k: v for k, v in good["documents"].items() if k != "src/main.ts"},
    }
    foreign = {**good, "index_sha256": "1" * 64}
    unknown_producer = {**good, "producer": {"name": "scip-java", "version": "0.4.0"}}
    wrong_version = {**good, "producer": {"name": "scip-typescript", "version": "9.9.9"}}
    other_producer_name = {**good, "producer": {"name": "scip-python", "version": "0.4.0"}}
    extra_key = {**good, "unexpected": 1}
    no_encoding = {k: v for k, v in good.items() if k != "position_encoding"}
    extra_document = {**good, "documents": {**good["documents"], "src/ghost.ts": "2" * 64}}
    cases = {
        "absent": (tmp_path / "does-not-exist.json", "manifest_unreadable"),
        "unparseable": (write("garbage.json", b"{not json"), "manifest_invalid"),
        "binary": (write("binary.json", b"\xff\xfe\x00"), "manifest_invalid"),
        "not_object": (write("list.json", "[]"), "manifest_invalid"),
        "duplicate_key": (write("dup.json", '{"format":"x","format":"y"}'), "manifest_invalid"),
        "extra_key": (write("extra.json", ev.canonical(extra_key)), "manifest_invalid"),
        "no_encoding": (write("noenc.json", ev.canonical(no_encoding)), "manifest_invalid"),
        "incomplete": (write("incomplete.json", ev.canonical(incomplete)), "document_set_mismatch"),
        "extra_document": (
            write("ghost.json", ev.canonical(extra_document)),
            "document_set_mismatch",
        ),
        "foreign": (write("foreign.json", ev.canonical(foreign)), "index_hash_mismatch"),
        "unknown_producer": (
            write("java.json", ev.canonical(unknown_producer)),
            "unknown_producer",
        ),
        "wrong_version": (
            write("version.json", ev.canonical(wrong_version)),
            "unknown_producer_version",
        ),
        "producer_name": (
            write("python.json", ev.canonical(other_producer_name)),
            "unknown_producer",
        ),
    }
    for name, (manifest, code) in cases.items():
        with pytest.raises(ValueError, match=code):
            graph.import_scip(INDEX, manifest)
        untouched(graph, before, generation)
        assert name
    # An index from an unallowed producer is rejected even with a perfect manifest.
    foreign_tool = craft(
        tmp_path, lambda i: setattr(i.metadata.tool_info, "name", "scip-java"), "java.scip"
    )
    with pytest.raises(ValueError, match="unknown_producer"):
        graph.import_scip(
            foreign_tool,
            manifest_for(
                tmp_path,
                foreign_tool.read_bytes(),
                name="java2.json",
                producer={"name": "scip-java", "version": "0.4.0"},
            ),
        )
    untouched(graph, before, generation)
    assert rows(graph, "SELECT COUNT(*) AS n FROM symbols WHERE kind LIKE 'scip%'")[0]["n"] == 0


def test_unicode_position_encoding(tmp_path):
    repo, graph, _ = imported(tmp_path)
    defs, refs = scip_view(graph)
    occurrences = occurrence_index(graph)
    imported_rows = {}
    for path, symbol, occurrence_id, row in [*defs, *refs]:
        item = occurrences[occurrence_id]
        imported_rows[(path, symbol, item["start_byte"])] = (item, row)
    unicode_rows = [r for r in [*LABELS["definitions"], *LABELS["references"]] if r["unicode"]]
    assert len(unicode_rows) == LABELS["counts"]["unicode_rows"] == 20
    for label in unicode_rows:
        item, _ = imported_rows[(label["path"], label["symbol"], label["start_byte"])]
        assert (item["start_byte"], item["end_byte"]) == (label["start_byte"], label["end_byte"])
        assert (item["line"], item["column"]) == (label["line"], label["utf8_byte_column"])
        assert snippet(repo, item) == label["text"]
    # Hand-checked rows: `saudação` follows an astral emoji on its line.
    row = next(r for r in LABELS["definitions"] if r["text"] == "saudação")
    assert (row["utf16_column"], row["utf8_byte_column"], row["codepoint_column"]) == (43, 45, 42)
    item, _ = imported_rows[(row["path"], row["symbol"], row["start_byte"])]
    assert item["column"] == 45
    # CRLF document: columns are measured from the line start, not the terminator.
    windows = next(r for r in LABELS["definitions"] if r["text"] == "ñandú")
    assert (windows["utf16_column"], windows["utf8_byte_column"]) == (43, 46)

    # Negative: the wrong encoding lands elsewhere (UTF-8 -> 43, UTF-32 -> 46, truth 45) ...
    source = scip.SourceText((repo / "src/greeting.ts").read_bytes(), "src/greeting.ts")
    assert source.locate(2, 43, "utf-16")[1] == 45
    assert source.locate(2, 43, "utf-8")[1] == 43
    assert source.locate(2, 43, "utf-32")[1] == 46
    assert source.locate(2, 42, "utf-32")[1] == 45  # 42 code points would be the real column
    # ... and the mechanical detector rejects an artifact imported under either encoding.
    reader_graph = graph
    from atlas_aci.codegraph import CodeGraph

    other = CodeGraph(repo, read_only=True)
    with other.reader_lease() as lease:
        submission = scip.load_submission(INDEX, MANIFEST)
        for wrong in ("utf-8", "utf-32"):
            forced = dataclasses.replace(
                submission,
                manifest=dataclasses.replace(submission.manifest, position_encoding=wrong),
            )
            with pytest.raises(
                ValueError, match=r"definition_text_mismatch|position_not_on_boundary"
            ):
                scip.plan_import(
                    forced,
                    current=lease._source_lease.current,
                    observations={
                        r["path"]: r
                        for r in lease._observation_records(lease.db, validate=False)
                        if r["type"] == "observation"
                    },
                    read_source=lease._source_lease.read,
                )
    del reader_graph
    # The declared encoding is never a free assertion: UTF-8/UTF-32 are not permitted for 0.4.0.
    for wrong in ("utf-8", "utf-32"):
        with pytest.raises(ValueError, match="encoding_not_permitted"):
            scip.build_manifest(repo, INDEX, wrong)
        manifest = manifest_for(
            tmp_path, INDEX.read_bytes(), name=f"{wrong}.json", position_encoding=wrong
        )
        fresh = workspace(tmp_path, "fresh-" + wrong)
        fresh_graph = CodeGraph(fresh)
        fresh_graph.build()
        before, generation = snapshot_of(fresh_graph)
        with pytest.raises(ValueError, match="encoding_not_permitted"):
            fresh_graph.import_scip(INDEX, manifest)
        untouched(fresh_graph, before, generation)
    # A document-level encoding that contradicts the manifest is rejected.
    conflicting = craft(
        tmp_path,
        lambda i: setattr(i.documents[0], "position_encoding", 1),
        "conflict.scip",
    )
    fresh = workspace(tmp_path, "conflict")
    conflict_graph = CodeGraph(fresh)
    conflict_graph.build()
    with pytest.raises(ValueError, match="encoding_conflict"):
        conflict_graph.import_scip(
            conflicting, manifest_for(tmp_path, conflicting.read_bytes(), name="conflict.json")
        )
    assert ev.relative_path("src/greeting.ts")
