---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T12:00:00Z
slice_id: ACI-S36
issue: https://github.com/Rynaro/atlas-aci/issues/57
---
# ACI-S36 - measure optional Leiden communities against the existing LPA baseline

## Scope

Issue #57. Base `53032fe` (main after PR #101; S01-S35 and S37 merged). Branch `aci-s36`.
- Marker `<!-- atlas-aci:slice:ACI-S36 -->` verified.
- Criteria digest `85f3c4758e6c24ab3926439212851d038d4587ac194319418871f23d12ed77fd` (7 product ACs + RUN-01/02).
- Risk P2. Timebox 3 engineering days. Rightsize tier `lite` (score 4); an independent checker still runs.
- **Dependencies PRESENT.** S17 (#38) and S23 (#44) receipts are COMPLETE. Both capabilities are consumed as data and pure code. No interface amendment.

Observed facts (verified at planning):
- **LPA** (`mcp-server/src/atlas_aci/label_propagation.py:46-105`).
  - Stdlib only. Takes `dict[node, set[node]]`.
  - Visits nodes in sorted order, asynchronously. Ties go to the smallest label.
  - `DEFAULT_MAX_ITERATIONS=100` (:43).
  - Renumbers communities 0..k-1 by smallest member. No seed. Isolated nodes become singletons.
- **Production selection.** `codegraph.py:39` imports it; `:677` sets `_LPA_MAX_ITERATIONS = 100`; `communities()` (:5305) calls `label_propagation(adjacency, _LPA_MAX_ITERATIONS)` (:5378).
  - Node = `(path, line_start, name)`. Undirected, self-loops dropped (:5365), confident edges only.
- **S17 probe export** (`scripts/probe-export-confident-graph.py:93-136`).
  - Writes `nodes` in `communities()` order and edges as sorted unique `i<j`.
  - A node whose only edge is a self-loop is still exported (:82-84), so isolated nodes can exist.
- **Precedent `verify-probe-verdict.py`.**
  - Frozen constants are never read from the audited file (:161-185).
  - `_modularity` (:317) is Q = sum(L_c/m - (D_c/2m)^2).
  - LPA is loaded by file path (:373-392) and re-run (:395-421).
- **Bundle** `.spectra/changes/aci-s17/probe/probe-graphs.json.gz`: 126726 bytes, sha `c5bfe097…1ffe` (sidecar :24).
  - Keys `solidus` (1883 nodes / 1782 edges) and `spree` (5474 / 5947).
  - Sidecar `lpa_q`: 0.9282557776291409 and 0.8726698244107914.
- **S23** (`atlas/evals/v3/manifest.v1.json`): `task_input_sha256 = 236c0def27a416dd93a411a4e40d2666510320c4d9f9cfff08f29a1145cc8fe6`.
  - Tasks: payment, request, unicode, phrase. Phrase terms are retry, transport, timeout.
  - `scripts/eval/v3_aggregate.py`: `canonical` (:104) and `ratio` (:133), a 6-place ROUND_HALF_EVEN string or null.
- **S18 fixture** `mcp-server/tests/fixtures/s18/useful-spans.v1.json` (sha `07553bd3d51be01a1d522fc59c240bf41ecf312a051c413c609585c60999b507`).
  - 7 `source_files`, plus `useful_spans` as byte ranges.
  - Its confident graph is expected to be about 7 nodes / 4 edges in 3 components. Day-1 (b) confirms.
- **Environment.** The dev image `atlas-aci-dev-s16` (CPython 3.12.16) has none of networkx, igraph, leidenalg or graspologic. CI runs Python 3.14.
- **Pins.**
  - Tools JSON: 7 tools, sha `94f9567a…ac56`, 1631472 bytes. Budgets 3436/3448/797/7730. `ev.RELATIONS` has 8 members.
  - CHANGELOG Unreleased/Added `bullets[0..3]` = S30/S33/S34/S35. `test_s35_isolation.py:411` needs `bullets[4]` to exist.
  - README `docs/rationale-drift.md` row (:948) is directly followed by `docs/verified-feedback.md` (:949).
  - `ci.yml` line numbers are S23 anchors (`test_s23_isolation.py:143-148`), so `ci.yml` is not edited.

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-05)
1. **No src change.**
   - These stay byte-identical: `mcp-server/src`, `pyproject.toml`, `uv.lock`, `.github/workflows/*`, every existing `scripts/*` and `atlas/evals/*`.
   - The runner loads `label_propagation.py` by file path and is stdlib-only apart from the optional provider.
   - Never create a file named `aci-s17-probe-lpa-vs-louvain.*`.
2. **Provider.**
   - `leidenalg` + `igraph`, `ModularityVertexPartition`, `n_iterations=-1`, seeds 0..9. Versions are chosen at Day-1 (f) and recorded.
   - Detection uses `find_spec`, `import_module` and `importlib.metadata.version`, all at call time.
   - Absent → `skipped_missing_provider`, exit 0. `--require-provider` → exit 1.
   - A pin mismatch is a typed rejection. Nothing is ever installed automatically.
3. **Tests.**
   - AC-005 runs the real CLI with no provider.
   - AC-001..004 use a fixture provider tagged `provider_kind:"fixture"`.
   - One real-provider test is guarded by `pytest.importorskip`.
   - AC-002 evidence comes from the fixture provider's own capture.
4. **A real measured run is required.** It runs in a throwaway `--rm` container. Evidence goes under `.spectra/evidence/aci-s36/real-run/` (at most 4 MB) and is verified in the dev image. An install failure is recorded as `skipped_missing_provider`; it is not a STOP.
5. **Manifest.** `atlas-community-experiment-manifest.v1` is preregistered and committed before any run. The verifier hard-codes the production digest and thresholds, and a test pins them.
6. **Projection.** D3. The same `(n, edges)` goes to both partitioners. Each run records the digest of what it was given.
7. **Graphs and tasks.** S18 task graph (frozen export) plus solidus/spree from the bundle (by sha, never copied). Usefulness uses `offline_community_expansion_proxy.v1`. The `inconclusive` outcome is preregistered.
8. **Metrics.** Exact `Fraction` arithmetic, quantised like S23 `ratio`. Exact ARI. No NMI. Timing and RSS are telemetry only.
9. **Decision vocabulary.** `retain_lpa` (default), `leiden_candidate_for_review`, `inconclusive`, `skipped_missing_provider`. Deployment selection is out of scope.
10. **Verifier.** Stdlib only; exit 0/1/2; closed reasons; strict JSON; bounded reads; iterative code.
11. **AC-007 pins.** The `is` identity of `label_propagation`, the value 100, `communities()` unchanged, lock bytes unchanged, and no provider name in the lock files.
12. **Scope amendment.** CHANGELOG bullet at index 4, the README row, `test_s36_*.py` and `.spectra/evidence/aci-s36/**`. No `ci.yml` edit. Hygiene is self-applied (D12).
13. **Invariance** pins as listed above.

### In
- **`scripts/verify-community-experiment.py`** (stdlib). It holds the library of record:
  - constants, vocabularies, strict loading, `project`, `canonical`, `ratio_of`, `validate_partition`;
  - `quality`, `ari`, `stability`, `usefulness`, `decide`, `rerun_lpa`;
  - `verify(results_dir, manifest_path, expected_sha, repo_root)` and the CLI.
- **`scripts/experiment-leiden-vs-lpa.py`**:
  - provider detection, the per-run child measurement (`measure-one`), the Leiden adapter, artifact assembly, the CLI.
  - It loads the verifier module and `label_propagation.py` by file path.
- **`mcp-server/tests/fixtures/community_experiment/`**:
  - `manifest.v1.json` (production), `manifest.fixture.v1.json`;
  - `s18-task-graph.v1.json`, `graphs/synthetic-cliques.v1.json`;
  - `fixture_site/{igraph,leidenalg}/__init__.py` plus two `*.dist-info/METADATA`.
