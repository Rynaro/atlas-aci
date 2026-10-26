# Versioned graph status over MCP

`graph_query` accepts the existing `{ "query": "definitions_of:Name" }` form and
one structured operation:

```json
{"schema_version":"atlas.graph.v1","op":"status"}
```

An optional `expected_generation_id` must be a 64-character lowercase SHA-256
identifier. A mismatch returns `SNAPSHOT_MISMATCH`; it does not retrieve an older
generation. Structured requests are closed objects. Unknown versions,
operations, fields, invalid types and mixed legacy/structured requests fail
before acquiring a graph reader. Legacy requests retain their existing query
syntax and compatibility with unrelated extra arguments.

Status describes one validated, published generation through one read-only
lease. It reports stored configuration and producer/capability declarations,
published file observations, completeness counts, and authorized source
references. Current observed bytes and retained historical indexed bytes have
separate hashes and provenance. The global limitations field is null when an
older capability version did not record it; current runtime limits are not
substituted. Source-free imports and legacy unknown coverage
remain unknown. An absent index is a successful unavailable status; corrupt
metadata, missing native source packs and inaccessible evidence are errors.

Status does not inspect current source files or include a later failed indexing
attempt, current runtime capabilities, timestamps or durations. The operator CLI
status continues to provide its existing live diagnostic view. With the same effective response bounds and byte budget, repeating the same
MCP request against the same published generation produces the same payload,
even if a later failed attempt, runtime capability declarations or active indexing
language settings have changed.

The closed envelope contains `schema_version`, `operation`, `status`,
`generation_id`, `snapshot_id`, `evidence_status`, `data`, `error` and `bounds`.
Configuration and capability records are explicitly bounded projections, not
portable records carrying a claim of full hash-bound contents. Every nested list
is capped by the configured element limit. `bounds.lists` records each returned
list's full count, returned count and truncation flag. Completeness and source
totals describe the full published evidence, independently of returned prefixes.

MCP protocol versions 2025-06-18 and 2025-11-25 receive the graph output schema,
`structuredContent` and matching JSON text. Versions 2024-11-05 and 2025-03-26
receive the same decoded payload through JSON text, without structured fields or
an advertised output schema. The locked SDK's actual negotiated protocol version
controls this choice, including fallback from unsupported requested versions;
client names do not. All seven existing tool names remain available.

All tool results pass through explicit input validation, mode-specific graph
output validation and a serialized SDK result byte limit. Invalid legacy queries
and failed operations set MCP `isError: true`. Valid empty results, unavailable
coverage, and a completed repository test process with nonzero exit status do
not become tool errors merely because their data includes the word "error".
Unexpected internal exceptions return a fixed public error without exception
contents.

The byte ceiling includes JSON escaping, text, structured content and `isError`
in the serialized `CallToolResult`. JSON-RPC request IDs and transport framing
are outside that ceiling. The smallest bounded overflow error is 148 bytes for
legacy text, 251 for legacy text plus structured content, 403 for structured
status text, and 729 for structured status text plus structured content. A
configured ceiling below the applicable minimum returns `INVALID_BUDGET` under
a separately measured fixed error-envelope cap (785 bytes for status; 797 bytes
across status and navigation operations). Oversized results use a
fixed bounded error rather than recursively returning oversized diagnostics.
