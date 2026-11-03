---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-03T21:00:00Z
slice_id: ACI-S20
issue: https://github.com/Rynaro/atlas-aci/issues/41
---
# ACI-S20 — explain potential change impact through typed reverse paths

## Scope

Implement issue #41 on merged base `59a4819b444c129832d75943bff2a5020534ee80` (main after PR
#83 / ACI-S19), branch `aci-s20`. Live marker `<!-- atlas-aci:slice:ACI-S20 -->` verified;
no prior S20 plan, receipt or execution state. Criteria bytes hash to
`4d606e970b80994f70e7dbcba65b9337e5997927e3454fe561e65edd1ec74717` = contract table (EARS
lint ok, 9 criteria). Predecessors verified on the execution base: S12 (#34, bounded typed
traversal `query/traversal.navigate`, canonical assertion proofs) and S19 (#40, snapshot
diff `query/diff.py`, `atlas-symbol-diff.v1`) are ancestors of HEAD with COMPLETE receipts.

Observed facts (re-verified at planning):
- `navigate(reader, seed=, direction="incoming", relations=, confidence=, accounting=,
  admit=None, **limits)` runs BFS over `IndexedReader.edge`; incoming steps use
  `idx_assertions_target_relation_id`; only EXTRACTED/INFERRED assertions with a
  `target_entity_id` are traversable (traversal.py:254-258); edges are appended in discovery
  order; the first edge reaching a fresh node is that node's BFS parent. A HOP is a canonical
  assertion/evidence/occurrence/producer proof and carries no snapshot id.
- `IndexedReader(accounting=)` accepts a shared work object exposing `vm_quantum`,
  `progress`, `check`, `before_sql`, `reserve_edge`, `refund_edge` (S18 `ContextWork`
  precedent, per-seed navigate under one allowance, context.py:188-203).
- AMBIGUOUS/UNRESOLVED assertions have `target_entity_id = null`; no index on
  `raw_target`/`candidate_ids`. Raw references live in `refs` (indexed `callee_name`,
  `unresolved_reason`) and resolved/ambiguous ones in `edges` (indexed `callee_name`,
  `confidence`). `CodeGraph.unresolved_ref_count(name, relations)` (codegraph.py:5134) is the
  existing semantics (None when reference coverage is unknown or the generation is an
  import). Rust references and `jsx` references are always unresolved.
- A `reader_lease()` reader is a CodeGraph object. S19 `compare()` = `_open` + `_lease` on
  both sides → `_check_compatible` → `_Loaded.load()` (unmetered full entity/definition scan)
  → `_build` (re-parses only hash-differing files). Entity ids are position-free, so a
  removed entity id resolves on the before lease.
- graph_query ops are a closed set in five hard-coded places (graph.py:676, :681; server.py
  :334-357, :612, :659; results.py:88, :126-139); S18 added `context` by appending schemas via
  `_register_context_schemas()`. MINIMUM_BUDGET 3,436 / COLLECTION_MINIMUM_BUDGET 3,448 are
  derived from `ev.RELATIONS`, `LIMITS` and the neighborhood/path shape only;
  FIXED_ERROR_ENVELOPE_CAP = max over op names ("impact" shorter than "neighborhood").
- Enforcement has no path rule for graph_query arguments (enforcement.py:90): the before side
  is therefore operator-only configuration, never a request argument.

In:
1. Library `query/impact.py` (+ `query/impact_work.py`, `query/impact_admission.py`; all under
   the issue glob `query/impact*`): change set over two held leases, seed selection, reverse
   propagation per seed under one shared allowance, evidence-path reconstruction, boundary
   probes, ranking, whole-atom admission, content-addressed result. CLI and MCP call the same
   function.
2. `graph_query op=impact` (closed `atlas.graph.v1` addition), schemas and validator in
   `protocol/impact.py`, registered additively. Request carries NO paths — only
   `expected_generation_id`, `expected_before_generation_id`, `relations`, `confidence`,
   `max_bytes`, integer limits.
3. Operator configuration: `serve --before-graph-root DIR [--before-repo DIR]` (before repo
   defaults to `--repo`; `--before-repo` alone also accepted = that repository's default
   namespace, S19 worktree workflow); same-namespace before/after rejected at startup;
   optional operator diff limits in `Config`.
4. Operator CLI `atlas-aci impact` (per-side repo/graph-root options exactly as S19 `diff`, plus
   propagation options), canonical JSON on stdout.
5. Typed errors `IMPACT_UNCONFIGURED`, `SNAPSHOT_DIFF_REJECTED` (closed reasons),
   `IMPACT_DIFF_TOO_LARGE`, `SNAPSHOT_MISMATCH`, `INVALID_BUDGET`, `INVALID_INPUT`.
6. Labeled fixture comparing the changed-file baseline with the reverse-path baseline (approach
   step 4).
7. Docs `atlas/docs/change-impact.md`, README/INTEGRATION pointers, CHANGELOG.

