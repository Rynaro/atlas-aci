"""ACI-S37: one scoped context envelope exported as offline HTML and canonical JSON.

Real envelopes come from the real indexer, the real MCP dispatch and the real lease service
(the S18 pattern, copied locally). Frozen envelopes and goldens live in
`tests/fixtures/evidence_report/`; they are regenerated only with

    python tests/slices/test_s37_evidence_report.py --regenerate

inside the Linux dev image (the repository path is fixed because snapshot ids bind it).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from html.parser import HTMLParser
from importlib import metadata, resources
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.protocol import graph
from atlas_aci.protocol.resources import next_uri, parse_uri
from atlas_aci.protocol.results import result_bytes
from atlas_aci.query.leases import digest
from atlas_aci.reports import evidence
from atlas_aci.server import build_server_state, call_tool_result
from atlas_aci.storage import evidence as ev

ROOT = Path(__file__).resolve().parents[3]
MCP = ROOT / "mcp-server"
FIXTURES = MCP / "tests/fixtures/evidence_report"
CORPUS = MCP / "tests/fixtures/s18/useful-spans.v1.json"
BASE_COMMIT = "7b009443492f0550400e1754b47066dc045b1af0"
FIXED_ROOT = Path("/tmp/atlas-s37-evidence")
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472

HOSTILE_SOURCE = (
    "def process_payment_audit():\n"
    '    """</pre><script>alert(1)</script>"><img src=x onerror=alert(2)> \u202e"""\n'
    "    return 1\n"
)
HOSTILE_STRINGS = (
    "</pre><script>alert(1)</script>",
    "<img src=x onerror=alert(2)>",
    "\u202e",
)
NON_UTF8_SOURCE = b"def process_payment_bin():\n    x = '\xff\xfe'\n    return x\n"
FILENAME_SOURCE = "def process_payment_fname():\n    return 1\n"
HOSTILE_FILENAME = 'app/x"><b>.py'
LABEL_TEXT = "process_payment <script>x</script>"

# Frozen fixtures: name -> (context arguments, repository state). Single page, so the
# resources are present and the cursor is null, except `truncated` (a real page cut).
FROZEN = {
    "inline": ({"text": "process_payment", "continuation": True}, "clean"),
    "path-references": (
        {
            "text": "process_payment",
            "continuation": True,
            "target": {"name": "validate_payment"},
            "source_policy": "references",
        },
        "clean",
    ),
    "truncated": (
        {"text": "process_payment", "continuation": True, "max_bytes": 40000},
        "clean",
    ),
    "hostile": ({"text": "process_payment_audit", "continuation": True}, "hostile"),
}

RECORD_KEYS = (
    "format",
    "request",
    "repository",
    "generation_id",
    "snapshot_id",
    "ranker",
    "tokenizer",
    "bundles",
    "effective_limits",
    "coverage",
    "retrieval",
    "frontiers",
    "work",
    "collection_stop",
    "selected_range",
    "selection",
    "source_resource_ids",
)


# ---- real envelope production (the S18 pattern) -----------------------------------------------


def build_repo(repo: Path) -> CodeGraph:
    corpus = json.loads(CORPUS.read_text())
    repo.mkdir(parents=True)
    for name, source in corpus["source_files"].items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    writer = CodeGraph(repo)
    writer.build()
    return writer


def add_hostile(repo: Path, writer: CodeGraph) -> None:
    (repo / "app/hostile.py").write_text(HOSTILE_SOURCE)
    (repo / "app/bin.py").write_bytes(NON_UTF8_SOURCE)
    (repo / HOSTILE_FILENAME).write_text(FILENAME_SOURCE)
    writer.build()


def made(result: Any) -> SimpleNamespace:
    text = result.content[0].text
    wire = json.loads(result_bytes(result)).get("structuredContent")
    return SimpleNamespace(
        result=result,
        text=text,
        raw=text.encode(),
        body=json.loads(text),
        structured=result.structuredContent,
        wire=wire,
    )


def request(**options: Any) -> dict[str, Any]:
    return {
        "schema_version": "atlas.graph.v1",
        "op": "context",
        "text": "process_payment",
        **options,
    }


def serve(repo: Path, base: Path, calls: list[Any]) -> list[Any]:
    """One `asyncio.run` for the whole call list, ending with `registry.aclose()`."""
    config = Config(repo=repo, memex_root=base / "memex", max_calls_per_minute=0)
    enforcement, memex, factory = build_server_state(config)

    async def run() -> list[Any]:
        results: list[Any] = []
        try:
            for call in calls:
                arguments = call(results) if callable(call) else call
                results.append(
                    await call_tool_result(
                        "graph_query",
                        arguments,
                        config,
                        enforcement,
                        memex,
                        factory,
                        protocol_version="2025-11-25",
                    )
                )
        finally:
            if factory._lease_registry is not None:
                await factory._lease_registry.aclose()
        return results

    try:
        return asyncio.run(run())
    finally:
        factory.close()


def produce(
    repo: Path, base: Path, *, expect_error: bool = False, **options: Any
) -> SimpleNamespace:
    (result,) = serve(repo, base, [request(**options)])
    assert bool(result.isError) is expect_error, result.content[0].text
    return made(result)


def produce_page_and_continue(repo: Path, base: Path) -> tuple[SimpleNamespace, SimpleNamespace]:
    def follow(results: list[Any]) -> dict[str, Any]:
        data = json.loads(results[0].content[0].text)["data"]
        return {
            "schema_version": "atlas.graph.v1",
            "op": "continue",
            "cursor": data["next_cursor"],
            "query_digest": data["query_digest"],
        }

    first, second = serve(repo, base, [request(continuation=True, max_bytes=40000), follow])
    assert not first.isError and not second.isError
    return made(first), made(second)


def produce_import(writer: CodeGraph, base: Path) -> SimpleNamespace:
    artifact = base / "portable.jsonl"
    writer.export_jsonl(artifact)
    destination = base / "destination"
    (destination / "app").mkdir(parents=True)
    (destination / "app/payments.py").write_text("def DestinationOnly():\n pass\n")
    imported = CodeGraph(destination)
    try:
        imported.import_jsonl(artifact)
        return produce(destination, base, continuation=True, max_bytes=40000)
    finally:
        imported.close()


