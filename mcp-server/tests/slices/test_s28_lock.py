"""ACI-S28 uv.lock reading: profile, pre-scan, variant selection, sources and locked wheels."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from test_s28_support import (
    FORKED,
    GEN,
    LIMITS,
    PACKAGE,
    PURE,
    ROOT,
    SINGLE,
    WHEEL_SHA,
    imp,
    reason,
    rejected,
)

from atlas_aci.dependencies import python_wheel as pw
from atlas_aci.dependencies import uv_lock as ul

REAL_LOCK = (ROOT / "mcp-server/uv.lock").read_bytes()
ENV = {"python_full_version": "3.12.4", "sys_platform": "linux"}
GOOD = GEN.pure_line("demo", "1.0.0")


def block(name="demo", version="1.0.0", **kwargs) -> str:
    kwargs.setdefault("wheels", (GEN.pure_line(name, version),))
    return GEN.package_block(name, version, **kwargs)


def doc(*blocks: str, **kwargs) -> bytes:
    return GEN.lock_document(list(blocks), **kwargs).encode()


def select(lock: bytes, package="demo", env=None, limits=LIMITS):
    return ul.select_package(lock, package, env or {}, limits)


def why(lock: bytes, package="demo", env=None, limits=LIMITS) -> str:
    return rejected(lambda: select(lock, package, env, limits)).reason


# ---- the pre-scan and the profile -----------------------------------------------------------


def test_lock_prescan_rejects_every_interpreter_dependent_construct():
    base = doc(block()).decode()
    assert select(base.encode()).version == "1.0.0"  # control: the same document is accepted
    nested = "x = " + "[" * 16 + "]" * 16 + "\n"
    assert select((nested + base).encode()).version == "1.0.0"  # depth 16 is accepted
    cases = {
        "bom": b"\xef\xbb\xbf" + base.encode(),
        "not utf-8": b"# caf\xe9\n" + base.encode(),
        "depth 17": ("x = " + "[" * 17 + "]" * 17 + "\n" + base).encode(),
        "depth 100000": ("x = " + "[" * 100000 + "]" * 100000 + "\n" + base).encode(),
        "lf in inline table": (base + "y = {a = 1,\n b = 2}\n").encode(),
        "escape e": (base + 'y = "\\e"\n').encode(),
        "escape x": (base + 'y = "\\x41"\n').encode(),
        "triple backslash": (base + 'y = "\\\\\\e"\n').encode(),
        "digit run": (base + "y = " + "1" * 200 + "\n").encode(),
        "underscore run": (base + "y = 1" + "_1" * 100 + "\n").encode(),
        "float": (base + "y = 1.5\n").encode(),
        "version true": doc(block()).replace(b"version = 1", b"version = true", 1),
        "no package": b"version = 1\nrevision = 3\n",
        "package not a list": b"version = 1\nrevision = 3\npackage = 1\n",
        "non-table entry": b"version = 1\nrevision = 3\npackage = [1]\n",
        "name not a string": b"version = 1\nrevision = 3\n[[package]]\nname = 3\n",
        "toml syntax": (base + "y = = 1\n").encode(),
        "revision a string": doc(block()).replace(b"revision = 3", b'revision = "3"'),
    }
    for label, lock in cases.items():
        assert why(lock) == "lock_invalid", label
    # An escaped backslash followed by a letter is NOT an escape: an even run is accepted.
    even = (base + 'y = "\\\\e and \\\\x"\n').encode()
    assert select(even).version == "1.0.0"


def test_lock_digit_run_is_independent_of_the_integer_digit_limit():
    lock = (doc(block()).decode() + "y = " + "7" * 5000 + "\n").encode()
    code = (
        "import sys; sys.path[:] = " + repr(sys.path) + "\n"
        "from atlas_aci.dependencies import uv_lock as ul\n"
        "try:\n"
        "    ul.select_package(sys.stdin.buffer.read(), 'demo', {}, ul.normalize_limits())\n"
        "except ul.DependencyRejected as exc:\n"
        "    print(exc.reason)\n"
    )
    for digits in ("0", "4300"):
        env = dict(os.environ, PYTHONINTMAXSTRDIGITS=digits)
        done = subprocess.run(
            [sys.executable, "-c", code], input=lock, capture_output=True, env=env, timeout=60
        )
        assert done.stdout.decode().strip() == "lock_invalid", (digits, done.stderr)


def test_lock_size_limit_and_profile():
    small = ul.normalize_limits({"max_lock_bytes": 10})
    assert why(SINGLE, PACKAGE, None, small) == "lock_too_large"
    exact = ul.normalize_limits({"max_lock_bytes": len(SINGLE)})
    assert select(SINGLE, PACKAGE, None, exact).name == PACKAGE
    for kwargs in ({"revision": 2}, {"revision": 4}, {"version": 2}, {"revision": 0}):
        assert why(doc(block(), **kwargs)) == "unsupported_lock_profile", kwargs
    assert why(doc(block(), revision=None)) == "unsupported_lock_profile"  # uv omits revision 0
    assert why(doc(block()).replace(b"version = 1\n", b"", 1)) == "lock_invalid"
    assert why(doc(block()), "absent") == "package_not_in_lock"
    assert ul.LOCK_PROFILE == "atlas-uv-lock-profile.v1"


def test_lock_real_repository_lock_is_in_profile():
    selected = select(REAL_LOCK, "packaging")
    assert (selected.name, selected.version) == ("packaging", "26.0")
    assert selected.registry == "https://pypi.org/simple"
    assert [(a.filename, a.size) for a in selected.artifacts] == [
        ("packaging-26.0-py3-none-any.whl", 74366)
    ]
    exc = rejected(lambda: select(REAL_LOCK, "atlas-aci"))
    assert exc.reason == "unsupported_source_kind"
    assert exc.coordinate is not None and exc.coordinate["coordinate"] == "pypi:atlas-aci@3.0.1"
    exc = rejected(lambda: select(REAL_LOCK, "tree-sitter-language-pack"))
    assert exc.reason == "no_locked_pure_wheel"  # every wheel of it is platform specific
    assert exc.coordinate is not None and exc.coordinate["expected_artifacts"] == []
    assert why(REAL_LOCK, "Not_There") == "package_not_in_lock"


# ---- source kinds and the locked pure-wheel set ---------------------------------------------


def test_lock_unsupported_source_kinds_name_the_coordinate():
    for kind in ("dirdep", "gitdep", "pathdep", "urldep", "virtualdep", "localreg", "s28-root"):
        exc = rejected(lambda k=kind: select(SINGLE, k))
        expected = "unsupported_source_kind"
        assert exc.reason == expected, kind
        assert exc.coordinate is not None and exc.coordinate["coordinate"].startswith(
            "pypi:" + kind
        )
    for source in ('registry = "ftp://example.com/simple"', 'registry = 5', 'foo = "x"',
                   'registry = "https://a.example/s", extra = "x"'):  # fmt: skip
        assert why(doc(block(source=source))) == "unsupported_source_kind", source
    assert select(doc(block(source='registry = "http://localhost:3141/simple"'))).registry == (
        "http://localhost:3141/simple"
    )
    assert why(doc(GEN.package_block("demo", "1.0.0"))) == "no_locked_pure_wheel"


def test_lock_pure_wheel_set_is_exactly_the_locked_pure_wheels():
    pure = GEN.pure_line("demo", "1.0.0")
    cases = {
        "sdist only": block(wheels=(), sdist=GEN.sdist_line("demo-1.0.0.tar.gz")),
        "platform only": block(
            wheels=(
                GEN.wheel_line("demo-1.0.0-cp312-cp312-manylinux_2_17_x86_64.whl", "ab" * 32, 1),
            )
        ),
        "abi3 any": block(wheels=(GEN.wheel_line("demo-1.0.0-cp312-abi3-any.whl", "ab" * 32, 1),)),
        "md5": block(wheels=(pure.replace("sha256:", "md5:"),)),
        "uppercase hex": block(
            wheels=(GEN.wheel_line("demo-1.0.0-py3-none-any.whl", "AB" * 32, 100),)
        ),
        "other dist": block(wheels=(GEN.pure_line("other", "1.0.0"),)),
        "other version": block(wheels=(GEN.pure_line("demo", "1.0.1"),)),
        "query in url": block(wheels=(pure.replace("-any.whl", "-any.whl?x=1"),)),
        "not a wheel name": block(wheels=(pure.replace("-py3-none-any", ""),)),
        "size not an int": block(wheels=(pure.replace("size = 100", 'size = "100"'),)),
        "no hash": block(wheels=(pure.replace("hash", "digest"),)),
    }
    for label, text in cases.items():
        exc = rejected(lambda t=text: select(doc(t)))
        assert exc.reason == "no_locked_pure_wheel", label
        assert exc.coordinate is not None and exc.coordinate["expected_artifacts"] == [], label
    kept = select(doc(block()))
    assert [a.filename for a in kept.artifacts] == ["demo-1.0.0-py3-none-any.whl"]
    assert kept.coordinate()["expected_artifacts"] == [
        {"filename": "demo-1.0.0-py3-none-any.whl", "sha256": kept.artifacts[0].sha256}
    ]
    two = block(wheels=(pure, pure))  # the same filename twice is not a lock uv writes
    assert why(doc(two)) == "lock_invalid"
    assert why(doc(block(wheels=("not a table",)))) == "lock_invalid"
    mixed = block(wheels=(pure, GEN.wheel_line("demo-1.0.0-py2.py3-none-any.whl", "cd" * 32, 5)))
    assert [a.filename for a in select(doc(mixed)).artifacts] == [
        "demo-1.0.0-py2.py3-none-any.whl",
        "demo-1.0.0-py3-none-any.whl",
    ]


def test_lock_version_strings_are_compared_exactly():
    """Limitation: no PEP 440 normalisation, so an unnormalised filename version is excluded."""
    lock = doc(GEN.package_block("demo", "1.0", wheels=(GEN.pure_line("demo", "1.0.0"),)))
    assert why(lock) == "no_locked_pure_wheel"
    escaped = doc(GEN.package_block("demo", "1.0.0-1", wheels=(GEN.pure_line("demo", "1.0.0_1"),)))
    assert select(escaped).version == "1.0.0-1"  # `-` is written `_` in a filename
    assert why(doc(GEN.package_block("demo", "bad version", wheels=(GOOD,)))) == "lock_invalid"
    assert why(doc(GEN.package_block("demo", "x1", wheels=(GOOD,)))) == "lock_invalid"
    assert "version_strings_compared_exactly_not_normalized" in pw.LIMITATIONS


# ---- variants -------------------------------------------------------------------------------


def test_lock_variant_selection_table():
    ge, lt = "python_full_version >= '3.12'", "python_full_version < '3.12'"
    a, b = block(version="1.0.0", markers=(lt,)), block(version="2.0.0", markers=(ge,))
    assert select(doc(a, b), env=ENV).version == "2.0.0"
    assert select(doc(a, b), env={"python_full_version": "3.11.9"}).version == "1.0.0"
    one = block(markers=(lt, ge))
    assert select(doc(one), env=ENV).version == "1.0.0"  # a single entry: markers are OR-ed
    cases = [
        (doc(a, b), None, "environment_incomplete"),
        (doc(block(markers=(lt,))), ENV, "no_matching_variant"),
        (doc(a, block(version="3.0.0"), b), ENV, "unmarked_variant"),
        (doc(block(version="1.0.0"), block(version="2.0.0")), ENV, "unmarked_variant"),
        (doc(block(markers=("extra == 'x'",))), ENV, "unsupported_marker"),
        (doc(block(markers=("not python_version < '3.9'",))), ENV, "unsupported_marker"),
        (doc(block(markers=("python_version ~= '3.9'",))), ENV, "unsupported_marker"),
        (doc(block(markers=("'3.9' <= python_version",))), ENV, "unsupported_marker"),
        (doc(block(markers=("python_version >= '3.9'" + " and python_version < '4'" * 60,))),
         ENV, "marker_limit_exceeded"),
        (doc(block(markers=("(" * 9 + "python_version >= '3.9'" + ")" * 9,))), ENV,
         "marker_limit_exceeded"),
        (doc(a, block(version="2.0.0", markers=(ge, "extra == 'x'"))), ENV, "unsupported_marker"),
    ]  # fmt: skip
    for lock, env, detail in cases:
        exc = rejected(lambda lock=lock, env=env: select(lock, env=env))
        assert exc.reason == "unresolved_lock_variant" and exc.detail == detail, detail
        assert exc.coordinate is None  # nothing is selected yet
    both = (
        block(version="1.0.0", markers=("sys_platform == 'linux'",))
        + "\n"
        + block(version="2.0.0", markers=("os_name != 'nt'",), source='git = "https://x.example/r"')
    )
    exc = rejected(lambda: select(doc(both), env={"sys_platform": "linux", "os_name": "posix"}))
    assert exc.detail == "multiple_matching_variants"  # a registry and a git candidate both match
    only_git = block(version="2.0.0", markers=("os_name != 'nt'",), source='git = "https://x/r"')
    other = block(version="1.0.0", markers=("sys_platform == 'win32'",))
    exc = rejected(
        lambda: select(doc(other, only_git), env={"sys_platform": "linux", "os_name": "a"})
    )
    assert exc.reason == "unsupported_source_kind"  # the selected variant has no supported source
    assert exc.coordinate is not None and exc.coordinate["coordinate"] == "pypi:demo@2.0.0"
    bad = block(markers=())
    assert select(doc(bad)).version == "1.0.0"
    assert why(
        doc(block().replace('version = "1.0.0"', 'version = "1.0.0"\nresolution-markers = []'))
    ) == ("lock_invalid")
    assert why(doc(block().replace("source =", "resolution-markers = [1]\nsource ="))) == (
        "lock_invalid"
    )
    many = tuple(f"os_name == 'n{i}'" for i in range(65))
    assert why(doc(block(markers=many))) == "lock_invalid"
    assert set(ul.VARIANT_DETAILS) == {
        "environment_incomplete", "marker_limit_exceeded", "multiple_matching_variants",
        "no_matching_variant", "unmarked_variant", "unsupported_marker",
    }  # fmt: skip


MARKERS = [
    ("python_full_version >= '3.12'", {"python_full_version": "3.12.0"}, True),
    ("python_full_version >= '3.12'", {"python_full_version": "3.11.9"}, False),
    ("python_full_version == '3.12'", {"python_full_version": "3.12.4"}, False),
    ("python_full_version == '3.12.4'", {"python_full_version": "3.12.4"}, True),
    ("python_full_version < '3.9.10'", {"python_full_version": "3.9.9"}, True),
    ("python_full_version != '3.9.10'", {"python_full_version": "3.9.10"}, False),
    ("python_version >= '3.10'", {"python_full_version": "3.9.18"}, False),
    ("python_version >= '3.10'", {"python_full_version": "3.10.0"}, True),
    ("python_version == '3.12'", {"python_full_version": "3.12.9"}, True),
    ("python_full_version == '3.10.*'", {"python_full_version": "3.10.14"}, True),
    ("python_full_version == '3.10.*'", {"python_full_version": "3.11.0"}, False),
    ("python_full_version != '3.10.*'", {"python_full_version": "3.11.0"}, True),
    ("python_full_version == '3.1.*'", {"python_full_version": "3.10.1"}, False),
    ("python_full_version == '3.10.1.*'", {"python_full_version": "3.10"}, False),
    ("python_version == '3.12.*'", {"python_full_version": "3.12.9"}, True),
    ("python_full_version == '3.*'", {"python_full_version": "3.9.1"}, True),
    (
        "python_version < '3.9' or sys_platform == 'win32'",
        {"python_full_version": "3.12.0", "sys_platform": "win32"},
        True,
    ),
    (
        "sys_platform == 'linux' and os_name == 'nt'",
        {"sys_platform": "linux", "os_name": "posix"},
        False,
    ),
    (
        "sys_platform == 'a' or sys_platform == 'b' and os_name == 'c'",
        {"sys_platform": "a", "os_name": "x"},
        True,
    ),
    (
        "(sys_platform == 'a' or sys_platform == 'b') and os_name == 'c'",
        {"sys_platform": "a", "os_name": "x"},
        False,
    ),
    ('implementation_name == "cpython"', {"implementation_name": "cpython"}, True),
    ("platform_machine != 'arm64'", {"platform_machine": "x86_64"}, True),
    ("platform_python_implementation == 'PyPy'", {"platform_python_implementation": "C"}, False),
    (
        "platform_system == 'Linux'  and\tos_name=='posix'",
        {"platform_system": "Linux", "os_name": "posix"},
        True,
    ),
]
BAD_MARKERS = [
    "", "   ", "not os_name == 'nt'", "os_name in 'abc'", "os_name not in 'abc'",
    "python_version ~= '3.9'", "python_version === '3.9'", "extra == 'x'", "group == 'x'",
    "implementation_version == '1'", "platform_release == '1'", "'nt' == os_name",
    "os_name == nt", "os_name < 'nt'", "python_version >= '3.9.*'",
    "python_version < '3.9.*'", "python_version == '*'", "python_version == '.*'",
    "python_version == '3.*.1'", "python_version >= '3.9rc1'",
    "python_version >= '3.9.1.2.3'", "python_version >= '03.9x'", "os_name == 'a' and",
    "or os_name == 'a'", "(os_name == 'a'", "os_name == 'a')", "()",
    "os_name == 'a' os_name == 'b'",
    "os_name == 'a\\n'", "os_name = 'a'", "os_name == 'a' ; x", "os_name == 'caf\u00e9'",
    "os_name == '" + "a" * 257 + "'", "python_version >= ''", "foo == 'a'",
]  # fmt: skip


def test_lock_marker_subset_is_exact_and_fails_closed():
    for marker, env, expected in MARKERS:
        compiled = ul.parse_marker(marker)
        assert ul.evaluate_marker(compiled, env) is expected, marker
    for marker in BAD_MARKERS:
        with pytest.raises(ul.MarkerError) as caught:
            ul.parse_marker(marker)
        assert caught.value.detail == "unsupported_marker", marker
    for marker, detail in (
        ("os_name == 'a'" + " or os_name == 'a'" * 128, "marker_limit_exceeded"),
        ("(" * 9 + "os_name == 'a'" + ")" * 9, "marker_limit_exceeded"),
        ("os_name == '" + "a" * 1100 + "'", "marker_limit_exceeded"),
    ):
        with pytest.raises(ul.MarkerError) as caught:
            ul.parse_marker(marker)
        assert caught.value.detail == detail
    depth8 = "(" * 8 + "os_name == 'a'" + ")" * 8
    assert ul.evaluate_marker(ul.parse_marker(depth8), {"os_name": "a"}) is True
    compiled = ul.parse_marker("python_version >= '3.9'")
    with pytest.raises(ul.MarkerError) as caught:
        ul.evaluate_marker(compiled, {"os_name": "a"})
    assert caught.value.detail == "environment_incomplete"
    assert ul.marker_variables(ul.parse_marker("os_name == 'a' and python_version > '3'")) == {
        "os_name", "python_version",
    }  # fmt: skip


# ---- arguments, limits, names and filenames -------------------------------------------------


def test_lock_environment_is_validated_and_canonical():
    assert ul.parse_environment([]) == {}
    parsed = ul.parse_environment(["sys_platform=linux", "python_full_version=3.012.04"])
    assert parsed == {"python_full_version": "3.12.4", "sys_platform": "linux"}
    assert ul.parse_environment(["python_full_version=3.012"]) == {"python_full_version": "3.12"}
    bad = [
        "x=1", "sys_platform", "python_full_version=3", "python_full_version=3.x",
        "python_full_version=3.1.2.3", "python_full_version=03.12.00004", "python_full_version=",
        "os_name=a b", "os_name=" + "a" * 65, "os_name=caf\u00e9", "python_version=3.12",
        "python_full_version=3.12.4a",
    ]  # fmt: skip
    for item in bad:
        with pytest.raises(ul.DependencyUsageError) as caught:
            ul.parse_environment([item])
        assert caught.value.what == "env", item
    with pytest.raises(ul.DependencyUsageError):
        ul.parse_environment(["os_name=a", "os_name=b"])  # a duplicate key
    # A leading zero never changes the identity.
    first = imp(FORKED, env={"python_full_version": "3.012.4"})
    second = imp(FORKED, env={"python_full_version": "3.12.4"})
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["environment"] == {"python_full_version": "3.12.4"}


def test_lock_limits_and_arguments():
    assert ul.normalize_limits() == ul.normalize_limits({})
    for name, (default, low, high) in ul.LIMITS.items():
        assert getattr(ul.normalize_limits(), name) == default
        assert getattr(ul.normalize_limits({name: low}), name) == low
        assert getattr(ul.normalize_limits({name: high}), name) == high
        for value in (low - 1, high + 1, True, False, 1.5, "3", -1):
            with pytest.raises(ul.DependencyUsageError) as caught:
                ul.normalize_limits({name: value})
            assert caught.value.what == name, (name, value)
    with pytest.raises(ul.DependencyUsageError):
        ul.normalize_limits({"max_unknown": 1})
    assert ul.LIMITS["max_compression_ratio"] == (200, 1, 10000)
    for bad in ("", "-a", "a-", "a b", "a/b", "caf\u00e9", "a" * 201, "a..", ".a", "a$", "a\n"):
        with pytest.raises(ul.DependencyUsageError) as caught:
            ul.parse_package_argument(bad)
        assert caught.value.what == "package", repr(bad)
    with pytest.raises(ul.DependencyUsageError):
        ul.parse_package_argument(None)


def test_lock_name_and_filename_tables():
    names = {
        "S28.Demo_Pkg": "s28-demo-pkg", "Foo__Bar": "foo-bar", "a-_.b": "a-b", "ABC": "abc",
        "x": "x", "Friendly.Bard": "friendly-bard", "a" * 200: "a" * 200,
    }  # fmt: skip
    for raw, normal in names.items():
        assert ul.parse_package_argument(raw) == normal
        assert ul.normalize_name(raw) == normal
    assert ul.normalize_name("\u0130stanbul") == "\u0130stanbul"  # only ASCII is folded
    good = {
        "demo-1.0.0-py3-none-any.whl": ("demo", "1.0.0", None, ["py3"], ["none"], ["any"]),
        "a_b-2.0.0rc1-1build-py2.py3-none-any.whl": (
            "a_b", "2.0.0rc1", "-1build", ["py2", "py3"], ["none"], ["any"],
        ),
        "x-1+local.1-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": (
            "x", "1+local.1", None, ["cp312"], ["cp312"],
            ["manylinux_2_17_x86_64", "manylinux2014_x86_64"],
        ),
    }  # fmt: skip
    for filename, expected in good.items():
        parts = ul.parse_wheel_filename(filename)
        assert parts is not None
        got = (parts["dist"], parts["version"], parts["build"], parts["python"], parts["abi"],
               parts["platform"])  # fmt: skip
        assert got == expected, filename
    for bad in (
        "demo-1.0.0.tar.gz",
        "demo-1.0.0-py3-none-any.zip",
        "demo-1.0.0-py3-none.whl",
        "demo-x-py3-none-any.whl",
        "de mo-1.0.0-py3-none-any.whl",
        "demo-1.0.0-py3-none-any.whl\n",
        "demo-1.0.0-py3-none-any.whl/x",
        "demo-1.0.0-py3-none-an\u00fd.whl",
        "a" * 250 + "-1.0.0-py3-none-any.whl",
        "",
    ):
        assert ul.parse_wheel_filename(bad) is None, repr(bad)


def test_lock_rejection_vocabulary_is_closed():
    assert len(ul.REJECTIONS) == 19 and len(set(ul.REJECTIONS)) == 19
    with pytest.raises(ValueError):
        ul.DependencyRejected("made_up")
    with pytest.raises(ValueError):
        ul.DependencyRejected("lock_invalid", detail="made_up")
    exc = ul.DependencyRejected("invalid_wheel")
    assert str(exc) == "dependency_rejected: invalid_wheel"
    assert exc.coordinate is None and exc.detail is None
    assert reason(lambda: imp(SINGLE, package="nope")) == "package_not_in_lock"
    assert WHEEL_SHA in SINGLE.decode() and PURE in SINGLE.decode()
    assert json.loads(json.dumps(ul.ENV_KEYS)) == list(ul.ENV_KEYS)
    assert "python_version" not in ul.ENV_KEYS


def test_lock_prescan_runs_before_and_independently_of_the_toml_parser():
    """Each pre-scan rule fires on its own, so the outcome never depends on the TOML version."""
    scan = ul.prescan
    assert scan(b'a = "b"\n', LIMITS) == 'a = "b"\n'
    rejected_inputs = {
        "lf in inline": b"y = {a = 1,\n b = 2}\n",
        "lf in nested inline": b"y = {a = {b = 1,\n c = 2}}\n",
        "escape e": b'y = "\\e"\n',
        "escape x": b'y = "\\x41"\n',
        "odd run of three": b'y = "\\\\\\e"\n',
        "depth 17": b"y = " + b"[" * 17 + b"]" * 17 + b"\n",
        "digits": b"y = " + b"9" * 129 + b"\n",
        "underscores": b"y = " + b"_" * 129 + b"\n",
        "bom": b"\xef\xbb\xbfa = 1\n",
        "latin": b"# \xe9\n",
    }
    for label, data in rejected_inputs.items():
        assert reason(lambda d=data: scan(d, LIMITS)) == "lock_invalid", label
    accepted_inputs = {
        "even run": b'y = "\\\\e"\n',
        "lf outside inline": b"y = {a = 1}\nz = 2\n",
        "depth 16": b"y = " + b"[" * 16 + b"]" * 16 + b"\n",
        "digits 128": b"y = " + b"9" * 128 + b"\n",
        "lone backslash": b'y = "\\n"\n',
    }
    for label, data in accepted_inputs.items():
        assert scan(data, LIMITS) == data.decode(), label
    assert reason(lambda: scan(b"x" * 11, ul.normalize_limits({"max_lock_bytes": 10}))) == (
        "lock_too_large"
    )
