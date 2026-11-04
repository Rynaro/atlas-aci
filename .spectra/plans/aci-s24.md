---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T12:00:00Z
slice_id: ACI-S24
issue: https://github.com/Rynaro/atlas-aci/issues/45
---
# ACI-S24 — Rails: map explicit routes to controller actions

## Scope

Implement issue #45 on base `2e342a3` (main after PR #91; S01-S23, S29, S31, S32 and S37 merged), branch `aci-s24`.
- Live marker `<!-- atlas-aci:slice:ACI-S24 -->` verified. There is no prior S24 receipt or execution state.
- The criteria bytes hash to `7bb68294…1ee1173`, equal to the contract table (7 product ACs plus RUN-01/02).
- Predecessors are present on the base:
  - S07: the closed evidence store `storage/evidence.py`.
  - S10: `resolvers/dependencies.py` plus the per-file replacement in `_run_build`.

Observed facts (line numbers on `2e342a3`):
- **`storage/evidence.py`:**
  - `RELATIONS` (:45) feeds MCP enums. `REFERENCE_RELATIONS`/`EDGE_RELATIONS` are at :57-58. `ASSERTION_RELATIONS` (:65) is the only gate on assertion relations (:451). `COVERAGE_RELATION` is the S21 precedent (:60-71).
  - Occurrence kind is a closed set (:395-397), and the kind↔role-prefix pairing is at :408-424. The producer fingerprint branches by top-level key (:277-282).
  - Assertion validity (:475-489): EXTRACTED/INFERRED ⇒ target, no candidates, no reason. Otherwise ⇒ no target and a non-empty `unresolved_reason`, with `bool(candidates) == (resolution == "AMBIGUOUS")`.
  - `validate_database` (:756-846): occurrence/entity location, evidence and assertion discrimination by producer and occurrence kind, and the generic rule "assertion source path == occurrence path".
  - Builders: `producer` :849, `file_entity` :861, `occurrence` :943, `provenance` :972, `assertion` :990.
- **`codegraph.py`:**
  - `__init__` :874-888.
  - `_producer_inputs` :1440 (fixed module list, not edited).
  - `_build_inputs` :2338. The configuration shape is closed: `{"languages","skip_patterns"}` at :1160. Generation-input keys are closed at :1132. The `manifest` table is free-form (`dep.KEY` at :3536-3540).
- **`_run_build` :3086:**
  - The source bytes are read and hashed at :3190-3198, before the disabled, unchanged-reuse and parse-failure `continue`s (:3200-3300).
  - Per-file removal uses `ev.remove_path` at :3299, :3312 and :3470.
  - `all_observed` is at :3494.
  - `dep.snapshot` (current) is at :3512.
  - `_resolve_edges` :3530 deletes the assertions of the selected paths (all of them in full mode, :4541-4553).
  - `_resolve_rationale_assertions` :3531.
  - `_validate_evidence_projections` :3532.
  - `dep.witness` :3534.
  - The snapshot binding at :3552 binds every occurrence and evidence record (:1248-1266).
- **Predecessor history:** `dropped` (:2567) excludes SCIP/coverage records from `_retained_closure` (:5895), which follows `*_id` links and evidence→assertion links.
- **`_validate_evidence_projections`:** role dispatch :6384-6401; assertion projection rule :6405-6423 (a resolved assertion needs an `edges` row); `coverage_py.validate_records` :6424. Active occurrences must match an `indexed` observation's hash and language (:6018-6033).
- **Ruby symbols** (`_binding_context` :4047, `_symbol_binding` :4221):
  - class `binding.namespace` is the enclosing class/module names plus the declared name. `module Admin; class UsersController` and `class Admin::UsersController` both give `Admin::UsersController`.
  - method `binding.owner` = `"ruby:"+namespace`; `category` is `instance` unless it is a `singleton_method` or inside `class << self` (`static`). The `symbols` columns include `entity_id`.
- **Query surfaces:**
  - `query/indexed.py:184` traverses only `ev.RELATIONS`.
  - `diff.py:115` and `retrieval.py:94-102` read only `definition` occurrences.
  - `architecture/checker.py:105-114` filters by relation.
  - `query/validation.py:360` loads assertions by path, only for probed Python test paths.
  - `query/impact.py:150-152` counts ALL occurrences for diff admission.
- **`storage/snapshots.py:38-53`** runs `git rev-parse` through `subprocess.run` on every build and catches `OSError`.
- **Pins:** tools JSON sha `94f9567a…ac56` / 1631472 bytes; 3436/3448/797/7730. CI runs Python 3.14 (`ci.yml:108`); the dev and production images run 3.12.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)
1. **In-build opt-in.** `CodeGraph(..., frameworks=())` and `atlas-aci index --frameworks rails`.
   - The registry is closed: `{"rails"}`. An unknown name is a usage error.
   - Rails records join the shared store inside `_run_build`, before `_snapshot_binding`.
   - There is no new generation kind and no protocol/query/server/tools edit.
   - The flag is not in `configuration`, and `frameworks/` is not in `_producer_inputs`.
2. **Scope amendment** for `storage/evidence.py` (minimal, S21-style) and `frameworks/__init__.py`.
   - `routes_to` goes only into `ASSERTION_RELATIONS`.
   - One occurrence kind, `rails_route`, is bound 1:1 to the role prefix `rails_route:`.
   - No new entity kind; the assertion source is the `config/routes.rb` file entity.
3. **Producer-id churn for every graph is accepted** (S16/S21 precedent) and stated in the plan, docs and PR. Gates (a)/(b)/(c) are in G-OFF, G-ONOFF and G-IMAGE.
4. **Shapes:** unique ⇒ INFERRED; ≥2 ⇒ AMBIGUOUS with candidates; otherwise UNRESOLVED with closed reasons. The convention label is a validated record field (D2).
5. **Invalidation:** recompute from scratch on every enabled build; purge on every build; rails ids join `dropped`.
6. **Constructs:** stricter reading (D3/D4).
7. **Lookup:** the `symbols` table, not restricted to `app/controllers`.
8. **No execution:** AST ban, spies, a sentinel and portability rules.
9. **Invariants:** as listed in the gates.
10. **Docs:** `docs/frameworks/rails.md` plus CHANGELOG/README pointers.

### In
1. `frameworks/__init__.py`: the closed registry plus `normalize(names, languages)`.
2. `frameworks/rails_routes.py`: constants and vocabularies, `scan` (pure), `camelize`, `resolve`, `refresh` (purge, emit, manifest), `record_ids`, `validate_records`.
3. `codegraph.py` edits CG1-CG8 (D6); `storage/evidence.py` edits EV1-EV6 (D5); `__main__.py` adds `index --frameworks`.
4. Fixture `tests/fixtures/rails_routes/{project/**,provenance.json}`; tests `tests/slices/test_s24_rails_routes.py` and `test_s24_isolation.py`.
5. `docs/frameworks/rails.md`, plus a CHANGELOG `[Unreleased]` entry and a README pointer.

### Out
- Running Ruby, Rails, rake, bundler or initializers.
- Resource/namespace/scope/concern expansion, engines, constraints, custom inflections, `config/routes/*.rb` (`draw :x`) and non-root `config/routes.rb`.
- MCP exposure: no op or enum change, no traversal of `routes_to`, no `edges` rows. No new entity kind and no derived generation.
- Edits to `server.py`, `protocol/*`, `query/*`, `tools/*`, `extractors/*`, `providers/*`, `indexing/*`, `resolvers/*`, `architecture/*`, `counterfactual/*`, `contracts/*`, `importers/*`, `reports/*`, `enforcement.py`, `config.py`, `storage/*` other than `evidence.py`, `pyproject.toml`, `uv.lock` and `.github/workflows`.

