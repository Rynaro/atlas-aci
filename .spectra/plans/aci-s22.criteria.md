### AC-S22-001 (event-driven)
GIVEN test T executed the changed span in the exact before snapshot of the requested diff
WHEN validation planning runs
THEN T SHALL be recommended with the before-snapshot execution evidence.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_observed_test_suggestion

### AC-S22-002 (event-driven)
GIVEN a test is suggested only by static relationships
WHEN the plan is serialized
THEN its basis SHALL be labeled structural.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_static_test_label

### AC-S22-003 (event-driven)
GIVEN a changed span has no mapped test evidence
WHEN planning runs
THEN the plan SHALL expose a validation coverage gap for that span.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_uncovered_change

### AC-S22-004 (event-driven)
GIVEN a validation plan is requested
WHEN planning completes
THEN the repository subprocess launch count SHALL be zero.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_no_execution

### AC-S22-005 (unwanted-behavior)
GIVEN available coverage matches neither the declared before snapshot nor the after snapshot
WHEN planning runs
THEN those observations SHALL not be treated as matching-build coverage.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_stale_coverage

### AC-S22-006 (event-driven)
GIVEN impact, evidence and ranker configuration are unchanged
WHEN planning is repeated
THEN the canonical recommendation order SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s22_contract.py::test_validation_replay

### AC-S22-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S22-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