- **Tests:** `test_s36_community_experiment.py` (the 7 named ACs), `test_s36_support.py`, `test_s36_semantics.py`, `test_s36_isolation.py`.
- **Docs:** `docs/experiments/community-comparison.md`, the CHANGELOG bullet and the README row.
- **Evidence:** `.spectra/evidence/aci-s36/{qualification.json, base-invariants.json, real-run/**, production-scenario.sh, py314.log, gate logs}`.

### Out
- Changing `communities()`, the LPA, the MCP surface or any dependency. Any `pyproject`/`uv.lock` edit, even dev extras.
- Automatic installation; a networkx or graspologic provider; NMI; significance tests.
- Byte-budget retrieval through atlas_aci; labelling solidus/spree; threshold changes after results.
- Adopting Leiden. The strongest possible outcome is "candidate for a separately reviewed decision".

## Approach

### Decisions

**D1 Formats.**
- Canonical JSON = the S23 rule: sorted keys, `(",",":")`, `ensure_ascii=False`, `allow_nan=False`, final LF.
- gzip uses `mtime=0`, level 9. Identity is always the sha256 of the decompressed canonical bytes.
- Ratios `R = {num, den, value}`:
  - `num/den` is the reduced `Fraction` (den > 0) and may be signed;
  - `value` is `format(Decimal(num)/Decimal(den) quantized 0.000001 ROUND_HALF_EVEN, "f")` with prec 60 in a `localcontext`;
  - zero denominator → `{num:0, den:0, value:null}`.
- No float appears in any S36 artifact, telemetry included (integers only).

**D2 Manifest `atlas-community-experiment-manifest.v1`** (closed key sets at every level; `manifest_sha256 = sha256(canonical(manifest))`):
```
{format, slice:"ACI-S36", experiment_id:"leiden-vs-lpa.v1",
 projection:{rule:"atlas-community-projection.v1", directed:false, weighted:false,
   self_loops:"dropped", multi_edges:"collapsed", node_order:"path_line_name_ascending",
   isolated_nodes:"retained"},
 graphs:[{id:"s18-task-graph", role:"task_graph", kind:"graph_file",
          path:"mcp-server/tests/fixtures/community_experiment/s18-task-graph.v1.json", sha256},
         {id:"solidus"|"spree", role:"graph_only", kind:"probe_bundle",
          path:".spectra/changes/aci-s17/probe/probe-graphs.json.gz", sha256:"c5bfe097…1ffe", key}],
 tasks:{graph_id:"s18-task-graph", s23_manifest_path:"atlas/evals/v3/manifest.v1.json",
   task_input_sha256:"236c0def…8fe6", items:[{corpus,id,terms,text}] (verbatim S23 tasks),
   useful_spans:{path:"mcp-server/tests/fixtures/s18/useful-spans.v1.json", sha256:"07553bd3…b507"}},
 algorithms:{lpa:{module_path:"mcp-server/src/atlas_aci/label_propagation.py", module_sha256,
   max_iterations:100, seeded:false},
   leiden:{provider:{kind:"real"|"fixture", distributions:{igraph:VER, leidenalg:VER}},
   partition_type:"ModularityVertexPartition", n_iterations:-1, seeds:[0,…,9]}},
 metrics:{arithmetic:"exact_fraction_ratio6_half_even",
   quality:["modularity","coverage","community_count","singleton_count","size_max",
            "size_median","size_p90","disconnected_communities","disconnected_share"],
   stability:["pairwise_ari","identical_pairs","lpa_vs_leiden_ari"],
   usefulness:{proxy:"offline_community_expansion_proxy.v1", budgets_nodes:[2,4,8]}},
 decision:{rule:"atlas-community-decision.v1", stability_min_mean_pairwise_ari:{num:9,den:10},
   min_budgets_strictly_better:1, modularity_margin:{num:0,den:1},
   outcomes:["retain_lpa","leiden_candidate_for_review","inconclusive","skipped_missing_provider"]},
 bounds:{max_input_bytes:16777216, max_nodes:200000, max_edges:1000000, run_timeout_seconds:600},
 telemetry:{canonical:false, time:"perf_counter_ns_partitioner_call",
   memory:"ru_maxrss_fresh_subprocess_per_run"}}
```
- **Verifier FROZEN constants**, cross-checked and never substituted. A difference in any of them is `manifest_constants_mismatch`:
  - the projection block, seeds 0..9, budgets (2,4,8), the decision block;
  - partition_type and n_iterations; LPA max_iterations 100;
  - `task_input_sha256`, the useful-spans sha, the bundle sha;
  - the bounds, the metric lists.
- **`PRODUCTION_MANIFEST_SHA256`** is a hard-coded constant, filled in after Day-1 (f). The production manifest must have `provider.kind == "real"` and versions without `+atlas`.
- **The fixture manifest** differs only in `graphs` (s18-task-graph plus synthetic-cliques) and `provider` (`kind:"fixture"`, both versions `0.0.0+atlas.fixture`).
- The pairing `kind` ⇔ `+atlas` suffix is enforced; violation → `provider_pin_invalid`.
- `module_sha256` pins the LPA file. A future LPA edit makes the committed evidence fail verification, which is intended (risk 6).

**D3 Projection `atlas-community-projection.v1`** (`project(nodes, edges, bounds)`):
1. Nodes must be `[str, int, str]` (int not bool, ≥ 1) and unique. Edges must be `[int, int]` within range. Otherwise `graph_invalid`.
2. `order = sorted(range(len(nodes)), key=lambda i: tuple(nodes[i]))`. Remap indices.
3. For each edge: drop it if u == v; otherwise `(min, max)`. Collapse duplicates with a set, then sort.
4. Require `1 ≤ m`, `n ≤ max_nodes`, `m ≤ max_edges`, else `graph_too_large`. Zero edges → `graph_invalid`.
5. Every node is retained, isolated ones included. igraph receives `n` vertices; LPA receives `{i: set()}` for each.

Digests:
- `nodes_sha256 = sha256(canonical(nodes))`;
- `projection_sha256 = sha256(canonical({"edges":E, "nodes":N}))`.

Sources:
- The S18 file and bundle entries are already canonical; a test asserts that the projection is the identity on them.
- The bundle is read bounded (`GzipFile.read(limit+1)`) and only `nodes` and `edges` of `key` are used. `lpa_labels` and `louvain_partitions` are ignored.

**D4 Task graph fixture `atlas-community-graph.v1`**:
```
{format, graph_id, nodes, edges,
 provenance:{source_fixture, source_fixture_sha256, exporter:"scripts/probe-export-confident-graph.py",
 exporter_sha256, materialization:"source_files written verbatim (UTF-8) at their relative paths"}}
```
- Produced on Day-1 (b) in the dev image: materialise the S18 `source_files` into a tmp repo, run the unchanged exporter, keep `nodes` and `edges`.
- `test_s18_task_graph_reproduces` recomputes it (one 7-file build) when `atlas_aci` is importable.
- If Day-1 measures more than 30 s, pin by sha and provenance only, and record that in the receipt.
- `graphs/synthetic-cliques.v1.json` is a fixture-only graph (never in the production manifest). Its generator spec is in the provenance; Day-1 (g) chooses it.