Declared paths (issue): `frameworks/rails_routes.py`, `codegraph.py`, `__main__.py`, `tests/slices/test_s24_rails_routes.py`, `tests/fixtures/rails_routes/`, `docs/frameworks/rails.md`.

The scope amendment `.spectra/changes/aci-s24/scope-amendment.json` is recorded before any product edit. It adds `storage/evidence.py`, `frameworks/__init__.py`, `tests/slices/test_s24_*.py`, `CHANGELOG.md` and `README.md`. Rightsize: `lite` (score 4). An independent checker is still run by the orchestrator.

## Approach

### Decisions

**D1 Activation and identity.**
- `CodeGraph.__init__(…, graph_root=None, frameworks: Iterable[str] = ())` adds the parameter last, so positional order is unchanged.
- `self.frameworks = frameworks.normalize(frameworks, self.langs)`. It strips, drops empties, dedupes and sorts the names.
  - An unknown name raises `ValueError("Unknown framework adapter: <name[:64]>")`.
  - `rails` without `ruby` in `langs` raises `ValueError("Framework adapter rails requires language ruby")`.
- CLI: `index --frameworks TEXT` (default `""`, comma-separated, help "opt-in adapters; closed set: rails"). A click callback runs `normalize` and maps `ValueError` to `click.BadParameter`: exit 2, nothing built, no graph directory published.
- Adapter identity has two homes, neither of them in `generation_inputs`:
  - the rails producer fingerprint;
  - the manifest key `rails_routes`, written iff the flag is on and deleted otherwise.
- The generation id still changes whenever rails records exist, because the snapshot id binds them (:1248-1266). An enabled build that emits zero records has a generation id identical to the flag-off build: same facts, same identity.

**D2 Record shapes.** Let `R = "config/routes.rb"` (`ev.RAILS_ROUTES_PATH`) and `F = ev.file_entity(R, "ruby")["id"]`.
- **Producer** (stored with path NULL, like the native producer at :3356; emitted iff ≥1 declaration):
  `ev.producer({"rails": {"adapter": "rails-routes.v1", "adapter_sha256": sha256(rails_routes.py bytes), "graph_producer_id": self._capture_producer["id"], "routes_path": R}})`, with basis `parsed`.
- **Occurrence** (path R): `ev.occurrence(stmt_node, R, "ruby", source, F, "rails_route", role, source_hash)`.
  - The span is the exact statement node.
  - `role` = `rails_route:<verb>` for a supported route, or `rails_route:unsupported:<construct>` for a diagnostic.
- **Evidence** (path R): `ev.provenance(occ, rails_producer_id)`.
- **Assertion** (path R): `ev.assertion(F, target, "routes_to", evidence_id, resolution, raw_target, candidates, reason)`.
- **`raw_target`** is canonical JSON (`ev.canonical`) and closed:
  - route: `{"mapping":"rails_convention.v1","path":<literal>,"target":<literal>,"verb":<verb>}`;
  - diagnostic: `{"construct":<code>,"mapping":"unexpanded"}`.
- **The convention label** is the validated field `mapping == "rails_convention.v1"`, plus resolution INFERRED; a rails mapping is never EXTRACTED, and EV4 enforces that. This puts it on the relation itself, which is what AC-002 names. evidence.py constrains `raw_target` only to a non-empty string. `validate_records` pins its shape.

| Case | resolution | target | candidate_ids | unresolved_reason |
| --- | --- | --- | --- | --- |
| exactly 1 matching method entity | INFERRED | method entity | [] | null |
| ≥2 distinct method entities | AMBIGUOUS | null | sorted ids | `ambiguous_controller_action` |
| controller class exists, no action | UNRESOLVED | null | [] | `action_not_found` |
| no class with that namespace | UNRESOLVED | null | [] | `controller_not_found` |
| unsupported declaration | UNRESOLVED | null | [] | `unsupported_route_construct` |

How AMBIGUOUS satisfies AC-003's "unresolved with candidate identities":
- evidence.py treats every non-EXTRACTED/INFERRED resolution as unresolved: no target and a mandatory reason (:482-488).
- :489 makes AMBIGUOUS the only valid carrier of candidates; UNRESOLVED with candidates is rejected.
- A test pins this rejection.

Manifest key `rails_routes` (canonical JSON):
`{"format":"atlas-rails-routes.v1","adapter":"rails-routes.v1","adapter_sha256":…,"routes_path":R,
"state":"indexed"|"absent"|"failed","declarations":n,"routes":n,"diagnostics":n,
"resolutions":{"AMBIGUOUS":n,"INFERRED":n,"UNRESOLVED":n},"truncated":bool,"producer_id":id|null}`.
- `absent`: there is no observation for R (no file, or it is skipped by skip patterns).
- `failed`: R's observation is not `indexed` (a parse error), or the hash or file entity is missing.

**D3 Closed vocabularies** (module constants; a test pins the exact sets).
- `VERBS = {get, post, put, patch, delete}`.
- `STATEMENT_CONSTRUCTS` (receiverless method name, 20): `collection, concern, concerns, constraints, controller, defaults, devise_for, direct, draw, match, member, mount, namespace, nested, resolve, resource, resources, root, scope, shallow`.
- `VERB_CONSTRUCTS` (10): `route_block, hash_rocket_target, extra_arguments, non_literal_path, interpolated_literal, malformed_literal, unsupported_option, missing_target, non_literal_target, malformed_target`.
- `OTHER_CONSTRUCTS` (4): `conditional, other_statement, unrecognized_draw_receiver, declaration_limit`.
- `REASONS` = {`ambiguous_controller_action`, `action_not_found`, `controller_not_found`, `unsupported_route_construct`}.
- Mapping labels: {`rails_convention.v1`, `unexpanded`}.
- Limits: `MAX_DECLARATIONS = 5000`, `MAX_PATH_BYTES = 512`, `MAX_TARGET_BYTES = 256` (UTF-8 bytes of the literal).
- Allowed options are only `to:` (required) and `as:` (a plain string or `simple_symbol`). Stricter reading:
  - `constraints:`/`defaults:` are runtime-dependent;
  - `format:`/`anchor:` change the effective pattern, so the recorded literal would be wrong;
  - `controller:`/`action:`/`module:`/`path:`/`via:`/`on:`/`shallow:`, string keys and `**splat` change dispatch.
  - All of these give `unsupported_option`.
  - The hash-rocket form gives `hash_rocket_target`, because the issue says "literal `to:`".

**D4 Extraction (`scan(tree, source) -> (declarations, truncated)`; pure; fixed depth, no recursion, no tree walk).**
1. Only the repo-root `config/routes.rb` is scanned. Top level = `tree.root_node.named_children`, skipping `comment`.
   - A statement counts as a draw wrapper only if it is a `call` whose `method` text is `draw`, with a `block` field of type `do_block`/`block`.
   - The wrapper is accepted iff the `receiver` bytes fullmatch `rb"Rails\.application\.routes|[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*::Application\.routes"` and there is no `arguments`.
   - Any other draw-with-block gives `unrecognized_draw_receiver` (for example engine routes). Other top-level statements are ignored and produce no records.
2. The block's statements are the named children of its `body` field (`body_statement`/`block_body`), or of the block itself minus `block_parameters` if the grammar has no body field (Day-1 (a) decides). `comment` is skipped, and nested blocks are never entered.
3. Each statement node `S` is classified in this precedence:
   - `call` without a receiver whose method is in VERBS → verb checks (step 4);
   - `call` without a receiver whose method is in STATEMENT_CONSTRUCTS, or an `identifier` in it → that code;
   - type in {`if`,`unless`,`if_modifier`,`unless_modifier`,`conditional`,`case`} → `conditional`;
   - anything else → `other_statement`.
