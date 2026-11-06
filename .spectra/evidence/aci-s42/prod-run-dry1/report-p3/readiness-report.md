# Shared-operation readiness report (ACI-S42)

Verdict: blocked

Failed checks: capacity.limits_held, capacity.saturation_typed, records.complete

## Checks

| check | state |
| --- | --- |
| records.complete | missing |
| environment.sdk_pinned | pass |
| isolation.unauthorized_zero | pass |
| isolation.positive_controls | pass |
| isolation.typed_denials | pass |
| isolation.concurrency | pass |
| isolation.ledger_teeth | pass |
| interrupt.all_recovered | pass |
| interrupt.teeth_detected | pass |
| retention.lease_respected | pass |
| compat.cells_recorded | pass |
| capacity.limits_held | missing |
| capacity.saturation_typed | missing |

## Deployment model

- clients: raw HTTP/1.1 and stdio JSON-RPC lines, plus the mcp SDK client
- keys: RS256 generated per run, offline JWKS file
- listen: 127.0.0.1 port 0 (ephemeral)
- policy_reload_seconds: 0
- principals: 4
- process: one loopback serve-shared process per phase
- repositories: a and b, plus c as the before snapshot of a at the same path
- storage: container-local tmp behind the lock canary
- tls_reverse_proxy: none

## What this evidence does and does not show

- H1: Black-box evidence: the ledger detects planted canaries and typed outcomes in responses and server log lines, not internal reads.
- H2: Unauthorized evidence access is zero within the enumerated cases; this is not a claim that no leakage exists.
- H3: Not tested: timing side channels, the shared address space of the single server process, and a TLS-terminating reverse proxy.
- H4: Inherited and not re-run: the S40 in-process Observer suite (mcp-server/tests/slices/test_s40_contract.py).
- The word isolation in this report means the enumerated cases of the case table and nothing wider.
- No real host (Claude Code, GitHub Copilot, Cursor) was tested; each is listed untested.

## Isolation (evidence access ledger)

Rows: 40 (22 allow, 18 deny) over 4 principals and routes a, b, c.
Unauthorized evidence access, within the enumerated cases: main 0, revocation 0; over-granted teeth run 1 (must be at least 1).
Concurrency: client intervals overlapped and the server gate peak was at least 3 (the gate counts requests, not principals): server_peak_ge_3 = true; holds_spanned (recorder-attested, not recomputed here) = true.

| class | deny cases | controls ok | unauthorized |
| --- | --- | --- | --- |
| control | 0 | 22 | 0 |
| ungranted_repo | 2 | 2 | 0 |
| ungranted_tool | 2 | 2 | 0 |
| disabled_tool | 2 | 2 | 0 |
| impact_before_ungranted | 1 | 1 | 0 |
| cross_principal_cursor | 1 | 1 | 0 |
| cross_principal_resource | 1 | 1 | 0 |
| cross_repo_resource | 1 | 1 | 0 |
| cross_repo_argument | 1 | 1 | 0 |
| path_escape | 2 | 2 | 0 |
| token_missing | 1 | 1 | 0 |
| token_invalid | 1 | 1 | 0 |
| token_wrong_audience | 1 | 1 | 0 |
| revoked_grant | 2 | 2 | 0 |

## Interruption matrix

