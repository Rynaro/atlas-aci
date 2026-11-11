"""ACI-S34 rejections: every closed reason is reachable, the CLI texts are fixed, no output."""

from __future__ import annotations

import json
import os

import pytest
from test_s34_support import (
    World,
    assert_rejected_cli,
    build_world,
    cli_run,
    drift_args,
    encode,
    map_args,
    sentinel_output,
    temp_leftovers,
)

from atlas_aci import __main__ as entry
from atlas_aci.codegraph import clear_authentication_memo
from atlas_aci.overlays import runtime
from atlas_aci.query import diff as symbol_diff
from atlas_aci.query.diff import Side
from atlas_aci.rationale import adr_mapping as am
from atlas_aci.rationale import drift
from atlas_aci.storage import snapshots


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return build_world(tmp_path_factory.mktemp("s34r"))


def _empty(world: World, name: str) -> Side:
    root = world.tmp / name
    root.mkdir(exist_ok=True)
    return Side(world.repo, root)


def _limits(**chosen):
    return am.normalize_limits(chosen)


def _doc(world: World, mutate=None, **top):
    document = world.document()
    document.update(top)
    if mutate is not None:
        mutate(document)
    return document


def _raises(reason, call):
    with pytest.raises(am.RationaleRejected) as info:
        call()
    assert info.value.reason == reason, info.value.reason


class Counter:
    """`runtime.open_active` that fails on the n-th call and delegates otherwise."""

    def __init__(self, fail_at: int, error: Exception):
        self.real, self.calls, self.fail_at, self.error = runtime.open_active, 0, fail_at, error

    def __call__(self, stack, side):
        self.calls += 1
        if self.calls == self.fail_at:
            raise self.error
        return self.real(stack, side)


def _lease_fails(mp, fail_at, reason):
    mp.setattr(runtime, "open_active", Counter(fail_at, runtime.OverlayRejected(reason)))


def _unreadable(mp):
    def refuse(self, path, source_hash=None):
        raise ValueError("source unavailable")

    mp.setattr(snapshots.SourceLease, "read", refuse)
    # The lease-time source reads that used to surface this failure run once per generation now
    # (issue #123); forget the remembered authentication so the patched read is reached.
    clear_authentication_memo()


def _tampered(mp, module, name, key):
    real = getattr(module, name)

    def wrapper(*args, **kwargs):
        value = real(*args, **kwargs)
        value = json.loads(json.dumps(value))
        value["counts"][key] += 1
        return value

    mp.setattr(module, name, wrapper)


# ---- the mapping parser -------------------------------------------------------------------------

LONG = "x" * 65


def _text(world, **fields):
    return encode({**world.document(), **fields})


def _entry_edit(world, path, value):
    document = world.document()
    node = document["mappings"][0]
    *parents, last = path
    for key in parents:
        node = node[key]
    node[last] = value
    return encode(document)


def _drop_entry_key(world, key):
    document = world.document()
    del document["mappings"][0][key]
    return encode(document)


