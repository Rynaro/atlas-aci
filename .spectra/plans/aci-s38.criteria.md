### AC-S38-001 (event-driven)
GIVEN a declared legacy host fixture connects
WHEN its lifecycle runs
THEN the fixture SHALL complete the documented legacy conversation.
VERIFY: test: mcp-server/tests/slices/test_s38_contract.py::test_legacy_host_fixture

### AC-S38-002 (event-driven)
GIVEN a declared modern host fixture connects
WHEN its lifecycle runs
THEN the fixture SHALL complete the selected revision conversation.
VERIFY: test: mcp-server/tests/slices/test_s38_contract.py::test_modern_host_fixture

### AC-S38-003 (event-driven)
GIVEN a structured operation returns
WHEN its output is checked
THEN the payload SHALL validate against the selected contract schema.
VERIFY: test: mcp-server/tests/slices/test_s38_contract.py::test_schema_conformance

### AC-S38-004 (unwanted-behavior)
GIVEN a client requests an unsupported protocol revision
WHEN connection handling runs
THEN Atlas SHALL return the revision-appropriate incompatibility response.
VERIFY: test: mcp-server/tests/slices/test_s38_contract.py::test_unsupported_version

### AC-S38-005 (event-driven)
GIVEN legacy and modern adapters execute the same canonical query
WHEN both complete
THEN their canonical graph evidence SHALL be equivalent.
VERIFY: test: mcp-server/tests/slices/test_s38_contract.py::test_engine_equivalence

### AC-S38-006 (event-driven)
GIVEN a supported protocol transcript is replayed twice
WHEN both replays finish
THEN their normalized responses SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s38_contract.py::test_adapter_replay

### AC-S38-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S38-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
