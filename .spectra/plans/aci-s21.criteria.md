### AC-S21-001 (event-driven)
GIVEN a valid context artifact records test T executing line L
WHEN coverage import completes
THEN the graph SHALL contain TEST_EXECUTES evidence from T to L.
VERIFY: test: mcp-server/tests/slices/test_s21_contract.py::test_test_execution_mapping

### AC-S21-002 (event-driven)
GIVEN a coverage edge is inspected
WHEN its provenance is returned
THEN the edge SHALL identify the imported run and environment as one observation record.
VERIFY: test: mcp-server/tests/slices/test_s21_contract.py::test_run_provenance

### AC-S21-003 (unwanted-behavior)
GIVEN the artifact source hash differs from the target snapshot
WHEN import is attempted
THEN the artifact SHALL not become authoritative evidence for that snapshot.
VERIFY: test: mcp-server/tests/slices/test_s21_contract.py::test_coverage_source_mismatch

### AC-S21-004 (event-driven)
GIVEN a line was executed without assertion metadata
WHEN coverage evidence is queried
THEN Atlas SHALL describe it only as observed execution.
VERIFY: test: mcp-server/tests/slices/test_s21_contract.py::test_no_assertion_inference

### AC-S21-005 (event-driven)
GIVEN the same run artifact is imported twice
WHEN the second import completes
THEN the full canonical observation set including provenance SHALL equal the first import result.
VERIFY: test: mcp-server/tests/slices/test_s21_contract.py::test_coverage_reimport

### AC-S21-006 (event-driven)
GIVEN the same test/span appears in a different run
WHEN the second run is imported
THEN both run provenance records SHALL remain inspectable.
VERIFY: test: mcp-server/tests/slices/test_s21_contract.py::test_distinct_run_preserved

### AC-S21-007 (unwanted-behavior)
GIVEN a coverage artifact lacks verifiable source/build identity
WHEN import is attempted
THEN the artifact SHALL remain unpublished as matching-snapshot evidence.
VERIFY: test: mcp-server/tests/slices/test_s21_contract.py::test_missing_coverage_identity

### AC-S21-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S21-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
