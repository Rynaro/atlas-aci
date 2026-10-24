# ACI-S11 execution evidence

The single authoritative receipt is `.spectra/state/aci-s11.execution.json`.
The original plan/criteria/envelope remain frozen. `independent-reviews.json`
records the actual scoped ATLAS reviews, without inventing product rubric scores.

The pre-edit fixture is the exact real 103-record portable artifact under
`mcp-server/tests/fixtures/s11/`. Its 12 legacy outputs retain every ID, link,
confidence, reason, count and ordering; only the proven top-level destination
`generation_id` difference is normalized. Initial SDK bypass/error-flag evidence,
protocol counterexamples, genuine original-identity v1 failure, wrapper failures
and the first capped protected run are retained alongside their corrections.

Final runtime hashes are in `source-v1-corrected-checkpoint.json`; the native
Docker record binds all 13 relevant runtime files to the final production image.
`full-suite.job.json` records 623 passing tests with all required Docker gates
and no skips. `static-final/checks.json` contains 11 true process results, with
27 probe, five size and six classification controls. `vivi-pass2-final-result.json`
records both protected 42-case runs passing without test tampering. Its task-temp
artifact references map to exact durable copies through `pass2-artifact-map.json`.
The only normalized native inventory values are the observed synthetic `/repo`
mount-root UID/GID; both raw observations remain preserved. Every other root
field, every descendant and the entire graph inventory remain strict.

`probe/` contains genuine final-producer measurements on the unchanged Solidus
and Spree pins/default ten-language profile. All 4877 original eligible path/hash
observations match; failed files have no active SQL support and no history.
Both trees contain zero Rust files: Rust correctness comes from the native
workspace and offline Docker controls, independently of this corpus regression.
The immutable verifier recomputes PASS for both repos from all 20 measured
partitions. Primary serial-job charges, catalog preservation and authorized
scratch cleanup are separately recorded; none resets the fixed 600/2700 caps.

The current Spree portable export measured 651,845,336 bytes with a process peak
RSS of 1,964,720,128 bytes. Extraction caches measured 334,683,424 and 531,906,606
bytes, with separate source-pack/catalog/manifest costs. These are material costs,
not a new size or speed threshold. The large full export and only the two closed
generated `.atlas` trees were removed after independent raw/closure verification
and root authorization. Their exact hashes, sizes, source commitments and cleanup
outcomes are retained; all pinned source parents and `.git` directories remain.

Completed raw logs are stored losslessly as deterministic gzip streams.
`raw-manifest.json` maps each original logical path to compressed path, raw and
compressed SHA-256, byte lengths and mtime zero. Existing command records retain
their original logical output paths. Read a log without modifying the checkout:

```python
import gzip
from pathlib import Path
print(gzip.decompress(Path("LOG_PATH.log.gz").read_bytes()).decode())
```

`probe/graph-raw-manifest.json` maps the two original measured graphs;
`probe/source-catalog-raw-manifest.json` maps four exact retained source
manifest/catalog streams. Observations carry their own compression provenance.
To reproduce the compact bundle, unpack the graph and modularity stdout streams
into a temporary directory and run the unchanged
`scripts/probe-assemble-graph-bundle.py --out OUTPUT --repo
solidus:GRAPH:MODULARITY --repo spree:GRAPH:MODULARITY`.
The full raw portable export is intentionally regenerable scratch, not a claimed
lossless committed copy; its measured SHA/size and audited disposal are explicit.
