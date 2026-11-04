---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T00:00:00Z
slice_id: ACI-S37
issue: https://github.com/Rynaro/atlas-aci/issues/58
---
# ACI-S37 — export one scoped evidence view as offline HTML and canonical JSON

## Scope

Issue #58 on base `7b00944` (main after PR #87 / ACI-S23), branch `aci-s37`. Live marker `<!-- atlas-aci:slice:ACI-S37 -->` verified. There is no prior S37 plan, receipt or execution state. The frozen criteria hash to `31a6c71bfdcf180cf4715ebe949fdbfd012ed221fb98aba574872c732283be55`, which matches the contract table (9 criteria). Predecessors on base: S13 (leases, paging, `atlas-source://` resources, `bounds`) and S18 (`op=context`, `CONTEXT_DATA`) are ancestors with COMPLETE receipts. There is no separate "S18 selection" artifact: S13 and S18 are layers of one `atlas.graph.v1` envelope.

Observed facts (re-verified at planning):
- **Validators.** `STRUCTURED_VALIDATOR` is rebuilt at import with the context and impact members (`protocol/graph.py:1254-1255`); read it as a module attribute. The `context` and `continue` oneOf members both accept `CONTEXT_DATA` (`graph.py:1220`). `validate_context` (`protocol/context.py:326-600`) is pure. It calls `parse_uri(resources[].uri)` (:574) and requires `has_more == (next_cursor is not None)` (:576), so it must see the unstripped input.
- **Identity domain.** `retrieval_record_id` = `digest` over the 17 keys listed at :579-597. Resource id = `digest(descriptor − {id, uri})` (:543-544). Source id excludes `id`, `inline` and `inline_reason` (:483). `uri` and `next_cursor` lie outside the identity domain (`atlas/docs/graph-context.md:99-105`). `uri` carries a lease token signed with a per-process secret (`query/leases.py:84`).
- **Snapshot fields.** The declared snapshot is the envelope's `generation_id`/`snapshot_id`, both nullable (`graph.py:612-613`). `data.generation_id` is a non-null hash, `data.snapshot_id` is a nullable hash, and each `resources[]` entry has `generation_id`/`snapshot_id` as non-null hashes (`context.py:124-125`). Bundles, records and sources carry no snapshot field; they bind by `source_sha256` (file bytes) and through `retrieval_record_id`. The only existing foreign-snapshot check (:556-573) runs after the identity checks, so a tampered resource fails earlier with a generic error.
- **Repository path.** `data.repository` = `str(reader.repo)`, an absolute path (`query/context.py:25`). Snapshot identity includes `root=str(repo)` (`storage/snapshots.py:38-64`), so envelopes are path-bound.
- **Canonical JSON.** `ev.canonical` (`storage/evidence.py:181`) is the sorted-key, compact, `ensure_ascii=False`, `allow_nan=False` encoder. `canonical_bytes` = `canonical + "\n"` (`query/impact.py:712`).
- **Producing real envelopes.** S18 shows the pattern: fixture → `CodeGraph.build` → `build_server_state` → `call_tool_result("graph_query", {op:"context"})` (`tests/slices/test_s18_contract.py:18-52`). Variants: path bundle via `target={"name":"validate_payment"}` (:196); truncated via `continuation=True, max_bytes=40000` (`test_s18_pages.py:42-45`); source-free import with `evidence_status partial`, unavailable sources and empty `resources` (`test_s18_pages.py:201-228`); error envelope via `max_bytes=1`.
- **Packaging.** Hatch `packages=["src/atlas_aci"]` ships non-py files (`pyproject.toml:60-61`). The image installs the built wheel. No `.gitattributes` may exist (`test_export.py:367`). The core deps are pinned (`test_thesis_negatives.py:57-73`).
- **Pins.** Tools JSON: 7 tools, sha `94f9567a…a56ac56`, 1,631,472 bytes. Budgets: MINIMUM_BUDGET 3436, COLLECTION 3448, FIXED_ERROR 797, IMPACT 7730 (`test_s22_isolation.py:44-65`). CLI commands are pinned by subset only.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)
1. **One input shape.** The only accepted input is an `atlas.graph.v1` envelope with `operation=="context"`, `status=="ok"` and `data.projection=="task_context"`, read from a file. The file holds the `content[0].text` JSON or the `structuredContent` object; both are the same envelope. `impact`, `page`/`continue`, `retrieve`, error envelopes and validation plans are rejected with `unsupported_operation`. Impact support is a follow-up.
2. **Credentials.** `data.resources[].uri` and `data.next_cursor` are removed from both outputs and listed in a visible omitted-fields section.
3. **CLI.** `atlas-aci evidence-report ENVELOPE --html OUT.html --json OUT.json`. Both outputs are required, with no defaults. The command imports lazily. A rejection prints `Evidence report rejected: <code>`, exits 1 and writes nothing. Usage errors exit 2.
4. **Library and template.** The library lives in `atlas_aci/reports/{__init__.py,evidence.py,evidence_template.html}`; `__init__.py` is added by amendment. The template is read with `importlib.resources`. The orchestrator verifies it inside the production image.
5. **Rendering.** A static `string.Template` shell; every fragment is built in Python with `html.escape(quote=True)`; no scripts; stdlib only. Planner refinement of control/bidi handling: see D6.
6. **Validation order.** Byte cap → strict parse → `STRUCTURED_VALIDATOR` → snapshot scan → `validate_context` → render. Every rejection carries a typed code from a closed set.
7. **JSON report.** Canonical UTF-8 plus LF, using the wrapper below. No timestamps, environment or locale.
8. **Markers.** Every scout marker appears in both formats, with explicit "none" states. Unknown is never turned into resolved.
9. **Output cap.** Oversized output is rejected with `report_too_large`.
10. **Docs.** `docs/evidence-reports.md`, the declared path, with no amendment.
11. **Fixtures.** Real in-test envelopes plus committed frozen envelopes and goldens; no mocks.
12. **AC-005 check.** A parser-based tag and attribute whitelist, also applied to the template.
13. **Invariance.** No change to `server.py`, `protocol/*`, `query/*`, `enforcement.py`, `config.py`, `pyproject.toml` or `uv.lock`, and the pins hold.

