---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T18:00:00Z
slice_id: ACI-S25
issue: https://github.com/Rynaro/atlas-aci/issues/46
---
# ACI-S25 — Rails: connect explicit associations and migration table references

## Scope

Implement issue #46 on base `f409094` (main after PR #92; S01–S24, S29, S31, S32 and S37 merged), branch `aci-s25`.
- Live marker `<!-- atlas-aci:slice:ACI-S25 -->` verified. There is no prior S25 receipt or execution state.
- Criteria bytes hash to `49fa5448…a81`, equal to the contract table (7 product ACs plus RUN-01/02).
- **Dependency ACI-S24 is PRESENT.**
  - `.spectra/state/aci-s24.execution.json` is COMPLETE with output `94c44a1`.
  - The base contains `frameworks/{__init__,rails_routes}.py`, the codegraph/evidence hooks below, `index --frameworks`, the S24 tests and `docs/frameworks/rails.md`.
- Rightsize is `lite` (score 4). An independent checker is still run by the orchestrator.

Observed facts (line numbers on `f409094`):
- **`frameworks/__init__.py`:**
  - :15 `REGISTRY = {"rails"}`, :17 `REQUIRED_LANGUAGE`.
  - :20-36 `normalize`. It imports nothing from codegraph.
- **`frameworks/rails_routes.py`:**
  - :153 `literal()`, :389-418 probe/purge on `$.fingerprint.rails`, :454-545 `refresh`, :548 `record_ids`, :658-733 `validate_records` (:676 allows ≤1 producer).
  - A producer whose only fingerprint key is `rails_models` is invisible to all of these.
- **`storage/evidence.py`:**
  - Relation sets are at :45-75. `ASSERTION_RELATIONS` (:75) is the only gate on assertion relations (:483). Producer branches are at :287-295.
  - Occurrence kinds are a closed set at :407-418. The kind↔prefix pairing is at :428-444.
  - Assertion validity is at :512-526. EXTRACTED/INFERRED ⇒ a target, no candidates and no reason. Otherwise ⇒ no target, a reason, and candidates iff AMBIGUOUS.
  - `_validate_rails_fingerprint` :755-771.
  - `validate_database` :812-920:
    - :823 kinds whose entity must be a file;
    - :844 rails evidence discrimination;
    - :869-880 rails assertion discrimination;
    - :899 generic rule: the source's path/language equal the occurrence's.
  - Builders: `occurrence` :1017, `provenance` :1046, `assertion` :1064.
- **`codegraph.py`:**
  - :35-36 imports; :876-888 `__init__(…, frameworks=())`.
  - :2571-2576 predecessor `dropped`.
  - :3170 `routes_capture`, set at :3212 after hashing and before every `continue`.
  - :3508 `all_observed`; :3526 `dep.snapshot` (current).
  - :3544 `_resolve_edges`, which in full mode deletes all assertions and otherwise only the assertions of the selected paths (:4565-4577).
  - :3548 `rails_routes.refresh`; :3556 `_validate_evidence_projections`; :3558 `dep.witness`; :3576 `_snapshot_binding`.
  - Projection validation: :6406 every non-file entity needs a definition projection; :6410-6413 role skip; :6432 relation skip; :6451 `rails_routes.validate_records`.
  - Freshness of active occurrences: :6042-6057 (indexed observation, same hash and language).
- **Ruby symbols:**
  - `QUERIES["ruby"]` :169-171 captures the whole `class` node as `@def.class`.
  - :3834-3851: the class `definition` occurrence spans the class node; `symbols` rows carry `entity_id`, `occurrence_id` and `binding.namespace` (enclosing names plus the declared name).
  - `class ::X` is not captured (#match requires a leading capital).
- **Query consumers:**
  - `query/indexed.py` traverses only `ev.RELATIONS`.
  - `diff.py:115` and `retrieval.py:94-102` read only `definition` occurrences.
  - `architecture/checker.py` reads only `import`.
  - `dependencies.validate_outputs` reads refs only.
  - `query/impact.py:150` counts all occurrences (limit 250000).
- **S10:** `dependencies.snapshot` :82-159 digests each file's assertions into its unit `output`; `plan` :179-239.
- **Strict-JSON precedent:** `architecture/rules.py:191-260` (`_strict_pairs`, `_refuse_constant`, `_check_nesting` byte scan, BOM, UTF-8).
- **CLI:** `__main__.py:161-189` normalizes in the `index` body (UsageError, exit 2, before `_open_graph`). `_open_graph` :35-39 maps ValueError to exit 1.
- **Pins:** tools JSON sha `94f9567a…ac56` / 1631472 bytes; 3436/3448/797/7730. CI runs Python 3.14; the dev and production images run 3.12.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)
1. **Separate adapter `rails_models`.**
   - Registry `{"rails","rails_models"}`, both requiring ruby.
   - Own producer key `rails_models` and own manifest key `rails_models`.
   - `rails_routes.py` is NOT edited; `literal` is copied.
   - With `--frameworks rails` alone, S24 route facts keep their identity and cost.
   - The two adapters can be combined, and each purges and recomputes only its own records.
2. **Option B for tables.**
   - `model_table` and `migration_mentions` are target-less EXTRACTED assertions that carry the table in canonical `raw_target`, through relation-gated carve-outs in evidence.py validity and the projection loop.
   - No entity kind is added. Tables are not graph entities.
   - `association` targets class entities: INFERRED, AMBIGUOUS or UNRESOLVED.
3. **Vocabulary placement.**
   - The three relations go only into `ASSERTION_RELATIONS`.
   - One occurrence kind, `rails_model`, with role prefix `rails_model:`.
   - Discrimination: fingerprint key == occurrence kind == relation set.
4. **Associations:** D4/D5 below. No pluralization or inflection. AMBIGUOUS is reported instead of Rails's "deepest wins".
5. **Tables:** a literal `self.table_name =` or the operator map `index --rails-table-map FILE`, which requires `rails_models` (D6/D7).
6. **Migrations:** `db/migrate/*.rb` only; literal create_table, add_column and remove_column; every other statement is a closed diagnostic (D4.3).
7. **Invalidation:** the S24 pattern: probe-gated global purge plus recompute, ids join `dropped`, and a bounded file capture before every `continue`.
8. **AC-005:** the declaration span plus the generation's snapshot binding. Map facts use the class span plus `map_sha256`.
9. **AC-007:** S24 spies plus sqlite3/socket guards and the AST ban. ASCII only, iterative walks, no `\d`/`\w`/`$`.
10. **Scope amendment and invariants:** as in the gates. Producer churn is accepted (S16/S21/S24 precedent).

### In
1. `frameworks/rails_models.py`, new: vocabularies, a copied `literal`, `scan_models`, `scan_migrations`, `parse_table_map`, `Resolver`, `refresh`, `record_ids`, `validate_records`.
2. `frameworks/__init__.py`: the registry plus `REQUIRED_LANGUAGE`.
3. `codegraph.py` CG1-CG8; `storage/evidence.py` EV1-EV6; `__main__.py` `--rails-table-map`.
4. Fixture `tests/fixtures/rails_models/{project/**,table-map.json,provenance.json}`.
5. Tests: `test_s25_rails_models.py` (ACs), `test_s25_isolation.py`, `test_s25_support.py`, plus the two-line `test_s24_rails_routes.py` edit.
6. Docs: a section in `docs/frameworks/rails.md`, a CHANGELOG `[Unreleased]` entry and a README pointer. `.spectra/evidence/aci-s25/compare_graphs.py`.

### Out
- Database connections, migration execution, `db/schema.rb`/`structure.sql` and live schema reconstruction.
- Pluralization, inflectors, polymorphic/through/habtm associations, callbacks, scopes, concerns, STI binding, `with_options`, SQL dataflow and migration safety.
- Engines (`*/app/models` outside the repo root), `table_name_prefix`/suffix, and `def self.up` migrations.
- MCP exposure: no op, enum or `edges` change; no new entity kind or generation kind.
- Edits to the gated paths listed in G-SRC, including `rails_routes.py`.

## Approach

### Decisions

**D1 Activation and identity.**
- `CodeGraph.__init__(…, frameworks=(), rails_table_map: bytes | None = None)`, the new parameter last. Right after `normalize` (:888):
  - a map without `"rails_models" in self.frameworks` raises `ValueError("Rails table map requires framework adapter rails_models")`;
  - otherwise `self._rails_table_map = rails_models.parse_table_map(raw)`, which raises a `TableMapError(ValueError)`. Both happen before any directory or graph write.
- CLI: `index --rails-table-map PATH` (`click.Path(exists=True, dir_okay=False, path_type=Path)`). In the `index` body, after `normalize`:
  - no `rails_models` → `click.UsageError`;
  - read with `path.open("rb").read(MAX_MAP_BYTES + 1)` (`__main__` is the only reader, never the adapter);
  - `parse_table_map(raw)`; any `ValueError` or `OSError` → `click.UsageError`, exit 2, no graph directory;
  - then `_open_graph(…, rails_table_map=raw)`.
