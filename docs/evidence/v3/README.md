# ATLAS ACI v3 evidence and compatibility package (ACI-S23)

This directory publishes a reproducible, paired evaluation of the v3 server against the
pre-v3 server and a search-then-read baseline, plus the release-integrity gate and a host/SDK
matrix. Everything in `release-report.json` / `release-report.md` is **derived** from the
immutable records in `raw/` and the frozen manifest `atlas/evals/v3/manifest.v1.json`.

What this is not: it is **not** a live agent evaluation. No model ran, so
`coding_task_success`, `model_tokens`, `model_cost` and `agent_turns` are `unmeasured`
(`no_operator_budget`). Policies are deterministic transcripts, byte-quarter tokens are not
model tokens, the labelled corpus is four tasks and 742 bytes, and no threshold or
significance is claimed. The report lists its limitations explicitly.

## What is measured

| Unit | Source | Notes |
| --- | --- | --- |
| Useful-span matrix | `raw/retrieval.<arm>.json.gz` | 5 arms x budgets 2000/8000/16000 byte-quarter tokens (`max_bytes` 8000/32000/64000) |
| Index cost counters | `raw/cost.<corpus>.<variant>.json.gz` | `CodeGraph.build()` return values; V0 from the v2 `index_done` event |
| P0 regression gate | `raw/p0.all.json.gz` | 30 explicit pytest node ids (14 binding, 16 snapshot) run in a subprocess |
| Probes | `raw/probe.<variant>.json.gz` | negotiated protocol, `serverInfo`, tool names, V0 image identity |
| Offline proxy | `raw/offline.validation_plan_comparison.json.gz` | S22 comparison, labelled `offline_fixture_proxy` |

Arms: `V0.legacy` (atlas-aci 2.0.0 image by digest, legacy tools), `V1.legacy` (HEAD, same
policy), `V1.context.references` and `V1.context.inline` (HEAD `graph_query op=context`),
`B.search_read` (HEAD `search_text` then `view_file`). `V0.context.*` is `not_applicable`
(`op_unavailable_pre_v3`), never zero. The three variants share one frozen task input
(`task_input_sha256`) and the same deterministic corpus commit (`corpus_commit`).

Policies (no model): `atlas-legacy-symbol-read.v1` calls `search_symbol(name=term)` per term and
`view_file` on the first 8 definitions; `atlas-search-read.v1` calls `search_text(term)` and
`view_file` on a 10-line window around the first 8 matches; `atlas-context.v1` is one
`graph_query op=context` call per task and budget. `search_symbol` is an exact-name match, so the
`phrase` task (terms `retry`, `transport`, `timeout`) finds no definitions on V0 and V1: that is
a property of the policy, not a defect. `atlas-budget-prefix.v1` admits the longest prefix of a
legacy transcript whose cumulative normalised bytes fit `max_bytes`; error results count their
bytes and deliver no spans.

## Prerequisites (no network at run time)

- The repository dependencies (`cd mcp-server && uv sync`), `rg`, `git`, and Docker.
- The pre-v3 image present locally by digest. The recorder never pulls:
  `ghcr.io/rynaro/atlas-aci@sha256:e2542ef8d569882c560065f0bcaed9ef2a7398e7e61765e25b142a3c93ec7cf7`.
- A V0 **grammar cache** (below). The v2.0.0 image does not prefetch tree-sitter grammars, so an
  offline `index` exits 0 with `files_indexed=0`; the recorder refuses to record such a V0.

### V0 grammar cache (a declared input, fetched once with network)

```sh
docker run --rm --pull never -v "$CACHE":/tmp/.cache --entrypoint python \
  ghcr.io/rynaro/atlas-aci@sha256:e2542ef8... \
  -c 'import tree_sitter_language_pack as t; t.download(["ruby","python","typescript","javascript","scss","html","yaml","markdown","bash"])'
```

