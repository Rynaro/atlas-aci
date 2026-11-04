# Evidence reports: one scoped context answer as offline HTML and canonical JSON

`atlas-aci evidence-report` turns one recorded `graph_query op=context` response into two
files you can attach to a review: a self-contained HTML page and a canonical JSON twin. It
is an operator command (like `diff` and `impact`). It is not an MCP tool, it adds nothing to
the tool manifest, and it contacts no server, repository or network.

```bash
atlas-aci evidence-report ENVELOPE --html report.html --json report.json
```

- `ENVELOPE` is a file holding one successful context response, either the text of
  `content[0].text` or the `structuredContent` object. Both are the same envelope: on the wire
  `structuredContent` and `json.loads(content[0].text)` compare equal for every envelope this
  slice produced (default, `references`, path, page, budget-cut and source-free import).
  Pretty-printed or re-escaped copies of the same JSON give the same report bytes.
- `--html` and `--json` are both required and have no defaults. All three paths must be
  different files (a hard link to the envelope counts as the same file); otherwise the command
  exits 2 and writes nothing.
- Both outputs are rendered in memory first and then staged next to their targets and moved
  into place. A rejection writes nothing and leaves existing files untouched.
- stdout is empty. A rejection prints `Error: Evidence report rejected: <code>` on stderr and
  exits 1. An unexpected failure (for example a write error) prints
  `Error: Evidence report failed: <ExceptionType>` and exits 1. Messages never carry a path or
  input text.

## What is accepted

Exactly one shape: an `atlas.graph.v1` envelope with `operation == "context"`,
`status == "ok"` and `data.projection == "task_context"`, produced with the default bounds
(1 MiB canonical response, 200 elements per list).

The checks run in this order, and every failure of a step maps to that step's code (no other
exception type escapes, including recursion, overflow and unicode errors):

| Step | Check | Code |
| --- | --- | --- |
| 1 | The file is at most 4 MiB | `envelope_too_large` |
| 2 | Strict UTF-8 (a BOM fails), JSON without NaN, Infinity, duplicate keys, lone surrogates or non-finite numbers | `invalid_input` |
| 3 | The canonical re-encoding is at most 1 MiB | `envelope_too_large` |
| 4 | The shipped `atlas.graph.v1` schema validator accepts it | `invalid_envelope` |
| 5 | `operation`, `status` and `projection` as above (`continue`, `page`, `impact`, `retrieve` and error envelopes are refused) | `unsupported_operation` |
| 6 | Snapshot scan (below) | `mixed_snapshot_evidence` |
| 7 | The shipped semantic validator `validate_context` with `min(200, bounds.element_limit)` (an `element_limit` above 200 is refused) | `invalid_envelope` |
| 8 | Rendered JSON at most 3 MiB, HTML at most 8 MiB | `report_too_large` |

### Snapshot scan

The envelope declares one `(generation_id, snapshot_id)` pair. The scan requires `data` and every
`data.resources[]` descriptor to carry exactly that pair. Null compares as a value, so a null
declared snapshot next to a non-null resource snapshot is rejected as mixed. A context
envelope carries a per-item pair in no other place: bundles, records and sources bind through
`source_sha256` and through `retrieval_record_id`, which the semantic validator checks next.
A consistent change of the whole snapshot is not a mix; it fails the identity checks as
`invalid_envelope`.

## Omitted fields (credentials)

`data.resources[].uri` carries a lease token and `data.next_cursor` is a continuation
capability. Neither is part of any identity, and neither may travel in a shareable file, so both
are removed from the JSON `envelope` and from the HTML. Each omission is listed:

| Pointer | Reason | Value |
| --- | --- | --- |
| `/data/next_cursor` | `capability_credential` | `redacted` when a cursor existed, `null` otherwise |
| `/data/resources/<i>/uri` | `capability_credential` | `redacted` |

`has_more` stays in the report, so the existence of a continuation remains visible. The stripped
envelope is deliberately not a valid envelope any more: feeding a report back in is
rejected as `invalid_envelope`. Every id (`retrieval_record_id`, resource, source and bundle ids)
recomputes from the report's own `envelope`.

## JSON report (`atlas-evidence-report.v1`)

Canonical JSON (sorted keys, compact, UTF-8) plus one LF. No timestamp, environment or locale
enters it. Top-level keys:

- `report_format` = `atlas-evidence-report.v1`, `rendering_version` = `atlas-evidence-render.1`,
  `options` (`credential_fields: omitted`, `html_excerpts: escaped_text`,
  `control_codepoints: visible_placeholder`).
- `envelope_sha256`: sha256 of the canonical stripped envelope, so it can be re-checked from the
  report and does not change with lease tokens.
- `omitted_fields`: the table above.
- `envelope`: the validated envelope minus the omitted fields, byte-for-byte the input values.
- `view`: copied values for reading, with no arithmetic: `identity` (schema version, operation,
  status, `evidence_status`, generation and snapshot ids, `data.repository`,
  `retrieval_record_id`, `/data/query_digest`, ranker, request text, source policy), `markers`,
  `bundles` (id, kind, rank, source ids, location, hops) and `sources` (deduplicated by id).

