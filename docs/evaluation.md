# Evaluation and evidence

This page explains what each claim in the [README](../README.md) rests on, how to check it yourself, and what the check does not show. Every number here comes from a file in this repository.

## Offline checks

These run with no key and no network. They need only the repository and `uv`.

- **No LLM SDK in the server.** `test_no_llm_client_importable_from_server_package` in [`test_thesis_negatives.py`](../mcp-server/tests/test_thesis_negatives.py) fails if `anthropic`, `openai` or `boto3` is imported anywhere under `src/`. It checks import statements in this source tree only. It does not inspect your host, your model or any dynamic import.
- **Pinned dependencies.** The same file pins the core runtime dependency list and asserts that `networkx` is absent from `pyproject.toml` and `uv.lock`. It also asserts that no `ALTER TABLE` migration ladder exists in the source, since a schema change bumps the epoch instead.
- **Export determinism.** Export the same index twice and compare the files. Identical bytes are the expected result:

  ```bash
  cd mcp-server
  uv run atlas-aci export --repo /path/to/your/repo /tmp/a.jsonl
  uv run atlas-aci export --repo /path/to/your/repo /tmp/b.jsonl
  diff /tmp/a.jsonl /tmp/b.jsonl
  ```

- **Query determinism.** Identical indexes give identical query results. The same is true of `status`, which prints sorted keys.

## CI gates

[`ci.yml`](../.github/workflows/ci.yml) runs on pull requests to `main` and on pushes to `main`.

- **Test job.** It checks that the lockfile is current with `uv lock --check`, installs with `uv sync --frozen`, then runs pytest, ruff check, ruff format check and mypy. It runs the same linters on `scripts/`, and it runs the self-tests of the guard scripts.
- **Export byte-determinism (AC-REL-1).** One job builds the same small fixture repository on `ubuntu-latest` and on `macos-latest` and uploads each export. A later job downloads both and fails the build if they differ by a byte. The fixture is small, and the check covers only macOS and Linux.
- **Read-only serve smoke test (AC-H-16).** One job builds the image, indexes a fixture on a writable mount, then starts `serve` with `--read-only` and a `:ro` repository mount. It checks that the server stays alive and that every file under `.atlas` is unchanged, byte for byte and in its modification time.

The [release workflow](../.github/workflows/release.yml) adds the image checks described in the README under [Verified releases](../README.md#verified-releases).

## The v3 evidence package

[`docs/evidence/v3/README.md`](evidence/v3/README.md) publishes a paired, reproducible evaluation of the v3 server. It compares the v3 server with the pre-v3 image and with a search-then-read baseline.

What it measures:

- A retrieval matrix over five arms: `V0.legacy`, `V1.legacy`, `V1.context.references`, `V1.context.inline` and `B.search_read`. Budgets are 2000, 8000 and 16000 byte-quarter tokens, which are a byte-based unit and not model tokens.
- Index cost counters from `CodeGraph.build()`.
- A regression gate of 30 pinned pytest node ids, split into 14 binding and 16 snapshot tests.
- Probes of the negotiated protocol revision, the server identity and the tool names.

What it does not measure:

- **No model ran.** Coding-task success, model tokens, model cost and agent turns are recorded as `unmeasured`. The evidence README states that no operator budget existed for a live run.
- **Policies are deterministic transcripts.** They approximate an agent's calls. They are not an agent.
- **The labelled corpus is small.** It has four tasks and 742 bytes. The evidence states that no threshold or significance is claimed.
- **Some results are expected, not defects.** `search_symbol` matches exact names, so the multi-word `phrase` task finds no definitions on V0 or V1. The evidence calls this a property of the policy.

Known failures are listed in the same document. `test_call_deadline_after_initialization` fails under uid 1000 in the development container, and `test_nonregular_pack_does_not_block_acquisition` is flaky. Neither is in the regression gate.

The recorder never pulls images from the network during a run. It aborts on any timeout or transport error, and it refuses to record an empty V0 index. The V0 grammar cache is a declared input.

Replay the aggregation from the committed records. A clean `git diff` afterwards means the report reproduces:

```bash
cd mcp-server
uv run python ../scripts/eval/v3_aggregate.py --manifest ../atlas/evals/v3/manifest.v1.json \
    --raw ../docs/evidence/v3/raw --out ../docs/evidence/v3
```

## Community detection

Communities come from a hand-written label-propagation algorithm, not from a library. That code shipped only after it cleared a bar fixed before the probe ran. The bar was evaluated independently on two pinned reference repositories and was never averaged. The bar and its arithmetic are in [graph-query-dsl.md](graph-query-dsl.md). An optional, version-pinned Leiden comparison is documented in [experiments/community-comparison.md](experiments/community-comparison.md). The production default remains label propagation.

Read communities as structure found by a deterministic rule, not as ground truth about your architecture.

## Cost of the tool manifest

The [Cursor host page](../hosts/cursor.md) reports a measured size for the `tools/list` result. It is 347,091 bytes of canonical JSON under protocol revision 2025-11-25, and 12,923 bytes under 2024-11-05, which has no `outputSchema`. That page states that tokens were not counted.

## Canary suite

The canary runner is [`run-canaries.py`](../scripts/run-canaries.py). Its `stdio` host starts the server over stdio, replays a task manifest and records a transport ledger. The manifest format is described in [atlas/evals/README.md](../atlas/evals/README.md). The `stdio` host runs no model, so it measures transport behaviour and tool results only. The other `--host` values, `stub`, `claude-code-api`, `copilot` and `cursor`, raise `NotImplementedError`. [SETUP.md §7](../SETUP.md#7-run-the-canary-suite) explains the run.

## What these checks do not show

- **Agent outcomes.** No check in this repository runs a model against real coding tasks.
- **Completeness on your code.** The tests use fixtures and two pinned reference repositories. A green suite does not mean your graph is complete.
- **Host behaviour.** The v3 host matrix marks Claude Code, Copilot and Cursor as documented, with no automated run. The HTTP transport page states that real hosts are untested.
- **Speed.** No speedup is claimed for the extraction cache or for incremental indexing.
- **Isolation of test execution.** Timeouts and output caps do not isolate `test_dry_run`. Use an OS-level sandbox.
- **Windows.** Windows path handling is outside the export determinism check.
