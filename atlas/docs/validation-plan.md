# Evidence-backed validation plan

`atlas-aci validation-plan` turns a change (two indexed snapshots) plus any imported
per-test coverage into a **ranked, advisory list of tests to run**, with the exact
evidence behind every suggestion and an explicit gap for every changed span nothing
maps to. It is an operator CLI command and a library function
(`atlas_aci.query.validation.run`) only: there is no MCP operation, the tool list stays
the same seven read-only tools, and requests carry no paths.

> **A plan is a recommendation, never permission to skip CI.** Nothing is run, imported
> or collected: the planner reads indexed records and snapshot bytes and launches no
> process. An observed execution says a test's process ran a line of the **before**
> snapshot; it is not a pass, not an assertion and not validation of the **after**
> snapshot unless the evidence says so (`post_change_validation`).

## Workflow

```bash
# 1. Index the before snapshot (and, in the TEST environment, import its coverage).
atlas-aci index --repo R --graph-root B            # then: import-coverage ... (see coverage-import.md)

# 2. Change the tree and index the after snapshot into a second graph root.
atlas-aci index --repo R --graph-root A

# 3. Plan. Both roots must be built for the same repository path.
atlas-aci validation-plan --repo R --before-graph-root B --after-graph-root A

# Optional: extra coverage sources (repeatable), each built for the same repository.
atlas-aci validation-plan --repo R --before-graph-root B --after-graph-root A \
    --coverage-graph-root C1 --coverage-graph-root C2
```

stdout is the canonical JSON plan plus one newline (identical bytes for the library and
the CLI). Nothing is printed on rejection (exit 1) or invalid usage (exit 2). The
options are `impact`'s side flags (`--repo/--before-repo/--after-repo`,
`--graph-root/--before-graph-root/--after-graph-root`), its propagation options
(`--relations --confidence --max-seeds --max-depth --max-visited-nodes
--max-examined-edges --max-vm-operations --deadline-ms`), the operator diff limits
(`--max-changed-files --max-diff-entities --max-diff-occurrences`) and the plan limits
below. `--max-bytes` and `--max-boundary-probes` do not exist: a plan has no byte budget
and reports no impact boundaries (use `atlas-aci impact` for those).

## What it composes

The change set, seed selection and propagation are `atlas-aci impact`'s, computed **once**
on already-held leases (never through `impact.compute`, so nothing is parsed or traversed
twice). The planner adds three things.

1. **Spans.** One span per eligible change record (categories `removed`, `signature`,
   `unknown`, `body`, `added`; `format` and `location` changes are not spans), ordered by
   `(category weight, path, first line, change_record_id)`.
2. **Observed execution** (basis `observed_execution`): S21 `test_executes` records matched
   to a span by *byte-identical file snapshot*.
3. **Structural discovery** (basis `structural`): tests found only through static
   relationships.

## The matching rule (observed execution)

A coverage source is a leased graph: the before side, the after side, or an external
`--coverage-graph-root`. For each changed path and each source:

| Classification | Meaning |
| --- | --- |
| `matches_before` / `matches_after` / `matches_both` | the source's recorded file hash equals that side's file hash |
| `stale` | the source holds observation rows for the file, but the bytes match neither side |
| `not_observed` | the source holds no rows for the file |
| `unread` | rows exist but the observation row limit stopped the read |

Only matching sources feed evidence. `stale` rows are **counted** (`work.stale_observation_rows`)
and shown in `coverage_sources`, and are never hydrated into evidence. An observation
matches a span side when its source hash equals the span's file hash and the **byte span**
of the observed line overlaps the span (expanded to the physical line start, because a
declaration node starts after its indentation). Line numbers are for display only:
tree-sitter counts `\n`, coverage.py's physical lines also split on `\r`, so the planner
matches by bytes and derives `header_end_line` from the same bytes.

A matched line is `body` when it lies after the header line (the first `def`, `async def`
or `class` line in the span, which skips decorators) and `declaration` otherwise.

External sources must lease, be compatible with the after side, resolve to a distinct graph
namespace and a distinct generation from the sides and from each other, and stay within
`--max-coverage-sources`; any failure is a typed, path-free
`VALIDATION_SOURCE_REJECTED: <reason>` (exit 1) with a reason from `diff` rejections plus
`same_namespace`, `duplicate_source`, `source_limit`. Generation ids are content hashes, so
a second root built from the same bytes and the same imported run **is** the same generation
and is rejected as `duplicate_source`.

## Structural discovery

The test rule `atlas-pytest-default-conventions.v1` is a closed predicate over indexed
fields: language `python`, kind `function` or `method`, a name starting with `test`, a
basename `test_*.py` or `*_test.py`, and, when a class owner exists, the nearest class owner's
name starting with `Test`. Pytest configuration is never read.

