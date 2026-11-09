# atlas-aci

Give your coding agent seven read-only tools it cannot misuse.

[![CI](https://github.com/Rynaro/atlas-aci/actions/workflows/ci.yml/badge.svg)](https://github.com/Rynaro/atlas-aci/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)](mcp-server/pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-server-6e44ff.svg)](https://modelcontextprotocol.io)

<p>
<a href="#quickstart">Quickstart</a> &middot;
<a href="#the-seven-tools">The seven tools</a> &middot;
<a href="#why-read-only">Why read-only</a> &middot;
<a href="#mechanical-bounds">Mechanical bounds</a> &middot;
<a href="#language-support">Languages</a> &middot;
<a href="#does-it-hold-up">Does it hold up?</a> &middot;
<a href="#when-not-to-use-it">When not to use it</a> &middot;
<a href="#documentation">Docs</a>
</p>

atlas-aci is a Model Context Protocol (MCP) server that lets a coding agent explore a repository through seven bounded, read-only tools. It builds a local code graph with tree-sitter and answers from that graph. It makes no LLM calls and needs no API key.

The agent is the untrusted party in this design. The server has no tool that writes files or runs arbitrary commands. The one tool that executes code, `test_dry_run`, is off unless you opt in. Every response is capped by code before the agent sees it.

## Quickstart

You need Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and ripgrep (`rg`) on your PATH.

```bash
cd mcp-server && uv sync                          # once, from a checkout of this repo
uv tool install .                                 # puts atlas-aci on your PATH

atlas-aci index --repo /path/to/your/repo         # builds the code graph in <repo>/.atlas/
atlas-aci tools --repo /path/to/your/repo | jq -r '.[].name'
# you should see the seven tool names, in this order:
# view_file
# list_dir
# search_text
# search_symbol
# graph_query
# test_dry_run
# memex_read
```

Add `.atlas/` to the repo's `.gitignore`. The index is derived data and can always be rebuilt.

Next, register the server in your host. For Claude Code, add an entry like this to the MCP configuration described in [hosts/claude-code.md](hosts/claude-code.md):

```json
{
  "mcpServers": {
    "atlas-aci": {
      "command": "atlas-aci",
      "args": ["serve", "--repo", "/path/to/your/repo"]
    }
  }
}
```

Restart the host. It should list the same seven `atlas-aci` tools. Without `--memex-root`, the excerpt store lives at `<repo>/.atlas/memex`.

For the full walkthrough, read [SETUP.md](SETUP.md). To onboard an existing codebase, read [INTEGRATION.md](INTEGRATION.md).

<details>
<summary>Copilot, Cursor and Docker</summary>

Copilot custom agent, in the agent's frontmatter (see [hosts/copilot.md](hosts/copilot.md)):

```yaml
tools:
  mcp_servers:
    - name: atlas-aci
      transport: stdio
      command: ["atlas-aci", "serve", "--repo", "${workspaceFolder}"]
```

Cursor: add a custom server in Settings with transport `stdio`, command `atlas-aci` and arguments `serve --repo /path/to/your/repo`. The same JSON as above works in `~/.cursor/mcp.json`. See [hosts/cursor.md](hosts/cursor.md).

Docker, with the published image:

```bash
docker run --rm -v /path/to/your/repo:/repo ghcr.io/rynaro/atlas-aci index --repo /repo
docker run --rm -i --read-only -v /path/to/your/repo:/repo:ro -v atlas-memex:/memex \
  ghcr.io/rynaro/atlas-aci serve --repo /repo --memex-root /memex
```

The newest published tag is `v3.0.1` (image `ghcr.io/rynaro/atlas-aci:3.0.1`). It prefetches the tree-sitter grammars, so it indexes without network access. Building locally from `main` gives changes made after 3.0.1. [docs/installation.md](docs/installation.md) covers how to build the image locally.

</details>

## The seven tools

Each tool's output passes the same bounds before the agent sees it. The defaults live in [`config.py`](mcp-server/src/atlas_aci/config.py).

| Tool | What it answers | Hard bound |
| --- | --- | --- |
| `view_file` | Read a window of lines from a UTF-8 text file. Binary files are rejected. | 100 lines and 8 KiB per call. `next_cursor` pages the rest. |
| `list_dir` | What is in this directory? The skip-list applies (`node_modules`, `.git`, `.atlas`, and more). | 200 entries per call. Overflow is flagged. |
| `search_text` | Where does this regex match? Ripgrep, smart-case by default. | 50 matches per call. 15 s timeout. |
| `search_symbol` | Where is this name defined and referenced? Needs an index. | 200 items per list field. Responses capped at 1 MiB. |
| `graph_query` | Who calls this, what inherits from it, what matters most? Needs an index. | The same caps. Unknown verbs are rejected. |
| `test_dry_run` | Run one test file. Off unless the operator opts in. | 30 s. 8 KiB per stdout and stderr stream. No OS sandbox. |
| `memex_read` | Read a saved excerpt back by its `memex://excerpt/<sha256>` ref. | Byte-exact. No ATLAS tool emits refs today. |

The process as a whole accepts 200 calls per minute. Excess calls get a structured error. Every call is logged to stderr as a `tool_call` event.

### graph_query in six lines

`graph_query` takes a DSL string in its `query` argument. Six examples, with placeholder names:

```text
{"query": "definitions_of:Config"}
{"query": "callers_of:dispatch_tool_call"}
{"query": "subclasses_of:Base"}
{"query": "god_nodes:"}
{"query": "communities:"}
{"query": "rationale:"}
```

Edges from `callers_of` and `subclasses_of` carry a `confidence` label: `EXTRACTED`, `INFERRED` or `AMBIGUOUS`. The labels come from the parser's structure and are never produced by a model. `god_nodes:` and `communities:` analyze only the confident edges, and their responses say so. Treat `EXTRACTED` as a floor, not an exact count: the shadowing guard can under-claim but never over-claim. The same tool also takes structured operations (`status`, `neighborhood`, `path`, `page`, `continue`, `retrieve`, `context`, `impact`). The full reference is in [docs/graph-query-dsl.md](docs/graph-query-dsl.md).

## Why atlas-aci

- **Read-only by construction.** An allowlist of seven tool names is checked before any tool code runs. Every other name returns `FORBIDDEN`. [Why read-only](#why-read-only)
- **One bounds chokepoint.** Every response passes a single check that caps list fields and total size, so a new tool cannot forget to cap. [Mechanical bounds](#mechanical-bounds)
- **A real code graph.** Definitions, call sites and inheritance for 11 languages, parsed with tree-sitter and stored in a local SQLite index. [Language support](#language-support)
- **No LLM calls, no API key.** Indexing and queries run as local programs, and a test fails the build if the server imports an LLM SDK. [Does it hold up?](#does-it-hold-up)
- **Deterministic export.** `atlas-aci export` writes canonical JSONL, so a teammate can import a graph instead of re-parsing the repo. [Export and import](docs/how-it-works.md#export-and-import)
- **Signed images.** Published images carry a keyless cosign signature plus SBOM and build-provenance attestations. [Verified releases](#verified-releases)

## How it compares

| Approach | Good for | Tradeoff |
| --- | --- | --- |
| Shell and text search (`rg`, `grep`, `find`) | Exact text, fast, no setup | No structure. A name match is not a definition or a call site. |
| Embedding or RAG search | Fuzzy questions and finding code by meaning | Results are approximate and depend on an embedding model. |
| Read-write agent toolkits (edit, shell, git) | Agents that also change code | The agent can modify the repo, so read-only use depends on prompts and host settings. |
| atlas-aci | Read-only exploration with structure and hard caps | Structural only. No type inference, no compiler semantics, no edits. |

## Why read-only

Most code-navigation servers also offer write tools and rely on instructions to keep them unused. atlas-aci makes the narrower choice. An exploration agent does not need to change anything, so the server offers no way to.

The allowlist is a `frozenset` literal in [`enforcement.py`](mcp-server/src/atlas_aci/enforcement.py). A call to `edit_file`, `write_file`, `shell_exec` or `git_commit` returns `FORBIDDEN` before any tool code runs. Because the agent is the untrusted party, two operations are CLI-only and never tools:

- **`import`** replaces the whole index from a file on disk. An agent that could call it could swap every future `search_symbol` or `graph_query` answer. A hand-made file with a fresh hash passes the same check as a real export.
- **`export`** is a closer call. It never writes to the index, but it writes a file at a path the caller chooses. An export tool would be the first exception to "no tool writes files", so it stays on the CLI.

Run the server against a read-only mount. `--read-only` plus a `:ro` bind mount is an OS-level guarantee, which is stronger than any check in code. If your workflow needs edits, migrations or deploys, pair this server with a different one. Do not add write tools here.

The read-only property covers ATLAS's own tools. `test_dry_run` is the deliberate exception: when you enable it, the test file's code runs with the server's permissions. It is off by default. See [Test execution is opt-in](docs/how-it-works.md#test-execution-is-opt-in).

## Mechanical bounds

Every bound is enforced in code. None is a hint to the model. Defaults are in [`config.py`](mcp-server/src/atlas_aci/config.py).

| Bound | Default | Enforced in |
| --- | --- | --- |
| Lines per `view_file` call | 100 | `enforcement.py` |
| Bytes per call (`view_file`; each `test_dry_run` stream) | 8 KiB | `config.py` |
| Entries per `list_dir` call | 200 | `enforcement.py` |
| Matches per `search_text` call | 50 | `enforcement.py` |
| Items per list field, every tool | 200 | `server.py` chokepoint |
| Serialized response, every tool | 1 MiB | `server.py` chokepoint |
| Calls per minute, whole process | 200 | `enforcement.py` |
| `search_text` wall-clock limit | 15 s | `tools/search_text.py` |
| `test_dry_run` wall-clock limit | 30 s | `config.py` |

The chokepoint matters most. Every response passes through `apply_central_bounds` in [`server.py`](mcp-server/src/atlas_aci/server.py) before serialization. It caps each declared list field on a whole-element boundary and marks the result `truncated`, with `truncated_fields` and `returned_count` so the agent can see what was cut. A response still over 1 MiB after that is refused with `RESPONSE_TOO_LARGE`. A tool can narrow a bound, but it cannot widen one. Errors are structured, with a `retry_hint` of `none`, `narrower_scope` or `different_tool`.

The export size ceiling (AC-REL-2) is GitHub's 100 MiB limit on one committed file. `atlas-aci export` warns on stderr at 50 MiB and again at 100 MiB, and never fails the export. [docs/how-it-works.md](docs/how-it-works.md#export-size-ceiling-ac-rel-2) covers what to do past it.

## Language support

The index covers 11 languages by default. `atlas-aci index --langs` takes a subset.

| Language | Extensions | What the index records |
| --- | --- | --- |
| Rust | `.rs` | Modules, structs, enums, traits, functions, methods. Calls are recorded but unresolved. |
| TSX | `.tsx` | Classes, functions, arrow components, methods, imports. Calls resolve within one file only. |
| Ruby | `.rb` | Classes, modules, methods, calls, inheritance. Callers of setters and operators are not resolved. |
| Python | `.py` | Classes, functions, calls, superclasses. |
| TypeScript | `.ts` | Classes, functions, methods, calls, `extends`. |
| JavaScript | `.js`, `.jsx` | Classes, functions, methods, calls, `extends`. |
| SCSS and CSS | `.scss`, `.css` | Mixins, functions, placeholders, `$variables`, class and id selectors. `@include` sites are references. |
| HTML | `.html`, `.htm` | Elements with an `id`. Symbols only. |
| YAML | `.yml`, `.yaml` | Mapping keys, including nested ones. Symbols only. |
| Markdown | `.md`, `.markdown` | Headings. Symbols only. |
| Bash | `.sh`, `.bash` | Function definitions and command invocations. |
| Go, Java | `.go`, `.java` | Recognized and skipped, with the skip reported. No extraction yet. |

### Rust structural navigation

Rust files are parsed with the locked Tree-sitter Rust grammar. Nothing is compiled. The indexer never runs Cargo, `build.rs` or procedural macros. Every call stays unresolved, because a matching name does not establish which function or trait method is meant. Declarations under `cfg` are recorded as written, so an indexed declaration does not prove it is built into a binary. Details are in [docs/how-it-works.md](docs/how-it-works.md#rust-structural-navigation).

### TSX structural navigation

`.tsx` is parsed with the locked Tree-sitter `tsx` grammar. Nothing runs: no `tsc`, no bundler, no module resolution and no JSX runtime. The matrix below is the contract. Each row names what is structural, what stays unresolved, and the test that backs it.

| Relation | Support | Boundary | Backing test |
| --- | --- | --- | --- |
| `definition: class` | structural | class declarations and class components | `test_s14_contract.py::test_tsx_definitions` |
| `definition: function` | structural | function declarations and arrow/function-expression declarators | `test_s14_contract.py::test_tsx_definitions` |
| `definition: method` | structural | `method_definition` in classes and object literals | `test_s14_contract.py::test_tsx_definitions` |
| `call` | scoped structural | same-file only; imported callees are unresolved | `test_s14_roundtrip.py::test_arrow_component_edge_owner_through_since_and_round_trip` |
| `construct` | scoped structural | `new X()` or `X()` resolving to a same-file class | `test_s14_contract.py::test_jsx_not_fabricated_call` |
| `superclass` | scoped structural | `extends <identifier>` to a same-file class | `test_s14_contract.py::test_jsx_not_fabricated_call` |
| `import` | unresolved occurrence | alias, source, kind and type-only kept; never resolved | `test_s14_contract.py::test_tsx_imports` |
| `jsx` | unresolved occurrence | value tags only; never a call or an edge | `test_s14_contract.py::test_jsx_not_fabricated_call` |
| `rationale_for` | comment-derived | same rules as TypeScript | `test_s14_roundtrip.py::test_rationale_comments_on_tsx` |
| parse failure | diagnostic | `failed`/`parse_error` with location; no leaked facts | `test_s14_contract.py::test_tsx_diagnostic` |
| incremental | equals clean rebuild | rename plus `--since` | `test_s14_contract.py::test_tsx_incremental` |
| replay | warm equals cold | unchanged source, cache hit and miss | `test_s14_contract.py::test_tsx_replay` |
| `limitation: tsx_module_resolution` | `unavailable_all_imports_unresolved` | no tsconfig paths, index files, or package exports | `test_s14_contract.py::test_tsx_imports` |
| `limitation: tsx_jsx_binding` | `value_tags_recorded_unresolved_without_semantic_binding` | no JSX runtime or component binding | `test_s14_contract.py::test_jsx_not_fabricated_call` |
| `limitation: tsx_intrinsic_jsx` | `lowercase_and_namespaced_tags_not_recorded` | host elements are not code references | `test_s14_limitations.py::test_reported_gap_is_real_and_not_silently_fabricated` |
| `limitation: tsx_export_specifiers` | `export_lists_and_reexports_not_recorded` | `export { X }` and `export { X } from` | `test_s14_limitations.py::test_reported_gap_is_real_and_not_silently_fabricated` |
| `limitation: tsx_default_export_identifier` | `export_default_identifier_not_recorded` | `export default Name;` | `test_s14_limitations.py::test_reported_gap_is_real_and_not_silently_fabricated` |
| `limitation: tsx_anonymous_default_export` | `unnamed_declarations_not_recorded` | `export default function () {}` | `test_s14_limitations.py::test_reported_gap_is_real_and_not_silently_fabricated` |
| `limitation: tsx_dynamic_binding` | `import_call_require_and_computed_member_unbound` | `import()`, `require`, `obj[key]()` | `test_s14_limitations.py::test_reported_gap_is_real_and_not_silently_fabricated` |

Out of scope: React-specific flow (hooks, props, context, higher-order components), type inference, and interface, type-alias or enum declarations. Side-effect-only imports (`import './x'`) introduce no binding and are not recorded. The matrix is cross-checked against the runtime capabilities by `test_s14_capabilities.py::test_readme_matrix_matches_capabilities_and_names_real_tests`.

### What the graph does not claim

- Structural labels are not compiler semantics. Every shipped producer reports compiler semantic binding as unsupported.
- `status` reports partial coverage. A file the parser cannot recover is quarantined as a whole, and the generation is published as partial.
- An indexed declaration is source syntax. It is not proof that the code is compiled or reachable.

## Does it hold up?

Start with the cheap checks. They need no key and no bill, and you can run them yourself.

```bash
cd mcp-server
uv run pytest -q                                   # includes the test that forbids LLM SDK imports
uv run atlas-aci export --repo /path/to/your/repo /tmp/a.jsonl
uv run atlas-aci export --repo /path/to/your/repo /tmp/b.jsonl
diff /tmp/a.jsonl /tmp/b.jsonl                     # no output means byte-identical
```

The export commands need an index built first. Each claim below points to its test, its data or its method in the repository.

| Claim | Evidence | The catch |
| --- | --- | --- |
| The server makes no LLM calls | `test_no_llm_client_importable_from_server_package` fails if `anthropic`, `openai` or `boto3` is imported under `src/` | It checks imports in this source tree. It cannot see what a host does with the agent. |
| Export bytes match on macOS and Linux | CI builds one fixture on each OS and fails on any byte difference (AC-REL-1) | The fixture is small. Windows path separators are outside the check. |
| A paired, reproducible evaluation of the v3 server | [docs/evidence/v3/README.md](docs/evidence/v3/README.md): five arms, three token budgets, 30 pinned pytest ids | No model ran. Coding-task success, model tokens and cost are `unmeasured`. |
| The read-only invariants hold | [`test_enforcement.py`](mcp-server/tests/test_enforcement.py) covers the allowlist, path guard and bounds | The tests check these invariants on this repo's fixtures. They do not show your graph is complete. |

<details>
<summary>Run the canary suite and replay the v3 evidence</summary>

Run the canary suite from the repository root with the stdio host:

```bash
uv run python scripts/run-canaries.py \
    --repo /path/to/your/repo \
    --canaries-dir /path/to/tasks \
    --host stdio \
    --output /tmp/canary-results.json
```

`--canaries-dir` holds a `tasks.json` manifest. See [atlas/evals/README.md](atlas/evals/README.md). The stdio host starts the server, replays the manifest's tool calls and records a transport ledger. It runs no model, so model tokens, cost and task success are not measured. To run the same manifest against another server command, such as a container, pass `--server-json` with an inline JSON array or a file path. The other `--host` values (`stub`, `claude-code-api`, `copilot`, `cursor`) raise `NotImplementedError`, so no agent pass rate exists yet. See [SETUP.md §7](SETUP.md#7-run-the-canary-suite).

Replay the v3 evidence. It regenerates the report from the committed records, and a clean `git diff` means it reproduces:

```bash
cd mcp-server
uv run python ../scripts/eval/v3_aggregate.py --manifest ../atlas/evals/v3/manifest.v1.json \
    --raw ../docs/evidence/v3/raw --out ../docs/evidence/v3
```

</details>

Full detail, including what each check does not show, is in [docs/evaluation.md](docs/evaluation.md).

## When not to use it

- **You need edits, migrations or deploys.** Use a different server. This one stays read-only by design.
- **You need an agent to call `export` or `import`.** Both are CLI-only on purpose. See [Why read-only](#why-read-only).
- **You need one committed graph larger than GitHub's file limit.** Re-index instead, or publish the export as a CI artifact. See [Mechanical bounds](#mechanical-bounds).
- **You need exact Ruby setter or operator call sites.** Definitions are captured, but callers of setters and operators are not, so `callers_of` misses them. See [docs/graph-query-dsl.md](docs/graph-query-dsl.md).
- **You need compiler-grade answers.** Type inference, TSX import resolution and Rust trait dispatch are unresolved, and every producer reports compiler binding as unsupported.
- **You would run `test_dry_run` on untrusted code without OS isolation.** The server cannot sandbox a test run. Put it behind a DevContainer or microVM first.

## Security model

- **Allowlist.** Only the seven tool names in `READ_ONLY_TOOLS` run. Everything else returns `FORBIDDEN`.
- **Path guard.** Every path is resolved, symlinks included, and must stay inside `--repo`. Anything outside returns `FORBIDDEN`.
- **Opt-in execution.** `test_dry_run` runs only when `ATLAS_ACI_ENABLE_TEST_EXECUTION=1` is set at startup. Request fields and repository files cannot enable it.
- **Loopback HTTP only.** The optional HTTP transport binds loopback addresses, has no authentication, and refuses to start while test execution is enabled. The `serve-shared` command adds authenticated principals over loopback HTTP. The HTTP transport and shared-operation entries are in the documentation table below.
- **Telemetry.** Each call is logged to stderr with its tool, size, duration and error. Arguments are logged except the `content` field.
- **Read-only mount.** Mount the repo read-only at the OS level, with `:ro` on the bind.
- **Hardening.** Before running unattended on shared infrastructure, follow [SETUP.md §8](SETUP.md#8-production-hardening-checklist).

These are checks inside one server process. They are not an OS sandbox.

## Verified releases

Each `v*` tag produces `linux/amd64` and `linux/arm64` images. The [release workflow](.github/workflows/release.yml) then does the following:

- **Trivy scan.** For GA tags, a HIGH or CRITICAL finding that has a fix fails the release before signing. Findings without a fix are recorded in the SARIF report and do not block.
- **Keyless cosign signature.** It uses GitHub OIDC and no long-lived key.
- **SBOM and provenance attestations.** An SPDX SBOM and a build-provenance attestation are stored in the registry.
- **Digest in the release notes.** Each release lists the exact `ghcr.io/rynaro/atlas-aci@sha256:<digest>` string.

Release candidate and beta tags are signed but are not gated by the Trivy check.

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/Rynaro/atlas-aci/.github/workflows/release.yml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/rynaro/atlas-aci@sha256:<digest>

gh attestation verify oci://ghcr.io/rynaro/atlas-aci@sha256:<digest> --owner Rynaro
```

Pull by digest. A tag can be moved, and a digest cannot. Signing shows which workflow built the image. It does not show that the image has no vulnerabilities the scan did not flag.

## Documentation

The README is the entry point. The documents below hold the depth.

| Document | What it covers |
| --- | --- |
| [`docs/how-it-works.md`](docs/how-it-works.md) | The engine in depth: invariants, test execution, graph storage, extraction cache, incremental binding, source snapshots, Rust detail, `status`, export and import, HTTP transport |
| [`docs/installation.md`](docs/installation.md) | Install paths, Docker invocations and image caveats, development setup, upgrades between schema epochs, hardening pointers |
| [`docs/evaluation.md`](docs/evaluation.md) | Every offline check, the CI gates, the v3 evidence package, and what each claim does not show |
| [`docs/graph-query-dsl.md`](docs/graph-query-dsl.md) | `graph_query` reference: edge shapes, confidence tiers, `god_nodes:`, `communities:`, `rationale:`, structured operations |
| [`atlas/docs/scip-import.md`](atlas/docs/scip-import.md) | Importing an official scip-typescript index (`atlas-aci scip-manifest` / `import-scip`) |
| [`atlas/docs/coverage-import.md`](atlas/docs/coverage-import.md) | Importing per-test Python coverage observations (`atlas-aci coverage-probe` / `coverage-manifest` / `import-coverage`) |
| [`atlas/docs/symbol-diff.md`](atlas/docs/symbol-diff.md) | Comparing two indexed snapshots at symbol and contract boundaries (`atlas-aci diff`) |
| [`atlas/docs/change-impact.md`](atlas/docs/change-impact.md) | Potential change impact through typed reverse paths (`graph_query op=impact`, `atlas-aci impact`) |
| [`atlas/docs/validation-plan.md`](atlas/docs/validation-plan.md) | Advisory test-selection plan from impact and observed coverage (`atlas-aci validation-plan`; CLI only) |
| [`docs/evidence-reports.md`](docs/evidence-reports.md) | One scoped context answer exported as offline HTML and canonical JSON (`atlas-aci evidence-report`; CLI only) |
| [`docs/architecture-rules.md`](docs/architecture-rules.md) | Forbidden-import rules checked against one indexed snapshot (`atlas-aci check-architecture`; CLI only) |
| [`docs/counterfactual-analysis.md`](docs/counterfactual-analysis.md) | Hypothetical analysis of a proposed patch, staged outside the checkout and never published (`atlas-aci counterfactual`; CLI only) |
| [`docs/importers/openapi.md`](docs/importers/openapi.md) | Importing one OpenAPI 3.1 JSON document into a contract snapshot (not a source snapshot) and explaining contract changes between two snapshots; S19 is not invoked, only its change-set shape is reused; provenance is artifact digest plus JSON pointer, binding to a source snapshot is a follow-up (`atlas-aci openapi-snapshot` / `openapi-diff`; CLI only, no MCP op) |
| [`docs/importers/opentelemetry.md`](docs/importers/opentelemetry.md) | Importing one local OTLP JSON trace artifact into a separate runtime overlay bound to the active snapshot: spans are linked to the exact indexed source snapshot or classified as unlinked (build mismatch, missing or malformed location, path outside the declared roots, file not in the snapshot, file not indexed, line out of range), span nesting is recorded as `observed_parent_of` (observed nesting, not a call) and every result carries `observed_sample` coverage; the import does not write the graph, only the operator's `--output` (`atlas-aci import-otel`; CLI only, no MCP op) |
| [`docs/importers/python-dependencies.md`](docs/importers/python-dependencies.md) | Indexing one uv-locked pure-Python dependency from a supplied local wheel as a standalone package snapshot: the lock selects exactly one registry variant for the declared environment, the wheel is matched by its locked filename and its sha256 must equal the lock entry before any archive is opened, then module, class and function declarations, import paths and literal `__all__` exports are read from the zip in memory (`declaration_only`, `call_binding: not_claimed`); the importer does not install, does not download and never executes package code, the snapshot is not written to the graph, and the python grammar must already be cached locally (`atlas-aci import-python-dependency`; CLI only, no MCP op) |
| [`docs/cross-repository-links.md`](docs/cross-repository-links.md) | Linking one Python consumer and one provider by an exact package revision vector: an explicit mapping, the consumer's `uv.lock`, the package snapshot and the provider's indexed sources must agree byte for byte, then a module-level `from pkg import name` becomes a link to the provider declaration under the ordered consumer/provider snapshot pair (not a call, `call_semantics: not_claimed`); a different version or source digest is `unresolved_version_mismatch`, an unavailable provider snapshot is a `missing_provider_snapshot` boundary, and a refresh records the links the changed vector invalidated; the overlay does not write the graph (`atlas-aci cross-repo-link` / `cross-repo-impact`; CLI only, no MCP op) |
| [`docs/history-signals.md`](docs/history-signals.md) | Bounded git co-change correlations: for one explicit commit window (`start_exclusive_end_inclusive`) each pair of files that changed together in the same eligible commit, with its exact support and the supporting commit ids, under a declared merge/generated/bulk filter policy whose digest every record carries; the indexed snapshot is leased read-only and bound to the source commit by git blob ids, and a shallow, missing or over-budget history is declared `incomplete_history` with the records withheld; a correlation, not a dependency and not causality; the overlay does not write the graph (`atlas-aci cochange`; CLI only, no MCP op) |
| [`docs/rationale-drift.md`](docs/rationale-drift.md) | Explicit ADR-to-source links: the operator maps a byte span of a decision document to an exact indexed endpoint (an entity id, or a file), resolved against one snapshot with the document read from that snapshot and the span checked by digest; between two snapshots a source change while the whole document is byte-identical is flagged `rationale_review_needed`, an unresolved link or an uncomparable target stays visible and is never rebound; an observation, not a verdict, and the results do not write the graph (`atlas-aci rationale-map`, `atlas-aci rationale-drift`; CLI only, no MCP op) |
| [`docs/verified-feedback.md`](docs/verified-feedback.md) | Verified test-outcome feedback: a claimed pytest outcome for retrieved results is recorded as a `verified_artifact` event only when a JUnit XML artifact, the saved retrieval record and the active snapshot agree (the digest proves bytes, not that a run occurred); an optional, versioned overlay moves bundles up to three slots, lists the supporting event ids and ignores stale events; without `--overlay` the order is the baseline ranker's (CLI only, no MCP op) |
| [`docs/experiments/community-comparison.md`](docs/experiments/community-comparison.md) | Offline comparison of an optional, version-pinned Leiden provider against the shipped label propagation over identical frozen graphs, with a preregistered manifest digest, exact re-derivable metrics and a mechanical outcome (`retain_lpa`, `leiden_candidate_for_review`, `inconclusive` or `skipped_missing_provider` when the provider is absent); the production default remains label propagation (`scripts/experiment-leiden-vs-lpa.py`, `scripts/verify-community-experiment.py`; no MCP op) |
| [`hosts/protocol-compatibility.md`](hosts/protocol-compatibility.md) | Which MCP protocol revisions the server was observed to serve (2024-11-05, 2025-03-26, 2025-06-18 and the selected 2025-11-25), how a revision is negotiated, what a client sees for an unsupported or malformed request, and what was tested: two declared fixture hosts against golden transcripts and the official schemas, with real hosts stated as untested |
| [`docs/frameworks/rails.md`](docs/frameworks/rails.md) | Opt-in Rails adapter: literal `config/routes.rb` routes mapped to controller actions with a convention-derived, `INFERRED` `routes_to` relation, ambiguity candidates and unsupported-construct diagnostics; build-time only, never executes Ruby (`atlas-aci index --frameworks rails`; no MCP op); and, as a second adapter, explicit model associations, `self.table_name` / operator-map tables and literal migration table references (`--frameworks rails_models [--rails-table-map FILE]`; no MCP op, never touches a database); and, as a third adapter, direct `Worker.perform_async` / `Job.perform_later` enqueues linked by a distinct asynchronous `async_enqueue` relation to the explicit `perform` method with fixed positional argument-to-parameter mappings and closed-vocabulary diagnostics (`--frameworks rails_jobs`; no MCP op, never enqueues or runs a job) |
| [`hosts/http-transport.md`](hosts/http-transport.md) | Optional loopback-only Streamable HTTP transport (`atlas-aci serve --transport http`): one stateless `POST /mcp` endpoint with SSE responses, a closed order of rejections (Host 421, Origin 403, path 404, method 405, in-flight gate 503 with `Retry-After`, body cap 413), a client disconnect that cancels the request in flight (a documented deviation from the MCP specification), and the limits left to S40; no authentication, so it is refused while test execution is enabled |
| [`SETUP.md`](SETUP.md) | End-to-end playbook: install → host wiring → canaries → hardening |
| [`docs/operations/shared-http-deployment.md`](docs/operations/shared-http-deployment.md) | Shared multi-principal operation (`atlas-aci serve-shared`): the shared-config and policy formats, offline JWT verification, the router and guard order, revocation, and what is not isolated |
| [`docs/operations/index-jobs-and-retention.md`](docs/operations/index-jobs-and-retention.md) | Durable index jobs (`atlas-aci jobs`): identity and reuse, checkpoints and resume, cancellation, retained generation farms, cross-process reader leases and lease-aware garbage collection. |
| [`docs/evidence/s42/README.md`](docs/evidence/s42/README.md) | Shared-operation readiness evidence (ACI-S42): tested deployment model, evidence access ledger, interruption matrix, tested client and transport combinations, one bounded workload record and unresolved limits; not a deployment |
| [`INTEGRATION.md`](INTEGRATION.md) | Onboarding an existing codebase: indexing, skip tuning, freshness, multi-repo |
| [`CLAUDE.md`](CLAUDE.md) | How Claude Code should navigate this repo when opened in an agent loop |

## FAQ

**Does it call a model or need an API key?**
No. Indexing, queries and export are local programs. A test checks that the server source does not import the `anthropic`, `openai` or `boto3` SDKs.

**Why is a symbol missing?**
Check that the index exists with `atlas-aci status --repo /path/to/your/repo`, which prints JSON. Then check the language is in the default set. Ruby setter callers and JSX references are known gaps.

**Can one server cover several repositories?**
No. Run one process per repository and give each host entry its own name. See [INTEGRATION.md](INTEGRATION.md#multi-repo-setups).

**Which Docker image should I use?**
The published `v3.0.1` image (`ghcr.io/rynaro/atlas-aci:3.0.1`) is the newest tag. Building locally from `main` gives changes made after 3.0.1: `docker build -t atlas-aci mcp-server`.

**Do I need to re-index after an upgrade?**
Only when the schema epoch changes. `serve` still starts, and graph tools return `INDEX_UNAVAILABLE` until a current index exists. See [docs/installation.md](docs/installation.md).

## Contributing

Open issues and pull requests on GitHub. Host wiring questions start in [`hosts/`](hosts/), since most editor problems are configuration. To run the test suite, use `cd mcp-server && uv sync && uv run pytest`. Scripts under [`scripts/`](scripts/) guard this repo's own workstreams and are not shipped code.

## License

Apache-2.0. See [LICENSE](LICENSE). The package metadata in [`mcp-server/pyproject.toml`](mcp-server/pyproject.toml) declares the same license.

---

*If a tool cannot be called, it cannot be misused.*
