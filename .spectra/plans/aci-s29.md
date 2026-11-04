---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T03:00:00Z
slice_id: ACI-S29
issue: https://github.com/Rynaro/atlas-aci/issues/50
---
# ACI-S29 - import one OpenAPI 3.1 JSON document and explain contract changes between exact snapshots

## Scope

Issue #50 on base `981a897` (main after PR #90 / ACI-S32), branch `aci-s29`. Marker `<!-- atlas-aci:slice:ACI-S29 -->` verified. There is
no prior S29 plan, receipt or execution state. The criteria bytes hash to `7c017111001f65be8237f420e0175f7bda7b2724a9f39b2e8053247b0c6f8563`,
which matches the contract table (7 product ACs plus RUN-01 and RUN-02). Dependency S19 (#40) is an ancestor of HEAD with a COMPLETE receipt.
Its change-set contract is the interface reused here: `query/diff.py` `FORMAT` :33, `_ref` :252, stable sort :565,
`id = sha256(FORMAT + ":" + ev.canonical(rest))` :583, `canonical_bytes` = canonical JSON + LF :587.

Observed facts (re-verified at planning; `M/` = `mcp-server/src/atlas_aci/`):
- S19 is graph-only. `_Loaded.load` treats every non-skipped entity as a source symbol, and `_check_compatible` needs graph snapshot ids. The producer
  fingerprint hashes `codegraph.py`, `storage/evidence.py` and others (`codegraph.py:1440-1463`), so a graph-backed contract kind churns every pin.
- Strict JSON precedent: `M/architecture/rules.py:191-260`. It is private, and `rules.py` imports jsonschema, so it is copied here, not imported.
- RFC 6901: `M/reports/evidence.py:201-217`. Its `resolve_pointer` uses `int(key)`, so it accepts `-1` and `01`. It serves only as a test oracle.
- Canonical form: `ev.canonical` (`M/storage/evidence.py:181`): sort_keys, compact, `ensure_ascii=False`, `allow_nan=False`. Lone surrogates
  make the later UTF-8 encode raise, so they are rejected at parse.
- CLI discipline: `check-architecture` (`__main__.py:834-924`) and `counterfactual` (957-1094). Both use fixed texts, `from None`, binary
  stdout, exit 0/1/2 and empty stdout on failure.
- Pins: 7 tools, tools JSON sha `94f9567a...a56ac56` / 1,631,472 bytes; MINIMUM 3436, COLLECTION 3448, FIXED_ERROR 797, IMPACT 7730
  (`test_s31_architecture_rules.py:1131-1147`). "openapi" occurs nowhere in src today.
- Python: dev and production images are 3.12 (`Dockerfile:32,55`); CI is 3.14 (`ci.yml:108`).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)
1. **Architecture = option B.**
   - A standalone, content-addressed contract snapshot `atlas-contract-snapshot.v1` is built by `importers/openapi31.py`.
   - The differ `contracts/openapi_diff.py` emits `atlas-contract-diff.v1`. It reuses S19's change-set contract by shape and vocabulary: format
     string, content id, `before`/`after` refs, `summary` by kind, `limitations`, canonical bytes plus LF, stable sort. It never touches the graph.
   - "Through S19" therefore means reusing the S19 change-set contract.
   - Option A (graph-backed import) is REJECTED. It requires edits to the producer-fingerprint inputs `codegraph.py`, `storage/evidence.py` and
     `query/diff.py`, which invalidates every pinned graph fixture and exceeds this P1, 3-day slice.
   - AC-002's "snapshot identity" is the contract `snapshot_id` (artifact sha256 + profile + options). Docs state that it is distinct from
     source and graph snapshots. Binding a contract file to a repo snapshot is a follow-up.
2. **Strict JSON.** The S31 approach is copied, not imported:
   - a byte-level `_check_nesting` with an explicit `MAX_NESTING_DEPTH`, independent of the interpreter;
   - rejection of BOM, duplicate keys, NaN/Infinity and lone surrogates; a bounded read;
   - all traversal iterative, with no Python recursion anywhere;
   - hand-written closed checks, no jsonschema.
3. **Profile `atlas-openapi31-profile.v1`.**
   - `openapi` must match `^3\.1\.\d+$`. swagger, 3.0, 3.2 and prerelease documents are rejected as whole documents.
   - YAML is not supported.
   - RFC 6901 helpers are copied locally.
4. **Identity** = (UPPERCASE method, path template with every `{name}` replaced by `{}`).
   - The original template is kept as metadata; operationId is metadata only.
   - When two operations in one document share a normalized identity, both are marked incomplete with `ambiguous_identity`, and neither gets
     definitive findings.
5. **Effective parameters.**
   - Operation parameters override path-item parameters on (in, name). Header names are lowercased.
   - `in: path` parameters are keyed by template-variable ordinal, so a path-parameter rename becomes review_required.
6. **References.**
   - Only `#/...` same-document references are supported, followed iteratively with a stack.
   - Reasons: `remote`, `outside_document`, `target_missing`, `cyclic_reference`, `reference_depth_exceeded`, `reference_expansion_limit`.
   - Diagnostic `unresolved_reference` marks the enclosing region incomplete.
   - Schema subtrees are not dereferenced for implication, but every `$ref` inside them is classified.
7. **Findings.**
   - `operation_removed` is emitted only when the after side's relevant region is complete; otherwise review_required.
   - `newly_required_parameter`, with `in != path`.
   - `review_required` with closed reasons, emitted for: any digest-changed region not fully explained by a supported finding, any incomplete
     region on either side, and any changed document-level region.
   - `operation_added` is informational.
   - Ignored fields are listed in `options.ignored_fields`.
   - There is no global verdict, and a vocabulary scan bans verdict words.
8. **CLI.**
   - `atlas-aci openapi-snapshot FILE` and `atlas-aci openapi-diff --before F --after F [bounds]`.
   - Error discipline follows `check-architecture`/`counterfactual`.
   - No `import-*` names: those commands write the graph.
9. **Packaging.**
   - `importers/__init__.py` and `contracts/__init__.py` are added via scope amendment.
   - Fixtures live in `tests/fixtures/openapi/` with `provenance.json`. Deep-nesting, NaN and duplicate-key cases are generated in-test.
   - Docs: `docs/importers/openapi.md`.
10. **Invariance.**
    - No change to server, protocol, query, tools, storage, codegraph, extractors, providers, architecture, counterfactual, indexing,
      resolvers, reports, enforcement, config, pyproject, uv.lock or workflows.
    - The pins above hold.
    - An AST ban applies to importers/contracts, and their source is ASCII only.

### In
1. `importers/openapi31.py` (new):
   - constants and closed vocabularies; `Options`, `normalize_options`, `OpenAPIRejected`, `OpenAPIUsageError`;
   - strict parse; local `pointer`/`resolve_pointer`/`_percent_decode`;
   - `import_document(raw: bytes, options) -> dict`, `validate_snapshot`, `canonical_bytes`.
