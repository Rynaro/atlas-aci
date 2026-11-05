"""Day-1 (d): the artifact outcomes over fixtures and generated variants, one JSON per interpreter.

Usage: python qualify_expat.py OUTPUT.json   (stdlib only; run under each interpreter)
"""
import hashlib
import json
import platform
import pyexpat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import junit_probe as jp  # noqa: E402

FX = HERE.parents[2] / "mcp-server/tests/fixtures/feedback"
DECL = b'<?xml version="1.0" encoding="utf-8"?>'
CASE = b'<testcase classname="t.m" name="test_a" time="0.1" />'


def wrap(body: bytes, decl: bytes = DECL, root: bytes = b"testsuites") -> bytes:
    return decl + b"<" + root + b'><testsuite name="s">' + body + b"</testsuite></" + root + b">"


variants: dict[str, bytes] = {}
for path in sorted(FX.glob("*.xml")):
    variants["fixture:" + path.name] = path.read_bytes()
variants["pytest-capture"] = (HERE / "pytest-junit-capture.xml").read_bytes()
variants.update(
    {
        "ok": wrap(CASE),
        "ok-upper-decl": wrap(CASE, b'<?xml version="1.0" encoding="UTF-8"?>'),
        "ok-trailing-newline": wrap(CASE) + b"\n",
        "ok-leading-whitespace-after-decl": DECL + b"\n  " + wrap(CASE)[len(DECL):],
        "ok-testsuite-root": DECL + b'<testsuite name="s">' + CASE + b"</testsuite>",
        "bad-decl-single-quotes": wrap(CASE, b"<?xml version='1.0' encoding='utf-8'?>"),
        "bad-decl-latin1": wrap(CASE, b'<?xml version="1.0" encoding="iso-8859-1"?>'),
        "bad-decl-standalone": wrap(CASE, b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>'),
        "bom": b"\xef\xbb\xbf" + wrap(CASE),
        "nul": wrap(CASE + b"\x00"),
        "invalid-utf8": wrap(b'<testcase classname="t.\xff" name="a" />'),
        "overlong-utf8": wrap(b'<testcase classname="t.\xc0\xaf" name="a" />'),
        "surrogate-utf8": wrap(b'<testcase classname="t.\xed\xa0\x80" name="a" />'),
        "control-char": wrap(b'<testcase classname="t.\x01" name="a" />'),
        "char-ref-ok": wrap(b'<testcase classname="t.&#65;" name="a&#x42;" />'),
        "char-ref-nul": wrap(b'<testcase classname="t.&#0;" name="a" />'),
        "char-ref-control": wrap(b'<testcase classname="t.&#1;" name="a" />'),
        "char-ref-big": wrap(b'<testcase classname="t.&#x110000;" name="a" />'),
        "entity-predefined": wrap(b'<testcase classname="t.&lt;&gt;&amp;&quot;&apos;" name="a" />'),
        "entity-undefined": wrap(b'<testcase classname="t.&foo;" name="a" />'),
        "entity-unterminated": wrap(b'<testcase classname="t.&amp" name="a" />'),
        "dup-attr": wrap(b'<testcase classname="t.m" classname="t.n" name="a" />'),
        "attr-no-quotes": wrap(b"<testcase classname=t.m name=a />"),
        "attr-lt-in-value": wrap(b'<testcase classname="t.<m" name="a" />'),
        "mismatched": wrap(b'<testcase classname="t.m" name="a"></testsuite>'),
        "truncated": wrap(CASE)[:-5],
        "empty": b"",
        "decl-only": DECL,
        "two-roots": wrap(CASE) + wrap(CASE),
        "junk-after-root": wrap(CASE) + b"junk",
        "text-before-root": DECL + b"junk" + wrap(CASE)[len(DECL):],
        "cdata-like-in-text": wrap(b"<system-out>]]&gt;</system-out>" + CASE),
        "bare-close-bracket-run": wrap(b"<system-out>]]></system-out>" + CASE),
        "testcase-missing-name": wrap(b'<testcase classname="t.m" />'),
        "testcase-empty-attrs": wrap(b'<testcase classname="" name="" />'),
        "unknown-root": DECL + b"<other>" + CASE + b"</other>",
        "suite-in-suite": wrap(b'<testsuite name="n">' + CASE + b"</testsuite>"),
        "case-in-case": wrap(b'<testcase classname="a" name="b">' + CASE + b"</testcase>"),
        "case-under-properties": wrap(b"<properties>" + CASE + b"</properties>"),
        "depth-8": wrap(b'<testcase classname="t" name="a"><a><b><c><d><e /></d></c></b></a></testcase>'),
        "depth-9": wrap(b'<testcase classname="t" name="a"><a><b><c><d><e><f /></e></d></c></b></a></testcase>'),
        "xml-prefix-attr": wrap(b'<testcase xml:lang="en" classname="t" name="a" />'),
        "colon-tag": wrap(b'<x:testcase classname="t" name="a" />'),
        "xmlns-attr": wrap(b'<testcase xmlns:x="urn:x" classname="t" name="a" />'),
        "pi-in-body": wrap(b"<?pi x?>" + CASE),
        "doctype-after-decl": DECL + b"<!DOCTYPE testsuites>" + wrap(CASE)[len(DECL):],
        "nested-failure": wrap(
            b'<testcase classname="t" name="a"><properties><failure /></properties></testcase>'
        ),
        "failure-passed-mix": wrap(
            b'<testcase classname="t" name="a"><failure /><error /></testcase>'
        ),
        "wide": wrap(CASE * 2000),
    }
)
results = {name: jp.classify(raw) for name, raw in sorted(variants.items())}
report = {
    "python": platform.python_version(),
    "expat": pyexpat.EXPAT_VERSION,
    "variants": len(results),
    "results_sha256": hashlib.sha256(
        json.dumps(results, sort_keys=True).encode("utf-8")
    ).hexdigest(),
    "results": results,
}
Path(sys.argv[1]).write_text(json.dumps(report, sort_keys=True, indent=1) + "\n")
print(report["python"], report["expat"], report["variants"], report["results_sha256"])
