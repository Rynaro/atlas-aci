"""ACI-S28 archive ladder: one case per rule of the zip, metadata and limit checks."""

from __future__ import annotations

import hashlib
import zipfile

import pytest
from test_s28_support import (
    GEN,
    PURE,
    WHEEL,
    good_entries,
    grammar,  # noqa: F401  (session fixture, used through usefixtures)
    imp,
    lock_for,
    minimal_entries,
    reason,
    rejected,
)

from atlas_aci.dependencies import uv_lock as ul

pytestmark = pytest.mark.usefixtures("grammar")

Entry = GEN.Entry
SOURCE = b"def f():\n    pass\n"
DIST = GEN.DIST


def build(entries, **kwargs):
    wheel = GEN.build_zip(entries, **kwargs)
    return wheel, lock_for(wheel)


def outcome(entries, limits=None, **kwargs):
    wheel, lock = build(entries, **kwargs)
    return reason(lambda: imp(lock, wheel=wheel, limits=limits or ul.normalize_limits()))


def accepted(entries, limits=None, **kwargs):
    wheel, lock = build(entries, **kwargs)
    return imp(lock, wheel=wheel, limits=limits or ul.normalize_limits())


def with_member(*extra, **kwargs):
    return [*minimal_entries(**kwargs), *extra]


def test_archive_positive_controls_import(tmp_path):
    stored = accepted(minimal_entries())
    assert [m["path"] for m in stored["members"]] == [
        DIST + "/METADATA",
        DIST + "/WHEEL",
        "s28_demo_pkg/__init__.py",
    ]
    deflated = accepted([Entry(e.name, e.data, method=8) for e in minimal_entries()])
    assert deflated["declarations"][0]["qualified_name"] == "A"
    assert [m["sha256"] for m in deflated["members"]] == [m["sha256"] for m in stored["members"]]
    assert deflated["package"]["artifact"]["sha256"] != stored["package"]["artifact"]["sha256"]
    assert [d["qualified_name"] for d in stored["declarations"]] == ["A"]
    # Directory entries are validated, excluded from `members` and from `counts.members`.
    folders = [
        Entry(b"s28_demo_pkg/", attr=GEN.DIRECTORY),
        Entry((DIST + "/").encode(), attr=GEN.DIRECTORY),
        *minimal_entries(),
    ]
    with_dirs = accepted(folders)
    assert [m["path"] for m in with_dirs["members"]] == [m["path"] for m in stored["members"]]
    assert with_dirs["counts"]["members"] == stored["counts"]["members"] == 3
    # A directory entry without a mode (zero attributes) is a directory when it ends in `/`.
    assert accepted([Entry(b"s28_demo_pkg/", attr=0), *minimal_entries()])["counts"]["members"] == 3


def test_archive_wheel_level_limits_and_digest_come_before_the_archive(monkeypatch):
    seen = []
    real = zipfile.ZipFile

    class Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            seen.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", Spy)
    small = ul.normalize_limits({"max_wheel_bytes": len(WHEEL) - 1})
    exc = rejected(lambda: imp(limits=small))
    assert exc.reason == "wheel_too_large" and exc.coordinate is not None
    assert seen == []
    exact = ul.normalize_limits({"max_wheel_bytes": len(WHEEL)})
    assert imp(limits=exact)["package"]["artifact"]["bytes"] == len(WHEEL)
    assert seen == [1]


INVALID = {
    "garbage": lambda: (b"this is not a zip archive", None),
    "empty": lambda: (b"", None),
    "truncated": lambda: (WHEEL[: len(WHEEL) - 40], None),
}


