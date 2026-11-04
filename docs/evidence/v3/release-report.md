# ATLAS ACI v3 evidence: release report

- manifest_sha256: `3b901b99607d3db95d02a79794792cc8fb0df142901dc35fb502a1fa8e49b1ce`
- task_input_sha256: `236c0def27a416dd93a411a4e40d2666510320c4d9f9cfff08f29a1145cc8fe6`
- canonical_sha256: `59b6e81d26979265f76bb52533a1b78848901bf177eb4e658e3f42506dcd3a4e`
- corpus_commit (all arms): `c1ac2ed0b8d13af001822cc27d605c3f6490ffc1`

Rendered from the canonical section only; replay with `v3_aggregate.py`.

## Gates

- cost: pass
- live_agent_evaluation: incomplete
- paired_inputs: pass
- release_integrity: pass
- span_matrix: pass

## Useful-span matrix (per arm and budget; micro-sum over tasks)

| arm | budget (byte_quarter tokens) | max_bytes | status | calls | errors | result_bytes (normalised) | useful_inline | useful_reference | unique_inline | inline_recall | reference_recall | inline_precision |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V0.legacy | 2000 | 8000 | measured | 10 | 0 | 2785 | 243 | 243 | 354 | 0.327493 | 0.327493 | 0.686441 |
| V0.legacy | 8000 | 32000 | measured | 10 | 0 | 2785 | 243 | 243 | 354 | 0.327493 | 0.327493 | 0.686441 |
| V0.legacy | 16000 | 64000 | measured | 10 | 0 | 2785 | 243 | 243 | 354 | 0.327493 | 0.327493 | 0.686441 |
| V1.legacy | 2000 | 8000 | measured | 10 | 0 | 4154 | 243 | 243 | 354 | 0.327493 | 0.327493 | 0.686441 |
| V1.legacy | 8000 | 32000 | measured | 10 | 0 | 4154 | 243 | 243 | 354 | 0.327493 | 0.327493 | 0.686441 |
| V1.legacy | 16000 | 64000 | measured | 10 | 0 | 4154 | 243 | 243 | 354 | 0.327493 | 0.327493 | 0.686441 |
| V1.context.references | 2000 | 8000 | measured | 4 | 1 | 20709 | 0 | 0 | 0 | 0.000000 | 0.000000 | n/a |
| V1.context.references | 8000 | 32000 | measured | 4 | 0 | 88544 | 0 | 572 | 0 | 0.000000 | 0.770889 | n/a |
| V1.context.references | 16000 | 64000 | measured | 4 | 0 | 113542 | 0 | 742 | 0 | 0.000000 | 1.000000 | n/a |
| V1.context.inline | 2000 | 8000 | measured | 4 | 1 | 20771 | 0 | 0 | 0 | 0.000000 | 0.000000 | n/a |
| V1.context.inline | 8000 | 32000 | measured | 4 | 0 | 97154 | 572 | 572 | 3879 | 0.770889 | 0.770889 | 0.147461 |
| V1.context.inline | 16000 | 64000 | measured | 4 | 0 | 129834 | 742 | 742 | 7395 | 1.000000 | 1.000000 | 0.100338 |
| B.search_read | 2000 | 8000 | measured | 4 | 0 | 1080 | 339 | 339 | 339 | 0.456873 | 0.456873 | 1.000000 |
| B.search_read | 8000 | 32000 | measured | 9 | 0 | 43659 | 339 | 339 | 3572 | 0.456873 | 0.456873 | 0.094905 |
| B.search_read | 16000 | 64000 | measured | 10 | 0 | 47065 | 339 | 339 | 6805 | 0.456873 | 0.456873 | 0.049816 |
| V0.context.references | 2000 | 8000 | not_applicable (op_unavailable_pre_v3) |  |  |  |  |  |  |  |  |  |
| V0.context.references | 8000 | 32000 | not_applicable (op_unavailable_pre_v3) |  |  |  |  |  |  |  |  |  |
| V0.context.references | 16000 | 64000 | not_applicable (op_unavailable_pre_v3) |  |  |  |  |  |  |  |  |  |
| V0.context.inline | 2000 | 8000 | not_applicable (op_unavailable_pre_v3) |  |  |  |  |  |  |  |  |  |
| V0.context.inline | 8000 | 32000 | not_applicable (op_unavailable_pre_v3) |  |  |  |  |  |  |  |  |  |
| V0.context.inline | 16000 | 64000 | not_applicable (op_unavailable_pre_v3) |  |  |  |  |  |  |  |  |  |

