### AC-S39-001 (event-driven)
GIVEN the same canonical query is sent over stdio and HTTP
WHEN both complete
THEN their canonical graph evidence SHALL be equivalent.
VERIFY: test: mcp-server/tests/slices/test_s39_contract.py::test_http_query_equivalence

### AC-S39-002 (state-driven)
GIVEN HTTP serving is enabled without S40 shared mode
THEN the listener SHALL bind only to loopback.
VERIFY: test: mcp-server/tests/slices/test_s39_contract.py::test_loopback_default

### AC-S39-003 (unwanted-behavior)
GIVEN a client disconnects during a query
WHEN cancellation propagates
THEN the request reader lease SHALL be released.
VERIFY: test: mcp-server/tests/slices/test_s39_contract.py::test_disconnect_cleanup

### AC-S39-004 (event-driven)
GIVEN requests exceed the configured in-flight limit
WHEN admission runs
THEN the active-query count SHALL not exceed the configured limit.
VERIFY: test: mcp-server/tests/slices/test_s39_contract.py::test_concurrency_bound

### AC-S39-005 (unwanted-behavior)
GIVEN an origin violates the configured transport policy
WHEN the request arrives
THEN the server SHALL reject it before query execution.
VERIFY: test: mcp-server/tests/slices/test_s39_contract.py::test_invalid_origin

### AC-S39-006 (event-driven)
GIVEN a read request is retried against the same snapshot and reaches the same deterministic frontier
WHEN both HTTP requests complete
THEN their canonical results SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s39_contract.py::test_http_replay

### AC-S39-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S39-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