def produce_impact(base: Path) -> SimpleNamespace:
    from test_s20_support import V1, V2, served_pair

    served = served_pair(base, V1, V2)
    try:
        result = served.call({})
        assert not result.isError, result.content[0].text
        return made(result)
    finally:
        served.close()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("s37")
    repo = base / "repo"
    writer = build_repo(repo)
    w = SimpleNamespace(base=base, envs={})
    try:
        e = w.envs
        e["E_inline"] = produce(repo, base, continuation=True)
        e["E_default"] = produce(repo, base)
        e["E_refs"] = produce(repo, base, continuation=True, source_policy="references")
        e["E_path"] = produce(repo, base, continuation=True, target={"name": "validate_payment"})
        e["E_trunc_budget"] = produce(repo, base, max_bytes=12000)
        e["E_trunc_page"], w.continue_page = produce_page_and_continue(repo, base)
        e["E_import"] = produce_import(writer, base)
        w.budget_error = produce(repo, base, expect_error=True, max_bytes=1)
        add_hostile(repo, writer)
        e["E_hostile"] = produce(repo, base, continuation=True, text="process_payment_audit")
        e["E_label"] = produce(repo, base, continuation=True, text=LABEL_TEXT)
        e["E_b64"] = produce(repo, base, continuation=True, text="process_payment_bin")
        e["E_filename"] = produce(repo, base, continuation=True, text="process_payment_fname")
        w.impact = produce_impact(tmp_path_factory.mktemp("s37-impact"))
        yield w
    finally:
        writer.close()


# ---- helpers ------------------------------------------------------------------------------------

VOID = {"meta", "br", "hr", "img", "input", "link", "base", "area", "col", "embed", "source"}
TAGS = {
    "html", "head", "meta", "title", "style", "body", "header", "main", "section", "article",
    "footer", "nav", "h1", "h2", "h3", "p", "dl", "dt", "dd", "table", "thead", "tbody", "tr",
    "th", "td", "ul", "ol", "li", "pre", "code", "a", "span",
}  # fmt: skip
ATTRIBUTES = {"lang", "charset", "http-equiv", "name", "content", "id", "class", "href"}
ID_PATTERN = re.compile(
    r"^(section-(identity|markers|omitted|bundles|sources)|marker-\d+|bundle-\d+"
    r"|bundle-\d+-hop-\d+|source-[0-9a-f]{64})$"
)
CSP = "default-src 'none'; style-src 'unsafe-inline'"


