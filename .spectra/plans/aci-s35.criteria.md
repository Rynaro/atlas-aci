### AC-S35-001 (event-driven)
GIVEN the supported result artifact passes digest and identity validation
WHEN feedback import completes
THEN the importer SHALL record a verified_artifact event linked to its retrieval record, run identity and source snapshot.
VERIFY: test: mcp-server/tests/slices/test_s35_feedback_overlay.py::test_feedback_verified_event_requires_artifact

### AC-S35-002 (unwanted-behavior)
GIVEN the envelope's success claim disagrees with the artifact or lacks its required provenance
WHEN outcome verification runs
THEN the importer SHALL reject the event as unverifiable_outcome.
VERIFY: test: mcp-server/tests/slices/test_s35_feedback_overlay.py::test_feedback_forged_success_is_rejected

### AC-S35-003 (ubiquitous)
THEN feedback processing SHALL leave the canonical code fact store unchanged.
VERIFY: test: mcp-server/tests/slices/test_s35_feedback_overlay.py::test_feedback_overlay_preserves_canonical_facts

### AC-S35-004 (optional-feature)
GIVEN the operator enables the versioned feedback-ranking overlay
THEN the ranker SHALL expose each applied adjustment's supporting event identities.
VERIFY: test: mcp-server/tests/slices/test_s35_feedback_overlay.py::test_feedback_opt_in_ranking_is_explained

### AC-S35-005 (event-driven)
GIVEN an event's referenced source fingerprint no longer matches the queried snapshot
WHEN the optional ranking adjustment is computed
THEN the ranking overlay SHALL exclude the stale event from active scoring.
VERIFY: test: mcp-server/tests/slices/test_s35_feedback_overlay.py::test_feedback_stale_source_is_excluded

### AC-S35-006 (event-driven)
GIVEN identical outcome bytes, retrieval/run identities, source snapshot and scoring policy
WHEN the same feedback artifact is imported twice
THEN the canonical event set and derived ranking adjustments SHALL remain identical after reimport.
VERIFY: test: mcp-server/tests/slices/test_s35_feedback_overlay.py::test_feedback_reimport_is_idempotent

### AC-S35-007 (state-driven)
GIVEN the feedback overlay is disabled
THEN the retrieval result ordering SHALL match the unchanged baseline ranker.
VERIFY: test: mcp-server/tests/slices/test_s35_feedback_overlay.py::test_feedback_disabled_preserves_baseline

### AC-S35-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S35-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
