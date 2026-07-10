# atlas-aci

Reference MCP server and setup bundle for the **ATLAS bounded
Agent-Computer Interface**. Read-only by construction — the agent
talking to this server cannot mutate your repository. Not by prompt,
not by convention. Mechanically.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](mcp-server/pyproject.toml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)](mcp-server/pyproject.toml)
[![Status](https://img.shields.io/badge/status-reference%20impl-orange.svg)](#status)
[![MCP SDK](https://img.shields.io/badge/mcp-%E2%89%A51.2-6e44ff.svg)](mcp-server/pyproject.toml)

```
┌─────────────────────────────────────────────┐
│            your MCP host                    │
│  (Claude Code / Copilot / Cursor / …)       │
└──────────────────┬──────────────────────────┘
                   │ stdio  (tools/list, tools/call)
                   ▼
┌─────────────────────────────────────────────┐
│   atlas-aci  (this server)                  │
│   ┌──────────────────────────────────────┐  │
│   │ enforcement.py                       │  │  ← read-only allowlist,
│   │  • READ_ONLY_TOOLS frozenset         │  │    path-traversal guard,
│   │  • assert_path_in_repo               │  │    sliding-window rate
│   │  • assert_rate_limit                 │  │    limiter, bound helpers,
│   │  • cap_lines / cap_matches / …       │  │    telemetry sink
│   └───────────────┬──────────────────────┘  │
│                   │ every call funnels here │
│   ┌───────────────▼──────────────────────┐  │
│   │ 7 tools  (view_file, list_dir,       │  │
│   │  search_text, search_symbol,         │  │
│   │  graph_query, test_dry_run,          │  │
│   │  memex_read)                         │  │
│   └───┬─────────────────────────────┬────┘  │
│       │                             │       │
│   ┌───▼──────┐               ┌──────▼────┐  │
│   │ memex.py │               │codegraph  │  │
│   │ hashed-  │               │tree-sitter│  │
│   │ dir KV   │               │+ SQLite   │  │
│   └──────────┘               └───────────┘  │
└─────────────────────────────────────────────┘
```

---

## Contents

- [Status](#status)
- [Migration](#migration)
- [The seven tools](#the-seven-tools)
- [Why read-only](#why-read-only)
- [Quick start](#quick-start)
- [Integrate with your codebase](#integrate-with-your-codebase)
- [Supported hosts](#supported-hosts)
- [Running in Docker](#running-in-docker)
- [Container image](#container-image)
- [Development](#development)
- [Repository layout](#repository-layout)
- [Security invariants](#security-invariants)
- [Production hardening](#production-hardening)
- [Further reading](#further-reading)
- [License](#license)

---

## Status

Reference implementation / MVP. Apache-2.0. Python ≥3.11, managed with
[`uv`](https://docs.astral.sh/uv/). Runs under the official `mcp`
Python SDK over stdio. Canary suite and host wiring docs target
Claude Code, GitHub Copilot custom agents, and Cursor.

> **Security boundary.** `test_dry_run` spawns a subprocess inside the
> repo. For untrusted models, the operator must sandbox this at the
> OS level — DevContainer, Firecracker microVM, or equivalent. The
> code alone cannot enforce it. See
> [`SETUP.md §8`](SETUP.md#8-production-hardening-checklist) for the
> full hardening checklist.

---

## Migration

Upgrading to v2.0.0 (or any release that bumps `SCHEMA_EPOCH`)? One
command:

```bash
atlas-aci index --repo /path/to/your/repo
```

The on-disk index is pure, disposable derived data — always rebuilt
fully from source, never migrated in place — so a schema change is
always a fresh `.atlas/graph.<SCHEMA_EPOCH>.db` (currently epoch `5`),
never an `ALTER TABLE` ladder against the old one. Run the command above
once per repo you've indexed before; there is nothing else to do.
`serve` never requires this step to be run *for* you and never attempts
it itself. **`serve` itself always starts** — it does not check the
epoch at startup — but every tool call that touches the code graph
(`search_symbol`, `graph_query`) independently asks whether the
current-epoch index exists, and a stale or missing one returns a
structured `INDEX_UNAVAILABLE` `ToolError` naming this exact command,
rather than silently serving stale or wrong results. `serve` performs
zero writes under `.atlas` either way — a mismatch never triggers a
sweep or a rebuild, only that per-call error. A downgrade to an older
binary finds no matching epoch file and rebuilds its own on its next
`index` run — one rebuild per direction you move, never a ping-pong.

---

## The seven tools

All bounds below are **mechanical** — enforced in the dispatcher
before the tool sees the request, not as advisory hints to the model.
Defaults live in
[`mcp-server/src/atlas_aci/config.py`](mcp-server/src/atlas_aci/config.py)
and can be tightened per deployment.

| Tool | Purpose | Hard bound |
|------|---------|------------|
| `view_file` | Read a window of lines from a UTF-8 text file. Rejects binaries by extension and by `UnicodeDecodeError`. | ≤100 lines/call, ≤8 KiB/call, `next_cursor` for paging |
| `list_dir` | List a directory, respecting the skip-list (`node_modules`, `vendor/bundle`, `.git`, `.atlas`, …). | ≤200 entries/call, overflow flag if truncated |
| `search_text` | Ripgrep-backed regex search over a repo-relative scope or glob. Smart-case by default. | ≤50 matches/call, 15s wall-clock timeout |
| `search_symbol` | Index-backed symbol lookup. Returns definitions + references for a name (optionally filtered by `kind`). | Requires `atlas-aci index` first; central element cap + byte ceiling (truncated + flagged) |
| `graph_query` | Tiny DSL over the code graph: `callers_of:Sym`, `definitions_of:Name`, `subclasses_of:Class`, `god_nodes:`, `communities:`, `rationale:`. | Rejects unknown verbs; central element cap + byte ceiling (truncated + flagged) |
| `test_dry_run` | Run one test file (optionally filtered by case name) as a subprocess with captured stdout/stderr. | 30s wall-clock, ≤8 KiB stdout+stderr. **Operator must sandbox.** |
| `memex_read` | Byte-exact retrieval of a previously captured excerpt via its `memex://excerpt/<sha256>` ref, minted by `Memex.write()`. No ATLAS tool currently emits one. | Scoped to the hashed-dir backend |

Process-wide: **200 calls/minute** sliding-window rate limit. Every
call is recorded in telemetry (per-tool count, bytes out, overflow
flags, errors) — wire `Enforcement.records` to your observability
sink.

### `graph_query` DSL — edge shape (v2.0.0 / A1)

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
`.spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.md`.
"What `graph_query` returns" and "what `god_nodes`/`communities` analyze"
deliberately differ, and both responses carry `analysis_basis`,
`ambiguous_edges_excluded`, and `resolved_edge_count` fields making that
divergence visible rather than implicit — a consumer who ranks via
`god_nodes` (or groups via `communities`) and then queries `callers_of`
and sees AMBIGUOUS edges too is not seeing a contradiction; the analysis
never included them.

### `graph_query` DSL — `god_nodes:` (v2.0.0 / A2)

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

### `graph_query` DSL — `communities:` (v2.0.0 / A3)

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
above and `.spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.md`
for the full measurement. The probe methodology (two pinned reference
repos, networkx Louvain as the comparison baseline) is a one-time
gate-clearing exercise, not a shipped runtime path — networkx never
appears in `mcp-server/pyproject.toml` or `mcp-server/uv.lock`.

### `graph_query` DSL — `rationale:` (v2.0.0 / A4)

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

---

## Why read-only

`atlas-aci` implements the bounded ACI contract from the ATLAS spec.
The thesis is narrow: a code-exploration agent should not need
`edit_file`, `write_file`, `shell_exec`, or any mutating primitive to
do its job. Stripping those tools out of the interface is cheaper and
more reliable than relying on prompt discipline. If a tool cannot be
called, it cannot be misused.

Every call funnels through `enforcement.assert_read_only` before any
tool code runs. The allowlist is a `frozenset` literal in source — not
a config value — so "add a write tool" requires a code change, a
review, and a release. See
[`mcp-server/src/atlas_aci/enforcement.py`](mcp-server/src/atlas_aci/enforcement.py).

**Explicitly absent:** `edit_file`, `write_file`, `shell_exec`,
`git_commit`, `git_checkout`, `migration_apply`, `deploy_run`, and any
other mutating primitive. Attempts to call them return `FORBIDDEN`
from `assert_read_only` before any code runs. If your workflow needs
to apply edits, run migrations, or deploy code, use a different
server — don't hollow out this one.

### `atlas-aci export` / `atlas-aci import` — CLI-only, by design (v2.0.0 / A5)

v2.0.0 adds a deterministic, portable JSONL export/import of the code
graph (D6) — the point being "one person builds the graph, everyone
else benefits immediately on `git pull`" instead of everyone re-parsing
the same repo. Both are **CLI commands only**
(`atlas-aci export --repo <repo> <out.jsonl>` / `atlas-aci import --repo
<repo> <in.jsonl>`) — neither is registered as an MCP tool, and neither
ever will be without a deliberate, separately-reviewed decision to widen
the read-only thesis above.

**`import` is a mutating primitive, full stop.** It replaces the entire
`.atlas/graph.<epoch>.db` from the bytes of a file on disk (index-path
only, DIR-2 — same write category as `atlas-aci index` itself). The
threat model this project defends is explicit: **the agent is the
untrusted party**, `serve` is read-only by construction (`READ_ONLY_TOOLS`
is a closed, source-level allowlist — see above), and every read tool's
correctness assumes the index it queries reflects the real repository. An
agent-callable `import` would hand that same untrusted party a way to
silently replace the ENTIRE index — every future `search_symbol`,
`graph_query`, `callers_of` result — with attacker-chosen content, from
any file reachable on the container's filesystem. The export format's
`content_hash` check only proves a file wasn't truncated in transit; it
proves nothing about *provenance* — a hand-crafted JSONL with a
self-consistent, freshly-computed hash passes the exact same check a
genuine `atlas-aci export` output does. There is no way to distinguish
"a real export" from "a plausible forgery" from inside `import_jsonl`
itself. That is precisely why this is an operator-invoked, human-in-the-loop
CLI command, never a tool a served agent can reach.

**`export` is a closer call, argued through rather than assumed.** It
never WRITES to the index DB itself — it opens `.atlas/graph.<epoch>.db`
read-only and writes a NEW file, at whatever `out_path` the caller
chooses (conventionally `.atlas/export/`, see below, but not required to
be), computed entirely from content already exposed by the seven read tools
(`search_symbol`, `graph_query`, and friends already let an agent read
every symbol/edge/rationale row this export would serialize). Nothing
export produces is new information. The case *for* a tool: it wouldn't
leak anything a sufficiently persistent agent couldn't already reconstruct
by paging through `graph_query` results. The case *against*, which wins:
`READ_ONLY_TOOLS` today has a property enforceable by inspection alone —
every tool in it performs zero filesystem writes, full stop, no need to
reason about scope or destination per tool. An `export` tool would be the
first exception, and would need its own path-scoping argument (limit
`out_path` to inside the repo? inside `.atlas`? either narrows the "commit
this artifact anywhere" use case the CLI command exists for) forever after.
The `write_file`-shaped capability — "let the model choose a path and put
bytes there" — is exactly the primitive this project's whole thesis
excludes, regardless of how well-scoped the bytes are. Keeping `export`
CLI-only preserves a clean, mechanically-checkable invariant over
carving out a single well-reasoned exception; `enforcement.py`'s
`READ_ONLY_TOOLS` frozenset stays the complete, closed tool set either
way.

**Cold-start workflow** (the reason this exists): commit the JSONL export
alongside your repo — conventionally at `.atlas/export/graph-export.jsonl`
(see `.gitignore`: `.atlas/*` is ignored, `.atlas/export/` is explicitly
NOT — derived data stays out, the portable artefact goes in), or publish
it as a CI artifact / fetch it from a shared cache instead — then on a
fresh checkout run

```bash
atlas-aci export --repo /path/to/your/repo .atlas/export/graph-export.jsonl
git add .atlas/export/graph-export.jsonl && git commit

# ...on a fresh checkout:
atlas-aci import --repo /path/to/checkout .atlas/export/graph-export.jsonl
```

instead of a full `atlas-aci index` — this skips re-parsing every source
file entirely. `import` is idempotent (repeat imports of the same file
reproduce the identical DB) and rejects a truncated, hand-edited, or
wrong-`schema_epoch` file with a clean, actionable error rather than a
partial or silently-wrong index. `import` also validates every path in
the file is repository-relative and repository-contained before
inserting a single row — a hand-edited or foreign export containing
`../../etc/passwd` (or an absolute path) is rejected, never inserted
verbatim; `content_hash` proves the bytes weren't truncated, never that
the paths inside them are safe, so that check is separate and mandatory.

**Known, named cross-OS hazards (not mechanically closed).** Byte-determinism
is verified on the macOS/Linux CI matrix (both POSIX, both forward-slash
path separators) for this project's own source and the two pinned
reference repos, but three hazards remain, disclosed rather than silently
assumed away: (0) a Windows backslash-separated relative path (outside
the macOS/Linux CI matrix AC-REL-1 itself is scoped to) is a real,
separate hazard this export does not close; (1) unicode filename normalization — macOS
traditionally presents decomposed (NFD) filenames, Linux presents
whatever bytes were written (typically composed, NFC) — could change an
exported path's bytes for a non-ASCII filename across operating systems
(mitigated in practice by git's default `core.precomposeUnicode` on
macOS; neither pinned reference repo has non-ASCII filenames); (2)
case-only-colliding filenames (`Foo.rb` / `foo.rb`) are two distinct
files on a case-sensitive filesystem but alias to one on a
case-insensitive macOS volume — a real filesystem-level data difference
before export ever runs, not something any export format can paper over.

### Export size ceiling (v2.0.0 / AC-REL-2)

Read this before committing a large repo's export. GitHub rejects any
single committed file at or above **100 MB** outright (`git push`
fails); it separately warns starting around **50 MB** (still
committable, but a visible signal). Measured once, for real, on the
larger of this project's two pinned Rails-scale reference repos:

| Repo | Files (Ruby) | Export size | % of the 100 MB ceiling |
|------|--------------|-------------|--------------------------|
| Spree @ `6699cde4` | 2,181 | **88,742,743 bytes (~84.6 MB)** | **84.63%** |

That is a **15% margin, not a 10x one.** A Rails application moderately
larger than Spree — or the same repo after this project's own extracted-grammar
coverage widens — produces an export that cannot be committed at all,
with the entire point of A5 (D6: *"one person builds the graph, everyone
else benefits immediately on `git pull`"*) silently stopping working right
at that boundary. This is a **documented bound, not a silent one**: `atlas-aci
export` itself warns (to stderr, never fails the export) once its output
crosses the 50 MB soft threshold, and warns more insistently at or past
the 100 MB hard limit — see `_GITHUB_FILE_WARN_BYTES`/
`_GITHUB_FILE_HARD_LIMIT_BYTES` in
[`mcp-server/src/atlas_aci/__main__.py`](mcp-server/src/atlas_aci/__main__.py).

**What to do if your export crosses the ceiling** (deliberately NOT fixed
by changing the export format — D6/AC-A5-1 freeze canonical, uncompressed
JSONL; a compressed-by-default export is a v2.1 design decision, not a
release-prep edit):
- **Compress it out-of-band.** `gzip -9 graph-export.jsonl` and commit the
  `.gz` instead (JSONL compresses well — repetitive keys, sorted paths);
  `import` does not read gzip directly today, so decompress before
  `atlas-aci import`.
- **Don't commit it.** Publish the export as a CI artifact or to a shared
  cache instead of `git add`-ing it, and have each checkout fetch it
  before `import` (see `INTEGRATION.md`'s Strategy C).
- **Re-index instead of importing**, for a one-off — `atlas-aci index`
  never has this ceiling; only the *portable, committed* artifact does.

---

## Quick start

The 30-second path. For the full walkthrough, read
[`SETUP.md`](SETUP.md) (install → smoke → canaries → hardening) and
[`INTEGRATION.md`](INTEGRATION.md) (pointing the server at your own
repo).

```bash
cd mcp-server
uv sync

# Build the code-graph index for your repo (one-time, ~30–120s for
# large Rails-scale codebases).
uv run atlas-aci index --repo /path/to/your/repo \
    --langs ruby,python,javascript,typescript

# Start the stdio server (this is what a host launches under the hood).
uv run atlas-aci serve --repo /path/to/your/repo
```

Already have a teammate's index? Skip the parse entirely:

```bash
# One person builds it once and commits the portable export...
uv run atlas-aci export --repo /path/to/your/repo .atlas/export/graph-export.jsonl
git add .atlas/export/graph-export.jsonl && git commit

# ...everyone else reproduces it on `git pull`, cold-start, no re-parsing.
uv run atlas-aci import --repo /path/to/your/repo .atlas/export/graph-export.jsonl
```

`export`/`import` are CLI-only, operator-invoked commands, never MCP
tools a served agent can call — see [Why read-only](#why-read-only) for
the threat-model reasoning.

Inspect the tool manifest without booting a host:

```bash
uv run atlas-aci tools --repo /path/to/your/repo | jq 'map(.name)'
# ["view_file","list_dir","search_text","search_symbol",
#  "graph_query","test_dry_run","memex_read"]
```

Any output containing a name not in that list means the server is
non-conformant — file a bug.

---

## Integrate with your codebase

Once the server runs, [`INTEGRATION.md`](INTEGRATION.md) is the
onboarding doc for pointing it at a real project: indexing strategy,
language selection, skip-list tuning, keeping the index fresh
(post-commit hooks, CI), multi-repo setups, and the common pitfalls
that catch first-time operators.

> **TL;DR** — `atlas-aci index` writes to `<repo>/.atlas/`; add that
> to your `.gitignore`; re-index cheaply with `--since <marker>` (skips
> files whose `(mtime, size)` are unchanged since the last pass — it does
> **not** diff a git ref; see
> [`INTEGRATION.md` §Keep the index fresh](INTEGRATION.md#step-6--keep-the-index-fresh));
> one server process per repo.

---

## Supported hosts

| Host | Wiring doc | Notes |
|------|-----------|-------|
| Claude Code | [`hosts/claude-code.md`](hosts/claude-code.md) | Best-ranked host. One JSON edit to `claude_desktop_config.json` per repo. |
| GitHub Copilot custom agents | [`hosts/copilot.md`](hosts/copilot.md) | MCP server goes in agent frontmatter. Skill descriptions must match ATLAS phases (T, L, A, S) for the selector to fire correctly. |
| Cursor | [`hosts/cursor.md`](hosts/cursor.md) | MCP config via Settings UI or `~/.cursor/mcp.json`. Cursor's agent loop is more eager — keep ATLAS invocations in Plan mode unless you want edits attempted alongside exploration. |

---

## Running in Docker

Two invocations, documented in detail in the
[`mcp-server/Dockerfile`](mcp-server/Dockerfile) header.

**Index once** (writable repo mount):

```bash
docker run --rm \
    -v /path/to/repo:/repo \
    atlas-aci index --repo /repo --langs ruby,python,javascript,typescript
```

**Serve long-lived** (read-only repo mount, persistent memex):

```bash
docker run --rm -i --read-only \
    -v /path/to/repo:/repo:ro \
    -v atlas-memex:/memex \
    atlas-aci
```

The `--read-only` flag plus the `:ro` bind mount is the OS-level
guarantee that the server cannot mutate your repo — stronger than any
code-level check. The runtime image runs as an unprivileged
`atlas:10001` user with `ripgrep` and `git` preinstalled. See
[`INTEGRATION.md`](INTEGRATION.md#running-in-docker) for the full
index-then-serve pattern.

---

## Container image

`ghcr.io/rynaro/atlas-aci` is the canonical distribution channel.
Images are built on every `v*` tag push via the [release workflow](.github/workflows/release.yml)
and support **linux/amd64 and linux/arm64** (Apple Silicon and Linux
servers are both first-class targets).

### Pull commands

```bash
# Latest stable release
docker pull ghcr.io/rynaro/atlas-aci:latest

# Specific version
docker pull ghcr.io/rynaro/atlas-aci:0.2.0

# Immutable digest pin (recommended for production)
docker pull ghcr.io/rynaro/atlas-aci@sha256:<digest>
```

Digest-pinned pulls are the most reliable — a tag can be overwritten,
but a digest never changes. The release notes for each tag include the
exact `ghcr.io/rynaro/atlas-aci@sha256:<digest>` string ready to copy.

### Supply-chain attestations

Every release is signed and attested:

- **Cosign keyless signature** — signed with the GitHub Actions OIDC
  token; no long-lived keys required.
- **SBOM (SPDX)** — attached as a ghcr.io attestation via
  `actions/attest-sbom`.
- **Build provenance** — attached via `actions/attest-build-provenance`.

### Verification

Every published GA image (tagged `v<x>.<y>.<z>` without a pre-release suffix)
has been scanned by Trivy before signing and has no known HIGH or CRITICAL CVEs
at release time — the workflow fails and does not proceed to cosign, SBOM
attestation, or GitHub Release creation if any are found. Pre-release images
(`-rc.*`, `-beta.*`) are exempt from the fail-on-vuln gate to allow
security-bug-fix testing, but the Trivy SARIF report is still uploaded and
visible in the repository's **Security** tab. Findings that are discovered
post-release also surface there; users who need a known-good image can pin an
earlier digest via `--image-digest` while a patch release is being prepared.

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

### Consumer integration via Eidolons

If you use the [Eidolons nexus](https://github.com/Rynaro/eidolons),
`eidolons mcp atlas-aci` pulls from `ghcr.io/rynaro/atlas-aci` by
default and wires the digest-pinned image into your project's
`.mcp.json`. For air-gapped environments or when the registry is
unreachable, pass `--build-locally`:

```bash
eidolons mcp atlas-aci pull --build-locally
```

See [`INTEGRATION.md §GHCR distribution`](INTEGRATION.md#ghcr-distribution)
for the full consumer workflow.

---

## Development

Container-based dev is preferred: it keeps the host clean and matches
how the server is deployed. A dedicated image
([`mcp-server/Dockerfile.dev`](mcp-server/Dockerfile.dev)) ships
`pytest`, `ruff`, and `mypy` pre-installed:

```bash
cd mcp-server
docker build -f Dockerfile.dev -t atlas-aci-dev .

# Interactive dev shell
docker run --rm -it -v "$PWD":/work atlas-aci-dev

# Or run one-shot commands without entering the shell
docker run --rm -v "$PWD":/work atlas-aci-dev uv run pytest -q
docker run --rm -v "$PWD":/work atlas-aci-dev uv run ruff check src/ tests/
docker run --rm -v "$PWD":/work atlas-aci-dev uv run mypy src/
```

The bind-mounted source round-trips edits without rebuilds; the
`.venv` lives outside `/work` so it survives across runs.

If you need to run on the host instead:

```bash
cd mcp-server
uv sync                                    # install deps
uv run pytest                              # test suite
uv run ruff check src/ tests/              # lint
uv run ruff format --check src/ tests/     # format check
uv run mypy src/                           # type check
uv run atlas-aci --help                    # CLI smoke
```

Pytest runs with `asyncio_mode = "auto"`
(`mcp-server/pyproject.toml`), so test functions can be plain
`async def` without per-test markers.

### Running the canary suite

From the repo root:

```bash
uv run python scripts/run-canaries.py \
    --repo /path/to/your/repo \
    --canaries atlas/evals/canary-missions.md \
    --output /tmp/canary-results.json
```

**Status: the host dispatcher is not implemented yet.** `--host stub` is the
only wired option; `StubDispatcher.dispatch()` raises `NotImplementedError`,
and any other `--host` value raises the same in `main()`. There is no
canary pass-rate to quote until a real dispatcher (Claude Code API, Copilot
Action, Cursor headless) is implemented — see the deferred note in
[`scripts/run-canaries.py`](scripts/run-canaries.py). Wiring one in is
tracked as follow-up work; see [`SETUP.md §7`](SETUP.md#7-run-the-canary-suite).

---

## Repository layout

```
atlas-aci/
├── README.md                          ← you are here (orientation)
├── SETUP.md                           ← end-to-end playbook
├── INTEGRATION.md                     ← codebase onboarding workflow
├── CLAUDE.md                          ← in-repo Claude Code guidance
│
├── mcp-server/                        ← the Python MCP server
│   ├── pyproject.toml
│   ├── Dockerfile                     ← production runtime image
│   ├── Dockerfile.dev                 ← dev-loop image (pytest/ruff/mypy)
│   ├── .dockerignore
│   ├── README.md                      ← package-scoped quick start
│   ├── src/atlas_aci/
│   │   ├── __main__.py                ← Click CLI: serve | index | export | import | tools
│   │   ├── server.py                  ← MCP stdio wiring + dispatcher
│   │   ├── enforcement.py             ← read-only guard, bounds, rate limit, telemetry
│   │   ├── config.py                  ← Config dataclass + skip patterns + path_is_within
│   │   ├── codegraph.py               ← tree-sitter indexer + SQLite queries + export/import (A5)
│   │   ├── label_propagation.py       ← dependency-free deterministic LPA core (A3)
│   │   ├── memex.py                   ← hashed-dir excerpt store
│   │   └── tools/
│   │       ├── view_file.py
│   │       ├── list_dir.py
│   │       ├── search_text.py         ← ripgrep wrapper
│   │       ├── search_symbol.py
│   │       ├── graph_query.py
│   │       └── test_dry_run.py
│   └── tests/
│       ├── test_enforcement.py        ← safety invariants
│       ├── test_codegraph.py          ← indexer + language-table honesty
│       ├── test_confidence.py         ← A1 confidence enum (EXTRACTED/INFERRED/AMBIGUOUS)
│       ├── test_communities.py        ← A3 label propagation + D3a probe gate
│       ├── test_rationale.py          ← A4 rationale nodes
│       ├── test_export.py             ← A5 export/import (CodeGraph API level)
│       ├── test_cli_export_import.py  ← A5 export/import (CLI reachability level)
│       ├── test_graph_query.py        ← graph_query DSL dispatch
│       ├── test_server.py             ← central bounds chokepoint (D2)
│       ├── test_schema_epoch.py       ← epoch-namespaced DB substrate (D1)
│       └── test_thesis_negatives.py   ← Track NEG / AC-REL-3 (no LLM, no networkx, ...)
│
├── hosts/
│   ├── claude-code.md
│   ├── copilot.md
│   └── cursor.md
│
└── scripts/                           ← guards for the augmentation workstreams (A1-A5), not
    │                                     shipped project code — see each file's own header
    ├── run-canaries.py                ← canary mission orchestrator
    ├── verify-probe-verdict.py        ← D3a probe verdict verifier (AC-A3-1/F7)
    ├── verify-export-size.py          ← AC-REL-2 export-size-on-Spree verifier
    ├── harden-gate-classify.sh        ← harden-gate.yml's diff classifier
    └── fingerprint-fixture/           ← committed multi-language fixture for the
                                          behavioural indexer fingerprint
```

---

## Security invariants

These are the properties the enforcement layer guarantees. If any of
them fail under test, the server is non-conformant — every invariant
has a corresponding test in
[`mcp-server/tests/test_enforcement.py`](mcp-server/tests/test_enforcement.py).

1. **Read-only allowlist.** Any tool name not in
   `enforcement.READ_ONLY_TOOLS` is rejected with `FORBIDDEN` before
   any tool code runs. The set is a source-level `frozenset` literal.
2. **Path-traversal guard.** Every file path is resolved (including
   symlinks) and checked against the configured repo root before any
   filesystem access. Paths that resolve outside the repo are rejected
   with `FORBIDDEN`.
3. **Mechanical bounds.** Line / entry / match / byte caps and the
   sliding-window rate limiter are applied per call in
   `enforcement.py`, plus a central dispatch-layer chokepoint
   (`server.py`'s `apply_central_bounds`) that element-caps every tool's
   declared list-valued field and enforces an absolute byte ceiling —
   the floor every tool (including `search_symbol` and `graph_query`)
   passes through regardless of its own caps. Tools can only narrow
   bounds, never widen them.
4. **Structured errors.** All errors are `ToolError(code, message,
   retry_hint)`. The retry hint is one of `none`, `narrower_scope`,
   `different_tool` — intended to be acted on by the model without
   humans in the loop.
5. **Telemetry of every call.** Tool name, arg shape (with the
   `content` field redacted), bytes out, duration, overflow flag, and
   error code are recorded for every call. Wire
   `Enforcement.records` to your observability sink at process start.

---

## Production hardening

Before letting `atlas-aci` run unattended on shared infrastructure,
work through the full checklist in
[`SETUP.md §8`](SETUP.md#8-production-hardening-checklist). The two
items most commonly skipped:

- **Sandbox `test_dry_run`.** The code cannot enforce this; the
  operator must put the server behind a DevContainer, Firecracker
  microVM, or equivalent before enabling the tool for untrusted
  models.
- **Mount the repo read-only at the OS level.** A read-only bind
  mount is a stronger guarantee than any code-level check and
  survives mis-configuration of the server itself.

---

## Further reading

- [`SETUP.md`](SETUP.md) — end-to-end playbook: install → host
  wiring → canaries → hardening → operating notes → anti-patterns.
- [`INTEGRATION.md`](INTEGRATION.md) — onboarding an existing
  codebase: indexing, skip tuning, freshness, multi-repo.
- [`mcp-server/README.md`](mcp-server/README.md) — package-scoped
  quick start, closer to the code.
- [`CLAUDE.md`](CLAUDE.md) — how Claude Code should navigate this
  repo when you open it in an agent loop.
- [`hosts/claude-code.md`](hosts/claude-code.md),
  [`hosts/copilot.md`](hosts/copilot.md),
  [`hosts/cursor.md`](hosts/cursor.md) — per-editor wiring.

---

## License

Apache-2.0. See the top-level [`LICENSE`](LICENSE) file for the full text;
[`mcp-server/pyproject.toml`](mcp-server/pyproject.toml) declares the same
identifier for the packaged distribution.
