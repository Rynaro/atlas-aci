"""Throwaway copy of the D3 profile (pre-scan, collector, nodeid mapping, derived outcome).

Stdlib only, so it runs under any interpreter (local dev image 3.12, python:3.12-slim,
python:3.14). Day-1 (c) and (d) qualification only; the shipped code is atlas_aci.feedback.outcomes.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

DECLARATIONS = (
    b'<?xml version="1.0" encoding="utf-8"?>',
    b'<?xml version="1.0" encoding="UTF-8"?>',
)
MAX_DEPTH = 8
NODEID = re.compile(
    r"([A-Za-z0-9_/-]{1,512})\.py((?:::[A-Za-z_][A-Za-z0-9_]{0,127}){1,8})(\[[ -~]{0,256}\])?"
)


class Invalid(Exception):
    pass


def prescan(raw: bytes) -> bool:
    if not raw.startswith(DECLARATIONS):
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if "\x00" in text or "<!" in text:
        return False
    return "<?" not in text[len(DECLARATIONS[0].decode()) :]


class Collector:
    def __init__(self, limit: int = 100000):
        self.limit = limit
        self.stack: list[str] = []
        self.cases: list[tuple[str, str, list[str]]] = []

    def start(self, tag, attrib):
        if "{" in tag or len(self.stack) >= MAX_DEPTH:
            raise Invalid("tag")
        parent = self.stack[-1] if self.stack else None
        if parent is None:
            if tag not in ("testsuites", "testsuite"):
                raise Invalid("root")
        elif tag == "testsuite" and parent != "testsuites":
            raise Invalid("suite")
        elif tag == "testcase":
            if parent != "testsuite":
                raise Invalid("case")
            classname, name = attrib.get("classname"), attrib.get("name")
            if not isinstance(classname, str) or not isinstance(name, str):
                raise Invalid("attrs")
            if len(self.cases) >= self.limit:
                raise Invalid("many")
            self.cases.append((classname, name, []))
        elif parent == "testcase" and tag in ("failure", "error", "skipped"):
            self.cases[-1][2].append(tag)
        self.stack.append(tag)

    def end(self, tag):
        self.stack.pop()

    def data(self, data):
        pass

    def close(self):
        return self.cases


def parse(raw: bytes):
    collector = Collector()
    parser = ET.XMLParser(target=collector)
    try:
        parser.feed(raw)
        return parser.close()
    except Exception as exc:  # collector errors and ParseError alike
        raise Invalid(type(exc).__name__) from None


def pair(nodeid: str) -> tuple[str, str] | None:
    match = NODEID.fullmatch(nodeid)
    if match is None:
        return None
    head, bracket, params = nodeid.partition("[")
    parts = head.split("::")
    classname = ".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]])
    return classname, parts[-1] + bracket + params


def derive(cases, nodeid: str) -> str:
    wanted = pair(nodeid)
    found = [c for c in cases if wanted is not None and (c[0], c[1]) == wanted]
    if not found:
        return "test_not_in_artifact"
    if len(found) > 1:
        return "outcome_not_objective"
    children = found[0][2]
    if not children:
        return "passed"
    if children == ["failure"]:
        return "failed"
    return "outcome_not_objective"


def classify(raw: bytes) -> dict:
    """The observable outcome of one artifact: a closed detail or the derived outcome table."""
    if not prescan(raw):
        return {"result": "artifact_invalid", "stage": "prescan"}
    try:
        cases = parse(raw)
    except Invalid as exc:
        return {"result": "artifact_invalid", "stage": "parse:" + str(exc)}
    names = sorted({f"{c[0]}::{c[1]}" for c in cases})
    return {
        "result": "ok",
        "testcases": len(cases),
        "table": {f"{c[0]}::{c[1]}": derive_pair(cases, c[0], c[1]) for c in cases},
        "names": names,
    }


def derive_pair(cases, classname, name):
    found = [c for c in cases if (c[0], c[1]) == (classname, name)]
    if len(found) > 1:
        return "outcome_not_objective"
    children = found[0][2]
    if not children:
        return "passed"
    return "failed" if children == ["failure"] else "outcome_not_objective"
