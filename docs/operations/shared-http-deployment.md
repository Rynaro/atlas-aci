# Shared multi-principal HTTP deployment (S40)

`atlas-aci serve-shared --shared-config PATH` serves several operator-declared repositories to
authenticated principals over one loopback listener. It is the shared-operation mode that the
loopback transport (`hosts/http-transport.md`, `atlas-aci serve --transport http`) deliberately did not
provide. Nothing about the stdio server, `serve --transport http`, the tools manifest or the query
engines changes. This page states what was built, what is enforced, and what is not isolated.

## 1. Summary and trust boundary

- The listener binds a loopback address only. "Shared" means several principals reach it through an
  operator reverse proxy that terminates TLS, rewrites `Host` to `127.0.0.1:<port>` and forwards the
  `Authorization` header. The proxy is outside Atlas.
- Behind the proxy every client appears to come from loopback, so the S39 peer check is moot and
  the bearer credential is the only gate. An unprivileged local process can squat the port: if it
  binds the configured port before Atlas does, the proxy forwards credentials to it. Start Atlas
  first, wait for `shared_http_listening` (a bind failure exits 1), and keep the port on a host where
  only the proxy and the operator run code.
- Principal = (issuer, subject) from a verified JWT access token. The operator policy file maps a
  principal to repository ids and to a subset of five shareable tools. The policy file is the
  authorization source of truth; token scopes can only narrow it (`required_scope`).
- Each registered repository is served on `/mcp/<repository_id>`, with its own derived `Config`,
  server state, graph namespace and lease registry. Unknown and unauthorized repositories both get
  403 with the same body, so a repository id cannot be enumerated.
- Authorization sits in three independent layers: the router (before any body is read), a guard on
  every MCP request handler, and the enforcement and lease layers (`SharedEnforcement`,
  `ScopedLeaseRegistry`). All three must hold for a request to reach a repository.
- `test_dry_run` and `memex_read` are never shareable and never callable in shared mode, and the
  server refuses to start while `ATLAS_ACI_ENABLE_TEST_EXECUTION=1`.
- The search tool passes the pattern to ripgrep as the value of `-e` after all options and the paths
  after `--` on every transport. In shared mode a `search_text` pattern that is not a string or
  that starts with `-` is refused before dispatch (`argument_rejected`).

## 2. The shared-config document

Format `atlas-aci-shared.v1`. The document is closed: an unknown key at any level is refused, as are
duplicate keys, NaN and Infinity. It is at most 1048576 bytes of UTF-8. The file and its parent
directory must not be group- or world-writable (`unsafe_file`); the parent directory is the one that
contains the file.

| Field | Default | Bound or rule |
| --- | --- | --- |
| `format` | required | `atlas-aci-shared.v1` |
| `listen.host` | `127.0.0.1` | resolves only to loopback addresses (otherwise a usage error) |
| `listen.port` | 8765 | 0 (pick a free port) or 1024 to 65535 |
| `listen.max_inflight` | 8 | 1 to 256, one gate shared by every repository |
| `listen.max_body_bytes` | 1048576 | 1024 to 16777216 |
| `listen.allowed_origins` | `[]` | at most 32 exact `scheme://host[:port]` origins |
| `listen.max_inflight_per_principal` | `max(1, max_inflight // 2)` | 1 to `max_inflight` |
| `identity.issuer` | required | `https://...`, at most 2048 characters, no space, `#` or `?` |
| `identity.jwks_file` | required | absolute path, read once at startup |
| `identity.resource_base_url` | required | `https://host[:port]/mcp`; a route's resource URL is this value plus `/<id>` |
| `identity.leeway_seconds` | 30 | 0 to 60 |
| `identity.max_token_bytes` | 8192 | 1024 to 16384 (the HTTP header limit makes more unreachable) |
| `identity.max_token_lifetime_seconds` | 86400 | 60 to 604800; `exp - iat`, or `exp - now` without `iat` |
| `identity.scopes_supported` | `[]` | at most 32 scope tokens, advertised in the metadata |
| `policy.file` | required | absolute path |
| `policy.reload_interval_seconds` | 2 | 0 (every request) to 60 |
| `limits.max_calls_per_minute` | 200 | 0 to 100000, per repository (0 disables) |
| `limits.max_calls_per_principal_per_minute` | 120 | 0 to 100000, one window per principal across repositories (0 disables) |
| `limits.max_leases_per_principal` | 1 | 1 to 64 |
| `limits.max_graph_leases` | 4 | 1 to 64, retained leases per repository |
| `limits.max_total_lease_state_bytes` | 1073741824 | 16777216 to 17179869184, process-wide |
| `limits.max_records` | 1024 | 0 to 65536, retained telemetry records per repository |
| `repositories[].id` | required | `[a-z0-9][a-z0-9._-]{0,63}`, unique; 1 to 64 repositories |
| `repositories[].repo` | required | absolute path of an existing directory |
| `repositories[].graph_root` | required | absolute path, outside every registered repository |
| `repositories[].memex_root` | required | absolute path, outside every registered repository |
| `repositories[].before_repo_id` | none | another registered id; the before side of `graph_query op=impact` |

