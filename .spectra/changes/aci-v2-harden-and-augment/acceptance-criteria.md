---
artifact: acceptance-criteria
plan: aci-v2-harden-and-augment
target: atlas-aci v2.0.0
form: EARS (ramza-ears-lint clean)
frozen: see plan-state.json criteria_sha256
amended: refine cycle 1 — applies checker-critique.md F1-F18 + orchestrator DIR-1/2/3
---

# atlas-aci v2.0.0 — Frozen Acceptance Criteria (amended)

Every criterion is mechanically verifiable: it names a test file, command, CI job, grep, or
artefact that decides pass/fail. No criterion requires human judgement. IDs are stable
(`AC-<track>-<n>`). Tracks: H (harden gate), DOC (doc-honesty), A1..A5 (augmentation), REL (D6
release blocker), NEG (thesis-protecting negatives).

Concrete constants used below (F5 + D3a). The **modularity PASS rule** is the D3a pre-registered
rule, evaluated per repo and never averaged: precondition `Louvain_Q_median >= Q_struct`, then pass
iff `LPA_Q >= Q_struct` and `LPA_Q >= R * Louvain_Q_median`. The constants are frozen **before** the
probe runs (anti-circularity: the probe never derives the bar it is graded against): `Q_struct =
0.30` (Newman "meaningful structure exists" floor, used only to reject a non-discriminating graph,
never a quality target for LPA) and `R = 0.85` (maintainer-confirmed midpoint of the principled band
[0.80, 0.90]; 0.95 upper = modularity-degeneracy plateau per Good-de Montjoye-Clauset 2010, 0.80
lower = LPA leaving >20% of provable structure unfound). Louvain baseline = **median** of `K = 10`
networkx `louvain_communities` runs at **seeds 0..9**, resolution **gamma = 1.0**; LPA = the single
deterministic run of the *shipped* implementation (no seed). Both algorithms consume the **confident
subgraph** (`EXTRACTED` union `INFERRED`, per D4a) as an **undirected unweighted projection**.
networkx is used ONLY inside an ephemeral probe-time environment (uvx / throwaway venv), never added
to `mcp-server/pyproject.toml` or `mcp-server/uv.lock`, which is what keeps AC-NEG-2 intact. The
**git-practical export ceiling** is `100 MB = 104857600 bytes` (D6/R6, tilde dropped). The
**reference Rails-scale repos** are pinned by commit SHA (SHAs, not tags, per ATLAS section 5):
`solidusio/solidus@4026945d614e81383c007ed1ab1278a0195ce5d9` and
`spree/spree@6699cde44303ea85ef6e56c5e87c44a738ab73fc` (both BSD-3, public). The rule must PASS on
**both, independently**; either failing cuts A3 to v2.1 (DIR-1). See AC-A3-1/AC-A3-4 and the
[VERIFY] list.

## Track H — Hardening gate (H0-H4)

### AC-H-1 (event-driven)
GIVEN the repository has a CI workflow at .github/workflows/ci.yml
WHEN a pull request is opened or updated against main
THEN the CI workflow SHALL run the pytest suite over mcp-server/tests and report a failing check run when any test fails
VERIFY: ci: .github/workflows/ci.yml triggers on pull_request; a probe PR with a deliberately failing test produces a failing check run (branch-protection enforcement is defence-in-depth, out-of-tree; see AC-NEG-6)

### AC-H-2 (event-driven)
GIVEN the CI workflow is present
WHEN a pull request is opened or updated against main
THEN the CI workflow SHALL run ruff and mypy and report a failing check run when either reports an error
VERIFY: ci: .github/workflows/ci.yml contains ruff + mypy steps gated on pull_request; a probe PR with a lint error produces a failing check run

### AC-H-3 (event-driven)
GIVEN a tool has returned a result inside _call_tool
WHEN the result is about to be serialized to the MCP transport
THEN the central dispatch-middleware SHALL apply the element-cap to the declared list-valued field on an element boundary before applying the serialized-byte ceiling
VERIFY: test: mcp-server/tests/test_server.py::test_central_bounds_applies_element_cap_then_byte_ceiling

