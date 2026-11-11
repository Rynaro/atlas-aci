# How atlas-aci works

This page holds the depth that the [README](../README.md) leaves out. Read the README first. Install steps are in [installation.md](installation.md), and measurements are in [evaluation.md](evaluation.md).

## The request path

```text
MCP host (Claude Code, Copilot, Cursor, ...)
  | stdio, or loopback HTTP at /mcp (optional)
  v
server.py         tools/list, tools/call, central bounds chokepoint
  |
enforcement.py    read-only allowlist, path guard, rate limit, per-call caps, telemetry
  |
tools/*.py        view_file, list_dir, search_text, search_symbol, graph_query, test_dry_run, memex_read
  |                                              |
memex.py          hashed-directory excerpt store
codegraph.py      tree-sitter extraction and the SQLite index under <repo>/.atlas/ (or --graph-root)
```

## Invariants

Each invariant is enforced in code and covered by tests in [`mcp-server/tests/`](../mcp-server/tests/), mainly `test_enforcement.py` and `test_server.py`.

1. **Read-only allowlist.** A tool name outside `READ_ONLY_TOOLS` returns `FORBIDDEN` before any tool code runs. The set is a `frozenset` literal in [`enforcement.py`](../mcp-server/src/atlas_aci/enforcement.py).
2. **Path containment.** Every path is resolved, symlinks included, and must stay under the repo root. The check is `path_is_within` in [`config.py`](../mcp-server/src/atlas_aci/config.py). Anything outside returns `FORBIDDEN`.
3. **Mechanical bounds.** Per-call caps live in `enforcement.py`. The central chokepoint, `apply_central_bounds` in [`server.py`](../mcp-server/src/atlas_aci/server.py), caps every declared list field to 200 elements on a whole-element boundary. It marks the result `truncated`, with `truncated_fields` and `returned_count`, and sets `more_available`. A response still over 1 MiB after capping is refused with `RESPONSE_TOO_LARGE`. A tool can narrow a bound but never widen one.
4. **Structured errors.** Every error carries a `code`, a `message` and a `retry_hint` of `none`, `narrower_scope` or `different_tool`.
5. **Telemetry.** Each call logs its tool, its arguments except the `content` field, bytes out, duration, overflow and error code. The same records are kept in `Enforcement.records`, a list you can drain into your own sink.

Rate limiting is a sliding window of 200 calls per minute for the whole process. Calls over the limit get a structured error.

## Test execution is opt-in

`test_dry_run` is off by default. A request gets a deterministic `FORBIDDEN` before any path inspection or subprocess creation. Request fields and repository files cannot turn it on. To enable it for a trusted repository, start the server with this exact value:

```sh
ATLAS_ACI_ENABLE_TEST_EXECUTION=1 atlas-aci serve --repo /path/to/repo
```

Only `1` enables it. Missing, empty, `0`, `false` and malformed values leave it off. The setting is read once at startup. Programmatic callers may pass `Config(test_execution_enabled=True)`, and an explicit `False` overrides the environment.

The tool stays visible in `tools/list`. Its description and `_meta["atlas-aci/test_execution_enabled"]` report the active state. When enabled, it keeps the supported runners (pytest, Minitest through `bundle exec rails test`, and Jest), case filtering, a case selector that may not start with `-`, path containment, the 30 s timeout, separate 8 KiB stdout and stderr caps, and the response ceiling.

On timeout, Atlas kills and reaps the immediate child process. It does not clean up descendants.

**Security boundary.** Enabled tests run repository code with the server's permissions, and they may write files or use the network. A timeout and output caps are not an OS sandbox. For untrusted code or models, run the server in a DevContainer or microVM with filesystem and network limits. See [SETUP.md §8](../SETUP.md#8-production-hardening-checklist).

## External graph storage