Startup refusals (each is a usage error, exit 2, with `invalid shared configuration (<reason> at
<field>)` and never an echoed value): `unreadable`, `too_large`, `unsafe_file`, `not_json`,
`unknown_field`, `missing_field`, `wrong_type`, `out_of_range`, `bad_format`, `duplicate_id`,
`not_absolute`, `not_directory`, `memex_inside_repository`, `duplicate_namespace`,
`duplicate_memex`, `repository_nested`, `graph_root_inside_repository`, `root_nested`,
`before_unknown`, `before_self`, `capacity_exceeded`, `jwks_invalid`, `policy_invalid` and
`test_execution_enabled`.

Overlap rules. A principal granted any id of a path can read that path's live tree (`view_file`,
`list_dir` and `search_text` read the working tree, including dotfiles and `.git`). So no registered
repository may contain or equal another, except one explicit pair: two ids with the same path, linked
by `before_repo_id`, with distinct graph roots. No graph root or memex root may lie inside any
registered repository, and no repository, graph root or memex root may lie inside another root
(`repository_nested`, `graph_root_inside_repository`, `memex_inside_repository`, `root_nested`,
`duplicate_namespace`, `duplicate_memex`). Two repositories may share one graph root, because a graph
namespace is derived from the repository path.

Capacity formula (`capacity_exceeded`). Per repository, `2 * max_leases_per_principal <=
max_graph_leases`, so one principal can never hold more than half of a repository's retained leases.
Process-wide, each repository's `max_total_lease_state_bytes` is `min(67108864,
limits.max_total_lease_state_bytes // repositories)`, and startup is refused if that is below 16777216
(the per-lease state cap). The aggregate retained lease state is therefore at most
`limits.max_total_lease_state_bytes` (default 1 GiB, which is 64 repositories at 16 MiB). Retained
leases live for `graph_lease_seconds` (300) and clients have no release call, so the quota is what
keeps one principal from filling a repository.

## 3. The policy document

Format `atlas-aci-shared-policy.v1`, closed, at most 1048576 bytes and 10000 grants. A grant is
`{"issuer", "subject", "repository", "tools", "required_scope"}`:

- `issuer` must equal `identity.issuer` (`issuer_unknown`); `subject` uses the same predicate as the
  token `sub` claim: 1 to 255 printable characters without leading or trailing space.
- `repository` must be registered (`repository_unknown`); `tools` is a non-empty unique subset of
  `view_file`, `list_dir`, `search_text`, `search_symbol` and `graph_query` (`tool_not_shareable` for
  `test_dry_run`, `memex_read` or anything else); `required_scope` is optional, one scope token.
- `(issuer, subject, repository)` is unique (`duplicate_grant`). An empty grant list is valid and
  means deny-all. The other reasons are `unreadable`, `too_large`, `unsafe_file`, `not_json`,
  `unknown_field`, `missing_field`, `wrong_type`, `bad_format` and `too_many_grants`.

Digest and epoch. The policy digest is the SHA-256 of the canonical document (grants sorted, tools
sorted, an absent `required_scope` as null), so key order, grant order and whitespace do not matter.
Once per `reload_interval_seconds` the store opens the file once (no symlink), checks that the
descriptor is a regular file that is not group- or world-writable inside a directory that is not,
reads and hashes the bytes from that same descriptor, and decides the epoch from the digest alone.
Timestamps, sizes and inodes are never used, so a same-size rewrite with a restored modification time
is still seen. The epoch starts at 1 and advances if and only if the effective digest changes. A file
that cannot be read, is not a regular file, is a symlink, is unsafe or does not validate is deny-all
(digest `invalid`, one `shared_policy_invalid reason=...` warning); invalid to valid always advances
the epoch. Edit the file by writing a new file in the same directory and renaming it over the old
one. A revocation that is undone before the next reload is not observed.

## 4. The JWKS file and key rotation

