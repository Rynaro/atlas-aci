# Loopback Streamable HTTP transport

This page describes the optional HTTP transport of the ATLAS ACI server (ACI-S39): what it
serves, how to start it, the closed set of rejections, what a client disconnect does, and what is
not supported. It was observed with a raw HTTP client and the mcp 1.30.0 `streamable_http_client`
(section 10); real hosts are untested (section 11). The query engine is the same read-only engine
as stdio. Nothing here changes what a tool returns.

## 1. Summary

- Start it with `atlas-aci serve --transport http`. The default transport stays `stdio` and the
  container image still starts stdio.
- One endpoint: `POST /mcp`. Responses are server-sent events (SSE). It is stateless: every POST
  gets its own SDK transport, there are no sessions and no `mcp-session-id` header.
- Loopback only. A name or address that is not loopback is refused with exit 2 before anything is
  bound. There is no public-bind option; a non-loopback bind needs the S40 shared mode, which is
  not available.
- Trust boundary: there is no authentication. Any local process or user that can reach the port
  can call every tool. Enabling repository test execution would make that local code execution, so
  `serve --transport http` and `run_http` refuse to start (exit 2) while
  `ATLAS_ACI_ENABLE_TEST_EXECUTION=1` is set. Under `docker run --network host` "loopback" is the
  host's loopback, so every local user of the host can reach it; a container's own loopback is not
  reachable through `-p`.
- A client disconnect cancels the request in flight (section 6). This deviates from the MCP
  specification and is deliberate.
- The server never writes graph storage, session files or temporary files. The request body is held
  in memory (at most `--max-body-bytes`).

## 2. Starting it

```
atlas-aci serve --repo REPO --transport http [--host H] [--port N] [--max-inflight N]
                [--max-body-bytes N] [--allowed-origins ORIGINS]
```

| option | default | bound |
| --- | --- | --- |
| `--transport` | `stdio` | `stdio` or `http` |
| `--host` (`http_host`) | `127.0.0.1` | 1 to 253 characters, no whitespace and no `%`; an IP literal must be loopback; a name must resolve only to loopback (section 3) |
| `--port` (`http_port`) | `8765` | 0 to 65535; 0 picks a free port; 1 to 1023 is rejected (see below) |
| `--max-inflight` (`http_max_inflight`) | `8` | 1 to 256 |
| `--max-body-bytes` (`http_max_body_bytes`) | `1048576` | 1024 to 16777216 |
| `--allowed-origins` (`http_allowed_origins`) | none | comma-separated, at most 32, unique, each `http(s)://host[:port]` in lower case; `*` and `null` are rejected |

- The product `--max-inflight` times `--max-body-bytes` must not exceed 268435456 bytes (256 MiB),
  because every admitted body is buffered in memory. Above it is a usage error.
- Ports 1 to 1023 are rejected so that a client cannot be locked out by a bare `Host` header: the
  server requires `Host: name:port`, and a client omits the port on port 80.
- Any HTTP option together with `--transport stdio` is a usage error (exit 2).
- A port already in use is exit 1 with the fixed text "The HTTP listener could not start (is the
  port already in use?)", plus one line from uvicorn on stderr.
- On start one info-level line is logged on stderr: `http_listening host=127.0.0.1 port=NNNN
  path=/mcp max_inflight=8 max_body_bytes=1048576`. With port 0 this is where the port is
  reported. On exit `http_stopped admitted= rejected= peak=` is logged. These are the only Atlas
  log lines of the HTTP path.
- SIGINT or SIGTERM stops it: SSE streams drain, in-flight requests get at most 5 seconds, the
  state is closed and the exit code is 0. The handlers are removed after the first signal, so a
  second signal terminates the process immediately.
- `run_http` also exits with a usage error when the installed `mcp` version is not 1.30.0, the
  version this transport was verified with (the dependency floor is lower).

## 3. The loopback rule

The host is resolved once by `resolve_loopback`, and only the resulting literal address is bound,
never the name:

1. An empty string is `empty_host`. A non-string, a string over 253 characters or one with
   whitespace is `unresolvable`.
2. A `%` anywhere is `scope_id`.
3. One pair of `[]` is stripped.
4. An IP literal: an IPv4-mapped IPv6 address is unwrapped; `0.0.0.0` and `::` are
   `wildcard_address`; anything not loopback is `non_loopback_address`; otherwise the canonical
   address is used.
