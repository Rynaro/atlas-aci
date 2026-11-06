"""ACI-S42 aggregator: shared-operation readiness records -> one report (pure, replayable).

Reads the immutable run records written by ``s42_record.py`` (one gzip-canonical JSON file per
unit) and the frozen manifest, and writes ``readiness-report.json`` and ``readiness-report.md``.
Aggregation is a pure function of its inputs: the same records give the same bytes. Every key
named ``observed`` or ``diagnostic`` (at any depth) is excluded from ``canonical_sha256``; counts
and verdicts are derived here from row-level facts, never taken from the records.

Usage::

    python scripts/eval/s42_aggregate.py --manifest M --raw DIR --out DIR [--verify]

Exit 0 when the report is written (or, with ``--verify``, equals the files in ``--out``), 1 on a
``--verify`` mismatch, 2 on a typed input error (nothing is written).
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

FORMAT_MANIFEST = "atlas-shared-readiness-manifest.v1"
FORMAT_RAW = "atlas-shared-raw.v1"
FORMAT_REPORT = "atlas-shared-readiness-report.v1"

VERDICT_BLOCKED = "blocked"
VERDICT_REVIEW = "ready_for_operator_review"
VERDICTS = (VERDICT_BLOCKED, VERDICT_REVIEW)
CHECK_STATES = ("pass", "fail", "missing")
STATUS = ("complete", "failed")
RECORD_KINDS = ("environment", "isolation", "interrupt", "compat", "capacity")
PHASES = ("warm", "hold", "main", "revocation", "teeth")
DOMAINS = ("A", "B", "C")
FAILURES = (
    "server_start_timeout",
    "request_deadline",
    "case_deadline",
    "subprocess_error",
    "sdk_unavailable",
    "stopped_line_missing",
    "unexpected_exception",
)
SOURCES = ("proc_status", "proc_fd", "wait4", "stopped_line", "unavailable")
RUSAGE_UNITS = ("kib", "bytes")
ERROR_CODES = (
    "FORBIDDEN",
    "SCOPE_MISMATCH",
    "INVALID_REFERENCE",
    "EXPIRED_REFERENCE",
    "OTHER",
)
HTTP_OUTCOMES = (
    "http:401:credentials_missing",
    "http:401:credentials_invalid",
    "http:403:repository_forbidden",
    "http:404:route_not_found",
    "http:503:inflight_limit",
    "http:503:principal_inflight_limit",
    "http:other",
)
OUTCOMES = (
    "ok",
    *(f"tool_error:{code}" for code in ERROR_CODES),
    *(f"rpc_error:{code}" for code in ERROR_CODES),
    *HTTP_OUTCOMES,
    "transport_error",
)
CLASSES = (
    "control",
    "ungranted_repo",
    "ungranted_tool",
    "disabled_tool",
    "impact_before_ungranted",
    "cross_principal_cursor",
    "cross_principal_resource",
    "cross_repo_resource",
    "cross_repo_argument",
    "path_escape",
    "token_missing",
    "token_invalid",
    "token_wrong_audience",
    "revoked_grant",
)
CHECKS = (
    "records.complete",
    "environment.sdk_pinned",
    "isolation.unauthorized_zero",
    "isolation.positive_controls",
    "isolation.typed_denials",
    "isolation.concurrency",
    "isolation.ledger_teeth",
    "interrupt.all_recovered",
    "interrupt.teeth_detected",
    "retention.lease_respected",
    "compat.cells_recorded",
    "capacity.limits_held",
    "capacity.saturation_typed",
)
UNRESOLVED_LIMITS = (
    "black_box_ledger_detects_planted_canaries_and_typed_outcomes_only",
    "zero_is_within_the_enumerated_cases_not_a_no_leakage_claim",
    "stderr_tool_call_corroboration_is_necessary_not_sufficient",
    "timing_side_channels_untested",
    "shared_address_space_untested",
    "tls_reverse_proxy_untested",
    "same_path_pair_exposes_the_live_tree_by_design",
    "real_hosts_untested_claude_code_copilot_cursor",
    "s40_in_process_observer_suite_inherited_not_rerun",
    "gc_seam_kills_untested_only_lease_writer_busy_and_expiry",
    "single_host_loopback_workload_not_a_load_test",
    "resource_metrics_linux_proc_with_source_labels",
    "no_deployment_credentials_or_dns_performed",
    "fresh_runs_compared_by_invariants_not_by_digest",
)
HONESTY = (
    "Black-box evidence: the ledger detects planted canaries and typed outcomes in responses and "
    "server log lines, not internal reads.",
    "Unauthorized evidence access is zero within the enumerated cases; this is not a claim that "
    "no leakage exists.",
    "Not tested: timing side channels, the shared address space of the single server process, and "
    "a TLS-terminating reverse proxy.",
    "Inherited and not re-run: the S40 in-process Observer suite "
    "(mcp-server/tests/slices/test_s40_contract.py).",
)
ERROR_CODES_AGG = (
    "manifest_mismatch",
    "unknown_shape",
    "unknown_kind",
    "unknown_unit",
    "duplicate_record",
    "not_canonical",
    "missing_input",
    "vocabulary",
)
INVARIANTS = (
    "all_calls_completed",
    "client_principal_concurrency_within_cap",
    "server_peak_within_limit",
    "no_untyped_5xx",
    "typed_503_has_retry_after",
    "unauthorized_zero",
    "limits_match_config_document",
)
SATURATION = (
    "third_rejected_typed",
    "other_principal_admitted",
    "recovered_after_disconnect",
    "gate_503_typed",
    "stopped_line_rejected_ge_1",
)
LOCK_CANARY = ("second_fd_blocks", "process_blocks", "hardlink_same_inode")
VALIDATION = (
    "status_current_matches",
    "no_invalid_jobs",
    "server_gid_matches",
    "server_expected_canary",
    "server_other_canary_absent",
    "acquire_ok",
    "control_gid_matches",
)
SDK_PIN = "1.30.0"
RECORD_KEYS = ("format", "kind", "unit", "manifest_sha256", "status", "failure")
RECORD_OPTIONAL = ("body", "observed", "diagnostic")
BODY_KEYS = {
    "environment": ("mcp_version", "atlas_aci_version", "lock_canary"),
    "isolation": ("policy", "policy_sha256", "rows", "holds_spanned", "server"),
    "interrupt": ("injection", "expected_tree", "facts", "validation", "valid", "live_unchanged"),
    "compat": (
        "client",
        "transport",
        "requested",
        "sent_revision",
        "expected",
        "negotiated",
        "server_info",
        "mcp_version",
        "tools",
        "call",
    ),
    "capacity": ("limits", "workload", "invariants", "saturation"),
}
ROW_KEYS = (
    "case_id",
    "phase",
    "principal",
    "route",
    "request_kind",
    "tool",
    "class",
    "expected",
    "outcome",
    "http_status",
    "canary_hits",
    "server_seen",
    "witness_in_request",
)
MANIFEST_KEYS = (
    "format",
    "slice",
    "issuer",
    "resource_base_url",
    "sdk_pin",
    "principals",
    "canaries",
    "trees",
    "repositories",
    "grants",
    "isolation",
    "interruption",
    "compatibility",
    "capacity",
    "deadlines",
    "deployment_model",
    "required_checks",
    "unresolved_limits",
    "honesty",
)


class ReadinessInputError(Exception):
    """A typed rejection of malformed or foreign input; ``code`` is one of ERROR_CODES_AGG."""

    def __init__(self, code: str, message: str) -> None:
        assert code in ERROR_CODES_AGG, code
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _load_v3() -> Any:
    path = Path(__file__).resolve().with_name("v3_aggregate.py")
    spec = importlib.util.spec_from_file_location("s42_v3_aggregate_loaded", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["s42_v3_aggregate_loaded"] = module
    spec.loader.exec_module(module)
    return module


_V3 = _load_v3()
canonical = _V3.canonical
digest = _V3.digest
gzip_canonical = _V3.gzip_canonical


def strip(value: Any) -> Any:
    """The value without any key named ``observed`` or ``diagnostic`` at any depth."""
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in ("observed", "diagnostic")}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def manifest_sha(manifest: dict[str, Any]) -> str:
    return str(digest(manifest))


# ------------------------------------------------------------------------------------ loading
def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadinessInputError(
            "unknown_shape", f"manifest unreadable: {type(exc).__name__}"
        ) from None
    return check_manifest(value)


def _unique(values: list[Any] | tuple[Any, ...], what: str) -> None:
    if len(set(values)) != len(values):
        raise ReadinessInputError("vocabulary", f"duplicate entries in {what}")


def check_manifest(manifest: Any) -> dict[str, Any]:
    """Closed-key and vocabulary validation; the manifest lists must equal the constants."""
    if not isinstance(manifest, dict) or set(manifest) != set(MANIFEST_KEYS):
        raise ReadinessInputError("unknown_shape", "manifest keys are not the closed set")
    if manifest["format"] != FORMAT_MANIFEST:
        raise ReadinessInputError("unknown_shape", "manifest format")
    for name, constant in (
        ("required_checks", CHECKS),
        ("unresolved_limits", UNRESOLVED_LIMITS),
        ("honesty", HONESTY),
    ):
        if tuple(manifest[name]) != constant:
            raise ReadinessInputError("vocabulary", f"manifest {name} differs from the aggregator")
    iso = manifest["isolation"]
    if tuple(iso["classes"]) != CLASSES:
        raise ReadinessInputError("vocabulary", "manifest classes differ from the aggregator")
    seen: list[tuple[str, str]] = []
    for case in iso["cases"]:
        seen.append((case["phase"], case["id"]))
        if case["phase"] not in PHASES or case["class"] not in CLASSES:
            raise ReadinessInputError("vocabulary", f"case {case['id']}")
        if case["expected"] not in ("allow", "deny"):
            raise ReadinessInputError("vocabulary", f"case {case['id']} expectation")
        wanted = case["expected_outcomes"]
        if any(o not in OUTCOMES for o in wanted):
            raise ReadinessInputError("vocabulary", f"case {case['id']} outcomes")
        if case["expected"] == "deny" and (
            not wanted or any(o.endswith(":OTHER") or o == "http:other" for o in wanted)
        ):
            raise ReadinessInputError("vocabulary", f"case {case['id']} deny outcomes")
        if any(d not in DOMAINS for d in case["domains"]):
            raise ReadinessInputError("vocabulary", f"case {case['id']} domains")
    _unique(seen, "case ids")
    return manifest  # type: ignore[no-any-return]


def decode_record(raw: bytes, name: str) -> dict[str, Any]:
    try:
        plain = gzip.decompress(raw)
        text = plain.decode("utf-8")
        value = json.loads(text)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadinessInputError(
            "not_canonical", f"{name}: undecodable record ({type(exc).__name__})"
        ) from None
    except (ValueError, RecursionError):
        raise ReadinessInputError("not_canonical", f"{name}: undecodable record") from None
    if not isinstance(value, dict):
        raise ReadinessInputError("unknown_shape", f"{name}: record is not an object")
    try:
        if canonical(value) != plain:
            raise ReadinessInputError("not_canonical", f"{name}: bytes are not canonical JSON")
    except ValueError:
        raise ReadinessInputError("not_canonical", f"{name}: not canonical JSON") from None
    return value


def read_raw_dir(directory: Path) -> list[dict[str, Any]]:
    path = Path(directory)
    if not path.is_dir():
        raise ReadinessInputError("missing_input", "raw directory is absent")
    records: list[dict[str, Any]] = []
    for item in sorted(path.iterdir()):
        if not item.name.endswith(".json.gz"):
            raise ReadinessInputError("unknown_shape", f"{item.name}: not a .json.gz record")
        record = decode_record(item.read_bytes(), item.name)
        kind, unit = record.get("kind"), record.get("unit")
        if item.name != f"{kind}.{unit}.json.gz":
            raise ReadinessInputError("unknown_shape", f"{item.name}: name differs from kind.unit")
        records.append(record)
    if not records:
        raise ReadinessInputError("missing_input", "raw directory holds no records")
    return records


# --------------------------------------------------------------------------------- unit table
def expected_units(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (kind, unit) the manifest requires, in a fixed order."""
    units: list[tuple[str, str]] = [("environment", "main")]
    units += [("isolation", name) for name in ("main", "revocation", "teeth")]
    units += [("interrupt", case["id"]) for case in manifest["interruption"]["cases"]]
    units += [("compat", cell["id"]) for cell in manifest["compatibility"]["cells"]]
    units.append(("capacity", "main"))
    return units