## Paired differences (integer, left minus right)

| left | right | budget | calls | errors | result_bytes_repo_normalized | byte_quarter_tokens | useful_inline_bytes | useful_reference_bytes | unique_inline_bytes | unique_reference_bytes | redundant_inline_bytes | redundant_reference_bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V1.legacy | V0.legacy | 2000 | 0 | 0 | 1369 | 342 | 0 | 0 | 0 | 0 | 0 | 0 |
| V1.legacy | V0.legacy | 8000 | 0 | 0 | 1369 | 342 | 0 | 0 | 0 | 0 | 0 | 0 |
| V1.legacy | V0.legacy | 16000 | 0 | 0 | 1369 | 342 | 0 | 0 | 0 | 0 | 0 | 0 |
| V1.context.inline | B.search_read | 2000 | 0 | 1 | 19691 | 4923 | -339 | -339 | -339 | -339 | 0 | -51 |
| V1.context.inline | B.search_read | 8000 | -5 | 0 | 53495 | 13373 | 233 | 233 | 307 | -2896 | 180 | -637268 |
| V1.context.inline | B.search_read | 16000 | -6 | 0 | 82769 | 20692 | 403 | 403 | 590 | 590 | 402 | -640249 |
| V1.context.references | V1.legacy | 2000 | -6 | 1 | 16555 | 4139 | -243 | -243 | -354 | -354 | 0 | -354 |
| V1.context.references | V1.legacy | 8000 | -6 | 0 | 84390 | 21097 | -243 | 329 | -354 | 3525 | 0 | -174 |
| V1.context.references | V1.legacy | 16000 | -6 | 0 | 109388 | 27346 | -243 | 499 | -354 | 7041 | 0 | 48 |

## Per-task rows

