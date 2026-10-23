---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-23T19:06:18Z
slice_id: ACI-S01
issue_url: https://github.com/Rynaro/atlas-aci/issues/24
---

# ACI-S01 — Replayable stdio MCP baseline

## Scope

Intent class: CHANGE. Implement one stdio MCP host adapter and a reproducible,
resumable transport-task ledger. This is a transport baseline, not a measured
coding-agent evaluation. No paid model calls, token savings, or engineering
success claims are authorized.

Base: `0506243e2e3a495b1a87f7a3efe55b5d511629c0`, clean `main` at pickup.
Worktree: `/tmp/atlas-aci-aci-s01`; branch: `aci-s01`.
Issue stable marker `atlas-aci:slice:ACI-S01` verified; no predecessor dependencies.
No existing S01 plan, receipt, eval directory, or slice test file was present.
The existing runner is a dispatcher stub; no successful pre-change MCP canary
baseline exists. Preserve that fact when recording the first real baseline.

Implementation scope:
- `scripts/run-canaries.py`: retained CLI plus adapter, manifest identity, ledger.
- `atlas/evals/*`: bounded manifest/fixture, ledger schema or documentation,
  and small replayable transport baseline artifacts as needed.
- `mcp-server/tests/slices/test_s01_*.py`: named behavioral contract tests.
- Planning, execution receipts, and verification evidence: `.spectra/*`.

No source-server edits, dependency/lockfile changes, unrelated rewrites, or other
slice work are planned. Scope amendments must be recorded here before widening
implementation paths; criteria stay unchanged. New eval modules, if useful,
remain under the existing declared eval glob. Keep implementation small; do not
introduce a general harness framework.

Observed sizing: approximately six implementation/test/eval files, public
CLI/ledger additions, existing MCP dependency, medium stakes. Actual
`ramza-rightsize`: score 3, lite. Complexity: 6/12, standard
(scope=2, ambiguity=1, dependencies=1, risk=2). Lite does not require an independent
critic. DISCOVER/CLARIFY omitted because the issue fixes outcome, scope, and ACs.

## Approach

Use the installed Python MCP SDK's `stdio_client`, `StdioServerParameters`, and
`ClientSession` to initialize one real Atlas subprocess and call existing tools.
The observed SDK is 1.27.0; no new SDK dependency is needed. Source inspection
confirmed `atlas-aci serve --repo PATH` uses stdio and `search_symbol` is exposed.

1. Retain public flags `--repo`, `--canaries-dir`, `--host`, `--output`,
   `--ci-gate`, and `--baseline`, including accepted legacy host spellings.
   Add an explicit stdio choice and bounded deadline configuration. Unsupported
   legacy adapters must remain clearly unsupported. Preserve legacy loading/
   scoring where meaningful; do not fabricate a scout report from tool output.
2. Define a versioned JSON task manifest for ordered tool calls. Pin repository
   revision plus source-content fingerprint, task arguments, adapter/tool
   configuration, server/producer identity, and model configuration (unavailable
   when no model runs). Canonical JSON hashes determine task and run IDs.
   Exclude duration, output directory, timestamps, process IDs, and absolute
   temporary fixture location from semantic identity. Include every input that
   affects results; reject ambiguous/unpinned manifests and duplicate task IDs.
3. Capture actual serialized MCP responses with request/response references and
   SHA-256 digests. Distinguish protocol success, server/application errors,
   timeout, and expectation failure. A successful transport round-trip is not
   coding-task success. Model tokens/cost and model-dependent quality metrics
   are null/unavailable with a reason unless actual telemetry was measured.
4. Write each task receipt and the completed run atomically using same-directory
   temporary files plus replace. Verify canonical input identity, schema,
   completion state, referenced payload existence, and payload hashes before
   reuse. Unverified/corrupt receipts cannot skip work. A matching completed run
   must return before constructing or invoking a dispatcher.
5. On interruption, preserve verified completed tasks and resume with the first
   missing/unverified task, keeping task order and stable run ID. Existing
   completed data must not be duplicated. Timing/audit metadata may vary;
   canonical identity and semantic evidence must remain stable.
6. Apply bounded initialization/call/shutdown behavior and terminate child
   processes on timeout/cancellation. Use a real nonresponding subprocess fixture
   to prove deadline handling, not a mock timeout exception. Tests may create
   fixture scripts within temporary directories from the scoped test module.
