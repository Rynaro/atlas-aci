# `graph_query` DSL — deep reference

Back to [`README.md`](../README.md#the-seven-tools). This is the full
reference for `graph_query`'s edge shape, the `god_nodes:` and
`communities:` analyses, and the `rationale:` comment-extraction verb —
relocated here to keep the top-level README lean. The seven-tools table,
the DSL's six-line summary, and the "`EXTRACTED` is a floor, not an exact
count" honest bound stay in the README itself.

## `graph_query` DSL — edge shape (v2.0.0 / A1)

`callers_of:Sym` and `subclasses_of:Class` both query the materialized
call/inheritance edge table `atlas-aci index` builds (`definitions_of:Name`
is unchanged — it delegates straight to `search_symbol`). Each element of
the returned `edges` list has this shape:

```jsonc
{
  "relation": "call",          // call | construct | superclass | include | extend | prepend
  "confidence": "EXTRACTED",   // EXTRACTED | INFERRED | AMBIGUOUS — never LLM-produced
  "source": {                  // caller context — replaces the old, always-null
    "path": "app/tallier.rb",  // `enclosing` field (v1). None/None when the
    "line": 3,                 // reference sits outside every known symbol's
    "name": "call",            // range (e.g. a Ruby top-level call).
    "kind": "method"
  },
  "target": {                  // populated for EXTRACTED/INFERRED only —
    "path": "app/target.rb",   // the single resolved definition.
    "line": 2,
    "name": "record_vote"
  },
  "candidates": null           // populated (never truncated silently — see
                                // below) for AMBIGUOUS edges only; mutually
                                // exclusive with `target`.
}
```
`callers_of`/`subclasses_of` responses also carry a top-level
`unresolved_refs` count: the number of raw references matching the queried
name that exist in the index but resolved to **no** edge (typically an
external/gem method with no local definition). An empty `edges: []` alone
cannot tell a consumer "nothing calls this" apart from "calls exist but
didn't resolve" — `unresolved_refs` makes that distinction explicit instead
of leaving both cases looking identical.

A **zero-candidate** reference (the callee resolves to no known definition
anywhere in the index) never becomes an edge at all — it stays an
unresolved name, exactly as `refs` recorded it pre-v2 (and is counted in
`unresolved_refs` above). `subclasses_of` aggregates every inheritance/mixin
relation (`superclass`, `include`, `extend`, `prepend`) under the one verb,
since a Rails engine leaning on `concerns/` mixins expresses "subclass-of"
through all four relations, not just `superclass`.

`relation: "construct"` is a bare `Foo(...)` / `new Foo()` (JS/TS) /
`Foo.new` (Ruby) call that resolves entirely to a class/module symbol — a
constructor invocation, not a method call, so it is never silently folded
into `relation: "call"`. `callers_of:SomeClass` returns these: the caller
doesn't know in advance whether a queried name is a callable or a class, so
`callers_of` searches both relations for exactly that reason — a symbol's
*kind* is never a reason a query silently comes back empty.

**EXTRACTED vs INFERRED, and a known, guarded limitation.** Ruby's grammar
distinguishes a constant receiver (`Foo.bar`) from a plain identifier
(`obj.bar`, `self.bar`) structurally — a real syntactic fact, no lookup
needed. Python/JS/TS grammars don't make that distinction, so those two
languages resolve `qualifier_name` (the receiver, or the bare callee itself)
against the symbol table: does it name a known `class`/`module`? A local
variable that happens to share a class's name — `Config = load_config()`
then `Config.reload()` — would otherwise be indistinguishable from the
class itself under a name-only check with no scope analysis. The resolver
guards against exactly this: if `qualifier_name` is ALSO assigned to as a
plain local variable anywhere in the same file, the edge is demoted to
`INFERRED` rather than asserting `EXTRACTED` certainty it doesn't have — a
false `EXTRACTED` is worse than an honest `INFERRED`. This guard is
deliberately narrow (a plain `identifier` assignment target only — tuple
unpacking, attribute assignment, and augmented assignment aren't tracked),
so it can under-claim in rare cases the reverse way, but it never
over-claims. The guard is **file-scoped, not scope-scoped**: it has no
notion of function/block scope, so a single local assignment shadowing a
class name *anywhere* in a file demotes *every* reference to that class
name in that file, even ones in unrelated functions that never see the
shadowing variable — under-claiming further than strictly necessary, but,
per the same principle, never over-claiming. Confidence tiers can
therefore be conservative; treat `EXTRACTED` as a floor, not an exact
count.

**The analysis-graph divergence (D4a).** `graph_query` always returns every
*matching* edge, AMBIGUOUS included, with its full ordered `candidates[]`
attached — this project's "never silently incomplete" thesis extends to
ambiguity itself: an edge with more than one candidate is reported, not
dropped. `CodeGraph.confident_edges()` is the query primitive over the
**confident subgraph** (`EXTRACTED` ∪ `INFERRED`) that excludes AMBIGUOUS
entirely (no fan-out to candidates, no fractional weight — ambiguity is
not importance); `god_nodes:` and `communities:` (both below) are its
consumers. Community detection (A3) was **gated on a pre-registered
evidence probe, evaluated before the implementation shipped**: a
hand-rolled label-propagation implementation ships in v2.0.0 only because
it cleared a bar fixed before the probe ran — `Louvain_Q_median >= 0.30`,
`LPA_Q >= 0.30`, and `LPA_Q >= 0.85 x Louvain_Q_median` — evaluated
independently on two pinned reference repos (solidus, spree), never
averaged. Both repos passed all three clauses
(solidus: `LPA_Q=0.6691` vs. `0.85 x Louvain_Q_median=0.6326`; spree:
`LPA_Q=0.7165` vs. `0.85 x Louvain_Q_median=0.6670`); had either repo
failed any clause, A3 would have been cut to v2.1 and v2.0.0 would ship
`god_nodes` alone — the bar was fixed before the probe ran specifically so
the outcome couldn't be argued with either way. Full numbers (all ten
Louvain seeds per repo, best/worst/mean/sd, the exact clause arithmetic):
`probe-lpa-vs-louvain.md`, committed under `.spectra/changes/` (the
`aci-v2-harden-and-augment` change folder; archived to
`.spectra/changes/archive/` once verified — search there if the exact
path has moved).
"What `graph_query` returns" and "what `god_nodes`/`communities` analyze"
deliberately differ, and both responses carry `analysis_basis`,
`ambiguous_edges_excluded`, and `resolved_edge_count` fields making that
divergence visible rather than implicit — a consumer who ranks via
`god_nodes` (or groups via `communities`) and then queries `callers_of`
and sees AMBIGUOUS edges too is not seeing a contradiction; the analysis
never included them.

## `graph_query` DSL — `god_nodes:` (v2.0.0 / A2)

`god_nodes:` (the trailing colon is required by the DSL's `verb:argument`
shape; the argument itself is ignored — the ranking is computed over the
whole confident subgraph, not a single named symbol) returns a
degree-centrality ranking:

```jsonc
{
  "god_nodes": [
    {
      "path": "mcp-server/src/atlas_aci/codegraph.py",
      "line": 422,
      "name": "CodeGraph",
      "kind": "class",
      "in_degree": 75,    // confident edges reaching this symbol
      "out_degree": 3,    // confident edges originating from it
      "degree": 78        // in_degree + out_degree — the primary rank key
    },
    // ...
  ],
  "analysis_basis": "confident_edges",
  "ambiguous_edges_excluded": 107,  // AMBIGUOUS edges in the FULL edges table
  "resolved_edge_count": 459        // edges the ranking was actually computed over
}
```

No clustering, no cluster/community detection, no graph-algorithm runtime
dependency — pure arithmetic over `confident_edges()`'s own output, which
is what makes AMBIGUOUS structurally unable to leak into the ranking (not
merely a filter that happened to be applied correctly). A node's identity
is a *specific* symbol definition (`path`, `line`, `name`), never a bare
name string, so two identically-named methods in different classes rank
as two different nodes.

**In-degree vs out-degree — a judgment call, not a criterion mandate.**
The frozen acceptance criteria don't specify which direction (or
combination) "degree centrality" means; FORGE's design record states that
both are computed over the confident subgraph and frames a god node as "a
symbol many references *definitely/probably* reach" (an in-degree
reading), while computing out-degree too for consistency with future
analysis consumers. This implementation exposes **both** on every node and
ranks by their **sum** — "degree centrality," read literally and
unqualified, in the graph-theory sense. If you specifically want "the
things everyone calls" (fan-in) or "the things that call everything"
(fan-out), re-sort the returned list by `in_degree` or `out_degree`
yourself; the response gives you both, not just the combined rank.

