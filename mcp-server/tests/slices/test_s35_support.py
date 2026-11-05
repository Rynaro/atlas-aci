"""ACI-S35 shared fixtures: real indexer builds, real MCP records and an independent envelope.

Repositories and graph roots are generated in `tmp_path` from `tests/fixtures/feedback/world.json`;
no golden id exists because snapshot ids embed absolute paths. A retrieval record is produced
through the very code the MCP server runs (`call_tool_result`) and saved as the tool result's
`structuredContent`. Everything the library must agree with is recomputed here independently
(`hashlib` and `json` only): the run identity, the record digests, the policy digest and the
D9 order. Tests that run the CLI or `rank_feedback` are plain functions (the baseline runs
`asyncio.run` inside).
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from test_s19_support import build, init_git, write_tree
from test_s34_support import cli_run, sha, stat_inventory

from atlas_aci.config import Config
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import Side
from atlas_aci.server import build_server_state, call_tool_result

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/feedback"
SRC = ROOT / "mcp-server/src/atlas_aci"
DOCS = ROOT / "docs/verified-feedback.md"
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
WORLD_TEXTS = json.loads((FX / "world.json").read_text(encoding="ascii"))

PROFILE = "atlas-junit-xml-profile.v1"
ENVELOPE_FORMAT = "atlas-feedback-envelope.v1"
STORE_FORMAT = "atlas-feedback-events.v1"
POLICY_NAME = "atlas-feedback-ranking-policy.v1"
POLICY = {
    "format": POLICY_NAME,
    "weights": {"verified_passed": 1, "verified_failed": 0, "unverified_annotation": 0},
    "unit": "distinct_artifact_sha256_and_selector_per_bundle",
    "max_delta_per_bundle": 3,
    "sort_key": [
        "baseline_position_minus_delta",
        "negative_delta",
        "baseline_position",
        "bundle_id",
    ],
    "pinned": "baseline_position_0",
}
PRODUCER = {"name": "pytest", "version": "9.0.3"}

# Selectors of the fixture artifacts (junit-outcomes.xml); the testcase pairs are in the XML.
NODE_A = "tests/test_payments.py::test_process_payment_ok"
NODE_B = "tests/test_payments.py::TestCards::test_declined"
NODE_C = "tests/test_payments.py::test_skipped"
NODE_D = "tests/test_payments.py::test_setup_error"
NODE_E = "tests/test_payments.py::test_dup"
NODE_F = "tests/test_payments.py::test_brand[master-card.v1]"
NODE_G = "tests/test_payments.py::test_with_logs"
NODE_H = "tests/test_payments.py::test_nested_failure"
NODE_I = "tests/test_payments.py::test_xfail"
NODE_J = "tests/test_payments.py::TestCards::TestInner::test_deep"
NODE_K = "tests/test_payments.py::test_mixed"
NODE_L = "tests/sub-dir/test_dash.py::test_a"
NODE_ABSENT = "tests/test_payments.py::test_absent"


def canon(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def domain_digest(domain: str, value: Any) -> str:
    return hashlib.sha256((domain + ":" + canon(value)).encode("utf-8")).hexdigest()


POLICY_ID = hashlib.sha256(canon(POLICY).encode("utf-8")).hexdigest()


def dumps(document: Any) -> bytes:
    """Operator-style JSON input: indented, sorted, ASCII, one trailing newline."""
    return json.dumps(document, sort_keys=True, indent=1).encode("ascii") + b"\n"


def fixture(name: str) -> bytes:
    return (FX / name).read_bytes()


# ---- the world ---------------------------------------------------------------------------------


@dataclass
class World:
    """One built world: the checkout holds the AFTER state; each root holds its own snapshot."""

    tmp: Path
    repo: Path
    sides: dict[str, Side]
    memex: Path
    inputs: Path
    ids: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def roots(self) -> list[Path]:
        return [side.graph_root for side in self.sides.values()]

    @property
    def trees(self) -> list[Path]:
        """Everything a feedback command must leave alone (the memex root sits outside)."""
        return [self.repo, *self.roots, self.inputs]

    def side(self, name: str = "before") -> Side:
        return self.sides[name]

    def root(self, name: str = "before") -> Path:
        return self.sides[name].graph_root

    def snapshot(self, name: str = "before") -> str:
        return self.ids[name][0]

    def generation(self, name: str = "before") -> str:
        return self.ids[name][1]

    def input(self, name: str, data: bytes) -> Path:
        path = self.inputs / name
        path.write_bytes(data)
        return path

    def output(self, name: str = "out") -> Path:
        path = self.tmp / name
        path.mkdir(exist_ok=True)
        return path


def snapshot_ids(side: Side) -> tuple[str, str]:
    with ExitStack() as stack:
        reader = runtime.open_active(stack, side)
        return str(reader.snapshot_id), str(reader.generation_id)


def build_world(tmp: Path) -> World:
    """Index five states of one checkout path; the checkout is left at the after state."""
    tmp.mkdir(parents=True, exist_ok=True)
    repo = tmp / "repo"
    repo.mkdir()
    init_git(repo)
    before = WORLD_TEXTS["before"]
    write_tree(repo, before)
    sides: dict[str, Side] = {}

    def index(name: str) -> None:
        build(repo, tmp / ("root-" + name))
        sides[name] = Side(repo, tmp / ("root-" + name))

    def restore(edit: dict[str, Any]) -> None:
        write_tree(repo, {key: before[key] for key in edit})

    index("before")
    index("rebuild")
    write_tree(repo, WORLD_TEXTS["unrelated"])
    index("unrelated")
    restore(WORLD_TEXTS["unrelated"])
    write_tree(repo, WORLD_TEXTS["removed"])
    index("removed")
    restore(WORLD_TEXTS["removed"])
    write_tree(repo, WORLD_TEXTS["after"])
    index("after")
    memex = tmp / "memex"
    memex.mkdir()
    inputs = tmp / "inputs"
    inputs.mkdir()
    return World(tmp, repo, sides, memex, inputs, {n: snapshot_ids(s) for n, s in sides.items()})


# ---- records -----------------------------------------------------------------------------------


def request_arguments(text: str = "process_payment", **options: Any) -> dict[str, Any]:
    return {"schema_version": "atlas.graph.v1", "op": "context", "text": text, **options}


def mcp_result(world: World, name: str, arguments: dict[str, Any]):
    """The tool result of the real MCP code path over the named root (protocol 2025-11-25)."""
    config = Config(
        repo=world.repo,
        graph_root=world.root(name),
        memex_root=world.memex,
        max_calls_per_minute=0,
    )
    enforcement, memex, graph = build_server_state(config)
    try:
        return asyncio.run(
            call_tool_result(
                "graph_query",
                arguments,
                config,
                enforcement,
                memex,
                graph,
                protocol_version="2025-11-25",
            )
        )
    finally:
        graph.close()


def produce_record(world: World, name: str, text: str = "process_payment", **options: Any):
    """(saved record bytes, decoded record): the tool result's `structuredContent`."""
    result = mcp_result(world, name, request_arguments(text, **options))
    assert not result.isError, result
    payload = result.structuredContent
    assert json.loads(result.content[0].text) == payload
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"), payload


