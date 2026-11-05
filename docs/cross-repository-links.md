# Cross-repository package links (ACI-S30)

`atlas-aci cross-repo-link` joins the import of a locked Python package in one repository (the
consumer) to the declaration that the package provider's repository holds, under an exact
**revision vector**: the ordered pair of the consumer snapshot and the provider snapshot that
were combined. `atlas-aci cross-repo-impact` then walks one step across such a link. Both are
CLI only, no MCP op: nothing is added to the seven read-only tools and the tool manifest is
byte-identical.

A link is an import reference joined to a declaration. It is **not a call**: every link and every
impact record says `call_semantics: not_claimed`, no call is completed and no name is matched
without the exact version evidence below. The overlay does not write the graph: it is a
standalone, content-addressed document written only to the operator's `--output`, built from
read-only leases of the two active graph snapshots. It never discovers, clones or fetches
anything: every input is a local file or an already indexed repository, and no network is used.

## Inputs

| Input | Where it comes from |
| --- | --- |
| Consumer repository and provider repository | Indexed with `atlas-aci index`; the snapshot ids are printed in the `index_done` log line (`snapshot_id=`) |
| Mapping | One JSON file, `atlas-cross-repo-mapping.v1` (below) |
| Lock | The consumer's `uv.lock`; its sha256 must equal the mapping's `lock_sha256` |
| Package snapshot | The `import-python-dependency` output for the locked package (`atlas-python-package-snapshot.v1`) |
| Previous overlay | Optional: the overlay being refreshed |

The roots come only from the role-keyed options `--consumer-repo`, `--consumer-graph-root`,
`--provider-repo` and `--provider-graph-root`. The mapping carries no path and no root: it names
the two repositories by operator label and exact snapshot id, so a mapping can never widen the
traversal. Traversal stays within the two repositories the mapping lists; any other repository id
is `repository_not_allowlisted`.

### The mapping

```
{
  "format": "atlas-cross-repo-mapping.v1",
  "repositories": [
    {
      "repository_id": "app",
      "role": "consumer",
      "snapshot_id": "1111111111111111111111111111111111111111111111111111111111111111"
    },
    {
      "repository_id": "lib",
      "role": "provider",
      "snapshot_id": "2222222222222222222222222222222222222222222222222222222222222222"
    }
  ],
  "consumer": {
    "lock_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
    "environment": {}
  },
  "packages": [
    {
      "provider_repository_id": "lib",
      "coordinate": "pypi:s30-shapes@1.0.0",
      "package_snapshot_sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "source_prefix": "src/"
    }
  ]
}
```

- `repositories` lists exactly two entries, one `consumer` and one `provider`; `repository_id`
  matches `[a-z0-9][a-z0-9._-]{0,63}` and is an operator label (limitation
  `repository_ids_are_operator_labels`); `snapshot_id` is the 64 hex digits of the snapshot.
- `consumer.environment` must equal the canonical environment of the S28 importer (empty for a
  lock without markers); `lock_sha256` is the digest of the lock.
- `packages` lists exactly one package: `provider_repository_id` must be the provider's id,
  `coordinate` is `pypi:<normalized name>@<version>`, `package_snapshot_sha256` is the digest of
  the package snapshot file and `source_prefix` is where the package sources live in the
  provider (for example `src/`; empty when the package directory is at the root).
- Keys are closed at every level; a duplicate key, a number, NaN or Infinity, a byte order mark,
  invalid UTF-8 or an over-deep document is `invalid_mapping`. Key order, white space and the
  order of the lists never change the result: the overlay records a canonical mapping digest.

## The revision vector

`revision_vector` is `[{role: consumer, repository_id, snapshot_id}, {role: provider,
repository_id, snapshot_id}]`, in that order and with exactly those keys. `vector_digest` is the
sha256 of `atlas-cross-repo-vector.v1:` plus the canonical vector. Every link carries a full copy
of the vector and the digest is part of its id, so a link of one vector is never the link of
another.

## Commands

```
atlas-aci cross-repo-link --mapping M --lock L --package-snapshot P
    --consumer-repo D [--consumer-graph-root G] --provider-repo D [--provider-graph-root G]
    [--previous O] --output OUT [--max-mapping-bytes N] [--max-lock-bytes N]
    [--max-package-snapshot-bytes N] [--max-overlay-bytes N] [--max-references N]
atlas-aci cross-repo-impact --overlay O --mapping M --seed HEX
    --direction {from_provider,to_provider}
    --consumer-repo D [--consumer-graph-root G] --provider-repo D [--provider-graph-root G]
    [--max-results N] [--max-links-examined N] [--max-boundaries N]
    [--max-overlay-bytes N] [--max-mapping-bytes N]
```

