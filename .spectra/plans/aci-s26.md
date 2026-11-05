---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T20:00:00Z
slice_id: ACI-S26
issue: https://github.com/Rynaro/atlas-aci/issues/47
---
# ACI-S26 — Rails: explain direct job enqueues and positional argument paths

## Scope

Implement issue #47 on base `0194758` (main after PR #93), branch `aci-s26`.
- Merged on the base: S01–S25, S29, S31, S32, S37.
- The live marker `<!-- atlas-aci:slice:ACI-S26 -->` is verified. There is no prior S26 receipt or execution state.
- The criteria bytes hash to `6b34e5cf…efdfe`, which equals the contract table: 7 product ACs plus RUN-01/02.
- **Dependency ACI-S24 is PRESENT.**
  - `.spectra/state/aci-s24.execution.json` is COMPLETE.
  - The base has `frameworks/{__init__,rails_routes}.py`, the codegraph/evidence hooks and `index --frameworks`. S25 is also COMPLETE.
- Rightsize is `lite` (score 4). The orchestrator still runs an independent checker.

Observed facts (line numbers on `0194758`):
- **`frameworks/__init__.py`:** :16 `REGISTRY = frozenset({"rails","rails_models"})`; :18 `REQUIRED_LANGUAGE`. `__main__.py:186-187` builds the `--frameworks` help from `REGISTRY`.
- **`storage/evidence.py`:**
  - :45-58 holds RELATIONS, REFERENCE and EDGE. :93-95 `ASSERTION_RELATIONS` is the only gate on assertion relations (:521).
  - Producer branches are at :307-320. `_validate_rails_models_fingerprint` is at :830-845.
  - Occurrence kinds are a closed set at :432-444. Kind↔prefix bijections are at :459-482.
  - Relation resolution checks are at :528-544. :562-582 sets resolved ⇒ target (with the S25 target-less carve-out only).
  - `validate_database` is at :886:
    - :901-904 the rails_model entity kind;
    - :927-931 evidence discrimination;
    - :964-977 assertion discrimination;
    - :995-1000 the generic rule that the source location equals the occurrence location.
  - Builders: `occurrence` :1114 (reads only start/end byte and point), `provenance` :1143, `assertion` :1161.
- **`codegraph.py`:**
  - :36 imports; :2580-2585 `dropped`.
  - :3179-3183 the capture locals; :3225-3228 captures happen before every `continue`.
  - :3559 `_resolve_edges`; :3563/:3571 the routes and models `refresh`; :3580 `_validate_evidence_projections`.
  - Projection check: :6430 every non-file entity needs a symbols projection; :6434-6436 role skip; :6456-6459 relation skip; :6479-6480 the adapter `validate_records`.
- **Ruby refs:**
  - The QUERIES pattern `(call receiver: (_)? @receiver method: (identifier) @callee) @ref.call` (:181) produces one `refs` row per call.
  - That row holds `callee_name`, `relation='call'`, `line`/`col` of the method identifier, and `binding` with `namespace`, `owner`, `category` and `byte` (:4095-4247).
  - It also carries `source_entity_id = owner(node)`: the nearest enclosing class/module/method entity, else the file entity. Under an uncaptured scope_resolution wrapper it is the file entity (:3800-3828).
  - `occurrence_id` points at a `reference:call` occurrence whose start/end byte span covers the identifier (:3979-4005).
  - The `idx_refs_callee` index exists (:402). Ruby `method`/`singleton_method` declarations are entities of kind `method` (:175-180). Parameters are not entities.
- **Consumers:** traversal and `query/indexed.py` filter by `ev.RELATIONS`. `query/impact.py:150-171` counts all occurrences against `diff_occurrence_limit`.
- **Pins:**
  - tools JSON sha `94f9567a…ac56`, 1631472 bytes; 3436 / 3448 / 797 / 7730.
  - `test_s24_rails_routes.py:381,418` and `test_s25_rails_models.py:645,687` pin the frameworks file set and `reads == 2`.
  - `test_s25_isolation.py:231-239` pins `ASSERTION_RELATIONS` exactly.
  - :244-267 is parametrized over `ASSERTION_RELATIONS` and needs no edit when no target-less carve-out is added.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)
1. **Layout.**
   - New adapter `rails_jobs` in the registry, requiring ruby.
   - Producer fingerprint key `rails_jobs` `{adapter:"rails-jobs.v1", adapter_sha256, graph_producer_id}`; manifest key `rails_jobs`, format `atlas-rails-jobs.v1`.
   - Occurrence kind `rails_job`, role prefix `rails_job:`.
   - `rails_routes.py` and `rails_models.py` are NOT edited; helpers are copied, not imported.
   - It can be replaced independently of the other two adapters. Tests cover jobs alone, jobs+routes+models, and that toggling jobs leaves the others byte-identical.
2. **Records.**
   - Two relations, only in `ASSERTION_RELATIONS`:
     - `async_enqueue` → the `perform` method entity (INFERRED / AMBIGUOUS / UNRESOLVED);
     - `async_argument` → the same perform method (INFERRED / UNRESOLVED).
   - No parameter entities, no QUERIES edit, no target-less carve-out.
   - The source is the `refs.source_entity_id` owner. No `call` edge to perform is created; AC-003 asserts this.
3. **AC-004, stricter reading.**
   - Dynamic receiver, set chain or delayed enqueue: the `async_enqueue` itself is UNRESOLVED `unsupported_argument_mapping`, with a construct code.
   - Splat, hash splat, keywords, block, block argument, forwarded arguments, incompatible arity or an unsupported signature on a resolvable receiver:
     - the enqueue keeps its resolution, and its raw_target carries `"arguments":"unsupported"` plus the construct;
     - NO mapping records are emitted (no partial mappings);
     - exactly one UNRESOLVED `async_argument` diagnostic with `unsupported_argument_mapping` references the enqueue occurrence.
   - A supported enqueue carries `"arguments":"mapped"`, or `"none"` for zero arguments.
4. **Syntax.**
   - Direct constant receivers only.
   - `perform_async` requires a Sidekiq class: a direct `include Sidekiq::Worker|Sidekiq::Job`, with an optional leading `::`.
   - `perform_later` requires an ActiveJob class: a direct superclass `ApplicationJob`, `ActiveJob::Base` or `::ActiveJob::Base`.
   - Only an explicit instance `perform` declared directly in the class counts.
   - Fixed positional parameters are the leading identifier/optional run; compatible arity is R ≤ A ≤ R+O.
   - `perform_in`/`perform_at` → `delayed_enqueue`; `.set(...)` → `set_chain`.
   - `perform_now`, `perform_sync`, `perform_inline`, `.new.perform` and `delay` are silent.
   - Values are never evaluated or copied; only spans are recorded.
5. **Resolution.**
   - S25 existence semantics over all class and module namespaces, plus the unbound rule.
   - Exactly one existing name with exactly one recognised perform → INFERRED.
   - ≥2 performs → AMBIGUOUS `ambiguous_job_target`.
   - Otherwise `job_class_not_found` / `perform_not_found` / `target_not_recognized_job`.
   - Atlas does not evaluate Ruby constant precedence.
6. **Discovery.**
   - Flag-gated bounded capture of only the files that match a byte regex.
   - Sites are found through refs callee rows plus a span lookup in a per-file parse. Trees are released per file and only plain spans are kept.
   - Caps: MAX_FILES / MAX_FILE_BYTES / MAX_CAPTURE_BYTES / MAX_SITES / MAX_ARGUMENTS.
   - All indexed Ruby files are in scope, specs included.
   - A Day-1 probe decides refs versus an iterative-walk fallback (STOP RULE).
7. **Invalidation and identity.**
   - The S24/S25 probe-gated global purge plus recompute; ids join `dropped`; ON→OFF purges.
   - AC-006 compares two fresh builds plus an incremental build.
8. **AC-007.**
   - The S25 spies, plus `redis`/`sidekiq` absent from `sys.modules`, sentinel side-effect workers, an unchanged repo inventory and an AST ban.
   - The module is ASCII and iterative, with no `\d`, `\w` or `$`.
9. **Scope amendment** (JSON below). `__main__.py` is NOT edited: the `--frameworks` help is registry-driven.
10. **Invariants** are as in G-SRC, G-MCP and G-ENVELOPE. Producer churn is accepted (S16/S21/S24/S25 precedent).

### In
1. `frameworks/rails_jobs.py` (new):
   - vocabularies, `Capture`, `scan_file`, `signature`, `call_arguments`, `nesting`, `Resolver`;
   - `refresh`, `record_ids`, `has_records`, `purge`, `validate_records`.
2. `frameworks/__init__.py`: the registry and `REQUIRED_LANGUAGE`.
3. Edits CG1-CG8 in `codegraph.py` and EV1-EV5 in `storage/evidence.py`.
4. Fixture `tests/fixtures/rails_jobs/{project/**,provenance.json}`.
5. Tests:
   - `test_s26_rails_jobs.py` (the ACs), `test_s26_isolation.py`, `test_s26_support.py`;
   - the exact prior-slice hunks listed in G-SRC.
6. Docs and evidence:
   - a section in `docs/frameworks/rails.md`, a CHANGELOG `[Unreleased]` entry and a README pointer;
   - `.spectra/evidence/aci-s26/{compare_graphs.py,g_image.sh,qual_*.py,…}`.

