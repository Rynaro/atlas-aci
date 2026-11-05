---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-11-05T00:00:00Z
slice_id: ACI-S28
issue: https://github.com/Rynaro/atlas-aci/issues/49
---
# ACI-S28 - expose the declaration surface of one uv-locked pure-Python wheel as a standalone package snapshot

## Scope

Issue #49 on base `8d7dd73` (main after PR #96 / ACI-S27), branch `aci-s28`.
- The marker `<!-- atlas-aci:slice:ACI-S28 -->` is verified; no prior S28 plan, receipt or execution state exists.
- The criteria bytes hash to `bf6704d7bbabcbd14c6d14513096d01647955923b467986add40d3d5303f0524` (7 product ACs plus RUN-01/02).
- Rightsize: computed tier `lite` (score 4). An independent checker still runs.

Dependencies (ancestors of HEAD, both with COMPLETE receipts):
- **S08 (#30):** `.spectra/state/aci-s08.execution.json`.
  - Snapshot ids are sha256 over canonical JSON: `M/storage/snapshots.py:34` `digest`, and the manifest id at `:910-911`.
  - Primitives: `ev.canonical` (`M/storage/evidence.py:229`, sort_keys, compact, `ensure_ascii=False`, `allow_nan=False`) and `ev.relative_path` (`:263`).
  - `ev.hash_value` (`:259`) only checks a hex64 string; it does not hash.
- **S16 (#37):** the external-evidence boundary. External facts get their own closed kind vocabulary (`scip_*`, kept disjoint from tree-sitter kinds) and are never `ev.RELATIONS` edges (`M/storage/evidence.py:45-57`).

Observed facts (re-verified at planning; `M/` = `mcp-server/src/atlas_aci/`):
- **Precedents.**
  - S29 `snapshot_identity` = sha256(FORMAT + ":" + canonical(inputs)) (`M/importers/openapi31.py:228-230`); `validate_snapshot` (`:1134`).
  - S27 `canonical_bytes` (`M/overlays/runtime.py:219-221`), `_identity` (`:494`), `_identity_of`/`resolve_output`/`write_atomic` (`:858-904`).
  - S27 `_read_otel_input`, which opens with O_NONBLOCK and checks S_ISREG (`M/__main__.py:1281-1297`); the `import-otel` CLI (`:1300-1389`).
- **Grammar.**
  - `get_parser` is used at `codegraph.py:2478`, `query/diff_syntax.py:274`; python is a key of `QUERIES` (`codegraph.py:202`), and `DEFAULT_LANGS = tuple(QUERIES)` (`:298`).
  - tree-sitter-language-pack 1.6.0 and tree-sitter 0.25.2 are pinned (`mcp-server/uv.lock:956-997`; `pyproject.toml:14` `<1.6.3`).
  - The pack downloads grammars lazily from NATIVE code (`.spectra/plans/aci-s15.md:61-66`; the release URL is inside the native binary, `.spectra/evidence/aci-s15/qualification/sdk-uri-strings.json`). Python-level socket spies cannot observe it.
  - The production image prefetches DEFAULT_LANGS under HOME=/tmp (`mcp-server/Dockerfile:86-90`). `Dockerfile.dev` does not prefetch.
  - The observed API: `download`, `configure(cache_dir=)`, `cache_dir()`, `downloaded_languages()`, `has_language` (`.spectra/evidence/aci-s15/qualification/sdk-before.json`).
- **Iterative tree walk precedent:** `M/providers/python_imports.py:31-36`, `CONTROL` at `:8-19`.
- **This repo's `uv.lock`:**
  - `version = 1`, `revision = 3` (`:1-2`).
  - `packaging` 26.0 is registry-sourced with one `py3-none-any` wheel `sha256:b36f1fef…7529` (`:491-497`).
  - `atlas-aci` is `source = { editable = "." }` (`:37-38`).
  - There are no `resolution-markers` and no duplicate names.
  - `requires-python >=3.11` (`pyproject.toml:6`), so `tomllib` is available. No new dependency is needed.
- **Test pins.**
  - S29 purity file-list (`tests/slices/test_s29_openapi_diff.py:1361-1369`, importers/ + contracts/ only); `BANNED_VOCABULARY` (`:1401-1404`).
  - README rows located by substring: `docs/importers/openapi.md` (`test_s29:1447`), `docs/importers/opentelemetry.md` (`test_s27_isolation.py:205`), `docs/frameworks/rails.md` (`test_s26_isolation.py:1705`).
  - README must not contain `canary-missions.md` (`test_s23_isolation.py:117`).
  - CHANGELOG entries are sliced by title up to `\n- **`.
  - `test_s14` parses only the TSX section; `test_export.py:367` bans a root `.gitattributes`; `test_thesis_negatives.py:51` bans "alter table" src-wide; `test_export.py:372` bans `nx.compose(`.
  - Tools JSON: 7 tools, `94f9567a…ac56` / 1631472 bytes; 3436/3448/797/7730 (`test_s29:1282-1295`).
- **Interpreters.** CI runs Python 3.14 (`.github/workflows/ci.yml:108`); the dev and production images run 3.12 (`Dockerfile:32,55`).

### Orchestrator decisions (user authorised autonomous execution with recommended defaults; recorded 2026-11-05)
1. **Architecture = scout option A.**
   - A standalone, content-addressed `atlas-python-package-snapshot.v1`, written atomically to an operator `--output` only.
   - No graph and no `--repo`. No edits to `codegraph.py`, `storage/*`, `query/*`, `protocol/*`, `tools/*`, `server.py`, `importers/*` or `overlays/*`.
   - "Use S08 snapshot identities" = mirror S08's canonical-JSON + sha256 id construction (`ev.canonical` + `hashlib.sha256`; `ev.hash_value` validates hex64).
   - AC-002 "attach to the verified package snapshot" = every member/module/declaration/import/export/diagnostic record carries `package_snapshot_id`, `member` and `member_sha256`. `validate_snapshot` recomputes every derived field and id.
   - The lock is bound by its sha256. No consumer-graph binding: `uv.lock` is not in the consumer lease. Consumer-snapshot binding is a follow-up (limitation 7).
2. **Modules.**
   - `dependencies/__init__.py` (docstring only).
   - `dependencies/uv_lock.py` (pure): shared errors/limits, TOML profile, normalization, marker subset, variant selection, locked pure-wheel set, coordinate.
   - `dependencies/python_wheel.py`: digest, zip safety, metadata cross-check, grammar guard, extraction, assembly, `validate_snapshot`, canonical bytes.
   - The library takes BYTES and zero-argument readers only. All filesystem I/O is in `__main__.py`, so neither module imports `os`/`pathlib`.
   - No third module. Output helpers are copied into `__main__.py`, not imported from gated `overlays/`.
3. **Lock profile, TOML hardening, environment** (D2-D4).
4. **Wheel ladder** (D5-D7). RECORD is NOT consulted (D7, limitation 12).
5. **Extractor = tree-sitter python**, behind a local-cache guard that never triggers a download (D8). The STOP-RULE fallback is `ast` (Day-1 (a)).
6. **Identities and determinism** (D10-D11). AC-005 also replays across `PYTHONHASHSEED` and against a committed golden snapshot, giving 3.12/3.14 parity in CI.
7. **AC-007** (D13): a subprocess audit-hook run (hook isolated from the pytest process), in-process S26-style spies with liveness controls, `sys.modules` delta, a sentinel with a positive control, a static AST scan, and the empty-cache no-download test.
8. **Fixtures:** committed binary wheel + locks + golden, `.gitattributes` `* -text`, and a stdlib-only generator that reproduces the wheel/lock bytes, checked by a test (D14).
9. **Scope amendment:**
   - `dependencies/__init__.py`, `test_s28_*.py`, `tests/fixtures/python_dependencies/**`;
   - a CHANGELOG separate `- **` bullet at the top of `### Added`;
   - a README docs-table row after the opentelemetry row (it contains none of the substrings `docs/importers/openapi.md`, `docs/importers/opentelemetry.md`, `docs/frameworks/rails.md`, `canary-missions.md`).
   - NO prior-test edit (verified above).
10. **Invariants:** the tools JSON and the four budgets; G-SRC; no new dependency; no interpreter-dependent outcome (every Unicode-database-dependent check is replaced by an ASCII rule; TOML and zip behaviours are pinned by our own pre-checks).

### In
1. `dependencies/uv_lock.py`:
   - `LOCK_PROFILE`, `ECOSYSTEM="pypi"`, `ENV_KEYS`, `REJECTIONS`, `VARIANT_DETAILS`, `LIMITS`;
   - `DependencyRejected(reason, detail=None, coordinate=None)`, `DependencyUsageError(what)`, `Limits`, `normalize_limits`;
   - `normalize_name`, `parse_package_argument`, `parse_environment`, `parse_marker`, `evaluate_marker`, `parse_wheel_filename`;
   - `Selection` (frozen), `select_package(lock_raw, package, environment, limits)`, `match_supplied(selection, basenames)`.
2. `dependencies/python_wheel.py`:
   - `FORMAT`, `SUMMARY_FORMAT`, `IMPORTER`, `DECLARATION_KINDS`, `DIAGNOSTIC_CODES`, `MEMBER_ROLES`, `PARSE_STATES`, `LIMITATIONS`, `COVERAGE`;
   - `python_parser()`, `build_snapshot(selection, filename, wheel_raw, limits)`, `import_dependency(lock_raw, package, environment, supplied, limits)` where `supplied` is a sequence of `(basename, reader)`;
   - `validate_snapshot`, `canonical_bytes`, `summary_document`.
3. `__main__.py`: `import-python-dependency`, plus private `_read_dependency_input`, `_dependency_output`, `_write_dependency_atomic`.
4. Tests `tests/slices/test_s28_*.py` and fixtures `tests/fixtures/python_dependencies/`.
5. `docs/importers/python-dependencies.md`, a CHANGELOG bullet and a README row.

### Out
- Downloads, installation, import or execution of package code, build backends, dependency resolution.
- Sdists, platform wheels, compiled-extension internals, `.pyi` stubs, `.data/`.
- Poetry/pip/Conda locks; editable/git/path/directory/url/virtual sources; non-HTTP(S) registries.
- `requires-python` evaluation; uv lock revisions other than 3.
- Call binding or cross-package import resolution; any graph, evidence-store or MCP exposure; any G-SRC edit.

## Approach

### Decisions

**D1 Pipeline.** The first failure wins. Nothing is written or printed to stdout on failure. No artifact is opened before stage 6.

| # | Stage | Touches | Failure |
| --- | --- | --- | --- |
| 1 | limits, `--env`, `--package` | arguments | usage, exit 2 |
| 2 | output-path checks (CLI) | path metadata | usage, exit 2 |
| 3 | lock read (O_NONBLOCK, S_ISREG, ≤ limit+1) → pre-scan → tomllib → profile | lock bytes | `lock_too_large`, `lock_invalid`, `unsupported_lock_profile` |
| 4 | candidates → variant selection → source kind → locked pure-wheel set | parsed lock | `package_not_in_lock`, `unresolved_lock_variant`, `unsupported_source_kind`, `no_locked_pure_wheel` |
| 5 | match supplied basenames to locked pure filenames (no file opened) | names only | `missing_dependency_source`, `ambiguous_dependency_source` |
| 6 | read ONLY the matched wheel; size; sha256 and size vs THAT lock entry | wheel bytes | `wheel_too_large`, `artifact_digest_mismatch` (before `ZipFile` exists) |
| 7 | zip structure, names, modes, offsets, caps, member reads + CRC | in-memory zip | `invalid_wheel`, `unsafe_archive_member`, `archive_limit_exceeded` |
| 8 | dist-info / METADATA / WHEEL cross-check | members | `wheel_metadata_mismatch`, `not_pure_python` |
| 9 | grammar guard, then extraction per `.py` member | members | `python_parser_unavailable`, `surface_limit_exceeded` |
| 10 | assembly → `validate_snapshot` → canonical bytes | memory | `invalid_snapshot` |
| 11 | atomic write of `--output` | output only | exit 1, fixed text |

**D2 Lock read and pre-scan** (bytes, linear, interpreter-independent; every failure → `lock_invalid`):
1. size > `max_lock_bytes` → `lock_too_large`.
2. Leading BOM; non-strict-UTF-8.
3. Running depth of `[`+`{` (string context ignored, floor 0) > `MAX_TOML_DEPTH = 16`.
4. LF while `{`-depth > 0. Inline tables are single-line in TOML 1.0, which rules out any newer-TOML acceptance on 3.14.
5. A backslash followed by `e` or `x`.
6. A run of ASCII digits/underscores longer than `MAX_DIGIT_RUN = 128`. This makes the outcome independent of `PYTHONINTMAXSTRDIGITS`.

Then `tomllib.loads(text, parse_float=_refuse)` inside `except Exception` → `lock_invalid` (RecursionError included). Shape:
- the root is a table; `version` and `revision` are `int` and not `bool` (absent → `lock_invalid`);
- `(version, revision) != (1, 3)` → `unsupported_lock_profile`. Strict: this repo's lock is the only observed instance, and other revisions are unverified.
- `package` is a list of tables, each with a `str` `name`.
- Only `name`, `version`, `source`, `resolution-markers`, `wheels` of candidate entries are read; everything else is never walked.

**D3 Selection** (`select_package`; pure; completes before any artifact access):
1. `--package` grammar: fullmatch `[A-Za-z0-9]([A-Za-z0-9._-]{0,198}[A-Za-z0-9])?`, else usage.
   Normalization (PEP 503, ASCII): `re.sub(r"[-_.]+", "-", n).lower()`.
2. Candidates = entries whose normalized `name` equals it. None → `package_not_in_lock`.
   - Each candidate needs a `version` fullmatching `[0-9][0-9A-Za-z!+._-]{0,63}`, else `lock_invalid`.
   - `resolution-markers`, when present, is a list of 1..`MAX_MARKERS = 64` strings, else `lock_invalid`.
3. Variant rule (`unresolved_lock_variant`; `detail` ∈ VARIANT_DETAILS):
   - Every marker of every candidate is parsed. Any unsupported construct → `unsupported_marker`; over a cap → `marker_limit_exceeded`; a referenced variable absent from the environment → `environment_incomplete`. This fails closed even if another marker is true.
   - With exactly one candidate: no markers → selected; markers (OR) false → `no_matching_variant`.
   - With ≥2 candidates: any without markers → `unmarked_variant`. Otherwise the candidates whose marker OR is true must number exactly 1; 0 → `no_matching_variant`, ≥2 → `multiple_matching_variants`.
4. Source kind (selected entry):
   - `source` must be a table with exactly one key.
   - `registry` with fullmatch `https?://[A-Za-z0-9._~:/?#@!&'()*+,;=%-]{1,2048}` → accepted.
   - Anything else (editable, git, path, directory, url, virtual, unknown key, or a non-HTTP registry) → `unsupported_source_kind`.
5. Locked pure-wheel set (each entry is kept or excluded; none kept → `no_locked_pure_wheel`):
   - `wheels` entries are tables, else `lock_invalid`.
   - `url` fullmatches the URL grammar with no `?`/`#`.
   - The basename parses as a PEP 427 filename (D6) with normalized dist == name and version (`-`→`_`) == the locked version.
   - The abi tag set is `{none}` and the platform tag set is `{any}`.
   - `hash` fullmatches `sha256:[0-9a-f]{64}`; `size`, when present, is an `int` and not `bool`.
6. Coordinate (closed):
   `{ecosystem:"pypi", name, version, coordinate:"pypi:<name>@<version>", source:{kind:"registry", registry}, expected_artifacts:[{filename, sha256}] sorted}`.
   It is attached to every later `DependencyRejected`.

**D4 Environment and marker subset** (`parse_environment`, `parse_marker`, `evaluate_marker`; iterative shunting-yard with explicit stacks, no recursion, no eval).
- `--env KEY=VALUE` is repeatable. Unknown key, duplicate key, missing `=` or a bad value → usage exit 2, `Invalid --env value.`.
- `ENV_KEYS` = `implementation_name`, `os_name`, `platform_machine`, `platform_python_implementation`, `platform_system`, `python_full_version`, `sys_platform`.
- `python_full_version` must fullmatch `[0-9]{1,4}(\.[0-9]{1,4}){1,2}`; other values `[A-Za-z0-9._+-]{1,64}`.
- `python_version` is a marker-only variable derived as the first two components of `python_full_version`.
- Grammar:
  - `expr := term ("or" term)*`; `term := atom ("and" atom)*`; `atom := "(" expr ")" | VAR OP LITERAL`.
  - Whitespace is space and tab only. `VAR` ∈ ENV_KEYS ∪ {python_version} and is ALWAYS on the left.
  - `OP` ∈ `== != < <= > >=`. `LITERAL` is single- or double-quoted, 0..256 printable ASCII, with no backslash and not the quote character.
- Version variables:
  - the literal fullmatches `[0-9]{1,4}(\.[0-9]{1,4}){0,3}` (no `.*`, pre/dev/post or local);
  - comparison is on zero-padded 4-tuples.
- String variables: `==`/`!=` only, exact.
- Unsupported (→ `unsupported_marker`):
  - `not`, `in`, `not in`, `~=`, `===`;
  - `extra`, `group` and every other variable;
  - a reversed operand order;
  - an empty marker, a dangling connective, unbalanced parentheses.
- Caps (→ `marker_limit_exceeded`): 1024 characters, parenthesis depth 8, 256 tokens.

**D5 Supplied-wheel matching** (`match_supplied`; basenames only, never opened):
- A supplied basename matches iff it EQUALS a filename in the locked pure-wheel set (renamed files do not match).
- 0 matches → `missing_dependency_source` (AC-006). >1 (the same basename twice, or two different locked pure wheels both supplied) → `ambiguous_dependency_source`.
- Unmatched supplied wheels are never opened. Their count appears only in the CLI summary (`wheels_ignored`), never in the snapshot.

**D6 PEP 427 filename:**
- fullmatch `([A-Za-z0-9_.]+)-([0-9][A-Za-z0-9_.!+]*)(-[0-9][A-Za-z0-9_.]*)?-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)\.whl`;
- tag sets split on `.`; total length ≤ 255.

**D7 Artifact and archive** (`build_snapshot`; the library receives bytes; `ZipFile` is constructed only after stage 6):
1. `len(raw) > max_wheel_bytes` → `wheel_too_large`.
2. `sha256(raw)` ≠ the matched entry's hash, or the lock `size` is present and ≠ `len(raw)` → `artifact_digest_mismatch`.
3. `zipfile.ZipFile(io.BytesIO(raw))`. Any exception here or while reading members → `invalid_wheel` (a typed `DependencyRejected` passes through).
4. `len(infolist()) > max_members`, or Σ declared `file_size` > `max_total_uncompressed_bytes` → `archive_limit_exceeded`.
5. Per entry, `unsafe_archive_member` on any of:
   - `orig_filename != filename` (NUL truncation);
   - a non-ASCII name, whatever the UTF-8 flag (Unicode-database-independent);
   - a name containing NUL, C0, DEL, backslash or `:`; a name longer than 1024; `not ev.relative_path(name.rstrip("/"))`;
   - a trailing `/` on a non-directory, or a directory entry with data;
   - a duplicate name, an ASCII-lowercase duplicate, or a file path that is also a directory prefix;
   - `flag_bits & ~0x080E` (encryption bit 0x1 and strong-encryption bits included);
   - `compress_type` ∉ {STORED, DEFLATED};
   - `external_attr >> 16` nonzero and neither S_ISREG nor S_ISDIR (symlinks, FIFOs, devices);
   - the local-header spans `[header_offset, header_offset + 30 + n + e + compress_size)`, with `n`/`e` read from the raw local header bytes, overlap, the minimum offset ≠ 0, or the local signature is wrong.
6. Per regular member:
   - `file_size > max_member_bytes`, or `file_size > 1024 and file_size > 200 * max(compress_size, 1)` → `archive_limit_exceeded`;
   - read `zf.open(info).read(file_size + 1)`; length ≠ `file_size` or `zlib.crc32(data) != info.CRC` → `invalid_wheel` (our own CRC check, not reliant on zipfile EOF behaviour);
   - sha256 per member.
   - Never `extract`/`extractall`; nothing touches disk.
7. Metadata:
   - exactly one top-level `<x>-<v>.dist-info/` whose normalized name/version equal the coordinate, else `wheel_metadata_mismatch`;
   - `METADATA` and `WHEEL` are strict UTF-8, parsed by a line scan up to the first blank line (no `email`); exactly one `Name` (normalized == name) and one `Version` (== version), else `wheel_metadata_mismatch`;
   - `WHEEL` `Wheel-Version` major 1, else `wheel_metadata_mismatch`;
   - `Root-Is-Purelib` must be `true` (ASCII case-insensitive), and ≥1 `Tag` with every tag `*-none-any`, else `not_pure_python`.
   - RECORD is not consulted. The lock-pinned artifact digest already authenticates every byte, and RECORD is producer-asserted data inside those same bytes. Parsing it would add an input surface without adding authenticity (limitation 12).

**D8 Grammar guard (`python_parser()`).**
- The importer NEVER calls `download`, `configure` or `get_parser` for a grammar that is not already local. The local-availability predicate is fixed on Day-1 (a):
  1. `has_language("python")` if it is shown local-only;
  2. otherwise `"python" in downloaded_languages()`;
  3. otherwise a name check under `cache_dir()`.
- Not local → `python_parser_unavailable` (exit 1). Then `get_parser("python")`.
- Extractor identity: `{parser:"tree-sitter", language:"python", grammar_version, abi_version}`, read from the loaded `Language`. `Language.semantic_version`/`abi_version` is preferred, else the pack `__version__`; Day-1 (g) fixes the accessors. `importlib.metadata` is allowed in python_wheel.py only if it is the sole accessor (recorded).

**D9 Extraction** (per member with role `python_source`; iterative explicit-stack walk; nodes as qualified Day-1 (a)):
- Roles (closed, by path):
  - `dist_info`: under the dist-info directory;
  - `data`: under `*.data/` → diagnostic `data_directory_not_indexed`;
  - `stub`: `.pyi` → `stub_file_not_indexed`;
  - `extension`: `.so/.pyd/.dylib/.dll` → `compiled_extension_not_indexed`;
  - `python_source`: `.py`;
  - `other`: → `non_python_member_not_indexed`.
- Module name: strip `.py`; `/__init__` → package (`is_package`). Every segment must fullmatch `[A-Za-z_][A-Za-z0-9_]*`, else `module: null`, `parse_state: "not_parsed"` and `non_importable_module_path`.
- Strict UTF-8 decode failure → `non_utf8_source` (`parse_state` of that name). `root_node.has_error` → `syntax_error` with NO declarations/imports/exports for that member (no error-recovery phantoms).
- Declarations:
  - Module- and class-body statements only, unconditional, after unwrapping `decorated_definition` (`decorated: true`).
  - `class_definition` → `class`; `function_definition` → `function`/`async_function` (the async token child), or `method`/`async_method` when the parent is a class body.
  - Classes nest to `MAX_CLASS_NESTING = 4`; a deeper class → `nesting_limit_exceeded` and its subtree is skipped.
  - Definitions inside function bodies are not indexed (limitation 4).
  - A CONTROL statement (`if/for/while/try/with/match`, per `providers/python_imports.py:8-19`) at module/class-body level containing ≥1 definition → one `conditional_definition_not_indexed` (span of the statement); containing ≥1 import → one `conditional_import_not_indexed`. Their contents are not indexed.
- Imports (module level, unconditional; one record per imported name):
  - `import a.b [as c]` → `{statement:"import", level:0, target:"a.b", name:null, alias, resolved_module:"a.b"}`;
  - `from X import y [as z]` → `{statement:"from", level, target (dots stripped), name, alias, resolved_module}`;
  - `name "*"` also emits `star_import_not_expanded`;
  - relative resolution: base = the package (the module itself if `is_package`, else its parent) minus `level-1` trailing segments; too few segments → `resolved_module: null`.
- Exports: exactly one unconditional module-level `__all__ = [..]`/`(..)` whose elements are plain `string` nodes, with no prefix, interpolation, escape or concatenation and content fullmatching `[A-Za-z_][A-Za-z0-9_]*`. Any other assignment/augmentation/call on `__all__` → `dynamic_all_not_indexed` and no exports for that module.
- An unconditional module-level `def __getattr__` → `dynamic_module_getattr` (the function is still a declaration).
- Spans: `{start_byte, end_byte, start_line: row+1, start_col: byte column, end_line, end_col}` over the member bytes. Names are raw grammar identifier text (not normalized).
- `len(declarations)+len(imports)+len(exports)+len(diagnostics) > max_records` → `surface_limit_exceeded`.

**D10 Snapshot `atlas-python-package-snapshot.v1`.** Closed keys; canonical JSON + LF. No paths of operator files, clock, host, limits or zip dates/comments.
```
{format, snapshot_id,
 importer:{name:"atlas-aci import-python-dependency", version:"atlas-python-dependency-importer.v1"},
 extractor:{parser:"tree-sitter", language:"python", grammar_version, abi_version},
 lock:{profile:"atlas-uv-lock-profile.v1", version:1, revision:3, sha256},
 environment:{<ENV_KEYS subset>: str},
 package:{ecosystem:"pypi", name, version, coordinate, source:{kind:"registry", registry},
          artifact:{filename, sha256, bytes, url, digest_check:"equals_lock_entry"}},
 dist_info:{directory, name, version, wheel_version, root_is_purelib:true, tags:[sorted]},
 coverage:{evidence_coverage:"declaration_only", completeness:"incomplete",
           call_binding:"not_claimed", execution:"not_performed"},
 counts:{members, python_sources, parsed_modules, declarations, imports, exports, diagnostics,
         diagnostics_by_code:{every DIAGNOSTIC_CODES key, zeros included}},
 limitations:[LIMITATIONS],
 members:[{path, role, sha256, bytes}],
 modules:[{id, type:"module", package_snapshot_id, member, member_sha256, module|null, is_package,
           parse_state, incomplete}],
 declarations:[{id, type:"declaration", package_snapshot_id, member, member_sha256, module, kind,
                name, qualified_name, symbol, parent_id|null, decorated, span}],
 imports:[{id, type:"import_path", package_snapshot_id, member, member_sha256, module, statement,
           level, target, name|null, alias|null, resolved_module|null, span}],
 exports:[{id, type:"export", package_snapshot_id, member, member_sha256, module, name, span}],
 diagnostics:[{id, type:"diagnostic", package_snapshot_id, member, member_sha256, code, span|null}]}
```
- `import_path` is deliberately not `import`: `ev.RELATIONS` has an `import` edge, and these are paths, not edges.
- `qualified_name` is dotted within the module; `symbol = module + "." + qualified_name`.
- `incomplete` is true iff any diagnostic names that member.
- Closed sets:
  - `PARSE_STATES = parsed, syntax_error, non_utf8_source, not_parsed`
  - `MEMBER_ROLES = python_source, stub, extension, data, dist_info, other`
  - `DECLARATION_KINDS = class, function, async_function, method, async_method`
  - `DIAGNOSTIC_CODES = compiled_extension_not_indexed, conditional_definition_not_indexed, conditional_import_not_indexed, data_directory_not_indexed, dynamic_all_not_indexed, dynamic_module_getattr, nesting_limit_exceeded, non_importable_module_path, non_python_member_not_indexed, non_utf8_source, star_import_not_expanded, stub_file_not_indexed, syntax_error`

**D11 Identity and order.**
- `snapshot_id = sha256(("atlas-python-package-snapshot.v1:" + ev.canonical(basis)).encode()).hexdigest()`, where `basis = {importer, extractor, lock, environment, package}` (artifact sha256 included). Limits are reject-only and not in the id.
- Record ids are `sha256(ev.canonical(T).encode()).hexdigest()`:

| Record | T |
| --- | --- |
| module | `["atlas-pydep-module.v1", sid, member]` |
| declaration | `["atlas-pydep-declaration.v1", sid, member, kind, qualified_name, start_byte]` |
| import | `["atlas-pydep-import.v1", sid, member, start_byte, ordinal]` |
| export | `["atlas-pydep-export.v1", sid, member, name, start_byte]` |
| diagnostic | `["atlas-pydep-diagnostic.v1", sid, member, code, start_byte or -1]` |

- Order:
  - members and modules by path;
  - declarations by (member, start_byte, kind, qualified_name);
  - imports by (member, start_byte, ordinal);
  - exports by (member, start_byte, name);
  - diagnostics by (member, code, start_byte or -1).
- `validate_snapshot` (→ `invalid_snapshot`) checks:
  - closed keys and enums at every level, and the constants;
  - `ev.hash_value` on every digest;
  - `coordinate == "pypi:"+name+"@"+version`;
  - snapshot_id and every id recompute; ids are unique;
  - every record's `package_snapshot_id == snapshot_id`, `member` ∈ members, and `member_sha256` equals that member's sha256;
  - exactly one module per `python_source` member; the module name recomputes from the path;
  - every `parent_id` resolves to a class in the same member; method kinds iff a parent exists; `qualified_name`/`symbol` recompute;
  - spans within `bytes` with start<end and lines ≥1; declarations/imports/exports only for `parsed` modules;
  - sort orders; `counts` recompute; `incomplete` recomputes.

**D12 CLI** `atlas-aci import-python-dependency --lock PATH --package NAME [--env KEY=VALUE]... [--wheel PATH]... --output PATH [limits]`.
- Paths are `click.Path(path_type=Path)` without `exists`. Imports are lazy. Only `--output` is written.
- Output check (a copy of S27's identity rule, no protected roots): the parent is resolved once; the target equal (realpath or `(st_dev, st_ino)`) to the lock or any `--wheel` → exit 2. mkstemp + fsync + `os.replace` happen in the resolved parent; any failure unlinks the temp.
- stdout on success: `atlas-python-dependency-import-summary.v1` = `{format, snapshot_id, coordinate, artifact_sha256, coverage, counts, wheels_ignored}`.
- Never `str(exc)`. A coordinate is printed only after selection, built from grammar-validated tokens.

| Situation | Exit | Text (after Click's `Error: `) |
| --- | --- | --- |
| bad limit | 2 | `Invalid Python dependency limit: <name>` |
| bad `--env` | 2 | `Invalid --env value.` |
| bad `--package` | 2 | `Invalid --package name.` |
| output equals lock or a wheel | 2 | `--output must differ from the lock and every wheel.` |
| lock unreadable or not regular | 1 | `uv lock file cannot be read.` |
| matched wheel unreadable or not regular | 1 | `Wheel file cannot be read.` |
| rejection before selection | 1 | `Python dependency rejected: <reason>` |
| rejection after selection | 1 | `Python dependency rejected: <reason> pypi:<name>@<version>` |
| write failure | 1 | `Python dependency snapshot cannot be written.` |
| other | 1 | `Python dependency import failed: <TypeName>` |

**D13 AC-007 non-execution and locality proof.** See the test table. A Python-level spy cannot see the pack's native downloader, so grammar locality is shown by:
- (i) the D8 guard;
- (ii) the empty-cache test: result `python_parser_unavailable`, the cache dir is still empty, and it returns in < 5 s;
- (iii) the production image succeeding under `--network none`.

**D14 Bounds** (out of range or not `int` → usage exit 2). Bounds accept or reject inputs and never change content.

| Limit | Default | Low | High |
| --- | --- | --- | --- |
| `max_lock_bytes` | 33554432 | 1 | 268435456 |
| `max_wheel_bytes` | 67108864 | 1 | 268435456 |
| `max_members` | 10000 | 1 | 100000 |
| `max_total_uncompressed_bytes` | 268435456 | 1 | 1073741824 |
| `max_member_bytes` | 16777216 | 1 | 268435456 |
| `max_records` | 200000 | 1 | 1000000 |

Constants: `MAX_TOML_DEPTH=16`, `MAX_DIGIT_RUN=128`, `MAX_MARKERS=64`, marker caps 1024/8/256, `MAX_CLASS_NESTING=4`, ratio 200, name ≤ 1024.

**D15 Closed rejections** (stage order): `lock_too_large, lock_invalid, unsupported_lock_profile, package_not_in_lock, unresolved_lock_variant, unsupported_source_kind, no_locked_pure_wheel, missing_dependency_source, ambiguous_dependency_source, wheel_too_large, artifact_digest_mismatch, invalid_wheel, unsafe_archive_member, archive_limit_exceeded, wheel_metadata_mismatch, not_pure_python, python_parser_unavailable, surface_limit_exceeded, invalid_snapshot`.
- `VARIANT_DETAILS = environment_incomplete, marker_limit_exceeded, multiple_matching_variants, no_matching_variant, unmarked_variant, unsupported_marker`.
- `str(DependencyRejected)` is `"dependency_rejected: <reason>"`.
- No constant, key or enum value contains an S29 `BANNED_VOCABULARY` token (for example `digest_check: equals_lock_entry`, not "verified").

### Limitations (static `LIMITATIONS`, emitted in every snapshot and named in the docs)
1. `declaration_only_no_call_binding`
2. `dynamic_exports_not_modeled`
3. `conditional_definitions_and_imports_not_indexed`
4. `local_definitions_and_imports_not_indexed`
5. `stubs_extensions_and_data_not_indexed`
6. `imports_are_paths_not_resolved_across_packages`
7. `lock_bound_by_digest_not_to_a_source_snapshot`
8. `single_registry_pure_wheel_only`
9. `uv_lock_version_1_revision_3_only`
10. `restricted_marker_subset`
11. `requires_python_not_evaluated`
12. `wheel_record_not_consulted`
13. `identifiers_recorded_as_written`
14. `not_written_to_the_graph_and_not_exposed_over_mcp`

### Work plan (timebox 5 engineering days)

**Day 1 AM - qualification, before product code.** Scratch probes write `.spectra/evidence/aci-s28/qualification.json` (3.12 dev, plus `uv run --python 3.14` where available offline, plus the images).
- (a) **Grammar.**
  - Record the pack API (`has_language`, `downloaded_languages`, `cache_dir`, `__version__`) and which env (`HOME`/`XDG_CACHE_HOME`) or `configure` redirects the cache in a probe process.
  - With an EMPTY cache: each candidate predicate returns False without creating files, within 5 s.
  - Run the same probe in the production image under `--network none --read-only` (python present; `get_parser` works) and in the dev image under `--network none` (outcome recorded; expected unavailable).
  - Record the audit events of a first grammar load (`open`, `ctypes.dlopen`, `socket.*`).
  - Record python node shapes: the `function_definition` async token; the `decorated_definition` `definition` field; `class_definition` body; `import_statement`, `import_from_statement`, `relative_import`, `aliased_import`, `wildcard_import`; `assignment` with list/tuple/`string` children (`string_start`/`string_content`/`escape_sequence`/`interpolation`); `has_error` on broken input; `Language.abi_version`/`semantic_version`.
- (b) **tomllib.**
  - The real `mcp-server/uv.lock` and the forked fixture parse on 3.12/3.14.
  - Record the measured max depth, max digit run and longest line.
  - Pre-scan cases: depth 16 accepted, 17 rejected, 100000 rejected; a 5000-digit int rejected with and without `PYTHONINTMAXSTRDIGITS=0`.
  - Record whether 3.14 accepts a multi-line inline table, `\e` or `\x41` (informational; the pre-scan rejects them anyway).
  - Confirm every `tree-sitter-language-pack` wheel in `uv.lock` is platform-specific.
- (c) **zipfile on 3.12 and 3.14.**
  - `orig_filename` keeps NUL while `filename` truncates; `flag_bits`, `external_attr`, `header_offset` and `CRC` are exposed.
  - Duplicates are kept in `infolist`; a central/local name mismatch raises on open.
  - Opening a crafted symlink/encrypted entry is never needed (we reject first).
  - Truncated or garbage input raises inside the constructor.
- (d) **Generator:** byte-identical outputs twice on 3.12 and on 3.14 (the raw writer, STORED only).
- (e) **Canonical bytes:** two imports, `PYTHONHASHSEED=1/2`, and 3.12 vs 3.14 → identical snapshot bytes for the main fixture.
- (f) **Base invariants** go to `base-invariants.json`: tools sha and bytes, 3436/3448/797/7730, and full `pytest -q` at base with test_s08 nonregular_pack deselected.
- (g) **Extractor identity accessors** (D8).
- (h) **Informational:** try a real forked `uv lock --offline` with local wheels to compare the `resolution-markers` shape. Otherwise record "fork shape hand-authored, unverified" in `provenance.json` and the docs.

STOP RULE:
- (a) No local-only predicate of the three → return to the orchestrator.
  - If the production image cannot load python offline, OR extraction outputs differ between 3.12 and 3.14 because of the grammar → apply the pre-authorised `ast` fallback:
    - `ast.parse(src, feature_version=(3, 12))`;
    - `SyntaxError`/`ValueError`/`RecursionError`/`MemoryError` → `syntax_error`;
    - extractor `{parser:"cpython-ast"}`;
    - the audit test allows `compile` events whose filename is the fixed `<atlas-pydep>`;
    - a residual limitation is added and both interpreters are shown identical on the fixtures. Record it.
  - Node-shape differences only → adapt D9, with no scope impact.
- (b) The real lock fails D2 on either interpreter → stop and return.
- (c) Unexpected zipfile behaviour → replace the reliance with raw-byte parsing in python_wheel.py (scope-neutral) and record it.
- (d)/(e) Non-identical bytes → find the cause before Day 2 / Day 3.

**Day 1 PM:** `uv_lock.py` (D2-D6, D14-D15) and `test_s28_lock.py`.

**Day 2:** `generate.py`, the fixtures, the archive ladder (D7) and `test_s28_archive.py`.

**Day 3:** the guard, extraction, assembly and `validate_snapshot` (D8-D11); AC-001/002/003/005/006 at library level; the golden.

**Day 4:** CLI (D12), AC-004/006/007 at CLI level, `test_s28_cli.py`, `test_s28_isolation.py`, docs, CHANGELOG, README.

**Day 5:** gates, the `prod-image.sh` script, RUN-01/02, buffer.

Cut order (each cut becomes a listed limitation; no AC is affected):
1. README row and doc depth;
2. the subprocess audit-hook test (keep spies, sentinel, `sys.modules` and the static scan);
3. the golden cross-interpreter pin (keep the PYTHONHASHSEED replay);
4. nested classes (`MAX_CLASS_NESTING=1`);
5. relative-import resolution (always null);
6. `__all__` exports (always `dynamic_all_not_indexed`).

Never cut:
- selection before any artifact read; digest before `ZipFile`;
- the full D7 ladder; the D8 guard;
- closed vocabularies; ids and canonical bytes; `validate_snapshot`;
- CLI fixed texts and the output checks/atomic write;
- G-SRC/G-MCP.

### Independent checker conditions
Checker verdict: **APPROVE_WITH_CONDITIONS** (`atlas-s28-plan-checker`, 3 MAJOR + 10 MINOR; `.spectra/plans/aci-s28.review.json`). Conditions 1-13 below are binding and override conflicting text above.

Orchestrator resolutions (stricter option wherever the checker offered a choice):
- C1: the audit-hook subprocess runs over the real CLI (`cli.main([...], standalone_mode=False)` after pre-import) with the open-event allowlist; the in-process spy is kept as a second check restricted to O_RDONLY.
- C2: the extractor block stays in the snapshot and its id, derived ONLY from the pack `__version__` plus `Language.abi_version` (never host files); the golden is produced/confirmed in the Linux production image and must equal the macOS dev value before commit; the golden test compares `extractor` first. If any extractor field differs between platforms or interpreters, STOP and report instead of committing a platform-specific golden.
- C4: the session fixture fails (never skips) when the grammar cannot be provisioned; empty-cache test uses `pack.configure(cache_dir=<tmp>)`.
- C6: an absent `revision` is `unsupported_lock_profile`.
- C8: the ratio cap applies to every member and becomes a documented limit (default 200, bounded maximum), not a hidden constant.
- C9: exact version equality kept; recorded as a limitation and tested.

Checker findings (verbatim):

1. MAJOR: the AC-007 proof for the CLI path is bypassable.
   - Evidence: plan lines 549(1)-(2); `__main__.py:1281-1297`.
   - The audit hook covers only the bytes-in library. The CLI path, which is where all filesystem reads live, is checked only by a Python-level `os.open` spy. That spy cannot see builtin `open()`, `pathlib` reads or `FileIO` calls.
   - Condition (stricter option): run the audit-hook subprocess over the real CLI (`cli.main([...], standalone_mode=False)` after pre-import). Assert zero `exec`, `compile`, `socket.*`, `subprocess.*`, `os.system`, `os.exec*`, `os.spawn*` and `os.posix_spawn` events. Assert that `open` events are limited to the lock, the matched wheel, the output temp file and pre-warmed module files. Assert that unmatched `--wheel` or FIFO paths never appear in any event. Keep the spy as a second check, restricted to O_RDONLY flags (mkstemp writes would otherwise pollute the set).

2. MAJOR: the extractor identity inside `snapshot_id` and the golden is a likely first-push CI failure.
   - Evidence: plan D11 (line 282), D8 (line 219), line 389; `ci.yml:108` (Linux, Python 3.14).
   - The golden is produced on a macOS dev host with Python 3.12. CI compares it on Linux with Python 3.14 and a freshly downloaded grammar. Day-1(e) compares only 3.12 against 3.14 on one host.
   - Conditions:
     - The grammar-version accessor must derive from the pack and grammar binary (pack `__version__` plus `Language.abi_version`), never from host files.
     - The golden must be produced or confirmed in the Linux production image. The `extractor` block must equal the macOS value before the golden is committed.
     - The golden test must compare `extractor` first, so extractor drift reads differently from fact drift.
     - If `importlib.metadata` is used, call it once at import. Otherwise its `METADATA` read fires an `open` event outside `cache_dir` and trips the finding 1 allowlist.

3. MAJOR: `__all__` and name-surface incompleteness can go silent.
   - Evidence: plan lines 236 and 242.
   - Control-statement contents are skipped. An `if`/`try` block that assigns or extends `__all__` produces neither exports nor `dynamic_all_not_indexed`. Annotated, tuple-unpack and `.extend(...)` forms are listed only loosely. Export ids use `(name, start_byte)`, so duplicate names such as `["a", "a"]` collide unless the span is the element node.
   - Conditions:
     - Any statement mentioning the identifier `__all__` that is not exactly the one unconditional literal form, including inside control blocks, emits `dynamic_all_not_indexed` and gives the module no exports.
     - Each export span is its string element node. Add a duplicate-name test.
     - Add limitation 15, `variables_aliases_and_type_aliases_not_indexed`. Module-level `X = f()`, annotated names and `type X = ...` are neither declarations nor exports, which the current 14 limitations do not say.

4. MINOR: grammar guard and provisioning details.
   - Evidence: `test_s07_records.py:9,15` and `test_s14_limitations.py:7,14` already call `get_parser` at runtime. `ci.yml:114-117` has no prefetch.
   - Provisioning the python grammar in a session fixture is therefore consistent with existing CI. It does not violate AC-007, because the importer's own guard never downloads and the fixture runs outside every measured window.
   - Conditions:
     - Provision via `get_parser("python")` in a session fixture. It must fail, not skip, if unavailable.
     - The empty-cache subprocess must redirect with `pack.configure(cache_dir=<tmp>)`, not `HOME`. The default cache location differs between macOS and Linux (`aci-s15/qualification/sdk-before.json:4`).
     - Evidence for `has_language` being local-only is only the "has_language('rust') False" note (`aci-s15.md:62`). Day-1(a) must show all three of: empty cache returns False with no files created; the same call after a local `download(["python"])` returns True; and the empty-cache case under blocked network returns promptly.
     - Any exception from `get_parser` after the guard maps to `python_parser_unavailable`.
     - The docs must give the exact provisioning command. Without it, a plain pip install gets `python_parser_unavailable` with no path forward.

5. MINOR: relative-import resolution has a wrong-fact edge (plan line 241).
   - A top-level module such as `six.py` has an empty package. A level-1 import would resolve to a top-level name, but Python raises ImportError there.
   - Condition: when the base package is empty and level ≥ 1, `resolved_module` is null. Add a test.

6. MINOR: lock pre-scan and profile precision (plan lines 123-129).
   - The backslash-then-`e`/`x` rule is not escape-aware, so `\\e` or `\\x` (an escaped backslash followed by a letter) is a false positive. Bracket depth ignores strings, so unbalanced brackets in strings can drift. uv omits `revision` when it is 0, so an absent `revision` is a profile question.
   - Conditions:
     - The backslash rule counts runs, and only an odd run followed by `e` or `x` triggers it.
     - An absent `revision` gives `unsupported_lock_profile`.
     - The Day-1(b) measurements are run against the real lock and a lock with `\\e`.

7. MINOR: pin three boundary decisions in tests and docs.
   - Normalise `--env python_full_version` components (strip leading zeros) before it enters the id, because `3.012.4` otherwise changes `snapshot_id`.
   - AC-004 reading: a selected variant with an unsupported source returns `unsupported_source_kind`, not `unresolved_lock_variant`. Add a case with a registry candidate and a git candidate that both match: it must give `multiple_matching_variants`.
   - AC-006 reading: a package with no locked pure wheel returns `no_locked_pure_wheel` and carries the coordinate. State this in the docs.

8. MINOR: zip rules.
   - Evidence: plan line 194 uses `ev.relative_path(name.rstrip("/"))`. `evidence.py:263-272` accepts `dir`, so `dir//` passes after the strip.
   - Conditions:
     - Strip exactly one trailing slash.
     - Say whether directory entries are validated and excluded from `members` and `counts.members`.
     - The ratio cap of 200 is a constant and applies to every member. Legitimate wheels with compressible data files would be rejected. Make it a limit or apply it to `python_source` members only.

9. MINOR: version equality is exact (plan lines 150, 208).
   - Legitimate wheels with unnormalised version strings in their filename or METADATA would be rejected or excluded.
   - Condition: record this as a limitation and test it. No new dependency is allowed.

10. MINOR: rejection must not disturb an existing output.
    - Condition: in AC-003 and every rejection test, pre-create `--output` with sentinel bytes and assert it is byte-unchanged and no temp file remains.

11. MINOR: audit-test design.
    - Evidence: plan line 549.
    - The grammar is loaded by native code, so `ctypes.dlopen` and `open` events will not fire for it. The "paths under cache_dir" assertion is vacuous for the grammar. Prove the load instead by non-empty parsed declarations and an identical `cache_dir` listing before and after.
    - Condition: `open` events are allowed only for module files covered by a recorded `import` event. Any lazy import in the window raises `exec`, so pre-import everything.
    - The positive control must not run `os.system` with anything other than a no-op.

12. MINOR: pin two outcomes.
    - Add a BOM-prefixed `.py` case. Pin the actual outcome (probably `syntax_error`), because offsets must stay relative to the member bytes.
    - Document that one construct the grammar does not parse, such as PEP 750 t-strings, drops the whole member as `syntax_error`.

13. MINOR: production-image and gate details.
    - Evidence: `Dockerfile:89` prefetches under `HOME=/tmp`; `Dockerfile:102` sets `ENV HOME=/tmp`.
    - Conditions:
      - `prod-image.sh` must not mount a tmpfs over `/tmp`, which would hide the grammar cache.
      - It must record `cmp` and `sha256sum` availability.
      - Run `ruff format --check` on `generate.py` and `mypy src/` as soon as the pack calls (`has_language`, `cache_dir`) exist, in case the stubs are untyped. Do not wait for Day 5.

Invariants: the tools JSON hash, the four budgets and the gated paths are untouched by the plan as written. Cut order is sound. The only cut that could affect these conditions is cut 2, and conditions 1 and 11 are the reason it should stay a last resort.

## Stories
As an agent operator, I want the declaration surface of exactly the dependency my uv.lock pins, read from a wheel I supply, so that agents never reason about a same-name package or the latest online docs.

As a reviewer, I want every fact tied to the lock digest, environment and wheel digest, with dynamic or unsupported content marked incomplete, so that declaration-only evidence is never mistaken for call binding.

## Acceptance Criteria

Frozen bytes: `.spectra/plans/aci-s28.criteria.md` (sha256 `bf6704d7…0f524`), verbatim:

```
### AC-S28-001 (event-driven)
GIVEN one selected uv.lock package entry matches the declared target environment
WHEN the local wheel is accepted
THEN the importer SHALL identify the package by its ecosystem, normalized name, locked version, source identity and verified artifact digest.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_exact_coordinate

### AC-S28-002 (event-driven)
GIVEN a hash-matching local wheel contains the fixture's supported .py declaration forms
WHEN the dependency surface is indexed
THEN the importer SHALL attach the supported Python declarations to the verified package snapshot.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_declarations_are_package_scoped

### AC-S28-003 (unwanted-behavior)
GIVEN the supplied wheel digest does not match the selected lock entry
WHEN artifact validation runs
THEN the importer SHALL reject the wheel before publishing package facts.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_hash_mismatch_rejected

### AC-S28-004 (unwanted-behavior)
GIVEN the declared environment does not select exactly one supported package variant
WHEN lockfile selection runs
THEN the importer SHALL return unresolved_lock_variant.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_ambiguous_variant_rejected

### AC-S28-005 (event-driven)
GIVEN the same lockfile, target environment, wheel digest and importer version
WHEN the dependency is imported twice
THEN the canonical dependency fact set SHALL remain identical after repeat import.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_reimport_is_idempotent

### AC-S28-006 (unwanted-behavior)
GIVEN no matching local wheel is supplied
WHEN the operator requests that dependency's surface
THEN the importer SHALL return missing_dependency_source for the selected locked coordinate.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_missing_source_is_explicit

### AC-S28-007 (ubiquitous)
THEN the dependency importer SHALL obtain source facts exclusively by parsing the supplied local artifacts.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_import_is_offline_nonexecuting

### AC-S28-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S28-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
```

### Fixtures (`tests/fixtures/python_dependencies/`, `.gitattributes` = `* -text\n`)
`generate.py` is stdlib only, with a raw STORED zip writer, fixed headers, no dates, and ASCII-only source. It embeds every member as a bytes literal, so no wheel source exists as a `.py` file, and nothing is collected by pytest or linted as package code. It writes:

1. **`s28_demo_pkg-1.2.0-py3-none-any.whl`:**
   - `s28_demo_pkg/__init__.py`:
     - a side effect (writes `$S28_SENTINEL_PATH`, calls `os.system`, raises);
     - literal `__all__ = ["Widget", "make_widget"]`;
     - `from .core import Widget, make_widget`, `from . import helpers as h`.
   - `s28_demo_pkg/core.py`:
     - `class Widget` with a `def`, an `async def`, `@staticmethod`, `@property` + setter, nested `Widget.Config` and a 5-deep class chain (→ `nesting_limit_exceeded`);
     - `def make_widget`, `async def fetch`, `@functools.lru_cache def cached`;
     - `import os.path`, `import json as j`, `from typing import Any`;
     - `if TYPE_CHECKING:` def; `try: import ujson`;
     - a def inside a function (not indexed).
   - `s28_demo_pkg/sub/__init__.py`: `from ..core import Widget`, `from ...outside import x` (→ null), `from .mod import *`.
   - `s28_demo_pkg/sub/mod.py`: `def __getattr__`, `__all__ = ["a"] + extra`.
   - `s28_demo_pkg/helpers.py`: plain functions.
   - `s28_demo_pkg/broken.py`: a syntax error after `class Phantom:`.
   - `s28_demo_pkg/latin.py`: invalid UTF-8.
   - Other members: `s28_demo_pkg/core.pyi`, `s28_demo_pkg/_speed.cpython-312-x86_64-linux-gnu.so` (opaque bytes), `s28_demo_pkg/data.json`, `s28_demo_pkg/bad-name.py`, `s28_demo_pkg-1.2.0.data/scripts/tool.py`.
   - `setup.py` (sentinel side effect, `def main`).
   - `s28_demo_pkg-1.2.0.dist-info/{METADATA (Name: S28.Demo_Pkg, Version: 1.2.0), WHEEL (Root-Is-Purelib: true, Tag: py3-none-any), RECORD}`.
2. **`single.uv.lock`** (v1 r3):
   - `s28-demo-pkg` 1.2.0 registry `https://pypi.org/simple`, with the pure wheel (real hash/size), a `cp312-cp312-manylinux_2_17_x86_64` wheel and an sdist;
   - an editable root;
   - `gitdep`, `pathdep`, `dirdep`, `urldep`, `virtualdep`, `localreg` (registry = a path);
   - `sdistonly`, `platonly`, `md5only`.
3. **`forked.uv.lock`** (hand-authored, marked unverified in provenance unless Day-1 (h) succeeds):
   - top-level `resolution-markers`;
   - `s28-demo-pkg` 1.2.0 (`python_full_version >= '3.12'`) and 1.1.0 (`< '3.12'`);
   - `splitpkg` (`sys_platform == 'win32'` / `!= 'win32'`);
   - `overlap` (both true at 3.12);
   - `extrapkg` (`extra == 'extra-3-foo-cpu'`);
   - `unmarked` (two entries, no markers);
   - `soloforked` (one entry, `python_full_version < '3.10'`).

Also committed: `s28_demo_pkg-1.2.0.snapshot.json` (the golden, produced by the importer from `single.uv.lock` with an empty env; NOT written by `generate.py`) and `provenance.json` (`{file:{sha256, bytes, purpose}}`, `authored`). Crafted archives and lock variants are built in-test through the generator's writer, which `test_s28_support.py` loads by path.

**Assertion rules.**
- Every `all(...)` is preceded by a non-empty assertion. Nothing about the behaviour under test is mocked.
- The instrumentation is: delegating counters on `zipfile.ZipFile` and on `get_parser`; recording readers in `supplied`; `os.open` recording in CLI runs; S26 spies; the subprocess audit hook.
- A session fixture provisions the python grammar via `get_parser("python")` OUTSIDE every measured window. This is test provisioning, like the Dockerfile prefetch.

| AC | Test | Observable |
| --- | --- | --- |
| 001 | test_python_dependency_exact_coordinate | **Library and CLI (single lock, empty env):**<br>- `package` equals exactly `{ecosystem:"pypi", name:"s28-demo-pkg", version:"1.2.0", coordinate:"pypi:s28-demo-pkg@1.2.0", source:{kind:"registry", registry:"https://pypi.org/simple"}, artifact:{filename, sha256 = sha256(file) = lock hash, bytes, url, digest_check:"equals_lock_entry"}}`;<br>- `--package S28.Demo_Pkg` and `s28_demo-pkg` give the same bytes;<br>- `lock.sha256` = sha256(lock bytes), with `profile`/`version`/`revision`;<br>- the forked lock with `--env python_full_version=3.12.4` selects 1.2.0 (positive control) and the coordinate matches;<br>- `dist_info` name/version/tags;<br>- the CLI summary `coordinate` and `artifact_sha256` equal the snapshot;<br>- `snapshot_id` recomputes from the basis. |
| 002 | test_python_dependency_declarations_are_package_scoped | - The `(module, kind, qualified_name, decorated)` set equals the fixture table exactly, including `async_method`, nested `Widget.Config` and the property setter twice.<br>- No `Phantom`; nothing from `latin.py`, `bad-name.py`, `.pyi`, `.data/` or function-local defs.<br>- Every record has `package_snapshot_id == snapshot_id`, and `member_sha256` = sha256 of the member bytes read independently via `zipfile` = `members[path].sha256`.<br>- `member_bytes[start:end]` starts with `class X`/`def x`/`async def x`.<br>- The `parent_id` chain is correct.<br>- Exports are exactly `{Widget, make_widget}` in `s28_demo_pkg`.<br>- The imports table includes `resolved_module` `s28_demo_pkg.core` and the null for `...outside`.<br>- The `(member, code)` diagnostics multiset equals the expected one.<br>- `coverage` is exact; `modules[].incomplete` is as expected; `validate_snapshot` passes.<br>- **Scoping:** the same wheel under a lock whose registry differs (in-test) gives a different `snapshot_id`, and every record id differs while `(member, kind, qualified_name)` stays equal. |
| 003 | test_python_dependency_hash_mismatch_rejected | Cases:<br>- (a) a byte-flipped copy with the locked basename in tmp;<br>- (b) a valid different wheel under the same name;<br>- (c) the genuine wheel with a lock carrying a different hash;<br>- (d) the genuine wheel with a lock `size` off by one.<br>Each gives `artifact_digest_mismatch` with the coordinate; the `ZipFile` counter is 0 and the `get_parser` counter is 0.<br>CLI: exit 1 with the exact text, empty stdout, `--output` absent, and no `.atlas-python-dependency-*` temp. |
| 004 | test_python_dependency_ambiguous_variant_rejected | **Forked lock:**<br>- no env → `environment_incomplete`;<br>- `overlap` at 3.12.4 → `multiple_matching_variants`;<br>- `s28-demo-pkg` at `python_full_version=3.9.1` with only 1.2.0/1.1.0 markers ≥3.12/<3.12 → selects 1.1.0 (control); `soloforked` at 3.12 → `no_matching_variant`;<br>- `extrapkg` → `unsupported_marker`; `unmarked` → `unmarked_variant`;<br>- `splitpkg` with `sys_platform=linux` → selected (control).<br>Each rejection carries `unresolved_lock_variant` with the detail, and no reader is called.<br>CLI with `--wheel` pointing at a nonexistent path named as a locked filename → `Python dependency rejected: unresolved_lock_variant` (not "Wheel file cannot be read."). |
| 005 | test_python_dependency_reimport_is_idempotent | **Byte-identical:**<br>- two library imports;<br>- CLI into `out1`/`out2`; a rerun over `out1`;<br>- the lock and wheel copied to other directories;<br>- `--env` given in a different order;<br>- an extra non-matching `--wheel`;<br>- `python -m atlas_aci import-python-dependency` with `PYTHONHASHSEED=1/2`;<br>- equality with the committed golden (3.12 locally, 3.14 in CI).<br>**Also:**<br>- stdout identical except `wheels_ignored`;<br>- every id recomputes.<br>**Decided:**<br>- a different env value that selects the same variant changes `snapshot_id` (the env is identity);<br>- a re-serialised but equivalent lock changes `lock.sha256` and `snapshot_id`, while `(member, kind, qualified_name)` sets stay equal. |
| 006 | test_python_dependency_missing_source_is_explicit | - No `--wheel` → library `missing_dependency_source` with `coordinate == {…, expected_artifacts:[{filename:"s28_demo_pkg-1.2.0-py3-none-any.whl", sha256}]}`; CLI exit 1, stderr exactly `Error: Python dependency rejected: missing_dependency_source pypi:s28-demo-pkg@1.2.0`, empty stdout, no output.<br>- Non-matching supplies give the same reason: another name; the same name at 1.1.0; the genuine wheel renamed; the locked platform-wheel name. Their readers/paths are never opened (`os.open` recorder and FIFO paths).<br>- `ambiguous_dependency_source` for the same basename twice.<br>- **Real lock:** `mcp-server/uv.lock --package packaging` → `missing_dependency_source pypi:packaging@26.0` with sha `b36f1fef…7529`. |
| 007 | test_python_dependency_import_is_offline_nonexecuting | **(1) Subprocess** `python -I -c` with a module-flag audit hook:<br>- pre-imports modules only, warms nothing else, activates, runs `import_dependency` from in-memory bytes (the first grammar load is inside the window), deactivates;<br>- zero `socket.*`, `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`, `os.spawn`, `os.fork`, `exec`, `compile` events;<br>- every `open`/`ctypes.dlopen` path lies under `pack.cache_dir()` (the grammar) per Day-1 (a); no `import` event names `s28_demo_pkg` or `setup`;<br>- liveness window: a deliberate `socket.socket()`, `compile` and `open(os.devnull)` ARE recorded.<br>**(2) In-process CLI run** with S26 spies (Popen, os exec/spawn/fork/system, asyncio subprocess, `socket.connect`, `create_connection`) and their liveness call:<br>- `os.open` read paths == {lock, matched wheel};<br>- the `sys.modules` delta has no wheel top-level name.<br>**(3) Sentinel:**<br>- `S28_SENTINEL_PATH` is absent after (1) and (2);<br>- positive control: executing the `__init__.py` bytes via `python -I -c` outside every window DOES create it.<br>**(4) Empty-cache subprocess** (cache redirected per Day-1 (a)): `python_parser_unavailable`, the cache directory is still empty, < 5 s.<br>**(5) Static scan** of `dependencies/*.py`:<br>- imports ⊆ allowlist (`__future__, hashlib, io, re, stat, zlib, tomllib, zipfile, dataclasses, typing, collections.abc, tree_sitter_language_pack, atlas_aci.storage.evidence, atlas_aci.dependencies.uv_lock`, plus `importlib.metadata` only if Day-1 (g) requires it);<br>- banned: `os, sys, pathlib, socket, urllib, http, subprocess, importlib (else), runpy, zipimport, ctypes, pickle, marshal, pkgutil, tempfile, shutil, threading, multiprocessing`;<br>- banned calls: `eval, exec, compile, __import__, open`, and attributes `.extract`, `.extractall`, `.download`, `.configure`;<br>- a planted positive control. |
| RUN-01 | gate | The SHA-256 manifest of the declared tracked implementation files is equal before and after clean-worktree re-entry; `git diff --exit-code <output_commit>` for those paths. |
| RUN-02 | gate | `slice_id` and `criteria_sha256` are equal in the interrupted and resumed receipts; the single receipt is `.spectra/state/aci-s28.execution.json`. |

### Supporting tests
- **`test_s28_lock.py`.**
  - Each D15 lock-stage code: `lock_invalid` for BOM, non-UTF-8, depth 17, depth 100000, LF in an inline table, `\e`, a 200-digit run (also in a subprocess with `PYTHONINTMAXSTRDIGITS=0`), a float, `version = true`, no `package`, a non-table entry, a non-str name, a TOML syntax error, a bad candidate version.
  - Profiles r2/r4/v2.
  - Every unsupported source kind, plus real-lock `atlas-aci`.
  - `no_locked_pure_wheel` for sdist-only, platform-only, md5, uppercase hex, and real-lock `tree-sitter-language-pack` (per Day-1 (b)).
  - The marker table: positives, and negatives for every D4 exclusion and cap.
  - PEP 503 and PEP 427 tables.
- **`test_s28_archive.py`** — one case per D7 rule:
  - `invalid_wheel`: garbage, truncated, CRC flip, declared-size lie, central/local name mismatch;
  - `unsafe_archive_member`: `../`, absolute, backslash, `C:/x`, `C:x`, NUL, `\n`, `a//b`, `./a`, non-ASCII with and without the flag, duplicate, case duplicate, file/dir prefix, symlink, FIFO, a directory with data, the encrypted bit, bzip2/lzma methods, overlapping offsets, prepended bytes, a 1025-character name;
  - `archive_limit_exceeded`: count, total, member, ratio;
  - metadata: two dist-infos, Name/Version mismatch, missing METADATA, non-UTF-8 METADATA, Wheel-Version 2.0 → `wheel_metadata_mismatch`; purelib false and a cp312 tag → `not_pure_python`;
  - a DEFLATED in-test wheel imports (positive control).
- **`test_s28_cli.py`.**
  - Exit 2 for every bad limit (including bool), `--env` and `--package`, and for an output equal to the lock or a wheel (same path, symlink, hardlink).
  - Exit 1 for a missing parent and for a directory output, with no temp left.
  - Lock and wheel unreadable or FIFO → fixed texts.
  - Every rejection → exact stderr with no tmp path or input sentinel text, and empty stdout.
  - Exactly one new file in the output directory.
- **`test_s28_isolation.py`.**
  - Tools JSON sha and bytes; `READ_ONLY_TOOLS == 7`; 3436/3448/797/7730.
  - `import-python-dependency` ∈ `cli.commands`; "python-package"/"pydep" are absent from the tools JSON.
  - `dependencies/*.py` == the three files; ASCII; lines ≤ 100; no `\d`/`$` regex; no self-recursion; no "alter table" or `nx.compose(`.
  - Vocabulary: every constant, key and enum value avoids `BANNED_VOCABULARY` (planted hit).
  - `validate_snapshot` passes on every output and rejects each tampering: `snapshot_id`, a record id, `package_snapshot_id`, `member_sha256`, unsorted records, an unknown key/kind/code, a dangling `parent_id`, counts, `coverage`, `incomplete`.
  - Fixture provenance: sha and bytes match, and `generate.py` into tmp reproduces every generated file byte-for-byte.
  - **Docs:** `docs/importers/python-dependencies.md` names every rejection, detail, diagnostic code, kind, role, parse state, env key, limitation, limit and constant; every format string and CLI text; and the phrases "does not install", "does not download", "never executes", "declaration_only", "not_claimed", "not written to the graph", "CLI only, no MCP op", "not bound to a source snapshot", "python_parser_unavailable" and "hand-authored".
  - The CHANGELOG entry (sliced at `\n- **`) and the README row contain "declaration_only", "does not install" and "CLI only, no MCP op".

### Gates beyond the ACs
- **G-SRC.**
  - `git diff --exit-code 8d7dd73 -- mcp-server/src/atlas_aci/{server.py,enforcement.py,config.py,codegraph.py,protocol,query,tools,storage,extractors,providers,indexing,resolvers,frameworks,architecture,counterfactual,contracts,importers,overlays,reports} mcp-server/pyproject.toml mcp-server/uv.lock mcp-server/Dockerfile mcp-server/Dockerfile.dev .github/workflows`
  - `git diff --name-only 8d7dd73 -- mcp-server/src` ⊆ {`dependencies/__init__.py`, `dependencies/uv_lock.py`, `dependencies/python_wheel.py`, `__main__.py`}.
  - Test changes are only `test_s28_*.py` and `fixtures/python_dependencies/**`.
- **G-MCP.** Tools JSON `94f9567a…` / 1631472; `READ_ONLY_TOOLS` 7.
- **Regression.** `test_s08*`, `test_s14*`, `test_s16*`, `test_s27*`, `test_s29*`, `test_thesis_negatives`, `test_export` are green and unchanged.
- **CI-exact lint.** `uv run --frozen ruff check src/ tests/`, `uv run --frozen ruff format --check src/ tests/`, `uv run --frozen mypy src/`.
- **Full suite** with `--deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition`. No new failure or skip vs Day-1 (f).
- **Production image** (`.spectra/evidence/aci-s28/prod-image.sh`, written by the executor and run by the orchestrator):
  1. Build `mcp-server/Dockerfile`.
  2. `docker run --rm --network none --read-only --tmpfs /out -v <fixtures>:/fx:ro -v <mcp-server/uv.lock>:/lk/uv.lock:ro --entrypoint sh`.
  3. Import twice to `/out/a.json` and `/out/b.json` → `cmp` equal; the sha equals the golden and the host run.
  4. A byte-flipped copy in `/out` → exit 1 `artifact_digest_mismatch` and no output.
  5. Forked lock with no env → exit 1 `unresolved_lock_variant`.
  6. `/lk/uv.lock --package packaging` with no wheel → exit 1 `missing_dependency_source pypi:packaging@26.0`.
  7. The success run itself proves the python grammar loads offline.
  8. `atlas-aci tools --repo /fx` matches the pin.
  9. Informational: the same under `Dockerfile.dev`.

```sh
cd mcp-server
uv run --frozen pytest tests/slices/test_s28_python_dependency.py -q
uv run --frozen pytest tests/slices/test_s28_*.py -q
uv run --frozen pytest tests/slices/test_s08_*.py tests/slices/test_s14_*.py tests/slices/test_s16_*.py tests/slices/test_s27_*.py tests/slices/test_s29_*.py tests/test_thesis_negatives.py tests/test_export.py -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen pytest -q --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition
uv run --frozen ruff check src/ tests/ && uv run --frozen ruff format --check src/ tests/ && uv run --frozen mypy src/
```

## Confidence
To be computed with `ramza-score`.
- **Why it is low-churn.** The slice is a pure importer writing a standalone artifact, using established precedents (S29 identity, S27 I/O and output). There is no graph, protocol or tools change.
- **Main uncertainties**, covered by Day 1 with a stop rule:
  - grammar locality, since the downloader is native;
  - the uv fork shape (hand-authored);
  - zipfile and tomllib behaviour across 3.12/3.14.

## Rejected Alternatives
- **(B) A derived graph generation (S16/S21 style).** It edits gated paths, every publish changes the consumer snapshot id, and it inherits S16's 14 conditions.
- **(C) `ast` as the primary extractor.** CPython grammar acceptance and recursion limits differ between 3.12 and 3.14, and it emits `compile` events. It is kept only as the STOP-RULE fallback.
- **Matching wheels by content or a renamed filename.** Exact locked basenames keep AC-006 deterministic and need no file opens.
- **Verifying RECORD.** The lock digest already authenticates the bytes (D7).
- **Accepting more uv revisions.** Unverified.
- **NFC/casefold name checks.** The Unicode database differs between interpreters; ASCII-only names instead.
- **Importing `overlays.runtime` output helpers.** That couples to S27 reasons and drags in query/diff.
- **An in-process audit hook.** It cannot be removed; it runs in a subprocess instead.

## Risks (ranked)
1. **Native grammar download is invisible to Python spies.**
   - Mitigation: the D8 guard, the empty-cache test and the production image under `--network none`.
   - Dev environments without a cached grammar get `python_parser_unavailable` by design (documented).
2. **uv fork and marker semantics are unverified.** The subset fails closed; there is a real-lock smoke test and the docs disclose it.
3. **zipfile patch-level differences** (overlap detection, NUL handling). Our own offset, CRC and name checks run before reliance; Day-1 (c).
4. **TOML differences across 3.12/3.14.** The byte pre-scan rejects newer-TOML constructs and digit runs.
5. **Error-recovery phantoms.** A member with any error gets no facts; tested with `Phantom`.
6. **Vocabulary drift** ("verified" in the AC text). The isolation test scans constants and keys.
7. **Timebox (5 days).** Follow the cut order; at the limit, report and propose a follow-up.
