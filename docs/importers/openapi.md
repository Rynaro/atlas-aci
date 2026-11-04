# OpenAPI 3.1 contract import and contract diff (ACI-S29)

Two operator commands, CLI only. They are **not** MCP tools: the server still exposes exactly
seven read-only tools and the tools JSON is unchanged.

```sh
atlas-aci openapi-snapshot DOCUMENT [limits]
atlas-aci openapi-diff --before FILE --after FILE [--max-findings N] [limits]
```

Both commands are read-only. They read the named files, print canonical JSON plus one newline on
stdout and write nothing. Exit 0 means the import or comparison ran. It does not mean "nothing
changed" and it is not a judgement of the API: there is no global verdict anywhere in the output.

## What this is, and what it is not

- **A contract snapshot, not a source snapshot.** The snapshot (`atlas-contract-snapshot.v1`) is
  built from one OpenAPI document's bytes. It is a *contract snapshot* and it is **not a source
  snapshot** and not a graph snapshot. It does not touch the code graph, the index, the evidence
  store or the repository.
- **`snapshot_id` is a contract identity.** It is
  `sha256("atlas-contract-snapshot.v1:" + canonical({artifact_sha256, profile, options}))`: it
  binds the artifact bytes, the profile and the options. It is not an importer version, and a
  different `--max-ref-depth` or a one-byte whitespace edit changes it.
- **Provenance is artifact digest plus JSON pointer.** Every imported element carries
  `artifact_sha256`, `snapshot_id` and an RFC 6901 `pointer` into the artifact. There is no
  source-snapshot provenance: binding a contract snapshot to a source snapshot of a repository is
  a follow-up and is not done here.
- **S19 is not invoked; only its change-set shape is reused.** Approach step 4 of the issue asks
  for the two snapshots to be compared with S19. This slice interprets that as reusing the S19 change-set
  contract by shape and vocabulary: a format string, a content `id` of the form
  `sha256(FORMAT + ":" + canonical(rest))`, `before`/`after` references, a `summary` by kind,
  `limitations`, canonical bytes plus one newline, and a stable sort. `atlas_aci.query.diff` is
  never called, because it works on graph snapshots and a graph-backed contract kind would change
  the producer fingerprint of every pinned graph. The same interpretation applies to the "snapshot
  identity" of the provenance requirement: it is the contract identity above.

## Profile `atlas-openapi31-profile.v1`

Accepted: one JSON document whose `openapi` string is exactly `3.1.<digits>` (ASCII digits, full
match: `3.1.0\n` and non-ASCII digits are rejected). Rejected as a whole: Swagger 2.0, 3.0.x,
3.2.x, `3.1`, prereleases such as `3.1.0-rc1`, a non-string `openapi`, YAML. Only same-document
`#/...` references are followed.

### Whole-document rejections (`REJECTIONS`)

| Reason | When |
| --- | --- |
| `too_large` | the artifact exceeds `max_document_bytes` |
| `invalid_json` | BOM, invalid UTF-8, trailing data, duplicate keys, `NaN`/`Infinity`, a number that overflows to infinity, an integer token over 4300 digits, a lone surrogate, or any non-JSON text (YAML) |
| `nesting_too_deep` | bracket depth outside strings exceeds `MAX_NESTING_DEPTH` (64) |
| `not_an_object` | the root is not an object |
| `root_reference` | the root has a `$ref` |
| `unsupported_profile` | not `3.1.<digits>`, or a `swagger` key |
| `invalid_document` | `paths`, `components` or `webhooks` is not an object, or `jsonSchemaDialect` is not a string |
| `limit_exceeded` | more path keys or operations than `max_operations`, or more parameters than `max_parameters_per_operation`: the import fails closed instead of truncating, because a truncated list would fake removals |

`MAX_NESTING_DEPTH` is a profile constant (64), checked by a byte scan before parsing, so it never
depends on the interpreter's recursion limit. Every walk is iterative.

