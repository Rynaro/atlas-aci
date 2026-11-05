"""ACI-S28 CLI: usage errors, fixed texts, atomic output and the rejection contract."""

from __future__ import annotations

import json
import os

import pytest
from test_s28_support import (
    FX,
    GEN,
    PURE,
    WHEEL,
    assert_rejected_cli,
    cli,
    cli_import,
    cli_run,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    lock_for,
    minimal_entries,
    sentinel_output,
)

from atlas_aci.dependencies import python_wheel as pw

pytestmark = pytest.mark.usefixtures("grammar")

LOCK = FX / "single.uv.lock"
COORDINATE = "pypi:s28-demo-pkg@1.2.0"
REJECTED = "Python dependency rejected: "


def usage(result, text: str) -> None:
    assert result.exit_code == 2, (result.exit_code, result.stderr)
    assert result.stdout_bytes == b""
    assert result.stderr.rstrip("\n").endswith("Error: " + text), result.stderr


def listing(path) -> list[str]:
    return sorted(p.name for p in path.iterdir())


def test_cli_success_writes_exactly_one_new_file(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    run = cli_run(
        "import-python-dependency", "--lock", LOCK, "--package", "s28-demo-pkg",
        "--wheel", FX / PURE, "--output", out_dir / "snap.json",
    )  # fmt: skip
    assert run.exit_code == 0, run.stderr
    assert listing(out_dir) == ["snap.json"]
    assert run.stderr == ""
    summary = json.loads(run.stdout_bytes)
    assert list(summary) == sorted(summary)  # canonical key order
    assert summary["format"] == "atlas-python-dependency-import-summary.v1"
    assert summary["wheels_ignored"] == 0 and summary["coordinate"] == COORDINATE
    assert run.stdout_bytes == pw.canonical_bytes(summary)
    written = (out_dir / "snap.json").read_bytes()
    assert written.endswith(b"}\n") and json.loads(written)["counts"] == summary["counts"]
    assert "import-python-dependency" in cli.commands


def test_cli_usage_errors_exit_2_and_create_nothing(tmp_path):
    base = ["import-python-dependency", "--lock", LOCK, "--package", "s28-demo-pkg"]
    out = tmp_path / "out.json"
    for flag, bound in (
        ("max-lock-bytes", (0, 268435457)),
        ("max-wheel-bytes", (0, 268435457)),
        ("max-members", (0, 100001)),
        ("max-total-uncompressed-bytes", (0, 1073741825)),
        ("max-member-bytes", (0, 268435457)),
        ("max-records", (0, 1000001)),
        ("max-compression-ratio", (0, 10001)),
    ):
        for value in bound:
            run = cli_run(*base, "--wheel", FX / PURE, "--" + flag, value, "--output", out)
            usage(run, "Invalid Python dependency limit: " + flag.replace("-", "_"))
        run = cli_run(*base, "--" + flag, "many", "--output", out)  # not an int at all
        assert run.exit_code == 2 and run.stdout_bytes == b""
    for bad in ("x=1", "sys_platform", "python_full_version=3", "os_name=a b", "os_name="):
        usage(cli_run(*base, "--env", bad, "--output", out), "Invalid --env value.")
    usage(
        cli_run(*base, "--env", "os_name=a", "--env", "os_name=b", "--output", out),
        "Invalid --env value.",
    )
    for bad in ("", "-x", "a b", "a/b", "café"):
        run = cli_run("import-python-dependency", "--lock", LOCK, "--package", bad, "--output", out)
        usage(run, "Invalid --package name.")
    run = cli_run("import-python-dependency", "--lock", LOCK, "--output", out)  # package missing
    assert run.exit_code == 2
    assert listing(tmp_path) == []


def test_cli_output_must_differ_from_every_input(tmp_path):
    lock, wheel = tmp_path / "uv.lock", tmp_path / PURE
    lock.write_bytes(LOCK.read_bytes())
    wheel.write_bytes(WHEEL)
    text = "--output must differ from the lock and every wheel."
    (tmp_path / "sub").mkdir()
    link, hard = tmp_path / "link.json", tmp_path / "hard.json"
    link.symlink_to(lock)
    os.link(wheel, hard)
    spellings = {
        "same path as the lock": lock,
        "same path as the wheel": wheel,
        "dot-dot spelling": tmp_path / "sub" / ".." / "uv.lock",
        "symlink to the lock": link,
        "hardlink of the wheel": hard,
    }
    for label, output in spellings.items():
        assert label
        run = cli_run(
            "import-python-dependency", "--lock", lock, "--package", "s28-demo-pkg",
            "--wheel", wheel, "--output", output,
        )  # fmt: skip
        usage(run, text)
    assert lock.read_bytes() == LOCK.read_bytes() and wheel.read_bytes() == WHEEL
    # An output equal to a NON-matching wheel is refused too (it would be overwritten).
    junk = tmp_path / "junk-1.0-py3-none-any.whl"
    junk.write_bytes(b"x")
    run = cli_run(
        "import-python-dependency", "--lock", lock, "--package", "s28-demo-pkg",
        "--wheel", junk, "--wheel", wheel, "--output", junk,
    )  # fmt: skip
    usage(run, text)
    assert junk.read_bytes() == b"x"
    # The same NON-EXISTENT path for the lock and the output is a usage error, not a read error.
    absent = tmp_path / "absent.lock"
    run = cli_run(
        "import-python-dependency", "--lock", absent, "--package", "s28-demo-pkg",
        "--wheel", wheel, "--output", absent,
    )  # fmt: skip
    usage(run, text)
    assert not absent.exists()


def test_cli_relative_output_and_symlinked_parent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = cli_run(
        "import-python-dependency", "--lock", LOCK, "--package", "s28-demo-pkg",
        "--wheel", FX / PURE, "--output", "rel.json",
    )  # fmt: skip
    assert run.exit_code == 0, run.stderr
    assert (tmp_path / "rel.json").read_bytes() == pw.canonical_bytes(
        json.loads((tmp_path / "rel.json").read_bytes())
    )
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real)
    run = cli_import(tmp_path, name="alias/via-link.json")
    assert run.exit_code == 0 and (real / "via-link.json").exists()
    assert [n for n in listing(real) if n.startswith(".atlas")] == []