`index`, `serve`, `status`, `export` and `import` accept `--graph-root PATH`. Without it, the graph lives in `<repo>/.atlas`. There is no environment variable that chooses the graph root. The root must sit outside the source checkout. Each resolved source path, including each Git worktree, gets its own namespace at `<graph-root>/repos/<SHA-256 of the resolved source path>`. Symlink aliases of one source share a namespace. `--memex-root` stays independent.

```bash
atlas-aci index --repo /path/to/source --graph-root /path/to/graphs
atlas-aci serve --repo /path/to/source --graph-root /path/to/graphs --memex-root /path/to/memex
atlas-aci status --repo /path/to/source --graph-root /path/to/graphs
```

Index and serve must use the same canonical source path. A host path and a container path are different identities, so `/home/project` on the host and `/repo` in a container do not match. Moving a checkout changes its namespace. A portable export can be imported into any root, because the root does not enter canonical facts.

Storage is operator-owned. This is not a defense against a hostile process racing symlinks inside the storage tree.

For Docker, prepare `/path/to/graphs` and `/path/to/memex` so that UID 10001 can write to them. Keep the source read-only in both commands.

```bash
docker run --rm --read-only --network none -v "$PWD":/repo:ro \
  -v /path/to/graphs:/graphs -v /path/to/memex:/memex \
  atlas-aci index --repo /repo --graph-root /graphs
docker run --rm -i --read-only --network none -v "$PWD":/repo:ro \
  -v /path/to/graphs:/graphs:ro -v /path/to/memex:/memex \
  atlas-aci serve --repo /repo --graph-root /graphs --memex-root /memex
```

`status`, `export` and `serve` open graph state read-only, and they create no graph directory when the index is missing. The Dockerfile in this repository prefetches the shipped parser languages at build time. Indexing therefore needs writable graph storage, not a writable source tree or a parser download. Do not mask `/tmp/.cache` with an empty runtime mount.

## Extraction cache

Index writers store the raw extraction for each source file under the repository's graph namespace, at `extraction-cache/v1/<prefix>/<key>.json`. The key covers the exact source bytes consumed, the relative path, the language, the schema, the producer and parser fingerprints, and the extraction configuration. Full builds and new writers reuse valid entries without parsing.

Global bindings are always recomputed, so a changed or removed dependency still changes query results. Entries are written atomically and validated on read. Missing, partial, corrupt or incompatible entries are reparsed from the source. Cache I/O failures fall back to extraction and appear in the build diagnostics. Serve, status, export and import never read or create the cache.

The cache checks integrity, not authenticity. An owner who can fabricate a fully consistent replacement can fool it. No eviction policy and no speed guarantee are provided.

Build output reports `extraction_cache` counters: disk hits, misses by reason, write failures, predecessor reuse and parser invocations. These values never enter canonical facts, observation identities or portable exports.

## Conservative incremental binding

`index --since <marker>` skips files whose `(mtime_ns, size)` is unchanged since the last pass. The marker is never read as a git ref. Any truthy value works.

Incremental builds keep binding witnesses in the graph manifest. Each active source path is one binding unit. A unit depends on complete language-level buckets of declarations, lexical blockers, imports and stale names. Python also depends on the full module and package path universe. The buckets include negative lookups, so a newly appearing target or an ambiguity invalidates old results. A declaration or scope change may rebind every unit in that language. A call-only edit with unchanged selection inputs can preserve other units in the same language. Full builds always rebind every unit.

Reuse requires the same producer and configuration, complete input and output witnesses, and a validated closure. Missing, malformed, unknown or incoherent metadata triggers an explicit full binding. Corrupt predecessor facts are restored through full extraction first. Witnesses never enter canonical facts, and readers never repair them.

Incremental mode still discovers and hashes source files. It is not a speedup for every edit.

Build output reports `binding_resolution` with its mode and reason, and the counts and paths of affected, reused and removed units.

## Graph-linked source snapshots

Graph responses include `snapshot_id` and `source_available`. Pass a non-null snapshot id to `view_file` to read the bytes that the graph generation used:

```json
{"path": "src/example.py", "start_line": 1, "end_line": 40, "snapshot_id": "..."}
```

