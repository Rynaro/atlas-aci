### AC-S03-001 (event-driven)
GIVEN a completed replacement is published
WHEN a new request acquires a reader
THEN the request SHALL read the published generation.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_new_request_new_generation

### AC-S03-002 (state-driven)
GIVEN a request holds generation G1 while G2 is published
THEN all database reads in that request SHALL use G1.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_active_request_pinned

### AC-S03-003 (unwanted-behavior)
GIVEN a build fails before publication
WHEN a request acquires a reader
THEN the request SHALL acquire the last completed generation.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_failed_publish

### AC-S03-004 (event-driven)
GIVEN two generations have the same schema epoch
WHEN the second generation is published
THEN new-reader freshness SHALL be determined by generation identity.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_same_epoch_replacement

### AC-S03-005 (unwanted-behavior)
GIVEN a request exits through cancellation
WHEN request cleanup runs
THEN the generation reader lease SHALL be released.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_reader_release

### AC-S03-006 (event-driven)
GIVEN unchanged repository, worktree, source-content, schema, relevant configuration and producer fingerprints
WHEN publication of the same canonical generation is retried
THEN the publisher SHALL reuse the existing generation identity.
VERIFY: test: mcp-server/tests/slices/test_s03_contract.py::test_generation_replay

### AC-S03-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S03-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
