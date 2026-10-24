# S07 execution evidence (in progress)

The authoritative receipt is `.spectra/state/aci-s07.execution.json`. No output
commit or COMPLETE claim exists yet. Product checkpoint `a7696a05…` passed the
375-test suite with the rebuilt Docker image and zero skips. The detailed
`docker-integration.json` retains UID10001, EROFS, offline/read-only mounts,
unchanged source/graph snapshots and real MCP responses. `vivi-pass2-result.json`
records protected pass² (55 tests/run, tamper count0).

Fresh current-producer probe readiness is still blocked. The genuine pinned
Solidus full-tree export failed on a recovered SCSS parse error; no replacement
PASS measurement has been invented. The authorized scratch-only candidate grammar
qualification repairs that first file but leaves22/24 errored eligible files in
Solidus/Spree respectively. See `grammar-qualification/scan-summary.json` and its
source/command provenance. No production grammar, dependency, query or default
language change was made for that experiment.

Historical red runs are retained, including the actual14-failure predecessor
run, the5-failure/1-pass projection omission run, cache/rationale counterexamples,
and failed probe/qualification harness invocations. A command returning0 for a
completed diagnostic scan does not mean the scanned repository parsed cleanly.

Large/whitespace raw logs are losslessly compressed with gzip mtime0. Their
logical names, original byte lengths/SHA256 and compressed SHA256 are in
`raw-log-manifest.json`. Read a logical `.log` through `gzip -dc <name>.log.gz`
when the plain file is absent. Qualification per-file JSON/failures use the same
protocol in `grammar-qualification/scan-manifest.json`; decompress before using
an existing consumer that expects its original logical JSON path.