- `--frameworks` help lists `rails, rails_models`.
- Identity lives in the `rails_models` producer fingerprint and manifest key: `adapter_sha256` of `rails_models.py` and `table_map_sha256 = sha256(raw map bytes)` or null.
  - None of it goes into `configuration`, `generation_inputs` or `_producer_inputs`.
  - Whitespace-only map edits change identity, by design: the artifact is its bytes.

**D2 Record shapes.**
- **Producer** (path NULL; emitted iff ≥1 record):
  `ev.producer({"rails_models":{"adapter":"rails-models.v1","adapter_sha256":…,"graph_producer_id":self._capture_producer["id"],"table_map_sha256":h|null}})`, with basis `parsed`.
- **Occurrence:** `ev.occurrence(node, P, "ruby", source, S, "rail_model"→"rails_model", role, source_hash)`.
  - P is the declaring file and S is the assertion's source entity, so occurrence `entity_id == source`.
  - For association/model_table, S is the declaring model's class entity; for migration_mentions, S is the migration file entity.
  - The span is the exact statement node, except map records, which use the class node.
- **Evidence:** `ev.provenance(occ, producer_id)`. **Assertion:** `ev.assertion(S, target, relation, evidence_id, resolution, raw_target, candidates, reason)`. All three are stored with path P.

Roles and canonical `raw_target` (via `ev.canonical`, closed key sets):

| Role | Relation | raw_target |
| --- | --- | --- |
| `rails_model:association:<kind>` (kind ∈ belongs_to, has_one, has_many) | association | `{"class_name":<literal as written>,"kind":k,"mapping":"rails_class_name.v1","name":n}` |
| `rails_model:association:unsupported:<code>` | association | `{"construct":c,"kind":<method>,"mapping":"unexpanded"}` |
| `rails_model:table:literal` | model_table | `{"source":"literal","table":t}` |
| `rails_model:table:operator_map` | model_table | `{"map_sha256":h,"source":"operator_map","table":t}` |
| `rails_model:table:unsupported:<code>` | model_table | `{"construct":c,"mapping":"unexpanded"}` |
| `rails_model:migration:<op>` (op ∈ create_table, add_column, remove_column) | migration_mentions | `{"op":"create_table","table":t}` or `{"column":c,"op":op,"table":t}` |
| `rails_model:migration:unsupported:<code>` | migration_mentions | `{"construct":c,"mapping":"unexpanded"}` |

| Relation / case | resolution | target | candidates | reason |
| --- | --- | --- | --- | --- |
| association, exactly 1 model entity | INFERRED | model class entity | [] | null |
| association, ≥2 model entities | AMBIGUOUS | null | sorted ids | `ambiguous_class_name` |
| association, 0 models, a candidate names a non-model class | UNRESOLVED | null | [] | `target_not_model` |
| association, no indexed class | UNRESOLVED | null | [] | `class_not_found` |
| association diagnostic | UNRESOLVED | null | [] | `unsupported_association` |
| model_table, sole literal / sole unambiguous map entry | EXTRACTED | null | [] | null |
| model_table, literal ≠ map entry (both records) | UNRESOLVED | null | [] | `conflicting_table_mapping` |
| model_table, ≥2 literal statements (each) | UNRESOLVED | null | [] | `ambiguous_table_name` |
| model_table, map key → ≥2 model entities (one per entity) | UNRESOLVED | null | [] | `ambiguous_model` |
| model_table diagnostic | UNRESOLVED | null | [] | `unsupported_table_name` |
| migration_mentions, supported op | EXTRACTED | null | [] | null |
| migration_mentions diagnostic | UNRESOLVED | null | [] | `unsupported_migration_op` |

- An association is never EXTRACTED; EV4 enforces this.
- model_table and migration_mentions are never INFERRED or AMBIGUOUS and never carry a target.

Manifest key `rails_models` (canonical; written iff the flag is on):
`{"format":"atlas-rails-models.v1","adapter":"rails-models.v1","adapter_sha256":…,"table_map_sha256":h|null,`
`"files":{"models":n,"migrations":n,"failed":n,"limited":n},"classes":{"models":n,"migrations":n,"unbound":n},"declarations":n,`
`"facts":{"association":{"AMBIGUOUS":n,"INFERRED":n,"UNRESOLVED":n},"migration_mentions":{"EXTRACTED":n,"UNRESOLVED":n},"model_table":{"EXTRACTED":n,"UNRESOLVED":n}},`
`"map":{"entries","bound","confirmed","conflicting","ambiguous","suppressed","unmatched"}|null,"truncated":bool,"producer_id":id|null}`.

**D3 Closed vocabularies and limits** (module constants; a test pins the exact sets).
- `ASSOCIATION_METHODS = {belongs_to, has_one, has_many, has_and_belongs_to_many}`; `ASSOCIATION_KINDS` = the first three.
- `ASSOCIATION_CONSTRUCTS` (17): `has_and_belongs_to_many, through, source, source_type, polymorphic, as, block, splat, scope_argument, extra_arguments, non_literal_name, unsupported_option, implicit_class_name, symbol_class_name, computed_class_name, malformed_class_name, declaration_limit`.
- `ALLOWED_ASSOCIATION_OPTIONS` (12, accepted but not recorded): `class_name, foreign_key, primary_key, inverse_of, dependent, optional, required, touch, autosave, validate, counter_cache, strict_loading`.
- `TABLE_CONSTRUCTS` (5): `symbol_table_name, computed_table_name, malformed_table_name, table_name_method, declaration_limit`.
- `MIGRATION_OPS = {create_table, add_column, remove_column}`.
- `MIGRATION_STATEMENT_CONSTRUCTS` (24): `change_table, drop_table, rename_table, rename_column, add_reference, remove_reference, add_belongs_to, remove_belongs_to, add_index, remove_index, add_timestamps, remove_timestamps, add_foreign_key, remove_foreign_key, change_column, change_column_default, change_column_null, create_join_table, drop_join_table, execute, reversible, revert, up_only, remove_columns`.
- `MIGRATION_OP_CONSTRUCTS` (8): `block, splat, missing_arguments, extra_arguments, non_literal_table, non_literal_column, interpolated_literal, malformed_literal`.
- `MIGRATION_OTHER_CONSTRUCTS` (3): `conditional, other_statement, declaration_limit`.
- `REASONS` (9): `ambiguous_class_name, target_not_model, class_not_found, unsupported_association, conflicting_table_mapping, ambiguous_table_name, ambiguous_model, unsupported_table_name, unsupported_migration_op`.
- Labels: `{rails_class_name.v1, unexpanded}`. Table sources: `{literal, operator_map}`.
- Limits:
  - `MAX_FILES=10000` (captured model plus migration files);
  - `MAX_DECLARATIONS=20000`;
  - `MAX_MAP_BYTES=1048576`, `MAX_MAP_ENTRIES=10000`, `MAX_MAP_NESTING=4`;
  - `MAX_NAME_BYTES=128`, `MAX_CLASS_NAME_BYTES=256`, `MAX_TABLE_BYTES=64`.
- Regexes, ASCII-only and used with `fullmatch`:
  - `CLASS_NAME_RE=r"(::)?[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"`
  - `MODEL_KEY_RE=r"[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"`
  - `NAME_RE=r"[a-z_][a-z0-9_]*"`
  - `IDENT_RE=r"[A-Za-z_][A-Za-z0-9_]*"` (tables and columns)
  - `MIGRATION_BASE_RE=rb"ActiveRecord::Migration(\[[0-9]+\.[0-9]+\])?"`
  - `MODEL_BASES={b"ApplicationRecord",b"ActiveRecord::Base",b"::ActiveRecord::Base"}`
- Paths:
  - `is_model_path(p)`: `p.startswith("app/models/") and p.endswith(".rb") and not p.startswith("app/models/concerns/")`;
  - `is_migration_path(p)`: `p.startswith("db/migrate/") and p.endswith(".rb") and "/" not in p[len("db/migrate/"):]`.

**D4 Extraction.** Pure and iterative, with no recursion and no generic tree walk.

D4.0 Containers.
- An explicit stack is seeded with `tree.root_node.named_children`. Only `module` and `class` nodes are expanded, by pushing the named children of their `body` field; `comment` and `heredoc_body` are skipped.
- `method`, `singleton_method`, `singleton_class`, blocks, `begin` and conditionals are never entered.
- Collected class nodes are sorted by `start_byte`.
- A class's superclass text is the bytes of the first named child of its `superclass` field.

D4.1 Model classes.
- Applies in a model file to a class whose superclass text ∈ `MODEL_BASES`.
- The class is bound to its entity through `Resolver.by_span[(path, class.start_byte, class.end_byte)]` (D5). With anything other than exactly one row it is unbound: counted and skipped.
- Its direct body statements (body named children minus comment/heredoc_body) are classified in order:
  - a `call` without receiver whose method ∈ `ASSOCIATION_METHODS`, or an `identifier` in it → D4.2;
  - a table statement → D4.4;
  - anything else is ignored, with no record. Validations, callbacks, scopes and `with_options` blocks are silent.

