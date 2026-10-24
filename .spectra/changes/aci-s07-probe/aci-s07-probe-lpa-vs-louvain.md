# ACI-S07 current producer measurement

Fresh default-profile CPU measurements on unchanged public pins. The earlier S02
metrics and all failed S07 attempts remain historical evidence. No coding-agent,
token-savings or compiler-completeness claim is made.

Recorded verdict: **PASS**. The unchanged verifier must independently
recompute this verdict; exit0 alone does not establish PASS. Bar: median Louvain
Q >=0.30, shipped LPA Q >=0.30, and LPA Q >=0.85 × median; resolution 1.0, seeds 0..9.

| Repo | Nodes | Edges | LPA Q | Louvain median | Ratio |
|---|---:|---:|---:|---:|---:|
| solidus | 1883 | 1782 | 0.928255777629 | 0.960300410263 | 0.966630616533 |
| spree | 5474 | 5947 | 0.872669824411 | 0.958270309317 | 0.910671880289 |

## Source observation coverage

Every original eligible path/hash matches the preserved inventory:2519 Solidus
and2358 Spree,4877 total. Failed extraction quarantines the whole file; failed
files remain explicit observations and contribute no newly extracted facts.
No historical facts were present in the fresh measured inputs.

| Repo | Indexed | Failed | Original eligible observations |
|---|---:|---:|---:|
| solidus | 2459 | 60 | 2519 |
| spree | 2332 | 26 | 2358 |

Per-language counts, exact original hashes and diagnostics are preserved in
the raw observation manifests under the attempt3 evidence directory. Partial
coverage is not a claim that the parser accepts every dialect or input.

- solidus: `4026945d614e81383c007ed1ab1278a0195ce5d9`; clean tracked source verified before and after indexing.
- spree: `6699cde44303ea85ef6e56c5e87c44a738ab73fc`; clean tracked source verified before and after indexing.

Solidus uses the full pinned tree; Spree retains historical `spree/*` checkout.
No source transformation, dependency change, grammar replacement or repository
hooks/tests were used. Networkx 3.6.1 ran only in isolated `uv --no-project` jobs.

## Resources and current export size

Serial subprocess deadlines:600 s/job and2700 s total, including393.127 s charged
to preserved failed/diagnostic attempts before this retry. All command exits,
actual timings and peak RSS are retained; no deadline was relaxed.

| Job | Seconds | Peak RSS bytes |
|---|---:|---:|
| solidus-export | 109.252 | 1003782144 |
| solidus-account | 0.150 | 44367872 |
| solidus-modularity | 0.491 | 53657600 |
| spree-export | 147.014 | 1559920640 |
| spree-account | 0.138 | 43646976 |
| spree-modularity | 1.009 | 64651264 |
| spree-jsonl | 66.086 | 1983594496 |

Current full Spree v2 JSONL export: **651,844,965 bytes**, SHA256`394461aefaa48287f0d6701463bc0b427542d30a794940ca7f7c9d918999c199`.
This new measurement does not replace the historical export-size saving or its
frozen gate. The large raw export remains temporary; only its exact size/hash
and actual producer resource evidence are retained.

Behavioral fingerprint: `0605f4ae8f83e953d3dd95cd2bb0b55bf37e8d0fa3c0c31a176314ec7b94383f`.
Bundle SHA256: `c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.

Raw graphs and all 20 seeded Louvain partitions are losslessly packaged with
original hashes. See `.spectra/evidence/aci-s07/parser-prerequisite/fresh-probe-attempt3/`.
