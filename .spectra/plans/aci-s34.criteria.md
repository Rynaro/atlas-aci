### AC-S34-001 (event-driven)
GIVEN a supplied mapping resolves to a local ADR span and indexed source endpoint
WHEN the mapping artifact is imported
THEN the importer SHALL emit a rationale mapping with the decision span, document digest and exact target identity.
VERIFY: test: mcp-server/tests/slices/test_s34_adr_drift.py::test_adr_mapping_has_exact_evidence

### AC-S34-002 (event-driven)
GIVEN the mapped source changes between snapshots while the ADR document digest remains unchanged
WHEN the operator compares the mapped snapshots
THEN the comparison SHALL emit a rationale_review_needed finding for the mapping.
VERIFY: test: mcp-server/tests/slices/test_s34_adr_drift.py::test_adr_source_change_requests_review

### AC-S34-003 (event-driven)
GIVEN the mapped source endpoint is unchanged across the compared snapshots
WHEN the rationale comparison runs
THEN the comparison SHALL emit no source-change finding for that mapping.
VERIFY: test: mcp-server/tests/slices/test_s34_adr_drift.py::test_adr_unchanged_source_has_no_drift_signal

### AC-S34-004 (unwanted-behavior)
GIVEN a declared ADR span or exact source endpoint is missing
WHEN mapping resolution runs
THEN the importer SHALL report an unresolved_mapping record.
VERIFY: test: mcp-server/tests/slices/test_s34_adr_drift.py::test_adr_missing_target_is_unresolved

### AC-S34-005 (event-driven)
GIVEN identical mapping bytes, decision/source snapshots and producer version
WHEN the same inputs are imported and compared twice
THEN the canonical rationale mapping and finding sets SHALL remain identical after repeat import and comparison.
VERIFY: test: mcp-server/tests/slices/test_s34_adr_drift.py::test_adr_reimport_is_idempotent

### AC-S34-006 (unwanted-behavior)
GIVEN S19 cannot establish a supported correspondence for its target across snapshots
WHEN rationale drift analysis reaches that target
THEN the comparison SHALL return unknown_target_history for the affected mapping.
VERIFY: test: mcp-server/tests/slices/test_s34_adr_drift.py::test_adr_incomparable_snapshot_is_unknown

### AC-S34-007 (ubiquitous)
THEN rationale mapping and drift analysis SHALL leave the supplied ADR and source files unchanged.
VERIFY: test: mcp-server/tests/slices/test_s34_adr_drift.py::test_adr_analysis_never_edits_decisions

### AC-S34-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S34-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
