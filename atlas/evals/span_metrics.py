"""Pure useful-span accounting shared by the S18 and S23 evaluations.

`union`, `metrics` and `body_and_sources` are copied verbatim from
`measure_context_yield.py` (ACI-S18) with type hints added; behaviour is unchanged. Parity with
the S18 module is asserted by `test_s23_metrics.py`, which loads both by importlib.
"""

from __future__ import annotations

import base64
import json
from typing import Any


def union(intervals: Any) -> list[list[int]]:
    result: list[list[int]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def metrics(sources: Any, useful: Any, fixture: Any) -> dict[str, Any]:
    references: dict[Any, Any] = {}
    inline: dict[Any, Any] = {}
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
    labels: dict[Any, Any] = {}
    for span in useful:
        labels.setdefault((span["path"], span["source_sha256"]), []).append(
            (span["start_byte"], span["end_byte"])
        )
    labels = {k: union(v) for k, v in labels.items()}

    def size(values: Any) -> int:
        return sum(end - start for rows in values.values() for start, end in rows)

    def covered(values: Any) -> int:
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


def body_and_sources(raw: Any, baseline: bool) -> tuple[Any, list[Any], int]:
    sdk = json.loads(raw)
    body = json.loads(sdk["content"][0]["text"])
    if body["status"] == "error":
        return body, [], 0
    data = body["data"]
    if baseline:
        sources = [candidate["source"] for candidate in data["candidates"]]
        return body, sources, len(data["candidates"])
    return body, [s for atom in data["bundles"] for s in atom["sources"]], len(data["bundles"])
