"""ACI-S23 isolation: the product surface is unchanged, the aggregator is pure, docs are honest."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from click.testing import CliRunner
from test_s23_support import EVIDENCE, RAW, ROOT, agg, manifest, rec

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
ALLOWED_IMPORTS = {
    "__future__", "argparse", "gzip", "hashlib", "importlib", "json", "re", "sys",
    "decimal", "pathlib", "typing",
}  # fmt: skip
BANNED_NAMES = {
    "time", "datetime", "random", "subprocess", "socket", "platform", "os", "atlas_aci",
    "environ", "getenv", "urllib", "uuid", "tempfile",
}  # fmt: skip


def test_the_tool_surface_is_byte_identical_and_read_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    out = CliRunner().invoke(cli, ["tools", "--repo", str(repo)], catch_exceptions=False)
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    assert len(json.loads(out.stdout)) == 7


def test_the_aggregator_is_stdlib_only_and_free_of_clocks_processes_and_environment():
    tree = ast.parse((ROOT / "scripts/eval/v3_aggregate.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported and imported <= ALLOWED_IMPORTS | {
        "collections",
        "dataclasses",
        "base64",
        "math",
    }
    assert not imported & BANNED_NAMES
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not used & BANNED_NAMES, used & BANNED_NAMES
    # importlib is used only to load the sibling span_metrics module.
    sources = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "spec_from_file_location"
    ]
    assert len(sources) == 1
    assert "atlas/evals/span_metrics.py" in (ROOT / "scripts/eval/v3_aggregate.py").read_text()


class _Strip(ast.NodeTransformer):
    """Drop type hints so a typed copy compares equal to the untyped original."""

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        node.returns = None
        for argument in [*node.args.args, *node.args.kwonlyargs]:
            argument.annotation = None
        return node

    def visit_Assign(self, node):
        self.generic_visit(node)
        target, value = node.targets[0], node.value
        if (
            len(node.targets) == 1
            and isinstance(target, ast.Tuple)
            and isinstance(value, ast.Tuple)
            and len(target.elts) == len(value.elts)
        ):  # `a, b = x, y` and `a = x` then `b = y` are the same statement sequence
            return [
                ast.copy_location(ast.Assign(targets=[t], value=v), node)
                for t, v in zip(target.elts, value.elts, strict=True)
            ]
        return node

    def visit_AnnAssign(self, node):
        self.generic_visit(node)
        if node.value is None:
            return None
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)


def test_the_s18_script_is_untouched_and_span_metrics_is_a_verbatim_move():
    script = (ROOT / "atlas/evals/measure_context_yield.py").read_bytes()
    assert (
        hashlib.sha256(script).hexdigest().startswith("c3b6fddf")
    )  # pinned in the S18/S22 receipts
    old = _Strip().visit(ast.parse(script.decode()))
    new = _Strip().visit(ast.parse((ROOT / "atlas/evals/span_metrics.py").read_text()))
    for name in ("union", "metrics", "body_and_sources"):
        before = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == name)
        after = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert before.body and ast.dump(before.body[0]) and ast.dump(before) == ast.dump(after)


def test_the_readme_documents_the_real_canary_flags_and_the_evidence():
    text = (ROOT / "README.md").read_text()
    assert "--canaries atlas/evals/canary-missions.md" not in text
    assert "canary-missions.md" not in text
    assert "--canaries-dir" in text and "--host stdio" in text and "--server-json" in text
    assert "docs/evidence/v3" in text
    assert "no dispatcher is implemented" not in text.lower()
    setup = (ROOT / "SETUP.md").read_text()
    assert "--canaries atlas/evals/canary-missions.md" not in setup


def test_the_raw_directory_holds_only_decodable_manifest_bound_records_within_a_size_cap():
    _, sha = manifest()
    files = sorted(RAW.iterdir())
    assert files and all(f.name.endswith(".json.gz") and f.is_file() for f in files)
    assert sum(f.stat().st_size for f in files) < 600_000
    assert max(f.stat().st_size for f in files) < 150_000
    kinds = set()
    for path in files:
        record = agg.decode_gzip_canonical(path.read_bytes())
        assert record["format"] == "atlas-eval-raw.v1" and record["manifest_sha256"] == sha
        assert record["status"] == "complete"  # a committed V0 not_run fails the release gate
        kinds.add(record["kind"])
        assert not path.name.endswith(".so.json.gz")
    assert kinds == {"probe", "retrieval", "cost", "p0", "offline"}
    assert {f.name for f in files} >= {"retrieval.V0.legacy.json.gz", "probe.V0.json.gz"}
    assert not [p for p in EVIDENCE.rglob("*") if p.suffix in {".so", ".dylib", ".dll"}]


def test_manifest_anchors_declared_inputs_and_p0_ids_still_exist():
    m, _ = manifest()
    assert len(m["evidence_anchors"]) >= 8
    for anchor in m["evidence_anchors"]:  # line numbers rot: each anchor still holds its token
        lines = (ROOT / anchor["path"]).read_text().splitlines()
        assert anchor["token"] in lines[anchor["line"] - 1], anchor
    declared = m["inputs"]["v0_grammar_cache"]
    cache = json.loads((ROOT / declared["path"]).read_bytes())
    assert hashlib.sha256((ROOT / declared["path"]).read_bytes()).hexdigest() == declared["sha256"]
    assert cache["tree_sha256"] == declared["tree_sha256"] and len(cache["files"]) >= 10
    assert agg.digest(sorted([k, v] for k, v in cache["files"].items())) == cache["tree_sha256"]
    assert cache["fetch"]["returned"] == 9 and cache["tree_sitter_language_pack"] == "1.6.0"
    for node in m["p0"]["node_ids"]:
        path, _, function = node["id"].partition("::")
        assert re.search(
            rf"^(async )?def {function}\(", (ROOT / "mcp-server" / path).read_text(), re.M
        )
    v0 = next(v for v in m["variants"] if v["id"] == "V0")["server"]
    assert v0["network"] == "none" and v0["pull"] == "never" and "--network" in v0["serve_argv"]
    assert "--network" in v0["index_argv"] and "none" in v0["index_argv"]
    assert v0["digest"] == "sha256:e2542ef8d569882c560065f0bcaed9ef2a7398e7e61765e25b142a3c93ec7cf7"
    assert list(rec.COUNTERS) == m["cost"]["counters"]
    lock = (ROOT / "eidolons.mcp.lock").read_text()
    assert v0["digest"].split(":")[1] in lock
    matrix = {(h["dimension"], h["value"]): h["status"] for h in m["host_matrix"]}
    assert matrix[("OS", "macOS")] == "tested (partial)"
    assert any("recorder" in dim for dim, _ in matrix)


def test_the_evidence_readme_exists_and_states_the_replay_commands():
    text = (EVIDENCE / "README.md").read_text()
    for needle in ("v3_record.py", "v3_aggregate.py", "--group-add", "grammar cache", "unmeasured"):
        assert needle in text, needle
    assert (
        Path(EVIDENCE / "release-report.json").is_file()
        and Path(EVIDENCE / "release-report.md").is_file()
    )