| arm | budget | task | calls | errors | result_bytes (normalised) | useful_inline | useful_reference | useful_label |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V0.legacy | 2000 | payment | 3 | 0 | 1209 | 101 | 101 | 270 |
| V0.legacy | 2000 | request | 2 | 0 | 632 | 73 | 73 | 207 |
| V0.legacy | 2000 | unicode | 2 | 0 | 629 | 69 | 69 | 132 |
| V0.legacy | 2000 | phrase | 3 | 0 | 315 | 0 | 0 | 133 |
| V0.legacy | 8000 | payment | 3 | 0 | 1209 | 101 | 101 | 270 |
| V0.legacy | 8000 | request | 2 | 0 | 632 | 73 | 73 | 207 |
| V0.legacy | 8000 | unicode | 2 | 0 | 629 | 69 | 69 | 132 |
| V0.legacy | 8000 | phrase | 3 | 0 | 315 | 0 | 0 | 133 |
| V0.legacy | 16000 | payment | 3 | 0 | 1209 | 101 | 101 | 270 |
| V0.legacy | 16000 | request | 2 | 0 | 632 | 73 | 73 | 207 |
| V0.legacy | 16000 | unicode | 2 | 0 | 629 | 69 | 69 | 132 |
| V0.legacy | 16000 | phrase | 3 | 0 | 315 | 0 | 0 | 133 |
| V1.legacy | 2000 | payment | 3 | 0 | 1527 | 101 | 101 | 270 |
| V1.legacy | 2000 | request | 2 | 0 | 880 | 73 | 73 | 207 |
| V1.legacy | 2000 | unicode | 2 | 0 | 874 | 69 | 69 | 132 |
| V1.legacy | 2000 | phrase | 3 | 0 | 873 | 0 | 0 | 133 |
| V1.legacy | 8000 | payment | 3 | 0 | 1527 | 101 | 101 | 270 |
| V1.legacy | 8000 | request | 2 | 0 | 880 | 73 | 73 | 207 |
| V1.legacy | 8000 | unicode | 2 | 0 | 874 | 69 | 69 | 132 |
| V1.legacy | 8000 | phrase | 3 | 0 | 873 | 0 | 0 | 133 |
| V1.legacy | 16000 | payment | 3 | 0 | 1527 | 101 | 101 | 270 |
| V1.legacy | 16000 | request | 2 | 0 | 880 | 73 | 73 | 207 |
| V1.legacy | 16000 | unicode | 2 | 0 | 874 | 69 | 69 | 132 |
| V1.legacy | 16000 | phrase | 3 | 0 | 873 | 0 | 0 | 133 |
| V1.context.references | 2000 | payment | 1 | 1 | 777 | 0 | 0 | 270 |
| V1.context.references | 2000 | request | 1 | 0 | 7158 | 0 | 0 | 207 |
| V1.context.references | 2000 | unicode | 1 | 0 | 7156 | 0 | 0 | 132 |
| V1.context.references | 2000 | phrase | 1 | 0 | 5618 | 0 | 0 | 133 |
| V1.context.references | 8000 | payment | 1 | 0 | 24826 | 0 | 100 | 270 |
| V1.context.references | 8000 | request | 1 | 0 | 26110 | 0 | 207 | 207 |
| V1.context.references | 8000 | unicode | 1 | 0 | 26100 | 0 | 132 | 132 |
| V1.context.references | 8000 | phrase | 1 | 0 | 11508 | 0 | 133 | 133 |
| V1.context.references | 16000 | payment | 1 | 0 | 49824 | 0 | 270 | 270 |
| V1.context.references | 16000 | request | 1 | 0 | 26110 | 0 | 207 | 207 |
| V1.context.references | 16000 | unicode | 1 | 0 | 26100 | 0 | 132 | 132 |
| V1.context.references | 16000 | phrase | 1 | 0 | 11508 | 0 | 133 | 133 |
| V1.context.inline | 2000 | payment | 1 | 1 | 777 | 0 | 0 | 270 |
| V1.context.inline | 2000 | request | 1 | 0 | 7178 | 0 | 0 | 207 |
| V1.context.inline | 2000 | unicode | 1 | 0 | 7176 | 0 | 0 | 132 |
| V1.context.inline | 2000 | phrase | 1 | 0 | 5640 | 0 | 0 | 133 |
| V1.context.inline | 8000 | payment | 1 | 0 | 25141 | 100 | 100 | 270 |
| V1.context.inline | 8000 | request | 1 | 0 | 26835 | 207 | 207 | 207 |
| V1.context.inline | 8000 | unicode | 1 | 0 | 26670 | 132 | 132 | 132 |
| V1.context.inline | 8000 | phrase | 1 | 0 | 18508 | 133 | 133 | 133 |
| V1.context.inline | 16000 | payment | 1 | 0 | 57821 | 270 | 270 | 270 |
| V1.context.inline | 16000 | request | 1 | 0 | 26835 | 207 | 207 | 207 |
| V1.context.inline | 16000 | unicode | 1 | 0 | 26670 | 132 | 132 | 132 |
| V1.context.inline | 16000 | phrase | 1 | 0 | 18508 | 133 | 133 | 133 |
| B.search_read | 2000 | payment | 0 | 0 | 0 | 0 | 0 | 270 |
| B.search_read | 2000 | request | 2 | 0 | 583 | 207 | 207 | 207 |
| B.search_read | 2000 | unicode | 2 | 0 | 497 | 132 | 132 | 132 |
| B.search_read | 2000 | phrase | 0 | 0 | 0 | 0 | 0 | 133 |
| B.search_read | 8000 | payment | 2 | 0 | 13198 | 0 | 0 | 270 |
| B.search_read | 8000 | request | 2 | 0 | 583 | 207 | 207 | 207 |
| B.search_read | 8000 | unicode | 2 | 0 | 497 | 132 | 132 | 132 |
| B.search_read | 8000 | phrase | 3 | 0 | 29381 | 0 | 0 | 133 |
| B.search_read | 16000 | payment | 2 | 0 | 13198 | 0 | 0 | 270 |
| B.search_read | 16000 | request | 2 | 0 | 583 | 207 | 207 | 207 |
| B.search_read | 16000 | unicode | 2 | 0 | 497 | 132 | 132 | 132 |
| B.search_read | 16000 | phrase | 4 | 0 | 32787 | 0 | 0 | 133 |

