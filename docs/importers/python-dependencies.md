# Python dependency surface from a uv lock and a local wheel (ACI-S28)

One operator command, CLI only, no MCP op. It is **not** an MCP tool: the server still exposes
exactly seven read-only tools and the tools JSON is unchanged.

```sh
atlas-aci import-python-dependency --lock uv.lock --package NAME [--env KEY=VALUE]... \
    [--wheel PATH]... --output OUT [limits]
```

The command reads the `uv.lock` that pins ONE dependency, selects the single locked variant for
the declared environment, matches a wheel you supply by its locked filename, checks the wheel's
sha256 against that lock entry and then reads the archive in memory. The result is one
standalone `atlas-python-package-snapshot.v1` written to `--output`. It prints a short
`atlas-python-dependency-import-summary.v1` on stdout. Nothing is printed on rejection (exit 1)
or invalid usage (exit 2).

## What this is, and what it is not

- **The importer does not install, does not download and never executes anything.** The wheel is
  a zip archive that is read as bytes: no member is extracted to disk, no package code is
  imported or run (a module body, `setup.py` or a build backend is only ever parsed), no network
  is used and no subprocess is started. The facts come exclusively from the supplied local
  artifacts. The lock is read, never rewritten.
- **Selection happens before any artifact is opened.** An unmatched `--wheel` path is never
  opened (a FIFO or a missing file is harmless) and its count appears only in the stdout
  summary as `wheels_ignored`, never in the snapshot.
- **Declaration only.** Every snapshot says `evidence_coverage: "declaration_only"`,
  `completeness: "incomplete"`, `call_binding: "not_claimed"` and `execution: "not_performed"`.
  There are no calls, no call graph and no cross-package import resolution.
- **Not written to the graph.** The snapshot is a separate file. The code graph, the evidence
  store and the repository are not touched and the snapshot is not exposed over MCP (CLI only,
  no MCP op). It is **not bound to a source snapshot**: `uv.lock` is not part of any consumer
  lease, so the lock is bound by its own sha256 only (limitation
  `lock_bound_by_digest_not_to_a_source_snapshot`). Binding it to a consumer snapshot is a
  follow-up.
- **The digest is the authentication.** `package.artifact.digest_check` is
  `equals_lock_entry`: the wheel bytes hash to the digest in the selected lock entry. `RECORD`
  is not consulted (`wheel_record_not_consulted`): it is producer-asserted data inside the very
  bytes that digest already covers.

## Providing the Python grammar

Members are parsed with tree-sitter's python grammar, which must already be in the local grammar
cache. The importer never fetches it. Without it the import stops with
`python_parser_unavailable` (exit 1). Provision it once, with network access, before importing:

```sh
python -c "from tree_sitter_language_pack import download, get_parser; download(['python']); get_parser('python')"
```

The production image already holds it (the image prefetches the grammars under `HOME=/tmp`), so
`--network none --read-only` works there. Locality is decided by
`tree_sitter_language_pack.downloaded_languages()`, which only lists the cache directory;
`has_language` is not used because it is false for an on-disk grammar until the first load.
Any failure after that check is the same `python_parser_unavailable`. The extractor identity is
`{parser: "tree-sitter", language: "python", grammar_version, abi_version}` where
`grammar_version` is the installed `tree-sitter-language-pack` release (read once at import) and
`abi_version` is the loaded grammar's ABI.

## The lock profile `atlas-uv-lock-profile.v1`

Only `version = 1`, `revision = 3` is accepted (limitation `uv_lock_version_1_revision_3_only`).
A lock written by a newer uv (for example uv 0.13.6 writes `revision = 5`) or an absent
`revision` is `unsupported_lock_profile`; a revision is added only after it has been checked against a real lock.

Reading is bounded and independent of the interpreter: a byte pre-scan runs before TOML parsing
and rejects (`lock_invalid`) a byte-order mark, non-UTF-8 bytes, a bracket depth above 16
(`MAX_TOML_DEPTH`), a line break inside an inline table, an odd run of backslashes followed by
`e` or `x`, and a run of more than 128 digits or underscores (`MAX_DIGIT_RUN`). A float, a
non-integer `version` or `revision`, or a missing `package` list is `lock_invalid`. Only the
`name`, `version`, `source`, `resolution-markers` and `wheels` of the candidate entries are read.

The pre-scan is linear in the lock size; at the default 32 MiB cap its worst case is a few
seconds (measured 7-9 s of CPU for 32 MiB of brackets under load).

Selection, in order:

1. `--package` is normalised (PEP 503, ASCII only) and every entry with that name is a candidate;
   none is `package_not_in_lock`.
2. Every `resolution-markers` entry of every candidate is parsed (at most 64 per entry,
   `MAX_MARKERS`), then evaluated against `--env`.
