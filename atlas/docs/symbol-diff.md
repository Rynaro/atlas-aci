# Comparing two snapshots at symbol and contract boundaries

`atlas-aci diff` answers one question about two **explicit, already-built**
snapshots of one repository: which declarations were added, removed, or changed
in *signature* or *body*, and which only moved. It is an operator CLI command and
a read-only library (`atlas_aci.query.diff`). Nothing is added to the seven
read-only MCP tools, no protocol enum or envelope changes, and nothing is ever
written under either graph root or either repository.

Every record points at hash-verified bytes in **both** source snapshots. The
live checkout is never read.

## Workflow

Each side is a (repository, graph root) pair that holds one complete graph
namespace:

```bash
# Same checkout, two roots: index, edit, index again into a second root.
atlas-aci index --repo /path/to/repo --graph-root /graphs/before
# ... edit the working tree ...
atlas-aci index --repo /path/to/repo --graph-root /graphs/after
atlas-aci diff  --repo /path/to/repo \
    --before-graph-root /graphs/before --after-graph-root /graphs/after

# Two git worktrees of one repository under one root.
atlas-aci diff --graph-root /graphs/shared \
    --before-repo /path/to/main --after-repo /path/to/worktree
```

`--repo` sets both sides' repository; `--before-repo`/`--after-repo` override one
side. `--graph-root` sets both roots; `--before-graph-root`/`--after-graph-root`
override one side. A side without a graph root uses `<repo>/.atlas`. Naming the
same (repo, graph root) namespace for both sides is a usage error (exit 2).
A published graph namespace holds exactly one generation, so a "before" needs its
own namespace: build it before you edit.

The change set is printed as canonical JSON (sorted keys, no whitespace, UTF-8)
plus one newline. Nothing is printed on rejection (exit 1) or bad usage (exit 2).

## When a comparison is rejected

`DiffRejected(reason)` (CLI: `Snapshot diff rejected: <reason>`, exit 1). The
reasons are closed, checked in this order:

| Reason | Meaning |
| --- | --- |
| `index_unavailable` | A side has no complete compatible graph generation |
| `source_snapshot_unavailable` | A side has no available source snapshot (JSONL import, legacy generation, unreadable pack) |
| `identity_kind_mismatch` | One side is a git checkout, the other is not (or git is unavailable on one) |
| `repository_identity_mismatch` | Git: different `git-common-dir`. Non-git: different root |
| `schema_mismatch` | Different graph schema epoch/DDL |
| `configuration_mismatch` | Different indexed languages or skip patterns (added/removed would be a configuration artifact) |
| `producer_mismatch` | Different indexer, queries or grammar versions, or the running `tree-sitter`/`tree-sitter-language-pack` differ from what the sides recorded |

Repository relatedness is only what the snapshot evidence proves: worktrees of
one repository share a `common_dir`; unrelated repositories (even with identical
content) and distinct non-git directories do not. Two clones of one upstream are
unrelated. **Limitation:** an unrelated repository re-created at the same path
with the same `.git` location is undetectable from snapshot evidence.

SCIP (`scip_*`) entities are excluded. JSONL-imported and legacy generations carry
no source snapshot and are rejected.

## Categories

For an entity present on both sides (same entity id), in precedence order:

| Category | When |
| --- | --- |
| `signature` | The signature fingerprint differs (`body_changed` is reported as a flag) |
| `body` | Signature equal, normalized body differs |
| `format` | Both fingerprints equal, the declaration's own text differs: whitespace or comments only |
| `location` | Fingerprints and own text equal; only the start position differs |
| `unknown` | The entity cannot be soundly classified (closed `reason`, below) |

`added` and `removed` are entity-id set differences. `unchanged` entities are
counted in `summary` only. Every record carries `signature_changed`,
`body_changed`, `text_changed` and `position_changed` (`null` where a flag cannot
be determined).

A *container* (class, module, trait, `mod`) is reported on its own syntax only:
a nested declaration is a placeholder in the parent's body fingerprint and own
text, so a class whose method grew is `unchanged` (or `location`); adding,
removing or renaming a member changes the parent's body.

### What is in a signature

Syntax the grammar places outside the declaration node counts as signature
("context"): Python decorators, JavaScript/TypeScript `export`/`default`/class and
method decorators, the TSX `const`/`let` keyword, Rust `#[attributes]`, and Ruby
inline visibility (`private def ...`). Per language the declaration node, name,
body and the child keys the map understands are frozen in
`mcp-server/src/atlas_aci/query/diff_syntax.py` (`ROWS`); any other named child
makes the declaration `unknown(unmapped_declaration_child)` instead of a guess.

| Language | Parsed kinds |
| --- | --- |
| python | function, class |
| javascript, typescript, tsx | function, method, class; tsx also arrow/function-expression declarators |
| ruby | method (including singleton), class, module |
| rust | function, method, struct, enum, trait, module (`mod x;` has no body) |
| bash | function (the header form `f()` / `function f` is the signature) |
| scss | mixin, function |

