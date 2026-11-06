"""ACI-S40 acceptance criteria AC-S40-001 .. AC-S40-009 (frozen criteria; no mock of the behaviour
under test; spies only count, and every zero is paired with a positive control)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_s30_support as s30
import test_s38_support as s38
from test_s40_support import (
    ALL_TOOLS,
    BASE,
    IMPACT_ARGS,
    ISSUER,
    PAGE_ARGS,
    RETRIEVE_B,
    STATUS_ARGS,
    V1,
    V2,
    Observer,
    World,
    error_code,
    fake_logger,
    fake_runs,
    get_labs,
    grant,
    page_cursor,
    router_call,
    tool_payload,
)

from atlas_aci.query import authz
from atlas_aci.query.authz import (
    AccessRequest,
    Credential,
    Principal,
    ScopedLeaseRegistry,
    decide,
    parse_policy,
)
from atlas_aci.query.leases import LeaseRegistry, digest

LIVE = s38.LIVE_SOURCE


@pytest.fixture(scope="module")
def labs(tmp_path_factory):
    return get_labs(tmp_path_factory.mktemp("s40labs"))


def withheld(body: dict) -> dict:
    return {"withhold_body": True, "declared_length": len(json.dumps(body).encode())}


def cred(subject: str, *scopes: str) -> Credential:
    return Credential(Principal(ISSUER, subject), frozenset(scopes))


def reason_of(response) -> str:
    return response.json()["error"]["data"]["reason"]


# ------------------------------------------------------------------------------------ AC-001
async def test_unauthorized_before_traversal(tmp_path, labs, monkeypatch):
    world = World(
        tmp_path,
        labs,
        grants=[grant("p1", "a"), grant("p1", "b"), grant("p2", "b")],
    )
    world.build()
    observer = Observer().install(monkeypatch, watch=[labs.a.repo])
    async with world.serve() as http:
        a_repo = str(labs.a.repo)
        retrieve = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
        probes = {
            "retrieve": {"name": "graph_query", "arguments": RETRIEVE_B},
            "neighborhood": {"name": "graph_query", "arguments": PAGE_ARGS["query"]},
            "page": {"name": "graph_query", "arguments": PAGE_ARGS},
        }
        mark = observer.mark()
        for name, params in probes.items():
            message = {**retrieve, "params": params}
            blocked = await http.post("a", "p2", message, **withheld(message))
            assert blocked.status == 403, name
            assert reason_of(blocked) == "repository_forbidden", name
            status, _headers, _body, receives = await router_call(world, "a", http.token("p2", "a"))
            assert status == 403 and receives == 0, (name, status, receives)
        assert observer.since(mark, "lease", a_repo) == 0
        assert observer.since(mark, "graph_init", a_repo) == 0
        assert observer.since(mark, "retrieve") == 0
        assert observer.since(mark, "navigate") == 0
        assert observer.since(mark, "read_span") == 0
        assert observer.since(mark, "path:read_bytes") == 0
        # (b) a repository argument naming another repository: denied before any lease.
        mark = observer.mark()
        other = {**RETRIEVE_B, "repository": a_repo}
        denied = tool_payload(await http.graph("b", "p2", other))
        assert denied["_isError"] is True and error_code(denied) == "FORBIDDEN", denied
        assert observer.since(mark, "lease", a_repo) == 0
        assert observer.since(mark, "lease", labs.b.repo) == 0
        assert observer.since(mark, "retrieve") == 0
        # Controls: the same observers read >= 1 for authorized requests.
        mark = observer.mark()
        status, _headers, _body, receives = await router_call(world, "a", http.token("p1", "a"))
        assert receives >= 1, (status, receives)
        granted = tool_payload(await http.graph("a", "p1", RETRIEVE_B))
        assert granted["status"] == "ok" and granted["data"]["repository_filter_matched"] is True
        assert observer.since(mark, "lease", a_repo) >= 1
        assert observer.since(mark, "retrieve") >= 1
        assert sum(granted["data"]["work"].values()) > 0
        mark = observer.mark()
        own = {**RETRIEVE_B, "repository": str(labs.b.repo)}
        control = tool_payload(await http.graph("b", "p2", own))
        assert control["status"] == "ok" and control["data"]["repository_filter_matched"] is True
        assert observer.since(mark, "lease", labs.b.repo) >= 1
        assert observer.since(mark, "retrieve") >= 1


# ------------------------------------------------------------------------------------ AC-002
async def test_resource_authorization(tmp_path, labs, monkeypatch):
    world = World(
        tmp_path,
        labs,
        grants=[
            grant("p1", "a"),
            grant("p1", "b"),
            grant("p2", "a", ["graph_query"]),
            grant("p4", "a", ["list_dir"]),
        ],
    )
    world.build()
    observer = Observer().install(monkeypatch)
    async with world.serve() as http:
        page = tool_payload(await http.graph("a", "p1", PAGE_ARGS))
        assert page["status"] == "ok"
        descriptor = page["data"]["resources"][0]
        uri = descriptor["uri"]

        async def read(repository, subject, address, **kwargs):
            return await http.rpc(repository, subject, "resources/read", {"uri": address}, **kwargs)

        # Another principal (granted the repository) presents P1's resource URI.
        mark = observer.mark()
        crossed = await read("a", "p2", uri)
        assert crossed.status == 200
        error = crossed.rpc(1)["error"]
        assert error["code"] == -32602 and error["data"] == {"reason": "SCOPE_MISMATCH"}
        assert observer.since(mark, "read_span") == 0
        # P1's URI used on another repository's route.
        wrong_repo = (await read("b", "p1", uri)).rpc(1)["error"]
        assert wrong_repo["data"] == {"reason": "INVALID_REFERENCE"}
        # A principal with no grant is stopped at the router, before the body.
        mark = observer.mark()
        body = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": uri}}
        blocked = await http.post("a", "p3", body, **withheld(body))
        assert blocked.status == 403 and reason_of(blocked) == "repository_forbidden"
        assert observer.since(mark, "authenticate") == 0
        # A tampered resource id.
        head = uri.split("://", 1)[1].split("?", 1)[0]
        flipped = ("0" if head[0] != "0" else "1") + head[1:]
        tampered = await read("a", "p1", uri.replace(head, flipped, 1))
        assert tampered.rpc(1)["error"]["data"] == {"reason": "INVALID_REFERENCE"}
        # Granted the repository but not graph_query: the guard rejects before authentication.
        mark = observer.mark()
        no_tool = (await read("a", "p4", uri)).rpc(1)["error"]
        assert no_tool["code"] == -32602 and no_tool["data"] == {"reason": "FORBIDDEN"}
        assert observer.since(mark, "authenticate") == 0
        # Control: the owner reads the bytes of the span; the same observers read >= 1.
        mark = observer.mark()
        own = await read("a", "p1", uri)
        contents = own.rpc(1)["result"]["contents"][0]
        blob = base64.b64decode(contents["blob"])
        start = descriptor.get("start_byte", 0)
        assert (blob and LIVE[start : start + len(blob)] == blob) or blob in LIVE
        assert observer.since(mark, "read_span") >= 1
        assert observer.since(mark, "authenticate") >= 1


# ------------------------------------------------------------------------------------ AC-003
async def test_cache_scope_isolation(tmp_path, labs, monkeypatch):
    world = World(tmp_path, labs, grants=[grant("p1", "a"), grant("p2", "a", ["graph_query"])])
    world.build()
    observer = Observer().install(monkeypatch)
    async with world.serve() as http:
        registry = world.registry("a")
        policy = world.router.store.current()
        results = {}
        for subject in ("p1", "p2"):
            observer.mark()
            scopes_before = len(observer.scopes)
            payload = tool_payload(await http.graph("a", subject, PAGE_ARGS))  # identical POST
            assert payload["status"] == "ok", payload
            used = observer.scopes[scopes_before:]
            assert used, "no scope() call was observed"
            expected = decide(
                cred(subject),
                policy,
                "a",
                AccessRequest("tools/call", "graph_query", "page"),
            ).scope_identity
            assert expected is not None
            assert all(r == "a" and c is not None for r, c in used)
            assert all(c.scope_identity == expected for _, c in used)
            assert all(c.credential.principal.subject == subject for _, c in used)
            results[subject] = (expected, payload)
        assert results["p1"][0] != results["p2"][0]
        assert results["p1"][1]["data"]["query_digest"] != results["p2"][1]["data"]["query_digest"]
        # Independent recomputation of each lease's scope and each token's authority.
        base = LeaseRegistry.scope(registry)
        leases = list(registry._leases.values())
        assert len(leases) == 2
        assert {lease.scope for lease in leases} == {
            digest({"base": base, "authz": results[s][0]}) for s in ("p1", "p2")
        }
        assert len({lease.scope for lease in leases}) == 2
        authorities = {}
        for subject in ("p1", "p2"):
            token = results[subject][1]["data"]["next_cursor"]
            body = token.split(".")[0]
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            authorities[subject] = json.loads(raw)["authority"]
            assert authorities[subject] == digest({"base": base, "authz": results[subject][0]})
        assert authorities["p1"] != authorities["p2"]
        # P2 resumes with P1's cursor and P1's digest: refused.
        stolen = page_cursor(results["p1"][1])
        refused = tool_payload(await http.graph("a", "p2", stolen))
        assert refused["_isError"] is True and error_code(refused) == "SCOPE_MISMATCH", refused
        # Control: P1 continues its own cursor.
        own = tool_payload(await http.graph("a", "p1", stolen))
        assert own["status"] == "ok" and own["_isError"] is False


# ------------------------------------------------------------------------------------ AC-004
def epoch(world: World) -> int:
    return world.router.store.current().epoch


async def test_revoked_cursor(tmp_path, labs, monkeypatch):
    base_grants = [grant("p1", "a"), grant("p1", "b"), grant("p2", "b")]
    world = World(tmp_path, labs, grants=base_grants, interval=0)
    world.build()
    async with world.serve() as http:
        registry = world.registry("a")
        e0 = epoch(world)
        page = tool_payload(await http.graph("a", "p1", PAGE_ARGS))
        cursor = page_cursor(page)
        assert registry.usage()["active"] == 1
        # Control first: rewriting identical bytes (new mtime, same digest) keeps the epoch.
        world.write_policy(base_grants)
        os.utime(world.policy_path, (1_000_000_000, 1_000_000_000))
        again = tool_payload(await http.graph("a", "p1", cursor))
        assert again["status"] == "ok" and epoch(world) == e0
        # (i) Revoke P1's grant on A: the continuation is refused and the lease is purged.
        world.write_policy([g for g in base_grants if g != grant("p1", "a")])
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
        body["params"] = {"name": "graph_query", "arguments": cursor}
        revoked = await http.post("a", "p1", body, **withheld(body))
        assert revoked.status == 403 and reason_of(revoked) == "repository_forbidden"
        assert epoch(world) == e0 + 1
        assert registry.usage()["active"] == 0
        # (ii) Re-grant, page again, then change an unrelated grant. With the purge patched to a
        # no-op the continuation must still be refused: the scope identity enforces revocation.
        world.write_policy(base_grants)
        page2 = tool_payload(await http.graph("a", "p1", PAGE_ARGS))
        cursor2 = page_cursor(page2)
        assert registry.usage()["active"] == 1
        monkeypatch.setattr(ScopedLeaseRegistry, "purge_revoked", _no_purge)
        world.write_policy([*base_grants, grant("p9", "b")])
        denied = tool_payload(await http.graph("a", "p1", cursor2))
        assert denied["_isError"] is True and error_code(denied) == "SCOPE_MISMATCH", denied
        assert registry.usage()["active"] == 1, "the stale lease was kept (purge disabled)"
        monkeypatch.undo()
        # (iii) Revoke, observe, re-grant identical bytes: the old cursor is never revived.
        world.write_policy(base_grants)
        page3 = tool_payload(await http.graph("a", "p1", PAGE_ARGS))
        cursor3 = page_cursor(page3)
        before = epoch(world)
        world.write_policy([g for g in base_grants if g != grant("p1", "a")])
        probe = await http.graph("a", "p1", PAGE_ARGS, **withheld({"x": 1}))
        assert probe.status == 403
        world.write_policy(base_grants)
        revived = tool_payload(await http.graph("a", "p1", cursor3))
        assert revived["_isError"] is True
        assert error_code(revived) in {"SCOPE_MISMATCH", "EXPIRED_REFERENCE"}, revived
        assert epoch(world) == before + 2
        # Control: a fresh page under the new epoch works and resumes.
        fresh = tool_payload(await http.graph("a", "p1", PAGE_ARGS))
        assert fresh["status"] == "ok"
        resumed = tool_payload(await http.graph("a", "p1", page_cursor(fresh)))
        assert resumed["status"] == "ok"


async def _no_purge(self, policy):
    self.advance_epoch(policy.epoch)
    return 0


# ------------------------------------------------------------------------------------ AC-005
def pair_world(tmp_path, grants, **kwargs):
    import test_s19_support as s19

    (tmp_path / "pair").mkdir()
    repo, before, after = s19.same_path_pair(tmp_path / "pair", V1, V2)
    labs = SimpleNamespace(
        a=SimpleNamespace(repo=repo, graphs=after.graph_root),
        c=SimpleNamespace(repo=repo, graphs=before.graph_root),
    )
    world = World(
        tmp_path,
        labs,
        repos={"a": labs.a, "c": labs.c},
        grants=grants,
        befores={"a": "c"},
        **kwargs,
    )
    return world, repo, before, after


async def test_cross_repo_scope(tmp_path, labs, monkeypatch):
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.query.shared_http import namespace_of

    world, repo, before, after = pair_world(
        tmp_path,
        [grant("p1", "a"), grant("p2", "a"), grant("p2", "c")],
    )
    graph_c = CodeGraph(repo, read_only=True, graph_root=before.graph_root)
    with graph_c.reader_lease() as reader:
        generation_c = reader.generation_id
    graph_c.close()
    world.load()
    from atlas_aci.query.shared_http import validate_deployment

    validate_deployment(world.deployment)  # the same-path pair passes impact validation
    world.build()
    observer = Observer().install(monkeypatch)
    ns_a = namespace_of(repo, after.graph_root)
    ns_c = namespace_of(repo, before.graph_root)
    async with world.serve() as http:
        mark = observer.mark()
        denied = tool_payload(await http.graph("a", "p1", IMPACT_ARGS))
        assert denied["_isError"] is True and error_code(denied) == "FORBIDDEN", denied
        assert observer.since(mark, "lease_ns", ns_c) == 0
        assert observer.since(mark, "lease_ns", ns_a) == 0
        assert observer.since(mark, "graph_init_ns", ns_c) == 0
        assert observer.since(mark, "tool:graph_query") == 0
        # Control: a principal granted both sides expands the before repository.
        mark = observer.mark()
        allowed = tool_payload(await http.graph("a", "p2", IMPACT_ARGS))
        assert allowed["_isError"] is False and allowed["status"] == "ok", allowed
        assert observer.since(mark, "lease_ns", ns_a) >= 1
        assert observer.since(mark, "lease_ns", ns_c) >= 1
        assert observer.since(mark, "graph_init_ns", ns_c) >= 1
        assert generation_c in json.dumps(allowed["data"])


def test_cross_repo_scope_path_table():
    """authorize_path over an S30 world's repository ids: the walk stops at the first denial."""
    package = s30.make_package()
    document = s30.mapping_document("a" * 64, "b" * 64, package)
    ids = [row["repository_id"] for row in document["repositories"]]
    assert ids == ["app", "lib"]
    registrations = {i: authz.Registration(i, "/srv/" + i, "/g/" + i, None) for i in ids}
    principal = Principal(ISSUER, "p1")

    def snapshot(*repos):
        policy = {
            "format": "atlas-aci-shared-policy.v1",
            "grants": [grant("p1", r, ["graph_query"]) for r in repos],
        }
        return parse_policy(json.dumps(policy).encode(), issuer=ISSUER, registrations=registrations)

    credential = Credential(principal, frozenset())
    one = authz.authorize_path(credential, snapshot("app"), ids)
    assert one == authz.PathDecision(("app",), "lib", "repository_not_granted")
    both = authz.authorize_path(credential, snapshot("app", "lib"), ids)
    assert both.denied_at is None and both.authorized == ("app", "lib")
    none = authz.authorize_path(credential, snapshot(), ids)
    assert none.authorized == () and none.denied_at == "app"
    assert authz.authorize_path(credential, snapshot("lib"), ids).denied_at == "app"