def required_units(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    optional = {
        ("compat", cell["id"])
        for cell in manifest["compatibility"]["cells"]
        if not cell["required"]
    }
    return [unit for unit in expected_units(manifest) if unit not in optional]


def index_records(
    manifest: dict[str, Any], records: list[dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Validate shapes and bind every record to the manifest; one record per unit."""
    sha = manifest_sha(manifest)
    allowed = set(expected_units(manifest))
    by_unit: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        keys = set(record)
        if not set(RECORD_KEYS) <= keys or not keys <= set(RECORD_KEYS) | set(RECORD_OPTIONAL):
            raise ReadinessInputError("unknown_shape", "record keys are not the closed set")
        if record["format"] != FORMAT_RAW:
            raise ReadinessInputError("unknown_shape", "record format")
        kind, unit = record["kind"], record["unit"]
        if kind not in RECORD_KINDS:
            raise ReadinessInputError("unknown_kind", f"record kind {kind!r}")
        if not isinstance(unit, str) or (kind, unit) not in allowed:
            raise ReadinessInputError("unknown_unit", f"{kind}.{unit} is not in the manifest")
        if record["manifest_sha256"] != sha:
            raise ReadinessInputError("manifest_mismatch", f"{kind}.{unit} names another manifest")
        if record["status"] not in STATUS:
            raise ReadinessInputError("vocabulary", f"{kind}.{unit} status")
        failure = record["failure"]
        if record["status"] == "complete":
            if failure is not None or not isinstance(record.get("body"), dict):
                raise ReadinessInputError("unknown_shape", f"{kind}.{unit} complete record shape")
        elif not isinstance(failure, dict) or failure.get("code") not in FAILURES:
            raise ReadinessInputError("vocabulary", f"{kind}.{unit} failure code")
        if (kind, unit) in by_unit:
            raise ReadinessInputError("duplicate_record", f"{kind}.{unit}")
        by_unit[(kind, unit)] = record
        if record["status"] == "complete":
            _check_body(manifest, record)
    return by_unit


def _check_body(manifest: dict[str, Any], record: dict[str, Any]) -> None:
    kind, body = record["kind"], record["body"]
    where = f"{kind}.{record['unit']}"
    if set(body) != set(BODY_KEYS[kind]):
        raise ReadinessInputError("unknown_shape", f"{where} body keys are not the closed set")
    if kind == "isolation":
        if not isinstance(body["rows"], list):
            raise ReadinessInputError("unknown_shape", f"{where} rows")
        for row in body["rows"]:
            if not isinstance(row, dict) or set(row) - set(ROW_KEYS) - {"observed"}:
                raise ReadinessInputError("unknown_shape", f"{where} row keys")
            if not set(ROW_KEYS) <= set(row):
                raise ReadinessInputError("unknown_shape", f"{where} row is incomplete")
            case = _row_case(manifest, row)
            if case is None:
                raise ReadinessInputError("unknown_unit", f"{where} row {row['case_id']!r}")
            for key in ("principal", "route", "class", "expected"):
                if row[key] != case[key]:
                    raise ReadinessInputError(
                        "manifest_mismatch", f"{where} row {row['case_id']} {key}"
                    )
            if row.get("outcome") not in OUTCOMES:
                raise ReadinessInputError(
                    "vocabulary", f"{where} row outcome {row.get('outcome')!r}"
                )
            if row.get("class") not in CLASSES:
                raise ReadinessInputError("vocabulary", f"{where} row class")
            if any(d not in DOMAINS for d in row.get("canary_hits", [])):
                raise ReadinessInputError("vocabulary", f"{where} row domain")
    elif kind == "compat":
        call = body.get("call", {})
        if call.get("outcome") not in OUTCOMES:
            raise ReadinessInputError("vocabulary", f"{where} call outcome")
    elif kind == "capacity":
        observed = record.get("observed", {})
        for key in ("memory", "fds", "rusage", "server"):
            source = observed.get(key, {}).get("source") if isinstance(observed, dict) else None
            if source is not None and source not in SOURCES:
                raise ReadinessInputError("vocabulary", f"{where} source label")
        unit_name = observed.get("rusage", {}).get("unit") if isinstance(observed, dict) else None
        if unit_name is not None and unit_name not in RUSAGE_UNITS:
            raise ReadinessInputError("unknown_unit", f"{where} rusage unit")


# ------------------------------------------------------------------------------- the D5 ledger
def case_table(manifest: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(c["phase"], c["id"]): c for c in manifest["isolation"]["cases"]}


def authorized(case: dict[str, Any]) -> set[str]:
    return set(case["domains"]) if case["expected"] == "allow" else set()


def is_unauthorized(row: dict[str, Any], case: dict[str, Any]) -> bool:
    """D5: a deny that succeeded, a hit outside the authorized domains, or a deny whose nonce
    reached a server tool_call line."""
    deny = case["expected"] == "deny"
    if deny and row["outcome"] == "ok":
        return True
    if set(row["canary_hits"]) - authorized(case):
        return True
    return bool(deny and case["nonce_carrier"] and row["server_seen"])


def teeth_cases(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """The re-run cases of the teeth phase: the main-table deny cases named by the manifest."""
    table = case_table(manifest)
    return [table[("main", cid)] for cid in manifest["isolation"]["teeth_rerun"]]


def _row_case(manifest: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    """The manifest case a row answers: (phase, id), or a teeth re-run of a main deny case."""
    phase, case_id = str(row.get("phase")), str(row.get("case_id"))
    case = case_table(manifest).get((phase, case_id))
    if case is None and phase == "teeth":
        case = next((c for c in teeth_cases(manifest) if c["id"] == case_id), None)
    return case


def rows_of(
    manifest: dict[str, Any], record: dict[str, Any] | None
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(row, manifest case) for every row of a complete isolation record (unknown rows skipped)."""
    if record is None or record["status"] != "complete":
        return []
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in record["body"].get("rows", []):
        case = _row_case(manifest, row)
        if case is not None:
            out.append((row, case))
    return out


def expected_row_ids(manifest: dict[str, Any], unit: str) -> list[tuple[str, str]]:
    if unit == "teeth":
        return [("teeth", c["id"]) for c in teeth_cases(manifest)]
    phases = ("warm", "hold", "main") if unit == "main" else ("revocation",)
    return [(c["phase"], c["id"]) for c in manifest["isolation"]["cases"] if c["phase"] in phases]


def row_ids(record: dict[str, Any]) -> list[tuple[str, str]]:
    return [(r.get("phase"), r.get("case_id")) for r in record["body"].get("rows", [])]  # type: ignore[misc]


def unauthorized_rows(
    manifest: dict[str, Any], record: dict[str, Any] | None
) -> list[dict[str, Any]]:
    return [row for row, case in rows_of(manifest, record) if is_unauthorized(row, case)]


# ----------------------------------------------------------------------------------- checks
def _state(units: list[dict[str, Any] | None]) -> str | None:
    """``missing`` if any unit is absent, ``fail`` if any failed, else None (evaluate)."""
    if any(u is None for u in units):
        return "missing"
    if any(u["status"] != "complete" for u in units if u is not None):
        return "fail"
    return None


def _all_rows(
    manifest: dict[str, Any], by: dict[tuple[str, str], dict[str, Any]], units: tuple[str, ...]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for unit in units:
        rows += rows_of(manifest, by.get(("isolation", unit)))
    return rows


def _row_set_differs(manifest: dict[str, Any], record: dict[str, Any]) -> bool:
    if record["kind"] != "isolation":
        return False
    return sorted(row_ids(record)) != sorted(expected_row_ids(manifest, record["unit"]))


def check_records_complete(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    states = []
    for unit in required_units(manifest):
        record = by.get(unit)
        if record is None:
            states.append("missing")
        elif record["status"] != "complete" or _row_set_differs(manifest, record):
            states.append("fail")
    if "fail" in states:
        return "fail"
    return "missing" if "missing" in states else "pass"


def check_environment(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    record = by.get(("environment", "main"))
    early = _state([record])
    if early:
        return early
    assert record is not None
    body = record["body"]
    canary = body.get("lock_canary", {})
    good = (
        body.get("mcp_version") == manifest["sdk_pin"] == SDK_PIN
        and all(canary.get(key) is True for key in LOCK_CANARY)
        and canary.get("shared_folder_fs") is False
    )
    return "pass" if good else "fail"


def check_unauthorized_zero(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    records = [by.get(("isolation", "main")), by.get(("isolation", "revocation"))]
    early = _state(records)
    if early:
        return early
    count = sum(len(unauthorized_rows(manifest, r)) for r in records)
    return "pass" if count == 0 else "fail"


def _control_ok(
    manifest: dict[str, Any], case: dict[str, Any], rows: dict[str, dict[str, Any]]
) -> bool:
    control = rows.get(case["control"])
    if control is None:
        return False
    return (
        control["outcome"] == "ok"
        and bool(case["target_domain"])
        and case["target_domain"] in control["canary_hits"]
    )


def check_positive_controls(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    records = [by.get(("isolation", "main")), by.get(("isolation", "revocation"))]
    early = _state(records)
    if early:
        return early
    pairs = _all_rows(manifest, by, ("main", "revocation"))
    if not pairs:
        return "fail"
    cases = manifest["isolation"]["cases"]
    deny_classes = {c["class"] for c in cases if c["expected"] == "deny"}
    if any(k != "control" and k not in deny_classes for k in manifest["isolation"]["classes"]):
        return "fail"
    by_case = {case["id"]: row for row, case in pairs}
    for row, case in pairs:
        if row.get("witness_in_request") is not False:
            return "fail"
        if case["expected"] == "allow":
            if row["outcome"] != "ok":
                return "fail"
            if case["nonce_carrier"] and row["server_seen"] is not True:
                return "fail"
        else:
            if not _control_ok(manifest, case, by_case):
                return "fail"
            if case["nonce_carrier"] and row["server_seen"] is not False:
                return "fail"
    seven = by_case.get("M07")
    if seven is None or "C" not in seven["canary_hits"]:
        return "fail"
    return "pass"


def check_typed_denials(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    records = [by.get(("isolation", "main")), by.get(("isolation", "revocation"))]
    early = _state(records)
    if early:
        return early
    pairs = _all_rows(manifest, by, ("main", "revocation"))
    deny = [(row, case) for row, case in pairs if case["expected"] == "deny"]
    if not deny:
        return "fail"
    for row, case in deny:
        if row["outcome"] in ("http:other", "transport_error") or row["outcome"].endswith(":OTHER"):
            return "fail"
        if row["outcome"] not in case["expected_outcomes"]:
            return "fail"
    return "pass"


def check_concurrency(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    record = by.get(("isolation", "main"))
    early = _state([record])
    if early:
        return early
    assert record is not None
    rows = [r for r, c in rows_of(manifest, record) if c["phase"] == "main" and r["principal"]]
    principals = {r["principal"] for r in rows}
    routes = {r["route"] for r in rows}
    body = record["body"]
    good = (
        len(principals) >= 4
        and len(routes) >= 2
        and body.get("holds_spanned") is True
        and body.get("server", {}).get("peak_ge_3") is True
    )
    return "pass" if good else "fail"


def check_ledger_teeth(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    record = by.get(("isolation", "teeth"))
    early = _state([record])
    if early:
        return early
    assert record is not None
    pairs = rows_of(manifest, record)
    if not pairs or len(pairs) != len(manifest["isolation"]["teeth_rerun"]):
        return "fail"
    table = case_table(manifest)
    for row, case in pairs:
        main = table[("main", case["id"])]
        if row["expected"] != main["expected"] or row["class"] != main["class"]:
            return "fail"
    flagged = [row for row, case in pairs if is_unauthorized(row, case)]
    return "pass" if flagged else "fail"


def _facts_ok(manifest: dict[str, Any], record: dict[str, Any], case: dict[str, Any]) -> bool:
    body = record["body"]
    return (
        body.get("facts") == case["facts"]
        and body.get("injection") == case["injection"]
        and body.get("expected_tree") == case["expected_tree"]
    )


def _valid_ok(body: dict[str, Any]) -> bool:
    validation = body.get("validation", {})
    parts = []
    for key in VALIDATION:
        value = validation.get(key)
        parts.append(value is True or (key == "server_gid_matches" and value is None))
    return body.get("valid") is True and all(parts)


def check_interrupt_recovered(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    cases = [c for c in manifest["interruption"]["cases"] if c["expect_valid"]]
    records = [by.get(("interrupt", c["id"])) for c in cases]
    early = _state(records)
    if early:
        return early
    for case, record in zip(cases, records, strict=True):
        assert record is not None
        if not (_valid_ok(record["body"]) and _facts_ok(manifest, record, case)):
            return "fail"
    return "pass" if cases else "fail"


def check_interrupt_teeth(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    cases = [c for c in manifest["interruption"]["cases"] if not c["expect_valid"]]
    records = [by.get(("interrupt", c["id"])) for c in cases]
    early = _state(records)
    if early:
        return early
    if len(cases) < 2:
        return "fail"
    for case, record in zip(cases, records, strict=True):
        assert record is not None
        body = record["body"]
        validation = body.get("validation", {})
        if body.get("valid") is not False or not _facts_ok(manifest, record, case):
            return "fail"
        if case["id"] == "teeth_replace_by_copy":
            good = (
                validation.get("acquire_ok") is False
                and body.get("live_unchanged") is True
                and validation.get("server_expected_canary") is True
            )
        else:
            good = all(
                validation.get(key) is False
                for key in (
                    "status_current_matches",
                    "server_expected_canary",
                    "control_gid_matches",
                )
            )
        if not good:
            return "fail"
    return "pass"


def check_retention(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    cases = [
        c for c in manifest["interruption"]["cases"] if c["id"] in ("gc_vs_lease", "gc_vs_build")
    ]
    records = [by.get(("interrupt", c["id"])) for c in cases]
    early = _state(records)
    if early:
        return early
    if len(cases) != 2:
        return "fail"
    for case, record in zip(cases, records, strict=True):
        assert record is not None
        if not (_valid_ok(record["body"]) and _facts_ok(manifest, record, case)):
            return "fail"
    return "pass"


def check_compat(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    cells = [c for c in manifest["compatibility"]["cells"] if c["required"]]
    records = [by.get(("compat", c["id"])) for c in cells]
    early = _state(records)
    if early:
        return early
    for cell, record in zip(cells, records, strict=True):
        assert record is not None
        if not _compat_record_ok(manifest, cell, record["body"]):
            return "fail"
    # recorded optional cells must be sound as well
    for cell in manifest["compatibility"]["cells"]:
        record = by.get(("compat", cell["id"]))
        if record is None or record["status"] != "complete" or cell["required"]:
            continue
        if not _compat_record_ok(manifest, cell, record["body"]):
            return "fail"
    return "pass" if cells else "fail"


def _compat_record_ok(manifest: dict[str, Any], cell: dict[str, Any], body: dict[str, Any]) -> bool:
    expected = body.get("expected", {})
    call = body.get("call", {})
    return bool(
        body.get("client") == cell["client"]
        and body.get("transport") == cell["transport"]
        and body.get("requested") == cell["requested"]
        and body.get("negotiated") == expected.get("offered")
        and body.get("sent_revision") == body.get("negotiated")
        and body.get("mcp_version") == manifest["sdk_pin"]
        and body.get("tools", {}).get("count") == 7
        and call.get("outcome") == "ok"
        and call.get("canary_hit") is True
    )


def check_capacity_limits(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    record = by.get(("capacity", "main"))
    early = _state([record])
    if early:
        return early
    assert record is not None
    body = record["body"]
    cap = manifest["capacity"]
    same = (
        body.get("limits") == {"listen": cap["listen"], "limits": cap["limits"]}
        and body.get("workload") == cap["workload"]
    )
    invariants = body.get("invariants", {})
    held = all(invariants.get(key) is True for key in INVARIANTS)
    return "pass" if same and held else "fail"


def check_capacity_saturation(manifest: dict[str, Any], by: dict[Any, Any]) -> str:
    record = by.get(("capacity", "main"))
    early = _state([record])
    if early:
        return early
    assert record is not None
    saturation = record["body"].get("saturation", {})
    return "pass" if all(saturation.get(key) is True for key in SATURATION) else "fail"


CHECK_FUNCTIONS = {
    "records.complete": check_records_complete,
    "environment.sdk_pinned": check_environment,
    "isolation.unauthorized_zero": check_unauthorized_zero,
    "isolation.positive_controls": check_positive_controls,
    "isolation.typed_denials": check_typed_denials,
    "isolation.concurrency": check_concurrency,
    "isolation.ledger_teeth": check_ledger_teeth,
    "interrupt.all_recovered": check_interrupt_recovered,
    "interrupt.teeth_detected": check_interrupt_teeth,
    "retention.lease_respected": check_retention,
    "compat.cells_recorded": check_compat,
    "capacity.limits_held": check_capacity_limits,
    "capacity.saturation_typed": check_capacity_saturation,
}


# ----------------------------------------------------------------------------------- report
def _isolation_section(manifest: dict[str, Any], by: dict[Any, Any]) -> dict[str, Any]:
    main, revocation, teeth = (by.get(("isolation", u)) for u in ("main", "revocation", "teeth"))
    pairs = _all_rows(manifest, by, ("main", "revocation"))
    flagged = [row for row, case in pairs if is_unauthorized(row, case)]
    by_case = {case["id"]: row for row, case in pairs}
    classes = []
    for name in manifest["isolation"]["classes"]:
        class_pairs = [(row, case) for row, case in pairs if case["class"] == name]
        deny = [(row, case) for row, case in class_pairs if case["expected"] == "deny"]
        if name == "control":
            controls_ok = sum(1 for row, _c in class_pairs if row["outcome"] == "ok")
        else:
            controls_ok = sum(1 for _r, case in deny if _control_ok(manifest, case, by_case))
        classes.append(
            {
                "class": name,
                "deny_cases": len(deny),
                "controls_ok": controls_ok,
                "unauthorized": sum(1 for row, case in class_pairs if is_unauthorized(row, case)),
            }
        )
    main_body = main["body"] if main is not None and main["status"] == "complete" else {}
    observed = main["observed"] if main is not None and "observed" in main else {}
    return {
        "principals": list(manifest["principals"]),
        "routes": sorted({row["route"] for row, _case in pairs}),
        "rows_total": len(pairs),
        "allow_rows": sum(1 for _r, case in pairs if case["expected"] == "allow"),
        "deny_rows": sum(1 for _r, case in pairs if case["expected"] == "deny"),
        "unauthorized": {
            "main": len(unauthorized_rows(manifest, main)),
            "revocation": len(unauthorized_rows(manifest, revocation)),
            "teeth": len(unauthorized_rows(manifest, teeth)),
        },
        "unauthorized_rows": sorted(row["case_id"] for row in flagged),
        "by_class": classes,
        "overlap": {
            "holds_spanned_recorder_attested": main_body.get("holds_spanned"),
            "server_peak_ge_3": main_body.get("server", {}).get("peak_ge_3"),
            "observed": {
                "max_client_overlap": observed.get("max_client_overlap"),
                "server_peak": observed.get("server", {}).get("peak"),
            },
        },
    }


def _interruption_section(manifest: dict[str, Any], by: dict[Any, Any]) -> dict[str, Any]:
    cases = []
    for case in manifest["interruption"]["cases"]:
        record = by.get(("interrupt", case["id"]))
        complete = record is not None and record["status"] == "complete"
        body: dict[str, Any] = record["body"] if record is not None and complete else {}
        cases.append(
            {
                "id": case["id"],
                "injection": case["injection"],
                "expected_tree": case["expected_tree"],
                "expect_valid": case["expect_valid"],
                "valid": body.get("valid") if complete else None,
                "facts": body.get("facts") if complete else None,
                "validation": body.get("validation") if complete else None,
                "live_unchanged": body.get("live_unchanged") if complete else None,
            }
        )
    return {"cases": cases}


def _compat_section(manifest: dict[str, Any], by: dict[Any, Any]) -> dict[str, Any]:
    cells = []
    untested = []
    for cell in manifest["compatibility"]["cells"]:
        record = by.get(("compat", cell["id"]))
        if record is not None and record["status"] == "complete":
            body = record["body"]
            cells.append(
                {
                    "id": cell["id"],
                    "client": body["client"],
                    "transport": body["transport"],
                    "requested": body["requested"],
                    "sent_revision": body["sent_revision"],
                    "expected": body["expected"],
                    "negotiated": body["negotiated"],
                    "server_info": body["server_info"],
                    "mcp_version": body["mcp_version"],
                    "tools": body["tools"],
                    "call": body["call"],
                    "shared": body["transport"] == "shared-http",
                }
            )
        else:
            reason = (
                "record_failed"
                if record is not None
                else cell.get("untested_reason") or "no_record"
            )
            untested.append({"id": cell["id"], "reason": reason})
    return {
        "cells": cells,
        "untested_cells": untested,
        "hosts": [dict(h) for h in manifest["compatibility"]["hosts"]],
    }


def _capacity_section(manifest: dict[str, Any], by: dict[Any, Any]) -> dict[str, Any]:
    record = by.get(("capacity", "main"))
    if record is None or record["status"] != "complete":
        return {"workload": None}
    body = record["body"]
    return {
        "workload": {
            "limits": body["limits"],
            "workload": body["workload"],
            "invariants": body["invariants"],
            "saturation": body["saturation"],
            "observed": record.get("observed", {}),
        }
    }


def aggregate(manifest: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """The report document for a manifest and its run records (a pure function)."""
    manifest = check_manifest(manifest)
    by = index_records(manifest, records)
    checks = {name: CHECK_FUNCTIONS[name](manifest, by) for name in CHECKS}
    failed = sorted(name for name, state in checks.items() if state != "pass")
    environment = by.get(("environment", "main"))
    env_body: dict[str, Any] = (
        environment["body"]
        if environment is not None and environment["status"] == "complete"
        else {}
    )
    report = {
        "manifest_sha256": manifest_sha(manifest),
        "inputs": sorted(
            (
                {"name": f"{kind}.{unit}", "sha256": digest(strip(record))}
                for (kind, unit), record in by.items()
            ),
            key=lambda item: item["name"],
        ),
        "verdict": VERDICT_BLOCKED if failed else VERDICT_REVIEW,
        "checks": checks,
        "failed_checks": failed,
        "deployment_model": manifest["deployment_model"],
        "honesty": list(HONESTY),
        "isolation": _isolation_section(manifest, by),
        "interruption": _interruption_section(manifest, by),
        "compatibility": _compat_section(manifest, by),
        "capacity": _capacity_section(manifest, by),
        "environment": {
            "mcp_version": env_body.get("mcp_version"),
            "atlas_aci_version": env_body.get("atlas_aci_version"),
            "observed": environment.get("observed", {}) if environment is not None else {},
        },
        "unresolved_limits": list(UNRESOLVED_LIMITS),
    }
    return {
        "format": FORMAT_REPORT,
        "report": report,
        "canonical_sha256": digest(strip(report)),
        "diagnostic": {},
    }


# ------------------------------------------------------------------------------- rendering
def render_json(doc: dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("|", "/")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def _flat(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def render_md(doc: dict[str, Any]) -> str:
    report = doc["report"]
    out: list[str] = ["# Shared-operation readiness report (ACI-S42)", ""]
    if report["verdict"] == VERDICT_BLOCKED:
        out.append("Verdict: blocked")
    else:
        out.append(
            "Verdict: ready_for_operator_review "
            "(evidence for an operator decision; not a deployment approval)"
        )
    out += ["", "Failed checks: " + (", ".join(report["failed_checks"]) or "none"), ""]
    out += ["## Checks", ""]
    out += _table(["check", "state"], [[name, state] for name, state in report["checks"].items()])
    out += ["", "## Deployment model", ""]
    for key in sorted(report["deployment_model"]):
        out.append(f"- {key}: {_cell(report['deployment_model'][key])}")
    out += ["", "## What this evidence does and does not show", ""]
    out += [f"- H{i + 1}: {text}" for i, text in enumerate(report["honesty"])]
    out += [
        "- The word isolation in this report means the enumerated cases of the case table and "
        "nothing wider.",
        "- No real host (Claude Code, GitHub Copilot, Cursor) was tested; each is listed untested.",
    ]
    iso = report["isolation"]
    out += ["", "## Isolation (evidence access ledger)", ""]
    out.append(
        f"Rows: {iso['rows_total']} ({iso['allow_rows']} allow, {iso['deny_rows']} deny) over "
        f"{len(iso['principals'])} principals and routes {', '.join(iso['routes']) or '-'}."
    )
    out.append(
        "Unauthorized evidence access, within the enumerated cases: "
        f"main {iso['unauthorized']['main']}, revocation {iso['unauthorized']['revocation']}; "
        f"over-granted teeth run {iso['unauthorized']['teeth']} (must be at least 1)."
    )
    overlap = iso["overlap"]
    out.append(
        "Concurrency: client intervals overlapped and the server gate peak was at least 3 "
        f"(the gate counts requests, not principals): server_peak_ge_3 = "
        f"{_cell(overlap['server_peak_ge_3'])}; holds_spanned (recorder-attested, not recomputed "
        f"here) = {_cell(overlap['holds_spanned_recorder_attested'])}."
    )
    out += [""]
    out += _table(
        ["class", "deny cases", "controls ok", "unauthorized"],
        [
            [c["class"], c["deny_cases"], c["controls_ok"], c["unauthorized"]]
            for c in iso["by_class"]
        ],
    )
    out += ["", "## Interruption matrix", ""]
    out += _table(
        ["case", "injection", "tree", "expect valid", "valid", "facts"],
        [
            [
                c["id"],
                _flat(c["injection"]),
                c["expected_tree"],
                c["expect_valid"],
                c["valid"],
                _flat(c["facts"]) if c["facts"] is not None else None,
            ]
            for c in report["interruption"]["cases"]
        ],
    )
    comp = report["compatibility"]
    out += ["", "## Tested client and transport combinations", ""]
    out += _table(
        ["cell", "client", "transport", "requested", "negotiated", "expected", "shared", "call"],
        [
            [
                c["id"],
                c["client"],
                c["transport"],
                c["requested"],
                c["negotiated"],
                c["expected"]["outcome"],
                c["shared"],
                c["call"]["outcome"],
            ]
            for c in comp["cells"]
        ],
    )
    out += ["", "Untested cells:", ""]
    out += [f"- {u['id']}: {u['reason']}" for u in comp["untested_cells"]] or ["- none"]
    out += ["", "Hosts:", ""]
    out += [f"- {h['id']}: {h['status']} ({h['reason']})" for h in comp["hosts"]]
    out += ["", "## Capacity workload record", ""]
    workload = report["capacity"]["workload"]
    if workload is None:
        out.append("No workload record.")
    else:
        out.append("Configured limits:")
        out.append("")
        limit_rows = [[f"listen.{k}", v] for k, v in sorted(workload["limits"]["listen"].items())]
        limit_rows += [[f"limits.{k}", v] for k, v in sorted(workload["limits"]["limits"].items())]
        limit_rows += [
            ["workload.clients", workload["workload"]["clients"]],
            ["workload.calls_per_client", workload["workload"]["calls_per_client"]],
        ]
        out += _table(["limit", "value"], limit_rows)
        out += ["", "Invariants (peak <= max_inflight is true by construction of the gate):", ""]
        out += _table(
            ["invariant", "held"], [[k, v] for k, v in sorted(workload["invariants"].items())]
        )
        out += ["", "Saturation:", ""]
        out += _table(["step", "held"], [[k, v] for k, v in sorted(workload["saturation"].items())])
        out += ["", "Observed (excluded from canonical_sha256):", ""]
        out += _table(
            ["metric", "value"],
            [[k, _flat(v)] for k, v in sorted(workload["observed"].items())],
        )
    out += ["", "## Unresolved limits", ""]
    out += [f"- {name}" for name in report["unresolved_limits"]]
    out += ["", "## Inputs", ""]
    out += _table(["input", "sha256"], [[i["name"], i["sha256"]] for i in report["inputs"]])
    out += ["", "## Canonical digest", "", f"canonical_sha256: {doc['canonical_sha256']}", ""]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------- CLI
def build(manifest_path: Path, raw_dir: Path) -> tuple[dict[str, Any], str, str]:
    manifest = load_manifest(manifest_path)
    doc = aggregate(manifest, read_raw_dir(raw_dir))
    return doc, render_json(doc), render_md(doc)


def run(manifest_path: Path, raw_dir: Path, out_dir: Path, verify: bool = False) -> tuple[int, str]:
    """(exit code, stdout line). Typed input errors propagate as ReadinessInputError."""
    doc, json_text, md_text = build(manifest_path, raw_dir)
    line = f"{doc['canonical_sha256']} {doc['report']['verdict']}"
    json_path, md_path = (
        Path(out_dir) / "readiness-report.json",
        Path(out_dir) / "readiness-report.md",
    )
    if verify:
        same = (
            json_path.is_file()
            and md_path.is_file()
            and json_path.read_bytes() == json_text.encode("ascii")
            and md_path.read_bytes() == md_text.encode("ascii")
        )
        return (0 if same else 1), line
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    json_path.write_bytes(json_text.encode("ascii"))
    md_path.write_bytes(md_text.encode("ascii"))
    return 0, line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s42_aggregate")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        code, line = run(Path(args.manifest), Path(args.raw), Path(args.out), args.verify)
    except ReadinessInputError as exc:
        sys.stderr.write(f"s42_aggregate: {exc.code}: {exc.message}\n")
        return 2
    sys.stdout.write(line + "\n")
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