### In
1. `reports/evidence.py`: `ReportRejected`, `load_envelope(raw)`, `build_report(envelope)`, `render_json`, `render_html`, `export(raw, *, max_json_bytes, max_html_bytes) -> Report(html, json)`, and the constants.
2. `reports/evidence_template.html`: the static shell. `reports/__init__.py`: a docstring only.
3. `__main__.py`: the `evidence-report` command.
4. `tests/slices/test_s37_evidence_report.py`: the 7 AC tests plus supporting and isolation tests, and a `__main__` fixture regenerator.
5. `tests/fixtures/evidence_report/`: frozen envelopes, golden HTML/JSON, `provenance.json`.
6. `docs/evidence-reports.md`, a CHANGELOG Unreleased entry and a README docs-table row.

### Out
- Hosted or live browsing, dashboards, sharing, auth, publication, MCP tools or resources, any change to the tools JSON.
- New retrieval, graph expansion or resource reads during rendering.
- Impact, page and continuation merging, validation-plan input, non-default element or byte budgets.
- Decoding base64 excerpts, syntax highlighting, JavaScript or CSS assets.
- Edits to the paths in decision 13, plus `tools/*`, `codegraph.py`, `storage/*`, `.github/workflows/*`.

Declared implementation paths (issue): `reports/evidence.py`, `reports/evidence_template.html`, `__main__.py`, `tests/slices/test_s37_evidence_report.py`, `tests/fixtures/evidence_report/`, `docs/evidence-reports.md`. The scope amendment (`.spectra/changes/aci-s37/scope-amendment.json`, recorded before implementation) adds `reports/__init__.py`, `CHANGELOG.md`, `README.md` and the Ramza, receipt and backlog files. Rightsize: computed tier `lite` (score 3); an independent checker still runs.

## Approach

### Decisions

**D1 Module boundary.** `evidence.py` imports only stdlib (`base64`, `hashlib`, `html`, `importlib.resources`, `json`, `string`, `dataclasses`, `typing`) plus `atlas_aci.config` (constants), `atlas_aci.protocol.graph` (module attribute `STRUCTURED_VALIDATOR`, `validate_context`), `atlas_aci.storage.evidence` (`canonical`) and `atlas_aci.enforcement` (`ToolError`, caught only). An AST test bans `time`, `datetime`, `random`, `uuid`, `os`, `locale`, `socket`, `urllib`, `http`, `subprocess`, `platform`, `tempfile`, `unicodedata`, `atlas_aci.server`, `atlas_aci.tools`, `atlas_aci.codegraph` and `atlas_aci.query`. No retrieval is possible by construction.

**D2 Input pipeline (`load_envelope(raw: bytes) -> dict`).** Each step maps to a closed code: `envelope_too_large`, `invalid_input`, `invalid_envelope`, `unsupported_operation`, `mixed_snapshot_evidence`, `report_too_large`.
1. If `len(raw) > MAX_INPUT_FILE_BYTES` (4 MiB) → `envelope_too_large`. The CLI reads at most cap+1 bytes. The 4 MiB guard bounds memory before parsing and still admits pretty-printed saves.
2. Strict `raw.decode("utf-8")` (a BOM fails). Then `json.loads` with `parse_constant` rejecting NaN/±Infinity and `object_pairs_hook` rejecting duplicate keys. Any failure, or a canonical re-encode that fails on a lone surrogate → `invalid_input`.
3. If `len(ev.canonical(env).encode()) > config.DEFAULT_MAX_RESPONSE_BYTES` (1 MiB, the default response budget) → `envelope_too_large`.
4. Not a dict, or `graph.STRUCTURED_VALIDATOR.is_valid(env)` is false → `invalid_envelope`. A CallToolResult wrapper also lands here.
5. `operation != "context"`, `status != "ok"` or `data.projection != "task_context"` → `unsupported_operation`.
6. Snapshot scan (D5) → `mixed_snapshot_evidence`.
7. `validate_context(env, "context", config.DEFAULT_MAX_BOUND_FIELD_ELEMENTS)`. Any `ValueError`, `TypeError`, `KeyError` or `ToolError` → `invalid_envelope`.

`ReportRejected(code)`: `str()` is the code, and the code is asserted to be in `CODES`. It never carries a path or input text.

**D3 Credential stripping (after validation).** On a deep copy: delete the key `data.next_cursor`, and delete the key `uri` from each `data.resources[i]`. `omitted_fields` lists `{"pointer","reason":"capability_credential","value":"redacted"|"null"}`: `/data/next_cursor` first, then `/data/resources/<i>/uri` in ascending i. `has_more` stays, so continuation existence remains visible. The stripped envelope is not itself a valid envelope (documented). Feeding a report back in → `invalid_envelope`.