D4.2 Association checks; the first failure wins:
1. method `has_and_belongs_to_many` → `has_and_belongs_to_many`;
2. a pair with a `hash_key_symbol` key in the fixed order `through, source, source_type, polymorphic, as` → that key;
3. a `block` field → `block`;
4. any `splat_argument`/`hash_splat_argument`/`block_argument` → `splat`;
5. positionals (non-pair, non-comment):
   - none → `non_literal_name`;
   - exactly 2 with the second of type `lambda` → `scope_argument`;
   - any other count ≠ 1, or a `hash` positional → `extra_arguments`;
6. the name is a `simple_symbol` (text minus its leading `:`) or (L) with `MAX_NAME_BYTES`, fullmatching `NAME_RE`; otherwise `non_literal_name`;
7. a pair with a `=>` token, a key that is not `hash_key_symbol`, a duplicate key, or a key outside `ALLOWED_ASSOCIATION_OPTIONS` → `unsupported_option`;
8. no `class_name` → `implicit_class_name`;
9. the `class_name` value:
   - `simple_symbol`/`delimited_symbol` → `symbol_class_name`;
   - a `string` with `interpolation`, or any non-string node → `computed_class_name`;
   - (L) failure, or not fullmatching `CLASS_NAME_RE` → `malformed_class_name`;
10. otherwise the association is supported and resolved by D5.

(L) is the copied S24 `literal` rule: the bytes between matching `"`/`'` delimiters, `string_content` children only, non-empty, within the limit, valid UTF-8.

D4.3 Migrations.
- In a migration file, a class whose superclass text fullmatches `MIGRATION_BASE_RE`; other classes are ignored.
- Its direct body `method` nodes named `change`, `up` or `down` are read; `singleton_method` is ignored.
- Each direct statement of the method body (`body` field named children minus comment/heredoc_body) is classified:
  - a receiverless `call` with method ∈ `MIGRATION_OPS` → op checks;
  - a receiverless `call` or `identifier` ∈ `MIGRATION_STATEMENT_CONSTRUCTS` → that code;
  - type ∈ {if, unless, if_modifier, unless_modifier, conditional, case} → `conditional`;
  - else → `other_statement`.
- Op checks; the first failure wins:
  1. add_column/remove_column with a `block` → `block` (a create_table block is allowed and never read);
  2. splat/hash_splat/block_argument → `splat`;
  3. positional count: below the minimum (create_table 1, add_column 3, remove_column 2) → `missing_arguments`; above the maximum (1, 3, 3) → `extra_arguments`;
  4. table: `simple_symbol` (text[1:]) or (L) with `MAX_TABLE_BYTES` fullmatching `IDENT_RE`:
     - another node type → `non_literal_table`;
     - interpolation → `interpolated_literal`;
     - otherwise → `malformed_literal`;
  5. the same check for the column (add/remove), with `non_literal_column`;
  6. otherwise → a mention.
- Option pairs (`force:`, `null:`, `default:`) are never recorded. The type argument is not inspected.

D4.4 Table statements, direct class-body only:
- an `assignment` whose `left` is a `call` with receiver `self` and method `table_name`:
  - right side (L) fullmatching `IDENT_RE` → a literal;
  - a symbol → `symbol_table_name`;
  - an interpolated string or any other node → `computed_table_name`;
  - a (L) or regex failure → `malformed_table_name`;
- an `operator_assignment` with the same left side → `computed_table_name`;
- a `singleton_method` on `self` named `table_name` → `table_name_method`.

D4.5 Limit.
- One global counter runs over files in sorted path order, classes by `start_byte`, then statements.
- When it reaches `MAX_DECLARATIONS`, the next statement becomes one `declaration_limit` diagnostic on its own area's relation, and scanning stops with `truncated=true`.
- A class whose body scan was cut gets no D6 table decision and no map record.
- Captured files beyond `MAX_FILES` increment `files.limited` and set `truncated`.
- Totals are bounded: assertions ≤ `MAX_DECLARATIONS + 1 + MAX_MAP_ENTRIES`.

**D5 Class-name resolution** (`Resolver`, one SQL scan per build).
- `SELECT s.path, s.entity_id, s.binding, o.payload FROM symbols s JOIN occurrences o ON o.id=s.occurrence_id WHERE s.lang='ruby' AND s.kind='class' ORDER BY s.path, s.line_start, s.id` builds:
  - `by_span[(path,start_byte,end_byte)] → [(entity_id, namespace)]`;
  - `all_classes` = the set of namespaces.
- `models[namespace] → set(entity_id)` covers bound D4.1 classes only, so model classes outside `app/models/**`, concerns, STI subclasses and other bases are not models.
- For a class name `X` declared in model namespace `M` (the `binding.namespace`):
  - if `X` starts with `::`: `cands = [X[2:]]`;
  - otherwise: `cands = [M+"::"+X] + [P+"::"+X for each proper prefix P of M, deepest first] + [X]`, de-duplicated with order kept. This is the Rails `compute_type` candidate list.
- `ids = sorted(∪ models[c])`:
  - 1 → INFERRED;
  - ≥2 → AMBIGUOUS `ambiguous_class_name`;
  - 0 and some `c ∈ all_classes` → `target_not_model`;
  - else → `class_not_found` ("no indexed class", which also covers skip patterns and disabled languages).
- Docs must say that Rails picks the first candidate that names an existing constant (deepest first); Atlas does not pick and reports AMBIGUOUS. A reopened model (≥2 entities) is AMBIGUOUS.

**D6 Table decision per fully scanned bound model class C.**
- Inputs: `U` = its unsupported table statements, `L` = its literal statements, and `M` = the map entry for `C.namespace`, used only if `models[key]` holds exactly one entity.
- Decision:
  1. `U` non-empty: emit only the U diagnostics; the map entry counts as `suppressed`.
  2. Otherwise, `|L| ≥ 2`: each literal is UNRESOLVED `ambiguous_table_name`; the map entry counts as `suppressed`.
  3. Otherwise, `|L| == 1`:
     - with `M ≠ L`: the literal record and a map record (class span) are both UNRESOLVED `conflicting_table_mapping` (`conflicting`);
     - with `M == L`: the literal is EXTRACTED and the map entry counts as `confirmed`;
     - with no `M`: the literal is EXTRACTED.
  4. Otherwise, `|L| == 0` with `M`: one map record, EXTRACTED (`bound`).
- A map key whose `models[key]` has ≥2 entities gets one map record per entity, UNRESOLVED `ambiguous_model` (`ambiguous`, counted per key). Those classes run steps 1-3 with no `M`.
- Keys matching no model (including non-model classes) count only as `unmatched`. No table is ever derived from the class name.

**D7 Map format and parser** (`parse_table_map(raw: bytes) -> TableMap(sha256, models: dict[str,str])`; S31 recipe copied; stdlib only).
- `TableMapError(ValueError)` carries a closed `reason` ∈ {`too_large, invalid_json, unsupported_format, schema, too_many_entries, malformed_model, malformed_table`}. Messages never echo document text.
- Check order:
  1. `len(raw) > MAX_MAP_BYTES` → too_large.
  2. Strict parse → invalid_json. This covers the `_check_nesting` byte scan at depth 4, a BOM, `decode("utf-8")`, `object_pairs_hook` duplicate rejection at every level, and `parse_constant` NaN/Infinity rejection; `except Exception`.
  3. Not an object → schema. A `format` that is present and ≠ `atlas-rails-table-map.v1` → unsupported_format.
  4. Keys ≠ {format, models}, or `models` not an object → schema.
  5. Entries > `MAX_MAP_ENTRIES` → too_many_entries.
  6. In sorted key order: a key that is not a str fullmatching `MODEL_KEY_RE` within 256 bytes (no leading `::`, ASCII) → malformed_model; a value that is not a str fullmatching `IDENT_RE` within 64 bytes → malformed_table.
- An empty `models` is allowed.

**D8 `storage/evidence.py` edits** (the complete list).
- **EV1** (after :75):
  - `RAILS_ASSOCIATION_RELATION="association"`, `RAILS_TABLE_RELATION="model_table"`, `RAILS_MIGRATION_RELATION="migration_mentions"`;
  - `RAILS_MODEL_RELATIONS` = the three; `RAILS_TARGETLESS_RELATIONS = {model_table, migration_mentions}`;
  - `RAILS_MODEL_KIND="rails_model"`, `RAILS_MODEL_ROLE_PREFIX="rails_model:"`, `RAILS_MODELS_ADAPTER="rails-models.v1"`;
  - `ASSERTION_RELATIONS = REFERENCE_RELATIONS | {COVERAGE_RELATION, RAILS_RELATION} | RAILS_MODEL_RELATIONS`, with an S21/S24-style comment.
  - `RELATIONS`, `REFERENCE_RELATIONS`, `EDGE_RELATIONS`, `FIELDS` and `COLUMNS` are untouched.
