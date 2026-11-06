### AC-S40-001 (unwanted-behavior)
GIVEN a principal lacks repository access
WHEN a query names that repository
THEN the repository traversal count SHALL be zero.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_unauthorized_before_traversal

### AC-S40-002 (unwanted-behavior)
GIVEN a principal presents another scope source URI
WHEN resource retrieval runs
THEN Atlas SHALL reject the access.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_resource_authorization

### AC-S40-003 (event-driven)
GIVEN two principals have different authorized scopes
WHEN both submit the same query text
THEN each cache lookup SHALL use its authorization scope identity.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_cache_scope_isolation

### AC-S40-004 (unwanted-behavior)
GIVEN repository access is revoked after a cursor is issued
WHEN the cursor is resumed
THEN Atlas SHALL reject the continuation.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_revoked_cursor

### AC-S40-005 (event-driven)
GIVEN a path reaches an unauthorized repository
WHEN cross-repository traversal runs
THEN the protected repository SHALL not be expanded.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_cross_repo_scope

### AC-S40-006 (event-driven)
GIVEN principal, policy and snapshot are unchanged
WHEN the same read request is repeated
THEN the authorization decision SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_auth_replay

### AC-S40-007 (unwanted-behavior)
GIVEN an allowed query warmed a cache before repository access was revoked
WHEN the same principal repeats the query
THEN the response SHALL contain no evidence from the revoked repository.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_cache_revocation

### AC-S40-008 (unwanted-behavior)
GIVEN a principal lacks access to the target repository
WHEN each legacy tool is invoked in a parameterized dispatch fixture
THEN repository read or execution activity SHALL remain zero for every denied tool call.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_legacy_tool_boundary

### AC-S40-009 (unwanted-behavior)
GIVEN credentials are absent or invalid for shared mode
WHEN any repository operation is requested
THEN Atlas SHALL deny the request before repository access.
VERIFY: test: mcp-server/tests/slices/test_s40_contract.py::test_invalid_credentials

### AC-S40-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S40-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
