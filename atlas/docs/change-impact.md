# Potential change impact through typed reverse paths

`graph_query op=impact` (MCP) and `atlas-aci impact` (operator CLI) answer one
question about two **explicit, already-built** snapshots of one repository: which
entities are *potentially affected* by what changed, and through which typed,
evidence-backed path. Both surfaces call **one library function**
(`atlas_aci.query.impact.compute`) and produce the same canonical envelope.

The result is **potential impact by static reachability**. It never claims
breakage, and it never observed the program running (`runtime_observation:
"not_performed"`). A short list is never a complete list: unresolved or ambiguous
references, work cut-offs and seed limits are reported as incomplete coverage.

There are still exactly seven MCP tools. `impact` is one more closed operation of
`graph_query` (`atlas.graph.v1`); the registration is additive and every earlier
schema member is byte-identical.

## Configuring the before snapshot (operator only)

The **after** side is always the generation `serve` already holds. The **before**
side is operator configuration; the request carries **no path of any kind**, so an
agent cannot point the server at another repository or graph:

```bash
atlas-aci index --repo /path/to/repo --graph-root /graphs/before
# ... edit the working tree ...
atlas-aci index --repo /path/to/repo --graph-root /graphs/after
atlas-aci serve --repo /path/to/repo --graph-root /graphs/after \
    --before-graph-root /graphs/before
```

`--before-repo` defaults to `--repo` (set it for a second worktree under one graph
root, as with `atlas-aci diff`; alone it selects that repository's default
namespace). The two sides must be different graph namespaces, checked at startup
(a click error) and again per request (`SNAPSHOT_DIFF_REJECTED: same_namespace`).
Without a before side, `op=impact` returns `IMPACT_UNCONFIGURED` before any lease
is taken. The before snapshot is opened per call (read-only, pinned lease) and
closed on every path; the served lease is owned and closed by the dispatcher.

## Request

```json
{"schema_version": "atlas.graph.v1", "op": "impact",
 "expected_generation_id": "<after generation>",
 "expected_before_generation_id": "<before generation>",
 "relations": ["call"], "confidence": ["EXTRACTED", "INFERRED"],
 "max_depth": 3, "max_bytes": 65536}
```

Only those keys exist (plus the integer limits below); anything else, including
`before_graph_root` or `repo`, is `INVALID_INPUT` before any lease is acquired.
A wrong expected generation on either side is `SNAPSHOT_MISMATCH`.

| limit | default | range | meaning |
| --- | --- | --- | --- |
| `max_seeds` | 32 | 1..64 | changed entities expanded (also capped by the operator element limit) |
| `max_depth` | 3 | 1..32 | reverse-path length |
| `max_visited_nodes` | 200 | 1..1000 | **total** across all seeds (never reset) |
| `max_examined_edges` | 2000 | 0..10000 | **total** across all seeds and probes |
| `max_boundary_probes` | 256 | 0..2048 | distinct (side, name) reference probes |
| `max_vm_operations` | 1,000,000 | 0..10,000,000 | SQLite VM quanta plus statements, shared |
| `deadline_ms` | 2000 | 0..2000 | propagation + probes, started after the change set exists |

Propagation relations default to `call, construct, superclass, include, extend,
prepend`. `import` is opt-in (module-level coupling is noisy); `rationale_for` is
not a dependency and is rejected. The direction is always incoming.

## What is explained

1. **Change set.** The S19 change set (`atlas-symbol-diff.v1`) is computed over the
   two held leases. `change_set_id` equals `atlas_aci.query.diff.compare()` for the
   same pair (pinned by a test).
2. **Seeds.** `removed` (before graph), `signature`, `body`, `added` (after graph)
   and `unknown` (after graph; the surviving side for one-sided extraction
   failures) seed propagation. `format` and `location` changes never do.
   Ordering: removed, signature, unknown, body, added; then after before before,
   then entity id.
3. **Reverse paths.** Each seed runs the bounded S12 traversal (`incoming`) on the
   lease of its side under one shared allowance. Every impacted entity carries the
   path from the changed entity to it: IDENTITY nodes and canonical assertion proofs
   (hops) from that snapshot, so a removed symbol's callers are explained from the
   **before** evidence even though the after graph has no edge to follow.
4. **Ranking.** `(distance, category weight, side, changed entity id, entity id)`.
   An entity reached from several seeds or sides keeps its single best explanation;
   `reached_from_seeds` counts the distinct seeds that reached it; `also_changed`
   marks entities that are themselves in the change set.

## Coverage and boundaries