`cross-repo-link` prints the canonical `atlas-cross-repo-link-summary.v1` (overlay id, vector,
binding state, provider state, counts, links invalidated) and writes the overlay to `--output`.
`cross-repo-impact` prints the canonical `atlas-cross-repo-impact.v1` and writes nothing. On a
rejection (exit 1) or a usage error (exit 2) nothing is printed on stdout and the output file is
untouched; the messages are fixed text and never echo a path or an input.

Order of work (the first failure wins): limits and output location; the mapping (read, parsed,
allowlist, distinct ids); the lock digest and the locked package; the package snapshot digest and
validity; the previous overlay; then both leases (consumer first); then the bind, the references
and the resolution. The provider is not opened before the mapping, the lock and the package
snapshot have been accepted.

| Situation | Exit | Text after `Error: ` |
| --- | --- | --- |
| a limit or budget out of range | 2 | `Invalid cross-repository limit: <name>` |
| a seed that is not 64 hex digits | 2 | `Invalid --seed value.` |
| `--output` inside a repository, its `.atlas` or a graph root | 2 | `--output must be outside both repositories and both graph roots.` |
| `--output` equals an input file (also by hard link) | 2 | `--output must differ from every input file.` |
| a repository cannot be a graph namespace | 2 | `A repository cannot be opened as a graph namespace.` |
| a repository nested in the other, or a graph root inside the other repository | 2 | `The repositories and graph roots must not overlap.` |
| an input missing, not a regular file or unreadable | 1 | `Cross-repository mapping cannot be read.`, `uv lock file cannot be read.`, `Package snapshot cannot be read.`, `Previous overlay cannot be read.`, `Cross-repository overlay cannot be read.` |
| a closed rejection of the link | 1 | `Cross-repository link rejected: <reason>` |
| a closed rejection of the impact | 1 | `Cross-repository impact rejected: <reason>` |
| the output cannot be written | 1 | `Cross-repository overlay cannot be written.` |
| anything else | 1 | `Cross-repository link failed: <TypeName>`, `Cross-repository impact failed: <TypeName>` |

A graph root shared by both sides, or beside both repositories, is fine: the namespaces are keyed
by the repository path. Two directories of one git repository (a worktree, or two directories of a
monorepo) share one git common dir and are not two repositories: that is `same_repository`.
Inputs are opened without blocking and must be regular files, so a FIFO or a directory is refused
at once. The only file written is `--output`, atomically (a temporary file beside it, then a
rename).

## The bind

The mapping, the lock, the package snapshot and the provider's indexed sources must agree byte
for byte. Five checks are always computed:

| Check | Value | When it differs |
| --- | --- | --- |
| `coordinate` | `equal` | the mapping coordinate, the locked coordinate and the snapshot coordinate are not all the same: `coordinate_differs` |
| `artifact` | `equals_lock_entry` | the snapshot's wheel digest is not one of the locked wheels: `artifact_digest_differs` |
| `lock` | `equal` | the snapshot was imported from another lock: `lock_digest_differs` |
| `environment` | `equal` | the snapshot environment is not the mapping's: `environment_differs` |
| `sources` | `equal` | every python source member of the wheel, read at `source_prefix` plus its path, has an indexed observation in the provider with the same sha256: otherwise `source_member_missing` or `source_digest_differs`; `not_evaluated` when the provider is unavailable |

The binding `state` is `version_mismatch` (with the first failing detail, in the order above),
`provider_snapshot_unavailable` (with the provider detail) or `bound`. A different package
version or source digest is never a whole-run rejection: each reference that reaches it is
`unresolved_version_mismatch` with that detail. Rejections are reserved for malformed inputs,
bytes that differ from a digest the mapping declares, invalid snapshots and consumer lease
failures. The lock is tied to the consumer only by the operator's declaration
(`attestation: operator_declared`): `uv.lock` is not an indexed language.

## Provider availability

The provider is available only when its **active generation** is the snapshot the mapping names
(`matched: true`). Otherwise `state` is `unavailable` with one closed detail and no provider fact
is read: `snapshot_not_active` (another snapshot is active), `index_unavailable`,
`source_snapshot_unavailable` or `index_upgrade_required` (a graph of an older schema epoch). A
consumer that cannot be leased, is not the mapped snapshot or is of an older epoch is a rejection
(`consumer_index_unavailable`, `consumer_source_snapshot_unavailable`,
`consumer_index_upgrade_required`, `consumer_snapshot_mismatch`).

## References and resolution

