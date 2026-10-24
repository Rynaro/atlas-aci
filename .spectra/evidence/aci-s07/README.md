# S07 execution evidence

The authoritative receipt is `.spectra/state/aci-s07.execution.json`. The final
producer `9af60175…` passed 408 tests with Docker enabled and zero skips;
`parser-prerequisite/docker-module-final/docker-integration.json` records the
actual UID10001, EROFS sentinel, offline/read-only mounts and MCP responses.
Protected pass² passed 88 tests per run with no tamper rejections. The final
probe self-test executed all27 controls, returning0 with27passed/0failed.

Fresh same-pin/default-language measurements are in `parser-prerequisite/fresh-probe-attempt3/`;
the unchanged verifier returns PASS for both repositories. All4877 original
path/hash observations are accounted for: Solidus2459indexed/60failed,
Spree2332indexed/26failed. Failed files contribute no current extracted facts.
Earlier failed exports, qualification attempts and red regressions remain intact.
The approved policy correction and explicit human review are linked by the receipt.

The current Spree portable export measures651,844,965bytes with peakRSS
1,983,594,496bytes. This is a material storage/memory cost, not a new S07 size
threshold: the archived P3 measurement used a different profile and is unchanged.
No historical compression ratio is claimed for this output.

Raw machine logs are losslessly gzip-compressed with mtime0. `raw-log-manifest.json`
maps logical paths to compressed paths, lengths and raw/packed SHA256 hashes.
For an existing helper expecting plain outputs, restore each missing logical
file with `gzip -dc <compressed_path> > <logical_path>` in a disposable checkout.
Fresh graph and observation manifests additionally record their original hashes;
restore those similarly before rerunning the recorded assembly helper. No raw
facts were sanitized. Source exports of hundreds of megabytes stay outside Git;
the exact byte/hash/type attribution and commands are retained.
