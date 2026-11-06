---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-06T02:30:00Z
slice_id: ACI-S40
issue: https://github.com/Rynaro/atlas-aci/issues/61
---
# ACI-S40 - enforce repository authorization before graph access and caching

## Scope

Issue #61. Base `f4f9f9f2b3c41ecf4eb4f5b65a97c9ae31991502`: main after PR #105 (S39) and PR #104 (re-lock: mcp 1.30.0, pyjwt 2.15.1, cryptography 50.0.2), with S01-S39 merged except S15. Branch `aci-s40`.
- Marker `<!-- atlas-aci:slice:ACI-S40 -->` verified.
- Criteria digest `98cb89a8437eeb73ac0f70ccb2547db80fc2e050dd7293093c07bd8fb478ff16` (9 product ACs in `test_s40_contract.py`, plus RUN-01/02).
- Risk **P0**. Timebox **5 engineering days**. Rightsize tier **full** (score 6): independent checker and critic record.
- **Dependencies PRESENT:**
  - S13: `query/leases.py` `LeaseRegistry` (`scope`, `token`, `authenticate`, `page`) and `protocol/resources.py`.
  - S30: `cross_repo/*`, offline only, with label `[a-z0-9][a-z0-9._-]{0,63}` (revision_vector.py:74).
  - S39: `protocol/http.py` `build_app(config, state)`, `AtlasHttpApp`, `InflightGate`, `run_http`.

Observed facts (verified at planning, base f4f9f9f):
- **http.py.**
  - Wrapper stages :316-338 (peer, host, origin, path `/mcp`, POST, gate). `_reject` :280 (S39 body shape). `allow_host_name` :272. `running` :340. `aclose_state` :356 (idempotent).
  - `build_app(config, state)` :370: a passed state gives `owns_state=False`.
  - Handlers :407-447. `_read_resource` creates a plain `LeaseRegistry` only when `_lease_registry is None` (:444-446).
  - `uvicorn_config` :520 calls `app.allow_host_name`. `_serve` :568, `_stop` :577. `run_http` :594 refuses on a `VERIFIED_SDK` mismatch or test execution (:601-604).
- **server.py.**
  - `dispatch_tool_call` :446 calls `assert_read_only` :463 and `assert_rate_limit` :464 before any I/O.
  - Impact: `before_side(config)` :466-470.
  - The registry is created only if None (:479-481, :486-488).
  - Repository and execution access: `reader_lease` :502, `source_lease` :539, `view_file`/`list_dir`/`search_text`/`test_dry_run` :544-554, `memex.read` :556.
  - `call_tool_result` :600 does manifest and jsonschema work only before dispatch.
- **Denial pieces.**
  - `results.fixed_result(code, mode, structured, operation)` :69 and `FIXED_MESSAGES["FORBIDDEN"]` :29.
  - `resources.read_resource` :118 calls `assert_rate_limit` first (:121), then `registry.authenticate` (:126). Errors are `McpError(error_data(reason))` with FORBIDDEN and SCOPE_MISMATCH mapped to -32602 (:18-28).
- **leases.py.**
  - `scope()` :92 is called only from `pending` :160, `authenticate` :256 and `install` :293/:300, never from `_expire`, `_idle_expiry` or `close`.
  - Token key set :212-223. SCOPE_MISMATCH :255-262. `page` :398.
  - `paging.normalize` binds `authority=scope` into `query_digest` (paging.py:51).
- **enforcement.py.** One limiter :76 (TIMEOUT :109). Unbounded `records` :77/:185. FORBIDDEN text :84-88.
- **config.py.** `test_execution_enabled` env factory :94-96. `http_*` tail :153-157 (pinned). `memex_root.mkdir` :206.
- **Impact and retrieval.**
  - `impact.serve` :654 opens the before side through `diff._open` (:192-203), a second `CodeGraph` plus `reader_lease`.
  - `diff._check_compatible` :212-228 requires the same root or the same git common dir.
  - `retrieval.retrieve` reads the manifest (:323-331) before the `repository` filter (:337-338).
- **SDK mcp 1.30.0** (scratch `s39-sdk/`):
  - `Server.request_handlers` is a public dict seeded with Ping (lowlevel/server.py:156-158).
  - The call_tool handler :527-592 catches Exception and returns `str(e)`.
  - `_get_cached_tool_definition` calls the ListTools handler with `None` (:482-490).
  - `_handle_request` maps McpError to error data; a missing handler gives -32601 (:777-803).
  - Auth pieces: `AccessToken` (auth/provider.py:40-47), `TokenVerifier` :97-100, `build_resource_metadata_url` (auth/routes.py:191-207), `ProtectedResourceMetadata` (shared/auth.py:159-180; `authorization_servers` min 1).
  - `RequireAuthMiddleware` emits free-text descriptions (bearer_auth.py:143-173), so it is not used.
- **Locked versions.** pyjwt 2.15.1 (uv.lock:703), cryptography 50.0.2 (:195); mcp requires `pyjwt[crypto]` (:438). Never `PyJWKClient`.
- **Pins** (test_s39_isolation.py):
  - :28-33 tools `94f9567a…`/1631472, graph output `713ec5b6…`, pyproject `981f0784…`, uv.lock `87ba265e…`, `SERVER_PY_SHA`.
  - :229-245 http.py import allow-list. :285-311 lazy import (`import atlas_aci.__main__` must not load `atlas_aci.server`). :314-319 `serve` params. :322-325 the `protocol/*.py` set. :328-337 the Config tail.
  - :373-414 http-transport.md tokens; every Claude Code/Copilot/Cursor line carries the untested phrase.
  - :431-446 CHANGELOG bullets[6..9] and "(ACI-S39)" once. :449-460 README rails+1 = http-transport, rails+2 = SETUP.md, `hosts/http-transport.md` exactly once.
  - Nothing pins the `query/*.py` set, Added bullets after index 9, or rows after README:954 (SETUP.md).
  - The CHANGELOG's last Added bullet is ACI-S14 at :396; `### Changed` is at :413.
- **S39 test support.**
  - `serving(app)` :507 uses `app.gate`, `app.running()` and `uvicorn_config`.
  - `asgi_call` :760 returns the receive count. `RawHttp.post` hard-codes `/mcp` (:316-332). `LeaseObserver` :469.
  - `HttpChild` parses a line containing `http_listening` plus `port=` (:628).
- **S30 test support.** `mapping_document(consumer_id="app", provider_id="lib")` (test_s30_support.py:148-178).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-06)
1. **Deployment model.** An operator-declared set of repositories in a closed JSON shared-config. Each repository gets a derived `Config`, its own `build_server_state` and an S39 `build_app`. The route is `/mcp/<repo_id>`, and the policy maps principal to repository ids.
   - Unknown and unauthorized repositories both get 403 (no enumeration); bad or absent credentials get 401 with `WWW-Authenticate` (`resource_metadata`).
   - Rejected: a `repository` tool argument or header; one Server plus a ContextVar router.
2. **Loopback-only bind is kept**; no network access is opened. "Shared" means multiple principals behind an operator reverse proxy that terminates TLS, rewrites Host to `127.0.0.1:<port>` and forwards Authorization. Bearer authentication is the only gate. The S39 pins and doc stay; the doc gains one pointer line.
3. **One IdP integration.** Generic OIDC JWT access tokens (RFC 9068 shape), verified offline with pyjwt against a pinned JWKS file.
   - `kid` is required and must be in the set. Algorithm allow-list RS256/PS256/ES256/EdDSA, matched to the key type.
   - `exp`, `iss`, `aud` and `sub` are required; `nbf` is honoured; leeway ≤ 60 s. `aud` contains the route's resource URL (RFC 8707). Token size cap 8 KiB, checked before decode. Principal = `(iss, sub)`.
   - The verifier conforms to `TokenVerifier` and is called from the Atlas router.
   - Protected Resource Metadata (RFC 9728) is served unauthenticated, using the SDK model.
   - The policy file is the source of truth; token scopes are optional narrowing only. Test keys are generated at test time.
4. **Guard layers**; `server.py` and `protocol/http.py` stay byte-identical. (a) An outer router before any body read. (b) Handler wrapping after `build_app`. (c) A `SharedEnforcement` subclass. Plus a per-principal lease quota and a forced `test_execution_enabled=False` (refuse to start if the environment enables it).
5. **Caches and cursors.** No result cache exists; the only evidence store is `LeaseRegistry._leases`.
   - `ScopedLeaseRegistry.scope()` = `digest({base, authz})`, read from a guard-set ContextVar and failing closed when it is unset.
   - The scope identity binds principal, repository, grant digest and policy epoch.
   - The epoch increments only when the policy digest changes; an invalid reload means deny-all.
   - `purge_revoked()` closes stale leases. No new cache is added.
6. **AC-005.** `impact`'s before-repository is the only served cross-repository expansion. A secondary pure test covers `authorize_path` over an S30 world's ids. S30 is never served.
7. **AC-006.** `decide()` is pure and frozen; its digest is identical on repeat, under shuffled policy order and in a fresh process.
8. **Module placement.** New modules go in `query/authz.py`, `query/authn.py` and `query/shared_http.py`. No prior test is edited; shared fields stay out of `Config`.
9. **Entry point.** A new `serve-shared --shared-config PATH` command with a lazy import (scope amendment). `serve` is untouched.
10. **Docs.**
    - `docs/operations/shared-http-deployment.md`.
    - One pointer line in `hosts/http-transport.md`.
    - One CHANGELOG bullet at the end of Unreleased/Added.
    - One README row after SETUP.md.
11. **Invariance.**
    - The tools JSON sha, the budgets 3436/3448/797/7730, `ev.RELATIONS` and the graph output sha are unchanged.
    - `server.py`, `http.py`, `codegraph.py`, `enforcement.py`, `config.py` and every producer input are byte-identical, as are pyproject and uv.lock.
    - The S38 goldens and S39 behaviour are unchanged. No repository writes; memex roots lie outside every repository.
