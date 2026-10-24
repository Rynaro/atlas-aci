# S08 paired source-lease footprint measurements

Actual first acquisition / same-process repeat on identical pinned bytes. No uncached-filesystem or per-request retained-memory claim.
Ordinary query is a real bounded stdio MCP search_symbol for a verified absent name, returning no definitions and the actual snapshot token.

| Corpus | Mode | Before seconds (first/repeat) | After seconds (first/repeat) | Before process peak RSS B | After process peak RSS B |
|---|---|---:|---:|---:|---:|
| solidus | graph | 20.047369 / 10.171913 | 10.972074 / 11.032409 | 3159588864 | 161447936 |
| solidus | source | 2.324174 / 2.274138 | 0.289650 / 0.287798 | 917864448 | 47808512 |
| solidus | mcp | 18.308031 / 18.387015 | 14.580095 / 14.668053 | 3527737344 | 194707456 |
| spree | graph | 44.359238 / 17.402614 | 18.018405 / 17.869587 | 3580231680 | 241549312 |
| spree | source | 3.634457 / 3.573720 | 0.256922 / 0.257479 | 1464090624 | 47349760 |
| spree | mcp | 29.899046 / 30.655723 | 23.553855 / 23.642666 | 3770580992 | 269942784 |

MCP memory is the actual server process peak, distinct from the client process peak; both are retained in raw output.
Intervals exclude setup as documented above; each job also records complete process runtime.

| Corpus | Before manifest B | Refined manifest B | Exact committed ranges | Source bindings | Unchanged pack B |
|---|---:|---:|---:|---:|---:|
| solidus | 89981511 | 1202989 | 306720 | 2519 | 6317973 |
| spree | 145713489 | 1137624 | 491458 | 2358 | 8566123 |

Whole accounting is separately measured, including its extra work (not ordinary lease latency):
- solidus: before 12.373038s / peak2212495360B; refined 12.691260s / peak173834240B.
- spree: before 18.758525s / peak2793046016B; refined 19.280261s / peak255066112B.

Cumulative corpus-job charge: 1391.461103626s, including all prior719.151777501s and the failed-wrapper measurement. This is a sum of actual serial job elapsed_seconds; runner orchestration wall time is separately recorded. Caps remain600s/job and2700s total.

The independent archived bd77 materialized algorithm matches every new corpus graph/observation commitment and every individual range field/order. Compact summaries do not replace own-graph range validation.
No portable format, eligible source, profile, verifier, bar or threshold changed. Larger corpora still require whole-graph validation work per ordinary request.

Warm direct graph acquisition was slightly higher in these measured repeats: Solidus11.032409s vs10.171913s; Spree17.869587s vs17.402614s. First-process graph, source-only and ordinary MCP intervals were lower in these pairs; no universal latency claim.

Existing historical-payload query callbacks still perform their own active-observation checks. Acquisition and epoch_ok reuse only equivalent validation within the owned lease; no checks were removed or shared across requests.
