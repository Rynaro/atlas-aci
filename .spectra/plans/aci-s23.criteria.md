### AC-S23-001 (event-driven)
GIVEN a comparison experiment runs
WHEN its manifest is inspected
THEN all variants SHALL use the same frozen task inputs.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_paired_inputs

### AC-S23-002 (event-driven)
GIVEN release evidence is assembled
WHEN known binding and snapshot regressions are evaluated
THEN all declared P0 regression cases SHALL pass.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_release_integrity_gate

### AC-S23-003 (event-driven)
GIVEN retrieval evaluation completes
WHEN the report is generated
THEN the report SHALL include useful-span results at each declared budget.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_span_budget_matrix

### AC-S23-004 (event-driven)
GIVEN cost measurements are aggregated
WHEN the report is generated
THEN the report SHALL include the declared indexing and query cost categories.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_cost_accounting

### AC-S23-005 (unwanted-behavior)
GIVEN no live agent evaluation was executed
WHEN the report is generated
THEN live engineering outcomes SHALL be marked unmeasured.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_incomplete_live_gate

### AC-S23-006 (event-driven)
GIVEN the same immutable run records are aggregated twice
WHEN both reports are generated
THEN their canonical metrics SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s23_contract.py::test_report_replay

### AC-S23-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S23-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