**D5 Partition artifact `atlas-community-partitions.v1`** (`partitions.json.gz`):
```
{format, manifest_sha256,
 graphs:[{graph_id, n, nodes_sha256, projection_sha256,
   runs:[{run_id:"lpa"|"leiden-seed-<s>", manifest_sha256, algorithm:"lpa"|"leiden", seed:null|int,
          provider_kind:"atlas_lpa"|"real"|"fixture", input_projection_sha256,
          communities:[[int,…],…]}]}]}
```
- Graphs follow manifest order. Runs are `lpa` then seeds ascending.
- **Canonical partition:** every community is non-empty and strictly ascending; communities are ordered by smallest member; together they cover 0..n-1 exactly once.
- `validate_partition(communities, n)` has precedence order:
  1. `partition_invalid_type`: not a list of lists of int, or any bool;
  2. `partition_node_unknown`: index < 0 or ≥ n, i.e. an invented identity;
  3. `partition_node_duplicated`;
  4. `partition_node_omitted`;
  5. `partition_noncanonical`: unsorted, empty community, or wrong community order.
- A `nodes_sha256` that differs from the projection gives `partition_node_table_mismatch`.

**D6 Provider adapter and run isolation.**
- **Parent.**
  - Detects `find_spec("leidenalg")` and `find_spec("igraph")`.
  - If either is None → skipped, with `missing` sorted.
  - Otherwise `import_module` both (failure → reject `provider_import_failed`) and `metadata.version` of each distribution. Not equal to the manifest pin → reject `provider_version_mismatch`.
  - `provider_kind = "fixture"` iff the version contains `+atlas.fixture`.
- **Child.** Every run (LPA included) is a fresh child:
  - argv: `[sys.executable, *flags, runner, "measure-one", --projection F, --algorithm A, --seed S, --repo-root R]`;
  - `flags` reproduces `-I`/`-S`/`-E` from `sys.flags`;
  - environment inherited; `subprocess.run(..., capture_output=True, timeout=run_timeout_seconds)`, with no shell.
- **Child computation.**
  - Parses the canonical projection file.
  - Computes `input_projection_sha256` from the very `nodes` and `edges` objects it passes on.
  - LPA: `label_propagation(adjacency, 100)`, from the module loaded by path.
  - Leiden:
    ```
    g = igraph.Graph(n=n, edges=[tuple(e) for e in edges], directed=False)
    membership = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition,
                                          n_iterations=-1, seed=s).membership
    ```
  - `elapsed_ns` (`perf_counter_ns`) covers the partitioner call only.
  - `ru_maxrss` is read before and after the call. Normalised: bytes on darwin, ×1024 on linux, otherwise `unavailable`.
  - Prints canonical `{membership, input_projection_sha256, versions|null, elapsed_ns, rss_*}`.
- **Parent checks on the child result.**
  - Child exit ≠ 0 → `measurement_failed`; timeout → `run_timeout`.
  - Versions ≠ the parent's → `provider_changed_during_run`.
  - Membership: a list of length n of ints, else `partition_invalid`.
  - Canonicalises (group, sort, order by min member), then runs `validate_partition`; failure → `partition_invalid`.

**D7 Metrics (verifier library; the runner imports the same functions).**
- **Rationale.** There is one implementation, so runner and verifier cannot drift. The verifier recomputes everything from the retained partitions, never from reported numbers. Correctness rests on independent test oracles (Supporting tests, semantics).
- **Quality**, per run, with m edges, `D_c` = degree sum and `L_c` = internal edges:
  - `modularity = sum(Fraction(L_c, m) - Fraction(D_c, 2m)**2)`;
  - `coverage = R(sum L_c, m)`;
  - `community_count`; `singleton_count`, with `isolated_nodes` reported per graph;
  - sizes s ascending: `size_max`; `size_median = s[ceil(k/2)-1]`; `size_p90 = s[ceil(9k/10)-1]` (nearest rank, integer math);
  - `disconnected_communities`: communities whose internal-edge subgraph is disconnected, found by iterative union-find (singletons count as connected); `disconnected_share = R(count, k)`.
- **ARI(P, Q)**, exact:
  - `index = Σ C2(n_ij)`, `A = Σ C2(a_i)`, `B = Σ C2(b_j)`, `N = C2(n)`;
  - `E = Fraction(A*B, N)`, `M = Fraction(A+B, 2)`;
  - `ARI = (index - E)/(M - E)`.
  - If M == E then the partitions are identical (asserted) and ARI = 1.
- **Stability**, per graph (null when skipped):
  - `pairwise_ari` for the 45 pairs (a < b) in seed order;
  - `min_pairwise_ari`, `mean_pairwise_ari` (sum/45);
  - `identical_pairs`, `identical_pair_share = R(·, 45)`, `distinct_leiden_partitions`;
  - `lpa_vs_leiden_ari[seed]`;
  - `lpa: "deterministic_no_seed"`.

**D8 Usefulness `offline_community_expansion_proxy.v1`** (task graph only):
- **Gold(t).** Node v is gold for task t iff:
  - `source_hashes[path(v)]` equals sha256 of the UTF-8 `source_files[path(v)]` (else `task_anchor_mismatch`); and
  - `off(v)` lies in `[start_byte, end_byte)` of a useful span of t with `span.path == path(v)` and an equal `source_sha256`.
  - `off(v)` is the byte offset of the start of 1-based line `line(v)`, splitting on `b"\n"` only (tree-sitter rows).
- **Seeds(t).**
  - Tier `exact_name`: the nodes whose name is in `terms`.
  - If that is empty, tier `lexical_file`: the nodes whose file text contains `text` as a case-sensitive UTF-8 byte substring.
  - Else tier `none`, with no seeds.
  - Seeds are in index order. This mirrors S17 exact_name → lexical, and the partition never affects seeds.
- **Expansion list L(P, t).** Seeds first; then, for each seed in order, its community's members ascending, appending unseen ones. `selected(b) = L[:b]` for b in (2, 4, 8).
- **Per row** `(run_id, budget, task)`: `selected`, `hits = |sel ∩ gold|`, `selected_count`, `gold_count`.
- **Totals** per `(run_id, budget)`: micro sums, `recall = R(hits, gold)`, `precision = R(hits, selected)`.
- Expected seeds and gold (Day-1 (c) confirms):
  - payment: exact {process_payment@app/payments.py}; gold {process_payment, validate_payment, write_ledger}.
  - request: exact {send_request}; gold {send_request, retry_request}.
  - unicode: exact {render_text}; gold {render_text, escape_text}.
  - phrase: lexical over app/client.py (noise/task.py has no graph node); gold {retry_request}.

**D9 Decision `atlas-community-decision.v1`** (`decide()` with FROZEN constants only):
- **Conditions** (each recorded; null when skipped):
  - `provider_available`;
  - `task_evidence_present`: Σ gold > 0 and some task has seeds;
  - `task_graph_partitions_differ`: some Leiden seed partition ≠ LPA on the task graph;
  - `usefulness_no_worse_all_budgets`: for every seed s and budget b, `recall_L ≥ recall_LPA` and `precision_L ≥ precision_LPA` (null equals null; null versus non-null counts as worse);
  - `strictly_better_budgets`: the number of b with `min_s recall_L(s, b) > recall_LPA(b)`;
  - `stability_floor_met`: every graph has `mean_pairwise_ari ≥ 9/10`;
  - `modularity_not_below_lpa`: every graph has `min_s Q_L(s) ≥ Q_LPA + 0`.
- **Outcome precedence:**
  1. not available → `skipped_missing_provider`, reasons `["provider_missing"]`;
  2. not `task_evidence_present` → `inconclusive` `["no_task_evidence"]`; partitions identical → `inconclusive` `["task_graph_partitions_identical"]`;
  3. any failure → `retain_lpa`, reasons in fixed order from `usefulness_regression`, `no_usefulness_gain` (strictly_better < 1), `stability_below_floor`, `modularity_below_lpa`;
  4. otherwise `leiden_candidate_for_review`, reasons `[]`.
- Modularity alone can never produce a candidate.
- **Preregistered expectation, stated plainly.** The S18 graph is small and splits into clean components, so both algorithms will probably return the same partition there. The predicted outcome is therefore `inconclusive`. This is a property of the frozen corpus, not a defect.

