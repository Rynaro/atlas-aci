# Bounded graph navigation

`graph_query` accepts `atlas.graph.v1` requests with `op: neighborhood` or
`op: path`. Status and the existing string query language remain available.
New traversal requires a native epoch 8 index. Reading an older native index
continues to support status and legacy queries; index again, or export and import,
to publish the navigation indexes. Import preserves portable schema 7 facts and
IDs. A present but malformed higher-epoch native index never silently selects older
data. When epoch 8 is absent, valid older indexes remain readable for status and
legacy queries; navigation reports the required upgrade.

```json
{"schema_version":"atlas.graph.v1","op":"path","seed":{"name":"caller"},"target":{"name":"target"},"direction":"outgoing","relations":["call"],"confidence":["EXTRACTED","INFERRED"],"max_depth":3,"max_visited_nodes":100,"max_examined_edges":1000,"deadline_ms":100}
```

Each endpoint is exactly one name or canonical `entity_id`. A name lookup reads
at most two indexed candidates. Ambiguity is returned explicitly and never fans
out. Candidate arrays are bounded prefixes; `candidates_complete: false` means
more candidates or an omitted whole identity. The independently reported unique
`entity_id` does not depend on how many candidate identities fit the response.

Directions select outgoing or incoming adjacency. Incoming traversal keeps each
assertion's original source and target and labels its traversal direction.
Relations are processed in sorted order, assertions in canonical ID order. Only
active, resolved EXTRACTED/INFERRED assertions participate. Unknown calls and
historical evidence are excluded. Parallel assertions remain distinct evidence
and each spends an examined-edge reservation. A path is the first discovered
shortest unweighted path; this is structural evidence, not runtime reachability.

| Limit | Default | Maximum |
|---|---:|---:|
| Visited nodes | 100 | 1,000 |
| Examined edges | 1,000 | 10,000 |
| Depth | 3 | 32 |
| Traversal deadline, ms | 100 | 2,000 |

Limits are integers, excluding booleans and floats. Node allowance is reserved
before discovery, edge allowance before each indexed row step. Rejected
confidence and unresolved rows still spend edge work. Only confirmed EOF refunds
a reservation; an interrupted in-flight step stays charged. Name/ID and fixed
proof hydration SQL have separate counters. No extra adjacency lookahead proves
that a capped result was exhaustive. Depth zero returns the seed without reading
adjacency. A stopped search returns `partial`, not a false `no_path`.

The response's labelled identity and assertion-proof projections include canonical
IDs, original producer, source hash, half-open byte span and source availability.
Source-free imports retain recorded provenance but cannot supply source bytes.
A successful path always includes the entire coherent chain; a partial path is
null. List limits apply recursively to whole identities and proofs. An owner chain
is never shortened to make an identity fit. Requested relation/confidence filters
are fixed bounded metadata, not enumerations of graph facts.

`max_bytes` can reduce the operator's result ceiling. Admission measures actual
MCP SDK tool-result JSON, including text escaping and dual structured/text
representation. A common conservative dual-framing minimum of 3,436 bytes applies
to both old text-only and current sessions. Below it, `INVALID_BUDGET` is returned
within the separately measured fixed error-envelope cap (797 bytes across all
operations). Whole proof records and final frontier/counter metadata are reserved
before admission; an oversized successful path becomes a partial null path.

The mandatory authenticated SQLite/source-pack acquisition precedes the traversal
deadline and counters. It validates published generation, source provenance,
canonical/projection coherence and snapshot integrity. Its whole-graph cost is
not hidden in traversal counters and is not bounded by `deadline_ms`. The owned
SQLite progress hook provides cooperative interruption after acquisition, not a
hard total MCP latency guarantee. Normal return, errors and actual task
cancellation clear that hook and release the request's SQLite and pack handles.

Responses contain deterministic counters and a frontier commitment, not timestamps
or elapsed durations. Same snapshot, inputs and stopping frontier reproduce the
same output. Real wall-clock deadlines can stop at different frontiers. A frontier
commitment describes the completed request; it is not a continuation token.