def _bad_mappings(world: World) -> dict[str, bytes]:
    good = world.raw()
    text = good.decode()
    first = world.document()["mappings"][0]
    return {
        "bom": b"\xef\xbb\xbf" + good,
        "duplicate_key": b'{"format":"x","format":"y"}',
        "float": _entry_edit(world, ("span", "start_byte"), 1.5),
        "nan": text.replace('"start_byte": 42', '"start_byte": NaN').encode(),
        "huge_int": _entry_edit(world, ("span", "start_byte"), 10**25),
        "negative": _entry_edit(world, ("span", "start_byte"), -1),
        "bool": _entry_edit(world, ("span", "start_byte"), True),
        "too_large_int": _entry_edit(world, ("span", "end_byte"), 2147483648),
        "extra_top": _text(world, extra=1),
        "extra_entry": _entry_edit(world, ("extra",), 1),
        "extra_nested": _entry_edit(world, ("span", "extra"), 1),
        "missing_key": _drop_entry_key(world, "decision_id"),
        "missing_top": encode({"format": "atlas-rationale-mapping.v1", "mappings": []}),
        "bad_label": _entry_edit(world, ("mapping_id",), "-bad"),
        "long_label": _entry_edit(world, ("decision_id",), LONG),
        "empty_label": _entry_edit(world, ("mapping_id",), ""),
        "space_label": _entry_edit(world, ("mapping_id",), "a b"),
        "upper_hex": _entry_edit(world, ("document", "sha256"), "A" * 64),
        "short_hex": _entry_edit(world, ("span", "sha256"), "ab"),
        "bad_snapshot": _text(world, snapshot_id="xyz"),
        "absolute_path": _entry_edit(world, ("document", "path"), "/etc/passwd"),
        "dotdot_path": _entry_edit(world, ("target", "path"), "a/../b"),
        "backslash_path": _entry_edit(world, ("target", "path"), "a\\b"),
        "drive_path": _entry_edit(world, ("document", "path"), "C:/x"),
        "long_path": _entry_edit(world, ("document", "path"), "a/" * 2100 + "b"),
        "bad_kind": _entry_edit(world, ("target", "kind"), "a b"),
        "empty_name": _entry_edit(world, ("target", "name"), ""),
        "long_name": _entry_edit(world, ("target", "name"), "n" * 1025),
        "non_string_name": _entry_edit(world, ("target", "name"), 7),
        "empty_mappings": _text(world, mappings=[]),
        "mappings_not_list": _text(world, mappings={}),
        "duplicate_mapping_id": encode({**world.document(), "mappings": [first, first]}),
        "top_list": b"[]",
        "not_json": b"{",
        "not_utf8": b"\xff\xfe",
        "deep": b"[" * 100000,
        "surrogate_name": text.replace('"name": "charge"', '"name": "\\ud800"', 1).encode(),
        "surrogate_format": text.replace(
            '"format": "atlas-rationale-mapping.v1"', '"format": "\\ud800"'
        ).encode(),
        "surrogate_key": text.replace('"decision_id"', '"\\ud800"', 1).encode(),
    }


def test_every_malformed_mapping_is_invalid(world):
    cases = _bad_mappings(world)
    assert len(cases) >= 39
    for name, raw in cases.items():
        _raises("invalid_mapping", lambda raw=raw: am.parse_mapping(raw))
        assert name


def test_a_lone_surrogate_never_reaches_a_crash(world, tmp_path):
    raw = _bad_mappings(world)["surrogate_name"]
    assert b"\\ud800" in raw
    path = world.mapping_file("surrogate.json", raw)
    out, sentinel = sentinel_output(tmp_path)
    result = cli_run(*map_args(world, path, out))
    assert_rejected_cli(result, "Rationale mapping rejected: invalid_mapping", out, sentinel)


def test_another_format_version_is_unsupported(world):
    for fmt in ("atlas-rationale-mapping.v2", "x", ""):
        _raises(
            "unsupported_mapping_version",
            lambda fmt=fmt: am.parse_mapping(_text(world, format=fmt)),
        )
    _raises(
        "unsupported_mapping_version",
        lambda: am.parse_mapping(b'{"format": "atlas-rationale-mapping.v2"}'),
    )
    _raises("invalid_mapping", lambda: am.parse_mapping(b'{"format": 2}'))


def test_the_limits_are_enforced(world):
    raw = world.raw()
    _raises("mapping_too_large", lambda: am.parse_mapping(raw, _limits(max_mapping_bytes=1)))
    _raises("too_many_mappings", lambda: am.parse_mapping(raw, _limits(max_mappings=1)))
    for name, (default, low, high) in am.LIMITS.items():
        assert am.normalize_limits({name: default}) is not None
        for bad in (0, low - 1, high + 1, True, False, "5", 1.5):
            with pytest.raises(am.RationaleUsageError) as info:
                am.normalize_limits({name: bad})
            assert info.value.what == name
        assert getattr(am.normalize_limits({name: low}), name) == low
        assert getattr(am.normalize_limits({name: high}), name) == high
    with pytest.raises(am.RationaleUsageError):
        am.normalize_limits({"nope": 1})
    assert am.normalize_limits(None) == am.normalize_limits({})


# ---- every closed rejection of each command is reachable ----------------------------------