**D4 JSON report.** `render_json(r) = ev.canonical(r).encode("utf-8") + b"\n"`:
```
{report_format:"atlas-evidence-report.v1", rendering_version:"atlas-evidence-render.1",
 options:{credential_fields:"omitted", html_excerpts:"escaped_text",
          control_codepoints:"visible_placeholder"},
 envelope_sha256, omitted_fields:[...], envelope:<validated envelope minus omitted>,
 view:{identity:{schema_version, operation, status, evidence_status, generation_id, snapshot_id,
                 repository, retrieval_record_id, query_digest, ranker, request_text, source_policy},
       markers:[{name, pointer, value}],
       bundles:[{index, id, kind, rank, source_ids:[...], location:{path,line,column,end_line,
                 end_column}|null, hops:[{index, assertion_id, relation, traversal_direction,
                 resolution, unresolved_reason, raw_target, source_entity_id, target_entity_id,
                 evidence_id, occurrence_id, producer_id, path, line, column, end_line, end_column,
                 start_byte, end_byte, source_sha256, source_id, source_availability}]}],
       sources:[{id, kind, state, reason, path, start_byte, end_byte, source_sha256,
                 provenance_state, inline_encoding, inline_reason, resource_ids:[...]}]}}
```
How each field is filled:
- `envelope_sha256 = sha256(ev.canonical(envelope_after_stripping).encode())`. It can be checked against the report's own `envelope`, it covers no credential, and it does not change with lease tokens. Hashing the raw input was rejected (see Rejected Alternatives).
- Every `view` value is copied verbatim from the envelope; there is no arithmetic and no conversion of line bases.
- `location` = definition `records.occurrence`, or null.
- `hops` = `[proof]` for relationship bundles, `records.hops` for path bundles, and `[]` otherwise.
- `hop.source_id` = `bundle.sources[k].id`; `validate_context` proves the k-to-k order.
- `sources` is deduplicated by id in first-appearance order. A missing field becomes null via `.get`.
- `resource_ids` = `resources[].id` with `source_reference_id == source.id`, in input order.
- Arrays keep input (rank) order. Maps are canonical-sorted. There are no timestamps, environment, locale, hostname or cwd.

**D5 Snapshot scan (precise).** D = (`env.generation_id`, `env.snapshot_id`). The scan checks exactly:
- (`data.generation_id`, `data.snapshot_id`);
- (`data.resources[i].generation_id`, `data.resources[i].snapshot_id`) for every i.

Each pair must equal D exactly, and null compares as a value. So a null declared snapshot with any non-null item snapshot is mixed, and a null envelope `generation_id` with the non-null `data.generation_id` is mixed. No other per-item snapshot field exists in a context envelope. Record membership is bound through `retrieval_record_id`, which `validate_context` checks next. The scan runs before `validate_context`, so tampered fixtures receive the typed code.

**D6 HTML (`render_html(report, json_bytes)`).**
- **Shell.** The template is fixed. `Template.get_identifiers() == ["identity","markers","omitted","bundles","sources"]` is asserted, and filled with a single-pass `substitute`. The head is `<!doctype html>`, `<html lang="en">`, `<meta charset="utf-8">`, `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">`, `<meta name="referrer" content="no-referrer">`, a fixed `<title>` and inline `<style>`. The CSS contains no `$`, `url(`, `@import` or `@font-face`. There is no script anywhere.
- **Escaping.** Every value goes through one function, `text(v)`:
  - a str goes through `html.escape(quote=True)`;
  - a non-str goes through canonical JSON, then escape;
  - null becomes `none`;
  - after escaping, each codepoint in a fixed table becomes `<span class="cp">U+XXXX</span>`. The table: U+0000–0008, 000B–001F (CR included), 007F–009F, 00AD, 061C, 180E, 200B–200F, 2028–202E, 2060–2064, 2066–206F, FEFF, FFF9–FFFB.
  - The fixed table is used instead of `unicodedata`, so output does not depend on the Python Unicode version.
  - Placeholders instead of numeric entities: `&#x202E;` decodes to the same codepoint in the DOM, so it would still reorder text.
- **Excerpts.**
  - utf8: `<pre class="excerpt">` plus `text(data)`.
  - base64: a labelled `<p>` reading "base64, not decoded", plus `<pre class="b64">` containing the escaped base64 string.
  - null: "excerpt not included: <inline_reason>".
- **Anchors.** Ids come only from fixed ASCII prefixes, decimal indexes and schema-validated 64-hex ids: `section-{identity,markers,omitted,bundles,sources}`, `marker-<n>`, `bundle-<i>`, `bundle-<i>-hop-<j>`, `source-<64hex>`. Every href is `#` plus an existing id.
- **Sections.**
  1. `header#section-identity` is a `<dl>` with: report_format, rendering_version, options, envelope_sha256, `json_report_sha256` (sha256 of the JSON artifact bytes, which binds the two artifacts), and all `view.identity` fields plus tokenizer/token_usage (labelled diagnostic).
  2. `section#section-markers` is a table with one row per `view.markers` entry (`id=marker-<n>`): name, pointer, value.
  3. `section#section-omitted` lists the omitted pointers, each with its value state.
  4. `section#section-bundles` has one `article#bundle-<i>` per bundle: kind, id, rank, and the node identities (id, name, kind, path). It also shows:
     - for definition bundles, the `path:line:column–end_line:end_column` location;
     - for lexical_file bundles, path, match_tier and score;
     - a hop table with one `tr#bundle-<i>-hop-<j>` per hop. Its cells are relation, traversal_direction, resolution, unresolved_reason, raw_target, source→target entity ids, the `path:line:column–end_line:end_column` location, `bytes [start_byte, end_byte)`, source_sha256, source_availability, and `<a href="#source-<id>">`.
  5. `section#section-sources` has one `article#source-<id>` per unique source: kind, state, reason, path, byte range, sha256, size, provenance, inline_reason, resource ids, and the excerpt.
- **Output.** UTF-8 with no BOM, LF only and a trailing LF, written with `write_bytes`.

**D7 Markers (fixed order; `pointer` is RFC 6901; `value` is verbatim).**
- Envelope: `/evidence_status`, `/bounds/element_limit`, `/bounds/truncated`, `/bounds/lists`.
- Selection: `/data/selection/{total_count,has_more,reason}`, `/data/selection_truncated`, `/data/selection_reason`, `/data/has_more`, `/data/total_count`.
- Retrieval: `/data/retrieval/{truncated,truncation_reason,diagnostic_reason}`.
- Collection: `/data/collection_stop`.
- Coverage: `/data/coverage/{state,source_policy,excluded_counts}`.
- Budget: `/data/effective_limits`, `/data/work`.
- Per frontier i: `/data/frontiers/i/{outcome,stop_reason,target_state}`.
- Per bundle i: `/data/bundles/i/rank/source_unavailable`; per source j: `/data/bundles/i/sources/j/{state,reason,inline_reason}` (a missing key is skipped); per hop: `…/assertion/{resolution,unresolved_reason}` and `…/source_availability` under `records/proof` or `records/hops/k`.

