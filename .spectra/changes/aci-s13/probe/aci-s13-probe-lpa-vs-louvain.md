# ACI-S13 fresh producer measurement

Fresh same-pin/default-language CPU measurements; historical S07 and P3 evidence
is unchanged. No source transformations, profile narrowing, dependencies or bar
changes. Solidus full tree4026945 and Spree sparse spree/*6699cde; exact full pins
and clean tracked-tree checks are in job provenance.

Recorded verdict: **PASS**. Only actual unchanged verifier PASS establishes readiness.
Bar: median Louvain Q>=0.30, LPA Q>=0.30 and>=0.85×median; seeds0..9/resolution1.0.

| Repo | Nodes | Edges | LPA Q | Median | Indexed | Failed |
|---|---:|---:|---:|---:|---:|---:|
| solidus | 1883 | 1782 | 0.928255777629 | 0.960300410263 | 2459 | 60 |
| spree | 5474 | 5947 | 0.872669824411 | 0.958270309317 | 2332 | 26 |

All4877 original eligible path/hash observations match the preserved inventory.
Failed files contribute no current extracted facts; fresh inputs have no history.
Full source hashes, language counts and diagnostic locations are losslessly retained.

| Job | Seconds | Peak RSS bytes |
|---|---:|---:|
| solidus-export | 148.293 | 658669568 |
| solidus-account | 12.662 | 173981696 |
| solidus-modularity | 0.489 | 53673984 |
| spree-export | 209.256 | 1059979264 |
| spree-account | 18.961 | 255197184 |
| spree-modularity | 1.018 | 65945600 |
| solidus-lease-cost | 40.791 | 194969600 |
| spree-lease-cost | 35.900 | 272367616 |
| spree-jsonl | 92.547 | 1973764096 |

## Separately measured mandatory acquisition and bounded query

These intervals use new processes after preceding index/export jobs; OS page-cache state is uncontrolled. Setup selects one actual active assertion source outside both intervals. Process peak RSS includes setup/acquisition/query and is not retained memory per request.

| Repo | Lease acquisition seconds | Lease CPU seconds | Query + admission/SDK construction seconds | Query CPU seconds |
|---|---:|---:|---:|---:|
| solidus | 29.411466 | 11.554305 | 0.000772 | 0.000673 |
| spree | 17.867093 | 17.866675 | 0.000831 | 0.000733 |

Sample query work (not a positive-path throughput benchmark):
- solidus: `{"confirmed_edge_eof": 0, "edge_queries": 1, "edge_rows": 1, "endpoint_queries": 1, "endpoint_rows": 1, "examined_edges": 1, "hydration_queries": 0, "hydration_rows": 0, "interrupted_edge_steps": 0, "interrupted_sql_queries": 0, "sql_queries": 2, "visited_nodes": 1}`, outcome `partial`, stop `examined_edge_limit`. Hydration and row outcomes are recorded directly; no positive-path throughput claim is inferred.
- spree: `{"confirmed_edge_eof": 0, "edge_queries": 1, "edge_rows": 1, "endpoint_queries": 1, "endpoint_rows": 1, "examined_edges": 1, "hydration_queries": 0, "hydration_rows": 0, "interrupted_edge_steps": 0, "interrupted_sql_queries": 0, "sql_queries": 2, "visited_nodes": 1}`, outcome `partial`, stop `examined_edge_limit`. Hydration and row outcomes are recorded directly; no positive-path throughput claim is inferred.

The query interval ends after SDK result construction (text JSON), before the final wire dump/size assertion. Mandatory graph/source integrity validation is outside traversal counters/deadline. The bounded query uses at most two visited nodes and one examined row; actual SQL traces, counters, complete SDK byte counts and SQLite/pack closure are recorded. No hard total request latency or new performance threshold is claimed.

Current Spree JSONL: **651,845,336bytes**, SHA256`241dedc74190f5f3deb90995111294148568ed0f40d131c1391ca9335bc66a8e`.
No new size threshold or historical ratio is claimed. This remains a material
storage/memory cost; raw full export stays temporary, exact measurement retained.

Extraction cache and source snapshot store byte/entry counts: `{"solidus": {"bytes": 334683424, "entries": 2519, "source_snapshot_store": {"catalog": {"bytes": 68, "files": 1}, "manifests": {"bytes": 1202949, "files": 1}, "packs": {"bytes": 6317973, "files": 1}}}, "spree": {"bytes": 531906606, "entries": 2358, "source_snapshot_store": {"catalog": {"bytes": 68, "files": 1}, "manifests": {"bytes": 1137584, "files": 1}, "packs": {"bytes": 8566123, "files": 1}}}}`.
No eviction policy or speed threshold is claimed.

Behavioral fingerprint:`1ebe6acc94891417dc7388830499f2c046a06fa6a034de4d520abf6adb880f4a`.
Graph bundle SHA256:`c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.

All source/command/raw partition/hash/resource records are under
.spectra/evidence/aci-s13/probe/. Serial600s/job/2700s total caps were unchanged.

Prior failed-attempt charge: 0.000000s; it remains inside the unchanged2700s budget.
Cumulative serial job charge including size and isolated lease/query jobs: 562.234928s.

Both corpora contain zero .rs files; the measured generation profile contains10 languages including Rust. Rust quality is established by qualified native workspace and offline Docker tests.
Prior and cumulative probe charges are sums of serial job elapsed_seconds, not total wall runtime; original elapsed_total fields retain orchestration overhead.

## Resource-owning retained lease sample

These separate issuance samples include mandatory acquisition and private chunk-catalog derivation, one actual resolved incoming hop, immutable page construction and registry transfer. Fixture selection is outside the interval; process peak RSS includes both ordinary and retained samples and is not canonical retained-state size. Resource-handler time includes admission and SDK content construction, not a complete MCP transport round trip. No new performance threshold is imposed.

- solidus: `{"captured_work": {"confirmed_edge_eof": 0, "edge_queries": 1, "edge_rows": 1, "endpoint_queries": 1, "endpoint_rows": 1, "examined_edges": 1, "hydration_queries": 4, "hydration_rows": 4, "interrupted_edge_steps": 0, "interrupted_sql_queries": 0, "sql_queries": 6, "visited_nodes": 2}, "chunk_catalog_canonical_bytes": 337471, "generation_id": "1454e92f693e6235b5c55ee79c5674b476ada69fa24b74af6b9f09b5f0d50182", "issuance_cpu_seconds": 11.051160999999999, "issuance_including_mandatory_acquisition_seconds": 11.051518207998015, "pack_fd_closed": true, "page_count": 1, "raw_source_bytes_returned": 20, "resource_count": 1, "resource_handler_seconds": 0.0012261249939911067, "resource_result_bytes": 1413, "snapshot_id": "3dbb4ebec109d6dae1a8c8afd7dc6ffd2d9ff924c076acd09d5059ab9db7c5ff", "sqlite_closed": true, "usage": {"active": 1, "pack_fds": 1, "pending": 0, "referenced_pack_bytes": 6317973, "sqlite_handles": 1, "state_bytes": 2222916}}`
- spree: `{"captured_work": {"confirmed_edge_eof": 0, "edge_queries": 1, "edge_rows": 1, "endpoint_queries": 1, "endpoint_rows": 1, "examined_edges": 1, "hydration_queries": 4, "hydration_rows": 4, "interrupted_edge_steps": 0, "interrupted_sql_queries": 0, "sql_queries": 6, "visited_nodes": 2}, "chunk_catalog_canonical_bytes": 320952, "generation_id": "ba0635586ff51e3e03017535cf62d8ed421b9a3d610c2ba499442b79634112eb", "issuance_cpu_seconds": 17.776238000000003, "issuance_including_mandatory_acquisition_seconds": 17.77673116599908, "pack_fd_closed": true, "page_count": 1, "raw_source_bytes_returned": 11, "resource_count": 1, "resource_handler_seconds": 0.0003260830126237124, "resource_result_bytes": 1403, "snapshot_id": "707075eecf0697ddf07191b47df9e49359fae2b484ee8824c2587246c535219f", "sqlite_closed": true, "usage": {"active": 1, "pack_fds": 1, "pending": 0, "referenced_pack_bytes": 8566123, "sqlite_handles": 1, "state_bytes": 2102700}}`
