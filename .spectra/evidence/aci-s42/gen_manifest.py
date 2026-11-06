"""Generate atlas/evals/shared/manifest.v1.json (ACI-S42). Run inside the dev image:

    .spectra/evidence/aci-s42/dk42.sh /opt/venv/bin/python /ws/.spectra/evidence/aci-s42/gen_manifest.py

The manifest is frozen after Day-1 (g); this generator is kept so the file is reproducible.
Token classes: PATTERN tokens appear in requests; WITNESS tokens never do (they sit on the same
source line as a pattern match, in a file path, and in the source span of a call).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "atlas" / "evals" / "shared" / "manifest.v1.json"

GRAPH = "atlas.graph.v1"
HONESTY = [
    "Black-box evidence: the ledger detects planted canaries and typed outcomes in responses and "
    "server log lines, not internal reads.",
    "Unauthorized evidence access is zero within the enumerated cases; this is not a claim that "
    "no leakage exists.",
    "Not tested: timing side channels, the shared address space of the single server process, and "
    "a TLS-terminating reverse proxy.",
    "Inherited and not re-run: the S40 in-process Observer suite "
    "(mcp-server/tests/slices/test_s40_contract.py).",
]
UNRESOLVED = [
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
]
CHECKS = [
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
]
CLASSES = [
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
]


def h(label: str) -> str:
    return hashlib.sha256(f"atlas-s42-{label}".encode()).hexdigest()[:12]


HA, HB, HC, H1, H2 = h("a"), h("b"), h("c"), h("t1"), h("t2")
A_PATTERN, A_WIT = f"s42cnry_a_{HA}", f"s42wit_a_{HA}"
B_PATTERN, B_WIT = f"s42cnry_b_{HB}", f"s42wit_b_{HB}"
C_WIT = f"s42cnry_c_{HC}"
T1_NAME, T2_NAME = f"s42cnry_t1_{H1}", f"s42cnry_t2_{H2}"
FUNCS = 30
A_SEED = f"{A_PATTERN}_hub"
B_SYMBOL = f"{B_PATTERN}_fn"


def tree_a2() -> dict[str, str]:
    lines = []
    for n in range(FUNCS):
        nxt = f"{A_PATTERN}_{n + 1}_{A_WIT}" if n + 1 < FUNCS else None
        lines.append(f'def {A_PATTERN}_{n}_{A_WIT}(x, tag="{A_WIT}"):  # {A_WIT}')
        lines.append(f'    return {nxt}(x, "{A_WIT}")' if nxt else "    return x")
        lines.append("")
    lines.append(f"def {A_PATTERN}_hub(x):  # {A_WIT}")
    for n in range(FUNCS):
        lines.append(f'    {A_PATTERN}_{n}_{A_WIT}(x, "{A_WIT}")')
    lines.append("    return x")
    lines.append("")
    core = f"# {A_WIT} core module\n\n\ndef core_entry(x):  # {A_WIT}\n    return x\n"
    return {"core.py": core, f"{A_WIT}.py": "\n".join(lines) + "\n"}


def tree_a1() -> dict[str, str]:
    files = tree_a2()
    files["old.py"] = f"def {C_WIT}(x):\n    return x\n"
    return files


def tree_b() -> dict[str, str]:
    main = f"# {B_WIT} main module\n\n\ndef main():  # {B_WIT}\n    return {B_PATTERN}_fn(1)\n"
    body = (
        f'def {B_PATTERN}_fn(x):  # {B_WIT}\n    return x\n\n\nPAYLOAD = "{B_PATTERN}"  # {B_WIT}\n'
    )
    return {"main.py": main, f"{B_WIT}.py": body}


def tree_t(version: int) -> dict[str, str]:
    name = T1_NAME if version == 1 else T2_NAME
    files = {}
    for i in range(5):
        extra = 0
        if version == 2 and i in (0, 1):
            extra = 100
        head = f"def {name}(x):\n    return x + {i}\n\n\n" if i == 0 else ""
        files[f"m{i}.py"] = (
            head + f"def f{i}(x):\n    return x + {i + extra}\n\n"
            f"class C{i}:\n    def m(self):\n        return f{i}(1)\n"
        )
    return files


def grant(subject: str, repository: str, tools: list[str]) -> dict[str, object]:
    return {"subject": subject, "repository": repository, "tools": tools}


ALL5 = ["graph_query", "list_dir", "search_symbol", "search_text", "view_file"]
PAGE_W1 = {
    "schema_version": GRAPH,
    "op": "page",
    "query": {
        "schema_version": GRAPH,
        "op": "neighborhood",
        "seed": {"name": A_SEED},
        "max_depth": 1,
        "deadline_ms": 2000,
    },
    "page_max_bytes": 18000,
}
PAGE_W2 = {**PAGE_W1, "page_max_bytes": 24000}


def tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {"kind": "tool", "tool": name, "arguments": arguments}


def case(
    cid: str,
    phase: str,
    principal: str | None,
    route: str,
    request: dict[str, object],
    klass: str,
    expected: str,
    outcomes: list[str],
    domains: list[str],
    *,
    token: str = "valid",
    target: str | None = None,
    control: str | None = None,
    carrier: bool = False,
) -> dict[str, object]:
    return {
        "id": cid,
        "phase": phase,
        "principal": principal,
        "token": token,
        "route": route,
        "request": request,
        "class": klass,
        "expected": expected,
        "expected_outcomes": outcomes,
        "domains": domains,
        "target_domain": target,
        "control": control,
        "nonce_carrier": carrier,
    }


SEARCH_A = tool("search_text", {"pattern": "$A_PATTERN|$NONCE", "scope": "."})
SEARCH_B = tool("search_text", {"pattern": "$B_PATTERN|$NONCE", "scope": "."})
VIEW_A = tool("view_file", {"path": "core.py", "start_line": 1, "end_line": 20})
VIEW_B = tool("view_file", {"path": "main.py", "start_line": 1, "end_line": 20})
LIST_A = tool("list_dir", {"path": "."})
LIST_B = tool("list_dir", {"path": "."})
IMPACT = tool("graph_query", {"schema_version": GRAPH, "op": "impact"})
RETRIEVE_A = tool(
    "graph_query",
    {"schema_version": GRAPH, "op": "retrieve", "text": A_SEED, "repository": "$REPO_A"},
)
RETRIEVE_B = tool(
    "graph_query",
    {"schema_version": GRAPH, "op": "retrieve", "text": B_SYMBOL, "repository": "$REPO_B"},
)
RETRIEVE_B_AT_A = tool(
    "graph_query",
    {"schema_version": GRAPH, "op": "retrieve", "text": A_SEED, "repository": "$REPO_A"},
)
CONTINUE_W1 = {"kind": "cursor_continue", "uses": "W1"}
READ_W2 = {"kind": "resource_read", "uses": "W2"}
OK = ["ok"]
F = "tool_error:FORBIDDEN"
R403 = "http:403:repository_forbidden"


def cases() -> list[dict[str, object]]:
    c = case
    return [
        c("W1", "warm", "p1", "a", tool("graph_query", PAGE_W1), "control", "allow", OK, ["A"]),
        c("W2", "warm", "p1", "a", tool("graph_query", PAGE_W2), "control", "allow", OK, ["A"]),
        c("H1", "hold", "p2", "a", tool("search_symbol", {"name": A_SEED}), "control", "allow", OK, ["A"]),
        c("H2", "hold", "p4", "b", LIST_B, "control", "allow", OK, ["B"]),
        c("M01", "main", "p1", "a", SEARCH_A, "control", "allow", OK, ["A"], carrier=True),
        c("M02", "main", "p1", "b", SEARCH_B, "control", "allow", OK, ["B"], carrier=True),
        c("M03", "main", "p1", "a", VIEW_A, "control", "allow", OK, ["A"]),
        c("M04", "main", "p3", "b", SEARCH_B, "control", "allow", OK, ["B"], carrier=True),
        c("M05", "main", "p2", "a", tool("search_symbol", {"name": A_SEED}), "control", "allow", OK, ["A"]),
        c("M06", "main", "p4", "b", VIEW_B, "control", "allow", OK, ["B"]),
        c("M07", "main", "p2", "a", IMPACT, "control", "allow", OK, ["A", "C"]),
        c("M08", "main", "p1", "a", CONTINUE_W1, "control", "allow", OK, ["A"]),
        c("M09", "main", "p1", "a", READ_W2, "control", "allow", OK, ["A"]),
        c("M10", "main", "p3", "b", RETRIEVE_B, "control", "allow", OK, ["B"]),
        c("M11", "main", "p4", "b", LIST_B, "control", "allow", OK, ["B"]),
        c("M12", "main", "p1", "a", RETRIEVE_A, "control", "allow", OK, ["A"]),
        c("M13", "main", "p1", "b", VIEW_B, "control", "allow", OK, ["B"]),
        c("M14", "main", "p1", "a", LIST_A, "control", "allow", OK, ["A"]),
        c("D01", "main", "p3", "a", SEARCH_A, "ungranted_repo", "deny", [R403], [], target="A", control="M01", carrier=True),
        c("D02", "main", "p3", "c", IMPACT, "ungranted_repo", "deny", [R403], [], target="C", control="M07"),
        c("D03", "main", "p2", "a", VIEW_A, "ungranted_tool", "deny", [F], [], target="A", control="M03"),
        c("D04", "main", "p4", "b", SEARCH_B, "ungranted_tool", "deny", [F], [], target="B", control="M04", carrier=True),
        c("D05", "main", "p1", "a", tool("test_dry_run", {"path": "core.py"}), "disabled_tool", "deny", [F], [], target="A", control="M03"),
        c("D06", "main", "p1", "b", tool("memex_read", {"ref": "memex://excerpt/" + "0" * 64}), "disabled_tool", "deny", [F], [], target="B", control="M13"),
        c("D07", "main", "p1", "a", IMPACT, "impact_before_ungranted", "deny", [F], [], target="C", control="M07"),
        c("D08", "main", "p2", "a", CONTINUE_W1, "cross_principal_cursor", "deny", ["tool_error:SCOPE_MISMATCH"], [], target="A", control="M08"),
        c("D09", "main", "p2", "a", READ_W2, "cross_principal_resource", "deny", ["rpc_error:SCOPE_MISMATCH"], [], target="A", control="M09"),
        c("D10", "main", "p1", "b", READ_W2, "cross_repo_resource", "deny", ["rpc_error:INVALID_REFERENCE"], [], target="A", control="M09"),
        c("D11", "main", "p3", "b", RETRIEVE_B_AT_A, "cross_repo_argument", "deny", [F], [], target="A", control="M12"),
        c("D12", "main", "p1", "a", tool("view_file", {"path": "../b/main.py", "start_line": 1, "end_line": 20}), "path_escape", "deny", [F], [], target="B", control="M13"),
        c("D13", "main", "p4", "b", tool("list_dir", {"path": "../a"}), "path_escape", "deny", [F], [], target="A", control="M14"),
        c("D14", "main", None, "a", SEARCH_A, "token_missing", "deny", ["http:401:credentials_missing"], [], token="missing", target="A", control="M01", carrier=True),
        c("D15", "main", "p1", "a", SEARCH_A, "token_invalid", "deny", ["http:401:credentials_invalid"], [], token="stranger", target="A", control="M01", carrier=True),
        c("D16", "main", "p1", "a", SEARCH_A, "token_wrong_audience", "deny", ["http:401:credentials_invalid"], [], token="audience:b", target="A", control="M01", carrier=True),
        c("R1", "revocation", "p1", "a", tool("graph_query", PAGE_W1), "control", "allow", OK, ["A"]),
        c("R2", "revocation", "p1", "a", {"kind": "cursor_continue", "uses": "R1"}, "control", "allow", OK, ["A"]),
        c("R3", "revocation", "p1", "a", {"kind": "cursor_continue", "uses": "R1"}, "revoked_grant", "deny", [R403], [], target="A", control="R2"),
        c("R4", "revocation", "p1", "b", SEARCH_B, "control", "allow", OK, ["B"], carrier=True),
        c("R5", "revocation", "p1", "a", {"kind": "cursor_continue", "uses": "R1"}, "revoked_grant", "deny", ["tool_error:EXPIRED_REFERENCE"], [], target="A", control="R2"),
        c("R6", "revocation", "p1", "a", tool("graph_query", PAGE_W1), "control", "allow", OK, ["A"]),
    ]


def compat_cells() -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    for rev in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2099-01-01"):
        cells.append(
            {"id": f"raw.shared-http.{rev}", "client": "raw-jsonrpc", "transport": "shared-http",
             "requested": rev, "required": True}
        )
    cells.append({"id": "raw.http.2025-11-25", "client": "raw-jsonrpc", "transport": "http",
                  "requested": "2025-11-25", "required": True})
    cells.append({"id": "raw.stdio.2025-11-25", "client": "raw-jsonrpc", "transport": "stdio",
                  "requested": "2025-11-25", "required": True})
    for transport in ("stdio", "http", "shared-http"):
        cells.append({"id": f"sdk.{transport}.sdk_default", "client": "mcp-sdk", "transport": transport,
                      "requested": "sdk_default", "required": True})
    return cells


def interruption() -> dict[str, object]:
    def ic(cid, injection, tree, valid, facts):
        return {"id": cid, "injection": injection, "expected_tree": tree,
                "expect_valid": valid, "facts": facts}

    return {
        "langs": ["python"],
        "retention": {"keep_last": 1},
        "workers": 3,
        "cases": [
            ic("cancel_marker", {"kind": "cancel_marker", "seam": "staged"}, "T1", True,
               {"exit": 4, "state": "cancelled", "cancel": "honoured", "published": False}),
            ic("cancel_sigterm", {"kind": "cancel_sigterm", "seam": "staged"}, "T1", True,
               {"exit": 4, "state": "cancelled", "cancel": "honoured", "published": False}),
            ic("kill_checkpoint", {"kind": "kill", "seam": "checkpoint", "at": 2}, "T2", True,
               {"killed": True, "interrupted": True, "interrupted_state": "running", "resumed_from": 2,
                "outcome": "built", "state": "completed"}),
            ic("kill_mid_publish", {"kind": "kill", "seam": "mid_publish"}, "T2", True,
               {"killed": True, "interrupted": True, "interrupted_state": "running",
                "outcome": "built", "state": "completed"}),
            ic("kill_post_replace", {"kind": "kill", "seam": "post_replace"}, "T2", True,
               {"killed": True, "interrupted": True, "interrupted_state": "published",
                "outcome": "recovered", "state": "completed"}),
            ic("kill_pre_receipt", {"kind": "kill", "seam": "pre_receipt"}, "T2", True,
               {"killed": True, "interrupted": True, "interrupted_state": "captured",
                "outcome": "recovered", "state": "completed"}),
            ic("gc_vs_lease", {"kind": "gc_vs_lease"}, "T2", True,
               {"leased_retained": True, "holder_killed": True, "retired_after_release": True,
                "acquire_exit": 3, "acquire_code": "EXPIRED_REFERENCE"}),
            ic("gc_vs_build", {"kind": "gc_vs_build", "seam": "staged"}, "T2", True,
               {"gc_exit": 75, "gc_code": "WRITER_BUSY", "state": "completed"}),
            ic("teeth_replace_by_copy", {"kind": "teeth_replace_by_copy"}, "T2", False,
               {"corrupted_farm_acquire_refused": True}),
            ic("teeth_wrong_expectation", {"kind": "teeth_wrong_expectation"}, "T2", False,
               {"validated_against": "T1"}),
        ],
    }


def build() -> dict[str, object]:
    base = "https://atlas.example.test/mcp"
    work = [
        {"id": "a", "tree": "A2"},
        {"id": "b", "tree": "B"},
        {"id": "c", "tree": "A1", "same_path_as": "a", "before_of": "a"},
    ]
    mix = [
        ["a", "search_text", {"pattern": A_PATTERN, "scope": "."}],
        ["b", "search_text", {"pattern": B_PATTERN, "scope": "."}],
        ["a", "search_symbol", {"name": A_SEED}],
        ["b", "search_symbol", {"name": B_SYMBOL}],
        ["a", "view_file", {"path": "core.py", "start_line": 1, "end_line": 20}],
        ["b", "view_file", {"path": "main.py", "start_line": 1, "end_line": 20}],
        ["a", "list_dir", {"path": "."}],
        ["b", "list_dir", {"path": "."}],
        ["a", "graph_query", {"schema_version": GRAPH, "op": "status"}],
        ["b", "graph_query", {"schema_version": GRAPH, "op": "status"}],
        ["a", "search_symbol", {"name": A_SEED}],
        ["b", "search_text", {"pattern": B_SYMBOL, "scope": "."}],
    ]
    return {
        "format": "atlas-shared-readiness-manifest.v1",
        "slice": "ACI-S42",
        "issuer": "https://idp.example.test",
        "resource_base_url": base,
        "sdk_pin": "1.30.0",
        "principals": ["p1", "p2", "p3", "p4"],
        "canaries": {
            "A": {"pattern": A_PATTERN, "witness": A_WIT, "seed": A_SEED},
            "B": {"pattern": B_PATTERN, "witness": B_WIT, "symbol": B_SYMBOL},
            "C": {"witness": C_WIT},
            "T1": {"name": T1_NAME},
            "T2": {"name": T2_NAME},
        },
        "trees": {"A1": tree_a1(), "A2": tree_a2(), "B": tree_b(), "T1": tree_t(1), "T2": tree_t(2)},
        "repositories": work,
        "grants": {
            "main": [
                grant("p1", "a", ALL5),
                grant("p1", "b", ALL5),
                grant("p2", "a", ["graph_query", "search_symbol"]),
                grant("p2", "c", ["graph_query"]),
                grant("p3", "b", ALL5),
                grant("p4", "b", ["list_dir", "view_file"]),
            ],
            "revoke": [{"subject": "p1", "repository": "a"}],
            "teeth_add": [grant("p3", "a", ["search_symbol", "search_text"])],
        },
        "isolation": {
            "listen": {"max_inflight": 16, "max_inflight_per_principal": 8},
            "limits": {
                "max_calls_per_minute": 0,
                "max_calls_per_principal_per_minute": 0,
                "max_leases_per_principal": 3,
                "max_graph_leases": 8,
            },
            "concurrency": 6,
            "settle_ms": 400,
            "classes": CLASSES,
            "cases": cases(),
            "teeth_rerun": ["D01", "D02"],
        },
        "interruption": interruption(),
        "compatibility": {
            "cells": compat_cells(),
            "hosts": [
                {"id": h_, "status": "untested", "reason": "no_real_host_run"}
                for h_ in ("claude-code", "github-copilot", "cursor")
            ],
        },
        "capacity": {
            "listen": {"max_inflight": 4, "max_inflight_per_principal": 2, "max_body_bytes": 1048576},
            "limits": {
                "max_calls_per_minute": 10000,
                "max_calls_per_principal_per_minute": 10000,
                "max_leases_per_principal": 1,
                "max_graph_leases": 4,
                "max_total_lease_state_bytes": 1073741824,
                "max_records": 1024,
            },
            "workload": {
                "principals": 4,
                "repositories": 2,
                "clients": 6,
                "calls_per_client": 12,
                "client_principals": ["p1", "p2", "p3", "p4", "p1", "p2"],
                "mix": mix,
                "grants": [grant(p, r, ALL5) for p in ("p1", "p2", "p3", "p4") for r in ("a", "b")],
                "max_attempts": 10,
            },
            "saturation": {"principal": "p1", "other": "p2", "gate_principal": "p3",
                           "holds": 2, "settle_ms": 400, "poll_ms": 100, "max_polls": 50},
        },
        "deadlines": {"request_s": 30, "startup_s": 60, "interrupt_case_s": 120, "recorder_total_s": 300},
        "deployment_model": {
            "process": "one loopback serve-shared process per phase",
            "listen": "127.0.0.1 port 0 (ephemeral)",
            "repositories": "a and b, plus c as the before snapshot of a at the same path",
            "principals": 4,
            "keys": "RS256 generated per run, offline JWKS file",
            "policy_reload_seconds": 0,
            "tls_reverse_proxy": "none",
            "storage": "container-local tmp behind the lock canary",
            "clients": "raw HTTP/1.1 and stdio JSON-RPC lines, plus the mcp SDK client",
        },
        "required_checks": CHECKS,
        "unresolved_limits": UNRESOLVED,
        "honesty": HONESTY,
    }


def main() -> int:
    doc = build()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True) + "\n")
    sys.stdout.write(f"wrote {out} ({out.stat().st_size} bytes)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