2. `contracts/openapi_diff.py` (new): `DiffOptions`, `normalize_diff_options`, `ContractDiffRejected`, `diff(before, after, options) -> dict`,
   `validate_diff`, `canonical_bytes`.
3. `importers/__init__.py` and `contracts/__init__.py`: docstrings only.
4. `__main__.py`: two commands, `openapi-snapshot` and `openapi-diff`.
5. `tests/slices/test_s29_openapi_diff.py`: 7 AC tests plus supporting tests.
6. `tests/fixtures/openapi/*.json` and `provenance.json` (D11).
7. `docs/importers/openapi.md`, a CHANGELOG Unreleased entry and a root README docs-table row.

### Out
- OpenAPI 2.0/3.0/3.2, YAML, remote or multi-document fetching, `$id`/`$anchor` resolution.
- JSON Schema implication, auth semantics, server execution, client generation, handler mapping, any compatible/incompatible verdict.
- MCP exposure, graph or evidence writes, and any edit to the decision-10 paths.

Rightsize: computed tier `lite` (score 4). An independent checker still runs.

## Approach

### Decisions

**D1 Strict parse.** Validation order, first failure wins, each with a closed rejection:
1. `len(raw) > max_document_bytes` → `too_large`.
2. BOM `EF BB BF` → `invalid_json`.
3. `_check_nesting(raw)` with `MAX_NESTING_DEPTH = 64` → `nesting_too_deep`.
4. `raw.decode("utf-8")` strict, then `json.loads` with:
   - `object_pairs_hook=_strict_pairs` (duplicate keys);
   - `parse_constant=_refuse` (NaN, ±Infinity);
   - `parse_float=_finite_float` (rejects `1e999` → inf).
   Any `Exception`, including RecursionError, trailing data and an over-long int, → `invalid_json`. YAML input fails here.
5. `_has_surrogate` (iterative, keys and values) → `invalid_json`.
6. Root not an object → `not_an_object`.
7. Root has a `$ref` key → `root_reference`.
8. A `swagger` key, or `openapi` that is not a string matching `^3\.1\.\d+$`, → `unsupported_profile`. This covers 2.0, 3.0.x, 3.2.x, `3.1`,
   `3.1.0-rc1` and numeric values.
9. `paths`/`components`/`webhooks` present but not objects, or `jsonSchemaDialect` not a string → `invalid_document`.
10. Path keys or operations over `max_operations`, or any operation over `max_parameters_per_operation` (path-level + operation-level entries)
    → `limit_exceeded`. This fails closed rather than truncating, which would fake removals.

`REJECTIONS = (too_large, invalid_json, nesting_too_deep, not_an_object, root_reference, unsupported_profile, invalid_document, limit_exceeded)`.

`OpenAPIRejected(ValueError)` carries `.reason`; `str()` is `"openapi_rejected: <reason>"`. It never contains document text or paths.

**D2 Pointers.**
- `pointer(*parts)` escapes `~`→`~0`, then `/`→`~1`.
- `resolve_pointer` is strict:
  - tokens are unescaped `~1` then `~0`; any other `~` sequence is invalid;
  - array tokens must match `0|[1-9][0-9]*` and be `< len`;
  - it returns a sentinel `MISSING`, never raises, and is iterative.
- `_percent_decode(fragment)`: `%XX` hex to bytes, then strict UTF-8 decode. A bad escape or bad UTF-8 is invalid.
- No `urllib` import.

**D3 Profile walk.** Deterministic order. Path keys are processed sorted by code point. Inside a path item:
1. path-level parameters;
2. methods in `METHODS = (get, put, post, delete, options, head, patch, trace)`, which are lowercase keys only;
3. within each operation: parameters, requestBody, responses (sorted by status key), then the remaining regions in `REGION_NAMES` order.

Document regions follow, sorted by pointer. Every dict walk iterates sorted keys. Uppercase method keys are unknown keys (region `other`).

**D4 Path templates and identity.**
- A valid key:
  - starts with `/`;
  - has braces that pair without nesting;
  - has variable names that are non-empty, contain no `{`, `}` or `/`, and are unique within the key.
- `identity = {method: METHOD.upper(), path: key with each {name} replaced by {}}`. There is no trailing-slash, case or percent normalization.
- An invalid key gives a path item record with `identity_path: null`, `status: incomplete`, and diagnostic
  `invalid_path_template/malformed_template`; no operations are enumerated.
- Ambiguity: when two or more operations in one document share (method, identity.path), each gets diagnostic
  `ambiguous_identity/normalized_identity_collision` and `status incomplete`.

**D5 Reference resolution** (iterative; shared `budget.expansions` counter for the whole import).
```
classify(ref): if not str → n/a; if ref starts "#/": decode → ("local", ptr) | ("bad", "target_missing")
               elif ref starts "#": ("bad","outside_document")            # "#", "#anchor" ($anchor/$id unsupported)
               elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:|^//", ref): ("bad","remote")
               else: ("bad","outside_document")                            # relative file / multi-document
follow(use_member_pointer, ref):                                           # Reference-Object positions only
  chain=[]; seen=set(); cur=ref; cur_member=use_member_pointer
  loop:
    if budget.expansions >= max_ref_expansions: fail("reference_expansion_limit", cur, cur_member)
    kind, x = classify(cur); if kind=="bad": fail(x, cur, cur_member)
    if x in seen: fail("cyclic_reference", cur, cur_member)
    if len(chain) >= max_ref_depth: fail("reference_depth_exceeded", cur, cur_member)
    budget.expansions += 1; target = resolve_pointer(doc, x); if MISSING: fail("target_missing", cur, cur_member)
    chain.append(x); seen.add(x)
    if dict(target) and str(target.get("$ref")): cur_member = x + "/$ref"; cur = target["$ref"]; continue
    return Resolved(target, declared_pointer=x, ref_chain=chain)
probe(member_pointer, ref): one step only: classify + (one charged expansion) resolve; never follows chains.
```
- Positions that follow chains: path item, parameter (both levels), operation `requestBody`, each `responses/<code>`.
- Every other string `$ref` member (schemas, callbacks, headers, links, examples, document regions) is `probe`d. These are found by an
  iterative sorted walk of the region subtree.
- A `fail` emits `unresolved_reference` with `{reason, ref: the failing string, region_pointer, provenance.pointer: the failing $ref member}`
  and marks that region incomplete.
- Probe results are cached per member pointer; resolved digests are cached per declared pointer. Shared path items and component parameters are
  therefore processed once.
- AC-005 "cyclic beyond the configured bound": a revisit proves the chain would expand without end, which is beyond any finite bound, including
  `max_ref_depth`. It is reported as `cyclic_reference` at the first revisit, which is earlier than waiting for the bound to be spent. A cycle
  longer than `max_ref_depth` is reported as `reference_depth_exceeded`. Both are `unresolved_reference` diagnostics with the region incomplete,
  and both cases are tested.

