# MCP protocol-revision compatibility

This page states which Model Context Protocol (MCP) revisions the ATLAS ACI server was
observed to serve, how a revision is negotiated, and what was and was not tested. It describes
behaviour only; nothing here changes what the server sends.

## 1. Summary

- Transport: stdio only. The server is `atlas-aci serve`; there is no HTTP or SSE transport.
- SDK: the locked Python SDK `mcp` 1.30.0 owns the wire. The dependency floors were raised by the
  security re-lock (`pyproject.toml` and `uv.lock`; receipts in `.spectra/evidence/sec-relock/`),
  after which every transcript was regenerated and differs from the mcp 1.27.0 recording only by
  the SDK version string. The supported set below is exactly what that SDK offers.
- Selected revision: `2025-11-25`, the newest revision the SDK offers (not the newest official
  revision; see section 10).
- The Atlas-owned table lives in `mcp-server/src/atlas_aci/protocol/revisions.py`. It is pure data
  and a pure negotiation model. A tripwire test fails when the installed SDK, its supported list
  or its latest revision stops matching the table, so an SDK bump cannot quietly promise support.

## 2. Revision matrix

| revision | family | status | outputSchema and structuredContent | verified with | golden file |
| --- | --- | --- | --- | --- | --- |
| 2024-11-05 | legacy | retained_legacy | none; text content only | raw line fixture host, mcp 1.30.0 ClientSession, official schema checks (draft-07), KNOWN_DEVIATIONS none | `tests/fixtures/s38/transcripts/legacy.2024-11-05.v1.json` |
| 2025-03-26 | legacy | retained_legacy | none; text content only; JSON-RPC batching unsupported | raw line fixture host, mcp 1.30.0 ClientSession, official schema checks (draft-07), KNOWN_DEVIATIONS none | `tests/fixtures/s38/transcripts/legacy.2025-03-26.v1.json` |
| 2025-06-18 | modern | verified_modern | `graph_query` advertises `outputSchema`; every `graph_query` result carries `structuredContent` | raw line fixture host, mcp 1.30.0 ClientSession, official schema checks (draft-07), KNOWN_DEVIATIONS none | `tests/fixtures/s38/transcripts/modern.2025-06-18.v1.json` |
| 2025-11-25 | modern | selected | same as 2025-06-18 | raw line fixture host, mcp 1.30.0 ClientSession (raw initialize and plain `initialize()`), official schema checks (draft 2020-12), KNOWN_DEVIATIONS none | `tests/fixtures/s38/transcripts/modern.2025-11-25.v1.json` |

Families: `legacy` and `modern`. Structured results are enabled only for the two modern
revisions, as before this slice. "Verified" means exactly the checks named in the fifth column
ran against the fixture repository `s38-mini.v1`; it does not extend to any real host.

Facts that hold for every revision (measured): the same 7 tools; `test_dry_run` carries `_meta`
and `annotations`; the resource template `atlas-source://`; `serverInfo` is `atlas-aci` with the
SDK version `1.30.0`; `experimental` is `{}`; the capabilities are identical. The `tools/list`
result is 12923 bytes of canonical JSON for 2024-11-05 and 347091 bytes for 2025-11-25.

## 3. Negotiation

The SDK answers `initialize`: a revision it supports is echoed back, anything else receives the
SDK's latest revision, `2025-11-25`. Atlas keeps that behaviour byte for byte. The adapter only
names the outcome and picks the revision that shapes manifests and tool results.

| requested | outcome | reason | offered | shaping revision | verified |
| --- | --- | --- | --- | --- | --- |
| a string in the Atlas table and the SDK list | supported | listed_revision | the request | the request | true |
| a string in the SDK list but not in the table | supported | sdk_only_revision | the request | 2024-11-05 | false |
| any other string (`2099-01-01`, the empty string, a draft id) | counter_offer | unknown_revision | 2025-11-25 | 2025-11-25 | true |
| an integer | counter_offer | integer_revision | 2025-11-25 | 2025-11-25 | true |
| `protocolVersion` absent | malformed | missing_revision | none | none | false |
| `protocolVersion` of another type (null, a list) | malformed | invalid_revision_type | none | none | false |
| other invalid initialize parameters | malformed | invalid_initialize_params | none | none | false |

