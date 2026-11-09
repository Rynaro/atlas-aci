# Installation

This page covers every install path, the container image, and upgrades. The [README](../README.md) has the short version. For an end-to-end walkthrough, read [SETUP.md](../SETUP.md). To onboard an existing codebase, read [INTEGRATION.md](../INTEGRATION.md).

## Requirements

- **Python 3.11 or newer.** [uv](https://docs.astral.sh/uv/) manages the environment and the lockfile.
- **ripgrep (`rg`) on your PATH.** `search_text` returns `INDEX_UNAVAILABLE` without it.
- **Docker**, optional, for the container image.

The index does not need `git`. `index --since` never shells out to git.

## From a checkout

```bash
git clone https://github.com/Rynaro/atlas-aci
cd atlas-aci/mcp-server
uv sync               # creates .venv from uv.lock
uv tool install .     # puts atlas-aci on your PATH, for host configurations
atlas-aci --help      # lists every command
```

`atlas-aci --help` lists the top-level commands. `atlas-aci jobs --help` lists the index-job subcommands.

## Index a repository

```bash
atlas-aci index --repo /path/to/your/repo
```

With no `--langs` flag, `index` covers every shipped language: `rust`, `tsx`, `ruby`, `python`, `typescript`, `javascript`, `scss`, `html`, `yaml`, `markdown` and `bash`. Pass `--langs` with a comma-separated list to index a subset. The list is the `DEFAULT_LANGS` constant in [`codegraph.py`](../mcp-server/src/atlas_aci/codegraph.py).

The index is written to `<repo>/.atlas/` unless you pass `--graph-root`. Add `.atlas/` to the repository's `.gitignore`.

Two options matter day to day:

- `--since <marker>` re-indexes incrementally. It skips files whose modification time and size are unchanged. The marker is not a git ref, and any truthy value works.
- `--frameworks` enables opt-in framework adapters. The accepted names are listed in `atlas-aci index --help`.

## Serve

```bash
atlas-aci serve --repo /path/to/your/repo                       # stdio, the default transport
atlas-aci serve --repo /path/to/your/repo --memex-root /path/to/memex
atlas-aci serve --repo /path/to/your/repo --transport http --port 8765   # loopback HTTP
```

Without `--memex-root`, the excerpt store lives at `<repo>/.atlas/memex`. The HTTP transport binds loopback addresses only and has no authentication. See [How it works](how-it-works.md#http-transport) before you use it.

## Host configuration

Each host needs one server entry per repository, and each entry needs a distinct name:

- Claude Code: [hosts/claude-code.md](../hosts/claude-code.md)
- GitHub Copilot custom agents: [hosts/copilot.md](../hosts/copilot.md)
- Cursor: [hosts/cursor.md](../hosts/cursor.md)

The entries all run the same command, `atlas-aci serve --repo <path>`. Restart the host after editing its configuration.

## Docker

### The published image

`ghcr.io/rynaro/atlas-aci` is built for `linux/amd64` and `linux/arm64` on every `v*` tag. The newest published tag is `v3.0.2` (image `ghcr.io/rynaro/atlas-aci:3.0.2`):

- The 3.0.2 image prefetches the tree-sitter grammars, so the first indexing run does not need network access.
- Changes made after 3.0.2 are not in it. Build locally from `main` to get them.

```bash
docker run --rm -v /path/to/your/repo:/repo ghcr.io/rynaro/atlas-aci index --repo /repo

docker run --rm -i --read-only -v /path/to/your/repo:/repo:ro -v atlas-memex:/memex \
  ghcr.io/rynaro/atlas-aci serve --repo /repo --memex-root /memex
```

The `--read-only` flag and the `:ro` bind mount together ensure the server cannot write to the repository. The named volume keeps excerpts across container restarts. Drop it if you want an ephemeral memex. The `-i` flag is required because MCP runs over stdio.

Pull by digest for production. Each release's notes list the digest. See [Verified releases](../README.md#verified-releases) for signature and attestation checks.

For the wider container workflow, including a route that pulls the pinned digest into a project's MCP configuration, see [INTEGRATION.md](../INTEGRATION.md#ghcr-distribution).

### Build the image locally

Building locally from `main` gives changes made after 3.0.2, and the build prefetches the parser grammars:

```bash
docker build -t atlas-aci mcp-server
docker run --rm -v /path/to/your/repo:/repo atlas-aci index --repo /repo
```

The same read-only `serve` pattern applies. The [Dockerfile](../mcp-server/Dockerfile) header lists the two invocations.

## Development

Run the suite and the linters on the host:

```bash
cd mcp-server
uv sync
uv run pytest -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```

Or use the development image, which ships pytest, ruff and mypy. The [Dockerfile.dev](../mcp-server/Dockerfile.dev) header describes it:

```bash
cd mcp-server
docker build -f Dockerfile.dev -t atlas-aci-dev .
docker run --rm -v "$PWD":/work atlas-aci-dev uv run pytest -q
```

## Upgrading between schema epochs

The graph database is derived data. It is always rebuilt from source and never migrated in place. A schema change bumps `SCHEMA_EPOCH` in [`codegraph.py`](../mcp-server/src/atlas_aci/codegraph.py) and produces a new file, `.atlas/graph.<SCHEMA_EPOCH>.db`.

After upgrading to a release that bumps the epoch, run `atlas-aci index` once. `serve` always starts, whatever the epoch. Each graph tool call checks that a current-epoch index exists. If it does not, the call returns a structured `INDEX_UNAVAILABLE` error that names the `index` command. `serve` writes nothing under `.atlas` either way.

Moving to an older binary finds no matching epoch file and builds its own on the next `index` run. Each direction costs one rebuild, and there is no back-and-forth.

## Hardening

Before running unattended on shared infrastructure, work through the checklist in [SETUP.md §8](../SETUP.md#8-production-hardening-checklist). The two items most often skipped are:

- isolating `test_dry_run` in a DevContainer or microVM before you enable it;
- mounting the repository read-only at the OS level.

Multi-repository setups run one process per repository. See [INTEGRATION.md](../INTEGRATION.md#multi-repo-setups).