**D6 Effective parameters** (per operation; both levels run through the same `keyed`):
```
keyed(list_ptr, value, level): if value absent → {}; if not list → diag invalid_parameter/not_an_array, region incomplete, {}
  for i, item: use = list_ptr/i; obj, declared, chain = follow(use+"/$ref", item["$ref"]) if dict+str $ref else (item, use, [])
    unresolved → continue (already diagnosed)
    checks → invalid_parameter/<reason>: not_an_object | missing_name (name not a non-empty str) | invalid_location (in ∉ query,header,path,cookie)
             | invalid_required (present, not bool) | path_parameter_not_required | path_parameter_not_in_template
    key = ("path", ordinal of name in template vars) if in=="path" else (in, name.lower() if in=="header" else name)
    key already in this level → invalid_parameter/duplicate_parameter (first kept)
effective = keyed(path_item.parameters) then update(keyed(operation.parameters))   # operation overrides on the same key
```
- `PARAM`:
  - `{key:{in,name}|{in:"path",ordinal}, in, name, required: bool (absent→false), level, reserved_header,`
  - `sha256, sha256_without_required, provenance:{artifact_sha256, snapshot_id, pointer: use-site, declared_pointer, ref_chain}}`.
- Digests are taken over the resolved object minus `description` and `x-*`, with `required` normalized to a bool.
- `reserved_header` is true for header names `accept`, `content-type` and `authorization`, which the spec says to ignore.

**D7 Regions and digests.**
- `digest(v) = sha256(ev.canonical(v).encode("utf-8"))`. Absent means `null`.
- Operation regions, in the closed `REGION_NAMES` order:

| Region | Digested value |
| --- | --- |
| `parameters` | `[[key_list, param.sha256]...]` sorted by key |
| `requestBody`, `responses`, `callbacks`, `security`, `operationId` | the operation value verbatim |
| `deprecated` | bool (absent→false; a non-bool is verbatim) |
| `servers` | `{"operation": op.servers, "path_item": pi.servers}` |
| `other` | `{"operation": unknown op keys, "path_item": unknown path-item keys}`, excluding known keys, ignored keys and `x-*` |

- Region record: `{sha256, status, pointers: [existing contributing pointers, sorted]}`.
- Document regions: `/jsonSchemaDialect`, `/servers`, `/security`, `/webhooks/<name>`, `/components/<type>/<name>` (or `/components/<type>`
  when that is not an object), and each unknown non-`x-` top-level key. Each is digested verbatim: `{pointer, sha256, status, provenance}`.
- `IGNORED_FIELDS = (document:openapi, document:info, document:tags, document:externalDocs, document:x-*, path_item:summary,
  path_item:description, path_item:x-*, operation:summary, operation:description, operation:tags, operation:externalDocs, operation:x-*,
  parameter:description, parameter:x-*)`.
- Nothing is stripped inside schemas or any user-named map. A `description` property inside a schema still changes the digest, so the
  outcome is conservative.

**D8 Snapshot `atlas-contract-snapshot.v1`** (closed; canonical + LF; no paths, clock or host data):
```
{format, profile:"atlas-openapi31-profile.v1", options:{max_document_bytes, max_operations, max_parameters_per_operation,
 max_ref_depth, max_ref_expansions, max_diagnostics}, artifact:{sha256, bytes, openapi, json_schema_dialect|null, paths_present},
 snapshot_id, status:"complete"|"incomplete",
 path_items:[{path_template, identity_path|null, status, reasons[], provenance:P}],                 # sorted by path_template
 operations:[{identity:{method,path}, path_template, operation_id|null, path_item_pointer, status, reasons[],
              provenance:P, parameters:[PARAM], regions:{name:{sha256,status,pointers[]}}}],   # sorted (identity.path, method, path_template)
 document_regions:[{pointer, sha256, status, provenance:P}], diagnostics:[D], diagnostics_total,
 counts:{path_items, operations, parameters, references_followed, references_unresolved}, limitations:[...]}
P = {artifact_sha256, snapshot_id, pointer}      D = {kind, reason, ref|null, region_pointer, provenance:P}
snapshot_id = sha256("atlas-contract-snapshot.v1:" + canonical({artifact_sha256, profile, options}))
```
- `provenance.pointer` of an operation is its actual location, which is in `components/pathItems/...` when the path item is a `$ref`.
  `path_item_pointer` is the `/paths/<esc>` use site.
- `DIAGNOSTIC_REASONS`:

| Kind | Reasons |
| --- | --- |
| `unresolved_reference` | the six D5 reasons |
| `invalid_path_item` | `not_an_object`, `reference_with_siblings` |
| `invalid_path_template` | `malformed_template` |
| `invalid_operation` | `not_an_object` |
| `invalid_parameter` | `not_an_array`, `not_an_object`, `missing_name`, `invalid_location`, `invalid_required`, `path_parameter_not_required`, `path_parameter_not_in_template`, `duplicate_parameter` |
| `ambiguous_identity` | `normalized_identity_collision` |

- Diagnostics are deduped on (kind, reason, pointer, region_pointer) and sorted by (region_pointer, pointer, kind, reason). The first
  `max_diagnostics` are emitted and the total is counted. Region and operation statuses always carry the incompleteness, whether or not the
  diagnostic was emitted.
- `status` is complete ⇔ there are no diagnostics.
- `validate_snapshot` checks: closed enums, sort orders, the `snapshot_id` recompute, emitted ≤ total, and that complete ⇔ no reasons.

**D9 Diff rules** (`differ: "atlas-openapi-diff-rules.v1"`). Group operations by identity on each side.
1. Path items incomplete on either side → `review_required/incomplete_region`, `region: "path_item"`.
2. Identity in before, absent in after:
   - if after has an incomplete path item whose `identity_path` is that path or null → `review_required/incomplete_region` with
     `blocked_finding: "operation_removed"`;
   - else → `operation_removed`, with `after.pointer` = the after path item of the same template, else the same identity path, else `/paths`
     (or `""` when `paths` is absent).
   - The symmetric case (in after, absent in before) gives `operation_added` (informational) or blocked `incomplete_region`.
3. More than one operation per identity on a side, or any ambiguous one → one `review_required/ambiguous_identity` per operation record. No
   other findings for that identity.
4. Common identity: for each region in `REGION_NAMES`:
   - incomplete on either side → `review_required/incomplete_region` (always, even with equal digests);
   - else equal digests → nothing;
   - else `parameters` → D9.5;
   - else → `review_required/region_changed` with `region`.
5. Parameters, over the union of keys in sorted order:
   - only in after: in path → `path_parameter_changed`; required, not reserved → `newly_required_parameter` (`change: "added_required"`);
     otherwise → `parameter_added_optional`;
   - only in before: in path → `path_parameter_changed`; otherwise → `parameter_removed`;
   - both, digests differ:
     - in path → `path_parameter_changed` (covers renames);
     - before optional, after required, not reserved → `newly_required_parameter` (`change: "optional_to_required"`), plus
       `parameter_changed` iff `sha256_without_required` also differs;
     - otherwise → `parameter_changed` (includes required→optional and schema/style changes).