4. Verb checks, first failure wins:
   1. a `block` field → `route_block`;
   2. any `pair` in `arguments` with a `=>` token → `hash_rocket_target`;
   3. positional named children of `argument_list` ≠ exactly 1, or any `splat_argument`/`hash_splat_argument`/`block_argument`/`hash` → `extra_arguments`;
   4. path literal check (L) failure → `non_literal_path` (not a `string`), `interpolated_literal` or `malformed_literal`;
   5. any pair key that is not a `hash_key_symbol`, a key outside {`to`,`as`}, a duplicate key, or an `as` value that is not a plain string or `simple_symbol` → `unsupported_option`;
   6. no `to` → `missing_target`;
   7. `to` value fails (L) → `non_literal_target`, `interpolated_literal` or `malformed_literal`;
   8. target not fullmatching `TARGET_RE` → `malformed_target`.
5. Literal check (L) on node N:
   - `N.type == "string"`, and its bytes start and end with the same quote (`"` or `'`);
   - every named child is `string_content`; any `interpolation` → `interpolated_literal`;
   - any other child (for example `escape_sequence`), an empty literal, a literal over its byte limit, or invalid UTF-8 → `malformed_literal`.
6. `TARGET_RE = re.compile(r"[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)*#[a-z_][a-z0-9_]*")`, used with `fullmatch` on `str`; no `\d`, `\w` or `$`.
7. After `MAX_DECLARATIONS` declarations, the next statement gets one `declaration_limit` diagnostic, scanning stops and `truncated = true`. That caps the output at 5001 declarations, i.e. 3×5001+1 records.

**D5 `storage/evidence.py` edits (the complete list).**
- **EV1** (:60-65): `RAILS_RELATION = "routes_to"`, `RAILS_ROUTE_KIND = "rails_route"`, `RAILS_ROLE_PREFIX = "rails_route:"`, `RAILS_ADAPTER = "rails-routes.v1"`, `RAILS_ROUTES_PATH = "config/routes.rb"`.
  - `ASSERTION_RELATIONS = REFERENCE_RELATIONS | {COVERAGE_RELATION, RAILS_RELATION}`, with a comment mirroring S21.
  - `RELATIONS`, `REFERENCE_RELATIONS`, `EDGE_RELATIONS`, `FIELDS` and `COLUMNS` are untouched.
- **EV2** (:277-282): `if set(fingerprint) == {"rails"}: _validate_rails_fingerprint(...); return`.
- **EV3** (new, after :715): `_validate_rails_fingerprint(value, basis)`. Basis `parsed`; keys == {adapter, adapter_sha256, graph_producer_id, routes_path}; `adapter == RAILS_ADAPTER`; `routes_path == RAILS_ROUTES_PATH`; both values are hashes.
- **EV4** (:395-424):
  - add `RAILS_ROUTE_KIND` to the kind set;
  - require `(kind == RAILS_ROUTE_KIND) == role.startswith(RAILS_ROLE_PREFIX)`;
  - for a rails kind, require state `source`, a hash `entity_id`, `path == RAILS_ROUTES_PATH` and `language == "ruby"`;
  - for an assertion (after :457), when relation == `RAILS_RELATION`, require resolution ∈ {INFERRED, AMBIGUOUS, UNRESOLVED}.
- **EV5** (`validate_database`):
  - :766: `(kind == RAILS_ROUTE_KIND) <= (entity kind == "file")`;
  - :782: `(fingerprint keys == {"rails"}) == (occurrence kind == RAILS_ROUTE_KIND)`;
  - :800: `(relation == RAILS_RELATION) == (fp == {"rails"}) == (occ kind == RAILS_ROUTE_KIND)`; for rails, `source.kind == "file"` and `source.id == occurrence.entity_id`. The generic source-location rule at :825 still applies.
- **EV6:** nothing else. Deep shape checks live in `rails_routes.validate_records`, as with `coverage_py`.

**D6 `codegraph.py` edits (the complete list).**
- **CG1:** `from atlas_aci import frameworks` and `from atlas_aci.frameworks import rails_routes`. `frameworks` imports only `storage.evidence`, so there is no import cycle.
- **CG2** `__init__` (:874-888): D1.
- **CG3** `_run_build`: `routes_capture: tuple[bytes, str] | None = None` before the loop (~:3162). After :3198: `if rel == ev.RAILS_ROUTES_PATH and lang == "ruby": routes_capture = (source, source_hash)`. This runs before every `continue`, so it covers cache hits and unchanged incremental reuse.
- **CG4:** between :3531 and :3532:
  `rails_summary = rails_routes.refresh(conn, enabled="rails" in self.frameworks, capture=routes_capture, observations=all_observed, get_parser=get_parser, graph_producer_id=self._capture_producer["id"])`.
  - It runs after `_resolve_edges`, so full-mode `DELETE FROM assertions` cannot erase fresh rails facts.
  - It runs before projection validation, `dep.witness` (:3534) and `_snapshot_binding` (:3552).
  - `build()` stats are unchanged; the summary lives only in the manifest key.
- **CG5** (:2567): `dropped = … | coverage_py.record_ids(full) | rails_routes.record_ids(full)`.
- **CG6** (:6386): `or role.startswith(ev.RAILS_ROLE_PREFIX)` → `continue`.
- **CG7** (:6406): `if record["relation"] in {ev.COVERAGE_RELATION, ev.RAILS_RELATION}: continue`.
- **CG8** (:6424): `rails_routes.validate_records(conn)` after `coverage_py.validate_records`.
- **Not edited:** the :6094 SQL (`by_evidence` is read only at :6150 by refs links; rails evidence ids are never cited there, so it is harmless); `_producer_inputs`; `_build_inputs`; the :1132/:1160 shapes; the generation kinds.

`refresh` sequence (single transaction, inside the staging connection):
1. Purge, always, whatever the flag:
   - `DELETE FROM assertions WHERE json_extract(payload,'$.relation')=?` (routes_to);
   - `DELETE FROM evidence WHERE json_extract(payload,'$.producer_id') IN (SELECT id FROM producers WHERE json_extract(payload,'$.fingerprint.rails') IS NOT NULL)`;
   - `DELETE FROM occurrences WHERE json_extract(payload,'$.kind')=?` (rails_route);
   - `DELETE FROM producers WHERE json_extract(payload,'$.fingerprint.rails') IS NOT NULL`;
   - `DELETE FROM manifest WHERE key='rails_routes'`.
2. If the flag is off, return.
3. Find R's observation in `observations`.
   - If there is none: state `absent`.
   - Otherwise, the observation must be `indexed`, the capture hash must equal `observed_source_sha256`, and `entities` must contain F. If any of these fails: state `failed`.
   - In either case: write the manifest key with zero counts and return.
4. Parse: `get_parser("ruby").parse(source)`. An exception propagates as a build failure (the native parse of the same bytes just succeeded). `tree.root_node.has_error` gives state `failed`.
5. `scan` → declarations. `resolve` each supported route (D8).
6. If there is ≥1 declaration, `ev.put` the producer (path None). For each declaration, put the occurrence, evidence and assertion (path R).
7. Write the manifest key.

**D7 Invalidation and the S10 effect (AC-005).**
Rails facts are a derived global function of (R bytes, the Ruby `symbols` table, the adapter). They are rebuilt from scratch on every build. No per-file patching happens, so a controller deleted while R is unchanged cannot leave a dangling `routes_to` (`validate_database` would raise "missing entity link").

