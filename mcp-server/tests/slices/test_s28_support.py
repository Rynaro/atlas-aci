"""ACI-S28 shared helpers: fixtures, the generator, and the library and CLI entry points."""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import tree_sitter_language_pack as pack
from click.testing import CliRunner

from atlas_aci.__main__ import cli
from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.dependencies import uv_lock as ul

ROOT = Path(__file__).resolve().parents[3]
FX = ROOT / "mcp-server/tests/fixtures/python_dependencies"
SRC = ROOT / "mcp-server/src/atlas_aci"
DOCS = ROOT / "docs/importers/python-dependencies.md"
PROVISION = (
    'python -c "from tree_sitter_language_pack import download, get_parser; '
    "download(['python']); get_parser('python')\""
)


def _load_generator():
    spec = importlib.util.spec_from_file_location("s28_generate", FX / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["s28_generate"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


GEN = _load_generator()
PURE = GEN.PURE
WHEEL = (FX / PURE).read_bytes()
SINGLE = (FX / "single.uv.lock").read_bytes()
FORKED = (FX / "forked.uv.lock").read_bytes()
WHEEL_SHA = hashlib.sha256(WHEEL).hexdigest()
LIMITS = ul.normalize_limits()
PACKAGE = "s28-demo-pkg"


@pytest.fixture(scope="session")
def grammar():
    """Provision the python grammar OUTSIDE every measured window; fail (never skip) without it."""
    try:
        pack.get_parser("python")
    except Exception as exc:
        pytest.fail(
            "the python grammar cannot be provisioned ("
            + type(exc).__name__
            + "); run once with network access: "
            + PROVISION,
            pytrace=False,
        )
    assert "python" in pack.downloaded_languages()
    return pack.cache_dir()


def supplied(wheel: bytes = WHEEL, name: str = PURE, reads: list | None = None):
    def read() -> bytes:
        if reads is not None:
            reads.append(name)
        return wheel

    return [(name, read)]


def imp(
    lock: bytes = SINGLE,
    package: str = PACKAGE,
    env: dict | None = None,
    wheel: bytes = WHEEL,
    name: str = PURE,
    limits: ul.Limits = LIMITS,
    reads: list | None = None,
):
    return pw.import_dependency(lock, package, env or {}, supplied(wheel, name, reads), limits)


@functools.cache
def _main_snapshot_bytes() -> bytes:
    return pw.canonical_bytes(imp())


def main_snapshot() -> dict:
    """A fresh copy of the main fixture snapshot (callers may mutate it)."""
    return json.loads(_main_snapshot_bytes())


def rejected(call) -> ul.DependencyRejected:
    try:
        call()
    except ul.DependencyRejected as exc:
        return exc
    raise AssertionError("expected a DependencyRejected")


def reason(call) -> str:
    return rejected(call).reason


def cli_run(*args):
    return CliRunner().invoke(cli, [str(a) for a in args])


def cli_import(tmp_path, lock=None, wheels=None, package=PACKAGE, extra=(), name="out.json"):
    lock = FX / "single.uv.lock" if lock is None else lock
    wheels = [FX / PURE] if wheels is None else wheels
    args = ["import-python-dependency", "--lock", lock, "--package", package]
    for wheel in wheels:
        args += ["--wheel", wheel]
    return cli_run(*args, *extra, "--output", tmp_path / name)


def crafted(entries, lock_name=PURE):
    """(wheel bytes, lock bytes) for an in-test archive pinned by its own digest."""
    wheel = GEN.build_zip(entries)
    return wheel, lock_for(wheel, lock_name)


def lock_for(wheel: bytes, filename: str = PURE, **block) -> bytes:
    line = GEN.wheel_line(filename, hashlib.sha256(wheel).hexdigest(), len(wheel))
    text = GEN.lock_document([GEN.package_block(PACKAGE, "1.2.0", wheels=(line,), **block)])
    return text.encode()


def good_entries(extra=(), skip=()):
    """The honest demo entries (as `Entry`), minus `skip` paths, plus `extra`."""
    entries = [
        GEN.Entry(path.encode(), data) for path, data in GEN.wheel_members() if path not in skip
    ]
    return [*entries, *extra]


def minimal_entries(py: bytes = b"class A:\n    pass\n", path="s28_demo_pkg/__init__.py"):
    """The smallest valid wheel: one source and the dist-info."""
    dist = GEN.DIST
    return [
        GEN.Entry(path.encode(), py),
        GEN.Entry((dist + "/METADATA").encode(), GEN.METADATA),
        GEN.Entry((dist + "/WHEEL").encode(), GEN.WHEEL),
    ]


def assert_rejected_cli(result, text: str, out: Path, sentinel: bytes) -> None:
    """Exit 1, the exact fixed text, empty stdout, the pre-existing output untouched, no temp."""
    assert result.exit_code == 1, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr == "Error: " + text + "\n"
    assert out.read_bytes() == sentinel
    leftovers = [p.name for p in out.parent.iterdir() if p.name.startswith(".atlas-python")]
    assert leftovers == []


def sentinel_output(tmp_path: Path, name: str = "out.json") -> tuple[Path, bytes]:
    """An existing `--output` holding sentinel bytes a rejection must leave byte-unchanged."""
    out = tmp_path / name
    sentinel = b"SENTINEL-" + name.encode() + b"\x00\xff"
    out.write_bytes(sentinel)
    return out, sentinel
