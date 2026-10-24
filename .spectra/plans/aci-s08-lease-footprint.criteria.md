### AC-S08-FP-001 (event-driven)
GIVEN identical active and historical typed graph records
WHEN the streaming proof is computed
THEN its canonical commitments SHALL match an independent materialized reference.
VERIFY: test: mcp-server/tests/slices/test_s08_lease_footprint.py::test_streaming_commitment_equivalence

### AC-S08-FP-002 (unwanted-behavior)
GIVEN a compact manifest with a coherently rehashed foreign or invalid graph source binding
WHEN a graph reader acquires it
THEN acquisition SHALL reject the contradiction before exposing the reader.
VERIFY: test: mcp-server/tests/slices/test_s08_lease_footprint.py::test_compact_manifest_rejects_graph_contradictions

### AC-S08-FP-003 (state-driven)
GIVEN an active source lease uses a supported manifest version
THEN the original source integrity and descriptor lifetime guarantees SHALL remain enforced.
VERIFY: test: mcp-server/tests/slices/test_s08_lease_footprint.py::test_manifest_version_integrity_and_lifetime

### AC-S08-FP-004 (event-driven)
GIVEN one completely validated immutable reader lease
WHEN another request acquires a reader
THEN the new request SHALL independently validate its own graph and source state.
VERIFY: test: mcp-server/tests/slices/test_s08_lease_footprint.py::test_validation_scope_is_one_lease

### AC-S08-FP-005 (event-driven)
GIVEN the identical pinned source inventories before and after refinement
WHEN reader costs are reported
THEN the paired report SHALL distinguish isolated lease measurements from whole-accounting measurements.
VERIFY: gate: record actual commands, source hashes, elapsed time, peak RSS and compact-manifest counts for isolated reader acquisition and ordinary bounded MCP queries on both pinned corpora, keeping aggregate accounting figures separately labeled.

### AC-S08-FP-006 (ubiquitous)
THEN the refinement SHALL preserve the original ACI-S08 acceptance and execution identity.
VERIFY: gate: original eight criteria and all applicable full/static/native/probe/reentry checks pass under the original five-day allocation and cumulative 600-second job/2700-second probe caps; compare original plan/state/envelope/criteria hashes and the one execution receipt identity.