The HTML shows null as `none`, booleans and numbers as canonical JSON, and strings escaped. Nothing is summarised as "complete".

**D8 Output caps.**
- JSON ≤ `MAX_JSON_REPORT_BYTES` = 3 MiB: the stripped envelope (≤ 1 MiB canonical) plus the view, which at most re-copies per-hop and per-source identity fields (≤ ~2×).
- HTML ≤ `MAX_HTML_REPORT_BYTES` = 8 MiB: worst-case escaping is 6× (`&quot;`) and the placeholder is ~5× a 6-byte JSON escape, plus bounded markup (≤ 200 elements per list).
- Above either cap → `report_too_large`, and nothing is written. Both caps are keyword arguments of `export`, so the test reaches them with small values. They are not CLI options and do not change output bytes.

**D9 CLI.**
- **Arguments.** `ENVELOPE` is `click.Path(exists=True, dir_okay=False, path_type=Path)`; `--html` and `--json` are required `click.Path(dir_okay=False, path_type=Path)`.
- **Usage errors (exit 2).** Any of the three resolved paths equal, or an output resolves to ENVELOPE.
- **Run.** Read ≤ 4 MiB + 1 bytes (`OSError` → exit 1 `Evidence report rejected: invalid_input`), call `export`, then `mkdir(parents=True, exist_ok=True)` and `write_bytes` for the JSON, then the HTML. Both are rendered in memory first, so a rejection writes nothing and existing files are untouched.
- **Rejection.** `ReportRejected` → `click.ClickException(f"Evidence report rejected: {code}")`, exit 1.
- **Unexpected error** → `Evidence report failed: <TypeName>`. `str(exc)` and paths are never printed.
- **stdout** is empty, nothing is logged, and the command makes no network or MCP calls.

### Limitations (documented in `docs/evidence-reports.md`)
1. Context envelopes only. Impact, page/continue, retrieve and validation-plan inputs are rejected (follow-up).
2. Continuation pages are neither merged nor fetched. `has_more` is shown, and the cursor is omitted.
3. Lease URIs and cursors are omitted; a report cannot fetch more evidence.
4. `data.repository` (an absolute server path) is retained because it is identity-bearing. Review before sharing.
5. Default bounds only: 1 MiB canonical envelope and 200 elements per list.
6. base64 excerpts are not decoded. Control and bidi codepoints are placeholders in the HTML; the JSON keeps the exact text.
7. The snapshot scan covers envelope, data and resources. Records are bound via `retrieval_record_id`.
8. Line and column values are shown as recorded.
9. The CSP meta is defence in depth only; the markup is inert by construction.

### Work plan (timebox 2 engineering days)

Day 1 AM, qualification (a scratch script; output to `.spectra/evidence/aci-s37/qualification.json`):
- (a) Real envelopes from the S18 fixture: default, `source_policy=references`, `target={"name":"validate_payment"}`, truncated (`continuation=True, max_bytes=40000`) and source-free import. Each passes `STRUCTURED_VALIDATOR` and `validate_context(...,200)` on the full input. Record for each: snapshot null or not, resource count, `next_cursor` set, hop and relation counts, and size.
- (b) After stripping `uri`/`next_cursor`: recomputing `retrieval_record_id` (17-key domain), every resource id and every source id equals the input.
- (c) `app/hostile.py` holds `def process_payment_audit()`, a docstring with `</pre><script>alert(1)</script>"><img src=x onerror=alert(2)>`, and a literal U+202E. Query `process_payment_audit`: its definition source is inline utf8 and contains those strings. Also check:
  - whether a non-UTF-8 byte in an indexed file yields a base64 inline;
  - whether `text="process_payment <script>x</script>"` yields an ok envelope;
  - whether a filename containing `"><` is indexed (optional).
- (d) Regeneration at `/tmp/atlas-s37-evidence/repo` works. `python -m atlas_aci --help` works.
- (e) Base invariants go to `base-invariants.json`: the tools sha and bytes, 3436/3448/797/7730, and the full pytest result at base.

STOP RULE:
- (a) fails for default, path or truncated → BLOCKED with the observed facts.
- (b) fails → stop and return to the orchestrator, because decision 2 would break AC-001. Never substitute mocks.
- (c) excerpt part fails → put hostile text into a partial-span inline of a copied real envelope, re-derive the bundle id and `retrieval_record_id` with `leases.digest` (S18 :184-191 pattern), and keep the request-text label.
- (c) base64 part fails → test the base64 branch through `render_html` on a copied envelope with re-derived ids.
- (c) request-text part fails → hostile `request.text` on a copy with a re-derived `retrieval_record_id`.

Day 1 PM:
- `evidence.py` `load_envelope`, stripping, `build_report`, `render_json`, and the CLI.
- Tests for AC-001, AC-003 (JSON half), AC-007, typed errors and the CLI.

Day 2 AM:
- Template and `render_html`.
- Tests for AC-002, AC-003 (HTML half), AC-004, AC-005 and the package resource.

Day 2 PM:
- Regenerate the frozen fixtures and goldens, then AC-006, isolation, docs, CHANGELOG and README.
- Gates in the lock-faithful dev image, then RUN-01 and RUN-02.

Cut order (each cut becomes a listed limitation):
1. README row.
2. Hostile-filename variant.
3. Subprocess replay under a varied environment.
4. Real `continue`/error `unsupported_operation` envelopes, replaced by a schema-valid copy with only `operation` changed. `continue` stays valid because it shares `CONTEXT_DATA`.
5. The real base64 fixture, replaced by the copy route.
6. Docs depth.