### AC-H-4 (event-driven)
GIVEN each registered tool and each graph_query verb is fed a synthetic response whose list-valued field exceeds the element-cap
WHEN it returns through _call_tool
THEN every such response SHALL come back truncated with the un-ignorable flag set, never returned whole
VERIFY: test: mcp-server/tests/test_server.py::test_every_tool_and_verb_truncates_and_flags_over_cap (drives an over-cap fixture per tool/verb; a no-op cap fails this)

### AC-H-5 (event-driven)
GIVEN a tool response exceeds the central element-cap but not the absolute byte-ceiling
WHEN the central middleware truncates it
THEN the returned object SHALL carry an un-ignorable top-level truncated:true with the returned count, more_available:true, plus retry_hint:narrower_scope
VERIFY: test: mcp-server/tests/test_server.py::test_overflow_truncate_and_flag_contract

### AC-H-6 (unwanted-behavior)
GIVEN a single degenerate response cannot satisfy the absolute serialized-byte ceiling even after element truncation
IF the byte-ceiling backstop is breached
THEN the middleware SHALL return a structured error via ToolError instead of an oversized body
VERIFY: test: mcp-server/tests/test_server.py::test_absolute_byte_ceiling_hard_fails

### AC-H-7 (event-driven)
GIVEN a search_symbol call on a common name that resolves to more definitions than the element-cap
WHEN search_symbol returns through _call_tool
THEN the response SHALL be truncated and flagged rather than returning the full unbounded definitions list
VERIFY: test: mcp-server/tests/test_server.py::test_search_symbol_is_bounded (regression for codegraph.py:408-414); in P0 gate_criteria

### AC-H-8 (event-driven)
GIVEN a graph_query callers_of on a hub callee that matches more edges than the element-cap
WHEN graph_query returns through _call_tool
THEN the response SHALL be truncated and flagged rather than returning the full unbounded edges list
VERIFY: test: mcp-server/tests/test_server.py::test_graph_query_is_bounded (regression for codegraph.py:425-431); in P0 gate_criteria

### AC-H-9 (event-driven)
GIVEN a repository is being indexed under v2 semantics
WHEN the DB is opened or created
THEN the path SHALL be .atlas/graph.<epoch>.db where <epoch> is the monotonic schema-epoch integer, never the bare .atlas/graph.db
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_db_path_is_epoch_namespaced

### AC-H-10 (event-driven)
GIVEN one or more .atlas/graph.*.db files exist whose epoch is not the current epoch
WHEN the index (write) command runs
THEN the non-current-epoch DB files SHALL be swept from .atlas by the index path only, never by serve
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_index_sweeps_stale_epoch_files_serve_does_not

### AC-H-11 (unwanted-behavior)
GIVEN an on-disk DB whose filename epoch does not match its in-DB manifest epoch row
IF the index command encounters it
THEN it SHALL be rebuilt, whereas serve encountering it SHALL fail fast with a structured ToolError naming the required index command, writing nothing
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_index_rebuilds_serve_fails_fast_on_epoch_mismatch

### AC-H-12 (ubiquitous)
THEN a committed EXPECTED_DDL_HASH constant paired with the integer epoch SHALL equal the hash of the current schema DDL, so changing the DDL without bumping the epoch and its recorded hash fails CI
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_expected_ddl_hash_matches_current_ddl

### AC-H-13 (ubiquitous)
THEN the set of LANG_BY_EXT values SHALL be a subset of the QUERIES keys unioned with an explicit unsupported-extension allowlist
VERIFY: test: mcp-server/tests/test_codegraph.py::test_lang_by_ext_consistent_with_queries (guards codegraph.py:41-44 vs QUERIES keys)

### AC-H-14 (unwanted-behavior)
GIVEN files with a recognized-but-unsupported extension are present in the indexed tree
IF the indexer skips them
THEN it SHALL emit a visible "unsupported extension skipped: N files" report rather than silently no-op
VERIFY: test: mcp-server/tests/test_codegraph.py::test_unsupported_extension_skip_is_reported

