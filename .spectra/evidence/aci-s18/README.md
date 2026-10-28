# ACI-S18 execution evidence

This directory preserves one execution identity and all failed/intermediate runs.
The authoritative receipt is `.spectra/state/aci-s18.execution.json`; frozen
criteria, plan and initial interrupted receipt are unchanged.

Final source/gates: `source-final.json`, `docker-build.job.json`,
`full-suite.job.json` (827 passed, no skips), `checks.json` (15 zero exits),
`protected-inputs.json` and `protected-loop/` (44 real S18 tests twice).
Detailed final native records are `s13-native-full.json`, `s17-native-full.json`,
`s18-native-full.json`, `native-pass2.json` and `docker-final/`.

`local-yield.json` preserves all 48 final SDK results against the unchanged
pre-packer fixture and 24 reference-only baseline results; `local-yield.md`
reports useful-span unions, redundancy, errors and unfavorable tight-budget
results. `producer-applicability.json` binds the unchanged fact producer to S17.
The unchanged S17 verifier is checked again; no old artifact is re-signed and no
new corpus or selector change is claimed for the query-only context feature.

Raw logs/text are losslessly compressed. `raw-manifest.json` maps every logical
path to its gzip bytes, raw/compressed SHA-256 and lengths. Gzip mtime is zero;
read a logical `.log` or `.txt` reference through this manifest. No whitespace or
failure output was sanitized. Original command output locations are separately
recorded as `producer_output_path` where known.

Historical checkpoints have explicit scope:

- `prior-first-image/`: image edbeb3a9, targeted native proof and the interrupted
  352-pass full run (exit 2), before the old-dispatch compatibility correction.
- `prior-s17-protected-loop/`: mistakenly selected S17 tests, explicitly rejected
  as S18 coverage. Both executable and protection globs were then checked.
- `pre-replay-gates/`: image 80d22011, 826-pass suite and protected 43-test loop,
  valid for their old source but superseded after the canonical clock-field bug.
- `pre-replay-runtime.json.gz`: exact old 47 runtime files authenticated against
  the immutable prior image and recorded source hashes, extracted honestly later.
- `first-image-preservation.json` and `pre-replay-gate-preservation.json` map
  original paths to their retained stage paths with exact hashes and lengths.
- `checks-before-import-correction.json` records the old import-order failure;
  its original failed `ruff.log` is retained as
  `ruff-before-import-correction.log` through the raw manifest.

`baseline-checkpoint.json` and `pre-edit-source.json.gz` retain the genuine
pre-packer producer/SQLite/pack/source and actual reference-only SDK outputs.
`protocol-preformat-provenance.json` accurately labels reconstructed-now prior
source authenticated by its independently reviewed SHA, not a pre-edit capture.
The replay reds and observed-expiry control retain the actual duration leak and
prove that its correction did not reset the real shared deadline.

No large corpus scratch was created or deleted for S18. Existing source parents,
Git metadata, SDK assets, UV cache and all historical compact evidence remain.
