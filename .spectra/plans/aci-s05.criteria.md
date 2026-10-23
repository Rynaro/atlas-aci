### AC-S05-001 (event-driven)
GIVEN the default graph profile is active
WHEN test_dry_run is called
THEN Atlas SHALL reject the call before creating a repository subprocess.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_default_no_spawn

### AC-S05-002 (optional-feature)
GIVEN the operator enables the execution profile
THEN test_dry_run SHALL use the existing bounded execution path.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_explicit_enabled_mode

### AC-S05-003 (unwanted-behavior)
GIVEN an enabled test subprocess exceeds its deadline
WHEN the timeout guard fires
THEN Atlas SHALL terminate the subprocess according to the documented process policy.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_timeout_preserved

### AC-S05-004 (event-driven)
GIVEN tool capabilities are requested
WHEN the manifest is produced
THEN the test execution capability SHALL identify its enabled state.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_profile_visible

### AC-S05-005 (event-driven)
GIVEN execution is disabled
WHEN the same forbidden test request is submitted twice
THEN the repository sentinel SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s05_contract.py::test_denial_replay

### AC-S05-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S05-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
