### AC-S11-001 (event-driven)
GIVEN a pinned legacy DSL fixture
WHEN graph_query executes it
THEN the normalized result SHALL match the legacy compatibility fixture.
VERIFY: test: mcp-server/tests/slices/test_s11_contract.py::test_legacy_compatibility

### AC-S11-002 (event-driven)
GIVEN op=status is requested with a supported schema version
WHEN graph_query responds
THEN the response SHALL validate against the published status output schema.
VERIFY: test: mcp-server/tests/slices/test_s11_contract.py::test_structured_status

### AC-S11-003 (unwanted-behavior)
GIVEN an unknown operation is submitted
WHEN input validation runs
THEN Atlas SHALL reject the unsupported operation explicitly.
VERIFY: test: mcp-server/tests/slices/test_s11_contract.py::test_invalid_operation

### AC-S11-004 (unwanted-behavior)
GIVEN a supported operation fails during execution
WHEN the MCP result is produced
THEN the result SHALL set isError to true.
VERIFY: test: mcp-server/tests/slices/test_s11_contract.py::test_tool_error_flag

### AC-S11-005 (event-driven)
GIVEN a valid query has no matching evidence
WHEN the MCP result is produced
THEN the result SHALL represent a successful empty answer.
VERIFY: test: mcp-server/tests/slices/test_s11_contract.py::test_no_evidence_success

### AC-S11-006 (event-driven)
GIVEN the same request targets the same snapshot
WHEN the request is repeated
THEN the canonical response payload SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s11_contract.py::test_structured_replay

### AC-S11-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S11-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
