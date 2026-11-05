### AC-S36-001 (event-driven)
GIVEN the operator supplies a complete preregistered experiment manifest
WHEN the community comparison runs
THEN the runner SHALL identify every measured result by the frozen experiment-manifest digest.
VERIFY: test: mcp-server/tests/slices/test_s36_community_experiment.py::test_community_experiment_uses_frozen_manifest

### AC-S36-002 (event-driven)
GIVEN LPA and the optional Leiden provider are available
WHEN the runner prepares their input
THEN both partitioners SHALL receive the same canonical projected-graph digest.
VERIFY: test: mcp-server/tests/slices/test_s36_community_experiment.py::test_community_algorithms_share_input_projection

### AC-S36-003 (event-driven)
GIVEN a completed experiment contains its raw partitions and task outputs
WHEN the result verifier checks the report
THEN the verifier SHALL reproduce the reported non-timing metrics from the retained partitions and frozen task evidence.
VERIFY: test: mcp-server/tests/slices/test_s36_community_experiment.py::test_community_metrics_are_rederived

### AC-S36-004 (event-driven)
GIVEN identical inputs, provider versions, fixed seed set and experiment configuration
WHEN the experiment is rerun
THEN the canonical partition and non-timing metric artifacts SHALL remain identical after replay.
VERIFY: test: mcp-server/tests/slices/test_s36_community_experiment.py::test_community_replay_preserves_deterministic_results

### AC-S36-005 (unwanted-behavior)
GIVEN the optional Leiden provider is not installed
WHEN the operator requests the comparison
THEN the runner SHALL report skipped_missing_provider.
VERIFY: test: mcp-server/tests/slices/test_s36_community_experiment.py::test_community_missing_provider_is_skipped

### AC-S36-006 (unwanted-behavior)
GIVEN the artifact omits required nodes, duplicates membership or invents node identities
WHEN partition validation runs
THEN the verifier SHALL reject the partition artifact.
VERIFY: test: mcp-server/tests/slices/test_s36_community_experiment.py::test_community_invalid_partition_rejected

### AC-S36-007 (ubiquitous)
THEN running the experiment SHALL leave the production LPA selection and core dependency lock unchanged.
VERIFY: test: mcp-server/tests/slices/test_s36_community_experiment.py::test_community_experiment_preserves_default

### AC-S36-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S36-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
