---
name: release
description: Cut a release of the atlas-aci MCP server end-to-end. Use when the user says "release", "cut vX.Y.Z", "ship it", "run the release", or after a version's PRs are green and ready to land. Covers the merge-strategy decision, the version gate, tagging, watching the signed multi-arch build, capturing the image digest, archiving the ESL change (and the CI paths that breaks), and bumping the nexus catalogue.
---

# release — Cut an atlas-aci release

This repo's thesis is that **a documented claim the code does not honour is a defect**. The release process is held to the same standard: every number in a changelog traces to an artefact, every gate actually runs, and every link still resolves a year from now.

## Invariants

1. **`release.yml` gates on `pyproject.toml` matching the tag.** Tag `v2.0.0` requires `version = "2.0.0"` in `mcp-server/pyproject.toml`. Bump `pyproject.toml`, `src/atlas_aci/__init__.py`, and re-lock `uv.lock` **before** tagging. `uv lock --check` must pass — a released tag once shipped a `uv.lock` pinning a different version than the package it locked (that is `AC-REL-3`).
2. **`serve` never mutates `.atlas/`.** Nothing in the release path may change that.
3. **The MCP tool surface is seven tools.** `export` and `import` are CLI verbs, deliberately: `import` writes, and the agent is the untrusted party. Do not add them to `server.py` to make a release feel complete.
4. **Never tag from a dirty tree.** Never tag a commit that CI has not gone green on.

## Phase 0 — Decide the merge strategy *before* merging

The default here is squash. **Squash is wrong when committed artefacts cite commit SHAs.**

`.spectra/changes/**/RETRO.md` and `DEFECT-LEDGER.txt` cite individual commits as evidence. Squashing orphans every one of those citations once the branches are gc'd — creating exactly the class of defect (a citation pointing at nothing) the release exists to eliminate.

```bash
# Do the artefacts cite SHAs?
grep -rlE '\b[0-9a-f]{7,12}\b' .spectra/changes/*/RETRO.md .spectra/changes/*/DEFECT-LEDGER.txt 2>/dev/null
```

If yes: `gh pr merge <n> --merge`. Afterwards, **prove the citations survived**:

```bash
for s in $(grep -oE '\b[0-9a-f]{7}\b' .spectra/changes/*/DEFECT-LEDGER.txt | sort -u); do
  git merge-base --is-ancestor "$s" origin/main && echo "$s reachable" || echo "$s ORPHANED"
done
```

Merge a hardening PR **before** the augmentation PR that stacks on it — that ordering is what `harden-gate.yml` exists to enforce.

## Phase 1 — Verify `main`, then tag

```bash
git checkout main && git pull --ff-only origin main
grep -m1 '^version' mcp-server/pyproject.toml          # must equal the tag, sans 'v'
grep -m1 '__version__' mcp-server/src/atlas_aci/__init__.py
(cd mcp-server && uv lock --check && uv run --frozen --extra dev pytest -q)
grep -ric networkx mcp-server/pyproject.toml mcp-server/uv.lock   # must be 0 0
```

Annotated tag, then push. The push is what triggers `release.yml`.

```bash
git tag -a v2.0.0 -m "…"
git push origin v2.0.0
```

`release.yml` builds multi-arch (QEMU + buildx), signs with cosign keyless, attests SBOM + build provenance, publishes to GHCR, and creates the GitHub Release. Expect ~4–5 minutes; arm64 emulation is the slow part.

```bash
gh run list --workflow=release.yml --limit 1
gh run view <id> --json status,conclusion
```

## Phase 2 — Capture the digest

**The image tag has no `v`.** `git tag v2.0.0` → `ghcr.io/rynaro/atlas-aci:2.0.0`. Inspecting `:v2.0.0` silently returns nothing, and an empty digest will happily be written into a catalogue.

```bash
docker buildx imagetools inspect ghcr.io/rynaro/atlas-aci:2.0.0 --format '{{.Manifest.Digest}}'
docker buildx imagetools inspect ghcr.io/rynaro/atlas-aci:2.0.0 --raw \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["mediaType"],len(d["manifests"]),"platforms")'
```

