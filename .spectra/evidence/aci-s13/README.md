# ACI-S13 execution evidence

One authoritative receipt is `.spectra/state/aci-s13.execution.json`; the original
planning criteria and pre-edit interrupted identity remain unchanged. Baseline
hashes cover 136 original inputs. Current runtime hashes are in `source-final.json`.

Observed gates: full suite 740 passed with all six required native integrations;
final protected S13 33 tests passed twice, with real pytest output and zero tamper
rejections. The earlier 32-test protected run remains intact. Static/lock and
size/classifier guards passed. The final selector self-test executed all 27 controls
and printed `27 passed, 0 failed`. `checks.json` contains direct process exits.
ATLAS independently approved corrected core/public/runtime, native/unaffected gates,
fresh raw/provenance/preservation, and the exact selector/packaging changes. Final
completion/output/re-entry review is a separate gate.

`raw-manifest.json` maps every original logical log path to deterministic gzip,
original byte count and SHA256, compressed byte count and SHA256. It includes all
red runs and both protected runs. Decode a stream with `gzip -dc <compressed_path>`;
verify its original hash from the manifest. To reproduce an assembly helper in a
disposable output checkout, restore the referenced logical log paths from this map
and restore both compact graph producer-output paths from
`probe/graph-raw-manifest.json`. Original producer command logs remain verbatim.
Do not overwrite historical evidence or re-sign an old measurement.

Real failures remain recorded: missing pre-feature page operation; SDK metadata
alias mismatch; immediate owned-timer shutdown and missing identity accounting;
final setup accounting deadline and zero collection budgets; captured work/seed
validation; initial wrong Docker build context. Test-harness corrections (source
pack offset, captured closed FD, imported evidence state unknown) retain their raw
failed runs and do not imply those were product defects.

The current fresh same-pin/default-ten-language probe passed the unchanged verifier.
Both corpora have zero Rust files; Rust behavior comes from qualified native Cargo
fixtures, not these corpora. All 4,877 eligible hashes, source packs, current/no-history
facts, failed-file exclusion and twenty raw partitions were independently verified.
Full Spree export was 651,845,336 bytes, peak process RSS 1,973,764,096 bytes. Cache
bytes were 334,683,424 (Solidus) and 531,906,606 (Spree). Resource-owning sample state
was 2,222,916/2,102,700 canonical bytes, with private catalogs 337,471/320,952 bytes.
Acquisition-inclusive issuance was 11.052/17.777 seconds; this is not a total request
latency guarantee or a throughput benchmark. See separately labelled measured
intervals, original raw jobs and `probe/budget-summary.json`.

Four exact source catalog/manifest streams preserve native identity, context,
current observations, range commitments and pack indexes. After independent raw and
preservation approval, root authorized exactly three completed scratch removals.
`probe/cleanup-closed.json` records pre-unlink full raw/index/pack/hash checks and
actual outcomes. All source parents, `.git`, seeds, SDK assets and compact evidence
remain. No other path was removed.
