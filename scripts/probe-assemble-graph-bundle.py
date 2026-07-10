#!/usr/bin/env python3
"""D3a probe, assembly step — merge phase 1 (graph + LPA) and phase 2
(Louvain partitions) into the single committed graph bundle
`probe-graphs.json.gz`.

Checker defect 13 (recompute-everything closure): `node_count`/
`edge_count` are summaries; a summary cannot be recomputed FROM. This
script commits the PRIMITIVES instead — the confident subgraph itself
(node identities + edge list, in the canonical total order phase 1
already derives from `communities()`'s own node ordering) and every
partition (the shipped LPA's labels, and all ten Louvain partitions by
seed) — so `scripts/verify-probe-verdict.py` can recompute every `Q`,
every node/edge/community count, from this file alone, trusting no
number any earlier phase printed.

Dependency-free: `json`, `gzip`, `hashlib` only — this script (like the
verifier) never imports networkx and never needs `mcp-server`'s own
environment; it just merges JSON two other scripts already produced.

Usage:
  python3 scripts/probe-assemble-graph-bundle.py \
      --out .spectra/changes/aci-v2-harden-and-augment/probe-graphs.json.gz \
      --repo solidus:<phase1.json>:<phase2.json> \
      --repo spree:<phase1.json>:<phase2.json>

Prints the output file's size (bytes) and sha256 to stdout — the sha256
belongs in the sidecar's `graph_bundle.sha256` field.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def _load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--repo",
        action="append",
        required=True,
        metavar="NAME:PHASE1_JSON:PHASE2_JSON",
        help="repeatable; one per repo",
    )
    args = parser.parse_args()

    bundle: dict[str, dict] = {}
    for spec in args.repo:
        name, phase1_path, phase2_path = spec.split(":", 2)
        phase1 = _load(phase1_path)
        phase2 = _load(phase2_path)

        if phase1["node_count"] != phase2["node_count"]:
            raise AssertionError(
                f"{name}: phase1 node_count {phase1['node_count']} != "
                f"phase2 node_count {phase2['node_count']}"
            )

        louvain_partitions = [None] * len(phase2["louvain_runs"])
        for run in phase2["louvain_runs"]:
            louvain_partitions[run["seed"]] = run["partition"]
        if any(p is None for p in louvain_partitions):
            raise AssertionError(f"{name}: missing a seed's partition (expected 0..9 contiguous)")

        bundle[name] = {
            "nodes": phase1["nodes"],
            "edges": phase1["edges"],
            "lpa_labels": phase1["lpa_labels"],
            "louvain_partitions": louvain_partitions,
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(payload, mtime=0)  # mtime=0: byte-reproducible across runs
    out_path.write_bytes(compressed)

    digest = hashlib.sha256(compressed).hexdigest()
    print(f"wrote {out_path} ({len(compressed)} bytes compressed, {len(payload)} bytes raw)")
    print(f"sha256={digest}")


if __name__ == "__main__":
    main()
