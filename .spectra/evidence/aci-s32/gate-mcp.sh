#!/bin/sh
# g-mcp: tools JSON byte-identical (run inside the container).
python -m atlas_aci tools --repo /ws > /tmp/tools.json
sha=$(sha256sum /tmp/tools.json | cut -d' ' -f1)
bytes=$(wc -c < /tmp/tools.json | tr -d ' ')
echo "sha256=$sha bytes=$bytes"
[ "$sha" = 94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56 ] && [ "$bytes" = 1631472 ]