Never cut: the D2 order, stripping, `text()` escaping and the whitelist, golden replay, the D5 scan, the caps, and the isolation pins.

## Independent checker conditions

Checker `atlas-s37-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s37.review.json`). Every finding below is a binding plan condition and overrides any conflicting text above. Orchestrator note (2026-11-04): conditions 1 and 2 replace the default-query and single-truncation fixture definitions; if Day-1 shows condition 4's wire comparison differs, the structuredContent-file input claim is dropped from D1/docs.

1. BLOCKER. The default query yields no `resources`, no `uri` and no `next_cursor`.
   - Evidence: `server.py:472-482` leases only when `continuation=True`. `query/context.py:293-341` calls `presentation` without `resources`, so `resources` is `[]`. `resources[].uri` and `next_cursor` come only from `query/context_pages.py:46-155`. `test_s18_contract.py:137-146` replays the default call, and `test_s18_pages.py:226` shows `resources` empty on import.
   - Effect: "E_inline: default query" with "resources >= 1" is impossible. AC-007 tampers (1), (2), (3) and (5) and the stripping assertions (i) and (vi) need resources.
   - Condition: E_inline, E_refs, E_path and the frozen inline and path-references fixtures must use `continuation=True`. The default max_bytes gives a single page: resources present, cursor null. The helper must run each call inside one `asyncio.run` and end with `await registry.aclose()` (`test_s18_pages.py:38-81`). Day-1 (a) must assert `resources` is non-empty and that every E_* used as an AC-007 base has `snapshot_id` non-null.

2. BLOCKER. "Truncated (`continuation=True, max_bytes=40000`) yields has_more=true / byte_limit" is half false.
   - Evidence: `context_pages.py:80-94` calls `presentation` without `reason`. A page therefore has `selection.reason` null, `selection_reason` null and `selection_truncated` false, even though `has_more` is true.
   - `byte_limit` appears only in the non-continuation path (`query/context.py:299-333`), where `has_more` is false, `selection_truncated` is true, `total_count` exceeds `returned_count`, and resources and cursor are empty.
   - `has_more` true is supported: `test_s18_pages.py:45` (BytesTokenizer) and `:158-165` (default tokenizer).
   - Condition: define two truncation fixtures.
     - E_trunc_page: `continuation=True, max_bytes=40000`. Assert `has_more` true, `/data/next_cursor` omitted as `redacted`, and `selection/reason` null.
     - E_trunc_budget: no continuation, small `max_bytes` (for example 12000). Assert `selection_truncated` true, `selection_reason` `byte_limit`, `has_more` false, and that the HTML shows no "complete" wording.
   - The frozen `truncated` fixture must be the page variant with at least one resource and a non-null cursor, so stripping is proven. Add the budget variant either as a fifth frozen fixture or in-test only. Fix the AC-003 row and the STOP-RULE (a) wording.

3. MAJOR. The exception and recursion surface is under-specified.
   - Evidence: `validate_context` (`context.py:326-600`) raises `ValueError` and, via `parse_uri` (`resources.py:79-80`), `ToolError`. `base64`, `UnicodeEncodeError` and `ev.validate_record` errors are `ValueError` subclasses.
   - The server catches `(TypeError, ValueError, OverflowError, RecursionError)` (`results.py:154`). The plan omits `OverflowError` and `RecursionError`. A 4 MiB `{"a":{"a":...}}` makes `json.loads` or `ev.canonical` raise `RecursionError`.
   - Condition: in D2 steps 2 to 4 and 7, map any `Exception` (including `RecursionError`, `OverflowError`, `UnicodeError`) to the step's code. Never let one escape as `Evidence report failed`. Add a deep-nesting test and a `1e999` test.
   - Also, `validate_context` must be called with `min(200, env["bounds"]["element_limit"])`, mirroring `results.py:143`. If `bounds.element_limit` is above 200, return `invalid_envelope`.

4. MAJOR. The claim that `structuredContent` equals `json.loads(content[0].text)` holds in process only.
   - Evidence: `sdk_result` (`results.py:43-59`) sets both from one dict, so the Python attribute equals the text. The wire form uses `model_dump_json(by_alias=True, exclude_none=True)` (`results.py:38-40`). Whether `exclude_none` drops null-valued keys inside the nested dict is unverified, and `snapshot_id`, `reason` and `next_cursor` are legitimately null.
   - Condition: Day-1 compares `json.loads(result_bytes(result))["structuredContent"]` with `json.loads(content[0].text)` on a real E_inline and E_trunc_page. If they differ, drop the "structuredContent file" input claim from D1 and the docs, and have AC-006 use only the in-process attribute copy.

5. MINOR. `view.identity.query_digest` is ambiguous. `data.query_digest` (`context.py:70`, which binds `structured`) and `data.retrieval.query_digest` both exist. Pin the pointer (use `/data/query_digest`) and add it to AC-001 (v).

6. MINOR. `assertion.unresolved_reason` is `{"type":"null"}` in the schema (`graph.py:337`) and `target_entity_id` is non-null in any valid context bundle (`context.py:417-421`). The "unresolved" signal is therefore `resolution` INFERRED, `source_availability`/`source.state` unavailable, `reason`, `candidate_ids` and `/data/bundles/i/rank/source_unavailable`. Condition: add `assertion/candidate_ids` (its length) as a marker. The docs must say `unresolved_reason` is always none in `atlas.graph.v1` context. No AC-003 assertion may require it non-null.

7. MINOR. The HTML rendering rules need two additions.
   - Browsers drop the first LF after `<pre>`, and the `html.parser` test would not see it. Emit one extra LF after the opening `<pre>` and make the AC-004 round-trip account for it.
   - The codepoint table omits Unicode tag characters U+E0001 and U+E0020-E007F (invisible text). Add them. The "no Unicode-version dependence" rationale still holds.

8. MINOR. Ruff `select` includes `RUF` (`pyproject.toml:70`), so RUF001 flags the en dash in the planned `path:line:column–end_line:end_column` strings, and a literal U+202E in tests is a hazard. Use ASCII `-` or `\u` escapes in src and tests.

