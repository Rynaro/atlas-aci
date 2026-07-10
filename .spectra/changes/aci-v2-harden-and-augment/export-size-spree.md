---
artifact: export-size-spree
phase: P3
change_id: aci-v2-harden-and-augment
maker: vivi
branch: feat/v2-p1-edges
ceiling_frozen_before_measurement: true
verdict: PASS
---

# AC-REL-2 — export size on the pinned Spree reference repo

## What this is

`AC-REL-2` asks for a real-world, worst-case-size data point: the larger
of the two pinned reference repos (Spree, per `v1-reference-repo.md`'s
own recommendation — "use the **larger** of the two pinned repos... since
AC-REL-2 is a worst-case git-practical-size stress test"), exported once
and checked against a **104,857,600 byte (100 MiB)** ceiling. Measured
**once**, here, at P3/release-prep — never re-cloned or re-measured per
PR (same pattern `probe-lpa-vs-louvain.md` established for the D3a probe:
a real clone + build is expensive and deterministic given fixed source,
so CI asserts the *recorded* number, not a re-derivation).

## Reproduction

```bash
mkdir spree-rel2 && cd spree-rel2
git init
git remote add origin https://github.com/spree/spree.git
git config core.sparseCheckout true
mkdir -p .git/info
echo "spree/*" > .git/info/sparse-checkout
git fetch --depth 1 origin 6699cde44303ea85ef6e56c5e87c44a738ab73fc
git checkout FETCH_HEAD

cd ../mcp-server
uv run atlas-aci index --repo ../spree-rel2/spree --langs ruby
uv run atlas-aci export --repo ../spree-rel2/spree ../spree-rel2/spree-export.jsonl
stat -c '%s' ../spree-rel2/spree-export.jsonl
```

Sparse-checkout on `spree/*` only (the Rails engine split — `docs/` is an
88.1 MB mkdocs site with images, `packages/` is a 3.3 MB JS storefront,
neither is Ruby source `atlas-aci` would index anyway; see
`v1-reference-repo.md`), matching how `probe-lpa-vs-louvain.md` indexed
the same pinned SHA for the D3a probe.

## Measured result

| Quantity | Value |
|---|---|
| Repo | `spree/spree` @ `6699cde44303ea85ef6e56c5e87c44a738ab73fc` (tag `v5.5.2`) |
| Files indexed | 2,181 (Ruby only) |
| Symbols | 9,347 |
| Edges | 51,129 |
| Rationale rows | 33 |
| Export records | 60,509 |
| **Export size** | **88,742,743 bytes (≈ 84.6 MiB)** |
| Ceiling (AC-REL-2) | 104,857,600 bytes (100 MiB) |
| Headroom | 16,114,857 bytes (≈ 15.4 MiB, **15.37% of the ceiling**) |
| Verdict | **PASS** |

The headroom is real but not large — a ~15% margin, not a 10x one. If a
future language/relation is added to the extracted grammar (more edge
types, more rationale coverage, a wider Rails-scale corpus), this number
should be re-measured rather than assumed stable; `scripts/verify-export-size.py`
recomputes the verdict from `export_bytes`/`ceiling_bytes` on every CI run
so a stale PASS recorded here cannot silently persist once someone edits
the JSON without re-measuring (mirroring the D3a probe's own
recompute-don't-trust discipline) — but it does NOT re-clone or
re-measure Spree itself; that remains a human, release-prep action.

## What this does not verify

Same trust boundary the D3a probe named for its own graph bundle: nothing
here mechanically re-verifies that this `export_bytes` figure was
genuinely produced by cloning the exact pinned SHA and running the exact
shipped exporter — that is attested by this document's reproduction
steps and by whoever re-runs them, not by a CI-computable check. What CI
*can* and does check on every PR: the recorded `export_bytes` is
literally `<=` the frozen `ceiling_bytes`, and the recorded `verdict`
label agrees with that comparison — the same "recompute the verdict from
the recorded numbers, never trust the label" discipline
`verify-probe-verdict.py` applies to the D3a probe.