This is the only networked step, it runs the V0 image itself (tree-sitter-language-pack 1.6.0,
linux/arm64), and its result is declared by `.spectra/evidence/aci-s23/v0-grammar-cache.json`
(a per-file sha256 manifest and a tree hash; the binaries are not committed). The manifest
references it under `inputs.v0_grammar_cache`. The recorder verifies the cache tree hash before
and after the run, mounts it read-only at `/tmp/.cache`, and then runs V0 `index` and `serve` with
`--network none --pull never`. The V0 index must report `files_indexed` equal to the corpus
file count and `symbols > 0`, otherwise the run aborts.

## Run the recorder (release run)

The release run used a container that has the repository's Python dependencies and a static
Linux docker CLI (`.spectra/evidence/aci-s23/Dockerfile.recorder`, a pinned download with a
sha256 check), the Docker socket mounted, and the repository and scratch directory mounted at
**identical absolute host paths** so nested `docker run -v` paths resolve on the daemon
(`.spectra/evidence/aci-s23/rec23.sh`). The socket is `root:root 0660` on Docker Desktop, so the
container runs as uid 1000 with `--group-add 0`; without that the socket is not accessible. The
recorder runtime is therefore CPython 3.12 (the CI runtime is 3.14).

```sh
cd mcp-server
uv run python ../scripts/eval/v3_record.py \
  --manifest ../atlas/evals/v3/manifest.v1.json --out ../docs/evidence/v3/raw \
  --work /abs/scratch --v0-cache /abs/scratch/v0cache
```

The recorder aborts (never records) on any `timeout` or `transport_error` (V0 deadline 90 s), an
empty or incomplete V0 index, a changed grammar cache, or a P0 id that did not pass. Only tool-level
`tool_error`/`application_error` results are recorded as errors. Raw records are canonical JSON
(sorted keys, compact, final LF), gzip level 9 with `mtime=0`; identity is the sha256 of the
decompressed bytes, never of the `.gz`.

## Aggregate and replay

```sh
cd mcp-server
uv run python ../scripts/eval/v3_aggregate.py --manifest ../atlas/evals/v3/manifest.v1.json \
  --raw ../docs/evidence/v3/raw --out ../docs/evidence/v3
```

`v3_aggregate.py` is stdlib-only and pure: the same raw records always give the same `canonical`
section and `release-report.md`; `canonical_sha256` is the sha256 of the canonical section. It
fails closed with a typed code (`manifest_mismatch`, `hash_mismatch`, `duplicate_input`,
`unpaired_inputs`, `incomplete_matrix`, `missing_cost_category`, `live_without_budget`,
`content_mismatch`, `unknown_shape`, `missing_input`). `diagnostic` (durations, run ids, producers,
platforms) is excluded from the canonical section and from the md. `test_s23_contract.py::
test_report_replay` re-aggregates the committed records and compares them with the committed report.

Any change to the manifest changes `manifest_sha256` and invalidates every raw record made under the
old one. The recorder, not CI, produces the raw records; CI only replays them.

## Record formats

- `atlas-eval-manifest.v1`: corpus (sha256 / tree hash), tasks, budgets, variants (V0 argv templates),
  arms, policies, comparisons, cost categories and counters, the 30 P0 ids and exclusions, the host
  matrix and its evidence anchors.
- `atlas-eval-raw.v1`: `{format, kind, manifest_sha256, status, ...}`; retrieval observations carry
  `observation_key`, the call, `result_text` (the deterministic re-serialisation of the client
  `CallToolResult`, whose length equals the server `result_bytes`) and `result_sha256`.
  `result_bytes_repo_normalized` replaces the repository root with `/repo` first.
- `atlas-release-report.v1`: `{canonical, canonical_sha256, diagnostic}`.

## Known failures and follow-ups

- `test_s01_contract::test_call_deadline_after_initialization` fails under uid 1000 in the dev
  container (known, not P0); `test_s08_integration::test_nonregular_pack_does_not_block_acquisition`
  is flaky and excluded from P0.
- Follow-ups: a live agent evaluation when an operator budget exists; a labelled corpus for
  `op=retrieve/impact/diff/validation-plan`; the stale S01 sample `atlas/evals/baseline/result.json`;
  `CLAUDE.md` still cites the removed `canary-missions.md`.