def test_cli_write_failures_are_fixed_text_and_leave_no_temp(tmp_path):
    text = "Python dependency snapshot cannot be written."
    missing = tmp_path / "no-such-dir" / "out.json"
    run = cli_run(
        "import-python-dependency", "--lock", LOCK, "--package", "s28-demo-pkg",
        "--wheel", FX / PURE, "--output", missing,
    )  # fmt: skip
    assert run.exit_code == 1 and run.stdout_bytes == b"" and run.stderr == "Error: " + text + "\n"
    assert listing(tmp_path) == []
    folder = tmp_path / "taken"
    folder.mkdir()
    (folder / "keep").write_bytes(b"k")
    run = cli_import(tmp_path, name="taken")
    assert run.exit_code == 1 and run.stdout_bytes == b"" and run.stderr == "Error: " + text + "\n"
    assert listing(tmp_path) == ["taken"] and listing(folder) == ["keep"]


def test_cli_unreadable_inputs_use_fixed_texts(tmp_path):
    out, sentinel = sentinel_output(tmp_path)
    ghost = tmp_path / "ghost.lock"
    run = cli_import(tmp_path, lock=ghost)
    assert_rejected_cli(run, "uv lock file cannot be read.", out, sentinel)
    assert str(ghost) not in run.stderr
    fifo = tmp_path / "lock.fifo"
    os.mkfifo(fifo)
    run = cli_import(tmp_path, lock=fifo)  # opened without blocking, then refused
    assert_rejected_cli(run, "uv lock file cannot be read.", out, sentinel)
    folder = tmp_path / "dir.lock"
    folder.mkdir()
    assert_rejected_cli(
        cli_import(tmp_path, lock=folder), "uv lock file cannot be read.", out, sentinel
    )
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    wheel = wheel_dir / PURE
    for label in ("missing", "fifo", "directory"):
        if label == "fifo":
            os.mkfifo(wheel)
        elif label == "directory":
            wheel.unlink()
            wheel.mkdir()
        run = cli_import(tmp_path, wheels=[wheel])
        assert_rejected_cli(run, "Wheel file cannot be read.", out, sentinel)
        assert str(wheel) not in run.stderr


def build_case(tmp_path, tag, entries):
    """A lock and a wheel (named like the locked one) in their own directory for `entries`."""
    folder = tmp_path / "cases" / tag
    folder.mkdir(parents=True)
    data = GEN.build_zip(entries)
    (folder / PURE).write_bytes(data)
    (folder / "uv.lock").write_bytes(lock_for(data, PURE))
    return folder / "uv.lock", folder / PURE