def test_every_mapping_rejection_is_reachable(world):
    raw, before = world.raw(), world.before
    mismatch = world.raw(snapshot_id="f" * 64)
    cases = {
        "mapping_too_large": lambda mp: am.map_rationale(raw, before, _limits(max_mapping_bytes=1)),
        "too_many_mappings": lambda mp: am.map_rationale(raw, before, _limits(max_mappings=1)),
        "invalid_mapping": lambda mp: am.map_rationale(b"{", before),
        "unsupported_mapping_version": lambda mp: am.map_rationale(b'{"format": "x"}', before),
        "index_unavailable": lambda mp: am.map_rationale(raw, _empty(world, "no-index")),
        "source_snapshot_unavailable": lambda mp: (_unreadable(mp), am.map_rationale(raw, before)),
        "index_upgrade_required": lambda mp: (
            mp.setattr(runtime, "SCHEMA_EPOCHS", (99,)),
            am.map_rationale(raw, before),
        ),
        "mapping_snapshot_mismatch": lambda mp: am.map_rationale(mismatch, before),
        "output_too_large": lambda mp: am.map_rationale(raw, before, _limits(max_output_bytes=1)),
        "invalid_overlay": lambda mp: (
            _tampered(mp, am, "build_overlay", "resolved"),
            am.map_rationale(raw, before),
        ),
    }
    assert set(cases) == set(am.REJECTIONS) and len(am.REJECTIONS) == 10
    for reason, call in cases.items():
        with pytest.MonkeyPatch.context() as mp:
            _raises(reason, lambda call=call, mp=mp: call(mp))
    # The lease reason an open_active refusal carries passes through unchanged.
    for reason in am.LEASE_REASONS:
        with pytest.MonkeyPatch.context() as mp:
            _lease_fails(mp, 1, reason)
            _raises(reason, lambda: am.map_rationale(raw, before))
    with pytest.MonkeyPatch.context() as mp:
        _lease_fails(mp, 1, "something_else")
        _raises("index_unavailable", lambda: am.map_rationale(raw, before))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(symbol_diff, "_open", _usage_refusal)
        with pytest.raises(am.RationaleUsageError) as info:
            am.map_rationale(raw, before)
        assert info.value.what == "graph_namespace"


def _usage_refusal(stack, side):
    raise symbol_diff.DiffUsageError("namespace")


def _compare_spy(mp, tamper=None, error=None):
    real = symbol_diff.compare

    def spy(before, after, **kwargs):
        if error is not None:
            raise error
        change_set = real(before, after, **kwargs)
        return tamper(change_set) if tamper else change_set

    mp.setattr(symbol_diff, "compare", spy)


def _tamper_after(change_set):
    return {**change_set, "after": {**change_set["after"], "generation_id": "0" * 64}}


def _bad_assembly(mp):
    real = drift._assemble

    def wrapper(*args, **kwargs):
        document = json.loads(json.dumps(real(*args, **kwargs)))
        document["counts"]["no_finding"] += 1
        return document

    mp.setattr(drift, "_assemble", wrapper)


def test_every_drift_rejection_is_reachable(world):
    raw, before, after = world.raw(), world.before, world.after
    run = drift.rationale_drift
    empty = _empty(world, "no-index-drift")
    cases = {
        "mapping_too_large": lambda mp: run(raw, before, after, _limits(max_mapping_bytes=1)),
        "too_many_mappings": lambda mp: run(raw, before, after, _limits(max_mappings=1)),
        "invalid_mapping": lambda mp: run(b"{", before, after),
        "unsupported_mapping_version": lambda mp: run(b'{"format": "x"}', before, after),
        "before_index_unavailable": lambda mp: run(raw, empty, after),
        "before_source_snapshot_unavailable": lambda mp: (
            _unreadable(mp),
            run(raw, before, after),
        ),
        "before_index_upgrade_required": lambda mp: (
            _lease_fails(mp, 1, "index_upgrade_required"),
            run(raw, before, after),
        ),
        "after_index_unavailable": lambda mp: run(raw, before, empty),
        "after_source_snapshot_unavailable": lambda mp: (
            _lease_fails(mp, 2, "source_snapshot_unavailable"),
            run(raw, before, after),
        ),
        "after_index_upgrade_required": lambda mp: (
            _lease_fails(mp, 2, "index_upgrade_required"),
            run(raw, before, after),
        ),
        "mapping_snapshot_mismatch": lambda mp: run(raw, after, before),
        "snapshot_changed": lambda mp: (
            _compare_spy(mp, _tamper_after),
            run(raw, before, after),
        ),
        "comparison_failed": lambda mp: (
            _compare_spy(mp, error=RuntimeError("boom")),
            run(raw, before, after),
        ),
        "output_too_large": lambda mp: run(raw, before, after, _limits(max_output_bytes=1)),
        "invalid_overlay": lambda mp: (
            _tampered(mp, am, "build_overlay", "resolved"),
            run(raw, before, after),
        ),
        "invalid_drift": lambda mp: (_bad_assembly(mp), run(raw, before, after)),
    }
    assert set(cases) == set(drift.DRIFT_REJECTIONS)
    assert len(drift.DRIFT_REJECTIONS) == 16
    assert "source_snapshot_unavailable" not in drift.DRIFT_REJECTIONS
    assert len(set(drift.DRIFT_REJECTIONS)) == 16
    for reason, call in cases.items():
        with pytest.MonkeyPatch.context() as mp:
            _raises(reason, lambda call=call, mp=mp: call(mp))
    with pytest.MonkeyPatch.context() as mp:
        _compare_spy(mp, error=symbol_diff.DiffUsageError("both sides"))
        with pytest.raises(am.RationaleUsageError) as info:
            run(raw, before, after)
        assert info.value.what == "graph_namespace"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(symbol_diff, "_open", _usage_refusal)
        with pytest.raises(am.RationaleUsageError) as info:
            run(raw, before, after)
        assert info.value.what == "graph_namespace"
    with pytest.raises(am.RationaleUsageError) as info:
        run(raw, before, Side(world.repo, before.graph_root))
    assert info.value.what == "same_namespace"


