set -eu
apk add --no-cache git curl >/dev/null 2>&1
git clone --quiet --depth 1 --no-checkout --filter=blob:none https://github.com/modelcontextprotocol/modelcontextprotocol /r
cd /r
git rev-parse HEAD
echo "== root"; git ls-tree --name-only HEAD
echo "== schema/"; git ls-tree --name-only HEAD schema/
for d in $(git ls-tree --name-only HEAD schema/); do echo "-- $d"; git ls-tree --name-only HEAD $d/; done
