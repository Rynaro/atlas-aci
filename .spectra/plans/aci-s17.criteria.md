### AC-S17-001 (event-driven)
GIVEN a query identifies an exact qualified entity
WHEN candidate retrieval runs
THEN that entity SHALL be ranked first.
VERIFY: test: mcp-server/tests/slices/test_s17_contract.py::test_exact_identity_first

### AC-S17-002 (event-driven)
GIVEN a source span uniquely matches a task phrase
WHEN FTS retrieval runs
THEN that span SHALL appear in the bounded candidate set.
VERIFY: test: mcp-server/tests/slices/test_s17_contract.py::test_lexical_candidate

### AC-S17-003 (event-driven)
GIVEN graph generation G2 is published
WHEN search acquires G2
THEN all returned candidates SHALL belong to G2.
VERIFY: test: mcp-server/tests/slices/test_s17_contract.py::test_search_generation

### AC-S17-004 (event-driven)
GIVEN no indexed evidence matches the query
WHEN retrieval runs
THEN the candidate set SHALL be empty.
VERIFY: test: mcp-server/tests/slices/test_s17_contract.py::test_no_match

### AC-S17-005 (event-driven)
GIVEN a repository/path filter excludes a matching candidate
WHEN retrieval runs
THEN the excluded candidate SHALL be absent.
VERIFY: test: mcp-server/tests/slices/test_s17_contract.py::test_search_filter

### AC-S17-006 (event-driven)
GIVEN query and snapshot are unchanged
WHEN retrieval is repeated
THEN the ordered candidate set SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s17_contract.py::test_retrieval_replay

### AC-S17-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S17-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