A JSON object `{"keys": [...]}` of at most 65536 bytes with 1 to 32 public keys. Each key is loaded on
its own and any failure refuses startup (`jwks_invalid`); a key is never skipped. A key needs a unique
string `kid`, `kty` RSA, EC (P-256) or OKP (Ed25519), no private member, `use` `sig` if present, an
`alg` in the allow-list if present and matching `kty`, and an RSA modulus of at least 2048 bits. The
file is read once at startup; rotate by adding the new key, issuing tokens with it, then removing the
old key and restarting. Revoking one compromised token before its `exp` needs a short `exp`, or a key
rotation with a restart. The identity provider must not act as an oracle that confirms which
audiences exist: audiences are the per-route URLs, one per repository.

## 5. The token profile

Generic OIDC JWT access tokens (RFC 9068 shape) verified offline with pyjwt; there is no key
discovery, token introspection or network access.

- One `Authorization: Bearer <token>` header, at most `identity.max_token_bytes`, three base64url
  parts. Algorithms RS256, PS256, ES256 and EdDSA, matched to the key type; `none`, HS256 and every
  other algorithm are refused, before and independently of pyjwt. The header needs a `kid` that is in
  the set; `crit`, `jku`, `jwk`, `x5u` and `x5c` are refused; `typ`, if present, is `at+jwt`,
  `application/at+jwt` or `JWT`.
- Required claims `exp`, `iss`, `aud` and `sub`. `exp`, `nbf` and `iat` must be integers (a boolean, a
  float or a numeric string is refused after decoding); `iat` in the future beyond the leeway is
  refused (observed pyjwt 2.15.1 behaviour). `aud` must be one string equal to the route's resource
  URL (pyjwt `strict_aud`): a token that lists several audiences is refused. The lifetime must not exceed
  `identity.max_token_lifetime_seconds`.
- Every refusal is one of: `header_missing`, `header_duplicate`, `scheme_invalid`, `token_oversize`,
  `token_malformed`, `header_invalid`, `alg_not_allowed`, `kid_missing`, `kid_unknown`,
  `key_type_mismatch`, `signature_invalid`, `expired`, `not_yet_valid`, `issuer_invalid`,
  `audience_invalid`, `claim_missing`, `subject_invalid` and `lifetime_excessive`. Any other
  exception is `token_malformed`. The reason is counted in the process and never sent.
- On the wire there are two answers. A missing header is 401 `credentials_missing` with
  `WWW-Authenticate: Bearer resource_metadata="<url>"`; everything else is 401 `credentials_invalid`
  with `WWW-Authenticate: Bearer error="invalid_token", resource_metadata="<url>"`. The metadata URL
  is `<origin of resource_base_url>/.well-known/oauth-protected-resource/mcp/<id>`.
- At startup `serve-shared` refuses to run if pyjwt or cryptography is older than the verified
  versions (2.15.1 and 50.0.2). JWT verification runs on the event loop, so the reverse proxy must
  rate-limit unauthenticated traffic.

## 6. The router and its closed order

The router decides from the ASGI scope, in this order, and never reads the body before stage 9.

| # | Stage | Refused when | Status | Reason |
| --- | --- | --- | --- | --- |
| 1 | peer | not a loopback peer | 403 | `peer_not_loopback` |
| 2 | host | Host is not the bound literal or `localhost` with the port | 421 | `host_not_allowed` |
| 3 | origin | an Origin that is not allowed | 403 | `origin_not_allowed` |
| 4 | route | the protected-resource metadata path with GET is answered 200 for any valid label, with no credential; another method is 405; a path that is not exactly `/mcp/<label>` is 404 | 200, 405, 404 | `metadata_method_not_allowed`, `route_not_found` |
| 5 | method | not POST | 405 | `method_not_allowed` |
| 6 | credentials | the token does not verify for this route's audience | 401 | `credentials_missing`, `credentials_invalid` |
| 7 | policy | the policy is reloaded; on an epoch change every lease of a revoked grant is purged | | |
| 8 | authorization | no grant for this repository, or an unknown repository | 403 | `repository_forbidden` |
| 8b | in-flight | the principal already has `max_inflight_per_principal` requests in flight | 503 | `principal_inflight_limit` |
| 9 | delegate | the S39 stages run again, then the shared gate (503 `inflight_limit`) and the SDK (413, 406, 415, 400) | | |