def test_cli_every_rejection_is_one_fixed_line(tmp_path):
    out, sentinel = sentinel_output(tmp_path)
    forked = FX / "forked.uv.lock"
    bad_lock = tmp_path / "bad.lock"
    bad_lock.write_bytes(b"not toml = = =")
    profile = tmp_path / "profile.lock"
    profile.write_bytes(LOCK.read_bytes().replace(b"revision = 3", b"revision = 2"))
    garbage_lock, garbage_wheel = tmp_path / "g.lock", tmp_path / "g" / PURE
    garbage_wheel.parent.mkdir()
    garbage_wheel.write_bytes(b"not a zip")
    garbage_lock.write_bytes(lock_for(b"not a zip"))
    base = minimal_entries()
    dist = GEN.DIST
    unsafe = build_case(tmp_path, "unsafe", [*base, GEN.Entry(b"../x.py", b"x")])
    impure_wheel = GEN.WHEEL.replace(b"true", b"false")
    impure = build_case(
        tmp_path, "impure", [base[0], base[1], GEN.Entry((dist + "/WHEEL").encode(), impure_wheel)]
    )
    wrong_name = GEN.Entry((dist + "/METADATA").encode(), b"Name: x\n")
    mismatch = build_case(tmp_path, "mismatch", [base[0], wrong_name, base[2]])
    wheel = FX / PURE
    cases = [
        ("lock_too_large", {"lock": LOCK, "extra": ("--max-lock-bytes", 5)}),
        ("lock_invalid", {"lock": bad_lock}),
        ("unsupported_lock_profile", {"lock": profile}),
        ("package_not_in_lock", {"package": "nothing-here"}),
        ("unresolved_lock_variant", {"lock": forked}),
        ("unsupported_source_kind pypi:gitdep@0.1.0", {"package": "gitdep"}),
        ("no_locked_pure_wheel pypi:sdistonly@1.0.0", {"package": "sdistonly"}),
        ("missing_dependency_source " + COORDINATE, {"wheels": []}),
        ("ambiguous_dependency_source " + COORDINATE, {"wheels": [wheel, tmp_path / "x" / PURE]}),
        ("wheel_too_large " + COORDINATE, {"extra": ("--max-wheel-bytes", 5)}),
        ("invalid_wheel " + COORDINATE, {"lock": garbage_lock, "wheels": [garbage_wheel]}),
        ("unsafe_archive_member " + COORDINATE, {"lock": unsafe[0], "wheels": [unsafe[1]]}),
        ("archive_limit_exceeded " + COORDINATE, {"extra": ("--max-members", 1)}),
        ("wheel_metadata_mismatch " + COORDINATE, {"lock": mismatch[0], "wheels": [mismatch[1]]}),
        ("not_pure_python " + COORDINATE, {"lock": impure[0], "wheels": [impure[1]]}),
        ("surface_limit_exceeded " + COORDINATE, {"extra": ("--max-records", 1)}),
    ]
    for text, options in cases:
        run = cli_import(
            tmp_path,
            lock=options.get("lock"),
            wheels=options.get("wheels"),
            package=options.get("package", "s28-demo-pkg"),
            extra=options.get("extra", ()),
        )
        assert_rejected_cli(run, REJECTED + text, out, sentinel)
        assert "Traceback" not in run.stderr and str(tmp_path) not in run.stderr
    # The same ambiguity when the two supplied files are really present.
    other = tmp_path / "x"
    other.mkdir()
    (other / PURE).write_bytes(WHEEL)
    run = cli_import(tmp_path, wheels=[wheel, other / PURE])
    assert_rejected_cli(run, REJECTED + "ambiguous_dependency_source " + COORDINATE, out, sentinel)


def test_cli_unexpected_failure_names_only_the_type(tmp_path, monkeypatch):
    out, sentinel = sentinel_output(tmp_path)

    def explode(*args, **kwargs):
        raise RuntimeError("secret " + str(tmp_path))

    monkeypatch.setattr(pw, "build_snapshot", explode)
    run = cli_import(tmp_path)
    assert_rejected_cli(run, "Python dependency import failed: RuntimeError", out, sentinel)
    assert "secret" not in run.stderr


def test_cli_help_documents_the_contract():
    run = cli_run("import-python-dependency", "--help")
    assert run.exit_code == 0
    for option in (
        "--lock",
        "--package",
        "--env",
        "--wheel",
        "--output",
        "--max-compression-ratio",
    ):
        assert option in run.stdout
    assert "nothing is installed" in run.stdout.replace("\n", " ")