# ---- the CLI: fixed text, empty stdout, nothing written ----------------------------------


def test_map_rejections_are_fixed_text(world, tmp_path):
    good = world.mapping_file("good.json")
    wrong = world.mapping_file("wrong-snapshot.json", world.raw(snapshot_id="f" * 64))
    broken = world.mapping_file("broken.json", b"{")
    v2 = world.mapping_file("v2.json", b'{"format": "atlas-rationale-mapping.v2"}')
    cases = [
        (map_args(world, wrong, tmp_path / "x"), "mapping_snapshot_mismatch"),
        (map_args(world, broken, tmp_path / "x"), "invalid_mapping"),
        (map_args(world, v2, tmp_path / "x"), "unsupported_mapping_version"),
        (map_args(world, good, tmp_path / "x", "--max-mapping-bytes", 1), "mapping_too_large"),
        (map_args(world, good, tmp_path / "x", "--max-mappings", 1), "too_many_mappings"),
        (map_args(world, good, tmp_path / "x", "--max-output-bytes", 1), "output_too_large"),
    ]
    for number, (args, reason) in enumerate(cases):
        out, sentinel = sentinel_output(tmp_path, f"m{number}.json")
        args[args.index("--output") + 1] = str(out)
        result = cli_run(*args)
        assert_rejected_cli(result, "Rationale mapping rejected: " + reason, out, sentinel)
    out, sentinel = sentinel_output(tmp_path, "empty-root.json")
    args = map_args(world, good, out)
    args[args.index("--graph-root") + 1] = str(world.tmp / "no-index")
    (world.tmp / "no-index").mkdir(exist_ok=True)
    result = cli_run(*args)
    assert_rejected_cli(result, "Rationale mapping rejected: index_unavailable", out, sentinel)


def test_drift_rejections_are_fixed_text(world, tmp_path):
    good = world.mapping_file("good.json")
    wrong = world.mapping_file("wrong-snapshot.json", world.raw(snapshot_id="f" * 64))
    cases = [
        (drift_args(world, wrong, tmp_path / "x"), "mapping_snapshot_mismatch"),
        (drift_args(world, good, tmp_path / "x", None, "--max-mappings", 1), "too_many_mappings"),
        (
            drift_args(world, good, tmp_path / "x", None, "--max-output-bytes", 1),
            "output_too_large",
        ),
    ]
    for number, (args, reason) in enumerate(cases):
        out, sentinel = sentinel_output(tmp_path, f"d{number}.json")
        args[args.index("--output") + 1] = str(out)
        result = cli_run(*args)
        assert_rejected_cli(result, "Rationale drift rejected: " + reason, out, sentinel)
    out, sentinel = sentinel_output(tmp_path, "after-empty.json")
    args = drift_args(world, good, out)
    args[args.index("--after-graph-root") + 1] = str(world.tmp / "no-index")
    result = cli_run(*args)
    assert_rejected_cli(result, "Rationale drift rejected: after_index_unavailable", out, sentinel)


