---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-02T00:00:00Z
slice_id: ACI-S14
issue: https://github.com/Rynaro/atlas-aci/issues/36
---
# ACI-S14 — measured TSX structural extraction

## Scope

Implement issue #36 on merged base `738358d` (main after PR #80), branch `aci-s14`.
Live marker `<!-- atlas-aci:slice:ACI-S14 -->` verified; no prior S14 plan, state
or receipt existed. Exact criteria bytes (between the markers, leading newline
removed) hash to `b9e4c691fd54910b1ff62b140f737fe197fe02e13313e6031eee2fcdf04bbddf`,
equal to the contract table. Predecessors verified on the execution base:
S04 (#27, merge `7b11b27`), S07 (#29, merge `dd88912`), S10 (#32, merge `f055e56`)
are ancestors of HEAD with COMPLETE receipts; their status/capabilities,
observation diagnostics and dependency-bucket interfaces exist in current source.

In:
- `.tsx` extraction with the locked grammar (tree-sitter 0.25.2,
  tree-sitter-language-pack 1.6.0, grammar `tsx`).
- Definitions: `class` (class declarations, incl. class components), `function`
  (function declarations, and `const`/`let` declarators whose value is an arrow
  function or function expression — the common component form), `method`.
- Import occurrences for default, named (aliased and unaliased), namespace and
  type-only specifiers with imported name, preserved local alias, module source and
  kind; every import stays UNRESOLVED (`tsx_module_resolution_unavailable`).
- Call/construct/superclass occurrences with the same scoped structural binding as
  TypeScript (no global name fallback).
- JSX element references for value-referencing tags only (capitalized identifier or
  member expression, per JSX's own syntactic rule; intrinsic lowercase/namespaced
  tags are host elements, not code references). Persisted with new relation `jsx`,
  never `call`, and always UNRESOLVED (`jsx_semantic_binding_unavailable`).
- Export status on declarations (`named`/`default`/none) in symbol binding context.
- Parse failures reported through existing completeness diagnostics (file
  observation `failed`/`parse_error` with source location).
- Capability snapshot `atlas-capabilities.v4`: explicit TSX definitions/relations
  plus TSX limitations; v1/v2/v3 snapshots stay readable and keep TSX unsupported.
- README relation-by-relation support matrix, each row backed by a named fixture test.

Out: resolving JSX tags as calls, React-specific flow (hooks, props, context,
HOCs), module/path resolution, type inference, re-export specifier occurrences,
interface/type-alias/enum declarations, anonymous default exports as named
declarations, new dependency versions, Docker/CI/lockfile edits (Docker prefetch
already iterates `DEFAULT_LANGS`, which derives from `QUERIES`).

Declared implementation paths:
- mcp-server/src/atlas_aci/codegraph.py
- mcp-server/src/atlas_aci/extractors/tsx.py (new; fingerprinted producer module)
- mcp-server/tests/slices/test_s14_*.py
- mcp-server/tests/fixtures/s14/*
- README.md

Scope amendment (recorded in `.spectra/changes/aci-s14/scope-amendment.json` before
implementation):
- mcp-server/src/atlas_aci/storage/evidence.py — add `jsx` to the closed relation
  set and the exact extended producer module set; historical sets stay valid.
- mcp-server/src/atlas_aci/indexing/extraction_cache.py — accept TSX raw import
  rows (cache packet validation is currently Python-only).
- mcp-server/src/atlas_aci/resolvers/dependencies.py — register `tsx` in complete
  language buckets.
- mcp-server/tests/test_codegraph.py — drop obsolete TSX-unsupported expectation;
  keep Go/Java negative controls.
- mcp-server/tests/slices/test_s15_contract.py, test_s15_integration.py — current
  format/module-set pins move to v4 / the extended module set; historical rows kept.
- `.spectra/plans/aci-s14.*`, `.spectra/plans/ramza-calibration.jsonl`,
  `.spectra/changes/aci-s14/*`, `.spectra/evidence/aci-s14/*`,
  `.spectra/state/aci-s14.execution.json`, `.spectra/backlog/*`.

Rightsize FULL 5 (12 files, public capability format, novel extraction, med stakes).

## Approach

1. Qualify grammar: dump the locked `tsx` parse tree for a fixture covering function
   and class components, arrow components, aliased/default/namespace/type imports,
   JSX intrinsic/component/member tags, fragments, anonymous default export and a
   broken declaration. Record node shapes as evidence.
2. `extractors/tsx.py`: `QUERY`, `KINDS`, `RELATIONS`, `LIMITATIONS`,
   `component_tag(node, source)` (JSX value-reference rule), `imports(tree, source)`
   (yield import node, name node, description), `export_status(node)`.
3. `codegraph.py`: register `QUERIES["tsx"]`, drop `tsx` from `UNSUPPORTED_LANGS`;
   treat `tsx` like TypeScript in scope/binding/blocker/resolution code paths; add a
   JSX branch in `_extract` (relation `jsx`, role `reference:jsx`); persist TSX
   imports into `import_bindings` with a TSX description shape; resolve TSX imports
   and JSX references as UNRESOLVED with explicit reasons; v4 capabilities and
   validator (format derived from producer module set; pre-v4 TSX must be
   unsupported; v4 TSX definitions/relations pinned); producer modules include
   `extractors/tsx.py`.
4. Fixtures under `tests/fixtures/s14/` and contract tests in
   `tests/slices/test_s14_contract.py` exercising the real indexer only (no mocks).
5. README section "TSX structural navigation" with the measured relation matrix.
6. Gates: ruff check/format, mypy, full pytest in the lock-faithful Docker dev
   image, then no-op re-entry and interrupted-resume receipt gates.

### Independent checker conditions (ATLAS review, APPROVE_WITH_CONDITIONS)

Recorded in `.spectra/plans/aci-s14.review.json`; all are binding on implementation.

1. `protocol/graph.py` PRODUCER `modules` schema is closed (`additionalProperties:
   false`): add `extractors/tsx.py`; test a real status payload against the schema.
2. Arrow/function-expression component declarators own their occurrences, so
   `_binding_context.source_node` must include a `variable_declarator` whose value is
   an `arrow_function`/`function_expression` (TSX only), else the edge-owner
   projection invariant fails. Test a resolved edge from inside an arrow component
   through `--since` and export/import round-trip.
3. Do NOT add `jsx` to `ev.RELATIONS` (it feeds traversal enums and the measured
   3,436/3,448-byte minimum envelopes). Add a separate reference-only set
   (`ev.REFERENCE_RELATIONS = RELATIONS | {"jsx"}`) used for raw refs, unresolved
   assertions and cache rows; `jsx` assertions must be UNRESOLVED; edges keep
   `RELATIONS`. Protocol records expose only EXTRACTED/INFERRED assertions, so no
   published schema or envelope changes.
4. Export gaps are reported, not silent: `tsx.LIMITATIONS` lists re-export
   specifiers, `export default <identifier>`, anonymous default exports, intrinsic
   JSX tags and dynamic binding (dynamic `import()`, computed member access); each is
   in v4 capabilities, README matrix, and a fixture test.
5. Rationale comments: add `tsx` to `_match_rationale_comment` (same rules as TS)
   with a test, so the advertised `rationale_for` relation is truthful.
6. One shared JS-family constant (`javascript`, `typescript`, `tsx`) replaces every
   hardcoded `{"javascript","typescript"}` site; `jsx` short-circuits in
   `resolve_reference` before the scoped-language gate.
7. TSX imports short-circuit in `import_target` to
   `tsx_module_resolution_unavailable` before shadow/ambiguity checks; TSX description
   branches in the codegraph projection validator and the extraction-cache validator;
   tests cover cold build, cache hit and export/import round-trip.
8. Historical readability: genuine v3 snapshot captured from base `738358d`
   (`tests/fixtures/s14/historical-v3-capabilities.json`) must still validate. Format
   rule: v4 ⇔ `extractors/tsx.py` in producer modules; v3 ⇔ rust present, tsx absent;
   pre-v4 TSX must be unsupported; v4 TSX definitions/relations pinned exactly.
9. Append `extractors/tsx.py` last in `_producer_inputs` modules (ordered historical
   prefixes are validated).
10. Non-vacuous AC tests: AC-003 uses a same-file and an imported component, asserts
    zero edges and empty `callers_of`; AC-004 asserts the exact error line, a sibling
    good file still indexed and no leaked symbols; AC-005 includes a cross-file user
    of the renamed symbol; AC-006 compares warm-cache with cold-cache runs.
11. JSX intrinsic rule: lowercase ASCII first character or namespaced name; `_Foo`,
    `$x`, member expressions are value references. README matrix cross-checked
    against `_capabilities()` by a test. `INTEGRATION.md` TSX row and CHANGELOG
    `[Unreleased]` updated.

## Stories

As an agent operator indexing a TypeScript UI repository, I want `.tsx` components,
methods and imports to be navigable symbols and occurrences, so that coding agents
receive real structural evidence instead of an "unsupported extension" skip.

As a reviewer of graph evidence, I want JSX tags, imports and dynamic bindings to
remain explicitly unresolved with reasons, so that no fabricated call edge is
presented as executable truth.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s14.criteria.md` (sha256 `b9e4c691…bbddf`).
Mapping to tests (all in `mcp-server/tests/slices/test_s14_contract.py`):

| AC | Test | Observable |
| --- | --- | --- |
| AC-S14-001 | test_tsx_definitions | function + class component symbols, exact line spans and occurrence byte spans |
| AC-S14-002 | test_tsx_imports | `useState as useLocalState` import row keeps local alias, name and source |
| AC-S14-003 | test_jsx_not_fabricated_call | JSX tags: relation `jsx`, UNRESOLVED, zero edges, no `call` assertion |
| AC-S14-004 | test_tsx_diagnostic | broken TSX file reported `failed`/`parse_error` with location in status |
| AC-S14-005 | test_tsx_incremental | rename + `--since` build canonical TSX facts == clean rebuild |
| AC-S14-006 | test_tsx_replay | two builds of unchanged source give equal canonical TSX facts |
| AC-S14-RUN-01 | gate | SHA-256 manifest of declared tracked files equal before/after re-entry; `git diff --exit-code` |
| AC-S14-RUN-02 | gate | slice_id + criteria_sha256 equal in interrupted and resumed receipt; one receipt path |

## Confidence

Computed with `ramza-score` (recorded in state gates). Grammar shapes were observed
directly, the extension pattern has a merged precedent (S15 Rust), and the binding
machinery for TS is reused rather than reinvented. Main uncertainty: validator
coupling (cache packets, evidence projection checks) to Python-shaped import rows.

## Rejected Alternatives

- Alias `tsx` to the TypeScript query unchanged: misses JSX honesty and imports,
  and TS-grammar queries do not cover JSX nodes.
- Persist JSX tags as `call` references (even unresolved): labels markup as an
  executable call, violating AC-S14-003's intent.
- Drop JSX occurrences entirely: honest but discards real reference evidence.
- Keep capability format v3 and silently add TSX: changes what a v3 snapshot means;
  old v3 snapshots would be indistinguishable from new ones.
- Resolve imports against `.ts/.tsx` files by path: module resolution (tsconfig
  paths, index files, extensions, package exports) is out of scope; a partial guess
  would fabricate bindings.

## Risks

- Python-coupled import validators (cache packet, projection checks) reject TSX rows
  → mitigated by lang-specific description shapes, both directions tested.
- Extending the closed relation set could weaken historical validation → historical
  rows stay valid; `jsx` only accepted where produced.
- Arrow-component declarators are also assignment blockers, so same-file calls to
  them stay unresolved (`shadowed_noncallable`), never mis-resolved.
- Timebox: 3 engineering days; at the limit report remaining gaps, no silent growth.
