"""Frozen local useful-span accounting over actual SDK results; no model score."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import tempfile
import time
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.protocol.results import FIXED_ERROR_ENVELOPE_CAP, result_bytes
from atlas_aci.server import build_server_state, call_tool_result

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".spectra/evidence/aci-s18"
FIXTURE = ROOT / "mcp-server/tests/fixtures/s18/useful-spans.v1.json"
EXPECTED_FIXTURE = "07553bd3d51be01a1d522fc59c240bf41ecf312a051c413c609585c60999b507"


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def metrics(sources, useful, fixture):
    references, inline = {}, {}
    summed_reference = summed_inline = 0
    files = set()
    for source in sources:
        if source["state"] != "available":
            continue
        path, sha = source["path"], source["source_sha256"]
        if fixture["source_hashes"].get(path) != sha:
            raise ValueError("Returned source differs from frozen labels")
        raw = fixture["source_files"][path].encode()
        start, end = source["start_byte"], source["end_byte"]
        if not 0 <= start <= end <= len(raw):
            raise ValueError("Returned span outside actual frozen source")
        key = (path, sha)
        references.setdefault(key, []).append((start, end))
        summed_reference += end - start
        files.add(path)
        value = source.get("inline")
        if value is not None:
            content = (
                value["data"].encode()
                if value["encoding"] == "utf8"
                else base64.b64decode(value["data"], validate=True)
            )
            if content != raw[start:end]:
                raise ValueError("Inline bytes differ from the exact authenticated span")
            inline.setdefault(key, []).append((start, end))
            summed_inline += end - start
    references = {k: union(v) for k, v in references.items()}
    inline = {k: union(v) for k, v in inline.items()}
    labels = {}
    for span in useful:
        labels.setdefault((span["path"], span["source_sha256"]), []).append(
            (span["start_byte"], span["end_byte"])
        )
    labels = {k: union(v) for k, v in labels.items()}

    def size(values):
        return sum(end - start for rows in values.values() for start, end in rows)

    def covered(values):
        return sum(
            max(0, min(end, right) - max(start, left))
            for key, rows in values.items()
            for start, end in rows
            for left, right in labels.get(key, [])
        )

    reference_bytes, inline_bytes = size(references), size(inline)
    return {
        "useful_label_bytes": size(labels),
        "useful_reference_bytes": covered(references),
        "useful_inline_bytes": covered(inline),
        "unique_reference_bytes": reference_bytes,
        "unique_inline_bytes": inline_bytes,
        "redundant_reference_bytes": summed_reference - reference_bytes,
        "redundant_inline_bytes": summed_inline - inline_bytes,
        "selected_files": sorted(files),
    }


def body_and_sources(raw, baseline):
    sdk = json.loads(raw)
    body = json.loads(sdk["content"][0]["text"])
    if body["status"] == "error":
        return body, [], 0
    data = body["data"]
    if baseline:
        sources = [candidate["source"] for candidate in data["candidates"]]
        return body, sources, len(data["candidates"])
    return body, [s for atom in data["bundles"] for s in atom["sources"]], len(data["bundles"])


def main():
    raw_fixture = FIXTURE.read_bytes()
    if hashlib.sha256(raw_fixture).hexdigest() != EXPECTED_FIXTURE:
        raise RuntimeError("Frozen labels/ranker/budgets changed")
    fixture = json.loads(raw_fixture)
    baseline_path = OUT / "baseline-checkpoint.json"
    baseline = json.loads(baseline_path.read_bytes())
    tasks = {task["id"]: task for task in fixture["tasks"]}
    baseline_rows = []
    for row in baseline["lexical_results"]:
        raw = base64.b64decode(row["result_base64"])
        if (
            len(raw) != row["result_bytes"]
            or hashlib.sha256(raw).hexdigest() != row["result_sha256"]
        ):
            raise ValueError("Frozen baseline SDK bytes changed")
        body, sources, count = body_and_sources(raw, True)
        baseline_rows.append(
            {
                **{
                    k: row[k]
                    for k in (
                        "task",
                        "protocol_version",
                        "max_bytes",
                        "result_bytes",
                        "result_sha256",
                        "elapsed_seconds",
                        "is_error",
                    )
                },
                "policy": "references",
                "selected_candidates": count,
                "metrics": metrics(sources, tasks[row["task"]]["useful_spans"], fixture),
                "truncation": None if row["is_error"] else body["data"]["truncation_reason"],
            }
        )
    rows = []
    with tempfile.TemporaryDirectory(prefix="aci-s18-local-yield-") as temporary:
        repo = Path(temporary).resolve() / "repo"
        repo.mkdir()
        for path, text in fixture["source_files"].items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode())
        writer = CodeGraph(repo)
        writer.build()
        config = Config(repo=repo, memex_root=repo.parent / "memex", max_calls_per_minute=0)
        enforcement, memex, factory = build_server_state(config)

        async def capture():
            for version in fixture["protocol_versions"]:
                for budget in fixture["sdk_result_byte_budgets"]:
                    for task in fixture["tasks"]:
                        for policy in fixture["source_policies"]:
                            arguments = {
                                "schema_version": "atlas.graph.v1",
                                "op": "context",
                                "text": task["text"],
                                "source_policy": policy,
                                "max_bytes": budget,
                                "max_candidates": fixture["retrieval_max_candidates"],
                                "max_inline_bytes": fixture["max_inline_source_bytes"],
                            }
                            start = time.perf_counter()
                            result = await call_tool_result(
                                "graph_query",
                                arguments,
                                config,
                                enforcement,
                                memex,
                                factory,
                                protocol_version=version,
                            )
                            elapsed = time.perf_counter() - start
                            raw = result_bytes(result)
                            if len(raw) > (FIXED_ERROR_ENVELOPE_CAP if result.isError else budget):
                                raise ValueError("Actual SDK result exceeds its applicable bound")
                            body, sources, count = body_and_sources(raw, False)
                            rows.append(
                                {
                                    "task": task["id"],
                                    "protocol_version": version,
                                    "max_bytes": budget,
                                    "policy": policy,
                                    "request": arguments,
                                    "result_bytes": len(raw),
                                    "result_sha256": hashlib.sha256(raw).hexdigest(),
                                    "result_base64": base64.b64encode(raw).decode(),
                                    "is_error": result.isError,
                                    "error_code": body["error"]["code"] if result.isError else None,
                                    "elapsed_seconds": elapsed,
                                    "selected_bundles": count,
                                    "metrics": metrics(sources, task["useful_spans"], fixture),
                                    "selection_reason": None
                                    if result.isError
                                    else body["data"]["selection_reason"],
                                    "collection_stop": None
                                    if result.isError
                                    else body["data"]["collection_stop"],
                                }
                            )

        try:
            asyncio.run(capture())
        finally:
            factory.close()
            writer.close()
    result = {
        "runtime_sha256": {
            str(p.relative_to(ROOT / "mcp-server/src/atlas_aci")): (
                hashlib.sha256(p.read_bytes()).hexdigest()
            )
            for p in sorted((ROOT / "mcp-server/src/atlas_aci").rglob("*.py"))
        },
        "fixture_sha256": EXPECTED_FIXTURE,
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "comparison": (
            "Only context references rows match the pre-packer reference-only baseline policy. "
            "Inline rows are standalone new capability measurements, not a matched inline baseline."
        ),
        "cost_scope": (
            "Production adapter elapsed includes mandatory lease authentication and SDK result "
            "construction, excludes final outer result_bytes serialization and network framing. "
            "Small local fixture only."
        ),
        "frozen_ranker": fixture["ranker"],
        "baseline_reference_only": baseline_rows,
        "context": rows,
        "quality_gate": (
            "Descriptive local accounting, no superiority/savings threshold "
            "or paid model evaluation."
        ),
    }
    (OUT / "local-yield.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "baseline_rows": len(baseline_rows),
                "context_rows": len(rows),
                "baseline_empty_4000": sum(
                    r["max_bytes"] == 4000 and r["selected_candidates"] == 0 for r in baseline_rows
                ),
                "context_errors": sum(r["is_error"] for r in rows),
                "fixture_sha256": EXPECTED_FIXTURE,
            }
        )
    )


if __name__ == "__main__":
    main()
