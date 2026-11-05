"""ACI-S38 semantics: the negotiation model, normaliser rules, hosts and the SDK-list fallback."""

import ast
import json
import sys
from pathlib import Path

import pytest
import test_s38_support as sup

from atlas_aci.config import Config
from atlas_aci.protocol import results
from atlas_aci.protocol import revisions as rev
from atlas_aci.server import build_tool_manifest

SERVER_PY = Path(__file__).resolve().parents[2] / "src" / "atlas_aci" / "server.py"
IDS = list(rev.REVISION_IDS)


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    return sup.get_lab(tmp_path_factory.mktemp("s38"))


# ------------------------------------------------------------------- the D2 table
@pytest.mark.parametrize("revision", IDS)
def test_listed_revisions_are_supported_and_verified(revision):
    row = {r.id: r for r in rev.REVISIONS}[revision]
    n = rev.negotiate(revision)
    assert (n.outcome, n.reason, n.wire) == ("supported", "listed_revision", "initialize_result")
    assert n.offered == n.shaping_revision == n.requested == revision and n.verified is True
    assert (n.family, n.structured_results) == (row.family, row.structured_results)
    assert n.revision() == rev.shaping_revision(revision) == rev.negotiated_version(revision)


def test_sdk_only_revision_is_shaped_conservatively():
    n = rev.negotiate("2026-06-30", sdk_supported=[*IDS, "2026-06-30"])
    assert (n.outcome, n.reason) == ("supported", "sdk_only_revision")
    assert n.offered == "2026-06-30" and n.shaping_revision == rev.CONSERVATIVE
    assert (n.verified, n.family, n.structured_results) == (False, "legacy", False)


def test_dropped_revision_and_unknown_strings_are_counter_offers():
    dropped = rev.negotiate("2025-03-26", sdk_supported=["2024-11-05", "2025-06-18", "2025-11-25"])
    assert (dropped.outcome, dropped.reason) == ("counter_offer", "unknown_revision")
    assert dropped.offered == "2025-11-25" and dropped.verified is True
    for text in ("2099-01-01", "", "2024-10-07", "draft", "2025-11-25 "):
        n = rev.negotiate(text)
        assert (n.outcome, n.reason, n.offered) == (
            "counter_offer",
            "unknown_revision",
            "2025-11-25",
        )
        assert (n.shaping_revision, n.verified, n.family) == ("2025-11-25", True, "modern")
        assert n.structured_results is True and n.requested_type == "string"


def test_integer_and_other_non_strings_never_raise():
    for value in (20241105, 0, -1, True, 1.5):
        n = rev.negotiate(value)  # type: ignore[arg-type]
        assert (n.outcome, n.reason, n.offered) == (
            "counter_offer",
            "integer_revision",
            "2025-11-25",
        )
        assert n.shaping_revision == "2025-11-25"
    assert rev.negotiate(20241105).requested_type == "integer"
    assert rev.negotiate(True).requested_type == "other"  # type: ignore[arg-type]


def test_unlisted_sdk_latest_is_not_verified():
    n = rev.negotiate("2099-01-01", sdk_supported=[*IDS, "2026-06-30"], sdk_latest="2026-06-30")
    assert (n.outcome, n.offered, n.shaping_revision) == (
        "counter_offer",
        "2026-06-30",
        "2024-11-05",
    )
    assert (n.verified, n.structured_results) == (False, False)


@pytest.mark.parametrize(
    ("params", "reason", "requested_type"),
    [
        (
            {"capabilities": {}, "clientInfo": {"name": "x", "version": "1"}},
            "missing_revision",
            "missing",
        ),
        (None, "invalid_initialize_params", "missing"),
        ([], "invalid_initialize_params", "missing"),
        (
            {
                "protocolVersion": None,
                "capabilities": {},
                "clientInfo": {"name": "x", "version": "1"},
            },
            "invalid_revision_type",
            "other",
        ),
        (
            {
                "protocolVersion": ["2025-11-25"],
                "capabilities": {},
                "clientInfo": {"name": "x", "version": "1"},
            },
            "invalid_revision_type",
            "other",
        ),
        (
            {"protocolVersion": "2025-11-25", "clientInfo": {"name": "x", "version": "1"}},
            "invalid_initialize_params",
            "string",
        ),
        (
            {"protocolVersion": "2025-11-25", "capabilities": {}},
            "invalid_initialize_params",
            "string",
        ),
    ],
)
def test_malformed_initialize_is_classified(params, reason, requested_type):
    n = rev.classify_initialize(params)
    assert (n.outcome, n.reason, n.wire) == ("malformed", reason, "invalid_params_error")
    assert n.requested_type == requested_type
    assert (n.offered, n.shaping_revision, n.verified, n.structured_results) == (
        None,
        None,
        False,
        False,
    )
    with pytest.raises(ValueError):
        n.revision()


