---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T18:00:00Z
slice_id: ACI-S38
issue: https://github.com/Rynaro/atlas-aci/issues/59
---
# ACI-S38 - a tested MCP protocol-revision compatibility adapter

## Scope

Issue #59. Base `2db6dd1a3cba6589bf4efc9ad83620e39bacb289` (main after PR #102; S01-S37 merged except S15). Branch `aci-s38`.
- Marker `<!-- atlas-aci:slice:ACI-S38 -->` verified.
- Criteria digest `d197ecdf9438db653eaacddc4e4d199f5c38d57133beb674546bd5f1564ac279` (6 product ACs + RUN-01/02).
- Risk **P0**. Timebox 3 engineering days. Rightsize tier **full** (score 6); an independent checker runs.
- **Dependencies PRESENT** (receipts COMPLETE):
  - S11 (#33, c376ef5): versioned outputSchema/structuredContent.
  - S13 (#35, 07a583f): resources and the real-SDK stdio test.
  - S23 (#44, ed41dd7): the stdio runner and evidence anchors.

Observed facts (verified at planning):
- **server.py.**
  - :17-21 imports; :20 `from mcp.server.stdio import stdio_server` (S23 anchor).
  - :57-60 `build_tool_manifest(config, protocol_version=types.LATEST_PROTOCOL_VERSION)`; :152-156 outputSchema only if `protocol_version in STRUCTURED_VERSIONS`.
  - :169-170 `_meta` and `annotations` on test_dry_run for every revision.
  - :600-680 `call_tool_result`, with :620 `structured = name == "graph_query" and protocol_version in STRUCTURED_VERSIONS`.
  - :683-749 `run_stdio`; :688-697 closure `session_version()` (non-str → LATEST); :715-717 `resources/list` → `[]`.
- **protocol/results.py.**
  - :9 imports `SUPPORTED_PROTOCOL_VERSIONS`; :22 `STRUCTURED_VERSIONS = frozenset({"2025-06-18","2025-11-25"})`.
  - :33-35 `negotiated_version`; :83-101 `FIXED_ERROR_ENVELOPE_CAP` (797).
- **SDK mcp 1.27.0.**
  - `shared/version.py:3` SUPPORTED = [2024-11-05, 2025-03-26, 2025-06-18, LATEST="2025-11-25"] (a list object, shared by `server/session.py`).
  - `server/session.py:163-187`: a supported version is echoed, otherwise LATEST is returned. `:188-193`: ping is allowed pre-init; any other pre-init request raises.
  - `shared/session.py:380-395`: any validation or pre-init failure → `{"code":-32602,"message":"Invalid request parameters","data":""}`.
  - `client/session.py:175`: `initialize()` always sends LATEST; `:190` rejects an unknown server version; `:395-419` validates structuredContent against the listed outputSchema.
  - `serverInfo.version` = `pkg_version("mcp")`.
- **Tests that pin today's behaviour:**
  - `test_s11_stdio.py:25-59` (PROXY, raw `initialize`) and :90-135, `test_s13_stdio.py:28-29`, `test_s18_stdio.py:33` all parametrize 2099-01-01 → 2025-11-25.
  - `test_s11_protocol.py:43-44` pins `negotiated_version`.
- **Pins.**
  - Tools JSON sha `94f9567a…ac56`, 1631472 bytes; budgets 3436/3448/797/7730; `ev.RELATIONS`.
  - pyproject sha `c0cfa084…`, uv.lock sha `6b820f14…` (`test_s36_isolation.py:43-44`); `uv.lock:408` `1.27.0` (S23 anchor).
  - The core dependency set (`test_thesis_negatives.py:57-73`). jsonschema 4.26.0 is locked transitively (uv.lock:307).
  - CHANGELOG Unreleased/Added `bullets[0..6]` = S30 (:12), S33 (:31), S34 (:49), S35 (:66), S36 (:83), S28 "Python dependency surface" (:98), S27 (:116). `test_s36_isolation.py:457-459` pins bullets[4] and bullets[5].
  - README :949 verified-feedback → :950 community (S36 pin) → :951 rails.
- **Environment.**
  - Image: python:3.12-slim (Dockerfile:55), uid 10001 `atlas` (:76, :97), `ENTRYPOINT ["atlas-aci"]` (:110), no `timeout` binary.
  - `max_calls_per_minute` 200 (config.py:125).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-05)
1. **No dependency change.**
   - pyproject.toml and uv.lock stay byte-identical; mcp stays 1.27.0.
   - "Pin the revision and the matrix" means an Atlas-owned table plus a tripwire test (installed version 1.27.0; the SDK SUPPORTED list and LATEST equal the table).
2. **Revisions.**
   - Selected modern: 2025-11-25. Also verified modern: 2025-06-18.
   - Retained legacy: 2024-11-05 and 2025-03-26.
   - Structured results for modern only, as today.
3. **Behaviour is byte-preserved for every revision**, including:
   - the well-formed-unsupported counter-offer (LATEST);
   - the -32602 for malformed or pre-initialize requests;
   - the default `build_tool_manifest(config)` bytes.
   `_meta` and `annotations` are documented, not stripped. Golden transcripts are captured at base before any src edit.
4. **Adapter.**
   - New `protocol/revisions.py`.
   - `results.py` re-exports `STRUCTURED_VERSIONS` and `negotiated_version`; `FIXED_ERROR_ENVELOPE_CAP` stays where it is.
   - `server.py` swaps the closure body for the adapter call. New imports go below line 20, and added lines carry no harden markers.
   - The query engine, tools, enforcement, config and storage are untouched.
   - An SDK-supported but unlisted revision never enables structured results.
5. **AC-004.**
   - Spec lifecycle counter-offer, plus the adapter's deterministic `counter_offer` diagnostic.
   - Malformed or missing params → -32602.
   - A legacy-only raw client detects the incompatibility deterministically.
   - No custom -32602 `data.supported`.
6. **Fixture hosts.**
   - (a) SDK `ClientSession` with a raw `InitializeRequest` (test_s11 pattern);
   - (b) an independent raw JSON-RPC line client over a real `run_stdio` subprocess.
   Both are labelled "declared fixture hosts", never Claude Code, Copilot or Cursor. The 1.6 MB tools frame is stored as a digest.
7. **AC-003 schema (stricter option).**
   - Official `schema.json` files are fetched at a pinned commit in a `--rm` container and committed with sha256 and provenance.
   - Frames are validated with the locked jsonschema; `structuredContent` against `outputSchema` (Draft 2020-12); `check_schema` on every advertised schema.
   - If the fetch fails: "SDK-modelled protocol conformance (mcp 1.27.0)", recorded, not a STOP.
   - If the official schema rejects a frame: a documented, tested known deviation, never a behaviour change.
8. **AC-005.** The same fixture and canonical query set over 2024-11-05 and 2025-11-25, plus a `dispatch_tool_call` cross-check.
9. **AC-006.** Two fresh `run_stdio` processes per supported transcript; byte-identical normalised output, also equal to the golden.
10. **Docs.** `hosts/protocol-compatibility.md`; real hosts "untested; negotiated revision not verified". `hosts/cursor.md:100` is fixed only if trivially correct.
11. **Scope amendment.**
    - `tests/fixtures/s38/**`;
    - CHANGELOG bullet at Unreleased/Added index 6 (right after "Python dependency surface", index 5);
    - README row after the S36 row;
    - `.spectra/evidence/aci-s38/**`.
12. **Invariance.** Tools pin; budgets; `ev.RELATIONS`; producer inputs; no runtime dependency; no model sampling.

### In
1. `protocol/revisions.py` (pure; D1-D3).
2. `protocol/results.py`: re-export only.
3. `server.py`: closure swap plus a one-time stderr negotiation record (D4).
4. Tests: `test_s38_contract.py` (the 6 named ACs), `test_s38_support.py` (stdlib-only hosts, conversation, normaliser, capture, production driver), `test_s38_semantics.py`, `test_s38_isolation.py`.
5. Fixtures `mcp-server/tests/fixtures/s38/`: goldens, official schemas with provenance and license, `MANIFEST.v1.json`.
6. Docs: `hosts/protocol-compatibility.md`, the CHANGELOG bullet, the README row.
7. Evidence under `.spectra/evidence/aci-s38/`: qualification, base-invariants, preserve digests, production-scenario.sh, py314.log, gate logs.

### Out
- Any SDK bump or lock/pyproject edit; HTTP/SSE transport; JSON-RPC batching.
- Prompts, completions, logging capability, sampling, elicitation, tasks, pagination.
- Per-revision field stripping; a custom error payload; a `build_server` split.
- A CLI `--protocol-version`; edits to `__main__.py`, `query/*`, `tools/*`, `codegraph.py`, `storage/*`, `enforcement.py`, `config.py`, `protocol/{graph,resources,context,impact,__init__}.py`.
- Any claim about real hosts.

## Approach

### Decisions

**D1 Revision table** (`protocol/revisions.py`). Imports allowed: `__future__`, `collections.abc`, `dataclasses`, `typing`, `pydantic`, `mcp.types`, `mcp.shared.version`.
```python
@dataclass(frozen=True)
class Revision: id: str; family: str; structured_results: bool; status: str
REVISIONS = (Revision("2024-11-05","legacy",False,"retained_legacy"),
             Revision("2025-03-26","legacy",False,"retained_legacy"),
             Revision("2025-06-18","modern",True,"verified_modern"),
             Revision("2025-11-25","modern",True,"selected"))
REVISION_IDS = tuple(r.id for r in REVISIONS); SELECTED = "2025-11-25"
LEGACY = ("2024-11-05","2025-03-26"); MODERN = ("2025-06-18","2025-11-25")
CONSERVATIVE = "2024-11-05"; VERIFIED_SDK = ("mcp", "1.27.0")
STRUCTURED_VERSIONS = frozenset(r.id for r in REVISIONS if r.structured_results)
FAMILIES=("legacy","modern"); STATUSES=("retained_legacy","verified_modern","selected")
```

**D2 Negotiation model.**
- **Closed vocabularies:**
  - `OUTCOMES = ("supported","counter_offer","malformed")` describes what the wire does.
  - `REASONS = ("listed_revision","sdk_only_revision","unknown_revision","integer_revision","missing_revision","invalid_revision_type","invalid_initialize_params")`.
  - `WIRE = ("initialize_result","invalid_params_error")`.
  - `REQUESTED_TYPES = ("string","integer","missing","other")`.
- **The Negotiation object.** `negotiate(requested: str | int, *, sdk_supported=None, sdk_latest=None) -> Negotiation` (frozen dataclass).
  - `None` means the SDK list and LATEST are read at call time (`mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS`, `mcp.types.LATEST_PROTOCOL_VERSION`), the same objects the SDK session uses.
  - Fields: `outcome, reason, requested, offered, shaping_revision, verified, family, structured_results, wire`.
  - Method `revision() -> str` returns `shaping_revision` and raises ValueError when the outcome is malformed.
- **Rules**, in order (`bool` is never produced by `negotiate`; see `classify_initialize`):

| Input | outcome | reason | offered | shaping_revision | verified |
| --- | --- | --- | --- | --- | --- |
| str in Atlas table and in SDK list | supported | listed_revision | requested | requested | true |
| str in SDK list, not in Atlas table | supported | sdk_only_revision | requested | `CONSERVATIVE` | false |
| any other str (2099-01-01, "", drafts, Atlas-listed but dropped by the SDK) | counter_offer | unknown_revision | sdk_latest | sdk_latest if listed else `CONSERVATIVE` | sdk_latest listed |
| int | counter_offer | integer_revision | sdk_latest | as the row above | as the row above |

- `family` and `structured_results` come from `shaping_revision`'s row.
- `classify_initialize(params: object) -> Negotiation` handles raw JSON params:
  - runs `types.InitializeRequestParams.model_validate(params)`, exactly the SDK's validation;
  - on failure: malformed, with `missing_revision` (key absent), `invalid_revision_type` (an error located at `protocolVersion`) or `invalid_initialize_params` (anything else); `offered`/`shaping_revision` are None and `wire` is `invalid_params_error`;
  - otherwise `negotiate(validated.protocolVersion)`.
- **Diagnostic record** `Negotiation.record()`, format `atlas-protocol-negotiation.v1`, closed keys:
  `{format, outcome, reason, requested, requested_type, offered, shaping_revision, verified, family, structured_results, wire, atlas_revisions:[4 ids], selected, sdk_supported:[…]}`.
  Canonical bytes are `json.dumps(sort_keys, separators=(",",":"), ensure_ascii=False)`.
- **Compatibility functions.**
  - `shaping_revision(requested: str|int) -> str` = `negotiate(requested).revision()`.
  - `negotiated_version(requested: str) -> str` = the same function (test_s11_protocol:43-44 holds).

**D3 Behaviour equivalence at the pinned SDK.** Today's rule is `requested if requested in SDK else LATEST`, with non-str → LATEST. Because the SDK list equals the Atlas table and LATEST equals SELECTED, `shaping_revision` returns the same value for every input:
- listed → itself; any other str → LATEST; int → LATEST.
- `STRUCTURED_VERSIONS` keeps the same members.

So manifests, `call_tool_result` shaping and the default tools JSON cannot change. A semantics test runs a literal copy of the old body against `shaping_revision` over {the 4 ids, 2099-01-01, "", "2024-10-07", 0, 20241105}. The pinned conditions are themselves tripwired (D9).

**D4 server.py change** (only lines 37-42 and 688-697, plus one import):
- `from atlas_aci.protocol.results import (STRUCTURED_VERSIONS, bounded_result, failure_payload)`: `negotiated_version` is dropped.
- New line `from atlas_aci.protocol.revisions import negotiate`, placed after the results block (isort order).
- Closure:
```python
    negotiation_logged = False

    def session_version() -> str:
        nonlocal negotiation_logged
        params = server.request_context.session.client_params
        if params is None:
            raise RuntimeError("MCP session is not initialized.")
        negotiation = negotiate(params.protocolVersion)
        if not negotiation_logged:
            negotiation_logged = True
            log.info("protocol_negotiated", negotiation=negotiation.record())
        return negotiation.revision()
```
- Everything else stays byte-unchanged: `run_stdio` signature, line 20, `build_tool_manifest` (default argument included), `call_tool_result`, handlers, `stdio_server` use.
- `results.py`: delete the local definitions of `STRUCTURED_VERSIONS` and `negotiated_version` and the `SUPPORTED_PROTOCOL_VERSIONS` import. Add `from atlas_aci.protocol.revisions import STRUCTURED_VERSIONS as STRUCTURED_VERSIONS` and `negotiated_version as negotiated_version`, the explicit re-export form for ruff and mypy.
- The log goes to stderr only and never to the wire.

**D5 Fixture hosts** (`test_s38_support.py`; stdlib only at module level; `atlas_aci` imported lazily inside functions so the base worktree and the production image can import it).
- **RawLineClient(argv, env, deadline_s=120).**
  - `subprocess.Popen` with stdin/stdout/stderr pipes.
  - A stdout reader thread reads unbounded lines into a queue (the 1.6 MB frame is safe; no asyncio 64 KiB limit). A stderr thread drains into a buffer capped at 1 MiB.
  - `request(method, params)` and `notify(method, params)` use integer ids from 1. The client waits up to 30 s per response, matched by id, and collects server notifications separately.
  - `close()` closes stdin, waits 10 s, otherwise kills and records the outcome. The raw line bytes are kept.
- **Server child.**
  - argv: `[sys.executable, "-c", SERVER, repo, memex]`, where SERVER = s11's script with default `Config(repo, memex_root)`.
  - env: `os.environ` minus `ATLAS_ACI_*`, plus `PYTHONDONTWRITEBYTECODE=1` and an explicit `PYTHONHASHSEED`.
- **SDK host.** In the contract test only (`from test_s11_stdio import PROXY, initialize`): `ClientSession` plus PROXY wire log, with the same steps except the raw-only ones.
  - AC-002 also runs plain `ClientSession.initialize()`.
- **Fixture repo `s38-mini.v1`.** `a.py` = `b"def target():\n return 1\ndef caller():\n target()\n"`, built once per module by `CodeGraph(repo).build()`.

**D6 The documented conversation** `lifecycle.v1`, identical for all four revisions (`CONVERSATION` tuple; the hosts doc table must equal it):
1. `initialize` `{protocolVersion:R, capabilities:{}, clientInfo:{name:"atlas-s38-fixture-host", version:"1"}}`
2. `notifications/initialized`
3. `ping`
4. `tools/list`
5. `resources/list`
6. `resources/templates/list`
7. graph_query `{"query":"definitions_of:target"}`
8. graph_query `{"query":"callers_of:target"}`
9. search_symbol `{"name":"target"}`
10. view_file `{"path":"a.py","start_line":1,"end_line":4}`
11. graph_query `{"schema_version":"atlas.graph.v1","op":"status"}`
12. graph_query neighborhood `{"seed":{"name":"target"},"max_depth":1,"deadline_ms":2000}`
13. graph_query path `{"seed":{"name":"caller"},"target":{"name":"target"},"deadline_ms":2000}`
14. graph_query `{"query":"not-a-query"}` (isError)
15. graph_query `{"query":false}` (INVALID_INPUT)
16. `tools/call` `write_file` (FORBIDDEN)
17. `logging/setLevel {level:"info"}` (-32601, raw only)

Expected isError for steps 7-16 is recorded at Day-1 (c) and asserted. Steps 14-15 are True per test_s11 and step 16 is True; 7-13 are False. Page, continue, context and resources/read run live in AC-002 only; they are not in the goldens because their lease and cursor tokens are random.

**D7 Normalisation** (`NORMALISATION_RULES`, closed, applied in order):
- N1: the response `id` becomes the ordinal of first appearance among server frames.
- N2: tmp paths (repo, memex, graph root) become `<repo>`, `<memex>`, `<graphs>`.
- N3: `content[i].text` that parses as JSON becomes `{"$json": normalised}`.
- N4: 64-hex runs (`(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])`) in string values become `<h64:N>`, numbered by first appearance across the transcript.
- N5: values under the keys `lease_id`, `cursor`, `next_cursor` become `<lease:N>`/`<cursor:N>`.
- N6: `serverInfo.version` is kept verbatim (1.27.0, tripwired).
- N7: the tools/list result becomes `{"tools_digest":{names(sorted), sha256(canonical(tools)), bytes, output_schema:[names], meta:[names], annotations:[names]}}`.
- N8: server notifications go to `notifications[]` (expected empty); stderr is excluded.
- N9: canonical JSON plus LF.

A field that varies but is not in these classes is a STOP (Day-1 c). A unit table tests each rule.

**D8 Golden transcripts** `atlas-s38-transcript.v1`, in `tests/fixtures/s38/transcripts/<case>.v1.json`:
```
{format, case, conversation:"lifecycle.v1"|"negotiation.v1", revision_requested, host:"raw-line",
 sdk:{"mcp":"1.27.0"}, fixture:"s38-mini.v1",
 exchanges:[{step, name, method, kind:"request"|"notification", request, response:null|{result}|{error}}],
 notifications:[], exit:{code, closed_by:"client"}}
```
- Cases: `legacy.2024-11-05`, `legacy.2025-03-26`, `modern.2025-06-18`, `modern.2025-11-25`, `unsupported.2099-01-01`, `unsupported.integer`, `malformed.missing-version`, `malformed.pre-initialize`.
- The negotiation cases run initialize, then ping, tools/list, and close.
- `MANIFEST.v1.json` holds `{format:"atlas-s38-fixtures.v1", base_commit:"2db6dd1…", captured_with:argv, sdk, files:[{path, sha256, bytes}]}`.
- Capture: `capture_golden(out)` runs in a **base worktree** (`git worktree add <scratch>/s38-base 2db6dd1`; `uv run --frozen --project <base>/mcp-server python <branch>/…/test_s38_support.py capture --out D`), twice, with `PYTHONHASHSEED` 0 and 4242, and the two outputs are compared with `cmp`.
- The same run writes `preserve/base/*.frames.json`: per-frame sha256 and bytes of the raw server lines with only N2 applied.

**D9 Tripwire and fallback.** `test_s38_isolation::test_sdk_matrix_tripwire` asserts:
- `importlib.metadata.version("mcp") == "1.27.0" == VERIFIED_SDK[1]`;
- `mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS == list(REVISION_IDS)`;
- `mcp.types.LATEST_PROTOCOL_VERSION == SELECTED`;
- `mcp.server.session.SUPPORTED_PROTOCOL_VERSIONS is mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS`;
- `uv.lock` holds `name = "mcp"` followed by `version = "1.27.0"`.

A future bump therefore fails CI loudly. At runtime an unlisted revision is shaped as `CONSERVATIVE`, with `verified:false` in the stderr record. An end-to-end semantics test proves this: a server child appends `"2026-06-30"` to the SDK list before `run_stdio`. The SDK echoes it; graph_query has no outputSchema and results have no structuredContent; stderr carries `sdk_only_revision`.

**D10 Schema conformance procedure (AC-003).**
- **Fetch.** `docker run --rm alpine/git` resolves `git ls-remote https://github.com/modelcontextprotocol/modelcontextprotocol refs/heads/main` to commit C. Then `--rm curlimages/curl` fetches `https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/C/schema/<rev>/schema.json` for 2025-11-25 and 2024-11-05 (plus 2025-06-18 and 2025-03-26 if cheap), and `LICENSE` at C.
- **Commit.** Bytes go to `tests/fixtures/s38/schemas/<rev>.schema.json` verbatim, with `LICENSE.mcp-specification` and `PROVENANCE.v1.json` `{repository, commit:C, license_spdx, files:[{revision, url, path, sha256, bytes, dialect:$schema}], fetched_with:{images+digests, argv}, fetched_at_utc}`.
- **License.** Committed only if the licence is MIT or Apache-2.0; otherwise fall back.
- **Validation.**
  - `referencing.Registry` registers each schema at `urn:mcp-schema:<rev>`.
  - The validator class is chosen by `jsonschema.validators.validator_for(schema)`, after `check_schema`.
  - Each response body validates against `{"$ref":"urn:mcp-schema:<rev>#/<defs>/<Definition>"}` using `FRAME_DEFINITIONS[rev][method]`, which is recorded at Day-1 (e): InitializeResult, the empty result, ListToolsResult, ListResourcesResult, ListResourceTemplatesResult, CallToolResult, ReadResourceResult, and the JSON-RPC response and error envelopes.
  - Coverage must include at least InitializeResult, ListToolsResult and CallToolResult per fetched revision.
- **Deviations.** Failures are collected as `(case, step, definition, json_pointer, keyword)`. The test asserts the set equals `KNOWN_DEVIATIONS` exactly, so new and vanished deviations both fail. Each deviation is listed in the hosts doc and `qualification.json` as a checker-visible finding.
- **Fallback.** If the fetch fails, validate with the SDK models (`types.InitializeResult.model_validate` etc.) under the label `SDK-modelled protocol conformance (mcp 1.27.0)`, and record the limitation in the doc.

**D11 Hosts doc outline** (`hosts/protocol-compatibility.md`):
1. Summary: stdio only, mcp 1.27.0, selected 2025-11-25, no dependency change.
2. Revision matrix: revision | family | status | outputSchema/structuredContent | verified with | golden file.
3. Negotiation: the D2 table, wire examples (2099 → 2025-11-25; int → 2025-11-25; missing, null or invalid params → -32602 "Invalid request parameters"; pre-initialize request → -32602; ping before init → `{}`), the stderr `protocol_negotiated` record, the client's duty to disconnect, and the retry guidance (request 2024-11-05 explicitly).
4. The documented conversation (the D6 table).
5. Declared fixture hosts verified: the raw line client and mcp 1.27.0 ClientSession (raw initialize and `initialize()`).
6. Real hosts Claude Code, GitHub Copilot and Cursor: "untested; negotiated revision not verified".
7. Emitted on all revisions: `_meta` and `annotations` (test_dry_run), resource `_meta`, `serverInfo.version` = SDK version, `experimental: {}`, identical capabilities.
8. Unsupported: HTTP/SSE; JSON-RPC batching (every revision, 2025-03-26 included); logging/setLevel (-32601); an unknown method (-32602); tasks, sampling, elicitation, prompts, completions, pagination; strict-schema legacy clients.
9. Framing-dependent admission for context, continue and impact (byte budget per framing).
10. Schema provenance and `KNOWN_DEVIATIONS`, or the fallback label.
11. Upgrade procedure: tripwire → table row → goldens → docs.

### Work plan (timebox 3 engineering days)
**Day 1 AM: qualification**, before any src edit. Outputs: `.spectra/evidence/aci-s38/qualification.json` and `base-invariants.json`.
- (a) `git diff --quiet 2db6dd1 -- mcp-server/src`. Record the mcp version, the SUPPORTED list, LATEST, and the pyproject/uv.lock shas.
- (b) `test_s11_stdio`, `test_s11_protocol`, `test_s13_stdio`, `test_s18_stdio` are green. The tools pin and the budgets hold.
- (c) Write `test_s38_support.py`, then run base-worktree golden capture twice and `cmp -r`. Record the isError vector, the case shas and the preserve digests.
- (d) Observe the AC-004 wire for each table case, including `true`. The `classify_initialize` rules are fixed to match what is observed.
- (e) Schema fetch and provenance, definition names, base frames validated → the `KNOWN_DEVIATIONS` set (or the fallback).
- (f) Record how canonical(2025-11-25 wire tools) relates to canonical(`atlas-aci tools` JSON). Expected: equal.
- (g) In-process at 2025-11-25 on s38-mini: which of the 8 graph ops yield non-error structured results.
- (h) Full suite at base with the known deselects (`test_s01_contract.py::test_call_deadline_after_initialization`, `test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition`).

**STOP RULE** (return to the orchestrator; never adapt silently):
- (a) mcp ≠ 1.27.0, SUPPORTED ≠ the 4 ids, or LATEST ≠ 2025-11-25;
- (b) any listed test red, or a pin mismatch at base;
- (c) the two captures differ in a field outside the D7 classes;
- (d) the 2099, missing-version or pre-initialize wire differs from the s11/s13/s18 expectation;
- any golden captured from a non-base tree.

**Recorded adaptations, not stops:**
- (e) fetch or licence failure → fallback label;
- (f) a non-equality → the observed relation is asserted instead;
- (g) error-only ops are still validated as error envelopes;
- (d) `true` → the recorded behaviour becomes a D2 row.

**Day 1 PM:** `revisions.py`, the semantics unit tables, the results.py re-export, the server.py closure swap.
**Day 2:** the 6 AC tests, the SDK host, the live resource step, the D9 end-to-end fallback.
**Day 3:** isolation, docs, CHANGELOG, README, the gates, py314, the production scenario, RUN-01/02.

**Cut order** (each cut recorded in the receipt):
1. The one-time stderr record (D4 shrinks to the closure body).
2. The `hosts/cursor.md:100` fix.
3. AC-006 limited to 2024-11-05 and 2025-11-25.
4. In-process AC-003 breadth (retrieve, context, impact).
5. Official schemas for 2025-06-18 and 2025-03-26.
6. The SDK host at 2025-03-26 and 2025-06-18.
7. The README row.

**Never cut:** base goldens and G-PRESERVE, the tripwire, the raw host, all 6 AC tests, the 2099/int/-32602/pre-init cases, legacy-client detection, D3 equivalence, the docs matrix with untested real hosts, G-SRC, G-MCP.

### Independent checker conditions
Verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s38-plan-checker`, independent read-only review with the mcp 1.27.0 source; 7 MAJOR, 8 MINOR, no BLOCKER). Every finding is accepted as a binding plan condition with the same number; where a condition conflicts with plan text above, the condition wins.

Orchestrator resolutions (under the user's autonomous-execution authorisation; stricter option chosen each time):
- Condition 2: take cut 1 now. `session_version()` and every other request-path function gain NO new I/O: no stderr log, no structlog call, nothing that can raise in `_list_tools` or `_call_tool`. The diagnostic is delivered by the pure `classify_initialize`/`record()` functions and the deterministic wire (byte-compared with base). The raw client still fails on any non-JSON stdout line.
- Condition 3: `record()` bounds `requested` (first 64 characters, plus `requested_len`; `requested_truncated` added to the closed key set), with the 1 MiB and newline tests, even though nothing logs it.
- Condition 4: with condition 2's resolution the server emits no `protocol_negotiated` record at all; the doc says so, and a test asserts that stderr for C1, for a normal modern session and for a legacy session carries no record and no Traceback.
- Condition 1 (c): a base-capture identity mismatch is a STOP, and the capture is made in a separate `git worktree` at 2db6dd1 using the base tree's own interpreter path, never the branch's modules.
- Condition 6: if the CLI-path capture differs from the API capture outside a recorded N-class, the goldens are captured through the CLI path (production parity wins).
- Condition 7: licence acceptance requires MIT or Apache-2.0 at the specification repository root with LICENSE/NOTICE copied beside the fixtures; any other outcome takes the labelled fallback, never a silent copy.
- Condition 15: cut 3 is taken before any reduction of SDK-host breadth; conditions 1, 2, 3, 5 and 10 are not cuttable.

**Findings**

1. MAJOR - the preservation proof covers too little. D8 and G-PRESERVE preserve only the 8 case conversations. The cases U2, U3, M2, M3 and R1 are checked live against `classify_initialize` but never byte-compared with base. Nothing compares `build_tool_manifest(config, v)` or `call_tool_result` bytes for the other tools. A base capture run from the branch's driver, with no recorded tree identity, can also silently be a branch capture.
   Condition: the base and after preserve sets must include:
   - (a) raw N2-only frame sha256 and bytes for every AC-004 table case (U1-U4, M1-M3, P1, P2, R1), plus tools/list for 2099 and int;
   - (b) an in-process dump, run in the base worktree and on the branch, of sha256(canonical `build_tool_manifest(config, v)`) for the 4 ids, "2099-01-01", "" and the `atlas-aci tools` sha, plus `call_tool_result` result bytes for one call of each of the 7 tools under legacy and modern shaping;
   - (c) a capture header recording `atlas_aci.__file__`, `git rev-parse HEAD` of the base tree, a clean-tree flag, and the sha256 of server.py and results.py, which must equal `git show 2db6dd1:<path>`. A mismatch is a STOP.

2. MAJOR - the new stderr log sits in the request path and can break it. `session_version()` runs inside `_list_tools` and `_call_tool`. If `log.info` raises (stderr closed or broken), a handler exception becomes `code:0` on tools/list and tools/call (`lowlevel/server.py:785-788`). The old closure had no I/O. If structlog is unconfigured, the line goes to stdout mid-conversation.
   Condition: set `negotiation_logged = True` first, then wrap the log call in `try/except Exception: pass`. Do not add imports above line 20. The raw client must fail on any non-JSON stdout line. Otherwise take cut 1.

3. MAJOR - `record().requested` echoes client input unbounded. A client can send a multi-MB `protocolVersion` or one containing newlines, and the record would copy it to stderr in a project whose thesis is mechanical bounds.
   Condition: before writing the oracle literals, bound `requested` (for example the first 64 characters plus `requested_len`, with `requested_truncated` only if it is added to the closed key set). Add a test with a 1 MiB version string and a version containing "\n".

4. MAJOR - the server-side diagnostic is never emitted in the incompatibility case itself. `session_version()` runs only on tools/list and tools/call. A client that receives a counter-offer and disconnects (C1) produces no `protocol_negotiated` record. The SDK exposes no initialize hook that fits the 10-line change.
   Condition: the doc must say the stderr record appears only on the first tools/list or tools/call. "Deterministic incompatibility diagnostics" is then delivered as the deterministic wire (tested against base) plus the pure `classify_initialize`/`record()` function. Add a test asserting that C1's stderr carries no record and no Traceback, so the doc and the behaviour agree.

5. MAJOR - the normaliser can hide real differences, and N2 will miss macOS paths.
   - N3 parses text into `$json` and discards the exact text bytes, so the goldens would not catch a key-order or separator change.
   - N2 must replace both the `Config`-resolved path (macOS `/private/var/...`) and the unresolved `tmp_path` form, longest first, including JSON-escaped forms.
   - A closed N-rule list means any unexpected varying field is a STOP.
   Conditions:
   - N3 also stores sha256 of the post-N4/N5 text string.
   - AC-006 additionally asserts N2-only raw frame equality between the two processes. Any field that differs must be named in an N-class.
   - Add mutation negative controls: a changed text byte, a changed `serverInfo.version`, a changed outputSchema name list, and a changed hash relation (same versus different) must each fail comparison.
   - A named duration or timestamp key may be added as a recorded N-class instead of a STOP.
   - Use `deadline_ms` of at least 30000 on steps 12-13 so a slow runner cannot truncate.

6. MAJOR - production parity is deferred to Day 3. Goldens are captured via `Config(repo, memex)` and `CodeGraph.build()`. The image uses `atlas-aci index` and `serve` (default `--langs`, `--graph-root /graphs`), on Linux with other grammar builds. A mismatch outside the N-classes would surface at G-PROD with no time left.
   Condition: add Day-1 (c2). At base, build the fixture through the CLI `index` and run `atlas-aci serve --graph-root`, then compare to the API capture. Record any difference as an N-class or capture goldens via the CLI path. The golden must also pass in G-PY314 (Linux) before merge.

7. MAJOR - AC-003 official-schema fetch needs a defined procedure.
   - Day-1 (e) must record `ls schema/` at commit C. As of 2026-11 the spec repo may hold a revision newer than 2025-11-25. If so, the doc lists it as "not supported: mcp 1.27.0 does not offer it" and the matrix says "selected = newest SDK-supported", not "official latest".
   - The licence rule must be explicit. Accept Apache-2.0 or MIT at repo root, and copy LICENSE and any NOTICE. Do not fall back merely because docs carry CC-BY. Record the SPDX decision.
   - Record the dialect per revision (draft-07 uses `definitions`, 2020-12 uses `$defs`), and verify the sha256 of the bytes before commit.
   - Include a negative control: a corrupted frame must fail under each fetched schema, which also proves nested `#/` refs resolve through the urn registry.
   - If any legacy revision has a non-empty KNOWN_DEVIATIONS (for example `annotations` or `_meta` on a 2024-11-05 Tool under `noExtraProps`), the matrix cell must name it and must not read "verified".
   - Time-box the fetch to 1 hour, then take the fallback label.

8. MINOR - the AC-004 C1 construction is contrived. A legacy-only client requesting an unknown pre-release is not a realistic incompatibility, since a legacy-only client requesting 2024-11-05 is never incompatible. It is non-vacuous only for the wire assertion (offered 2025-11-25 not in the client set, server stays alive, exit 0).
   Condition: add a future-only client (supports {"2099-01-01"} only) as the primary C1. Keep the legacy variant. State in the doc that spec-compliant clients disconnect, and note the spec's example -32602 "Unsupported protocol version" error that Atlas does not emit, with the reason. C2 must run both on the same process (as R1) and on a fresh one.

9. MINOR - doc wire claims lack tests. Section 8 asserts "unknown method -> -32602" and "batching unsupported". Neither appears in D6 or the AC-004 cases. Condition: add a post-init `foo/bar` request and a one-line batch with assertions (the batch yields no JSON-RPC response; the following ping is answered). Otherwise word the claims "not exercised". The 2025-03-26 matrix row must say batching is unsupported.

10. MINOR - the manifest and import-order pin is cheap to prove.
    - `revisions.py` imports neither `protocol.graph` nor `protocol.results`, so import order cannot change `GRAPH_OUTPUT_SCHEMA`.
    - Condition: pin the canonical sha of `GRAPH_OUTPUT_SCHEMA` before and after. Add a fresh-interpreter test that imports `atlas_aci.protocol.revisions` first and then checks the tools sha 94f9567a… and the budgets 3436/3448/797/7730.
    - Assert `session_version` has exactly two call sites (`_list_tools`, `_call_tool`).

11. MINOR - tripwire and re-exports.
    - The D9 tripwire is sound; `server/session.py:59` and `client/session.py:16` share the same list object.
    - Condition: test `STRUCTURED_VERSIONS == frozenset({"2025-06-18","2025-11-25"})`, the s11 values, and that `negotiated_version` and `FIXED_ERROR_ENVELOPE_CAP` stay importable from `results`.
    - The D9 end-to-end test must assert that stderr carries `verified:false` and that no structuredContent is present.

12. MINOR - fixture host hygiene.
    - Keep draining stdout and stderr after the 1 MiB stderr cap, discarding the excess.
    - Join the threads, close the pipes, and make the kill path record its outcome.
    - Use per-process memex directories and run the servers sequentially, since a shared `.atlas` is touched.
    - Add a client-side check that the 1.6 MB frame is read whole.

13. MINOR - AC-005 breadth. Steps 7-13 on a 4-line repo are a weak but honest set; the exclusion of context, continue and impact is correct. If Day-1 (g) shows `retrieve` or `page` are non-error and not framing-dependent, add them. Record the exclusions in qualification.

14. MINOR - G-PROD shell robustness.
    - Under `set -eu`, `wait` must be written `wait $pid || rc=$?`, because S5 expects exit 1.
    - Use unique container names (`$$`), a `trap` that runs `docker rm -f`, and ignore the `docker kill` race when the container is not yet created.
    - S7 must fail if pytest reports skipped.
    - Capture the image id and mcp version in the output.

15. MINOR - timebox and cuts for a P0.
    - Hold a Day-1 checkpoint after qualification (STOP rule, fetch outcome, CLI parity).
    - Record the cut decision at Day-2 end with a clock reading.
    - The cut order is acceptable, but cut 3 should be taken before the SDK-host breadth.
    - Rollback by `git revert` is sound.
    - The pin and scope items verify as the plan states. pyproject and uv.lock stay unchanged (the `test_s36_isolation` shas, the uv.lock anchor and G-SRC enforce this). Only one `server.py` anchor exists, at line 20. CHANGELOG index 6 and the README row after :950 satisfy the s30-s36 pins. The harden gate inspects only the server.py hot file.

## Stories
As an agent operator, I want Atlas to serve an explicitly selected modern MCP revision while legacy hosts keep exactly the conversation they had. Then upgrading Atlas never silently breaks a working host.

As a reviewer, I want golden transcripts, an SDK tripwire and an honest revision matrix. Then no SDK bump or doc claim can promise support that was not observed.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s38.criteria.md` (sha256 `d197ecdf…ac279`). AC-S38-001..006 and RUN-01/02 are not re-stated; the executor reads the frozen file.

**Setup.**
- A module-scoped s38-mini build. Every server is a real `run_stdio` subprocess.
- No mock of the behaviour under test. The D9 SDK-list append changes a dependency's data, not Atlas.
- Every `all(...)` is preceded by a non-empty assertion.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_legacy_host_fixture[raw\|sdk × 2024-11-05\|2025-03-26] | **Initialize.** protocolVersion == R; `serverInfo == {"name":"atlas-aci","version":"1.27.0"}`; capabilities == recorded; `negotiate(R)` is supported/listed_revision/legacy/structured false. **Steps.** ping → `{}`. tools/list: exactly 7 names; no outputSchema anywhere; `_meta` and `annotations` only on test_dry_run. resources/list `[]`; 1 template `atlas-source://`. Steps 7-16: the isError vector equals Day-1 (c); no `structuredContent` key in any raw frame; step-7 payload non-empty. Step 17 → -32601. **Golden.** The raw normalised transcript == golden `legacy.R` byte for byte. The SDK host's normalised responses == the golden responses for its steps. **Exit.** Client close → server exit 0 within 10 s; no `Traceback` in stderr. |
| 002 | test_modern_host_fixture[raw\|sdk × 2025-11-25\|2025-06-18] + plain `initialize()` | **Initialize.** protocolVersion == R; plain `ClientSession.initialize()` yields 2025-11-25. **Schemas.** graph_query outputSchema == `GRAPH_OUTPUT_SCHEMA`; other tools have none. **Results.** Every graph_query result has `structuredContent == json.loads(text)`; non-graph results have none. The SDK host's `call_tool` completes, so client-side outputSchema validation passed. **Live step.** page (neighborhood seed target) → `resources/read(data.resources[0].uri)` → blob == the source slice; `_meta.generation_id` == the payload's. **Golden.** The normalised transcript == golden `modern.R`. |
| 003 | test_schema_conformance | **structuredContent.** Every modern-conversation result, plus in-process results of each Day-1 (g) op, validates under `Draft202012Validator(outputSchema)`. Status, neighborhood, path, page and continue must be non-error; the text payload is canonically equal. **check_schema.** Runs on every advertised outputSchema and inputSchema of all 4 revisions. **Frames.** Every frame of the 4 lifecycle goldens plus the live modern run validates against the official schema of its negotiated revision via D10; the failure set == `KNOWN_DEVIATIONS` exactly; definition coverage is non-vacuous. The provenance shas equal the bytes. Legacy text payloads also validate under `GRAPH_OUTPUT_SCHEMA`. |
| 004 | test_unsupported_version | See the cases table below. **Model consistency.** For each case, the wire equals `classify_initialize(params)` (`offered` == `result.protocolVersion`; `wire` kind). **Diagnostics.** `record()` bytes are stable over two calls and equal the literal oracle for 2099 and for missing. **Golden.** Equal to the goldens for the four negotiation cases. **Client.** The legacy-only client detects incompatibility deterministically. |
| 005 | test_engine_equivalence | **Query set.** Steps 7-13 run in a 2024-11-05 process and a 2025-11-25 process: the text bytes are identical (only N5 keys may be masked, expected none); isError is equal; structuredContent is None for legacy and == payload for modern. **Cross-check.** `dispatch_tool_call(structured=False/True)` on the same inputs gives equal payloads. **Non-vacuity.** Payloads are non-empty, and the tools/list digests differ (outputSchema). |
| 006 | test_adapter_replay[4 revisions] | **Replay.** Two fresh processes (PYTHONHASHSEED 0 and 4242, distinct pids) per transcript → identical normalised bytes, and equal to the golden. **Non-vacuity.** The legacy and modern transcripts differ. The golden MANIFEST shas hold. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal across the interrupted and resumed receipts; one receipt, `.spectra/state/aci-s38.execution.json`. |

**AC-004 cases** (raw host; "same process" means one server):

| Case | Sent | Wire (Day-1 d confirms) | classify outcome/reason |
| --- | --- | --- | --- |
| U1 | protocolVersion "2099-01-01" | InitializeResult with 2025-11-25 | counter_offer / unknown_revision |
| U2 | "2024-10-07" | 2025-11-25 | counter_offer / unknown_revision |
| U3 | "" | 2025-11-25 | counter_offer / unknown_revision |
| U4 | 20241105 | 2025-11-25 | counter_offer / integer_revision |
| M1 | no protocolVersion | -32602 "Invalid request parameters", data "" | malformed / missing_revision |
| M2 | null; `["2025-11-25"]` | -32602 | malformed / invalid_revision_type |
| M3 | no capabilities | -32602 | malformed / invalid_initialize_params |
| P1 | tools/list before initialize | -32602 | — (lifecycle) |
| P2 | ping before initialize | `{}` | — |
| R1 | M1, then a valid 2024-11-05 initialize in the same process | 2024-11-05 | supported (recovery as observed) |
| C1 | legacy-only client (supports {2024-11-05}) requests the pre-release "2024-10-07" | offered 2025-11-25 ∉ client set → client record `{"client_outcome":"disconnect","reason":"offered_revision_not_supported_by_client","requested":"2024-10-07","offered":"2025-11-25"}`; no further requests; server exit 0 within 10 s | counter_offer, offered == the wire |
| C2 | the same client reconnects with 2024-11-05 | initialize, ping, tools/list complete | supported |

### Supporting tests
- **`test_s38_semantics.py`.**
  - The D2 table, with injected `sdk_supported`/`sdk_latest` for the sdk_only and dropped-revision rows.
  - The closed vocabularies and the record key set.
  - D3 old-rule equivalence; the re-exports (`results.STRUCTURED_VERSIONS is revisions.STRUCTURED_VERSIONS`, likewise `negotiated_version`).
  - The test_s11_protocol values.
  - The D9 end-to-end fallback, and the stderr `protocol_negotiated` record appearing exactly once in the 2099 replay.
  - The N1-N9 rule table; the RawLineClient timeout, kill and notification separation.
- **`test_s38_isolation.py`.**
  - The D9 tripwire.
  - G-MCP: tools sha and bytes, 7 tools, 3436/3448/797/7730, `ev.RELATIONS`.
  - pyproject/uv.lock shas == the S36 constants; no `jsonschema` in pyproject.
  - `server.py` line 20 text unchanged; harden-marker count in server.py == 3 and in revisions.py == 0.
  - revisions.py imports ⊆ the D1 allow-list, with no `open`/`os`/`subprocess`/`time`/`random`/`secrets`/`logging`.
  - `test_s38_support` top-level imports are stdlib only; fixture tree contents equal `MANIFEST` exactly, total ≤ 2 MiB.
  - Signatures of `run_stdio`, `build_tool_manifest`, `call_tool_result`, `dispatch_tool_call` and `build_server_state` equal their base strings.
  - **Docs:**
    - ASCII; names every REVISIONS id, STATUS, OUTCOME, REASON and N-rule, "1.27.0", SELECTED, each unsupported item, the schema commit or the fallback label, and every `KNOWN_DEVIATIONS` entry;
    - the D6 table == `CONVERSATION`;
    - each real host name appears only on lines containing "untested; negotiated revision not verified";
    - none of the tokens {certified, guaranteed, proven, seamless, universal}.
  - **CHANGELOG:**
    - "(ACI-S38)" occurs exactly once, at `bullets[6]`;
    - bullets[0..5] keep their S30/S33/S34/S35/S36/"Python dependency surface" prefixes;
    - no `- **` continuation line; no prior title or docs locator.
  - **README:**
    - the row directly after the community-comparison row, with 3 pipes;
    - no prior locator and no `canary-missions.md`.

### Gates beyond the ACs
- **G-S38:** `uv run --frozen pytest tests/slices/test_s38_contract.py -q`, then `tests/slices/test_s38_*.py`.
- **G-REG** (unchanged and green, run after the docs edits): `tests/slices/test_s11_*.py tests/slices/test_s12_*.py tests/slices/test_s13_*.py tests/slices/test_s17_*.py tests/slices/test_s18_*.py tests/slices/test_s20_mcp.py tests/slices/test_s23_*.py tests/slices/test_s3[0-6]_isolation.py tests/slices/test_s27_isolation.py tests/slices/test_s28_isolation.py tests/test_server*.py tests/test_thesis_negatives.py tests/slices/test_s01_contract.py`, with the s01 deselect.
- **G-LINT, exactly as CI** (`uv run --frozen`):
  - `ruff check src/ tests/`; `ruff format --check src/ tests/`; `mypy src/`;
  - `ruff check ../scripts/ --exclude fingerprint-fixture`; `ruff format --check ../scripts/ --exclude fingerprint-fixture`; `mypy ../scripts/`.
- **G-FULL:** `uv run --frozen pytest -q` with both deselects; no new failure or skip versus `base-invariants.json`.
- **G-SRC:**
  - `git diff --exit-code 2db6dd1 -- mcp-server/src ':!mcp-server/src/atlas_aci/protocol' ':!mcp-server/src/atlas_aci/server.py' mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile .github/workflows scripts atlas docs`;
  - `git diff --name-status 2db6dd1 -- mcp-server/src` == {A protocol/revisions.py, M protocol/results.py, M server.py};
  - `git diff --name-only 2db6dd1 -- mcp-server/tests` ⊆ `test_s38_*.py` ∪ `fixtures/s38/**`;
  - `git diff --name-only 2db6dd1 -- hosts` ⊆ {protocol-compatibility.md, cursor.md}.
- **G-MCP:** `atlas-aci tools` sha `94f9567a…` / 1631472 bytes.
- **G-PRESERVE:** branch captures, run as in D8, give `preserve/after` == `preserve/base` per-frame digests (`cmp`), and normalised goldens equal.
- **G-HARDEN:** `bash scripts/harden-gate-classify.sh 2db6dd1 HEAD` prints `augmented=false` and `a3_touched=false`.
- **G-PY314:** `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s38_*.py tests/slices/test_s11_protocol.py tests/slices/test_s11_stdio.py -q'`, logged to `py314.log`.
- **G-PROD** (`.spectra/evidence/aci-s38/production-scenario.sh IMG`; orchestrator-run against the built image; POSIX sh with `set -eu`; workdir from `mktemp -d`; every container `--rm --network none`, plus `--read-only` for serve):
  - **Bounding without `timeout`:** `bounded SECS NAME args…` runs `docker run --rm --name NAME … &`, polls `kill -0` once per second up to SECS, then `docker kill NAME` and FAIL 124. It returns the container's exit code.
  - **S1 identity:** `--entrypoint python IMG -c …` prints the mcp version, SUPPORTED, LATEST, the REVISIONS rows, `STRUCTURED_VERSIONS`, `classify_initialize` records for {2024-11-05, 2025-11-25, 2099-01-01, 20241105, {}}, and sha256 of `server.py`, `protocol/revisions.py`, `protocol/results.py`. Exact string compare against the host-computed expected values.
  - **S2 index:** the fixture repo is written by `printf`; graphs/memex directories get mode 777 (uid 10001); `IMG index --repo /repo --graph-root /graphs` → exit 0.
  - **S3 replay in the image:**
    - mounts: `-v mcp-server/tests/slices:/s38:ro -v fixtures/s38:/golden:ro`;
    - command: `--entrypoint python IMG -I -c 'import sys;sys.path.insert(0,"/s38");import test_s38_support as s;raise SystemExit(s.production_main(sys.argv[1:]))' --case C --golden /golden -- atlas-aci serve --repo /repo --graph-root /graphs --memex-root /memex`;
    - cases: all 8; the driver prints `{case, match, sha256}`; each must be `match:true`.
  - **S4 replay twice:** `legacy.2024-11-05` and `modern.2025-11-25` in fresh containers; equal shas.
  - **S5 negative control:** `--case legacy.2024-11-05` against the modern golden → exit 1 with `match:false`. Grep the raw output of the 2099 case for `"protocolVersion":"2025-11-25"`.
  - **S6 tools pin:** `IMG tools --repo /repo` → sha and bytes equal the pin.
  - **S7 docker regression:** `ATLAS_ACI_S11_DOCKER_IMAGE=IMG ATLAS_ACI_S13_DOCKER_IMAGE=IMG uv run --frozen pytest tests/slices/test_s11_docker.py tests/slices/test_s13_docker.py -q` → passed, not skipped.
  - **S8 host checks:** G-SRC and the pyproject/uv.lock shas.
  - The script writes `production-scenario.json` and exits 0 only if S1-S8 pass.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s38_contract.py -q
uv run --frozen pytest tests/slices/test_s38_*.py -q
uv run --frozen pytest -q --deselect tests/slices/test_s01_contract.py::test_call_deadline_after_initialization --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
```

## Confidence
To be computed with `ramza-score`.
- **Low churn.** One new pure module, one re-export, and a 10-line closure swap.
- **Behaviour preservation is proven three ways:**
  - D3 old-rule equivalence;
  - per-frame raw digests from a base worktree;
  - normalised goldens.
  The wire is SDK-owned and unchanged.
- **Main uncertainties, all covered by Day-1:**
  - transcript determinism (c);
  - the observed wire for odd inputs (d);
  - official schema availability, licence and deviations (e);
  - production-image parity (G-PROD).

## Rejected Alternatives
- **An SDK bump to gain a newer revision.** It breaks the pyproject/uv.lock sha pins, the `uv.lock:408` anchor, budgets and the tools pin. It is the "blind bump" the issue warns about.
- **A custom -32602 with `data.supported` via a stream wrapper.** It breaks the three pinned 2099 tests and departs from the spec counter-offer.
- **Stripping `_meta`/`annotations` for 2024-11-05.** A behaviour change with no host evidence. It is documented instead, and any official-schema rejection becomes a known deviation.
- **A `build_server(config)` split for in-memory tests.** More server.py churn. Subprocess stdio is the real transport.
- **SDK `ClientSession` as the only host.** `initialize()` always sends LATEST and validates client-side. The raw line client is independent of the SDK.
- **"Nearest lower known revision" fallback** (scout). It could enable structured results without verification. `CONSERVATIVE` never does.
- **Failing server startup when the SDK list ≠ the table.** It would brick `serve` after a bump in production. The CI tripwire plus conservative shaping plus the stderr record cover the case.
- **Goldens captured from the branch.** They cannot prove preservation. Captures run from a base worktree only.

## Risks (ranked)
1. **Hidden behaviour drift in the refactor.** Base goldens, G-PRESERVE and D3; the STOP rule on capture provenance.
2. **The official schema rejects current frames, or the fetch or licence fails.** An exact `KNOWN_DEVIATIONS` set plus the doc and a checker-visible finding; or the labelled fallback.
3. **Nondeterministic transcript fields.** Day-1 double capture, closed N-rules; STOP outside those classes.
4. **SDK-coupled goldens** (`serverInfo.version`, tools digest). Intended coupling, guarded by the tripwire. A later SDK or tools change must re-capture with recorded provenance.
5. **CHANGELOG and README pins.** Index 6 and the row after :950; G-REG runs right after the edits.
6. **Production parity** (CLI `serve` vs `run_stdio(Config)`, uid 10001 volumes). G-PROD S3 compares against the same goldens; directories are 777.
7. **Large tools/list frames** (time). Thread-based unbounded readline, digests only, one process per conversation.
8. **Ruff/mypy re-export patterns.** The `X as X` form; the CI-exact lint gate.
9. **Timebox.** Apply the cut order at Day-2 end.

## Rollback
- One bounded PR, with no data, schema, dependency or wire change.
- `git revert <output_commit>` restores the closure and `results.py` definitions.
- Tests, fixtures and docs are additive.
- Since the wire is byte-identical (G-PRESERVE), a revert has no host impact. The only runtime difference is the stderr `protocol_negotiated` line.
