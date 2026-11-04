### AC-S26-001 (event-driven)
GIVEN a direct perform_async or perform_later receiver names one indexed worker/job class
WHEN the job adapter binds the enqueue
THEN the adapter SHALL emit an async_enqueue relation to the unique matching perform method.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_direct_enqueue_target

### AC-S26-002 (event-driven)
GIVEN the enqueue and perform signature have compatible fixed positional arity
WHEN the job adapter analyzes their argument boundary
THEN the adapter SHALL emit argument-to-parameter mappings matching the fixed positional ordinals.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_positional_argument_mapping

### AC-S26-003 (ubiquitous)
THEN every adapter-produced enqueue relation SHALL carry the distinct relation kind async_enqueue.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_enqueue_relation_is_not_synchronous

### AC-S26-004 (unwanted-behavior)
GIVEN the enqueue uses splats, transformed keyword arguments or a dynamic receiver
WHEN argument-boundary analysis encounters the unsupported form
THEN the adapter SHALL report unsupported_argument_mapping for that enqueue.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_transformed_arguments_are_unresolved

### AC-S26-005 (unwanted-behavior)
GIVEN more than one worker/job definition matches the declared receiver
WHEN enqueue binding attempts to select a target
THEN the adapter SHALL retain the candidate set as an unresolved job target.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_job_collision_is_unresolved

### AC-S26-006 (event-driven)
GIVEN identical snapshot and job-adapter version
WHEN the adapter indexes the same inputs twice
THEN the canonical enqueue and argument-flow fact set SHALL remain identical after repeat indexing.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_job_reindex_is_idempotent

### AC-S26-007 (ubiquitous)
THEN the job adapter SHALL analyze enqueue syntax without dispatching a job.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_jobs_never_enqueue

### AC-S26-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S26-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
