"""Import version-matched per-test Python coverage observations (ACI-S21).

Input is one coverage.py JSON report (``coverage json --show-contexts``, format 3) produced by
pytest-cov with ``--cov-context=test``, plus an ``atlas-coverage-manifest.v1`` sidecar that
binds the run to the exact source bytes it measured. Nothing is executed: no test, no
interpreter, no coverage.py. The only Atlas module this file may import is the evidence model.

A recorded observation is *observed execution* and nothing more: it is never an assertion
about behavior, never a pass/fail outcome, and never traversable or exposed over MCP.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import stat
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atlas_aci.storage import evidence as ev

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

MANIFEST_FORMAT = "atlas-coverage-manifest.v1"
ENVIRONMENT_FORMAT = ev.COVERAGE_ENVIRONMENT_FORMAT
IMPORT_FORMAT = "atlas-coverage-import.v1"
RUN_FORMAT = ev.COVERAGE_RUN_FORMAT
STATUS_FORMAT = "atlas-execution-observations.v1"
REPORT_FORMAT = 3
CONTEXT_MODE = "pytest-cov:test"
PHASES = ev.COVERAGE_PHASES
ROLE_PREFIX = ev.COVERAGE_ROLE_PREFIX
RELATION = ev.COVERAGE_RELATION
TEST_KIND = ev.COVERAGE_TEST_KIND
EXECUTION_KIND = ev.COVERAGE_EXECUTION_KIND

# Qualified producers (ACI-S21 Day 1). Widening requires a newly qualified fixture.
PRODUCERS: dict[str, dict[str, dict[str, Any]]] = {
    "coverage.py": {
        "7.16.2": {
            "report_format": REPORT_FORMAT,
            "context_mode": CONTEXT_MODE,
            "pytest-cov": frozenset({"7.1.0"}),
        }
    }
}

# Operator-visible limits. The defaults are the Day-3 measured values (cost evidence in
# .spectra/evidence/aci-s21/cost.json): coverage rows enter every reader lease's snapshot
# binding and retrieval closure, so they are bounded per run and per published generation.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_FILES = 20_000
MAX_TESTS = 100_000
MAX_NODEID_BYTES = 4096
MAX_EXECUTES_PER_RUN = 50_000
MAX_EXECUTES_PER_GENERATION = 100_000
MAX_RECORDS_PER_GENERATION = 3 * MAX_EXECUTES_PER_GENERATION + MAX_TESTS
MAX_RUNS = 16
MAX_LINE = 10_000_000

LIMITATIONS = {
    "test_outcome": "pass_fail_not_recorded",
    "semantics": "execution_is_not_an_assertion_about_behavior",
    "absence": "unexecuted_or_unmeasured_lines_carry_no_evidence",
    "unattributed": "lines_without_test_context_not_attributed",
    "import_time": "import_time_lines_attributed_to_active_context",
    "granularity": "statement_start_lines_only",
    "branches": "branch_arcs_not_imported",
    "producer": "only_qualified_coverage_py_json_format_3_pytest_cov_test_contexts",
    "environment": "environment_is_producer_attested_only",
    "exposure": "not_traversable_not_exposed_over_mcp",
    "chaining": "does_not_combine_with_scip_import",
    "portability": "jsonl_round_trip_keeps_records_drops_run_block",
    "commit": "producer_commit_not_captured",
}

_LINE_KEY = re.compile(r"[1-9][0-9]*")
_LINE_BREAKS = re.compile(rb"\r\n|\r|\n")
_ROOTDIR_HINT = (
    "pytest nodeids are relative to its rootdir: run pytest with --rootdir equal to the "
    "repository root"
)


class CoverageImportError(ValueError):
    """A rejected coverage submission; ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _fail(code: str, message: str) -> CoverageImportError:
    return CoverageImportError(code, message)


@dataclass(frozen=True)
class Limits:
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES
    max_manifest_bytes: int = MAX_MANIFEST_BYTES
    max_files: int = MAX_FILES
    max_tests: int = MAX_TESTS
    max_nodeid_bytes: int = MAX_NODEID_BYTES
    max_executes_per_run: int = MAX_EXECUTES_PER_RUN
    max_executes_per_generation: int = MAX_EXECUTES_PER_GENERATION
    max_records_per_generation: int = MAX_RECORDS_PER_GENERATION
    max_runs: int = MAX_RUNS


DEFAULT_LIMITS = Limits()