For historical evidence, also pass `source_sha256`. A missing, pruned, foreign or corrupt snapshot fails with an explicit error. It never falls back to live bytes. Without a snapshot id, `view_file` reads the live file and labels the result `source_mode: live`. Line, list and byte caps apply in both modes.

A graph request holds one read-only SQLite handle and one immutable source-pack descriptor. A publisher may unlink old snapshot paths while an acquired reader keeps reading its original bytes. Idle entries keep the current generation and one previous generation. Active readers keep unlinked storage until their descriptors close. This is **not** a global disk-byte bound. There is no TTL, no reader lease file and no background service. Supported storage is the tested POSIX local-filesystem envelope.

Capture is a deterministic per-file byte vector, not an atomic snapshot of the whole filesystem. Unsafe source symlinks abort publication.

The Python build API also accepts `overlays`, a mapping of relative paths to bytes. It indexes unsaved drafts without writing them into the checkout:

```python
graph.build(overlays={"new/unsaved.py": b"def draft(): pass\n"})
```

There is no implicit editor read and no CLI flag for overlays.

## Rust structural navigation

Rust source is indexed with the locked Tree-sitter Rust grammar. Nothing is compiled. The indexer never runs Cargo, `build.rs`, procedural macros or a compiler.

The producer records modules, structs, enums, traits, free functions, and direct impl and trait methods, once per source occurrence. Each occurrence keeps the exact consumed source hash and byte spans. Impl owners use token-normalized literal headers, so comments and whitespace outside literals do not change identity, while literal contents and generic bounds do.

Calls keep their raw spelling and any syntactic qualifier. Every Rust call is unresolved, with `rust_semantic_binding_unavailable`. A matching name does not establish a function, associated method, trait or dispatch target. Capability snapshots from `atlas-capabilities.v3` onward report this boundary explicitly.

Macro bodies, derive output and unmaterialized generated code are unavailable. Materialized `.rs` files are ordinary input. Declarations under `cfg` are recorded as source syntax, and conditions are not evaluated, so an indexed declaration does not prove inclusion in a binary. The grammar is locked, and the Dockerfile in this repository prefetches it so that offline indexing needs no download.

## Status

```bash
atlas-aci status --repo /path/to/repo
```

`status` is CLI only. It prints canonical, key-sorted JSON on one line. It opens one read-only generation lease, reads one attempt snapshot and writes nothing. A missing or incompatible index is reported with a null `generation_id` and `INDEX_UNAVAILABLE`, and the command still exits 0.

The output separates two things. `runtime` describes the installed structural producer. `indexed` describes what the indexed generation persisted: its capabilities, language selection, producer fingerprint and coverage identifier. Languages disabled by `--langs` are unsupported for that generation, even when they are available at runtime.

Recognized Go and Java extensions have no extraction query. SCSS `@include` produces a `call` reference. HTML ids, YAML keys and Markdown headings provide symbols only. A `partial` result means bounded Tree-sitter syntax or lookup support. `unsupported` means that no enabled provider supplies the operation.

Enumeration and extraction are reported separately. Every recognized source file records its observed hash and an outcome of indexed, failed, unsupported or disabled. A file whose syntax tree needed recovery is quarantined as a whole, and the generation is published as partial. No subtree of such a file supplies new facts.

When a previously indexed file fails extraction, its exact historical facts are retained separately, with their original hashes, byte spans and producer identifiers. They appear with explicit stale freshness. They never supply current bindings or confident analysis.

A failed refresh keeps the previous immutable generation and records a failure observation under the writer lock. Fail-fast counts cover only the files observed before the stop. Remaining files are reported as `not_processed`, not as freshly verified.

## Export and import

```bash
atlas-aci export --repo /path/to/your/repo .atlas/export/graph-export.jsonl
git add .atlas/export/graph-export.jsonl && git commit -m "Add graph export"

# on a fresh checkout:
atlas-aci import --repo /path/to/checkout .atlas/export/graph-export.jsonl
```

