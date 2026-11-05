"""ACI-S23 isolation: the product surface is unchanged, the aggregator is pure, docs are honest."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from test_s23_support import EVIDENCE, RAW, ROOT, agg, manifest, rec

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
MANIFEST_SHA256 = "3b901b99607d3db95d02a79794792cc8fb0df142901dc35fb502a1fa8e49b1ce"
# The `mcp-server/uv.lock` anchor is checked against the lock as it was recorded at eval time, not
# against the live file: dependency bumps stop tripping it while every other anchor stays live.
FROZEN_LOCK_PATH = "mcp-server/uv.lock"
FROZEN_ROUTED = frozenset({FROZEN_LOCK_PATH})
FROZEN_LOCK = ROOT / "mcp-server/tests/fixtures/s23/uv.lock.as-recorded.gz"
FROZEN_LOCK_SHA256 = "6b820f14327cd71951abe8d4a41b2d1962af21b33b492469de82553a31ca2962"
RECEIPT = ROOT / ".spectra/evidence/aci-s23/base-invariants.json"
RECEIPT_BASE_COMMIT = "1cedf60331ef892f1fb2b3a68eb942bf2dac894b"
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
        check_anchor(anchor)
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


# ----------------------------------------------------------------------------- anchor routing
def frozen_lock_bytes() -> bytes:
    return gzip.decompress(FROZEN_LOCK.read_bytes())


def frozen_source(path: str) -> str:
    """The frozen eval-time text for `path`; only the lock has one."""
    if path not in FROZEN_ROUTED:
        raise ValueError(f"no frozen eval-time copy for {path}")
    return frozen_lock_bytes().decode()


def anchor_source(anchor: dict[str, Any], root: Path) -> tuple[str, list[str]]:
    """(route, lines) an anchor is checked against: the frozen copy or the live file."""
    path = anchor["path"]
    if path in FROZEN_ROUTED:
        return "frozen", frozen_source(path).splitlines()
    return "live", (root / path).read_text().splitlines()


def check_anchor(anchor: dict[str, Any], root: Path = ROOT) -> str:
    """The anchor's token is on its line in its source; returns the route used."""
    route, lines = anchor_source(anchor, root)
    assert 1 <= anchor["line"] <= len(lines), anchor
    assert anchor["token"] in lines[anchor["line"] - 1], anchor
    return route


def test_the_frozen_lock_is_the_lock_recorded_at_eval_time():
    data = frozen_lock_bytes()
    assert hashlib.sha256(data).hexdigest() == FROZEN_LOCK_SHA256
    receipt = json.loads(RECEIPT.read_text())
    recorded = receipt["src_pyproject_uvlock_sha_manifest"]["files"]["mcp-server/uv.lock"]
    assert recorded == FROZEN_LOCK_SHA256 and receipt["base_commit"] == RECEIPT_BASE_COMMIT
    assert FROZEN_LOCK.read_bytes()[4:8] == b"\0\0\0\0"  # gzip header carries no mtime
    assert FROZEN_LOCK.name != "uv.lock" and FROZEN_LOCK.stat().st_size < 100_000
    # Optional extra: the recorded base commit holds the same bytes when history is present
    # (a shallow CI checkout has no such commit; only this extra is skipped there).
    try:
        have = subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{RECEIPT_BASE_COMMIT}^{{commit}}"],
            capture_output=True,
            timeout=60,
        ).returncode
    except (FileNotFoundError, subprocess.TimeoutExpired):
        have = 1
    if have == 0:
        shown = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{RECEIPT_BASE_COMMIT}:mcp-server/uv.lock"],
            capture_output=True,
            timeout=60,
        )
        assert shown.returncode == 0
        assert hashlib.sha256(shown.stdout).hexdigest() == FROZEN_LOCK_SHA256


def test_anchor_routing_freezes_only_the_lock_and_checks_the_rest_live():
    m, sha = manifest()
    assert sha == MANIFEST_SHA256  # the anchors (and their paths) cannot change unnoticed
    anchors = m["evidence_anchors"]
    routes = [(a["path"], check_anchor(a)) for a in anchors]
    frozen = {path for path, route in routes if route == "frozen"}
    assert frozen == set(FROZEN_ROUTED) == {"mcp-server/uv.lock"}
    live = [path for path, route in routes if route == "live"]
    assert len(live) == len(anchors) - 1 == 8
    assert "mcp-server/uv.lock" not in live


def test_negative_controls_a_rotted_anchor_fails_and_the_frozen_route_is_exclusive(tmp_path):
    (tmp_path / "f.txt").write_text("a\nb token c\nd\n")
    good = {"path": "f.txt", "line": 2, "token": "token"}
    assert check_anchor(good, tmp_path) == "live"
    for rotted in ({**good, "token": "tokex"}, {**good, "line": 3}, {**good, "line": 99}):
        with pytest.raises(AssertionError):
            check_anchor(rotted, tmp_path)
    (tmp_path / "f.txt").write_text("new first line\na\nb token c\nd\n")  # a line moves
    with pytest.raises(AssertionError):
        check_anchor(good, tmp_path)
    # The frozen route refuses every other path and does not fall back to the live file.
    for other in ("mcp-server/Dockerfile", "f.txt", ".github/workflows/ci.yml", "mcp-server/uv"):
        with pytest.raises(ValueError):
            frozen_source(other)
    with pytest.raises(FileNotFoundError):  # a non-lock anchor is live: no file, no pass
        check_anchor({"path": "mcp-server/Dockerfile", "line": 55, "token": "x"}, tmp_path)
    # The lock anchor ignores the live file: it passes in an empty root, fails on a rotted token.
    lock = next(a for a in manifest()[0]["evidence_anchors"] if a["path"] == FROZEN_LOCK_PATH)
    assert check_anchor(lock, tmp_path) == "frozen"
    for rotted in ({**lock, "token": "1.27.1"}, {**lock, "line": lock["line"] + 1}):
        with pytest.raises(AssertionError):
            check_anchor(rotted, tmp_path)
    assert frozen_lock_bytes().decode().splitlines()[lock["line"] - 2] == 'name = "mcp"'


def test_the_evidence_readme_exists_and_states_the_replay_commands():
    text = (EVIDENCE / "README.md").read_text()
    for needle in ("v3_record.py", "v3_aggregate.py", "--group-add", "grammar cache", "unmeasured"):
        assert needle in text, needle
    assert (
        Path(EVIDENCE / "release-report.json").is_file()
        and Path(EVIDENCE / "release-report.md").is_file()
    )
