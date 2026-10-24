# ACI-S09 fresh producer measurement

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
| solidus-export | 129.426 | 1039646720 |
| solidus-account | 0.150 | 44498944 |
| solidus-modularity | 0.446 | 53723136 |
| spree-export | 178.338 | 1592262656 |
| spree-account | 0.141 | 43761664 |
| spree-modularity | 1.014 | 64520192 |
| spree-jsonl | 66.407 | 1983627264 |

Current Spree JSONL: **651,845,063bytes**, SHA256`371fe45c1b1e05202fb16289eeeb982c27e567fd79a3bcd01d6e2198ba93bc6f`.
No new size threshold or historical ratio is claimed. This remains a material
storage/memory cost; raw full export stays temporary, exact measurement retained.

Extraction cache bytes/entry counts: `{"solidus": {"bytes": 333306797, "entries": 2519}, "spree": {"bytes": 530609730, "entries": 2358}}`.
No eviction policy or speed threshold is claimed.

Behavioral fingerprint:`0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f`.
Graph bundle SHA256:`c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.

All source/command/raw partition/hash/resource records are under
.spectra/evidence/aci-s09/probe/. Serial600s/job/2700s total caps were unchanged.