9. MINOR. The Python version differs by environment: the dev image is 3.12 (`Dockerfile.dev:32`) and CI is 3.14 (`ci.yml:108`). The plan only mentions macOS to Linux.
   - Condition: run `--regenerate` in the Linux dev image. `provenance.json` records the python and tree-sitter-language-pack versions, since producer fingerprints enter the ids. Tests only replay frozen envelopes.
   - Confirmed safe: `validate_context` never verifies the lease HMAC. `parse_uri` checks shape only (`resources.py:45-80`). Frozen envelopes therefore validate on CI.

10. MINOR. CLI details.
    - `ClickException` prints `Error: Evidence report rejected: <code>` on stderr. The exact-stderr test must include that prefix. Use `result.stderr`; click is 8.3.2.
    - Write both outputs to sibling temp files and `Path.replace` them. Otherwise an `OSError` on the second write leaves a partial pair. `OSError` on write or mkdir must give `Evidence report failed: <TypeName>`, exit 1.
    - Use `Path.samefile` where the file exists, so hardlinks to ENVELOPE are caught.
    - The scope entry `README.md` means the root README (docs table at line ~934), not `mcp-server/README.md`.

11. MINOR. Size and fixture caps. The 1 MiB canonical cap is coherent, since a valid envelope is at most `max_response_bytes` (`config.py:23`). The 4, 3 and 8 MiB caps are generous. Day-1 must record the real sizes. If any frozen file exceeds 512 KB, shrink via `max_bytes` or a narrower query, never by editing bytes.

12. MINOR. D1's "no retrieval by construction" is overstated. Importing `protocol.graph` transitively loads `query.*` through `_register_context_schemas` (`graph.py:1213-1255`). Reword to "no retrieval call is made". Keep `evidence.py` importing `protocol.graph` first and never `protocol.context` directly, to avoid the circular import.

**Verified OK, no condition needed**
- Identity derivations match the plan.
  - Record id: 17 keys at `context.py:579-597`.
  - Resource id: `digest(descriptor - {id, uri})` at `:543-544`.
  - Source id: excludes `id`, `inline` and `inline_reason` (`:483`).
  - Bundle id: excludes `id` and `rank` (`:364`, `context_bundles.py:305`).
  - `leases.digest` is `sha256(ev.canonical(...))` (`leases.py:37`).
  - `uri` and `next_cursor` are outside every identity domain, so stripping keeps all ids recomputable.
- D5 is correct.
  - Only `data` and `resources[]` carry per-item generation and snapshot (`context.py:124-125`, `:251-252`). No revision vector exists in the schema.
  - `data.generation_id` is non-null, `data.snapshot_id` is nullable, and `validate_context` forces equality with the envelope (`:337-341`).
  - A real null-snapshot envelope can only be resource-free, because `RESOURCE.snapshot_id` is a non-null hash. The null-as-value rule therefore never falsely rejects a real envelope.
  - A consistent whole-snapshot change fails at `:538` (a `ValueError`) and so gives `invalid_envelope`.
- Packaging: hatch `packages=["src/atlas_aci"]` ships `.html`, and `.gitignore` has no `*.html` rule. `reports/__init__.py` suffices for `importlib.resources`.
- No test enumerates src modules or packages in a way a new package trips. `test_thesis_negatives.py:21` and `test_export.py:372` grep all `*.py` for forbidden strings ("alter table", `import anthropic`, `nx.compose(`, merge-driver regex), so `evidence.py` must avoid those phrases. `test_s18_docker.py:109` hashes `*.py` generically.
- CLI tests pin subsets only (`test_s21_isolation.py:92`, `test_s22_isolation.py:56-57`). `python -m atlas_aci` works via the `__main__` guard (`__main__.py:849`).
- Hostile content: a docstring inside a Python definition span is inlined (spans cover the whole def, `useful-spans.v1.json`, up to 8192 bytes). `request.text` accepts any string up to 4096 bytes without NUL (`retrieval.py:35`). Base64 inline is reachable (`context_bundles.py:298-301`), but whether the indexer accepts a non-UTF-8 source is unverified; the copy route is the fallback.
- Scope: no missing file and no gated path implied. `test_s22` and `test_s23` pins are unaffected.

## Stories

As a reviewer, I want one bounded context answer as a static page whose every hop shows its relation, location and source excerpt, so I can audit the evidence path without a server or a repository dump.

As an agent operator, I want a canonical JSON twin with the same snapshot and evidence ids and every truncation or unknown marker, and with live credentials removed, so the artifact replays byte-for-byte and is safe to attach.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s37.criteria.md` (sha256 `31a6c71b…be55`), verbatim:

```
### AC-S37-001 (event-driven)
GIVEN a valid bounded evidence envelope is supplied
WHEN the report exporter renders it
THEN the canonical JSON report SHALL preserve the input envelope's snapshot and evidence identities.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_json_preserves_response_identity

### AC-S37-002 (event-driven)
GIVEN the envelope contains provenance-bearing evidence paths
WHEN the static report is rendered
THEN the HTML report SHALL expose each included evidence path's source locations and relation labels.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_html_has_source_paths

### AC-S37-003 (event-driven)
GIVEN the supplied evidence envelope is incomplete or budget-truncated
WHEN the exporter renders that envelope
THEN both report formats SHALL display the input envelope's unresolved and truncation markers.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_report_preserves_incompleteness

### AC-S37-004 (unwanted-behavior)
GIVEN a fixture includes script tags, event attributes or closing-tag injection in a label or excerpt
WHEN the report is rendered
THEN the HTML exporter SHALL render source-derived markup as inert escaped text.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_html_escapes_source_content

### AC-S37-005 (ubiquitous)
THEN the generated HTML SHALL require no external assets or executable scripts.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_report_is_self_contained

