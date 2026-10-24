### AC-S15-001 (event-driven)
GIVEN a Rust fixture contains the declared structural constructs
WHEN the real indexer runs
THEN the canonical declaration set SHALL equal the labeled fixture set.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_rust_declarations

### AC-S15-002 (event-driven)
GIVEN two impl blocks define same-name methods
WHEN facts are extracted
THEN each method occurrence SHALL retain its enclosing impl ownership.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_impl_ownership

### AC-S15-003 (event-driven)
GIVEN a Rust function contains a call expression
WHEN extraction runs
THEN Atlas SHALL record its source call occurrence.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_rust_call_occurrence

### AC-S15-004 (unwanted-behavior)
GIVEN a declaration exists only after unsupported macro expansion
WHEN capabilities are requested
THEN Atlas SHALL report the macro expansion gap.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_macro_gap

### AC-S15-005 (event-driven)
GIVEN a trait-dispatch target is not established
WHEN binding runs
THEN the call SHALL remain unresolved.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_no_trait_guess

### AC-S15-006 (event-driven)
GIVEN the same Cargo workspace is indexed twice
WHEN the second index completes
THEN the canonical Rust fact set SHALL equal the first successful result.
VERIFY: test: mcp-server/tests/slices/test_s15_contract.py::test_rust_replay

### AC-S15-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S15-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
