# ACI-S03 execution evidence

The initial production-dispatch reproduction (`cached-reader-red.log`) validates the replacement pathname but reads the old cached inode. The corrected implementation opens one validated read-only SQLite lease per request and stages full, incremental, and import publication under the same writer lock.

`full.log` records 274 passing tests; `targeted.log` records 77 compatibility tests; final focused coverage records 26 cases. Each of the six named product criteria has its own command, true return code, and evidence path in the authoritative receipt. `vivi-pass2-result.json` records two successful anchored runs with no test-tamper rejection. `probe-selftest.result.json` checks both the direct return code and the explicit 27-control completion marker.

`metadata-and-allocation-red.log` and `import-initialization-red.log` preserve actual failing regression anchors before the required fixes. The final malformed-provenance, staging-failure, empty-source/import, controlled-cancellation, real-closed-handle, and concurrent-reader/writer controls use real parser and SQLite behavior, not fabricated graph rows. Independent review results are attributed in `independent-review.json`.

The existing S02 behavioral fingerprint and measured probe are unchanged; no new measurement or alteration of prior slice artifacts was needed. `format-equivalence.json` establishes AST equivalence between the reviewed source and the final adjacent-string formatting change.

One helper invocation used an unsupported EARS option and returned 2 (`ears-final.log`); the corrected positional invocation returned 0 (`ears-corrected.log`). An initial final-fingerprint wrapper omitted uv from its process environment and stopped before the self-test; the corrected wrapper produced `fingerprint-after.json` and the complete self-test record. Neither failure was counted as a passing gate.

Cancellation evidence covers cancellation at a controlled await while the actual lease is live; it does not claim SQLite synchronous-query preemption or live MCP cancellation. This slice preserves existing SQLite read-only deployment behavior and does not introduce an operating-system sandbox. Canonical identity describes the exact buffers consumed rather than an atomic external source-tree snapshot.