3. One candidate: no markers selects it; markers (OR) that are all false give
   `no_matching_variant`. Several candidates: any without markers is `unmarked_variant`;
   otherwise exactly one must match: none is `no_matching_variant`, two or more is
   `multiple_matching_variants` (a registry and a git candidate that both match are ambiguous
   too). All of these are `unresolved_lock_variant` with the stated `detail`; the CLI line names
   only the reason.
4. The selected entry's `source` must be a table with exactly one key, `registry`, holding an
   `http(s)` URL. An editable, git, path, directory, url or virtual source, a registry that is a
   path, or any other key is `unsupported_source_kind` (also when it is the selected variant of
   several). Only a single registry pure wheel is supported (`single_registry_pure_wheel_only`).
5. The locked pure wheels are the `wheels` entries whose URL basename is a PEP 427 filename of
   this name and exactly this locked version, tags `none` / `any`, with a `sha256:` hash of 64
   lowercase hex digits. None is `no_locked_pure_wheel`; the rejection carries the coordinate.
   Versions are compared as exact strings, with `-` written `_` in the filename (limitation
   `version_strings_compared_exactly_not_normalized`): `1.0` does not match `1.0.0`.

`--env KEY=VALUE` is repeatable. Keys: `implementation_name`, `os_name`, `platform_machine`,
`platform_python_implementation`, `platform_system`, `python_full_version`, `sys_platform`.
`python_full_version` is `N.N` or `N.N.N` and is canonicalised (`3.012.4` becomes `3.12.4`)
before it enters the identity. `python_version` is derived from its first two components and
exists only inside markers. A key given twice, an unknown key or a malformed value is a usage
error (`Invalid --env value.`, exit 2). The environment is part of the snapshot identity.

The marker grammar is a restricted subset (limitation `restricted_marker_subset`):
`expr := term ("or" term)*`, `term := atom ("and" atom)*`, `atom := "(" expr ")" | VAR OP LITERAL`,
with the variable always on the left. Version variables take `== != < <= > >=` against up to four
numeric components, and `==` / `!=` also accept a numeric prefix such as `'3.10.*'`. String
variables take `==` and `!=`. Everything else (`not`, `in`, `~=`, `===`, `extra`, `group`, any
other variable, a reversed operand order, an empty or dangling marker, unbalanced parentheses) is
`unsupported_marker`. Caps: 1024 characters, parenthesis depth 8, 256 tokens
(`marker_limit_exceeded`). A variable that is referenced but absent from `--env` is
`environment_incomplete`, even when another marker is true. `requires-python` is not evaluated
(`requires_python_not_evaluated`).

The forked fixture `forked.uv.lock` is **hand-authored** at revision 3 and unverified against uv.
Day-1 qualification compared the per-package `resolution-markers` and marker spelling with a real
lock produced by uv 0.13.6 (which writes revision 5, so it is outside this profile); see
`.spectra/evidence/aci-s28/qualification.json`.

## Wheel handling (before any fact is published)

The order is fixed and the first failure wins:

1. Size above `max_wheel_bytes` is `wheel_too_large`.
2. The sha256 and (when the lock records one) the size are compared with the selected lock
   entry: `artifact_digest_mismatch`. **No archive object exists before this passes.**
3. The archive must open (`invalid_wheel` otherwise, including a failing CRC or a size that
   does not match the central directory).
4. Count, total and per-member limits and a compression-ratio cap on **every** member:
   `archive_limit_exceeded`. A member larger than 1024 bytes whose uncompressed size exceeds
   `max_compression_ratio` times its compressed size is refused (default 200); compressible
   data files above that ratio need a higher `--max-compression-ratio`.
5. Entry rules, all `unsafe_archive_member`: a name that differs from its raw form (NUL), a
   non-ASCII name, NUL / C0 / DEL / backslash / `:`, a name over 1024 characters, an unsafe
   relative path (`..`, `.`, absolute, drive, `//`; exactly one trailing slash is stripped
   first), a duplicate or ASCII-case duplicate, a file that is also a directory prefix, any
   flag bit besides the standard ones (so encryption), a method other than stored or deflated,
   a mode that is neither a regular file nor a directory (symlink, FIFO, device), a directory
   entry with data or a trailing-slash entry that is not a directory, local headers that
   overlap, a wrong local signature, or bytes before the first entry. **Directory entries are
   validated and excluded from `members` and from `counts.members`.**
6. `<name>-<version>.dist-info/` must be the single top-level dist-info of this coordinate;
   `METADATA` must name it (`Name`, `Version`) and `WHEEL` must declare `Wheel-Version` 1.x, else
   `wheel_metadata_mismatch`. `Root-Is-Purelib: true` and only `*-none-any` tags, else
   `not_pure_python`.

