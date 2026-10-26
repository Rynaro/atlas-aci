"""Freeze actual S17 lexical SDK results before implementing S18 context packing."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import time
from pathlib import Path

from atlas_aci.codegraph import CodeGraph
from atlas_aci.config import Config
from atlas_aci.protocol.results import result_bytes
from atlas_aci.server import build_server_state, call_tool_result

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".spectra/evidence/aci-s18"
FIXTURE = ROOT / "mcp-server/tests/fixtures/s18/useful-spans.v1.json"


def main():
    fixture_raw = FIXTURE.read_bytes()
    fixture = json.loads(fixture_raw)
    repo = Path("/tmp/aci-s18-lexical-baseline-repo").resolve()
    if repo.exists():
        raise RuntimeError("Refuse to overwrite a captured baseline repository")
    repo.mkdir()
    for name, text in fixture["source_files"].items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = text.encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != fixture["source_hashes"][name]:
            raise RuntimeError("Frozen source mismatch")
        target.write_bytes(raw)
    graph = CodeGraph(repo)
    build = graph.build()
    config = Config(
        repo=repo, memex_root=repo.parent / "aci-s18-baseline-memex", max_calls_per_minute=0
    )
    enforcement, memex, factory = build_server_state(config)
    rows = []

    async def capture():
        for version in fixture["protocol_versions"]:
            for budget in fixture["sdk_result_byte_budgets"]:
                for task in fixture["tasks"]:
                    arguments = {
                        "schema_version": "atlas.graph.v1",
                        "op": "retrieve",
                        "text": task["text"],
                        "max_candidates": fixture["retrieval_max_candidates"],
                        "max_bytes": budget,
                    }
                    begun = time.perf_counter()
                    result = await call_tool_result(
                        "graph_query",
                        arguments,
                        config,
                        enforcement,
                        memex,
                        factory,
                        protocol_version=version,
                    )
                    elapsed = time.perf_counter() - begun
                    raw = result_bytes(result)
                    rows.append(
                        {
                            "task": task["id"],
                            "protocol_version": version,
                            "max_bytes": budget,
                            "request": arguments,
                            "exit_code": 0,
                            "is_error": result.isError,
                            "elapsed_seconds": elapsed,
                            "result_bytes": len(raw),
                            "result_sha256": hashlib.sha256(raw).hexdigest(),
                            "result_base64": base64.b64encode(raw).decode(),
                        }
                    )
        unsupported = await call_tool_result(
            "graph_query",
            {
                "schema_version": "atlas.graph.v1",
                "op": "context",
                "text": fixture["tasks"][0]["text"],
            },
            config,
            enforcement,
            memex,
            factory,
        )
        if not unsupported.isError:
            raise RuntimeError("Context baseline unexpectedly supported")
        return unsupported.model_dump(mode="json", by_alias=True, exclude_none=True)

    try:
        unsupported = asyncio.run(capture())
        with graph.reader_lease() as reader:
            generation, snapshot = reader.generation_id, reader.snapshot_id
            producer = json.loads(
                reader.db.execute(
                    "SELECT value FROM manifest WHERE key='generation_inputs'"
                ).fetchone()[0]
            )["producer"]
            for path, text in fixture["source_files"].items():
                if reader.read_source(path) != text.encode("utf-8"):
                    raise RuntimeError("Captured source bytes differ")
        source_root = ROOT / "mcp-server/src/atlas_aci"
        runtime = {
            str(p.relative_to(source_root)): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "base64": base64.b64encode(p.read_bytes()).decode(),
            }
            for p in sorted(source_root.rglob("*.py"))
        }
        raw = (json.dumps(runtime, sort_keys=True, separators=(",", ":")) + "\n").encode()
        packed = gzip.compress(raw, mtime=0)
        archive = OUT / "pre-edit-source.json.gz"
        archive.write_bytes(packed)
        native = {
            str(p.relative_to(graph.atlas_dir)): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "base64": base64.b64encode(p.read_bytes()).decode(),
            }
            for p in sorted(graph.atlas_dir.rglob("*"))
            if p.is_file() and "extraction-cache" not in p.parts
        }
        data = {
            "base_commit": fixture["base_commit"],
            "before_packer_implementation": True,
            "fixture_sha256": hashlib.sha256(fixture_raw).hexdigest(),
            "original_repository": str(repo),
            "build": build,
            "generation_id": generation,
            "snapshot_id": snapshot,
            "producer": producer,
            "native_files": native,
            "lexical_results": rows,
            "context_before": unsupported,
            "runtime_archive": {
                "path": str(archive.relative_to(ROOT)),
                "files": len(runtime),
                "raw_bytes": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "compressed_bytes": len(packed),
                "compressed_sha256": hashlib.sha256(packed).hexdigest(),
                "gzip_mtime": 0,
            },
        }
        (OUT / "baseline-checkpoint.json").write_text(json.dumps(data, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "fixture_sha256": data["fixture_sha256"],
                    "lexical_sdk_results": len(rows),
                    "errors": sum(row["is_error"] for row in rows),
                    "runtime_files": len(runtime),
                    "generation_id": generation,
                    "snapshot_id": snapshot,
                    "context_before_error": True,
                }
            )
        )
    finally:
        graph.close()
        factory.close()


if __name__ == "__main__":
    main()