Rails ids also join `dropped`. A later parse failure of R never resurrects predecessor rails records through `_retained_closure`, which follows occurrence→evidence→assertion links.

`dep.snapshot` effect:
- Rails assertions carry path R, so R's unit `output` digest includes them when the flag is on.
- The witness is written after `refresh`, so the predecessor witness equals the predecessor snapshot.
- The current snapshot (:3512) is taken before `refresh`:
  - if R and the Ruby bucket are unchanged, the predecessor rails assertions are still present, the output is equal, and R is reused;
  - if R changed, `remove_path` cleared its records, the local digest differs, and R is affected;
  - if a controller changed, the Ruby bucket differs and R is affected.
- Flag toggles: off→on leaves no rails in either digest; on→off leaves rails in both (purged after :3512).
- Conclusion: `unavailable_prior_outputs` cannot be triggered by rails records. An unchanged incremental rebuild stays `affected`/`complete_dependency_match`, exactly as with the flag off.

**D8 Camelize and resolution (convention-derived).**
- `camelize(c) = "::".join("".join(p[:1].upper() + p[1:] for p in seg.split("_")) for seg in c.split("/")) + "Controller"`.
  - For the TARGET_RE alphabet this equals the ActiveSupport default inflection (no acronyms): `"a__b"` → `"AB"`, `"api/v1/widgets"` → `"Api::V1::WidgetsController"`.
- `resolve` (per build, on the staging `conn`):
  - (i) One scan: `SELECT path, binding FROM symbols WHERE lang='ruby' AND kind='class' ORDER BY path, line_start, id` → `classes[namespace] = set(paths)`.
  - (ii) Per distinct action name (cached): `SELECT entity_id, path, binding FROM symbols WHERE lang='ruby' AND kind='method' AND name=? ORDER BY path, line_start, id`.
  - (iii) `Q = camelize(controller)`. If `Q not in classes` → `controller_not_found`.
  - (iv) `ids = sorted({entity_id | binding.owner == "ruby:"+Q, binding.category == "instance", path ∈ classes[Q], entity_id not null})`.
  - (v) 0 ids → `action_not_found`; 1 → INFERRED; ≥2 → AMBIGUOUS.
- Lookup is not restricted to `app/controllers` (decision 7).
- Documented limits:
  - no visibility (`private` actions resolve);
  - no inheritance (an inherited action gives `action_not_found`);
  - a reopened class or a double `def` counts as ≥2 entities and gives AMBIGUOUS;
  - `def self.x` and `class << self` are excluded;
  - acronym namespaces (`API::`) give `controller_not_found`;
  - a module and a class sharing Q in one file are not disambiguated;
  - a controller file that fails to parse yields `controller_not_found`/`action_not_found` for that generation.

**D9 `rails_routes.validate_records(conn)`** (called by CG8 on build, predecessor validation, import and export):
- ≤1 rails producer. Each one is cited by ≥1 evidence, and its `graph_producer_id` exists in `producers`.
- Every `rails_route` occurrence has `entity_id == F` and is cited by exactly one evidence, which is cited by exactly one `routes_to` assertion.
- Every `routes_to` assertion:
  - has `source == F`;
  - has a `raw_target` that is canonical and of a closed shape;
  - has a role/raw_target verb or construct that agrees;
  - has a resolution/reason pair from D2's table;
  - for routes, has verb ∈ VERBS, a target fullmatching `TARGET_RE`, and a path of 1..512 bytes;
  - for diagnostics, has construct ∈ CONSTRUCTS and is UNRESOLVED with `unsupported_route_construct`;
  - has an INFERRED target and AMBIGUOUS candidates (≥2) that are entities with kind `method` and language `ruby`.
- Total declarations ≤ 5001.
- Every failure is `ValueError("Invalid evidence: rails …")`.

**D10 Identity and churn (stated in docs/PR).**
- Editing `codegraph.py` and `evidence.py` changes the native producer id. So every evidence id, assertion id, projection link id (`refs.evidence_id`, `edges.assertion_id`, …), generation id, snapshot id and export byte changes against `2e342a3`, for all graphs, with the flag on or off.
- Existing indexes rebuild in full on the first `--since` run (producer mismatch).
- Entity ids, occurrence ids and the content of the symbols/refs/edges rows (minus producer-derived link ids) are unchanged; G-IMAGE proves this.
- On the same code, flag ON minus rails-owned ids == flag OFF on every record id (the native producer is identical, because the flag is not in `_build_inputs`).

**D11 No execution and portability (AC-007).**
- `frameworks/*.py` import only {`__future__`, `hashlib`, `json`, `re`, `sqlite3`, `dataclasses`, `pathlib`, `typing`, `collections.abc`, `atlas_aci.storage.evidence`, `atlas_aci.frameworks`}.
- Not allowed: `eval`/`exec`/`compile`/`__import__`/`open`. `Path(__file__).read_bytes()` is the only file read, for `adapter_sha256`.
- Not allowed: `os`, `subprocess`, `socket`, `urllib`, `http`, `time`, `datetime`, `random`, `importlib`, `ctypes`, `multiprocessing`, `pty` or `runpy`.
- Source bytes are ASCII-only. No function calls itself (AST check), and no recursion-limit reliance. Regexes use `[0-9]` and fullmatch.
- Repository Ruby is only parsed by tree-sitter, from the bytes already captured by the build.

### Limitations (docs `docs/frameworks/rails.md`)
- Only root `config/routes.rb` and direct statements of recognised draw blocks are read.
- Only `get/post/put/patch/delete` with a literal path and a literal `to: "c#a"`; everything else is an `unsupported_route_construct` diagnostic and is never expanded.
- Mappings are convention-derived (`rails_convention.v1`, INFERRED), not runtime-verified.
- No visibility, inheritance or acronym inflection handling; reopened or duplicate definitions are ambiguous.
- A parse error in routes.rb yields no rails facts (state `failed`) and drops the previous facts.
- Rails facts are not traversable over MCP; inspect them through the evidence export and the `rails_routes` manifest key.
- Producer and generation id churn (D10).
- Rails occurrences count toward impact diff admission (`query/impact.py:151`; at most 5001).

### Work plan (timebox 3 engineering days)
**Day 1 AM — qualification, before any product edit.** Write a scratch probe that runs on the host and in the 3.12 dev image, with outputs in `.spectra/evidence/aci-s24/qualification.json`:
- **(a) Grammar probe.** Parse fixture `config/routes.rb` with the pinned `tree-sitter-language-pack` `get_parser("ruby")`.
  - `has_error` must be false.
  - Record, per statement, the node type and fields: `call`, `receiver`, `method`, `argument_list`, `pair` (key type `hash_key_symbol` vs `string`, presence of the `=>` token), `string` with `string_content`/`interpolation`/`escape_sequence`, `simple_symbol`, `do_block`/`block` with their `body` field (`body_statement`/`block_body`), `if_modifier`, and `comment`.
- **(b) Symbol probe.** Base-code build of the fixture: class `namespace` and method `owner`/`category`/`entity_id` for the top-level, nested-module, compact `Api::V1::`, `API::V1::`, `def self.build` and the two `Legacy::OrdersController` classes.
- **(c) Validity probe** with the base `ev.validate_record`, using relation `call` as a stand-in: INFERRED with target, AMBIGUOUS with 2 sorted candidates and a reason, and UNRESOLVED with a reason all pass; UNRESOLVED with candidates fails.
- **(d) Flag-off baseline.** The G-IMAGE comparison digest of the fixture built by base code (host) → `base-digest.json`.
- **(e) Base invariants:** tools sha and bytes; 3436/3448/797/7730; the full `pytest -q` result at base.