6. Document regions: a digest change, or presence on only one side → `review_required/document_region_changed`. Incomplete on either side →
   `incomplete_region`.

Closed sets:
- `FINDING_KINDS = (operation_removed, newly_required_parameter, review_required, operation_added)`.
- `REVIEW_REASONS = (incomplete_region, ambiguous_identity, region_changed, parameter_added_optional, parameter_removed, parameter_changed,
  path_parameter_changed, document_region_changed)`.
- `action` is `informational` for `operation_added` and `review` otherwise.

**D10 Diff `atlas-contract-diff.v1`** (closed; canonical + LF):
```
{format, profile, differ, options:{max_findings, ignored_fields:[IGNORED_FIELDS]},
 before:{artifact_sha256, snapshot_id, status, operations, diagnostics_total}, after:{...},
 findings:[F], findings_total, selection:{findings_truncated}, summary:{<kind>:n}, review_reasons:{<reason>:n}, limitations:[...], id}
F = {type:"contract_finding", kind, action, reason|null, change|null, blocked_finding|null, identity:{method,path}|null,
     region|null, parameter:{in,name}|{in:"path",ordinal}|null, before:R|null, after:R|null, id}
R = {artifact_sha256, snapshot_id, pointer, declared_pointer}
```
- `F.id = sha256("atlas-contract-finding.v1:" + canonical(F - id))`.
- Sort key: (identity null last, identity.path, identity.method, kind, reason, region, canonical(parameter), before.pointer, after.pointer, id).
- The emitted list is the first `max_findings`; `summary`, `review_reasons` and `findings_total` count all findings.
- `id = sha256("atlas-contract-diff.v1:" + canonical(rest))`, as in S19 :583.
- There is no verdict, status-of-diff or "compatible" key.
- `ContractDiffRejected` reasons: `invalid_snapshot` (validate_snapshot fails or `snapshot_id` mismatch), `profile_mismatch`.
- `validate_diff` checks: closed enums, the D9 field combinations, strict order, totals, summary sums, and the `F.id`/`id` recompute.

**D11 Bounds** (out of range → usage exit 2):

| Limit | Default | Low | High | Unit |
| --- | --- | --- | --- | --- |
| `max_document_bytes` | 8388608 | 1 | 33554432 | artifact bytes |
| `max_operations` | 5000 | 1 | 50000 | path keys and operations, each |
| `max_parameters_per_operation` | 128 | 1 | 1024 | path-level + operation-level entries |
| `max_ref_depth` | 8 | 1 | 64 | followed chain hops |
| `max_ref_expansions` | 100000 | 0 | 1000000 | pointer resolutions per import |
| `max_diagnostics` | 1000 | 0 | 100000 | emitted diagnostics |
| `max_findings` (diff) | 1000 | 0 | 100000 | emitted findings |

`MAX_NESTING_DEPTH = 64` is a profile constant, not an option.

Cost:
- O(bytes) for the scan and parse;
- O(nodes) for the walks, each region walked once thanks to the caches;
- at most `max_ref_expansions` resolutions, each O(pointer tokens);
- the diff is O(operations × regions + parameters).

**D12 Determinism (AC-006).**
- Pure functions of (bytes, profile, options).
- Sorted walks and explicit sort keys everywhere; sets never reach the output.
- No clock, randomness, environment, path or host data.
- Floats come out in Python's shortest repr, which is stable across 3.12 and 3.14. Day-1 records `1` vs `1.0` (they are different digests:
  a conservative review).

**D13 CLI.**
- `openapi-snapshot FILE [--max-document-bytes ... --max-diagnostics]`.
- `openapi-diff --before F --after F [same import limits] [--max-findings N]`.
- Paths use `click.Path(path_type=Path)` with no `exists`/`dir_okay`, which would echo the path.
- Read with `open("rb").read(max_document_bytes + 1)`. On `OSError`, print the fixed text below.
- `except` order: `OpenAPIRejected` and `ContractDiffRejected` before `(OSError, ValueError)`.
- Stdout is `canonical_bytes` after validate; exit 0 regardless of findings.
- Fixed texts (`Error: ` prefix from click; stdout empty):

| Situation | Exit | Text |
| --- | --- | --- |
| unreadable file | 1 | `OpenAPI document cannot be read[: before\|after].` |
| import rejection | 1 | `OpenAPI document rejected: <reason>[ (before\|after)]` |
| diff rejection | 1 | `OpenAPI diff rejected: <reason>` |
| other `OSError`/`ValueError` | 1 | `OpenAPI import failed: <TypeName>` |
| bad limit | 2 | `Invalid OpenAPI limit: <name>` |

- `str(exc)`, paths and document text are never printed.

### Limitations (static `LIMITATIONS`, emitted on every snapshot and diff, stated in the docs)
1. `json_documents_only_yaml_not_supported`
2. `same_document_json_pointer_references_only`
3. `schemas_are_compared_by_digest_not_by_implication`
4. `no_global_verdict_is_produced`
5. `security_and_authentication_changes_are_review_only`
6. `contract_snapshot_is_not_bound_to_a_source_snapshot`
7. `operations_are_not_mapped_to_handlers`
8. `ignored_fields_are_excluded_from_digests`
9. `reference_chains_are_followed_at_path_item_parameter_request_body_and_response_positions_only`
10. `document_region_changes_are_not_attributed_to_operations`
11. `reserved_header_parameters_are_review_only`
12. `ref_like_values_inside_examples_are_classified_conservatively`
13. `not_exposed_over_mcp`

### Work plan (timebox 3 engineering days)

**Day 1 AM - qualification, before any product code.** A scratch script writes `.spectra/evidence/aci-s29/qualification.json`.
- (a) **Strict parser.** Run the copied helpers in the 3.12 dev image, and under `uv run --python 3.14` if available offline:
  - nesting depth 64 imports and depth 65 gives `nesting_too_deep`, on `petstore-before.json` wrapped in `x-deep` and on generated arrays,
    objects and mixed nesting;
  - depth 100000 gives `nesting_too_deep` with no RecursionError;
  - brackets inside strings and escaped quotes are not counted;
  - record whether a bare `json.loads` raises at 100000 (rationale for the scan);
  - `ev.canonical` succeeds at depth 64;
  - `1e999`, `NaN`, a duplicate key, BOM, `"\ud800"` and non-UTF-8 are each rejected.
- (b) **Pointer round-trip.**
  - The local `pointer`/`resolve_pointer` agree with `reports.evidence` on keys with `~`, `/`, `%`, non-ASCII text, the empty key and indices.
  - The local copy rejects `-1`, `01`, `~2` and an index past the end.
  - The percent-decoder handles `%7B`, `%25` and `%7e`; `%G1` and `%C3` are invalid.
