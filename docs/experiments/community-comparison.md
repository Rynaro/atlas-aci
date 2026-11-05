# Community comparison: optional Leiden against the shipped label propagation

This page documents an offline experiment (ACI-S36). It measures an optional Leiden provider against the label propagation that `CodeGraph.communities()` ships, over identical frozen inputs, and reports a mechanical outcome. It changes nothing in the product: the production default remains label propagation (`mcp-server/src/atlas_aci/label_propagation.py`), no dependency is added, no MCP tool is added, and the experiment never installs anything.

The most this experiment can report is `leiden_candidate_for_review`, which means a candidate for a separately reviewed decision. Deployment selection is out of scope and is not made here.

## Operator quick start

The provider is optional and is not part of the project's dependencies. To run the comparison with a pinned provider, without touching the project environment:

```sh
uv run --no-project --with leidenalg==0.12.0 --with igraph==1.0.0 python scripts/experiment-leiden-vs-lpa.py run \
    --manifest mcp-server/tests/fixtures/community_experiment/manifest.v1.json \
    --manifest-sha256 3239ec59d9fce113a115ff4b27c42bfbf06715060e1b2cb7e7de8e2e435a2beb \
    --out /path/to/new/output-directory
```

Then verify the output with the standard library only (no provider needed):

```sh
python scripts/verify-community-experiment.py --results /path/to/new/output-directory
```

The verifier prints `production_manifest: true` only when the manifest digest equals the digest frozen in the verifier, and only a verification with `production_manifest: true` counts as a measurement of the production experiment. A run over any other manifest, and every run that used the fixture provider, prints `production_manifest: false`.

## Preregistered manifest

The manifest `mcp-server/tests/fixtures/community_experiment/manifest.v1.json` (`atlas-community-experiment-manifest.v1`) is committed before any measured run. Its digest is the sha256 of its canonical bytes:

`3239ec59d9fce113a115ff4b27c42bfbf06715060e1b2cb7e7de8e2e435a2beb`

Every measured result carries this digest, and the runner refuses to start when `--manifest-sha256` differs. The verifier hard-codes the projection rule, the seeds, the budgets, the decision thresholds, the bounds, the partitioner settings and the task anchors; it never reads them from the artifacts it audits, so a manifest cannot lower a bar. The provider versions are part of the manifest and therefore of its digest.

| Frozen setting | Value |
| --- | --- |
| Partitioner settings | `ModularityVertexPartition`, `n_iterations=-1`, seeds 0 to 9 |
| LPA | `label_propagation(adjacency, 100)`, loaded by file path and pinned by sha256 |
| Provider pin | `leidenalg==0.12.0`, `igraph==1.0.0` |
| Node budgets | 2, 4 and 8 nodes (node budgets, not byte budgets) |
| Stability floor | mean pairwise ARI >= 9/10 on every graph |
| Minimum budgets with a recall gain | 1 |
| Modularity margin | 0 |
| Graphs | the S18 task graph (frozen export), `solidus` and `spree` (from the S17 bundle, by sha256, never copied) |

## What is measured

**Projection.** Both partitioners receive the same canonical projected graph (`atlas-community-projection.v1`): undirected, unweighted, self-loops dropped, multi-edges collapsed, nodes sorted by (path, line, name), isolated nodes retained. Each run is a fresh child process; it reports the digest of the very objects it handed to its partitioner (the LPA adjacency as sorted pairs, or the exact edge tuples given to `igraph.Graph`), and the verifier requires that digest to equal the graph's projection digest.

**Quality**, exact `Fraction` arithmetic quantised to six places (round half even): modularity, coverage, community count, singleton count, size max, size median and size p90 (nearest rank), disconnected communities and their share.

**Stability**, exact adjusted Rand index (pair counting): the 45 pairwise values between the ten Leiden seeds (minimum, mean, identical pairs, distinct partitions) and the agreement of each seed with LPA. LPA has no seed (`deterministic_no_seed`).

