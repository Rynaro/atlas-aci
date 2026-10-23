# ACI-S02 — current producer LPA/Louvain evidence

This is a fresh local CPU measurement of the constrained resolver, separate from
the unchanged archived probe. It is not a model/agent evaluation and makes no
token-savings or coding-success claim. The graph input changed; higher modularity
does not establish an improvement over the historical measurement.

Current verdict: **PASS**. Fixed bar: median Louvain Q >= 0.30, LPA Q >= 0.30 and
LPA Q >= 0.85 × median Louvain Q; resolution 1.0, seeds 0..9, ten runs per repo.

| Repository | Nodes | Edges | LPA Q | Louvain median Q | Ratio |
|---|---:|---:|---:|---:|---:|
| solidus | 1787 | 1675 | 0.927811628425 | 0.961363154377 | 0.965100050070 |
| spree | 5442 | 5907 | 0.874108956302 | 0.958660361384 | 0.911802543958 |

Both disposable inputs were fetched at exact commits and checked for a clean
tracked source tree before indexing. Solidus is the complete shallow tree; Spree
uses the historical runbook's `spree/*` sparse checkout. No repository tests ran.

- solidus: `4026945d614e81383c007ed1ab1278a0195ce5d9`
- spree: `6699cde44303ea85ef6e56c5e87c44a738ab73fc`

Source SHA-256: `cb13abaf72e673071d1f8d8c5af624bb472a38c8ed57431107145df2c8f10cb7`.
Behavioral indexer fingerprint: `11adbda5b9d9a7fc9f81e2c24760e29ce672dfaa4693c6a518f8f7dffac86ca0`.
Bundle SHA-256: `e2e3eb28a6fecd4fa44b840954f1810e97eb95fd77e203e0e04f8cfb205a2bd3`.

All jobs were serial, bounded to 600 seconds each and 2700 seconds total.
No deadline fired. Peak memory below is actual macOS `/usr/bin/time -l` RSS.

| Job | Seconds | Peak RSS bytes |
|---|---:|---:|
| solidus-export | 71.758 | 296304640 |
| solidus-modularity | 0.921 | 57376768 |
| spree-export | 83.520 | 418971648 |
| spree-modularity | 1.063 | 64307200 |

Raw graphs, all twenty seeded partitions, commands, stdout/stderr, runtime
environment and assembly provenance are under
.spectra/evidence/aci-s02/probe/. The shipped LPA and independent networkx
3.6.1 Louvain runs used identical graph primitives; the unchanged verifier
recomputes every Q/count from the committed bundle. Networkx was isolated with
`uv run --no-project --with networkx==3.6.1`; project dependencies are unchanged.