def test_well_formed_initialize_params_follow_negotiate():
    base = {"capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}
    assert (
        rev.classify_initialize({**base, "protocolVersion": "2024-11-05"}).reason
        == "listed_revision"
    )
    assert (
        rev.classify_initialize({**base, "protocolVersion": 20241105}).reason == "integer_revision"
    )
    assert rev.classify_initialize({**base, "protocolVersion": True}).reason == "integer_revision"


def test_closed_vocabularies_and_record_keys():
    assert rev.OUTCOMES == ("supported", "counter_offer", "malformed")
    assert rev.WIRE == ("initialize_result", "invalid_params_error")
    assert rev.REQUESTED_TYPES == ("string", "integer", "missing", "other")
    assert rev.FAMILIES == ("legacy", "modern") and len(rev.REASONS) == 7
    assert rev.STATUSES == ("retained_legacy", "verified_modern", "selected")
    assert rev.REVISION_IDS == ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
    assert (rev.LEGACY, rev.MODERN, rev.SELECTED) == (
        ("2024-11-05", "2025-03-26"),
        ("2025-06-18", "2025-11-25"),
        "2025-11-25",
    )
    base = {"capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}
    seen_reasons = set()
    samples = [
        rev.negotiate("2025-11-25"),
        rev.negotiate("2026-06-30", sdk_supported=[*IDS, "2026-06-30"]),
        rev.negotiate("2099-01-01"),
        rev.negotiate(1),
        rev.classify_initialize({}),
        rev.classify_initialize({**base, "protocolVersion": None}),
        rev.classify_initialize({"protocolVersion": "x"}),
    ]
    for n in samples:
        record = n.record()
        assert tuple(sorted(record)) == tuple(sorted(rev.RECORD_KEYS))
        assert n.outcome in rev.OUTCOMES and n.reason in rev.REASONS and n.wire in rev.WIRE
        assert n.requested_type in rev.REQUESTED_TYPES
        seen_reasons.add(n.reason)
    assert seen_reasons == set(rev.REASONS)
    assert rev.RECORD_FORMAT == "atlas-protocol-negotiation.v1"


def test_record_bounds_client_input():
    huge = "9" * (1024 * 1024)
    n = rev.negotiate(huge)
    record = n.record()
    assert record["requested"] == "9" * 64 and record["requested_len"] == 1024 * 1024
    assert record["requested_truncated"] is True
    assert len(n.record_bytes()) < 1024
    newline = rev.negotiate("2025-11-25\nforged: line\r\n")
    assert b"\n" not in newline.record_bytes() and b"\r" not in newline.record_bytes()
    assert json.loads(newline.record_bytes())["requested"] == "2025-11-25\nforged: line\r\n"
    exact = rev.negotiate("1" * 64).record()
    assert exact["requested_truncated"] is False and exact["requested_len"] == 64
    assert rev.negotiate("1" * 65).record()["requested_truncated"] is True
    assert (
        rev.negotiate(7).record()["requested"] == 7
        and rev.negotiate(7).record()["requested_len"] is None
    )


# ------------------------------------------------------------------- behaviour equivalence
def _old_shaping(requested, supported=("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")):
    """A literal copy of the pre-S38 rule (session_version plus results.negotiated_version)."""
    latest = "2025-11-25"
    return (
        (requested if requested in supported else latest) if isinstance(requested, str) else latest
    )


@pytest.mark.parametrize(
    "requested", [*IDS, "2099-01-01", "", "2024-10-07", 0, 20241105, -5, "2025-11-25\n"]
)
def test_old_rule_equivalence(requested, tmp_path):
    new = rev.negotiate(requested).revision()
    assert new == _old_shaping(requested)
    assert (new in results.STRUCTURED_VERSIONS) == (
        _old_shaping(requested) in {"2025-06-18", "2025-11-25"}
    )
    config = Config(repo=tmp_path, memex_root=tmp_path / "m")
    assert build_tool_manifest(config, new) == build_tool_manifest(config, _old_shaping(requested))


def test_reexports_and_s11_values():
    assert results.STRUCTURED_VERSIONS is rev.STRUCTURED_VERSIONS
    assert results.negotiated_version is rev.negotiated_version
    assert frozenset({"2025-06-18", "2025-11-25"}) == results.STRUCTURED_VERSIONS
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert results.negotiated_version("2099-01-01") == "2025-11-25"
    assert results.negotiated_version("2024-11-05") == "2024-11-05"
    assert results.negotiated_version("") == "2025-11-25"
    assert rev.VERIFIED_SDK == ("mcp", "1.30.0") and rev.CONSERVATIVE == "2024-11-05"


def test_session_version_has_exactly_two_call_sites():
    tree = ast.parse(SERVER_PY.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "session_version"
    ]
    assert len(calls) == 2
    per_function = {
        fn.name: sum(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "session_version"
            for call in ast.walk(fn)
        )
        for fn in ast.walk(tree)
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name in {"_list_tools", "_call_tool"}
    }
    assert per_function == {"_list_tools": 1, "_call_tool": 1}


# ------------------------------------------------------------------- end-to-end fallback
def test_unlisted_sdk_revision_is_shaped_as_the_conservative_revision(lab):
    """A child extends the SDK list (dependency data, not Atlas code) and negotiates 2026-06-30."""
    paths = lab.api()
    memex = lab.fresh(paths).memex
    client = sup.RawLineClient(
        [sys.executable, "-c", sup.SERVER_APPENDED, str(paths.repo), str(memex)],
        env=sup.child_env(0),
    )
    try:
        init = json.loads(client.request("initialize", sup.initialize_params("2026-06-30")))
        client.notify("notifications/initialized")
        tools = json.loads(client.request("tools/list"))["result"]["tools"]
        raws = [
            client.request(
                "tools/call",
                {"name": "graph_query", "arguments": {"schema_version": sup.GRAPH, "op": "status"}},
            ),
            client.request(
                "tools/call",
                {"name": "graph_query", "arguments": {"query": "definitions_of:target"}},
            ),
        ]
    finally:
        close = client.close()
    assert init["result"]["protocolVersion"] == "2026-06-30"  # the SDK echoes what it supports
    assert len(tools) == 7 and not any("outputSchema" in t for t in tools)
    for raw in raws:
        result = json.loads(raw)["result"]
        assert result["isError"] is False and "structuredContent" not in result
        assert json.loads(result["content"][0]["text"])
    pure = rev.negotiate("2026-06-30", sdk_supported=[*IDS, "2026-06-30"]).record()
    assert (pure["verified"], pure["reason"], pure["shaping_revision"]) == (
        False,
        "sdk_only_revision",
        "2024-11-05",
    )
    assert pure["structured_results"] is False and pure["family"] == "legacy"
    assert close == {"exit_code": 0, "killed": False}
    assert "Traceback" not in client.stderr_text()
    assert "protocol_negotiated" not in client.stderr_text()


def test_no_negotiation_record_and_no_traceback_on_any_session(lab):
    """The server emits no negotiation record: diagnostics are the pure model plus the wire."""
    for case in ("legacy.2024-11-05", "modern.2025-11-25", "unsupported.2099-01-01"):
        run, _ = lab.lifecycle(case)
        assert "protocol_negotiated" not in run.stderr and "Traceback" not in run.stderr
        assert "server_starting" in run.stderr  # stderr was captured, not vacuously empty
        assert run.close == {"exit_code": 0, "killed": False}


# ------------------------------------------------------------------- unknown method, batch
def test_unknown_method_and_batch_after_initialize(lab):
    client = sup.RawLineClient(lab.fresh().serve_argv(), env=sup.child_env(0))
    try:
        client.request("initialize", sup.initialize_params("2025-03-26"))
        client.notify("notifications/initialized")
        unknown = json.loads(client.request("foo/bar", {"a": 1}))
        assert unknown["error"] == {
            "code": -32602,
            "message": "Invalid request parameters",
            "data": "",
        }
        client.send_raw(b'[{"jsonrpc":"2.0","id":77,"method":"ping"}]\n')
        after = json.loads(client.request("ping"))  # the ping after the batch is answered
        assert after["result"] == {}
        assert not [f for f in client.frames if json.loads(f).get("id") == 77]  # no batch response
        responses = [
            f for f in client.frames if "result" in json.loads(f) or "error" in json.loads(f)
        ]
        assert (
            len(responses) == 3
        )  # initialize, foo/bar and the ping; the log notification is no response
    finally:
        client.close()


# ------------------------------------------------------------------- hosts
def _script(body):
    return [sys.executable, "-c", body]


def test_raw_client_timeout_kill_and_notification_separation():
    answering = (
        "import sys,json\n"
        "print(json.dumps({'jsonrpc':'2.0','method':'notifications/x','params':{}}),flush=True)\n"
        "line=sys.stdin.readline()\n"
        "print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'ok':True}}),flush=True)\n"
        "sys.stdin.read()\n"
    )
    client = sup.RawLineClient(_script(answering))
    assert json.loads(client.request("ping"))["result"] == {"ok": True}
    assert [n["method"] for n in client.notifications] == ["notifications/x"]
    assert client.close() == {"exit_code": 0, "killed": False}
    silent = sup.RawLineClient(_script("import time\ntime.sleep(600)\n"))
    silent.close_timeout = 1
    with pytest.raises(sup.ProtocolError, match="timed out"):
        silent.request("ping", timeout=1)
    record = silent.close()
    assert record["killed"] is True and record["exit_code"] != 0
    assert silent.proc.stdout.closed and silent.proc.stderr.closed
    assert not any(t.is_alive() for t in silent._threads)


def test_raw_client_rejects_a_non_json_stdout_line():
    client = sup.RawLineClient(_script("import sys\nprint('hello',flush=True)\nsys.stdin.read()\n"))
    with pytest.raises(sup.ProtocolError, match="non-JSON"):
        client.request("ping", timeout=10)
    client.close()


def test_raw_client_reads_a_huge_frame_whole_and_drains_stderr_past_the_cap():
    body = (
        "import sys,json\n"
        "sys.stdin.readline()\n"
        "sys.stderr.write('e'*(3*1024*1024));sys.stderr.flush()\n"
        "blob='x'*(2*1024*1024)\n"
        "print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'blob':blob}}),flush=True)\n"
        "sys.stdin.read()\n"
    )
    client = sup.RawLineClient(_script(body))
    raw = client.request("ping", timeout=60)
    assert len(raw) > 2 * 1024 * 1024 and len(json.loads(raw)["result"]["blob"]) == 2 * 1024 * 1024
    assert client.close() == {"exit_code": 0, "killed": False}
    assert len(client.stderr) == sup.STDERR_CAP and client.stderr_discarded >= 2 * 1024 * 1024


def test_the_tools_list_frame_is_read_whole(lab):
    run, _ = lab.lifecycle("modern.2025-11-25")
    raw = next(ex.raw for ex in run.exchanges if ex.method == "tools/list")
    assert len(raw) > 300_000 and raw.endswith(b"\n") and raw.count(b"\n") == 1
    digest = sup.tools_digest(json.loads(raw)["result"])["tools_digest"]
    assert digest["bytes"] == 347091


# ------------------------------------------------------------------- normalisation rules
def _run(frames_by_step, case="x"):
    exchanges, frames = [], []
    for step, (method, params, body) in enumerate(frames_by_step, start=1):
        raw = json.dumps(body).encode() + b"\n" if body is not None else None
        exchanges.append(sup.Exchange(step, method, method, "request", params, raw))
        if raw is not None:
            frames.append(raw)
    return sup.CaseRun(
        case, "negotiation.v1", "2025-11-25", exchanges, frames, [], {"exit_code": 0}
    )


PATHS = sup.Paths(Path("/p/repo"), Path("/p/memex"), Path("/p/graphs"))


def _norm(frames_by_step, paths=PATHS):
    return sup.normalise_run(_run(frames_by_step), paths, "1.30.0")


def test_n1_response_ids_become_first_appearance_ordinals():
    t = _norm(
        [
            ("ping", None, {"jsonrpc": "2.0", "id": 41, "result": {}}),
            ("ping", None, {"jsonrpc": "2.0", "id": 7, "result": {}}),
        ]
    )
    assert [e["response"]["id"] for e in t["exchanges"]] == [1, 2]


def test_n2_replaces_resolved_and_unresolved_paths_longest_first(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    paths = sup.Paths(link / "repo", link / "memex", link / "graphs")
    for sub in ("repo", "memex", "graphs"):
        (real / sub).mkdir()
    texts = [
        f"{link}/repo/a.py",
        f"{real}/memex/x",
        json.dumps(f"{link}/graphs")[1:-1] + "/graph.db",
        f"{real}/graphs",
        f"{link}/repo/.atlas",
    ]
    subs = sup.n2_substitutions(paths)
    out = [sup.apply_n2(text, subs) for text in texts]
    assert out == ["<repo>/a.py", "<memex>/x", "<graphs>/graph.db", "<graphs>", "<repo>/.atlas"]
    # JSON-escaped forms (a path holding a quote and a backslash) are replaced in nested text too.
    odd = tmp_path / 'we"ird\\dir'
    odd.mkdir()
    odd_paths = sup.Paths(odd / "repo", odd / "m", odd / "g")
    once = json.dumps(str(odd / "repo"))[1:-1]
    twice = json.dumps(once)[1:-1]
    odd_subs = sup.n2_substitutions(odd_paths)
    assert (
        sup.apply_n2(once + "|" + twice + "|" + str(odd / "repo"), odd_subs)
        == "<repo>|<repo>|<repo>"
    )
    assert sup.apply_n2("/p/repository", sup.n2_substitutions(PATHS)) == "/p/repository"


def test_n3_text_json_is_parsed_and_its_exact_bytes_are_hashed():
    payload = '{"a":1,"b":"x"}'
    swapped = '{"b":"x","a":1}'

    def body(text):
        return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}

    one = _norm([("tools/call", None, body(payload))])["exchanges"][0]["response"]["result"]
    two = _norm([("tools/call", None, body(swapped))])["exchanges"][0]["response"]["result"]
    text_one, text_two = one["content"][0]["text"], two["content"][0]["text"]
    assert text_one["$json"] == text_two["$json"] == {"a": 1, "b": "x"}
    assert text_one["sha256"] == sup.sha256_hex(payload) != text_two["sha256"]
    plain = _norm([("tools/call", None, body("not json"))])["exchanges"][0]["response"]["result"]
    assert plain["content"][0]["text"] == "not json"


def test_n4_hash_numbering_keeps_the_same_or_different_relation():
    a, b = "a" * 64, "b" * 64
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"x": [a, b, a, "c" * 63, "d" * 65], "k": "pre-" + a},
    }
    result = _norm([("m", None, body)])["exchanges"][0]["response"]["result"]
    assert result["x"] == ["<h64:1>", "<h64:2>", "<h64:1>", "c" * 63, "d" * 65]
    assert result["k"] == "pre-<h64:1>"
    diff = {**body, "result": {"x": [a, b, b], "k": "z"}}
    assert _norm([("m", None, diff)])["exchanges"][0]["response"]["result"]["x"] == [
        "<h64:1>",
        "<h64:2>",
        "<h64:2>",
    ]


