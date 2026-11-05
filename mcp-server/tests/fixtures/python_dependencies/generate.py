"""Deterministic generator of the ACI-S28 fixtures (standard library only, ASCII only).

Writes the demo wheel and the two uv.lock files into a directory (default: this directory).
Every wheel member is embedded as a bytes literal, so no wheel source exists as a `.py` file
here and nothing in it is collected, linted or imported. The zip writer is raw (fixed headers,
no dates, no comments), so the outputs are byte-identical on every run and interpreter.

The tests load this module by path and use `build_zip`, `Entry`, `package_block` and
`lock_document` to craft archives and lock variants in memory.
"""

from __future__ import annotations

import base64
import hashlib
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

PURE = "s28_demo_pkg-1.2.0-py3-none-any.whl"
PLATFORM = "s28_demo_pkg-1.2.0-cp312-cp312-manylinux_2_17_x86_64.whl"
OLD = "s28_demo_pkg-1.1.0-py3-none-any.whl"
HOST = "https://files.pythonhosted.org/packages/00/11/22334455/"
REGISTRY = "https://pypi.org/simple"
REGULAR = 0o100644 << 16
DIRECTORY = (0o040755 << 16) | 0x10


@dataclass
class Entry:
    """One zip member; every optional field overrides the honest value (to craft bad input)."""

    name: bytes
    data: bytes = b""
    attr: int = REGULAR
    flags: int = 0
    method: int = 0
    crc: int | None = None
    local_name: bytes | None = None
    size: int | None = None
    csize: int | None = None
    payload: bytes | None = None
    offset: int | None = None
    extra: bytes = b""


def _le(value: int, width: int) -> bytes:
    return value.to_bytes(width, "little")


def build_zip(entries: list[Entry], prefix: bytes = b"") -> bytes:
    """A zip archive with raw STORED (or DEFLATED when asked) members and no timestamps."""
    body = b""
    central = b""
    for entry in entries:
        payload = entry.payload
        if payload is None:
            payload = entry.data
            if entry.method == 8:
                packer = zlib.compressobj(9, zlib.DEFLATED, -15)
                payload = packer.compress(entry.data) + packer.flush()
        crc = zlib.crc32(entry.data) if entry.crc is None else entry.crc
        size = len(entry.data) if entry.size is None else entry.size
        csize = len(payload) if entry.csize is None else entry.csize
        local = entry.name if entry.local_name is None else entry.local_name
        header = b"PK\x03\x04" + _le(20, 2) + _le(entry.flags, 2) + _le(entry.method, 2)
        header += _le(0, 2) + _le(0x21, 2) + _le(crc, 4) + _le(csize, 4) + _le(size, 4)
        header += _le(len(local), 2) + _le(len(entry.extra), 2) + local + entry.extra
        offset = len(body) if entry.offset is None else entry.offset
        record = b"PK\x01\x02" + _le(788, 2) + _le(20, 2) + _le(entry.flags, 2)
        record += _le(entry.method, 2) + _le(0, 2) + _le(0x21, 2) + _le(crc, 4)
        record += _le(csize, 4) + _le(size, 4) + _le(len(entry.name), 2)
        record += _le(len(entry.extra), 2) + _le(0, 2) + _le(0, 2) + _le(0, 2)
        record += _le(entry.attr, 4) + _le(offset, 4) + entry.name + entry.extra
        body += header + payload
        central += record
    end = b"PK\x05\x06" + _le(0, 2) + _le(0, 2) + _le(len(entries), 2) + _le(len(entries), 2)
    end += _le(len(central), 4) + _le(len(body), 4) + _le(0, 2)
    return prefix + body + central + end


# ---- the demo wheel -------------------------------------------------------------------------