### Out
- Redis, Sidekiq or ActiveJob runtime; enqueue execution; scheduler inspection; app boot.
- Delayed/set chains, dynamic receivers, splats and keyword transformations (diagnostics only).
- GlobalID/JSON serialisation, middleware, retries, proof of execution, interprocedural dataflow.
- Inherited `perform`; custom base classes and mixins (`ApplicationWorker`, `< BaseJob`); `perform_bulk`; `.rake` files (not indexed).
- Ruby ancestor-chain constant lookup.
- MCP exposure: no op, enum, edges, entity-kind or generation-kind change.
- Edits to G-SRC gated paths, including `rails_routes.py`, `rails_models.py` and `__main__.py`.

## Approach

### Decisions

**D1 Activation and identity.**
- `REGISTRY = frozenset({"rails","rails_jobs","rails_models"})`, `REQUIRED_LANGUAGE["rails_jobs"]="ruby"`, and the module docstring names S26.
- `CodeGraph.__init__` is unchanged: `frameworks=("rails_jobs",)` and `index --frameworks rails_jobs` work through `normalize`.
- Identity lives only in the `rails_jobs` producer fingerprint and the manifest:
  - `adapter_sha256 = sha256(rails_jobs.py bytes)`, the module's only `read_bytes`;
  - nothing goes into `configuration`, `generation_inputs` or `_producer_inputs`.

**D2 Record shapes.**
- **Producer** (path NULL, emitted iff ≥1 record):
  `ev.producer({"rails_jobs":{"adapter":"rails-jobs.v1","adapter_sha256":h,"graph_producer_id":self._capture_producer["id"]}})`, basis `parsed`.
- **Occurrence:** `ev.occurrence(span, P, "ruby", source, S, "rails_job", role, source_hash)`.
  - P is the enqueue file.
  - S is the site's `refs.source_entity_id` (method/class/module/file entity in P), so occurrence `entity_id == assertion source`.
- **Evidence** `ev.provenance(occ, producer_id)` and **assertion** `ev.assertion(S, target, relation, evidence_id, resolution, raw, candidates, reason)` are both stored with path P.
- Enqueue span: the whole `call` node. Mapped-argument span: the argument expression node. The perform-definition span is the target entity's existing symbols definition occurrence; it is not duplicated.

Roles and canonical `raw_target` (`ev.canonical`; closed key sets):

| Role | Relation | raw_target |
| --- | --- | --- |
| `rails_job:enqueue:<kind>` (kind ∈ perform_async, perform_later) | async_enqueue | `{"arguments":a,"arity":A,"kind":k,"mapping":"rails_job_receiver.v1","mechanism":m,"receiver":R}`, plus `"construct":c` iff `a=="unsupported"` |
| `rails_job:enqueue:unsupported:<c>` | async_enqueue | `{"construct":c,"kind":<site method>,"mapping":"unexpanded"}` |
| `rails_job:argument:mapped` | async_argument | `{"arg":i,"enqueue":<enqueue occurrence id>,"mapping":"rails_job_positional.v1","param":i,"param_name":n}` |
| `rails_job:argument:unsupported:<c>` | async_argument | `{"construct":c,"enqueue":<enqueue occurrence id>,"mapping":"unexpanded"}` |

Field rules:
- m = `sidekiq` for perform_async and `active_job` for perform_later.
- R is the receiver text as written; it is a constant name, not a value.
- A = the positional-plus-unsupported child count of `argument_list` (minus `comment`/`heredoc_body`); 0 if there is none.
- The `enqueue` field holds the occurrence id. It is content-derived and does not depend on the producer.

| Case | resolution | target | candidates | reason | `arguments` |
| --- | --- | --- | --- | --- | --- |
| enqueue: one existing name, one recognised perform | INFERRED | perform method | [] | null | mapped / none / unsupported |
| enqueue: ≥2 recognised performs | AMBIGUOUS | null | sorted perform ids | `ambiguous_job_target` | unbound / unsupported (call-side codes only) |
| enqueue: no existing name | UNRESOLVED | null | [] | `job_class_not_found` | unbound / unsupported (call-side) |
| enqueue: one existing name, recognised for m, no explicit perform | UNRESOLVED | null | [] | `perform_not_found` | same |
| enqueue: anything else (non-job, mechanism mismatch, shadowing names) | UNRESOLVED | null | [] | `target_not_recognized_job` | same |
| enqueue diagnostic | UNRESOLVED | null | [] | `unsupported_argument_mapping` | n/a |
| argument mapped (enqueue INFERRED and mapped) | INFERRED | the enqueue's target | [] | null | n/a |
| argument diagnostic (exactly one per enqueue whose arguments == unsupported) | UNRESOLVED | null | [] | `unsupported_argument_mapping` | n/a |

- Neither relation is ever EXTRACTED; `async_argument` is never AMBIGUOUS.
- Manifest key `rails_jobs` (canonical; written iff the flag is on):
  `{"format":"atlas-rails-jobs.v1","adapter":"rails-jobs.v1","adapter_sha256":h,"files":{"captured":n,"scanned":n,"failed":n,"limited":n},"classes":{"jobs":n,"unbound":n},"performs":n,"sites":{"located":n,"unlocated":n,"skipped":n,"limited":n},"facts":{"async_argument":{"INFERRED":n,"UNRESOLVED":n},"async_enqueue":{"AMBIGUOUS":n,"INFERRED":n,"UNRESOLVED":n}},"truncated":bool,"producer_id":id|null}`.

**D3 Closed vocabularies and limits** (module constants; a test pins the exact sets).
- Methods:
  - `ENQUEUE_METHODS={"perform_async":"sidekiq","perform_later":"active_job"}`; `DELAYED_METHODS={perform_in, perform_at}`; `SITE_METHODS` = their union (4).
  - `MECHANISMS={sidekiq, active_job}`.
- Recognition:
  - `SIDEKIQ_MIXINS={b"Sidekiq::Worker",b"Sidekiq::Job",b"::Sidekiq::Worker",b"::Sidekiq::Job"}`;
  - `ACTIVE_JOB_BASES={b"ApplicationJob",b"ActiveJob::Base",b"::ActiveJob::Base"}`.
- `ENQUEUE_CONSTRUCTS` (5): `dynamic_receiver, set_chain, delayed_enqueue, unbound_namespace, site_limit`.
- `CALL_ARGUMENT_CONSTRUCTS` (6, in precedence order): `forwarded_arguments, splat, hash_splat, keyword_arguments, block_argument, block`.
- `ARGUMENT_CONSTRUCTS` (9): the call-side codes plus `argument_limit, unsupported_signature, arity_mismatch`. Non-INFERRED enqueues may carry only the call-side codes plus `argument_limit`.
- `REASONS` (5): `job_class_not_found, perform_not_found, target_not_recognized_job, ambiguous_job_target, unsupported_argument_mapping`.
- `ARGUMENT_STATES={mapped, none, unsupported, unbound}`. Labels: `{rails_job_receiver.v1, rails_job_positional.v1, unexpanded}`.
- Limits:
  - `MAX_FILES=10000`, `MAX_FILE_BYTES=1048576`, `MAX_CAPTURE_BYTES=67108864`;
  - `MAX_SITES=5000`, `MAX_ARGUMENTS=16`;
  - `MAX_RECEIVER_BYTES=256`, `MAX_PARAM_NAME_BYTES=128`.
  - Validator cap: assertions ≤ `(MAX_SITES+1)*(1+MAX_ARGUMENTS)`, derived from the same constants.
- Regexes (ASCII, `fullmatch` unless noted):
  - `CLASS_NAME_RE=r"(::)?[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"`
  - `JOB_KEY_RE=r"[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"`
  - `PARAM_RE=r"[a-z_][A-Za-z0-9_]*"`
  - `CAPTURE_RE=rb"perform_async|perform_later|perform_in|perform_at|Sidekiq::Worker|Sidekiq::Job|ApplicationJob|ActiveJob::Base|def[ \t]+perform"`, used with `search`. Over-capture (`perform_inline`, `def perform_now`) is harmless and bounded. There is no `\b`, `\d`, `\w` or `$`.

**D4 Extraction.** Pure and iterative, with no recursion. Every result is plain data (`Span` = start/end byte plus start/end point, copied from S25). The tree is deleted after each file.

D4.1 Classes (`_classes` copied from S25):
- An explicit stack seeded with root named children; only `module`/`class` bodies are expanded.
- Per class node, record:
  - its span;
  - `compact` = the name node is `scope_resolution` and the class has ≥1 enclosing class/module;
  - `mechanisms`:
    - `sidekiq` if a direct body statement is a receiverless `call` named `include` with an argument whose bytes ∈ SIDEKIQ_MIXINS;
    - `active_job` if the superclass text ∈ ACTIVE_JOB_BASES.
  - `performs`: direct body `method` nodes whose name is the identifier `perform`. Each has its span and a `signature(method)` (D4.3).
- `singleton_method` and `class << self` contents are never read.