12. **Dev image** `atlas-aci-dev-relock`.
13. **Production scenario** in the production image (G-PROD below).

### In
1. `query/authz.py`: policy model and loader, `decide`, `authorize_path`, scope identity, `PolicyStore`, `ScopedLeaseRegistry`, `PrincipalLimiter`, `SharedEnforcement` (D1-D4).
2. `query/authn.py`: `JwtVerifier`, `ResourceTokenVerifier`, resource metadata, `WWW-Authenticate` (D5).
3. `query/shared_http.py`: shared-config loader, `SharedRouter`, guard installation, `build_shared`, `run_shared` (D6-D9).
4. `__main__.py`: a new `serve-shared` command (D10), additions only.
5. Tests: `test_s40_{support,contract,policy,authn,access,isolation}.py`.
6. Docs: `docs/operations/shared-http-deployment.md` (new), one line in `hosts/http-transport.md`, the CHANGELOG bullet, the README row.
7. Evidence under `.spectra/evidence/aci-s40/`.

### Out
- Any non-loopback bind, TLS, an IdP, token introspection, JWKS fetching, OAuth authorization-server endpoints, DPoP and mTLS.
- Edits to `server.py`, `protocol/*`, `config.py`, `enforcement.py`, `codegraph.py`, `query/leases.py`, `tools/*`, `storage/*`, `cross_repo/*`, pyproject, uv.lock, the Dockerfile and workflows.
- Serving S30 over HTTP. A result cache. Per-principal memex. Thread offload.

## Approach

### Decisions

**D1 Shared-config schema** (`atlas-aci-shared.v1`). Closed JSON: unknown keys at any level are rejected. ≤ 1 MiB, UTF-8, no duplicate keys.
```json
{"format": "atlas-aci-shared.v1",
 "listen": {"host": "127.0.0.1", "port": 8765, "max_inflight": 8, "max_body_bytes": 1048576,
            "allowed_origins": []},
 "identity": {"issuer": "https://idp.example.com", "jwks_file": "/etc/atlas/jwks.json",
              "resource_base_url": "https://atlas.example.com/mcp", "leeway_seconds": 30,
              "max_token_bytes": 8192, "scopes_supported": []},
 "policy": {"file": "/etc/atlas/policy.json", "reload_interval_seconds": 2},
 "limits": {"max_calls_per_minute": 200, "max_calls_per_principal_per_minute": 120,
            "max_leases_per_principal": 2, "max_records": 1024},
 "repositories": [{"id": "repo-a", "repo": "/srv/a", "graph_root": "/srv/graphs/a",
                   "memex_root": "/var/lib/atlas/memex/a", "before_repo_id": "repo-a-base"}]}
```
- **Required:** `format`; `identity.{issuer, jwks_file, resource_base_url}`; `policy.file`; `repositories` (1..64), each with `{id, repo, graph_root, memex_root}`. Everything else is optional with the defaults shown.
- **Bounds:**
  - `listen` uses S39 `Config` validation (port 0 or 1024..65535) plus `resolve_loopback(host)`.
  - `leeway_seconds` 0..60; `max_token_bytes` 1024..16384; `scopes_supported` ≤ 32 RFC 6749 scope-tokens.
  - `reload_interval_seconds` 0..60 (0 = every request).
  - `max_calls_per_minute` 0..100000 (→ `Config.max_calls_per_minute`); per-principal 0..100000 (0 = off).
  - `max_leases_per_principal` 1..`Config.max_graph_leases` (4); `max_records` 0..65536.
- **Formats:**
  - `issuer` matches `^https://[^\s#?]+$` (≤ 2048 characters).
  - `resource_base_url` matches `^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/mcp$`.
  - `id` matches the S30 label `[a-z0-9][a-z0-9._-]{0,63}` and is unique.
- **Paths:**
  - All paths are absolute. `repo` is an existing directory.
  - `(repo, graph namespace)` pairs are unique; the same repo path with distinct graph roots is allowed (a before snapshot).
  - `memex_root` values are unique, and none lies inside any `repo` (`config.path_is_within`).
  - `before_repo_id` must name another registered id (`before_unknown`, `before_self`).
- **Refusals:** the environment `ATLAS_ACI_ENABLE_TEST_EXECUTION == "1"` → `test_execution_enabled`. The JWKS and the startup policy must load (`jwks_invalid`, `policy_invalid`).
- **Derived Config per entry:**
  ```python
  Config(repo, memex_root, graph_root=..., before_repo=B.repo, before_graph_root=B.graph_root,
         max_calls_per_minute=..., test_execution_enabled=False, http_host=<resolved literal>,
         http_port=..., http_max_inflight=..., http_max_body_bytes=..., http_allowed_origins=...)
  ```
  built through `dataclasses.replace` of one template. Then `impact.validate_configuration(derived)`; a failure is exit 1 with fixed text.
- `SharedConfigError(ValueError)` has `reason ∈ SHARED_CONFIG_REASONS` and `field` (a JSON pointer). `str(exc)` = `invalid shared configuration (<reason> at <field>)`; it never echoes values or file content.
- `SHARED_CONFIG_REASONS` = (unreadable, too_large, not_json, unknown_field, missing_field, wrong_type, out_of_range, bad_format, duplicate_id, not_absolute, not_directory, memex_inside_repository, duplicate_namespace, duplicate_memex, before_unknown, before_self, jwks_invalid, policy_invalid, not_loopback, test_execution_enabled).

**D2 Policy schema** (`atlas-aci-shared-policy.v1`). Closed, ≤ 1 MiB, ≤ 10000 grants.
```json
{"format": "atlas-aci-shared-policy.v1",
 "grants": [{"issuer": "https://idp.example.com", "subject": "alice", "repository": "repo-a",
             "tools": ["graph_query", "list_dir", "search_symbol", "search_text", "view_file"],
             "required_scope": "atlas.read"}]}
```
- **Grant fields:**
  - `issuer` must equal `identity.issuer` (`issuer_unknown`).
  - `subject` is 1..255 characters with no control characters.
  - `repository` must be registered (`repository_unknown`).
  - `tools` is non-empty, unique and ⊆ `SHAREABLE_TOOLS` = {view_file, list_dir, search_text, search_symbol, graph_query}. `test_dry_run` and `memex_read` give `tool_not_shareable`.
  - `required_scope` is optional and is one RFC 6749 scope-token.
  - `(issuer, subject, repository)` is unique (`duplicate_grant`).
- An empty `grants` list is valid and means deny-all.
- **Digests** (both `leases.digest`):
  - `policy_digest` covers the canonical document: grants sorted by `(issuer, subject, repository)`, tools sorted, absent `required_scope` → null.
  - `grant_digest` covers one canonical grant.
- `POLICY_REASONS` = (unreadable, too_large, not_json, unknown_field, missing_field, wrong_type, bad_format, issuer_unknown, repository_unknown, tool_not_shareable, duplicate_grant, too_many_grants).
- **Reload and epoch** (`PolicyStore`):
  - `refresh()` stats `(st_ino, st_size, st_mtime_ns, st_ctime_ns)` at most once per interval and re-reads only when the stat changes.
  - `current()` never does I/O.
  - The epoch starts at 1 and increments iff the effective digest changes. Deny-all uses the digest sentinel `"invalid"`, so invalid → valid always increments and old cursors never revive.
  - An unreadable or invalid reload → deny-all, plus one `shared_policy_invalid reason=` warning on the transition.
  - `MemoryPolicySource.set(document|None)` is for tests.

**D3 Authorization model** (`query/authz.py`, pure apart from the file sources).
```python
NEVER_SHARED = frozenset({"test_dry_run", "memex_read"})
SHAREABLE_TOOLS = READ_ONLY_TOOLS - NEVER_SHARED
DENIAL_REASONS = ("policy_unavailable", "repository_unknown", "repository_not_granted",
  "scope_not_presented", "tool_disabled", "tool_unknown", "tool_not_granted",
  "repository_argument_mismatch", "cross_repository_not_granted", "principal_missing")
KINDS = ("route", "tools/list", "tools/call", "resources/list", "resources/templates/list",
         "resources/read")
@dataclass(frozen=True) class Principal: issuer: str; subject: str
@dataclass(frozen=True) class Credential: principal: Principal; scopes: frozenset[str]
@dataclass(frozen=True) class Grant: repository_id: str; tools: frozenset[str]; required_scope: str | None; digest: str
@dataclass(frozen=True) class Registration: repository_id: str; repo: str; namespace: str; before_id: str | None
@dataclass(frozen=True) class PolicySnapshot:   # immutable view: policy + registry
    valid: bool; digest: str; epoch: int; registry_digest: str
    registrations: Mapping[str, Registration]; grants: Mapping[tuple[Principal, str], Grant]
@dataclass(frozen=True) class AccessRequest:
    kind: str; tool: str | None = None; op: str | None = None
    repository_argument: str = "absent"          # "absent" | "route" | "other"
@dataclass(frozen=True) class Decision:
    allowed: bool; reason: str; principal: Principal; repository_id: str; request: AccessRequest
    policy_digest: str; registry_digest: str; policy_epoch: int
    grant_digest: str | None; scope_identity: str | None
    def digest(self) -> str   # leases.digest of asdict with format "atlas-aci-authz-decision.v1"
@dataclass(frozen=True) class PathDecision: authorized: tuple[str, ...]; denied_at: str | None; reason: str
def classify(kind: str, name: str | None, arguments: object, route_repo: str) -> AccessRequest
def decide(credential: Credential, policy: PolicySnapshot, repository_id: str, request: AccessRequest) -> Decision
def authorize_path(credential: Credential, policy: PolicySnapshot, repository_ids: Sequence[str]) -> PathDecision
def scope_identity(principal: Principal, repository_id: str, grant_digest: str, policy_epoch: int) -> str
@dataclass(frozen=True) class AuthzContext: credential: Credential; repository_id: str; tools: frozenset[str]; policy_epoch: int; scope_identity: str
AUTHZ_CONTEXT: ContextVar[AuthzContext | None]   # default None; set only by the D7 guard
```
- **`classify`** is pure and never raises. For `graph_query`, `op` = `arguments["op"]` when it is a str in the eight structured ops, else None (legacy). `repository_argument` is set only for retrieve/context.
- **`decide`** applies rules in order; the first match wins:
  1. `not policy.valid` → `policy_unavailable`.
  2. id not registered → `repository_unknown`.
  3. No grant → `repository_not_granted`.
  4. `required_scope ∉ credential.scopes` → `scope_not_presented`.
  5. Kind route, tools/list, resources/list or templates/list → allow.
  6. resources/read → allow iff `graph_query ∈ grant.tools`, else `tool_not_granted`.
  7. tools/call:
     - `tool_disabled` (NEVER_SHARED), else `tool_unknown` (∉ READ_ONLY_TOOLS), else `tool_not_granted`;
     - `repository_argument == "other"` → `repository_argument_mismatch`;
     - `op == "impact"` and the registration has a `before_id` → `authorize_path(cred, policy, [route, before_id])` with `graph_query` required on each hop; on a deny → `cross_repository_not_granted`;
     - otherwise allow.
