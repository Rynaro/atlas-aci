### AC-S04-001 (event-driven)
GIVEN a recognized fixture language has no enabled extraction provider
WHEN status is requested
THEN that language SHALL be reported as unsupported for extraction.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_unsupported_extension

### AC-S04-002 (event-driven)
GIVEN a producer supplies definitions but no semantic binding
WHEN capabilities are serialized
THEN semantic binding SHALL be reported as unsupported for that producer.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_producer_specific_capability

### AC-S04-003 (unwanted-behavior)
GIVEN a fixture parser fails
WHEN status is requested
THEN the failed file SHALL appear in completeness diagnostics.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_parse_failure_visible

### AC-S04-004 (state-driven)
GIVEN facts from a failed refresh were retained
THEN the retained facts SHALL be marked stale.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_retained_stale_fact

### AC-S04-005 (event-driven)
GIVEN the same generation is queried without changes
WHEN status is requested twice
THEN the canonical capability payload SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s04_contract.py::test_status_replay

### AC-S04-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S04-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
