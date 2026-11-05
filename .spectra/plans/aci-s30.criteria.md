### AC-S30-001 (event-driven)
GIVEN the locked coordinate and explicit provider mapping match validated snapshots
WHEN a supported external reference is linked
THEN the link result SHALL carry the exact ordered consumer/provider revision vector.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_link_has_exact_vector

### AC-S30-002 (unwanted-behavior)
GIVEN a same-named provider symbol belongs to a different package version or source digest
WHEN link resolution considers that provider
THEN the linker SHALL leave the reference unresolved_version_mismatch.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_wrong_version_is_unresolved

### AC-S30-003 (unwanted-behavior)
GIVEN the mapping's exact provider snapshot is unavailable
WHEN impact traversal reaches the external reference
THEN the traversal SHALL return a missing_provider_snapshot boundary record.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_missing_snapshot_is_boundary

### AC-S30-004 (event-driven)
GIVEN either consumer or provider snapshot identity changes
WHEN the operator refreshes the cross-repository overlay
THEN the linker SHALL invalidate links computed for the superseded revision vector.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_vector_change_invalidates_links

### AC-S30-005 (event-driven)
GIVEN identical authorized snapshots, mappings and producer version
WHEN the overlay is rebuilt twice in different input load orders
THEN the canonical cross-repository link set SHALL remain identical after repeat construction.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_link_replay_is_idempotent

### AC-S30-006 (ubiquitous)
THEN cross-repository traversal SHALL remain within the repositories explicitly listed in the mapping manifest.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_allowlist_bounds_traversal

### AC-S30-007 (ubiquitous)
THEN cross-repository linking SHALL leave each source repository's canonical fact store unchanged.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_overlay_preserves_source_graphs

### AC-S30-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S30-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