def test_archive_invalid_wheel_cases():
    for label, make in INVALID.items():
        wheel, _ = make()
        assert reason(lambda w=wheel: imp(lock_for(w), wheel=w)) == "invalid_wheel", label
    flipped = with_member(Entry(b"m/x.py", SOURCE, crc=0x12345678))
    assert outcome(flipped) == "invalid_wheel"
    lie = with_member(Entry(b"m/x.py", SOURCE, size=len(SOURCE) - 3))
    assert outcome(lie) == "invalid_wheel"
    bigger = with_member(Entry(b"m/x.py", SOURCE, size=len(SOURCE) + 5, csize=len(SOURCE) + 5))
    assert outcome(bigger) == "invalid_wheel"
    mismatch = with_member(Entry(b"m/abc.py", SOURCE, local_name=b"m/xyz.py"))
    assert outcome(mismatch) == "invalid_wheel"
    # A deflate stream that expands past its declared size is read only up to size + 1.
    zeros = b"\x00" * 50000
    packer = __import__("zlib").compressobj(9, 8, -15)
    payload = packer.compress(zeros) + packer.flush()
    bomb = with_member(Entry(b"m/x.py", zeros, method=8, payload=payload, size=100))
    assert outcome(bomb) == "invalid_wheel"
    # A structurally intact wheel with one flipped payload byte fails its CRC (the digest is
    # recomputed here so the digest check cannot hide the structural error).
    data = bytearray(GEN.build_zip(with_member(Entry(b"m/x.py", SOURCE))))
    at = bytes(data).index(SOURCE)
    data[at] ^= 0x01
    assert reason(lambda: imp(lock_for(bytes(data)), wheel=bytes(data))) == "invalid_wheel"


def test_archive_unsafe_member_names():
    names = {
        "parent": b"../x.py",
        "parent nested": b"a/../../x.py",
        "absolute": b"/etc/x.py",
        "backslash": b"a\\b.py",
        "drive slash": b"C:/x.py",
        "drive": b"C:x.py",
        "colon": b"a:b.py",
        "nul": b"a\x00b.py",
        "newline": b"a\nb.py",
        "del": b"a\x7fb.py",
        "double slash": b"a//b.py",
        "dot segment": b"./a.py",
        "dot dot": b"a/./b.py",
        "directory double slash": b"dir//",
        "latin without flag": b"caf\xe9.py",
        "utf8 without flag": "caf\u00e9.py".encode(),
        "long": b"a" * 1025,
        "empty": b"",
        "slash only": b"/",
    }
    for label, name in names.items():
        assert outcome(with_member(Entry(name, SOURCE))) == "unsafe_archive_member", label
    utf8 = Entry("caf\u00e9.py".encode(), SOURCE, flags=0x800)
    assert outcome(with_member(utf8)) == "unsafe_archive_member"  # non-ASCII even when flagged
    assert accepted(with_member(Entry(b"a" * 1000, SOURCE)))  # the length boundary is 1024
    assert outcome(with_member(Entry(b"a" * 1024 + b".py", SOURCE))) == "unsafe_archive_member"


def test_archive_unsafe_member_structure():
    mine = Entry(b"m/x.py", SOURCE)
    cases = {
        "duplicate": with_member(mine, Entry(b"m/x.py", SOURCE)),
        "case duplicate": with_member(mine, Entry(b"M/X.py", SOURCE)),
        "file then directory prefix": with_member(Entry(b"m", SOURCE), mine),
        "directory double slash as a directory": with_member(
            Entry(b"d//", attr=GEN.DIRECTORY), mine
        ),
        "directory double slash without mode": with_member(Entry(b"d//", attr=0), mine),
        "directory then file": with_member(Entry(b"m/", attr=GEN.DIRECTORY), Entry(b"m", SOURCE)),
        "symlink": with_member(Entry(b"link.py", b"x.py", attr=0o120777 << 16)),
        "fifo": with_member(Entry(b"pipe.py", b"", attr=0o010644 << 16)),
        "device": with_member(Entry(b"dev.py", b"", attr=0o020644 << 16)),
        "directory with data": with_member(Entry(b"d/", b"x", attr=GEN.DIRECTORY)),
        "trailing slash on a file mode": with_member(Entry(b"x.py/", attr=GEN.REGULAR)),
        "directory mode without slash": with_member(Entry(b"d", attr=GEN.DIRECTORY)),
        "encrypted": with_member(Entry(b"m/e.py", SOURCE, flags=0x1)),
        "strong encryption": with_member(Entry(b"m/e.py", SOURCE, flags=0x40)),
        "masked local header": with_member(Entry(b"m/e.py", SOURCE, flags=0x2000)),
        "bzip2": with_member(Entry(b"m/b.py", SOURCE, method=12)),
        "lzma": with_member(Entry(b"m/l.py", SOURCE, method=14)),
        "overlapping offsets": with_member(Entry(b"m/o.py", SOURCE, offset=0)),
        "wrong local signature": with_member(Entry(b"m/o.py", SOURCE, offset=3)),
        "offset past the end": with_member(Entry(b"m/o.py", SOURCE, offset=10**6)),
    }
    for label, entries in cases.items():
        assert outcome(entries) == "unsafe_archive_member", label
    assert outcome(with_member(mine), prefix=b"prepended") == "unsafe_archive_member"