- (c) **Profile table:** `3.1.0`/`3.1.1` accept. `3.0.3`, `3.2.0`, `3.1`, `3.1.0-rc1`, numeric `3.1`, `swagger: 2.0` and missing `openapi` reject.
- (d) **Real-world-shaped import.** `petstore-before.json` (info, servers, tags, path-level parameters, component parameters, requestBodies,
  responses and schemas, a pathItems `$ref`, security, a webhook) imports with:
  - status `complete`;
  - the expected operation count;
  - every pointer resolving through the `reports.evidence` oracle.
- (e) **Base invariants** go to `base-invariants.json`: tools sha and bytes, 3436/3448/797/7730, `openapi` absent from the tools JSON, and the
  full pytest result at base.

STOP RULE:
- (a) A valid document at depth ≤ 64 fails parse or canonicalization on either interpreter → lower `MAX_NESTING_DEPTH` to the observed safe
  value (≥ 32) and record it. Below 32 → return to the orchestrator.
- (b) The local pointer disagrees with the oracle on emitted forms → fix the local copy before Day 1 PM. Never edit `reports/`.
- (c) Any row differs from D1 → fix the regex, then retest.
- (d) The real-world document is not `complete`, or any pointer fails → stop and fix the importer before Day 2. If the fix needs a profile change
  outside decisions 3-6 → return to the orchestrator.
- 3.14 is unavailable offline → record it; CI is the 3.14 evidence.

**Day 1 PM:** D1-D5 (parse, pointers, profile, walk, resolver) and the AC-005 and rejection tests.

**Day 2:** D6-D10 (parameters, regions, snapshot, differ) and AC-001 to AC-004 and AC-007.

**Day 3:** CLI, AC-006, isolation/AST/vocabulary/docs tests, docs, CHANGELOG and README, gates in the dev image, production image, RUN-01/02.

Cut order (each cut becomes a listed limitation; no AC is affected):
1. README row and doc depth;
2. the `operation_added` informational finding;
3. `validate_snapshot`/`validate_diff` reduced to closed enums plus id recompute;
4. chain following at requestBody/response positions, reduced to `probe`;
5. per-entry `components` regions, reduced to one region per component type;
6. the PYTHONHASHSEED subprocess replay.

Never cut: D1 order and the nesting scan, closed rejections, D4 identity and ambiguity, ordinal path keys, the D5 resolver with all six reasons,
incompleteness blocking definitive findings, the review_required fallback for every changed region, provenance on every element, ids and
canonical bytes, CLI fixed texts, the invariance gates, the no-verdict vocabulary.

### Independent checker conditions
Pending. The orchestrator appends them after the independent checker run; they are binding and override conflicting text above.

## Independent checker conditions

Checker `atlas-s29-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s29.review.json`); this is the validation required by the confidence verdict VALIDATE (83.25). Every finding below is a binding plan condition and overrides any conflicting text above.