def test_n5_lease_and_cursor_values_become_tokens():
    inner = json.dumps({"lease_id": "L1", "next_cursor": "C9", "cursor": "C9", "keep": "v"})
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "lease_id": "L2",
            "content": [{"type": "text", "text": inner}],
            "structuredContent": {"next_cursor": "C9", "lease_id": "L1"},
        },
    }
    result = _norm([("tools/call", None, body)])["exchanges"][0]["response"]["result"]
    assert result["lease_id"] == "<lease:1>"
    assert result["structuredContent"] == {"lease_id": "<lease:2>", "next_cursor": "<cursor:1>"}
    parsed = result["content"][0]["text"]["$json"]
    assert parsed == {
        "cursor": "<cursor:1>",
        "keep": "v",
        "lease_id": "<lease:2>",
        "next_cursor": "<cursor:1>",
    }


def test_n6_server_info_version_is_kept_verbatim():
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"serverInfo": {"name": "atlas-aci", "version": "9.9.9"}},
    }
    result = _norm([("initialize", None, body)])["exchanges"][0]["response"]["result"]
    assert result["serverInfo"]["version"] == "9.9.9"


def test_n7_tools_list_becomes_a_digest():
    tools = [
        {"name": "b", "inputSchema": {}, "_meta": {"k": 1}, "annotations": {}},
        {"name": "a", "inputSchema": {}, "outputSchema": {"type": "object"}},
    ]
    body = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
    digest = _norm([("tools/list", None, body)])["exchanges"][0]["response"]["result"][
        "tools_digest"
    ]
    text = sup.canon(tools)
    assert digest == {
        "names": ["a", "b"],
        "sha256": sup.sha256_hex(text),
        "bytes": len(text.encode()),
        "output_schema": ["a"],
        "meta": ["b"],
        "annotations": ["b"],
    }