def test_archive_limits_are_documented_bounds():
    three = with_member(Entry(b"m/x.py", SOURCE))
    count = len(three)
    assert outcome(three, ul.normalize_limits({"max_members": count - 1})) == (
        "archive_limit_exceeded"
    )
    assert accepted(three, ul.normalize_limits({"max_members": count}))
    total = sum(len(e.data) for e in three)
    limits = ul.normalize_limits({"max_total_uncompressed_bytes": total - 1})
    assert outcome(three, limits) == "archive_limit_exceeded"
    assert accepted(three, ul.normalize_limits({"max_total_uncompressed_bytes": total}))
    biggest = max(len(e.data) for e in three)
    small = ul.normalize_limits({"max_member_bytes": biggest - 1})
    assert outcome(three, small) == "archive_limit_exceeded"
    assert accepted(three, ul.normalize_limits({"max_member_bytes": biggest}))
    # The ratio cap applies to every member (here a dist-info file) and is a limit, not a constant.
    padded = b"Name: S28.Demo_Pkg\nVersion: 1.2.0\n" + b"X-Pad: " + b"a" * 60000 + b"\n"
    entries = [
        Entry(b"m/x.py", SOURCE),
        Entry((DIST + "/METADATA").encode(), padded, method=8),
        Entry((DIST + "/WHEEL").encode(), GEN.WHEEL),
    ]
    assert outcome(entries) == "archive_limit_exceeded"
    assert accepted(entries, ul.normalize_limits({"max_compression_ratio": 10000}))
    data = Entry(b"m/data.json", b"0" * 100000, method=8)
    assert outcome(with_member(data)) == "archive_limit_exceeded"
    assert accepted(with_member(data), ul.normalize_limits({"max_compression_ratio": 10000}))
    ordinary = Entry(b"m/ok.py", b"x = 1\n" * 100, method=8)  # small and compressible: fine
    assert accepted(with_member(ordinary))