### AC-S37-006 (event-driven)
GIVEN identical input envelope, rendering version and explicit options
WHEN the exporter writes the same scoped report twice
THEN the HTML and JSON artifact bytes SHALL remain identical after repeat export.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_report_replay_is_idempotent

### AC-S37-007 (unwanted-behavior)
GIVEN an evidence item identifies a snapshot outside the envelope's declared snapshot or revision vector
WHEN report-input validation runs
THEN the exporter SHALL reject the envelope with a mixed_snapshot_evidence error.
VERIFY: test: mcp-server/tests/slices/test_s37_evidence_report.py::test_evidence_mixed_snapshot_rejected

### AC-S37-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S37-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

**In-test envelopes** (real, built with the S18 `setup` pattern copied locally):
- E_inline: default query.
- E_refs: `source_policy=references`.
- E_path: target `validate_payment`.
- E_trunc: `continuation=True, max_bytes=40000`.
- E_import: source-free import, partial.
- E_hostile: `app/hostile.py`.
- E_label: hostile request text.

**Frozen fixtures** (`tests/fixtures/evidence_report/`): `{inline,path-references,truncated,hostile}.envelope.json`, each with `.report.json` and `.report.html`, plus `provenance.json`. `provenance.json` records the base commit, the fixed repo path, the query arguments and the sha256 of every file. The fixtures are regenerated only with `uv run python tests/slices/test_s37_evidence_report.py --regenerate`. Their lease tokens come from a discarded per-process secret and are inert.

No mocks. Every `all(...)` is preceded by a non-empty assertion. A local `html.parser` helper collects tags, attributes, text by id and the pre/end-tag balance.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_evidence_json_preserves_response_identity | For E_inline, E_refs, E_path, E_trunc, E_import and the 4 frozen fixtures, through library and CLI:<br>(i) `report.envelope == deepcopy(input)` with exactly the D3 keys deleted; `omitted_fields` equals the expected pointers.<br>(ii) The identity multiset (pointer, value) of every `id`/`*_id`/`source_sha256`/`generation_id`/`snapshot_id`/`repository` key equals the input's, and is non-empty.<br>(iii) Recomputing `retrieval_record_id`, every resource id and every source/bundle id from `report.envelope` with `leases.digest` equals the input.<br>(iv) `envelope_sha256 == sha256(canonical(report.envelope))`.<br>(v) Every `view` value equals the pointed input value.<br>(vi) No `atlas-source://`, token or `next_cursor` value appears in the JSON or HTML bytes; E_trunc proves a non-null cursor existed.<br>(vii) JSON bytes == canonical(report) + LF. |
| 002 | test_evidence_html_has_source_paths | E_path, E_inline and frozen path-references:<br>– The hop row count == the total hops (> 0, with a path bundle of ≥ 1 hop present).<br>– Each row `bundle-i-hop-j` text contains the relation, direction, resolution, `path:line:column–end_line:end_column`, `bytes [s, e)` and source_sha256.<br>– Its `#source-<id>` href resolves to an article containing the same path and byte range.<br>– Definition bundles show their occurrence location. |
| 003 | test_evidence_report_preserves_incompleteness | E_trunc, E_import, E_refs and frozen truncated:<br>– For every D7 marker: the JSON `view.markers[n].value` == the input value at `pointer`, and the HTML `marker-n` row shows `none` iff the value is null, else the escaped canonical value (a total mapping).<br>– Specific checks: E_trunc `has_more` is `true` and reason is `byte_limit`; the omitted `/data/next_cursor` is `redacted`; E_import `evidence_status` is `partial` and every source is `unavailable` with its reason; E_refs `inline_reason` is `references_policy` and shows "excerpt not included".<br>– No marker reads "complete" unless the input says so. |
| 004 | test_evidence_html_escapes_source_content | E_hostile, E_label and frozen hostile:<br>– The parsed tag set contains no `script` or `img`, and no attribute starts with `on`.<br>– `<pre>` starts == ends == the excerpt count.<br>– Unescaping each excerpt's text equals the input `inline.data` with placeholders mapped back.<br>– The raw bytes contain no `<script` (case-insensitive), `<img`, or U+202E; the `U+202E` placeholder is present.<br>– The label `<script>` appears only as `&lt;script&gt;`.<br>– Positive control: the source text really contains each hostile string. |
| 005 | test_evidence_report_is_self_contained | Every HTML produced in this module, plus the template file:<br>– Tags ⊆ {html, head, meta, title, style, body, header, main, section, article, footer, nav, h1, h2, h3, p, dl, dt, dd, table, thead, tbody, tr, th, td, ul, ol, li, pre, code, a, span}.<br>– Attributes ⊆ {lang, charset, http-equiv, name, content, id, class, href}.<br>– Every href starts with `#` and targets an existing id; ids match the D6 pattern.<br>– There is no `src`, `srcset`, `style` attribute, `<link>`, `<base>`, `<form>`, `<iframe>`, `<object>`, `<embed>`, `<svg>` or `<math>`.<br>– The `<style>` text has no `url(`, `@import`, `//` or `http`.<br>– The CSP and referrer metas are present with exact content.<br>– Text outside text nodes contains no `:` scheme. |
| 006 | test_evidence_report_replay_is_idempotent | Each frozen envelope:<br>– Two in-process exports plus two CLI runs into different paths all equal the committed goldens byte-for-byte.<br>– One `python -m atlas_aci evidence-report` subprocess with `TZ=Pacific/Kiritimati`, `LC_ALL=C` and `PYTHONHASHSEED=123` also equals them.<br>– An indent=2 pretty-printed copy and a `structuredContent` copy of the same envelope give identical bytes.<br>– E_inline from `content[0].text` and from `structuredContent` gives identical reports, rendered twice.<br>– The frozen envelopes still pass current validation. |
| 007 | test_evidence_mixed_snapshot_rejected | Base: E_inline (non-null snapshot, resources ≥ 1, per Day-1 (a)). Rejected as `mixed_snapshot_evidence` (library and CLI: exit 1, exact stderr, neither output created, a pre-existing sentinel output unchanged):<br>(1) `resources[0].snapshot_id` set to another hash, ids untouched;<br>(2) the same with the resource id re-derived and the uri rebuilt via `resources.next_uri`;<br>(3) `resources[0].generation_id` foreign;<br>(4) `data.snapshot_id` ≠ envelope;<br>(5) declared snapshot nulled (envelope and data) while resources stay non-null.<br>Controls: untampered E_inline is accepted; a consistent whole-snapshot change (envelope, data and all resources) gets `invalid_envelope` and not mixed; E_import is accepted. |
| RUN-01 | gate | SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt path is `.spectra/state/aci-s37.execution.json`. |