## Phase 3 — Archive the ESL change, and fix what that breaks

When the change is `verified` and `drift_checked`, archive it:

```bash
tb() { docker run --rm -i --user "$(id -u):$(id -g)" -v "$PWD:/workspace:z" -w /workspace \
  --cap-drop ALL --security-opt no-new-privileges ghcr.io/rynaro/tonberry@sha256:<pinned> "$@"; }
tb archive --change_id <id> --project_root /workspace
```

**Archive moves `.spectra/changes/<id>/` to `.spectra/changes/archive/<date>-<id>/`.** Anything resolving those artefacts *by hardcoded path* breaks. Observed, all three live:

- `harden-gate.yml`'s A3 probe-precondition step (`PROBE_ARTIFACT=`, `PROBE_JSON=`)
- `harden-gate.yml`'s `AC-REL-2` export-size step
- `scripts/test-verify-probe-verdict.sh`, which **silently `SKIP`ped all 27 scenarios** — and a SKIP exits `0`, so CI stays green while testing nothing

A path is a proxy. The invariant is *"exactly one valid probe record exists."* Resolve by search — `scripts/resolve-probe-artifact.sh` does this: it looks under `.spectra/changes/` including `archive/`, and **fails loudly on zero or more than one match**. Ambiguity is an error, not a first-match.

Before you archive, grep for the pattern and fix it:

```bash
grep -rn '\.spectra/changes/[a-z0-9-]*/' .github/workflows/ scripts/ README.md
```

Then re-run the gate self-tests against the *post-archive* tree and confirm they still run — count the scenarios, do not trust the exit code:

```bash
bash scripts/test-verify-probe-verdict.sh   # expect "27 passed", not "0 passed"
bash scripts/test-harden-gate-classify.sh   # expect "6 passed"
```

## Phase 4 — Doc-honesty gates still apply to the release prose

`AC-DOC-1`…`AC-DOC-10` are repo-wide grep-zero checks. A changelog is prose, and prose is where false claims are born. After writing release notes, re-verify that none of the forbidden claims reappeared: `--since` as git-ref diffing, canary pass-rates, Prism Ruby mode, `.atlas/symbols.db`, `unbounded (cheap)`, and any claim that `serve` "fails fast" (it starts; the *tool call* returns `INDEX_UNAVAILABLE`).

**Every number in the notes must trace to a committed artefact.** A stale number once shipped inside `RETRO.md` itself — a counterfactual quoting pre-canonicalization medians that no longer matched `probe-lpa-vs-louvain.json` one directory away. Recompute; do not recall.

## Phase 5 — Bump the nexus catalogue

The release is not finished until consumers can get it. In `Rynaro/eidolons`, use the **`bump-mcp`** skill: `roster/mcps.yaml` `versions.latest` + `pins.stable` + a `releases:` entry with the verified digest, the `cli/tests/mcp_images.bats` fixture, and a CHANGELOG entry.

When linking to this repo's ESL artefacts from anywhere else, **pin the tag**: `blob/v2.0.0/.spectra/changes/<id>/RETRO.md`. A `blob/main` link 404s the moment the change is archived.

## Checklist

- [ ] Hardening PR merged before augmentation PR
- [ ] Merge commits if artefacts cite SHAs; citations proven reachable from `main`
- [ ] `pyproject` == `__init__` == `uv.lock` == tag; `uv lock --check` passes
- [ ] Suite green on `main`; `grep -ric networkx` → `0 0`
- [ ] Tag annotated and pushed; `release.yml` green
- [ ] Digest captured from the **unprefixed** image tag; multi-arch confirmed
- [ ] Artefact paths resolved by search *before* archiving; self-tests still count scenarios
- [ ] ESL change archived; `drift_checked` intact
- [ ] Doc-honesty greps re-verified after writing prose
- [ ] Nexus catalogue bumped via `bump-mcp`; links pin the tag