def test_n8_n9_notifications_are_separate_and_the_bytes_are_canonical():
    run = _run([("ping", None, {"jsonrpc": "2.0", "id": 1, "result": {}})])
    run.notifications = [
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {"p": "/p/repo"}}
    ]
    t = sup.normalise_run(run, PATHS, "1.30.0")
    assert t["notifications"] == [
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {"p": "<repo>"}}
    ]
    data = sup.transcript_bytes(t)
    assert data.endswith(b"}\n") and data.count(b"\n") == 1
    assert (
        data
        == (
            json.dumps(t, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode()
    )


def test_rule_table_is_closed_and_ordered():
    assert [name for name, _ in sup.NORMALISATION_RULES] == [f"N{i}" for i in range(1, 10)]


# ------------------------------------------------------------------- mutation controls
def _mutated(run, step, edit):
    exchanges = [
        sup.Exchange(
            e.step, e.name, e.method, e.kind, e.request, edit(e.raw) if e.step == step else e.raw
        )
        for e in run.exchanges
    ]
    return sup.CaseRun(
        run.case,
        run.conversation,
        run.requested,
        exchanges,
        run.frames,
        run.notifications,
        run.close,
    )


def test_mutations_of_a_recorded_conversation_are_not_hidden_by_the_normaliser(lab):
    run, paths = lab.lifecycle("modern.2025-11-25")
    version = sup.sdk_version()
    golden = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "s38"
        / "transcripts"
        / "modern.2025-11-25.v1.json"
    ).read_bytes()
    assert sup.transcript_bytes(sup.normalise_run(run, paths, version)) == golden

    def regenerate(step, edit):
        mutated = _mutated(run, step, edit)
        assert (
            mutated.exchanges[step - 1].raw != run.exchanges[step - 1].raw
        )  # the edit really landed
        return sup.transcript_bytes(sup.normalise_run(mutated, paths, version))

    # (a) one changed text byte
    assert regenerate(10, lambda raw: raw.replace(b"return 1", b"return 2")) != golden
    # (b) a changed serverInfo.version; derived from the SDK so the edit always lands
    old_version = b'"version":"' + version.encode() + b'"'
    assert (
        regenerate(1, lambda raw: raw.replace(old_version, old_version[:-1] + b".1" + b'"'))
        != golden
    )

    # (c) a changed outputSchema name list: the tools/list digest records which tools carry one
    def drop_output_schema(raw):
        body = json.loads(raw)
        del body["result"]["tools"][
            next(i for i, t in enumerate(body["result"]["tools"]) if t["name"] == "graph_query")
        ]["outputSchema"]
        return json.dumps(body).encode() + b"\n"

    assert regenerate(4, drop_output_schema) != golden
    # (d) a changed hash relation: step 8 reports a different generation than step 7
    first = json.loads(run.exchanges[6].raw)
    generation = json.loads(first["result"]["content"][0]["text"])["generation_id"]
    assert len(generation) == 64
    assert regenerate(8, lambda raw: raw.replace(generation.encode(), b"f" * 64)) != golden
    # An identity edit stays equal, so the controls above are not failing vacuously.
    identical = _mutated(run, 10, lambda raw: raw)
    assert sup.transcript_bytes(sup.normalise_run(identical, paths, version)) == golden
