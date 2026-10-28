### AC-S18-001 (event-driven)
GIVEN a relationship bundle is selected
WHEN context is serialized
THEN the bundle SHALL include its supporting evidence reference.
VERIFY: test: mcp-server/tests/slices/test_s18_contract.py::test_complete_bundle

### AC-S18-002 (event-driven)
GIVEN the requested context exceeds max_bytes and max_bytes is at least the documented minimum envelope size
WHEN a supported MCP response is produced
THEN its complete serialized payload SHALL not exceed max_bytes.
VERIFY: test: mcp-server/tests/slices/test_s18_contract.py::test_full_envelope_budget

### AC-S18-003 (optional-feature)
GIVEN an exact tokenizer is configured
THEN reported token usage SHALL equal that tokenizer applied to the emitted payload.
VERIFY: test: mcp-server/tests/slices/test_s18_contract.py::test_declared_tokenizer

### AC-S18-004 (state-driven)
GIVEN no exact tokenizer is configured
THEN token estimates SHALL be labeled estimated.
VERIFY: test: mcp-server/tests/slices/test_s18_contract.py::test_estimate_labeled

### AC-S18-005 (unwanted-behavior)
GIVEN the requested limit cannot fit the minimum valid envelope
WHEN context is requested
THEN Atlas SHALL return an invalid-budget error within the documented server error cap.
VERIFY: test: mcp-server/tests/slices/test_s18_contract.py::test_budget_too_small

### AC-S18-006 (event-driven)
GIVEN request, snapshot and ranker configuration are unchanged and execution reaches the same deterministic frontier
WHEN context is requested again
THEN the canonical evidence package SHALL be identical.
VERIFY: test: mcp-server/tests/slices/test_s18_contract.py::test_context_replay

### AC-S18-007 (event-driven)
GIVEN the canonical context request and selected evidence are unchanged
WHEN a context response is produced
THEN the response SHALL carry the same retrieval-record identity.
VERIFY: test: mcp-server/tests/slices/test_s18_contract.py::test_retrieval_record_identity

### AC-S18-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S18-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
