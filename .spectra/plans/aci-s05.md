---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-23T19:31:12Z
slice_id: ACI-S05
issue_url: https://github.com/Rynaro/atlas-aci/issues/28
---

# ACI-S05 — Explicit opt-in for repository test execution

## Scope

Intent: CHANGE. Default graph serving must reject test_dry_run before repository
code can be launched; an explicit operator opt-in retains the existing bounded
execution behavior. This is not an OS sandbox and does not add execution to
graph suggestions or any new tool.

Base: `f81231ef3fd4bbc89fd444f213be521d6063e99e`, clean main after PR #65.
Worktree `/tmp/atlas-aci-aci-s05`; branch `aci-s05`.
Stable issue marker and all seven criteria verified. No predecessor dependencies
or existing S05 plan/receipt. S01 planning/receipt/evidence are immutable inputs.

Declared product paths:
- `mcp-server/src/atlas_aci/config.py`
- `mcp-server/src/atlas_aci/server.py`
- `mcp-server/src/atlas_aci/tools/test_dry_run.py`
- `mcp-server/tests/slices/test_s05_*.py`
- `README.md`

### Scope amendment S05-PATH-01

Add `mcp-server/tests/test_server.py` solely to opt its existing
`test_test_dry_run_truncated_reflects_actual_bytes_not_a_proxy` test into execution.
That test directly calls the tool under a default Config and currently expects
successful bounded output; changing the default must not silently discard its
existing regression coverage. Do not broaden or rewrite unrelated tests.
This amendment changes no frozen criterion or product scope. No CLI source edit
is required: Config can read the explicit operator environment at construction.

Planning/evidence/receipt paths stay under `.spectra/` with S05 names; use
`aci-s05.calibration.jsonl` rather than modifying S01's shared calibration log.

Observed right-sizing: six files, public configuration/manifest behavior,
security boundary, high stakes. `ramza-rightsize` computed full (score 5).
Complexity computed 7/12 extended: scope 2, ambiguity 1, dependencies 1, risk 3.
Independent maker/checker review is mandatory before Assemble.
DISCOVER/CLARIFY are unnecessary because the issue defines outcome and scope.

## Approach

### Entry-point evidence

Source scan on the actual base found:
- `server.py:438-480`: stdio callback calls `dispatch_tool_call`.
- `server.py:365-403`: direct dispatcher routes test_dry_run at lines 392-393.
- `tools/test_dry_run.py:27-119`: directly importable tool; its one
  `asyncio.create_subprocess_exec` at line 66 launches bundle/rails, pytest, or
  npx/jest according to file extension. All branches converge at this spawn.
- `tools/search_text.py:61`: separate ripgrep subprocess for navigation;
  it does not run repository test code and must remain available.
- `__main__.py:63,218`: serve/tools construct Config, so operator environment
  read by Config reaches real stdio and CLI manifest without new CLI flags.
- `tests/test_server.py:701-736`: existing enabled-output regression described
  in S05-PATH-01.

### Selected implementation

1. Add a typed Config boolean `test_execution_enabled`, disabled by default.
   Operator enablement is `ATLAS_ACI_ENABLE_TEST_EXECUTION=1` at server launch;
   only the exact string `1` enables environment-based execution. Missing,
   empty, `0`, `false`, and malformed values remain disabled. A programmatic
   caller can explicitly construct Config with True/False; explicit False
   overrides environment-derived defaults. Use a per-instance default factory,
   not a module-import-time environment snapshot. Config controls the switch;
   request arguments, repository files, graph results, and environment mutation
   after Config construction cannot grant execution. Avoid bool(string).
2. Put the authoritative guard inside `test_dry_run` before reading request
   keys, inspecting test files, detecting a runner, or creating a subprocess.
   Deny with existing `ToolError("FORBIDDEN", ...)`, a concise disabled-capability
   message, and a valid non-retrying hint. This protects direct Python calls,
   direct dispatch, and stdio through one shared guard. Unknown request fields
   such as `enabled` or `execution_profile` must not bypass it.
3. Keep the tool name and existing input schema. Expose enabled state in the
   manifest from the same Config: human-readable description plus machine-
   readable MCP Tool metadata (`_meta`, with a namespaced execution-enabled
   boolean). Use an SDK-supported field and test the serialized stdio
   `list_tools` output, not only an in-process dictionary. Do not falsely mark
   an enabled code-executing tool read-only or claim OS isolation.
4. Preserve enabled execution: supported runners/argument lists, containment,
   case filtering, timeout, separate stdout/stderr byte caps, truncation fields,
   and central response ceiling. The existing timeout policy kills and reaps
   the immediate subprocess; it does not guarantee descendant-tree cleanup.
   Document that precise policy and test the immediate child. Do not introduce
   an OS sandbox or broad process-management refactor.
