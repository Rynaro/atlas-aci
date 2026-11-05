---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T00:00:00Z
slice_id: ACI-S30
issue: https://github.com/Rynaro/atlas-aci/issues/51
---
# ACI-S30 - link one Python consumer and provider by an exact package revision vector

## Scope

This is issue #51 on base `dc57096` (main after PR #97 / ACI-S28), branch `aci-s30`.
- The marker `<!-- atlas-aci:slice:ACI-S30 -->` is verified.
- The criteria bytes hash to `421bbc3c1ae66686eb7fc81a452cb684881a3bd2f17dcdde22ffd83693777df8` (7 product ACs plus RUN-01/02).
- Rightsize computed tier `lite` (score 4). An independent checker still runs.

Dependencies are ancestors of HEAD, and both have COMPLETE receipts:
- **S20 (#41).** Only its vocabulary is mirrored: `status potential_impact`, `runtime_observation not_performed` (`M/query/impact.py:543-545`; `M/` = `mcp-server/src/atlas_aci/`). `compute` is not reused: it is a same-repo before/after engine.
- **S28 (#49).** The API used:
  - `ul.select_package(lock_raw, package, environment, limits) -> Selection` (`M/dependencies/uv_lock.py:538`); `Selection.coordinate()` (`:427`) and `.lock_sha256`; `DependencyRejected` (`:99`);
  - `pw.validate_snapshot` (`M/dependencies/python_wheel.py:958`) and `pw.canonical_bytes` (`:115`);
  - snapshot keys `package.{coordinate, artifact.sha256}`, `lock.sha256`, `environment`, `members[{path, role, sha256}]`, `modules[{module, incomplete}]`, `declarations[{module, kind, name, qualified_name, symbol, parent_id, member}]`, `imports[{module, statement, name, alias}]`.

Observed anchors (re-verified at planning):
- **Leases.**
  - `runtime.open_active(stack, side)` (`M/overlays/runtime.py:227-242`) raises `OverlayRejected` with `index_unavailable`, `source_snapshot_unavailable` or `index_upgrade_required`. `DiffUsageError` passes through.
  - `CodeGraph(read_only=True)` does not mkdir (`M/codegraph.py:923`), and its db opens with `mode=ro` (`:974-977`).
  - `snap.acquire` only reads (`M/storage/snapshots.py:717-763`). There is no `journal_mode` anywhere, so the graph uses a rollback journal.
- **Graph shape.** Single-generation `graph.9.db`. The namespace is `sha256(str(repo))` under the graph root (`codegraph.py:912`).
- **`import_bindings`.**
  - DDL `codegraph.py:518-523`: `path, local_name, scope, byte_start, description, lang, source_entity_id, occurrence_id, evidence_id, assertion_id`.
  - Populated at `:4033-4046`. `description` = `json.dumps(sort_keys)` of `{level, module[], name, local_name, reason}` (`providers/python_imports.py:69-79`). The shape check is at `codegraph.py:6391-6413`.
  - Every binding gets an `import` assertion (UNRESOLVED with a reason for absolute imports; `:4916-4933`), so `assertion_id` is non-null.
- **Import forms** (`python_imports.py:39-68`):
  - `from a.b import X` gives `{level 0, module [a,b], name X, local X, reason unsupported_absolute_import}`.
  - `... import X as Y` gives `reason unsupported_import_alias` and `local Y`.
  - `from a import *` gives `name "*"`, `local "*"`, `reason unsupported_import_star`.
  - `import a.b` gives `{level 0, module [], name "a.b", local "a"}` (no `module_name` field; Day-1 confirms).
  - `scope` is `python:<path>:root` for a module-level import (`codegraph.py:4246-4249`).
  - The control reason is checked after the absolute and alias reasons, so conditionality is hidden.
- **Provider symbols.**
  - `symbols(name, kind, path, line_start, line_end, lang, binding)` plus link columns `entity_id, occurrence_id` (`M/storage/evidence.py:219-225`).
  - `binding` JSON has `scope`, plus `import_export_safe` = unconditional for python (`codegraph.py:4284-4287`).
  - Python kinds come from `@def.class` / `@def.function` (`:203-204`).
- **Source observations.** `manifest.source_observations` records `{type: observation, path, state, language, observed_source_sha256}` (`runtime.py:78,263-277`).
- **Output helpers.**
  - `runtime.resolve_output(output, protected, inputs)` (`:866-889`) raises `OverlayUsageError` with `output_not_outside_graph` or `output_equals_input`.
  - `write_atomic` uses prefix `.atlas-runtime-overlay-` (`:85`), so it is not reused.
- **CLI conventions.** `_read_dependency_input` (`M/__main__.py:1412-1428`, O_NONBLOCK + S_ISREG); fixed texts; lazy imports (`test_s37_evidence_report.py:1192` keeps `__main__` lazy).
- **Pins.**
  - Tools JSON 7 tools, `94f9567a…ac56` / 1631472 bytes; budgets 3436/3448/797/7730 (`test_s28_isolation.py:52-68`).
  - Banned vocabulary `safe, verified, proven, guaranteed, compliant, compatible, incompatible, breaking` (`test_s27_isolation.py:143`, `test_s29:1401`, `test_s28_isolation.py:46`); `applied` (`test_s32:1735`).
  - S28 banned regex `\d \w \s $` and banned methods `isdigit… isidentifier normalize casefold` (`test_s28_isolation.py:77-79`).
  - `alter table` (`test_thesis_negatives.py:51`); `nx.compose(` (`test_export.py:372`).
  - README row locators (`docs/importers/openapi.md`, `opentelemetry.md`, `python-dependencies.md`, `docs/frameworks/rails.md`) and `canary-missions.md` (`test_s28_isolation.py:331-344`).
  - CHANGELOG locators "Python dependency surface", "OpenTelemetry trace import", "OpenAPI 3.1 contract import".
  - `tests/fixtures/python_dependencies/**/*.py == [generate.py]` (`test_s28_isolation.py:260`).
- **Interpreters.** CI runs Python 3.14; the images run 3.12.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-05)
1. **Architecture.**
   - A standalone, content-addressed `atlas-cross-repo-overlay.v1`, written atomically to `--output` only, built from READ-ONLY leases of both active snapshots (the S27 pattern). No graph writes and no gated-path edits.
   - New package `cross_repo/{__init__,revision_vector,package_links}.py`, plus `__main__.py`.
   - Public imports only: `overlays.runtime.{open_active, OverlayRejected, resolve_output, OverlayUsageError}`, `query.diff.{Side, DiffUsageError}`, `dependencies.uv_lock.{select_package, normalize_limits, canonical_environment, normalize_name, DependencyRejected, DependencyUsageError}`, `dependencies.python_wheel.{validate_snapshot, canonical_bytes}`, `storage.evidence as ev`.
   - No underscore-private import. The only private access is the attribute `reader._source_lease.manifest`, as S19 and S27 do.
2. **Provider verification: the strict three-way bind (D4).** There is no `exclude_members`.
   - **Refinement:** the closed mismatch details are six. Besides the four given, `lock_digest_differs` and `environment_differs` are needed. A package snapshot imported from another lock must stay a per-reference `unresolved_version_mismatch`, not a rejection; otherwise the AC-002 "different locked version" case would become a rejection.
3. **Supported form.**
   - An unconditional (see limitation 3) module-level, level-0 `from <module> import <Name>`, aliases allowed.
   - The identity is `module.name`, compared by exact string equality.
   - `import pkg.mod` → `module_import`; wildcard → `wildcard_import`. Re-exports are not followed. The shadow guard is included.
4. **Revision vector.** Operator-declared ids (closed grammar), the same-repository rejection, the `vector_digest` inside every link id, and the full vector on every link.
5. **AC-003.** "Available" means the provider's active id equals the mapped id. Otherwise `missing_provider_snapshot` with a closed detail, and no provider fact is read.
6. **AC-004.** `--previous` refresh produces `supersedes`. The equal-input choice is in D9.
7. **AC-006.** Roots come only from role-keyed CLI options. A record naming an unlisted repository → `repository_not_allowlisted`, decided before any open.
8. **AC-007.** SHA-256, size, mode and mtime inventories of both repos and both graph roots, on success AND rejection paths.
9. **Impact.** Library plus CLI, both directions, depth 1, explicit budgets, mirrored S20 vocabulary.
10. **CLI.** Bounded reads, fixed texts, exit 0/1/2, output containment.
11. **Fixtures.** Generated in-test from S28's generator plus two tiny indexed repos; no golden overlays (snapshot ids depend on absolute paths).
12. **Invariants.** Tools JSON and budgets unchanged; `ev.RELATIONS` unchanged; no new dependency; NO prior-test edit (verified: no existing test enumerates `src/atlas_aci` packages or CLI commands exhaustively).

### In
1. `cross_repo/revision_vector.py`: `MAPPING_FORMAT`, `VECTOR_FORMAT`, `ROLES`, `PROVIDER_DETAILS`, `CrossRepoRejected`, `CrossRepoUsageError`, `parse_mapping`, `mapping_digest`, `revision_vector`, `vector_digest`, `open_side`, `provider_state`, `check_distinct`, `supersedes`.
2. `cross_repo/package_links.py`: `FORMAT`, `IMPACT_FORMAT`, `SUMMARY_FORMAT`, `PRODUCER`, `RESOLUTIONS`, `RESOLUTION_DETAILS`, `MISMATCH_DETAILS`, `FORM_DETAILS`, `REJECTIONS`, `BOUNDARY_KINDS`, `DIRECTIONS`, `LIMITS`, `BUDGETS`, `LIMITATIONS`, `COVERAGE`, `MAX_INVALIDATED_IDS`, `link(...)`, `validate_overlay`, `overlay_identity`, `impact(...)`, `validate_impact`, `summary_document`.
3. `__main__.py`: `cross-repo-link`, `cross-repo-impact`, and private `_read_cross_repo_input` / `_write_cross_repo_atomic`.
4. Tests `test_s30_cross_repo.py` (the 7 named), `test_s30_rejections.py`, `test_s30_isolation.py`, `test_s30_support.py`; fixtures `tests/fixtures/cross_repo/`.
5. `docs/cross-repository-links.md`, a CHANGELOG bullet and a README row.

### Out
- Discovery, cloning, fetching, credentials, network.
- Other ecosystems; more than one consumer, provider or package; transitive packages.
- Call completion, name-only matching, node merging, global graph composition; any graph, MCP or gated-path change.

## Approach

### Decisions

**D1 Link pipeline.** The first failure wins. On failure nothing is written and stdout is empty. The provider is never opened before stage 7.

| # | Stage | Touches | Failure |
| --- | --- | --- | --- |
| 1 | limits; output check: `resolve_output(out, protected=[both repos, both repo/.atlas, both graph roots], inputs=[mapping, lock, package snapshot, previous])` | args, path metadata | usage, exit 2 |
| 2 | mapping read (bounded) → strict JSON → closed shape → allowlist → distinct ids | mapping bytes | `mapping_too_large`, `invalid_mapping`, `unsupported_mapping_version`, `repository_not_allowlisted`, `same_repository` |
| 3 | pre-open distinctness: `consumer.repo.resolve() == provider.repo.resolve()` | paths | `same_repository` |
| 4 | lock read; size; `sha256 == consumer.lock_sha256`; `select_package(lock, name, env, ul limits)` | lock bytes | `lock_too_large`, `lock_digest_mismatch`, S28 `DependencyRejected.reason` passthrough |
| 5 | package snapshot read; size; sha256 == the mapping's; strict JSON; `validate_snapshot`; `canonical_bytes(s) == raw` | snapshot bytes | `package_snapshot_too_large`, `package_snapshot_digest_mismatch`, `invalid_package_snapshot` |
| 6 | `--previous` read; strict JSON; `validate_overlay`; canonical; same (role, repository_id) pairs | overlay bytes | `previous_overlay_too_large`, `invalid_previous_overlay`, `previous_overlay_unrelated` |
| 7 | ONE `ExitStack`: `open_side(consumer)`; active id == mapped id; then `open_side(provider)` → `provider_state` | leases | `consumer_index_unavailable`, `consumer_source_snapshot_unavailable`, `consumer_index_upgrade_required`, `consumer_snapshot_mismatch`; the provider never rejects (D5) |
| 8 | post-lease distinctness: equal manifest `context.identity` (`kind` plus `common_dir` or `root`) | manifests | `same_repository` |
| 9 | bind (D4), references (D6), resolution (D7), assembly + `validate_overlay` (D8), supersedes (D9) | ro SQL | `invalid_consumer_evidence`, `invalid_provider_evidence`, `reference_limit_exceeded` |
| 10 | atomic write of `--output` (CLI) | output only | exit 1 |

**D2 Mapping `atlas-cross-repo-mapping.v1`.** Bytes only; contains no paths and no roots.
```
{"format":"atlas-cross-repo-mapping.v1",
 "repositories":[{"repository_id":"app","role":"consumer","snapshot_id":"<hex64>"},
                 {"repository_id":"lib","role":"provider","snapshot_id":"<hex64>"}],
 "consumer":{"lock_sha256":"<hex64>","environment":{}},
 "packages":[{"provider_repository_id":"lib","coordinate":"pypi:s30-shapes@1.0.0",
              "package_snapshot_sha256":"<hex64>","source_prefix":"src/"}]}
```
- **Parse.**
  - size ≤ `max_mapping_bytes`, else `mapping_too_large`;
  - strict UTF-8, no BOM;
  - `json.loads` with `object_pairs_hook` rejecting duplicate keys, and `parse_int`, `parse_float`, `parse_constant` all raising. The mapping has no numbers, so `PYTHONINTMAXSTRDIGITS` is irrelevant.
  - `RecursionError` and `MemoryError` are caught. Every failure → `invalid_mapping`. `format` is a str ≠ the constant → `unsupported_mapping_version`.
- **Closed keys at every level.**
  - `repositories` has exactly 2 entries, roles exactly {consumer, provider}, distinct ids.
  - `repository_id` fullmatches `[a-z0-9][a-z0-9._-]{0,63}`; `snapshot_id` passes `ev.hash_value`.
  - `environment` is a str→str object equal to `ul.canonical_environment(env)` (`DependencyUsageError` → `invalid_mapping`).
  - `packages` has exactly 1 entry.
  - `coordinate` fullmatches `pypi:([a-z0-9][a-z0-9-]{0,198})@([0-9][0-9A-Za-z!+._-]{0,63})` with `ul.normalize_name(name) == name`.
  - `source_prefix` fullmatches `([A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}/){0,8}`. Segments never start with `.`, so `.` and `..` are excluded.
- **Checks in order.**
  - `packages[0].provider_repository_id` ≠ the provider entry's id → `repository_not_allowlisted` (AC-006).
  - The two `snapshot_id`s are equal → `same_repository`.
- **Normalized form** (consumer first, then provider; keys sorted by canonical JSON).
  - `mapping_digest = sha256("atlas-cross-repo-mapping.v1:" + ev.canonical(normalized))`.
  - Key order, whitespace and list order therefore do not change the digest (AC-005).

**D3 Revision vector.**
- `revision_vector = [{role:"consumer", repository_id, snapshot_id}, {role:"provider", repository_id, snapshot_id}]`, using the MAPPED ids. Exact order, exactly these keys.
- `vector_digest = sha256("atlas-cross-repo-vector.v1:" + ev.canonical(vector))`.
- Repository ids are operator labels. Each snapshot id binds its label to one concrete root, because the manifest identity includes the absolute root (`storage/snapshots.py:38-64`).

**D4 Bind** (`package.binding`; never a rejection; every check is computed):

| Check | Value | Detail on failure |
| --- | --- | --- |
| coordinate | mapping coordinate == locked `coordinate_text` AND snapshot `package.coordinate` == locked → `equal` | `coordinate_differs` |
| artifact | snapshot `package.artifact.sha256` ∈ the locked `expected_artifacts[].sha256` → `equals_lock_entry` | `artifact_digest_differs` |
| lock | snapshot `lock.sha256` == mapping `lock_sha256` → `equal` | `lock_digest_differs` |
| environment | snapshot `environment` == mapping environment → `equal` | `environment_differs` |
| sources | provider available: for every `role == python_source` member in path order, at `source_prefix + path`: an observation with `state == "indexed"` and `observed_source_sha256 == member.sha256` counts as equal; an observation with another sha counts as differing; otherwise missing. All equal → `equal`. Provider unavailable → `not_evaluated` | `source_member_missing`, then `source_digest_differs` |

- **State.**
  - First failure among coordinate, artifact, lock, environment → `version_mismatch` with that detail.
  - Otherwise, provider unavailable → `provider_snapshot_unavailable` with the provider detail.
  - Otherwise, sources failed → `version_mismatch` with `source_member_missing` (when any member is missing) or else `source_digest_differs`.
  - Otherwise `bound`.
- Counts: `members_compared, members_equal, members_missing, members_differing`.
- The lock-to-consumer binding is recorded as `attestation: "operator_declared"`: `uv.lock` is not a graph language, so it is not in the consumer snapshot.

**D5 Leases** (`open_side`, `provider_state`).
- Both leases live in one `ExitStack`, consumer first. `open_side` calls `runtime.open_active` and maps `OverlayRejected.reason` r:
  - for the consumer → rejection `consumer_<r>`;
  - for the provider → `provider = {repository_id, declared_snapshot_id, supplied_snapshot_id: null, matched: false, state: "unavailable", detail: r}`.
- `DiffUsageError` → `CrossRepoUsageError("graph_namespace")` (exit 2).
- A provider lease whose `snapshot_id` ≠ the mapped id gives `state: "unavailable"`, `detail: "snapshot_not_active"`, `supplied_snapshot_id` = the active id. No SQL is issued on that reader after the lease (proven in AC-003).
- `PROVIDER_DETAILS = snapshot_not_active, index_unavailable, source_snapshot_unavailable, index_upgrade_required`. The fourth extends the given three: an old-epoch graph is a distinct, observable cause.

**D6 Reference extraction** (consumer lease, read-only):
- Rows: `SELECT path, local_name, scope, byte_start, description, source_entity_id, occurrence_id, evidence_id, assertion_id FROM import_bindings WHERE lang='python'`.
- Each `occurrence_id` → `SELECT payload FROM occurrences WHERE id=?` → `{path, line, column, start_byte}`. Exact keys are fixed on Day-1 (a).
- Required, else `invalid_consumer_evidence`: closed description keys; `level` an int; `module` a list of str; `name` a non-empty str; occurrence path == row path; non-null `source_entity_id`/`assertion_id`/`occurrence_id`/`evidence_id`.
- Package top-level modules = `{m.split(".")[0] for m in snapshot.modules if m.module}`.
- **Candidate** iff `level == 0` and root ∈ top-level modules, where root = `module[0]` if `module` else `name.split(".")[0]`. Everything else, relative imports included, is counted only (`counts.references_out_of_scope`).
- More than `max_references` candidates → `reference_limit_exceeded`.
- Order: (path, occurrence start_byte, occurrence_id).
- `form`: `module == []` → `module_import`; `name == "*"` → `wildcard_import`; otherwise `from_import`. `aliased = local_name != name` (from_import only).

**D7 Resolution** (precedence; the first match wins; closed):
1. **Form** → `unresolved_unsupported_reference_form`, with detail:
   - `nested_scope_import` when `scope != "python:"+path+":root"`;
   - `wildcard_import`;
   - `module_import`;
   - `non_ascii_identifier` when any module part or the name fails ASCII `[A-Za-z_][A-Za-z0-9_]*`;
   - the `reason` must be one of `unsupported_absolute_import`, `unsupported_import_alias`, `unsupported_import_star`, else `invalid_consumer_evidence`.
2. **Shadow** → `unresolved_local_module_shadow`: a consumer observation with `state == "indexed"` has a path equal to, or ending with `/` +, `root + ".py"` or `root + "/__init__.py"`. Namespace directories are not detected (limitation 11).
3. **Lock-level version mismatch** → `unresolved_version_mismatch` with the binding detail.
4. **Provider unavailable** → `unresolved_provider_snapshot_unavailable` with the provider detail.
5. **Source mismatch** → `unresolved_version_mismatch` with the binding detail.
6. **Lookup**, with `q = ".".join(module) + "." + name`:
   - D = snapshot declarations with `symbol == q`, `parent_id is None`, `kind ∈ {class, function, async_function}`;
   - S = 1 if `q` is an S28 module, else 0.
   - |D| ≥ 2, or |D| ≥ 1 with S = 1 → `unresolved_ambiguous_provider_symbol` / `package_declarations`.
   - |D| = 0 and S = 1 → `unresolved_unsupported_reference_form` / `submodule_import`.
   - |D| = 0, S = 0:
     - module `".".join(module)` absent → `unresolved_symbol_not_declared` / `module_not_in_package`;
     - else the module has an `imports` record with `statement == "from"` binding `name` (alias == name, or alias null and name == name) → `unresolved_reexport_not_followed`;
     - else the module is `incomplete` → `unresolved_symbol_not_declared` / `module_incomplete`;
     - else → `unresolved_symbol_not_declared` / `name_not_declared`.
   - |D| = 1 (declaration d) → provider symbols at `source_prefix + d.member` with `name == d.name`, `lang == 'python'`, binding `scope == "python:<that path>:root"`, `import_export_safe is True`, and kind map `class→class`, `function|async_function→function` (Day-1 (c)):
     - exactly 1 → `linked_declaration`;
     - ≥2 → `unresolved_ambiguous_provider_symbol` / `provider_symbols`;
     - 0 → `unresolved_provider_declaration_not_indexed`.
- `RESOLUTIONS` (9) and their details (null where not listed): `linked_declaration`; `unresolved_version_mismatch` {coordinate_differs, artifact_digest_differs, lock_digest_differs, environment_differs, source_member_missing, source_digest_differs}; `unresolved_provider_snapshot_unavailable` {PROVIDER_DETAILS}; `unresolved_unsupported_reference_form` {module_import, wildcard_import, nested_scope_import, submodule_import, non_ascii_identifier}; `unresolved_local_module_shadow`; `unresolved_reexport_not_followed`; `unresolved_symbol_not_declared` {module_not_in_package, name_not_declared, module_incomplete}; `unresolved_ambiguous_provider_symbol` {package_declarations, provider_symbols}; `unresolved_provider_declaration_not_indexed`.

**D8 Overlay `atlas-cross-repo-overlay.v1`.** Closed keys; canonical JSON plus LF; no absolute path, clock, host or limit.
```
{format, overlay_id,
 producer:{name:"atlas-aci cross-repo-link", version:"atlas-cross-repo-linker.v1"},
 mapping:{format, digest}, revision_vector:[2], vector_digest,
 consumer:{repository_id, snapshot_id, lock:{sha256, attestation:"operator_declared"}},
 provider:{repository_id, declared_snapshot_id, supplied_snapshot_id|null, matched, state, detail|null},
 package:{coordinate (locked), mapping_coordinate, package_snapshot_id, package_snapshot_sha256,
          artifact_sha256, source_prefix, top_level_modules[sorted],
          binding:{state, detail|null, checks:{coordinate, artifact, lock, environment, sources},
                   members_compared, members_equal, members_missing, members_differing}},
 coverage:{evidence_coverage:"import_reference_and_declaration_only", completeness:"incomplete",
           call_binding:"not_claimed", execution:"not_performed", traversal:"mapped_repositories_only"},
 counts:{references, references_out_of_scope, links, by_resolution:{all 9 keys}},
 limitations:[LIMITATIONS], references:[...], links:[...], supersedes:null|{...}}
```
- **Reference record.** `{id, type:"external_reference", consumer:{repository_id, snapshot_id, path, line, column, byte_start, owner_entity_id, occurrence_id, evidence_id, assertion_id}, form, module, name, qualified_name, aliased, resolution, detail, link_id|null}`.
  - `id = sha256(canonical(["atlas-cross-repo-reference.v1", consumer repository_id, consumer snapshot_id, occurrence_id]))`.
- **Link record** (only `linked_declaration`). `{id, type:"cross_repository_link", relation:"external_reference", call_semantics:"not_claimed", reference_id, qualified_name, revision_vector:[full copy], vector_digest, consumer:{repository_id, snapshot_id, path, line, owner_entity_id, assertion_id}, provider:{repository_id, snapshot_id, entity_id, path, name, kind, line_start, line_end, declaration_id, member, member_sha256}}`.
  - `id = sha256(canonical(["atlas-cross-repo-link.v1", vector_digest, reference_id, provider entity_id, declaration_id]))`.
  - No value equals an `ev.RELATIONS` member.
- **Ids and order.**
  - `overlay_id = sha256("atlas-cross-repo-overlay.v1:" + ev.canonical(rest))`.
  - References are sorted by (path, byte_start, occurrence_id); links by id.
- **`validate_overlay`** → `invalid_overlay`. It checks:
  - closed keys and enums, constants, `ev.hash_value`;
  - recomputes `vector_digest`, every reference/link id and `overlay_id`;
  - every link's vector == the top vector;
  - links ↔ `linked_declaration` references are a bijection, and every detail is legal for its resolution;
  - counts, sort orders, the binding state/detail rule (D4), and the provider block consistency;
  - `supersedes` shape, its ids sorted, and none of them present in `links`.

**D9 Refresh / invalidation (AC-004).** With `--previous P`:
- Build fresh with `supersedes: null`, giving F.
- If `P.overlay_id == F.overlay_id`, the output is F, byte-identical to a fresh build (a fixpoint).
- Otherwise `supersedes = {overlay_id: P.overlay_id, vector_digest: P.vector_digest, links_invalidated: |P.links − F.links|, invalidated_link_ids: sorted(P ids − F ids)[:MAX_INVALIDATED_IDS=10000], invalidated_link_ids_truncated: bool}`, and the overlay id is recomputed.
- A vector change changes every link id, so every old link is invalidated.
- An unchanged mapping after a provider re-index gives `provider unavailable`, so there are no links and every old link is invalidated.
- **Why not always record `supersedes`:** the AC-005 replay identity and RUN-01 re-entry need refresh to be idempotent. An always-present record changes on every chained rerun (`P2.supersedes.overlay_id = P1`), so it never reaches a fixpoint. Provenance stays exact whenever anything changed.
- `--output` may not equal `--previous`; it is an input (`output_equals_input`).

**D10 Impact `atlas-cross-repo-impact.v1`** (`impact(overlay_raw, mapping_raw, consumer, provider, seed, direction, budgets)`). Read-only; nothing is written.
1. Budgets: not an int, a bool, or out of range → usage. `direction` ∈ {`from_provider`, `to_provider`}. `seed` must pass `ev.hash_value`, else usage.
2. Overlay bounded read, strict JSON, `validate_overlay`, canonical → `invalid_overlay`. Mapping parsed per D2; its `mapping_digest` ≠ the overlay's → `overlay_mapping_mismatch`.
3. Every vector entry's (role, repository_id, snapshot_id) must appear in the mapping, else `repository_not_allowlisted`. This happens BEFORE any open.
4. One `ExitStack`.
   - Consumer lease (D5 rejections); active ≠ the vector's consumer id → `stale_revision_vector`.
   - Provider lease → current state. Not matched → no provider SQL.
5. `to_provider`:
   - The seed must be a consumer entity (`SELECT 1 FROM entities WHERE id=?`), else `unknown_seed`.
   - Examine the references with `owner_entity_id == seed`, in id order:
     - overlay provider unavailable, or resolution `unresolved_provider_snapshot_unavailable`, or provider unavailable NOW → boundary `missing_provider_snapshot` (detail: the current one if unavailable now, else the overlay's) — AC-003;
     - `linked_declaration` → result;
     - otherwise → boundary `unresolved_external_reference` (`detail` = the resolution).
6. `from_provider`:
   - Provider unavailable now → a single boundary `missing_provider_snapshot` with `at: "seed"` and no results.
   - Otherwise the seed must be a provider entity, else `unknown_seed`. The links with `provider.entity_id == seed` give results.
- **Records.**
  - Result: `{link_id, reference_id, relation:"external_reference", call_semantics:"not_claimed", depth:1, consumer:{...as link}, provider:{...as link}}`.
  - Boundary: `{kind, detail|null, at:"seed"|"reference", reference_id|null, link_id|null, repository_id, snapshot_id}`.
  - `BOUNDARY_KINDS = missing_provider_snapshot, unresolved_external_reference, budget_exhausted`.
- **Budgets** (default/low/high): `max_results` 1000/1/100000, `max_links_examined` 100000/1/1000000, `max_boundaries` 1000/1/100000.
  - When a cap would be exceeded, stop and append `{kind:"budget_exhausted", detail:<budget name>}`, which does not count against `max_boundaries`; `complete: false`.
- **Top-level fields.** `{format, result_id, overlay_id, vector_digest, revision_vector, direction, seed, status:"potential_impact", basis:"static_reference", runtime_observation:"not_performed", call_semantics:"not_claimed", depth_limit:1, provider_state:{declared_snapshot_id, supplied_snapshot_id, matched}, budgets, work:{records_examined, results, boundaries}, complete, results, boundaries}`.
  - `result_id = sha256(FORMAT + ":" + canonical(rest))`.
  - Results are sorted by link_id; boundaries by (kind, reference_id or "", detail or "").
  - `validate_impact` recomputes everything.
- Consumer-side `navigate` expansion is excluded (limitation 9).

**D11 Closed rejections** (`CrossRepoRejected(reason)`; `str` = `"cross_repo_rejected: "+reason`):
- `mapping_too_large, invalid_mapping, unsupported_mapping_version, repository_not_allowlisted, same_repository, lock_too_large, lock_digest_mismatch, package_snapshot_too_large, package_snapshot_digest_mismatch, invalid_package_snapshot, previous_overlay_too_large, invalid_previous_overlay, previous_overlay_unrelated, consumer_index_unavailable, consumer_source_snapshot_unavailable, consumer_index_upgrade_required, consumer_snapshot_mismatch, invalid_consumer_evidence, invalid_provider_evidence, reference_limit_exceeded, overlay_too_large, invalid_overlay, overlay_mapping_mismatch, stale_revision_vector, unknown_seed`;
- plus every `ul.REJECTIONS` value, passed through unchanged from `select_package` (`DependencyRejected`).
- No constant, key or enum value contains a banned token, including `applied`.

**D12 Limits** (link; reject-only, never in the output): `max_mapping_bytes` 65536/1/1048576; `max_lock_bytes` (S28: 33554432/1/268435456); `max_package_snapshot_bytes` 67108864/1/268435456; `max_overlay_bytes` 67108864/1/268435456; `max_references` 100000/1/1000000.

**D13 CLI.**
- `atlas-aci cross-repo-link --mapping M --lock L --package-snapshot P --consumer-repo D [--consumer-graph-root G] --provider-repo D [--provider-graph-root G] [--previous O] --output OUT [limits]`.
  - Prints `atlas-cross-repo-link-summary.v1` = `{format, overlay_id, vector_digest, revision_vector, binding_state, provider_state, counts, links_invalidated|null}`.
- `atlas-aci cross-repo-impact --overlay O --mapping M --consumer-repo/--consumer-graph-root --provider-repo/--provider-graph-root --seed HEX --direction {from_provider,to_provider} [budgets] [--max-overlay-bytes] [--max-mapping-bytes]`.
  - Prints the canonical result and writes nothing.
- Repos are `click.Path(exists=True, file_okay=False)`; other paths are `click.Path(path_type=Path)`. Imports are lazy, and there is no option for any third root.
- Reads use `_read_cross_repo_input` (O_NONBLOCK, S_ISREG, limit+1). Writes use mkstemp `.atlas-cross-repo-overlay-` + fsync + `os.replace`, unlinking on failure. Never `str(exc)`.

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| bad limit/budget | 2 | `Invalid cross-repository limit: <name>` |
| bad seed | 2 | `Invalid --seed value.` |
| output inside a repo/.atlas/graph root | 2 | `--output must be outside both repositories and both graph roots.` |
| output equals an input | 2 | `--output must differ from every input file.` |
| namespace unusable | 2 | `A repository cannot be opened as a graph namespace.` |
| mapping/lock/package snapshot/previous/overlay unreadable or not regular | 1 | `Cross-repository mapping cannot be read.` / `uv lock file cannot be read.` / `Package snapshot cannot be read.` / `Previous overlay cannot be read.` / `Cross-repository overlay cannot be read.` |
| link rejection | 1 | `Cross-repository link rejected: <reason>` |
| impact rejection | 1 | `Cross-repository impact rejected: <reason>` |
| write failure | 1 | `Cross-repository overlay cannot be written.` |
| other | 1 | `Cross-repository link failed: <TypeName>` / `Cross-repository impact failed: <TypeName>` |

### Limitations (static `LIMITATIONS`, emitted in every overlay and named in the docs)
1. `import_reference_only_no_call_binding`
2. `module_level_from_imports_only`
3. `conditional_absolute_imports_not_distinguished` (the indexer's reason hides control flow)
4. `reexports_not_followed`
5. `lock_bound_to_consumer_by_operator_declaration`
6. `every_wheel_python_member_must_match_provider_source` (generated members such as `_version.py` fail with `source_member_missing`; there is no `exclude_members`)
7. `provider_snapshot_must_be_the_active_generation`
8. `one_consumer_one_provider_one_package`
9. `impact_depth_one_without_in_repository_expansion`
10. `repository_ids_are_operator_labels`
11. `local_shadow_detected_by_indexed_paths_only`
12. `not_written_to_the_graph_and_not_exposed_over_mcp`

### Work plan (timebox 5 engineering days)

**Day 1 AM - qualification before product code.** Probes write `.spectra/evidence/aci-s30/qualification.json`.
- (a) **`import_bindings` on a built fixture consumer.** Record the exact row and description JSON, `scope`, `source_entity_id` (== file entity id?), non-null `assertion_id`, and the occurrence payload keys for:
  - `from s30_shapes.geometry import area`;
  - `... import perimeter as per`;
  - `from x import *`;
  - `import s30_shapes.geometry`;
  - the same import inside a `def` and inside an `if`;
  - `from .local import y`.
- (b) **Provider observations.** `state == "indexed"` and `observed_source_sha256 == sha256(file bytes)` for a `src/` layout. Record which names `config.py` skip patterns drop.
- (c) **Provider symbols.** `kind` for class, def, async def and decorated def at root; binding `scope` and `import_export_safe`; `entity_id` non-null; a conditional def gives `import_export_safe: false`.
- (d) **Two read-only leases at once** in one ExitStack, provider first and consumer first. The S19 `inventory` is unchanged, with no `-journal` or other new file. Record the outcome for a nonexistent graph root (rejection vs usage) and confirm no directory is created.
- (e) **Determinism.**
  - The same repo indexed into two fresh graph roots → equal `snapshot_id`?
  - Files written in reverse order → equal?
  - A re-index with no change → equal?
- (f) **S28 snapshot** built in-test from a generated `s30-shapes` wheel; `validate_snapshot`; manual bind against (b).
- (g) **Timing:** two tiny `CodeGraph.build()` runs, and a full mock run of the D1 pipeline.
- (h) **Base invariants** go to `base-invariants.json`: tools sha and bytes, the four budgets, and a full `pytest -q` with test_s08 nonregular_pack deselected.

STOP RULE:
- (a) The description shape, `scope` or `assertion_id` differs from D6 → STOP and return. The supported form depends on it.
- (b) `observed_source_sha256` is not the file sha → apply the pre-authorised fallback: hash bytes read with `reader._source_lease.read(path, observed_sha)`. That is still read-only, so record it. If that read is impossible as well → STOP.
- (c) Kind or scope differences → adapt the D7 kind map (scope-neutral).
- (d) Any lease writes a byte → STOP (AC-007).
- (e) Non-deterministic ids across fresh roots → drop that AC-005 variant and record it.
- (g) A world takes more than 10 s → module-scoped worlds only, and record it.

**Day 1 PM:** `revision_vector.py` (D2, D3, D5, D9 helper); mapping and lease tests in `test_s30_rejections.py`.

**Day 2:** bind, references and resolution (D4, D6, D7); `test_s30_support.py` worlds; AC-001 and AC-002.

**Day 3:** assembly, `validate_overlay`, supersedes (D8, D9); impact (D10); AC-003/004/005/006.

**Day 4:** CLI (D13); AC-007; CLI rejections; `test_s30_isolation.py`; docs, CHANGELOG, README.

**Day 5:** gates, py314 container, `prod-image.sh`, RUN-01/02, buffer.

Cut order (each cut becomes a listed limitation; no AC is affected):
1. README row and doc depth.
2. The PYTHONHASHSEED subprocess replay (keep the in-process and CLI replays).
3. The `unresolved_reexport_not_followed` refinement (folds into `name_not_declared`).
4. The `submodule_import` detail (folds into `name_not_declared`).
5. The production-image refresh step (keep link, rejection, inventory and pin).

Never cut:
- the allowlist-before-open rule; the strict three-way bind; the provider-after-consumer order;
- the closed vocabularies; the vector on every link; supersedes;
- both impact directions; the shadow guard; AC-007 inventories on rejection paths;
- CLI texts and output containment; G-SRC and G-MCP.

### Independent checker conditions
Checker verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s30-plan-checker`, 3 MAJOR + 9 MINOR; `.spectra/plans/aci-s30.review.json`). Conditions 1-12 below are binding and override conflicting text above.

Orchestrator resolutions (stricter option wherever the checker offered a choice):
- C1: refresh compares `content_id` (overlay id with `supersedes` forced null); equal content emits the fresh build with `supersedes: null`; tested O1 -> O2 (non-null) -> unchanged rerun byte-identical to a fresh build, and a further rerun stable.
- C2: both sides require lease epoch == max(runtime.SCHEMA_EPOCHS); every read-only SQL block maps `sqlite3.Error` to `invalid_consumer_evidence` / `invalid_provider_evidence`.
- C3: the rebinding guard (all same-name root symbol rows safe and exactly one; no provider `assignment_targets` / `import_bindings` hit at that path, name and root scope) is implemented with the new closed detail `provider_name_rebound`; decorated definitions stay linked and the link records `declaration.decorated` (plus a limitation).
- C4: test `unresolved_provider_declaration_not_indexed` through the pure resolver with fabricated rows; skip-pattern components documented under limitation 6.
- C5: add a distinct closed detail `non_identifier_name` (keep `non_ascii_identifier` for identifier-shaped non-ASCII names); Day-1 (a) pins comment children, `from pkg import mod`, multi-name and parenthesised forms.
- C10: nested repo / graph-root overlap is a usage rejection (exit 2), pinned by a test; monorepo/worktree siblings are `same_repository`, documented.

Checker findings (verbatim):

1. **MAJOR: refresh is not a fixpoint once `P` carries `supersedes`.**
   - Evidence: D8 hashes `overlay_id` over the whole body, including `supersedes`. D9 compares `P.overlay_id == F.overlay_id`, where `F` is built with `supersedes: null`.
   - After any refresh (the output carries `supersedes`), re-running with `--previous <that output>` never matches. It emits another `supersedes` with `links_invalidated: 0`, and that repeats on every rerun. AC-004(d) and AC-005 only test a null-`supersedes` `P`, so they would pass while the operator flow still drifts.
   - Condition: compare a `content_id` (the overlay id computed with `supersedes` forced null). If the ids are equal, emit `F` (`supersedes` null). Otherwise record `supersedes` as D9 says.
   - Add a test: O1, then refresh to O2 (non-null), then refresh with inputs unchanged against O2. The result must be byte-identical to a fresh build, and a further rerun must be stable.
   - Confirmed: link ids embed `reference_id` (which holds both snapshot ids) and `vector_digest`, so they differ whenever the vector changes. `invalidated = P − F` is then all of `P`.

2. **MAJOR: epoch-8 graphs reach epoch-9-only SQL.**
   - Evidence: `runtime.py:39,237` accept epochs 8 and 9. `codegraph.py:954` falls back to `graph.8.db`. D6/D7 read `import_bindings` link columns, `occurrences`, and `symbols.entity_id`. Day-1 probes only a freshly built epoch-9 graph.
   - Result on a legacy graph: an uncaught `sqlite3.OperationalError`, then "link failed: OperationalError".
   - Condition: after `open_active`, require `reader._lease_schema_epoch == max(runtime.SCHEMA_EPOCHS)`. Do not import `codegraph` just for `SCHEMA_EPOCH`; the import allowlist forbids it.
   - Mapping: consumer maps to `consumer_index_upgrade_required`; provider maps to the existing `index_upgrade_required` detail.
   - Also wrap every ro SQL block in `sqlite3.Error`, mapping to `invalid_consumer_evidence` or `invalid_provider_evidence`. Day-1 (a) records the epoch.
   - Test the guard by overriding the lease epoch through the `open_active` spy.

3. **MAJOR: `linked_declaration` can be a false positive when the provider rebinds the name.**
   - Evidence:
     - S28 indexes only unconditional module-level defs (`python_wheel.py:613-641`). A conditional redefinition only sets the module's `incomplete` flag. S28 records no assignments or rebinding.
     - D7.6 counts only provider symbol rows with `import_export_safe is True`, so a second conditional `def area` is ignored.
     - The precedent refuses in that case: `python_imports.py:124-125` (any unsafe row) and `:114` (blockers from `assignment_targets`/imports).
     - The provider graph already holds the facts: `assignment_targets` (`codegraph.py:3404`, rows built at `:4351-4491`, including `import` kind) and `import_bindings`.
   - Condition, applied after the member bind passes: select all provider symbol rows at (path, name, root scope). If any row has `import_export_safe != True`, or the count is not 1, the result is `unresolved_ambiguous_provider_symbol` / `provider_symbols`.
   - Also query the provider's `assignment_targets` and `import_bindings` for that path, name and `python:<path>:root`. Any hit gives `unresolved_ambiguous_provider_symbol` with a new closed detail `provider_name_rebound`. Add it to `RESOLUTION_DETAILS`, the docs and the `validate_overlay` legality table.
   - Add fixtures: `area = wrap(area)`, a conditional redefinition, and `from x import area` after the `def`.
   - Decorated defs (the `perimeter` fixture) stay linked. Record `declaration.decorated` on the link, or list it as a limitation.

4. **MINOR: a stated test case for `unresolved_provider_declaration_not_indexed` is unreachable.**
   - Evidence: the indexer skips whole paths whose parts equal any `DEFAULT_SKIP_PATTERNS` entry (`codegraph.py:3743-3753`), so those files get no observation at all. They therefore fail the bind with `source_member_missing` at D7 step 5, before the lookup.
   - Condition: test this state with a unit-level call of the pure resolver (fabricated provider rows), or a Day-1 (c) disagreement case between S28 and the indexer. Drop the skip-pattern fixture.
   - Skip-pattern dirs are a real cause of `source_member_missing`. Name them in limitation 6 and the docs: `log`, `tmp`, `build`, `dist`, `coverage` and `node_modules` as any path component of the member. Multi-part patterns like `public/assets` never match.

5. **MINOR: comments inside a parenthesised import become bogus references.**
   - Evidence: `python_imports.py:43-60` treats every named child that is not `module_name` as a name. A `comment` child inside `from a import (b, # note\n c)` yields a row with `name == "# note"`.
   - Condition: Day-1 (a) probes this case.
   - D7.1 must not label such rows `non_ascii_identifier`. Rename the detail to `non_identifier_name`, or add a distinct one. Pin the outcome.
   - Day-1 (a) must also pin `from pkg import mod` (a row, hence `submodule_import`) and multi-name and parenthesised lists.

6. **MINOR: the shadow guard is weaker than it needs to be.**
   - Condition: count a consumer observation of any `state` (including `failed`) at the shadow paths, not only `indexed`.

7. **MINOR: the loader for overlay, previous-overlay and package-snapshot JSON is undefined.**
   - These documents contain integers (spans, lines), so D2's "`parse_int` raises" cannot apply.
   - Condition: use one strict loader for them. It rejects duplicate keys and NaN/Infinity, caps integer digit length at 20 or fewer with `parse_int`, and catches `RecursionError`, `MemoryError` and `ValueError`.

8. **MINOR: the vocabulary scan trips on `import_export_safe`.**
   - Evidence: the S28-style scan (`test_s28_isolation.py:105-114`) tokenizes module-level string constants. The token `safe` is banned (`:46`).
   - Condition: keep the literal `"import_export_safe"` inline inside a function and never emit it. Do not define a module-level constant for it. State the S30 scan scope (module-level constants plus output keys and values) in the test.

9. **MINOR: details of the AC-006 observation design.**
   - Call `runtime.open_active` as a module attribute so the spy takes effect.
   - `reader_lease` itself constructs a second `CodeGraph` (`codegraph.py:1331`). Assert the set of resolved repos, not a count.
   - Click's `exists=True` echoes the path on exit 2, as the other commands do (`__main__.py:95`). The "no echo" test must exempt Click usage errors.

10. **MINOR: same-repository semantics and root overlap.**
    - Evidence: `diff.py:221-223` compares git `common_dir`. Two directories of one monorepo or worktree are rejected as `same_repository`.
    - Condition: document this.
    - Pin the outcome (usage reject or documented accept) when the provider repo or either graph root nests inside the other repo. Roots that merely coincide are safe, because namespaces are keyed by `sha256(repo)` (`codegraph.py:912`).

11. **MINOR: `DependencyRejected` mapping from `validate_snapshot`.**
    - It raises reason `invalid_snapshot` (`python_wheel.py:965`). D11 says passthrough "unchanged". Specify that this one maps to `invalid_package_snapshot`, and that passthrough applies only to `select_package`.

12. **MINOR: impact seeds.**
    - Module-level import owners are file entities. `to_provider` from a consumer function entity returns nothing (limitation 9). State that seeds are file-level in the docs and the `unknown_seed` text.
    - Resolve Day-1 (a) "owner == file entity" before relying on it.

Items checked with no condition required:
- **Prior pins.**
  - S28 scans only `dependencies/*.py`.
  - S27 scans only `overlays`.
  - `test_s37:1192` only forbids `reports` at the top of `__main__.py`.
  - Src-wide scans cover only `alter table`, `nx.compose(`, and `import anthropic|openai|boto3`.
  - `cli.commands` checks are inclusive.
  - Docker tests list runtime files from both host and image.
  - The README TSX test reads only its own section, so the new row is safe outside it.
  - CHANGELOG locators are safe if the S30 bullet omits the prior titles, as planned.
  - `canary-missions.md` is untouched.
- **Replay.**
  - Load orders cover the mapping, the CLI options, the inputs and hash seeds.
  - No path appears in the overlay, so path-dependent snapshot ids affect only the inputs.
- **Three-way bind.** All six mismatch details are per-reference states. V1-V5 are each testable per reference as planned, including AC-002.
- **Python 3.14 and CI.** No interpreter-specific hazard found. `python:3.14` (non-slim) ships git.
- **Timebox.** Five days is plausible. If it slips, cut README and docs depth first, and land AC-001..007 before the docs.

## Stories
As an agent operator, I want a consumer's import of a locked package linked to the exact provider declaration only when the lock, the package artifact and the provider's indexed source agree byte for byte, so that agents never reason about a same-named symbol from another version.

As a reviewer, I want every link and impact answer to name the ordered consumer/provider snapshot pair it combined, stay inside the two allowlisted repositories, and leave both graphs untouched.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s30.criteria.md` (sha256 `421bbc3c…7df8`), verbatim:

```
### AC-S30-001 (event-driven)
GIVEN the locked coordinate and explicit provider mapping match validated snapshots
WHEN a supported external reference is linked
THEN the link result SHALL carry the exact ordered consumer/provider revision vector.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_link_has_exact_vector

### AC-S30-002 (unwanted-behavior)
GIVEN a same-named provider symbol belongs to a different package version or source digest
WHEN link resolution considers that provider
THEN the linker SHALL leave the reference unresolved_version_mismatch.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_wrong_version_is_unresolved

### AC-S30-003 (unwanted-behavior)
GIVEN the mapping's exact provider snapshot is unavailable
WHEN impact traversal reaches the external reference
THEN the traversal SHALL return a missing_provider_snapshot boundary record.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_missing_snapshot_is_boundary

### AC-S30-004 (event-driven)
GIVEN either consumer or provider snapshot identity changes
WHEN the operator refreshes the cross-repository overlay
THEN the linker SHALL invalidate links computed for the superseded revision vector.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_vector_change_invalidates_links

### AC-S30-005 (event-driven)
GIVEN identical authorized snapshots, mappings and producer version
WHEN the overlay is rebuilt twice in different input load orders
THEN the canonical cross-repository link set SHALL remain identical after repeat construction.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_link_replay_is_idempotent

### AC-S30-006 (ubiquitous)
THEN cross-repository traversal SHALL remain within the repositories explicitly listed in the mapping manifest.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_allowlist_bounds_traversal

### AC-S30-007 (ubiquitous)
THEN cross-repository linking SHALL leave each source repository's canonical fact store unchanged.
VERIFY: test: mcp-server/tests/slices/test_s30_cross_repo.py::test_cross_repo_overlay_preserves_source_graphs

### AC-S30-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S30-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

### Fixtures
**`test_s30_support.py`** loads `tests/fixtures/python_dependencies/generate.py` via `spec_from_file_location` (as `test_s28_support.py:30-36` does) and uses `build_zip`, `Entry`, `wheel_line`, `package_block` and `lock_document`. It reuses the S28 `grammar` session fixture semantics: fail, never skip.

**Package `s30-shapes` 1.0.0** (in-test wheel; dist-info `s30_shapes-1.0.0.dist-info` with METADATA/WHEEL built in-test). Members:
- `s30_shapes/__init__.py`: `from .geometry import area`, `def describe(): ...`
- `s30_shapes/geometry.py`: `import math`, `class Circle`, `def area`, `async def fetch_area`, `@functools.cache def perimeter`, `def _hidden_helper`
- `s30_shapes/compat.py`: `if TYPE_CHECKING:\n    def hidden(): ...` (incomplete module)
- `s30_shapes/dup.py`: `def twice` defined twice (ambiguous)
- `s30_shapes/_version.py`

**Provider repo `lib/`** (git):
- `src/` + every `.py` member byte-identical, so digests match by construction;
- `src/other_mod.py` with a root `def area` (a same name outside the package, never linked);
- `tools/notes.py` (edited to change the provider snapshot without touching members).

**Consumer repo `app/`** (git): `uv.lock` (s30-shapes 1.0.0 pinned to the wheel sha), `app/area_tools.py` (a local `def area`), and `app/main.py` with one line per state:
- `from s30_shapes.geometry import area, Circle`; `... import perimeter as per`; `... import fetch_area` → linked
- `from s30_shapes import area as reexported_area` → reexport
- `from s30_shapes.geometry import missing_name` → name_not_declared
- `from s30_shapes.compat import hidden` → module_incomplete
- `from s30_shapes.nothere import x` → module_not_in_package
- `from s30_shapes.dup import twice` → ambiguous/package_declarations
- `from s30_shapes import geometry` → submodule_import
- `from s30_shapes.geometry import *` → wildcard_import
- `import s30_shapes.geometry` → module_import
- `def run(): from s30_shapes.geometry import area` → nested_scope_import
- `if TYPE_CHECKING: from s30_shapes.geometry import Circle` → linked (pins limitation 3)
- `import json`; `from .local import y` → out of scope

**Variants:** (V1) one byte changed in provider `src/s30_shapes/geometry.py`; (V2) a lock pinning 1.1.0 with the mapping at 1.0.0; (V3) the mapping and lock at 1.1.0 with the S28 snapshot of 1.0.0; (V4) a lock with a different wheel hash; (V5) provider without `_version.py`; (V6) a consumer file `s30_shapes.py`; (V7) provider re-indexed after editing `tools/notes.py`; (V8) a sentinel third repo `other/`, indexed, holding the same package.

`tests/fixtures/cross_repo/` (static, JSON only, no `.py`):
- `mapping-example.json`: the D2 shape with zero-hex ids, also quoted in the docs; a test parses it and checks the docs embed it verbatim.
- `world.json`: the consumer and provider file texts listed above, so test code and docs share one source.

There are no golden overlays: snapshot ids depend on absolute paths. Read-only tests use module-scoped worlds; only AC-003/004 and V7 re-index. Every `all(...)` is preceded by a non-empty assertion, and nothing under test is mocked.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_cross_repo_link_has_exact_vector | **Main world:**<br>- `links` is exactly 5 (area, Circle, perimeter, fetch_area, TYPE_CHECKING Circle);<br>- every `link.revision_vector == [{"role":"consumer","repository_id":"app","snapshot_id":Sc},{"role":"provider","repository_id":"lib","snapshot_id":Sp}]` (list order, exact keys), where Sc/Sp are the `build()` stats ids and equal `reader_lease().snapshot_id`;<br>- `vector_digest` and link id recompute; `provider.entity_id` equals the provider `symbols` row at `src/s30_shapes/geometry.py` (never `src/other_mod.py` or `app/area_tools.py`);<br>- `consumer.assertion_id`/`occurrence_id` equal the `import_bindings` row; the aliased `per` has the same provider entity as unaliased `perimeter` would;<br>- `relation == "external_reference"` and `call_semantics == "not_claimed"`; binding `bound` with `members_equal == members_compared == 5`;<br>- the CLI output bytes equal the library's; the summary vector equals the overlay's.<br>The full D7 resolution table equals the expected multiset. |
| 002 | test_cross_repo_wrong_version_is_unresolved | For V1/V2/V3/V4/V5 (each with its own valid mapping/lock/snapshot):<br>- the provider graph still has a root `area` symbol (same name, asserted directly);<br>- the `area` reference is `unresolved_version_mismatch` with detail `source_digest_differs`/`coordinate_differs`/`coordinate_differs`/`artifact_digest_differs`/`source_member_missing`;<br>- `links == []`; `binding.state == "version_mismatch"`; checks and counts as expected;<br>- exit 0 (a reference state, not a rejection); the positive control (unmutated) links. |
| 003 | test_cross_repo_missing_snapshot_is_boundary | (a) Overlay O1, then V7 re-index. `to_provider` from the `app/main.py` file entity → a `missing_provider_snapshot` boundary per reference with `detail "snapshot_not_active"`, `at "reference"`, the declared provider id, and `results == []`. A `set_trace_callback` on the provider reader, captured via a spy on `open_active`, records zero statements after the lease.<br>(b) `from_provider` with the `area` entity → one boundary at `seed`.<br>(c) Provider graph root emptied → `detail "index_unavailable"`.<br>(d) An overlay built while the provider was not active → references `unresolved_provider_snapshot_unavailable`, and impact gives the boundary.<br>Every result has `status potential_impact`, `basis static_reference`, `runtime_observation not_performed`. |
| 004 | test_cross_repo_vector_change_invalidates_links | (a) Consumer edit + re-index + new mapping, `--previous O1` → O2: `supersedes == {overlay_id: O1.id, vector_digest: O1.vd, links_invalidated: 5, invalidated_link_ids: sorted(O1 ids), invalidated_link_ids_truncated: false}`; O2 ids ∩ O1 ids = ∅; O2 has 5 links carrying the new vector.<br>(b) V7 + new mapping, `--previous O2` → all invalidated.<br>(c) V7 with the stale mapping → provider unavailable, `links == []`, all invalidated.<br>(d) `--previous` equal to a fresh build → byte-identical output, `supersedes: null`.<br>(e) Impact over O1 after (a) → `stale_revision_vector`.<br>(f) A previous overlay from other ids → `previous_overlay_unrelated`. |
| 005 | test_cross_repo_link_replay_is_idempotent | Byte-identical overlays across:<br>- two library builds;<br>- the mapping with repositories swapped, keys reordered and re-indented, environment keys reordered;<br>- inputs copied to other dirs;<br>- CLI option order permuted;<br>- CLI out1/out2, and `--previous out1` (fixpoint);<br>- `python -m atlas_aci cross-repo-link` with `PYTHONHASHSEED=1/2`;<br>- fresh graph roots at the same repo paths, files rewritten in reverse order (if Day-1 (e)).<br>Every id recomputes. |
| 006 | test_cross_repo_allowlist_bounds_traversal | A spy on `CodeGraph.__init__` (resolved repos) and on `open_active`:<br>- a successful link and impact touch only {app, lib};<br>- a mapping whose package names `other` → `repository_not_allowlisted` with ZERO constructions and no lock or snapshot read (CLI `os.open` recorder);<br>- 3 repositories → `invalid_mapping`, zero opens;<br>- an overlay re-hashed via `overlay_identity` naming `other` → impact `repository_not_allowlisted`, zero opens;<br>- a seed from `other` → `unknown_seed`;<br>- results only carry ids ∈ mapping; the provider snapshot's `imports` (`math`, `functools`) are never followed;<br>- CLI `--help` has exactly the two role root pairs; a mapping with a `root`/`path` key → `invalid_mapping`. |
| 007 | test_cross_repo_overlay_preserves_source_graphs | An inventory (S19 `inventory` extended with size, mode and mtime_ns; dirs listed; `-journal/-wal/-shm`, `source-snapshots`, packs, `repos/<sha>`) of app, lib, other, both graph roots and the inputs is equal before and after:<br>- library and CLI link, refresh, impact (both directions);<br>- every rejection path: mapping, lock digest, consumer mismatch, provider unavailable, invalid previous, stale vector, unknown seed, output usage.<br>Only `--output` appears, and no `.atlas-cross-repo-overlay-*` remains. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt is `.spectra/state/aci-s30.execution.json`. |

### Supporting tests
- **`test_s30_rejections.py`.**
  - Every D11 reason at library level, with a CLI exit 1 exact text, empty stdout, a sentinel `--output` byte-unchanged, and no temp file left.
  - **No echo:** no path, sentinel or input text appears in stderr.
  - Mapping cases: BOM, non-UTF-8, duplicate key, number, NaN, depth 10000, extra key, bad id/coordinate/prefix (`../`, `.x/`, absolute), wrong format, env not canonical.
  - S28 passthrough: `package_not_in_lock` and `unresolved_lock_variant`.
  - Package snapshot: non-canonical bytes, tampered id.
  - Usage exit 2: every limit/budget (bool, 0, high+1), bad seed and direction, output in each repo/.atlas/graph root (path, symlinked parent, hardlink), output == each input, unusable namespace.
  - Inputs as a FIFO or a directory → the fixed read texts, promptly.
- **Resolution states.** One test per D7 state and detail, including V6 shadow, `nested_scope_import`, `non_ascii_identifier` (a non-ASCII module part), and `unresolved_provider_declaration_not_indexed` (provider file under a skip-pattern dir with the prefix pointed there, per Day-1 (b)).
- **Budgets.** Each cap at 1 → `budget_exhausted` with its detail and `complete: false`; deterministic truncation by id; default/low/high table.
- **`test_s30_isolation.py`.**
  - Tools JSON sha and bytes; `READ_ONLY_TOOLS` 7; 3436/3448/797/7730; `ev.RELATIONS` unchanged; `cross-repo-link` and `cross-repo-impact` ∈ `cli.commands`; "cross-repo"/"cross_repo" absent from the tools JSON.
  - `cross_repo/*.py == [__init__, package_links, revision_vector]`; ASCII; lines ≤100.
  - The S28 banned-regex/method scan (no `\d \w \s $`, no `isidentifier`); no self-recursion; no "alter table" or `nx.compose(`; no `open(`, `os.replace`, `tempfile` or `sqlite3.connect` in `cross_repo/`; only SELECT SQL (planted positive control).
  - Import allowlist: `__future__, hashlib, json, re, contextlib, collections.abc, dataclasses, typing, pathlib, atlas_aci.{overlays.runtime, query.diff, dependencies.uv_lock, dependencies.python_wheel, storage.evidence, cross_repo.revision_vector}`.
  - Vocabulary: string constants plus keys/enum values of the overlay, impact and summary avoid `BANNED_VOCABULARY ∪ {applied}`, with a planted hit.
  - **`validate_overlay` tamperings:** overlay_id, vector_digest, a link id, swapped vector order, a link vector differing from the top, an unknown resolution, an illegal detail, a linked reference without a link and the reverse, counts, coverage, limitations, unsorted references or links, a supersedes id inside links, unsorted supersedes, an extra key, `relation:"call"`, `call_semantics:"claimed"`.
  - **`validate_impact` tamperings:** result_id, status, an unsorted list, an unknown boundary kind.
  - **Docs:** `docs/cross-repository-links.md` is ASCII and names every closed value, format, limit, budget (with defaults), limitation and CLI text, plus the phrases "not a call", "call_semantics", "does not write the graph", "CLI only, no MCP op", "operator_declared", "re-exports are not followed", "conditional", "active generation", "source_member_missing", "never discovers, clones or fetches", "index_done". `mapping-example.json` is embedded verbatim.
  - The CHANGELOG entry (located by "Cross-repository package links") and the README row (by `docs/cross-repository-links.md`) contain "not a call" and "CLI only, no MCP op", and contain none of the prior locators or titles.

### Gates beyond the ACs
- **G-SRC.**
  - `git diff --exit-code dc57096 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage,extractors,providers,indexing,resolvers,frameworks,architecture,counterfactual,contracts,importers,overlays,dependencies,reports} mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows`
  - `git diff --name-only dc57096 -- mcp-server/src` ⊆ {`cross_repo/__init__.py`, `cross_repo/package_links.py`, `cross_repo/revision_vector.py`, `__main__.py`}.
  - Test changes are only `test_s30_*.py` and `fixtures/cross_repo/**`. No prior test is edited.
- **G-MCP.** Tools JSON `94f9567a…` / 1631472; `READ_ONLY_TOOLS` 7.
- **Regression.** `test_s19*`, `test_s20*`, `test_s27*`, `test_s28*`, `test_s29*`, `test_thesis_negatives`, `test_export` are green and unchanged.
- **CI-exact lint.** `uv run --frozen ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/`.
- **Full suite** with the test_s08 nonregular_pack deselect. No new failure or skip vs Day-1 (h).
- **py314.**
  - `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen pytest tests/slices/test_s30_*.py -q'`.
  - Throwaway; the host `.venv` is untouched. Log to `.spectra/evidence/aci-s30/py314.log`.
- **Production image** (`.spectra/evidence/aci-s30/prod-image.sh`, written by the executor, run by the orchestrator):
  1. A host step writes the world (app, lib, wheel, lock) to a scratch dir via `make-world.py` (it loads the support generator).
  2. Build `mcp-server/Dockerfile`, then `docker run --rm --network none -v <scratch>:/w`.
  3. `atlas-aci index` app and lib into `/w/graphs/*`; read the snapshot ids from `index_done`.
  4. `import-python-dependency` → `/w/pkg.json`; write the mapping.
  5. `cross-repo-link` twice → `cmp` equal; 5 links.
  6. Edit `tools/notes.py`, re-index lib, new mapping, `--previous` → `supersedes.links_invalidated == 5`.
  7. Impact with the old overlay plus the old mapping → `missing_provider_snapshot`.
  8. Mapping naming `other` → exit 1 `repository_not_allowlisted`; lock flipped → `lock_digest_mismatch`.
  9. `sha256sum` inventories of `/w/app /w/lib /w/graphs` equal around steps 5, 7 and 8.
  10. `atlas-aci tools` matches the pin. Record `cmp` and `sha256sum` availability.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s30_cross_repo.py -q
uv run --frozen pytest tests/slices/test_s30_*.py -q
uv run --frozen pytest tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/slices/test_s27_*.py tests/slices/test_s28_*.py tests/slices/test_s29_*.py tests/test_thesis_negatives.py tests/test_export.py -q
uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/ && uv run --frozen mypy src/
```

## Confidence
To be computed with `ramza-score`.
- **Why it is low-churn.** S30 is a read-only composer over two existing leases and one S28 snapshot. Every primitive exists and is used by S27/S31/S32 (`open_active`, `resolve_output`, `select_package`, `validate_snapshot`). There is no graph, protocol or gated change.
- **Main uncertainties**, covered by Day 1 with a stop rule:
  - the `import_bindings` and occurrence row shapes;
  - provider kind/scope agreement with S28 declarations;
  - snapshot-id determinism across fresh roots;
  - whether read-only leases are byte-silent.

## Rejected Alternatives
- **Writing cross-repository edges into the consumer graph** (a derived generation). It breaks AC-007, every publish changes the consumer snapshot id, and it edits gated paths.
- **Binding the provider by the S28 snapshot alone** (scout option b). There is no provider repository evidence, so "provider snapshot" would be a label.
- **Binding by a manifest-declared digest only** (option c). An unchecked operator claim.
- **`exclude_members`.** It weakens the bind; a mismatch is the stricter, honest outcome (limitation 6).
- **`import pkg.mod` as a module link.** No declaration identity; cut to a typed state.
- **Reusing S20 `compute`.** Same-repo before/after semantics.
- **Recording the raw mapping sha.** Load-order variants would change the overlay (AC-005); the canonical digest is used instead.
- **Always recording `supersedes`.** It never reaches a fixpoint (D9).
- **Reusing `runtime.write_atomic`.** Its temp prefix names S27.
- **Roots in the mapping.** That turns the manifest into a discovery channel (AC-006).

## Risks (ranked)
1. **Snapshot ids depend on absolute paths, and possibly on the graph root.** No goldens; in-test replay; Day-1 (e) decides one AC-005 variant.
2. **Graph extraction and S28 extraction disagree** (decorated, async, conditional, nested). The typed state `unresolved_provider_declaration_not_indexed`, never a guess; Day-1 (c).
3. **Read-only leases touching the filesystem** (journal, atime-only dirs). Day-1 (d) STOP; inventories include mtime.
4. **Real packages ship generated members.** Mismatch by design; documented (limitation 6) as a candidate follow-up.
5. **Coupling to private reader attributes** (`_source_lease`) and to `overlays.runtime`. Same as S19/S27, pinned by G-SRC and their own tests.
6. **Vocabulary and regex scans** (`applied`, `isidentifier`, `$`). The isolation test runs from Day 1 PM.
7. **Timebox (5 days).** Follow the cut order; at the limit, report and propose a follow-up.
