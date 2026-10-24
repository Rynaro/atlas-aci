# Evidence graph exchange v1 (schema epoch7)

`CodeGraph.evidence_records()` returns canonical entity, occurrence, producer,
evidence and directed assertion records in type/ID order. IDs use SHA-256 over
sorted-key UTF-8 JSON excluding `id`, prefixed by `atlas-evidence.v1:<type>\0`.
Closed fields/enums are defined in `storage/evidence.py`; unknown fields/types,
ID mismatches and broken links fail validation before publication.

Entities key language, normalized relative path, semantic kind/name, structural
owner chain and AST-order matching-sibling ordinal. No byte/line, source hash,
SQLite ID, timestamp or absolute path enters an entity ID. Blank-line moves
preserve IDs; inserting/reordering indistinguishable duplicates may change their
ordinals. Anonymous relevant lexical owners contribute kind/ordinal. File-level
relations use explicit file entities. This is not arbitrary edit tracking.

Occurrences identify exact consumed source SHA-256/size and half-open AST byte
span, derived display location and syntactic role. Evidence binds the entire
provenance tuple to its original producer. Assertions retain directed source,
nullable target, relation, evidence, resolution/candidates, raw target and stable
unresolved reason. Different sites/producers/types never collapse by endpoint pair.

The closed current JSONL header has type,format,schema_epoch,content_hash and
record_count. Canonical records and projection records follow in type/ID order.
Projections carry exactly the named legacy query columns without rowids or
file-cache diagnostics, plus canonical links. Import verifies provenance and
projection coherence. Declared source sizes/internal hashes do not independently
authenticate absent source bytes.

Known epoch6 graphs remain readable without filesystem writes. Writer conversion
stages epoch7 and preserves graph.6.db. Missing legacy owners/spans/producers are
explicitly legacy_unavailable; genuinely known manifest hashes remain known.
Legacy entity identity_basis=legacy_projection does not assert continuity with a
later parse. Current imports preserve original evidence producers; importer and
converter provenance stays in generation metadata. Source-free destination
completeness stays unavailable. Malformed present epoch7 never falls back to6.

Python import binding is limited to unaliased package-local relative imports with
real indexed package markers and one module/root declaration. Aliases, stars,
external/dynamic imports, ambiguous modules and shadowed bindings stay unresolved.
Every producer remains structural; compiler semantic binding is unsupported.
