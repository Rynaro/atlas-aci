"""ACI-S32: a strict, pure unified-diff parser and exact applier over bytes.

No process, file, clock or environment is touched: the input is the patch bytes (and, for
`apply`, the base file bytes) and the output is a typed value or a `PatchRejected` carrying
one closed reason. Application is exact: there is no fuzz and no offset search.

Grammar (lines split on LF only; a CR is content; ASCII digits only; everything iterative,
so nothing depends on the interpreter's recursion limit)::

    patch    := preamble? section+ signature?
    preamble := lines before the first line starting "diff --git " or "--- "
    section  := git_hdr ext* (files hunk+)? | files hunk+
    git_hdr  := "diff --git a/X b/X"       (X must equal the ---/+++ path)
    ext      := "index H1..H2[ MODE]" | "new file mode MODE" | "deleted file mode MODE"
              | "old mode MODE" | "new mode MODE"
              | "similarity index" | "dissimilarity index" | "rename from|to" | "copy from|to"
    files    := "--- " (a/P | /dev/null) ["\\t" any] LF "+++ " (b/P | /dev/null) ["\\t" any] LF
    hunk     := "@@ -S[,C] +T[,D] @@" [" " any] LF body
    body     := (" "|"-"|"+") content LF  |  "\\ " any LF
    signature:= "-- " LF any*

`\\ No newline at end of file` binding: it marks the preceding body line as lacking its
final LF. After a context line it applies to BOTH sides; after `-` to the old side; after
`+` to the new side. It may sit between a `-` run and a `+` run (`-x`, `\\ `, `+y`). A line
carrying it is the last line of its side: any later body line on that side, a marker before
any body line, and two markers for one line are `malformed_patch`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from atlas_aci.storage import evidence as ev

PATCH_LIMITS: dict[str, tuple[int, int, int]] = {
    "max_patch_bytes": (262144, 1, 262144),
    "max_files": (32, 1, 32),
    "max_hunks": (256, 1, 1024),
    "max_line_bytes": (8192, 1, 65536),
    "max_result_file_bytes": (1048576, 1, 8388608),
}
PATCH_REJECTIONS = (
    "malformed_patch",
    "unsupported_patch_feature",
    "symlink_change",
    "submodule_change",
    "unsupported_mode",
    "invalid_path",
    "duplicate_path",
    "limit_exceeded",
    "binary_patch",
    "not_utf8",
    "unsupported_path",
    "path_not_in_base",
    "path_exists_in_base",
    "base_mismatch",
)
OPERATIONS = ("create", "delete", "modify")
REGULAR_MODES = ("100644", "100755")

_HUNK = re.compile(
    rb"@@ -([0-9]{1,9})(?:,([0-9]{1,9}))? \+([0-9]{1,9})(?:,([0-9]{1,9}))? @@(?: .*)?"
)
_INDEX = re.compile(rb"index ([0-9a-f]{4,64})\.\.([0-9a-f]{4,64})(?: ([0-9]{6}))?")
_MODE = re.compile(rb"[0-7]{6}")
_BINARY = re.compile(rb"Binary files .* differ")
_UNSUPPORTED_PREFIXES = (
    b"similarity index ",
    b"dissimilarity index ",
    b"rename from ",
    b"rename to ",
    b"copy from ",
    b"copy to ",
)
_NO_NEWLINE = b"\\ "
_DEV_NULL = b"/dev/null"


class PatchRejected(ValueError):
    """The patch is rejected; `reason` is a closed code and the message is exactly that code."""

    def __init__(self, reason: str):
        if reason not in PATCH_REJECTIONS:
            raise ValueError("Unknown patch rejection")
        super().__init__(reason)
        self.reason = reason


class PatchUsageError(ValueError):
    """A patch limit is out of range; `limit` names it."""

    def __init__(self, limit: str):
        super().__init__("Invalid counterfactual limit: " + limit)
        self.limit = limit


def normalize_patch_limits(values: Mapping[str, object] | None = None) -> dict[str, int]:
    """Defaults for absent or None entries; unknown or out-of-range entries are rejected."""
    given = dict(values or {})
    for name in given:
        if name not in PATCH_LIMITS:
            raise PatchUsageError(str(name))
    result: dict[str, int] = {}
    for name, (default, low, high) in PATCH_LIMITS.items():
        value = given.get(name)
        value = default if value is None else value
        if type(value) is not int or not low <= value <= high:
            raise PatchUsageError(name)
        result[name] = value
    return result


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    # (tag, content, no_newline): tag is " ", "-" or "+"; content excludes the LF.
    lines: tuple[tuple[str, bytes, bool], ...]

    @property
    def anchor(self) -> int:
        return self.old_start if self.old_count == 0 else self.old_start - 1


@dataclass(frozen=True)
class FilePatch:
    path: str
    op: str
    mode: str | None
    hunks: tuple[Hunk, ...]
    old_blob: str | None = None
    new_blob: str | None = None

    @property
    def added(self) -> int:
        return sum(1 for h in self.hunks for tag, _c, _n in h.lines if tag == "+")

    @property
    def removed(self) -> int:
        return sum(1 for h in self.hunks for tag, _c, _n in h.lines if tag == "-")


@dataclass(frozen=True)
class Patch:
    sha256: str
    size: int
    files: tuple[FilePatch, ...]

    @property
    def files_total(self) -> int:
        return len(self.files)

    @property
    def hunks_total(self) -> int:
        return sum(len(f.hunks) for f in self.files)


def _reject(reason: str) -> PatchRejected:
    return PatchRejected(reason)


# ---- P0: global scans ----------------------------------------------------------------------


def _scan(raw: bytes, limits: Mapping[str, int]) -> list[bytes]:
    if len(raw) > limits["max_patch_bytes"]:
        raise _reject("limit_exceeded")
    if b"\x00" in raw:
        raise _reject("binary_patch")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _reject("not_utf8") from None
    lines = raw.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    # else: a final line without LF is only ever legal inside a body marker; reported below
    if any(line == b"GIT binary patch" or _BINARY.fullmatch(line) for line in lines):
        raise _reject("binary_patch")
    if any(len(line) > limits["max_line_bytes"] for line in lines):
        raise _reject("limit_exceeded")
    if raw and not raw.endswith(b"\n"):
        raise _reject("malformed_patch")
    return lines


# ---- P1: sequential parse -------------------------------------------------------------------


def _valid_path(path: str) -> None:
    if (
        not ev.relative_path(path)
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
        or any(part == ".git" for part in path.split("/"))
    ):
        raise _reject("invalid_path")


def _header_path(line: bytes, side: bytes) -> str | None:
    body = line[4:].split(b"\t", 1)[0]
    if body == _DEV_NULL:
        return None
    if body.startswith(b'"'):
        raise _reject("invalid_path")
    if not body.startswith(side):
        raise _reject("malformed_patch")
    path = body[2:].decode("utf-8")
    _valid_path(path)
    return path


def _mode_value(token: bytes) -> str:
    if _MODE.fullmatch(token) is None:
        raise _reject("malformed_patch")
    mode = token.decode("ascii")
    if mode == "120000":
        raise _reject("symlink_change")
    if mode == "160000":
        raise _reject("submodule_change")
    if mode not in REGULAR_MODES:
        raise _reject("unsupported_mode")
    return mode


def _marker(tags: list[str], marked: list[bool], closed: list[bool]) -> None:
    """Bind a `\\ ` line to the preceding body line (see the module docstring)."""
    if not tags or marked[-1]:
        raise _reject("malformed_patch")
    marked[-1] = True
    if tags[-1] == " ":
        closed[0] = closed[1] = True
    elif tags[-1] == "-":
        closed[0] = True
    else:
        closed[1] = True


def _hunk(lines: list[bytes], at: int) -> tuple[Hunk, int]:
    matched = _HUNK.fullmatch(lines[at])
    if matched is None:
        raise _reject("malformed_patch")
    start, count, new_start, new_count = (
        int(matched.group(1)),
        1 if matched.group(2) is None else int(matched.group(2)),
        int(matched.group(3)),
        1 if matched.group(4) is None else int(matched.group(4)),
    )
    if count > 0 and start == 0:
        raise _reject("malformed_patch")
    at += 1
    old_left, new_left = count, new_count
    tags: list[str] = []
    contents: list[bytes] = []
    marked: list[bool] = []
    closed = [False, False]  # old side ended, new side ended
    total = len(lines)
    while old_left > 0 or new_left > 0:
        if at >= total:
            raise _reject("malformed_patch")
        line = lines[at]
        at += 1
        if line.startswith(_NO_NEWLINE):
            _marker(tags, marked, closed)
            continue
        tag = line[:1]
        if tag == b" ":
            if closed[0] or closed[1] or old_left <= 0 or new_left <= 0:
                raise _reject("malformed_patch")
            old_left -= 1
            new_left -= 1
        elif tag == b"-":
            if closed[0] or old_left <= 0:
                raise _reject("malformed_patch")
            old_left -= 1
        elif tag == b"+":
            if closed[1] or new_left <= 0:
                raise _reject("malformed_patch")
            new_left -= 1
        else:
            raise _reject("malformed_patch")
        tags.append(tag.decode("ascii"))
        contents.append(line[1:])
        marked.append(False)
    if at < total and lines[at].startswith(_NO_NEWLINE):
        _marker(tags, marked, closed)
        at += 1
    if all(tag == " " for tag in tags):
        raise _reject("malformed_patch")
    frozen = tuple(zip(tags, contents, marked, strict=True))
    return Hunk(start, count, new_start, new_count, frozen), at


def _section(
    lines: list[bytes], at: int, hunks_so_far: int, limits: Mapping[str, int]
) -> tuple[FilePatch, int]:
    total = len(lines)
    git = lines[at].startswith(b"diff --git ")
    header_path: str | None = None
    flags: dict[str, bytes] = {}
    old_blob: str | None = None
    new_blob: str | None = None
    index_mode: str | None = None
    mode_pair = False
    if git:
        rest = lines[at][len(b"diff --git ") :]
        if rest.startswith(b'"'):
            raise _reject("invalid_path")
        half = (len(rest) - 1) // 2
        guess = rest[2:half]
        # A renamed or copied path (`a/X b/Y`) is reported by its extended header below.
        header_ok = len(rest) % 2 == 1 and rest == b"a/" + guess + b" b/" + guess
        if header_ok:
            header_path = guess.decode("utf-8")
            _valid_path(header_path)
        at += 1
        while at < total:
            line = lines[at]
            if line.startswith((b"--- ", b"@@", b"diff --git ")) or line == b"-- ":
                break
            if line.startswith(_UNSUPPORTED_PREFIXES):
                raise _reject("unsupported_patch_feature")
            handled = False
            for name, prefix in (
                ("new", b"new file mode "),
                ("deleted", b"deleted file mode "),
                ("old_mode", b"old mode "),
                ("new_mode", b"new mode "),
            ):
                if line.startswith(prefix):
                    if name in flags:
                        raise _reject("malformed_patch")
                    flags[name] = line[len(prefix) :]
                    _mode_value(line[len(prefix) :])
                    mode_pair = mode_pair or name in {"old_mode", "new_mode"}
                    handled = True
                    break
            if not handled:
                found = _INDEX.fullmatch(line)
                if found is None or "index" in flags:
                    raise _reject("malformed_patch")
                flags["index"] = b""
                old_blob, new_blob = found.group(1).decode(), found.group(2).decode()
                if found.group(3) is not None:
                    index_mode = _mode_value(found.group(3))
            at += 1
        if mode_pair:
            raise _reject("unsupported_patch_feature")
        if not header_ok:
            raise _reject("malformed_patch")
    old: str | None
    new: str | None
    has_files = at < total and lines[at].startswith(b"--- ")
    if has_files:
        if at + 1 >= total or not lines[at + 1].startswith(b"+++ "):
            raise _reject("malformed_patch")
        old = _header_path(lines[at], b"a/")
        new = _header_path(lines[at + 1], b"b/")
        at += 2
        if old is None and new is None:
            raise _reject("malformed_patch")
        if old is None:
            op, path = "create", str(new)
        elif new is None:
            op, path = "delete", old
        else:
            if old != new:
                raise _reject("invalid_path")
            op, path = "modify", old
        if git and (
            header_path != path
            or (op == "create") != ("new" in flags)
            or (op == "delete") != ("deleted" in flags)
        ):
            raise _reject("malformed_patch")
        mode: str | None = None
        if git:
            if op == "create":
                mode = flags["new"].decode()
            elif op == "delete":
                mode = flags["deleted"].decode()
            else:
                mode = index_mode
    else:
        if not git or (at < total and lines[at].startswith(b"@@")):
            raise _reject("malformed_patch")
        if ("new" in flags) == ("deleted" in flags):
            raise _reject("malformed_patch")
        path = str(header_path)
        op = "create" if "new" in flags else "delete"
        mode = flags["new" if op == "create" else "deleted"].decode()
    hunks: list[Hunk] = []
    if has_files:
        while at < total and lines[at].startswith(b"@@"):
            if hunks_so_far + len(hunks) >= limits["max_hunks"]:
                raise _reject("limit_exceeded")
            hunk, at = _hunk(lines, at)
            if hunks:
                previous = hunks[-1]
                if hunk.anchor < previous.anchor + previous.old_count:
                    raise _reject("malformed_patch")
            hunks.append(hunk)
        if not hunks:
            raise _reject("malformed_patch")
        if op == "create" and not (
            len(hunks) == 1
            and (hunks[0].old_start, hunks[0].old_count, hunks[0].new_start) == (0, 0, 1)
            and all(tag == "+" for tag, _c, _n in hunks[0].lines)
        ):
            raise _reject("malformed_patch")
        if op == "delete" and not (
            len(hunks) == 1
            and (hunks[0].old_start, hunks[0].new_start, hunks[0].new_count) == (1, 0, 0)
            and all(tag == "-" for tag, _c, _n in hunks[0].lines)
        ):
            raise _reject("malformed_patch")
    return FilePatch(path, op, mode, tuple(hunks), old_blob, new_blob), at


def parse(raw: bytes, limits: Mapping[str, int] | None = None) -> Patch:
    """P0 global scans, then the sequential P1 parse; the first failure wins."""
    effective = normalize_patch_limits(limits)
    lines = _scan(raw, effective)
    total = len(lines)
    at = 0
    while at < total and not lines[at].startswith((b"diff --git ", b"--- ")):
        at += 1
    if at >= total:
        raise _reject("malformed_patch")
    files: list[FilePatch] = []
    seen: set[str] = set()
    hunks = 0
    while at < total:
        line = lines[at]
        if line.startswith((b"diff --git ", b"--- ")):
            if len(files) >= effective["max_files"]:
                raise _reject("limit_exceeded")
            item, at = _section(lines, at, hunks, effective)
            if item.path in seen:
                raise _reject("duplicate_path")
            seen.add(item.path)
            hunks += len(item.hunks)
            files.append(item)
        elif line == b"-- ":
            break
        else:
            raise _reject("malformed_patch")
    return Patch(hashlib.sha256(raw).hexdigest(), len(raw), tuple(files))


# ---- P2: base-relative checks (pure over the base bytes) ------------------------------------


class Scope:
    """Observed base paths plus the patch's created paths, for the scope and collision checks."""

    def __init__(
        self,
        observed: Collection[str],
        creates: Collection[str],
        suffixes: Collection[str],
        skip: Collection[str],
    ):
        self.observed = frozenset(observed)
        self.suffixes = frozenset(suffixes)
        self.skip = frozenset(skip)
        everything = set(self.observed) | set(creates)
        self._files = frozenset(everything)
        directories: set[str] = set()
        for path in everything:
            parts = path.split("/")
            for upto in range(1, len(parts)):
                directories.add("/".join(parts[:upto]))
        self._directories = frozenset(directories)

    def check(self, fp: FilePatch) -> None:
        """Steps 1-3 of P2 for one file: scope, presence in base, creation collisions."""
        path = fp.path
        if PurePosixPath(path).suffix not in self.suffixes or any(
            part in self.skip for part in PurePosixPath(path).parts
        ):
            raise _reject("unsupported_path")
        if fp.op in {"modify", "delete"}:
            if path not in self.observed:
                raise _reject("path_not_in_base")
            return
        if path in self.observed or path in self._directories:
            raise _reject("path_exists_in_base")
        parts = path.split("/")
        if any("/".join(parts[:upto]) in self._files for upto in range(1, len(parts))):
            raise _reject("path_exists_in_base")