@lru_cache(maxsize=1)
def importer_sha256() -> str:
    """Digest of every ``providers/coverage*.py`` module: the importer's code identity."""
    here = Path(__file__).parent
    return hashlib.sha256(
        ev.canonical(
            {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(here.glob("coverage*.py"))
            }
        ).encode()
    ).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_id_of(artifact_sha256: str, manifest_sha256: str) -> str:
    return ev.coverage_run_id(artifact_sha256, manifest_sha256)


def role_of(phases: Any) -> str:
    return ROLE_PREFIX + "+".join(sorted(phases))


def _read_bounded(path: Path, limit: int, what: str) -> bytes:
    try:
        if path.stat().st_size > limit:
            raise _fail(f"{what}_too_large", f"{what} exceeds {limit} bytes")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise _fail(f"{what}_unreadable", f"cannot read {what}: {exc}") from exc
    if len(data) > limit:
        raise _fail(f"{what}_too_large", f"{what} exceeds {limit} bytes")
    return data


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _load_json(raw: bytes, code: str, what: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _fail(code, f"{what} is not strict UTF-8 JSON: {exc}") from exc


def _plain_int(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _text(value: Any, limit: int = 256) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit


# ---- environment (closed probe output) -------------------------------------


def check_environment(value: Any) -> dict[str, Any]:
    """Closed ``atlas-coverage-environment.v1`` shape; the probe's output, nothing else."""
    if not (
        isinstance(value, dict)
        and set(value) == {"format", "python", "platform", "packages"}
        and value["format"] == ENVIRONMENT_FORMAT
        and isinstance(value["python"], dict)
        and set(value["python"]) == {"version", "implementation", "cache_tag"}
        and isinstance(value["platform"], dict)
        and set(value["platform"]) == {"system", "machine"}
        and isinstance(value["packages"], dict)
        and set(value["packages"]) == {"coverage", "pytest", "pytest-cov"}
        and all(_text(v) for v in value["python"].values())
        and all(_text(v) for v in value["platform"].values())
        and all(_text(v) for v in value["packages"].values())
    ):
        raise _fail("environment_invalid", "environment is not the closed v1 probe output")
    return value


def check_producer(version: Any, pytest_cov: Any = None) -> dict[str, Any]:
    qualified = PRODUCERS["coverage.py"].get(version) if isinstance(version, str) else None
    if qualified is None:
        raise _fail("unknown_producer_version", f"unsupported coverage.py version {version!r}")
    if pytest_cov is not None and pytest_cov not in qualified["pytest-cov"]:
        raise _fail(
            "unknown_producer_version",
            f"coverage.py {version} is qualified with pytest-cov "
            f"{sorted(qualified['pytest-cov'])}, not {pytest_cov!r}",
        )
    return qualified


# ---- the coverage.py JSON report -------------------------------------------


@dataclass(frozen=True)
class FileReport:
    executed: frozenset[int]
    contexts: dict[int, frozenset[str]]


@dataclass(frozen=True)
class Report:
    version: str
    timestamp: str
    files: dict[str, FileReport]


def _check_path(path: Any) -> str:
    if ev.relative_path(path):
        return str(path)
    absolute = isinstance(path, str) and (
        path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", path) is not None
    )
    raise _fail(
        "absolute_path" if absolute else "path_not_normalized",
        f"report path {path!r} is not a normalized repository-relative POSIX path "
        "(set [run] relative_files = true and run coverage from the repository root)",
    )


def parse_report(raw: bytes, limits: Limits = DEFAULT_LIMITS) -> Report:
    data = _load_json(raw, "report_invalid", "report")
    if not (
        isinstance(data, dict)
        and {"meta", "files"} <= set(data)
        and set(data) <= {"meta", "files", "totals"}
    ):
        raise _fail("report_invalid", "report does not have the coverage.py JSON top level")
    meta = data["meta"]
    if not (
        isinstance(meta, dict)
        and set(meta) == {"format", "version", "timestamp", "branch_coverage", "show_contexts"}
    ):
        raise _fail("report_invalid", "report meta is not the qualified format-3 shape")
    if type(meta["format"]) is not int or meta["format"] != REPORT_FORMAT:
        raise _fail("unsupported_report_format", f"report format {meta['format']!r} is not 3")
    if not (_text(meta["version"], 64) and _text(meta["timestamp"], 64)):
        raise _fail("report_invalid", "report version/timestamp are not strings")
    if type(meta["show_contexts"]) is not bool or type(meta["branch_coverage"]) is not bool:
        raise _fail("report_invalid", "report meta flags are not booleans")
    check_producer(meta["version"])
    if meta["show_contexts"] is not True:
        raise _fail("contexts_absent", "report was written without --show-contexts")
    files = data["files"]
    if not isinstance(files, dict):
        raise _fail("report_invalid", "report files is not an object")
    if len(files) > limits.max_files:
        raise _fail("too_many_files", f"report measures more than {limits.max_files} files")
    parsed: dict[str, FileReport] = {}
    for key in files:
        path = _check_path(key)
        entry = files[key]
        if not isinstance(entry, dict) or "executed_lines" not in entry:
            raise _fail("report_invalid", f"{path}: file entry has no executed_lines")
        if "contexts" not in entry:
            raise _fail("contexts_absent", f"{path}: file entry has no contexts")
        executed = entry["executed_lines"]
        if not (
            isinstance(executed, list)
            and all(_plain_int(n, 1) for n in executed)
            and all(a < b for a, b in itertools.pairwise(executed))
        ):
            raise _fail("report_invalid", f"{path}: executed_lines is not sorted unique integers")
        contexts = entry["contexts"]
        if not isinstance(contexts, dict):
            raise _fail("report_invalid", f"{path}: contexts is not an object")
        lines: dict[int, frozenset[str]] = {}
        for line_key, values in contexts.items():
            if not (
                isinstance(line_key, str)
                and _LINE_KEY.fullmatch(line_key)
                and isinstance(values, list)
                and all(isinstance(v, str) for v in values)
            ):
                raise _fail("report_invalid", f"{path}: contexts entry {line_key!r} is malformed")
            number = int(line_key)
            if number > MAX_LINE:
                raise _fail("line_out_of_range", f"{path}:{number} is not a plausible line")
            lines[number] = frozenset(values)
        # Condition 3: the context keys are the raw recorded lines. `executed_lines` is
        # reported statement starts; it is used for counts only, never as an identity or a
        # rejection criterion (real reports record continuation/docstring lines it omits).
        parsed[path] = FileReport(frozenset(executed), lines)
    return Report(meta["version"], meta["timestamp"], parsed)


# ---- contexts -> observations ----------------------------------------------


@dataclass(frozen=True)
class Observations:
    """Normalized per-test executions, independent of any source bytes."""

    executes: dict[tuple[str, str, int], frozenset[str]]  # (nodeid, path, line) -> phases
    test_files: frozenset[str]
    counts: dict[str, int]


def split_context(context: str, limits: Limits = DEFAULT_LIMITS) -> tuple[str, str]:
    """``<nodeid>|<phase>`` split at the LAST ``|``; returns (nodeid, phase)."""
    nodeid, bar, phase = context.rpartition("|")
    if not bar or phase not in PHASES:
        raise _fail(
            "unsupported_context",
            f"context {context[:120]!r} is not '<nodeid>|setup|run|teardown' "
            "(static [run] context and other labelling are not supported)",
        )
    if len(nodeid.encode("utf-8", "surrogatepass")) > limits.max_nodeid_bytes:
        raise _fail("nodeid_too_long", f"a test id exceeds {limits.max_nodeid_bytes} bytes")
    file_part, sep, _rest = nodeid.partition("::")
    if not (
        sep
        and ev.relative_path(file_part)
        and file_part.endswith(".py")
        and "|" not in file_part
        and _rest
    ):
        raise _fail(
            "unsupported_context",
            f"context {context[:120]!r} does not name a repository-relative pytest test file "
            "(a static context prefix is not supported)",
        )
    return nodeid, phase


def observe(report: Report, limits: Limits = DEFAULT_LIMITS) -> Observations:
    phases: dict[tuple[str, str, int], set[str]] = {}
    tests: set[str] = set()
    observed_lines = 0
    unattributed = 0
    for path in sorted(report.files):
        entry = report.files[path]
        attributed: set[int] = set()
        for line in sorted(entry.contexts):
            for context in sorted(entry.contexts[line]):
                if context == "":
                    continue
                nodeid, phase = split_context(context, limits)
                attributed.add(line)
                key = (nodeid, path, line)
                if key not in phases:
                    if len(phases) >= limits.max_executes_per_run:
                        raise _fail(
                            "run_limit",
                            f"run records more than {limits.max_executes_per_run} test executions",
                        )
                    if nodeid not in tests and len(tests) >= limits.max_tests:
                        raise _fail("too_many_tests", f"more than {limits.max_tests} tests")
                tests.add(nodeid)
                phases.setdefault(key, set()).add(phase)
        observed_lines += len(attributed)
        unattributed += len((entry.executed | set(entry.contexts)) - attributed)
    if not phases:
        raise _fail(
            "no_test_contexts",
            "report carries no per-test context (run pytest with --cov-context=test)",
        )
    return Observations(
        {key: frozenset(value) for key, value in phases.items()},
        frozenset(key[0].partition("::")[0] for key in phases),
        {
            "measured_files": len(report.files),
            "tests": len(tests),
            "observed_lines": observed_lines,
            "test_executes": len(phases),
            "unattributed_lines": unattributed,
        },
    )


# ---- source lines ----------------------------------------------------------


def line_spans(source: bytes) -> list[tuple[int, int]]:
    """Half-open byte spans of each physical line, terminator excluded.

    Lines split ONLY on ``\\n``, ``\\r\\n`` and ``\\r`` (the Python tokenizer's terminators):
    never ``str.splitlines``, which also splits on form feed, U+2028 and friends. A BOM
    belongs to line 1; a trailing terminator does not start another line.
    """
    spans: list[tuple[int, int]] = []
    position = 0
    for match in _LINE_BREAKS.finditer(source):
        spans.append((position, match.start()))
        position = match.end()
    if position < len(source):
        spans.append((position, len(source)))
    return spans


# ---- the sidecar -----------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    tool_version: str
    report_format: int
    context_mode: str
    environment: dict[str, Any]
    artifact_sha256: str
    measured_files: dict[str, str]
    test_files: dict[str, str]


@dataclass(frozen=True)
class Submission:
    """A fully validated (but not yet snapshot-bound) report plus its sidecar."""

    report: Report
    observations: Observations
    artifact_sha256: str
    manifest: Manifest
    manifest_sha256: str
    limits: Limits = field(default=DEFAULT_LIMITS, compare=False)

    @property
    def run_id(self) -> str:
        return run_id_of(self.artifact_sha256, self.manifest_sha256)

    @property
    def tool(self) -> dict[str, str]:
        return {"name": "coverage.py", "version": self.manifest.tool_version}


def _hash_map(value: Any, what: str, limit: int) -> dict[str, str]:
    if not (
        isinstance(value, dict)
        and len(value) <= limit
        and all(ev.relative_path(k) and ev.hash_value(v) for k, v in value.items())
    ):
        raise _fail("manifest_invalid", f"manifest {what} is not a path -> sha256 map")
    return dict(value)


def parse_manifest(raw: bytes, limits: Limits = DEFAULT_LIMITS) -> Manifest:
    data = _load_json(raw, "manifest_invalid", "manifest")
    if not isinstance(data, dict):
        raise _fail("manifest_invalid", "manifest is not an object")
    if "environment" not in data:
        raise _fail("environment_invalid", "manifest carries no environment attestation")
    if set(data) != {
        "format",
        "artifact_sha256",
        "tool",
        "report_format",
        "context_mode",
        "environment",
        "commit",
        "measured_files",
        "test_files",
    }:
        raise _fail("manifest_invalid", "manifest does not have the closed v1 field set")
    tool = data["tool"]
    if not (
        data["format"] == MANIFEST_FORMAT
        and ev.hash_value(data["artifact_sha256"])
        and isinstance(tool, dict)
        and set(tool) == {"name", "version"}
        and tool["name"] == "coverage.py"
        and _text(tool["version"], 64)
        and type(data["report_format"]) is int
        and data["context_mode"] == CONTEXT_MODE
        # Condition 5: commit capture is cut, so regenerating a sidecar can never change a
        # run's identity.
        and data["commit"] == {"state": "unavailable"}
    ):
        raise _fail("manifest_invalid", "manifest field values are invalid")
    environment = check_environment(data["environment"])
    return Manifest(
        tool["version"],
        data["report_format"],
        data["context_mode"],
        environment,
        data["artifact_sha256"],
        _hash_map(data["measured_files"], "measured_files", limits.max_files),
        _hash_map(data["test_files"], "test_files", limits.max_files),
    )


def _cross_check(report: Report, manifest: Manifest, artifact_sha: str) -> None:
    if manifest.artifact_sha256 != artifact_sha:
        raise _fail("artifact_hash_mismatch", "report bytes differ from the manifest's sha256")
    if manifest.tool_version != report.version:
        raise _fail(
            "producer_mismatch",
            f"manifest names coverage.py {manifest.tool_version} but the report is "
            f"{report.version}",
        )
    qualified = check_producer(report.version, manifest.environment["packages"]["pytest-cov"])
    if manifest.report_format != qualified["report_format"]:
        raise _fail("manifest_invalid", "manifest report_format is not the qualified one")
    if manifest.environment["packages"]["coverage"] != report.version:
        raise _fail(
            "environment_tool_mismatch",
            "the environment attests coverage "
            f"{manifest.environment['packages']['coverage']} but the report is {report.version}",
        )


def load_submission(
    artifact_path: Path, manifest_path: Path, limits: Limits = DEFAULT_LIMITS
) -> Submission:
    """Every check that needs neither the lock nor the published graph."""
    manifest_raw = _read_bounded(manifest_path, limits.max_manifest_bytes, "manifest")
    artifact_raw = _read_bounded(artifact_path, limits.max_artifact_bytes, "report")
    manifest = parse_manifest(manifest_raw, limits)
    report = parse_report(artifact_raw, limits)
    artifact_sha = sha256_hex(artifact_raw)
    _cross_check(report, manifest, artifact_sha)
    measured, declared = set(report.files), set(manifest.measured_files)
    if measured - declared:
        raise _fail(
            "manifest_incomplete",
            f"manifest has no hash for measured files {sorted(measured - declared)[:5]}",
        )
    if declared - measured:
        raise _fail(
            "manifest_extra",
            f"manifest hashes files the report did not measure {sorted(declared - measured)[:5]}",
        )
    observations = observe(report, limits)
    for path in set(manifest.measured_files) & set(manifest.test_files):
        if manifest.measured_files[path] != manifest.test_files[path]:
            raise _fail("manifest_invalid", f"manifest hashes {path!r} two different ways")
    named, declared_tests = set(observations.test_files), set(manifest.test_files)
    if named - declared_tests:
        raise _fail(
            "manifest_incomplete",
            f"manifest has no hash for test files {sorted(named - declared_tests)[:5]}; "
            + _ROOTDIR_HINT,
        )
    if declared_tests - named:
        raise _fail(
            "manifest_extra",
            f"manifest hashes test files no context names {sorted(declared_tests - named)[:5]}",
        )
    return Submission(
        report, observations, artifact_sha, manifest, sha256_hex(manifest_raw), limits
    )


# ---- producer-time sidecar -------------------------------------------------


def _safe_read(repo: Path, relative: str, what: str) -> bytes:
    """Bytes of a regular file under ``repo``; symlinks and escapes are rejected."""
    current = repo
    for part in relative.split("/"):
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise _fail("source_unavailable", f"{what} {relative!r} passes through a symlink")
        except OSError as exc:
            raise _fail(
                "source_unavailable", f"{what} {relative!r} is not in the working tree: {exc}"
            ) from exc
    try:
        descriptor = os.open(current, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise _fail("source_unavailable", f"cannot open {what} {relative!r}: {exc}") from exc
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise _fail("source_unavailable", f"{what} {relative!r} is not a regular file")
        return stream.read()


def build_manifest(
    repo: Path, artifact_path: Path, environment_path: Path, limits: Limits = DEFAULT_LIMITS
) -> bytes:
    """Producer-time sidecar: the probe's environment plus working-tree hashes."""
    artifact_raw = _read_bounded(artifact_path, limits.max_artifact_bytes, "report")
    report = parse_report(artifact_raw, limits)
    observations = observe(report, limits)
    environment = check_environment(
        _load_json(
            _read_bounded(environment_path, limits.max_manifest_bytes, "environment"),
            "environment_invalid",
            "environment",
        )
    )
    qualified = check_producer(report.version, environment["packages"]["pytest-cov"])
    if environment["packages"]["coverage"] != report.version:
        raise _fail(
            "environment_tool_mismatch",
            f"the environment attests coverage {environment['packages']['coverage']} but the "
            f"report is {report.version}",
        )
    repo = repo.resolve()
    measured = {path: sha256_hex(_safe_read(repo, path, "measured file")) for path in report.files}
    tests: dict[str, str] = {}
    for path in sorted(observations.test_files):
        try:
            tests[path] = sha256_hex(_safe_read(repo, path, "test file"))
        except CoverageImportError as exc:
            raise _fail("manifest_incomplete", f"{exc}; " + _ROOTDIR_HINT) from exc
    manifest = {
        "format": MANIFEST_FORMAT,
        "artifact_sha256": sha256_hex(artifact_raw),
        "tool": {"name": "coverage.py", "version": report.version},
        "report_format": qualified["report_format"],
        "context_mode": qualified["context_mode"],
        "environment": environment,
        "commit": {"state": "unavailable"},
        "measured_files": dict(sorted(measured.items())),
        "test_files": dict(sorted(tests.items())),
    }
    return (ev.canonical(manifest) + "\n").encode()


# ---- records ---------------------------------------------------------------


def run_fingerprint(submission: Submission) -> dict[str, Any]:
    manifest = submission.manifest
    return {
        "coverage": {
            "run_id": submission.run_id,
            "tool": submission.tool,
            "report_format": manifest.report_format,
            "context_mode": manifest.context_mode,
            "report_timestamp": submission.report.timestamp,
            "artifact_sha256": submission.artifact_sha256,
            "manifest_sha256": submission.manifest_sha256,
            "environment": manifest.environment,
            "commit": {"state": "unavailable"},
            "importer_sha256": importer_sha256(),
        }
    }


@dataclass(frozen=True)
class Plan:
    producer: dict[str, Any]
    records: list[tuple[dict[str, Any], str | None]]
    run: dict[str, Any]
    record_count: int


def run_entry(submission: Submission, producer_id: str) -> dict[str, Any]:
    fingerprint = run_fingerprint(submission)["coverage"]
    return {
        "run_id": fingerprint["run_id"],
        "producer_id": producer_id,
        "tool": fingerprint["tool"],
        "report_format": fingerprint["report_format"],
        "context_mode": fingerprint["context_mode"],
        "report_timestamp": fingerprint["report_timestamp"],
        "artifact_sha256": fingerprint["artifact_sha256"],
        "manifest_sha256": fingerprint["manifest_sha256"],
        "importer_sha256": fingerprint["importer_sha256"],
        "environment": fingerprint["environment"],
        "commit": fingerprint["commit"],
        "counts": dict(submission.observations.counts),
    }


def plan_import(
    submission: Submission,
    *,
    current: Mapping[str, str],
    observations: Mapping[str, Mapping[str, Any]],
    read_source: Callable[[str, str], bytes],
    known_entity: Callable[[str], bool],
) -> Plan:
    """Validate against the pinned snapshot and map every observation, or raise."""
    producer = ev.producer(run_fingerprint(submission))
    ev.validate_record(producer)
    sizes: dict[str, tuple[int, list[tuple[int, int]]]] = {}

    def bind(path: str, sha: str, code: str) -> None:
        if path in sizes:
            return
        observed = observations.get(path)
        if (
            observed is None
            or observed.get("state") != "indexed"
            or observed.get("language") != "python"
        ):
            state = observed.get("state") if observed else "unobserved"
            raise _fail(
                "document_not_indexed", f"{path}: observation state is {state!r} (python required)"
            )
        if observed.get("observed_source_sha256") != sha or current.get(path) != sha:
            raise _fail(
                code,
                f"{path}: manifest hash {sha[:12]} differs from the published snapshot "
                f"({str(observed.get('observed_source_sha256'))[:12]})",
            )
        raw = read_source(path, sha)
        if sha256_hex(raw) != sha:
            raise _fail(code, f"{path}: snapshot bytes do not match their hash")
        sizes[path] = (len(raw), line_spans(raw))

    # A file that is both a test file and measured is bound as a test file (the more specific
    # role); the sidecar parser guarantees both maps agree on its hash.
    for path in sorted(submission.manifest.test_files):
        bind(path, submission.manifest.test_files[path], "test_source_mismatch")
    for path in sorted(submission.manifest.measured_files):
        bind(path, submission.manifest.measured_files[path], "source_mismatch")

    entities: dict[str, dict[str, Any]] = {}
    occurrences: dict[tuple[str, int, tuple[str, ...]], dict[str, Any]] = {}
    proofs: dict[str, dict[str, Any]] = {}
    records: dict[str, tuple[dict[str, Any], str | None]] = {producer["id"]: (producer, None)}
    for (nodeid, path, line), phases in sorted(submission.observations.executes.items()):
        size, spans = sizes[path]
        if line > len(spans):
            raise _fail(
                "line_out_of_range",
                f"{path}:{line} is beyond the {len(spans)} lines of the published snapshot",
            )
        test_path = nodeid.partition("::")[0]
        entity = entities.get(nodeid)
        if entity is None:
            entity = ev.record(
                "entity",
                path=test_path,
                language="python",
                kind=TEST_KIND,
                name=nodeid,
                owners=[],
                ordinal=0,
                identity_basis="parsed",
            )
            entities[nodeid] = entity
            records[entity["id"]] = (entity, test_path)
        file_entity = ev.file_entity(path, "python")
        if not known_entity(file_entity["id"]):
            raise _fail("document_not_indexed", f"{path}: no file entity in the published graph")
        start, end = spans[line - 1]
        key = (path, line, tuple(sorted(phases)))
        occurrence = occurrences.get(key)
        if occurrence is None:
            occurrence = ev.record(
                "occurrence",
                entity_id=file_entity["id"],
                kind=EXECUTION_KIND,
                path=path,
                language="python",
                source_sha256=submission.manifest.measured_files[path],
                source_size=size,
                start_byte=start,
                end_byte=end,
                line=line,
                column=0,
                end_line=line,
                end_column=end - start,
                role=role_of(phases),
                provenance_state="source",
            )
            occurrences[key] = occurrence
            records[occurrence["id"]] = (occurrence, path)
        proof = proofs.get(occurrence["id"])
        if proof is None:
            proof = ev.provenance(occurrence, producer["id"])
            proofs[occurrence["id"]] = proof
            records[proof["id"]] = (proof, path)
        assertion = ev.assertion(
            entity["id"], file_entity["id"], RELATION, proof["id"], "EXTRACTED", f"{path}:{line}"
        )
        records[assertion["id"]] = (assertion, path)
    ordered = sorted(records.values(), key=lambda item: (item[0]["type"], item[0]["id"]))
    return Plan(producer, ordered, run_entry(submission, producer["id"]), len(ordered) - 1)


# ---- generation block ------------------------------------------------------

_RUN_KEYS = {
    "run_id",
    "producer_id",
    "tool",
    "report_format",
    "context_mode",
    "report_timestamp",
    "artifact_sha256",
    "manifest_sha256",
    "importer_sha256",
    "environment",
    "commit",
    "counts",
}
_COUNT_KEYS = {"measured_files", "tests", "observed_lines", "test_executes", "unattributed_lines"}


def block_of(base_generation_id: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": IMPORT_FORMAT,
        "base_generation_id": base_generation_id,
        "runs": sorted(runs, key=lambda run: run["run_id"]),
    }


def run_fingerprint_of(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "coverage": {
            key: run[key]
            for key in (
                "run_id",
                "tool",
                "report_format",
                "context_mode",
                "report_timestamp",
                "artifact_sha256",
                "manifest_sha256",
                "environment",
                "commit",
                "importer_sha256",
            )
        }
    }


def block_producer_ids(block: Mapping[str, Any]) -> set[str]:
    return {run["producer_id"] for run in block["runs"]}


def validate_block(block: Any, limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    """Closed shape, allow-lists and recomputed identities of the ``coverage`` block."""
    ev.require(
        isinstance(block, dict)
        and set(block) == {"format", "base_generation_id", "runs"}
        and block["format"] == IMPORT_FORMAT
        and ev.hash_value(block["base_generation_id"])
        and isinstance(block["runs"], list)
        and 0 < len(block["runs"]) <= limits.max_runs,
        "coverage import block",
    )
    ids = []
    executes = 0
    for run in block["runs"]:
        ev.require(
            isinstance(run, dict)
            and set(run) == _RUN_KEYS
            and isinstance(run["counts"], dict)
            and set(run["counts"]) == _COUNT_KEYS
            and all(_plain_int(v, 0) for v in run["counts"].values())
            and ev.hash_value(run["importer_sha256"])
            and ev.hash_value(run["producer_id"])
            and isinstance(run["tool"], dict)
            and set(run["tool"]) == {"name", "version"},
            "coverage run entry",
        )
        try:
            check_producer(run["tool"]["version"], run["environment"]["packages"]["pytest-cov"])
            check_environment(run["environment"])
        except (CoverageImportError, KeyError, TypeError) as exc:
            raise ValueError("Invalid evidence: coverage run producer") from exc
        ev.require(
            run["tool"]["name"] == "coverage.py"
            and run["commit"] == {"state": "unavailable"}
            and run["run_id"] == run_id_of(run["artifact_sha256"], run["manifest_sha256"])
            and run["producer_id"] == ev.producer(run_fingerprint_of(run))["id"],
            "coverage run identity",
        )
        ids.append(run["run_id"])
        executes += run["counts"]["test_executes"]
    ev.require(ids == sorted(set(ids)), "coverage runs are unique and sorted")
    ev.require(executes <= limits.max_executes_per_generation, "coverage generation bound")
    return block


def merge_runs(
    base: Mapping[str, Any] | None, entry: dict[str, Any], limits: Limits = DEFAULT_LIMITS
) -> tuple[str, list[dict[str, Any]]]:
    """``("unchanged"|"new", runs)``; conflicting identical-run entries are rejected."""
    runs = [dict(run) for run in base["runs"]] if base else []
    for run in runs:
        if run["run_id"] == entry["run_id"]:
            if run == entry:
                return "unchanged", runs
            raise _fail(
                "run_conflict",
                f"run {entry['run_id'][:12]} is already published by a different importer "
                "build; rebuild from source first",
            )
    runs.append(entry)
    if len(runs) > limits.max_runs:
        raise _fail("run_limit", f"a generation keeps at most {limits.max_runs} runs")
    if sum(run["counts"]["test_executes"] for run in runs) > limits.max_executes_per_generation:
        raise _fail(
            "generation_limit",
            f"a generation keeps at most {limits.max_executes_per_generation} test executions",
        )
    return "new", runs


# ---- record selection and referential checks --------------------------------


def record_ids(records: list[dict[str, Any]]) -> set[str]:
    """IDs of every canonical record contributed by a coverage producer."""
    producers = {
        r["id"]
        for r in records
        if r["type"] == "producer"
        and isinstance(r["fingerprint"], dict)
        and "coverage" in r["fingerprint"]
    }
    proofs = {r["id"] for r in records if r["type"] == "evidence" and r["producer_id"] in producers}
    occurrences = {
        r["occurrence_id"] for r in records if r["type"] == "evidence" and r["id"] in proofs
    }
    assertions = {
        r["id"] for r in records if r["type"] == "assertion" and r["evidence_id"] in proofs
    }
    entities = {r["id"] for r in records if r["type"] == "entity" and r["kind"] == TEST_KIND}
    return producers | proofs | occurrences | assertions | entities


def validate_records(conn: Any) -> None:
    """Block-independent referential completeness of the coverage records."""
    sources = {
        row[0]
        for row in conn.execute(
            "SELECT json_extract(payload,'$.source_entity_id') FROM assertions "
            "WHERE json_extract(payload,'$.relation')=?",
            (RELATION,),
        )
    }
    for (identifier,) in conn.execute(
        "SELECT id FROM entities WHERE json_extract(payload,'$.kind')=?", (TEST_KIND,)
    ):
        ev.require(identifier in sources, "coverage test without an observation")
    cited = {
        row[0]
        for row in conn.execute(
            "SELECT json_extract(payload,'$.occurrence_id') FROM evidence "
            "WHERE json_extract(payload,'$.producer_id') IN (SELECT id FROM producers "
            "WHERE json_extract(payload,'$.fingerprint.coverage') IS NOT NULL)"
        )
    }
    for (identifier,) in conn.execute(
        "SELECT id FROM occurrences WHERE json_extract(payload,'$.kind')=?", (EXECUTION_KIND,)
    ):
        ev.require(identifier in cited, "execution occurrence without coverage evidence")


def coverage_producer_ids(conn: Any) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT id FROM producers WHERE json_extract(payload,'$.fingerprint.coverage') "
            "IS NOT NULL"
        )
    }


def row_counts(conn: Any) -> dict[str, int]:
    """Counts derived from published rows (not from the block)."""

    def count(sql: str, *args: Any) -> int:
        return int(conn.execute(sql, args).fetchone()[0])

    return {
        "runs": len(coverage_producer_ids(conn)),
        "tests": count(
            "SELECT COUNT(*) FROM entities WHERE json_extract(payload,'$.kind')=?", TEST_KIND
        ),
        "observed_lines": count(
            "SELECT COUNT(*) FROM (SELECT DISTINCT path, json_extract(payload,'$.line') "
            "FROM occurrences WHERE json_extract(payload,'$.kind')=?)",
            EXECUTION_KIND,
        ),
        "test_executes": count(
            "SELECT COUNT(*) FROM assertions WHERE json_extract(payload,'$.relation')=?", RELATION
        ),
    }


def record_totals(conn: Any) -> int:
    """Coverage occurrences + evidence + assertions + tests currently stored."""

    def count(sql: str, *args: Any) -> int:
        return int(conn.execute(sql, args).fetchone()[0])

    return (
        count(
            "SELECT COUNT(*) FROM occurrences WHERE json_extract(payload,'$.kind')=?",
            EXECUTION_KIND,
        )
        + count(
            "SELECT COUNT(*) FROM evidence WHERE json_extract(payload,'$.producer_id') IN "
            "(SELECT id FROM producers WHERE json_extract(payload,'$.fingerprint.coverage') "
            "IS NOT NULL)"
        )
        + count(
            "SELECT COUNT(*) FROM assertions WHERE json_extract(payload,'$.relation')=?", RELATION
        )
        + count("SELECT COUNT(*) FROM entities WHERE json_extract(payload,'$.kind')=?", TEST_KIND)
    )


def status_block(conn: Any, block: Mapping[str, Any]) -> dict[str, Any]:
    """CLI status: every item is labelled ``observed_execution``; no verdicts."""
    return {
        "format": STATUS_FORMAT,
        "semantics": "observed_execution",
        "assertion_inference": "not_performed",
        "test_outcome": "not_recorded",
        "base_generation_id": block["base_generation_id"],
        "runs": [{**run, "semantics": "observed_execution"} for run in block["runs"]],
        "counts": row_counts(conn),
        "limitations": [
            {"name": name, "reason": reason} for name, reason in sorted(LIMITATIONS.items())
        ],
    }
