"""Run the s23 tests with mcp.client.stdio imported first (as an earlier module does in the full
suite). Without it, `stdio_client`'s default `errlog=sys.stderr` binds to a CliRunner stream when
test_s23_isolation runs first, and 2 recorder tests fail with 'fileno'. Pre-existing: the same 2
fail on the base tree under the old lock/SDK in atlas-aci-dev-s16 when s23 is run alone."""

import glob
import sys

import mcp.client.stdio  # noqa: F401  (preload; see docstring)
import pytest

sys.exit(pytest.main(["-p", "no:cacheprovider", "-q", "-rf", *sorted(glob.glob("tests/slices/test_s23_*.py"))]))
