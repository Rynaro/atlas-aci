---
artifact: verify-resolution
verify_item: V1
plan: aci-v2-harden-and-augment
target: atlas-aci v2.0.0
resolver: ATLAS (session 2026-07-09)
status: recommendation — read-only, not implemented
relates_to: AC-A3-1, AC-A3-4, AC-A3-5, AC-REL-2, D3 (deliberation.md), F5 (checker-critique.md)
source_anchor: mcp-server/src/atlas_aci/codegraph.py @ f56a78e (tag v0.4.0, the plan's declared base_head) — NOT the working tree
---

# V1 — Reference Rails-scale repo for the D3 probe (recommendation)

**Scope note.** This is a read-only recommendation. No code, workflow, or acceptance-criteria
edit was made. a private production Rails repository on the maintainer's host was **not indexed, read, or cited** —
it was considered (it is the only large Rails codebase on this host) and excluded solely because
it is a private production repo and this plan's artefacts are committed to a public repo
(`github.com/Rynaro/atlas-aci`). No fact, file count, or structural claim below is derived from it.

All commit SHAs, file/byte counts, licence texts, and directory structures for the *candidate
reference repos* (§1) were fetched **via the GitHub REST API and raw-content endpoints only**
(`api.github.com`, `raw.githubusercontent.com`) — metadata reads and small single-file fetches,
never a `git clone`. Nothing large was cloned. Numbers are correct as of **2026-07-09**; SHAs are
cited so anyone can re-verify independently (`gh api repos/<owner>/<repo>/commits/<sha>` or
`git ls-remote`).

**Concurrent-write hazard (flagged mid-session, addressed here):** the `atlas-aci` checkout at
`<repo>` is on branch `feat/v2-p0-harden` and is being
actively edited by another agent (Vivi, implementing P0) for the duration of this session. Every
claim about **atlas-aci's own source** in §4 below (G-B: refs are bare name-strings, no real edge
objects) is anchored to the **committed blob at `f56a78e`** (tag `v0.4.0`, the plan's declared
`base_head` in `spec.md`), fetched via `git show f56a78e:mcp-server/src/atlas_aci/codegraph.py`,
**not** whatever the working tree currently contains. I additionally ran `diff` between that base
blob and the live working-tree file at the time of this check: **zero differences** — Vivi's edits
had not yet touched `codegraph.py` at the moment I verified, so my earlier working-tree read and
the base-blob read agree byte-for-byte. That agreement is a point-in-time fact, not a standing
guarantee — it could diverge the moment this session ends. Treat every `codegraph.py:<line>`
citation below as sourced from `f56a78e`, independent of the working tree's current state.

---

## 1. Ranked shortlist

### #1 (recommended) — `solidusio/solidus` @ `4026945d614e81383c007ed1ab1278a0195ce5d9` (tag `v4.7.0`)

| Property | Value |
|---|---|
| Commit SHA (lightweight tag → commit directly, not an annotated-tag object) | `4026945d614e81383c007ed1ab1278a0195ce5d9` |
| Ruby files | **1,943** `.rb` files, ~4.1 MB of Ruby source (verified via `git/trees?recursive=1` at the pinned SHA) |
| Language mix | 83.2% Ruby by bytes (highest Ruby density of any candidate checked — least dilution from JS/HTML noise in the probe graph) |
| Full-history clone size (GitHub `size` field) | 109,867 KB ≈ **107 MB** |
| **Single-commit working-tree size** (sum of blob sizes at this SHA — the realistic shallow-clone floor) | **~15.3 MB** across 3,341 blobs — no bulky `docs/`/frontend-asset directory diluting it |
| Licence | `LICENSE.md` header reads "Spree License" — textually **identical to BSD-3-Clause** (verified by fetching the raw file: standard 3-clause redistribution/attribution/no-endorsement text, no field-of-use or SaaS restriction). GitHub's detector tags it `NOASSERTION` only because the header text isn't a stock SPDX-recognized preamble — the clause text itself **is** BSD-3-Clause. Fine as a test fixture; flag the non-standard header to the maintainer as a one-line caveat, not a blocker. |
| Structure | Rails-engine monorepo: `core/`, `api/`, `backend/`, `admin/`, `promotions/`, `legacy_promotions/` — six real Rails engines under one repo, each a `Rails::Engine` with its own `app/`, `lib/`, `config/`. |
| Mixin density (directly verified, not inferred) | **65** files under `*/concerns/` (ActiveSupport::Concern modules). |
| Why discriminating | The six-engine split is a **natural ground-truth partition** independent of any algorithm's output — `core` symbols cluster differently from `admin`/`backend`/`api` symbols in any structurally-sound community algorithm. That gives the probe something to sanity-check *beyond* the raw modularity number: does LPA's partition roughly track the engine boundaries the way Louvain's does, or does it degenerate into one giant blob (LPA's known failure mode on dense graphs)? A repo with no real module structure can't offer this cross-check at all. |

