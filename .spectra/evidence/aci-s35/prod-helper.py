"""Helper run INSIDE the production image by prod-scenario.sh (python only).

  prod-helper.py world DEST STATE       write the fixture state ("before" or "after") into DEST
                                        (git init on "before")
  prod-helper.py record REPO ROOT OUT   a real retrieval record through the image's MCP code path
                                        (build_server_state + call_tool_result), saved as the
                                        tool result's structuredContent; memex root in OUT's dir
  prod-helper.py request OUT            the context request used by feedback-rank
  prod-helper.py envelope RECORD ARTIFACT OUT [forged]
                                        the feedback envelope (claims: last bundle passed, bundle 1
                                        failed; "forged" claims bundle 1 passed) with an independent
                                        run identity; one useful annotation
  prod-helper.py order FILE KIND        bundle ids in order: record | baseline | ranking
  prod-helper.py adjustments FILE       "<bundle position>:<delta>:<ids>" of every adjustment
  prod-helper.py events STORE           "<outcome>:<id>" of every event, sorted
  prod-helper.py reasons FILE           the sorted exclusion reasons of a ranking sidecar
  prod-helper.py get FILE KEY...        print one JSON field (ints index lists)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

FX = Path("/repo/mcp-server/tests/fixtures/feedback")
PROFILE = "atlas-junit-xml-profile.v1"
NODE_A = "tests/test_payments.py::test_process_payment_ok"
NODE_B = "tests/test_payments.py::TestCards::test_declined"


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(domain, value):
    return hashlib.sha256((domain + ":" + canon(value)).encode("utf-8")).hexdigest()


def sources_of(record, bundle_id):
    bundle = next(b for b in record["data"]["bundles"] if b["id"] == bundle_id)
    return {s["path"]: s["source_sha256"] for s in bundle["sources"] if s["state"] == "available"}


command = sys.argv[1]
if command == "world":
    dest, state = Path(sys.argv[2]), sys.argv[3]
    world = json.loads((FX / "world.json").read_text(encoding="ascii"))
    dest.mkdir(parents=True, exist_ok=True)
    if state == "before":
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
        texts = world["before"]
    else:
        texts = world[state]
    for name, text in texts.items():
        path = dest / name
        if text is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
elif command == "record":
    from atlas_aci.config import Config
    from atlas_aci.server import build_server_state, call_tool_result

    repo, root, out = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    config = Config(
        repo=repo, graph_root=root, memex_root=out.parent / "memex", max_calls_per_minute=0
    )
    enforcement, memex, graph = build_server_state(config)
    request = {"schema_version": "atlas.graph.v1", "op": "context", "text": "process_payment"}
    result = asyncio.run(
        call_tool_result(
            "graph_query", request, config, enforcement, memex, graph,
            protocol_version="2025-11-25",
        )
    )
    graph.close()
    assert not result.isError, result
    payload = result.structuredContent
    assert json.loads(result.content[0].text) == payload
    assert payload["data"]["collection_stop"] is None
    out.write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
elif command == "request":
    request = {"schema_version": "atlas.graph.v1", "op": "context", "text": "process_payment"}
    Path(sys.argv[2]).write_bytes(json.dumps(request, sort_keys=True).encode("ascii") + b"\n")
elif command == "envelope":
    record = json.loads(Path(sys.argv[2]).read_text())
    artifact = Path(sys.argv[3]).read_bytes()
    forged = len(sys.argv) > 5 and sys.argv[5] == "forged"
    data = record["data"]
    ids = [b["id"] for b in data["bundles"]]
    producer = {"name": "pytest", "version": "9.0.3"}
    snapshot = {"snapshot_id": data["snapshot_id"], "generation_id": data["generation_id"]}
    run = digest(
        "atlas-test-run.v1",
        {
            "artifact": {"profile": PROFILE, "sha256": hashlib.sha256(artifact).hexdigest(),
                         "bytes": len(artifact)},
            "producer": producer,
            "snapshot": snapshot,
        },
    )

    def claim(label, nodeid, outcome, bundle):
        return {
            "claim_id": label,
            "selector": {"kind": "pytest_nodeid", "value": nodeid},
            "outcome": outcome,
            "results": [bundle],
            "source_files": sources_of(record, bundle),
        }

    document = {
        "format": "atlas-feedback-envelope.v1",
        "retrieval_record_id": data["retrieval_record_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "generation_id": snapshot["generation_id"],
        "run": {"run_id": run, "producer": producer},
        "artifact": {"profile": PROFILE, "sha256": hashlib.sha256(artifact).hexdigest(),
                     "bytes": len(artifact)},
        "claims": [
            claim("claim-a", NODE_A, "passed", ids[-1]),
            claim("claim-b", NODE_B, "passed" if forged else "failed", ids[1]),
        ],
        "annotations": [
            {
                "annotation_id": "note-1", "kind": "useful", "results": [ids[2]],
                "source_files": sources_of(record, ids[2]), "note": "read this one",
            }
        ],
    }
    Path(sys.argv[4]).write_bytes(json.dumps(document, sort_keys=True, indent=1).encode("ascii") + b"\n")
elif command == "order":
    document = json.loads(Path(sys.argv[2]).read_text())
    kind = sys.argv[3]
    if kind == "record":
        found = [b["id"] for b in document["data"]["bundles"]]
    elif kind == "baseline":
        found = [b["bundle_id"] for b in document["baseline"]["bundles"]]
    else:
        found = [b["bundle_id"] for b in document["ranking"]]
    print(",".join(found))
elif command == "adjustments":
    document = json.loads(Path(sys.argv[2]).read_text())
    for entry in document["ranking"]:
        if entry["adjustment"]:
            adjustment = entry["adjustment"]
            print(
                f"{entry['baseline_position']}->{entry['position']}:{adjustment['delta']}:"
                + ",".join(adjustment["supporting_event_ids"])
            )
elif command == "events":
    document = json.loads(Path(sys.argv[2]).read_text())
    print(" ".join(sorted(f"{e['claim']['outcome']}:{e['id']}" for e in document["events"])))
elif command == "reasons":
    document = json.loads(Path(sys.argv[2]).read_text())
    print(",".join(sorted({e["reason"] for e in document["events"]["excluded"]})))
elif command == "get":
    value = json.loads(Path(sys.argv[2]).read_text())
    for key in sys.argv[3:]:
        value = value[int(key)] if isinstance(value, list) else value[key]
    print(value if not isinstance(value, (dict, list)) else json.dumps(value, sort_keys=True))
