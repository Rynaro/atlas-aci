"""Generate atlas/evals/v3/manifest.v1.json (run once at the end of Day 1; the file is then frozen)."""
import importlib.util, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("v3_aggregate", ROOT / "scripts/eval/v3_aggregate.py")
agg = importlib.util.module_from_spec(spec); sys.modules["v3_aggregate"] = agg; spec.loader.exec_module(agg)

fixture_path = "mcp-server/tests/fixtures/s18/useful-spans.v1.json"
fixture = json.loads((ROOT / fixture_path).read_bytes())
tasks = []
for t in fixture["tasks"]:
    terms = []
    for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", t["text"]):
        if term not in terms:
            terms.append(term)
    tasks.append({"id": t["id"], "corpus": "s18-useful-spans", "text": t["text"], "terms": terms})
task_sha = agg.digest(tasks)

P = "tests/slices/"
binding = {
    "test_s02_contract.py": ["test_unrelated_receiver", "test_owned_receiver", "test_shadowed_name",
        "test_cross_language_collision", "test_unknown_alias", "test_binding_replay",
        "test_no_cross_file_module_guess", "test_additional_binding_constructs_fail_closed"],
    "test_s10_contract.py": ["test_call_only_reuses_nonempty_same_language_unit",
        "test_unknown_dependency_fallback", "test_incremental_replay"],
    "test_s10_dependencies.py": ["test_incoherent_outputs_force_full_repair",
        "test_untrusted_witness_full_fallback", "test_fatal_selected_binding_preserves_pinned_g1"],
}
snapshot = {
    "test_s03_contract.py": ["test_active_request_pinned", "test_failed_publish",
        "test_concurrent_readers_keep_their_own_generation", "test_generation_replay",
        "test_consumed_buffer_hash_and_failure"],
    "test_s08_contract.py": ["test_snapshot_source_coherence", "test_active_lease_retained", "test_snapshot_replay"],
    "test_s08_integration.py": ["test_coherently_rehashed_foreign_binding_fails_before_exposure",
        "test_prune_during_unexposed_acquisition_retries_latest"],
    "test_s08_lease_footprint.py": ["test_rehashed_compact_or_raw_range_contradiction"],
    "test_s09_contract.py": ["test_same_stat_changed_content", "test_producer_change"],
    "test_s12_storage.py": ["test_staged_upgrade_preserves_history_and_held_source",
        "test_genuine_epoch6_reader_and_writer_conversion"],
    "test_s17_storage.py": ["test_genuine_held8_upgrade9_overlay_and_replay"],
}
node_ids = [{"id": f"{P}{f}::{n}", "category": cat} for cat, group in (("binding", binding), ("snapshot", snapshot)) for f, names in group.items() for n in names]
assert len(node_ids) == 30

