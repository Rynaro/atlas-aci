### AC-S29-001 (event-driven)
GIVEN a supported OpenAPI document contains operations with absent or repeated operationId metadata
WHEN the contract is imported
THEN the importer SHALL identify each supported operation by its normalized HTTP method and path template.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_operation_identity

### AC-S29-002 (ubiquitous)
THEN every imported contract element SHALL retain its artifact digest, snapshot identity and JSON pointer.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_element_provenance

### AC-S29-003 (event-driven)
GIVEN an operation exists in the before contract but not in the after contract
WHEN the operator compares the two contract snapshots
THEN the differ SHALL emit an operation_removed finding for that method/path identity.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_removed_operation_finding

### AC-S29-004 (event-driven)
GIVEN the after contract adds a required request parameter absent or optional in the before contract
WHEN the supported parameter diff runs
THEN the differ SHALL emit a newly_required_parameter review finding for that parameter.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_new_required_parameter_finding

### AC-S29-005 (unwanted-behavior)
GIVEN a reference is remote, cyclic beyond the configured bound or outside the document
WHEN reference resolution encounters it
THEN the importer SHALL mark the affected contract region incomplete with an unresolved_reference diagnostic.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_unresolved_reference_is_visible

### AC-S29-006 (event-driven)
GIVEN identical before/after artifact bytes, importer profile and diff options
WHEN both artifacts are imported again and compared again
THEN the canonical contract diff SHALL remain byte-identical after repeat import and comparison.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_reimport_diff_is_idempotent

### AC-S29-007 (unwanted-behavior)
GIVEN a changed contract field falls outside the supported removal/required-parameter checks
WHEN the operator requests its compatibility assessment
THEN the differ SHALL classify the changed region as review_required.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_unsupported_change_requires_review

### AC-S29-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S29-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