5. Update README tool description and execution-boundary guidance with the
   default disabled state, exact operator environment example, manifest state,
   denial behavior, enabled timeout/output limits, and immediate-child timeout
   policy. Clarify that timeout and output caps do not supply OS sandboxing.
   Correct the tool module's misleading “sandboxed test execution” wording.
6. Add the named S05 tests using actual controlled executable fixtures and a
   real MCP subprocess. Preserve existing bounded-output test coverage by the
   single explicit opt-in adjustment in S05-PATH-01.

Reusable patterns: Config dataclass, ToolError and stdio serialization, current
manifest factory, existing child timeout/kill/wait path, S01 real MCP test setup.
No package addition or lockfile change is expected. Prior CRYSTALIUM persistence
rejection is terminal; no retry or payload redirection is part of this work.

## Stories

### Story 1: Execution requires operator consent

As an Atlas operator, I want default graph serving to reject repository test
execution, so navigation does not silently execute repository code.
Timebox: 2 engineering days total. Risk P0. Executor: one standard Vivi
allocation with independent verification as required by the full planning tier.
Deliver default-denial, explicit enablement, preserved bounds, discoverable
capability state, documentation, and observed integration evidence.

## Acceptance Criteria

Exact content lines between issue marker lines, preserving final LF and excluding
the opening marker's terminating LF, are materialized in
`.spectra/plans/aci-s05.criteria.md`.
SHA-256:
`ea6a3baf17e73a01314bea6662c3903840b29265fb4e92d37c5d1eda09f5333a`.
No amendment to criteria.

### AC-S05-001 (event-driven)
GIVEN the default graph profile is active
WHEN test_dry_run is called
THEN Atlas SHALL reject the call before creating a repository subprocess.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_default_no_spawn

### AC-S05-002 (optional-feature)
GIVEN the operator enables the execution profile
THEN test_dry_run SHALL use the existing bounded execution path.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_explicit_enabled_mode

### AC-S05-003 (unwanted-behavior)
GIVEN an enabled test subprocess exceeds its deadline
WHEN the timeout guard fires
THEN Atlas SHALL terminate the subprocess according to the documented process policy.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_timeout_preserved

### AC-S05-004 (event-driven)
GIVEN tool capabilities are requested
WHEN the manifest is produced
THEN the test execution capability SHALL identify its enabled state.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_profile_visible

### AC-S05-005 (event-driven)
GIVEN execution is disabled
WHEN the same forbidden test request is submitted twice
THEN the repository sentinel SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_denial_replay

### AC-S05-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S05-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.

## Verification

### Behavioral evidence

- `test_default_no_spawn`: place an executable sentinel named pytest in a
  controlled PATH. If launched it writes a marker. Call the direct tool, direct
  dispatcher, and real stdio server under disabled Config; expect FORBIDDEN,
  unchanged marker, and no sentinel invocation. Also exercise request-supplied
  opt-in fields and deny before invalid/missing path work where feasible.
- `test_explicit_enabled_mode`: explicit operator environment on a real server
  launches the same sentinel through the normal path. Assert marker, exit code,
  output, selected arguments/case, and byte truncation behavior. Explicit Config
  False must still deny with the enabling environment present.
- `test_timeout_preserved`: real executable records its PID and then blocks
  past a short configured deadline. Assert TIMEOUT and that the recorded immediate
  child is no longer alive/reaped. Test the real existing timeout path, not a
  mocked exception. Fixture must not spawn descendants; claims are limited to
  the documented policy.
- `test_profile_visible`: compare disabled/enabled manifest and actual stdio
  `list_tools` serialization; field state agrees with Config and tool name
  stays present. Verify default read-only navigation still succeeds.
- `test_denial_replay`: send the same forbidden request twice through real
  stdio and confirm identical capability-denial meaning and unchanged sentinel.
  Scrub enabling ambient environment in disabled tests; test malformed values
  and per-instance environment evaluation explicitly.

The sentinel is a real executable controlling side effects; avoid mocked
`create_subprocess_exec` as evidence for no execution or timeout. An optional
spawn spy can supplement the filesystem evidence, but cannot replace it.

### Coverage and self-consistency

Three independently useful decompositions converge on the same responsibilities:
- Runtime sequence: construct policy -> report capability -> deny or execute ->
  enforce timeout/output -> record acceptance/re-entry evidence.
- File ownership: Config policy -> server metadata -> tool guard/bounds ->
  README contract -> slice integration tests + existing regression opt-in.
- Failure scenarios: absent/malformed enablement -> direct-call/stdio bypass ->
  misleading manifest -> enabled timeout/output regression -> stale receipt.

Shared requirements covered by all three views: default-denial, explicit operator
control, actual capability state, preserved enabled bounds, real observable
tests. They cover all five product ACs; receipt verification covers both RUN ACs.
No decomposition introduces an additional execution entry point or sandbox claim.
Independent critique must challenge this mapping and the two-day timebox.

### Commands

