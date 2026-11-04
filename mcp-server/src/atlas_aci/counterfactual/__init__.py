"""ACI-S32: counterfactual analysis of a proposed patch in an isolated hypothetical snapshot.

`patch_input` is the pure, strict unified-diff parser and applier; `analyze` stages the
patched tree outside the repository and compares it with the declared base snapshot. Nothing
here is exposed over MCP.
"""