def check_scope(
    fp: FilePatch,
    observed: Collection[str],
    suffixes: Collection[str],
    skip: Collection[str],
    creates: Collection[str] = (),
) -> None:
    """One-shot form of `Scope(...).check(fp)`."""
    Scope(observed, creates, suffixes, skip).check(fp)


def git_blob_ids(data: bytes) -> tuple[str, str]:
    """The git blob ids of `data` under SHA-1 and SHA-256 object formats."""
    framed = b"blob %d\x00" % len(data) + data
    return (
        hashlib.sha1(framed, usedforsecurity=False).hexdigest(),
        hashlib.sha256(framed).hexdigest(),
    )


def _split(data: bytes) -> list[bytes]:
    lines: list[bytes] = []
    start = 0
    while True:
        end = data.find(b"\n", start)
        if end < 0:
            break
        lines.append(data[start : end + 1])
        start = end + 1
    if start < len(data):
        lines.append(data[start:])
    return lines


def _zeros(token: str) -> bool:
    return set(token) == {"0"}


def apply(
    fp: FilePatch, base: bytes | None, limits: Mapping[str, int] | None = None
) -> bytes | None:
    """Apply one file patch exactly; the result is the new bytes, or None for a deletion.

    Order of failures: `base_mismatch` (old blob id, anchors, exact context), then
    `limit_exceeded` (result size), then `malformed_patch` (new-side positions, an interior
    line without LF, new blob id).
    """
    effective = normalize_patch_limits(limits)
    data = b"" if base is None else base
    if fp.old_blob is not None:
        if fp.op == "create":
            if not _zeros(fp.old_blob):
                raise _reject("base_mismatch")
        elif not any(ident.startswith(fp.old_blob) for ident in git_blob_ids(data)):
            raise _reject("base_mismatch")
    source = _split(data)
    out: list[bytes] = []
    cursor = 0
    positions: list[int] = []
    for hunk in fp.hunks:
        old = [c + (b"" if nonl else b"\n") for tag, c, nonl in hunk.lines if tag in " -"]
        new = [c + (b"" if nonl else b"\n") for tag, c, nonl in hunk.lines if tag in " +"]
        anchor = hunk.anchor
        if not cursor <= anchor <= len(source):
            raise _reject("base_mismatch")
        out.extend(source[cursor:anchor])
        positions.append(len(out))
        if source[anchor : anchor + len(old)] != old:
            raise _reject("base_mismatch")
        out.extend(new)
        cursor = anchor + len(old)
    out.extend(source[cursor:])
    result = b"".join(out)
    if fp.op == "delete" and result != b"":
        raise _reject("base_mismatch")
    if len(result) > effective["max_result_file_bytes"]:
        raise _reject("limit_exceeded")
    for hunk, at in zip(fp.hunks, positions, strict=True):
        expected = hunk.new_start if hunk.new_count == 0 else hunk.new_start - 1
        if expected != at:
            raise _reject("malformed_patch")
    if any(not item.endswith(b"\n") for item in out[:-1]):
        raise _reject("malformed_patch")
    if fp.new_blob is not None:
        if fp.op == "delete":
            if not _zeros(fp.new_blob):
                raise _reject("malformed_patch")
        elif not any(ident.startswith(fp.new_blob) for ident in git_blob_ids(result)):
            raise _reject("malformed_patch")
    return None if fp.op == "delete" else result
