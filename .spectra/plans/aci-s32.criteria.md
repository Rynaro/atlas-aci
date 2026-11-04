### AC-S32-001 (event-driven)
GIVEN a valid bounded patch applies to the declared base snapshot
WHEN counterfactual analysis completes
THEN the report SHALL identify its hypothetical snapshot by the base snapshot, patch digest and producer configuration.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_identity_is_hypothetical

### AC-S32-002 (event-driven)
GIVEN the patch changes the fixture's supported source declarations
WHEN the proposed snapshot has been indexed
THEN the report SHALL contain the S19 source diff between the declared base and the staged patched snapshot.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_source_diff_matches_proposal

### AC-S32-003 (event-driven)
GIVEN the patch introduces or removes a supported forbidden-import path
WHEN S31 rules are compared across the two snapshots
THEN the report SHALL classify the fixture's architecture findings by their before/after presence.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_architecture_delta

### AC-S32-004 (ubiquitous)
THEN counterfactual analysis SHALL leave the original working tree, git refs and active snapshot state unchanged.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_original_state_unchanged

### AC-S32-005 (unwanted-behavior)
GIVEN the patch escapes the repository scope, changes a symlink or binary, fails its base check or exceeds the declared input limits
WHEN patch validation runs
THEN the analyzer SHALL reject the patch before indexing staged source.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_unsafe_patch_rejected

### AC-S32-006 (event-driven)
GIVEN identical base snapshot, patch bytes, analyzer configuration and rule document
WHEN the proposal is analyzed twice
THEN the canonical counterfactual report SHALL remain byte-identical.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_replay_is_idempotent

### AC-S32-007 (unwanted-behavior)
GIVEN extraction, diff or rule evaluation cannot finish within its supported scope and bounds
WHEN counterfactual result assembly runs
THEN the report SHALL classify the affected analysis as incomplete.
VERIFY: test: mcp-server/tests/slices/test_s32_counterfactual.py::test_counterfactual_incomplete_analysis_is_visible

### AC-S32-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S32-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