def test_unexpected_failures_never_echo_their_text(world, tmp_path, monkeypatch):
    good = world.mapping_file("good.json")

    def boom(*args, **kwargs):
        raise RuntimeError("/secret/path")

    monkeypatch.setattr(am, "map_rationale", boom)
    out, sentinel = sentinel_output(tmp_path, "boom.json")
    result = cli_run(*map_args(world, good, out))
    assert_rejected_cli(result, "Rationale mapping failed: RuntimeError", out, sentinel)
    assert "secret" not in result.stderr
    monkeypatch.setattr(drift, "rationale_drift", boom)
    result = cli_run(*drift_args(world, good, out))
    assert_rejected_cli(result, "Rationale drift failed: RuntimeError", out, sentinel)


def test_a_write_failure_is_fixed_text(world, tmp_path, monkeypatch):
    good = world.mapping_file("good.json")

    def refuse(target, data):
        raise OSError("/secret/path")

    monkeypatch.setattr(entry, "_write_rationale_atomic", refuse)
    out, sentinel = sentinel_output(tmp_path, "write.json")
    result = cli_run(*map_args(world, good, out))
    assert_rejected_cli(result, "Rationale output cannot be written.", out, sentinel)


def test_a_failed_write_leaves_no_temporary_file(world, tmp_path, monkeypatch):
    target = tmp_path / "atomic.json"

    def refuse(source, destination):
        raise OSError("no")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        entry._write_rationale_atomic(target, b"data")
    assert temp_leftovers(tmp_path) == [] and not target.exists()


def _usage(result, text):
    assert result.exit_code == 2, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert "Error: " + text in result.stderr


def test_usage_errors_exit_two(world, tmp_path):
    good = world.mapping_file("good.json")
    out = tmp_path / "usage.json"
    for name, (_, low, high) in am.LIMITS.items():
        for bad in (low - 1, high + 1):
            flag = "--" + name.replace("_", "-")
            result = cli_run(*map_args(world, good, out, flag, bad))
            _usage(result, "Invalid rationale option: " + name)
            result = cli_run(*drift_args(world, good, out, None, flag, bad))
            _usage(result, "Invalid rationale option: " + name)
    assert not out.exists()
    result = cli_run(*map_args(world, good, out, "--max-mappings", "True"))
    assert result.exit_code == 2 and not out.exists()
    protected = [
        world.repo / "x.json",
        world.repo / ".atlas" / "x.json",
        world.before.graph_root / "x.json",
    ]
    link = tmp_path / "link"
    link.symlink_to(world.before.graph_root)
    protected.append(link / "x.json")
    outside = "--output must be outside the repositories and graph roots."
    for place in protected:
        _usage(cli_run(*map_args(world, good, place)), outside)
        _usage(cli_run(*drift_args(world, good, place)), outside)
        assert not place.exists()
    after_root = world.after.graph_root / "x.json"  # protected by the drift command only
    _usage(cli_run(*drift_args(world, good, after_root)), outside)
    assert not after_root.exists()
    result = cli_run(*map_args(world, good, good))
    _usage(result, "--output must differ from the mapping.")
    assert good.read_bytes() == world.raw()
    same = drift_args(world, good, out)
    same[same.index("--after-graph-root") + 1] = str(world.before.graph_root)
    _usage(cli_run(*same), "Both sides resolve to the same graph namespace.")
    bare = ["rationale-drift", "--mapping", str(good), "--output", str(out)]
    _usage(cli_run(*bare), "Give --repo, or both --before-repo and --after-repo.")
    half = [*bare, "--before-repo", str(world.repo)]
    _usage(cli_run(*half), "Give --repo, or both --before-repo and --after-repo.")
    assert not out.exists()


def test_a_namespace_that_cannot_be_opened_is_usage(world, tmp_path, monkeypatch):
    good = world.mapping_file("good.json")
    monkeypatch.setattr(symbol_diff, "_open", _usage_refusal)
    _usage(
        cli_run(*map_args(world, good, tmp_path / "n.json")),
        "The repository cannot be opened as a graph namespace.",
    )
    _usage(
        cli_run(*drift_args(world, good, tmp_path / "n2.json")),
        "The repository cannot be opened as a graph namespace.",
    )


def test_an_unreadable_mapping_is_a_fixed_exit_one(world, tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for path in (fifo, tmp_path, tmp_path / "missing.json"):
        out, sentinel = sentinel_output(tmp_path, "unreadable.json")
        for args in (map_args(world, path, out), drift_args(world, path, out)):
            result = cli_run(*args)
            assert_rejected_cli(result, "Rationale mapping cannot be read.", out, sentinel)
