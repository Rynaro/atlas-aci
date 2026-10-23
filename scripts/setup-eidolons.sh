#!/usr/bin/env bash
# Restore ignored agent packages and generate machine-local MCP registrations.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
for dependency in eidolons docker python3; do
  command -v "$dependency" >/dev/null || {
    printf 'Missing prerequisite: %s\n' "$dependency" >&2
    exit 1
  }
done
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
docker info >/dev/null

eidolons sync --non-interactive
eidolons harness install --non-interactive
# Regenerate existing lock entries for this checkout, then install new MCPs.
eidolons mcp sync --repair-wiring
eidolons mcp sync
eidolons mcp pull atlas-aci
eidolons mcp pull crystalium
python3 scripts/configure-eidolons.py
eidolons harness check --smoke --host codex

printf '\nOpen a new host session and approve the project MCP servers when prompted.\n'
