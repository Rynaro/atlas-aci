### AC-S14-001 (event-driven)
GIVEN a TSX fixture contains a function component and class component
WHEN the real indexer runs
THEN both declarations SHALL appear at their correct source spans.
VERIFY: test: mcp-server/tests/slices/test_s14_contract.py::test_tsx_definitions

### AC-S14-002 (event-driven)
GIVEN TSX imports an aliased symbol
WHEN extraction runs
THEN the import occurrence SHALL preserve the local alias.
VERIFY: test: mcp-server/tests/slices/test_s14_contract.py::test_tsx_imports

### AC-S14-003 (event-driven)
GIVEN a JSX tag has no semantic producer evidence
WHEN the graph is built
THEN the tag SHALL not be represented as a resolved executable call.
VERIFY: test: mcp-server/tests/slices/test_s14_contract.py::test_jsx_not_fabricated_call

### AC-S14-004 (unwanted-behavior)
GIVEN the TSX parser cannot recover a declaration
WHEN indexing completes
THEN that extraction gap SHALL be reported in completeness diagnostics.
VERIFY: test: mcp-server/tests/slices/test_s14_contract.py::test_tsx_diagnostic

### AC-S14-005 (event-driven)
GIVEN a TSX declaration is renamed
WHEN incremental indexing completes
THEN the canonical TSX facts SHALL equal a clean rebuild.
VERIFY: test: mcp-server/tests/slices/test_s14_contract.py::test_tsx_incremental

### AC-S14-006 (event-driven)
GIVEN unchanged TSX source is indexed twice
WHEN the second build completes
THEN the canonical TSX fact set SHALL equal the first successful result.
VERIFY: test: mcp-server/tests/slices/test_s14_contract.py::test_tsx_replay

### AC-S14-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S14-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