## HTML report

A static page: no script, no external asset, no inline event attribute and no form. A
`Content-Security-Policy` meta (`default-src 'none'`) and `referrer: no-referrer` are present as
defence in depth only; the markup is inert by construction (a tag and attribute whitelist test
covers every page and the template). Every dynamic value is escaped text. The page shows
identity, a marker table, the omitted fields, one article per evidence bundle (kind, id, rank,
node identities, definition location, and a hop table with relation, direction, resolution,
`path:line:column-end_line:end_column`, `bytes [start, end)`, `source_sha256` and a link to the
source) and one article per unique source with its excerpt.

- Line and column values are shown exactly as recorded in the envelope.
- A utf8 excerpt is a `<pre>` block; a base64 excerpt is shown as the encoded string and labelled
  "base64, not decoded". A source without an excerpt says "excerpt not included: <reason>".
- Control and bidirectional codepoints (and Unicode tag characters) are replaced by a visible
  `U+XXXX` placeholder. The table is fixed in the code, so the output does not depend on the
  Python Unicode database. The JSON keeps the exact text.

## Markers

Every marker appears in both formats, with the pointer and the input value. A null value is
shown as `none`; nothing is summarised and nothing is turned into a positive claim.

- Envelope: `/evidence_status`, `/bounds/element_limit`, `/bounds/truncated`, `/bounds/lists`.
- Selection: `/data/selection/{total_count,has_more,reason}`, `/data/selection_truncated`,
  `/data/selection_reason`, `/data/has_more`, `/data/total_count`.
- Retrieval and collection: `/data/retrieval/{truncated,truncation_reason,diagnostic_reason}`,
  `/data/collection_stop`.
- Coverage and budget: `/data/coverage/{state,source_policy,excluded_counts}`,
  `/data/effective_limits`, `/data/work`.
- Per frontier: `outcome`, `stop_reason`, `target_state`.
- Per bundle: `rank/source_unavailable`; per source `state`, `reason`, `inline_reason`; per hop
  `assertion/resolution`, `assertion/unresolved_reason`, `assertion/candidate_ids` and
  `source_availability`.

Two truncation shapes exist and look different on purpose:

- A continuation **page** (`continuation=true` and a small `max_bytes`) has `has_more: true`, a
  cursor (omitted as `redacted`) and a null `selection/reason`.
- A **budget cut** (no continuation, a small `max_bytes`) has `selection_truncated: true`,
  `selection_reason: byte_limit` and `has_more: false`.

In `atlas.graph.v1` context, `assertion.unresolved_reason` is always none (the schema fixes it to
null) and a hop's `target_entity_id` is never null. The "unresolved" signals are the `resolution`
value (for example `INFERRED`), `source_availability`, a source `state` of `unavailable` with its
`reason`, `rank/source_unavailable`, and `assertion/candidate_ids`. The `candidate_ids` marker
carries the list as recorded: an empty list means no alternative candidates were recorded, not a
resolved target.

## Limitations

1. Context envelopes only. Impact, page/continue, retrieve and validation-plan inputs are
   rejected as `unsupported_operation` or `invalid_envelope`.
2. Continuation pages are neither merged nor fetched. `has_more` is shown and the cursor is
   omitted.
3. Lease URIs and cursors are omitted; a report cannot fetch more evidence.
4. `data.repository` (an absolute server path) is retained because it is identity-bearing.
   Review it before sharing.
5. Default bounds only: 1 MiB canonical envelope and 200 elements per list. An envelope from a
   server with larger limits is rejected as `envelope_too_large` or `invalid_envelope`.
6. base64 excerpts are not decoded.
7. The snapshot scan covers the envelope, `data` and `resources[]`. Records are bound through
   `retrieval_record_id`.
8. Line and column values are shown as recorded.
9. The CSP meta is defence in depth only.
10. The two outputs are moved into place one after the other; a failure between the two moves
    can leave only the first in place (the command then reports `failed`).

## Frozen fixtures and regeneration

`mcp-server/tests/fixtures/evidence_report/` holds four real envelopes (`inline`,
`path-references`, `truncated`, `hostile`), their golden `.report.json` and `.report.html`, and
`provenance.json` (base commit, the fixed repository path, query arguments, sha256 of every
file, the Python, tree-sitter and tree-sitter-language-pack versions). Snapshot ids bind the
repository path, so the envelopes were produced at the fixed path
`/tmp/atlas-s37-evidence/repo` inside the Linux dev image. The lease tokens inside the envelopes
come from a discarded per-process secret and are inert. The tests only replay the frozen files.

```bash
# inside the dev image (the repository path is fixed)
python tests/slices/test_s37_evidence_report.py --regenerate
```

Regenerating rewrites the envelope files (new random lease tokens) and updates `provenance.json`;
the golden reports do not change, because credentials are omitted from them.