7. Build/index a tiny versioned Python fixture through the real Atlas indexer,
   then send `search_symbol` through the real stdio subprocess. Persist a small
   local baseline whose manifest, response evidence, and exact replay command
   identify the implementation revision. Keep generated indexes and transient
   fixture directories out of tracked outputs.

Selection evidence from `ramza-score explore`: SDK client plus atomic ledger 89;
handwritten JSON-RPC 70; general host framework 67.5. Existing SDK lifecycle and
the runner's public flags are the reusable patterns. Memory preflight returned
no injected context (gated/no signal).

## Stories

### Story 1: Measure and replay one real MCP task

As an Atlas operator, I want a pinned stdio task run with verified durable
results, so I can reproduce a transport baseline and resume interrupted work.
Timebox: 3 engineering days for the entire slice; stop and report remaining gaps.
Risk: P1. Executor: one standard Vivi allocation. No paid model runs.
Build the adapter, manifest/ledger contract, seven named tests, and the local
transport baseline in the declared paths.

## Acceptance Criteria

Canonical source: issue #24. Extraction means all complete content lines after
the opening marker line and before the closing marker line, preserving the
final LF. The LF terminating the opening marker itself is excluded. This
matches the issue's published hash exactly; no criteria amendment occurred.
Authoritative criteria file: `.spectra/plans/aci-s01.criteria.md`.
SHA-256: `d0570c2647fcb993176bc789651fa9a92612a33282105443d2798b7335c5c537`.

### AC-S01-001 (event-driven)
GIVEN an indexed fixture repository
WHEN the canary host sends search_symbol through stdio
THEN the runner SHALL record the actual MCP response for that call.
VERIFY: test: mcp-server/tests/slices/test_s01_contract.py::test_real_stdio_dispatch

### AC-S01-002 (event-driven)
GIVEN a pinned task manifest
WHEN the runner starts a run
THEN the ledger SHALL include the canonical task and repository fingerprint.
VERIFY: test: mcp-server/tests/slices/test_s01_contract.py::test_run_identity

### AC-S01-003 (event-driven)
GIVEN a completed run with the same canonical inputs
WHEN the same run is requested again
THEN the result store SHALL contain one completed result for that run identity.
VERIFY: test: mcp-server/tests/slices/test_s01_contract.py::test_same_run_replay

### AC-S01-004 (unwanted-behavior)
GIVEN a run interrupted after one completed task
WHEN the runner resumes that run
THEN the runner SHALL resume at the first task without a verified completed receipt.
VERIFY: test: mcp-server/tests/slices/test_s01_contract.py::test_interrupted_resume

### AC-S01-005 (unwanted-behavior)
GIVEN an MCP fixture that never responds
WHEN the configured call deadline expires
THEN the ledger SHALL record a timeout outcome for that task.
VERIFY: test: mcp-server/tests/slices/test_s01_contract.py::test_timeout_is_failure

### AC-S01-006 (state-driven)
GIVEN no live model usage telemetry is available
THEN the ledger SHALL represent model token usage as unavailable.
VERIFY: test: mcp-server/tests/slices/test_s01_contract.py::test_unmeasured_metrics

### AC-S01-007 (event-driven)
GIVEN a completed run receipt verifies against identical canonical inputs
WHEN the run is requested again
THEN the host dispatcher invocation count SHALL be zero.
VERIFY: test: mcp-server/tests/slices/test_s01_contract.py::test_completed_run_no_dispatch

### AC-S01-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S01-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

Run from `mcp-server/`, preferring repository-frozen uv commands:
- `uv run --frozen pytest tests/slices/test_s01_contract.py -q`
- `uv run --frozen pytest -q`
- `uv run --frozen ruff check src/ tests/`
- `uv run --frozen ruff format --check src/ tests/`
- `uv run --frozen mypy src/`
- `uv run --frozen ruff check ../scripts/ --exclude fingerprint-fixture`
- `uv run --frozen ruff format --check ../scripts/ --exclude fingerprint-fixture`
- `uv run --frozen mypy ../scripts/`
- `bash ../scripts/test-harden-gate-classify.sh`
- `bash ../scripts/test-verify-probe-verdict.sh`
- `bash ../scripts/test-verify-export-size.sh`

Also lint/typecheck new eval Python modules if any, using the same configuration.
The CI workflow defines frozen sync/lock checks and separate Docker/export jobs;
report remote/platform-only jobs accurately rather than claiming local execution.
No lockfile change is planned.