**Usefulness** (`offline_community_expansion_proxy.v1`, task graph only): for each of the four frozen S23 tasks the seeds are the nodes named by the task terms (`exact_name`), else the nodes of any file that contains the task text (`lexical_file`), else none. The expansion list is the seeds, then each seed's community members in ascending order. Gold is the set of nodes whose line starts inside a frozen useful byte span of the S18 evidence. Recall and precision are micro sums over the four tasks at each node budget.

Seeding restates the S17 tier semantics offline and does not call `graph_query retrieve`; lexical seeding here is a case-sensitive whole-file substring, which differs from the full-text tier. The proxy measures node-level overlap, not agent success. `solidus` and `spree` have no gold labels and contribute quality and stability only.

**Telemetry**, elapsed nanoseconds around the partitioner call and peak resident memory of the fresh child, is recorded in `telemetry.json`. It is not canonical: it never enters a canonical digest or the decision, and the verifier checks its shape and its binding only.

## Decision rule

`atlas-community-decision.v1` maps the recomputed values to one of `retain_lpa`, `leiden_candidate_for_review`, `inconclusive`, `skipped_missing_provider`.

1. Provider missing: `skipped_missing_provider` with the reason `provider_missing`.
2. No task evidence, or every Leiden seed partition equals the LPA partition on the task graph: `inconclusive` (`no_task_evidence`, `task_graph_partitions_identical`).
3. Otherwise any failed condition gives `retain_lpa`, with the reasons in this fixed order: `usefulness_regression`, `no_usefulness_gain`, `stability_below_floor`, `modularity_below_lpa`.
4. Otherwise `leiden_candidate_for_review`.

Modularity alone never selects an algorithm: with modularity alone, no candidate can be produced while the usefulness or stability conditions fail. A recall tie is not a gain, and a null (no gold) compared with a non-null value counts as worse. A gain at a budget needs every seed strictly above LPA at that budget.

## Skipped state

The provider is detected at call time (`find_spec`, `import_module`, `importlib.metadata`). When `leidenalg` or `igraph` is absent the run is recorded as `skipped_missing_provider`, exit 0, with the LPA rows retained and no Leiden rows. `--require-provider` turns that into `provider_required_missing` (exit 1). A different installed version is the typed rejection `provider_version_mismatch`; a missing distribution record (for example only the legacy `python-igraph`) is `provider_import_failed`. To make sure an installed provider stays invisible, run the runner with `python -S`.

## Command reference

`python scripts/experiment-leiden-vs-lpa.py run --manifest M --manifest-sha256 HEX --out DIR [--repo-root R] [--require-provider]`. The output directory must not exist, its parent must exist and it must not lie under `mcp-server/`. Results are written to a temporary directory next to `--out` and renamed only after the runner has verified them; on any failure no output directory remains. `measure-one` is an internal subcommand used for each fresh child; it is not an operator interface. On success stdout is `atlas-community-run-summary.v1`.

`python scripts/verify-community-experiment.py --results DIR [--manifest M] [--manifest-sha256 HEX] [--repo-root R] [--max-artifact-bytes N]`. The defaults are the production manifest and its frozen digest. `--max-artifact-bytes` defaults to 67108864 (minimum 1, maximum 268435456). On success stdout is `atlas-community-verification.v1`.

| Case | Exit | stderr |
| --- | --- | --- |
| Success, complete or skipped | 0 | nothing |
| Runner rejection | 1 | `Community experiment rejected: <reason>[: <detail>]` |
| Verifier rejection | 1 | `Community verification rejected: <reason>[: <detail>]` |
| Usage | 2 | `Usage error: <name>` |

`<detail>` is only a graph id, a run id or an input id. Closed vocabularies:

