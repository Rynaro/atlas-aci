# ACI-S15 fresh producer measurement

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
| solidus-export | 134.583 | 1046511616 |
| solidus-account | 0.143 | 44400640 |
| solidus-modularity | 0.431 | 53723136 |
| spree-export | 188.045 | 1661911040 |
| spree-account | 0.142 | 44941312 |
| spree-modularity | 1.018 | 65634304 |
| spree-jsonl | 66.847 | 1972158464 |

Current Spree JSONL: **651,845,246bytes**, SHA256`fc07224ccad932343103b86cc58e5617c568e473fd1c57bf2d7bd6f49411a539`.
No new size threshold or historical ratio is claimed. This remains a material
storage/memory cost; raw full export stays temporary, exact measurement retained.

Extraction cache bytes/entry counts: `{"solidus": {"bytes": 334235404, "entries": 2519}, "spree": {"bytes": 531484506, "entries": 2358}}`.
No eviction policy or speed threshold is claimed.

Behavioral fingerprint:`0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f`.
Graph bundle SHA256:`c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.

All source/command/raw partition/hash/resource records are under
.spectra/evidence/aci-s15/probe-attempt2/. Serial600s/job/2700s total caps were unchanged.

Prior failed-attempt charge: 256.160077s; it remains inside the unchanged2700s budget.
Cumulative serial job charge including size: 649.735072s.

Both corpora contain zero `.rs` files; each measured generation profile contains
10 languages including Rust. These measurements check existing corpus behavior
under the new producer/profile; Rust extraction quality is established by the
qualified native Cargo-layout workspace tests and real offline Docker queries.

Prior256.160076833s and cumulative649.735071999s are the **sum of serial job
`elapsed_seconds`**, not an assertion of total wall runtime. The first runner
records256.170715709s wall `elapsed_total`, including10.639ms orchestration
overhead. All original raw timing records remain unchanged.

Available recorded invocation wall totals: first runner256.170715709s, retry
runner326.741051792s, separate size subprocess66.847462792s; their sum is
649.759230293s (<2700s), including runner orchestration overhead. Capacity
restoration/review pauses between invocations are separate from these job charges.
