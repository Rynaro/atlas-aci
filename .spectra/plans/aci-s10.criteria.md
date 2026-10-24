### AC-S10-001 (event-driven)
GIVEN an imported declaration changes
WHEN incremental indexing completes
THEN its canonical graph SHALL equal a clean rebuild of the new source.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_export_change_equivalence

### AC-S10-002 (event-driven)
GIVEN a referenced file is deleted
WHEN incremental indexing completes
THEN its canonical graph SHALL equal the deletion fixture clean build.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_delete_equivalence

### AC-S10-003 (event-driven)
GIVEN a file is renamed without changing its declarations
WHEN incremental indexing completes
THEN its canonical graph SHALL equal the rename fixture clean build.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_rename_equivalence

### AC-S10-004 (event-driven)
GIVEN resolver configuration changes
WHEN incremental indexing completes
THEN its canonical graph SHALL equal a clean rebuild with that configuration.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_resolver_config_equivalence

### AC-S10-005 (unwanted-behavior)
GIVEN the dependency closure cannot be determined
WHEN incremental binding is requested
THEN Atlas SHALL use the documented conservative rebuild path.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_unknown_dependency_fallback

### AC-S10-006 (event-driven)
GIVEN an incremental update completed successfully
WHEN the same update is retried
THEN the canonical graph SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s10_contract.py::test_incremental_replay

### AC-S10-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S10-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
