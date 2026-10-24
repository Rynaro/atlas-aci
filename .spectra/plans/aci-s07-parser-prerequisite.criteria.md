### AC-S07-PARSER-001 (event-driven)
GIVEN both pinned inputs and unchanged default eligibility
WHEN indexing completes source observation
THEN the generation SHALL account for every eligible original path and source hash.
VERIFY: gate: compare 2519 Solidus and 2358 Spree sorted path/hash entries with preserved qualification manifests; no language narrowing, byte transformation or hidden failed path.

### AC-S07-PARSER-002 (event-driven)
GIVEN a real parser returns a recovered tree with syntax errors
WHEN a complete observation generation is published
THEN that file SHALL be reported as failed extraction with explicit partial coverage and no newly extracted facts.
VERIFY: test: real malformed Python/JavaScript/Ruby and pinned SCSS/ERB examples expose observed hashes and error locations; first-build failures remain visible; clean sibling facts remain usable.

### AC-S07-PARSER-003 (event-driven)
GIVEN a previously indexed file now has a recovered syntax error
WHEN its historical facts are retained
THEN every retained fact SHALL preserve its original evidence and be marked stale relative to the new observation.
VERIFY: test: start with actual definitions and a resolved relationship, change bytes to malformed content, assert old fact hash/span/producer and new observed hash differ, query/export/import preserve staleness, and no old fact is rebased onto new bytes.

### AC-S07-PARSER-004 (unwanted-behavior)
GIVEN a graph contains stale or extraction-failed source observations
WHEN resolution or confident graph analysis runs
THEN stale facts SHALL NOT contribute fresh bindings or confident relationships.
VERIFY: test: fresh callers cannot bind a stale target, stale source references are not resolved anew, historical assertions stay identifiable, and confident_edges/community/god-node projections and cached results exclude stale contributions.

### AC-S07-PARSER-005 (unwanted-behavior)
GIVEN source reading, parser execution, extraction, persistence or publication fails fatally
WHEN a replacement build is attempted
THEN the previous completed generation SHALL remain intact and usable.
VERIFY: test: inject real exceptions after partial progress, retain reader G1 across publication failure, compare graph and evidence bytes, prove cleanup and incomplete diagnostics; first fatal build publishes no generation.

### AC-S07-PARSER-006 (event-driven)
GIVEN a failed file is retried or repaired
WHEN full or incremental indexing completes
THEN the generation SHALL recompute its observation and expose coherent deterministic freshness.
VERIFY: test: retry unchanged failed bytes invokes parser, repaired bytes replace stale facts, same-size/same-mtime changes are read, identical observations replay IDs, pinned leases remain coherent, and legacy/import unknowns never become verified source claims.

### AC-S07-PARSER-007 (event-driven)
GIVEN a portable artifact contains historical facts and current observation provenance
WHEN it is imported without source files
THEN known stale associations SHALL survive with destination source verification unavailable.
VERIFY: test: strict closed-record validation rejects contradictory hashes/freshness and dangling history; repeated imports/export are deterministic; legacy artifacts without observations remain explicitly unknown.

### AC-S07-PARSER-008 (state-driven)
GIVEN any product, compatibility or fresh probe gate is failed or unmeasured
THEN ACI-S07 SHALL remain incomplete.
VERIFY: gate: original criteria/identity and historical measurements stay immutable; run original full tests and unchanged fresh same-pin/default producer probe with frozen modularity bar; partial coverage counts are reported beside actual measurements and CUT cannot satisfy readiness.
