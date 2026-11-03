# S16 exploration (pre-plan observation, not a product fixture)

- `index.scip`: produced by `@sourcegraph/scip-typescript@0.4.0` (npm, `--save-exact`)
  in `node:22-slim` over `greeting.ts`/`main.ts` (`--cwd /x/proj`).
- `scip.proto`: `scip-code/scip` tag v0.6.1 (commit 9d5796159c97c3b107355c424f6c06fb1a0ea01c).
- `decoded.txt`: official protobuf runtime 5.27.2 decoding via protoc-generated bindings
  (grpcio-tools 1.66.2), script `decode.py`.
- Observed: no `Document.position_encoding`; UTF-16 code-unit offsets; Definition role
  bit only; no symbol kinds/relationships; external TypeScript lib symbols as references.
