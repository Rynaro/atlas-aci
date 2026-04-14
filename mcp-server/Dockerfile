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
#   1. One-time index build — needs a writable /repo so .atlas/ can be
#      created inside the repo:
#
#        docker run --rm \
#            -v "$PWD":/repo \
#            atlas-aci index --repo /repo --langs ruby,python,javascript,typescript
#
#   2. Long-lived serve — repo mounted read-only, memex on its own
#      named volume so excerpts persist across runs:
#
#        docker run --rm -i --read-only \
#            -v "$PWD":/repo:ro \
#            -v atlas-memex:/memex \
#            atlas-aci
#
# The read-only mount in step 2 is the OS-level guarantee that the
# server cannot mutate your repo — stronger than any code-level check.
# See README.md §Security invariants and SETUP.md §8.
# ──────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src

# Build the wheel directly. No intermediate `uv sync` — hatchling + uv
# build is self-contained, and the runtime stage installs via pip.
# README.md is copied because pyproject.toml's `readme = "README.md"`
# field makes hatchling require it at build time.
RUN uv build --wheel --out-dir /dist

# ──────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="atlas-aci" \
      org.opencontainers.image.description="ATLAS bounded ACI — read-only MCP server" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/Rynaro/atlas-aci"

# System deps:
#   ripgrep — mandatory; search_text wraps it
#   git     — used by `atlas-aci index --since <ref>` for incremental runs
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ripgrep \
        git \
    && rm -rf /var/lib/apt/lists/*

# Dedicated unprivileged user. UID pinned to 10001 so volume ownership
# is predictable across rebuilds.
RUN useradd --create-home --uid 10001 atlas

COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Pre-create the mount points and hand /memex to the unprivileged user.
# /repo is operator-provided (read-only in production), so its ownership
# doesn't matter — we only need read access.
RUN mkdir -p /repo /memex && chown atlas:atlas /memex

USER atlas
WORKDIR /home/atlas

VOLUME ["/repo", "/memex"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# MCP stdio handles SIGINT cleanly; make `docker stop` fast by mapping
# SIGTERM → SIGINT instead of the default 10s grace-then-KILL.
STOPSIGNAL SIGINT

ENTRYPOINT ["atlas-aci"]
CMD ["serve", "--repo", "/repo", "--memex-root", "/memex"]