- **EV2** (:293): `if set(fingerprint) == {"rails_models"}: _validate_rails_models_fingerprint(...); return`.
- **EV3** (new, after :771):
  - basis `parsed`; keys == {adapter, adapter_sha256, graph_producer_id, table_map_sha256};
  - adapter == `RAILS_MODELS_ADAPTER`; the first two values are hashes; `table_map_sha256` is None or a hash;
  - never compared to the running adapter.
- **EV4:**
  - :407-418 adds `RAILS_MODEL_KIND`;
  - after :436: `(kind == RAILS_MODEL_KIND) == role.startswith(RAILS_MODEL_ROLE_PREFIX)`, and for the kind: state `source`, a hash `entity_id`, language `ruby`;
  - after :494: association ⇒ resolution ∈ {INFERRED, AMBIGUOUS, UNRESOLVED}; a targetless relation ⇒ resolution ∈ {EXTRACTED, UNRESOLVED}, target None, no candidates.
- **EV5** (:512-518 carve-out): the resolved-endpoints rule becomes `(relation in RAILS_TARGETLESS_RELATIONS or target is not None) and not candidates and reason is None`. Every other relation keeps "resolved requires target"; this is tested for every other member of `ASSERTION_RELATIONS`.
- **EV6** (`validate_database`):
  - after :826: `(kind == RAILS_MODEL_KIND) <= (entity kind in {"class","file"})`;
  - after :848: `(fp == {"rails_models"}) == (occ kind == RAILS_MODEL_KIND)`;
  - after :880: `(relation in RAILS_MODEL_RELATIONS) == (fp == {"rails_models"}) == (occ kind == RAILS_MODEL_KIND)`, and for those `source.id == occurrence.entity_id` and `source.kind == ("file" if relation == migration_mentions else "class")`. The generic :899 location rule still applies.
- Deep shape checks live in `rails_models.validate_records`.

**D9 `codegraph.py` edits** (the complete list).
- **CG1** (:36): `from atlas_aci.frameworks import rails_models`.
- **CG2** (:876-888): the `rails_table_map` parameter plus D1 validation and parsing.
- **CG3:** `model_capture: dict[str, tuple[bytes,str]] = {}` and `model_limited = 0` beside :3170. After :3213:
  - `if "rails_models" in self.frameworks and lang == "ruby" and (is_model_path(rel) or is_migration_path(rel)):` capture while `len < MAX_FILES`, else `model_limited += 1`.
  - Flag-gated, so OFF and rails-only builds hold no extra bytes. It runs before every `continue`, so cache hits are covered.
- **CG4:** after :3555, before :3556:
  `rails_models.refresh(conn, enabled="rails_models" in self.frameworks, captures=model_capture, limited=model_limited, table_map=self._rails_table_map, observations=all_observed, get_parser=get_parser, graph_producer_id=self._capture_producer["id"])`.
- **CG5** (:2575): `| rails_models.record_ids(full)`.
- **CG6** (:6411): add `ev.RAILS_MODEL_ROLE_PREFIX` to the skip tuple.
- **CG7** (:6432): `{ev.COVERAGE_RELATION, ev.RAILS_RELATION} | ev.RAILS_MODEL_RELATIONS` → `continue`.
- **CG8** (:6451): `rails_models.validate_records(conn)`.
- **Not edited:** `_producer_inputs`, `_build_inputs`, the :6118 SQL, generation kinds and `rails_routes.refresh`.

`refresh` sequence (staging transaction):
1. If `has_records(conn)` (the `rails_models` manifest key, or `SELECT 1 FROM producers WHERE json_extract(payload,'$.fingerprint.rails_models') IS NOT NULL LIMIT 1`), purge:
   - assertions with relation ∈ the 3-set;
   - evidence whose producer is a rails_models producer;
   - occurrences of kind `rails_model`;
   - those producers;
   - the manifest key.
2. If the flag is off, return None.
3. `current` = `type == "observation"` records keyed by path. For each captured path, in sorted order: the observation must exist and be `indexed`, the capture hash must equal `observed_source_sha256`, and the file entity must exist in `entities`; otherwise `files.failed += 1`.
4. If ≥1 eligible file: `Resolver(conn)` and `parser = get_parser("ruby")` (never called otherwise). Per file, `parse`; `has_error` → failed. Then `scan_models` or `scan_migrations` (D4).
5. Model index → map binding (D6) → association resolution (D5).
6. If ≥1 record, `ev.put` the producer; then per declaration, put the occurrence, evidence and assertion (path P).
7. Write the manifest.

**D10 Invalidation and S10** (the S24 D7 argument, generalised; honest wording is binding).
- rails_models facts are a global function of (captured model and migration bytes, Ruby `symbols`, the map bytes, the adapter). They are rebuilt by the adapter on every enabled build, not republished per file through the S10 plan.
- S10 contributes `remove_path` for changed or removed files, and the `affected` classification.
- The ids join `dropped`, so a later parse failure never resurrects them through `_retained_closure`.
- `dep.snapshot` (:3526) runs before `refresh`:
  - an unchanged path still carries the predecessor rails_models assertions, equal to the witness (written after refresh), so it is reused;
  - a changed file was cleared by `remove_path`, so it is affected;
  - a changed class elsewhere changes the Ruby bucket, so it is affected;
  - a map change with sources unchanged gives `affected_units 0`, then a global recompute;
  - flag toggles keep both digests consistent (ON→OFF purges after the snapshot).
- `unavailable_prior_outputs` is therefore unreachable through rails_models records.

**D11 `rails_models.validate_records(conn)`** (build, predecessor, import, export). Every failure is `ValueError("Invalid evidence: rails_models …")`; KeyError, TypeError, IndexError and AttributeError are wrapped.
- **Producer:** ≤1 rails_models producer; it is cited by ≥1 evidence; its `graph_producer_id` exists.
- **Occurrence:** every `rails_model` occurrence:
  - is cited by exactly one evidence from that producer, and that evidence by exactly one assertion with relation in the 3-set;
  - has a path matching `is_model_path` or `is_migration_path` (model areas use model paths; migration uses migration paths);
  - has a role area (`association:`/`table:`/`migration:`) that agrees with the relation.
- **Assertion:**
  - source == the occurrence entity; the entity kind is class/ruby, or file;
  - raw_target is a canonical object of the closed shape for its role;
  - kind, op, construct and source are in their vocabularies; name/class_name/table/column fullmatch their regexes;
  - an `operator_map` `map_sha256` equals the producer's non-null `table_map_sha256`;
  - the (role, resolution, reason) triple is in the D2 table;
  - INFERRED targets and AMBIGUOUS candidates (≥2) are class/ruby entities.
- **Count:** ≤ `MAX_DECLARATIONS + 1 + MAX_MAP_ENTRIES`.
- **Hash:** never compared to the running adapter (S24 condition 4).

**D12 Identity and churn.**
- Editing `codegraph.py` and `evidence.py` changes the native producer id again. Every evidence, assertion, link, generation and snapshot id changes against `f409094` for all graphs, and existing indexes rebuild in full on the first `--since`.
- Entity ids, occurrence ids and projection row contents (minus producer-derived link ids) are unchanged; G-IMAGE proves this.
- On the same code, ON minus rails_models-owned ids == OFF.
- S24 route records under `--frameworks rails`:
  - occurrence ids, normalized assertions, the `adapter_sha256` and the `rails_routes` manifest value (minus `producer_id`) are unchanged against base;
  - only producer-derived ids churn (G-ROUTES).

**D13 No execution and portability (AC-007).**
- `rails_models.py` passes S24's AST gate unchanged:
  - allowed imports `__future__, hashlib, json, re, sqlite3, dataclasses, pathlib, typing, collections.abc, atlas_aci.storage(.evidence)`;
  - no eval/exec/compile/`__import__`/open/getattr/setattr/globals/locals/vars/breakpoint/input/`__builtins__`;
  - no system/popen/spawn*/exec*/fork* attributes;
  - one `Path(__file__).read_bytes()`.
- The S25 test also bans the attributes `connect`, `create_connection` and `urlopen`, requires ASCII source, forbids self-calls, and forbids `\d`/`\w` and `$` in regex literals.
- Repository Ruby is only parsed by tree-sitter, from bytes the build already captured. The map is parsed from caller-supplied bytes.

### Limitations (`docs/frameworks/rails.md`, new section "Models, associations and tables (ACI-S25)")
- **Scope of reading:**
  - only root `app/models/**` (not concerns) and `db/migrate/*.rb`;
  - only direct class-body statements of classes whose base is literally `ApplicationRecord`/`ActiveRecord::Base`/`::ActiveRecord::Base`;
  - `ApplicationRecord` itself counts;
  - STI subclasses, `with_options`, concerns, conditionals and methods are silent.
- **Associations:**
  - a supported association needs a literal `class_name:`; implicit names are diagnostics, with no pluralization;
  - AMBIGUOUS where Rails would choose the deepest candidate.
- **Tables:**
  - tables are not graph entities and never traversable; inspect them via export and the `rails_models` manifest key;
  - mappings come only from a literal `self.table_name` or the operator map; `table_name_prefix` is ignored.