`candidates[]` (and every edge enumeration) is emitted in a fixed total
order (`path`, `line`, `name`) for identical input — required for the
project's byte-deterministic export goal, and incidentally what makes the
shape safe to diff/test. Like every other bounded field, an over-cap
`candidates[]` is truncated on a whole-element boundary and flagged
(`truncated: true`, `truncated_fields: ["edges.candidates"]`,
`more_available: true`) rather than silently cut — nested sub-fields get
the same "never silently incomplete" treatment the top-level `edges` list
already had.

## `graph_query` DSL — `communities:` (v2.0.0 / A3)

`communities:` (same `verb:argument` shape, trailing colon required,
argument ignored — the analysis spans the whole confident subgraph)
returns a deterministic label-propagation community assignment:

```jsonc
{
  "communities": [
    {
      "path": "mcp-server/src/atlas_aci/codegraph.py",
      "line": 422,
      "name": "CodeGraph",
      "kind": "class",
      "community_id": 3
    },
    // ...
  ],
  "community_count": 12,
  "analysis_basis": "confident_edges",
  "ambiguous_edges_excluded": 107,
  "resolved_edge_count": 459
}
```

Zero new runtime dependency — a hand-rolled, deterministic asynchronous
label-propagation algorithm (Raghavan-style) over an undirected/unweighted
projection of the confident subgraph, single run, no seed, no randomness
anywhere: nodes are visited every pass in a fixed total order (`path`,
`line`, `name`), labels start at each node's sorted index, and ties break
toward the smallest label value — never insertion-order- or
hash-order-dependent (`PYTHONHASHSEED` has no effect on the output). Final
`community_id`s are renumbered `0..N-1` by ascending smallest-member node,
so the numbering itself is reproducible, not just the grouping. AMBIGUOUS
edges are excluded from community membership the same way `god_nodes`
excludes them from degree — here it is additionally *algorithmically
forced*, not merely filtered: an AMBIGUOUS edge has no single target, so
there is no single node to draw an undirected connection to in the first
place.