STOP RULE:
- (a) The fixture's supported lines do not parse without error, or literal and interpolated strings cannot be told apart → BLOCKED (report the node dump). Field or name differences only adapt D4 and are recorded.
- (b) Methods lack `owner`/`category`/`entity_id` as described → BLOCKED for AC-002/003 (fixing that would need `extractors/` or codegraph extraction edits outside scope).
- (c) The AMBIGUOUS shape is rejected → BLOCKED (re-plan AC-003).
- (d)/(e) The base is not green → BLOCKED.

**Day 1 PM:** `frameworks/__init__.py` and `rails_routes.py` (scan, camelize, vocabularies, validate). The fixture and its provenance. Pure scan tests.

**Day 2:** EV1-EV6, CG1-CG8, the CLI, and `test_s24_rails_routes.py` (AC-001..007).

**Day 3:** `test_s24_isolation.py`, docs, CHANGELOG/README, gates in the lock-faithful dev image (rebuilt from the branch), the production-image scenario (orchestrator), then RUN-01/02.

Cut order (each cut becomes a documented limitation):
1. README/CHANGELOG depth.
2. Export→import round-trip test.
3. G-NOLEAK MCP call comparison (keep G-MCP and relation-set pins).
4. G-QUERY validation-plan comparison (keep the impact admission arithmetic and change-set equality).
5. The `<Const>::Application.routes` wrapper (keep `Rails.application.routes`).
6. The `as:` option (→ `unsupported_option`).

Never cut: the AC tests, purge+recompute+`dropped`, G-OFF/G-ONOFF/G-IMAGE, EV/CG validation and `validate_records`, the no-exec gates, G-SRC, G-MCP.

### Independent checker conditions (binding; override conflicting plan text)
Checker `atlas-s24-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s24.review.json`); this is the validation required by the confidence verdict VALIDATE (83.75). Every finding below is a binding plan condition and overrides any conflicting text above.

