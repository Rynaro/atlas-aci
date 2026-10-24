# ACI-S08 execution evidence

The authoritative execution receipt is `.spectra/state/aci-s08.execution.json`.
Original criteria and the supporting footprint criteria retain one identity.

Final runtime source: engine `30d0c8d7…`, snapshots `67d3d7c3…`; all nine runtime
files match the final read-only/offline Docker image. The final full suite passed
581 tests without skips; protected S08 verification passed64 tests twice.
Static checks and the final27-control probe self-test passed. Independent ATLAS
reviews cover the source, native integration, measurements and selector delta.

The held original baseline and refined producer both passed the unchanged probe
bar on the same4877 source paths/hashes and ten-language profile. Both corpora
contain zero Rust files; native workspace tests establish Rust behavior. Final
source manifests are compact v2, with every original typed range committed and
validated from the opened graph. Genuine v1 compatibility uses an actual archived
parser-built generation, never a renamed version marker.

See `lease-footprint/probe/paired-costs.md` for isolated graph/source/MCP timings
and process peak RSS. Warm direct graph repeats were slightly slower; there is
no universal latency claim. Existing historical-query checks remain. Full Spree
portable output was651845336 bytes; export format and source inputs are unchanged.
The completed measurement charge was1391.461103626 serial-job seconds. Two
predelete catalog-preservation jobs add1.352324667s, final1392.813428293s within the
unchanged600s/job and2700s envelope. These sums are not total orchestration wall
runtime. The original five-day allocation and supporting one-day cap did not reset.

`lease-footprint/closed-measurement-cleanup.json` records the exact six authorized
regenerable scratch removals. Source parents, Git metadata, seeds, native assets,
compact graphs/partitions, original failures, genuine v1 fixture and nine frozen
old source archives remain. Compact old catalogs and actual v2 manifests preserve
the exact per-source comparison without committing145MB range arrays.

## Lossless raw evidence

`raw-log-manifest.json` is the final logical-path map for227 completed raw log
streams. Each gzip has mtime0 and recorded compressed/raw byte lengths and SHA256.
Earlier baseline manifests describe their earlier copy-retained checkpoint;
the final map identifies their current recoverable location. Graph, observation,
source-catalog and runtime-source manifests retain their own lossless mappings.
Post-output verification logs remain separate as explicitly excluded receipt data.

To unpack raw logs into a separate scratch tree:

```python
import gzip, hashlib, json
from pathlib import Path
root = Path.cwd()
out = Path('/tmp/aci-s08-evidence-replay')
for item in json.loads((root / '.spectra/evidence/aci-s08/raw-log-manifest.json').read_text())['files']:
    raw = gzip.decompress((root / item['compressed_path']).read_bytes())
    assert len(raw) == item['uncompressed_bytes']
    assert hashlib.sha256(raw).hexdigest() == item['uncompressed_sha256']
    target = out / item['logical_path']
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
```

The refined `lease-footprint/probe/graph-raw-manifest.json` maps both compact graph files. Unpack
them and the two `*-modularity.stdout.log` streams, then use the unchanged shipped
`scripts/probe-assemble-graph-bundle.py --out <scratch>/probe-graphs.json.gz
--repo solidus:<solidus-graph>:<solidus-modularity>
--repo spree:<spree-graph>:<spree-modularity>` command. The recorded bundle SHA256
is `c5bfe09753acccb2652e1ea730079e3269ed47b2ed58ee6d3ea642fc08ff1ffe`.
Original command wrappers, pins, resource records, package versions and producer
hashes document the measurements; recreating SQL/cache state requires reindexing
the same pinned source with its recorded producer, rather than inventing state.
