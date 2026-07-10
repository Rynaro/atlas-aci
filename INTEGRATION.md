# Seamless integration with your codebase

This guide walks an operator from "I have a repo" to "my agent can
explore it through the ATLAS bounded ACI" — the onboarding gap between
[`SETUP.md`](SETUP.md) (which installs the server) and
[`hosts/*.md`](hosts/) (which wire the server into your editor).

If you haven't installed `atlas-aci` yet, do that first — start at
[`SETUP.md §1`](SETUP.md#1-install-prerequisites) and come back here
once `uv run atlas-aci --help` prints its banner.

---

## Who this is for

You have an existing repository — small, large, monorepo, polyglot,
doesn't matter — and you want an AI agent that can **read** it through
a bounded interface. No `edit_file`, no `shell_exec`, no write
primitives. Just exploration: view files, list dirs, search text,
resolve symbols, query the code graph, run one test file, read back
captured excerpts.

This doc is the "how do I make that work against *my* code" path.

---

## The 5-minute version

```bash
# 1. Index your repo (one-time; incremental updates are cheap)
uv run atlas-aci index --repo /path/to/your/repo \
    --langs ruby,python,javascript,typescript

# 2. Start the stdio server pointed at that repo
uv run atlas-aci serve --repo /path/to/your/repo

# 3. Wire it into your host
#    → hosts/claude-code.md
#    → hosts/copilot.md
#    → hosts/cursor.md
```

That's the happy path. The rest of this doc is everything you'll want
to know the second that doesn't feel "seamless" — which languages are
supported, what to exclude, how to keep the index fresh, what Docker
looks like, and the pitfalls that bite first-time operators.

---

## Step 1 — Point the server at your repo

`atlas-aci serve --repo <path>` is the root of everything. The `<path>`
is:

- **A single repo.** Multi-repo setups run one process per repo — see
  [Multi-repo setups](#multi-repo-setups) below.
- **Resolved to an absolute path on start.** Symlinks are followed
  once; the resolved path becomes the traversal boundary. Any tool
  call whose path resolves outside this boundary is rejected with
  `FORBIDDEN` — see
  [`config.py`](mcp-server/src/atlas_aci/config.py) `is_in_repo()`.
- **The root the server will create `.atlas/` inside.** That subtree
  holds the code-graph index and (by default) the Memex excerpt store.

No configuration file. The flags on `atlas-aci serve` and
`atlas-aci index` are the whole surface.

---

## Step 2 — Build the code-graph index

`search_symbol` and `graph_query` both require a pre-built index.
Without it, they return empty — the server doesn't crash, it just
silently has nothing to say. So always index before wiring.

```bash
uv run atlas-aci index --repo /path/to/your/repo \
    --langs ruby,python,javascript,typescript
```

What this produces, rooted at `<repo>/.atlas/`:

| File | What it holds |
|------|---------------|
| `graph.<epoch>.db` | SQLite: symbols, refs (caller/callee adjacency), and a `manifest` table (schema epoch) and `files` table (per-file incremental-indexing state) — see [`CLAUDE.md`](CLAUDE.md) |
| `memex/` | Hashed-dir KV store for `memex_read` excerpts (default location) |

There is no separate `symbols.db` or `manifest.yaml` file — everything above
lives inside the one epoch-namespaced `graph.<epoch>.db` SQLite file.

**Timing.** A ~100k-file Rails monorepo on a modern laptop indexes in
roughly 30–120 seconds. Python/JS/TS-only repos are faster. Tree-sitter
is the dominant cost; SQLite writes are noise.

**Where the index lives.** Inside `<repo>/.atlas/`. That matters for
two reasons:

1. You want `.atlas/` in your `.gitignore` (see Step 5).
2. Docker needs a **writable** repo mount during indexing but a
   **read-only** mount during serving. See
   [Running in Docker](#running-in-docker).

---

## Step 3 — Pick your language set

The `--langs` flag defaults to every grammar the indexer ships with:
`ruby,python,typescript,javascript,scss,html,yaml,markdown,bash`.
Pass a comma-separated list to index only a subset.

| Language | Notes |
|----------|-------|
| `ruby` | Uses `tree-sitter-ruby` out of the box. There is no separate Ruby specialist mode shipped today — this is the only Ruby support. |
| `python` | Supported via `tree-sitter-language-pack`. |
| `javascript` | Supported via `tree-sitter-language-pack`, including `.jsx`. |
| `typescript` | Supported via `tree-sitter-language-pack` for `.ts`. **`.tsx` is recognized but not yet queried** — files are skipped with a visible `unsupported extension skipped` report, not silently indexed to nothing. |
| `scss` | Mixins, functions, placeholders, `$variables`, and class/id selectors become defs; `@include` sites become refs. The SCSS grammar also parses plain `.css`. |
| `html` | Elements carrying an `id` are indexed as anchor / JS-hook targets. |
| `yaml` | Every mapping key (incl. nested) is a lookup target — covers `_config.yml`, `_data/*`, and front matter files. |
| `markdown` | ATX and setext headings index the document outline (`.md` / `.markdown`). |
| `bash` | Function definitions are defs; command invocations are refs, so `callers_of:<fn>` resolves call sites (`.sh` / `.bash`). |

`.go`, `.rs`, and `.java` are likewise recognized extensions with no query
support yet — same visible skip behavior, not a coverage commitment.

> The `scss`/`html`/`yaml`/`markdown`/`bash` grammars make ATLAS useful on
> **static-site repos** (Jekyll, Hugo, plain HTML/SCSS) where the symbols worth
> jumping to are stylesheet tokens, element ids, data keys, and headings rather
> than classes and methods.

**Mixed-language repos.** Index everything you care about in a single
pass. The indexer is fine with polyglot trees — it just walks files
and dispatches to the right grammar.

**Languages not listed.** Tree-sitter has dozens of grammars available
through the language pack; adding one is a handful of lines in
[`codegraph.py`](mcp-server/src/atlas_aci/codegraph.py). This is the
first place you're likely to need a code change when onboarding a new
stack.

---

## Step 4 — Exclude generated code and vendored trees

The indexer and the content tools (`list_dir`, `search_text`) honor a
hardcoded skip-list in
[`config.py`](mcp-server/src/atlas_aci/config.py) —
`DEFAULT_SKIP_PATTERNS`. Out of the box it excludes:

```
node_modules   vendor/bundle   vendor/cache   tmp
log            .git            dist           build
public/assets  public/packs    public/packs-test
coverage       .bundle         __pycache__    .venv
.atlas         storage         _site          .jekyll-cache
.sass-cache
```

That's a Rails/Node/Python/Jekyll-centric default (`_site`,
`.jekyll-cache`, and `.sass-cache` keep generated static-site output out
of the index). If your repo has other
noisy trees — generated protobuf stubs, snapshot fixtures, migration
dumps, large checked-in binaries — you have two options today:

1. **Edit `DEFAULT_SKIP_PATTERNS`** in `config.py` and rebuild. Quick
   but requires forking.
2. **Construct `Config(skip_patterns=…)` programmatically** if you're
   embedding the server instead of running the CLI.

A config-file override path is a known ergonomics gap; track it as a
follow-up if the default list doesn't fit your repo.

---

## Step 5 — Add `.atlas/` to your `.gitignore`

```bash
echo '.atlas/' >> .gitignore
```

The index is a build artifact: rebuildable, machine-local, and
potentially large (tens to hundreds of MB for big monorepos). It also
contains full-text excerpts in Memex. You don't want it committed and
you definitely don't want it in a PR diff.

`.atlas` is already in `DEFAULT_SKIP_PATTERNS` so the server won't
*list* it back to the agent — but that's a runtime filter, not a VCS
rule. Add the gitignore entry.

---

## Step 6 — Keep the index fresh

The index is a snapshot of whatever `HEAD` looked like when you ran
`atlas-aci index`. As the repo drifts, symbol lookups grow stale.
Three strategies, pick based on how often the repo changes:

### A — Manual re-index (simplest)

Re-run `atlas-aci index --repo <path>` whenever you pull new code and
plan to do a meaningful exploration session. Good enough for a solo
developer.

### B — Incremental re-index on `post-commit`

The `--since <marker>` flag enables incremental mode: it does **not** diff
the git ref you pass — the marker's value is never read — it keys purely on
each file's on-disk `(mtime_ns, size)` versus the last indexed pass, skipping
anything unchanged. Any truthy value works as the marker; `HEAD~1` below is
just a readable convention, not something the indexer resolves. A
lightweight `post-commit` hook:

```bash
# .git/hooks/post-commit
#!/usr/bin/env bash
uv run atlas-aci index --repo "$(git rev-parse --show-toplevel)" \
    --since post-commit >/dev/null 2>&1 &
```

Fire-and-forget, runs in the background, typically finishes in well
under a second per commit on incremental runs.

### C — CI job on `main` + a portable JSONL export (v2.0.0 / A5, preferred)

For teams, index once per merge to `main` and ship the graph, so
developers pull a pre-built index instead of building locally. As of
v2.0.0, prefer `atlas-aci export` over tarring up the raw `.atlas/`
SQLite file: the export is canonical, byte-deterministic JSONL (D6) —
git-diffable, and reproduces an identical DB on `import` regardless of
which machine built it or which one imports it. The raw `.atlas/`
tarball still works (nothing in v2.0.0 removes it), but it ships an
opaque binary blob tied to the exact `SCHEMA_EPOCH` it was built under,
with no way to diff or verify it, and no defined merge/conflict story.

```yaml
- name: Build and export the atlas-aci graph
  run: |
    uv run atlas-aci index --repo . --langs ruby,python,javascript,typescript
    uv run atlas-aci export --repo . graph-export.jsonl
- uses: actions/upload-artifact@v4
  with:
    name: atlas-graph-export
    path: graph-export.jsonl
```

Developers then run `atlas-aci import --repo . graph-export.jsonl`
instead of `atlas-aci index` — a cold start with no re-parsing. `import`
is idempotent and rejects a truncated, hand-edited, or wrong-epoch file
with a clean error rather than a partial or silently-wrong index.
`export`/`import` are CLI-only (never MCP tools a served agent can call)
— see [`README.md` § Why read-only](README.md#why-read-only) for why.

If your conflict-resolution workflow ever needs to reconcile two
divergent exports, the answer is: **regenerate, don't merge.** There is
no semantic graph/union merge driver, by design (D6-Q2) — discard the
conflicted file and re-run `index` + `export`.

---

## Step 7 — Wire your editor

Pick your host and follow its wiring doc. These cover the MCP
configuration specific to each client — frontmatter, config files,
restart steps.

- [`hosts/claude-code.md`](hosts/claude-code.md) — Claude Code
  (best-ranked host; one JSON edit)
- [`hosts/copilot.md`](hosts/copilot.md) — GitHub Copilot custom
  agents (MCP in agent frontmatter)
- [`hosts/cursor.md`](hosts/cursor.md) — Cursor (Settings UI or
  `~/.cursor/mcp.json`)

After wiring, restart the host. When the agent asks `tools/list`, it
should see exactly the seven ATLAS tools. Anything else means the
server is non-conformant — check
[`README.md §The seven tools`](README.md#the-seven-tools) for the
expected manifest.

---

## Step 8 — Your first exploration

A good smoke-test mission against a real repo:

> "Locate the rate limiter in this codebase. Report: which file
> defines it, what class or function, how clients obtain an instance,
> and where the bound (requests per window) is configured. Cite every
> claim with `file:line`."

What a healthy scout report looks like:

- Every claim has a `file:line` reference.
- The agent used `search_symbol` or `graph_query` to find the
  definition, not just `search_text`.
- No `view_file` calls on paths inside `node_modules/`, `.git/`, or
  other skipped trees.
- The run completed under the rate limit (200 calls/minute) and no
  tool call tripped the per-call byte cap.

**If the agent reports "I couldn't find it"** and your codebase
definitely has a rate limiter, the likely cause is an unindexed
language. Re-run `atlas-aci index` with the right `--langs` and try
again.

---

## Running in Docker

The [`mcp-server/Dockerfile`](mcp-server/Dockerfile) ships with two
documented invocations — they're also in the file's header comment.

**Index phase** (writable mount, one-time):

```bash
docker run --rm \
    -v /path/to/repo:/repo \
    atlas-aci index --repo /repo --langs ruby,python,javascript,typescript
```

The `.atlas/` directory ends up on the host filesystem inside the
repo, because Docker's bind mount is just a view into the real
directory.

**Serve phase** (read-only mount, long-lived):

```bash
docker run --rm -i --read-only \
    -v /path/to/repo:/repo:ro \
    -v atlas-memex:/memex \
    atlas-aci
```

A few notes:

- `--read-only` on the container + `:ro` on the bind mount is
  belt-and-suspenders. The OS-level guarantee is stronger than any
  code-level check — if the server binary is somehow compromised, it
  still cannot write to your repo.
- The `atlas-memex` named volume keeps excerpt bytes persistent across
  container restarts. Drop it if you want per-session ephemeral memex.
- `-i` (interactive) is required because MCP is stdio-based — the
  host on the outside speaks JSON-RPC into stdin.

For the dev loop — pytest, ruff, mypy, CLI smoke — use
[`mcp-server/Dockerfile.dev`](mcp-server/Dockerfile.dev). That image
is documented in its own header comment.

---

## Multi-repo setups

`atlas-aci` is **one process per repo**. There is no "workspace" or
"root directory" abstraction; the `--repo` flag is the traversal
boundary and the enforcement layer's path-traversal guard is hard-wired
to it.

If you need ATLAS against several repos, run several processes. Your
host can register multiple MCP servers — Claude Code's `mcpServers`
object, Copilot's agent frontmatter, Cursor's `~/.cursor/mcp.json` all
take arbitrary keys. Name them per repo:

```json
{
  "mcpServers": {
    "atlas-my-repo": {
      "command": "uv",
      "args": ["run", "atlas-aci", "serve", "--repo", "/path/to/your-repo"]
    },
    "atlas-internal-tools": {
      "command": "uv",
      "args": ["run", "atlas-aci", "serve", "--repo", "/path/to/internal-tools"]
    }
  }
}
```

Each process owns its own `.atlas/` and its own Memex. Cross-repo
symbol lookup is not supported and is not a goal — keep concerns
separated. See [`SETUP.md §8`](SETUP.md#8-production-hardening-checklist)
for the multi-repo isolation hardening item.

---

## Common pitfalls

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `search_symbol` returns empty for a name you know exists | Index not built, or built against a different language set | `atlas-aci index --repo … --langs …` |
| `view_file` returns fewer lines than requested | You hit the 100-line or 8 KiB per-call cap; paginate with `next_cursor` | Use `start_line`/`end_line` windows or follow `next_cursor` |
| `view_file` rejects a path | `UnicodeDecodeError` on a binary, or the path resolved outside the repo | Check file type; confirm path is inside `--repo` root |
| Server errors on re-index inside Docker | Repo mounted read-only | Use the writable index invocation (see [Docker](#running-in-docker)) |
| `test_dry_run` runs untrusted code in your shell | You skipped the sandbox | Put the server in a DevContainer or Firecracker microVM before enabling `test_dry_run` for untrusted models — see [`SETUP.md §8`](SETUP.md#8-production-hardening-checklist) |
| Rate limit trips mid-mission | Model is thrashing; default is 200 calls/minute | Investigate the tool-call log at `--log-level debug`; tighten mission scope before raising the cap |

---

## GHCR distribution

`ghcr.io/rynaro/atlas-aci` is the canonical image registry. Images are
published on every tagged release and support `linux/amd64` and
`linux/arm64`.

### Using `eidolons mcp atlas-aci` (recommended)

The [Eidolons nexus](https://github.com/Rynaro/eidolons) automates the
image pull and `.mcp.json` wiring. From your project root:

```bash
# Pull the pinned image from GHCR and wire it into your project
eidolons mcp atlas-aci
```

This command:
1. Pulls `ghcr.io/rynaro/atlas-aci@sha256:<pinned-digest>` from GHCR
   (the digest is embedded in the nexus and updated with each atlas-aci
   release).
2. Writes a `.mcp.json` entry pointing at the digest-pinned image.

The pinned digest is the integrity primitive — you do not need to run
cosign on every pull. The digest cannot be faked.

### Air-gap escape hatch

If GHCR is unreachable (firewall, air-gap, registry outage), use the
`--build-locally` flag to build the image directly from source:

```bash
eidolons mcp atlas-aci pull --build-locally
```

This invokes `docker build` against the upstream `atlas-aci` git
repository and tags the result for local use. The `--build-locally`
path is a **P0 invariant** in the nexus — it will never be removed.

You may also specify a custom git ref:

```bash
eidolons mcp atlas-aci pull --build-locally --git-ref v0.2.0
```

### Manual pull

If you are not using the Eidolons nexus, pull directly:

```bash
# By tag
docker pull ghcr.io/rynaro/atlas-aci:latest

# By digest (immutable pin — preferred for production)
docker pull ghcr.io/rynaro/atlas-aci@sha256:<digest>
```

The digest for each release is published in the GitHub Release notes.

### Verifying supply-chain attestations (optional)

Every published image carries a cosign keyless signature and GitHub
Sigstore attestations for SBOM and build provenance. Verification is
optional (the digest pin alone is sufficient for integrity) but
available for operators with stricter supply-chain requirements.

**Cosign keyless verify:**

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/Rynaro/atlas-aci/.github/workflows/release.yml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/rynaro/atlas-aci@sha256:<digest>
```

**SBOM and provenance via `gh attestation`:**

```bash
gh attestation verify oci://ghcr.io/rynaro/atlas-aci@sha256:<digest> \
  --owner Rynaro
```

A successful `gh attestation verify` confirms that:
- The image was built by GitHub Actions in the `Rynaro/atlas-aci`
  repository.
- The SBOM lists every dependency vendored into the image.
- The provenance links the image to the exact commit that triggered the
  release.

---

## Further reading

- [`README.md`](README.md) — orientation, tool manifest, security
  invariants
- [`SETUP.md`](SETUP.md) — end-to-end install → canaries → hardening
- [`hosts/claude-code.md`](hosts/claude-code.md),
  [`hosts/copilot.md`](hosts/copilot.md),
  [`hosts/cursor.md`](hosts/cursor.md) — per-editor wiring
- [`mcp-server/src/atlas_aci/config.py`](mcp-server/src/atlas_aci/config.py) —
  skip patterns, bounds, path-traversal guard
- [`mcp-server/src/atlas_aci/enforcement.py`](mcp-server/src/atlas_aci/enforcement.py) —
  read-only allowlist and mechanical bounds