### AC-H-15 (ubiquitous)
THEN every registered tool or graph_query verb that exposes a list-valued field SHALL declare a non-empty registered _bounded_field so the central cap can never be a silent no-op
VERIFY: test: mcp-server/tests/test_server.py::test_every_list_returning_tool_registers_a_bounded_field (registry-completeness; the D2:48 "every tool calls a cap helper" prescription); in P0 gate_criteria

### AC-H-16 (state-driven)
GIVEN serve is started against a read-only .atlas mount matching the README:201-210 --read-only :ro deployment
WHILE the epoch matches
THEN serve SHALL start successfully performing zero writes or unlinks under .atlas, and SHALL fail cleanly with a ToolError naming index when the epoch does not match
VERIFY: ci/test: mcp-server/tests/test_schema_epoch.py::test_serve_read_only_mount_zero_writes plus a CI job running serve against a :ro mount asserting no filesystem writes under .atlas

### AC-H-17 (event-driven)
GIVEN two index processes run concurrently as the documented background post-commit hook can trigger
WHEN each builds the epoch DB
THEN the build SHALL write to a temporary path then atomically rename under a single-writer lock so a concurrent run cannot corrupt the DB
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_concurrent_index_atomic_rename_no_corruption (addresses F17; codegraph.py:214 default locking)

### AC-H-18 (ubiquitous)
THEN for every tool and every graph_query verb a truncation signal SHALL be set if and only if content that both exists and was requested was withheld; and no continuation cursor SHALL point beyond the end of the available content
VERIFY: test: mcp-server/tests/test_server.py::test_truncation_signal_iff_content_withheld parametrizes each tool and verb at cap-1, cap, cap+1, and a request extending past end-of-content, with expected truncation and cursor values computed independently of the implementation under test (pins the F-1/NEW-1 proxy-vs-invariant defect class the P0 pass reproduced and fixed four times)

## Track DOC — Doc-honesty

