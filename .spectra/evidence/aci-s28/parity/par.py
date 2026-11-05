import hashlib, json, os, subprocess, sys, tempfile, platform
from pathlib import Path
root = Path(sys.argv[1]); out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
fx = root / "mcp-server/tests/fixtures/python_dependencies"
sys.path.insert(0, str(root / "mcp-server/src"))
import importlib.util
spec = importlib.util.spec_from_file_location("gen28", fx / "generate.py"); gen = importlib.util.module_from_spec(spec); sys.modules["gen28"] = gen; spec.loader.exec_module(gen)
res = {"python": sys.version.split()[0], "platform": platform.machine()}
for i in (1, 2):
    d = out / f"gen{i}"; d.mkdir(exist_ok=True)
    res[f"gen{i}"] = {k: hashlib.sha256(v).hexdigest() for k, v in gen.generate(d).items()}
res["generator_repeatable"] = res["gen1"] == res["gen2"]
res["generator_equals_committed"] = all(hashlib.sha256((fx / k).read_bytes()).hexdigest() == v for k, v in res["gen1"].items())
snaps = []
for seed in ("1", "2"):
    o = out / f"snap{seed}.json"
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(root / "mcp-server/src"))
    p = subprocess.run([sys.executable, "-m", "atlas_aci", "import-python-dependency", "--lock", str(fx / "single.uv.lock"),
        "--package", "s28-demo-pkg", "--wheel", str(fx / gen.PURE), "--output", str(o)], capture_output=True, env=env)
    snaps.append((p.returncode, p.stdout, p.stderr[-300:], o.read_bytes() if o.exists() else b""))
res["rc"] = [s[0] for s in snaps]
res["stderr"] = snaps[0][2].decode()
res["snapshot_sha"] = [hashlib.sha256(s[3]).hexdigest() for s in snaps]
res["stdout_equal"] = snaps[0][1] == snaps[1][1]
snap = json.loads(snaps[0][3]) if snaps[0][3] else {}
res["extractor"] = snap.get("extractor"); res["snapshot_id"] = snap.get("snapshot_id"); res["bytes"] = len(snaps[0][3])
print(json.dumps(res, indent=1))