### #2 (paired secondary repo) — `spree/spree` @ `6699cde44303ea85ef6e56c5e87c44a738ab73fc` (tag `v5.5.2`)

| Property | Value |
|---|---|
| Commit SHA (dereferenced from annotated tag `v5.5.2`, tag object `90a211aff96c0be3be0ee692422e01e9a7c35c45`) | `6699cde44303ea85ef6e56c5e87c44a738ab73fc` |
| Ruby files | **2,181** `.rb` files, ~6.8 MB of Ruby source |
| Language mix | 65.2% Ruby, 26.0% TypeScript (storefront frontend — excluded by `--langs ruby` at index time) |
| Full-history clone size | 283,407 KB ≈ **277 MB** |
| Single-commit working-tree size | ~102.6 MB raw, but **88.1 MB of that is `docs/`** (a mkdocs site with images) and **3.3 MB is `packages/`** (JS storefront) — the actual Ruby engines live entirely under `spree/` and total **only 10.8 MB** (3,283 files). A sparse-checkout on `spree/` alone (`git sparse-checkout set spree/`) gets clone cost down to Solidus's ballpark. |
| Licence | `LICENSE` — GitHub-confirmed SPDX `BSD-3-Clause` (clean auto-detection, unlike Solidus's non-standard header). |
| Structure | Same engine pattern, one generation earlier: `spree/{admin,api,core,emails,lib}`. |
| Mixin density (directly verified) | **117** files under `*/concerns/`; and a directly-fetched sample (`spree/core/app/models/spree/product.rb`) shows the canonical pattern cold: 8 `include Spree::<Module>` statements plus a nested `include Spree::VendorConcern` inside a conditional block, and a sampled concern (`breadcrumb_concern.rb`) opens with `extend ActiveSupport::Concern` — exactly the static, syntactically-visible `include`/`extend` forms atlas-aci's Tree-sitter `QUERIES` table can capture (`(call method: (identifier) @callee) @ref.call` matches `include`/`extend` as ordinary method calls with a constant-receiver argument). This is real evidence, not an assumption. |
| Why pair it with Solidus | Solidus is a historical fork of Spree — same architectural family, but the two have diverged (Solidus dropped/renamed engines, Spree kept `emails` as its own engine, versions differ by ~10 years of independent development). Running the probe on **both** gives two genuinely different graphs without leaving the "well-structured Rails engine" population — this is what deliverable 3 asks for (2+ repos, not 1) without inflating scope to a structurally alien codebase. |

### #3 (stretch / largest-scale option) — `discourse/discourse` @ `ded677e00beb0e9bae6e70b69582aecfb72b5477` (tag `v2026.7.0-latest`)

| Property | Value |
|---|---|
| Commit SHA | `ded677e00beb0e9bae6e70b69582aecfb72b5477` (most recent tag as of 2026-07-09; Discourse tags continuously, no GitHub "Releases" objects) |
| Ruby files | **10,585** `.rb` files, ~36.3 MB of Ruby source — an order of magnitude bigger than #1/#2, genuinely the largest readily-available public Rails-scale Ruby codebase |
| Full-history clone size | 933,849 KB ≈ **912 MB** |
| Single-commit working-tree size | **~163 MB**, spread realistically across `plugins/` (42.5 MB, 11,086 files — Discourse now bundles its official plugins in-monorepo), `config/` (30.7 MB), `spec/` (29.3 MB), `docs/` (27.6 MB), `frontend/` (13.7 MB), `app/` (4.6 MB) — no single directory to sparse-checkout away; this is genuinely a large, sprawling app. |
| Licence | **GPL-2.0** (copyleft). Fine for read-only CI-fixture use — atlas-aci only clones, indexes, and measures it, never redistributes or links Discourse's code — but it's a heavier legal posture than Solidus/Spree's BSD-3-Clause, worth flagging explicitly rather than picking silently. |
| Mixin density | 114 `concerns/` files, plus a large `lib/plugin/` + `plugins/` extension surface. **Caveat, directly checked, not assumed:** I fetched `lib/plugin/instance.rb` (1,733 lines) at the pinned SHA and grepped it — Discourse's plugin API is **not** primarily static `prepend`. It's dominated by `reloadable_patch { ... }` / `add_to_class(...)` / `class_eval %Q{...}` — runtime metaprogramming helpers. A Tree-sitter query sees these as ordinary `@ref.call` nodes (`callee_name: "reloadable_patch"`), not as `include`/`extend`/`prepend` AST nodes, so this particular repo's *headline* extension mechanism is actually **less** discriminating for A1's static mixin resolution than Spree/Solidus's plain `include Foo::Bar` style, despite Discourse's outsized reputation for monkeypatching. Its value here is scale and its 114 real `concerns/` files, not the plugin API specifically. |
| Verdict | Keep as an optional third/stress repo if CI budget allows a bigger run, not the primary pick — biggest clone cost, most legally conservative licence, and its most-hyped mixin pattern doesn't actually stress the static resolver the way its reputation suggests. |

**Also considered, excluded, with reasons (not full write-ups):**
- `redmine/redmine` @ `e192cf1fd10e58bac716cf94b91b0a5ac8df72a1` (tag `7.0.0`) — GPL-2.0, 1,104 `.rb` files, ~13.6 MB working tree. Real production Rails app, but **single monolithic engine** (`app/`, no engine split) with an unpopulated `plugins/` directory (the plugin *mechanism* exists, no plugins actually ship in-repo) — no natural ground-truth partition to sanity-check community output against, less discriminating than the engine-split candidates.
- `huginn/huginn` @ `e605da6bd3e8cc49d94cc0e0f70ba488fb4f3fb2` (tag `v2022.08.18`) — MIT, cleanest licence of anything checked, but only 422 `.rb` files / 1.4 MB source. Too small to credibly claim "Rails-scale" per constraint 4; last tagged release is from 2022. Good for A1 fixture tests (its `Agent` subclass hierarchy is a nice small `subclasses_of` case), not for a community-detection probe.
- `chatwoot/chatwoot` — split licence (MIT outside `enterprise/`, separate licence *inside* `enterprise/`) and only 47.8% Ruby by bytes (majority Vue/JS) — the licence carve-out and Ruby dilution both make it a worse fixture than a clean single-licence, Ruby-dominant repo.
- `forem/forem`, `mastodon/mastodon` — AGPL-3.0 (strongest copyleft, checked and rejected on the same read-only-fixture reasoning as Discourse but with less structural payoff and, for forem, ~1.7 GB full-history size); not pursued past the metadata check.
- a private production Rails repository on the maintainer's host — **excluded per hard constraint**: private production repo, plan artefacts are public. Not indexed, not read, not cited.

---

## 2. Recommendation

**Pin two repos, not one:**

- **Primary — `solidusio/solidus@4026945d614e81383c007ed1ab1278a0195ce5d9`** (tag `v4.7.0`, BSD-3-Clause-equivalent, ~15.3 MB clone, highest Ruby density, natural 6-engine ground truth).
- **Secondary — `spree/spree@6699cde44303ea85ef6e56c5e87c44a738ab73fc`** (tag `v5.5.2`, BSD-3-Clause confirmed clean, sparse-checkout `spree/` only → ~10.8 MB clone, 117 verified `concerns/` files).

Both are genuinely Rails-scale, genuinely `include`/`extend`-heavy (verified by direct file fetch, not assumed), cleanly and permissively licensed, and structurally distinct enough from each other (different engine boundaries, ~10 years of independent divergence) to satisfy "don't validate the threshold against a single easy graph."

**Trade-off, stated plainly:** Discourse would be the more headline-grabbing "Rails-scale" proof point (10,585 files vs ~2,000), but its extra scale buys mostly `spec/`/`config/`/`plugins/`-bundle bulk, its flagship extension mechanism (`reloadable_patch`/`add_to_class`) doesn't actually exercise the static `include`/`extend`/`prepend` grammar A1 resolves, its licence is copyleft, and its clone is ~10x heavier. Spree+Solidus give more *discriminating signal per clone byte*. If the maintainer wants belt-and-suspenders scale evidence, run Discourse as a third, optional, non-gating data point in the same probe artefact — don't make it load-bearing for the AC-A3-4 pass/fail branch.

**On running the probe once vs. per-PR (directly answering the "AC-A3-1 checks for artefact presence" question):**
`AC-A3-1`'s `verify_method` is unambiguous and already correctly designed: *"harden-gate.yml fails when an A3 path changes without `.spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.md` present"* — this is a **file-presence check**, not a re-execution check. The probe (clone reference repo(s), index, resolve edges, run hand-rolled LPA, run `networkx.community.louvain_communities` as the one-time comparison baseline, diff modularity) should run **once, offline/locally**, and commit only its verdict (`probe-lpa-vs-louvain.md`) — never re-clone or re-run in CI. This is also what keeps `AC-NEG-2` intact: `AC-NEG-2`'s check is scoped to `mcp-server/pyproject.toml` / `mcp-server/uv.lock` ("resolved dependency tree"), so a throwaway probe script that `pip install`s `networkx` in an ad hoc scratch venv (never touching those two files) never trips it — networkx is a **probe-time-only tool**, not a project dependency, and the mechanism already supports that split cleanly.

**`AC-REL-2` is *not* written the same way, and should be — flagging this as a gap you should fix before P3, not something I'm inventing new scope for:**
`AC-REL-2`'s `verify_method` currently reads `"ci/artefact: export size measured on the pinned reference repo..."` — the `ci/` prefix is ambiguous about whether this re-clones+re-indexes+re-exports the reference repo on every CI run (real, recurring clone cost) or is a one-time measurement like the probe. Given the same clone-cost logic applies (even Solidus's lean ~15 MB clone is wasteful to repeat on every PR for a number that's deterministic given fixed source), recommend amending `AC-REL-2`'s `verify_method` to match `AC-A3-1`'s pattern exactly: measure once at P3/release-prep (owner `vivi`, per `plan-state.json` V4), record the byte count into the same `probe-lpa-vs-louvain.md` artefact (or a sibling `export-size-rails-scale.md`), and have CI check the *recorded number* rather than re-deriving it. Use the **larger** of the two pinned repos (Spree, or Discourse if adopted) for this specific measurement, since AC-REL-2 is a worst-case git-practical-size stress test, not a structural-quality comparison — Solidus alone would understate real export size.

---

## 3. Threshold sanity check — push back as requested

**`LPA_modularity >= Louvain_modularity − 0.05` (AC-A3-4) is not defensible as written; recommend replacing it.**

The core problem: modularity (Newman-Girvan Q) is **scale- and resolution-dependent**, and its *achievable ceiling* varies enormously by graph. A fixed absolute delta treats every graph as if it had the same achievable Q, which is false:

- On a graph with strong, clean community structure — e.g. Solidus's 6-engine split, where Louvain might plausibly find Q in the 0.5–0.7 range — a 0.05 absolute gap is a small (~7–10%) relative miss. Tolerable, roughly what the plan intends.
- On a graph with weaker/more tangled structure — e.g. a monolithic app, or even Spree/Solidus's `core` engine alone (the densest, most interconnected part, where a single ActiveRecord model hub can dominate) — Louvain might only reach Q in the 0.15–0.25 range. There, the *same* 0.05 absolute gap is a **20–33% relative miss** — LPA finding meaningfully worse structure — yet the fixed-epsilon rule scores it identically to the strong-structure case. The threshold gets *laxer in exactly the cases where a real quality gap matters most*, and stricter (in relative terms) only where structure is already strong and forgiving.
- Compounding this: modularity has a known **resolution limit** (small clusters below a size threshold merge into larger ones on large graphs) — so raw Q values from a ~2,000-file Solidus graph and a ~10,000-file Discourse graph aren't even apples-to-apples in the first place, which argues against calibrating a single global constant off of any one graph's characteristics (exactly what a single-repo probe would do).

**Recommendation:** replace the absolute-delta rule with a **relative floor**, e.g. `LPA_modularity >= R * Louvain_modularity` for some `R` in the 0.85–0.90 range. I'm not asserting the exact constant — that's the same epistemic move FORGE flagged as inferred-not-given for the current 0.05, and I don't have probe data to justify a precise number either. What I *am* asserting is the **shape** of the rule should change from absolute-delta to relative-ratio, because a relative rule automatically tightens in absolute terms as achievable structure gets stronger and loosens as it gets weaker — matching how "materially worse" should actually behave, rather than fighting it.

One caveat on the relative form: it degrades near `Louvain_modularity ≈ 0`. If Louvain itself can't find real structure on a candidate reference graph (informally, `Q < 0.3` is a commonly-used floor in the community-detection literature for "meaningful structure exists at all"), that graph isn't a valid test case for the comparison either way — the fix isn't a cleverer formula, it's a **precondition**: assert `Louvain_modularity` itself clears a sanity floor on each reference repo before comparing LPA against it. Both Solidus and Spree are exactly the kind of engine-partitioned repos likely to clear that floor comfortably (the ground-truth engine split *is* real structure), which is a second reason to prefer them as the reference pair over a flatter, less-partitioned codebase.

**Is one repo enough evidence? No — recommend 2+, mandatory, not just "nice to have."** A single-repo probe risks exactly the overfitting trap the plan should worry about: tune/validate the pass/fail bound against one graph (say, Solidus's very cleanly separated engines — an "easy" graph for both algorithms) and the numeric bound passes trivially, while giving no evidence about how LPA behaves on a messier, more typical target repo (which is what atlas-aci's actual users will index). Recommend requiring the relative bound to hold **independently on each** reference repo (not on an average across them) — a plan that passes on Solidus but fails on Spree shouldn't be allowed to "average out" to a pass, since the whole point is catching graphs where LPA silently degenerates (e.g., its known giant-community collapse failure mode on denser graphs) that a single favorable graph would hide.

**Net:** amend `AC-A3-4`/`AC-A3-1` before the probe executes — measure Louvain's own Q as a precondition, replace the fixed `-0.05` with a relative ratio (value TBD from the actual measurement, not asserted here), and run against both Solidus and Spree with a per-repo (not averaged) pass requirement.

---

## 4. Feasibility ordering — minimum A1 subset before the probe can run

**Anchored to the committed blob `mcp-server/src/atlas_aci/codegraph.py` at `f56a78e` (tag `v0.4.0`,
spec.md's declared `base_head`)** — fetched via `git -C <repo>
show f56a78e:mcp-server/src/atlas_aci/codegraph.py`, **not** the live working tree, which is
concurrently being edited by another agent on `feat/v2-p0-harden` for the duration of this session
(see the concurrent-write-hazard note at the top of this document). A `diff` against the working
tree at verification time showed zero differences for this file, but that is a point-in-time
observation, not a guarantee — the citations below stand on the base blob regardless. This matches
G-B in `deliberation.md` verbatim:

- `refs` is `(id, callee_name TEXT NOT NULL, path, line, enclosing TEXT, lang)` (`codegraph.py:155-163`) — `callee_name` is a **bare string**, no foreign key to `symbols.id`.
- `enclosing` is unconditionally set to `None` at extraction time (`codegraph.py:395`, inside `_extract`) — despite the column comment "enclosing def name, if known," nothing ever populates it. There is currently **no record of which symbol a given reference occurs inside**.
- `search_symbol` (`codegraph.py:408-423`) resolves definitions by `WHERE name = ?` and references by `WHERE callee_name = ?` — two independent string-equality lookups joined only by matching text at *query* time, not by any stored edge.
- `callers_of` (`codegraph.py:425-431`) is the same pattern: `SELECT path, line, enclosing FROM refs WHERE callee_name = ?` — again a name-string filter, and `enclosing` is always `NULL` per the point above, so even this "caller" information is empty today.
- `subclasses_of` (`codegraph.py:454-460`) is an explicit no-op stub: `{"edges": [], "warning": "subclasses_of requires extended index; not implemented in MVP."}`. No language's `QUERIES` entry captures a superclass/heritage field today (Ruby's query at `codegraph.py:75-81` only captures `class name: (constant) @name`, no `superclass:`), so there is **no inheritance edge of any kind** to materialize from yet.

**Conclusion: there is no graph to run LPA or Louvain over today (at `f56a78e`).** Both algorithms need actual nodes-with-identity and edges-with-two-resolved-endpoints; today's `refs` table is a flat list of unresolved name strings with no source endpoint and no target endpoint. Community detection literally cannot execute, let alone be compared, until some resolution work lands. (Whatever Vivi is landing on `feat/v2-p0-harden` right now is P0 hardening work per the phase spine — H0-H4 — not A1; per `plan-state.json`'s `phase_spine`, A1 is P1, gated behind the full P0 harden-gate. So this conclusion should still hold at the *start* of A1 work regardless of how far P0 has progressed by the time this is read.)

**Minimum A1 subset that must exist before the probe can run** (this is a subset of the full A1 scope in `spec.md` D4/`acceptance-criteria.md` AC-A1-1..11, not all of it):

1. **Source-endpoint capture** — retire the always-`None` `enclosing` column and instead record which symbol a reference occurs *inside* (this is exactly F10/AC-A1-9/AC-A1-10's "caller context from the materialized edge source endpoint"). Without this there is no source node for any edge.
2. **Target resolution for the single-candidate case** — the D4 candidate-count partition logic (extending the extraction path per spec.md D4/F18), at minimum for **Ruby only** (the reference repos are pure-Ruby fixtures, so Python/JS-TS qualification rules per AC-A1-11 are not on the probe's critical path — they can land later without blocking it). This needs to produce, for `candidate_count == 1` refs, real `(source_symbol_id, target_symbol_id)` pairs tagged EXTRACTED or INFERRED per the Ruby rule pinned in spec.md D4/F18 (constant-receiver/`::`-scope → EXTRACTED; local-variable/`self` receiver or unique bare name → INFERRED).
3. **A materialized edges table** actually storing those `(source_id, target_id, relation, confidence)` rows (AC-A1-1) — scoped to **call edges only** is sufficient for a first-pass probe; inheritance edges (`subclasses_of`, AC-A1-7) enrich the graph but are not a hard blocker for computing *a* modularity comparison, since a call-graph-only community structure is already a legitimate (if partial) test of LPA vs Louvain. Cheap to include if it lands in the same pass, since D4's resolution machinery is shared, but not worth sequencing the probe behind it specifically.
4. **A pinned decision on `AMBIGUOUS` edges in the probe's graph construction — currently undecided in `spec.md`, needs to be made explicit before running.** `candidate_count > 1` refs (AC-A1-5) carry an ordered `candidates[]`, not a single resolved target — feeding them into a community-detection graph requires an explicit choice: exclude them from the probe graph entirely (cleanest, apples-to-apples over the *confident* edge subset — recommended default), or fan them out as multiple edges to every candidate (changes graph density and could bias the comparison in either direction). This is a probe-methodology gap, not a coding gap — flagging it now so it doesn't get decided implicitly by whatever the first implementation happens to do.

**Not required for the probe** (useful for other AC-A1 criteria, but off the probe's critical path): AC-A1-8's cross-run total-ordering of `candidates[]` (a D6/export-determinism concern, not a modularity-computation concern), and full JS/TS/Python qualification coverage (AC-A1-11) — the probe only needs the Ruby rule since the reference repos are Ruby.

This lines up with `plan-state.json`'s own sequencing (`P1` A1 exit gate is `AC-A1-1..11` green, `P2` A3 is blocked on "A1 (trustworthy edges) + A2 (ordered first) + the D3 evidence probe") — the point above is narrower: the probe itself needs only the source-endpoint-capture + single-candidate Ruby resolution + edges-table slice of A1, not the full 11-criterion set, so it could in principle run as soon as that slice is testable rather than waiting for every AC-A1 criterion including JS/TS/Python fixtures to go green. Whether to actually sequence it that way (partial-A1-then-probe vs. full-A1-then-probe) is a scheduling call for whoever owns P1/P2, not something this resolution changes.

---

## 5. UNVERIFIED / explicitly flagged

- **Exact achievable modularity (Q) values on Solidus/Spree's real edge graphs** — UNVERIFIED. No edge table exists yet at `f56a78e` (§4), so no modularity number can be computed today. The relative-threshold recommendation in §3 names the *shape* of a fix, not a validated constant — that constant is only knowable once A1's minimum slice lands and the probe actually runs.
- **Whether GitHub tags could be force-moved after this session** — treated as practically immutable per open-source convention, but not cryptographically guaranteed. Recommend the acceptance criteria pin the **commit SHA**, not the tag name, when this is written into `spec.md`/`acceptance-criteria.md` (the SHAs above are the actual pin; the tag names are only for human legibility).
- **Solidus's `LICENSE.md` classification** — the clause text is BSD-3-Clause-identical (directly fetched and read, quoted above), but it is not SPDX-tagged as such by GitHub because of its non-standard "Spree License" header. Recommend the maintainer do a final human read of the full `LICENSE.md` before treating this as settled, since I'm reporting a text match, not a legal opinion.
- **Solidus/Spree's un-sampled files** — I fetched and read specific files (`product.rb`, `breadcrumb_concern.rb`, `instance.rb`) to verify mixin-pattern claims; I did not read every file in either repo (that would mean indexing/cloning at scale, out of bounds for a read-only, no-large-clone resolution). The aggregate counts (`.rb` file counts, `concerns/` counts, byte totals) come from the GitHub tree API over the full pinned commit, so those are exhaustive and verified; the *qualitative* mixin-style claims are sampled, not exhaustive.
- **Whether `git sparse-checkout` on Spree's `spree/` subtree actually reduces the transferred pack size proportionally** — the ~10.8 MB figure is the *working-tree* size of that subtree (sum of blob sizes at the pinned commit), which is a reasonable proxy but not a measured `git clone` transfer size (that would require actually cloning, which was avoided per instruction).
- **The state of `feat/v2-p0-harden` at the moment this document is read** — everything in §4 is anchored to `f56a78e`, verified independent of the working tree. If a future reader wants to confirm A1 hasn't accidentally started before P0 is green (a scenario `AC-NEG-6`/`harden-gate.yml` is designed to catch), re-run `git -C <repo> show f56a78e:mcp-server/src/atlas_aci/codegraph.py` and diff against the then-current working tree, rather than trusting this document's now-stale point-in-time diff.
