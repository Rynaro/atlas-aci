"""Helper of prod-scenario.sh (ACI-S30); standard library plus atlas_aci, run inside the image.

    make-world.py world W REPO          build the repositories, the wheel and the lock under W
    make-world.py mapping W OUT APP LIB [PACKAGE_PROVIDER]
                                        write a mapping for the two snapshot ids
    make-world.py get FILE KEY...       print one value of a JSON file (keys and list indexes)
    make-world.py kinds FILE            the distinct boundary kinds of an impact result
    make-world.py entity PATH           the file entity id of a python path
    make-world.py link-entity FILE NAME the provider entity id of the link to NAME

The texts come from tests/fixtures/cross_repo/world.json and the wheel is built by the S28
generator, so the scenario links the same world the contract tests link.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

NAME = "s30-shapes"
VERSION = "1.0.0"
WHEEL = "s30_shapes-1.0.0-py3-none-any.whl"


def load_generator(repo: Path):
    path = repo / "mcp-server/tests/fixtures/python_dependencies/generate.py"
    spec = importlib.util.spec_from_file_location("s30_scenario_generate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["s30_scenario_generate"] = module
    spec.loader.exec_module(module)
    return module


def write_tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def init_git(root: Path) -> None:
    for args in (["init", "-q"], ["config", "user.email", "s30@example.invalid"]):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "s30"], check=True)


def world(work: Path, repo: Path) -> None:
    gen = load_generator(repo)
    texts = json.loads((repo / "mcp-server/tests/fixtures/cross_repo/world.json").read_text())
    package: dict[str, str] = texts["package"]
    dist = "s30_shapes-" + VERSION + ".dist-info"
    entries = [gen.Entry(path.encode(), text.encode()) for path, text in sorted(package.items())]
    entries.append(
        gen.Entry(
            (dist + "/METADATA").encode(),
            ("Metadata-Version: 2.1\nName: s30-shapes\nVersion: " + VERSION + "\n\n").encode(),
        )
    )
    entries.append(
        gen.Entry(
            (dist + "/WHEEL").encode(),
            b"Wheel-Version: 1.0\nGenerator: s30-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
    )
    wheel = gen.build_zip(entries)
    line = gen.wheel_line(WHEEL, hashlib.sha256(wheel).hexdigest(), len(wheel))
    lock = gen.lock_document([gen.package_block(NAME, VERSION, wheels=(line,))])
    inputs = work / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / WHEEL).write_bytes(wheel)
    (inputs / "uv.lock").write_text(lock)
    provider = {"src/" + path: text for path, text in package.items()}
    provider.update(texts["provider_extra"])
    sentinel = {"src/" + path: text for path, text in package.items()}
    sentinel["src/only_other.py"] = "def only_other():\n    pass\n"
    for name, files in (("app", texts["consumer"]), ("lib", provider), ("other", sentinel)):
        root = work / name
        root.mkdir(parents=True, exist_ok=True)
        init_git(root)
        write_tree(root, files)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mapping(work: Path, out: Path, app: str, lib: str, stranger: str | None) -> None:
    inputs = work / "inputs"
    document = {
        "format": "atlas-cross-repo-mapping.v1",
        "repositories": [
            {"repository_id": "app", "role": "consumer", "snapshot_id": app},
            {"repository_id": "lib", "role": "provider", "snapshot_id": lib},
        ],
        "consumer": {"lock_sha256": sha256_file(inputs / "uv.lock"), "environment": {}},
        "packages": [
            {
                "provider_repository_id": stranger or "lib",
                "coordinate": "pypi:" + NAME + "@" + VERSION,
                "package_snapshot_sha256": sha256_file(inputs / "pkg.json"),
                "source_prefix": "src/",
            }
        ],
    }
    out.write_text(json.dumps(document, indent=2) + "\n")


def get(path: Path, keys: list[str]) -> None:
    value = json.loads(path.read_text())
    for key in keys:
        value = value[int(key)] if isinstance(value, list) else value[key]
    print(json.dumps(value) if isinstance(value, (dict, list)) else str(value))


def main(argv: list[str]) -> int:
    command = argv[1]
    if command == "world":
        world(Path(argv[2]), Path(argv[3]))
    elif command == "mapping":
        extra = argv[6] if len(argv) > 6 else None
        mapping(Path(argv[2]), Path(argv[3]), argv[4], argv[5], extra)
    elif command == "get":
        get(Path(argv[2]), argv[3:])
    elif command == "kinds":
        data = json.loads(Path(argv[2]).read_text())
        print(",".join(sorted({item["kind"] for item in data["boundaries"]})))
    elif command == "entity":
        from atlas_aci.storage import evidence as ev

        print(ev.file_entity(argv[2], "python")["id"])
    elif command == "link-entity":
        data = json.loads(Path(argv[2]).read_text())
        found = [i["provider"]["entity_id"] for i in data["links"] if i["provider"]["name"] == argv[3]]
        print(found[0])
    else:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
