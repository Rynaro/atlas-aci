---
artifact: declared-scope
plan: aci-v2-harden-and-augment
target: atlas-aci v2.0.0
purpose: exhaustive file list v2.0.0 may touch; drift against this list is a gate failure (ramza-drift)
note: paths are repo-relative to <repo>
amended: refine cycle 1 (harden-gate.yml/uv.lock/Dockerfile) + amendment 2 (SCOPE-1 memex.py into scope; SCOPE-2 test_thesis_negatives.py declared)
---

# atlas-aci v2.0.0 — Declared Scope (amended)

Exhaustive, honest list of files v2.0.0 may touch, grouped by phase. A changed file outside this
list (minus the standing `.spectra/*` allow) is DRIFT and fails `ramza-drift`. The glob form used
by the mechanical check is in `plan-state.json` `declared_scope`.

## P0 — Hardening gate (H0-H4 + doc-honesty)

# H0 — CI + in-tree harden-gate (net-new)
.github/workflows/ci.yml
.github/workflows/harden-gate.yml
scripts/harden-gate-classify.sh
scripts/test-harden-gate-classify.sh

# H1 — central bounds chokepoint
mcp-server/src/atlas_aci/server.py
mcp-server/src/atlas_aci/enforcement.py

# H2 — route search_symbol + graph_query + over-cap/registry tests
mcp-server/src/atlas_aci/tools/search_symbol.py
mcp-server/src/atlas_aci/tools/graph_query.py
mcp-server/src/atlas_aci/tools/view_file.py
mcp-server/tests/test_server.py

# H3 — schema-epoch DB substrate + sweep/rebuild (index-path only) + serve fail-fast + DDL-hash + rationale-store DDL
mcp-server/src/atlas_aci/codegraph.py
mcp-server/src/atlas_aci/config.py
mcp-server/src/atlas_aci/memex.py
mcp-server/src/atlas_aci/__main__.py
mcp-server/tests/test_schema_epoch.py

# H4 — dead-language honesty (LANG_BY_EXT <-> QUERIES) + kind-enum honesty
mcp-server/src/atlas_aci/codegraph.py
mcp-server/tests/test_codegraph.py

# Doc-honesty batch (correct-the-doc / add-file; repo-wide grep-zero per F11/F12/F13)
README.md
INTEGRATION.md
SETUP.md
CLAUDE.md
CHANGELOG.md
mcp-server/README.md
mcp-server/Dockerfile
scripts/run-canaries.py
mcp-server/pyproject.toml
LICENSE

## P1 — Edges + god nodes (A1, A2)

# A1 — edge table + confidence enum + candidates + subclasses_of + refs.enclosing DROP + caller-context-from-source
mcp-server/src/atlas_aci/codegraph.py
mcp-server/src/atlas_aci/tools/graph_query.py
mcp-server/src/atlas_aci/tools/search_symbol.py
mcp-server/src/atlas_aci/server.py
mcp-server/tests/test_confidence.py
mcp-server/tests/test_graph_query.py

# A2 — degree-centrality god nodes over the edge table
mcp-server/src/atlas_aci/codegraph.py
mcp-server/src/atlas_aci/tools/graph_query.py
mcp-server/tests/test_graph_query.py

## P2 — Communities + rationale (A3, A4)

# A3 — hand-rolled deterministic LPA communities (total-ordered) + D3 probe artifact (may be CUT to v2.1 per DIR-1)
mcp-server/src/atlas_aci/codegraph.py
mcp-server/src/atlas_aci/tools/graph_query.py
mcp-server/tests/test_communities.py
.spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.md

# A4 — rationale nodes Ruby -> Python -> JS/TS (parallel; store DDL folded into H3, edge-graph-resolution-independent)
mcp-server/src/atlas_aci/codegraph.py
mcp-server/tests/test_rationale.py

## P3 — Export + release (A5 + release-prep)

# A5 — deterministic sorted-JSONL export + idempotent import (no graph/union merge driver)
mcp-server/src/atlas_aci/export.py
mcp-server/src/atlas_aci/__main__.py
mcp-server/src/atlas_aci/codegraph.py
mcp-server/tests/test_export.py
.gitignore
INTEGRATION.md

# Release-prep — version bump, lockfile, changelog, migration doc, canary-honesty
mcp-server/pyproject.toml
mcp-server/uv.lock
mcp-server/src/atlas_aci/__init__.py
CHANGELOG.md
README.md

## Notes on scope honesty

- `mcp-server/src/atlas_aci/codegraph.py` is the hot file: touched in every phase (schema, QUERIES,
  edge/confidence, god nodes, LPA, rationale, export). Expected — it is the single 462-line class
  every consumer calls (`server.py:160,174-188`).
- **H3 rename coupling (vigil C3):** the hardcoded `(config.repo / ".atlas" / "graph.db").exists()`
  checks at `search_symbol.py:23` and `graph_query.py:22` must move to the epoch path; both files
  are already in scope (H2/A1). Their existence-check failure on mismatch is how `serve` fails fast
  (DIR-2). Not a scope gap — an implementation coupling for vivi.
