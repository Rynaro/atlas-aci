"""ACI-S23 metric semantics: S18 parity, line-to-byte conversion, normalisation, prefix rule."""

from __future__ import annotations

import base64
import hashlib
import json
import sys

import pytest
from test_s23_support import ROOT, agg, call_result, fixture, load_script

SPAN = ROOT / "atlas/evals/span_metrics.py"
S18 = ROOT / ".spectra/evidence/aci-s18"


def span_module():
    return load_script("atlas_eval_span_metrics", SPAN)


def test_span_metrics_reproduce_every_committed_s18_row():
    """Day-1 (c): `span_metrics` equals the S18 module on all 48 context + 24 baseline rows."""
    sys.path.insert(0, str(ROOT / "mcp-server/src"))
    s18 = load_script("measure_context_yield", ROOT / "atlas/evals/measure_context_yield.py")
    mine = span_module()
    fx = fixture()
    tasks = {t["id"]: t for t in fx["tasks"]}
    local = json.loads((S18 / "local-yield.json").read_bytes())
    checkpoint = json.loads((S18 / "baseline-checkpoint.json").read_bytes())
    context = baseline = 0
    for row in local["context"]:
        raw = base64.b64decode(row["result_base64"])
        body, sources, count = mine.body_and_sources(raw, False)
        assert (body, sources, count) == s18.body_and_sources(raw, False)
        useful = tasks[row["task"]]["useful_spans"]
        assert mine.metrics(sources, useful, fx) == row["metrics"]
        assert count == row["selected_bundles"]
        context += 1
    lexical = {
        (b["task"], b["max_bytes"], b["protocol_version"]): b
        for b in local["baseline_reference_only"]
    }
    for row in checkpoint["lexical_results"]:
        raw = base64.b64decode(row["result_base64"])
        body, sources, count = mine.body_and_sources(raw, True)
        committed = lexical[(row["task"], row["max_bytes"], row["protocol_version"])]
        assert mine.metrics(sources, tasks[row["task"]]["useful_spans"], fx) == committed["metrics"]
        assert count == committed["selected_candidates"]
        baseline += 1
    assert (context, baseline) == (48, 24)  # non-vacuous
    intervals = [(5, 9), (0, 3), (2, 4)]
    assert mine.union(intervals) == s18.union(intervals) == [[0, 4], [5, 9]]


def context_for(text: str, sha: str):
    fx = {"source_files": {"a.py": text}, "source_hashes": {"a.py": sha}, "tasks": []}
    return fx, agg.SpanContext(fx, "/repo")


def view(path: str, first: int, last: int, lines: list[str]) -> str:
    return call_result({"path": path, "start_line": first, "end_line": last, "lines": lines})


def test_view_file_lines_become_byte_ranges_with_universal_newlines():
    text = "é = 1\nb = 2\nc = 3\n"  # multi-byte first line
    sha = hashlib.sha256(text.encode()).hexdigest()
    fx, ctx = context_for(text, sha)
    spans, bundles = agg.spans_for("view_file", {}, view("a.py", 2, 3, ["b = 2\n", "c = 3\n"]), ctx)
    assert bundles is None and len(spans) == 1
    assert (spans[0]["start_byte"], spans[0]["end_byte"]) == (
        len("é = 1\n".encode()),
        len(text.encode()),
    )
    assert spans[0]["inline"] == {"encoding": "utf8", "data": "b = 2\nc = 3\n"}
    assert spans[0]["source_sha256"] == sha
    # The repo-prefixed (V0 style) and relative paths are the same file.
    again, _ = agg.spans_for("view_file", {}, view("/repo/a.py", 2, 3, ["b = 2\n", "c = 3\n"]), ctx)
    assert again == spans
    # The metrics function accepts exactly these dicts.
    label = [{"path": "a.py", "source_sha256": sha, "start_byte": 7, "end_byte": 13}]
    result = span_module().metrics(spans, label, fx)
    assert result["useful_inline_bytes"] == 6 and result["useful_label_bytes"] == 6


def test_crlf_and_altered_content_fail_closed_as_content_mismatch():
    # A CRLF source cannot equal the universal-newline text view_file returns.
    text = "x = 1\r\ny = 2\r\n"
    sha = hashlib.sha256(text.encode()).hexdigest()
    _, ctx = context_for(text, sha)
    with pytest.raises(agg.EvalInputError) as error:
        agg.spans_for("view_file", {}, view("a.py", 1, 1, ["x = 1\n"]), ctx)
    assert error.value.code == "content_mismatch"
    lf = "x = 1\ny = 2\n"
    _, ctx = context_for(lf, hashlib.sha256(lf.encode()).hexdigest())
    with pytest.raises(agg.EvalInputError) as error:
        agg.spans_for("view_file", {}, view("a.py", 1, 1, ["x = 9\n"]), ctx)
    assert error.value.code == "content_mismatch"
    with pytest.raises(agg.EvalInputError) as error:
        agg.spans_for("view_file", {}, view("missing.py", 1, 1, ["x = 1\n"]), ctx)
    assert error.value.code == "unknown_shape"
    with pytest.raises(agg.EvalInputError) as error:
        agg.spans_for("view_file", {}, view("a.py", 1, 5, ["x = 1\n"]), ctx)
    assert error.value.code == "unknown_shape"
    assert agg.line_table("a\rb\r\nc\n") == [(0, 2), (2, 5), (5, 7)]