The reasons `route_not_found`, `metadata_method_not_allowed`, `credentials_missing`,
`credentials_invalid`, `repository_forbidden` and `principal_inflight_limit` use the S39 body shape
with a fixed message. Paths are matched with `fullmatch` and an ASCII label; the raw path must equal
the decoded path (any percent-encoding is a 404), a trailing slash, `//`, upper case, a newline and a
non-empty `root_path` are 404. Stages 6 to 8 run inside a catch-all: a verifier, policy or purge
failure is a fixed 401 or 403, and the text of an exception never reaches the wire.

The protected-resource metadata (RFC 9728) names the route's resource URL, the issuer as the
authorization server and `scopes_supported`. The issuer URL is normalized by the SDK model and gains a
trailing slash.

## 7. Guards and the access-path matrix

Every MCP request handler of every repository (not `ping`) is wrapped by a guard that classifies the
request, decides again against the current policy snapshot, and sets the authorization context only
inside the handler task (reset in `finally`; it is unset in the router and in any task spawned outside
a guard). Guard rules:

- `tools/list`, `resources/list` and `resources/templates/list` need a grant; the tool list is the
  unchanged seven-tool manifest, so `test_dry_run` and `memex_read` are listed but denied.
- `resources/read` needs the `graph_query` grant. `tools/call` needs the tool in the grant; an unknown
  tool, `test_dry_run` and `memex_read` give the same fixed `FORBIDDEN` result as the base server.
- `graph_query op=retrieve` and `op=context` with a `repository` argument other than this route's
  repository are `FORBIDDEN`, before any lease. `op=impact` needs a `graph_query` grant on the route
  and on its `before_repo_id` (`authorize_path`), or it is `FORBIDDEN` before any lease on either.
- A denied `tools/call` returns the fixed isError result, a denied `resources/read` is error -32602
  `FORBIDDEN`, any other denied request is error -32600 `Request not authorized.`. A guard failure
  (an exception while classifying or deciding) is a denial; no exception text is sent.
- The reason for a denial is counted in the process and never sent. The closed set is
  `policy_unavailable`, `repository_unknown`, `repository_not_granted`, `scope_not_presented`,
  `tool_disabled`, `tool_unknown`, `tool_not_granted`, `argument_rejected`,
  `repository_argument_mismatch`, `cross_repository_not_granted` and `principal_missing`.
- The SDK handler set is checked at startup (the verified `mcp` version, exactly the six request
  handlers, no notification handler); anything else refuses to serve.

Access-path matrix. In the first three columns the response arrives while the body is withheld, the
ASGI `receive` is never called and every repository observer reads zero.

| Request | No token | Bad token | Unauthorized or unknown repository | Authorized |
| --- | --- | --- | --- | --- |
| `initialize`, `ping`, notifications | 401 | 401 | 403 | SDK answer (200 or 202) |
| `tools/list` | 401 | 401 | 403 | 200, the seven tools |
| `view_file`, `list_dir`, `search_text`, `search_symbol` | 401 | 401 | 403 | result; not in the grant: isError `FORBIDDEN` |
| `graph_query` status, neighborhood, path, legacy | 401 | 401 | 403 | result; not granted: `FORBIDDEN` |
| `graph_query` retrieve, context | 401 | 401 | 403 | result; another repository argument: `FORBIDDEN` |
| `graph_query` page, continue | 401 | 401 | 403 | own cursor: page; another principal's: `SCOPE_MISMATCH`; another repository's: `INVALID_REFERENCE` |
| `graph_query` impact | 401 | 401 | 403 | before side not granted: `FORBIDDEN`; none configured: `IMPACT_UNCONFIGURED` |
| `test_dry_run`, `memex_read`, unknown tool | 401 | 401 | 403 | isError `FORBIDDEN` always |
| `resources/list`, `resources/templates/list` | 401 | 401 | 403 | 200 |
| `resources/read` | 401 | 401 | 403 | own URI: bytes; another principal's: -32602 `SCOPE_MISMATCH`; no `graph_query` grant: -32602 `FORBIDDEN` |
| unknown method | 401 | 401 | 403 | -32601 |
| GET or DELETE on `/mcp/<id>` | 405 | 405 | 405 | 405 |
| GET metadata path | 200 | 200 | 200 | 200 |
| `/mcp`, `/x`, `/mcp/<id>/`, a bad label | 404 | 404 | 404 | 404 |

Scope identity. Every lease, cursor and resource token is bound to the scope identity: the SHA-256 of
the principal, the repository id, the grant digest and the policy epoch, combined with the base scope
of the lease registry. The retained-evidence store is the lease registry (leases, their pages and
their resources); there is no result cache, so "the cache" of an authorization scope means the lease
registry. `scope()` fails closed when no authorization context is set for the repository. Another
principal's cursor or resource is refused with `SCOPE_MISMATCH`; another repository's is
`INVALID_REFERENCE`. A request decided at an older epoch cannot create or resume anything.