A reference is a Python import binding of the consumer whose root module is a top-level module of
the package snapshot (`top_level_modules`). The supported form is an unconditional-looking,
module-level, absolute `from <module> import <name>`, aliases allowed; the identity is
`<module>.<name>`, compared by exact string equality. Relative imports and other roots are only
counted (`references_out_of_scope`). Each reference gets exactly one resolution, by this
precedence:

| Resolution | Detail |
| --- | --- |
| `unresolved_unsupported_reference_form` | `nested_scope_import` (inside a function or class), `wildcard_import`, `module_import` (`import pkg.mod`), `non_ascii_identifier` (an identifier-shaped name with a non-ASCII character), `non_identifier_name` (a name the indexer recorded that is not an identifier, such as a comment inside a parenthesised import or a line continuation), `submodule_import` (`from pkg import mod`) |
| `unresolved_local_module_shadow` | a consumer file observed in any state (even a failed one) is named like the root module (`<root>.py` or `<root>/__init__.py`, at any depth); namespace directories are not detected |
| `unresolved_version_mismatch` | `coordinate_differs`, `artifact_digest_differs`, `lock_digest_differs`, `environment_differs`, `source_member_missing`, `source_digest_differs` |
| `unresolved_provider_snapshot_unavailable` | `snapshot_not_active`, `index_unavailable`, `source_snapshot_unavailable`, `index_upgrade_required` |
| `unresolved_reexport_not_followed` | none: the package module imports the name from elsewhere; re-exports are not followed |
| `unresolved_symbol_not_declared` | `module_not_in_package`, `name_not_declared`, `module_incomplete` (the module has definitions the snapshot could not index) |
| `unresolved_ambiguous_provider_symbol` | `package_declarations` (two top-level declarations, or a declaration and a module), `provider_symbols` (the provider holds several symbols there, or one that is conditional), `provider_name_rebound` (the provider assigns, imports or star-imports that name again at module level) |
| `unresolved_provider_declaration_not_indexed` | none: the declaration is in the wheel but the provider graph has no matching symbol (class against function kinds included) |
| `linked_declaration` | none: exactly one unconditional provider symbol at the declaring member, with the declared kind, and the name is not rebound |

Only `linked_declaration` creates a link. The link names the provider entity, path, name, kind,
lines, declaration id, member and member digest, and whether the declaration is `decorated`: a
decorated definition stays linked to the undecorated declaration (limitation
`decorated_definitions_link_the_undecorated_declaration`), because the decorator may rebind the
name.

Skip patterns: the indexer skips whole paths that have one of the components `log`, `tmp`,
`build`, `dist`, `coverage` or `node_modules` (and the other default skip patterns). A wheel
member whose path under `source_prefix` has such a component has no provider observation and the
bind fails with `source_member_missing`; so do generated members such as `_version.py` that the
provider repository does not hold (limitation
`every_wheel_python_member_must_match_provider_source`; there is no exclude list).

## The overlay

`atlas-cross-repo-overlay.v1` is canonical JSON plus a newline with closed keys: `format`,
`overlay_id`, `producer` (`atlas-aci cross-repo-link`, `atlas-cross-repo-linker.v1`), `mapping` (format and canonical digest), `revision_vector`,
`vector_digest`, `consumer` (id, snapshot id, `lock` with `attestation: operator_declared`),
`provider` (id, `declared_snapshot_id`, `supplied_snapshot_id`, `matched`, `state` of
`available` or `unavailable`, `detail`), `package` (coordinates, snapshot id and digests,
`source_prefix`, `top_level_modules`, `binding` with its five checks and member counts),
`coverage`, `counts`, `limitations`, `references`, `links` and `supersedes`. It holds no
absolute path, clock, host or limit. `coverage` is
`import_reference_and_declaration_only`, `incomplete`, `call_binding: not_claimed`,
`execution: not_performed` and `traversal: mapped_repositories_only`. The ids are sha256 digests:
a reference id of the consumer repository id, snapshot id and occurrence id, a link id of the
vector digest, the reference id, the provider entity id and the declaration id, and the overlay id
of the whole body. `validate_overlay` recomputes all of them and checks the shapes, the orders,
the counts and the legality of every detail.

## Refresh and invalidation

`--previous O` refreshes an overlay. The fresh overlay is built with `supersedes: null`; if its
content (the overlay id with `supersedes` forced null) equals the previous overlay's content, the
output is that fresh build, byte for byte, so a refresh with unchanged inputs is a fixpoint and
repeating it changes nothing. Otherwise the output records `supersedes`: the previous overlay id
and vector digest, `links_invalidated`, the sorted `invalidated_link_ids` (at most 10000, with
`invalidated_link_ids_truncated`). When either snapshot id changes the vector changes, so every
old link is invalidated; the same happens when the provider is no longer the mapped snapshot. A
previous overlay must name the same roles and repository ids (`previous_overlay_unrelated`).
`--output` may not be the previous overlay.

