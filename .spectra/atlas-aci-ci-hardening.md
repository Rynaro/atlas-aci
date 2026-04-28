# SPECTRA Spec: atlas-aci CI Hardening

- **id:** `atlas-aci-ci-hardening`
- **status:** `draft`
- **owner:** `TBD`
- **cycle:** S → P → E → C → T → R → A
- **related:** sibling spec `atlas-aci-codegraph-build-regression-test` (out-of-scope here)

---

## S — Frame

`Rynaro/atlas-aci` ships a Python-based MCP server as a Docker image. End users
get this image via `eidolons atlas aci --container --runtime docker`. The image
is the product. The repo currently has **zero** `.github/workflows/`, so:

- No automated check that the production image builds at all.
- No automated check that the built image is *behaviourally* correct (the CLI
  can actually load `atlas_aci.codegraph` and index a repo).
- No detection when an upstream transitive dep silently restructures its wheel
  layout between releases.

This is the prevention surface for the class of bug that just shipped to users
in `1.1.0`: `tree-sitter-language-pack 1.6.3` dropped its top-level module, the
production `Dockerfile` was re-resolving deps from PyPI (`pip install /tmp/*.whl`
without `--no-deps`), and every fresh build silently produced a broken artifact.
Build succeeded, `ModuleNotFoundError` at first use.

The fix in `atlas-aci#1` (already merged) tightened the constraint to `<1.6.3`
and switched the runtime stage to `uv export --frozen` → `requirements.txt` →
`pip install --no-deps`. That closes the *current* hole, but `pip --no-deps`
only enforces metadata pinning — it does not catch a future upstream that ships
a broken-but-installable wheel. We need a behavioural gate that exercises the
production image end-to-end on every PR and on a daily cron, so a freshly
published broken upstream cannot ship undetected for more than ~24 hours.

### Existing surface this spec must respect

- `mcp-server/Dockerfile` — production runtime image (multi-stage:
  `builder` runs `uv build --wheel` and `uv export --frozen --no-dev`; `runtime`
  installs `requirements.txt` then the wheel with `--no-deps`). Build context
  is `mcp-server/`.
- `mcp-server/Dockerfile.dev` — dev image, `uv sync --extra dev`, ships
  `pytest`, `ruff`, `mypy`. Build context is `mcp-server/`.
- `mcp-server/pyproject.toml` — `[project.optional-dependencies] dev`
  already lists `pytest>=8.0`, `pytest-asyncio>=0.23`, `ruff>=0.4`, `mypy>=1.10`.
  Ruff config: `line-length = 100`, `target-version = "py311"`,
  `select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF"]`. Pytest:
  `testpaths = ["tests"]`, `asyncio_mode = "auto"`.
- `mcp-server/tests/test_enforcement.py` — only existing test file; covers the
  enforcement layer, not codegraph.
- `mcp-server/src/atlas_aci/__main__.py` — entry point exposes
  `atlas-aci serve | index | tools`. The relevant subcommand for the
  prevention check is `index --repo <path> --langs python`, which calls
  `CodeGraph(repo, langs).build(since=None)` — this is where the
  `tree_sitter_language_pack` import fires.
- `requires-python = ">=3.11"`. Both Dockerfiles pin `python:3.12-slim`.

---

## P — Problem

**Goal.** Add CI to `Rynaro/atlas-aci` such that:

1. The production image build is exercised on every PR and on a daily cron with
   `--no-cache`, so transient PyPI-side breakage gets caught within ~24h even
   when no PR is open.
2. The built production image is sanity-checked **behaviourally** — not just
   `--help`, but an actual `index` run against a repo fixture that produces
   `>0` symbols. This is the gate that would have caught the `1.1.0`
   `ModuleNotFoundError`.
3. Source-level quality (ruff, mypy, pytest) runs on every PR, fast, against
   the dev surface that `pyproject.toml` already declares.
4. The dev image (`Dockerfile.dev`) is smoke-built on every PR — cheaper than
   the prod build and catches dev-loop drift.

**Non-goals (deferred).** GHCR publishing, image releases, semantic-release,
versioning automation, Dependabot/Renovate, the standalone unit-level
`CodeGraph.build()` regression test (sibling spec).