`coverage.state` is `complete` only when the reason set is empty, and that means
*no same-name unresolved or ambiguous references were found for the probed
entities and no limit was hit* - **not** a proof of completeness. Reasons:
`unresolved_reference`, `ambiguous_reference`, `reference_coverage_unknown`,
`work_limit`, `seed_limit`, `boundary_probe_limit`, `partial_extraction`.

A boundary is a **name-based** probe: for each seed and each impacted entity (on
the side it was explained on), the server counts raw references with the same
spelled name that never resolved to an edge, and ambiguous edges with that name.
This over-approximates (any language, any same-named symbol). Aliased imports
create references under the local alias, which the probe does not follow
(`aliased_reference_names_not_probed`). Rust and `jsx` references are always
unresolved. Coverage is computed from the whole captured collection, so truncating
the presentation never hides an incomplete state.

## Size, admission and determinism

Collection finishes first. `seeds` are metadata and always returned. `impacted`
atoms and then `boundaries` atoms are admitted as **one strict prefix** of the ranked
sequence on the conservative dual text+structured SDK framing; the first atom that
does not fit stops admission (no skipping, no reordering). `impacted_total`,
`boundaries_total` and `seeds_total` stay honest; `selection` says why it stopped.
`IMPACT_MINIMUM_BUDGET` (7730 bytes, measured at import from the worst-case
metadata envelope) is the smallest `max_bytes` the server will attempt; below it
the fixed `INVALID_BUDGET` envelope is returned. At exactly the minimum, a request
whose real metadata (for example many seeds) does not fit gets the tool-raised
`INVALID_BUDGET` ("Impact metadata cannot fit the result budget."). Results are not
paginated; raise `max_bytes` or narrow relations/depth.

There are no timestamps, durations, absolute paths or host data, so the canonical
result is a pure function of the two snapshots, the request and the configuration:
repeating a request reproduces identical bytes and the same `impact_id` whenever the
deterministic frontier is the same. A deadline-stopped run is replay-identical only
if its frontier commitments match.

## Operator bounds on the change-set phase (not request arguments)

Computing the change set parses changed files **synchronously and blocks the event
loop** for that duration. It is bounded before any entity load or parse by
`serve --impact-max-diff-entities` (default 50,000 per side),
`--impact-max-diff-occurrences` (250,000 per side) and `--impact-max-changed-files`
(200 distinct files `diff` would parse). Exceeding one returns
`IMPACT_DIFF_TOO_LARGE` with a closed reason (`diff_entity_limit`,
`diff_occurrence_limit`, `changed_file_limit`) and no result.

Defaults come from a Day-1 measurement on a synthetic Python repository (Docker,
uid 1000): loading both sides took ~0.49 s for 21,000 entities / 41,000
occurrences per side, and parsing 500 changed files (10,000 changed-file entities)
took ~2.2 s (about 4.4 ms per changed file). 50,000 entities/200 files therefore
bound the synchronous phase to roughly 1-2 s on that class of machine. Lease
acquisition (full generation validation) and the change-set phase are outside the
propagation counters and are observed separately; `work` and `diff_work` in the
result report the deterministic counters.

## Typed errors

`IMPACT_UNCONFIGURED`, `SNAPSHOT_DIFF_REJECTED` (closed reasons: the S19 rejections
plus `same_namespace`; also any failure opening the before snapshot, mapped to
`index_unavailable`), `IMPACT_DIFF_TOO_LARGE`, `SNAPSHOT_MISMATCH`,
`INVALID_BUDGET`, `INVALID_INPUT`. Messages are fixed strings: no exception text and
no filesystem path ever reaches a result.

## CLI

```bash
atlas-aci impact --repo R --before-graph-root B --after-graph-root A
atlas-aci impact --graph-root G --before-repo R1 --after-repo R2 \
    --relations call,import --max-depth 4 --max-bytes 200000
```

Per-side repository and graph-root options are exactly those of `atlas-aci diff`.
Canonical JSON plus one newline goes to stdout and is byte-identical to the MCP
`structuredContent` for the same request. A typed rejection exits 1 with
`Impact rejected: <CODE>[: <reason>]` on stderr and an empty stdout; invalid usage
(including an invalid limit or a budget below the metadata minimum) exits 2.

## Limitations

Static reachability only: dynamic dispatch, reflection and runtime wiring are not
modelled; tests are not selected; behavior-bearing comment edits are classified as
`format` by the S19 diff and do not seed; the probe is name-based and aliased names
are not followed; `import` coupling is opt-in. The implementation composes private
S19 helpers (`_check_compatible`, `_Loaded`, `_build`) so that `diff.py` and the
S19 digests stay unchanged; a test pins the equality with `diff.compare`.
