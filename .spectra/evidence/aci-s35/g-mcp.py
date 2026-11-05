"""ACI-S35 G-MCP (run in the dev image): the tools JSON pin and the seven read-only tools."""
import hashlib
import sys

from click.testing import CliRunner

from atlas_aci.__main__ import cli
from atlas_aci.enforcement import READ_ONLY_TOOLS

out = CliRunner().invoke(cli, ["tools", "--repo", "/ws"])
raw = out.stdout_bytes
sha = hashlib.sha256(raw).hexdigest()
print("exit", out.exit_code, "bytes", len(raw), "sha256", sha, "tools", sorted(READ_ONLY_TOOLS))
ok = (
    out.exit_code == 0
    and sha == "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
    and len(raw) == 1631472
    and len(READ_ONLY_TOOLS) == 7
    and not any("feedback" in name for name in READ_ONLY_TOOLS)
    and all(
        text not in out.stdout
        for text in (
            "feedback-import", "feedback-rank", "verified_artifact", "unverifiable_outcome",
            "atlas-feedback-",
        )
    )
)
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