Orchestrator resolutions of the open options (recorded 2026-11-04 under the user's autonomous-execution instruction): condition 9 takes the STRICTER option - probes walk resolved reference targets (cached, uncharged on a cache hit) and mark the use-site region incomplete when a referenced component contains an unresolved/remote reference, with a test; the docs still state that edits inside `components/*` surface only as `document_region_changed`; condition 10 lowers the `max_operations` high bound to 20000 (and adds the parse_int digit guard and float-collapse documentation).

1. **MAJOR - Interpretation honesty.**
   - Evidence: the issue's Approach step 4 and title versus `query/diff.py:33`.
   - Condition: the docs, CHANGELOG, README row, receipt and PR body MUST say all of the following.
     - S19 is not invoked; only its change-set shape is reused.
     - `snapshot_id` is a contract identity that binds (bytes, profile, options), not an importer version.
     - Provenance is artifact digest plus pointer, not source-snapshot provenance.
     - Binding to a source snapshot is a follow-up.
   - The receipt MUST list Approach 4 and AC-002 "snapshot identity" as interpreted. No text may say "through S19" without the qualifier. The docs test MUST assert the phrases.

2. **MAJOR - Regex semantics.**
   - Evidence: D1.8 `^3\.1\.\d+$` and D2 `0|[1-9][0-9]*`. Python `$` accepts a trailing `\n` and `\d` accepts Unicode digits. This is the same defect as S31 condition 3.
   - Condition: use `re.fullmatch` with `[0-9]` for the profile, the array index and the remote classifier. Add rejection cases `"3.1.0\n"` and `"3.1.\u0660"`.

3. **MAJOR - Header-name case leaks into digests.**
   - Evidence: D6/D9.5 and the AC-004 row. `x-tenant` (path level) versus `X-Tenant` (operation level) have the same key but different `name`, so `sha256_without_required` differs. The result is a spurious `parameter_changed` next to `optional_to_required`.
   - Condition: lowercase the header `name` inside the parameter digests. The AC-004 test MUST assert the exact finding set for that operation (one `newly_required_parameter`, no `parameter_changed`).

4. **MAJOR - Mislabelled reason.**
   - Evidence: D9.5 sends an added required reserved header (Authorization) to `parameter_added_optional`.
   - Condition: rename the reason to `parameter_added`, with `reserved_header` visible on the finding or its parameter. Update `REVIEW_REASONS`, the docs and the AC-004 "required Authorization" assertion.

5. **MAJOR - Closure holes (false removed or silently dropped changes).**
   - (a) A path item with no supported operations has no operation record, so changes to its `parameters`, `servers`, unknown keys or `$ref` target are invisible.
     - Condition: the path-item record carries a digest of its non-operation content. Emit `review_required/region_changed` with `region:"path_item"` when a template has zero operations on both sides and the digest or presence differs.
   - (b) `get: null` or a non-object operation gives an `invalid_operation` diagnostic, but D3/D4 do not say whether the record survives.
     - Condition: keep it as an incomplete operation record under its identity. That blocks `operation_removed`, and a test covers it.

6. **MAJOR - Template-dependent caching.**
   - Evidence: D5 caches shared path items per declared pointer, but D6 ordinal keys and `path_parameter_not_in_template` depend on the use-site template.
   - Condition: cache only template-independent results (resolved object and digests). Compute keys, ordinals and diagnostics per `/paths` key. Add a test where one `components/pathItems` entry is referenced by `/a/{x}` and `/b/{y}/c/{z}`.

7. **MINOR - Paths `x-` extensions.**
   - Evidence: D4 treats any key not starting with `/` as `malformed_template`. OpenAPI 3.1 allows `x-` keys in `paths`, so this gives a false `incomplete`.
   - Condition: skip `x-*` keys in `paths`, and in `components` type keys. List `paths:x-*` in `IGNORED_FIELDS`. Any other non-`/` key stays malformed.

8. **MINOR - Reference Object siblings.**
   - Evidence: a parameter `{"$ref": ..., "required": true}` is spec-ignored here, so the edit is invisible.
   - Condition: fold siblings other than `summary`, `description` and `x-*` into the use-site parameter digest. Define `reference_with_siblings` for path items with the same sibling set, so a bare `summary`/`description` sibling does not mark the item incomplete.

9. **MINOR - Incompleteness propagation.**
   - Evidence: probes walk only the operation subtree. A component parameter, requestBody or response whose schema carries a remote `$ref` leaves the dependent operation region complete.
   - Condition: either walk resolved targets (cached, uncharged on a hit) and mark the use-site region incomplete, or add a limitation plus a test. Docs MUST also state that edits inside `components/requestBodies/X` surface only as `document_region_changed`.

10. **MINOR - Number handling and output size.**
    - Evidence: D1.4 relies on the interpreter's `int` digit limit, which `PYTHONINTMAXSTRDIGITS` can lift. `parse_float` collapses distinct decimal literals, and `1` differs from `1.0`. `max_operations` goes up to 50000 with no cap on snapshot size.
    - Condition: add a `parse_int` hook rejecting tokens over 4300 digits. Document float collapse. Lower the `max_operations` high bound to 20000 or document the output size.

11. **MINOR - `$id`/`$anchor` rebasing.**
    - Evidence: a `#/..` ref inside a schema with `$id` rebases in JSON Schema 2020-12, yet the plan classifies it as local.
    - Condition: add limitation 14, `schema_id_and_anchor_base_uris_are_not_applied`, with its docs entry and docs test.

12. **MINOR - AST and hygiene.**
    - Condition: the banned `compile` applies to a bare name only, since `re.compile` would otherwise trip.
    - Condition: source stays ASCII, because the thesis and export scans use `read_text()`, plus the `alter table` and `nx.compose(` strings.
    - Condition: shorten any limitation token that reaches column 100, to avoid E501.

13. **MINOR - Test construction.**
    - Condition: AC-003 and AC-004 negative variants are built by in-test mutation of fixtures, and no new files are needed.
    - Condition: the AC-004/007 tests assert the exact finding set per operation, not just presence.
    - Condition: `validate_diff` requires at least one non-null `before`/`after` ref on every finding.
    - Condition: AC-002 states that region `pointers` inherit the enclosing operation's provenance.

Checked, no condition needed:
- The nesting scan: the byte-scan logic, and UTF-8 continuation bytes never being `0x22`, `0x5C`, `0x5B` or `0x7B`.
- Duplicate keys at nested levels, and escaped duplicates.
- The surrogate handling.
- The pointer escape and unescape order.
- Percent-decode before split.
- The cycle check preceding the depth check, giving the AC-005 results.
- The path-templating equivalence `{}` and the lowercase-only method set.
- The CLI discipline and exit codes.
- The `cli.commands` and test_s32 regression set.
- The `harden-gate.yml` greps: no `alter table`, anthropic or openai imports are planned.

## Stories
As an API reviewer, I want removed operations and newly required request parameters between two exact OpenAPI 3.1 snapshots, each with exact
before/after JSON pointers and artifact digests, so I can act on concrete contract changes.

As an agent operator, I want every unsupported, unresolved or ambiguous change to surface as `review_required` with a closed reason, and no
global verdict, so that a short finding list is never read as proof that the API is unaffected.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s29.criteria.md` (sha256 `7c017111...0f6563`), verbatim:

```
### AC-S29-001 (event-driven)
GIVEN a supported OpenAPI document contains operations with absent or repeated operationId metadata
WHEN the contract is imported
THEN the importer SHALL identify each supported operation by its normalized HTTP method and path template.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_operation_identity

### AC-S29-002 (ubiquitous)
THEN every imported contract element SHALL retain its artifact digest, snapshot identity and JSON pointer.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_element_provenance

### AC-S29-003 (event-driven)
GIVEN an operation exists in the before contract but not in the after contract
WHEN the operator compares the two contract snapshots
THEN the differ SHALL emit an operation_removed finding for that method/path identity.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_removed_operation_finding

### AC-S29-004 (event-driven)
GIVEN the after contract adds a required request parameter absent or optional in the before contract
WHEN the supported parameter diff runs
THEN the differ SHALL emit a newly_required_parameter review finding for that parameter.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_new_required_parameter_finding

### AC-S29-005 (unwanted-behavior)
GIVEN a reference is remote, cyclic beyond the configured bound or outside the document
WHEN reference resolution encounters it
THEN the importer SHALL mark the affected contract region incomplete with an unresolved_reference diagnostic.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_unresolved_reference_is_visible

### AC-S29-006 (event-driven)
GIVEN identical before/after artifact bytes, importer profile and diff options
WHEN both artifacts are imported again and compared again
THEN the canonical contract diff SHALL remain byte-identical after repeat import and comparison.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_reimport_diff_is_idempotent

### AC-S29-007 (unwanted-behavior)
GIVEN a changed contract field falls outside the supported removal/required-parameter checks
WHEN the operator requests its compatibility assessment
THEN the differ SHALL classify the changed region as review_required.
VERIFY: test: mcp-server/tests/slices/test_s29_openapi_diff.py::test_openapi_unsupported_change_requires_review

### AC-S29-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S29-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

**Fixtures** (`tests/fixtures/openapi/`, hand-authored, 2-space JSON):
- `petstore-before.json`, `petstore-after.json`, and `petstore-after-reformatted.json`, which has the same JSON value as `petstore-after.json`
  with different whitespace and key order;
- `identity-before.json`, `identity-after.json`, `refs.json`, `review-before.json`, `review-after.json`;
- `rejected-swagger20.json`, `rejected-openapi303.json`, `rejected-openapi320.json`;
- `provenance.json`: per file `{sha256, bytes, purpose}`, plus `authored: "hand-authored for ACI-S29; no external source"`.

Other rejection, nesting, NaN and duplicate-key inputs are generated in-test. Library and CLI bytes are compared wherever a CLI path exists.
Every `all(...)` is preceded by a non-empty assertion. Nothing about the behaviour under test is mocked.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_openapi_operation_identity | **identity-before:**<br>- two operations without `operationId`;<br>- `GET /a` and `POST /b` both with `operationId "dup"`;<br>- `/users/{id}` get/delete;<br>- `/files/{name}~v` (exercises `~0`);<br>- `/users/` (trailing slash);<br>- an uppercase `GET` key;<br>- a path item via `$ref`;<br>- `/x/{a}` and `/x/{b}` both `get`.<br>**Assertions:**<br>- the identity set equals the expected set exactly: methods uppercase, `/users/{}` and `/users/` distinct, the `GET` key not an operation, the path-item-`$ref` operation keyed by its `/paths` key;<br>- `operation_id` is null or `"dup"`, as metadata only;<br>- `/x/{}` GET gives two records, both incomplete with `ambiguous_identity`.<br>**identity-after:** renames `{id}` to `{userId}`, changes and removes operationIds. The diff has no `operation_removed`/`operation_added` for those identities. It has `review_required/region_changed` with `region "operationId"`, `review_required/path_parameter_changed` for the renamed path parameter, and only `ambiguous_identity` findings for `/x/{}`. |
| 002 | test_openapi_element_provenance | For petstore-before, refs and identity-before (library and CLI snapshot), for every operation, parameter, path item, document region, diagnostic and region pointer:<br>- `artifact_sha256 == sha256(file bytes)`;<br>- `snapshot_id` equals the recompute from (artifact, profile, options);<br>- the pointer resolves through the `reports.evidence.resolve_pointer` oracle.<br>**Resolved values match:**<br>- operation → a dict with the record's `operationId`;<br>- parameter `declared_pointer` → a dict with the same `name`/`in`;<br>- `ref_chain[-1] == declared_pointer`;<br>- diagnostic pointer → exactly the `ref` string;<br>- document region → `digest(value) == sha256`.<br>**Pointer escaping:** at least one pointer contains `~0` and `~1`.<br>**Diff refs:** every non-null before/after `R` in the petstore diff resolves in its own artifact.<br>**Identity sensitivity:**<br>- copying the file to another path gives the same `snapshot_id`;<br>- `--max-ref-depth 7` changes `snapshot_id`;<br>- a one-byte whitespace edit changes both ids. |
| 003 | test_openapi_removed_operation_finding | **petstore pair:** exactly one `operation_removed` for `{DELETE, /pets/{}}`, with `action "review"`, a `before.pointer` that resolves to the before DELETE operation, an `after.pointer` equal to the after path item, and `after.artifact_sha256` equal to the after file's sha256.<br>**Removed whole path item:** `after.pointer == "/paths"`.<br>**Negative controls:**<br>- the renamed path variable gives no removal;<br>- an after variant whose path item is an unresolved `$ref` gives `review_required/incomplete_region` with `blocked_finding "operation_removed"` and no `operation_removed`;<br>- before == after gives `findings == []`. |
| 004 | test_openapi_new_required_parameter_finding | **petstore `GET /pets`, findings:**<br>- query `limit`, absent before and required after → `newly_required_parameter` with `change "added_required"` and `before.pointer` = the before operation;<br>- path-level header `x-tenant` optional before, overridden by operation-level `X-Tenant` with `required: true` after → `optional_to_required`, key `{header, x-tenant}`;<br>- a component parameter via `$ref` turned required → `after.declared_pointer == "/components/parameters/Page"`.<br>**Negative cases, no `newly_required_parameter`:**<br>- `required` absent ↔ `false`: no finding at all;<br>- required → optional: `parameter_changed`;<br>- an added optional cookie parameter: `parameter_added_optional`;<br>- an added path parameter: `path_parameter_changed`;<br>- a required `Authorization` header: review only.<br>**Optional → required plus a schema change:** both `newly_required_parameter` and `parameter_changed`. |
| 005 | test_openapi_unresolved_reference_is_visible | **refs.json.** Each parameter below gives `unresolved_reference` with the reason shown, its region incomplete, the operation and snapshot `incomplete`, `ref` equal to the document string, the provenance pointer resolving to that string, and `region_pointer` equal to the operation's parameters list:<br>- `https://x.example/p.json#/a` → `remote`;<br>- `//h/p` → `remote`;<br>- `other.json#/components/parameters/X` → `outside_document`;<br>- `#Anchor` and `#` → `outside_document`;<br>- `#/components/parameters/Missing` and `#/components/parameters/a%G1` → `target_missing`;<br>- `A→B→A` → `cyclic_reference` (pointer at B's `$ref`);<br>- self `C→C` → `cyclic_reference`;<br>- an acyclic 9-hop chain → `reference_depth_exceeded`;<br>- a 3-cycle with `--max-ref-depth 2` → `reference_depth_exceeded`;<br>- `--max-ref-expansions 0` → `reference_expansion_limit` on every reference.<br>**Other positions:**<br>- a remote `$ref` inside a requestBody schema → region `requestBody` incomplete (probe);<br>- a remote path-item `$ref` → path item incomplete with no operations;<br>- a remote `$ref` in `components/schemas/S` → that document region incomplete.<br>**Positive controls:** `#/components/parameters/Ok`, percent-encoded `#/components/parameters/a%20b` and a `~1` key each resolve `complete`.<br>**Diff with refs.json as after:** `incomplete_region` for those regions and no definitive finding for those operations. |
| 006 | test_openapi_reimport_diff_is_idempotent | **petstore pair and refs/identity pairs:** all of these are byte-identical:<br>- two fresh import+diff runs in process;<br>- the CLI twice;<br>- copies of both files at other paths;<br>- `python -m atlas_aci openapi-diff` with `PYTHONHASHSEED=1` and `=2`.<br>`id` and every `F.id` recompute.<br>**Decided:** `petstore-after-reformatted.json` has a different `artifact_sha256`, `snapshot_id`, diff `id` and diff bytes. The projection of every finding to (kind, reason, change, identity, region, parameter, before.pointer, after.pointer) is identical. This shows the identity binds to bytes and the findings bind to content.<br>**Options:** `--max-findings 1` changes `id` (options are part of it), and its findings are a prefix with equal `findings_total`. |
| 007 | test_openapi_unsupported_change_requires_review | **review pair, one change per operation**, each exactly `review_required` with the reason/region shown and both refs resolving:<br>- a response schema type changes → `region_changed/responses`;<br>- requestBody → `requestBody`;<br>- security → `security`;<br>- `deprecated: true` → `deprecated`;<br>- servers → `servers`;<br>- a new unknown key → `other`;<br>- callbacks → `callbacks`;<br>- a query parameter's schema → `parameter_changed`;<br>- an optional parameter is removed → `parameter_removed`;<br>- `components/schemas/User` → `document_region_changed`;<br>- `webhooks/newPet` → `document_region_changed`.<br>**Ignored fields:**<br>- operation `summary`/`description`/`tags` and `info.version` changes give no finding;<br>- `options.ignored_fields == IGNORED_FIELDS`;<br>- a `description` inside a schema property is not ignored (`region_changed`).<br>**No verdict:** no finding or diff key outside D10; `kind ∈ FINDING_KINDS`. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt is `.spectra/state/aci-s29.execution.json`. |