- **Allow results** carry `grant_digest` and `scope_identity` = sha256 of canonical `{"format": "atlas-aci-authz-scope.v1", "principal": {issuer, subject}, "repository_id", "grant_digest", "policy_epoch"}`. The identity is per principal, which is stricter than per scope.
- **`authorize_path`** decides each hop with kind `route` and stops at the first deny.

**D4 Registry and enforcement contracts** (`query/authz.py`).
```python
class ScopedLeaseRegistry(LeaseRegistry):
    def __init__(self, config, factory, *, repository_id: str, max_leases_per_principal: int)
    def scope(self) -> str                      # digest({"base": super().scope(), "authz": ctx.scope_identity})
    @asynccontextmanager async def pending(self) # per-principal quota, then super().pending()
    async def purge_revoked(self, policy: PolicySnapshot) -> int   # closes stale leases; returns count
    def held(self, principal: Principal) -> int
class PrincipalLimiter:                         # ONE instance shared by every repository
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic)
    def check(self, principal: Principal) -> None   # ToolError("TIMEOUT", "Rate limit exceeded for this principal.")
class SharedEnforcement(Enforcement):
    def __init__(self, config, *, repository_id: str, limiter: PrincipalLimiter, max_records: int)
    def assert_read_only(self, tool_name: str) -> None
    def assert_rate_limit(self) -> None
    def record(self, **kwargs) -> None
```
- **`scope()`.** If `AUTHZ_CONTEXT` is None or names another repository → `ToolError("SCOPE_MISMATCH", "Reference scope does not match.")` (fail closed). Each computed digest is mapped to `(principal, epoch)` in `_scope_owner`, which is pruned by the purge.
- **Unchanged base behaviour:** the token key set, HMAC, `authenticate` and `page`. So another principal's cursor or resource gives `SCOPE_MISMATCH` and another repository's gives `INVALID_REFERENCE` (instance).
- **Quota.** `pending()` runs synchronously before its first await: `self._expire()`; if `held(principal) + pending_by[principal] >= quota` → `ToolError("LEASE_CAPACITY", "No retained lease capacity.")`. It increments `pending_by` and decrements it in `finally`.
- **Purge.** `purge_revoked` (under `_lock`) closes every lease whose owner epoch ≠ `policy.epoch`, or whose principal has no grant on this repository.
- **SharedEnforcement:**
  - `assert_read_only`: a missing, foreign-repository or not-granted context, or a NEVER_SHARED tool, gives `ToolError("FORBIDDEN", FIXED_MESSAGES["FORBIDDEN"])`; then `super()`.
  - `assert_rate_limit`: no context → FORBIDDEN; then `limiter.check(principal)`; then `super()` (the repository-wide window).
  - `record`: `super().record`, then trim `records` to the last `max_records` (it stays a `list`).

**D5 Credential verification** (`query/authn.py`). No network, sockets or `PyJWKClient`.
```python
ALLOWED_ALGORITHMS = {"RS256": "RSA", "PS256": "RSA", "ES256": ("EC", "P-256"), "EdDSA": ("OKP", "Ed25519")}
AUTHN_REASONS = ("header_missing", "header_duplicate", "scheme_invalid", "token_oversize",
  "token_malformed", "header_invalid", "alg_not_allowed", "kid_missing", "kid_unknown",
  "key_type_mismatch", "signature_invalid", "expired", "not_yet_valid", "issuer_invalid",
  "audience_invalid", "claim_missing", "subject_invalid")
class AuthnError(Exception): reason: str            # str(exc) fixed text
@dataclass(frozen=True) class VerifiedToken: credential: Credential; audience: str; expires_at: int; client_id: str
class JwtVerifier:
    def __init__(self, *, issuer: str, jwks: Mapping[str, Any], leeway_seconds: int, max_token_bytes: int)
    @classmethod def from_file(cls, *, issuer, jwks_file: Path, leeway_seconds, max_token_bytes) -> JwtVerifier
    def verify(self, authorization: list[str], *, audience: str) -> VerifiedToken   # raises AuthnError
    def for_resource(self, audience: str) -> ResourceTokenVerifier
    counts: dict[str, int]                          # per AUTHN reason; never on the wire
class ResourceTokenVerifier:                        # conforms to mcp.server.auth.provider.TokenVerifier
    async def verify_token(self, token: str) -> AccessToken | None   # resource=audience, subject=sub, claims={"iss": iss}
def resource_url(base: str, repository_id: str) -> str          # base + "/" + id
def metadata_path(repository_id: str) -> str                    # "/.well-known/oauth-protected-resource/mcp/" + id
def metadata_url(base: str, repository_id: str) -> str          # origin(base) + metadata_path(id)
def metadata_body(base, repository_id, issuer, scopes_supported) -> bytes   # ProtectedResourceMetadata(...).model_dump_json(exclude_none=True)
def www_authenticate(base: str, repository_id: str, *, invalid: bool) -> bytes
```
- **JWKS load** (≤ 64 KiB, 1..32 keys, validated before `PyJWKSet.from_dict`):
  - each key has a unique str `kid`, `kty ∈ {RSA, EC, OKP}`, no private members (`d`, `p`, `q`, `dp`, `dq`, `qi`, `k`);
  - `use` is "sig" if present; `alg`, if present, is in the allow-list;
  - EC means `crv` P-256 and OKP means Ed25519; RSA modulus ≥ 2048 bits;
  - after loading, `len(set.keys) == len(doc["keys"])`, so no key is silently dropped.
- **Verify order:**
  1. Exactly one Authorization value (`header_missing`/`header_duplicate`), scheme `Bearer` case-insensitive with a single space (`scheme_invalid`).
  2. Length ≤ `max_token_bytes` (`token_oversize`).
  3. `^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$` (`token_malformed`; rejects an empty `none` signature).
  4. `jwt.get_unverified_header`: no `crit`, `jku`, `jwk`, `x5u` or `x5c` (`header_invalid`); `typ`, if present, ∈ {at+jwt, application/at+jwt, JWT} case-insensitive; alg ∈ allow-list (`alg_not_allowed`: none, HS*, others); `kid` str (`kid_missing`) and in the set (`kid_unknown`); the key's kty/crv match the alg and any JWK `alg` equals it (`key_type_mismatch`).
  5. `jwt.decode(token, key.key, algorithms=[alg], audience=audience, issuer=issuer, leeway=leeway, options={"require": ["exp", "iss", "aud", "sub"], "verify_signature": True, "verify_exp": True, "verify_nbf": True, "verify_aud": True, "verify_iss": True})`.
     - Exceptions map to: ExpiredSignature → `expired`; ImmatureSignature → `not_yet_valid`; InvalidIssuer → `issuer_invalid`; InvalidAudience → `audience_invalid`; MissingRequiredClaim → `claim_missing`; InvalidSignature → `signature_invalid`; any other `jwt.PyJWTError` → `token_malformed`.
  6. `sub` is a str of 1..255 printable characters (`subject_invalid`).
- `scopes` = split of a str `scope` claim (other forms → empty). `client_id` = `client_id` or `azp` or "".
- **Wire mapping:** `header_missing` → 401 `credentials_missing`; every other reason → 401 `credentials_invalid`.
- **`WWW-Authenticate`:** `Bearer resource_metadata="<metadata_url>"` when missing; `Bearer error="invalid_token", resource_metadata="<metadata_url>"` when invalid.

**D6 Router** (`SharedRouter.__call__`). The order is closed. `receive()` is never called before stage 9.

| # | stage | rejected when | status | reason (S39 body shape) | by |
| --- | --- | --- | --- | --- | --- |
| 0 | type | `scope["type"] != "http"` | RuntimeError | - | R |
| 1 | peer | not loopback (`http._is_loopback_peer`) | 403 | peer_not_loopback | S39 `_reject` |
| 2 | host | sub-app `_host_allowed` false | 421 | host_not_allowed | S39 `_reject` |
| 3 | origin | sub-app `_origin_allowed` false | 403 | origin_not_allowed | S39 `_reject` |
| 4 | route | path is `metadata_path(<label>)`: GET → 200 PRM, any label, unauthenticated, registry not consulted; other method → 405 `metadata_method_not_allowed` (`Allow: GET`). Path not `^/mcp/<label>$` → 404 `route_not_found` | 200/405/404 | see left | R |
| 5 | method | not POST | 405 | method_not_allowed (`Allow: POST`) | S39 `_reject` |
| 6 | credentials | `verifier.verify(..., audience=resource_url(base, id))` raises | 401 | credentials_missing / credentials_invalid + `WWW-Authenticate` | R |
| 7 | policy | `store.refresh()`; on an epoch change, `await purge_revoked` on every registry | - | - | R |
| 8 | authorization | `decide(cred, store.current(), id, AccessRequest("route"))` denies (unknown id included) | 403 | repository_forbidden | R |
| 9 | delegate | child = `dict(scope)` with `path="/mcp"`, `raw_path=b"/mcp"`, `PRINCIPAL_SCOPE_KEY = cred`; `await apps[id](child, receive, send)`. The S39 stages run again, then the shared gate (503) and the SDK (413/406/415/400) | - | - | sub-app |