- Runner rejections: `manifest_unreadable`, `manifest_invalid`, `manifest_digest_mismatch`, `manifest_constants_mismatch`, `provider_pin_invalid`, `input_unreadable`, `input_too_large`, `input_hash_mismatch`, `task_anchor_mismatch`, `graph_invalid`, `graph_too_large`, `provider_required_missing`, `provider_import_failed`, `provider_version_mismatch`, `provider_changed_during_run`, `measurement_failed`, `run_timeout`, `partition_invalid`, `self_check_failed`, `output_write_failed`.
- Verifier rejections: `manifest_unreadable`, `manifest_invalid`, `manifest_digest_mismatch`, `manifest_constants_mismatch`, `provider_pin_invalid`, `input_unreadable`, `input_too_large`, `input_hash_mismatch`, `task_anchor_mismatch`, `graph_invalid`, `graph_too_large`, `artifact_unreadable`, `artifact_too_large`, `artifact_invalid_json`, `artifact_schema_invalid`, `canonical_digest_mismatch`, `partitions_digest_mismatch`, `manifest_reference_mismatch`, `projection_mismatch`, `run_set_mismatch`, `partition_invalid_type`, `partition_node_unknown`, `partition_node_duplicated`, `partition_node_omitted`, `partition_noncanonical`, `partition_node_table_mismatch`, `lpa_rerun_mismatch`, `metric_mismatch`, `usefulness_mismatch`, `decision_mismatch`, `skipped_state_inconsistent`, `provider_record_mismatch`, `telemetry_invalid`.
- Runner usage names: `command`, `manifest`, `manifest_sha256`, `out`, `out_exists`, `out_inside_mcp_server`, `repo_root`, `arguments`.
- Verifier usage names: `results`, `manifest`, `manifest_sha256`, `repo_root`, `max_artifact_bytes`, `arguments`.
- Statuses: `complete`, `skipped_missing_provider`. Outcomes: `retain_lpa`, `leiden_candidate_for_review`, `inconclusive`, `skipped_missing_provider`. Reasons: `provider_missing`, `no_task_evidence`, `task_graph_partitions_identical`, `usefulness_regression`, `no_usefulness_gain`, `stability_below_floor`, `modularity_below_lpa`.
- Seed tiers: `exact_name`, `lexical_file`, `none`. Provider kinds: `atlas_lpa`, `real`, `fixture`.

Artifacts are strict JSON: a duplicate key, `NaN`, `Infinity`, a float, a byte order mark or invalid UTF-8 in a retained artifact is reported as `artifact_invalid_json`, so a float member of a partition reaches the command line as `artifact_invalid_json`, not as `partition_invalid_type`. Partition validation (`partition_invalid_type`, `partition_node_unknown`, `partition_node_duplicated`, `partition_node_omitted`, `partition_noncanonical`, `partition_node_table_mismatch`) applies to a well-formed document.

Formats: `atlas-community-experiment-manifest.v1`, `atlas-community-graph.v1`, `atlas-community-partitions.v1`, `atlas-community-experiment-result.v1`, `atlas-community-telemetry.v1`, `atlas-community-run-summary.v1`, `atlas-community-verification.v1`.

## Limitations

- `task_graph_is_small_and_may_not_discriminate`
- `usefulness_is_an_offline_node_level_proxy_not_agent_success`
- `graph_only_repositories_have_no_gold_labels`
- `leiden_results_are_pinned_to_provider_versions_and_platform`
- `fixture_provider_results_are_not_leiden_measurements`
- `timing_and_memory_are_telemetry_not_canonical`
- `modularity_alone_never_selects_an_algorithm`
- `node_budgets_not_byte_budgets`
- `production_default_remains_label_propagation`
- `deployment_selection_requires_a_separately_reviewed_decision`
- `seeding_restates_the_s17_tiers_offline_and_does_not_call_graph_query_retrieve`
- `lexical_seeding_is_a_case_sensitive_whole_file_substring`