Every other kind (scss selector/placeholder/variable/id, html id, yaml key,
markdown heading) is **raw text**: equal text is `location` or unchanged, any text
change is `unknown(fingerprint_unsupported_kind)`.

### How the body is normalized

Fingerprints are recomputed from the snapshot bytes with the same grammar:

- Comment nodes (`COMMENT_TYPES`) are omitted with their subtrees.
- Node types and nesting are hashed, not only the token sequence, so a Python
  statement dedent with an unchanged token sequence is a `body` change.
- String, template, regex, heredoc and character literals keep their interior
  whitespace and interpolations verbatim (`ATOMIC_TYPES`).
- Whitespace between sibling nodes is insignificant only where the gap is pure
  ASCII whitespace; any other uncovered byte is `unknown(uncovered_source_text)`.

Documented consequences: a `format` change can mask a behavior-bearing comment
(`# type: ignore`, `// @ts-ignore`, pragmas, Rust doc comments); whitespace-only
text leaves such as JSX text report as `body`; a `;` versus newline separator in
bash or JavaScript is a token and reports as `body`.

## Unknown reasons

`fingerprint_unsupported_kind`, `extraction_failed_before`,
`extraction_failed_after`, `definition_occurrence_ambiguous`,
`source_unavailable`, `declaration_node_not_found`,
`parse_error_in_declaration`, `uncovered_source_text`,
`unmapped_declaration_child`, `non_utf8_declaration`, `inconsistent_evidence`.

A path whose extraction failed on one side (it is deleted from the active tables)
is listed in `partial_paths`. Its surviving side's entities get a one-sided
`unknown` record (`extraction_failed_before`/`_after`, the failed side `null`);
`added`/`removed` are never emitted for them. Reasons that need unreadable or
contradictory stored evidence (`source_unavailable`,
`definition_occurrence_ambiguous`, `inconsistent_evidence`) are defensive.

Files whose source hash is identical on both sides (and indexed on both) are never
parsed; only hash-differing files, and files holding added or removed entities,
are re-parsed.

## Rename and move candidates

A separate `candidates` list, never merged into identity. A `removed` and an
`added` declaration pair only if they have the same language and kind, a `parsed`
basis, equal signature and body fingerprints (the name is a placeholder), and the
match is unique in both directions. `relation` is `rename`, `move` or
`rename_and_move`; the `removed`/`added` records stay unchanged.

A trivial body never pairs: a body (or, for body-less kinds, a signature) needs at
least `MIN_CANDIDATE_TOKENS` = 6 non-punctuation tokens (leaves containing a
letter, digit or underscore). `pass`, `...`, `{}`, a lone `return x`, a unit
struct or `mod x;` can never pair two unrelated declarations. Names are never
compared for similarity.

## The change set: `atlas-symbol-diff.v1`

Keys: `format`, `before`/`after` (`snapshot_id`, `generation_id`), `compatibility`,
`changes` (sorted by path, entity id), `candidates`, `partial_paths`, `summary`
(each category, `unchanged`, `candidates`), `limitations`, and `id` =
sha256(`"atlas-symbol-diff.v1:"` + canonical JSON of everything else).

A `symbol_change` record has the entity fields (`entity_id`, `language`, `kind`,
`name`, `owners`, `ordinal`, `path`), its category, the four flags, `reason`
(unknown only) and `before`/`after` side references: `snapshot_id`,
`generation_id`, `occurrence_id`, `evidence_id`, `path`, `source_sha256`,
`start_byte`/`end_byte`/`line`/`column`/`end_line`/`end_column`,
`declaration_sha256` (the raw span bytes), `own_text_sha256`, `signature_sha256`,
`body_sha256` and `fingerprint_basis` (`parsed`, `raw_text`, `unsupported`).

The same snapshot pair always yields byte-identical output: there are no
timestamps, durations, host data or absolute paths in the document itself.
**Generation and snapshot ids embed the absolute repository path, so ids (and the
change-set `id`) are not portable across checkout paths**; rebuilding both sides at
the same path reproduces them.

## Other limitations

- Duplicate same-name siblings that swap order appear as signature/body changes
  (identity uses an ordinal), not as moves.
- Entity ordinals include anonymous scope nodes: an unrelated block insertion can
  re-identify nested declarations as `removed`/`added`; the candidate matcher
  pairs them when the match is unique and non-trivial.
- TypeScript overload signatures and other non-entity siblings are not compared.
- Ruby section-style `private` (a bare `private` line) is not part of a method
  signature.
- Generated or macro-expanded code is not compared; semantic equivalence is not
  decided.
- The implementation reads two private lease attributes
  (`_source_lease.manifest`, and `source_observations` from the graph manifest) to
  avoid changing the fingerprinted indexer; the observation list is read directly
  rather than revalidated a second time.
