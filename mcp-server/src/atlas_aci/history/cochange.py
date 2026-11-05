"""Bounded git co-change correlations with commit evidence (ACI-S33).

A co-change signal says that two paths changed in the same eligible commit of an explicit window.
It is a correlation: not a dependency and not causality. The result is a standalone overlay,
`atlas-history-overlay.v1`, that is never part of a graph and never exposed over MCP.

The window is `start..end` (start excluded, end included) of a repository whose SELECTED source
snapshot (a read-only lease) is bound mechanically to one commit: the git blob id of every indexed
file of the snapshot must equal the entry of that commit's tree, and `end` must be that commit or
one of its ancestors.

Git is run through one hardened runner: an argument list (never a shell), a from-scratch
environment, fixed `-c` overrides, bounded output, a per-call timeout and an overall deadline.
A timeout or a deadline rejects; it never yields a partial result. `safe.directory` is never
set, so a repository that git considers to be owned by another user is rejected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from atlas_aci.overlays import runtime
from atlas_aci.query.diff import DiffUsageError, Side
from atlas_aci.storage import evidence as ev

FORMAT = "atlas-history-overlay.v1"
SUMMARY_FORMAT = "atlas-history-summary.v1"
POLICY_FORMAT = "atlas-history-filter.v1"
PRODUCER = {"name": "atlas-aci cochange", "version": "atlas-history-cochange.v1"}
EVIDENCE_KIND = "co_change_correlation"
EVIDENCE_SEMANTICS = "file_pair_co_occurrence_in_eligible_commits"
IDENTITY_RULE = "operator_label_bound_to_snapshot_git_common_dir"
WINDOW_RULE = "start_exclusive_end_inclusive"
BINDING = "indexed_files_match_commit_tree"
CONTAINMENT = "end_commit_is_ancestor_or_equal"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

REJECTIONS = (
    "history_unavailable",
    "unsupported_object_format",
    "repository_not_toplevel",
    "history_output_invalid",
    "snapshot_unavailable",
    "snapshot_repository_mismatch",
    "commit_not_found",
    "source_commit_not_found",
    "outside_snapshot_history",
    "start_not_ancestor_of_end",
    "snapshot_commit_mismatch",
    "snapshot_source_unavailable",
    "source_tree_too_large",
    "pair_limit_exceeded",
    "overlay_too_large",
    "invalid_overlay",
)
INCOMPLETE_REASONS = (
    "commit_budget_exhausted",
    "object_missing",
    "shallow_boundary_in_window",
    "start_commit_missing",
)
COVERAGE_STATES = ("complete", "incomplete_history")
RECORDS_STATES = ("complete", "withheld")
COUNT_KEYS = (
    "commits_in_window",
    "merge_commits",
    "bulk_commits",
    "oversized_commits",
    "empty_commits",
    "single_path_commits",
    "pair_commits",
    "generated_paths",
    "gitlink_paths",
    "unrepresentable_paths",
    "pairs",
    "pair_observations",
)
LIMITATIONS = (
    "correlation_not_dependency_not_causality",
    "renames_not_followed_old_and_new_paths_pair",
    "merge_commits_excluded_including_merge_resolutions",
    "generated_paths_by_declared_prefix_name_suffix_only",
    "incomplete_history_withholds_all_records",
    "snapshot_binding_covers_indexed_files_only_files_added_later_are_not_detected",
    "working_tree_conversions_cause_snapshot_mismatch",
    "repository_owner_must_match_process_user",
    "sha1_object_format_only",
    "repository_id_is_an_operator_label",
    "git_timeouts_reject_never_truncate",
    "legacy_grafts_adding_parents_not_detected",
    "ancestry_across_a_shallow_boundary_reported_conservatively",
    "not_written_to_the_graph_and_not_exposed_over_mcp",
    "damaged_chain_between_end_and_source_may_report_outside_snapshot_history",
    "indexer_ignores_gitignore_so_untracked_indexable_files_reject_the_binding",
)

# name -> (minimum, maximum, default)
LIMITS = {
    "max_commits": (1, 20000, 1000),
    "max_pairs": (1, 1000000, 100000),
    "max_pair_observations": (1, 10000000, 1000000),
    "max_overlay_bytes": (1, 268435456, 67108864),
    "max_tree_bytes": (1, 268435456, 67108864),
    "git_timeout": (1, 600, 30),
    "git_deadline": (1, 86400, 600),
}
POLICY_LIMITS = {
    "bulk_threshold": (2, 1000, 50),
    "diff_output_cap": (4096, 67108864, 4194304),
}
DEFAULT_GENERATED = {
    "prefixes": ("build/", "coverage/", "dist/", "node_modules/", "vendor/"),
    "names": (
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    ),
    "suffixes": (".map", ".min.css", ".min.js", ".pb.go", ".pyc", "_pb2.py", "_pb2_grpc.py"),
}
GENERATED_KINDS = ("prefixes", "names", "suffixes")
GENERATED_USAGE = {
    "prefixes": "generated_prefix",
    "names": "generated_name",
    "suffixes": "generated_suffix",
}
MAX_GENERATED_ITEMS = 256
USAGE_NAMES = (
    "repo",
    "repository_id",
    "start",
    "end",
    "source_commit",
    "snapshot_id",
    "graph_root",
    "bulk_threshold",
    "diff_output_cap",
    "generated_prefix",
    "generated_name",
    "generated_suffix",
    "no_generated_defaults",
    *LIMITS,
    "option",
)
TOP_KEYS = (
    "format",
    "overlay_id",
    "key",
    "producer",
    "repository",
    "window",
    "source_snapshot",
    "filter",
    "evidence",
    "coverage",
    "counts",
    "limitations",
    "records",
)
RECORD_KEYS = (
    "id",
    "type",
    "evidence_kind",
    "a",
    "b",
    "support",
    "supporting_commits",
    "repository_id",
    "start_commit",
    "end_commit",
    "filter_digest",
)
SUMMARY_KEYS = (
    "format",
    "overlay_id",
    "key",
    "repository_id",
    "start_commit",
    "end_commit",
    "filter_digest",
    "evidence_kind",
    "coverage_state",
    "reasons",
    "records",
)

_HEX40 = r"[0-9a-f]{40}"
_LABEL = r"[a-z0-9][a-z0-9._-]{0,63}"
_PREFIX = r"(?:[A-Za-z0-9_.@+-]{1,128}/){1,16}"
_NAME = r"[A-Za-z0-9_.@+-]{1,128}"
_SUFFIX = r"[A-Za-z0-9_.@+-]{1,64}"
_GENERATED_GRAMMAR = {"prefixes": _PREFIX, "names": _NAME, "suffixes": _SUFFIX}
_LISTING_LINE = r"[0-9a-f]{40}(?: [0-9a-f]{40})*"
_TREE_META = r"([0-7]{6}) (blob|commit) ([0-9a-f]{40})"
_RAW_META = r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}) ([0-9a-f]{40}) ([ADMT])"
_GITLINK = "160000"
_FILE_MODES = ("100644", "100755")
PROBE_CAP = 65536
HEADER_CAP = 65536
LINE_CAP = 4100

_PREFIX_ARGV = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.quotePath=false",
    "-c",
    "diff.external=",
    "-c",
    "diff.renames=false",
)
_PROMISOR_PATTERN = r"^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$"


class HistoryRejected(ValueError):
    """The analysis stopped; `reason` is a closed code (one of REJECTIONS)."""

    def __init__(self, reason: str):
        super().__init__("history_rejected: " + reason)
        self.reason = reason


class HistoryUsageError(ValueError):
    """The invocation is invalid; `what` names the option (one of USAGE_NAMES)."""

    def __init__(self, what: str):
        super().__init__("history_usage: " + what)
        self.what = what


def _sha(value: Any) -> str:
    return hashlib.sha256(ev.canonical(value).encode("utf-8")).hexdigest()


def _reject(reason: str) -> HistoryRejected:
    return HistoryRejected(reason)


# ---- options ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    repo: Path
    repository_id: str
    start: str
    end: str
    source_commit: str
    snapshot_id: str
    graph_root: Path | None
    bulk_threshold: int
    diff_output_cap: int
    prefixes: tuple[str, ...]
    names: tuple[str, ...]
    suffixes: tuple[str, ...]
    max_commits: int
    max_pairs: int
    max_pair_observations: int
    max_overlay_bytes: int
    max_tree_bytes: int
    git_timeout: int
    git_deadline: int


_RAW_KEYS = {
    "repo",
    "repository_id",
    "start",
    "end",
    "source_commit",
    "snapshot_id",
    "graph_root",
    "bulk_threshold",
    "diff_output_cap",
    "generated_prefix",
    "generated_name",
    "generated_suffix",
    "no_generated_defaults",
    *LIMITS,
}


def _text(raw: Mapping[str, Any], name: str, pattern: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise HistoryUsageError(name)
    return value


def _bounded(raw: Mapping[str, Any], name: str, table: Mapping[str, tuple[int, int, int]]) -> int:
    minimum, maximum, default = table[name]
    value = raw.get(name)
    if value is None:
        return default
    if type(value) is not int or not minimum <= value <= maximum:
        raise HistoryUsageError(name)
    return value


def _items(raw: Mapping[str, Any], kind: str, no_defaults: bool) -> tuple[str, ...]:
    name = GENERATED_USAGE[kind]
    given = raw.get(name)
    if given is None:
        given = ()
    if isinstance(given, str) or not isinstance(given, Sequence):
        raise HistoryUsageError(name)
    if len(given) > MAX_GENERATED_ITEMS:
        raise HistoryUsageError(name)
    for item in given:
        if not isinstance(item, str) or re.fullmatch(_GENERATED_GRAMMAR[kind], item) is None:
            raise HistoryUsageError(name)
        if kind != "suffixes" and any(part in {".", ".."} for part in item.split("/")):
            raise HistoryUsageError(name)
    base = () if no_defaults else DEFAULT_GENERATED[kind]
    return tuple(sorted(set(base) | set(given)))


def normalize_options(raw: Mapping[str, Any]) -> Options:
    """Validate every option before anything is read or run; raises HistoryUsageError."""
    if not isinstance(raw, Mapping) or set(raw) - _RAW_KEYS:
        raise HistoryUsageError("option")
    repo = raw.get("repo")
    if not isinstance(repo, (str, Path)):
        raise HistoryUsageError("repo")
    try:
        resolved = Path(repo).resolve()
    except (OSError, RuntimeError):
        raise HistoryUsageError("repo") from None
    graph_root = raw.get("graph_root")
    if graph_root is not None and not isinstance(graph_root, (str, Path)):
        raise HistoryUsageError("graph_root")
    no_defaults = raw.get("no_generated_defaults", False)
    if no_defaults is None:
        no_defaults = False
    if type(no_defaults) is not bool:
        raise HistoryUsageError("no_generated_defaults")
    return Options(
        repo=resolved,
        repository_id=_text(raw, "repository_id", _LABEL),
        start=_text(raw, "start", _HEX40),
        end=_text(raw, "end", _HEX40),
        source_commit=_text(raw, "source_commit", _HEX40),
        snapshot_id=_text(raw, "snapshot_id", r"[0-9a-f]{64}"),
        graph_root=None if graph_root is None else Path(graph_root),
        bulk_threshold=_bounded(raw, "bulk_threshold", POLICY_LIMITS),
        diff_output_cap=_bounded(raw, "diff_output_cap", POLICY_LIMITS),
        prefixes=_items(raw, "prefixes", no_defaults),
        names=_items(raw, "names", no_defaults),
        suffixes=_items(raw, "suffixes", no_defaults),
        max_commits=_bounded(raw, "max_commits", LIMITS),
        max_pairs=_bounded(raw, "max_pairs", LIMITS),
        max_pair_observations=_bounded(raw, "max_pair_observations", LIMITS),
        max_overlay_bytes=_bounded(raw, "max_overlay_bytes", LIMITS),
        max_tree_bytes=_bounded(raw, "max_tree_bytes", LIMITS),
        git_timeout=_bounded(raw, "git_timeout", LIMITS),
        git_deadline=_bounded(raw, "git_deadline", LIMITS),
    )


# ---- the filter policy -----------------------------------------------------------------------


def filter_policy(options: Options) -> dict[str, Any]:
    """The canonical filter policy document (the limits outside it are not part of the digest)."""
    return {
        "format": POLICY_FORMAT,
        "merge_commits": "excluded",
        "generated": {
            "prefixes": list(options.prefixes),
            "names": list(options.names),
            "suffixes": list(options.suffixes),
        },
        "bulk": {
            "threshold": options.bulk_threshold,
            "counts": "eligible_paths_after_generated",
            "rule": "exclude_commit_when_count_exceeds_threshold",
        },
        "oversized": {"diff_output_cap_bytes": options.diff_output_cap, "rule": "exclude_commit"},
        "renames": "not_followed",
        "deleted_paths": "included",
        "type_changes": "included",
        "gitlinks": "excluded",
        "unrepresentable_paths": "excluded",
        "path_identity": "git_tree_path_utf8_exact",
    }


def filter_digest(policy: Mapping[str, Any]) -> str:
    return hashlib.sha256((POLICY_FORMAT + ":" + ev.canonical(policy)).encode("utf-8")).hexdigest()


def record_id(repository_id: str, start: str, end: str, digest: str, a: str, b: str) -> str:
    payload = ev.canonical([repository_id, start, end, digest, a, b])
    return hashlib.sha256(("atlas-history-record.v1:" + payload).encode("utf-8")).hexdigest()


def overlay_key(repository_id: str, start: str, end: str, digest: str) -> str:
    """Keyed by repository identity (label), window and filter digest, plus the producer."""
    payload = ev.canonical(
        {
            "repository_id": repository_id,
            "start_commit": start,
            "end_commit": end,
            "filter_digest": digest,
            "producer_version": PRODUCER["version"],
        }
    )
    return hashlib.sha256(("atlas-history-key.v1:" + payload).encode("utf-8")).hexdigest()


def overlay_identity(overlay: Mapping[str, Any]) -> str:
    rest = {k: v for k, v in overlay.items() if k != "overlay_id"}
    return hashlib.sha256((FORMAT + ":" + ev.canonical(rest)).encode("utf-8")).hexdigest()


def canonical_bytes(document: Any) -> bytes:
    return runtime.canonical_bytes(document)


# ---- the git runner --------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    """A from-scratch environment: only PATH is copied; nothing else of the caller's leaks in."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "none",
        "GIT_GRAFT_FILE": "/dev/null",
    }


