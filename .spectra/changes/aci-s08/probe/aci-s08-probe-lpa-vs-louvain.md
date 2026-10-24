# ACI-S08 fresh producer measurement

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
| solidus-export | 147.430 | 2329853952 |
| solidus-account | 12.373 | 2212495360 |
| solidus-modularity | 0.546 | 53952512 |
| spree-export | 211.063 | 3062480896 |
| spree-account | 18.759 | 2793046016 |
| spree-modularity | 1.131 | 65716224 |
| spree-jsonl | 92.757 | 3026485248 |

Current Spree JSONL: **651,845,336bytes**, SHA256`3c65f32c4702d6d9398f17bde21c292c29af66ecd5bffd6d9ca68355bfce3832`.
No new size threshold or historical ratio is claimed. This remains a material
storage/memory cost; raw full export stays temporary, exact measurement retained.

Extraction cache and source snapshot store bytes/entry counts: `{"solidus": {"bytes": 334683424, "entries": 2519, "source_snapshot_store": {"manifests": {"bytes": 89981511, "files": 1}, "packs": {"bytes": 6317973, "files": 1}}}, "spree": {"bytes": 531906606, "entries": 2358, "source_snapshot_store": {"manifests": {"bytes": 145713489, "files": 1}, "packs": {"bytes": 8566123, "files": 1}}}}`.
No eviction policy or speed threshold is claimed.

Behavioral fingerprint:`0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f`.
Graph bundle SHA256:`c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.

All source/command/raw partition/hash/resource records are under
.spectra/evidence/aci-s08/probe/. Serial600s/job/2700s total caps were unchanged.

Prior failed-attempt charge: 0.000000s; it remains inside the unchanged2700s budget.
Cumulative serial job charge including size: 486.645136s.

Both corpora contain zero .rs files; profile10 includes Rust. Snapshot correctness is established by actual native graph/source/lease/worktree/overlay tests, independently from this corpus regression.
Prior and cumulative probe charges are sums of serial job elapsed_seconds, not total wall runtime; original elapsed_total fields retain orchestration overhead.