Supporting tests (same file):
- **Typed errors.** Each code is reached:
  - over 4 MiB raw, and a 1 MiB canonical cap via a padded real envelope;
  - non-UTF-8, BOM, NaN, duplicate key, lone surrogate → `invalid_input`;
  - schema failure and report re-input → `invalid_envelope`;
  - real `continue` page, real `INVALID_BUDGET` error envelope and real impact envelope (via `test_s20_support`) → `unsupported_operation`;
  - small `max_html_bytes`/`max_json_bytes` → `report_too_large`.
  - In every case `str(exc) == code`.
- **CLI.** Exit 2 for missing `--html`/`--json`, outputs equal to each other or to ENVELOPE, and a nonexistent ENVELOPE. Exit 1 messages contain no tmp path. A successful run creates exactly the two files in an empty directory and leaves stdout empty.
- **Package resource.** `importlib.resources.files("atlas_aci.reports")/"evidence_template.html"` bytes equal the source file. The template identifiers equal the D6 list. pyproject's wheel `packages` includes `src/atlas_aci`.
- **Isolation.**
  - Tools JSON sha and bytes are the base values; `READ_ONLY_TOOLS` has 7 entries.
  - `"evidence-report" in cli.commands` and the command is absent from the tools JSON.
  - 3436/3448/797/7730 unchanged.
  - The D1 AST import allow-list and deny-list.
  - No `.gitattributes`.
  - The docs mention every code and the omitted fields.
  - Fixture directory under 2 MB, each file under 512 KB.

Gates beyond the ACs:
- G-SRC: `git diff --exit-code 7b00944 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage} mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows`, and `git diff --name-only 7b00944 -- mcp-server/src` ⊆ {`reports/*`, `__main__.py`}.
- G-MCP: tools JSON sha `94f9567a…` / 1631472 bytes.
- All of `test_s13_*`, `test_s18_*`, `test_s20_cli` and `test_export` pass unchanged.

Commands (record exits and any skips; no required skips):
```sh
cd mcp-server
uv run pytest tests/slices/test_s37_evidence_report.py -q
uv run pytest tests/slices/test_s13_*.py tests/slices/test_s18_*.py tests/slices/test_s20_cli.py tests/test_export.py -q
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/
```

## Confidence

To be computed with `ramza-score`. The slice is a pure, stdlib-only transformation over an envelope that already has an authoritative validator and an explicit identity domain. Nothing upstream changes, so pins cannot churn. The remaining uncertainties are all covered by the Day-1 checks and the stop rule:
- whether hostile content can be placed in a real indexed excerpt and request label;
- whether the import envelope has a null snapshot;
- whether base64 excerpts are reachable;
- golden fixture size.

## Rejected Alternatives

- **Hand-building the whole HTML in Python.** It removes the packaging risk but the markup is hard to review, and the issue declares a template file.
- **Jinja2/markupsafe.** It adds a dependency, which breaks `test_no_new_runtime_dependency`.
- **Embedding the JSON in `<script type="application/json">` and rendering client-side.** It violates "no executable scripts".
- **Carrying `uri`/`next_cursor` with a warning.** Live capability tokens would end up in a shareable artifact, and they add nothing to identity.
- **Hashing the raw input or the unstripped canonical input for `envelope_sha256`.** The first depends on how the file was saved (text versus `structuredContent`, indentation). Both cover credentials and cannot be checked from the report alone.
- **Supporting impact now.** It has a second shape with no excerpts and a second marker vocabulary, which does not fit the 2-day timebox. It is an explicit follow-up.
- **Merging continuation pages.** That needs live cursors (the "hidden follow-up expansion" the issue excludes).
- **Numeric entities for bidi controls.** They re-materialise the codepoint.
- **Using `unicodedata` categories.** Output would depend on the Python Unicode version.
- **`--output-dir` with fixed names, or stdout.** Less explicit, and stdout does not fit two artifacts.
- **Relocating envelopes by re-deriving ids.** The snapshot and generation ids bind the repo path (`snapshots.py:38-64`), so a fixed-path regenerator is used instead.

## Risks

- **Path disclosure.** `data.repository` exposes the server's absolute path. It is identity-bearing, so it cannot be removed. Docs and the HTML footer warn about it, and the frozen fixtures use `/tmp/atlas-s37-evidence/repo`.
- **Template missing from wheel or image.** Covered by the resource test and the orchestrator's image check.
- **Golden drift.** If the protocol changes later, the frozen envelopes are flagged by the validity check in AC-006. Regenerate with `--regenerate`; this is never silent.
- **Envelopes from non-default servers.** Envelopes with `max_response_bytes` above 1 MiB or `element_limit` above 200 are rejected as too large or invalid. This is documented.
- **Weak AC-007 tampering.** A tamper that hits the generic identity check instead of the scan would weaken AC-007. Mitigation: the scan runs first, and control (2) shows that re-derived ids still produce `mixed`.
- **Cross-OS byte identity.** Pure-Python rendering with LF and bytes I/O; CI on Linux re-checks the macOS-generated goldens.
- **Timebox of 2 days.** Follow the cut order. At the limit, report completed checks and a narrow follow-up; no silent growth.
