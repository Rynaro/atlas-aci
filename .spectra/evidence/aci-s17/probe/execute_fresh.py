"""Capture the real serial corpus-run process result without shell masking."""
import subprocess,sys,json,time
from pathlib import Path
out=Path(__file__).resolve().parent;root=out.parents[3]
command=[str(root/'mcp-server/.venv/bin/python'),str(out/'run.py')]
started=time.monotonic()
with (out/'run.log').open('wb') as log:
 result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
(out/'run.exit').write_text(str(result.returncode)+'\n')
(out/'runner-result.json').write_text(json.dumps({'argv':command,'exit_code':result.returncode,'elapsed_wall_seconds':time.monotonic()-started},indent=2)+'\n')
raise SystemExit(result.returncode)
