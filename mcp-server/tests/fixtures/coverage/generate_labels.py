"""Write one run's expected execution labels, independent of Atlas.

Deliberately imports nothing from ``atlas_aci``. It runs inside the producer container,
reads the producer's own ``.coverage`` data file through coverage.py's public
``CoverageData.contexts_by_lineno`` API (not the JSON report the importer parses), and
derives every byte span from the project's source files with plain byte arithmetic.

Line numbering is cross-checked against three independent oracles; any disagreement is a
hard failure (exit 3), which is the S21 stop rule:

* ``bytes.splitlines()`` (the oracle for the terminator-only splitter the importer uses);
* ``tokenize`` row/column text against the split lines (the tokenizer's physical rows);
* ``ast`` statement start rows against the recorded ``executed_lines`` (compile's rows).

    python generate_labels.py --project DIR --data .coverage --report coverage.json \
        --run a --out labels-a.json
"""

import argparse
import ast
import hashlib
import io
import json
import re
import sys
import tokenize
from pathlib import Path

from coverage.data import CoverageData

SPLIT = re.compile(rb"\r\n|\r|\n")
PHASES = {"run", "setup", "teardown"}


def line_spans(source: bytes) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for match in SPLIT.finditer(source):
        spans.append((pos, match.start()))
        pos = match.end()
    if pos < len(source):
        spans.append((pos, len(source)))
    return spans


def oracle(path: str, source: bytes, spans: list[tuple[int, int]], executed: set[int]) -> dict:
    split = [source[a:b] for a, b in spans]
    if split != source.splitlines():
        sys.exit(f"STOP: {path}: terminator splitter disagrees with bytes.splitlines()")
    texts = [line.decode("utf-8") for line in split]
    covered_rows: set[int] = set()
    mismatches = 0
    tokens = 0
    for token in tokenize.tokenize(io.BytesIO(source).readline):
        if token.type in (tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        (row, col), (row_end, col_end) = token.start, token.end
        if token.type in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            continue
        tokens += 1
        covered_rows.update(range(row, row_end + 1))
        if row == row_end and token.string and texts[row - 1][col:col_end] != token.string:
            mismatches += 1
    if mismatches:
        sys.exit(f"STOP: {path}: {mismatches} tokenize rows disagree with the split lines")
    statement_rows: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.stmt):
            statement_rows.add(node.lineno)
            statement_rows.update(d.lineno for d in getattr(node, "decorator_list", []))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                head = split[node.lineno - 1][node.col_offset :]
                keyword = (
                    b"async def"
                    if isinstance(node, ast.AsyncFunctionDef)
                    else b"class"
                    if isinstance(node, ast.ClassDef)
                    else b"def"
                )
                if not head.startswith(keyword):
                    sys.exit(f"STOP: {path}: ast line {node.lineno} disagrees with the bytes")
    stray = sorted(executed - statement_rows)
    if stray:
        sys.exit(f"STOP: {path}: executed lines {stray} are not ast statement starts")
    return {
        "lines": len(spans),
        "tokens": tokens,
        "token_rows": len(covered_rows),
        "str_splitlines_disagrees": source.decode("utf-8").splitlines() != texts,
        "_covered_rows": covered_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    project = Path(args.project)
    report_raw = Path(args.report).read_bytes()
    report = json.loads(report_raw)
    data = CoverageData(basename=args.data)
    data.read()
    rows, files, unattributed = [], {}, {}
    measured = {}
    for name in data.measured_files():
        path = Path(name)
        relative = str(path.relative_to(project)) if path.is_absolute() else str(path)
        measured[relative] = name
    if set(measured) != set(report["files"]):
        sys.exit("STOP: the data file and the JSON report measure different files")
    for relative in sorted(measured):
        source = (project / relative).read_bytes()
        spans = line_spans(source)
        executed = set(report["files"][relative]["executed_lines"])
        checked = oracle(relative, source, spans, executed)
        covered_rows = checked.pop("_covered_rows")
        files[relative] = {
            "sha256": hashlib.sha256(source).hexdigest(),
            "missing_lines": report["files"][relative]["missing_lines"],
            "executed_lines_without_context_entry": sorted(
                executed - {int(k) for k in report["files"][relative]["contexts"]}
            ),
            **checked,
        }
        for line, contexts in sorted(data.contexts_by_lineno(measured[relative]).items()):
            if not 1 <= line <= len(spans) or line not in covered_rows:
                sys.exit(f"STOP: {relative}:{line} is not a physical tokenizer row")
            by_test: dict[str, set[str]] = {}
            for context in contexts:
                if context == "":
                    continue
                nodeid, _, phase = context.rpartition("|")
                if phase not in PHASES:
                    sys.exit(f"STOP: unexpected context {context!r}")
                by_test.setdefault(nodeid, set()).add(phase)
            if not by_test:
                unattributed.setdefault(relative, []).append(line)
            start, end = spans[line - 1]
            for nodeid, phases in sorted(by_test.items()):
                rows.append(
                    {
                        "nodeid": nodeid,
                        "path": relative,
                        "line": line,
                        "phases": sorted(phases),
                        "start_byte": start,
                        "end_byte": end,
                        "text": source[start:end].decode("utf-8"),
                    }
                )
    labels = {
        "run": args.run,
        "artifact_sha256": hashlib.sha256(report_raw).hexdigest(),
        "tool": {"name": "coverage.py", "version": report["meta"]["version"]},
        "source": "coverage.data.CoverageData.contexts_by_lineno",
        "files": files,
        "unattributed_lines": unattributed,
        "rows": rows,
    }
    Path(args.out).write_text(json.dumps(labels, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{args.run}: {len(rows)} rows over {len(files)} files")


if __name__ == "__main__":
    main()
