# Architecture rules: forbidden-import checks

`atlas-aci check-architecture` evaluates a small declarative rule document against **one indexed
snapshot** and prints a canonical report. Each rule says "files in group A must not import files in
group B". A violated rule carries the exact import chain, one hop per import statement, with the
same `canonical_assertion_proof` provenance that `graph_query op=path` returns.

It is an operator command only. There is no MCP operation and no new tool: the tools JSON is
byte-identical. It is read-only: it opens a read-only connection on the leased graph and writes
nothing under the repository or the graph root.

```sh
atlas-aci index --repo /path/to/repo
atlas-aci check-architecture --repo /path/to/repo --rules rules.json [--graph-root DIR] [--max-...]
```

## Read the result correctly

* **Exit 0 does not mean the repository complies.** The command exits 0 whenever the check ran,
  whatever the outcomes, and `--fail-on-violation` does not exist: the checker reports, it does not
  enforce. Read each rule's `outcome` and `reasons`. Exit 1 is a typed rejection, exit 2 is usage.
* `satisfied_within_scope` is **not a proof of absence**. It says: within the supported facts, no
  forbidden import path exists. See the limitations below; most real repositories will report
  `unknown`.
* `violated` with a non-empty `reasons` object is **sound but not exhaustive**. Every emitted
  witness is a real chain of indexed import statements, but the search met a boundary or a bound
  elsewhere, so further violations may exist.

## Rule document `atlas-architecture-rules.v1`

```json
{
  "format": "atlas-architecture-rules.v1",
  "rules": [
    {
      "id": "domain-not-infra",
      "description": "optional, at most 512 characters",
      "relation": "import",
      "from": ["pkg/domain/**"],
      "to": ["pkg/infra/**"]
    }
  ]
}
```

* `id` matches `^[a-z0-9][a-z0-9._-]{0,63}$` (the full string; a trailing newline is rejected) and
  is unique in the document.
* `relation` must be `import`. Calls and other relations are not supported.
* `from` and `to` are non-empty, duplicate-free lists of at most 32 selectors of 1 to 512
  characters.
