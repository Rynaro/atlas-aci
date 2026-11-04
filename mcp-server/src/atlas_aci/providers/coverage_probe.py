"""Print the closed ``atlas-coverage-environment.v1`` JSON of the interpreter running this.

ACI-S21 operator probe. Atlas never spawns a foreign interpreter: the operator pipes this
source to the TEST interpreter, inside the environment that produced the coverage report:

    atlas-aci coverage-probe | <venv>/bin/python -I - > environment.json

Standard library only (``importlib.metadata``, ``json``, ``platform``, ``sys``); it reads
installed package metadata and writes one JSON object. It imports nothing from Atlas and runs
nothing else.
"""

import importlib.metadata
import json
import platform
import sys

PACKAGES = ("coverage", "pytest", "pytest-cov")


def main() -> int:
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            sys.stderr.write(f"coverage-probe: package {name!r} is not installed here\n")
            return 1
    tag = sys.implementation.cache_tag
    if not tag:
        sys.stderr.write("coverage-probe: this interpreter has no bytecode cache tag\n")
        return 1
    environment = {
        "format": "atlas-coverage-environment.v1",
        "python": {
            "version": platform.python_version(),
            "implementation": sys.implementation.name,
            "cache_tag": tag,
        },
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "packages": packages,
    }
    sys.stdout.write(
        json.dumps(environment, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