**Constraints.**
- GitHub Actions only (free tier; matrix budget matters).
- All Docker work must use the same `mcp-server/` build context the
  Dockerfiles already assume.
- Cron schedule must run even when there are no PRs in flight.
- The fixture must exercise the actual import path
  (`atlas_aci.codegraph` → `tree_sitter_language_pack`), otherwise the
  prevention gate is theatre.

---

## E — Explore (decisions made up-front)

These decision points are resolved here so the implementer does not re-derive
them. Open questions remain in §Open Questions for items that genuinely need
human input.

### D1. Workflow split

**Decision:** Two workflows, not one.

- `ci.yml` — runs on `pull_request` and `push: [main]`. Fast lane: lint, type,
  unit tests, dev-image smoke build. Should complete in <3 min.
- `image.yml` — runs on `pull_request` (only when `mcp-server/**`,
  `.github/workflows/image.yml`, or this spec changes), on `push: [main]`,
  and on `schedule: cron '17 6 * * *'` (06:17 UTC daily — off the top of the
  hour to avoid GitHub's cron stampede). Heavy lane: full
  `docker build --no-cache` of the production image, plus the behavioural
  fixture run.

Rationale: every PR pays the fast lane; only PRs that touch the image surface
pay the slow lane; the cron guarantees daily coverage regardless of PR
activity.

### D2. Fixture location and shape

**Decision:** Checked-in fixture at `mcp-server/tests/fixtures/sample_py/`.
Generating on the fly inside the workflow couples the prevention gate to the
workflow YAML and makes it untestable locally.

Minimum content:
- `mcp-server/tests/fixtures/sample_py/main.py` — a single Python file with
  at least one top-level function and one class with one method. Five symbols
  is plenty; the assertion is `>0`, not a specific count.
- `mcp-server/tests/fixtures/sample_py/README.md` — one-paragraph note that
  this directory is a CI fixture, do not delete, do not import from production
  code.

The fixture must be self-contained (no external imports beyond the stdlib) so
tree-sitter parses it without surprises.

### D3. Matrix dimensions

**Decision:**
- Fast lane (`ci.yml`): matrix on `python-version: ['3.11', '3.12']`,
  `os: ubuntu-latest` only. Two cells. Justification: `requires-python =
  ">=3.11"`, the prod image runs 3.12, but consumers may install the wheel
  directly under 3.11. macOS/Windows are not supported runtime targets — the
  image is the product.
- Heavy lane (`image.yml`): single cell, `ubuntu-latest`, no Python matrix
  (the image pins 3.12-slim itself). Adding a matrix here would 2× a job that
  already takes minutes and provides no additional coverage — the Dockerfile
  is the unit under test.

### D4. How the behavioural gate fails fast

**Decision:** Three-step assertion inside `image.yml` after the build:

1. `docker run --rm <image> --help` — proves the entry point loads. Cheap
   trip-wire.
2. `docker run --rm -v "$PWD/mcp-server/tests/fixtures/sample_py":/repo
   <image> index --repo /repo --langs python` — exercises `CodeGraph.build()`,
   which is where the `tree_sitter_language_pack` import fires. This is the
   step that would have failed loudly in `1.1.0`.
3. Assert `mcp-server/tests/fixtures/sample_py/.atlas/graph.db` exists and
   `sqlite3 .atlas/graph.db 'SELECT COUNT(*) FROM symbols;'` returns a value
   greater than zero. Use a small inline shell snippet; do not add a new
   Python helper just for this.

Each step's failure mode is distinct and the logs make it obvious which
property broke.

Note: `index` writes `.atlas/` inside the mounted repo, so the volume mount
must be read-write (not `:ro`). The fixture's `.atlas/` directory must be in
`.gitignore` (see §Validation Gates).

### D5. uv installation strategy in fast lane

**Decision:** Use `astral-sh/setup-uv@v3` (or current pinned major). Run
`uv sync --extra dev` once per matrix cell, then invoke tools via `uv run`.
Do not duplicate the dev install logic in YAML — it already exists in
`Dockerfile.dev` and we want them to drift together, not separately.

### D6. Caching

**Decision:**
- Fast lane: `setup-uv` action's built-in cache (`enable-cache: true`),
  keyed on `mcp-server/uv.lock`.
- Heavy lane: **no** Docker layer cache for the prod-image build. The cron's
  whole point is to detect transient upstream breakage; a cache mask would
  hide exactly the failure mode we are trying to surface. `--no-cache` is
  load-bearing here, not a perf nit.
- Dev-image smoke (in fast lane): GHA cache via `actions/cache` keyed on
  `mcp-server/Dockerfile.dev` + `mcp-server/pyproject.toml` is acceptable,
  because the dev image is a convenience check, not a prevention gate.

### D7. Concurrency

**Decision:** Both workflows set
`concurrency: { group: '${{ github.workflow }}-${{ github.ref }}',
cancel-in-progress: true }` so successive pushes to the same PR cancel the
previous run. The cron run uses
`group: 'image-cron'` (no ref) so daily runs serialize cleanly without
cancelling each other.

---

## C — Acceptance Criteria

All criteria are GIVEN/WHEN/THEN. An implementer can treat each as an
independent checkbox.

### AC1. Fast lane runs on every PR

- **GIVEN** a PR is opened or updated against `main`,
- **WHEN** GitHub Actions evaluates workflows,
- **THEN** `ci.yml` runs on the matrix `{python: 3.11, 3.12} × {os: ubuntu-latest}`
  and each cell executes, in order: `uv sync --extra dev`,
  `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`,
  `uv run mypy src/`, `uv run pytest -q`.
- **AND** the workflow completes in under 5 minutes wall-clock for a clean PR.

### AC2. Dev-image smoke

- **GIVEN** a PR touches anything under `mcp-server/`,
- **WHEN** `ci.yml` runs,
- **THEN** a job builds `mcp-server/Dockerfile.dev` and runs
  `docker run --rm <dev-image> uv run atlas-aci --help`, asserting exit 0.

### AC3. Production-image behavioural gate on PR

- **GIVEN** a PR touches `mcp-server/**` or `.github/workflows/image.yml`,
- **WHEN** `image.yml` runs,
- **THEN** the workflow:
  1. Runs `docker build --no-cache -f mcp-server/Dockerfile -t atlas-aci:ci mcp-server/`.
  2. Runs `docker run --rm atlas-aci:ci --help` and expects exit 0.
  3. Runs `docker run --rm -v "$GITHUB_WORKSPACE/mcp-server/tests/fixtures/sample_py":/repo
     atlas-aci:ci index --repo /repo --langs python` and expects exit 0.
  4. Asserts `mcp-server/tests/fixtures/sample_py/.atlas/graph.db` exists and
     contains at least one row in the `symbols` table.

### AC4. Daily cron

- **GIVEN** no PRs are open,
- **WHEN** the schedule trigger `17 6 * * *` fires,
- **THEN** `image.yml` executes the full AC3 sequence against `main`.
- **AND** failures notify via the default GitHub Actions failure path
  (commit/run status; no Slack/email integration in this spec).

### AC5. Specific-bug regression coverage

- **GIVEN** a synthetic upstream regression where
  `tree-sitter-language-pack` is bumped to a version whose wheel layout drops
  the top-level `tree_sitter_language_pack` module (or any equivalent failure
  mode),
- **WHEN** `image.yml` runs,
- **THEN** AC3 step 3 fails with a non-zero exit code and the failing log
  surfaces the `ModuleNotFoundError` (or the corresponding runtime error) —
  i.e. the gate detects behavioural drift, not just a missing build.

This is the load-bearing AC for the prevention surface. An implementer
**must** dry-run this scenario locally (e.g. by temporarily bumping the
constraint upper bound) before declaring the spec done.

### AC6. Idempotency of the fixture

- **GIVEN** AC3 step 3 has run once,
- **WHEN** the fixture's `.atlas/` directory is already present,
- **THEN** subsequent `index` invocations either succeed or are explicitly
  cleaned up at the start of the assertion step.
- **NOTE:** the simplest implementation is `rm -rf
  mcp-server/tests/fixtures/sample_py/.atlas` before the run. The fixture's
  `.atlas/` directory is `.gitignore`d.

### AC7. Marker-bounded blame

- **GIVEN** future workflow files coexist with these,
- **WHEN** an implementer reads `ci.yml` or `image.yml`,
- **THEN** each file's top-of-file comment names the spec id
  (`atlas-aci-ci-hardening`) and links to this document, so the next
  maintainer knows where the design decisions live.

---

## T — Validation Gates

Hard gates that must be green before this spec ships:

| Gate | Check | Owner |
|------|-------|-------|
| G1   | `ci.yml` exists, both matrix cells green on a no-op PR | impl |
| G2   | `image.yml` exists, full sequence green on a no-op PR touching `mcp-server/` | impl |
| G3   | `image.yml` cron runs on `main` at least once and is green | impl |
| G4   | AC5 dry-run: with constraint widened to allow `>=1.6.3`, the gate fails red on the `index` step | impl |
| G5   | `mcp-server/tests/fixtures/sample_py/` is checked in and contains at least one Python file with parseable symbols | impl |
| G6   | `.gitignore` includes `mcp-server/tests/fixtures/sample_py/.atlas/` | impl |
| G7   | Fast-lane wall-clock < 5 min on a clean PR; heavy-lane wall-clock < 10 min | impl |
| G8   | `shellcheck` clean on any inline shell that exceeds 5 lines (extract to a script under `mcp-server/scripts/ci/` if so) | impl |
| G9   | No secrets used; both workflows run on the public `ubuntu-latest` runner with no `secrets.*` references | impl |
| G10  | Both workflow files carry the spec-id comment header (AC7) | impl |

---

## R — Out of Scope

Explicitly **not** part of this spec — track separately if needed:

- **GHCR publishing.** No `docker push`, no registry login, no image-tag
  automation. The image is built and discarded inside CI.
- **Versioning / release automation.** No semantic-release, no tag triggers,
  no CHANGELOG enforcement.
- **Dependabot / Renovate.** Useful complementary surface, but a separate
  concern with its own review surface.
- **`CodeGraph.build()` unit test.** Sibling spec
  `atlas-aci-codegraph-build-regression-test` covers this at the Python
  level. The current spec's behavioural gate is at the *image* level — both
  layers are needed; neither replaces the other.
- **Multi-arch builds (arm64).** Single-arch (amd64) only. Cross-platform
  testing belongs in a follow-up once a real arm64 user reports drift.
- **macOS / Windows runners.** The product is a Linux container image.
- **Notification integrations.** Slack / email / PagerDuty hooks are out;
  the default GitHub failure status is sufficient for now.
- **Performance/benchmark CI.** No timing assertions beyond the soft G7
  budget; we are catching correctness, not regressions in indexing speed.

---

## A — Open Questions

Items that need human input before final implementation. Not blocking the
spec being marked `draft → ready`; they are prompts for the implementer to
raise during the C-stage review.

1. **Cron timezone.** `17 6 * * *` UTC = 03:17 BRT. Is there a preferred
   window (e.g. so failure emails arrive at the start of the working day)?
2. **Cron failure noise.** If the cron flakes due to upstream PyPI outage
   (vs. a real regression), do we want a retry-once-then-fail wrapper, or
   accept the false positive and re-run manually?
3. **Fixture languages.** The spec restricts the fixture to Python because
   that is the language whose import path triggered the original bug.
   Should we also add a tiny Ruby / JS / TS fixture to broaden the gate?
   Adds runtime; adds coverage. Trade-off lives with the maintainer.
4. **Concurrency policy on cron.** Do daily cron runs queue or skip if a
   previous run is still in-flight (e.g. a stuck job)? Default in this spec
   is queue (`cancel-in-progress: false` on the cron group).
5. **Workflow permissions.** Default `permissions: contents: read` is
   enough for everything in this spec. Confirm with the maintainer that no
   downstream automation will need write scopes added later.
6. **Pinning action versions.** This spec uses `astral-sh/setup-uv@v3` style
   major-pinning. Project policy may prefer SHA-pinning for supply-chain
   hardening — confirm before merging.