* Limits of the document: 256 KiB, 64 rules, nesting depth 16 (`[`/`{` outside strings; deeper is
  `invalid_json`, checked by a linear pre-parse scan, never by the interpreter's recursion limit). Bounds are never supplied by a rule; they are command
  options.
* The schema is closed (no extra keys). Duplicate keys, `NaN`/`Infinity`, a byte order mark,
  non-UTF-8 bytes and lone surrogates are rejected.

### Selectors

Selectors match **observed source files** of the snapshot, as repository-relative POSIX paths.
Matching is by exact Unicode code point: no normalization (NFC/NFD), no case folding, no regex and
no fnmatch.

| Selector | Matches |
| --- | --- |
| `**` | every observed file |
| `dir/**` | every file below `dir/`, at any depth |
| `dir/*` | files directly in `dir/` (cost is proportional to the subtree size) |
| `dir/file.py` | exactly that file; a directory name as a literal matches nothing |

Anything else is malformed: `*`, `?`, `[`, `]`, `{`, `}`, `!`, backslash, control characters, empty
segments, `.` or `..` segments, a leading or trailing `/`, `*` or `**` anywhere but the last segment,
and a non-relative path.

The same file may be both a source and a target. A pair `(s, s)` is a violation only through an
import cycle of length at least 1.

**Prefer narrow selectors.** `**` and `dir/**` also select non-Python files that the indexer
observed (markdown, yaml, ruby, tsx, ...). Each is an `import_language_unsupported` boundary and
turns the rule into `unknown`. Select Python trees such as `pkg/**`.

### Rejections

An invalid document is rejected before the graph is opened, with a structured `invalid_rule` error:
`{error, reason, rule_index, rule_id}`. `rule_id` is set only when it matches the id pattern.
Selector text, descriptions and paths are never echoed.

`reason` is one of `too_large`, `invalid_json` (including nesting deeper than 16), `unsupported_format`, `schema`,
`unsupported_relation`, `malformed_selector`, `duplicate_rule_id`, checked in that order (the first
failure wins). Per rule, in document order: selector count, relation, each `from` then `to`
selector, then the id.

## What an import edge is

A file-level breadth-first search follows `import` assertions whose resolution is `EXTRACTED` or
`INFERRED` and that have a target entity. The source file is the file holding the import statement
(`assertions.path`, including imports written inside a function), the destination is the file of
the imported definition. A witness is a list of file nodes and one provenance hop per edge.
Consecutive hops join by file, not by entity.

Per rule and per source file the search is level by level, in ascending path order, with edges in
ascending assertion id, so the witness is the same on every run. It is the shortest chain; ties
break lexicographically from the target backwards. Each violating `(source, target)` pair gets one
witness.

## Outcomes and reasons

`outcome` is one of, in precedence order:

1. `violated`: at least one violating pair was found.
2. `incomplete_due_to_budget`: no pair, but a bound stopped a search or limited the sources.
3. `unknown`: no pair and no bound, but a boundary or an empty selection.
4. `satisfied_within_scope`: no pair, `reasons` empty.

`reasons` always lists every reason that applies, including on a violated rule.

Budget reasons: `depth_limit`, `visited_node_limit`, `examined_edge_limit`,
`total_examined_edge_limit`, `source_limit`.

Unknown reasons (each is a boundary, except the two empty selections):

* `unresolved_import`: an import fact in a visited file is `UNRESOLVED` or has no target
  (absolute imports, aliases, stars, control-flow imports, submodule imports, constants, ...).
* `dynamic_import_call`: a visited Python file calls one of `__import__`, `exec`, `exec_module`,
  `import_module`, `load_module`, `module_from_spec`, `reload`, `run_module`, `run_path`,
  `spec_from_file_location`. The probe matches **callee names only**, so a locally defined
  function named `reload` or `exec` is a false positive (an `unknown`), and an aliased loader
  (`im = importlib.import_module`) is a silent false negative.
* `import_language_unsupported`: a selected or reached file is indexed but not Python (tsx imports
  are always unresolved; ruby, js, ts, go, rust and java have no import facts).
* `file_not_indexed`: a selected or reached file has no `indexed` observation (a parse failure, a
  disabled language, or an unobserved path). Facts retained for a failed file are never traversed.
* `empty_source_selection`, `empty_target_selection`: a selector set matched nothing, which usually
  is a typo; it is never silently satisfied.

A boundary in a file that is never visited does not affect the outcome. A boundary carries its
file, observation state, and, when an import or call row is involved, the assertion, evidence and
occurrence ids with the source line.

## Bounds

| Option | Default | Range | Unit |
| --- | --- | --- | --- |
| `max_depth` (`--max-depth`) | 16 | 1..32 | hops per source search |
| `max_visited_nodes` | 1000 | 1..1000 | files per source search, source included |
| `max_examined_edges` | 10000 | 0..10000 | import rows per source search |
| `max_total_examined_edges` | 200000 | 0..2000000 | import rows per run |
| `max_sources_per_rule` | 256 | 1..4096 | sorted prefix of the source selection |
| `max_counterexamples_per_rule` | 16 | 1..64 | emitted witnesses (all pairs are counted) |
| `max_boundaries_per_rule` | 32 | 0..256 | emitted boundaries (all are counted) |

An out-of-range value is a usage error (exit 2): `Invalid architecture check limit: <name>`.
All bounds are counts: there is no clock, so a report depends only on the snapshot, the rule bytes,
the checker version and the bounds. Two evaluations are byte-identical, and a changed bound changes
`report_id`.

`depth_limit` is exact: at `max_depth` the frontier files are probed with a charged adjacency read,
and the search only reports `depth_limit` when a frontier file has an admissible import that would
still change the result. A leaf at exactly `max_depth` is not a stop.

**Cost.** Every examined import row is charged. The adjacency query reads every assertion stored
under a visited file path (calls and references as well as imports, because the `relation` test is a
residual filter on `idx_assertions_path`); decoding those rows is **not** charged to
`examined_edges`. Only `(id, target_entity_id, resolution)` tuples are cached. There is no output
byte cap: at the maxima a rule can emit 64 witnesses of up to 32 hops, each hop about 2.5 KB of
provenance, so a 64-rule document can produce a very large report; choose the bounds accordingly.

## Report `atlas-architecture-report.v1`

Canonical JSON plus one newline, repository-relative paths only (no clock, host or absolute path).
Top-level keys: `format`, `checker` (`atlas-architecture-check.v1`), `rules_format`,
`rules_sha256`, `snapshot` (`snapshot_id`, `generation_id`), `effective_limits`, `support`,
`limitations`, `summary`, `work`, `rules`, `report_id`.

* `support.scope = {languages, skip_patterns}` echoes the indexer configuration of the snapshot.
  Files in a skipped directory and files whose extension is not an indexed language (for example
  `.pyi`) are never observed and are silently outside every selector.
* `report_id` is the SHA-256 of the report without it; the CLI validates the report schema,
  outcome consistency, counts and the id before printing.
* Per rule: `outcome`, `reasons`, `selection`, `counts`, `counterexamples`, `boundaries`, and
  `stops` (one per stopped source search: `source_path`, `stop_reason`, `depth`,
  `visited_files`, `examined_edges`).

## Typed rejections (exit 1, nothing on stdout)

| stderr | Meaning |
| --- | --- |
| `Architecture rules rejected: invalid_rule: <reason>[ (rule N)]` | invalid rule document |
| `Architecture check rejected: index_unavailable` | no complete, compatible graph generation |
| `Architecture check rejected: source_snapshot_unavailable` | no source snapshot (for example an imported generation): an exact snapshot is required |
| `Architecture check rejected: index_upgrade_required` | the graph is older than native epoch 8; re-run `atlas-aci index` |
| `Architecture rules file cannot be read.` | the `--rules` path cannot be read |
| `Architecture check failed: <ExceptionType>` | any other operational failure |

The graph namespace that cannot be opened is a usage error (exit 2):
`The repository cannot be opened as a graph namespace.` Messages never contain a path.

## Limitations

Every report carries these flags, and they apply to every result:

1. `dynamic_imports_are_not_modeled`: `importlib`, `__import__`, `exec`, `sys.path` and
   `sys.modules` manipulation and plugin loaders produce no import facts, so a
   `satisfied_within_scope` result cannot see them.
2. `dynamic_import_probe_matches_callee_names_only`.
3. `satisfied_within_scope_is_not_a_proof_of_absence`.
4. `python_relative_imports_only`: only package-local, unaliased, unconditional relative
   `from .m import name` of a module-level definition resolves.
5. `absolute_imports_are_unresolved_boundaries`: the facts cannot tell the standard library from
   internal absolute imports, so real repositories often yield `unknown`.
6. `import_targets_must_be_module_level_definitions`: `from . import mod` and constants are
   boundaries.
7. `import_edges_are_explicit_statements_not_package_initialization`: implicit `__init__.py`
   execution is not an edge.
8. `non_python_imports_are_not_modeled`.
9. `selectors_match_observed_source_files_only`: see `support.scope` above.
10. `depth_limit_stop_is_conservative`: a frontier file whose only admissible import leads back
    into already visited files does not stop the search, but any other admissible import at
    `max_depth` does, even if it would never reach a target.
11. `reports_do_not_enforce_policy`.
12. `not_exposed_over_mcp`.

Reuse notes for maintainers: the checker uses the private `symbol_diff._open` and `_lease` and
decodes records locally with `ev.validate_record`, with a test asserting equality with
`IndexedReader._record`. `IndexedReader` and `navigate` are not used: `navigate` walks entity ids,
so a file-level chain would dead-end as a false `no_path`.