## Cost

### Indexing counters (build() return values, no durations)

| category | corpus | variant | status | files_indexed | files_skipped | symbols | parser_invocations | disk_hits | misses | predecessor_reused |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| index.cold_full | s18-useful-spans | V1 | measured | 6 | 0 | 10 | 7 | 0 | 7 | 0 |
| index.cold_full | s18-useful-spans | V0 | measured | 7 | 0 | 11 |  |  |  |  |
| index.cold_full | self-source-fa75bec | V1 | measured | 23 | 0 | 201 | 23 | 0 | 23 | 0 |
| index.cold_full | self-source-fa75bec | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |
| index.cold_full | coverage-project | V1 | measured | 11 | 0 | 31 | 11 | 0 | 11 | 0 |
| index.cold_full | coverage-project | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |
| index.warm_full | s18-useful-spans | V1 | measured | 6 | 0 | 10 | 1 | 6 | 1 | 0 |
| index.warm_full | s18-useful-spans | V0 | not_applicable (pre_v3_counters_not_exposed) |  |  |  |  |  |  |  |
| index.warm_full | self-source-fa75bec | V1 | measured | 23 | 0 | 201 | 0 | 23 | 0 | 0 |
| index.warm_full | self-source-fa75bec | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |
| index.warm_full | coverage-project | V1 | measured | 11 | 0 | 31 | 0 | 11 | 0 | 0 |
| index.warm_full | coverage-project | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |
| index.incremental_noop | s18-useful-spans | V1 | measured | 0 | 6 | 0 | 1 | 0 | 1 | 6 |
| index.incremental_noop | s18-useful-spans | V0 | not_applicable (pre_v3_counters_not_exposed) |  |  |  |  |  |  |  |
| index.incremental_noop | self-source-fa75bec | V1 | measured | 0 | 23 | 0 | 0 | 0 | 0 | 23 |
| index.incremental_noop | self-source-fa75bec | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |
| index.incremental_noop | coverage-project | V1 | measured | 0 | 11 | 0 | 0 | 0 | 0 | 11 |
| index.incremental_noop | coverage-project | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |
| index.refresh_after_edit | s18-useful-spans | V1 | measured | 1 | 5 | 3 | 2 | 0 | 2 | 5 |
| index.refresh_after_edit | s18-useful-spans | V0 | not_applicable (pre_v3_counters_not_exposed) |  |  |  |  |  |  |  |
| index.refresh_after_edit | self-source-fa75bec | V1 | measured | 1 | 22 | 0 | 1 | 0 | 1 | 22 |
| index.refresh_after_edit | self-source-fa75bec | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |
| index.refresh_after_edit | coverage-project | V1 | measured | 1 | 10 | 7 | 1 | 0 | 1 | 10 |
| index.refresh_after_edit | coverage-project | V0 | unmeasured (v0_index_cost_recorded_on_s18_only) |  |  |  |  |  |  |  |

### query.retrieval