Orchestrator resolutions (recorded 2026-11-04 under the user's autonomous-execution instruction): condition 9 - the purge is gated on the rails producer probe (a rails producer row or the `rails_routes` manifest key), with a test that an ON->OFF incremental build still purges every rails-owned record; condition 12 - the `_validate_evidence_projections` test on a copy of an ON database is NOT cuttable; condition 3 - the honest wording is binding: rails facts are rebuilt globally by the adapter on every enabled build, and S10 contributes per-file `remove_path` and the `affected` binding plan; no text may claim per-file republication through the S10 plan.

1. **MAJOR: the parameter shadows the module.**
   - Evidence: D1 and CG1/CG2 add `frameworks` as a parameter of `CodeGraph.__init__` (codegraph.py:874) and also import `from atlas_aci import frameworks`. Inside `__init__`, `frameworks.normalize(frameworks, …)` is `tuple.normalize`. It raises AttributeError and fails mypy.
   - Condition: import under an alias (`from atlas_aci.frameworks import normalize as normalize_frameworks`, or `import … as framework_adapters`). Never reuse the module name as a local name. Mypy must be clean.

2. **MAJOR: the observation lookup can pick a history record.**
   - Evidence: `all_observed` (:3494) holds `observation` and `history` records. A history record exists for R in AC-005(iii), because `failed_histories` gets R's retained closure at :3266. Sorting by `(type,id)` puts `history` before `observation`. D6 step 3 says "R's observation" without filtering.
   - Condition: filter `type == "observation"` and key by path. AC-005(iii) must assert `state "failed"` while R's history exists.

3. **MAJOR: AC-005 / S10 wording and proof are weaker than the AC text.**
   - Evidence: D7 recomputes globally on every build. S10 only contributes `remove_path` for a changed R (:3312, :3470) and the `dep.plan` affected classification (`dependencies.py:179-237`). The adapter does not "republish that file's facts" through the S10 plan.
   - Condition: the docs, CHANGELOG, PR and receipt must say the rails facts are rebuilt globally by the adapter on every enabled build, not republished by the per-file S10 plan.
   - Condition: the AC-005 test asserts `binding_resolution.mode == "affected"` with `config/routes.rb` in `affected_paths` for (i) and for (ii).
   - Condition: add (v): edit only a controller's declaration lines, with R bytes unchanged. R must be in `affected_paths` (the Ruby bucket digest changed) and the rails set must equal a fresh full build. That is what proves D7's claim.

4. **MAJOR: `validate_records` must not pin the current adapter, and must raise only ValueError.**
   - Evidence: it is called at :2557 (predecessor, which catches only ValueError/KeyError/TypeError), :3532, :6436 and :6739.
   - Condition, hash: neither EV3 nor `validate_records` may compare `adapter_sha256` to the current `rails_routes.py`. Check hash format only. A test must accept a record carrying a different valid hash.
   - Condition, errors: every shape failure raises `ValueError("Invalid evidence: rails …")`. That includes `raw_target` JSON that is a list or non-object, a bad canonical form and a missing key. The planted-corruption tests must include non-object JSON.

5. **MINOR: CLI exit code and option order.**
   - Evidence: `_open_graph` (`__main__.py:33-37`) turns `ValueError` into `ClickException`, exit 1. A click callback cannot reliably see `--langs`, because click processes parameters in command-line order.
   - Condition: call `normalize(names, langs)` in the `index` body before `_open_graph`. Raise `click.UsageError`/`BadParameter` (exit 2). Test both option orders. Create no graph directory.

6. **MINOR: the `as:` value.**
   - Evidence: D3/D4 say "plain string" without applying (L).
   - Condition: an `as:` string passes (L). Otherwise it is `unsupported_option` (interpolated or escaped). Docs must say `as:` is accepted but not recorded in `raw_target`.

7. **MINOR: string literal definition.**
   - Condition: the literal value is the source bytes between the delimiters, not a concatenation of child nodes. Add a test that several `string_content` children (for example around a lone `#`) still pass.
   - Condition: Day-1 (a) records `%q`, `chained_string`, heredoc and single-quoted `#{` shapes.
   - Condition: Day-1 (b) also records the symbol `kind` strings (`class` and `module`).

8. **MINOR: `controller_not_found` can be a false "no class".**
   - Evidence: `_iter_source_files` (:3688) and `config.py:26-48` skip any path component in {storage, log, tmp, build, dist, coverage, vendor/bundle, …}. A controller under `app/controllers/storage/` is never indexed.
   - Condition: the docs and the reason text state "no indexed class", name the skip-pattern and language-disabled cases, and a test pins one such case.

9. **MINOR: purge cost and parse side effects in OFF builds.**
   - Evidence: "purge always" adds three full-table JSON scans (assertions, evidence, occurrences) to every OFF build.
   - Condition: gate the purge on a cheap probe (a rails producer in `producers`, or the `rails_routes` manifest key). Rails occurrences cannot exist without that producer.
   - Condition: `get_parser` is never called when the flag is off, R is absent, or R is failed. Add a counting-parser test in the style of `test_s09_cache_validation.py:430`.
   - Condition: docs say an ON build re-parses routes.rb every build, outside `cache_stats`.

10. **MINOR: AC-007 ban list and spy.**
    - Evidence: the spy design is sound. `snapshots.py:43-54` calls `subprocess.run` per flag and breaks on the first OSError, so there is exactly one launch under the spy. `identity()` falls back to `git_unavailable` or `non_git`, which is harmless.
    - Condition: also ban the names `getattr`, `setattr`, `globals`, `locals`, `vars`, `breakpoint`, `input` and `__builtins__`, and the attribute names `system`, `popen`, `spawn*`, `exec*` and `fork*`.
    - Condition: install the spies through monkeypatch so they are restored in teardown.
    - Condition: the OFF launch-list comparison must run under the same spy.

11. **MINOR: G-IMAGE comparison.**
    - Condition: `compare_graphs.py` lists its exclusions explicitly (`files.indexed_at`, `manifest.generation_id`, `generation_inputs.snapshot` and retrieval tables, or compare retrieval by a candidate-id-free projection). It asserts non-empty row counts per table, so no comparison is vacuous.
    - Condition: record the base, OFF and ON producer ids as evidence of the D10 churn.

12. **MINOR: silent and composed cases.**
    - Condition: docs and a test say a `draw` block wrapped in `module`, `if` or `begin` yields zero facts and no diagnostic.
    - Condition: add one cheap test that `_validate_evidence_projections` passes on a copy of an ON database. That covers the derived-generation carve-outs without a coverage fixture. If it is cut, state the cut.

13. **MINOR: scope and timebox.**
    - No missing paths: `scope.txt` and the amendment cover `frameworks/*`, `__main__.py`, the fixtures, `docs/frameworks/rails.md` and `test_s24_*.py`. `pyproject.toml:60-61` (`packages = ["src/atlas_aci"]`) already packages the new subpackage.
    - Frozen fixtures use only input ids (`test_s37`, `test_export:605`). S21 precedent shows the producer churn is survivable.
    - The three days are tight but credible.
    - Condition: if EV/CG integration is not green at the end of Day 2, cut in the plan's stated order (README/CHANGELOG depth first) and never cut G-OFF, G-ONOFF, G-IMAGE or `validate_records`.

## Stories
- As an agent operator, I want each literal Rails route recorded with its verb, path, target text and exact span, and linked to the controller action by an explicitly convention-labelled relation. Then I can start from an HTTP endpoint without booting Rails.
- As a reviewer, I want every dynamic or unsupported declaration surfaced as a diagnostic, and every ambiguous or missing target left unresolved with candidates or a reason. Then no route mapping is fabricated.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s24.criteria.md` (sha256 `7bb68294a58a4ac246660bf33b6dd407a3994d56b6e8b889ae3a551309ee1173`), verbatim:

```
### AC-S24-001 (event-driven)
GIVEN a supported literal route in an indexed Ruby file
WHEN the Rails adapter indexes that source snapshot
THEN the adapter SHALL emit one route fact containing the literal HTTP verb, path pattern, target text and declaration evidence.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_literal_route_fact

### AC-S24-002 (event-driven)
GIVEN one explicit target resolves to one controller class with the named action
WHEN route binding completes
THEN the adapter SHALL emit a convention-derived routes_to relation to the uniquely matching controller-owned action.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_exact_controller_action_mapping

### AC-S24-003 (unwanted-behavior)
GIVEN two controller/action candidates match the same declared target
WHEN route binding encounters the collision
THEN the adapter SHALL report the route target as unresolved with the candidate identities.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_ambiguous_target_is_unresolved

### AC-S24-004 (unwanted-behavior)
GIVEN a fixture uses interpolation, resources, an engine mount or a runtime-dependent route target
WHEN the adapter encounters that declaration
THEN the adapter SHALL emit an unsupported_route_construct diagnostic for the unexpanded declaration.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_dynamic_route_diagnostic

### AC-S24-005 (event-driven)
GIVEN an indexed route declaration is deleted from the next snapshot
WHEN S10 invalidation republishes that file's adapter facts
THEN the published Rails fact set SHALL exclude the removed declaration's route mapping.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_removed_route_retracts_mapping

### AC-S24-006 (event-driven)
GIVEN identical source snapshot, adapter version and configuration
WHEN the Rails adapter indexes the same inputs twice
THEN the canonical Rails fact set SHALL remain identical after the repeat index.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_route_reindex_is_idempotent

### AC-S24-007 (ubiquitous)
THEN the Rails adapter SHALL derive route facts without executing repository Ruby code.
VERIFY: test: mcp-server/tests/slices/test_s24_rails_routes.py::test_rails_routes_never_execute_application

### AC-S24-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S24-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

Fixture `tests/fixtures/rails_routes/project/` (ASCII; copied to tmp with copytree as in S21's `workspace`; `provenance.json` lists each file's sha256 and byte count). Controllers:
- `app/controllers/users_controller.rb`: `UsersController#index/show`, `private def helper`, `def self.build`.
- `app/controllers/admin/users_controller.rb`: `module Admin; class UsersController` with `#index/#destroy`.
- `app/controllers/api/v1/widgets_controller.rb`: `class Api::V1::WidgetsController` with `#create/#update`.
- `app/controllers/api/v1/ping_controller.rb`: `module API; module V1; class PingController` with `#show`.
- `app/controllers/reports_controller.rb`: `ReportsController#show`.
- `app/controllers/legacy/orders_controller.rb` and `lib/legacy/orders_controller.rb`: both `module Legacy; class OrdersController; def index`.

`config/routes.rb` opens with top-level `File.write("ATLAS_S24_SENTINEL_TOP","executed")` (ignored), then one `Rails.application.routes.draw do` block with the 12 routes and the first 22 diagnostics, then `Blog::Engine.routes.draw do … end`:

| # | Statement | Expected |
| --- | --- | --- |
| R1 | `get "/users", to: "users#index"` | INFERRED UsersController#index |
| R2 | `get "/users/:id", to: "users#show", as: "user"` | INFERRED |
| R3 | `get "/admin/users", to: "admin/users#index"` | INFERRED Admin::UsersController#index (≠ R1 target) |
| R4 | `delete "/admin/users/:id", to: "admin/users#destroy"` | INFERRED |
| R5 | `post "/api/v1/widgets", to: "api/v1/widgets#create"` | INFERRED (compact class) |
| R6 | `patch '/api/v1/widgets/:id', to: 'api/v1/widgets#update'` | INFERRED (single quotes) |
| R7 | `put "/reports/:id", to: "reports#show"` | INFERRED ReportsController#show |
| R8 | `get "/orders", to: "legacy/orders#index"` | AMBIGUOUS, 2 candidates |
| R9 | `get "/missing", to: "ghosts#index"` | UNRESOLVED controller_not_found |
| R10 | `get "/users/export", to: "users#export"` | UNRESOLVED action_not_found |
| R11 | `get "/users/build", to: "users#build"` | action_not_found (static excluded) |
| R12 | `get "/ping", to: "api/v1/ping#show"` | controller_not_found (acronym limit) |

Diagnostics D1-D23:

| # | Statement | Construct code |
| --- | --- | --- |
| D1 | `get "/greet/#{name}", to: "users#index"` | interpolated_literal |
| D2 | `get "/dyn", to: "users##{action_name}"` | interpolated_literal |
| D3 | `resources :photos` | resources |
| D4 | `mount Sidekiq::Web => "/sidekiq"` | mount |
| D5 | `get "/old", to: redirect("/users")` | non_literal_target |
| D6 | `get "/rocket" => "users#index"` | hash_rocket_target |
| D7 | `get "/ctrl", controller: "users", action: "index"` | unsupported_option |
| D8 | `get "/fmt", to: "users#index", format: false` | unsupported_option |
| D9 | `get "/bad", to: "Users#Index"` | malformed_target |
| D10 | `get :sym_path, to: "users#index"` | non_literal_path |
| D11 | `get "/implicit"` | missing_target |
| D12 | `get "/blk", to: "users#index" do end` | route_block |
| D13 | `get "/a", "/b", to: "users#index"` | extra_arguments |
| D14 | `namespace :admin do get "/stats", to: "stats#index" end` | namespace |
| D15 | `scope "/v2" do … end` | scope |
| D16 | `constraints(subdomain: "api") do … end` | constraints |
| D17 | `match "/legacy", to: "users#index", via: [:get, :post]` | match |
| D18 | `root to: "users#index"` | root |
| D19 | `get "/cond", to: "users#index" if ENV["FEATURE"]` | conditional |
| D20 | `system("touch ATLAS_S24_SENTINEL_DRAW")` | other_statement |
| D21 | `draw :extra` | draw |
| D22 | `concern :commentable do end` | concern |
| D23 | `Blog::Engine.routes.draw do get "/e", to: "users#index" end` (top level) | unrecognized_draw_receiver |

Totals: 35 declarations; 106 rails records; resolutions INFERRED 7, AMBIGUOUS 1, UNRESOLVED 27.

All AC tests use real builds (`CodeGraph(repo, graph_root=<external>, frameworks=("rails",))`). Every graph root is external, and roots that are compared share the same repo path. Every `all(...)` is preceded by a non-empty assertion. There are no mocks of the behaviour under test. Expected ids come from independent SQL on `symbols`.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_rails_literal_route_fact | Exactly one `rails_route:get` occurrence whose assertion's `raw_target` == canonical `{"mapping":"rails_convention.v1","path":"/users","target":"users#index","verb":"get"}`. `routes_rb[start_byte:end_byte] == b'get "/users", to: "users#index"'`; `line` = the fixture line; `source_sha256` == sha256(routes.rb); `entity_id` == F. The evidence spans equal the occurrence's; the producer has fingerprint key `rails`. Across the graph: the (verb, path, target) set == R1-R12 exactly; one occurrence per declaration (35 distinct spans); every verb appears. |
| 002 | test_rails_exact_controller_action_mapping | R1: `relation routes_to`, `resolution INFERRED`, `target_entity_id` == the `symbols.entity_id` of `index` in `app/controllers/users_controller.rb` with owner `ruby:UsersController`, `candidate_ids []`, reason null, `mapping "rails_convention.v1"`. R3's target is Admin's `index` (≠ R1). R5's target is the compact class. Over all 7 INFERRED records, the target entities are kind `method`, language `ruby`. `ev.validate_record` rejects R1 relabelled EXTRACTED. `validate_records` on a copied DB rejects R1 with `mapping` removed. |
| 003 | test_rails_ambiguous_target_is_unresolved | R8: AMBIGUOUS, target null, reason `ambiguous_controller_action`, `candidate_ids` == sorted({index in `app/controllers/legacy/orders_controller.rb`, index in `lib/legacy/orders_controller.rb`}) (len 2). No `routes_to` INFERRED to either. `ev.validate_record` rejects the same record with resolution UNRESOLVED (candidates kept), which documents why AMBIGUOUS is the unresolved encoding. |
| 004 | test_rails_dynamic_route_diagnostic | For D1-D23: role `rails_route:unsupported:<code>` per the table; the span equals the exact statement bytes; the assertion is UNRESOLVED / `unsupported_route_construct` / `raw_target {"construct":code,"mapping":"unexpanded"}`; target null; no candidates. Interpolation (D1/D2), resources (D3), engine mount (D4), runtime target (D5) and engine draw (D23) are explicitly present. No route record has path `/stats`, `/e`, `/x`, `/c`, `/legacy` or `/greet/…` (no expansion). Manifest counts are `routes 12`, `diagnostics 23`, `truncated false`. |
| 005 | test_rails_removed_route_retracts_mapping | G0 (on). (i) Delete line R2, then `build(since="x")`: the R2 tuple is absent, the other 11 route tuples (verb, path, target, resolution, target, candidates, reason) are unchanged, and the rails record set == a fresh full build of the edited repo. (ii) Delete `reports_controller.rb` with routes.rb unchanged, then `build(since="x")`: R7 becomes UNRESOLVED `controller_not_found` with target null; `_validate_evidence_projections` passes on the lease; no target or candidate id is missing from `entities`; result == a fresh full build. (iii) Break routes.rb syntax, then `build(since)`: zero rails records, manifest `state "failed"`, and R's history record contains no rails id. (iv) `build(since)` with `frameworks=()`: zero rails records and no manifest key. |
| 006 | test_rails_route_reindex_is_idempotent | Two full ON builds into fresh roots, plus `build(since="x")` on the first with no change: the rails records (canonical JSON lines sorted by id) and the `rails_routes` value are byte-identical. `adapter_sha256` == sha256(`rails_routes.py`); `graph_producer_id` == the native producer id. Unchanged rebuild `binding_resolution`: `mode "affected"`, `reason "complete_dependency_match"`, `affected_units 0` (as with OFF). Non-vacuous control: editing R1's literal changes the set. |
| 007 | test_rails_routes_never_execute_application | Spy installed after copytree: `subprocess.Popen` → record argv, then raise `FileNotFoundError`. `os.{system,popen,fork,forkpty,exec*,spawn*,posix_spawn,posix_spawnp}` and `asyncio.create_subprocess_{exec,shell}` → AssertionError. Liveness: `subprocess.run(["true"])` is recorded once and then reset. ON build: success, ≥7 INFERRED. Every recorded argv starts `["git","-C"]` and contains `rev-parse` (the pre-existing `snap.identity`). The launch list == an OFF build under the same spy. No argv token contains `ruby`, `rails`, `rake`, `bundle` or `routes.rb`. Sentinels `ATLAS_S24_SENTINEL_{TOP,DRAW}` are absent; the repo SHA-256 inventory is unchanged. A pure `scan` + `refresh` on a staging copy under an all-raising spy records 0 launches. D11 AST and ASCII scan of `frameworks/*.py`. |
| RUN-01 | gate | SHA-256 manifest of declared tracked implementation files equal before/after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` + `criteria_sha256` equal in interrupted and resumed receipts; one authoritative receipt `.spectra/state/aci-s24.execution.json`. |

Supporting tests:
- **`test_s24_rails_routes.py`:**
  - Construct table: inline snippets through `scan` cover every code in the 34-element set that the fixture does not (`resource, member, collection, nested, shallow, controller, devise_for, direct, resolve, concerns, defaults, malformed_literal, declaration_limit` with `MAX_DECLARATIONS` monkeypatched to 2).
  - `camelize` table.
  - Exact vocabulary sets (VERBS, CONSTRUCTS, REASONS, labels).
  - CLI: `--frameworks rails` gives facts; `rails,rails` is normalized; `django` → exit 2 with "Unknown framework adapter" and no graph DB created; `--frameworks rails --langs python` → exit 2; the default gives none.
  - Planted corruptions on a copied DB each raise: role/kind mismatch, extra fingerprint key, source ≠ F, construct outside the vocabulary, INFERRED to a class entity, an orphan rails occurrence.
- **`test_s24_isolation.py`:**
  - **G-OFF:** a build without the flag has zero records with fingerprint key `rails`, kind `rails_route` or relation `routes_to`, and no `rails_routes` manifest key. A fixture without routes.rb gives byte-identical `generation_inputs` ON vs OFF.
  - **G-ONOFF:** for the same repo path, `ids(ON) − rails_routes.record_ids(ON) == ids(OFF)` for every record type. The projection records (symbols, refs, edges, rationale, import_bindings, assignment_targets) are equal. `generation_inputs` are equal except `snapshot.id`/`manifest_sha256`. `retrieval.document_digest` is equal.
  - **G-ENVELOPE:** 3436/3448/797/7730. `ev.RELATIONS` is the base 8-set; REFERENCE/EDGE are as at base; `routes_to` is only in `ASSERTION_RELATIONS` and not in `KNOWN_EDGE_RELATIONS`.
  - **G-MCP:** 7 tools; tools JSON sha/bytes pinned; `index` is in `cli.commands`.
  - **G-NOLEAK:** MCP neighbors/context from F and from R1's target are equal ON vs OFF after scrubbing ids, and `routes_to` is absent from the response text.
  - **G-QUERY:** an ON pair vs an OFF pair (edit `UsersController#index` body). `impact` change records (category, entity_id, path) are equal. `_admit_diff` occurrence counts ON == OFF + 35 per side. Impacted entity-id sequences are equal. The validation plan's `spans`, `recommendations` and `gaps`, scrubbed of generation/snapshot/plan ids, are equal (validation.py:360 only probes Python test paths; routes.rb is never loaded).
  - S10: an incremental ON→OFF→ON toggle stays `affected`.
  - Export→import round trip keeps the rails records and validates.

Gates beyond the ACs:
- **G-SRC:**
  - `git diff --name-only 2e342a3 -- mcp-server/src` ⊆ {`atlas_aci/codegraph.py`, `atlas_aci/__main__.py`, `atlas_aci/storage/evidence.py`, `atlas_aci/frameworks/__init__.py`, `atlas_aci/frameworks/rails_routes.py`}.
  - `git diff --exit-code 2e342a3 -- mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows`.
  - `git diff 2e342a3 -- …/storage/evidence.py` touches only EV1-EV6.
- **G-MCP:** `atlas-aci tools` sha `94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56`, 1631472 bytes.
- **Regression set unchanged:** `test_s07_*`, `test_s09_*`, `test_s10_*`, `test_s12_storage`, `test_s16_*`, `test_s19_*`, `test_s20_*`, `test_s21_*`, `test_s22_*`, `test_s37_*`, `tests/test_thesis_negatives.py`, `tests/test_export.py`, `tests/test_schema_epoch.py`. The full suite has no new skips relative to base.
- **G-IMAGE** (orchestrator):
  1. Build the image from `2e342a3` (base) and from the branch (new). Run each with `docker run --network none`, the fixture project mounted `:ro` at `/repo`, and graph roots on separate tmpfs/volumes:
     - base: `index --repo /repo --graph-root /g`;
     - new: the same without the flag (OFF), and with `--frameworks rails` (ON);
     - new: `--frameworks django` → exit 2.
  2. Run the comparison with the host's `python -I .spectra/evidence/aci-s24/compare_graphs.py base.db off.db on.db` (stdlib sqlite3 only). Required:
     - entity id sets: base == OFF == ON;
     - occurrence id sets: base == OFF == ON − {kind `rails_route`};
     - symbols rows `(name, kind, path, line_start, line_end, lang, binding, entity_id, occurrence_id)`: equal multisets across all three;
     - refs rows (all `COLUMNS["refs"]` + `source_entity_id`, `occurrence_id`; `evidence_id` excluded): equal;
     - edges rows (`COLUMNS["edges"]`; `assertion_id` excluded): equal;
     - assertions normalized `(source, target, relation, resolution, candidates, raw_target, reason, evidence.occurrence_id)`: base == OFF == ON − {routes_to};
     - OFF vs ON: every evidence/assertion/producer id equal except rails-owned ones;
     - producers: base and OFF each have exactly one native producer (ids differ: the expected D10 churn, both recorded); ON = OFF's + 1 rails producer;
     - manifest `rails_routes` absent in base and OFF, present in ON.
  3. `atlas-aci tools` in the image matches the pin, and the mount inventory is unchanged.
  4. Evidence goes in `.spectra/evidence/aci-s24/image-compare.json`. Also run it on `tests/fixtures/coverage/project` (Python, no routes) → ON == OFF fully.

```sh
cd mcp-server
uv run pytest tests/slices/test_s24_rails_routes.py -q
uv run pytest tests/slices/test_s24_*.py tests/slices/test_s07_*.py tests/slices/test_s09_*.py tests/slices/test_s10_*.py tests/slices/test_s12_storage.py tests/slices/test_s16_*.py tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/slices/test_s21_*.py tests/slices/test_s22_*.py tests/slices/test_s37_*.py tests/test_thesis_negatives.py tests/test_export.py tests/test_schema_epoch.py -q
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/
uv run --frozen mypy src/
# if an offline 3.14 interpreter is available: uv run --python 3.14 pytest tests/slices/test_s24_*.py -q
```

## Confidence
To be computed with `ramza-score`. Most of the design follows existing patterns:
- the S21 precedent: a closed-set amendment, an opt-in producer, and `record_ids`/`validate_records`;
- the S10 analysis showing that the witness ordering is coherent;
- the Ruby binding facts already persisted.

The residual uncertainty is grammar shape (Day-1 (a)) and closed-set misses in the validation layers. Both are gated by Day-1 checks with a STOP RULE, the planted-corruption tests and the predecessor-validation path exercised by AC-005.

## Rejected Alternatives
- **B: a derived `rails_import` generation.** It needs a new kind at ~8 kind-switch sites, chaining rules and a publish path, and it duplicates S21 machinery for a build-time fact.
- **C: a sidecar store.** It violates "shared fact store" and bypasses snapshot binding.
- **`routes_to` in `RELATIONS`/`EDGE_RELATIONS` with `edges` rows.** It changes MCP enums, the tools JSON and envelopes, and makes routes traversable.
- **A new `rails_route` entity kind.** It causes knock-ons in retrieval, indexed, diff and impact (S21 needed four extra files for this).
- **Hashing `frameworks/rails_routes.py` into `_producer_inputs`.** It breaks the closed module sets (evidence.py:316-367, protocol/graph.py) and `test_exact_producer_module_shapes`.
- **The flag in `configuration`.** That shape is closed (:1160, protocol) and would also churn native ids with the flag ON.
- **UNRESOLVED with candidates.** Rejected by evidence.py:489.
- **Per-file incremental patching of rails facts.** It leaves cross-file dangling targets when a controller changes.
- **Accepting hash-rocket, `constraints:`, `defaults:` or `format:`.** The issue requires literal `to:`; these options are runtime-dependent or change the pattern.
- **Restricting lookup to `app/controllers`.** It hides real collisions; decision 7.

## Risks (ranked)
1. **A closed-set or validation miss** in EV/CG/`validate_records` makes an enabled build fail to stage, which turns the whole graph invalid. Mitigation: planted corruptions, ON builds through full/incremental/import/export paths, and the full suite.
2. **Grammar shape drift** in the pinned `tree-sitter-language-pack` (block body field, string children). Mitigation: the Day-1 (a) probe on host and image; D4 adapts by recorded node names only.
3. **Producer-id churn** against `2e342a3` for all graphs: accepted (D10). Mitigation: stated in docs/CHANGELOG/PR; G-IMAGE proves that entities, occurrences and rows are unchanged.
4. **Convention false positives or negatives** (private, inherited, reopened, acronyms). Mitigation: INFERRED plus the `rails_convention.v1` label and documented limits.
5. **S10 interplay:** the witness includes R's rails assertions. Mitigation: the D7 analysis and tests (unchanged rebuild stays `affected`; flag toggles); the worst case is the safe full fallback.
6. **Parse-error routes.rb drops facts:** documented, tested in AC-005 (iii).
7. **Impact admission counts include ≤5001 rails occurrences:** conservative, quantified in G-QUERY.
8. **The 3-day timebox:** the cut order above. At the limit, report completed checks and a narrow follow-up; never expand silently.
