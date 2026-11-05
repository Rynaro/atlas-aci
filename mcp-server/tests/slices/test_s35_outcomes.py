"""ACI-S35 outcomes: the JUnit profile, the envelope, the record, every closed rejection."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from test_s35_support import (
    NODE_A,
    NODE_ABSENT,
    NODE_B,
    NODE_C,
    NODE_D,
    NODE_E,
    NODE_F,
    NODE_G,
    NODE_H,
    NODE_I,
    NODE_J,
    NODE_K,
    NODE_L,
    PRODUCER,
    World,
    annotation,
    assert_rejected,
    assert_usage,
    build_world,
    bundle_ids,
    claim,
    cli_run,
    domain_digest,
    dumps,
    envelope,
    fixture,
    import_args,
    import_ok,
    produce_record,
    rank_args,
    request_arguments,
    run_import,
    run_rank,
    serial,
    store_of,
)

from atlas_aci.feedback import outcomes as fo
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import Side

ARTIFACT = fixture("junit-outcomes.xml")
REJECT = "Feedback import rejected: "
HEX = "a" * 64
LIMITS = fo.normalize_limits()


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return build_world(tmp_path_factory.mktemp("s35o"))


@pytest.fixture(scope="module")
def r1(world):
    raw, record = produce_record(world, "before")
    return raw, record


# ---- the pre-scan and the parse -----------------------------------------------------------------

GOOD = fixture("junit-second.xml")
DECL = b'<?xml version="1.0" encoding="utf-8"?>'


def prescan_rows():
    return [
        ("valid", GOOD, True),
        ("upper-case encoding", GOOD.replace(b'"utf-8"', b'"UTF-8"', 1), True),
        ("trailing newline", GOOD + b"\n", True),
        ("no declaration", fixture("junit-nodecl.xml"), False),
        ("byte order mark", b"\xef\xbb\xbf" + GOOD, False),
        (
            "single-quoted declaration",
            GOOD.replace(DECL, b"<?xml version='1.0' encoding='utf-8'?>"),
            False,
        ),
        ("other encoding", GOOD.replace(b'"utf-8"', b'"iso-8859-1"', 1), False),
        ("standalone", GOOD.replace(b'"utf-8"?>', b'"utf-8" standalone="yes"?>', 1), False),
        ("leading space", b" " + GOOD, False),
        ("nul byte", GOOD.replace(b"fixture-two", b"fixture\x00two"), False),
        ("invalid utf-8", GOOD.replace(b"fixture-two", b"fixture\xfftwo"), False),
        ("overlong utf-8", GOOD.replace(b"fixture-two", b"fixture\xc0\xaftwo"), False),
        ("doctype with entities", fixture("junit-doctype.xml"), False),
        ("comment", fixture("junit-comment.xml"), False),
        (
            "cdata",
            GOOD.replace(b"<testcase", b"<system-out><![CDATA[x]]></system-out><testcase", 1),
            False,
        ),
        ("processing instruction", fixture("junit-pi.xml"), False),
        ("second declaration", GOOD + GOOD, False),
        ("empty", b"", False),
    ]


@pytest.mark.parametrize(
    ("label", "raw", "ok"), prescan_rows(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_prescan_rule_table(label, raw, ok):
    if ok:
        fo.prescan_junit(raw)
        assert len(fo.parse_junit(raw, 100)) == 2
    else:
        with pytest.raises(fo.ArtifactInvalid):
            fo.prescan_junit(raw)


def wrap(body: bytes, root: bytes = b"testsuites") -> bytes:
    return DECL + b"<" + root + b'><testsuite name="s">' + body + b"</testsuite></" + root + b">"


CASE = b'<testcase classname="t.m" name="test_a" time="0.1" />'


def parse_rows():
    deep = b'<testcase classname="t" name="a">%s</testcase>'
    return [
        ("depth 8", wrap(deep % b"<a><b><c><d><e /></d></c></b></a>"), True),
        ("depth 9", fixture("junit-depth.xml"), False),
        ("namespace", fixture("junit-namespace.xml"), False),
        ("testcase under the root", fixture("junit-misplaced.xml"), False),
        ("testsuite as root", DECL + b'<testsuite name="s">' + CASE + b"</testsuite>", True),
        ("unknown root", DECL + b"<other>" + CASE + b"</other>", False),
        ("nested testsuite", wrap(b'<testsuite name="n">' + CASE + b"</testsuite>"), False),
        ("testcase in testcase", wrap(deep % CASE), False),
        ("testcase under properties", wrap(b"<properties>" + CASE + b"</properties>"), False),
        ("missing name", wrap(b'<testcase classname="t" />'), False),
        ("missing classname", wrap(b'<testcase name="t" />'), False),
        ("empty attributes", wrap(b'<testcase classname="" name="" />'), True),
        ("undefined entity", wrap(b'<testcase classname="t&foo;" name="a" />'), False),
        ("character reference to nul", wrap(b'<testcase classname="t&#0;" name="a" />'), False),
        ("predefined entities", wrap(b'<testcase classname="t&lt;&amp;" name="a" />'), True),
        ("duplicate attribute", wrap(b'<testcase classname="a" classname="b" name="a" />'), False),
        ("mismatched tags", wrap(b'<testcase classname="a" name="a"></testsuite>'), False),
        ("truncated", wrap(CASE)[:-5], False),
        ("junk after the root", wrap(CASE) + b"junk", False),
        ("control character", wrap(b'<testcase classname="t\x01" name="a" />'), False),
    ]


@pytest.mark.parametrize(
    ("label", "raw", "ok"), parse_rows(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_parse_rule_table(label, raw, ok):
    if ok:
        fo.prescan_junit(raw)
        assert fo.parse_junit(raw, 100)
    else:
        with pytest.raises(fo.ArtifactInvalid):
            fo.prescan_junit(raw)
            fo.parse_junit(raw, 100)


def test_the_testcase_limit_is_enforced_and_failures_must_be_direct_children():
    cases = fo.parse_junit(ARTIFACT, 100)
    assert len(cases) == 13
    with pytest.raises(fo.ArtifactInvalid):
        fo.parse_junit(ARTIFACT, 12)
    assert len(fo.parse_junit(ARTIFACT, 13)) == 13
    children = {c.name: c.children for c in cases}
    assert children["test_with_logs"] == ()
    assert children["test_nested_failure"] == ()
    assert children["test_declined"] == ("failure",)
    assert children["test_setup_error"] == ("error",)
    assert children["test_skipped"] == ("skipped",) == children["test_xfail"]
    assert children["test_mixed"] == ("failure", "error")


# ---- node ids -----------------------------------------------------------------------------------

SUPPORTED = [
    ("tests/test_pay.py::test_ok", ("tests.test_pay", "test_ok")),
    ("tests/test_pay.py::TestCards::test_ok[visa]", ("tests.test_pay.TestCards", "test_ok[visa]")),
    ("tests/test_pay.py::A::B::test_x", ("tests.test_pay.A.B", "test_x")),
    ("tests/test_pay.py::test_p[master-card.v1]", ("tests.test_pay", "test_p[master-card.v1]")),
    ("tests/test_pay.py::test_p[a b]", ("tests.test_pay", "test_p[a b]")),
    ("tests/test_pay.py::test_p[a::b]", ("tests.test_pay", "test_p[a::b]")),
    ('tests/test_pay.py::test_p[x<y&z"q]', ("tests.test_pay", 'test_p[x<y&z"q]')),
    ("tests/sub-dir/test_dash.py::test_a", ("tests.sub-dir.test_dash", "test_a")),
    ("test_top.py::test_a", ("test_top", "test_a")),
    ("a/b/c_d/test_x.py::test_a", ("a.b.c_d.test_x", "test_a")),
    ("tests/test_pay.py::test_p[]", ("tests.test_pay", "test_p[]")),
]
UNSUPPORTED = [
    "tests/my.pytools/test_a.py::test_x",
    "tests/a.b.py::test_x",
    "tests/test_a.pyc::test_x",
    "/abs/test_a.py::test_x",
    "tests/../test_a.py::test_x",
    "tests//test_a.py::test_x",
    "tests/test_a.py",
    "tests/test_a.py::",
    "tests/test_a.py::1bad",
    "tests\\test_a.py::t",
    "tests/test_a.py::t[a",
    "tests/t" + chr(233) + "st.py::t",
    "tests/test a.py::t",
    "tests/test_a.txt::t",
    "tests/test_a.py::t\n",
    ".py::t",
    "tests/test_a.py::" + "::".join(["a"] * 9),
    "tests/" + "a" * 600 + ".py::t",
    "tests/test_a.py::t[" + "x" * 1100 + "]",
    "tests/test_a.py::" + "a" * 129,
]


@pytest.mark.parametrize(("nodeid", "pair"), SUPPORTED)
def test_nodeid_pairs_follow_pytest(nodeid, pair):
    assert fo.valid_nodeid(nodeid)
    assert fo.nodeid_pair(nodeid) == pair


@pytest.mark.parametrize("nodeid", UNSUPPORTED)
def test_unsupported_nodeid_shapes_are_rejected(nodeid):
    assert not fo.valid_nodeid(nodeid)
    document = {
        **base_envelope(),
        "claims": [
            {
                "claim_id": "c",
                "selector": {"kind": "pytest_nodeid", "value": nodeid},
                "outcome": "passed",
                "results": [HEX],
                "source_files": {"app/a.py": HEX},
            }
        ],
    }
    with pytest.raises(fo.FeedbackRejected) as caught:
        fo.parse_envelope(json.dumps(document).encode(), LIMITS)
    assert caught.value.reason == "invalid_envelope"


def test_the_pairs_are_what_pytest_writes(tmp_path):
    """Run pytest once over the supported shapes and compare with the D3 mapping."""
    files = {
        "tests/test_pay.py": (
            "import pytest\n\n"
            "class TestCards:\n    def test_ok(self):\n        pass\n\n"
            "    class TestInner:\n        def test_deep(self):\n            pass\n\n"
            "@pytest.mark.parametrize('b', ['visa', 'master-card', 'v1.2', 'a b'])\n"
            "def test_p(b):\n    pass\n"
        ),
        "tests/sub-dir/test_dash.py": "def test_a():\n    pass\n",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "--import-mode=importlib",
            "--junitxml",
            "j.xml",
            "tests",
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout
    raw = (tmp_path / "j.xml").read_bytes()
    assert raw.startswith(fo.DECLARATIONS)
    fo.prescan_junit(raw)
    cases = fo.parse_junit(raw, 100)
    nodeids = [
        "tests/test_pay.py::TestCards::test_ok",
        "tests/test_pay.py::TestCards::TestInner::test_deep",
        "tests/test_pay.py::test_p[visa]",
        "tests/test_pay.py::test_p[master-card]",
        "tests/test_pay.py::test_p[v1.2]",
        "tests/test_pay.py::test_p[a b]",
        "tests/sub-dir/test_dash.py::test_a",
    ]
    assert sorted((c.classname, c.name) for c in cases) == sorted(
        fo.nodeid_pair(n) for n in nodeids
    )
    assert all(fo.derive_outcome(cases, n) == "passed" for n in nodeids)


DERIVED = [
    (NODE_A, "passed"),
    (NODE_B, "failed"),
    (NODE_C, "outcome_not_objective"),
    (NODE_D, "outcome_not_objective"),
    (NODE_E, "outcome_not_objective"),
    (NODE_F, "passed"),
    (NODE_G, "passed"),
    (NODE_H, "passed"),
    (NODE_I, "outcome_not_objective"),
    (NODE_J, "passed"),
    (NODE_K, "outcome_not_objective"),
    (NODE_L, "passed"),
    (NODE_ABSENT, "test_not_in_artifact"),
]


@pytest.mark.parametrize(("nodeid", "expected"), DERIVED)
def test_derived_outcomes(nodeid, expected):
    assert fo.derive_outcome(fo.parse_junit(ARTIFACT, 100), nodeid) == expected


# ---- the envelope -------------------------------------------------------------------------------


def base_envelope() -> dict:
    return {
        "format": fo.ENVELOPE_FORMAT,
        "retrieval_record_id": HEX,
        "snapshot_id": "b" * 64,
        "generation_id": "c" * 64,
        "run": None,
        "artifact": None,
        "claims": [],
        "annotations": [
            {
                "annotation_id": "n1",
                "kind": "useful",
                "results": ["d" * 64],
                "source_files": {"app/a.py": "e" * 64},
                "note": None,
            }
        ],
    }


def with_claim(**change) -> dict:
    item = {
        "claim_id": "c1",
        "selector": {"kind": "pytest_nodeid", "value": NODE_A},
        "outcome": "passed",
        "results": ["d" * 64],
        "source_files": {"app/a.py": "e" * 64},
    }
    return {**base_envelope(), "annotations": [], "claims": [{**item, **change}]}


def mutated(path: list, value) -> dict:
    document = base_envelope()
    node = document
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return document


def envelope_rows():
    wide = "x" * 513
    return [
        (
            "v2 format",
            {**base_envelope(), "format": "atlas-feedback-envelope.v2"},
            "unsupported_envelope_version",
        ),
        (
            "v2 with other keys",
            {"format": "atlas-feedback-envelope.v2", "x": 1},
            "unsupported_envelope_version",
        ),
        ("extra top key", {**base_envelope(), "extra": 1}, "invalid_envelope"),
        (
            "missing key",
            {k: v for k, v in base_envelope().items() if k != "run"},
            "invalid_envelope",
        ),
        (
            "no claims and no annotations",
            {**base_envelope(), "annotations": []},
            "invalid_envelope",
        ),
        ("bad record id", mutated(["retrieval_record_id"], "A" * 64), "invalid_envelope"),
        ("short snapshot id", mutated(["snapshot_id"], "a" * 63), "invalid_envelope"),
        ("claims not a list", {**base_envelope(), "claims": {}}, "invalid_envelope"),
        ("annotation extra key", mutated(["annotations", 0, "extra"], 1), "invalid_envelope"),
        ("annotation kind", mutated(["annotations", 0, "kind"], "other"), "invalid_envelope"),
        (
            "annotation id leading dash",
            mutated(["annotations", 0, "annotation_id"], "-x"),
            "invalid_envelope",
        ),
        (
            "annotation id too long",
            mutated(["annotations", 0, "annotation_id"], "a" * 65),
            "invalid_envelope",
        ),
        (
            "annotation id empty",
            mutated(["annotations", 0, "annotation_id"], ""),
            "invalid_envelope",
        ),
        ("note too long", mutated(["annotations", 0, "note"], wide), "invalid_envelope"),
        (
            "note not printable",
            mutated(["annotations", 0, "note"], "caf" + chr(233)),
            "invalid_envelope",
        ),
        ("note number", mutated(["annotations", 0, "note"], 1), "invalid_envelope"),
        ("results empty", mutated(["annotations", 0, "results"], []), "invalid_envelope"),
        (
            "results too many",
            mutated(["annotations", 0, "results"], [f"{i:064x}" for i in range(129)]),
            "invalid_envelope",
        ),
        (
            "results duplicate",
            mutated(["annotations", 0, "results"], [HEX, HEX]),
            "invalid_envelope",
        ),
        ("results not hex", mutated(["annotations", 0, "results"], ["g" * 64]), "invalid_envelope"),
        (
            "source file parent segment",
            mutated(["annotations", 0, "source_files"], {"../x.py": HEX}),
            "invalid_envelope",
        ),
        (
            "source file absolute",
            mutated(["annotations", 0, "source_files"], {"/x.py": HEX}),
            "invalid_envelope",
        ),
        (
            "source file backslash",
            mutated(["annotations", 0, "source_files"], {"a\\b.py": HEX}),
            "invalid_envelope",
        ),
        (
            "source file digest",
            mutated(["annotations", 0, "source_files"], {"a.py": "xyz"}),
            "invalid_envelope",
        ),
        (
            "source file path too long",
            mutated(["annotations", 0, "source_files"], {"a" * 4097: HEX}),
            "invalid_envelope",
        ),
        (
            "duplicate annotation id",
            {**base_envelope(), "annotations": [base_envelope()["annotations"][0]] * 2},
            "invalid_envelope",
        ),
        ("claim outcome success", with_claim(outcome="success"), "invalid_envelope"),
        ("claim outcome skipped", with_claim(outcome="skipped"), "invalid_envelope"),
        ("claim free text", with_claim(note="it passed"), "invalid_envelope"),
        (
            "claim selector kind",
            with_claim(selector={"kind": "other", "value": NODE_A}),
            "invalid_envelope",
        ),
        (
            "claim selector extra key",
            with_claim(selector={"kind": "pytest_nodeid", "value": NODE_A, "x": 1}),
            "invalid_envelope",
        ),
        ("claim id grammar", with_claim(claim_id="a b"), "invalid_envelope"),
        (
            "run producer name empty",
            {**base_envelope(), "run": {"run_id": HEX, "producer": {"name": "", "version": "1"}}},
            "invalid_envelope",
        ),
        (
            "run producer too long",
            {
                **base_envelope(),
                "run": {"run_id": HEX, "producer": {"name": "n" * 129, "version": "1"}},
            },
            "invalid_envelope",
        ),
        (
            "run producer not ascii",
            {
                **base_envelope(),
                "run": {"run_id": HEX, "producer": {"name": "caf" + chr(233), "version": "1"}},
            },
            "invalid_envelope",
        ),
        (
            "run extra key",
            {**base_envelope(), "run": {"run_id": HEX, "producer": PRODUCER, "x": 1}},
            "invalid_envelope",
        ),
        (
            "artifact bool bytes",
            {**base_envelope(), "artifact": {"profile": "p", "sha256": HEX, "bytes": True}},
            "invalid_envelope",
        ),
        (
            "artifact negative bytes",
            {**base_envelope(), "artifact": {"profile": "p", "sha256": HEX, "bytes": -1}},
            "invalid_envelope",
        ),
        (
            "artifact huge bytes",
            {**base_envelope(), "artifact": {"profile": "p", "sha256": HEX, "bytes": 2**31}},
            "invalid_envelope",
        ),
        (
            "artifact digest",
            {**base_envelope(), "artifact": {"profile": "p", "sha256": "A" * 64, "bytes": 1}},
            "invalid_envelope",
        ),
        ("not an object", [], "invalid_envelope"),
    ]


@pytest.mark.parametrize(
    ("label", "document", "reason"), envelope_rows(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_strict_envelope_rejections(label, document, reason):
    raw = json.dumps(document).encode()
    with pytest.raises(fo.FeedbackRejected) as caught:
        fo.parse_envelope(raw, LIMITS)
    assert (caught.value.reason, caught.value.detail) == (reason, None), label


def test_the_envelope_is_parsed_strictly_at_the_byte_level():
    good = json.dumps(base_envelope()).encode()
    assert fo.parse_envelope(good, LIMITS)["annotations"][0]["annotation_id"] == "n1"
    duplicated = good.replace(b'"run": null', b'"run": null, "run": null')
    surrogate = good.replace(b'"app/a.py"', b'"app/\\ud800.py"')
    rows = {
        "byte order mark": b"\xef\xbb\xbf" + good,
        "duplicate key": duplicated,
        "float": good.replace(b'"note": null', b'"note": 1.5'),
        "nan": good.replace(b'"note": null', b'"note": NaN'),
        "lone surrogate": surrogate,
        "not utf-8": good.replace(b"app", b"\xff\xfe"),
        "not json": b"claims",
        "empty": b"",
        "too deep": b"[" * 100000 + b"]" * 100000,
    }
    for label, raw in rows.items():
        with pytest.raises(fo.FeedbackRejected) as caught:
            fo.parse_envelope(raw, LIMITS)
        assert caught.value.reason == "invalid_envelope", label
    with pytest.raises(fo.FeedbackRejected) as caught:
        fo.parse_envelope(good, fo.normalize_limits({"max_envelope_bytes": len(good) - 1}))
    assert caught.value.reason == "envelope_too_large"
    two = {
        **base_envelope(),
        "annotations": [
            base_envelope()["annotations"][0],
            {**base_envelope()["annotations"][0], "annotation_id": "n2"},
        ],
    }
    with pytest.raises(fo.FeedbackRejected) as caught:
        fo.parse_envelope(json.dumps(two).encode(), fo.normalize_limits({"max_claims": 1}))
    assert caught.value.reason == "too_many_claims"
    # Order and labels never matter: claims sort by label, everything else is content.
    one = with_claim()
    parsed = fo.parse_envelope(json.dumps(one).encode(), LIMITS)
    assert parsed["claims"][0]["selector"] == {"kind": "pytest_nodeid", "value": NODE_A}


# ---- the retrieval record -----------------------------------------------------------------------


def test_the_record_loader_is_strict_and_never_leaks_an_exception(r1):
    raw, record = r1
    loaded = fo.load_record(raw)
    assert loaded.record_id == record["data"]["retrieval_record_id"]
    assert [b.bundle_id for b in loaded.bundles] == bundle_ids(record)
    assert [b.position for b in loaded.bundles] == list(range(len(loaded.bundles)))
    forged = json.loads(raw)
    forged["data"]["bundles"][0]["records"]["entity"]["name"] = "x"
    rows = {
        "edited": json.dumps(forged).encode(),
        "byte order mark": b"\xef\xbb\xbf" + raw,
        "duplicate key": raw.replace(b'{"bounds"', b'{"bounds":1,"bounds"', 1),
        "NaN constant": raw.replace(b'"offset":0', b'"offset":NaN', 1),
        "not utf-8": b"\xff\xfe",
        "not json": b"x",
        "a list": b"[]",
        "an error result": json.dumps({**json.loads(raw), "status": "error"}).encode(),
        "another operation": json.dumps({**json.loads(raw), "operation": "retrieve"}).encode(),
        "too deep": b"[" * 200000 + b"]" * 200000,
        "deep object": b'{"a":' * 100000 + b"1" + b"}" * 100000,
        "empty": b"",
    }
    for label, data in rows.items():
        with pytest.raises(fo.RecordInvalid):
            fo.load_record(data)
        assert label


# ---- every closed rejection ---------------------------------------------------------------------


class Proxy:
    """A real lease with chosen attributes replaced (the spy of the lease boundary)."""

    def __init__(self, reader, **replaced):
        self._reader = reader
        self._replaced = replaced

    def __getattr__(self, name):
        return self._replaced[name] if name in self._replaced else getattr(self._reader, name)


class NoRow:
    def execute(self, *args):
        return self

    def fetchone(self):
        return None


def spy(monkeypatch, **replaced):
    real = runtime.open_active
    monkeypatch.setattr(
        runtime, "open_active", lambda stack, side: Proxy(real(stack, side), **replaced)
    )


def attempt(
    world, document, record, artifact=ARTIFACT, store=None, limits=LIMITS, name="before", side=None
):
    """(reason, detail) of a rejected library import; fails when it is not rejected."""
    raw = document if isinstance(document, bytes) else dumps(document)
    with pytest.raises(fo.FeedbackRejected) as caught:
        fo.import_feedback(raw, record, artifact, store, side or world.side(name), limits)
    return caught.value.reason, caught.value.detail


def test_every_closed_rejection_is_reachable(world, r1, tmp_path, monkeypatch):
    raw, record = r1
    ids = bundle_ids(record)
    last = len(ids) - 1
    reached: set[tuple[str, str | None]] = set()

    def expect(got, reason, detail=None):
        assert got == (reason, detail)
        reached.add(got)

    good = envelope(record, [claim(record, "a", NODE_A, "passed", [last])], artifact=ARTIFACT)
    note_only = envelope(record, [], [annotation(record, "n", "useful", [2], None)])
    one = lambda nodeid, outcome, **kw: envelope(  # noqa: E731
        record, [claim(record, "c", nodeid, outcome, [last])], artifact=ARTIFACT, **kw
    )
    small = lambda **change: fo.normalize_limits(change)  # noqa: E731
    # sizes
    expect(attempt(world, good, raw, limits=small(max_envelope_bytes=10)), "envelope_too_large")
    expect(attempt(world, good, raw, limits=small(max_record_bytes=10)), "record_too_large")
    expect(attempt(world, good, raw, limits=small(max_artifact_bytes=10)), "artifact_too_large")
    expect(
        attempt(world, good, raw, store=b"x" * 20, limits=small(max_store_bytes=10)),
        "store_too_large",
    )
    # the envelope and the previous store
    expect(attempt(world, b"{}", raw), "invalid_envelope")
    expect(attempt(world, {**good, "format": "v2"}, raw), "unsupported_envelope_version")
    two = envelope(
        record,
        [claim(record, "a", NODE_A, "passed", [last]), claim(record, "b", NODE_B, "failed", [1])],
        artifact=ARTIFACT,
    )
    expect(attempt(world, two, raw, limits=small(max_claims=1)), "too_many_claims")
    expect(
        attempt(world, good, raw, store=b'{"format": "atlas-feedback-events.v1"}'), "invalid_store"
    )
    expect(attempt(world, good, raw, store=b'{"format": "v2"}'), "unsupported_store_version")
    # the lease
    unindexed = tmp_path / "unindexed"
    unindexed.mkdir()
    expect(attempt(world, good, raw, side=Side(unindexed, tmp_path / "g")), "index_unavailable")
    with monkeypatch.context() as patch:
        spy(patch, _lease_schema_epoch=8)
        expect(attempt(world, good, raw), "index_upgrade_required")
    with monkeypatch.context() as patch:
        spy(patch, db=NoRow())
        expect(attempt(world, good, raw), "source_snapshot_unavailable")
    # the record and the claims
    forged = json.loads(raw)
    forged["data"]["bundles"][0]["records"]["entity"]["name"] = "x"
    tampered = json.dumps(forged).encode()
    expect(attempt(world, good, tampered), "unverifiable_outcome", "record_invalid")
    expect(
        attempt(world, note_only, tampered, artifact=None), "unlinked_annotation", "record_invalid"
    )
    other_id = {**good, "retrieval_record_id": "1" * 64}
    expect(attempt(world, other_id, raw), "unverifiable_outcome", "record_id_mismatch")
    expect(
        attempt(world, {**note_only, "retrieval_record_id": "1" * 64}, raw, artifact=None),
        "unlinked_annotation",
        "record_id_mismatch",
    )
    other_snapshot = {**good, "snapshot_id": "2" * 64}
    expect(attempt(world, other_snapshot, raw), "unverifiable_outcome", "snapshot_mismatch")
    expect(
        attempt(world, {**note_only, "generation_id": "2" * 64}, raw, artifact=None),
        "unlinked_annotation",
        "snapshot_mismatch",
    )
    expect(attempt(world, good, raw, artifact=None), "unverifiable_outcome", "missing_provenance")
    bad_profile = json.loads(json.dumps(good))
    bad_profile["artifact"]["profile"] = "x"
    expect(attempt(world, bad_profile, raw), "unverifiable_outcome", "unsupported_profile")
    sized = json.loads(json.dumps(good))
    sized["artifact"]["bytes"] += 1
    expect(attempt(world, sized, raw), "unverifiable_outcome", "artifact_size_mismatch")
    expect(
        attempt(world, good, raw, artifact=ARTIFACT[:40] + b"X" + ARTIFACT[41:]),
        "unverifiable_outcome",
        "artifact_digest_mismatch",
    )
    wrong_run = json.loads(json.dumps(good))
    wrong_run["run"]["run_id"] = "0" * 64
    expect(attempt(world, wrong_run, raw), "unverifiable_outcome", "run_identity_mismatch")
    doctype = fixture("junit-doctype.xml")
    invalid = envelope(record, good["claims"], artifact=doctype)
    expect(
        attempt(world, invalid, raw, artifact=doctype), "unverifiable_outcome", "artifact_invalid"
    )
    for outer, mutate_in, detail in (
        ("claims", {"results": ["f" * 64]}, "result_not_in_record"),
        ("claims", {"source_files": {"app/payments.py": "a" * 64}}, "source_fingerprint_mismatch"),
    ):
        broken = json.loads(json.dumps(good))
        broken[outer][0].update(mutate_in)
        expect(attempt(world, broken, raw), "unverifiable_outcome", detail)
    for mutate_in, detail in (
        ({"results": ["f" * 64]}, "result_not_in_record"),
        ({"source_files": {"app/payments.py": "a" * 64}}, "source_fingerprint_mismatch"),
    ):
        broken = json.loads(json.dumps(note_only))
        broken["annotations"][0].update(mutate_in)
        expect(attempt(world, broken, raw, artifact=None), "unlinked_annotation", detail)
    expect(
        attempt(world, one(NODE_ABSENT, "passed"), raw),
        "unverifiable_outcome",
        "test_not_in_artifact",
    )
    expect(
        attempt(world, one(NODE_C, "passed"), raw), "unverifiable_outcome", "outcome_not_objective"
    )
    expect(
        attempt(world, one(NODE_B, "passed"), raw),
        "unverifiable_outcome",
        "claim_disagrees_with_artifact",
    )
    # a bundle with no available source, and a source that is not indexed: the checks themselves
    parsed = fo.load_record(raw)
    bare = fo.Record(
        parsed.record_id,
        parsed.snapshot_id,
        parsed.generation_id,
        parsed.ranker,
        (fo.Bundle(ids[0], 0, "definition", ()),),
    )
    item = {"results": [ids[0]], "source_files": {}}
    for kind in ("unverifiable_outcome", "unlinked_annotation"):
        with pytest.raises(fo.FeedbackRejected) as caught:
            fo._check_item(kind, item, bare, {})
        expect((caught.value.reason, caught.value.detail), kind, "result_source_unavailable")
        shown = fo.Record(
            parsed.record_id,
            parsed.snapshot_id,
            parsed.generation_id,
            parsed.ranker,
            (fo.Bundle(ids[0], 0, "definition", (("app/gone.py", HEX),)),),
        )
        with pytest.raises(fo.FeedbackRejected) as caught:
            fo._check_item(kind, item, shown, {})
        expect((caught.value.reason, caught.value.detail), kind, "source_not_indexed")
    # a conflicting duplicate, too many events, an invalid and an oversized output
    previous = import_ok(world, "before", good, raw, ARTIFACT).read_bytes()
    wider = envelope(record, [claim(record, "a", NODE_A, "passed", [last, 1])], artifact=ARTIFACT)
    expect(attempt(world, wider, raw, store=previous), "conflicting_duplicate")
    expect(attempt(world, two, raw, limits=small(max_events=1)), "too_many_events")
    expect(attempt(world, good, raw, limits=small(max_output_bytes=1)), "output_too_large")
    with monkeypatch.context() as patch:
        patch.setattr(fo, "assemble_store", lambda events, annotations: {"format": fo.STORE_FORMAT})
        expect(attempt(world, good, raw), "invalid_output")
    # the whole closed vocabulary, nothing more
    assert {r for r, _ in reached} == set(fo.IMPORT_REJECTIONS)
    assert {d for r, d in reached if r == "unverifiable_outcome"} == set(fo.UNVERIFIABLE_DETAILS)
    assert {d for r, d in reached if r == "unlinked_annotation"} == set(fo.ANNOTATION_DETAILS)


def test_unexpected_errors_never_echo_their_text(world, r1, monkeypatch):
    raw, record = r1
    document = envelope(record, [claim(record, "a", NODE_A, "passed", [5])], artifact=ARTIFACT)
    monkeypatch.setattr(
        fo, "import_feedback", lambda *args: (_ for _ in ()).throw(TypeError("/secret/path"))
    )
    result, out = run_import(world, "before", document, raw, ARTIFACT)
    assert_rejected(result, "Feedback import failed: TypeError", out)
    assert b"secret" not in result.stderr.encode()


# ---- limits, outputs and inputs -----------------------------------------------------------------

IMPORT_LIMITS = [
    "max_envelope_bytes",
    "max_record_bytes",
    "max_artifact_bytes",
    "max_store_bytes",
    "max_claims",
    "max_testcases",
    "max_events",
    "max_output_bytes",
]
RANK_LIMITS = ["max_request_bytes", "max_store_bytes", "max_events", "max_output_bytes"]


@pytest.mark.parametrize("name", list(fo.LIMITS))
def test_limits_reject_a_bool_zero_and_a_value_above_the_maximum(name):
    default, low, high = fo.LIMITS[name]
    assert fo.normalize_limits({name: default}) == fo.normalize_limits({name: None})
    for bad in (True, False, 0, low - 1, high + 1, 1.5, "7"):
        with pytest.raises(fo.FeedbackUsageError) as caught:
            fo.normalize_limits({name: bad})
        assert caught.value.what == name
    assert getattr(fo.normalize_limits({name: high}), name) == high
    assert getattr(fo.normalize_limits({name: low}), name) == low


def test_limits_are_usage_errors_on_the_command_line(world, r1):
    raw, record = r1
    document = envelope(record, [claim(record, "a", NODE_A, "passed", [5])], artifact=ARTIFACT)
    for name in IMPORT_LIMITS:
        high = fo.LIMITS[name][2]
        for bad in (0, high + 1):
            result, out = run_import(
                world, "before", document, raw, ARTIFACT, extra=("--" + name.replace("_", "-"), bad)
            )
            assert_usage(result, "Invalid feedback option: " + name, out)
    for name in RANK_LIMITS:
        high = fo.LIMITS[name][2]
        for bad in (0, high + 1):
            result = run_rank(
                world, "before", request_arguments(), extra=("--" + name.replace("_", "-"), bad)
            )
            assert_usage(result, "Invalid feedback option: " + name)


def test_the_output_may_not_be_inside_the_repository_or_a_graph_root(world, r1, tmp_path):
    raw, record = r1
    document = envelope(record, [claim(record, "a", NODE_A, "passed", [5])], artifact=ARTIFACT)
    n = serial()
    paths = (
        world.input(f"e{n}.json", dumps(document)),
        world.input(f"r{n}.json", raw),
        world.input(f"a{n}.xml", ARTIFACT),
    )
    link = tmp_path / "link"
    link.symlink_to(world.root("before"))
    store = import_ok(world, "before", document, raw, ARTIFACT)
    kept = store.read_bytes()
    text = "--output must be outside the repository and graph root."
    for output in (
        world.repo / "out.json",
        world.repo / ".atlas" / "out.json",
        world.root("before") / "out.json",
        world.root("before") / "deeper" / "out.json",
        link / "out.json",
        world.repo / "tests" / "out.json",
    ):
        result = cli_run(*import_args(world, "before", *paths[:2], output, artifact=paths[2]))
        assert_usage(result, text, output)
    for output in (
        store,
        paths[0],
        paths[1],
        paths[2],
    ):
        result = cli_run(
            *import_args(world, "before", *paths[:2], output, artifact=paths[2], store=store)
        )
        assert_usage(result, "--output must differ from every input.")
    assert store.read_bytes() == kept
    assert not (world.repo / ".atlas").exists() or (world.repo / ".atlas").is_dir()


def test_a_write_failure_is_reported_after_a_clean_import(world, r1, tmp_path):
    raw, record = r1
    document = envelope(record, [claim(record, "a", NODE_A, "passed", [5])], artifact=ARTIFACT)
    n = serial()
    paths = (
        world.input(f"e{n}.json", dumps(document)),
        world.input(f"r{n}.json", raw),
        world.input(f"a{n}.xml", ARTIFACT),
    )
    out = tmp_path / "no-such-directory" / "store.json"
    result = cli_run(*import_args(world, "before", paths[0], paths[1], out, artifact=paths[2]))
    assert_rejected(result, "Feedback output cannot be written.", out)
    assert not out.parent.exists()


def test_unreadable_inputs_are_named_by_role(world, r1, tmp_path):
    raw, record = r1
    document = envelope(record, [claim(record, "a", NODE_A, "passed", [5])], artifact=ARTIFACT)
    good = (
        world.input("ok-e.json", dumps(document)),
        world.input("ok-r.json", raw),
        world.input("ok-a.xml", ARTIFACT),
    )
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    directory = tmp_path / "dir"
    directory.mkdir()
    missing = tmp_path / "missing"
    for role, bad in (("envelope", fifo), ("retrieval-record", directory), ("artifact", missing)):
        out = tmp_path / f"o-{role}.json"
        env, rec, art = good
        args = import_args(
            world,
            "before",
            bad if role == "envelope" else env,
            bad if role == "retrieval-record" else rec,
            out,
            artifact=bad if role == "artifact" else art,
        )
        result = cli_run(*args)
        assert_rejected(result, "Feedback input cannot be read: " + role, out)
    out = tmp_path / "o-store.json"
    result = cli_run(
        *import_args(world, "before", good[0], good[1], out, artifact=good[2], store=directory)
    )
    assert_rejected(result, "Feedback input cannot be read: store", out)
    for bad in (fifo, directory, missing):
        result = cli_run(*rank_args(world, "before", bad))
        assert result.exit_code == 1 and result.stdout_bytes == b""
        assert result.stderr == "Error: Feedback input cannot be read: request\n"
    result = cli_run(*rank_args(world, "before", good[0], store=missing, overlay=True))
    assert result.stderr == "Error: Feedback input cannot be read: store\n"


def test_an_unindexed_repository_is_a_rejection_and_a_bad_graph_root_is_usage(world, r1, tmp_path):
    raw, record = r1
    document = envelope(record, [claim(record, "a", NODE_A, "passed", [5])], artifact=ARTIFACT)
    empty = tmp_path / "empty"
    empty.mkdir()
    n = serial()
    paths = (
        world.input(f"e{n}.json", dumps(document)),
        world.input(f"r{n}.json", raw),
        world.input(f"a{n}.xml", ARTIFACT),
    )
    out = tmp_path / "o.json"
    args = [
        "feedback-import",
        "--repo",
        str(empty),
        "--graph-root",
        str(tmp_path / "nowhere"),
        "--envelope",
        str(paths[0]),
        "--retrieval-record",
        str(paths[1]),
        "--artifact",
        str(paths[2]),
        "--output",
        str(out),
    ]
    assert_rejected(cli_run(*args), REJECT + "index_unavailable", out)
    file_root = tmp_path / "file"
    file_root.write_text("x")
    args[args.index("--graph-root") + 1] = str(file_root / "sub")
    assert_usage(cli_run(*args), "The repository cannot be opened as a graph namespace.", out)
    request = world.input(f"q{n}.json", dumps(request_arguments()))
    result = cli_run(
        "feedback-rank",
        "--repo",
        str(empty),
        "--graph-root",
        str(tmp_path / "nowhere"),
        "--request",
        str(request),
    )
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Feedback ranking rejected: index_unavailable\n"


# ---- the store ----------------------------------------------------------------------------------


def hand_built_event(record, nodeid, outcome, picks, artifact_sha="9" * 64):
    """A store event assembled here from the D6 text alone: no artifact ever existed."""
    data = record["data"]
    ids = bundle_ids(record)
    results = []
    for pick in sorted(picks, key=lambda p: ids[p]):
        pairs = {
            (s["path"], s["source_sha256"])
            for s in data["bundles"][pick]["sources"]
            if s.get("state") == "available"
        }
        results.append(
            {
                "bundle_id": ids[pick],
                "position": pick,
                "sources": [{"path": p, "source_sha256": h} for p, h in sorted(pairs)],
            }
        )
    snapshot = (data["snapshot_id"], data["generation_id"])
    declared = {"name": "forger", "version": "1"}
    pair = {"classname": "tests.test_payments", "name": nodeid.split("::")[-1]}
    selector = {"kind": "pytest_nodeid", "value": nodeid}
    fingerprint = sorted({(s["path"], s["source_sha256"]) for r in results for s in r["sources"]})
    body = {
        "type": "feedback_event",
        "status": "verified_artifact",
        "claim": {"selector": selector, "outcome": outcome},
        "verification": {
            "profile": "atlas-junit-xml-profile.v1",
            "attestation": "operator_declared",
            "derived_outcome": outcome,
            "testcase": pair,
        },
        "retrieval": {
            "record_id": data["retrieval_record_id"],
            "ranker": data["ranker"],
            "record_bundle_ids": ids,
        },
        "run": {
            "run_id": run_identity_for(artifact_sha, len_=1, producer=declared, snapshot=snapshot),
            "producer": declared,
        },
        "artifact": {"sha256": artifact_sha, "bytes": 1},
        "snapshot": {"snapshot_id": snapshot[0], "generation_id": snapshot[1]},
        "results": results,
        "source_fingerprint": domain_digest(
            "atlas-feedback-fingerprint.v1", [list(p) for p in fingerprint]
        ),
        "dedup_key": domain_digest(
            "atlas-feedback-dedup.v1",
            {
                "record_id": data["retrieval_record_id"],
                "artifact_sha256": artifact_sha,
                "selector": selector,
            },
        ),
    }
    return {"id": domain_digest("atlas-feedback-event.v1", body), **body}


def run_identity_for(artifact_sha, len_, producer, snapshot):
    return domain_digest(
        "atlas-test-run.v1",
        {
            "artifact": {
                "profile": "atlas-junit-xml-profile.v1",
                "sha256": artifact_sha,
                "bytes": len_,
            },
            "producer": producer,
            "snapshot": {"snapshot_id": snapshot[0], "generation_id": snapshot[1]},
        },
    )


def hand_built_store(events):
    rest = {
        "format": fo.STORE_FORMAT,
        "producer": {"name": "atlas-aci feedback-import", "version": "atlas-feedback-import.v1"},
        "counts": {"events": len(events), "annotations": 0},
        "events": sorted(events, key=lambda e: e["id"]),
        "annotations": [],
        "limitations": list(fo.LIMITATIONS),
    }
    return {**rest, "id": domain_digest(fo.STORE_FORMAT, rest)}


def test_a_hand_built_self_consistent_store_is_accepted_and_is_never_relabelled(world, r1):
    """Limitation: the store is self-consistent, not authenticated (no secret signs it)."""
    raw, record = r1
    forged = hand_built_event(record, NODE_A, "passed", [len(bundle_ids(record)) - 1])
    store = hand_built_store([forged])
    fo.validate_store(store)
    assert "store_is_self_consistent_not_authenticated" in store["limitations"]
    path = world.input("hand-built.json", json.dumps(store).encode())
    # The ranking trusts nothing more than self-consistency: this is the documented limit.
    result = run_rank(world, "before", request_arguments(), store=path, overlay=True)
    assert result.exit_code == 0, result.stderr
    document = json.loads(result.stdout_bytes)
    assert [a["event_id"] for a in document["events"]["active"]] == [forged["id"]]
    assert all(a["attestation"] == "operator_declared" for a in document["events"]["active"])
    # Importing over it keeps its event exactly as it is.
    honest = envelope(record, [claim(record, "b", NODE_B, "failed", [1])], artifact=ARTIFACT)
    merged = import_ok(world, "before", honest, raw, ARTIFACT, store=path)
    events = {e["id"]: e for e in store_of(merged)["events"]}
    assert events[forged["id"]] == forged and len(events) == 2


def test_validate_store_checks_every_part(world, r1):
    _, record = r1
    forged = hand_built_event(record, NODE_A, "passed", [len(bundle_ids(record)) - 1])
    good = hand_built_store([forged])
    fo.validate_store(good)

    def broken(edit, seal=False):
        store = json.loads(json.dumps(good))
        edit(store)
        if seal:
            store["events"][0]["id"] = domain_digest(
                "atlas-feedback-event.v1",
                {k: v for k, v in store["events"][0].items() if k != "id"},
            )
            rest = {k: v for k, v in store.items() if k != "id"}
            store["id"] = domain_digest(fo.STORE_FORMAT, rest)
        with pytest.raises(ValueError):
            fo.validate_store(store)

    event = lambda s: s["events"][0]  # noqa: E731
    broken(lambda s: s.update(extra=1))
    broken(lambda s: s.pop("limitations"))
    broken(lambda s: s.update(format="v2"))
    broken(lambda s: s.update(id="0" * 64))
    broken(lambda s: s["producer"].update(version="x"))
    broken(lambda s: s["counts"].update(events=2))
    broken(lambda s: s["limitations"].pop())
    broken(lambda s: event(s).update(type="other"), seal=True)
    broken(lambda s: event(s).update(status="unverified_annotation"), seal=True)
    broken(lambda s: event(s)["claim"].update(outcome="success"), seal=True)
    broken(lambda s: event(s)["claim"].update(outcome="failed"), seal=True)
    broken(lambda s: event(s)["verification"].update(attestation="verified"), seal=True)
    broken(lambda s: event(s)["verification"]["testcase"].update(name="other"), seal=True)
    broken(lambda s: event(s)["run"].update(run_id="1" * 64), seal=True)
    broken(lambda s: event(s)["artifact"].update(bytes=2), seal=True)
    broken(lambda s: event(s)["results"][0].update(position=0), seal=True)
    broken(lambda s: event(s)["results"][0].update(bundle_id="3" * 64), seal=True)
    broken(lambda s: event(s)["results"][0]["sources"][0].update(path="../x"), seal=True)
    broken(lambda s: event(s).update(source_fingerprint="2" * 64), seal=True)
    broken(lambda s: event(s).update(dedup_key="2" * 64), seal=True)
    broken(
        lambda s: event(s)["retrieval"]["record_bundle_ids"].append(
            event(s)["retrieval"]["record_bundle_ids"][0]
        ),
        seal=True,
    )
    broken(lambda s: s["events"].append(s["events"][0]) or s["counts"].update(events=2))
    two = hand_built_store([forged, hand_built_event(record, NODE_B, "failed", [1])])
    two["events"].reverse()
    with pytest.raises(ValueError):
        fo.validate_store(two)
    for junk in (None, [], "x", 1):
        with pytest.raises(ValueError):
            fo.validate_store(junk)


def test_the_summary_is_validated(world, r1):
    raw, record = r1
    document = envelope(record, [claim(record, "a", NODE_A, "passed", [5])], artifact=ARTIFACT)
    _, summary = fo.import_feedback(
        dumps(document), raw, ARTIFACT, None, world.side("before"), LIMITS
    )
    fo.validate_summary(summary)
    for edit in (
        lambda s: s.update(extra=1),
        lambda s: s["counts"].update(new_events=5),
        lambda s: s["counts"].update(events=-1),
        lambda s: s.update(store_id="x"),
        lambda s: s.update(command="other"),
    ):
        copy = json.loads(json.dumps(summary))
        edit(copy)
        with pytest.raises(ValueError):
            fo.validate_summary(copy)