This shipped **only because the D3a probe passed** — see the paragraph
above and `probe-lpa-vs-louvain.md` (see the note above on where that's
committed) for the full measurement. The probe methodology (two pinned
reference repos, networkx Louvain as the comparison baseline) is a one-time
gate-clearing exercise, not a shipped runtime path — networkx never
appears in `mcp-server/pyproject.toml` or `mcp-server/uv.lock`.

## `graph_query` DSL — `rationale:` (v2.0.0 / A4)

`rationale:` (same `verb:argument` shape, trailing colon required,
argument ignored) returns every recognized rationale comment in the
repo — `# NOTE:`/`# IMPORTANT:`/`# HACK:`/`# WHY:`/`# RATIONALE:`/
`# TODO:`/`# FIXME:`-prefixed comments (ported from graphify's prefix
set), plus, JS/TS only, any comment referencing an ADR or RFC identifier
(no prefix required for that case):

```jsonc
{
  "rationale": [
    {
      "path": "mcp-server/src/atlas_aci/codegraph.py",
      "line": 1234,
      "text": "# HACK: this method special-cases nil for legacy reasons",
      "label": null,
      "target": {"path": "mcp-server/src/atlas_aci/codegraph.py", "line": 1230, "name": "CodeGraph"},
      "lang": "ruby"
    },
    {
      "path": "app/foo.ts",
      "line": 7,
      "text": "* background reading: RFC 793",
      "label": "RFC-793",
      "target": {"path": "app/foo.ts", "line": 5, "name": "bar"},
      "lang": "typescript"
    }
  ],
  "rationale_count": 2
}
```

Ruby → Python → JS/TS only (D5) — scss/html/yaml/markdown/bash never get
a rationale node, even when they contain comment-like, prefix-matching
text (the capture is added to exactly four of the QUERIES entries, never
those five). `target` is the comment's tightest enclosing symbol (the
`rationale_for` edge's destination), or `null` when the comment sits
outside every known symbol's range (e.g. a module-level comment) — a
real "no enclosing definition" fact, not an error. `label` is the
canonicalized `ADR-0011`/`RFC-793`-style identifier (JS/TS only,
`extract.py:1087`'s regex ported over), `null` otherwise.

`rationale_for` edges carry **no confidence value** and live in their
own `rationale` relation, entirely separate from the call/inheritance
`edges` table — a rationale comment was never a call/inheritance
candidate in the first place (structural: the tree-sitter capture that
feeds it is tagged `comment.*`, never `def.*`, so `PRODUCED_KINDS`
mechanically can never contain `"rationale"` — the same guarantee that
keeps `AMBIGUOUS` out of the analysis graph, applied here to keep
rationale comments out of `symbols` altogether). A `# NOTE:`-shaped
string inside a string or template literal is never captured either —
tree-sitter's grammar distinguishes `comment` nodes from `string`/
`template_string` nodes at the parse-tree level, not by a text filter
applied after the fact.
