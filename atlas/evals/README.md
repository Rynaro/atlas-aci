# Local stdio transport baseline

The canary runner has one real MCP SDK host adapter. It starts Atlas over stdio,
initializes the session and sends the manifest's ordered tool calls. This
measures transport/tool behavior, **not a coding-agent evaluation**. No model
runs or paid calls occur. Model tokens, cost and coding-task success stay `null`
with an unavailable reason. A successful lookup cannot establish token savings
or engineering success. The prior dispatcher was a stub, so no working
pre-change host measurement exists for comparison.

Use the Python environment containing `mcp-server`'s dependencies. From the
checkout root, record a tiny independently versioned/indexed Python fixture:

```sh
python atlas/evals/record_baseline.py --output /tmp/atlas-s01/baseline.json
```

The generator pins the fixture commit with fixed source, author and commit date,
indexes it using this checkout, then invokes the real runner. It preserves the
actual manifest as `/tmp/atlas-s01/baseline.tasks.json`; the responses and task
receipts live under `/tmp/atlas-s01/baseline.ledger/<run_id>/`. Repeating the
command with the same checkout/dependencies reuses the completed evidence. A
checked-in sample is under `atlas/evals/baseline/`; its producer/source hashes
and dependency versions identify the measured implementation. A new environment
or producer may yield a new run identity.

For an existing indexed Git repository, place `tasks.json` in a directory:

```json
{
  "schema": "atlas/task-manifest.v1",
  "repository": {"revision": "FULL_CURRENT_GIT_COMMIT"},
  "model": null,
  "tasks": [
    {
      "id": "lookup",
      "tool": "search_symbol",
      "arguments": {"name": "baseline_target"},
      "expect_text": "baseline_target"
    }
  ]
}
```

Then run:

```sh
python scripts/run-canaries.py --repo /path/to/repo \
  --canaries-dir /path/to/tasks --host stdio \
  --output /tmp/atlas-result.json --deadline 10 --ci-gate
```

`--repo`, `--canaries-dir`, `--host`, `--output`, `--ci-gate` and `--baseline`
remain available. `stdio` is now the default. Legacy `stub`, `claude-code-api`,
`copilot` and `cursor` spellings remain accepted, but those adapters are still
unsupported. Legacy scout-report scoring is retained separately; raw MCP output
is never converted into a fabricated scout report. `--baseline` compares
transport counts only for matching canonical task/repository inputs.

## Ledger and identity

The versioned `atlas/transport-ledger.v1` ledger uses SHA-256 of canonical UTF-8
JSON (sorted keys, compact separators, final LF). A run fingerprints the complete
manifest, each ordered task, full repository revision and source-content set,
index facts, Atlas/adapter source, Python and relevant package versions, server
command/binary or script contents, and deadline. Repository paths are normalized
out of the command. Index cache mtimes and diagnostic indexing dates are excluded
from the logical index hash. Output location, process IDs, durations and temporary
fixture locations do not define a run. Source, arguments, index facts, producer
or configuration changes yield a different run. Keep the ledger outside the
indexed repository so output files cannot feed back into its fingerprint.

Each task stores the actual serialized MCP response, request references,
SHA-256 payload hashes, duration, and an outcome: `success`, `timeout`,
`transport_error`, `tool_error`, `application_error`, or `expectation_failure`.
`COMPLETE` means execution finished; inspect `summary.failed` for failures.
Exit status is 0 for all-success transport, 1 for task failure, and 2 for invalid
inputs. A bounded deadline covers initialization and the call; the SDK closes
stdin and performs bounded process-tree termination during cleanup.

Writes use a same-directory temporary file, flush/fsync, then atomic replace.
On resume, receipts must match the canonical task/run identity, completion
schema and referenced request/response hashes. Corrupt or missing evidence is
re-executed. A verified completed run returns before constructing a dispatcher
and leaves its ledger bytes unchanged. Interrupted runs retain verified tasks
and continue with the first missing task. Completed failure outcomes are durable
also; change the inputs/deadline or explicitly remove that run's receipt to retry.
Use one writer per result directory; concurrent execution is outside this slice.
Durations and Atlas diagnostic response timing may differ on fresh executions.

## Validation-plan comparison (ACI-S22)

`validation_plan_comparison.py` is a small, labelled demonstration of
`atlas-aci validation-plan`: on one edit of the coverage fixture
(`mcp-server/tests/fixtures/coverage`, three function bodies changed) it compares
run-everything, structural-only and the plan against truth taken from the frozen
run-`a` labels, at function granularity (precision, recall, TP/FP/FN and the gap count).
It launches no process and runs no test. Run it from the checkout root in the
`mcp-server` Python environment:

```sh
python atlas/evals/validation_plan_comparison.py [--output PATH]
```

It writes `.spectra/evidence/aci-s22/plan-comparison.json` when that directory exists.
The numbers are honest about their limits: the plan's observed recall is consistent by
construction (it reads the same run as the truth), the structural baseline is perfect here
only because the fixture tests call the edited functions by name, and no superiority
threshold is claimed. `ruff check` covers `tests/`, which imports this script; `mypy src/`
does not cover it.

## v3 evidence (ACI-S23)

`span_metrics.py` holds the S18 `union`/`metrics`/`body_and_sources` accounting as a pure module
shared by the v3 aggregator (`scripts/eval/v3_aggregate.py`); `measure_context_yield.py` is
unchanged and `test_s23_metrics.py` proves the two agree on every committed S18 row. The frozen
manifest is `v3/manifest.v1.json`. `run-canaries.py --server-json` runs any `tasks.json` against
another server argv (inline JSON array or a file). See `docs/evidence/v3/README.md`.

## ACI-S42 shared-operation readiness

`shared/manifest.v1.json` (`atlas-shared-readiness-manifest.v1`) freezes the declared fixture for the
shared-operation evidence: the four principals and their grants, the planted canary trees, the
evidence access case table (controls, deny cases with a same-shape control and a target domain,
a revocation sequence and the over-granted teeth re-run), the interruption matrix, the client and
transport cells, the bounded workload, the deadlines and the required checks. Two scripts use it:

```sh
python scripts/eval/s42_record.py --manifest atlas/evals/shared/manifest.v1.json \
    --out /tmp/s42/raw --work /tmp/s42/work
python scripts/eval/s42_aggregate.py --manifest atlas/evals/shared/manifest.v1.json \
    --raw docs/evidence/s42/raw --out docs/evidence/s42 [--verify]
```

The recorder starts real `atlas-aci` processes (`serve-shared`, stdio `serve`, `serve --transport
http`, `jobs`) and writes one gzip-canonical record per unit; the aggregator is pure and derives
every count and the verdict (`blocked` or `ready_for_operator_review`) from row-level facts. Keys
named `observed` or `diagnostic` are excluded from `canonical_sha256`, so replaying the committed
records is byte-identical while a fresh run is compared by invariants. Formats, commands, the
tested deployment model and the committed run are in `docs/evidence/s42/README.md`.

H1 Black-box evidence: the ledger detects planted canaries and typed outcomes in responses and server log lines, not internal reads.

H2 Unauthorized evidence access is zero within the enumerated cases; this is not a claim that no leakage exists.

H3 Not tested: timing side channels, the shared address space of the single server process, and a TLS-terminating reverse proxy.

H4 Inherited and not re-run: the S40 in-process Observer suite (mcp-server/tests/slices/test_s40_contract.py).

Hosts are listed and never tested: claude-code: untested, github-copilot: untested, cursor: untested.
Unresolved limits, always rendered in the report:

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