- `SHARED_REJECTION_REASONS = ("route_not_found", "metadata_method_not_allowed", "credentials_missing", "credentials_invalid", "repository_forbidden")`.
- Codes and fixed messages:
  - route_not_found: 404, -32600, "Not found.";
  - metadata_method_not_allowed: 405, -32600, "Method not allowed; use GET.";
  - credentials_missing: 401, -32600, "Authentication required.";
  - credentials_invalid: 401, -32600, "Invalid credentials.";
  - repository_forbidden: 403, -32600, "Access to this repository is not allowed.".
- Headers: `content-type`, `content-length`, `connection: close`.
- `PRINCIPAL_SCOPE_KEY = "atlas_aci.shared_principal"`. `router.rejected: Counter[reason]`.
- The router also exposes `gate` (one shared `InflightGate(listen.max_inflight)`, assigned to every `app.gate`), `allow_host_name(name)` (forwarded to every app), `running()` (an AsyncExitStack over every `app.running()`, then `aclose_state()` of every app in `finally`) and `aclose_state()`. So S39 `uvicorn_config(cast("AtlasHttpApp", router), …)` and the S39 `serving()` fixture work unchanged.

**D7 Guards** (`install_guards(app, repository_id, store)`, run right after `build_app`).
- **Tripwire, else `RefusedError` before serving:**
  - `importlib.metadata.version("mcp") == VERIFIED_SDK[1]`;
  - `set(app.server.request_handlers) == {Ping, ListTools, CallTool, ListResources, ListResourceTemplates, ReadResource}`;
  - `app.server.notification_handlers == {}`.
- Every non-Ping handler is replaced by `guarded(original, kind)`, marked `__atlas_guard__ = kind`.
- **Principal lookup:** `server.request_context.request.scope[PRINCIPAL_SCOPE_KEY]`. A missing request, scope key or LookupError → `principal_missing` (a deny).
- **Common path:** `decision = decide(cred, store.current(), repository_id, classify(kind, name, args, str(config.repo)))`. On allow → `token = AUTHZ_CONTEXT.set(AuthzContext(...))`, `try: return await original(req) finally: AUTHZ_CONTEXT.reset(token)`.
- **Denials** (`guard.denied: Counter[reason]`):
  - **tools/call** → `ServerResult(fixed_result("FORBIDDEN", mode=m or "legacy", structured=s, operation=o))`. `m`, `s` and `o` are derived exactly as `call_tool_result` :611-628 (pre-validation formula; `s` from `negotiate(header or DEFAULT_NEGOTIATED_VERSION).revision() in STRUCTURED_VERSIONS`). These bytes equal the base FORBIDDEN result.
  - **resources/read** → `raise McpError(error_data("FORBIDDEN"))` (-32602, "Resource request rejected.").
  - **tools/list, resources/list, templates** → `raise McpError(ErrorData(code=-32600, message="Request not authorized."))`.
  - **ListTools called with `req is None`** (the SDK cache refresh inside a guarded call): allowed iff `AUTHZ_CONTEXT` is set for this repository; otherwise RuntimeError("Shared request is not authorized.").
- tools/list output is unchanged: 7 tools; the disabled tools are listed but denied (documented).

**D8 Access-path matrix.**
- Layers: R = router, G = guard, E = SharedEnforcement, L = ScopedLeaseRegistry, SDK.
- In the first three columns the response arrives with `withhold_body`, `asgi_call` receives == 0, and every repository observer is 0.

| request kind | no token | bad token | unauthorized / unknown repo | authorized (outcome, layer) |
| --- | --- | --- | --- | --- |
| initialize | 401m R6 | 401i R6 | 403 R8 | 200 InitializeResult (SDK session; no Atlas handler) |
| ping | 401m | 401i | 403 | 200 `{}` (SDK `_ping_handler`) |
| notifications/initialized, cancelled | 401m | 401i | 403 | 202 (SDK; no notification handlers) |
| tools/list | 401m | 401i | 403 | 200, 7 tools, bytes = base manifest (G allow) |
| tools/call view_file / list_dir / search_text / search_symbol | 401m | 401i | 403 | granted → result (G, E pass); not granted → isError FORBIDDEN (G; E backstop) |
| tools/call graph_query status/neighborhood/path/legacy | 401m | 401i | 403 | granted → result; not granted → FORBIDDEN (G) |
| graph_query retrieve/context | 401m | 401i | 403 | `repository` absent or route path → result; other → FORBIDDEN (G), zero leases |
| graph_query page/continue (cursor) | 401m | 401i | 403 | own cursor → page; another principal's → isError SCOPE_MISMATCH (L); another repo's → INVALID_REFERENCE (L); stale epoch → SCOPE_MISMATCH (L) |
| graph_query impact | 401m | 401i | 403 | before not granted → FORBIDDEN (G, zero leases on route and before); both granted → result; no before → IMPACT_UNCONFIGURED (base) |
| test_dry_run, memex_read | 401m | 401i | 403 | isError FORBIDDEN always (G; E; Config refuses execution) |
| unknown tool name | 401m | 401i | 403 | isError FORBIDDEN, bytes = base (G) |
| resources/list | 401m | 401i | 403 | 200 `[]` (G allow) |
| resources/templates/list | 401m | 401i | 403 | 200 template (G allow) |
| resources/read | 401m | 401i | 403 | own URI → blob; another principal's → -32602 SCOPE_MISMATCH (L); no graph_query grant → -32602 FORBIDDEN (G) |
| unknown method | 401m | 401i | 403 | -32601 (SDK, no handler) |
| GET/DELETE `/mcp/<id>` | 405 R5 | 405 | 405 | 405 (method is not secret) |
| GET PRM path | 200 R4 | 200 | 200 (any label) | 200 |
| `/mcp`, `/x`, `/mcp/<id>/`, bad label | 404 R4 | 404 | 404 | 404 |

**D9 `build_shared` and `run_shared`.**
- **`build_shared(deployment, *, policy_source=None, verifier=None) -> SharedRouter`.** For each entry, in id order:
  1. derive the `Config`;
  2. `enforcement = SharedEnforcement(cfg, repository_id=id, limiter=shared, max_records=…)`;
  3. `memex = Memex(cfg.memex_root)`;
  4. `code_graph = CodeGraph(repo=cfg.repo, read_only=True, query_limit=cfg.max_bound_field_elements + 1, graph_root=cfg.graph_root)` — identical to `build_server_state` :591-596, with a parity test;
  5. `code_graph._lease_registry = ScopedLeaseRegistry(cfg, code_graph, repository_id=id, …)` before any request;
  6. `app = build_app(cfg, (enforcement, memex, code_graph))`;
  7. `install_guards`;
  8. `app.gate = shared_gate`.
  Any failure closes every state built so far.
- **`run_shared(deployment, *, on_listening=None, install_signals=True)`** mirrors `run_http` :594-639 from http.py helpers (`resolve_loopback`, `AtlasUvicornServer`, `uvicorn_config`, `_serve`, `_stop`, `_listening_addresses`, `_is_loopback_literal`):
  - SDK check → RefusedError; any derived `test_execution_enabled` → RefusedError; the post-bind loopback check.
  - Logs only `shared_http_listening host= port= repositories=<n> policy_epoch=` and `shared_http_stopped admitted= rejected= peak=`, plus the D2 transition warning.
  - The request path does no other logging; its only I/O is the throttled policy `stat`/read.

**D10 CLI** (`serve-shared`, appended to `__main__.py`, additions only).
```python
@cli.command("serve-shared")
@click.option("--shared-config", "shared_config", required=True,
              type=click.Path(dir_okay=False, path_type=Path))
def serve_shared(shared_config: Path) -> None:
    """Serve registered repositories to authenticated principals over loopback HTTP (shared mode)."""
```
- The body does the lazy `from atlas_aci.query.shared_http import …`, then `load_shared_config(shared_config, environ=os.environ)`, then `asyncio.run(run_shared(dep))`.
- **Usage errors** (exit 2, nothing bound, empty stdout):
  - (V1) option missing (click);
  - (V2) `SharedConfigError` → `--shared-config: invalid shared configuration (<reason> at <field>)`;
  - (V3) test execution → `shared serving is not available while test execution is enabled (ATLAS_ACI_ENABLE_TEST_EXECUTION=1)`;
  - (V4) `LoopbackError` → `--shared-config: listen.host must resolve only to loopback addresses (reason: R)`;
  - (V5) `RefusedError` → its fixed text.
- **Exit 1:** `validate_configuration` failure → `repository <id>: graph index is unavailable or misconfigured` (never `str(exc)`); bind failure → S39's fixed OSError text.

