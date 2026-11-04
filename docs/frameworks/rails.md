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

The registry is closed: `{"rails"}`. An unknown name is a usage error (exit 2) and nothing is
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
candidates only on `AMBIGUOUS`. A rails mapping is **never** `EXTRACTED`: it is a convention
(`rails_convention.v1`, `INFERRED`), not a runtime-verified fact.

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