Out: automatic test skipping; runtime observation (S27); pagination/continuation of impact
results (D9); client-supplied snapshot paths; edits to fingerprinted producer modules
(`codegraph.py`, `storage/*`, `extractors/*`, `providers/*`, `indexing/*`, `resolvers/*`);
edits to `query/diff.py` / `query/diff_syntax.py`; edits to `query/traversal.py` /
`query/indexed.py`; changes to `ev.RELATIONS`, `LIMITS`, the 3,436/3,448 minimum envelopes or
FIXED_ERROR_ENVELOPE_CAP; schema changes to existing ops (additive registration only);
persistent impact cache; any write under either graph root or repository.

Declared implementation paths (issue): `mcp-server/src/atlas_aci/query/impact*`,
`mcp-server/src/atlas_aci/tools/graph_query.py`, `mcp-server/tests/slices/test_s20_*.py`.

Scope amendment (`.spectra/changes/aci-s20/scope-amendment.json`, recorded before
implementation): `protocol/graph.py` (op set, integer-limit keys,
`_register_impact_schemas()`), `protocol/impact.py` (new; closed schemas + `validate_impact`,
S18 `protocol/context.py` precedent), `protocol/results.py` (FIXED cap loop, validation
chain), `server.py` (operation set, impact minimum budget, result-validation branch),
`config.py` (`before_repo`, `before_graph_root`, diff limits), `__main__.py` (`serve` options,
`impact` command), `mcp-server/tests/fixtures/s20/*` (only if inline sources become
unreadable), `atlas/docs/change-impact.md`, `README.md`, `INTEGRATION.md`, `CHANGELOG.md`, and
Ramza/receipt/backlog files.

Rightsize FULL 6 (14 files est., public MCP + CLI surface, security-relevant operator-only path
configuration, novel propagation model, med stakes).

## Approach

### Decisions (operator decision adopted; open points decided here)

D1 Surface (operator decision). MCP `graph_query op=impact` + operator-set before side, plus
CLI. The after side is always the served generation (`code_graph.reader_lease()` acquired by
the existing dispatch block, server.py:486-521). The before side is opened per call from
`Config.before_repo/before_graph_root` with S19 `diff._open`/`diff._lease` in an `ExitStack`
(identical rejection mapping to S19; both leases closed on every path).
`tools/graph_query.py` gets an early `impact` branch (after the epoch check, inside the
if/elif chain so telemetry still records). No before side configured → `IMPACT_UNCONFIGURED`
(retry_hint `different_tool`) before anything is opened. Error messages are fixed strings plus
closed reason codes; no exception text or absolute path ever reaches a result.

D2 Change set over held leases. `impact.py` composes S19 internals on the two leases it already
holds — `_check_compatible(before_reader, after_reader)`, `_Loaded(...).load()` per side,
`_build(old, new, compatibility, FileAnalysis)` — avoiding a second acquisition and mixed
generations, and leaving `diff.py` byte-unchanged. G-DIFF asserts the result equals
`diff.compare(Side…, Side…)` (same `id`) for every fixture pair. Same namespace (`atlas_dir`
equal) → `SNAPSHOT_DIFF_REJECTED: same_namespace` (also rejected at `serve` startup).
`DiffRejected(reason)` → `SNAPSHOT_DIFF_REJECTED`, message `Snapshot diff rejected: <reason>`,
reasons = closed S19 `REJECTIONS` + `same_namespace`.

D3 Seeds. Eligible change categories and traversal side:

| category | seed? | side | rationale |
| --- | --- | --- | --- |
| removed | yes | before | callers exist only in the before graph; after has no target |
| signature | yes | after | contract changed; callers in current code |
| unknown (both sides) | yes | after | cannot prove no change → conservative |
| unknown (one side only: extraction_failed_*) | yes | the side holding the ref | only that side has facts; also coverage reason `partial_extraction` |
| body | yes | after | behavior changed, contract equal |
| added | yes | after | can shift name resolution (shadowing, previously-unresolved refs) |
| format | no | — | S19: normalized tree equal; limitation: behavior-bearing comments masked (inherited S19 limitation, listed) |
| location | no | — | identical declaration text, only moved |

Rename/move candidates are not separate seeds (their removed/added records already seed).
Seed order/category weight: removed 0, signature 1, unknown 2, body 3, added 4; order =
(weight, side after=0/before=1, entity_id). Effective `max_seeds` = min(request,
`config.max_bound_field_elements`); eligible seeds beyond it are listed by count only and add
coverage reason `seed_limit`.

D4 Work and diff cost. Two counter families, both reported and both validated against limits:
- Propagation work (client-tunable within server upper bounds): one `ImpactWork` (mirrors the
  ContextWork interface: VM quantum = `indexed.VM_OP_QUANTUM`, absolute post-acquisition
  deadline, stop reasons `impact_*`) owning totals: `examined_edges`, `visited_nodes` (summed
  over seeds; each navigate gets the remaining allowance, min 1, never a reset),
  `sql_queries`, `vm_operations_reserved`, `vm_progress_callbacks`, `boundary_probes`.
  Per-seed `navigate(..., max_examined_edges=work.remaining("examined_edges"),
  max_visited_nodes=remaining_visited, max_depth=, deadline_ms=work.remaining_ms(),
  accounting=work, admit=None)`.
