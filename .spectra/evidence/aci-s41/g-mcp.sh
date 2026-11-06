#!/bin/sh
# ACI-S41 G-MCP: `atlas-aci tools` JSON sha256 and byte count equal the pin; 7 tools.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$("$HERE/dk41.sh" sh -c '/opt/venv/bin/python -m atlas_aci tools --repo /ws > /tmp/tools.json; sha256sum /tmp/tools.json | cut -d" " -f1; wc -c < /tmp/tools.json | tr -d " "; /opt/venv/bin/python -c "import json;print(len(json.load(open(\"/tmp/tools.json\"))))"' 2>/dev/null)
SHA=$(echo "$OUT" | sed -n 1p); BYTES=$(echo "$OUT" | sed -n 2p); N=$(echo "$OUT" | sed -n 3p)
{
    echo "sha256 $SHA bytes $BYTES tools $N"
    if [ "$SHA" = 94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56 ] && [ "$BYTES" = 1631472 ] && [ "$N" = 7 ]; then CODE=0; echo "g-mcp MATCH"; else CODE=1; echo "g-mcp MISMATCH"; fi
    echo "$CODE" > "$HERE/g-mcp.exit"
} 2>&1 | gzip -9n > "$HERE/g-mcp.log.gz"
echo "g-mcp exit $(cat "$HERE/g-mcp.exit")"
