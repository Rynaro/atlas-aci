### AC-S09-001 (event-driven)
GIVEN source bytes change while size and mtime are preserved
WHEN incremental extraction runs
THEN Atlas SHALL extract the changed content.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_same_stat_changed_content

### AC-S09-002 (event-driven)
GIVEN the extraction query fingerprint changes
WHEN incremental extraction runs
THEN the old cached facts SHALL be invalidated.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_producer_change

### AC-S09-003 (event-driven)
GIVEN an extraction-relevant option changes
WHEN incremental extraction runs
THEN the affected file cache entry SHALL be invalidated.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_config_change

### AC-S09-004 (unwanted-behavior)
GIVEN a cache write was interrupted
WHEN the cache entry is read
THEN Atlas SHALL treat that entry as a cache miss.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_partial_cache_rejected

### AC-S09-005 (event-driven)
GIVEN all canonical extraction inputs are unchanged
WHEN extraction is repeated
THEN the canonical extracted facts SHALL equal the first run.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_warm_replay

### AC-S09-006 (event-driven)
GIVEN a complete valid cache entry exists
WHEN the same file is indexed again
THEN the parser invocation count for that file SHALL be zero.
VERIFY: test: mcp-server/tests/slices/test_s09_contract.py::test_unchanged_no_parse

### AC-S09-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S09-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