def _kill(proc: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with suppress(OSError):
        proc.kill()


def _expire(proc: subprocess.Popen[bytes], fired: list[bool]) -> None:
    fired.append(True)
    _kill(proc)


class _Run:
    """One analysis: the options, the repository and the overall deadline."""

    def __init__(self, options: Options):
        self.options = options
        self.deadline = time.monotonic() + options.git_deadline

    def git(self, args: Sequence[str], cap: int) -> tuple[int, bytes, bool]:
        """Run git; return (returncode, stdout up to `cap` bytes, overflow).

        The overflow flag is decided before the return code is meaningful: a process killed for
        exceeding the cap has no usable code. A timeout or an overall deadline never returns
        partial output; it rejects as `history_unavailable`.
        """
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise _reject("history_unavailable")
        argv = ["git", "--no-optional-locks", "-C", str(self.options.repo), *_PREFIX_ARGV, *args]
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_git_env(),
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise _reject("history_unavailable") from None
        fired: list[bool] = []
        timer = threading.Timer(min(self.options.git_timeout, left), _expire, args=(proc, fired))
        timer.daemon = True
        timer.start()
        data = b""
        try:
            assert proc.stdout is not None
            data = proc.stdout.read(cap + 1)
            overflow = len(data) > cap
            if overflow:
                _kill(proc)
        finally:
            timer.cancel()
            timer.join()
            if proc.stdout is not None:
                proc.stdout.close()
            code = proc.wait()
        if fired:
            raise _reject("history_unavailable")
        return code, data[:cap], overflow


# ---- probing and small git questions ---------------------------------------------------------


@dataclass(frozen=True)
class _Probe:
    toplevel: str
    common_dir: str
    shallow: bool


def _lines(data: bytes) -> list[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _reject("history_output_invalid") from None
    if not text.endswith("\n"):
        raise _reject("history_output_invalid")
    return text[:-1].split("\n")


def _probe(run: _Run) -> _Probe:
    args = [
        "rev-parse",
        "--show-toplevel",
        "--absolute-git-dir",
        "--git-common-dir",
        "--is-shallow-repository",
        "--is-bare-repository",
        "--show-object-format",
    ]
    code, data, overflow = run.git(args, PROBE_CAP)
    if overflow:
        raise _reject("history_output_invalid")
    if code != 0:
        raise _reject("history_unavailable")
    lines = _lines(data)
    if len(lines) != 6 or lines[3] not in {"true", "false"} or lines[4] not in {"true", "false"}:
        raise _reject("history_output_invalid")
    if lines[5] != "sha1":
        raise _reject("unsupported_object_format")
    repo = run.options.repo
    try:
        top = str(Path(lines[0]).resolve())
        common = str((repo / lines[2]).resolve())
    except (OSError, RuntimeError, ValueError):
        raise _reject("history_output_invalid") from None
    if top != str(repo):
        raise _reject("repository_not_toplevel")
    code, _unused, overflow = run.git(
        ["config", "--local", "--get-regexp", _PROMISOR_PATTERN], PROBE_CAP
    )
    if overflow or code != 1:
        # A partial clone can fetch lazily and so run repository-configured programs.
        raise _reject("history_unavailable")
    return _Probe(toplevel=top, common_dir=common, shallow=lines[3] == "true")


def _exists(run: _Run, sha: str) -> bool:
    code, _data, overflow = run.git(["cat-file", "-e", "--end-of-options", sha + "^{commit}"], 0)
    if overflow:
        raise _reject("history_output_invalid")
    if code == 0:
        return True
    if code in {1, 128}:
        return False
    raise _reject("history_unavailable")


def _end_in_source(run: _Run) -> bool:
    end, source = run.options.end, run.options.source_commit
    if end == source:
        return True
    args = ["merge-base", "--is-ancestor", "--end-of-options", end, source]
    code, _data, overflow = run.git(args, 0)
    if overflow or code not in {0, 1}:
        raise _reject("history_unavailable")
    return code == 0


@dataclass(frozen=True)
class _Listing:
    state: str  # "full", "truncated" or "object_missing"
    parents: dict[str, tuple[str, ...]]


def _list(run: _Run) -> _Listing:
    opts = run.options
    limit = opts.max_commits
    args = [
        "rev-list",
        "--parents",
        "--max-count=" + str(limit + 1),
        "--end-of-options",
        opts.end,
        "^" + opts.start,
    ]
    code, data, overflow = run.git(args, (limit + 1) * LINE_CAP)
    if overflow:
        raise _reject("history_output_invalid")
    if code != 0:
        return _Listing("object_missing", {})
    if not data:
        return _Listing("full", {})
    rows = _lines(data)
    parents: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if re.fullmatch(_LISTING_LINE, row) is None:
            raise _reject("history_output_invalid")
        names = row.split(" ")
        if names[0] in parents:
            raise _reject("history_output_invalid")
        parents[names[0]] = tuple(names[1:])
    if len(rows) > limit:
        return _Listing("truncated", {})
    if opts.end not in parents:
        raise _reject("history_output_invalid")
    return _Listing("full", parents)


def _raw_parents_present(run: _Run, sha: str) -> bool | None:
    """True when the raw commit has a parent header; None when the commit cannot be read."""
    code, data, overflow = run.git(["cat-file", "commit", sha], HEADER_CAP)
    cut = data.find(b"\n\n")
    if cut >= 0:
        header = data[: cut + 1]  # a header within the cap is a success even if the body is not
    elif overflow:
        raise _reject("history_output_invalid")
    elif code != 0:
        return None
    else:
        header = data
    return any(row.startswith(b"parent ") for row in header.split(b"\n"))


# ---- the source tree and the snapshot binding ------------------------------------------------


def _tree(run: _Run) -> dict[bytes, tuple[str, str, str]]:
    args = ["ls-tree", "-r", "-z", "--full-tree", "--end-of-options", run.options.source_commit]
    code, data, overflow = run.git(args, run.options.max_tree_bytes)
    if overflow:
        raise _reject("source_tree_too_large")
    if code != 0:
        raise _reject("history_unavailable")
    result: dict[bytes, tuple[str, str, str]] = {}
    if not data:
        return result
    if not data.endswith(b"\0"):
        raise _reject("history_output_invalid")
    for entry in data[:-1].split(b"\0"):
        meta, tab, path = entry.partition(b"\t")
        try:
            text = meta.decode("ascii")
        except UnicodeDecodeError:
            raise _reject("history_output_invalid") from None
        found = re.fullmatch(_TREE_META, text)
        if not tab or not path or found is None or path in result:
            raise _reject("history_output_invalid")
        result[path] = (found.group(1), found.group(2), found.group(3))
    return result


def _observations(reader: Any) -> list[dict[str, Any]]:
    try:
        row = reader.db.execute(runtime.OBSERVATIONS_SQL).fetchone()
        loaded = json.loads(row[0]) if row else None
    except (sqlite3.Error, TypeError, ValueError):
        raise _reject("snapshot_unavailable") from None
    if not isinstance(loaded, list):
        raise _reject("snapshot_unavailable")
    found = [r for r in loaded if isinstance(r, dict) and r.get("type") == "observation"]
    for item in found:
        path, state = item.get("path"), item.get("state")
        if not ev.relative_path(path) or state not in {
            "indexed",
            "disabled",
            "unsupported",
            "failed",
        }:
            raise _reject("snapshot_unavailable")
        if state == "indexed" and not ev.hash_value(item.get("observed_source_sha256")):
            raise _reject("snapshot_unavailable")
    return sorted(found, key=lambda item: item["path"])


def _bind(run: _Run, reader: Any) -> int:
    """Every indexed file of the leased snapshot must be the source commit's blob; returns count."""
    records = _observations(reader)
    tree = _tree(run)
    compared = 0
    for item in records:
        try:
            raw_path = item["path"].encode("utf-8")
        except UnicodeEncodeError:
            raise _reject("snapshot_commit_mismatch") from None
        entry = tree.get(raw_path)
        if entry is None or entry[1] != "blob" or entry[0] not in _FILE_MODES:
            raise _reject("snapshot_commit_mismatch")
        if item["state"] != "indexed":
            continue
        try:
            data = reader._source_lease.read(item["path"], item["observed_source_sha256"])
        except (ValueError, RuntimeError, OSError, KeyError):
            raise _reject("snapshot_source_unavailable") from None
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
        if blob != entry[2]:
            raise _reject("snapshot_commit_mismatch")
        compared += 1
    return compared


def _lease(stack: ExitStack, options: Options, probe: _Probe) -> Any:
    try:
        reader = runtime.open_active(stack, Side(options.repo, options.graph_root))
    except runtime.OverlayRejected:
        raise _reject("snapshot_unavailable") from None
    except DiffUsageError:
        raise
    except (sqlite3.Error, OSError, RuntimeError):
        raise _reject("snapshot_unavailable") from None
    try:
        epoch = reader._lease_schema_epoch
        context = reader._source_lease.manifest["context"]["identity"]
        same_id = reader.snapshot_id == options.snapshot_id
        kind = context["kind"]
        common, worktree = context.get("common_dir"), context.get("worktree_root")
        root = context.get("root")
    except (AttributeError, KeyError, TypeError):
        raise _reject("snapshot_unavailable") from None
    if not same_id or epoch != max(runtime.SCHEMA_EPOCHS):
        raise _reject("snapshot_unavailable")
    try:
        root_ok = str(Path(root).resolve()) == probe.toplevel
    except (OSError, RuntimeError, ValueError, TypeError):
        root_ok = False
    if kind != "git" or common != probe.common_dir or worktree != probe.toplevel or not root_ok:
        raise _reject("snapshot_repository_mismatch")
    return reader


# ---- filters and pairs -----------------------------------------------------------------------


def _generated(options: Options, path: str) -> bool:
    if any(path.startswith(prefix) for prefix in options.prefixes):
        return True
    if path.rsplit("/", 1)[-1] in options.names:
        return True
    return any(path.endswith(suffix) for suffix in options.suffixes)


@dataclass
class _Counts:
    merge: int = 0
    bulk: int = 0
    oversized: int = 0
    empty: int = 0
    single: int = 0
    pair: int = 0
    generated: int = 0
    gitlink: int = 0
    unrepresentable: int = 0


def _parse_raw(data: bytes) -> list[tuple[str, str, bytes]]:
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise _reject("history_output_invalid")
    parts = data[:-1].split(b"\0")
    if len(parts) % 2:
        raise _reject("history_output_invalid")
    entries = []
    for index in range(0, len(parts), 2):
        try:
            meta = parts[index].decode("ascii")
        except UnicodeDecodeError:
            raise _reject("history_output_invalid") from None
        found = re.fullmatch(_RAW_META, meta)
        if found is None or not parts[index + 1]:
            raise _reject("history_output_invalid")
        entries.append((found.group(1), found.group(2), parts[index + 1]))
    return entries


def _eligible(
    options: Options, entries: list[tuple[str, str, bytes]], counts: _Counts
) -> list[str]:
    paths: set[str] = set()
    for old_mode, new_mode, raw in entries:
        if _GITLINK in (old_mode, new_mode):
            counts.gitlink += 1
            continue
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            counts.unrepresentable += 1
            continue
        if not ev.relative_path(path):
            counts.unrepresentable += 1
        elif _generated(options, path):
            counts.generated += 1
        else:
            paths.add(path)
    return sorted(paths)


def _diff_commit(run: _Run, parent: str, sha: str) -> tuple[int, bytes, bool]:
    args = [
        "diff-tree",
        "-r",
        "-z",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
        "--end-of-options",
        parent,
        sha,
    ]
    return run.git(args, run.options.diff_output_cap)


# ---- the analysis ----------------------------------------------------------------------------


def _coverage(reasons: set[str], budget: int | None) -> dict[str, Any]:
    done = not reasons
    return {
        "state": "complete" if done else "incomplete_history",
        "reasons": sorted(reasons),
        "records": "complete" if done else "withheld",
        "commit_budget": budget if "commit_budget_exhausted" in reasons else None,
    }


def _walk(
    run: _Run, listing: _Listing, reasons: set[str]
) -> tuple[_Counts, dict[tuple[str, str], list[str]]] | None:
    """Raw-parent and diff phases (full listing only); None when history proved incomplete."""
    options = run.options
    parents = listing.parents
    roots = sorted(sha for sha, found in parents.items() if not found)
    for sha in roots:
        present = _raw_parents_present(run, sha)
        if present is None:
            reasons.add("object_missing")
        elif present:
            reasons.add("shallow_boundary_in_window")
    if reasons:
        return None
    counts = _Counts()
    pairs: dict[tuple[str, str], list[str]] = {}
    observations = 0
    for sha in sorted(parents):
        found = parents[sha]
        if len(found) > 1:
            counts.merge += 1
            continue
        code, data, overflow = _diff_commit(run, found[0] if found else EMPTY_TREE, sha)
        if overflow:
            counts.oversized += 1
            continue
        if code != 0:
            reasons.add("object_missing")
            return None
        paths = _eligible(options, _parse_raw(data), counts)
        if len(paths) > options.bulk_threshold:
            counts.bulk += 1
        elif not paths:
            counts.empty += 1
        elif len(paths) == 1:
            counts.single += 1
        else:
            counts.pair += 1
            for key in combinations(paths, 2):
                observations += 1
                if observations > options.max_pair_observations:
                    raise _reject("pair_limit_exceeded")
                pairs.setdefault(key, []).append(sha)
                if len(pairs) > options.max_pairs:
                    raise _reject("pair_limit_exceeded")
    return counts, pairs


def _records(options: Options, digest: str, pairs: Mapping[tuple[str, str], list[str]]) -> list:
    result = []
    for a, b in sorted(pairs):
        commits = sorted(pairs[(a, b)])
        result.append(
            {
                "id": record_id(options.repository_id, options.start, options.end, digest, a, b),
                "type": "co_change",
                "evidence_kind": EVIDENCE_KIND,
                "a": a,
                "b": b,
                "support": len(commits),
                "supporting_commits": commits,
                "repository_id": options.repository_id,
                "start_commit": options.start,
                "end_commit": options.end,
                "filter_digest": digest,
            }
        )
    return result


def cochange(options: Options | Mapping[str, Any]) -> dict[str, Any]:
    """Compute the co-change overlay of one window; raises HistoryRejected or HistoryUsageError."""
    opts = options if isinstance(options, Options) else normalize_options(options)
    run = _Run(opts)
    probe = _probe(run)
    reasons: set[str] = set()
    budget: int | None = None
    listing = _Listing("object_missing", {})
    with ExitStack() as stack:
        reader = _lease(stack, opts, probe)
        if not _exists(run, opts.end):
            raise _reject("commit_not_found")
        if not _exists(run, opts.source_commit):
            raise _reject("source_commit_not_found")
        if opts.start == opts.end:
            raise _reject("start_not_ancestor_of_end")
        if not _exists(run, opts.start):
            if not probe.shallow:
                raise _reject("commit_not_found")
            reasons.add("start_commit_missing")
        else:
            # The listing comes before any ancestry conclusion: start < end is read from it.
            listing = _list(run)
            if listing.state == "object_missing":
                reasons.add("object_missing")
            elif listing.state == "truncated":
                reasons.add("commit_budget_exhausted")
                budget = opts.max_commits
        if not _end_in_source(run):
            raise _reject("outside_snapshot_history")
        if listing.state == "full" and not any(
            opts.start in found for found in listing.parents.values()
        ):
            raise _reject("start_not_ancestor_of_end")
        compared = _bind(run, reader)
    policy = filter_policy(opts)
    digest = filter_digest(policy)
    counts: dict[str, int] | None = None
    records: list[dict[str, Any]] = []
    if not reasons:
        outcome = _walk(run, listing, reasons)
        if outcome is not None:
            tally, pairs = outcome
            records = _records(opts, digest, pairs)
            counts = {
                "commits_in_window": len(listing.parents),
                "merge_commits": tally.merge,
                "bulk_commits": tally.bulk,
                "oversized_commits": tally.oversized,
                "empty_commits": tally.empty,
                "single_path_commits": tally.single,
                "pair_commits": tally.pair,
                "generated_paths": tally.generated,
                "gitlink_paths": tally.gitlink,
                "unrepresentable_paths": tally.unrepresentable,
                "pairs": len(records),
                "pair_observations": sum(r["support"] for r in records),
            }
    overlay: dict[str, Any] = {
        "format": FORMAT,
        "key": overlay_key(opts.repository_id, opts.start, opts.end, digest),
        "producer": dict(PRODUCER),
        "repository": {
            "id": opts.repository_id,
            "kind": "git",
            "object_format": "sha1",
            "identity_rule": IDENTITY_RULE,
        },
        "window": {"start_commit": opts.start, "end_commit": opts.end, "rule": WINDOW_RULE},
        "source_snapshot": {
            "snapshot_id": opts.snapshot_id,
            "commit": opts.source_commit,
            "binding": BINDING,
            "containment": CONTAINMENT,
            "files_compared": compared,
        },
        "filter": {"policy": policy, "filter_digest": digest},
        "evidence": {
            "kind": EVIDENCE_KIND,
            "semantics": EVIDENCE_SEMANTICS,
            "direction": "undirected",
            "dependency_claim": "none",
            "causality_claim": "none",
        },
        "coverage": _coverage(reasons, budget),
        "counts": counts,
        "limitations": list(LIMITATIONS),
        "records": records if not reasons else [],
    }
    overlay["overlay_id"] = overlay_identity(overlay)
    ordered = {key: overlay[key] for key in TOP_KEYS}
    validate_overlay(ordered)
    if len(canonical_bytes(ordered)) > opts.max_overlay_bytes:
        raise _reject("overlay_too_large")
    return ordered


# ---- validation ------------------------------------------------------------------------------


def _fail() -> HistoryRejected:
    return HistoryRejected("invalid_overlay")


def _need(condition: bool) -> None:
    if not condition:
        raise _fail()


def _match(value: Any, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _closed(value: Any, keys: Sequence[str]) -> dict[str, Any]:
    _need(isinstance(value, dict) and set(value) == set(keys))
    return value


def _is_int(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _check_policy(policy: Any) -> str:
    keys = (
        "format",
        "merge_commits",
        "generated",
        "bulk",
        "oversized",
        "renames",
        "deleted_paths",
        "type_changes",
        "gitlinks",
        "unrepresentable_paths",
        "path_identity",
    )
    document = _closed(policy, keys)
    constants = {
        "format": POLICY_FORMAT,
        "merge_commits": "excluded",
        "renames": "not_followed",
        "deleted_paths": "included",
        "type_changes": "included",
        "gitlinks": "excluded",
        "unrepresentable_paths": "excluded",
        "path_identity": "git_tree_path_utf8_exact",
    }
    _need(all(document[name] == value for name, value in constants.items()))
    generated = _closed(document["generated"], GENERATED_KINDS)
    for kind in GENERATED_KINDS:
        items = generated[kind]
        _need(isinstance(items, list) and len(items) <= MAX_GENERATED_ITEMS + 64)
        _need(all(_match(item, _GENERATED_GRAMMAR[kind]) for item in items))
        _need(items == sorted(set(items)))
        if kind != "suffixes":
            _need(all(part not in {".", ".."} for item in items for part in item.split("/")))
    bulk = _closed(document["bulk"], ("threshold", "counts", "rule"))
    low, high, _default = POLICY_LIMITS["bulk_threshold"]
    _need(type(bulk["threshold"]) is int and low <= bulk["threshold"] <= high)
    _need(bulk["counts"] == "eligible_paths_after_generated")
    _need(bulk["rule"] == "exclude_commit_when_count_exceeds_threshold")
    oversized = _closed(document["oversized"], ("diff_output_cap_bytes", "rule"))
    low, high, _default = POLICY_LIMITS["diff_output_cap"]
    cap = oversized["diff_output_cap_bytes"]
    _need(type(cap) is int and low <= cap <= high and oversized["rule"] == "exclude_commit")
    return filter_digest(document)


def _check_coverage(overlay: Mapping[str, Any]) -> bool:
    coverage = _closed(overlay["coverage"], ("state", "reasons", "records", "commit_budget"))
    reasons = coverage["reasons"]
    _need(isinstance(reasons, list) and reasons == sorted(set(reasons)))
    _need(all(item in INCOMPLETE_REASONS for item in reasons))
    complete = coverage["state"] == "complete"
    _need(coverage["state"] in COVERAGE_STATES and coverage["records"] in RECORDS_STATES)
    _need(complete == (not reasons) and complete == (coverage["records"] == "complete"))
    if "commit_budget_exhausted" in reasons:
        _need(reasons == ["commit_budget_exhausted"] and _is_int(coverage["commit_budget"], 1))
    else:
        _need(coverage["commit_budget"] is None)
    return complete


def _check_counts(overlay: Mapping[str, Any], complete: bool) -> None:
    counts = overlay["counts"]
    records = overlay["records"]
    if not complete:
        _need(counts is None and records == [])
        return
    document = _closed(counts, COUNT_KEYS)
    _need(all(_is_int(document[name]) for name in COUNT_KEYS))
    parts = ("merge", "bulk", "oversized", "empty", "single_path", "pair")
    total = sum(document[name + "_commits"] for name in parts)
    _need(document["commits_in_window"] == total)
    _need(document["pairs"] == len(records))
    _need(document["pair_observations"] == sum(item["support"] for item in records))


def _check_records(overlay: Mapping[str, Any], digest: str) -> None:
    repository_id = overlay["repository"]["id"]
    start, end = overlay["window"]["start_commit"], overlay["window"]["end_commit"]
    records = overlay["records"]
    _need(isinstance(records, list))
    previous: tuple[str, str] | None = None
    for item in records:
        row = _closed(item, RECORD_KEYS)
        _need(row["type"] == "co_change" and row["evidence_kind"] == EVIDENCE_KIND)
        _need(row["repository_id"] == repository_id and row["filter_digest"] == digest)
        _need(row["start_commit"] == start and row["end_commit"] == end)
        a, b = row["a"], row["b"]
        _need(ev.relative_path(a) and ev.relative_path(b) and a < b)
        _need(previous is None or previous < (a, b))
        previous = (a, b)
        commits = row["supporting_commits"]
        _need(isinstance(commits, list) and commits == sorted(set(commits)) and len(commits) >= 1)
        _need(all(_match(sha, _HEX40) for sha in commits))
        _need(type(row["support"]) is int and row["support"] == len(commits))
        _need(row["id"] == record_id(repository_id, start, end, digest, a, b))


def validate_overlay(overlay: Any) -> None:
    """Recompute every derived field; raises HistoryRejected("invalid_overlay") on any drift."""
    document = _closed(overlay, TOP_KEYS)
    _need(document["format"] == FORMAT and document["producer"] == PRODUCER)
    repository = _closed(document["repository"], ("id", "kind", "object_format", "identity_rule"))
    _need(_match(repository["id"], _LABEL) and repository["kind"] == "git")
    _need(repository["object_format"] == "sha1" and repository["identity_rule"] == IDENTITY_RULE)
    window = _closed(document["window"], ("start_commit", "end_commit", "rule"))
    _need(_match(window["start_commit"], _HEX40) and _match(window["end_commit"], _HEX40))
    _need(window["rule"] == WINDOW_RULE and window["start_commit"] != window["end_commit"])
    source = _closed(
        document["source_snapshot"],
        ("snapshot_id", "commit", "binding", "containment", "files_compared"),
    )
    _need(ev.hash_value(source["snapshot_id"]) and _match(source["commit"], _HEX40))
    _need(source["binding"] == BINDING and source["containment"] == CONTAINMENT)
    _need(_is_int(source["files_compared"]))
    filtered = _closed(document["filter"], ("policy", "filter_digest"))
    digest = _check_policy(filtered["policy"])
    _need(filtered["filter_digest"] == digest)
    evidence = _closed(
        document["evidence"],
        ("kind", "semantics", "direction", "dependency_claim", "causality_claim"),
    )
    _need(evidence["kind"] == EVIDENCE_KIND and evidence["semantics"] == EVIDENCE_SEMANTICS)
    _need(evidence["direction"] == "undirected" and evidence["dependency_claim"] == "none")
    _need(evidence["causality_claim"] == "none")
    complete = _check_coverage(document)
    _check_records(document, digest)
    _check_counts(document, complete)
    _need(document["limitations"] == list(LIMITATIONS))
    _need(
        document["key"]
        == overlay_key(repository["id"], window["start_commit"], window["end_commit"], digest)
    )
    _need(document["overlay_id"] == overlay_identity(document))
    _need(not _names_a_relation(document))


def _names_a_relation(document: Any) -> bool:
    """True when any key is `relation` or any string value is a graph relation name."""
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if "relation" in node or "relations" in node:
                return True
            # Paths and path lists are data, not enumerated values.
            pending.extend(
                v
                for k, v in node.items()
                if k not in {"a", "b", "generated", "id", "repository_id"}
            )
        elif isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, str) and node in ev.RELATIONS:
            return True
    return False


# ---- the stdout summary ----------------------------------------------------------------------


def summary_document(overlay: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "overlay_id": overlay["overlay_id"],
        "key": overlay["key"],
        "repository_id": overlay["repository"]["id"],
        "start_commit": overlay["window"]["start_commit"],
        "end_commit": overlay["window"]["end_commit"],
        "filter_digest": overlay["filter"]["filter_digest"],
        "evidence_kind": overlay["evidence"]["kind"],
        "coverage_state": overlay["coverage"]["state"],
        "reasons": list(overlay["coverage"]["reasons"]),
        "records": len(overlay["records"]),
    }


def validate_summary(summary: Any) -> None:
    document = _closed(summary, SUMMARY_KEYS)
    _need(document["format"] == SUMMARY_FORMAT and document["evidence_kind"] == EVIDENCE_KIND)
    _need(ev.hash_value(document["overlay_id"]) and ev.hash_value(document["key"]))
    _need(_match(document["repository_id"], _LABEL) and ev.hash_value(document["filter_digest"]))
    _need(_match(document["start_commit"], _HEX40) and _match(document["end_commit"], _HEX40))
    reasons = document["reasons"]
    _need(isinstance(reasons, list) and all(item in INCOMPLETE_REASONS for item in reasons))
    _need(document["coverage_state"] in COVERAGE_STATES and _is_int(document["records"]))
    _need((document["coverage_state"] == "complete") == (not reasons))