5. A name goes to the system resolver. An error, an empty answer, an over-long label or a NUL is
   `unresolvable`. Every answer must be loopback: a scope id is `scope_id`, a wildcard is
   `wildcard_address`, and a mix of loopback and other addresses is `mixed_resolution`, while only
   non-loopback answers are `non_loopback_address`. The first IPv4 answer is preferred, else the
   first IPv6 one.

The closed reasons are `empty_host`, `wildcard_address`, `scope_id`, `unresolvable`,
`non_loopback_address`, `mixed_resolution` and `bound_non_loopback`. The last is a post-bind check:
after the listener is created every socket address must be loopback, otherwise the server shuts down
and refuses.

The CLI checks this before building anything and answers exit 2 with `Usage:` and:
"--host must resolve only to loopback addresses (reason: R); a non-loopback bind needs S40 shared
mode, which is not available". `run_http` resolves again, so a DNS change between the check and the
bind fails closed with no listener. Examples refused: `0.0.0.0`, `::`, an empty host, `192.0.2.1`,
`fe80::1%lo`.

## 4. Admission and rejections

Every request is checked, in this fixed order, on the request line and headers only. The body is not
read until stage 6, so a rejected request never reaches the SDK, a reader lease or a query. A body
that is declared but never sent still gets its answer.

| # | stage | rejected when | status | extra header | reason | code |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | peer | the peer address is missing or not loopback | 403 | | `peer_not_loopback` | -32600 |
| 1 | host | `Host` is missing, or not (lower-cased) `name:port` for the bound address, `localhost` or the operator hostname | 421 | | `host_not_allowed` | -32600 |
| 2 | origin | `Origin` is present (repeated, empty, `null`) and not exactly in `--allowed-origins` | 403 | | `origin_not_allowed` | -32600 |
| 3 | path | the path is not exactly `/mcp` | 404 | | `path_not_found` | -32600 |
| 4 | method | the method is not POST (GET, DELETE, OPTIONS, ...) | 405 | `Allow: POST` | `method_not_allowed` | -32600 |
| 5 | admission | `max_inflight` requests are already in flight | 503 | `Retry-After: 1` | `inflight_limit` | -32000 |
| 6 | SDK | see below | | | | |

An absent `Origin` passes (a non-browser client). The port in `Host` is the port the server is
bound to. A request that violates several stages gets the status of the earliest one.

A rejection body is JSON with fixed text; no header value is echoed. Example:

```
HTTP/1.1 403
content-type: application/json
connection: close

{"jsonrpc":"2.0","id":"server-error","error":{"code":-32600,"message":"Origin header is not allowed.","data":{"reason":"origin_not_allowed"}}}
```

The MCP specification says the body of a 403 for a bad Origin MAY be a JSON-RPC error that has no
id; Atlas uses the same shape as the SDK, whose id is the string `server-error`.

Stage 6 is the SDK's answer, unchanged: 413 (plain text) when the body is larger than
`--max-body-bytes`, declared or chunked, before anything is parsed; 406 when `Accept` lacks
`application/json` or `text/event-stream`; a 400 or 415 for a wrong `Content-Type`; 400 with
-32700 for a body that is not JSON; 400 with -32602 for anything that is not one JSON-RPC message
(a batch array is a 400); 400 for an unsupported `mcp-protocol-version`; 202 for a notification. The
SDK check of `Host` is case-sensitive: `LOCALHOST:port` passes the Atlas stage and is then answered
421 in plain text by the SDK.

Excess requests are rejected, never queued. The in-flight count is a ticket held by the HTTP exchange
and by every Atlas handler of that POST, until both have ended, including the unwind after a
disconnect. The active count therefore never exceeds the limit, even while a cancelled request is
still closing its reader. A handler that starts after its exchange has already ended returns an
`isError` tool result ("The request ended before the handler started.") and does no engine work; it
is not a transport error.

## 5. Protocol revision over HTTP

There is no session, so the shaping revision of tools/list and tools/call comes from the
`mcp-protocol-version` request header, through the same `negotiate` table as stdio. A client that
omits the header on a later request is shaped as `2025-03-26`, the SDK default: legacy, with no
structured results and no `outputSchema`, even if it negotiated `2025-11-25` in `initialize`. A
client should send the header it negotiated.