## Impact

`cross-repo-impact` returns `status: potential_impact`, `basis: static_reference`,
`runtime_observation: not_performed`, `call_semantics: not_claimed` and `depth_limit: 1`. It
refuses an overlay whose mapping digest differs (`overlay_mapping_mismatch`), whose vector names a
repository the mapping does not (`repository_not_allowlisted`, decided before any lease) or whose
consumer snapshot is no longer the active one (`stale_revision_vector`).

- `to_provider`: the seed must be a consumer entity (`unknown_seed` otherwise). Seeds are
  file-level for module-level imports: the owner of a module-level import is the file entity, so a
  consumer function entity returns nothing (impact depth one without in-repository expansion).
  Each reference owned by the seed is a result (a linked declaration) or a boundary.
- `from_provider`: the seed must be a provider entity; the results are the links to it. If the
  provider is not the mapped, active snapshot there is one boundary at the seed and no result.

Boundary kinds: `missing_provider_snapshot` (the provider is not available now, or the overlay was
built while it was not; the detail is the provider detail), `unresolved_external_reference` (the
detail is the unresolved resolution) and `budget_exhausted` (the detail is the budget name, the
result is `complete: false`). Records are examined in id order, so truncation is deterministic.

## Limits and budgets

| Name | Default | Minimum | Maximum |
| --- | --- | --- | --- |
| `max_mapping_bytes` | 65536 | 1 | 1048576 |
| `max_lock_bytes` | 33554432 | 1 | 268435456 |
| `max_package_snapshot_bytes` | 67108864 | 1 | 268435456 |
| `max_overlay_bytes` | 67108864 | 1 | 268435456 |
| `max_references` | 100000 | 1 | 1000000 |
| `max_results` | 1000 | 1 | 100000 |
| `max_links_examined` | 100000 | 1 | 1000000 |
| `max_boundaries` | 1000 | 1 | 100000 |

A value that is not an integer, a boolean or out of range is a usage error. The limits reject; they
are never part of the output.

## Rejections

Every closed reason is one of: `mapping_too_large`, `invalid_mapping`, `unsupported_mapping_version`, `repository_not_allowlisted`, `same_repository`, `lock_too_large`, `lock_digest_mismatch`, `lock_invalid`, `unsupported_lock_profile`, `package_not_in_lock`, `unresolved_lock_variant`, `unsupported_source_kind`, `no_locked_pure_wheel`, `package_snapshot_too_large`, `package_snapshot_digest_mismatch`, `invalid_package_snapshot`, `previous_overlay_too_large`, `invalid_previous_overlay`, `previous_overlay_unrelated`, `consumer_index_unavailable`, `consumer_source_snapshot_unavailable`, `consumer_index_upgrade_required`, `consumer_snapshot_mismatch`, `invalid_consumer_evidence`, `invalid_provider_evidence`, `reference_limit_exceeded`, `overlay_too_large`, `invalid_overlay`, `overlay_mapping_mismatch`, `stale_revision_vector`, `unknown_seed`. The reasons `lock_invalid`,
`unsupported_lock_profile`, `package_not_in_lock`, `unresolved_lock_variant`,
`unsupported_source_kind` and `no_locked_pure_wheel` pass through unchanged from the S28 lock
reader; the S28 `invalid_snapshot` of the snapshot validator is reported as
`invalid_package_snapshot`. The loader of the overlay, the previous overlay and the package
snapshot rejects duplicate keys, NaN and Infinity, integers of more than 20 digits and
over-deep documents.

## Limitations

Emitted in every overlay: `import_reference_only_no_call_binding`, `module_level_from_imports_only`, `conditional_absolute_imports_not_distinguished`, `reexports_not_followed`, `lock_bound_to_consumer_by_operator_declaration`, `every_wheel_python_member_must_match_provider_source`, `provider_snapshot_must_be_the_active_generation`, `one_consumer_one_provider_one_package`, `impact_depth_one_without_in_repository_expansion`, `repository_ids_are_operator_labels`, `local_shadow_detected_by_observed_paths_only`, `not_written_to_the_graph_and_not_exposed_over_mcp`, `decorated_definitions_link_the_undecorated_declaration`, `repositories_sharing_one_git_common_dir_are_not_distinct`.

Details worth knowing: only module-level absolute `from` imports are linked, and the indexer
cannot tell a conditional absolute import from an unconditional one (an import under
`if TYPE_CHECKING:` or `try:` is a link); re-exports are not followed; the consumer's lock is bound
by declaration; one consumer, one provider and one package per overlay; the overlay is not
written to the graph and is not exposed over MCP.
