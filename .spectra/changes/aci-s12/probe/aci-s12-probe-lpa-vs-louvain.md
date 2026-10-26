# ACI-S12 fresh producer measurement

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
| solidus-export | 149.167 | 700661760 |
| solidus-account | 12.853 | 174129152 |
| solidus-modularity | 0.490 | 53723136 |
| spree-export | 210.297 | 1062109184 |
| spree-account | 19.199 | 255066112 |
| spree-modularity | 1.015 | 65699840 |
| solidus-lease-cost | 30.623 | 189317120 |
| spree-lease-cost | 18.121 | 266600448 |
| spree-jsonl | 91.626 | 1959526400 |

## Separately measured mandatory acquisition and bounded query

These intervals use new processes after preceding index/export jobs; OS page-cache state is uncontrolled. Setup selects one actual active assertion source outside both intervals. Process peak RSS includes setup/acquisition/query and is not retained memory per request.

| Repo | Lease acquisition seconds | Lease CPU seconds | Query + admission + SDK result construction seconds | Query CPU seconds |
|---|---:|---:|---:|---:|
| solidus | 30.315461 | 11.529582 | 0.000762 | 0.000664 |
| spree | 17.867536 | 17.867110 | 0.000833 | 0.000736 |

Sample query work (not a positive-path throughput benchmark):
- solidus: `{"confirmed_edge_eof": 0, "edge_queries": 1, "edge_rows": 1, "endpoint_queries": 1, "endpoint_rows": 1, "examined_edges": 1, "hydration_queries": 0, "hydration_rows": 0, "interrupted_edge_steps": 0, "interrupted_sql_queries": 0, "sql_queries": 2, "visited_nodes": 1}`, outcome `partial`, stop `examined_edge_limit`. The selected first raw adjacency row was unresolved, so no hop proof was hydrated.
- spree: `{"confirmed_edge_eof": 0, "edge_queries": 1, "edge_rows": 1, "endpoint_queries": 1, "endpoint_rows": 1, "examined_edges": 1, "hydration_queries": 0, "hydration_rows": 0, "interrupted_edge_steps": 0, "interrupted_sql_queries": 0, "sql_queries": 2, "visited_nodes": 1}`, outcome `partial`, stop `examined_edge_limit`. The selected first raw adjacency row was unresolved, so no hop proof was hydrated.

Mandatory graph/source integrity validation is outside traversal counters/deadline. The bounded query uses at most two visited nodes and one examined row; actual SQL traces, counters, complete SDK byte counts and SQLite/pack closure are recorded. No hard total request latency or new performance threshold is claimed.

Current Spree JSONL: **651,845,336bytes**, SHA256`3ed093aca904d72d04ca0875eb865f23058baf4486dcb7d05edce00798d25346`.
No new size threshold or historical ratio is claimed. This remains a material
storage/memory cost; raw full export stays temporary, exact measurement retained.

Extraction cache and source snapshot store byte/entry counts: `{"solidus": {"bytes": 334683424, "entries": 2519, "source_snapshot_store": {"catalog": {"bytes": 68, "files": 1}, "manifests": {"bytes": 1202949, "files": 1}, "packs": {"bytes": 6317973, "files": 1}}}, "spree": {"bytes": 531906606, "entries": 2358, "source_snapshot_store": {"catalog": {"bytes": 68, "files": 1}, "manifests": {"bytes": 1137584, "files": 1}, "packs": {"bytes": 8566123, "files": 1}}}}`.
No eviction policy or speed threshold is claimed.

Behavioral fingerprint:`1ebe6acc94891417dc7388830499f2c046a06fa6a034de4d520abf6adb880f4a`.
Graph bundle SHA256:`c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.

All source/command/raw partition/hash/resource records are under
.spectra/evidence/aci-s12/probe/. Serial600s/job/2700s total caps were unchanged.

Prior failed-attempt charge: 0.000000s; it remains inside the unchanged2700s budget.
Cumulative serial job charge including size and isolated lease/query jobs: 535.762548s.

Both corpora contain zero .rs files; the measured generation profile contains10 languages including Rust. Rust quality is established by qualified native workspace and offline Docker tests.
Prior and cumulative probe charges are sums of serial job elapsed_seconds, not total wall runtime; original elapsed_total fields retain orchestration overhead.

Preservation of four source catalog/manifest streams took 0.082730625s; independent-verifier invocation took 0.214870125s. Primary serial jobs plus these checks total 536.060148711s; the separately recorded conservative wall interval including orchestration remained below2700s.

Timing scope clarification: the query interval includes traversal, admission's SDK-framing measurements, and final `sdk_result` construction/text JSON encoding. The final `result_bytes()` wire dump and byte-length assertion occur after that timer. It is not a full MCP transport/serialization latency measurement. Raw numeric fields and measurements remain unchanged.
