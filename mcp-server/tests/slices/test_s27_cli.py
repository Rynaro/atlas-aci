"""ACI-S27 CLI surface: output containment, read-only operation and input reads."""

from __future__ import annotations

import os
import stat

import pytest
from test_s19_support import inventory
from test_s27_support import basic, example, make_world, manifest_for, raw_of, run_cli, write_inputs

from atlas_aci.codegraph import CodeGraph
from atlas_aci.overlays import runtime

OUTSIDE = "Error: --output must be outside --repo and the graph root.\n"
DIFFERENT = "Error: --output must differ from the manifest and the artifact.\n"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("s27-cli"))


@pytest.fixture
def inputs(world, tmp_path):
    manifest = raw_of(manifest_for(basic(), world.snapshot_id))
    return write_inputs(tmp_path / "inputs", basic(), manifest)


def lease_identity(world):
    graph = CodeGraph(world.repo, read_only=True, graph_root=world.root)
    try:
        with graph.reader_lease() as reader:
            return reader.snapshot_id, reader._lease_generation_id
    finally:
        graph.close()


def tail(result) -> str:
    return result.stderr.split("\n\n")[-1]


def test_otel_output_must_be_outside_the_repo_and_the_graph(world, inputs, tmp_path):
    manifest, artifact = inputs
    link_repo = tmp_path / "link-repo"
    link_graph = tmp_path / "link-graph"
    link_repo.symlink_to(world.repo, target_is_directory=True)
    link_graph.symlink_to(world.root, target_is_directory=True)
    (tmp_path / "sub").mkdir()
    inside = [
        world.repo / "out.json",
        world.repo / "no" / "such" / "dir" / "out.json",
        world.root / "out.json",
        world.root / "source-snapshots" / "out.json",
        link_repo / "out.json",
        link_graph / "out.json",
        tmp_path / "sub" / ".." / "link-repo" / "out.json",
        world.repo,
        world.root,
        world.repo / ".atlas" / "out.json",
    ]
    before = inventory(world.repo, world.root)
    for target in inside:
        result = run_cli(world, manifest, artifact, target)
        assert result.exit_code == 2 and result.stdout_bytes == b"", target
        assert tail(result) == OUTSIDE, target
    # The default namespace: no --graph-root, output under <repo>/.atlas.
    result = run_cli(world, manifest, artifact, world.repo / ".atlas" / "out.json", graph=False)
    assert result.exit_code == 2 and tail(result) == OUTSIDE
    assert inventory(world.repo, world.root) == before
    swapped = type(world.repo)(str(world.repo).swapcase())
    if swapped != world.repo and swapped.exists():  # a case-insensitive file system
        result = run_cli(world, manifest, artifact, swapped / "out.json")
        assert result.exit_code == 2 and tail(result) == OUTSIDE
    good = tmp_path / "ok" / "out.json"
    good.parent.mkdir()
    assert run_cli(world, manifest, artifact, good).exit_code == 0 and good.exists()


def test_otel_output_must_differ_from_its_inputs(world, inputs, tmp_path):
    manifest, artifact = inputs
    alias = tmp_path / "alias.json"
    alias.symlink_to(artifact)
    for target in (
        artifact,
        manifest,
        alias,
        tmp_path / "inputs" / ".." / "inputs" / manifest.name,
    ):
        before = (artifact.read_bytes(), manifest.read_bytes())
        result = run_cli(world, manifest, artifact, target)
        assert result.exit_code == 2 and result.stdout_bytes == b"", target
        assert tail(result) == DIFFERENT, target
        assert (artifact.read_bytes(), manifest.read_bytes()) == before


def test_otel_write_failures_are_fixed_text_and_leave_nothing(world, inputs, tmp_path):
    manifest, artifact = inputs
    directory = tmp_path / "a-directory"
    directory.mkdir()
    for target in (tmp_path / "missing" / "out.json", directory):
        result = run_cli(world, manifest, artifact, target)
        assert result.exit_code == 1 and result.stdout_bytes == b""
        assert result.stderr == "Error: Runtime overlay cannot be written.\n"
        assert str(tmp_path) not in result.stderr
    assert not list(tmp_path.rglob(".atlas-runtime-overlay-*"))
    assert list(directory.iterdir()) == [] and not (tmp_path / "missing").exists()


