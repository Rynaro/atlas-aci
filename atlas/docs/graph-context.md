# Complete task context

`graph_query` accepts `schema_version: "atlas.graph.v1"`, `op: "context"` and
`text` on native epoch9 indexes. It combines current retrieval candidates and
bounded graph expansion through one acquired graph/source generation. Older
indexes require an indexing upgrade for this operation; existing status and
legacy readers retain their compatibility contracts.

```json
{"schema_version":"atlas.graph.v1","op":"context","text":"process_payment","source_policy":"references","max_bytes":32000}
```

The result's `task_context` projection contains complete versioned bundles:
canonical definitions, relationships with both endpoint slots and their complete
assertion/evidence/occurrence/producer chain, an indivisible ordered path when
`target` is supplied, or a native whole-file retrieval document. A file match is
not a matched-term offset or assertion. Recursive calls can have two endpoint
slots referring to the same entity. Unknown legacy/imported source stays explicitly
unavailable; no destination or live source file repairs it.

The frozen `atlas-context-diversity.v1` policy first selects the highest exact
**definition** anchor, when present, then rotates file and owner queues. Ordering
uses retrieval tier, seed position, hop distance, availability, bundle kind and
stable identity. No degree scan or model-based relevance score is performed.
Admission preserves a deterministic prefix of complete bundles. It never clips
proofs, owner chains, paths or source spans to fit a later smaller item.

`source_policy` defaults to `inline_if_complete`. It includes an entire referenced
span only when available and within the default 8192 raw inline-byte allowance.
UTF-8 is returned directly; other exact byte buffers use base64. Otherwise the
reference and omission reason remain. `references` returns no inline bytes.
File references describe `[0, source_size)` over the authenticated consumed buffer;
occurrence references keep their original hash and exact span. Whole-file inline
buffers also validate against that full-file hash. Partial occurrence bytes are
not falsely compared to a full-file hash.

Default collection limits are 32 retrieval candidates, 8 expansion seeds,
128 bundles and 8 MiB of canonical collection state. The collection budget is
separate from the emitted SDK result budget. Defaults also allow depth 2,
256 examined edges, 1,000,000 reserved VM operations and a 2000ms post-acquisition
query interval. Retrieval, every seed's navigation, canonical proof hydration,
source reads and token counting share one finite work context. Starting another
seed does not reset an allowance. SQL counters describe driver queries and
cooperative VM callbacks; they do not imply preemption of arbitrary C-runtime
work. Mandatory whole-generation authentication occurs before this query interval
and is outside its counters. Each frontier explicitly labels its deadline scope
`configured_shared_context`: `effective_limits.deadline_ms` is that configured
global allowance, not elapsed remaining time at one seed. Navigation receives
the actual remaining interval and the same absolute shared deadline hook;
reporting the policy neither resets nor extends it. Elapsed remaining milliseconds
are operational durations and never enter the canonical package identity.
No global filesystem instant or latency bound is
promised.

`max_bytes` bounds the complete negotiated MCP `CallToolResult`, including
compatibility text and structured content where supported. Recursive operator
list limits apply to the entire result. Proofs and references are omitted whole.
The minimum is measured per request: serialize an empty complete context with its
actual query, G1 coverage/frontier, operator bounds and declared tokenizer, reserving
19 count digits and any required bounded continuation fields. Long query/owner
metadata and the negotiated text-only versus dual representation change this size;
there is no universal constant context minimum. Below that measured envelope the
operation returns bounded `INVALID_BUDGET`;
fixed error envelopes follow the existing server error cap, not the caller's
impossibly small allowance. JSON-RPC framing is measured separately.

An operator can supply `Config.context_tokenizer`, a trusted local object declaring
`algorithm_id`, `version` and `encode(str) -> sequence`. No request can load a
module, tokenizer package, URL or executable code. Its exact count covers the
UTF-8 decoding of the **final negotiated serialized SDK result**, including its
own `token_usage`, source URIs and continuation cursor. A bounded fixed-point loop
allows at most 16 calls per result, including final equality checks; cycles,
nonconvergence and callback failures return `TOKENIZER_FAILED`. Trusted synchronous
operator code is not preemptible. With no tokenizer the labeled `byte_quarter.v1`
estimate is `ceil(final_result_bytes / 4)`, not model tokens. Counts are diagnostic
and differ between old text-only and dual representations.

`continuation: true` reserves one S13 ownership slot and captures all complete
pages before issuing the first one. Byte budgets and token counts include final
credentials; admission first reserves maximum count digits and bounded URI/cursor
sizes. A path that cannot fit any requested page returns `PAGE_TOO_SMALL` before
issuance. Continuation exhausts this captured collection; it does not resume a
terminal BFS or rerun retrieval/ranking/tokenization. Repeating one cursor returns
the identical saved SDK payload. Unexpired promises are never evicted to admit a
new request. State, page, resource, private chunk-catalog, pack and descriptor
capacity is finite and charged to the existing S13 limits.

Issued `atlas-source` resources have typed definition, assertion or whole-file
membership in the actual returned bundle. Definitions link entity/occurrence/
evidence/producer; files link native document provenance and current source
binding. No fake assertion is manufactured for either. Only the owned G1 reader
and its authenticated private chunk catalog are reused across requests, and every
touched chunk is checked again. They survive a newer publication and pathname
pruning until expiry/shutdown. Changing operator retention/read scope or tokenizer
object/declaration rejects old context references. Changing negotiated tool representation also rejects context
page continuations; source resources have their separate bounded resource SDK
representation. Ordinary non-context leases keep their previous behavior.

`retrieval_record_id` hashes an explicit canonical domain: normalized request,
repository/G1/snapshot, ranker/tokenizer declaration, ordered complete bundles,
source/provenance/uncertainty, effective limits, coverage, deterministic frontier,
work, stable selected range and resource identities. Random lease/cursor/URI
credentials, expiration/durations and emitted token-count diagnostics are outside
this domain. Equal captured packages retain their identity across independent
issuances; one repeated cursor retains even its credentials and count.

The checked-in local useful-span fixture freezes labels, ranker and byte budgets
before implementation. Its S17 baseline contains **reference-only** results.
Only context `references` rows are a matched policy comparison; inline rows measure
a new capability separately. Results are descriptive, including empty/error and
truncated outputs. They do not establish universal quality, token savings or a
paid model score.