The same-path pair (A and C with the same path and distinct graph roots, A naming C as its
`before_repo_id`) is "same working tree, distinct graph namespace and snapshot". What `impact`
protects there is historical graph and source-pack evidence of the before snapshot, not the live
files: a grant on either id exposes the live tree through the file tools. This is a real boundary
but it is not a content-isolation claim.

## 8. Revocation

- A change of the policy file takes effect at the first request after `reload_interval_seconds`
  (0 means every request). A revoked principal gets 403 at the router.
- Every policy change advances the epoch and therefore invalidates every cursor and resource of every
  principal (a safe over-invalidation). Rewriting the same bytes does not change the epoch.
- Requests already in flight finish; they cannot commit a new lease after the epoch moves. Retained
  leases of a stale epoch or a revoked grant are purged on the next request that reloads the policy,
  and every lease expires after 300 seconds in any case. A restored policy never revives an old
  cursor.
- Per principal: at most `max_leases_per_principal` retained leases, `max_inflight_per_principal`
  requests in flight and `max_calls_per_principal_per_minute` calls; one principal at a limit does not
  block another.

## 9. The command line

`atlas-aci serve-shared --shared-config PATH`. Usage errors are `exit 2`: nothing is bound and nothing
is printed on stdout: (V1) the option is missing; (V2) `--shared-config: invalid shared configuration (<reason> at
<field>)`; (V3) `shared serving is not available while test execution is enabled
(ATLAS_ACI_ENABLE_TEST_EXECUTION=1)`; (V4) `listen.host must resolve only to loopback addresses
(reason: ...)`; (V5) a refusal with fixed text (an unverified `mcp` version, an older pyjwt or
cryptography, an unexpected handler set). The runtime failures are `exit 1`: a repository whose graph
index is unavailable or misconfigured (`repository <id>: graph index is unavailable or misconfigured`, never a path), or a
bind failure (`The HTTP listener could not start (is the port already in use?)`). It logs
`shared_http_listening host= port= repositories= policy_epoch=` and `shared_http_stopped admitted=
rejected= peak=`; SIGTERM stops it with exit 0. The request path logs nothing else.

## 10. Not isolated

This mode separates principals and repositories at the three layers above. It does not give process
isolation, and it does not remove these limits:

- One process and one address space serve every principal and repository; a bug that crosses a
  repository boundary in memory is not contained.
- Synchronous operations (JWT verification, the policy reload, graph traversal and the file tools)
  run on the single event loop and block it for every principal while they run.
- Timing side channels are not addressed: latency can reveal that a repository exists or how large
  a result was.
- `Enforcement.record` logs tool arguments (patterns, paths) to the operator's stderr at the Atlas
  logger; they are operator-visible, and telemetry records are trimmed to `limits.max_records`.
- The server's own repository path is visible in `retrieve` and `context` outputs to authorized
  principals.
- `tools/list` advertises the seven tools, including `test_dry_run` and `memex_read`, which are denied.
- The memex is per repository, not per principal; `memex_read` is disabled in shared mode.
- The S30 cross-repository workflow stays offline and is not served; `impact`'s before repository is
  the only served expansion.
- `test_dry_run` is disabled and test execution makes the server refuse to start.
- A token is valid for one route only (the audience is the route's resource URL).
- `view_file`, `list_dir` and `search_text` read the working tree, including dotfiles and `.git`.
- A grant on any id of a shared path exposes that path's live tree.
- The `search_text` regular expression runs in ripgrep with its own 15 second bound; the interface
  does not bound its CPU use per principal.

## 11. Reverse-proxy requirements

- Terminate TLS at the proxy and publish the resource URL (`identity.resource_base_url`) as the
  public address of `/mcp`.
- Rewrite `Host` to `127.0.0.1:<port>` (the bound literal), forward `Authorization` unchanged, and
  forward only `/mcp/<id>` and the metadata paths.
- Rate-limit unauthenticated requests and limit request size and slow bodies; Atlas bounds in-flight
  work but verifies JWTs on the event loop.
- Do not run the container with `--network host`: the loopback bind would then be the host's
  loopback. Publish nothing from the container; reach it from the proxy over a private network
  namespace or a loopback-only port on the same host.
- Keep the shared-config, policy and JWKS files, and their directory, writable only by the operator:
  Atlas refuses group- or world-writable files and directories. A local writer of the policy file
  bypasses every check above.
