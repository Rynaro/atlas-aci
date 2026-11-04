### AC-S37-001 (event-driven)
GIVEN a valid bounded evidence envelope is supplied
WHEN the report exporter renders it
THEN the canonical JSON report SHALL preserve the input envelope's snapshot and evidence identities.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_json_preserves_response_identity

### AC-S37-002 (event-driven)
GIVEN the envelope contains provenance-bearing evidence paths
WHEN the static report is rendered
THEN the HTML report SHALL expose each included evidence path's source locations and relation labels.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_html_has_source_paths

### AC-S37-003 (event-driven)
GIVEN the supplied evidence envelope is incomplete or budget-truncated
WHEN the exporter renders that envelope
THEN both report formats SHALL display the input envelope's unresolved and truncation markers.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_report_preserves_incompleteness

### AC-S37-004 (unwanted-behavior)
GIVEN a fixture includes script tags, event attributes or closing-tag injection in a label or excerpt
WHEN the report is rendered
THEN the HTML exporter SHALL render source-derived markup as inert escaped text.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_html_escapes_source_content

### AC-S37-005 (ubiquitous)
THEN the generated HTML SHALL require no external assets or executable scripts.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_report_is_self_contained

### AC-S37-006 (event-driven)
GIVEN identical input envelope, rendering version and explicit options
WHEN the exporter writes the same scoped report twice
THEN the HTML and JSON artifact bytes SHALL remain identical after repeat export.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_report_replay_is_idempotent

### AC-S37-007 (unwanted-behavior)
GIVEN an evidence item identifies a snapshot outside the envelope's declared snapshot or revision vector
WHEN report-input validation runs
THEN the exporter SHALL reject the envelope with a mixed_snapshot_evidence error.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_mixed_snapshot_rejected

### AC-S37-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S37-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