D4.2 Sites. For this path, the refs rows are those located by D6 step 4, and each yields a `call` node.
- **Receiver classification**, first match wins:
  1. Method ∈ DELAYED_METHODS → `delayed_enqueue`.
  2. No receiver → `dynamic_receiver`.
  3. The receiver is a `call` with method `set` whose own receiver is a `constant`/`scope_resolution` fullmatching CLASS_NAME_RE → `set_chain`.
  4. The receiver type is not `constant`/`scope_resolution`, or its text fails CLASS_NAME_RE or MAX_RECEIVER_BYTES → `dynamic_receiver`.
  5. Otherwise the site is resolvable with receiver R.
- **`nesting(call)`** uses a parent loop to collect class/module ancestors, outermost first.
  - The full name of each is the previous full name + `::` + its name text.
  - The result is unbound if any name node is not `constant`/`scope_resolution`, or any name text starts with `::`.
  - It is also unbound if the innermost full name ≠ the refs `binding.namespace`, or it fails JOB_KEY_RE.
  - An unbound result with R not starting with `::` → `unbound_namespace`.
  - `nesting` = the full names, innermost first.
- **Call-side argument summary** (`call_arguments`): the `arguments` field's named children, minus `comment`/`heredoc_body`.
  - The type → code map is `forward_argument`→forwarded_arguments, `splat_argument`→splat, `hash_splat_argument`→hash_splat, `pair`→keyword_arguments, `block_argument`→block_argument. A `block` field on the call → block.
  - The first code in CALL_ARGUMENT_CONSTRUCTS order that occurs wins; its span is the first node of that type.
  - Every other child is positional; its span is kept.
  - A = the child count. Diagnostic span for arity, limit and signature codes: the `argument_list` node, or the call node when there is none.

D4.3 `signature(method)`: the named children of the `parameters` field (minus `comment`), or empty.
- `identifier`: required. It is unsupported if an optional parameter was already seen (Ruby fills post-optional requireds from the end).
- `optional_parameter`: optional; its name is the `name` field.
- `splat_parameter`, `destructured_parameter`, `forward_parameter`, or a `keyword_parameter` without a `value` field (a required keyword) → unsupported.
- `keyword_parameter` with a value, `hash_splat_parameter`, `hash_splat_nil` and `block_parameter` are tolerated and ignored. The call can never pass keywords or blocks in a supported case, because step 1 rejects them.
- Any other node type, or a fixed name failing PARAM_RE or MAX_PARAM_NAME_BYTES → unsupported.
- Result: `Signature(supported, names[0..R+O-1], R, O)`.

D4.4 Argument decision. Only for a resolvable enqueue; first match wins.
1. A call-side code → `unsupported` with that code.
2. A > MAX_ARGUMENTS → `argument_limit`.
3. If the target is not INFERRED → `unbound`.
4. The target signature is unsupported → `unsupported_signature`.
5. Not R ≤ A ≤ R+O → `arity_mismatch`.
6. A == 0 → `none`.
7. Otherwise → `mapped`: for i in 0..A-1, emit one INFERRED `async_argument` with `arg=param=i` and `param_name=names[i]`, spanning positional child i.

D4.5 Limits.
- Sites are counted in global (path, call start_byte) order.
- The (MAX_SITES+1)-th located site becomes one `site_limit` enqueue diagnostic. Later sites are dropped and counted in `sites.limited`, with `truncated=true`.
- Class scanning continues for every eligible file, so earlier sites still resolve.
- `Capture.offer` (copied S25 shape) first requires `CAPTURE_RE.search(source)`. Then, if `len(files)>=MAX_FILES`, the file is over `MAX_FILE_BYTES`, or the total would exceed `MAX_CAPTURE_BYTES`, it does `limited += 1`.
- `files.limited` sets `truncated`.

**D5 Resolution** (`Resolver`, one SQL scan per build).
- Query:
  `SELECT s.path, s.kind, s.entity_id, s.binding, json_extract(o.payload,'$.start_byte'), json_extract(o.payload,'$.end_byte') FROM symbols s JOIN occurrences o ON o.id=s.occurrence_id WHERE s.lang='ruby' AND s.kind IN ('class','module','method') ORDER BY s.path, s.line_start, s.id`.
- It builds:
  - `namespaces`: every class/module `binding.namespace`;
  - `class_by_span` and `method_by_span`, restricted to the scanned paths.
- **Class binding:** exactly one class row at the span. The class is unbound if `compact` is set or the namespace fails JOB_KEY_RE (S25 rule, counted in `classes.unbound`).
- **Perform binding:** exactly one method row at the span, with `binding.owner == "ruby:"+ns` and `binding.category == "instance"`. Otherwise it is not counted.
- **Indexes:** `jobs[ns] ⊇ mechanisms` and `performs[ns] ∪= {method entity → Signature}`, taken over all bound definitions of ns (so reopened classes merge).
- **Candidates:**
  - R starting with `::` → `[R[2:]]`;
  - otherwise `[n+"::"+R for n in nesting] + [R]`, de-duplicated with order kept.
  - With an empty nesting the candidate list is `[R]`. This deliberately fixes the S25 helper, which yields `::R` for an empty namespace.
- **Decision**, with `existing = [c ∈ cands if c in namespaces]`:
  1. No existing name → `job_class_not_found`.
  2. `ids = sorted(∪ performs[c] for c in existing if m ∈ jobs[c])`.
  3. `len(existing)==1 and len(ids)==1` → INFERRED.
  4. `len(ids)>=2` → AMBIGUOUS `ambiguous_job_target`.
  5. `len(existing)==1 and m ∈ jobs[existing[0]]` → `perform_not_found`.
  6. Otherwise → `target_not_recognized_job`.
- Docs state these limits:
  - Ruby picks the first lexical candidate, then the cref's ancestors, then top level. Atlas evaluates neither precedence nor ancestor lookup; it reports.
  - A reopened job with two `def perform` is AMBIGUOUS, although Ruby keeps the last one.

**D6 `refresh` sequence** (staging transaction, after `rails_models.refresh`).
1. If `has_records(conn)` (the manifest key `rails_jobs`, or a producer with `$.fingerprint.rails_jobs`), purge:
   - assertions with relation ∈ {async_enqueue, async_argument};
   - evidence whose producer is a rails_jobs producer;
   - occurrences of kind `rails_job`;
   - those producers;
   - the manifest key.
2. Flag off → return None.
3. `current` = `type=="observation"` records keyed by path (S24 condition 2). A captured path is eligible iff it is observed `indexed`, the hash matches and its file entity exists; otherwise `files.failed += 1`.
4. Site rows:
   `SELECT r.path, r.callee_name, r.binding, r.source_entity_id, json_extract(o.payload,'$.start_byte'), json_extract(o.payload,'$.end_byte') FROM refs r JOIN occurrences o ON o.id=r.occurrence_id WHERE r.lang='ruby' AND r.relation='call' AND r.callee_name IN (<4 site methods>) ORDER BY r.path, 5, 6`.
   - Rows whose path is not eligible count as `sites.skipped`.
5. For each eligible path in sorted order (parser fetched lazily; `has_error` → failed):
   - run D4.1;
   - for each row: `n = root.descendant_for_byte_range(s, e)`. A located site requires `n.type == "identifier"`, `n.parent.type == "call"`, the parent's `method` field to span exactly (s, e), and a non-null `source_entity_id`; otherwise `sites.unlocated += 1`;
   - run D4.2; `del tree`.
6. Bind classes and performs (D5); resolve each site; decide arguments (D4.4).
7. Sort facts by (path, start_byte, end_byte, role).
8. If ≥1 fact, `ev.put` the producer. Then for each fact, in order: occurrence, evidence, assertion (path P). Argument facts are built after their enqueue occurrence id is known.
9. Write the manifest.

**D7 `storage/evidence.py` edits** (complete list).
- **EV1** (after :92):
  - `ASYNC_ENQUEUE_RELATION="async_enqueue"`, `ASYNC_ARGUMENT_RELATION="async_argument"`;
  - `RAILS_JOB_RELATIONS` = the two; `RAILS_JOB_KIND="rails_job"`, `RAILS_JOB_ROLE_PREFIX="rails_job:"`, `RAILS_JOBS_ADAPTER="rails-jobs.v1"`;
  - `ASSERTION_RELATIONS = … | RAILS_MODEL_RELATIONS | RAILS_JOB_RELATIONS`, with an S24/S25-style comment;
  - RELATIONS, REFERENCE, EDGE, FIELDS and COLUMNS are untouched.
- **EV2** (after :320): `if set(fingerprint) == {"rails_jobs"}: _validate_rails_jobs_fingerprint(...); return`.
- **EV3** (new, after :845):
  - basis `parsed`; keys == {adapter, adapter_sha256, graph_producer_id}; adapter == `RAILS_JOBS_ADAPTER`; both values are hashes;
  - never compared to the running adapter.
- **EV4:**
  - :434-442 adds `RAILS_JOB_KIND`.
  - After :482: `(kind == RAILS_JOB_KIND) == role.startswith(RAILS_JOB_ROLE_PREFIX)`, and for the kind: state `source`, a hash `entity_id`, language `ruby`.
  - After :544: `async_enqueue` ⇒ resolution ∈ {INFERRED, AMBIGUOUS, UNRESOLVED}; `async_argument` ⇒ {INFERRED, UNRESOLVED}.
  - :562-582 is unchanged, so resolved still requires a target for both.
