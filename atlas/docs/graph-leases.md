# Captured graph pages and source resources

`graph_query` accepts opt-in `page` and `continue` operations in `atlas.graph.v1`.
Ordinary status, legacy, neighborhood and path requests retain existing behavior.

```json
{"schema_version":"atlas.graph.v1","op":"page","query":{"schema_version":"atlas.graph.v1","op":"neighborhood","seed":{"name":"caller"},"max_depth":1,"max_bytes":1048576},"page_max_bytes":18000}
```

The nested query is an existing neighborhood or path request. Its `max_bytes`
limits the retained collection independently of `page_max_bytes`, which limits one
complete MCP `CallToolResult`. The collection minimum is 3,448 bytes, measured with
conservative dual text/structured framing; ordinary navigation remains 3,436 bytes.
A page must fit metadata and one whole captured item, otherwise issuance fails with
`PAGE_TOO_SMALL` before any usable reference is issued.

Continue with the returned original `data.query_digest` and `data.next_cursor`:

```json
{"schema_version":"atlas.graph.v1","op":"continue","cursor":"<saved cursor>","query_digest":"<original digest>"}
```

Issuance runs one bounded traversal and constructs all pages before exposing any.
Continuation reads saved canonical pages: repeated and concurrent calls return the
same page and next cursor without traversal or latest-generation reacquisition.
This does not resume BFS beyond terminal work limits. `captured.outcome`, stop
reason, frontier and counters describe the captured query. Exhausting pages can
still leave a partial query; a collection byte stop is `retained_collection_limit`.
Neighborhood edges retain both identities and complete evidence. A path is one
indivisible item; a successful path prefix is never substituted.

Each resource authorizes exactly its returned assertion's evidence span and hash
in G1. Call MCP `resources/read` with the issued `atlas-source://` URI. Results are
base64 `application/octet-stream` blobs with `atlas.source.v1` `_meta`: exact span,
returned byte offsets, original generation/snapshot IDs and `next_uri` if needed.
Offsets are bytes relative to that span; prefixes may split UTF8 and preserve NUL.
There is no live-file fallback or fabricated source for source-free/legacy imports.
Resource listing is empty, with one bounded template and no subscriptions.

The URI's `max_bytes` bounds the complete SDK `ReadResourceResult`, including URI,
base64, metadata and next URI, and cannot exceed the server response cap. Raw bytes
also obey `max_bytes_per_call`. The exact progress minimum depends on issued URI,
offset and completion metadata: admission measures one byte, or empty EOF, using
actual SDK serialization. Insufficient budgets return `INVALID_BUDGET`, never an
endless same-offset URI. Resource errors use fixed bounded SDK `ErrorData`, not
`isError`; the measured fixed ErrorData cap is 93 bytes. JSON-RPC IDs and transport framing are outside these result caps.
Invalid URI/scope/budget/policy uses -32602; valid retired references use
-32002/`EXPIRED_REFERENCE`; unavailable source uses -32002; corruption uses
-32603/`SOURCE_CORRUPT`. Restart credentials are invalid, since the new process
cannot authenticate the old MAC.

An owned lease retains a real SQLite connection and source-pack descriptor through
G2/G3 publication and old-path pruning. Acquisition still validates all graph and
source integrity. Resource-owning acquisition derives private 64 KiB hashes from
those same verified buffers; later reads verify touched chunks and return slices
of exactly those buffers. I/O is at most returned bytes plus two chunks. Ordinary
leases retain no private chunk catalog. Persistent and portable formats are
unchanged. There is no global or new-generation validation cache: continuation and
resource reads reuse only their own validated G1 lease/catalog, and touched chunks
are verified again.

Positive exact-integer `Config` policies default to four pending plus active
leases, 300-second absolute monotonic lifetime, 16 MiB canonical state per lease,
64 MiB total state, 512 MiB referenced packs, 256 pages and 4,096 descriptors per
lease, and 2,000 ms setup after mandatory acquisition. No new CLI/environment
controls are added. Accounting includes pages, tokens, descriptors, chunk catalogs,
reader metadata and lease identity/scope. Canonical-byte and FD limits are not a
Python RSS guarantee. The setup deadline covers final serialization/accounting
before ownership transfer. Capacity failure rejects new issuance without evicting
unexpired promises.

Idle expiry and shutdown close handles. Final pages remain replayable until fixed
expiry. Failed or cancelled setup closes unpublished ownership. Unlinked pack
storage remains physically retained while its FD is open. Authorization scope is
local operator/server configuration, repository, generation and query, not an
authenticated multi-user identity. Credentials are process-local, not durable.
