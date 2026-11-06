"""Shared ACI-S42 helpers: loaders for the manifest, the aggregator and the recorder, committed and
fresh evidence, record mutations and a bounded recorder runner.

The recorder and the aggregator are loaded by path under unique module names. Nothing here mocks
the behaviour under test: a "fresh" run is the real recorder driving real ``serve-shared``, stdio,
HTTP and ``atlas-aci jobs`` subprocesses. The file defines no test function.
"""

from __future__ import annotations

import copy
import gzip
import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts" / "eval"
AGGREGATOR = SCRIPTS / "s42_aggregate.py"
RECORDER = SCRIPTS / "s42_record.py"
MANIFEST = ROOT / "atlas" / "evals" / "shared" / "manifest.v1.json"
EVIDENCE = ROOT / "docs" / "evidence" / "s42"
COMMITTED_RAW = EVIDENCE / "raw"
REPORT_JSON = EVIDENCE / "readiness-report.json"
REPORT_MD = EVIDENCE / "readiness-report.md"
EVIDENCE_README = EVIDENCE / "README.md"
RECORDER_TIMEOUT_S = 600.0  # 300 s recorder deadline plus a cold grammar download (<= 240 s)
CLI_TIMEOUT_S = 90.0

_MODULES: dict[str, Any] = {}


def _load(name: str, path: Path) -> Any:
    if name not in _MODULES:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return _MODULES[name]


def aggregator() -> Any:
    return _load("s42_test_aggregator", AGGREGATOR)


def recorder() -> Any:
    return _load("s42_test_recorder", RECORDER)


def manifest() -> dict[str, Any]:
    """The committed manifest, deep-copied (callers may mutate their copy)."""
    return copy.deepcopy(aggregator().load_manifest(MANIFEST))  # type: ignore[no-any-return]


def records_from(directory: Path) -> list[dict[str, Any]]:
    return copy.deepcopy(aggregator().read_raw_dir(directory))  # type: ignore[no-any-return]


def committed_records() -> list[dict[str, Any]]:
    return records_from(COMMITTED_RAW)


def aggregate(records: list[dict[str, Any]], manifest_doc: dict[str, Any] | None = None) -> Any:
    return aggregator().aggregate(manifest_doc if manifest_doc is not None else manifest(), records)


def find(records: list[dict[str, Any]], kind: str, unit: str) -> dict[str, Any]:
    matches = [r for r in records if r["kind"] == kind and r["unit"] == unit]
    assert len(matches) == 1, (kind, unit, len(matches))
    return matches[0]


def without(records: list[dict[str, Any]], kind: str, unit: str) -> list[dict[str, Any]]:
    kept = [copy.deepcopy(r) for r in records if (r["kind"], r["unit"]) != (kind, unit)]
    assert len(kept) == len(records) - 1, (kind, unit)
    return kept


def mutated(
    records: list[dict[str, Any]], kind: str, unit: str, change: Any
) -> list[dict[str, Any]]:
    """A deep copy of the records with ``change(record)`` applied to one of them."""
    copied = copy.deepcopy(records)
    change(find(copied, kind, unit))
    return copied


def row_of(record: dict[str, Any], case_id: str, phase: str | None = None) -> dict[str, Any]:
    rows = [
        r
        for r in record["body"]["rows"]
        if r["case_id"] == case_id and (phase is None or r["phase"] == phase)
    ]
    assert len(rows) == 1, (case_id, len(rows))
    return rows[0]


def write_dir(directory: Path, records: list[dict[str, Any]]) -> Path:
    """Write records the way the recorder does (canonical bytes, gzip level 9, mtime 0)."""
    directory.mkdir(parents=True, exist_ok=True)
    for record in records:
        name = f"{record['kind']}.{record['unit']}.json.gz"
        (directory / name).write_bytes(aggregator().gzip_canonical(record))
    return directory


def cli(*args: str, timeout: float = CLI_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AGGREGATOR), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT / "mcp-server"),
    )


def run_aggregator(raw: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return cli("--manifest", str(MANIFEST), "--raw", str(raw), "--out", str(out), *extra)


def run_recorder(out: Path, work: Path, *extra: str, timeout: float = RECORDER_TIMEOUT_S):
    """Run the real recorder in its own session; on a timeout the whole group is killed."""
    out.parent.mkdir(parents=True, exist_ok=True)
    log = out.parent / "recorder.stderr"
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_ACI_")}
    with open(log, "wb") as handle:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(RECORDER),
                "--manifest",
                str(MANIFEST),
                "--out",
                str(out),
                "--work",
                str(work),
                *extra,
            ],
            stdout=subprocess.DEVNULL,
            stderr=handle,
            env=env,
            cwd=str(ROOT / "mcp-server"),
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = -1
            _kill_group(proc.pid)
            proc.wait(timeout=30)
        finally:
            _kill_group(proc.pid)  # nothing of the recorder's group may outlive the run
    tail = log.read_bytes().decode("utf-8", errors="replace")[-1500:]
    return code, tail


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    time.sleep(0.05)


def gz(value: Any, level: int = 9) -> bytes:
    """gzip of the canonical bytes (another level gives other deflate bytes, same plain bytes)."""
    return gzip.compress(aggregator().canonical(value), compresslevel=level, mtime=0)


def independent_unauthorized(manifest_doc: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    """The D5 rule coded again from the manifest, without calling the aggregator."""
    cases = {(c["phase"], c["id"]): c for c in manifest_doc["isolation"]["cases"]}
    flagged = []
    for row in rows:
        case = cases.get((row["phase"], row["case_id"]))
        if case is None:  # a teeth re-run of a main deny case
            case = cases[("main", row["case_id"])]
        deny = case["expected"] == "deny"
        allowed = set() if deny else set(case["domains"])
        wrong = (
            (deny and row["outcome"] == "ok")
            or bool(set(row["canary_hits"]) - allowed)
            or (deny and case["nonce_carrier"] and row["server_seen"] is True)
        )
        if wrong:
            flagged.append(row["case_id"])
    return flagged


def synthetic_row(**fields: Any) -> dict[str, Any]:
    """A closed-shape isolation row for unit tests of the ledger rule."""
    row = {
        "case_id": "M01",
        "phase": "main",
        "principal": "p1",
        "route": "a",
        "request_kind": "tool",
        "tool": "search_text",
        "class": "control",
        "expected": "allow",
        "outcome": "ok",
        "http_status": 200,
        "canary_hits": ["A"],
        "server_seen": True,
        "witness_in_request": False,
    }
    row.update(fields)
    return row


def ready_word(text: str) -> list[str]:
    """Occurrences of the bare word ``ready`` (not ``already``, ``readiness`` or the verdict)."""
    import re

    return re.findall(r"\bready\b", text)
