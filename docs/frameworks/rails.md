# Rails explicit routes (ACI-S24)

An opt-in, build-time adapter that maps the literal routes of a repository's root
`config/routes.rb` to controller actions, without booting Rails. It adds shared-store evidence
records; it adds **no** MCP operation, tool, enum, traversal, `edges` row or entity kind.

```sh
atlas-aci index --repo /path/to/rails/app --frameworks rails
```

```python
CodeGraph(repo, frameworks=("rails",))
```

The registry is closed: `{"rails", "rails_models"}` (the second adapter is the subject of the
section "Models, associations and tables (ACI-S25)" below). An unknown name is a usage error (exit 2) and nothing is
built or created. `rails` needs the `ruby` language, so `--frameworks rails --langs python` is a
usage error too, in either option order. `--frameworks rails,rails` is normalised to one adapter.
With the default (no flag) no adapter code runs and no adapter record is written.

## What is read

Only the repository-root `config/routes.rb`, from the bytes the build already captured, parsed by
tree-sitter. Ruby, Rails, rake, bundler and initializers are never run, and no repository file
other than the captured bytes is opened.

* The top-level statements of `Rails.application.routes.draw do ... end` (also `{ ... }`) and of
  `<Const>::Application.routes.draw do ... end` are the only entry points. Any other top-level
  `draw` with a block (for example `Blog::Engine.routes.draw`, a receiverless `draw`, or one with
  arguments) is one `unrecognized_draw_receiver` diagnostic, never expanded.
* Other top-level Ruby is ignored: no record, no diagnostic. This includes a `draw` block wrapped
  in `module`, `if`, `begin` or any other construct: it yields **zero facts and no diagnostic**.
* Only the direct statements of a recognised draw block are read; nested blocks are never entered.

### Supported declarations

`get`, `post`, `put`, `patch`, `delete` with a receiverless call, one plain string-literal path
and a plain string-literal `to: "controller/path#action"`. A literal is the source text between
its quotes (`"` or `'`): no interpolation, escape sequence, `%q(...)`, heredoc, adjacent-string
concatenation or empty literal, at most 512 bytes (path) and 256 bytes (target), valid UTF-8. The
target must fully match `[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)*#[a-z_][a-z0-9_]*`.

The only other accepted option is `as:` with a plain string literal or a symbol. **`as:` is
accepted but not recorded** in `raw_target`. Every other option (`constraints:`, `defaults:`,
`format:`, `anchor:`, `controller:`, `action:`, `module:`, `path:`, `via:`, `on:`, `shallow:`,
string keys, `**splat`) changes dispatch or the effective pattern and is a diagnostic.

### Diagnostics

Everything else is one `unsupported_route_construct` diagnostic carrying a closed construct code
and the exact statement span; nothing is expanded or guessed.

* Receiverless statement names: `collection concern concerns constraints controller defaults
  devise_for direct draw match member mount namespace nested resolve resource resources root scope
  shallow`.
* Verb-route failures: `route_block hash_rocket_target extra_arguments non_literal_path
  interpolated_literal malformed_literal unsupported_option missing_target non_literal_target
  malformed_target` (the first failing check wins).
* Other: `conditional` (a statement under `if`/`unless`/`case`), `other_statement`,
  `unrecognized_draw_receiver`, `declaration_limit` (after 5000 declarations the next statement is
  one `declaration_limit` diagnostic and scanning stops; `truncated` is true).

## Records

Per declaration, in the shared evidence store (all with path `config/routes.rb`):

* a `rails_route` **occurrence** over the exact statement span, role `rails_route:<verb>` or
  `rails_route:unsupported:<construct>`, attached to the `config/routes.rb` file entity;
* an **evidence** record from a `{"rails": {adapter, adapter_sha256, graph_producer_id,
  routes_path}}` producer;
* a `routes_to` **assertion** whose source is the file entity and whose `raw_target` is canonical
  JSON: `{"mapping":"rails_convention.v1","path":...,"target":...,"verb":...}` for a route or
  `{"construct":...,"mapping":"unexpanded"}` for a diagnostic.