* `reverse_path`: an impacted entity that passes the rule, with the hop proof (assertion,
  evidence, occurrence, producer, relation, resolution) from impact.
* `same_name_reference`: a same-spelled reference from a test file (the callee name matches
  the changed or impacted entity's name), found through bounded `refs` lookups. It
  over-approximates (a hot name such as `get` matches unrelated code), ranks below
  `reverse_path` and is counted against `max_reference_rows`.

`method` is `reverse_path` when any reverse-path evidence exists, otherwise
`same_name_reference`. Structural records never carry run or coverage fields,
`observed_in == {}` and `post_change_validation == false`. `also_observed` marks a
structural record whose selector hint equals an observed node id (its `[param]` suffix
stripped). Observed and structural identities are not merged.

## Record `atlas-validation-plan.v1`

Canonical JSON, repository-relative paths only, closed by
`atlas_aci.query.validation_schema.PLAN_SCHEMA`; the CLI runs `validate_plan` (schema plus
semantic rules, including the content-addressed `plan_id`) before printing.

* Header: `format`, `status = recommendations_only`, `execution = not_performed`,
  `ci_policy = advisory_only`, `ranker`, `test_rule`, `before` / `after`
  (`snapshot_id`, `generation_id`, `generation_kind`), `change_set_id`, `diff_work`.
* `impact`: `seeds_total`, `seeds_expanded`, `impacted_total`, `relations`, `confidence`
  and per-seed `frontiers` (`expansion`, `outcome`, `stop_reason`, `commitment`).
* `effective_limits`, `work` (counters only: `examined_edges`, `visited_nodes`,
  `observation_rows_read`, `stale_observation_rows`, `reference_probes`,
  `reference_rows_read`, `assertion_paths_loaded`, `source_files_read`).
* `coverage_sources`: each source (`before`, `after`, `external`) with `runs` and per-file
  `classification` / `observation_rows`.
* `spans`: identity, per-side `{snapshot_id, generation_id, source_sha256, line, end_line,
  header_end_line}`, `coverage` per side, `structural`, emitted `recommendations` ids and
  `state`.
* `recommendations`: `recommendation_id`, `action = run_test`, `basis`
  (`observed_execution` | `structural`), `method`, `test` (identity kind, entity id, path,
  name and `selector`: `pytest_nodeid` for observed, `pytest_selector_hint` for structural),
  `observed_in`, `post_change_validation`, `present_in_after`, `also_observed`, `rank`,
  `spans_total`, `evidence_total` and up to `max_evidence_per_recommendation` evidence entries.
* `gaps`: one per evaluated span in state `gap`: `action = inspect`, `basis = unsupported`,
  per-side `lines`, `coverage`, `structural` and closed `reasons`.
* `completeness`, `selection` (truncation flags), `limitations`, `plan_id`.

Observed evidence names the matched side and its snapshot/generation, the coverage source
generation and run, and the exact assertion / evidence / occurrence / producer ids (they resolve
with `ev.get` in the source's graph), the lowest body line (else lowest line) as the representative
`line`, `phases`, `scope`, up to `max_matched_lines` matched lines and `test_file_after`
(`same_bytes`, `changed_bytes`, `absent`).

### Span and gap states

Per span side, the first applicable of: `not_applicable`, `unsupported_language`,
`observation_limit`, `executed`, `declaration_only`, `no_observation_in_span`,
`no_observation_in_file`, `stale_only`, `no_coverage_source`. Span `state` is `observed_after`
(after side `executed`), else `observed_before_only`, else `structural_only` (a structural
recommendation maps), else `gap`. Gap reasons: `no_observation_in_span`,
`no_observation_in_file`, `no_coverage_source`, `observation_limit`, `unsupported_language`,
`stale_coverage_only`, `declaration_only_execution`, `no_structural_test`,
`structural_discovery_incomplete`. Gaps are computed from the full evaluated collection, never
from the emitted prefix.

## Ranking `atlas-validation-rank.v1`

Ascending tuple `(basis_order, -spans, -body_lines, category_weight, distance, selector.value,
recommendation_id)`. `basis_order`: 0 observed with an after-snapshot body match, 1 observed
with a before-snapshot body match only, 2 observed declaration-only, 3 structural with a
reverse path, 4 structural same-name only. `recommendation_id` is
`sha256("atlas-validation-recommendation.v1:" + canonical(identity key))`; `plan_id` is
`sha256("atlas-validation-plan.v1:" + canonical(plan - plan_id))`.

## Limits

| Limit | Default | Range |
| --- | --- | --- |
| `max_spans` | 256 | 1 - 2048 |
| `max_recommendations` | 50 | 1 - 500 |
| `max_gaps` | 256 | 1 - 2048 |
| `max_evidence_per_recommendation` | 8 | 1 - 64 |
| `max_matched_lines` | 32 | 1 - 256 |
| `max_observation_rows` | 100000 | 0 - 1000000 |
| `max_reference_probes` | 64 | 0 - 512 |
| `max_reference_rows` | 2000 | 0 - 20000 |
| `max_coverage_sources` | 4 | 0 - 8 |

Impact propagation limits keep impact's ranges; diff limits are operator-only. Each list is a
ranked prefix with its total and a `selection` flag. `completeness` is `incomplete` (with closed
reasons `span_limit`, `seed_limit`, `impact_work_limit`, `partial_extraction`,
`observation_limit`, `reference_probe_limit`, `reference_row_limit`) whenever anything was cut,
partially extracted or stopped on a limit.

Every row loaded after propagation (assertion rows, and the evidence, occurrence, test entity
and producer records they cite) is charged to `max_observation_rows`; every reference row and
test-file assertion row to `max_reference_rows`. Those phases use counters only, never a wall
clock. Only the propagation allowance (`--deadline-ms` and friends) is time-bounded.

Cost compared with `atlas-aci impact`: the same single change set and propagation (boundary
probes are skipped), plus one lease acquisition per external source (at most 8, the dominant
fixed cost), O(changed paths x sources) indexed queries, at most `max_reference_probes` indexed
`refs` lookups and at most one assertions load per distinct test path. The output is not
paginated: at the maxima (2048 spans, 500 recommendations x 64 evidence entries, 2048 gaps) it can
reach tens of megabytes; the defaults stay in the tens of kilobytes.

## Determinism

Every collection is sorted by explicit keys. Counters are pure counts; the plan contains no
durations, absolute paths or host data (`report_timestamp` is copied from the imported run).
Ids are content-addressed, so rebuilding and re-importing into fresh roots at the same repository
path reproduces the plan byte for byte, and external roots in any flag order give identical
bytes. The one caveat is impact's propagation deadline: replay is identical only while the
frontier `commitment`s match (they are in the plan).

## Errors and exit codes

| Condition | Exit | Output |
| --- | --- | --- |
| success | 0 | the plan |
| `SNAPSHOT_DIFF_REJECTED`, `IMPACT_DIFF_TOO_LARGE`, `VALIDATION_SOURCE_REJECTED` | 1 | stderr `Validation plan rejected: CODE[: reason]`, empty stdout |
| usage, invalid limit | 2 | empty stdout |

Messages never carry `str(exc)` or a path.

## Limitations (all in `limitations` of every plan)

`recommendations_are_advisory_and_do_not_change_ci`, `tests_are_not_executed_by_planning`,
`observed_execution_is_not_a_pass_or_assertion`,
`before_snapshot_observation_is_not_post_change_validation`,
`absence_of_observation_is_not_evidence_of_non_execution`,
`coverage_matches_only_byte_identical_file_snapshots`,
`declaration_lines_are_weighted_below_body_lines`,
`structural_tests_follow_pytest_default_conventions_only`,
`same_name_references_over_approximate`, `absolute_test_imports_do_not_yield_reverse_paths`,
`observed_and_structural_identities_are_not_merged`, `format_and_location_changes_are_not_spans`,
`impact_boundaries_not_reported_use_atlas_aci_impact`, `results_are_not_paginated`,
`not_exposed_over_mcp`.

Additional operational notes: an impacted test is mapped only to the best-ranked seed that reaches
it (impact keeps one path per entity); the header heuristic ignores multi-line decorators and
nested definitions and only affects ranking and `declaration_only`; an external root built by a
different producer is rejected (`producer_mismatch`: rebuild it with this Atlas).

## Coupling

The planner reaches into `atlas_aci.query.diff` private helpers only through `impact` and these
named symbols: `diff._open`, `_lease` and `_check_compatible`, and the lease attributes `db`,
`atlas_dir`, `snapshot_id`, `generation_id`, `read_source`. `impact._probe_relations` is
reimplemented locally. `test_s22_semantics` (impact parity) fails loudly, naming them, if impact
or diff drift.

## Evaluation

`python atlas/evals/validation_plan_comparison.py` compares run-everything, structural-only and the
plan on one edit of the coverage fixture against run-`a` labels, at function granularity. It makes no
superiority claim: the plan's observed recall reads the same run as the truth, and the structural
baseline is perfect only because this fixture's tests call the edited functions by name.
