### AC-S06-001 (event-driven)
GIVEN the source mount is read-only and the configured index root is writable
WHEN indexing runs
THEN the graph generation SHALL be written beneath the configured index root.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_readonly_source_index

### AC-S06-002 (event-driven)
GIVEN the read-only fixture is indexed
WHEN indexing completes
THEN the source-tree content digest SHALL equal its pre-index digest.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_source_unchanged

### AC-S06-003 (event-driven)
GIVEN an external index exists
WHEN serve is launched with the same graph-root configuration
THEN graph queries SHALL read that external index.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_serve_external_root

### AC-S06-004 (state-driven)
GIVEN no graph-root override is configured
THEN Atlas SHALL retain the documented repository-local default location.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_default_compatibility

### AC-S06-005 (event-driven)
GIVEN two repositories use the same external root
WHEN both are indexed
THEN each repository SHALL have a distinct storage identity.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_root_identity_isolation

### AC-S06-006 (event-driven)
GIVEN unchanged source is indexed into the same external root
WHEN the index command is repeated
THEN the canonical graph fact set SHALL equal the first successful result.
VERIFY: test: mcp-server/tests/slices/test_s06_contract.py::test_external_replay

### AC-S06-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S06-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
