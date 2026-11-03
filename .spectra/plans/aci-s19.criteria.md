### AC-S19-001 (event-driven)
GIVEN a function parameter changes between two snapshots
WHEN the diff runs
THEN the function SHALL be classified as a signature change.
VERIFY: test: mcp-server/tests/slices/test_s19_contract.py::test_signature_change

### AC-S19-002 (event-driven)
GIVEN only the normalized function body changes
WHEN the diff runs
THEN the function SHALL be classified as a body change.
VERIFY: test: mcp-server/tests/slices/test_s19_contract.py::test_body_change

### AC-S19-003 (event-driven)
GIVEN only source line positions change
WHEN the diff runs
THEN the declaration SHALL be classified as a location-only change.
VERIFY: test: mcp-server/tests/slices/test_s19_contract.py::test_location_only

### AC-S19-004 (event-driven)
GIVEN a change exists on both sides
WHEN its diff record is read
THEN the record SHALL reference both source snapshots.
VERIFY: test: mcp-server/tests/slices/test_s19_contract.py::test_diff_evidence

### AC-S19-005 (unwanted-behavior)
GIVEN the snapshots have unrelated repository identities
WHEN a local snapshot diff is requested
THEN Atlas SHALL reject the comparison.
VERIFY: test: mcp-server/tests/slices/test_s19_contract.py::test_incompatible_repositories

### AC-S19-006 (event-driven)
GIVEN the same snapshot pair is compared twice
WHEN both comparisons complete
THEN the canonical change set SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s19_contract.py::test_diff_replay

### AC-S19-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S19-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
