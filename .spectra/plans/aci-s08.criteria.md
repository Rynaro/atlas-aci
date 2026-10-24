### AC-S08-001 (event-driven)
GIVEN a query is pinned before its file is edited
WHEN the query opens its source evidence
THEN the returned bytes SHALL match the pinned source hash.
VERIFY: test: mcp-server/tests/slices/test_s08_contract.py::test_snapshot_source_coherence

### AC-S08-002 (event-driven)
GIVEN two worktrees define the same symbol differently
WHEN each worktree is queried
THEN each result SHALL identify its own worktree snapshot.
VERIFY: test: mcp-server/tests/slices/test_s08_contract.py::test_worktree_isolation

### AC-S08-003 (event-driven)
GIVEN an uncommitted source edit changes content
WHEN a snapshot is published
THEN the new snapshot SHALL have a different canonical content identity.
VERIFY: test: mcp-server/tests/slices/test_s08_contract.py::test_dirty_digest

### AC-S08-004 (unwanted-behavior)
GIVEN a required source blob is missing
WHEN publication is attempted
THEN the publisher SHALL reject the incomplete snapshot.
VERIFY: test: mcp-server/tests/slices/test_s08_contract.py::test_incomplete_manifest

### AC-S08-005 (state-driven)
GIVEN a reader holds a snapshot lease
THEN the referenced source blobs SHALL remain readable.
VERIFY: test: mcp-server/tests/slices/test_s08_contract.py::test_active_lease_retained

### AC-S08-006 (event-driven)
GIVEN source, repository/worktree identity, schema, relevant configuration and producer fingerprints are unchanged
WHEN snapshot construction is repeated
THEN the canonical snapshot identity SHALL be reused.
VERIFY: test: mcp-server/tests/slices/test_s08_contract.py::test_snapshot_replay

### AC-S08-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S08-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
