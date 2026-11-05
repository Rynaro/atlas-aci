"""ACI-S27: strict parsing, closed rejections, limits, source roots and overlay validation."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_s27_support import (
    T1,
    T2,
    basic,
    example,
    make_world,
    manifest_for,
    otlp,
    raw_of,
    run_cli,
    sid,
    span_json,
    write_inputs,
)

from atlas_aci.importers import otel_json as otel
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import Side

DEFAULT = otel.normalize_limits()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s27-rej"))


def reason(function, *args) -> str:
    with pytest.raises(otel.OtelRejected) as info:
        function(*args)
    assert str(info.value) == "otel_rejected: " + info.value.reason
    return info.value.reason


def manifest_reason(raw) -> str:
    return reason(otel.parse_manifest, raw_of(raw))


def artifact_reason(raw: bytes, limits=DEFAULT, **patch) -> str:
    return reason(otel.parse_artifact, raw, manifest_for(raw, **patch), limits)


def changed(path, value):
    document = example()
    target = document
    for part in path[:-1]:
        target = target[part]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return document


def nested(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


MANIFEST_INVALID = {
    "extra key": {**example(), "extra": 1},
    "extra nested key": changed(("run", "other"), 1),
    "missing format": changed(("format",), KeyError),
    "missing profile": changed(("profile",), KeyError),
    "missing artifact": changed(("artifact",), KeyError),
    "missing run": changed(("run",), KeyError),
    "missing producer": changed(("producer",), KeyError),
    "missing build": changed(("build",), KeyError),
    "missing roots": changed(("source_roots",), KeyError),
    "missing sha": changed(("artifact", "sha256"), KeyError),
    "wrong format": changed(("format",), "atlas-otel-provenance.v2"),
    "run newline": changed(("run", "id"), "r\n"),
    "run arabic digit": changed(("run", "id"), "r\u0663"),
    "run empty": changed(("run", "id"), ""),
    "run long": changed(("run", "id"), "r" * 129),
    "build space": changed(("build", "id"), "a b"),
    "uppercase snapshot": changed(("build", "snapshot_id"), "AB" * 32),
    "short snapshot": changed(("build", "snapshot_id"), "ab" * 31),
    "uppercase sha": changed(("artifact", "sha256"), "AB" * 32),
    "bool bytes": changed(("artifact", "bytes"), True),
    "zero bytes": changed(("artifact", "bytes"), 0),
    "huge bytes": changed(("artifact", "bytes"), 268435457),
    "float bytes": changed(("artifact", "bytes"), 10.0),
    "producer non ascii": changed(("producer", "name"), "caf\u00e9"),
    "producer empty": changed(("producer", "version"), ""),
    "producer long": changed(("producer", "name"), "x" * 129),
    "roots not a list": changed(("source_roots",), {}),
    "17 roots": changed(("source_roots",), [{"from": f"/r{n}", "to": ""} for n in range(17)]),
    "duplicate from": changed(("source_roots",), [{"from": "/a", "to": ""}] * 2),
    "root extra key": changed(("source_roots",), [{"from": "/a", "to": "", "x": 1}]),
    "root missing to": changed(("source_roots",), [{"from": "/a"}]),
    "to dotdot": changed(("source_roots",), [{"from": "/a", "to": "x/../y"}]),
    "to absolute": changed(("source_roots",), [{"from": "/a", "to": "/x"}]),
    "to nul": changed(("source_roots",), [{"from": "/a", "to": "x\u0000y"}]),
    "to newline": changed(("source_roots",), [{"from": "/a", "to": "x\ny"}]),
    "to long": changed(("source_roots",), [{"from": "/a", "to": "x" * 1025}]),
    "to backslash": changed(("source_roots",), [{"from": "/a", "to": "x\\y"}]),
    "from trailing slash": changed(("source_roots",), [{"from": "/a/", "to": ""}]),
    "from dotdot": changed(("source_roots",), [{"from": "/a/../b", "to": ""}]),
    "from dot": changed(("source_roots",), [{"from": "/a/./b", "to": ""}]),
    "from double slash": changed(("source_roots",), [{"from": "//a", "to": ""}]),
    "from empty segment": changed(("source_roots",), [{"from": "a//b", "to": ""}]),
    "from backslash": changed(("source_roots",), [{"from": "C:\\a", "to": ""}]),
    "from nul": changed(("source_roots",), [{"from": "/a\u0000b", "to": ""}]),
    "from del": changed(("source_roots",), [{"from": "/a\u007fb", "to": ""}]),
    "from long": changed(("source_roots",), [{"from": "/" + "a" * 1024, "to": ""}]),
    "from not text": changed(("source_roots",), [{"from": 1, "to": ""}]),
    "profile not text": changed(("profile",), 5),
    "profile null": changed(("profile",), None),
}


@pytest.mark.parametrize("label", sorted(MANIFEST_INVALID))
def test_otel_manifest_shape_is_closed(label):
    assert manifest_reason(MANIFEST_INVALID[label]) == "manifest_invalid"


def test_otel_manifest_raw_rejections():
    good = raw_of(example())
    assert manifest_reason(b"[]") == "manifest_invalid"
    assert manifest_reason(b"") == "manifest_invalid"
    assert manifest_reason(b"\xef\xbb\xbf" + good) == "manifest_invalid"
    assert manifest_reason(good + b" x") == "manifest_invalid"
    assert manifest_reason(good.replace(b'"run"', b'"run":{"id":"a"},"run"', 1)) == (
        "manifest_invalid"
    )
    assert manifest_reason(b'{"format": NaN}') == "manifest_invalid"
    assert manifest_reason(b"\xff" + good) == "manifest_invalid"
    assert manifest_reason(nested(33)) == "manifest_invalid"
    assert manifest_reason(nested(30000)) == "manifest_invalid"
    assert manifest_reason(b"x" * 65537) == "manifest_too_large"
    assert manifest_reason(b" " * 65537 + good) == "manifest_too_large"
    assert reason(otel.parse_manifest, raw_of(changed(("profile",), "other"))) == (
        "unsupported_profile"
    )
    broken = {**changed(("profile",), "other"), "extra": 1}
    assert manifest_reason(broken) == "manifest_invalid"  # shape is checked before the profile
    assert otel.parse_manifest(b" " * 65000 + good)["run"] == {"id": "run-2026-11-05-001"}


def test_otel_manifest_accepts_the_documented_shapes():
    roots = [{"from": f"/r{n}", "to": ""} for n in range(16)]
    parsed = otel.parse_manifest(raw_of(changed(("source_roots",), roots)))
    assert len(parsed["source_roots"]) == 16
    odd = [
        {"from": "", "to": ""},
        {"from": "/", "to": "sub"},
        {"from": "/srv/\u00e9", "to": "a/b"},
    ]
    assert otel.parse_manifest(raw_of(changed(("source_roots",), odd)))["source_roots"] == odd
    assert otel.parse_manifest(raw_of(changed(("source_roots",), [])))["source_roots"] == []
    assert otel.parse_manifest(raw_of(changed(("artifact", "bytes"), 268435456)))
    assert otel.parse_manifest(raw_of(changed(("source_roots",), [{"from": "a" * 1024, "to": ""}])))


def test_otel_artifact_digest_is_checked_before_parsing():
    document = basic()
    flipped = bytes([document[0] ^ 1]) + document[1:]
    assert reason(otel.parse_artifact, flipped, manifest_for(document), DEFAULT) == (
        "artifact_digest_mismatch"
    )
    wrong_size = manifest_for(document)
    wrong_size["artifact"]["bytes"] += 1
    assert reason(otel.parse_artifact, document, wrong_size, DEFAULT) == "artifact_digest_mismatch"
    for junk in (b"not json at all", b"\xff\xfe\x00", b"[" * 100000, b"\xef\xbb\xbf{}"):
        assert reason(otel.parse_artifact, junk, manifest_for(document), DEFAULT) == (
            "artifact_digest_mismatch"
        )
        # The same bytes with a matching digest reach the parser instead.
        assert artifact_reason(junk) in {
            "artifact_invalid_json",
            "nesting_too_deep",
            "artifact_not_otlp_traces",
        }
    limits = otel.normalize_limits({"max_artifact_bytes": 10})
    assert reason(otel.parse_artifact, document, manifest_for(document), limits) == (
        "artifact_too_large"
    )
    assert reason(otel.parse_artifact, document, manifest_for(b"x"), limits) == "artifact_too_large"


INVALID_JSON = {
    "bom": b"\xef\xbb\xbf" + otlp(),
    "nan": b'{"resourceSpans": [NaN]}',
    "infinity": b'{"resourceSpans": [-Infinity]}',
    "huge float": b'{"resourceSpans": [], "x": 1e999}',
    "duplicate key": b'{"resourceSpans": [], "resourceSpans": []}',
    "nested duplicate": b'{"resourceSpans": [{"a": 1, "a": 2}]}',
    "surrogate": b'{"resourceSpans": [], "x": "\\ud800"}',
    "surrogate key": b'{"resourceSpans": [], "\\udc00": 1}',
    "non utf8": b'{"resourceSpans": [], "x": "\xff"}',
    "json lines": otlp() + b"\n" + otlp() + b"\n",
    "trailing data": otlp() + b" x",
    "long int": b'{"resourceSpans": [], "x": 1' + b"0" * 4300 + b"}",
    "empty": b"",
    "truncated": otlp()[:-3],
}


@pytest.mark.parametrize("label", sorted(INVALID_JSON))
def test_otel_artifact_json_is_strict(label):
    assert artifact_reason(INVALID_JSON[label]) == "artifact_invalid_json"


def test_otel_artifact_json_accepts_its_boundaries():
    shortest = b'{"resourceSpans": [], "x": 1' + b"0" * 4299 + b"}"
    assert otel.parse_artifact(shortest, manifest_for(shortest), DEFAULT) == []
    deep = b'{"resourceSpans": [], "x": ' + nested(30) + b"}"  # depth 32 in all
    assert otel.parse_artifact(deep, manifest_for(deep), DEFAULT) == []
    quoted = b'{"resourceSpans": [], "x": "' + b"[{" * 500 + b'"}'
    assert otel.parse_artifact(quoted, manifest_for(quoted), DEFAULT) == []
    escaped = b'{"resourceSpans": [], "x": "\\"[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[["}'
    assert otel.parse_artifact(escaped, manifest_for(escaped), DEFAULT) == []


def test_otel_nesting_is_bounded_by_bytes_not_recursion():
    assert otel.MAX_NESTING_DEPTH == 32
    assert artifact_reason(nested(32)) == "artifact_not_otlp_traces"
    assert artifact_reason(nested(33)) == "nesting_too_deep"
    assert artifact_reason(nested(100000)) == "nesting_too_deep"
    assert artifact_reason(b"{" * 40) == "nesting_too_deep"


NOT_OTLP = {
    "array root": b"[]",
    "scalar root": b"1",
    "no spans key": b"{}",
    "snake case": b'{"resource_spans": []}',
    "resourceSpans object": b'{"resourceSpans": {}}',
    "resourceSpans null": b'{"resourceSpans": null}',
    "resource item scalar": b'{"resourceSpans": [1]}',
    "scopeSpans object": b'{"resourceSpans": [{"scopeSpans": {}}]}',
    "scopeSpans null": b'{"resourceSpans": [{"scopeSpans": null}]}',
    "scope item scalar": b'{"resourceSpans": [{"scopeSpans": [1]}]}',
    "spans object": b'{"resourceSpans": [{"scopeSpans": [{"spans": {}}]}]}',
    "span scalar": b'{"resourceSpans": [{"scopeSpans": [{"spans": [1]}]}]}',
    "legacy library spans": b'{"resourceSpans": [{"instrumentationLibrarySpans": []}]}',
    "legacy beside scope": (
        b'{"resourceSpans": [{"scopeSpans": [], "instrumentationLibrarySpans": []}]}'
    ),
}


@pytest.mark.parametrize("label", sorted(NOT_OTLP))
def test_otel_artifact_shape_is_otlp_traces(label):
    assert artifact_reason(NOT_OTLP[label]) == "artifact_not_otlp_traces"


def test_otel_counts_and_limits_apply_before_span_work():
    two = otlp(span_json(1), span_json(2))
    one = otel.normalize_limits({"max_spans": 1})
    assert reason(otel.parse_artifact, two, manifest_for(two), one) == "span_limit_exceeded"
    first_invalid = otlp({"spanId": sid(1)}, span_json(2))
    assert reason(otel.parse_artifact, first_invalid, manifest_for(first_invalid), one) == (
        "span_limit_exceeded"
    )
    assert artifact_reason(first_invalid) == "invalid_trace_id"
    plenty = otel.normalize_limits({"max_spans": 2})
    assert len(otel.parse_artifact(two, manifest_for(two), plenty)) == 2
    many = otlp(span_json(1, path="/a", line=1, extra={}))
    document = json.loads(many)
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    spans[0]["attributes"].append({"key": "x", "value": {"stringValue": "y"}})
    heavy = json.dumps(document).encode()
    tight = otel.normalize_limits({"max_attributes_per_span": 1})
    assert reason(otel.parse_artifact, heavy, manifest_for(heavy), tight) == (
        "attribute_limit_exceeded"
    )
    ok = otel.normalize_limits({"max_attributes_per_span": 3})
    assert len(otel.parse_artifact(heavy, manifest_for(heavy), ok)) == 1


def with_span(**changes) -> bytes:
    base = span_json(1)
    for key, value in changes.items():
        if value is KeyError:
            base.pop(key, None)
        else:
            base[key] = value
    return otlp(base)


BAD_TRACE = ["a" * 31, "0" * 32, "a" * 33, "q83vEjRWeJCrze8SNFZ4kA==", "g" * 32, 5, None, "", True]
BAD_SPAN = ["a" * 15, "0" * 16, "a" * 17, "q83vEjRWeJA=", "g" * 16, 5, None, "", False]
BAD_PARENT = ["a" * 15, "0" * 16, "a" * 17, "q83vEjRWeJA=", None, 5, "g" * 16, {}, []]


def test_otel_identifiers_are_validated():
    for value in (*BAD_TRACE, "a" * 31 + "\n", "\u0663" * 32):
        assert artifact_reason(with_span(traceId=value)) == "invalid_trace_id", value
    assert artifact_reason(with_span(traceId=KeyError)) == "invalid_trace_id"
    for value in (*BAD_SPAN, "a" * 15 + "\n", "\u0663" * 16):
        assert artifact_reason(with_span(spanId=value)) == "invalid_span_id", value
    assert artifact_reason(with_span(spanId=KeyError)) == "invalid_span_id"
    for value in BAD_PARENT:
        assert artifact_reason(with_span(parentSpanId=value)) == "invalid_parent_span_id", value
    spans = otel.parse_artifact(
        with_span(traceId="AB" * 16, spanId="CD" * 8, parentSpanId="EF" * 8),
        manifest_for(with_span(traceId="AB" * 16, spanId="CD" * 8, parentSpanId="EF" * 8)),
        DEFAULT,
    )
    assert (spans[0].trace_id, spans[0].span_id, spans[0].parent_span_id) == (
        "ab" * 16,
        "cd" * 8,
        "ef" * 8,
    )
    for value in ("", KeyError):
        raw = with_span(parentSpanId=value)
        assert otel.parse_artifact(raw, manifest_for(raw), DEFAULT)[0].parent_span_id is None


def test_otel_times_status_and_attributes_are_validated():
    U64 = 2**64 - 1
    bad_times = [
        "+1", "01", "1_0", " 1", "1\n", "\u0663", "-1", "0", 0, -1, 1.5, True, None, [], {},
        "1" + "0" * 20, str(2**64), 2**64, "", "1.0", "0x1", "1e3",
    ]  # fmt: skip
    for value in bad_times:
        assert artifact_reason(with_span(startTimeUnixNano=value)) == "invalid_span", value
        assert artifact_reason(with_span(endTimeUnixNano=value)) == "invalid_span", value
    assert artifact_reason(with_span(startTimeUnixNano=KeyError)) == "invalid_span"
    assert artifact_reason(with_span(endTimeUnixNano=KeyError)) == "invalid_span"
    assert artifact_reason(with_span(startTimeUnixNano="20", endTimeUnixNano="10")) == (
        "invalid_span"
    )
    assert artifact_reason(with_span(startTimeUnixNano=21, endTimeUnixNano=20)) == "invalid_span"
    for start, end in (("1", "1"), (str(U64), str(U64)), (1, U64), ("5", 6)):
        raw = with_span(startTimeUnixNano=start, endTimeUnixNano=end)
        span = otel.parse_artifact(raw, manifest_for(raw), DEFAULT)[0]
        assert (span.start, span.end) == (str(start), str(end))
    for value in (3, -1, "STATUS_CODE_OK", True, 1.0, None, [], "1"):
        assert artifact_reason(with_span(status={"code": value})) == "invalid_span", value
    for value in ([], "ok", 1, None):
        assert artifact_reason(with_span(status=value)) == "invalid_span", value
    for value in ({}, "x", 1, None, [1], [{}], [{"key": 1}], [{"key": None}], ["a"], [[]]):
        assert artifact_reason(with_span(attributes=value)) == "invalid_span", value
    for status in (KeyError, {}, {"code": 0}, {"code": 2, "message": "ignored"}):
        raw = with_span(status=status)
        parsed = otel.parse_artifact(raw, manifest_for(raw), DEFAULT)[0]
        assert parsed.status_code in (0, 2)
    raw = with_span(attributes=KeyError)
    assert otel.parse_artifact(raw, manifest_for(raw), DEFAULT)[0].location_state == "missing"


def test_otel_source_location_states():
    def location(attributes):
        raw = with_span(attributes=attributes)
        span = otel.parse_artifact(raw, manifest_for(raw), DEFAULT)[0]
        return span.location_state, span.declared_path, span.line, span.legacy

    path = {"key": "code.file.path", "value": {"stringValue": "/a/b.py"}}
    line = {"key": "code.line.number", "value": {"intValue": "7"}}
    assert location([path, line]) == ("present", "/a/b.py", 7, False)
    assert location([path]) == ("present", "/a/b.py", None, False)
    assert location([path, {**line, "value": {"intValue": 7}}])[0] == "present"
    assert location([]) == ("missing", None, None, False)
    assert location([line]) == ("missing", None, 7, False)
    legacy = {"key": "code.filepath", "value": {"stringValue": "/a/b.py"}}
    assert location([legacy]) == ("missing", None, None, True)
    assert location([{"key": "code.lineno", "value": {"intValue": "1"}}])[3] is True
    bad_lines = [
        {"stringValue": "7"}, {"intValue": "0"}, {"intValue": 0}, {"intValue": "2147483648"},
        {"intValue": "07"}, {"intValue": 1.5}, {"intValue": True}, {"intValue": "7", "x": 1},
        {"doubleValue": 7}, {}, None, "7",
    ]  # fmt: skip
    for value in bad_lines:
        assert location([path, {"key": "code.line.number", "value": value}])[0] == "malformed"
    bad_paths = [
        {"stringValue": ""}, {"stringValue": "a\u0000b"}, {"stringValue": "a\nb"},
        {"stringValue": "x" * 4097}, {"stringValue": 5}, {"intValue": "1"}, {}, None, "x",
        {"stringValue": "a", "extra": 1},
    ]  # fmt: skip
    for value in bad_paths:
        state = location([{"key": "code.file.path", "value": value}, line])
        assert state[0] == "malformed" and state[1] is None, value
    assert location([path, path])[0] == "malformed"
    assert location([path, line, line])[0] == "malformed"
    assert location([{"key": "code.file.path"}])[0] == "malformed"
    boundary = {"key": "code.file.path", "value": {"stringValue": "x" * 4096}}
    assert location([boundary])[0] == "present"
    assert location([path, {**line, "value": {"intValue": "2147483647"}}])[2] == 2147483647


def test_otel_duplicates_and_cycles_are_rejected():
    mixed = otlp(span_json(1, trace="AB" * 16), span_json(1, trace="ab" * 16))
    assert artifact_reason(mixed) == "duplicate_span"
    assert artifact_reason(otlp(span_json(1), span_json(1))) == "duplicate_span"
    other = otlp(span_json(1, trace=T1), span_json(1, trace=T2))
    assert len(otel.parse_artifact(other, manifest_for(other), DEFAULT)) == 2
    assert artifact_reason(otlp(span_json(1, parent=1))) == "parent_cycle"
    assert artifact_reason(otlp(span_json(1, parent=2), span_json(2, parent=1))) == "parent_cycle"
    three = otlp(span_json(1, parent=3), span_json(2, parent=1), span_json(3, parent=2))
    assert artifact_reason(three) == "parent_cycle"
    tail = otlp(span_json(9, parent=1), span_json(1, parent=2), span_json(2, parent=1))
    assert artifact_reason(tail) == "parent_cycle"
    chain = otlp(span_json(3, parent=2), span_json(2, parent=1), span_json(1))
    assert len(otel.parse_artifact(chain, manifest_for(chain), DEFAULT)) == 3
    cross = otlp(span_json(1, trace=T1, parent=2), span_json(2, trace=T2, parent=1))
    assert len(otel.parse_artifact(cross, manifest_for(cross), DEFAULT)) == 2  # not in-trace loops
    first = otlp({"spanId": sid(1)}, span_json(2), span_json(2))
    assert artifact_reason(first) == "invalid_trace_id"  # first failure in document order
    ordered = otlp(span_json(2), span_json(2), {"spanId": sid(1)})
    assert artifact_reason(ordered) == "duplicate_span"
    deep = [span_json(n + 2, parent=n + 1 if n else None) for n in range(2000)]
    long_chain = json.dumps(
        {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {"spans": [dict(s, spanId="%016x" % (i + 1)) for i, s in enumerate(deep)]}
                    ]
                }
            ]
        }
    )
    assert long_chain  # a 2000 long parent chain below is walked iteratively
    ids = ["%016x" % (n + 1) for n in range(2000)]
    spans = [
        {**span_json(1), "spanId": ids[n], **({"parentSpanId": ids[n - 1]} if n else {})}
        for n in range(2000)
    ]
    raw = otlp(*spans)
    assert len(otel.parse_artifact(raw, manifest_for(raw), DEFAULT)) == 2000


def test_otel_limits_are_normalized():
    assert otel.normalize_limits() == otel.Limits(33554432, 100000, 512)
    assert otel.normalize_limits({"max_spans": None}) == otel.Limits()
    for name, (low, high) in otel.LIMIT_RANGES.items():
        assert getattr(otel.normalize_limits({name: low}), name) == low
        assert getattr(otel.normalize_limits({name: high}), name) == high
        for bad in (low - 1, high + 1, True, False, "5", 1.5, [], -1):
            with pytest.raises(otel.OtelUsageError) as info:
                otel.normalize_limits({name: bad})
            assert info.value.limit == name
    with pytest.raises(otel.OtelUsageError):
        otel.normalize_limits({"unknown": 1})


def test_otel_cli_limit_errors_are_fixed_text(world, tmp_path):
    manifest, artifact = write_inputs(tmp_path, basic(), example())
    for flag, name, bad in (
        ("--max-artifact-bytes", "max_artifact_bytes", "0"),
        ("--max-artifact-bytes", "max_artifact_bytes", "268435457"),
        ("--max-spans", "max_spans", "0"),
        ("--max-spans", "max_spans", "1000001"),
        ("--max-attributes-per-span", "max_attributes_per_span", "0"),
        ("--max-attributes-per-span", "max_attributes_per_span", "4097"),
    ):
        result = run_cli(world, manifest, artifact, tmp_path / "o.json", flag, bad)
        assert result.exit_code == 2 and result.stdout_bytes == b"", (flag, bad)
        assert f"Error: Invalid OpenTelemetry import limit: {name}\n" in result.stderr
    assert not (tmp_path / "o.json").exists()


def cli_case(world, tmp_path, manifest, artifact, *extra, graph=True):
    paths = write_inputs(tmp_path, artifact, manifest)
    result = run_cli(world, *paths, tmp_path / "out.json", *extra, graph=graph)
    assert result.stdout_bytes == b"" and not (tmp_path / "out.json").exists()
    assert str(tmp_path) not in result.stderr and "S27SENTINEL" not in result.stderr
    assert not list(tmp_path.glob(".atlas-runtime-overlay-*"))
    return result


def test_otel_cli_rejections_are_fixed_text(world, tmp_path):
    cases = {
        "manifest_too_large": (b"x" * 65537, basic(), ()),
        "manifest_invalid": (b"{}", basic(), ()),
        "unsupported_profile": (changed(("profile",), "other"), basic(), ()),
        "artifact_too_large": (example(), basic(), ("--max-artifact-bytes", "10")),
        "artifact_digest_mismatch": (example(), b"x" + basic(), ()),
        "artifact_invalid_json": (manifest_for(b"{x"), b"{x", ()),
        "nesting_too_deep": (manifest_for(nested(33)), nested(33), ()),
        "artifact_not_otlp_traces": (manifest_for(b"[]"), b"[]", ()),
        "span_limit_exceeded": (
            manifest_for(otlp(span_json(1), span_json(2))),
            otlp(span_json(1), span_json(2)),
            ("--max-spans", "1"),
        ),
        "attribute_limit_exceeded": (
            manifest_for(basic()),
            basic(),
            ("--max-attributes-per-span", "1"),
        ),
        "invalid_trace_id": (manifest_for(with_span(traceId="x")), with_span(traceId="x"), ()),
        "invalid_span_id": (manifest_for(with_span(spanId="x")), with_span(spanId="x"), ()),
        "invalid_parent_span_id": (
            manifest_for(with_span(parentSpanId="x")),
            with_span(parentSpanId="x"),
            (),
        ),
        "invalid_span": (
            manifest_for(with_span(startTimeUnixNano="x")),
            with_span(startTimeUnixNano="x"),
            (),
        ),
        "duplicate_span": (
            manifest_for(otlp(span_json(1), span_json(1))),
            otlp(span_json(1), span_json(1)),
            (),
        ),
        "parent_cycle": (
            manifest_for(otlp(span_json(1, parent=1))),
            otlp(span_json(1, parent=1)),
            (),
        ),
    }
    assert set(cases) == set(otel.REJECTIONS)
    for code, (manifest, artifact, extra) in cases.items():
        result = cli_case(world, tmp_path / code, manifest, artifact, *extra)
        assert result.exit_code == 1, (code, result.stderr)
        assert result.stderr == f"Error: OpenTelemetry import rejected: {code}\n", code
    ghost = tmp_path / "ghost-graph"
    manifest, artifact = write_inputs(tmp_path / "g", basic(), example())
    result = run_cli(world, manifest, artifact, tmp_path / "g/out.json")
    assert result.exit_code == 0
    args = ["import-otel", "--repo", str(world.repo), "--graph-root", str(ghost)]
    args += ["--manifest", str(manifest), "--output", str(tmp_path / "g/o2.json"), str(artifact)]
    from click.testing import CliRunner

    from atlas_aci.__main__ import cli

    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: OpenTelemetry import rejected: index_unavailable\n"
    assert str(tmp_path) not in result.stderr
    blocked = tmp_path / "occupied"
    blocked.write_text("x")
    args[args.index(str(ghost))] = str(blocked)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2 and result.stdout_bytes == b""
    assert "Error: The repository cannot be opened as a graph namespace." in result.stderr
    assert str(tmp_path) not in result.stderr


@pytest.mark.parametrize(
    ("declared", "roots", "expected"),
    [
        ("/srv/app/x.py", [{"from": "/srv/app", "to": ""}], "x.py"),
        ("/srv/application/x.py", [{"from": "/srv/app", "to": ""}], None),
        ("/srv/app", [{"from": "/srv/app", "to": ""}], None),
        ("/srv/app/", [{"from": "/srv/app", "to": ""}], None),
        (
            "/srv/app/a/b.py",
            [{"from": "/srv", "to": "r"}, {"from": "/srv/app", "to": "s"}],
            "s/a/b.py",
        ),
        (
            "/srv/app/a/b.py",
            [{"from": "/srv/app", "to": "s"}, {"from": "/srv", "to": "r"}],
            "s/a/b.py",
        ),
        ("/srv/b.py", [{"from": "/srv", "to": "r"}, {"from": "/srv/app", "to": "s"}], "r/b.py"),
        ("app/x.py", [{"from": "", "to": ""}], "app/x.py"),
        ("app/x.py", [{"from": "", "to": "lib"}], "lib/app/x.py"),
        ("/app/x.py", [{"from": "", "to": ""}], None),
        ("app/x.py", [{"from": "/", "to": ""}], None),
        ("/app/x.py", [{"from": "/", "to": ""}], "app/x.py"),
        ("/app/x.py", [{"from": "/", "to": "r"}], "r/app/x.py"),
        ("/", [{"from": "/", "to": ""}], None),
        ("/srv/app/../x.py", [{"from": "/srv/app", "to": ""}], None),
        ("/srv/app/a/../x.py", [{"from": "/srv/app", "to": ""}], None),
        ("/srv/app/./x.py", [{"from": "/srv/app", "to": ""}], None),
        ("/srv/app//x.py", [{"from": "/srv/app", "to": ""}], None),
        ("/srv/app/x/", [{"from": "/srv/app", "to": ""}], None),
        ("C:\\x", [{"from": "", "to": ""}], None),
        ("C:\\x", [{"from": "/", "to": ""}], None),
        ("C:/x/y.py", [{"from": "C:/x", "to": ""}], None),
        ("c:/x/y.py", [{"from": "", "to": ""}], None),
        ("a\\b.py", [{"from": "", "to": ""}], None),
        ("/srv/app/x.py", [{"from": "/SRV/APP", "to": ""}], None),
        ("/srv/app/x.py", [], None),
        ("", [{"from": "", "to": ""}], None),
        ("/srv/\u00e9/x.py", [{"from": "/srv/\u00e9", "to": ""}], "x.py"),
        ("/srv/e\u0301/x.py", [{"from": "/srv/\u00e9", "to": ""}], None),
    ],
)
def test_otel_source_root_mapping(declared, roots, expected):
    assert otel.map_source_path(declared, roots) == expected


@pytest.fixture(scope="module")
def sealed(world):
    raw = raw_of(manifest_for(basic(), world.snapshot_id))
    return runtime.import_otel(world.side, raw, basic())


def reseal(overlay):
    rest = {k: v for k, v in overlay.items() if k != "overlay_id"}
    overlay["overlay_id"] = runtime._identity(rest)
    return overlay


def tampered(overlay, edit, seal=True):
    copied = copy.deepcopy(overlay)
    edit(copied)
    return reseal(copied) if seal else copied


def obs(overlay, number, trace=T1):
    return next(
        o for o in overlay["observations"] if o["trace_id"] == trace and o["span_id"] == sid(number)
    )


def test_otel_validate_overlay_rejects_every_tampering(sealed, world):
    runtime.validate_overlay(sealed)
    mismatch = runtime.import_otel(world.side, raw_of(manifest_for(basic(), "ab" * 32)), basic())
    runtime.validate_overlay(mismatch)

    def swap(i, j):
        def edit(o):
            o["observations"][i], o["observations"][j] = o["observations"][j], o["observations"][i]

        return edit

    edits = {
        "unsorted observations": swap(0, 1),
        "duplicate observation": lambda o: o["observations"].append(
            copy.deepcopy(o["observations"][0])
        ),
        "missing evidence_coverage": lambda o: obs(o, 1).pop("evidence_coverage"),
        "wrong evidence_coverage": lambda o: obs(o, 1).update(evidence_coverage="complete"),
        "relation coverage": lambda o: o["relations"][0].update(evidence_coverage="complete"),
        "unknown classification": lambda o: obs(o, 3)["linkage"].update(classification="linked_x"),
        "dangling relation": lambda o: o["relations"][0].update(child_id="0" * 64),
        "relation semantics": lambda o: o["relations"][0].update(semantics="call"),
        "relation kind": lambda o: o["relations"][0].update(relation="calls"),
        "extra relation": lambda o: o["relations"].append(copy.deepcopy(o["relations"][0])),
        "dropped relation": lambda o: o["relations"].pop(),
        "relation id": lambda o: o["relations"][0].update(id="0" * 64),
        "observation id": lambda o: obs(o, 1).update(id="0" * 64),
        "extra top key": lambda o: o.update(extra=1),
        "extra observation key": lambda o: obs(o, 1).update(name="x"),
        "extra linkage key": lambda o: obs(o, 1)["linkage"].update(attributes={}),
        "extra symbol key": lambda o: obs(o, 1)["linkage"]["symbol"].update(kind2="x"),
        "coverage": lambda o: o["coverage"].update(completeness="complete"),
        "limitations": lambda o: o.update(limitations=o["limitations"][:-1]),
        "importer": lambda o: o["importer"].update(version="x"),
        "producer attestation": lambda o: o["producer"].update(attestation="verified"),
        "matched flipped": lambda o: o["snapshot"].update(matched=False),
        "supplied differs": lambda o: o["snapshot"].update(supplied_id="ab" * 32),
        "snapshot_id null when matched": lambda o: obs(o, 1).update(snapshot_id=None),
        "snapshot_id foreign": lambda o: obs(o, 1).update(snapshot_id="ab" * 32),
        "mismatch class in matched overlay": lambda o: obs(o, 3)["linkage"].update(
            classification="unlinked_build_mismatch", path=None, file_entity_id=None
        ),
        "linked without path": lambda o: obs(o, 3)["linkage"].update(path=None),
        "unlinked with path": lambda o: obs(o, 7)["linkage"].update(path="app/x.py"),
        "symbol outside the line": lambda o: obs(o, 1)["linkage"]["symbol"].update(
            line_start=9, line_end=9
        ),
        "file with symbol": lambda o: obs(o, 3)["linkage"].update(symbol={}),
        "summary linked": lambda o: o["summary"].update(
            linked_file=o["summary"]["linked_file"] + 1
        ),
        "summary reason": lambda o: o["summary"]["unlinked_by_reason"].update(path_outside_root=0),
        "summary total": lambda o: o["summary"].update(observations_total=99),
        "summary traces": lambda o: o["summary"].update(traces_total=5),
        "summary roots": lambda o: o["summary"].update(root_spans=0),
        "summary orphans": lambda o: o["summary"].update(parent_not_in_artifact=0),
        "summary relations": lambda o: o["summary"].update(relations_total=0),
        "summary legacy": lambda o: o["summary"].update(legacy_source_attributes_ignored=99),
        "summary locations missing": lambda o: o["summary"]["source_locations"].update(
            missing_source_location=0, present=12
        ),
        "summary locations present": lambda o: o["summary"]["source_locations"].update(present=9),
        "summary locations key": lambda o: o["summary"]["source_locations"].update(extra=0),
        "summary coverage": lambda o: o["summary"].update(evidence_coverage="complete"),
        "bool status": lambda o: obs(o, 1).update(status_code=True),
        "bad status": lambda o: obs(o, 1).update(status_code=3),
        "bad time": lambda o: obs(o, 1).update(start_time_unix_nano="+1"),
        "time order": lambda o: obs(o, 1).update(end_time_unix_nano="1"),
        "line zero": lambda o: obs(o, 1)["source"].update(line=0),
        "missing with path": lambda o: obs(o, 4)["source"].update(declared_path="/x"),
        "present without path": lambda o: obs(o, 3)["source"].update(declared_path=None),
        "bad run": lambda o: obs(o, 1).update(run_id="other"),
        "bad artifact digest": lambda o: obs(o, 1).update(artifact_sha256="0" * 64),
        "roots unsorted": lambda o: o["source_roots"].reverse(),
        "roots invalid": lambda o: o["source_roots"].append({"from": "a/../b", "to": ""}),
        "cycle": lambda o: (
            obs(o, 1).update(parent_span_id=sid(2)),
            o["relations"].clear(),
        ),
    }
    for label, edit in edits.items():
        with pytest.raises(runtime.OverlayRejected) as info:
            runtime.validate_overlay(tampered(sealed, edit))
        assert info.value.reason == "invalid_overlay", label
    with pytest.raises(runtime.OverlayRejected):
        runtime.validate_overlay(
            tampered(sealed, lambda o: obs(o, 1).update(run_id="x"), seal=False)
        )
    for overlay in (sealed, mismatch):
        with pytest.raises(runtime.OverlayRejected):
            runtime.validate_overlay({**overlay, "overlay_id": "0" * 64})
    mismatch_edits = {
        "mismatch with linkage": lambda o: obs(o, 3)["linkage"].update(
            classification="linked_file"
        ),
        "mismatch with snapshot": lambda o: obs(o, 3).update(
            snapshot_id=o["snapshot"]["supplied_id"]
        ),
        "mismatch declared": lambda o: o["snapshot"].update(
            declared_id=o["snapshot"]["supplied_id"]
        ),
        "mismatch class unrelated": lambda o: obs(o, 3)["linkage"].update(
            classification="unlinked_path_outside_root"
        ),
    }
    for label, edit in mismatch_edits.items():
        with pytest.raises(runtime.OverlayRejected):
            runtime.validate_overlay(tampered(mismatch, edit))
        assert label
    for junk in (None, [], "x", 1, {}):
        with pytest.raises(runtime.OverlayRejected):
            runtime.validate_overlay(junk)


def test_otel_empty_artifacts_are_valid_overlays(world):
    for body in (
        b'{"resourceSpans": []}',
        b'{"resourceSpans": [{}]}',
        b'{"resourceSpans": [{"scopeSpans": [{}]}]}',
    ):
        overlay = runtime.import_otel(
            world.side, raw_of(manifest_for(body, world.snapshot_id)), body
        )
        runtime.validate_overlay(overlay)
        assert overlay["observations"] == [] and overlay["relations"] == []
        assert overlay["summary"]["observations_total"] == 0
        assert overlay["summary"]["traces_total"] == 0
        assert overlay["summary"]["source_locations"] == {
            "present": 0,
            "missing_source_location": 0,
            "malformed_source_location": 0,
        }
        result = runtime.runtime_paths(overlay)
        assert result["paths"] == [] and result["paths_total"] == 0
        assert result["paths_truncated"] is False
        runtime.validate_runtime_paths(result)
        assert result["evidence_coverage"] == "observed_sample"


def test_otel_runtime_paths_are_validated_and_bounded(world, sealed):
    result = runtime.runtime_paths(sealed)
    runtime.validate_runtime_paths(result)
    edits = {
        "format": lambda r: r.update(format="x"),
        "coverage": lambda r: r.update(evidence_coverage="complete"),
        "completeness": lambda r: r.update(completeness="complete"),
        "semantics": lambda r: r.update(semantics="call"),
        "extra key": lambda r: r.update(extra=1),
        "missing key": lambda r: r.pop("paths_total"),
        "path coverage": lambda r: r["paths"][0].update(evidence_coverage="complete"),
        "path missing coverage": lambda r: r["paths"][0].pop("evidence_coverage"),
        "path starts_at": lambda r: r["paths"][0].update(starts_at="root"),
        "path extra": lambda r: r["paths"][0].update(name="x"),
        "path length": lambda r: r["paths"][0].update(
            span_ids=r["paths"][0]["span_ids"][:-1] or []
        ),
        "limitations": lambda r: r.update(limitations=[]),
        "limits": lambda r: r["limits"].update(max_paths=0),
        "truncated type": lambda r: r.update(paths_truncated="no"),
        "too many paths": lambda r: r["limits"].update(max_paths=1),
    }
    for label, edit in edits.items():
        copied = copy.deepcopy(result)
        edit(copied)
        with pytest.raises(runtime.OverlayRejected):
            runtime.validate_runtime_paths(copied)
        assert label
    for name, bad in (
        ("max_paths", 0),
        ("max_paths", 100001),
        ("max_path_length", 0),
        ("max_path_length", 4097),
        ("max_paths", True),
        ("max_path_length", "1"),
    ):
        with pytest.raises(runtime.OverlayUsageError):
            runtime.runtime_paths(sealed, **{name: bad})
    assert (
        runtime.runtime_paths(sealed, max_paths=100000, max_path_length=4096)["paths_total"] == 11
    )
    with pytest.raises(runtime.OverlayRejected):
        runtime.runtime_paths({**sealed, "overlay_id": "0" * 64})
    ids = ["%016x" % (n + 1) for n in range(300)]
    spans = [
        {**span_json(1), "spanId": ids[n], **({"parentSpanId": ids[n - 1]} if n else {})}
        for n in range(300)
    ]
    chain = otlp(*spans)
    overlay = runtime.import_otel(world.side, raw_of(manifest_for(chain, "ab" * 32)), chain)
    cut = runtime.runtime_paths(overlay)
    assert cut["paths_total"] == 1 and cut["paths_truncated"] is True
    assert len(cut["paths"]) == 1 and len(cut["paths"][0]["span_ids"]) == 256
    assert cut["paths"][0]["truncated"] is True
    full = runtime.runtime_paths(overlay, max_path_length=300)
    assert len(full["paths"][0]["span_ids"]) == 300 and full["paths_truncated"] is False
    fan = otlp(span_json(1), *[span_json(n, parent=1) for n in range(2, 40)])
    wide = runtime.import_otel(world.side, raw_of(manifest_for(fan, "ab" * 32)), fan)
    capped = runtime.runtime_paths(wide, max_paths=5)
    assert len(capped["paths"]) == 5 and capped["paths_total"] == 38
    assert capped["paths_truncated"] is True
    assert hashlib.sha256(b"").hexdigest() and Side
