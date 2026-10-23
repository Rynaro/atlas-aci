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
