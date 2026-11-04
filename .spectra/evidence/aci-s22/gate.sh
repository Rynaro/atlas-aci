#!/bin/sh
# usage: gate.sh <name> <cmd...>  -> <name>.log.gz and <name>.exit under .spectra/evidence/aci-s22/
name="$1"; shift
dir="$(cd "$(dirname "$0")" && pwd)"
"$@" > "$dir/$name.log" 2>&1
echo $? > "$dir/$name.exit"
gzip -f "$dir/$name.log"
