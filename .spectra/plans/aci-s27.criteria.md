### AC-S27-001 (event-driven)
GIVEN a supported OTLP artifact has valid provenance for an indexed snapshot
WHEN the operator imports the artifact
THEN the importer SHALL emit a runtime observation carrying the run, trace, span, artifact and matched snapshot identities.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_observation_has_snapshot_provenance

### AC-S27-002 (event-driven)
GIVEN a child span identifies a parent span present in the same trace
WHEN the importer materializes their relationship
THEN the runtime overlay SHALL represent the relationship as observed_parent_of.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_parent_relation_preserves_observed_semantics

### AC-S27-003 (unwanted-behavior)
GIVEN the artifact's declared build cannot be matched to the supplied source snapshot
WHEN source linkage is attempted
THEN the importer SHALL classify the observation as unlinked_build_mismatch.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_build_mismatch_is_unlinked

### AC-S27-004 (unwanted-behavior)
GIVEN a valid span lacks the supported source-location attributes
WHEN the importer processes that span
THEN the import summary SHALL count the observation under missing_source_location.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_missing_source_is_counted

### AC-S27-005 (event-driven)
GIVEN the artifact digest, run identity, importer version and snapshot mapping are unchanged
WHEN the same trace artifact is imported twice
THEN the runtime observation set SHALL remain identical after reimport.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_reimport_is_idempotent

### AC-S27-006 (ubiquitous)
THEN the persisted runtime overlay SHALL exclude attributes outside the declared import allowlist.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_attributes_are_allowlisted

### AC-S27-007 (ubiquitous)
THEN every runtime-path result SHALL declare its evidence coverage as observed_sample.
VERIFY: test: mcp-server/tests/slices/test_s27_otel_import.py::test_otel_sampling_limits_are_explicit

### AC-S27-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S27-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