- **EV5** (`validate_database`):
  - after :904: `(kind == RAILS_JOB_KIND) <= (entity kind in {"method","class","module","file"})`;
  - after :931: `(fp == {"rails_jobs"}) == (occ kind == RAILS_JOB_KIND)`;
  - after :977: `(relation in RAILS_JOB_RELATIONS) == (fp == {"rails_jobs"}) == (occ kind == RAILS_JOB_KIND)`, and for those `source.id == occurrence.entity_id`, with the source kind in the same 4-set.
  - The generic :995-1000 location rule still applies.
- Deep shape checks live in `rails_jobs.validate_records`.

**D8 `codegraph.py` edits** (complete list).
- **CG1** (:36): `from atlas_aci.frameworks import rails_jobs, rails_models, rails_routes`.
- **CG2** (:3182-3183): `job_capture = rails_jobs.Capture()` and `capture_jobs = "rails_jobs" in self.frameworks`.
- **CG3** (after :3228): `if capture_jobs and lang == "ruby": job_capture.offer(rel, source, source_hash)`. It sits before every `continue`, so cache hits are covered.
- **CG4** (after :3579, before :3580): `rails_jobs.refresh(conn, enabled=capture_jobs, capture=job_capture, observations=all_observed, get_parser=get_parser, graph_producer_id=self._capture_producer["id"])`.
- **CG5** (:2584): `| rails_jobs.record_ids(full)`.
- **CG6** (:6435): add `ev.RAILS_JOB_ROLE_PREFIX` to the skip tuple.
- **CG7** (:6457-6458): `or record["relation"] in ev.RAILS_JOB_RELATIONS` → `continue`.
- **CG8** (:6480): `rails_jobs.validate_records(conn)`.
- **Not edited:** QUERIES, `__init__` parameters, `_producer_inputs`, `_build_inputs`, generation kinds, and the other adapters' calls.

**D9 `rails_jobs.validate_records(conn)`** (build, predecessor, import and export). Every failure is `ValueError("Invalid evidence: rails_jobs …")`; KeyError, TypeError, IndexError and AttributeError are wrapped.
- **Producer:** ≤1; cited by ≥1 evidence; its `graph_producer_id` exists as a producer row; the hash format is checked, never pinned.
- **Occurrence:** every `rails_job` occurrence is cited by exactly one evidence from that producer, and that evidence by exactly one assertion. The role area (`enqueue:` / `argument:`) matches the relation.
- **Assertion:**
  - source == occurrence entity, of kind method/class/module/file and language ruby;
  - raw_target is a canonical object of the closed key set for its role;
  - the vocabularies hold, and kind↔mechanism is paired;
  - R and `param_name` fullmatch their regexes;
  - the (role, resolution, reason, arguments) tuple is in the D2 table;
  - INFERRED targets and AMBIGUOUS candidates (≥2) are ruby `method` entities named `perform`.
- **Cross-record checks** for every argument record, with E = the occurrence named by its `enqueue`:
  - E exists, has kind `rails_job`, and its role starts with `rails_job:enqueue:` but not `…unsupported:`;
  - same path, span ⊆ E's span, same source as E's assertion.
  - Mapped: E's assertion is INFERRED with `arguments=="mapped"`, the target equals E's target, `arg==param`, and the ordinals are exactly 0..arity-1, once each.
  - Diagnostic: exactly one per E with `arguments=="unsupported"`, with the same construct.
  - E with `mapped` has exactly arity mapped records. E with `none`, `unbound`, or a diagnostic enqueue has none.
- **Count:** ≤ the D3 cap.

**D10 Invalidation and S10** (the S24 D7/S25 D10 argument; this wording is binding).
- rails_jobs facts are a global function of: the captured bytes, Ruby `symbols` and `refs`, and the adapter.
- They are rebuilt globally by the adapter on every enabled build, not republished per file by the S10 plan.
- S10 contributes `remove_path` for changed or removed files and the `affected` classification.
- The ids join `dropped`, so a parse failure never resurrects them.
- `dep.snapshot` runs before refresh:
  - unchanged paths reuse;
  - an edited enqueue file is cleared and affected;
  - an edited job file changes the Ruby bucket, so the enqueue file is affected;
  - flag toggles keep the digests consistent (ON→OFF purges after the snapshot).

**D11 Identity and churn.**
- Editing `codegraph.py` and `evidence.py` changes the native producer id, and with it every evidence, assertion, link, generation and snapshot id, for all graphs. Existing indexes rebuild in full once.
- Entity ids, occurrence ids and projection rows (minus producer-derived links) are unchanged; G-IMAGE proves this.
- On the same code, ON minus rails_jobs-owned ids == OFF.
- `rails_routes.py` and `rails_models.py` are byte-identical, so their `adapter_sha256` is unchanged. Only `graph_producer_id`-derived ids churn (G-ROUTES / G-MODELS).

**D12 No execution (AC-007).**
- `rails_jobs.py` passes the S24/S25 AST gates unchanged:
  - the allowed imports; the banned names and args (`eval`, `exec`, `compile`, `open`, `getattr`, `vars`, `input`, …);
  - the banned attributes (`connect`, `create_connection`, `urlopen`, `system`, `popen`, `spawn*`, `exec*`, `fork*`);
  - one `Path(__file__).read_bytes()`; ASCII only; no self-calls;
  - no `\d`/`\w`, and no `$` in `re.*` literals;
  - no "alter table" or `nx.compose(`.
- It imports neither redis nor sidekiq. Repository Ruby is only parsed by tree-sitter, from bytes the build already captured.

### Limitations (`docs/frameworks/rails.md`, new section "Jobs and enqueue argument paths (ACI-S26)")
- **What is read:**
  - every indexed `.rb` file (specs included) whose bytes match CAPTURE_RE;
  - direct constant receivers only;
  - recognition is limited to a direct `include Sidekiq::Worker|Job` or a direct `ApplicationJob`/`ActiveJob::Base` superclass. Custom bases or mixins and inherited `perform` → `target_not_recognized_job`.
- **Async boundary:**
  - `async_enqueue` is an asynchronous boundary, not a call and not proof that the job ran;
  - it is invisible to MCP traversal; inspect it via export and the `rails_jobs` manifest key;
  - the native call graph is unchanged (`.new.perform` stays an ordinary `call`).
- **Arguments:**
  - a mapping says positional argument i is delivered to parameter i;
  - serialisation (JSON, GlobalID), keyword transformation and middleware are not modelled;
  - a heredoc argument spans only its opener.
- **Resolution:** lexical candidates only; no Ruby precedence or ancestor lookup; reopened classes with two performs are AMBIGUOUS.
- **Cost and failure:**
  - every ON build re-parses the captured files outside `cache_stats`;
  - `rails_job` occurrences count toward `diff_occurrence_limit` (≤ (MAX_SITES+1)×17);
  - a file that fails to parse yields no facts.
- **Identity:** producer-id churn (D11).

### Work plan (timebox 3 engineering days)
**Day 1 AM: qualification, before any product edit.** Scratch probes run on the host and in the 3.12 dev image. They write `.spectra/evidence/aci-s26/qualification.json` (`qual_grammar.py`, `qual_refs.py`, `qual_validity.py`).
- **(a) Grammar.** Parse every fixture file with the pinned `get_parser("ruby")`; `has_error` must be false. Record, for each fixture form:
  - the `call` fields receiver/method/arguments/block: constant, `A::B`, `::A`, set chain, receiverless, no parens, empty parens;
  - the argument_list child types: `splat_argument`, `hash_splat_argument`, `block_argument`, `forward_argument` in `def f(...)`, `pair` (symbol key and `=>`), `heredoc_beginning`/`heredoc_body` placement, `do_block`/`block`;
  - the method `parameters` type and children: identifier, `optional_parameter` name field, `splat_parameter`, `keyword_parameter` with and without value, `hash_splat_parameter`, `hash_splat_nil`, `block_parameter`, `forward_parameter`, `destructured_parameter`, and parenless `def perform a, b`;
  - the `include Sidekiq::Job` call shape; the superclass shapes;
  - that `Node.descendant_for_byte_range` exists.
- **(b) Refs and binding.** A base-code build of the fixture:
  - every perform_async/perform_later/perform_in/perform_at site has exactly one `refs` row (`relation='call'`, ruby);
  - that row's `occurrence_id` span equals the method identifier node, and D6 step 5 locates the call;
  - `source_entity_id` is non-null and equals the expected owner (method, or the file for spec blocks);
  - `binding.namespace` equals the innermost `nesting` name;
  - after an unchanged `since=x` build, the refs rows of unchanged files are still present with the same values;
  - perform and class definition occurrences map 1:1 to node spans, with `owner`/`category` as D5 expects.
- **(c) Validity.** In a scratch copy with EV1-EV5:
  - INFERRED with target passes for both relations;
  - EXTRACTED (with and without target), AMBIGUOUS `async_argument` and target-less INFERRED all fail;
  - `validate_database` plus `_validate_evidence_projections` (with CG6/CG7) pass on a hand-built DB holding one record per role;
  - the S25 parametrized carve-out test passes unchanged.