**D10 Result `atlas-community-experiment-result.v1`** (`result.json`, canonical):
```
{format, manifest_sha256, experiment_id, status:"complete"|"skipped_missing_provider",
 provider:{available, kind:null|"real"|"fixture", missing:[…], versions:null|{igraph,leidenalg}},
 inputs:[{id, kind, path, sha256}] (graphs, useful_spans, s23_manifest, lpa_module; sorted by id),
 partitions_sha256,
 graphs:[{graph_id, role, manifest_sha256, n, m, isolated_nodes, nodes_sha256, projection_sha256,
   runs:[{run_id, manifest_sha256, algorithm, seed, input_projection_sha256, quality:{…}}],
   stability:null|{…D7}}],
 usefulness:{proxy, manifest_sha256, graph_id, task_input_sha256, useful_spans_sha256, budgets,
   tasks:[{task_id, seed_tier, seeds, gold}], rows:[{manifest_sha256, run_id, budget, task_id,
   selected, hits, selected_count, gold_count}], totals:[{manifest_sha256, run_id, budget, hits,
   selected_count, gold_count, recall, precision}]},
 decision:{manifest_sha256, rule, outcome, reasons, conditions},
 limitations:[LIMITATIONS], canonical_sha256}
```
- `canonical_sha256 = sha256(canonical(result minus canonical_sha256))`.
- A skipped result still carries the LPA rows, quality and usefulness, but no Leiden rows.
- **Telemetry `atlas-community-telemetry.v1`** (`telemetry.json`, non-canonical):
  ```
  {format, canonical:false, manifest_sha256, result_canonical_sha256,
   platform:{system, machine, python_version, python_implementation},
   runs:[{graph_id, run_id, elapsed_ns, peak_rss_bytes_before_call, peak_rss_bytes_after_call, rss_source}]}
  ```
  Telemetry never enters a canonical digest or the decision.
- Artifacts carry no timestamps, no absolute paths and no host data.

**D11 CLIs.**
- **Runner:** `python scripts/experiment-leiden-vs-lpa.py run --manifest M --manifest-sha256 HEX --out DIR [--repo-root R] [--require-provider]`.
  - `--repo-root` defaults to the script's `parents[1]`.
  - `--out` must not exist, its parent must exist, and it must not lie under `<repo-root>/mcp-server`.
  - Writes go to a `mkdtemp(dir=parent, prefix=".s36-run-")` directory, which is then renamed. On any failure the temp directory is removed and no output is left.
  - On success stdout is `atlas-community-run-summary.v1` `{format, status, outcome, manifest_sha256, canonical_sha256, partitions_sha256}`.
  - `measure-one` is internal and documented as such.
  - Runner pipeline (first failure wins): read the manifest (≤ 1 MiB) → strict parse + schema → sha ≠ `--manifest-sha256` → constants (via the verifier) → inputs (bounded read, sha, task anchors) → projection → provider → children → assemble → self-verify (calls `verify`; failure → `self_check_failed`) → write.
- **Verifier:** `python scripts/verify-community-experiment.py --results DIR [--manifest M] [--manifest-sha256 HEX] [--repo-root R] [--max-artifact-bytes N]`.
  - Defaults are the production manifest path and `PRODUCTION_MANIFEST_SHA256`.
  - On pass, stdout is `atlas-community-verification.v1` `{format, status:"pass", manifest_sha256, production_manifest, outcome, canonical_sha256, partitions_sha256, checks:[…]}`.
- **Exit codes and messages** (stderr, never `str(exc)` or absolute paths):

| Case | Exit | stderr |
| --- | --- | --- |
| Success, complete or skipped | 0 | — |
| Runner rejection | 1 | `Community experiment rejected: <reason>[: <detail>]` |
| Verifier rejection | 1 | `Community verification rejected: <reason>[: <detail>]` |
| Usage | 2 | `Usage error: <name>` |

- `<detail>` is a graph_id, run_id or input id only.

**D12 Vocabularies (closed, exported as tuples and tested).**
- `RUNNER_REJECTIONS`: `manifest_unreadable, manifest_invalid, manifest_digest_mismatch, manifest_constants_mismatch, provider_pin_invalid, input_unreadable, input_too_large, input_hash_mismatch, task_anchor_mismatch, graph_invalid, graph_too_large, provider_required_missing, provider_import_failed, provider_version_mismatch, provider_changed_during_run, measurement_failed, run_timeout, partition_invalid, self_check_failed, output_write_failed`.
- `VERIFIER_REJECTIONS`:
  - the first 11 runner reasons;
  - `artifact_unreadable, artifact_too_large, artifact_invalid_json, artifact_schema_invalid, canonical_digest_mismatch, partitions_digest_mismatch, manifest_reference_mismatch, projection_mismatch, run_set_mismatch`;
  - the 6 `partition_*` reasons;
  - `lpa_rerun_mismatch, metric_mismatch, usefulness_mismatch, decision_mismatch, skipped_state_inconsistent, provider_record_mismatch, telemetry_invalid`.
- `USAGE_NAMES` (runner): `command, manifest, manifest_sha256, out, out_exists, out_inside_mcp_server, repo_root`. Verifier: `results, manifest, manifest_sha256, repo_root, max_artifact_bytes`.
- `STATUSES`, `OUTCOMES`, `REASONS` (`provider_missing, no_task_evidence, task_graph_partitions_identical, usefulness_regression, no_usefulness_gain, stability_below_floor, modularity_below_lpa`), `SEED_TIERS` (`exact_name, lexical_file, none`), `PROVIDER_KINDS`.
- `LIMITATIONS`:
  1. `task_graph_is_small_and_may_not_discriminate`
  2. `usefulness_is_an_offline_node_level_proxy_not_agent_success`
  3. `graph_only_repositories_have_no_gold_labels`
  4. `leiden_results_are_pinned_to_provider_versions_and_platform`
  5. `fixture_provider_results_are_not_leiden_measurements`
  6. `timing_and_memory_are_telemetry_not_canonical`
  7. `modularity_alone_never_selects_an_algorithm`
  8. `node_budgets_not_byte_budgets`
  9. `production_default_remains_label_propagation`
  10. `deployment_selection_requires_a_separately_reviewed_decision`
- **Strict loading** (`load_strict`):
  - bytes, strict UTF-8, no BOM;
  - `object_pairs_hook` rejects duplicate keys; `parse_constant` rejects NaN/Infinity; `parse_float` rejects floats;
  - `RecursionError`/`MemoryError` → `artifact_invalid_json`;
  - reads bounded at limit+1.
- **Verifier pipeline** (first failure wins): usage → manifest (read, strict, schema, digest, constants, pin) → inputs → result (strict, schema, canonical digest, `manifest_sha256` on every row, decision and usefulness) → partitions (bounded gunzip, sha == `partitions_sha256`, schema, per graph `nodes_sha256`/`projection_sha256`/`input_projection_sha256`, run set = {lpa} ∪ (seeds if available else ∅), `validate_partition`) → LPA rerun equality → quality/stability recompute → usefulness → decision → skipped consistency (status skipped ⇔ provider unavailable ⇔ no Leiden runs ⇔ outcome skipped) and provider record == manifest pin → telemetry (closed keys, ints, bound to `manifest_sha256` and `result_canonical_sha256`; values never judged).
- **Default `--max-artifact-bytes`:** 67108864, min 1, max 268435456.

**D13 Fixture provider** (`fixture_site/`).
- **API subset only:**
  - `igraph.Graph(n, edges, directed)`, `vcount()`, `__version__`;
  - `leidenalg.find_partition(graph, partition_type, n_iterations, seed)` → an object with `.membership`;
  - `leidenalg.ModularityVertexPartition`, `__version__`;
  - dist-info `Name`/`Version: 0.0.0+atlas.fixture`.
