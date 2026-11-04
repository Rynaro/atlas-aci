### AC-S31-001 (event-driven)
GIVEN the fixture contains a supported path from the rule's source set to its forbidden target set
WHEN the rule is evaluated at the pinned snapshot
THEN the checker SHALL return the deterministic shortest provenance-bearing forbidden import path.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_violation_has_counterexample

### AC-S31-002 (event-driven)
GIVEN the declared supported graph scope is complete and contains no forbidden import path
WHEN the rule evaluation finishes within its bounds
THEN the checker SHALL return satisfied_within_scope.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_complete_scope_can_satisfy

### AC-S31-003 (unwanted-behavior)
GIVEN an unsupported or unresolved dependency boundary can affect the rule's declared scope
WHEN the evaluation reaches that boundary
THEN the checker SHALL return unknown.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_incomplete_scope_is_unknown

### AC-S31-004 (unwanted-behavior)
GIVEN the configured node or depth bound prevents completion
WHEN rule traversal exhausts that bound
THEN the checker SHALL mark the rule result incomplete_due_to_budget.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_path_budget_is_visible

### AC-S31-005 (unwanted-behavior)
GIVEN the document contains an unsupported relation, malformed selector or duplicate rule ID
WHEN rule validation runs
THEN the checker SHALL reject the rule document with a structured invalid_rule error.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_invalid_rule_rejected

### AC-S31-006 (event-driven)
GIVEN identical snapshot, rule bytes, checker version and bounds
WHEN the rule set is evaluated twice
THEN the canonical rule report SHALL remain byte-identical.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_rule_replay_is_idempotent

### AC-S31-007 (ubiquitous)
THEN architecture checking SHALL leave the canonical fact store unchanged.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_check_is_read_only

### AC-S31-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S31-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
