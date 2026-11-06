"""ACI-S40 access paths: the router's closed order, the access-path matrix, the guards, the wiring
tripwires, ContextVar propagation, exhaustion limits, the CLI and the serving subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import pytest
import test_s39_support as s39
from test_s40_support import (
    ALL_TOOLS,
    BASE,
    IMPACT_ARGS,
    PAGE_ARGS,
    RETRIEVE_B,
    SELECTED,
    STATUS_ARGS,
    Observer,
    World,
    _Child,
    child_env,
    error_code,
    get_labs,
    grant,
    page_cursor,
    router_call,
    serve_shared_argv,
    tool_payload,
    write_fake,
    write_private,
)

from atlas_aci.config import Config
from atlas_aci.protocol import http
from atlas_aci.protocol.results import result_bytes
from atlas_aci.query import authz, shared_http
from atlas_aci.query.authz import (
    AUTHZ_CONTEXT,
    AuthzContext,
    Credential,
    PolicyStore,
    Principal,
    ScopedLeaseRegistry,
    SharedEnforcement,
)
from atlas_aci.server import build_server_state, call_tool_result


@pytest.fixture(scope="module")
def labs(tmp_path_factory):
    return get_labs(tmp_path_factory.mktemp("s40labs"))


def body_of(message):
    return {"withhold_body": True, "declared_length": len(json.dumps(message).encode())}


def reason_of(response):
    return response.json()["error"]["data"]["reason"]


# ---------------------------------------------------------------------------- the router, in order
async def test_router_stage_order_table(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    token = world.keys.mint("p1", f"{BASE}/a")
    async with world.serve() as http_:

        async def call(**kwargs):
            return await router_call(world, "a", kwargs.pop("token", None), **kwargs)

        # 1 peer: a non-loopback peer is refused first, even with a valid token and a bad Host.
        status, _h, body, receives = await call(token=token, client=("10.0.0.5", 9), host="evil:1")
        assert status == 403 and receives == 0
        assert json.loads(body)["error"]["data"] == {"reason": "peer_not_loopback"}
        status, _h, body, receives = await call(token=token, client=None)
        assert status == 403 and receives == 0
        # 2 host: a bad Host with no token is a 421, not a 401.
        status, _h, body, receives = await call(host="attacker.example:5555")
        assert status == 421 and receives == 0
        assert json.loads(body)["error"]["data"] == {"reason": "host_not_allowed"}
        # 3 origin
        status, _h, body, receives = await call(extra=[("Origin", "http://evil.example")])
        assert status == 403 and receives == 0
        assert json.loads(body)["error"]["data"] == {"reason": "origin_not_allowed"}
        # 4/5 route and method: a GET with no token is a 405 (the method is not secret).
        status, headers, body, receives = await call(method="GET")
        assert status == 405 and headers["allow"] == "POST" and receives == 0
        assert json.loads(body)["error"]["data"] == {"reason": "method_not_allowed"}
        status, headers, _b, receives = await call(method="DELETE")
        assert status == 405 and receives == 0
        # 6 credentials: only now does the missing token matter.
        status, headers, body, receives = await call()
        assert status == 401 and receives == 0
        assert json.loads(body)["error"]["data"] == {"reason": "credentials_missing"}
        assert headers["www-authenticate"].startswith('Bearer resource_metadata="https://atlas')
        # 8 authorization: a valid token for an unregistered or ungranted repository.
        status, _h, body_unknown, receives = await router_call(
            world, "nope", world.keys.mint("p1", f"{BASE}/nope")
        )
        assert status == 403 and receives == 0
        status, _h, body_denied, receives = await router_call(
            world, "b", world.keys.mint("p1", f"{BASE}/b")
        )
        assert status == 403 and receives == 0 and body_unknown == body_denied
        assert json.loads(body_denied)["error"]["data"] == {"reason": "repository_forbidden"}
        # 9 delegate: the valid token reaches the SDK (the receive count is the positive control).
        status, _h, _b, receives = await call(token=token)
        assert receives >= 1
        assert http_.port > 0


PATHS_404 = [
    "/mcp",
    "/mcp/",
    "//mcp/a",
    "/mcp//a",
    "/mcp/a/",
    "/mcp/a/x",
    "/mcp/A",
    "/mcp/a\n",
    "/mcp/-a",
    "/mcp/" + "a" * 65,
    "/mcp/a b",
    "/x",
    "/",
    "/mcp/a%0a",
    "/mcp/é",
]
PRM = "/.well-known/oauth-protected-resource/mcp/"


async def test_route_matching_is_exact(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    token = world.keys.mint("p1", f"{BASE}/a")
    async with world.serve():
        for path in PATHS_404:
            status, _h, body, receives = await router_call(world, "a", token, path=path)
            assert status == 404 and receives == 0, path
            assert json.loads(body)["error"]["data"] == {"reason": "route_not_found"}, path
        import test_s39_support as support

        async def raw(path, raw_path, **scope_extra):
            world.router.allow_host_name("127.0.0.1")
            headers = [
                ("Host", "127.0.0.1:5555"),
                ("Authorization", f"Bearer {token}"),
                ("Accept", support.ACCEPT),
                ("Content-Type", "application/json"),
            ]
            scope = support.asgi_scope(path=path, headers=headers)
            if raw_path is not None:
                scope["raw_path"] = raw_path
            scope.update(scope_extra)
            return await support.asgi_call(world.router, scope)

        # Percent-encoding: the decoded path matches, the raw path does not.
        status, *_rest, receives = await raw("/mcp/a", b"/mcp/%61")
        assert status == 404 and receives == 0
        status, *_rest, receives = await raw("/mcp/a", None)  # no raw_path at all
        assert status == 404 and receives == 0
        status, *_rest, receives = await raw("/mcp/a", b"/mcp/a", root_path="/prefix")
        assert status == 404 and receives == 0
        status, *_rest, receives = await raw("/mcp/%2e%2e/a", b"/mcp/%2e%2e/a")
        assert status == 404 and receives == 0
        status, *_rest, receives = await raw("/mcp/a/b", b"/mcp/a%2fb")
        assert status == 404 and receives == 0
        # A query string is not part of the path: the route matches (and is then authorized).
        status, *_rest, receives = await raw("/mcp/a", b"/mcp/a", query_string=b"x=1")
        assert receives >= 1 or status in {200, 400, 406, 415}
        # PRM variants.
        for path in (
            PRM,
            PRM + "a/",
            PRM + "A",
            PRM + "a%0a",
            PRM + "-a",
            "/.well-known/oauth-protected-resource",
            PRM + "a/b",
        ):
            status, _h, _b, receives = await router_call(world, "a", None, method="GET", path=path)
            assert status == 404 and receives == 0, path
        status, *_rest = await raw(PRM + "a", b"/.well-known/oauth-protected-resource/mcp/%61")
        assert status == 404


async def test_protected_resource_metadata_is_public_and_registry_free(tmp_path, labs, monkeypatch):
    world = World(tmp_path, labs, grants=[])
    world.build()
    observer = Observer().install(monkeypatch)
    async with world.serve() as http_:
        mark = observer.mark()
        bodies = {}
        for label in ("a", "b", "unregistered", "x-y.z_1"):
            response = await http_.request("GET", PRM + label)
            assert response.status == 200, label
            assert response.header("content-type") == "application/json"
            document = response.json()
            assert document["resource"] == f"{BASE}/{label}"
            assert document["authorization_servers"] == ["https://idp.example.test/"]
            bodies[label] = response.body
        assert len(set(bodies.values())) == 4
        assert observer.since(mark) == 0  # no lease, graph or tool activity at all
        assert world.router.verifier.counts == {}  # unauthenticated: the verifier never ran
        wrong = await http_.request("POST", PRM + "a", body=b"{}")
        assert wrong.status == 405 and wrong.header("allow") == "GET"
        assert wrong.json()["error"]["data"] == {"reason": "metadata_method_not_allowed"}
        # Control: the same server answers an authorized call.
        world.write_policy([grant("p1", "a")])
        assert (await http_.rpc("a", "p1", "tools/list")).status == 200


# ---------------------------------------------------------------------------- the D8 matrix
INIT = {
    "protocolVersion": SELECTED,
    "capabilities": {},
    "clientInfo": {"name": "t", "version": "1"},
}
NOTE = "notification"


def op(**kw):
    return {"schema_version": "atlas.graph.v1", **kw}


ROWS = {
    "initialize": ("initialize", INIT),
    "ping": ("ping", None),
    "notifications/initialized": (NOTE, "notifications/initialized"),
    "notifications/cancelled": (NOTE, "notifications/cancelled"),
    "tools/list": ("tools/list", None),
    "view_file": ("tools/call", ("view_file", {"path": "a.py", "start_line": 1, "end_line": 2})),
    "list_dir": ("tools/call", ("list_dir", {"path": "."})),
    "search_text": ("tools/call", ("search_text", {"pattern": "A", "scope": "."})),
    "search_symbol": ("tools/call", ("search_symbol", {"name": "A"})),
    "graph status": ("tools/call", ("graph_query", STATUS_ARGS)),
    "graph neighborhood": ("tools/call", ("graph_query", PAGE_ARGS["query"])),
    "graph path": (
        "tools/call",
        ("graph_query", op(op="path", seed={"name": "A"}, target={"name": "B"}, deadline_ms=2000)),
    ),
    "graph legacy": ("tools/call", ("graph_query", {"query": "definitions_of:A"})),
    "retrieve": ("tools/call", ("graph_query", RETRIEVE_B)),
    "context": ("tools/call", ("graph_query", op(op="context", text="A"))),
    "page": ("tools/call", ("graph_query", PAGE_ARGS)),
    "impact": ("tools/call", ("graph_query", IMPACT_ARGS)),
    "test_dry_run": ("tools/call", ("test_dry_run", {"path": "a.py"})),
    "memex_read": ("tools/call", ("memex_read", {"ref": "0" * 64})),
    "unknown tool": ("tools/call", ("no_such_tool", {})),
    "resources/list": ("resources/list", None),
    "resources/templates/list": ("resources/templates/list", None),
    "resources/read": (
        "resources/read",
        {"uri": "atlas-source://" + "0" * 64 + "?token=x&offset=0&max_bytes=10"},
    ),
    "unknown method": ("resources/subscribe", {"uri": "atlas-source://x"}),
}


def message_for(row):
    kind, payload = ROWS[row]
    if kind == NOTE:
        return {"jsonrpc": "2.0", "method": payload}
    message = {"jsonrpc": "2.0", "id": 1, "method": kind}
    if kind == "tools/call":
        message["params"] = {"name": payload[0], "arguments": payload[1]}
    elif payload is not None:
        message["params"] = payload
    return message


def check_authorized(row, response):
    kind = ROWS[row][0]
    if kind == NOTE:
        assert response.status == 202, response.status
        return
    assert response.status == 200, (row, response.status, response.text[:200])
    rpc = response.rpc(1)
    if row == "initialize":
        assert rpc["result"]["protocolVersion"] == SELECTED
    elif row == "ping":
        assert rpc["result"] == {}
    elif row == "tools/list":
        assert len(rpc["result"]["tools"]) == 7
    elif row == "resources/list":
        assert rpc["result"]["resources"] == []
    elif row == "resources/templates/list":
        assert len(rpc["result"]["resourceTemplates"]) == 1
    elif row == "resources/read":
        assert rpc["error"]["data"] == {"reason": "INVALID_REFERENCE"}
    elif row == "unknown method":
        assert rpc["error"]["code"] == -32601
    elif row in {"test_dry_run", "memex_read", "unknown tool"}:
        payload = json.loads(rpc["result"]["content"][0]["text"])
        assert rpc["result"]["isError"] is True and error_code(payload) == "FORBIDDEN", row
    elif row == "impact":
        payload = json.loads(rpc["result"]["content"][0]["text"])
        assert error_code(payload) == "IMPACT_UNCONFIGURED"
    else:
        payload = json.loads(rpc["result"]["content"][0]["text"])
        assert rpc["result"]["isError"] is False, (row, payload)


async def test_access_path_matrix(tmp_path, labs, monkeypatch):
    # An authorized search_text spawns ripgrep; a fake `rg` keeps this test independent of it.
    directory = tmp_path / "fakebin"
    directory.mkdir()
    write_fake(directory, "rg", "exit 0\n")
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    observer = Observer().install(monkeypatch)
    async with world.serve() as http_:
        mark = observer.mark()
        for row in ROWS:
            message = message_for(row)
            kwargs = body_of(message)
            none = await http_.post("a", None, message, **kwargs)
            assert none.status == 401, row
            assert reason_of(none) == "credentials_missing", row
            assert "error=" not in none.header("www-authenticate"), row
            bad = await http_.post("a", None, message, token="a.b.c", **kwargs)
            assert bad.status == 401 and reason_of(bad) == "credentials_invalid", row
            assert 'error="invalid_token"' in bad.header("www-authenticate"), row
            ungranted = await http_.post("a", "nobody", message, **kwargs)
            unknown = await http_.post(
                "nope", None, message, token=http_.token("p1", "nope"), **kwargs
            )
            assert ungranted.status == unknown.status == 403, row
            assert ungranted.body == unknown.body and reason_of(ungranted) == "repository_forbidden"
            for subject in (None, "nobody"):
                status, _h, _b, receives = await router_call(
                    world, "a", None if subject is None else http_.token(subject, "a")
                )
                assert status in {401, 403} and receives == 0, row
        assert observer.since(mark, "lease") == 0 and observer.since(mark, "graph_init") == 0
        assert not [k for k, _ in observer.events[mark:] if k.startswith("tool:")]
        # The authorized column: the same observers see activity.
        for row in ROWS:
            response = await http_.post("a", "p1", message_for(row))
            check_authorized(row, response)
        assert observer.since(mark, "lease", labs.a.repo) >= 1
        assert observer.count("tool:graph_query") >= 1 and observer.count("tool:view_file") >= 1
        # Methods and routes are not secret and need no credentials.
        for method in ("GET", "DELETE"):
            for subject in (None, "p1"):
                response = await http_.request(
                    method,
                    "/mcp/a",
                    headers=http_.headers(None if subject is None else http_.token(subject, "a")),
                )
                assert response.status == 405 and response.header("allow") == "POST"
        for path in ("/mcp", "/x", "/mcp/a/", "/mcp/Bad"):
            response = await http_.request(
                "POST", path, headers=http_.headers(http_.token("p1", "a")), body=b"{}"
            )
            assert response.status == 404


async def test_granted_and_ungranted_tools_per_row(tmp_path, labs):
    world = World(
        tmp_path,
        labs,
        grants=[grant("p1", "a", ["view_file"]), grant("p2", "a", ["graph_query", "list_dir"])],
    )
    world.build()
    async with world.serve() as http_:
        # Rows outside a principal's grant are FORBIDDEN; rows inside it are served.
        expect = {
            ("p1", "view_file"): True,
            ("p1", "list_dir"): False,
            ("p1", "search_text"): False,
            ("p1", "search_symbol"): False,
            ("p1", "graph status"): False,
            ("p1", "retrieve"): False,
            ("p2", "view_file"): False,
            ("p2", "list_dir"): True,
            ("p2", "graph status"): True,
            ("p2", "retrieve"): True,
            ("p2", "page"): True,
        }
        for (subject, row), allowed in expect.items():
            response = await http_.post("a", subject, message_for(row))
            result = response.rpc(1)["result"]
            payload = json.loads(result["content"][0]["text"])
            assert (error_code(payload) == "FORBIDDEN") == (not allowed), (subject, row, payload)
        # Exact: a tool outside the grant is FORBIDDEN, one inside it is served.
        denied = tool_payload(await http_.tool("a", "p1", "list_dir", {"path": "."}))
        assert error_code(denied) == "FORBIDDEN"
        served = tool_payload(
            await http_.tool(
                "a", "p1", "view_file", {"path": "a.py", "start_line": 1, "end_line": 2}
            )
        )
        assert served["_isError"] is False
        denied = tool_payload(await http_.graph("a", "p1", STATUS_ARGS))
        assert error_code(denied) == "FORBIDDEN"
        ok = tool_payload(await http_.graph("a", "p2", STATUS_ARGS))
        assert ok["status"] == "ok"
        # retrieve/context: a repository argument that is the route repository is allowed.
        route = {**RETRIEVE_B, "repository": str(labs.a.repo)}
        assert tool_payload(await http_.graph("a", "p2", route))["status"] == "ok"
        other = {**RETRIEVE_B, "repository": str(labs.b.repo)}
        refused = tool_payload(await http_.graph("a", "p2", other))
        assert error_code(refused) == "FORBIDDEN"
        context = op(op="context", text="A", repository=str(labs.b.repo))
        assert error_code(tool_payload(await http_.graph("a", "p2", context))) == "FORBIDDEN"


async def test_denial_bytes_equal_the_base_forbidden_result(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    app = world.router.apps["a"]
    base_config = Config(
        repo=labs.a.repo,
        memex_root=tmp_path / "bm",
        graph_root=labs.a.graphs,
        max_calls_per_minute=0,
    )
    enforcement, memex, code_graph = build_server_state(base_config)
    try:
        async with world.serve() as http_:
            for version, legacy in ((SELECTED, False), ("2024-11-05", True)):
                response = await http_.tool("a", "p1", "no_such_tool", {}, version=version)
                served = response.rpc(1)["result"]
                expected = await call_tool_result(
                    "no_such_tool",
                    {},
                    base_config,
                    enforcement,
                    memex,
                    code_graph,
                    protocol_version=version,
                )
                assert json.dumps(served, sort_keys=True, separators=(",", ":")) == json.dumps(
                    json.loads(result_bytes(expected)), sort_keys=True, separators=(",", ":")
                )
                assert ("structuredContent" in served) is False
                # test_dry_run and memex_read are denied with the same bytes.
                for name, arguments in (
                    ("test_dry_run", {"path": "a.py"}),
                    ("memex_read", {"ref": "0" * 64}),
                ):
                    denied = await http_.tool("a", "p1", name, arguments, version=version)
                    assert denied.rpc(1)["result"] == served, (name, legacy)
            assert app.config.test_execution_enabled is False
    finally:
        code_graph.close()


async def test_tools_list_bytes_equal_the_plain_s39_manifest(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    plain = http.build_app(
        Config(
            repo=labs.a.repo,
            memex_root=tmp_path / "pm",
            graph_root=labs.a.graphs,
            max_calls_per_minute=0,
        )
    )
    async with world.serve() as shared:
        mine = (await shared.rpc("a", "p1", "tools/list")).rpc(1)["result"]
    async with s39.serving(plain) as (host, port):
        theirs = (await s39.RawHttp(host, port).rpc("tools/list", version=SELECTED)).rpc(1)[
            "result"
        ]
    assert mine == theirs and len(mine["tools"]) == 7
    assert {t["name"] for t in mine["tools"]} >= {
        "test_dry_run",
        "memex_read",
    }  # listed, but denied


# ---------------------------------------------------------------------------- guards and wiring
def plain_app(tmp_path, labs):
    cfg = Config(
        repo=labs.a.repo,
        memex_root=tmp_path / "pm",
        graph_root=labs.a.graphs,
        max_calls_per_minute=0,
    )
    return http.build_app(cfg)


def store_for(world):
    return world.router.store


async def test_guard_tripwires_refuse_an_unexpected_sdk_shape(tmp_path, labs, monkeypatch):
    from mcp import types

    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    store = store_for(world)
    # Control: the verified shape installs.
    app = plain_app(tmp_path, labs)
    guard = shared_http.install_guards(app, "a", store)
    assert guard.repository_id == "a"
    for handler_type, handler in app.server.request_handlers.items():
        if handler_type is types.PingRequest:
            assert not hasattr(handler, shared_http.GUARD_ATTRIBUTE)
        else:
            assert (
                getattr(handler, shared_http.GUARD_ATTRIBUTE)
                == shared_http.GUARD_KINDS[handler_type]
            )
    before = dict(app.server.request_handlers)
    shared_http.install_guards(app, "a", store)  # idempotent: nothing is wrapped twice
    assert app.server.request_handlers == before
    # An extra request handler.
    app = plain_app(tmp_path, labs)

    async def extra(request):
        return types.ServerResult(types.EmptyResult())

    app.server.request_handlers[types.SubscribeRequest] = extra
    with pytest.raises(http.RefusedError):
        shared_http.install_guards(app, "a", store)
    # A notification handler.
    app = plain_app(tmp_path, labs)
    app.server.notification_handlers[types.ProgressNotification] = extra
    with pytest.raises(http.RefusedError):
        shared_http.install_guards(app, "a", store)
    # A missing handler.
    app = plain_app(tmp_path, labs)
    del app.server.request_handlers[types.ListResourcesRequest]
    with pytest.raises(http.RefusedError):
        shared_http.install_guards(app, "a", store)
    # An SDK version mismatch.
    app = plain_app(tmp_path, labs)
    monkeypatch.setattr(shared_http.importlib.metadata, "version", lambda name: "0.0.0")
    with pytest.raises(http.RefusedError):
        shared_http.install_guards(app, "a", store)
    monkeypatch.undo()
    await world.router.aclose_state()


async def test_wiring_types_and_the_experimental_handlers_are_never_touched(
    tmp_path, labs, monkeypatch
):
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.lowlevel.server import _ping_handler

    touched = []
    monkeypatch.setattr(
        Server,
        "experimental",
        property(lambda self: touched.append(1) or (_ for _ in ()).throw(AssertionError)),
    )
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    async with world.serve() as http_:
        assert (await http_.rpc("a", "p1", "tools/list")).status == 200
        assert tool_payload(await http_.graph("a", "p1", STATUS_ARGS))["status"] == "ok"
    assert touched == []
    for _identifier, app in world.router.apps.items():
        enforcement, _memex, code_graph = app.state
        assert type(enforcement) is SharedEnforcement
        assert type(code_graph._lease_registry) is ScopedLeaseRegistry
        assert app.server.request_handlers[types.PingRequest] is _ping_handler
        for handler_type, handler in app.server.request_handlers.items():
            if handler_type is not types.PingRequest:
                assert getattr(handler, "__atlas_guard__", None)
        assert app.gate is world.router.gate
    # The tripwire itself trips on a plain registry, a plain enforcement and an unguarded handler.
    app = world.router.apps["a"]
    registry = app.state[2]._lease_registry
    from atlas_aci.query.leases import LeaseRegistry

    app.state[2]._lease_registry = LeaseRegistry(app.config, app.state[2])
    with pytest.raises(http.RefusedError):
        shared_http.verify_wiring(world.router)
    app.state[2]._lease_registry = registry
    shared_http.verify_wiring(world.router)
    original = app.server.request_handlers[types.ListToolsRequest]
    app.server.request_handlers[types.ListToolsRequest] = (
        original.__wrapped__ if hasattr(original, "__wrapped__") else (lambda r: None)
    )
    with pytest.raises(http.RefusedError):
        shared_http.verify_wiring(world.router)
    app.server.request_handlers[types.ListToolsRequest] = original


async def test_list_tools_without_a_request_inside_and_outside_a_guarded_call(tmp_path, labs):
    from mcp import types

    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    app = world.router.apps["a"]
    handler = app.server.request_handlers[types.ListToolsRequest]
    with pytest.raises(RuntimeError, match="not authorized"):
        await handler(None)  # outside any guarded call
    guard = world.router.guards["a"]
    assert guard.denied["principal_missing"] == 1
    context = AuthzContext(
        Credential(Principal(authz.POLICY_FORMAT, "x"), frozenset()),
        "b",
        frozenset(ALL_TOOLS),
        1,
        "s" * 64,
    )
    token = AUTHZ_CONTEXT.set(context)
    try:
        with pytest.raises(RuntimeError, match="not authorized"):
            await handler(None)  # a context for another repository
    finally:
        AUTHZ_CONTEXT.reset(token)
    # Inside a guarded tools/call the SDK's own cache refresh works: the call below needs it
    # (the tool cache is empty on a fresh server), and it is the only caller of ListTools(None).
    async with world.serve() as http_:
        assert tool_payload(await http_.graph("a", "p1", STATUS_ARGS))["status"] == "ok"
        assert guard.allowed >= 2  # the tools/call and its internal tools-list refresh


async def test_a_sub_application_called_without_the_router_denies_everything(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    app = world.router.apps["a"]
    guard = world.router.guards["a"]
    async with s39.serving(app) as (host, port):
        client = s39.RawHttp(host, port)
        listed = await client.rpc("tools/list", version=SELECTED)
        assert listed.status == 200 and listed.rpc(1)["error"]["code"] == -32600
        assert listed.rpc(1)["error"]["message"] == "Request not authorized."
        called = await client.tool("graph_query", STATUS_ARGS)
        payload = json.loads(called.rpc(1)["result"]["content"][0]["text"])
        assert called.rpc(1)["result"]["isError"] is True and error_code(payload) == "FORBIDDEN"
        read = await client.rpc(
            "resources/read",
            {"uri": "atlas-source://" + "0" * 64 + "?token=x&offset=0&max_bytes=10"},
        )
        assert read.rpc(1)["error"]["data"] == {"reason": "FORBIDDEN"}
    assert guard.denied["principal_missing"] == 3 and guard.allowed == 0
    assert world.router.apps["b"].gate is world.router.gate  # untouched
    await world.router.apps["b"].aclose_state()


async def test_the_guard_fails_closed_and_never_echoes_an_exception(tmp_path, labs, monkeypatch):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    async with world.serve() as http_:
        control = tool_payload(await http_.graph("a", "p1", STATUS_ARGS))
        assert control["status"] == "ok"

        def explode(*args, **kwargs):
            raise RuntimeError("secret-path /etc/shadow")

        monkeypatch.setattr(shared_http, "decide", explode)
        for call in (
            http_.graph("a", "p1", STATUS_ARGS),
            http_.rpc("a", "p1", "tools/list"),
            http_.rpc("a", "p1", "resources/read", {"uri": "atlas-source://x"}),
        ):
            response = await call
            assert "secret" not in response.text and "shadow" not in response.text
            assert response.status == 403  # stage 8 failed closed (the router calls decide too)
        monkeypatch.undo()
        # The guard alone (router decide intact): classify raising inside the handler.
        original = shared_http.classify
        monkeypatch.setattr(shared_http, "classify", explode)
        response = await http_.graph("a", "p1", STATUS_ARGS)
        assert response.status == 200
        payload = json.loads(response.rpc(1)["result"]["content"][0]["text"])
        assert response.rpc(1)["result"]["isError"] is True and error_code(payload) == "FORBIDDEN"
        assert "secret" not in response.text
        listed = await http_.rpc("a", "p1", "tools/list")
        assert (
            listed.rpc(1)["error"]["message"] == "Request not authorized."
            and "secret" not in listed.text
        )
        monkeypatch.setattr(shared_http, "classify", original)
        # The router contains verifier and purge failures.
        monkeypatch.setattr(shared_http.JwtVerifier, "_verify", explode)
        failed = await http_.rpc("a", "p1", "tools/list")
        assert failed.status == 401 and "secret" not in failed.text
        monkeypatch.undo()
        again = tool_payload(await http_.graph("a", "p1", STATUS_ARGS))
        assert again["status"] == "ok"
        monkeypatch.setattr(PolicyStore, "refresh", explode)
        denied = await http_.rpc("a", "p1", "tools/list")
        assert denied.status == 403 and reason_of(denied) == "repository_forbidden"
        assert "secret" not in denied.text


# ---------------------------------------------------------------------------- ContextVar
async def test_authz_context_is_set_only_inside_a_guard(tmp_path, labs, monkeypatch):
    world = World(tmp_path, labs, grants=[grant("p1", "a"), grant("p2", "a")])
    world.build()
    seen = []
    original_refresh = PolicyStore.refresh

    def spy(store):
        seen.append(AUTHZ_CONTEXT.get())  # stage 7 runs inside the router
        return original_refresh(store)

    monkeypatch.setattr(PolicyStore, "refresh", spy)
    async with world.serve() as http_:
        assert AUTHZ_CONTEXT.get() is None
        assert tool_payload(await http_.graph("a", "p1", STATUS_ARGS))["status"] == "ok"
        assert len(seen) >= 1 and all(c is None for c in seen)
        assert AUTHZ_CONTEXT.get() is None

        async def child():
            return AUTHZ_CONTEXT.get()

        assert await asyncio.create_task(child()) is None
        assert tool_payload(await http_.graph("a", "p2", STATUS_ARGS))["status"] == "ok"
        assert AUTHZ_CONTEXT.get() is None
        await router_call(world, "a", http_.token("p1", "a"))
        assert AUTHZ_CONTEXT.get() is None


async def test_two_principals_in_flight_on_one_repository_each_see_their_own_identity(
    tmp_path, labs, monkeypatch
):
    world = World(tmp_path, labs, grants=[grant("p1", "a"), grant("p2", "a")])
    world.build()
    observer = Observer().install(monkeypatch)
    async with world.serve() as http_:
        registry = world.registry("a")
        policy = world.router.store.current()
        expected = {
            subject: authz.decide(
                Credential(
                    Principal(authz.POLICY_FORMAT and "https://idp.example.test", subject),
                    frozenset(),
                ),
                policy,
                "a",
                authz.AccessRequest("tools/call", "graph_query", "page"),
            ).scope_identity
            for subject in ("p1", "p2")
        }
        assert expected["p1"] != expected["p2"]
        # Phase 1: both pages are inside `pending` at the same moment.
        barrier = asyncio.Barrier(2)
        inside = []
        original_pending = ScopedLeaseRegistry.pending

        import contextlib

        @contextlib.asynccontextmanager
        async def spy_pending(self):
            async with original_pending(self) as reservation:
                await asyncio.wait_for(barrier.wait(), 20)
                inside.append(AUTHZ_CONTEXT.get().credential.principal.subject)
                yield reservation

        monkeypatch.setattr(ScopedLeaseRegistry, "pending", spy_pending)
        mark = len(observer.scopes)
        first, second = await asyncio.gather(
            http_.graph("a", "p1", PAGE_ARGS), http_.graph("a", "p2", PAGE_ARGS)
        )
        monkeypatch.setattr(ScopedLeaseRegistry, "pending", original_pending)
        pages = {"p1": tool_payload(first), "p2": tool_payload(second)}
        assert sorted(inside) == ["p1", "p2"]
        assert all(p["status"] == "ok" for p in pages.values())
        used = observer.scopes[mark:]
        assert {c.credential.principal.subject for _, c in used} == {"p1", "p2"}
        assert all(c.scope_identity == expected[c.credential.principal.subject] for _, c in used)
        assert len({lease.scope for lease in registry._leases.values()}) == 2
        # Phase 2: both continues are inside `page` at the same moment.
        owner = {pages[s]["data"]["next_cursor"]: s for s in ("p1", "p2")}
        barrier2 = asyncio.Barrier(2)
        ctx_at_rendezvous = []
        original_page = ScopedLeaseRegistry.page

        async def spy_page(self, token, *args, **kwargs):
            await asyncio.wait_for(barrier2.wait(), 20)
            ctx_at_rendezvous.append(
                (owner[token], AUTHZ_CONTEXT.get().credential.principal.subject)
            )
            return await original_page(self, token, *args, **kwargs)

        monkeypatch.setattr(ScopedLeaseRegistry, "page", spy_page)
        mark = len(observer.scopes)
        done = await asyncio.gather(
            http_.graph("a", "p1", page_cursor(pages["p1"])),
            http_.graph("a", "p2", page_cursor(pages["p2"])),
        )
        monkeypatch.setattr(ScopedLeaseRegistry, "page", original_page)
        assert all(tool_payload(d)["status"] == "ok" for d in done)
        assert sorted(ctx_at_rendezvous) == [("p1", "p1"), ("p2", "p2")]
        used = observer.scopes[mark:]
        assert len(used) >= 2
        assert all(c.scope_identity == expected[c.credential.principal.subject] for _, c in used)
        assert AUTHZ_CONTEXT.get() is None


async def test_the_expiry_timer_never_calls_scope(tmp_path, labs, monkeypatch):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    registry = world.registry("a")
    registry.config.graph_lease_seconds = 1
    observer = Observer().install(monkeypatch)
    async with world.serve() as http_:
        page = tool_payload(await http_.graph("a", "p1", PAGE_ARGS))
        assert page["status"] == "ok" and registry.usage()["active"] == 1
        calls = len(observer.scopes)
        assert calls >= 1  # scope() ran during the request
        assert await s39.poll(lambda: registry.usage()["active"] == 0, 8.0, 0.05)  # the timer ran
        assert len(observer.scopes) == calls  # and called scope() zero times
        # Control: after the expiry the continuation is rejected as expired.
        late = tool_payload(await http_.graph("a", "p1", page_cursor(page)))
        assert late["_isError"] is True and error_code(late) == "EXPIRED_REFERENCE"


async def test_purge_revoked_counts_and_prunes(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a"), grant("p2", "a")])
    world.build()
    async with world.serve() as http_:
        registry = world.registry("a")
        for subject in ("p1", "p2"):
            assert tool_payload(await http_.graph("a", subject, PAGE_ARGS))["status"] == "ok"
        assert registry.usage()["active"] == 2 and len(registry._scope_owner) == 2
        store = world.router.store
        world.write_policy([grant("p2", "a")])
        assert store.refresh() is True
        registry.advance_epoch(store.current().epoch)
        assert await registry.purge_revoked(store.current()) == 2  # a new epoch: both are stale
        assert registry.usage()["active"] == 0 and registry._scope_owner == {}
        assert await registry.purge_revoked(store.current()) == 0
        assert registry.held(Principal("https://idp.example.test", "p1")) == 0


# ---------------------------------------------------------------------------- exhaustion limits
async def test_a_principal_at_its_lease_quota_does_not_block_another(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a"), grant("p2", "a"), grant("p3", "a")])
    world.build()
    async with world.serve() as http_:
        registry = world.registry("a")
        assert registry.quota == 1 and registry.config.max_graph_leases == 4
        assert tool_payload(await http_.graph("a", "p1", PAGE_ARGS))["status"] == "ok"
        full = tool_payload(await http_.graph("a", "p1", PAGE_ARGS))
        assert full["_isError"] is True and error_code(full) == "LEASE_CAPACITY"
        assert tool_payload(await http_.graph("a", "p2", PAGE_ARGS))["status"] == "ok"
        assert tool_payload(await http_.graph("a", "p3", PAGE_ARGS))["status"] == "ok"
        assert (
            registry.usage()["active"] == 3
            and registry.held(Principal("https://idp.example.test", "p1")) == 1
        )
        # Control: the principal at its quota can still use its own cursor and read tools.
        assert tool_payload(await http_.graph("a", "p1", STATUS_ARGS))["status"] == "ok"


async def hold(http_, repository, subject, message):
    """Open a request and send half of its body: the in-flight slot is held until it finishes."""
    reader, writer = await asyncio.open_connection(http_.host, http_.port)
    body = json.dumps(message).encode()
    half = len(body) // 2
    head = s39._headers_block(
        "POST",
        f"/mcp/{repository}",
        f"{http_.host}:{http_.port}",
        http_.headers(http_.token(subject, repository)),
        len(body),
        False,
    )
    writer.write(head + body[:half])
    await writer.drain()
    return reader, writer, body[half:]


async def finish(held):
    reader, writer, rest = held
    writer.write(rest)
    await writer.drain()
    response = await asyncio.wait_for(s39._read_response(reader), 30)
    await s39.close_writer(writer)
    return response


async def test_per_principal_in_flight_cap_leaves_other_principals_unblocked(tmp_path, labs):
    world = World(
        tmp_path,
        labs,
        grants=[grant("p1", "a"), grant("p2", "a")],
        listen={"max_inflight": 4},
    )
    world.build()
    assert world.deployment.listen.max_inflight_per_principal == 2
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with world.serve() as http_:
        held = [await hold(http_, "a", "p1", message) for _ in range(2)]
        await asyncio.sleep(0.5)
        assert world.router.inflight.active(Principal("https://idp.example.test", "p1")) == 2
        third = await http_.post("a", "p1", message)
        assert third.status == 503 and reason_of(third) == "principal_inflight_limit"
        assert third.header("retry-after") == "1"
        other = await http_.post("a", "p2", message)  # not blocked
        assert other.status == 200 and len(other.rpc(1)["result"]["tools"]) == 7
        statuses = [(await finish(h)).status for h in held]
        assert statuses == [200, 200]
        again = await http_.post("a", "p1", message)  # the slots were released
        assert again.status == 200
        assert world.router.inflight.active(Principal("https://idp.example.test", "p1")) == 0
        assert world.router.inflight.rejected == 1


async def test_the_in_flight_slot_is_released_when_the_client_disconnects(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")], listen={"max_inflight": 2})
    world.build()
    assert world.deployment.listen.max_inflight_per_principal == 1
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with world.serve() as http_:
        held = await hold(http_, "a", "p1", message)
        await asyncio.sleep(0.4)
        busy = await http_.post("a", "p1", message)
        assert busy.status == 503
        await s39.close_writer(held[1])
        principal = Principal("https://idp.example.test", "p1")
        assert await s39.poll(lambda: world.router.inflight.active(principal) == 0, 8.0)
        assert (await http_.post("a", "p1", message)).status == 200


async def test_the_shared_gate_spans_repositories(tmp_path, labs):
    world = World(
        tmp_path,
        labs,
        grants=[grant("p1", "a"), grant("p2", "b"), grant("p3", "a")],
        listen={"max_inflight": 2, "max_inflight_per_principal": 2},
    )
    world.build()
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with world.serve() as http_:
        held = [await hold(http_, "a", "p1", message), await hold(http_, "b", "p2", message)]
        await asyncio.sleep(0.5)
        third = await http_.post("a", "p3", message)
        assert third.status == 503 and reason_of(third) == "inflight_limit"
        assert third.header("retry-after") == "1"
        assert [(await finish(h)).status for h in held] == [200, 200]
        assert (await http_.post("a", "p3", message)).status == 200
        assert world.router.gate.snapshot()["peak"] == 2 and world.router.gate.rejected == 1


def test_build_shared_matches_build_server_state(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    router = world.build()
    for _identifier, app in router.apps.items():
        config = app.config
        _e, _m, reference = build_server_state(config)
        try:
            _enforcement, _memex, code_graph = app.state
            assert (
                code_graph.query_limit
                == reference.query_limit
                == config.max_bound_field_elements + 1
            )
            assert code_graph.read_only is reference.read_only is True
            assert code_graph.graph_root == reference.graph_root == config.graph_root
            assert code_graph.atlas_dir == reference.atlas_dir and code_graph.repo == reference.repo
            assert config.test_execution_enabled is False
        finally:
            reference.close()
    asyncio.run(router.aclose_state())


async def test_a_failing_build_closes_everything_built_so_far(tmp_path, labs, monkeypatch):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.load()
    real = http.build_app
    calls = []

    def failing(config, state=None):
        calls.append(config.repo)
        if len(calls) == 2:
            raise RuntimeError("second repository failed")
        return real(config, state)

    monkeypatch.setattr(http, "build_app", failing)
    closed = []
    from atlas_aci.codegraph import CodeGraph

    original_close = CodeGraph.close
    monkeypatch.setattr(
        CodeGraph, "close", lambda self: (closed.append(str(self.repo)), original_close(self))[1]
    )
    with pytest.raises(RuntimeError, match="second repository"):
        world.build()
    assert len(calls) == 2 and len(closed) >= 2


# ---------------------------------------------------------------------------- CLI
def run_cli(config_path, env=None, *extra):
    return subprocess.run(
        [*serve_shared_argv(config_path), *extra],
        capture_output=True,
        env=env or child_env(),
        timeout=90,
    )


def test_cli_usage_errors_exit_two_with_empty_stdout(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    # V1: the option is required.
    done = subprocess.run(
        [sys.executable, "-m", "atlas_aci", "serve-shared"],
        capture_output=True,
        env=child_env(),
        timeout=60,
    )
    assert done.returncode == 2 and done.stdout == b"" and b"--shared-config" in done.stderr
    # V2: an invalid document names its reason and field and never its content.
    document = dict(world.document)
    document["extra"] = "secret-value"
    write_private(world.config_path, json.dumps(document))
    done = run_cli(world.config_path)
    assert done.returncode == 2 and done.stdout == b""
    assert b"--shared-config: invalid shared configuration (unknown_field at /extra)" in done.stderr
    assert b"secret-value" not in done.stderr
    world.write_config()
    # V3: test execution is enabled.
    done = run_cli(world.config_path, child_env({"ATLAS_ACI_ENABLE_TEST_EXECUTION": "1"}))
    assert done.returncode == 2 and done.stdout == b""
    assert b"shared serving is not available while test execution is enabled" in done.stderr
    # V4: a non-loopback host.
    document = json.loads(world.config_path.read_text())
    document["listen"]["host"] = "0.0.0.0"
    write_private(world.config_path, json.dumps(document))
    done = run_cli(world.config_path)
    assert done.returncode == 2 and done.stdout == b"" and b"wildcard_address" in done.stderr
    world.write_config()
    # Missing file.
    done = run_cli(tmp_path / "nope.json")
    assert done.returncode == 2 and b"unreadable" in done.stderr


def test_cli_v5_and_exit_one_fixed_texts(tmp_path, labs, monkeypatch):
    from click.testing import CliRunner

    from atlas_aci.__main__ import cli
    from atlas_aci.query import authn

    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    runner = CliRunner()
    monkeypatch.setitem(authn.MIN_VERSIONS, "pyjwt", (99, 0, 0))
    result = runner.invoke(cli, ["serve-shared", "--shared-config", str(world.config_path)])
    assert result.exit_code == 2 and "requires pyjwt 99.0.0 or newer" in result.output
    monkeypatch.undo()
    import atlas_aci.query.impact as impact

    def broken(config):
        raise ValueError(f"leaky {config.repo}")

    monkeypatch.setattr(impact, "validate_configuration", broken)
    result = runner.invoke(cli, ["serve-shared", "--shared-config", str(world.config_path)])
    assert result.exit_code == 1
    assert "repository a: graph index is unavailable or misconfigured" in result.output
    assert "leaky" not in result.output and str(labs.a.repo) not in result.output
    helped = runner.invoke(cli, ["serve-shared", "--help"])
    assert helped.exit_code == 0 and "--shared-config" in helped.output


def test_cli_bind_failure_is_exit_one_with_the_fixed_text(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]
        document = json.loads(world.config_path.read_text())
        document["listen"]["port"] = port
        write_private(world.config_path, json.dumps(document))
        done = run_cli(world.config_path)
    assert done.returncode == 1 and done.stdout == b""
    assert b"The HTTP listener could not start" in done.stderr and b"Traceback" not in done.stderr


def test_serve_shared_serves_and_stops_on_sigterm(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")], interval=0)
    child = _Child(serve_shared_argv(world.config_path), env=child_env())
    try:
        assert child.port and "repositories=2" in child.stderr_text()

        async def drive():
            client = s39.RawHttp(s39.LOOPBACK, child.port)
            token = world.keys.mint("p1", f"{BASE}/a")
            headers = [*client._rpc_headers(SELECTED, None), ("Authorization", f"Bearer {token}")]
            message = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
            ok = await client.request("POST", "/mcp/a", headers=headers, body=message)
            assert ok.status == 200 and len(ok.rpc(1)["result"]["tools"]) == 7
            none = await client.request(
                "POST",
                "/mcp/a",
                headers=client._rpc_headers(SELECTED, None),
                declared_length=64,
                withhold_body=True,
            )
            assert none.status == 401
            # The policy file is the live source: a revocation is seen without a restart.
            world.write_policy([])
            revoked = await client.request("POST", "/mcp/a", headers=headers, body=message)
            assert revoked.status == 403

        asyncio.run(drive())
        rows = s39.listen_rows(child.port, "tcp") if os.path.exists("/proc/net/tcp") else None
        if rows is not None:
            assert rows == [s39.expected_loopback_row(child.port)]
        started = time.monotonic()
        child.proc.send_signal(15)
        code = child.wait(15)
        assert code == 0 and time.monotonic() - started < 15
        assert (
            "shared_http_stopped" in child.stderr_text() and "Traceback" not in child.stderr_text()
        )
        assert s39.connection_refused(child.port)
    finally:
        child.kill()


async def test_the_guard_sets_the_context_only_around_the_original_and_always_resets_it(
    tmp_path, labs
):
    """Unit level: the same task sees the context inside the handler and never afterwards."""
    from types import SimpleNamespace

    from mcp import types

    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    store = world.router.store
    seen = []
    mode = {"raise": False}

    async def original(req):
        seen.append(AUTHZ_CONTEXT.get())
        if mode["raise"]:
            raise RuntimeError("handler failed")
        return types.ServerResult(types.EmptyResult())

    credential = Credential(Principal("https://idp.example.test", "p1"), frozenset())
    request = SimpleNamespace(scope={shared_http.PRINCIPAL_SCOPE_KEY: credential}, headers={})
    server = SimpleNamespace(
        request_handlers={t: original for t in shared_http.HANDLER_TYPES},
        notification_handlers={},
        request_context=SimpleNamespace(request=request),
    )
    app = SimpleNamespace(server=server, config=world.router.apps["a"].config)
    shared_http.install_guards(app, "a", store)  # type: ignore[arg-type]
    handler = server.request_handlers[types.ListToolsRequest]
    assert AUTHZ_CONTEXT.get() is None
    await handler(types.ListToolsRequest(method="tools/list"))
    assert len(seen) == 1 and seen[0] is not None
    assert seen[0].repository_id == "a" and seen[0].credential == credential
    assert AUTHZ_CONTEXT.get() is None  # reset after a normal return
    mode["raise"] = True
    with pytest.raises(RuntimeError, match="handler failed"):
        await handler(types.ListToolsRequest(method="tools/list"))
    assert len(seen) == 2 and AUTHZ_CONTEXT.get() is None  # reset after an exception too
    # A denied request never runs the original and never sets the context.
    world.write_policy([])
    store.refresh()
    denied = server.request_handlers[types.CallToolRequest]
    request.scope[shared_http.PRINCIPAL_SCOPE_KEY] = credential
    call = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="graph_query", arguments=STATUS_ARGS),
    )
    result = await denied(call)
    assert len(seen) == 2 and result.root.isError is True and AUTHZ_CONTEXT.get() is None
    await world.router.aclose_state()
