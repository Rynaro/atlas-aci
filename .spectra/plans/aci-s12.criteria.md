### AC-S12-001 (event-driven)
GIVEN A calls B while C calls A
WHEN outgoing calls from A are requested
THEN the neighborhood SHALL contain B as its sole call target.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_direction_filter

### AC-S12-002 (event-driven)
GIVEN a typed path exists between exact entities
WHEN a path query succeeds
THEN each path hop SHALL reference its supporting evidence.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_path_evidence

### AC-S12-003 (event-driven)
GIVEN a hub fixture exceeds max_visited_nodes
WHEN traversal runs
THEN the visited-node counter SHALL not exceed the requested limit.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_visit_budget

### AC-S12-004 (unwanted-behavior)
GIVEN the configured deadline expires during traversal
WHEN the next expansion is attempted
THEN the query SHALL return a deadline-bounded partial result.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_deadline

### AC-S12-005 (unwanted-behavior)
GIVEN a seed name matches several entities
WHEN traversal is requested without an identity
THEN Atlas SHALL return a disambiguation result.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_ambiguous_seed

### AC-S12-006 (event-driven)
GIVEN the request and snapshot are unchanged and traversal completes or stops at the same deterministic work frontier
WHEN traversal is repeated
THEN the ordered canonical result SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_traversal_replay

### AC-S12-007 (event-driven)
GIVEN a dense parallel-edge fixture exceeds max_examined_edges
WHEN traversal runs
THEN the examined-edge counter SHALL not exceed max_examined_edges.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_examined_edge_budget

### AC-S12-008 (event-driven)
GIVEN the requested byte budget meets the documented minimum envelope size
WHEN a traversal response is serialized
THEN the complete serialized payload SHALL not exceed the requested byte budget.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_serialized_byte_budget

### AC-S12-009 (event-driven)
GIVEN a graph has paths deeper than max_depth
WHEN traversal runs
THEN the expanded path depth SHALL not exceed max_depth.
VERIFY: test: mcp-server/tests/slices/test_s12_contract.py::test_depth_budget

### AC-S12-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S12-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