- **(d) Flag-off identity baselines.** Base digests (compare_graphs) of the S26 fixture OFF, the S24 fixture `--frameworks rails`, and the S25 fixture `--frameworks rails_models --rails-table-map`.
- **(e) Base invariants:** tools sha and bytes, 3436/3448/797/7730, and the full `pytest -q` at base with test_s08 nonregular_pack deselected.
- **(f) Fidelity.** Cite the Sidekiq Getting-Started and Rails ActiveJob guides at pinned revisions: positional arguments are delivered to perform, keywords and serialisation are transformed. If that is not verifiable, the docs avoid specifics.

STOP RULE:
- **(a)** Supported fixture lines fail to parse, or argument/parameter children cannot be classified into the closed node types → BLOCKED with the node dump. Name-only differences adapt D4 and are recorded.
- **(b)** Refs do not locate every fixture site with a non-null owner → fall back to an iterative explicit-stack walk of the captured trees (call nodes with a SITE_METHODS method). The owner then becomes the innermost enclosing `symbols` entity by span containment (method/class/module), else the file entity, cross-checked with the refs `binding.byte`. Decide by the end of Day 1 AM.
  - If neither yields a deterministic owner for every fixture site, or perform definitions are not 1:1 → BLOCKED for AC-001/002, because the fix needs out-of-scope extractor edits.
- **(c)** Any edit beyond EV1-EV5/CG6-CG7 is needed, or a gated consumer breaks → BLOCKED.
- **(d)/(e)** Base not green → BLOCKED.

**Day 1 PM** (S25 condition 12: the EV edits come first):
- EV1-EV5; `frameworks/__init__.py`;
- the pure parts of `rails_jobs.py` (vocabularies, Capture, `_classes`, `signature`, `call_arguments`, `nesting`, candidates);
- the fixture plus `provenance.json` (sha256 and bytes per file, expected totals);
- pure tests.

**Day 2:**
- CG1-CG8; `Resolver`, `refresh`, `record_ids`, `validate_records`;
- `test_s26_rails_jobs.py` AC-001..007.

**Day 3:**
- `test_s26_isolation.py` (planted corruptions, S10, caps, combinations);
- the prior-slice hunks; docs, CHANGELOG and README;
- gates in a dev image rebuilt from the branch;
- G-IMAGE / G-ROUTES / G-MODELS (orchestrator); RUN-01/02.

Cut order (each cut becomes a documented limitation):
1. README/CHANGELOG depth.
2. Export→import round-trip test.
3. G-NOLEAK/G-QUERY MCP comparisons (keep G-MCP and the relation-set pins).
4. The three-adapter incremental toggle test (keep jobs-alone, the combined build, and the jobs ON→OFF toggle).
5. Inline-snippet reachability for `hash_splat_nil`/`destructured_parameter`/`forward_parameter` (fixture codes stay).
6. S10 case (iv), the syntax break.

Never cut:
- the AC tests; EV4/EV5 and their negatives;
- purge + recompute + `dropped`;
- G-OFF/G-ONOFF/G-IMAGE/G-ROUTES/G-MODELS;
- `validate_records` plus planted corruptions; the `_validate_evidence_projections` copy test;
- S10 (i)(ii)(iii)(v);
- the no-exec gates, G-SRC, G-MCP.

### Independent checker conditions (binding; override conflicting plan text)
Checker `atlas-s26-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s26.review.json`); this is the validation required by the confidence verdict VALIDATE (82.75). Every condition below is binding and overrides any conflicting plan text.