class Page(HTMLParser):
    """Collects tags, attributes, text by id, table cells and pre/end-tag balance."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str, str | None]] = []
        self.ids: list[str] = []
        self.hrefs: list[tuple[str | None, str | None]] = []
        self.text: dict[str, str] = {}
        self.cells: dict[str, list[str]] = {}
        self.pre_starts = 0
        self.pre_ends = 0
        self.pres: list[tuple[str, str]] = []
        self.styles: list[str] = []
        self.metas: list[dict[str, str | None]] = []
        self.balanced = True
        self._stack: list[tuple[str, str | None]] = []
        self._pre: list[str] | None = None
        self._pre_class = ""
        self._cell: list[str] | None = None
        self._row: str | None = None
        self._cp = False
        self._style = False
        self.feed(source)
        self.close()
        self.balanced = self.balanced and not self._stack

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        found = dict(attrs)
        self.attrs.extend((tag, name, value) for name, value in attrs)
        if "id" in found:
            self.ids.append(found["id"])
        owner = next((i for _, i in reversed(self._stack) if i), None)
        if tag == "a":
            self.hrefs.append((owner, found.get("href")))
        if tag == "meta":
            self.metas.append(found)
        if tag == "pre":
            self.pre_starts += 1
            self._pre = []
            self._pre_class = found.get("class") or ""
        if tag == "span" and found.get("class") == "cp" and self._pre is not None:
            self._cp = True
        if tag == "tr":
            self._row = found.get("id")
            if self._row:
                self.cells[self._row] = []
        if tag == "td":
            self._cell = []
        if tag == "style":
            self._style = True
        if tag not in VOID:
            self._stack.append((tag, found.get("id")))

    def handle_endtag(self, tag):
        if not self._stack or self._stack[-1][0] != tag:
            self.balanced = False
            return
        self._stack.pop()
        if tag == "pre":
            self.pre_ends += 1
            body = "".join(self._pre or [])
            assert body.startswith("\n"), "the extra LF after the opening pre tag is missing"
            self.pres.append((self._pre_class, body[1:]))
            self._pre = None
        elif tag == "span":
            self._cp = False
        elif tag == "td":
            if self._row:
                self.cells[self._row].append("".join(self._cell or []))
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "style":
            self._style = False

    def handle_data(self, data):
        for _, identifier in self._stack:
            if identifier:
                self.text[identifier] = self.text.get(identifier, "") + data
        if self._pre is not None:
            if self._cp:
                assert data.startswith("U+")
                data = chr(int(data[2:], 16))
            self._pre.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._style:
            self.styles.append(data)


def span(occurrence: dict[str, Any]) -> str:
    return (
        f"{occurrence['path']}:{occurrence['line']}:{occurrence['column']}"
        f"-{occurrence['end_line']}:{occurrence['end_column']}"
    )


def canon(value: Any) -> str:
    return ev.canonical(value)


def cell_text(value: Any) -> str:
    return "none" if value is None else (value if isinstance(value, str) else canon(value))


def frozen_raw(name: str) -> bytes:
    return (FIXTURES / f"{name}.envelope.json").read_bytes()


def golden(name: str) -> evidence.Report:
    return evidence.Report(
        html=(FIXTURES / f"{name}.report.html").read_bytes(),
        json=(FIXTURES / f"{name}.report.json").read_bytes(),
    )


def invoke(tmp_path: Path, raw: bytes, *, tag: str = "a", html: str | None = None):
    source = tmp_path / f"in-{tag}.json"
    source.write_bytes(raw)
    out_html = tmp_path / (html or f"out-{tag}.html")
    out_json = tmp_path / f"out-{tag}.json"
    result = CliRunner().invoke(
        cli, ["evidence-report", str(source), "--html", str(out_html), "--json", str(out_json)]
    )
    return result, out_html, out_json


def inputs(world) -> dict[str, bytes]:
    found = {name: env.raw for name, env in world.envs.items() if name != "E_default"}
    found.update({f"frozen-{name}": frozen_raw(name) for name in FROZEN})
    return found


def unique_sources(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for bundle in envelope["data"]["bundles"]:
        for source in bundle["sources"]:
            seen.setdefault(source["id"], source)
    return list(seen.values())


def hops_of(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    if bundle["kind"] == "relationship":
        return [bundle["records"]["proof"]]
    if bundle["kind"] == "path":
        return bundle["records"]["hops"]
    return []


def identity_items(node: Any, where: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}/{key}"
            wanted = key == "id" or key.endswith("_id") or key in {"source_sha256", "repository"}
            if wanted and isinstance(value, str):
                found.append((here, value))
            found.extend(identity_items(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(identity_items(value, f"{where}/{index}"))
    return found


def recomputed_ids(envelope: dict[str, Any]) -> list[tuple[str, str, str]]:
    data = envelope["data"]
    rows = [
        (
            "retrieval_record_id",
            data["retrieval_record_id"],
            digest({key: data[key] for key in RECORD_KEYS}),
        )
    ]
    for resource in data["resources"]:
        stable = {k: v for k, v in resource.items() if k not in {"id", "uri"}}
        rows.append(("resource", resource["id"], digest(stable)))
    for bundle in data["bundles"]:
        stable = {k: v for k, v in bundle.items() if k not in {"id", "rank"}}
        rows.append(("bundle", bundle["id"], digest(stable)))
        for source in bundle["sources"]:
            stable = {k: v for k, v in source.items() if k not in {"id", "inline", "inline_reason"}}
            rows.append(("source", source["id"], digest(stable)))
    return rows


def with_ids(envelope: dict[str, Any]) -> dict[str, Any]:
    """Re-derive the retrieval record id after a tamper (the S18 pattern)."""
    data = envelope["data"]
    data["retrieval_record_id"] = digest({key: data[key] for key in RECORD_KEYS})
    return envelope


def as_raw(value: Any) -> bytes:
    return json.dumps(value).encode()


# ---- AC-S37-001 ---------------------------------------------------------------------------------


def check_identity(raw: bytes, produced: evidence.Report) -> dict[str, Any]:
    source = json.loads(raw)
    report = json.loads(produced.json)
    # (i) the envelope equals the input minus exactly the credential keys
    expected = copy.deepcopy(source)
    pointers = ["/data/next_cursor"]
    del expected["data"]["next_cursor"]
    for index, resource in enumerate(expected["data"]["resources"]):
        del resource["uri"]
        pointers.append(f"/data/resources/{index}/uri")
    assert report["envelope"] == expected
    assert [item["pointer"] for item in report["omitted_fields"]] == pointers
    cursor = source["data"]["next_cursor"]
    assert report["omitted_fields"][0]["value"] == ("null" if cursor is None else "redacted")
    assert all(item["reason"] == "capability_credential" for item in report["omitted_fields"][1:])
    # (ii) the identity multiset is unchanged and non-empty
    before = Counter(identity_items(source))
    assert before
    assert Counter(identity_items(report["envelope"])) == before
    # (iii) every id recomputes from the report's own envelope
    rows = recomputed_ids(report["envelope"])
    assert rows
    assert all(stored == derived for _, stored, derived in rows)
    # (iv) the envelope hash
    assert (
        report["envelope_sha256"] == hashlib.sha256(canon(report["envelope"]).encode()).hexdigest()
    )
    # (v) every view value equals the pointed input value
    identity = report["view"]["identity"]
    assert {name: resolve(source, ptr) for name, ptr in evidence.IDENTITY_POINTERS} == identity
    assert identity["query_digest"] == source["data"]["query_digest"]
    assert report["view"]["markers"]
    for marker in report["view"]["markers"]:
        assert marker["value"] == resolve(source, marker["pointer"])
    for index, (view, bundle) in enumerate(
        zip(report["view"]["bundles"], source["data"]["bundles"], strict=True)
    ):
        assert (view["index"], view["id"], view["kind"], view["rank"]) == (
            index,
            bundle["id"],
            bundle["kind"],
            bundle["rank"],
        )
        assert view["source_ids"] == [s["id"] for s in bundle["sources"]]
        for k, (hop_view, hop) in enumerate(zip(view["hops"], hops_of(bundle), strict=True)):
            assert hop_view["index"] == k
            assert hop_view["assertion_id"] == hop["assertion"]["id"]
            assert hop_view["relation"] == hop["assertion"]["relation"]
            assert hop_view["resolution"] == hop["assertion"]["resolution"]
            assert hop_view["traversal_direction"] == hop["traversal_direction"]
            assert hop_view["source_id"] == bundle["sources"][k]["id"]
            for key in ("path", "line", "column", "end_line", "end_column"):
                assert hop_view[key] == hop["occurrence"][key]
            for key in ("start_byte", "end_byte", "source_sha256", "producer_id"):
                assert hop_view[key] == hop["evidence"][key]
    assert [s["id"] for s in report["view"]["sources"]] == [s["id"] for s in unique_sources(source)]
    for view, found in zip(report["view"]["sources"], unique_sources(source), strict=True):
        for key in ("kind", "state", "reason", "path", "start_byte", "end_byte", "source_sha256"):
            assert view[key] == found.get(key)
        assert view["resource_ids"] == [
            r["id"] for r in source["data"]["resources"] if r["source_reference_id"] == found["id"]
        ]
    # (vi) no credential in either artifact
    for blob in (produced.json, produced.html):
        assert b"atlas-source://" not in blob
        if cursor is not None:
            assert cursor.encode() not in blob
        for resource in source["data"]["resources"]:
            assert parse_uri(resource["uri"])[1].encode() not in blob
    # (vii) the exact canonical form
    assert produced.json.endswith(b"}\n") and not produced.json.endswith(b"\n\n")
    assert produced.json == canon(report).encode() + b"\n"
    return source


def resolve(document: Any, pointer: str) -> Any:
    return evidence.resolve_pointer(document, pointer)


def test_evidence_json_preserves_response_identity(world, tmp_path):
    names = [n for n in inputs(world) if n not in {"E_default", "E_label", "E_b64", "E_filename"}]
    assert {"E_inline", "E_refs", "E_path", "E_trunc_page", "E_import"} <= set(names)
    assert {f"frozen-{n}" for n in FROZEN} <= set(names)
    everything = inputs(world)
    with_cursor = 0
    for index, name in enumerate(names):
        raw = everything[name]
        library = evidence.export(raw)
        source = check_identity(raw, library)
        with_cursor += source["data"]["next_cursor"] is not None
        if name in {"E_inline", "E_path", "E_trunc_page", "E_import", "frozen-truncated"}:
            result, out_html, out_json = invoke(tmp_path, raw, tag=str(index))
            assert result.exit_code == 0, result.stderr
            assert out_json.read_bytes() == library.json
            assert out_html.read_bytes() == library.html
    inline = json.loads(everything["E_inline"])
    assert len(recomputed_ids(inline)) > 20 and len(inline["data"]["resources"]) >= 1
    assert with_cursor >= 2  # E_trunc_page and the import envelope carry a real cursor
    assert json.loads(world.envs["E_trunc_page"].raw)["data"]["next_cursor"]


# ---- AC-S37-002 ---------------------------------------------------------------------------------


def test_evidence_html_has_source_paths(world):
    cases = {
        "E_path": world.envs["E_path"].raw,
        "E_inline": world.envs["E_inline"].raw,
        "frozen-path-references": frozen_raw("path-references"),
    }
    path_bundles = 0
    for name, raw in cases.items():
        source = json.loads(raw)
        page = Page(evidence.export(raw).html.decode())
        assert page.balanced
        bundles = source["data"]["bundles"]
        total = sum(len(hops_of(b)) for b in bundles)
        assert total > 0, name
        rows = [i for i in page.ids if re.fullmatch(r"bundle-\d+-hop-\d+", i)]
        assert len(rows) == total
        path_bundles += sum(1 for b in bundles if b["kind"] == "path" and hops_of(b))
        for i, bundle in enumerate(bundles):
            if bundle["kind"] == "definition" and bundle["records"]["occurrence"] is not None:
                assert span(bundle["records"]["occurrence"]) in page.text[f"bundle-{i}"]
            for j, hop in enumerate(hops_of(bundle)):
                cells = page.cells[f"bundle-{i}-hop-{j}"]
                occ, evd, assertion = hop["occurrence"], hop["evidence"], hop["assertion"]
                location = span(occ)
                byte_range = f"bytes [{evd['start_byte']}, {evd['end_byte']})"
                assert cells[:3] == [
                    assertion["relation"],
                    hop["traversal_direction"],
                    assertion["resolution"],
                ]
                assert cells[7] == location and cells[8] == byte_range
                assert cells[9] == evd["source_sha256"]
                (link,) = [h for owner, h in page.hrefs if owner == f"bundle-{i}-hop-{j}"]
                assert link == f"#source-{bundle['sources'][j]['id']}"
                article = page.text[link[1:]]
                assert occ["path"] in article and byte_range in article
    assert path_bundles >= 2  # E_path and the frozen path-references fixture


# ---- AC-S37-003 ---------------------------------------------------------------------------------


def test_evidence_report_preserves_incompleteness(world):
    cases = {
        "E_trunc_page": world.envs["E_trunc_page"].raw,
        "E_trunc_budget": world.envs["E_trunc_budget"].raw,
        "E_import": world.envs["E_import"].raw,
        "E_refs": world.envs["E_refs"].raw,
        "frozen-truncated": frozen_raw("truncated"),
    }
    for raw in cases.values():
        source = json.loads(raw)
        produced = evidence.export(raw)
        report = json.loads(produced.json)
        page = Page(produced.html.decode())
        markers = report["view"]["markers"]
        assert markers and len(markers) == len([i for i in page.ids if i.startswith("marker-")])
        for n, marker in enumerate(markers):
            value = resolve(source, marker["pointer"])
            assert marker["value"] == value
            shown = page.cells[f"marker-{n}"][3]
            assert shown == cell_text(value)
            assert (shown == "none") == (value is None)
            if shown == "complete":
                assert value == "complete"
        if "complete" not in canon(source):
            assert "complete" not in produced.html.decode().lower()
    # E_trunc_page: a real page cut with a cursor
    page_env = json.loads(cases["E_trunc_page"])
    assert page_env["data"]["has_more"] is True and page_env["data"]["selection_reason"] is None
    report = json.loads(evidence.export(cases["E_trunc_page"]).json)
    assert report["omitted_fields"][0] == {
        "pointer": "/data/next_cursor",
        "reason": "capability_credential",
        "value": "redacted",
    }
    by_pointer = {m["pointer"]: m["value"] for m in report["view"]["markers"]}
    assert by_pointer["/data/has_more"] is True and by_pointer["/data/selection/reason"] is None
    shown = Page(evidence.export(cases["E_trunc_page"]).html.decode()).text["section-omitted"]
    assert "/data/next_cursor - capability_credential - redacted" in shown
    # E_trunc_budget: a budget cut is explicit
    budget = json.loads(cases["E_trunc_budget"])
    assert budget["data"]["selection_truncated"] is True and budget["data"]["has_more"] is False
    assert budget["data"]["selection_reason"] == "byte_limit"
    produced = evidence.export(cases["E_trunc_budget"])
    page = Page(produced.html.decode())
    rows = {c[1]: c[3] for key, c in page.cells.items() if key.startswith("marker-")}
    assert rows["data.selection_reason"] == "byte_limit"
    assert rows["data.selection_truncated"] == "true" and rows["data.has_more"] == "false"
    # E_import: partial, every source unavailable with its reason
    imported = json.loads(cases["E_import"])
    assert imported["evidence_status"] == "partial"
    sources = unique_sources(imported)
    assert sources and all(
        s["state"] == "unavailable" and s["reason"] == "source_unavailable" for s in sources
    )
    page = Page(evidence.export(cases["E_import"]).html.decode())
    for source in sources:
        article = page.text[f"source-{source['id']}"]
        assert "unavailable" in article and "source_unavailable" in article
    # E_refs: excerpts are not included, and the page says so
    refs = json.loads(cases["E_refs"])
    reasons = {s["inline_reason"] for s in unique_sources(refs)}
    assert reasons == {"references_policy"}
    page = Page(evidence.export(cases["E_refs"]).html.decode())
    assert page.pre_starts == 0
    for source in unique_sources(refs):
        assert "excerpt not included: references_policy" in page.text[f"source-{source['id']}"]
    # The never-resolved signals stay visible and are never invented
    for name in cases:
        report = json.loads(evidence.export(cases[name]).json)
        names = {m["name"] for m in report["view"]["markers"]}
        assert "evidence_status" in names
    hop_markers = [
        m
        for m in json.loads(evidence.export(world.envs["E_inline"].raw).json)["view"]["markers"]
        if m["pointer"].endswith("/assertion/unresolved_reason")
    ]
    assert hop_markers and all(m["value"] is None for m in hop_markers)


# ---- AC-S37-004 ---------------------------------------------------------------------------------


def test_evidence_html_escapes_source_content(world):
    cases = {
        "E_hostile": world.envs["E_hostile"].raw,
        "E_label": world.envs["E_label"].raw,
        "E_filename": world.envs["E_filename"].raw,
        "E_b64": world.envs["E_b64"].raw,
        "frozen-hostile": frozen_raw("hostile"),
    }
    # Positive controls: the hostile text really is in the real input.
    for name in ("E_hostile", "frozen-hostile"):
        data = canon(json.loads(cases[name])["data"])
        assert all(canon(s)[1:-1] in data for s in HOSTILE_STRINGS), name
    assert json.loads(cases["E_label"])["data"]["request"]["text"] == LABEL_TEXT
    assert any(
        s["path"] == HOSTILE_FILENAME for s in unique_sources(json.loads(cases["E_filename"]))
    )
    assert any(
        (s["inline"] or {}).get("encoding") == "base64"
        for s in unique_sources(json.loads(cases["E_b64"]))
    )
    for name, raw in cases.items():
        source = json.loads(raw)
        blob = evidence.export(raw).html
        text = blob.decode()
        page = Page(text)
        assert page.balanced
        assert page.tags and "script" not in page.tags and "img" not in page.tags
        assert page.attrs and not any(attr.startswith("on") for _, attr, _ in page.attrs)
        excerpts = [s for s in unique_sources(source) if s.get("inline") is not None]
        assert page.pre_starts == page.pre_ends == len(excerpts)
        assert [body for _, body in page.pres] == [s["inline"]["data"] for s in excerpts], name
        assert b"<script" not in blob.lower() and b"<img" not in blob.lower()
        assert "\u202e" not in text
    hostile = Page(evidence.export(cases["E_hostile"]).html.decode())
    assert any("\u202e" in body for _, body in hostile.pres)
    assert "U+202E" in evidence.export(cases["E_hostile"]).html.decode()
    assert "U+202E" in evidence.export(cases["frozen-hostile"]).html.decode()
    label = evidence.export(cases["E_label"]).html.decode()
    assert "&lt;script&gt;x&lt;/script&gt;" in label
    assert label.lower().count("<script") == 0
    filename = evidence.export(cases["E_filename"]).html.decode()
    assert "app/x&quot;&gt;&lt;b&gt;.py" in filename and "<b>" not in filename
    base64_page = Page(evidence.export(cases["E_b64"]).html.decode())
    assert [cls for cls, _ in base64_page.pres] == ["b64"]
    assert "base64, not decoded" in evidence.export(cases["E_b64"]).html.decode()


def test_text_escapes_markup_controls_and_tag_characters():
    assert evidence.text('<a href="x">&\'') == "&lt;a href=&quot;x&quot;&gt;&amp;&#x27;"
    assert evidence.text(None) == "none"
    assert evidence.text(True) == "true" and evidence.text({"b": 1, "a": "<"}) == (
        "{&quot;a&quot;:&quot;&lt;&quot;,&quot;b&quot;:1}"
    )
    for codepoint in (0, 8, 0xB, 0xD, 0x1F, 0x7F, 0x9F, 0xAD, 0x61C, 0x180E, 0x200B, 0x200F):
        assert evidence.text(chr(codepoint)) == f'<span class="cp">U+{codepoint:04X}</span>'
    for codepoint in (0x2028, 0x202E, 0x2060, 0x2064, 0x2066, 0x206F, 0xFEFF, 0xFFF9, 0xFFFB):
        assert evidence.text(chr(codepoint)) == f'<span class="cp">U+{codepoint:04X}</span>'
    for codepoint in (0xE0001, 0xE0020, 0xE0041, 0xE007F):
        assert evidence.text(chr(codepoint)) == f'<span class="cp">U+{codepoint:04X}</span>'
    for codepoint in (9, 10, 0x20, 0x7E, 0xA0, 0x2065, 0xE0000, 0xE0002, 0xE0080):
        assert evidence.text(chr(codepoint)) == chr(codepoint)


# ---- AC-S37-005 ---------------------------------------------------------------------------------


def every_page(world) -> list[tuple[str, str]]:
    pages = [(name, evidence.export(raw).html.decode()) for name, raw in inputs(world).items()]
    pages += [(f"golden-{name}", golden(name).html.decode()) for name in FROZEN]
    return pages


def check_self_contained(source: str, *, template: bool = False) -> Page:
    page = Page(source)
    assert page.balanced and page.tags
    assert set(page.tags) <= TAGS
    assert page.attrs and {name for _, name, _ in page.attrs} <= ATTRIBUTES
    for tag in ("link", "base", "form", "iframe", "object", "embed", "svg", "math", "script"):
        assert tag not in page.tags
    assert not {n for _, n, _ in page.attrs} & {"src", "srcset", "style"}
    style = "".join(page.styles)
    assert style
    for fragment in ("url(", "@import", "//", "http", "@font-face"):
        assert fragment not in style
    assert {m.get("http-equiv"): m.get("content") for m in page.metas if "http-equiv" in m} == {
        "Content-Security-Policy": CSP
    }
    assert {"name": "referrer", "content": "no-referrer"} in page.metas
    for _tag, name, value in page.attrs:
        if name in {"content", "lang", "charset", "name", "class", "http-equiv", "id"}:
            assert value is not None and not re.search(r"[a-z][a-z0-9+.-]*://", value)
    ids = set(page.ids)
    assert len(ids) == len(page.ids)
    assert all(ID_PATTERN.fullmatch(i) for i in ids)
    assert page.hrefs
    for _, href in page.hrefs:
        assert href is not None and href.startswith("#") and href[1:] in ids
    if not template:
        assert source.endswith("</html>\n") and "\r" not in source and "\ufeff" not in source
    return page


def test_evidence_report_is_self_contained(world):
    pages = every_page(world)
    assert len(pages) >= 14
    for _name, source in pages:
        check_self_contained(source)
    template = (MCP / "src/atlas_aci/reports/evidence_template.html").read_text()
    page = Page(template)
    assert page.balanced and set(page.tags) <= TAGS
    assert {name for _, name, _ in page.attrs} <= ATTRIBUTES
    assert "<script" not in template.lower() and "$" not in "".join(page.styles)
    check_self_contained(_fill(template), template=True)


def _fill(template: str) -> str:
    """The shell with empty sections: every nav href then has a target."""
    sections = {
        "identity": '<header id="section-identity"></header>',
        "markers": '<section id="section-markers"></section>',
        "omitted": '<section id="section-omitted"></section>',
        "bundles": '<section id="section-bundles"></section>',
        "sources": '<section id="section-sources"></section>',
    }
    from string import Template

    return Template(template).substitute(sections)


# ---- AC-S37-006 ---------------------------------------------------------------------------------


def test_evidence_report_replay_is_idempotent(world, tmp_path):
    names = list(FROZEN)
    assert names
    for name in names:
        raw = frozen_raw(name)
        gold = golden(name)
        envelope = json.loads(raw)
        assert graph.STRUCTURED_VALIDATOR.is_valid(envelope)
        assert evidence.load_envelope(raw) == envelope
        first, second = evidence.export(raw), evidence.export(raw)
        assert first == second == gold
        for tag in ("x", "y"):
            result, out_html, out_json = invoke(tmp_path, raw, tag=f"{name}-{tag}")
            assert result.exit_code == 0, result.stderr
            assert out_html.read_bytes() == gold.html and out_json.read_bytes() == gold.json
        pretty = json.dumps(envelope, indent=2).encode()
        assert pretty != raw and evidence.export(pretty) == gold
        escaped = json.dumps(envelope, ensure_ascii=True).encode()
        assert evidence.export(escaped) == gold
    # One subprocess under a hostile environment equals the goldens.
    env = {**os.environ, "TZ": "Pacific/Kiritimati", "LC_ALL": "C", "PYTHONHASHSEED": "123"}
    for name in ("inline", "hostile"):
        source = tmp_path / f"sub-{name}.json"
        source.write_bytes(frozen_raw(name))
        out_html, out_json = tmp_path / f"sub-{name}.html", tmp_path / f"sub-{name}.report.json"
        run = subprocess.run(
            [
                sys.executable, "-m", "atlas_aci", "evidence-report", str(source),
                "--html", str(out_html), "--json", str(out_json),
            ],
            env=env, capture_output=True, check=False, cwd=tmp_path,
        )  # fmt: skip
        assert run.returncode == 0 and run.stdout == b"", run.stderr
        assert out_html.read_bytes() == golden(name).html
        assert out_json.read_bytes() == golden(name).json
    # content[0].text and structuredContent give identical reports, rendered twice.
    live = world.envs["E_inline"]
    assert live.wire == live.body == live.structured
    from_text = evidence.export(live.raw)
    from_structured = evidence.export(json.dumps(live.structured).encode())
    from_wire = evidence.export(json.dumps(live.wire, indent=2).encode())
    assert from_text == from_structured == from_wire == evidence.export(live.raw)


# ---- AC-S37-007 ---------------------------------------------------------------------------------


def tampers(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    other = "f" * 64
    found: dict[str, dict[str, Any]] = {}
    one = copy.deepcopy(envelope)
    one["data"]["resources"][0]["snapshot_id"] = other
    found["resource_snapshot"] = one
    two = copy.deepcopy(envelope)
    resource = two["data"]["resources"][0]
    _, token, offset, budget = parse_uri(resource["uri"])
    resource["snapshot_id"] = other
    resource["id"] = digest({k: v for k, v in resource.items() if k not in {"id", "uri"}})
    resource["uri"] = next_uri(resource["id"], token, offset, budget)
    found["resource_snapshot_rederived"] = two
    three = copy.deepcopy(envelope)
    three["data"]["resources"][0]["generation_id"] = other
    found["resource_generation"] = three
    four = copy.deepcopy(envelope)
    four["data"]["snapshot_id"] = other
    found["data_snapshot"] = four
    five = copy.deepcopy(envelope)
    five["snapshot_id"] = None
    five["data"]["snapshot_id"] = None
    found["declared_nulled"] = five
    return found


def test_evidence_mixed_snapshot_rejected(world, tmp_path):
    base = world.envs["E_inline"].body
    assert base["snapshot_id"] is not None and len(base["data"]["resources"]) >= 1
    assert evidence.export(as_raw(base))  # the untampered base is accepted
    assert evidence.export(world.envs["E_import"].raw)  # a null declared snapshot is accepted
    cases = tampers(base)
    assert len(cases) == 5
    for name, forged in cases.items():
        with pytest.raises(evidence.ReportRejected) as info:
            evidence.load_envelope(as_raw(forged))
        assert str(info.value) == "mixed_snapshot_evidence", name
        sentinel = tmp_path / f"sentinel-{name}.html"
        sentinel.write_bytes(b"keep me")
        result, out_html, out_json = invoke(tmp_path, as_raw(forged), tag=name, html=sentinel.name)
        assert result.exit_code == 1, name
        assert result.stderr == "Error: Evidence report rejected: mixed_snapshot_evidence\n"
        assert result.stdout == "" and not out_json.exists()
        assert sentinel.read_bytes() == b"keep me" and out_html == sentinel
    # A consistent whole-snapshot change is a generic invalid envelope, not a mixed one.
    shifted = copy.deepcopy(base)
    shifted["snapshot_id"] = shifted["data"]["snapshot_id"] = "e" * 64
    for resource in shifted["data"]["resources"]:
        resource["snapshot_id"] = "e" * 64
    with pytest.raises(evidence.ReportRejected) as info:
        evidence.load_envelope(as_raw(shifted))
    assert str(info.value) == "invalid_envelope"
    # Resource-free envelopes with a nulled snapshot are fine as far as the scan goes.
    assert world.envs["E_import"].body["snapshot_id"] is None


# ---- typed errors -------------------------------------------------------------------------------


def rejected(raw: bytes, code: str) -> None:
    with pytest.raises(evidence.ReportRejected) as info:
        evidence.load_envelope(raw)
    assert str(info.value) == code and info.value.code == code
    assert info.value.__cause__ is None


def test_every_rejection_code_is_reachable_and_typed(world):
    live = world.envs["E_inline"]
    assert set(evidence.CODES) == {
        "envelope_too_large",
        "invalid_input",
        "invalid_envelope",
        "unsupported_operation",
        "mixed_snapshot_evidence",
        "report_too_large",
    }
    rejected(b"{" + b" " * evidence.MAX_INPUT_FILE_BYTES, "envelope_too_large")
    padded = {**live.body, "padding": "x" * (evidence.MAX_ENVELOPE_BYTES + 1)}
    assert len(as_raw(padded)) <= evidence.MAX_INPUT_FILE_BYTES
    rejected(as_raw(padded), "envelope_too_large")
    for raw in (
        b"\xff\xfe",
        b"\xef\xbb\xbf" + live.raw,
        b'{"a": NaN}',
        b'{"a": -Infinity}',
        b'{"a": 1, "a": 2}',
        b'{"a": "\\ud800"}',
        b'{"a": 1e999}',
        b"",
        b"not json",
        b"[" * 400_000 + b"]" * 400_000,
        b'{"a":' * 300_000 + b"1" + b"}" * 300_000,
    ):
        rejected(raw, "invalid_input")
    wrapper = {
        "content": [{"type": "text", "text": live.text}],
        "structuredContent": live.body,
        "isError": False,
    }
    report_json = evidence.export(live.raw).json
    over = copy.deepcopy(live.body)
    over["bounds"]["element_limit"] = 201
    for raw in (b"[1]", b"{}", b"null", as_raw(wrapper), report_json, as_raw(over)):
        rejected(raw, "invalid_envelope")
    assert world.continue_page.body["operation"] == "continue"
    assert world.budget_error.body["error"]["code"] == "INVALID_BUDGET"
    assert world.impact.body["operation"] == "impact"
    for raw in (world.continue_page.raw, world.budget_error.raw, world.impact.raw):
        rejected(raw, "unsupported_operation")
    with pytest.raises(evidence.ReportRejected) as info:
        evidence.export(live.raw, max_json_bytes=10)
    assert str(info.value) == "report_too_large"
    with pytest.raises(evidence.ReportRejected) as info:
        evidence.export(live.raw, max_html_bytes=10)
    assert str(info.value) == "report_too_large"
    assert evidence.export(live.raw, max_json_bytes=len(evidence.export(live.raw).json))
    with pytest.raises(ValueError):
        evidence.ReportRejected("not a code")


def test_the_continue_and_error_shapes_are_otherwise_valid_envelopes(world):
    # The rejection is semantic (operation), not an accident of schema failure.
    for env in (world.continue_page, world.budget_error, world.impact):
        assert graph.STRUCTURED_VALIDATOR.is_valid(env.body)


# ---- CLI ----------------------------------------------------------------------------------------


def test_cli_contract(world, tmp_path):
    runner = CliRunner()
    assert "evidence-report" in cli.commands
    raw = world.envs["E_inline"].raw
    source = tmp_path / "env.json"
    source.write_bytes(raw)
    out_dir = tmp_path / "nested/out"
    result = runner.invoke(
        cli,
        [
            "evidence-report",
            str(source),
            "--html",
            str(out_dir / "r.html"),
            "--json",
            str(out_dir / "r.json"),
        ],
    )
    assert result.exit_code == 0 and result.stdout == "" and result.stderr == ""
    assert sorted(p.name for p in out_dir.iterdir()) == ["r.html", "r.json"]
    assert (out_dir / "r.html").read_bytes() == evidence.export(raw).html
    # Usage errors: exit 2, nothing written.
    html, js = tmp_path / "u.html", tmp_path / "u.json"
    for args in (
        ["evidence-report", str(source), "--html", str(html)],
        ["evidence-report", str(source), "--json", str(js)],
        ["evidence-report", str(tmp_path / "missing.json"), "--html", str(html), "--json", str(js)],
        ["evidence-report", str(source), "--html", str(html), "--json", str(html)],
        ["evidence-report", str(source), "--html", str(source), "--json", str(js)],
        ["evidence-report", str(source), "--html", str(html), "--json", str(source)],
    ):
        result = runner.invoke(cli, args)
        assert result.exit_code == 2, args
        assert not html.exists() and not js.exists()
    # A hard link to the envelope is the same file.
    alias = tmp_path / "alias.json"
    os.link(source, alias)
    result = runner.invoke(
        cli, ["evidence-report", str(source), "--html", str(alias), "--json", str(js)]
    )
    assert result.exit_code == 2 and source.read_bytes() == raw and not js.exists()
    # Rejections: exit 1, exact stderr, no path, nothing created, existing output untouched.
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{}")
    kept = tmp_path / "kept.html"
    kept.write_bytes(b"old")
    result = runner.invoke(
        cli, ["evidence-report", str(bad), "--html", str(kept), "--json", str(js)]
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == "Error: Evidence report rejected: invalid_envelope\n"
    assert str(tmp_path) not in result.stderr
    assert kept.read_bytes() == b"old" and not js.exists()
    # An unreadable envelope (a directory is refused by click; a missing file by exists=True).
    # A write failure leaves no pair and no temp file.
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    result = runner.invoke(
        cli,
        [
            "evidence-report",
            str(source),
            "--html",
            str(blocker / "x.html"),
            "--json",
            str(tmp_path / "j.json"),
        ],
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith("Error: Evidence report failed: ")
    assert str(tmp_path) not in result.stderr
    assert not (tmp_path / "j.json").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    # Oversized files are rejected after reading at most the cap plus one byte.
    huge = tmp_path / "huge.json"
    huge.write_bytes(b" " * (evidence.MAX_INPUT_FILE_BYTES + 10))
    result = runner.invoke(
        cli, ["evidence-report", str(huge), "--html", str(html), "--json", str(js)]
    )
    assert result.exit_code == 1
    assert result.stderr == "Error: Evidence report rejected: envelope_too_large\n"


# ---- package resource and isolation -------------------------------------------------------------


def test_the_template_ships_as_a_package_resource():
    packaged = resources.files("atlas_aci.reports").joinpath("evidence_template.html")
    assert (
        packaged.read_bytes() == (MCP / "src/atlas_aci/reports/evidence_template.html").read_bytes()
    )
    from string import Template

    assert Template(packaged.read_text()).get_identifiers() == evidence.TEMPLATE_IDENTIFIERS
    assert (MCP / "src/atlas_aci/reports/__init__.py").is_file()
    wheel = (MCP / "pyproject.toml").read_text()
    assert 'packages = ["src/atlas_aci"]' in wheel


def test_tool_surface_and_budgets_are_the_base_values(tmp_path):
    from test_s21_support import workspace

    from atlas_aci.protocol import results
    from atlas_aci.query import admission, impact_admission, paging

    assert len(READ_ONLY_TOOLS) == 7
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert "evidence" not in {t["name"] for t in json.loads(out.stdout)}
    assert "evidence-report" not in out.stdout
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    assert not list(ROOT.glob(".gitattributes"))


BANNED_MODULES = {
    "time", "datetime", "random", "uuid", "os", "locale", "socket", "urllib", "http",
    "subprocess", "platform", "tempfile", "unicodedata", "pathlib", "shutil", "ctypes",
    "multiprocessing", "asyncio",
}  # fmt: skip
BANNED_ATLAS = (
    "atlas_aci.server",
    "atlas_aci.tools",
    "atlas_aci.codegraph",
    "atlas_aci.query",
    "atlas_aci.protocol.context",
    "atlas_aci.enforcement",
)
ALLOWED_PLAIN = {
    "__future__", "copy", "dataclasses", "hashlib", "html", "importlib", "json", "string",
    "typing",
}  # fmt: skip
ALLOWED_ATLAS = {"atlas_aci.config", "atlas_aci.protocol.graph", "atlas_aci.storage.evidence"}


def test_the_exporter_module_has_a_closed_import_surface():
    source = (MCP / "src/atlas_aci/reports/evidence.py").read_text()
    tree = ast.parse(source)
    plain: set[str] = set()
    atlas: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [(alias.name, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            names = [(node.module or "", (node.module or "") + "." + a.name) for a in node.names]
        else:
            continue
        for module, full in names:
            if module.startswith("atlas_aci"):
                atlas.add(full)
            else:
                plain.add(module)
            assert module.split(".")[0] not in BANNED_MODULES, module
            assert not any(full == b or full.startswith(b + ".") for b in BANNED_ATLAS), full
    assert plain <= ALLOWED_PLAIN and plain
    assert atlas == ALLOWED_ATLAS
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "eval", "exec", "compile", "__import__", "input"}
    order = [
        n.module
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and n.module.startswith("atlas")
    ]
    # protocol.graph is imported before storage; protocol.context is never imported directly.
    assert order == ["atlas_aci", "atlas_aci.protocol", "atlas_aci.storage"]


def test_the_command_module_stays_lazy():
    source = (MCP / "src/atlas_aci/__main__.py").read_text()
    top = [n for n in ast.parse(source).body if isinstance(n, ast.ImportFrom | ast.Import)]
    assert not any("reports" in ast.dump(n) for n in top)


# ---- fixtures -----------------------------------------------------------------------------------


def test_the_frozen_fixture_directory_is_complete_small_and_provenanced():
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    files = sorted(p.name for p in FIXTURES.iterdir())
    expected = sorted(
        ["provenance.json"]
        + [
            f"{n}.{kind}"
            for n in FROZEN
            for kind in ("envelope.json", "report.json", "report.html")
        ]
    )
    assert files == expected
    assert sum(p.stat().st_size for p in FIXTURES.iterdir()) < 2 * 1024 * 1024
    assert all(p.stat().st_size < 512 * 1024 for p in FIXTURES.iterdir())
    assert provenance["base_commit"] == BASE_COMMIT
    assert provenance["repository_path"] == str(FIXED_ROOT / "repo")
    assert provenance["rendering_version"] == evidence.RENDERING_VERSION
    for key in ("python", "tree-sitter", "tree-sitter-language-pack", "image"):
        assert provenance["environment"][key]
    assert set(provenance["fixtures"]) == set(FROZEN)
    for name, entry in provenance["fixtures"].items():
        assert entry["arguments"] == request(**FROZEN[name][0])
        for filename, digest_ in entry["sha256"].items():
            assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == digest_
        assert len(entry["sha256"]) == 3
        envelope = json.loads(frozen_raw(name))
        assert envelope["data"]["repository"] == provenance["repository_path"]
    truncated = json.loads(frozen_raw("truncated"))["data"]
    assert truncated["has_more"] and truncated["next_cursor"] and truncated["resources"]
    for name in ("inline", "path-references", "hostile"):
        data = json.loads(frozen_raw(name))["data"]
        assert data["resources"] and data["next_cursor"] is None


def test_the_docs_name_every_code_and_omission():
    docs = (ROOT / "docs/evidence-reports.md").read_text()
    for code in evidence.CODES:
        assert code in docs
    for needle in (
        "/data/next_cursor",
        "/data/resources/<i>/uri",
        "capability_credential",
        "unresolved_reason",
        "candidate_ids",
        "data.repository",
        "structuredContent",
        "atlas-evidence-report.v1",
        evidence.RENDERING_VERSION,
        "--regenerate",
    ):
        assert needle in docs, needle
    assert "evidence-reports.md" in (ROOT / "README.md").read_text()


# ---- fixture regenerator ------------------------------------------------------------------------


def regenerate() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(FIXED_ROOT, ignore_errors=True)
    repo = FIXED_ROOT / "repo"
    writer = build_repo(repo)
    entries: dict[str, Any] = {}
    try:
        for state in ("clean", "hostile"):
            if state == "hostile":
                add_hostile(repo, writer)
            for name, (options, wanted) in FROZEN.items():
                if wanted != state:
                    continue
                env = produce(repo, FIXED_ROOT, **options)
                produced = evidence.export(env.raw)
                files = {
                    f"{name}.envelope.json": canon(env.body).encode() + b"\n",
                    f"{name}.report.json": produced.json,
                    f"{name}.report.html": produced.html,
                }
                assert evidence.export(files[f"{name}.envelope.json"]) == produced
                for filename, payload in files.items():
                    (FIXTURES / filename).write_bytes(payload)
                entries[name] = {
                    "arguments": request(**options),
                    "sha256": {f: hashlib.sha256(p).hexdigest() for f, p in files.items()},
                }
    finally:
        writer.close()
        shutil.rmtree(FIXED_ROOT, ignore_errors=True)
    provenance = {
        "base_commit": BASE_COMMIT,
        "repository_path": str(FIXED_ROOT / "repo"),
        "rendering_version": evidence.RENDERING_VERSION,
        "environment": {
            "image": "atlas-aci-dev-s16 (Dockerfile.dev, Linux)",
            "python": ".".join(str(part) for part in sys.version_info[:3]),
            "tree-sitter": metadata.version("tree-sitter"),
            "tree-sitter-language-pack": metadata.version("tree-sitter-language-pack"),
        },
        "fixtures": entries,
    }
    (FIXTURES / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["--regenerate"]:
        raise SystemExit("usage: test_s37_evidence_report.py --regenerate")
    regenerate()