def test_otel_output_is_replaced_atomically_and_kept_on_rejection(world, inputs, tmp_path):
    manifest, artifact = inputs
    output = tmp_path / "overlay.json"
    output.write_bytes(b"previous")
    result = run_cli(world, manifest, artifact, output)
    assert result.exit_code == 0 and result.stderr == ""
    data = output.read_bytes()
    assert data.endswith(b"\n") and data.startswith(b'{"artifact"')
    assert stat.S_IMODE(output.stat().st_mode) == 0o600  # owner only, as mkstemp creates it
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "inputs") == ["overlay.json"]
    bad_manifest, bad_artifact = write_inputs(tmp_path / "bad", b"x" + basic(), example())
    result = run_cli(world, bad_manifest, bad_artifact, output)
    assert result.exit_code == 1 and output.read_bytes() == data


def test_otel_write_atomic_cleans_up_after_a_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.json"

    def failing(_descriptor):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", failing)
    with pytest.raises(OSError):
        runtime.write_atomic(target, b"data")
    assert list(tmp_path.iterdir()) == []
    monkeypatch.undo()
    runtime.write_atomic(target, b"data")
    assert target.read_bytes() == b"data" and [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_otel_import_never_writes_the_graph(world, inputs, tmp_path):
    manifest, artifact = inputs
    mismatch_manifest, mismatch_artifact = write_inputs(
        tmp_path / "m", basic(), example(), stem="m"
    )
    rejected_manifest, rejected_artifact = write_inputs(
        tmp_path / "r", b"x" + basic(), example(), stem="r"
    )
    before = inventory(world.repo, world.root)
    identity = lease_identity(world)
    files = sorted(p.name for p in tmp_path.iterdir())
    for index, args in enumerate(
        ((manifest, artifact, 0), (mismatch_manifest, mismatch_artifact, 0))
    ):
        output = tmp_path / f"o{index}.json"
        result = run_cli(world, args[0], args[1], output)
        assert result.exit_code == args[2], result.stderr
        assert inventory(world.repo, world.root) == before
        assert lease_identity(world) == identity
    rejected = run_cli(world, rejected_manifest, rejected_artifact, tmp_path / "o2.json")
    assert rejected.exit_code == 1 and not (tmp_path / "o2.json").exists()
    assert inventory(world.repo, world.root) == before
    assert lease_identity(world) == identity
    assert not [name for name in before if name.endswith(("-wal", "-shm", "-journal"))]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*files, "o0.json", "o1.json"])


def test_otel_import_works_over_a_read_only_graph(tmp_path):
    local = make_world(tmp_path, name="ro")
    manifest, artifact = write_inputs(
        tmp_path / "in", basic(), raw_of(manifest_for(basic(), local.snapshot_id))
    )
    paths = [local.root, *local.root.rglob("*")]
    modes = {p: p.lstat().st_mode for p in paths if not p.is_symlink()}
    try:
        for path, mode in modes.items():
            path.chmod(mode & ~0o222)
        before = inventory(local.root)
        result = run_cli(local, manifest, artifact, tmp_path / "out.json")
        assert result.exit_code == 0, result.stderr
        assert inventory(local.root) == before
    finally:
        for path, mode in modes.items():
            path.chmod(mode)


def test_otel_inputs_are_read_without_blocking(world, inputs, tmp_path):
    manifest, artifact = inputs
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    cases = [
        (fifo, artifact, "OpenTelemetry manifest cannot be read."),
        (manifest, fifo, "OpenTelemetry artifact cannot be read."),
        (tmp_path, artifact, "OpenTelemetry manifest cannot be read."),
        (manifest, tmp_path, "OpenTelemetry artifact cannot be read."),
        (tmp_path / "absent", artifact, "OpenTelemetry manifest cannot be read."),
        (manifest, tmp_path / "absent", "OpenTelemetry artifact cannot be read."),
    ]
    for manifest_path, artifact_path, text in cases:
        result = run_cli(world, manifest_path, artifact_path, tmp_path / "out.json")
        assert result.exit_code == 1 and result.stdout_bytes == b"", text
        assert result.stderr == "Error: " + text + "\n"
        assert str(tmp_path) not in result.stderr
    assert not (tmp_path / "out.json").exists()