- `.gitignore` (P3) un-ignores the committable JSONL export subpath while keeping the DB
  (`.atlas/graph.*.db`) ignored (G-A: DB is derived/ephemeral).
- `mcp-server/uv.lock` (F14) is IN scope. **Live defect confirmed against the COMMITTED blob:**
  `git show HEAD:mcp-server/uv.lock:37` is `version = "0.3.1"` while `mcp-server/pyproject.toml:3` is
  `version = "0.4.0"` — the released `v0.4.0` tag shipped a lockfile pinning the package at `0.3.1`.
  **vigil's finding stands** (my earlier working-tree read was polluted by a transient `uv` re-lock;
  never trust the working tree for a shipped-state claim). v2.0.0 regenerates the lock; AC-REL-3
  asserts `uv.lock` == `pyproject.toml` mechanically so this drift can never ship again.
- `mcp-server/Dockerfile` (F12) is IN scope: its `--since <ref>` line (`Dockerfile:64`) is one of
  the repo-wide `--since` untruths AC-DOC-2 must clear.
- `.github/workflows/harden-gate.yml` (DIR-3/F3) is the in-tree gate mechanism (AC-A3-1, AC-A3-5,
  AC-NEG-6). Branch protection is documented defence-in-depth, not a tracked file.
- New files declared: `.github/workflows/ci.yml`, `.github/workflows/harden-gate.yml`, `LICENSE`,
  `mcp-server/src/atlas_aci/export.py`, tests `test_server.py`/`test_schema_epoch.py`/
  `test_confidence.py`/`test_graph_query.py`/`test_communities.py`/`test_rationale.py`/
  `test_export.py`/`test_thesis_negatives.py`, and the probe artifact `probe-lpa-vs-louvain.md`; plus
  `scripts/harden-gate-classify.sh` and `scripts/test-harden-gate-classify.sh` (SCOPE-4).
  **SCOPE-2 (vigil, amendment 2):** `mcp-server/tests/test_thesis_negatives.py` is a pytest mirror of
  the in-scope AC-NEG criteria (no new capability); it is mechanically covered by the
  `mcp-server/tests/*.py` glob but Vivi added it at P0 **without disclosing it** (the memex change it
  did disclose) - recorded here plainly.
- Explicitly NOT in scope (would be drift): `mcp-server/Dockerfile.dev`, `mcp-server/.dockerignore`,
  `hosts/*` (no new host wiring in v2.0.0). **memex-ref *emission* features remain deferred** (only the
  `server.py:139` manifest string is corrected).
- **SCOPE-1 (vigil, amendment 2):** `mcp-server/src/atlas_aci/memex.py` is moved **into** P0 scope (H3
  list above). `Config.__post_init__` **and** `Memex.__init__` both `mkdir` the same root, so the real
  Docker `--read-only`/`:ro` smoke test for AC-H-16 crashes at startup (`OSError: Errno 30`, EROFS)
  unless `Memex.__init__` is also made fail-open; fixing only the in-scope `config.py` leaves `Memex`
  crashing (F-8). The exclusion's *intent* (memex-ref emission deferred) is untouched; the change is a
  4-line best-effort-`mkdir` hardening, not an emission feature. Vivi disclosed it.
- **SCOPE-3 (ramza-drift, amendment 2):** `mcp-server/src/atlas_aci/tools/view_file.py` is added to P0
  scope (H2 list above). Vigil's second pass certified exactly two undeclared files at 11 commits; the
  12th commit (`2761cda`) landed the **NEW-1-residual** fix - view_file's `overflow`/`next_cursor` now
  honour the truncate-signal-iff-content-withheld invariant AC-H-18 pins (the fourth instance of the
  defect class): no overflow on a fully-satisfied short-file window, no `next_cursor` past EOF. In-scope
  by the same intent as AC-H-18/H2; surfaced by `ramza-drift`, not vigil, because the plan advanced one
  commit past vigil's snapshot.
- **SCOPE-4 (ramza-drift, coordinator, amendment 2):** `scripts/harden-gate-classify.sh` and
  `scripts/test-harden-gate-classify.sh` are added to P0 scope (H0 list above). `AC-NEG-6`'s in-tree
  mechanism (DIR-3) was itself defective - `harden-gate.yml`'s marker greps matched *comments*, not
  code (`A3_CODE_MARKERS` tripped on a comment in `codegraph.py` saying the grep is evadable;
  `AUGMENTATION_CODE_MARKERS` tripped on `rationale_for` inside rationale-DDL SQL comments), which
  would have failed this very PR. Vivi's commit `1e3cfd7` extracts the classification into a script the
  workflow *calls* (never a copy) and strips whole-line and trailing comments before matching; the
  self-test is `6 passed / 0 failed` with teeth (reverting the strip yields `4 passed / 2 failed`,
  exactly the two comment scenarios). The false-*positive* class is now closed; the known
  false-*negative* class (evasion by renaming, F-3) remains open BY DESIGN, defended structurally by
  `KNOWN_QUERY_VERBS` reachability + the unconditional `ci.yml` suite, not by the markers. Criteria
  unchanged; the frozen SHA `5c3adddb...` stays.
