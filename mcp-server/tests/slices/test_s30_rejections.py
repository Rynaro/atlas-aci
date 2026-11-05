"""ACI-S30 rejections and usage errors: closed reasons, fixed texts, empty stdout, no echo."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_s19_support import build, git, init_git
from test_s30_support import (
    FX,
    GEN,
    SHA_EMPTY,
    World,
    assert_rejected_cli,
    cli_impact,
    cli_link,
    cli_run,
    file_entity,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    make_world,
    sentinel_output,
    sha256,
)

from atlas_aci.cross_repo import package_links as pl
from atlas_aci.cross_repo import revision_vector as rv
from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.overlays import runtime
from atlas_aci.query.diff import Side

pytestmark = pytest.mark.usefixtures("grammar")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("rej"))


@pytest.fixture(scope="module")
def paths(world):
    return world.write_inputs()


@pytest.fixture(scope="module")
def built(world):
    return world.link()


def fresh(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    return folder


def reason_of(call) -> str:
    with pytest.raises(rv.CrossRepoRejected) as caught:
        call()
    return caught.value.reason


def link_rejected(tmp_path, world: World, paths: dict[str, Path], reason: str, **inputs: bytes):
    """Library and CLI agree: the closed reason, exit 1, the fixed text, empty stdout."""
    out, sentinel = sentinel_output(tmp_path)
    written = dict(paths)
    for name, data in inputs.items():
        target = tmp_path / (name + ".in")
        target.write_bytes(data)
        written[name] = target
    result = cli_link(world, written, out)
    assert_rejected_cli(result, "Cross-repository link rejected: " + reason, out, sentinel)


# ---- the mapping -------------------------------------------------------------------------------


def valid_text(world: World) -> str:
    return json.dumps(world.document())


def mapping_cases(world: World) -> dict[str, tuple[bytes, str]]:
    doc = world.document()
    cases: dict[str, tuple[bytes, str]] = {}

    def edit(name: str, reason: str, change) -> None:
        copy = json.loads(json.dumps(doc))
        change(copy)
        cases[name] = (json.dumps(copy).encode(), reason)

    bad = "invalid_mapping"
    text = valid_text(world)
    cases["bom"] = (b"\xef\xbb\xbf" + text.encode(), bad)
    cases["not_utf8"] = (b"\xff\xfe{}", bad)
    cases["duplicate_key"] = (text.replace('"role"', '"role": "consumer", "role"', 1).encode(), bad)
    for name, raw in {
        "nan": b'{"format": NaN}',
        "infinity": b'{"format": Infinity}',
        "float": b'{"format": 1.5}',
        "deep": b"[" * 10000 + b"]" * 10000,
        "empty": b"",
        "array": b"[]",
    }.items():
        cases[name] = (raw, bad)
    edit("number", bad, lambda d: d["consumer"].update(n=1))
    edit("wrong_format", "unsupported_mapping_version", lambda d: d.update(format="x.v2"))
    edit("format_not_text", bad, lambda d: d.update(format=2))
    edit("extra_top", bad, lambda d: d.update(extra=1))
    edit("extra_repo", bad, lambda d: d["repositories"][0].update(path="/x"))
    edit("extra_consumer", bad, lambda d: d["consumer"].update(root="/x"))
    edit("extra_package", bad, lambda d: d["packages"][0].update(root="/x"))
    edit("one_repo", bad, lambda d: d["repositories"].pop())
    edit("two_consumers", bad, lambda d: d["repositories"][1].update(role="consumer"))
    edit("no_package", bad, lambda d: d.update(packages=[]))
    edit("two_packages", bad, lambda d: d["packages"].append(d["packages"][0]))
    repo = "repository_id"
    edit("upper_id", bad, lambda d: d["repositories"][0].update({repo: "App"}))
    edit("long_id", bad, lambda d: d["repositories"][0].update({repo: "a" * 65}))
    edit("dash_id", bad, lambda d: d["repositories"][0].update({repo: "-a"}))
    edit("short_snapshot", bad, lambda d: d["repositories"][0].update(snapshot_id="ab"))
    edit("upper_snapshot", bad, lambda d: d["repositories"][0].update(snapshot_id="A" * 64))
    edit("bad_lock_digest", bad, lambda d: d["consumer"].update(lock_sha256="x"))
    env = "environment"
    edit("env_unknown", bad, lambda d: d["consumer"].update({env: {"x": "y"}}))
    edit("env_not_text", bad, lambda d: d["consumer"].update({env: {"os_name": 1}}))
    edit("env_zero", bad, lambda d: d["consumer"].update({env: {"python_full_version": "03.12"}}))
    edit("env_list", bad, lambda d: d["consumer"].update({env: []}))
    coordinate = "coordinate"
    for name, value in {
        "coordinate_case": "pypi:S30-shapes@1.0.0",
        "coordinate_unnormalized": "pypi:s30--shapes@1.0.0",
        "coordinate_no_version": "pypi:s30-shapes",
        "coordinate_ecosystem": "npm:s30-shapes@1.0.0",
    }.items():
        edit(name, bad, lambda d, value=value: d["packages"][0].update({coordinate: value}))
    for name, value in {
        "prefix_parent": "../",
        "prefix_dot": ".x/",
        "prefix_absolute": "/abs/",
        "prefix_double": "a//b/",
        "prefix_no_slash": "src",
        "prefix_deep": "a/" * 9,
    }.items():
        edit(name, bad, lambda d, value=value: d["packages"][0].update(source_prefix=value))
    edit("package_hash", bad, lambda d: d["packages"][0].update(package_snapshot_sha256="z"))
    stranger = "provider_repository_id"
    edit(
        "other_repository",
        "repository_not_allowlisted",
        lambda d: d["packages"][0].update({stranger: "other"}),
    )

    def same_labels(d):
        d["repositories"][1][repo] = "app"
        d["packages"][0][stranger] = "app"

    edit("same_labels", "same_repository", same_labels)
    first = doc["repositories"][0]["snapshot_id"]
    edit(
        "same_snapshots",
        "same_repository",
        lambda d: d["repositories"][1].update(snapshot_id=first),
    )
    return cases


def test_every_malformed_mapping_is_rejected_with_a_closed_reason(world, paths, tmp_path):
    cases = mapping_cases(world)
    assert len(cases) > 40
    for name, (raw, reason) in cases.items():
        assert reason_of(lambda raw=raw: world.link(mapping_raw=raw)) == reason, name
        assert reason in rv.REJECTIONS
    # The CLI carries the same reasons with the fixed text.
    for name in ("bom", "deep", "extra_repo", "other_repository", "wrong_format", "same_labels"):
        raw, reason = cases[name]
        link_rejected(fresh(tmp_path, name), world, paths, reason, mapping=raw)


def test_the_mapping_size_is_bounded(world):
    raw = valid_text(world).encode()
    limits = pl.normalize_limits({"max_mapping_bytes": len(raw)})
    assert world.link(limits=limits)["links"]
    small = pl.normalize_limits({"max_mapping_bytes": len(raw) - 1})
    assert reason_of(lambda: world.link(limits=small)) == "mapping_too_large"


def test_a_mapping_that_equals_the_example_shape_parses():

    parsed = rv.parse_mapping((FX / "mapping-example.json").read_bytes(), 65536)
    assert (parsed.consumer_id, parsed.provider_id) == ("app", "lib")
    assert parsed.source_prefix == "src/" and parsed.environment == {}


# ---- the lock and the package snapshot ---------------------------------------------------------


def locked(world: World, lock: bytes) -> dict:
    return {"lock_raw": lock, "mapping_raw": world.mapping(lock=lock)}


def lock_cases() -> dict[str, bytes]:
    other = GEN.package_block(
        "other-package", "1.0.0", wheels=(GEN.pure_line("other-package", "1.0.0"),)
    )
    pure = GEN.pure_line("s30-shapes", "1.0.0"), GEN.pure_line("s30-shapes", "1.1.0")
    two = [
        GEN.package_block("s30-shapes", "1.0.0", wheels=(pure[0],)),
        GEN.package_block("s30-shapes", "1.1.0", wheels=(pure[1],)),
    ]
    path_source = GEN.package_block("s30-shapes", "1.0.0", 'path = "../x"')
    no_wheel = GEN.package_block(
        "s30-shapes", "1.0.0", sdist=GEN.sdist_line("s30_shapes-1.0.0.tar.gz")
    )
    return {
        "lock_invalid": b"not = [valid",
        "unsupported_lock_profile": GEN.lock_document([other], version=2).encode(),
        "package_not_in_lock": GEN.lock_document([other]).encode(),
        "unresolved_lock_variant": GEN.lock_document(two).encode(),
        "unsupported_source_kind": GEN.lock_document([path_source]).encode(),
        "no_locked_pure_wheel": GEN.lock_document([no_wheel]).encode(),
    }


def test_lock_rejections(world, paths, tmp_path):
    cases = lock_cases()
    assert set(cases) == set(rv.LOCK_REASONS)
    for reason, lock in cases.items():
        assert reason_of(lambda lock=lock: world.link(**locked(world, lock))) == reason
        shown = fresh(tmp_path, reason)
        link_rejected(shown, world, paths, reason, lock=lock, mapping=world.mapping(lock=lock))
    # The lock bytes must be the ones the mapping declares; the size is bounded.
    assert reason_of(lambda: world.link(lock_raw=world.lock + b"\n")) == "lock_digest_mismatch"
    small = pl.normalize_limits({"max_lock_bytes": len(world.lock) - 1})
    assert reason_of(lambda: world.link(limits=small)) == "lock_too_large"


def mapped(world: World, raw: bytes) -> dict:
    """The keyword arguments of a link whose mapping declares `raw` as the package snapshot."""
    doc = world.document()
    doc["packages"][0]["package_snapshot_sha256"] = sha256(raw)
    return {"package_raw": raw, "mapping_raw": json.dumps(doc).encode()}


def test_package_snapshot_rejections(world, paths, tmp_path):
    package = world.package
    document = json.loads(package.snapshot)

    pretty = json.dumps(document, indent=2).encode()
    forged = json.loads(package.snapshot)
    forged["snapshot_id"] = SHA_EMPTY
    tampered = json.loads(package.snapshot)
    source = next(m for m in tampered["members"] if m["role"] == "python_source")
    source["sha256"] = SHA_EMPTY  # the module and declaration records name the member digest
    duplicate = package.snapshot.replace(b'{"artifact"', b'{"artifact":1,"artifact"', 1)
    cases = {
        "pretty": pretty,
        "forged_id": pw.canonical_bytes(forged),
        "tampered_source": pw.canonical_bytes(tampered),
        "duplicate_key": duplicate,
        "nan": b'{"snapshot_id": NaN}',
        "huge_integer": package.snapshot.replace(b'"revision":3', b'"revision":' + b"9" * 400, 1),
        "deep": b"[" * 10000 + b"]" * 10000,
        "bom": b"\xef\xbb\xbf" + package.snapshot,
        "empty": b"{}",
    }
    for name, raw in cases.items():
        assert (
            reason_of(lambda raw=raw: world.link(**mapped(world, raw)))
            == "invalid_package_snapshot"
        ), name
    # The reason of `validate_snapshot` is mapped, not passed through.
    assert "invalid_snapshot" not in rv.REJECTIONS
    assert reason_of(lambda: world.link(package_raw=package.snapshot + b"\n")) == (
        "package_snapshot_digest_mismatch"
    )
    small = pl.normalize_limits({"max_package_snapshot_bytes": len(package.snapshot) - 1})
    assert reason_of(lambda: world.link(limits=small)) == "package_snapshot_too_large"
    link_rejected(
        tmp_path, world, paths, "invalid_package_snapshot",
        package=pretty, mapping=mapped(world, pretty)["mapping_raw"],
    )  # fmt: skip


def test_the_previous_overlay_is_judged_strictly(world, paths, built, tmp_path):
    raw = pl.canonical_bytes(built)
    pretty = json.dumps(built, indent=1).encode()
    tampered = json.loads(raw)
    tampered["counts"]["links"] += 1
    huge = raw.replace(b'"line":1,', b'"line":' + b"9" * 400 + b",", 1)
    for name, data in {
        "pretty": pretty, "tampered": json.dumps(tampered).encode(), "huge": huge,
        "garbage": b"not json", "array": b"[]",
        "dup": raw.replace(b'"format"', b'"format":1,"format"', 1),
        "deep": b"[" * 10000, "nan": b'{"a": NaN}',
    }.items():  # fmt: skip
        assert reason_of(lambda data=data: world.link(previous_raw=data)) == (
            "invalid_previous_overlay"
        ), name
    small = pl.normalize_limits({"max_overlay_bytes": len(raw) - 1})
    assert reason_of(lambda: world.link(previous_raw=raw, limits=small)) == (
        "previous_overlay_too_large"
    )
    out, sentinel = sentinel_output(tmp_path)
    garbage = tmp_path / "previous.json"
    garbage.write_bytes(b"{")
    result = cli_link(world, paths, out, "--previous", garbage)
    assert_rejected_cli(
        result, "Cross-repository link rejected: invalid_previous_overlay", out, sentinel
    )


# ---- the leases --------------------------------------------------------------------------------


def test_consumer_lease_failures_are_rejections_and_the_provider_is_a_state(tmp_path):
    world = make_world(tmp_path)
    wrong = world.document()
    wrong["repositories"][0]["snapshot_id"] = "9" * 64
    assert reason_of(lambda: world.link(mapping_raw=json.dumps(wrong).encode())) == (
        "consumer_snapshot_mismatch"
    )
    sources = world.graphs / "app"
    sources_dir = next(sources.rglob("source-snapshots"))
    stash = tmp_path / "stash"
    shutil.move(str(sources_dir), str(stash))
    assert reason_of(world.link) == "consumer_source_snapshot_unavailable"
    shutil.move(str(stash), str(sources_dir))
    assert world.link()["links"]
    shutil.rmtree(world.graphs / "app")
    assert reason_of(world.link) == "consumer_index_unavailable"
    missing_provider = shutil.rmtree(world.graphs / "lib")
    assert missing_provider is None


def test_provider_lease_failures_are_closed_states_not_rejections(tmp_path):
    world = make_world(tmp_path)
    shutil.rmtree(next((world.graphs / "lib").rglob("source-snapshots")))
    built = world.link()
    assert built["provider"]["detail"] == "source_snapshot_unavailable"
    assert built["provider"]["state"] == "unavailable" and built["links"] == []
    shutil.rmtree(world.graphs / "lib")
    assert world.link()["provider"]["detail"] == "index_unavailable"
    for detail in pl.PROVIDER_DETAILS:
        assert detail in pl.RESOLUTION_DETAILS["unresolved_provider_snapshot_unavailable"]


def test_the_two_sides_must_be_distinct_repositories(tmp_path):
    world = make_world(tmp_path)
    same = Side(world.app, world.graphs / "app")
    assert reason_of(lambda: world.link(provider=same)) == "same_repository"
    default_roots = Side(world.app)  # the default `.atlas` lies inside the one repository
    assert reason_of(lambda: world.link(consumer=default_roots, provider=default_roots)) == (
        "same_repository"
    )
    # Two directories of one git repository (a worktree) share a common dir: not distinct.
    init_git(world.app, commit=True)
    world.reindex("app")
    git(world.app, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "second")
    stats = build(tmp_path / "wt", world.graphs / "wt", ["python"])
    doc = world.document()
    doc["repositories"][1]["snapshot_id"] = stats["snapshot_id"]
    side = Side(tmp_path / "wt", world.graphs / "wt")
    assert reason_of(lambda: world.link(mapping_raw=json.dumps(doc).encode(), provider=side)) == (
        "same_repository"
    )


# ---- impact ------------------------------------------------------------------------------------


def test_impact_rejections(world, built, paths, tmp_path):
    raw = pl.canonical_bytes(built)
    seed = file_entity("app/main.py")
    assert reason_of(lambda: world.impact(b"{}", seed, "to_provider")) == "invalid_overlay"
    assert (
        reason_of(lambda: world.impact(json.dumps(built, indent=1).encode(), seed, "to_provider"))
        == "invalid_overlay"
    )
    assert reason_of(lambda: world.impact(raw + b" ", seed, "to_provider")) == "invalid_overlay"
    assert (
        reason_of(
            lambda: world.impact(
                raw.replace(b'"format"', b'"format":1,"format"', 1), seed, "to_provider"
            )
        )
        == "invalid_overlay"
    )
    other = world.mapping(prefix="vendor/")
    assert (
        reason_of(lambda: world.impact(raw, seed, "to_provider", other))
        == "overlay_mapping_mismatch"
    )
    assert reason_of(lambda: world.impact(raw, "f" * 64, "to_provider")) == "unknown_seed"
    assert reason_of(lambda: world.impact(raw, "f" * 64, "from_provider")) == "unknown_seed"
    small = pl.normalize_limits({"max_overlay_bytes": len(raw) - 1})
    assert (
        reason_of(
            lambda: pl.impact(
                raw,
                world.mapping(),
                world.consumer,
                world.provider,
                seed,
                "to_provider",
                None,
                small,
            )
        )
        == "overlay_too_large"
    )
    out = tmp_path / "overlay.json"
    out.write_bytes(raw)
    for overlay, who, text in (
        (paths["lock"], seed, "invalid_overlay"),
        (out, "f" * 64, "unknown_seed"),
    ):
        result = cli_impact(world, overlay, paths["mapping"], who, "to_provider")
        assert result.exit_code == 1 and result.stdout_bytes == b""
        assert result.stderr == "Error: Cross-repository impact rejected: " + text + "\n"


def test_the_overlay_validator_names_one_closed_reason(built):
    for broken in ({}, [], "x", None, 1):
        assert reason_of(lambda broken=broken: pl.validate_overlay(broken)) == "invalid_overlay"
    assert reason_of(lambda: pl.validate_impact({})) == "invalid_overlay"
    assert pl.validate_overlay(built) is None


# ---- usage errors (exit 2) ---------------------------------------------------------------------


def usage(result, text: str):
    assert result.exit_code == 2, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr.rstrip().endswith("Error: " + text), result.stderr


@pytest.mark.parametrize("name", sorted(pl.LIMITS))
def test_every_link_limit_is_range_checked(world, paths, tmp_path, name):
    _, low, high = pl.LIMITS[name]
    option = "--" + name.replace("_", "-")
    for bad in (low - 1, high + 1, -5):
        out = tmp_path / f"o{bad}.json"
        result = cli_link(world, paths, out, option, bad)
        usage(result, "Invalid cross-repository limit: " + name)
        assert not out.exists()
    for bad in (True, 0, high + 1, "3", 1.0, None):
        if bad is None:
            continue
        with pytest.raises(rv.CrossRepoUsageError) as caught:
            pl.normalize_limits({name: bad})
        assert caught.value.what == name
    ok = cli_link(world, paths, tmp_path / "ok.json", option, high)
    assert ok.exit_code == 0


@pytest.mark.parametrize("name", sorted({*pl.BUDGETS, "max_overlay_bytes", "max_mapping_bytes"}))
def test_every_impact_budget_and_limit_is_range_checked(world, paths, built, tmp_path, name):
    table = pl.BUDGETS if name in pl.BUDGETS else pl.LIMITS
    _, low, high = table[name]
    out = tmp_path / "overlay.json"
    out.write_bytes(pl.canonical_bytes(built))
    seed = file_entity("app/main.py")
    for bad in (low - 1, high + 1):
        result = cli_impact(
            world, out, paths["mapping"], seed, "to_provider", "--" + name.replace("_", "-"), bad
        )
        usage(result, "Invalid cross-repository limit: " + name)


def test_bad_seed_and_direction_are_usage(world, paths, built, tmp_path):
    out = tmp_path / "overlay.json"
    out.write_bytes(pl.canonical_bytes(built))
    for seed in ("xyz", "", "A" * 64, "f" * 63):
        usage(
            cli_impact(world, out, paths["mapping"], seed, "to_provider"), "Invalid --seed value."
        )
    sideways = cli_impact(world, out, paths["mapping"], "f" * 64, "sideways")
    assert sideways.exit_code == 2 and sideways.stdout_bytes == b""
    assert "Invalid value for '--direction'" in sideways.stderr


def test_the_output_must_stay_outside_both_repositories_and_graph_roots(world, paths, tmp_path):
    text = "--output must be outside both repositories and both graph roots."
    link = tmp_path / "linked"
    link.symlink_to(world.app)
    candidates = [
        world.app / "o.json",
        world.lib / "sub" / "o.json",
        world.app / ".atlas" / "o.json",
        world.graphs / "app" / "o.json",
        world.graphs / "lib" / "repos" / "o.json",
        link / "o.json",
    ]
    for target in candidates:
        usage(cli_link(world, paths, target), text)
        assert not target.exists()
    assert not list(world.app.rglob("o.json")) and not list(world.lib.rglob("o.json"))
    before = sorted(p.name for p in world.app.iterdir())
    usage(cli_link(world, paths, link / "o.json"), text)
    assert sorted(p.name for p in world.app.iterdir()) == before


def test_the_output_must_differ_from_every_input(world, paths, built, tmp_path):
    text = "--output must differ from every input file."
    previous = tmp_path / "previous.json"
    previous.write_bytes(pl.canonical_bytes(built))
    for name in ("mapping", "lock", "package"):
        usage(cli_link(world, paths, paths[name]), text)
    usage(cli_link(world, paths, previous, "--previous", previous), text)
    hard = tmp_path / "hard.json"
    os.link(paths["mapping"], hard)
    usage(cli_link(world, paths, hard), text)
    assert hard.read_bytes() == paths["mapping"].read_bytes()


def test_a_repository_that_cannot_be_a_namespace_is_usage(world, paths, tmp_path):
    result = cli_run(
        "cross-repo-link", "--mapping", paths["mapping"], "--lock", paths["lock"],
        "--package-snapshot", paths["package"],
        "--consumer-repo", world.app, "--consumer-graph-root", "/dev/null/graphs",
        "--provider-repo", world.lib, "--provider-graph-root", world.graphs / "lib",
        "--output", tmp_path / "o.json",
    )  # fmt: skip
    usage(result, "A repository cannot be opened as a graph namespace.")
    assert not (tmp_path / "o.json").exists()
    with pytest.raises(rv.CrossRepoUsageError) as caught:
        world.link(consumer=Side(world.app, Path("/dev/null/graphs")))
    assert caught.value.what == "graph_namespace"


def test_nested_repositories_and_graph_roots_are_usage_errors(world, paths, tmp_path):
    text = "The repositories and graph roots must not overlap."
    nested = world.app / "vendored"
    nested.mkdir()
    out = tmp_path / "o.json"
    base = ["cross-repo-link", "--mapping", paths["mapping"], "--lock", paths["lock"],
            "--package-snapshot", paths["package"], "--output", out]  # fmt: skip
    for consumer, provider, consumer_root, provider_root in (
        (world.app, nested, world.graphs / "app", world.graphs / "lib"),
        (nested, world.app, world.graphs / "app", world.graphs / "lib"),
        (world.app, world.lib, world.lib / "g", world.graphs / "lib"),
        (world.app, world.lib, world.graphs / "app", world.app / "g"),
    ):
        result = cli_run(
            *base, "--consumer-repo", consumer, "--provider-repo", provider,
            "--consumer-graph-root", consumer_root, "--provider-graph-root", provider_root,
        )  # fmt: skip
        usage(result, text)
        assert not out.exists()
    with pytest.raises(rv.CrossRepoUsageError) as caught:
        world.link(provider=Side(nested, world.graphs / "lib"))
    assert caught.value.what == "repository_overlap"
    nested.rmdir()
    # A graph root shared by both sides is fine: namespaces are keyed by the repository path.
    shared = tmp_path / "shared"
    for name, repo in (("app", world.app), ("lib", world.lib)):
        world.snapshots[name] = build(repo, shared, ["python"])["snapshot_id"]
    chosen = dict(paths, mapping=tmp_path / "shared-mapping.json")
    chosen["mapping"].write_bytes(world.mapping())
    result = cli_run(
        *base[:-2], "--output", out, "--mapping", chosen["mapping"],
        "--consumer-repo", world.app, "--provider-repo", world.lib,
        "--consumer-graph-root", shared, "--provider-graph-root", shared,
    )  # fmt: skip
    assert result.exit_code == 0, result.stderr


# ---- operator inputs ---------------------------------------------------------------------------


def test_inputs_that_are_not_regular_files_are_refused_promptly(world, paths, built, tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    folder = tmp_path / "folder"
    folder.mkdir()
    previous = tmp_path / "previous.json"
    previous.write_bytes(pl.canonical_bytes(built))
    for special in (fifo, folder, tmp_path / "absent"):
        for name, text in (
            ("mapping", "Cross-repository mapping cannot be read."),
            ("lock", "uv lock file cannot be read."),
            ("package", "Package snapshot cannot be read."),
        ):
            out, sentinel = sentinel_output(tmp_path, name + "-" + special.name + ".json")
            result = cli_link(world, dict(paths, **{name: special}), out)
            assert_rejected_cli(result, text, out, sentinel)
        out, sentinel = sentinel_output(tmp_path)
        result = cli_link(world, paths, out, "--previous", special)
        assert_rejected_cli(result, "Previous overlay cannot be read.", out, sentinel)
        for option, text in (
            ("--overlay", "Cross-repository overlay cannot be read."),
            ("--mapping", "Cross-repository mapping cannot be read."),
        ):
            args = {"--overlay": previous, "--mapping": paths["mapping"]}
            args[option] = special
            result = cli_run(
                "cross-repo-impact", "--overlay", args["--overlay"], "--mapping", args["--mapping"],
                "--seed", "f" * 64, "--direction", "to_provider",
                "--consumer-repo", world.app, "--consumer-graph-root", world.graphs / "app",
                "--provider-repo", world.lib, "--provider-graph-root", world.graphs / "lib",
            )  # fmt: skip
            assert result.exit_code == 1 and result.stdout_bytes == b""
            assert result.stderr == "Error: " + text + "\n"


def test_an_unwritable_output_is_a_fixed_text_not_a_traceback(world, paths, tmp_path):
    result = cli_link(world, paths, tmp_path / "missing-directory" / "o.json")
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Cross-repository overlay cannot be written.\n"
    assert not (tmp_path / "missing-directory").exists()


def test_an_unexpected_error_names_only_its_type(world, paths, built, tmp_path, monkeypatch):
    secret = "SECRET-" + str(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(pl, "link", boom)
    out, sentinel = sentinel_output(tmp_path)
    assert_rejected_cli(
        cli_link(world, paths, out), "Cross-repository link failed: RuntimeError", out, sentinel
    )
    monkeypatch.setattr(pl, "impact", boom)
    overlay = tmp_path / "overlay.json"
    overlay.write_bytes(pl.canonical_bytes(built))
    result = cli_impact(world, overlay, paths["mapping"], file_entity("app/main.py"), "to_provider")
    assert result.exit_code == 1 and result.stdout_bytes == b""
    assert result.stderr == "Error: Cross-repository impact failed: RuntimeError\n"


def test_no_rejection_echoes_a_path_or_an_input(world, paths, tmp_path):
    marker = "MARKER-" + "x" * 8
    raw = ('{"format": "' + marker + '"}').encode()
    out, sentinel = sentinel_output(tmp_path)
    secret_dir = tmp_path / ("dir-" + marker)
    secret_dir.mkdir()
    mapping = secret_dir / "mapping.json"
    mapping.write_bytes(raw)
    result = cli_link(world, dict(paths, mapping=mapping), out)
    assert_rejected_cli(
        result, "Cross-repository link rejected: unsupported_mapping_version", out, sentinel
    )
    assert marker not in result.stderr and str(tmp_path) not in result.stderr


# ---- the closed set ----------------------------------------------------------------------------


def denying(real, who: str, table: str):
    """A lease whose database refuses to read one table of one side (a real sqlite error)."""

    def lease(stack, side):
        reader = real(stack, side)
        if side.repo.name == who:

            def authorizer(action, first, second, database, source):
                deny = action == sqlite3.SQLITE_READ and first == table
                return sqlite3.SQLITE_DENY if deny else sqlite3.SQLITE_OK

            reader.db.set_authorizer(authorizer)
        return reader

    return lease


def test_the_rejection_set_is_closed_and_every_reason_is_reachable(tmp_path, monkeypatch):
    seen: set[str] = set()
    world = make_world(tmp_path / "w")
    old_mapping = world.mapping()
    raw = pl.canonical_bytes(world.link())
    seed = file_entity("app/main.py")
    tiny = {name: pl.normalize_limits({name: 10}) for name in pl.LIMITS}

    def record(call):
        seen.add(reason_of(call))

    for data, _ in mapping_cases(world).values():
        record(lambda data=data: world.link(mapping_raw=data))
    record(lambda: world.link(limits=tiny["max_mapping_bytes"]))
    for lock in lock_cases().values():
        record(lambda lock=lock: world.link(**locked(world, lock)))
    record(lambda: world.link(lock_raw=world.lock + b"\n"))
    record(lambda: world.link(limits=tiny["max_lock_bytes"]))
    record(lambda: world.link(**mapped(world, b"{}")))
    record(lambda: world.link(package_raw=world.package.snapshot + b"\n"))
    record(lambda: world.link(limits=tiny["max_package_snapshot_bytes"]))
    record(lambda: world.link(previous_raw=b"{}"))
    record(lambda: world.link(previous_raw=raw, limits=tiny["max_overlay_bytes"]))
    unrelated = world.mapping(consumer_id="a2", provider_id="l2")
    record(lambda: world.link(mapping_raw=unrelated, previous_raw=raw))
    record(lambda: world.link(limits=pl.normalize_limits({"max_references": 1})))
    record(lambda: world.impact(b"{}", seed, "to_provider"))
    record(lambda: world.impact(raw, "f" * 64, "to_provider"))
    record(lambda: world.impact(raw, seed, "to_provider", world.mapping(prefix="x/")))
    record(lambda: world.impact(raw, seed, "to_provider", limits=tiny["max_overlay_bytes"]))
    wrong = world.document()
    wrong["repositories"][0]["snapshot_id"] = "9" * 64
    record(lambda: world.link(mapping_raw=json.dumps(wrong).encode()))
    real = runtime.open_active

    def old(stack, side):
        reader = real(stack, side)
        reader._lease_schema_epoch = 8 if side.repo.name == "app" else 9
        return reader

    monkeypatch.setattr(runtime, "open_active", old)
    record(world.link)
    for who, table in (("app", "import_bindings"), ("lib", "symbols")):
        monkeypatch.setattr(runtime, "open_active", denying(real, who, table))
        record(world.link)
    monkeypatch.setattr(runtime, "open_active", real)
    # A moved consumer makes the old overlay stale; a damaged or vanished consumer index rejects.
    world.edit("app", "app/area_tools.py", "def area():\n    return 5\n")
    record(lambda: world.impact(raw, seed, "to_provider", old_mapping))
    shutil.rmtree(next((world.graphs / "app").rglob("source-snapshots")))
    record(world.link)
    shutil.rmtree(world.graphs / "app")
    record(world.link)
    assert seen == set(rv.REJECTIONS), set(rv.REJECTIONS) ^ seen
