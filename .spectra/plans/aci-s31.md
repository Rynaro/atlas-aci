---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-04T02:00:00Z
slice_id: ACI-S31
issue: https://github.com/Rynaro/atlas-aci/issues/52
---
# ACI-S31 — check declarative forbidden-import rules with bounded counterexamples

## Scope

Issue #52 on base `69e9a46` (main after PR #88 / ACI-S37), branch `aci-s31`. The marker `<!-- atlas-aci:slice:ACI-S31 -->` is verified. There is no prior S31 plan, receipt or execution state. The frozen criteria hash to `7491b64310b6763d3c0416069e5ee9ca79b5dc88a9bd6befb92d905710600d64` (7 product ACs plus RUN-01 and RUN-02), which matches the contract table. Predecessors on base, both ancestors with COMPLETE receipts:
- S12 (#34): `query/traversal.py`, `query/indexed.py`;
- S20 (#41): `query/impact*.py`, with path shape `{nodes,hops}`.

Observed facts (re-verified at planning; `M/` = `mcp-server/src/atlas_aci/`):
- **Import facts.**
  - Only python and tsx emit `import` (`codegraph.py:1488-1489, 3959-3983`).
  - `resolve_import` writes `assertion(source=owner(import_node), target=<definition entity in the target module>, "import", INFERRED|UNRESOLVED, raw_target=name, reason)` with `assertions.path` = importing file (`:4853-4870`).
  - The owner is the file entity, or the enclosing def/class (`:3772-3780`).
  - Python resolves only package-local, unaliased, unconditional relative `from .m import <top-level def>` (`providers/python_imports.py:31-128`). Absolute imports, aliases, stars, control-flow imports, missing package markers, submodules and constants are UNRESOLVED with a reason.
  - tsx imports are always UNRESOLVED (`codegraph.py:4580-4584`).
  - Ruby, JS/TS, Go, Rust and Java emit no import fact.
  - `importlib` and `__import__` produce no import fact (a silent gap).
  - Resolutions: `{EXTRACTED, INFERRED, AMBIGUOUS, UNRESOLVED, LEGACY_UNAVAILABLE}` (`storage/evidence.py:463-465`).
- **S12 `navigate`** (`query/traversal.py:103-317`):
  - It walks entity ids, so hop 1 lands on a symbol entity that has no outgoing import. A file-level chain dead-ends as a false `no_path`.
  - It skips UNRESOLVED and null-target facts silently (:254-258).
  - It takes one seed and one target.
  - Reusable pure pieces: `identity` :38-54, `hydrate` :77-100 (`canonical_assertion_proof`), `LIMITS` :20-25.
- **IndexedReader** (`query/indexed.py:55-124`) always arms a monotonic-clock deadline (0..2000 ms; 0 expires immediately). The staticmethod `_record(row, kind)` :146-155 validates a row.
- **Lease and snapshot.**
  - `symbol_diff._open` uses `CodeGraph(read_only=True)`, and `_lease` maps errors to `DiffRejected(index_unavailable|source_snapshot_unavailable)` (`query/diff.py:192-209`).
  - `reader.snapshot_id` is None for imported generations (`codegraph.py:1271-1273`; `export_jsonl` :6426, `import_jsonl` :6638).
  - Precedent for rejecting a null snapshot: `diff.py:213-215`.
- **File universe.** `manifest.source_observations` holds one record per observed path, with state `indexed|unsupported|disabled|failed` (`evidence.py:1072-1096`). A failed file may retain stale history facts. There is a direct-read precedent at `impact.py:132-135`. Each parsed file has one `kind=file` entity (`codegraph.py:3790`, `evidence.py:861-871`).
- **Indexes.**
  - `idx_<table>_path` on every canonical table (`evidence.py:24-28`).
  - Navigation indexes (:30-43).
- **Precedents.**
  - CLI error mapping with fixed texts: `__main__.py:816-828`.
  - `ev.canonical` (`evidence.py:181`).
  - Content id: `impact_limits.impact_id` :101-103.
  - Closed schema helpers: `query/validation_schema.py:13-44`. jsonschema is transitive, with `# type: ignore[import-untyped]`.
  - Fixtures: `test_s19_support.write_tree/build/inventory` (:22-47, :92-104).
  - The `test_s07_imports.py:10-74` unresolved reasons.
- **Pins.**
  - Tools JSON: 7 tools, sha `94f9567a…a56ac56`, 1,631,472 bytes.
  - MINIMUM 3436, COLLECTION 3448, FIXED_ERROR 797, IMPACT 7730 (`test_s22_isolation.py:25-26, 61-65`).
  - Core dependencies are pinned (`test_thesis_negatives.py:57-73`).
  - The producer fingerprint hashes codegraph, evidence, python_imports and tsx (`codegraph.py:1440-1463`).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-04)
1. **Architecture.** Option A: a file-level BFS in `architecture/checker.py` over one read-only leased graph (`_open/_lease`). It reuses `traversal.identity` and `traversal.hydrate`, the S20 `{nodes,hops}` shape, the S12 stop vocabulary, `ev.canonical` and a content-addressed id.
   - Justified interpretation of approach step 2: "reuse S12's typed bounded path operation" means its primitives and bounds, not `navigate(op=path)`. Per-pair navigate is REJECTED as unsound.
   - Producer edits (codegraph, extractors, providers, evidence) are REJECTED.
2. **Relations.** rules.v1 supports only `import`.
   - Edges are import assertions with resolution ∈ {EXTRACTED, INFERRED} and a non-null target.
   - src = `assertions.path`; dst = path of the target entity.
   - Any other import fact in a visited file is a boundary.
3. **Outcomes.** Precedence: `violated` > `incomplete_due_to_budget` > `unknown` > `satisfied_within_scope`. The reason vocabulary is closed. Boundaries are counted and listed (bounded) with provenance.
4. **Snapshot.** Exact snapshot only. A null `snapshot_id` is rejected as `source_snapshot_unavailable`.
5. **Bounds.** Count-based only. There is no wall-clock deadline: it is omitted entirely (D1). Each violating (source, target) pair gets one witness, shortest with a stable tie-break.
6. **Rule document.** A closed `atlas-architecture-rules.v1` schema plus semantic checks. Errors are structured `invalid_rule` with a closed reason and rule index/id. Selector text and paths are never echoed.
7. **CLI.** `atlas-aci check-architecture` prints the canonical report plus LF.
   - Exit 0 whether or not a rule is violated (the checker reports and does not enforce; `--fail-on-violation` is OUT).
   - Exit 1 for a typed rejection, exit 2 for usage.
8. **Packaging.** `architecture/__init__.py` by amendment. Rule JSON fixtures live in `tests/fixtures/architecture_rules/`. Source trees are in-test dicts. Docs: `docs/architecture-rules.md`.
9. **Read-only and invariance.** Read-only by `inventory` and a read-only connection. Invariance: no change to server, protocol, query, tools, storage, codegraph, extractors, providers, enforcement, config, pyproject, uv.lock or workflows; the pins hold.

### In
1. `architecture/rules.py` (new):
   - constants and closed vocabularies;
   - `InvalidRule`, `Selector`, `parse_selector`, `select`, `Rule`, `RuleSet`;
   - `SCHEMA`/`VALIDATOR`;
   - `load(raw: bytes) -> RuleSet`.
2. `architecture/checker.py` (new):
   - constants, `LIMITS`, `normalize_limits`, `ArchitectureRejected`, `ArchitectureUsageError`;
   - `open_snapshot(stack, side)`;
   - `check(reader, ruleset, limits) -> dict`;
   - `report_id`, `REPORT_SCHEMA`, `validate_report`;
   - `run(side, rule_bytes, limits)`, `canonical_bytes`.
3. `architecture/__init__.py`: a docstring only.
4. `__main__.py`: the `check-architecture` command.
5. `tests/slices/test_s31_architecture_rules.py`: the 7 AC tests, plus supporting and isolation tests.
6. `tests/fixtures/architecture_rules/*.json`: one valid document and ten invalid ones.
7. `docs/architecture-rules.md`, a CHANGELOG Unreleased entry and a root README docs-table row.

### Out
- MCP op/tool/schema, policy enforcement or exit-on-violation, branch protection, automatic repairs.
- Relations other than `import`; call/semantic restrictions; runtime topology.
- Resolving absolute imports, or classifying them as external.
- Regex or fnmatch selectors.
- Rule-supplied bounds.
- Producer changes of any kind.
- Edits to the decision-9 paths and to `reports/*`.

Declared implementation paths (issue): `architecture/rules.py`, `architecture/checker.py`, `__main__.py`, `tests/slices/test_s31_architecture_rules.py`, `tests/fixtures/architecture_rules/`, `docs/architecture-rules.md`.

Scope amendment (`.spectra/changes/aci-s31/scope-amendment.json`, recorded before implementation) adds `architecture/__init__.py`, `CHANGELOG.md`, `README.md`, and the Ramza, receipt and backlog files.

Rightsize: computed tier `lite` (score 4). An independent checker still runs.

## Approach

### Decisions

**D1 Reuse boundary.**
- Reused unchanged:
  - `symbol_diff._open/_lease/Side/DiffRejected/DiffUsageError`;
  - `traversal.identity`, `traversal.hydrate` and the `traversal.LIMITS` ranges;
  - `IndexedReader._record`;
  - `ev.canonical`, `ev.TABLES`, `ev.relative_path`.
- `hydrate` receives `_Records`, a clock-free adapter with `.reader` (the lease) and `.record(kind, id, *, endpoint=False)`. It runs `SELECT id,payload FROM <ev.TABLES[kind]> WHERE id=?` → `IndexedReader._record(row, kind)` and counts `hydration_queries`. Hops are therefore exactly the S12/S20 `canonical_assertion_proof`.
- IndexedReader is never instantiated: its deadline cannot be disarmed, and a deadline stop would break AC-006. `navigate` is not imported.
- The module docstring lists the private symbols used: `_open`, `_lease`, `_record`.

**D2 Rule document `atlas-architecture-rules.v1`.**
```
{"format":"atlas-architecture-rules.v1",
 "rules":[{"id":"^[a-z0-9][a-z0-9._-]{0,63}$", "description"?:str≤512, "relation":str,
           "from":[selector,…], "to":[selector,…]}]}       # selector: str, 1..512 chars
```
- Draft202012Validator, `additionalProperties:false` everywhere. `rules` has `minItems 1`; `from`/`to` have `minItems 1` and `uniqueItems`. `relation` is typed only as a string, so the semantic step can name it.
- Validation order (first failure wins; documented):
  1. `len(raw) > 262144` → `too_large`.
  2. Strict parse → `invalid_json`. That covers UTF-8 decoding with BOM rejected, duplicate keys via `object_pairs_hook`, and NaN/Infinity via `parse_constant`. Any `Exception`, including `RecursionError`, maps here.
  3. An object whose `format` is present and ≠ FORMAT → `unsupported_format`.
  4. JSON Schema → `schema`, choosing the error with the minimal `absolute_path`.
  5. `len(rules) > 64` → `too_large`.
  6. Per rule in order:
     - a side with more than 32 selectors → `too_large`;
     - `relation != "import"` → `unsupported_relation`;
     - each `from` then `to` selector → `malformed_selector`;
     - id already seen → `duplicate_rule_id`.
- `InvalidRule(ValueError)` has `code="invalid_rule"`, `reason ∈ INVALID_RULE_REASONS`, `rule_index: int|None` and `rule_id`. `rule_id` is set only if it matches the id pattern.
  - `str()` is `"invalid_rule: <reason>"`.
  - `as_dict()` gives exactly `{error, reason, rule_index, rule_id}`.
- `RuleSet.sha256` = sha256 of the raw bytes.

**D3 Selector grammar** (closed, no regex execution, string operations only).
- `**` alone matches all observed files.
- Otherwise the selector is split on `/`. Each segment must be non-empty, must not be `.` or `..`, and must contain no `* ? [ ] { } !`, `\`, NUL or C0/DEL.
  - A final segment exactly `**` means `tree(base)`.
  - A final segment exactly `*` means `children(base)`; base must be non-empty.
  - Otherwise it is `literal`.
- A non-empty base must satisfy `ev.relative_path`. A leading or trailing `/` is malformed.
- Matching runs over `universe` = sorted observation paths (every state):
  - `literal` matches equality;
  - `tree` matches the prefix `base/`;
  - `children` matches the prefix `base/` with no further `/`;
  - `all` matches every path.
- Prefix ranges use `bisect`, so cost is O(log n + matches). A literal naming a directory matches nothing (docs say: use `dir/**`). Files matching both sides are both source and target.

**D4 Facts and SQL.** All SQL is fixed with `?` parameters. G-PLAN asserts the indexes below with `EXPLAIN QUERY PLAN` and no `SCAN` of assertions or entities.
```
OBSERVATIONS_SQL = "SELECT value FROM manifest WHERE key='source_observations'"
ADJACENCY_SQL = "SELECT id,payload FROM assertions INDEXED BY idx_assertions_path "
                "WHERE path=? AND json_extract(payload,'$.relation')='import' ORDER BY id LIMIT 10001"
ENTITY_SQL    = "SELECT id,payload FROM entities WHERE id=?"                       # PK
FILE_ENTITY_SQL = "SELECT id,payload FROM entities INDEXED BY idx_entities_path "
                  "WHERE path=? AND json_extract(payload,'$.kind')='file' ORDER BY id LIMIT 2"
DYNAMIC_SQL   = "SELECT id,payload FROM assertions INDEXED BY idx_assertions_path WHERE path=? "
                "AND json_extract(payload,'$.relation')='call' "
                "AND json_extract(payload,'$.raw_target') IN (<DYNAMIC_IMPORT_CALLEES>) ORDER BY id LIMIT 1"
```
- `admissible(p)` ⇔ the observation exists, has state `indexed` and has language `python`.
  - A missing or non-indexed observation is boundary `file_not_indexed` (stale or retained facts are never traversed).
  - An indexed non-python file is `import_language_unsupported` (tsx included).
- Adjacency, probe, entity-path and file-entity results are cached per path or id for the run. Charging is per examined row, independent of the cache.

**D5 Traversal** (per source, level-synchronous, count-bounded):
```
check(reader, ruleset, L):
  obs = observations(); universe = sorted(obs); total = 0
  for rule in ruleset.rules:                                   # document order
    S = sorted(∪ select(universe, s) for s in rule.from); T = set(∪ select(universe, t) for t in rule.to)
    reasons = {}; if not S: reasons.empty_source_selection; if not T: reasons.empty_target_selection
    if S and T:
      if len(S) > L.max_sources_per_rule: reasons.source_limit; S = S[:L.max_sources_per_rule]
      for s in S:
        if total >= L.max_total_examined_edges: reasons.total_examined_edge_limit; break
        stop = traverse(s)            # pairs found before a stop are kept
        if stop: reasons[stop] = True; stops.append({source_path:s, stop_reason:stop, depth, visited_files, examined_edges})
traverse(s):
  if not admissible(s): boundary(s); return None
  visited = {s}; parent = {}; level = [s]; depth = 0; edges = 0; found = {}
  while level:
    if depth >= L.max_depth: return "depth_limit"             # conservative, S12 :241-242
    nxt = set()
    for f in level:                                           # ascending path
      if probe(f): boundary(dynamic_import_call, f, probe_row)   # D9
      for a in adjacency(f):                                  # ascending assertion id
        if edges >= L.max_examined_edges: return "examined_edge_limit"
        if total >= L.max_total_examined_edges: return "total_examined_edge_limit"
        edges += 1; total += 1
        if a.resolution not in {EXTRACTED, INFERRED} or a.target_entity_id is None:
          boundary(unresolved_import, f, a); continue
        d = entity(a.target_entity_id).path
        if not admissible(d): boundary(d); continue
        if d in T and d not in found: found[d] = chain(parent, f) + [(f, a)]   # length ≥ 1, d may be s
        if d in visited: continue
        if len(visited) >= L.max_visited_nodes: return "visited_node_limit"
        visited.add(d); parent[d] = (f, a); nxt.add(d)
    level = sorted(nxt); depth += 1
  return None
```
- Cycles terminate through `visited`.
- A pair (s, s) is a violation only through a cycle of length ≥ 1.
- The traversal continues through target files.

**D6 Witnesses.**
- Tie-break: the BFS above records the first edge into t at minimal depth. Levels are processed in ascending path order, edges in ascending assertion id, and a node's parent is fixed by its first discovery. Equivalent oracle: among all shortest import paths s→t, the witness is the minimum of the sequence `[(pred_k, id_k), (pred_{k-1}, id_{k-1}), …]`, read from the target backwards. An independent test oracle asserts this.
- Emission: after each traversal, pairs are sorted by target path and appended while the rule has emitted fewer than `max_counterexamples_per_rule`; beyond that they are counted only. Sources ascend, so the emitted list is the prefix of the global `(source_path, target_path)` order.
- `W = {source_path, target_path, length, path:{nodes:[identity(file entity of p) for p in chain paths], hops:[hydrate(_Records, a, "outgoing")]}}`.
- `ev.require` checks, per hop i:
  - `entity(assertion.source_entity_id).path == nodes[i].path`;
  - target path `== nodes[i+1].path`;
  - `occurrence.path == nodes[i].path`.
- `nodes` are file entities, so consecutive hops join by file, not by entity. This is documented as the file-level step.

**D7 Outcome.**
- Vocabularies:
  - `BUDGET_REASONS = (depth_limit, visited_node_limit, examined_edge_limit, total_examined_edge_limit, source_limit)`;
  - `UNKNOWN_REASONS = (unresolved_import, dynamic_import_call, import_language_unsupported, file_not_indexed, empty_source_selection, empty_target_selection)`.
- Each boundary kind sets the reason of the same name.
- `outcome` is chosen in this order:
  1. `violated` iff `violating_pairs > 0` (witnesses are sound regardless);
  2. else `incomplete_due_to_budget` iff any budget reason;
  3. else `unknown` iff any unknown reason;
  4. else `satisfied_within_scope`.
- `reasons` always lists every reason that applies, so a violated rule still shows whether its search was exhaustive.
- Satisfied ⇔ pairs = 0 and reasons = {}. A boundary in a file that is never visited does not affect the outcome.

**D8 Bounds** (CLI options; out of range → usage exit 2). Per-source ranges reuse `traversal.LIMITS`.

| Limit | Default | Low | High | Unit |
| --- | --- | --- | --- | --- |
| `max_depth` | 16 | 1 | 32 | hops per source traversal |
| `max_visited_nodes` | 1000 | 1 | 1000 | files per source traversal (source included) |
| `max_examined_edges` | 10000 | 0 | 10000 | import rows per source traversal |
| `max_total_examined_edges` | 200000 | 0 | 2000000 | import rows per run |
| `max_sources_per_rule` | 256 | 1 | 4096 | sorted prefix of the source selection |
| `max_counterexamples_per_rule` | 16 | 1 | 64 | emitted witnesses (all pairs counted) |
| `max_boundaries_per_rule` | 32 | 0 | 256 | emitted boundaries (all counted) |

Document caps: 256 KiB of bytes, 64 rules, 32 selectors per side, 512-character selectors.

Cost:
- File visits ≤ charged edges + sources.
- Queries ≤ distinct files × 3 + entity lookups + hydration (≤ 4 per emitted hop or boundary).
- The docs state the worst-case output at the maxima.

**D9 Dynamic-import probe** (narrows the silent gap; cut item 4).
- For each expanded python file, DYNAMIC_SQL runs once (cached).
- `DYNAMIC_IMPORT_CALLEES = ("__import__", "exec", "exec_module", "import_module", "load_module", "module_from_spec", "reload", "run_module", "run_path", "spec_from_file_location")`.
- A hit makes boundary `dynamic_import_call` → `unknown`. The spelling is qualified on Day 1 (e).
- Name aliasing (`im = importlib.import_module`) still escapes, so the limitation flags stay.

**D10 Report `atlas-architecture-report.v1`** (canonical JSON + LF; repo-relative paths only; no clock, host or absolute path):
```
{format, checker:"atlas-architecture-check.v1", rules_format, rules_sha256,
 snapshot:{snapshot_id, generation_id}, effective_limits{7},
 support:{relation:"import", edge_resolutions:["EXTRACTED","INFERRED"], import_languages:["python"],
          dynamic_import_callees:[…]},
 limitations:{flag:true…}, summary:{rules_total, violated, incomplete_due_to_budget, unknown, satisfied_within_scope},
 work:{examined_edges, traversals, adjacency_queries, probe_queries, entity_queries, file_entity_queries, hydration_queries},
 rules:[{index, id, description|null, relation, from[], to[], outcome, reasons{},
         selection:{sources_total, sources_evaluated, targets_total},
         counts:{violating_pairs, counterexamples, boundaries_total, traversals_stopped, examined_edges, visited_files},
         counterexamples:[W], boundaries:[B], stops:[{source_path, stop_reason, depth, visited_files, examined_edges}]}],
 report_id}
B = {kind, path, language, observation_state, observation_reason, assertion_id, evidence_id, occurrence_id,
     line, relation, resolution, unresolved_reason, raw_target}       # fixed keys, nulls when n/a
```
- Ordering:
  - boundaries are deduplicated by `(kind, path, assertion_id or "")`, sorted, and the first N hydrated (evidence → occurrence for `occurrence_id`/`line`);
  - stops are sorted by `source_path`;
  - rules follow document order.
- `report_id = sha256("atlas-architecture-report.v1:" + canonical(report − report_id))`.
- `validate_report` (the CLI runs it before printing) checks:
  - the closed `REPORT_SCHEMA` (`obj`/`enum`/`flags` helpers copied locally; the `validation_schema` module is not imported);
  - D7 consistency;
  - emitted ≤ totals ≤ caps;
  - sort orders;
  - `limitations` == LIMITATIONS;
  - the `report_id` recompute.

**D11 Snapshot and read-only.**
- `open_snapshot(stack, side)`: `_open` (`DiffUsageError` passes through), then `_lease` (`DiffRejected` → `ArchitectureRejected(reason)`). A null `snapshot_id` → `ArchitectureRejected("source_snapshot_unavailable")`.
- The checker issues only SELECTs on the lease connection (`mode=ro`). No temp files are written; nothing under the repo or graph root changes.

**D12 CLI** `atlas-aci check-architecture --repo R [--graph-root G] --rules FILE [--max-…]`:
- `run` order: limits → read ≤ 262145 bytes → `rules.load` → open/lease → check → `validate_report`. An invalid rule is rejected before any graph access.
- stdout is `canonical_bytes(report)`; exit 0 whatever the outcomes.
- Fixed stderr texts (`Error: ` prefix from click), stdout empty:
  - `InvalidRule` → `Architecture rules rejected: invalid_rule: <reason>[ (rule <index>)]`, exit 1;
  - `ArchitectureRejected` → `Architecture check rejected: <reason>`, exit 1;
  - `DiffUsageError` → UsageError `The repository cannot be opened as a graph namespace.`, exit 2;
  - a bad limit → UsageError `Invalid architecture check limit: <name>`, exit 2;
  - `OSError`, `ValueError` or `sqlite3.Error` → `Architecture check failed: <TypeName>`, exit 1.
- `str(exc)`, selector text and paths are never printed. `--fail-on-violation` is out.

**D13 Determinism (AC-006).**
- Every collection is ordered by explicit keys: SQL `ORDER BY id`, sorted levels and pairs, sorted selections.
- Sets never reach the output unsorted. Counters are deterministic counts.
- There is no clock, and the AST gate bans `time` and similar modules.
- Snapshot and generation ids are content-addressed, so a fresh build at the same repo path replays.

### Limitations (static `LIMITATIONS`, emitted as flags on every report and stated plainly in the docs)
1. **`dynamic_imports_are_not_modeled`.** importlib, `__import__`, exec, sys.path/sys.modules manipulation and plugin loaders produce no import facts. A `satisfied_within_scope` result cannot see them.
2. **`dynamic_import_probe_matches_callee_names_only`.**
3. **`satisfied_within_scope_is_not_a_proof_of_absence`.**
4. **`python_relative_imports_only`.**
5. **`absolute_imports_are_unresolved_boundaries`.** Real repositories therefore often yield `unknown`.
6. **`import_targets_must_be_module_level_definitions`.** `from . import mod` and constants are boundaries.
7. **`import_edges_are_explicit_statements_not_package_initialization`.** Implicit `__init__.py` execution is not an edge.
8. **`non_python_imports_are_not_modeled`.** tsx is always unresolved; ruby, js, ts, go, rust and java have no import facts.
9. **`selectors_match_observed_source_files_only`.**
10. **`depth_limit_stop_is_conservative`.**
11. **`reports_do_not_enforce_policy`.**
12. **`not_exposed_over_mcp`.**

### Work plan (timebox 3 engineering days)
Day 1 AM: qualification, before any product code. A scratch script writes `.spectra/evidence/aci-s31/qualification.json`.
- **(a) Chain resolves at file level.**
  - Build in-test `pkg/{__init__,domain/__init__,domain/a,domain/b,infra/__init__,infra/c}.py`:
    - `a.py`: `from .b import fb`;
    - `b.py`: `from ..infra.c import fc`, plus one import inside a def.
  - Lease via `_open`/`_lease`. Record:
    - `snapshot_id` is non-null;
    - each import assertion has `path` = the importing file, resolution INFERRED, and a target entity whose path is the imported module;
    - the source is the file entity, or the def for the in-function import.
- **(b) UNRESOLVED reasons.** `import json` → `unsupported_absolute_import`; `as` alias → `unsupported_import_alias`; try/except → `unsupported_import_control`; `from . import b` and `from .b import CONST` → `relative_import_missing_target`.
- **(c) Observations.**
  - Record the state and language of `x.rb`, of `view.tsx` (and its tsx UNRESOLVED reason), and of `x.rb` under `langs=["python"]` (state disabled?).
  - Check a python syntax error (failed?) and whether a `.txt` file is observed.
- **(d) Lookup query plans.** `FILE_ENTITY_SQL` returns exactly one row per python file. The `EXPLAIN QUERY PLAN` of all D4 SQL uses the named indexes or the PK, with no SCAN.
- **(e) Dynamic-call spelling.** Record the call assertion `raw_target` for `importlib.import_module("x")` and `__import__("x")` (path = that file).
- **(f) Imported generation.** `export_jsonl` → `import_jsonl` into a fresh root, then lease: `snapshot_id is None`.
- **(g) Hydration.** `hydrate(_Records, a, "outgoing")` on an (a) assertion succeeds with `source_availability == "available"`.
- **Base invariants** go to `base-invariants.json`: tools sha and bytes, 3436/3448/797/7730, and the full pytest result at base.

STOP RULE:
- (a) fails (no resolved chain, or the target path is not the module) → BLOCKED with the facts. AC-001 is impossible without a producer change. Never mock.
- (b) or (c) differ → fixture spellings and expected reasons follow the observed facts, never invented ones. If no file state other than `indexed` can be produced, `file_not_indexed` is tested only through the disabled-language build, or listed as untested.
- (d) shows a SCAN → rewrite with `INDEXED BY` or a computed-id PK lookup (`ev.file_entity(path,"python")["id"]`). If still a SCAN → return to the orchestrator.
- (e) does not spell either name → drop D9 and its flag; the limitation stays.
- (f) yields a non-null snapshot → keep the code path and record the null-snapshot test as not constructible.
- (g) fails → BLOCKED: hop provenance must be canonical.

Day 1 PM: `rules.py` (D2, D3) and its tests (AC-005, selector grammar).
Day 2: `checker.py` D4-D10 plus AC-001 to AC-004, the oracle, cycles and query plans.
Day 3: CLI, AC-006, AC-007, isolation, docs, CHANGELOG and README, gates in the lock-faithful dev image, then RUN-01 and RUN-02.

Cut order (each cut becomes a listed limitation): 1. README row; 2. boundary `occurrence_id`/`line` hydration (ids only); 3. `REPORT_SCHEMA` (keep the semantic `validate_report`); 4. the D9 probe; 5. the PYTHONHASHSEED subprocess replay.

Never cut: D2 order and closed reasons, the selector grammar, D7 precedence, witness hydration and tie-break, the oracle test, no-clock, the null-snapshot rejection, `report_id`, the read-only and invariance gates.

### Independent checker conditions
Pending. The orchestrator appends them after the independent checker run; they are binding and override conflicting text above.

## Independent checker conditions

Checker `atlas-s31-plan-checker` returned APPROVE_WITH_CONDITIONS (`.spectra/plans/aci-s31.review.json`); this is the validation required by the confidence verdict VALIDATE (83). Every finding below is a binding plan condition and overrides any conflicting text above.

Orchestrator resolutions of the open options (recorded 2026-11-04 under the user's autonomous-execution instruction): condition 7 takes the PREFERRED option — decode records locally with the public `ev.validate_record` plus identity/coverage_test checks, with an equality test against `IndexedReader._record` on every CHAIN record; condition 11 takes the EXACT option — at depth == max_depth perform a charged adjacency probe of the frontier files and report `depth_limit` only if at least one has an admissible outgoing import edge (a leaf at exactly max_depth does not stop as depth_limit); the AC-004 test pins both the leaf case and the true-limit case.

1. **MAJOR. Observation list is not one record per path.**
   - Evidence: `manifest.source_observations` holds `observation` and `history` records (`codegraph.py:3494-3496`, 4936-4938). The plan says "one record per observed path".
   - Binding condition: `observations()` MUST keep only `type=="observation"`, keyed by `path`. The hash field is `observed_source_sha256`, while evidence uses `source_sha256`. Add a failed-file-with-history test, or list it as unconstructible after a Day-1 attempt.
2. **MAJOR. Epoch gate is missing from the adapter.**
   - Evidence: `indexed.py:55-64` rejects an unvalidated lease and any epoch outside {8, 9}. `_Records` skips this.
   - Without it, `INDEXED BY idx_assertions_path` on an older epoch gives a raw `OperationalError`, reported as the untyped "failed" exit 1.
   - Binding condition: `open_snapshot` MUST check `not reader._closed`, `_validated_lease` and `_lease_schema_epoch in {8, 9}`. Otherwise raise `ArchitectureRejected("index_upgrade_required")`, which must be added to the closed reasons, the docs and the CLI text. Day-1 (d) MUST also record whether epoch-8 DBs have `idx_*_path`.
3. **MAJOR. Python's `$` accepts a trailing newline in `id`.**
   - Evidence: jsonschema uses `re.search`. `^[a-z0-9][a-z0-9._-]{0,63}$` therefore accepts `"abc\n"`. The same applies to the "rule_id only if it matches" echo guard.
   - Binding condition: re-check ids with `re.fullmatch` after schema validation. Add the `"a\n"` case to the invalid fixtures and test it as a `schema` reason.
4. **MAJOR. Lone surrogates in selectors or `description` crash the report.**
   - Evidence: `\ud800` parses, passes `relative_path`, and is echoed in the report. `ensure_ascii=False` plus `.encode("utf-8")` then raises `UnicodeEncodeError` after the graph is already opened.
   - Binding condition: reject any lone surrogate in `description` or selectors during validation (`invalid_json` or `malformed_selector`). Add a test. Also state in the docs that matching is exact by code point (no NFC, no case-folding).
5. **MINOR. Adjacency cost is understated.**
   - Evidence: the `ADJACENCY_SQL` plan is `SEARCH ... idx_assertions_path` plus a temp b-tree for `ORDER BY id`. `json_extract` is only a residual filter, so the index is not defeated. It still decodes every assertion at that path, including calls, which `examined_edges` does not charge.
   - Binding condition: the docs MUST say this. G-PLAN MUST assert `SEARCH ... USING INDEX idx_assertions_path` and `USE TEMP B-TREE` and fail on `SCAN assertions`. Cache only `(id, target_entity_id, resolution)` tuples per path, and the target path per id. Cached payloads at 2M charged rows would be unbounded memory.
6. **MINOR. Choose the file-entity lookup and guard None returns.**
   - Evidence: `_record` returns None for `coverage_test` entities.
   - Binding condition: prefer the computed-id PK lookup `ev.file_entity(path, obs.language)["id"]`, verifying `kind=="file"` and the path, over `FILE_ENTITY_SQL`. Any None from `entity()` or `record()` MUST raise `ValueError` (exit 1), never `AttributeError`.
7. **MINOR. Private reuse needs a drift guard (safest pattern).**
   - Binding condition: choose one of two options.
     - Keep `IndexedReader._record` but add a test using `inspect.getattr_static(...) is staticmethod` plus output equality on every CHAIN record.
     - Preferably, decode locally with the public `ev.validate_record` and the identity and coverage_test checks (about 6 lines), plus an equality test against `_record`.
   - Either way, list `_open`, `_lease` and the chosen decode in the docstring.
8. **MINOR. Non-code files make `**` rules always `unknown`.**
   - Evidence: the universe includes md, yaml, html, scss and other `LANG_BY_EXT` languages (`codegraph.py:62-84`). Any such indexed file in a source selection triggers `import_language_unsupported`. This is sound but noisy.
   - Binding condition: pin the behaviour with a `.md` file in the selection. The docs must say to use narrower selectors such as `pkg/**` over python-only trees.
9. **MINOR. Scope echo is missing.**
   - Evidence: `DEFAULT_SKIP_PATTERNS` and `languages` are in `generation_inputs.configuration` (`codegraph.py:2346-2349`). Files in skipped directories, and files with extensions outside `LANG_BY_EXT` (for example `.pyi`), are never observed. They are silently outside selection.
   - Binding condition: add `support.scope = {languages, skip_patterns}` read from the manifest `generation_inputs` JSON. Limitation 9 MUST name this explicitly.
10. **MINOR. Outcome precedence differs from the literal AC wording.**
    - Evidence: AC-003 says boundary "SHALL return unknown" and AC-004 says "mark incomplete_due_to_budget". The plan returns `violated` plus the reason when a witness exists.
    - Binding condition: the docs and tests MUST state that `violated` is sound but not exhaustive whenever `reasons` is non-empty. Docs must also say that exit 0 does not mean compliant.
11. **MINOR. The conservative depth stop gives a false `depth_limit` for a leaf at exactly `max_depth`.**
    - Evidence: the non-empty level at `depth == max_depth` stops without checking whether its files have any outgoing imports.
    - Binding condition: either do an exact check (a charged adjacency probe at that depth) or pin the exactly-max-depth-leaf case in AC-004 and the docs. The plan's claim that the ranges "reuse `traversal.LIMITS`" is imprecise: the default is 16 rather than 3, and the lower bound of 1 is a subset of 0..32.
12. **MINOR. Fixture details.**
    - Each imported name (`fb`, `fb2`, `fd`, `fe`, `fc`, `fc_alt`) MUST be a top-level `def` with no rebinding. Otherwise the import becomes `relative_import_blocked_export`.
    - Every package directory needs an `__init__.py`.
    - The D9 probe also fires on locally defined names such as `reload` or `exec` (a false-positive `unknown`). Document it.
13. **MINOR. CLI details.**
    - `except ArchitectureRejected` and `except InvalidRule` MUST precede the generic `ValueError`, because both subclass `ValueError`.
    - `click.Path(exists=True)` for `--rules` echoes the user path on stderr. Use `dir_okay=False` without `exists` and map the read error to a fixed text, or pin that text in the test.
    - Click's own message for a non-integer limit is acceptable.
14. **MINOR. ruff `RUF001`/`RUF002`/`RUF003` flag ambiguous non-ASCII.** Use ASCII only in `architecture/*.py` and the tests, with `\u` escapes for Unicode selectors. Avoid the substring "alter table" in src.

## Stories
As an architect, I want a small declarative rule that says module group A must not import group B, with the exact import chain for every violation, so I can review the offending statements at a pinned snapshot.

As an agent operator, I want an unresolved, unsupported or budget-truncated analysis reported as `unknown` or `incomplete_due_to_budget` with its boundaries listed, so a clean-looking result is never a hidden gap.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s31.criteria.md` (sha256 `7491b643…0600d64`), verbatim:

```
### AC-S31-001 (event-driven)
GIVEN the fixture contains a supported path from the rule's source set to its forbidden target set
WHEN the rule is evaluated at the pinned snapshot
THEN the checker SHALL return the deterministic shortest provenance-bearing forbidden import path.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_violation_has_counterexample

### AC-S31-002 (event-driven)
GIVEN the declared supported graph scope is complete and contains no forbidden import path
WHEN the rule evaluation finishes within its bounds
THEN the checker SHALL return satisfied_within_scope.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_complete_scope_can_satisfy

### AC-S31-003 (unwanted-behavior)
GIVEN an unsupported or unresolved dependency boundary can affect the rule's declared scope
WHEN the evaluation reaches that boundary
THEN the checker SHALL return unknown.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_incomplete_scope_is_unknown

### AC-S31-004 (unwanted-behavior)
GIVEN the configured node or depth bound prevents completion
WHEN rule traversal exhausts that bound
THEN the checker SHALL mark the rule result incomplete_due_to_budget.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_path_budget_is_visible

### AC-S31-005 (unwanted-behavior)
GIVEN the document contains an unsupported relation, malformed selector or duplicate rule ID
WHEN rule validation runs
THEN the checker SHALL reject the rule document with a structured invalid_rule error.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_invalid_rule_rejected

### AC-S31-006 (event-driven)
GIVEN identical snapshot, rule bytes, checker version and bounds
WHEN the rule set is evaluated twice
THEN the canonical rule report SHALL remain byte-identical.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_rule_replay_is_idempotent

### AC-S31-007 (ubiquitous)
THEN architecture checking SHALL leave the canonical fact store unchanged.
VERIFY: test: mcp-server/tests/slices/test_s31_architecture_rules.py::test_architecture_check_is_read_only

### AC-S31-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S31-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

**In-test trees.** All are built for real with `write_tree` + `build` (git repo, external graph root); there are no mocks. Every `all(...)` is preceded by a non-empty assertion. Every python file below has `pkg/__init__.py` and the package `__init__.py` it needs.

**CHAIN:**
- `domain/a.py` imports `.b.fb`, `.b2.fb2` and `.d.fd`.
- `domain/b.py` has two statements: `from ..infra.c import fc` and `from ..infra.c import fc_alt`.
- `domain/b2.py` imports `..infra.c.fc`.
- `domain/d.py` imports `.e.fe`.
- `domain/e.py` imports `..infra.c.fc`.
- `infra/c.py` defines `fc` and `fc_alt`.

**CLEAN:**
- `domain/a.py` imports `.b.fb`; `domain/b.py` defines `fb`.
- `infra/c.py` imports `..domain.b.fb` (the allowed direction).
- `other/z.py` holds `import json`; it is not reachable.
- `tools/build.rb` is a ruby file outside the selection.

**LINE:** the chain `domain/a→b→c→d`, plus an unreachable `infra/x.py`.

**Rule fixture.** `tests/fixtures/architecture_rules/valid-domain-infra.json` holds two rules, both `to:["pkg/infra/**"]`:
- `domain-a-not-infra`: `from:["pkg/domain/a.py"]`;
- `domain-not-infra`: `from:["pkg/domain/**"]`.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_architecture_violation_has_counterexample | **Pair (a, c), library and CLI bytes equal:**<br>– rule `domain-a-not-infra` is `violated` with `reasons == {}`;<br>– the witness has length 2 and node paths `[a, b, c]`: b wins the length-2 tie over b2 by path, and d→e→c (length 3) is not chosen;<br>– the hop b→c assertion id is the min of the two b→c import ids read from the DB.<br>**Every hop:**<br>– `projection == "canonical_assertion_proof"`, relation `import`, resolution INFERRED;<br>– assertion, evidence, occurrence and producer exist via `ev.get`;<br>– the source and target entity paths and `occurrence.path` match the D6 joins;<br>– `evidence.source_sha256 ==` the indexed observation hash of that file;<br>– `source_availability == "available"`.<br>**Report:** `snapshot.snapshot_id ==` the lease `snapshot_id`.<br>**Rule `domain-not-infra`:** the pairs are {(a,c),(b,c),(b2,c),(d,c),(e,c)} (`violating_pairs == 5`), and (d,c) is `[d, e, c]`. |
| 002 | test_architecture_complete_scope_can_satisfy | **CLEAN, both rules:**<br>– `satisfied_within_scope`, with `reasons == {}`, `boundaries == []`, `counterexamples == []`;<br>– `sources_evaluated == sources_total` (≥ 1), `targets_total ≥ 1`, `traversals_stopped == 0`;<br>– `examined_edges ≥ 1` (non-vacuous).<br>**Limitations:** `dynamic_imports_are_not_modeled` and `satisfied_within_scope_is_not_a_proof_of_absence` are true on every satisfied result, and the docs contain both names.<br>**Unreached boundaries:** `other/z.py`'s unresolved import and the ruby file do not appear.<br>**Positive control:** CLEAN with `b.py` importing `..infra.c.fc` gives `violated`. |
| 003 | test_architecture_incomplete_scope_is_unknown | **CLEAN variants, rule `domain-not-infra`, each `unknown` with the named reason and a boundary for that file:**<br>– `import json` in a.py → `unresolved_import`, `unsupported_absolute_import`;<br>– an alias → `unsupported_import_alias`;<br>– try/except → `unsupported_import_control`;<br>– the unresolved import placed in b.py, reached at depth 1;<br>– `domain/legacy.rb` → `import_language_unsupported`;<br>– `domain/view.tsx` → `import_language_unsupported`;<br>– ruby with `langs=["python"]` → `file_not_indexed`, per Day-1 (c);<br>– `importlib.import_module` in b.py → `dynamic_import_call` (if D9 ships);<br>– rule to `pkg/missing/**` → `empty_target_selection`.<br>**Unresolved boundaries:** `proof` ids resolve, and `resolution` is UNRESOLVED.<br>**Precedence:** CHAIN + `import json` in a.py → `violated` with `unresolved_import` in its reasons. |
| 004 | test_architecture_path_budget_is_visible | **LINE (default bounds give `satisfied`), each with one stop for source a:**<br>– `--max-depth 2` → `incomplete_due_to_budget` with `{depth_limit}`;<br>– `--max-visited-nodes 2` → `visited_node_limit`.<br>**Also:**<br>– `--max-examined-edges 1` → `examined_edge_limit`;<br>– `from pkg/domain/*` with `--max-sources-per-rule 1` → `source_limit`, `sources_evaluated 1 < total`;<br>– `--max-total-examined-edges 1` on the two-rule doc → the second rule has `sources_evaluated 0` and `total_examined_edge_limit`.<br>**Precedence:** budget + boundary → `incomplete_due_to_budget` with both reasons; a witness found before the stop → `violated` plus `depth_limit`. |
| 005 | test_architecture_invalid_rule_rejected | **Fixture files `invalid-*.json`:**<br>– relation `call` → `unsupported_relation` (rule 0);<br>– selectors `pkg/.*`, `pkg/**/infra`, `pkg/../secrets`, `/etc/passwd`, `pkg/*.py`, `pkg/*/x.py`, `pkg//x.py`, `pkg\x.py` → `malformed_selector`;<br>– a duplicate id → `duplicate_rule_id` (rule 1);<br>– an extra key or missing `to` → `schema`;<br>– format v2 → `unsupported_format`.<br>**In-test documents:**<br>– 65 rules, 33 selectors or > 256 KiB → `too_large`;<br>– BOM, duplicate key, NaN, non-UTF-8 or deep nesting → `invalid_json`.<br>**Each case:**<br>– `InvalidRule.as_dict()` has exactly the 4 keys, the expected reason and index;<br>– CLI exit 1, the exact stderr, empty stdout;<br>– no selector text in the message;<br>– rejected even when `--graph-root` does not exist (validation precedes the graph). |
| 006 | test_architecture_rule_replay_is_idempotent | On CHAIN and LINE:<br>– two library runs and one CLI run give identical bytes, and `report_id` recomputes;<br>– a rebuild into a fresh root at the same repo path gives the same generation and snapshot ids and identical bytes;<br>– two `python -m atlas_aci check-architecture` subprocesses with `PYTHONHASHSEED=1` and `=2` give identical bytes;<br>– changing a bound (`--max-counterexamples-per-rule 1`) changes `report_id`, and its witnesses are a prefix of the same order;<br>– a whitespace-only change of the rule bytes changes `rules_sha256`. |
| 007 | test_architecture_check_is_read_only | `inventory(repo, graph_root)` is unchanged across library and CLI runs (violated, budget-stopped and invalid-rule cases). Inside `open_snapshot`, `reader.db.execute("CREATE TABLE s31_probe(x)")` raises `sqlite3.OperationalError`. A fresh lease afterwards has the same generation and snapshot ids. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt path is `.spectra/state/aci-s31.execution.json`. |

Supporting tests (same file):
- **Selector grammar table:** valid and malformed selectors, and match sets over a fixed path list, including a directory literal matching nothing.
- **Witness oracle:** a brute-force all-shortest-paths search built from `CodeGraph.evidence_records()`. Every CHAIN witness equals the D6 backward-lexicographic minimum.
- **Cycles:** a↔b with the rule a→a gives length 2, `violated`; a cycle that never reaches the target terminates with `satisfied`.
- **Query plans (G-PLAN):** `EXPLAIN QUERY PLAN` for each D4 SQL statement.
- **Null snapshot:** an export/import generation gives `source_snapshot_unavailable` (library and CLI, exit 1).
- **CLI:** usage for a bad limit or unopenable graph (exit 2, fixed text); no tmp path in stderr; stdout equals the library `canonical_bytes`.
- **validate_report:** passes on all reports. It rejects a relabelled outcome and a tampered `report_id`.
- **Isolation:**
  - tools JSON sha and bytes are the base values; `READ_ONLY_TOOLS` has 7 entries;
  - 3436/3448/797/7730 unchanged;
  - `check-architecture` is in `cli.commands` and absent from the tools JSON.
- **AST deny-list** on `architecture/*.py`:
  - no imports of `time`, `datetime`, `random`, `uuid`, `os`, `subprocess`, `multiprocessing`, `threading`, `importlib`, `socket`, `urllib`, `http`, `atlas_aci.server`, `atlas_aci.tools`;
  - no `eval`, `exec`, `compile` or `__import__` calls;
  - no `IndexedReader(...)` call and no `navigate` reference.
- **Vocabulary:** no report key or enum token in {safe, verified, proven, guaranteed, compliant}.
- **Docs:** the docs name every outcome, reason, limitation and limit.

Gates beyond the ACs:
- **G-SRC.**
  - `git diff --exit-code 69e9a46 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage,extractors,providers,indexing,resolvers,reports} mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows`.
  - `git diff --name-only 69e9a46 -- mcp-server/src` ⊆ {`architecture/{__init__,rules,checker}.py`, `__main__.py`}.
- **G-MCP:** tools JSON sha `94f9567a…` / 1631472 bytes.
- **Regression set:** `test_s07_imports`, `test_s12_*`, `test_s19_*`, `test_s20_*`, `test_s22_*`, `test_s37_*` and `test_thesis_negatives` pass unchanged.

Commands (record exits and any skips; no required skips):
```sh
cd mcp-server
uv run pytest tests/slices/test_s31_architecture_rules.py -q
uv run pytest tests/slices/test_s12_*.py tests/slices/test_s19_*.py tests/slices/test_s20_*.py tests/slices/test_s22_*.py tests/slices/test_s37_*.py tests/test_s07_imports.py tests/test_thesis_negatives.py -q
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/
```
(Path note: if `test_s07_imports.py` lives under `tests/slices/`, use that path.)

## Confidence
To be computed with `ramza-score`. This is a read-only consumer of facts that already exist and are canonical. It makes no producer, protocol or query change, so generation ids, envelopes and the tools JSON cannot churn.

Main uncertainties, all covered by Day-1 checks with a stop rule:
- file-level resolution of `..` relative imports;
- non-indexed observation states;
- the spelling of dynamic-import callees;
- whether an imported generation has a null snapshot;
- query plans.

Semantic risk, an inherent provider limit and not a checker defect: real repositories mostly yield `unknown`.

## Rejected Alternatives
- **`navigate(op=path)` per entity pair.** It is entity-chained: hop 1 lands on a symbol with no outgoing import. Chains give a false `no_path`, which means a false satisfied. It also hides UNRESOLVED facts and has a mandatory wall-clock deadline.
- **S20 `impact.propagate` with `relations=["import"]` from targets in reverse.** It has the same entity-chaining defect, needs a change set, and is deadline-bound.
- **Synthetic file→file edges in the indexer, or resolving absolute imports.** They edit fingerprinted producers (codegraph, python_imports), churn generation and snapshot ids, and fall outside the declared paths. Absolute-import resolution is a follow-up provider slice.
- **IndexedReader for all reads.** Its deadline cannot be disarmed, so canonical output would depend on load (AC-006).
- **Multi-source BFS per rule.** It gives one witness per target rather than per (source, target) pair.
- **Treating absolute imports as external.** The facts cannot tell stdlib from internal absolute imports, so a satisfied result would be unsound.
- **fnmatch or regex selectors.** The issue excludes regex, and fnmatch `*` crosses `/`.
- **Vacuous satisfied for empty selections.** It hides typos. Empty selections are `unknown` with a named reason.
- **`--fail-on-violation` or MCP exposure.** Policy enforcement is excluded, and MCP exposure would break the tools-JSON and budget pins.

## Risks
- **Mostly `unknown` on real repos.** Absolute and submodule imports are boundaries. Docs and limitation flags say so plainly; the follow-up is an absolute-import resolution provider.
- **Silent dynamic imports.** The D9 probe narrows the gap, and the flags on every satisfied result disclose the rest. Aliased loaders still escape.
- **Conservative depth stop.** A chain exactly `max_depth` long reports `depth_limit`. This is documented, and the default of 16 avoids it in practice.
- **Report size at the maxima** (counterexamples × hops × proof ≈ 2.5 KB each). The defaults are modest and the worst case is documented.
- **Private coupling** to `_open`, `_lease` and `_record`. It is listed in the docstring and exercised by tests; a rename fails loudly.
- **Retained stale facts in failed files.** These are never traversed; they appear as `file_not_indexed`.
- **Timebox of 3 days.** Follow the cut order. At the limit, report completed checks and a narrow follow-up; no silent growth.
