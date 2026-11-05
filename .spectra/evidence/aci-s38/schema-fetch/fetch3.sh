set -eu
apk add --no-cache curl >/dev/null 2>&1
C=3c4d91b7e832d9b74338d56eed03403ccc662123
B=https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/$C
mkdir -p /out/schemas
curl -fsS --max-time 60 -o /out/LICENSE $B/LICENSE
for r in 2024-11-05 2025-03-26 2025-06-18 2025-11-25; do
  curl -fsS --max-time 60 -o /out/schemas/$r.schema.json $B/schema/$r/schema.json
done
curl -s -o /dev/null -w "NOTICE http %{http_code}\n" --max-time 30 $B/NOTICE || true
cd /out; sha256sum LICENSE schemas/*; wc -c LICENSE schemas/*; head -5 LICENSE; curl --version | head -1
