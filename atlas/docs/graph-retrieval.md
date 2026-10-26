# Task-context candidates

`graph_query` accepts `schema_version: "atlas.graph.v1"`, `op: "retrieve"` and
`text`. Retrieval requires a native epoch9 index. Serving an older epoch6–8
index returns `INDEX_UPGRADE_REQUIRED`; serving does not migrate it. Existing
legacy queries, status, navigation and retained page/source-resource operations
keep their own compatibility contracts.

For example:

```json
{"schema_version":"atlas.graph.v1","op":"retrieve","text":"task phrase","path_prefixes":["src"],"max_candidates":10,"max_bytes":20000}
```

Candidates are current indexed entities or whole indexed UTF-8 source files.
They are context candidates, not semantic call-resolution or relevance-quality
claims. Historical facts do not become fresh retrieval documents. Failed,
disabled, unsupported and non-UTF-8 source files are excluded from source-text
retrieval and reflected in coverage diagnostics. Source-free imported graphs can
return entity metadata with unavailable source references; they do not recreate
file text from the destination working tree.

Ordering is exact entity ID, exact qualified label, normalized relative path,
exact literal entity name, then lexical BM25 score ascending and candidate ID.
Exact matches are deduplicated before the lexical tier. A qualified label is the
literal canonical `atlas-qualified-label.v1` JSON string returned on the candidate:
it includes language, path, owner chain, leaf name, kind, ordinal and identity
basis. It is not a compiler-qualified namespace. Repository equality and
component-aware relative path-prefix filters apply inside each SQL tier before
ranking and candidate limits. Prefix `src` includes `src/a.py`, not `src2/a.py`.

Lexical input is one literal FTS5 phrase using the `unicode61` tokenizer with
`remove_diacritics 2`. Quotes and query operators are escaped as data. Query text
must contain 1–4096 UTF-8 bytes and no NUL; tokenless input produces a valid empty
result. A SQLite failure or interrupted ranking operation produces a typed error,
not an empty result. Identical successful queries on the same generation preserve
candidate ordering; work/deadline limits can instead cause an error.

Entity candidates carry canonical entity, occurrence and evidence identities when
available. Their available source references use the linked occurrence's exact
hash and byte span. Source-file candidates use `granularity: "file"` and
`[0, source_size)` over the exact consumed UTF-8 buffer. This is not a matched-term
offset, snippet, assertion or retained resource URI. These results do not issue an
S13 continuation or source-resource lease. The request holds one validated SQLite
and source-pack generation through result construction; subsequent retrieval uses
a newly acquired generation. `expected_generation_id` rejects a mismatch.

Limits default to 100 candidates, 1,000,000 reserved VM operations and a 2000ms
query deadline. Candidates range from 1–100, VM allowance from 0–10,000,000 and
deadline from 0–2000ms; path-prefix lists contain at most 16 items. Mandatory
whole-generation canonical/source/FTS integrity validation precedes this query
interval and is outside its counters. SQL ranking can perform internal FTS scans
and temporary sorts despite a candidate LIMIT; temporary storage is in memory.
The VM progress hook is cooperative, with quantum1 and a conservative prepaid
statement quantum plus observed callbacks. Counters describe explicit driver SQL,
key rows and proof hydration, not hidden FTS SQL statements or exact C-runtime
work. These controls are not a hard wall-time or memory guarantee.

Interrupted work returns `RETRIEVAL_BUDGET_EXCEEDED`; unexpected SQLite errors
return `RETRIEVAL_FAILED`. The owned progress hook and request's SQLite/pack handles
are released on success, error and cancellation. Published FTS data is a private
native derivative: both ordinary documents and actual FTS candidate-ID/text pairs
are validated against canonical metadata and authenticated source bytes. A healthy
SQLite integrity result alone does not establish that correspondence. No validation
result is cached across newly acquired generations.

`max_bytes` uses the existing complete SDK `CallToolResult` budget, including both
structured data and compatibility text when supported by the negotiated protocol.
Admission keeps a deterministic prefix of whole candidates and reports truncation;
it never clips owner chains, hashes, spans or proof records. Operator recursive
list caps can omit a whole oversized candidate. A budget too small for bounded
metadata returns `INVALID_BUDGET`; fixed errors follow the existing measured error
cap. JSON-RPC framing is measured separately from the result object.

Portable epoch7 evidence exports contain no FTS tables or source text. Native
rebuild/import derives new retrieval storage without changing canonical portable
records. No live source fallback repairs missing or contradictory modern metadata
while serving.