At preflight uv was absent from PATH. A usable existing interpreter is
`/Users/henrique/workspace/oss/agents/atlas-aci/mcp-server/.venv/bin/python`
(MCP 1.27.0, pytest 9.0.3, jsonschema 4.26.0). Equivalent `python -m pytest/ruff/mypy`
commands with `PYTHONPATH=<worktree>/mcp-server/src` may validate this checkout;
record exact commands/environment and do not accidentally test the original
checkout's installed source. Prefer a worktree-local environment if available.

Behavioral evidence must include real-process search output, actual timeout,
changed canonical input invalidation, corrupted receipt handling, interrupted
resume, and zero dispatch on completed replay. All seven product AC names must
match the frozen issue criteria. Do not count a mock of the behavior under test
as evidence.

## Execution receipt and re-entry

One authoritative receipt:
`.spectra/state/aci-s01.execution.json`.
Initialize IN_PROGRESS before product implementation. Include slice_id, issue_url,
criteria_sha256, base_commit, output_commit (null until recorded), declared_paths,
output_digests, status, and per-AC command/exit code/evidence location. Preserve
identity and any verified evidence when resuming; do not create a second receipt.

For AC-S01-RUN-02, capture the real IN_PROGRESS receipt as verification evidence,
resume this execution with the frozen criteria verification, and compare
slice_id/criteria_sha256 with the eventual receipt. Use the authoritative receipt
as input, not an invented completed phase history.

For AC-S01-RUN-01, after product gates pass, record an implementation output commit
and manifest every declared tracked implementation file's SHA-256. In a clean
checkout at that commit, verify the receipt, input hashes, and existing evidence;
take the ALREADY_SATISFIED path without implementation writes. Require exact
before/after digest-manifest equality and `git diff --exit-code <output_commit>
-- <declared implementation paths>`. Planning/audit receipt changes do not
alter implementation digests. Record exact commands/results. A follow-up metadata
commit may hold the receipt referencing the earlier implementation commit;
avoid circular self-hashing of receipt/output commit.

A stale identity, failed product gate, missing evidence, or changed implementation
must not yield COMPLETE/ALREADY_SATISFIED. Final COMPLETE requires all nine ACs,
applicable checks, matching output digests, and reviewable bounded changes.
No issue closure, merge, release, or deployment is part of this handoff.

## Confidence

The Assemble confidence rubric and actual verdict are recorded in
`aci-s01.state.json` and `ramza-calibration.jsonl`; no product success is implied.
Inputs reflect observed SDK availability, explicit issue criteria, bounded file
ownership, and environment/testing constraints.

## Rejected Alternatives

- Handwritten JSON-RPC (70): unnecessary protocol/lifecycle maintenance when the
  installed MCP SDK already provides initialization and client sessions.
- General multi-host framework (67.5): exceeds the one-adapter scope and timebox.
- Model-driven canary evaluation now: requires operator budget/telemetry absent
  from this slice; transport results cannot support coding-agent success claims.

## Risks

| Risk | Mitigation |
|---|---|
| Stale or incomplete fingerprints permit false replay | Hash canonical task, source and tool inputs; verify referenced evidence before skip. |
| Hung subprocess survives deadline | Real hanging fixture and bounded cleanup/cancellation tests. |
| Tool application error looks like success | Inspect actual MCP and Atlas payload errors; preserve raw response. |
| New checkout tests old editable installation | Explicit source PYTHONPATH/worktree environment and captured commands. |
| Claims exceed observation | Label baseline transport-only; mark model metrics unavailable. |

## Assemble handoff

```json
{
  "slice_id": "ACI-S01",
  "executor": "Vivi",
  "tier": "standard",
  "worktree": "/tmp/atlas-aci-aci-s01",
  "branch": "aci-s01",
  "base_commit": "0506243e2e3a495b1a87f7a3efe55b5d511629c0",
  "criteria_sha256": "d0570c2647fcb993176bc789651fa9a92612a33282105443d2798b7335c5c537",
  "receipt": ".spectra/state/aci-s01.execution.json",
  "scope": ["scripts/run-canaries.py", "atlas/evals/*", "mcp-server/tests/slices/test_s01_*.py"],
  "paid_model_runs": false,
  "max_executors": 1
}
```

Planning tools are the installed Ramza 1.1.0 scripts at
`/Users/henrique/workspace/oss/agents/atlas-aci/.eidolons/ramza/bin/`.
State schema is `ramza/plan-state.v1`; handoff envelope is ECL 2.0 and must pass
the full vendored JSON Schema plus independent spec/criteria digest checks.
