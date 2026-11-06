#!/bin/sh
# usage: dkx.sh NAME SECONDS [docker-extra-args...] -- cmd...   (watchdog-bounded, scratch mounted at /s42)
NAME=$1; SECS=$2; shift 2
EXTRA=""
while [ "$1" != "--" ]; do EXTRA="$EXTRA $1"; shift; done
shift
S=/private/tmp/claude-501/-Users-henrique-workspace-oss-agents-atlas-aci/694bbcf4-4624-4586-978b-245709fb6cab/scratchpad
( sleep $SECS; docker kill $NAME >/dev/null 2>&1 ) &
WD=$!
docker run --rm --name $NAME $EXTRA --user 1000:1000 -e HOME=/hc -e PYTHONPATH=/ws/mcp-server/src \
  -v $S/home:/hc -v $S/s42:/s42 -v /Users/henrique/workspace/oss/agents/atlas-aci:/ws -w /ws/mcp-server atlas-aci-dev-relock "$@"
RC=$?
kill $WD 2>/dev/null
exit $RC
