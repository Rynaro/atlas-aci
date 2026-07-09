# Scout report — atlas-aci v2.0.0 pre-planning

Repo: `<repo>`
HEAD: `f56a78e` (tag `v0.4.0`), clean tree, `origin/main` == HEAD.
All `file:line` anchors below are relative to this checkout unless stated otherwise.

---

## Deliverable 1 — Verdicts on the 8 dossier claims

| # | Claim | Verdict |
|---|-------|---------|
| 1 | No confidence tagging on edges | **VERIFIED** |
| 2 | `subclasses_of` is a stub | **VERIFIED** |
| 3 | No incremental indexing | **STALE** |
| 4 | Fused pipeline (detect/parse/extract/write in one `build()`) | **PARTIAL** |
| 5 | No community/module detection | **VERIFIED** |
| 6 | No rationale-node extraction | **VERIFIED** |
| 7 | Three hosts only | **VERIFIED** |
| 8 | Graph DB not a committable artifact | **VERIFIED** |

### 1. No confidence tagging on edges — VERIFIED

`refs` schema (`mcp-server/src/atlas_aci/codegraph.py:155-163`):
```
CREATE TABLE IF NOT EXISTS refs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    callee_name TEXT NOT NULL,
    path        TEXT NOT NULL,
    line        INTEGER NOT NULL,
    enclosing   TEXT,                -- enclosing def name, if known
    lang        TEXT NOT NULL
);
```
No confidence/provenance column. `grep -rni "confidence|EXTRACTED|INFERRED|AMBIGUOUS" mcp-server/src mcp-server/tests README.md CHANGELOG.md` returns zero hits (only unrelated matches for "re-extracted" in changelog prose). `search_symbol` (`codegraph.py:408-423`) and `graph_query` (`codegraph.py:433-462`) both return raw `dict(sqlite3.Row)` rows straight from `refs`/`symbols` — no confidence field is synthesized anywhere in the response path (`tools/search_symbol.py:30`, `tools/graph_query.py:29`). Confirmed real gap, exactly as described.

### 2. `subclasses_of` is a stub — VERIFIED

`codegraph.py:454-460`:
```python
if verb == "subclasses_of":
    # Best-effort; without inheritance edges, return classes whose name appears
    # near the parent. A real implementation extends QUERIES with superclass capture.
    return {
        "edges": [],
        "warning": "subclasses_of requires extended index; not implemented in MVP.",
    }
```
Exact string match to the dossier's quote. No inheritance/superclass capture exists anywhere in the `QUERIES` table (`codegraph.py:74-135`) — Ruby's `class name: (constant) @name` omits the grammar's `superclass:` field, Python's `class_definition name: (identifier) @name` omits `superclasses:`, and neither TS/JS class queries capture `heritage`/`extends` clauses. `grep -rn "superclass|inherit|extends|subclass"` across `mcp-server/src` and `mcp-server/tests` returns only the doc-string self-references quoted above and an unrelated "extends the default index" comment (`codegraph.py:138`). Still true at v0.4.0, unchanged since (no commit touched this).

### 3. No incremental indexing — STALE

Confirmed shipped in v0.4.0. `CHANGELOG.md:9-17` documents commit-equivalent of #16; code lives in `codegraph.py:221-329`.

**What `--since` actually does now:**
- CLI flag (`__main__.py:66-73`) accepts any string as a "marker" — help text: *"Enable incremental re-index: skip files unchanged since the last pass (pass any marker, e.g. HEAD)"*.
- In `CodeGraph.build(self, since: str | None = None)`, the **value** of `since` is never read past a truthiness check: `incremental = since is not None` (`codegraph.py:240`). `grep -n "since" codegraph.py` shows no other use of the variable's contents.
- Key: **`(mtime_ns, size)` per file**, not git SHA, not content hash. New `files` manifest table (`codegraph.py:172-178`): `path PRIMARY KEY, mtime_ns, size, lang, indexed_at`.
- Full mode (`since=None`): wipes `symbols`/`refs`/`files` (`codegraph.py:243-245`), re-indexes everything, records baseline `(mtime_ns, size)` per file.
- Incremental mode: loads `stored_files` map (`codegraph.py:248-252`); for each file on disk, if `stored_files.get(rel) == (mtime_ns, size)` → skip (`codegraph.py:271-275`, counted in `files_skipped`); otherwise purge that file's stale `symbols`/`refs` rows (`codegraph.py:285-289`) then re-extract and re-insert.
- Deletion handling: **yes** — after the file walk, any `stored_files` path not seen on disk this pass has its `symbols`/`refs`/`files` rows deleted (`codegraph.py:314-319`, counted in `files_removed`).
- Ref invalidation for changed files: **yes** — old rows are purged before re-insert per changed file (same lines as above), so no duplicate/stale rows accumulate for a modified file.
- Stats gained `files_skipped`, `files_removed` (`codegraph.py:323-329`), matching the CHANGELOG claim.
- Tests: `mcp-server/tests/test_codegraph.py:225-313` cover skip-unchanged, reindex-changed-without-stale-rows, remove-deleted-file, idempotency-on-repeat-run, and files-manifest-populated-on-full-build. All 5 scenarios pass conceptually per code inspection (test bodies assert exactly the behavior above).