Tested equal to stdio: the initialize result, the tools digest, the resource lists, ping and the
tool results (text bytes, `isError` and `structuredContent`) of the S38 conversation at `2025-11-25`
and `2024-11-05`, a page and its `resources/read` on a larger repository, and a tool call with no
header against the stdio `2025-03-26` shaping. See `hosts/protocol-compatibility.md` for the stdio
goldens.

## 6. Disconnect and cancellation

The MCP specification (`docs/specification/2025-11-25/basic/transports.mdx`, commit
`3c4d91b7e832d9b74338d56eed03403ccc662123`) says:

> Disconnection SHOULD NOT be interpreted as the client cancelling its request.
> To cancel, the client SHOULD explicitly send an MCP `CancelledNotification`.

Atlas deviates deliberately. In stateless mode the SDK ends the work of a POST when its connection
closes, and a query that keeps running for a client that is gone would hold a reader lease and a
slot. So a client disconnect cancels the request:

- The per-request reader lease and its source-pack file descriptor are released (verified), and the
  in-flight slot returns.
- `search_text` and `test_dry_run` kill their immediate child process on cancellation (the TimeoutError
  path is unchanged). Descendants of that child are not killed.
- `notifications/cancelled` is accepted (202) and is a no-op: each POST has its own server task, so
  there is nothing for it to cancel.
- Synchronous operations cannot be cancelled mid-call: legacy `graph_query` and `search_symbol`,
  `view_file` and `list_dir` run to the end on the event loop, bounded by their own caps (SQL limit,
  line, entry and match caps, and `deadline_ms` where the operation has one). While one runs it blocks
  the loop for every HTTP client. Structured `graph_query` operations yield and are cancellable.
- SSE response mode is the only mode of the SDK in which a disconnect reaches the handler; in JSON
  response mode a handler keeps running after its client is gone (observed, not assumed). That is why
  this transport answers with SSE.

A request retried after a disconnect, against the same snapshot and reaching the same deterministic
frontier, returns a byte-identical SSE body (tested with `visited_node_limit`).

## 7. Concurrency and bounds

- `--max-inflight` slots; the 503 answer carries `Retry-After: 1`.
- Body cap `--max-body-bytes`: 413. Request bodies are buffered in memory.
- Keep-alive 5 seconds; graceful shutdown 5 seconds; uvicorn's default header-size limit applies.
- Atlas does not log on the request path. The SDK may write a traceback to stderr when a client
  drops in the middle of a body; scan stderr with that in mind.

## 8. Limitations (S40)

These are measured and left for the shared-operation slice S40:

- `Enforcement.records` grows without bound (`enforcement.py:77`, appended at `enforcement.py:185`);
  stdio has the same growth.
- One process-wide sliding rate limiter is shared by all clients (`enforcement.py:76` and
  `enforcement.py:100-114`; default 200 calls per minute).
- A slow-body POST holds its slot until its body ends or the connection closes; a slow-reading client holds a
  slot without a write timeout; slow headers hold a file descriptor.
- Synchronous operations block the loop (section 6).
- `uvicorn` is not a declared direct dependency. It is used as a locked transitive dependency of `mcp`
  (as are `starlette` and `sse-starlette`); `mcp` itself already imports `uvicorn` when `atlas_aci.server`
  is imported, so the HTTP module is the only importer of it on purpose, not the only way it loads.
- Multi-principal shared operation (S40, `atlas-aci serve-shared`) keeps this loopback-only bind; see
  `docs/operations/shared-http-deployment.md`.

## 9. Not supported

Public or non-loopback bind, TLS, authentication or authorization, sessions, the standalone GET
stream, DELETE, resumability and an event store, batching (400), websockets, CORS preflight
(OPTIONS is 405).

## 10. Verified with

- A stdlib raw HTTP client over asyncio streams (chunked decoding, an SSE parser that handles CRLF,
  comments and `event:` and `id:` fields), against a real uvicorn listener on a loopback port.
- The mcp 1.30.0 `streamable_http_client` with `ClientSession`.
- Locked dependencies: mcp 1.30.0, uvicorn 0.44.0, starlette 1.3.1, sse-starlette 3.3.4, anyio 4.16.0.

## 11. Real hosts

- Claude Code: untested; negotiated revision not verified.
- GitHub Copilot: untested; negotiated revision not verified.
- Cursor: untested; negotiated revision not verified.

## 12. Read-only over HTTP

The seven read-only tools are unchanged and the transport adds no write path: no event store, no
session files, no temporary files and no graph writes. The request body is parsed from memory and
dropped with the request.