INIT = (
    b'"""Demo package: this module body has side effects that must never run."""\n'
    b"import os\n"
    b"import pathlib\n"
    b"\n"
    b'_target = os.environ.get("S28_SENTINEL_PATH")\n'
    b"if _target:\n"
    b'    pathlib.Path(_target).write_text("executed")\n'
    b'os.system(":")\n'
    b"\n"
    b'__all__ = ["Widget", "make_widget"]\n'
    b"\n"
    b"from .core import Widget, make_widget\n"
    b"from . import helpers as h\n"
    b'raise RuntimeError("the s28 demo package must never be imported")\n'
)
CORE = (
    b"import functools\n"
    b"import os.path\n"
    b"import json as j\n"
    b"from typing import Any\n"
    b"\n"
    b"try:\n"
    b"    import ujson\n"
    b"except ImportError:\n"
    b"    ujson = None\n"
    b"\n"
    b"if TYPE_CHECKING:\n"
    b"    def typing_only():\n"
    b"        pass\n"
    b"\n"
    b"\n"
    b"class Widget:\n"
    b'    """A widget."""\n'
    b"\n"
    b"    def method(self):\n"
    b"        pass\n"
    b"\n"
    b"    async def amethod(self):\n"
    b"        pass\n"
    b"\n"
    b"    @staticmethod\n"
    b"    def static():\n"
    b"        pass\n"
    b"\n"
    b"    @property\n"
    b"    def size(self):\n"
    b"        return 1\n"
    b"\n"
    b"    @size.setter\n"
    b"    def size(self, value):\n"
    b"        pass\n"
    b"\n"
    b"    class Config:\n"
    b"        def load(self):\n"
    b"            pass\n"
    b"\n"
    b"    def with_locals(self):\n"
    b"        def inner():\n"
    b"            pass\n"
    b"\n"
    b"        class InnerClass:\n"
    b"            pass\n"
    b"\n"
    b"\n"
    b"class Chain1:\n"
    b"    class Chain2:\n"
    b"        class Chain3:\n"
    b"            class Chain4:\n"
    b"                class Chain5:\n"
    b"                    def too_deep(self):\n"
    b"                        pass\n"
    b"\n"
    b"\n"
    b"def make_widget():\n"
    b"    return Widget()\n"
    b"\n"
    b"\n"
    b"async def fetch():\n"
    b"    pass\n"
    b"\n"
    b"\n"
    b"@functools.lru_cache\n"
    b"def cached():\n"
    b"    pass\n"
    b"\n"
    b"\n"
    b"def outer():\n"
    b"    def inner():\n"
    b"        pass\n"
)
SUB_INIT = b"from ..core import Widget\nfrom ...outside import x\nfrom .mod import *\n"
SUB_MOD = b'def __getattr__(name):\n    raise AttributeError(name)\n\n__all__ = ["a"] + extra\n'
HELPERS = b"def helper_one():\n    pass\n\n\ndef helper_two(x, y=1):\n    pass\n"
BROKEN = b"class Phantom:\n    def broken(self\n"
LATIN = b"# caf\xe9\ndef latin():\n    pass\n"
STUB = b"class Widget: ...\n"
EXTENSION = b"\x7fELF\x02\x01\x01\x00opaque-compiled-bytes\x00\x01\x02"
DATA_JSON = b'{"key": 1}\n'
BAD_NAME = b"def bad():\n    pass\n"
TOOL = b"def tool():\n    pass\n"
SETUP = (
    b"import os\n"
    b'open(os.environ["S28_SENTINEL_PATH"], "w").write("executed")\n'
    b"\n"
    b"\n"
    b"def main():\n"
    b"    pass\n"
)
DIST = "s28_demo_pkg-1.2.0.dist-info"
METADATA = b"Metadata-Version: 2.1\nName: S28.Demo_Pkg\nVersion: 1.2.0\nSummary: demo\n\nBody\n"
WHEEL = b"Wheel-Version: 1.0\nGenerator: s28-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"