| case | injection | tree | expect valid | valid | facts |
| --- | --- | --- | --- | --- | --- |
| cancel_marker | {"kind":"cancel_marker","seam":"staged"} | T1 | true | true | {"cancel":"honoured","exit":4,"published":false,"state":"cancelled"} |
| cancel_sigterm | {"kind":"cancel_sigterm","seam":"staged"} | T1 | true | true | {"cancel":"honoured","exit":4,"published":false,"state":"cancelled"} |
| kill_checkpoint | {"at":2,"kind":"kill","seam":"checkpoint"} | T2 | true | true | {"interrupted":true,"interrupted_state":"running","killed":true,"outcome":"built","resumed_from":2,"state":"completed"} |
| kill_mid_publish | {"kind":"kill","seam":"mid_publish"} | T2 | true | true | {"interrupted":true,"interrupted_state":"running","killed":true,"outcome":"built","state":"completed"} |
| kill_post_replace | {"kind":"kill","seam":"post_replace"} | T2 | true | true | {"interrupted":true,"interrupted_state":"published","killed":true,"outcome":"recovered","state":"completed"} |
| kill_pre_receipt | {"kind":"kill","seam":"pre_receipt"} | T2 | true | true | {"interrupted":true,"interrupted_state":"captured","killed":true,"outcome":"recovered","state":"completed"} |
| gc_vs_lease | {"kind":"gc_vs_lease"} | T2 | true | true | {"acquire_code":"EXPIRED_REFERENCE","acquire_exit":3,"holder_killed":true,"leased_retained":true,"retired_after_release":true} |
| gc_vs_build | {"kind":"gc_vs_build","seam":"staged"} | T2 | true | true | {"gc_code":"WRITER_BUSY","gc_exit":75,"state":"completed"} |
| teeth_replace_by_copy | {"kind":"teeth_replace_by_copy"} | T2 | false | false | {"corrupted_farm_acquire_refused":true} |
| teeth_wrong_expectation | {"kind":"teeth_wrong_expectation"} | T2 | false | false | {"validated_against":"T1"} |

## Tested client and transport combinations

| cell | client | transport | requested | negotiated | expected | shared | call |
| --- | --- | --- | --- | --- | --- | --- | --- |
| raw.shared-http.2024-11-05 | raw-jsonrpc | shared-http | 2024-11-05 | 2024-11-05 | supported | true | ok |
| raw.shared-http.2025-03-26 | raw-jsonrpc | shared-http | 2025-03-26 | 2025-03-26 | supported | true | ok |
| raw.shared-http.2025-06-18 | raw-jsonrpc | shared-http | 2025-06-18 | 2025-06-18 | supported | true | ok |
| raw.shared-http.2025-11-25 | raw-jsonrpc | shared-http | 2025-11-25 | 2025-11-25 | supported | true | ok |
| raw.shared-http.2099-01-01 | raw-jsonrpc | shared-http | 2099-01-01 | 2025-11-25 | counter_offer | true | ok |
| raw.http.2025-11-25 | raw-jsonrpc | http | 2025-11-25 | 2025-11-25 | supported | false | ok |
| raw.stdio.2025-11-25 | raw-jsonrpc | stdio | 2025-11-25 | 2025-11-25 | supported | false | ok |
| sdk.stdio.sdk_default | mcp-sdk | stdio | sdk_default | 2025-11-25 | supported | false | ok |
| sdk.http.sdk_default | mcp-sdk | http | sdk_default | 2025-11-25 | supported | false | ok |
| sdk.shared-http.sdk_default | mcp-sdk | shared-http | sdk_default | 2025-11-25 | supported | true | ok |

Untested cells:

- none

Hosts:

- claude-code: untested (no_real_host_run)
- github-copilot: untested (no_real_host_run)
- cursor: untested (no_real_host_run)

## Capacity workload record

No workload record.

## Unresolved limits

- black_box_ledger_detects_planted_canaries_and_typed_outcomes_only
- zero_is_within_the_enumerated_cases_not_a_no_leakage_claim
- stderr_tool_call_corroboration_is_necessary_not_sufficient
- timing_side_channels_untested
- shared_address_space_untested
- tls_reverse_proxy_untested
- same_path_pair_exposes_the_live_tree_by_design
- real_hosts_untested_claude_code_copilot_cursor
- s40_in_process_observer_suite_inherited_not_rerun
- gc_seam_kills_untested_only_lease_writer_busy_and_expiry
- single_host_loopback_workload_not_a_load_test
- resource_metrics_linux_proc_with_source_labels
- no_deployment_credentials_or_dns_performed
- fresh_runs_compared_by_invariants_not_by_digest