Use `/tmp/aci-s01-tools/bin/uv` with
`UV_CACHE_DIR=/tmp/aci-s01-uv-cache`, from this worktree's `mcp-server/`.
Sync frozen dev dependencies if needed. Run:
- `uv run --frozen pytest tests/slices/test_s05_contract.py -q`
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

Record actual command, exit code, logs, and environment; use current worktree
source rather than an editable installation pointing at the prior S01 checkout.
Run required CI and report platform-only checks accurately. No paid model run.

### Slice execution receipt

Initialize `.spectra/state/aci-s05.execution.json` IN_PROGRESS before coding,
including stable slice_id/criteria_sha256, base_commit, issue_url, declared_paths,
output_commit (initially null), output_digests, and one result per AC. Preserve
a genuine intermediate receipt for RUN-02; resume with the same authoritative
path/identity and frozen hash, recording evidence.

For RUN-01, after passing product checks, create a bounded implementation commit,
manifest declared tracked implementation-file SHA-256 digests, and re-enter at
that commit in a clean checkout using the verified receipt. Require identical
before/after manifests and `git diff --exit-code <output_commit> -- <paths>`.
Use a later receipt metadata commit if needed to avoid self-referential hashing.
Do not claim COMPLETE from plan lint or unit mocks. Final completion requires all
seven ACs and applicable checks. No merge/release/deployment is in this handoff.

## Confidence

Independent ATLAS review approved at Test with no blockers or prescriptions;
actual refine rubric 4.6/5, minimum 4, pass. At Assemble, actual confidence
rubric computed 96.25 AUTO_PROCEED (pattern_match 95, requirement_clarity 100,
decomposition_stability 95, constraint_compliance 95). Criteria were then frozen
at the issue digest. These are planning readiness results, not product evidence.

## Rejected Alternatives

Actual Explore scores:
- Config plus authoritative tool-level guard: 93.5, selected; covers every entry.
- Dispatch-only gate: 69; a direct tool call bypasses the execution boundary.
- Separate execution server/profile process: 74.5; separation is useful at wider
  scope, but adds packaging/protocol work beyond this bounded compatibility fix.

## Risks

| Risk | Mitigation |
|---|---|
| Truthy malformed config enables code | Only exact operator value 1; explicit False wins; request keys never control Config. |
| Direct calls bypass dispatcher policy | Authoritative guard in tool function before path/runner operations. |
| Metadata dropped in MCP serialization | Verify real list_tools output with installed SDK field. |
| Timeout overstated as process-tree sandbox | Preserve and document immediate-child kill/reap policy; real PID fixture. |
| Existing bounds regression is hidden by default denial | Explicitly enable only the existing regression test covered by scope amendment. |
| Ambient test environment enables execution unexpectedly | Scrub and parameterize environment; validate per-instance policy snapshot. |

## Independent checker contract

Independent review was performed at phase T and approved; its evidence is in
aci-s05.critique.json and the state gate history. The following contract governed
the review: receive only this plan, criteria file, and state; ignore author
identity while judging. Use installed `.eidolons/ramza/skills/critic/SKILL.md`.
Run structural/EARS checks first, then score clarity, completeness,
actionability, efficiency, testability via actual `ramza-score --rubric refine
--cycle 1`. A read-only scout may omit --state and return the exact command,
JSON output, and findings to the orchestrator for recording.

Report exactly: verdict/tool statuses; five per-dimension findings tied to plan
sections/ACs; ordered actionable prescriptions (or none). Challenge direct/stdio
coverage, strict operator configuration, manifest wire visibility, timeout policy,
test scope amendment, and the seven-AC evidence map. Checker identity must differ
from `ramza-backlog-order-s05`; no author self-approval.

After review is accepted, the orchestrator records the real critic evidence and
identities through ramza-gate critic. Author then follows T->R->T if changes are
required, or T->A; score confidence, declare six implementation globs, freeze
criteria, emit/validate handoff, then DONE. No lifecycle reset or initial freeze
replay.

## Handoff mechanics

Installed helpers:
`/Users/henrique/workspace/oss/agents/atlas-aci/.eidolons/ramza/bin/`.
Package Ramza 1.1.0; state schema ramza/plan-state.v1; ECL 2.0 if emitted.

Envelope payload paths must be relative to the envelope directory:
`artifact.path="aci-s05.md"`,
`x_ramza_acceptance_criteria.path="aci-s05.criteria.md"`.
Validate full vendored ecl-envelope.v2.json and plan-state.v1.json using
jsonschema Draft202012Validator with FormatChecker, independently recompute
payload/criteria hashes, run ramza-verify-emit with full spec/envelope paths, then
have the orchestrator run mechanical `eidolons verify-envelope <envelope> --block`
and retain its verify_pass trace before Vivi receives it. Do not fabricate that
trace. No envelope is emitted before full-tier checker/Assemble completion.