def meta(**overrides):
    base = {
        DIST + "/METADATA": GEN.METADATA,
        DIST + "/WHEEL": GEN.WHEEL,
    }
    base.update({DIST + "/" + k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            base.pop(DIST + "/" + key, None)
    return [Entry(b"m/x.py", SOURCE)] + [Entry(n.encode(), d) for n, d in base.items()]


def test_archive_metadata_cross_check():
    good = b"Metadata-Version: 2.1\nName: s28-demo-pkg\nVersion: 1.2.0\n\nName: other\n"
    assert accepted(meta(METADATA=good))["dist_info"]["name"] == "s28-demo-pkg"  # body ignored
    folded = b"Metadata-Version: 2.1\nSummary: a\n  continued\nNAME: S28_demo.pkg\nversion: 1.2.0\n"
    assert accepted(meta(METADATA=folded))["dist_info"]["name"] == "S28_demo.pkg"
    cases = {
        "name mismatch": b"Name: other\nVersion: 1.2.0\n",
        "version mismatch": b"Name: s28-demo-pkg\nVersion: 1.2.1\n",
        "version not normalised": b"Name: s28-demo-pkg\nVersion: 1.2\n",
        "version padded": b"Name: s28-demo-pkg\nVersion: 1.2.0.0\n",
        "duplicate name": b"Name: s28-demo-pkg\nName: s28-demo-pkg\nVersion: 1.2.0\n",
        "no name": b"Version: 1.2.0\n",
        "no version": b"Name: s28-demo-pkg\n",
        "not utf-8": b"Name: s28-demo-pkg\nVersion: 1.2.0\nSummary: caf\xe9\n",
        "empty": b"",
    }
    for label, data in cases.items():
        assert outcome(meta(METADATA=data)) == "wheel_metadata_mismatch", label
    assert outcome(meta(METADATA=None)) == "wheel_metadata_mismatch"
    assert outcome(meta(WHEEL=None)) == "wheel_metadata_mismatch"
    wheel_versions = {
        "2.0": b"Wheel-Version: 2.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "none": b"Root-Is-Purelib: true\nTag: py3-none-any\n",
        "two": b"Wheel-Version: 1.0\nWheel-Version: 1.1\nRoot-Is-Purelib: true\nTag: a-none-any\n",
        "text": b"Wheel-Version: one\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    for label, data in wheel_versions.items():
        assert outcome(meta(WHEEL=data)) == "wheel_metadata_mismatch", label
    assert accepted(meta(WHEEL=b"Wheel-Version: 1.9\nRoot-Is-Purelib: TRUE\nTag: py3-none-any\n"))
    impure = {
        "purelib false": b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-any\n",
        "purelib absent": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
        "platform tag": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp312-cp312-linux_x64\n",
        "abi tag": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp312-abi3-any\n",
        "no tag": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
        "mixed tags": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\nTag: cp312-cp312-w\n"
        ),
    }
    for label, data in impure.items():
        assert outcome(meta(WHEEL=data)) == "not_pure_python", label
    tags = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\nTag: py2-none-any\n"
    assert accepted(meta(WHEEL=tags))["dist_info"]["tags"] == ["py2-none-any", "py3-none-any"]


def test_archive_dist_info_directory_rules():
    other = "zzz_other_pkg-1.2.0.dist-info"
    two = [*meta(), Entry((other + "/METADATA").encode(), GEN.METADATA)]
    assert outcome(two) == "wheel_metadata_mismatch"
    renamed = [Entry(b"m/x.py", SOURCE)]
    for directory in ("s28_demo_pkg-1.2.1.dist-info", other, "s28_demo_pkg.dist-info"):
        renamed += [Entry((directory + "/METADATA").encode(), GEN.METADATA)]
        renamed += [Entry((directory + "/WHEEL").encode(), GEN.WHEEL)]
        assert outcome(renamed) == "wheel_metadata_mismatch", directory
        renamed = [Entry(b"m/x.py", SOURCE)]
    assert outcome([Entry(b"m/x.py", SOURCE)]) == "wheel_metadata_mismatch"
    nested = [Entry(b"m/x.py", SOURCE)] + [
        Entry(("deep/" + DIST + "/" + n).encode(), d)
        for n, d in (("METADATA", GEN.METADATA), ("WHEEL", GEN.WHEEL))
    ]
    assert outcome(nested) == "wheel_metadata_mismatch"  # only a TOP-LEVEL dist-info counts
    assert accepted(meta())  # control: the same shape, correctly named, imports


def test_archive_rejections_carry_the_coordinate_and_precede_the_grammar(monkeypatch):
    import tree_sitter_language_pack as pack

    loads = []
    real = pack.get_parser
    monkeypatch.setattr(pack, "get_parser", lambda name: loads.append(name) or real(name))
    wheel, lock = build(with_member(Entry(b"../x.py", SOURCE)))
    exc = rejected(lambda: imp(lock, wheel=wheel))
    assert exc.reason == "unsafe_archive_member"
    assert exc.coordinate is not None and exc.coordinate["coordinate"] == "pypi:s28-demo-pkg@1.2.0"
    wheel, lock = build(
        meta(WHEEL=b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-any\n")
    )
    assert reason(lambda: imp(lock, wheel=wheel)) == "not_pure_python"
    assert loads == []  # the grammar is only needed after the archive and metadata are accepted
    imp()
    assert loads == ["python"]


def test_archive_wheel_digest_is_the_identity_of_the_file():
    wheel, lock = build(minimal_entries())
    snapshot = imp(lock, wheel=wheel)
    assert snapshot["package"]["artifact"]["sha256"] == hashlib.sha256(wheel).hexdigest()
    assert snapshot["package"]["artifact"]["bytes"] == len(wheel)
    assert [e for e in good_entries(skip=("setup.py",))] and PURE in lock.decode()
