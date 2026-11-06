---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T22:00:00Z
slice_id: ACI-S39
issue: https://github.com/Rynaro/atlas-aci/issues/60
---
# ACI-S39 - serve the query engine over loopback Streamable HTTP

## Scope

Issue #60. Base `8ab6582e862d8c3bc29141104e58287bbee8f6be`: main after PR #104 (the security re-lock to mcp 1.30.0 and anyio 4.16.0), with S01-S38 merged except S15. Branch `aci-s39`.
- Marker `<!-- atlas-aci:slice:ACI-S39 -->` verified.
- Criteria digest `66c854ca2f103d9ac1edae2954db92de1ef03a9c5927969121538bad2b49d4e4` (6 product ACs + RUN-01/02).
- Risk **P0**. Timebox 3 engineering days. Rightsize tier **full** (score 6): independent checker and critic record.
- **Dependencies PRESENT:**
  - S12 (#34): `query/traversal.py` `navigate`, `IndexedReader.check_deadline`.
  - S13 (#35): `query/leases.py` `LeaseRegistry`; `codegraph.py` `reader_lease` with `finally: reader.close()`.
  - S38 (#59): `protocol/revisions.py` `negotiate`/`VERIFIED_SDK`, used at `server.py:42`.

Observed facts (verified at planning):
- **server.py.** :20 `from mcp.server.stdio import stdio_server`; :446 `dispatch_tool_call`; :499-535 per-request reader held by an `ExitStack`; :567 `build_server_state`; :600 `call_tool_result`; :683-744 `run_stdio` (handler closures :688-729, teardown :740-744). There is no `build_server` seam.
- **SDK mcp 1.30.0** (scratch `s39-sdk/`):
  - **Manager.** `StreamableHTTPSessionManager(app, event_store, json_response, stateless, security_settings, retry_interval, session_idle_timeout, max_request_body_size=4 MiB, max_sessions)` (:91-119). `asgi_app = RequestBodyLimitMiddleware` answers a plain-text 413 before parsing. `run()` can be entered once (:135-174).
  - **Stateless path** (:209-255): a fresh transport per POST; the server task starts in the manager task group; a shielded `terminate()` runs in `finally`.
  - **Transport** (`streamable_http.py`):
    - Order: security check (:450), then POST/GET/DELETE dispatch (:464-471).
    - POST: Accept 406 (:503), Content-Type 415 (:523), parse error 400 -32700 (:537), validation 400 -32602 (:544, so a batch array gives 400), protocol header 400 (:907-927; absent → `DEFAULT_NEGOTIATED_VERSION` "2025-03-26", `types.py:35`).
    - Notifications get 202 (:575-588). The SSE branch sends headers before dispatch (:668-683).
    - `ServerMessageMetadata(request_context=request)` (:283-289, :584): the handler sees the Starlette `Request` and its scope.
    - Error body shape (:336-365): `{"jsonrpc":"2.0","id":"server-error","error":{"code":..,"message":..}}`.
  - **`transport_security.py`.** Host mismatch → 421, Origin mismatch → 403, absent Origin passes (:74-78). Exact match plus a `host:*` wildcard.
  - **`Server.run`** (`lowlevel/server.py:679-696`) cancels in-flight handlers when the transport closes. A CancelledError without `message.cancelled` is re-raised (:779-790). The handler context carries `.request` (:741-775).
  - **sse_starlette 3.3.4.** `_listen_for_disconnect` (:331) handles client disconnects. Module import patches `uvicorn.Server.handle_exit` and keeps a process-global `AppStatus.should_exit` (:134-176).
  - **uvicorn 0.44.0.** `capture_signals` re-raises captured signals (:321-339). Bind failure → `sys.exit(1)`. `scope["server"]`/`scope["client"]` come from socket addresses (`h11_impl.py:98-99,208-209`).
- **Locked versions** (uv.lock): mcp 1.30.0, uvicorn 0.44.0 (mcp requires it with marker `sys_platform != 'emscripten'`), starlette 1.3.1, sse-starlette 3.3.4, anyio 4.16.0, h11 0.16.0, httpx 0.28.1. starlette is also a `[tool.uv] constraint-dependencies` floor (pyproject:57), not a direct dependency.
- **Pins:**
  - Tools JSON sha `94f9567a…ac56` (1631472 bytes); budgets 3436/3448/797/7730; `ev.RELATIONS`.
  - pyproject sha `981f0784…d92f` and uv.lock sha `87ba265e…46fd` (`test_s38_isolation.py:29-30`); the S38 tripwire pins 1.30.0.
  - CHANGELOG Unreleased: `### Security` first. Added bullets: [6] S38 (:133), [7] "OpenTelemetry trace import" (:148), [8] S26 Rails (:166).
  - README: :951 protocol row → :952 rails row → :953 SETUP.md.
  - `serve` must not gain `frameworks` or `rails_table_map` (`test_s24_isolation:191`, `test_s25_isolation:285,947`).
- **Pre-existing, stays untouched** (S40 territory):
  - `Enforcement.records` grows without bound (`enforcement.py:77,185`).
  - One process-wide sliding rate limiter, default 200/min (`enforcement.py:76,100-114`; `config.py:125`).
- **No thread offload exists.** Structured ops yield only at `await asyncio.sleep(0)` (`traversal.py:212,247`), the module's only asyncio uses. Legacy `graph_query`/`search_symbol`, `view_file` and `list_dir` run synchronously on the loop thread.
- **Environment.**
  - Dev image `atlas-aci-dev-relock` (mcp 1.30.0); `atlas-aci-dev-s16` is stale.
  - Production image: python 3.12 at `/usr/local/bin/python`, user `atlas`, `ENTRYPOINT ["atlas-aci"]` (Dockerfile:110), CMD stdio (:111); no `timeout` binary, no pytest.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-05)
1. **No dependency change.** pyproject.toml and uv.lock stay byte-identical. uvicorn, starlette and sse-starlette are used only as locked transitive dependencies of mcp. A tripwire asserts that `importlib.metadata.requires("mcp")` names all three.
2. **Handler reuse is scout option (b).**
   - `protocol/http.py` re-registers the same five handlers. Their bodies call `build_server_state`, `build_tool_manifest`, `call_tool_result`, `read_resource` and `RESOURCE_TEMPLATE`.
   - `session_version` = `negotiate(header or "2025-03-26").revision()`.
   - `server.py` is NOT edited. Drift is guarded by parity tests.
3. **SDK mode:** stateless, `json_response=False` (SSE), no event store.
   - Disconnect = cancellation is a documented deviation from the spec. Its wording is verified at Day-1 (k).
   - Stateless `notifications/cancelled` is a no-op (documented).
4. **Outer raw-ASGI wrapper** before the SDK reads a body: Host 421 → Origin 403 → path `/mcp` 404 → POST-only 405 → in-flight gate 503 with Retry-After. Then the SDK manager with `max_request_body_size` (413) and matching `TransportSecuritySettings`. `uvicorn.Config(lifespan="off", proxy_headers=False, log_config=None, access_log=False, server_header=False)`.
5. **Loopback:** pure `resolve_loopback`, bind the literal IP, enforced at the CLI (UsageError, exit 2) and again in `run_http`. No public-bind option.
6. **Config/CLI:** new `Config` fields with defaults, validated in `__post_init__`. `serve --transport [stdio|http] --host --port --max-inflight --max-body-bytes --allowed-origins`. The `protocol.http` import is lazy. No `frameworks` or `rails_table_map` parameter.
7. **Cancellation reach.** Resolved at planning to the **amend** branch (see Approach, D9): both tools await an async subprocess.
8. **`Enforcement.records` and the shared rate limiter** are documented S40 limitations; `enforcement.py` is untouched.
9. **Tests:** in-loop uvicorn on port 0 with a real loopback bind; shutdown via `should_exit` only; `traversal.asyncio = SimpleNamespace(sleep=gate)`; `streamable_http_client` with a pinned revision; extra cases as listed.
10. **Docs and pins:**
    - new `hosts/http-transport.md`;
    - minimal edits to `hosts/protocol-compatibility.md`, lines 9 and 158;
    - a CHANGELOG bullet at Added index **8**;
    - a README row after the rails row;
    - a scope amendment.
11. **Invariance:** the tools pin, budgets, `ev.RELATIONS` and producer inputs; `server.py` byte-identical; the S38 goldens and tripwire; no event store, session files or temp files.
12. **Dev image** `atlas-aci-dev-relock`.
13. **Production scenario** runs inside the production image with `--network none`, as specified below.

### In
1. `mcp-server/src/atlas_aci/protocol/http.py` (new; D1-D8).
2. `config.py`: five appended fields and their validation (D6).
3. `__main__.py`: `serve` options, the usage errors and the lazy HTTP branch (D7).
4. `tools/search_text.py` and `tools/test_dry_run.py`: an additions-only kill on cancellation (D9; scope amendment).
5. Tests:
   - `test_s39_contract.py` (the 6 ACs);
   - `test_s39_support.py` (stdlib-only top level: raw async HTTP client, SSE parser, in-loop harness with lazy imports, production driver; no test functions);
   - `test_s39_transport.py`, `test_s39_loopback.py`, `test_s39_cancellation.py`, `test_s39_isolation.py`.
6. Docs: `hosts/http-transport.md` (new), `hosts/protocol-compatibility.md` (2 lines), the CHANGELOG bullet, the README row.
7. Evidence under `.spectra/evidence/aci-s39/`: qualification.json, base-invariants.json, preserve/{base,after}, the spike logs, production-scenario.sh and .json, py314.log, gate logs.

### Out
- Public or non-loopback bind and any `--public` flag; TLS; authentication or authorization (S40).
- Stateful sessions, the standalone GET stream, DELETE, resumability and event store, batching, websockets, CORS preflight.
- Edits to `server.py`, `enforcement.py`, `codegraph.py`, `query/*`, `storage/*`, other `tools/*`, `protocol/{__init__,graph,resources,results,context,impact,revisions}.py`, the Dockerfile (CMD stays stdio), workflows, pyproject or uv.lock.
- Thread offload of synchronous ops. Per-client rate limits. Bounding `Enforcement.records`.

## Approach

### Decisions

**D1 Module surface** (`protocol/http.py`). It is the only module that imports uvicorn.
```python
ENDPOINT_PATH = "/mcp"; RETRY_AFTER_SECONDS = 1; SHUTDOWN_GRACE_S = 5; KEEP_ALIVE_S = 5
TICKET_SCOPE_KEY = "atlas_aci.inflight_ticket"
REJECTION_REASONS = ("peer_not_loopback", "host_not_allowed", "origin_not_allowed",
                     "path_not_found", "method_not_allowed", "inflight_limit")
LOOPBACK_REASONS = ("empty_host", "wildcard_address", "scope_id", "unresolvable",
                    "non_loopback_address", "mixed_resolution", "bound_non_loopback")
class LoopbackError(ValueError): reason: str          # str(exc) is fixed text naming the reason
def resolve_loopback(host: str) -> str                # pure apart from _getaddrinfo (D5)
class RequestGone(RuntimeError): ...
class Ticket: hold() -> None; release() -> None; released: bool
class InflightGate: limit; active; peak; rejected; admitted; try_admit() -> Ticket | None; snapshot() -> dict
class AtlasHttpApp:  # ASGI3 callable
    config; gate: InflightGate; server: mcp.server.lowlevel.Server; manager; state; owns_state: bool
    async def __call__(self, scope, receive, send) -> None
    def running(self) -> AbstractAsyncContextManager[None]  # manager.run(); teardown if owns_state
def build_app(config: Config, state: tuple[Enforcement, Memex, CodeGraph] | None = None) -> AtlasHttpApp
class AtlasUvicornServer(uvicorn.Server): __init__(self, config, *, install_signals: bool = True)
def uvicorn_config(app: AtlasHttpApp, host_ip: str, port: int) -> uvicorn.Config
async def run_http(config: Config, *, on_listening: Callable[[list[tuple[str, int]]], None] | None = None,
                   install_signals: bool = True) -> None
```
- **Import allow-list:** `__future__`, `asyncio`, `contextlib`, `ipaddress`, `json`, `signal`, `socket`, `collections.abc`, `typing`, `structlog`, `uvicorn`, `mcp.types`, `mcp.server.lowlevel`, `mcp.server.streamable_http_manager`, `mcp.server.transport_security`, `atlas_aci.config`, `atlas_aci.server` (only `build_server_state`, `build_tool_manifest`, `call_tool_result`), `atlas_aci.protocol.resources`, `atlas_aci.protocol.revisions`, `atlas_aci.query.leases`.
- No starlette routing.
- None of `open`, `tempfile`, `os`, `subprocess`, `sqlite3`, `print`.
- No harden markers.
- The request path performs no logging and no I/O beyond the ASGI send. Only `run_http` logs, and only twice: `http_listening` and `http_stopped`.

**D2 Wrapper** (`AtlasHttpApp.__call__`).
- A closed stage order. It runs on the scope only, and `receive()` is never called before stage 6 passes.
- A non-`http` scope raises RuntimeError; it is unreachable with `ws="none"` and `lifespan="off"`.

| # | stage | rejected when | status | extra headers | reason | JSON-RPC code |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | peer | `scope["client"]` missing or not a loopback IP | 403 | - | peer_not_loopback | -32600 |
| 1 | host | Host missing, or (lower-cased) not exactly `name:P`, where P = `scope["server"][1]` and name ∈ {bound literal (IPv6 bracketed), `localhost`, the operator hostname if one was resolved} | 421 | - | host_not_allowed | -32600 |
| 2 | origin | Origin present and not exactly in `http_allowed_origins` (`null` always rejected) | 403 | - | origin_not_allowed | -32600 |
| 3 | path | `scope["path"] != "/mcp"` | 404 | - | path_not_found | -32600 |
| 4 | method | method != POST (GET, DELETE, OPTIONS, ...) | 405 | `Allow: POST` | method_not_allowed | -32600 |
| 5 | admission | `gate.try_admit()` is None | 503 | `Retry-After: 1` | inflight_limit | -32000 |
| 6 | SDK | `manager.handle_request` with `scope[TICKET_SCOPE_KEY] = ticket`; SDK-owned results: 413 (body > `http_max_body_bytes`, plain text), 406, 415, 400 (-32700 parse, -32602 validation/batch, unsupported `mcp-protocol-version`) | - | - | - | - |

- **Rejection body:** `{"jsonrpc":"2.0","id":"server-error","error":{"code":C,"message":M,"data":{"reason":R}}}`. M is fixed text per reason; no header value is ever echoed. Headers: `content-type: application/json`, `connection: close`.
- **Stage 6:** `try: await manager.handle_request(...) finally: ticket.release()` (the wrapper's hold).
- **SDK `TransportSecuritySettings`:**
  - `enable_dns_rebinding_protection=True`;
  - `allowed_hosts` = `[f"{n}:*" for n in names]`;
  - `allowed_origins` = `list(http_allowed_origins)`.

  The wrapper stays authoritative.

**D3 Handlers** (`build_app`). `Server("atlas-aci")` gets exactly the stdio handler set. Bodies call the shared functions:
- `list_tools` → `[Tool(**t) for t in build_tool_manifest(config, session_version())]`;
- `call_tool(validate_input=False)` → `call_tool_result(..., protocol_version=session_version())`;
- `list_resources` → `[]`;
- `list_resource_templates` → the same `ResourceTemplate(name="ATLAS leased source span", uriTemplate=RESOURCE_TEMPLATE, mimeType="application/octet-stream")`;
- `read_resource` → the same registry-or-create and `read_resource(...)`.

Each body runs inside `_admitted()`:
- it fetches the ticket from `server.request_context.request.scope[TICKET_SCOPE_KEY]` and calls `ticket.hold()`;
- in `finally` it calls `ticket.release()`;
- a missing request or ticket → RuntimeError (fail closed).

`session_version()`:
- `request is None` → RuntimeError;
- otherwise `negotiate(request.headers.get("mcp-protocol-version", types.DEFAULT_NEGOTIATED_VERSION)).revision()`.

The SDK already rejects unsupported header values with 400 for non-initialize requests, and `initialize` reaches no Atlas handler. Consequence (documented): a client that omits the header is shaped as 2025-03-26 (legacy).

**D4 Gate and ticket.**
- **Admission.** `try_admit()` is synchronous (no await, so it is atomic on the loop). If `active >= limit`: `rejected += 1`, return None. Otherwise: `active += 1`, `admitted += 1`, `peak = max(peak, active)`, and return `Ticket(holds=1)`.
- **Holds.** `hold()` raises `RequestGone` if the ticket is already released, so engine work never starts without a slot; otherwise `holds += 1`.
- **Release.** `release()` decrements `holds`. At 0 it marks the ticket released and runs `gate.active -= 1` exactly once.
- **Effect.** The slot lives until both the HTTP exchange and every Atlas handler for that POST have ended, including the cancellation unwind after a disconnect. Excess requests are rejected, never queued.
- **`snapshot()`** = `{limit, active, peak, admitted, rejected}`.

**D5 Loopback resolution** (`resolve_loopback`). The resolver is the module attribute `_getaddrinfo = socket.getaddrinfo`, which tests patch; the global socket module is never patched.
1. Non-str, `""`, whitespace, or longer than 253 characters → `empty_host` (for `""`) or `unresolvable`.
2. Contains `%` → `scope_id`.
3. Strip one pair of `[]`.
4. **IP literal:** unwrap `ipv4_mapped`. `is_unspecified` → `wildcard_address`. Not `is_loopback` → `non_loopback_address`. Otherwise return the canonical `str(addr)`.
5. **Hostname:** `_getaddrinfo(host, None, type=SOCK_STREAM)`.
   - An error or an empty result → `unresolvable`.
   - Each result: scope id → `scope_id`; unwrap mapped; unspecified → `wildcard_address`.
   - A non-loopback result → `mixed_resolution` if any loopback result exists, else `non_loopback_address`.
   - Otherwise return the first IPv4 result, else the first IPv6 result.
6. The caller binds only that literal, never the name.
- `run_http` re-resolves, so a DNS change between the CLI check and the bind fails closed with no bind.
- After bind, every listening `getsockname()` must be loopback; otherwise shut down and raise `bound_non_loopback`.

**D6 Config fields.** Appended last, so positional `Config(repo, memex_root)` is unchanged. Validated in `__post_init__` before the memex mkdir, with no DNS. Messages are fixed text naming the field.

| field | type | default | bound |
| --- | --- | --- | --- |
| `http_host` | str | `"127.0.0.1"` | 1..253 chars, no whitespace or `%`; if it is an IP literal (brackets and mapped form unwrapped) it must be loopback |
| `http_port` | int (not bool) | 8765 | 0..65535 (0 = ephemeral, reported by `http_listening`) |
| `http_max_inflight` | int | 8 | 1..256 |
| `http_max_body_bytes` | int | 1048576 | 1024..16777216 |
| `http_allowed_origins` | tuple[str, ...] | `()` | ≤ 32 entries, unique, each matches `^https?://([a-z0-9.-]+\|\[[0-9a-f:]+\])(:[1-9][0-9]{0,4})?$` with port ≤ 65535; `*` and `null` rejected; lists are coerced to a tuple |

**D7 CLI** (`serve`).
- **Added options:**
  - `--transport` `Choice(["stdio","http"])`, default `stdio`;
  - `--host` str, default None;
  - `--port` `IntRange(0,65535)`;
  - `--max-inflight` `IntRange(1,256)`;
  - `--max-body-bytes` `IntRange(1024,16777216)`;
  - `--allowed-origins` (comma-separated).
- The docstring becomes "Start the MCP server over stdio (default) or loopback Streamable HTTP."
- **Usage errors** (exit 2, nothing bound, nothing on stdout):
  - (U1) any HTTP option with stdio: "--host, --port, --max-inflight, --max-body-bytes and --allowed-origins require --transport http".
  - (U2) `resolve_loopback` fails: "--host must resolve only to loopback addresses (reason: R); a non-loopback bind needs S40 shared mode, which is not available".
  - (U3) a Config ValueError from an HTTP field → its fixed message.
- **HTTP path:**
  - `Config(..., http_host=<resolved literal>, ...)`, passing only the options actually given;
  - `validate_configuration` (unchanged);
  - `from atlas_aci.protocol.http import LoopbackError, run_http` inside the branch;
  - `asyncio.run(run_http(config))`.
  - A `LoopbackError` → UsageError (U2); `(ValueError, OSError)` → ClickException exactly as stdio.
- **Stdio path** is byte-identical behaviour: the same `run_stdio` call and the same error mapping.
- A port already in use → uvicorn `SystemExit(1)` (exit 1, uvicorn's message on stderr; documented).

**D8 run_http lifecycle and signals.**
- **Startup:**
  - `ip = resolve_loopback(config.http_host)`;
  - `app = build_app(config)`;
  - `server = AtlasUvicornServer(uvicorn_config(app, ip, port), install_signals=...)`.
- **`uvicorn_config`:** `host=ip, port=port, lifespan="off", proxy_headers=False, server_header=False, log_config=None, access_log=False, ws="none", http="h11", loop="asyncio", timeout_graceful_shutdown=5, timeout_keep_alive=5`.
- **Serving:** `async with app.running():` start a `server.serve()` task and poll `server.started` every 10 ms. If the task finishes first, await it (this propagates SystemExit). Then run the post-bind loopback check (D5), log `http_listening host= port= path=/mcp max_inflight= max_body_bytes=`, call `on_listening(addresses)`, and await the task.
- **Teardown:** `running()` mirrors `server.py:740-744` (`registry.aclose()`, `code_graph.close()`) when it owns the state; then `http_stopped admitted= rejected= peak=` is logged.
- **`capture_signals` override:** `install_signals=False` → no-op. Otherwise it registers `loop.add_signal_handler(sig, self.handle_exit, sig, None)` for SIGINT and SIGTERM and removes them on exit, **never re-raising**.
  - `handle_exit` resolves to sse_starlette's class-level wrapper, so SSE streams drain and uvicorn's `should_exit` is set.
  - SIGTERM → graceful stop of at most 5 s, teardown, exit 0.

**D9 Cancellation reach** (decision 7, amend branch).
- **Finding.** `search_text.py:61-75` and `test_dry_run.py:66-85` await `asyncio.create_subprocess_exec` and kill the child only in `except TimeoutError`.
- **Fix** (additions only, no deleted line). Add `import contextlib`, plus after the TimeoutError clause:
  `except BaseException: if proc.returncode is None: with contextlib.suppress(ProcessLookupError): proc.kill(); raise`.
  There is no await in the cancel path: anyio re-delivers cancellation on each await, and the asyncio child watcher reaps the process.
- **Unchanged:** the TimeoutError path; immediate-child only (the `test_dry_run` docstring already disclaims descendants).
- **Documented, not changed:**
  - Legacy `graph_query`/`search_symbol`, `view_file` and `list_dir` are synchronous, so they are uncancellable mid-call. They are bounded by their own caps (SQL `query_limit`, line, entry and match caps, and `deadline_ms` where the op has one), and they block the loop for every HTTP client while running.
  - Structured ops are cancellable at `traversal.py:212,247`, `retrieval.py:361,381`, `context.py:245`, `impact.py:607` and `paging.py:159,194`.

**D10 Docs.** `hosts/http-transport.md` (ASCII; none of {certified, guaranteed, proven, seamless, universal}):
1. Summary.
2. Starting it: the option table with defaults and bounds, the `http_listening` line, port 0.
3. The loopback rule: D5 steps, every `LOOPBACK_REASONS`, the U2 text, binding the literal.
4. The admission table: D2 with every `REJECTION_REASONS`, status, header and body example; the SDK-owned statuses 413/406/415/400.
5. Protocol revision over HTTP: header shaping, absent → 2025-03-26, equivalence with stdio tested.
6. Disconnect and cancellation:
   - the spec sentence (exact quote with commit, or labelled "paraphrased; not verified");
   - the Atlas deviation: disconnect = cancel;
   - reader lease release and subprocess kill;
   - `notifications/cancelled` a no-op;
   - synchronous ops uncancellable.
7. Concurrency and bounds: the ticket semantics, `Retry-After: 1`, body cap, keep-alive 5 s, graceful shutdown 5 s, h11 header-size default.
8. Limitations (S40): `Enforcement.records` (`enforcement.py:77,185`), the shared limiter (`enforcement.py:76,100-114`), slow-body slots, loop-blocking sync ops, port in use → exit 1.
9. Not supported: public bind, TLS, auth, sessions, GET stream, DELETE, resumability, batching (400), websockets, OPTIONS (405).
10. Verified with: the stdlib raw HTTP fixture client and the mcp 1.30.0 `streamable_http_client`.
11. Real hosts: one line each, "Claude Code / GitHub Copilot / Cursor: untested; negotiated revision not verified".
12. Read-only over HTTP: no event store, session files or temp files; the body is held in memory.

`hosts/protocol-compatibility.md`, keeping every S38-pinned token:
- **:9** → "- Transport: this page covers stdio (`atlas-aci serve`, the default). An operator-enabled, loopback-only Streamable HTTP transport with SSE responses (`atlas-aci serve --transport http`) is described in `hosts/http-transport.md`; the goldens and fixture hosts on this page are stdio only."
- **:158** → "- HTTP and SSE beyond the loopback Streamable HTTP transport of `hosts/http-transport.md`: no public bind, no sessions, no standalone GET stream, no resumability."

**CHANGELOG** (Added index 8, after the OTel bullet, before S26):
- one line starting `- **Loopback Streamable HTTP transport (ACI-S39).**`;
- continuation lines indented, never starting `- **`;
- it names `hosts/http-transport.md`, "loopback only", "disconnect cancels" and "S40";
- it carries no "(ACI-S38)" and no prior locator.

**README** (directly after the `docs/frameworks/rails.md` row, before SETUP.md): `| [`hosts/http-transport.md`](hosts/http-transport.md) | <one cell, no pipes> |`, exactly 3 pipes, with no `protocol-compatibility.md` and no `canary-missions.md`.

**D11 Test harness and in-process hazards** (`test_s39_support.py`; atlas, uvicorn and httpx imported lazily).
- **H1 serving.** `serving(app)`: an async context manager that runs `AtlasUvicornServer(uvicorn_config(app, "127.0.0.1", 0), install_signals=False).serve()` as a task in the test loop and yields `(host, port)` read from `servers[0].sockets[0].getsockname()`.
  - Exit: assert the in-flight gate shows `active == 0`, set `should_exit = True`, and await the task with a 15 s `asyncio.timeout`. Never a signal.
  - The fixture asserts and resets `sse_starlette.sse.AppStatus.should_exit is False` on entry. A fresh `build_app` is used per test (`manager.run` is once-only).
- **H2 gate.** The traversal gate is `monkeypatch.setattr(traversal, "asyncio", SimpleNamespace(sleep=gate))`, never the global asyncio module.
  - `gate` counts `inside`/`max_inside`.
  - The first call per task blocks on `release`; others call the captured real `asyncio.sleep`.
  - It records `cancelled` on CancelledError and re-raises.
  - An AST test asserts that `traversal` uses only `asyncio.sleep`.
- **H3 lease observer.** A class-level `CodeGraph.reader_lease` wrapper (contextmanager) captures `reader`, `reader.db` and `reader._source_lease` and keeps `open_leases`/`max_open_leases` (S12 precedent, `test_s12_admission.py:119-163`).
- **H4 client.** Only async clients in the loop:
  - `RawHttp` (asyncio streams; HTTP/1.1, `Connection: close`, chunked decoding, SSE `data:` parsing, `withhold_body=True` sends headers with a Content-Length and no body, `open_stream()` returns after the response headers for disconnect tests);
  - httpx through the SDK client.

  A blocking stdlib client is never called on the loop.
- **H5 fixtures.** Gated ops are structured (`neighborhood`/`path`), never legacy synchronous ops. Fixtures are S38 `FIXTURE_SOURCE`/`LIVE_SOURCE` via `build_fixture`.
- **H6 rate limiter.** `max_calls_per_minute=0` in gate, concurrency and replay tests.
- **H7 timeouts.** Every wait is bounded (`asyncio.timeout` 10-30 s). Subprocess servers are killed in `finally`.
- **H8 subprocess servers.** The `serve --transport http --port 0` child's port is parsed from stderr `\bport=(\d+)` on the `http_listening` line.

### Work plan (timebox 3 engineering days)
**Day 1 AM: qualification**, before any src edit. Outputs: `.spectra/evidence/aci-s39/qualification.json` and `base-invariants.json`. Spikes live in scratch and are logged.
- (a) `git diff --quiet 8ab6582 -- mcp-server/src`. Record:
  - versions: mcp 1.30.0, uvicorn 0.44.0, starlette 1.3.1, sse-starlette 3.3.4, anyio 4.16.0, h11 0.16.0, httpx 0.28.1;
  - `requires("mcp")` names uvicorn, starlette and sse-starlette;
  - the pyproject/uv.lock shas;
  - `SERVER_PY_SHA` = sha256 of `git show 8ab6582:mcp-server/src/atlas_aci/server.py`.
- (b) Base green: G-REG files, the tools pin, the budgets.
- (c) The S38 goldens are green at base (`test_s38_contract.py`). Base preserve capture: `git worktree add <scratch>/s39-base 8ab6582`, then `uv run --frozen --project <base>/mcp-server python <base>/mcp-server/tests/slices/test_s38_support.py capture --out preserve/base --fixture F --label base` run twice (seeds 0 and 4242) and compared with `cmp`.
- (d) A scratch raw-ASGI app on uvicorn `127.0.0.1:0` in-loop serves a POST in the dev image and in `python:3.14`. Record `scope["server"]`/`scope["client"]`.
- (e) A stateless SSE manager with a lowlevel Server whose `call_tool` awaits an Event; the client closes TCP after the headers.
  - The handler sees CancelledError within 2 s (record the latency); a real `reader_lease` inside it is closed.
  - Control: with `json_response=True` the handler is NOT cancelled. This records why the mode was chosen.
- (f) Inside that handler, `request_context.request.scope is <wrapper scope>`. Record whether the handler's unwind finishes after the wrapper's `finally` (the window behind D4).
- (g) Over the spike, tools/list with header R == `build_tool_manifest(config, R)` canonical, and == the S38 stdio `tools_digest` for all 4 R. An absent header gives the 2025-03-26 manifest.
- (h) Re-confirm D9 at base: cancel `search_text` with a fake `rg` (`echo $$ > pidfile; exec sleep 60`); the child is still alive 2 s later.
- (i) A scratch `uvicorn.Server.serve()` child under SIGTERM exits by the re-raised signal at base. The `capture_signals` override exits 0 with teardown.
- (j) Lazy-import baseline: the sorted third-party module set after `import atlas_aci.__main__` and after `import atlas_aci.server`.
- (k) Spec text:
  - Fetch `docs/specification/2025-11-25/basic/transports.mdx` at commit `3c4d91b7e832d9b74338d56eed03403ccc662123` using `docker run --rm curlimages/curl` (1 h timebox).
  - Record the exact sentences on disconnect versus cancellation, Origin validation and localhost binding.
  - Fallback: label them "paraphrased; not verified against the published text".
- (l) On LIVE: neighborhood `{seed:{name:"A"}, max_visited_nodes:1, deadline_ms:30000}` in-process. Record `stop_reason` (expected `visited_node_limit`) and that two runs give equal `frontier.commitment`.
- (m) Full suite at base with the known deselects; record pass/skip counts.

**STOP RULE** (return to the orchestrator; never adapt silently):
- (a) any version or sha mismatch, or `requires("mcp")` missing a name;
- (b)/(c) anything red at base, or the double capture differs;
- (d) uvicorn cannot bind and serve in-process in the dev image or on 3.14;
- (e) disconnect does not cancel the gated handler, or the reader is not closed;
- (f) scope identity is false;
- (g) any header-derived manifest ≠ stdio shaping;
- (l) the stop is `deadline` or the commitments differ.

**Recorded adaptations, not stops:**
- (h) if the base already kills the child → drop D9 and the amendment entries for `tools/*`;
- (i) if the override cannot give exit 0 → keep uvicorn's default, and the docs and G-PROD E7 assert "teardown logged; exit by SIGTERM (143)";
- (k) fetch failure → the label above;
- (c) if `_inprocess_dump` serialises Config fields → compare with the new keys excluded and record it.

**Day 1 checkpoint:** qualification.json is committed with a clock reading.
**Day 1 PM:** `http.py` (D1-D8), the Config fields, the CLI, the D9 tools fix; `test_s39_loopback.py` and the gate/ticket unit tests.
**Day 2:** the 6 AC tests, `test_s39_transport.py`, `test_s39_cancellation.py`. Record the cut decision at Day-2 end with a clock reading.
**Day 3:** `test_s39_isolation.py`, docs, CHANGELOG, README, gates, py314, the production scenario, RUN-01/02.

**Cut order** (each cut recorded in the receipt):
1. The `notifications/cancelled` no-op test.
2. The AC-003 `page` (registry) variant.
3. AC-006 at the 2024-11-05 header.
4. The SDK-client host in AC-001 (the raw HTTP host stays).
5. The bind-conflict exit-1 test.
6. The AC-002 non-loopback-interface connect probe (`getsockname` stays).
7. `test_dry_run` cancellation over HTTP (the direct test stays).
8. The README row.

**Never cut:**
- the 6 AC tests and the full D2 rejection matrix, including 405/413/400/421/404/503;
- the loopback matrix, CLI exit 2 and the race case;
- ticket refcounting;
- handler parity;
- the D9 fix with its direct tests;
- the isolation pins;
- G-SRC, G-MCP, G-STDIO-PRESERVE, G-PROD;
- the docs deviation and limitation sections.

### Independent checker conditions
Verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s39-plan-checker`, independent read-only review with the mcp 1.30.0, uvicorn 0.44.0, starlette and sse_starlette source; 7 MAJOR, 9 MINOR, no BLOCKER). Ramza's complexity verdict was `human_loop`; under the user's autonomous-execution authorisation this review is the substitute human gate. Every finding is accepted as a binding plan condition with the same number; where a condition conflicts with plan text above, the condition wins.

Orchestrator resolutions (stricter option chosen each time):
- Condition 7: fail closed. `serve --transport http` and `run_http` exit 2 with a fixed usage error when `config.test_execution_enabled` is true; in-process `build_app` tests may enable it. The docs also state the trust boundary (any local process or user can reach the endpoint) and that `--network host` makes loopback the host's loopback.
- Condition 8 (port 80): `Config` rejects `http_port` in 1-1023 (0 stays allowed for an ephemeral port), so the bare-Host case cannot lock clients out; the wrapper keeps requiring `host:port`.
- Condition 11: fail closed. `run_http` exits 2 with fixed text when `importlib.metadata.version("mcp") != VERIFIED_SDK[1]`, tested with patched metadata.
- Condition 15: `Config` caps `http_max_inflight * http_max_body_bytes` at 256 MiB (usage error above it); the other gaps are documented.
- Condition 16: if any never-cut item is not green at the Day-2 clock reading, stop and return to the orchestrator with the state; do not extend.
- Conditions 1-7 are never cut.

**Findings**

1. **MAJOR: D8 mis-states how bind failure surfaces.**
   - Evidence: `Server.startup` does `sys.exit(1)` inside the `serve()` task (uvicorn/server.py:179-182). asyncio's `Task.__step` re-raises `SystemExit` out of `run_forever`, not at `await task`. In-process (pytest or API use) it crashes the loop and the test, and in the CLI it skips the orderly unwind.
   - Condition: wrap the serve task body as `try: await server.serve() except SystemExit as e: raise OSError(...)`, so `run_http` raises an ordinary exception. Map it to ClickException exit 1, stderr text fixed. The cut-5 bind-conflict test must be a subprocess test or use this conversion.

2. **MAJOR: the SIGTERM override has no automated test outside G-PROD E7.**
   - Evidence: the 12-line `capture_signals` replacement is checked only by E7 in the production gate. CI (3.14, ubuntu) never exercises it, and the in-process tests use `install_signals=False`.
   - Condition, never cut:
     - A subprocess test of `serve --transport http --port 0` that parses the port, sends SIGTERM, and asserts exit 0 within 10 s plus `http_stopped`, with no Traceback and a refused connection afterwards. It can be the tail of AC-001's HTTP child.
     - A second signal must be able to terminate the process. After the first signal, remove the handler (`loop.remove_signal_handler`) so a repeat gets the default disposition. Otherwise a hung teardown makes SIGTERM useless.
     - Guard the install with `threading.main_thread()`, as uvicorn does.
     - The tripwire must also assert `AppStatus.original_handler is not None`. uvicorn's own `_get_uvicorn_server()` fallback finds no server because the plan uses `add_signal_handler`, so drain depends on the patched `handle_exit`.

3. **MAJOR: E9 literal is wrong.**
   - Evidence: port 18765 is `0x494D`, so the entry is `0100007F:494D`. The plan's `0100007F:4983` is port 18819, which would fail G-PROD deterministically.
   - Condition: compute the expected row as `"0100007F:%04X" % port`. Require exactly one LISTEN row on `/proc/net/tcp` for that port and none on `tcp6`.

4. **MAJOR: the lazy-import probe is Python-version fragile.**
   - Evidence: Day-1 (j) records an equal-set baseline on the 3.12 dev image, but CI is 3.14. Third-party transitive modules differ across versions.
   - Also: `mcp/server/__init__.py:1` imports FastMCP, so `import atlas_aci.server` already loads uvicorn, starlette and sse_starlette in stdio. The lazy import only guards `__main__`, not those libraries once `server` is imported.
   - Condition:
     - In a fresh interpreter after `import atlas_aci.__main__`, assert `atlas_aci.protocol.http`, `uvicorn`, `starlette`, `sse_starlette` and `atlas_aci.server` are absent. Do not compare a pinned full module set.
     - Record the uvicorn-already-loaded fact in the docs.

5. **MAJOR: test-determinism rules missing.**
   - (a) The H1 fixture asserts `gate.active == 0` on exit, and AC-004 "Final" asserts it right after completion.
     - The wrapper's `finally` runs a few loop turns after the client sees the last chunk.
     - Both must poll with a 5 s bound, and should poll `snapshot()`.
   - (b) For the 413 cases, body writes can hit RST. The server closes with unread bytes (`Connection: close`, flow control pauses reading above 64 KiB).
     - Use `http_max_body_bytes=1024` in the matrix.
     - Read the response concurrently with the write.
     - Tolerate `ConnectionResetError`/`BrokenPipe` on write only for these cases.
     - E4 at 65537 needs the same tolerance.
   - (c) The SSE parser must handle CRLF, ignore `:` comment lines (ping every 15 s), and accept `event:`/`id:` fields.
   - (d) `writer.close()` followed by `wait_closed()` must swallow reset errors.

6. **MAJOR: the ticket accounting needs atomicity rules written down.**
   - Evidence: the handler body starts after the wrapper may already have released (manager:233-255, lowlevel/server.py:684). A `RequestGone` there is a normal outcome, and `call_tool` turns it into an `isError` result (lowlevel/server.py:589).
   - Condition:
     - `_admitted` is `hold(); try: yield; finally: release()` with no await between `hold()` and the `try`.
     - In the wrapper, `scope[KEY]=ticket` goes inside the `try` that has the `finally`.
     - `release()` after the ticket is released is a no-op and never double-decrements.
     - Nested holds (`call_tool` → cache-refresh `list_tools`, lowlevel/server.py:482-490) balance.
     - Unit tests cover cancel-before-first-step, `RequestGone`, and nested hold.
     - Document that `RequestGone` surfaces as an `isError` result, not a transport error.

7. **MAJOR: unauthenticated local reach, including `test_dry_run`, is not addressed.**
   - Evidence: `test_execution_enabled` is env-driven (config.py:88). With `--transport http` any local process or user can invoke every tool, including repo test execution. In stdio only the spawning host could.
   - Condition, choose one:
     - (preferred) `serve --transport http` and `run_http` fail closed with a usage error (exit 2) when `config.test_execution_enabled`. In-process `build_app` tests may still enable it.
     - Or document the trust boundary ("any local process or user can reach the endpoint; with test execution enabled that is local code execution"), and add `test_execution=enabled|disabled` to the `http_listening` log line.
     - Either way, the docs say that `--network host` makes "loopback" the host's loopback.

8. **MINOR: loopback and Host/Origin hardening details.**
   - `resolve_loopback` must catch `(OSError, UnicodeError, ValueError)` around `getaddrinfo` (labels over 63 characters and `\x00` raise ValueError subclasses, not `gaierror`). Map them to `unresolvable`, and test both.
   - Scope-id detection on hostname results must check both `"%"` in `sockaddr[0]` and a non-zero `sockaddr[3]`.
   - `uvicorn_config` itself asserts `ip_address(host_ip).is_loopback`. It is public and currently unguarded. It must also set `workers=1` (env `WEB_CONCURRENCY` otherwise feeds `Config`) and assert `fd` and `uds` are None.
   - Wrapper Host and Origin: reject duplicate Host or Origin header entries. Compare Host lower-cased, but test `LOCALHOST:P` and record the status, because the SDK's check is case-sensitive and answers 421 in plain text. An empty `Origin:` is a 403 in the wrapper.
   - A bare Host (no port) is rejected, so `--port 80` locks out every legitimate client. Either accept the bare name only when P == 80, or reject port 80 in Config.

9. **MINOR: uvicorn `Config.load` calls `loaded_app()` with no arguments** (uvicorn/config.py:446-456).
   - It tolerates only `TypeError`. `AtlasHttpApp.__call__` must have exactly three required positional parameters and no defaults, with a unit test.
   - Pass `interface="asgi3"` explicitly in `uvicorn_config`.

10. **MINOR: `run_http` lifecycle leaks.**
    - Put the serve task in a `try/finally` that sets `should_exit` and awaits it with a timeout, then cancels it, so an exception in `on_listening` or a cancellation cannot orphan it.
    - Guarantee that `code_graph.close()` and `registry.aclose()` run on every path, including a failure between `build_app` and `running()`.

11. **MINOR: SDK floor.**
    - The pyproject floor stays `mcp>=1.28.1`, so a pip consumer on an older SDK could lack `max_request_body_size` or `max_sessions`.
    - Fail closed at `run_http` if `importlib.metadata.version("mcp") != VERIFIED_SDK` (exit 2, fixed text), or document it, and test with patched metadata.

12. **MINOR: `http.py` allow-list.**
    - Annotations need `atlas_aci.enforcement`, `atlas_aci.memex` and `atlas_aci.codegraph` under `TYPE_CHECKING`. They are not in the D1 list, so the AST test would fail.
    - ASGI aliases come from `collections.abc`, not starlette.
    - Condition: extend the allow-list for TYPE_CHECKING-only imports and record it.

13. **MINOR: AC-002 probe.**
    - The probe that requires `checked == true` depends on a non-loopback interface and fails under `--network none` or offline laptops.
    - Add a Linux-only deterministic probe: connecting to `127.0.0.2:port` must be refused while bound to `127.0.0.1` (proves not wildcard, works with no network).
    - Gate the "checked must be true" assertion behind an env var that the gate sets.

14. **MINOR: parity and AC-006 and AC-005.**
    - Add one absent-header `tools/call` equality against the stdio 2025-03-26 shaping, not only `tools/list`. The doc already admits the legacy fallback for a client that negotiated 2025-11-25 but sends no header, so that path is worth one real result comparison.
    - Day-1 (l) must record byte-equality of the whole SSE body across two runs, not only the commitment.
    - AC-005 controls must also assert the `manager.handle_request` spy count is 1, so that spy is non-vacuous.

15. **MINOR: documented gaps.**
    - Slow-read clients hold slots without bound (no write timeout). Slow headers hold fds.
    - SDK `logger.exception` writes tracebacks to stderr on a mid-body client drop (stderr scans must be scoped).
    - The "no logging on the request path" claim covers Atlas only.
    - `http_listening` is info-level.
    - `max_inflight × max_body_bytes` can reach 4 GiB at the config maxima. Document, or cap the product.
    - `test_dry_run` kills only the immediate child on cancel.
    - The `uvicorn` import is undeclared-direct (already a decision).

16. **MINOR: timebox.**
    - Day-1 AM (a)-(m) is too heavy, and the Day-2 never-cut set leaves the cut list almost no lever.
    - Condition: if any never-cut item is not green at the Day-2 clock reading, return to the orchestrator rather than extend.
    - Move the check that `uvicorn` imports in the production image from Day 3 into Day-1 (a).

**Stdio, resources and rollback**
- stdio is unchanged: `server.py` is untouched, the default `--transport stdio` path is the same call, and the D9 additions only kill children that were already leaking.
- Readers and slots are covered by conditions 6 and 10.
- Rollback is clean: additive, with no lock or dependency change.

## Stories
As an agent operator, I want to enable a loopback-only HTTP endpoint that answers exactly what stdio answers, so that a local client that cannot spawn a stdio child can use the same query engine.

As a reviewer, I want every resource bound (in-flight slots, body size, readers, child processes) observed under disconnect and overload, and every deviation from the spec documented. Then the shared-operation slice (S40) builds on measured behaviour.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s39.criteria.md` (sha256 `66c854ca…b49d4e4`). AC-S39-001..006 and RUN-01/02 are not restated.

**Setup.** No mock of the behaviour under test. Spies only count. Every `all(...)` is preceded by a non-empty assertion. All rejection cases use `withhold_body=True` where noted. A response that arrives while the body was never sent proves the rejection happened before body read and therefore before any query.

| AC | Test (`test_s39_contract.py`) | Steps and observables |
| --- | --- | --- |
| 001 | test_http_query_equivalence[2025-11-25\|2024-11-05] | **Setup.** One `build_fixture` index (CLI path). Server children: stdio `paths.serve_argv()` (S38 `RawLineClient`) and `serve --transport http --port 0` (same repo, graph root and separate memex). **Conversation (stdio).** S38 `steps_for(lifecycle)` steps 1-17. **Conversation (HTTP).** The same steps through `RawHttp`: initialize without a header; later requests carry `mcp-protocol-version: R`; notifications/initialized → 202. **Equal.** InitializeResult (protocolVersion, serverInfo, capabilities); `tools_digest`; resources/list; resources/templates/list; ping `{}`; for steps 7-16, `content[0].text` bytes, `isError` and `structuredContent` (present iff R is modern); the step 17 error object (-32601). **Live step.** On LIVE: page → resources/read blob bytes and `_meta.generation_id` equal (lease tokens via S38 `Normaliser` N5 only). **SDK host** (R = 2025-11-25). `streamable_http_client` + `ClientSession`, raw initialize (`test_s11_stdio.initialize`); `call_tool` dumps for steps 7-13 canonical-equal to stdio. **Non-vacuity.** ≥ 10 compared results; non-empty payloads. |
| 002 | test_loopback_default | **Defaults.** `Config(repo, memex_root).http_host == "127.0.0.1"`. **API bind.** `run_http(default config, http_port=0, install_signals=False, on_listening=cb)` as a task: every address in cb is `127.0.0.1` and `is_loopback`; a POST ping over loopback → 200; then `should_exit`. **Probe.** If a non-loopback local IPv4 exists (UDP-connect discovery to 192.0.2.1, no packet sent), connecting to it on the port → refused; `checked` is recorded and must be true in the dev image. **CLI.** `serve --transport http --host H` with H ∈ {`0.0.0.0`, `::`, `""`, `192.0.2.1`, `fe80::1%lo`} → exit 2, `Usage:` on stderr plus the U2 reason, empty stdout, within 30 s. **Hostname.** Patched `_getaddrinfo`: a hostname resolving to `192.0.2.1` → `LoopbackError("non_loopback_address")` and `on_listening` never called. |
| 003 | test_disconnect_cleanup | **Setup.** LIVE, H2 gate, H3 observer, `http_max_inflight=2`. **Request.** `open_stream` POST tools/call neighborhood seed A (deadline 30000). Wait for `gate.inside == 1`. Assert the reader is open (`_validated_lease`, `source.fd is not None`), `open_leases == 1` and `gate.active == 1`. **Disconnect.** Close the socket (`writer.close()`). **Within 10 s:** `reader._closed`; `source.fd is None`; `conn.execute("SELECT 1")` raises `sqlite3.ProgrammingError`; `open_leases == 0`; `gate.active == 0`; the hook recorded `cancelled`; `release` never set. **Control.** Set `release`, send the same request → 200, isError false. **Cut 2 variant.** A `page` op; `code_graph._lease_registry.usage()` returns to its baseline. |
| 004 | test_concurrency_bound | **Setup.** LIVE, L = 3, H2 + H3. **Saturate.** L concurrent neighborhood POSTs; wait until `open_leases == L`. **Overflow.** 4 more POSTs (2 normal, 2 `withhold_body`): each gets 503 within 5 s, `Retry-After: 1`, body reason `inflight_limit`; `open_leases` stays L; `gate.rejected == 4`. **Disconnect window.** Disconnect one admitted request and immediately send one more: it gets 503 or 200, never a 4th concurrent lease. **Release.** All admitted → 200 non-error. **Final.** `gate.active == 0`, `gate.peak == L` (exact, non-vacuous), `max_open_leases <= L`, `max_inside <= L`; a follow-up request → 200. |
| 005 | test_invalid_origin[...] | **Spies.** Count `manager.handle_request` (instance-patched), `atlas_aci.protocol.http.call_tool_result` and H3 leases. **Cases** (`allowed=("http://127.0.0.1:5173",)`): Origin `http://evil.example`, `null`, `http://127.0.0.1:5174`, `HTTP://127.0.0.1:5173`, each with `withhold_body` → 403 with `origin_not_allowed`; Host `evil.example:P` → 421 with `host_not_allowed`. All three spies stay 0. **Controls.** The exact allowed Origin → 200 and `call_tool_result` count 1; an absent Origin → 200. **Default config.** Any Origin → 403. |
| 006 | test_http_replay | **Request.** R = Day-1 (l) neighborhood (`max_visited_nodes` 1). **Attempt 1.** Gated, then disconnected; wait for cleanup as in 003. **Retries.** Pass-through gate; send byte-identical POST R twice (same JSON-RPC id, header 2025-11-25) on new connections. **Assert.** The SSE `data:` lines are byte-identical; isError false; `stop_reason` == the Day-1 value (never `deadline`); `frontier.commitment` equal and 64-hex; `generation_id`/`snapshot_id` equal; `structuredContent == json.loads(text)`. **Non-vacuity.** `max_visited_nodes` 2 gives a different commitment. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal across the interrupted and resumed receipts; one receipt, `.spectra/state/aci-s39.execution.json`. |

### Supporting tests
- **`test_s39_transport.py`** (in-loop, H1-H7):
  - **Stage-order matrix.**
    - One request violating stages k and j > k gets stage k's status: bad peer is unit-tested via a direct ASGI call with `scope["client"]=("192.0.2.1",1)`; bad Host + bad Origin → 421; bad Origin + `/x` → 403; `/x` + GET → 404; GET `/mcp` → 405 with `Allow: POST`; DELETE and OPTIONS → 405; a full gate + GET → 405.
    - Every body has the D2 shape, a reason ∈ `REJECTION_REASONS` and `connection: close`.
  - **SDK-owned statuses:**
    - body `http_max_body_bytes+1` (declared) → 413, and a chunked overrun → 413, both with no handler call;
    - a JSON array batch → 400 -32602;
    - a bad JSON body → 400 -32700;
    - `mcp-protocol-version: 2099-01-01` on tools/list → 400;
    - Accept without `text/event-stream` → 406;
    - `text/plain` → 400 or 415, recording the status that is observed.
  - **Gate and ticket units.** Refcount; `hold()` after release raises `RequestGone`; `active` decrements exactly once; `peak`; `snapshot()` keys.
  - **Fail closed.** A handler invoked without a ticket fails closed.
  - **Handler parity:**
    - runtime: `set(request_handlers)` and `set(notification_handlers)` of `build_app(...).server` equal those of a `run_stdio` instance captured by patching `atlas_aci.server.Server` with a recording subclass and `atlas_aci.server.stdio_server` with a closed memory-stream context;
    - AST: the decorator sequence, including `validate_input=False`, equals `run_stdio`'s.
  - **`session_version`.** Equals `negotiate(header).revision()` for the 4 ids; absent → 2025-03-26 shaping (no outputSchema in tools/list).
  - **uvicorn config.** `uvicorn_config` fields equal D8 exactly.
  - **No sessions.** `streamable_http_client` round trip; no `mcp-session-id` header in any response.
  - **Cancel notification (cut 1).** `notifications/cancelled` → 202; the in-flight request is unaffected.
- **`test_s39_loopback.py`:**
  - **`resolve_loopback` table:**
    - accepts `127.0.0.1`, `127.0.0.2` (resolution only), `::1`, `[::1]`, `::ffff:127.0.0.1` → `127.0.0.1`;
    - `localhost` with a patched resolver (`[::1, 127.0.0.1]` → `127.0.0.1`; `[::1]` → `::1`);
    - rejects `""`, `0.0.0.0`, `::`, `[::]`, `192.0.2.1`, `10.0.0.1`, `::ffff:192.0.2.1`, `fe80::1%lo0`, a name resolving to `[127.0.0.1, 192.0.2.1]` (`mixed_resolution`), a name resolving to `[0.0.0.0]` (wildcard), and a gaierror (unresolvable), each with the exact reason.
  - **Race.** A resolver returning `[127.0.0.1]` then `[192.0.2.1]`: CLI-style resolve, then `run_http` → `LoopbackError` and no listener. With a literal `http_host`, `run_http` makes zero resolver calls through DNS (AI_NUMERICHOST path).
  - **Post-bind check.** A patched `getsockname` reporting non-loopback → `bound_non_loopback` and shutdown.
  - **Config.** The bounds table, field by field (types, bool rejection, origins regex, `*`, `null`, duplicates, 33 entries, list coercion); `Config(repo=, memex_root=)` unchanged; positional construction unchanged.
  - **CLI.** U1 for each HTTP option with stdio; U3 messages; `serve --help` lists the new options.
  - **Bind conflict (cut 5).** Port in use → exit 1.
- **`test_s39_cancellation.py`:**
  - **Fake binaries.** `search_text` with a fake `rg` on PATH (`monkeypatch.setenv`). Start it, wait for the pidfile, cancel the task → CancelledError; within 5 s `os.kill(pid, 0)` raises ProcessLookupError.
  - **test_dry_run.** The same, with `Config(test_execution_enabled=True)` and a fake `pytest`.
  - **TimeoutError path unchanged.** Short timeouts → ToolError TIMEOUT and the child dead.
  - **Over HTTP.** search_text, then a client disconnect → the pid is gone, and test_dry_run likewise (cut 7).
  - **Diff check.** The D9 diff vs 8ab6582 for both files has 0 deleted lines (`git diff --numstat`).
- **`test_s39_isolation.py`:**
  - **Tripwire.** `requires("mcp")` names uvicorn, starlette and sse-starlette; the uvicorn version is 0.44.0 and `capture_signals` is a contextmanager on `uvicorn.Server`; the pyproject/uv.lock shas equal the S38 constants.
  - **G-MCP in a fresh interpreter importing `atlas_aci.protocol.http` first.** Tools sha/bytes, 7 tools, budgets 3436/3448/797/7730, `ev.RELATIONS`, `GRAPH_OUTPUT_SHA`.
  - **server.py.** sha == `SERVER_PY_SHA`; line 20 unchanged; MARKERS count 3.
  - **http.py surface.** Imports ⊆ the D1 allow-list; no `open/print/exec/eval` calls, no `tempfile/os/subprocess/sqlite3`, no harden markers; `REJECTION_REASONS`/`LOOPBACK_REASONS` closed and unique.
  - **Lazy import.** The `import atlas_aci.__main__` probe: `atlas_aci.protocol.http` absent and the third-party module set equal to the Day-1 (j) baseline.
  - **`serve` params.** Exactly base ∪ {transport, host, port, max_inflight, max_body_bytes, allowed_origins}, with no `frameworks` or `rails_table_map`.
  - **protocol package.** The surface is base ∪ {`http.py`}.
  - **Docs.**
    - `http-transport.md`: ASCII, no banned words; names every reason, option, default and bound, 403/404/405/413/421/503, `Retry-After`, `2025-03-26`, `notifications/cancelled`, `SHOULD NOT`, `S40`, `enforcement.py:77`; the real-host lines carry the untested phrase.
    - `protocol-compatibility.md`: still passes the S38 doc test and names `hosts/http-transport.md`.
    - CHANGELOG: `### Security` first; bullets[7] OTel, bullets[8] S39, bullets[9] "Rails: direct job enqueues"; "(ACI-S39)" exactly once.
    - README: the row right after the rails row, 3 pipes, `hosts/http-transport.md` exactly once.

### Gates beyond the ACs (dev image `atlas-aci-dev-relock`, `uv run --frozen`; logs and exit files under `.spectra/evidence/aci-s39/`)
- **G-S39:** `pytest tests/slices/test_s39_contract.py -q`, then `pytest tests/slices/test_s39_*.py -q`.
- **G-REG**, run after the docs edits:
  `tests/slices/test_s11_*.py tests/slices/test_s13_*.py tests/slices/test_s18_stdio*.py tests/slices/test_s23_*.py tests/slices/test_s3[3-8]_isolation.py tests/slices/test_s38_*.py tests/slices/test_s12_admission.py tests/slices/test_s24_isolation.py tests/slices/test_s25_isolation.py tests/slices/test_s26_isolation.py tests/slices/test_s27_isolation.py tests/slices/test_s28_isolation.py tests/slices/test_s30_isolation.py tests/slices/test_s37_evidence_report.py tests/test_server*.py tests/test_thesis_negatives.py`.
- **G-LINT, exactly as CI:** `ruff check src/ tests/`; `ruff format --check src/ tests/`; `mypy src/`; `ruff check ../scripts/ --exclude fingerprint-fixture`; `ruff format --check ../scripts/ --exclude fingerprint-fixture`; `mypy ../scripts/`.
- **G-FULL:** `pytest -q --deselect tests/slices/test_s01_contract.py::test_call_deadline_after_initialization --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition`. No new failure or skip versus `base-invariants.json`.
- **G-SRC:**
  - `git diff --exit-code 8ab6582 -- mcp-server/src ':!mcp-server/src/atlas_aci/protocol/http.py' ':!mcp-server/src/atlas_aci/__main__.py' ':!mcp-server/src/atlas_aci/config.py' ':!mcp-server/src/atlas_aci/tools/search_text.py' ':!mcp-server/src/atlas_aci/tools/test_dry_run.py' mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile .github/workflows scripts atlas docs`;
  - `git diff --exit-code 8ab6582 -- mcp-server/src/atlas_aci/server.py` (explicit);
  - `git diff --name-status 8ab6582 -- mcp-server/src` == {A protocol/http.py, M __main__.py, M config.py, M tools/search_text.py, M tools/test_dry_run.py};
  - `--numstat` deletions == 0 for the two tools files;
  - tests ⊆ `tests/slices/test_s39_*.py`;
  - hosts ⊆ {A http-transport.md, M protocol-compatibility.md};
  - root ⊆ {CHANGELOG.md, README.md}.
- **G-MCP:** `atlas-aci tools` sha `94f9567a…` / 1631472 bytes.
- **G-STDIO-PRESERVE:**
  - branch captures via `test_s38_support.py capture`, seeds 0 and 4242;
  - `transcripts/*` equal to the committed `tests/fixtures/s38/transcripts/*` and to `preserve/base` (`cmp`);
  - `frames.json["frames"]`, `ac004.json` and `inprocess.json` (identity excluded) equal to base;
  - `test_s38_contract.py` green.
- **G-HARDEN:** `bash scripts/harden-gate-classify.sh 8ab6582 HEAD` prints `augmented=false` and `a3_touched=false`.
- **G-PY314:** `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s39_*.py tests/slices/test_s38_isolation.py -q'` → `py314.log`. Passed counts and no skips. The fake `rg` makes ripgrep unnecessary.
- **G-DRIFT:** `ramza-drift` with the declared scope below.
- **G-PROD** (`.spectra/evidence/aci-s39/production-scenario.sh IMG`, run by the orchestrator; POSIX sh, `set -eu`; workdir from `mktemp -d`):
  - **Container rules.** Every container is `--rm` with a unique name `s39-$$-N`, cleaned up by `trap 'docker rm -f <names> >/dev/null 2>&1 || true' EXIT INT TERM`.
  - **Watchdog.** `bounded SECS NAME args…` backgrounds `docker run`, polls `kill -0` once per second, then `docker kill`, FAIL 124. It uses `wait $pid || rc=$?`.
  - **Image.** Record the image id. `--entrypoint` is always explicit.
  - **Host-side checks:**
    - H1 identity: `--entrypoint /usr/local/bin/python IMG -I -c …` prints the mcp/uvicorn/starlette/sse-starlette/anyio versions and the sha256 of `server.py`, `protocol/http.py`, `config.py` and `__main__.py`; exact compare with host values.
    - H2 index: fixture files (written with printf, plus LIVE), directories mode 777; `--entrypoint atlas-aci IMG index --repo /repo --graph-root /graphs` → 0. Then record host-side sha256 manifests of `repo/` and `graphs/`.
    - H3 driver: `--network none --read-only -e PYTHONDONTWRITEBYTECODE=1 -v repo:/repo:ro -v graphs:/graphs -v memex:/memex -v mcp-server/tests/slices:/s39:ro --entrypoint /usr/local/bin/python IMG -I -c 'import sys;sys.path.insert(0,"/s39");import test_s39_support as s;raise SystemExit(s.production_main(sys.argv[1:]))' --repo /repo --graph-root /graphs --memex-root /memex --port 18765`. The driver (stdlib only, asyncio streams) prints one JSON line per check and runs E1-E9 below.
    - H4 non-loopback CLI, host-side: `--network none --entrypoint atlas-aci IMG serve --transport http --host 0.0.0.0 --repo /repo` → exit 2 with `Usage:`; likewise `--host 192.0.2.1`.
    - H5: the repo and graph-root manifests are unchanged after all runs; repo mounted `:ro`.
    - H6: S38 `production_main --case modern.2025-11-25` against `/golden` → `match:true` (stdio preserved in the image).
    - H7: `IMG tools --repo /repo` sha and bytes equal the pin.
    - H8: G-SRC and the pyproject/uv.lock shas on the host.
  - **Driver checks** (inside the H3 container; each non-vacuous):
    - E1: stdio `atlas-aci serve` versus `serve --transport http --host 127.0.0.1 --port 18765 --max-inflight 2 --max-body-bytes 65536` → AC-001 equality for steps 1-16 at 2025-11-25 and 2024-11-05.
    - E2: Origin `http://evil.example` with the body withheld → 403 `origin_not_allowed`; then a valid call → 200.
    - E3: two slow-body POSTs (half a body sent) are held; a third → 503 + `Retry-After: 1`; finish both → 200; a fourth → 200.
    - E4: declared 65537 → 413; a chunked overrun → 413.
    - E5: GET and DELETE `/mcp` → 405 `Allow: POST`; GET `/x` → 404.
    - E6: Host `attacker.example:18765` → 421.
    - E7: SIGTERM to the HTTP child → exit 0 within 10 s, `http_stopped` on stderr, no Traceback, then connection refused.
    - E8: in-container `--host 0.0.0.0` → exit 2.
    - E9: `/proc/net/tcp` and `tcp6` LISTEN entries on 18765 are exactly `0100007F:4983`.
  - **Output.** The script writes `production-scenario.json` and exits 0 only if H1-H8 and E1-E9 pass.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s39_contract.py -q
uv run --frozen pytest tests/slices/test_s39_*.py -q
uv run --frozen pytest -q --deselect tests/slices/test_s01_contract.py::test_call_deadline_after_initialization --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
```

## Confidence
To be computed with `ramza-score`.
- **The SDK owns the wire.** Atlas adds one module, five Config fields, CLI options and a two-line fix in each of two tools. `server.py` is untouched.
- **Behaviour preservation:** G-SRC, G-STDIO-PRESERVE (base worktree captures) and G-MCP.
- **Main uncertainties, all covered by Day-1:**
  - disconnect → cancel timing (e);
  - scope identity for the ticket (f);
  - signal handling (i);
  - 3.14 behaviour (d).

## Rejected Alternatives
- **Option (a), a `build_server(config, version_resolver)` extraction in `server.py`.** It touches the stdio golden code path and the S38 anchors. Parity tests guard option (b) instead.
- **Stateful sessions.** The handler survives a POST disconnect, so AC-003 would fail. It also needs `max_sessions` and an idle reaper, and holds server state.
- **`json_response=True`.** There is no disconnect watcher, so a disconnect never cancels.
- **Starlette `Router`/`Mount` or `TrustedHostMiddleware`.** Routing order is opaque, and the middleware runs after body buffering. The raw wrapper orders the checks explicitly before any body read.
- **uvicorn `limit_concurrency`.** It counts idle keep-alive connections and answers in plain text; at most a loose backstop.
- **A queueing semaphore.** Queued requests hold sockets and bodies without bound. The gate rejects instead.
- **Counting only the HTTP request in the gate.** The handler unwind outlives the request (stateless server task), so the count could transiently exceed the limit. Hence the ticket refcount.
- **A ContextVar ticket.** It depends on anyio task-spawn context copying. The scope dict is passed by identity through every SDK layer (Day-1 f).
- **Patching global `asyncio.sleep` or `socket.getaddrinfo`.** It would hit uvicorn and anyio in-process. Module attributes are patched instead.
- **A `--public` flag or `0.0.0.0` behind a warning.** Excluded until S40.
- **Fixing `Enforcement.records` or the per-client rate limit here.** Undeclared `enforcement.py`; S40.

## Risks (ranked)
1. **Partial cancellation reach.** Synchronous ops are uncancellable and block the loop. Documented, bounded by their caps; D9 closes the subprocess leak.
2. **Disconnect = cancel deviates from the spec.** Quoted in the doc (Day-1 k); `notifications/cancelled` is a stateless no-op.
3. **Handler duplication drift.** Runtime and AST parity, plus AC-001 over all handlers.
4. **SDK internals coupling** (scope identity, stateless task group, SSE disconnect). Day-1 (e)/(f) STOP, the uvicorn and mcp tripwires, a byte-pinned uv.lock.
5. **The sse_starlette global `AppStatus` and the uvicorn signal re-raise.** The override plus tests that never signal; the fixture resets the flag.
6. **S40 limits:** unbounded `Enforcement.records`, the process-wide rate limiter, slow-body slot holding. Documented with anchors.
7. **In-process hazards:** the global patches, blocking clients, sync ops. Rules H1-H8.
8. **Docs pins** (CHANGELOG index 8, README after rails, protocol-compatibility tokens). G-REG runs after the docs edits.
9. **Python 3.14** (uvicorn 0.44, anyio 4.16, sse-starlette 3.3.4). Day-1 (d) and G-PY314.
10. **Timebox.** The cut order and the Day-2 checkpoint.

## Rollback
- One bounded PR with no data, schema, dependency, lock or stdio wire change.
- `git revert <output_commit>` removes the module, the options, the fields and the two-line tool fixes.
- Docs and tests are additive.
- The Dockerfile CMD stays stdio, so a deployed image is unaffected unless an operator opts into `--transport http`.