**D11 Docs.**
- **`docs/operations/shared-http-deployment.md`** (ASCII; none of {certified, guaranteed, proven, seamless, universal}). Sections:
  1. Summary and trust boundary.
  2. Shared-config table (every D1 field, its default and bound).
  3. Policy format (D2) and its digest and epoch.
  4. JWKS file and key rotation (add the new key, re-issue tokens, then remove the old key; restart required, since the JWKS is read once).
  5. Token profile (D5), every `AUTHN_REASONS` value and the two `WWW-Authenticate` forms.
  6. Router table (D6), every `SHARED_REJECTION_REASONS` value, and PRM.
  7. Guard rules and the D8 matrix.
  8. Revocation semantics: effective at the first request after `reload_interval_seconds`; every policy change invalidates every cursor (safe over-invalidation); in-flight requests finish; retained leases are purged on the next request or expire within 300 s.
  9. The `serve-shared` CLI and V1-V5.
  10. Not isolated: one process and memory; synchronous ops block the loop for every principal; timing side channels; operator-visible `Enforcement.record` log arguments; the server `repository` path is visible in retrieve/context outputs to authorized principals; tools/list advertises disabled tools; memex is per repository, not per principal; S30 is offline only; `test_dry_run`/`memex_read` are disabled; per-route token audience.
  11. Reverse-proxy requirements (TLS, Host rewrite to `127.0.0.1:<port>`, forward Authorization, no `--network host` surprises).
- **`hosts/http-transport.md`.** Append one line to §8: "Multi-principal shared operation (S40, `atlas-aci serve-shared`) keeps this loopback-only bind; see `docs/operations/shared-http-deployment.md`." No host names; every S39 token is kept.
- **CHANGELOG.** One bullet after the ACI-S14 bullet, before `### Changed`, starting `- **Shared multi-principal HTTP mode (ACI-S40).**`. Continuation lines are indented. It names `serve-shared` and `docs/operations/shared-http-deployment.md`, and carries no other `(ACI-S` token.
- **README.** One row directly after the SETUP.md row (:954): `| [\`docs/operations/shared-http-deployment.md\`](docs/operations/shared-http-deployment.md) | <one cell> |`, with 3 pipes and no `hosts/http-transport.md`.

**D12 Test harness** (`test_s40_support.py`; stdlib-only top level; atlas, jwt and cryptography imported lazily; no test functions).
- **`Keys`:** test-time RSA-2048, EC P-256 and Ed25519 keys and a JWKS dict. `mint(principal, *, aud, iss, exp, nbf, kid, alg, extra, header)` signs with `jwt.encode`. Raw forgers produce `alg=none` and an HS256 token keyed with the RSA public PEM.
- **`Deployment(tmp)`:** builds repos with `test_s38_support.build_fixture` or `test_s19_support.write_tree/build`, writes shared-config, policy and JWKS, and returns `load_shared_config`. Its `policy(...)` rewrites the file atomically (temp file plus `os.replace` in the same dir).
- **`SharedHttp(RawHttp)`:** overrides `post`/`open_stream` to use `/mcp/<id>` plus `Authorization`.
- **Observers** (class-level, S39 H3 precedent):
  - `LeaseObserver` keyed by `reader.repo` plus graph namespace;
  - `CodeGraph.__init__`, `CodeGraph.source_lease`, `SourceLease.read_span`;
  - `ScopedLeaseRegistry.scope`/`authenticate`, which record the AUTHZ identity per call;
  - counting wrappers on `atlas_aci.server.{view_file, list_dir, search_text, search_symbol, graph_query, test_dry_run}`;
  - `Memex.read`; `atlas_aci.query.retrieval.retrieve`; `atlas_aci.query.traversal.navigate` at its import sites;
  - a fake `rg`/`pytest` on PATH writing a marker.
- Every zero is paired with the same observer reading ≥ 1 in a positive control.
- `max_calls_per_minute=0` except in the limiter tests. Every wait is bounded. `production_main(argv)` drives G-PROD.

