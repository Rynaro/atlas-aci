"""ACI-S20 labeled comparison (issue approach step 4): changed-file baseline vs reverse paths.

Labels are frozen constants (`LABEL_*` in test_s20_support, sha256-pinned fixture; captured in
`.spectra/evidence/aci-s20/labels.json` BEFORE any propagation code existed). Only facts
constructed into the fixture are asserted; no superiority threshold is claimed. The honest
per-baseline numbers are written to `.spectra/evidence/aci-s20/baseline-comparison.json`.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path

from test_s20_support import (
    FIXTURE_SHA256,
    LABEL_AFFECTED,
    LABEL_SEEDS,
    LABEL_UNAFFECTED,
    V1,
    V2,
    fixture_digest,
    key,
    lease,
    served_pair,
)

EVIDENCE = Path(__file__).resolve().parents[3] / ".spectra" / "evidence" / "aci-s20"


def score(predicted: set[str], positives: set[str], universe: set[str]) -> dict:
    true = predicted & positives
    false = predicted - positives
    missed = positives - predicted
    return {
        "predicted": sorted(predicted),
        "predicted_count": len(predicted),
        "true_positives": sorted(true),
        "false_positives": sorted(false),
        "false_negatives": sorted(missed),
        "precision": round(len(true) / len(predicted), 4) if predicted else None,
        "recall": round(len(true) / len(positives), 4) if positives else None,
        "universe_size": len(universe),
    }


def test_changed_file_baseline_vs_reverse_path_baseline(tmp_path):
    assert fixture_digest() == FIXTURE_SHA256  # frozen fixture
    assert LABEL_AFFECTED.isdisjoint(LABEL_UNAFFECTED)
    served = served_pair(tmp_path, V1, V2)
    try:
        data = served.impact()["data"]
        changed_paths = {p for p in V1 if V1[p] != V2.get(p)} | {p for p in V2 if p not in V1}
        assert changed_paths == {"pkg/geometry.py"}
        with ExitStack() as stack:
            readers = {"before": lease(stack, served.before), "after": lease(stack, served.after)}
            entities = {}
            for side, reader in readers.items():
                for (payload,) in reader.db.execute("SELECT payload FROM entities"):
                    entity = json.loads(payload)
                    if entity["kind"] != "file" and not entity["kind"].startswith("scip_"):
                        entities.setdefault(entity["id"], (side, entity))
            seed_ids = {s["changed_entity_id"] for s in data["seeds"]}
            seed_keys = {
                key(entities[i][1]): s["category"]
                for i in seed_ids
                for s in data["seeds"]
                if s["changed_entity_id"] == i
            }
            assert seed_keys == LABEL_SEEDS
            universe = {key(e) for _, e in entities.values()}
            # B0: every non-file entity in a path whose bytes differ, excluding the seeds.
            b0 = {
                key(e)
                for i, (_, e) in entities.items()
                if e["path"] in changed_paths and i not in seed_ids
            }
            # B1: the reverse-path baseline (impact `impacted`).
            impacted = data["impacted"]
            assert impacted
            b1 = {key(i["entity"]) for i in impacted}
            explained_b1 = sum(1 for i in impacted if i["path"]["hops"]) / len(impacted)
            boundary_names = {b["name"] for b in data["coverage"]["boundaries"]}
            assert boundary_names
            covered = {}
            for missed in sorted(LABEL_AFFECTED - b1):
                entity_id = next(i for i, (_, e) in entities.items() if key(e) == missed)
                side, _ = entities[entity_id]
                rows = (
                    readers[side]
                    .db.execute(
                        "SELECT callee_name FROM refs WHERE source_entity_id=?", (entity_id,)
                    )
                    .fetchall()
                )
                covered[missed] = sorted({r[0] for r in rows} & boundary_names)
        results = {"B0_changed_file": score(b0, LABEL_AFFECTED, universe)}
        results["B0_changed_file"]["explained_fraction"] = 0.0  # no evidence path by construction
        results["B1_reverse_path"] = score(b1, LABEL_AFFECTED, universe)
        results["B1_reverse_path"]["explained_fraction"] = explained_b1
        results["B1_reverse_path"]["false_negatives_covered_by_boundary"] = covered

        # Fixture-constructed facts only.
        assert explained_b1 == 1.0
        assert {"pkg/shapes.py::Rect.size", "pkg/cli.py::main"} <= b1  # cross-file + before-side
        assert not {"pkg/shapes.py::Rect.size", "pkg/cli.py::main"} & b0  # B0 misses both
        assert "pkg/geometry.py::perimeter" in b0  # B0 over-reports the unchanged sibling
        assert "pkg/geometry.py::perimeter" not in b1
        assert not (b1 & LABEL_UNAFFECTED)  # B1 never reports a labeled-unaffected entity
        assert b0 & LABEL_UNAFFECTED == {"pkg/geometry.py::perimeter"}
        # B1's only false negative is covered by the unresolved `Rect.size` boundary.
        assert LABEL_AFFECTED - b1 == {"pkg/report.py::summary"}
        assert covered == {"pkg/report.py::summary": ["size"]}
        assert data["coverage"]["state"] == "incomplete"

        summary = {
            "slice": "ACI-S20",
            "fixture_sha256": FIXTURE_SHA256,
            "labels": "frozen before implementation; .spectra/evidence/aci-s20/labels.json",
            "positives": sorted(LABEL_AFFECTED),
            "labeled_unaffected": sorted(LABEL_UNAFFECTED),
            "baselines": results,
            "caveats": [
                "single hand-constructed fixture: shows mechanisms, not a population estimate",
                "no superiority threshold is claimed; B1 recall < 1 is reported, not hidden",
                "B1 false negatives are only 'covered' when a same-name unresolved boundary exists",
            ],
        }
        if EVIDENCE.is_dir():
            (EVIDENCE / "baseline-comparison.json").write_text(
                json.dumps(summary, indent=1, sort_keys=True) + "\n"
            )
    finally:
        served.close()