def bundle_ids(record: dict[str, Any]) -> list[str]:
    return [bundle["id"] for bundle in record["data"]["bundles"]]


def assert_untruncated(record: dict[str, Any]) -> None:
    """Condition 3: the baseline is wall-clock bounded; every request here must not be stopped."""
    assert record["data"]["collection_stop"] is None
    assert record["data"]["selection_reason"] is None


def bundle_sources(record: dict[str, Any], bundle_id: str) -> list[list[str]]:
    """Sorted unique [path, source_sha256] pairs of a bundle's available sources."""
    bundle = next(b for b in record["data"]["bundles"] if b["id"] == bundle_id)
    pairs = {
        (s["path"], s["source_sha256"]) for s in bundle["sources"] if s.get("state") == "available"
    }
    return [list(pair) for pair in sorted(pairs)]


def observations(side: Side) -> dict[str, str]:
    """{path: observed sha256} of the indexed observations, read with a plain read-only sqlite."""
    with ExitStack() as stack:
        db_path = runtime.open_active(stack, side).db_path
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(runtime.OBSERVATIONS_SQL).fetchone()
    finally:
        connection.close()
    return {
        item["path"]: item["observed_source_sha256"]
        for item in json.loads(row[0])
        if item.get("type") == "observation" and item.get("state") == "indexed"
    }