### Acceptance-test mapping (`test_s40_contract.py`; no mock of the behaviour under test)
| AC | Test | Steps and observers |
| --- | --- | --- |
| 001 | test_unauthorized_before_traversal | **Setup.** Deployment {A (LIVE), B (mini)}. P1 is granted A+B; P2 is granted B only. **(a)** P2 → `/mcp/A`: `retrieve` text "B", neighborhood and `page`, each with `withhold_body` → 403 `repository_forbidden`; `asgi_call` receives == 0; A leases, `CodeGraph.__init__` (after startup), `retrieve`/`navigate` and `read_span` == 0. **(b)** P2 → `/mcp/B` `retrieve repository=<A path>` → isError FORBIDDEN; leases on A **and** B == 0; retrieve == 0. **Control.** P1 → `/mcp/A` retrieve → lease 1, retrieve 1, `work` counts > 0, `repository_filter_matched` true. |
| 002 | test_resource_authorization | P1 pages on A and takes a resource URI. P2 (granted A) reads it → -32602 reason SCOPE_MISMATCH, `read_span` 0. P1's URI on `/mcp/B` by P1 → INVALID_REFERENCE. P3 (no grant) → 403 before body. A tampered `atlas-source://` id → INVALID_REFERENCE. P2 granted A without graph_query → -32602 FORBIDDEN (G) with `authenticate` 0. **Control.** P1 → blob bytes == the source span; `read_span` 1. |
| 003 | test_cache_scope_isolation | P1 and P2 (both granted A) send byte-identical `page` POSTs. For each, every `scope()` call during pending/install/authenticate used `AUTHZ.scope_identity == decide(...).scope_identity` of that principal; identities differ; the returned `query_digest` differs. P2 `continue` with P1's cursor and digest → SCOPE_MISMATCH. **Control.** P1 continues its own → page bytes. `_leases` holds 2 leases with distinct `.scope`. |
| 004 | test_revoked_cursor | P1 pages on A (cursor c, epoch e). (i) Policy drops P1's A grant → continue → 403; `usage()["active"]` falls by 1 (purge). (ii) P1 re-pages, then an unrelated grant changes (epoch e+1) → continue c' → SCOPE_MISMATCH. (iii) Revoke, observe, re-grant identical bytes → epoch e+3 → continue → SCOPE_MISMATCH, never revived. **Control.** Rewriting identical bytes (new mtime, same digest) → epoch unchanged → continue OK. `reload_interval_seconds=0`. |
| 005 | test_cross_repo_scope | `same_path_pair`: C built at v1 (graph root gC), A at v2 (gA), same path, `A.before_repo_id = C`. P1 is granted A only; `impact` on `/mcp/A` → isError FORBIDDEN; leases on C == 0 **and** A == 0; `CodeGraph(repo, gC)` constructions 0. **Control.** P2 granted A+C → impact ok; C lease 1, A lease 1; `before.generation_id == C`'s. **Secondary.** `test_s30_support.make_world`: policy grants `app` only → `authorize_path(cred, policy, [consumer_id, provider_id]) == PathDecision(("app",), "lib", "repository_not_granted")`; granting both → `denied_at is None`. |
| 006 | test_auth_replay | 3 principals × 3 repos × every `AccessRequest` kind/tool/op: `decide` twice → equal `digest()`. Reversed grants and shuffled tools → equal policy digest and decisions. A fresh interpreter (PYTHONHASHSEED 0 and 4242) prints equal digests. **HTTP.** The same denied and allowed POSTs twice → byte-identical bodies. **Non-vacuity.** Changed tools, a different principal or a different repo → different digest. |
| 007 | test_cache_revocation | P1 warms a `page` on A (record the fixture entity ids and names). Revoke → repeat the same POST → 403; the body has none of the warmed ids or names; leases 0; `usage()["active"] == 0`; `_scope_owner` pruned. Variant: graph_query removed from the grant → page and continue → FORBIDDEN, zero leases. **Control.** Before revocation, a repeat gives the evidence. |
| 008 | test_legacy_tool_boundary[tool×case] | 7 tools × {P2 not granted on A (403, R); P1 with the tool not granted (FORBIDDEN, G); `SharedEnforcement` direct via `dispatch_tool_call` with AUTHZ unset or foreign (FORBIDDEN, E)}, plus `test_dry_run`/`memex_read` for a fully granted P1. Every tool-entry counter, lease, `source_lease`, `Memex.read`, the fake-binary marker and `Path.iterdir`/`read_bytes` under the repo == 0. **Control.** Granted P1 → each shareable tool counter ≥ 1. Outside shared mode, `dispatch_tool_call` with `Config(test_execution_enabled=True)` and a plain `Enforcement` → marker written; a memex ref → `Memex.read` 1. |
| 009 | test_invalid_credentials[case×kind] | Credential cases: absent, `Basic`, empty bearer, duplicate header, 8193 bytes, malformed, expired, nbf + 120 s, wrong aud (B's URL on A), wrong iss, unknown kid, `alg=none`, HS256 with the public key, missing sub, untrusted key with a known kid. Request kinds: initialize, tools/list, tools/call, resources/read. Each → 401, `WWW-Authenticate` exactly per D5, `withhold_body` answered, receive 0, leases 0, `CodeGraph.__init__` 0; `verifier.counts` incremented the expected reason. **Control.** A valid token → 200 and lease 1. |
| RUN-01 | gate | SHA-256 manifest of the declared tracked files equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` on those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` equal in the interrupted and resumed receipts; one receipt, `.spectra/state/aci-s40.execution.json`. |

### Supporting tests
- **`test_s40_policy.py`:**
  - D1/D2 schema tables: every reason with its `field`.
  - Digest canonicalization; epoch transitions (valid→valid same digest, changed, invalid, back to valid).
  - The stat throttle with an injected clock; `MemoryPolicySource`.
  - `decide` rule-order table, including `scope_not_presented`.
  - `ScopedLeaseRegistry` fail-closed (unset or foreign context), the quota (`LEASE_CAPACITY` at quota+1, others unaffected), `purge_revoked` counts.
  - `PrincipalLimiter` window; `SharedEnforcement` trim; FORBIDDEN text equals `FIXED_MESSAGES`.
- **`test_s40_authn.py`:**
  - JWKS validation table (oct key, private member, duplicate kid, P-384, RSA-1024, silently-dropped key).
  - Every `AUTHN_REASONS` case; RS256/PS256/ES256/EdDSA positives; leeway at ±60 s.
  - `typ` values; header `jku`/`jwk`/`crit`; `aud` as a list.
  - `ResourceTokenVerifier.verify_token` → AccessToken fields.
  - `metadata_body` parses as `ProtectedResourceMetadata`, with `resource == resource_url`.
- **`test_s40_access.py`:**
  - The full D8 matrix, parametrized, in-loop through `serving(router)`.
  - The router stage-order table: bad peer via `asgi_call`; bad Host plus no token → 421; GET `/mcp/A` with no token → 405; `/mcp/` → 404; PRM for an unregistered label → 200.
  - Denial bytes: a guard FORBIDDEN == `call_tool_result("no_such_tool")` bytes at both a modern and a legacy revision.
  - Guard tripwires: an extra handler or notification handler, or a version mismatch → RefusedError.
  - `req=None` ListTools inside and outside a guarded call; `principal_missing` (sub-app called without the router).
  - Shared gate 503 across two repos.
  - `build_shared` parity with `build_server_state` (`query_limit`, `read_only`).
  - The CLI: V1-V5 exit 2 with empty stdout; exit 1 fixed text; `serve-shared --help`.
  - `run_shared` with a subprocess `serve-shared` on port 0, then SIGTERM → exit 0 and `shared_http_stopped`.
- **`test_s40_isolation.py`:**
  - G-MCP in a fresh interpreter importing `atlas_aci.query.shared_http` first: the tools sha/bytes, 7 tools, budgets, `ev.RELATIONS`, graph output sha.
  - The sha of `server.py` == `SERVER_PY_SHA`; the sha of `http.py`, `config.py` and `enforcement.py` == the Day-1 base constants.
  - AST checks on the 3 modules: no `subprocess`, `tempfile`, `sqlite3`, `socket`, `ssl`, `http.client`, `urllib.request`, `httpx` or `requests`; no `PyJWKClient`, `print`, `exec` or `eval`; `open` only inside the named loader functions.
  - Reason tuples are closed and unique; `SHARED_REJECTION_REASONS` is disjoint from `http.REJECTION_REASONS`.
  - Lazy import: `import atlas_aci.__main__` loads none of `atlas_aci.query.{shared_http,authn,authz}`, `jwt` or `atlas_aci.server`.
  - `serve-shared` params == {"shared_config"}; `serve` params unchanged.
  - Tripwire: `requires("mcp")` names pyjwt; pyjwt 2.15.1, cryptography 50.0.2.
  - Docs: the doc is ASCII, has no banned words, and names every reason, field, default, `serve-shared` and each not-isolated item.
  - http-transport.md names `docs/operations/shared-http-deployment.md` once.
  - CHANGELOG: `### Security` first; the S40 bullet is the last bullet before `### Changed`; `(ACI-S40)` appears once.
  - README: the row directly after the SETUP.md row, 3 pipes.
  - `test_s40_*.py` set equality.

### Gates (dev image `atlas-aci-dev-relock`, `uv run --frozen`; logs and exit files under `.spectra/evidence/aci-s40/`)
- **G-S40:** `pytest tests/slices/test_s40_contract.py -q`, then `pytest tests/slices/test_s40_*.py -q`.
- **G-REG** (after the docs edits): `tests/slices/test_s13_*.py tests/slices/test_s17_*.py tests/slices/test_s18_*.py tests/slices/test_s30_*.py tests/slices/test_s38_*.py tests/slices/test_s39_*.py tests/slices/test_s3[3-9]_isolation.py tests/slices/test_s12_admission.py tests/test_server*.py tests/test_thesis_negatives.py`.
- **G-LINT, exactly as CI** (ci.yml:117-139): `ruff check src/ tests/`; `ruff format --check src/ tests/`; `mypy src/`; `ruff check ../scripts/ --exclude fingerprint-fixture`; `ruff format --check ../scripts/ --exclude fingerprint-fixture`; `mypy ../scripts/`.
- **G-FULL:** `pytest -q` with the S39 deselects. No new failure or skip compared with `base-invariants.json`.
- **G-SRC:**
  - `git diff --exit-code f4f9f9f -- mcp-server/src ':!mcp-server/src/atlas_aci/query/authz.py' ':!mcp-server/src/atlas_aci/query/authn.py' ':!mcp-server/src/atlas_aci/query/shared_http.py' ':!mcp-server/src/atlas_aci/__main__.py' mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile .github/workflows scripts atlas`;
  - explicitly: `server.py`, `protocol/http.py`, `codegraph.py`, `enforcement.py`, `config.py`, `query/leases.py`;
  - `--name-status` over src == {A authz.py, A authn.py, A shared_http.py, M `__main__.py`}; `__main__.py` `--numstat` deletions == 0;
  - tests ⊆ A `test_s40_*.py`; hosts == {M http-transport.md, 0 deletions}; docs == {A docs/operations/shared-http-deployment.md}; root ⊆ {CHANGELOG.md, README.md}, 0 deletions.
- **G-MCP:** `atlas-aci tools` sha `94f9567a…` / 1631472 bytes.
- **G-S39-PRESERVE:**
  - `test_s39_*.py` green, unedited;
  - S38 capture (seeds 0 and 4242) `cmp`-equal to the committed `tests/fixtures/s38/transcripts/*` and to a base-worktree capture;
  - `test_s38_contract.py` green.
- **G-HARDEN:** `bash scripts/harden-gate-classify.sh f4f9f9f HEAD` prints `augmented=false a3_touched=false`.
- **G-PY314:** `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s40_*.py tests/slices/test_s39_isolation.py -q'`. Passed counts, no skips.
- **G-DRIFT:** `ramza-drift` with the declared scope.
- **G-PROD** (`.spectra/evidence/aci-s40/production-scenario.sh IMG`, POSIX sh, `set -eu`):
  - **Containers.** Every container is `--rm`, named `s40-$$-N`, cleaned up by `trap 'docker rm -f … || true' EXIT INT TERM`. A host-side `bounded SECS NAME …` watchdog does `docker kill` → FAIL 124. `--entrypoint` is always explicit.
  - **H1:** image identity plus the sha256 of the 4 changed and 5 frozen src files equal to the host values; pyjwt, cryptography and mcp versions.
  - **H2:** host-side `index` of repos A and B (written with printf) → host sha256 manifests.
  - **H3, the driver:** `--network none --read-only --tmpfs /tmp -e PYTHONDONTWRITEBYTECODE=1 -v A:/repo-a:ro -v B:/repo-b:ro -v graphs:/graphs:ro -v tests/slices:/s40:ro --entrypoint /usr/local/bin/python IMG -I -c '…test_s40_support.production_main(...)'`, running as user `atlas` with no pytest and no `timeout`. In `/tmp` it generates keys, the JWKS, the policy and the shared-config (memex under `/tmp`), starts `atlas-aci serve-shared` as a child, parses `port=`, and runs E1-E10 (one JSON line each):
    - E1: no token, body withheld → 401 plus `resource_metadata`; a valid token → 200.
    - E2: expired, `alg=none` and wrong-aud tokens → 401.
    - E3: P2 on B and an unknown id → 403 with identical bodies; P1 on B → 200.
    - E4: P1 pages on A; P2's `continue` → SCOPE_MISMATCH; P1's → OK.
    - E5: policy swap removes P1's A grant → P1 continue and page → 403; P2 on A still → 200.
    - E6: P1 test_dry_run and memex_read → isError FORBIDDEN.
    - E7: `ATLAS_ACI_ENABLE_TEST_EXECUTION=1 serve-shared` → exit 2.
    - E8: `/proc/net/tcp` has exactly one row `"0100007F:%04X" % port` and tcp6 has none; `listen.host 0.0.0.0` → exit 2.
    - E9: PRM GET → 200 with `resource` equal to the URL.
    - E10: SIGTERM → exit 0 within 10 s, `shared_http_stopped`, no Traceback.
  - **H4:** the repo and graph manifests are unchanged.
  - **H5:** `IMG tools` sha and bytes equal the pin.
  - **Output.** It writes `production-scenario.json` and exits 0 only if everything passes.

### Work plan (timebox 5 engineering days)
**Day 1 AM: qualification**, before any src edit. Outputs: `qualification.json` and `base-invariants.json`; spikes live in scratch.
- (a) `git diff --quiet f4f9f9f -- mcp-server/src`. Record:
  - versions: mcp 1.30.0, pyjwt 2.15.1, cryptography 50.0.2, uvicorn 0.44.0, starlette 1.3.1, sse-starlette 3.3.4;
  - `requires("mcp")` ∋ `pyjwt[crypto]`;
  - the pyproject and uv.lock shas; `HTTP_PY_SHA`, `CONFIG_PY_SHA` and `ENFORCEMENT_PY_SHA` from `git show f4f9f9f:…`;
  - that pyjwt, cryptography and key generation work in the production image under `--network none` and in `python:3.14`.
- (b) pyjwt spike with RSA, EC and Ed25519 keys:
  - `PyJWKSet.from_dict` (`key_id`, `key_type`, `algorithm_name`; whether oct and private keys load or are silently dropped);
  - `decode(options={"require": [...]})` with the exact exception class per D5 reason; list `aud`; leeway; `iat` handling;
  - PS256 against an RSA JWK without `alg`;
  - `alg=none` and HS256-with-public-PEM tokens rejected both by the D5 pre-checks and by `decode(algorithms=["RS256"])`.
- (c) SDK: after `build_app(cfg, state)`, the exact `request_handlers` and `notification_handlers` sets.
  - A replaced CallTool handler is honoured over in-process HTTP.
  - ListTools(None) runs inside call_tool when `_tool_cache` is empty.
  - `request_context.request.scope` is the router's child dict.
  - A ContextVar set in the replaced handler is visible inside `registry.pending`/`install`/`authenticate` for page, continue, context and resources/read.
- (d) Spike of `ScopedLeaseRegistry` (scratch subclass):
  - P1's cursor fails under P2 (SCOPE_MISMATCH) and works under P1;
  - an unset context fails closed;
  - with `graph_lease_seconds=1` the expiry timer runs and never calls `scope()`.
- (e) Two `build_app` instances plus a shared gate behind a scratch router on one in-loop uvicorn. S39 `serving(router)` works; both repos answer tools/list and a page; mypy accepts the `cast`.
- (f) A same-path pair registered as A and C passes `validate_configuration`; shared impact gives C lease 1.
- (g) Base green: G-REG, G-S39-PRESERVE capture, full-suite counts.
- (h) `ProtectedResourceMetadata(...).model_dump_json` bytes for a `/mcp/<id>` resource, and any URL normalization.

**STOP RULE** (return to the orchestrator; never adapt silently):
- (a) any version or sha mismatch, or pyjwt unusable in the image or on 3.14;
- (b) pyjwt accepts `alg=none` or HS-confusion under the D5 options, `require` is unsupported, or a malformed JWKS key is silently dropped with no detectable count;
- (c) the handler set differs, replacement is not honoured, the scope identity is false, or the ContextVar is not visible;
- (d) any cross-principal authenticate success, or a timer `scope()` call;
- (e) two repositories cannot be served in one process;
- (g) anything red at base.

**Recorded adaptations, not stops:**
- (f) fails → the AC-005 control asserts C lease 1 plus a closed impact rejection, not success;
- (h) normalization → `resource` compares after pydantic normalization (documented);
- (b) iat in the future rejected or accepted → documented as observed.

**Day 1 PM:** `authz.py` (D2-D4) and `test_s40_policy.py`.
**Day 2:** `authn.py` (D5), `test_s40_authn.py`, the router and guards (D6, D7), the tripwires.
**Day 3:** `build_shared`/`run_shared`/CLI (D9, D10); `test_s40_support.py`; AC-001/002/003/009; the D8 matrix.
**Day 4:** AC-004/005/006/007/008; `test_s40_isolation.py`; docs, CHANGELOG, README, pointer. **Checkpoint with a clock reading:** if any never-cut item is red, return to the orchestrator; do not extend.
**Day 5:** G-REG, G-LINT, G-FULL, G-SRC, G-MCP, G-S39-PRESERVE, G-HARDEN, G-PY314, G-PROD; RUN-01/02; the receipt.

**Cut order** (each cut becomes a documented limitation):
1. The README row.
2. The `typ` header check.
3. The `max_records` trim (documented as unbounded).
4. The per-principal rate limiter (the repository-wide limiter stays).
5. The per-principal lease quota.
6. Stat throttling (stat on every request).
7. The S30 `authorize_path` world test (the pure-table test stays).
8. G-PROD E9 (PRM).

**Never cut:**
- the 9 AC tests with positive controls;
- the D6 order with 401/403 before any body read;
- the guard rules, including the repository-argument and impact before-repository rules;
- `SharedEnforcement`'s principal re-check;
- `ScopedLeaseRegistry` with fail-closed and purge;
- disabled `test_dry_run`/`memex_read` and the test-execution refusal;
- every D5 negative; loopback only; the tripwires;
- G-SRC, G-MCP, G-S39-PRESERVE and G-PROD E1-E8 and E10;
- the docs' not-isolated section.

### Independent checker conditions
Verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s40-plan-checker`, independent read-only security review with the mcp 1.30.0 source; 1 BLOCKER, 6 MAJOR, 8 MINOR). Ramza's complexity verdict was `human_loop`; under the user's autonomous-execution authorisation this review is the substitute human gate. Every finding is accepted as a binding plan condition with the same number; where a condition conflicts with plan text above, the condition wins.

Orchestrator resolutions (stricter option chosen each time):
- Condition 1 (BLOCKER, ripgrep option injection, pre-existing on main): BOTH layers, never cut.
  (a) Root-cause fix by scope amendment: `tools/search_text.py` builds the command as `[... options ..., "-e", pattern, "--", <paths>]` so the pattern is never parsed as an option on ANY transport (stdio, S39 HTTP, shared). Verified anchor: `cmd.append(pattern)` at tools/search_text.py:49; the `scope` argument is safe (the `-g` value or an absolute path). Tests run the REAL `rg` from the dev image with a marker program for `--pre=`/`--hostname-bin=`: the program never runs and the pattern is searched literally; plus a fake-`rg` argv spy proving `-e` precedes the pattern and `--` precedes paths; benign patterns return identical results to base (stdio results unchanged; S38 goldens still match).
  (b) Defence in depth in shared mode: `classify` rejects a non-str pattern or one starting with "-" (`argument_rejected`) before dispatch, with the zero-spawn test and a positive control.
  (c) Audit every subprocess argv under `mcp-server/src` (`tools/test_dry_run.py`, `storage/snapshots.py`, `history/cochange.py`) for user-controlled values reaching option position; record the audit with anchors in the evidence; fix any found under the same amendment, else record "none".
  The CHANGELOG gains a `### Security` bullet for the injection fix (in the existing Security section, after the re-lock bullet).
- Condition 6: per-principal in-flight cap default `max(1, max_inflight // 2)`; `max_leases_per_principal` default 1; bounded `limits.max_graph_leases`; capacity formula enforced at startup.
- Condition 10: adopt the token-lifetime cap (`exp - iat`, or `exp - now` when `iat` is absent) with default 86400 s, configurable and bounded; `run_shared` fails closed when pyjwt or cryptography are below the verified versions.
- Condition 11: refuse group- or world-writable policy, JWKS and shared-config files and their parent directories.
- Condition 15: never-cut = conditions 1-7, policy deny-all with the epoch, JWKS validation, the in-flight cap, and every AC. Stop and return to the orchestrator at the Day-4 checkpoint if any never-cut item is red.

**Findings**

1. **BLOCKER: `search_text` option injection gives code execution through a shared grant.**
   - Evidence: `tools/search_text.py:39-49` appends `pattern` positionally with no `-e` and no `--`. The schema is `{"type":"string"}` (server.py:106). The guard only inspects `repository`.
   - A pattern of `--pre=/bin/sh` makes ripgrep run `sh <file>` over every repo file. `--hostname-bin=` also runs a program. `-f/etc/x` reads a file as patterns.
   - This is the "execute anything" and "read outside the repository" path for any principal granted `search_text`. It predates S40, but S40 makes it a privilege boundary.
   - **Condition:** `classify` rejects any `search_text` call whose `pattern` is a str starting with "-" (reason `argument_rejected`, added to `DENIAL_REASONS`). The guard returns FORBIDDEN before dispatch. Reject a non-str pattern the same way.
   - Test with a fake `rg` that records argv:
     - `--pre=…`, `--hostname-bin=…`, `-f…`, `-e` and `--files` give 0 spawns.
     - A benign pattern gives 1 spawn as the positive control.
     - Add this to AC-008 and the never-cut list.
   - Also file a follow-up issue to fix `search_text.py` with `-e pattern --`. The fix is out of scope here and stdio/S39 remain exposed.

2. **MAJOR: repository, graph_root and memex overlap is not validated.**
   - Evidence: D1 only checks memex_root against repos and (repo, namespace) uniqueness. `CodeGraph` blocks a graph_root only inside its own repo (codegraph.py:905). `path_is_within` confines to the route repo only (config.py:58-76).
   - Repo B nested in repo A, or B's graph_root inside A, lets A's principals read B through `view_file`, `list_dir` and `search_text`.
   - **Condition:**
     - Add startup reasons `repository_nested`, `graph_root_inside_repository` and `root_nested`.
     - No registered repo may contain or equal another, except an explicit same-path pair where `before_repo_id` links the two ids and the graph roots differ.
     - No graph_root or memex_root may lie inside any registered repo or inside another's root.
     - Add table tests for each reason.
     - The doc states that a grant on any id of a path exposes that path's live tree.

3. **MAJOR: the policy stat gate can miss a revocation and make AC-004 flaky.**
   - Evidence: D2 gates the re-read on `(ino, size, mtime_ns, ctime_ns)`. After `os.replace`, an inode can be reused. Kernel timestamps are coarse (about 4 ms). Same-size edits made within one tick, such as a changed subject of equal length, can leave the tuple identical, so a revoke is never seen.
   - **Condition:** once per interval, open the file once, `fstat` that fd, read and hash its bytes from the same fd, and decide the epoch from the content digest. Do not skip the read on an unchanged stat.
   - Keep `current()` free of I/O. Treat a non-regular file or a symlink swap as invalid, which means deny-all.
   - Test with same-size, same-mtime rewrites, setting mtime with `os.utime` to the old value.

4. **MAJOR: ContextVar propagation is only specified for the sequential case.**
   - The SDK copies context at `tg.start`, so a router-set ContextVar would reach the handler but never be reset. The plan correctly takes the principal from the scope key. Bind these conditions:
     - `AUTHZ_CONTEXT` is set only inside `guarded` (the handler task) and reset in `finally`. Never set it in the router.
     - Add a test that `AUTHZ_CONTEXT.get() is None` in the router and in a task spawned outside a guard.
     - Add a concurrent two-principal test on one repository. A rendezvous inside a spy in `pending` and `authenticate` forces both requests in flight at once. Each `scope()` call must see its own identity.
     - Assert the expiry timer calls `scope()` zero times, with a positive control that it ran.

5. **MAJOR: exceptions are not contained, so verification can crash or leak.**
   - Evidence: pyjwt coerces `int(payload["exp"])`. A signed token with `exp` as a list, `Infinity` or `1e400` can raise TypeError or OverflowError, which are not `PyJWTError`. The plan maps only `PyJWTError`.
   - The SDK turns any handler exception into `str(e)`: server.py:589-590 for tools/call, :791-794 (`ErrorData(code=0, message=str(err))`) for resources/read.
   - **Condition:**
     - `verify()` catches `Exception` as `token_malformed`.
     - After decode, check explicitly that `exp`, `nbf` and `iat` are `type is int`, and that `aud` is a str or a list of str.
     - The router wraps stages 6-8 in `try/except Exception` and answers 401 or 403 with the fixed bodies.
     - `guarded` wraps classify, decide and context creation, and on any exception returns the denial. A raw `str(e)` never reaches the wire.
     - Test with correctly signed tokens carrying list, NaN, Infinity, 1e400, bool and numeric-string `exp`/`nbf`.

6. **MAJOR: shared-resource exhaustion by one authenticated principal.**
   - (a) One shared `InflightGate` (default 8) and no per-principal concurrency cap. A slow `search_text` (15 s) or a slow body (S39 §8 slow holds) can pin all slots for every repository.
   - (b) `Config.max_graph_leases` is 4 (config.py:119) and leases live 300 s. With the quota allowed up to 4 and defaulting to 2, two principals starve everyone else on a repository, and clients have no release call.
   - (c) 64 repositories times 64 MiB of lease state (config.py:122) is about 4 GiB, with no process-wide cap.
   - **Condition:**
     - Add a router per-principal in-flight counter (synchronous increment, decrement in `finally`), default at most `max(1, max_inflight // 2)`.
     - Default `max_leases_per_principal` to 1.
     - Add `limits.max_graph_leases` to the shared-config, bounded, and refuse startup if the capacity formula is not met.
     - Document the aggregate-memory formula.
     - Test that principal A at its cap does not block B.
     - Add the in-flight cap to never-cut.

7. **MAJOR: AC test soundness gaps.**
   - AC-003: an observer that records `AUTHZ.scope_identity` per `scope()` call passes even if `scope()` ignored the context. Also assert independently that the returned scope equals `digest({"base": LeaseRegistry.scope(reg), "authz": expected_identity})`, and that the lease `.scope` and the token `authority` differ per principal.
   - AC-004: reject the continuation with `purge_revoked` patched to a no-op. This proves scope mismatch is what enforces revocation, not the purge. Purge is hygiene.
   - AC-009: test `alg=none` with an empty signature (`token_malformed`) and with a non-empty one (`alg_not_allowed`).
   - Controls: pair every `receive == 0` with an authorized POST showing receive ≥ 1.
   - Leeway edges: pyjwt reads the real clock, so use margins of at least 5 s.
   - AC-006: the digest input must hold sorted lists only, never a set. The PYTHONHASHSEED runs catch a violation.
   - AC-008: add the following.
     - `view_file` with an absolute path into another registered repo.
     - A symlink escape.
     - A `snapshot_id` from another namespace used on the route.
     - Observers on `Path.read_bytes` for paths under the other repo.

8. **MINOR: stale leases from in-flight requests across an epoch change.**
   - A request decided at epoch e can install a lease after the purge at e+1. It cannot be reused (scope mismatch), but it holds quota and pack fds for up to 300 s.
   - **Condition:** the registry keeps `_epoch`, set under `_lock` by `purge_revoked`. `pending` and `install` raise SCOPE_MISMATCH when `ctx.policy_epoch != _epoch`.

9. **MINOR: router path matching.**
   - Match `scope["path"]` with `fullmatch` and an ASCII label regex, because `$` accepts a trailing "\n". Also require `scope["raw_path"] == scope["path"].encode()`, so any percent-encoding gives 404. Require `root_path == ""`.
   - Test trailing slash, `//`, `%2f`, `%2e%2e`, case, `%0a`, `/mcp` with no id, `/mcp/<id>/x`, a query string, and a PRM path with an unregistered or invalid label.

10. **MINOR: pyjwt Day-1 additions (these must be proven, not assumed).**
    - Build the key map with one `PyJWK(dict)` per key, failing startup on any exception, instead of `PyJWKSet.from_dict`, which can skip keys silently. Record `PyJWK.algorithm_name` for RSA keys with no `alg`.
    - Probe whether `options={"strict_aud": True}` exists and use it if it does.
    - Prove with `decode(algorithms=["RS256"])` that HS256 and `none` fail independently of the pre-checks.
    - Observe how `iat` in the future, `sub` that is not a str, and `jti` that is not a str behave.
    - `run_shared` must check at runtime that pyjwt and cryptography meet the verified minimum. Today only a test pins them, and pyproject does not declare pyjwt (`test_thesis_negatives` pins the core dependency list).
    - Optionally reject tokens with an excessive remaining lifetime.

11. **MINOR: policy and config hygiene.**
    - Reject duplicate keys, NaN and Infinity in the policy, the JWKS and the shared-config.
    - Use one subject predicate for both the policy and the token. D2 says "no control characters" and D5 says "printable", so a policy subject with NBSP is unreachable.
    - Refuse a group- or world-writable policy, JWKS or shared-config file or parent directory. A local writer of `policy.json` bypasses everything.
    - A `max_token_bytes` above about 16 KiB is unreachable because of the h11 header limit.

12. **MINOR: wiring tripwires.**
    - After `build_shared`, assert `type(registry) is ScopedLeaseRegistry`, `type(enforcement) is SharedEnforcement`, and that every non-Ping handler carries `__atlas_guard__`.
    - Assert Ping is the SDK `_ping_handler`.
    - Assert `server.experimental` is never touched, since it registers task handlers.
    - Make `install_guards` idempotent.
    - Test `req=None` ListTools both inside and outside a guarded call.

13. **MINOR: AC wording.** Document AC-005's C as "same working tree, distinct graph namespace and snapshot". The protected asset is historical graph and source-pack evidence, not live files. This is a real boundary, but not a content-isolation claim. Also document that AC-003's "cache" is the lease registry, the only retained-evidence store.

14. **MINOR: documentation must state these items.**
    - Behind the proxy every client appears loopback, so the peer check is moot and Bearer is the only gate.
    - JWT verification runs on the loop, so the proxy must rate-limit.
    - An unprivileged local process can squat the port before the proxy forwards credentials.
    - The IdP must not be an enumeration oracle for audiences.
    - `view_file` reads dotfiles and `.git` in the tree.
    - Revocation of a compromised token needs a short `exp` or a key rotation with restart.

15. **MINOR: timebox and cuts.**
    - Five days is tight for three modules, six test files, docs and G-PROD. Day-1 (g), the base full suite, should run in the background.
    - Never-cut must also include findings 1-6, policy deny-all with the epoch, JWKS validation, and the in-flight cap.
    - Cut 4 (the per-principal limiter) is acceptable only with the in-flight cap kept.
    - Rollback is fine: revert and no data changes.

## Stories
As an operator, I want one loopback service behind my TLS proxy to serve several repositories to authenticated principals, with authorization decided before any graph, source or memex access. Then sharing Atlas never widens what a principal can read.

As a security reviewer, I want every access path, cursor and resource bound to the principal's authorization scope, and observed with non-vacuous zero-activity tests. Then revocation and cross-principal replay are demonstrably closed.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s40.criteria.md` (sha256 `98cb89a8…78ff16`). AC-S40-001..009 and RUN-01/02 are not restated; the mapping is above. Setup: no mock of the behaviour under test; spies only count; every `all(...)` is preceded by a non-empty assertion.

## Confidence
To be computed with `ramza-score`.
- Authorization sits in front of unchanged engines: three new modules plus one additive command. `server.py`, `http.py`, `leases.py` and `enforcement.py` are byte-identical, so the stdio and S39 behaviour and every pin hold by construction.
- Main uncertainties, all covered by Day-1:
  - pyjwt API and JWKS strictness (b);
  - SDK handler-dict coupling and ContextVar reach (c);
  - lease-scope binding (d);
  - in-process multi-repository serving (e).

## Rejected Alternatives
- **A `repository` tool argument or header on `/mcp`.** It changes the inputSchema or tools sha, and the authorization unit would be request-controlled.
- **One Server plus a ContextVar repository router.** It duplicates S39 handlers and mixes per-repository state in one Server.
- **Starlette `AuthenticationMiddleware` and `RequireAuthMiddleware`.** They emit free-text descriptions and fix the order after routing; the raw router stays closed and body-free.
- **`PyJWKClient` or token introspection (RFC 7662).** Network access is out of scope.
- **Token scopes as the authorization source.** They are not revocable before `exp`; the policy file is.
- **New modules in `protocol/`.** That needs an edit to `test_s39_isolation.py:322-325`.
- **Editing the `LeaseRegistry` token key set or adding a principal claim.** That breaks the S13/S17/S18 exact-shape tests; the scope override binds the existing `authority`.
- **A scope-keyed result cache.** No cache exists, and adding one adds risk without value.
- **A deployment-wide audience.** A token for one repository URL would be replayable on every route; per-route RFC 8707 audiences contain that.
- **Filtering results after traversal.** The issue's own "too late" pattern (retrieval.py:323-338).

## Risks (ranked)
1. **A missed access path (P0).** Mitigated by three independent layers, the D8 matrix, the handler tripwire and per-layer AC-008 cases.
2. **Vacuous zero-activity tests.** Every zero is paired with a positive control on the same observer; withheld bodies and receive counts back the before-body claims.
3. **SDK coupling** (`request_handlers`, `req=None`, scope identity). Day-1 (c) STOP, the version tripwire, a byte-pinned uv.lock.
4. **JWT edge cases** (algorithm confusion, a JWKS silently dropping keys, clock skew). Day-1 (b) STOP, the D5 pre-checks, leeway ≤ 60.
5. **Revocation latency and over-invalidation.** Bounded by the reload interval; any policy change invalidates every cursor (safe); documented.
6. **Pin collisions** (CHANGELOG, README, http-transport.md, lazy import). G-REG runs after the docs edits; additions only.
7. **Shared-process DoS** (loop-blocking sync ops, lease capacity). Per-principal lease quota and limiter; the rest is documented.
8. **Timebox.** Cut order and the Day-4 checkpoint.

## Rollback
- One bounded PR with no data, schema, dependency, lock, stdio or `serve` change.
- `git revert <output_commit>` removes three modules, one command, the tests and the docs.
- Deployments that never run `serve-shared` are unaffected.