Keep `.atlas/` ignored, but if you commit the export, make sure your `.gitignore` does not exclude that path.

The export is canonical, byte-deterministic JSONL. An import reproduces a current-epoch database without parsing any source file. Import is idempotent, so repeated imports of the same file produce the same database. It rejects a truncated, hand-edited or wrong-epoch file with a clear error. It also checks that every path in the file is repository-relative and stays inside the repository before it inserts a row. The content hash proves that bytes were not truncated. It proves nothing about who wrote them, so the path check is separate and mandatory.

**Why neither is a tool.** `import` replaces the entire index from bytes on disk. An agent that could call it could swap every later `search_symbol` or `graph_query` answer. A hand-made file with a fresh hash passes the same check as a real export, so the format cannot tell the two apart. `export` is a closer call. It never writes to the index, but it writes a file at a path the caller chooses. An export tool would be the first exception to the rule that no tool writes files. Both commands are operator-run, and both stay on the CLI.

**Conflicts.** Regenerate, do not merge. There is no semantic merge driver. Discard the conflicted file and run `index` and `export` again.

**Cross-OS hazards.** CI builds one small fixture on macOS and on Linux and fails the build if the two exports differ by a single byte. Windows backslash-separated relative paths are outside that check, and the format does not close that hazard. Unicode normalization (macOS usually presents filenames as NFD, Linux as NFC) and filenames that differ only in case are documented, not mechanically handled.

### Export size ceiling (AC-REL-2)

GitHub rejects a committed file of 100 MiB or more. `atlas-aci export` writes a warning to stderr once the output reaches 50 MiB and another once it reaches 100 MiB. The thresholds are `_GITHUB_FILE_WARN_BYTES` and `_GITHUB_FILE_HARD_LIMIT_BYTES` in [`__main__.py`](../mcp-server/src/atlas_aci/__main__.py). The export never fails because of its size.

If an export crosses the ceiling, you have three options. Compress it outside the repository (for example `gzip -9`) and decompress it before import. Publish it as a CI artifact instead of committing it. Or re-index instead of importing. `index` has no such limit. Only the portable, committed artifact does.

## HTTP transport

`serve --transport http` starts a loopback-only Streamable HTTP endpoint at `POST /mcp`. The default address is `127.0.0.1:8765`. Responses are server-sent events. The transport is stateless, with no sessions.

There is no authentication. Any local process that can reach the port can call every tool. For that reason, `serve --transport http` refuses to start while `ATLAS_ACI_ENABLE_TEST_EXECUTION=1` is set. Requests are checked in a fixed order (peer, Host, Origin, path, method, admission, body) and rejected rather than queued. A client disconnect cancels the request in flight. That deviates from the MCP specification on purpose, so that abandoned queries do not hold a reader lease. Graph reads run on worker threads, so a disconnect interrupts the running SQLite statement, and every `graph_query` and `search_symbol` call has a server-side deadline (`GRAPH_DEADLINE_EXCEEDED`, 20 seconds by default). The first lease of a process authenticates the published generation once and remembers the result while the same files stay in place; `serve` does that in the background at startup. The full rules are in [hosts/http-transport.md](../hosts/http-transport.md).

For several principals, `serve-shared` serves registered repositories to authenticated principals over loopback HTTP, with offline JWT verification. See [docs/operations/shared-http-deployment.md](operations/shared-http-deployment.md).

## Known limits

- The index is structural. There is no type inference, no compiler semantics, and no module resolution for TypeScript or TSX imports.
- Callers of Ruby setters and operators are invisible to `callers_of`. Their definitions are captured.
- Go and Java files are recognized and skipped, and the skip is reported.
- Symbols from HTML, YAML and Markdown are not code references.
- Macros, generated code and `cfg` conditions are not evaluated.
- Windows is not part of the export determinism check.
- Test execution has no OS sandbox. Its timeout and caps do not isolate it.