# ------------------------------------------------------------------------------------ AC-006
PRINCIPALS = ("p1", "p2", "p3")
REPOS = ("r1", "r2", "r3")


def replay_requests():
    requests = [AccessRequest(kind) for kind in authz.KINDS]
    for tool in (*sorted(ALL_TOOLS), "test_dry_run", "memex_read", "bogus"):
        requests.append(AccessRequest("tools/call", tool))
    for op in (None, "status", "page", "retrieve", "context", "impact"):
        requests.append(AccessRequest("tools/call", "graph_query", op))
    requests.append(AccessRequest("tools/call", "graph_query", "retrieve", "other"))
    requests.append(AccessRequest("tools/call", "search_text", None, "absent", "rejected"))
    return requests


def replay_grants(order: int = 1):
    grants = [
        grant("p1", "r1", ["graph_query", "view_file", "search_text"]),
        grant("p1", "r2", ["list_dir"]),
        grant("p2", "r2", ALL_TOOLS, scope="atlas.read"),
        grant("p2", "r3", ["search_symbol", "graph_query"]),
        grant("p3", "r1", ALL_TOOLS),
    ]
    if order < 0:
        grants.reverse()
        for item in grants:
            item["tools"] = list(reversed(item["tools"]))
    return grants


def replay_policy(order: int = 1):
    registrations = {
        r: authz.Registration(r, "/srv/" + r, "/g/" + r, "r2" if r == "r1" else None) for r in REPOS
    }
    document = {"format": "atlas-aci-shared-policy.v1", "grants": replay_grants(order)}
    return parse_policy(json.dumps(document).encode(), issuer=ISSUER, registrations=registrations)