def wheel_members() -> list[tuple[str, bytes]]:
    members = [
        ("s28_demo_pkg/__init__.py", INIT),
        ("s28_demo_pkg/_speed.cpython-312-x86_64-linux-gnu.so", EXTENSION),
        ("s28_demo_pkg/bad-name.py", BAD_NAME),
        ("s28_demo_pkg/broken.py", BROKEN),
        ("s28_demo_pkg/core.py", CORE),
        ("s28_demo_pkg/core.pyi", STUB),
        ("s28_demo_pkg/data.json", DATA_JSON),
        ("s28_demo_pkg/helpers.py", HELPERS),
        ("s28_demo_pkg/latin.py", LATIN),
        ("s28_demo_pkg/py.typed", b""),
        ("s28_demo_pkg/sub/__init__.py", SUB_INIT),
        ("s28_demo_pkg/sub/mod.py", SUB_MOD),
        ("s28_demo_pkg-1.2.0.data/scripts/tool.py", TOOL),
        ("setup.py", SETUP),
    ]
    dist = [(DIST + "/METADATA", METADATA), (DIST + "/WHEEL", WHEEL)]
    lines = []
    for name, data in [*members, *dist]:
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        lines.append(name + ",sha256=" + digest + "," + str(len(data)))
    lines.append(DIST + "/RECORD,,")
    return [*members, *dist, (DIST + "/RECORD", ("\n".join(lines) + "\n").encode())]


def wheel_bytes(members: list[tuple[str, bytes]] | None = None, method: int = 0) -> bytes:
    chosen = wheel_members() if members is None else members
    return build_zip([Entry(name.encode(), data, method=method) for name, data in chosen])


# ---- uv.lock builders -----------------------------------------------------------------------


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wheel_line(filename: str, digest: str, size: int, host: str = HOST) -> str:
    return (
        '    { url = "'
        + host
        + filename
        + '", hash = "sha256:'
        + digest
        + '", size = '
        + str(size)
        + " },"
    )


def package_block(
    name: str,
    version: str,
    source: str = 'registry = "' + REGISTRY + '"',
    wheels: tuple[str, ...] = (),
    sdist: str | None = None,
    markers: tuple[str, ...] = (),
) -> str:
    lines = ["[[package]]", 'name = "' + name + '"', 'version = "' + version + '"']
    if markers:
        lines.append("resolution-markers = [")
        lines.extend('    "' + marker + '",' for marker in markers)
        lines.append("]")
    lines.append("source = { " + source + " }")
    if sdist is not None:
        lines.append(sdist)
    if wheels:
        lines.append("wheels = [")
        lines.extend(wheels)
        lines.append("]")
    return "\n".join(lines) + "\n"


def lock_document(
    blocks: list[str],
    markers: tuple[str, ...] = (),
    version: int = 1,
    revision: int | None = 3,
) -> str:
    head = ["version = " + str(version)]
    if revision is not None:
        head.append("revision = " + str(revision))
    head.append('requires-python = ">=3.9"')
    if markers:
        head.append("resolution-markers = [")
        head.extend('    "' + marker + '",' for marker in markers)
        head.append("]")
    return "\n".join(head) + "\n\n" + "\n".join(blocks)


def sdist_line(name: str, digest: str = "ab" * 32, size: int = 1234) -> str:
    url, tail = '"' + HOST + name + '"', '"sha256:' + digest + '", size = ' + str(size)
    return "sdist = { url = " + url + ", hash = " + tail + " }"


def pure_line(name: str, version: str) -> str:
    """A deterministic locked pure wheel of `name` (fake bytes: only its entry is used)."""
    filename = name.replace("-", "_") + "-" + version + "-py3-none-any.whl"
    return wheel_line(filename, sha((name + version).encode()), 100)


