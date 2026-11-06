# Shared-operation readiness report (ACI-S42)

Verdict: blocked

Failed checks: isolation.positive_controls, isolation.typed_denials, isolation.unauthorized_zero

## Checks

| check | state |
| --- | --- |
| records.complete | pass |
| environment.sdk_pinned | pass |
| isolation.unauthorized_zero | fail |
| isolation.positive_controls | fail |
| isolation.typed_denials | fail |
| isolation.concurrency | pass |
| isolation.ledger_teeth | pass |
| interrupt.all_recovered | pass |
| interrupt.teeth_detected | pass |
| retention.lease_respected | pass |
| compat.cells_recorded | pass |
| capacity.limits_held | pass |
| capacity.saturation_typed | pass |

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
Unauthorized evidence access, within the enumerated cases: main 1, revocation 0; over-granted teeth run 1 (must be at least 1).
Concurrency: client intervals overlapped and the server gate peak was at least 3 (the gate counts requests, not principals): server_peak_ge_3 = true; holds_spanned (recorder-attested, not recomputed here) = true.

| class | deny cases | controls ok | unauthorized |
| --- | --- | --- | --- |
| control | 0 | 22 | 0 |
| ungranted_repo | 2 | 2 | 0 |
| ungranted_tool | 2 | 2 | 1 |
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

Configured limits:

| limit | value |
| --- | --- |
| listen.max_body_bytes | 1048576 |
| listen.max_inflight | 4 |
| listen.max_inflight_per_principal | 2 |
| limits.max_calls_per_minute | 10000 |
| limits.max_calls_per_principal_per_minute | 10000 |
| limits.max_graph_leases | 4 |
| limits.max_leases_per_principal | 1 |
| limits.max_records | 1024 |
| limits.max_total_lease_state_bytes | 1073741824 |
| workload.clients | 6 |
| workload.calls_per_client | 12 |

Invariants (peak <= max_inflight is true by construction of the gate):

| invariant | held |
| --- | --- |
| all_calls_completed | true |
| client_principal_concurrency_within_cap | true |
| limits_match_config_document | true |
| no_untyped_5xx | true |
| server_peak_within_limit | true |
| typed_503_has_retry_after | true |
| unauthorized_zero | true |

Saturation:

| step | held |
| --- | --- |
| gate_503_typed | true |
| other_principal_admitted | true |
| recovered_after_disconnect | true |
| stopped_line_rejected_ge_1 | true |
| third_rejected_typed | true |

Observed (excluded from canonical_sha256):

| metric | value |
| --- | --- |
| attempts | 74 |
| fds | {"max":14,"source":"proc_fd"} |
| latency_ms | {"max":103,"n":72,"p50":37,"p95":84} |
| memory | {"samples":12,"source":"proc_status","vm_hwm_kib":73392,"vm_rss_kib":73392} |
| rejections | {"inflight_limit":2,"principal_inflight_limit":0} |
| rusage | {"ru_maxrss":101336,"source":"wait4","unit":"kib"} |
| saturation_polls | 3 |
| server | {"admitted":80,"peak":4,"rejected":3,"source":"stopped_line"} |
| wall_s | 1.4 |

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
| capacity.main | a4cea4126cd48b1408ba2682ebc6bd557a53c23940450d583fe1bc03f1a8768d |
| compat.raw.http.2025-11-25 | 3889fe051cbad9cd315b582e6e7a40018e816f8dca61657ba7b5d08856c244d5 |
| compat.raw.shared-http.2024-11-05 | 92d1f666c430f26c1ad319c64dbf9572ffe630ca8e2c4041f59fffda832cbcf9 |
| compat.raw.shared-http.2025-03-26 | bc93d59200d42d9a5883accc5b7b34aec3e5e49e1fbb53aeee91130b64524e7e |
| compat.raw.shared-http.2025-06-18 | b022bb76df73bb00a1f1756f571ee98eefba4ad4e899c4008866b2987d8cb05c |
| compat.raw.shared-http.2025-11-25 | 12070624a95611b98ff3db3076b1e143c7b31c2fa8babd3443ba6d8010f2b022 |
| compat.raw.shared-http.2099-01-01 | 2ea054adb7244e02ae54155c11661ec552c558cba02266bac3df354c23b21e7a |
| compat.raw.stdio.2025-11-25 | 912837daf5531c89dc0e4fb61169a954591ee918b50512393894c5063c77a6e1 |
| compat.sdk.http.sdk_default | 09a5dbf4974ded319eece64f9e4a0d7bdafbd2b9505ec87f03281db8564cee4e |
| compat.sdk.shared-http.sdk_default | 2958fac5f11e011204b470a7f349bfe11ffdb4828624de17136071dec2f3601d |
| compat.sdk.stdio.sdk_default | 66a4b7908c0ba5a0b940d79ee06ee04ecab54504df6833a00f6b4cb1937241aa |
| environment.main | bd19115423e7eff87623131501c007eca10e41fcef224ead0e51f9258ab5d73a |
| interrupt.cancel_marker | 812662f697977feb44a409f8060a29ade6c2e5fe7945c44778ff184024755471 |
| interrupt.cancel_sigterm | e48aae003dd321dbb09fe1004f7efdf5a1293c0c565d797a94c14887710200e8 |
| interrupt.gc_vs_build | ad356f12fecd16bc8ce9aab3dbd77d2f69bab4d3e3420cea12436386ae4469f8 |
| interrupt.gc_vs_lease | 34f9002d46a6afbe70b324ab8fef644d1810d498f092d4b38eff75be1046aa82 |
| interrupt.kill_checkpoint | 153e39016a6220d6403edd1f4d4415f902ae3edaf1e0036cf295df991fe84c55 |
| interrupt.kill_mid_publish | 40cecfd19736e809d31032b1b5e50a940559ee10e354fd739ca70443b5ddf646 |
| interrupt.kill_post_replace | 83d6a07489515741854758d00ba7ddb370a05be191fcebac7c5898c57addacb7 |
| interrupt.kill_pre_receipt | 748e2421233b55d37771614c8143cd1b669c4a7ae592c554f19c7228a855aac9 |
| interrupt.teeth_replace_by_copy | 1423f1fe5f0708a2abf2dfc68cb370e2b948dd95ccade06cc5466f73c2b939da |
| interrupt.teeth_wrong_expectation | 9ea80499a585cbeba4602f28cc4218a58241f4c060737f143a64fbee6994f960 |
| isolation.main | fbbdb5e3d3ee701a6c9c46e2cb3b7ce037db276cde1db3b93689b863c9b26f1f |
| isolation.revocation | 1f716e1a26b336836db84113912cc6ef798708a31b5a7d94fe175ad9e59fcea4 |
| isolation.teeth | c3f92d75c5227f51424b6e8e40adb772a98e5b628b2746ee65e82007efc09731 |

## Canonical digest

canonical_sha256: f0de2c344e88a44e55430f2fa0e60a0707b2c62dad20c22b379e67eec5f6a2eb

