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
