# Importing an official SCIP index (scip-typescript)

`atlas-aci import-scip` publishes compiler-grade definition and reference facts
from **one** producer, `@sourcegraph/scip-typescript` **0.4.0**, next to Atlas's
structural graph. It is an operator CLI command only: nothing is added to the
seven read-only MCP tools, nothing runs a compiler in the server, and the
imported facts are read through the existing tools like any other evidence.

The wire decoder is Atlas's own strict, dependency-free implementation of the
vendored, hash-pinned schema `mcp-server/src/atlas_aci/providers/scip.proto`
(scip-code/scip tag `v0.6.1`, commit `9d5796159c97c3b107355c424f6c06fb1a0ea01c`,
sha256 `b855eb9abdb3fe7f07600ee92f24a6c40f75fceef37136ccdc6844b300936a31`).
The official `protobuf` package is a **dev-only** extra used as a differential
test oracle; the runtime dependency set is unchanged.

## Workflow

```bash
# 1. Index the repository as usual (structural graph, `build` generation).
atlas-aci index --repo /path/to/repo

# 2. Run the pinned producer over the SAME working tree (no Atlas involvement).
npx --yes @sourcegraph/scip-typescript@0.4.0 index --output index.scip

# 3. Right after the producer, record producer-time source identity.
atlas-aci scip-manifest --repo /path/to/repo --position-encoding utf-16 \
    --output atlas-scip-manifest.json index.scip

# 4. Import. Validation completes before the writer lock; failure changes nothing.
atlas-aci import-scip --repo /path/to/repo --manifest atlas-scip-manifest.json index.scip
```

`--graph-root` works on `import-scip` exactly as on `index`. A reproducible,
digest-pinned producer run (Docker `node:22-slim`, npm lockfile with integrity
hashes) lives in `mcp-server/tests/fixtures/scip/generate.sh`.

## The sidecar: `atlas-scip-manifest.v1`

A SCIP index records neither source hashes nor how its columns are counted, and
the producer's `project_root` is a host path. Source identity therefore comes
from a sidecar written at producer time:

```json
{"format":"atlas-scip-manifest.v1",
 "producer":{"name":"scip-typescript","version":"0.4.0"},
 "position_encoding":"utf-16",
 "index_sha256":"<sha256 of the exact artifact bytes>",
 "documents":{"src/a.ts":"<sha256 of the file as the producer read it>"}}
```

`scip-manifest` reads the index (producer name/version come from the index, never
from a flag), hashes every document in the working tree, and refuses documents it
cannot read. The manifest is a closed object with no duplicate keys; anything
missing, unparseable, foreign (`index_sha256` mismatch) or incomplete (document
set differs from the index) is rejected and the artifact stays unpublished.

## Position encoding is declared, never guessed

`--position-encoding` is required and has no default. Permitted encodings are
pinned per producer version: scip-typescript 0.4.0 counts **UTF-16 code units**
and never sets `Document.position_encoding`, so only `utf-16` is accepted. A
document that does set an encoding must equal the manifest's. Columns are
converted to UTF-8 byte offsets against the exact snapshot bytes (TypeScript line
rules: LF, CRLF, CR, U+2028/U+2029; a UTF-8 BOM is skipped). As a mechanical
wrong-encoding detector every definition span must spell its descriptor name
(producer conventions accepted: the zero-width file-module span at 0:0, quoted
property names, `<constructor>`). Example from the fixture: `saudação` after an
emoji starts at UTF-16 column 43 = byte column 45 (UTF-8 read of 43, or 42 code
points, would land elsewhere and be rejected). Atlas `line` is 1-based and
`column` is the byte column (tree-sitter convention).

## What is imported

* One entity per SCIP symbol **per definition range**: kind `scip_<namespace|type|
  term|method|type_parameter|parameter|meta|macro|local>`, name from the last
  descriptor, `owners` encoding package, descriptor chain and method
  disambiguators (two `(p)` parameters never merge; a symbol defined twice gets
  two entities, with ordinals when in one document). `scip_*` kinds never
  collide with tree-sitter kinds and are excluded from name seeds and structural
  analytics.
* Definition occurrences (`definition:scip_<kind>`, `symbols` rows) and reference
  occurrences (`reference:reference`, `refs` rows whose `callee_name` is the full
  SCIP symbol). Role bits beyond `Definition` are recorded in the binding JSON and
  never become `import`.
* Reference-only occurrences become relation **`reference`**, never `call`:
  `EXTRACTED` to the defining SCIP entity, `AMBIGUOUS` when the symbol has several
  definitions, `UNRESOLVED` (`scip_external_symbol`) when the index does not
  define it. The source entity is the innermost SCIP definition whose
  `enclosing_range` contains the occurrence, else the file.
* Every record carries the producer fingerprint
  `{"scip": {tool, schema_sha256, index_sha256, manifest_sha256, position_encoding,
  importer_sha256}}` (identity basis `parsed`).

## Publication, re-import and lifecycle

The import publishes a derived generation of kind `scip_import` from the current
`build` generation: base facts and snapshot bytes are copied, the SCIP layer is
added, a new source snapshot, retrieval index and coverage are written, and the
database is replaced atomically under the writer lock. The base generation id is
verified again under the lock; a concurrent publication aborts the import.

* Same artifact + manifest onto the same build base: identical generation id and
  canonical records. Onto a `scip_import` generation with the same digests: a no-op
  reporting the same id.
* A different artifact on a `scip_import` generation is rejected: run `atlas-aci
  index` first (the same repository path gives the same base identity again).
* `index` (with or without `--since`) after an import is a full rebuild, so imported
  facts are superseded, never orphaned. A later parse failure never retains SCIP
  records as stale history.
* `atlas-aci export` of a `scip_import` generation round-trips through `import`
  with the same canonical records; the imported generation has kind `import`
  (source-free), not `scip_import`.

## Rejection is all-or-nothing

Any of these rejects the whole import and leaves the published graph
byte-identical (no staging file, same generation id): malformed or oversized
protobuf, unknown producer or version, encoding not permitted, manifest defects,
`index_sha256` mismatch, any document hash differing from the published snapshot,
any document whose path is not observed `indexed` (for example excluded, failed or
language-disabled), out-of-line positions, definition text mismatches, base
generation not `build`. Importing a subset of an index is not supported.

## Status

`atlas-aci status` (CLI) gains `semantic_imports`: producer, version, schema/index/
manifest/importer digests, encoding, counts and limitations. The MCP `status`
schema is unchanged and still reports `semantic_binding: unsupported` — nothing
runs at query time. Its `facts` counts include SCIP rows.

## Limitations

`scip_reference_traversal` (`reference` is not in the traversable relation set, so
`graph_query` navigation, `callers_of`, god nodes and communities are unchanged),
`scip_external_symbols` (external symbol metadata is not imported),
`scip_producers` (only scip-typescript 0.4.0), `scip_relationships` and
`scip_diagnostics` (decoded for validation, not mapped). SCIP symbols are not
mapped onto tree-sitter entities.
