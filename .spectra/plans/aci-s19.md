---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-03T00:00:00Z
slice_id: ACI-S19
issue: https://github.com/Rynaro/atlas-aci/issues/40
---
# ACI-S19 — compare snapshots at symbol and contract boundaries

## Scope

Implement issue #40 on merged base `66d8a38` (main after PR #82), branch `aci-s19`.
Live marker `<!-- atlas-aci:slice:ACI-S19 -->` verified; no prior S19 plan, state or
receipt. Criteria bytes (between markers, leading newline removed) hash to
`fea6cab2657356748b092b5824a29723886e1c296d24d2ac080035b203ef64e3` = contract table
(EARS lint ok, 8 criteria). Predecessors verified on the execution base: S08 (#30),
S10 (#32), S12 (#34) are ancestors of HEAD with COMPLETE receipts; content-addressed
source snapshots with hash-checked leases, evidence records with position-free entity
identity, and pinned reader leases exist.

Observed facts (re-verified at planning):
- A published graph namespace holds exactly one generation (`graph.9.db`); source
  snapshots retain 2, but the previous generation's entities/occurrences are gone.
  Explicit before/after evidence therefore means two complete graph namespaces, each
  opened read-only: `CodeGraph(repo, read_only=True, graph_root=G).reader_lease()` exposes
  `generation_id`, `snapshot_id`, `read_source(path, sha)` and `_source_lease.manifest`.
- Namespace = `graph_root/repos/sha256(str(repo))`; two worktrees of one repository can
  share a graph root and record equal `identity.common_dir`, different `git_dir` (S08
  worktree test).
- Entity id = hash{path, language, kind, name, owners, ordinal, identity_basis} (no
  lines/bytes): a pure line shift keeps the entity id and changes the occurrence id. Every
  non-file parsed entity has exactly one `definition` occurrence (role
  `definition:def.<kind>`) spanning the whole declaration node captured by `@def.*`.
- Signature/body spans are not stored; they are reconstructed offline from hash-verified
  snapshot bytes with the same grammar (`get_parser(lang)`).
- `_producer_inputs` fingerprints `codegraph.py` and 8 listed modules
  (`storage/evidence.py`, `storage/snapshots.py`, …): editing any of them changes every
  generation id and pinned fixtures (`tests/fixtures/s08/*`). New `query/*` modules are
  not fingerprinted.
- JSONL `import` and legacy generations have no available source snapshot (pointer state
  ≠ `available`), so no fingerprint is possible.

In:
1. Pure read-only library `query/diff.py` (comparison, compatibility, records,
   candidates) plus `query/diff_syntax.py` (closed per-language declaration map and
   normalization) — both within the issue glob `query/diff*`.
2. Operator CLI `atlas-aci diff` (JSON on stdout). Each side = (repo, graph-root):
   `--repo R` sets both sides' repo, `--before-repo/--after-repo` override; `--graph-root
   G` sets both, `--before-graph-root/--after-graph-root` override. Supported workflows:
   same path with two roots (index → edit → index into a second root), and
   worktree-before/worktree-after under one root. Read-only: no writes under either graph
   root or repo.
3. Repository/snapshot compatibility check with closed rejection reasons (AC-005).
4. Change categories `added`, `removed`, `signature`, `body`, `format`, `location`,
   `unknown`; `unchanged` counted only.
5. Conservative rename/move candidates in a separate list (never merged into identity).
6. Content-addressed canonical change set `atlas-symbol-diff.v1` (AC-004/AC-006).
7. Docs: `atlas/docs/symbol-diff.md`, README/INTEGRATION pointers, CHANGELOG.

Out: semantic equivalence; similarity/name-based rename guessing; MCP tool or protocol
changes (the seven tools, `graph_query` ops, `ev.RELATIONS`, schemas and the
3,436/3,448-byte minimum envelopes are unchanged); any edit to fingerprinted producer
modules (`codegraph.py`, `storage/*`, `extractors/*`, `providers/*`, `indexing/*`,
`resolvers/*`); persisting fingerprints at index time; cross-Atlas-version diffs; diffs
over JSONL-imported or legacy generations; SCIP (`scip_*`) entities.

Declared implementation paths (issue): `mcp-server/src/atlas_aci/query/diff*`,
`mcp-server/src/atlas_aci/storage/*`, `mcp-server/tests/slices/test_s19_*.py`.
`storage/*` is issue-declared but deliberately not edited (producer fingerprint) and is
narrowed out of `aci-s19.scope.txt`, so `ramza-drift` flags any accidental edit; the
G-PRODUCER gate enforces the same.

Scope amendment (`.spectra/changes/aci-s19/scope-amendment.json`, recorded before
implementation): `mcp-server/src/atlas_aci/__main__.py` (diff command; not
fingerprinted), `atlas/docs/symbol-diff.md`, `README.md`, `INTEGRATION.md`,
`CHANGELOG.md`, `mcp-server/tests/fixtures/s19/*` (only if inline test sources become
unreadable), and Ramza/receipt/backlog files.

Rightsize FULL 5 (12 files est., public CLI surface, novel comparison model, med stakes).

## Approach

### Decisions (operator defaults adopted and recorded)

D1 Surface. Library + operator CLI only. A `graph_query` op would touch
`ev.RELATIONS`-derived protocol enums and measured envelopes, and MCP serves one
repository namespace per process; a pairwise offline comparison fits the S16
`import-scip` CLI precedent.

D2 Compatibility rule (AC-005). Both sides open via `reader_lease()`; the diff compares
the two `_source_lease.manifest["context"]` itself and rejects with
`DiffRejected(reason)` (`ValueError` subclass; CLI → `ClickException`, exit 1,
`Snapshot diff rejected: <reason>`, nothing on stdout). Closed reasons, in check order:
`index_unavailable` (no complete compatible generation), `source_snapshot_unavailable`
(either side's snapshot pointer not `available`: JSONL import, legacy),
`identity_kind_mismatch` (`identity.kind` differs), `repository_identity_mismatch` (git:
`common_dir` differs; non_git/git_unavailable: `root` differs), `schema_mismatch`,
`configuration_mismatch` (languages, skip_patterns — else added/removed would be
configuration artifacts), `producer_mismatch` (entity-id derivation, queries and grammar
versions must be identical for ids and fingerprints to be comparable). Snapshot evidence
is the only identity Atlas records; `common_dir` is shared by worktrees of one repository
and differs for unrelated ones; non-git trees have no relatedness evidence beyond the
same root. Rejecting unprovable relatedness (e.g. two clones of one upstream) is the
conservative reading. Documented limitation: an unrelated repository re-created at the
same path with the same `.git` location is undetectable from snapshot evidence.

D3 Categories, for entities present on both sides (same entity id), in precedence:
`signature` (signature fingerprint differs; `body_changed` reported as a flag) → `body`
(signature equal, body differs) → `format` (both fingerprints equal, own-text hash
differs: whitespace/comments only) → `location` (fingerprints and own text equal, start
position (start_byte, line, column) differs) → `unchanged` (counted only). Flags
`signature_changed`, `body_changed`, `text_changed`, `position_changed` on every record.
`added`/`removed` = entity-id set difference over current, successfully extracted paths.
`unknown` (closed `reason`) whenever a both-sides entity cannot be soundly classified
(D6). Whitespace/comment-only edits are `format`: AC-002 speaks of the *normalized* body
(so not `body`), AC-003 of "only line positions" (so `location` requires identical
declaration own-text). Containers: a nested entity's change is reported on that entity
only — its subtree is a placeholder in the parent's body fingerprint and own-text hash, so
a class whose method grew is `unchanged`/`location`; adding/removing/renaming a member
changes the parent body (placeholder sequence).

D4 Candidates. Pair a `removed` r with an `added` a only if same language and kind, both
fingerprint basis `parsed`, equal signature and body fingerprints (name is a placeholder
in the signature), and the match is unique in both directions. `relation` = `rename`
(name differs; path/owners same) | `move` (path or owners differ; name same) |
`rename_and_move`. Names are never compared for similarity. `removed`/`added` records are
kept unchanged; the candidate references their record ids, `basis` =
`identical_normalized_signature_and_body`.

D5 Records (AC-004/AC-006). Change record: `type: "symbol_change"`, `category`, flags,
entity fields (`entity_id`, `language`, `kind`, `name`, `owners`, `ordinal`), `reason`
(unknown only), `before`/`after` side refs (null for added/removed), `id =
sha256(canonical(record without id))` via `ev.canonical`. Side ref: `snapshot_id`,
`generation_id`, `occurrence_id` (definition), `evidence_id`, `path`, `source_sha256`,
`start_byte`, `end_byte`, `line`, `column`, `end_line`, `end_column`,
`declaration_sha256` (raw span bytes), `own_text_sha256`, `signature_sha256`,
`body_sha256` (null if body-less/unsupported), `fingerprint_basis` ∈ {`parsed`,
`raw_text`, `unsupported`}. Change set: `format: "atlas-symbol-diff.v1"`, `before`/`after`
(snapshot_id, generation_id), `compatibility` (identity_kind, identity_rule
`git_common_dir`|`same_root`, schema, sha256 of configuration and producer), `changes`
sorted by (path, entity_id) with path = after.path or before.path, `candidates` sorted by
(before_entity_id, after_entity_id), `partial_paths` (sorted paths whose extraction failed
on a side), `summary` (every category + `unchanged` + `candidates`), `limitations` (static,
sorted), `id` = sha256(`"atlas-symbol-diff.v1:"` + canonical(rest)). No absolute paths,
timestamps, durations or host data. CLI prints `ev.canonical(change_set)` + newline.

D6 Unknown reasons (closed): `fingerprint_unsupported_kind` (raw text differs for a kind
outside the parsed map), `extraction_failed_before`/`extraction_failed_after` (observation
`failed`; that side's facts are retained stale history), `definition_occurrence_ambiguous`
(≠ 1 definition occurrence), `source_unavailable` (`read_source` fails),
`declaration_node_not_found` (no node of an expected type with the exact span),
`parse_error_in_declaration` (ERROR/MISSING inside the span), `uncovered_source_text` (gap
check), `unmapped_declaration_child`, `non_utf8_declaration`, `inconsistent_evidence` (same
file sha256 on both sides but different occurrence id). Added/removed entities whose
fingerprint is unsupported keep their category with `fingerprint_basis: "unsupported"` and
are never candidates. Fast path: a path with the same `source_sha256` on both sides and
both observations `indexed` has its shared entities `unchanged` without parsing (after
asserting equal occurrence ids); only hash-differing files are re-parsed, each distinct
(path, sha) at most once per side.

### Normalization rule (exact)

Inputs: source bytes only from `reader.read_source(path, source_sha256)` (hash-verified);
parse with `get_parser(lang)` from the same `tree-sitter`/`tree-sitter-language-pack`
versions (guaranteed by required `producer` equality, which pins package versions). The
declaration node is the unique node whose `(start_byte, end_byte)` equals the definition
occurrence and whose type is in the map for (lang, kind); else
`declaration_node_not_found`.

`ser(n)` (canonical nested JSON value):
- Comment nodes (`COMMENT_TYPES[lang]`: python `comment`, `line_continuation`; js/ts/tsx
  `comment`; ruby `comment`; rust `line_comment`, `block_comment`; bash `comment`; scss
  `comment`, `single_line_comment`; confirmed at qualification) are omitted with their
  subtrees.
- `n.type == "ERROR"`, `n.is_missing`, or `n.has_error` → unknown
  `parse_error_in_declaration`.
- Atomic node (leaf, or `n.type` in `ATOMIC_TYPES[lang]`: string/template/heredoc/regex/
  char/raw-string literal types, confirmed at qualification) → `[n.type, text(n)]`; text
  decoded strictly as UTF-8 (else `non_utf8_declaration`). Whitespace-only leaves are real
  tokens and kept verbatim. Atomic literals keep internal whitespace and interpolations
  verbatim (conservative: over-reporting a body change is allowed, a false "unchanged" is
  not).
- Otherwise → `[n.type, [ser(c) for c in n.children if c not omitted]]`. Gap check: the
  bytes between `n.start_byte`, each child span and `n.end_byte` must be ASCII whitespace
  only, else `uncovered_source_text` (fails closed on any grammar leaving text outside
  nodes).
- Placeholders: name field child → `["<name>"]`; body child (inside the signature) →
  `["<body>"]`; nested declaration (node whose span equals another entity's definition
  occurrence in the same file and side; outermost only) → `["<decl>", kind, name]`.

Node types and nesting (not only the leaf sequence) make block structure part of the
fingerprint — required for Python indentation-only nesting changes with equal leaf
sequences.

Fingerprints: `signature_sha256` = sha256(canonical({"context": [ser(c) for c in context
nodes], "declaration": ser(decl with body→`<body>`, name→`<name>`)})); `body_sha256` =
sha256(canonical(ser(body))) or null for body-less kinds; `own_text_sha256` =
sha256(canonical(raw text segments of the declaration span with each outermost nested
declaration span replaced by `{"decl": [kind, name]}`)); `declaration_sha256` =
sha256(raw span bytes). Raw-text basis (kinds outside the parsed map): only
`own_text_sha256` and `declaration_sha256`; equal text → `location`/`unchanged`, different
text → `unknown(fingerprint_unsupported_kind)`.

Context nodes (syntax the grammar places outside the declaration node but part of the
contract; comments skipped): python `decorator` children of a parent
`decorated_definition`; javascript/typescript/tsx tokens of a parent `export_statement`
other than the declaration (`export`, `default`) plus contiguous preceding `decorator`
siblings if the grammar places them outside; tsx declarators: the `lexical_declaration`
keyword (`const`/`let`) and its parent `export_statement`; rust contiguous preceding
`attribute_item` siblings; ruby inline visibility call wrapper (`private`/`protected`/
`public`/`module_function`/`private_class_method`/`public_class_method` whose
`argument_list` holds the method). Section-style Ruby `private` is a documented
limitation.

Closed-map fail-closed rule: if a body-bearing (lang, node type) lacks its body
child/field yet has named non-comment children not under a listed signature field →
`unmapped_declaration_child` (guards against grammar versions where body statements are
unfielded direct children).

### Per-language declaration map (hypothesis; Day-1 qualification confirms or demotes)

| lang | kind | node type | name | body | signature covers | basis |
| --- | --- | --- | --- | --- | --- | --- |
| python | function | function_definition | field `name` | field `body` | async/def, type_parameters, parameters, return_type, decorators (context) | parsed |
| python | class | class_definition | `name` | `body` | type_parameters, superclasses, decorators | parsed (container) |
| javascript, typescript, tsx | function | function_declaration | `name` | `body` | async, `*`, type_parameters, parameters, return_type, export (context) | parsed |
| javascript, typescript, tsx | method | method_definition | `name` | `body` | decorators, accessibility/static/readonly/override/async/get/set/`*`, type_parameters, parameters, return_type | parsed |
| javascript, typescript, tsx | class | class_declaration | `name` | `body` (class_body) | decorators, type_parameters, class_heritage, export | parsed (container) |
| tsx | function | variable_declarator (value arrow_function/function_expression) | `name` | value's `body` (block or expression) | declarator `type`, value async/type_parameters/`parameters` or `parameter`/return_type; const/let + export (context) | parsed |
| ruby | method | method, singleton_method | `name` | `body` (body_statement) | parameters; singleton `object` + `.`; inline visibility (context) | parsed |
| ruby | class, module | class, module | `name` | `body` | class `superclass` | parsed (container) |
| rust | function, method | function_item | `name` | `body` | visibility, function_modifiers, type_parameters, parameters, return_type, where_clause, attributes (context) | parsed |
| rust | method | function_signature_item | `name` | none (null) | everything | parsed, body-less |
| rust | struct, enum | struct_item, enum_item | `name` | none (field/variant lists are contract) | everything incl. fields/variants, attributes | parsed, body-less |
| rust | trait, module | trait_item, mod_item | `name` | `body` (declaration_list; absent for `mod x;` → null) | bounds, visibility, attributes | parsed (container) |
| bash | function | function_definition | `name` (word) | `body` | `function` keyword/`()` header only (no declared parameters) | parsed |
| scss | mixin, function | mixin_statement, function_statement | first `identifier` child (unfielded) | child of type `block` | `parameters` child (optional) | parsed (type-selected) |
| scss | placeholder, variable, selector, id | — | — | — | — | raw_text |
| html | id | — | — | — | — | raw_text |
| yaml | key | — | — | — | — | raw_text |
| markdown | heading | — | — | — | — | raw_text |
| any | `scip_*` | — | — | — | — | excluded (limitation) |

Stop rule: any parsed row failing qualification or its per-row tests is demoted to
`raw_text` with a listed limitation — never faked. Minimum that must stay `parsed` for the
ACs: python function/class and typescript function/method/class.

### Work plan (timebox 5 engineering days)

1. Day 1 — grammar qualification: dump the locked grammars' trees for every map row
   (decorated/exported/attributed forms, empty bodies, `mod x;`, endless Ruby method,
   single-param arrow, string-whitespace literals, Python dedent case, comment
   placements). Evidence under `.spectra/evidence/aci-s19/grammar/`. Freeze
   `COMMENT_TYPES`, `ATOMIC_TYPES`, the map and context rules.
2. Day 2 — `query/diff_syntax.py`: map, `ser`, gap check, placeholders, context,
   fingerprints. No `atlas_aci.codegraph` mutation, no fingerprinted module edits.
3. Day 3 — `query/diff.py`: `Side(repo, graph_root=None)`, `compare(before, after) ->
   dict`, `DiffRejected`, context checks (D2), entity + definition-occurrence loading via
   SQL on each lease (`entities`, `occurrences` where `kind='definition'`, `evidence` by
   path; exclude `kind='file'` and `scip_*`), observations via
   `reader._observation_records(reader.db)`, `manifest["current"]` path vector,
   categories, candidates, records, ids.
4. Day 4 — `__main__.py` `diff` command; docs; contract and supporting tests.
5. Day 5 — gates in the lock-faithful Docker dev image (ruff check/format, mypy, full
   pytest unprivileged), producer/MCP invariance gates, then RUN-01/02.

Gates beyond the ACs:
- G-PRODUCER: `git diff --exit-code 66d8a38 -- mcp-server/src/atlas_aci/{codegraph.py,
  storage/,extractors/,providers/,indexing/,resolvers/,protocol/,tools/,server.py,
  enforcement.py}`; existing pinned generation-id and envelope tests pass unchanged.
- G-MCP: `atlas-aci tools` output byte-identical to base; `READ_ONLY_TOOLS` unchanged.
- G-RO: SHA-256 inventory of both graph roots and both repos identical before/after every
  diff call, including rejection paths.

### Independent checker conditions

Independent ATLAS review: APPROVE_WITH_CONDITIONS, recorded in
`.spectra/plans/aci-s19.review.json`. Every condition below is binding on
implementation and overrides any conflicting text above.

1. Failed extraction. A failed path's rows are deleted from the active tables
   (codegraph.py:3055-3064); they are not retained. For a path that is `failed` on
   either side:
   - emit one-sided `unknown` records (`extraction_failed_before` or
     `extraction_failed_after`, with the other side null) for the surviving side's
     entities;
   - never emit `added` or `removed` for them;
   - list the path in `partial_paths`.

   A real-parse-failure test asserts this exact shape. AC-004 applies to records that
   have both sides.
2. Candidate substance. A trivial body never yields a candidate. Trivial means one of:
   - only `pass`, `...`, an empty block, or a lone literal, `return` or identifier;
   - fewer than a fixed minimum of non-punctuation body tokens. Choose the constant and
     document it.

   Body-less kinds (rust struct/enum, `function_signature_item`, `mod x;`) are excluded
   from candidates unless their normalized signature is non-trivial; document the rule.
   Add negative tests for two unrelated `pass` functions and for two unrelated unit
   structs.
3. Lease error mapping. Wrap both sides in an `ExitStack` that closes both leases on
   every path, including rejection. Map errors as follows:
   - `ToolError` `INDEX_UNAVAILABLE` → `index_unavailable`.
   - `SNAPSHOT_UNAVAILABLE`, or `reader.snapshot_id is None` / `_source_lease is None` →
     `source_snapshot_unavailable`.
   - The `CodeGraph` constructor `ValueError` (graph root inside the repo,
     codegraph.py:889) and a closed graph → a clean CLI error.

   No traceback reaches stdout.
4. Evidence loading. Evidence tables are `(id, path, payload)` (evidence.py:25), with no
   `kind` column:
   - select by `path` and parse the payload JSON;
   - the definition occurrence has `role == "definition:def." + kind` and
     `provenance_state == "source"`;
   - exclude `kind == "file"` and `scip_*`;
   - if not exactly one matches → `definition_occurrence_ambiguous`.
5. Replay scope. Generation and snapshot ids embed the absolute repo path
   (codegraph.py:1043; snapshots.py:354, 393). Requirements:
   - AC-006's rebuild half rebuilds at the same path: restore v1 and then v2 into fresh
     roots, and assert equal `generation_id`, `snapshot_id` and change-set `id`.
   - `symbol-diff.md` states that ids are not portable across checkout paths.
   - The CLI writes `ev.canonical(...).encode("utf-8")` plus a newline to
     `sys.stdout.buffer`.
6. Reasons tested. Every closed rejection reason has a test built from real inputs, or
   is documented as defensive and unreachable:
   - `producer_mismatch`: patch `_producer_inputs` only during one side's build (input
     fabrication, not the compare path).
   - `configuration_mismatch`: use `CodeGraph(langs=[...])`.
   - `schema_mismatch`: use an epoch-8 graph built from the native8 fixture, or mark it
     defensive.

   Verify that the running `tree-sitter` and language-pack versions equal the sides'
   producer packages, or reject with `producer_mismatch`.
7. AC-003 wording. A top-of-file shift makes every later entity `location`. The test
   asserts:
   - no record of any category other than `location` in that file;
   - entity ids and ordinals unchanged.

   The fixture inserts only blank lines and comments.
8. Normalization.
   - The gap check treats omitted comment spans as covered.
   - The declaration node is found by exact span and type, walking up from
     `descendant_for_byte_range`.
   - Document that `format` can mask behavior-bearing comments (`# type:`, `@ts-ignore`,
     pragmas).
   - Document that whitespace-only leaves (JSX text, bash newlines) report as `body`.
9. Same namespace. Reject, as a usage error, any invocation where both sides resolve to
   the same (repo, graph-root) namespace.
10. Hygiene.
    - Every `all(...)` over records is preceded by a non-empty assertion.
    - There is at least one negative control per category.
    - The README edit adds no 4-column table inside "### TSX structural navigation"
      (test_s14_capabilities.py:262-275).
    - New `src` files do not contain the string "alter table" (test_thesis_negatives.py:50).
    - Prefer public `reader.snapshot_id` / `generation_id`.
    - The `_observation_records` cost is accepted, or the manifest is read directly; note
      which.

## Stories

As an agent operator reviewing a change, I want to know which declarations were added,
removed, or changed in signature or body between two explicit snapshots, so that I can
see whether an API actually changed rather than just which files changed.

As a reviewer of graph evidence, I want every change record to point at hash-verified
source spans in both snapshots and unrelated or unverifiable snapshot pairs to be
rejected, so that a diff never presents guessed identity or stale facts as established
change.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s19.criteria.md` (sha256 `fea6cab2…ef64e3`). All in
`mcp-server/tests/slices/test_s19_contract.py`; each side built by the real indexer
(`CodeGraph.build()`) into its own graph namespace and compared through the real library
(CLI where noted). No mocks of the behavior under test.

| AC | Test | Observable |
| --- | --- | --- |
| AC-S19-001 | test_signature_change | `def area(w, h)` → `def area(w, h, scale=1)` (python) + TS method parameter-type edit: exactly one record per entity id, category `signature`, `signature_changed` true, signature sha differs, same entity id, no added/removed for it |
| AC-S19-002 | test_body_change | `return w * h` → `return w * h * 2` → `body` (signature sha equal, body sha differs); controls: comment/whitespace-only edit to another function → `format` (not `body`); Python statement dedent (equal leaf sequence) → `body` |
| AC-S19-003 | test_location_only | blank lines + module comment inserted above → `location`; equal entity id, `declaration_sha256`, fingerprints; `after.line == before.line + N`; occurrence ids differ; no other record for that file |
| AC-S19-004 | test_diff_evidence | every both-sides record: `before/after.snapshot_id` equal each side's lease `snapshot_id` and differ from each other; generation ids match; `sha256(read_source(path, source_sha256)[start:end]) == declaration_sha256` on each side; `occurrence_id`/`evidence_id` exist in that side's DB with this `entity_id` and role `definition:def.<kind>` |
| AC-S19-005 | test_incompatible_repositories | two unrelated `git init` repos with identical content → `DiffRejected("repository_identity_mismatch")`, CLI exit 1, empty stdout; two distinct non-git dirs → rejected; git vs non-git → `identity_kind_mismatch`; G-RO inventory unchanged; positive controls: worktree pair (same `common_dir`) and same path with two roots accepted |
| AC-S19-006 | test_diff_replay | same pair compared twice in-process and once via CLI subprocess → byte-identical canonical change sets, equal `id`; rebuilding both sides into fresh roots → equal snapshot ids and identical change set |
| AC-S19-RUN-01 | gate | SHA-256 manifest of declared tracked implementation files equal before/after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths |
| AC-S19-RUN-02 | gate | `slice_id` + `criteria_sha256` equal in interrupted and resumed receipts; one authoritative receipt path `.spectra/state/aci-s19.execution.json` |

Supporting tests (same real-build discipline):
- `test_s19_languages.py` (parametrized per `parsed` map row): signature edit →
  `signature`; body edit → `body`; comment/whitespace edit → `format`; shift →
  `location`; string-literal whitespace `" "`→`"  "` → `body`; decorator/export/
  attribute/inline-visibility edit → `signature`; container with edited member → parent
  `unchanged`/`location`; member added → parent `body`; raw_text kinds → `location` on
  shift, `unknown(fingerprint_unsupported_kind)` on text change.
- `test_s19_semantics.py`: unique rename with identical body → one `rename` candidate,
  `removed`/`added` retained; file move → `move` candidate; two identical removed → no
  candidate (ambiguity); `area`→`area2` with different body → no candidate; recursive
  function renamed → no candidate; parse failure after → `unknown(extraction_failed_after)`
  + `partial_paths`; JSONL-imported side → `source_snapshot_unavailable`;
  configuration/producer mismatch rejected; `scip_import` side excludes `scip_*`
  entities; unchanged-file fast path exercised.
- `test_s19_cli.py`: option resolution (`--repo`/`--before-repo`/`--graph-root` …),
  conflicting options rejected, canonical stdout bytes.

## Confidence

Computed with `ramza-score` (recorded in state gates). Identity, snapshot binding, leases
and worktree identity are established, tested predecessors; comment-free token
normalization has an in-repo precedent (`rust.impl_header`); the approach avoids every
fingerprinted module, so generation ids and envelopes cannot churn. Main uncertainty:
per-grammar field shapes (not dumped at planning) and soundness of CST-shape
serialization for whitespace-significant syntax — covered by Day-1 qualification,
fail-closed gap/map checks and demotion to `raw_text`.

## Rejected Alternatives

- Persist signature/body fingerprints at index time (`storage/evidence.py` or
  `codegraph.py`): changes the producer fingerprint → every generation id, pinned fixture
  and snapshot churns; does not help existing indexes. Deferred.
- `graph_query` diff op / MCP tool: touches protocol enums and measured envelopes; MCP
  serves one namespace per process; would need a second-namespace path through
  enforcement.
- Leaf-token-only normalization (no tree shape): equates Python bodies differing only by
  indentation nesting.
- Raw-text/line diff of declaration spans: cannot separate signature from body or ignore
  comments soundly.
- Name/similarity rename heuristics: excluded by the issue; fabricates identity.
- Comparing repositories by absolute path only: rejects legitimate worktree pairs and is
  no safer than `common_dir`.
- Relaxing producer equality to a subset: plausible later, but cross-build comparability
  of entity ids and grammar output is unproven; strict equality is honest now.

## Risks

- Grammar field shapes differ from the map (Ruby body field, bash/scss unfielded
  children, Rust where/visibility, TS decorator placement) → Day-1 dump, closed-map
  fail-closed checks, demotion to `raw_text` with a limitation; never faked.
- Whitespace-significant syntax hidden from the CST (string content without child nodes,
  heredocs, bash newline separators) → gap check fails closed; atomic literal types;
  per-language whitespace-literal tests.
- Entity ordinals include anonymous scope nodes, so an unrelated block insertion can
  re-identify nested declarations (spurious removed/added) → candidate matcher pairs
  them; documented.
- Duplicate same-name siblings swapped in order appear as signature/body changes, not
  moves (ordinal identity); documented limitation.
- Private-API coupling (`reader._source_lease.manifest`, `reader._observation_records`)
  accepted to avoid editing fingerprinted `codegraph.py`; S08 tests use the same
  attributes; documented.
- Path-based git identity cannot detect an unrelated repository re-created at the same
  path; documented limitation.
- Large repositories: only hash-differing files are parsed; memory O(entities) per side;
  no MCP bounds apply (CLI). Report if a measured fixture exceeds reasonable limits rather
  than add a silent cap.
- Docker runtime-hash tests (S13/S17/S18) compare image vs host modules — rebuild the
  image from the branch before gates.
- Timebox 5 engineering days; at the limit report completed checks, demoted rows and
  gaps; no silent growth.