The fixture provider used by the tests is a real deterministic partitioner placed on a test child's `PYTHONPATH`; it carries the version marker `+atlas.fixture`, results made with it are tagged `provider_kind: fixture`, and they are never Leiden measurements. The verifier pins `label_propagation.py` by sha256, so a later edit of that file makes the committed evidence fail verification on purpose; a later slice must measure again or record the new digest.

## Committed real run

The directory `.spectra/evidence/aci-s36/real-run/` holds one measured run of the production manifest: `leidenalg 0.12.0` and `igraph 1.0.0` (wheels installed offline inside a throwaway `python:3.12-slim` container, aarch64), run twice in that container with byte-identical results. The verifier passes on it in an image without the provider, with `production_manifest: true`.

- Outcome: `inconclusive`
- Reasons: `task_graph_partitions_identical`
- Manifest digest: `3239ec59d9fce113a115ff4b27c42bfbf06715060e1b2cb7e7de8e2e435a2beb`
- Canonical result digest: `6c804b4c5870da1bf922c228a5f488614d605935f6f465e7ff1d09763ba07a67`
- Partitions digest: `4a92fb9e7972e4891794e13b432442daed1cf5b866c66a963bf98239d82f909e`

| Condition | Value |
| --- | --- |
| `modularity_not_below_lpa` | `true` |
| `provider_available` | `true` |
| `recall_gain_budgets` | `0` |
| `stability_floor_met` | `true` |
| `task_evidence_present` | `true` |
| `task_graph_partitions_differ` | `false` |
| `usefulness_no_worse_all_budgets` | `true` |

The S18 task graph has 7 nodes and 4 edges in three connected components of at most 3 nodes each, and both algorithms return the same partition there (the preregistered expectation, stated before the run). Usefulness on the task graph is therefore identical for every Leiden seed and for LPA:

| Node budget | Hits | Selected | Gold | Recall | Precision |
| --- | --- | --- | --- | --- | --- |
| 2 | 7 | 8 | 8 | 0.875000 | 0.875000 |
| 4 | 8 | 9 | 8 | 1.000000 | 0.888889 |
| 8 | 8 | 9 | 8 | 1.000000 | 0.888889 |

### Graph `s18-task-graph` (n=7, m=4, isolated nodes=0)

