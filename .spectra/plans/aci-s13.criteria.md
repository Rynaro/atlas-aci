### AC-S13-001 (event-driven)
GIVEN a query returns a source resource
WHEN the resource is opened after a worktree edit
THEN the resource SHALL return the original snapshot bytes.
VERIFY: test: mcp-server/tests/slices/test_s13_contract.py::test_exact_source_resource

### AC-S13-002 (event-driven)
GIVEN an unexpired leased cursor exists and a newer generation is published after page one
WHEN the saved cursor requests page two
THEN page two SHALL use the original generation.
VERIFY: test: mcp-server/tests/slices/test_s13_contract.py::test_continuation_snapshot

### AC-S13-003 (unwanted-behavior)
GIVEN the referenced generation was legitimately retired
WHEN a resource is opened
THEN Atlas SHALL return an explicit expired-reference result.
VERIFY: test: mcp-server/tests/slices/test_s13_contract.py::test_expired_reference

### AC-S13-004 (unwanted-behavior)
GIVEN a cursor is presented under a different repository or query scope
WHEN continuation is requested
THEN Atlas SHALL reject the cursor.
VERIFY: test: mcp-server/tests/slices/test_s13_contract.py::test_cursor_scope_mismatch

### AC-S13-005 (event-driven)
GIVEN a source span exceeds the full response byte budget and that budget meets the documented minimum envelope size
WHEN the resource is read
THEN the response SHALL stay within the byte limit.
VERIFY: test: mcp-server/tests/slices/test_s13_contract.py::test_resource_budget

### AC-S13-006 (event-driven)
GIVEN an unexpired cursor is consumed twice
WHEN both continuation requests complete
THEN both canonical pages SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s13_contract.py::test_cursor_replay

### AC-S13-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S13-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
