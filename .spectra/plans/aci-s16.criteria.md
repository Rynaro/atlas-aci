### AC-S16-001 (event-driven)
GIVEN a pinned official scip-typescript protobuf artifact is provided
WHEN the importer runs
THEN the imported symbol/occurrence set SHALL match the labeled artifact fixture.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_official_artifact

### AC-S16-002 (event-driven)
GIVEN semantic evidence is imported
WHEN its provenance is inspected
THEN the evidence SHALL identify the SCIP producer version.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_producer_identity

### AC-S16-003 (event-driven)
GIVEN an occurrence has only reference semantics
WHEN it is imported
THEN Atlas SHALL preserve it as a reference relation.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_reference_not_call

### AC-S16-004 (unwanted-behavior)
GIVEN the index source identity conflicts with the target snapshot
WHEN import is attempted
THEN Atlas SHALL reject publication of mismatched evidence.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_source_mismatch

### AC-S16-005 (unwanted-behavior)
GIVEN a malformed protobuf artifact is supplied
WHEN import runs
THEN the current published graph SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_malformed_scip

### AC-S16-006 (event-driven)
GIVEN the same artifact targets the same snapshot
WHEN import is repeated
THEN the canonical imported assertion set SHALL remain unchanged.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_scip_reimport

### AC-S16-007 (unwanted-behavior)
GIVEN a supplied SCIP artifact has no sufficient producer-time source identity evidence
WHEN import is attempted
THEN the artifact SHALL remain unpublished as authoritative snapshot evidence.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_missing_source_manifest

### AC-S16-008 (event-driven)
GIVEN the official producer fixture includes non-ASCII text and a declared position encoding
WHEN occurrence ranges are imported
THEN each imported source span SHALL match the labeled Unicode fixture range.
VERIFY: test: mcp-server/tests/slices/test_s16_contract.py::test_unicode_position_encoding

### AC-S16-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S16-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