- **Migrations:**
  - migration mentions are source statements, not proof a migration ran, and not the live schema;
  - `db/schema.rb` and `def self.up` are not read;
  - option values are not recorded.
- **Cost and failure:**
  - a model or migration file that fails to parse yields no facts for that generation;
  - every ON build re-parses the captured files outside `cache_stats`;
  - `rails_model` occurrences count toward impact diff admission.
- **Identity:** producer-id churn (D12).

### Work plan (timebox 3 engineering days)
**Day 1 AM — qualification, before any product edit.** A scratch probe runs on the host and in the 3.12 dev image, with output in `.spectra/evidence/aci-s25/qualification.json`:
- **(a) Grammar.** Parse every fixture model and migration file with the pinned `get_parser("ruby")`; `has_error` must be false. Record:
  - `class` fields `name`/`superclass`/`body`, and the superclass inner node type for the three bases and `Migration[7.1]`;
  - `assignment` left/right for `self.table_name = "x"` and `:x`; `operator_assignment`; `singleton_method` object/name;
  - for association calls: `argument_list` children, `pair` key `hash_key_symbol`, the `=>` token, `simple_symbol` text including `:`, `lambda`, `string` with `interpolation`, and the block field;
  - the `method` body field for `def change`; create_table `do_block`; the `heredoc_body` placement.
- **(b) Binding.** A base-code build of the fixture:
  - for every class: kind `class`, `binding.namespace` (top-level, module-wrapped, compact `Blog::Tag`, nested, reopened), and definition occurrence `start_byte/end_byte` == the class node span (1:1);
  - `app/models/storage/vault.rb` is unobserved (skip pattern);
  - the `generation_inputs` snapshot key path and `reader.snapshot_id`.
- **(c) Validity.** On base, `ev.validate_record` rejects a target-less EXTRACTED `call`. Then apply EV1/EV5 in a scratch copy:
  - the target-less EXTRACTED record passes only for model_table/migration_mentions;
  - INFERRED without a target still fails for association/routes_to/call;
  - `validate_database` plus `_validate_evidence_projections` (with CG6/CG7) pass on a hand-built DB holding one such record.
- **(d) Flag-off identity baselines.** Base-code digests (compare_graphs) of the S25 and S24 fixtures: OFF and `--frameworks rails`.
- **(e) Base invariants:** tools sha and bytes, 3436/3448/797/7730, and the full `pytest -q` at base.
- **(f) Rails fidelity.** Cite `compute_type` at a pinned Rails tag. If that is not verifiable, the docs avoid precedence specifics beyond "Rails picks one; Atlas reports ambiguity".

STOP RULE:
- (a) Supported fixture lines do not parse without error, the superclass/body cannot be read, or literal vs interpolated strings cannot be told apart → BLOCKED with the node dump. Field or name differences only adapt D4 and are recorded.
- (b) Class nodes do not map 1:1 to definition occurrences → BLOCKED for AC-001/002/005, because the fix needs extractor or codegraph extraction edits outside scope.
- (c) The carve-out needs any edit beyond EV1-EV6/CG6-CG7, or a gated consumer breaks → BLOCKED (re-plan to option C, which needs an orchestrator decision).
- (d)/(e) Base not green → BLOCKED.

**Day 1 PM:**
- `frameworks/__init__.py`;
- `rails_models.py` pure parts (vocabularies, `literal`, scans, `parse_table_map`, candidate list);
- the fixture plus `provenance.json` (sha256 and bytes per file, expected totals);
- pure scan and parser tests.

**Day 2:**
- EV1-EV6, CG1-CG8, `Resolver`, `refresh`, `validate_records`, the CLI;
- `test_s25_rails_models.py` AC-001..007.

**Day 3:**
- `test_s25_isolation.py`;
- the two-line S24 edit;
- docs, CHANGELOG and README;
- gates in the lock-faithful dev image rebuilt from the branch;
- the G-IMAGE/G-ROUTES production-image scenario (orchestrator);
- RUN-01/02.

Cut order (each cut becomes a documented limitation):
1. README/CHANGELOG depth.
2. Export→import round-trip test.
3. G-NOLEAK/G-QUERY MCP comparisons (keep G-MCP and the relation-set pins).
4. `ALLOWED_ASSOCIATION_OPTIONS` reduced to {class_name, foreign_key, inverse_of, dependent, optional}.
5. `MIGRATION_STATEMENT_CONSTRUCTS` reduced to decision 6's eight (change_table, drop_table, rename_table, rename_column, add_reference, add_index, execute, reversible); the others become `other_statement`.
6. The both-adapters incremental toggle test (keep the single combined build test).

Never cut:
- the AC tests;
- EV5 and its negative tests;
- purge + recompute + `dropped`;
- G-OFF/G-ONOFF/G-IMAGE/G-ROUTES;
- `validate_records` plus planted corruptions;
- the `_validate_evidence_projections` copy test;
- the map-parser negatives;
- the S10 tests (model delete, map change);
- the no-exec gates, G-SRC, G-MCP.

### Independent checker conditions (binding; override conflicting plan text)
Checker `atlas-s25-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s25.review.json`); this is the validation required by the confidence verdict VALIDATE (82.5). Every condition below is binding and overrides any conflicting plan text.

