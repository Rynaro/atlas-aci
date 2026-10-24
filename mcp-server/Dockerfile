# syntax=docker/dockerfile:1
#
# atlas-aci — bounded read-only MCP server (production runtime image).
#
# This image is intentionally minimal: it contains the atlas-aci wheel,
# ripgrep, git, and nothing else. No pytest, no ruff, no mypy — use
# Dockerfile.dev for the dev loop.
#
# ──────────────────────────────────────────────────────────────────────
# Two invocations you'll want (see INTEGRATION.md for the full story):
#
#   1. One-time external index build — /repo may remain read-only;
#      /graphs is operator-provided storage writable by UID10001:
#
#        docker run --rm \
#            --read-only -v "$PWD":/repo:ro -v /path/to/graphs:/graphs \
#            atlas-aci index --repo /repo --graph-root /graphs
#
#   2. Long-lived serve — repo mounted read-only, memex on its own
#      named volume so excerpts persist across runs:
#
#        docker run --rm -i --read-only \
#            -v "$PWD":/repo:ro \
#            -v /path/to/graphs:/graphs:ro -v atlas-memex:/memex \
#            atlas-aci serve --repo /repo --graph-root /graphs --memex-root /memex
#
# The read-only mount in step 2 is the OS-level guarantee that the
# server cannot mutate your repo — stronger than any code-level check.
# See README.md §Security invariants and SETUP.md §8.
# ──────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Build the wheel directly. README.md is copied because pyproject.toml's
# `readme = "README.md"` field makes hatchling require it at build time.
RUN uv build --wheel --out-dir /dist

# Export the locked transitive deps (production extras only) so the
# runtime stage installs the exact versions captured in uv.lock and not
# whatever pip resolves fresh against PyPI. Without this, every fresh
# build is exposed to upstream wheel restructures — e.g. tree-sitter-
# language-pack 1.6.3 dropped its top-level module and silently broke
# the image (no build error, ModuleNotFoundError at runtime).
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt \
        --output-file /dist/requirements.txt

# ──────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="atlas-aci" \
      org.opencontainers.image.description="ATLAS bounded ACI — read-only MCP server" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/Rynaro/atlas-aci"

# System deps:
#   ripgrep — mandatory; search_text wraps it
#   git     — general repo tooling for operators/test_dry_run; NOT used by
#             `atlas-aci index --since <marker>`, which never shells out to
#             git — it hashes the exact consumed file buffers
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ripgrep \
        git \
    && rm -rf /var/lib/apt/lists/*

# Dedicated unprivileged user. UID pinned to 10001 so volume ownership
# is predictable across rebuilds.
RUN useradd --create-home --uid 10001 atlas

COPY --from=builder /dist/requirements.txt /dist/*.whl /tmp/
# Install transitive deps from the lockfile-derived requirements.txt
# first, then the project wheel itself with --no-deps so pip cannot
# re-resolve and undo the lock-respecting install.
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && pip install --no-cache-dir --no-deps /tmp/*.whl \
 && rm /tmp/*.whl /tmp/requirements.txt

# Linux parser wheels lazily fetch grammars. Prefetch the locked package's
# existing shipped languages into the production HOME cache so indexing works
# under --read-only and --network none without a writable parser cache mount.
RUN HOME=/tmp python -c "from atlas_aci.codegraph import DEFAULT_LANGS; from tree_sitter_language_pack import download, get_parser; download(list(DEFAULT_LANGS)); [get_parser(lang) for lang in DEFAULT_LANGS]" \
 && chmod -R a+rX /tmp/.cache/tree-sitter-language-pack

# Pre-create the mount points and hand /memex to the unprivileged user.
# /repo is operator-provided (read-only in production), so its ownership
# doesn't matter — we only need read access.
RUN mkdir -p /repo /memex && chown atlas:atlas /memex

USER atlas
WORKDIR /home/atlas

VOLUME ["/repo", "/memex"]

ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# MCP stdio handles SIGINT cleanly; make `docker stop` fast by mapping
# SIGTERM → SIGINT instead of the default 10s grace-then-KILL.
STOPSIGNAL SIGINT

ENTRYPOINT ["atlas-aci"]
CMD ["serve", "--repo", "/repo", "--memex-root", "/memex"]
