"""ACI-S40 policy and configuration: schema tables, digests and epochs, the file reload, decide,
the scoped registry's fail-closed behaviour, the limiter and the shared enforcement."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from test_s40_support import (
    ALL_TOOLS,
    BASE,
    ISSUER,
    World,
    get_labs,
    grant,
    policy_document,
    write_private,
)

from atlas_aci.enforcement import READ_ONLY_TOOLS, ToolError
from atlas_aci.protocol.http import LoopbackError
from atlas_aci.protocol.results import FIXED_MESSAGES
from atlas_aci.query import authz
from atlas_aci.query.authz import (
    AUTHZ_CONTEXT,
    AccessRequest,
    AuthzContext,
    Credential,
    FilePolicySource,
    MemoryPolicySource,
    PolicyError,
    PolicyStore,
    Principal,
    PrincipalLimiter,
    Registration,
    classify,
    decide,
    deny_all,
    parse_policy,
    scope_identity,
)
from atlas_aci.query.shared_http import (
    SHARED_CONFIG_REASONS,
    SharedConfigError,
)

REGISTRATIONS = {
    "a": Registration("a", "/srv/a", "/g/a", None),
    "b": Registration("b", "/srv/b", "/g/b", None),
}


@pytest.fixture(scope="module")
def labs(tmp_path_factory):
    return get_labs(tmp_path_factory.mktemp("s40labs"))


def snapshot(grants, regs=None):
    document = json.dumps(policy_document(grants)).encode()
    return parse_policy(document, issuer=ISSUER, registrations=regs or REGISTRATIONS)


def cred(subject="p1", *scopes):
    return Credential(Principal(ISSUER, subject), frozenset(scopes))


# ---------------------------------------------------------------------------- policy schema table
def doc(**changes):
    base = {"format": "atlas-aci-shared-policy.v1", "grants": [grant("p1", "a")]}
    base.update(changes)
    return base


def grants_with(**changes):
    entry = grant("p1", "a")
    entry.update(changes)
    return doc(grants=[entry])


def without(key):
    entry = grant("p1", "a")
    del entry[key]
    return doc(grants=[entry])


def compact_grants(count):
    rows = [
        {"issuer": ISSUER, "subject": f"s{i}", "repository": "a", "tools": ["list_dir"]}
        for i in range(count)
    ]
    return json.dumps(doc(grants=rows), separators=(",", ":")).encode()


POLICY_TABLE = [
    ("not_json", b"{nope", ""),
    ("not_json", b'{"format":"x","format":"y","grants":[]}', ""),
    ("not_json", b'{"format":"atlas-aci-shared-policy.v1","grants":[],"x":NaN}', ""),
    ("not_json", b'{"format":"atlas-aci-shared-policy.v1","grants":[],"x":1e400}', ""),
    ("not_json", b'{"format":"atlas-aci-shared-policy.v1","grants":[],"x":Infinity}', ""),
    ("too_large", b" " * (1024 * 1024 + 1), ""),
    ("wrong_type", [], ""),
    ("unknown_field", doc(extra=1), "/extra"),
    ("missing_field", {"format": "atlas-aci-shared-policy.v1"}, "/grants"),
    ("bad_format", doc(format="atlas-aci-shared-policy.v2"), "/format"),
    ("wrong_type", doc(grants={}), "/grants"),
    ("too_many_grants", compact_grants(10001), "/grants"),
    ("wrong_type", doc(grants=[1]), "/grants/0"),
    ("unknown_field", grants_with(extra=1), "/grants/0/extra"),
    ("missing_field", without("tools"), "/grants/0/tools"),
    ("missing_field", without("subject"), "/grants/0/subject"),
    ("issuer_unknown", grants_with(issuer="https://other.example.test"), "/grants/0/issuer"),
    ("wrong_type", grants_with(subject=5), "/grants/0/subject"),
    ("bad_format", grants_with(subject=""), "/grants/0/subject"),
    ("bad_format", grants_with(subject="a\nb"), "/grants/0/subject"),
    ("bad_format", grants_with(subject="a" + chr(0xA0) + "b"), "/grants/0/subject"),
    ("bad_format", grants_with(subject="x" * 256), "/grants/0/subject"),
    ("repository_unknown", grants_with(repository="zzz"), "/grants/0/repository"),
    ("wrong_type", grants_with(tools=[]), "/grants/0/tools"),
    ("wrong_type", grants_with(tools="view_file"), "/grants/0/tools"),
    ("wrong_type", grants_with(tools=[1]), "/grants/0/tools"),
    ("duplicate_grant", grants_with(tools=["view_file", "view_file"]), "/grants/0/tools"),
    ("tool_not_shareable", grants_with(tools=["test_dry_run"]), "/grants/0/tools"),
    ("tool_not_shareable", grants_with(tools=["memex_read"]), "/grants/0/tools"),
    ("tool_not_shareable", grants_with(tools=["no_such_tool"]), "/grants/0/tools"),
    ("bad_format", grants_with(required_scope="has space"), "/grants/0/required_scope"),
    ("bad_format", grants_with(required_scope=""), "/grants/0/required_scope"),
    ("bad_format", grants_with(required_scope=5), "/grants/0/required_scope"),
    (
        "duplicate_grant",
        doc(grants=[grant("p1", "a"), grant("p1", "a", ["list_dir"])]),
        "/grants/1",
    ),
]


@pytest.mark.parametrize(("reason", "payload", "where"), POLICY_TABLE)
def test_policy_schema_table(reason, payload, where):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    with pytest.raises(PolicyError) as caught:
        parse_policy(raw, issuer=ISSUER, registrations=REGISTRATIONS)
    assert caught.value.reason == reason and caught.value.field == where
    assert reason in authz.POLICY_REASONS
    text = str(caught.value)
    assert text == f"invalid shared policy ({reason} at {where or '/'})"


def test_every_policy_reason_is_exercised_and_the_empty_policy_is_deny_all():
    seen = {row[0] for row in POLICY_TABLE} | {"unreadable", "unsafe_file"}
    assert seen == set(authz.POLICY_REASONS)
    assert len(set(authz.POLICY_REASONS)) == len(authz.POLICY_REASONS)
    empty = snapshot([])
    assert empty.valid and not empty.grants
    assert not decide(cred(), empty, "a", AccessRequest("route")).allowed
    assert authz.SHAREABLE_TOOLS == READ_ONLY_TOOLS - authz.NEVER_SHARED
    assert {"test_dry_run", "memex_read"} == authz.NEVER_SHARED


def test_policy_digest_is_canonical_and_ignores_order():
    one = snapshot([grant("p1", "a", ["view_file", "list_dir"]), grant("p2", "b")])
    two = snapshot([grant("p2", "b"), grant("p1", "a", ["list_dir", "view_file"])])
    assert one.digest == two.digest
    assert snapshot([grant("p1", "a", ["view_file"]), grant("p2", "b")]).digest != one.digest
    assert (
        snapshot([grant("p1", "a", ["view_file", "list_dir"], scope="x"), grant("p2", "b")]).digest
        != one.digest
    )


# ---------------------------------------------------------------------------- epochs and reload
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def memory_store(source, interval=5.0, clock=None):
    return PolicyStore(
        source, REGISTRATIONS, issuer=ISSUER, interval_s=interval, clock=clock or Clock()
    )


def test_epoch_advances_only_when_the_effective_digest_changes():
    source = MemoryPolicySource(policy_document([grant("p1", "a")]))
    clock = Clock()
    store = memory_store(source, interval=0, clock=clock)
    assert store.current().epoch == 1 and store.current().valid
    assert store.refresh() is False and store.current().epoch == 1  # same bytes
    source.set(policy_document([grant("p1", "a")]))  # same content, rewritten
    assert store.refresh() is False and store.current().epoch == 1
    source.set(policy_document([grant("p1", "a"), grant("p2", "b")]))  # changed
    assert store.refresh() is True and store.current().epoch == 2
    source.set(None)  # unreadable -> deny-all
    assert store.refresh() is True
    assert store.current().epoch == 3 and not store.current().valid
    assert store.current().digest == authz.INVALID_DIGEST
    assert store.last_error is not None and store.last_error.reason == "unreadable"
    source.set(b"{broken")  # still invalid: no further advance
    assert store.refresh() is False and store.current().epoch == 3
    source.set(policy_document([grant("p1", "a"), grant("p2", "b")]))  # back to a valid digest
    assert store.refresh() is True and store.current().epoch == 4 and store.current().valid
    assert store.last_error is None


def test_an_invalid_initial_policy_is_deny_all_at_epoch_one():
    store = memory_store(MemoryPolicySource(b"garbage"))
    assert store.current().epoch == 1 and not store.current().valid
    assert (
        decide(cred(), store.current(), "a", AccessRequest("route")).reason == "policy_unavailable"
    )


def test_the_reload_is_throttled_by_the_interval_and_current_does_no_io():
    clock = Clock()
    source = MemoryPolicySource(policy_document([grant("p1", "a")]))
    store = memory_store(source, interval=5.0, clock=clock)
    reads = source.reads
    for _ in range(50):
        store.current()
        store.refresh()
    assert source.reads == reads  # nothing before the interval elapsed
    source.set(policy_document([]))
    clock.now += 4.9
    assert store.refresh() is False and store.current().epoch == 1
    clock.now += 0.2
    assert store.refresh() is True and store.current().epoch == 2
    assert source.reads == reads + 1


def file_store(path, interval=0.0):
    return PolicyStore(FilePolicySource(path), REGISTRATIONS, issuer=ISSUER, interval_s=interval)


def test_same_size_same_mtime_rewrites_are_seen_through_the_content(tmp_path):
    directory = tmp_path / "etc"
    directory.mkdir(mode=0o700)
    path = directory / "policy.json"
    write_private(path, json.dumps(policy_document([grant("alice", "a")])))
    stamp = os.stat(path)
    store = file_store(path)
    assert decide(cred("alice"), store.current(), "a", AccessRequest("route")).allowed
    # (1) atomic replace: new inode, identical size, mtime restored to the old value.
    write_private(path, json.dumps(policy_document([grant("alicf", "a")])))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert os.stat(path).st_size == stamp.st_size and os.stat(path).st_mtime_ns == stamp.st_mtime_ns
    assert store.refresh() is True
    assert not decide(cred("alice"), store.current(), "a", AccessRequest("route")).allowed
    assert decide(cred("alicf"), store.current(), "a", AccessRequest("route")).allowed
    # (2) in-place rewrite: same inode, same size, same mtime.
    before = os.stat(path)
    with open(path, "r+b") as handle:
        data = handle.read().replace(b"alicf", b"alicg")
        handle.seek(0)
        handle.write(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(path)
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    assert store.refresh() is True
    assert decide(cred("alicg"), store.current(), "a", AccessRequest("route")).allowed
    assert not decide(cred("alicf"), store.current(), "a", AccessRequest("route")).allowed


def test_non_regular_symlinked_and_unsafe_policy_files_mean_deny_all(tmp_path):
    directory = tmp_path / "etc"
    directory.mkdir(mode=0o700)
    path = directory / "policy.json"
    good = json.dumps(policy_document([grant("p1", "a")]))
    write_private(path, good)
    store = file_store(path)
    assert store.current().valid
    # A symlink swap (even to the very same content).
    target = directory / "real.json"
    write_private(target, good)
    path.unlink()
    path.symlink_to(target)
    assert store.refresh() is True and not store.current().valid
    assert store.last_error is not None and store.last_error.reason == "unreadable"
    # Restored: valid again, and the epoch moved on twice.
    path.unlink()
    write_private(path, good)
    assert store.refresh() is True and store.current().valid and store.current().epoch == 3
    # A FIFO and a directory are not regular files.
    path.unlink()
    os.mkfifo(path)
    assert store.refresh() is True and not store.current().valid
    path.unlink()
    path.mkdir()
    assert store.refresh() is False and not store.current().valid
    path.rmdir()
    # Group- or world-writable file; world-writable parent directory.
    write_private(path, good, mode=0o666)
    store.refresh()
    assert not store.current().valid and store.last_error.reason == "unsafe_file"
    write_private(path, good)
    assert store.refresh() is True and store.current().valid
    os.chmod(directory, 0o777)
    assert store.refresh() is True and store.last_error.reason == "unsafe_file"
    os.chmod(directory, 0o700)
    assert store.refresh() is True and store.current().valid
    # Too large.
    write_private(path, b" " * (1024 * 1024 + 1))
    assert store.refresh() is True and store.last_error.reason == "too_large"


def test_a_read_failure_inside_the_store_is_contained(monkeypatch):
    class Exploding:
        def read(self):
            raise RuntimeError("secret path /etc/shadow")

    store = PolicyStore(Exploding(), REGISTRATIONS, issuer=ISSUER, interval_s=0)
    assert not store.current().valid and store.last_error.reason == "unreadable"
    assert "shadow" not in str(store.last_error)


# ---------------------------------------------------------------------------- decide
RULES = [
    # (policy grants, credential, repository, request, expected reason)
    ([grant("p1", "a")], cred(), "a", AccessRequest("route"), "allowed"),
    ([], cred(), "zzz", AccessRequest("route"), "repository_unknown"),
    ([grant("p1", "a")], cred(), "b", AccessRequest("route"), "repository_not_granted"),
    ([grant("p1", "a")], cred("p9"), "a", AccessRequest("route"), "repository_not_granted"),
    (
        [grant("p1", "a", scope="atlas.read")],
        cred(),
        "a",
        AccessRequest("route"),
        "scope_not_presented",
    ),
    (
        [grant("p1", "a", scope="atlas.read")],
        cred("p1", "atlas.read"),
        "a",
        AccessRequest("route"),
        "allowed",
    ),
    ([grant("p1", "a", ["list_dir"])], cred(), "a", AccessRequest("tools/list"), "allowed"),
    ([grant("p1", "a", ["list_dir"])], cred(), "a", AccessRequest("resources/list"), "allowed"),
    (
        [grant("p1", "a", ["list_dir"])],
        cred(),
        "a",
        AccessRequest("resources/templates/list"),
        "allowed",
    ),
    (
        [grant("p1", "a", ["list_dir"])],
        cred(),
        "a",
        AccessRequest("resources/read"),
        "tool_not_granted",
    ),
    ([grant("p1", "a", ["graph_query"])], cred(), "a", AccessRequest("resources/read"), "allowed"),
    ([grant("p1", "a")], cred(), "a", AccessRequest("tools/call", "test_dry_run"), "tool_disabled"),
    ([grant("p1", "a")], cred(), "a", AccessRequest("tools/call", "memex_read"), "tool_disabled"),
    ([grant("p1", "a")], cred(), "a", AccessRequest("tools/call", "bogus"), "tool_unknown"),
    ([grant("p1", "a")], cred(), "a", AccessRequest("tools/call", ""), "tool_unknown"),
    (
        [grant("p1", "a", ["list_dir"])],
        cred(),
        "a",
        AccessRequest("tools/call", "view_file"),
        "tool_not_granted",
    ),
    ([grant("p1", "a")], cred(), "a", AccessRequest("tools/call", "view_file"), "allowed"),
    (
        [grant("p1", "a")],
        cred(),
        "a",
        AccessRequest("tools/call", "search_text", None, "absent", "rejected"),
        "argument_rejected",
    ),
    (
        [grant("p1", "a")],
        cred(),
        "a",
        AccessRequest("tools/call", "graph_query", "retrieve", "other"),
        "repository_argument_mismatch",
    ),
    (
        [grant("p1", "a")],
        cred(),
        "a",
        AccessRequest("tools/call", "graph_query", "retrieve", "route"),
        "allowed",
    ),
    ([grant("p1", "a")], cred(), "a", AccessRequest("nonsense"), "tool_unknown"),
]


@pytest.mark.parametrize(("grants", "credential", "repository", "access", "reason"), RULES)
def test_decide_rule_order_table(grants, credential, repository, access, reason):
    decision = decide(credential, snapshot(grants), repository, access)
    assert decision.reason == reason and decision.allowed == (reason == "allowed")
    assert (decision.scope_identity is not None) == decision.allowed
    assert decision.reason in {*authz.DENIAL_REASONS, "allowed"}


def test_an_earlier_rule_wins_over_a_later_one():
    # Unknown repository beats everything; a missing grant beats a missing scope and a bad tool.
    policy = snapshot([grant("p1", "a", ["list_dir"], scope="atlas.read")])
    request = AccessRequest("tools/call", "test_dry_run")
    assert decide(cred(), policy, "zzz", request).reason == "repository_unknown"
    assert decide(cred("p9"), policy, "a", request).reason == "repository_not_granted"
    assert decide(cred(), policy, "a", request).reason == "scope_not_presented"
    assert decide(cred("p1", "atlas.read"), policy, "a", request).reason == "tool_disabled"
    invalid = deny_all(REGISTRATIONS)
    assert decide(cred(), invalid, "zzz", request).reason == "policy_unavailable"


def test_impact_requires_a_graph_query_grant_on_every_hop():
    regs = {
        "a": Registration("a", "/srv/x", "/g/a", "c"),
        "c": Registration("c", "/srv/x", "/g/c", None),
    }
    impact = AccessRequest("tools/call", "graph_query", "impact")
    only_a = snapshot([grant("p1", "a")], regs)
    assert decide(cred(), only_a, "a", impact).reason == "cross_repository_not_granted"
    no_tool = snapshot([grant("p1", "a"), grant("p1", "c", ["list_dir"])], regs)
    assert decide(cred(), no_tool, "a", impact).reason == "cross_repository_not_granted"
    both = snapshot([grant("p1", "a"), grant("p1", "c")], regs)
    assert decide(cred(), both, "a", impact).allowed
    # A different operation is unaffected by the before repository.
    assert decide(cred(), only_a, "a", AccessRequest("tools/call", "graph_query", "status")).allowed


def test_scope_identity_binds_principal_repository_grant_and_epoch():
    p = Principal(ISSUER, "p1")
    base = scope_identity(p, "a", "g" * 64, 1)
    assert base == scope_identity(p, "a", "g" * 64, 1)
    for changed in (
        scope_identity(Principal(ISSUER, "p2"), "a", "g" * 64, 1),
        scope_identity(Principal("https://x.example.test", "p1"), "a", "g" * 64, 1),
        scope_identity(p, "b", "g" * 64, 1),
        scope_identity(p, "a", "h" * 64, 1),
        scope_identity(p, "a", "g" * 64, 2),
    ):
        assert changed != base


def test_classify_is_total_and_never_raises():
    odd = [None, 5, [], "x", {"op": 5}, {"pattern": 5}, {"op": ["retrieve"]}]
    for kind in ("tools/call", "tools/list", "resources/read", "zzz", 5, None):
        for name in ("graph_query", "search_text", None, 5, ""):
            for arguments in odd:
                request = classify(kind, name, arguments, "/srv/a")  # type: ignore[arg-type]
                assert isinstance(request, AccessRequest)
    assert classify("tools/call", "search_text", {"pattern": "-f/etc"}, "/r").argument == "rejected"
    assert classify("tools/call", "search_text", {"pattern": 5}, "/r").argument == "rejected"
    assert classify("tools/call", "search_text", {}, "/r").argument == "rejected"
    assert classify("tools/call", "search_text", {"pattern": "ok"}, "/r").argument == "ok"
    retrieve = {"op": "retrieve", "text": "x", "repository": "/other"}
    assert classify("tools/call", "graph_query", retrieve, "/r").repository_argument == "other"
    retrieve["repository"] = "/r"
    assert classify("tools/call", "graph_query", retrieve, "/r").repository_argument == "route"
    nested = {"op": "page", "query": {"op": "retrieve", "repository": "/x"}}
    assert classify("tools/call", "graph_query", nested, "/r").repository_argument == "absent"
    assert classify("tools/call", "graph_query", {"op": "bogus"}, "/r").op is None
    assert classify("zzz", None, None, "/r").kind == "zzz"


# ---------------------------------------------------------------------------- limiter, enforcement
def test_principal_limiter_window_and_independence():
    clock = Clock()
    limiter = PrincipalLimiter(2, clock=clock)
    one, two = Principal(ISSUER, "p1"), Principal(ISSUER, "p2")
    limiter.check(one)
    limiter.check(one)
    with pytest.raises(ToolError) as caught:
        limiter.check(one)
    assert (
        caught.value.code == "TIMEOUT"
        and caught.value.message == "Rate limit exceeded for this principal."
    )
    limiter.check(two)  # another principal is unaffected
    clock.now += 61
    limiter.check(one)  # the window slid
    PrincipalLimiter(0, clock=clock).check(one)  # 0 disables


def test_shared_enforcement_rechecks_the_principal_and_trims_records(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")], limits={"max_records": 3})
    world.build()
    app = world.router.apps["a"]
    enforcement = app.state[0]
    assert type(enforcement).__name__ == "SharedEnforcement"

    def forbidden(call):
        with pytest.raises(ToolError) as caught:
            call()
        assert caught.value.code == "FORBIDDEN"
        assert caught.value.message == FIXED_MESSAGES["FORBIDDEN"]

    forbidden(lambda: enforcement.assert_read_only("view_file"))  # no context
    forbidden(lambda: enforcement.assert_rate_limit())
    context = AuthzContext(cred(), "a", frozenset({"view_file"}), 1, "s" * 64)
    token = AUTHZ_CONTEXT.set(context)
    try:
        enforcement.assert_read_only("view_file")
        enforcement.assert_rate_limit()
        forbidden(lambda: enforcement.assert_read_only("list_dir"))  # not in the grant
        forbidden(lambda: enforcement.assert_read_only("test_dry_run"))
        forbidden(lambda: enforcement.assert_read_only("memex_read"))
        forbidden(lambda: enforcement.assert_read_only("nope"))
        for index in range(10):
            enforcement.record(tool="view_file", args={"i": index}, bytes_out=1, duration_ms=1)
        assert isinstance(enforcement.records, list) and len(enforcement.records) == 3
        assert [r.args["i"] for r in enforcement.records] == [7, 8, 9]
    finally:
        AUTHZ_CONTEXT.reset(token)
    token = AUTHZ_CONTEXT.set(AuthzContext(cred(), "b", frozenset(ALL_TOOLS), 1, "s" * 64))
    try:
        forbidden(lambda: enforcement.assert_read_only("view_file"))  # a foreign repository
    finally:
        AUTHZ_CONTEXT.reset(token)


async def test_scoped_registry_fails_closed_and_enforces_the_quota(tmp_path, labs):
    world = World(tmp_path, labs, grants=[grant("p1", "a")])
    world.build()
    registry = world.registry("a")
    assert registry.quota == 1

    def mismatch():
        return pytest.raises(ToolError, match="SCOPE_MISMATCH")

    with mismatch():
        registry.scope()  # no context
    token = AUTHZ_CONTEXT.set(AuthzContext(cred(), "b", frozenset(ALL_TOOLS), 1, "s" * 64))
    try:
        with mismatch():
            registry.scope()  # a context for another repository
        with mismatch():
            async with registry.pending():
                pass
    finally:
        AUTHZ_CONTEXT.reset(token)
    one = AuthzContext(cred("p1"), "a", frozenset(ALL_TOOLS), 1, "1" * 64)
    two = AuthzContext(cred("p2"), "a", frozenset(ALL_TOOLS), 1, "2" * 64)
    stale = AuthzContext(cred("p1"), "a", frozenset(ALL_TOOLS), 7, "3" * 64)
    token = AUTHZ_CONTEXT.set(stale)
    try:
        with mismatch():
            registry.scope()  # decided at another epoch
        with mismatch():
            async with registry.pending():  # the epoch fence also guards the reservation
                pass
    finally:
        AUTHZ_CONTEXT.reset(token)
    token = AUTHZ_CONTEXT.set(one)
    try:
        async with registry.pending():
            with pytest.raises(ToolError, match="LEASE_CAPACITY"):
                async with registry.pending():  # quota + 1 for the same principal
                    pass
            token_two = AUTHZ_CONTEXT.set(two)
            try:
                async with registry.pending():  # another principal is unaffected
                    assert registry._pending_by == {
                        one.credential.principal: 1,
                        two.credential.principal: 1,
                    }
            finally:
                AUTHZ_CONTEXT.reset(token_two)
        assert registry._pending_by == {}
        async with registry.pending():  # released: the quota is available again
            pass
    finally:
        AUTHZ_CONTEXT.reset(token)
    assert AUTHZ_CONTEXT.get() is None
    assert await registry.purge_revoked(world.router.store.current()) == 0
    await world.router.aclose_state()


# ---------------------------------------------------------------------------- shared-config table
def make_world(tmp_path, labs, **kwargs):
    return World(tmp_path, labs, grants=[grant("p1", "a")], **kwargs)


def edit(world, change):
    document = copy.deepcopy(world.document)
    change(document)
    write_private(world.config_path, json.dumps(document))
    return world


def set_path(*keys, value):
    def apply(document):
        target = document
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return apply


def drop(*keys):
    def apply(document):
        target = document
        for key in keys[:-1]:
            target = target[key]
        del target[keys[-1]]

    return apply


CONFIG_TABLE = [
    ("unknown_field", set_path("extra", value=1), "/extra"),
    ("unknown_field", set_path("listen", "bogus", value=1), "/listen/bogus"),
    ("unknown_field", set_path("repositories", 0, "bogus", value=1), "/repositories/0/bogus"),
    ("missing_field", drop("identity"), "/identity"),
    ("missing_field", drop("identity", "issuer"), "/identity/issuer"),
    ("missing_field", drop("repositories", 0, "memex_root"), "/repositories/0/memex_root"),
    ("wrong_type", set_path("listen", "port", value=True), "/listen/port"),
    ("wrong_type", set_path("listen", "port", value="8765"), "/listen/port"),
    ("wrong_type", set_path("repositories", value={}), "/repositories"),
    ("wrong_type", set_path("listen", "allowed_origins", value="x"), "/listen/allowed_origins"),
    ("out_of_range", set_path("listen", "port", value=80), "/listen/port"),
    ("out_of_range", set_path("listen", "port", value=70000), "/listen/port"),
    ("out_of_range", set_path("listen", "max_inflight", value=0), "/listen/max_inflight"),
    ("out_of_range", set_path("listen", "max_body_bytes", value=10), "/listen/max_body_bytes"),
    (
        "out_of_range",
        set_path("listen", "max_inflight_per_principal", value=9),
        "/listen/max_inflight_per_principal",
    ),
    ("out_of_range", set_path("identity", "leeway_seconds", value=61), "/identity/leeway_seconds"),
    (
        "out_of_range",
        set_path("identity", "max_token_bytes", value=100),
        "/identity/max_token_bytes",
    ),
    (
        "out_of_range",
        set_path("identity", "max_token_bytes", value=70000),
        "/identity/max_token_bytes",
    ),
    (
        "out_of_range",
        set_path("identity", "max_token_lifetime_seconds", value=5),
        "/identity/max_token_lifetime_seconds",
    ),
    (
        "out_of_range",
        set_path("policy", "reload_interval_seconds", value=61),
        "/policy/reload_interval_seconds",
    ),
    ("out_of_range", set_path("limits", "max_records", value=70000), "/limits/max_records"),
    ("out_of_range", set_path("limits", "max_graph_leases", value=65), "/limits/max_graph_leases"),
    (
        "out_of_range",
        set_path("limits", "max_leases_per_principal", value=0),
        "/limits/max_leases_per_principal",
    ),
    ("out_of_range", set_path("repositories", value=[]), "/repositories"),
    ("bad_format", set_path("format", value="atlas-aci-shared.v2"), "/format"),
    (
        "bad_format",
        set_path("identity", "issuer", value="http://idp.example.test"),
        "/identity/issuer",
    ),
    (
        "bad_format",
        set_path("identity", "issuer", value="https://idp.example.test/#x"),
        "/identity/issuer",
    ),
    (
        "bad_format",
        set_path("identity", "resource_base_url", value="http://a.example/mcp"),
        "/identity/resource_base_url",
    ),
    (
        "bad_format",
        set_path("identity", "resource_base_url", value="https://a.example/other"),
        "/identity/resource_base_url",
    ),
    (
        "bad_format",
        set_path("identity", "scopes_supported", value=["has space"]),
        "/identity/scopes_supported",
    ),
    ("bad_format", set_path("repositories", 0, "id", value="Bad_ID"), "/repositories/0/id"),
    ("bad_format", set_path("repositories", 0, "id", value="-x"), "/repositories/0/id"),
    ("duplicate_id", set_path("repositories", 1, "id", value="a"), "/repositories/1/id"),
    (
        "not_absolute",
        set_path("repositories", 0, "repo", value="relative/dir"),
        "/repositories/0/repo",
    ),
    (
        "not_absolute",
        set_path("repositories", 0, "graph_root", value="g"),
        "/repositories/0/graph_root",
    ),
    ("not_absolute", set_path("identity", "jwks_file", value="jwks.json"), "/identity/jwks_file"),
    ("not_absolute", set_path("policy", "file", value="policy.json"), "/policy/file"),
    (
        "not_directory",
        set_path("repositories", 0, "repo", value="/nonexistent-s40-dir"),
        "/repositories/0/repo",
    ),
    (
        "before_unknown",
        set_path("repositories", 0, "before_repo_id", value="zzz"),
        "/repositories/0/before_repo_id",
    ),
    (
        "before_self",
        set_path("repositories", 0, "before_repo_id", value="a"),
        "/repositories/0/before_repo_id",
    ),
    (
        "capacity_exceeded",
        set_path("limits", "max_leases_per_principal", value=3),
        "/limits/max_leases_per_principal",
    ),
    (
        "capacity_exceeded",
        set_path("limits", "max_total_lease_state_bytes", value=16 * 1024 * 1024),
        "/limits/max_total_lease_state_bytes",
    ),
]


@pytest.mark.parametrize(("reason", "change", "where"), CONFIG_TABLE)
def test_shared_config_table(tmp_path, labs, reason, change, where):
    world = make_world(tmp_path, labs)
    edit(world, change)
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert (caught.value.reason, caught.value.field) == (reason, where)
    assert str(caught.value) == f"invalid shared configuration ({reason} at {where})"


def test_the_valid_world_loads_with_the_documented_defaults(tmp_path, labs):
    world = make_world(tmp_path, labs)
    deployment = world.load()
    assert set(deployment.configs) == {"a", "b"} and deployment.host_ip == "127.0.0.1"
    listen, identity, limits = deployment.listen, deployment.identity, deployment.limits
    assert listen.max_inflight == 8 and listen.max_inflight_per_principal == 4
    assert (identity.leeway_seconds, identity.max_token_bytes) == (30, 8192)
    assert identity.max_token_lifetime_seconds == 86400
    assert limits.max_leases_per_principal == 1 and limits.max_graph_leases == 4
    assert limits.max_total_lease_state_bytes == 1024 * 1024 * 1024 and limits.max_records == 1024
    for config in deployment.configs.values():
        assert config.test_execution_enabled is False and config.max_graph_leases == 4
        assert config.max_total_lease_state_bytes == 64 * 1024 * 1024
        assert config.http_host == "127.0.0.1" and config.graph_root is not None


def test_capacity_formula_bounds_the_aggregate_lease_memory(tmp_path, labs):
    world = make_world(tmp_path, labs, limits={"max_total_lease_state_bytes": 64 * 1024 * 1024})
    deployment = world.load()
    for config in deployment.configs.values():
        assert config.max_total_lease_state_bytes == 32 * 1024 * 1024
    total = sum(c.max_total_lease_state_bytes for c in deployment.configs.values())
    assert total <= deployment.limits.max_total_lease_state_bytes


def test_config_documents_reject_duplicates_nan_and_unsafe_files(tmp_path, labs):
    world = make_world(tmp_path, labs)
    good = world.config_path.read_text()
    cases = {
        "not_json": [
            good.replace('"port": 0', '"port": 0, "port": 1'),
            good.replace('"port": 0', '"port": NaN'),
            good.replace('"port": 0', '"port": 1e400'),
            "{broken",
        ],
    }
    for text in cases["not_json"]:
        write_private(world.config_path, text)
        with pytest.raises(SharedConfigError) as caught:
            world.load()
        assert caught.value.reason == "not_json", text[:60]
    write_private(world.config_path, good, mode=0o666)
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert caught.value.reason == "unsafe_file"
    write_private(world.config_path, good)
    os.chmod(world.config_dir, 0o777)
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert caught.value.reason == "unsafe_file"
    os.chmod(world.config_dir, 0o700)
    write_private(world.config_path, b" " * (1024 * 1024 + 1))
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert caught.value.reason == "too_large"
    world.config_path.unlink()
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert caught.value.reason == "unreadable"
    write_private(world.config_path, good)
    assert world.load() is not None  # control


def test_jwks_and_policy_files_are_checked_at_startup(tmp_path, labs):
    world = make_world(tmp_path, labs)
    write_private(world.jwks_path, b'{"keys": []}')
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert (caught.value.reason, caught.value.field) == ("jwks_invalid", "/identity/jwks_file")
    write_private(world.jwks_path, world.keys.jwks_bytes(), mode=0o664)
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert caught.value.reason == "jwks_invalid"
    write_private(world.jwks_path, world.keys.jwks_bytes())
    world.write_policy(b"{nope")
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert (caught.value.reason, caught.value.field) == ("policy_invalid", "/policy/file")
    world.write_policy([grant("p1", "zzz")])
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert caught.value.reason == "policy_invalid"
    world.write_policy([grant("p1", "a")])
    assert world.load() is not None


def test_test_execution_and_non_loopback_hosts_refuse_startup(tmp_path, labs):
    world = make_world(tmp_path, labs)
    with pytest.raises(SharedConfigError) as caught:
        world.load({"ATLAS_ACI_ENABLE_TEST_EXECUTION": "1"})
    assert caught.value.reason == "test_execution_enabled"
    assert world.load({"ATLAS_ACI_ENABLE_TEST_EXECUTION": "0"}) is not None
    for host in ("0.0.0.0", "10.1.2.3", "::", ""):
        edit(world, set_path("listen", "host", value=host))
        with pytest.raises(LoopbackError):
            world.load()
    edit(world, set_path("listen", "host", value="::1"))
    assert world.load().host_ip == "::1"
    assert {"test_execution_enabled", "jwks_invalid", "policy_invalid"} <= set(
        SHARED_CONFIG_REASONS
    )
    assert len(set(SHARED_CONFIG_REASONS)) == len(SHARED_CONFIG_REASONS)


# ---------------------------------------------------------------------------- overlap rules
def dirs(tmp_path, *names):
    out = {}
    for name in names:
        path = tmp_path / "x" / name
        path.mkdir(parents=True, exist_ok=True)
        out[name] = path
    return out


def overlay(world, rows):
    """Replace the repositories list; `rows` are dicts with id/repo/graph_root/memex_root."""
    document = copy.deepcopy(world.document)
    document["repositories"] = rows
    write_private(world.config_path, json.dumps(document))


def row(identifier, repo, graph, memex, before=None):
    entry = {
        "id": identifier,
        "repo": str(repo),
        "graph_root": str(graph),
        "memex_root": str(memex),
    }
    if before:
        entry["before_repo_id"] = before
    return entry


def expect(world, reason, where=None):
    with pytest.raises(SharedConfigError) as caught:
        world.load()
    assert caught.value.reason == reason, (caught.value.reason, caught.value.field)
    if where is not None:
        assert caught.value.field == where


def test_overlapping_repositories_and_roots_are_refused(tmp_path, labs):
    world = make_world(tmp_path, labs)
    d = dirs(tmp_path, "r1", "r1/sub", "r2", "g1", "g2", "m1", "m2", "g1/inner", "m1/inner")
    ok = [row("a", d["r1"], d["g1"], d["m1"]), row("b", d["r2"], d["g2"], d["m2"])]
    world.write_policy([])
    overlay(world, ok)
    assert world.load() is not None  # control: disjoint trees pass
    # A repository nested in another (either order).
    overlay(world, [ok[0], row("b", d["r1/sub"], d["g2"], d["m2"])])
    expect(world, "repository_nested", "/repositories/1/repo")
    overlay(world, [row("a", d["r1/sub"], d["g1"], d["m1"]), row("b", d["r1"], d["g2"], d["m2"])])
    expect(world, "repository_nested")
    # The same path twice without a before link, or linked but with one graph root.
    overlay(world, [ok[0], row("b", d["r1"], d["g2"], d["m2"])])
    expect(world, "repository_nested", "/repositories/1/repo")
    overlay(world, [ok[0], row("b", d["r1"], d["g1"], d["m2"], None)])
    expect(world, "duplicate_namespace", "/repositories/1/graph_root")
    overlay(world, [row("a", d["r1"], d["g1"], d["m1"], "b"), row("b", d["r1"], d["g1"], d["m2"])])
    expect(world, "duplicate_namespace")
    # An explicit same-path pair with distinct graph roots is the one allowed overlap.
    overlay(world, [row("a", d["r1"], d["g1"], d["m1"], "b"), row("b", d["r1"], d["g2"], d["m2"])])
    assert world.load() is not None
    overlay(world, [row("a", d["r1"], d["g1"], d["m1"]), row("b", d["r1"], d["g2"], d["m2"], "a")])
    assert world.load() is not None
    # A graph root or memex root inside any repository (own or another).
    overlay(world, [row("a", d["r1"], d["r1/sub"], d["m1"]), ok[1]])
    expect(world, "graph_root_inside_repository", "/repositories/0/graph_root")
    overlay(world, [ok[0], row("b", d["r2"], d["r1/sub"], d["m2"])])
    expect(world, "graph_root_inside_repository", "/repositories/1/graph_root")
    overlay(world, [row("a", d["r1"], d["g1"], d["r1/sub"]), ok[1]])
    expect(world, "memex_inside_repository", "/repositories/0/memex_root")
    overlay(world, [ok[0], row("b", d["r2"], d["g2"], d["r1"])])
    expect(world, "memex_inside_repository", "/repositories/1/memex_root")
    # Roots nested in or equal to one another.
    overlay(world, [ok[0], row("b", d["r2"], d["g2"], d["m1"])])
    expect(world, "duplicate_memex", "/repositories/1/memex_root")
    overlay(world, [ok[0], row("b", d["r2"], d["g2"], d["m1/inner"])])
    expect(world, "root_nested", "/repositories/0/memex_root")
    overlay(world, [ok[0], row("b", d["r2"], d["g1/inner"], d["m2"])])
    expect(world, "root_nested")
    overlay(world, [row("a", d["r1"], d["g1"], d["g1"]), ok[1]])
    expect(world, "root_nested")
    # A repository inside a storage root.
    overlay(world, [row("a", d["r1"], d["g1"], d["m1"]), row("b", d["g1/inner"], d["g2"], d["m2"])])
    expect(world, "root_nested")
    # Two repositories may share one graph root (namespaces are per repository path).
    overlay(world, [ok[0], row("b", d["r2"], d["g1"], d["m2"])])
    assert world.load() is not None
    assert {c.graph_root for c in world.deployment.configs.values()} == {d["g1"].resolve()}


def test_the_documented_reasons_are_each_reachable():
    required = {
        "repository_nested",
        "graph_root_inside_repository",
        "root_nested",
        "memex_inside_repository",
        "duplicate_namespace",
        "duplicate_memex",
        "capacity_exceeded",
        "unsafe_file",
    }
    assert required <= set(SHARED_CONFIG_REASONS)
    assert BASE.startswith("https://")


def test_a_symlinked_repository_resolves_to_its_target_before_the_overlap_checks(tmp_path, labs):
    world = make_world(tmp_path, labs)
    d = dirs(tmp_path, "real", "g1", "g2", "m1", "m2")
    alias = tmp_path / "x" / "alias"
    alias.symlink_to(d["real"])
    world.write_policy([])
    overlay(world, [row("a", d["real"], d["g1"], d["m1"]), row("b", alias, d["g2"], d["m2"])])
    expect(world, "repository_nested", "/repositories/1/repo")


def test_policy_digest_helpers_are_pure(tmp_path):
    first = snapshot([grant("p1", "a")])
    again = snapshot([grant("p1", "a")])
    assert first == again and first.registry_digest == again.registry_digest
    other = snapshot(
        [grant("p1", "a")], {**REGISTRATIONS, "c": Registration("c", "/srv/c", "/g/c", None)}
    )
    assert other.registry_digest != first.registry_digest
    assert isinstance(tmp_path, Path)