Outcomes: `supported`, `counter_offer`, `malformed`. Wire: an `initialize_result` for the first
two outcomes, an `invalid_params_error` for `malformed` (a JSON-RPC error `-32602` with message
`Invalid request parameters` and data `""`). Observed wire examples (raw line host, goldens under
`tests/fixtures/s38/transcripts/`):

- `protocolVersion` `2099-01-01` or the integer `20241105`: an `InitializeResult` with
  `protocolVersion` `2025-11-25`; the session then behaves as `2025-11-25`.
- `protocolVersion` missing, null, a list, or `capabilities` missing: `-32602`.
- Any request other than `ping` before `initialize` (for example `tools/list`): `-32602`.
- `ping` before `initialize`: `{}`.
- After a rejected `initialize`, a valid `initialize` on the same process is accepted.

A revision the SDK supports but the table does not list is shaped as `2024-11-05` (no
`outputSchema`, no `structuredContent`) with `verified` false, so an unverified revision can never
turn structured results on. This was exercised end to end by extending the SDK list in a server
child.

### What a client does on a counter-offer

The server returns the counter-offer and the client decides. A spec-compliant client that cannot
speak `2025-11-25` disconnects; the fixture tests show a future-only client and a legacy-only
client doing exactly that, the server staying alive and exiting 0 when the client closes. To retry,
request `2024-11-05` (or any listed revision) explicitly. The specification's example for an
unsupported revision is a `-32602` error with message "Unsupported protocol version". Atlas does
not emit it: the SDK owns the lifecycle and answers with a counter-offer, and replacing it would
change the wire that the existing 2099 tests and every host already rely on.

### Diagnostics

The server emits no negotiation record: nothing is written to stderr or stdout for it, and no code
in the request path performs input or output beyond the SDK's own. A test asserts that stderr for
an unsupported-revision session, a normal modern session and a legacy session carries no
`protocol_negotiated` record and no `Traceback`. The incompatibility diagnostic is the
deterministic wire above plus two pure functions in `atlas_aci.protocol.revisions`:
`classify_initialize(params)` and `Negotiation.record()`, whose record format is
`atlas-protocol-negotiation.v1` with a closed key set. The record bounds client input: `requested`
keeps the first 64 characters, with `requested_len` and `requested_truncated`.

## 4. The documented conversation

The same conversation, `lifecycle.v1`, runs for each of the four revisions. Steps 7 to 16 use the
fixture repository `s38-mini.v1` (one file, `a.py`, four lines). Steps 7 to 13 succeed, steps 14
to 16 are tool errors, step 17 is a JSON-RPC error.

| step | method | arguments |
| --- | --- | --- |
| 1 | initialize | `revision-specific` |
| 2 | notifications/initialized | none |
| 3 | ping | none |
| 4 | tools/list | none |
| 5 | resources/list | none |
| 6 | resources/templates/list | none |
| 7 | tools/call graph_query | `{"query":"definitions_of:target"}` |
| 8 | tools/call graph_query | `{"query":"callers_of:target"}` |
| 9 | tools/call search_symbol | `{"name":"target"}` |
| 10 | tools/call view_file | `{"end_line":4,"path":"a.py","start_line":1}` |
| 11 | tools/call graph_query | `{"op":"status","schema_version":"atlas.graph.v1"}` |
| 12 | tools/call graph_query | `{"deadline_ms":2000,"max_depth":1,"op":"neighborhood","schema_version":"atlas.graph.v1","seed":{"name":"target"}}` |
| 13 | tools/call graph_query | `{"deadline_ms":2000,"op":"path","schema_version":"atlas.graph.v1","seed":{"name":"caller"},"target":{"name":"target"}}` |
| 14 | tools/call graph_query | `{"query":"not-a-query"}` |
| 15 | tools/call graph_query | `{"query":false}` |
| 16 | tools/call write_file | `{"content":"x","path":"a.py"}` |
| 17 | logging/setLevel | `{"level":"info"}` |

The page, continue and resource-read steps run live on a second, larger fixture; they are not in
the goldens because their lease and cursor tokens are random.