Supporting tests (same file):
- **Rejections table.** Library `.reason`, plus CLI exit 1 with exact stderr, empty stdout, and no tmp path or document text in stderr. One case
  per D1 code:
  - BOM, duplicate key, NaN, `-Infinity`, `1e999`, `"\ud800"`, non-UTF-8, trailing data and YAML text → `invalid_json`;
  - array root → `not_an_object`;
  - top-level `$ref` → `root_reference`;
  - the three rejected fixtures, plus `3.1`, `3.1.0-rc1` and numeric `3.1` → `unsupported_profile`;
  - `paths: []` → `invalid_document`;
  - `--max-document-bytes 10` → `too_large`;
  - `--max-operations 1` → `limit_exceeded`;
  - `--max-parameters-per-operation 1` → `limit_exceeded`.
- **Nesting boundary.**
  - Generated depth 64 imports; 65 gives `nesting_too_deep`.
  - 100000 gives `nesting_too_deep` with no RecursionError.
  - `"[[[["` and `"\"{"` inside strings are not counted.
  - The petstore fixture wrapped to exactly 64 imports and to 65 rejects.
- **Pointer strictness table:** `-1`, `01`, `~2` and an out-of-range index → MISSING; percent-decoding cases.
- **CLI.**
  - Both commands' stdout equals the library `canonical_bytes`.
  - Exit 0 with findings present.
  - Exit 2 for each out-of-range limit, with the fixed text.
  - Exit 1 for an unreadable file and for a directory, with the fixed text and side tag.