def replay_digest(policy) -> str:
    rows = []
    for subject in PRINCIPALS:
        for scopes in ((), ("atlas.read",)):
            for repository in REPOS:
                for request in replay_requests():
                    decision = decide(cred(subject, *scopes), policy, repository, request)
                    again = decide(cred(subject, *scopes), policy, repository, request)
                    assert decision.digest() == again.digest()
                    rows.append([subject, list(scopes), repository, decision.digest()])
    return digest(sorted(rows))


def test_auth_replay():
    policy = replay_policy()
    shuffled = replay_policy(-1)
    assert shuffled.digest == policy.digest
    total = replay_digest(policy)
    assert total == replay_digest(shuffled)
    allowed = [
        decide(cred(s), policy, r, q).allowed
        for s in PRINCIPALS
        for r in REPOS
        for q in replay_requests()
    ]
    assert any(allowed) and not all(allowed)
    # A fresh interpreter, two hash seeds: identical digests.
    script = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);import test_s40_contract as t;"
        "print(t.replay_digest(t.replay_policy()))"
    )
    outputs = set()
    for seed in ("0", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", script, str(Path(__file__).parent)],
            capture_output=True,
            env=env,
            timeout=120,
            text=True,
        )
        assert done.returncode == 0, done.stderr[-400:]
        outputs.add(done.stdout.strip())
    assert outputs == {total}
    # Non-vacuity: any input change gives a different digest.
    base = decide(cred("p1"), policy, "r1", AccessRequest("tools/call", "view_file")).digest()
    other_principal = decide(cred("p3"), policy, "r1", AccessRequest("tools/call", "view_file"))
    other_repo = decide(cred("p1"), policy, "r2", AccessRequest("tools/call", "view_file"))
    other_tool = decide(cred("p1"), policy, "r1", AccessRequest("tools/call", "list_dir"))
    assert len({base, other_principal.digest(), other_repo.digest(), other_tool.digest()}) == 4
    changed = replay_grants()
    changed[0] = grant("p1", "r1", ["graph_query", "view_file"])
    document = {"format": "atlas-aci-shared-policy.v1", "grants": changed}
    registrations = dict(policy.registrations)
    altered = parse_policy(
        json.dumps(document).encode(), issuer=ISSUER, registrations=registrations
    )
    assert altered.digest != policy.digest


