import hashlib, pathlib, sys, tempfile
from click.testing import CliRunner
from atlas_aci.__main__ import cli
d = tempfile.mkdtemp(); (pathlib.Path(d) / "a.py").write_text("x=1\n")
o = CliRunner().invoke(cli, ["tools", "--repo", d], catch_exceptions=False).stdout.encode()
sha = hashlib.sha256(o).hexdigest()
print(len(o), sha)
want = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
sys.exit(0 if sha == want and len(o) == 1631472 else 1)
