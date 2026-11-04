### AC-S24-001 (event-driven)
GIVEN a supported literal route in an indexed Ruby file
WHEN the Rails adapter indexes that source snapshot
THEN the adapter SHALL emit one route fact containing the literal HTTP verb, path pattern, target text and declaration evidence.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_literal_route_fact

### AC-S24-002 (event-driven)
GIVEN one explicit target resolves to one controller class with the named action
WHEN route binding completes
THEN the adapter SHALL emit a convention-derived routes_to relation to the uniquely matching controller-owned action.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_exact_controller_action_mapping

### AC-S24-003 (unwanted-behavior)
GIVEN two controller/action candidates match the same declared target
WHEN route binding encounters the collision
THEN the adapter SHALL report the route target as unresolved with the candidate identities.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_ambiguous_target_is_unresolved

### AC-S24-004 (unwanted-behavior)
GIVEN a fixture uses interpolation, resources, an engine mount or a runtime-dependent route target
WHEN the adapter encounters that declaration
THEN the adapter SHALL emit an unsupported_route_construct diagnostic for the unexpanded declaration.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_dynamic_route_diagnostic

### AC-S24-005 (event-driven)
GIVEN an indexed route declaration is deleted from the next snapshot
WHEN S10 invalidation republishes that file's adapter facts
THEN the published Rails fact set SHALL exclude the removed declaration's route mapping.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_removed_route_retracts_mapping

### AC-S24-006 (event-driven)
GIVEN identical source snapshot, adapter version and configuration
WHEN the Rails adapter indexes the same inputs twice
THEN the canonical Rails fact set SHALL remain identical after the repeat index.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_route_reindex_is_idempotent

### AC-S24-007 (ubiquitous)
THEN the Rails adapter SHALL derive route facts without executing repository Ruby code.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_routes_never_execute_application

### AC-S24-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S24-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