### AC-DOC-1 (state-driven)
GIVEN atlas-aci v2.0.0 is released
WHILE the per-tool cap tests (AC-H-7, AC-H-8, AC-H-15) pass
THEN the README:411-413 "Mechanical bounds ... applied per call ... narrow bounds never widen" claim SHALL be true for all tools including search_symbol and graph_query
VERIFY: test: AC-H-7/AC-H-8/AC-H-15 green; grep: README.md:411-413 retains the bounds invariant with no per-tool exception (anchor corrected from the scout's wrong 107-111 per F6)

### AC-DOC-2 (ubiquitous)
THEN no file in the repository SHALL describe --since as git-ref diffing, since the implementation keys only on (mtime_ns, size)
VERIFY: grep: `grep -rniE "since <?git-?ref|restricts indexing to files changed since a ref|--since HEAD~[0-9]"` over README.md INTEGRATION.md SETUP.md mcp-server returns zero (covers INTEGRATION.md:201, README.md:173, INTEGRATION.md:208, mcp-server/Dockerfile:64, SETUP.md:238 per F12)

### AC-DOC-3 (ubiquitous)
THEN no file SHALL quote a canary pass-rate for a suite whose dispatcher raises NotImplementedError, and scripts/run-canaries.py SHALL carry a visible deferred-in-v2.0.0 note
VERIFY: grep: canary pass-rate claim absent or flagged aspirational in README.md (348-349) and SETUP.md (182-183, 282) per F13/F15; scripts/run-canaries.py contains an explicit deferred note

### AC-DOC-4 (ubiquitous)
THEN the Prism Ruby specialist-mode references SHALL be removed from codegraph.py, SETUP.md, INTEGRATION.md, and pyproject.toml
VERIFY: grep: `grep -rni prism` over source and docs returns zero matches, or only an explicit "not shipped" note (catches SETUP.md:120 fifth site per F-C7)

### AC-DOC-5 (ubiquitous)
THEN a top-level LICENSE file SHALL exist declaring Apache-2.0 as promised in README:462-463
VERIFY: cmd: test -f LICENSE; grep: LICENSE contains the Apache License 2.0 identifier

### AC-DOC-6 (ubiquitous)
THEN the search_symbol MCP kind enum SHALL be a superset of the kinds the indexer actually produces
VERIFY: test: mcp-server/tests/test_server.py::test_search_symbol_kind_enum_superset_of_produced_kinds (server.py:97-101 vs codegraph.py:104-134)

### AC-DOC-7 (ubiquitous)
THEN this repository's CLAUDE.md SHALL NOT reference a .atlas/symbols.db artifact that the code never creates
VERIFY: grep: CLAUDE.md contains no "symbols.db"; only .atlas/graph.<epoch>.db is named

### AC-DOC-8 (ubiquitous)
THEN the README repository-layout section SHALL list mcp-server/tests/test_codegraph.py
VERIFY: grep: README.md repository-layout block (near README.md:384) includes test_codegraph.py

### AC-DOC-9 (ubiquitous)
THEN the server.py tool-manifest memex description SHALL NOT claim refs are returned by other tools while no tool emits a memex ref
VERIFY: grep/test: server.py:139 description corrected; or a test asserting at least one tool emits a memex:// ref

### AC-DOC-10 (ubiquitous)
THEN the mcp-server/README.md tools table SHALL NOT describe search_symbol as unbounded nor graph_query as implementation-defined once the central bound lands
VERIFY: grep: `grep -niE "unbounded \(cheap\)|implementation-defined" mcp-server/README.md` returns zero (corrects mcp-server/README.md:34-35 per F11)

## Track A1 — Materialized edges + confidence enum

### AC-A1-1 (ubiquitous)
THEN the v2-epoch schema SHALL include a materialized call/inheritance edge table carrying source, target, relation type, confidence, and candidate set
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_edges_table_present_v2 (edges table exists under the v2 epoch)

### AC-A1-2 (ubiquitous)
THEN every materialized call/inheritance edge SHALL carry a confidence value drawn from exactly the set {EXTRACTED, INFERRED, AMBIGUOUS}
VERIFY: test: mcp-server/tests/test_confidence.py::test_every_call_inheritance_edge_confidence_in_closed_enum (scoped to call/inheritance edges only per F9; rationale_for edges are excluded, see AC-A4-6; PRESERVED under D4a: AMBIGUOUS edges stay in the table and are still returned by graph_query, the analysis-graph exclusion is an analysis-time filter, not a drop)

### AC-A1-3 (event-driven)
GIVEN a reference resolves to exactly one candidate that is syntactically type-qualified per the per-language rule pinned in spec.md D4
WHEN the edge is emitted
THEN its confidence SHALL be EXTRACTED
VERIFY: test: mcp-server/tests/test_confidence.py::test_single_type_qualified_is_extracted (per-language fixtures per AC-A1-11)

### AC-A1-4 (event-driven)
GIVEN a reference resolves to exactly one candidate via heuristic that is not type-qualified
WHEN the edge is emitted
THEN its confidence SHALL be INFERRED
VERIFY: test: mcp-server/tests/test_confidence.py::test_single_heuristic_is_inferred

### AC-A1-5 (event-driven)
GIVEN a reference resolves to more than one candidate
WHEN the edge is emitted
THEN it SHALL be tagged AMBIGUOUS with the full ordered candidates[] attached, never dropped
VERIFY: test: mcp-server/tests/test_confidence.py::test_multi_candidate_is_ambiguous_with_candidates (PRESERVED under D4a: AMBIGUOUS is never dropped from the edge table or graph_query; D4a only excludes it from the A2/A3/probe analysis graph)

### AC-A1-6 (unwanted-behavior)
GIVEN a reference resolves to zero candidates
IF no target can be found
THEN no edge SHALL be emitted and the reference SHALL remain an unresolved name, never assigned an enum value
VERIFY: test: mcp-server/tests/test_confidence.py::test_zero_candidates_emits_no_edge

### AC-A1-7 (event-driven)
GIVEN the v2 QUERIES capture superclass/heritage for Ruby, Python, and JS/TS
WHEN subclasses_of is called for a class with known subclasses
THEN it SHALL return real inheritance edges rather than the empty stub-with-warning
VERIFY: test: mcp-server/tests/test_graph_query.py::test_subclasses_of_returns_real_edges (retires codegraph.py:454-460)

### AC-A1-8 (ubiquitous)
THEN the candidates[] of any AMBIGUOUS edge SHALL be emitted in a fixed total order for identical input
VERIFY: test: mcp-server/tests/test_confidence.py::test_candidates_total_order_stable (same-machine; foundation for D6)

### AC-A1-9 (ubiquitous)
THEN the v2-epoch schema SHALL omit the legacy always-NULL refs.enclosing column, and caller context SHALL be carried by the materialized edge source endpoint instead
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_refs_enclosing_dropped_no_always_null_column (resolves F10 by DROP; codegraph.py:391-397)

### AC-A1-10 (event-driven)
GIVEN refs.enclosing is dropped from the v2 schema
WHEN callers_of returns edges
THEN each edge SHALL carry caller context from the edge source endpoint, the graph_query DSL documentation SHALL reflect the changed response shape, and it SHALL document the analysis-graph divergence whereby graph_query returns all matching edges including AMBIGUOUS while god_nodes and communities analyze only the confident subgraph
VERIFY: test: mcp-server/tests/test_graph_query.py::test_callers_of_caller_context_from_edge_source; grep: DSL doc updated for the F10 response-shape change and the D4a divergence (graph_query returns AMBIGUOUS; god_nodes/communities exclude it)

### AC-A1-11 (ubiquitous)
THEN spec.md D4 SHALL pin the syntactic type-qualified rule for each shipped language, and per-language fixtures SHALL decide EXTRACTED versus INFERRED accordingly
VERIFY: test: mcp-server/tests/test_confidence.py::test_type_qualified_rule_per_language (Ruby/Python/JS-TS fixtures per the pinned rule; resolves F18)

## Track A2 — God nodes

### AC-A2-1 (event-driven)
GIVEN a materialized v2 edge table
WHEN god nodes are requested
THEN the result SHALL be the degree-centrality ranking computed over the confident subgraph (EXTRACTED union INFERRED, with AMBIGUOUS edges excluded from degree) in a fixed deterministic order for identical input
VERIFY: test: mcp-server/tests/test_graph_query.py::test_god_nodes_degree_centrality_deterministic (degree computed over confident edges only per D4a)

### AC-A2-2 (state-driven)
GIVEN god-node ranking is computed
WHILE it consumes only edge counts from the edge table
THEN it SHALL require no graph-algorithm runtime dependency
VERIFY: test/grep: god-node code imports no networkx/igraph/graspologic; test_graph_query.py::test_god_nodes_uses_only_edge_counts

### AC-A2-3 (ubiquitous)
THEN the god_nodes and communities responses SHALL carry analysis_basis set to "confident_edges", ambiguous_edges_excluded as a count, and resolved_edge_count, making the analysis-graph-versus-returned-edges divergence visible
VERIFY: test: mcp-server/tests/test_graph_query.py::test_analysis_basis_fields_present (god_nodes and communities responses expose analysis_basis, ambiguous_edges_excluded, resolved_edge_count per D4a)

## Track A3 — LPA communities

### AC-A3-1 (unwanted-behavior)
GIVEN the D3 decision is flagged at 0.65 confidence
IF any A3 community source path is modified in a commit that does not also contain the probe verdict artefact
THEN the harden-gate CI job SHALL fail so A3 code cannot land before the LPA-vs-Louvain modularity probe and its verdict are recorded
VERIFY: ci: .github/workflows/harden-gate.yml fails when an A3 path changes without .spectra/changes/aci-v2-harden-and-augment/probe-lpa-vs-louvain.md present (mechanical ordering per F7). The artefact SHALL record, per repo (Solidus and Spree): the K=10 networkx louvain_communities best/worst/median/mean+/-sd with seeds 0..9 and gamma=1.0; the single deterministic LPA_Q from the shipped implementation; the confident-subgraph construction (D4a); the three-clause evaluation; and the PASS/CUT verdict. The check SHALL also assert the recorded verdict equals the mechanical evaluation of the recorded numbers (a maker cannot record failing numbers under a PASS label, resolving F7). networkx runs ONLY in an ephemeral probe-time environment (uvx or a throwaway venv), never in mcp-server/pyproject.toml or mcp-server/uv.lock, which is what preserves AC-NEG-2 (running `uv add networkx` would detonate AC-NEG-2)

### AC-A3-2 (event-driven)
GIVEN a fixed v2 edge table projected to the confident subgraph (EXTRACTED union INFERRED, AMBIGUOUS excluded)
WHEN LPA community detection runs twice on identical input
THEN it SHALL assign identical community memberships with identical total-ordered community IDs
VERIFY: test: mcp-server/tests/test_communities.py::test_lpa_deterministic_total_order (same-machine; the D3a probe consumes this shipped deterministic LPA over the confident subgraph, single run, no seed)

### AC-A3-3 (ubiquitous)
THEN community detection SHALL be computed with a hand-rolled label-propagation implementation over the confident subgraph (EXTRACTED union INFERRED, AMBIGUOUS never fanned out) that adds no new runtime dependency
VERIFY: test/grep: mcp-server/tests/test_communities.py::test_no_new_dependency; community module imports no networkx and operates only on confident edges (D4a)

### AC-A3-4 (unwanted-behavior)
GIVEN the probe evaluates the three-clause rule per repo on Solidus and Spree independently and never averaged, all of: (i) Louvain_Q_median >= 0.30 as a precondition, (ii) LPA_Q >= 0.30, (iii) LPA_Q >= 0.85 * Louvain_Q_median, where Louvain_Q_median is the median of K=10 networkx louvain_communities runs at seeds 0..9 and gamma=1.0, LPA_Q is the single deterministic run of the shipped LPA, both over the confident subgraph (EXTRACTED union INFERRED) as an undirected unweighted projection
IF either pinned repo fails any of the three clauses
THEN A3 communities SHALL be cut from v2.0.0 and deferred to v2.1, shipping A2 god nodes only, never adopting networkx or Louvain as a dependency
VERIFY: artefact: probe-lpa-vs-louvain.md records the per-repo numeric comparison and the resulting proceed-LPA-or-cut-to-v2.1 verdict; a source-verified-structured repo failing clause (i) is a construction FAIL that cuts A3 (not a repo swap); networkx runs only in a probe-time scratch venv, preserving AC-NEG-2 (DIR-1: networkx flip deleted; AC-NEG-2 stays absolute; SHAs solidusio/solidus@4026945d614e81383c007ed1ab1278a0195ce5d9 and spree/spree@6699cde44303ea85ef6e56c5e87c44a738ab73fc)

### AC-A3-5 (unwanted-behavior)
GIVEN v2.0.0 is assembled for release
IF any A3 community code exists in the tree while the probe verdict artefact does not record PASS on both pinned repos
THEN the harden-gate CI job SHALL fail the build
VERIFY: ci: .github/workflows/harden-gate.yml asserts (probe verdict == PASS, i.e. AC-A3-4's three-clause rule holds independently on both Solidus and Spree) OR (no communities module/verb present); mechanical cut-branch per DIR-1

## Track A4 — Rationale nodes

### AC-A4-1 (event-driven)
GIVEN a Ruby source file containing a recognized rationale-prefixed comment
WHEN it is indexed
THEN a rationale node SHALL be created for that comment with a rationale_for edge to its enclosing scope
VERIFY: test: mcp-server/tests/test_rationale.py::test_ruby_rationale_node_and_edge

### AC-A4-2 (event-driven)
GIVEN a Python or JS/TS source file containing a recognized rationale-prefixed comment
WHEN it is indexed
THEN a rationale node SHALL be created for that comment, ported from the graphify prefix set
VERIFY: test: mcp-server/tests/test_rationale.py::test_python_and_jsts_rationale_nodes

### AC-A4-3 (event-driven)
GIVEN a JS/TS comment referencing an ADR or RFC identifier
WHEN it is indexed
THEN the rationale node SHALL carry the canonicalized ADR/RFC label
VERIFY: test: mcp-server/tests/test_rationale.py::test_jsts_adr_rfc_promotion (ports extract.py:1087)

### AC-A4-4 (ubiquitous)
THEN rationale nodes SHALL be excluded from cross-file symbol resolution so they never form false symbol links
VERIFY: test: mcp-server/tests/test_rationale.py::test_rationale_excluded_from_resolution

### AC-A4-5 (unwanted-behavior)
GIVEN scss, html, yaml, markdown, or bash files are indexed
IF they contain comment-like content
THEN no rationale node SHALL be created for those markup/config languages
VERIFY: test: mcp-server/tests/test_rationale.py::test_no_rationale_for_markup_config_langs

### AC-A4-6 (ubiquitous)
THEN rationale_for edges SHALL live in a separate rationale relation defined by the H3 epoch substrate DDL, carrying no confidence enum value, so A4 depends only on H3 and not on A1 edge resolution
VERIFY: test: mcp-server/tests/test_schema_epoch.py::test_rationale_relation_separate_no_confidence (resolves F9; edge-store folded into H3)

## Track A5 — Deterministic export/import

### AC-A5-1 (event-driven)
GIVEN a built v2 graph
WHEN it is exported
THEN the output SHALL be canonical JSONL with one record per line, sorted keys, fixed float formatting, and LF line endings
VERIFY: test: mcp-server/tests/test_export.py::test_canonical_jsonl_shape

### AC-A5-2 (event-driven)
GIVEN a graph exported from a repository
WHEN the export is written
THEN all path keys SHALL be repository-relative so the artefact re-anchors on load
VERIFY: test: mcp-server/tests/test_export.py::test_relative_path_keys_reanchor

### AC-A5-3 (ubiquitous)
THEN two exports of an identical source tree on the same machine SHALL be byte-identical
VERIFY: test: mcp-server/tests/test_export.py::test_export_byte_identical_for_identical_input

### AC-A5-4 (event-driven)
GIVEN a canonical JSONL export
WHEN it is imported on a cold start
THEN the importer SHALL reproduce a valid current-epoch DB that passes an integrity check, idempotently on repeat import
VERIFY: test: mcp-server/tests/test_export.py::test_import_roundtrip_idempotent

### AC-A5-5 (ubiquitous)
THEN v2.0.0 SHALL ship no graph or union merge driver, while a trivial regenerate-on-conflict driver is permitted, and the documented conflict workflow SHALL be to regenerate from source
VERIFY: grep/test: no nx.compose-style graph/union merge shipped; a regenerate-on-conflict driver is allowed even if it registers git config merge.<x>.driver (narrowed per the AC-A5-5 nit)

### AC-A5-6 (event-driven)
GIVEN a JSONL export
WHEN the header record is read
THEN it SHALL carry the schema-epoch and a content hash of the export body
VERIFY: test: mcp-server/tests/test_export.py::test_header_record_epoch_and_content_hash

### AC-A5-7 (event-driven)
GIVEN the same graph is built with records inserted in a shuffled order
WHEN it is exported
THEN the exporter SHALL emit records in an explicit canonical order independent of rowid or insertion order
VERIFY: test: mcp-server/tests/test_export.py::test_record_level_canonical_order_independent_of_rowid (ORDER BY type,path,line,name,...; resolves F8, same-machine)

### AC-A5-8 (ubiquitous)
THEN source-file iteration SHALL be deterministically sorted rather than filesystem-order rglob
VERIFY: test: mcp-server/tests/test_codegraph.py::test_source_file_iteration_sorted (fixes codegraph.py:336 unsorted rglob; foundation for byte-determinism per F8)

## Track REL — D6 cross-platform release blocker

### AC-REL-1 (unwanted-behavior)
GIVEN the same source tree is exported on macOS and on Linux in the CI OS matrix
IF the two exports are not byte-identical
THEN the CI OS-matrix job SHALL fail so the release cannot proceed until cross-platform byte-determinism is restored
VERIFY: ci: .github/workflows/ci.yml OS-matrix job diffs the two exports; a mismatch fails the job (release blocking is the job plus operator branch protection as defence-in-depth)

### AC-REL-2 (event-driven)
GIVEN the larger pinned reference repo (Spree @ 6699cde44303ea85ef6e56c5e87c44a738ab73fc) is exported once at P3
WHEN the export completes
THEN its size SHALL be recorded into the probe or a sibling artefact and asserted below 104857600 bytes
VERIFY: artefact: export size is measured once at P3/release-prep on Spree (the larger repo; owner vivi per V4), recorded into probe-lpa-vs-louvain.md or a sibling export-size artefact, and CI asserts the recorded number against the 100 MB ceiling rather than re-cloning per PR (matches AC-A3-1's measure-once presence pattern; export includes AMBIGUOUS edges, so D4a does not shrink it; tilde dropped per F5)

### AC-REL-3 (ubiquitous)
THEN the atlas-aci version pinned in mcp-server/uv.lock SHALL equal the version in mcp-server/pyproject.toml
VERIFY: ci/test: a check asserts the atlas-aci stanza version in mcp-server/uv.lock equals pyproject.toml version (guards the shipped-v0.4.0 defect: git show HEAD:mcp-server/uv.lock:37 = 0.3.1 vs pyproject.toml:3 = 0.4.0)

## Track NEG — Thesis-protecting negatives

### AC-NEG-1 (ubiquitous)
THEN no LLM client SHALL be importable from the atlas_aci server package
VERIFY: test/grep: `grep -rn "import anthropic\|import openai\|import boto3\|from anthropic\|from openai" mcp-server/src` returns zero; test_no_llm_import asserts ImportError-free absence

### AC-NEG-2 (ubiquitous)
THEN networkx SHALL NOT appear anywhere in the resolved dependency tree, absolutely and unconditionally
VERIFY: cmd: `grep -i networkx mcp-server/pyproject.toml mcp-server/uv.lock` returns zero matches (DIR-1: no D3 flip can change this; a below-threshold probe cuts A3, it does not add networkx). NOTE: the D3a probe computes the Louvain baseline WITH networkx, but only inside an ephemeral throwaway environment (uvx or a scratch venv) that never touches pyproject.toml or uv.lock; a `uv add networkx` would detonate this criterion, so the probe MUST keep networkx out of the package deps and lockfile

### AC-NEG-3 (ubiquitous)
THEN AMBIGUOUS SHALL be produced only by the deterministic candidate-count-greater-than-one rule, never by any LLM path
VERIFY: test/grep: no LLM producer of AMBIGUOUS exists; test_confidence.py::test_ambiguous_producer_is_deterministic_only

### AC-NEG-4 (ubiquitous)
THEN the migration path SHALL contain no ALTER TABLE ladder
VERIFY: grep: `grep -rni "alter table" mcp-server/src` returns zero matches

### AC-NEG-5 (ubiquitous)
THEN v2.0.0 SHALL add no new runtime dependency beyond the v0.4.0 core set
VERIFY: cmd: diff of mcp-server/pyproject.toml core dependencies shows only the version bump, no added runtime package (the [dev] extras already ship pytest/ruff/mypy, so H0 adds none)

### AC-NEG-6 (unwanted-behavior)
GIVEN the committed harden-gate workflow at .github/workflows/harden-gate.yml
IF an augmentation A1-A5 path is modified while any hardening check is absent or failing
THEN the harden-gate job SHALL fail the build regardless of branch-protection settings
VERIFY: ci: .github/workflows/harden-gate.yml is the in-tree mechanism (DIR-3); a probe augmentation PR against a red gate fails the job; branch protection is documented defence-in-depth only

### AC-NEG-7 (ubiquitous)
THEN AMBIGUOUS edges SHALL NOT contribute to degree-centrality god-node ranking nor to community membership
VERIFY: test/grep: mcp-server/tests/test_confidence.py::test_ambiguous_excluded_from_analysis_graph asserts no ambiguity-as-importance (no fan-out to candidates, no fractional-weight injection); god_nodes and communities consume only the confident subgraph (EXTRACTED union INFERRED) per D4a