img = "ghcr.io/rynaro/atlas-aci"
dig = "sha256:e2542ef8d569882c560065f0bcaed9ef2a7398e7e61765e25b142a3c93ec7cf7"
cache = ROOT / ".spectra/evidence/aci-s23/v0-grammar-cache.json"
sp = "mcp-server/tests/fixtures/s08/self-source-fa75bec"
manifest = {
 "format": "atlas-eval-manifest.v1",
 "slice": "ACI-S23",
 "scope": {
  "selected": ["legacy retrieval tools (search_symbol, search_text, view_file) on V0, V1 and B",
    "graph_query op=context on V1 (references and inline source policies)",
    "indexing cost counters from CodeGraph.build() and the v2 index_done event",
    "P0 binding and snapshot regression ids", "host and SDK compatibility matrix"],
  "excluded": ["live agent evaluation (no operator budget)",
    "retrieval evaluation of op=retrieve, op=impact, op=diff, op=validation-plan (no labelled corpus)",
    "release tag or deploy"]},
 "corpus": [
  {"id": "s18-useful-spans", "role": "labelled_retrieval", "path": fixture_path,
   "sha256": "07553bd3d51be01a1d522fc59c240bf41ecf312a051c413c609585c60999b507"},
  {"id": "self-source-fa75bec", "role": "cost", "path": sp + ".json.gz",
   "sha256": "dcf0e79f0bfed5b774911ebd9dc9c7d8026e36ee74272e3fc767ec4e37734426",
   "provenance_path": sp + ".provenance.json",
   "provenance_sha256": agg.sha256_bytes((ROOT / (sp + ".provenance.json")).read_bytes()),
   "materialize": "gunzip; for each files[] entry base64-decode to <root>/<path>; verify every sha256 and the file count against provenance files[] (atlas-complete-source-fixture.v1)"},
  {"id": "coverage-project", "role": "cost", "path": "mcp-server/tests/fixtures/coverage/project",
   "tree_sha256": agg.tree_sha256(ROOT / "mcp-server/tests/fixtures/coverage/project")},
 ],
 "tasks": tasks,
 "task_input_sha256": task_sha,
 "budgets": {"unit": "byte_quarter.v1", "tokens": [2000, 8000, 16000], "max_bytes": [8000, 32000, 64000],
   "measure": "result_bytes_repo_normalized.v1", "prefix_rule": "atlas-budget-prefix.v1"},
 "measure": {
  "result_text": "deterministic re-serialisation of the ledger CallToolResult dict: json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False); the stdio_runner ledger stores model_dump(mode='json', by_alias=True, exclude_none=True)",
  "client_length_equals_server_result_bytes": "proved at HEAD on the 24 S18 context calls (3 budgets x 4 tasks x 2 source policies) for the negotiated protocol: len(result_text) == len(result_bytes(result)) with 0 mismatches (.spectra/evidence/aci-s23/qualification.json d_context)",
  "normalization": "repo_root (and the macOS /private alias) is replaced by /repo before measuring; V0 already serves /repo"},
 "variants": [
  {"id": "V0", "name": "pre_v3_atlas", "task_input_sha256": task_sha,
   "server": {"kind": "oci_image", "image": img, "digest": dig, "version": "2.0.0", "network": "none", "pull": "never",
     "grammar_cache": {"declared_input": "inputs.v0_grammar_cache", "mount": "/tmp/.cache", "mode": "ro",
       "why": "the v2.0.0 image does not prefetch tree-sitter grammars; an offline index exits 0 with files_indexed=0"},
     "index_argv": ["{docker}", "run", "--rm", "--pull", "never", "--network", "none", "-v", "{cache}:/tmp/.cache:ro", "-v", "{repo}:/repo", "{image}", "index", "--repo", "/repo"],
     "serve_argv": ["{docker}", "run", "--rm", "-i", "--pull", "never", "--network", "none", "-v", "{cache}:/tmp/.cache:ro", "-v", "{repo}:/repo:ro", "{image}", "serve", "--repo", "/repo", "--memex-root", "/memex"]}},
  {"id": "V1", "name": "v3_atlas", "task_input_sha256": task_sha, "server": {"kind": "checkout", "ref": "HEAD"}},
  {"id": "B", "name": "search_read", "task_input_sha256": task_sha, "server": {"kind": "checkout", "ref": "HEAD"}},
 ],
 "arms": [
  {"id": "V0.legacy", "variant": "V0", "kind": "legacy", "policy": "atlas-legacy-symbol-read.v1"},
  {"id": "V1.legacy", "variant": "V1", "kind": "legacy", "policy": "atlas-legacy-symbol-read.v1"},
  {"id": "V1.context.references", "variant": "V1", "kind": "context", "policy": "atlas-context.v1", "source_policy": "references"},
  {"id": "V1.context.inline", "variant": "V1", "kind": "context", "policy": "atlas-context.v1", "source_policy": "inline_if_complete"},
  {"id": "B.search_read", "variant": "B", "kind": "search_read", "policy": "atlas-search-read.v1"},
 ],
 "not_applicable": [
  {"arm": "V0.context.references", "reason": "op_unavailable_pre_v3"},
  {"arm": "V0.context.inline", "reason": "op_unavailable_pre_v3"}],
 "policies": {
  "legacy": {"max_reads": 8},
  "search_read": {"max_reads": 8, "window_lines": 10, "regex": False, "scope": "."},
  "context": {"schema_version": "atlas.graph.v1", "max_candidates": 32, "max_inline_bytes": 8192}},
 "comparisons": [["V1.legacy", "V0.legacy"], ["V1.context.inline", "B.search_read"], ["V1.context.references", "V1.legacy"]],
 "cost": {
  "categories": ["index.cold_full", "index.warm_full", "index.incremental_noop", "index.refresh_after_edit", "query.retrieval", "query.model"],
  "counters": ["files_indexed", "files_skipped", "files_removed", "symbols", "refs", "edges", "rationale", "extraction_state", "files_failed", "unsupported_skipped", "binding_resolution", "schema_epoch", "extraction_cache"],
  "v0_counters": ["files_indexed", "files_skipped", "files_removed", "symbols", "refs", "edges", "rationale", "schema_epoch", "unsupported_skipped"],
  "corpora": ["s18-useful-spans", "self-source-fa75bec", "coverage-project"],
  "v0_corpora": ["s18-useful-spans"],
  "refresh_edit": {
    "s18-useful-spans": {"path": "app/payments.py", "append_bytes": "\n# atlas-v3-eval refresh\n"},
    "self-source-fa75bec": {"path": "atlas_aci/__init__.py", "append_bytes": "\n# atlas-v3-eval refresh\n"},
    "coverage-project": {"path": "app/calc.py", "append_bytes": "\n# atlas-v3-eval refresh\n"}}},
 "engineering_outcomes": {"live": ["coding_task_success", "model_tokens", "model_cost", "agent_turns"], "live_budget": None, "offline": ["validation_plan_comparison"]},
 "p0": {"count": 30, "node_ids": node_ids,
  "excluded": [
   {"id": P + "test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition", "reason": "known flaky (timing-sensitive FIFO acquisition)"},
   {"id": P + "test_s01_contract.py::test_call_deadline_after_initialization", "reason": "known failure under uid 1000 in the dev container"},
   {"id": P + "test_s*_docker.py", "reason": "env-gated: need a built Docker image"}]},
 "host_matrix": [
  {"dimension": "MCP client SDK", "value": "mcp 1.27.0 (uv.lock)", "status": "tested", "evidence": "release recorder probe and arms; test_s18_stdio"},
  {"dimension": "Protocol (op=context only)", "value": "2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25; unknown -> negotiated", "status": "tested", "evidence": "test_s18_stdio.py:32-35 (CI); this evaluation negotiates one version per server (see probes)"},
  {"dimension": "Transport", "value": "stdio", "status": "tested", "evidence": "server.py:20; all arms"},
  {"dimension": "Transport", "value": "HTTP/SSE", "status": "unsupported", "evidence": "not implemented"},
  {"dimension": "CPython (client and CI)", "value": "3.14", "status": "tested", "evidence": "ci.yml:108"},
  {"dimension": "CPython (recorder runtime)", "value": "3.12 in the atlas-aci-dev-s23-recorder container", "status": "tested", "evidence": "release run (recorder producer, diagnostic)"},
  {"dimension": "CPython (server runtime)", "value": "3.12 in the Docker python:3.12-slim image only, driven by a 3.14 host client", "status": "tested (smoke)", "evidence": "mcp-server/Dockerfile:55; ci.yml S06/S08 docker job"},
  {"dimension": "CPython", "value": "3.11, 3.13", "status": "unverified", "evidence": "requires-python >=3.11 only"},
  {"dimension": "OS", "value": "Linux x86_64", "status": "tested", "evidence": "CI ubuntu"},
  {"dimension": "OS", "value": "macOS", "status": "tested (partial)", "evidence": "CI export-determinism job only"},
  {"dimension": "OS", "value": "Windows", "status": "unverified", "evidence": "no CI or evidence"},
  {"dimension": "Image", "value": "linux/amd64", "status": "tested", "evidence": "CI docker smoke job"},
  {"dimension": "Image", "value": "linux/arm64", "status": "documented", "evidence": "built by release.yml:155, not run in CI (the pre-v3 image is run on arm64 in this evaluation)"},
  {"dimension": "Hosts", "value": "Claude Code, Copilot, Cursor", "status": "documented", "evidence": "hosts/*.md, no automated run"},
  {"dimension": "Canary adapters", "value": "claude-code-api, copilot, cursor", "status": "unsupported", "evidence": "run-canaries.py:342"},
  {"dimension": "Pre-v3 server", "value": "atlas-aci 2.0.0 @ sha256:e2542ef8d569882c560065f0bcaed9ef2a7398e7e61765e25b142a3c93ec7cf7", "status": "tested (this eval)", "evidence": "V0 records"},
 ],
 "evidence_anchors": [
  {"path": ".github/workflows/ci.yml", "line": 108, "token": "3.14"},
  {"path": ".github/workflows/ci.yml", "line": 166, "token": "macos-latest"},
  {"path": ".github/workflows/ci.yml", "line": 426, "token": "S06+S08"},
  {"path": ".github/workflows/release.yml", "line": 155, "token": "linux/arm64"},
  {"path": "mcp-server/Dockerfile", "line": 55, "token": "python:3.12-slim"},
  {"path": "mcp-server/tests/slices/test_s18_stdio.py", "line": 33, "token": "2025-06-18"},
  {"path": "mcp-server/src/atlas_aci/server.py", "line": 20, "token": "stdio_server"},
  {"path": "scripts/run-canaries.py", "line": 342, "token": "NotImplementedError"},
  {"path": "mcp-server/uv.lock", "line": 408, "token": "1.27.0"},
 ],
 "known_failures": [
  {"id": "tests/slices/test_s01_contract.py::test_call_deadline_after_initialization", "kind": "known_failure_uid_1000", "evidence": "fails under uid 1000 in the dev container; passes in CI (qualification and base-invariants.json)"},
  {"id": "tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition", "kind": "known_flaky", "evidence": "timing-sensitive; excluded from the P0 gate"},
 ],
 "inputs": {"v0_grammar_cache": {"path": ".spectra/evidence/aci-s23/v0-grammar-cache.json",
   "sha256": agg.sha256_bytes(cache.read_bytes()), "tree_sha256": json.loads(cache.read_bytes())["tree_sha256"],
   "note": "per-file sha256 manifest of the grammar binaries fetched once, with network, in a separate recorded step; the binaries are not committed"}},
}
agg.validate_manifest(manifest)
out = ROOT / "atlas/evals/v3/manifest.v1.json"
out.write_bytes(agg.canonical(manifest))
print(agg.manifest_digest(manifest), len(manifest["p0"]["node_ids"]), manifest["corpus"][2]["tree_sha256"])