- **Partitioner `atlas-fixture-greedy-modularity.v1`:**
  - node order permuted by a 32-bit LCG seeded with `seed`;
  - local moving with exact `Fraction` modularity gain; ties go to the smallest community label; at most 100 passes;
  - sorted iteration only (no set order). Real and deterministic; output computed, never canned.
- **Test hooks:**
  - `ATLAS_S36_FIXTURE_CAPTURE_DIR` writes `<pid>.json` = canonical `{"edges","n","seed"}` exactly as received;
  - `ATLAS_S36_FIXTURE_FAULT=omit` returns a membership one shorter.
- **Why this is not a mock of the behaviour under test.** Under test are the runner's detection, projection, child isolation, adapter calls, canonicalisation, metrics, artifacts, decision and the verifier, and all of them run for real against a real partitioner that implements the pinned interface. Fixture results are tagged `provider_kind:"fixture"`, carry a `+atlas.fixture` version and a non-production manifest digest, and never appear in the docs as Leiden.

**D14 Hygiene (self-applied; `test_s36_isolation`).** Applies to both scripts and the fixture packages:
- ASCII only; lines ≤ 100; no function calls itself (AST); regex literals contain none of `\d \w \s $`.
- **Verifier imports:** `__future__, argparse, collections, decimal, fractions, gzip, hashlib, importlib.util, json, math, pathlib, sys, typing`.
- **Runner imports:** the verifier set plus `importlib, importlib.metadata, os, platform, resource, subprocess, tempfile, time`.
- **Runner subprocess:** exactly one `subprocess.run` call site, inside `_measure_run`, with a list argv starting `sys.executable`, a `timeout=` keyword and no `shell`.
- **Banned in either script:** `os.system/popen/exec*/spawn*/fork`, `socket`, `urllib`, `http`, `ensurepip`, `pip`, `venv`, any call name containing `install`, and static `import igraph/leidenalg/networkx/graspologic`.
- **Banned tokens** (the S35 BANNED set plus `favourable`, `favorable`) in docs, the CHANGELOG entry, the README row and every emitted string value.

### Work plan (timebox 3 engineering days)

**Day 1 AM: qualification.** Before any product code. Output: `.spectra/evidence/aci-s36/qualification.json` plus `base-invariants.json` (tools sha/bytes, budgets, `ev.RELATIONS`, a sha manifest of the gated paths at 53032fe, the full suite with the known nonregular_pack deselect).
- (a) **LPA by file path equals production.** In the dev image, `label_propagation` by file path on the projected S18 export equals `CodeGraph(tmp).communities()` on the materialised S18 repo, by member identity. Re-running on the bundle equals `lpa_labels` for solidus and spree.
- (b) **Task graph export.** Two fresh S18 exports give identical nodes/edges. Record n, m, isolated, components and build seconds. Write `s18-task-graph.v1.json` and its sha.
- (c) **S23 anchors.** `digest(tasks) == 236c0def…`; the useful-spans file sha `== 07553bd3…`; source hashes match; D8 seeds and gold equal the expectation table.
- (d) **Projection digests** of all three graphs are equal on 3.12 (dev image) and in `python:3.14` `--rm` (and on the host if available).
- (e) **Bundle** sha `== c5bfe097…1ffe`, size 126726; record the decompressed bytes.
- (f) **Provider install attempt.**
  - In `python:3.12-slim` `--rm`: `pip download --only-binary=:all: --dest <scratch>/wheels leidenalg==X igraph==Y`, where X/Y are the newest releases with cp312 linux wheels that satisfy leidenalg's `Requires-Dist` on igraph.
  - Then `--network none`: `pip install --no-index --find-links /wheels --target /tmp/site`, import, `find_partition` on a triangle pair.
  - Record versions, wheel names and sha256s, `uname -m`, and the outcome (or the observed failure).
  - Then **freeze both manifests**; put the production digest into the verifier constant and the docs.
- (g) **Fixture partitioner non-vacuity.** On synthetic-cliques it differs from LPA for at least 1 seed and between at least 2 seeds; one fixture-manifest runner invocation takes under 15 s. Choose and record the generator spec.
- (h) `importlib.metadata.version` sees the fixture dist-info through PYTHONPATH on 3.12 and 3.14.
- (i) A runner/verifier skeleton passes the exact CI `ruff`/`ruff format`/`mypy ../scripts/` commands.

**STOP RULE** (return to the orchestrator; never adapt silently):
- (a) any mismatch: the experiment would not measure the production LPA;
- (b) the exports differ between runs;
- (c) or (e) a mismatch;
- (d) a digest differs across interpreters;
- (i) passing needs a config or CI edit.

Recorded adaptations (not stops):
- (b) build > 30 s → pin by sha (D4);
- (f) no wheel or network → real run recorded `skipped_missing_provider` with the failure log, and the docs say so;
- (g) fails → change the synthetic spec; never weaken tests;
- (h) fails → dist-info naming fix only.

**Day 1 PM:** the verifier library (D1, D3, D5, D7-D10, D12), `test_s36_support.py`, `test_s36_semantics.py` metric oracles.
**Day 2:** the runner (D6, D11), the fixture provider (D13), the 7 AC tests, the rest of semantics. Apply the cut order at Day-2 end if needed.
**Day 3:** the real run, evidence, docs, CHANGELOG, README, `test_s36_isolation.py`, gates, py314, production scenario, RUN-01/02.

**Real run** (implementer; `.spectra/evidence/aci-s36/real-run/`):
- `timeout 1800 docker run --rm --network none --user $(id -u):$(id -g) -e HOME=/tmp -v <repo>:/repo:ro -v <scratch>/wheels:/wheels:ro -v <scratch>/out:/out python:3.12-slim sh -c 'pip install --no-index --find-links /wheels --target /tmp/site leidenalg==X igraph==Y && PYTHONPATH=/tmp/site python /repo/scripts/experiment-leiden-vs-lpa.py run --repo-root /repo --manifest /repo/mcp-server/tests/fixtures/community_experiment/manifest.v1.json --manifest-sha256 <H> --out /out/run1 && … --out /out/run2'`.
- Copy `run1` to `real-run/`.
- `replay.json` = `{run1, run2}` canonical and partitions shas, which must be equal.
- `environment.json`: image digest, versions, wheel sha256s, argv. The pip log goes to `run.log`.
- The total under `real-run/` must be at most 4 MiB. Otherwise keep only the gzipped spree/solidus Leiden partitions for seeds 0..9 and say so (not expected: about 1 MB estimated).
- The verifier must pass on it in the dev image with no provider.
- **Docs report** the outcome, reasons and conditions copied from `result.json`, plus the canonical sha. The telemetry table is labelled non-canonical. No qualitative adjective appears beyond the outcome token.

**Cut order** (each cut is recorded in the receipt; no AC is affected):
1. Docs depth beyond the scanned phrases.
2. The `disconnected_*` metrics. The follow-up is "connectivity metrics".
3. `size_p90`/`size_median` (keep max, count, singletons).
4. The host leg of Day-1 (d).
5. Verifier tamper breadth beyond one case per reason.

**Never cut:**
- the manifest digest and frozen constants;
- an identical projection for both partitioners with per-run digests;
- partition validation, LPA rerun, exact Q/ARI, usefulness, decision recompute;
- the skipped state;
- telemetry separation;
- the real-run attempt (or its recorded skip);
- the 7 AC tests, G-SRC, G-MCP.