| Case | resolution | target | candidates | `unresolved_reason` |
| --- | --- | --- | --- | --- |
| exactly one matching method entity | `INFERRED` | the method | none | none |
| two or more distinct method entities | `AMBIGUOUS` | none | their sorted ids | `ambiguous_controller_action` |
| controller class found, no such action | `UNRESOLVED` | none | none | `action_not_found` |
| no indexed class with that name | `UNRESOLVED` | none | none | `controller_not_found` |
| unsupported declaration | `UNRESOLVED` | none | none | `unsupported_route_construct` |

`AMBIGUOUS` is the unresolved encoding with candidate identities: the evidence store accepts
candidates only on `AMBIGUOUS`. A `routes_to` mapping is **never** `EXTRACTED`: it is a
convention (`rails_convention.v1`, `INFERRED`), not a runtime-verified fact. (The `model_table`
and `migration_mentions` relations of the second adapter use `EXTRACTED` with a different, defined
meaning; see below.)

Resolution: `users#index` becomes `UsersController`, `admin/users#index` becomes
`Admin::UsersController` (ActiveSupport's default inflection for that alphabet), looked up in the
Ruby `symbols` table by class namespace and instance-method owner. Both `module Admin; class
UsersController` and `class Admin::UsersController` match. The lookup is not restricted to
`app/controllers`, so a duplicate controller elsewhere is an honest ambiguity.

Facts are not traversable over MCP. Inspect them with the evidence export
(`atlas-aci export`) and the manifest key `rails_routes` (state `indexed`/`absent`/`failed`,
counts, `truncated`, `producer_id`).

## Limitations

* Only the root `config/routes.rb` and direct statements of recognised draw blocks. No
  `resources`/`namespace`/`scope`/`concern` expansion, engines, constraints, custom inflections,
  `draw :x` / `config/routes/*.rb`.
* No visibility (a `private` action resolves), no inheritance (an inherited action is
  `action_not_found`), no acronym inflection (`API::V1::PingController` for `api/v1/ping#show` is
  `controller_not_found`). A reopened class or a double `def` counts as two entities
  (`AMBIGUOUS`); `def self.x` and `class << self` methods are excluded.
* **`controller_not_found` means "no indexed class".** A controller the index never saw gives the
  same reason: a path under a skip pattern (for example `app/controllers/storage/`, `tmp`, `log`,
  `vendor/bundle`), a disabled language, or a file that failed to parse.
* A syntax error in `config/routes.rb` yields no rails facts (state `failed`) and drops the
  previous ones; they are never resurrected from history.
* Rails occurrences count toward `graph_query op=impact` diff admission (at most 5001 per side).
* An enabled build re-parses `config/routes.rb` every time, outside `cache_stats` and the
  extraction cache. A disabled build, a repository without the file and a failed file parse
  nothing extra.

## Incremental builds and S10

Rails facts are a derived global function of (the `config/routes.rb` bytes, the Ruby `symbols`
table, the adapter). **They are rebuilt globally by the adapter on every enabled build; they are
not republished per file through the S10 plan.** S10 contributes two things: the per-file
`remove_path` that clears the facts of a changed `config/routes.rb`, and the `affected` binding
plan, which classifies `config/routes.rb` as affected when its own digest changes or when the Ruby
bucket digest changes (a controller edit). An unchanged incremental rebuild stays `affected` with
`complete_dependency_match` exactly as with the flag off. A build without the flag still purges
every rails-owned record left by an earlier enabled build (gated on a cheap probe: a rails
producer or the `rails_routes` manifest key).

## Identity and producer-id churn

The flag is not part of the generation configuration and `frameworks/` is not hashed into the
producer inputs: adapter identity lives in the rails producer fingerprint and the manifest key
`rails_routes`, written only when the flag is on. Editing `codegraph.py` and `storage/evidence.py`
changes the native producer id, so for **every** graph, flag on or off, evidence ids, assertion
ids, projection link ids, generation ids, snapshot ids and export bytes differ from the previous
release, and existing indexes rebuild in full on the first `--since` run. Entity ids, occurrence
ids and the content of the symbols/refs/edges rows are unchanged. On the same code, the flag-on
graph minus the rails-owned records equals the flag-off graph on every record id.

# Models, associations and tables (ACI-S25)

A second opt-in, build-time adapter, `rails_models`, records **explicit** declarations of a
repository's Rails models and migrations: literal association class names linked to the model
class they name, literal or operator-mapped model tables, and literal migration table
operations. Like `rails` it never boots Rails, runs a migration, opens a database or runs any
Ruby, and it adds shared-store evidence records only: **no** MCP operation, tool, enum,
traversal, `edges` row or entity kind.

```sh
atlas-aci index --repo /path/to/rails/app --frameworks rails_models
atlas-aci index --repo /path/to/rails/app --frameworks rails,rails_models --rails-table-map map.json
```

```python
CodeGraph(repo, frameworks=("rails_models",), rails_table_map=map_bytes)
```

`rails_models` needs the `ruby` language. The two adapters are independent: they can be combined
or used alone, each has its own producer, its own manifest key (`rails_models`) and its own
records, and each build purges and recomputes only the records of the adapters that are enabled
(a disabled adapter's records are purged). With `--frameworks rails` alone, route facts keep their
identity and cost. `--rails-table-map FILE` requires `rails_models` in `--frameworks` (usage
error, exit 2, no graph directory created).

## What is read

Only bytes the build already captured, parsed by tree-sitter:

* `app/models/**/*.rb` at the repository root, **except** `app/models/concerns/`;
* `db/migrate/*.rb` (the directory itself, not nested directories).

`db/schema.rb`, `db/structure.sql`, `db/seeds.rb`, engines (`*/app/models`), `lib/`, concerns and
every other file are never read. A path under a skip pattern (for example `app/models/storage/`)
is not indexed and therefore not read either.

### Models and associations

Only the **direct class-body statements** of a class whose superclass is literally
`ApplicationRecord`, `ActiveRecord::Base` or `::ActiveRecord::Base` are read (`ApplicationRecord`
itself counts). Subclasses of other models (STI), `with_options`, callbacks, validations, scopes,
methods, conditionals, blocks and concerns are silent: no record and no diagnostic.

A class is **bound** to its class entity by its exact source span, and only if its namespace
(the enclosing module and class names plus its own) is a plain `A::B` name. A class under a
rooted wrapper (`module ::A`) or a compact `class A::B` declared inside any enclosing module or
class is **unbound**: it is counted in the manifest (`classes.unbound`) and yields no fact.

A supported association is a receiverless `belongs_to`, `has_one` or `has_many` with one symbol
or string name and a literal `class_name:` (`"Blog::Post"`, `"::Tag"`). Allowed options that are
accepted but not recorded: `foreign_key primary_key inverse_of dependent optional required touch
autosave validate counter_cache strict_loading`. Everything else is an
`unsupported_association` diagnostic with a closed construct code and the statement span:
`has_and_belongs_to_many`, `through`, `source`, `source_type`, `polymorphic`, `as`, `block`,
`splat`, `scope_argument`, `extra_arguments`, `non_literal_name`, `unsupported_option`,
`implicit_class_name`, `symbol_class_name`, `computed_class_name`, `malformed_class_name`,
`declaration_limit` (the first failing check wins). There is **no pluralization or inflection**:
an association without a literal `class_name:` is `implicit_class_name`, never guessed.

### Class-name resolution

The class name is looked up with Rails' `compute_type` candidate list for the declaring model's
namespace, deepest first (for `Blog::Post`: `Blog::Post::Tag`, `Blog::Tag`, `Tag`; a leading `::`
names exactly one absolute candidate). This is what Rails does at runtime in
`ActiveRecord::Inheritance::ClassMethods#compute_type`, verified against the Rails `v7.1.3`,
`v7.2.0` and `v8.0.0` sources. **Atlas does not evaluate Rails precedence**: it never claims
which candidate Rails would load.

* Existence is computed over **all indexed class and module names**, not only models. A fact is
  `INFERRED` only when exactly one candidate name exists and it has exactly one model entity.
  A plain object that shadows a model (`Blog::Label` as a PORO, `::Label` as a model) is not
  silently skipped: the fact is not `INFERRED` to the model.
* Two or more model entities reached (a reopened class, or two candidate names that both hold a
  model) are `AMBIGUOUS` with the sorted candidate ids.
* Otherwise `UNRESOLVED`: `class_not_found` means "no indexed class or module has that name" (a
  skip pattern, a disabled language or a parse failure also gives this), and
  `target_not_recognized_model` means **"class exists but is not recognised as a model by this
  adapter"** (a plain class, a model outside `app/models`, an STI or other-base model such as
  `Spree::Base`, or the shadow case above).
* Models are the bound classes under `app/models/**` (not concerns) with one of the three bases.

### Tables

A model table comes from exactly one of:

* a literal `self.table_name = "name"` direct class-body statement (plain string, `[A-Za-z_]`
  `[A-Za-z0-9_]*`, at most 64 bytes); or
* the operator map (below).

Unsupported table statements are `unsupported_table_name` diagnostics: `symbol_table_name`,
`computed_table_name` (interpolation, expressions, `||=`), `malformed_table_name`,
`table_name_method` (`def self.table_name`), `declaration_limit`. `table_name_prefix` and
`table_name_suffix` are ignored. A table is **never derived from the class name**.

Decision per fully scanned bound class (`U` unsupported table statements, `L` literals, `M` the
map entry of its namespace when that namespace has exactly one model entity):

1. `U` non-empty: only the `U` diagnostics are emitted; the map entry counts as `suppressed`.
2. Two or more literals: each is `UNRESOLVED` `ambiguous_table_name`; the map entry is
   `suppressed`.
3. One literal: with a different `M` the literal and a map record are both `UNRESOLVED`
   `conflicting_table_mapping`; with an equal `M` the literal is `EXTRACTED` and the entry is
   `confirmed`; with no `M` the literal is `EXTRACTED`.
4. No literal and an `M`: one map record, `EXTRACTED` (`bound`).

A map key matching two or more model entities (a reopened class) gets one map record per entity,
`UNRESOLVED` `ambiguous_model`; keys matching no model (including non-model classes) count only
as `unmatched` in the manifest.

### The operator map

```json
{"format": "atlas-rails-table-map.v1", "models": {"Blog::Tag": "blog_tags", "Profile": "user_profiles"}}
```

Keys are model class names (`A` or `A::B`, ASCII, at most 256 bytes, no leading `::`); values are
table names (at most 64 bytes). The file is read by the CLI only (the adapter takes bytes) and
parsed strictly: at most 1 MiB, 10000 entries, nesting depth 4, no byte-order mark, no duplicate
keys at any level, no `NaN`/`Infinity`, no lone surrogate, `format` required and exact, no extra
field. A rejection is a closed reason (`too_large`, `invalid_json`, `unsupported_format`,
`schema`, `too_many_entries`, `malformed_model`, `malformed_table`) with fixed message text that
never echoes the path or the document; the CLI exits 2 before any graph directory is created. The
CLI opens the path without blocking and requires a regular file. The map identity is
`sha256(file bytes)` (a whitespace-only edit changes it, by design: the artifact is its bytes).

### Migrations

Classes whose superclass is `ActiveRecord::Migration` or `ActiveRecord::Migration[d.d]`; only the
direct statements of `def change`, `def up` and `def down` (not `def self.up`). A receiverless
`create_table`, `add_column` or `remove_column` with literal table (and column) names is a
`migration_mentions` fact; a `create_table` block is never read and option pairs (`force:`,
`null:`, `default:`) are never recorded. Every other statement is an
`unsupported_migration_op` diagnostic with a closed code: the 24 statement names
(`change_table drop_table rename_table rename_column add_reference remove_reference
add_belongs_to remove_belongs_to add_index remove_index add_timestamps remove_timestamps
add_foreign_key remove_foreign_key change_column change_column_default change_column_null
create_join_table drop_join_table execute reversible revert up_only remove_columns`), the
operation failures (`block splat missing_arguments extra_arguments non_literal_table
non_literal_column interpolated_literal malformed_literal`) and `conditional`, `other_statement`,
`declaration_limit`.

## Records

Per declaration, in the shared store (path = the declaring file): a `rails_model` **occurrence**
over the exact statement span (the class span for operator-map facts), evidence from a
`{"rails_models": {adapter, adapter_sha256, graph_producer_id, table_map_sha256}}` producer, and
one assertion with a canonical `raw_target`. The assertion source is the declaring model's class
entity (`association`, `model_table`) or the migration file entity (`migration_mentions`).

| Relation | resolution | target | `unresolved_reason` |
| --- | --- | --- | --- |
| `association` (supported) | `INFERRED` | the model class entity | none |
| `association` (several models) | `AMBIGUOUS` | none (sorted candidate ids) | `ambiguous_class_name` |
| `association` | `UNRESOLVED` | none | `target_not_recognized_model`, `class_not_found` |
| `association` (diagnostic) | `UNRESOLVED` | none | `unsupported_association` |
| `model_table` | `EXTRACTED` | none | none |
| `model_table` | `UNRESOLVED` | none | `conflicting_table_mapping`, `ambiguous_table_name`, `ambiguous_model`, `unsupported_table_name` |
| `migration_mentions` | `EXTRACTED` | none | none |
| `migration_mentions` (diagnostic) | `UNRESOLVED` | none | `unsupported_migration_op` |

An association is **never** `EXTRACTED`. For `model_table` and `migration_mentions`, `EXTRACTED`
means "literal declaration extracted, no graph edge, tables are not entities": tables are not
graph entities, so these assertions are target-less and carry the table (and column) in the
canonical `raw_target`, for example `{"column":"email","op":"add_column","table":"users"}` or
`{"map_sha256":"...","source":"operator_map","table":"blog_tags"}`. Only these two relations may be
resolved without a target; every other relation keeps the rule that a resolved assertion names its
target. A migration mention is a source statement, not proof that the migration ran and not the
live schema (`db/schema.rb` and `def self.up` are not read).

The three relations are only in the evidence store's assertion relation set: they are in none of
`RELATIONS`, `REFERENCE_RELATIONS` or `EDGE_RELATIONS`, never produce an `edges` row, are not
traversable and are not MCP enum values. Inspect them with the evidence export
(`atlas-aci export`) and the manifest key `rails_models`:
`format`, `adapter`, `adapter_sha256`, `table_map_sha256`, `files`
(`models migrations failed limited`), `classes` (`models migrations unbound`), `declarations`,
`facts` per relation and resolution, `map` (`entries bound confirmed conflicting ambiguous
suppressed unmatched`, or null without a map), `truncated`, `producer_id`.

## Limitations

* Out of scope: database connections, migration execution, `db/schema.rb`/`structure.sql`, live
  schema reconstruction, pluralization and inflectors, polymorphic/through/habtm associations,
  callbacks, scopes, concerns, STI binding, `with_options`, SQL dataflow, migration safety,
  engines, `table_name_prefix`/`suffix` and `def self.up` migrations.
* **Scope of reading:** only root `app/models/**` and `db/migrate/*.rb`, direct class-body
  statements, the three model bases. Silent cases are listed above.
* **`AMBIGUOUS` is reported instead of Rails precedence.** Where Rails would pick the deepest
  existing candidate, Atlas reports the ambiguity (or `target_not_recognized_model` when a
  non-model name comes first); it never chooses.
* **`ApplicationRecord` counts as a model** (it has the `ActiveRecord::Base` base); a reopened
  model is two entities.
* **Tables are not entities** and are never traversable; there is no table graph.
* A model or migration file that fails to parse yields no facts for that generation (the file is
  counted in `files.failed`) and its earlier facts are never resurrected from history.
* **Bounds:** at most 10000 captured files (`files.limited` counts the rest), 1 MiB per file and
  64 MiB in total (also `files.limited`), 20000 declarations (the next statement is one
  `declaration_limit` diagnostic and no further statement is read; classes are still listed so
  the model index stays complete) and 10000 emitted map records. Any of these sets `truncated`.
* **Cost:** the captured bytes are retained only when the adapter is on, and every enabled build
  re-parses the captured files outside `cache_stats` and the extraction cache. A build without the
  flag, and a repository without model or migration files, parse nothing extra.
* `rails_model` occurrences count toward `graph_query op=impact` diff admission.

## Incremental builds, identity and churn

As for routes, the facts are a derived global function (captured model and migration bytes, the
Ruby `symbols` table, the map bytes, the adapter) rebuilt by the adapter on every enabled build,
not republished per file through the S10 plan; S10 contributes the per-file `remove_path` and the
`affected` classification. A map change with unchanged sources reports zero affected units and
then recomputes. A build without the flag purges every rails_models record (gated on a producer
or manifest-key probe).

Adapter and map identity live in the producer fingerprint and the manifest key, not in the
generation configuration. Editing `codegraph.py` and `storage/evidence.py` changes the native
producer id again, so for **every** graph, flag on or off, evidence, assertion, link, generation
and snapshot ids differ from the previous release and existing indexes rebuild in full on the
first `--since` run; entity ids, occurrence ids and projection row contents are unchanged. On
the same code, the flag-on graph minus the rails_models-owned records equals the flag-off graph on
every record id.

# Jobs and enqueue argument paths (ACI-S26)

A third opt-in, build-time adapter, `rails_jobs`, records **direct** job enqueues of a
repository: a literal `Worker.perform_async(...)` or `Job.perform_later(...)` is linked to the one
explicit `perform` method of the class it names, and, when the argument boundary is simple enough
to state exactly, positional argument `i` is mapped to parameter `i` of that `perform`. Like the
other adapters it never boots Rails, runs Ruby, touches Redis, Sidekiq or ActiveJob, enqueues or
runs a job, or evaluates an argument, and it adds shared-store evidence records only: **no** MCP
operation, tool, enum, traversal, `edges` row or entity kind.

```sh
atlas-aci index --repo /path/to/rails/app --frameworks rails_jobs
atlas-aci index --repo /path/to/rails/app --frameworks rails,rails_jobs,rails_models
```

```python
CodeGraph(repo, frameworks=("rails_jobs",))
```

`rails_jobs` needs the `ruby` language and has no other option. The adapters are independent:
each has its own producer (`{"rails_jobs": {adapter, adapter_sha256, graph_producer_id}}`), its
own manifest key (`rails_jobs`, format `atlas-rails-jobs.v1`) and its own records, and each build
purges and recomputes only the records of the adapters that are enabled. Toggling `rails_jobs`
leaves the route and model records byte-identical.

## What is read

Every indexed `.rb` file whose bytes mention one of `perform_async`, `perform_later`,
`perform_in`, `perform_at`, `Sidekiq::Worker`, `Sidekiq::Job`, `ApplicationJob`,
`ActiveJob::Base` or `def perform` (specs included) is captured and re-parsed with tree-sitter;
other Ruby files are not retained. The adapter reads exactly the files the build indexed: a path
under a skip pattern (a path component such as `tmp`, `storage` or `build`) is not indexed and
therefore not read either.

* **Enqueue sites** are found through the build's `refs` rows for the four method names and
  located by the byte span of the method identifier in a per-file parse; a row that does not
  locate a `call` node is counted (`sites.unlocated`) and never guessed.
* **Direct constant receivers only:** `Foo.perform_async(...)`, `A::B.perform_later(...)`,
  `::Foo.perform_later(...)`.
* **Recognition:** `perform_async` needs a class with a direct `include Sidekiq::Worker` or
  `include Sidekiq::Job` (an optional leading `::`); `perform_later` needs a direct superclass
  `ApplicationJob`, `ActiveJob::Base` or `::ActiveJob::Base`. Custom base classes, mixins
  (`ApplicationWorker`, `< BaseJob`), `include` inside a conditional and an inherited `perform`
  are **not** recognised and yield `target_not_recognized_job`.
* **The target** is an explicit instance `def perform` that is a direct statement of the class
  body. `def self.perform`, `class << self`, `private def perform` and any inherited or
  module-provided `perform` are never read.
* `perform_now`, `perform_sync`, `perform_inline`, `.new.perform`, `delay`, `perform_bulk`,
  `send`/`public_send`, comments and string literals are silent: no record and no diagnostic.

## Resolution

The candidate names of a receiver `R` are the lexical ones, innermost first: for every enclosing
class or module `S` in `Module.nesting` order, `S::R`, then `R` itself (a rooted `::R` names
exactly `R`; with no enclosing scope the only candidate is `R`). Existence is checked over every
class and module of the repository. Then:

* exactly one existing name with exactly one recognised `perform`: `INFERRED` to that method;
* two or more recognised `perform` methods (two files declaring the same class, or two lexical
  candidates): `AMBIGUOUS` with the sorted candidate method ids, reason `ambiguous_job_target`;
* no existing name: `UNRESOLVED` `job_class_not_found`; a recognised class without an explicit
  `perform`: `perform_not_found`; anything else (not a job, wrong mechanism for the method,
  several existing names but one `perform`, an inherited job): `target_not_recognized_job`.

A site whose enclosing classes or modules cannot be stated lexically is `UNRESOLVED` with the
`unbound_namespace` diagnostic: a class that is not a plain `constant` or `A::B` name, a rooted
`class ::A`, or a compact name such as `class Inner::Thing` **inside** another class or module
(Ruby may define it outside the lexical parent). A top-level compact `class A::B` is bound, and
`module Billing; class Nightly` resolves through `Billing::Nightly` then `Billing`. A name that
has a class or module definition in a file the adapter could not read (a file over a size or
count cap, a file that fails to parse, an unbound or unmatched class) never resolves to
`INFERRED` or `AMBIGUOUS`; it is `target_not_recognized_job`.

Atlas does **not** evaluate Ruby constant lookup: Ruby picks the first lexical candidate, then
the ancestors of the innermost scope, then top level; Atlas evaluates neither precedence nor
ancestor lookup, it reports. A reopened job with two `def perform` is `AMBIGUOUS` although Ruby
keeps the last one.

## Records

Per enqueue and per argument, in the shared store (path = the enqueuing file): a `rails_job`
**occurrence** (the exact `call` span, or the argument expression span), evidence from the
`rails_jobs` producer and one assertion. The assertion source is the enclosing method, class or
module entity of the site (the file entity at top level, in a spec block or under an uncaptured
wrapper); the occurrence entity is the same entity.

| Relation | role | resolution | target | `unresolved_reason` |
| --- | --- | --- | --- | --- |
| `async_enqueue` | `rails_job:enqueue:perform_async` / `perform_later` | `INFERRED` | the `perform` method | none |
| `async_enqueue` | same | `AMBIGUOUS` | none (sorted candidate ids) | `ambiguous_job_target` |
| `async_enqueue` | same | `UNRESOLVED` | none | `job_class_not_found`, `perform_not_found`, `target_not_recognized_job` |
| `async_enqueue` | `rails_job:enqueue:unsupported:<construct>` | `UNRESOLVED` | none | `unsupported_argument_mapping` |
| `async_argument` | `rails_job:argument:mapped` | `INFERRED` | the same `perform` method | none |
| `async_argument` | `rails_job:argument:unsupported:<construct>` | `UNRESOLVED` | none | `unsupported_argument_mapping` |

An enqueue's canonical `raw_target` is `{"arguments","arity","kind","mapping":"rails_job_receiver.v1",
"mechanism","receiver"}` plus `"construct"` when `arguments` is `unsupported`; `arguments` is
`mapped`, `none` (no argument), `unbound` (no single target) or `unsupported`; `mechanism` is
`sidekiq` for `perform_async` and `active_job` for `perform_later`; diagnostics use the mapping
label `unexpanded`. A mapped argument
is `{"arg","enqueue","mapping":"rails_job_positional.v1","param","param_name"}` with
`arg == param`; `enqueue` is the id of the enqueue occurrence. Values are never evaluated or
copied: no `raw_target` holds argument source text.

**What a mapping says:** positional argument `i` of the enqueue is delivered to parameter `i` of
the `perform`, by span. The fixed positional part of a `perform` is its leading required
parameters then its optional ones; the arity is compatible when it lies between the number of
required and the number of required plus optional parameters. Serialisation of arguments (JSON,
GlobalID), keyword transformation and middleware are **not** modelled, and the mapping is not
proof that anything ran. A heredoc argument spans only its opener.

### Unsupported forms (`unsupported_argument_mapping`)

* **The enqueue itself is the diagnostic** (role `rails_job:enqueue:unsupported:<construct>`,
  `UNRESOLVED` `unsupported_argument_mapping`, no argument records): `dynamic_receiver` (a
  non-constant receiver, a call or `self.class` receiver, or a **receiverless** call such as
  `perform_async(x)` inside the job class), `set_chain` (`Job.set(...).perform_later`),
  `delayed_enqueue` (`perform_in`, `perform_at`), `unbound_namespace` and `site_limit`.
* **The enqueue keeps its resolution** when the receiver resolves but the arguments cannot be
  mapped: splat, hash splat, bare keyword arguments, a block argument, a block, forwarded
  arguments (`...`), more than 16 arguments, a `perform` signature the mapping cannot state
  (`*args`, a required keyword, a required parameter after an optional one, destructuring or
  forwarding) and an incompatible arity. Its `raw_target` carries `"arguments":"unsupported"` and
  the construct code (`forwarded_arguments`, `splat`, `hash_splat`, `keyword_arguments`,
  `block_argument`, `block`, `argument_limit`, `unsupported_signature`, `arity_mismatch`) and
  **no partial mapping is emitted**. An `INFERRED` assertion must keep a null reason, so the
  `unsupported_argument_mapping` reason lives on exactly one `async_argument` diagnostic that
  references the enqueue occurrence (span: the offending argument node, the argument list or the
  call). An `AMBIGUOUS` or `UNRESOLVED` enqueue with a call-side construct keeps its candidate
  set and reason and also carries that one diagnostic.

## Async boundary

`async_enqueue` is an **asynchronous boundary**, not a call, and not proof that the job ran. The
relations are only in the evidence store's assertion relation set: they are in none of
`RELATIONS`, `REFERENCE_RELATIONS` or `EDGE_RELATIONS`, never produce an `edges` row, are not
traversable and are not MCP enum values, so MCP traversal never reaches a `perform` through an
enqueue. The native call graph is unchanged (`HardWorker.new.perform(1)` stays an ordinary
`call`). Inspect the records with the evidence export (`atlas-aci export`) and the manifest key
`rails_jobs`: `format`, `adapter`, `adapter_sha256`, `files`
(`captured scanned failed limited`), `classes` (`jobs unbound`), `performs`, `sites`
(`located unlocated skipped limited`), `facts` per relation and resolution, `truncated`,
`producer_id`.

## Limitations

* Out of scope: Redis, Sidekiq and ActiveJob runtime behaviour, enqueue execution, scheduler
  inspection, app boot, serialisation, middleware, retries, interprocedural dataflow,
  `perform_bulk`, `.rake` files and Ruby ancestor-chain constant lookup.
* **Silent limits** (no record, no diagnostic, or a diagnostic that says less than the truth):
  a receiverless self-enqueue is `dynamic_receiver`; `send` and `public_send` are invisible;
  class definitions in skip-pattern files are invisible (the name is `job_class_not_found`) and in
  files that fail to parse make the name `target_not_recognized_job`; constant assignment
  (`Worker = Class.new`) and `Class.new` shadows are not modelled; for a multi-segment receiver
  `A::B` only the first segment `A` is resolved lexically by Ruby, then `B` is looked up inside
  `A` and its ancestors: Atlas looks up the whole joined name once per lexical scope and does not
  model that ancestor lookup.
* **Bounds:** at most 10000 captured files, 1 MiB per file and 64 MiB in total (`files.limited`),
  5000 enqueue sites (the next one is a single `site_limit` diagnostic, the rest are counted in
  `sites.limited`) and 16 arguments per enqueue (`argument_limit`). Any of these sets `truncated`.
* A file that fails to parse yields no facts for that generation (`files.failed`) and its
  earlier facts are never resurrected from history.
* **Cost:** the captured bytes are retained only when the adapter is on, and every enabled build
  re-parses the captured files outside `cache_stats` and the extraction cache. A build without
  the flag parses nothing extra. `rails_job` occurrences count toward `graph_query op=impact`
  diff admission (at most 5001 sites times 17 records).

## Incremental builds, identity and churn

The facts are a derived global function of the captured bytes, the Ruby `symbols` and `refs`
tables and the adapter. They are rebuilt by the adapter on every enabled build, not republished
per file through the S10 plan: S10 contributes the per-file `remove_path` for changed or removed
files and the `affected` classification. This slice does not claim that editing a job file puts
the enqueuing file in `affected_paths` (a parameters-only edit leaves every `symbols` row
unchanged); the facts are nevertheless exact, because the recompute is global. A build without
the flag purges every `rails_jobs` record (gated on a producer or manifest-key probe).

Adapter identity lives in the producer fingerprint and the manifest key, not in the generation
configuration. Editing `codegraph.py` and `storage/evidence.py` changes the native producer id
again, so for **every** graph, flag on or off, evidence, assertion, link, generation and snapshot
ids differ from the previous release and existing indexes rebuild in full on the first `--since`
run; entity ids, occurrence ids and projection row contents are unchanged. On the same code, the
flag-on graph minus the rails_jobs-owned records equals the flag-off graph on every record id.