Golden transcripts, `atlas-s38-transcript.v1`, were captured from a checkout of the base commit
before any source change, through the production path (`atlas-aci index` then `atlas-aci serve
--graph-root`), twice with different `PYTHONHASHSEED` values, and compared byte for byte. They are
normalised by nine closed rules: N1 response ids become first-appearance ordinals; N2 fixture
paths become `<repo>`, `<memex>` and `<graphs>`; N3 JSON text content is parsed and its exact text
is hashed; N4 64-hex runs become `<h64:N>`; N5 lease and cursor values become tokens; N6
`serverInfo.version` is kept verbatim; N7 the `tools/list` result becomes a digest; N8 server
notifications are collected separately and stderr is excluded; N9 canonical JSON plus one LF. A
field that varies and is not covered by these rules is a defect, not a new rule by default.

## 5. Declared fixture hosts

Two fixtures, labelled as fixtures and nothing more:

- an independent raw JSON-RPC line client (standard library only) over a real `run_stdio`
  subprocess, one process per conversation;
- the mcp 1.30.0 `ClientSession`, once with a raw `initialize` for the requested revision and once
  with plain `initialize()`, which requests `2025-11-25`.

## 6. Real hosts

- Claude Code: untested; negotiated revision not verified.
- GitHub Copilot: untested; negotiated revision not verified.
- Cursor: untested; negotiated revision not verified.

No claim is made about which revision a real host negotiates or how it handles a counter-offer.

## 7. Emitted on every revision

`_meta` and `annotations` on `test_dry_run` are not removed for the legacy revisions. They were
accepted by the official 2024-11-05 schema checks, so no per-revision stripping exists. Also
unchanged: `serverInfo.version` equal to the SDK version, `experimental` `{}`, and identical
capabilities. Resource blobs carry `_meta` (with the generation id); that was observed in the
modern live runs only, because the legacy conversations do not read resources.

## 8. Unsupported

- HTTP and SSE transports.
- JSON-RPC batching, for every revision including 2025-03-26. Exercised: a one-line batch after
  `initialize` yields a server log notification and no JSON-RPC response, and the next `ping` is
  answered.
- `logging/setLevel`: `-32601` Method not found (exercised, step 17).
- An unknown method after `initialize` (exercised with `foo/bar`): `-32602` Invalid request
  parameters.
- Not offered, and not exercised by the fixtures: tasks, sampling, elicitation, prompts,
  completions and pagination.
- Legacy clients with strict-schema checks: the 2024-11-05 and 2025-03-26 tool descriptors carry the extra
  `_meta` and `annotations` members; the official schema checks accepted them, but no real strict
  client was tested.

## 9. Framing-dependent admission

For `context`, `continue` and `impact` the byte budget admitted for a result depends on the
framing of the tool-result envelope by design, so they are excluded from the cross-revision
equivalence tests; `status`, `neighborhood`, `path`, `retrieve`, the legacy query language,
`search_symbol` and `view_file` return identical text bytes under 2024-11-05 and 2025-11-25.

## 10. Schema provenance

The official `schema.json` files for the four revisions were fetched from
https://github.com/modelcontextprotocol/modelcontextprotocol at commit
`3c4d91b7e832d9b74338d56eed03403ccc662123` and committed unmodified under
`mcp-server/tests/fixtures/s38/schemas/` with their sha256 in `PROVENANCE.v1.json`. The license
is Apache-2.0 (the repository root states a transition from MIT; the license text is copied as
`LICENSE.mcp-specification`; there is no NOTICE file). Dialects: 2024-11-05, 2025-03-26 and
2025-06-18 are draft-07 and use `definitions`; 2025-11-25 is draft 2020-12 and uses `$defs`.

The repository also holds a newer revision, `2026-07-28` (not supported: mcp 1.30.0 does not offer it). The matrix therefore says "selected = newest SDK-supported", not "official latest".

Every response and error frame of the four conversations, the unsupported and malformed cases, and
a live page and resource-read run is validated against its negotiated revision's schema.
`KNOWN_DEVIATIONS`: none. A corrupted frame fails under each schema, which also shows that nested
references resolve. If the official schema ever rejects a frame, the failure set must equal an
explicit `KNOWN_DEVIATIONS` list in the tests, and this page must name each entry.

## 11. Upgrading the SDK

1. Bump the SDK deliberately; the tripwire test fails first.
2. Add or change the row in `revisions.py` and decide its status.
3. Re-capture the golden transcripts from a pristine checkout and record the provenance.
4. Update this page.
