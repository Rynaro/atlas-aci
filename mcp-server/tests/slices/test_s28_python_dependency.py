"""ACI-S28 acceptance: one uv-locked pure wheel as a standalone package snapshot (AC-001..007)."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import zipfile
from collections import Counter

import pytest
import tree_sitter_language_pack as pack
from test_s28_support import (
    FORKED,
    FX,
    GEN,
    LIMITS,
    PACKAGE,
    PURE,
    ROOT,
    SINGLE,
    SRC,
    WHEEL,
    WHEEL_SHA,
    assert_rejected_cli,
    cli_import,
    cli_run,
    crafted,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    imp,
    lock_for,
    main_snapshot,
    minimal_entries,
    reason,
    rejected,
    sentinel_output,
)

from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.dependencies import uv_lock as ul
from atlas_aci.storage import evidence as ev

pytestmark = pytest.mark.usefixtures("grammar")

REGISTRY = "https://pypi.org/simple"
COORDINATE = "pypi:s28-demo-pkg@1.2.0"
URL = GEN.HOST + PURE

EXPECTED_DECLARATIONS = sorted(
    [
        ("s28_demo_pkg.core", "class", "Widget", False),
        ("s28_demo_pkg.core", "method", "Widget.method", False),
        ("s28_demo_pkg.core", "async_method", "Widget.amethod", False),
        ("s28_demo_pkg.core", "method", "Widget.static", True),
        ("s28_demo_pkg.core", "method", "Widget.size", True),
        ("s28_demo_pkg.core", "method", "Widget.size", True),
        ("s28_demo_pkg.core", "class", "Widget.Config", False),
        ("s28_demo_pkg.core", "method", "Widget.Config.load", False),
        ("s28_demo_pkg.core", "method", "Widget.with_locals", False),
        ("s28_demo_pkg.core", "class", "Chain1", False),
        ("s28_demo_pkg.core", "class", "Chain1.Chain2", False),
        ("s28_demo_pkg.core", "class", "Chain1.Chain2.Chain3", False),
        ("s28_demo_pkg.core", "class", "Chain1.Chain2.Chain3.Chain4", False),
        ("s28_demo_pkg.core", "function", "make_widget", False),
        ("s28_demo_pkg.core", "async_function", "fetch", False),
        ("s28_demo_pkg.core", "function", "cached", True),
        ("s28_demo_pkg.core", "function", "outer", False),
        ("s28_demo_pkg.helpers", "function", "helper_one", False),
        ("s28_demo_pkg.helpers", "function", "helper_two", False),
        ("s28_demo_pkg.sub.mod", "function", "__getattr__", False),
        ("setup", "function", "main", False),
    ]
)
EXPECTED_DIAGNOSTICS = sorted(
    [
        ("s28_demo_pkg-1.2.0.data/scripts/tool.py", "data_directory_not_indexed"),
        ("s28_demo_pkg/_speed.cpython-312-x86_64-linux-gnu.so", "compiled_extension_not_indexed"),
        ("s28_demo_pkg/bad-name.py", "non_importable_module_path"),
        ("s28_demo_pkg/broken.py", "syntax_error"),
        ("s28_demo_pkg/core.py", "conditional_definition_not_indexed"),
        ("s28_demo_pkg/core.py", "conditional_import_not_indexed"),
        ("s28_demo_pkg/core.py", "nesting_limit_exceeded"),
        ("s28_demo_pkg/core.pyi", "stub_file_not_indexed"),
        ("s28_demo_pkg/data.json", "non_python_member_not_indexed"),
        ("s28_demo_pkg/latin.py", "non_utf8_source"),
        ("s28_demo_pkg/py.typed", "non_python_member_not_indexed"),
        ("s28_demo_pkg/sub/__init__.py", "star_import_not_expanded"),
        ("s28_demo_pkg/sub/mod.py", "dynamic_all_not_indexed"),
        ("s28_demo_pkg/sub/mod.py", "dynamic_module_getattr"),
    ]
)
EXPECTED_INCOMPLETE = {
    "s28_demo_pkg/__init__.py": False,
    "s28_demo_pkg/bad-name.py": True,
    "s28_demo_pkg/broken.py": True,
    "s28_demo_pkg/core.py": True,
    "s28_demo_pkg/helpers.py": False,
    "s28_demo_pkg/latin.py": True,
    "s28_demo_pkg/sub/__init__.py": True,
    "s28_demo_pkg/sub/mod.py": True,
    "setup.py": False,
}


def zipped(raw: bytes = WHEEL) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(raw))


def basis_id(snapshot: dict) -> str:
    keys = ("importer", "extractor", "lock", "environment", "package")
    basis = {key: snapshot[key] for key in keys}
    text = "atlas-python-package-snapshot.v1:" + ev.canonical(basis)
    return hashlib.sha256(text.encode()).hexdigest()


# ---- AC-001 ---------------------------------------------------------------------------------


def test_python_dependency_exact_coordinate(tmp_path):
    snapshot = main_snapshot()
    expected = {
        "ecosystem": "pypi",
        "name": "s28-demo-pkg",
        "version": "1.2.0",
        "coordinate": COORDINATE,
        "source": {"kind": "registry", "registry": REGISTRY},
        "artifact": {
            "filename": PURE,
            "sha256": WHEEL_SHA,
            "bytes": len(WHEEL),
            "url": URL,
            "digest_check": "equals_lock_entry",
        },
    }
    assert snapshot["package"] == expected
    assert hashlib.sha256((FX / PURE).read_bytes()).hexdigest() == WHEEL_SHA
    assert ("sha256:" + WHEEL_SHA) in SINGLE.decode()  # the lock entry holds this very digest
    assert snapshot["lock"] == {
        "profile": "atlas-uv-lock-profile.v1",
        "version": 1,
        "revision": 3,
        "sha256": hashlib.sha256(SINGLE).hexdigest(),
    }
    assert snapshot["dist_info"] == {
        "directory": "s28_demo_pkg-1.2.0.dist-info",
        "name": "S28.Demo_Pkg",
        "version": "1.2.0",
        "wheel_version": "1.0",
        "root_is_purelib": True,
        "tags": ["py3-none-any"],
    }
    assert snapshot["snapshot_id"] == basis_id(snapshot)
    assert snapshot["environment"] == {}
    for alias in ("S28.Demo_Pkg", "s28_demo-pkg", "S28-DEMO-PKG"):
        assert pw.canonical_bytes(imp(package=alias)) == pw.canonical_bytes(snapshot)
    # The forked lock selects the same coordinate when the environment says so (control).
    forked = imp(FORKED, env={"python_full_version": "3.12.4"})
    assert forked["package"] == snapshot["package"]
    assert forked["snapshot_id"] != snapshot["snapshot_id"]
    # The CLI reports the same coordinate and digest it wrote.
    out = tmp_path / "out.json"
    run = cli_import(tmp_path)
    assert run.exit_code == 0, run.stderr
    summary = json.loads(run.stdout_bytes)
    written = json.loads(out.read_bytes())
    assert summary["coordinate"] == written["package"]["coordinate"] == COORDINATE
    assert summary["artifact_sha256"] == written["package"]["artifact"]["sha256"] == WHEEL_SHA
    assert summary["snapshot_id"] == written["snapshot_id"] == snapshot["snapshot_id"]
    assert out.read_bytes() == pw.canonical_bytes(snapshot)


# ---- AC-002 ---------------------------------------------------------------------------------


def test_python_dependency_declarations_are_package_scoped():
    snapshot = main_snapshot()
    sid = snapshot["snapshot_id"]
    declarations = snapshot["declarations"]
    assert declarations
    assert (
        sorted((d["module"], d["kind"], d["qualified_name"], d["decorated"]) for d in declarations)
        == EXPECTED_DECLARATIONS
    )
    names = {d["name"] for d in declarations}
    assert not names & {"Phantom", "broken", "latin", "bad", "tool", "inner", "InnerClass"}
    assert not names & {"typing_only", "too_deep", "Chain5"}
    assert {d["member"] for d in declarations} == {
        "s28_demo_pkg/core.py",
        "s28_demo_pkg/helpers.py",
        "s28_demo_pkg/sub/mod.py",
        "setup.py",
    }
    archive = zipped()
    members = {m["path"]: m for m in snapshot["members"]}
    by_id = {d["id"]: d for d in declarations}
    for kind in ("modules", "declarations", "imports", "exports", "diagnostics"):
        records = snapshot[kind]
        assert records
        for record in records:
            data = archive.read(record["member"])
            assert record["package_snapshot_id"] == sid
            assert record["member_sha256"] == hashlib.sha256(data).hexdigest()
            assert record["member_sha256"] == members[record["member"]]["sha256"]
    for declaration in declarations:
        data = archive.read(declaration["member"])
        span = declaration["span"]
        text = data[span["start_byte"] : span["end_byte"]]
        prefix = {"class": "class ", "function": "def ", "method": "def "}.get(
            declaration["kind"], "async def "
        )
        assert text.startswith((prefix + declaration["name"]).encode())
        parent = declaration["parent_id"]
        if declaration["qualified_name"].count(".") == 0:
            assert parent is None
        else:
            head, _, _ = declaration["qualified_name"].rpartition(".")
            assert by_id[parent]["qualified_name"] == head
            assert by_id[parent]["kind"] == "class"
        assert declaration["symbol"] == declaration["module"] + "." + declaration["qualified_name"]
    exports = snapshot["exports"]
    assert [(e["member"], e["name"]) for e in exports] == [
        ("s28_demo_pkg/__init__.py", "Widget"),
        ("s28_demo_pkg/__init__.py", "make_widget"),
    ]
    init = archive.read("s28_demo_pkg/__init__.py")
    assert [init[e["span"]["start_byte"] : e["span"]["end_byte"]] for e in exports] == [
        b'"Widget"',
        b'"make_widget"',
    ]
    resolved = {
        (i["member"], i["level"], i["target"], i["name"]): i["resolved_module"]
        for i in snapshot["imports"]
    }
    assert resolved[("s28_demo_pkg/__init__.py", 1, "core", "Widget")] == "s28_demo_pkg.core"
    assert resolved[("s28_demo_pkg/__init__.py", 1, "", "helpers")] == "s28_demo_pkg"
    assert resolved[("s28_demo_pkg/sub/__init__.py", 2, "core", "Widget")] == "s28_demo_pkg.core"
    assert resolved[("s28_demo_pkg/sub/__init__.py", 3, "outside", "x")] is None
    assert resolved[("s28_demo_pkg/sub/__init__.py", 1, "mod", "*")] == "s28_demo_pkg.sub.mod"
    assert resolved[("s28_demo_pkg/core.py", 0, "os.path", None)] == "os.path"
    diagnostics = sorted((d["member"], d["code"]) for d in snapshot["diagnostics"])
    assert diagnostics == EXPECTED_DIAGNOSTICS
    assert snapshot["coverage"] == {
        "evidence_coverage": "declaration_only",
        "completeness": "incomplete",
        "call_binding": "not_claimed",
        "execution": "not_performed",
    }
    assert {m["member"]: m["incomplete"] for m in snapshot["modules"]} == EXPECTED_INCOMPLETE
    assert snapshot["counts"]["members"] == len(snapshot["members"]) == 17
    pw.validate_snapshot(snapshot)
    # Scoping: the same wheel under another registry is another package snapshot entirely.
    other = lock_for(WHEEL, PURE, source='registry = "https://pypi.example.org/simple"')
    scoped = imp(other)
    assert scoped["snapshot_id"] != sid
    assert not {r["id"] for r in scoped["declarations"]} & {r["id"] for r in declarations}
    keys = lambda snap: sorted(  # noqa: E731
        (d["member"], d["kind"], d["qualified_name"]) for d in snap["declarations"]
    )
    assert keys(scoped) == keys(snapshot)
    assert scoped["package"]["source"]["registry"] == "https://pypi.example.org/simple"


# ---- AC-003 ---------------------------------------------------------------------------------


class Counters:
    """Delegating counters on the archive reader and the grammar loader (nothing is mocked)."""

    def __init__(self, monkeypatch):
        counters = self
        self.zips = 0
        self.parsers = 0
        real_zip, real_get = zipfile.ZipFile, pack.get_parser

        class CountingZip(real_zip):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                counters.zips += 1
                super().__init__(*args, **kwargs)

        def counting_get(name):
            counters.parsers += 1
            return real_get(name)

        monkeypatch.setattr(zipfile, "ZipFile", CountingZip)
        monkeypatch.setattr(pack, "get_parser", counting_get)


def test_python_dependency_hash_mismatch_rejected(tmp_path, monkeypatch):
    counters = Counters(monkeypatch)
    flipped = bytearray(WHEEL)
    flipped[len(flipped) // 2] ^= 0x01
    other, _ = crafted(minimal_entries())
    wrong_hash = SINGLE.replace(WHEEL_SHA.encode(), (b"0" * 64)[:64])
    wrong_size = SINGLE.replace(b"size = " + str(len(WHEEL)).encode(), b"size = 1")
    assert wrong_hash != SINGLE and wrong_size != SINGLE
    cases = [
        ("flipped", SINGLE, bytes(flipped)),
        ("different wheel", SINGLE, other),
        ("different lock hash", wrong_hash, WHEEL),
        ("lock size off", wrong_size, WHEEL),
    ]
    for label, lock, wheel in cases:
        exc = rejected(lambda lock=lock, wheel=wheel: imp(lock, wheel=wheel))
        assert exc.reason == "artifact_digest_mismatch", label
        assert exc.coordinate is not None and exc.coordinate["coordinate"] == COORDINATE, label
        assert exc.coordinate["expected_artifacts"][0]["filename"] == PURE
    assert counters.zips == 0 and counters.parsers == 0
    # Liveness: the same counters do move for an accepted wheel.
    imp()
    assert counters.zips >= 1 and counters.parsers >= 1
    # CLI: exit 1, exact text, nothing on stdout, a pre-existing output untouched, no temp file.
    bad = tmp_path / "wheels" / PURE
    bad.parent.mkdir()
    bad.write_bytes(bytes(flipped))
    out, sentinel = sentinel_output(tmp_path)
    run = cli_import(tmp_path, wheels=[bad])
    assert_rejected_cli(
        run, "Python dependency rejected: artifact_digest_mismatch " + COORDINATE, out, sentinel
    )
    lock = tmp_path / "wrong.uv.lock"
    lock.write_bytes(wrong_hash)
    run = cli_import(tmp_path, lock=lock)
    assert_rejected_cli(
        run, "Python dependency rejected: artifact_digest_mismatch " + COORDINATE, out, sentinel
    )


# ---- AC-004 ---------------------------------------------------------------------------------


def variant(package, env=None, lock=FORKED):
    return rejected(lambda: imp(lock, package=package, env=env))


def test_python_dependency_ambiguous_variant_rejected(tmp_path):
    reads: list = []
    cases = [
        (PACKAGE, None, "environment_incomplete"),
        ("overlap", {"python_full_version": "3.12.4"}, "multiple_matching_variants"),
        ("soloforked", {"python_full_version": "3.12.4"}, "no_matching_variant"),
        ("extrapkg", None, "unsupported_marker"),
        ("unmarked", None, "unmarked_variant"),
    ]
    for package, env, detail in cases:
        exc = rejected(lambda p=package, e=env: imp(FORKED, package=p, env=e, reads=reads))
        assert exc.reason == "unresolved_lock_variant" and exc.detail == detail, (package, detail)
    assert reads == []  # no artifact reader was called for any of them
    assert set(ul.VARIANT_DETAILS) >= {d for _, _, d in cases}
    # Controls: the same lock does select exactly one variant when the environment decides.
    env = {"python_full_version": "3.9.1"}
    old = ul.select_package(FORKED, PACKAGE, env, LIMITS)
    assert old.version == "1.1.0" and old.coordinate_text == "pypi:s28-demo-pkg@1.1.0"
    new = ul.select_package(FORKED, PACKAGE, {"python_full_version": "3.12.4"}, LIMITS)
    assert new.version == "1.2.0"
    split = ul.select_package(FORKED, "splitpkg", {"sys_platform": "linux"}, LIMITS)
    assert split.version == "2.0.0"
    # CLI: the selection failure wins over an unreadable wheel path named like a locked file.
    out, sentinel = sentinel_output(tmp_path)
    ghost = tmp_path / PURE
    assert not ghost.exists()
    run = cli_import(tmp_path, lock=FX / "forked.uv.lock", wheels=[ghost])
    assert_rejected_cli(run, "Python dependency rejected: unresolved_lock_variant", out, sentinel)


# ---- AC-005 ---------------------------------------------------------------------------------


def run_module(tmp_path, seed: str, name: str, lock=None, wheel=None):
    """`python -m atlas_aci import-python-dependency` in a fresh interpreter."""
    env = dict(os.environ, PYTHONHASHSEED=seed)
    command = [
        sys.executable, "-m", "atlas_aci", "import-python-dependency",
        "--lock", str(lock or FX / "single.uv.lock"), "--package", PACKAGE,
        "--wheel", str(wheel or FX / PURE), "--output", str(tmp_path / name),
    ]  # fmt: skip
    done = subprocess.run(command, capture_output=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    return done.stdout, (tmp_path / name).read_bytes()


def test_python_dependency_reimport_is_idempotent(tmp_path):
    first, second = pw.canonical_bytes(imp()), pw.canonical_bytes(imp())
    assert first == second
    out1, out2 = tmp_path / "out1.json", tmp_path / "out2.json"
    runs = [cli_import(tmp_path, name="out1.json"), cli_import(tmp_path, name="out2.json")]
    assert [r.exit_code for r in runs] == [0, 0], runs[0].stderr
    assert out1.read_bytes() == out2.read_bytes() == first
    assert runs[0].stdout_bytes == runs[1].stdout_bytes
    rerun = cli_import(tmp_path, name="out1.json")  # over the previous output
    assert rerun.exit_code == 0 and out1.read_bytes() == first
    # The same inputs from other directories (no path enters the snapshot).
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    (moved / "uv.lock").write_bytes(SINGLE)
    (moved / PURE).write_bytes(WHEEL)
    run = cli_import(tmp_path, lock=moved / "uv.lock", wheels=[moved / PURE], name="moved.json")
    assert run.exit_code == 0 and (tmp_path / "moved.json").read_bytes() == first
    assert str(tmp_path).encode() not in first and b"elsewhere" not in first
    # --env order does not matter; an extra non-matching wheel only changes `wheels_ignored`.
    forked = FX / "forked.uv.lock"
    env_a = ["--env", "python_full_version=3.12.4", "--env", "sys_platform=linux"]
    env_b = ["--env", "sys_platform=linux", "--env", "python_full_version=3.12.4"]
    run_a = cli_import(tmp_path, lock=forked, extra=env_a, name="a.json")
    run_b = cli_import(tmp_path, lock=forked, extra=env_b, name="b.json")
    assert run_a.exit_code == run_b.exit_code == 0, run_a.stderr
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    junk = tmp_path / "other-1.0-py3-none-any.whl"
    junk.write_bytes(b"not a wheel")
    run_c = cli_import(tmp_path, wheels=[junk, FX / PURE], name="c.json")
    assert run_c.exit_code == 0 and (tmp_path / "c.json").read_bytes() == first
    summary_a, summary_c = json.loads(runs[0].stdout_bytes), json.loads(run_c.stdout_bytes)
    assert summary_a.pop("wheels_ignored") == 0 and summary_c.pop("wheels_ignored") == 1
    assert summary_a == summary_c
    # Different interpreter hash seeds, one process each.
    stdout_1, data_1 = run_module(tmp_path, "1", "seed1.json")
    stdout_2, data_2 = run_module(tmp_path, "2", "seed2.json")
    assert data_1 == data_2 == first and stdout_1 == stdout_2 == runs[0].stdout_bytes
    # The committed golden (produced in the Linux production image, compared here first by
    # extractor so extractor drift reads differently from fact drift).
    golden = json.loads((FX / "s28_demo_pkg-1.2.0.snapshot.json").read_bytes())
    current = json.loads(first)
    assert current["extractor"] == golden["extractor"]
    assert current["snapshot_id"] == golden["snapshot_id"]
    assert first == (FX / "s28_demo_pkg-1.2.0.snapshot.json").read_bytes()
    # Every id recomputes (the fact set is content addressed).
    sid = current["snapshot_id"]
    assert current["snapshot_id"] == basis_id(current)
    for module in current["modules"]:
        expected = ev.canonical(["atlas-pydep-module.v1", sid, module["member"]])
        assert module["id"] == hashlib.sha256(expected.encode()).hexdigest()
    pw.validate_snapshot(current)
    # Decided: the environment and the lock bytes are identity.
    base = json.loads(first)
    same_variant = imp(FORKED, env={"python_full_version": "3.12.5"})
    other_variant = imp(FORKED, env={"python_full_version": "3.12.4"})
    assert same_variant["package"] == other_variant["package"]
    assert same_variant["snapshot_id"] != other_variant["snapshot_id"]
    reserialised = SINGLE + b"\n# re-serialised\n"
    again = imp(reserialised)
    assert again["lock"]["sha256"] != base["lock"]["sha256"]
    assert again["snapshot_id"] != base["snapshot_id"]
    keys = lambda snap: sorted(  # noqa: E731
        (d["member"], d["kind"], d["qualified_name"]) for d in snap["declarations"]
    )
    assert keys(again) == keys(base)


# ---- AC-006 ---------------------------------------------------------------------------------


def test_python_dependency_missing_source_is_explicit(tmp_path, monkeypatch):
    exc = rejected(lambda: pw.import_dependency(SINGLE, PACKAGE, {}, [], LIMITS))
    assert exc.reason == "missing_dependency_source"
    assert exc.coordinate == {
        "ecosystem": "pypi",
        "name": "s28-demo-pkg",
        "version": "1.2.0",
        "coordinate": COORDINATE,
        "source": {"kind": "registry", "registry": REGISTRY},
        "expected_artifacts": [{"filename": PURE, "sha256": WHEEL_SHA}],
    }
    out, sentinel = sentinel_output(tmp_path)
    run = cli_run(
        "import-python-dependency", "--lock", FX / "single.uv.lock", "--package", PACKAGE,
        "--output", out,
    )  # fmt: skip
    assert_rejected_cli(
        run, "Python dependency rejected: missing_dependency_source " + COORDINATE, out, sentinel
    )
    # Non-matching supplies give the same reason and their readers are never called.
    reads: list = []
    others = [
        ("other-1.0.0-py3-none-any.whl", WHEEL),
        ("s28_demo_pkg-1.1.0-py3-none-any.whl", WHEEL),
        ("renamed_" + PURE, WHEEL),
        (GEN.PLATFORM, WHEEL),
    ]
    for name, data in others:
        assert reason(lambda n=name, d=data: imp(wheel=d, name=n, reads=reads)) == (
            "missing_dependency_source"
        ), name
    assert reads == []
    # FIFO and other non-matching paths are never opened by the CLI.
    fifo = tmp_path / "fifo-1.0-py3-none-any.whl"
    os.mkfifo(fifo)
    ghost = tmp_path / "ghost-1.0-py3-none-any.whl"
    opened: list = []
    real_open = os.open

    def recording(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    run = cli_run(
        "import-python-dependency", "--lock", FX / "single.uv.lock", "--package", PACKAGE,
        "--wheel", fifo, "--wheel", ghost, "--output", out,
    )  # fmt: skip
    assert_rejected_cli(
        run, "Python dependency rejected: missing_dependency_source " + COORDINATE, out, sentinel
    )
    assert opened and str(FX / "single.uv.lock") in opened  # the recorder is live
    assert str(fifo) not in opened and str(ghost) not in opened
    # The same basename twice is ambiguous, never a silent pick.
    twice = [(PURE, lambda: WHEEL), (PURE, lambda: WHEEL)]
    exc = rejected(lambda: pw.import_dependency(SINGLE, PACKAGE, {}, twice, LIMITS))
    assert exc.reason == "ambiguous_dependency_source" and exc.coordinate is not None
    # The repository's own lock: a locked coordinate that nobody supplied.
    real = (ROOT / "mcp-server/uv.lock").read_bytes()
    exc = rejected(lambda: pw.import_dependency(real, "packaging", {}, [], LIMITS))
    assert exc.reason == "missing_dependency_source"
    assert exc.coordinate is not None
    assert exc.coordinate["coordinate"] == "pypi:packaging@26.0"
    assert exc.coordinate["expected_artifacts"] == [
        {
            "filename": "packaging-26.0-py3-none-any.whl",
            "sha256": "b36f1fef9334a5588b4166f8bcd26a14e521f2b55e6b9de3aaa80d3ff7a37529",
        }
    ]


# ---- AC-007 ---------------------------------------------------------------------------------

AUDIT = textwrap.dedent(
    """
    import json, os, subprocess, sys
    payload = json.loads(sys.argv[1])
    sys.path[:] = payload["path"]
    # Pre-import everything a run needs: a lazy import inside the window would be an exec event.
    import atlas_aci.__main__ as entry
    import click, hashlib, io, re, stat, tempfile, tomllib, zipfile, zlib, socket
    import encodings.cp437  # zipfile decodes names without the UTF-8 flag through this codec
    import tree_sitter, tree_sitter_language_pack as pack
    from atlas_aci.dependencies import python_wheel, uv_lock
    cache = pack.cache_dir()
    before = sorted(os.listdir(cache))
    def run(args):
        try:
            entry.cli.main(args, standalone_mode=False)
            return 0
        except click.ClickException:
            return 1
    events = []
    live = [False]
    def hook(event, args):
        if live[0]:
            events.append([event, repr(args)[:300]])
    sys.addaudithook(hook)
    # Warm click with a run that stops at selection (no grammar, no output).
    run(["import-python-dependency", "--lock", payload["lock"], "--package", "nothing-here",
         "--output", payload["warm"]])
    args = ["import-python-dependency", "--lock", payload["lock"], "--package", payload["package"],
            "--wheel", payload["unmatched"], "--wheel", payload["fifo"],
            "--wheel", payload["wheel"], "--output", payload["output"]]
    live[0] = True
    code = run(args)
    live[0] = False
    after = sorted(os.listdir(cache))
    window = list(events)
    del events[:]
    live[0] = True  # liveness control: the hook must see harmless forbidden-class operations
    socket.socket().close()
    compile("1", "<control>", "eval")
    open(os.devnull).close()
    os.system(":")
    subprocess.Popen(["true"]).wait()
    live[0] = False
    print(json.dumps({"code": code, "window": window, "control": events,
                      "cache_same": before == after, "cache_files": len(after)}))
    """
)


def test_python_dependency_import_is_offline_nonexecuting(tmp_path):
    sentinel = tmp_path / "sentinel"
    env = dict(os.environ, S28_SENTINEL_PATH=str(sentinel))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    fifo = tmp_path / "fifo-1.0-py3-none-any.whl"
    os.mkfifo(fifo)
    unmatched = tmp_path / "other-1.0-py3-none-any.whl"
    unmatched.write_bytes(b"unmatched wheel bytes")
    lock = tmp_path / "uv.lock"
    lock.write_bytes(SINGLE)
    wheel = tmp_path / PURE
    wheel.write_bytes(WHEEL)
    payload = {
        "path": sys.path, "lock": str(lock), "package": PACKAGE, "wheel": str(wheel),
        "unmatched": str(unmatched), "fifo": str(fifo), "output": str(out_dir / "snap.json"),
        "warm": str(out_dir / "warm.json"),
    }  # fmt: skip
    script = tmp_path / "audit.py"
    script.write_text(AUDIT)
    done = subprocess.run(
        [sys.executable, str(script), json.dumps(payload)],
        capture_output=True, env=env, timeout=120, cwd=tmp_path,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    report = json.loads(done.stdout.decode().splitlines()[-1])
    window, control = report["window"], report["control"]
    assert report["code"] == 0 and window  # the run succeeded and the hook was live
    names = [name for name, _ in window]
    forbidden = ("exec", "compile", "os.system", "os.fork", "os.posix_spawn", "ctypes.")
    assert not [n for n in names if n in forbidden or n.startswith(forbidden)]
    assert not [n for n in names if n.startswith(("socket.", "subprocess.", "os.exec", "os.spawn"))]
    assert not [n for n in names if n.startswith(("import", "marshal", "pickle", "urllib"))]
    # Every `open` is the lock, the matched wheel, the output temp file, or an fd.
    temp_prefix = str(out_dir / ".atlas-python-dependency-")
    allowed = {str(lock), str(wheel)}
    opens = [args for name, args in window if name == "open"]
    assert opens
    for args in opens:
        first = args.split(",")[0].lstrip("(").strip("'\"")
        assert first in allowed or first.startswith(temp_prefix) or first.isdigit(), args
    assert any(str(lock) in args for args in opens) and any(str(wheel) in args for args in opens)
    joined = "\n".join(args for _, args in window)
    assert str(fifo) not in joined and str(unmatched) not in joined
    assert str(sentinel) not in joined
    # The control window proves the hook sees these classes of events (harmless operations).
    control_names = {name for name, _ in control}
    assert {"compile", "os.system", "subprocess.Popen", "open"} <= control_names
    assert any(name.startswith("socket.") for name in control_names)
    # The native grammar load emits no Python-visible event: prove the load by its output.
    written = json.loads((out_dir / "snap.json").read_bytes())
    assert written["counts"]["declarations"] == len(EXPECTED_DECLARATIONS) > 0
    assert report["cache_same"] is True and report["cache_files"] > 0
    assert not sentinel.exists()
    assert not (out_dir / "warm.json").exists()
    # Positive control: executing the very same member bytes DOES create the sentinel.
    member = zipped().read("s28_demo_pkg/__init__.py")
    ran = subprocess.run(
        [sys.executable, "-I", "-c", member.decode()], env=env, capture_output=True
    )
    assert ran.returncode != 0 and sentinel.exists()  # it writes the sentinel, then raises
    sentinel.unlink()


def test_python_dependency_in_process_spies_see_only_reads(tmp_path, monkeypatch):
    """Second check: a Python-level spy over the same CLI path (read-only opens only)."""
    calls: list = []
    names = [
        (subprocess, "Popen"), (os, "system"), (os, "fork"), (os, "execv"), (os, "execve"),
        (os, "posix_spawn"), (os, "spawnv"), (socket, "create_connection"),
    ]  # fmt: skip
    for module, attr in names:
        if hasattr(module, attr):
            monkeypatch.setattr(module, attr, _spy(calls, attr))
    monkeypatch.setattr(socket.socket, "connect", _spy(calls, "connect"))
    reads: list = []
    real_open = os.open

    def recording(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR) == 0:
            reads.append(str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    before = set(sys.modules)
    run = cli_import(tmp_path)
    assert run.exit_code == 0, run.stderr
    assert calls == []
    assert sorted(reads) == sorted([str(FX / "single.uv.lock"), str(FX / PURE)])
    new = {name.split(".")[0] for name in set(sys.modules) - before}
    assert not new & {"s28_demo_pkg", "setup"}
    # Liveness: each spy records when called directly (the spy would raise, never execute).
    for module, attr in [*names, (socket.socket, "connect")]:
        if hasattr(module, attr):
            with pytest.raises(AssertionError):
                getattr(module, attr)()
    assert {attr for attr, _ in calls} >= {"Popen", "system", "connect"}


def _spy(calls: list, label: str):
    def spy(*args, **kwargs):
        calls.append((label, args))
        raise AssertionError("forbidden call: " + label)

    return spy


EMPTY_CACHE = textwrap.dedent(
    """
    import json, os, sys, time
    payload = json.loads(sys.argv[1])
    sys.path[:] = payload["path"]
    import tree_sitter_language_pack as pack
    from atlas_aci.dependencies import python_wheel, uv_lock
    pack.configure(cache_dir=payload["cache"])
    started = time.monotonic()
    wheel = open(payload["wheel"], "rb").read()
    lock = open(payload["lock"], "rb").read()
    try:
        python_wheel.import_dependency(lock, "s28-demo-pkg", {}, [(payload["name"], lambda: wheel)],
                                       uv_lock.normalize_limits())
        reason = None
    except uv_lock.DependencyRejected as exc:
        reason = exc.reason
    listing = sorted(os.listdir(payload["cache"])) if os.path.isdir(payload["cache"]) else []
    print(json.dumps({"reason": reason, "listing": listing, "seconds": time.monotonic() - started,
                      "downloaded": pack.downloaded_languages()}))
    """
)


def test_python_dependency_empty_grammar_cache_never_downloads(tmp_path):
    cache = tmp_path / "empty-cache"
    payload = {
        "path": sys.path, "cache": str(cache), "wheel": str(FX / PURE),
        "lock": str(FX / "single.uv.lock"), "name": PURE,
    }  # fmt: skip
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", EMPTY_CACHE, json.dumps(payload)],
        capture_output=True, timeout=60, cwd=tmp_path,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    report = json.loads(done.stdout.decode().splitlines()[-1])
    assert report["reason"] == "python_parser_unavailable"
    assert report["listing"] == [] and report["downloaded"] == []
    assert not cache.exists() or list(cache.iterdir()) == []
    assert report["seconds"] < 5 and time.monotonic() - started < 30

    # The guard is the contract: any failure after it is the same closed reason.
    class Boom(Exception):
        pass

    real = pack.get_parser

    def explode(name):
        raise Boom

    pack.get_parser = explode
    try:
        assert reason(lambda: pw.python_parser()) == "python_parser_unavailable"
    finally:
        pack.get_parser = real
    parser, extractor = pw.python_parser()
    assert extractor["parser"] == "tree-sitter" and extractor["language"] == "python"
    assert extractor["abi_version"] == parser.language.abi_version


ALLOWED_IMPORTS = {
    "__future__", "hashlib", "io", "re", "stat", "zlib", "tomllib", "zipfile", "dataclasses",
    "typing", "collections.abc", "tree_sitter_language_pack", "atlas_aci.storage.evidence",
    "atlas_aci.dependencies.uv_lock", "importlib.metadata",
}  # fmt: skip
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "open"}
BANNED_ATTRIBUTES = {"extract", "extractall", "download", "configure"}


def scan(source: str) -> list[str]:
    """Imports outside the allowlist, banned calls and banned attributes of one source."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems += ["import " + a.name for a in node.names if a.name not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for alias in node.names:
                if base not in ALLOWED_IMPORTS and base + "." + alias.name not in ALLOWED_IMPORTS:
                    problems.append("from " + base + " import " + alias.name)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
                problems.append("call " + func.id)
        if isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRIBUTES:
            problems.append("attribute " + node.attr)
    return problems


def test_python_dependency_sources_are_statically_offline():
    files = sorted((SRC / "dependencies").glob("*.py"))
    assert [f.name for f in files] == ["__init__.py", "python_wheel.py", "uv_lock.py"]
    for file in files:
        assert scan(file.read_text()) == [], file.name
    planted = "import os\nimport socket\nopen('x')\nzf.extractall('.')\npack.download([])\n"
    assert set(scan(planted)) == {
        "import os", "import socket", "call open", "attribute extractall", "attribute download",
    }  # fmt: skip
    assert Counter(scan("from os import path\nexec('1')\n")) == {
        "from os import path": 1,
        "call exec": 1,
    }