| arm | budget | calls | result_bytes (normalised) | byte_quarter_tokens |
| --- | ---: | ---: | ---: | ---: |
| V0.legacy | 2000 | 10 | 2785 | 698 |
| V0.legacy | 8000 | 10 | 2785 | 698 |
| V0.legacy | 16000 | 10 | 2785 | 698 |
| V1.legacy | 2000 | 10 | 4154 | 1040 |
| V1.legacy | 8000 | 10 | 4154 | 1040 |
| V1.legacy | 16000 | 10 | 4154 | 1040 |
| V1.context.references | 2000 | 4 | 20709 | 5179 |
| V1.context.references | 8000 | 4 | 88544 | 22137 |
| V1.context.references | 16000 | 4 | 113542 | 28386 |
| V1.context.inline | 2000 | 4 | 20771 | 5194 |
| V1.context.inline | 8000 | 4 | 97154 | 24290 |
| V1.context.inline | 16000 | 4 | 129834 | 32460 |
| B.search_read | 2000 | 4 | 1080 | 271 |
| B.search_read | 8000 | 9 | 43659 | 10917 |
| B.search_read | 16000 | 10 | 47065 | 11768 |

### query.model: unmeasured (no_operator_budget)

## Engineering outcomes

### Live (agent) outcomes

- agent_turns: unmeasured (no_operator_budget)
- coding_task_success: unmeasured (no_operator_budget)
- model_cost: unmeasured (no_operator_budget)
- model_tokens: unmeasured (no_operator_budget)

### Offline (label: offline_fixture_proxy)

- validation_plan_comparison: measured, label offline_fixture_proxy

## Release integrity (P0 regression cases)

status: pass (30/30 ids passed)

| node id | category | outcome | testcases |
| --- | --- | --- | ---: |
| tests/slices/test_s02_contract.py::test_additional_binding_constructs_fail_closed | binding | passed | 1 |
| tests/slices/test_s02_contract.py::test_binding_replay | binding | passed | 1 |
| tests/slices/test_s02_contract.py::test_cross_language_collision | binding | passed | 1 |
| tests/slices/test_s02_contract.py::test_no_cross_file_module_guess | binding | passed | 1 |
| tests/slices/test_s02_contract.py::test_owned_receiver | binding | passed | 1 |
| tests/slices/test_s02_contract.py::test_shadowed_name | binding | passed | 1 |
| tests/slices/test_s02_contract.py::test_unknown_alias | binding | passed | 1 |
| tests/slices/test_s02_contract.py::test_unrelated_receiver | binding | passed | 1 |
| tests/slices/test_s03_contract.py::test_active_request_pinned | snapshot | passed | 3 |
| tests/slices/test_s03_contract.py::test_concurrent_readers_keep_their_own_generation | snapshot | passed | 1 |
| tests/slices/test_s03_contract.py::test_consumed_buffer_hash_and_failure | snapshot | passed | 1 |
| tests/slices/test_s03_contract.py::test_failed_publish | snapshot | passed | 1 |
| tests/slices/test_s03_contract.py::test_generation_replay | snapshot | passed | 1 |
| tests/slices/test_s08_contract.py::test_active_lease_retained | snapshot | passed | 1 |
| tests/slices/test_s08_contract.py::test_snapshot_replay | snapshot | passed | 1 |
| tests/slices/test_s08_contract.py::test_snapshot_source_coherence | snapshot | passed | 1 |
| tests/slices/test_s08_integration.py::test_coherently_rehashed_foreign_binding_fails_before_exposure | snapshot | passed | 4 |
| tests/slices/test_s08_integration.py::test_prune_during_unexposed_acquisition_retries_latest | snapshot | passed | 1 |
| tests/slices/test_s08_lease_footprint.py::test_rehashed_compact_or_raw_range_contradiction | snapshot | passed | 6 |
| tests/slices/test_s09_contract.py::test_producer_change | snapshot | passed | 1 |
| tests/slices/test_s09_contract.py::test_same_stat_changed_content | snapshot | passed | 1 |
| tests/slices/test_s10_contract.py::test_call_only_reuses_nonempty_same_language_unit | binding | passed | 1 |
| tests/slices/test_s10_contract.py::test_incremental_replay | binding | passed | 1 |
| tests/slices/test_s10_contract.py::test_unknown_dependency_fallback | binding | passed | 1 |
| tests/slices/test_s10_dependencies.py::test_fatal_selected_binding_preserves_pinned_g1 | binding | passed | 1 |
| tests/slices/test_s10_dependencies.py::test_incoherent_outputs_force_full_repair | binding | passed | 4 |
| tests/slices/test_s10_dependencies.py::test_untrusted_witness_full_fallback | binding | passed | 8 |
| tests/slices/test_s12_storage.py::test_genuine_epoch6_reader_and_writer_conversion | snapshot | passed | 1 |
| tests/slices/test_s12_storage.py::test_staged_upgrade_preserves_history_and_held_source | snapshot | passed | 1 |
| tests/slices/test_s17_storage.py::test_genuine_held8_upgrade9_overlay_and_replay | snapshot | passed | 1 |

