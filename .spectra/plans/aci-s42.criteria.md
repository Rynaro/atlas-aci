### AC-S42-001 (event-driven)
GIVEN the declared multi-principal fixture runs
WHEN its evidence access ledger is checked
THEN the unauthorized evidence access count SHALL be zero.
VERIFY: test: mcp-server/tests/slices/test_s42_contract.py::test_negative_isolation_suite

### AC-S42-002 (event-driven)
GIVEN the declared interruption matrix runs
WHEN each case recovers
THEN each recovered server SHALL expose a valid completed generation.
VERIFY: test: mcp-server/tests/slices/test_s42_contract.py::test_failure_recovery_suite

### AC-S42-003 (event-driven)
GIVEN shared-readiness evidence is generated
WHEN the report is inspected
THEN the report SHALL identify each actually tested host/protocol combination.
VERIFY: test: mcp-server/tests/slices/test_s42_contract.py::test_compatibility_matrix

### AC-S42-004 (event-driven)
GIVEN the bounded concurrency workload completes
WHEN metrics are aggregated
THEN the report SHALL include its configured limits and observed resource use as one workload record.
VERIFY: test: mcp-server/tests/slices/test_s42_contract.py::test_capacity_record

### AC-S42-005 (unwanted-behavior)
GIVEN one required shared-readiness check fails
WHEN the report is generated
THEN the shared-readiness verdict SHALL be blocked.
VERIFY: test: mcp-server/tests/slices/test_s42_contract.py::test_failed_gate_visible

### AC-S42-006 (event-driven)
GIVEN the same immutable run records are reaggregated
WHEN the report is rebuilt
THEN the canonical shared-readiness metrics SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s42_contract.py::test_shared_report_replay

### AC-S42-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S42-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
