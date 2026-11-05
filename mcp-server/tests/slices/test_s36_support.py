"""ACI-S36 shared helpers: independent canonical/projection/reseal code and script runners.

Nothing here imports the verifier or the runner for its own answers: `canonical`,
`projection` and `reseal` are re-implemented with `hashlib`, `json` and `gzip` only, so an
agreement between them and the scripts is evidence, not a tautology. Scripts are run as real
child processes (`python -S` where the optional provider must stay invisible) with
`PYTHONDONTWRITEBYTECODE=1`; tests never touch `sys.path` or `sys.modules`.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
RUNNER = SCRIPTS / "experiment-leiden-vs-lpa.py"
VERIFIER = SCRIPTS / "verify-community-experiment.py"
FX = ROOT / "mcp-server/tests/fixtures/community_experiment"
SITE = FX / "fixture_site"
PROD_MANIFEST = FX / "manifest.v1.json"
FIX_MANIFEST = FX / "manifest.fixture.v1.json"
S18_GRAPH = FX / "s18-task-graph.v1.json"
SYN_GRAPH = FX / "graphs/synthetic-cliques.v1.json"
USEFUL = ROOT / "mcp-server/tests/fixtures/s18/useful-spans.v1.json"
S23_MANIFEST = ROOT / "atlas/evals/v3/manifest.v1.json"
BUNDLE = ROOT / ".spectra/changes/aci-s17/probe/probe-graphs.json.gz"
SIDECAR = ROOT / ".spectra/changes/aci-s17/probe/aci-s17-probe-lpa-vs-louvain.json"
LPA_FILE = ROOT / "mcp-server/src/atlas_aci/label_propagation.py"
DOCS = ROOT / "docs/experiments/community-comparison.md"
REAL_RUN = ROOT / ".spectra/evidence/aci-s36/real-run"
PRODUCTION_SHA = "3239ec59d9fce113a115ff4b27c42bfbf06715060e1b2cb7e7de8e2e435a2beb"
BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
REPO_COPY = (
    ".spectra/changes/aci-s17/probe/probe-graphs.json.gz",
    "atlas/evals/v3/manifest.v1.json",
    "mcp-server/tests/fixtures/s18/useful-spans.v1.json",
    "mcp-server/src/atlas_aci/label_propagation.py",
    "mcp-server/tests/fixtures/community_experiment/s18-task-graph.v1.json",
    "mcp-server/tests/fixtures/community_experiment/graphs/synthetic-cliques.v1.json",
)
GAP = 1  # seconds of slack for process start-up inside timeouts


# ------------------------------------------------------------------ independent helpers


def canonical(value: Any) -> bytes:
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return (text + "\n").encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: Any) -> str:
    return sha(canonical(value))


def projection(nodes: list, edges: list) -> tuple[list, list]:
    """Independent atlas-community-projection.v1: sort nodes, drop loops, collapse pairs."""
    order = sorted(range(len(nodes)), key=lambda i: tuple(nodes[i]))
    where = {old: new for new, old in enumerate(order)}
    pairs = {(min(where[a], where[b]), max(where[a], where[b])) for a, b in edges if a != b}
    return [list(nodes[i]) for i in order], [list(p) for p in sorted(pairs)]


def graph_of(path: Path) -> dict:
    return json.loads(path.read_bytes())


def bundle_graph(key: str) -> dict:
    return json.loads(gzip.decompress(BUNDLE.read_bytes()))[key]


def projected(graph_id: str) -> tuple[list, list]:
    """The independently projected graph of a manifest graph id."""
    if graph_id in ("solidus", "spree"):
        raw = bundle_graph(graph_id)
    elif graph_id == "synthetic-cliques":
        raw = graph_of(SYN_GRAPH)
    else:
        raw = graph_of(S18_GRAPH)
    return projection(raw["nodes"], raw["edges"])


def projection_sha(graph_id: str) -> str:
    nodes, edges = projected(graph_id)
    return digest({"edges": edges, "nodes": nodes})


def read_partitions(directory: Path) -> dict:
    return json.loads(gzip.decompress((directory / "partitions.json.gz").read_bytes()))


def read_result(directory: Path) -> dict:
    return json.loads((directory / "result.json").read_bytes())


def reseal_raw(directory: Path, plain: bytes | None = None, result_edit=None) -> None:
    """Re-bind the artifacts after a deliberate tamper so a semantic check is reached.

    `plain` replaces the decompressed partitions document; `result_edit(result)` may edit the
    result before its digests are recomputed. Telemetry stays bound to the new result digest.
    """
    if plain is not None:
        (directory / "partitions.json.gz").write_bytes(gzip.compress(plain, 9, mtime=0))
    result = read_result(directory)
    plain_now = gzip.decompress((directory / "partitions.json.gz").read_bytes())
    result["partitions_sha256"] = sha(plain_now)
    if result_edit is not None:
        result_edit(result)
    body = {k: v for k, v in result.items() if k != "canonical_sha256"}
    result["canonical_sha256"] = digest(body)
    (directory / "result.json").write_bytes(canonical(result))
    telemetry = json.loads((directory / "telemetry.json").read_bytes())
    telemetry["result_canonical_sha256"] = result["canonical_sha256"]
    (directory / "telemetry.json").write_bytes(canonical(telemetry))


def tamper_partitions(directory: Path, edit) -> None:
    doc = read_partitions(directory)
    edit(doc)
    reseal_raw(directory, canonical(doc))


def tamper_result(directory: Path, edit) -> None:
    reseal_raw(directory, None, edit)


def copy_run(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def repo_copy(base: Path, extra: tuple[str, ...] = ()) -> Path:
    """A tmp repo root holding every input the scripts read, so a byte can be flipped safely."""
    root = base / "repo"
    for relative in (*REPO_COPY, *extra):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    return root


# ------------------------------------------------------------------------ script runners


def child_env(site: bool = False, **extra: str) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if site:
        env["PYTHONPATH"] = str(SITE)
    env.update(extra)
    return env


def run_script(
    script: Path,
    args: list[str],
    *,
    site: bool = False,
    flags: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    timeout: int = 240,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *flags, str(script), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_env(site) if env is None else env,
        check=False,
    )


DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("s36_runner", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
knobs = json.loads(sys.argv[2])
if knobs.pop("hook_make_temp", False):
    def broken(**kwargs):
        raise OSError("planted")
    knobs["make_temp"] = broken
if knobs.pop("hook_self_check", False):
    def refuse(*args, **kwargs):
        raise module.V.Rejection("metric_mismatch")
    knobs["self_check"] = refuse
sys.exit(module.main(sys.argv[3:], overrides=knobs))
"""


