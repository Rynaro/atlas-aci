### AC-S20-001 (event-driven)
GIVEN a changed function has a resolved caller
WHEN impact is requested
THEN that caller SHALL appear as potentially affected.
VERIFY: test: mcp-server/tests/slices/test_s20_contract.py::test_reverse_caller_impact

### AC-S20-002 (event-driven)
GIVEN an entity appears in the impact result
WHEN its explanation is inspected
THEN the result SHALL include an evidence path to a changed entity.
VERIFY: test: mcp-server/tests/slices/test_s20_contract.py::test_impact_reason_path

### AC-S20-003 (event-driven)
GIVEN impact is derived only from static reachability
WHEN the result is serialized
THEN its status SHALL be potential impact.
VERIFY: test: mcp-server/tests/slices/test_s20_contract.py::test_no_breakage_claim

### AC-S20-004 (unwanted-behavior)
GIVEN propagation reaches an unresolved reference
WHEN impact expansion continues
THEN Atlas SHALL expose that boundary as incomplete coverage.
VERIFY: test: mcp-server/tests/slices/test_s20_contract.py::test_unresolved_boundary

### AC-S20-005 (event-driven)
GIVEN the reverse closure exceeds the work limit
WHEN impact runs
THEN the work counters SHALL remain within their configured limits.
VERIFY: test: mcp-server/tests/slices/test_s20_contract.py::test_impact_budget

### AC-S20-006 (event-driven)
GIVEN the diff and propagation configuration are unchanged and execution reaches the same deterministic frontier
WHEN impact is repeated
THEN the canonical ranked result SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s20_contract.py::test_impact_replay

### AC-S20-007 (event-driven)
GIVEN a function with a known before-snapshot caller is removed in the after snapshot
WHEN impact is computed
THEN the result SHALL explain that caller through the before-snapshot evidence path.
VERIFY: test: mcp-server/tests/slices/test_s20_contract.py::test_removed_symbol_impact

### AC-S20-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S20-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