Each regular member is hashed (`members[].sha256`) and classified by `role`: `python_source`
(`.py`), `stub` (`.pyi`), `extension` (`.so`, `.pyd`, `.dylib`, `.dll`), `data` (under
`*.data/`), `dist_info` or `other`. Only `python_source` members are parsed. Every other
non-dist-info member carries one diagnostic: `stub_file_not_indexed`,
`compiled_extension_not_indexed`, `data_directory_not_indexed` or
`non_python_member_not_indexed` (limitation `stubs_extensions_and_data_not_indexed`).

## What is indexed (`python_source` members only)

A member that is not strict UTF-8 is `parse_state: "non_utf8_source"` (diagnostic
`non_utf8_source`). A path that is not an importable module name is `not_parsed` (diagnostic
`non_importable_module_path`; `module` is null). A member the grammar reports any error for is
`syntax_error` and contributes **no** declarations, imports or exports, so error recovery never
invents a phantom. One construct the grammar cannot parse therefore drops the whole member as
`syntax_error`. A parsed member is `parsed`. Offsets are always relative to the member's own
bytes: a byte-order mark is part of those bytes (a BOM-prefixed class starts at byte 3, column 3).

- **Declarations**, from module and class bodies, unconditional only: kinds `class`, `function`,
  `async_function`, `method`, `async_method`; `decorated` is true for a decorated definition and
  the span is the definition itself. Classes nest to depth 4 (`MAX_CLASS_NESTING`); a deeper
  class is `nesting_limit_exceeded` and its subtree is skipped. Definitions and imports inside
  function bodies, and imports in class bodies, are not indexed
  (`local_definitions_and_imports_not_indexed`). Module-level variables, annotated names,
  aliases and `type X = ...` are neither declarations nor exports
  (`variables_aliases_and_type_aliases_not_indexed`).
- **Control blocks** (`if`, `for`, `while`, `try`, `with`, `match`) at module or class level are
  not indexed (`conditional_definitions_and_imports_not_indexed`): one
  `conditional_definition_not_indexed` and/or `conditional_import_not_indexed` per statement.
- **Imports** (module level, unconditional; one `import_path` per imported name) record the
  statement, level, target, name, alias and `resolved_module`. A relative import resolves
  against the member's own package; when the base package is empty (a top-level module) or the
  level climbs past it, `resolved_module` is null. A `*` import adds `star_import_not_expanded`.
  They are paths, not edges, and are not resolved across packages
  (`imports_are_paths_not_resolved_across_packages`).
- **Exports**: exactly one unconditional module-level `__all__ = [...]` or `(...)` whose elements
  are plain quoted identifiers; the span is each string element and a repeated name gets its own
  record. Any other statement that mentions `__all__` (`+=`, `.extend`, an annotation, a tuple
  unpack, an assignment inside a control block, a read in a function, a second assignment, a
  non-literal element) gives `dynamic_all_not_indexed` and the module has no exports
  (`dynamic_exports_not_modeled`).
- A module-level `def __getattr__` adds `dynamic_module_getattr` (the function is still a
  declaration). Identifiers are recorded as written (`identifiers_recorded_as_written`).

## The snapshot `atlas-python-package-snapshot.v1`

Canonical JSON (sorted keys, compact) plus one newline; no clock, host, path of an operator
file, limit or zip date enters it. `importer` is `atlas-aci import-python-dependency` /
`atlas-python-dependency-importer.v1`. Top-level keys: `format`, `snapshot_id`, `importer`,
`extractor`, `lock` (`profile`, `version`, `revision`, `sha256`), `environment`, `package`
(`ecosystem` `pypi`, normalised `name`, `version`, `coordinate` `pypi:<name>@<version>`,
`source`, `artifact`), `dist_info`, `coverage`, `counts`, `limitations`, `members`, `modules`,
`declarations`, `imports`, `exports`, `diagnostics`.

`snapshot_id = sha256("atlas-python-package-snapshot.v1:" + canonical({importer, extractor, lock,
environment, package}))`, so the lock bytes, the environment, the wheel digest, the importer and
the extractor are the identity. Record ids are sha256 of the canonical list
`["atlas-pydep-<type>.v1", snapshot_id, member, ...]`. Every record carries
`package_snapshot_id`, `member` and `member_sha256`, so a fact can never be read without the
package, version, source and digest it came from. Re-importing the same inputs gives the same
bytes (the golden is `s28_demo_pkg-1.2.0.snapshot.json`). `validate_snapshot` re-computes every
id, count, order and derived field and rejects anything else as `invalid_snapshot`.

