# Frozen local context comparison

Fixture `07553bd3…` fixes seven source buffers, four hand-selected useful-span tasks, the ranker and byte budgets before packing. The useful-label union sums to 742 bytes across the four tasks. No weights, budgets or labels were tuned after measurement.

The 24 pre-packer S17 results are **reference-only**. Only new `references` rows are a matched source-policy comparison. The 24 new inline rows describe a separate capability; the baseline is never hydrated outside its actual SDK result budget. Source references do not count as delivered inline bytes.

Each table row aggregates four real task calls. The budget applies to each complete negotiated SDK result, not their sum. Useful bytes count exact path/hash/span intersections once per task; duplicate references are reported separately in JSON. Errors remain visible, and fixed errors use the existing server error cap.

|Protocol|Per-call bytes|Operation/policy|Errors|Useful reference bytes|Useful inline bytes|Total SDK bytes|Redundant referenced bytes|
|---|---:|---|---:|---:|---:|---:|---:|
|2024-11-05|4000|S17 references|0|0|0|5992|0|
|2024-11-05|4000|context references|1|0|0|11176|0|
|2024-11-05|4000|context inline|1|0|0|11207|0|
|2024-11-05|12000|S17 references|0|572|0|16260|140|
|2024-11-05|12000|context references|0|373|0|29319|0|
|2024-11-05|12000|context inline|0|373|373|33171|0|
|2024-11-05|32000|S17 references|0|742|0|21048|350|
|2024-11-05|32000|context references|0|742|0|58995|402|
|2024-11-05|32000|context inline|0|742|742|67230|402|
|2025-11-25|4000|S17 references|0|0|0|11328|0|
|2025-11-25|4000|context references|4|0|0|3108|0|
|2025-11-25|4000|context inline|4|0|0|3108|0|
|2025-11-25|12000|S17 references|0|572|0|30826|140|
|2025-11-25|12000|context references|0|133|0|35246|0|
|2025-11-25|12000|context inline|0|0|0|29444|0|
|2025-11-25|32000|S17 references|0|742|0|39946|350|
|2025-11-25|32000|context references|0|572|0|95106|180|
|2025-11-25|32000|context inline|0|572|572|96766|180|

These measurements show a cost, not a general savings claim: complete context proofs consume more bytes and can cover fewer labeled reference bytes under tight or dual-output budgets. At 32000 bytes text-only context references cover 742 labeled bytes and dual context covers 572; the reference-only baseline covers 742 for both. Inline content is delivered only in the new inline policy. All eight 4000-byte baseline rows are valid empty results. Context can instead reject metadata that cannot fit; those errors are included above.

The final 47 runtime hashes are embedded in `local-yield.json` and match `source-final.json`, including the explicitly configured shared-deadline scope. The JSON preserves all 48 final SDK byte strings, hashes, exact lengths, source spans, selected files, truncation/collection reasons and elapsed timings. Every inline span was compared byte-for-byte with the frozen source. Baseline 24 raw SDK byte strings remain unchanged in `baseline-checkpoint.json`.

Timing scope: production adapter calls include mandatory generation/pack/FTS authentication and SDK result construction. Final outer `result_bytes` dumping and JSON-RPC transport are outside these intervals. This is a small local fixture, not corpus throughput or a latency guarantee. Counts use the labeled byte-quarter estimate for this measurement, not model tokens. Declared exact local tokenizers are tested separately through native SDK sessions.

No paid model evaluation, universal relevance claim, token-saving percentage or new performance threshold was introduced.
