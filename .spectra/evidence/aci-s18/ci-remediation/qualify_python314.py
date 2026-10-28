"""Qualify the locked dependencies and real context SDK paths on CPython 3.14."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[4]
out = Path(__file__).resolve().parent
environment = {
    **os.environ,
    "UV_CACHE_DIR": "/tmp/aci-s01-uv-cache",
    "UV_PROJECT_ENVIRONMENT": "/tmp/aci-s18-ci-python314-qualified",
    "PYTHONDONTWRITEBYTECODE": "1",
}
python = environment["UV_PROJECT_ENVIRONMENT"] + "/bin/python"
uv = "/tmp/aci-s01-tools/bin/uv"
records = {}


def run(name, argv, cwd):
    start = time.monotonic()
    result = subprocess.run(argv, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    raw = result.stdout
    packed = gzip.compress(raw, mtime=0)
    (out / (name + ".log.gz")).write_bytes(packed)
    record = {
        "argv": argv, "cwd": str(cwd), "exit_code": result.returncode,
        "elapsed_seconds": time.monotonic() - start,
        "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "gzip_sha256": hashlib.sha256(packed).hexdigest(),
        "environment": {key: environment[key] for key in
                        ("UV_CACHE_DIR", "UV_PROJECT_ENVIRONMENT", "PYTHONDONTWRITEBYTECODE")},
    }
    records[name] = record
    (out / "python314-qualification.json").write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    print(raw.decode(errors="replace")[-1800:], flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    return raw


run("python314-frozen-sync", [uv, "sync", "--frozen", "--extra", "dev", "--python",
                             "/opt/homebrew/bin/python3.14"], root / "mcp-server")
run("python314-lock", [uv, "lock", "--check"], root / "mcp-server")
raw = run("python314-imports", [python, "-c", """
import sys, json, platform, importlib.metadata as m
import rpds, pydantic_core, tree_sitter, tree_sitter_language_pack, atlas_aci
from pathlib import Path
assert sys.version_info[:2] == (3, 14)
assert Path(atlas_aci.__file__).resolve().is_relative_to(Path.cwd() / 'src')
print(json.dumps({'executable':sys.executable,'version':sys.version,'platform':platform.platform(),
 'source_import':atlas_aci.__file__, 'versions':{n:m.version(n) for n in
 ['atlas-aci','mcp','rpds-py','pydantic-core','tree-sitter','tree-sitter-language-pack','pytest']},
 'native_imports':['rpds','pydantic_core','tree_sitter','tree_sitter_language_pack']}))
"""], root / "mcp-server")
(out / "python314-runtime.json").write_bytes(raw)
run("python314-context-sdk", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
    "tests/slices/test_s18_contract.py", "tests/slices/test_s18_stdio.py"], root / "mcp-server")
