### AC-S33-001 (event-driven)
GIVEN a bounded local history fixture has known eligible pair co-occurrences
WHEN the operator computes co-change signals
THEN the overlay SHALL report the fixture's exact eligible-commit support count for each file pair.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_support_matches_commits

### AC-S33-002 (ubiquitous)
THEN every co-change record SHALL carry its repository identity, start/end commits, filter digest and supporting commit IDs.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_window_provenance

### AC-S33-003 (event-driven)
GIVEN the history contains filtered commits and generated paths
WHEN co-change aggregation runs
THEN the reported support counts SHALL exclude contributions removed by the declared merge/generated/bulk filters.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_excluded_commits_do_not_contribute

### AC-S33-004 (ubiquitous)
THEN every history signal SHALL carry the evidence kind co_change_correlation.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_is_correlation_only

### AC-S33-005 (unwanted-behavior)
GIVEN a required ancestor is missing or the commit budget is exhausted
WHEN the history scan cannot cover the requested window
THEN the result SHALL declare incomplete_history.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_missing_history_is_incomplete

### AC-S33-006 (event-driven)
GIVEN identical repository objects, window, filter policy and producer version
WHEN the same co-change analysis runs twice
THEN the canonical history overlay SHALL remain identical after recomputation.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_replay_is_idempotent

### AC-S33-007 (unwanted-behavior)
GIVEN the requested end commit is not contained in the selected source snapshot's ancestry
WHEN window validation runs
THEN the analyzer SHALL reject the history window as outside_snapshot_history.
VERIFY: test: mcp-server/tests/slices/test_s33_cochange.py::test_cochange_future_window_rejected

### AC-S33-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S33-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
