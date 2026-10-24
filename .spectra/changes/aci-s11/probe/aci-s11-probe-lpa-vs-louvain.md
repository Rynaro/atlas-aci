# ACI-S11 fresh producer measurement

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
| solidus-export | 146.562 | 699777024 |
| solidus-account | 12.713 | 173916160 |
| solidus-modularity | 0.487 | 53952512 |
| spree-export | 207.187 | 1061634048 |
| spree-account | 19.114 | 255000576 |
| spree-modularity | 1.005 | 65699840 |
| spree-jsonl | 91.708 | 1964720128 |

Current Spree JSONL: **651,845,336bytes**, SHA256`fdd923467e5a8a0e572f66f48efe269f775d5334dadfd7a060db23708c6a88d6`.
No new size threshold or historical ratio is claimed. This remains a material
storage/memory cost; raw full export stays temporary, exact measurement retained.

Extraction cache and source snapshot store byte/entry counts: `{"solidus": {"bytes": 334683424, "entries": 2519, "source_snapshot_store": {"catalog": {"bytes": 68, "files": 1}, "manifests": {"bytes": 1202949, "files": 1}, "packs": {"bytes": 6317973, "files": 1}}}, "spree": {"bytes": 531906606, "entries": 2358, "source_snapshot_store": {"catalog": {"bytes": 68, "files": 1}, "manifests": {"bytes": 1137584, "files": 1}, "packs": {"bytes": 8566123, "files": 1}}}}`.
No eviction policy or speed threshold is claimed.

Behavioral fingerprint:`0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f`.
Graph bundle SHA256:`c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.

All source/command/raw partition/hash/resource records are under
.spectra/evidence/aci-s11/probe/. Serial600s/job/2700s total caps were unchanged.

Prior failed-attempt charge: 0.000000s; it remains inside the unchanged2700s budget.
Cumulative serial job charge including size: 480.986927s.

Both corpora contain zero .rs files; the measured generation profile contains10 languages including Rust. Rust quality is established by qualified native workspace and offline Docker tests.
Prior and cumulative probe charges are sums of serial job elapsed_seconds, not total wall runtime; original elapsed_total fields retain orchestration overhead.