Orchestrator resolutions (recorded 2026-11-04 under the user's autonomous-execution instruction), stricter option each time: condition 1 - existence is computed over all indexed class and module namespaces, INFERRED only with exactly one existing candidate name carrying exactly one model entity, with a shadow fixture/test; condition 3 - a class is unbound unless its namespace fullmatches MODEL_KEY_RE, AND a scope_resolution-named (compact) class with any enclosing class/module is unbound; condition 4 - emitted map records are capped at MAX_MAP_ENTRIES with `truncated` set, and the validator cap derives from the same constants; condition 7 - the reason is renamed `target_not_recognized_model` now, documented as 'class exists but is not recognised as a model by this adapter', and the AMBIGUOUS rationale says Atlas does not evaluate Rails precedence.

**Findings (check results)**
- **Target-less EXTRACTED carve-out is sound.**
  - Every consumer that dereferences targets is relation-filtered by `RELATIONS` or a relation parameter: `query/indexed.py:19-27`, `traversal.py:255-260`, `architecture/checker.py:106-110` (relation `import` only), `query/validation_coverage.py:38-45`, and `retrieval.py:94` / `diff.py:115` (definition occurrences only).
  - `ev.records` is consumed only inside `codegraph.py`.
  - `validate_database` `:913-920` already tolerates a null target, and `dependencies.snapshot` digests assertion payloads without resolving them.
  - The only real leak points are the CG6 and CG7 skips at `:6410` and `:6432`, which the plan covers.
  - An UNRESOLVED `table_not_entity` alternative would be less honest, so keep option B.
- **Two producers coexist safely.**
  - `$.fingerprint.rails` is an exact JSON key (`rails_routes.py:390,416,673`), so it never matches `rails_models`.
  - `record_ids` uses dict-key membership, and the S24 check at `evidence.py:844-875` stays false for rails_models records.
  - ON/OFF of one adapter independent of the other is sound.
- **Class binding via `symbols.occurrence_id`** is confirmed (`codegraph.py:169-171`, `:3832-3851`), and the class node equals the definition span.
- **S24 tests:** I found no S24 test needing an edit beyond the two lines (`:381` file set, `:418` reads == 2).
  - The S24 fixture has no `app/models`, so rails_models yields zero records there.
  - The `'"rails"' not in text` check at `:929` is unaffected by `"rails_models"`.

**Findings (conditions)**

1. **MAJOR: false INFERRED in D5.**
   - `ids = ∪ models[c]` ignores non-model classes and modules in earlier candidates. Rails `compute_type` picks the first existing candidate, deepest first. If `Blog::Tag` is a PORO and `::Tag` is a model, the plan emits INFERRED to `Tag`, but Rails resolves `Blog::Tag`.
   - Condition (stricter): compute existence over all indexed class and module namespaces, not only models.
     - INFERRED only if exactly one distinct candidate name exists and it has exactly one model entity.
     - If at least two names exist and at least two class entities result, emit AMBIGUOUS `ambiguous_class_name`.
     - Otherwise emit `target_not_model` or `class_not_found`.
   - Add a shadow fixture case and test.
2. **MAJOR: "bounded capture" is bounded only by file count.**
   - Evidence: CG3 retains every captured file's bytes, with no total or per-file byte cap. `Declaration(node, …)` (D4, mirroring `rails_routes.py:299`) keeps every parse tree alive until emission.
   - Condition:
     - Scans return plain span objects holding start/end byte and start/end point, which is all `ev.occurrence` reads (`evidence.py:1035-1040`).
     - Trees are dropped per file.
     - Add `MAX_CAPTURE_BYTES` (total) and a per-file cap, counted in `files.limited` and `truncated`.
     - Add a test that monkeypatches the caps.
3. **MINOR: namespace validity.** `binding.namespace` (`codegraph.py:4082-4086`) joins the raw name text of every enclosing class or module.
   - An uncaptured `class ::X` wrapper yields `::X::Y`. A compact `class A::B` inside `module Z` yields `Z::A::B`, which Ruby may not define.
   - Condition: a class is `unbound` unless its namespace fullmatches `MODEL_KEY_RE`. Stricter: a scope_resolution-named class with any enclosing class or module is also `unbound`. Add a fixture and test.
4. **MINOR: cap versus map records.** `validate_records` caps at `MAX_DECLARATIONS+1+MAX_MAP_ENTRIES` (D11). But `ambiguous_model` emits one record per entity per key, so the total can exceed the cap and make staging raise.
   - Condition: cap emitted map records at `MAX_MAP_ENTRIES` (set `truncated`), or derive the validator cap from the same quantity. Add a test.
5. **MINOR: map parser versus the S31 recipe (`rules.py:204-260`).**
   - The plan omits `_has_surrogate`. A lone-surrogate key hitting `.encode` raises `UnicodeEncodeError`, a `ValueError` subclass whose message echoes the text.
   - Condition:
     - Check the ASCII fullmatch before any `.encode`.
     - Wrap every non-`TableMapError` failure into a fixed-text `TableMapError`.
     - Pin that `format` is required (D7 step 4 is ambiguous), and test a lone surrogate.
6. **MINOR: CLI read.** `click.Path(exists=True)` accepts a FIFO, and `read(MAX+1)` bounds size but not blocking.
   - Condition: open, then `os.fstat` and `S_ISREG` (precedent `providers/coverage_py.py:597`), then read at most `MAX_MAP_BYTES+1`. Use fixed message text so no path is echoed (S31 condition 13), with exit 2 and no graph directory.
7. **MINOR: honest reason wording.**
   - `target_not_model` is shown for a real ActiveRecord model whose base is not one of the three `MODEL_BASES` (for example `Spree::Base`).
   - Condition: document it as "class exists but is not recognised as a model by this adapter". Stricter: rename it to `target_not_recognized_model` now, since the reason set is pinned by a test.
   - Also drop the claim that AMBIGUOUS is needed because of "runtime constant state". Say instead that Atlas does not evaluate Rails precedence. Keep the Day-1 (f) citation.
8. **MINOR: AC-007 spy design.** The graph's own `sqlite3.connect` calls include a `file:…?mode=ro` URI (`codegraph.py:2557`) and `:memory:` (`:6082`).
   - Condition:
     - Normalise URIs and skip `:memory:`.
     - Assert at least one recorded target and that all targets lie under `graph_root` or `tmp_path`, none under the repo.
     - The spy calls through to the real `connect`.
     - Run the liveness check first.
9. **MINOR: `compare_graphs.py`.** Ownership is `"rails" in fingerprint` (`:93-94`), so rails_models records would be counted as native.
   - Condition: report separate owned sets for `rails` and `rails_models`. Exclude their union from the native comparison, and check both manifest keys. Self-test on a two-adapter database with non-zero counts for each, and include the `--frameworks rails` G-ROUTES run.
10. **MINOR: whole-source scans and lint.**
    - `test_thesis_negatives.py:51` and `test_export.py:374` scan all of `src` for "alter table" and `nx.compose(`. Ruff runs `RUF`, `B` and `SIM`.
    - Condition: `rails_models.py` and the docstrings avoid those strings, stay ASCII, and avoid `\d`, `\w` and `$`. Also avoid local names `input`, `compile`, `vars`, `open` (the S24 AST ban).
11. **MINOR: docs conflict.** `docs/frameworks/rails.md:83` says a rails mapping is "never EXTRACTED".
    - Condition: scope that sentence to `routes_to`. State that EXTRACTED for `model_table` and `migration_mentions` means "literal declaration extracted, no graph edge, tables are not entities".
12. **MINOR: timebox.** Day 2 carries EV1-6, CG1-8, Resolver, refresh, validate_records, the CLI and 7 AC tests.
    - Condition: move the EV edits (small) to Day 1 PM. Move the planted-corruption and CLI tests to Day 3. Keep the cut order. If Day-1 (a) or (b) fails, stop per the STOP RULE.

## Stories
- As an agent operator, I want each literal association recorded with its kind and linked to the uniquely named model, each model bound to an explicitly named table, and each literal migration table operation recorded with its span. Then a schema change is inspectable without booting Rails or touching a database.
- As a reviewer, I want implicit, through, polymorphic, computed and conflicting declarations visible as closed-vocabulary unresolved facts. Then no association or table binding is guessed.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s25.criteria.md` (sha256 `49fa5448f099fd7250da0d50378783c5ba0fb8d941c8332bcec78a2bd16081a8`), verbatim:

```
### AC-S25-001 (event-driven)
GIVEN a supported association declares a literal class_name matching one model
WHEN the model adapter indexes the declaration
THEN the adapter SHALL emit an association relation of the declared kind to the unique explicitly named model.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_explicit_association_target

### AC-S25-002 (event-driven)
GIVEN a model supplies a literal self.table_name or one unambiguous operator mapping
WHEN the model adapter binds its table
THEN the adapter SHALL emit a model_table relation to the explicitly named table.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_explicit_model_table

### AC-S25-003 (event-driven)
GIVEN a migration contains literal create_table, add_column or remove_column statements
WHEN the migration adapter indexes the file
THEN the adapter SHALL emit a migration_mentions fact for each supported literal table operation.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_migration_table_mentions

### AC-S25-004 (unwanted-behavior)
GIVEN an association uses through, polymorphic or a computed class_name
WHEN the adapter encounters that unsupported declaration
THEN the adapter SHALL report unsupported_association for the declaration.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_unsupported_association_is_visible

### AC-S25-005 (ubiquitous)
THEN every Rails model/table relation SHALL retain the producing declaration's source span and snapshot identity.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_model_relation_provenance

### AC-S25-006 (event-driven)
GIVEN identical source snapshot, explicit mapping artifact and adapter version
WHEN the model adapter indexes the same inputs twice
THEN the canonical model/table fact set SHALL remain identical after the repeat index.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_model_reindex_is_idempotent

### AC-S25-007 (ubiquitous)
THEN the model adapter SHALL derive its facts without executing repository or database code.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_models_never_execute_database_code

### AC-S25-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S25-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

Fixture `tests/fixtures/rails_models/project/` (ASCII; copied with copytree; exact totals are pinned in `provenance.json` on Day 1 PM). Models:
- **`application_record.rb`:** `ApplicationRecord < ActiveRecord::Base` with `self.abstract_class = true` (ignored).
- **`user.rb`** — `User < ApplicationRecord`:
  - A1 `has_many :posts, class_name: "Blog::Post", dependent: :destroy` → INFERRED Blog::Post;
  - A2 `has_one :profile, class_name: "::Profile", inverse_of: :user` → INFERRED Profile;
  - A3 `has_many :invoices, class_name: "Invoice"` → AMBIGUOUS (2 reopened);
  - A4 `belongs_to :team, class_name: "Team"` → target_not_model (`lib/team.rb`);
  - A5 `belongs_to :widget, class_name: "Widget"` → target_not_model (`lib/models/widget.rb` < ApplicationRecord, outside app/models);
  - A6 `belongs_to :ghost, class_name: "Ghost"` → class_not_found;
  - A7 `belongs_to :vault, class_name: "Vault"` → class_not_found (`app/models/storage/vault.rb` is skip-patterned);
  - `has_many :comments` → implicit_class_name;
  - silent: `with_options(dependent: :destroy) do has_many :drafts, class_name: "Blog::Post" end`, `validates`, and `after_save { File.write("ATLAS_S25_SENTINEL_CALLBACK","x") }`.
- **`profile.rb`** — `Profile < ::ActiveRecord::Base`: T1 `self.table_name = "user_profiles"` → EXTRACTED literal. The map confirms it.
- **`blog/post.rb`** — `module Blog; class Post < ApplicationRecord`:
  - A8 `belongs_to :author, class_name: "User"` → INFERRED User;
  - A9 `has_many :tags, class_name: "Tag"` → AMBIGUOUS [Blog::Tag, Tag];
  - A10 `has_many :global_tags, class_name: "::Tag"` → INFERRED Tag;
  - diagnostics:
    - `through` (`has_many :taggings, through: :post_tags, class_name: "Tagging"`);
    - `polymorphic` (`belongs_to :commentable, polymorphic: true`);
    - `computed_class_name` ×2 (`class_name: "#{PREFIX}User"`, `class_name: User.name`);
    - `symbol_class_name` (`:User`) and `malformed_class_name` (`"user"`);
    - `scope_argument` (`-> { order(:id) }`), `block` (`do end`), `splat` (`has_many(*SHARED)`);
    - `non_literal_name` (`has_many relation_name, …`), `has_and_belongs_to_many`, `as`;
    - `unsupported_option` (`anonymous_class:`), `extra_arguments` (`has_many :y, :z, …`);
    - `self.table_name = :blog_posts` → symbol_table_name.
- **`blog/tag.rb`:** `class Blog::Tag < ApplicationRecord` (compact). The map `Blog::Tag→blog_tags` gives T2, EXTRACTED operator_map.
- **`tag.rb`:** `Tag` with `self.table_name = "tags_v2"`; the map says `Tag→tags_legacy` → 2× conflicting_table_mapping.
- **`legacy/order.rb`:** `Legacy::Order` with `"orders_a"` and `"orders_b"` → 2× ambiguous_table_name. Its map entry is suppressed.
- **`archive.rb`:** `self.table_name = "arch_#{SUFFIX}"` → computed_table_name; `def self.table_name` → table_name_method.
- **`invoice.rb` + `invoice_reopened.rb`:** each `class Invoice < ApplicationRecord`. The map `Invoice→invoices_x` → 2× ambiguous_model.
- **Silent cases (zero records):** `admin.rb` `Admin < User` (STI) with an association, and `concerns/taggable.rb` (an `included do` association plus a model class).
- **`table-map.json`:** keys Blog::Tag, Invoice, Legacy::Order, Nope::Missing, Profile, Tag, Team (non-model). Map counters: entries 7, bound 1, confirmed 1, conflicting 1, ambiguous 1, suppressed 1, unmatched 2.

Migrations:
- **`20240101000000_create_users.rb`** — `CreateUsers < ActiveRecord::Migration[7.1]`, `def change`:
  - M1 `create_table :users do |t| t.string :name end` (the block is never read);
  - M2 `create_table "teams", force: true`;
  - M3 `add_column :users, :email, :string, null: false`;
  - M4 `remove_column :users, :legacy_flag, :boolean`.
- **`20240102000000_alter_posts.rb`** — `def up`:
  - M5 `add_column "blog_posts", "title", :string`;
  - diagnostics, one statement each: change_table, drop_table, rename_table, rename_column, add_reference, add_index, `execute "DELETE FROM users"`, reversible;
  - a modifier `if` → conditional;
  - `add_column table_name, …` → non_literal_table; `add_column :users, column_name, …` → non_literal_column;
  - `create_table "logs_#{year}"` → interpolated_literal; `remove_column :users` → missing_arguments;
  - `File.write("ATLAS_S25_SENTINEL_MIGRATION","x")` → other_statement.
  - `def down`: M6 `remove_column :blog_posts, :title`.
- **`20240103000000_legacy_rows.rb`** — `LegacyRows < ActiveRecord::Migration`: M7 `create_table :legacy_rows`; a `def self.up` holding a create_table is silent.
- **Silent files:** `20240104000000_helper.rb` (non-migration class), `db/migrate/archive/2023…_old.rb` (nested directory), `db/schema.rb`, and `db/seeds.rb` (`File.write("ATLAS_S25_SENTINEL_SEEDS","x")`).

Test rules:
- All AC tests use real builds (`CodeGraph(repo, graph_root=<external>, frameworks=("rails_models",), rails_table_map=MAP)`).
- Expected ids come from independent SQL on `symbols`.
- Every `all(...)` is preceded by a non-empty assertion. There are no mocks of the behaviour under test.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_rails_explicit_association_target | A1/A2/A8/A10 are `association` INFERRED. `target_entity_id` == the class entity (independent SQL: kind class, path, namespace). `raw_target` == canonical `{"class_name":"Blog::Post","kind":"has_many","mapping":"rails_class_name.v1","name":"posts"}` (A1). Role `rails_model:association:has_many`, and all three kinds appear across A1/A2/A8. Source == the declaring class entity == occurrence `entity_id`. A9/A3 are AMBIGUOUS `ambiguous_class_name` with candidates == sorted 2 ids. A4/A5 → `target_not_model`; A6/A7 → `class_not_found`. No association is EXTRACTED. `ev.validate_record` rejects A1 relabelled EXTRACTED. |
| 002 | test_rails_explicit_model_table | T1: `model_table` EXTRACTED, target null, raw `{"source":"literal","table":"user_profiles"}`, the statement bytes, source Profile. T2: EXTRACTED `{"map_sha256":sha256(map),"source":"operator_map","table":"blog_tags"}`, span == the `class Blog::Tag` node. Exactly one EXTRACTED model_table per bound class, and none for Tag, Legacy::Order, Invoice, Blog::Post or Archive (each has the D6 reason). Without the map, Blog::Tag has no model_table (no guess) and Tag's literal is EXTRACTED. `validate_record` rejects T1 as INFERRED or with a target. Manifest map counters match. |
| 003 | test_rails_migration_table_mentions | The M1-M7 raw_targets are exact: `{"op":"create_table","table":"users"}` and `{"column":"email","op":"add_column","table":"users"}`. All are EXTRACTED, target null, source == the migration file entity, one per statement, span == the statement bytes. No mention of `name`/`old_style`/`nope`, nor from schema.rb or the nested directory. Every diagnostic is UNRESOLVED `unsupported_migration_op` with the expected code. `validate_record` accepts the target-less EXTRACTED mention, but rejects the same shape with relation `call`. |
| 004 | test_rails_unsupported_association_is_visible | The through, polymorphic and both computed declarations, plus every other Blog::Post diagnostic, each give: role `rails_model:association:unsupported:<code>`, UNRESOLVED `unsupported_association`, raw `{"construct":c,"kind":k,"mapping":"unexpanded"}`, target null, no candidates, span == the statement. No supported association record is named taggings, commentable, readers or editors. `implicit_class_name` appears for `comments`. |
| 005 | test_rails_model_relation_provenance | For every rails_model assertion (non-empty; all three relations): evidence spans == occurrence spans; `source_sha256` == the observation hash == sha256(file); line and column match. Within `graph.reader_lease()`: `reader.snapshot_id` == the generation's `generation_inputs` snapshot id, and `reader.read_source(path, sha)[start:end]` == the expected declaration bytes. This still holds after the working-tree file is overwritten, which shows the bytes are bound by the snapshot rather than read live. Map facts: the class span plus `map_sha256` == the producer's `table_map_sha256`. |
| 006 | test_rails_model_reindex_is_idempotent | Two full ON builds into fresh roots, plus `build(since="x")` with no change: the rails_models canonical lines and the manifest value are byte-identical. `adapter_sha256` == sha256(`rails_models.py`); `table_map_sha256` == sha256(map); `graph_producer_id` == the native producer id. The unchanged rebuild gives `affected` / `complete_dependency_match` / `affected_units 0`. Non-vacuous controls: editing A1's literal changes the set, and a changed map value changes the set. |
| 007 | test_rails_models_never_execute_database_code | Monkeypatched spies: `subprocess.Popen` records and raises FileNotFoundError; os exec/spawn/fork/system/popen and asyncio subprocess raise AssertionError; `socket.socket.connect`/`socket.create_connection` raise AssertionError; `sqlite3.connect` records its target. A liveness check runs first. The repo holds a test-created `db/development.sqlite3`. ON build: success, ≥4 INFERRED. Launches are only `git -C … rev-parse` and equal an OFF build under the same spy. No argv token contains ruby/rails/rake/bundle. No `sqlite3.connect` target resolves inside the repo. The sentinels are absent and the repo SHA-256 inventory, including the sqlite file, is unchanged. A pure `scan_*` plus `refresh` on a staging copy records 0 launches. AST/ASCII gate (D13). |
| RUN-01 | gate | The SHA-256 manifest of declared tracked implementation files is equal before and after clean re-entry; `git diff --exit-code <output_commit>` holds for those paths. |
| RUN-02 | gate | `slice_id` + `criteria_sha256` are equal across interrupted and resumed receipts; one authoritative receipt, `.spectra/state/aci-s25.execution.json`. |

Supporting tests (`test_s25_rails_models.py` / `test_s25_isolation.py`):
- **Vocabularies:** the exact D3 sets, counts (17/5/24/8/3/9), limits and regexes. `ev.RELATIONS` is the base 8-set; REFERENCE/EDGE are as at base. The 3 relations are only in `ASSERTION_RELATIONS` and not in `KNOWN_EDGE_RELATIONS`.
- **EV5 carve-out:** parametrized over `ASSERTION_RELATIONS`, a target-less EXTRACTED record is accepted iff relation ∈ {model_table, migration_mentions}; INFERRED target-less is rejected for all.
- **Reachability:** every construct code is reached, through the fixture plus inline `scan_*` snippets (`declaration_limit` with `MAX_DECLARATIONS` monkeypatched to 2; `files.limited` with `MAX_FILES` monkeypatched).
- **Map parser negatives,** one per reason: oversize, BOM, depth 5, duplicate top/models key, NaN/Infinity, non-UTF-8, wrong format, an extra field, `models` a list, too many entries (monkeypatched), keys `::A`/`a`/non-ASCII/257 bytes, values `bad-name`/`1`/65 bytes. The message never contains document text.
- **CLI:**
  - `--frameworks rails_models` produces facts;
  - `--rails-table-map` without `rails_models` → exit 2, no graph dir (both option orders);
  - an invalid map → exit 2, no graph dir; a missing path → exit 2;
  - `rails_models --langs python` → exit 2;
  - `CodeGraph(rails_table_map=…)` without the adapter raises ValueError.
- **Both adapters:**
  - on a merged repo (S24 project plus S25 project), `("rails","rails_models")` gives a route record set == a rails-only build (byte-identical ids) and a models set == a models-only build;
  - separately replaceable: incremental both→rails→both keeps routes byte-identical throughout and restores models identically.
- **G-OFF:** no `rails_models` fingerprint, `rails_model` kind, relation or manifest key without the flag.
- **G-ONOFF:** `ids(ON) − record_ids(ON) == ids(OFF)` per record type; projections are equal.
- **G-ENVELOPE / G-MCP / G-NOLEAK / G-QUERY:** as S24, with impact admission ON == OFF + the rails_model occurrence count.
- **Validation:**
  - `validate_database` and `_validate_evidence_projections` pass on a copy of an ON DB;
  - planted corruptions each raise ValueError: role/kind mismatch, relation/role-area mismatch, an extra fingerprint key, source ≠ occurrence entity, INFERRED to a method or file, model_table with a target, raw_target as a list, non-canonical JSON or an unknown key, a construct outside the vocabulary, a `map_sha256` mismatch, an orphan occurrence, a second producer;
  - a different valid `adapter_sha256` is accepted.
- **S10** (each compared with a fresh full build; `_validate_evidence_projections` on the lease):
  - (i) delete `profile.rb` → A2 `class_not_found`;
  - (ii) change the map with sources unchanged → `affected_units 0`, then the new facts;
  - (iii) edit a migration;
  - (iv) break `tag.rb` syntax → no facts from that file, `files.failed 1`, and its history holds no rails_models ids;
  - (v) ON→OFF → zero records and no key;
  - (vi) OFF→ON.
- **Counting parser:** `get_parser` is not called with the flag OFF, or when no eligible file was captured.
- **Export→import round trip** keeps and validates the records (cuttable, item 2).

Gates beyond the ACs:
- **G-SRC:**
  - `git diff --name-only f409094 -- mcp-server/src` ⊆ {`codegraph.py`, `__main__.py`, `storage/evidence.py`, `frameworks/__init__.py`, `frameworks/rails_models.py`};
  - `git diff --exit-code f409094 --` for `frameworks/rails_routes.py`, `server.py`, `protocol/ query/ tools/ extractors/ providers/ indexing/ resolvers/ architecture/ counterfactual/ contracts/ importers/ reports/`, `enforcement.py`, `config.py`, `storage/*` except `evidence.py`, `pyproject.toml`, `uv.lock` and `.github/workflows`;
  - the evidence.py diff touches only EV1-EV6;
  - `git diff f409094 -- mcp-server/tests/slices/test_s24_*.py` = exactly two changed lines in `test_s24_rails_routes.py`: :381 adds `"rails_models.py"` and :418 becomes `reads == 2`.
- **G-MCP:** `atlas-aci tools` sha `94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56`, 1631472 bytes.
- **G-ENVELOPE:** 3436/3448/797/7730.
- **Regression set unchanged:** `test_s07*`, `test_s09*`, `test_s10*`, `test_s12_storage`, `test_s16*`, `test_s19*`, `test_s20*`, `test_s21*`, `test_s22*`, `test_s24*`, `test_s37*`, `test_schema_epoch`, `test_thesis_negatives`, `test_export`.
- **Full suite:** no new failure or skip against base. The base's known uid-1000 `test_s01` failure and the deselected `test_s08` flaky test are classified as in S24.
- **G-IMAGE** (orchestrator; base image from `f409094`):
  - `docker run --network none`, fixture mounted `:ro`, separate graph roots.
  - S25 fixture, base vs OFF vs ON (`--frameworks rails_models --rails-table-map /map.json`):
    - entity and occurrence id sets: base == OFF == ON − {kind `rails_model`};
    - symbols/refs/edges rows equal (link ids excluded);
    - normalized assertions: base == OFF == ON − {3 relations};
    - OFF vs ON: ids equal except rails_models-owned;
    - ON = OFF's producers + 1 `rails_models` producer;
    - manifest `rails_models` only in ON.
  - Rejections: `--frameworks django`, and `--rails-table-map` without the adapter → exit 2.
- **G-ROUTES:** the S24 fixture with `--frameworks rails`, base image vs new image. These must be equal:
  - rails_route occurrence ids;
  - normalized routes_to assertions (with `occurrence_id`);
  - the rails fingerprint minus `graph_producer_id`, including `adapter_sha256`;
  - `rails_routes` minus `producer_id`.
  - Also `rails,rails_models` on the S24 fixture == rails-only plus a `rails_models` key with zero records.
- **`compare_graphs.py`:** copied to `.spectra/evidence/aci-s25/`. Ownership is fingerprint key `rails` or `rails_models`, reported separately, plus both manifest keys. Exclusions are explicit, row counts must be non-empty, and the base/OFF/ON producer ids are recorded. Output goes to `image-compare.json`.

```sh
cd mcp-server
uv run pytest tests/slices/test_s25_rails_models.py -q
uv run pytest tests/slices/test_s25_*.py tests/slices/test_s24_*.py tests/slices/test_s07_*.py tests/slices/test_s09_*.py tests/slices/test_s10_*.py tests/slices/test_s12_storage.py tests/slices/test_s16_*.py tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/slices/test_s21_*.py tests/slices/test_s22_*.py tests/slices/test_s37_*.py tests/test_thesis_negatives.py tests/test_export.py tests/test_schema_epoch.py -q
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/
uv run --frozen mypy src/
uv run --frozen ruff check ../scripts/ --exclude fingerprint-fixture && uv run --frozen ruff format --check ../scripts/ --exclude fingerprint-fixture && uv run --frozen mypy ../scripts/
```

## Confidence
To be computed with `ramza-score`. Most of the design reuses S24 machinery that is merged and verified: an opt-in producer, probe-gated purge plus recompute, `dropped`, `validate_records`, the CLI pattern and the no-exec gates. The parser is the S31 strict-JSON recipe.

The new risk is concentrated in three places, each gated on Day 1 with a STOP RULE:
- the EV5 carve-out (only relation-filtered consumers exist);
- class-entity binding by span;
- grammar shapes.

## Rejected Alternatives
- **Widening the `rails` adapter or fingerprint.** It is closed at evidence.py:761 and pinned to `rails-routes.v1`, the one-producer rule is at rails_routes.py:676, and it would churn route identity and add parse cost to `--frameworks rails`.
- **A `table` entity kind (option A).** It needs carve-outs in ≥5 gated files (codegraph :6406, retrieval, indexed, impact, checker), and the retrieval digest would drift.
- **UNRESOLVED `table_not_entity` (option C).** A literal fact that was extracted would read as a failure.
- **Inflecting implicit class names or deriving tables from class names.** This is the excluded scope; the issue forbids guesses.
- **Picking Rails's deepest candidate.** It depends on runtime constant state (autoload, load order); AMBIGUOUS is the honest answer.
- **Reading the map inside the adapter or CodeGraph from a path.** It breaks the S24 single-read AST gate, and a bytes API keeps the parse pure and testable.
- **Map identity in `generation_inputs`/`configuration`.** That shape is closed and would churn native ids.
- **Importing `rails_routes.literal`.** It couples adapter identities; the decision is to copy.
- **Per-file incremental patching.** It leaves dangling cross-file targets after a model delete.

## Risks (ranked)
1. **A closed-set or validation miss** (EV/CG/`validate_records`) makes an enabled build fail to stage. Mitigation: planted corruptions; ON builds through full, incremental, failed-parse, import and export paths; the full suite.
2. **Class binding.** If definition occurrence spans or namespaces differ from the probe, associations and tables bind wrongly or not at all. Mitigation: Day-1 (b) STOP rule; unbound classes are counted, never guessed.
3. **Carve-out leak.** A consumer assumes "resolved ⇒ target". Mitigation: Day-1 (c), G-NOLEAK/G-QUERY, and relation-filtered consumers only (verified anchors).
4. **Grammar drift** in the pinned language pack (class body field, assignment and `lambda` shapes). Mitigation: Day-1 (a) on host and image.
5. **Producer-id churn** against `f409094` for all graphs: accepted (D12) and documented. G-IMAGE/G-ROUTES prove the substance is unchanged.
6. **Rails fidelity.** AMBIGUOUS where Rails picks deepest; ApplicationRecord counted as a model; STI silent; implicit-name volume. Mitigation: documented, plus Day-1 (f).
7. **S10 interplay over many paths.** Mitigation: the D10 analysis and tests (i)-(vi); the worst case is the safe full fallback.
8. **Volume and cost.** Every ON build re-parses ≤10000 files; facts ≤ 3×(20001+10000) records; impact admission counts them. Mitigation: caps, `truncated`, docs.
9. **The 3-day timebox.** Mitigation: the cut order. At the limit, report the completed checks and a narrow follow-up; never expand silently.