| Run | Communities | Modularity | Coverage | Singletons | Size max | Size median | Size p90 | Disconnected |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `lpa` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-0` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-1` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-2` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-3` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-4` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-5` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-6` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-7` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-8` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |
| `leiden-seed-9` | 3 | 0.625000 | 1.000000 | 0 | 3 | 2 | 3 | 0 |

Stability: mean pairwise ARI 1.000000, minimum 1.000000, identical pairs 45 of 45, distinct Leiden partitions 1. ARI of each seed (0 to 9) against LPA: 1.000000, 1.000000, 1.000000, 1.000000, 1.000000, 1.000000, 1.000000, 1.000000, 1.000000, 1.000000.

### Graph `solidus` (n=1883, m=1782, isolated nodes=2)

| Run | Communities | Modularity | Coverage | Singletons | Size max | Size median | Size p90 | Disconnected |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `lpa` | 394 | 0.928256 | 0.949495 | 2 | 82 | 2 | 8 | 0 |
| `leiden-seed-0` | 355 | 0.960641 | 0.985410 | 2 | 79 | 2 | 8 | 0 |
| `leiden-seed-1` | 354 | 0.960548 | 0.987093 | 2 | 88 | 2 | 8 | 0 |
| `leiden-seed-2` | 354 | 0.960481 | 0.986532 | 2 | 86 | 2 | 8 | 0 |
| `leiden-seed-3` | 354 | 0.960630 | 0.985971 | 2 | 86 | 2 | 8 | 0 |
| `leiden-seed-4` | 355 | 0.960525 | 0.984848 | 2 | 79 | 2 | 8 | 0 |
| `leiden-seed-5` | 355 | 0.960641 | 0.985410 | 2 | 79 | 2 | 8 | 0 |
| `leiden-seed-6` | 355 | 0.960525 | 0.984848 | 2 | 79 | 2 | 8 | 0 |
| `leiden-seed-7` | 355 | 0.960525 | 0.984848 | 2 | 79 | 2 | 8 | 0 |
| `leiden-seed-8` | 355 | 0.960493 | 0.985971 | 2 | 80 | 2 | 8 | 0 |
| `leiden-seed-9` | 354 | 0.960548 | 0.987093 | 2 | 88 | 2 | 8 | 0 |

Stability: mean pairwise ARI 0.957187, minimum 0.919833, identical pairs 5 of 45, distinct Leiden partitions 6. ARI of each seed (0 to 9) against LPA: 0.744241, 0.713864, 0.728652, 0.737600, 0.754735, 0.744241, 0.754735, 0.754735, 0.733681, 0.713864.

### Graph `spree` (n=5474, m=5947, isolated nodes=9)

| Run | Communities | Modularity | Coverage | Singletons | Size max | Size median | Size p90 | Disconnected |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `lpa` | 964 | 0.872670 | 0.880444 | 9 | 197 | 3 | 10 | 0 |
| `leiden-seed-0` | 685 | 0.959237 | 0.983353 | 9 | 354 | 3 | 13 | 0 |
| `leiden-seed-1` | 684 | 0.959267 | 0.983857 | 9 | 366 | 3 | 12 | 0 |
| `leiden-seed-2` | 685 | 0.959314 | 0.982680 | 9 | 366 | 3 | 13 | 0 |
| `leiden-seed-3` | 685 | 0.959236 | 0.982848 | 9 | 372 | 3 | 12 | 0 |
| `leiden-seed-4` | 684 | 0.959214 | 0.982680 | 9 | 366 | 3 | 12 | 0 |
| `leiden-seed-5` | 685 | 0.959314 | 0.982680 | 9 | 366 | 3 | 13 | 0 |
| `leiden-seed-6` | 685 | 0.959268 | 0.983185 | 9 | 354 | 3 | 13 | 0 |
| `leiden-seed-7` | 685 | 0.959289 | 0.983857 | 9 | 372 | 3 | 12 | 0 |
| `leiden-seed-8` | 682 | 0.959319 | 0.983857 | 9 | 360 | 3 | 12 | 0 |
| `leiden-seed-9` | 684 | 0.959119 | 0.983353 | 9 | 354 | 3 | 12 | 0 |

Stability: mean pairwise ARI 0.966038, minimum 0.940964, identical pairs 1 of 45, distinct Leiden partitions 9. ARI of each seed (0 to 9) against LPA: 0.445988, 0.436768, 0.449114, 0.443994, 0.448013, 0.449114, 0.448896, 0.436978, 0.436225, 0.444170.

### Telemetry (not canonical)

Elapsed time around the partitioner call and the peak resident memory of each fresh child, as recorded in `telemetry.json`. These values are not canonical and are not part of the decision; they depend on the machine and the load.

| Graph | Run | Elapsed ns | Peak RSS bytes after the call |
| --- | --- | --- | --- |
| `s18-task-graph` | `lpa` | 18333 | 42762240 |
| `s18-task-graph` | `leiden-seed-0` | 268125 | 42762240 |
| `s18-task-graph` | `leiden-seed-9` | 237417 | 42762240 |
| `solidus` | `lpa` | 6925792 | 42762240 |
| `solidus` | `leiden-seed-0` | 66364625 | 42762240 |
| `solidus` | `leiden-seed-9` | 63863084 | 43155456 |
| `spree` | `lpa` | 24680708 | 43679744 |
| `spree` | `leiden-seed-0` | 314533250 | 43810816 |
| `spree` | `leiden-seed-9` | 325350292 | 44990464 |

Re-running with another provider version, another platform or after any change to the inputs is a different experiment: it needs its own manifest and digest.
