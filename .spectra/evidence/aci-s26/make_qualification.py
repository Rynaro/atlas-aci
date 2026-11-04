"""Assemble qualification.json and base-invariants.json from the Day-1 probe outputs."""
import json
from pathlib import Path

root = Path("/ws/.spectra/evidence/aci-s26")
data = root / "qual-data"
g12 = json.loads((data / "grammar-py312.json").read_text())
g14 = json.loads((data / "grammar-py314.json").read_text())
r12 = json.loads((data / "refs-py312.json").read_text())
r14 = json.loads((data / "refs-py314.json").read_text())
validity = json.loads((data / "validity.json").read_text())


def strip_versions(d):
    d = json.loads(json.dumps(d))
    d["facts"].pop("versions", None)
    return d


grammar_equal = strip_versions(g12) == strip_versions(g14)
refs_equal = r12 == r14
sites = [s for s in r12["sites"] if not s["path"].startswith("tmp/")]
skipped = [s for s in r12["sites"] if s["path"].startswith("tmp/")]
defs = r12["definitions"]
qualification = {
    "slice": "ACI-S26",
    "base_commit": "01947584046036ee4328eda03d23787ec7471a30",
    "environments": {
        "dev_image": {
            "image": "atlas-aci-dev-s16",
            "sys.version": g12["facts"]["versions"]["python"],
            "tree-sitter": "0.25.2",
            "tree-sitter-language-pack": "1.6.0",
            "pins_source": "mcp-server/uv.lock (tree-sitter 0.25.2, tree-sitter-language-pack 1.6.0)",
        },
        "python_3_14": {
            "image": "python:3.14 (throwaway container, repo mounted read-only, network allowed for that container only)",
            "sys.version": g14["facts"]["versions"]["python"],
            "tree-sitter": "0.25.2",
            "tree-sitter-language-pack": "1.6.0",
            "install": "pip install tree-sitter==0.25.2 tree-sitter-language-pack==1.6.0 (plus mcp, click, pydantic, structlog for the (b) probe); pinned wheels were available for 3.14",
            "never_on_host": True,
        },
    },
    "a_grammar": {
        "stop_rule": "not triggered",
        "fixture_files": g12["facts"]["fixture_files"],
        "fixture_all_clean_py312": g12["facts"]["fixture_all_clean"],
        "fixture_all_clean_py314": g14["facts"]["fixture_all_clean"],
        "py312_equals_py314": grammar_equal,
        "descendant_for_byte_range": g12["facts"]["descendant_for_byte_range"],
        "descendant_probe": g12["facts"]["descendant_probe"],
        "include_shape": g12["facts"]["include_shape"],
        "snippets": len(g12["snippets"]),
        "all_snippets_clean": not any(v["has_error"] for v in g12["snippets"].values()),
        "findings": [
            "call fields receiver/method/arguments/block as planned; set chain is a call receiver; receiverless has no receiver; no-parens and empty-parens both give an argument_list (bare has none)",
            "argument_list children: splat_argument, hash_splat_argument, block_argument, forward_argument, pair (hash_key_symbol and rocket both pair), hash for a braced hash, heredoc_beginning in the list with heredoc_body a sibling of the statement (never inside the list), comment inside the list",
            "block field is `block` for braces and `do_block` for do/end",
            "method parameters: method_parameters with identifier, optional_parameter(name,value), splat_parameter, keyword_parameter (value field only when defaulted), hash_splat_parameter, hash_splat_nil, block_parameter, forward_parameter, destructured_parameter; parenless def parses to the same node; `def perform` without parentheses or parameters has no parameters field",
            "include Sidekiq::Job is a receiverless call `include` whose argument is a scope_resolution; superclass nodes are `superclass` holding constant or scope_resolution (`::ActiveJob::Base` is a nested scope_resolution whose text is the full bytes)",
            "name-only differences: none that change D4",
        ],
        "data": ["qual-data/grammar-py312.json", "qual-data/grammar-py314.json"],
    },
    "b_refs_binding": {
        "stop_rule": "not triggered (refs locate every site; owners non-null; definitions 1:1)",
        "fallback_to_iterative_walk": False,
        "fixture_site_rows_indexed": len(sites),
        "skip_pattern_sites": [{"path": s["path"], "refs_rows": s["refs_rows"]} for s in skipped],
        "problems_py312": r12["problems"],
        "problems_py314": r14["problems"],
        "py312_equals_py314": refs_equal,
        "every_site_exactly_one_refs_row": all(s["refs_rows"] == 1 for s in sites),
        "every_site_located_by_span": all(s["located"] for s in sites),
        "every_owner_non_null": all(s["owner_non_null"] for s in sites),
        "refs_rows_without_a_call_node": r12["extra"]["refs_rows_without_a_call_node"],
        "binding_namespace_equals_innermost_nesting_join": all(s["namespace_equals_nesting_join"] for s in sites),
        "owners": {
            "method_owner_sites": sum(1 for s in sites if s["owner"][0] == "method"),
            "file_owner_sites": [
                {"path": s["path"], "line": s["line"], "unsupported_source_owner": s["unsupported_source_owner"]}
                for s in sites
                if s["owner"][0] == "file"
            ],
        },
        "rooted_probe": [
            s for s in sites if s["path"] == "app/services/outer.rb" and s["binding_namespace"] == "::Rooted"
        ],
        "compact_probe": [
            {"path": s["path"], "line": s["line"], "owner": s["owner"], "binding_namespace": s["binding_namespace"]}
            for s in sites
            if s["path"] in {"app/services/outer.rb", "app/services/reports/daily.rb"} and s["binding_namespace"] != "::Rooted"
        ],
        "incremental": {
            "unchanged_since_x_site_rows_equal": r12["extra"]["incremental_site_refs_equal"],
            "unchanged_since_x_all_ruby_refs_equal": r12["extra"]["incremental_all_ruby_refs_equal"],
            "after_unrelated_edit_site_rows_equal": r12["extra"]["incremental_after_edit_site_rows_equal"],
        },
        "definitions": {
            "perform_and_class_nodes": len(defs),
            "one_to_one": all(len(d["rows"]) == 1 for d in defs if d["name"] != "::Rooted" and not d["name"].startswith("::")),
            "rooted_class_has_no_symbol_row": [d for d in defs if d["name"] == "::Rooted" or d["name"].startswith("::")][0]["rows"] == [],
            "singleton_performs_are_category_static": sorted(
                {d["rows"][0]["binding"]["category"] for d in defs if d["node"] == "singleton_method"}
            ),
        },
        "data": ["qual-data/refs-py312.json", "qual-data/refs-py314.json"],
    },
    "c_validity": {
        "scratch_copy": "base tree + EV1-EV5 + CG6/CG7 (apply script identical to the product edit)",
        "results": validity,
        "s25_parametrized_carve_out_test": "test_s25_isolation.py:244-267 passes unchanged on the branch (see regression gate)",
    },
    "d_flag_off_baselines": {
        "method": "git archive 0194758 run in atlas-aci-dev-s16; compare_graphs.py --digest (the S26 copy, ADAPTERS extended)",
        "s26_fixture_off": {"sha256": json.loads((data / "base-digest-s26-off.json").read_text())["sha256"], "file": "qual-data/base-digest-s26-off.json"},
        "s24_fixture_rails": {"sha256": json.loads((data / "base-digest-s24-rails.json").read_text())["sha256"], "file": "qual-data/base-digest-s24-rails.json"},
        "s25_fixture_rails_models_map": {"sha256": json.loads((data / "base-digest-s25-models.json").read_text())["sha256"], "file": "qual-data/base-digest-s25-models.json"},
    },
    "f_fidelity": {
        "verifiable": False,
        "reason": "the Sidekiq and Rails ActiveJob guides cannot be fetched at pinned revisions in this environment (network only for the python:3.14 probe container)",
        "decision": "docs/frameworks/rails.md states only what the adapter does (positional argument i to parameter i by span) and names serialisation, keyword transformation and middleware as not modelled; no version-specific claim about either library",
    },
    "stop_rule_triggered": False,
}
(root / "qualification.json").write_text(json.dumps(qualification, indent=1, sort_keys=True) + "\n")
base = {
    "base_commit": "01947584046036ee4328eda03d23787ec7471a30",
    "method": "git archive 0194758 into a scratch tree, run in atlas-aci-dev-s16 (uid 1000, python 3.12.16)",
    "tools_json": {
        "sha256": "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56",
        "bytes": 1631472,
        "matches_pin": True,
        "command": "python -P -m atlas_aci tools --repo /ws",
    },
    "envelopes": {
        "MINIMUM_BUDGET": 3436,
        "COLLECTION_MINIMUM_BUDGET": 3448,
        "FIXED_ERROR_ENVELOPE_CAP": 797,
        "IMPACT_MINIMUM_BUDGET": 7730,
        "source": "asserted by the base tests test_s21/test_s22/test_s24/test_s32 isolation (passed at base)",
    },
    "full_suite": {
        "command": "pytest -q -p no:cacheprovider --deselect tests/slices/test_s08_integration.py::test_nonregular_pack_does_not_block_acquisition",
        "result": "1 failed, 1825 passed, 10 skipped, 1 deselected in 648.09s (0:10:48)",
        "failed": ["tests/slices/test_s01_contract.py::test_call_deadline_after_initialization (known pre-existing failure under uid 1000)"],
        "deselected": "test_s08_integration::test_nonregular_pack_does_not_block_acquisition (known flaky; hangs in the dev image)",
        "ci_reference": "baseline.json: 1827 passed, 10 skipped on CI (Python 3.14)",
        "log": "qual-data/base-full-suite.log.gz",
    },
    "stop_rule": "not triggered: the base is green apart from the two known pre-existing items",
}
(root / "base-invariants.json").write_text(json.dumps(base, indent=1) + "\n")
print("ok")
