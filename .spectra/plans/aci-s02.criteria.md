### AC-S02-001 (event-driven)
GIVEN Widget lacks close while unrelated Other defines close
WHEN the fixture is indexed through the real parser
THEN Atlas SHALL retain Widget.close as unresolved.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_unrelated_receiver

### AC-S02-002 (event-driven)
GIVEN a directly declared method belongs to the referenced receiver
WHEN call binding runs
THEN Atlas SHALL bind the call to that receiver-owned declaration.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_owned_receiver

### AC-S02-003 (event-driven)
GIVEN a lexical name shadows a same-name outer declaration
WHEN binding runs
THEN Atlas SHALL select the declaration in the applicable lexical scope.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_shadowed_name

### AC-S02-004 (unwanted-behavior)
GIVEN only another language defines the same callee name
WHEN binding runs
THEN Atlas SHALL leave the reference unresolved.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_cross_language_collision

### AC-S02-005 (unwanted-behavior)
GIVEN an import alias cannot be resolved from available facts
WHEN binding runs
THEN Atlas SHALL preserve an unresolved reason for the alias.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_unknown_alias

### AC-S02-006 (event-driven)
GIVEN unchanged source and resolver configuration
WHEN the same fixture is rebuilt twice
THEN the canonical resolved-edge set SHALL be identical across builds.
VERIFY: test: mcp-server/tests/slices/test_s02_contract.py::test_binding_replay

### AC-S02-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S02-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