- Diff admission (operator-only Config: `impact_max_diff_entities` default 200000,
  `impact_max_changed_files` default 1000). Before `_Loaded.load()` the library runs one
  `SELECT COUNT(*) FROM entities` per side and compares the two manifests'
  `source_observations` (paths present on one side or with different
  `observed_source_sha256`); either count above its limit → `IMPACT_DIFF_TOO_LARGE` (closed
  reason `diff_entity_limit` | `changed_file_limit`) before any entity load or parse.
  Reported deterministic `diff_work`: `before_entities`, `after_entities`, `changed_files`,
  `parsed_files` (distinct analyses per side).
- "Work counters within configured limits" (AC-005) covers both families. Lease acquisition
  and generation validation stay outside counters, separately observed (S18 precedent).
- No server-side change-set cache (lower risk; bounded diff admission suffices; the rate
  limiter bounds frequency).

Limits (`IMPACT_LIMITS`, (default, low, high)): `max_seeds` (32,1,64), `max_depth` (3,1,32),
`max_visited_nodes` (200,1,1000), `max_examined_edges` (2000,0,10000), `max_boundary_probes`
(256,0,2048), `max_vm_operations` (1000000,0,10000000), `deadline_ms` (2000,0,2000);
`max_bytes` ≤ `config.max_response_bytes`. All keys join graph.py:681 (bool/float rejected).

