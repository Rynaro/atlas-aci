# Shared-operation evidence (ACI-S42)

This directory holds immutable run records and one derived report that say what was observed when
the shared multi-principal HTTP mode (`atlas-aci serve-shared`, S40), the index jobs (S41) and the
protocol surfaces (S38, S39) were exercised together. It is evidence for an operator decision. It
is not a deployment, not an approval, and no product code changed in this slice.

- `raw/*.json.gz`: one record per unit, written by `scripts/eval/s42_record.py` (canonical JSON,
  gzip level 9, mtime 0). Records are never edited or overwritten.
- `readiness-report.json` and `readiness-report.md`: the report, rebuilt by
  `scripts/eval/s42_aggregate.py` from the records and the frozen manifest
  `atlas/evals/shared/manifest.v1.json`.

## What the evidence is, and what it is not

H1 Black-box evidence: the ledger detects planted canaries and typed outcomes in responses and server log lines, not internal reads.

H2 Unauthorized evidence access is zero within the enumerated cases; this is not a claim that no leakage exists.

H3 Not tested: timing side channels, the shared address space of the single server process, and a TLS-terminating reverse proxy.

H4 Inherited and not re-run: the S40 in-process Observer suite (mcp-server/tests/slices/test_s40_contract.py).

The word isolation in these documents means the enumerated cases of the case table and nothing
wider. A zero is a count over those rows. The verdict vocabulary is `blocked` or
`ready_for_operator_review`; the second is never a deployment approval.

Hosts. No real host was tested. Each is listed and none is a cell of the compatibility matrix:

- claude-code: untested (no_real_host_run)
- github-copilot: untested (no_real_host_run)
- cursor: untested (no_real_host_run)

## The deployment model that was tested

One loopback `serve-shared` process per phase, a generated RS256 key pair and an offline JWKS file,
port 0 (ephemeral), policy reload interval 0 (every request re-reads the policy), no TLS reverse
proxy, container-local temporary storage behind a flock and hardlink canary. Repositories `a` and
`b`, plus `c`: the before snapshot of `a`, registered at the same path (S40 same-path pair). Four
principals (p1 to p4). Clients: raw HTTP/1.1 and stdio JSON-RPC lines, and the mcp SDK client.

## The five phases

1. Isolation (evidence access ledger). One server, 40 enumerated rows: warm pages, two held
   requests, a concurrent main gather (controls and deny cases), a revocation sequence and an
   over-granted teeth run. Canary tokens are planted per evidence domain: a pattern token that
   requests may carry and a witness token that no request ever carries (it sits on the same source
   line, in a file path and in a callee identifier). A row is unauthorized when a deny succeeded,
   when a witness of a domain outside the row's authorization appears in the response bytes (or in a
   base64-decoded resource blob), or when a deny row's nonce reaches a server `tool_call` log line.
   Every deny case has a same-shape control that must succeed and reach the same witness through an
   allowed principal. The over-granted teeth run must make the ledger fire.
2. Interruption matrix. Eight cases (cancel by marker and by SIGTERM, kills at a checkpoint, mid
   publish, after replace and before receipt, a garbage collection against a held lease and against
   a running build) plus two teeth cases (a retained farm corrupted by replace-by-copy, and a healthy
   namespace validated against the other tree). A recovered namespace is valid when `jobs status`
   shows the expected generation with no running, interrupted or failed job, a stdio server on it
   reports that generation and finds the expected tree canary and not the other tree's, an acquire
   of the current generation succeeds, and the generation equals an independent twin build.
3. Compatibility. Ten cells (client, transport, requested revision, negotiated revision): raw JSON-RPC
   over shared HTTP at four listed revisions and one unlisted revision (counter-offer), raw over
   plain HTTP and stdio, and the SDK over stdio, HTTP and shared HTTP. Only cells with a record exist.
   `sent_revision` is what the recorder sent on later requests for the raw HTTP cells; for stdio and
   the SDK cells it is recorded as the negotiated revision and was not observed on the wire.
4. Capacity. One bounded workload record: six clients, 12 calls each (72), limits as configured, a
   per-principal saturation step (typed 503 `principal_inflight_limit` with `retry-after`), a gate
   step (typed 503 `inflight_limit`, and a stopped line with `rejected >= 1`) and recovery. The
   invariant `peak <= max_inflight` is true by construction of the gate and is labelled so.
5. Environment. The SDK pin, versions and the lock canary; the producing image, python and platform
   are in `observed`.

## Checks and verdict

Thirteen checks, each `pass`, `fail` or `missing` (missing counts as fail); any non-pass makes the
verdict `blocked`. Malformed or foreign input raises a typed error and writes no report. The
checks are listed in `readiness-report.md`.

## Unresolved limits (always rendered)

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

## Which run is committed

The committed records come from one recorder run inside the production image, started with
`--entrypoint /usr/local/bin/python`, user `atlas`, `--network none` and `python -I`:

- image id: sha256:1785257ee96b40dee1368082d8a672d78aa0aac0f0cdc8fc1bcf8237b4144790 (tag
  atlas-aci:s42-wip, built from `mcp-server/Dockerfile` at base 4f470d7 with `--no-cache`)
- python: 3.12.16, platform: Linux-aarch64

The report's `environment.observed` block records the same facts. A fresh run is compared with the
committed one by invariants (every check passes), never by digest: ports, timings, resource use
and the order of concurrent requests differ from run to run. Replaying the committed records is
byte-identical.

## Commands

Regenerate the records (needs the project environment; in the production image the same command
runs with `python -I`):

    python scripts/eval/s42_record.py --manifest atlas/evals/shared/manifest.v1.json \
        --out /tmp/s42/raw --work /tmp/s42/work

A cold tree-sitter grammar cache downloads the grammar once, serially, before any unit starts (up to
240 s, outside the 300 s recorder deadline); the production image prefetches its grammars and the
recorder runs there with `--network none`. `--out` and `--work` must be empty. Exit 0: every unit complete; 1: at least one unit recorded
failed; 2: usage or invalid manifest; 3: abort (lock canary failed or a directory not empty).

Rebuild the report and verify the committed files (from `mcp-server/`):

    uv run --frozen python ../scripts/eval/s42_aggregate.py \
        --manifest ../atlas/evals/shared/manifest.v1.json --raw ../docs/evidence/s42/raw \
        --out ../docs/evidence/s42 --verify

Exit 0 means the rebuilt bytes equal the committed files; 1 means they differ; 2 is a typed input
error. Without `--verify` the report files are written.

The production scenario (`.spectra/evidence/aci-s42/prod-scenario.sh IMAGE`) runs the recorder in
the production image, aggregates, and prints a PASS or FAIL line per check. With `S42_NEGATIVE=1` it
over-grants one principal and must produce `blocked`.

## Record formats

Raw record `atlas-shared-raw.v1`: `{format, kind, unit, manifest_sha256, status, failure, body,
observed, diagnostic}`. Kinds: environment, isolation, interrupt, compat, capacity. Ports, pids,
tokens, absolute paths, timings, counters and observed generation ids live only under keys named
`observed` or `diagnostic`; every other value is canonical. The report digest
`canonical_sha256` is the SHA-256 of the report with those keys removed at every depth. Report
format `atlas-shared-readiness-report.v1`; manifest `atlas-shared-readiness-manifest.v1`.