Orchestrator resolutions (recorded 2026-11-04 under the user's autonomous-execution instruction), stricter option each time: condition 1 - any site whose ancestor chain contains a scope_resolution-named class/module with at least one enclosing class/module is `unbound_namespace` (top-level compact `class A::B` stays bound); the Outer fixture case becomes unbound_namespace and a bound nested-module INFERRED case is kept; condition 2 - the affected_paths membership claim is dropped; S10 (iii) asserts arity_mismatch, mode == affected and equality with a fresh full build; D10 and docs reworded; condition 3 - names with a class/module row in a limited or ineligible path never yield INFERRED or AMBIGUOUS (target_not_recognized_job), with a MAX_FILES test; condition 6 - Day-1 (a) and (b) are also run under Python 3.14 in a throwaway `python:3.14` container with tree-sitter 0.25.2 and tree-sitter-language-pack 1.6.0 pinned (network allowed for that container only); if the pinned wheels are unavailable for 3.14 the reason is recorded in qualification.json; condition 7 - the INFERRED enqueue keeps a null reason and carries `"arguments": "unsupported"` plus the construct code in raw_target, while `unsupported_argument_mapping` is the reason of the single async_argument diagnostic that references the enqueue occurrence; docs state this.

**Verified sound (no condition needed)**
- Ruby `refs` rows exist for `Foo.perform_async(...)`.
  - The QUERIES pattern at codegraph.py:181 yields one row per call, with `callee_name` as the identifier text and `receiver` in `binding` (:3971-4005).
  - `occurrence_id` points at the identifier span.
  - `source_entity_id = owner(node)`: the method, class or module entity, else the file entity. This covers blocks, lambdas, top-level scripts and spec blocks (:3820-3828).
  - `class << self` is excluded from `namespace` (:4106-4110) and Module.nesting is equivalent there.
  - Captures run before every `continue` (:3225-3228), so cache hits are covered. Refs persist across incremental builds (:3157-3161).
- A site cannot be missed by the byte prefilter, because the callee name is itself a literal byte match. Only caps can drop a file.
- Bare keywords are a direct `pair`, and a braced hash is a positional `hash` node. The distinction in the plan is right.
- Prior-slice test edits are exhaustive.
  - I grepped REGISTRY, `frameworks/`, `rails_models.py` and ASSERTION_RELATIONS across the tests. Only the five listed hunks break.
  - The parametrized test at test_s25_isolation.py:244-267 passes unchanged for both new relations. EXTRACTED fails either via EV4 or the target-less rule (evidence.py:562-574). INFERRED with a target is accepted.
  - The src-wide scans at test_thesis_negatives:51 and test_export:374 are satisfied by the plan's string avoidance.
- Consumers are relation-filtered: indexed.py:18-26, architecture/checker.py:106-115, validation.py:403 (`raw_target != name` never matches canonical JSON), and diff.py:115 and retrieval.py:94 read definition occurrences only. CG6 and CG7 are both required, because otherwise `_validate_evidence_projections` (codegraph.py:6455-6477) demands edges or refs projections.
- The 4-set of source kinds is valid. `symbols` rows of kind method come from `@def.method`, including `singleton_method`.

**Findings**

1. **MAJOR: false INFERRED for compact classes used as a site namespace.**
   - Evidence: plan :219-223 and fixture :575 treat `module Outer; class Inner::Thing` as bound, giving nesting `[Outer::Inner::Thing, Outer]`.
   - `binding.namespace` is raw name-text concatenation (codegraph.py:4106-4110, 4278), so the cross-check against it is vacuous. Ruby may define `::Inner::Thing`.
   - This contradicts the S25 checker condition 3 (aci-s25.md:519-521) and S26's own D5 class rule, which marks the class unbound. A shadow case is realistic: `Inner::Thing::R` exists in addition to top-level `R`.
   - Binding condition (stricter, take it): if any ancestor name node is a `scope_resolution` with at least one enclosing class or module, the site is `unbound_namespace`. A top-level compact name `class A::B` stays bound.
   - Change the Outer fixture case to `unbound_namespace`. Keep a bound nested-module INFERRED case (Billing::Nightly) and add a test.

2. **MAJOR: S10 case (iii) and the D10 claim are unsound as written.**
   - Plan :358-360 and :617-621 assert that editing `ReportJob#perform` puts `enqueuer.rb` in `affected_paths`.
   - The Ruby bucket digest is only `symbols` rows, `blockers`, `imports` and `stale_names` (resolvers/dependencies.py:119-133).
   - Ruby has no `@assign` captures, so `blockers` is empty.
   - A params-only edit leaves every `symbols` row unchanged: the name, line range, `byte` and structural `entity_id` stay the same. The enqueue file is then not affected, and the assertion is unsatisfiable.
   - Facts are still correct, because the adapter recomputes globally on every build.
   - Binding condition:
     - Drop the `affected_paths` membership requirement.
     - Assert E3 `arity_mismatch` and equality with a fresh full build, with `mode == "affected"`.
     - Reword D10 and the docs so that job-file edits are not claimed to affect the enqueue file.
     - Optionally add a variant that shifts `symbols` rows.

3. **MINOR: INFERRED can be wrong under caps.**
   - `namespaces` come from all symbols (D5 :257-263), but `performs` come only from captured files.
   - If the second `ExportJob` file is `limited` (D4.5 :252) or not eligible, the result is INFERRED instead of AMBIGUOUS.
   - Condition: `Capture` records the limited paths as a bounded set. Any existing name that has a Ruby class or module row in a limited or ineligible path never yields INFERRED or AMBIGUOUS and becomes `target_not_recognized_job`. Add a `MAX_FILES` monkeypatch test.

4. **MINOR: regression coverage and doc edits can break prior tests.**
   - The regression set (:643) omits test_s14, test_s23, test_s29, test_s31 and test_s32.
   - test_s14_capabilities.py:262-274 parses the README section `### TSX structural navigation` for four-cell table rows.
   - test_s29_openapi_diff.py:1444-1448 slices the first `## [` section of CHANGELOG up to the next `\n- **`.
   - Condition:
     - Put the README pointer outside that section.
     - Add the CHANGELOG entry as a separate top-level bullet that does not split the OpenAPI bullet.
     - Run those five files explicitly, in addition to the full suite.

5. **MINOR: compare_graphs.py and g_image.sh are hard-wired for two adapters.**
   - `routes_pair`, `both_pair` and `selftest` (S25 compare_graphs.py:386-450) check only `absent(rails_models)` and copy two fixtures.
   - Condition:
     - Add `absent(rails_jobs)` to the G-ROUTES and G-MODELS pairs.
     - Add a jobs-alone versus jobs+others pair.
     - The self-test merges three fixtures. Verify no path or class collisions: S24 `lib/legacy/` versus S26 `lib/legacy/`.
     - g_image.sh runs the S26 fixture three times plus both rejections.

6. **MINOR: Day-1 qualification.**
   - CI runs 3.14, but the probes run on the host and the 3.12 image.
   - Condition: record `sys.version`, tree-sitter 0.25.2 and language-pack 1.6.0 (uv.lock:957, 993) in `qualification.json`, and run (a) and (b) under 3.14 or state why not.
   - Add to (b): `::Rooted` gives `unsupported_source_owner` and a file-entity owner; `Inner::Thing` has an entity and a method owner; the S19 grammar dump has no Ruby `argument_list` nodes, so (a) is the only evidence for them.

7. **MINOR: the docs must name the silent limits.**
   - Receiverless self-enqueues such as `perform_async(x)` inside the job class are reported as `dynamic_receiver`.
   - `send` and `public_send` are invisible.
   - Definitions in skip-pattern or failed files (`storage`, `tmp`, `build`; config.py:26-48) are invisible.
   - Constant assignment and `Class.new` shadows are not modelled.
   - For multi-segment receivers, only the first segment is lexically resolved.
   - State explicitly that the `unsupported_argument_mapping` for splat and keyword cases lives on the `async_argument` diagnostic that references the enqueue. An INFERRED assertion must keep a null reason (evidence.py:572), so the enqueue itself cannot carry it.

8. **MINOR: timebox.**
   - Day 2 carries CG1-8, Resolver, refresh, `validate_records` with its cross-record checks, and 7 AC tests.
   - Condition: add a Day-2 checkpoint. AC-001, AC-002, AC-003 and AC-005 must be green before AC-004, AC-006 or AC-007 work and before Day 3. Otherwise apply cut #1 and cut #2, then #5 and #6, and report.

## Stories
- As an agent operator, I want each direct `Worker.perform_async(...)` / `Job.perform_later(...)` linked to its job's explicit `perform` by a distinct asynchronous relation, with argument i mapped to parameter i by span. Then I can trace an enqueue to the parameters it feeds without booting Rails or touching Redis.
- As a reviewer, I want dynamic, delayed, set-chained, splatted, keyworded or arity-incompatible enqueues visible as closed-vocabulary unresolved facts. Then no job target or argument flow is guessed or described as a synchronous call.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s26.criteria.md` (sha256 `6b34e5cff581e753cbf807271ae2e511887994cbee54c0546d9c1e76f29efdfe`), verbatim:

```
### AC-S26-001 (event-driven)
GIVEN a direct perform_async or perform_later receiver names one indexed worker/job class
WHEN the job adapter binds the enqueue
THEN the adapter SHALL emit an async_enqueue relation to the unique matching perform method.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_direct_enqueue_target

### AC-S26-002 (event-driven)
GIVEN the enqueue and perform signature have compatible fixed positional arity
WHEN the job adapter analyzes their argument boundary
THEN the adapter SHALL emit argument-to-parameter mappings matching the fixed positional ordinals.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_positional_argument_mapping

### AC-S26-003 (ubiquitous)
THEN every adapter-produced enqueue relation SHALL carry the distinct relation kind async_enqueue.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_enqueue_relation_is_not_synchronous

### AC-S26-004 (unwanted-behavior)
GIVEN the enqueue uses splats, transformed keyword arguments or a dynamic receiver
WHEN argument-boundary analysis encounters the unsupported form
THEN the adapter SHALL report unsupported_argument_mapping for that enqueue.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_transformed_arguments_are_unresolved

### AC-S26-005 (unwanted-behavior)
GIVEN more than one worker/job definition matches the declared receiver
WHEN enqueue binding attempts to select a target
THEN the adapter SHALL retain the candidate set as an unresolved job target.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_job_collision_is_unresolved

### AC-S26-006 (event-driven)
GIVEN identical snapshot and job-adapter version
WHEN the adapter indexes the same inputs twice
THEN the canonical enqueue and argument-flow fact set SHALL remain identical after repeat indexing.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_job_reindex_is_idempotent

### AC-S26-007 (ubiquitous)
THEN the job adapter SHALL analyze enqueue syntax without dispatching a job.
VERIFY: test: mcp-server/tests/slices/test_s26_rails_jobs.py::test_rails_jobs_never_enqueue

### AC-S26-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S26-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

Fixture `tests/fixtures/rails_jobs/project/` (ASCII; copied with copytree; totals pinned in `provenance.json`; no file in `app/models/`, `config/routes.rb` or `db/migrate/`, so the S24/S25 adapters yield zero records here).

Jobs and workers:
- `app/jobs/application_job.rb`: `ApplicationJob < ActiveJob::Base` (no perform).
- `app/jobs/report_job.rb`: `ReportJob < ApplicationJob`, `def perform(report, format)`.
- `app/jobs/billing/sync_job.rb`: `module Billing; class SyncJob < ApplicationJob; def perform(account)`.
- `app/jobs/sync_job.rb`: `SyncJob < ::ActiveJob::Base`, `def perform(account, mode)`.
- `app/jobs/export_job.rb` and `lib/legacy/export_job.rb`: both top-level `ExportJob < ApplicationJob` with `def perform(id)`. This is a same-name collision.
- `app/jobs/no_perform_job.rb`: `NoPerformJob < ApplicationJob` with only `def self.perform(x)` and `class << self; def perform(y)`.
- `app/jobs/inherited_job.rb`: `InheritedJob < ReportJob`.
- `app/jobs/sentinel_job.rb`: `SentinelJob < ApplicationJob` with `after_enqueue { File.write("ATLAS_S26_SENTINEL_AFTER_ENQUEUE","x") }`, `before_perform {…BEFORE_PERFORM…}`, and `def perform(path) File.write(path,"x") end`.
- `app/workers/hard_worker.rb`: `include Sidekiq::Job`; `def perform(user_id, mode = "full", notify: false, &blk)` (R=1, O=1, tolerated kw and block).
- `app/workers/ping_worker.rb`: `include ::Sidekiq::Worker`; `def perform`.
- `app/workers/splat_worker.rb`: `include Sidekiq::Worker`; `def perform(*args)`.
- `app/workers/ordered_worker.rb`: `def perform(a, b = 1, c)`.
- `app/workers/strict_kw_worker.rb`: `def perform(a, b:)`.
- `app/workers/sentinel_worker.rb`: `include Sidekiq::Job`; class-body `File.write("ATLAS_S26_SENTINEL_CLASS_BODY","x")`; `def perform(path) File.write(path,"x") end`.
- `app/services/plain_task.rb`: `PlainTask`, `def perform(x)` (not a job).

Sites:
- **`app/services/enqueuer.rb`** — `class Enqueuer; def run(user, report, ids, opts, job_class, blk, t)`:
  - **INFERRED:**
    - E1 `HardWorker.perform_async(user.id)` → [0 user_id]
    - E2 `HardWorker.perform_async(user.id, "fast")` → [0 user_id, 1 mode]
    - E3 `ReportJob.perform_later(report, :pdf)` → [0 report, 1 format]
    - E4 `::ReportJob.perform_later report, :csv`
    - E5 `PingWorker.perform_async` and E6 `PingWorker.perform_async()` → `none`
    - E7 `Billing::SyncJob.perform_later(user)` → [0 account]
    - E15/E16 `SentinelWorker.perform_async("ATLAS_S26_SENTINEL_ASYNC")` and `SentinelJob.perform_later("ATLAS_S26_SENTINEL_LATER")`
  - **AMBIGUOUS:** E8 `ExportJob.perform_later(1)` → 2 candidates, `unbound`.
  - **UNRESOLVED:**
    - E9 `GhostJob.perform_later(1)` → `job_class_not_found`
    - E10 `NoPerformJob.perform_later(1)` → `perform_not_found`
    - E11 `PlainTask.perform_async(1)`, E12 `HardWorker.perform_later(1)`, E13 `ReportJob.perform_async(1, 2)` and E14 `InheritedJob.perform_later(1, 2)` → `target_not_recognized_job`
  - **Enqueue diagnostics:**
    - D1 `job_class.perform_later(1)`, D2 `self.class.perform_async(1)`, D3 `"ReportJob".constantize.perform_later(1, 2)` and D4 receiverless `perform_async(1)` → `dynamic_receiver`
    - S1 `ReportJob.set(wait: 300).perform_later(report, :pdf)` → `set_chain`
    - L1 `HardWorker.perform_in(300, user.id)` and L2 `HardWorker.perform_at(t, user.id)` → `delayed_enqueue`
  - **Argument diagnostics** (enqueue INFERRED, `unsupported`):
    - U1 `HardWorker.perform_async(*ids)` → splat
    - U2 `ReportJob.perform_later(**opts)` → hash_splat
    - U3 `ReportJob.perform_later(report, format: :pdf)` → keyword_arguments
    - U4 `HardWorker.perform_async(user.id, &blk)` → block_argument
    - U5 `HardWorker.perform_async(user.id) { |x| x }` → block
    - U6 `ReportJob.perform_later(1, 2, 3)` and U7 `HardWorker.perform_async` → arity_mismatch
    - U8 `SplatWorker.perform_async(1)`, U9 `OrderedWorker.perform_async(1, 2)` and U10 `StrictKwWorker.perform_async(1)` → unsupported_signature
    - U11 `ExportJob.perform_later(*ids)` → AMBIGUOUS + splat (call-side on a non-INFERRED target)
    - `def fwd(...) HardWorker.perform_async(...) end` → forwarded_arguments
  - **Silent:** `ReportJob.perform_now(report, :pdf)`, `HardWorker.new.perform(1)`, `HardWorker.perform_inline(1)`, `HardWorker.perform_sync(1)`, `HardWorker.delay.ping`, `HardWorker.perform_bulk([[1]])`, a comment `# HardWorker.perform_async(1)`, and the string `"ReportJob.perform_later"`.
- **`app/services/billing/nightly.rb`** — `module Billing; class Nightly; def run`:
  - B1 `SyncJob.perform_later(1)` → AMBIGUOUS [Billing::SyncJob#perform, SyncJob#perform];
  - B2 `ReportJob.perform_later(1, 2)` → INFERRED (only `ReportJob` exists).
- **`app/services/outer.rb`:**
  - `module Outer; class Inner::Thing; def go; ReportJob.perform_later(1, 2)` → INFERRED (nesting [Outer::Inner::Thing, Outer]);
  - `class ::Rooted; def go; ReportJob.perform_later(1, 2)` → `unbound_namespace`.
- **`spec/jobs/report_job_spec.rb`:** `RSpec.describe ReportJob do it "x" do ReportJob.perform_later(1, 2)` → INFERRED with the spec file entity as source.
- **`vendor/bundle/gems/x/lib/w.rb`:** a skip-patterned enqueue; no facts.

Test rules:
- Real builds: `CodeGraph(repo, graph_root=<external>, frameworks=("rails_jobs",))`.
- Expected ids come from independent SQL on `symbols` and `refs`.
- Every `all(...)` is preceded by a non-empty assertion. There are no mocks of the behaviour under test.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_rails_direct_enqueue_target | E1/E3/E4/E7/B2 and the spec site are `async_enqueue` INFERRED. The target equals the perform method id (independent SQL: kind method, name perform, `binding.owner=="ruby:<ns>"`, category instance). The occurrence bytes equal the call text (E1 == `HardWorker.perform_async(user.id)`). Source == occurrence entity == `Enqueuer#run` (the spec file entity for the spec site). raw_target is exact canonical (E1: `{"arguments":"mapped","arity":1,"kind":"perform_async","mapping":"rails_job_receiver.v1","mechanism":"sidekiq","receiver":"HardWorker"}`). The target's definition occurrence bytes start with `def perform`. Evidence comes from the single `rails_jobs` producer. E9-E14 carry the D2 reasons. `ev.validate_record` rejects E1 relabelled EXTRACTED. |
| 002 | test_rails_positional_argument_mapping | E1 → one mapped record `{"arg":0,"enqueue":<E1 occ id>,…,"param":0,"param_name":"user_id"}` spanning `user.id`. E2 → ordinals 0,1 → user_id, mode (span `"fast"`). E3 → report, format. E7 → account. Each record: INFERRED, target == the enqueue target, source == the enqueue source, span ⊆ the enqueue span, `arg==param`, count == arity. E5/E6 → `none` and zero records. No raw_target contains argument source text (`user.id`, `fast`, `pdf` are absent). |
| 003 | test_rails_enqueue_relation_is_not_synchronous | Every rails_jobs enqueue record (non-empty, all roles `rails_job:enqueue:`) has relation `async_enqueue`. `async_enqueue`/`async_argument` ∉ `ev.RELATIONS`, `REFERENCE_RELATIONS`, `EDGE_RELATIONS`, `KNOWN_EDGE_RELATIONS`. No `edges` row has those relations. No `call` assertion or edge targeting any perform id has an occurrence inside an enqueue span. `perform_now`, `.new.perform`, `perform_inline`, `perform_sync`, `delay` and `perform_bulk` sites have no rails_job record. `validate_record` rejects `async_enqueue` with resolution EXTRACTED. |
| 004 | test_rails_transformed_arguments_are_unresolved | D1-D4, S1, L1, L2 and `::Rooted` → enqueue UNRESOLVED `unsupported_argument_mapping`, role `rails_job:enqueue:unsupported:<c>`, raw `{"construct":c,"kind":k,"mapping":"unexpanded"}`, no argument records. U1-U11 and fwd → enqueue raw `arguments:"unsupported"` with that construct, zero mapped records, and exactly one UNRESOLVED `async_argument` (`unsupported_argument_mapping`, raw `{"construct":c,"enqueue":<occ>,"mapping":"unexpanded"}`) with the D4.2 span. Every construct code in the fixture set is reached. |
| 005 | test_rails_job_collision_is_unresolved | E8 and B1 → AMBIGUOUS `ambiguous_job_target`. Candidates == the sorted 2 perform ids from independent SQL (two ExportJob files; Billing::SyncJob and SyncJob). Target null, `arguments:"unbound"`, no argument records. U11 keeps the candidates and has a splat diagnostic. `validate_record` rejects AMBIGUOUS with one candidate or with a target. |
| 006 | test_rails_job_reindex_is_idempotent | Two full ON builds into fresh roots, plus `build(since="x")` with no change: the sorted canonical lines of every rails_jobs-owned record and the manifest value are byte-identical. `adapter_sha256` == sha256(`rails_jobs.py`); `graph_producer_id` == the native producer id. The unchanged rebuild gives `affected_units 0`. Non-vacuous controls: editing E1's argument text changes the set, and adding a parameter to `ReportJob#perform` changes the E3 records. |
| 007 | test_rails_jobs_never_enqueue | The S25 spies via monkeypatch, with the liveness check first: Popen records and refuses; os exec/spawn/fork/system/popen, asyncio subprocess and socket connect raise; `sqlite3.connect` records URIs normalised and skips `:memory:`. ON build succeeds with ≥8 INFERRED. Launches equal an OFF build under the same spy, and no argv token contains ruby/rails/rake/bundle/sidekiq/redis. All connect targets are under graph_root or tmp_path. `redis`/`sidekiq` ∉ `sys.modules` before and after. No `ATLAS_S26_SENTINEL_*` exists under repo, tmp_path or cwd. The repo SHA-256 inventory is unchanged. AST gate: no redis/sidekiq import, plus the D12 rules. |
| RUN-01 | gate | The SHA-256 manifest of declared tracked implementation files is equal before and after clean re-entry; `git diff --exit-code <output_commit>` holds for those paths. |
| RUN-02 | gate | `slice_id` + `criteria_sha256` are equal across interrupted and resumed receipts; one authoritative receipt, `.spectra/state/aci-s26.execution.json`. |

Supporting tests (`test_s26_isolation.py`):
- **Vocabulary:** the exact D3 sets and counts (4/2/5/6/9/5/4/3), the limits and the regexes. `ev.RELATIONS` is the base 8-set. The two relations are only in `ASSERTION_RELATIONS`. `RAILS_JOB_*` constants.
- **Pure units:**
  - `signature` for every parameter shape (incl. parenless, required-after-optional, `hash_splat_nil`, `destructured_parameter`, `forward_parameter`);
  - `call_arguments` precedence;
  - candidates: empty nesting → `[R]`, and `::R`.
- **Caps** (monkeypatched):
  - `MAX_SITES=2` → one `site_limit` and `truncated`;
  - `MAX_ARGUMENTS=1` → `argument_limit`;
  - `MAX_FILES`/`MAX_FILE_BYTES`/`MAX_CAPTURE_BYTES` → `files.limited` and `truncated`;
  - `get_parser` is never called when OFF or with no eligible file (counting parser).
- **Combinations** (merged repo = S24 + S25 + S26 projects):
  - each adapter's owned set in the `("rails","rails_jobs","rails_models")` build == its single-adapter build (byte-identical ids);
  - incremental all → (rails, rails_models) → all keeps routes and models byte-identical and restores jobs identically.
- **G-OFF:** no `rails_jobs` fingerprint, `rails_job` kind, relation or manifest key without the flag.
- **G-ONOFF:** `ids(ON) − record_ids(ON) == ids(OFF)` per record type; projections are equal.
- **Validation:**
  - `validate_database` and `_validate_evidence_projections` pass on a copy of an ON DB;
  - planted corruptions each raise ValueError (D9): role/kind mismatch, relation/role-area mismatch, an extra fingerprint key, source ≠ occurrence entity, INFERRED to a class or non-perform method, `async_argument` AMBIGUOUS, raw_target as a list, non-canonical JSON or an unknown key, a construct outside the vocabulary, a kind/mechanism mismatch, `param != arg`, a dangling or unsupported `enqueue` reference, an argument span outside the enqueue, an argument target ≠ the enqueue target, mapped count ≠ arity, a missing diagnostic for `unsupported`, an orphan occurrence, a second producer;
  - a different valid `adapter_sha256` is accepted.
- **S10** (each compared with a fresh full build; `_validate_evidence_projections` on the lease; `binding_resolution.mode=="affected"`):
  - (i) delete `report_job.rb` → E3 `job_class_not_found` and no E3 arguments;
  - (ii) edit `enqueuer.rb` E2 to 3 args → arity_mismatch;
  - (iii) edit only `ReportJob#perform` to 3 params, with `enqueuer.rb` unchanged and in `affected_paths` → E3 arity_mismatch;
  - (iv) break `enqueuer.rb` syntax → `files.failed 1`, no facts from it, and its history holds no rails_jobs ids;
  - (v) ON→OFF → zero records and no key;
  - (vi) OFF→ON.
- **G-ENVELOPE / G-MCP / G-NOLEAK / G-QUERY:**
  - as S25; the help lists `rails_jobs`;
  - the tools output contains none of `rails_jobs`, `async_enqueue`, `async_argument`;
  - traversal from `Enqueuer#run` never reaches a perform through these records;
  - impact admission ON == OFF + the rails_job occurrence count.
- **Export→import round trip** keeps and validates the records (cut 2).

Gates beyond the ACs:
- **G-SRC:**
  - `git diff --name-only 0194758 -- mcp-server/src` ⊆ {`codegraph.py`, `storage/evidence.py`, `frameworks/__init__.py`, `frameworks/rails_jobs.py`}.
  - `git diff --exit-code 0194758 --` holds for: `__main__.py`, `frameworks/rails_routes.py`, `frameworks/rails_models.py`, `server.py`, `protocol/ query/ tools/ extractors/ providers/ indexing/ resolvers/ architecture/ counterfactual/ contracts/ importers/ reports/`, `enforcement.py`, `config.py`, `storage/*` except `evidence.py`, `pyproject.toml`, `uv.lock` and `.github/workflows`.
  - The `evidence.py` diff touches only EV1-EV5; the `codegraph.py` diff only CG1-CG8.
  - The prior-slice test diff is exactly:
    - `test_s24_rails_routes.py`: the :381 statement adds `"rails_jobs.py"` (one statement; ruff format may wrap it) and :418 becomes `reads == 3`;
    - `test_s25_rails_models.py`: the same two at :645 and :687;
    - `test_s25_isolation.py`: the :231-239 set adds `"async_enqueue", "async_argument"`;
    - no other `test_s0*`/`test_s1*`/`test_s2*`/`test_s3*` file changes.
- **G-MCP:** `atlas-aci tools` sha `94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56`, 1631472 bytes.
- **G-ENVELOPE:** 3436/3448/797/7730.
- **Regression set unchanged:** `test_s07*`, `test_s09*`, `test_s10*`, `test_s12_storage`, `test_s16*`, `test_s19*`, `test_s20*`, `test_s21*`, `test_s22*`, `test_s24*`, `test_s25*`, `test_s37*`, `test_schema_epoch`, `test_thesis_negatives`, `test_export`.
- **Full suite:** no new failure or skip against base. The test_s08 nonregular_pack case is deselected (it hangs in the dev image), and the base's known classifications are carried over.
- **G-IMAGE** (orchestrator; base image from `0194758`; `docker run --network none`, fixture `:ro`, separate roots):
  - S26 fixture, base OFF vs new OFF vs new ON (`--frameworks rails_jobs`):
    - entity and occurrence id sets: base == OFF == ON − {kind `rails_job`};
    - symbols/refs/edges rows equal (link ids excluded);
    - normalized assertions: base == OFF == ON − {2 relations};
    - ON = OFF producers + 1 `rails_jobs` producer;
    - manifest `rails_jobs` only in ON.
  - The base image rejects `--frameworks rails_jobs` (exit 2), and the new image rejects `--frameworks django` (exit 2).
- **G-ROUTES / G-MODELS:** the S24 fixture `--frameworks rails` and the S25 fixture `--frameworks rails_models --rails-table-map`, base image vs new image. These must be equal:
  - the owned occurrence ids;
  - the normalized owned assertions (with `occurrence_id`);
  - the fingerprint minus `graph_producer_id`, including `adapter_sha256`;
  - the manifest value minus `producer_id`.
- **Three adapters together:** `rails,rails_jobs,rails_models` on the merged fixture == each adapter alone.
- **`compare_graphs.py` / `g_image.sh`:** copied to `.spectra/evidence/aci-s26/`.
  - `ADAPTERS` gains `"rails_jobs":"rails_jobs"`. Ownership is per fingerprint key, reported separately; the union is excluded from native.
  - The self-test runs a three-adapter DB with non-zero counts for each and disjoint owned sets.
  - It asserts non-empty row counts, and records the base/OFF/ON producer ids.
  - Output goes to `image-compare.json`.

```sh
cd mcp-server
uv run pytest tests/slices/test_s26_rails_jobs.py -q
uv run pytest tests/slices/test_s26_*.py tests/slices/test_s25_*.py tests/slices/test_s24_*.py tests/slices/test_s07_*.py tests/slices/test_s09_*.py tests/slices/test_s10_*.py tests/slices/test_s12_storage.py tests/slices/test_s16_*.py tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/slices/test_s21_*.py tests/slices/test_s22_*.py tests/slices/test_s37_*.py tests/test_thesis_negatives.py tests/test_export.py tests/test_schema_epoch.py -q
uv run --frozen pytest -q --deselect <test_s08 nonregular_pack node id>
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/
uv run --frozen mypy src/
uv run --frozen ruff check ../scripts/ --exclude fingerprint-fixture && uv run --frozen ruff format --check ../scripts/ --exclude fingerprint-fixture && uv run --frozen mypy ../scripts/
```

## Confidence
To be computed with `ramza-score`. Most of the machinery is the merged, verified S24/S25 pattern:
- an opt-in producer; probe-gated purge plus recompute; `dropped`;
- `validate_records`; the CLI registry; the no-exec gates.

The new risk is concentrated in three places, each gated on Day 1 with a STOP RULE:
- locating refs sites (span plus owner);
- grammar shapes for arguments and parameters;
- the absence of a carve-out (both relations always carry a target when resolved).

## Rejected Alternatives
- **Parameter entities through QUERIES.** This changes `queries_sha256` and the native extraction for all Ruby graphs, needs symbols projections (:6430), and widens MCP-visible entities.
- **Widening `rails_models` or `rails`.** Their fingerprints are closed (evidence.py :811-845), and it would churn their identity and cost.
- **A target-less `async_argument` carrying the parameter in raw_target.** It needs a new carve-out; using the perform target keeps "resolved ⇒ target".
- **Partial mappings on unsupported calls.** These guess positions after a splat or keyword transformation; AC-004 forbids that.
- **Picking Ruby's first lexical candidate, or modelling ancestor lookup.** That needs runtime constant state and ancestor evaluation; AMBIGUOUS or UNRESOLVED is honest.
- **Capturing all Ruby bytes, or a full-tree walk of every file.** It costs O(repo) memory and parse time; the byte regex plus refs location bounds both.
- **Importing helpers from `rails_models`/`rails_routes`.** It couples `adapter_sha256` identities; copy instead.
- **A `call` edge to perform.** The issue requires the boundary not to read as synchronous.

## Risks (ranked)
1. **Site location.** Refs spans or owners may not map to call nodes (comment/string false matches, legacy rows). Mitigation: Day-1 (b) with the STOP/fallback; `sites.unlocated` is counted, never guessed.
2. **A closed-set or validation miss** (EV/CG/D9 cross-record checks) makes an ON build fail to stage. Mitigation: planted corruptions; ON builds through full, incremental, failed-parse and import/export paths; the full suite.
3. **Grammar drift** for `argument_list` and `method_parameters` children in the pinned pack. Mitigation: Day-1 (a) on host and image.
4. **False INFERRED from Ruby constant lookup**: ancestor-chain constants, reopened classes, `Class.new` blocks. Mitigation: existence over all namespaces, `Module.nesting` with the refs cross-check, `unbound_namespace`, and docs.
5. **Producer-id churn** against `0194758` for all graphs: accepted (D11). G-IMAGE, G-ROUTES and G-MODELS prove the substance is unchanged.
6. **Fidelity and volume.** `def perform(*args)` is common in Sidekiq (→ unsupported_signature), and custom bases give `target_not_recognized_job`. Mitigation: documented.
7. **S10 interplay** across files (a job file edit with the enqueue file unchanged). Mitigation: D10 and S10 (iii); the worst case is the safe global recompute.
8. **Cost.** A byte-regex scan of every Ruby file ON; re-parsing matching files; up to (MAX_SITES+1)×17 occurrences toward impact admission. Mitigation: caps, `truncated`, docs.
9. **The 3-day timebox.** Mitigation: the cut order. At the limit, report the completed checks and a narrow follow-up.