async def test_auth_replay_http(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a"), grant("p2", "b")])
    world.build()
    async with world.serve() as http:
        requests = [
            ("a", "p1", "tools/list", None),
            ("a", "p1", "tools/call", {"name": "graph_query", "arguments": STATUS_ARGS}),
            ("a", "p1", "tools/call", {"name": "test_dry_run", "arguments": {"path": "a.py"}}),
            ("a", "p2", "tools/list", None),
            (
                "b",
                "p2",
                "tools/call",
                {"name": "search_text", "arguments": {"pattern": "-f", "scope": "."}},
            ),
        ]
        statuses = []
        for repository, subject, method, params in requests:
            first = await http.rpc(repository, subject, method, params)
            second = await http.rpc(repository, subject, method, params)
            assert first.status == second.status and first.body == second.body
            statuses.append(first.status)
        assert 200 in statuses and 403 in statuses


# ------------------------------------------------------------------------------------ AC-007
async def test_cache_revocation(tmp_path, labs, monkeypatch):
    grants = [grant("p1", "a"), grant("p2", "b")]
    world = World(tmp_path, labs, grants=grants, limits={"max_leases_per_principal": 2})
    world.build()
    observer = Observer().install(monkeypatch)
    async with world.serve() as http:
        registry = world.registry("a")
        warmed = await http.graph("a", "p1", PAGE_ARGS)
        first = tool_payload(warmed)
        names = {"A", "B"}
        text = warmed.text
        assert all(name in text for name in names), "the warmed page carries the fixture entities"
        # Control: before revocation the repeated query returns the evidence again.
        mark = observer.mark()
        repeat = await http.graph("a", "p1", PAGE_ARGS)
        assert repeat.status == 200 and "neighborhood" in repeat.text
        assert observer.since(mark, "lease", labs.a.repo) >= 1
        assert registry.usage()["active"] == 2 and first["status"] == "ok"
        # Revoke: the same POST is refused and no evidence leaves.
        world.write_policy([grant("p2", "b")])
        mark = observer.mark()
        revoked = await http.graph("a", "p1", PAGE_ARGS)
        assert revoked.status == 403
        for name in names:
            assert f'"{name}"' not in revoked.text
        assert "neighborhood" not in revoked.text and "next_cursor" not in revoked.text
        assert observer.since(mark, "lease", labs.a.repo) == 0
        assert registry.usage()["active"] == 0
        assert registry._scope_owner == {}
        # Variant: the repository stays granted but graph_query is removed.
        world.write_policy([grant("p1", "a", ["list_dir"]), grant("p2", "b")])
        mark = observer.mark()
        for arguments in (
            PAGE_ARGS,
            {**PAGE_ARGS, "op": "continue", "cursor": "x", "query_digest": "0" * 64},
        ):
            payload = tool_payload(await http.graph("a", "p1", arguments))
            assert payload["_isError"] is True, payload
            assert error_code(payload) == "FORBIDDEN"
        assert observer.since(mark, "lease", labs.a.repo) == 0
        assert observer.since(mark, "tool:graph_query") == 0


# ------------------------------------------------------------------------------------ AC-008
CALLS = {
    "view_file": {"path": "a.py", "start_line": 1, "end_line": 2},
    "list_dir": {"path": "."},
    "search_text": {"pattern": "A", "scope": "."},
    "search_symbol": {"name": "A"},
    "graph_query": STATUS_ARGS,
    "test_dry_run": {"path": "a.py"},
    "memex_read": {"ref": "0" * 64},
}
SHAREABLE = ("view_file", "list_dir", "search_text", "search_symbol", "graph_query")
CASES = ("principal_not_granted", "tool_not_granted", "enforcement_direct")


def fake_tools(tmp_path, monkeypatch):
    directory = tmp_path / "fakebin"
    directory.mkdir()
    logs = {}
    for name in ("rg", "pytest"):
        _script, logs[name] = fake_logger(directory, name)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return logs


@pytest.mark.parametrize("tool", sorted(CALLS))
@pytest.mark.parametrize("case", CASES)
async def test_legacy_tool_boundary(tmp_path, labs, monkeypatch, tool, case):
    logs = fake_tools(tmp_path, monkeypatch)
    others = [t for t in SHAREABLE if t != tool]
    grants = [
        grant("p1", "a", others if tool in SHAREABLE else ALL_TOOLS),
        grant("p1", "b"),
        grant("p2", "b"),
        grant("p5", "a", ALL_TOOLS),
    ]
    world = World(tmp_path, labs, grants=grants)
    world.build()
    observer = Observer().install(monkeypatch, watch=[labs.a.repo, labs.b.repo])
    a_repo = labs.a.repo

    def quiet(mark):
        assert observer.since(mark, "lease", a_repo) == 0
        assert observer.since(mark, "source_lease", a_repo) == 0
        assert observer.since(mark, "memex_read") == 0
        assert observer.since(mark, "read_span") == 0
        assert observer.since(mark, "path:iterdir", a_repo) == 0
        assert observer.since(mark, "path:read_bytes", a_repo) == 0
        assert observer.since(mark, "tool:" + tool, a_repo) == 0
        assert fake_runs(logs["rg"]) == [] and fake_runs(logs["pytest"]) == []

    async with world.serve() as http:
        mark = observer.mark()
        if case == "principal_not_granted":
            response = await http.tool("a", "p2", tool, CALLS[tool])
            assert response.status == 403 and reason_of(response) == "repository_forbidden"
        elif case == "tool_not_granted":
            response = await http.tool("a", "p1", tool, CALLS[tool])
            payload = tool_payload(response)
            assert payload["_isError"] is True and error_code(payload) == "FORBIDDEN", payload
        else:
            from atlas_aci.query.authz import AUTHZ_CONTEXT, AuthzContext
            from atlas_aci.server import dispatch_tool_call

            app = world.router.apps["a"]
            enforcement, memex, code_graph = app.state
            config = app.config
            from atlas_aci.enforcement import ToolError

            foreign = AuthzContext(cred("p1"), "b", frozenset(ALL_TOOLS), 1, "x" * 64)
            for context in (None, foreign):
                token = AUTHZ_CONTEXT.set(context)
                try:
                    with pytest.raises(ToolError) as caught:
                        await dispatch_tool_call(
                            tool, CALLS[tool], config, enforcement, memex, code_graph
                        )
                finally:
                    AUTHZ_CONTEXT.reset(token)
                assert caught.value.code == "FORBIDDEN"
        quiet(mark)
        # Control: the same observers see activity when the tool is allowed.
        if tool in SHAREABLE:
            mark = observer.mark()
            payload = tool_payload(await http.tool("a", "p5", tool, CALLS[tool]))
            assert observer.since(mark, "tool:" + tool, a_repo) >= 1, payload
            if tool == "search_text":
                assert fake_runs(logs["rg"]), "the fake rg was spawned for an allowed search"
    if tool in {"test_dry_run", "memex_read"}:
        await _plain_mode_control(tmp_path, monkeypatch, logs, observer, tool)


async def _plain_mode_control(tmp_path, monkeypatch, logs, observer, tool):
    """Outside shared mode the same dispatch reaches the legacy tool (the control)."""
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.config import Config
    from atlas_aci.enforcement import Enforcement
    from atlas_aci.memex import Memex
    from atlas_aci.server import dispatch_tool_call

    repo = tmp_path / "plain" / "repo"
    repo.mkdir(parents=True)
    (repo / "test_x.py").write_text("def test_x():\n    pass\n")
    config = Config(
        repo=repo,
        memex_root=tmp_path / "plain" / "memex",
        max_calls_per_minute=0,
        test_execution_enabled=True,
    )
    code_graph = CodeGraph(repo, read_only=True)
    memex = Memex(config.memex_root)
    mark = observer.mark()
    try:
        if tool == "test_dry_run":
            await dispatch_tool_call(
                "test_dry_run",
                {"path": "test_x.py"},
                config,
                Enforcement(config),
                memex,
                code_graph,
            )
            assert fake_runs(logs["pytest"]), "the fake pytest ran in plain mode"
        else:
            with pytest.raises(Exception):  # noqa: B017 - NOT_FOUND for an unknown ref
                await dispatch_tool_call(
                    "memex_read", {"ref": "0" * 64}, config, Enforcement(config), memex, code_graph
                )
            assert observer.since(mark, "memex_read") == 1
    finally:
        code_graph.close()


async def test_cross_repo_file_access(tmp_path, labs, monkeypatch):
    """Absolute paths, symlinks and snapshot ids from another registered repository."""
    foreign = labs.b.repo
    link = labs.c.repo / "escape.py"
    link.symlink_to(foreign / "a.py")
    try:
        world = World(
            tmp_path,
            labs,
            repos={"a": labs.a, "b": labs.b, "c": labs.c},
            grants=[
                grant("p1", "a"),
                grant("p2", "c", ["view_file", "list_dir", "search_text"]),
                grant("p2", "b", ["view_file"]),
            ],
        )
        world.build()
        observer = Observer().install(monkeypatch, watch=[foreign])
        view = {"start_line": 1, "end_line": 2}
        async with world.serve() as http:
            mark = observer.mark()
            absolute = tool_payload(
                await http.tool("c", "p2", "view_file", {"path": str(foreign / "a.py"), **view})
            )
            assert absolute["_isError"] is True and error_code(absolute) == "FORBIDDEN"
            escaped = tool_payload(
                await http.tool("c", "p2", "view_file", {"path": "escape.py", **view})
            )
            assert escaped["_isError"] is True and error_code(escaped) == "FORBIDDEN"
            listing = tool_payload(await http.tool("c", "p2", "list_dir", {"path": str(foreign)}))
            assert listing["_isError"] is True and error_code(listing) == "FORBIDDEN"
            assert observer.since(mark, "path:read_bytes", foreign) == 0
            assert observer.since(mark, "path:iterdir", foreign) == 0
            # A snapshot id issued for repository A, replayed on repository B's route.
            page = tool_payload(await http.graph("a", "p1", PAGE_ARGS))
            snapshot = page["data"]["resources"][0]["snapshot_id"]
            mark = observer.mark()
            replay = tool_payload(
                await http.tool(
                    "b", "p2", "view_file", {"path": "a.py", "snapshot_id": snapshot, **view}
                )
            )
            assert replay["_isError"] is True, replay
            assert observer.since(mark, "read_span") == 0
            assert observer.since(mark, "source_lease", labs.a.repo) == 0
            # Controls: the same observers read >= 1 for the granted reads.
            mark = observer.mark()
            own = tool_payload(await http.tool("b", "p2", "view_file", {"path": "a.py", **view}))
            assert own["_isError"] is False, own
            assert observer.since(mark, "path:read_bytes", foreign) >= 1
            mark = observer.mark()
            pinned = tool_payload(
                await http.tool(
                    "a", "p1", "view_file", {"path": "a.py", "snapshot_id": snapshot, **view}
                )
            )
            assert pinned["_isError"] is False, pinned
            assert observer.since(mark, "source_lease", labs.a.repo) >= 1
    finally:
        link.unlink()


# ------------------------------------------------------------------------------------ AC-009
def expected_challenge(reason_missing: bool, repository: str = "a") -> str:
    url = f"https://atlas.example.test/.well-known/oauth-protected-resource/mcp/{repository}"
    if reason_missing:
        return f'Bearer resource_metadata="{url}"'
    return f'Bearer error="invalid_token", resource_metadata="{url}"'


def credential_cases(keys, audience: str):
    claims = keys.claims("p1", audience)
    text = json.dumps(claims)
    import time

    now = int(time.time())
    cases = {
        "absent": (None, "header_missing"),
        "basic": (["Basic dXNlcjpwdw=="], "scheme_invalid"),
        "empty_bearer": (["Bearer "], "token_malformed", "scheme_invalid"),
        "duplicate": (
            [f"Bearer {keys.mint('p1', audience)}", f"Bearer {keys.mint('p1', audience)}"],
            "header_duplicate",
        ),
        "oversize": (["Bearer " + "A" * 8193], "token_oversize"),
        "malformed": (["Bearer abc"], "token_malformed"),
        "expired": ([f"Bearer {keys.mint('p1', audience, ttl=-300, iat=now - 600)}"], "expired"),
        "not_yet_valid": ([f"Bearer {keys.mint('p1', audience, nbf=now + 120)}"], "not_yet_valid"),
        "wrong_audience": (
            [f"Bearer {keys.mint('p1', 'https://atlas.example.test/mcp/b')}"],
            "audience_invalid",
        ),
        "wrong_issuer": (
            [f"Bearer {keys.mint('p1', audience, iss='https://evil.example.test')}"],
            "issuer_invalid",
        ),
        "unknown_kid": ([f"Bearer {keys.mint('p1', audience, kid='kid-unknown')}"], "kid_unknown"),
        "alg_none_empty": (
            ["Bearer " + keys.forge({"alg": "none", "kid": "kid-rsa"}, text, signature=b"")],
            "token_malformed",
        ),
        "alg_none_signed": (
            ["Bearer " + keys.forge({"alg": "none", "kid": "kid-rsa"}, text, signature=b"x" * 8)],
            "alg_not_allowed",
        ),
        "hs256_public_key": (
            [f"Bearer {keys.hs256_with_public_key('p1', audience)}"],
            "alg_not_allowed",
        ),
        "missing_sub": ([f"Bearer {keys.mint('p1', audience, sub=_MISSING)}"], "claim_missing"),
        "untrusted_key_known_kid": (
            [
                "Bearer "
                + keys.forge({"alg": "RS256", "kid": "kid-rsa"}, text, sign_with="stranger")
            ],
            "signature_invalid",
        ),
    }
    return cases


from test_s40_support import MISSING as _MISSING  # noqa: E402

KINDS = {
    "initialize": (
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    ),
    "tools/list": ("tools/list", None),
    "tools/call": ("tools/call", {"name": "graph_query", "arguments": PAGE_ARGS}),
    "resources/read": (
        "resources/read",
        {"uri": "atlas-source://" + "0" * 64 + "?token=x&offset=0&max_bytes=10"},
    ),
}


async def test_invalid_credentials(tmp_path, labs, monkeypatch):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    observer = Observer().install(monkeypatch)
    audience = f"{BASE}/a"
    cases = credential_cases(world.keys, audience)
    assert len(cases) >= 15
    async with world.serve() as http:
        verifier = world.router.verifier
        mark = observer.mark()
        for case, (authorization, reason, *http_reason) in cases.items():
            wire_reason = http_reason[0] if http_reason else reason
            for kind, (method, params) in KINDS.items():
                message = {"jsonrpc": "2.0", "id": 1, "method": method}
                if params is not None:
                    message["params"] = params
                before = verifier.counts.get(wire_reason, 0)
                response = await http.post(
                    "a",
                    None,
                    message,
                    authorization=authorization or [],
                    **withheld(message),
                )
                assert response.status == 401, (case, kind, response.status)
                assert response.header("www-authenticate") == expected_challenge(
                    wire_reason == "header_missing"
                ), (case, kind)
                body = response.json()["error"]
                assert body["data"]["reason"] == (
                    "credentials_missing"
                    if wire_reason == "header_missing"
                    else "credentials_invalid"
                )
                assert verifier.counts.get(wire_reason, 0) == before + 1, (case, kind, wire_reason)
                status, _headers, _body, receives = await router_call(
                    world, "a", None, authorization=authorization or []
                )
                assert status == 401 and receives == 0, (case, kind, receives)
        assert observer.since(mark, "lease") == 0
        assert observer.since(mark, "graph_init") == 0
        assert observer.since(mark, "tool:graph_query") == 0
        assert observer.since(mark, "retrieve") == 0
        # Control: a valid token is admitted (receive >= 1), served, and takes a lease.
        token = http.token("p1", "a")
        status, _headers, _body, receives = await router_call(world, "a", token)
        assert receives >= 1, (status, receives)
        mark = observer.mark()
        listed = await http.rpc("a", "p1", "tools/list")
        assert listed.status == 200 and len(listed.rpc(1)["result"]["tools"]) == 7
        page = tool_payload(await http.graph("a", "p1", PAGE_ARGS))
        assert page["status"] == "ok"
        assert observer.since(mark, "lease", labs.a.repo) >= 1
        assert world.registry("a").usage()["active"] == 1
