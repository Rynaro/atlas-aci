"""Write labels.json: the expected SCIP symbols and occurrences, independent of Atlas.

Deliberately imports nothing from ``atlas_aci``. It decodes ``index.scip`` with the
official protoc-generated bindings (``scip_pb2.py``) and derives every byte span from
the project's source files with plain Python ``str`` arithmetic (UTF-16 code units from
``encode("utf-16-le")``, UTF-8 columns from ``encode()``, code points from ``len``).

    python generate_labels.py index.scip project labels.json
"""

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scip_pb2


def descriptor_kind(symbol: str) -> str:
    if symbol.startswith("local "):
        return "local"
    descriptors = symbol.split(" ", 4)[4]
    if descriptors.endswith(")."):
        return "method"
    if descriptors.endswith(")"):
        return "parameter"
    return {"/": "namespace", "#": "type", ".": "term"}[descriptors[-1]]


DESCRIPTOR = re.compile(
    r"\((?P<param>[^)]+)\)|(?P<name>`(?:[^`]|``)+`|[\w+\-$]+)(?P<suffix>\(\)\.|[/#.:!])"
)


def descriptor_name(symbol: str, text: str) -> str:
    if symbol.startswith("local "):
        return text
    last = list(DESCRIPTOR.finditer(symbol.split(" ", 4)[4]))[-1]
    if last["param"]:
        return last["param"]
    name = last["name"]
    return name[1:-1].replace("``", "`") if name.startswith("`") else name


def lines_of(source: bytes) -> list[str]:
    # The fixture only uses LF and CRLF terminators.
    return [line.rstrip("\r") for line in source.decode("utf-8").split("\n")]


def convert(lines, line_no, utf16_col):
    line = lines[line_no]
    units = line.encode("utf-16-le")
    prefix = units[: 2 * utf16_col].decode("utf-16-le")  # raises on a split surrogate
    return len(prefix), len(prefix.encode("utf-8")), len(line.encode("utf-16-le")) // 2


def main(index_path: str, project: str, out: str) -> None:
    raw = Path(index_path).read_bytes()
    index = scip_pb2.Index()
    index.ParseFromString(raw)
    labels = {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "tool": [index.metadata.tool_info.name, index.metadata.tool_info.version],
        "position_encoding": "utf-16",
        "documents": {},
        "definitions": [],
        "references": [],
    }
    defined = set()
    rows = []
    for document in sorted(index.documents, key=lambda d: d.relative_path):
        source = (Path(project) / document.relative_path).read_bytes()
        lines = lines_of(source)
        starts, offset = [], 0
        for line in source.decode("utf-8").split("\n"):
            starts.append(offset)
            offset += len(line.encode("utf-8")) + 1
        labels["documents"][document.relative_path] = hashlib.sha256(source).hexdigest()
        for occurrence in document.occurrences:
            r = list(occurrence.range)
            sl, sc, el, ec = (r[0], r[1], r[0], r[2]) if len(r) == 3 else tuple(r)
            s_cp, s_b, _ = convert(lines, sl, sc)
            _e_cp, e_b, _ = convert(lines, el, ec)
            start, end = starts[sl] + s_b, starts[el] + e_b
            text = source[start:end].decode("utf-8")
            row = {
                "path": document.relative_path,
                "symbol": occurrence.symbol,
                "range": r,
                "line": sl + 1,
                "utf16_column": sc,
                "utf8_byte_column": s_b,
                "codepoint_column": s_cp,
                "start_byte": start,
                "end_byte": end,
                "text": text,
                "unicode": s_b != sc,
            }
            if occurrence.symbol_roles & 1:
                row["kind"] = descriptor_kind(occurrence.symbol)
                row["name"] = descriptor_name(occurrence.symbol, text)
                defined.add(occurrence.symbol)
            rows.append((occurrence.symbol_roles & 1, row))
    for is_definition, row in rows:
        if is_definition:
            labels["definitions"].append(row)
        else:
            row["resolution"] = "defined" if row["symbol"] in defined else "external"
            labels["references"].append(row)
    labels["counts"] = {
        "documents": len(labels["documents"]),
        "definitions": len(labels["definitions"]),
        "references": len(labels["references"]),
        "external_references": sum(r["resolution"] == "external" for r in labels["references"]),
        "unicode_rows": sum(r["unicode"] for r in rows_flat(labels)),
    }
    Path(out).write_text(json.dumps(labels, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def rows_flat(labels):
    return [*labels["definitions"], *labels["references"]]


if __name__ == "__main__":
    main(*sys.argv[1:4])