- **`validate_snapshot`/`validate_diff`:** pass on every output; reject a relabelled kind, an unsorted list, a tampered `id`, and a
  `newly_required_parameter` with `in: path`.
- **Isolation (G-SURFACE).**
  - Tools JSON sha and bytes equal the pins; `READ_ONLY_TOOLS` has 7 entries.
  - 3436/3448/797/7730 unchanged.
  - `openapi-snapshot` and `openapi-diff` are in `cli.commands`.
  - Neither `openapi` nor `atlas-contract` appears in the tools JSON.
  - No `import-openapi` command exists.
- **AST hygiene** (`importers/*.py`, `contracts/*.py`; the module list is exactly the 4 files):
  - Banned imports: `time`, `datetime`, `random`, `uuid`, `os`, `sys`, `io`, `pathlib`, `tempfile`, `shutil`, `subprocess`, `multiprocessing`,
    `threading`, `importlib`, `socket`, `urllib`, `http`, `sqlite3`, `jsonschema`, and `atlas_aci.{server,tools,codegraph,query,reports,
    architecture,counterfactual}`.
  - Allowed: `__future__`, `hashlib`, `json`, `re`, `math`, `dataclasses`, `typing`, `collections.abc`, `atlas_aci.storage.evidence`
    (canonical only), and `atlas_aci.importers.openapi31` (from contracts).
  - Banned calls: `eval`, `exec`, `compile`, `__import__`, `open`.
  - No function calls itself by name (no-recursion check).
  - ASCII only.
  - No "alter table" or `nx.compose(` string.
  - A planted positive control fails.
- **Vocabulary.** Keys and closed-enum values of every snapshot and diff, and all constants, are tokenized on non-alphanumerics. None may be in
  {safe, verified, proven, guaranteed, compliant, compatible, incompatible, breaking}. A planted `{"compatible": 1}` hit proves the scan works.
- **Docs.** `docs/importers/openapi.md` names:
  - every rejection, diagnostic kind and reason, finding kind, review reason, region, ignored field, limit, limitation and format string;
  - both commands and `MAX_NESTING_DEPTH`;
  - the phrases "contract snapshot" and "not a source snapshot";
  - the S19 reuse interpretation.
- **Fixture provenance:** every fixture file is listed, and its sha256 and bytes match.

Gates beyond the ACs:
- **G-SRC.**
  - `git diff --exit-code 981a897 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage,
    extractors,providers,indexing,resolvers,reports,architecture,counterfactual} mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows`.
  - `git diff --name-only 981a897 -- mcp-server/src` ⊆ {`importers/{__init__,openapi31}.py`, `contracts/{__init__,openapi_diff}.py`, `__main__.py`}.
- **G-MCP:** tools JSON `94f9567a...` / 1631472.
- **Regression set unchanged:** `test_s19_*`, `test_s31_*`, `test_s32_*` (`__main__.py` shares the file), `test_s37_*`, `test_thesis_negatives`,
  `test_export`.
- **Production image** (orchestrator):
  - build `mcp-server/Dockerfile` from the branch;
  - `docker run --network none` with `tests/fixtures/openapi` mounted `:ro`;
  - `openapi-diff` on the petstore pair, twice → identical stdout, with sha256 equal to the host run (3.12 image vs host);
  - `openapi-snapshot refs.json` → exit 0 with `status incomplete`;
  - `rejected-swagger20.json` → exit 1 with the fixed text and empty stdout;
  - the mount inventory is unchanged;
  - `atlas-aci tools` in the image matches the pin.

```sh
cd mcp-server
uv run pytest tests/slices/test_s29_openapi_diff.py -q
uv run pytest tests/slices/test_s19_*.py tests/slices/test_s31_*.py tests/slices/test_s32_*.py tests/slices/test_s37_*.py tests/test_thesis_negatives.py tests/test_export.py -q
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/
# if an offline 3.14 interpreter is available: uv run --python 3.14 pytest tests/slices/test_s29_openapi_diff.py -q
```

## Confidence
To be computed with `ramza-score`. This is a pure function of bytes with no graph, producer, protocol or query change, so generation ids,
envelopes and the tools JSON cannot churn. The parse discipline is copied from S31, which merged and passed on 3.12 and 3.14.

Main uncertainties, covered by Day 1 with a stop rule:
- the nesting bound across interpreters;
- strict pointer semantics;
- profile detection;
- whether a realistic document imports `complete`.

The semantic risk is review_required noise, which errs in the safe direction.

## Rejected Alternatives
- **Option A, graph-backed contract entities through `query/diff.py`.** It edits the fingerprinted producers, churning every pinned generation
  and fixture, and needs scope far beyond the declared paths and timebox.
- **Raw JSON tree diff (JSON-Patch style) then classification.** It has no stable operation identity, loses `$ref` provenance, and is
  order-sensitive.
- **jsonschema validation of the OpenAPI meta-schema.** It is recursive (the 3.12/3.14 hazard), its error texts echo the document, and it
  validates more than the profile needs.
- **Keying by operationId.** It is optional and renameable (Approach 1).
- **Keying path parameters by name.** A rename would yield a spurious `newly_required_parameter`.
- **Stripping `description`/`x-*` everywhere.** It would hide changes to schema properties with those names.
- **Truncating at `max_operations`.** It would fake removals; the document is rejected instead.
- **A global compatible/incompatible verdict, or MCP exposure.** Both are excluded by the issue and the pins.

## Risks
1. **A semantically relevant field is excluded from a digest, hiding a change.** Stripping is confined to the enumerated object positions
   (D7), and AC-007 has a row per region plus a schema-`description` control.
2. **"Through S19" read literally.** Decision 1 and the docs record the interpretation. Graph binding is a follow-up.
3. **Interpreter recursion differences (3.12 dev/image vs 3.14 CI).** Addressed by the byte scan, iterative walks, the no-self-call AST check
   and the boundary tests.
4. **Noise.** Component edits give `document_region_changed` alongside operation findings; `1` vs `1.0` gives a review. These are documented
   limitations, and the direction is conservative.
5. **Parameter edge semantics:** override, header case, reserved headers, ordinal keys. Each has a D6 rule and an AC-004 row.
6. **Error-text leakage.** Fixed texts, `from None`, and the no-tmp-path test.
7. **Timebox of 3 days.** Follow the cut order. At the limit, report completed checks and a narrow follow-up; no silent growth.