def run_driver(
    overrides: dict, args: list[str], *, script: Path = RUNNER, site: bool = True, **env: str
) -> subprocess.CompletedProcess[str]:
    """Call `main(argv, overrides=...)` in a child, so bound overrides never touch the CLI."""
    return subprocess.run(
        [sys.executable, "-c", DRIVER, str(script), json.dumps(overrides), *args],
        capture_output=True,
        text=True,
        timeout=240,
        env=child_env(site, **env),
        check=False,
    )


def run_experiment(
    manifest: Path,
    manifest_sha: str,
    out: Path,
    *,
    repo_root: Path | None = None,
    site: bool = True,
    flags: tuple[str, ...] = (),
    extra: list[str] | None = None,
    **env: str,
) -> subprocess.CompletedProcess[str]:
    args = ["run", "--manifest", str(manifest), "--manifest-sha256", manifest_sha]
    args += ["--out", str(out)]
    if repo_root is not None:
        args += ["--repo-root", str(repo_root)]
    args += extra or []
    merged = child_env(site, **env)
    return run_script(RUNNER, args, flags=flags, env=merged)


def run_verify(
    results: Path,
    manifest: Path = FIX_MANIFEST,
    manifest_sha: str | None = None,
    *,
    repo_root: Path | None = None,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = ["--results", str(results), "--manifest", str(manifest)]
    args += ["--manifest-sha256", manifest_sha or sha(manifest.read_bytes())]
    if repo_root is not None:
        args += ["--repo-root", str(repo_root)]
    args += extra or []
    return run_script(VERIFIER, args)


def fixture_manifest_sha() -> str:
    return sha(FIX_MANIFEST.read_bytes())


def write_manifest(path: Path, manifest: dict) -> str:
    raw = canonical(manifest)
    path.write_bytes(raw)
    return sha(raw)


def manifest_copy(source: Path = FIX_MANIFEST) -> dict:
    return json.loads(source.read_bytes())


def load_path_module(path: Path, name: str) -> Any:
    """Load a script by file path without registering it anywhere; no bytecode is written."""
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


_CACHE: dict[str, Any] = {}


def verifier() -> Any:
    if "verifier" not in _CACHE:
        _CACHE["verifier"] = load_path_module(VERIFIER, "s36_verifier_under_test")
    return _CACHE["verifier"]


def runner() -> Any:
    if "runner" not in _CACHE:
        _CACHE["runner"] = load_path_module(RUNNER, "s36_runner_under_test")
    return _CACHE["runner"]


def lpa_module() -> Any:
    if "lpa" not in _CACHE:
        _CACHE["lpa"] = load_path_module(LPA_FILE, "s36_lpa_under_test")
    return _CACHE["lpa"]


def make_fx_run(base: Path, hash_seed: str = "0", capture: Path | None = None) -> Path:
    """The fixture manifest run once with the fixture provider on PYTHONPATH."""
    out = base / ("run-" + hash_seed)
    env: dict[str, str] = {"PYTHONHASHSEED": hash_seed}
    if capture is not None:
        capture.mkdir(parents=True, exist_ok=True)
        env["ATLAS_S36_FIXTURE_CAPTURE_DIR"] = str(capture)
    done = run_experiment(FIX_MANIFEST, fixture_manifest_sha(), out, **env)
    assert done.returncode == 0, done.stderr
    return out


def make_prod_run(base: Path, name: str) -> Path:
    """The production manifest run with `python -S`, so no provider can be seen."""
    out = base / name
    done = run_experiment(PROD_MANIFEST, PRODUCTION_SHA, out, site=False, flags=("-S",))
    assert done.returncode == 0, done.stderr
    return out


def leftover(parent: Path) -> list[str]:
    return sorted(p.name for p in parent.iterdir() if p.name.startswith(".s36-run-"))


def rejected(done: subprocess.CompletedProcess[str], text: str, who: str = "experiment") -> None:
    prefix = (
        "Community experiment rejected: "
        if who == "experiment"
        else ("Community verification rejected: ")
    )
    assert done.returncode == 1, (done.returncode, done.stderr)
    assert done.stdout == ""
    assert done.stderr == prefix + text + "\n", done.stderr


# ------------------------------------------------------------- shared, session-long runs


def shared_fx(factory) -> dict[str, Any]:
    """One fixture-provider run (PYTHONHASHSEED=0, with captures), shared by every module."""
    if "fx" not in _CACHE:
        base = factory.mktemp("s36fx")
        capture = base / "captures"
        out = make_fx_run(base, "0", capture)
        _CACHE["fx"] = {"dir": out, "captures": capture, "base": base}
    return _CACHE["fx"]


def shared_prod(factory) -> Path:
    """One production-manifest run with `python -S` (provider invisible), shared."""
    if "prod" not in _CACHE:
        _CACHE["prod"] = make_prod_run(factory.mktemp("s36prod"), "run-a")
    return _CACHE["prod"]