Number handling: integers and floats are different values (`1` and `1.0` differ and give a review).
Decimal literals are parsed to binary floats, so two distinct decimal spellings of the same double
collapse to one value. This is a limitation, listed below.

## Identity

An operation is identified by its **uppercase HTTP method** and its **path template with every
`{name}` replaced by `{}`**. `operationId` is metadata only: absent or repeated ids never merge or
split operations. There is no trailing-slash, case or percent normalisation. Method keys must be
lowercase (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`); an uppercase `GET`
key is an unknown key (region `other`). When two operations of one document share an identity (for
example `/x/{a}` and `/x/{b}`), every one of them is marked incomplete with
`ambiguous_identity`, and the diff reports only `ambiguous_identity` for that identity.

`x-*` keys in `paths` and in `components` are skipped.

## References

Chains are followed iteratively at path item, parameter, `requestBody` and `responses/<code>`
positions. Every other string `$ref` is probed one step and, because a referenced component can
itself hold an unresolved reference, the probe also walks the resolved targets (cached, so a
recursive or shared schema is walked once); an unresolved or remote reference inside a referenced
component marks the use-site region incomplete. Edits **inside** `components/requestBodies/X` (and
every other component) surface as `document_region_changed`, not as an operation finding.

Diagnostic kind `unresolved_reference` carries one of the reasons: `remote`, `outside_document`,
`target_missing`, `cyclic_reference`, `reference_depth_exceeded`, `reference_expansion_limit`. A
revisit in a chain is reported as `cyclic_reference` at the first revisit (it can never end, so it
is beyond any bound); a chain, or a cycle longer than `max_ref_depth`, is
`reference_depth_exceeded`. The failing `$ref` member is the provenance pointer and the failing
string is `ref`. A fragment is percent-decoded then read as an RFC 6901 pointer; `%G1` or invalid
UTF-8 is `target_missing`; `#`, `#Anchor` and relative files are `outside_document`.

Other diagnostics (`DIAGNOSTIC_REASONS`): `invalid_path_item` (`not_an_object`,
`reference_with_siblings`), `invalid_path_template` (`malformed_template`), `invalid_operation`
(`not_an_object`), `invalid_parameter` (`not_an_array`, `not_an_object`, `missing_name`,
`invalid_location`, `invalid_required`, `path_parameter_not_required`,
`path_parameter_not_in_template`, `duplicate_parameter`) and `ambiguous_identity`
(`normalized_identity_collision`). A region with a diagnostic is `incomplete`; diagnostics beyond
`max_diagnostics` are counted in `diagnostics_total` but not listed, and the statuses still carry
the incompleteness.

## Effective parameters

Operation-level parameters override path-item parameters on (`in`, `name`); header names are
case-insensitive and are lowercased inside the digests. A `path` parameter is keyed by the ordinal
of its variable in the template, so renaming a path variable is `path_parameter_changed`, not a
removal plus an addition. The accompanying `$ref` siblings other than `summary`, `description` and
`x-*` are folded into the use-site digest. The headers `accept`, `content-type` and
`authorization` are `reserved_header` (the spec says to ignore them): a required reserved header
is a review only.

## Regions and digests

Each operation has the regions `parameters`, `requestBody`, `responses`, `callbacks`, `security`,
`operationId`, `deprecated`, `servers` and `other`; findings may also name the regions
`path_item`, `operation` and `document`. A region record is `{sha256, status, pointers}`: its `pointers` inherit the enclosing operation's
provenance (the same artifact digest and snapshot id) and each resolves in the artifact. Digests
are SHA-256 of the canonical JSON of the verbatim
value, so a schema is compared by digest, not by implication: a `description` property inside a
schema still counts.

Ignored fields (`IGNORED_FIELDS`, also listed in the diff `options`): `document:openapi`,
`document:info`, `document:tags`, `document:externalDocs`, `document:x-*`, `components:x-*`,
`paths:x-*`, `path_item:summary`, `path_item:description`, `path_item:x-*`, `operation:summary`,
`operation:description`, `operation:tags`, `operation:externalDocs`, `operation:x-*`,
`parameter:description`, `parameter:x-*`.

## Diff `atlas-contract-diff.v1`

Rules `atlas-openapi-diff-rules.v1`. Finding kinds (`FINDING_KINDS`):

- `operation_removed`: the identity is in `before` only and the `after` path item is complete.
  Otherwise it is `review_required` with `blocked_finding`.
- `newly_required_parameter` (`change` is `added_required` or `optional_to_required`): a
  non-path, non-reserved parameter that is required in `after` and absent or optional in `before`.
- `review_required`: every other change, with a closed reason.
- `operation_added`: informational.

Review reasons (`REVIEW_REASONS`): `incomplete_region`, `ambiguous_identity`, `region_changed`,
`parameter_added`, `parameter_removed`, `parameter_changed`, `path_parameter_changed`,
`document_region_changed`. A finding has `before` and/or `after` references
(`artifact_sha256`, `snapshot_id`, `pointer`, `declared_pointer`) that resolve in their own
artifact. The list is sorted; `--max-findings` keeps a prefix, and `findings_total`, `summary` and
`review_reasons` count everything.

An empty finding list means no supported or detectable change; it is not evidence that the API is
unaffected. There is no verdict and no "compatible" key.

## Limits

| Option | Default | Range |
| --- | --- | --- |
| `max_document_bytes` | 8388608 | 1..33554432 |
| `max_operations` | 5000 | 1..20000 |
| `max_parameters_per_operation` | 128 | 1..1024 |
| `max_ref_depth` | 8 | 1..64 |
| `max_ref_expansions` | 100000 | 0..1000000 |
| `max_diagnostics` | 1000 | 0..100000 |
| `max_findings` (diff) | 1000 | 0..100000 |

With the highest bounds a snapshot is large (every operation and parameter is listed); lower the
limits to cap the output.

## Errors

Fixed texts on stderr after `Error: `; stdout stays empty and no path or document text is echoed.

| Situation | Exit | Text |
| --- | --- | --- |
| unreadable file or directory | 1 | `OpenAPI document cannot be read.` (`: before` / `: after` for the diff) |
| import rejection | 1 | `OpenAPI document rejected: <reason>` (` (before)` / ` (after)` for the diff) |
| diff rejection | 1 | `OpenAPI diff rejected: <reason>` |
| other failure | 1 | `OpenAPI import failed: <TypeName>` |
| limit out of range | 2 | `Invalid OpenAPI limit: <name>` |

## Limitations

Emitted on every snapshot and diff:

- `json_documents_only_yaml_not_supported`
- `same_document_json_pointer_references_only`
- `schemas_are_compared_by_digest_not_by_implication`
- `no_global_verdict_is_produced`
- `security_and_authentication_changes_are_review_only`
- `contract_snapshot_is_not_bound_to_a_source_snapshot`
- `operations_are_not_mapped_to_handlers`
- `ignored_fields_are_excluded_from_digests`
- `reference_chains_follow_only_at_path_item_parameter_body_response_positions`
- `document_region_changes_are_not_attributed_to_operations`
- `reserved_header_parameters_are_review_only`
- `ref_like_values_inside_examples_are_classified_conservatively`
- `not_exposed_over_mcp`
- `schema_id_and_anchor_base_uris_are_not_applied`: a `$id` or `$anchor` inside a schema is not
  used to re-base a `#/...` reference.
- `integer_and_float_literals_differ_and_decimals_collapse_to_floats`

Out of scope: OpenAPI 2.0, 3.0 and 3.2, YAML, remote or multi-document references, JSON Schema
implication, auth semantics, handler mapping, client generation, MCP exposure and any
compatible or incompatible judgement.
