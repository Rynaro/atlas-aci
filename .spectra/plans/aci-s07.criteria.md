### AC-S07-001 (event-driven)
GIVEN A imports B and A calls B
WHEN both facts are persisted
THEN the graph SHALL retain both typed relationships.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_parallel_relations

### AC-S07-002 (event-driven)
GIVEN a directed call A to B is exported and imported
WHEN the imported graph is queried
THEN the call direction SHALL remain A to B.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_direction_preserved

### AC-S07-003 (event-driven)
GIVEN only blank lines are inserted before a declaration
WHEN the file is reindexed
THEN the entity identity SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_line_move_identity

### AC-S07-004 (event-driven)
GIVEN a source-backed assertion is stored
WHEN its evidence is read
THEN the evidence SHALL identify the source content hash and span as one provenance record.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_source_evidence

### AC-S07-005 (unwanted-behavior)
GIVEN migration stops before publication
WHEN a legacy query runs
THEN the previous usable generation SHALL remain available.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_migration_interruption

### AC-S07-006 (event-driven)
GIVEN the same canonical assertions are imported twice
WHEN the second import completes
THEN the full canonical assertion set including provenance SHALL equal the first import result.
VERIFY: test: mcp-server/tests/slices/test_s07_contract.py::test_assertion_replay

### AC-S07-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S07-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
