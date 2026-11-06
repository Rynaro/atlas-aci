### AC-S41-001 (event-driven)
GIVEN an identical index job has a retained, digest-verified completed output
WHEN the same canonical job is submitted
THEN the existing job result SHALL be reused.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_job_dedup

### AC-S41-002 (unwanted-behavior)
GIVEN a process stops after a durable checkpoint
WHEN the job is resumed
THEN execution SHALL continue from the last valid checkpoint.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_job_resume

### AC-S41-003 (unwanted-behavior)
GIVEN cancellation occurs before publication
WHEN the job exits
THEN the previous active generation SHALL remain selected.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_cancel_no_publish

### AC-S41-004 (state-driven)
GIVEN a generation has an active reader lease
THEN garbage collection SHALL retain that generation.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_leased_generation_retained

### AC-S41-005 (event-driven)
GIVEN a retention policy retires an unleased generation
WHEN its old resource is requested
THEN Atlas SHALL return explicit reference expiry.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_expired_reference_visible

### AC-S41-006 (event-driven)
GIVEN garbage collection already completed for the same reachable set
WHEN it runs again
THEN the retained canonical artifact set SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_gc_replay

### AC-S41-007 (event-driven)
GIVEN G is the current published generation with zero active readers
WHEN garbage collection runs
THEN G SHALL remain acquirable.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_current_generation_retained

### AC-S41-008 (event-driven)
GIVEN reader acquisition races with reclamation under a deterministic barrier
WHEN both operations complete
THEN the acquiring reader SHALL receive a readable retained generation or an explicit retryable expiry.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_lease_gc_race

### AC-S41-009 (unwanted-behavior)
GIVEN a completed receipt points to an expired or digest-invalid output
WHEN the job is requested again
THEN the job manager SHALL decline completed-output reuse.
VERIFY: test: mcp-server/tests/slices/test_s41_contract.py::test_missing_job_output

### AC-S41-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S41-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