## Probes

- V0: protocol 2025-11-25, server {'name': 'atlas-aci', 'version': '1.27.0'}
- V1: protocol 2025-11-25, server {'name': 'atlas-aci', 'version': '1.27.0'}

## Host and SDK matrix

| dimension | value | status | evidence |
| --- | --- | --- | --- |
| MCP client SDK | mcp 1.27.0 (uv.lock) | tested | release recorder probe and arms; test_s18_stdio |
| Protocol (op=context only) | 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25; unknown -> negotiated | tested | test_s18_stdio.py:32-35 (CI); this evaluation negotiates one version per server (see probes) |
| Transport | stdio | tested | server.py:20; all arms |
| Transport | HTTP/SSE | unsupported | not implemented |
| CPython (client and CI) | 3.14 | tested | ci.yml:108 |
| CPython (recorder runtime) | 3.12 in the atlas-aci-dev-s23-recorder container | tested | release run (recorder producer, diagnostic) |
| CPython (server runtime) | 3.12 in the Docker python:3.12-slim image only, driven by a 3.14 host client | tested (smoke) | mcp-server/Dockerfile:55; ci.yml S06/S08 docker job |
| CPython | 3.11, 3.13 | unverified | requires-python >=3.11 only |
| OS | Linux x86_64 | tested | CI ubuntu |
| OS | macOS | tested (partial) | CI export-determinism job only |
| OS | Windows | unverified | no CI or evidence |
| Image | linux/amd64 | tested | CI docker smoke job |
| Image | linux/arm64 | documented | built by release.yml:155, not run in CI (the pre-v3 image is run on arm64 in this evaluation) |
| Hosts | Claude Code, Copilot, Cursor | documented | hosts/*.md, no automated run |
| Canary adapters | claude-code-api, copilot, cursor | unsupported | run-canaries.py:342 |
| Pre-v3 server | atlas-aci 2.0.0 @ sha256:e2542ef8d569882c560065f0bcaed9ef2a7398e7e61765e25b142a3c93ec7cf7 | tested (this eval) | V0 records |

## Known failures

- tests/slices/test_s01_contract.py::test_call_deadline_after_initialization (known_failure_uid_1000): fails under uid 1000 in the dev container; passes in CI (qualification and base-invariants.json)
- tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition (known_flaky): timing-sensitive; excluded from the P0 gate

## Limitations

- no_live_agent_evaluation_no_operator_budget
- four_labelled_tasks_742_bytes_no_generalisation
- no_thresholds_or_significance_claimed
- policies_are_deterministic_transcripts_not_agents
- byte_quarter_is_not_model_tokens
- result_bytes_client_measured_repo_root_normalized
- protocol_version_negotiated_per_server_not_controlled
- v0_limited_to_legacy_tools
- offline_validation_comparison_is_fixture_proxy
- grammar_cache_warm_in_cold_builds
- durations_diagnostic_only
- retrieve_impact_diff_ops_not_span_evaluated
- search_symbol_is_exact_name_match_so_phrase_terms_find_no_definitions
- v0_index_uses_a_prefetched_declared_grammar_cache
- search_text_match_order_is_ripgrep_thread_order_so_B_recordings_can_vary_between_runs