## Inputs

| input | sha256 |
| --- | --- |
| compat.raw.http.2025-11-25 | 9ee050906672b5d30d7d1cad18e5f16917f59253a8af785e23a4b0bfc5322b26 |
| compat.raw.shared-http.2024-11-05 | b493cb67444f0220702c393b15a1a0954a0b38c0da5401f6f8d33863aad2b9ee |
| compat.raw.shared-http.2025-03-26 | fd96c947e242a20974a80956d49b336d529c6e96e71dcf02029dfc1081d1e269 |
| compat.raw.shared-http.2025-06-18 | b319aeb368e282a8be47d207b7cdd6b0b80d95c38e4437e2bec5b9b1d10cbb60 |
| compat.raw.shared-http.2025-11-25 | e8e1628e5b62fcb19d7c21ef875b237fc7f097cd1caf7652466da91274da39e2 |
| compat.raw.shared-http.2099-01-01 | b9eeaa331f637f422a37adc1b990cba9e707e63e388356b779c44d54517af5a9 |
| compat.raw.stdio.2025-11-25 | 44f85755ddd5a5bec86788c4ad2ee765ae94facbc3f3d7de5616709ee3d17b27 |
| compat.sdk.http.sdk_default | e03c56501ed71f6697c16819afd527461f308f5fad556efc0dbfb1cf567d4756 |
| compat.sdk.shared-http.sdk_default | e4d806a6e8768dc8534b9ecd86f8156c0eee053408509cba9817fbb04f32366d |
| compat.sdk.stdio.sdk_default | dee13bd734cadec185314f0a2b9eb6ff3fd303902af7a0100d6f20ed9e3d20b4 |
| environment.main | aa935d55c1981d1dbbc5efd53a9ea7841059748dabaffe5b1937a6a09c130bf2 |
| interrupt.cancel_marker | b4fa033a4adf88df966459ddb9128bb36cdb967e5abffd4fd4ee93cd0ce1ffca |
| interrupt.cancel_sigterm | f0d15fc4805996c7227738cd8a30ec07bf6262ba8ffb7d6858869cd1360f9712 |
| interrupt.gc_vs_build | 4e2dcbe1a3296a1b48ec73d2d5e2cf882d1fa330b908ff0ad36b17ee6e31cb63 |
| interrupt.gc_vs_lease | 5f24b7ff276e1cbce43a30dfe8a08c47466ea6af4cfcdfe9a7b0970aa9190bef |
| interrupt.kill_checkpoint | 1e3718f9bef51d303e5e08a1b5cf6942a93531ff75bcf9fe0a1c2472518cd906 |
| interrupt.kill_mid_publish | 9ec2e31f98e2d80653fea793bd80f06b048af9a25cfbe6cfe57dc742144349b2 |
| interrupt.kill_post_replace | eed44c4060f6e072338c579899ca444e02fa9ab3627aaa7351bbeef48378f905 |
| interrupt.kill_pre_receipt | 61529ad8eee1219f15ed5567b1b9b987f1f630716c4db06810e438390542f992 |
| interrupt.teeth_replace_by_copy | 39c01ee06fd81db11609642c031c016091615749fe88dd6d94bd379b321bc40c |
| interrupt.teeth_wrong_expectation | 20abd88cb03274532a6298c9ac8a6c41aeb26bf571f3b1be5794f12b39ca5f2c |
| isolation.main | 3fd547e306389abc11b3017d2817d5489ae5c38b1578ea81658d27cb95d9b166 |
| isolation.revocation | c953bf376dd838855943bf00450c6e4334819b2675486530772f0ebc2dfcef3c |
| isolation.teeth | 5c7a451058358a8bc84fe066a33550c21773f9e5ecdfb8abfaa9a2c52bbe3cf8 |

## Canonical digest

canonical_sha256: 5c057472791ff64d793f5c34c761d0fdc830cff9150d58ff39ec1e45c8769319