# ---- envelopes ---------------------------------------------------------------------------------


def run_identity(artifact: bytes, producer: dict[str, str], snapshot: tuple[str, str]) -> str:
    return domain_digest(
        "atlas-test-run.v1",
        {
            "artifact": {
                "profile": PROFILE,
                "sha256": sha(artifact),
                "bytes": len(artifact),
            },
            "producer": producer,
            "snapshot": {"snapshot_id": snapshot[0], "generation_id": snapshot[1]},
        },
    )


def result_ids(record: dict[str, Any], picks: list[int | str]) -> list[str]:
    ids = bundle_ids(record)
    return [ids[p] if isinstance(p, int) else p for p in picks]


def source_files(record: dict[str, Any], ids: list[str]) -> dict[str, str]:
    return {path: digest for i in ids for path, digest in bundle_sources(record, i)}


def claim(
    record: dict[str, Any],
    claim_id: str,
    nodeid: str | None,
    outcome: str,
    picks: list[int | str],
) -> dict[str, Any]:
    ids = result_ids(record, picks)
    return {
        "claim_id": claim_id,
        "selector": None if nodeid is None else {"kind": "pytest_nodeid", "value": nodeid},
        "outcome": outcome,
        "results": ids,
        "source_files": source_files(record, ids),
    }


def annotation(
    record: dict[str, Any],
    annotation_id: str,
    kind: str,
    picks: list[int | str],
    note: str | None = None,
) -> dict[str, Any]:
    ids = result_ids(record, picks)
    return {
        "annotation_id": annotation_id,
        "kind": kind,
        "results": ids,
        "source_files": source_files(record, ids),
        "note": note,
    }


def envelope(
    record: dict[str, Any],
    claims: list[dict[str, Any]] | None = None,
    annotations: list[dict[str, Any]] | None = None,
    *,
    artifact: bytes | None = None,
    producer: dict[str, str] | None = None,
    profile: str = PROFILE,
) -> dict[str, Any]:
    """The D1 envelope for `record`; `artifact` None yields the null run and artifact."""
    data = record["data"]
    snapshot = (data["snapshot_id"], data["generation_id"])
    producer = producer or PRODUCER
    return {
        "format": ENVELOPE_FORMAT,
        "retrieval_record_id": data["retrieval_record_id"],
        "snapshot_id": snapshot[0],
        "generation_id": snapshot[1],
        "run": None
        if artifact is None
        else {"run_id": run_identity(artifact, producer, snapshot), "producer": producer},
        "artifact": None
        if artifact is None
        else {"profile": profile, "sha256": sha(artifact), "bytes": len(artifact)},
        "claims": claims or [],
        "annotations": annotations or [],
    }


# ---- the independent D9 order ------------------------------------------------------------------


def expected_order(baseline: list[str], deltas: dict[str, int]) -> list[str]:
    """Position 0 stays; the rest sort by (pos - delta, -delta, pos, bundle_id)."""
    head, rest = baseline[:1], list(enumerate(baseline))[1:]
    ordered = sorted(
        rest, key=lambda item: (item[0] - deltas.get(item[1], 0), -deltas.get(item[1], 0), *item)
    )
    return head + [bundle for _, bundle in ordered]


# ---- running -----------------------------------------------------------------------------------