**What remains missing / weak, beyond the dossier's ask:**
- The `since` argument is **not** actually a git ref — passing `--since HEAD~1` (as `INTEGRATION.md:201-208`'s documented post-commit hook does) has zero effect on *which* files get reindexed; it is purely a boolean "turn on incremental mode" sentinel. `INTEGRATION.md:201` states *"The `--since <git-ref>` flag restricts indexing to files changed since a ref"* — this is materially incorrect against the shipped v0.4.0 semantics (see Deliverable 3).
- Change detection is `(mtime_ns, size)` only, no content hash — the CHANGELOG (`CHANGELOG.md:17`) explicitly justifies this ("indexer targets arbitrary directories not guaranteed to be git repos") but it means a file rewritten to identical bytes with a stat-preserving copy (or a filesystem/clock quirk) can silently evade re-indexing, and conversely a `touch` with no content change forces an unnecessary re-extract.
- No stale-edge GC across the *whole* graph: `refs.callee_name` is a bare string with no foreign key to a `symbols.id`, so there is no "dangling edge" concept to begin with — matching is done dynamically by name at query time (`codegraph.py:408-423`, `425-431`). This sidesteps the invalidation problem the dossier worries about, but only because the schema has no real edge objects yet (see claim 1 / Deliverable 3).

### 4. Fused pipeline — PARTIAL

The gap is real but the "one method, no stages" framing overstates it slightly.

- File **detection** (extension → language lookup, `LANG_BY_EXT.get(ext)`), the **tree-sitter `parser.parse()` call**, and the **SQLite `INSERT`s** are all inline in `build()`'s per-file loop (`codegraph.py:261-319`) — detection at `codegraph.py:262-265`, parse at `codegraph.py:277-283`, inserts at `codegraph.py:292-311`. No stage boundary, no intermediate serialization, direct `self.db.execute(...)` calls scattered through the loop.
- However, **symbol/ref extraction from an already-parsed tree** *is* factored into a separate method, `_extract()` (`codegraph.py:344-400`), which takes a `tree`/`source` and returns typed `list[Symbol]`/`list[Reference]` dataclass objects (`codegraph.py:182-198`) — this is a real (if thin) validated-schema boundary between "extract" and "persist." `_extract` itself does not touch `self.db` at all.
- So: **detection + parse + write are fused into `build()`**; **extract is not** — it's a pure function of `(tree, source) → (symbols, refs)`. A stage split would mainly need to (a) factor detection+parse into their own method returning `(lang, tree, source)` or raise a typed "unparseable" result, and (b) factor the SQLite write loop into a `_persist(symbols, refs)` method — both mechanical extractions of existing inline code, not a redesign, since `Symbol`/`Reference` already exist as the natural DTO. Cost is low; the class is single-file (462 lines total) and every consumer only calls `CodeGraph.build()`/`.search_symbol()`/`.query()` (`server.py:160,174-188`), so no external caller depends on the fused shape.

### 5. No community/module detection — VERIFIED

`grep -rni "community|centrality|\bhub\b|degree"` across `mcp-server/src`, `mcp-server/tests`, `README.md`, `CHANGELOG.md` → zero hits. `graph_query`'s DSL is exactly 3 verbs (`codegraph.py:446-462`: `callers_of`, `definitions_of`, `subclasses_of`); no verb answers "what subsystem is X in" or "what are the hubs." No graph-algorithm code (no NetworkX/igraph dependency in `mcp-server/pyproject.toml`).

### 6. No rationale-node extraction — VERIFIED

`grep -rni "rationale|# NOTE|# WHY|# HACK|\bADR\b"` across source/tests/docs → only a `README.md:107` heading "## Why read-only" (unrelated). None of the 9 language `QUERIES` entries (`codegraph.py:74-135`) capture comment nodes of any kind — every pattern targets `class`/`method`/`function`/`call`/mixin/selector/key/heading/etc. definition or reference nodes, never `comment`. No ADR-reference capture exists.

### 7. Three hosts only — VERIFIED

`hosts/` contains exactly three files: `hosts/claude-code.md` (104 lines), `hosts/copilot.md` (116 lines), `hosts/cursor.md` (113 lines) — confirmed via directory listing. No `AGENTS.md`-style generic install doc and no `~/.agents/skills/` cross-framework path anywhere in this repo (that generic-install concept exists at the EIIS spec level in the separate `eidolons-eiis` repo, but atlas-aci does not implement/reference it — see Deliverable 3 on eidolons wiring).

### 8. Graph DB not a committable artifact — VERIFIED

- `.gitignore:14-15`: `# Atlas index artifacts` / `.atlas/` — the whole index directory is gitignored.
- `config.py:25` also lists `.atlas` in `DEFAULT_SKIP_PATTERNS` (excluded from `list_dir`/`search_text` at runtime, separate from the git-level exclusion).
- `db_path = self.repo / ".atlas" / "graph.db"` (`codegraph.py:207`) — a bare per-machine SQLite file, opened with a plain `sqlite3.connect` (`codegraph.py:214`), no `VACUUM INTO`, no `.dump`, no JSON/portable export.
- `__main__.py`'s `cli` group has exactly three subcommands — `serve`, `index`, `tools` (`__main__.py:38,56,86`) — no `export`/`import`/`snapshot` verb.
- The only documented workaround is manual: `INTEGRATION.md`'s Strategy C (`tar czf atlas-index.tgz .atlas/` in a GitHub Actions step) — an ad hoc, undocumented-in-code convention, not a first-class feature, and it ships a machine-specific SQLite file (absolute-path-free but still opaque binary) rather than a portable/versioned snapshot format.

---

## Deliverable 2 — Architecture map

### SQLite schema (`codegraph.py:142-179`)

```sql
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- class | module | method | function
    path        TEXT NOT NULL,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    lang        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);

CREATE TABLE IF NOT EXISTS refs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    callee_name TEXT NOT NULL,
    path        TEXT NOT NULL,
    line        INTEGER NOT NULL,
    enclosing   TEXT,                -- enclosing def name, if known
    lang        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refs_callee ON refs(callee_name);

CREATE TABLE IF NOT EXISTS manifest (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    mtime_ns    INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    lang        TEXT NOT NULL,
    indexed_at  TEXT
);
```
(`codegraph.py:142-179`, all `CREATE ... IF NOT EXISTS`, applied via `self._db.executescript(SCHEMA)` on every connection open — `codegraph.py:214-215`.)

`manifest` table's **actual** contents: exactly one row is ever written, `('version', '1')`, at the end of every `build()` call (`codegraph.py:321`). Nothing else reads or writes `manifest` — `grep -n "manifest" codegraph.py` shows no consumer of the `version` key; it is write-only telemetry with no migration logic gated on it (see Deliverable 3, "no schema version enforcement").

`refs.enclosing` is declared and indexed for future use but **always `NULL`** in practice — `_extract()` hardcodes `enclosing=None` for every `Reference` it builds (`codegraph.py:395`), and nothing else populates it. No caller-context (which method the call site is inside) is actually captured despite the column existing.

`symbols.kind` values are not just `class|module|method|function` as the column comment says — the SCSS/HTML/YAML/Markdown/Bash queries introduce `mixin|function|placeholder|variable|selector|id|key|heading` kinds too (`codegraph.py:104-134`, confirmed by `search_symbol.py`'s tool schema enum only listing `class|module|method|function`, `server.py:99` — so the MCP-facing `kind` filter enum in the tool manifest is **stale relative to the query table**: a caller cannot filter `search_symbol` by `kind=mixin` even though such symbols exist in the index).

### Tree-sitter query set (`codegraph.py:35-139`)

Languages (9, keyed by `LANG_BY_EXT` → `QUERIES`, `codegraph.py:35-139`): `ruby, python, javascript, typescript(tsx separately mapped to .tsx but no `QUERIES["tsx"]` entry — see below), scss, html, yaml, markdown, bash`. `DEFAULT_LANGS = tuple(QUERIES)` (`codegraph.py:139`) so the default index set is derived mechanically from `QUERIES`' keys (9 entries — `ruby, python, typescript, javascript, scss, html, yaml, markdown, bash` in dict-insertion order).

**Gap found in passing:** `LANG_BY_EXT[".tsx"] = "tsx"` (`codegraph.py:41`) but `QUERIES` has no `"tsx"` key — only `"typescript"` and `"javascript"`. In `build()`'s loop, `lang not in QUERIES` short-circuits the file (`codegraph.py:264`), so **`.tsx` files are silently never indexed** despite `.tsx` being a recognized extension. Also `.go`, `.rs`, `.java` are mapped in `LANG_BY_EXT` (`codegraph.py:42-44`) with no corresponding `QUERIES` entries — same silent no-op. `test_known_web_extensions_are_recognized` (`test_codegraph.py:205-211`) only asserts extension→lang mapping exists, not that a query exists for it, so this gap has no test coverage.

Capture kinds: every pattern uses `@def.<kind>` (paired with `@name`) for definitions or `@callee` (optionally with `@ref.*`) for references — documented at `codegraph.py:66-73`. `_extract()` (`codegraph.py:344-400`) iterates `QueryCursor(query).matches(tree.root_node)` (matches, not raw captures, since the 0.3.0 change per `CHANGELOG.md:33-34`) so `#eq?`/`#match?` predicates apply per-pattern.

Ruby specifically: `codegraph.py:75-81` — `class`/`module`/`method`/`singleton_method` defs plus `call` refs, all via the vendor-neutral `tree-sitter-ruby` grammar bundled through `tree-sitter-language-pack`. No specialist Prism-based Ruby handling exists in this codebase despite being referenced repeatedly in docs (module docstring `codegraph.py:9-11`: *"For Ruby-heavy repos, the recommended production deployment also runs `prism-codegraph` as a separate MCP server with deeper Ruby semantics"*; also `SETUP.md:20,218,264`, `INTEGRATION.md:112`, `mcp-server/pyproject.toml:22-23` commented-out `ruby` extra). `grep -rn "prism"` across all source finds zero executable code — only doc references and a commented-out `pyproject.toml` extras stanza. `prism-codegraph` is not a module, script, or dependency anywhere in this repo (see Deliverable 3).

### `graph_query` DSL (`codegraph.py:433-462`)

Dispatched entirely inside `CodeGraph.query(self, dsl: str)`:
1. Split on first `:` → `verb, arg`; missing `:` → `{"error": "INVALID_QUERY", "message": "Expected 'verb:argument' form."}` (`codegraph.py:440-441`).
2. `callers_of:<Class#method or bare method>` — strips `Class#` prefix if present (`codegraph.py:447-448`), returns `{"edges": [{"path","line","enclosing"} ...]}` from `refs` filtered by `callee_name` (`codegraph.py:425-431`) — **no `LIMIT`**, fully unbounded result set.
3. `definitions_of:<name>` — delegates to `search_symbol(arg)` (`codegraph.py:451-452`), returning `{"definitions": [...], "references": [...]}` — `definitions` unbounded, `references` capped at 200 (`codegraph.py:419`).
4. `subclasses_of:<name>` — stub, see claim 2.
5. Unknown verb → `{"error": "UNKNOWN_VERB", "message": f"Unknown verb {verb!r}."}` (`codegraph.py:462`).

Response shape is inconsistent across verbs: `callers_of`→`{"edges": [...]}`, `definitions_of`→`{"definitions": [...], "references": [...]}`, `subclasses_of`→`{"edges": [...], "warning": "..."}` — no shared envelope.

The MCP-facing wrapper `tools/graph_query.py:13-37` adds only an index-existence precondition check (`graph_query.py:22-27`) and telemetry (`graph_query.py:31-36`) — it does **not** call any `enforcement.cap_*` helper before returning `result`, so nothing on the `graph_query` path actually bounds output size (see Deliverable 3).

### MCP tool surface (`server.py` + `tools/`)

7 tools registered via `build_tool_manifest()` (`server.py:31-152`) and dispatched in `_call_tool` (`server.py:167-212`):

| Tool | File | Params (schema) | Response shape |
|---|---|---|---|
| `view_file` | `tools/view_file.py:36-137` | `path` (str, req), `start_line`/`end_line` (int, req) | `{"path","start_line","end_line","lines":[...]}` + optional `next_cursor`/`total_lines`; or `{"error","message","retry_hint"}` for `BINARY_CONTENT`/`OVERFLOW` |
| `list_dir` | `tools/list_dir.py:14-71` | `path` (str, req), `glob` (str, opt) | `{"path","entries":[{"name","kind","size","mtime"}]}` + optional `overflow`/`message` |
| `search_text` | `tools/search_text.py:18-132` | `pattern` (str, req), `scope` (str, req), `regex` (bool, default True), `limit` (int, ≤50) | `{"matches":[{"path","line","col","preview"}]}` + optional `overflow`/`message` |
| `search_symbol` | `tools/search_symbol.py:13-41` | `name` (str, req), `kind` (enum, default `any`) | `{"definitions":[...],"references":[...]}` |
| `graph_query` | `tools/graph_query.py:13-37` | `query` (str, req) | verb-dependent, see above |
| `test_dry_run` | `tools/test_dry_run.py:27-107` | `path` (str, req), `case` (str, opt) | `{"exit_code","stdout","stderr","truncated"}` |
| `memex_read` | `server.py:185-190`, `memex.py:53-70` | `ref` (str, pattern `^memex://excerpt/[a-f0-9]+$`, req) | `{"ref","content"}` |

All calls funnel through `enforcement.assert_read_only(name)` then `enforcement.assert_rate_limit()` (`server.py:170-171`) before dispatch; errors from `ToolError` and generic `Exception` are both caught and serialized to a uniform `{"error","message","retry_hint"}` JSON body (`server.py:196-212`), never raised to the MCP transport as protocol-level errors.

### `enforcement.py` and `config.py` — what's mechanically enforced vs. documented

Mechanically enforced (code path, not just docstring):
- **Read-only allowlist**: `assert_read_only` (`enforcement.py:81-88`) checks tool name against `READ_ONLY_TOOLS` frozenset (`enforcement.py:26-36`); called unconditionally for every call before dispatch (`server.py:170`). Test-pinned (`test_enforcement.py:33-63`).
- **Path-traversal guard**: `Config.is_in_repo` (`config.py:58-65`) resolves symlinks and requires `.relative_to(self.repo)` to succeed; `Enforcement.assert_path_in_repo` wraps it into a `ToolError` (`enforcement.py:90-96`). Actually invoked by `view_file` (`view_file.py:53`), `list_dir` (`list_dir.py:25`), `search_text` (`search_text.py:52`), `test_dry_run` (`test_dry_run.py:38`) — **not** invoked by `search_symbol`/`graph_query` (they take no filesystem path argument, so this is not a gap) nor by `memex_read` (memex resolves purely by content hash, `memex.py:34-36`, so also not a gap). Test-pinned (`test_enforcement.py:74-89`, including a `../../etc/passwd` traversal case).
- **Rate limiting**: sliding 60s window via `deque` (`enforcement.py:100-114`), `max_calls_per_minute=200` default (`config.py:49`), invoked once per call in `server.py:171` — applies uniformly to all 7 tools regardless of type. Test-pinned (`test_enforcement.py:125-135`).
- **Mechanical bounds — but inconsistently wired**: `cap_lines`/`cap_matches`/`cap_entries`/`cap_bytes` (`enforcement.py:118-138`) are called by `view_file` (`view_file.py:75,105`), `list_dir` (`list_dir.py:32`), `search_text` (`search_text.py:28`), and indirectly `test_dry_run` (byte-slicing directly against `config.max_bytes_per_call`, `test_dry_run.py:88-91`, without going through `enforcement.cap_bytes`). **`search_symbol` and `graph_query` call none of `enforcement`'s cap helpers** — confirmed by reading both tool wrappers end to end (`tools/search_symbol.py:13-41`, `tools/graph_query.py:13-37`): they call `enforcement.record(...)` for telemetry only, never `cap_entries`/`cap_matches`. This means `search_symbol`'s `definitions` list and `graph_query`'s `callers_of`/`definitions_of` results are **unbounded** at the tool layer, contradicting the README's claim (`README.md:107-111`, security invariant #3): *"Mechanical bounds. Line / entry / match / byte caps ... are applied per call in `enforcement.py`. Tools can only narrow bounds, never widen them."* This is true for the filesystem-facing tools but false for the two graph-facing tools. See Deliverable 3.
- **Structured errors**: `ToolError(code, message, retry_hint)` (`enforcement.py:39-53`) with `retry_hint ∈ {none, narrower_scope, different_tool}`, consistently used across every tool's `raise` sites.
- **Telemetry**: `Enforcement.record`/`telemetry_summary` (`enforcement.py:142-188`) is genuinely invoked by every tool wrapper (`view_file.py:62,81,106,130`; `list_dir.py:64`; `search_text.py:125`; `search_symbol.py:35`; `graph_query.py:31`; `test_dry_run.py:100`) — but it is **in-memory only** (`self.records: list[ToolCallRecord]`, `enforcement.py:77`) with an explicit code comment *"replace with sink in prod"* — nothing persists it; a process restart loses all telemetry. `memex_read`'s dispatch path in `server.py:185-190` does **not** call `enforcement.record` at all — the only one of the 7 tools with no telemetry call site.

Documented-but-not-code-enforced (per README/SETUP explicit caveats, not code):
- `test_dry_run` sandboxing — README/SETUP/module docstring (`test_dry_run.py:1-14`) are explicit this is **not** enforced by code, operator responsibility only.
- Read-only-at-the-OS-level via `docker run --read-only` — a deployment convention, not a code guarantee (`README.md:378-397`).

### `memex.py` — what it is / relation to the graph

Content-addressable, hashed-directory KV store (`memex.py:27-77`) — SHA-256 of content is the key (`memex.py:39`), two-level directory fanout (`h[:2]/h[2:4]/h`, `memex.py:34-36`) to bound directory size. `write()` is idempotent (`if not p.exists(): p.write_bytes(...)`, `memex.py:42-43`) and can merge JSON metadata sidecars (`memex.py:44-50`). `read()` validates the ref format strictly (`^memex://excerpt/[a-f0-9]{64}$`-equivalent hand check, `memex.py:60-62`) and raises `NOT_FOUND` `ToolError`s for malformed or missing refs.

**Relation to the code graph: none, structurally.** Nothing in `codegraph.py` calls `Memex.write()` — `grep -n "Memex\|memex"` in `codegraph.py` returns zero hits. The graph's `search_symbol`/`graph_query` responses never emit a `memex://` ref. Despite the tool manifest description claiming *"Refs are returned by other tools when they cite source content"* (`server.py:138-139`), no tool in this codebase (`grep -rn "memex.write\|memex_root\|Memex(" mcp-server/src` limited to `server.py:159` instantiation and CLI wiring) actually produces a memex ref for the agent to later `memex_read`. The MVP docstring (`memex.py:1-5`) frames it as swappable for `sqlite-vec` "same public interface" — an aspiration, not implemented (`sqlite-vec` is an optional extra in `pyproject.toml:25-27` but no code imports it).

### Test coverage

- `mcp-server/tests/test_codegraph.py` (313 lines): pins the SCSS/HTML/YAML/Markdown/Bash/Ruby symbol-kind extraction, the `_site`/skip-list exclusion, extension recognition, and — thoroughly — the 5 incremental-indexing scenarios (skip-unchanged, reindex-changed-no-stale-rows, remove-deleted, idempotent-rerun, files-manifest-populated). Does **not** test `graph_query`'s DSL dispatch (`callers_of`/`definitions_of`/`subclasses_of`/error paths) at all — only `CodeGraph.search_symbol()` is exercised directly; `CodeGraph.query()` and `CodeGraph.callers_of()` have zero direct test coverage (`grep -n "\.query(\|callers_of" test_codegraph.py` → no hits).
- `mcp-server/tests/test_enforcement.py` (151 lines): pins the read-only allowlist exhaustiveness, forbidden-tool rejection, path-traversal (including dotdot), all 4 cap helpers, rate-limit accept/reject, and telemetry aggregation. Solid 1:1 coverage of every `Enforcement` method.
- **No test file exists for `server.py`** (MCP protocol wiring, `_call_tool` dispatch, error-to-JSON serialization), **`memex.py`** (write/read/metadata round-trip), or any of the 6 individual tool wrapper modules in `tools/` (`view_file.py`, `list_dir.py`, `search_text.py`, `search_symbol.py`, `graph_query.py`, `test_dry_run.py`) — confirmed via `find mcp-server/tests -type f` → exactly the two files above, nothing else. The enforcement *primitives* are tested; their *wiring into each tool* (does `view_file` actually call `cap_lines`? does `search_symbol` actually skip `cap_entries`, as found above?) is untested and was only discoverable by manual code reading.

---

## Deliverable 3 — Gaps the dossier missed

1. **`search_symbol`/`graph_query` results are unbounded at the tool layer**, contradicting the project's core "mechanical bounds, always" thesis. `code_graph.search_symbol()`'s `definitions` list has no `LIMIT` (`codegraph.py:408-414`); `callers_of()` has no `LIMIT` (`codegraph.py:425-431`); neither `tools/search_symbol.py:13-41` nor `tools/graph_query.py:13-37` calls any `enforcement.cap_*` helper. On a common symbol name (e.g. `call`, `initialize`) in a large repo this can return thousands of rows in one response, defeating the byte-budget premise that every other tool enforces. This is the single biggest internal contradiction found — worth prioritizing over any of the 8 dossier gaps for a v2.0.0 hardening pass.

2. **No CI workflow runs the test suite, lint, or type-check.** `.github/workflows/` contains exactly one file, `release.yml`, and it only triggers on `v*` tag pushes; its 11 steps are entirely build/sign/scan/publish (`release.yml:28-33`, `46-311`) with no `pytest`/`ruff`/`mypy` step anywhere. `grep -n "pytest|ruff|mypy" .github/workflows/release.yml` → zero hits. This means the two existing test files (313 + 151 lines, real coverage of codegraph incremental logic and enforcement invariants) are **never automatically run** on PRs or pushes to `main` — nothing currently blocks a regression from merging. For a project whose entire value proposition is "mechanically enforced, not advisory," having zero CI gate on the mechanism's own tests is a structural risk for any v2.0.0 refactor.

3. **`INTEGRATION.md` documents `--since` with incorrect semantics.** `INTEGRATION.md:201`: *"The `--since <git-ref>` flag restricts indexing to files changed since a ref."* This is false against the shipped v0.4.0 implementation — the ref value is ignored entirely (see claim 3 above); the documented `post-commit` hook example (`INTEGRATION.md:205-208`, `--since HEAD~1`) works only by accident (it happens to enable incremental mode, not because `HEAD~1` is diffed). A v2.0.0 either needs to make `--since` actually git-aware, or fix this doc to stop implying it is.

4. **Prism-based Ruby specialist mode is pure vaporware in this repo.** Referenced in `codegraph.py:9-11` (module docstring), `SETUP.md:20,218,264`, `INTEGRATION.md:112`, and a commented-out `pyproject.toml:21-23` extras stanza (`gem install prism` — a manual step, no automation) — but zero executable code exists for it (`grep -rn "prism"` across all of `mcp-server/src` returns only the docstring line quoted above). If v2.0.0's graphify-inspired design assumes deeper Ruby AST fidelity is already partially there, it is not — Ruby indexing today is 100% generic tree-sitter, identical in kind to Python/JS/TS.

5. **The canary suite has no working dispatcher.** `scripts/run-canaries.py:72-96` defines `HostDispatcher` as a `Protocol` with one concrete `NotImplementedError`-raising stub and a commented-out example; the CLI's dispatch selection explicitly `raise NotImplementedError(f"Dispatcher {args.host!r} not yet implemented.")` (`scripts/run-canaries.py:334`). README's claim of *"First-run pass rate of 50–60% is normal"* (`README.md:337`) presupposes a runnable canary suite that does not exist in this repo as shipped — running the documented command verbatim fails immediately.

6. **No LICENSE file despite the README/pyproject promising Apache-2.0.** `README.md:462-463`: *"A top-level `LICENSE` file will land with the first published release."* Four releases have shipped since (`v0.2.3` → `v0.4.0` per `CHANGELOG.md`), and `ls LICENSE*` at repo root still returns no matches. Low risk but a genuine unfulfilled promise, and matters for any downstream packaging/distribution work in v2.0.0.

7. **No schema-version migration path.** The `manifest` table's `version` key is written (`codegraph.py:321`, always `'1'`) but never read (`grep -n "manifest"` shows no `SELECT` against it anywhere) — there is no code that would detect a schema change and migrate or rebuild an old `.atlas/graph.db`. All `CREATE TABLE` statements use `IF NOT EXISTS` (`codegraph.py:143,155,165,172`), which is exactly how the `files` table was safely added in v0.4.0 without breaking existing DBs (per `CHANGELOG.md:9`, *"created with `CREATE TABLE IF NOT EXISTS` so existing `.atlas/graph.db` files upgrade transparently"*) — but this pattern only tolerates **additive** schema changes (new tables/columns with defaults). A v2.0.0 that needs to *alter* `symbols`/`refs` (e.g. to add the confidence column from claim 1, or real edge objects for claim 5) has no migration mechanism at all; the only current answer is a full rebuild, and nothing detects when one is required versus silently running queries against a stale/partial schema.

8. **CLAUDE.md (this repo's own) has a stale filename claim.** `CLAUDE.md:54`: *"Produces `.atlas/symbols.db` and `.atlas/graph.db` (SQLite)."* — only `.atlas/graph.db` is ever created (`codegraph.py:207`); `symbols.db` does not exist anywhere in the code. Minor, but it's the file Claude Code itself reads first when working in this repo, so it actively misdirects future agent work here.

9. **README's own repository-layout diagram omits `test_codegraph.py`.** `README.md` "Repository layout" section lists only `tests/test_enforcement.py` under `mcp-server/tests/` (confirmed in the rendered tree) despite `test_codegraph.py` being the larger of the two test files (313 vs 151 lines) and covering the newest, most load-bearing feature (incremental indexing). Documentation drift, low severity, but signals the docs aren't being kept in lockstep with the code across recent releases.

10. **Eidolons wiring: absent in this repo, present-but-uncommitted in a sibling checkout — and that sibling is *older*, not newer.** This canonical checkout (`<repo>`, HEAD `f56a78e`/`v0.4.0`) has no `eidolons.yaml`, `EIDOLONS.md`, or `.eidolons/` — confirmed absent. The sibling at `<local>` is at an **older** tag, `v0.3.1` (`466c03b`), with local **uncommitted** additions (`git status --short` shows `?? .eidolons/`, `?? eidolons.yaml`, `?? EIDOLONS.md`, plus a modified `.gitignore`/`CLAUDE.md` not yet committed) — this is a personal dev-harness experiment layering eidolons v2.3.0 tooling on top of an old atlas-aci checkout, not a released or upstream state of the atlas-aci project. **It does not reflect atlas-aci being "eidolons-wired" in any shipped sense** — none of these files exist in `origin/main` for either checkout's remote. For v2.0.0 planning this matters two ways: (a) don't assume any eidolons-side scaffolding (cortex hooks, ECL envelopes, etc.) is currently real for atlas-aci — it isn't, anywhere in git history; (b) if the intent is to eventually wire atlas-aci itself as an Eidolons-consumer project (as opposed to atlas-aci being consumed *by* Eidolons via `eidolons mcp atlas-aci`, which *is* real and documented at `README.md:283-296`), that's net-new scope, not a resumption of existing work.

11. **`search_symbol` MCP schema's `kind` enum is stale relative to what the indexer actually produces.** The tool manifest restricts `kind` to `["any","class","module","method","function"]` (`server.py:97-101`), but the SCSS/HTML/YAML/Markdown/Bash queries produce `kind` values `mixin|function|placeholder|variable|selector|id|key|heading` (`codegraph.py:104-134`) that a caller cannot filter by via the documented schema (passing `kind=mixin` isn't in the advertised enum, though `codegraph.search_symbol`'s SQL would accept it fine since it's untyped at that layer — `codegraph.py:408-413`). Minor API-surface inconsistency introduced by the v0.3.0 static-site feature that nobody updated the MCP schema for.

12. **`.tsx`/`.go`/`.rs`/`.java` are recognized extensions with no matching query, so they silently index to nothing.** See Deliverable 2 detail above (`codegraph.py:41-44` vs `QUERIES` keys) — `LANG_BY_EXT` promises coverage that `QUERIES`/`DEFAULT_LANGS` doesn't deliver, with zero test coverage catching the gap (`test_codegraph.py:205-211` only checks the extension-map side).