### Independent checker conditions
Verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s36-plan-checker`, independent read-only review; 5 MAJOR, 9 MINOR, no BLOCKER). Every finding is accepted as a binding plan condition with the same number; where a condition conflicts with plan text above, the condition wins.

Orchestrator resolutions (under the user's autonomous-execution authorisation; stricter option chosen each time):
- Condition 1: the independent oracles are hand-computed literals in the test file, written without importing the verifier or runner; every `quality` and `stability` field of the AC-003 recompute is covered.
- Condition 2: `decide()` takes primitives only; the full outcome table and the 9/10 boundary rows are mandatory, not cut candidates.
- Condition 3: every closed rejection reason is reached by a test with exact stderr text; bound overrides exist only as keyword arguments of internal functions and are never exposed on the CLI.
- Condition 5: metric cuts, if any, are decided before the Day-1 (f) freeze; the production manifest is committed in the working tree (recorded by sha256 in `environment.json` and the receipt) before the real run; a post-freeze cut requires a recorded re-freeze before the real run. A runner rejection during the real run is recorded as a failure log, never as a skipped result.
- Condition 12: a `PackageNotFoundError` from `importlib.metadata.version` maps to the typed rejection `provider_import_failed` (the stricter choice), with a test.
- Condition 14: the two extra cuts are appended after the existing cut order; conditions 1-4 are not cuttable.

**Findings**

1. **MAJOR, oracle independence (D7, plan:223-224).**
   - Runner and verifier share one implementation, so an untampered AC-003 pass is tautological. It only proves integrity, not correctness. The plan names independent oracles only for Q, ARI and usefulness (plan:458-459, 469-477).
   - Nothing independent covers `coverage`, `singleton_count`, `size_median`, `size_p90`, `disconnected_*`, isolated nodes, the seed-then-community expansion order, or truncation.
   - **Binding condition:**
     - Add hand-computed literal expectations, with no import of the library under test, on tiny graphs. Include a community made of two non-adjacent nodes, an isolated node, even and odd k, and p90 at k=10 and k=11.
     - Include a hand-written expansion table with a community larger than the budget.
     - Include one pair-counting ARI case with unequal block sizes, checked as an exact Fraction.
     - Make the AC-003 independent recompute cover every `quality` and `stability` field, not just Q and ARI.
     - Keep the sidecar `lpa_q` check and the `verify-probe-verdict._modularity` parity as hard assertions.

2. **MAJOR, decision recompute (D9).**
   - On the frozen S18 graph each connected component is a path of at most 3 nodes. Modularity of the 3-node path is maximal as a single community, so LPA and any correct partitioner return the same partition.
   - Every end-to-end run, fixture included, therefore reaches only `inconclusive` or `skipped`. The `retain_lpa` and `leiden_candidate_for_review` paths and the usefulness conditions are never exercised through `verify`.
   - **Binding condition:**
     - `decide()` must take plain primitive inputs (Fractions, counts, booleans).
     - Add a hand-written table covering every outcome and reason order.
     - Add boundary rows: mean ARI exactly 9/10 passes and just below fails; recall tie is not strictly better; null versus non-null counts as worse; only one seed differing from LPA.
     - Pin the frozen thresholds by value in a test.

3. **MAJOR, rejection reachability.**
   - `run_timeout_seconds`, `max_*` and the other bounds are FROZEN, so a manifest cannot lower them. The plan still says every reason is reachable with exact text.
   - Only `FAULT=omit` exists. `run_timeout`, `measurement_failed`, `provider_changed_during_run`, `provider_import_failed`, `input_too_large`, `graph_too_large` (via the runner), `self_check_failed` and `output_write_failed` have no stated path.
   - **Binding condition:**
     - Extend the fixture `FAULT` vocabulary with `sleep`, `exit1` and `version_skew`, plus an import-error variant.
     - Give the internal functions (`_measure_run`, the bounded reader) keyword bound overrides that the CLI never exposes.
     - Test each reason with exact stderr text.
     - Override argparse `error()` so usage failures print `Usage error: <name>`, exit 2, for every `USAGE_NAMES` entry including unknown arguments.
     - Assert that any failure leaves no output directory and no `.s36-run-*` temp directory.

4. **MAJOR, AC-002 weak spot (D6, plan:206-208).**
   - "digest from the very objects it passes" is ambiguous. If the digest is taken from the parsed lists before the adjacency or tuple conversion, an adapter bug that drops edges still gets the right digest.
   - The independent capture covers only the fixture Leiden side.
   - **Binding condition:**
     - The LPA child derives its digest from the adjacency it actually passes (sorted `u<v` edge pairs, `n = len(adjacency)`).
     - The Leiden child derives its digest from the exact tuple list given to `igraph.Graph`.
     - The Leiden adapter asserts `g.vcount() == n` and `g.ecount() == m` before partitioning.
     - Test the LPA adjacency path against the test's independent projection.

5. **MAJOR, cut order against the freeze (plan:422-427, 391).**
   - Cuts 2 and 3 (`disconnected_*`, `size_p90`, `size_median`) alter the frozen metric lists. That changes the manifest digest, `PRODUCTION_MANIFEST_SHA256`, the verifier constants and the docs.
   - **Binding condition:**
     - Decide any metric cut before the Day-1 (f) freeze. After the freeze, a cut is allowed only with a recorded re-freeze and a re-pin that precedes the real run.
     - Commit the production manifest before the real run and record the commit sha in `environment.json` and the receipt.
     - Re-pinning provider versions after a failed attempt is allowed only for install or wheel reasons observed before any measured run, never for results.
     - If the real run ends in a runner rejection rather than an install failure, record a failure log with no result. Do not substitute a skipped result.
     - The verifier output carries `production_manifest` true or false, and the docs say only true counts.

6. **MINOR, fixture isolation.**
   - Fixtures shadow a real install through `PYTHONPATH`. That is intended for FX-RUN.
   - **Binding condition:** the AC-005 and partial-provider tests run the runner under `-S` so a host with a real leidenalg still sees `missing`.
   - Tests never mutate `sys.path` or `sys.modules`.
   - Test subprocesses and the runner set `PYTHONDONTWRITEBYTECODE=1`, and the runner sets `sys.dont_write_bytecode` before path-loading. Otherwise `__pycache__` appears under `fixture_site`, `src/` and `scripts/`.
   - The fixture-contents test ignores `__pycache__`.

7. **MINOR, full-suite gate (plan:506).**
   - "No new skip" contradicts the `importorskip` real-provider test. It adds exactly one skip in CI and in the dev image.
   - **Binding condition:** the gate allows exactly the one named skip, `test_real_provider_replay`, and `base-invariants.json` records it.

8. **MINOR, token scan (D14, plan:377).**
   - `tokens()` splits on non-alphanumerics (`test_s35_isolation.py:166`). `strictly_better_budgets` and `min_budgets_strictly_better` therefore contain the banned token "better", and the docs may name them.
   - **Binding condition:** rename them (for example `recall_gain_budgets`, `min_budgets_with_recall_gain`).
   - Run the BANNED scan over all vocabulary names, manifest keys and the docs before freeze.

9. **MINOR, partition typing (D5, D12).**
   - `parse_float` rejects floats at load, so a float member reaches the CLI as `artifact_invalid_json`, not `partition_invalid_type`.
   - **Binding condition:** document this. Test floats, NaN and duplicate keys at the CLI as `artifact_invalid_json`. Test `validate_partition` directly for bool, float and negative inputs. Test an empty community and `[1,1]` inside one community.

10. **MINOR, big integers.**
    - `mean_pairwise_ari` is a sum of 45 Fractions with distinct denominators, so it can reach about 1.5k digits. The 4300-digit `int` to `str` limit holds, but there is no test.
    - **Binding condition:** add a spree-scale test, run on 3.12 and 3.14, that serialises and quantises the largest Fractions.

11. **MINOR, retrieval claim (D8).**
    - The issue says "reuse S17's retrieval contract". The proxy re-states the tiers offline, and substring seeding differs from the FTS5 lexical tier.
    - **Binding condition:** add a limitation, or reword limitation 2, saying seeding restates S17 tier semantics and does not call `graph_query retrieve`. Say that lexical here is a case-sensitive whole-file substring.
    - The Day-1 (c) seeds and gold table is a preregistered expectation. A mismatch is a STOP, not an adaptation.

12. **MINOR, provider detection.**
    - `metadata.version` raises `PackageNotFoundError` when only the legacy `python-igraph` distribution is present.
    - **Binding condition:** map that to `provider_import_failed`, or to missing with the reason recorded, and add a test.
    - Day-1 (f) must still pin versions if the network is down. Record the choice basis, and record the skipped real run as such.

13. **MINOR, paths and loading.**
    - Specify that the verifier and `label_propagation.py` are loaded from the script directory and `repo_root/<module_path>` respectively, never from `--out`.
    - Compare `--out` against `mcp-server` using `resolve()`, symlink-safe.
    - Tmp repo-root copies must hold the bundle, the S18 graph, the useful-spans file, the S23 manifest and the LPA file.

14. **MINOR, timebox.**
    - 3 days is tight but achievable with Day-1 gating and a pre-freeze cut decision (finding 5).
    - Add as cuts, after the existing ones: tamper cases beyond one per reason, and per-reason exact-text tests collapsed into a table.

## Stories
As an operator, I want a preregistered, replayable comparison of the shipped LPA with a pinned optional Leiden provider over identical frozen inputs. Then any later default change rests on usefulness, stability and cost evidence, not on an algorithm's reputation.

As a reviewer, I want every number re-derivable from retained partitions and frozen task evidence, and a mechanical outcome that may be `retain_lpa` or `inconclusive`. Then no favourable claim can precede or exceed the measurement.

## Acceptance Criteria
Frozen bytes: `.spectra/plans/aci-s36.criteria.md` (sha256 `85f3c475…7fd`). AC-S36-001..007 and RUN-01/02 are not re-stated; the executor reads the frozen file.

**Fixtures.**
- FX-RUN: the fixture manifest run with `PYTHONPATH=fixture_site` (module-scoped, in a tmp out).
- FX-PROD: the production manifest run with `python -S`.
- Independent helpers in `test_s36_support` use only `hashlib`/`json`: `canonical`, `projection`, `reseal(dir)` (recomputes `partitions_sha256` and `canonical_sha256` after a tamper, so semantic checks are reached).
- Every `all(...)` is preceded by a non-empty assertion. No mock of the behaviour under test.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_community_experiment_uses_frozen_manifest | **Digest.** In-test `sha256(canonical(json.loads(manifest)))` of the fixture manifest == `manifest_sha256` on: the partitions doc, every partition run, the result, every graph and run row, every usefulness row and total, the decision, and the telemetry. **Production.** The production manifest's digest == `PRODUCTION_MANIFEST_SHA256` == the docs string. **Negatives** (exit 1, no out dir): wrong `--manifest-sha256` → `manifest_digest_mismatch`; a copy with the stability floor changed to 8/10 (correct sha passed) → `manifest_constants_mismatch`; an extra key → `manifest_invalid`; a graph byte flipped in a tmp repo-root copy → `input_hash_mismatch`; a fixture-kind pin without `+atlas` → `provider_pin_invalid`. |
| 002 | test_community_algorithms_share_input_projection | FX-RUN with `ATLAS_S36_FIXTURE_CAPTURE_DIR`. **Captures.** Exactly 10 per graph (one per seed). Each capture's sha of `canonical({"edges","n"})` == the test's independent `{n, edges}` digest for that graph. **Run rows.** Every row (`lpa` and every seed) has `input_projection_sha256` == the test-computed `projection_sha256` == the graph's `projection_sha256`. **LPA input.** Re-running `label_propagation` (by path) on the test's projection == the retained LPA partition. **Fault.** `FAULT=omit` → runner exit 1 `partition_invalid`. |
| 003 | test_community_metrics_are_rederived | **Pass.** The verifier CLI on FX-RUN exits 0 with checks ⊇ {lpa_rerun, quality, stability, usefulness, decision}. **Independent re-derivation.** The test recomputes Q (Fraction), pair-counting ARI (O(n²)) and usefulness rows from the partitions and `useful-spans.v1.json`, and they equal the result. **Tampers**, each resealed, each exit 1 with the exact reason: one quality `num` → `metric_mismatch`; one ARI → `metric_mismatch`; one `hits` or gold list → `usefulness_mismatch`; the outcome → `decision_mismatch`; one node moved in a Leiden partition → `metric_mismatch`; the LPA partition swapped for a Leiden partition → `lpa_rerun_mismatch`. **Telemetry.** Changed telemetry values → still exit 0. |
| 004 | test_community_replay_preserves_deterministic_results | **Replay.** Two FX-RUN invocations into fresh dirs, with `PYTHONHASHSEED` 0 and 4242 → byte-identical `result.json` and decompressed `partitions.json.gz`; equal summaries; `telemetry.result_canonical_sha256` equal. **Non-vacuity.** Synthetic-cliques Leiden partitions are not all identical across seeds, and at least one differs from LPA. A manifest copy with different graphs → a different `canonical_sha256`. The skipped run replays identically too. |
| 005 | test_community_missing_provider_is_skipped | **Run.** The real CLI with `python -S` on the production manifest → exit 0. **Result.** `status` and `decision.outcome == "skipped_missing_provider"`; `provider.available false`; `missing == ["igraph","leidenalg"]`; no Leiden runs; LPA rows present; `stability` null. **Verify.** The verifier exits 0. **Required.** With `--require-provider` → exit 1 `provider_required_missing`, no out dir. **Dev env.** When `find_spec` finds neither, the same also holds without `-S`. **Partial provider.** A PYTHONPATH with only the fixture `igraph` gives `missing == ["leidenalg"]`. |
| 006 | test_community_invalid_partition_rejected | From FX-RUN, each resealed, exit 1 with the exact text: a node omitted → `partition_node_omitted`; a node in two communities → `partition_node_duplicated`; index n and -1 → `partition_node_unknown`; `nodes_sha256` altered → `partition_node_table_mismatch`; unsorted members → `partition_noncanonical`; `True` as a member → `partition_invalid_type`. `validate_partition` returns the same reasons in D5 precedence. A missing seed run → `run_set_mismatch`. |
| 007 | test_community_experiment_preserves_default | **Identity.** `codegraph.label_propagation is label_propagation.label_propagation`; `codegraph._LPA_MAX_ITERATIONS == 100`; the `codegraph.py` text holds `label_propagation(adjacency, _LPA_MAX_ITERATIONS)`. **Default output.** `CodeGraph(tmp S18 repo).communities()` before == after FX-RUN and FX-PROD, and == the LPA re-run on its export. **Lock files.** sha256 of `pyproject.toml` and `uv.lock` equal before and after; none of `leidenalg`, `igraph`, `graspologic`, `networkx` in either (lowercased). **Output path.** An out under `mcp-server/` → exit 2 `out_inside_mcp_server`. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the one receipt is `.spectra/state/aci-s36.execution.json`. |

### Supporting tests
- **`test_s36_semantics.py`.**
  - Projection table: reordering, duplicates, self-loops, isolated nodes, bool line, duplicate identity, zero edges, `graph_too_large` via `project(..., max_nodes=3)`. Identity on the bundle and on the S18 file.
  - Quality oracles:
    - two triangles plus a bridge give Q = 5/14;
    - the single-community and all-singleton cases;
    - `_modularity` parity: the `verify-probe-verdict.py` function loaded by path, within 1e-12 on synthetic-cliques;
    - the bundle LPA Q equals the sidecar `lpa_q` within 1e-9.
  - ARI table: identical → 1; `[[0,1],[2,3]]` vs one block → 0; the pair-counting oracle on synthetic-cliques.
  - `ratio`/`canonical` byte parity with `v3_aggregate` (loaded by path) on signed and zero-denominator values.
  - The D8 gold/seed table for the four tasks.
  - `decide()` table covering every outcome and every reason combination.
  - Strict JSON: duplicate key, NaN, Infinity, a float, BOM, invalid UTF-8, deep nesting.
  - Every RUNNER and VERIFIER rejection reason reachable, with exact text. Usage exits 2 for each `USAGE_NAMES` entry.
  - `provider_version_mismatch`: the fixture site against the production manifest.
  - `telemetry_invalid`: an unbound `result_canonical_sha256`.
  - `test_s18_task_graph_reproduces` (D4).
  - `test_real_provider_replay`:
    - `pytest.importorskip("leidenalg")`;
    - asserts `metadata.version("leidenalg")` has no `+atlas` (else skip);
    - runs a manifest whose pin is the installed versions, twice → equal bytes, `provider_kind "real"`.
- **`test_s36_isolation.py`.**
  - D14 scans with planted-hit controls.
  - `fixture_site` contents are exactly the two packages and two dist-info directories.
  - **Tests:** `PRODUCTION_MANIFEST_SHA256` and the FROZEN constants pinned by value.
  - **G-MCP:** tools JSON sha/bytes and 7 tools; budgets 3436/3448/797/7730; `ev.RELATIONS`.
  - **Probe artifacts:** under `.spectra/changes` exactly the existing `aci-s17-probe-lpa-vs-louvain.{md,json}`.
  - **Evidence:** `real-run/` total ≤ 4 MiB; the verifier passes on it; the docs outcome equals `result.json` `decision.outcome` and its canonical sha.
  - **Docs:** ASCII; names every D12 value, both CLIs, the operator command `uv run --no-project --with leidenalg==X --with igraph==Y python scripts/experiment-leiden-vs-lpa.py`, the manifest digest, and the phrases "candidate for a separately reviewed decision", "modularity alone", "not canonical", "skipped_missing_provider"; BANNED-free.
  - **CHANGELOG:** the bullet located by "Leiden community experiment (ACI-S36)" == `bullets[4]`; `bullets[0..3]` unchanged; one bullet; no PRIOR_TITLES/locators; BANNED-free.
  - **README:** the row has 3 pipes and is directly after the `docs/verified-feedback.md` row; no prior locator and no `canary-missions.md`.

### Gates beyond the ACs
- **G-S36:** `uv run --frozen pytest tests/slices/test_s36_*.py -q` (dev image as uid 1000, and host).
- **Regression** (green and unchanged; run right after the CHANGELOG/README edits): `tests/slices/test_s17_*.py tests/slices/test_s23_*.py tests/slices/test_s22_eval.py tests/slices/test_s35_isolation.py tests/slices/test_s34_isolation.py tests/slices/test_s33_isolation.py tests/slices/test_s30_isolation.py tests/slices/test_s28_isolation.py tests/slices/test_s27_isolation.py tests/slices/test_s26_isolation.py tests/slices/test_s21_isolation.py tests/test_communities.py tests/test_thesis_negatives.py tests/test_export.py`.
- **CI-exact lint** (from `mcp-server/`):
  - `ruff check src/ tests/`; `ruff format --check src/ tests/`; `mypy src/`;
  - `ruff check ../scripts/ --exclude fingerprint-fixture`; `ruff format --check ../scripts/ --exclude fingerprint-fixture`; `mypy ../scripts/`;
  - all with `uv run --frozen`.
- **Full suite** with `--deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition`; no new failure or skip versus `base-invariants.json`.
- **G-SRC:**
  - `git diff --exit-code 53032fe -- mcp-server/src mcp-server/pyproject.toml mcp-server/uv.lock .github/workflows atlas/evals mcp-server/tests/fixtures/s18 .spectra/changes/aci-s17`;
  - `git diff --name-status 53032fe -- scripts` == exactly `A` for the two new scripts;
  - `git diff --name-only 53032fe -- mcp-server/tests` ⊆ `tests/slices/test_s36_*.py` ∪ `tests/fixtures/community_experiment/**`.
- **G-MCP:** `atlas-aci tools` sha `94f9567a…` / 1631472 bytes.
- **py314:**
  - `docker run --rm -v <repo>:/src:ro python:3.14 sh -c 'cp -a /src /w && cd /w/mcp-server && rm -rf .venv && pip -q install uv && uv run --frozen --extra dev pytest tests/slices/test_s36_*.py tests/test_communities.py tests/test_thesis_negatives.py -q'`;
  - log to `py314.log`.
- **real-run:**
  - `replay.json` equal;
  - the verifier exits 0 on `real-run/` in `atlas-aci-dev-s16`, with the provider absent from that image;
  - or the skipped run is recorded with its failure log.
- **Production scenario** (`.spectra/evidence/aci-s36/production-scenario.sh`, orchestrator-rerun, dev image, `--network none`):
  1. The verifier over `real-run/` → exit 0; the printed outcome == docs.
  2. The runner with `-S` on the production manifest → skipped; the verifier passes.
  3. Fixture replay: two FX-RUN invocations → `cmp` of `result.json` and of decompressed partitions; the verifier passes with the fixture digest.
  4. Hashes of `pyproject.toml`/`uv.lock` are unchanged; G-SRC; `atlas-aci tools` pin.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s36_community_experiment.py -q
uv run --frozen pytest tests/slices/test_s36_*.py -q
uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen python ../scripts/verify-community-experiment.py --results ../.spectra/evidence/aci-s36/real-run
```

## Confidence
To be computed with `ramza-score`.
- **Low churn.** No src or lock edit; two new standalone scripts; the only shared code is the unchanged LPA file, loaded by path (the S17 precedent).
- **Main uncertainties, covered by Day-1:**
  - LPA-by-path parity;
  - S18 export determinism;
  - cross-interpreter projection digests;
  - wheel availability for the pinned provider.

## Rejected Alternatives
- **networkx `leiden_communities`.** It is dispatch-only and needs a backend; float modularity.
- **Reusing `probe-modularity.py`.** It edits a gated script and its float metrics.
- **graspologic.** Heavy, gated to Python < 3.13, many transitive dependencies.
- **Monkeypatching a fake `leidenalg` inside pytest.** It would mock the behaviour under test and skip detection and child isolation.
- **In-process measurement.** Peak RSS would include earlier runs and the test harness.
- **Two independent metric implementations** (runner and verifier). They drift with no added assurance beyond the test oracles.
- **A new labelled corpus.** The issue excludes creating a favourable corpus.
- **Byte budgets via `line_end`.** The S17 export has no `line_end`, and adding it edits the fingerprinted exporter.
- **A `ci.yml` job.** It would shift the S23 line anchors; pytest carries the verification instead.

## Risks (ranked)
1. **The S18 task graph does not discriminate.** Outcome `inconclusive` is preregistered and stated before the run; solidus/spree add quality and stability only (limitations 1 and 3).
2. **Leiden replay drifts across platforms or versions.** Exact pins, `replay.json` in one container, limitation 4; the verifier does not regenerate Leiden output, it validates and scores it.
3. **No cp312 wheel or no network at Day-1.** Recorded `skipped_missing_provider`, docs say so; not a stop.
4. **Overclaiming.** Closed outcomes, BANNED scans, docs copy the outcome token mechanically, limitations 7 and 10.
5. **Fixture provider mistaken for Leiden.** Kind tag, version suffix, non-production digest, isolation scan of the docs.
6. **Pinned `label_propagation.py` sha.** Any future LPA edit fails the committed-evidence test by design. A later slice must re-measure or record the new sha; this is documented in the docs.
7. **CHANGELOG/README pins.** Bullet at index 4, row after verified-feedback, regression set run right after the edits.
8. **CI time.** Fixture runs are about 22 child processes per invocation and module-scoped; the committed-evidence verification on spree is about 10 s. Measured on Day 1 (g).
9. **Timebox.** Apply the cut order at Day-2 end.