def test_search_results_become_whole_line_reference_spans():
    text = "def f():\n    return 1\n"
    sha = hashlib.sha256(text.encode()).hexdigest()
    _, ctx = context_for(text, sha)
    hit = call_result({"matches": [{"path": "a.py", "line": 2, "col": 5, "preview": "x"}]})
    spans, _ = agg.spans_for("search_text", {}, hit, ctx)
    assert [(s["start_byte"], s["end_byte"]) for s in spans] == [(9, 22)]
    assert "inline" not in spans[0]
    symbol = call_result(
        {
            "definitions": [{"path": "a.py", "line_start": 1, "line_end": 2}],
            "references": [{"path": "a.py", "line": 1}],
        }
    )
    spans, _ = agg.spans_for("search_symbol", {}, symbol, ctx)
    assert [(s["start_byte"], s["end_byte"]) for s in spans] == [(0, 22), (0, 9)]
    errors, _ = agg.spans_for("search_text", {}, call_result({"error": "x"}, True), ctx)
    assert errors == []
    with pytest.raises(agg.EvalInputError) as error:
        agg.spans_for("search_text", {}, call_result({"unexpected": 1}), ctx)
    assert error.value.code == "unknown_shape"


def test_repo_root_normalisation_is_not_vacuous_and_leaves_no_host_prefix():
    root = "/private/var/folders/ab/T/atlas-v1/repo"
    alias = "/var/folders/ab/T/atlas-v1/repo"
    text = f'{{"a":"{root}/x.py","b":"{alias}/y.py","c":"/repo/z.py"}}'
    normal, count = agg.normalise(text, root)
    assert count == 2  # recorded, not assumed
    assert normal == '{"a":"/repo/x.py","b":"/repo/y.py","c":"/repo/z.py"}'
    assert "folders" not in normal
    assert agg.normalise(text, alias) == (normal, 2)  # either spelling of the root works
    assert agg.repo_relative(f"{root}/pkg/m.py", root) == "pkg/m.py"
    assert agg.repo_relative("/repo/pkg/m.py", "/tmp/elsewhere") == "pkg/m.py"
    for bad in ('/tmp/a"b', "/tmp/a\\b", "/tmp/a\nb", "relative/path"):
        with pytest.raises(agg.EvalInputError) as error:
            agg.check_repo_root(bad)
        assert error.value.code == "unknown_shape"
    agg.check_repo_root("/private/tmp/ünicode ok")


def test_normalisation_removes_host_paths_from_a_real_recorded_arm():
    """The committed V1 records carry a host repo_root; none survives normalisation."""
    records = [
        r for r in agg.read_raw_dir(ROOT / "docs/evidence/v3/raw") if r["kind"] == "retrieval"
    ]
    assert len(records) == 5
    for record in records:
        agg.check_repo_root(record["repo_root"])
        assert record["observations"]
        for obs in record["observations"]:
            normal, _ = agg.normalise(obs["result_text"], record["repo_root"])
            assert not any(
                alias in normal
                for alias in agg.root_aliases(record["repo_root"])
                if alias != "/repo"
            )


def test_prefix_rule_edges():
    def obs(step, size):
        return {"step": step, "result_text": "x" * size}

    arm = {"kind": "legacy"}
    calls = [obs(0, 10), obs(1, 20), obs(2, 5)]
    assert [o["step"] for o in agg._admit(arm, calls, 35, "/repo")] == [0, 1, 2]  # exact fit
    assert [o["step"] for o in agg._admit(arm, calls, 34, "/repo")] == [0, 1]
    assert [o["step"] for o in agg._admit(arm, calls, 30, "/repo")] == [0, 1]
    assert agg._admit(arm, calls, 9, "/repo") == []  # first call over budget: zero calls
    # A later small call never leapfrogs an over-budget call (longest prefix).
    assert [
        o["step"] for o in agg._admit(arm, [obs(0, 10), obs(1, 99), obs(2, 1)], 50, "/repo")
    ] == [0]
    # Out-of-order records are ordered by step first.
    assert [o["step"] for o in agg._admit(arm, [obs(1, 20), obs(0, 10)], 30, "/repo")] == [0, 1]


def test_ratio_uses_half_even_decimal_strings_and_a_null_for_zero_denominators():
    assert agg.ratio(1, 3) == {"num": 1, "den": 3, "value": "0.333333"}
    assert agg.ratio(2, 3)["value"] == "0.666667"
    assert agg.ratio(1, 2000000)["value"] == "0.000000"  # 0.0000005 -> even
    assert agg.ratio(3, 2000000)["value"] == "0.000002"  # 0.0000015 -> even
    assert agg.ratio(5, 2000000)["value"] == "0.000002"  # 0.0000025 -> even
    assert agg.ratio(1, 1)["value"] == "1.000000"
    assert agg.ratio(0, 5)["value"] == "0.000000"
    assert agg.ratio(4, 0) == {"num": 4, "den": 0, "value": None}
    assert not any(isinstance(v, float) for v in agg.ratio(7, 9).values())


def test_gzip_identity_is_the_decompressed_canonical_bytes():
    record = {"b": [1, 2], "a": "é"}
    one = agg.gzip_canonical(record)
    assert agg.decode_gzip_canonical(one) == record
    assert one == agg.gzip_canonical({"a": "é", "b": [1, 2]})
    import gzip

    other = gzip.compress(agg.canonical(record), compresslevel=1, mtime=12345)
    assert other != one and agg.decode_gzip_canonical(other) == record
    with pytest.raises(agg.EvalInputError) as error:
        agg.decode_gzip_canonical(gzip.compress(b'{"b": 1, "a": 2}\n'))
    assert error.value.code == "hash_mismatch"
    with pytest.raises(ValueError):
        agg.canonical({"x": float("nan")})