def import_args(
    world: World,
    name: str,
    envelope_path: Path,
    record_path: Path,
    output: Path,
    *,
    artifact: Path | None = None,
    store: Path | None = None,
    extra: tuple[Any, ...] = (),
) -> list[str]:
    args = [
        "feedback-import",
        "--repo",
        str(world.repo),
        "--graph-root",
        str(world.root(name)),
        "--envelope",
        str(envelope_path),
        "--retrieval-record",
        str(record_path),
        "--output",
        str(output),
    ]
    if artifact is not None:
        args += ["--artifact", str(artifact)]
    if store is not None:
        args += ["--store", str(store)]
    return [*args, *[str(item) for item in extra]]


def rank_args(
    world: World,
    name: str,
    request_path: Path,
    *,
    store: Path | None = None,
    overlay: bool = False,
    extra: tuple[Any, ...] = (),
) -> list[str]:
    args = [
        "feedback-rank",
        "--repo",
        str(world.repo),
        "--graph-root",
        str(world.root(name)),
        "--request",
        str(request_path),
    ]
    if overlay:
        args += ["--overlay", POLICY_NAME]
    if store is not None:
        args += ["--store", str(store)]
    return [*args, *[str(item) for item in extra]]


def temp_leftovers(*directories: Path) -> list[str]:
    return sorted(
        str(p) for d in directories for p in d.rglob("*") if p.name.startswith(".atlas-feedback-")
    )


def assert_rejected(result, text: str, out: Path, sentinel: bytes | None = None) -> None:
    """Exit 1, the exact fixed text, empty stdout, the output untouched, no temp file."""
    assert result.exit_code == 1, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr == "Error: " + text + "\n"
    if sentinel is None:
        assert not out.exists()
    else:
        assert out.read_bytes() == sentinel
    assert temp_leftovers(out.parent) == []


def assert_usage(result, text: str, out: Path | None = None) -> None:
    assert result.exit_code == 2, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr.rstrip("\n").splitlines()[-1] == "Error: " + text
    if out is not None:
        assert not out.exists()


_COUNTER = itertools.count()


def serial() -> int:
    """A process-unique number for staged file names."""
    return next(_COUNTER)


def run_import(
    world: World,
    name: str,
    document: dict[str, Any] | bytes,
    record: bytes,
    artifact: bytes | None = None,
    *,
    store: Path | None = None,
    extra: tuple[Any, ...] = (),
):
    """Stage the inputs under `world.inputs` and run `feedback-import`; returns (result, output)."""
    n = serial()
    raw = document if isinstance(document, bytes) else dumps(document)
    envelope_path = world.input(f"env-{n}.json", raw)
    record_path = world.input(f"rec-{n}.json", record)
    artifact_path = None if artifact is None else world.input(f"art-{n}.xml", artifact)
    out = world.output(f"out-{n}") / "store.json"
    result = cli_run(
        *import_args(
            world, name, envelope_path, record_path, out,
            artifact=artifact_path, store=store, extra=extra,
        )
    )  # fmt: skip
    return result, out


def run_rank(
    world: World,
    name: str,
    request: dict[str, Any] | bytes,
    *,
    store: Path | None = None,
    overlay: bool = False,
    extra: tuple[Any, ...] = (),
):
    """Stage the request and run `feedback-rank`; returns the CLI result."""
    n = serial()
    raw = request if isinstance(request, bytes) else dumps(request)
    path = world.input(f"req-{n}.json", raw)
    return cli_run(*rank_args(world, name, path, store=store, overlay=overlay, extra=extra))


def import_ok(world: World, name: str, document, record: bytes, artifact=None, **kwargs) -> Path:
    """Run an import that must succeed; returns the store path."""
    result, out = run_import(world, name, document, record, artifact, **kwargs)
    assert result.exit_code == 0, (result.exit_code, result.stderr)
    return out


def store_of(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


__all__ = [
    "cli_run",
    "serial",
    "sha",
    "stat_inventory",
    "write_tree",
]