D5 Paths. Reconstruct each seed's BFS tree from navigate's `edges` list: for each hop in order,
`child = assertion.source_entity_id` (incoming), `parent = assertion.target_entity_id`; the
first hop introducing a child is its parent (navigate's own parent rule with `admit=None`).
Each impacted entity's path goes from the changed entity to the impacted entity: `nodes` =
[changed … impacted] (IDENTITY projections from that side's lease), `hops` = HOP proofs with
`traversal_direction: "incoming"`, `distance = len(hops)` (1..max_depth). Every impacted item
is tagged `snapshot_side`, `snapshot_id`, `generation_id`, `changed_entity_id`,
`change_category`, `change_record_id`.

D6 Boundaries / incomplete coverage. Propagation "reaches an unresolved reference" when an
entity on the propagation (each seed on its side, and each impacted entity on its path side,
in rank order; probes deduplicated by (side, name)) has references the reverse index cannot
follow. Name-based probe, inside an `IndexedReader` context on that lease so VM
progress/deadline/SQL counts are charged to `ImpactWork`:
`unresolved_reference_count = reader.unresolved_ref_count(name, relations')` with
`relations'` = selected relations ∪ {"jsx"} (∪ {"call"} if `construct` selected) — existing
semantics; None → `reference_coverage: "unknown"`; `ambiguous_reference_count` = fixed SQL
`SELECT COUNT(*) FROM edges WHERE callee_name=? AND confidence='AMBIGUOUS' AND relation IN
(…)` (`idx_edges_callee`). Boundary record emitted when any count > 0 or coverage unknown:
`entity_id`, `name`, `language`, `snapshot_side`, counts, `reference_coverage`,
`basis: "same_name_reference"`, `reasons` map. Documented imprecision: over-approximates by
spelled name (any language); never under-reports relative to the refs table.
`coverage.state = "complete"` iff the reason set is empty. Closed reasons:
`unresolved_reference`, `ambiguous_reference`, `reference_coverage_unknown`, `work_limit` (any
frontier stopped: deadline/depth/visited/edges/VM), `seed_limit`, `boundary_probe_limit` (probe
allowance or work exhausted before all entities probed), `partial_extraction` (change-set
`partial_paths` non-empty). Coverage is computed from the full captured collection, never from
the emitted prefix.

D7 Result shape and ranking. Envelope `operation: "impact"`; `generation_id`/`snapshot_id` =
after lease; `evidence_status` = worst of the two leases (available > partial > unknown >
unavailable); `bounds.truncated` = presentation truncation or coverage `work_limit`.
`data` (`IMPACT_DATA`, closed, all `obj()`): `projection: "change_impact"`,
`status: const "potential_impact"`, `basis: const "static_reachability"`,
`runtime_observation: const "not_performed"`, `before`/`after` {snapshot_id, generation_id},
`change_set_id`, `change_summary` (S19 summary counts), `propagation` {direction const
"incoming", relations map, confidence map, seed_categories map, ranker const
"atlas-impact-rank.v1"}, `effective_limits` (incl. diff limits), `work`, `diff_work`,
`seeds[]` {changed_entity_id, change_record_id, category, snapshot_side, snapshot_id,
generation_id, name, kind, language, path, expansion enum[expanded, not_expanded], frontier
{outcome, stop_reason, commitment, queued_nodes, depth}}, `seeds_total`, `impacted[]`
{status const "potentially_affected", basis const "static_reachability", entity IDENTITY,
also_changed bool, snapshot_side, snapshot_id, generation_id, changed_entity_id,
change_category, change_record_id, distance, reached_from_seeds, rank {distance,
category_weight, side_order}, path {nodes, hops}}, `impacted_total`, `impacted_returned`,
`coverage` {state, reasons map, boundaries[], boundaries_total}, `selection` {truncated,
reason enum[byte_limit, element_limit] | null}, `limitations` map (static), `impact_id`. Maps
(`{value: true}`) instead of lists for small sets keep the element cap honest (S18
`set_membership_maps`).
An entity reached from several seeds/both sides keeps its single best explanation by the rank
key: (distance asc, category_weight asc, side_order asc, changed_entity_id, entity_id).
`reached_from_seeds` counts distinct seeds within captured frontiers. Seeds themselves appear
in `impacted` only when reached at distance ≥ 1 from another seed (`also_changed: true`).
`impact_id = sha256("atlas-impact.v1:" + canonical(data without impact_id))`. No timestamps,
durations, absolute paths or host data → expected-generation guards are plain equality:
`expected_generation_id` checked against the after lease by the existing dispatch;
`expected_before_generation_id` checked by impact after the before lease is acquired; mismatch
on either → `SNAPSHOT_MISMATCH`.

D8 Admission and minimum budget. Collection completes before presentation (counters final when
admitted). `seeds` are metadata and always emitted. `impacted` then `boundaries` are whole
atoms admitted as ONE strict prefix of the concatenated ranked sequence: the first atom that
does not fit (bytes measured on the conservative dual text+structured SDK framing,
`Admission.size` style, or element cap) stops all later admission, no reordering. Prefix length
by binary search (size is monotone). `IMPACT_MINIMUM_BUDGET` is measured at import from a
worst-case metadata envelope (zero seeds/atoms, maximal limits, all reason/limitation keys,
999999999 counters, 64-hex ids), pinned by a test and documented; server.py:659 passes it for
`operation == "impact"`, so a budget below it gets the fixed `INVALID_BUDGET` within
FIXED_ERROR_ENVELOPE_CAP. If the real metadata including seeds does not fit, the tool raises
`INVALID_BUDGET` (context precedent).
Determinism (AC-006): the selected prefix is a pure function of the captured collection and
`max_bytes`; identical request + identical leases + identical frontier ⇒ identical bytes.
Deadline-stopped runs are replay-identical only when their frontier commitments match (issue
step 7).

D9 No pagination. S13 paging is closed over NAV_DATA atoms (graph.py:699-704; `validate_page`
atom kinds node/edge/path, typed resource membership); paging impact needs a new atom kind,
captured-schema variant and typed resource proofs for two snapshots — beyond a 3-day box.
Results already state `impacted_total`, `boundaries_total`, `seeds_total` and `selection`;
clients raise `max_bytes` or narrow relations/depth. Follow-up candidate only.

D10 One library function. `impact.compute(before_reader, after_reader, request, *,
element_limit, max_bytes, diff_limits) -> envelope` (compatibility, admission, seeds,
propagation, boundaries, ranking, presentation, id). `impact.normalize(arguments)` validates
via `INPUT_VALIDATOR` + limits. MCP: `impact.serve(after_reader, arguments, config)` opens the
before side and calls `compute`. CLI: `impact.run(before: Side, after: Side, arguments,
settings)` opens both and calls `compute`, then `validate_impact`, prints
`ev.canonical(envelope)` + "\n" to `stdout.buffer`; typed errors → exit 1 with
`Impact rejected: <CODE>[: <reason>]` on stderr and empty stdout; usage errors → exit 2. CLI
defaults equal MCP defaults (element limit `DEFAULT_MAX_BOUND_FIELD_ELEMENTS`, `max_bytes` =
`Config.max_response_bytes`), so CLI and MCP produce the same envelope.

D11 Relations (approach step 1). Propagation relation enum = `sorted(ev.RELATIONS -
{"rationale_for"})` (derived; `ev.RELATIONS` unchanged). Default: `call`, `construct`,
`superclass`, `include`, `extend`, `prepend`; `import` opt-in (module-level coupling, noisy);
`rationale_for` excluded (not an edge relation); `jsx`/`reference` not traversable (jsx counts
only toward boundaries). Confidence default {EXTRACTED, INFERRED}; client may narrow.
Direction fixed incoming (not a request field).

D12 Protocol registration. `_register_impact_schemas()` runs after
`_register_context_schemas()` and appends, in order: `IMPACT_INPUT` to the input oneOf
(required `schema_version`, `op`; `additionalProperties: false`), `"impact"` to the operation
enum, `IMPACT_DATA` to `data.anyOf`, one envelope oneOf member (`operation const impact`,
`status ok`, data `IMPACT_DATA`); then rebuilds both validators. Because everything is
appended, the first '"relation"' in `json.dumps(GRAPH_OUTPUT_SCHEMA)` stays in NAV HOP
(test_s14_capabilities.py:231). server.py:334 and results.py:126-139 gain an `impact` →
`validate_impact` branch; server.py:612 and results.py:88 gain "impact". `validate_impact`
(structured validator) re-checks every hop with `graph.validate_hop` against `{relations,
confidence, direction: incoming}`, path continuity/endpoints, side ids vs `before`/`after`,
`changed_entity_id` ∈ seeds with matching side, strict rank order, counts and limits,
coverage/reasons consistency (boundaries_total > 0 ⇒ reason present; `complete` ⇔ empty),
element caps and `impact_id`.

### Work plan (timebox 3 engineering days)

1. Day 1 morning — freeze inputs: labeled fixture sources v1/v2, labels and changed-file
   baseline expectation as constants with sha256 pin, captured under
   `.spectra/evidence/aci-s20/labels.json` BEFORE any propagation code; verify on a real build
   with the existing neighborhood op/SQL: (a) cross-file Python `from geometry import area`
   call → EXTRACTED/INFERRED assertion; (b) unknown-receiver `r.size()` → `refs` row
   `callee_name="size"` with `unresolved_reason`; (c) an AMBIGUOUS `edges` row for duplicated
   names; (d) removed-entity incoming navigate on the before lease. Record base invariants
   (MINIMUM_BUDGET, COLLECTION_MINIMUM_BUDGET, FIXED_ERROR_ENVELOPE_CAP, sha256 of canonical
   dumps of every pre-existing schema component, `atlas-aci tools` JSON) in
   `.spectra/evidence/aci-s20/base-invariants.json`. A failing (a)/(b) stops and is reported;
   never faked.
2. Day 1 afternoon — `query/impact_work.py`, `query/impact.py` (compatibility, diff admission,
   seeds, propagation, paths, boundaries, ranking, id).
3. Day 2 — `protocol/impact.py`, registration, results/server wiring, `tools/graph_query.py`
   branch, config + `serve` options, `query/impact_admission.py` + minimum budget, CLI,
   contract tests.
4. Day 3 — supporting tests, labeled baseline evidence, docs, gates in the lock-faithful
   Docker dev image (ruff check/format, mypy, full pytest unprivileged; rebuild the image from
   the branch first — test_s11/s12 docker runtime-hash lists include server.py,
   graph_query.py, protocol/graph.py, results.py), then RUN-01/02.

Gates beyond the ACs:
- G-PRODUCER: `git diff --exit-code 59a4819 -- mcp-server/src/atlas_aci/{codegraph.py,
  storage/,extractors/,providers/,indexing/,resolvers/}`; existing pinned generation-id and
  fixture tests pass unchanged.
- G-ENGINE: `git diff --exit-code 59a4819 -- mcp-server/src/atlas_aci/query/{traversal.py,
  indexed.py,diff.py,diff_syntax.py,admission.py,paging.py}`.
- G-ENVELOPE: MINIMUM_BUDGET == 3436, COLLECTION_MINIMUM_BUDGET == 3448,
  FIXED_ERROR_ENVELOPE_CAP == recorded base value, `ev.RELATIONS` equal to the literal
  8-element set.
- G-SCHEMA-ADDITIVE: every pre-existing input oneOf member, data.anyOf member, envelope oneOf
  member, the GRAPH_OUTPUT_SCHEMA legacy branch, and each other tool's schema byte-identical
  (canonical sha256) to base; the only deltas are the four appends in D12;
  test_s14_capabilities passes unchanged.
- G-MCP: exactly seven tools; `READ_ONLY_TOOLS` unchanged; `atlas-aci tools` diff vs base is
  exactly the graph_query schema appends.
- G-RO: SHA-256 inventory of served repo, before repo, both graph roots and memex identical
  before/after every impact call (MCP and CLI), including every rejection path.
- G-DIFF: impact's internal change set `id` equals `diff.compare()` for every fixture pair.
- G-NOPATH: impact request with any extra key (e.g. `before_graph_root`, `repo`) →
  `INVALID_INPUT` before any lease acquisition; no absolute path in any impact output or
  error.

### Independent checker conditions

Independent ATLAS review: APPROVE_WITH_CONDITIONS (recorded in
`.spectra/plans/aci-s20.review.json`). Every condition below is binding on implementation
and overrides conflicting text above and below (including the fixture description in
Acceptance Criteria).

1. (BLOCKER) Fixture uses a package with relative imports. Absolute imports are
   `unsupported_absolute_import` (python_imports.py:61-62) and top-level relative imports
   are `relative_import_no_package` (:87-90).
   - Files: `pkg/__init__.py`, `pkg/geometry.py`, `pkg/shapes.py`
     (`from .geometry import area`, top-level, unconditional, unaliased) and `pkg/cli.py`
     (`from .geometry import legacy`).
   - Tests must not assert the unresolved reason string for `r.size()`. The actual reason
     is `shadowed_noncallable` (codegraph.py:4472-4499); the refs row still has
     `callee_name="size"`.
   - The ambiguous-boundary fixture uses a non-scoped language (e.g. Ruby), because Python
     and JS rarely yield AMBIGUOUS edges (`_SCOPED_LANGS`, codegraph.py:323).
2. Visited cap. `navigate` is called only when `remaining_visited >= 1`; otherwise the seed
   is `not_expanded`.
   - Pass `max_visited_nodes=remaining`; `visited_nodes` = Σ `len(visited)`.
   - Add a test with more seeds than the cap.
   - Catch `QueryStopped` raised by `work.remaining_ms()` / `check()` outside `navigate`,
     and stop expanding once `work.stop_reason` is set (context.py:242-244 precedent).
3. Metered probes. Each boundary statement:
   - charges `work.before_sql("boundary")`;
   - on `sqlite3.OperationalError` with SQLITE_INTERRUPT while `shared_stop_reason` or
     `deadline_triggered` is set, records `boundary_probe_limit` + `work_limit` and never a
     partial count;
   - re-raises any other sqlite error.

   Relations are passed as a sorted tuple.
4. No completeness overclaim. Delete "never under-reports".
   - Always emit the limitation `aliased_reference_names_not_probed`: aliased imports
     produce refs under the local alias (python_imports.py:48-52; JS `import {x as y}`).
   - Document `coverage.state=complete` as meaning "no same-name unresolved references",
     not proof of completeness.
5. Diff-phase bound.
   - Add an operator occurrences-count gate with closed reason `diff_occurrence_limit`.
   - Defaults come from Day-1 measurement, starting at 50,000 entities / 200 changed files.
   - `changed_files` is computed over exactly the set `_build` parses, including paths with
     no observation record (diff.py:496-501).
   - Document that parsing blocks the event loop.
   - Create `ImpactWork` (and its propagation deadline) AFTER the change set is built.
6. Seed side is deterministic:
   - `removed` → before; `added` → after;
   - `unknown` with exactly one non-null ref → that ref's side;
   - `unknown` with both refs null → after if `entity_id` is in the after side's entities
     for that path, else before.

   Add tests for the extraction-failed and ambiguous-definition records.
7. Dispatch order.
   - The "before configured" check happens in dispatch before lease acquisition (as the
     `context` branch does at server.py:459), so `IMPACT_UNCONFIGURED` is deterministic.
   - `impact` never closes the served reader; the dispatch ExitStack owns it.
   - Impact receives the structured arguments through dispatch, as `context` does
     (server.py:511-516).
8. No path leakage.
   - Validate the before configuration at `serve` startup with a read-only `CodeGraph`
     (click error on failure).
   - At request time, map `DiffUsageError` and any `ValueError`, `OSError` or
     `sqlite3.Error` from the before side to a fixed typed error whose message never
     contains `str(exc)`.
   - Test with a before root that is missing or inside the checkout. Assert that `tmp_path`
     appears in neither the text nor the structured content.
9. Paths and async.
   - Exclude the seed as a child.
   - Assert `distance == frontier depth` and `distance <= max_depth`.
   - `compute` is `async def`; the CLI uses `asyncio.run`.
10. Determinism.
    - Never copy per-seed `nav["effective_limits"]`, which hold the wall-clock remaining
      deadline, into the result.
    - `work` records probes attempted, probes completed and the probe stop reason.
11. Prefix admission.
    - Size candidates with worst-case reserved selection and counter encodings
      (`admission.reserve` precedent, admission.py:57-67) so admission is monotone.
    - Assert the final encoding is ≤ budget.
    - Document and test that a request at exactly `IMPACT_MINIMUM_BUDGET` whose seeds do
      not fit gets the tool-raised `INVALID_BUDGET`.
12. AC-003 scan scope. The "no breakage/broken/confirmed" scan covers the `IMPACT_DATA`
    schema and the impact payload only (NAV `work` has `confirmed_edge_eof`). Navigate
    counters are not embedded verbatim.
13. AC-007 non-vacuous. Also assert:
    - the hop assertion id is in the before `assertions` table and absent from the after
      one;
    - the after-side `main→legacy` assertion is UNRESOLVED with `target_entity_id` null;
    - navigating `legacy` on the after lease gives `no_evidence`.
14. G-DIFF also covers the case where the after lease is the one dispatch acquired.
    Registration appends only; pre-existing schema members are pinned by hash.
15. Timebox cut order, used only if overrunning, and each cut listed as a limitation:
    1. baseline polish and doc depth;
    2. the `import` opt-in;
    3. the ambiguous-count probe (keep unresolved; list ambiguity as a limitation);
    4. cross-side best-explanation nuance and `also_changed`;
    5. CLI propagation options beyond the defaults.

    Never cut shared accounting (condition 2), `validate_impact`, G-DIFF, G-RO, G-NOPATH
    or the fixture fix.

## Stories

As an agent operator reviewing a change, I want each potentially affected caller listed with
a typed evidence path back to the changed symbol in an identified snapshot, so that I can
triage impact without treating reachability as proven breakage.

As a reviewer of graph evidence, I want unresolved or ambiguous references, work cut-offs and
seed limits reported as incomplete coverage and repeated runs to give byte-identical ranked
results, so that a short impact list is never mistaken for a complete one.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s20.criteria.md` (sha256 `4d606e97…ec74717`). All in
`mcp-server/tests/slices/test_s20_contract.py`; each side built by the real indexer
(`CodeGraph.build()`, `test_s19_support.same_path_pair`) into its own graph root at the same
repo path; impact run through the real MCP path (`call_tool_result` with
`Config(before_graph_root=…)`) and, where noted, the CLI. No mocks of the behavior under test;
every `all(...)` preceded by a non-empty assertion.

Shared fixture (Python), v1 → v2: `geometry.py`: `area(w, h)` → `area(w, h, scale=1)`
(signature); `helper()` body edit (body); `perimeter` unchanged; `legacy()` removed.
`shapes.py`: `class Rect` with `size(self)` calling `area` (cross-file, file unchanged).
`report.py`: `summary(r)` calling `r.size()` (unknown receiver → unresolved). `cli.py`:
`main()` calling `legacy()` (unchanged file; in v2 the call is unresolved). `util.py`:
`unrelated()` calling `perimeter` (negative control). Chain fixture for budget:
`c0 ← c1 ← … ← c30` plus two changed seeds with 10 callers each.

| AC | Test | Observable |
| --- | --- | --- |
| AC-S20-001 | test_reverse_caller_impact | `area` signature change → `impacted` contains `Rect.size` with `status == "potentially_affected"`, `changed_entity_id == area id`, `change_category == "signature"`, `snapshot_side == "after"`, `distance == 1`; same-file resolved caller also present; negative control `unrelated` absent; CLI stdout envelope == MCP structuredContent |
| AC-S20-002 | test_impact_reason_path | for every (non-empty) impacted item: `path.nodes[0].id == changed_entity_id` ∈ `seeds` on the same side; `nodes[-1].id == entity.id`; `len(hops) == distance`; each hop `traversal_direction == "incoming"`, `assertion.target_entity_id == nodes[i].id`, `source_entity_id == nodes[i+1].id`, `graph.validate_hop` passes; assertion/evidence/occurrence ids exist in that side's DB (lease opened on the tagged snapshot), `snapshot_id`/`generation_id` equal that lease's |
| AC-S20-003 | test_no_breakage_claim | `data.status == "potential_impact"`, `data.basis == "static_reachability"`, `runtime_observation == "not_performed"`, every item `basis == "static_reachability"`; the published schema rejects any other value for these consts (mutated payload → validator error); no key or enum value in the output/schema names breakage/broken/confirmed |
| AC-S20-004 | test_unresolved_boundary | `Rect.size` is impacted and `r.size()` is unresolved → `coverage.state == "incomplete"`, `"unresolved_reference"` in reasons, a boundary `{entity_id: Rect.size id, snapshot_side: "after", unresolved_reference_count >= 1, basis: "same_name_reference"}`; expansion still returned the other impacted items; negative control without unresolved refs/limits → `complete` with empty boundaries; under a `max_bytes` that drops the boundaries list, state/reasons stay `incomplete` |
| AC-S20-005 | test_impact_budget | chain with `max_visited_nodes=5, max_examined_edges=8, max_depth=32` → `work.visited_nodes <= 5`, `work.examined_edges <= 8`, `vm_operations_reserved <= max_vm_operations`, `boundary_probes <= max_boundary_probes`, frontier `stop_reason` ∈ {visited_node_limit, examined_edge_limit, impact_*_limit}, reason `work_limit`; sweep of limits 0..N stays within; two-seed fan-out with `max_examined_edges=12` totals ≤ 12 (no per-seed reset); `diff_work` within operator limits and `impact_max_changed_files=0` → `IMPACT_DIFF_TOO_LARGE` with no result |
| AC-S20-006 | test_impact_replay | same request twice in-process → identical `result_bytes`; once via CLI subprocess → equal envelope and `impact_id`; both sides rebuilt into fresh roots at the same repo path → equal snapshot/generation ids and identical canonical result; negative: relations subset or changed `max_depth` changes `impact_id`; generous deadline (frontier commitments asserted equal) |
| AC-S20-007 | test_removed_symbol_impact | `legacy` removed → seed `category == "removed"`, `snapshot_side == "before"`; `main` in `impacted` with `snapshot_side == "before"`, `snapshot_id == before lease snapshot_id != after snapshot_id`, `path.nodes[0].id == legacy id`; the hop assertion id exists in the before DB and the after DB has no resolved assertion targeting `legacy` |
| AC-S20-RUN-01 | gate | SHA-256 manifest of declared tracked implementation files equal before/after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths |
| AC-S20-RUN-02 | gate | `slice_id` + `criteria_sha256` equal in interrupted and resumed receipts; one authoritative receipt path `.spectra/state/aci-s20.execution.json` |

Supporting tests:
- `test_s20_mcp.py`: `IMPACT_UNCONFIGURED` without before config (after index valid);
  `SNAPSHOT_DIFF_REJECTED` for an unrelated before repo (`repository_identity_mismatch`) and a
  before root with no index (`index_unavailable`); startup same-namespace rejection;
  `SNAPSHOT_MISMATCH` for both generation guards; G-NOPATH; `max_bytes` below
  `IMPACT_MINIMUM_BUDGET` → fixed `INVALID_BUDGET` ≤ FIXED_ERROR_ENVELOPE_CAP and
  exactly-minimum succeeds; text content == structured; whole-atom prefix (no reordering,
  totals honest); element-limit stop; G-RO inventory; G-SCHEMA-ADDITIVE / G-ENVELOPE pins.
- `test_s20_cli.py`: option resolution (`--repo`, `--before-*`, `--after-*`, `--graph-root`),
  canonical stdout bytes, rejection exit 1 + empty stdout, usage exit 2.
- `test_s20_semantics.py`: seed table (format/location never seed; added seeds;
  extraction-failed one-sided unknown → `partial_extraction`), seed_limit, best-explanation
  choice across seeds/sides, `also_changed`, ambiguous boundary, `import` opt-in,
  `rationale_for` rejected, G-DIFF equality.
- `test_s20_baseline.py` (labeled comparison, issue approach step 4): computes B0
  changed-file baseline (every non-file entity in a path whose bytes differ, excluding seeds)
  and B1 reverse-path (impact `impacted`); against frozen labels reports per baseline
  |predicted|, TP/FP/FN, precision, recall, explained fraction (B0 = 0, B1 = 1.0 by AC-002),
  and FNs of B1 covered by a boundary. Asserts only fixture-constructed facts: B1 finds
  cross-file `Rect.size` and before-side `main`, which B0 misses; B0 includes unchanged
  `perimeter` (labeled unaffected), B1 excludes it; B1's FN `summary` is covered by the
  `Rect.size` boundary. Summary JSON → `.spectra/evidence/aci-s20/baseline-comparison.json`.
  No superiority threshold; report honestly.

Commands (record exits; no required skips):
```sh
cd mcp-server
uv run pytest tests/slices/test_s20_contract.py -q
uv run pytest tests/slices/test_s20_*.py tests/slices/test_s19_*.py tests/slices/test_s18_contract.py tests/slices/test_s12_*.py tests/slices/test_s11_protocol.py tests/slices/test_s14_capabilities.py tests/test_server.py -q
uv run pytest -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```

## Confidence

Computed with `ramza-score` (recorded in state gates). Traversal, accounting, proof
validation, registration and diff are established, tested predecessors composed without
editing them; no producer module changes, so generation ids and envelopes cannot churn. Main
uncertainty: whether `refs`/`edges` rows hold the unresolved/ambiguous reference facts needed
for name-based boundaries on native epoch-9 builds (Day-1 check), whether Python cross-file
calls resolve in the fixture, and usefulness of a name-based boundary on real repositories
(over-approximation).

## Rejected Alternatives

- New multi-source BFS engine in `impact.py`: shortest global distances and less overlap work,
  but duplicates subtle traversal accounting (reservation/refund, interrupts, frontier
  commitment); reusing `navigate` per seed under one shared allowance follows S18.
- CLI-only offline impact (S19 style): simpler/lower risk but contradicts the operator decision
  and the issue's `op=impact`.
- Client-supplied before paths in the request: needs a new enforcement path rule; arbitrary
  repository/graph exposure; rejected by operator decision.
- Editing `diff.py` to expose `compare_leases`: cleaner API but changes S19 output digests;
  composing internals with a G-DIFF equality guard suffices.
- Server-process change-set cache: saves repeat parses but adds hit/miss state that must not
  leak into canonical bytes; bounded diff admission is enough within the box.
- Paging via S13 leases: new atom kinds and two-snapshot resource proofs; out of box.
- Native-assertion boundary scan (`raw_target`/`candidate_ids`): no index → O(all assertions)
  per probe.

## Risks

- `refs`/`edges` coverage on native generations differs from assumption → Day-1 check; if
  absent, boundaries fall back to `reference_coverage_unknown` on every probed entity (never
  `complete`), with a listed limitation; never fake counts.
- Name-based boundaries over-report (common names) → coverage often `incomplete`; truthful,
  documented, quantified in baseline evidence.
- navigate's depth stop fires whenever the queue head reaches `max_depth`, so default depth-3
  closures are often `work_limit` incomplete; truthful; documented.
- Private coupling to `diff._check_compatible/_Loaded/_build/_open/_lease` → G-DIFF test;
  documented.
- Per-call before-lease acquisition (full generation validation) adds latency; outside
  counters, separately observed (S18).
- Operator republishing the before root mid-session changes its generation;
  `expected_before_generation_id` guard + lease pinning keep each call coherent.
- Docker runtime-hash tests compare image vs host → rebuild the image from the branch before
  gates.
- Timebox 3 engineering days; at the limit report completed checks and gaps (e.g. ambiguous
  probe, CLI polish) with a narrow follow-up; no silent growth.