def single_lock(wheel: bytes) -> str:
    pure = wheel_line(PURE, sha(wheel), len(wheel))
    platform = wheel_line(PLATFORM, "cd" * 32, 4321)
    md5 = '    { url = "' + HOST + 'md5only-1.0.0-py3-none-any.whl", hash = "md5:' + "ab" * 16
    git = 'git = "https://github.com/example/gitdep?rev=abc#' + "a" * 40 + '"'
    local = wheel_line("localreg-1.0.0-py3-none-any.whl", "ee" * 32, 10, "../local-registry/")
    platonly = wheel_line("platonly-1.0.0-cp312-cp312-manylinux_2_17_x86_64.whl", "ab" * 32, 9)
    blocks = [
        package_block("dirdep", "0.1.0", 'directory = "../dirdep"'),
        package_block("gitdep", "0.1.0", git),
        package_block("localreg", "1.0.0", 'registry = "../local-registry"', (local,)),
        package_block("md5only", "1.0.0", wheels=(md5 + '", size = 10 },',)),
        package_block("pathdep", "1.0.0", 'path = "../pathdep-1.0.0-py3-none-any.whl"'),
        package_block("platonly", "1.0.0", wheels=(platonly,)),
        package_block(
            "s28-demo-pkg",
            "1.2.0",
            wheels=(pure, platform),
            sdist=sdist_line("s28_demo_pkg-1.2.0.tar.gz"),
        ),
        package_block("s28-root", "0.1.0", 'editable = "."'),
        package_block("sdistonly", "1.0.0", sdist=sdist_line("sdistonly-1.0.0.tar.gz")),
        package_block(
            "urldep", "1.0.0", 'url = "https://example.com/urldep-1.0.0-py3-none-any.whl"'
        ),
        package_block("virtualdep", "1.0.0", 'virtual = "."'),
    ]
    return lock_document(blocks)


def forked_lock(wheel: bytes) -> str:
    pure = wheel_line(PURE, sha(wheel), len(wheel))
    old = wheel_line(OLD, "11" * 32, 2000)
    ge, lt = "python_full_version >= '3.12'", "python_full_version < '3.12'"
    win, other = "sys_platform == 'win32'", "sys_platform != 'win32'"
    blocks = [
        package_block(
            "extrapkg",
            "1.0.0",
            wheels=(pure_line("extrapkg", "1.0.0"),),
            markers=("extra == 'extra-3-foo-cpu'",),
        ),
        package_block(
            "overlap",
            "1.0.0",
            wheels=(pure_line("overlap", "1.0.0"),),
            markers=("python_full_version >= '3.10'",),
        ),
        package_block("overlap", "2.0.0", wheels=(pure_line("overlap", "2.0.0"),), markers=(ge,)),
        package_block("s28-demo-pkg", "1.1.0", wheels=(old,), markers=(lt,)),
        package_block("s28-demo-pkg", "1.2.0", wheels=(pure,), markers=(ge,)),
        package_block(
            "soloforked",
            "1.0.0",
            wheels=(pure_line("soloforked", "1.0.0"),),
            markers=("python_full_version < '3.10'",),
        ),
        package_block(
            "splitpkg", "2.0.0", wheels=(pure_line("splitpkg", "2.0.0"),), markers=(win,)
        ),
        package_block(
            "splitpkg", "2.0.0", wheels=(pure_line("splitpkg", "2.0.0"),), markers=(other,)
        ),
        package_block("unmarked", "1.0.0", wheels=(pure_line("unmarked", "1.0.0"),)),
        package_block("unmarked", "2.0.0", wheels=(pure_line("unmarked", "2.0.0"),)),
    ]
    return lock_document(blocks, markers=(ge, lt))


def generate(target: Path) -> dict[str, bytes]:
    wheel = wheel_bytes()
    outputs = {
        PURE: wheel,
        "single.uv.lock": single_lock(wheel).encode(),
        "forked.uv.lock": forked_lock(wheel).encode(),
    }
    for name, data in outputs.items():
        (target / name).write_bytes(data)
    return outputs


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent)