Closed vocabularies: declaration kinds above; roles `python_source`, `stub`, `extension`, `data`,
`dist_info`, `other`; parse states `parsed`, `syntax_error`, `non_utf8_source`, `not_parsed`;
diagnostic codes `compiled_extension_not_indexed`, `conditional_definition_not_indexed`,
`conditional_import_not_indexed`, `data_directory_not_indexed`, `dynamic_all_not_indexed`,
`dynamic_module_getattr`, `nesting_limit_exceeded`, `non_importable_module_path`,
`non_python_member_not_indexed`, `non_utf8_source`, `star_import_not_expanded`,
`stub_file_not_indexed`, `syntax_error`. `modules[].incomplete` is true when any diagnostic names
the member.

Limitations stated in every snapshot (`limitations`): `declaration_only_no_call_binding`,
`dynamic_exports_not_modeled`, `conditional_definitions_and_imports_not_indexed`,
`local_definitions_and_imports_not_indexed`, `stubs_extensions_and_data_not_indexed`,
`imports_are_paths_not_resolved_across_packages`,
`lock_bound_by_digest_not_to_a_source_snapshot`, `single_registry_pure_wheel_only`,
`uv_lock_version_1_revision_3_only`, `restricted_marker_subset`, `requires_python_not_evaluated`,
`wheel_record_not_consulted`, `identifiers_recorded_as_written`,
`not_written_to_the_graph_and_not_exposed_over_mcp`,
`variables_aliases_and_type_aliases_not_indexed`,
`version_strings_compared_exactly_not_normalized`.

## Rejections and limits

Rejections (closed, in stage order), all exit 1 with nothing on stdout: `lock_too_large`,
`lock_invalid`, `unsupported_lock_profile`, `package_not_in_lock`, `unresolved_lock_variant`
(details `environment_incomplete`, `marker_limit_exceeded`, `multiple_matching_variants`,
`no_matching_variant`, `unmarked_variant`, `unsupported_marker`), `unsupported_source_kind`,
`no_locked_pure_wheel`, `missing_dependency_source`, `ambiguous_dependency_source`,
`wheel_too_large`, `artifact_digest_mismatch`, `invalid_wheel`, `unsafe_archive_member`,
`archive_limit_exceeded`, `wheel_metadata_mismatch`, `not_pure_python`,
`python_parser_unavailable`, `surface_limit_exceeded`, `invalid_snapshot`.

- `missing_dependency_source` names the selected coordinate: no supplied basename equals a
  locked pure-wheel filename (a renamed wheel does not match). Two matching supplied wheels are
  `ambiguous_dependency_source`.
- A rejection never replaces or touches an existing `--output`; the output is written only
  after the snapshot is complete, through a temporary file in the same directory and an atomic
  rename.

| Limit | Option | Default | Range |
| --- | --- | --- | --- |
| `max_lock_bytes` | `--max-lock-bytes` | 33554432 | 1..268435456 |
| `max_wheel_bytes` | `--max-wheel-bytes` | 67108864 | 1..268435456 |
| `max_members` | `--max-members` | 10000 | 1..100000 |
| `max_total_uncompressed_bytes` | `--max-total-uncompressed-bytes` | 268435456 | 1..1073741824 |
| `max_member_bytes` | `--max-member-bytes` | 16777216 | 1..268435456 |
| `max_records` | `--max-records` | 200000 | 1..1000000 |
| `max_compression_ratio` | `--max-compression-ratio` | 200 | 1..10000 |

Limits accept or reject inputs and never change content; an out-of-range value is a usage error.

## CLI texts

Every line follows Click's `Error: `. The path of an operator file and any exception message
are never echoed.

| Situation | Exit | Text |
| --- | --- | --- |
| bad limit | 2 | `Invalid Python dependency limit: <name>` |
| bad `--env` | 2 | `Invalid --env value.` |
| bad `--package` | 2 | `Invalid --package name.` |
| output equals the lock or a wheel | 2 | `--output must differ from the lock and every wheel.` |
| lock unreadable or not a regular file | 1 | `uv lock file cannot be read.` |
| matched wheel unreadable or not regular | 1 | `Wheel file cannot be read.` |
| rejection before selection | 1 | `Python dependency rejected: <reason>` |
| rejection after selection | 1 | `Python dependency rejected: <reason> pypi:<name>@<version>` |
| write failure | 1 | `Python dependency snapshot cannot be written.` |
| anything else | 1 | `Python dependency import failed: <TypeName>` |

## Production image

`.spectra/evidence/aci-s28/prod-scenario.sh` runs the command in the production image under
`--network none --read-only` with the fixtures mounted read-only and the output directory
outside `/tmp` (a tmpfs over `/tmp` would hide the prefetched grammar).
